# Plugin API — Документація для розробників аддонів

> RecordSystem v1.x | Оновлено: 2026-03

---

## Огляд

Плагіни розміщуються в `plugins/<slug>/plugin.py` і реєструються через `Plugin(BasePlugin)`.

**Точка входу:**
```python
# plugins/my_addon/plugin.py
from core.plugin_base import BasePlugin

class Plugin(BasePlugin):
    name        = "Мій аддон"
    slug        = "my_addon"
    description = "Опис функціоналу"
    version     = "1.0.0"
    author      = "Ваше ім'я"

    def register(self, app, api, hooks):
        # Реєстрація Blueprint, хуків тощо
        pass
```

Плагін активується через інтерфейс **Система → Модулі розширення**.

---

## SystemAPI — доступ до даних

Об'єкт `api` передається в `register(app, api, hooks)`.

### PersonnelAPI — `api.personnel`

```python
# Список особового складу
people = api.personnel.get_list(
    search="Шокота",        # пошук по ПІБ
    group_id=1,             # фільтр по групі
    unit_id=2,              # фільтр по підрозділу
    is_active=1             # тільки активні
)
# → list[dict]: id, last_name, first_name, middle_name, rank, position,
#               unit_id, unit_name, group_id, group_name, is_active

# Одна картка
person = api.personnel.get(personnel_id=5)
# → dict або None

# Майно на картці
items = api.personnel.get_items(personnel_id=5)
# → list[dict]: item_id, item_name, quantity, price, category,
#               issue_date, status, invoice_id

# Підрозділи
units = api.personnel.get_units()
# → list[dict]: id, name, battalion_id, battalion_name

# Групи
groups = api.personnel.get_groups()
# → list[dict]: id, name, type

# Нотатки
notes = api.personnel.get_notes(entity_type="personnel", entity_id=5)
# → list[dict]: id, text, source, created_at

api.personnel.add_note(entity_type="personnel", entity_id=5,
                        text="Текст нотатки", source="my_addon")

# Вкладення
attachments = api.personnel.get_attachments(entity_type="personnel", entity_id=5)
# → list[dict]: id, original_name, description, source, file_path

api.personnel.attach_file(entity_type="personnel", entity_id=5,
                           file_path="/path/to/file.pdf",
                           original_name="документ.pdf",
                           description="Опис", source="my_addon")

# Оновити поле
api.personnel.update_field(personnel_id=5, field="position", value="Новa посада")
# Дозволені поля: rank, position, notes, group_id, unit_id
```

### WarehouseAPI — `api.warehouse`

```python
# Залишки складу
stock = api.warehouse.get_stock()
# → list[dict]: item_id, item_name, category, price, balance, unit_of_measure

# Журнал приходів
incomes = api.warehouse.get_incomes(item_id=None, date_from=None, date_to=None)
# → list[dict]: id, date, document_number, supplier, item_id, item_name,
#               quantity, price, category, source_type, scan_path

# Словник майна
items = api.warehouse.get_items(search=None, is_inventory=None)
# → list[dict]: id, name, unit_of_measure, has_serial_number, is_inventory

# Додати прихід (програмно)
income_id = api.warehouse.add_income(
    date="2026-03-15",
    item_id=3,
    quantity=10,
    price=250.00,
    category="II",
    document_number="АКТ-5",
    supplier="40 ОТБ",
    source_type="narad",   # income_doc | narad | attestat | manual
    created_by=user_id
)
```

### InvoiceAPI — `api.invoices`

```python
# Список накладних
invoices = api.invoices.get_list(
    status=None,           # draft | created | issued | processed | cancelled
    direction=None,        # issue | return | transfer
    date_from=None,
    date_to=None
)
# → list[dict]: id, number, status, direction, total_sum,
#               recipient_type, created_at, valid_until

# Одна накладна
inv = api.invoices.get(invoice_id=10)
# → dict або None, включає поле signatories (JSON)

# Позиції накладної
items = api.invoices.get_items(invoice_id=10)
# → list[dict]: id, item_id, item_name, planned_qty, actual_qty,
#               price, category, serial_numbers
```

### SettingsAPI — `api.settings`

```python
value = api.settings.get("company_name", default="А5027")
api.settings.set("my_addon_key", "значення")

all_settings = api.settings.get_all()
# → dict: {key: value, ...}
```

### DatabaseAPI — `api.db`

```python
conn = api.db.get_connection()
# → sqlite3.Connection з row_factory=sqlite3.Row
# ВАЖЛИВО: закривайте connection після використання

rows = api.db.query("SELECT * FROM personnel WHERE is_active=1")
# → list[sqlite3.Row]

api.db.execute("UPDATE settings SET value=? WHERE key=?", ("val", "key"))
# Виконує і комітить
```

### AuditAPI — `api.audit`

```python
api.audit.log(
    action="add",          # add | edit | delete | status_change | import
    table_name="invoices",
    record_id=10,
    old_data={"status": "draft"},
    new_data={"status": "created"},
    user_id=session.get("user_id")
)
```

---

## Hook System — події та слоти

### Підписка на події (Data Events)

```python
def register(self, app, api, hooks):
    hooks.on("invoice.created",   self._on_invoice_created)
    hooks.on("invoice.processed", self._on_invoice_processed)
    hooks.on("invoice.cancelled", self._on_invoice_cancelled)
    hooks.on("item.issued",       self._on_item_issued)
    hooks.on("warehouse.income",  self._on_warehouse_income)
    hooks.on("personnel.archived",self._on_personnel_archived)
    hooks.on("personnel.restored",self._on_personnel_restored)

def _on_invoice_created(self, invoice_id, data, **kw):
    # data = {"number": "2026/1/РС", "direction": "issue"}
    pass

def _on_invoice_processed(self, invoice_id, **kw):
    pass

def _on_item_issued(self, personnel_id, item_id, quantity, **kw):
    pass

def _on_warehouse_income(self, date, rows, **kw):
    # rows = list of dicts with item_id, quantity, price, category
    pass
```

### Авто-реєстрація через naming convention

Замість явного `hooks.on(...)` можна назвати методи за конвенцією:

```python
# on_<event_with_dots_as_double_underscore>
def on_invoice__created(self, invoice_id, data, **kw):
    pass
# слухає: invoice.created

def on_personnel__archived(self, personnel_id, **kw):
    pass
# слухає: personnel.archived
```

### UI Слоти (HTML вставки)

```python
# Додати HTML у слот
def ui_dashboard__widgets(self, **kw):
    return "<div class='card'>Мій віджет</div>"
# → вставляється в slot('dashboard.widgets') на дашборді

def ui_personnel__card__tabs(self, personnel_id, **kw):
    return [{"id": "my_tab", "label": "Мій таб", "icon": "bi-star"}]
# → додає таб на картку особового складу

def ui_personnel__card__tab_content(self, personnel_id, **kw):
    return "<div id='my_tab'>Вміст табу</div>"
# → вміст табу

def ui_sidebar__items(self, **kw):
    return [{"url": "/my-addon/", "icon": "bi-star", "label": "Мій аддон",
             "badge": {"class": "bg-danger", "text": "3"}}]
# → додає пункт у бічне меню

def ui_settings__sections(self, **kw):
    return [{"title": "Мій аддон", "content_html": "<form>...</form>"}]
# → додає секцію в Налаштування
```

**Доступні слоти:**
| Слот | Де відображається |
|------|------------------|
| `dashboard.widgets` | Дашборд |
| `personnel.list.filters` | Список о/с — фільтри |
| `personnel.list.columns` | Список о/с — додаткові колонки |
| `personnel.list.row_actions` | Список о/с — кнопки в рядку |
| `personnel.card.actions` | Картка о/с — кнопки вгорі |
| `personnel.card.tabs` | Картка о/с — таби |
| `personnel.card.tab_content` | Картка о/с — вміст табів |
| `sidebar.items` | Бічне меню |
| `settings.sections` | Налаштування |

---

## Реєстрація Blueprint

```python
from flask import Blueprint, render_template
from core.auth import login_required

def register(self, app, api, hooks):
    bp = Blueprint("my_addon", __name__,
                   url_prefix="/my-addon",
                   template_folder="templates")

    @bp.route("/")
    @login_required
    def index():
        return render_template("my_addon/index.html")

    app.register_blueprint(bp)
```

Шаблони розміщуйте в `plugins/my_addon/templates/my_addon/`.

---

## Приклад мінімального аддону

```python
# plugins/my_addon/plugin.py
from core.plugin_base import BasePlugin

class Plugin(BasePlugin):
    name    = "Статистика видачі"
    slug    = "issue_stats"
    version = "1.0.0"

    def register(self, app, api, hooks):
        hooks.on("invoice.processed", self._on_processed)

    def _on_processed(self, invoice_id, **kw):
        conn = self.api.db.get_connection()
        inv  = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        conn.close()
        self.api.audit.log("add", "issue_stats", invoice_id,
                           new_data={"total": inv["total_sum"] if inv else 0})
```

---

## Таблиці БД — довідник

### Основні таблиці

| Таблиця | Призначення |
|---------|-------------|
| `personnel` | Особовий склад |
| `units` | Підрозділи |
| `unit_responsible` | Відповідальні особи підрозділів (role_name: commander/supply_sergeant/other) |
| `item_dictionary` | Словник майна (has_serial_number, is_inventory) |
| `warehouse_income` | Прихід на склад |
| `item_serials` | Серійні номери (status: stock/issued/written_off) |
| `invoices` | Накладні на видачу |
| `invoice_items` | Позиції накладних |
| `personnel_items` | Майно на картці о/с |
| `unit_items` | Майно підрозділу |
| `settings` | Налаштування системи |
| `audit_log` | Журнал дій |
| `attachments` | Вкладення (entity_type + entity_id) |
| `notes` | Нотатки (entity_type + entity_id) |

### Статуси накладних

```
draft → created → issued → processed
                         → cancelled
```

- `draft` — чернетка, `number` = `ЧЕРНЕТКА-{timestamp}` (номер не присвоєно)
- `created` — номер присвоєно, передана командиру
- `issued` — повернулась підписана, внесена фактична видача
- `processed` — проведена у ФЕС, майно списано зі складу

### Поле `signatories` (JSON)

```json
[
  {"role": "Начальник речової служби", "rank": "капітан", "name": "Іваненко І.І.",
   "tag": "chief", "is_tvo": false},
  {"role": "Здав", "rank": "сержант", "name": "Коваль К.К.",
   "tag": "given", "is_tvo": false},
  {"role": "Прийняв", "rank": "солдат", "name": "Петренко П.П.",
   "tag": "received", "is_tvo": false},
  {"role": "Діловод РС", "rank": "ст. солдат", "name": "Бондар Б.Б.",
   "tag": "clerk", "is_tvo": false}
]
```

Теги: `chief` | `given` | `received` | `warehouse` | `clerk` | `""` (без мітки)

---

## Шорткоди шаблонів (TODO — Крок 2)

Будуть реалізовані в модулі шаблонів документів.

```
{{recipient_name}}      — ПІБ одержувача
{{recipient_rank}}      — звання одержувача
{{chief_name}}          — начальник служби
{{chief_rank}}          — звання начальника служби
{{invoice_number}}      — номер накладної
{{invoice_date}}        — дата накладної
{{unit_name}}           — назва частини
{{base_document}}       — підстава
{{total_sum}}           — загальна сума (цифрами)
{{total_sum_words}}     — загальна сума (прописом)
{{table:items_list}}    — таблиця позицій майна
{{table:signatories}}   — блок підписантів
```
