"""
modules/personnel/routes.py — маршрути модуля Особовий склад
Author: White
"""
import json
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, jsonify, session, flash
)
from core.auth import login_required
from core.db import get_connection
from core.audit import log_action
from core.hooks import emit, filter_value, collect

bp = Blueprint("personnel", __name__, url_prefix="/personnel")

PER_PAGE = 50

def _get_ranks() -> list:
    """Повертає список звань з БД згідно активного режиму."""
    from core.settings import get_setting
    mode = get_setting("rank_mode", "army")
    conn = get_connection()
    rows = conn.execute(
        """SELECT name, short_name, category, subcategory, insignia
           FROM rank_presets
           WHERE mode=? AND is_active=1
           ORDER BY sort_order, id""",
        (mode,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_default_active_group_id(conn) -> int | None:
    """Повертає id групи типу 'active' (Картотека) — для автовибору при створенні."""
    row = conn.execute(
        "SELECT id FROM groups WHERE type = 'active' ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def _next_card_number(conn) -> str:
    """
    Генерує наступний номер картки.
    Використовує doc_sequences з doc_type='personnel_card'.
    Формат: порядковий номер (1, 2, 3, ...) без прив'язки до року.
    """
    from core.settings import get_setting
    row = conn.execute(
        "SELECT sequence FROM doc_sequences WHERE doc_type='personnel_card' AND year=0"
    ).fetchone()
    if row:
        seq = row["sequence"]
        conn.execute(
            "UPDATE doc_sequences SET sequence=?, updated_at=datetime('now','localtime') "
            "WHERE doc_type='personnel_card' AND year=0",
            (seq + 1,)
        )
    else:
        seq = 1
        conn.execute(
            "INSERT INTO doc_sequences (doc_type, year, sequence, suffix) VALUES ('personnel_card',0,2,'')"
        )
    conn.commit()
    return str(seq)

ARCHIVE_REASONS = [
    "переведення", "демобілізація", "загибель",
    "самовільне залишення частини (СЗЧ)",
    "безвісті зниклий", "інше",
]


# ─────────────────────────────────────────────────────────────
#  Допоміжні функції
# ─────────────────────────────────────────────────────────────

def _get_dismissed_group_id(conn) -> int | None:
    row = conn.execute(
        "SELECT id FROM groups WHERE type = 'dismissed' LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def _get_all_groups(conn) -> list:
    return conn.execute(
        "SELECT id, name, type FROM groups ORDER BY id"
    ).fetchall()


def _get_all_battalions(conn) -> list:
    return conn.execute(
        "SELECT id, name FROM battalions ORDER BY name"
    ).fetchall()


def _get_units_by_battalion(conn, battalion_id) -> list:
    return conn.execute(
        "SELECT id, name FROM units WHERE battalion_id = ? ORDER BY name",
        (battalion_id,)
    ).fetchall()


def _get_platoons_by_unit(conn, unit_id) -> list:
    return conn.execute(
        "SELECT id, name FROM platoons WHERE unit_id = ? ORDER BY name",
        (unit_id,)
    ).fetchall()


def _active_inventory_items(conn, personnel_id: int) -> list:
    """Повертає список активного інвентарного майна особи."""
    rows = conn.execute(
        """SELECT pi.id, d.name, pi.quantity, pi.price
           FROM personnel_items pi
           JOIN item_dictionary d ON pi.item_id = d.id
           WHERE pi.personnel_id = ?
             AND pi.status = 'active'
             AND d.is_inventory = 1""",
        (personnel_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
#  Список особового складу
# ─────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    conn = get_connection()

    # --- параметри фільтрів ---
    unit_id    = request.args.get("unit_id",    type=int)
    group_id   = request.args.get("group_id",   type=int)
    search     = request.args.get("q",          "").strip()
    show_arch  = request.args.get("archived",   "0") == "1"
    page       = request.args.get("page",       1, type=int)
    if page < 1:
        page = 1

    # --- базовий запит ---
    where_parts = []
    params = []

    if show_arch:
        where_parts.append("p.is_active = 0")
    else:
        where_parts.append("p.is_active = 1")

    if unit_id:
        where_parts.append("p.unit_id = ?")
        params.append(unit_id)

    if group_id:
        where_parts.append("p.group_id = ?")
        params.append(group_id)

    if search:
        where_parts.append(
            "(p.last_name LIKE ? OR p.first_name LIKE ? OR p.middle_name LIKE ? OR p.ipn LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])

    where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

    base_sql = f"""
        SELECT p.id, p.last_name, p.first_name, p.middle_name,
               p.rank, p.position, p.is_active,
               b.name  AS battalion_name,
               u.name  AS unit_name,
               pl.name AS platoon_name,
               g.name  AS group_name,
               g.type  AS group_type
        FROM personnel p
        LEFT JOIN battalions b  ON p.battalion_id = b.id
        LEFT JOIN units      u  ON p.unit_id      = u.id
        LEFT JOIN platoons   pl ON p.platoon_id   = pl.id
        LEFT JOIN groups     g  ON p.group_id     = g.id
        {where_sql}
    """

    total = conn.execute(
        f"SELECT COUNT(*) FROM ({base_sql})", params
    ).fetchone()[0]

    offset = (page - 1) * PER_PAGE
    rows = conn.execute(
        base_sql + " ORDER BY p.last_name, p.first_name LIMIT ? OFFSET ?",
        params + [PER_PAGE, offset]
    ).fetchall()

    groups     = _get_all_groups(conn)
    battalions = _get_all_battalions(conn)

    # усі підрозділи для фільтру
    all_units = conn.execute(
        "SELECT id, battalion_id, name FROM units ORDER BY name"
    ).fetchall()

    # Підрахунок потреб у видачі для кожної особи на сторінці
    from datetime import date, timedelta
    today_str = date.today().isoformat()
    needs_map = {}  # person_id -> {"summer": int, "winter": int, "total": int}
    person_ids = [r["id"] for r in rows]
    if person_ids:
        placeholders = ",".join("?" * len(person_ids))
        # Рахуємо скільки позицій норми потребують видачі (строк вийшов або не видавалось)
        need_rows = conn.execute(f"""
            SELECT p.id AS person_id,
                   COUNT(DISTINCT sni.norm_dict_id) AS total_needed
            FROM personnel p
            JOIN supply_norm_items sni ON sni.norm_id = p.norm_id
            WHERE p.id IN ({placeholders})
              AND (
                -- Не видавалось взагалі
                NOT EXISTS (
                    SELECT 1 FROM personnel_items pi
                    JOIN item_dictionary d ON pi.item_id = d.id
                    WHERE pi.personnel_id = p.id
                      AND d.norm_dict_id = sni.norm_dict_id
                      AND pi.status = 'active'
                )
                OR
                -- Строк носіння вийшов (wear_years > 0)
                (sni.wear_years > 0 AND EXISTS (
                    SELECT 1 FROM personnel_items pi
                    JOIN item_dictionary d ON pi.item_id = d.id
                    WHERE pi.personnel_id = p.id
                      AND d.norm_dict_id = sni.norm_dict_id
                      AND pi.status = 'active'
                      AND pi.issue_date IS NOT NULL
                      AND date(pi.issue_date, '+' || CAST(CAST(sni.wear_years * 365 AS INT) AS TEXT) || ' days') <= ?
                ))
              )
            GROUP BY p.id
        """, person_ids + [today_str]).fetchall()
        for r in need_rows:
            needs_map[r["person_id"]] = r["total_needed"]

    conn.close()

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)

    return render_template(
        "personnel/index.html",
        personnel=rows,
        groups=groups,
        battalions=battalions,
        all_units=all_units,
        total=total,
        page=page,
        total_pages=total_pages,
        per_page=PER_PAGE,
        needs_map=needs_map,
        filter_unit_id=unit_id,
        filter_group_id=group_id,
        filter_search=search,
        show_arch=show_arch,
    )


# ─────────────────────────────────────────────────────────────
#  Додавання
# ─────────────────────────────────────────────────────────────

@bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    conn = get_connection()

    if request.method == "POST":
        data = _collect_form()
        errors = _validate_form(data, conn)

        if errors:
            groups     = _get_all_groups(conn)
            battalions = _get_all_battalions(conn)
            units      = _get_units_by_battalion(conn, data.get("battalion_id")) if data.get("battalion_id") else []
            platoons   = _get_platoons_by_unit(conn, data.get("unit_id")) if data.get("unit_id") else []
            supply_norms = conn.execute(
                "SELECT id, name FROM supply_norms WHERE is_active=1 ORDER BY name"
            ).fetchall()
            conn.close()
            return render_template(
                "personnel/form.html",
                person=data, errors=errors,
                groups=groups, battalions=battalions,
                units=units, platoons=platoons,
                ranks=_get_ranks(), is_edit=False,
                supply_norms=supply_norms,
            )

        # Автовибір групи: якщо не обрано — ставимо "Картотека" (type='active')
        if not data["group_id"]:
            data["group_id"] = _get_default_active_group_id(conn)

        # Автономер картки: якщо не вказано — генеруємо наступний
        if not data["card_number"]:
            data["card_number"] = _next_card_number(conn)

        cur = conn.execute(
            """INSERT INTO personnel
               (last_name, first_name, middle_name, rank, position, category,
                battalion_id, unit_id, platoon_id, group_id,
                ipn, card_number, phone,
                size_head, size_height, size_underwear, size_suit,
                size_jacket, size_pants, size_shoes,
                enroll_date, enroll_order, dismiss_date, dismiss_order,
                draft_date, draft_by, norm_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["last_name"], data["first_name"], data["middle_name"],
                data["rank"], data["position"], data["category"],
                data["battalion_id"], data["unit_id"], data["platoon_id"], data["group_id"],
                data["ipn"], data["card_number"], data["phone"],
                data["size_head"], data["size_height"], data["size_underwear"], data["size_suit"],
                data["size_jacket"], data["size_pants"], data["size_shoes"],
                data["enroll_date"], data["enroll_order"],
                data["dismiss_date"], data["dismiss_order"],
                data["draft_date"], data["draft_by"], data["norm_id"],
            )
        )
        conn.commit()
        new_id = cur.lastrowid
        log_action("add", "personnel", new_id, None, data)
        conn.close()
        emit("personnel.created", person_id=new_id, data=data)
        return redirect(url_for("personnel.card", person_id=new_id))

    # GET
    groups     = _get_all_groups(conn)
    battalions = _get_all_battalions(conn)
    default_group_id = _get_default_active_group_id(conn)
    supply_norms = conn.execute(
        "SELECT id, name FROM supply_norms WHERE is_active=1 ORDER BY name"
    ).fetchall()
    conn.close()

    return render_template(
        "personnel/form.html",
        person={"group_id": default_group_id}, errors={},
        groups=groups, battalions=battalions,
        units=[], platoons=[],
        ranks=_get_ranks(), is_edit=False,
        supply_norms=supply_norms,
    )


# ─────────────────────────────────────────────────────────────
#  Перегляд картки
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>")
@login_required
def card(person_id):
    conn = get_connection()

    person = conn.execute(
        """SELECT p.*,
                  b.name  AS battalion_name,
                  u.name  AS unit_name,
                  pl.name AS platoon_name,
                  g.name  AS group_name,
                  g.type  AS group_type
           FROM personnel p
           LEFT JOIN battalions b  ON p.battalion_id = b.id
           LEFT JOIN units      u  ON p.unit_id      = u.id
           LEFT JOIN platoons   pl ON p.platoon_id   = pl.id
           LEFT JOIN groups     g  ON p.group_id     = g.id
           WHERE p.id = ?""",
        (person_id,)
    ).fetchone()

    if not person:
        conn.close()
        return "Особу не знайдено", 404

    items = conn.execute(
        """SELECT pi.id, d.name AS item_name, pi.quantity, pi.price,
                  pi.category, pi.issue_date, pi.status, pi.notes,
                  d.is_inventory, d.unit_of_measure
           FROM personnel_items pi
           JOIN item_dictionary d ON pi.item_id = d.id
           WHERE pi.personnel_id = ?
           ORDER BY pi.status, d.name""",
        (person_id,)
    ).fetchall()

    # Підрахунок суми
    total_sum = sum(
        r["quantity"] * r["price"]
        for r in items
        if r["status"] == "active" and r["price"]
    )

    # ── Картка речового майна (матрична) ──
    # Рядки = стандартні назви норм (через item_dictionary.norm_dict_id)
    # Колонки = документи видачі (накладна або РВ), відсортовані за датою
    property_card = _build_property_card(conn, person_id)

    # Групи для кнопки "архівувати"
    archive_reasons = ARCHIVE_REASONS

    conn.close()

    return render_template(
        "personnel/card.html",
        person=dict(person),
        items=items,
        total_sum=total_sum,
        archive_reasons=archive_reasons,
        property_card=property_card,
    )


def _build_property_card(conn, person_id: int) -> dict:
    """
    Будує дані для матричної картки речового майна.
    Рядки = всі позиції норми особи (навіть без видачі) + видане без норми.
    Кольорова логіка:
      green  — видано >= норми І строк носіння ще не вийшов (> 1 міс залишилось)
      yellow — видано >= норми АЛЕ строк носіння закінчується (<=1 міс) або вже вийшов
      orange — видано часткову кількість
      red    — не видавалось взагалі
    """
    from datetime import date, timedelta
    today = date.today()

    # Категорія особи
    person_row = conn.execute(
        "SELECT category, norm_id FROM personnel WHERE id=?", (person_id,)
    ).fetchone()
    is_officer = person_row and person_row["category"] == "officer"
    norm_id = person_row["norm_id"] if person_row else None

    # --- Рядки норми (supply_norm_items) ---
    norm_rows = {}  # nd_id -> dict
    if norm_id:
        sni_rows = conn.execute("""
            SELECT sni.norm_dict_id, sni.quantity AS norm_qty,
                   sni.wear_years, sni.category AS norm_cat,
                   nd.name AS nd_name, nd.unit AS nd_unit,
                   ndg.name AS nd_group_name,
                   ndg.sort_order AS g_order,
                   nd.sort_order AS nd_order
            FROM supply_norm_items sni
            JOIN norm_dictionary nd ON sni.norm_dict_id = nd.id
            LEFT JOIN norm_dict_groups ndg ON nd.group_id = ndg.id
            WHERE sni.norm_id = ?
            ORDER BY ndg.sort_order, nd.sort_order
        """, (norm_id,)).fetchall()
        for r in sni_rows:
            nd_id = r["norm_dict_id"]
            norm_rows[nd_id] = {
                "norm_id":    nd_id,
                "norm_name":  r["nd_name"],
                "group_name": r["nd_group_name"] or "Без групи",
                "g_order":    r["g_order"] or 999,
                "nd_order":   r["nd_order"] or 999,
                "unit":       r["nd_unit"] or "шт",
                "norm_qty":   r["norm_qty"] or 0,
                "wear_years": r["wear_years"] or 0,
                "cells": {},
                "total_qty": 0.0,
                "total_active": 0.0,
                "last_issue_date": None,
                "color": "red",  # буде перераховано
                "from_norm": True,
            }

    # --- Фактичне майно особи ---
    raw = conn.execute("""
        SELECT pi.id, pi.quantity, pi.price, pi.issue_date, pi.status,
               pi.invoice_id, pi.sheet_id, pi.source_type,
               d.unit_of_measure,
               nd.id AS nd_id, nd.name AS nd_name,
               ndg.name AS nd_group_name,
               ndg.sort_order AS g_order, nd.sort_order AS nd_order
        FROM personnel_items pi
        JOIN item_dictionary d ON pi.item_id = d.id
        LEFT JOIN norm_dictionary nd ON d.norm_dict_id = nd.id
        LEFT JOIN norm_dict_groups ndg ON nd.group_id = ndg.id
        WHERE pi.personnel_id = ?
        ORDER BY ndg.sort_order NULLS LAST, nd.sort_order NULLS LAST, pi.issue_date
    """, (person_id,)).fetchall()

    # Збираємо унікальні документи-колонки
    docs_map = {}
    for r in raw:
        if r["invoice_id"]:
            key = f"inv_{r['invoice_id']}"
            if key not in docs_map:
                inv = conn.execute(
                    "SELECT number, issue_date, created_at FROM invoices WHERE id=?",
                    (r["invoice_id"],)
                ).fetchone()
                if inv:
                    docs_map[key] = {
                        "key": key, "id": r["invoice_id"],
                        "label": inv["number"] or f"Накл.{r['invoice_id']}",
                        "date": inv["issue_date"] or inv["created_at"][:10],
                        "type": "invoice",
                    }
        elif r["sheet_id"]:
            key = f"rv_{r['sheet_id']}"
            if key not in docs_map:
                rv = conn.execute(
                    "SELECT number, created_at FROM distribution_sheets WHERE id=?",
                    (r["sheet_id"],)
                ).fetchone()
                if rv:
                    docs_map[key] = {
                        "key": key, "id": r["sheet_id"],
                        "label": rv["number"] or f"РВ.{r['sheet_id']}",
                        "date": rv["created_at"][:10],
                        "type": "rv",
                    }
        else:
            src = r["source_type"] or "manual"
            key = f"manual_{r['id']}"
            docs_map[key] = {
                "key": key, "id": None,
                "label": r["issue_date"] or src,
                "date": r["issue_date"] or "",
                "type": src,
            }

    docs = sorted(docs_map.values(), key=lambda d: d["date"] or "")

    # Будуємо карту nd_id -> row (з норми або додаємо "без норми")
    norms_map = {}
    # Спочатку копіюємо з норми
    for nd_id, nr in norm_rows.items():
        norms_map[nd_id] = nr

    # Додаємо видачі
    for r in raw:
        nd_id = r["nd_id"]

        # Якщо норми нема — показуємо під "Без норми"
        if not nd_id:
            key = f"no_norm_{r['id']}"
            norms_map[key] = norms_map.get(key, {
                "norm_id": key, "norm_name": "— (без прив'язки до норми)",
                "group_name": "Без норми", "g_order": 9999, "nd_order": 9999,
                "unit": r["unit_of_measure"] or "шт",
                "norm_qty": 0, "wear_years": 0,
                "cells": {}, "total_qty": 0.0, "total_active": 0.0,
                "last_issue_date": None, "color": "grey", "from_norm": False,
            })
            nd_id = key

        if nd_id not in norms_map:
            # Видача є, але норма не в supply_norm_items — додаємо рядок
            norms_map[nd_id] = {
                "norm_id": nd_id, "norm_name": r["nd_name"] or "—",
                "group_name": r["nd_group_name"] or "Без групи",
                "g_order": r["g_order"] or 999, "nd_order": r["nd_order"] or 999,
                "unit": r["unit_of_measure"] or "шт",
                "norm_qty": 0, "wear_years": 0,
                "cells": {}, "total_qty": 0.0, "total_active": 0.0,
                "last_issue_date": None, "color": "grey", "from_norm": False,
            }

        # Ключ документа
        if r["invoice_id"] and f"inv_{r['invoice_id']}" in docs_map:
            doc_key = f"inv_{r['invoice_id']}"
        elif r["sheet_id"] and f"rv_{r['sheet_id']}" in docs_map:
            doc_key = f"rv_{r['sheet_id']}"
        else:
            doc_key = f"manual_{r['id']}"

        row = norms_map[nd_id]
        if doc_key not in row["cells"]:
            row["cells"][doc_key] = {"qty": 0.0, "date": r["issue_date"] or "", "status": r["status"]}
        row["cells"][doc_key]["qty"] += r["quantity"] or 0
        row["total_qty"] += r["quantity"] or 0
        if r["status"] == "active":
            row["total_active"] += r["quantity"] or 0
            if r["issue_date"] and (not row["last_issue_date"] or r["issue_date"] > row["last_issue_date"]):
                row["last_issue_date"] = r["issue_date"]

    # Розраховуємо колір для кожного рядка
    for row in norms_map.values():
        if not row["from_norm"]:
            row["color"] = "grey"
            continue
        norm_qty   = row["norm_qty"] or 0
        wear_years = row["wear_years"] or 0
        active     = row["total_active"]
        last_date  = row["last_issue_date"]

        if active <= 0:
            row["color"] = "red"
        elif norm_qty > 0 and active < norm_qty:
            row["color"] = "orange"
        else:
            # Видано норму або більше — перевіряємо строк носіння
            if wear_years <= 0 or not last_date:
                # до зносу або дата невідома — вважаємо зеленим
                row["color"] = "green"
            else:
                # Коли закінчується строк
                try:
                    issue_dt = datetime.strptime(last_date, "%Y-%m-%d").date()
                    wear_days = int(wear_years * 365.25)
                    expiry_dt = issue_dt + timedelta(days=wear_days)
                    days_left = (expiry_dt - today).days
                    if days_left < 0:
                        row["color"] = "yellow"   # строк вийшов — потребує заміни
                    elif days_left <= 30:
                        row["color"] = "yellow"   # залишилось < 1 міс
                    else:
                        row["color"] = "green"
                except Exception:
                    row["color"] = "green"

        row["days_left"] = None
        if wear_years > 0 and last_date:
            try:
                issue_dt  = datetime.strptime(last_date, "%Y-%m-%d").date()
                wear_days = int(wear_years * 365.25)
                expiry_dt = issue_dt + timedelta(days=wear_days)
                row["days_left"] = (expiry_dt - today).days
                row["expiry_date"] = expiry_dt.isoformat()
            except Exception:
                pass

    rows = sorted(norms_map.values(), key=lambda x: (x["g_order"], x["nd_order"]))
    return {"docs": docs, "rows": rows}


# ─────────────────────────────────────────────────────────────
#  Редагування
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/edit", methods=["GET", "POST"])
@login_required
def edit(person_id):
    conn = get_connection()

    person_row = conn.execute(
        "SELECT * FROM personnel WHERE id = ?", (person_id,)
    ).fetchone()

    if not person_row:
        conn.close()
        return "Особу не знайдено", 404

    if request.method == "POST":
        old_data = dict(person_row)
        data = _collect_form()
        errors = _validate_form(data, conn, exclude_id=person_id)

        if errors:
            groups     = _get_all_groups(conn)
            battalions = _get_all_battalions(conn)
            units      = _get_units_by_battalion(conn, data.get("battalion_id")) if data.get("battalion_id") else []
            platoons   = _get_platoons_by_unit(conn, data.get("unit_id")) if data.get("unit_id") else []
            supply_norms = conn.execute(
                "SELECT id, name FROM supply_norms WHERE is_active=1 ORDER BY name"
            ).fetchall()
            conn.close()
            return render_template(
                "personnel/form.html",
                person=data, errors=errors,
                groups=groups, battalions=battalions,
                units=units, platoons=platoons,
                ranks=_get_ranks(), is_edit=True,
                person_id=person_id,
                supply_norms=supply_norms,
            )

        conn.execute(
            """UPDATE personnel SET
               last_name=?, first_name=?, middle_name=?,
               rank=?, position=?, category=?,
               battalion_id=?, unit_id=?, platoon_id=?, group_id=?,
               ipn=?, card_number=?, phone=?,
               size_head=?, size_height=?, size_underwear=?, size_suit=?,
               size_jacket=?, size_pants=?, size_shoes=?,
               enroll_date=?, enroll_order=?, dismiss_date=?, dismiss_order=?,
               draft_date=?, draft_by=?, norm_id=?,
               updated_at=datetime('now','localtime')
               WHERE id=?""",
            (
                data["last_name"], data["first_name"], data["middle_name"],
                data["rank"], data["position"], data["category"],
                data["battalion_id"], data["unit_id"], data["platoon_id"], data["group_id"],
                data["ipn"], data["card_number"], data["phone"],
                data["size_head"], data["size_height"], data["size_underwear"], data["size_suit"],
                data["size_jacket"], data["size_pants"], data["size_shoes"],
                data["enroll_date"], data["enroll_order"],
                data["dismiss_date"], data["dismiss_order"],
                data["draft_date"], data["draft_by"], data["norm_id"],
                person_id,
            )
        )
        conn.commit()
        log_action("edit", "personnel", person_id, old_data, data)
        conn.close()
        emit("personnel.updated", person_id=person_id, old=old_data, new=data)
        return redirect(url_for("personnel.card", person_id=person_id))

    # GET — заповнити форму поточними даними
    person = dict(person_row)
    groups     = _get_all_groups(conn)
    battalions = _get_all_battalions(conn)
    units      = _get_units_by_battalion(conn, person.get("battalion_id")) if person.get("battalion_id") else []
    platoons   = _get_platoons_by_unit(conn, person.get("unit_id")) if person.get("unit_id") else []
    supply_norms = conn.execute(
        "SELECT id, name FROM supply_norms WHERE is_active=1 ORDER BY name"
    ).fetchall()
    conn.close()

    return render_template(
        "personnel/form.html",
        person=person, errors={},
        groups=groups, battalions=battalions,
        units=units, platoons=platoons,
        ranks=_get_ranks(), is_edit=True,
        person_id=person_id,
        supply_norms=supply_norms,
    )


# ─────────────────────────────────────────────────────────────
#  Архівування
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/archive", methods=["POST"])
@login_required
def archive(person_id):
    conn = get_connection()

    person = conn.execute(
        "SELECT id, last_name, first_name, is_active FROM personnel WHERE id = ?",
        (person_id,)
    ).fetchone()

    if not person:
        conn.close()
        return jsonify({"error": "Особу не знайдено"}), 404

    if not person["is_active"]:
        conn.close()
        return jsonify({"error": "Особа вже заархівована"}), 400

    # Перевірка інвентарного майна
    inv_items = _active_inventory_items(conn, person_id)
    if inv_items:
        conn.close()
        return jsonify({
            "error": "Є не здане інвентарне майно",
            "items": inv_items,
        }), 409

    body = request.get_json(silent=True) or {}
    reason = body.get("reason", "").strip()
    note   = body.get("note", "").strip()
    full_reason = reason
    if note:
        full_reason = f"{reason}: {note}" if reason else note

    dismissed_gid = _get_dismissed_group_id(conn)

    conn.execute(
        """UPDATE personnel SET
           is_active = 0,
           archived_at = datetime('now','localtime'),
           archive_reason = ?,
           group_id = ?,
           updated_at = datetime('now','localtime')
           WHERE id = ?""",
        (full_reason, dismissed_gid, person_id)
    )
    conn.commit()
    log_action("status_change", "personnel", person_id,
               {"is_active": 1},
               {"is_active": 0, "archive_reason": full_reason})
    conn.close()
    emit("personnel.archived", person_id=person_id, reason=full_reason)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  Відновлення з архіву
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/restore", methods=["POST"])
@login_required
def restore(person_id):
    conn = get_connection()

    person = conn.execute(
        "SELECT id, is_active FROM personnel WHERE id = ?", (person_id,)
    ).fetchone()

    if not person:
        conn.close()
        return jsonify({"error": "Особу не знайдено"}), 404

    if person["is_active"]:
        conn.close()
        return jsonify({"error": "Особа вже активна"}), 400

    # Знайти групу "БЕЗ ГРУПИ"
    no_group = conn.execute(
        "SELECT id FROM groups WHERE type = 'no_group' LIMIT 1"
    ).fetchone()
    no_group_id = no_group["id"] if no_group else None

    conn.execute(
        """UPDATE personnel SET
           is_active = 1,
           archived_at = NULL,
           archive_reason = NULL,
           group_id = ?,
           updated_at = datetime('now','localtime')
           WHERE id = ?""",
        (no_group_id, person_id)
    )
    conn.commit()
    log_action("status_change", "personnel", person_id,
               {"is_active": 0}, {"is_active": 1})
    conn.close()
    emit("personnel.restored", person_id=person_id)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  Масове переміщення
# ─────────────────────────────────────────────────────────────

@bp.route("/move", methods=["POST"])
@login_required
def move():
    body = request.get_json(silent=True) or {}
    ids      = body.get("ids", [])
    group_id = body.get("group_id") or None
    unit_id  = body.get("unit_id")  or None
    platoon_id = body.get("platoon_id") or None

    if not ids:
        return jsonify({"error": "Не обрано жодної особи"}), 400

    if not isinstance(ids, list):
        return jsonify({"error": "ids має бути масивом"}), 400

    # Безпечний список id (тільки int)
    try:
        ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        return jsonify({"error": "Невірний формат ids"}), 400

    conn = get_connection()

    # Будуємо SET-частину динамічно — оновлюємо тільки передані поля
    set_parts = ["updated_at = datetime('now','localtime')"]
    set_params = []

    if group_id is not None:
        set_parts.append("group_id = ?")
        set_params.append(int(group_id))

    if unit_id is not None:
        set_parts.append("unit_id = ?")
        set_params.append(int(unit_id))

        # При зміні підрозділу — шукаємо батальйон
        bat = conn.execute(
            "SELECT battalion_id FROM units WHERE id = ?", (int(unit_id),)
        ).fetchone()
        if bat:
            set_parts.append("battalion_id = ?")
            set_params.append(bat["battalion_id"])

        # Скидаємо взвод якщо не переданий
        if platoon_id is None:
            set_parts.append("platoon_id = NULL")
        else:
            set_parts.append("platoon_id = ?")
            set_params.append(int(platoon_id))

    placeholders = ",".join(["?"] * len(ids))
    sql = f"UPDATE personnel SET {', '.join(set_parts)} WHERE id IN ({placeholders})"
    conn.execute(sql, set_params + ids)
    conn.commit()

    log_action("move", "personnel", None,
               None,
               {"ids": ids, "group_id": group_id, "unit_id": unit_id, "platoon_id": platoon_id})
    conn.close()
    emit("personnel.moved", person_ids=ids, group_id=group_id,
         unit_id=unit_id, platoon_id=platoon_id)
    return jsonify({"ok": True, "count": len(ids)})


# ─────────────────────────────────────────────────────────────
#  API — динамічне завантаження підрозділів і взводів
# ─────────────────────────────────────────────────────────────

@bp.route("/api/units")
@login_required
def api_units():
    battalion_id = request.args.get("battalion_id", type=int)
    if not battalion_id:
        return jsonify([])
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name FROM units WHERE battalion_id = ? ORDER BY name",
        (battalion_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route("/api/platoons")
@login_required
def api_platoons():
    unit_id = request.args.get("unit_id", type=int)
    if not unit_id:
        return jsonify([])
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name FROM platoons WHERE unit_id = ? ORDER BY name",
        (unit_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route("/api/ranks")
@login_required
def api_ranks():
    """
    JSON список звань з category для автовизначення officer/soldier.
    Використовується в формі: при виборі звання — автоматично виставляти category.
    """
    return jsonify(_get_ranks())


@bp.route("/api/list")
@login_required
def api_list():
    """JSON для автодоповнення / пошуку в інших модулях."""
    q        = request.args.get("q", "").strip()
    unit_id  = request.args.get("unit_id", type=int)
    active   = request.args.get("active", "1")
    limit    = request.args.get("limit", 50, type=int)

    where_parts = []
    params = []

    if active == "1":
        where_parts.append("p.is_active = 1")

    if unit_id:
        where_parts.append("p.unit_id = ?")
        params.append(unit_id)

    if q:
        where_parts.append(
            "(p.last_name LIKE ? OR p.first_name LIKE ? OR p.middle_name LIKE ? OR p.ipn LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])

    where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

    conn = get_connection()
    rows = conn.execute(
        f"""SELECT p.id,
                   p.last_name || ' ' || p.first_name || COALESCE(' ' || p.middle_name, '') AS full_name,
                   p.rank, p.position,
                   u.name AS unit_name
            FROM personnel p
            LEFT JOIN units u ON p.unit_id = u.id
            {where_sql}
            ORDER BY p.last_name, p.first_name
            LIMIT ?""",
        params + [limit]
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ─────────────────────────────────────────────────────────────
#  Фото
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/photo", methods=["POST"])
@login_required
def upload_photo(person_id):
    """Завантаження фото військовослужбовця (jpg/png, max 5MB)."""
    import os
    from werkzeug.utils import secure_filename
    from flask import current_app

    conn = get_connection()
    person = conn.execute("SELECT id, last_name, first_name FROM personnel WHERE id=?", (person_id,)).fetchone()
    if not person:
        conn.close()
        return jsonify({"error": "Особу не знайдено"}), 404

    file = request.files.get("photo")
    if not file or not file.filename:
        conn.close()
        return jsonify({"error": "Файл не обрано"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        conn.close()
        return jsonify({"error": "Дозволені формати: jpg, jpeg, png"}), 400

    # Зберігаємо поруч з exe / в папці проекту
    from pathlib import Path
    storage = Path(current_app.root_path) / "storage" / "photos"
    storage.mkdir(parents=True, exist_ok=True)

    filename = f"personnel_{person_id}{ext}"
    filepath = storage / filename

    # Видалити старе фото якщо інший ext
    for old_ext in (".jpg", ".jpeg", ".png"):
        old = storage / f"personnel_{person_id}{old_ext}"
        if old.exists() and old != filepath:
            old.unlink(missing_ok=True)

    file.save(str(filepath))

    # Зберігаємо відносний шлях
    rel_path = f"/storage/photos/{filename}"
    conn.execute(
        "UPDATE personnel SET photo_path=?, updated_at=datetime('now','localtime') WHERE id=?",
        (rel_path, person_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "photo_url": rel_path})


@bp.route("/<int:person_id>/photo/delete", methods=["POST"])
@login_required
def delete_photo(person_id):
    """Видалення фото."""
    import os
    from pathlib import Path
    from flask import current_app

    conn = get_connection()
    person = conn.execute("SELECT photo_path FROM personnel WHERE id=?", (person_id,)).fetchone()
    if not person:
        conn.close()
        return jsonify({"error": "Особу не знайдено"}), 404

    if person["photo_path"]:
        full = Path(current_app.root_path) / person["photo_path"].lstrip("/")
        if full.exists():
            full.unlink(missing_ok=True)

    conn.execute(
        "UPDATE personnel SET photo_path=NULL, updated_at=datetime('now','localtime') WHERE id=?",
        (person_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  Документи картки (накладні де особа є отримувачем)
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/documents")
@login_required
def api_documents(person_id):
    """
    API: накладні та РВ де ця особа є отримувачем.
    Повертає JSON для вкладки "Документи" в картці.
    """
    conn = get_connection()
    invoices = conn.execute(
        """SELECT i.id, i.number, i.status, i.direction,
                  i.total_sum, i.created_at
           FROM invoices i
           WHERE i.recipient_personnel_id=? OR i.sender_personnel_id=?
           ORDER BY i.created_at DESC, i.id DESC
           LIMIT 100""",
        (person_id, person_id)
    ).fetchall()

    rv_sheets = conn.execute(
        """SELECT ds.id, ds.number, ds.doc_date, ds.status, ds.total_sum
           FROM distribution_sheets ds
           JOIN distribution_sheet_rows dsr ON dsr.sheet_id=ds.id
           WHERE dsr.personnel_id=?
           ORDER BY ds.doc_date DESC, ds.id DESC
           LIMIT 50""",
        (person_id,)
    ).fetchall()

    conn.close()
    return jsonify({
        "invoices": [dict(r) for r in invoices],
        "rv_sheets": [dict(r) for r in rv_sheets],
    })


# ─────────────────────────────────────────────────────────────
#  Речовий атестат
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/attestat")
@login_required
def attestat(person_id):
    """Речовий атестат — перегляд і друк."""
    from datetime import date
    from collections import OrderedDict
    conn = get_connection()

    person = conn.execute(
        """SELECT p.*,
                  b.name AS battalion_name,
                  u.name AS unit_name,
                  pl.name AS platoon_name,
                  g.name AS group_name,
                  g.type AS group_type
           FROM personnel p
           LEFT JOIN battalions b  ON p.battalion_id = b.id
           LEFT JOIN units      u  ON p.unit_id      = u.id
           LEFT JOIN platoons   pl ON p.platoon_id   = pl.id
           LEFT JOIN groups     g  ON p.group_id     = g.id
           WHERE p.id = ?""",
        (person_id,)
    ).fetchone()

    if not person:
        conn.close()
        return "Особу не знайдено", 404

    from core.settings import get_setting
    person = dict(person)
    norm_id = person.get("norm_id")

    # ── 1. Позиції норми (якщо призначена) ──────────────────────
    group_order = {}
    positions_map = OrderedDict()
    pos_group = {}
    pos_order = {}
    seq = [0]

    if norm_id:
        norm_rows = conn.execute(
            """SELECT sni.norm_dict_id AS nd_id, sni.quantity AS norm_qty,
                      nd.name AS norm_name, nd.sort_order AS nd_order,
                      nd.unit_of_measure AS nd_uom,
                      ndg.name AS group_name, ndg.sort_order AS g_order
               FROM supply_norm_items sni
               JOIN norm_dictionary nd ON nd.id = sni.norm_dict_id
               LEFT JOIN norm_dict_groups ndg ON nd.group_id = ndg.id
               WHERE sni.norm_id = ?
               ORDER BY ndg.sort_order NULLS LAST, nd.sort_order NULLS LAST, nd.name""",
            (norm_id,)
        ).fetchall()
        for r in norm_rows:
            nd_id  = r["nd_id"]
            gname  = r["group_name"] or "Інше"
            go     = r["g_order"] if r["g_order"] is not None else 9999
            if gname not in group_order:
                group_order[gname] = go
            seq[0] += 1
            positions_map[nd_id] = {
                "seq":       seq[0],
                "name":      r["norm_name"],
                "unit":      r["nd_uom"] or "шт",
                "norm_qty":  r["norm_qty"] or 0,
                "issuances": [],
                "total_qty": 0.0,
                "total_sum": 0.0,
            }
            pos_group[nd_id] = gname
            pos_order[nd_id] = (go, r["nd_order"] if r["nd_order"] is not None else 9999)

    # ── 2. Реальні видачі ──────────────────────────────────────
    rows = conn.execute(
        """SELECT pi.id, pi.quantity, pi.price, pi.issue_date, pi.status, pi.category,
                  pi.notes, pi.source_type,
                  d.name AS item_name, d.unit_of_measure,
                  nd.id AS nd_id, nd.name AS norm_name,
                  ndg.name AS group_name, ndg.sort_order AS g_order,
                  nd.sort_order AS nd_order
           FROM personnel_items pi
           JOIN item_dictionary d ON pi.item_id = d.id
           LEFT JOIN norm_dictionary nd ON d.norm_dict_id = nd.id
           LEFT JOIN norm_dict_groups ndg ON nd.group_id = ndg.id
           WHERE pi.personnel_id = ?
             AND pi.status = 'active'
           ORDER BY ndg.sort_order NULLS LAST, nd.sort_order NULLS LAST, d.name, pi.issue_date""",
        (person_id,)
    ).fetchall()

    for r in rows:
        nd_id = r["nd_id"]
        # Ключ: якщо є nd_id і він вже в нормі — використовуємо його
        # інакше — новий ключ для позаномних позицій
        key = nd_id if nd_id else f"no_norm_{r['item_name']}"

        if key not in positions_map:
            gname  = r["group_name"] or "Інше"
            go     = r["g_order"] if r["g_order"] is not None else 9999
            if gname not in group_order:
                group_order[gname] = go
            seq[0] += 1
            positions_map[key] = {
                "seq":       seq[0],
                "name":      r["norm_name"] or r["item_name"],
                "unit":      r["unit_of_measure"] or "шт",
                "norm_qty":  0,
                "issuances": [],
                "total_qty": 0.0,
                "total_sum": 0.0,
            }
            pos_group[key] = gname
            pos_order[key] = (go, r["nd_order"] if r["nd_order"] is not None else 9999)

        qty   = r["quantity"] or 0
        price = r["price"] or 0
        positions_map[key]["issuances"].append({
            "date":     r["issue_date"] or "",
            "qty":      qty,
            "price":    price,
            "category": r["category"] or "",
            "notes":    r["notes"] or "",
        })
        positions_map[key]["total_qty"] += qty
        positions_map[key]["total_sum"] += qty * price

    # Визначаємо max_cols (макс кількість видач для будь-якої позиції)
    max_issuances = max((len(p["issuances"]) for p in positions_map.values()), default=1)

    # Будуємо впорядкований список груп
    sorted_groups = sorted(group_order.keys(), key=lambda g: group_order[g])

    # Кожна група -> список позицій в правильному порядку
    groups_out = OrderedDict()
    for gname in sorted_groups:
        keys_in_group = [k for k, g in pos_group.items() if g == gname]
        keys_in_group.sort(key=lambda k: pos_order[k])
        groups_out[gname] = [positions_map[k] for k in keys_in_group]

    total_sum = sum(p["total_sum"] for p in positions_map.values())
    has_norm = bool(norm_id)

    settings = {
        "unit_name":      get_setting("company_name", ""),
        "chief_name":     get_setting("chief_name", ""),
        "chief_rank":     get_setting("chief_rank", ""),
        "chief_is_tvo":   get_setting("chief_is_tvo", "0"),
        "chief_tvo_name": get_setting("chief_tvo_name", ""),
        "chief_tvo_rank": get_setting("chief_tvo_rank", ""),
        "clerk_name":     get_setting("clerk_name", ""),
        "clerk_rank":     get_setting("clerk_rank", ""),
    }

    conn.close()

    return render_template(
        "personnel/attestat.html",
        person=person,
        groups=groups_out,
        total_sum=total_sum,
        max_issuances=max_issuances,
        settings=settings,
        has_norm=has_norm,
        today=date.today().strftime("%d.%m.%Y"),
    )


# ─────────────────────────────────────────────────────────────
#  Прийом майна з атестату (AJAX)
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/attestat-import", methods=["GET", "POST"])
@login_required
def attestat_import(person_id):
    """
    GET  — сторінка форми внесення майна з атестату.
    POST — зберегти рядки (JSON array).
    """
    conn = get_connection()
    person = conn.execute(
        "SELECT * FROM personnel WHERE id=?", (person_id,)
    ).fetchone()
    if not person:
        conn.close()
        flash("Особу не знайдено", "error")
        return redirect(url_for("personnel.index"))

    if request.method == "POST":
        rows = request.get_json(silent=True) or []
        if not rows:
            conn.close()
            return jsonify({"ok": False, "error": "Немає рядків для збереження"}), 400

        today = __import__("datetime").date.today().isoformat()
        saved = 0
        errors = []
        for i, row in enumerate(rows):
            item_id   = row.get("item_id")
            quantity  = float(row.get("quantity") or 1)
            price     = float(row.get("price") or 0)
            category  = row.get("category") or "I"
            issue_date = row.get("issue_date") or today
            notes     = (row.get("notes") or "").strip() or None

            if not item_id:
                errors.append(f"Рядок {i+1}: не вибрано найменування")
                continue

            # Перевірка що item_id існує
            item = conn.execute("SELECT id FROM item_dictionary WHERE id=?", (item_id,)).fetchone()
            if not item:
                errors.append(f"Рядок {i+1}: невідоме майно (id={item_id})")
                continue

            conn.execute("""
                INSERT INTO personnel_items
                    (personnel_id, item_id, quantity, price, category,
                     source_type, issue_date, wear_started_date, status,
                     notes, created_at, updated_at)
                VALUES (?,?,?,?,?,
                        'attestat_import',?,?,  'active',
                        ?,datetime('now','localtime'),datetime('now','localtime'))
            """, (person_id, item_id, quantity, price, category,
                  issue_date, issue_date, notes))
            saved += 1

        if saved:
            conn.commit()
            log_action("add", "personnel_items", person_id,
                       new_data={"source": "attestat_import", "count": saved})

        conn.close()
        return jsonify({"ok": True, "saved": saved, "errors": errors})

    # GET — форма
    from datetime import date
    item_dict = conn.execute("""
        SELECT id, name, unit_of_measure,
               norm_dict_id,
               (SELECT nd.name FROM norm_dictionary nd WHERE nd.id = item_dictionary.norm_dict_id) AS norm_dict_name
        FROM item_dictionary
        ORDER BY name
    """).fetchall()

    # Вже внесені записи з атестату (для відображення і редагування)
    existing = conn.execute("""
        SELECT pi.id, pi.quantity, pi.price, pi.category,
               pi.issue_date, pi.notes,
               pi.item_id,
               d.name AS item_name, d.unit_of_measure
        FROM personnel_items pi
        JOIN item_dictionary d ON pi.item_id = d.id
        WHERE pi.personnel_id = ?
          AND pi.source_type = 'attestat_import'
          AND pi.status = 'active'
        ORDER BY pi.issue_date, d.name
    """, (person_id,)).fetchall()
    existing = [dict(r) for r in existing]

    conn.close()

    return render_template("personnel/attestat_import.html",
                           person=person, item_dict=item_dict,
                           existing=existing,
                           today=date.today().isoformat())


@bp.route("/<int:person_id>/attestat-import/<int:item_pi_id>/delete", methods=["POST"])
@login_required
def attestat_import_delete(person_id, item_pi_id):
    """Видалити один запис з атестату (AJAX)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM personnel_items WHERE id=? AND personnel_id=? AND source_type='attestat_import'",
        (item_pi_id, person_id)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "error": "Запис не знайдено"}), 404
    conn.execute("DELETE FROM personnel_items WHERE id=?", (item_pi_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  Утиліти форми
# ─────────────────────────────────────────────────────────────

def _collect_form() -> dict:
    """Зібрати дані з POST-форми."""
    def _str(key):
        v = request.form.get(key, "").strip()
        return v if v else None

    def _int(key):
        v = request.form.get(key, "").strip()
        return int(v) if v else None

    return {
        "last_name":      request.form.get("last_name", "").strip(),
        "first_name":     request.form.get("first_name", "").strip(),
        "middle_name":    _str("middle_name"),
        "rank":           _str("rank"),
        "position":       _str("position"),
        "category":       request.form.get("category", "soldier"),
        "battalion_id":   _int("battalion_id"),
        "unit_id":        _int("unit_id"),
        "platoon_id":     _int("platoon_id"),
        "group_id":       _int("group_id"),
        "ipn":            _str("ipn"),
        "card_number":    _str("card_number"),
        "phone":          _str("phone"),
        "size_head":      _str("size_head"),
        "size_height":    _str("size_height"),
        "size_underwear": _str("size_underwear"),
        "size_suit":      _str("size_suit"),
        "size_jacket":    _str("size_jacket"),
        "size_pants":     _str("size_pants"),
        "size_shoes":     _str("size_shoes"),
        "enroll_date":    _str("enroll_date"),
        "enroll_order":   _str("enroll_order"),
        "dismiss_date":   _str("dismiss_date"),
        "dismiss_order":  _str("dismiss_order"),
        "draft_date":     _str("draft_date"),
        "draft_by":       _str("draft_by"),
        "norm_id":        _int("norm_id"),
    }


def _validate_form(data: dict, conn, exclude_id: int | None = None) -> dict:
    """Валідація форми. Повертає dict помилок."""
    errors = {}

    if not data.get("last_name"):
        errors["last_name"] = "Прізвище обов'язкове"

    if not data.get("first_name"):
        errors["first_name"] = "Ім'я обов'язкове"

    # Перевірка унікальності ІПН
    if data.get("ipn"):
        q = "SELECT id FROM personnel WHERE ipn = ?"
        params = [data["ipn"]]
        if exclude_id:
            q += " AND id != ?"
            params.append(exclude_id)
        row = conn.execute(q, params).fetchone()
        if row:
            errors["ipn"] = "ІПН вже існує в базі"

    return errors
