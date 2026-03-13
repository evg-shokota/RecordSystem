"""
plugins/example_plugin/plugin.py — Повний приклад плагіна (шаблон).

Демонструє ВСІ можливості Plugin SDK:
  • SystemAPI (personnel, warehouse, invoices, items, settings, db, files, audit)
  • Data hooks  (on_*)
  • Filter hooks (filter_*)
  • UI hooks     (ui_*)
  • settings_schema, menu_items, on_install/on_uninstall

Щоб зробити власний плагін:
  1. Скопіюйте папку example_plugin → your_plugin
  2. Перейменуйте папку і змініть slug/name/description нижче
  3. Видаліть непотрібні хуки, залиште лише ті що треба
Author: White
"""
from core.plugin_base import BasePlugin


class Plugin(BasePlugin):

    # ── Обов'язкові атрибути ───────────────────────────────────────────
    name        = "Приклад модуля"
    slug        = "example_plugin"
    version     = "1.0.0"
    description = "Повний демонстраційний модуль — шаблон для написання власних розширень"
    author      = "White"
    icon        = "bi-puzzle-fill"

    # ── Налаштування модуля (відображаються у /plugins/<slug>/settings) ─
    settings_schema = [
        {
            "key":     "enabled",
            "label":   "Модуль активний",
            "type":    "switch",
            "default": "1",
        },
        {
            "key":     "title",
            "label":   "Заголовок сторінки",
            "type":    "text",
            "default": "Мій модуль",
            "help":    "Відображається в заголовку сторінки модуля",
        },
        {
            "key":     "notify_email",
            "label":   "E-mail для сповіщень",
            "type":    "text",
            "default": "",
            "help":    "Залиште порожнім якщо сповіщення не потрібні",
        },
        {
            "key":     "max_items",
            "label":   "Максимум позицій у звіті",
            "type":    "number",
            "default": "100",
        },
        {
            "key":     "report_mode",
            "label":   "Режим звіту",
            "type":    "select",
            "options": [
                {"value": "short",    "label": "Короткий"},
                {"value": "full",     "label": "Повний"},
                {"value": "extended", "label": "Розширений"},
            ],
            "default": "short",
        },
    ]

    # ── Пункти меню у розділі «Модулі» ────────────────────────────────
    # Якщо визначено — плагін автоматично з'являється у розділі «Модулі» сайдбару.
    # НЕ визначайте ui_sidebar__items якщо вже маєте menu_items — буде дублікат.
    menu_items = [
        {"label": "Приклад", "url": "/example/", "icon": "bi-puzzle-fill"},
    ]

    # ══════════════════════════════════════════════════════════════════
    # register() — реєстрація Blueprint і збереження api
    # ══════════════════════════════════════════════════════════════════
    def register(self, app, api, hooks=None):
        """
        Викликається одноразово при старті додатку (після install).

        Параметри
        ---------
        app   — Flask application object
        api   — SystemAPI instance (доступ до даних системи)
        hooks — HookRegistry (зазвичай не потрібен: хуки реєструються
                автоматично через _auto_register_hooks())

        SystemAPI:
          api.personnel.get_list(**filters)   — список о/с
          api.personnel.get(id)               — одна картка
          api.personnel.get_items(id)         — майно на картці
          api.warehouse.get_stock()           — залишки складу
          api.invoices.get_list(**filters)    — накладні
          api.invoices.get(id)               — одна накладна
          api.items.get_list()               — словник майна
          api.items.get(id)                  — одна позиція
          api.settings.get(key, default)     — налаштування системи
          api.settings.set(key, value)       — змінити налаштування
          api.db.execute(sql, params)        — SELECT → list[dict]
          api.db.write(sql, params)          — INSERT/UPDATE/DELETE
          api.files.save(file_obj, category) — зберегти файл
          api.files.get_url(path)            — отримати URL файлу
          api.audit.log(action, details)     — запис у журнал подій
        """
        self.api = api
        from .routes import bp
        bp.api = api
        app.register_blueprint(bp)

    # ══════════════════════════════════════════════════════════════════
    # Lifecycle hooks
    # ══════════════════════════════════════════════════════════════════
    def on_install(self, conn):
        """Викликається при першому встановленні плагіна."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS example_plugin_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event      TEXT NOT NULL,
                details    TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()

    def on_uninstall(self, conn):
        """Викликається при видаленні плагіна (keep_data=False)."""
        # Розкоментуйте якщо хочете видаляти дані при деінсталяції:
        # conn.execute("DROP TABLE IF EXISTS example_plugin_log")
        pass

    # ══════════════════════════════════════════════════════════════════
    # Data hooks  (on_<event__with__dots__as__double_underscore>)
    # Підпис: def on_<name>(self, **kwargs)
    # Метод спрацьовує ПІСЛЯ події, повертане значення ігнорується.
    # ══════════════════════════════════════════════════════════════════

    def on_personnel__created(self, person_id, data, **kwargs):
        """Спрацьовує коли додано нового о/с."""
        self.api.audit.log(
            "example_plugin",
            f"Новий о/с id={person_id}: {data.get('last_name', '')}",
        )

    def on_personnel__updated(self, person_id, data, **kwargs):
        """Спрацьовує після оновлення картки о/с."""
        pass

    def on_personnel__archived(self, person_id, reason, **kwargs):
        """Спрацьовує коли о/с переводиться в архів."""
        # Записати у власну таблицю
        self.api.db.write(
            "INSERT INTO example_plugin_log (event, details) VALUES (?, ?)",
            ("personnel.archived", f"id={person_id} reason={reason}"),
        )

    def on_personnel__restored(self, person_id, **kwargs):
        """Спрацьовує коли о/с відновлено з архіву."""
        pass

    def on_invoice__created(self, invoice_id, data, **kwargs):
        """Спрацьовує після створення нової накладної."""
        pass

    def on_invoice__approved(self, invoice_id, **kwargs):
        """Спрацьовує після затвердження накладної (списання/видача)."""
        self.api.audit.log(
            "example_plugin",
            f"Накладна #{invoice_id} затверджена",
        )

    def on_invoice__deleted(self, invoice_id, **kwargs):
        """Спрацьовує після видалення накладної."""
        pass

    def on_warehouse__received(self, invoice_id, items, **kwargs):
        """Спрацьовує після надходження майна на склад."""
        pass

    def on_warehouse__issued(self, invoice_id, items, **kwargs):
        """Спрацьовує після видачі майна зі складу."""
        pass

    def on_settings__saved(self, key, value, **kwargs):
        """Спрацьовує після зміни налаштувань системи."""
        pass

    # ══════════════════════════════════════════════════════════════════
    # Filter hooks  (filter_<event>)
    # Отримує поточне значення, повертає змінене.
    # ══════════════════════════════════════════════════════════════════

    def filter_personnel__display_name(self, value, person=None, **kwargs):
        """
        Дозволяє змінити відображуване ім'я о/с.
        value  — поточне (напр. "Іваненко І.І.")
        person — dict з даними картки
        Повертає рядок.
        """
        # Приклад: додати позначку якщо є особлива умова
        # if person and person.get('rank') == 'генерал':
        #     return f"★ {value}"
        return value

    def filter_invoice__can_delete(self, value, invoice=None, **kwargs):
        """
        Дозволяє заборонити видалення накладної.
        value   — True/False (поточна відповідь системи)
        invoice — dict з даними накладної
        Повертає True/False.
        """
        return value

    def filter_sidebar__menu(self, items, **kwargs):
        """
        Дозволяє змінити список пунктів бічного меню.
        items — list[dict] з ключами label, url, icon
        Повертає змінений list.
        """
        return items

    # ══════════════════════════════════════════════════════════════════
    # UI hooks  (ui_<slot__with__dots>)
    # Повертають dict або list[dict] (для tabs/actions/items)
    # або HTML-рядок (для *__content та *__html слотів).
    # ══════════════════════════════════════════════════════════════════

    # ── Картка о/с ────────────────────────────────────────────────────

    def ui_personnel__card__tabs(self, person=None, **kwargs):
        """
        Додає вкладку на картку о/с.
        Повертає dict або list[dict]:
          id    — унікальний ідентифікатор вкладки
          label — назва вкладки
          icon  — Bootstrap Icons клас (необов'язково)
        """
        return {"id": "example", "label": "Приклад", "icon": "bi-puzzle"}

    def ui_personnel__card__tab_content(self, tab_id=None, person=None, **kwargs):
        """
        Повертає HTML-вміст для вкладки.
        Викликається для КОЖНОЇ вкладки — перевіряйте tab_id.
        """
        if tab_id != "example":
            return ""
        name = f"{person.get('last_name','')} {person.get('first_name','')}" if person else "—"
        return f"""
        <div class="p-4">
            <div class="alert alert-info border-0">
                <i class="bi bi-puzzle-fill me-2"></i>
                Вміст від модуля «Приклад» для <strong>{name}</strong>
            </div>
            <p class="text-muted small">
                Тут можна відображати довільні дані пов'язані з цим о/с —
                наприклад, власну таблицю нарахувань, журнал подій тощо.
            </p>
        </div>
        """

    def ui_personnel__card__actions(self, person=None, **kwargs):
        """
        Додає кнопку в topbar картки о/с.
        Повертає dict або list[dict]:
          label — текст кнопки
          url   — посилання (або '#' для JS-дій)
          icon  — Bootstrap Icons клас
          class — CSS-клас кнопки (напр. btn-outline-primary)
        """
        pid = person["id"] if person else 0
        return {
            "label": "Дія прикладу",
            "url":   f"/example/person/{pid}",
            "icon":  "bi-puzzle",
            "class": "btn-outline-secondary",
        }

    # ── Список о/с ────────────────────────────────────────────────────

    def ui_personnel__list__columns(self, **kwargs):
        """
        Додає колонку в таблицю списку о/с.
        Повертає dict або list[dict]:
          key    — ключ даних (або None для обчислювальних)
          label  — заголовок колонки
          render — необов'язково: JS-функція як рядок
        """
        return {"key": "example_flag", "label": "Прим."}

    def ui_personnel__list__row(self, person=None, **kwargs):
        """
        Повертає HTML для додаткової комірки в рядку списку о/с.
        """
        return '<span class="badge bg-secondary">ok</span>'

    # ── Картка майна ─────────────────────────────────────────────────

    def ui_item__card__tabs(self, item=None, **kwargs):
        """Додає вкладку на картку позиції майна."""
        return {"id": "example_item", "label": "Приклад", "icon": "bi-puzzle"}

    def ui_item__card__tab_content(self, tab_id=None, item=None, **kwargs):
        """Повертає HTML для вкладки картки майна."""
        if tab_id != "example_item":
            return ""
        return '<div class="p-4 text-muted">Вміст від модуля «Приклад»</div>'

    # ── Сторінка налаштувань ──────────────────────────────────────────

    def ui_settings__sections(self, **kwargs):
        """
        Додає секцію в сторінку налаштувань системи.
        Повертає dict або list[dict]:
          title       — назва секції
          description — короткий опис
          url         — посилання (напр. /plugins/<slug>/settings)
          icon        — Bootstrap Icons клас
        """
        return {
            "title":       "Налаштування модуля «Приклад»",
            "description": "Параметри прикладного модуля",
            "url":         "/plugins/example_plugin/settings",
            "icon":        "bi-puzzle-fill",
        }

    # ── Бічна панель (sidebar) ────────────────────────────────────────
    # НЕ визначайте цей метод якщо вже маєте menu_items — буде дублікат!
    # ui_sidebar__items залишається у «Розширення», menu_items — у «Модулі».
    #
    # def ui_sidebar__items(self, **kwargs):
    #     return {"label": "Приклад", "url": "/example/", "icon": "bi-puzzle-fill"}

    # ── Dashboard ─────────────────────────────────────────────────────

    def ui_dashboard__widgets(self, **kwargs):
        """
        Додає HTML-віджет на головну сторінку (dashboard).
        Повертає HTML-рядок — він вставляється у слот як є.
        """
        try:
            count = len(self.api.personnel.get_list(limit=9999))
            return f"""
            <div class="col-md-3">
                <div class="card border-0 shadow-sm">
                    <div class="card-header bg-transparent small fw-medium py-2">
                        <i class="bi bi-puzzle-fill me-1 text-primary"></i>Приклад: о/с
                    </div>
                    <div class="card-body text-center py-3">
                        <div style="font-size:2rem;color:var(--app-primary)">{count}</div>
                        <div class="text-muted small">Особового складу</div>
                    </div>
                </div>
            </div>"""
        except Exception:
            return ""
