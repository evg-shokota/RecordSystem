"""
modules/planning/routes.py — Планування видачі майна

Порівнює видане майно з нормою видачі кожного військовослужбовця.
Показує хто → що → скільки потрібно ще → коли підійде час наступної видачі.
"""
from datetime import date, timedelta
from flask import Blueprint, render_template, request
from core.auth import login_required
from core.db import get_connection

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

    # Активні групи (не архів)
    archive_types = ('szch', 'deceased', 'missing')
    rows = conn.execute(f"""
        SELECT
            p.id            AS personnel_id,
            p.last_name || ' ' || p.first_name ||
                COALESCE(' ' || p.middle_name, '') AS full_name,
            p.rank,
            COALESCE(u.name, '') AS unit_name,
            nd.id           AS norm_dict_id,
            nd.name         AS norm_dict_name,
            nd.unit,
            sni.quantity    AS norm_qty,
            sni.wear_years,
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
            ) AS last_issue_date
        FROM personnel p
        JOIN groups g ON p.group_id = g.id
        JOIN supply_norms sn ON p.norm_id = sn.id
        JOIN supply_norm_items sni ON sni.norm_id = sn.id
        JOIN norm_dictionary nd ON sni.norm_dict_id = nd.id
        LEFT JOIN item_dictionary d ON d.norm_dict_id = nd.id
        LEFT JOIN units u ON p.unit_id = u.id
        WHERE p.is_active = 1
          AND g.type NOT IN ('szch', 'deceased', 'missing')
          AND sni.norm_dict_id IS NOT NULL
          {where_unit}
          {season_cond}
        GROUP BY p.id, nd.id
        ORDER BY p.last_name, p.first_name, nd.name
    """, params_filter).fetchall()

    today = date.today()
    result = []
    for r in rows:
        issued_qty   = r["issued_qty"] or 0
        norm_qty     = r["norm_qty"] or 0
        remaining    = max(0, norm_qty - issued_qty)
        wear_years   = r["wear_years"] or 0

        # Дата наступної видачі
        last_date_str   = r["last_issue_date"]
        next_issue_date = None
        next_issue_days = None
        if last_date_str and wear_years > 0:
            try:
                last_dt = date.fromisoformat(last_date_str[:10])
                next_dt = last_dt + timedelta(days=int(wear_years * 365))
                next_issue_date = next_dt.isoformat()
                next_issue_days = (next_dt - today).days
            except (ValueError, TypeError):
                pass

        row_data = {
            "personnel_id":    r["personnel_id"],
            "full_name":       r["full_name"].strip(),
            "rank":            r["rank"] or "",
            "unit_name":       r["unit_name"],
            "norm_dict_id":    r["norm_dict_id"],
            "norm_dict_name":  r["norm_dict_name"],
            "unit":            r["unit"] or "шт",
            "norm_qty":        norm_qty,
            "wear_years":      wear_years,
            "issued_qty":      issued_qty,
            "remaining":       remaining,
            "last_issue_date": last_date_str[:10] if last_date_str else None,
            "next_issue_date": next_issue_date,
            "next_issue_days": next_issue_days,
        }

        if only_needs and remaining <= 0 and (next_issue_days is None or next_issue_days > 0):
            continue

        result.append(row_data)

    return result


@bp.route("/")
@login_required
def index():
    conn = get_connection()

    unit_id_str  = request.args.get("unit_id", "")
    season       = request.args.get("season", "all")
    only_needs   = request.args.get("only_needs", "0") == "1"

    unit_id = int(unit_id_str) if unit_id_str.isdigit() else None

    # Список підрозділів для фільтра
    units = conn.execute(
        """SELECT u.id, u.name, b.name as bat_name
           FROM units u
           JOIN battalions b ON u.battalion_id = b.id
           ORDER BY b.name, u.name"""
    ).fetchall()

    rows = _planning_data(conn, unit_id=unit_id,
                          season_filter=season if season != "all" else "",
                          only_needs=only_needs)
    conn.close()

    # Підрахунки для заголовка
    total_persons = len({r["personnel_id"] for r in rows})
    total_needs   = sum(1 for r in rows if r["remaining"] > 0)

    return render_template(
        "planning/index.html",
        rows=rows,
        units=[dict(r) for r in units],
        unit_id=unit_id,
        season=season,
        only_needs=only_needs,
        total_persons=total_persons,
        total_needs=total_needs,
    )
