"""
modules/reports/routes.py — Звіти системи обліку речового майна

Маршрути:
  GET /reports/          — список звітів
  GET /reports/stock     — залишки складу
  GET /reports/turnover  — оборот за період
  GET /reports/needs     — потреба по нормі (=планування у форматі звіту)
  GET /reports/summary   — зведена відомість (о/с + майно)
Author: White
"""
from datetime import date, timedelta
from flask import Blueprint, render_template, request
from core.auth import login_required
from core.db import get_connection
from core.warehouse import get_stock
from core.military_logic import get_cycle_status, wear_years_to_months

bp = Blueprint("reports", __name__, url_prefix="/reports")


# ─────────────────────────────────────────────────────────────
#  Головна — список звітів
# ─────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    return render_template("reports/index.html")


# ─────────────────────────────────────────────────────────────
#  Залишки складу
# ─────────────────────────────────────────────────────────────

@bp.route("/stock")
@login_required
def stock():
    conn = get_connection()
    try:
        category_filter = request.args.get("category", "")
        search          = request.args.get("q", "").strip()
        rows = get_stock(conn)
    finally:
        conn.close()

    # Фільтрація на Python-стороні
    if category_filter:
        rows = [r for r in rows if r["category"] == category_filter]
    if search:
        q = search.lower()
        rows = [r for r in rows if q in r["item_name"].lower()]

    # Підсумки
    total_sum     = sum(r["total_sum"] or 0 for r in rows)
    total_items   = len(rows)
    categories    = ["I", "II", "III"]

    return render_template(
        "reports/stock.html",
        rows=rows,
        total_sum=total_sum,
        total_items=total_items,
        categories=categories,
        category_filter=category_filter,
        search=search,
        today=date.today().isoformat(),
    )


# ─────────────────────────────────────────────────────────────
#  Оборот майна за період
# ─────────────────────────────────────────────────────────────

@bp.route("/turnover")
@login_required
def turnover():
    conn = get_connection()
    try:
        # Діапазон дат за замовчуванням — поточний місяць
        today_dt = date.today()
        date_from = request.args.get("date_from", today_dt.replace(day=1).isoformat())
        date_to   = request.args.get("date_to",   today_dt.isoformat())

        income_rows = conn.execute("""
            SELECT wi.item_id, d.name AS item_name, d.unit_of_measure,
                   wi.category, wi.price,
                   SUM(wi.quantity) AS qty,
                   SUM(wi.quantity * wi.price) AS total_sum
            FROM warehouse_income wi
            JOIN item_dictionary d ON wi.item_id = d.id
            WHERE wi.date BETWEEN ? AND ?
            GROUP BY wi.item_id, wi.category, wi.price
            ORDER BY d.name, wi.category
        """, (date_from, date_to)).fetchall()

        issue_rows = conn.execute("""
            SELECT ii.item_id, d.name AS item_name, d.unit_of_measure,
                   ii.category, ii.price,
                   SUM(COALESCE(ii.actual_qty, ii.planned_qty)) AS qty,
                   SUM(COALESCE(ii.actual_qty, ii.planned_qty) * ii.price) AS total_sum
            FROM invoice_items ii
            JOIN invoices i ON ii.invoice_id = i.id
            JOIN item_dictionary d ON ii.item_id = d.id
            WHERE i.direction = 'issue'
              AND i.status = 'processed'
              AND DATE(i.updated_at) BETWEEN ? AND ?
            GROUP BY ii.item_id, ii.category, ii.price
            ORDER BY d.name, ii.category
        """, (date_from, date_to)).fetchall()

        rv_issue_rows = conn.execute("""
            SELECT dsq.item_id, d.name AS item_name, d.unit_of_measure,
                   dsi.category, dsi.price,
                   SUM(COALESCE(dsq.actual_qty, dsq.quantity)) AS qty,
                   SUM(COALESCE(dsq.actual_qty, dsq.quantity) * dsi.price) AS total_sum
            FROM distribution_sheet_quantities dsq
            JOIN distribution_sheet_items dsi ON dsi.sheet_id = dsq.sheet_id
                                              AND dsi.item_id = dsq.item_id
            JOIN distribution_sheets ds ON ds.id = dsq.sheet_id
            JOIN item_dictionary d ON dsq.item_id = d.id
            WHERE ds.direction = 'issue'
              AND ds.status = 'processed'
              AND DATE(ds.updated_at) BETWEEN ? AND ?
            GROUP BY dsq.item_id, dsi.category, dsi.price
            ORDER BY d.name, dsi.category
        """, (date_from, date_to)).fetchall()
    finally:
        conn.close()

    # Об'єднати видачу з накладних і РВ
    issue_combined = {}
    for r in list(issue_rows) + list(rv_issue_rows):
        key = (r["item_id"], r["category"], r["price"])
        if key not in issue_combined:
            issue_combined[key] = {
                "item_id": r["item_id"],
                "item_name": r["item_name"],
                "unit_of_measure": r["unit_of_measure"],
                "category": r["category"],
                "price": r["price"],
                "qty": 0,
                "total_sum": 0,
            }
        issue_combined[key]["qty"]       += r["qty"] or 0
        issue_combined[key]["total_sum"] += r["total_sum"] or 0

    income_total = sum(r["total_sum"] or 0 for r in income_rows)
    issue_total  = sum(v["total_sum"] for v in issue_combined.values())

    return render_template(
        "reports/turnover.html",
        income_rows=[dict(r) for r in income_rows],
        issue_rows=sorted(issue_combined.values(), key=lambda r: r["item_name"]),
        income_total=income_total,
        issue_total=issue_total,
        date_from=date_from,
        date_to=date_to,
        today=date.today().isoformat(),
    )


# ─────────────────────────────────────────────────────────────
#  Потреба по нормі (зведений звіт з планування)
# ─────────────────────────────────────────────────────────────

@bp.route("/needs")
@login_required
def needs():
    from modules.planning.routes import _planning_data
    conn = get_connection()
    try:
        unit_id_str = request.args.get("unit_id", "")
        unit_id = int(unit_id_str) if unit_id_str.isdigit() else None

        units = conn.execute(
            """SELECT u.id, u.name, b.name as bat_name
               FROM units u
               JOIN battalions b ON u.battalion_id = b.id
               ORDER BY b.name, u.name"""
        ).fetchall()

        rows = _planning_data(conn, unit_id=unit_id, only_needs=True)
        rows = [r for r in rows if r["remaining"] > 0]
    finally:
        conn.close()

    # Згрупувати по позиції норми
    by_norm = {}
    for r in rows:
        key = r["norm_dict_name"]
        if key not in by_norm:
            by_norm[key] = {
                "name": key,
                "unit": r["unit"],
                "total_remaining": 0,
                "persons": [],
            }
        by_norm[key]["total_remaining"] += r["remaining"]
        by_norm[key]["persons"].append(r)

    return render_template(
        "reports/needs.html",
        by_norm=sorted(by_norm.values(), key=lambda x: x["name"]),
        all_rows=rows,
        units=[dict(r) for r in units],
        unit_id=unit_id,
        today=date.today().isoformat(),
    )


# ─────────────────────────────────────────────────────────────
#  Звіт по боргу (хто не отримав по нормі)
# ─────────────────────────────────────────────────────────────

@bp.route("/debt")
@login_required
def debt():
    """Звіт: борг по нормах видачі — хто, що і скільки не отримав."""
    conn = get_connection()
    try:
        unit_id_str  = request.args.get("unit_id", "")
        service_type_f = request.args.get("service_type", "")  # "" | "mobilized" | "contract"
        unit_id = int(unit_id_str) if unit_id_str.isdigit() else None

        units = conn.execute(
            """SELECT u.id, u.name, b.name as bat_name
               FROM units u JOIN battalions b ON u.battalion_id = b.id
               ORDER BY b.name, u.name"""
        ).fetchall()

        where_parts = ["p.is_active = 1", "g.type NOT IN ('szch', 'deceased', 'missing')", "sni.norm_dict_id IS NOT NULL"]
        params: list = []
        if unit_id:
            where_parts.append("p.unit_id = ?")
            params.append(unit_id)
        if service_type_f:
            where_parts.append("COALESCE(p.service_type, 'mobilized') = ?")
            params.append(service_type_f)

        where_sql = " AND ".join(where_parts)

        rows = conn.execute(f"""
            SELECT
                p.id AS personnel_id,
                p.last_name || ' ' || p.first_name || COALESCE(' ' || p.middle_name, '') AS full_name,
                p.rank,
                COALESCE(u.name, '') AS unit_name,
                COALESCE(p.service_type, 'mobilized') AS service_type,
                p.enroll_date,
                nd.id   AS norm_dict_id,
                nd.name AS norm_dict_name,
                nd.unit AS unit,
                sni.id       AS sni_id,
                COALESCE(sniw_r.qty, sni.quantity) AS norm_qty,
                pn.personnel_cat AS personnel_cat,
                COALESCE((
                    SELECT SUM(pi.quantity) FROM personnel_items pi
                    JOIN item_dictionary idi ON pi.item_id = idi.id
                    WHERE pi.personnel_id = p.id AND pi.status = 'active'
                      AND idi.norm_dict_id = nd.id
                ), 0) AS issued_qty,
                (
                    SELECT MAX(COALESCE(pi.issue_date, pi.created_at)) FROM personnel_items pi
                    JOIN item_dictionary idi ON pi.item_id = idi.id
                    WHERE pi.personnel_id = p.id AND pi.status = 'active'
                      AND idi.norm_dict_id = nd.id
                ) AS last_issue_date,
                (
                    SELECT pi.cycle_start_date FROM personnel_items pi
                    JOIN item_dictionary idi ON pi.item_id = idi.id
                    WHERE pi.personnel_id = p.id AND pi.status = 'active'
                      AND idi.norm_dict_id = nd.id
                    ORDER BY COALESCE(pi.issue_date, pi.created_at) DESC LIMIT 1
                ) AS cycle_start_date
            FROM personnel p
            JOIN groups g ON p.group_id = g.id
            JOIN personnel_norms pn ON pn.personnel_id = p.id
            JOIN supply_norm_items sni ON sni.norm_id = pn.norm_id
            JOIN norm_dictionary nd ON sni.norm_dict_id = nd.id
            LEFT JOIN units u ON p.unit_id = u.id
            LEFT JOIN supply_norm_item_wear sniw_r
                   ON sniw_r.norm_item_id = sni.id AND sniw_r.personnel_cat = pn.personnel_cat
            WHERE {where_sql}
            GROUP BY p.id, nd.id
            ORDER BY p.last_name, p.first_name, nd.name
        """, params).fetchall()

        # Завантажуємо wear_months
        sni_ids = list({r["sni_id"] for r in rows})
        wear_map: dict = {}
        if sni_ids:
            ph = ",".join("?" * len(sni_ids))
            for w in conn.execute(
                f"SELECT norm_item_id, personnel_cat, wear_months FROM supply_norm_item_wear WHERE norm_item_id IN ({ph})",
                sni_ids
            ).fetchall():
                wear_map[(w["norm_item_id"], w["personnel_cat"])] = int(w["wear_months"] or 0)
    finally:
        conn.close()

    today = date.today()
    result = []
    for r in rows:
        issued_qty = float(r["issued_qty"] or 0)
        norm_qty   = float(r["norm_qty"]   or 0)
        if norm_qty <= 0:
            continue

        wear_months = wear_map.get((r["sni_id"], r["personnel_cat"]),
                                   wear_years_to_months(r.get("wear_years")))

        cs = get_cycle_status(
            service_type     = r["service_type"],
            cycle_start_date = r["cycle_start_date"] or r["last_issue_date"],
            norm_date        = r["enroll_date"],
            wear_months      = wear_months,
            issued_qty       = issued_qty,
            norm_qty         = norm_qty,
        )

        if cs["debt_qty"] <= 0:
            continue

        result.append({
            "personnel_id":    r["personnel_id"],
            "full_name":       r["full_name"].strip(),
            "rank":            r["rank"] or "",
            "unit_name":       r["unit_name"],
            "service_type":    r["service_type"],
            "norm_dict_name":  r["norm_dict_name"],
            "unit":            r["unit"] or "шт",
            "norm_qty":        norm_qty,
            "issued_qty":      issued_qty,
            "debt_qty":        cs["debt_qty"],
            "color":           cs["color"],
            "next_issue_date": cs["next_issue_date"],
            "days_left":       cs["days_left"],
        })

    # Зведення по позиції норми
    by_norm: dict = {}
    for r in result:
        key = r["norm_dict_name"]
        if key not in by_norm:
            by_norm[key] = {"name": key, "unit": r["unit"], "total_debt": 0.0, "persons": []}
        by_norm[key]["total_debt"] += r["debt_qty"]
        by_norm[key]["persons"].append(r)

    total_debt_persons = len({r["personnel_id"] for r in result})

    return render_template(
        "reports/debt.html",
        result=result,
        by_norm=sorted(by_norm.values(), key=lambda x: x["name"]),
        total_debt_persons=total_debt_persons,
        units=[dict(r) for r in units],
        unit_id=unit_id,
        service_type_f=service_type_f,
        today=today.isoformat(),
    )


# ─────────────────────────────────────────────────────────────
#  Зведена відомість о/с + майно
# ─────────────────────────────────────────────────────────────

@bp.route("/summary")
@login_required
def summary():
    conn = get_connection()
    try:
        unit_id_str = request.args.get("unit_id", "")
        unit_id = int(unit_id_str) if unit_id_str.isdigit() else None

        units = conn.execute(
            """SELECT u.id, u.name, b.name as bat_name
               FROM units u
               JOIN battalions b ON u.battalion_id = b.id
               ORDER BY b.name, u.name"""
        ).fetchall()

        where_unit = "AND p.unit_id = ?" if unit_id else ""
        params = [unit_id] if unit_id else []

        personnel_rows = conn.execute(f"""
            SELECT p.id, p.last_name, p.first_name, p.middle_name, p.rank,
                   COALESCE(u.name, '') AS unit_name,
                   COALESCE(b.name, '') AS bat_name
            FROM personnel p
            LEFT JOIN units u ON p.unit_id = u.id
            LEFT JOIN battalions b ON p.battalion_id = b.id
            JOIN groups g ON p.group_id = g.id
            WHERE p.is_active = 1
              AND g.type NOT IN ('szch', 'deceased', 'missing')
              {where_unit}
            ORDER BY b.name, u.name, p.last_name, p.first_name
        """, params).fetchall()

        if not personnel_rows:
            return render_template(
                "reports/summary.html",
                by_unit={},
                units=[dict(r) for r in units],
                unit_id=unit_id,
                today=date.today().isoformat(),
            )

        pid_list = [r["id"] for r in personnel_rows]
        pid_ph   = ",".join("?" * len(pid_list))

        items_rows = conn.execute(f"""
            SELECT pi.personnel_id, d.name AS item_name, pi.quantity,
                   pi.category, pi.issue_date
            FROM personnel_items pi
            JOIN item_dictionary d ON pi.item_id = d.id
            WHERE pi.personnel_id IN ({pid_ph})
              AND pi.status = 'active'
            ORDER BY d.name
        """, pid_list).fetchall()
    finally:
        conn.close()

    # Групуємо майно по personnel_id
    items_by_person = {}
    for item in items_rows:
        pid = item["personnel_id"]
        if pid not in items_by_person:
            items_by_person[pid] = []
        items_by_person[pid].append(dict(item))

    # Групуємо о/с по підрозділу
    by_unit = {}
    for p in personnel_rows:
        unit_key = f"{p['bat_name']} / {p['unit_name']}" if p["bat_name"] else (p["unit_name"] or "Без підрозділу")
        if unit_key not in by_unit:
            by_unit[unit_key] = []
        by_unit[unit_key].append({
            "id":          p["id"],
            "last_name":   p["last_name"],
            "first_name":  p["first_name"],
            "middle_name": p["middle_name"],
            "rank":        p["rank"] or "",
            "items":       items_by_person.get(p["id"], []),
        })

    return render_template(
        "reports/summary.html",
        by_unit=by_unit,
        units=[dict(r) for r in units],
        unit_id=unit_id,
        today=date.today().isoformat(),
    )


# ─────────────────────────────────────────────────────────────
#  Звіт по особі — хронологія видач/повернень
# ─────────────────────────────────────────────────────────────

@bp.route("/person/<int:person_id>")
@login_required
def person_report(person_id):
    """Хронологія всіх видач і повернень для конкретного військовослужбовця."""
    conn = get_connection()
    try:
        person = conn.execute(
            "SELECT * FROM personnel WHERE id=?", (person_id,)
        ).fetchone()
        if not person:
            from flask import abort
            abort(404)

        # Всі накладні де особа є отримувачем
        invoice_rows = conn.execute("""
            SELECT i.id, i.number, i.direction, i.status,
                   i.doc_date, i.updated_at,
                   i.base_document,
                   SUM(COALESCE(ii.actual_qty, ii.planned_qty) * ii.price) AS total_sum,
                   COUNT(ii.id) AS items_count
            FROM invoices i
            JOIN invoice_items ii ON ii.invoice_id = i.id
            WHERE i.recipient_personnel_id = ?
              AND i.status = 'processed'
            GROUP BY i.id
            ORDER BY i.updated_at DESC
        """, (person_id,)).fetchall()

        # Детальний перелік майна на картці (active)
        items_rows = conn.execute("""
            SELECT pi.*, d.name AS item_name, d.unit_of_measure,
                   i.number AS invoice_number, i.doc_date AS invoice_date
            FROM personnel_items pi
            JOIN item_dictionary d ON pi.item_id = d.id
            LEFT JOIN invoices i ON pi.invoice_id = i.id
            WHERE pi.personnel_id = ?
            ORDER BY pi.issue_date DESC, d.name
        """, (person_id,)).fetchall()

        # Загальна сума активного майна
        total_active = sum(
            (r["quantity"] or 0) * (r["price"] or 0)
            for r in items_rows if r["status"] == "active"
        )

    finally:
        conn.close()

    return render_template(
        "reports/person_report.html",
        person=person,
        invoice_rows=[dict(r) for r in invoice_rows],
        items_rows=[dict(r) for r in items_rows],
        total_active=total_active,
        today=date.today().isoformat(),
    )
