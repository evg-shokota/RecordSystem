# КАРТА ПРОЕКТУ — Система обліку речового майна А5027
Версія: 1.6 | Початок розробки: Березень 2026 | Оновлено: 2026-03-12

---

## ТЕХНІЧНИЙ СТЕК
- **Бекенд:** Python 3.x + Flask (blueprint-архітектура)
- **Оболонка:** PyWebView (запуск як нативний .exe)
- **База даних:** SQLite (`database.db`) — WAL-режим, foreign keys ON
- **Сховище файлів:** зовнішній HDD (сканування документів — `scans/`)
- **Збірка:** PyInstaller + інсталятор (перевірка WebView2)
- **UI:** Bootstrap 5.3 + Bootstrap Icons (офлайн), JS ванільний
- **Запуск dev:** `py run_dev.py` → http://127.0.0.1:5050
- **Python:** запускати через `py` (не python/python3)

---

## СТРУКТУРА ПАПОК

```
RecordSystem/
├── main.py                  ← точка входу: Flask app, context_processor, register_blueprints/plugins
├── run_dev.py               ← запуск без PyWebView (браузер)
├── database.db              ← база даних SQLite
├── PROJECT_MAP.md           ← ця карта проекту
├── docs/
│   └── PLUGIN_API.md        ← документація API для розробників аддонів
├── core/
│   ├── db.py                ← підключення БД, CREATE TABLE, _migrate(), _insert_defaults()
│   ├── auth.py              ← авторизація, сесії, ролі, login_required
│   ├── settings.py          ← get_setting / set_setting / update_settings / get_all_settings
│   ├── backup.py            ← автобекап (ротація: день/тиждень/місяць/рік)
│   ├── audit.py             ← log_action()
│   ├── hooks.py             ← HookRegistry, emit/filter/collect/collect_html, make_slot_function()
│   ├── plugin_base.py       ← BasePlugin + _auto_register_hooks (naming convention)
│   ├── plugin_manager.py    ← load_and_register(), get_loaded_plugins(), get_all_menu_items()
│   └── plugin_api.py        ← SystemAPI: PersonnelAPI, WarehouseAPI, InvoiceAPI,
│                               ItemDictionaryAPI, SettingsAPI, DatabaseAPI, AuditAPI, FilesAPI
├── modules/
│   ├── personnel/routes.py  ← CRUD картотеки о/с (704 рядки) + emit() хуки
│   ├── warehouse/routes.py  ← склад: залишки, прихід, інвентаризація, скан документів
│   ├── invoices/routes.py   ← накладні: CRUD, статуси, проведення, підписанти-конструктор
│   ├── settings/routes.py   ← налаштування: реквізити, підрозділи, групи, словник, типи документів
│   └── plugins/routes.py    ← керування плагінами (активація/деактивація)
├── plugins/
│   └── example_plugin/      ← шаблон плагіна з прикладами всіх хуків
├── templates/
│   ├── base.html            ← Bootstrap sidebar + slot('sidebar.items') + {{ unit_name }}
│   ├── dashboard.html       ← дашборд зі статистикою + slot('dashboard.widgets')
│   ├── login.html / setup.html
│   ├── personnel/           ← index.html, card.html, form.html
│   ├── warehouse/           ← index.html, income_list.html, income_form.html,
│   │                           inventory_list/form/view/print.html
│   ├── invoices/            ← index.html, form.html (конструктор+перегляд), view.html
│   └── settings/            ← index.html, general.html, units.html, ...
├── static/
│   ├── css/bootstrap.min.css, bootstrap-icons.min.css, main.css
│   └── js/bootstrap.bundle.min.js, main.js
├── scans/                   ← скани документів приходів (auto-created)
├── exports/                 ← Excel/PDF експорти
├── backups/                 ← автоматичні резервні копії БД
└── logs/                    ← журнал дій
```

---

## АРХІТЕКТУРНІ РІШЕННЯ

### Plugin System
- Плагіни: `plugins/<slug>/plugin.py` → клас `Plugin(BasePlugin)`
- Активні плагіни — в таблиці `plugins` де `is_active=1`
- `_auto_register_hooks()` — naming convention:
  - `on_invoice__created` → слухає `invoice.created`
  - `ui_dashboard__widgets` → повертає HTML для слота `dashboard.widgets`
  - `__ (подвійне підкреслення)` = крапка в назві події
- `register(app, api, hooks)` — реєстрація Blueprint і явних хуків
- **Не плутати:** `plugin_manager.get_loaded_plugins()` ≠ `hooks.get_registry()`

### Slot System
- `_HTML_SLOTS` → `collect_html()` → повертають рядок HTML
- `_LIST_SLOTS` → `collect()` → повертають список dict
- В шаблоні: `{{ slot('dashboard.widgets') | safe }}` або `{% for item in slot('settings.sections') %}`
- `_inject_globals` в `main.py` передає `slot` і `unit_name` у всі шаблони

### Динамічна назва підрозділу (unit_name)
- `main.py` context processor: `unit_name = get_setting("company_name", "А5027")`
- Змінити: **Налаштування → Реквізити → Назва підрозділу**

### БД — міграції
- `_migrate()` в `db.py` — `ALTER TABLE ADD COLUMN` в try/except
- Викликається при кожному `init_db()`, безпечно для існуючих БД

### Нумерація документів
- Формат: `2026/1/РС`, `2026/30/РС` (без ведучих нулів)
- Таблиця `doc_sequences`: `doc_type, year, sequence, suffix`
- Номер присвоюється тільки при переході з `draft → created`

---

## БАЗА ДАНИХ — АКТУАЛЬНА СХЕМА (34+ таблиці)

### Системні
| Таблиця | Призначення |
|---------|-------------|
| `roles` | Ролі: Адміністратор, Діловод, Начальник складу |
| `users` | Користувачі системи |
| `settings` | Ключ-значення налаштувань |
| `audit_log` | Журнал всіх дій |
| `plugins` | Реєстр плагінів (slug, is_active, metadata) |
| `doc_sequences` | Лічильники нумерації документів |

### Структура підрозділів
| Таблиця | Призначення |
|---------|-------------|
| `battalions` | Батальйони (рівень 1) |
| `units` | Підрозділи (рівень 2) |
| `platoons` | Взводи (рівень 3, опційно) |
| `groups` | Групи: Картотека / БЕЗ ГРУПИ / Вибувші / СЗЧ / Загиблі / Безвісті |
| `unit_responsible` | Відповідальні особи підрозділів (role_name: commander/supply_sergeant/other) |

### Особовий склад
| Таблиця | Призначення |
|---------|-------------|
| `personnel` | Картки о/с (ПІБ, звання, розміри, підрозділ, група) |
| `personnel_history` | Архівні блоки при реактивації |
| `personnel_items` | Майно на картці о/с (invoice_id, status: active/written_off/returned) |

### Склад
| Таблиця | Призначення |
|---------|-------------|
| `item_dictionary` | Словник майна (has_serial_number, is_inventory, unit_of_measure) |
| `warehouse_income` | Прихід на склад (category: I/II/III, nom_code, source_type, **scan_path**, **scan_original_name**) |
| `item_serials` | Серійні номери (status: stock/issued/written_off, warehouse_income_id) |
| `inventories` | Інвентаризації (date, commission_members, status) |
| `inventory_items` | Позиції інвентаризації (qty_expected vs qty_actual) |

### Накладні
| Таблиця | Призначення |
|---------|-------------|
| `invoices` | Накладні (status: **draft**/created/issued/processed/cancelled) |
| `invoice_items` | Позиції накладної (planned_qty, actual_qty, serial_numbers JSON) |

### Документи
| Таблиця | Призначення |
|---------|-------------|
| `distribution_sheets` | Роздавальні відомості (РВ) |
| `distribution_sheet_rows` | Рядки РВ (personnel_id, отримані позиції) |
| `unit_items` | Майно на картці підрозділу |
| `attestats` | Речові атестати |
| `attestat_items` | Позиції атестатів |
| `write_offs` | Акти списання |
| `write_off_items` | Позиції актів списання |
| `exploitation_acts` | Акти введення в експлуатацію |
| `registries` | Реєстри документів до ФЕС |
| `registry_items` | Позиції реєстрів |

### Довідники
| Таблиця | Призначення |
|---------|-------------|
| `document_types` | Типи документів (Накладна, РВ, Акт, Атестат...) |
| `norm1` | Норма №1 (довідкова, норми видачі) |
| `attachments` | Вкладення (entity_type + entity_id, original_name, source) |
| `notes` | Нотатки (entity_type + entity_id) |

### Ключові поля `invoices`
```
number TEXT UNIQUE          — формат 2026/1/РС (для draft: ЧЕРНЕТКА-{timestamp})
status TEXT                 — draft | created | issued | processed | cancelled
direction TEXT              — issue | return | transfer
recipient_type TEXT         — personnel | unit
recipient_personnel_id      — одержувач-особа
recipient_unit_id           — одержувач-підрозділ
sender_unit_id              — підрозділ-відправник (для transfer)
sender_personnel_id         — особа що повертає (для return)
signatories TEXT (JSON)     — масив підписантів [{role,rank,name,tag,is_tvo}]
base_document TEXT          — підстава (наказ, норма)
valid_until TEXT            — термін дії
total_sum REAL
scan_path TEXT
```

### Поле `signatories` (JSON-масив)
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

## НАЛАШТУВАННЯ СИСТЕМИ (settings keys)

| Ключ | Призначення |
|------|-------------|
| `company_name` | Назва підрозділу (відображається скрізь як unit_name) |
| `service_name` | Назва служби |
| `chief_name` | Начальник речової служби (ПІБ) |
| `chief_rank` | Звання начальника |
| `chief_is_tvo` | ТВО (1/0) |
| `chief_tvo_name` | ПІБ ТВО |
| `chief_tvo_rank` | Звання ТВО |
| `warehouse_chief_name` | Начальник складу (ПІБ) |
| `warehouse_chief_rank` | Звання начальника складу |
| `clerk_name` | Діловод РС (ПІБ) |
| `clerk_rank` | Звання діловода |
| `invoice_suffix` | Суфікс накладних (РС) |
| `invoice_valid_days` | Термін дії накладної за замовчуванням |
| `rv_suffix` | Суфікс РВ |
| `backup_reminder_days` | Нагадування про бекап (днів) |

---

## СТАТУСИ ТА БІЗНЕС-ЛОГІКА

### Накладна (invoices)
```
draft → created → issued → processed
              ↘         ↘
           cancelled   cancelled
```
- **draft** — чернетка, номер не присвоєно. Вільно редагується. Видалити можна.
- **created** — номер присвоєно (`assign_number` route). Передана відповідальній особі.
- **issued** — повернулась підписана. Внесена фактична кількість (може відрізнятись від плану).
- **processed** — проведена у ФЕС. Майно списано зі складу → записано на картку о/с або підрозділу.
- **cancelled** — скасована з коментарем. Зберігається в архіві.

**Проведення (process):**
1. Перевіряє залишки по (item_id, category, price)
2. Серійні номери → status='issued' в item_serials
3. Записує в `personnel_items` або `unit_items`
4. Emit: `invoice.processed`, `item.issued`

### Підписанти — автологіка по напрямку
| Напрямок | Здав | Прийняв |
|----------|------|---------|
| Видача (issue) | Начальник складу | Одержувач |
| Повернення (return) | Особа що повертає | Начальник складу |
| Переміщення (transfer) | Відп. особа підрозділу-відправника | Відп. особа підрозділу-приймача |

### Склад — розрахунок залишків
```
залишок = SUM(warehouse_income.quantity) - SUM(invoice_items.actual_qty WHERE invoice.status='processed')
групування по: (item_id, category, price)
```

### Номенклатурне майно
- Включається в атестат завжди (навіть якщо строк вийшов)
- При вибутті (Вибувші) — блокування архівування до здачі
- При СЗЧ/Загиблі/Безвісті — НЕ блокується, але відображається на дашборді

### Резервне копіювання
- Щоденні: 7 → Недільний: 1 → Місячний: 1 → Річний: 1
- Ручний: будь-коли через Налаштування

---

## МОДУЛІ — СТАТУС РОЗРОБКИ

### ✅ ЗАВЕРШЕНО

#### Ядро (core/)
- `db.py` — 34+ таблиці, повна схема + `_migrate()` для оновлення існуючих БД
- `auth.py` — авторизація, сесії, ролі, `login_required`
- `settings.py` — `get_setting / set_setting / update_settings`
- `backup.py` — резервне копіювання з ротацією
- `plugin_base.py` — `BasePlugin` + `_auto_register_hooks`
- `plugin_manager.py` — `get_loaded_plugins()`, `get_all_menu_items()`
- `plugin_api.py` — `SystemAPI` з усіма субAPI
- `hooks.py` — `HookRegistry`, `emit/filter/collect/collect_html`, `make_slot_function()`

#### Точки входу
- `main.py` — Flask app, `fromjson` Jinja2 filter, `_inject_globals` context processor
- `run_dev.py` — dev-запуск без PyWebView

#### Особовий склад
- `modules/personnel/routes.py` — повний CRUD (704 рядки) + emit() хуки
- `templates/personnel/index.html` — список + масові дії + слоти
- `templates/personnel/card.html` — картка + слоти для плагінів
- `templates/personnel/form.html` — форма додавання/редагування

#### Склад
- `modules/warehouse/routes.py` — залишки, прихід, інвентаризація, скан
- `templates/warehouse/index.html` — залишки з фільтром
- `templates/warehouse/income_list.html` — журнал + іконка скану
- `templates/warehouse/income_form.html` — мультирядкова + серійні + завантаження скану
- `templates/warehouse/inventory_*.html` — інвентаризація (форма, перегляд, друк A4)
- Excel-експорт інвентаризації (`/inventory/<id>/export/xlsx`)
- Route для скачування сканів: `/warehouse/income/<id>/scan`

#### Накладні
- `modules/invoices/routes.py` — повний модуль
- `templates/invoices/index.html` — список з фільтром + чернетки
- `templates/invoices/form.html` — конструктор підписантів + попередній перегляд + поля для return/transfer
- `templates/invoices/view.html` — перегляд + всі кнопки дій

#### Налаштування
- `modules/settings/routes.py` — реквізити, підрозділи, групи, словник, типи документів
- `templates/settings/general.html` — реквізити + начальник складу + нумерація

#### Плагіни
- `modules/plugins/routes.py` — активація/деактивація
- `plugins/example_plugin/` — повний шаблон з прикладами всіх хуків
- `docs/PLUGIN_API.md` — повна документація API для розробників

#### Картки підрозділів (Крок 3)
- `modules/settings/routes.py` — додано: `unit_card`, `unit_responsible_add/edit/toggle/delete`
- `templates/settings/unit_card.html` — відповідальні особи + о/с + майно підрозділу
- В `settings/units.html` — кнопка переходу на картку підрозділу
- В `personnel/card.html` — підрозділ — посилання на картку підрозділу

#### Шаблони документів (Крок 2)
- `modules/doc_templates/routes.py` — CRUD + редактор + демо-перегляд
- `templates/doc_templates/index.html` — список з групуванням по типах документів
- `templates/doc_templates/editor.html` — редактор: toolbar, шорткоди, вставка таблиці, демо-перегляд
- `templates/doc_templates/preview.html` — перегляд з демоданими (підстановка реальних реквізитів)
- Blueprint `/doc-templates/`, sidebar в секції "Документи"
- Шорткоди: документ, одержувач, підписанти, організація, таблиці
- Preview-inline AJAX endpoint для перегляду без збереження

### 🔄 В ЧЕРЗІ (узгоджений порядок)

#### Крок 2 — Шаблони документів ← ПОТОЧНИЙ
- `modules/doc_templates/routes.py` — CRUD шаблонів ✅ (2026-03-12)
- `templates/doc_templates/editor.html` — редактор contenteditable + toolbar + шорткоди ✅
- `templates/doc_templates/index.html` — список з групуванням по типах ✅
- `templates/doc_templates/preview.html` — перегляд з демоданими ✅
- `doc_templates` blueprint зареєстровано в `main.py` ✅
- "Шаблони" в sidebar в секції "Документи" ✅
- TODO: вибір шаблону при формуванні конкретного документа (накладна, РВ тощо)
- TODO: розширені шорткоди для таблиць (template_tables як окремі об'єкти)
- Таблиця `doc_templates` в БД (вже є)

#### Крок 3 — Картки підрозділів ✅ (2026-03-12)
- `/settings/units/<uid>/card` — картка підрозділу
- Відповідальні особи (`unit_responsible`): додати/редагувати/деактивувати/видалити (AJAX)
- Вибір відповідальних з картотеки о/с або ввести вручну
- Список о/с підрозділу з посиланнями на картки
- Майно підрозділу (`unit_items` зі статусом `active`) — з маркером НМ
- Кнопка "Картка підрозділу" в списку підрозділів (settings/units.html)
- Посилання на картку підрозділу з картки о/с (personnel/card.html)

#### Крок 4 — Роздавальні відомості (РВ)
- Групова видача (список о/с + перелік позицій)
- Таблиця відомості: рядки = о/с, колонки = позиції майна
- Часткове отримання (чекбокс по рядку, фактична кількість)
- Статуси: `draft → created → signed → processed`
- Нумерація: `2026/1/РВ`

#### Крок 5 — Атестат речовий
- Формування атестату при вибутті о/с
- Включення: всі `personnel_items` зі статусом `active`
- Номенклатурне майно — завжди в атестаті
- Блокування архівування до видачі/закриття атестату
- Друк + PDF

#### Крок 6 — Планування видачі
- Порівняння наявного майна о/с з Нормою №1
- Список нестач по особах і підрозділах
- Зведена потреба vs залишки складу
- Норма: `item_dictionary.officer_norm_qty / soldier_norm_qty` + `wear_period`
- Сезонність: `demi | winter | summer`

#### Крок 7 — Звіти
- Залишки на дату, оборот за період
- Борги по нормі, зведена відомість
- Реєстр документів до ФЕС (`registries`)
- Фільтри: по сезонності / підрозділу / групі
- Друк / Excel-експорт

#### Крок 8 — Імпорт/Експорт
- Імпорт 179 карток о/с з Excel (`Картки_особового_складу_А5027_3.xlsx`)
- Режим попереднього перегляду перед імпортом
- Обробка конфліктів (дублі по ПІБ або IPN)
- Експорт картотеки в Excel

#### Крок 9 — Реєстр документів
- Формування реєстру до ФЕС
- Таблиця `registries` + `registry_items`
- Прив'язка до реальних накладних/РВ/атестатів
- Нумерація: `2026/1/РЄ`

#### Крок 10 — Акти списання і введення в експлуатацію
- Акт списання: `write_offs` + `write_off_items`
- Акт введення в експлуатацію: `exploitation_acts`
- Пов'язані з `personnel_items` та `unit_items`

#### Крок 11 — Фінальна збірка
- PyInstaller + інсталятор (перевірка WebView2)
- Тестування на чистому Windows 10/11
- Документація користувача

### ❌ НЕ РЕАЛІЗОВАНО (майбутнє / за умов доступу)
- SAP-заявки (окремий додаток, після доступу до програми)
- Передача змін між базами (синхронізація)
- Мобільна версія (сканування у полі)

---

## КЛЮЧОВІ ПРАВИЛА СИСТЕМИ

### Зберігання файлів
- Скани документів: `scans/<uuid>.<ext>` поруч з `database.db`
- Фото о/с: зовнішній HDD (шлях в settings)
- Формати сканів: PDF, JPG, PNG, TIFF

### Друк і поля
- Книжний: верх 2, низ 2, ліво 3, право 1 (см)
- Двосторонній (парний аркуш): ліво 1, право 3

### Відповідальна особа підрозділу (для переміщення)
- Береться з таблиці `unit_responsible` WHERE `unit_id=? AND is_active=1`
- role_name: `commander` | `supply_sergeant` | `other`

---

## ЗРАЗКИ ДОКУМЕНТІВ

- Картка о/с + Атестат: `NEW/2025/03_Картки.../1. Картки...xlsx` → аркуш "Меглей В.І"
- Накладна (вимога): `C:\Rechova\скріншоти\накладна_стор1.png`
- Зведена відомість: `NEW/2025/05_Зведені_відомості/1 рота повернення на склад.docx`

---

## ЗАЛЕЖНОСТІ PYTHON
```
flask           — веб-фреймворк
pywebview       — десктоп оболонка
openpyxl        — Excel (.xlsx) читання/запис
xlrd            — Excel (.xls)
python-docx     — Word (.docx)
pdfplumber      — PDF читання
bcrypt          — хешування паролів
Bootstrap 5.3 + Bootstrap Icons — UI (локально, офлайн)
```

---

## ЖУРНАЛ РОЗРОБКИ

### 2026-03-11
- Створено структуру проекту, погоджено технічний стек
- ✅ Ядро: БД (34 таблиці), авторизація, дашборд, backup, plugin system, hook system
- ✅ Модуль Особового складу (повний CRUD)
- ✅ Модуль Налаштувань (реквізити, підрозділи, групи, словник, типи документів)

### 2026-03-12
- ✅ Модуль Складу: залишки, прихід (серійні номери, категорії I/II/III, скан документа)
- ✅ Інвентаризація: форма, перегляд, друк A4, Excel-експорт
- ✅ Накладні (повний модуль): CRUD, конструктор підписантів, попередній перегляд
- ✅ Статус draft (чернетка) — номер присвоюється окремо
- ✅ Нумерація: 2026/1/РС (без ведучих нулів)
- ✅ Автологіка підписантів залежно від напрямку (видача/повернення/переміщення)
- ✅ Поля sender_unit_id / sender_personnel_id для return/transfer
- ✅ Налаштування: начальник складу (warehouse_chief_name/rank)
- ✅ docs/PLUGIN_API.md — документація для розробників аддонів
- Наступний крок: Крок 2 — Шаблони документів
