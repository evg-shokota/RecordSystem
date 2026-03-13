"""
core/plugin_base.py — Базовий клас для всіх плагінів системи.

Мінімальний плагін:
─────────────────────────────────────────────
from core.plugin_base import BasePlugin

class Plugin(BasePlugin):
    name        = "Мій плагін"
    slug        = "my_plugin"
    version     = "1.0.0"
    description = "Короткий опис"
    author      = "Автор"
    icon        = "bi-puzzle"

    def register(self, app, api, hooks):
        from .routes import bp
        bp.api = api
        app.register_blueprint(bp)
─────────────────────────────────────────────

Декларативна реєстрація хуків (__ = крапка в імені події):

    def on_personnel__archived(self, person_id, reason):
        # виконується коли о/с архівується
        ...

    def on_invoice__processed(self, invoice_id):
        ...

    def filter_dashboard__stats(self, stats):
        stats['my_count'] = 42
        return stats

    def ui_personnel__card__tabs(self):
        return [{"id": "my_tab", "label": "Мій розділ", "icon": "bi-star"}]

    def ui_personnel__card__tab_content(self, tab_id, person):
        if tab_id != "my_tab":
            return None
        from flask import render_template
        return render_template("my_plugin/tab.html", person=person)

    def ui_settings__sections(self):
        return [{"title": "Мій модуль", "url": "/my/settings/",
                 "icon": "bi-star", "description": "Налаштування"}]

─────────────────────────────────────────────
Схема налаштувань:
    settings_schema = [
        {"key": "api_key", "label": "API ключ", "type": "text",   "default": ""},
        {"key": "enabled", "label": "Активно",  "type": "switch", "default": "1"},
        {"key": "mode",    "label": "Режим",     "type": "select", "default": "a",
         "options": [{"value": "a", "label": "А"}, {"value": "b", "label": "Б"}]},
    ]
    # type: text | number | switch | select | textarea

─────────────────────────────────────────────
Маппінг prefix_method → тип хука:
    on_*     → emit    (data hook)
    filter_* → filter  (filter hook)
    ui_*     → collect / collect_html  (ui hook — тип визначається реєстром)
"""


class BasePlugin:

    # ── Обов'язкові атрибути ──────────────────────────────────
    name:        str  = "Без назви"
    slug:        str  = ""
    version:     str  = "1.0.0"
    description: str  = ""
    author:      str  = ""
    icon:        str  = "bi-puzzle"

    # ── Опціональні атрибути ──────────────────────────────────
    settings_schema: list = []
    menu_items:      list = []

    # ── Обов'язковий метод ────────────────────────────────────

    def register(self, app, api, hooks=None) -> None:
        """
        Реєструє Blueprint(и) і явні хуки.

        app   — Flask application
        api   — SystemAPI (api.personnel, api.warehouse, api.invoices,
                            api.items, api.settings, api.db, api.audit)
        hooks — HookRegistry (для явної реєстрації hooks.register(event, cb))

        Декларативні хуки (on_*, filter_*, ui_*) реєструються автоматично
        до виклику register().
        """
        pass

    # ── Lifecycle ─────────────────────────────────────────────

    def on_install(self, conn) -> None:
        """Викликається при першому встановленні. conn — sqlite3.Connection."""
        pass

    def on_uninstall(self, conn) -> None:
        """Викликається при видаленні. За замовчуванням дані не видаляються."""
        pass

    def on_enable(self) -> None:
        """Викликається при активації встановленого плагіна."""
        pass

    def on_disable(self) -> None:
        """Викликається при деактивації."""
        pass

    # ── API helpers ───────────────────────────────────────────

    def get_menu_items(self) -> list:
        return self.menu_items

    def get_settings_schema(self) -> list:
        return self.settings_schema

    # ── Автоматична реєстрація декларативних хуків ───────────

    def _auto_register_hooks(self, hooks) -> None:
        """
        Сканує методи класу з префіксами on_/filter_/ui_
        і реєструє їх як callback відповідних хуків.

        Подвійне підкреслення __ в імені методу → крапка в імені події:
            on_personnel__archived     → personnel.archived
            ui_personnel__card__tabs   → personnel.card.tabs
        """
        self._registered_callbacks: list = []
        for attr_name in dir(type(self)):
            if attr_name.startswith("_"):
                continue
            method = getattr(self, attr_name, None)
            if not callable(method):
                continue

            for prefix in ("on_", "filter_", "ui_"):
                if not attr_name.startswith(prefix):
                    continue
                raw   = attr_name[len(prefix):]
                event = raw.replace("__", ".")
                hooks.register(event, method)
                self._registered_callbacks.append(method)
                break

    def _unregister_hooks(self, hooks) -> None:
        """Знімає всі раніше зареєстровані хуки плагіна."""
        callbacks = getattr(self, "_registered_callbacks", [])
        if callbacks:
            hooks.unregister_all(callbacks)

    # ── Service ───────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<Plugin {self.slug} v{self.version}>"
