"""
modules/supply_norms/routes.py — Норми видачі майна

Норма = шаблон переліку майна що повинен отримати військовослужбовець
залежно від типу контракту (Контракт 3 форма, Мобілізований тощо).
"""
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash
from core.auth import login_required
from core.db import get_connection
from core.audit import log_action

bp = Blueprint("supply_norms", __name__, url_prefix="/supply-norms")


def _get_norm_groups(conn):
    """Повертає список груп з позиціями словника норм."""
    groups_raw = conn.execute(
        "SELECT id, name FROM norm_dict_groups ORDER BY sort_order, name"
    ).fetchall()
    try:
        items_rows = conn.execute(
            "SELECT id, name, unit, group_id FROM norm_dictionary ORDER BY name"
        ).fetchall()
        items_raw = [{"id": r["id"], "name": r["name"], "unit": r["unit"] or "шт",
                      "group_id": r["group_id"]}
                     for r in items_rows]
    except Exception:
        items_rows = conn.execute(
            "SELECT id, name, '' AS unit, group_id FROM norm_dictionary ORDER BY name"
        ).fetchall()
        items_raw = [{"id": r["id"], "name": r["name"], "unit": "шт",
                      "group_id": r["group_id"]}
                     for r in items_rows]
    groups = []
    for g in groups_raw:
        groups.append({
            "id": g["id"],
            "name": g["name"],
            "norms": [i for i in items_raw if i["group_id"] == g["id"]],
        })
    # Позиції без групи
    ungrouped = [i for i in items_raw if i["group_id"] is None]
    if ungrouped:
        groups.append({"id": None, "name": "Без групи", "norms": ungrouped})
    return groups


# ─────────────────────────────────────────────────────────────
#  Список норм
# ─────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    conn = get_connection()
    norms = conn.execute("""
        SELECT sn.*,
               COUNT(DISTINCT sni.id)  AS items_count,
               COUNT(DISTINCT p.id)    AS assigned_count
        FROM supply_norms sn
        LEFT JOIN supply_norm_items sni ON sni.norm_id = sn.id
        LEFT JOIN personnel p ON p.norm_id = sn.id AND p.is_active = 1
        GROUP BY sn.id
        ORDER BY sn.is_active DESC, sn.name
    """).fetchall()
    conn.close()
    return render_template("supply_norms/index.html", norms=norms)


# ─────────────────────────────────────────────────────────────
#  Створити нову норму
# ─────────────────────────────────────────────────────────────

@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name:
            flash("Назва норми обов'язкова", "error")
            conn = get_connection()
            norm_groups = _get_norm_groups(conn)
            conn.close()
            return render_template("supply_norms/form.html",
                                   norm=None, items=[], norm_groups=norm_groups)
        conn = get_connection()
        try:
            cur = conn.execute(
                """INSERT INTO supply_norms (name, description)
                   VALUES (?, ?)""",
                (name, description or None)
            )
            norm_id = cur.lastrowid
            conn.commit()
            log_action("add", "supply_norms", norm_id, new_data={"name": name})
            conn.close()
            return redirect(url_for("supply_norms.edit", norm_id=norm_id))
        except Exception as e:
            conn.close()
            if "UNIQUE" in str(e):
                flash(f"Норма з назвою «{name}» вже існує", "error")
            else:
                flash(f"Помилка: {e}", "error")
            conn = get_connection()
            norm_groups = _get_norm_groups(conn)
            conn.close()
            return render_template("supply_norms/form.html",
                                   norm={"name": name, "description": description},
                                   items=[], norm_groups=norm_groups)

    conn = get_connection()
    norm_groups = _get_norm_groups(conn)
    conn.close()
    return render_template("supply_norms/form.html",
                           norm=None, items=[], norm_groups=norm_groups)


# ─────────────────────────────────────────────────────────────
#  Редагувати норму
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:norm_id>/edit", methods=["GET", "POST"])
@login_required
def edit(norm_id):
    conn = get_connection()
    norm = conn.execute("SELECT * FROM supply_norms WHERE id=?", (norm_id,)).fetchone()
    if not norm:
        conn.close()
        flash("Норму не знайдено", "error")
        return redirect(url_for("supply_norms.index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        is_active = 1 if request.form.get("is_active") else 0
        if not name:
            flash("Назва норми обов'язкова", "error")
        else:
            try:
                conn.execute(
                    """UPDATE supply_norms
                       SET name=?, description=?, is_active=?,
                           updated_at=datetime('now','localtime')
                       WHERE id=?""",
                    (name, description or None, is_active, norm_id)
                )
                conn.commit()
                log_action("edit", "supply_norms", norm_id,
                           old_data=dict(norm),
                           new_data={"name": name, "is_active": is_active})
                flash("Збережено", "success")
            except Exception as e:
                if "UNIQUE" in str(e):
                    flash(f"Норма з назвою «{name}» вже існує", "error")
                else:
                    flash(f"Помилка: {e}", "error")
            norm = conn.execute("SELECT * FROM supply_norms WHERE id=?", (norm_id,)).fetchone()

    items = conn.execute("""
        SELECT sni.*, nd.name AS item_name, nd.unit AS unit_of_measure,
               ndg.name AS group_name
        FROM supply_norm_items sni
        JOIN norm_dictionary nd ON sni.norm_dict_id = nd.id
        LEFT JOIN norm_dict_groups ndg ON nd.group_id = ndg.id
        WHERE sni.norm_id = ?
        ORDER BY sni.sort_order, sni.id
    """, (norm_id,)).fetchall()

    norm_groups = _get_norm_groups(conn)
    conn.close()

    return render_template("supply_norms/form.html",
                           norm=norm, items=items, norm_groups=norm_groups)


# ─────────────────────────────────────────────────────────────
#  Увімкнути / вимкнути норму (AJAX)
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:norm_id>/toggle", methods=["POST"])
@login_required
def toggle(norm_id):
    conn = get_connection()
    norm = conn.execute("SELECT id, is_active FROM supply_norms WHERE id=?", (norm_id,)).fetchone()
    if not norm:
        conn.close()
        return jsonify({"ok": False, "error": "Не знайдено"}), 404
    new_val = 0 if norm["is_active"] else 1
    conn.execute(
        "UPDATE supply_norms SET is_active=?, updated_at=datetime('now','localtime') WHERE id=?",
        (new_val, norm_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "is_active": new_val})


# ─────────────────────────────────────────────────────────────
#  Видалити норму
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:norm_id>/delete", methods=["POST"])
@login_required
def delete(norm_id):
    conn = get_connection()
    assigned = conn.execute(
        "SELECT COUNT(*) FROM personnel WHERE norm_id=?", (norm_id,)
    ).fetchone()[0]
    if assigned > 0:
        conn.close()
        flash(f"Неможливо видалити: норму призначено {assigned} військовослужбовцям", "error")
        return redirect(url_for("supply_norms.index"))
    conn.execute("DELETE FROM supply_norms WHERE id=?", (norm_id,))
    conn.commit()
    log_action("delete", "supply_norms", norm_id)
    conn.close()
    flash("Норму видалено", "success")
    return redirect(url_for("supply_norms.index"))


# ─────────────────────────────────────────────────────────────
#  AJAX — позиції норми
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:norm_id>/items/add", methods=["POST"])
@login_required
def item_add(norm_id):
    conn = get_connection()
    norm = conn.execute("SELECT id FROM supply_norms WHERE id=?", (norm_id,)).fetchone()
    if not norm:
        conn.close()
        return jsonify({"ok": False, "error": "Норму не знайдено"}), 404

    data = request.get_json(silent=True) or {}
    norm_dict_id = data.get("norm_dict_id")
    quantity     = float(data.get("quantity") or 1)
    wear_years   = float(data.get("wear_years") or 0)
    category     = data.get("category", "I")
    notes        = (data.get("notes") or "").strip() or None

    if not norm_dict_id:
        conn.close()
        return jsonify({"ok": False, "error": "Оберіть найменування майна"}), 400

    # Наступний sort_order
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order),0) FROM supply_norm_items WHERE norm_id=?", (norm_id,)
    ).fetchone()[0]

    try:
        cur = conn.execute("""
            INSERT INTO supply_norm_items
                (norm_id, norm_dict_id, quantity, wear_years, category, sort_order, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (norm_id, norm_dict_id, quantity, wear_years, category, max_order + 10, notes))
        new_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.close()
        if "UNIQUE" in str(e):
            return jsonify({"ok": False, "error": "Ця позиція вже є в нормі"}), 400
        return jsonify({"ok": False, "error": str(e)}), 500

    row = conn.execute("""
        SELECT sni.*, nd.name AS item_name, nd.unit AS unit_of_measure
        FROM supply_norm_items sni
        JOIN norm_dictionary nd ON sni.norm_dict_id = nd.id
        WHERE sni.id = ?
    """, (new_id,)).fetchone()
    conn.close()
    return jsonify({"ok": True, "item": dict(row)})


@bp.route("/<int:norm_id>/items/<int:item_row_id>/edit", methods=["POST"])
@login_required
def item_edit(norm_id, item_row_id):
    conn = get_connection()
    data = request.get_json(silent=True) or {}
    quantity   = float(data.get("quantity") or 1)
    wear_years = float(data.get("wear_years") or 0)
    category   = data.get("category", "I")
    notes      = (data.get("notes") or "").strip() or None

    conn.execute("""
        UPDATE supply_norm_items
        SET quantity=?, wear_years=?, category=?, notes=?
        WHERE id=? AND norm_id=?
    """, (quantity, wear_years, category, notes, item_row_id, norm_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:norm_id>/items/<int:item_row_id>/delete", methods=["POST"])
@login_required
def item_delete(norm_id, item_row_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM supply_norm_items WHERE id=? AND norm_id=?", (item_row_id, norm_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:norm_id>/items/reorder", methods=["POST"])
@login_required
def item_reorder(norm_id):
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])  # [id1, id2, id3, ...]
    conn = get_connection()
    for i, row_id in enumerate(ids):
        conn.execute(
            "UPDATE supply_norm_items SET sort_order=? WHERE id=? AND norm_id=?",
            (i * 10, row_id, norm_id)
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  API — список норм для select
# ─────────────────────────────────────────────────────────────

@bp.route("/api/list")
@login_required
def api_list():
    conn = get_connection()
    norms = conn.execute(
        "SELECT id, name FROM supply_norms WHERE is_active=1 ORDER BY name"
    ).fetchall()
    conn.close()
    return jsonify([dict(n) for n in norms])
