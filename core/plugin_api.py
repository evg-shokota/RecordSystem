"""
core/plugin_api.py — Публічний API системи для плагінів.

Плагін отримує екземпляр SystemAPI через метод register(app, api).
Це єдина точка входу — плагін не імпортує core.* напряму.

Приклад використання в плагіні:
─────────────────────────────────────────────
class Plugin(BasePlugin):

    def register(self, app, api):
        self.api = api          # зберегти для використання в routes
        from .routes import bp
        bp.api = api            # передати в Blueprint
        app.register_blueprint(bp)

    def on_install(self, conn):
        # conn — пряме з'єднання, для міграцій
        conn.execute("CREATE TABLE IF NOT EXISTS ...")

# У routes.py плагіна:
@bp.route("/my-route")
def my_view():
    personnel = bp.api.personnel.get_list(unit_id=1)
    items = bp.api.warehouse.get_stock()
    ...
─────────────────────────────────────────────
"""

from __future__ import annotations
from typing import Optional


class PersonnelAPI:
    """Доступ до даних особового складу."""

    def get_list(self, *, unit_id=None, group_id=None,
                 battalion_id=None, search=None,
                 is_active=True, limit=500) -> list[dict]:
        """Список о/с з фільтрами."""
        from core.db import get_connection
        conn = get_connection()
        where = ["1=1"]
        params = []
        if is_active is not None:
            where.append("p.is_active = ?"); params.append(1 if is_active else 0)
        if unit_id:
            where.append("p.unit_id = ?"); params.append(unit_id)
        if group_id:
            where.append("p.group_id = ?"); params.append(group_id)
        if battalion_id:
            where.append("p.battalion_id = ?"); params.append(battalion_id)
        if search:
            where.append("(p.last_name LIKE ? OR p.first_name LIKE ? OR p.ipn LIKE ?)")
            s = f"%{search}%"; params += [s, s, s]
        sql = f"""
            SELECT p.*,
                   u.name as unit_name,
                   b.name as battalion_name,
                   g.name as group_name
            FROM personnel p
            LEFT JOIN units u ON p.unit_id = u.id
            LEFT JOIN battalions b ON p.battalion_id = b.id
            LEFT JOIN groups g ON p.group_id = g.id
            WHERE {' AND '.join(where)}
            ORDER BY p.last_name, p.first_name
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get(self, personnel_id: int) -> Optional[dict]:
        """Одна картка по id."""
        from core.db import get_connection
        conn = get_connection()
        row = conn.execute("""
            SELECT p.*, u.name as unit_name, b.name as battalion_name, g.name as group_name
            FROM personnel p
            LEFT JOIN units u ON p.unit_id = u.id
            LEFT JOIN battalions b ON p.battalion_id = b.id
            LEFT JOIN groups g ON p.group_id = g.id
            WHERE p.id = ?
        """, (personnel_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_items(self, personnel_id: int, status="active") -> list[dict]:
        """Майно на картці о/с."""
        from core.db import get_connection
        conn = get_connection()
        rows = conn.execute("""
            SELECT pi.*, d.name as item_name, d.unit_of_measure, d.is_inventory
            FROM personnel_items pi
            JOIN item_dictionary d ON pi.item_id = d.id
            WHERE pi.personnel_id = ? AND pi.status = ?
            ORDER BY d.name
        """, (personnel_id, status)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_units(self) -> list[dict]:
        """Всі підрозділи."""
        from core.db import get_connection
        conn = get_connection()
        rows = conn.execute("""
            SELECT u.*, b.name as battalion_name
            FROM units u JOIN battalions b ON u.battalion_id = b.id
            ORDER BY b.name, u.name
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_groups(self) -> list[dict]:
        from core.db import get_connection
        conn = get_connection()
        rows = conn.execute("SELECT * FROM groups ORDER BY id").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Запис ──────────────────────────────────────────────────────────

    def attach_file(self, entity_type: str, entity_id: int,
                    file_path: str, original_name: str,
                    description: str = "", source: str = "plugin") -> int:
        """
        Прикріпити файл до картки о/с, підрозділу або іншого запису.

        entity_type: 'personnel' | 'unit' | 'invoice' | 'sheet' | 'attestat'
        entity_id:   id запису
        file_path:   абсолютний шлях до файлу на диску
        original_name: оригінальна назва файлу (для відображення)
        description: текстовий опис
        source:      звідки прийшов файл (наприклад: 'cloud_sync', 'import')

        Повертає: id новоствореного запису в таблиці attachments
        """
        from core.db import get_connection
        conn = get_connection()
        cur = conn.execute("""
            INSERT INTO attachments
                (entity_type, entity_id, file_path, original_name, description, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))
        """, (entity_type, entity_id, file_path, original_name, description, source))
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return new_id

    def add_note(self, entity_type: str, entity_id: int,
                 text: str, source: str = "plugin") -> int:
        """
        Додати текстову нотатку до картки о/с або підрозділу.

        entity_type: 'personnel' | 'unit'
        entity_id:   id запису
        text:        текст нотатки
        source:      назва плагіна або 'user'

        Повертає: id новоствореного запису
        """
        from core.db import get_connection
        conn = get_connection()
        cur = conn.execute("""
            INSERT INTO notes (entity_type, entity_id, text, source, created_at)
            VALUES (?, ?, ?, ?, datetime('now','localtime'))
        """, (entity_type, entity_id, text, source))
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return new_id

    def get_notes(self, entity_type: str, entity_id: int) -> list[dict]:
        """Нотатки прив'язані до картки."""
        from core.db import get_connection
        conn = get_connection()
        rows = conn.execute("""
            SELECT * FROM notes
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY created_at DESC
        """, (entity_type, entity_id)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_attachments(self, entity_type: str, entity_id: int) -> list[dict]:
        """Прикріплені файли до картки."""
        from core.db import get_connection
        conn = get_connection()
        rows = conn.execute("""
            SELECT * FROM attachments
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY created_at DESC
        """, (entity_type, entity_id)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_field(self, personnel_id: int, field: str, value) -> bool:
        """
        Оновити окреме поле картки о/с.

        Дозволені поля (лише некритичні, що не впливають на облік):
            phone, photo_path, card_number, notes_text

        Повертає True якщо успішно, False якщо поле заборонено.
        """
        ALLOWED = {'phone', 'photo_path', 'card_number'}
        if field not in ALLOWED:
            return False
        from core.db import get_connection
        conn = get_connection()
        conn.execute(
            f"UPDATE personnel SET {field} = ? WHERE id = ?",
            (value, personnel_id)
        )
        conn.commit()
        conn.close()
        return True


class FilesAPI:
    """
    Робота з файловою системою — збереження, отримання шляху.
    Плагін зберігає файли в окремій підпапці, не ризикуючи зіпсувати чужі файли.
    """

    def get_plugin_dir(self, plugin_slug: str) -> str:
        """
        Повертає шлях до папки плагіна для збереження файлів.
        Папка створюється автоматично.
        Шлях: <db_dir>/plugin_files/<plugin_slug>/
        """
        from core.db import get_db_path
        from pathlib import Path
        folder = Path(get_db_path()).parent / "plugin_files" / plugin_slug
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def get_entity_dir(self, plugin_slug: str, entity_type: str, entity_id: int) -> str:
        """
        Папка для файлів конкретного запису.
        Шлях: <db_dir>/plugin_files/<plugin_slug>/<entity_type>/<entity_id>/
        """
        from core.db import get_db_path
        from pathlib import Path
        folder = (Path(get_db_path()).parent / "plugin_files"
                  / plugin_slug / entity_type / str(entity_id))
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def save_bytes(self, plugin_slug: str, entity_type: str,
                   entity_id: int, filename: str, data: bytes) -> str:
        """
        Зберегти байти у файл і повернути абсолютний шлях.

        Приклад:
            path = api.files.save_bytes('cloud_docs', 'personnel', 42, 'doc.pdf', pdf_bytes)
            api.personnel.attach_file('personnel', 42, path, 'doc.pdf', 'З хмари')
        """
        from pathlib import Path
        folder = Path(self.get_entity_dir(plugin_slug, entity_type, entity_id))
        dest = folder / filename
        # Якщо файл вже існує — додати суфікс
        counter = 1
        while dest.exists():
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            dest = folder / f"{stem}_{counter}{suffix}"
            counter += 1
        dest.write_bytes(data)
        return str(dest)

    def list_files(self, plugin_slug: str, entity_type: str, entity_id: int) -> list[dict]:
        """
        Список файлів в папці запису.
        Повертає: list[{name, path, size, modified_at}]
        """
        from pathlib import Path
        folder = Path(self.get_entity_dir(plugin_slug, entity_type, entity_id))
        result = []
        for f in sorted(folder.iterdir()):
            if f.is_file():
                stat = f.stat()
                result.append({
                    "name":        f.name,
                    "path":        str(f),
                    "size":        stat.st_size,
                    "modified_at": stat.st_mtime,
                })
        return result


class WarehouseAPI:
    """Доступ до складу."""

    def get_stock(self, item_id=None, category=None) -> list[dict]:
        """
        Залишки на складі (прихід мінус видані).
        Повертає список {item_id, item_name, unit_of_measure, category, qty_in, qty_out, qty_balance, price}.
        Параметри item_id і category — необов'язкові фільтри.
        """
        from core.db import get_connection
        from core.warehouse import get_stock
        conn = get_connection()
        rows = get_stock(conn)
        conn.close()
        if item_id:
            rows = [r for r in rows if r["item_id"] == item_id]
        if category:
            rows = [r for r in rows if r["category"] == category]
        return rows

    def get_income(self, item_id=None, date_from=None, date_to=None) -> list[dict]:
        """Прихідні записи."""
        from core.db import get_connection
        conn = get_connection()
        where = ["1=1"]; params = []
        if item_id:   where.append("wi.item_id = ?"); params.append(item_id)
        if date_from: where.append("wi.date >= ?");   params.append(date_from)
        if date_to:   where.append("wi.date <= ?");   params.append(date_to)
        rows = conn.execute(f"""
            SELECT wi.*, d.name as item_name, d.unit_of_measure
            FROM warehouse_income wi JOIN item_dictionary d ON wi.item_id = d.id
            WHERE {' AND '.join(where)} ORDER BY wi.date DESC
        """, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]


class InvoiceAPI:
    """Доступ до накладних."""

    def get_list(self, *, status=None, direction=None,
                 date_from=None, date_to=None, limit=200) -> list[dict]:
        from core.db import get_connection
        conn = get_connection()
        where = ["1=1"]; params = []
        if status:    where.append("i.status = ?");    params.append(status)
        if direction: where.append("i.direction = ?"); params.append(direction)
        if date_from: where.append("i.created_at >= ?"); params.append(date_from)
        if date_to:   where.append("i.created_at <= ?"); params.append(date_to)
        rows = conn.execute(f"""
            SELECT i.*,
                   p.last_name || ' ' || p.first_name as recipient_name,
                   u.name as recipient_unit_name
            FROM invoices i
            LEFT JOIN personnel p ON i.recipient_personnel_id = p.id
            LEFT JOIN units u ON i.recipient_unit_id = u.id
            WHERE {' AND '.join(where)}
            ORDER BY i.created_at DESC LIMIT ?
        """, params + [limit]).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get(self, invoice_id: int) -> Optional[dict]:
        from core.db import get_connection
        conn = get_connection()
        row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        items = conn.execute("""
            SELECT ii.*, d.name as item_name, d.unit_of_measure
            FROM invoice_items ii JOIN item_dictionary d ON ii.item_id = d.id
            WHERE ii.invoice_id = ?
        """, (invoice_id,)).fetchall()
        conn.close()
        if not row: return None
        result = dict(row)
        result["items"] = [dict(i) for i in items]
        return result


class ItemDictionaryAPI:
    """Доступ до словника майна."""

    def get_list(self, search=None, is_inventory=None) -> list[dict]:
        from core.db import get_connection
        conn = get_connection()
        where = ["1=1"]; params = []
        if search:       where.append("name LIKE ?"); params.append(f"%{search}%")
        if is_inventory is not None:
            where.append("is_inventory = ?"); params.append(1 if is_inventory else 0)
        rows = conn.execute(
            f"SELECT * FROM item_dictionary WHERE {' AND '.join(where)} ORDER BY name",
            params
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get(self, item_id: int) -> Optional[dict]:
        from core.db import get_connection
        conn = get_connection()
        row = conn.execute("SELECT * FROM item_dictionary WHERE id = ?", (item_id,)).fetchone()
        conn.close()
        return dict(row) if row else None


class SettingsAPI:
    """Доступ до налаштувань системи."""

    def get(self, key: str, default: str = "") -> str:
        from core.settings import get_setting
        return get_setting(key, default)

    def get_all(self) -> dict:
        from core.settings import get_all_settings
        return get_all_settings()

    def set(self, key: str, value: str) -> None:
        from core.settings import set_setting
        set_setting(key, value)


class DatabaseAPI:
    """
    Прямий доступ до БД для складних запитів яких немає в інших API.
    Плагін відповідає за закриття з'єднання.
    """

    def get_connection(self):
        """Повертає sqlite3.Connection з row_factory=sqlite3.Row."""
        from core.db import get_connection
        return get_connection()

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        """Виконати SELECT і повернути список dict. З'єднання закривається автоматично."""
        from core.db import get_connection
        conn = get_connection()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def write(self, sql: str, params: tuple = ()) -> int:
        """
        Виконати INSERT/UPDATE/DELETE. Повертає lastrowid.
        УВАГА: використовуй тільки для таблиць свого плагіна.
        Для основних таблиць — через відповідний API.
        """
        from core.db import get_connection
        conn = get_connection()
        cur = conn.execute(sql, params)
        conn.commit()
        last_id = cur.lastrowid
        conn.close()
        return last_id


class AuditAPI:
    """Запис в журнал дій."""

    def log(self, action: str, table_name: str,
            record_id: int = None, old_data: dict = None, new_data: dict = None) -> None:
        from core.audit import log_action
        log_action(action, table_name, record_id, old_data, new_data)


class SystemAPI:
    """
    Головний об'єкт API — передається в plugin.register(app, api).
    Містить всі підсистеми як атрибути.

    api.personnel   — особовий склад
    api.warehouse   — склад
    api.invoices    — накладні
    api.items       — словник майна
    api.settings    — налаштування
    api.db          — прямий доступ до БД
    api.audit       — журнал дій
    """

    def __init__(self):
        self.personnel = PersonnelAPI()
        self.warehouse = WarehouseAPI()
        self.invoices  = InvoiceAPI()
        self.items     = ItemDictionaryAPI()
        self.settings  = SettingsAPI()
        self.db        = DatabaseAPI()
        self.audit     = AuditAPI()
        self.files     = FilesAPI()


# Глобальний екземпляр — створюється один раз при старті
_api_instance: Optional[SystemAPI] = None


def get_api() -> SystemAPI:
    """Повертає глобальний екземпляр SystemAPI."""
    global _api_instance
    if _api_instance is None:
        _api_instance = SystemAPI()
    return _api_instance
