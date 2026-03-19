"""
core/db.py — підключення до SQLite та створення всіх таблиць
Author: White
"""
import sqlite3
from datetime import datetime
from pathlib import Path


_db_path: str = ""


def set_db_path(path: str) -> None:
    global _db_path
    _db_path = path


def get_db_path() -> str:
    return _db_path


def get_connection() -> sqlite3.Connection:
    if not _db_path:
        raise RuntimeError("Шлях до бази даних не встановлено")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    """Створити всі таблиці якщо вони не існують."""
    conn = get_connection()
    cur = conn.cursor()

    # ══════════════════════════════════════════
    #  ДОВІДНИКИ / СИСТЕМНІ ТАБЛИЦІ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            permissions TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role_id INTEGER NOT NULL REFERENCES roles(id),
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER REFERENCES users(id),
            username   TEXT NOT NULL DEFAULT '',
            category   TEXT NOT NULL DEFAULT 'bug',
            priority   TEXT NOT NULL DEFAULT 'normal',
            title      TEXT NOT NULL,
            body       TEXT NOT NULL DEFAULT '',
            page_url   TEXT NOT NULL DEFAULT '',
            status     TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback_comments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            feedback_id INTEGER NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,
            parent_id   INTEGER REFERENCES feedback_comments(id) ON DELETE CASCADE,
            user_id     INTEGER REFERENCES users(id),
            username    TEXT NOT NULL DEFAULT '',
            body        TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            action TEXT NOT NULL,
            table_name TEXT NOT NULL,
            record_id INTEGER,
            old_data TEXT,
            new_data TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    # ══════════════════════════════════════════
    #  СТРУКТУРА ПІДРОЗДІЛІВ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS battalions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            battalion_id INTEGER NOT NULL REFERENCES battalions(id),
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS platoons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_id INTEGER NOT NULL REFERENCES units(id),
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL DEFAULT 'custom',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # type: active | no_group | dismissed | szch | deceased | missing | custom

    # ══════════════════════════════════════════
    #  МАТЕРІАЛЬНО-ВІДПОВІДАЛЬНІ ОСОБИ ПІДРОЗДІЛУ
    #  (командир роти, сержант МЗ, заступник і т.д.)
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS unit_responsible (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_id INTEGER NOT NULL REFERENCES units(id),
            role_name TEXT NOT NULL,
            rank TEXT,
            full_name TEXT NOT NULL,
            personnel_id INTEGER REFERENCES personnel(id),
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # role_name: 'commander' | 'deputy_commander' | 'supply_sergeant' | 'other'
    # Можна посилатись на картку о/с (personnel_id) або вводити вручну

    # ══════════════════════════════════════════
    #  ОСОБОВИЙ СКЛАД
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS personnel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- Основні дані
            last_name TEXT NOT NULL,
            first_name TEXT NOT NULL,
            middle_name TEXT,
            rank TEXT,
            position TEXT,
            category TEXT NOT NULL DEFAULT 'soldier',
            -- Розміщення
            battalion_id INTEGER REFERENCES battalions(id),
            unit_id INTEGER REFERENCES units(id),
            platoon_id INTEGER REFERENCES platoons(id),
            group_id INTEGER REFERENCES groups(id),
            -- Ідентифікатори
            ipn TEXT UNIQUE,
            card_number TEXT,
            phone TEXT,
            photo_path TEXT,
            -- Розміри
            size_head TEXT,
            size_height TEXT,
            size_underwear TEXT,
            size_suit TEXT,
            size_jacket TEXT,
            size_pants TEXT,
            size_shoes TEXT,
            size_vest  TEXT,
            -- Зарахування / вибуття
            enroll_date TEXT,
            enroll_order TEXT,
            enroll_order_file TEXT,
            dismiss_date TEXT,
            dismiss_order TEXT,
            dismiss_order_file TEXT,
            -- Стан
            is_active INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT,
            archive_reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # category: officer | soldier  (soldier = сержанти і солдати)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS personnel_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            personnel_id INTEGER NOT NULL REFERENCES personnel(id),
            block_data TEXT NOT NULL,
            archived_at TEXT NOT NULL,
            reason TEXT
        )
    """)

    # ══════════════════════════════════════════
    #  СЛОВНИК НОРМ ВИДАЧІ (стандартні назви)
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS norm_dict_groups (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active  INTEGER NOT NULL DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS norm_dictionary (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id         INTEGER NOT NULL REFERENCES norm_dict_groups(id),
            name             TEXT NOT NULL UNIQUE,
            unit             TEXT NOT NULL DEFAULT 'шт',
            default_qty      REAL NOT NULL DEFAULT 1,
            default_wear_years REAL NOT NULL DEFAULT 0,
            note_refs        TEXT,
            sort_order       INTEGER NOT NULL DEFAULT 0,
            is_active        INTEGER NOT NULL DEFAULT 1
        )
    """)

    # ══════════════════════════════════════════
    #  СЛОВНИК МАЙНА
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS item_dictionary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            unit_of_measure TEXT NOT NULL DEFAULT 'шт',
            -- Ознаки
            is_inventory INTEGER NOT NULL DEFAULT 0,
            has_serial_number INTEGER NOT NULL DEFAULT 0,
            needs_passport INTEGER NOT NULL DEFAULT 0,
            needs_exploitation_act INTEGER NOT NULL DEFAULT 0,
            -- Класифікація
            season TEXT NOT NULL DEFAULT 'demi',
            gender TEXT NOT NULL DEFAULT 'unisex',
            -- Норми: офіцери
            officer_norm_qty REAL NOT NULL DEFAULT 1,
            officer_wear_period INTEGER NOT NULL DEFAULT 0,
            -- Норми: солдати/сержанти
            soldier_norm_qty REAL NOT NULL DEFAULT 1,
            soldier_wear_period INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # season: demi | winter | summer
    # wear_period: 0 = до зносу, >0 = кількість місяців

    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            short_name TEXT,
            is_system INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    # ══════════════════════════════════════════
    #  НУМЕРАЦІЯ ДОКУМЕНТІВ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS doc_sequences (
            doc_type TEXT NOT NULL,
            year     INTEGER NOT NULL,
            sequence INTEGER NOT NULL DEFAULT 1,
            suffix   TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (doc_type, year)
        )
    """)
    # doc_type: invoice | rv | attestat | registry | write_off | exploit_act
    # PRIMARY KEY (doc_type, year) — окремий лічильник для кожного типу і кожного року

    # ══════════════════════════════════════════
    #  СКЛАД — ПРИХІД
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS warehouse_income (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            document_number TEXT,
            document_type_id INTEGER REFERENCES document_types(id),
            supplier TEXT,
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            quantity REAL NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT 'I',
            nom_code TEXT,
            source_type TEXT NOT NULL DEFAULT 'income_doc',
            scan_path TEXT,
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # category: 'I' | 'II' | 'III'
    # source_type: 'income_doc' | 'narad' | 'attestat' | 'manual' | 'inventory'

    # ══════════════════════════════════════════
    #  СКЛАД — ІНВЕНТАРИЗАЦІЯ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS inventories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            commission_members TEXT,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # status: draft | done

    cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inventory_id INTEGER NOT NULL REFERENCES inventories(id) ON DELETE CASCADE,
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            item_name_snapshot TEXT NOT NULL,
            unit_of_measure TEXT NOT NULL DEFAULT 'шт',
            category TEXT NOT NULL DEFAULT 'I',
            price REAL NOT NULL DEFAULT 0,
            qty_expected REAL NOT NULL DEFAULT 0,
            qty_actual REAL NOT NULL DEFAULT 0
        )
    """)

    # ══════════════════════════════════════════
    #  НАКЛАДНІ (ВИМОГИ) — Додаток 25
    #
    #  Напрямок:
    #    issue   — видача зі складу (служба → о/с або рота)
    #    return  — повернення на службу (о/с або рота → служба)
    #    transfer— переміщення між підрозділами
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- Нумерація
            number TEXT NOT NULL UNIQUE,
            year INTEGER NOT NULL,
            sequence_num INTEGER NOT NULL,
            suffix TEXT,
            -- Тип і напрямок
            invoice_type TEXT NOT NULL DEFAULT 'invoice',
            direction TEXT NOT NULL DEFAULT 'issue',
            -- Одержувач (о/с або підрозділ)
            recipient_type TEXT NOT NULL DEFAULT 'personnel',
            recipient_personnel_id INTEGER REFERENCES personnel(id),
            recipient_unit_id INTEGER REFERENCES units(id),
            -- Підписанти (підставляються в документ)
            given_by_rank TEXT,
            given_by_name TEXT,
            received_by_rank TEXT,
            received_by_name TEXT,
            -- Начальник служби (автопідстановка з налаштувань, можна перевизначити)
            chief_rank TEXT,
            chief_name TEXT,
            chief_is_tvo INTEGER NOT NULL DEFAULT 0,
            -- Діловод
            clerk_rank TEXT,
            clerk_name TEXT,
            -- Підстава (підстава для видачі: наказ, норма, рапорт)
            base_document TEXT,
            -- Дати та терміни
            valid_until TEXT,
            issued_date TEXT,
            -- Майно
            pages_count INTEGER NOT NULL DEFAULT 1,
            total_sum REAL NOT NULL DEFAULT 0,
            -- Статус і скан
            status TEXT NOT NULL DEFAULT 'created',
            cancel_comment TEXT,
            scan_path TEXT,
            notes TEXT,
            -- Аудит
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # invoice_type: invoice | rv
    # direction: issue | return | transfer
    # status: created | issued | processed | cancelled

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            planned_qty REAL NOT NULL,
            actual_qty REAL,
            price REAL NOT NULL DEFAULT 0,
            category INTEGER NOT NULL DEFAULT 1,
            -- TODO: category тут INTEGER (1/2/3), тоді як warehouse_income.category TEXT ('I'/'II'/'III').
            -- Не критично поки немає JOIN між ними, але уніфікувати при наступній великій міграції.
            serial_numbers TEXT,
            notes TEXT
        )
    """)
    # serial_numbers: JSON-масив s/n якщо has_serial_number

    # ══════════════════════════════════════════
    #  РОЗДАВАЛЬНІ (ЗДАВАЛЬНІ) ВІДОМОСТІ — РВ
    #
    #  Структура: шапка РВ → список о/с → по кожному о/с: к-сть по кожній позиції
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS distribution_sheets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- Нумерація
            number TEXT NOT NULL UNIQUE,
            year INTEGER NOT NULL,
            sequence_num INTEGER NOT NULL,
            suffix TEXT,
            -- Напрямок: видача або здача
            direction TEXT NOT NULL DEFAULT 'issue',
            -- Шапка документа
            unit_id INTEGER REFERENCES units(id),
            service_name TEXT,
            supplier_name TEXT,
            doc_date TEXT,
            -- Підписанти
            given_by_rank TEXT,
            given_by_name TEXT,
            received_by_rank TEXT,
            received_by_name TEXT,
            chief_rank TEXT,
            chief_name TEXT,
            chief_is_tvo INTEGER NOT NULL DEFAULT 0,
            clerk_rank TEXT,
            clerk_name TEXT,
            -- Підстава
            base_document TEXT,
            -- Підсумки
            total_sum REAL NOT NULL DEFAULT 0,
            -- Статус
            status TEXT NOT NULL DEFAULT 'draft',
            scan_path TEXT,
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # direction: issue | return
    # status: draft | signed | processed

    cur.execute("""
        CREATE TABLE IF NOT EXISTS distribution_sheet_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheet_id INTEGER NOT NULL REFERENCES distribution_sheets(id) ON DELETE CASCADE,
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            unit_of_measure TEXT,
            price REAL NOT NULL DEFAULT 0,
            category INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Перелік позицій майна у відомості (колонки таблиці)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS distribution_sheet_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheet_id INTEGER NOT NULL REFERENCES distribution_sheets(id) ON DELETE CASCADE,
            personnel_id INTEGER NOT NULL REFERENCES personnel(id),
            sort_order INTEGER NOT NULL DEFAULT 0,
            received INTEGER NOT NULL DEFAULT 0,
            received_date TEXT,
            signature_done INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Рядки відомості (о/с)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS distribution_sheet_quantities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheet_id INTEGER NOT NULL REFERENCES distribution_sheets(id) ON DELETE CASCADE,
            row_id INTEGER NOT NULL REFERENCES distribution_sheet_rows(id) ON DELETE CASCADE,
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            quantity REAL NOT NULL DEFAULT 0,
            serial_numbers TEXT
        )
    """)
    # Кількість по кожній клітинці (о/с × позиція)

    # ══════════════════════════════════════════
    #  МАЙНО НА КАРТЦІ ВІЙСЬКОВОСЛУЖБОВЦЯ (П-174)
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS personnel_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            personnel_id INTEGER NOT NULL REFERENCES personnel(id),
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            quantity REAL NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            category INTEGER NOT NULL DEFAULT 1,
            -- Звідки прийшло
            invoice_id INTEGER REFERENCES invoices(id),
            sheet_id INTEGER REFERENCES distribution_sheets(id),
            source_type TEXT NOT NULL DEFAULT 'invoice',
            -- Дати
            issue_date TEXT,
            wear_started_date TEXT,
            return_date TEXT,
            return_doc TEXT,
            -- Статус
            status TEXT NOT NULL DEFAULT 'active',
            write_off_doc_path TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # status: active | written_off | returned
    # source_type: invoice | sheet | attestat_import | manual

    # ══════════════════════════════════════════
    #  МАЙНО НА КАРТЦІ ПІДРОЗДІЛУ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS unit_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_id INTEGER NOT NULL REFERENCES units(id),
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            quantity REAL NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            category INTEGER NOT NULL DEFAULT 1,
            invoice_id INTEGER REFERENCES invoices(id),
            sheet_id INTEGER REFERENCES distribution_sheets(id),
            source_type TEXT NOT NULL DEFAULT 'invoice',
            issue_date TEXT,
            return_date TEXT,
            return_doc TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            write_off_doc_path TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    # ══════════════════════════════════════════
    #  АТЕСТАТИ
    #
    #  Атестат — окремий документ при переведенні/вибутті о/с.
    #  Містить перелік майна що закріплене за о/с на момент вибуття.
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attestats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- Нумерація
            number TEXT NOT NULL UNIQUE,
            year INTEGER NOT NULL,
            sequence_num INTEGER NOT NULL,
            suffix TEXT,
            -- Кому виданий
            personnel_id INTEGER NOT NULL REFERENCES personnel(id),
            -- Реквізити
            reg_number TEXT,
            doc_number TEXT,
            doc_date TEXT,
            -- Підписанти
            chief_rank TEXT,
            chief_name TEXT,
            chief_is_tvo INTEGER NOT NULL DEFAULT 0,
            clerk_rank TEXT,
            clerk_name TEXT,
            -- Відправник / одержувач (частини)
            sender_unit TEXT,
            receiver_unit TEXT,
            service_name TEXT,
            -- Рахунок
            account_number TEXT,
            -- Статус
            status TEXT NOT NULL DEFAULT 'draft',
            scan_path TEXT,
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # status: draft | signed | sent | received | closed

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attestat_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attestat_id INTEGER NOT NULL REFERENCES attestats(id) ON DELETE CASCADE,
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            quantity REAL NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            category INTEGER NOT NULL DEFAULT 1,
            -- Дата видачі і термін
            issue_date TEXT,
            wear_period INTEGER NOT NULL DEFAULT 0,
            -- Звідки взято (посилання на personnel_items)
            personnel_item_id INTEGER REFERENCES personnel_items(id),
            notes TEXT
        )
    """)

    # ══════════════════════════════════════════
    #  РЕЄСТРИ ДОКУМЕНТІВ ДО ФЕС
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS registries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- Нумерація
            number TEXT NOT NULL UNIQUE,
            year INTEGER NOT NULL,
            sequence_num INTEGER NOT NULL,
            -- Шапка
            reg_date TEXT NOT NULL,
            service_name TEXT,
            fes_unit TEXT,
            period_from TEXT,
            period_to TEXT,
            -- Підписанти
            given_by_rank TEXT,
            given_by_name TEXT,
            received_by_rank TEXT,
            received_by_name TEXT,
            -- Підсумок
            total_docs INTEGER NOT NULL DEFAULT 0,
            total_items_text TEXT,
            scan_path TEXT,
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS registry_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id INTEGER NOT NULL REFERENCES registries(id) ON DELETE CASCADE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            -- Документ
            doc_name TEXT NOT NULL,
            doc_number TEXT,
            doc_date TEXT,
            pages_count INTEGER NOT NULL DEFAULT 1,
            -- Посилання на реальний документ в БД (опціонально)
            ref_type TEXT,
            ref_id INTEGER,
            notes TEXT
        )
    """)
    # ref_type: invoice | sheet | attestat | write_off | exploit_act

    # ══════════════════════════════════════════
    #  АКТИ СПИСАННЯ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS write_offs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT NOT NULL UNIQUE,
            year INTEGER NOT NULL,
            sequence_num INTEGER NOT NULL,
            suffix TEXT,
            act_date TEXT NOT NULL,
            -- Підрозділ або о/с
            unit_id INTEGER REFERENCES units(id),
            personnel_id INTEGER REFERENCES personnel(id),
            -- Підписанти
            chief_rank TEXT,
            chief_name TEXT,
            chief_is_tvo INTEGER NOT NULL DEFAULT 0,
            commission_members TEXT,
            -- Підстава
            base_document TEXT,
            total_sum REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft',
            scan_path TEXT,
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS write_off_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            write_off_id INTEGER NOT NULL REFERENCES write_offs(id) ON DELETE CASCADE,
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            quantity REAL NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            category INTEGER NOT NULL DEFAULT 1,
            personnel_item_id INTEGER REFERENCES personnel_items(id),
            unit_item_id INTEGER REFERENCES unit_items(id),
            reason TEXT,
            notes TEXT
        )
    """)

    # ══════════════════════════════════════════
    #  АКТИ ВВЕДЕННЯ В ЕКСПЛУАТАЦІЮ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS exploitation_acts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT NOT NULL UNIQUE,
            year INTEGER NOT NULL,
            sequence_num INTEGER NOT NULL,
            act_date TEXT NOT NULL,
            -- Де знаходиться майно
            unit_id INTEGER REFERENCES units(id),
            personnel_id INTEGER REFERENCES personnel(id),
            -- Майно
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            unit_item_id INTEGER REFERENCES unit_items(id),
            personnel_item_id INTEGER REFERENCES personnel_items(id),
            quantity REAL NOT NULL DEFAULT 1,
            serial_number TEXT,
            -- Підписанти
            chief_rank TEXT,
            chief_name TEXT,
            chief_is_tvo INTEGER NOT NULL DEFAULT 0,
            commission_members TEXT,
            scan_path TEXT,
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    # ══════════════════════════════════════════
    #  СЕРІЙНІ НОМЕРИ ТА ПАСПОРТИ МАЙНА
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS item_serials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            serial_number TEXT NOT NULL,
            personnel_item_id INTEGER REFERENCES personnel_items(id),
            unit_item_id INTEGER REFERENCES unit_items(id),
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    # status: active | written_off | returned

    cur.execute("""
        CREATE TABLE IF NOT EXISTS item_passports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES item_dictionary(id),
            serial_id INTEGER REFERENCES item_serials(id),
            personnel_item_id INTEGER REFERENCES personnel_items(id),
            unit_item_id INTEGER REFERENCES unit_items(id),
            passport_number TEXT,
            issue_date TEXT,
            scan_path TEXT,
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    # ══════════════════════════════════════════
    #  СИСТЕМА ПЛАГІНІВ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS plugins (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT '1.0.0',
            description TEXT,
            author TEXT,
            is_active INTEGER NOT NULL DEFAULT 0,
            installed_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS plugin_settings (
            plugin_slug TEXT NOT NULL REFERENCES plugins(slug) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (plugin_slug, key)
        )
    """)

    # ══════════════════════════════════════════
    #  ЗВАННЯ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rank_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'enlisted',
            subcategory TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'army',
            sort_order INTEGER NOT NULL DEFAULT 0,
            insignia TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            is_custom INTEGER NOT NULL DEFAULT 0
        )
    """)
    # category: enlisted | nco | officer
    # subcategory: '' | junior | senior | higher  (для nco і officer)
    # mode: army | navy | nato
    # insignia: зірочки/смуги для відображення погону

    # ══════════════════════════════════════════
    #  ШАБЛОНИ ДОКУМЕНТІВ (конструктор)
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS doc_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            doc_type TEXT NOT NULL DEFAULT 'custom',
            template_group TEXT NOT NULL DEFAULT '',
            description TEXT,
            grid_data TEXT NOT NULL DEFAULT '{}',
            page_orientation TEXT NOT NULL DEFAULT 'portrait',
            page_size TEXT NOT NULL DEFAULT 'A4',
            margin_top REAL NOT NULL DEFAULT 20,
            margin_bottom REAL NOT NULL DEFAULT 20,
            margin_left REAL NOT NULL DEFAULT 30,
            margin_right REAL NOT NULL DEFAULT 10,
            font_family TEXT NOT NULL DEFAULT 'Times New Roman',
            base_font_size INTEGER NOT NULL DEFAULT 12,
            is_system INTEGER NOT NULL DEFAULT 0,
            default_for_type INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    # ══════════════════════════════════════════
    #  ПРИКРІПЛЕНІ ФАЙЛИ
    #  entity_type: personnel | unit | invoice | sheet | attestat | write_off | exploit_act
    #  source: 'user' — додано вручну, або slug плагіна — додано плагіном
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type   TEXT NOT NULL,
            entity_id     INTEGER NOT NULL,
            file_path     TEXT NOT NULL,
            original_name TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            source        TEXT NOT NULL DEFAULT 'user',
            created_by    INTEGER REFERENCES users(id),
            created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_attachments_entity ON attachments(entity_type, entity_id)")

    # ══════════════════════════════════════════
    #  НОТАТКИ (для плагінів і системи)
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id   INTEGER NOT NULL,
            text        TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT 'user',
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notes_entity ON notes(entity_type, entity_id)")

    # ══════════════════════════════════════════
    #  НОРМИ ВИДАЧІ МАЙНА
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS supply_norms (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT,
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS supply_norm_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            norm_id      INTEGER NOT NULL REFERENCES supply_norms(id) ON DELETE CASCADE,
            norm_dict_id INTEGER REFERENCES norm_dictionary(id),
            item_id      INTEGER REFERENCES item_dictionary(id),
            quantity     REAL    NOT NULL DEFAULT 1,
            wear_years   REAL    NOT NULL DEFAULT 0,
            category     TEXT    NOT NULL DEFAULT 'I',
            sort_order   INTEGER NOT NULL DEFAULT 0,
            notes        TEXT
        )
    """)

    # ══════════════════════════════════════════
    #  ЗНІМКИ ДЛЯ ГРАФІКІВ ДАШБОРДУ
    # ══════════════════════════════════════════

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chart_monthly_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            year         INTEGER NOT NULL,
            month        INTEGER NOT NULL,
            needs_count  INTEGER NOT NULL DEFAULT 0,
            snapshot_date TEXT NOT NULL DEFAULT (date('now','localtime')),
            UNIQUE(year, month)
        )
    """)

    conn.commit()
    _migrate(conn)
    _insert_defaults(conn)
    conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """
    Міграції схеми для існуючих БД.
    Використовуємо ALTER TABLE ADD COLUMN IF NOT EXISTS (через перехоплення помилки).
    """
    cur = conn.cursor()

    def add_column_if_missing(table: str, column: str, definition: str) -> None:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.commit()
        except Exception:
            pass  # колонка вже існує — ігноруємо

    # attachments: нові поля замість старих file_name/file_type
    add_column_if_missing("attachments", "original_name", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing("attachments", "description",   "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing("attachments", "source",        "TEXT NOT NULL DEFAULT 'user'")
    add_column_if_missing("attachments", "created_by",    "INTEGER REFERENCES users(id)")

    # warehouse_income: нові поля
    add_column_if_missing("warehouse_income", "nom_code",    "TEXT")
    add_column_if_missing("warehouse_income", "source_type", "TEXT NOT NULL DEFAULT 'income_doc'")

    # item_serials: прив'язка до запису приходу
    add_column_if_missing("item_serials", "warehouse_income_id", "INTEGER REFERENCES warehouse_income(id)")

    # invoices: JSON масив підписантів + скан приходу
    add_column_if_missing("invoices", "signatories", "TEXT")

    # warehouse_income: скан документу
    add_column_if_missing("warehouse_income", "scan_path",          "TEXT")
    add_column_if_missing("warehouse_income", "scan_original_name", "TEXT")

    # invoices: поля для напрямку переміщення/повернення
    add_column_if_missing("invoices", "sender_unit_id",      "INTEGER REFERENCES units(id)")
    add_column_if_missing("invoices", "sender_personnel_id", "INTEGER REFERENCES personnel(id)")

    # doc_templates: нові колонки для груп, замовчування, шрифту
    add_column_if_missing("doc_templates", "template_group",  "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing("doc_templates", "default_for_type","INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing("doc_templates", "font_family",     "TEXT NOT NULL DEFAULT 'Times New Roman'")
    add_column_if_missing("doc_templates", "base_font_size",  "INTEGER NOT NULL DEFAULT 12")

    # personnel: розмір бронежилета та файли наказів
    add_column_if_missing("personnel", "size_vest",           "TEXT")
    add_column_if_missing("personnel", "enroll_order_file",   "TEXT")
    add_column_if_missing("personnel", "dismiss_order_file",  "TEXT")

    # invoices: збережений HTML тіла документа (індивідуальне для цієї накладної)
    add_column_if_missing("invoices", "body_html", "TEXT")
    # distribution_sheets: збережений HTML тіла документа
    add_column_if_missing("distribution_sheets", "body_html", "TEXT")

    # ── Зовнішні документи (is_external) ────────────────────────────
    # invoices: зовнішній документ (не створений в системі)
    add_column_if_missing("invoices", "is_external",      "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing("invoices", "external_number",  "TEXT")
    add_column_if_missing("invoices", "scan_original_name", "TEXT")
    # distribution_sheets: те саме
    add_column_if_missing("distribution_sheets", "is_external",      "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing("distribution_sheets", "external_number",  "TEXT")
    add_column_if_missing("distribution_sheets", "scan_original_name", "TEXT")
    add_column_if_missing("distribution_sheets", "unit_text", "TEXT")

    # warehouse_income: чернетка (застарілий підхід — замінено на income_docs)
    add_column_if_missing("warehouse_income", "status", "TEXT NOT NULL DEFAULT 'confirmed'")

    # personnel_items: номер і дата документа-джерела (атестат, наказ тощо)
    add_column_if_missing("personnel_items", "source_doc_number", "TEXT")
    add_column_if_missing("personnel_items", "source_doc_date",   "TEXT")

    # ── Чернетки приходу (документ-заголовок + позиції) ─────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS income_docs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            status           TEXT NOT NULL DEFAULT 'draft',
            date             TEXT,
            document_number  TEXT,
            document_type_id INTEGER REFERENCES document_types(id),
            supplier         TEXT,
            source_type      TEXT NOT NULL DEFAULT 'income_doc',
            notes            TEXT,
            scan_path        TEXT,
            scan_original_name TEXT,
            created_by       INTEGER REFERENCES users(id),
            created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at       TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS income_doc_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id     INTEGER NOT NULL REFERENCES income_docs(id) ON DELETE CASCADE,
            item_id    INTEGER REFERENCES item_dictionary(id),
            quantity   REAL    NOT NULL DEFAULT 0,
            price      REAL    NOT NULL DEFAULT 0,
            category   TEXT    NOT NULL DEFAULT 'I',
            nom_code   TEXT,
            serial_numbers TEXT
        )
    """)

    # ── Фактична кількість по клітинках РВ ──────────────────────────
    # distribution_sheet_quantities: actual_qty (фактично отримано, може відрізнятись від quantity)
    add_column_if_missing("distribution_sheet_quantities", "actual_qty", "REAL")

    # ── Статус received для накладних і РВ ──────────────────────────
    # (статус вже зберігається як TEXT, нові значення 'received' додаються без міграції схеми)

    # supply_norm_items: прив'язка до стандартної назви (norm_dictionary) замість item_dictionary
    add_column_if_missing("supply_norm_items", "norm_dict_id", "INTEGER REFERENCES norm_dictionary(id)")

    # supply_norm_items: зробити item_id nullable (тепер використовується norm_dict_id)
    # SQLite не підтримує ALTER COLUMN, тому перестворюємо таблицю якщо item_id ще NOT NULL
    try:
        cols_info = cur.execute("PRAGMA table_info(supply_norm_items)").fetchall()
        item_id_col = next((c for c in cols_info if c["name"] == "item_id"), None)
        if item_id_col and item_id_col["notnull"] == 1:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS supply_norm_items_new (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    norm_id    INTEGER NOT NULL REFERENCES supply_norms(id) ON DELETE CASCADE,
                    norm_dict_id INTEGER REFERENCES norm_dictionary(id),
                    item_id    INTEGER REFERENCES item_dictionary(id),
                    quantity   REAL    NOT NULL DEFAULT 1,
                    wear_years REAL    NOT NULL DEFAULT 0,
                    category   TEXT    NOT NULL DEFAULT 'I',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    notes      TEXT
                )
            """)
            cur.execute("""
                INSERT INTO supply_norm_items_new
                    (id, norm_id, norm_dict_id, item_id, quantity, wear_years, category, sort_order, notes)
                SELECT id, norm_id, norm_dict_id, item_id, quantity, wear_years, category, sort_order, notes
                FROM supply_norm_items
            """)
            cur.execute("DROP TABLE supply_norm_items")
            cur.execute("ALTER TABLE supply_norm_items_new RENAME TO supply_norm_items")
            conn.commit()
    except Exception:
        pass

    # norm_dictionary: одиниця виміру та дефолтні значення норми
    add_column_if_missing("norm_dictionary", "unit",               "TEXT NOT NULL DEFAULT 'шт'")
    add_column_if_missing("norm_dictionary", "default_qty",        "REAL NOT NULL DEFAULT 1")
    add_column_if_missing("norm_dictionary", "default_wear_years", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing("norm_dictionary", "note_refs",          "TEXT")

    # personnel: нові поля — призов та лічильник карток
    add_column_if_missing("personnel", "draft_date",  "TEXT")
    add_column_if_missing("personnel", "draft_by",    "TEXT")

    # doc_sequences: лічильник для card_number (personnel)
    # Реєструється через doc_type='personnel_card' — так само як invoice/rv

    # doc_sequences: міграція зі старої схеми (doc_type PRIMARY KEY) на нову (doc_type, year)
    # Якщо стара таблиця — перестворюємо зі збереженням даних
    try:
        cols = [r[1] for r in cur.execute("PRAGMA table_info(doc_sequences)").fetchall()]
        # У старій схемі PRIMARY KEY тільки doc_type — нема UNIQUE (doc_type, year)
        idx_info = cur.execute("PRAGMA index_list(doc_sequences)").fetchall()
        pk_cols = []
        for idx in idx_info:
            if idx["origin"] == "pk":
                pk_cols = [r[2] for r in cur.execute(f"PRAGMA index_info({idx['name']})").fetchall()]
        if pk_cols == ["doc_type"]:
            # Стара схема — мігруємо
            cur.execute("""
                CREATE TABLE IF NOT EXISTS doc_sequences_new (
                    doc_type TEXT NOT NULL,
                    year     INTEGER NOT NULL,
                    sequence INTEGER NOT NULL DEFAULT 1,
                    suffix   TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    PRIMARY KEY (doc_type, year)
                )
            """)
            cur.execute("""
                INSERT OR IGNORE INTO doc_sequences_new (doc_type, year, sequence, suffix, updated_at)
                SELECT doc_type, year, sequence, suffix, updated_at FROM doc_sequences
            """)
            cur.execute("DROP TABLE doc_sequences")
            cur.execute("ALTER TABLE doc_sequences_new RENAME TO doc_sequences")
            conn.commit()
    except Exception:
        pass

    # supply_norms: норми видачі майна
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supply_norms (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supply_norm_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                norm_id      INTEGER NOT NULL REFERENCES supply_norms(id) ON DELETE CASCADE,
                norm_dict_id INTEGER REFERENCES norm_dictionary(id),
                item_id      INTEGER REFERENCES item_dictionary(id),
                quantity     REAL    NOT NULL DEFAULT 1,
                wear_years   REAL    NOT NULL DEFAULT 0,
                category     TEXT    NOT NULL DEFAULT 'I',
                sort_order   INTEGER NOT NULL DEFAULT 0,
                notes        TEXT
            )
        """)
        conn.commit()
    except Exception:
        pass

    add_column_if_missing("personnel", "norm_id", "INTEGER REFERENCES supply_norms(id)")

    # norm_dictionary: нові таблиці для існуючих БД
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS norm_dict_groups (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_active  INTEGER NOT NULL DEFAULT 1
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS norm_dictionary (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id         INTEGER NOT NULL REFERENCES norm_dict_groups(id),
                name             TEXT NOT NULL UNIQUE,
                unit             TEXT NOT NULL DEFAULT 'шт',
                default_qty      REAL NOT NULL DEFAULT 1,
                default_wear_years REAL NOT NULL DEFAULT 0,
                note_refs        TEXT,
                sort_order       INTEGER NOT NULL DEFAULT 0,
                is_active        INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.commit()
    except Exception:
        pass

    add_column_if_missing("item_dictionary", "norm_dict_id",
                          "INTEGER REFERENCES norm_dictionary(id)")

    # norm_dictionary: виправлення даних (кашкет wear 1→2, структура груп)
    try:
        nd_count = cur.execute("SELECT COUNT(*) FROM norm_dictionary").fetchone()[0]
        if nd_count > 0:
            _update_norm_dict_data(cur)
            conn.commit()
    except Exception:
        pass

    # Норма №1 у supply_norms — створюємо якщо ще немає
    try:
        existing_norm1 = cur.execute(
            "SELECT COUNT(*) FROM supply_norms WHERE name='Норма №1'"
        ).fetchone()[0]
        if existing_norm1 == 0:
            nd_count = cur.execute("SELECT COUNT(*) FROM norm_dictionary").fetchone()[0]
            if nd_count > 0:
                _insert_norm1_supply_norm(cur)
                conn.commit()
    except Exception:
        pass

    # ══════════════════════════════════════════
    #  КОМПЛЕКТИ: складові норм
    # ══════════════════════════════════════════
    # norm_dict_components: розкладка комплекту на складові
    # parent_id → позиція словника норм що є комплектом
    # child_id  → складова (теж позиція словника норм)
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS norm_dict_components (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id  INTEGER NOT NULL REFERENCES norm_dictionary(id) ON DELETE CASCADE,
                child_id   INTEGER NOT NULL REFERENCES norm_dictionary(id) ON DELETE CASCADE,
                qty        REAL    NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                UNIQUE(parent_id, child_id)
            )
        """)
        conn.commit()
    except Exception:
        pass

    # norm_dictionary: позначка що це комплект (has_components)
    add_column_if_missing("norm_dictionary", "has_components", "INTEGER NOT NULL DEFAULT 0")

    # invoice_items: посилання на батьківський рядок комплекту
    # kit_norm_id — якщо видано як складова комплекту, тут id norm_dictionary батька
    add_column_if_missing("invoice_items", "kit_norm_id",
                          "INTEGER REFERENCES norm_dictionary(id)")

    # personnel_items: аналогічно — для відображення в картці
    add_column_if_missing("personnel_items", "kit_norm_id",
                          "INTEGER REFERENCES norm_dictionary(id)")

    # distribution_sheet_quantities: аналогічно для РВ
    add_column_if_missing("distribution_sheet_quantities", "kit_norm_id",
                          "INTEGER REFERENCES norm_dictionary(id)")

    # feedback: додаємо поля "хто вирішив" і "нотатка вирішення"
    add_column_if_missing("feedback", "resolved_by",   "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing("feedback", "resolved_at",   "TEXT")
    add_column_if_missing("feedback", "resolve_note",  "TEXT NOT NULL DEFAULT ''")

    # feedback_comments: таблиця коментарів до записів багтрекера
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback_comments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,
                parent_id   INTEGER REFERENCES feedback_comments(id) ON DELETE CASCADE,
                user_id     INTEGER REFERENCES users(id),
                username    TEXT NOT NULL DEFAULT '',
                body        TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()
    except Exception:
        pass
    # feedback_comments: parent_id для гілок (якщо таблиця вже існує без цієї колонки)
    add_column_if_missing("feedback_comments", "parent_id",
                          "INTEGER REFERENCES feedback_comments(id) ON DELETE CASCADE")

    # Теми: поле theme в users + системна тема за замовченням в settings
    add_column_if_missing("users", "theme", "TEXT NOT NULL DEFAULT 'default'")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_theme', 'default')")
    conn.commit()

    # Атестат: списки підстав і отримувачів (JSON-масиви)
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('attestat_basis_list', '[]')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('attestat_recipient_list', '[]')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('attestat_service', 'РСТ')")
    conn.commit()

    # Атестат: збереження реєстраційних полів по особі (серверне, доступно всім)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attestat_data (
            personnel_id INTEGER PRIMARY KEY REFERENCES personnel(id) ON DELETE CASCADE,
            reg_number     TEXT NOT NULL DEFAULT '',
            reg_sheet      TEXT NOT NULL DEFAULT '1',
            reg_doc_number TEXT NOT NULL DEFAULT '',
            reg_doc_date   TEXT NOT NULL DEFAULT '',
            reg_basis      TEXT NOT NULL DEFAULT '',
            reg_service    TEXT NOT NULL DEFAULT '',
            reg_recipient  TEXT NOT NULL DEFAULT '',
            reg_font_size  TEXT NOT NULL DEFAULT '12',
            updated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    add_column_if_missing("attestat_data", "reg_font_size", "TEXT NOT NULL DEFAULT '12'")
    conn.commit()

    # income_docs: підтримка attestat_import — прив'язка до особи
    add_column_if_missing("income_docs", "person_id", "INTEGER REFERENCES personnel(id)")

    # personnel_items: прив'язка до income_doc (для attestat_import)
    add_column_if_missing("personnel_items", "income_doc_id", "INTEGER REFERENCES income_docs(id)")

    # rank_presets: нова таблиця через CREATE IF NOT EXISTS — міграція не потрібна,
    # але якщо таблиця вже існує без нових колонок — додаємо
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rank_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                short_name TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'enlisted',
                subcategory TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT 'army',
                sort_order INTEGER NOT NULL DEFAULT 0,
                insignia TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                is_custom INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════
    #  ЛОГІКА НОСКИ: service_type, цикли, норми, борги
    # ══════════════════════════════════════════════════════════════

    # 1. Тип служби на особі: mobilized | contract
    add_column_if_missing("personnel", "service_type", "TEXT NOT NULL DEFAULT 'mobilized'")

    # 2. supply_norm_item_wear — таблиця строків носки по категоріях
    #    (використовувалась в коді але не існувала в БД)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supply_norm_item_wear (
            norm_item_id INTEGER NOT NULL REFERENCES supply_norm_items(id) ON DELETE CASCADE,
            personnel_cat INTEGER NOT NULL,
            wear_months   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (norm_item_id, personnel_cat)
        )
    """)

    # 2b. Кількість по категорії персоналу (NULL = використовувати supply_norm_items.quantity)
    add_column_if_missing("supply_norm_item_wear", "qty", "REAL")

    # 3. Тип служби для якого призначена норма: all | mobilized | contract
    add_column_if_missing("supply_norms", "service_type", "TEXT NOT NULL DEFAULT 'all'")

    # 4. personnel_norm_history — історія норм для контрактників
    cur.execute("""
        CREATE TABLE IF NOT EXISTS personnel_norm_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            personnel_id  INTEGER NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
            norm_id       INTEGER NOT NULL REFERENCES supply_norms(id),
            personnel_cat INTEGER NOT NULL DEFAULT 5,
            date_from     TEXT NOT NULL,
            date_to       TEXT,
            notes         TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_norm_history_person
        ON personnel_norm_history(personnel_id)
    """)

    # 5. personnel_items — нові поля для циклів і розрахунків
    #    cycle_start_date — дата першої видачі в поточному циклі
    #    cycle_closed     — 1 якщо норму закрито в повному обсязі
    #    norm_qty_at_issue — кількість по нормі на момент видачі (знімок)
    #    wear_months_at_issue — строк носки на момент видачі (знімок)
    #    next_issue_date  — розрахункова дата наступного отримання
    add_column_if_missing("personnel_items", "cycle_start_date",    "TEXT")
    add_column_if_missing("personnel_items", "cycle_closed",        "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing("personnel_items", "norm_qty_at_issue",   "REAL NOT NULL DEFAULT 0")
    add_column_if_missing("personnel_items", "wear_months_at_issue","INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing("personnel_items", "next_issue_date",     "TEXT")

    # 6. Налаштування за замовченням для нової логіки
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_service_type', 'mobilized')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('wear_warning_days', '30')")

    conn.commit()


def _update_norm_dict_data(cur) -> None:
    """
    Виправити дані словника норм видачі відповідно до реального тексту
    наказу МОУ №306 від 10.06.2019 (змінює №232).
    Оновлюємо по назві предмета.
    """
    corrections = [
        # (name, unit, default_qty, default_wear_years, note_refs)
        ("Кашкет польовий (кепі бойове)",          "штука",    2, 2, "1; 2; 5; 8; 11"),
        ("Костюм вітровологозахисний демісезонний", "комплект", 1, 2, None),
        ("Куртка вітровологозахисна зимова (куртка утеплена польова)", "комплект", 1, 2, "1; 2; 5; 8; 10; 11"),
        ("Штани вітровологозахисні зимові (штани утеплені польові)",   "комплект", 1, 2, "1; 2; 5; 8; 10; 11"),
        ("Труси та топ жіночий",                   "штука",    2, 1, None),
        ("Білизна для холодної погоди (сорочка зимова та кальсони зимові)", "комплект", 2, 2, "1; 2; 7; 11"),
        ("Черевики з високими берцями літні або черевики літні", "пара", 1, 2, "1; 6; 11"),
        ("Модульна всесезонна спальна система (мішок спальний)", "комплект", 1, 2, "3; 11"),
    ]
    for name, unit, qty, wear, notes in corrections:
        cur.execute(
            """UPDATE norm_dictionary
               SET unit=?, default_qty=?, default_wear_years=?, note_refs=?
               WHERE name=?""",
            (unit, qty, wear, notes, name)
        )
    # Також виправляємо старі назви що змінились
    old_to_new = [
        ("Куртка вітровологозахисна зимова",  "Куртка вітровологозахисна зимова (куртка утеплена польова)"),
        ("Штани вітровологозахисні зимові",   "Штани вітровологозахисні зимові (штани утеплені польові)"),
        ("Черевики з високими берцями літні",  "Черевики з високими берцями літні або черевики літні"),
        ("Білизна для холодної погоди",        "Білизна для холодної погоди (сорочка зимова та кальсони зимові)"),
        ("Модульна всесезонна спальна система","Модульна всесезонна спальна система (мішок спальний)"),
    ]
    for old_name, new_name in old_to_new:
        cur.execute(
            "UPDATE norm_dictionary SET name=? WHERE name=? AND NOT EXISTS (SELECT 1 FROM norm_dictionary WHERE name=?)",
            (new_name, old_name, new_name)
        )

    # Виправляємо групи: рукавички (демі/зимові) мають бути в Обмундируванні,
    # не в окремій групі "Рукавички та захист рук".
    # Чохли шолому/жилета — в Спорядженні, не в ЗІЗ.
    # Польовий побут → Спорядження та екіпірування
    group_fixes = [
        # (item_names, target_group_name)
        (["Рукавички демісезонні", "Рукавички зимові"], "Обмундирування"),
        (["Рукавички тактичні", "Беруші спеціальні індивідуальні", "Ремінь брючний",
          "Сумка транспортна індивідуальна", "Сумка адміністративна",
          "Сумка-підсумок для предметів особистої гігієни",
          "Модульна всесезонна спальна система (мішок спальний)",
          "Килим спальний польовий ізоляційний", "Сидіння польове ізоляційне",
          "Чохол для фляги індивідуальної польової", "Казанок індивідуальний польовий",
          "Фляга індивідуальна польова", "Столовий набір індивідуальний польовий",
          "Кухоль індивідуальний складаний", "Ніж індивідуальний табірний",
          "Ліхтарик польовий індивідуальний",
          "Чохол для шолома балістичного",
          "Чохол для захисного балістичного модульного жилета"], "Спорядження та екіпірування"),
        (["Окуляри захисні балістичні", "Шолом бойовий балістичний",
          "Бронежилет модульний"], "Засоби індивідуального захисту"),
    ]
    for item_names, target_group in group_fixes:
        grp = cur.execute(
            "SELECT id FROM norm_dict_groups WHERE name=?", (target_group,)
        ).fetchone()
        if grp:
            for item_name in item_names:
                cur.execute(
                    "UPDATE norm_dictionary SET group_id=? WHERE name=?",
                    (grp["id"], item_name)
                )
    # Видаляємо зайві порожні групи (Рукавички та захист рук, Польовий побут)
    for old_group in ("Рукавички та захист рук", "Польовий побут"):
        grp = cur.execute(
            "SELECT id FROM norm_dict_groups WHERE name=?", (old_group,)
        ).fetchone()
        if grp:
            cnt = cur.execute(
                "SELECT COUNT(*) FROM norm_dictionary WHERE group_id=?", (grp["id"],)
            ).fetchone()[0]
            if cnt == 0:
                cur.execute("DELETE FROM norm_dict_groups WHERE id=?", (grp["id"],))

    # Оновити роль "Діловод" — повні права + settings/plugins на читання
    import json as _json
    row = cur.execute("SELECT id, permissions FROM roles WHERE name='Діловод'").fetchone()
    if row:
        try:
            perms = _json.loads(row["permissions"])
        except Exception:
            perms = {}
        changed = False
        for key in ("settings", "plugins"):
            if key not in perms:
                perms[key] = "read"
                changed = True
        if changed:
            cur.execute("UPDATE roles SET permissions=? WHERE id=?",
                        (_json.dumps(perms, ensure_ascii=False), row["id"]))
            conn.commit()


def _insert_defaults(conn: sqlite3.Connection) -> None:
    """Вставити початкові дані якщо їх ще немає."""
    cur = conn.cursor()

    # Системні ролі
    roles = [
        ("Адміністратор",    '{"all": true}'),
        ("Діловод",          '{"personnel": true, "warehouse": true, "invoices": true, "reports": true, "settings": "read", "plugins": "read"}'),
        ("Начальник складу", '{"warehouse": "read", "invoices": "read"}'),
    ]
    for name, perms in roles:
        cur.execute(
            "INSERT OR IGNORE INTO roles (name, permissions) VALUES (?, ?)",
            (name, perms)
        )

    # Системні групи
    groups = [
        ("Картотека",         "active"),
        ("БЕЗ ГРУПИ",         "no_group"),
        ("Вибувші",           "dismissed"),
        ("СЗЧ",               "szch"),
        ("Загиблі",           "deceased"),
        ("Безвісті зниклі",   "missing"),
    ]
    for name, gtype in groups:
        cur.execute(
            "INSERT OR IGNORE INTO groups (name, type) VALUES (?, ?)",
            (name, gtype)
        )

    # Системні типи документів
    doc_types = [
        ("Накладна (вимога)",            "Накладна",   1),
        ("Роздавальна відомість",         "РВ",         1),
        ("Здавальна відомість",           "ЗдВ",        1),
        ("Акт списання",                  "Акт спис.",  1),
        ("Акт введення в експлуатацію",   "Акт вв.е.",  1),
        ("Речовий атестат",               "Атестат",    1),
        ("Прихідна накладна",             "Прихід",     1),
        ("Реєстр документів",             "Реєстр",     1),
    ]
    for name, short, is_sys in doc_types:
        cur.execute(
            "INSERT OR IGNORE INTO document_types (name, short_name, is_system) VALUES (?, ?, ?)",
            (name, short, is_sys)
        )

    # Початкові налаштування
    year = datetime.now().year
    defaults = [
        ("company_name",        ""),
        ("service_name",        ""),
        ("chief_name",          ""),
        ("chief_rank",          ""),
        ("chief_is_tvo",        "0"),
        ("chief_tvo_name",      ""),
        ("chief_tvo_rank",      ""),
        ("clerk_name",          ""),
        ("clerk_rank",          ""),
        ("warehouse_chief_name", ""),
        ("warehouse_chief_rank", ""),
        ("invoice_year",        str(year)),
        ("invoice_suffix",      "РС"),
        ("invoice_valid_days",  "10"),
        ("invoice_sequence",    "1"),
        ("rv_suffix",           "РВ"),
        ("rv_sequence",         "1"),
        ("backup_reminder_days","3"),
    ]
    for key, val in defaults:
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, val)
        )

    # Звання (тільки якщо таблиця порожня)
    existing_ranks = cur.execute("SELECT COUNT(*) FROM rank_presets").fetchone()[0]
    if existing_ranks == 0:
        # (name, short_name, category, subcategory, mode, sort_order, insignia)
        rank_data = [
            # ─── АРМІЙСЬКІ ───────────────────────────────────────────────────────
            # Рядовий склад
            ("Солдат",                  "солдат",       "enlisted", "",       "army",  1,  "☐"),
            ("Старший солдат",          "ст. солдат",   "enlisted", "",       "army",  2,  "☐▪"),
            # Молодший сержантський склад
            ("Молодший сержант",        "мол. серж.",   "nco", "junior",      "army",  3,  "▪"),
            ("Сержант",                 "серж.",        "nco", "junior",      "army",  4,  "▪▪"),
            ("Старший сержант",         "ст. серж.",    "nco", "junior",      "army",  5,  "▪▪▪"),
            # Старший сержантський склад
            ("Майстер-сержант",         "м-серж.",      "nco", "senior",      "army",  6,  "★▪"),
            ("Штаб-сержант",            "шт.-серж.",    "nco", "senior",      "army",  7,  "★▪▪"),
            ("Майстер штабу",           "м. штабу",     "nco", "senior",      "army",  8,  "★▪▪▪"),
            # Вищий сержантський склад
            ("Головний сержант",        "гол. серж.",   "nco", "higher",      "army",  9,  "★★"),
            ("Головний сержант ЗСУ",    "гол. серж. ЗСУ","nco","higher",     "army", 10,  "★★★"),
            # Молодші офіцери
            ("Молодший лейтенант",      "мол. лейт.",   "officer", "junior",  "army", 11,  "★"),
            ("Лейтенант",               "лейт.",        "officer", "junior",  "army", 12,  "★★"),
            ("Старший лейтенант",       "ст. лейт.",    "officer", "junior",  "army", 13,  "★★★"),
            ("Капітан",                 "кап.",         "officer", "junior",  "army", 14,  "★★★★"),
            # Старші офіцери
            ("Майор",                   "майор",        "officer", "senior",  "army", 15,  "☆"),
            ("Підполковник",            "пдп-к",        "officer", "senior",  "army", 16,  "☆☆"),
            ("Полковник",               "полк.",        "officer", "senior",  "army", 17,  "☆☆☆"),
            # Вищі офіцери
            ("Генерал-майор",           "ген.-майор",   "officer", "higher",  "army", 18,  "⬡"),
            ("Генерал-лейтенант",       "ген.-лейт.",   "officer", "higher",  "army", 19,  "⬡⬡"),
            ("Генерал",                 "генерал",      "officer", "higher",  "army", 20,  "⬡⬡⬡"),
            ("Генерал армії України",   "ген. армії",   "officer", "higher",  "army", 21,  "⬡⬡⬡⬡"),
            # ─── КОРАБЕЛЬНІ ───────────────────────────────────────────────────────
            ("Матрос",                  "матрос",       "enlisted", "",       "navy",  1,  "☐"),
            ("Старший матрос",          "ст. матрос",   "enlisted", "",       "navy",  2,  "☐▪"),
            ("Старшина 2 статті",       "старш. 2 ст.", "nco", "junior",      "navy",  3,  "▪"),
            ("Старшина 1 статті",       "старш. 1 ст.", "nco", "junior",      "navy",  4,  "▪▪"),
            ("Головний старшина",       "гол. старш.",  "nco", "junior",      "navy",  5,  "▪▪▪"),
            ("Мічман",                  "мічман",       "nco", "senior",      "navy",  6,  "★▪"),
            ("Старший мічман",          "ст. мічман",   "nco", "senior",      "navy",  7,  "★▪▪"),
            ("Молодший лейтенант",      "мол. лейт.",   "officer", "junior",  "navy",  8,  "★"),
            ("Лейтенант",               "лейт.",        "officer", "junior",  "navy",  9,  "★★"),
            ("Старший лейтенант",       "ст. лейт.",    "officer", "junior",  "navy", 10,  "★★★"),
            ("Капітан-лейтенант",       "кап.-лейт.",   "officer", "junior",  "navy", 11,  "★★★★"),
            ("Капітан 3 рангу",         "кап. 3 р.",    "officer", "senior",  "navy", 12,  "☆"),
            ("Капітан 2 рангу",         "кап. 2 р.",    "officer", "senior",  "navy", 13,  "☆☆"),
            ("Капітан 1 рангу",         "кап. 1 р.",    "officer", "senior",  "navy", 14,  "☆☆☆"),
            ("Контр-адмірал",           "контр-адм.",   "officer", "higher",  "navy", 15,  "⬡"),
            ("Віце-адмірал",            "віце-адм.",    "officer", "higher",  "navy", 16,  "⬡⬡"),
            ("Адмірал",                 "адмірал",      "officer", "higher",  "navy", 17,  "⬡⬡⬡"),
            # ─── НАТО ─────────────────────────────────────────────────────────────
            ("OR-1",  "OR-1",  "enlisted", "",       "nato",  1,  "OR-1"),
            ("OR-2",  "OR-2",  "enlisted", "",       "nato",  2,  "OR-2"),
            ("OR-3",  "OR-3",  "enlisted", "",       "nato",  3,  "OR-3"),
            ("OR-4",  "OR-4",  "nco", "junior",      "nato",  4,  "OR-4"),
            ("OR-5",  "OR-5",  "nco", "junior",      "nato",  5,  "OR-5"),
            ("OR-6",  "OR-6",  "nco", "junior",      "nato",  6,  "OR-6"),
            ("OR-7",  "OR-7",  "nco", "senior",      "nato",  7,  "OR-7"),
            ("OR-8",  "OR-8",  "nco", "senior",      "nato",  8,  "OR-8"),
            ("OR-9",  "OR-9",  "nco", "higher",      "nato",  9,  "OR-9"),
            ("OF-1",  "OF-1",  "officer", "junior",  "nato", 10,  "OF-1"),
            ("OF-2",  "OF-2",  "officer", "junior",  "nato", 11,  "OF-2"),
            ("OF-3",  "OF-3",  "officer", "senior",  "nato", 12,  "OF-3"),
            ("OF-4",  "OF-4",  "officer", "senior",  "nato", 13,  "OF-4"),
            ("OF-5",  "OF-5",  "officer", "senior",  "nato", 14,  "OF-5"),
            ("OF-6",  "OF-6",  "officer", "higher",  "nato", 15,  "OF-6"),
            ("OF-7",  "OF-7",  "officer", "higher",  "nato", 16,  "OF-7"),
            ("OF-8",  "OF-8",  "officer", "higher",  "nato", 17,  "OF-8"),
            ("OF-9",  "OF-9",  "officer", "higher",  "nato", 18,  "OF-9"),
            ("OF-10", "OF-10", "officer", "higher",  "nato", 19,  "OF-10"),
        ]
        for name, short, cat, subcat, mode, order, insignia in rank_data:
            cur.execute(
                """INSERT INTO rank_presets
                   (name, short_name, category, subcategory, mode, sort_order, insignia)
                   VALUES (?,?,?,?,?,?,?)""",
                (name, short, cat, subcat, mode, order, insignia)
            )

    # Нумерація документів по типах
    doc_seqs = [
        ("invoice",     year, 1, "РС"),
        ("rv",          year, 1, "РВ"),
        ("attestat",    year, 1, "АТ"),
        ("registry",    year, 1, "РЄ"),
        ("write_off",   year, 1, "АС"),
        ("exploit_act", year, 1, "АВЕ"),
    ]
    for dt, y, seq, suf in doc_seqs:
        cur.execute(
            "INSERT OR IGNORE INTO doc_sequences (doc_type, year, sequence, suffix) VALUES (?,?,?,?)",
            (dt, y, seq, suf)
        )

    # Системні шаблони документів
    existing_tpls = cur.execute("SELECT COUNT(*) FROM doc_templates WHERE is_system=1").fetchone()[0]
    if existing_tpls == 0:
        _insert_system_templates(cur)

    # Словник норм — підгрупи та позиції (вставляємо один раз)
    existing_groups = cur.execute("SELECT COUNT(*) FROM norm_dict_groups").fetchone()[0]
    if existing_groups == 0:
        _insert_norm_dictionary(cur)

    # Норма №1 у supply_norms (вставляємо один раз якщо ще немає)
    existing_norm1 = cur.execute(
        "SELECT COUNT(*) FROM supply_norms WHERE name='Норма №1'"
    ).fetchone()[0]
    if existing_norm1 == 0:
        _insert_norm1_supply_norm(cur)

    conn.commit()


def _insert_norm1_supply_norm(cur) -> None:
    """
    Створити норму видачі "Норма №1" і заповнити її позиціями зі словника норм.
    Дані: наказ МОУ №232 зі змінами №306 від 10.06.2019.
    Категорія 5, строки носіння в роках (0 = безстрокове).
    """
    cur.execute(
        """INSERT INTO supply_norms (name, description, is_active)
           VALUES ('Норма №1',
                   'Норми забезпечення речовим майном військовослужбовців ЗСУ. Наказ МОУ №232 від 29.04.2016 зі змінами №306 від 10.06.2019. Категорія 5.',
                   1)"""
    )
    norm_id = cur.lastrowid

    # (nd_name, quantity, wear_years, category)
    # Порядок відповідає таблиці наказу (позиції 1-50)
    items = [
        # ГОЛОВНІ УБОРИ (1-4)
        ("Кашкет польовий (кепі бойове)",                                        2, 2, "I"),
        ("Панама літня польова",                                                  1, 2, "I"),
        ("Шапка-феска (шапка-підшоломник)",                                       1, 1, "I"),
        ("Шапка зимова",                                                          1, 2, "I"),
        # ОБМУНДИРУВАННЯ (5-16)
        ("Костюм літній польовий",                                                2, 2, "I"),
        ("Штани костюма літнього польового",                                      1, 1, "I"),
        ("Костюм-утеплювач",                                                      1, 2, "I"),
        ("Костюм вітровологозахисний демісезонний",                               1, 2, "I"),
        ("Куртка вітровологозахисна зимова (куртка утеплена польова)",            1, 2, "I"),
        ("Штани вітровологозахисні зимові (штани утеплені польові)",              1, 2, "I"),
        ("Сорочка-поло",                                                          2, 2, "I"),
        ("Сорочка бойова",                                                        2, 2, "I"),
        ("Рукавички демісезонні",                                                 1, 1, "I"),
        ("Рукавички зимові",                                                      1, 1, "I"),
        ("Шарф-труба літній",                                                     1, 2, "I"),
        ("Шарф-труба зимовий",                                                    1, 2, "I"),
        # БІЛИЗНА ТА ШКАРПЕТКИ (17-23)
        ("Фуфайка (з короткими рукавами)",                                        2, 1, "I"),
        ("Труси чоловічі",                                                        2, 1, "I"),
        ("Труси та топ жіночий",                                                  2, 1, "I"),
        ("Білизна натільна демісезонна",                                          2, 1, "I"),
        ("Білизна для холодної погоди (сорочка зимова та кальсони зимові)",       2, 2, "I"),
        ("Шкарпетки літні (трекінгові)",                                          6, 1, "I"),
        ("Шкарпетки зимові (трекінгові)",                                         3, 1, "I"),
        # ВЗУТТЯ (24-29)
        ("Черевики з високими берцями демісезонні",                               1, 2, "I"),
        ("Черевики з високими берцями літні або черевики літні",                  1, 2, "I"),
        ("Черевики з високими берцями зимові",                                    1, 2, "I"),
        ("Капці казармені",                                                       1, 2, "I"),
        ("Бахіли утеплені",                                                       1, 2, "I"),
        ("Чоботи гумові",                                                         1, 2, "I"),
        # СПОРЯДЖЕННЯ ТА ЕКІПІРУВАННЯ (30-47)
        ("Рукавички тактичні",                                                    1, 2, "I"),
        ("Беруші спеціальні індивідуальні",                                       1, 2, "I"),
        ("Ремінь брючний",                                                        1, 2, "I"),
        ("Сумка транспортна індивідуальна",                                       1, 5, "I"),
        ("Сумка адміністративна",                                                 1, 5, "I"),
        ("Сумка-підсумок для предметів особистої гігієни",                        1, 4, "I"),
        ("Модульна всесезонна спальна система (мішок спальний)",                  1, 2, "I"),
        ("Килим спальний польовий ізоляційний",                                   1, 2, "I"),
        ("Сидіння польове ізоляційне",                                            1, 1, "I"),
        ("Чохол для фляги індивідуальної польової",                               1, 4, "I"),
        ("Казанок індивідуальний польовий",                                       1, 4, "I"),
        ("Фляга індивідуальна польова",                                           1, 2, "I"),
        ("Столовий набір індивідуальний польовий",                                1, 4, "I"),
        ("Кухоль індивідуальний складаний",                                       1, 4, "I"),
        ("Ніж індивідуальний табірний",                                           1, 4, "I"),
        ("Ліхтарик польовий індивідуальний",                                      1, 2, "I"),
        ("Чохол для шолома балістичного",                                         3, 2, "I"),
        ("Чохол для захисного балістичного модульного жилета",                    1, 2, "I"),
        # ЗАСОБИ ІНДИВІДУАЛЬНОГО ЗАХИСТУ (48-50)
        ("Окуляри захисні балістичні",                                            1, 2, "I"),
        ("Шолом бойовий балістичний",                                             1, 2, "I"),
        ("Бронежилет модульний",                                                  1, 2, "I"),
    ]

    for sort_i, (nd_name, qty, wear, cat) in enumerate(items):
        nd = cur.execute(
            "SELECT id FROM norm_dictionary WHERE name=?", (nd_name,)
        ).fetchone()
        if nd:
            cur.execute(
                """INSERT INTO supply_norm_items
                       (norm_id, norm_dict_id, quantity, wear_years, category, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (norm_id, nd["id"], qty, wear, cat, sort_i * 10)
            )


def _insert_norm_dictionary(cur) -> None:
    """
    Вставити початковий словник норм видачі на основі Норми №1 (наказ МОУ №232 зі змінами №306).
    Джерело: https://zakon.rada.gov.ua/laws/show/z0745-19
    Строки носіння — Категорія 5 (бойові частини).
    Формат: (name, unit, qty, wear_years_cat5, note_refs)
    Позиції 1-50 відповідно до таблиці наказу.
    """
    groups_and_items = [
        # Позиції 1-4
        ("Головні убори", 10, [
            ("Кашкет польовий (кепі бойове)",          "штука",    2, 2,   "1; 2; 5; 8; 11"),
            ("Панама літня польова",                    "штука",    1, 2,   "1; 3; 8; 11"),
            ("Шапка-феска (шапка-підшоломник)",         "штука",    1, 1,   "1; 8; 11"),
            ("Шапка зимова",                            "штука",    1, 2,   "1; 2; 8; 11"),
        ]),
        # Позиції 5-16
        ("Обмундирування", 20, [
            ("Костюм літній польовий",                  "комплект", 2, 2,   "1; 2; 5; 7; 8; 9; 11"),
            ("Штани костюма літнього польового",        "штука",    1, 1,   "1"),
            ("Костюм-утеплювач",                        "комплект", 1, 2,   "1; 5; 8; 11"),
            ("Костюм вітровологозахисний демісезонний", "комплект", 1, 2,   None),
            ("Куртка вітровологозахисна зимова (куртка утеплена польова)",  "комплект", 1, 2, "1; 2; 5; 8; 10; 11"),
            ("Штани вітровологозахисні зимові (штани утеплені польові)",    "комплект", 1, 2, "1; 2; 5; 8; 10; 11"),
            ("Сорочка-поло",                            "штука",    2, 2,   None),
            ("Сорочка бойова",                          "штука",    2, 2,   "1; 7; 8; 11"),
            ("Рукавички демісезонні",                   "пара",     1, 1,   "1; 8; 11"),
            ("Рукавички зимові",                        "пара",     1, 1,   "1; 2; 8; 11"),
            ("Шарф-труба літній",                       "штука",    1, 2,   "1; 8"),
            ("Шарф-труба зимовий",                      "штука",    1, 2,   "1; 8"),
        ]),
        # Позиції 17-23
        ("Білизна та шкарпетки", 30, [
            ("Фуфайка (з короткими рукавами)",          "штука",    2, 1,   "2; 7; 11"),
            ("Труси чоловічі",                          "штука",    2, 1,   "7; 11"),
            ("Труси та топ жіночий",                    "штука",    2, 1,   None),
            ("Білизна натільна демісезонна",            "комплект", 2, 1,   "7; 11"),
            ("Білизна для холодної погоди (сорочка зимова та кальсони зимові)", "комплект", 2, 2, "1; 2; 7; 11"),
            ("Шкарпетки літні (трекінгові)",            "пара",     6, 1,   "7"),
            ("Шкарпетки зимові (трекінгові)",           "пара",     3, 1,   "7"),
        ]),
        # Позиції 24-29
        ("Взуття", 40, [
            ("Черевики з високими берцями демісезонні", "пара",     1, 2,   "1; 2; 6; 11"),
            ("Черевики з високими берцями літні або черевики літні", "пара", 1, 2, "1; 6; 11"),
            ("Черевики з високими берцями зимові",      "пара",     1, 2,   "1; 6; 11"),
            ("Капці казармені",                         "пара",     1, 2,   "1; 8; 12"),
            ("Бахіли утеплені",                         "пара",     1, 2,   "1; 3; 8; 11; 12"),
            ("Чоботи гумові",                           "пара",     1, 2,   "3"),
        ]),
        # Позиції 30-47
        ("Спорядження та екіпірування", 50, [
            ("Рукавички тактичні",                      "пара",     1, 2,   "1; 3; 8; 11"),
            ("Беруші спеціальні індивідуальні",         "комплект", 1, 2,   "3; 8; 12"),
            ("Ремінь брючний",                          "штука",    1, 2,   "1; 2; 8; 11"),
            ("Сумка транспортна індивідуальна",         "штука",    1, 5,   "1; 11"),
            ("Сумка адміністративна",                   "штука",    1, 5,   None),
            ("Сумка-підсумок для предметів особистої гігієни", "штука", 1, 4, "3; 11"),
            ("Модульна всесезонна спальна система (мішок спальний)", "комплект", 1, 2, "3; 11"),
            ("Килим спальний польовий ізоляційний",     "штука",    1, 2,   "3; 4"),
            ("Сидіння польове ізоляційне",              "штука",    1, 1,   "3; 11; 12"),
            ("Чохол для фляги індивідуальної польової", "штука",    1, 4,   None),
            ("Казанок індивідуальний польовий",         "штука",    1, 4,   "3"),
            ("Фляга індивідуальна польова",             "штука",    1, 2,   None),
            ("Столовий набір індивідуальний польовий",  "комплект", 1, 4,   "3"),
            ("Кухоль індивідуальний складаний",         "штука",    1, 4,   "3; 11"),
            ("Ніж індивідуальний табірний",             "штука",    1, 4,   "11"),
            ("Ліхтарик польовий індивідуальний",        "штука",    1, 2,   None),
            ("Чохол для шолома балістичного",           "штука",    3, 2,   "2"),
            ("Чохол для захисного балістичного модульного жилета", "штука", 1, 2, "2"),
        ]),
        # Позиції 48-50
        ("Засоби індивідуального захисту", 60, [
            ("Окуляри захисні балістичні",              "комплект", 1, 2,   "2; 3; 11"),
            ("Шолом бойовий балістичний",               "штука",    1, 2,   "2"),
            ("Бронежилет модульний",                    "комплект", 1, 2,   "2"),
        ]),
    ]

    for group_name, g_order, items in groups_and_items:
        cur.execute(
            "INSERT INTO norm_dict_groups (name, sort_order) VALUES (?, ?)",
            (group_name, g_order)
        )
        group_id = cur.lastrowid
        for i, (item_name, unit, qty, wear_years, notes) in enumerate(items):
            cur.execute(
                """INSERT INTO norm_dictionary
                       (group_id, name, unit, default_qty, default_wear_years, note_refs, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (group_id, item_name, unit, qty, wear_years or 0, notes, i * 10)
            )


def _insert_system_templates(cur) -> None:
    """Вставити системні шаблони документів."""
    import json

    INVOICE_HTML = """<div style="text-align:center;font-size:11pt;margin-bottom:4px"><strong>{{unit_name}}</strong><br>{{service_name}}</div>
<div style="text-align:center;margin-bottom:16px"><strong style="font-size:14pt">НАКЛАДНА-ВИМОГА № {{invoice_number}}</strong><br>від {{invoice_date}}</div>
<table style="width:100%;border-collapse:collapse;margin-bottom:8px"><tr><td style="width:50%">Підстава: {{base_document}}</td><td style="width:50%;text-align:right">Дійсна до: {{valid_until}}</td></tr><tr><td colspan="2">Отримувач: {{recipient_rank}} {{recipient_name}}, {{recipient_unit}}</td></tr></table>
{{table:items_list}}
<div style="margin-top:8px;text-align:right">Загальна сума: <strong>{{total_sum}} грн</strong><br>({{total_sum_words}})</div>
<div style="margin-top:24px">{{table:signatories}}</div>"""

    RV_HTML = """<div style="text-align:center;font-size:11pt;margin-bottom:4px"><strong>{{unit_name}}</strong><br>{{service_name}}</div>
<div style="text-align:center;margin-bottom:16px"><strong style="font-size:14pt">РОЗДАВАЛЬНА ВІДОМІСТЬ № {{invoice_number}}</strong><br>від {{invoice_date}}</div>
<table style="width:100%;border-collapse:collapse;margin-bottom:8px"><tr><td>Підрозділ: {{recipient_unit}}</td><td style="text-align:right">Підстава: {{base_document}}</td></tr></table>
{{table:items_list}}
<div style="margin-top:24px">{{table:signatories}}</div>"""

    ATTESTAT_HTML = """<div style="text-align:center;font-size:11pt;margin-bottom:4px"><strong>{{unit_name}}</strong></div>
<div style="text-align:center;margin-bottom:16px"><strong style="font-size:14pt">РЕЧОВИЙ АТЕСТАТ</strong><br>№ {{invoice_number}} від {{invoice_date}}</div>
<table style="width:100%;border-collapse:collapse;margin-bottom:8px"><tr><td colspan="2">Виданий: {{recipient_rank}} {{recipient_name}}</td></tr><tr><td>Посада: </td><td>Підрозділ: {{recipient_unit}}</td></tr></table>
<p>Перебуває на речовому забезпеченні з ___.___.{{current_year}} р.</p>
{{table:items_list}}
<div style="margin-top:24px">{{table:signatories}}</div>"""

    WRITE_OFF_HTML = """<div style="text-align:center;font-size:11pt;margin-bottom:4px"><strong>ЗАТВЕРДЖУЮ</strong><br>Командир в/ч {{unit_name}}<br>{{chief_tvo}}{{chief_rank}} {{chief_name}}<br>"___" ___________ {{current_year}} р.</div>
<div style="text-align:center;margin-bottom:16px"><strong style="font-size:14pt">АКТ № {{invoice_number}}</strong><br>про списання речового майна<br>від {{doc_date_full}}</div>
<p>Підстава: {{base_document}}</p>
{{table:items_list}}
<div style="margin-top:8px;text-align:right">Загальна сума: <strong>{{total_sum}} грн</strong></div>
<div style="margin-top:24px">{{table:signatories}}</div>"""

    templates = [
        # (name, doc_type, group, description, html, orientation, size, mt, mb, ml, mr)
        ("Накладна-вимога (стандартна)", "invoice", "Накладні",
         "Стандартна форма накладної-вимоги (Додаток 25)",
         INVOICE_HTML, "portrait", "A4", 20, 20, 30, 10),
        ("Роздавальна відомість (стандартна)", "rv", "Відомості",
         "Стандартна форма роздавальної відомості",
         RV_HTML, "landscape", "A4", 15, 15, 20, 10),
        ("Речовий атестат (стандартний)", "attestat", "Атестати",
         "Стандартна форма речового атестату",
         ATTESTAT_HTML, "portrait", "A4", 20, 20, 30, 10),
        ("Акт списання (стандартний)", "write_off", "Акти",
         "Стандартна форма акту списання речового майна",
         WRITE_OFF_HTML, "portrait", "A4", 20, 20, 30, 10),
    ]
    for name, doc_type, group, desc, html, orient, size, mt, mb, ml, mr in templates:
        grid_data = json.dumps({"html": html}, ensure_ascii=False)
        cur.execute("""
            INSERT OR IGNORE INTO doc_templates
              (name, doc_type, template_group, description, grid_data,
               page_orientation, page_size,
               margin_top, margin_bottom, margin_left, margin_right,
               is_system, default_for_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,1)
        """, (name, doc_type, group, desc, grid_data, orient, size, mt, mb, ml, mr))

