"""
plugins/example_plugin/routes.py — Приклад Blueprint плагіна.

Демонструє:
  • Звичайні HTML-сторінки (render_template)
  • JSON API ендпоінти
  • Запис у власну БД-таблицю (api.db.write)
  • Читання через api.db.execute
  • Доступ до всіх підсистем SystemAPI
"""
from flask import Blueprint, render_template, jsonify, request
from core.auth import login_required

bp = Blueprint("example_plugin", __name__, url_prefix="/example")
bp.api = None  # встановлюється в plugin.register()


# ══════════════════════════════════════════════════════════════════════
# HTML сторінки
# ══════════════════════════════════════════════════════════════════════

@bp.route("/")
@login_required
def index():
    """Головна сторінка модуля — зведена інформація."""
    # ── SystemAPI: personnel ──────────────────────────────────────────
    personnel     = bp.api.personnel.get_list(is_active=True, limit=9999)
    personnel_count = len(personnel)

    # ── SystemAPI: warehouse ──────────────────────────────────────────
    stock = bp.api.warehouse.get_stock()

    # ── SystemAPI: invoices ───────────────────────────────────────────
    recent_invoices = bp.api.invoices.get_list(limit=5)

    # ── SystemAPI: settings ───────────────────────────────────────────
    unit_name = bp.api.settings.get("company_name", "А5027")

    # ── SystemAPI: db.execute (власна таблиця) ────────────────────────
    log_entries = bp.api.db.execute(
        "SELECT * FROM example_plugin_log ORDER BY created_at DESC LIMIT 5"
    )

    return render_template(
        "example_plugin/index.html",
        personnel_count=personnel_count,
        stock=stock,
        recent_invoices=recent_invoices,
        unit_name=unit_name,
        log_entries=log_entries,
    )


@bp.route("/person/<int:pid>")
@login_required
def person_detail(pid):
    """Приклад сторінки з деталями о/с."""
    # ── SystemAPI: personnel.get(id) ──────────────────────────────────
    person = bp.api.personnel.get(pid)
    if not person:
        from flask import abort
        abort(404)

    # ── SystemAPI: personnel.get_items(id) ────────────────────────────
    items = bp.api.personnel.get_items(pid)

    # ── SystemAPI: audit.log ──────────────────────────────────────────
    bp.api.audit.log("example_plugin.view_person", f"Переглянуто картку id={pid}")

    return render_template(
        "example_plugin/person.html",
        person=person,
        items=items,
    )


# ══════════════════════════════════════════════════════════════════════
# JSON API
# ══════════════════════════════════════════════════════════════════════

@bp.route("/api/personnel")
@login_required
def api_personnel():
    """JSON: список активного о/с."""
    data = bp.api.personnel.get_list(is_active=True, limit=100)
    return jsonify({"ok": True, "count": len(data), "personnel": data})


@bp.route("/api/stock")
@login_required
def api_stock():
    """JSON: залишки складу."""
    stock = bp.api.warehouse.get_stock()
    return jsonify({"ok": True, "count": len(stock), "stock": stock})


@bp.route("/api/invoices")
@login_required
def api_invoices():
    """JSON: останні 20 накладних."""
    invoices = bp.api.invoices.get_list(limit=20)
    return jsonify({"ok": True, "invoices": invoices})


@bp.route("/api/items")
@login_required
def api_items():
    """JSON: словник майна."""
    items = bp.api.items.get_list()
    return jsonify({"ok": True, "count": len(items), "items": items})


@bp.route("/api/log", methods=["POST"])
@login_required
def api_log():
    """JSON: записати подію у власну таблицю."""
    data = request.get_json() or {}
    event   = data.get("event", "manual")
    details = data.get("details", "")

    # ── SystemAPI: db.write ───────────────────────────────────────────
    bp.api.db.write(
        "INSERT INTO example_plugin_log (event, details) VALUES (?, ?)",
        (event, details),
    )
    bp.api.audit.log("example_plugin.log", f"event={event}")
    return jsonify({"ok": True})


@bp.route("/api/log")
@login_required
def api_log_list():
    """JSON: журнал подій модуля."""
    # ── SystemAPI: db.execute ─────────────────────────────────────────
    rows = bp.api.db.execute(
        "SELECT * FROM example_plugin_log ORDER BY created_at DESC LIMIT 50"
    )
    return jsonify({"ok": True, "log": rows})
