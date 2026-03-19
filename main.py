"""
main.py — точка входу системи обліку речового майна
Flask + PyWebView

Author: White
"""
import os
import sys
import json
import threading
from urllib.parse import quote
from pathlib import Path

try:
    import webview
    WEBVIEW_AVAILABLE = True
except ImportError:
    WEBVIEW_AVAILABLE = False

from flask import Flask, render_template, redirect, url_for, jsonify, request, session

from core.db import set_db_path, init_db, get_db_path
from core.auth import (
    login_required, login_user, set_session, clear_session,
    is_first_run, create_user, get_all_roles, current_user,
)
from core.backup import auto_backup, check_backup_reminder
from core.settings import get_all_settings
from core import plugin_manager

# ---------- Ініціалізація Flask ----------

app = Flask(__name__)

# secret_key — стабільний між перезапусками (сесії не скидаються).
# Генерується один раз і зберігається у файлі поруч з exe.
def _get_secret_key() -> bytes:
    key_file = Path(__file__).parent / ".secret_key"
    if key_file.exists():
        return key_file.read_bytes()
    key = os.urandom(32)
    try:
        key_file.write_bytes(key)
    except OSError:
        pass
    return key

app.secret_key = _get_secret_key()

# Hardening session cookies
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# SESSION_COOKIE_SECURE залишається False — система працює по HTTP (офлайн)

# Обмеження розміру завантажуваних файлів (50 МБ)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# Додаткові Jinja2 фільтри
@app.template_filter("fromjson")
def _fromjson(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []

@app.template_filter("urlencode")
def _urlencode(value):
    return quote(str(value), safe='')

@app.template_filter("fromjson_dict")
def _fromjson_dict(value):
    if not value:
        return {}
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}

@app.template_filter("fdate")
def _fdate(value, fallback="—"):
    """YYYY-MM-DD → ДД.ММ.РР  (напр. 2024-07-25 → 25.07.24)"""
    if not value:
        return fallback
    s = str(value).strip()[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}.{s[5:7]}.{s[2:4]}"
    return s or fallback

@app.template_filter("fdatetime")
def _fdatetime(value, fallback="—"):
    """YYYY-MM-DD HH:MM:SS → ДД.ММ.РР ГГ:ХХ  (напр. 2024-07-25 14:30 → 25.07.24 14:30)"""
    if not value:
        return fallback
    s = str(value).strip()
    date_part = s[:10]
    time_part = s[11:16] if len(s) >= 16 else ""
    if len(date_part) == 10 and date_part[4] == "-" and date_part[7] == "-":
        formatted = f"{date_part[8:10]}.{date_part[5:7]}.{date_part[2:4]}"
        return f"{formatted} {time_part}".strip() if time_part else formatted
    return s or fallback

# slot() реєструється через context_processor — стабільно незалежно від debug/reloader.
# _slot_fn може бути замінена на повноцінну після завантаження плагінів.
def _slot_stub(name, **kwargs):
    return ""

# Поточна активна slot() — замінюється після register_plugins()
_active_slot = _slot_stub

@app.context_processor
def _inject_globals():
    from core.settings import get_setting
    unit_name = get_setting("company_name", "") or "Речова служба"
    return {"slot": _active_slot, "unit_name": unit_name}


# ---------- Заборона кешування HTML-відповідей ----------

@app.after_request
def _no_cache(response):
    if "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ---------- Обробники помилок ----------

def _auto_log_error(code: int, title: str, body: str) -> int | None:
    """Автоматично записує HTTP-помилку в feedback. Повертає id запису або None.
    Дедуплікація: якщо такий самий title+page_url вже є зі статусом new/in_progress
    і створений менше 60 хв тому — повертає існуючий id без нового запису."""
    try:
        from core.db import get_connection as _gc
        conn = _gc()
        try:
            existing = conn.execute(
                """SELECT id FROM feedback
                   WHERE title=? AND page_url=? AND status IN ('new','in_progress')
                   AND created_at >= datetime('now','localtime','-60 minutes')
                   ORDER BY id DESC LIMIT 1""",
                (title, request.url)
            ).fetchone()
            if existing:
                return existing["id"]
            cur = conn.execute(
                """INSERT INTO feedback (user_id, username, category, priority, title, body, page_url, status)
                   VALUES (?, ?, 'bug', 'high', ?, ?, ?, 'new')""",
                (
                    session.get("user_id"),
                    session.get("full_name") or session.get("username") or "system",
                    title,
                    body,
                    request.url,
                )
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except Exception:
        return None


_STATIC_EXTS = {".css", ".js", ".ico", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                ".woff", ".woff2", ".ttf", ".eot", ".map"}

@app.errorhandler(404)
def not_found(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"error": "Не знайдено", "code": 404}), 404
    from pathlib import PurePosixPath
    suffix = PurePosixPath(request.path).suffix.lower()
    # Не логувати 404 для статичних ресурсів — лише для реальних сторінок
    fid = None
    _skip_prefixes = ("/.well-known/", "/favicon.")
    if suffix not in _STATIC_EXTS and not any(request.path.startswith(p) for p in _skip_prefixes):
        fid = _auto_log_error(
            404,
            f"404 — Сторінку не знайдено: {request.path}",
            f"URL: {request.url}\nМетод: {request.method}\nReferer: {request.referrer or '—'}"
        )
    return render_template("errors/404.html", auto_fid=fid), 404


@app.errorhandler(403)
def forbidden(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"error": "Доступ заборонено", "code": 403}), 403
    return render_template("errors/403.html"), 403


@app.errorhandler(500)
def server_error(e):
    import traceback as _tb
    err_text = _tb.format_exc()
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"error": "Внутрішня помилка сервера", "code": 500}), 500
    body = (
        f"URL: {request.url}\n"
        f"Метод: {request.method}\n"
        f"Referer: {request.referrer or '—'}\n\n"
        f"Traceback:\n{err_text}"
    )
    fid = _auto_log_error(500, f"500 — {str(e)[:120]}", body)
    return render_template("errors/500.html", error=str(e), traceback=err_text, auto_fid=fid), 500


# ---------- Реєстрація blueprints і плагінів ----------

def register_blueprints(app: Flask) -> None:
    """
    Реєструє основні blueprints системи.
    ВАЖЛИВО: Flask вимагає реєстрацію blueprints до першого запиту.
    Тому викликається одразу після визначення — в кінці цього блоку.
    """
    from modules.personnel.routes import bp as personnel_bp
    from modules.warehouse.routes import bp as warehouse_bp
    from modules.invoices.routes import bp as invoices_bp
    from modules.settings.routes import bp as settings_bp
    from modules.plugins.routes import bp as plugins_bp
    from modules.doc_templates.routes import bp as doc_templates_bp
    from modules.rv.routes import bp as rv_bp
    from modules.supply_norms.routes import bp as supply_norms_bp
    from modules.planning.routes import bp as planning_bp
    from modules.reports.routes import bp as reports_bp
    from modules.import_export.routes import bp as import_export_bp
    from modules.feedback.routes import bp as feedback_bp
    from modules.attestat_import.routes import bp as attestat_import_bp
    from modules.registry.routes import bp as registry_bp
    from modules.acts.routes import bp as acts_bp

    app.register_blueprint(attestat_import_bp)
    app.register_blueprint(personnel_bp)
    app.register_blueprint(warehouse_bp)
    app.register_blueprint(invoices_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(plugins_bp)
    app.register_blueprint(doc_templates_bp)
    app.register_blueprint(rv_bp)
    app.register_blueprint(supply_norms_bp)
    app.register_blueprint(planning_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(import_export_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(registry_bp)
    app.register_blueprint(acts_bp)

    @app.context_processor
    def inject_plugin_menu():
        return {"plugin_menu_items": plugin_manager.get_all_menu_items()}


def register_plugins(app: Flask) -> None:
    """
    Завантажує та реєструє плагіни. Потребує ініціалізованої БД.
    Плагіни також можуть реєструвати власні bluepрints —
    тому register_plugins() теж має викликатись до першого запиту.
    """
    global _active_slot
    plugin_manager.load_and_register(app)

    from core.hooks import make_slot_function
    _active_slot = make_slot_function()


# Реєструємо blueprints одразу — до будь-якого запиту.
# Плагіни реєструються пізніше в run_flask() після init_db().
register_blueprints(app)


# ---------- Маршрути ядра ----------

@app.route("/storage/<path:filename>")
@login_required
def serve_storage(filename):
    """Роздає файли з папки storage/ (фото, скани тощо)."""
    from flask import send_from_directory, abort
    from core.settings import get_storage_path
    storage_root = get_storage_path().resolve()
    # Захист від path traversal: перевіряємо що resolved шлях всередині storage/
    target = (storage_root / filename).resolve()
    if not str(target).startswith(str(storage_root)):
        abort(403)
    return send_from_directory(str(storage_root), filename)


@app.route("/units/")
@login_required
def units_redirect():
    """Перенаправляє /units/ на settings.units_list."""
    return redirect(url_for("settings.units_list"))


@app.route("/")
@login_required
def dashboard():
    from core.db import get_connection
    conn = get_connection()

    # Статистика для дашборду
    active_group_ids = conn.execute(
        "SELECT id FROM groups WHERE type = 'active'"
    ).fetchall()
    active_ids = [r["id"] for r in active_group_ids]

    personnel_count = 0
    if active_ids:
        placeholders = ",".join("?" * len(active_ids))
        personnel_count = conn.execute(
            f"SELECT COUNT(*) FROM personnel WHERE group_id IN ({placeholders}) AND is_active = 1",
            active_ids
        ).fetchone()[0]

    # Непроведені накладні
    pending_invoices = conn.execute(
        "SELECT COUNT(*) FROM invoices WHERE status IN ('created', 'issued')"
    ).fetchone()[0]

    # Залишки складу (загальна сума + топ-10)
    from core.warehouse import get_stock
    stock_rows = get_stock(conn)
    stock_total = round(sum(r["total_sum"] or 0 for r in stock_rows), 2)
    stock_sorted = sorted(stock_rows, key=lambda r: r["qty_balance"] or 0, reverse=True)
    stock_top10 = [dict(r) for r in stock_sorted[:10]]
    stock_bottom10 = [dict(r) for r in sorted(stock_rows, key=lambda r: r["qty_balance"] or 0)[:10] if (r["qty_balance"] or 0) > 0]

    # Майно до видачі (кількість позицій де залишок < норми)
    needs_count = conn.execute("""
        SELECT COUNT(DISTINCT p.id || '-' || nd.id) FROM personnel p
        JOIN groups g ON p.group_id = g.id
        JOIN personnel_norms pn ON pn.personnel_id = p.id
        JOIN supply_norm_items sni ON sni.norm_id = pn.norm_id
        JOIN norm_dictionary nd ON sni.norm_dict_id = nd.id
        WHERE p.is_active=1 AND g.type NOT IN ('szch','deceased','missing')
          AND COALESCE((
              SELECT SUM(pi.quantity) FROM personnel_items pi
              JOIN item_dictionary idi ON pi.item_id=idi.id
              WHERE pi.personnel_id=p.id AND pi.status='active' AND idi.norm_dict_id=nd.id
          ), 0) < COALESCE(sni.quantity, 0)
    """).fetchone()[0] or 0

    # Прострочені накладні (створено і минув термін дійсності)
    overdue_invoices = conn.execute(
        """SELECT id, number, valid_until, created_at,
                  recipient_personnel_id, recipient_unit_id
           FROM invoices
           WHERE status = 'created'
             AND valid_until IS NOT NULL
             AND valid_until < date('now','localtime')
           ORDER BY valid_until"""
    ).fetchall()

    # Накладні без скану (статус issued, scan_path IS NULL) — до 10 + загальна кількість
    invoices_no_scan_all = conn.execute(
        """SELECT id, number, created_at, recipient_unit_id, recipient_personnel_id
           FROM invoices
           WHERE status = 'issued'
             AND (scan_path IS NULL OR scan_path = '')
           ORDER BY created_at DESC"""
    ).fetchall()
    invoices_no_scan = invoices_no_scan_all[:10]
    invoices_no_scan_total = len(invoices_no_scan_all)

    # РВ без скану (статус active або closed, scan_path IS NULL) — до 10 + загальна кількість
    rv_no_scan_all = conn.execute(
        """SELECT id, number, created_at, unit_id
           FROM distribution_sheets
           WHERE status IN ('active', 'closed')
             AND (scan_path IS NULL OR scan_path = '')
           ORDER BY created_at DESC"""
    ).fetchall()
    rv_no_scan = rv_no_scan_all[:10]
    rv_no_scan_total = len(rv_no_scan_all)

    # В/с яким потрібна видача (к-ть осіб, не позицій)
    personnel_needs_count = conn.execute("""
        SELECT COUNT(DISTINCT p.id) FROM personnel p
        JOIN groups g ON p.group_id = g.id
        JOIN personnel_norms pn ON pn.personnel_id = p.id
        JOIN supply_norm_items sni ON sni.norm_id = pn.norm_id
        JOIN norm_dictionary nd ON sni.norm_dict_id = nd.id
        WHERE p.is_active=1 AND g.type NOT IN ('szch','deceased','missing')
          AND COALESCE((
              SELECT SUM(pi.quantity) FROM personnel_items pi
              JOIN item_dictionary idi ON pi.item_id=idi.id
              WHERE pi.personnel_id=p.id AND pi.status='active' AND idi.norm_dict_id=nd.id
          ), 0) < COALESCE(sni.quantity, 0)
    """).fetchone()[0] or 0

    # Останні видачі (накладні із статусом issued, до 10)
    recent_issues = conn.execute("""
        SELECT i.id, i.number, i.created_at,
               p.last_name, p.first_name, p.rank,
               u.name as unit_name
        FROM invoices i
        LEFT JOIN personnel p ON i.recipient_personnel_id = p.id
        LEFT JOIN units u ON i.recipient_unit_id = u.id
        WHERE i.status = 'issued'
        ORDER BY i.created_at DESC
        LIMIT 10
    """).fetchall()

    # Графік 1: видачі по місяцях (попередні 6 міс + поточний)
    from datetime import date as _date
    import calendar as _cal
    _today = _date.today()
    chart_past = []
    for i in range(5, -1, -1):
        m = _today.month - i
        y = _today.year
        while m <= 0:
            m += 12; y -= 1
        month_start = f"{y:04d}-{m:02d}-01"
        last_day = _cal.monthrange(y, m)[1]
        month_end = f"{y:04d}-{m:02d}-{last_day:02d}"
        UA_MONTHS_SHORT = ["","Січ","Лют","Бер","Кві","Тра","Чер",
                           "Лип","Сер","Вер","Жов","Лис","Гру"]
        cnt = conn.execute("""
            SELECT COUNT(DISTINCT COALESCE(recipient_personnel_id, recipient_unit_id))
            FROM invoices
            WHERE status IN ('issued','processed')
              AND date(COALESCE(issued_date, created_at)) BETWEEN ? AND ?
        """, (month_start, month_end)).fetchone()[0] or 0
        chart_past.append({"label": f"{UA_MONTHS_SHORT[m]} {y}", "value": cnt,
                            "year": y, "month": m})

    # Графік 2: потреби по місяцях (наступні 6 міс)
    from modules.planning.routes import _planning_data, _group_by_calendar
    plan_rows = _planning_data(conn)
    calendar_data = _group_by_calendar(plan_rows)
    # Overdue (year=0, month=0) — прострочені, зараховуємо в поточний місяць
    overdue_data = next((c for c in calendar_data if c.get("overdue")), None)
    overdue_personnel = set(r["personnel_id"] for r in overdue_data["rows"]) if overdue_data else set()
    chart_future = []
    for i in range(0, 6):
        m = _today.month + i
        y = _today.year
        while m > 12:
            m -= 12; y += 1
        UA_MONTHS_SHORT = ["","Січ","Лют","Бер","Кві","Тра","Чер",
                           "Лип","Сер","Вер","Жов","Лис","Гру"]
        month_data = next((c for c in calendar_data if c["year"] == y and c["month"] == m), None)
        persons = set(r["personnel_id"] for r in month_data["rows"]) if month_data else set()
        # Для поточного місяця додаємо overdue
        if i == 0:
            persons |= overdue_personnel
        chart_future.append({"label": f"{UA_MONTHS_SHORT[m]} {y}", "value": len(persons),
                              "year": y, "month": m})

    # Номенклатурне майно на СЗЧ / Загиблих / Безвісті
    archive_groups = conn.execute(
        "SELECT id FROM groups WHERE type IN ('szch', 'deceased', 'missing')"
    ).fetchall()
    archive_group_ids = [r["id"] for r in archive_groups]

    nomenclature_archive = []
    if archive_group_ids:
        placeholders = ",".join("?" * len(archive_group_ids))
        nomenclature_archive = conn.execute(
            f"""SELECT p.last_name, p.first_name, p.middle_name, p.rank,
                       g.name as group_name,
                       d.name as item_name, pi.quantity, pi.price, pi.category
                FROM personnel_items pi
                JOIN personnel p ON pi.personnel_id = p.id
                JOIN groups g ON p.group_id = g.id
                JOIN item_dictionary d ON pi.item_id = d.id
                WHERE p.group_id IN ({placeholders})
                  AND d.is_inventory = 1
                  AND pi.status = 'active'
                ORDER BY g.name, p.last_name""",
            archive_group_ids
        ).fetchall()

    # Словник для пошуку залишків на плашці
    from core.db import get_connection as _gc2
    conn2 = _gc2()
    norm_dict_options = conn2.execute("""
        SELECT nd.id, nd.name, nd.unit, ndg.name AS group_name
        FROM norm_dictionary nd
        LEFT JOIN norm_dict_groups ndg ON nd.group_id = ndg.id
        ORDER BY ndg.sort_order NULLS LAST, nd.sort_order NULLS LAST, nd.name
    """).fetchall()
    conn2.close()

    conn.close()

    backup_reminder = check_backup_reminder()

    return render_template(
        "dashboard.html",
        today_year=_today.year,
        personnel_count=personnel_count,
        pending_invoices=pending_invoices,
        overdue_invoices=[dict(r) for r in overdue_invoices],
        nomenclature_archive=[dict(r) for r in nomenclature_archive],
        invoices_no_scan=[dict(r) for r in invoices_no_scan],
        invoices_no_scan_total=invoices_no_scan_total,
        rv_no_scan=[dict(r) for r in rv_no_scan],
        rv_no_scan_total=rv_no_scan_total,
        backup_reminder=backup_reminder,
        stock_total=stock_total,
        stock_top10=stock_top10,
        stock_bottom10=stock_bottom10,
        needs_count=needs_count,
        personnel_needs_count=personnel_needs_count,
        recent_issues=[dict(r) for r in recent_issues],
        norm_dict_options=[dict(r) for r in norm_dict_options],
        user=current_user(),
    )


def _save_needs_snapshot(conn, year: int, month: int, needs_count: int) -> None:
    """Зберігає знімок потреб у видачі за місяць (upsert)."""
    from datetime import date
    conn.execute(
        """INSERT INTO chart_monthly_snapshots (year, month, needs_count, snapshot_date)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(year, month) DO UPDATE SET
               needs_count   = excluded.needs_count,
               snapshot_date = excluded.snapshot_date""",
        (year, month, needs_count, date.today().isoformat())
    )
    conn.commit()


@app.route("/api/dashboard/chart")
@login_required
def api_dashboard_chart():
    """Returns: {"ok": bool, "data": [{"label", "month_num", "issued", "needs"}], "years": [...], "msg": str}"""
    import calendar as _cal
    from datetime import date
    from modules.planning.routes import _planning_data, _group_by_calendar
    from core.db import get_connection

    today = date.today()
    year = request.args.get("year", today.year, type=int)

    UA_MONTHS = ["", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
                 "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]

    conn = get_connection()
    try:
        # Діапазон доступних років (з invoices + snapshots + поточний)
        min_year_row = conn.execute(
            "SELECT MIN(year) as y FROM invoices WHERE year > 2000"
        ).fetchone()
        snap_min_row = conn.execute(
            "SELECT MIN(year) as y FROM chart_monthly_snapshots"
        ).fetchone()
        candidates = [y for y in [
            min_year_row["y"] if min_year_row and min_year_row["y"] else None,
            snap_min_row["y"] if snap_min_row and snap_min_row["y"] else None,
            today.year,
        ] if y is not None]
        min_year = min(candidates)
        available_years = list(range(min_year, today.year + 2))

        # Потреби поточного місяця — рахуємо live і зберігаємо знімок
        plan_rows = _planning_data(conn)
        cal_data  = _group_by_calendar(plan_rows)
        overdue_data = next((c for c in cal_data if c.get("overdue")), None)
        overdue_ids  = set(r["personnel_id"] for r in overdue_data["rows"]) if overdue_data else set()

        # Зберігаємо знімок поточного місяця
        cur_month_data = next((c for c in cal_data if c["year"] == today.year and c["month"] == today.month), None)
        cur_needs_ids  = set(r["personnel_id"] for r in cur_month_data["rows"]) if cur_month_data else set()
        cur_needs_ids |= overdue_ids
        _save_needs_snapshot(conn, today.year, today.month, len(cur_needs_ids))

        # Знімки за запитаний рік
        snapshots = {
            r["month"]: r["needs_count"]
            for r in conn.execute(
                "SELECT month, needs_count FROM chart_monthly_snapshots WHERE year = ?", (year,)
            ).fetchall()
        }

        result = []
        for m in range(1, 13):
            # Видачі: рахуємо з invoices
            month_start = f"{year:04d}-{m:02d}-01"
            last_day    = _cal.monthrange(year, m)[1]
            month_end   = f"{year:04d}-{m:02d}-{last_day:02d}"
            issued = conn.execute(
                """SELECT COUNT(DISTINCT COALESCE(recipient_personnel_id, recipient_unit_id))
                   FROM invoices
                   WHERE status IN ('issued','processed')
                     AND date(COALESCE(issued_date, created_at)) BETWEEN ? AND ?""",
                (month_start, month_end)
            ).fetchone()[0] or 0

            # Потреби: знімок або live (поточний місяць поточного року)
            if year == today.year and m == today.month:
                needs = len(cur_needs_ids)
            elif year == today.year and m > today.month:
                # Майбутні місяці поточного року — з calendar
                future = next((c for c in cal_data if c["year"] == year and c["month"] == m), None)
                needs  = len(set(r["personnel_id"] for r in future["rows"])) if future else 0
            else:
                needs = snapshots.get(m, 0)

            result.append({
                "label":     UA_MONTHS[m],
                "month_num": m,
                "issued":    issued,
                "needs":     needs,
            })

    finally:
        conn.close()

    return jsonify({"ok": True, "data": result, "years": available_years, "msg": ""})


@app.route("/api/stock-lookup")
@login_required
def api_stock_lookup():
    """Повертає залишки складу для конкретної позиції норм-словника."""
    norm_dict_id = request.args.get("norm_dict_id", type=int)
    if not norm_dict_id:
        return jsonify({"ok": False, "error": "norm_dict_id required"}), 400

    from core.db import get_connection
    from core.warehouse import get_stock
    conn = get_connection()

    # Знаходимо item_dictionary ids що належать до цього norm_dict_id
    item_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM item_dictionary WHERE norm_dict_id = ?", (norm_dict_id,)
    ).fetchall()]

    nd = conn.execute(
        "SELECT name, unit FROM norm_dictionary WHERE id = ?", (norm_dict_id,)
    ).fetchone()

    if not nd or not item_ids:
        conn.close()
        return jsonify({"ok": True, "qty_balance": 0, "unit": nd["unit"] if nd else "шт",
                        "name": nd["name"] if nd else "—", "batches": []})

    stock_all = get_stock(conn)
    conn.close()

    # Фільтруємо по item_ids
    batches = [r for r in stock_all if r["item_id"] in item_ids]
    qty_total = sum(r["qty_balance"] or 0 for r in batches)

    return jsonify({
        "ok": True,
        "name": nd["name"],
        "unit": nd["unit"] or "шт",
        "qty_balance": qty_total,
        "batches": [
            {"category": r["category"], "price": r["price"],
             "qty_balance": r["qty_balance"] or 0}
            for r in batches if (r["qty_balance"] or 0) != 0
        ]
    })


@app.route("/audit/")
@login_required
def audit_log():
    from core.audit import get_audit_log
    page = request.args.get("page", 1, type=int)
    limit = 100
    offset = (page - 1) * limit
    rows = get_audit_log(limit=limit, offset=offset)
    return render_template("audit.html", rows=rows, page=page)


@app.route("/login", methods=["GET", "POST"])
def login_page():
    from flask import request
    from core.db import get_connection as _gc
    def _get_default_theme():
        try:
            conn = _gc()
            row = conn.execute("SELECT value FROM settings WHERE key='default_theme'").fetchone()
            conn.close()
            return row["value"] if row else "default"
        except Exception:
            return "default"

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = login_user(username, password)
        if user:
            set_session(user)
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Невірний логін або пароль",
                               default_theme=_get_default_theme())
    return render_template("login.html", default_theme=_get_default_theme())


@app.route("/logout")
def logout():
    clear_session()
    return redirect(url_for("login_page"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    from core.auth import get_user_by_id, hash_password, check_password
    from core.db import get_connection as _gc
    user = get_user_by_id(session["user_id"])
    error = None
    success = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_info":
            full_name = request.form.get("full_name", "").strip()
            if not full_name:
                error = "Введіть ПІБ"
            else:
                conn = _gc()
                conn.execute("UPDATE users SET full_name=? WHERE id=?",
                             (full_name, user["id"]))
                conn.commit()
                conn.close()
                session["full_name"] = full_name
                success = "Дані оновлено"
                user = get_user_by_id(session["user_id"])

        elif action == "change_theme":
            theme = request.form.get("theme", "default")
            if theme not in ("default", "dark", "zsu"):
                theme = "default"
            conn = _gc()
            conn.execute("UPDATE users SET theme=? WHERE id=?", (theme, user["id"]))
            conn.commit()
            conn.close()
            session["theme"] = theme
            success = "Тему змінено"
            user = get_user_by_id(session["user_id"])

        elif action == "change_password":
            old_pw  = request.form.get("old_password", "")
            new_pw  = request.form.get("new_password", "")
            conf_pw = request.form.get("confirm_password", "")
            if not check_password(old_pw, user["password_hash"]):
                error = "Поточний пароль невірний"
            elif len(new_pw) < 4:
                error = "Новий пароль — мінімум 4 символи"
            elif new_pw != conf_pw:
                error = "Паролі не збігаються"
            else:
                conn = _gc()
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (hash_password(new_pw), user["id"]))
                conn.commit()
                conn.close()
                success = "Пароль змінено"

    return render_template("profile.html", user=user, error=error, success=success)


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """Початкове налаштування — перший запуск."""
    if not is_first_run():
        return redirect(url_for("login_page"))
    from flask import request
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        full_name = request.form.get("full_name", "").strip()
        if username and password and full_name:
            roles = get_all_roles()
            admin_role = next((r for r in roles if r["name"] == "Адміністратор"), None)
            if admin_role:
                create_user(username, password, full_name, admin_role["id"])
                return redirect(url_for("login_page"))
    return render_template("setup.html")


# ---------- Вибір / пошук бази даних ----------

def find_default_db() -> str | None:
    """Шукати database.db поруч з exe або в поточній папці."""
    candidates = [
        Path(sys.executable).parent / "database.db",
        Path(__file__).parent / "database.db",
        Path.cwd() / "database.db",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def choose_db_path() -> str:
    """Повертає шлях до БД (знаходить існуючу або визначає шлях для нової)."""
    default = find_default_db()
    if default:
        return default
    default_new = Path(sys.executable).parent / "database.db"
    if not getattr(sys, "frozen", False):
        default_new = Path(__file__).parent / "database.db"
    return str(default_new)


# ---------- Запуск Flask у фоновому потоці ----------

def run_flask() -> None:
    # Плагіни реєструємо тут — після init_db() в main(),
    # але до app.run() — тобто до першого запиту.
    register_plugins(app)
    app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)


# ---------- Головна функція ----------

def main():
    db_path = choose_db_path()
    set_db_path(db_path)
    init_db()

    try:
        auto_backup()
    except Exception:
        pass

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    if WEBVIEW_AVAILABLE:
        window = webview.create_window(
            title="Облік речового майна",
            url="http://127.0.0.1:5050",
            width=1400,
            height=900,
            min_size=(1024, 700),
            resizable=True,
        )
        webview.start(debug=False)
    else:
        print("PyWebView не встановлено — відкрий браузер: http://127.0.0.1:5050")
        flask_thread.join()


if __name__ == "__main__":
    main()
