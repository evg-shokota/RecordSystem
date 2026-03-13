"""
modules/rv/routes.py — Роздавальні відомості (Додаток 4)

Матрична структура:
  distribution_sheets       — заголовок РВ
  distribution_sheet_rows   — рядки (о/с)
  distribution_sheet_items  — стовпці (позиції майна)
  distribution_sheet_quantities — комірки (кількість для кожного о/с × майно)

Статуси:
  draft     — чернетка
  active    — активна (видача ще не завершена)
  closed    — закрита (всі підписали)
  cancelled — скасована
Author: White
"""
import json
import time
from datetime import datetime, date
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, jsonify, flash
)
from core.auth import login_required
from core.db import get_connection
from core.audit import log_action
from core.settings import get_all_settings, get_setting

bp = Blueprint("rv", __name__, url_prefix="/rv")

STATUS_LABELS = {
    "draft":     "Чернетка",
    "active":    "Активна",
    "closed":    "Закрита",
    "cancelled": "Скасована",
}

STATUS_COLORS = {
    "draft":     "warning",
    "active":    "primary",
    "closed":    "success",
    "cancelled": "danger",
}


# ─────────────────────────────────────────────────────────────
#  Допоміжні
# ─────────────────────────────────────────────────────────────

def _get_next_number(conn) -> tuple[str, int]:
    """
    Формує наступний номер РВ. Повертає (number_str, sequence_num).
    Зберігає лічильник в doc_sequences (спільна таблиця, PRIMARY KEY (doc_type, year)).
    """
    year   = date.today().year
    suffix = get_setting("rv_suffix", "РВ")

    row = conn.execute(
        "SELECT sequence, suffix FROM doc_sequences WHERE doc_type='rv' AND year=?",
        (year,)
    ).fetchone()

    if row:
        seq    = row["sequence"]
        suffix = row["suffix"] or suffix
        conn.execute(
            "UPDATE doc_sequences SET sequence=?, updated_at=datetime('now','localtime') "
            "WHERE doc_type='rv' AND year=?",
            (seq + 1, year)
        )
    else:
        seq = 1
        conn.execute(
            "INSERT INTO doc_sequences (doc_type, year, sequence, suffix) VALUES ('rv',?,2,?)",
            (year, suffix)
        )
    conn.commit()

    number = f"{year}/{seq}/{suffix}"
    return number, seq


def _build_matrix(conn, sheet_id: int) -> dict:
    """
    Повертає матрицю РВ:
    {
      'items': [{id, item_id, item_name, uom, price, category, sort_order}, ...],
      'rows':  [{id, personnel_id, last_name, first_name, rank, ...}, ...],
      'qty':   {(row_id, item_id): {quantity, serial_numbers}},
    }
    """
    items = conn.execute(
        """SELECT dsi.id, dsi.item_id, d.name as item_name, d.unit_of_measure,
                  dsi.price, dsi.category, dsi.sort_order
           FROM distribution_sheet_items dsi
           JOIN item_dictionary d ON dsi.item_id = d.id
           WHERE dsi.sheet_id=?
           ORDER BY dsi.sort_order, dsi.id""",
        (sheet_id,)
    ).fetchall()

    rows = conn.execute(
        """SELECT dsr.id, dsr.personnel_id, dsr.sort_order,
                  dsr.received, dsr.received_date, dsr.signature_done,
                  p.last_name, p.first_name, p.middle_name, p.rank, p.position
           FROM distribution_sheet_rows dsr
           JOIN personnel p ON dsr.personnel_id = p.id
           WHERE dsr.sheet_id=?
           ORDER BY dsr.sort_order, p.last_name""",
        (sheet_id,)
    ).fetchall()

    qty_rows = conn.execute(
        "SELECT row_id, item_id, quantity, serial_numbers FROM distribution_sheet_quantities WHERE sheet_id=?",
        (sheet_id,)
    ).fetchall()
    qty = {(q["row_id"], q["item_id"]): dict(q) for q in qty_rows}

    return {
        "cols": [dict(r) for r in items],
        "rows": [dict(r) for r in rows],
        "qty":  qty,
    }


# ─────────────────────────────────────────────────────────────
#  Список
# ─────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    status_filter = request.args.get("status", "")
    search        = request.args.get("q", "").strip()

    conn = get_connection()
    where, params = [], []
    if status_filter:
        where.append("ds.status=?"); params.append(status_filter)
    if search:
        like = f"%{search}%"
        where.append("""(ds.number LIKE ? OR u.name LIKE ? OR
                         EXISTS (SELECT 1 FROM distribution_sheet_items dsi
                                 JOIN item_dictionary d ON dsi.item_id=d.id
                                 WHERE dsi.sheet_id=ds.id AND d.name LIKE ?) OR
                         EXISTS (SELECT 1 FROM distribution_sheet_rows dsr
                                 JOIN personnel p ON dsr.personnel_id=p.id
                                 WHERE dsr.sheet_id=ds.id AND p.last_name LIKE ?))""")
        params += [like, like, like, like]

    sql = """
        SELECT ds.id, ds.number, ds.doc_date, ds.status,
               ds.total_sum, ds.created_at,
               COALESCE(u.name, ds.unit_text) as unit_name
        FROM distribution_sheets ds
        LEFT JOIN units u ON ds.unit_id = u.id
        {where}
        ORDER BY ds.created_at DESC
    """.format(where="WHERE " + " AND ".join(where) if where else "")

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template(
        "rv/index.html",
        rows=[dict(r) for r in rows],
        status_filter=status_filter,
        search=search,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        today_str=date.today().isoformat(),
    )


# ─────────────────────────────────────────────────────────────
#  Нова РВ
# ─────────────────────────────────────────────────────────────

@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    conn = get_connection()
    is_external = request.args.get("is_external", "0") == "1" or request.form.get("is_external") == "1"

    # ── Зовнішня (вже надрукована) РВ ──────────────────────────
    if is_external:
        s = get_all_settings()
        units = conn.execute(
            "SELECT u.id, u.name, b.name as bat_name FROM units u JOIN battalions b ON u.battalion_id=b.id ORDER BY b.name, u.name"
        ).fetchall()
        all_personnel = conn.execute(
            """SELECT p.id, p.last_name, p.first_name, p.middle_name, p.rank,
                      u.name as unit_name
               FROM personnel p
               LEFT JOIN units u ON p.unit_id = u.id
               WHERE p.is_active=1
               ORDER BY p.last_name, p.first_name"""
        ).fetchall()
        all_items = conn.execute(
            "SELECT id, name, unit_of_measure FROM item_dictionary ORDER BY name"
        ).fetchall()

        if request.method == "POST":
            import os
            from werkzeug.utils import secure_filename as _secure
            errors = []
            external_number = request.form.get("external_number", "").strip()
            doc_date        = request.form.get("doc_date") or date.today().isoformat()
            unit_id         = request.form.get("unit_id", type=int) or None
            unit_text       = request.form.get("unit_text", "").strip()
            notes_val       = request.form.get("notes", "").strip()

            if not external_number:
                errors.append("Вкажіть номер документа")

            # Позиції
            item_ids  = request.form.getlist("item_id[]")
            qtys      = request.form.getlist("planned_qty[]")
            prices    = request.form.getlist("price[]")
            cats      = request.form.getlist("category[]")
            rows_items = []
            for item_id, qty_s, price_s, cat in zip(item_ids, qtys, prices, cats):
                if not item_id:
                    continue
                try:
                    qty = float(qty_s)
                    price = float(price_s) if price_s else 0.0
                except ValueError:
                    errors.append("Невірна кількість або ціна")
                    continue
                if qty <= 0:
                    continue
                rows_items.append({
                    "item_id": int(item_id), "category": cat or "I",
                    "price": price, "qty": qty,
                })

            if not rows_items and not errors:
                errors.append("Додайте хоча б одну позицію")

            # Особовий склад (множинний вибір)
            personnel_ids = request.form.getlist("personnel_ids[]")

            if not errors:
                number, seq = _get_next_number(conn)
                year   = date.today().year
                suffix = number.rsplit("/", 1)[-1] if "/" in number else ""

                service_name  = s.get("service_name", "")
                supplier_name = s.get("company_name", "")
                chief_rank    = s.get("chief_rank", "")
                chief_name    = s.get("chief_name", "")
                chief_is_tvo  = 1 if s.get("chief_is_tvo") == "1" else 0
                given_rank    = s.get("warehouse_chief_rank", "")
                given_name    = s.get("warehouse_chief_name", "")
                clerk_rank    = s.get("clerk_rank", "")
                clerk_name    = s.get("clerk_name", "")

                total_sum = sum(r["qty"] * r["price"] for r in rows_items)

                conn.execute("""
                    INSERT INTO distribution_sheets
                      (number, year, sequence_num, suffix, unit_id, unit_text,
                       service_name, supplier_name,
                       doc_date, given_by_rank, given_by_name,
                       received_by_rank, received_by_name,
                       chief_rank, chief_name, chief_is_tvo,
                       clerk_rank, clerk_name,
                       base_document, notes, status,
                       is_external, external_number, total_sum)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    number, year, seq, suffix,
                    unit_id, unit_text,
                    service_name, supplier_name,
                    doc_date, given_rank, given_name,
                    "", "",
                    chief_rank, chief_name, chief_is_tvo,
                    clerk_rank, clerk_name,
                    "зовнішня відомість", notes_val, "active",
                    1, external_number, total_sum,
                ))
                conn.commit()
                sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                # Додати позиції майна
                for sort_order, row in enumerate(rows_items):
                    conn.execute("""
                        INSERT INTO distribution_sheet_items
                            (sheet_id, item_id, price, category, sort_order)
                        VALUES (?,?,?,?,?)
                    """, (sid, row["item_id"], row["price"], row["category"], sort_order))

                conn.commit()

                # Додати рядки о/с (якщо обрані)
                for sort_order, pid in enumerate(personnel_ids):
                    if pid:
                        conn.execute("""
                            INSERT INTO distribution_sheet_rows
                                (sheet_id, personnel_id, sort_order)
                            VALUES (?,?,?)
                        """, (sid, int(pid), sort_order))

                conn.commit()

                # Зберегти скан
                scan_file = request.files.get("scan")
                if scan_file and scan_file.filename:
                    ext = os.path.splitext(scan_file.filename)[1].lower()
                    if ext in (".pdf", ".jpg", ".jpeg", ".png"):
                        from core.db import get_db_path
                        storage_dir = os.path.join(os.path.dirname(get_db_path()), "storage", "scans", "rv")
                        os.makedirs(storage_dir, exist_ok=True)
                        filename = f"rv_{sid}{ext}"
                        scan_file.save(os.path.join(storage_dir, filename))
                        rel_path = f"scans/rv/{filename}"
                        orig_name = _secure(scan_file.filename)
                        conn.execute(
                            "UPDATE distribution_sheets SET scan_path=?, scan_original_name=? WHERE id=?",
                            (rel_path, orig_name, sid)
                        )
                        conn.commit()

                conn.close()
                return redirect(url_for("rv.view", sid=sid))

            conn.close()
            return render_template(
                "rv/new_external.html",
                s=s, units=[dict(r) for r in units],
                all_personnel=[dict(r) for r in all_personnel],
                all_items=[dict(r) for r in all_items],
                errors=errors, form=request.form,
                today=date.today().isoformat(),
            )

        conn.close()
        return render_template(
            "rv/new_external.html",
            s=s, units=[dict(r) for r in units],
            all_personnel=[dict(r) for r in all_personnel],
            all_items=[dict(r) for r in all_items],
            errors=[], form={},
            today=date.today().isoformat(),
        )

    # ── Звичайна РВ ────────────────────────────────────────────
    if request.method == "POST":
        s          = get_all_settings()
        unit_id    = request.form.get("unit_id", type=int) or None
        unit_text  = request.form.get("unit_text", "").strip()
        doc_date   = request.form.get("doc_date") or date.today().isoformat()
        base_doc   = request.form.get("base_document", "").strip() or "планова видача"
        notes      = request.form.get("notes", "").strip()

        # Підписанти з форми (або з налаштувань якщо форма пуста)
        given_by_rank  = request.form.get("given_by_rank", "").strip() or s.get("warehouse_chief_rank", "")
        given_by_name  = request.form.get("given_by_name", "").strip() or s.get("warehouse_chief_name", "")
        received_by_rank = request.form.get("received_by_rank", "").strip()
        received_by_name = request.form.get("received_by_name", "").strip()
        chief_rank     = request.form.get("chief_rank", "").strip() or s.get("chief_rank", "")
        chief_name     = request.form.get("chief_name", "").strip() or s.get("chief_name", "")
        chief_is_tvo   = 1 if request.form.get("chief_is_tvo") else 0
        clerk_rank     = request.form.get("clerk_rank", "").strip() or s.get("clerk_rank", "")
        clerk_name     = request.form.get("clerk_name", "").strip() or s.get("clerk_name", "")
        service_name   = s.get("service_name", "")
        supplier_name  = s.get("company_name", "")

        conn.execute("""
            INSERT INTO distribution_sheets
              (number, year, sequence_num, suffix, unit_id, unit_text, service_name, supplier_name,
               doc_date, given_by_rank, given_by_name, received_by_rank, received_by_name,
               chief_rank, chief_name, chief_is_tvo, clerk_rank, clerk_name,
               base_document, notes, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'draft')
        """, (
            f"ЧЕРНЕТКА-{int(time.time())}", 0, 0, "",
            unit_id, unit_text, service_name, supplier_name,
            doc_date, given_by_rank, given_by_name,
            received_by_rank, received_by_name,
            chief_rank, chief_name, chief_is_tvo,
            clerk_rank, clerk_name, base_doc, notes,
        ))
        conn.commit()
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        back = request.form.get("back") or request.args.get("back", "")
        return redirect(url_for("rv.edit_matrix", sid=sid, back=back) if back else url_for("rv.edit_matrix", sid=sid))

    # GET — форма
    s = get_all_settings()
    units = conn.execute(
        "SELECT u.id, u.name, b.name as bat_name FROM units u JOIN battalions b ON u.battalion_id=b.id ORDER BY b.name, u.name"
    ).fetchall()
    conn.close()
    return render_template("rv/new.html", s=s, units=[dict(r) for r in units],
                           today=date.today().isoformat())


# ─────────────────────────────────────────────────────────────
#  Редагування матриці
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/matrix")
@login_required
def edit_matrix(sid):
    conn = get_connection()
    sheet = conn.execute("SELECT * FROM distribution_sheets WHERE id=?", (sid,)).fetchone()
    if not sheet:
        conn.close()
        flash("РВ не знайдено", "danger")
        return redirect(url_for("rv.index"))

    matrix = _build_matrix(conn, sid)

    # Всі підрозділи для autocomplete
    units = conn.execute(
        "SELECT u.id, u.name, b.name as bat_name FROM units u JOIN battalions b ON u.battalion_id=b.id ORDER BY b.name, u.name"
    ).fetchall()

    # Весь активний о/с для додавання рядків
    all_personnel = conn.execute(
        """SELECT p.id, p.last_name, p.first_name, p.middle_name, p.rank, p.position,
                  u.name as unit_name
           FROM personnel p
           LEFT JOIN units u ON p.unit_id = u.id
           WHERE p.is_active=1
           ORDER BY p.last_name, p.first_name"""
    ).fetchall()

    # Вже доданий о/с
    added_pids = {r["personnel_id"] for r in matrix["rows"]}

    # Всі позиції словника для додавання стовпців
    all_items = conn.execute(
        "SELECT id, name, unit_of_measure FROM item_dictionary ORDER BY name"
    ).fetchall()
    added_iids = {r["item_id"] for r in matrix["cols"]}

    # Залишки складу (для StockItemRow в модалі)
    from core.warehouse import get_stock
    stock = get_stock(conn)

    conn.close()
    return render_template(
        "rv/matrix.html",
        sheet=dict(sheet),
        matrix=matrix,
        all_personnel=[dict(r) for r in all_personnel],
        added_pids=added_pids,
        all_items=[dict(r) for r in all_items],
        added_iids=added_iids,
        units=[dict(r) for r in units],
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        stock=stock,
    )


# ─────────────────────────────────────────────────────────────
#  Перегляд (тільки читання)
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/view")
@login_required
def view(sid):
    conn = get_connection()
    sheet = conn.execute(
        """SELECT ds.*, u.name as unit_name
           FROM distribution_sheets ds
           LEFT JOIN units u ON ds.unit_id = u.id
           WHERE ds.id=?""",
        (sid,)
    ).fetchone()
    if not sheet:
        conn.close()
        flash("РВ не знайдено", "danger")
        return redirect(url_for("rv.index"))

    matrix = _build_matrix(conn, sid)
    conn.close()
    return render_template(
        "rv/view.html",
        sheet=dict(sheet),
        matrix=matrix,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
    )


# ─────────────────────────────────────────────────────────────
#  Друк
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/print")
@login_required
def print_rv(sid):
    conn = get_connection()
    sheet = conn.execute(
        """SELECT ds.*, u.name as unit_name
           FROM distribution_sheets ds
           LEFT JOIN units u ON ds.unit_id = u.id
           WHERE ds.id=?""",
        (sid,)
    ).fetchone()
    if not sheet:
        conn.close()
        flash("РВ не знайдено", "danger")
        return redirect(url_for("rv.index"))
    matrix = _build_matrix(conn, sid)
    conn.close()
    return render_template("rv/print.html", sheet=dict(sheet), matrix=matrix)


# ─────────────────────────────────────────────────────────────
#  Присвоїти номер
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/assign-number", methods=["POST"])
@login_required
def assign_number(sid):
    conn = get_connection()
    sheet = conn.execute("SELECT * FROM distribution_sheets WHERE id=?", (sid,)).fetchone()
    if not sheet or sheet["status"] != "draft":
        conn.close()
        flash("Неможливо присвоїти номер", "danger")
        return redirect(url_for("rv.view", sid=sid))

    number, seq = _get_next_number(conn)  # вже робить commit лічильника
    year   = date.today().year
    suffix = get_setting("rv_suffix", "РВ")

    conn.execute(
        "UPDATE distribution_sheets SET number=?, year=?, sequence_num=?, suffix=?, status='active' WHERE id=?",
        (number, year, seq, suffix, sid)
    )
    conn.commit()
    log_action("edit", "distribution_sheets", sid, {"status": "draft"}, {"status": "active", "number": number})
    conn.close()
    flash(f"Номер {number} присвоєно", "success")
    return redirect(url_for("rv.view", sid=sid))


# ─────────────────────────────────────────────────────────────
#  Закрити / Скасувати / Видалити
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/close", methods=["POST"])
@login_required
def close(sid):
    """
    Закрити РВ:
    - Записує майно на картки о/с (personnel_items) для тих хто отримав (received=1)
    - Статус → closed
    """
    conn = get_connection()
    sheet = conn.execute("SELECT * FROM distribution_sheets WHERE id=?", (sid,)).fetchone()
    if not sheet or sheet["status"] != "active":
        conn.close()
        flash("Можна закрити тільки активну РВ", "danger")
        return redirect(url_for("rv.view", sid=sid))

    matrix = _build_matrix(conn, sid)
    today = date.today().isoformat()

    try:
        for row in matrix["rows"]:
            # Записуємо тільки тих хто отримав (received=1)
            if not row.get("received"):
                continue
            pid = row["personnel_id"]
            row_id = row["id"]
            for col in matrix["cols"]:
                item_id = col["item_id"]
                cell = matrix["qty"].get((row_id, item_id), {})
                qty = cell.get("quantity") or 0
                if qty <= 0:
                    continue
                # Перевіряємо чи вже записано (уникаємо дублів при повторному закритті)
                exists = conn.execute(
                    """SELECT id FROM personnel_items
                       WHERE personnel_id=? AND item_id=? AND sheet_id=? AND source_type='rv'""",
                    (pid, item_id, sid)
                ).fetchone()
                if exists:
                    continue
                conn.execute("""
                    INSERT INTO personnel_items
                        (personnel_id, item_id, quantity, price, category,
                         sheet_id, source_type, issue_date,
                         wear_started_date, status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,'rv',?,?,
                            'active', datetime('now','localtime'), datetime('now','localtime'))
                """, (
                    pid, item_id,
                    cell.get("actual_qty") or qty,
                    col["price"], col["category"],
                    sid, today, today,
                ))

        conn.execute(
            "UPDATE distribution_sheets SET status='closed', updated_at=datetime('now','localtime') WHERE id=?",
            (sid,)
        )
        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        flash(f"Помилка при закритті РВ: {e}", "danger")
        return redirect(url_for("rv.view", sid=sid))

    log_action("edit", "distribution_sheets", sid, {"status": "active"}, {"status": "closed"})
    conn.close()
    flash("РВ закрито. Майно записано на картки о/с.", "success")
    return redirect(url_for("rv.view", sid=sid))


@bp.route("/<int:sid>/cancel", methods=["POST"])
@login_required
def cancel(sid):
    conn = get_connection()
    conn.execute(
        "UPDATE distribution_sheets SET status='cancelled', updated_at=datetime('now','localtime') WHERE id=?",
        (sid,)
    )
    conn.commit()
    log_action("edit", "distribution_sheets", sid, {}, {"status": "cancelled"})
    conn.close()
    flash("РВ скасовано", "warning")
    return redirect(url_for("rv.view", sid=sid))


@bp.route("/<int:sid>/delete", methods=["POST"])
@login_required
def delete(sid):
    conn = get_connection()
    sheet = conn.execute("SELECT status FROM distribution_sheets WHERE id=?", (sid,)).fetchone()
    if sheet and sheet["status"] not in ("draft", "cancelled"):
        conn.close()
        flash("Можна видаляти тільки чернетки та скасовані РВ", "danger")
        return redirect(url_for("rv.view", sid=sid))
    conn.execute("DELETE FROM distribution_sheet_quantities WHERE sheet_id=?", (sid,))
    conn.execute("DELETE FROM distribution_sheet_items WHERE sheet_id=?", (sid,))
    conn.execute("DELETE FROM distribution_sheet_rows WHERE sheet_id=?", (sid,))
    conn.execute("DELETE FROM distribution_sheets WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    flash("РВ видалено", "success")
    return redirect(url_for("rv.index"))


# ─────────────────────────────────────────────────────────────
#  AJAX — Управління рядками (о/с)
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/row/add", methods=["POST"])
@login_required
def row_add(sid):
    pids = request.form.getlist("personnel_id[]", type=int)
    if not pids:
        pid = request.form.get("personnel_id", type=int)
        if pid:
            pids = [pid]
    if not pids:
        return jsonify({"error": "Оберіть військовослужбовця"}), 400

    conn = get_connection()
    sheet = conn.execute("SELECT status FROM distribution_sheets WHERE id=?", (sid,)).fetchone()
    if not sheet or sheet["status"] not in ("draft", "active"):
        conn.close()
        return jsonify({"error": "РВ недоступна для редагування"}), 400

    added = []
    for pid in pids:
        dup = conn.execute(
            "SELECT id FROM distribution_sheet_rows WHERE sheet_id=? AND personnel_id=?",
            (sid, pid)
        ).fetchone()
        if dup:
            continue
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0) FROM distribution_sheet_rows WHERE sheet_id=?",
            (sid,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO distribution_sheet_rows (sheet_id, personnel_id, sort_order) VALUES (?,?,?)",
            (sid, pid, max_order + 1)
        )
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        p = conn.execute(
            "SELECT id, last_name, first_name, middle_name, rank, position FROM personnel WHERE id=?",
            (pid,)
        ).fetchone()
        # Ініціалізуємо кількості для всіх наявних стовпців = 0
        items = conn.execute(
            "SELECT id, item_id FROM distribution_sheet_items WHERE sheet_id=?", (sid,)
        ).fetchall()
        for it in items:
            exists = conn.execute(
                "SELECT id FROM distribution_sheet_quantities WHERE sheet_id=? AND row_id=? AND item_id=?",
                (sid, row_id, it["item_id"])
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO distribution_sheet_quantities (sheet_id, row_id, item_id, quantity) VALUES (?,?,?,0)",
                    (sid, row_id, it["item_id"])
                )
        conn.commit()
        added.append({"row_id": row_id, "personnel": dict(p)})

    conn.close()
    return jsonify({"ok": True, "added": added})


@bp.route("/<int:sid>/row/<int:rid>/delete", methods=["POST"])
@login_required
def row_delete(sid, rid):
    conn = get_connection()
    conn.execute("DELETE FROM distribution_sheet_quantities WHERE sheet_id=? AND row_id=?", (sid, rid))
    conn.execute("DELETE FROM distribution_sheet_rows WHERE id=? AND sheet_id=?", (rid, sid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:sid>/row/<int:rid>/toggle", methods=["POST"])
@login_required
def row_toggle(sid, rid):
    """Позначити/зняти 'отримав'."""
    conn = get_connection()
    r = conn.execute("SELECT received FROM distribution_sheet_rows WHERE id=?", (rid,)).fetchone()
    if not r:
        conn.close()
        return jsonify({"error": "not found"}), 404
    new_val = 0 if r["received"] else 1
    rec_date = date.today().isoformat() if new_val else None
    conn.execute(
        "UPDATE distribution_sheet_rows SET received=?, received_date=? WHERE id=?",
        (new_val, rec_date, rid)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "received": new_val, "received_date": rec_date})


# ─────────────────────────────────────────────────────────────
#  AJAX — Управління стовпцями (майно)
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/item/add", methods=["POST"])
@login_required
def item_add(sid):
    item_id  = request.form.get("item_id", type=int)
    price    = request.form.get("price", 0.0, type=float)
    category = request.form.get("category", "I")

    if not item_id:
        return jsonify({"error": "Оберіть позицію майна"}), 400

    conn = get_connection()
    sheet = conn.execute("SELECT status FROM distribution_sheets WHERE id=?", (sid,)).fetchone()
    if not sheet or sheet["status"] not in ("draft", "active"):
        conn.close()
        return jsonify({"error": "РВ недоступна для редагування"}), 400

    dup = conn.execute(
        "SELECT id FROM distribution_sheet_items WHERE sheet_id=? AND item_id=?",
        (sid, item_id)
    ).fetchone()
    if dup:
        conn.close()
        return jsonify({"error": "Ця позиція вже є в РВ"}), 400

    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order),0) FROM distribution_sheet_items WHERE sheet_id=?",
        (sid,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO distribution_sheet_items (sheet_id, item_id, price, category, sort_order) VALUES (?,?,?,?,?)",
        (sid, item_id, price, category, max_order + 1)
    )
    conn.commit()
    col_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Ініціалізуємо кількості для всіх наявних рядків = 0
    rows = conn.execute(
        "SELECT id FROM distribution_sheet_rows WHERE sheet_id=?", (sid,)
    ).fetchall()
    for row in rows:
        exists = conn.execute(
            "SELECT id FROM distribution_sheet_quantities WHERE sheet_id=? AND row_id=? AND item_id=?",
            (sid, row["id"], item_id)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO distribution_sheet_quantities (sheet_id, row_id, item_id, quantity) VALUES (?,?,?,0)",
                (sid, row["id"], item_id)
            )
    conn.commit()

    item = conn.execute(
        "SELECT name, unit_of_measure FROM item_dictionary WHERE id=?", (item_id,)
    ).fetchone()
    conn.close()
    return jsonify({
        "ok": True, "col_id": col_id,
        "item_id": item_id,
        "item_name": item["name"],
        "uom": item["unit_of_measure"],
        "price": price,
        "category": category,
    })


@bp.route("/<int:sid>/item/<int:col_id>/delete", methods=["POST"])
@login_required
def item_delete(sid, col_id):
    conn = get_connection()
    col = conn.execute("SELECT item_id FROM distribution_sheet_items WHERE id=? AND sheet_id=?", (col_id, sid)).fetchone()
    if not col:
        conn.close()
        return jsonify({"error": "not found"}), 404
    conn.execute("DELETE FROM distribution_sheet_quantities WHERE sheet_id=? AND item_id=?", (sid, col["item_id"]))
    conn.execute("DELETE FROM distribution_sheet_items WHERE id=?", (col_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  AJAX — Збереження кількостей
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/qty/save", methods=["POST"])
@login_required
def qty_save(sid):
    """
    Приймає JSON: [{row_id, item_id, quantity, serial_numbers}, ...]
    або одну комірку: {row_id, item_id, quantity}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no data"}), 400
    if isinstance(data, dict):
        data = [data]

    conn = get_connection()
    sheet = conn.execute("SELECT status FROM distribution_sheets WHERE id=?", (sid,)).fetchone()
    if not sheet or sheet["status"] not in ("draft", "active"):
        conn.close()
        return jsonify({"error": "РВ недоступна для редагування"}), 400

    for cell in data:
        row_id  = cell.get("row_id")
        item_id = cell.get("item_id")
        qty     = float(cell.get("quantity", 0) or 0)
        serials = cell.get("serial_numbers", "")
        existing = conn.execute(
            "SELECT id FROM distribution_sheet_quantities WHERE sheet_id=? AND row_id=? AND item_id=?",
            (sid, row_id, item_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE distribution_sheet_quantities SET quantity=?, serial_numbers=? WHERE id=?",
                (qty, serials, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO distribution_sheet_quantities (sheet_id, row_id, item_id, quantity, serial_numbers) VALUES (?,?,?,?,?)",
                (sid, row_id, item_id, qty, serials)
            )

    # Перерахувати суму
    total = conn.execute("""
        SELECT COALESCE(SUM(q.quantity * i.price), 0)
        FROM distribution_sheet_quantities q
        JOIN distribution_sheet_items i ON q.sheet_id=i.sheet_id AND q.item_id=i.item_id
        WHERE q.sheet_id=?
    """, (sid,)).fetchone()[0]

    conn.execute(
        "UPDATE distribution_sheets SET total_sum=?, updated_at=datetime('now','localtime') WHERE id=?",
        (total, sid)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "total": round(total, 2)})


# ─────────────────────────────────────────────────────────────
#  AJAX — Редагування заголовка
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/header/save", methods=["POST"])
@login_required
def header_save(sid):
    conn = get_connection()
    unit_id = request.form.get("unit_id") or None
    if unit_id:
        unit_id = int(unit_id)
    unit_text = request.form.get("unit_text", "").strip()
    base_doc = request.form.get("base_document", "").strip() or "планова видача"
    conn.execute("""
        UPDATE distribution_sheets SET
            unit_id=?, unit_text=?, doc_date=?, base_document=?, notes=?,
            given_by_rank=?, given_by_name=?,
            received_by_rank=?, received_by_name=?,
            chief_rank=?, chief_name=?, chief_is_tvo=?,
            clerk_rank=?, clerk_name=?,
            updated_at=datetime('now','localtime')
        WHERE id=?
    """, (
        unit_id,
        unit_text,
        request.form.get("doc_date") or date.today().isoformat(),
        base_doc,
        request.form.get("notes", "").strip(),
        request.form.get("given_by_rank", "").strip(),
        request.form.get("given_by_name", "").strip(),
        request.form.get("received_by_rank", "").strip(),
        request.form.get("received_by_name", "").strip(),
        request.form.get("chief_rank", "").strip(),
        request.form.get("chief_name", "").strip(),
        1 if request.form.get("chief_is_tvo") else 0,
        request.form.get("clerk_rank", "").strip(),
        request.form.get("clerk_name", "").strip(),
        sid,
    ))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  РЕНДЕР / ДРУК через шаблон документа
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:sid>/render")
@login_required
def render_rv(sid):
    """
    Рендер РВ через шаблон документа.
    ?tpl_id=<id> — конкретний шаблон; якщо не вказано — дефолтний для 'rv'.
    Fallback: старий rv/print.html якщо шаблон не налаштовано.
    """
    from core.renderer import render_doc, get_template_html

    conn = get_connection()
    sheet = conn.execute(
        """SELECT ds.*, u.name as unit_name
           FROM distribution_sheets ds
           LEFT JOIN units u ON ds.unit_id = u.id
           WHERE ds.id=?""",
        (sid,)
    ).fetchone()
    if not sheet:
        conn.close()
        flash("РВ не знайдено", "danger")
        return redirect(url_for("rv.index"))

    matrix = _build_matrix(conn, sid)

    tpl_id = request.args.get("tpl_id", type=int)
    html_tpl, tpl = get_template_html(conn, "rv", tpl_id)

    if not html_tpl:
        # Fallback на старий print.html (без шаблону)
        conn.close()
        return render_template("rv/print.html", sheet=dict(sheet), matrix=matrix)

    # Нормалізуємо qty: (row_id, item_id) → кількість (float) для renderer
    qty_flat = {k: v.get("quantity", 0) for k, v in matrix["qty"].items()}
    data = {
        "html":   html_tpl,
        "sheet":  dict(sheet),
        "items":  matrix["cols"],
        "rows":   matrix["rows"],
        "qty":    qty_flat,
    }
    rendered_body = render_doc("rv", data, conn)
    conn.close()

    return render_template(
        "doc_templates/print_render.html",
        rendered_body=rendered_body,
        tpl=dict(tpl) if tpl else {},
        doc_title=f"РВ {sheet['number'] or 'б/н'}",
    )
