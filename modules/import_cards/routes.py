"""
modules/import_cards/routes.py — Імпорт карток А5027 з Excel

Кроки:
  1. GET/POST /import-cards/          — upload файлу → парсинг → redirect на чергу
  2. GET      /import-cards/<uid>/     — черга: список всіх карток з файлу
  3. GET      /import-cards/<uid>/<n>  — review картки N (перевірка/редагування)
  4. POST     /import-cards/<uid>/<n>/save  — зберегти картку N
  5. POST     /import-cards/<uid>/<n>/skip  — пропустити картку N
  6. GET      /import-cards/<uid>/result    — підсумок

Стан імпорту зберігається у storage/imports/<uid>.json
"""
import json
import threading
import uuid
from datetime import date
from pathlib import Path

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify,
)

from core.auth import login_required
from core.db import get_connection
from core.settings import get_storage_path
from core.audit import log_action

bp = Blueprint("import_cards", __name__, url_prefix="/import-cards")


# ── Стан імпорту (JSON файл) ───────────────────────────────────────────────────

def _imports_dir() -> Path:
    p = get_storage_path() / "imports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path(uid: str) -> Path:
    return _imports_dir() / f"{uid}.json"


def _load_state(uid: str) -> dict | None:
    p = _state_path(uid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_state(uid: str, state: dict) -> None:
    _state_path(uid).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _delete_state(uid: str) -> None:
    p = _state_path(uid)
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


def _cleanup_old_sessions(max_age_days: int = 3) -> None:
    """Видалити сесії імпорту старші за max_age_days днів."""
    import time
    cutoff = time.time() - max_age_days * 86400
    imports_dir = _imports_dir()
    for f in imports_dir.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass
    # Також прибрати осиротілі tmp-файли завантажень
    for f in imports_dir.glob("*_upload.*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass


# ── Прогрес парсингу ──────────────────────────────────────────────────────────

def _progress_path(uid: str) -> Path:
    return _imports_dir() / f"{uid}_progress.json"


def _write_progress(uid: str, done: int, total: int, current: str, status: str,
                    error: str = "") -> None:
    data = {
        "done":    done,
        "total":   total,
        "current": current,
        "status":  status,   # "parsing" | "done" | "error"
        "error":   error,
        "pct":     int(done * 100 / total) if total else 0,
    }
    _progress_path(uid).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _read_progress(uid: str) -> dict | None:
    p = _progress_path(uid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _delete_progress(uid: str) -> None:
    try:
        _progress_path(uid).unlink(missing_ok=True)
    except OSError:
        pass


# ── Фоновий парсинг ───────────────────────────────────────────────────────────

def _parse_in_background(uid: str, tmp_path: Path, filename: str) -> None:
    """Запускається у фоновому потоці. Парсить файл і зберігає стан."""
    from modules.import_cards.parser import SKIP_SHEETS, _parse_sheet
    import openpyxl

    try:
        wb = openpyxl.load_workbook(str(tmp_path), read_only=True, data_only=True)
    except Exception as e:
        _write_progress(uid, 0, 0, "", "error", error=f"Не вдалось відкрити файл: {e}")
        tmp_path.unlink(missing_ok=True)
        return

    # Рахуємо аркуші що будемо парсити (без SKIP_SHEETS)
    sheets_to_parse = [
        s for s in wb.sheetnames
        if s.lower().strip() not in SKIP_SHEETS
    ]
    total = len(sheets_to_parse)

    if total == 0:
        _write_progress(uid, 0, 0, "", "error", error="У файлі не знайдено жодної картки")
        wb.close()
        tmp_path.unlink(missing_ok=True)
        return

    _write_progress(uid, 0, total, sheets_to_parse[0], "parsing")

    cards_raw = []
    for i, sheet_name in enumerate(sheets_to_parse):
        _write_progress(uid, i, total, sheet_name, "parsing")
        ws = wb[sheet_name]
        try:
            card = _parse_sheet(ws)
            card["sheet_name"] = sheet_name
            cards_raw.append(card)
        except Exception as e:
            cards_raw.append({
                "sheet_name":     sheet_name,
                "card_number":    "",
                "last_name":      sheet_name,
                "first_name":     "",
                "middle_name":    "",
                "rank":           "",
                "unit_raw":       "",
                "sizes":          {},
                "docs":           [],
                "items":          [],
                "parse_warnings": [f"Критична помилка парсингу: {e}"],
            })

    wb.close()
    tmp_path.unlink(missing_ok=True)

    # Зберігаємо стан
    for c in cards_raw:
        c["status"]     = STATUS_PENDING
        c["person_id"]  = None
        c["saved_docs"] = []

    state = {
        "uid":        uid,
        "filename":   filename,
        "created_at": date.today().isoformat(),
        "cards":      cards_raw,
    }
    _save_state(uid, state)
    _write_progress(uid, total, total, "", "done")


# ── Статуси карток ─────────────────────────────────────────────────────────────

STATUS_PENDING   = "pending"    # ще не оброблена
STATUS_IMPORTED  = "imported"   # збережено в БД
STATUS_SKIPPED   = "skipped"    # пропущено оператором
STATUS_DUPLICATE = "duplicate"  # знайдено дубль в БД (потребує рішення)
STATUS_ERROR     = "error"      # помилка при збереженні


# ── Допоміжні ─────────────────────────────────────────────────────────────────

def _next_pending(state: dict) -> int | None:
    """Індекс першої картки зі статусом pending або duplicate."""
    for i, c in enumerate(state["cards"]):
        if c["status"] in (STATUS_PENDING, STATUS_DUPLICATE):
            return i
    return None


def _get_form_data(conn) -> dict:
    """Дані для форм: підрозділи, норми, звання."""
    units = conn.execute("SELECT id, name FROM units ORDER BY name").fetchall()
    norms = conn.execute(
        "SELECT id, name FROM supply_norms WHERE is_active=1 ORDER BY name"
    ).fetchall()
    ranks = conn.execute(
        "SELECT name FROM rank_presets WHERE is_active=1 ORDER BY sort_order, name"
    ).fetchall()
    return {
        "units": [dict(r) for r in units],
        "norms": [dict(r) for r in norms],
        "ranks": [r["name"] for r in ranks],
    }


def _find_duplicate(conn, last_name: str, first_name: str, unit_raw: str) -> dict | None:
    """Шукає особу з таким самим прізвищем+ім'ям в БД."""
    if not last_name:
        return None
    row = conn.execute(
        """SELECT p.*, u.name as unit_name
           FROM personnel p
           LEFT JOIN units u ON p.unit_id = u.id
           WHERE lower(p.last_name) = lower(?) AND lower(p.first_name) = lower(?)
           LIMIT 1""",
        (last_name, first_name),
    ).fetchone()
    return dict(row) if row else None


def _next_card_number(conn) -> str:
    """Авто-номер картки через doc_sequences."""
    row = conn.execute(
        "SELECT seq FROM doc_sequences WHERE doc_type='personnel_card' AND year=0"
    ).fetchone()
    if row:
        new_seq = row["seq"] + 1
        conn.execute(
            "UPDATE doc_sequences SET seq=? WHERE doc_type='personnel_card' AND year=0",
            (new_seq,),
        )
    else:
        new_seq = 1
        conn.execute(
            "INSERT INTO doc_sequences (doc_type, year, seq) VALUES ('personnel_card', 0, 1)"
        )
    return str(new_seq)


# ── Крок 1 — Upload ────────────────────────────────────────────────────────────

@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    """Крок 1: AJAX upload → запуск фонового парсингу → повертає uid для polling."""
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"ok": False, "msg": "Оберіть файл Excel"}), 400

        ext = Path(f.filename).suffix.lower()
        if ext not in (".xlsx", ".xls"):
            return jsonify({"ok": False, "msg": "Підтримуються тільки .xlsx / .xls"}), 400

        uid = uuid.uuid4().hex
        tmp_path = _imports_dir() / f"{uid}_upload{ext}"
        f.save(str(tmp_path))

        # Одразу пишемо початковий прогрес — щоб polling не отримав 404
        _write_progress(uid, 0, 0, "", "parsing")

        # Запускаємо парсинг у фоні
        t = threading.Thread(
            target=_parse_in_background,
            args=(uid, tmp_path, f.filename),
            daemon=True,
        )
        t.start()

        return jsonify({"ok": True, "uid": uid, "msg": ""})

    # Прибираємо старі сесії при відкритті сторінки
    try:
        _cleanup_old_sessions()
    except Exception:
        pass
    return render_template("import_cards/index.html")


@bp.route("/<uid>/parse-status")
@login_required
def parse_status(uid: str):
    """Returns: {"ok": bool, "status": "parsing"|"done"|"error", "done": int,
                 "total": int, "pct": int, "current": str, "error": str,
                 "queue_url": str}"""
    prog = _read_progress(uid)
    if not prog:
        # Ще не стартував або uid невірний
        return jsonify({"ok": True, "status": "parsing", "done": 0,
                        "total": 0, "pct": 0, "current": "", "error": "", "queue_url": ""})

    queue_url = ""
    if prog["status"] == "done":
        queue_url = url_for("import_cards.queue", uid=uid)
        _delete_progress(uid)
    elif prog["status"] == "error":
        _delete_progress(uid)   # прибираємо і при помилці

    return jsonify({
        "ok":       True,
        "status":   prog["status"],
        "done":     prog["done"],
        "total":    prog["total"],
        "pct":      prog["pct"],
        "current":  prog["current"],
        "error":    prog.get("error", ""),
        "queue_url": queue_url,
    })


# ── Крок 2 — Черга ────────────────────────────────────────────────────────────

@bp.route("/<uid>/")
@login_required
def queue(uid: str):
    """Крок 2: список всіх карток з файлу."""
    state = _load_state(uid)
    if not state:
        flash("Сесію імпорту не знайдено. Завантажте файл заново.", "warning")
        return redirect(url_for("import_cards.index"))

    cards = state["cards"]
    total    = len(cards)
    imported = sum(1 for c in cards if c["status"] == STATUS_IMPORTED)
    skipped  = sum(1 for c in cards if c["status"] == STATUS_SKIPPED)
    pending  = sum(1 for c in cards if c["status"] in (STATUS_PENDING, STATUS_DUPLICATE))
    errors   = sum(1 for c in cards if c["status"] == STATUS_ERROR)

    # Перший очікуючий — для кнопки "Продовжити"
    next_idx = _next_pending(state)

    return render_template(
        "import_cards/queue.html",
        uid=uid,
        state=state,
        cards=cards,
        total=total,
        imported=imported,
        skipped=skipped,
        pending=pending,
        errors=errors,
        next_idx=next_idx,
        STATUS_PENDING=STATUS_PENDING,
        STATUS_IMPORTED=STATUS_IMPORTED,
        STATUS_SKIPPED=STATUS_SKIPPED,
        STATUS_DUPLICATE=STATUS_DUPLICATE,
        STATUS_ERROR=STATUS_ERROR,
    )


# ── Крок 3 — Review картки ────────────────────────────────────────────────────

@bp.route("/<uid>/<int:idx>")
@login_required
def review(uid: str, idx: int):
    """Крок 3: перевірка/редагування однієї картки."""
    state = _load_state(uid)
    if not state:
        flash("Сесію імпорту не знайдено.", "warning")
        return redirect(url_for("import_cards.index"))

    cards = state["cards"]
    if idx < 0 or idx >= len(cards):
        return redirect(url_for("import_cards.queue", uid=uid))

    card = cards[idx]
    conn = get_connection()
    form_data = _get_form_data(conn)

    # Перевірка дубліката
    duplicate = None
    if card["status"] in (STATUS_PENDING, STATUS_DUPLICATE):
        dup = _find_duplicate(conn, card.get("last_name", ""), card.get("first_name", ""), card.get("unit_raw", ""))
        if dup:
            card["status"] = STATUS_DUPLICATE
            _save_state(uid, state)
            duplicate = dup
            # Майно дублікату для порівняння
            dup_items = conn.execute(
                """SELECT pi.id, d.name as item_name, pi.quantity, pi.issue_date, pi.category
                   FROM personnel_items pi
                   JOIN item_dictionary d ON pi.item_id = d.id
                   WHERE pi.personnel_id = ? AND pi.status = 'active'
                   ORDER BY d.name""",
                (dup["id"],),
            ).fetchall()
            duplicate["items"] = [dict(r) for r in dup_items]

    # Fuzzy матчинг назв майна
    from modules.import_cards.matcher import match_items
    all_dict_items = conn.execute(
        "SELECT id, name FROM item_dictionary ORDER BY name"
    ).fetchall()
    dict_items = [{"id": r["id"], "name": r["name"]} for r in all_dict_items]
    matched_items = match_items(card.get("items", []), dict_items)

    conn.close()

    # Навігація
    prev_idx = next((i for i in range(idx - 1, -1, -1)
                     if cards[i]["status"] in (STATUS_PENDING, STATUS_DUPLICATE, STATUS_IMPORTED)), None)
    next_idx_nav = next((i for i in range(idx + 1, len(cards))
                         if cards[i]["status"] in (STATUS_PENDING, STATUS_DUPLICATE)), None)

    return render_template(
        "import_cards/review.html",
        uid=uid,
        idx=idx,
        card=card,
        matched_items=matched_items,
        duplicate=duplicate,
        form_data=form_data,
        prev_idx=prev_idx,
        next_idx=next_idx_nav,
        total=len(cards),
        STATUS_DUPLICATE=STATUS_DUPLICATE,
    )


# ── Крок 4 — Зберегти картку ──────────────────────────────────────────────────

@bp.route("/<uid>/<int:idx>/save", methods=["POST"])
@login_required
def save_card(uid: str, idx: int):
    """POST: зберегти/оновити особу і майно."""
    state = _load_state(uid)
    if not state:
        return jsonify({"ok": False, "msg": "Сесію не знайдено"}), 400

    cards = state["cards"]
    if idx < 0 or idx >= len(cards):
        return jsonify({"ok": False, "msg": "Невірний індекс"}), 400

    card = cards[idx]
    conn = get_connection()

    try:
        # ── Дані особи з форми ──
        last_name   = request.form.get("last_name", "").strip()
        first_name  = request.form.get("first_name", "").strip()
        middle_name = request.form.get("middle_name", "").strip()
        rank        = request.form.get("rank", "").strip()
        unit_id     = request.form.get("unit_id", type=int) or None
        card_number = request.form.get("card_number", "").strip()
        service_type = request.form.get("service_type", "") or None
        norm_id     = request.form.get("norm_id", type=int) or None
        norm_cat    = request.form.get("norm_cat", type=int) or 1   # числовий id категорії норми
        enroll_date = request.form.get("enroll_date", "") or None
        is_active   = int(request.form.get("is_active", "1"))
        archive_reason = request.form.get("archive_reason", "") or None

        # Розміри
        size_head       = request.form.get("size_head", "") or None
        size_height     = request.form.get("size_height", "") or None
        size_underwear  = request.form.get("size_underwear", "") or None
        size_suit       = request.form.get("size_suit", "") or None
        size_jacket     = request.form.get("size_jacket", "") or None
        size_pants      = request.form.get("size_pants", "") or None
        size_shoes      = request.form.get("size_shoes", "") or None

        if not last_name:
            conn.close()
            return jsonify({"ok": False, "msg": "Прізвище обов'язкове"}), 400

        # Авто-номер картки якщо порожній
        if not card_number:
            card_number = _next_card_number(conn)

        # Режим: нова особа або оновлення дубліката
        update_person_id = request.form.get("update_person_id") or None

        if update_person_id:
            # Оновлюємо існуючу особу
            person_id = int(update_person_id)
            conn.execute("""
                UPDATE personnel SET
                    last_name=?, first_name=?, middle_name=?, rank=?,
                    unit_id=?, card_number=?, service_type=?, norm_id=?,
                    enroll_date=?,
                    size_head=?, size_height=?, size_underwear=?,
                    size_suit=?, size_jacket=?, size_pants=?, size_shoes=?,
                    is_active=?, archive_reason=?,
                    updated_at=datetime('now','localtime')
                WHERE id=?
            """, (
                last_name, first_name, middle_name, rank,
                unit_id, card_number, service_type, norm_id,
                enroll_date,
                size_head, size_height, size_underwear,
                size_suit, size_jacket, size_pants, size_shoes,
                is_active, archive_reason,
                person_id,
            ))
        else:
            # Нова особа — знаходимо групу type='active'
            group_row = conn.execute(
                "SELECT id FROM groups WHERE type='active' LIMIT 1"
            ).fetchone()
            group_id = group_row["id"] if group_row else None

            cur = conn.execute("""
                INSERT INTO personnel
                    (last_name, first_name, middle_name, rank,
                     unit_id, card_number, service_type, norm_id,
                     enroll_date,
                     size_head, size_height, size_underwear,
                     size_suit, size_jacket, size_pants, size_shoes,
                     is_active, archive_reason, group_id,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        datetime('now','localtime'), datetime('now','localtime'))
            """, (
                last_name, first_name, middle_name, rank,
                unit_id, card_number, service_type, norm_id,
                enroll_date,
                size_head, size_height, size_underwear,
                size_suit, size_jacket, size_pants, size_shoes,
                is_active, archive_reason, group_id,
            ))
            person_id = cur.lastrowid

        # Якщо є норма — додати в personnel_norms
        if norm_id:
            conn.execute(
                """INSERT OR IGNORE INTO personnel_norms
                   (personnel_id, norm_id, personnel_cat, created_at)
                   VALUES (?, ?, ?, datetime('now','localtime'))""",
                (person_id, norm_id, norm_cat),
            )

        # ── Майно (personnel_items) ──
        items_json = request.form.get("items_json", "[]")
        try:
            items_data = json.loads(items_json)
        except json.JSONDecodeError:
            items_data = []

        # При оновленні дублікату — пропускаємо item_id що вже є в personnel_items
        skip_pi_ids_json = request.form.get("skip_item_ids_json", "[]")
        try:
            skip_pi_ids = [int(x) for x in json.loads(skip_pi_ids_json) if x]
        except (json.JSONDecodeError, ValueError):
            skip_pi_ids = []

        skip_item_ids: set[int] = set()
        if skip_pi_ids and update_person_id:
            # Завантажуємо item_id для відзначених personnel_items рядків
            placeholders = ",".join("?" * len(skip_pi_ids))
            rows = conn.execute(
                f"SELECT item_id FROM personnel_items WHERE id IN ({placeholders})",
                skip_pi_ids,
            ).fetchall()
            skip_item_ids = {r["item_id"] for r in rows}

        for it in items_data:
            if it.get("item_id") in skip_item_ids:
                continue  # вже є у цієї особи — пропускаємо
            item_id    = it.get("item_id")
            if not item_id:
                continue
            qty        = float(it.get("qty") or 0)
            if qty <= 0:
                continue
            issue_date = it.get("issue_date") or None
            price      = float(it.get("price") or 0) or None

            # Розраховуємо next_issue_date якщо є норма і вид служби
            next_issue_date = None
            if service_type and norm_id:
                wear_row = conn.execute("""
                    SELECT sniw.wear_months
                    FROM supply_norm_items sni
                    JOIN supply_norm_item_wear sniw
                           ON sniw.norm_item_id = sni.id
                          AND sniw.personnel_cat = ?
                    WHERE sni.norm_id = ? AND sni.item_id = ?
                    LIMIT 1
                """, (norm_cat, norm_id, item_id)).fetchone()
                if wear_row and wear_row["wear_months"]:
                    from core.military_logic import get_next_issue_date
                    next_dt = get_next_issue_date(
                        service_type     = service_type,
                        cycle_start_date = issue_date,
                        norm_date        = enroll_date,
                        wear_months      = int(wear_row["wear_months"]),
                    )
                    next_issue_date = next_dt.isoformat() if next_dt else None

            conn.execute("""
                INSERT INTO personnel_items
                    (personnel_id, item_id, quantity, price, category,
                     source_type, issue_date, wear_started_date, status,
                     cycle_start_date, next_issue_date,
                     created_at, updated_at)
                VALUES (?,?,?,?,'II',
                        'import', ?, ?, 'active',
                        ?, ?,
                        datetime('now','localtime'), datetime('now','localtime'))
            """, (
                person_id, item_id, qty, price,
                issue_date, issue_date,
                issue_date, next_issue_date,
            ))

        # ── РВ/Накладні (зовнішні — is_external=1) ──
        docs_json = request.form.get("docs_json", "[]")
        try:
            docs_data = json.loads(docs_json)
        except json.JSONDecodeError:
            docs_data = []

        saved_docs = []
        for doc in docs_data:
            action   = doc.get("action")   # "auto", "create", "skip"
            if action == "skip":
                continue

            ext_number = doc.get("number", "").strip()
            doc_date   = doc.get("date") or None
            doc_type   = doc.get("doc_type", "rv")

            if not ext_number:
                continue

            if doc_type == "rv":
                # Шукаємо існуючу зовнішню РВ по external_number + doc_date
                existing = conn.execute(
                    """SELECT id FROM distribution_sheets
                       WHERE is_external=1 AND external_number=?
                         AND (doc_date=? OR ? IS NULL)""",
                    (ext_number, doc_date, doc_date),
                ).fetchone()

                if existing:
                    sheet_id = existing["id"]
                    # Дописуємо особу якщо ще немає
                    exists_row = conn.execute(
                        "SELECT id FROM distribution_sheet_rows WHERE sheet_id=? AND personnel_id=?",
                        (sheet_id, person_id),
                    ).fetchone()
                    if not exists_row:
                        max_sort = conn.execute(
                            "SELECT IFNULL(MAX(sort_order),0) FROM distribution_sheet_rows WHERE sheet_id=?",
                            (sheet_id,),
                        ).fetchone()[0]
                        conn.execute("""
                            INSERT INTO distribution_sheet_rows
                                (sheet_id, personnel_id, sort_order, received)
                            VALUES (?,?,?,1)
                        """, (sheet_id, person_id, max_sort + 1))
                    saved_docs.append({"id": sheet_id, "action": "linked", "number": ext_number})
                else:
                    # Створюємо нову зовнішню РВ зі статусом closed
                    from core.settings import get_all_settings as _get_settings
                    s = _get_settings()
                    cur2 = conn.execute("""
                        INSERT INTO distribution_sheets
                            (number, year, sequence_num, unit_id,
                             doc_date, status, is_external, external_number,
                             service_name, supplier_name,
                             base_document,
                             created_at, updated_at)
                        VALUES ('імпорт', 0, 0, ?,
                                ?, 'closed', 1, ?,
                                ?, ?,
                                'Імпорт карток А5027',
                                datetime('now','localtime'), datetime('now','localtime'))
                    """, (
                        unit_id,
                        doc_date,
                        ext_number,
                        s.get("service_name", ""),
                        s.get("company_name", ""),
                    ))
                    sheet_id = cur2.lastrowid
                    conn.execute("""
                        INSERT INTO distribution_sheet_rows
                            (sheet_id, personnel_id, sort_order, received)
                        VALUES (?,?,1,1)
                    """, (sheet_id, person_id))
                    saved_docs.append({"id": sheet_id, "action": "created", "number": ext_number})

        conn.commit()

        # Оновлюємо стан
        card["status"]     = STATUS_IMPORTED
        card["person_id"]  = person_id
        card["saved_docs"] = saved_docs
        _save_state(uid, state)

        log_action("add", "personnel", person_id,
                   new_data={"source": "import", "name": f"{last_name} {first_name}"})

        conn.close()

        # Знаходимо наступну pending картку
        next_idx = _next_pending(state)
        if next_idx is not None:
            next_url = url_for("import_cards.review", uid=uid, idx=next_idx)
        else:
            next_url = url_for("import_cards.result", uid=uid)

        return jsonify({"ok": True, "next_url": next_url, "msg": ""})

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"ok": False, "msg": f"Помилка збереження: {e}"}), 500


# ── Пропустити картку ─────────────────────────────────────────────────────────

@bp.route("/<uid>/<int:idx>/skip", methods=["POST"])
@login_required
def skip_card(uid: str, idx: int):
    """Пропустити картку."""
    state = _load_state(uid)
    if not state:
        return jsonify({"ok": False, "msg": "Сесію не знайдено"}), 400

    cards = state["cards"]
    if 0 <= idx < len(cards):
        cards[idx]["status"] = STATUS_SKIPPED
        _save_state(uid, state)

    next_idx = _next_pending(state)
    if next_idx is not None:
        next_url = url_for("import_cards.review", uid=uid, idx=next_idx)
    else:
        next_url = url_for("import_cards.result", uid=uid)

    return jsonify({"ok": True, "next_url": next_url})


# ── Крок 4 — Результат ────────────────────────────────────────────────────────

@bp.route("/<uid>/result")
@login_required
def result(uid: str):
    """Підсумок імпорту."""
    state = _load_state(uid)
    if not state:
        flash("Сесію імпорту не знайдено.", "warning")
        return redirect(url_for("import_cards.index"))

    cards = state["cards"]
    imported = [c for c in cards if c["status"] == STATUS_IMPORTED]
    skipped  = [c for c in cards if c["status"] == STATUS_SKIPPED]
    pending  = [c for c in cards if c["status"] in (STATUS_PENDING, STATUS_DUPLICATE)]
    errors   = [c for c in cards if c["status"] == STATUS_ERROR]

    # Унікальні РВ створені/дописані
    all_docs = []
    seen_doc_ids = set()
    for c in imported:
        for d in c.get("saved_docs", []):
            if d["id"] not in seen_doc_ids:
                all_docs.append(d)
                seen_doc_ids.add(d["id"])

    docs_created = [d for d in all_docs if d["action"] == "created"]
    docs_linked  = [d for d in all_docs if d["action"] == "linked"]

    # Якщо все оброблено (немає pending) — сесія більше не потрібна, прибираємо
    if not pending:
        _delete_state(uid)

    return render_template(
        "import_cards/result.html",
        uid=uid,
        state=state,
        imported=imported,
        skipped=skipped,
        pending=pending,
        errors=errors,
        docs_created=docs_created,
        docs_linked=docs_linked,
    )


# ── API: пошук існуючої РВ ────────────────────────────────────────────────────

@bp.route("/api/check-rv")
@login_required
def api_check_rv():
    """Returns: {"ok": bool, "exists": bool, "sheet_id": int|null, "msg": str}"""
    number   = request.args.get("number", "").strip()
    doc_date = request.args.get("date", "").strip()
    if not number:
        return jsonify({"ok": False, "exists": False, "sheet_id": None, "msg": "number required"})

    conn = get_connection()
    row = conn.execute(
        """SELECT id FROM distribution_sheets
           WHERE is_external=1 AND external_number=?
             AND (doc_date=? OR ? IS NULL)""",
        (number, doc_date, doc_date),
    ).fetchone()
    conn.close()
    return jsonify({"ok": True, "exists": bool(row),
                    "sheet_id": row["id"] if row else None, "msg": ""})
