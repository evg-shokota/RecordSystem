"""
core/settings.py — читання та запис налаштувань системи
Author: White
"""
from pathlib import Path
from core.db import get_connection


def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO settings (key, value, updated_at)
           VALUES (?, ?, datetime('now','localtime'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value,
           updated_at = excluded.updated_at""",
        (key, value)
    )
    conn.commit()
    conn.close()


def get_storage_path() -> Path:
    """Повертає абсолютний шлях до папки зберігання файлів.
    Читає storage_path з налаштувань, або дефолт: поруч з database.db."""
    from core.db import get_db_path
    configured = get_setting("storage_path", "")
    if configured:
        p = Path(configured)
        if p.exists():
            return p
        # Спробувати створити
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError:
            pass
    # Дефолт: поруч з database.db
    return Path(get_db_path()).parent / "storage"


def get_all_settings() -> dict:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def update_settings(data: dict) -> None:
    """Масове оновлення налаштувань."""
    conn = get_connection()
    for key, value in data.items():
        conn.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, datetime('now','localtime'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
               updated_at = excluded.updated_at""",
            (key, str(value))
        )
    conn.commit()
    conn.close()
