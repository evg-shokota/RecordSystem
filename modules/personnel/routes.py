"""
modules/personnel/routes.py — маршрути модуля Особовий склад
Author: White
"""
import json
from datetime import datetime
from pathlib import Path
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, jsonify, session, flash, current_app
)
from core.auth import login_required
from core.db import get_connection
from core.audit import log_action
from core.hooks import emit, filter_value, collect
from core.warehouse import get_units_by_battalion as _get_units_by_battalion, get_platoons_by_unit as _get_platoons_by_unit

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
            JOIN personnel_norms pn ON pn.personnel_id = p.id
            JOIN supply_norm_items sni ON sni.norm_id = pn.norm_id
            LEFT JOIN supply_norm_item_wear sniw
                   ON sniw.norm_item_id = sni.id AND sniw.personnel_cat = pn.personnel_cat
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
                -- Строк носіння вийшов (wear_months > 0)
                (COALESCE(sniw.wear_months, CAST(sni.wear_years * 12 AS INT)) > 0 AND EXISTS (
                    SELECT 1 FROM personnel_items pi
                    JOIN item_dictionary d ON pi.item_id = d.id
                    WHERE pi.personnel_id = p.id
                      AND d.norm_dict_id = sni.norm_dict_id
                      AND pi.status = 'active'
                      AND pi.issue_date IS NOT NULL
                      AND date(pi.issue_date, '+' || CAST(
                              COALESCE(sniw.wear_months, CAST(sni.wear_years * 12 AS INT)) * 30
                          AS TEXT) || ' days') <= ?
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
                person_norms=[],
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
                size_jacket, size_pants, size_shoes, size_vest,
                enroll_date, enroll_order, dismiss_date, dismiss_order,
                draft_date, draft_by, norm_id, service_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["last_name"], data["first_name"], data["middle_name"],
                data["rank"], data["position"], data["category"],
                data["battalion_id"], data["unit_id"], data["platoon_id"], data["group_id"],
                data["ipn"], data["card_number"], data["phone"],
                data["size_head"], data["size_height"], data["size_underwear"], data["size_suit"],
                data["size_jacket"], data["size_pants"], data["size_shoes"], data["size_vest"],
                data["enroll_date"], data["enroll_order"],
                data["dismiss_date"], data["dismiss_order"],
                data["draft_date"], data["draft_by"], data["norm_id"],
                data["service_type"],
            )
        )
        new_id = cur.lastrowid
        # Якщо обрана норма — додати в personnel_norms
        if data["norm_id"]:
            norm_cat = int(request.form.get("norm_cat") or 1)
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO personnel_norms
                       (personnel_id, norm_id, personnel_cat, created_at)
                       VALUES (?, ?, ?, datetime('now','localtime'))""",
                    (new_id, data["norm_id"], norm_cat)
                )
            except Exception:
                pass
        conn.commit()
        log_action("add", "personnel", new_id, None, data)
        conn.close()
        emit("personnel.created", person_id=new_id, data=data)
        return redirect(url_for("personnel.card", person_id=new_id))

    # GET
    from core.settings import get_setting
    groups     = _get_all_groups(conn)
    battalions = _get_all_battalions(conn)
    default_group_id = _get_default_active_group_id(conn)
    supply_norms = conn.execute(
        "SELECT id, name FROM supply_norms WHERE is_active=1 ORDER BY name"
    ).fetchall()
    default_service_type = get_setting("default_service_type", "mobilized")
    conn.close()

    return render_template(
        "personnel/form.html",
        person={"group_id": default_group_id}, errors={},
        groups=groups, battalions=battalions,
        units=[], platoons=[],
        ranks=_get_ranks(), is_edit=False,
        supply_norms=supply_norms,
        person_norms=[],
        default_service_type=default_service_type,
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

    # Insignia для звання (з rank_presets)
    from core.settings import get_setting as _get_setting
    rank_mode = _get_setting("rank_mode", "army")
    rank_row = conn.execute(
        "SELECT insignia, category FROM rank_presets WHERE name=? AND mode=? LIMIT 1",
        (dict(person).get("rank") or "", rank_mode)
    ).fetchone()
    rank_insignia = rank_row["insignia"] if rank_row else ""
    rank_category = rank_row["category"] if rank_row else (dict(person).get("category") or "")

    # Норми особи
    person_norms = conn.execute(
        """SELECT pn.id, sn.name AS norm_name, sn.service_type AS norm_service_type, pn.personnel_cat
           FROM personnel_norms pn
           JOIN supply_norms sn ON sn.id = pn.norm_id
           WHERE pn.personnel_id = ?
           ORDER BY pn.id""",
        (person_id,)
    ).fetchall()

    conn.close()

    return render_template(
        "personnel/card.html",
        person=dict(person),
        items=items,
        total_sum=total_sum,
        archive_reasons=archive_reasons,
        property_card=property_card,
        rank_insignia=rank_insignia,
        rank_category=rank_category,
        person_norms=[dict(r) for r in person_norms],
    )


def _build_property_card(conn, person_id: int) -> dict:
    """
    Будує дані для матричної картки речового майна.
    Рядки = всі позиції норми особи (навіть без видачі) + видане без норми.
    Кольорова логіка через core/military_logic.py:
      green  — видано >= норми, строк не вийшов
      yellow — строк закінчується або вийшов
      orange — видано часткову кількість (борг)
      red    — не видавалось взагалі
    """
    from datetime import date, timedelta
    from core.military_logic import get_cycle_status
    today = date.today()

    # Категорія та тип служби особи
    person_row = conn.execute(
        "SELECT category, service_type, enroll_date FROM personnel WHERE id=?", (person_id,)
    ).fetchone()
    is_officer   = person_row and person_row["category"] == "officer"
    service_type = (person_row["service_type"] if person_row else None) or "mobilized"
    norm_date    = person_row["enroll_date"] if person_row else None

    # --- Норми особи (personnel_norms, може бути кілька) ---
    pn_rows = conn.execute("""
        SELECT pn.norm_id, pn.personnel_cat
        FROM personnel_norms pn
        WHERE pn.personnel_id = ?
    """, (person_id,)).fetchall()

    # --- Рядки норм (supply_norm_items для всіх норм) ---
    norm_rows = {}  # nd_id -> dict
    if pn_rows:
        # Для кожного nd_id збираємо мінімальний wear_months з усіх норм по категорії особи
        for pn in pn_rows:
            pn_norm_id = pn["norm_id"]
            pn_cat = pn["personnel_cat"]
            sni_rows = conn.execute("""
                SELECT sni.id AS sni_id, sni.norm_dict_id, sni.quantity AS norm_qty,
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
            """, (pn_norm_id,)).fetchall()
            for r in sni_rows:
                nd_id = r["norm_dict_id"]
                # Строк носіння і к-сть: з supply_norm_item_wear по категорії
                wear_row = conn.execute(
                    "SELECT wear_months, qty FROM supply_norm_item_wear WHERE norm_item_id=? AND personnel_cat=?",
                    (r["sni_id"], pn_cat)
                ).fetchone()
                if wear_row and wear_row["wear_months"] > 0:
                    wear_years_eff = wear_row["wear_months"] / 12.0
                else:
                    wear_years_eff = r["wear_years"] or 0
                # Якщо для категорії задана своя к-сть — використовуємо її
                cat_norm_qty = float(wear_row["qty"]) if wear_row and wear_row["qty"] is not None else None

                if nd_id not in norm_rows:
                    norm_rows[nd_id] = {
                        "norm_id":    nd_id,
                        "norm_name":  r["nd_name"],
                        "group_name": r["nd_group_name"] or "Без групи",
                        "g_order":    r["g_order"] or 999,
                        "nd_order":   r["nd_order"] or 999,
                        "unit":       r["nd_unit"] or "шт",
                        "norm_qty":   cat_norm_qty if cat_norm_qty is not None else (r["norm_qty"] or 0),
                        "wear_years": wear_years_eff,
                        "cells": {},
                        "total_qty": 0.0,
                        "total_active": 0.0,
                        "last_issue_date": None,
                        "color": "red",
                        "from_norm": True,
                    }
                else:
                    # Якщо позиція вже є з іншої норми — беремо мінімальний строк (більш суворий)
                    if wear_years_eff > 0:
                        existing_wear = norm_rows[nd_id]["wear_years"]
                        if existing_wear <= 0 or wear_years_eff < existing_wear:
                            norm_rows[nd_id]["wear_years"] = wear_years_eff
                    # Кількість — беремо максимальну (щедрішу норму), враховуємо cat_norm_qty
                    eff_qty = cat_norm_qty if cat_norm_qty is not None else (r["norm_qty"] or 0)
                    if eff_qty > norm_rows[nd_id]["norm_qty"]:
                        norm_rows[nd_id]["norm_qty"] = eff_qty

    # --- Фактичне майно особи ---
    raw = conn.execute("""
        SELECT pi.id, pi.quantity, pi.price, pi.issue_date, pi.status,
               pi.invoice_id, pi.sheet_id, pi.source_type,
               pi.source_doc_number, pi.source_doc_date,
               pi.income_doc_id,
               COALESCE(pi.source_doc_number, idoc.document_number) AS eff_doc_number,
               COALESCE(pi.source_doc_date, idoc.date)              AS eff_doc_date,
               d.unit_of_measure,
               nd.id AS nd_id, nd.name AS nd_name,
               ndg.name AS nd_group_name,
               ndg.sort_order AS g_order, nd.sort_order AS nd_order
        FROM personnel_items pi
        JOIN item_dictionary d ON pi.item_id = d.id
        LEFT JOIN income_docs idoc ON pi.income_doc_id = idoc.id
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
            if src == "attestat_import":
                # Кожен унікальний атестат — окрема колонка
                # Пріоритет: income_doc_id (новий підхід) → source_doc_number/date (legacy)
                if r["income_doc_id"]:
                    key = f"attestat_doc_{r['income_doc_id']}"
                    doc_num  = (r["eff_doc_number"] or "").strip()
                    doc_date = (r["eff_doc_date"] or "").strip()
                else:
                    doc_num  = (r["source_doc_number"] or "").strip()
                    doc_date = (r["source_doc_date"] or "").strip()
                    key = f"attestat_{doc_num}_{doc_date}" if (doc_num or doc_date) else "attestat"
                if key not in docs_map:
                    label = f"Атестат №{doc_num}" if doc_num else "Атестат"
                    docs_map[key] = {
                        "key": key, "id": r["income_doc_id"],
                        "label": label,
                        "date": doc_date or "",
                        "type": "attestat_import",
                    }
            else:
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
        elif (r["source_type"] or "") == "attestat_import":
            if r["income_doc_id"]:
                doc_key = f"attestat_doc_{r['income_doc_id']}"
            else:
                doc_num  = (r["source_doc_number"] or "").strip()
                doc_date = (r["source_doc_date"] or "").strip()
                doc_key = f"attestat_{doc_num}_{doc_date}" if (doc_num or doc_date) else "attestat"
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

    # Розраховуємо колір, дати і борг через military_logic
    for row in norms_map.values():
        if not row["from_norm"]:
            row["color"]       = "grey"
            row["days_left"]   = None
            row["expiry_date"] = None
            row["debt_qty"]    = 0.0
            continue

        from core.military_logic import wear_years_to_months
        wear_months = wear_years_to_months(row["wear_years"])
        cycle_start = row.get("last_issue_date")  # дата першої/останньої видачі

        cs = get_cycle_status(
            service_type  = service_type,
            cycle_start_date = cycle_start,
            norm_date     = norm_date,
            wear_months   = wear_months,
            issued_qty    = row["total_active"],
            norm_qty      = row["norm_qty"] or 0,
            today         = today,
        )
        row["color"]       = cs["color"]
        row["days_left"]   = cs["days_left"]
        row["expiry_date"] = cs["next_issue_date"]
        row["debt_qty"]    = cs["debt_qty"]

    rows = sorted(norms_map.values(), key=lambda x: (x["g_order"], x["nd_order"]))
    return {"docs": docs, "rows": rows}


# ─────────────────────────────────────────────────────────────
#  Картка речового майна (друк)
# ─────────────────────────────────────────────────────────────

def _count_words(n: int) -> str:
    """Ціле число від 0 до 999 прописом (українська, називний відмінок)."""
    if n == 0:
        return ''
    ones  = ['', 'один', 'два', 'три', 'чотири', "п'ять", 'шість', 'сім',
             'вісім', "дев'ять", 'десять', 'одинадцять', 'дванадцять',
             'тринадцять', 'чотирнадцять', "п'ятнадцять", 'шістнадцять',
             'сімнадцять', 'вісімнадцять', "дев'ятнадцять"]
    tens  = ['', '', 'двадцять', 'тридцять', 'сорок', "п'ятдесят",
             'шістдесят', 'сімдесят', 'вісімдесят', "дев'яносто"]
    hunds = ['', 'сто', 'двісті', 'триста', 'чотириста', "п'ятсот",
             'шістсот', 'сімсот', 'вісімсот', "дев'ятсот"]
    parts = []
    if n >= 100:
        parts.append(hunds[n // 100])
        n %= 100
    if n >= 20:
        parts.append(tens[n // 10])
        n %= 10
    if n > 0:
        parts.append(ones[n])
    return ' '.join(parts)


@bp.route("/<int:person_id>/property-card")
@login_required
def property_card_print(person_id):
    """Окрема сторінка-друк картки обліку речового майна."""
    conn = get_connection()

    person = conn.execute(
        """SELECT p.*,
                  b.name  AS battalion_name,
                  u.name  AS unit_name,
                  pl.name AS platoon_name
           FROM personnel p
           LEFT JOIN battalions   b  ON p.battalion_id = b.id
           LEFT JOIN units        u  ON p.unit_id      = u.id
           LEFT JOIN platoons     pl ON p.platoon_id   = pl.id
           WHERE p.id = ?""",
        (person_id,)
    ).fetchone()

    if not person:
        conn.close()
        return "Особу не знайдено", 404

    # Список норм особи з категоріями (для заголовку)
    person_norms_names = conn.execute("""
        SELECT sn.name, pn.personnel_cat FROM personnel_norms pn
        JOIN supply_norms sn ON sn.id = pn.norm_id
        WHERE pn.personnel_id = ? ORDER BY pn.id
    """, (person_id,)).fetchall()
    person = dict(person)
    # Формат: "Норма 1 кат. 5; Норма 2 кат. 3"
    norm_parts = []
    for r in person_norms_names:
        part = r["name"]
        if r["personnel_cat"]:
            part += f" кат. {r['personnel_cat']}"
        norm_parts.append(part)
    person["norm_name"] = "; ".join(norm_parts) or None

    property_card = _build_property_card(conn, person_id)
    from core.settings import get_setting
    company_name = get_setting("company_name", "")
    conn.close()

    active_items = len([r for r in property_card["rows"] if r.get("color") == "green"])
    active_items_words = _count_words(active_items)

    return render_template(
        "personnel/property_card_print.html",
        person=dict(person),
        property_card=property_card,
        company_name=company_name,
        active_items=active_items,
        active_items_words=active_items_words,
    )


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
            person_norms = conn.execute("""
                SELECT pn.id, pn.norm_id, pn.personnel_cat, sn.name AS norm_name
                FROM personnel_norms pn
                JOIN supply_norms sn ON sn.id = pn.norm_id
                WHERE pn.personnel_id = ? ORDER BY pn.id
            """, (person_id,)).fetchall()
            conn.close()
            return render_template(
                "personnel/form.html",
                person=data, errors=errors,
                groups=groups, battalions=battalions,
                units=units, platoons=platoons,
                ranks=_get_ranks(), is_edit=True,
                person_id=person_id,
                supply_norms=supply_norms,
                person_norms=[dict(r) for r in person_norms],
            )

        old_service_type = old_data.get("service_type") if old_data else None
        conn.execute(
            """UPDATE personnel SET
               last_name=?, first_name=?, middle_name=?,
               rank=?, position=?, category=?,
               battalion_id=?, unit_id=?, platoon_id=?, group_id=?,
               ipn=?, card_number=?, phone=?,
               size_head=?, size_height=?, size_underwear=?, size_suit=?,
               size_jacket=?, size_pants=?, size_shoes=?, size_vest=?,
               enroll_date=?, enroll_order=?, dismiss_date=?, dismiss_order=?,
               draft_date=?, draft_by=?, norm_id=?, service_type=?,
               updated_at=datetime('now','localtime')
               WHERE id=?""",
            (
                data["last_name"], data["first_name"], data["middle_name"],
                data["rank"], data["position"], data["category"],
                data["battalion_id"], data["unit_id"], data["platoon_id"], data["group_id"],
                data["ipn"], data["card_number"], data["phone"],
                data["size_head"], data["size_height"], data["size_underwear"], data["size_suit"],
                data["size_jacket"], data["size_pants"], data["size_shoes"], data["size_vest"],
                data["enroll_date"], data["enroll_order"],
                data["dismiss_date"], data["dismiss_order"],
                data["draft_date"], data["draft_by"], data["norm_id"], data["service_type"],
                person_id,
            )
        )
        conn.commit()
        log_action("edit", "personnel", person_id, old_data, data)
        conn.close()
        emit("personnel.updated", person_id=person_id, old=old_data, new=data)
        if old_service_type and old_service_type != data["service_type"]:
            from core.hooks import emit as _emit
            _emit("personnel.service_type_changed", person_id=person_id,
                  old_type=old_service_type, new_type=data["service_type"])
        return redirect(url_for("personnel.card", person_id=person_id))

    # GET — заповнити форму поточними даними
    from core.settings import get_setting as _get_setting
    person = dict(person_row)
    groups     = _get_all_groups(conn)
    battalions = _get_all_battalions(conn)
    units      = _get_units_by_battalion(conn, person.get("battalion_id")) if person.get("battalion_id") else []
    platoons   = _get_platoons_by_unit(conn, person.get("unit_id")) if person.get("unit_id") else []
    supply_norms = conn.execute(
        "SELECT id, name FROM supply_norms WHERE is_active=1 ORDER BY name"
    ).fetchall()
    person_norms = conn.execute("""
        SELECT pn.id, pn.norm_id, pn.personnel_cat, sn.name AS norm_name
        FROM personnel_norms pn
        JOIN supply_norms sn ON sn.id = pn.norm_id
        WHERE pn.personnel_id = ?
        ORDER BY pn.id
    """, (person_id,)).fetchall()
    default_service_type = _get_setting("default_service_type", "mobilized")
    conn.close()

    return render_template(
        "personnel/form.html",
        person=person, errors={},
        groups=groups, battalions=battalions,
        units=units, platoons=platoons,
        ranks=_get_ranks(), is_edit=True,
        person_id=person_id,
        supply_norms=supply_norms,
        person_norms=[dict(r) for r in person_norms],
        default_service_type=default_service_type,
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
        return jsonify({"ok": False, "msg": "Особу не знайдено"}), 404

    if not person["is_active"]:
        conn.close()
        return jsonify({"ok": False, "msg": "Особа вже заархівована"}), 400

    # Перевірка інвентарного майна
    inv_items = _active_inventory_items(conn, person_id)
    if inv_items:
        conn.close()
        return jsonify({
            "ok": False,
            "msg": "Є не здане інвентарне майно",
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
        return jsonify({"ok": False, "msg": "Особу не знайдено"}), 404

    if person["is_active"]:
        conn.close()
        return jsonify({"ok": False, "msg": "Особа вже активна"}), 400

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
        return jsonify({"ok": False, "msg": "Не обрано жодної особи"}), 400

    if not isinstance(ids, list):
        return jsonify({"ok": False, "msg": "ids має бути масивом"}), 400

    # Безпечний список id (тільки int)
    try:
        ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "Невірний формат ids"}), 400

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


@bp.route("/api/check-card-number")
@login_required
def api_check_card_number():
    """Returns: {"ok": true, "taken": bool, "owner": str}"""
    num        = request.args.get("num", "").strip()
    exclude_id = request.args.get("exclude_id", type=int)
    if not num:
        return jsonify({"ok": False})
    conn = get_connection()
    sql = "SELECT id, last_name, first_name FROM personnel WHERE card_number = ?"
    params = [num]
    if exclude_id:
        sql += " AND id != ?"
        params.append(exclude_id)
    row = conn.execute(sql, params).fetchone()
    conn.close()
    if row:
        owner = f"{row['last_name'].upper()} {row['first_name']} (#{row['id']})"
        return jsonify({"ok": True, "taken": True, "owner": owner})
    return jsonify({"ok": True, "taken": False, "owner": ""})


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

def _person_folder_name(person) -> str:
    """Формує ім'я папки для особи: прізвище_і_б_номеркартки (або id якщо немає картки).
    Тільки ASCII-безпечні символи — кирилиця дозволена, пробіли замінюються на _."""
    import re
    last  = (person["last_name"]   or "").strip().lower()
    first = (person["first_name"]  or "").strip()
    mid   = (person["middle_name"] or "").strip()
    card  = str(person["card_number"] or person["id"])

    # ініціали
    f_i = first[0].upper()  if first  else ""
    m_i = mid[0].upper()    if mid    else ""

    # прізвище_І_Б_номер
    parts = [last]
    if f_i:
        parts.append(f_i)
    if m_i:
        parts.append(m_i)
    parts.append(card)

    folder = "_".join(parts)
    # Прибрати символи що небезпечні для шляху
    folder = re.sub(r'[<>:"/\\|?*]', "", folder)
    return folder or f"person_{person['id']}"


@bp.route("/<int:person_id>/photo", methods=["POST"])
@login_required
def upload_photo(person_id):
    """Завантаження фото військовослужбовця (jpg/png, max 5MB)."""
    import os
    from werkzeug.utils import secure_filename
    from flask import current_app

    conn = get_connection()
    person = conn.execute("SELECT * FROM personnel WHERE id=?", (person_id,)).fetchone()
    if not person:
        conn.close()
        return jsonify({"ok": False, "msg": "Особу не знайдено"}), 404

    file = request.files.get("photo")
    if not file or not file.filename:
        conn.close()
        return jsonify({"ok": False, "msg": "Файл не обрано"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        conn.close()
        return jsonify({"ok": False, "msg": "Дозволені формати: jpg, jpeg, png"}), 400

    from core.settings import get_storage_path
    folder_name = _person_folder_name(person)
    storage = get_storage_path() / "personnel" / folder_name / "photos"
    storage.mkdir(parents=True, exist_ok=True)

    filename = f"photo{ext}"
    filepath = storage / filename

    # Видалити старе фото якщо інший ext
    for old_ext in (".jpg", ".jpeg", ".png"):
        old = storage / f"photo{old_ext}"
        if old.exists() and old != filepath:
            old.unlink(missing_ok=True)

    file.save(str(filepath))

    # Зберігаємо відносний шлях
    rel_path = f"/storage/personnel/{folder_name}/photos/{filename}"
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
        return jsonify({"ok": False, "msg": "Особу не знайдено"}), 404

    if person["photo_path"]:
        from core.settings import get_storage_path
        rel = person["photo_path"].lstrip("/")
        # відрізати "storage/" prefix якщо є
        if rel.startswith("storage/"):
            rel = rel[len("storage/"):]
        full = get_storage_path() / rel
        if full.exists():
            full.unlink(missing_ok=True)

    conn.execute(
        "UPDATE personnel SET photo_path=NULL, updated_at=datetime('now','localtime') WHERE id=?",
        (person_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:person_id>/sizes", methods=["POST"])
@login_required
def save_size(person_id):
    """Inline-збереження одного поля розміру.
    Returns: {"ok": bool, "msg": str}
    """
    ALLOWED = {
        "size_height", "size_head", "size_suit", "size_jacket",
        "size_pants", "size_shoes", "size_underwear", "size_vest",
    }
    data = request.get_json(silent=True) or {}
    field = data.get("field", "")
    value = (data.get("value") or "").strip()[:20]

    if field not in ALLOWED:
        return jsonify({"ok": False, "msg": "Невідоме поле"}), 400

    conn = get_connection()
    if not conn.execute("SELECT id FROM personnel WHERE id=?", (person_id,)).fetchone():
        conn.close()
        return jsonify({"ok": False, "msg": "Особу не знайдено"}), 404

    # field перевірено через ALLOWED — безпечна підстановка імені колонки
    # Використовуємо словник замість f-string для додаткової ясності
    _FIELD_SQL = {f: f"UPDATE personnel SET {f}=?, updated_at=datetime('now','localtime') WHERE id=?"
                  for f in ALLOWED}
    conn.execute(_FIELD_SQL[field], (value or None, person_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  Файли наказів (зарахування / вибуття)
# ─────────────────────────────────────────────────────────────

def _order_file_upload(person_id: int, field: str):
    """Спільна логіка завантаження файлу наказу. field = 'enroll' або 'dismiss'."""
    conn = get_connection()
    person = conn.execute("SELECT * FROM personnel WHERE id=?", (person_id,)).fetchone()
    if not person:
        conn.close()
        return jsonify({"ok": False, "msg": "Особу не знайдено"}), 404

    file = request.files.get("file")
    if not file or not file.filename:
        conn.close()
        return jsonify({"ok": False, "msg": "Файл не вибрано"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".jpg", ".jpeg", ".png"):
        conn.close()
        return jsonify({"ok": False, "msg": "Дозволені формати: PDF, JPG, PNG"}), 400

    from core.settings import get_storage_path
    folder_name = _person_folder_name(person)
    storage = get_storage_path() / "personnel" / folder_name / "orders"
    storage.mkdir(parents=True, exist_ok=True)

    db_field = f"{field}_order_file"
    filename  = f"{field}_order{ext}"
    filepath  = storage / filename

    # Видалити старий файл якщо є
    old_val = person[db_field]
    if old_val:
        rel_old = old_val.lstrip("/")
        if rel_old.startswith("storage/"):
            rel_old = rel_old[len("storage/"):]
        storage_root = get_storage_path().resolve()
        old_path = (storage_root / rel_old).resolve()
        # Захист від path traversal: тільки файли всередині storage/
        if str(old_path).startswith(str(storage_root)) and old_path.exists():
            old_path.unlink()

    file.save(str(filepath))
    rel = f"/storage/personnel/{folder_name}/orders/{filename}"

    # db_field перевірено через field in {"enroll","dismiss"} перед викликом
    _ORDER_FIELD_SQL = {
        "enroll_order_file": "UPDATE personnel SET enroll_order_file=?, updated_at=datetime('now','localtime') WHERE id=?",
        "dismiss_order_file": "UPDATE personnel SET dismiss_order_file=?, updated_at=datetime('now','localtime') WHERE id=?",
    }
    conn.execute(_ORDER_FIELD_SQL[db_field], (rel, person_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "url": rel})


def _order_file_delete(person_id: int, field: str):
    """Спільна логіка видалення файлу наказу."""
    conn = get_connection()
    person = conn.execute("SELECT * FROM personnel WHERE id=?", (person_id,)).fetchone()
    if not person:
        conn.close()
        return jsonify({"ok": False, "msg": "Особу не знайдено"}), 404

    _ALLOWED_DELETE_FIELDS = {"enroll", "dismiss"}
    if field not in _ALLOWED_DELETE_FIELDS:
        conn.close()
        return jsonify({"ok": False, "msg": "Недозволене поле"}), 400

    db_field = f"{field}_order_file"
    _ORDER_FIELD_SQL_NULL = {
        "enroll_order_file":  "UPDATE personnel SET enroll_order_file=NULL,  updated_at=datetime('now','localtime') WHERE id=?",
        "dismiss_order_file": "UPDATE personnel SET dismiss_order_file=NULL, updated_at=datetime('now','localtime') WHERE id=?",
    }
    old_val  = person[db_field]
    if old_val:
        from core.settings import get_storage_path
        storage_root = get_storage_path().resolve()
        rel_old = old_val.lstrip("/")
        if rel_old.startswith("storage/"):
            rel_old = rel_old[len("storage/"):]
        old_path = (storage_root / rel_old).resolve()
        if str(old_path).startswith(str(storage_root)) and old_path.exists():
            old_path.unlink()
    conn.execute(_ORDER_FIELD_SQL_NULL[db_field], (person_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:person_id>/enroll-order-file", methods=["POST"])
@login_required
def upload_enroll_order(person_id):
    return _order_file_upload(person_id, "enroll")


@bp.route("/<int:person_id>/enroll-order-file/delete", methods=["POST"])
@login_required
def delete_enroll_order(person_id):
    return _order_file_delete(person_id, "enroll")


@bp.route("/<int:person_id>/dismiss-order-file", methods=["POST"])
@login_required
def upload_dismiss_order(person_id):
    return _order_file_upload(person_id, "dismiss")


@bp.route("/<int:person_id>/dismiss-order-file/delete", methods=["POST"])
@login_required
def delete_dismiss_order(person_id):
    return _order_file_delete(person_id, "dismiss")


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
#  API: Журнал змін
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/history")
@login_required
def api_history(person_id):
    """
    API: журнал подій для картки о/с.
    Об'єднує: audit_log по personnel/personnel_items + накладні + РВ.
    """
    import json as _json
    conn = get_connection()

    # audit_log по картці особи + її майну
    audit_rows = conn.execute("""
        SELECT al.action, al.table_name, al.record_id,
               al.new_data, al.old_data, al.created_at,
               u.username
        FROM audit_log al
        LEFT JOIN users u ON al.user_id = u.id
        WHERE (al.table_name = 'personnel' AND al.record_id = ?)
           OR (al.table_name = 'personnel_items' AND al.record_id IN (
               SELECT id FROM personnel_items WHERE personnel_id = ?
           ))
        ORDER BY al.created_at DESC
        LIMIT 200
    """, (person_id, person_id)).fetchall()

    # Накладні де особа є отримувачем/відправником
    invoices = conn.execute("""
        SELECT i.id, i.number, i.status, i.created_at, i.issued_date,
               ii_sum.total_sum, ii_sum.item_count
        FROM invoices i
        LEFT JOIN (
            SELECT invoice_id,
                   SUM(COALESCE(actual_qty, planned_qty) * price) AS total_sum,
                   COUNT(*) AS item_count
            FROM invoice_items GROUP BY invoice_id
        ) ii_sum ON ii_sum.invoice_id = i.id
        WHERE i.recipient_personnel_id = ? OR i.sender_personnel_id = ?
        ORDER BY i.created_at DESC
        LIMIT 50
    """, (person_id, person_id)).fetchall()

    # РВ (роздавальні відомості) де особа є отримувачем
    rv_sheets = conn.execute("""
        SELECT s.id, s.number, s.status, s.created_at, s.doc_date,
               (SELECT SUM(dsi.price * dsr2.received)
                FROM distribution_sheet_rows dsr2
                JOIN distribution_sheet_items dsi ON dsi.sheet_id = dsr2.sheet_id
                WHERE dsr2.sheet_id = s.id AND dsr2.personnel_id = ?) AS total_sum,
               (SELECT COUNT(*) FROM distribution_sheet_items WHERE sheet_id = s.id) AS item_count
        FROM distribution_sheets s
        WHERE s.id IN (
            SELECT DISTINCT sheet_id FROM distribution_sheet_rows
            WHERE personnel_id = ?
        )
        ORDER BY s.created_at DESC
        LIMIT 50
    """, (person_id, person_id)).fetchall()

    conn.close()

    ACTION_LABELS = {
        "add":    "Додано",
        "edit":   "Змінено",
        "delete": "Видалено",
        "archive": "Архівовано",
    }
    TABLE_LABELS = {
        "personnel":       "Картка особи",
        "personnel_items": "Майно о/с",
    }

    events = []

    for r in audit_rows:
        new_d = {}
        old_d = {}
        try:
            if r["new_data"]: new_d = _json.loads(r["new_data"])
        except Exception: pass
        try:
            if r["old_data"]: old_d = _json.loads(r["old_data"])
        except Exception: pass

        desc = ""
        if r["table_name"] == "personnel_items":
            if r["action"] == "add":
                src = new_d.get("source", "")
                cnt = new_d.get("count", "")
                desc = f"Додано майно ({src})" + (f", {cnt} поз." if cnt else "")
            elif r["action"] == "edit":
                desc = "Змінено майно"
            elif r["action"] == "delete":
                desc = "Майно видалено/списано"
        elif r["table_name"] == "personnel":
            if r["action"] == "add":
                desc = "Особу додано до системи"
            elif r["action"] == "edit":
                changed = [k for k in new_d if new_d.get(k) != old_d.get(k)]
                if changed:
                    desc = "Змінено: " + ", ".join(changed[:5])
                    if len(changed) > 5:
                        desc += f" та ще {len(changed)-5}"
                else:
                    desc = "Дані оновлено"
            elif r["action"] == "archive":
                desc = "Особу архівовано"

        events.append({
            "type":       "audit",
            "action":     r["action"],
            "table":      r["table_name"],
            "table_label": TABLE_LABELS.get(r["table_name"], r["table_name"]),
            "action_label": ACTION_LABELS.get(r["action"], r["action"]),
            "description": desc,
            "user":       r["username"] or "система",
            "date":       r["created_at"][:16] if r["created_at"] else "",
        })

    INV_STATUS_LABELS = {
        "draft": "Чернетка", "created": "Створено",
        "issued": "Видано", "processed": "Проведено",
        "cancelled": "Скасовано",
    }
    RV_STATUS_LABELS = {
        "draft": "Чернетка", "active": "Активна",
        "closed": "Закрита", "cancelled": "Скасовано",
    }

    for inv in invoices:
        eff_date = inv["issued_date"] or inv["created_at"] or ""
        events.append({
            "type":        "invoice",
            "id":          inv["id"],
            "number":      inv["number"] or f"Накладна #{inv['id']}",
            "status":      inv["status"],
            "status_label": INV_STATUS_LABELS.get(inv["status"], inv["status"]),
            "total_sum":   round(inv["total_sum"] or 0, 2),
            "item_count":  inv["item_count"] or 0,
            "date":        eff_date[:16] if eff_date else "",
        })

    for rv in rv_sheets:
        eff_date = rv["doc_date"] or rv["created_at"] or ""
        events.append({
            "type":        "rv",
            "id":          rv["id"],
            "number":      rv["number"] or f"РВ #{rv['id']}",
            "status":      rv["status"],
            "status_label": RV_STATUS_LABELS.get(rv["status"], rv["status"]),
            "total_sum":   round(rv["total_sum"] or 0, 2),
            "item_count":  rv["item_count"] or 0,
            "date":        eff_date[:16] if eff_date else "",
        })

    # Сортуємо всі події по даті
    events.sort(key=lambda e: e["date"], reverse=True)

    return jsonify({"events": events})


# ─────────────────────────────────────────────────────────────
#  API — норми особи (personnel_norms)
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/norms", methods=["GET"])
@login_required
def api_norms_list(person_id):
    """Повертає список норм призначених особі."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT pn.id, pn.norm_id, pn.personnel_cat,
               sn.name AS norm_name, sn.is_active AS norm_is_active
        FROM personnel_norms pn
        JOIN supply_norms sn ON sn.id = pn.norm_id
        WHERE pn.personnel_id = ?
        ORDER BY pn.id
    """, (person_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route("/<int:person_id>/norms/add", methods=["POST"])
@login_required
def api_norm_add(person_id):
    """Додати норму особі."""
    conn = get_connection()
    person = conn.execute("SELECT id FROM personnel WHERE id=?", (person_id,)).fetchone()
    if not person:
        conn.close()
        return jsonify({"ok": False, "error": "Особу не знайдено"}), 404

    data = request.get_json(silent=True) or {}
    norm_id = data.get("norm_id")
    personnel_cat = int(data.get("personnel_cat") or 1)
    if not norm_id:
        conn.close()
        return jsonify({"ok": False, "error": "Оберіть норму"}), 400
    if not (1 <= personnel_cat <= 5):
        conn.close()
        return jsonify({"ok": False, "error": "Категорія 1–5"}), 400

    try:
        cur = conn.execute("""
            INSERT INTO personnel_norms (personnel_id, norm_id, personnel_cat,
                                         created_at)
            VALUES (?, ?, ?, datetime('now','localtime'))
        """, (person_id, norm_id, personnel_cat))
        new_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.close()
        if "UNIQUE" in str(e):
            return jsonify({"ok": False, "error": "Ця норма вже призначена"}), 400
        return jsonify({"ok": False, "error": str(e)}), 500

    row = conn.execute("""
        SELECT pn.id, pn.norm_id, pn.personnel_cat,
               sn.name AS norm_name, sn.is_active AS norm_is_active
        FROM personnel_norms pn
        JOIN supply_norms sn ON sn.id = pn.norm_id
        WHERE pn.id = ?
    """, (new_id,)).fetchone()
    conn.close()
    return jsonify({"ok": True, "norm": dict(row)})


@bp.route("/<int:person_id>/norms/<int:pn_id>/delete", methods=["POST"])
@login_required
def api_norm_delete(person_id, pn_id):
    """Видалити норму у особи."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM personnel_norms WHERE id=? AND personnel_id=?",
        (pn_id, person_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:person_id>/norms/<int:pn_id>/cat", methods=["POST"])
@login_required
def api_norm_cat(person_id, pn_id):
    """Змінити категорію норми для особи."""
    data = request.get_json(silent=True) or {}
    personnel_cat = int(data.get("personnel_cat") or 1)
    if not (1 <= personnel_cat <= 5):
        return jsonify({"ok": False, "error": "Категорія 1–5"}), 400
    conn = get_connection()
    conn.execute(
        "UPDATE personnel_norms SET personnel_cat=? WHERE id=? AND personnel_id=?",
        (personnel_cat, pn_id, person_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


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

    # ── 1. Позиції норм особи (може бути кілька норм) ──────────
    group_order = {}
    positions_map = OrderedDict()
    pos_group = {}
    pos_order = {}
    seq = [0]

    pn_rows = conn.execute(
        "SELECT norm_id FROM personnel_norms WHERE personnel_id=? ORDER BY id",
        (person_id,)
    ).fetchall()
    norm_ids = [r["norm_id"] for r in pn_rows]

    # Отримуємо pn_cat для цієї особи (для COALESCE per-category qty)
    _pn_cat_for_norm = conn.execute(
        "SELECT personnel_cat FROM personnel_norms WHERE personnel_id=? LIMIT 1", (person_id,)
    ).fetchone()
    _pn_cat_val = int(_pn_cat_for_norm["personnel_cat"]) if _pn_cat_for_norm else 5

    if norm_ids:
        for norm_id_iter in norm_ids:
            norm_rows = conn.execute(
                """SELECT sni.norm_dict_id AS nd_id,
                          COALESCE(sniw.qty, sni.quantity) AS norm_qty,
                          nd.name AS norm_name, nd.sort_order AS nd_order,
                          nd.unit AS nd_uom,
                          ndg.name AS group_name, ndg.sort_order AS g_order
                   FROM supply_norm_items sni
                   JOIN norm_dictionary nd ON nd.id = sni.norm_dict_id
                   LEFT JOIN norm_dict_groups ndg ON nd.group_id = ndg.id
                   LEFT JOIN supply_norm_item_wear sniw
                          ON sniw.norm_item_id = sni.id AND sniw.personnel_cat = ?
                   WHERE sni.norm_id = ?
                   ORDER BY ndg.sort_order NULLS LAST, nd.sort_order NULLS LAST, nd.name""",
                (_pn_cat_val, norm_id_iter)
            ).fetchall()
            for r in norm_rows:
                nd_id  = r["nd_id"]
                if nd_id in positions_map:
                    # Якщо позиція вже є з іншої норми — беремо максимальну кількість
                    if (r["norm_qty"] or 0) > positions_map[nd_id]["norm_qty"]:
                        positions_map[nd_id]["norm_qty"] = r["norm_qty"] or 0
                    continue
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
    has_norm = bool(norm_ids)

    # ── 3. Розраховуємо рядки атестату через military_logic ───
    from core.military_logic import calc_attestat_row as _calc_att
    from datetime import date as _date
    service_type = person.get("service_type") or "mobilized"
    norm_date    = person.get("enroll_date")

    # Категорія та wear_months для кожної nd_id
    pn_cat = _pn_cat_val  # вже отримано вище для COALESCE-запиту

    # Завантажуємо wear_months для всіх nd_id позицій
    nd_ids = [k for k in positions_map.keys() if isinstance(k, int)]
    nd_wear_map: dict = {}  # nd_id -> wear_months
    if nd_ids:
        placeholders = ",".join("?" * len(nd_ids))
        wear_rows_att = conn.execute(f"""
            SELECT sni.norm_dict_id, sniw.wear_months
            FROM supply_norm_items sni
            JOIN personnel_norms pn ON pn.norm_id = sni.norm_id AND pn.personnel_id = ?
            LEFT JOIN supply_norm_item_wear sniw
                   ON sniw.norm_item_id = sni.id AND sniw.personnel_cat = ?
            WHERE sni.norm_dict_id IN ({placeholders})
        """, [person_id, pn_cat] + nd_ids).fetchall()
        for w in wear_rows_att:
            if w["norm_dict_id"] and w["wear_months"]:
                nd_wear_map[w["norm_dict_id"]] = int(w["wear_months"])

    for nd_id, pos in positions_map.items():
        if not isinstance(nd_id, int):
            pos["att_show"]  = pos["total_qty"] > 0
            pos["att_qty"]   = pos["total_qty"]
            pos["att_date"]  = pos["issuances"][0]["date"] if pos["issuances"] else None
            pos["att_date_label"] = ""
            pos["att_is_partial"] = False
            continue

        wear_months  = nd_wear_map.get(nd_id, 0)
        last_iss     = pos["issuances"][-1]["date"] if pos["issuances"] else None
        cycle_start  = pos["issuances"][0]["date"] if pos["issuances"] else None

        att = _calc_att(
            service_type     = service_type,
            cycle_start_date = cycle_start,
            norm_date        = norm_date,
            wear_months      = wear_months,
            issued_qty       = pos["total_qty"],
            norm_qty         = pos["norm_qty"],
            last_issue_date  = last_iss,
        )
        pos["att_show"]       = att["att_show"]
        pos["att_qty"]        = att["att_qty"]
        pos["att_date"]       = att["att_date"]
        pos["att_date_label"] = att["att_date_label"]
        pos["att_is_partial"] = att["is_partial"]

    # Кількість кожної позиції прописом (ключ = seq) — по att_qty для показаних
    total_qty_words = {}
    for pos in positions_map.values():
        qty = int(pos.get("att_qty") or pos["total_qty"] or 0)
        total_qty_words[pos["seq"]] = _count_words(qty)

    # Загальна кількість предметів — тільки показані позиції, по att_qty
    total_items_count = sum(
        int(p.get("att_qty") or p["total_qty"] or 0)
        for p in positions_map.values()
        if p.get("att_show")
    )
    total_items_words = _count_words(total_items_count)

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

    attestat_service = get_setting("attestat_service", "РСТ")
    basis_list       = json.loads(get_setting("attestat_basis_list",     "[]") or "[]")
    recipient_list   = json.loads(get_setting("attestat_recipient_list", "[]") or "[]")

    conn.close()

    return render_template(
        "personnel/attestat.html",
        person=person,
        groups=groups_out,
        total_sum=total_sum,
        max_issuances=max_issuances,
        settings=settings,
        has_norm=has_norm,
        total_qty_words=total_qty_words,
        total_items_count=total_items_count,
        total_items_words=total_items_words,
        today=date.today().strftime("%d.%m.%Y"),
        attestat_service=attestat_service,
        basis_list=basis_list,
        recipient_list=recipient_list,
    )


# ─────────────────────────────────────────────────────────────
#  Атестат — збереження / завантаження реєстраційних полів
# ─────────────────────────────────────────────────────────────

ATTESTAT_FIELDS = {"reg_number", "reg_sheet", "reg_doc_number", "reg_doc_date",
                   "reg_basis", "reg_service", "reg_recipient", "reg_font_size"}


@bp.route("/<int:person_id>/attestat/data", methods=["GET"])
@login_required
def attestat_data_get(person_id):
    """Returns: {"ok": true, "data": {field: value, ...}}"""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM attestat_data WHERE personnel_id=?", (person_id,)
    ).fetchone()
    conn.close()
    return jsonify({"ok": True, "data": dict(row) if row else {}})


@bp.route("/<int:person_id>/attestat/data", methods=["POST"])
@login_required
def attestat_data_save(person_id):
    """Returns: {"ok": true}"""
    body = request.get_json(silent=True) or {}
    field = body.get("field", "")
    value = body.get("value", "")
    if field not in ATTESTAT_FIELDS:
        return jsonify({"ok": False, "msg": "Невідоме поле"}), 400
    conn = get_connection()
    conn.execute(
        f"""INSERT INTO attestat_data (personnel_id, {field}, updated_at)
            VALUES (?, ?, datetime('now','localtime'))
            ON CONFLICT(personnel_id) DO UPDATE SET
                {field}=excluded.{field},
                updated_at=excluded.updated_at""",
        (person_id, value)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  Чернетка накладної "Заповнити по нормі"
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/create-invoice-by-norm", methods=["POST"])
@login_required
def create_invoice_by_norm(person_id):
    """
    POST /personnel/<person_id>/create-invoice-by-norm
    Створює чернетку накладної з позиціями що ще не видані по нормі.
    Returns: {"ok": true, "inv_id": int} або {"ok": false, "msg": str}
    """
    import time
    from datetime import date as _date
    from core.military_logic import get_cycle_status as _cs
    from core.settings import get_setting

    conn = get_connection()

    person = conn.execute(
        "SELECT service_type, enroll_date, norm_id FROM personnel WHERE id=?", (person_id,)
    ).fetchone()
    if not person:
        conn.close()
        return jsonify({"ok": False, "msg": "Особу не знайдено"}), 404

    # Позиції з норми особи
    norm_items = conn.execute("""
        SELECT sni.item_id,
               COALESCE(sniw_inv.qty, sni.quantity) AS norm_qty,
               sni.wear_years,
               sni.category, nd.name AS norm_name,
               pn.personnel_cat,
               COALESCE((
                   SELECT SUM(pi.quantity) FROM personnel_items pi
                   WHERE pi.personnel_id=? AND pi.item_id=sni.item_id AND pi.status='active'
               ), 0) AS issued_qty,
               (
                   SELECT pi.cycle_start_date FROM personnel_items pi
                   WHERE pi.personnel_id=? AND pi.item_id=sni.item_id AND pi.status='active'
                   ORDER BY COALESCE(pi.issue_date, pi.created_at) DESC LIMIT 1
               ) AS cycle_start_date
        FROM personnel p
        JOIN personnel_norms pn ON pn.personnel_id = p.id
        JOIN supply_norm_items sni ON sni.norm_id = pn.norm_id
        LEFT JOIN norm_dictionary nd ON nd.id = sni.norm_dict_id
        LEFT JOIN supply_norm_item_wear sniw_inv
               ON sniw_inv.norm_item_id = sni.id AND sniw_inv.personnel_cat = pn.personnel_cat
        WHERE p.id = ? AND sni.item_id IS NOT NULL
    """, (person_id, person_id, person_id)).fetchall()

    if not norm_items:
        conn.close()
        return jsonify({"ok": False, "msg": "Норму не призначено або позицій немає"}), 400

    # Wear_months
    sni_ids = []
    pn_cat  = None
    for r in norm_items:
        if pn_cat is None:
            pn_cat = r["personnel_cat"]
    wear_map: dict = {}

    # Відбираємо тільки ті позиції де є борг
    service_type = person["service_type"] or "mobilized"
    norm_date    = person["enroll_date"]
    need_items   = []
    for r in norm_items:
        norm_qty   = float(r["norm_qty"] or 0)
        issued_qty = float(r["issued_qty"] or 0)
        if norm_qty <= 0:
            continue
        from core.military_logic import wear_years_to_months as _wym
        wear_months = _wym(r["wear_years"])
        cs = _cs(service_type, r["cycle_start_date"], norm_date, wear_months, issued_qty, norm_qty)
        if cs["debt_qty"] > 0:
            need_items.append({
                "item_id":  r["item_id"],
                "qty":      cs["debt_qty"],
                "category": r["category"] or "I",
            })

    if not need_items:
        conn.close()
        return jsonify({"ok": False, "msg": "Боргів по нормі немає — все видано"}), 200

    # Створюємо чернетку накладної
    s = get_setting
    today = _date.today().isoformat()
    conn.execute("""
        INSERT INTO invoices
            (number, direction, status, doc_date,
             recipient_type, recipient_personnel_id,
             given_by_rank, given_by_name,
             chief_rank, chief_name, chief_is_tvo,
             clerk_rank, clerk_name,
             service_name, supplier_name,
             base_document, notes, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                datetime('now','localtime'), datetime('now','localtime'))
    """, (
        f"ЧЕРНЕТКА-{int(time.time())}",
        "issue", "draft", today,
        "personnel", person_id,
        s("warehouse_chief_rank", ""), s("warehouse_chief_name", ""),
        s("chief_rank", ""), s("chief_name", ""), 1 if s("chief_is_tvo") == "1" else 0,
        s("clerk_rank", ""), s("clerk_name", ""),
        s("service_name", ""), s("company_name", ""),
        "Заповнення по нормі", "",
    ))
    conn.commit()
    inv_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    for i, it in enumerate(need_items):
        conn.execute("""
            INSERT INTO invoice_items (invoice_id, item_id, planned_qty, price, category, sort_order)
            VALUES (?,?,?,?,?,?)
        """, (inv_id, it["item_id"], it["qty"], 0.0, it["category"], i * 10))

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "inv_id": inv_id})


# ─────────────────────────────────────────────────────────────
#  Прийом майна з атестату — редирект до нового blueprint
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:person_id>/attestat-import", methods=["GET", "POST"])
@login_required
def attestat_import(person_id):
    """Редирект до нового blueprint attestat_import.index."""
    return redirect(url_for("attestat_import.index", person_id=person_id))




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
        "size_vest":      _str("size_vest"),
        "enroll_date":    _str("enroll_date"),
        "enroll_order":   _str("enroll_order"),
        "dismiss_date":   _str("dismiss_date"),
        "dismiss_order":  _str("dismiss_order"),
        "draft_date":     _str("draft_date"),
        "draft_by":       _str("draft_by"),
        "norm_id":        _int("norm_id"),
        "service_type":   request.form.get("service_type", "mobilized"),
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


# ─────────────────────────────────────────────────────────────
#  MW partial — для багатозадачного вікна
# ─────────────────────────────────────────────────────────────

@bp.route("/mw/")
@login_required
def mw_index():
    """Partial: список о/с для MW-вікна."""
    conn = get_connection()
    search = request.args.get("q", "").strip()
    unit_id = request.args.get("unit_id", type=int)

    where, params = ["p.is_active = 1"], []
    if search:
        where.append("(p.last_name LIKE ? OR p.first_name LIKE ? OR p.middle_name LIKE ?)")
        like = f"%{search}%"
        params += [like, like, like]
    if unit_id:
        where.append("p.unit_id = ?")
        params.append(unit_id)

    rows = conn.execute(f"""
        SELECT p.id, p.last_name, p.first_name, p.middle_name,
               p.rank, p.position,
               u.name AS unit_name
        FROM personnel p
        LEFT JOIN units u ON p.unit_id = u.id
        WHERE {' AND '.join(where)}
        ORDER BY p.last_name, p.first_name
        LIMIT 100
    """, params).fetchall()

    units = conn.execute(
        "SELECT id, name FROM units ORDER BY name"
    ).fetchall()
    conn.close()
    return render_template("personnel/mw_index.html",
                           rows=rows, units=units,
                           search=search, filter_unit_id=unit_id)


@bp.route("/mw/<int:person_id>")
@login_required
def mw_card(person_id):
    """MW-картка особи."""
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
                  pi.category, pi.issue_date, pi.status
           FROM personnel_items pi
           JOIN item_dictionary d ON pi.item_id = d.id
           WHERE pi.personnel_id = ?
           ORDER BY pi.status, d.name""",
        (person_id,)
    ).fetchall()

    total_sum = sum(
        r["quantity"] * r["price"]
        for r in items
        if r["status"] == "active" and r["price"]
    )

    person_norms = conn.execute(
        """SELECT pn.id, sn.name AS norm_name, pn.personnel_cat
           FROM personnel_norms pn
           JOIN supply_norms sn ON sn.id = pn.norm_id
           WHERE pn.personnel_id = ?
           ORDER BY pn.id""",
        (person_id,)
    ).fetchall()

    conn.close()
    return render_template(
        "personnel/mw_card.html",
        person=dict(person),
        items=items,
        total_sum=total_sum,
        person_norms=[dict(r) for r in person_norms],
    )
