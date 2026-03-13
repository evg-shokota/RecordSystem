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

    category_filter = request.args.get("category", "")
    search          = request.args.get("q", "").strip()

    rows = get_stock(conn)
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

    # Діапазон дат за замовчуванням — поточний місяць
    today_dt = date.today()
    date_from = request.args.get("date_from", today_dt.replace(day=1).isoformat())
    date_to   = request.args.get("date_to",   today_dt.isoformat())

    # Прихід за період
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

    # Видача за період (processed накладні)
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

    # Видача за РВ (processed)
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

    unit_id_str = request.args.get("unit_id", "")
    unit_id = int(unit_id_str) if unit_id_str.isdigit() else None

    units = conn.execute(
        """SELECT u.id, u.name, b.name as bat_name
           FROM units u
           JOIN battalions b ON u.battalion_id = b.id
           ORDER BY b.name, u.name"""
    ).fetchall()

    # Тільки ті, хто має потребу
    rows = _planning_data(conn, unit_id=unit_id, only_needs=True)
    # Фільтр: тільки залишок > 0 (не вистачає за нормою)
    rows = [r for r in rows if r["remaining"] > 0]
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
#  Зведена відомість о/с + майно
# ─────────────────────────────────────────────────────────────

@bp.route("/summary")
@login_required
def summary():
    conn = get_connection()

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

    # Особовий склад з групуванням по підрозділу
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
        conn.close()
        return render_template(
            "reports/summary.html",
            by_unit={},
            units=[dict(r) for r in units],
            unit_id=unit_id,
            today=date.today().isoformat(),
        )

    pid_list = [r["id"] for r in personnel_rows]
    pid_ph   = ",".join("?" * len(pid_list))

    # Майно активних о/с
    items_rows = conn.execute(f"""
        SELECT pi.personnel_id, d.name AS item_name, pi.quantity,
               pi.category, pi.issue_date
        FROM personnel_items pi
        JOIN item_dictionary d ON pi.item_id = d.id
        WHERE pi.personnel_id IN ({pid_ph})
          AND pi.status = 'active'
        ORDER BY d.name
    """, pid_list).fetchall()

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
