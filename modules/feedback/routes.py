"""
modules/feedback/routes.py — Багтрекер / Побажання
"""
import io
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, session, send_file
from core.auth import login_required
from core.db import get_connection

bp = Blueprint("feedback", __name__, url_prefix="/feedback")

CATEGORIES = {
    "bug":     "🐛 Помилка",
    "feature": "💡 Побажання",
    "ui":      "🎨 Інтерфейс",
    "other":   "📝 Інше",
}
PRIORITIES = {
    "low":    "Низький",
    "normal": "Нормальний",
    "high":   "Високий",
}
STATUSES = {
    "new":         ("Нове",              "primary"),
    "in_progress": ("В роботі",          "warning"),
    "rework":      ("На доопрацювання",  "danger"),
    "done":        ("Виконано",          "success"),
    "rejected":    ("Відхилено",         "secondary"),
}


def _load_comments(conn, feedback_ids):
    """Повертає dict {feedback_id: [flat list of comments sorted by created_at]}"""
    if not feedback_ids:
        return {}
    placeholders = ",".join("?" * len(feedback_ids))
    rows = conn.execute(
        f"SELECT * FROM feedback_comments WHERE feedback_id IN ({placeholders}) ORDER BY created_at ASC",
        list(feedback_ids)
    ).fetchall()
    result = {fid: [] for fid in feedback_ids}
    for r in rows:
        result[r["feedback_id"]].append(dict(r))
    return result


def _build_tree(flat_comments):
    """Будує дерево: [{...comment, children: [...]}, ...]"""
    by_id = {c["id"]: dict(c, children=[]) for c in flat_comments}
    roots = []
    for c in by_id.values():
        pid = c.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(c)
        else:
            roots.append(c)
    return roots


def _format_comment_tree(comments, indent=0):
    """Рекурсивно форматує дерево коментарів у текст для звіту."""
    lines = []
    prefix = "  " * indent + ("↳ " if indent else "💬 ")
    for c in comments:
        lines.append(f"{prefix}[{c['created_at'][:16]}] {c['username']}: {c['body']}")
        if c.get("children"):
            lines.extend(_format_comment_tree(c["children"], indent + 1))
    return lines


@bp.route("/")
@login_required
def index():
    conn = get_connection()
    items = conn.execute(
        "SELECT * FROM feedback ORDER BY created_at DESC"
    ).fetchall()
    items = [dict(i) for i in items]
    comments_flat = _load_comments(conn, [i["id"] for i in items])
    conn.close()
    # Будуємо дерево для кожного запису
    comments_tree = {fid: _build_tree(flat) for fid, flat in comments_flat.items()}
    return render_template("feedback/index.html",
                           items=items,
                           comments=comments_tree,
                           categories=CATEGORIES,
                           priorities=PRIORITIES,
                           statuses=STATUSES)


@bp.route("/add", methods=["POST"])
@login_required
def add():
    data = request.get_json() or {}
    title    = (data.get("title") or "").strip()
    body     = (data.get("body") or "").strip()
    category = data.get("category", "bug")
    priority = data.get("priority", "normal")
    page_url = (data.get("page_url") or "").strip()

    if not title:
        return jsonify({"error": "Введіть заголовок"}), 400
    if category not in CATEGORIES:
        category = "other"
    if priority not in PRIORITIES:
        priority = "normal"

    conn = get_connection()
    conn.execute(
        """INSERT INTO feedback (user_id, username, category, priority, title, body, page_url)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session.get("user_id"), session.get("full_name", ""), category, priority,
         title, body, page_url)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:fid>/status", methods=["POST"])
@login_required
def set_status(fid):
    data   = request.get_json() or {}
    status = data.get("status", "new")
    note   = (data.get("note") or "").strip()
    if status not in STATUSES:
        return jsonify({"error": "Невідомий статус"}), 400

    conn = get_connection()
    if status in ("done", "rejected"):
        conn.execute(
            """UPDATE feedback SET status=?, resolved_by=?, resolved_at=datetime('now','localtime'),
               resolve_note=? WHERE id=?""",
            (status, session.get("full_name", ""), note, fid)
        )
    else:
        conn.execute(
            "UPDATE feedback SET status=?, resolved_by='', resolved_at=NULL, resolve_note='' WHERE id=?",
            (status, fid)
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:fid>/comment/add", methods=["POST"])
@login_required
def comment_add(fid):
    data      = request.get_json() or {}
    body      = (data.get("body") or "").strip()
    parent_id = data.get("parent_id")  # може бути None

    if not body:
        return jsonify({"error": "Порожній коментар"}), 400

    conn = get_connection()
    if not conn.execute("SELECT id FROM feedback WHERE id=?", (fid,)).fetchone():
        conn.close()
        return jsonify({"error": "Запис не знайдено"}), 404

    # Перевірити parent_id
    if parent_id:
        p = conn.execute(
            "SELECT id FROM feedback_comments WHERE id=? AND feedback_id=?", (parent_id, fid)
        ).fetchone()
        if not p:
            parent_id = None

    cur = conn.execute(
        """INSERT INTO feedback_comments (feedback_id, parent_id, user_id, username, body)
           VALUES (?, ?, ?, ?, ?)""",
        (fid, parent_id, session.get("user_id"), session.get("full_name", ""), body)
    )
    conn.commit()
    comment_id = cur.lastrowid
    row = conn.execute("SELECT * FROM feedback_comments WHERE id=?", (comment_id,)).fetchone()
    conn.close()
    return jsonify({"ok": True, "comment": dict(row)})


@bp.route("/<int:fid>/comment/<int:cid>/delete", methods=["POST"])
@login_required
def comment_delete(fid, cid):
    conn = get_connection()
    conn.execute("DELETE FROM feedback_comments WHERE id=? AND feedback_id=?", (cid, fid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:fid>/delete", methods=["POST"])
@login_required
def delete(fid):
    conn = get_connection()
    conn.execute("DELETE FROM feedback WHERE id=?", (fid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/claude")
@login_required
def claude_report():
    """Текстовий звіт для Клода — тільки активні записи з повним деревом коментарів."""
    conn = get_connection()
    items = conn.execute(
        "SELECT * FROM feedback WHERE status IN ('new','in_progress','rework') ORDER BY priority DESC, created_at ASC"
    ).fetchall()
    items = [dict(i) for i in items]
    comments_flat = _load_comments(conn, [i["id"] for i in items])
    conn.close()

    lines = []
    lines.append("=" * 70)
    lines.append("  АКТИВНІ ЗАПИСИ БАГТРЕКЕРА / ПОБАЖАНЬ")
    lines.append(f"  Станом на: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    lines.append(f"  Активних записів: {len(items)}")
    lines.append("=" * 70)
    lines.append("")

    if not items:
        lines.append("  Активних записів немає. Всі завдання виконані або відхилені.")
    else:
        for pri in ["high", "normal", "low"]:
            grp = [i for i in items if i["priority"] == pri]
            if not grp:
                continue
            lines.append(f"▌ ПРІОРИТЕТ: {PRIORITIES[pri].upper()} ({len(grp)} шт.)")
            lines.append("")
            for item in grp:
                cat  = CATEGORIES.get(item["category"], item["category"])
                stat = STATUSES.get(item["status"], (item["status"], ""))[0]
                lines.append(f"  [{item['id']}] {cat} | {stat}")
                lines.append(f"  Тема: {item['title']}")
                lines.append(f"  Від: {item['username'] or '—'}  |  {item['created_at'][:16]}")
                if item["page_url"]:
                    lines.append(f"  Сторінка: {item['page_url']}")
                if item["body"]:
                    lines.append(f"  Опис: {item['body']}")
                tree = _build_tree(comments_flat.get(item["id"], []))
                if tree:
                    lines.append(f"  Коментарі:")
                    lines.extend(_format_comment_tree(tree, indent=1))
                lines.append("")

    lines.append("=" * 70)
    lines.append("  ІНСТРУКЦІЯ ДЛЯ КЛОДА:")
    lines.append("  - Коментарі від користувача — уточнення / залишкові баги")
    lines.append("  - Відповідь в коментарі: POST /feedback/<id>/comment/add {body, parent_id}")
    lines.append("  - Зміна статусу: POST /feedback/<id>/status {status, note}")
    lines.append("=" * 70)

    content = "\n".join(lines)
    buf = io.BytesIO(content.encode("utf-8-sig"))
    filename = f"bugtracker_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="text/plain; charset=utf-8")


@bp.route("/export")
@login_required
def export():
    conn = get_connection()
    items = conn.execute("SELECT * FROM feedback ORDER BY created_at DESC").fetchall()
    items = [dict(i) for i in items]
    comments_flat = _load_comments(conn, [i["id"] for i in items])
    conn.close()

    lines = []
    lines.append("=" * 60)
    lines.append("  ЗВОРОТНІЙ ЗВ'ЯЗОК / ПОБАЖАННЯ / ПОМИЛКИ")
    lines.append(f"  Вигружено: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    lines.append("=" * 60)
    lines.append("")

    for item in items:
        cat  = CATEGORIES.get(item["category"], item["category"])
        pri  = PRIORITIES.get(item["priority"], item["priority"])
        stat = STATUSES.get(item["status"], (item["status"], ""))[0]
        lines.append(f"[{item['id']}] {cat}  |  Пріоритет: {pri}  |  Статус: {stat}")
        lines.append(f"Від: {item['username']}  |  {item['created_at']}")
        lines.append(f"Сторінка: {item['page_url'] or '—'}")
        lines.append(f"Тема: {item['title']}")
        if item["body"]:
            lines.append(f"Опис:\n{item['body']}")
        if item.get("resolved_by"):
            lines.append(f"Вирішив: {item['resolved_by']}  |  {(item.get('resolved_at') or '')[:16]}")
        if item.get("resolve_note"):
            lines.append(f"Нотатка: {item['resolve_note']}")
        tree = _build_tree(comments_flat.get(item["id"], []))
        if tree:
            lines.append("Коментарі:")
            lines.extend(_format_comment_tree(tree))
        lines.append("-" * 60)
        lines.append("")

    content = "\n".join(lines)
    buf = io.BytesIO(content.encode("utf-8-sig"))
    filename = f"feedback_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="text/plain; charset=utf-8")
