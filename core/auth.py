"""
core/auth.py — авторизація, ролі, сесії
Author: White
"""
import json
import bcrypt
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
from core.db import get_connection


# ---------- Паролі ----------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ---------- Користувачі ----------

def get_user_by_username(username: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT u.*, r.name as role_name, r.permissions
           FROM users u JOIN roles r ON u.role_id = r.id
           WHERE u.username = ? AND u.is_active = 1""",
        (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT u.*, r.name as role_name, r.permissions
           FROM users u JOIN roles r ON u.role_id = r.id
           WHERE u.id = ?""",
        (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def login_user(username: str, password: str) -> dict | None:
    """Перевірити логін/пароль. Повертає дані користувача або None."""
    user = get_user_by_username(username)
    if not user:
        return None
    if not check_password(password, user["password_hash"]):
        return None
    return user


def create_user(username: str, password: str, full_name: str, role_id: int) -> int:
    """Створити нового користувача. Повертає id."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, full_name, role_id) VALUES (?, ?, ?, ?)",
        (username, hash_password(password), full_name, role_id)
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def is_first_run() -> bool:
    """Перевірити чи є хоч один користувач в базі."""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count == 0


# ---------- Сесія ----------

def set_session(user: dict) -> None:
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["full_name"] = user["full_name"]
    session["role_name"] = user["role_name"]
    session["permissions"] = user["permissions"]
    session["theme"] = user.get("theme") or "default"


def clear_session() -> None:
    session.clear()


def current_user() -> dict | None:
    if "user_id" not in session:
        return None
    return get_user_by_id(session["user_id"])


# ---------- Права доступу ----------

def has_permission(permission: str) -> bool:
    """Перевірити чи має поточний користувач певне право."""
    if "permissions" not in session:
        return False
    try:
        perms = json.loads(session["permissions"])
    except (json.JSONDecodeError, TypeError):
        return False
    if perms.get("all"):
        return True
    return bool(perms.get(permission))


# ---------- Декоратори ----------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"error": "not_authenticated"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def permission_required(permission: str):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not has_permission(permission):
                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"error": "forbidden"}), 403
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorator


# ---------- Ролі ----------

def get_all_roles() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM roles ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_role(name: str, permissions: dict) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO roles (name, permissions) VALUES (?, ?)",
        (name, json.dumps(permissions, ensure_ascii=False))
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_role(role_id: int, name: str, permissions: dict) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE roles SET name = ?, permissions = ? WHERE id = ?",
        (name, json.dumps(permissions, ensure_ascii=False), role_id)
    )
    conn.commit()
    conn.close()
