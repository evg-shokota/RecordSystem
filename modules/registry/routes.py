"""
modules/registry/routes.py — Реєстр документів

Об'єднаний список: накладні (invoices) + роздавальні відомості (rv).
Фільтри: тип, статус, напрям, підрозділ, дата від/до, пошук по номеру.
Author: White
"""
from flask import Blueprint, render_template, request
from core.auth import login_required
from core.db import get_connection

bp = Blueprint("registry", __name__, url_prefix="/registry")

STATUS_LABELS = {
    "draft":     "Чернетка",
    "created":   "Створено",
    "assigned":  "Присвоєно",
    "issued":    "Видано",
    "received":  "Отримано",
    "processed": "Проведено",
    "cancelled": "Скасовано",
    "active":    "Активна",
    "closed":    "Закрита",
}

STATUS_COLORS = {
    "draft":     "secondary",
    "created":   "primary",
    "assigned":  "info",
    "issued":    "warning",
    "received":  "info",
    "processed": "success",
    "cancelled": "danger",
    "active":    "warning",
    "closed":    "success",
}

DIRECTION_LABELS = {
    "issue":  "Видача",
    "return": "Повернення",
}


@bp.route("/")
@login_required
def index():
    conn = get_connection()

    # ── Фільтри ──────────────────────────────────────────────
    doc_type  = request.args.get("doc_type", "")     # invoice | rv | ""
    status    = request.args.get("status", "")
    direction = request.args.get("direction", "")
    unit_id   = request.args.get("unit_id", "", type=str)
    date_from = request.args.get("date_from", "")
    date_to   = request.args.get("date_to", "")
    search    = request.args.get("q", "").strip()
    no_scan   = request.args.get("no_scan", "")      # "1" — тільки без скану

    rows = []

    # ── Накладні ─────────────────────────────────────────────
    if doc_type in ("", "invoice"):
        conds = ["1=1"]
        params = []
        if status:
            conds.append("i.status = ?"); params.append(status)
        if direction:
            conds.append("i.direction = ?"); params.append(direction)
        if unit_id:
            conds.append("(i.recipient_unit_id = ? OR i.sender_unit_id = ?)"); params += [unit_id, unit_id]
        if date_from:
            conds.append("COALESCE(i.issued_date, i.created_at) >= ?"); params.append(date_from)
        if date_to:
            conds.append("COALESCE(i.issued_date, i.created_at) <= ?"); params.append(date_to + " 23:59:59")
        if search:
            conds.append("(i.number LIKE ? OR p.last_name LIKE ? OR u.name LIKE ?)"); params += [f"%{search}%"] * 3
        if no_scan == "1":
            conds.append("(i.scan_path IS NULL OR i.scan_path = '')")
            if not status:
                conds.append("i.status NOT IN ('draft','cancelled')")

        invoices = conn.execute(f"""
            SELECT
                'invoice'                           AS doc_type,
                i.id,
                i.number,
                i.status,
                i.direction,
                COALESCE(i.issued_date, i.created_at) AS doc_date,
                i.created_at,
                i.scan_path,
                i.is_external,
                (p.last_name || ' ' || COALESCE(p.first_name, '') ||
                 CASE WHEN p.middle_name IS NOT NULL AND p.middle_name != '' THEN ' ' || p.middle_name ELSE '' END)
                                                    AS recipient_name,
                u.name                              AS unit_name,
                COUNT(ii.id)                        AS items_count,
                SUM(COALESCE(ii.actual_qty, ii.planned_qty) * ii.price) AS total_sum
            FROM invoices i
            LEFT JOIN personnel p  ON i.recipient_personnel_id = p.id
            LEFT JOIN units u      ON COALESCE(i.recipient_unit_id, i.sender_unit_id) = u.id
            LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
            WHERE {' AND '.join(conds)}
            GROUP BY i.id
            ORDER BY COALESCE(i.issued_date, i.created_at) DESC
            LIMIT 500
        """, params).fetchall()
        rows += [dict(r) for r in invoices]

    # ── Роздавальні відомості ─────────────────────────────────
    if doc_type in ("", "rv"):
        conds = ["1=1"]
        params = []
        if status:
            # Маппінг статусів РВ до статусів накладних де потрібно
            rv_status = status
            if status == "issued":   rv_status = "active"
            if status == "processed": rv_status = "closed"
            conds.append("ds.status = ?"); params.append(rv_status)
        if direction:
            conds.append("ds.direction = ?"); params.append(direction)
        if unit_id:
            conds.append("ds.unit_id = ?"); params.append(unit_id)
        if date_from:
            conds.append("COALESCE(ds.doc_date, ds.created_at) >= ?"); params.append(date_from)
        if date_to:
            conds.append("COALESCE(ds.doc_date, ds.created_at) <= ?"); params.append(date_to + " 23:59:59")
        if search:
            conds.append("(ds.number LIKE ? OR u.name LIKE ?)"); params += [f"%{search}%"] * 2
        if no_scan == "1":
            conds.append("(ds.scan_path IS NULL OR ds.scan_path = '')")
            if not status:
                conds.append("ds.status NOT IN ('draft','cancelled')")

        rvs = conn.execute(f"""
            SELECT
                'rv'                                AS doc_type,
                ds.id,
                ds.number,
                ds.status,
                ds.direction,
                COALESCE(ds.doc_date, ds.created_at) AS doc_date,
                ds.created_at,
                ds.scan_path,
                ds.is_external                      AS is_external,
                NULL                                AS recipient_name,
                u.name                              AS unit_name,
                COUNT(DISTINCT dsr.id)              AS items_count,
                ds.total_sum                        AS total_sum
            FROM distribution_sheets ds
            LEFT JOIN units u ON ds.unit_id = u.id
            LEFT JOIN distribution_sheet_rows dsr ON dsr.sheet_id = ds.id
            WHERE {' AND '.join(conds)}
            GROUP BY ds.id
            ORDER BY COALESCE(ds.doc_date, ds.created_at) DESC
            LIMIT 500
        """, params).fetchall()
        rows += [dict(r) for r in rvs]

    # Сортуємо об'єднаний список за датою
    rows.sort(key=lambda r: (r.get("doc_date") or ""), reverse=True)
    rows = rows[:500]

    # ── Довідники для фільтрів ────────────────────────────────
    units = conn.execute(
        "SELECT id, name FROM units ORDER BY name"
    ).fetchall()

    conn.close()

    return render_template(
        "registry/index.html",
        rows=rows,
        units=[dict(u) for u in units],
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        direction_labels=DIRECTION_LABELS,
        filters={
            "doc_type":  doc_type,
            "status":    status,
            "direction": direction,
            "unit_id":   unit_id,
            "date_from": date_from,
            "date_to":   date_to,
            "q":         search,
            "no_scan":   no_scan,
        },
    )
