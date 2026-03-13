"""
core/plugin_manager.py — Менеджер плагінів системи.

Відповідає за:
- Сканування папки plugins/
- Завантаження класів плагінів
- Реєстрацію активних плагінів у Flask app
- Надання меню-пунктів для sidebar
- Встановлення / видалення / увімкнення / вимкнення
"""

import importlib.util
import sys
from pathlib import Path
from typing import Optional

from core.plugin_base import BasePlugin


# Єдиний реєстр завантажених плагінів: slug → екземпляр BasePlugin
_registry: dict[str, BasePlugin] = {}


def _plugins_dir() -> Path:
    return Path(__file__).parent.parent / "plugins"


# ── Сканування та завантаження ────────────────────────────────────────

def scan_plugins() -> dict[str, BasePlugin]:
    """
    Сканує папку plugins/, завантажує класи, повертає dict slug→instance.
    НЕ реєструє в app — тільки читає метадані.
    """
    found: dict[str, BasePlugin] = {}
    plugins_dir = _plugins_dir()

    if not plugins_dir.exists():
        return found

    for folder in sorted(plugins_dir.iterdir()):
        if not folder.is_dir():
            continue
        plugin_file = folder / "plugin.py"
        if not plugin_file.exists():
            continue

        slug = folder.name
        try:
            spec = importlib.util.spec_from_file_location(
                f"plugins.{slug}.plugin", plugin_file
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"plugins.{slug}.plugin"] = module
            spec.loader.exec_module(module)

            if not hasattr(module, "Plugin"):
                continue

            instance: BasePlugin = module.Plugin()
            instance.slug = slug  # slug завжди = назва папки
            found[slug] = instance

        except Exception as e:
            print(f"[PluginManager] Помилка завантаження '{slug}': {e}")

    return found


def load_and_register(app) -> None:
    """
    Головна функція — викликається з main.py після register_modules().
    1. Сканує plugins/
    2. Перевіряє статус кожного в БД (таблиця plugins)
    3. Реєструє активні плагіни в Flask app
    4. Зберігає в _registry
    """
    global _registry

    from core.db import get_connection

    found = scan_plugins()
    if not found:
        return

    conn = get_connection()
    active_slugs = {
        r["slug"] for r in
        conn.execute("SELECT slug FROM plugins WHERE is_active = 1").fetchall()
    }
    conn.close()

    from core.plugin_api import get_api
    from core.hooks import get_registry
    api   = get_api()
    hooks = get_registry()

    for slug, plugin in found.items():
        _registry[slug] = plugin
        if slug in active_slugs:
            try:
                plugin._auto_register_hooks(hooks)
                plugin.register(app, api, hooks)
                _inject_blueprint_guard(app, slug)
            except Exception as e:
                print(f"[PluginManager] Помилка реєстрації '{slug}': {e}")


def _inject_blueprint_guard(app, slug: str) -> None:
    """
    Додає before_request guard до всіх blueprints плагіна.
    Якщо плагін вимкнений в БД — повертає 404 для всіх його маршрутів.
    """
    from flask import abort
    from core.db import get_connection

    def _make_guard(s):
        def _plugin_guard():
            try:
                c = get_connection()
                row = c.execute(
                    "SELECT is_active FROM plugins WHERE slug = ?", (s,)
                ).fetchone()
                c.close()
                if row and not row["is_active"]:
                    abort(404)
            except Exception:
                pass
        _plugin_guard.__name__ = f"_plugin_guard_{s}"
        return _plugin_guard

    for bp_name, bp in app.blueprints.items():
        if not bp.import_name or f"plugins.{slug}" not in bp.import_name:
            continue
        bp.before_request(_make_guard(slug))


def get_loaded_plugins() -> dict[str, BasePlugin]:
    """Повертає всі завантажені плагіни (slug → instance)."""
    return _registry


def get_plugin(slug: str) -> Optional[BasePlugin]:
    return _registry.get(slug)


# ── Меню для sidebar ──────────────────────────────────────────────────

def get_all_menu_items() -> list[dict]:
    """
    Збирає пункти меню від усіх активних плагінів.
    Викликається з контекстного процесора Flask.
    """
    from core.db import get_connection

    try:
        conn = get_connection()
        active = {
            r["slug"] for r in
            conn.execute("SELECT slug FROM plugins WHERE is_active = 1").fetchall()
        }
        conn.close()
    except Exception:
        return []

    items = []
    for slug, plugin in _registry.items():
        if slug in active:
            try:
                items.extend(plugin.get_menu_items())
            except Exception:
                pass
    return items


# ── Встановлення / видалення ──────────────────────────────────────────

def install_plugin(slug: str) -> dict:
    """
    Встановлює плагін: запускає on_install(), записує в БД.
    Повертає {"ok": True} або {"error": "..."}
    """
    plugin = _registry.get(slug)
    if not plugin:
        return {"error": f"Плагін '{slug}' не знайдено"}

    from core.db import get_connection
    conn = get_connection()
    try:
        plugin.on_install(conn)
        conn.execute("""
            INSERT INTO plugins (slug, name, version, description, author, is_active, installed_at)
            VALUES (?, ?, ?, ?, ?, 1, datetime('now','localtime'))
            ON CONFLICT(slug) DO UPDATE SET
                name = excluded.name,
                version = excluded.version,
                is_active = 1,
                installed_at = excluded.installed_at
        """, (slug, plugin.name, plugin.version, plugin.description, plugin.author))
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception as e:
        conn.close()
        return {"error": str(e)}


def uninstall_plugin(slug: str, keep_data: bool = True) -> dict:
    """
    Видаляє плагін з реєстру БД. keep_data=True — таблиці не чіпаємо.
    """
    plugin = _registry.get(slug)
    from core.db import get_connection
    conn = get_connection()
    try:
        if plugin and not keep_data:
            plugin.on_uninstall(conn)
        conn.execute("DELETE FROM plugins WHERE slug = ?", (slug,))
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception as e:
        conn.close()
        return {"error": str(e)}


def toggle_plugin(slug: str, active: bool) -> dict:
    """Увімкнути або вимкнути плагін без видалення."""
    plugin = _registry.get(slug)
    from core.db import get_connection
    from core.hooks import get_registry as get_hooks
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE plugins SET is_active = ? WHERE slug = ?",
            (1 if active else 0, slug)
        )
        conn.commit()
        conn.close()
        hooks = get_hooks()
        if plugin:
            if active:
                plugin._auto_register_hooks(hooks)
                plugin.on_enable()
            else:
                plugin._unregister_hooks(hooks)
                plugin.on_disable()
        return {"ok": True, "requires_restart": active}
    except Exception as e:
        conn.close()
        return {"error": str(e)}


def get_plugin_settings(slug: str) -> dict:
    """Читає збережені налаштування плагіна з таблиці plugin_settings."""
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT key, value FROM plugin_settings WHERE plugin_slug = ?", (slug,)
    ).fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def save_plugin_settings(slug: str, data: dict) -> None:
    """Зберігає налаштування плагіна."""
    from core.db import get_connection
    conn = get_connection()
    for key, value in data.items():
        conn.execute("""
            INSERT INTO plugin_settings (plugin_slug, key, value)
            VALUES (?, ?, ?)
            ON CONFLICT(plugin_slug, key) DO UPDATE SET value = excluded.value
        """, (slug, key, str(value)))
    conn.commit()
    conn.close()
