"""
core/audit.py — журнал дій користувачів
"""
import json
from flask import session
from core.db import get_connection


def log_action(
    action: str,
    table_name: str,
    record_id: int | None = None,
    old_data: dict | None = None,
    new_data: dict | None = None,
) -> None:
    """
    Записати дію в журнал.
    action: add | edit | delete | move | status_change
    """
    user_id = session.get("user_id")
    conn = get_connection()
    conn.execute(
        """INSERT INTO audit_log (user_id, action, table_name, record_id, old_data, new_data)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            action,
            table_name,
            record_id,
            json.dumps(old_data, ensure_ascii=False) if old_data else None,
            json.dumps(new_data, ensure_ascii=False) if new_data else None,
        )
    )
    conn.commit()
    conn.close()


def get_audit_log(limit: int = 100, offset: int = 0) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.*, u.full_name as user_full_name, u.username
           FROM audit_log a
           LEFT JOIN users u ON a.user_id = u.id
           ORDER BY a.created_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
