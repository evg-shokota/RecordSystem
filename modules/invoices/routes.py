"""
modules/invoices/routes.py — Накладні на видачу (Додаток 25)

Статуси:
  draft     — чернетка, номер не присвоєно
  created   — створена, ще не видана
  issued    — видана одержувачу (підписана)
  processed — проведена: списує з складу, записує на картку о/с / підрозділу
  cancelled — скасована
Author: White
"""
import json
import time
from datetime import datetime, date, timedelta
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, jsonify, session, flash
)
from core.auth import login_required
from core.db import get_connection
from core.utils import next_doc_number
from core.audit import log_action
from core.hooks import emit
from core.settings import get_setting, get_all_settings
from core.warehouse import get_stock_for_invoice as _get_stock
from core.military_logic import get_next_issue_date


def _default_signatories(settings: dict, direction: str = "issue",
                          recipient_name: str = "", recipient_rank: str = "") -> list:
    """Підписанти за замовчуванням з налаштувань."""
    sigs = []
    chief_name = settings.get("chief_name", "")
    chief_rank = settings.get("chief_rank", "")
    if chief_name:
        sigs.append({
            "role": settings.get("service_name", "Начальник речової служби"),
            "rank": chief_rank,
            "name": chief_name,
            "tag": "chief",
            "is_tvo": settings.get("chief_is_tvo") == "1",
        })

    wh_name = settings.get("warehouse_chief_name", "")
    wh_rank = settings.get("warehouse_chief_rank", "")

    if direction == "issue":
        # Здав = начальник складу, Прийняв = одержувач
        if wh_name:
            sigs.append({"role": "Здав", "rank": wh_rank, "name": wh_name, "tag": "given", "is_tvo": False})
        sigs.append({"role": "Прийняв", "rank": recipient_rank, "name": recipient_name, "tag": "received", "is_tvo": False})
    elif direction == "return":
        # Здав = той хто повертає (буде заповнено з форми), Прийняв = нач. складу
        sigs.append({"role": "Здав", "rank": "", "name": "", "tag": "given", "is_tvo": False})
        if wh_name:
            sigs.append({"role": "Прийняв", "rank": wh_rank, "name": wh_name, "tag": "received", "is_tvo": False})
    elif direction == "transfer":
        # Обидва вибираються при створенні
        sigs.append({"role": "Здав", "rank": "", "name": "", "tag": "given", "is_tvo": False})
        sigs.append({"role": "Прийняв", "rank": "", "name": "", "tag": "received", "is_tvo": False})
    else:
        # Fallback
        sigs.append({"role": "Видав", "rank": "", "name": "", "tag": "given", "is_tvo": False})
        sigs.append({"role": "Одержав", "rank": "", "name": "", "tag": "received", "is_tvo": False})

    clerk_name = settings.get("clerk_name", "")
    clerk_rank = settings.get("clerk_rank", "")
    if clerk_name:
        sigs.append({
            "role": "Діловод РС",
            "rank": clerk_rank,
            "name": clerk_name,
            "tag": "clerk",
            "is_tvo": False,
        })
    return sigs


def _parse_signatories_from_form() -> list:
    """Зібрати список підписантів з POST-форми."""
    roles   = request.form.getlist("signer_role[]")
    ranks   = request.form.getlist("signer_rank[]")
    names   = request.form.getlist("signer_name[]")
    tags    = request.form.getlist("signer_tag[]")
    tvos    = request.form.getlist("signer_tvo[]")  # checkbox values '1'

    result = []
    for i, role in enumerate(roles):
        result.append({
            "role":   role.strip(),
            "rank":   ranks[i].strip()  if i < len(ranks)  else "",
            "name":   names[i].strip()  if i < len(names)  else "",
            "tag":    tags[i]           if i < len(tags)    else "",
            "is_tvo": bool(tvos[i])     if i < len(tvos)    else False,
        })
    return result

bp = Blueprint("invoices", __name__, url_prefix="/invoices")

CATEGORIES = ["I", "II", "III"]


# ─────────────────────────────────────────────────────────────
#  Нумерація
# ─────────────────────────────────────────────────────────────

def _next_invoice_number(conn) -> tuple[str, int, int, str]:
    """Повертає (number, year, sequence_num, suffix)."""
    return next_doc_number(conn, "invoice", "РС")


# ─────────────────────────────────────────────────────────────
#  Список накладних
# ─────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    conn = get_connection()

    status_filter = request.args.get("status", "")
    search        = request.args.get("q", "").strip()

    where = ["1=1"]
    params = []
    if status_filter:
        where.append("i.status = ?")
        params.append(status_filter)
    if search:
        where.append("""(i.number LIKE ? OR
                         p.last_name LIKE ? OR p.first_name LIKE ? OR
                         u.name LIKE ? OR
                         EXISTS (SELECT 1 FROM invoice_items ii
                                 JOIN item_dictionary d ON ii.item_id=d.id
                                 WHERE ii.invoice_id=i.id AND d.name LIKE ?))""")
        like = f"%{search}%"
        params += [like, like, like, like, like]

    rows = conn.execute(f"""
        SELECT i.*,
               p.last_name || ' ' || p.first_name || ' ' ||
               COALESCE(p.middle_name,'') AS recipient_person_name,
               p.rank AS recipient_rank,
               u.name AS recipient_unit_name
        FROM invoices i
        LEFT JOIN personnel p ON i.recipient_personnel_id = p.id
        LEFT JOIN units     u ON i.recipient_unit_id      = u.id
        WHERE {' AND '.join(where)}
        ORDER BY i.created_at DESC
        LIMIT 300
    """, params).fetchall()

    conn.close()
    return render_template(
        "invoices/index.html",
        rows=rows,
        status_filter=status_filter,
        search=search,
        today_str=date.today().isoformat(),
    )


# ─────────────────────────────────────────────────────────────
#  Перегляд накладної
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:inv_id>")
@login_required
def view(inv_id):
    conn = get_connection()
    inv = conn.execute("""
        SELECT i.*,
               p.last_name || ' ' || p.first_name || ' ' ||
               COALESCE(p.middle_name,'') AS recipient_person_name,
               p.rank  AS recipient_rank,
               u.name  AS recipient_unit_name
        FROM invoices i
        LEFT JOIN personnel p ON i.recipient_personnel_id = p.id
        LEFT JOIN units     u ON i.recipient_unit_id      = u.id
        WHERE i.id = ?
    """, (inv_id,)).fetchone()

    if not inv:
        conn.close()
        flash("Накладну не знайдено", "danger")
        return redirect(url_for("invoices.index"))

    items = conn.execute("""
        SELECT ii.*, d.name AS item_name, d.unit_of_measure, d.has_serial_number
        FROM invoice_items ii
        JOIN item_dictionary d ON ii.item_id = d.id
        WHERE ii.invoice_id = ?
        ORDER BY d.name
    """, (inv_id,)).fetchall()

    conn.close()
    return render_template("invoices/view.html", inv=inv, items=items)


# ─────────────────────────────────────────────────────────────
#  Створення накладної
# ─────────────────────────────────────────────────────────────

def _form_data_for_invoice(conn, settings, exclude_invoice_id=None):
    """Загальні дані для форми (для new та edit)."""
    personnel_list = conn.execute("""
        SELECT p.id,
               p.last_name || ' ' || p.first_name || ' ' ||
               COALESCE(p.middle_name,'') AS full_name,
               p.rank, u.name AS unit_name
        FROM personnel p
        LEFT JOIN units u ON p.unit_id = u.id
        WHERE p.is_active = 1
        ORDER BY p.last_name, p.first_name
    """).fetchall()
    units_list = conn.execute("SELECT id, name FROM units ORDER BY name").fetchall()
    stock = _get_stock(conn, exclude_invoice_id=exclude_invoice_id)
    from core.warehouse import get_norm_groups as _get_norm_groups
    norm_groups = _get_norm_groups(conn)
    return personnel_list, units_list, stock, norm_groups


def _validate_and_collect_items(conn, item_ids, cats, prices, planned_qtys, serials_raw,
                                is_draft=False):
    """Валідація та збір позицій форми. Повертає (errors, rows_to_insert)."""
    errors = []
    rows_to_insert = []
    for i, item_id in enumerate(item_ids):
        if not item_id:
            continue
        try:
            qty   = float(planned_qtys[i]) if planned_qtys[i] else 0
            price = float(prices[i])        if prices[i]       else 0
        except (ValueError, IndexError):
            if not is_draft:
                errors.append(f"Рядок {i+1}: невірне значення")
            continue
        if qty <= 0:
            if not is_draft:
                errors.append(f"Рядок {i+1}: кількість має бути > 0")
            continue

        cat   = cats[i]        if i < len(cats)       else "I"
        s_raw = serials_raw[i] if i < len(serials_raw) else ""

        item_row = conn.execute(
            "SELECT has_serial_number FROM item_dictionary WHERE id=?", (item_id,)
        ).fetchone()
        if item_row and item_row["has_serial_number"] and not is_draft:
            serials = [s.strip() for s in s_raw.split(",") if s.strip()]
            if not serials:
                errors.append(f"Рядок {i+1}: потрібен серійний номер")
                continue
        else:
            serials = [s.strip() for s in s_raw.split(",") if s.strip()]

        rows_to_insert.append({
            "item_id": item_id, "category": cat,
            "price": price, "planned_qty": qty, "serials": serials,
        })
    return errors, rows_to_insert


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    conn = get_connection()
    settings = get_all_settings()
    is_external = request.args.get("is_external", "0") == "1" or request.form.get("is_external") == "1"

    # ── Зовнішня (вже надрукована) накладна ────────────────────
    if is_external:
        personnel_list, units_list, _, norm_groups = _form_data_for_invoice(conn, settings)
        # Всі позиції словника (не зі складу — зовнішня)
        all_items = conn.execute(
            "SELECT id, name, unit_of_measure FROM item_dictionary ORDER BY name"
        ).fetchall()

        if request.method == "POST":
            import os
            from werkzeug.utils import secure_filename as _secure
            errors = []
            external_number  = request.form.get("external_number", "").strip()
            doc_date         = request.form.get("doc_date") or date.today().isoformat()
            recipient_type   = request.form.get("recipient_type", "personnel")
            rec_pers_id      = request.form.get("recipient_personnel_id") or None
            rec_unit_id      = request.form.get("recipient_unit_id") or None
            notes_val        = request.form.get("notes", "").strip()

            if not external_number:
                errors.append("Вкажіть номер документа")
            if recipient_type == "personnel" and not rec_pers_id:
                errors.append("Оберіть одержувача (особу)")
            if recipient_type == "unit" and not rec_unit_id:
                errors.append("Оберіть підрозділ-одержувач")

            # Позиції (без перевірки залишків)
            item_ids  = request.form.getlist("item_id[]")
            qtys      = request.form.getlist("planned_qty[]")
            prices    = request.form.getlist("price[]")
            cats      = request.form.getlist("category[]")
            rows_to_insert = []
            for item_id, qty_s, price_s, cat in zip(item_ids, qtys, prices, cats):
                if not item_id:
                    continue
                try:
                    qty = float(qty_s)
                    price = float(price_s) if price_s else 0.0
                except ValueError:
                    errors.append(f"Невірна кількість або ціна для позиції")
                    continue
                if qty <= 0:
                    continue
                rows_to_insert.append({
                    "item_id": int(item_id), "category": cat or "I",
                    "price": price, "planned_qty": qty, "serials": None,
                })

            if not rows_to_insert and not errors:
                errors.append("Додайте хоча б одну позицію")

            if not errors:
                total_sum = sum(r["planned_qty"] * r["price"] for r in rows_to_insert)
                number, year, seq, suffix = _next_invoice_number(conn)

                cur = conn.execute("""
                    INSERT INTO invoices
                        (number, year, sequence_num, suffix,
                         invoice_type, direction,
                         recipient_type, recipient_personnel_id, recipient_unit_id,
                         base_document, valid_until,
                         total_sum, status,
                         is_external, external_number,
                         notes, created_by, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                            datetime('now','localtime'), datetime('now','localtime'))
                """, (
                    number, year, seq, suffix,
                    "invoice", "issue",
                    recipient_type, rec_pers_id, rec_unit_id,
                    "", None,
                    total_sum, "issued",
                    1, external_number,
                    notes_val, session.get("user_id"),
                ))
                invoice_id = cur.lastrowid

                for row in rows_to_insert:
                    conn.execute("""
                        INSERT INTO invoice_items
                            (invoice_id, item_id, planned_qty, price, category, serial_numbers)
                        VALUES (?,?,?,?,?,?)
                    """, (invoice_id, row["item_id"], row["planned_qty"],
                          row["price"], row["category"], None))

                # Зберегти скан якщо завантажено
                scan_file = request.files.get("scan")
                if scan_file and scan_file.filename:
                    ext = os.path.splitext(scan_file.filename)[1].lower()
                    if ext in (".pdf", ".jpg", ".jpeg", ".png"):
                        filepath, rel_path = _invoice_scan_storage(conn, invoice_id, ext)
                        scan_file.save(str(filepath))
                        orig_name = _secure(scan_file.filename)
                        conn.execute(
                            "UPDATE invoices SET scan_path=?, scan_original_name=? WHERE id=?",
                            (rel_path, orig_name, invoice_id)
                        )

                conn.commit()
                log_action("add", "invoices", invoice_id,
                           new_data={"number": number, "direction": "issue",
                                     "status": "issued", "is_external": 1})
                emit("invoice.created", invoice_id=invoice_id,
                     data={"number": number, "direction": "issue", "is_external": True})
                conn.close()
                return redirect(url_for("invoices.view", inv_id=invoice_id))

            conn.close()
            return render_template(
                "invoices/form_external.html",
                personnel_list=personnel_list, units_list=units_list,
                all_items=[dict(r) for r in all_items],
                categories=CATEGORIES, settings=settings,
                errors=errors, form=request.form,
                today=date.today().isoformat(),
            )

        conn.close()
        return render_template(
            "invoices/form_external.html",
            personnel_list=personnel_list, units_list=units_list,
            all_items=[dict(r) for r in all_items],
            categories=CATEGORIES, settings=settings,
            errors=[], form={},
            today=date.today().isoformat(),
        )

    # ── Звичайна накладна ───────────────────────────────────────
    personnel_list, units_list, stock, norm_groups = _form_data_for_invoice(conn, settings)

    if request.method == "POST":
        errors = []
        direction            = request.form.get("direction", "issue")
        recipient_type       = request.form.get("recipient_type", "personnel")
        rec_pers_id          = request.form.get("recipient_personnel_id") or None
        rec_unit_id          = request.form.get("recipient_unit_id") or None
        sender_unit_id       = request.form.get("sender_unit_id") or None
        sender_personnel_id  = request.form.get("sender_personnel_id") or None
        base_document        = request.form.get("base_document", "").strip()
        valid_days           = request.form.get("valid_days", "10")
        notes_val            = request.form.get("notes", "").strip()
        signatories          = _parse_signatories_from_form()
        is_draft             = request.form.get("save_as") == "draft"

        if not is_draft:
            if recipient_type == "personnel" and not rec_pers_id:
                errors.append("Оберіть одержувача (особу)")
            if recipient_type == "unit" and not rec_unit_id:
                errors.append("Оберіть підрозділ-одержувач")

        item_errors, rows_to_insert = _validate_and_collect_items(
            conn,
            request.form.getlist("item_id[]"),
            request.form.getlist("category[]"),
            request.form.getlist("price[]"),
            request.form.getlist("planned_qty[]"),
            request.form.getlist("serial_numbers[]"),
            is_draft=is_draft,
        )
        errors += item_errors
        # Чернетка може зберігатися без позицій
        if not rows_to_insert and not errors and not is_draft:
            errors.append("Додайте хоча б одну позицію")

        if not errors:
            try:
                vd = int(valid_days)
                valid_until = (date.today() + timedelta(days=vd)).isoformat()
            except ValueError:
                valid_until = None

            total_sum = sum(r["planned_qty"] * r["price"] for r in rows_to_insert)

            if is_draft:
                number = f"ЧЕРНЕТКА-{int(time.time()*1000)}"
                year   = date.today().year
                seq    = 0
                suffix = ""
                status = "draft"
            else:
                number, year, seq, suffix = _next_invoice_number(conn)
                status = "created"

            sigs_json = json.dumps(signatories, ensure_ascii=False)

            cur = conn.execute("""
                INSERT INTO invoices
                    (number, year, sequence_num, suffix,
                     invoice_type, direction,
                     recipient_type, recipient_personnel_id, recipient_unit_id,
                     sender_unit_id, sender_personnel_id,
                     signatories,
                     base_document, valid_until,
                     total_sum, status,
                     notes, created_by, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        datetime('now','localtime'), datetime('now','localtime'))
            """, (
                number, year, seq, suffix,
                "invoice", direction,
                recipient_type, rec_pers_id, rec_unit_id,
                sender_unit_id, sender_personnel_id,
                sigs_json,
                base_document, valid_until,
                total_sum, status,
                notes_val, session.get("user_id"),
            ))
            invoice_id = cur.lastrowid

            for row in rows_to_insert:
                sn_json = json.dumps(row["serials"], ensure_ascii=False) if row["serials"] else None
                conn.execute("""
                    INSERT INTO invoice_items
                        (invoice_id, item_id, planned_qty, price, category, serial_numbers)
                    VALUES (?,?,?,?,?,?)
                """, (invoice_id, row["item_id"], row["planned_qty"],
                      row["price"], row["category"], sn_json))

            conn.commit()
            log_action("add", "invoices", invoice_id,
                       new_data={"number": number, "direction": direction, "status": status})
            emit("invoice.created", invoice_id=invoice_id,
                 data={"number": number, "direction": direction})
            conn.close()
            back = request.form.get("back", "")
            return redirect(url_for("invoices.view", inv_id=invoice_id, back=back) if back else url_for("invoices.view", inv_id=invoice_id))

        conn.close()
        return render_template(
            "invoices/form.html",
            personnel_list=personnel_list, units_list=units_list,
            stock=stock, categories=CATEGORIES, settings=settings,
            errors=errors, form=request.form,
            initial_signatories=signatories,
            default_signatories=_default_signatories(settings, direction),
            edit_mode=False, inv=None,
            existing_items_json="[]",
            norm_groups=norm_groups,
        )

    # Префіл отримувача якщо переходимо з картки особи (?recipient_personnel_id=N)
    prefill_pers_id = request.args.get("recipient_personnel_id", type=int)
    prefill_form = {"valid_days": get_setting("invoice_valid_days", "10")}
    if prefill_pers_id:
        prefill_form["recipient_personnel_id"] = str(prefill_pers_id)
        prefill_form["recipient_type"] = "personnel"

    conn.close()
    return render_template(
        "invoices/form.html",
        personnel_list=personnel_list, units_list=units_list,
        stock=stock, categories=CATEGORIES, settings=settings,
        errors=[], form=prefill_form,
        initial_signatories=[],
        default_signatories=_default_signatories(settings),
        edit_mode=False, inv=None,
        existing_items_json="[]",
        norm_groups=norm_groups,
    )


# ─────────────────────────────────────────────────────────────
#  Редагування накладної
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:inv_id>/edit", methods=["GET", "POST"])
@login_required
def edit(inv_id):
    conn = get_connection()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()

    if not inv:
        conn.close()
        flash("Накладну не знайдено", "danger")
        return redirect(url_for("invoices.index"))

    if inv["status"] not in ("draft", "created", "issued"):
        conn.close()
        flash("Проведену або скасовану накладну не можна редагувати", "warning")
        return redirect(url_for("invoices.view", inv_id=inv_id))

    settings = get_all_settings()
    personnel_list, units_list, stock, norm_groups = _form_data_for_invoice(conn, settings, exclude_invoice_id=inv_id)

    existing_items = conn.execute("""
        SELECT ii.*, d.name AS item_name, d.unit_of_measure
        FROM invoice_items ii
        JOIN item_dictionary d ON ii.item_id = d.id
        WHERE ii.invoice_id = ?
        ORDER BY d.name
    """, (inv_id,)).fetchall()

    # Підписанти з БД або дефолт
    try:
        current_sigs = json.loads(inv["signatories"]) if inv["signatories"] else []
    except (json.JSONDecodeError, TypeError):
        current_sigs = []

    if request.method == "POST":
        errors = []
        direction           = request.form.get("direction", inv["direction"])
        recipient_type      = request.form.get("recipient_type", inv["recipient_type"])
        rec_pers_id         = request.form.get("recipient_personnel_id") or None
        rec_unit_id         = request.form.get("recipient_unit_id") or None
        sender_unit_id      = request.form.get("sender_unit_id") or None
        sender_personnel_id = request.form.get("sender_personnel_id") or None
        base_document       = request.form.get("base_document", "").strip()
        valid_days          = request.form.get("valid_days", "10")
        notes_val           = request.form.get("notes", "").strip()
        signatories         = _parse_signatories_from_form()

        if recipient_type == "personnel" and not rec_pers_id:
            errors.append("Оберіть одержувача (особу)")
        if recipient_type == "unit" and not rec_unit_id:
            errors.append("Оберіть підрозділ-одержувач")

        item_errors, rows_to_insert = _validate_and_collect_items(
            conn,
            request.form.getlist("item_id[]"),
            request.form.getlist("category[]"),
            request.form.getlist("price[]"),
            request.form.getlist("planned_qty[]"),
            request.form.getlist("serial_numbers[]"),
        )
        errors += item_errors
        if not rows_to_insert and not errors:
            errors.append("Додайте хоча б одну позицію")

        if not errors:
            try:
                vd = int(valid_days)
                valid_until = (date.today() + timedelta(days=vd)).isoformat()
            except ValueError:
                valid_until = None

            total_sum = sum(r["planned_qty"] * r["price"] for r in rows_to_insert)
            sigs_json = json.dumps(signatories, ensure_ascii=False)

            conn.execute("""
                UPDATE invoices SET
                    direction=?, recipient_type=?,
                    recipient_personnel_id=?, recipient_unit_id=?,
                    sender_unit_id=?, sender_personnel_id=?,
                    signatories=?, base_document=?, valid_until=?,
                    total_sum=?, notes=?, updated_at=datetime('now','localtime')
                WHERE id=?
            """, (
                direction, recipient_type,
                rec_pers_id, rec_unit_id,
                sender_unit_id, sender_personnel_id,
                sigs_json, base_document, valid_until,
                total_sum, notes_val, inv_id,
            ))

            # Замінити позиції
            conn.execute("DELETE FROM invoice_items WHERE invoice_id=?", (inv_id,))
            for row in rows_to_insert:
                sn_json = json.dumps(row["serials"], ensure_ascii=False) if row["serials"] else None
                conn.execute("""
                    INSERT INTO invoice_items
                        (invoice_id, item_id, planned_qty, price, category, serial_numbers)
                    VALUES (?,?,?,?,?,?)
                """, (inv_id, row["item_id"], row["planned_qty"],
                      row["price"], row["category"], sn_json))

            conn.commit()
            log_action("edit", "invoices", inv_id, new_data={"direction": direction})
            conn.close()
            flash("Накладну оновлено", "success")
            return redirect(url_for("invoices.view", inv_id=inv_id))

        conn.close()
        return render_template(
            "invoices/form.html",
            personnel_list=personnel_list, units_list=units_list,
            stock=stock, categories=CATEGORIES, settings=settings,
            errors=errors, form=request.form,
            initial_signatories=signatories,
            default_signatories=_default_signatories(settings, direction),
            edit_mode=True, inv=inv,
            existing_items_json="[]",
            norm_groups=norm_groups,
        )

    # GET — заповнити форму поточними даними
    form_data = {
        "direction":              inv["direction"],
        "recipient_type":         inv["recipient_type"],
        "recipient_personnel_id": str(inv["recipient_personnel_id"] or ""),
        "recipient_unit_id":      str(inv["recipient_unit_id"] or ""),
        "sender_unit_id":         str(inv["sender_unit_id"] or "") if "sender_unit_id" in inv.keys() else "",
        "sender_personnel_id":    str(inv["sender_personnel_id"] or "") if "sender_personnel_id" in inv.keys() else "",
        "base_document":          inv["base_document"] or "",
        "notes":                  inv["notes"] or "",
        "valid_days":             get_setting("invoice_valid_days", "10"),
    }
    existing_items_json_str = json.dumps(
        [dict(r) for r in existing_items], ensure_ascii=False
    )
    conn.close()
    return render_template(
        "invoices/form.html",
        personnel_list=personnel_list, units_list=units_list,
        stock=stock, categories=CATEGORIES, settings=settings,
        errors=[], form=form_data,
        initial_signatories=current_sigs or _default_signatories(settings),
        default_signatories=_default_signatories(settings),
        edit_mode=True, inv=inv,
        existing_items_json=existing_items_json_str,
        norm_groups=norm_groups,
    )


# ─────────────────────────────────────────────────────────────
#  Присвоїти номер чернетці
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:inv_id>/assign_number", methods=["POST"])
@login_required
def assign_number(inv_id):
    """Присвоїти номер чернетці → статус created."""
    conn = get_connection()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv or inv["status"] != "draft":
        conn.close()
        flash("Накладна не є чернеткою", "warning")
        return redirect(url_for("invoices.view", inv_id=inv_id))

    number, year, seq, suffix = _next_invoice_number(conn)
    conn.execute("""
        UPDATE invoices SET number=?, year=?, sequence_num=?, suffix=?,
               status='created', updated_at=datetime('now','localtime')
        WHERE id=?
    """, (number, year, seq, suffix, inv_id))
    conn.commit()
    log_action("status_change", "invoices", inv_id,
               old_data={"status": "draft"}, new_data={"status": "created", "number": number})
    conn.close()
    flash(f"Накладній присвоєно номер {number}", "success")
    return redirect(url_for("invoices.view", inv_id=inv_id))


# ─────────────────────────────────────────────────────────────
#  Зміна статусу: видати / провести / скасувати
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:inv_id>/issue", methods=["POST"])
@login_required
def issue(inv_id):
    """Позначити як видану (підписана одержувачем)."""
    conn = get_connection()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if inv and inv["status"] == "created":
        if not inv["is_external"] and not inv["scan_path"]:
            conn.close()
            flash("Завантажте скан підписаної накладної перед тим як позначити її виданою.", "danger")
            return redirect(url_for("invoices.view", inv_id=inv_id))
        conn.execute(
            "UPDATE invoices SET status='issued', issued_date=date('now','localtime'), "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (inv_id,)
        )
        conn.commit()
        log_action("status_change", "invoices", inv_id,
                   old_data={"status": "created"}, new_data={"status": "issued"})
    conn.close()
    return redirect(url_for("invoices.view", inv_id=inv_id))


@bp.route("/<int:inv_id>/receive", methods=["POST"])
@login_required
def receive(inv_id):
    """
    Позначити як отриману (майно фізично отримано, вносяться actual_qty).
    issued → received. Майно переходить в "зарезервовано" для підрахунку складу.
    """
    conn = get_connection()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv or inv["status"] != "issued":
        conn.close()
        flash("Накладна не в статусі 'видано'", "warning")
        return redirect(url_for("invoices.view", inv_id=inv_id))

    data = request.get_json(silent=True) or {}
    items = data.get("items", [])

    try:
        for item in items:
            conn.execute(
                "UPDATE invoice_items SET actual_qty=?, serial_numbers=? WHERE id=? AND invoice_id=?",
                (item.get("actual_qty"), item.get("serial_numbers"), item["id"], inv_id)
            )
        conn.execute(
            "UPDATE invoices SET status='received', updated_at=datetime('now','localtime') WHERE id=?",
            (inv_id,)
        )
        conn.commit()
        log_action("status_change", "invoices", inv_id,
                   old_data={"status": "issued"}, new_data={"status": "received"})
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"ok": False, "error": str(e)}), 500


def _invoice_scan_storage(conn, inv_id: int, ext: str):
    """Повертає (filepath, rel_path) для збереження скану накладної.
    Якщо накладна прив'язана до конкретного військовослужбовця — зберігає в його папці.
    Інакше — у scans/invoices/."""
    from core.settings import get_storage_path
    from pathlib import Path

    inv = conn.execute(
        "SELECT number, issued_date, created_at, recipient_personnel_id FROM invoices WHERE id=?",
        (inv_id,)
    ).fetchone()

    # Формуємо ім'я файлу: invoice_<номер>_<дата>.<ext>
    number_safe = (inv["number"] or str(inv_id)).replace("/", "-").replace(" ", "_")
    date_str = (inv["issued_date"] or inv["created_at"] or "")[:10].replace("-", "")
    filename = f"invoice_{number_safe}_{date_str}{ext}" if date_str else f"invoice_{number_safe}{ext}"

    person_id = inv["recipient_personnel_id"]
    if person_id:
        person = conn.execute("SELECT * FROM personnel WHERE id=?", (person_id,)).fetchone()
        if person:
            # імпортуємо хелпер з personnel routes
            from modules.personnel.routes import _person_folder_name
            folder = get_storage_path() / "personnel" / _person_folder_name(person) / "scans"
            folder.mkdir(parents=True, exist_ok=True)
            rel = f"personnel/{_person_folder_name(person)}/scans/{filename}"
            return folder / filename, rel

    # Загальна папка якщо без конкретної особи
    folder = get_storage_path() / "scans" / "invoices"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / filename, f"scans/invoices/{filename}"


@bp.route("/<int:inv_id>/scan", methods=["POST"])
@login_required
def scan_upload(inv_id):
    """Завантажити скан документа (для будь-якого статусу крім processed)."""
    import os
    from werkzeug.utils import secure_filename
    conn = get_connection()
    inv = conn.execute("SELECT id, status FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv:
        conn.close()
        return jsonify({"ok": False, "error": "Не знайдено"}), 404

    f = request.files.get("scan")
    if not f or not f.filename:
        conn.close()
        return jsonify({"ok": False, "error": "Файл не обрано"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".pdf", ".jpg", ".jpeg", ".png"):
        conn.close()
        return jsonify({"ok": False, "error": "Дозволені формати: PDF, JPG, PNG"}), 400

    filepath, rel_path = _invoice_scan_storage(conn, inv_id, ext)
    original_name = secure_filename(f.filename)
    f.save(str(filepath))

    conn.execute(
        "UPDATE invoices SET scan_path=?, scan_original_name=?, updated_at=datetime('now','localtime') WHERE id=?",
        (rel_path, original_name, inv_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "path": rel_path, "name": original_name})


@bp.route("/<int:inv_id>/scan/delete", methods=["POST"])
@login_required
def scan_delete(inv_id):
    """Видалити прикріплений скан."""
    import os
    from core.db import get_db_path
    conn = get_connection()
    inv = conn.execute("SELECT scan_path FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if inv and inv["scan_path"]:
        from core.settings import get_storage_path
        full = get_storage_path() / inv["scan_path"]
        try:
            full.unlink(missing_ok=True)
        except OSError:
            pass
        conn.execute(
            "UPDATE invoices SET scan_path=NULL, scan_original_name=NULL, updated_at=datetime('now','localtime') WHERE id=?",
            (inv_id,)
        )
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:inv_id>/process", methods=["POST"])
@login_required
def process(inv_id):
    """
    Провести накладну:
    - Зменшує залишки складу (через item_serials для серійних)
    - Записує майно на картку о/с або підрозділу (personnel_items / unit_items)
    - Статус → processed
    """
    conn = get_connection()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()

    if not inv or inv["status"] not in ("created", "issued", "received"):
        conn.close()
        flash("Накладна вже проведена або скасована", "warning")
        return redirect(url_for("invoices.view", inv_id=inv_id))

    items = conn.execute("""
        SELECT ii.*, d.name AS item_name, d.has_serial_number
        FROM invoice_items ii
        JOIN item_dictionary d ON ii.item_id = d.id
        WHERE ii.invoice_id = ?
    """, (inv_id,)).fetchall()

    inv_date_str = inv.get("issued_date") or inv.get("created_at") or date.today().isoformat()
    today    = inv_date_str[:10]
    errors   = []

    # Перевірка залишків через централізовану логіку складу
    from core.warehouse import get_stock
    stock = {(r["item_id"], r["category"], r["price"]): r
             for r in get_stock(conn, exclude_invoice_id=inv_id)}

    for item in items:
        qty = item["actual_qty"] if item["actual_qty"] is not None else item["planned_qty"]
        if qty <= 0:
            continue
        key = (item["item_id"], item["category"], item["price"])
        s = stock.get(key)
        available = (s["qty_free"] if s else 0) + (s["qty_reserved"] if s else 0)
        if qty > available:
            errors.append(
                f"Недостатньо на складі: {item['item_name']} "
                f"кат.{item['category']} (потрібно {qty}, є {round(available, 4)})"
            )

    if errors:
        conn.close()
        flash(" | ".join(errors), "danger")
        return redirect(url_for("invoices.view", inv_id=inv_id))

    # Дані особи для розрахунку циклів (якщо видача на о/с)
    person_info: dict = {}
    if inv["direction"] == "issue" and inv["recipient_type"] == "personnel" and inv["recipient_personnel_id"]:
        pid = inv["recipient_personnel_id"]
        p = conn.execute(
            "SELECT service_type, enroll_date FROM personnel WHERE id=?", (pid,)
        ).fetchone()
        person_info = {
            "service_type": (p["service_type"] if p else None) or "mobilized",
            "norm_date":    p["enroll_date"] if p else None,
        }

    # Кеш wear_months/norm_qty по item_id для цієї особи
    wear_cache: dict[int, dict] = {}

    # Проводимо — всі записи в одній транзакції
    try:
        for item in items:
            qty = item["actual_qty"] if item["actual_qty"] is not None else item["planned_qty"]

            # Серійні номери — позначити як видані
            if item["has_serial_number"] and item["serial_numbers"]:
                try:
                    sns = json.loads(item["serial_numbers"])
                except (json.JSONDecodeError, TypeError):
                    sns = []
                for sn in sns:
                    conn.execute(
                        "UPDATE item_serials SET status='issued' "
                        "WHERE item_id=? AND serial_number=? AND status='stock'",
                        (item["item_id"], sn)
                    )

            # Записати на картку о/с або підрозділу
            if inv["direction"] == "issue":
                # Категорія I при видачі стає II (майно введено в експлуатацію)
                issued_category = "II" if item["category"] == "I" else item["category"]
                if inv["recipient_type"] == "personnel" and inv["recipient_personnel_id"]:
                    pid          = inv["recipient_personnel_id"]
                    service_type = person_info.get("service_type", "mobilized")
                    norm_date    = person_info.get("norm_date")
                    item_id      = item["item_id"]

                    # Отримати wear_months і norm_qty з норми особи
                    if item_id not in wear_cache:
                        w = conn.execute("""
                            SELECT sniw.wear_months,
                                   COALESCE(sniw.qty, sni.quantity) AS quantity
                            FROM personnel p
                            JOIN personnel_norms pn ON pn.personnel_id = p.id
                            JOIN supply_norm_items sni ON sni.norm_id = pn.norm_id AND sni.item_id = ?
                            LEFT JOIN supply_norm_item_wear sniw
                                   ON sniw.norm_item_id = sni.id
                                  AND sniw.personnel_cat = p.personnel_cat
                            WHERE p.id = ?
                            LIMIT 1
                        """, (item_id, pid)).fetchone()
                        wear_cache[item_id] = {
                            "wear_months": int(w["wear_months"] or 0) if w else 0,
                            "norm_qty":    float(w["quantity"] or 0)  if w else 0.0,
                        }

                    wdata        = wear_cache[item_id]
                    wear_months  = wdata["wear_months"]
                    norm_qty_val = wdata["norm_qty"]
                    cycle_start  = today

                    next_dt = get_next_issue_date(
                        service_type     = service_type,
                        cycle_start_date = cycle_start,
                        norm_date        = norm_date,
                        wear_months      = wear_months,
                    )
                    next_issue = next_dt.isoformat() if next_dt else None

                    conn.execute("""
                        INSERT INTO personnel_items
                            (personnel_id, item_id, quantity, price, category,
                             invoice_id, source_type, issue_date,
                             wear_started_date, status,
                             cycle_start_date, norm_qty_at_issue,
                             wear_months_at_issue, next_issue_date,
                             created_at, updated_at)
                        VALUES (?,?,?,?,?,?,'invoice',?,?,
                                'active', ?,?,?,?,
                                datetime('now','localtime'), datetime('now','localtime'))
                    """, (
                        pid, item_id, qty, item["price"], issued_category,
                        inv_id, today, today,
                        cycle_start, norm_qty_val, wear_months, next_issue,
                    ))
                    emit("item.issued",
                         personnel_id=pid,
                         item_id=item_id, quantity=qty)

                elif inv["recipient_type"] == "unit" and inv["recipient_unit_id"]:
                    conn.execute("""
                        INSERT INTO unit_items
                            (unit_id, item_id, quantity, price, category,
                             invoice_id, source_type, issue_date,
                             status, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,'invoice',?,
                                'active', datetime('now','localtime'), datetime('now','localtime'))
                    """, (
                        inv["recipient_unit_id"],
                        item["item_id"], qty, item["price"], issued_category,
                        inv_id, today,
                    ))

        conn.execute("""
            UPDATE invoices
            SET status='processed', updated_at=datetime('now','localtime')
            WHERE id=?
        """, (inv_id,))
        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        flash(f"Помилка при проведенні: {e}", "danger")
        return redirect(url_for("invoices.view", inv_id=inv_id))

    log_action("status_change", "invoices", inv_id,
               old_data={"status": inv["status"]}, new_data={"status": "processed"})
    emit("invoice.processed", invoice_id=inv_id)

    conn.close()
    flash("Накладну проведено. Залишки складу оновлено.", "success")
    return redirect(url_for("invoices.view", inv_id=inv_id))


@bp.route("/<int:inv_id>/cancel", methods=["POST"])
@login_required
def cancel(inv_id):
    """Скасувати накладну (тільки непроведену)."""
    conn = get_connection()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    comment = request.form.get("comment", "").strip()

    if inv and inv["status"] in ("draft", "created", "issued", "received"):
        conn.execute("""
            UPDATE invoices
            SET status='cancelled', cancel_comment=?, updated_at=datetime('now','localtime')
            WHERE id=?
        """, (comment, inv_id))
        conn.commit()
        log_action("status_change", "invoices", inv_id,
                   old_data={"status": inv["status"]},
                   new_data={"status": "cancelled", "comment": comment})
        emit("invoice.cancelled", invoice_id=inv_id)
    conn.close()
    return redirect(url_for("invoices.view", inv_id=inv_id))


@bp.route("/<int:inv_id>/delete", methods=["POST"])
@login_required
def delete(inv_id):
    """Видалити накладну (тільки скасовану або чернетку)."""
    conn = get_connection()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if inv and inv["status"] in ("draft", "created", "cancelled"):
        conn.execute("DELETE FROM invoice_items WHERE invoice_id=?", (inv_id,))
        conn.execute("DELETE FROM invoices WHERE id=?", (inv_id,))
        conn.commit()
        log_action("delete", "invoices", inv_id, old_data={"number": inv["number"]})
    else:
        flash("Не можна видалити проведену або видану накладну", "warning")
    conn.close()
    return redirect(url_for("invoices.index"))


# ─────────────────────────────────────────────────────────────
#  Редагування фактичної кількості (при проведенні)
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:inv_id>/set_actual", methods=["POST"])
@login_required
def set_actual(inv_id):
    """Оновити фактичні кількості позицій перед проведенням."""
    conn = get_connection()
    inv = conn.execute("SELECT status FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv or inv["status"] == "processed":
        conn.close()
        return jsonify({"ok": False, "msg": "Накладна вже проведена"}), 400

    data = request.get_json(silent=True) or {}
    # data = {"items": [{"id": item_id, "actual_qty": qty, "serial_numbers": "..."}]}
    for item in data.get("items", []):
        conn.execute(
            "UPDATE invoice_items SET actual_qty=?, serial_numbers=? WHERE id=? AND invoice_id=?",
            (item.get("actual_qty"), item.get("serial_numbers"), item["id"], inv_id)
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  JSON API — пошук о/с
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
#  РЕНДЕР / ДРУК через шаблон документа
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:inv_id>/render")
@login_required
def render_invoice(inv_id):
    """
    Рендер накладної через шаблон документа.
    ?tpl_id=<id> — конкретний шаблон; якщо не вказано — дефолтний для 'invoice'.
    Повертає standalone HTML для window.print() або iframe.
    """
    from core.renderer import render_doc, get_template_html

    conn = get_connection()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv:
        conn.close()
        return "Накладну не знайдено", 404

    items = conn.execute("""
        SELECT ii.*, d.name AS item_name, d.unit_of_measure
        FROM invoice_items ii
        JOIN item_dictionary d ON ii.item_id = d.id
        WHERE ii.invoice_id = ?
        ORDER BY ii.id
    """, (inv_id,)).fetchall()

    tpl_id = request.args.get("tpl_id", type=int)

    # Якщо є збережений індивідуальний HTML — використовуємо його
    if inv["body_html"]:
        html_tpl = inv["body_html"]
        tpl = None
        # Дістаємо метадані шаблону для налаштувань сторінки
        _, tpl = get_template_html(conn, "invoice", tpl_id)
    else:
        html_tpl, tpl = get_template_html(conn, "invoice", tpl_id)

    if not html_tpl:
        conn.close()
        return render_template("invoices/print_fallback.html", inv=dict(inv),
                               items=[dict(i) for i in items])

    data = {
        "html":      html_tpl,
        "invoice":   dict(inv),
        "items":     [dict(i) for i in items],
    }
    rendered_body = render_doc("invoice", data, conn)
    conn.close()

    return render_template(
        "doc_templates/print_render.html",
        rendered_body=rendered_body,
        tpl=dict(tpl) if tpl else {},
        doc_title=f"Накладна {inv['number']}",
    )


# ─────────────────────────────────────────────────────────────
#  Preview + редагування тіла документа
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:inv_id>/preview", methods=["POST"])
@login_required
def preview(inv_id):
    """
    POST JSON: {html?: string, tpl_id?: int}
    Рендерить накладну з реальними даними і повертає готовий HTML-фрагмент
    для відображення в iframe модального вікна.
    """
    from core.renderer import render_doc, get_template_html

    conn = get_connection()
    inv = conn.execute(
        """SELECT i.*,
                  p.last_name || ' ' || p.first_name || COALESCE(' '||p.middle_name,'') AS recipient_name,
                  p.rank AS recipient_rank,
                  u.name AS recipient_unit
           FROM invoices i
           LEFT JOIN personnel p ON i.recipient_personnel_id = p.id
           LEFT JOIN units     u ON i.recipient_unit_id = u.id
           WHERE i.id=?""",
        (inv_id,)
    ).fetchone()
    if not inv:
        conn.close()
        return jsonify({"ok": False, "msg": "Накладну не знайдено"}), 404

    items = conn.execute(
        """SELECT ii.*, d.name AS item_name, d.unit_of_measure
           FROM invoice_items ii
           JOIN item_dictionary d ON ii.item_id = d.id
           WHERE ii.invoice_id=?
           ORDER BY ii.id""",
        (inv_id,)
    ).fetchall()

    body = request.get_json(silent=True) or {}
    tpl_id = body.get("tpl_id") or request.args.get("tpl_id", type=int)

    # HTML: з тіла запиту → з індивідуального body_html → з шаблону
    html_tpl = body.get("html")
    tpl = None
    if not html_tpl:
        if inv["body_html"]:
            html_tpl = inv["body_html"]
            _, tpl = get_template_html(conn, "invoice", tpl_id)
        else:
            html_tpl, tpl = get_template_html(conn, "invoice", tpl_id)

    if not html_tpl:
        conn.close()
        return jsonify({"ok": False, "msg": "Шаблон не налаштовано", "code": "no_template"}), 404

    data = {
        "html":    html_tpl,
        "invoice": dict(inv),
        "items":   [dict(i) for i in items],
    }
    rendered_body = render_doc("invoice", data, conn)
    conn.close()
    return jsonify({"html": rendered_body})


@bp.route("/<int:inv_id>/save-body", methods=["POST"])
@login_required
def save_body(inv_id):
    """
    POST JSON: {html: string}
    Зберігає індивідуальний HTML тіла накладної (відредагований через вбудований редактор).
    Цей HTML буде використовуватись замість шаблону при друці цієї конкретної накладної.
    """
    conn = get_connection()
    inv = conn.execute("SELECT id, status FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv:
        conn.close()
        return jsonify({"ok": False, "msg": "Накладну не знайдено"}), 404
    if inv["status"] == "processed":
        conn.close()
        return jsonify({"ok": False, "msg": "Проведену накладну не можна редагувати"}), 400

    body = request.get_json(silent=True) or {}
    html = body.get("html", "").strip()

    conn.execute(
        "UPDATE invoices SET body_html=?, updated_at=datetime('now','localtime') WHERE id=?",
        (html or None, inv_id)
    )
    conn.commit()
    conn.close()
    log_action("edit", "invoices", inv_id, {}, {"body_html": "updated"})
    return jsonify({"ok": True})


@bp.route("/<int:inv_id>/reset-body", methods=["POST"])
@login_required
def reset_body(inv_id):
    """Скинути індивідуальний HTML — повернутись до шаблону."""
    conn = get_connection()
    conn.execute(
        "UPDATE invoices SET body_html=NULL, updated_at=datetime('now','localtime') WHERE id=?",
        (inv_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:inv_id>/get-body", methods=["GET"])
@login_required
def get_body(inv_id):
    """Повернути збережений body_html або HTML шаблону (сирий, без рендеру)."""
    from core.renderer import get_template_html
    conn = get_connection()
    inv = conn.execute("SELECT body_html FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv:
        conn.close()
        return jsonify({"ok": False, "msg": "not found"}), 404
    html = inv["body_html"]
    if not html:
        html, _ = get_template_html(conn, "invoice", None)
    conn.close()
    return jsonify({"html": html or ""})


# ─────────────────────────────────────────────────────────────
#  JSON API — пошук о/с
# ─────────────────────────────────────────────────────────────

@bp.route("/api/personnel_search")
@login_required
def api_personnel_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    conn = get_connection()
    rows = conn.execute("""
        SELECT p.id,
               p.rank || ' ' || p.last_name || ' ' || p.first_name || ' ' ||
               COALESCE(p.middle_name,'') AS label,
               u.name AS unit_name
        FROM personnel p
        LEFT JOIN units u ON p.unit_id = u.id
        WHERE p.is_active=1
          AND (p.last_name LIKE ? OR p.first_name LIKE ? OR p.middle_name LIKE ?)
        ORDER BY p.last_name LIMIT 20
    """, (f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ─────────────────────────────────────────────────────────────
#  MW partial — для багатозадачного вікна
# ─────────────────────────────────────────────────────────────

@bp.route("/mw/")
@login_required
def mw_index():
    """Partial: список накладних для MW-вікна."""
    conn = get_connection()
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")

    where, params = ["1=1"], []
    if status_filter:
        where.append("i.status = ?")
        params.append(status_filter)
    if search:
        where.append("(i.number LIKE ? OR p.last_name LIKE ? OR u.name LIKE ?)")
        like = f"%{search}%"
        params += [like, like, like]

    rows = conn.execute(f"""
        SELECT i.id, i.number, i.status, i.created_at, i.issued_date,
               p.last_name || ' ' || p.first_name AS recipient_name,
               u.name AS recipient_unit_name
        FROM invoices i
        LEFT JOIN personnel p ON i.recipient_personnel_id = p.id
        LEFT JOIN units u ON i.recipient_unit_id = u.id
        WHERE {' AND '.join(where)}
        ORDER BY i.created_at DESC
        LIMIT 100
    """, params).fetchall()
    conn.close()
    return render_template("invoices/mw_index.html",
                           rows=rows, search=search, status_filter=status_filter)


@bp.route("/mw/<int:inv_id>")
@login_required
def mw_view(inv_id):
    """MW-перегляд накладної."""
    conn = get_connection()
    inv = conn.execute("""
        SELECT i.*,
               p.last_name || ' ' || p.first_name || ' ' ||
               COALESCE(p.middle_name,'') AS recipient_person_name,
               p.rank  AS recipient_rank,
               u.name  AS recipient_unit_name
        FROM invoices i
        LEFT JOIN personnel p ON i.recipient_personnel_id = p.id
        LEFT JOIN units     u ON i.recipient_unit_id      = u.id
        WHERE i.id = ?
    """, (inv_id,)).fetchone()
    if not inv:
        conn.close()
        return "Накладну не знайдено", 404

    items = conn.execute("""
        SELECT ii.*, d.name AS item_name, d.unit_of_measure
        FROM invoice_items ii
        JOIN item_dictionary d ON ii.item_id = d.id
        WHERE ii.invoice_id = ?
        ORDER BY d.name
    """, (inv_id,)).fetchall()

    conn.close()
    return render_template("invoices/mw_view.html", inv=inv, items=items)
