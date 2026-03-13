"""
modules/settings/routes.py — Налаштування системи
Включає: підрозділи, групи, словник майна, типи документів, норма №1, реквізити
Author: White
"""
import json
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash
from core.auth import login_required, permission_required
from core.db import get_connection
from core.audit import log_action
from core.settings import get_all_settings, update_settings

bp = Blueprint("settings", __name__, url_prefix="/settings")


# ─────────────────────────────────────────────
#  ГОЛОВНА СТОРІНКА НАЛАШТУВАНЬ
# ─────────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    return render_template("settings/index.html")


# ─────────────────────────────────────────────
#  РЕКВІЗИТИ СИСТЕМИ
# ─────────────────────────────────────────────

@bp.route("/general", methods=["GET", "POST"])
@login_required
def general():
    if request.method == "POST":
        update_settings({
            "company_name":        request.form.get("company_name", ""),
            "service_name":        request.form.get("service_name", ""),
            "chief_name":          request.form.get("chief_name", ""),
            "chief_rank":          request.form.get("chief_rank", ""),
            "chief_is_tvo":        "1" if request.form.get("chief_is_tvo") else "0",
            "chief_tvo_name":      request.form.get("chief_tvo_name", ""),
            "chief_tvo_rank":      request.form.get("chief_tvo_rank", ""),
            "clerk_name":          request.form.get("clerk_name", ""),
            "clerk_rank":          request.form.get("clerk_rank", ""),
            "warehouse_chief_name": request.form.get("warehouse_chief_name", ""),
            "warehouse_chief_rank": request.form.get("warehouse_chief_rank", ""),
            "invoice_year":        request.form.get("invoice_year", "2026"),
            "invoice_suffix":      request.form.get("invoice_suffix", "РС"),
            "invoice_valid_days":  request.form.get("invoice_valid_days", "10"),
            "invoice_sequence":    request.form.get("invoice_sequence", "1"),
            "rv_suffix":           request.form.get("rv_suffix", "РВ"),
            "rv_sequence":         request.form.get("rv_sequence", "1"),
            "backup_reminder_days":request.form.get("backup_reminder_days", "3"),
        })
        flash("Налаштування збережено", "success")
        return redirect(url_for("settings.general"))
    s = get_all_settings()
    from core.settings import get_setting
    rank_mode = get_setting("rank_mode", "army")
    default_theme = s.get("default_theme", "default")
    conn = get_connection()
    rank_names = [r["name"] for r in conn.execute(
        "SELECT name FROM rank_presets WHERE mode=? AND is_active=1 ORDER BY sort_order, id",
        (rank_mode,)
    ).fetchall()]
    conn.close()
    return render_template("settings/general.html", s=s, rank_names=rank_names,
                           default_theme=default_theme)


# ─────────────────────────────────────────────
#  СТРУКТУРА ПІДРОЗДІЛІВ
# ─────────────────────────────────────────────

@bp.route("/units")
@login_required
def units():
    conn = get_connection()
    battalions = conn.execute(
        "SELECT * FROM battalions ORDER BY name"
    ).fetchall()
    units = conn.execute(
        "SELECT u.*, b.name as battalion_name FROM units u "
        "JOIN battalions b ON u.battalion_id = b.id ORDER BY b.name, u.name"
    ).fetchall()
    platoons = conn.execute(
        "SELECT p.*, u.name as unit_name FROM platoons p "
        "JOIN units u ON p.unit_id = u.id ORDER BY u.name, p.name"
    ).fetchall()
    # Кількість людей по підрозділах
    unit_counts = {r["unit_id"]: r["cnt"] for r in conn.execute(
        "SELECT unit_id, COUNT(*) as cnt FROM personnel WHERE is_active=1 GROUP BY unit_id"
    ).fetchall()}
    conn.close()
    return render_template("settings/units.html",
        battalions=[dict(r) for r in battalions],
        units=[dict(r) for r in units],
        platoons=[dict(r) for r in platoons],
        unit_counts=unit_counts,
    )


# --- Батальйони ---

@bp.route("/units/battalion/add", methods=["POST"])
@login_required
def battalion_add():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Назва не може бути порожньою", "danger")
        return redirect(url_for("settings.units"))
    conn = get_connection()
    try:
        conn.execute("INSERT INTO battalions (name) VALUES (?)", (name,))
        conn.commit()
        log_action("add", "battalions", new_data={"name": name})
        flash(f'Батальйон "{name}" додано', "success")
    except Exception:
        flash("Батальйон з такою назвою вже існує", "danger")
    conn.close()
    return redirect(url_for("settings.units"))


@bp.route("/units/battalion/<int:bid>/edit", methods=["POST"])
@login_required
def battalion_edit(bid):
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Порожня назва"}), 400
    conn = get_connection()
    old = conn.execute("SELECT name FROM battalions WHERE id=?", (bid,)).fetchone()
    conn.execute("UPDATE battalions SET name=? WHERE id=?", (name, bid))
    conn.commit()
    log_action("edit", "battalions", bid, {"name": old["name"]}, {"name": name})
    conn.close()
    return jsonify({"ok": True, "name": name})


@bp.route("/units/battalion/<int:bid>/delete", methods=["POST"])
@login_required
def battalion_delete(bid):
    conn = get_connection()
    cnt = conn.execute(
        "SELECT COUNT(*) FROM units WHERE battalion_id=?", (bid,)
    ).fetchone()[0]
    if cnt > 0:
        conn.close()
        return jsonify({"error": f"Спочатку видаліть {cnt} підрозділ(ів)"}), 400
    old = conn.execute("SELECT name FROM battalions WHERE id=?", (bid,)).fetchone()
    conn.execute("DELETE FROM battalions WHERE id=?", (bid,))
    conn.commit()
    log_action("delete", "battalions", bid, {"name": old["name"]})
    conn.close()
    return jsonify({"ok": True})


# --- Підрозділи ---

@bp.route("/units/unit/add", methods=["POST"])
@login_required
def unit_add():
    name = request.form.get("name", "").strip()
    battalion_id = request.form.get("battalion_id", type=int)
    if not name or not battalion_id:
        flash("Заповніть всі поля", "danger")
        return redirect(url_for("settings.units"))
    conn = get_connection()
    conn.execute("INSERT INTO units (battalion_id, name) VALUES (?,?)", (battalion_id, name))
    conn.commit()
    log_action("add", "units", new_data={"name": name, "battalion_id": battalion_id})
    conn.close()
    flash(f'Підрозділ "{name}" додано', "success")
    return redirect(url_for("settings.units"))


@bp.route("/units/unit/<int:uid>/edit", methods=["POST"])
@login_required
def unit_edit(uid):
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Порожня назва"}), 400
    conn = get_connection()
    old = conn.execute("SELECT name FROM units WHERE id=?", (uid,)).fetchone()
    conn.execute("UPDATE units SET name=? WHERE id=?", (name, uid))
    conn.commit()
    log_action("edit", "units", uid, {"name": old["name"]}, {"name": name})
    conn.close()
    return jsonify({"ok": True, "name": name})


@bp.route("/units/unit/<int:uid>/delete", methods=["POST"])
@login_required
def unit_delete(uid):
    conn = get_connection()
    cnt = conn.execute(
        "SELECT COUNT(*) FROM personnel WHERE unit_id=? AND is_active=1", (uid,)
    ).fetchone()[0]
    if cnt > 0:
        conn.close()
        return jsonify({"error": f"В підрозділі є {cnt} військовослужбовець(ів). Спочатку перемістіть їх."}), 400
    old = conn.execute("SELECT name FROM units WHERE id=?", (uid,)).fetchone()
    conn.execute("DELETE FROM platoons WHERE unit_id=?", (uid,))
    conn.execute("DELETE FROM units WHERE id=?", (uid,))
    conn.commit()
    log_action("delete", "units", uid, {"name": old["name"]})
    conn.close()
    return jsonify({"ok": True})


# --- Взводи ---

@bp.route("/units/platoon/add", methods=["POST"])
@login_required
def platoon_add():
    name = request.form.get("name", "").strip()
    unit_id = request.form.get("unit_id", type=int)
    if not name or not unit_id:
        flash("Заповніть всі поля", "danger")
        return redirect(url_for("settings.units"))
    conn = get_connection()
    conn.execute("INSERT INTO platoons (unit_id, name) VALUES (?,?)", (unit_id, name))
    conn.commit()
    log_action("add", "platoons", new_data={"name": name, "unit_id": unit_id})
    conn.close()
    flash(f'Взвод "{name}" додано', "success")
    return redirect(url_for("settings.units"))


@bp.route("/units/platoon/<int:pid>/edit", methods=["POST"])
@login_required
def platoon_edit(pid):
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Порожня назва"}), 400
    conn = get_connection()
    old = conn.execute("SELECT name FROM platoons WHERE id=?", (pid,)).fetchone()
    conn.execute("UPDATE platoons SET name=? WHERE id=?", (name, pid))
    conn.commit()
    log_action("edit", "platoons", pid, {"name": old["name"]}, {"name": name})
    conn.close()
    return jsonify({"ok": True, "name": name})


@bp.route("/units/platoon/<int:pid>/delete", methods=["POST"])
@login_required
def platoon_delete(pid):
    conn = get_connection()
    cnt = conn.execute(
        "SELECT COUNT(*) FROM personnel WHERE platoon_id=? AND is_active=1", (pid,)
    ).fetchone()[0]
    if cnt > 0:
        conn.close()
        return jsonify({"error": f"У взводі є {cnt} військовослужбовець(ів)"}), 400
    old = conn.execute("SELECT name FROM platoons WHERE id=?", (pid,)).fetchone()
    conn.execute("DELETE FROM platoons WHERE id=?", (pid,))
    conn.commit()
    log_action("delete", "platoons", pid, {"name": old["name"]})
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  ГРУПИ ВІЙСЬКОВОСЛУЖБОВЦІВ
# ─────────────────────────────────────────────

@bp.route("/groups")
@login_required
def groups():
    conn = get_connection()
    groups = conn.execute("SELECT * FROM groups ORDER BY id").fetchall()
    counts = {r["group_id"]: r["cnt"] for r in conn.execute(
        "SELECT group_id, COUNT(*) as cnt FROM personnel WHERE is_active=1 GROUP BY group_id"
    ).fetchall()}
    conn.close()
    return render_template("settings/groups.html",
        groups=[dict(r) for r in groups],
        counts=counts,
    )


@bp.route("/groups/add", methods=["POST"])
@login_required
def group_add():
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Порожня назва"}), 400
    conn = get_connection()
    try:
        conn.execute("INSERT INTO groups (name, type) VALUES (?, 'custom')", (name,))
        conn.commit()
        gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        log_action("add", "groups", new_data={"name": name})
        conn.close()
        return jsonify({"ok": True, "id": gid, "name": name})
    except Exception:
        conn.close()
        return jsonify({"error": "Група з такою назвою вже існує"}), 400


@bp.route("/groups/<int:gid>/edit", methods=["POST"])
@login_required
def group_edit(gid):
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Порожня назва"}), 400
    conn = get_connection()
    old = conn.execute("SELECT name, type FROM groups WHERE id=?", (gid,)).fetchone()
    if old["type"] != "custom":
        conn.close()
        return jsonify({"error": "Системну групу не можна перейменувати"}), 400
    conn.execute("UPDATE groups SET name=? WHERE id=?", (name, gid))
    conn.commit()
    log_action("edit", "groups", gid, {"name": old["name"]}, {"name": name})
    conn.close()
    return jsonify({"ok": True, "name": name})


@bp.route("/groups/<int:gid>/delete", methods=["POST"])
@login_required
def group_delete(gid):
    conn = get_connection()
    grp = conn.execute("SELECT name, type FROM groups WHERE id=?", (gid,)).fetchone()
    if not grp:
        conn.close()
        return jsonify({"error": "Групу не знайдено"}), 404
    if grp["type"] != "custom":
        conn.close()
        return jsonify({"error": "Системну групу не можна видалити"}), 400
    cnt = conn.execute(
        "SELECT COUNT(*) FROM personnel WHERE group_id=?", (gid,)
    ).fetchone()[0]
    if cnt > 0:
        conn.close()
        return jsonify({"error": f"В групі є {cnt} військовослужбовець(ів)"}), 400
    conn.execute("DELETE FROM groups WHERE id=?", (gid,))
    conn.commit()
    log_action("delete", "groups", gid, {"name": grp["name"]})
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  СЛОВНИК НОРМ (norm_dict_groups + norm_dictionary)
# ─────────────────────────────────────────────

@bp.route("/norm-dict")
@login_required
def norm_dict():
    conn = get_connection()
    groups = conn.execute(
        "SELECT * FROM norm_dict_groups ORDER BY sort_order, id"
    ).fetchall()
    items_rows = conn.execute(
        "SELECT nd.*, ndg.name AS group_name FROM norm_dictionary nd "
        "JOIN norm_dict_groups ndg ON nd.group_id = ndg.id "
        "ORDER BY ndg.sort_order, nd.sort_order, nd.id"
    ).fetchall()
    # Підраховуємо скільки позицій item_dictionary прив'язано до кожної норми
    linked = {r["norm_dict_id"]: r["cnt"] for r in conn.execute(
        "SELECT norm_dict_id, COUNT(*) as cnt FROM item_dictionary "
        "WHERE norm_dict_id IS NOT NULL GROUP BY norm_dict_id"
    ).fetchall()}
    # Кількість складових для кожного комплекту
    components_count = {r["parent_id"]: r["cnt"] for r in conn.execute(
        "SELECT parent_id, COUNT(*) as cnt FROM norm_dict_components GROUP BY parent_id"
    ).fetchall()}
    conn.close()
    return render_template("settings/norm_dict.html",
                           groups=[dict(r) for r in groups],
                           items=[dict(r) for r in items_rows],
                           linked=linked,
                           components_count=components_count)


@bp.route("/norm-dict/group/add", methods=["POST"])
@login_required
def norm_dict_group_add():
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Назва не може бути порожньою"}), 400
    conn = get_connection()
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM norm_dict_groups").fetchone()[0]
    try:
        conn.execute("INSERT INTO norm_dict_groups (name, sort_order) VALUES (?,?)",
                     (name, max_order + 10))
        conn.commit()
        gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({"ok": True, "id": gid, "name": name})
    except Exception:
        conn.close()
        return jsonify({"error": "Група з такою назвою вже існує"}), 400


@bp.route("/norm-dict/group/<int:gid>/edit", methods=["POST"])
@login_required
def norm_dict_group_edit(gid):
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Порожня назва"}), 400
    conn = get_connection()
    conn.execute("UPDATE norm_dict_groups SET name=? WHERE id=?", (name, gid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "name": name})


@bp.route("/norm-dict/group/<int:gid>/delete", methods=["POST"])
@login_required
def norm_dict_group_delete(gid):
    conn = get_connection()
    cnt = conn.execute("SELECT COUNT(*) FROM norm_dictionary WHERE group_id=?", (gid,)).fetchone()[0]
    if cnt > 0:
        conn.close()
        return jsonify({"error": f"Спочатку видаліть або перемістіть {cnt} позицій у цій групі"}), 400
    conn.execute("DELETE FROM norm_dict_groups WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/norm-dict/item/<int:iid>/api", methods=["GET"])
@login_required
def norm_dict_item_api(iid):
    """API: повернути дані однієї позиції словника норм (unit)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, name, unit, note_refs FROM norm_dictionary WHERE id=?",
        (iid,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Не знайдено"}), 404
    return jsonify(dict(row))


@bp.route("/norm-dict/item/add", methods=["POST"])
@login_required
def norm_dict_item_add():
    name       = request.form.get("name", "").strip()
    group_id   = request.form.get("group_id", type=int)
    unit       = request.form.get("unit", "шт").strip() or "шт"
    if not name or not group_id:
        return jsonify({"error": "Заповніть всі поля"}), 400
    conn = get_connection()
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM norm_dictionary WHERE group_id=?",
                             (group_id,)).fetchone()[0]
    try:
        conn.execute(
            """INSERT INTO norm_dictionary (group_id, name, unit, sort_order)
               VALUES (?,?,?,?)""",
            (group_id, name, unit, max_order + 10))
        conn.commit()
        iid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        group_name = conn.execute("SELECT name FROM norm_dict_groups WHERE id=?", (group_id,)).fetchone()["name"]
        conn.close()
        return jsonify({"ok": True, "id": iid, "name": name, "unit": unit, "group_id": group_id, "group_name": group_name})
    except Exception:
        conn.close()
        return jsonify({"error": "Позиція з такою назвою вже існує"}), 400


@bp.route("/norm-dict/item/<int:iid>/edit", methods=["POST"])
@login_required
def norm_dict_item_edit(iid):
    name       = request.form.get("name", "").strip()
    group_id   = request.form.get("group_id", type=int)
    unit       = request.form.get("unit", "шт").strip() or "шт"
    if not name or not group_id:
        return jsonify({"error": "Заповніть всі поля"}), 400
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE norm_dictionary SET name=?, group_id=?, unit=? WHERE id=?",
            (name, group_id, unit, iid))
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "name": name, "unit": unit, "group_id": group_id})
    except Exception:
        conn.close()
        return jsonify({"error": "Позиція з такою назвою вже існує"}), 400


@bp.route("/norm-dict/item/<int:iid>/delete", methods=["POST"])
@login_required
def norm_dict_item_delete(iid):
    conn = get_connection()
    linked = conn.execute("SELECT COUNT(*) FROM item_dictionary WHERE norm_dict_id=?", (iid,)).fetchone()[0]
    if linked > 0:
        conn.close()
        return jsonify({"error": f"Прив'язано {linked} позицій у словнику майна — спочатку відв'яжіть"}), 400
    conn.execute("DELETE FROM norm_dictionary WHERE id=?", (iid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  СКЛАДОВІ КОМПЛЕКТУ (norm_dict_components)
# ─────────────────────────────────────────────

@bp.route("/norm-dict/item/<int:iid>/components", methods=["GET"])
@login_required
def norm_dict_components_get(iid):
    """Повернути список складових комплекту."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT c.id, c.child_id, c.qty, c.sort_order,
                  nd.name AS child_name, nd.unit AS child_unit
           FROM norm_dict_components c
           JOIN norm_dictionary nd ON nd.id = c.child_id
           WHERE c.parent_id = ?
           ORDER BY c.sort_order, c.id""",
        (iid,)
    ).fetchall()
    conn.close()
    return jsonify({"ok": True, "components": [dict(r) for r in rows]})


@bp.route("/norm-dict/item/<int:iid>/components/add", methods=["POST"])
@login_required
def norm_dict_components_add(iid):
    """Додати складову до комплекту."""
    data = request.get_json(silent=True) or {}
    child_id = int(data.get("child_id", 0))
    qty      = float(data.get("qty", 1))
    if not child_id or child_id == iid:
        return jsonify({"error": "Невірна складова"}), 400
    conn = get_connection()
    try:
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0) FROM norm_dict_components WHERE parent_id=?", (iid,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO norm_dict_components (parent_id, child_id, qty, sort_order) VALUES (?,?,?,?)",
            (iid, child_id, qty, max_order + 10)
        )
        # Позначити батька як комплект
        conn.execute("UPDATE norm_dictionary SET has_components=1 WHERE id=?", (iid,))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    return jsonify({"ok": True})


@bp.route("/norm-dict/component/<int:cid>/edit", methods=["POST"])
@login_required
def norm_dict_component_edit(cid):
    """Оновити кількість складової."""
    data = request.get_json(silent=True) or {}
    qty  = float(data.get("qty", 1))
    conn = get_connection()
    conn.execute("UPDATE norm_dict_components SET qty=? WHERE id=?", (qty, cid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/norm-dict/component/<int:cid>/delete", methods=["POST"])
@login_required
def norm_dict_component_delete(cid):
    """Видалити складову і якщо більше немає — зняти позначку комплекту."""
    conn = get_connection()
    parent = conn.execute(
        "SELECT parent_id FROM norm_dict_components WHERE id=?", (cid,)
    ).fetchone()
    if not parent:
        conn.close()
        return jsonify({"error": "Не знайдено"}), 404
    parent_id = parent["parent_id"]
    conn.execute("DELETE FROM norm_dict_components WHERE id=?", (cid,))
    remaining = conn.execute(
        "SELECT COUNT(*) FROM norm_dict_components WHERE parent_id=?", (parent_id,)
    ).fetchone()[0]
    if remaining == 0:
        conn.execute("UPDATE norm_dictionary SET has_components=0 WHERE id=?", (parent_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  СЛОВНИК МАЙНА
# ─────────────────────────────────────────────

@bp.route("/items")
@login_required
def items():
    conn = get_connection()
    items = conn.execute(
        """SELECT d.*, nd.name AS norm_dict_name, ndg.name AS norm_dict_group_name
           FROM item_dictionary d
           LEFT JOIN norm_dictionary nd ON d.norm_dict_id = nd.id
           LEFT JOIN norm_dict_groups ndg ON nd.group_id = ndg.id
           ORDER BY d.name"""
    ).fetchall()
    _nd_rows = conn.execute("""
        SELECT g.id AS group_id, g.name AS group_name, g.sort_order,
               nd.id, nd.name, nd.unit,
               nd.sort_order AS item_order
        FROM norm_dict_groups g
        JOIN norm_dictionary nd ON nd.group_id = g.id
        WHERE g.is_active=1 AND nd.is_active=1
        ORDER BY g.sort_order, nd.sort_order
    """).fetchall()
    conn.close()
    # Групуємо для optgroup у шаблоні
    _groups_dict = {}
    for r in _nd_rows:
        gid = r["group_id"]
        if gid not in _groups_dict:
            _groups_dict[gid] = {"id": gid, "name": r["group_name"], "norms": []}
        _groups_dict[gid]["norms"].append({
            "id": r["id"], "name": r["name"],
            "unit": r["unit"] or "шт",
        })
    norm_groups = list(_groups_dict.values())
    return render_template("settings/items.html",
                           items=[dict(r) for r in items],
                           norm_groups=norm_groups)


@bp.route("/items/search")
@login_required
def items_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"items": []})
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, unit_of_measure, is_inventory, season, gender, "
        "officer_norm_qty, officer_wear_period, soldier_norm_qty, soldier_wear_period "
        "FROM item_dictionary WHERE name LIKE ? ORDER BY name LIMIT 20",
        (f"%{q}%",)
    ).fetchall()
    conn.close()
    return jsonify({"items": [dict(r) for r in rows]})


@bp.route("/items/add", methods=["POST"])
@login_required
def item_add():
    data = _item_from_form(request.form)
    if not data["name"]:
        return jsonify({"error": "Назва не може бути порожньою"}), 400
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO item_dictionary
            (name, unit_of_measure, is_inventory, has_serial_number,
             needs_passport, needs_exploitation_act, season, gender,
             officer_norm_qty, officer_wear_period,
             soldier_norm_qty, soldier_wear_period, norm_dict_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (data["name"], data["unit_of_measure"], data["is_inventory"],
              data["has_serial_number"], data["needs_passport"], data["needs_exploitation_act"],
              data["season"], data["gender"],
              data["officer_norm_qty"], data["officer_wear_period"],
              data["soldier_norm_qty"], data["soldier_wear_period"],
              data["norm_dict_id"]))
        conn.commit()
        iid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        log_action("add", "item_dictionary", new_data=data)
        conn.close()
        return jsonify({"ok": True, "id": iid})
    except Exception:
        conn.close()
        return jsonify({"error": "Позиція з такою назвою вже існує"}), 400


@bp.route("/items/<int:iid>/edit", methods=["POST"])
@login_required
def item_edit(iid):
    data = _item_from_form(request.form)
    if not data["name"]:
        return jsonify({"error": "Назва не може бути порожньою"}), 400
    conn = get_connection()
    old = dict(conn.execute("SELECT * FROM item_dictionary WHERE id=?", (iid,)).fetchone())
    conn.execute("""
        UPDATE item_dictionary SET
            name=?, unit_of_measure=?, is_inventory=?, has_serial_number=?,
            needs_passport=?, needs_exploitation_act=?, season=?, gender=?,
            officer_norm_qty=?, officer_wear_period=?,
            soldier_norm_qty=?, soldier_wear_period=?,
            norm_dict_id=?,
            updated_at=datetime('now','localtime')
        WHERE id=?
    """, (data["name"], data["unit_of_measure"], data["is_inventory"],
          data["has_serial_number"], data["needs_passport"], data["needs_exploitation_act"],
          data["season"], data["gender"],
          data["officer_norm_qty"], data["officer_wear_period"],
          data["soldier_norm_qty"], data["soldier_wear_period"],
          data["norm_dict_id"], iid))
    conn.commit()
    log_action("edit", "item_dictionary", iid, old, data)
    conn.close()
    return jsonify({"ok": True})


@bp.route("/items/<int:iid>/delete", methods=["POST"])
@login_required
def item_delete(iid):
    conn = get_connection()
    cnt = conn.execute(
        "SELECT COUNT(*) FROM personnel_items WHERE item_id=?", (iid,)
    ).fetchone()[0]
    if cnt > 0:
        conn.close()
        return jsonify({"error": f"Позиція використовується в {cnt} картках — видалити неможливо"}), 400
    old = dict(conn.execute("SELECT * FROM item_dictionary WHERE id=?", (iid,)).fetchone())
    conn.execute("DELETE FROM item_dictionary WHERE id=?", (iid,))
    conn.commit()
    log_action("delete", "item_dictionary", iid, old)
    conn.close()
    return jsonify({"ok": True})


def _item_from_form(form) -> dict:
    norm_dict_id = form.get("norm_dict_id", "").strip()
    return {
        "name":               form.get("name", "").strip(),
        "unit_of_measure":    form.get("unit_of_measure", "шт").strip(),
        "is_inventory":       1 if form.get("is_inventory") else 0,
        "has_serial_number":  1 if form.get("has_serial_number") else 0,
        "needs_passport":     1 if form.get("needs_passport") else 0,
        "needs_exploitation_act": 1 if form.get("needs_exploitation_act") else 0,
        "season":             form.get("season", "demi"),
        "gender":             form.get("gender", "unisex"),
        "officer_norm_qty":   float(form.get("officer_norm_qty") or 1),
        "officer_wear_period":int(form.get("officer_wear_period") or 0),
        "soldier_norm_qty":   float(form.get("soldier_norm_qty") or 1),
        "soldier_wear_period":int(form.get("soldier_wear_period") or 0),
        "norm_dict_id":       int(norm_dict_id) if norm_dict_id else None,
    }


# ─────────────────────────────────────────────
#  ТИПИ ДОКУМЕНТІВ
# ─────────────────────────────────────────────

@bp.route("/doctypes")
@login_required
def doctypes():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM document_types ORDER BY is_system DESC, name").fetchall()
    conn.close()
    return render_template("settings/doctypes.html", doctypes=[dict(r) for r in rows])


@bp.route("/doctypes/add", methods=["POST"])
@login_required
def doctype_add():
    name = request.form.get("name", "").strip()
    short = request.form.get("short_name", "").strip()
    if not name:
        return jsonify({"error": "Назва не може бути порожньою"}), 400
    conn = get_connection()
    try:
        conn.execute("INSERT INTO document_types (name, short_name) VALUES (?,?)", (name, short))
        conn.commit()
        did = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        log_action("add", "document_types", new_data={"name": name})
        conn.close()
        return jsonify({"ok": True, "id": did, "name": name, "short_name": short})
    except Exception:
        conn.close()
        return jsonify({"error": "Тип з такою назвою вже існує"}), 400


@bp.route("/doctypes/<int:did>/edit", methods=["POST"])
@login_required
def doctype_edit(did):
    name = request.form.get("name", "").strip()
    short = request.form.get("short_name", "").strip()
    conn = get_connection()
    dt = conn.execute("SELECT * FROM document_types WHERE id=?", (did,)).fetchone()
    if dt["is_system"]:
        conn.close()
        return jsonify({"error": "Системний тип не можна редагувати"}), 400
    conn.execute("UPDATE document_types SET name=?, short_name=? WHERE id=?", (name, short, did))
    conn.commit()
    log_action("edit", "document_types", did, dict(dt), {"name": name, "short_name": short})
    conn.close()
    return jsonify({"ok": True})


@bp.route("/doctypes/<int:did>/delete", methods=["POST"])
@login_required
def doctype_delete(did):
    conn = get_connection()
    dt = conn.execute("SELECT * FROM document_types WHERE id=?", (did,)).fetchone()
    if dt["is_system"]:
        conn.close()
        return jsonify({"error": "Системний тип не можна видалити"}), 400
    conn.execute("DELETE FROM document_types WHERE id=?", (did,))
    conn.commit()
    log_action("delete", "document_types", did, dict(dt))
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  КАРТКА ПІДРОЗДІЛУ
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  КАРТОТЕКА ПІДРОЗДІЛІВ (публічна сторінка, /units/)
# ─────────────────────────────────────────────

@bp.route("/units-list")
@login_required
def units_list():
    """Зручна сторінка-список підрозділів з картками (аналог картотеки о/с)."""
    conn = get_connection()
    battalions = conn.execute("SELECT * FROM battalions ORDER BY name").fetchall()
    units = conn.execute(
        """SELECT u.*, b.name as battalion_name,
                  (SELECT COUNT(*) FROM personnel p WHERE p.unit_id=u.id AND p.is_active=1) as active_count,
                  (SELECT COUNT(*) FROM personnel p WHERE p.unit_id=u.id) as total_count
           FROM units u
           JOIN battalions b ON u.battalion_id = b.id
           ORDER BY b.name, u.name"""
    ).fetchall()
    # Відповідальні особи — з актуальними даними з personnel
    responsible_map = {}
    for r in conn.execute(
        """SELECT ur.id, ur.unit_id, ur.role_name, ur.is_active,
                  p.id as personnel_id,
                  p.last_name, p.first_name, p.middle_name,
                  p.rank, p.position, p.phone
           FROM unit_responsible ur
           JOIN personnel p ON ur.personnel_id = p.id
           WHERE ur.is_active=1
           ORDER BY ur.unit_id, ur.id"""
    ).fetchall():
        responsible_map.setdefault(r["unit_id"], []).append(dict(r))

    conn.close()
    return render_template(
        "settings/units_list.html",
        battalions=[dict(b) for b in battalions],
        units=[dict(u) for u in units],
        responsible_map=responsible_map,
        role_labels=ROLE_LABELS,
    )


ROLE_LABELS = {
    "commander":       "Командир",
    "deputy_commander":"Заступник командира",
    "supply_sergeant": "Сержант із МЗ",
    "other":           "Інша посада",
}

@bp.route("/units/<int:uid>/card")
@login_required
def unit_card(uid):
    conn = get_connection()
    unit = conn.execute(
        "SELECT u.*, b.name as battalion_name FROM units u "
        "JOIN battalions b ON u.battalion_id = b.id WHERE u.id=?", (uid,)
    ).fetchone()
    if not unit:
        conn.close()
        flash("Підрозділ не знайдено", "error")
        return redirect(url_for("settings.units"))

    # Відповідальні особи — JOIN з personnel щоб мати актуальні дані
    responsible = conn.execute(
        """SELECT ur.id, ur.unit_id, ur.role_name, ur.is_active,
                  p.id as personnel_id,
                  p.last_name, p.first_name, p.middle_name,
                  p.rank, p.position, p.phone, p.is_active as p_is_active
           FROM unit_responsible ur
           JOIN personnel p ON ur.personnel_id = p.id
           WHERE ur.unit_id=?
           ORDER BY ur.is_active DESC, ur.id""",
        (uid,)
    ).fetchall()

    # Особовий склад підрозділу
    personnel = conn.execute(
        """SELECT p.id, p.last_name, p.first_name, p.middle_name, p.rank, p.position,
                  p.is_active, g.name as group_name
           FROM personnel p
           LEFT JOIN groups g ON p.group_id = g.id
           WHERE p.unit_id=?
           ORDER BY p.is_active DESC, p.last_name, p.first_name""",
        (uid,)
    ).fetchall()

    # Майно підрозділу
    unit_items = conn.execute(
        """SELECT ui.*, d.name as item_name, d.unit_of_measure, d.is_inventory
           FROM unit_items ui
           JOIN item_dictionary d ON ui.item_id = d.id
           WHERE ui.unit_id=? AND ui.status='active'
           ORDER BY d.name""",
        (uid,)
    ).fetchall()

    # Список ВСІХ активних о/с для вибору відповідальних (не тільки цього підрозділу)
    personnel_list = conn.execute(
        """SELECT p.id, p.last_name, p.first_name, p.middle_name, p.rank, p.position,
                  u.name as unit_name
           FROM personnel p
           LEFT JOIN units u ON p.unit_id = u.id
           WHERE p.is_active=1
           ORDER BY p.last_name, p.first_name""",
    ).fetchall()

    # Які personnel_id вже є відповідальними в цьому підрозділі
    existing_resp_pids = {r["personnel_id"] for r in responsible}

    conn.close()
    return render_template(
        "settings/unit_card.html",
        unit=unit,
        responsible=[dict(r) for r in responsible],
        personnel=[dict(r) for r in personnel],
        unit_items=[dict(r) for r in unit_items],
        personnel_list=[dict(r) for r in personnel_list],
        existing_resp_pids=existing_resp_pids,
        role_labels=ROLE_LABELS,
    )


@bp.route("/units/<int:uid>/responsible/add", methods=["POST"])
@login_required
def unit_responsible_add(uid):
    conn = get_connection()
    unit = conn.execute("SELECT id FROM units WHERE id=?", (uid,)).fetchone()
    if not unit:
        conn.close()
        return jsonify({"error": "Підрозділ не знайдено"}), 404

    role_name    = request.form.get("role_name", "other")
    personnel_id = request.form.get("personnel_id", type=int)

    if not personnel_id:
        conn.close()
        return jsonify({"error": "Оберіть військовослужбовця з картотеки"}), 400

    p = conn.execute(
        "SELECT id, last_name, first_name, middle_name, rank, position, phone FROM personnel WHERE id=?",
        (personnel_id,)
    ).fetchone()
    if not p:
        conn.close()
        return jsonify({"error": "Військовослужбовця не знайдено"}), 404

    # Перевірка дублів у цьому підрозділі
    dup = conn.execute(
        "SELECT id FROM unit_responsible WHERE unit_id=? AND personnel_id=?",
        (uid, personnel_id)
    ).fetchone()
    if dup:
        conn.close()
        return jsonify({"error": "Цей військовослужбовець вже є відповідальною особою підрозділу"}), 400

    full_name = f"{p['last_name']} {p['first_name']} {p['middle_name'] or ''}".strip()
    rank = p["rank"] or ""

    conn.execute(
        """INSERT INTO unit_responsible (unit_id, role_name, rank, full_name, personnel_id)
           VALUES (?,?,?,?,?)""",
        (uid, role_name, rank, full_name, personnel_id)
    )
    conn.commit()
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log_action("add", "unit_responsible", rid,
               new_data={"unit_id": uid, "role_name": role_name, "personnel_id": personnel_id})
    conn.close()
    return jsonify({
        "ok": True, "id": rid,
        "role_name": role_name,
        "role_label": ROLE_LABELS.get(role_name, role_name),
        "personnel_id": personnel_id,
        "rank": rank,
        "full_name": full_name,
        "position": p["position"] or "",
        "phone": p["phone"] or "",
    })


@bp.route("/units/responsible/<int:rid>/edit", methods=["POST"])
@login_required
def unit_responsible_edit(rid):
    """Редагування — тільки зміна ролі (дані беруться з personnel)."""
    role_name = request.form.get("role_name", "other")
    conn = get_connection()
    old = conn.execute("SELECT * FROM unit_responsible WHERE id=?", (rid,)).fetchone()
    if not old:
        conn.close()
        return jsonify({"error": "not found"}), 404
    conn.execute(
        "UPDATE unit_responsible SET role_name=? WHERE id=?",
        (role_name, rid)
    )
    conn.commit()
    log_action("edit", "unit_responsible", rid, dict(old), {"role_name": role_name})
    conn.close()
    return jsonify({"ok": True, "role_label": ROLE_LABELS.get(role_name, role_name)})


@bp.route("/units/responsible/<int:rid>/toggle", methods=["POST"])
@login_required
def unit_responsible_toggle(rid):
    conn = get_connection()
    r = conn.execute("SELECT is_active FROM unit_responsible WHERE id=?", (rid,)).fetchone()
    if not r:
        conn.close()
        return jsonify({"error": "not found"}), 404
    new_val = 0 if r["is_active"] else 1
    conn.execute("UPDATE unit_responsible SET is_active=? WHERE id=?", (new_val, rid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "is_active": new_val})


@bp.route("/units/responsible/<int:rid>/delete", methods=["POST"])
@login_required
def unit_responsible_delete(rid):
    conn = get_connection()
    old = conn.execute("SELECT * FROM unit_responsible WHERE id=?", (rid,)).fetchone()
    if not old:
        conn.close()
        return jsonify({"error": "not found"}), 404
    conn.execute("DELETE FROM unit_responsible WHERE id=?", (rid,))
    conn.commit()
    log_action("delete", "unit_responsible", rid, dict(old))
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  ЗВАННЯ
# ─────────────────────────────────────────────

RANK_CATEGORY_LABELS = {
    "enlisted": "Рядовий склад",
    "nco":      "Сержантський і старшинський склад",
    "officer":  "Офіцерський склад",
}
RANK_SUBCATEGORY_LABELS = {
    "":       "",
    "junior": "Молодший",
    "senior": "Старший",
    "higher": "Вищий",
}
RANK_MODE_LABELS = {
    "army": "Армійські",
    "navy": "Корабельні",
    "nato": "НАТО",
}


@bp.route("/ranks")
@login_required
def ranks():
    from core.settings import get_setting
    active_mode = get_setting("rank_mode", "army")
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM rank_presets ORDER BY mode, sort_order, id"
    ).fetchall()
    conn.close()
    return render_template(
        "settings/ranks.html",
        ranks=[dict(r) for r in rows],
        active_mode=active_mode,
        category_labels=RANK_CATEGORY_LABELS,
        subcategory_labels=RANK_SUBCATEGORY_LABELS,
        mode_labels=RANK_MODE_LABELS,
    )


@bp.route("/ranks/set-mode", methods=["POST"])
@login_required
def ranks_set_mode():
    mode = request.form.get("mode", "army")
    if mode not in ("army", "navy", "nato"):
        return jsonify({"error": "Невідомий режим"}), 400
    from core.settings import update_settings
    update_settings({"rank_mode": mode})
    return jsonify({"ok": True})


@bp.route("/ranks/add", methods=["POST"])
@login_required
def rank_add():
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Назва не може бути порожньою"}), 400
    short_name = request.form.get("short_name", "").strip()
    category   = request.form.get("category", "enlisted")
    subcategory= request.form.get("subcategory", "")
    mode       = request.form.get("mode", "army")
    insignia   = request.form.get("insignia", "").strip()

    conn = get_connection()
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order),0) FROM rank_presets WHERE mode=? AND category=?",
        (mode, category)
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO rank_presets (name, short_name, category, subcategory, mode, sort_order, insignia, is_active, is_custom)
           VALUES (?,?,?,?,?,?,?,1,1)""",
        (name, short_name, category, subcategory, mode, max_order + 1, insignia)
    )
    conn.commit()
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log_action("add", "rank_presets", new_data={"name": name, "mode": mode})
    conn.close()
    return jsonify({"ok": True, "id": rid})


@bp.route("/ranks/<int:rid>/edit", methods=["POST"])
@login_required
def rank_edit(rid):
    conn = get_connection()
    rank = conn.execute("SELECT * FROM rank_presets WHERE id=?", (rid,)).fetchone()
    if not rank:
        conn.close()
        return jsonify({"error": "not found"}), 404
    name       = request.form.get("name", "").strip() or rank["name"]
    short_name = request.form.get("short_name", "").strip()
    category   = request.form.get("category", rank["category"])
    subcategory= request.form.get("subcategory", rank["subcategory"])
    insignia   = request.form.get("insignia", rank["insignia"]).strip()
    conn.execute(
        "UPDATE rank_presets SET name=?, short_name=?, category=?, subcategory=?, insignia=? WHERE id=?",
        (name, short_name, category, subcategory, insignia, rid)
    )
    conn.commit()
    log_action("edit", "rank_presets", rid, dict(rank), {"name": name})
    conn.close()
    return jsonify({"ok": True})


@bp.route("/ranks/<int:rid>/toggle", methods=["POST"])
@login_required
def rank_toggle(rid):
    conn = get_connection()
    r = conn.execute("SELECT is_active FROM rank_presets WHERE id=?", (rid,)).fetchone()
    if not r:
        conn.close()
        return jsonify({"error": "not found"}), 404
    new_val = 0 if r["is_active"] else 1
    conn.execute("UPDATE rank_presets SET is_active=? WHERE id=?", (new_val, rid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "is_active": new_val})


@bp.route("/ranks/<int:rid>/delete", methods=["POST"])
@login_required
def rank_delete(rid):
    conn = get_connection()
    r = conn.execute("SELECT * FROM rank_presets WHERE id=?", (rid,)).fetchone()
    if not r:
        conn.close()
        return jsonify({"error": "not found"}), 404
    if not r["is_custom"]:
        conn.close()
        return jsonify({"error": "Системне звання не можна видалити. Можна лише вимкнути."}), 400
    conn.execute("DELETE FROM rank_presets WHERE id=?", (rid,))
    conn.commit()
    log_action("delete", "rank_presets", rid, dict(r))
    conn.close()
    return jsonify({"ok": True})


@bp.route("/ranks/reorder", methods=["POST"])
@login_required
def ranks_reorder():
    """Зберегти новий порядок: [{id, sort_order}, ...]"""
    data = request.get_json(silent=True) or []
    conn = get_connection()
    for item in data:
        conn.execute("UPDATE rank_presets SET sort_order=? WHERE id=?",
                     (item.get("sort_order", 0), item.get("id")))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  РЕЗЕРВНІ КОПІЇ
# ─────────────────────────────────────────────

@bp.route("/backup")
@login_required
def backup():
    from core.backup import get_backup_list
    backups = get_backup_list()
    return render_template("settings/backup.html", backups=backups)


@bp.route("/backup/create", methods=["POST"])
@login_required
def backup_create():
    from core.backup import manual_backup
    try:
        dest = manual_backup()
        flash(f"Резервну копію створено: {dest.name}", "success")
    except Exception as e:
        flash(f"Помилка при створенні бекапу: {e}", "danger")
    return redirect(url_for("settings.backup"))


# ─────────────────────────────────────────────
#  КОРИСТУВАЧІ
# ─────────────────────────────────────────────

PERMISSIONS_LIST = [
    ("personnel",  "Особовий склад",     "bi-people-fill"),
    ("warehouse",  "Склад",              "bi-box-seam-fill"),
    ("invoices",   "Накладні / РВ",      "bi-receipt"),
    ("reports",    "Звіти",              "bi-file-earmark-bar-graph"),
    ("settings",   "Налаштування",       "bi-gear-fill"),
    ("plugins",    "Модулі розширень",   "bi-puzzle-fill"),
]


@bp.route("/users")
@login_required
def users():
    conn = get_connection()
    users_list = conn.execute(
        """SELECT u.*, r.name as role_name
           FROM users u JOIN roles r ON u.role_id = r.id
           ORDER BY u.id"""
    ).fetchall()
    roles_list = conn.execute("SELECT * FROM roles ORDER BY id").fetchall()
    conn.close()
    return render_template(
        "settings/users.html",
        users=[dict(u) for u in users_list],
        roles=[dict(r) for r in roles_list],
    )


@bp.route("/users/add", methods=["POST"])
@login_required
def user_add():
    from core.auth import create_user
    username  = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    password  = request.form.get("password", "").strip()
    role_id   = request.form.get("role_id", type=int)

    if not username or not full_name or not password or not role_id:
        flash("Заповніть всі поля", "danger")
        return redirect(url_for("settings.users"))

    conn = get_connection()
    exists = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if exists:
        flash(f"Логін «{username}» вже зайнятий", "danger")
        return redirect(url_for("settings.users"))

    try:
        create_user(username, password, full_name, role_id)
        log_action("user_add", f"Створено користувача: {username}")
        flash(f"Користувача «{full_name}» створено", "success")
    except Exception as e:
        flash(f"Помилка: {e}", "danger")
    return redirect(url_for("settings.users"))


@bp.route("/users/<int:uid>/edit", methods=["POST"])
@login_required
def user_edit(uid):
    from flask import session as sess
    full_name = request.form.get("full_name", "").strip()
    role_id   = request.form.get("role_id", type=int)
    password  = request.form.get("password", "").strip()

    if not full_name or not role_id:
        return jsonify({"error": "Заповніть всі поля"}), 400

    conn = get_connection()
    if password:
        from core.auth import hash_password
        conn.execute(
            "UPDATE users SET full_name=?, role_id=?, password_hash=? WHERE id=?",
            (full_name, role_id, hash_password(password), uid)
        )
    else:
        conn.execute(
            "UPDATE users SET full_name=?, role_id=? WHERE id=?",
            (full_name, role_id, uid)
        )
    conn.commit()
    conn.close()
    log_action("user_edit", f"Редагування користувача id={uid}")
    return jsonify({"ok": True})


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@login_required
def user_toggle(uid):
    from flask import session as sess
    # Не можна деактивувати себе
    if uid == sess.get("user_id"):
        return jsonify({"error": "Не можна деактивувати власний акаунт"}), 400

    conn = get_connection()
    row = conn.execute("SELECT is_active FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Не знайдено"}), 404
    new_state = 0 if row["is_active"] else 1
    conn.execute("UPDATE users SET is_active=? WHERE id=?", (new_state, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "is_active": new_state})


@bp.route("/users/<int:uid>/delete", methods=["POST"])
@login_required
def user_delete(uid):
    from flask import session as sess
    if uid == sess.get("user_id"):
        return jsonify({"error": "Не можна видалити власний акаунт"}), 400

    conn = get_connection()
    row = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Не знайдено"}), 404
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    log_action("user_delete", f"Видалено користувача: {row['username']}")
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  РОЛІ / ГРУПИ ДОСТУПУ
# ─────────────────────────────────────────────

@bp.route("/roles")
@login_required
def roles():
    from core.auth import get_all_roles
    roles_list = get_all_roles()
    # Підрахувати кількість юзерів на ролі
    conn = get_connection()
    counts = {r["role_id"]: r["cnt"] for r in
              conn.execute("SELECT role_id, COUNT(*) as cnt FROM users GROUP BY role_id").fetchall()}
    conn.close()
    for r in roles_list:
        r["user_count"] = counts.get(r["id"], 0)
        try:
            r["perms_parsed"] = json.loads(r["permissions"])
        except Exception:
            r["perms_parsed"] = {}
    return render_template("settings/roles.html",
                           roles=roles_list,
                           permissions_list=PERMISSIONS_LIST)


@bp.route("/roles/add", methods=["POST"])
@login_required
def role_add():
    from core.auth import create_role
    name = request.form.get("name", "").strip()
    if not name:
        flash("Введіть назву ролі", "danger")
        return redirect(url_for("settings.roles"))

    perms = {}
    if request.form.get("perm_all"):
        perms["all"] = True
    else:
        for key, _, _ in PERMISSIONS_LIST:
            val = request.form.get(f"perm_{key}")
            if val == "write":
                perms[key] = True
            elif val == "read":
                perms[key] = "read"

    conn = get_connection()
    exists = conn.execute("SELECT id FROM roles WHERE name=?", (name,)).fetchone()
    conn.close()
    if exists:
        flash(f"Роль «{name}» вже існує", "danger")
        return redirect(url_for("settings.roles"))

    create_role(name, perms)
    log_action("role_add", f"Створено роль: {name}")
    flash(f"Роль «{name}» створено", "success")
    return redirect(url_for("settings.roles"))


@bp.route("/roles/<int:rid>/edit", methods=["POST"])
@login_required
def role_edit(rid):
    from core.auth import update_role
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Введіть назву"}), 400

    perms = {}
    if request.form.get("perm_all"):
        perms["all"] = True
    else:
        for key, _, _ in PERMISSIONS_LIST:
            val = request.form.get(f"perm_{key}")
            if val == "write":
                perms[key] = True
            elif val == "read":
                perms[key] = "read"

    update_role(rid, name, perms)
    log_action("role_edit", f"Редагування ролі id={rid}")
    return jsonify({"ok": True})


@bp.route("/roles/<int:rid>/delete", methods=["POST"])
@login_required
def role_delete(rid):
    conn = get_connection()
    row = conn.execute("SELECT name FROM roles WHERE id=?", (rid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Не знайдено"}), 404
    cnt = conn.execute("SELECT COUNT(*) FROM users WHERE role_id=?", (rid,)).fetchone()[0]
    if cnt > 0:
        conn.close()
        return jsonify({"error": f"Роль використовується {cnt} користувачами"}), 400
    conn.execute("DELETE FROM roles WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    log_action("role_delete", f"Видалено роль: {row['name']}")
    return jsonify({"ok": True})


@bp.route("/default-theme", methods=["POST"])
@login_required
def default_theme():
    theme = request.form.get("theme", "default")
    if theme not in ("default", "dark", "zsu"):
        theme = "default"
    conn = get_connection()
    conn.execute("UPDATE settings SET value=? WHERE key='default_theme'", (theme,))
    conn.commit()
    conn.close()
    flash("Тему за замовченням збережено", "success")
    return redirect(url_for("settings.general"))
