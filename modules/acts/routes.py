"""
modules/acts/routes.py — Акти списання та введення в експлуатацію

/acts/write-off/  — акти списання (write_offs + write_off_items)
/acts/exploit/    — акти введення в експлуатацію (exploitation_acts)

Author: White
"""
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

from core.auth import login_required
from core.db import get_connection
from core.settings import get_setting
from core.audit import log_action

bp = Blueprint("acts", __name__, url_prefix="/acts")

STATUS_LABELS = {
    "draft":     "Чернетка",
    "created":   "Оформлено",
    "cancelled": "Скасовано",
}
STATUS_COLORS = {
    "draft":     "secondary",
    "created":   "success",
    "cancelled": "danger",
}


# ─────────────────────────────────────────────────────────────
#  Нумерація
# ─────────────────────────────────────────────────────────────

def _next_number(conn, doc_type: str, default_suffix: str) -> tuple[str, int, int, str]:
    """Повертає (number, year, sequence_num, suffix)."""
    year = date.today().year
    suffix = get_setting(f"{doc_type}_suffix", default_suffix)

    row = conn.execute(
        "SELECT sequence, suffix FROM doc_sequences WHERE doc_type=? AND year=?",
        (doc_type, year)
    ).fetchone()

    if row:
        seq = row["sequence"]
        suffix = row["suffix"] or suffix
        conn.execute(
            "UPDATE doc_sequences SET sequence=?, updated_at=datetime('now','localtime') "
            "WHERE doc_type=? AND year=?",
            (seq + 1, doc_type, year)
        )
    else:
        seq = 1
        conn.execute(
            "INSERT INTO doc_sequences (doc_type, year, sequence, suffix) VALUES (?,?,2,?)",
            (doc_type, year, suffix)
        )

    number = f"{year}/{seq}/{suffix}"
    return number, year, seq, suffix


# ═══════════════════════════════════════════════════════════════
#  АКТИ СПИСАННЯ
# ═══════════════════════════════════════════════════════════════

@bp.route("/write-off/")
@login_required
def write_off_list():
    conn = get_connection()

    status   = request.args.get("status", "")
    unit_id  = request.args.get("unit_id", "")
    year     = request.args.get("year", "")
    search   = request.args.get("q", "").strip()

    conds  = ["1=1"]
    params = []

    if status:
        conds.append("w.status = ?"); params.append(status)
    if unit_id:
        conds.append("w.unit_id = ?"); params.append(unit_id)
    if year:
        conds.append("w.year = ?"); params.append(year)
    if search:
        conds.append("(w.number LIKE ? OR u.name LIKE ?)"); params += [f"%{search}%"] * 2

    rows = conn.execute(f"""
        SELECT w.id, w.number, w.act_date, w.status, w.total_sum,
               w.scan_path, w.created_at,
               u.name AS unit_name,
               COUNT(wi.id) AS items_count
        FROM write_offs w
        LEFT JOIN units u ON w.unit_id = u.id
        LEFT JOIN write_off_items wi ON wi.write_off_id = w.id
        WHERE {' AND '.join(conds)}
        GROUP BY w.id
        ORDER BY w.act_date DESC, w.id DESC
        LIMIT 300
    """, params).fetchall()

    units = conn.execute("SELECT id, name FROM units ORDER BY name").fetchall()
    years = conn.execute(
        "SELECT DISTINCT year FROM write_offs ORDER BY year DESC"
    ).fetchall()

    conn.close()
    return render_template(
        "acts/write_off_list.html",
        rows=[dict(r) for r in rows],
        units=[dict(u) for u in units],
        years=[r["year"] for r in years],
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        filters={"status": status, "unit_id": unit_id, "year": year, "q": search},
    )


@bp.route("/write-off/new", methods=["GET", "POST"])
@login_required
def write_off_new():
    conn = get_connection()

    if request.method == "POST":
        act_date  = request.form.get("act_date", "").strip() or str(date.today())
        unit_id   = request.form.get("unit_id", "") or None
        chief_rank  = request.form.get("chief_rank", "").strip()
        chief_name  = request.form.get("chief_name", "").strip()
        chief_is_tvo = 1 if request.form.get("chief_is_tvo") else 0
        commission  = request.form.get("commission_members", "").strip()
        base_doc    = request.form.get("base_document", "").strip()
        notes       = request.form.get("notes", "").strip()

        number, year, seq, suffix = _next_number(conn, "write_off", "АС")

        cur = conn.execute("""
            INSERT INTO write_offs
              (number, year, sequence_num, suffix, act_date, unit_id,
               chief_rank, chief_name, chief_is_tvo, commission_members,
               base_document, notes, status, total_sum, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'draft',0,?)
        """, (number, year, seq, suffix, act_date, unit_id,
              chief_rank, chief_name, chief_is_tvo, commission,
              base_doc, notes, _current_user_id()))
        wid = cur.lastrowid
        conn.commit()
        conn.close()

        log_action("create", "write_off", wid, f"Акт списання {number}")
        flash(f"Акт списання {number} створено", "success")
        return redirect(url_for("acts.write_off_view", wid=wid))

    units = conn.execute("SELECT id, name FROM units ORDER BY name").fetchall()
    conn.close()
    return render_template(
        "acts/write_off_form.html",
        act=None,
        units=[dict(u) for u in units],
        today=str(date.today()),
    )


@bp.route("/write-off/<int:wid>")
@login_required
def write_off_view(wid):
    conn = get_connection()
    act = conn.execute("""
        SELECT w.*, u.name AS unit_name,
               p.last_name || ' ' || COALESCE(p.first_name,'') || ' ' || COALESCE(p.middle_name,'') AS person_name
        FROM write_offs w
        LEFT JOIN units u ON w.unit_id = u.id
        LEFT JOIN personnel p ON w.personnel_id = p.id
        WHERE w.id = ?
    """, (wid,)).fetchone()

    if not act:
        conn.close()
        return redirect(url_for("acts.write_off_list"))

    items = conn.execute("""
        SELECT wi.*, d.name AS item_name, d.unit_of_measure AS item_unit
        FROM write_off_items wi
        JOIN item_dictionary d ON wi.item_id = d.id
        WHERE wi.write_off_id = ?
        ORDER BY wi.id
    """, (wid,)).fetchall()

    conn.close()
    return render_template(
        "acts/write_off_view.html",
        act=dict(act),
        items=[dict(i) for i in items],
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
    )


@bp.route("/write-off/<int:wid>/edit", methods=["GET", "POST"])
@login_required
def write_off_edit(wid):
    conn = get_connection()
    act = conn.execute("SELECT * FROM write_offs WHERE id=?", (wid,)).fetchone()
    if not act or act["status"] not in ("draft",):
        conn.close()
        flash("Редагування неможливе", "warning")
        return redirect(url_for("acts.write_off_view", wid=wid))

    if request.method == "POST":
        act_date    = request.form.get("act_date", "").strip() or str(date.today())
        unit_id     = request.form.get("unit_id", "") or None
        chief_rank  = request.form.get("chief_rank", "").strip()
        chief_name  = request.form.get("chief_name", "").strip()
        chief_is_tvo = 1 if request.form.get("chief_is_tvo") else 0
        commission  = request.form.get("commission_members", "").strip()
        base_doc    = request.form.get("base_document", "").strip()
        notes       = request.form.get("notes", "").strip()

        conn.execute("""
            UPDATE write_offs SET
              act_date=?, unit_id=?, chief_rank=?, chief_name=?, chief_is_tvo=?,
              commission_members=?, base_document=?, notes=?,
              updated_at=datetime('now','localtime')
            WHERE id=?
        """, (act_date, unit_id, chief_rank, chief_name, chief_is_tvo,
              commission, base_doc, notes, wid))
        conn.commit()
        conn.close()
        flash("Збережено", "success")
        return redirect(url_for("acts.write_off_view", wid=wid))

    units = conn.execute("SELECT id, name FROM units ORDER BY name").fetchall()
    items = conn.execute("""
        SELECT wi.*, d.name AS item_name, d.unit_of_measure AS item_unit
        FROM write_off_items wi
        JOIN item_dictionary d ON wi.item_id = d.id
        WHERE wi.write_off_id = ?
        ORDER BY wi.id
    """, (wid,)).fetchall()

    stock = conn.execute("""
        SELECT d.id, d.name, d.unit_of_measure,
               COALESCE(SUM(wi2.quantity),0) - COALESCE(SUM(wi3.quantity),0) AS balance
        FROM item_dictionary d
        LEFT JOIN warehouse_income wi2 ON wi2.item_id = d.id
        LEFT JOIN invoice_items ii ON ii.item_id = d.id
        LEFT JOIN invoices inv ON ii.invoice_id = inv.id AND inv.status = 'processed' AND inv.direction = 'issue'
        LEFT JOIN invoice_items ii3 ON ii3.item_id = d.id
        LEFT JOIN invoices inv3 ON ii3.invoice_id = inv3.id AND inv3.status = 'processed' AND inv3.direction = 'return'
        GROUP BY d.id
        HAVING COALESCE(SUM(wi2.quantity),0) > 0
        ORDER BY d.name
    """).fetchall()

    conn.close()
    return render_template(
        "acts/write_off_form.html",
        act=dict(act),
        items=[dict(i) for i in items],
        units=[dict(u) for u in units],
        stock=[dict(s) for s in stock],
        today=str(date.today()),
    )


@bp.route("/write-off/<int:wid>/confirm", methods=["POST"])
@login_required
def write_off_confirm(wid):
    """Оформити акт (draft → created). Зменшує залишки складу."""
    conn = get_connection()
    act = conn.execute("SELECT * FROM write_offs WHERE id=?", (wid,)).fetchone()
    if not act or act["status"] != "draft":
        conn.close()
        return jsonify(ok=False, msg="Неможливо оформити"), 400

    items = conn.execute(
        "SELECT * FROM write_off_items WHERE write_off_id=?", (wid,)
    ).fetchall()
    if not items:
        conn.close()
        return jsonify(ok=False, msg="Акт не містить жодної позиції"), 400

    # Розраховуємо загальну суму
    total = sum(float(i["quantity"]) * float(i["price"]) for i in items)

    conn.execute("""
        UPDATE write_offs SET status='created', total_sum=?,
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (round(total, 2), wid))
    conn.commit()
    conn.close()

    log_action("confirm", "write_off", wid, f"Акт списання {act['number']} оформлено")
    return jsonify(ok=True)


@bp.route("/write-off/<int:wid>/cancel", methods=["POST"])
@login_required
def write_off_cancel(wid):
    conn = get_connection()
    act = conn.execute("SELECT * FROM write_offs WHERE id=?", (wid,)).fetchone()
    if not act or act["status"] not in ("draft", "created"):
        conn.close()
        flash("Неможливо скасувати", "warning")
        return redirect(url_for("acts.write_off_view", wid=wid))

    conn.execute(
        "UPDATE write_offs SET status='cancelled', updated_at=datetime('now','localtime') WHERE id=?",
        (wid,)
    )
    conn.commit()
    conn.close()
    log_action("cancel", "write_off", wid, f"Акт {act['number']} скасовано")
    flash("Акт скасовано", "warning")
    return redirect(url_for("acts.write_off_view", wid=wid))


@bp.route("/write-off/<int:wid>/delete", methods=["POST"])
@login_required
def write_off_delete(wid):
    conn = get_connection()
    act = conn.execute("SELECT * FROM write_offs WHERE id=?", (wid,)).fetchone()
    if not act or act["status"] not in ("draft", "cancelled"):
        conn.close()
        flash("Видалення неможливе", "warning")
        return redirect(url_for("acts.write_off_view", wid=wid))

    conn.execute("DELETE FROM write_offs WHERE id=?", (wid,))
    conn.commit()
    conn.close()
    log_action("delete", "write_off", wid, f"Акт {act['number']} видалено")
    flash("Акт видалено", "success")
    return redirect(url_for("acts.write_off_list"))


# ── Позиції акта списання (AJAX) ─────────────────────────────

@bp.route("/write-off/<int:wid>/item/add", methods=["POST"])
@login_required
def write_off_item_add(wid):
    conn = get_connection()
    act = conn.execute("SELECT status FROM write_offs WHERE id=?", (wid,)).fetchone()
    if not act or act["status"] != "draft":
        conn.close()
        return jsonify(ok=False, msg="Редагування заблоковано"), 400

    item_id  = request.json.get("item_id")
    quantity = float(request.json.get("quantity", 1))
    price    = float(request.json.get("price", 0))
    category = int(request.json.get("category", 1))
    reason   = request.json.get("reason", "").strip()

    if not item_id:
        conn.close()
        return jsonify(ok=False, msg="Оберіть майно"), 400

    item = conn.execute("SELECT * FROM item_dictionary WHERE id=?", (item_id,)).fetchone()
    if not item:
        conn.close()
        return jsonify(ok=False, msg="Майно не знайдено"), 404

    cur = conn.execute("""
        INSERT INTO write_off_items (write_off_id, item_id, quantity, price, category, reason)
        VALUES (?,?,?,?,?,?)
    """, (wid, item_id, quantity, price, category, reason))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()

    return jsonify(ok=True, id=row_id, item_name=item["name"],
                   item_unit=item["unit"] or "шт",
                   quantity=quantity, price=price, category=category, reason=reason)


@bp.route("/write-off/<int:wid>/item/<int:iid>/edit", methods=["POST"])
@login_required
def write_off_item_edit(wid, iid):
    conn = get_connection()
    act = conn.execute("SELECT status FROM write_offs WHERE id=?", (wid,)).fetchone()
    if not act or act["status"] != "draft":
        conn.close()
        return jsonify(ok=False, msg="Редагування заблоковано"), 400

    quantity = float(request.json.get("quantity", 1))
    price    = float(request.json.get("price", 0))
    category = int(request.json.get("category", 1))
    reason   = request.json.get("reason", "").strip()

    conn.execute("""
        UPDATE write_off_items SET quantity=?, price=?, category=?, reason=?
        WHERE id=? AND write_off_id=?
    """, (quantity, price, category, reason, iid, wid))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@bp.route("/write-off/<int:wid>/item/<int:iid>/delete", methods=["POST"])
@login_required
def write_off_item_delete(wid, iid):
    conn = get_connection()
    act = conn.execute("SELECT status FROM write_offs WHERE id=?", (wid,)).fetchone()
    if not act or act["status"] != "draft":
        conn.close()
        return jsonify(ok=False, msg="Редагування заблоковано"), 400

    conn.execute("DELETE FROM write_off_items WHERE id=? AND write_off_id=?", (iid, wid))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ── Скан акта списання ────────────────────────────────────────

@bp.route("/write-off/<int:wid>/scan/upload", methods=["POST"])
@login_required
def write_off_scan_upload(wid):
    from pathlib import Path
    from core.settings import get_storage_path

    conn = get_connection()
    act = conn.execute("SELECT number FROM write_offs WHERE id=?", (wid,)).fetchone()
    if not act:
        conn.close()
        return jsonify(ok=False, msg="Акт не знайдено"), 404

    f = request.files.get("scan")
    if not f or not f.filename:
        conn.close()
        return jsonify(ok=False, msg="Файл не вибрано"), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in (".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif"):
        conn.close()
        return jsonify(ok=False, msg="Формат не підтримується"), 400

    safe_num = act["number"].replace("/", "_")
    filename = f"write_off_{safe_num}{ext}"
    scan_dir = get_storage_path() / "scans" / "write_offs"
    scan_dir.mkdir(parents=True, exist_ok=True)
    scan_path = f"scans/write_offs/{filename}"
    f.save(str(scan_dir / filename))

    conn.execute("UPDATE write_offs SET scan_path=? WHERE id=?", (scan_path, wid))
    conn.commit()
    conn.close()
    return jsonify(ok=True, scan_path=scan_path)


@bp.route("/write-off/<int:wid>/scan/delete", methods=["POST"])
@login_required
def write_off_scan_delete(wid):
    from pathlib import Path
    from core.settings import get_storage_path

    conn = get_connection()
    act = conn.execute("SELECT scan_path FROM write_offs WHERE id=?", (wid,)).fetchone()
    if act and act["scan_path"]:
        p = get_storage_path() / act["scan_path"]
        if p.exists():
            p.unlink()
    conn.execute("UPDATE write_offs SET scan_path=NULL WHERE id=?", (wid,))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ═══════════════════════════════════════════════════════════════
#  АКТИ ВВЕДЕННЯ В ЕКСПЛУАТАЦІЮ
# ═══════════════════════════════════════════════════════════════

@bp.route("/exploit/")
@login_required
def exploit_list():
    conn = get_connection()

    unit_id = request.args.get("unit_id", "")
    year    = request.args.get("year", "")
    search  = request.args.get("q", "").strip()

    conds  = ["1=1"]
    params = []
    if unit_id:
        conds.append("e.unit_id = ?"); params.append(unit_id)
    if year:
        conds.append("e.year = ?"); params.append(year)
    if search:
        conds.append("(e.number LIKE ? OR d.name LIKE ? OR u.name LIKE ?)"); params += [f"%{search}%"] * 3

    rows = conn.execute(f"""
        SELECT e.id, e.number, e.act_date, e.quantity, e.serial_number,
               e.scan_path, e.created_at,
               d.name AS item_name, d.unit_of_measure AS item_unit,
               u.name AS unit_name,
               p.last_name || ' ' || COALESCE(p.first_name,'') AS person_name
        FROM exploitation_acts e
        LEFT JOIN item_dictionary d ON e.item_id = d.id
        LEFT JOIN units u ON e.unit_id = u.id
        LEFT JOIN personnel p ON e.personnel_id = p.id
        WHERE {' AND '.join(conds)}
        ORDER BY e.act_date DESC, e.id DESC
        LIMIT 300
    """, params).fetchall()

    units = conn.execute("SELECT id, name FROM units ORDER BY name").fetchall()
    years = conn.execute(
        "SELECT DISTINCT year FROM exploitation_acts ORDER BY year DESC"
    ).fetchall()

    conn.close()
    return render_template(
        "acts/exploit_list.html",
        rows=[dict(r) for r in rows],
        units=[dict(u) for u in units],
        years=[r["year"] for r in years],
        filters={"unit_id": unit_id, "year": year, "q": search},
    )


@bp.route("/exploit/new", methods=["GET", "POST"])
@login_required
def exploit_new():
    conn = get_connection()

    if request.method == "POST":
        act_date     = request.form.get("act_date", "").strip() or str(date.today())
        unit_id      = request.form.get("unit_id", "") or None
        personnel_id = request.form.get("personnel_id", "") or None
        item_id      = request.form.get("item_id", "")
        quantity     = float(request.form.get("quantity", 1) or 1)
        serial_num   = request.form.get("serial_number", "").strip()
        chief_rank   = request.form.get("chief_rank", "").strip()
        chief_name   = request.form.get("chief_name", "").strip()
        chief_is_tvo = 1 if request.form.get("chief_is_tvo") else 0
        commission   = request.form.get("commission_members", "").strip()
        notes        = request.form.get("notes", "").strip()

        if not item_id:
            flash("Оберіть майно", "danger")
            units = conn.execute("SELECT id, name FROM units ORDER BY name").fetchall()
            conn.close()
            return render_template("acts/exploit_form.html", act=None,
                                   units=[dict(u) for u in units], today=str(date.today()))

        number, year, seq, _ = _next_number(conn, "exploit_act", "АВЕ")

        cur = conn.execute("""
            INSERT INTO exploitation_acts
              (number, year, sequence_num, act_date, unit_id, personnel_id,
               item_id, quantity, serial_number,
               chief_rank, chief_name, chief_is_tvo, commission_members, notes, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (number, year, seq, act_date, unit_id, personnel_id,
              item_id, quantity, serial_num,
              chief_rank, chief_name, chief_is_tvo, commission, notes, _current_user_id()))
        eid = cur.lastrowid
        conn.commit()
        conn.close()

        log_action("create", "exploit_act", eid, f"Акт введення в експлуатацію {number}")
        flash(f"Акт {number} створено", "success")
        return redirect(url_for("acts.exploit_view", eid=eid))

    units = conn.execute("SELECT id, name FROM units ORDER BY name").fetchall()
    items = conn.execute(
        "SELECT id, name, unit_of_measure AS unit FROM item_dictionary ORDER BY name"
    ).fetchall()
    conn.close()
    return render_template(
        "acts/exploit_form.html",
        act=None,
        units=[dict(u) for u in units],
        items=[dict(i) for i in items],
        today=str(date.today()),
    )


@bp.route("/exploit/<int:eid>")
@login_required
def exploit_view(eid):
    conn = get_connection()
    act = conn.execute("""
        SELECT e.*,
               d.name AS item_name, d.unit_of_measure AS item_unit,
               u.name AS unit_name,
               p.last_name || ' ' || COALESCE(p.first_name,'') || ' ' || COALESCE(p.middle_name,'') AS person_name
        FROM exploitation_acts e
        LEFT JOIN item_dictionary d ON e.item_id = d.id
        LEFT JOIN units u ON e.unit_id = u.id
        LEFT JOIN personnel p ON e.personnel_id = p.id
        WHERE e.id = ?
    """, (eid,)).fetchone()

    if not act:
        conn.close()
        return redirect(url_for("acts.exploit_list"))

    conn.close()
    return render_template("acts/exploit_view.html", act=dict(act))


@bp.route("/exploit/<int:eid>/edit", methods=["GET", "POST"])
@login_required
def exploit_edit(eid):
    conn = get_connection()
    act = conn.execute("SELECT * FROM exploitation_acts WHERE id=?", (eid,)).fetchone()
    if not act:
        conn.close()
        return redirect(url_for("acts.exploit_list"))

    if request.method == "POST":
        act_date     = request.form.get("act_date", "").strip() or str(date.today())
        unit_id      = request.form.get("unit_id", "") or None
        personnel_id = request.form.get("personnel_id", "") or None
        item_id      = request.form.get("item_id", "") or act["item_id"]
        quantity     = float(request.form.get("quantity", 1) or 1)
        serial_num   = request.form.get("serial_number", "").strip()
        chief_rank   = request.form.get("chief_rank", "").strip()
        chief_name   = request.form.get("chief_name", "").strip()
        chief_is_tvo = 1 if request.form.get("chief_is_tvo") else 0
        commission   = request.form.get("commission_members", "").strip()
        notes        = request.form.get("notes", "").strip()

        conn.execute("""
            UPDATE exploitation_acts SET
              act_date=?, unit_id=?, personnel_id=?, item_id=?, quantity=?,
              serial_number=?, chief_rank=?, chief_name=?, chief_is_tvo=?,
              commission_members=?, notes=?
            WHERE id=?
        """, (act_date, unit_id, personnel_id, item_id, quantity,
              serial_num, chief_rank, chief_name, chief_is_tvo,
              commission, notes, eid))
        conn.commit()
        conn.close()
        flash("Збережено", "success")
        return redirect(url_for("acts.exploit_view", eid=eid))

    units = conn.execute("SELECT id, name FROM units ORDER BY name").fetchall()
    items = conn.execute("SELECT id, name, unit_of_measure AS unit FROM item_dictionary ORDER BY name").fetchall()
    conn.close()
    return render_template(
        "acts/exploit_form.html",
        act=dict(act),
        units=[dict(u) for u in units],
        items=[dict(i) for i in items],
        today=str(date.today()),
    )


@bp.route("/exploit/<int:eid>/delete", methods=["POST"])
@login_required
def exploit_delete(eid):
    conn = get_connection()
    act = conn.execute("SELECT number FROM exploitation_acts WHERE id=?", (eid,)).fetchone()
    if act:
        conn.execute("DELETE FROM exploitation_acts WHERE id=?", (eid,))
        conn.commit()
        log_action("delete", "exploit_act", eid, f"Акт {act['number']} видалено")
        flash("Акт видалено", "success")
    conn.close()
    return redirect(url_for("acts.exploit_list"))


@bp.route("/exploit/<int:eid>/scan/upload", methods=["POST"])
@login_required
def exploit_scan_upload(eid):
    from pathlib import Path
    from core.settings import get_storage_path

    conn = get_connection()
    act = conn.execute("SELECT number FROM exploitation_acts WHERE id=?", (eid,)).fetchone()
    if not act:
        conn.close()
        return jsonify(ok=False, msg="Акт не знайдено"), 404

    f = request.files.get("scan")
    if not f or not f.filename:
        conn.close()
        return jsonify(ok=False, msg="Файл не вибрано"), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in (".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif"):
        conn.close()
        return jsonify(ok=False, msg="Формат не підтримується"), 400

    safe_num = act["number"].replace("/", "_")
    filename = f"exploit_{safe_num}{ext}"
    scan_dir = get_storage_path() / "scans" / "exploit_acts"
    scan_dir.mkdir(parents=True, exist_ok=True)
    scan_path = f"scans/exploit_acts/{filename}"
    f.save(str(scan_dir / filename))

    conn.execute("UPDATE exploitation_acts SET scan_path=? WHERE id=?", (scan_path, eid))
    conn.commit()
    conn.close()
    return jsonify(ok=True, scan_path=scan_path)


@bp.route("/exploit/<int:eid>/scan/delete", methods=["POST"])
@login_required
def exploit_scan_delete(eid):
    from pathlib import Path
    from core.settings import get_storage_path

    conn = get_connection()
    act = conn.execute("SELECT scan_path FROM exploitation_acts WHERE id=?", (eid,)).fetchone()
    if act and act["scan_path"]:
        p = get_storage_path() / act["scan_path"]
        if p.exists():
            p.unlink()
    conn.execute("UPDATE exploitation_acts SET scan_path=NULL WHERE id=?", (eid,))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ─────────────────────────────────────────────────────────────
#  Хелпери
# ─────────────────────────────────────────────────────────────

def _current_user_id():
    from flask import session
    return session.get("user_id")
