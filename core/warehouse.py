"""
core/warehouse.py — спільна логіка складу

Централізує розрахунок залишків щоб не дублювати в invoices/, warehouse/, plugin_api.py

Стани майна на складі:
  qty_in        = весь прихід
  qty_out       = списано (накладні processed + РВ closed)
  qty_reserved  = зарезервовано (накладні issued/received + РВ active)
  qty_pending   = у чернетках (накладні assigned + РВ draft) — тільки попередження
  qty_free      = qty_in - qty_out - qty_reserved  ← доступно для нових накладних
Author: White
"""


# ─────────────────────────────────────────────────────────────
#  Спільні query-хелпери (раніше дублювались у modules/)
# ─────────────────────────────────────────────────────────────

def get_norm_groups(conn) -> list:
    """
    Групи словника норм з позиціями — для вибору майна у формах.
    Повертає: [{"id", "name", "norms": [{"id", "name", "unit"}, ...]}, ...]
    """
    nd_rows = conn.execute("""
        SELECT g.id AS group_id, g.name AS group_name, g.sort_order,
               nd.id, nd.name, nd.unit, nd.sort_order AS item_order
        FROM norm_dict_groups g
        JOIN norm_dictionary nd ON nd.group_id = g.id
        WHERE g.is_active = 1 AND nd.is_active = 1
        ORDER BY g.sort_order, nd.sort_order
    """).fetchall()
    groups: dict = {}
    for r in nd_rows:
        gid = r["group_id"]
        if gid not in groups:
            groups[gid] = {"id": gid, "name": r["group_name"], "norms": []}
        groups[gid]["norms"].append({
            "id": r["id"], "name": r["name"], "unit": r["unit"] or "шт",
        })
    return list(groups.values())


def get_units_by_battalion(conn, battalion_id: int) -> list:
    """Підрозділи батальйону, відсортовані за назвою."""
    return conn.execute(
        "SELECT id, name FROM units WHERE battalion_id = ? ORDER BY name",
        (battalion_id,)
    ).fetchall()


def get_platoons_by_unit(conn, unit_id: int) -> list:
    """Взводи підрозділу, відсортовані за назвою."""
    return conn.execute(
        "SELECT id, name FROM platoons WHERE unit_id = ? ORDER BY name",
        (unit_id,)
    ).fetchall()


def get_stock_for_invoice(conn, exclude_invoice_id=None) -> list:
    """
    Залишки для форми накладної: повертає всі позиції з qty_free > 0
    або qty_free <= 0 але з попередженням (qty_pending > 0).
    exclude_invoice_id: повертає позиції цієї накладної в залишок (для редагування).
    """
    return get_stock(conn, exclude_invoice_id=exclude_invoice_id, only_available=True)


def get_stock_for_rv(conn, exclude_sheet_id=None) -> list:
    """
    Залишки для форми РВ.
    exclude_sheet_id: повертає позиції цього РВ в залишок (для редагування).
    """
    return get_stock(conn, exclude_sheet_id=exclude_sheet_id, only_available=True)


def get_stock(conn,
              exclude_invoice_id=None,
              exclude_sheet_id=None,
              only_positive: bool = False,
              only_available: bool = False) -> list:
    """
    Залишки складу, згруповані по (item_id, category, price).

    Повертає список dicts:
      item_id, item_name, unit_of_measure, has_serial_number,
      category, price,
      qty_in,       — весь прихід
      qty_out,      — списано (processed)
      qty_reserved, — зарезервовано (issued + received)
      qty_pending,  — у assigned накладних/РВ (попередження)
      qty_free,     — доступно (qty_in - qty_out - qty_reserved)
      qty_balance,  — alias qty_free (для сумісності)
      balance,      — alias qty_free (для сумісності)
      has_warning,  — True якщо qty_free <= 0 але qty_free + qty_pending > 0
      total_sum

    only_positive:   повертати тільки рядки де qty_balance > 0 (старий режим)
    only_available:  повертати рядки де qty_free > 0 АБО has_warning=True
    """
    stock_rows = conn.execute("""
        SELECT wi.item_id, d.name AS item_name, d.unit_of_measure,
               d.has_serial_number,
               wi.category, wi.price,
               SUM(wi.quantity) AS qty_in
        FROM warehouse_income wi
        JOIN item_dictionary d ON wi.item_id = d.id
        WHERE COALESCE(wi.status, 'confirmed') = 'confirmed'
        GROUP BY wi.item_id, wi.category, wi.price
        ORDER BY d.name, wi.category, wi.price
    """).fetchall()

    # ── Списано (processed) ──────────────────────────────────────────
    out_map = {}

    # Накладні processed
    for r in conn.execute("""
        SELECT ii.item_id, ii.category, ii.price,
               SUM(COALESCE(ii.actual_qty, ii.planned_qty)) AS qty
        FROM invoice_items ii
        JOIN invoices i ON ii.invoice_id = i.id
        WHERE i.direction='issue' AND i.status='processed'
          AND (? IS NULL OR ii.invoice_id != ?)
        GROUP BY ii.item_id, ii.category, ii.price
    """, (exclude_invoice_id, exclude_invoice_id)).fetchall():
        key = (r["item_id"], r["category"], r["price"])
        out_map[key] = out_map.get(key, 0) + (r["qty"] or 0)

    # РВ closed (= виданий і підписаний — аналог processed для накладних)
    for r in conn.execute("""
        SELECT dsq.item_id,
               dsi.category, dsi.price,
               SUM(COALESCE(dsq.actual_qty, dsq.quantity)) AS qty
        FROM distribution_sheet_quantities dsq
        JOIN distribution_sheet_items dsi ON dsi.sheet_id = dsq.sheet_id
                                          AND dsi.item_id = dsq.item_id
        JOIN distribution_sheets ds ON ds.id = dsq.sheet_id
        WHERE ds.direction='issue' AND ds.status='closed'
          AND (? IS NULL OR dsq.sheet_id != ?)
        GROUP BY dsq.item_id, dsi.category, dsi.price
    """, (exclude_sheet_id, exclude_sheet_id)).fetchall():
        key = (r["item_id"], r["category"], r["price"])
        out_map[key] = out_map.get(key, 0) + (r["qty"] or 0)

    # Повернення (processed direction=return) — зменшують qty_out
    for r in conn.execute("""
        SELECT ii.item_id, ii.category, ii.price,
               SUM(COALESCE(ii.actual_qty, ii.planned_qty)) AS qty
        FROM invoice_items ii
        JOIN invoices i ON ii.invoice_id = i.id
        WHERE i.direction='return' AND i.status='processed'
          AND (? IS NULL OR ii.invoice_id != ?)
        GROUP BY ii.item_id, ii.category, ii.price
    """, (exclude_invoice_id, exclude_invoice_id)).fetchall():
        key = (r["item_id"], r["category"], r["price"])
        out_map[key] = out_map.get(key, 0) - (r["qty"] or 0)

    # ── Зарезервовано (issued + received) ────────────────────────────
    reserved_map = {}

    # Накладні issued + received
    for r in conn.execute("""
        SELECT ii.item_id, ii.category, ii.price,
               SUM(COALESCE(ii.actual_qty, ii.planned_qty)) AS qty
        FROM invoice_items ii
        JOIN invoices i ON ii.invoice_id = i.id
        WHERE i.direction='issue' AND i.status IN ('issued', 'received')
          AND (? IS NULL OR ii.invoice_id != ?)
        GROUP BY ii.item_id, ii.category, ii.price
    """, (exclude_invoice_id, exclude_invoice_id)).fetchall():
        key = (r["item_id"], r["category"], r["price"])
        reserved_map[key] = reserved_map.get(key, 0) + (r["qty"] or 0)

    # РВ active (= видача в процесі — резервуємо майно, аналог issued для накладних)
    for r in conn.execute("""
        SELECT dsq.item_id,
               dsi.category, dsi.price,
               SUM(COALESCE(dsq.actual_qty, dsq.quantity)) AS qty
        FROM distribution_sheet_quantities dsq
        JOIN distribution_sheet_items dsi ON dsi.sheet_id = dsq.sheet_id
                                          AND dsi.item_id = dsq.item_id
        JOIN distribution_sheets ds ON ds.id = dsq.sheet_id
        WHERE ds.direction='issue' AND ds.status='active'
          AND (? IS NULL OR dsq.sheet_id != ?)
        GROUP BY dsq.item_id, dsi.category, dsi.price
    """, (exclude_sheet_id, exclude_sheet_id)).fetchall():
        key = (r["item_id"], r["category"], r["price"])
        reserved_map[key] = reserved_map.get(key, 0) + (r["qty"] or 0)

    # ── Pending (assigned) — тільки попередження ─────────────────────
    pending_map = {}

    # Накладні assigned
    for r in conn.execute("""
        SELECT ii.item_id, ii.category, ii.price,
               SUM(ii.planned_qty) AS qty
        FROM invoice_items ii
        JOIN invoices i ON ii.invoice_id = i.id
        WHERE i.direction='issue' AND i.status='assigned'
          AND (? IS NULL OR ii.invoice_id != ?)
        GROUP BY ii.item_id, ii.category, ii.price
    """, (exclude_invoice_id, exclude_invoice_id)).fetchall():
        key = (r["item_id"], r["category"], r["price"])
        pending_map[key] = pending_map.get(key, 0) + (r["qty"] or 0)

    # РВ draft (чернетка — слабке попередження, ще не підтверджено)
    for r in conn.execute("""
        SELECT dsq.item_id,
               dsi.category, dsi.price,
               SUM(dsq.quantity) AS qty
        FROM distribution_sheet_quantities dsq
        JOIN distribution_sheet_items dsi ON dsi.sheet_id = dsq.sheet_id
                                          AND dsi.item_id = dsq.item_id
        JOIN distribution_sheets ds ON ds.id = dsq.sheet_id
        WHERE ds.direction='issue' AND ds.status='draft'
          AND (? IS NULL OR dsq.sheet_id != ?)
        GROUP BY dsq.item_id, dsi.category, dsi.price
    """, (exclude_sheet_id, exclude_sheet_id)).fetchall():
        key = (r["item_id"], r["category"], r["price"])
        pending_map[key] = pending_map.get(key, 0) + (r["qty"] or 0)

    # ── Збираємо результат ───────────────────────────────────────────
    result = []
    for r in stock_rows:
        d = dict(r)
        key = (d["item_id"], d["category"], d["price"])

        qty_in       = d["qty_in"] or 0
        qty_out      = max(out_map.get(key, 0), 0)
        qty_reserved = max(reserved_map.get(key, 0), 0)
        qty_pending  = max(pending_map.get(key, 0), 0)
        qty_free     = round(qty_in - qty_out - qty_reserved, 4)

        d["qty_out"]      = qty_out
        d["qty_reserved"] = qty_reserved
        d["qty_pending"]  = qty_pending
        d["qty_free"]     = qty_free
        d["qty_balance"]  = qty_free   # сумісність
        d["balance"]      = qty_free   # сумісність
        d["has_warning"]  = qty_free <= 0 and (qty_free + qty_pending) > 0
        d["total_sum"]    = round(qty_free * d["price"], 2)

        if only_positive and qty_free <= 0:
            continue
        if only_available and qty_free <= 0 and not d["has_warning"]:
            continue
        result.append(d)
    return result
