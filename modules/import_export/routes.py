"""
modules/import_export/routes.py — Імпорт та експорт даних

Маршрути:
  GET  /import-export/                        — головна
  GET/POST /import-export/personnel/import   — завантаження xlsx + перегляд
  POST /import-export/personnel/import/confirm — підтвердження імпорту
  GET  /import-export/db/export              — скачати database.db
  GET  /import-export/db/import              — форма заміни БД
  POST /import-export/db/import              — замінити БД (з бекапом)
Author: White
"""
import os
import json
import shutil
from datetime import date
from pathlib import Path
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, send_file, session
)
from core.auth import login_required
from core.db import get_connection, get_db_path

bp = Blueprint("import_export", __name__, url_prefix="/import-export")


def _split_full_name(full_name: str) -> tuple[str, str, str]:
    """
    Розбиває ПІБ на last_name, first_name, middle_name.
    Формат: 'Прізвище Ім'я По-батькові' або 'Прізвище Ім'я'.
    """
    parts = full_name.strip().split()
    last_name   = parts[0] if len(parts) > 0 else ""
    first_name  = parts[1] if len(parts) > 1 else ""
    middle_name = parts[2] if len(parts) > 2 else ""
    return last_name, first_name, middle_name


def _get_default_group_id(conn) -> int | None:
    """Повертає id першої активної групи."""
    row = conn.execute(
        "SELECT id FROM groups WHERE type='active' ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


# ─────────────────────────────────────────────────────────────
#  Головна
# ─────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    return render_template("import_export/index.html")


# ─────────────────────────────────────────────────────────────
#  Імпорт карток о/с з Excel
# ─────────────────────────────────────────────────────────────

@bp.route("/personnel/import", methods=["GET", "POST"])
@login_required
def personnel_import():
    if request.method == "GET":
        return render_template("import_export/personnel_import.html", errors=[])

    # POST — завантажити файл і показати попередній перегляд
    f = request.files.get("file")
    if not f or not f.filename:
        return render_template(
            "import_export/personnel_import.html",
            errors=["Оберіть файл для завантаження"]
        )

    ext = os.path.splitext(f.filename)[1].lower()
    if ext != ".xlsx":
        return render_template(
            "import_export/personnel_import.html",
            errors=["Дозволено тільки файли .xlsx"]
        )

    try:
        import openpyxl
    except ImportError:
        return render_template(
            "import_export/personnel_import.html",
            errors=["Бібліотека openpyxl не встановлена: py -m pip install openpyxl"]
        )

    # Зберегти файл тимчасово
    tmp_dir = Path(get_db_path()).parent / "storage" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"import_{session.get('user_id', 0)}_{date.today().isoformat()}.xlsx"
    f.save(str(tmp_path))

    try:
        wb = openpyxl.load_workbook(str(tmp_path), read_only=True, data_only=True)
        ws = wb.active

        rows_data = []
        errors    = []
        header    = None

        for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if not any(row):
                continue
            if header is None:
                # Перший непустий рядок — заголовок
                header = [str(c).strip().lower() if c else "" for c in row]
                continue

            # Визначити індекси колонок
            def col(names):
                for name in names:
                    for i, h in enumerate(header):
                        if name in h:
                            return row[i] if i < len(row) else None
                return None

            # ПІБ — або окремі поля або одне загальне
            last_name   = str(col(["прізвище", "фамилия", "last_name"]) or "").strip()
            first_name  = str(col(["ім'я", "имя", "first_name"]) or "").strip()
            middle_name = str(col(["по батькові", "отчество", "middle_name", "по-батькові"]) or "").strip()

            # Якщо є загальне поле ПІБ
            if not last_name:
                full = str(col(["піб", "пиб", "пибо", "full_name", "прізвище ім'я"]) or "").strip()
                if full:
                    last_name, first_name, middle_name = _split_full_name(full)

            if not last_name or not first_name:
                errors.append(f"Рядок {row_idx}: не вдалося визначити ПІБ")
                continue

            rank     = str(col(["звання", "rank"]) or "").strip()
            position = str(col(["посада", "position"]) or "").strip()
            ipn_raw  = col(["іпн", "інн", "ipn", "ідентиф"])
            ipn      = str(int(ipn_raw)) if ipn_raw and str(ipn_raw).replace(".0", "").isdigit() else str(ipn_raw or "").strip()
            svc_raw  = str(col(["тип служби", "service_type", "тип_служби"]) or "").strip().lower()
            svc      = "contract" if "контракт" in svc_raw or svc_raw == "contract" else (
                       "mobilized" if "мобіл" in svc_raw or svc_raw == "mobilized" else None)

            rows_data.append({
                "last_name":    last_name,
                "first_name":   first_name,
                "middle_name":  middle_name,
                "rank":         rank,
                "position":     position,
                "ipn":          ipn or None,
                "service_type": svc,
            })

        wb.close()
    except Exception as e:
        return render_template(
            "import_export/personnel_import.html",
            errors=[f"Помилка читання файлу: {e}"]
        )

    if not rows_data:
        return render_template(
            "import_export/personnel_import.html",
            errors=(errors or ["Файл порожній або не містить даних"])
        )

    # Перевірити дублікати в БД
    conn = get_connection()
    existing = conn.execute(
        "SELECT last_name, first_name, middle_name, ipn FROM personnel WHERE is_active=1"
    ).fetchall()
    conn.close()

    existing_set_ipn = {str(r["ipn"]) for r in existing if r["ipn"]}
    existing_set_fio = {
        (r["last_name"].lower(), r["first_name"].lower(),
         (r["middle_name"] or "").lower())
        for r in existing
    }

    for row in rows_data:
        row["is_duplicate_ipn"] = bool(row["ipn"] and row["ipn"] in existing_set_ipn)
        row["is_duplicate_fio"] = (
            row["last_name"].lower(),
            row["first_name"].lower(),
            row["middle_name"].lower()
        ) in existing_set_fio

    # Зберегти дані в сесії для підтвердження
    session["import_data"] = json.dumps(rows_data, ensure_ascii=False)
    session["import_file"] = str(tmp_path)

    duplicates = [r for r in rows_data if r["is_duplicate_ipn"] or r["is_duplicate_fio"]]

    return render_template(
        "import_export/personnel_import_preview.html",
        rows=rows_data,
        duplicates=duplicates,
        errors=errors,
        total=len(rows_data),
    )


@bp.route("/personnel/import/confirm", methods=["POST"])
@login_required
def personnel_import_confirm():
    raw = session.get("import_data")
    if not raw:
        flash("Сесія підтвердження закінчилась. Завантажте файл знову.", "warning")
        return redirect(url_for("import_export.personnel_import"))

    rows_data   = json.loads(raw)
    skip_dupes  = request.form.get("skip_duplicates") == "1"

    conn = get_connection()
    default_group_id = _get_default_group_id(conn)
    svc_row = conn.execute("SELECT value FROM settings WHERE key='default_service_type'").fetchone()
    default_service_type = svc_row["value"] if svc_row else "mobilized"

    # Перевірити існуючі ще раз (між завантаженням і підтвердженням міг хтось додати)
    existing_set_ipn = {
        str(r["ipn"])
        for r in conn.execute("SELECT ipn FROM personnel WHERE ipn IS NOT NULL").fetchall()
    }

    added   = 0
    skipped = 0

    for row in rows_data:
        is_dupe = (row.get("is_duplicate_ipn") or row.get("is_duplicate_fio"))
        if skip_dupes and is_dupe:
            skipped += 1
            continue
        if not skip_dupes and row["ipn"] and row["ipn"] in existing_set_ipn:
            skipped += 1
            continue

        try:
            row_svc = row.get("service_type") or default_service_type
            conn.execute("""
                INSERT INTO personnel
                    (last_name, first_name, middle_name, rank, position,
                     category, group_id, is_active, service_type)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                row["last_name"], row["first_name"], row["middle_name"] or None,
                row["rank"] or None, row["position"] or None,
                "soldier",
                default_group_id,
                1,
                row_svc,
            ))
            if row["ipn"]:
                pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                try:
                    conn.execute(
                        "UPDATE personnel SET ipn=? WHERE id=?",
                        (row["ipn"], pid)
                    )
                except Exception:
                    pass  # IPN дублікат — ігноруємо
            added += 1
        except Exception:
            skipped += 1

    conn.commit()
    conn.close()

    # Видалити тимчасовий файл
    tmp_path = session.pop("import_file", None)
    if tmp_path:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    session.pop("import_data", None)

    flash(f"Імпорт завершено: додано {added}, пропущено {skipped}.", "success")
    return redirect(url_for("personnel.index"))


# ─────────────────────────────────────────────────────────────
#  Експорт БД
# ─────────────────────────────────────────────────────────────

@bp.route("/db/export")
@login_required
def db_export():
    db_path = get_db_path()
    if not os.path.exists(db_path):
        flash("База даних не знайдена", "danger")
        return redirect(url_for("import_export.index"))

    filename = f"database_{date.today().isoformat()}.db"
    return send_file(
        db_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/octet-stream",
    )


# ─────────────────────────────────────────────────────────────
#  Імпорт (заміна) БД
# ─────────────────────────────────────────────────────────────

@bp.route("/db/import", methods=["GET", "POST"])
@login_required
def db_import():
    if request.method == "GET":
        return render_template("import_export/db_import.html")

    f = request.files.get("db_file")
    if not f or not f.filename:
        flash("Оберіть файл бази даних (.db)", "warning")
        return render_template("import_export/db_import.html")

    ext = os.path.splitext(f.filename)[1].lower()
    if ext != ".db":
        flash("Дозволено тільки файли .db", "warning")
        return render_template("import_export/db_import.html")

    db_path = get_db_path()
    db_dir  = os.path.dirname(db_path)

    # Автоматичний бекап поточної БД
    backup_name = f"database_backup_{date.today().isoformat()}.db"
    backup_path = os.path.join(db_dir, backup_name)
    try:
        shutil.copy2(db_path, backup_path)
    except Exception as e:
        flash(f"Не вдалося зробити резервну копію: {e}", "danger")
        return render_template("import_export/db_import.html")

    # Зберегти новий файл
    try:
        f.save(db_path)
    except Exception as e:
        # Спробувати відновити бекап
        try:
            shutil.copy2(backup_path, db_path)
        except Exception:
            pass
        flash(f"Помилка при заміні БД: {e}", "danger")
        return render_template("import_export/db_import.html")

    flash(
        f"База даних успішно замінена. Резервна копія збережена: {backup_name}. "
        "Перезапустіть застосунок для застосування змін.",
        "success"
    )
    return redirect(url_for("import_export.index"))
