"""
modules/attestat_import/routes.py — Прийом майна з атестату
Blueprint: attestat_import, url_prefix=/personnel/<person_id>/attestat-import

Логіка:
  draft  — редагується вільно (позиції, номер, дата, скан)
  confirmed — проведено: записано в personnel_items + warehouse_income (для інвентарного)

Author: White
"""
import uuid
from datetime import date
from pathlib import Path
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, jsonify, session, flash
)
from core.auth import login_required
from core.db import get_connection
from core.audit import log_action

bp = Blueprint("attestat_import", __name__, url_prefix="/personnel/<int:person_id>/attestat-import")

ALLOWED_SCAN_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif"}
CATEGORIES = ["I", "II", "III"]


# ─────────────────────────────────────────────────────────────
#  Допоміжні функції
# ─────────────────────────────────────────────────────────────

def _get_person_or_404(conn, person_id):
    person = conn.execute("SELECT * FROM personnel WHERE id=?", (person_id,)).fetchone()
    return person


def _get_doc_or_404(conn, doc_id, person_id):
    return conn.execute(
        "SELECT * FROM income_docs WHERE id=? AND person_id=? AND source_type='attestat_import'",
        (doc_id, person_id)
    ).fetchone()


def _get_doc_items(conn, doc_id):
    return conn.execute("""
        SELECT i.*, d.name AS item_name, d.unit_of_measure, d.is_inventory
        FROM income_doc_items i
        JOIN item_dictionary d ON d.id = i.item_id
        WHERE i.doc_id = ?
        ORDER BY i.id
    """, (doc_id,)).fetchall()


def _save_scan_file(file, person_id: int, doc_number: str = "") -> tuple:
    """Зберегти скан атестату. Повертає (relative_path, original_name) або (None, None)."""
    if not file or not file.filename:
        return None, None
    original_name = file.filename
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_SCAN_EXTENSIONS:
        return None, None
    from core.settings import get_storage_path
    scans_dir = get_storage_path() / "scans" / "attestat"
    scans_dir.mkdir(parents=True, exist_ok=True)
    num_safe = (doc_number or "").replace("/", "-").replace(" ", "_")
    if num_safe:
        filename = f"attestat_{person_id}_{num_safe}{ext}"
    else:
        filename = f"attestat_{person_id}_{uuid.uuid4().hex[:8]}{ext}"
    save_path = scans_dir / filename
    file.save(str(save_path))
    return str(save_path), original_name


def _confirm_attestat_import(conn, doc_id, person_id):
    """Провести документ: записати в personnel_items та warehouse_income."""
    doc = conn.execute(
        "SELECT * FROM income_docs WHERE id=? AND person_id=? AND source_type='attestat_import'",
        (doc_id, person_id)
    ).fetchone()
    if not doc:
        return False, "Документ не знайдено"

    items = conn.execute("""
        SELECT i.*, d.is_inventory, d.name
        FROM income_doc_items i
        JOIN item_dictionary d ON d.id = i.item_id
        WHERE i.doc_id = ?
    """, (doc_id,)).fetchall()

    if not items:
        return False, "Немає позицій у документі"

    today = date.today().isoformat()
    created_by = session.get("user_id")

    for item in items:
        category = item["category"] or "II"
        # Категорія I → II (нова → в користуванні)
        issued_cat = "II" if category == "I" else category

        conn.execute("""
            INSERT INTO personnel_items
                (personnel_id, item_id, quantity, price, category,
                 source_type, income_doc_id,
                 issue_date, wear_started_date, status,
                 notes, created_at, updated_at)
            VALUES (?,?,?,?,?,
                    'attestat_import',?,
                    ?,?, 'active',
                    ?,datetime('now','localtime'),datetime('now','localtime'))
        """, (
            person_id,
            item["item_id"],
            item["quantity"],
            item["price"],
            issued_cat,
            doc_id,
            doc["date"] or today,
            doc["date"] or today,
            None,
        ))

        # Інвентарне майно → прихід на склад
        if item["is_inventory"]:
            conn.execute("""
                INSERT INTO warehouse_income
                    (date, document_number, item_id, quantity, price, category,
                     notes, source_type, status, created_by, created_at)
                VALUES (?,?,?,?,?,?,?,
                        'attestat_import','confirmed',?,datetime('now','localtime'))
            """, (
                doc["date"] or today,
                doc["document_number"],
                item["item_id"],
                item["quantity"],
                item["price"],
                item["category"],
                f"Прийом з атестату особи #{person_id}",
                created_by,
            ))

    conn.execute(
        "UPDATE income_docs SET status='confirmed', updated_at=datetime('now','localtime') WHERE id=?",
        (doc_id,)
    )
    return True, None


# ─────────────────────────────────────────────────────────────
#  Маршрути
# ─────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index(person_id):
    """Список атестатних документів для особи."""
    conn = get_connection()
    person = _get_person_or_404(conn, person_id)
    if not person:
        conn.close()
        flash("Особу не знайдено", "danger")
        return redirect(url_for("personnel.index"))

    docs = conn.execute("""
        SELECT d.*,
               COUNT(i.id) AS items_count,
               SUM(i.quantity * i.price) AS total_sum
        FROM income_docs d
        LEFT JOIN income_doc_items i ON i.doc_id = d.id
        WHERE d.person_id = ? AND d.source_type = 'attestat_import'
        GROUP BY d.id
        ORDER BY d.created_at DESC
    """, (person_id,)).fetchall()

    conn.close()
    return render_template(
        "personnel/attestat_import.html",
        person=person,
        docs=[dict(d) for d in docs],
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new(person_id):
    """Створити новий документ прийому з атестату (чернетка)."""
    conn = get_connection()
    person = _get_person_or_404(conn, person_id)
    if not person:
        conn.close()
        flash("Особу не знайдено", "danger")
        return redirect(url_for("personnel.index"))

    today = date.today().isoformat()
    created_by = session.get("user_id")

    cur = conn.execute("""
        INSERT INTO income_docs
            (status, date, document_number, source_type, person_id, created_by,
             created_at, updated_at)
        VALUES ('draft', ?, NULL, 'attestat_import', ?, ?,
                datetime('now','localtime'), datetime('now','localtime'))
    """, (today, person_id, created_by))
    conn.commit()
    doc_id = cur.lastrowid
    conn.close()

    return redirect(url_for("attestat_import.edit", person_id=person_id, doc_id=doc_id))


@bp.route("/<int:doc_id>/edit", methods=["GET", "POST"])
@login_required
def edit(person_id, doc_id):
    """Редагування чернетки атестатного документу."""
    conn = get_connection()
    person = _get_person_or_404(conn, person_id)
    if not person:
        conn.close()
        flash("Особу не знайдено", "danger")
        return redirect(url_for("personnel.index"))

    doc = _get_doc_or_404(conn, doc_id, person_id)
    if not doc:
        conn.close()
        flash("Документ не знайдено", "danger")
        return redirect(url_for("attestat_import.index", person_id=person_id))

    if doc["status"] != "draft":
        conn.close()
        flash("Документ вже проведено — редагування неможливе", "warning")
        return redirect(url_for("attestat_import.index", person_id=person_id))

    errors = []

    if request.method == "POST":
        action = request.form.get("action", "save")
        doc_number = request.form.get("document_number", "").strip() or None
        doc_date   = request.form.get("date", "").strip() or None
        notes      = request.form.get("notes", "").strip() or None

        # Оновлюємо шапку
        conn.execute("""
            UPDATE income_docs
            SET document_number=?, date=?, notes=?, updated_at=datetime('now','localtime')
            WHERE id=?
        """, (doc_number, doc_date, notes, doc_id))

        # Оновлюємо позиції: видаляємо всі → вставляємо заново
        conn.execute("DELETE FROM income_doc_items WHERE doc_id=?", (doc_id,))

        item_ids    = request.form.getlist("item_id[]")
        quantities  = request.form.getlist("quantity[]")
        prices      = request.form.getlist("price[]")
        categories  = request.form.getlist("category[]")
        nom_codes   = request.form.getlist("nom_code[]")

        saved_items = 0
        for i, item_id in enumerate(item_ids):
            item_id = item_id.strip()
            if not item_id:
                continue
            try:
                qty   = float(quantities[i]) if i < len(quantities) else 1.0
                price = float(prices[i])     if i < len(prices)     else 0.0
            except (ValueError, IndexError):
                qty, price = 1.0, 0.0
            cat      = categories[i] if i < len(categories) else "II"
            nom_code = nom_codes[i].strip() if i < len(nom_codes) else None
            nom_code = nom_code or None

            conn.execute("""
                INSERT INTO income_doc_items (doc_id, item_id, quantity, price, category, nom_code)
                VALUES (?,?,?,?,?,?)
            """, (doc_id, int(item_id), qty, price, cat, nom_code))
            saved_items += 1

        conn.commit()

        if action == "confirm":
            if not doc_date:
                errors.append("Для проведення необхідно вказати дату")
            elif saved_items == 0:
                errors.append("Для проведення необхідна хоча б одна позиція")
            else:
                ok, err = _confirm_attestat_import(conn, doc_id, person_id)
                if ok:
                    conn.commit()
                    log_action("confirm", "income_docs", doc_id,
                               new_data={"source": "attestat_import", "person_id": person_id})
                    conn.close()
                    flash("Документ проведено успішно", "success")
                    return redirect(url_for("attestat_import.index", person_id=person_id))
                else:
                    errors.append(err or "Помилка проведення")

        if not errors:
            conn.close()
            flash("Збережено", "success")
            return redirect(url_for("attestat_import.edit", person_id=person_id, doc_id=doc_id))

    # GET або повторний POST з помилками
    doc = _get_doc_or_404(conn, doc_id, person_id)
    doc_items = _get_doc_items(conn, doc_id)

    from modules.warehouse.routes import _get_norm_groups
    item_dict = conn.execute(
        "SELECT id, name, unit_of_measure, is_inventory FROM item_dictionary ORDER BY name"
    ).fetchall()
    norm_groups = _get_norm_groups(conn)

    conn.close()
    return render_template(
        "personnel/attestat_import_edit.html",
        person=person,
        doc=dict(doc),
        doc_items=[dict(i) for i in doc_items],
        item_dict=[dict(i) for i in item_dict],
        norm_groups=norm_groups,
        categories=CATEGORIES,
        errors=errors,
        today=date.today().isoformat(),
    )


@bp.route("/<int:doc_id>/confirm", methods=["POST"])
@login_required
def confirm(person_id, doc_id):
    """Провести документ (POST без редагування)."""
    conn = get_connection()
    doc = _get_doc_or_404(conn, doc_id, person_id)
    if not doc:
        conn.close()
        flash("Документ не знайдено", "danger")
        return redirect(url_for("attestat_import.index", person_id=person_id))

    if doc["status"] != "draft":
        conn.close()
        flash("Документ вже проведено", "warning")
        return redirect(url_for("attestat_import.index", person_id=person_id))

    ok, err = _confirm_attestat_import(conn, doc_id, person_id)
    if ok:
        conn.commit()
        log_action("confirm", "income_docs", doc_id,
                   new_data={"source": "attestat_import", "person_id": person_id})
        flash("Документ проведено успішно", "success")
    else:
        flash(err or "Помилка проведення", "danger")

    conn.close()
    return redirect(url_for("attestat_import.index", person_id=person_id))


@bp.route("/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete(person_id, doc_id):
    """Видалити чернетку (тільки draft)."""
    conn = get_connection()
    doc = _get_doc_or_404(conn, doc_id, person_id)
    if not doc:
        conn.close()
        flash("Документ не знайдено", "danger")
        return redirect(url_for("attestat_import.index", person_id=person_id))

    if doc["status"] != "draft":
        conn.close()
        flash("Не можна видалити проведений документ", "warning")
        return redirect(url_for("attestat_import.index", person_id=person_id))

    conn.execute("DELETE FROM income_docs WHERE id=?", (doc_id,))
    conn.commit()
    conn.close()
    flash("Чернетку видалено", "success")
    return redirect(url_for("attestat_import.index", person_id=person_id))


@bp.route("/<int:doc_id>/scan")
@login_required
def scan(person_id, doc_id):
    """Відкрити/завантажити скан документу."""
    import os
    from flask import send_file, abort
    conn = get_connection()
    doc = _get_doc_or_404(conn, doc_id, person_id)
    conn.close()
    if not doc or not doc["scan_path"]:
        abort(404)
    scan_path = doc["scan_path"]
    if not os.path.exists(scan_path):
        abort(404)
    return send_file(
        scan_path,
        as_attachment=False,
        download_name=doc["scan_original_name"] or Path(scan_path).name,
    )


@bp.route("/<int:doc_id>/upload-scan", methods=["POST"])
@login_required
def upload_scan(person_id, doc_id):
    """AJAX: завантажити скан документу."""
    conn = get_connection()
    doc = _get_doc_or_404(conn, doc_id, person_id)
    if not doc:
        conn.close()
        return jsonify({"ok": False, "error": "Документ не знайдено"}), 404

    file = request.files.get("scan")
    if not file or not file.filename:
        conn.close()
        return jsonify({"ok": False, "error": "Файл не вибрано"}), 400

    doc_number = doc["document_number"] or ""
    scan_path, original_name = _save_scan_file(file, person_id, doc_number)
    if scan_path is None:
        conn.close()
        return jsonify({"ok": False, "error": "Недозволений тип файлу"}), 400

    conn.execute(
        "UPDATE income_docs SET scan_path=?, scan_original_name=?, updated_at=datetime('now','localtime') WHERE id=?",
        (scan_path, original_name, doc_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "scan_path": scan_path, "filename": original_name})
