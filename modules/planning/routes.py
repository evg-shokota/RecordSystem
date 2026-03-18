"""
modules/planning/routes.py — Планування видачі майна

Порівнює видане майно з нормою видачі кожного військовослужбовця.
Показує хто → що → скільки потрібно ще → коли підійде час наступної видачі.
"""
from datetime import date, timedelta
from collections import defaultdict
from flask import Blueprint, render_template, request
from core.auth import login_required
from core.db import get_connection
from core.military_logic import get_next_issue_date as _next_issue, wear_years_to_months

bp = Blueprint("planning", __name__, url_prefix="/planning")


def _planning_data(conn, unit_id: int | None = None,
                   season_filter: str = "",
                   only_needs: bool = False) -> list[dict]:
    """
    Повертає список записів: {
        personnel_id, full_name, rank, unit_name,
        norm_dict_id, norm_dict_name, norm_qty, wear_years, unit,
        issued_qty, remaining, last_issue_date, next_issue_date, next_issue_days
    }
    Враховує тільки активний о/с (не архівні групи).
    """
    params_filter = []
    where_unit = ""
    if unit_id:
        where_unit = "AND p.unit_id = ?"
        params_filter.append(unit_id)

    season_cond = ""
    if season_filter and season_filter != "all":
        season_cond = "AND d.season = ?"
        params_filter.append(season_filter)

    rows = conn.execute(f"""
        SELECT
            p.id            AS personnel_id,
            p.last_name || ' ' || p.first_name ||
                COALESCE(' ' || p.middle_name, '') AS full_name,
            p.rank,
            COALESCE(u.name, '') AS unit_name,
            COALESCE(p.service_type, 'mobilized') AS service_type,
            p.enroll_date,
            nd.id           AS norm_dict_id,
            nd.name         AS norm_dict_name,
            nd.unit,
            sni.id          AS sni_id,
            COALESCE(sniw_p.qty, sni.quantity) AS norm_qty,
            sni.wear_years,
            pn.personnel_cat AS personnel_cat,
            -- Видано: сума активних personnel_items через item_dictionary.norm_dict_id
            COALESCE((
                SELECT SUM(pi.quantity)
                FROM personnel_items pi
                JOIN item_dictionary idi ON pi.item_id = idi.id
                WHERE pi.personnel_id = p.id
                  AND pi.status = 'active'
                  AND idi.norm_dict_id = nd.id
            ), 0) AS issued_qty,
            -- Остання дата видачі
            (
                SELECT MAX(COALESCE(pi.issue_date, pi.created_at))
                FROM personnel_items pi
                JOIN item_dictionary idi ON pi.item_id = idi.id
                WHERE pi.personnel_id = p.id
                  AND pi.status = 'active'
                  AND idi.norm_dict_id = nd.id
            ) AS last_issue_date,
            -- cycle_start_date з personnel_items (мобілізований)
            (
                SELECT pi.cycle_start_date
                FROM personnel_items pi
                JOIN item_dictionary idi ON pi.item_id = idi.id
                WHERE pi.personnel_id = p.id
                  AND pi.status = 'active'
                  AND idi.norm_dict_id = nd.id
                ORDER BY COALESCE(pi.issue_date, pi.created_at) DESC
                LIMIT 1
            ) AS cycle_start_date
        FROM personnel p
        JOIN groups g ON p.group_id = g.id
        JOIN personnel_norms pn ON pn.personnel_id = p.id
        JOIN supply_norm_items sni ON sni.norm_id = pn.norm_id
        JOIN norm_dictionary nd ON sni.norm_dict_id = nd.id
        LEFT JOIN item_dictionary d ON d.norm_dict_id = nd.id
        LEFT JOIN units u ON p.unit_id = u.id
        LEFT JOIN supply_norm_item_wear sniw_p
               ON sniw_p.norm_item_id = sni.id AND sniw_p.personnel_cat = pn.personnel_cat
        WHERE p.is_active = 1
          AND g.type NOT IN ('szch', 'deceased', 'missing')
          AND sni.norm_dict_id IS NOT NULL
          {where_unit}
          {season_cond}
        GROUP BY p.id, nd.id
        ORDER BY p.last_name, p.first_name, nd.name
    """, params_filter).fetchall()

    today = date.today()

    # Отримуємо wear_months з supply_norm_item_wear для конкретних (sni_id, cat) пар
    # Будуємо map (sni_id, cat) -> wear_months
    sni_cat_pairs = list({(r["sni_id"], r["personnel_cat"]) for r in rows})
    wear_lookup = {}
    if sni_cat_pairs:
        # Завантажуємо всі needed wear записи
        all_sni_ids = list({p[0] for p in sni_cat_pairs})
        placeholders = ",".join("?" * len(all_sni_ids))
        wear_rows = conn.execute(
            f"SELECT norm_item_id, personnel_cat, wear_months FROM supply_norm_item_wear WHERE norm_item_id IN ({placeholders})",
            all_sni_ids
        ).fetchall()
        for w in wear_rows:
            wear_lookup[(w["norm_item_id"], w["personnel_cat"])] = w["wear_months"]

    result = []
    # Дедублікація: якщо особа має 2 норми з одним і тим самим nd_id — беремо мінімальний строк
    dedup = {}  # (personnel_id, norm_dict_id) -> row_data
    for r in rows:
        issued_qty   = r["issued_qty"] or 0
        norm_qty     = r["norm_qty"] or 0
        remaining    = max(0, norm_qty - issued_qty)

        # Строк носіння: спочатку з wear_lookup по категорії, потім wear_years
        sni_id = r["sni_id"]
        pn_cat = r["personnel_cat"]
        wear_months_specific = wear_lookup.get((sni_id, pn_cat))
        if wear_months_specific and wear_months_specific > 0:
            wear_months = wear_months_specific
            wear_years  = wear_months / 12.0
        else:
            wear_years  = r["wear_years"] or 0
            wear_months = wear_years_to_months(wear_years)

        # Дата наступної видачі через military_logic
        service_type    = r["service_type"] or "mobilized"
        norm_date       = r["enroll_date"]
        cycle_start     = r["cycle_start_date"] or r["last_issue_date"]
        next_issue_date = None
        next_issue_days = None
        if wear_months > 0:
            try:
                next_dt = _next_issue(
                    service_type     = service_type,
                    cycle_start_date = cycle_start,
                    norm_date        = norm_date,
                    wear_months      = wear_months,
                )
                if next_dt:
                    next_issue_date = next_dt.isoformat()
                    next_issue_days = (next_dt - today).days
            except (ValueError, TypeError):
                pass

        key = (r["personnel_id"], r["norm_dict_id"])
        last_date_str = r["last_issue_date"]
        row_data = {
            "personnel_id":    r["personnel_id"],
            "full_name":       r["full_name"].strip(),
            "rank":            r["rank"] or "",
            "unit_name":       r["unit_name"],
            "service_type":    service_type,
            "norm_dict_id":    r["norm_dict_id"],
            "norm_dict_name":  r["norm_dict_name"],
            "unit":            r["unit"] or "шт",
            "norm_qty":        norm_qty,
            "wear_years":      wear_years,
            "wear_months":     wear_months,
            "issued_qty":      issued_qty,
            "remaining":       remaining,
            "last_issue_date": last_date_str[:10] if last_date_str else None,
            "next_issue_date": next_issue_date,
            "next_issue_days": next_issue_days,
        }

        if key in dedup:
            # Беремо мінімальний строк (більш суворий)
            existing = dedup[key]
            if wear_years > 0 and (existing["wear_years"] <= 0 or wear_years < existing["wear_years"]):
                existing["wear_years"] = wear_years
                existing["next_issue_date"] = next_issue_date
                existing["next_issue_days"] = next_issue_days
        else:
            dedup[key] = row_data

    for row_data in dedup.values():
        if only_needs and row_data["remaining"] <= 0 and (row_data["next_issue_days"] is None or row_data["next_issue_days"] > 0):
            continue
        result.append(row_data)

    return result


def _group_by_item(rows: list[dict]) -> list[dict]:
    """Групує рядки по norm_dict_id → {item, persons: [...]}."""
    groups = {}
    for r in rows:
        nid = r["norm_dict_id"]
        if nid not in groups:
            groups[nid] = {
                "norm_dict_id":   nid,
                "norm_dict_name": r["norm_dict_name"],
                "unit":           r["unit"],
                "persons":        [],
                "total_remaining": 0,
            }
        groups[nid]["persons"].append(r)
        groups[nid]["total_remaining"] += r["remaining"]
    return sorted(groups.values(), key=lambda g: g["norm_dict_name"])


def _group_by_calendar(rows: list[dict]) -> list[dict]:
    """Групує рядки по (рік, місяць) наступної видачі → список подій.
    Охоплює наступні 18 місяців + прострочені.
    """
    today = date.today()
    overdue = []
    months  = defaultdict(list)

    for r in rows:
        nid = r["next_issue_date"]
        rem = r["remaining"]
        # До видачі = є потреба АБО строк носіння підходить
        needs_issue = rem > 0 or (r["next_issue_days"] is not None and r["next_issue_days"] <= 0)
        upcoming    = r["next_issue_days"] is not None and 0 < r["next_issue_days"] <= 548  # 18 міс

        if needs_issue or upcoming:
            if r["next_issue_days"] is not None and r["next_issue_days"] <= 0:
                overdue.append(r)
            elif r["next_issue_date"]:
                try:
                    d = date.fromisoformat(r["next_issue_date"])
                    key = (d.year, d.month)
                    months[key].append(r)
                except ValueError:
                    pass
            elif rem > 0:
                overdue.append(r)

    result = []
    if overdue:
        result.append({
            "year": 0, "month": 0,
            "label": "Прострочено / Потребує видачі зараз",
            "rows": sorted(overdue, key=lambda r: r["next_issue_date"] or ""),
            "overdue": True,
        })

    # Наступні 18 місяців
    for i in range(18):
        m = today.month + i
        y = today.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        key = (y, m)
        if key in months:
            from calendar import month_name
            UA_MONTHS = ["","Січень","Лютий","Березень","Квітень","Травень","Червень",
                         "Липень","Серпень","Вересень","Жовтень","Листопад","Грудень"]
            result.append({
                "year": y, "month": m,
                "label": f"{UA_MONTHS[m]} {y}",
                "rows": sorted(months[key], key=lambda r: r["norm_dict_name"]),
                "overdue": False,
            })

    return result


@bp.route("/")
@login_required
def index():
    conn = get_connection()
    try:
        unit_id_str  = request.args.get("unit_id", "")
        season       = request.args.get("season", "all")
        only_needs   = request.args.get("only_needs", "0") == "1"
        view         = request.args.get("view", "list")   # list | by_item | calendar
        sort_by      = request.args.get("sort", "name")   # name | item | date

        unit_id = int(unit_id_str) if unit_id_str.isdigit() else None

        units = conn.execute(
            """SELECT u.id, u.name, b.name as bat_name
               FROM units u
               JOIN battalions b ON u.battalion_id = b.id
               ORDER BY b.name, u.name"""
        ).fetchall()

        norm_items = conn.execute(
            "SELECT id, name FROM norm_dictionary ORDER BY name"
        ).fetchall()

        norm_dict_id_str = request.args.get("norm_dict_id", "")
        norm_dict_id = int(norm_dict_id_str) if norm_dict_id_str.isdigit() else None

        rows = _planning_data(conn, unit_id=unit_id,
                              season_filter=season if season != "all" else "",
                              only_needs=only_needs)
    finally:
        conn.close()

    # Фільтр по конкретній позиції норми
    if norm_dict_id:
        rows = [r for r in rows if r["norm_dict_id"] == norm_dict_id]

    # Сортування
    if sort_by == "item":
        rows = sorted(rows, key=lambda r: (r["norm_dict_name"], r["full_name"]))
    elif sort_by == "date":
        rows = sorted(rows, key=lambda r: (r["next_issue_date"] or "9999", r["full_name"]))
    elif sort_by == "remaining":
        rows = sorted(rows, key=lambda r: (-r["remaining"], r["full_name"]))
    # default: name (вже відсортовано в SQL)

    # Підрахунки для заголовка
    total_persons = len({r["personnel_id"] for r in rows})
    total_needs   = sum(1 for r in rows if r["remaining"] > 0)

    # Дані для альтернативних виглядів
    by_item    = _group_by_item(rows)    if view == "by_item"   else []
    by_calendar = _group_by_calendar(rows) if view == "calendar" else []

    return render_template(
        "planning/index.html",
        rows=rows,
        by_item=by_item,
        by_calendar=by_calendar,
        units=[dict(r) for r in units],
        norm_items=[dict(r) for r in norm_items],
        unit_id=unit_id,
        season=season,
        only_needs=only_needs,
        total_persons=total_persons,
        total_needs=total_needs,
        view=view,
        sort_by=sort_by,
        norm_dict_id=norm_dict_id,
    )
