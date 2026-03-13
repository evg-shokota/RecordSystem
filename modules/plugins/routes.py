"""
modules/plugins/routes.py — Керування плагінами системи.
Author: White
"""
import os
import zipfile
import shutil
from pathlib import Path
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from core.auth import login_required
from core.db import get_connection
from core import plugin_manager

bp = Blueprint("plugins", __name__, url_prefix="/plugins")

PLUGINS_DIR = Path(__file__).parent.parent.parent / "plugins"


@bp.route("/")
@login_required
def index():
    """Список всіх плагінів — знайдених і встановлених."""
    # Сканувати папку (свіжо)
    found = plugin_manager.scan_plugins()

    # Статус з БД
    conn = get_connection()
    db_plugins = {
        r["slug"]: dict(r) for r in
        conn.execute("SELECT * FROM plugins").fetchall()
    }
    conn.close()

    plugins_list = []
    for slug, plugin in found.items():
        db_info = db_plugins.get(slug, {})
        plugins_list.append({
            "slug":        slug,
            "name":        plugin.name,
            "version":     plugin.version,
            "description": plugin.description,
            "author":      plugin.author,
            "icon":        plugin.icon,
            "has_settings": bool(plugin.get_settings_schema()),
            "is_installed": slug in db_plugins,
            "is_active":   db_info.get("is_active", 0),
            "installed_at":db_info.get("installed_at", ""),
        })

    return render_template("plugins/index.html", plugins=plugins_list)


@bp.route("/<slug>/install", methods=["POST"])
@login_required
def install(slug):
    # Переконатись що плагін в реєстрі (якщо ні — спробувати завантажити)
    if slug not in plugin_manager.get_loaded_plugins():
        found = plugin_manager.scan_plugins()
        plugin_manager._registry.update(found)

    result = plugin_manager.install_plugin(slug)
    if result.get("ok"):
        # Зареєструвати Blueprint якщо ще не зареєстровано
        from flask import current_app
        p = plugin_manager.get_plugin(slug)
        if p:
            try:
                from core.plugin_api import get_api
                p.register(current_app._get_current_object(), get_api())
            except Exception:
                pass
        flash(f"Плагін встановлено", "success")
    else:
        flash(result.get("error", "Помилка"), "danger")
    return redirect(url_for("plugins.index"))


@bp.route("/<slug>/uninstall", methods=["POST"])
@login_required
def uninstall(slug):
    keep = request.form.get("keep_data", "1") == "1"
    result = plugin_manager.uninstall_plugin(slug, keep_data=keep)
    if result.get("ok"):
        flash("Плагін видалено", "success")
    else:
        flash(result.get("error", "Помилка"), "danger")
    return redirect(url_for("plugins.index"))


@bp.route("/<slug>/toggle", methods=["POST"])
@login_required
def toggle(slug):
    active = request.json.get("active", True)
    result = plugin_manager.toggle_plugin(slug, active)
    if result.get("ok"):
        result["restart"] = True
    return jsonify(result)


@bp.route("/restart", methods=["POST"])
@login_required
def restart_server():
    """Перезапуск Flask-процесу після зміни плагінів."""
    import os, sys, threading
    def _do_restart():
        import time
        time.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify({"ok": True})


@bp.route("/<slug>/settings", methods=["GET", "POST"])
@login_required
def settings(slug):
    plugin = plugin_manager.get_plugin(slug)
    if not plugin:
        flash("Плагін не знайдено", "danger")
        return redirect(url_for("plugins.index"))

    schema = plugin.get_settings_schema()
    current = plugin_manager.get_plugin_settings(slug)

    if request.method == "POST":
        data = {}
        for field in schema:
            key = field["key"]
            if field["type"] == "switch":
                data[key] = "1" if request.form.get(key) else "0"
            else:
                data[key] = request.form.get(key, field.get("default", ""))
        plugin_manager.save_plugin_settings(slug, data)
        flash("Налаштування збережено", "success")
        return redirect(url_for("plugins.settings", slug=slug))

    # Заповнити дефолти якщо немає збережених
    for field in schema:
        if field["key"] not in current:
            current[field["key"]] = field.get("default", "")

    return render_template("plugins/settings.html",
                           plugin=plugin, schema=schema, current=current)


@bp.route("/upload", methods=["POST"])
@login_required
def upload():
    """
    Завантаження плагіна у вигляді .zip архіву.
    Структура архіву: my_plugin/plugin.py (+ все інше)
    """
    f = request.files.get("plugin_zip")
    if not f or not f.filename.endswith(".zip"):
        return jsonify({"error": "Потрібен .zip файл"}), 400

    # Розпакувати в тимчасову папку, перевірити структуру
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "plugin.zip"
        f.save(str(zip_path))

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                # Знайти plugin.py
                plugin_files = [n for n in names if n.endswith("plugin.py")]
                if not plugin_files:
                    return jsonify({"error": "Файл plugin.py не знайдено в архіві"}), 400

                # Визначити slug (перша папка)
                slug = plugin_files[0].split("/")[0]
                if not slug or slug == "plugin.py":
                    return jsonify({"error": "Архів має містити папку плагіна"}), 400

                target = PLUGINS_DIR / slug
                if target.exists():
                    shutil.rmtree(target)

                zf.extractall(tmpdir)
                shutil.copytree(Path(tmpdir) / slug, target)

        except zipfile.BadZipFile:
            return jsonify({"error": "Пошкоджений ZIP файл"}), 400

    # Оновити реєстр
    found = plugin_manager.scan_plugins()
    plugin_manager._registry.update(found)

    return jsonify({"ok": True, "slug": slug})


@bp.route("/sdk-docs")
@login_required
def sdk_docs():
    """Документація Plugin SDK."""
    return render_template("plugins/sdk_docs.html")

