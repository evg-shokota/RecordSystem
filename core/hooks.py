"""
core/hooks.py — Система хуків і UI-слотів.

Три типи хуків:
  emit()         — Data hook: сповістити про подію (side-effects, результат ігнорується)
  filter_value() — Filter hook: змінити дані через ланцюг плагінів
  collect()      — UI hook: зібрати список dict від всіх плагінів
  collect_html() — UI hook: зібрати HTML-рядки від всіх плагінів

Використання в модулях системи:
    from core.hooks import emit, filter_value
    emit('personnel.archived', person_id=pid, reason=reason)
    params = filter_value('personnel.list.query', params)

Використання в шаблонах:
    {{ slot('dashboard.widgets') | safe }}
    {% set tabs = slot('personnel.card.tabs') %}
Author: White
"""

from __future__ import annotations
import logging
from typing import Any, Callable, Optional

log = logging.getLogger("hooks")


# ══════════════════════════════════════════════════════════════
#  КАТАЛОГ ПОДІЙ (константи — щоб не друкувати рядки вручну)
# ══════════════════════════════════════════════════════════════

# ── Data events ───────────────────────────────────────────────
EV_PERSONNEL_CREATED   = "personnel.created"
EV_PERSONNEL_UPDATED   = "personnel.updated"
EV_PERSONNEL_ARCHIVED  = "personnel.archived"
EV_PERSONNEL_RESTORED  = "personnel.restored"
EV_PERSONNEL_MOVED     = "personnel.moved"

EV_INVOICE_CREATED     = "invoice.created"
EV_INVOICE_PROCESSED   = "invoice.processed"
EV_INVOICE_CANCELLED   = "invoice.cancelled"

EV_SHEET_CREATED       = "sheet.created"
EV_SHEET_PROCESSED     = "sheet.processed"

EV_ATTESTAT_CREATED    = "attestat.created"
EV_ATTESTAT_SIGNED     = "attestat.signed"

EV_ITEM_ISSUED         = "item.issued"
EV_ITEM_RETURNED       = "item.returned"
EV_ITEM_WRITTEN_OFF    = "item.written_off"

EV_WAREHOUSE_INCOME    = "warehouse.income"
EV_WRITE_OFF_CREATED   = "write_off.created"

# ── Filter events ─────────────────────────────────────────────
FLT_PERSONNEL_LIST_QUERY    = "personnel.list.query"
FLT_INVOICE_FORM_DEFAULTS   = "invoice.form.defaults"
FLT_DASHBOARD_STATS         = "dashboard.stats"
FLT_ITEM_ISSUED_DATA        = "item.issued.data"

# ── UI events ─────────────────────────────────────────────────
# Дашборд
UI_DASHBOARD_WIDGETS            = "dashboard.widgets"

# Картка о/с
UI_PERSONNEL_CARD_TABS          = "personnel.card.tabs"
UI_PERSONNEL_CARD_TAB_CONTENT   = "personnel.card.tab_content"
UI_PERSONNEL_CARD_ACTIONS       = "personnel.card.actions"
UI_PERSONNEL_CARD_HEADER        = "personnel.card.header"

# Список о/с
UI_PERSONNEL_LIST_COLUMNS       = "personnel.list.columns"
UI_PERSONNEL_LIST_ROW_ACTIONS   = "personnel.list.row_actions"
UI_PERSONNEL_LIST_FILTERS       = "personnel.list.filters"

# Накладні
UI_INVOICE_FORM_SECTIONS        = "invoice.form.sections"
UI_INVOICE_VIEW_TABS            = "invoice.view.tabs"
UI_INVOICE_VIEW_TAB_CONTENT     = "invoice.view.tab_content"
UI_INVOICE_VIEW_ACTIONS         = "invoice.view.actions"

# Роздавальні відомості
UI_SHEET_VIEW_TABS              = "sheet.view.tabs"
UI_SHEET_VIEW_TAB_CONTENT       = "sheet.view.tab_content"

# Налаштування
UI_SETTINGS_SECTIONS            = "settings.sections"

# Sidebar (підрозділ в base.html)
UI_SIDEBAR_ITEMS                = "sidebar.items"

# Загальні
UI_PAGE_FOOTER_SCRIPTS          = "page.footer_scripts"
UI_PAGE_HEAD_EXTRA              = "page.head_extra"


# ══════════════════════════════════════════════════════════════
#  РЕЄСТР
# ══════════════════════════════════════════════════════════════

# UI-слоти що повертають HTML (collect_html)
_HTML_SLOTS: set[str] = {
    UI_DASHBOARD_WIDGETS,
    UI_PERSONNEL_CARD_TAB_CONTENT,
    UI_PERSONNEL_CARD_HEADER,
    UI_INVOICE_FORM_SECTIONS,
    UI_INVOICE_VIEW_TAB_CONTENT,
    UI_SHEET_VIEW_TAB_CONTENT,
    UI_PAGE_FOOTER_SCRIPTS,
    UI_PAGE_HEAD_EXTRA,
}

# UI-слоти що повертають список dict (collect)
_LIST_SLOTS: set[str] = {
    UI_PERSONNEL_CARD_TABS,
    UI_PERSONNEL_CARD_ACTIONS,
    UI_PERSONNEL_LIST_COLUMNS,
    UI_PERSONNEL_LIST_ROW_ACTIONS,
    UI_PERSONNEL_LIST_FILTERS,
    UI_INVOICE_VIEW_TABS,
    UI_INVOICE_VIEW_ACTIONS,
    UI_SHEET_VIEW_TABS,
    UI_SETTINGS_SECTIONS,
    UI_SIDEBAR_ITEMS,
}


class HookRegistry:
    """
    Центральний реєстр хуків.
    Один глобальний екземпляр (_hooks) на весь Flask-процес.
    """

    def __init__(self):
        # event_name → list of (priority, callback)
        self._data: dict[str, list[tuple[int, Callable]]] = {}

    def register(self, event: str, callback: Callable, priority: int = 10) -> None:
        """Зареєструвати callback для event."""
        if event not in self._data:
            self._data[event] = []
        self._data[event].append((priority, callback))

    def unregister_all(self, callbacks: list[Callable]) -> None:
        """Видалити всі вказані callbacks з усіх подій (при вимкненні плагіна)."""
        cb_set = set(id(cb) for cb in callbacks)
        for event in list(self._data):
            self._data[event] = [
                (p, cb) for p, cb in self._data[event]
                if id(cb) not in cb_set
            ]

    def _sorted(self, event: str) -> list[tuple[int, Callable]]:
        return sorted(self._data.get(event, []), key=lambda x: x[0])

    # ── Три типи виклику ──────────────────────────────────────

    def emit(self, event: str, **kwargs) -> None:
        """
        Data hook — виконати всі callbacks послідовно.
        Помилки логуються і не зупиняють виконання.
        """
        for _, cb in self._sorted(event):
            try:
                cb(**kwargs)
            except Exception as e:
                log.error("Hook error [%s] in %s: %s", event, _cb_name(cb), e)

    def filter(self, event: str, value: Any, **kwargs) -> Any:
        """
        Filter hook — передати value через ланцюг callbacks.
        Кожен callback отримує попередній результат.
        """
        for _, cb in self._sorted(event):
            try:
                result = cb(value, **kwargs)
                if result is not None:
                    value = result
            except Exception as e:
                log.error("Filter error [%s] in %s: %s", event, _cb_name(cb), e)
        return value

    def collect(self, event: str, **kwargs) -> list:
        """
        UI hook — зібрати список dict від всіх callbacks.
        Callback повертає dict або list[dict].
        """
        results = []
        for _, cb in self._sorted(event):
            try:
                result = cb(**kwargs)
                if result is None:
                    continue
                if isinstance(result, list):
                    results.extend(result)
                else:
                    results.append(result)
            except Exception as e:
                log.error("Collect error [%s] in %s: %s", event, _cb_name(cb), e)
        return results

    def collect_html(self, event: str, **kwargs) -> str:
        """
        UI hook — зібрати HTML від всіх callbacks і об'єднати в рядок.
        """
        parts = []
        for _, cb in self._sorted(event):
            try:
                html = cb(**kwargs)
                if html:
                    parts.append(str(html))
            except Exception as e:
                log.error("HTML error [%s] in %s: %s", event, _cb_name(cb), e)
        return "\n".join(parts)

    def clear(self) -> None:
        """Очистити всі хуки (для тестів)."""
        self._data.clear()


def _cb_name(cb: Callable) -> str:
    return getattr(cb, "__qualname__", repr(cb))


# ══════════════════════════════════════════════════════════════
#  ГЛОБАЛЬНИЙ РЕЄСТР
# ══════════════════════════════════════════════════════════════

_hooks = HookRegistry()


def get_registry() -> HookRegistry:
    return _hooks


# ── Публічні функції для модулів системи ─────────────────────

def emit(event: str, **kwargs) -> None:
    """Сповістити про подію. Використовувати в routes після будь-якої значимої дії."""
    _hooks.emit(event, **kwargs)


def filter_value(event: str, value: Any, **kwargs) -> Any:
    """Пропустити значення через ланцюг плагінів. Повертає змінене значення."""
    return _hooks.filter(event, value, **kwargs)


def collect(event: str, **kwargs) -> list:
    """Зібрати список від плагінів (для UI list-слотів)."""
    return _hooks.collect(event, **kwargs)


def collect_html(event: str, **kwargs) -> str:
    """Зібрати HTML від плагінів (для UI html-слотів)."""
    return _hooks.collect_html(event, **kwargs)


# ── Jinja2 slot() функція ─────────────────────────────────────

def make_slot_function() -> Callable:
    """
    Повертає функцію slot() для реєстрації в app.jinja_env.globals.

    В шаблоні:
        {{ slot('dashboard.widgets') | safe }}        ← HTML слот
        {% set tabs = slot('personnel.card.tabs') %}  ← list слот
    """
    def slot(name: str, **context) -> Any:
        if name in _HTML_SLOTS:
            return _hooks.collect_html(name, **context)
        elif name in _LIST_SLOTS:
            return _hooks.collect(name, **context)
        else:
            # Невідомий слот — повертаємо безпечне значення
            log.debug("Unknown slot: %s", name)
            return ""

    return slot
