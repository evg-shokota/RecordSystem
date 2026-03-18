"""
modules/doc_templates/routes.py — Шаблони документів
CRUD + WYSIWYG редактор з live preview (side-by-side)
Групи, системні шаблони (захищені від видалення), копіювання, set-default
Author: White
"""
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from core.auth import login_required
from core.db import get_connection
from core.renderer import render_demo as _render_with_demo_data, get_template_html

bp = Blueprint("doc_templates", __name__, url_prefix="/doc-templates")

# ─────────────────────────────────────────────────────────────
#  Довідник шорткодів
# ─────────────────────────────────────────────────────────────

SHORTCODES = {
    "Документ": [
        ("{{invoice_number}}",   "Номер документа"),
        ("{{invoice_date}}",     "Дата документа"),
        ("{{base_document}}",    "Підстава"),
        ("{{total_sum}}",        "Загальна сума (цифрами)"),
        ("{{total_sum_words}}",  "Загальна сума (прописом)"),
        ("{{valid_until}}",      "Дійсна до"),
        ("{{doc_date_full}}",    "Дата прописом"),
        ("{{current_year}}",     "Поточний рік"),
    ],
    "Одержувач": [
        ("{{recipient_name}}",   "ПІБ одержувача"),
        ("{{recipient_rank}}",   "Звання одержувача"),
        ("{{recipient_unit}}",   "Підрозділ одержувача"),
    ],
    "Підписанти": [
        ("{{chief_name}}",       "Начальник служби — ПІБ"),
        ("{{chief_rank}}",       "Начальник служби — звання"),
        ("{{chief_tvo}}",        "ТВО позначка"),
        ("{{given_name}}",       "Здав — ПІБ"),
        ("{{given_rank}}",       "Здав — звання"),
        ("{{received_name}}",    "Прийняв — ПІБ"),
        ("{{received_rank}}",    "Прийняв — звання"),
        ("{{warehouse_name}}",   "Нач. складу — ПІБ"),
        ("{{warehouse_rank}}",   "Нач. складу — звання"),
        ("{{clerk_name}}",       "Діловод РС — ПІБ"),
        ("{{clerk_rank}}",       "Діловод РС — звання"),
    ],
    "Організація": [
        ("{{unit_name}}",        "Назва частини"),
        ("{{service_name}}",     "Назва служби"),
    ],
    "Таблиці": [
        ("{{table:items_list}}", "Таблиця позицій майна"),
        ("{{table:signatories}}","Блок підписантів"),
    ],
}

DOC_TYPE_LABELS = {
    "invoice":     "Накладна (вимога)",
    "rv":          "Роздавальна відомість",
    "attestat":    "Речовий атестат",
    "write_off":   "Акт списання",
    "exploit_act": "Акт введення в експлуатацію",
    "registry":    "Реєстр документів",
    "custom":      "Довільний шаблон",
}


# ─────────────────────────────────────────────────────────────
#  СПИСОК (згрупований по групах і типах)
# ─────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    conn = get_connection()
    templates = conn.execute(
        "SELECT * FROM doc_templates ORDER BY template_group, doc_type, name"
    ).fetchall()
    conn.close()

    # Групуємо: {group_name: {doc_type: [templates]}}
    groups = {}
    for t in templates:
        g = t["template_group"] or "Без групи"
        dt = t["doc_type"]
        groups.setdefault(g, {}).setdefault(dt, []).append(dict(t))

    return render_template(
        "doc_templates/index.html",
        groups=groups,
        doc_type_labels=DOC_TYPE_LABELS,
    )


# ─────────────────────────────────────────────────────────────
#  РЕДАКТОР (новий + редагування)
# ─────────────────────────────────────────────────────────────

@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        return _save_template(None)
    # Копіювання від існуючого?
    copy_from = request.args.get("copy_from", type=int)
    base_t = None
    if copy_from:
        conn = get_connection()
        base_t = conn.execute("SELECT * FROM doc_templates WHERE id=?", (copy_from,)).fetchone()
        conn.close()
    return render_template(
        "doc_templates/editor.html",
        t=None,
        base_t=dict(base_t) if base_t else None,
        existing_html=_get_html(base_t) if base_t else "",
        shortcodes=SHORTCODES,
        doc_type_labels=DOC_TYPE_LABELS,
        form={},
    )


@bp.route("/<int:tpl_id>/edit", methods=["GET", "POST"])
@login_required
def edit(tpl_id):
    conn = get_connection()
    t = conn.execute("SELECT * FROM doc_templates WHERE id=?", (tpl_id,)).fetchone()
    conn.close()
    if not t:
        flash("Шаблон не знайдено", "error")
        return redirect(url_for("doc_templates.index"))
    if request.method == "POST":
        return _save_template(tpl_id)
    return render_template(
        "doc_templates/editor.html",
        t=dict(t),
        base_t=None,
        existing_html=_get_html(t),
        shortcodes=SHORTCODES,
        doc_type_labels=DOC_TYPE_LABELS,
        form={},
    )


def _save_template(tpl_id):
    """Спільна логіка збереження нового/існуючого шаблону."""
    name        = request.form.get("name", "").strip()
    doc_type    = request.form.get("doc_type", "custom")
    grp         = request.form.get("template_group", "").strip()
    description = request.form.get("description", "").strip()
    content     = request.form.get("content", "")
    orientation = request.form.get("page_orientation", "portrait")
    page_size   = request.form.get("page_size", "A4")
    margin_top    = _float(request.form.get("margin_top", "20"))
    margin_bottom = _float(request.form.get("margin_bottom", "20"))
    margin_left   = _float(request.form.get("margin_left", "30"))
    margin_right  = _float(request.form.get("margin_right", "10"))
    font_family   = request.form.get("font_family", "Times New Roman").strip() or "Times New Roman"
    base_font_size = _int(request.form.get("base_font_size", "12"), 12)

    if not name:
        flash("Назва шаблону обов'язкова", "error")
        return redirect(request.referrer or url_for("doc_templates.index"))

    grid_data = json.dumps({"html": content}, ensure_ascii=False)
    conn = get_connection()

    # Якщо це системний шаблон — забороняємо зміну is_system
    is_system = 0
    if tpl_id:
        existing = conn.execute("SELECT is_system FROM doc_templates WHERE id=?", (tpl_id,)).fetchone()
        if existing:
            is_system = existing["is_system"]

    try:
        if tpl_id is None:
            conn.execute("""
                INSERT INTO doc_templates
                  (name, doc_type, template_group, description, grid_data,
                   page_orientation, page_size,
                   margin_top, margin_bottom, margin_left, margin_right,
                   font_family, base_font_size)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (name, doc_type, grp, description, grid_data,
                  orientation, page_size,
                  margin_top, margin_bottom, margin_left, margin_right,
                  font_family, base_font_size))
            conn.commit()
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.close()
            flash(f"Шаблон «{name}» створено", "success")
            return redirect(url_for("doc_templates.edit", tpl_id=new_id))
        else:
            conn.execute("""
                UPDATE doc_templates SET
                  name=?, doc_type=?, template_group=?, description=?, grid_data=?,
                  page_orientation=?, page_size=?,
                  margin_top=?, margin_bottom=?, margin_left=?, margin_right=?,
                  font_family=?, base_font_size=?,
                  updated_at=datetime('now','localtime')
                WHERE id=?
            """, (name, doc_type, grp, description, grid_data,
                  orientation, page_size,
                  margin_top, margin_bottom, margin_left, margin_right,
                  font_family, base_font_size, tpl_id))
            conn.commit()
            conn.close()
            flash(f"Шаблон «{name}» збережено", "success")
            return redirect(url_for("doc_templates.edit", tpl_id=tpl_id))
    except Exception as e:
        conn.close()
        if "UNIQUE" in str(e):
            flash(f"Шаблон з назвою «{name}» вже існує", "error")
        else:
            flash(f"Помилка: {e}", "error")
        return redirect(request.referrer or url_for("doc_templates.index"))


# ─────────────────────────────────────────────────────────────
#  AJAX — авто-збереження контенту (без перезавантаження)
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:tpl_id>/autosave", methods=["POST"])
@login_required
def autosave(tpl_id):
    content = request.form.get("content", "")
    grid_data = json.dumps({"html": content}, ensure_ascii=False)
    conn = get_connection()
    conn.execute(
        "UPDATE doc_templates SET grid_data=?, updated_at=datetime('now','localtime') WHERE id=?",
        (grid_data, tpl_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  ПОПЕРЕДНІЙ ПЕРЕГЛЯД (inline AJAX)
# ─────────────────────────────────────────────────────────────

@bp.route("/preview-inline", methods=["POST"])
@login_required
def preview_inline():
    content = request.form.get("content", "")
    rendered = _render_with_demo_data(content)
    return jsonify({"rendered": rendered})


@bp.route("/<int:tpl_id>/preview")
@login_required
def preview(tpl_id):
    conn = get_connection()
    t = conn.execute("SELECT * FROM doc_templates WHERE id=?", (tpl_id,)).fetchone()
    conn.close()
    if not t:
        flash("Шаблон не знайдено", "error")
        return redirect(url_for("doc_templates.index"))
    rendered = _render_with_demo_data(_get_html(t))
    return render_template("doc_templates/preview.html", t=dict(t), rendered=rendered,
                           doc_type_labels=DOC_TYPE_LABELS)


# ─────────────────────────────────────────────────────────────
#  ВИДАЛЕННЯ
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:tpl_id>/delete", methods=["POST"])
@login_required
def delete(tpl_id):
    conn = get_connection()
    t = conn.execute("SELECT name, is_system FROM doc_templates WHERE id=?", (tpl_id,)).fetchone()
    if not t:
        conn.close()
        flash("Шаблон не знайдено", "error")
        return redirect(url_for("doc_templates.index"))
    if t["is_system"]:
        conn.close()
        flash("Системний шаблон не можна видалити. Можна лише скопіювати і редагувати копію.", "error")
        return redirect(url_for("doc_templates.index"))
    conn.execute("DELETE FROM doc_templates WHERE id=?", (tpl_id,))
    conn.commit()
    conn.close()
    flash(f"Шаблон «{t['name']}» видалено", "success")
    return redirect(url_for("doc_templates.index"))


# ─────────────────────────────────────────────────────────────
#  ДУБЛЮВАННЯ / КОПІЯ
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:tpl_id>/duplicate", methods=["POST"])
@login_required
def duplicate(tpl_id):
    conn = get_connection()
    t = conn.execute("SELECT * FROM doc_templates WHERE id=?", (tpl_id,)).fetchone()
    if not t:
        conn.close()
        flash("Шаблон не знайдено", "error")
        return redirect(url_for("doc_templates.index"))

    new_name = f"{t['name']} (копія)"
    i = 2
    base_name = new_name
    while conn.execute("SELECT id FROM doc_templates WHERE name=?", (new_name,)).fetchone():
        new_name = f"{base_name} {i}"
        i += 1

    ff  = t["font_family"]     if "font_family"     in t.keys() else "Times New Roman"
    bfs = t["base_font_size"] if "base_font_size"  in t.keys() else 12
    conn.execute("""
        INSERT INTO doc_templates
          (name, doc_type, template_group, description, grid_data,
           page_orientation, page_size,
           margin_top, margin_bottom, margin_left, margin_right,
           font_family, base_font_size,
           is_system, default_for_type)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
    """, (new_name, t["doc_type"], t["template_group"] or "", t["description"] or "",
          t["grid_data"],
          t["page_orientation"], t["page_size"],
          t["margin_top"], t["margin_bottom"], t["margin_left"], t["margin_right"],
          ff, bfs))
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    flash(f"Шаблон скопійовано як «{new_name}»", "success")
    return redirect(url_for("doc_templates.edit", tpl_id=new_id))


# ─────────────────────────────────────────────────────────────
#  ВСТАНОВИТИ ЗАМОВЧУВАННЯ
# ─────────────────────────────────────────────────────────────

@bp.route("/<int:tpl_id>/set-default", methods=["POST"])
@login_required
def set_default(tpl_id):
    conn = get_connection()
    t = conn.execute("SELECT doc_type FROM doc_templates WHERE id=?", (tpl_id,)).fetchone()
    if not t:
        conn.close()
        return jsonify({"ok": False, "msg": "not found"}), 404
    # Скинути default у всіх шаблонів цього типу
    conn.execute(
        "UPDATE doc_templates SET default_for_type=0 WHERE doc_type=?", (t["doc_type"],)
    )
    conn.execute(
        "UPDATE doc_templates SET default_for_type=1 WHERE id=?", (tpl_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  API — отримати шаблон для рендеру
# ─────────────────────────────────────────────────────────────

@bp.route("/api/<int:tpl_id>")
@login_required
def api_get(tpl_id):
    conn = get_connection()
    t = conn.execute("SELECT * FROM doc_templates WHERE id=?", (tpl_id,)).fetchone()
    conn.close()
    if not t:
        return jsonify({"ok": False, "msg": "not found"}), 404
    grid = json.loads(t["grid_data"] or "{}")
    return jsonify({
        "id": t["id"], "name": t["name"], "doc_type": t["doc_type"],
        "html": grid.get("html", ""),
        "page_orientation": t["page_orientation"],
        "page_size": t["page_size"],
        "font_family": t["font_family"] if "font_family" in t.keys() else "Times New Roman",
        "base_font_size": t["base_font_size"] if "base_font_size" in t.keys() else 12,
        "margins": {"top": t["margin_top"], "bottom": t["margin_bottom"],
                    "left": t["margin_left"], "right": t["margin_right"]},
    })


@bp.route("/api/default/<doc_type>")
@login_required
def api_default(doc_type):
    """Повернути дефолтний шаблон для типу документа."""
    conn = get_connection()
    t = conn.execute(
        "SELECT * FROM doc_templates WHERE doc_type=? AND default_for_type=1 LIMIT 1",
        (doc_type,)
    ).fetchone()
    conn.close()
    if not t:
        return jsonify({"ok": False, "msg": "no default"}), 404
    grid = json.loads(t["grid_data"] or "{}")
    return jsonify({"id": t["id"], "name": t["name"], "html": grid.get("html", "")})


# ─────────────────────────────────────────────────────────────
#  Допоміжні функції
# ─────────────────────────────────────────────────────────────

def _get_html(t) -> str:
    if not t:
        return ""
    try:
        grid = json.loads(t["grid_data"] or "{}")
        return grid.get("html", "")
    except Exception:
        return ""


def _float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _int(val, default=0):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# _render_with_demo_data → перенесено в core/renderer.py як render_demo()
