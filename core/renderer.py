"""
core/renderer.py — єдина система рендеру документів із шаблонів
Author: White

Відповідає за:
  1. Заміну шорткодів в HTML шаблону реальними даними (або демоданими)
  2. Побудову context-словника для кожного типу документа
  3. Рендер шаблону → готовий HTML для друку/перегляду

Використання:
  from core.renderer import render_doc, render_demo

  # Реальний документ
  html = render_doc("invoice", invoice_data, conn)
  html = render_doc("rv", rv_data, conn)

  # Демоперегляд (для editor.html preview)
  html = render_demo(raw_html)
"""
import json
import re
from datetime import date
from core.settings import get_setting, get_all_settings


# ─────────────────────────────────────────────────────────────
#  ЧИСЛА ПРОПИСОМ (гривні)
# ─────────────────────────────────────────────────────────────

_ONES = ["", "один", "два", "три", "чотири", "п'ять", "шість", "сім", "вісім", "дев'ять",
         "десять", "одинадцять", "дванадцять", "тринадцять", "чотирнадцять", "п'ятнадцять",
         "шістнадцять", "сімнадцять", "вісімнадцять", "дев'ятнадцять"]
_TENS = ["", "", "двадцять", "тридцять", "сорок", "п'ятдесят",
         "шістдесят", "сімдесят", "вісімдесят", "дев'яносто"]
_HUND = ["", "сто", "двісті", "триста", "чотириста", "п'ятсот",
         "шістсот", "сімсот", "вісімсот", "дев'ятсот"]


def _hundreds(n: int) -> str:
    parts = []
    h = n // 100
    r = n % 100
    if h:
        parts.append(_HUND[h])
    if r < 20:
        if r:
            parts.append(_ONES[r])
    else:
        parts.append(_TENS[r // 10])
        if r % 10:
            parts.append(_ONES[r % 10])
    return " ".join(parts)


def amount_words(amount: float) -> str:
    """Сума прописом: 'три тисячі п'ятсот п'ятдесят гривень 00 копійок'."""
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return ""
    kopecks = round((amount - int(amount)) * 100)
    hryvnias = int(amount)

    if hryvnias == 0:
        return f"нуль гривень {kopecks:02d} копійок"

    billions  = hryvnias // 1_000_000_000
    millions  = (hryvnias % 1_000_000_000) // 1_000_000
    thousands = (hryvnias % 1_000_000) // 1_000
    remainder = hryvnias % 1_000

    parts = []
    if billions:
        parts.append(_hundreds(billions))
        last2 = billions % 100
        last1 = billions % 10
        if 11 <= last2 <= 19:
            parts.append("мільярдів")
        elif last1 == 1:
            parts.append("мільярд")
        elif 2 <= last1 <= 4:
            parts.append("мільярди")
        else:
            parts.append("мільярдів")

    if millions:
        parts.append(_hundreds(millions))
        last2 = millions % 100
        last1 = millions % 10
        if 11 <= last2 <= 19:
            parts.append("мільйонів")
        elif last1 == 1:
            parts.append("мільйон")
        elif 2 <= last1 <= 4:
            parts.append("мільйони")
        else:
            parts.append("мільйонів")

    if thousands:
        # тисяча — жіночий рід: одна/дві
        th_str = _hundreds(thousands)
        th_str = th_str.replace("один ", "одна ").replace("два ", "дві ")
        if th_str.endswith("один"):
            th_str = th_str[:-4] + "одна"
        elif th_str.endswith("два"):
            th_str = th_str[:-3] + "дві"
        parts.append(th_str)
        last2 = thousands % 100
        last1 = thousands % 10
        if 11 <= last2 <= 19:
            parts.append("тисяч")
        elif last1 == 1:
            parts.append("тисяча")
        elif 2 <= last1 <= 4:
            parts.append("тисячі")
        else:
            parts.append("тисяч")

    if remainder:
        parts.append(_hundreds(remainder))

    # відмінювання гривень
    last2 = hryvnias % 100
    last1 = hryvnias % 10
    if 11 <= last2 <= 19:
        parts.append("гривень")
    elif last1 == 1:
        parts.append("гривня")
    elif 2 <= last1 <= 4:
        parts.append("гривні")
    else:
        parts.append("гривень")

    parts.append(f"{kopecks:02d} копійок")
    return " ".join(p for p in parts if p)


# ─────────────────────────────────────────────────────────────
#  МІСЯЦІ
# ─────────────────────────────────────────────────────────────

MONTH_NAMES = [
    "", "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"
]


def date_full(d: date) -> str:
    """'5 березня 2026 р.'"""
    return f"{d.day} {MONTH_NAMES[d.month]} {d.year} р."


# ─────────────────────────────────────────────────────────────
#  БАЗОВИЙ КОНТЕКСТ (з налаштувань системи)
# ─────────────────────────────────────────────────────────────

def _base_context() -> dict:
    """Спільний контекст для всіх типів документів."""
    s = get_all_settings()
    today = date.today()
    return {
        "unit_name":      s.get("company_name", ""),
        "service_name":   s.get("service_name", ""),
        "chief_name":     s.get("chief_name", ""),
        "chief_rank":     s.get("chief_rank", ""),
        "chief_tvo":      "ТВО " if s.get("chief_is_tvo") == "1" else "",
        "warehouse_name": s.get("warehouse_chief_name", ""),
        "warehouse_rank": s.get("warehouse_chief_rank", ""),
        "clerk_name":     s.get("clerk_name", ""),
        "clerk_rank":     s.get("clerk_rank", ""),
        "current_year":   str(today.year),
        "doc_date_full":  date_full(today),
    }


# ─────────────────────────────────────────────────────────────
#  КОНТЕКСТ ДЛЯ НАКЛАДНОЇ
# ─────────────────────────────────────────────────────────────

def _invoice_context(data: dict, conn) -> dict:
    """
    data: dict з полями накладної (invoice row + items list).
    Ключі: invoice, items (list of dicts), recipient (dict, optional)
    """
    ctx = _base_context()
    inv = data.get("invoice", {})
    items = data.get("items", [])

    # Дата документа
    doc_date = inv.get("doc_date") or inv.get("created_at", "")
    if doc_date:
        try:
            d = date.fromisoformat(str(doc_date)[:10])
            ctx["invoice_date"]    = d.strftime("%d.%m.%Y")
            ctx["doc_date_full"]   = date_full(d)
        except ValueError:
            ctx["invoice_date"] = str(doc_date)[:10]
    else:
        ctx["invoice_date"] = ""

    ctx["invoice_number"]  = inv.get("number") or "б/н"
    ctx["base_document"]   = inv.get("base_document") or ""
    ctx["valid_until"]     = inv.get("valid_until") or ""

    # Одержувач
    recipient_name = inv.get("recipient_name") or ""
    recipient_rank = inv.get("recipient_rank") or ""
    recipient_unit = inv.get("recipient_unit") or ""
    if not recipient_name:
        # спробуємо знайти по personnel_id
        pid = inv.get("personnel_id")
        if pid and conn:
            row = conn.execute(
                "SELECT last_name, first_name, middle_name, rank, unit_id FROM personnel WHERE id=?",
                (pid,)
            ).fetchone()
            if row:
                recipient_name = f"{row['last_name']} {row['first_name']} {row['middle_name'] or ''}".strip()
                recipient_rank = row["rank"] or ""
    ctx["recipient_name"] = recipient_name
    ctx["recipient_rank"] = recipient_rank
    ctx["recipient_unit"] = recipient_unit

    # Підписанти зі списку JSON
    signatories = inv.get("signatories", [])
    if isinstance(signatories, str):
        try:
            signatories = json.loads(signatories)
        except Exception:
            signatories = []
    for sig in signatories:
        tag = sig.get("tag", "")
        if tag == "chief":
            ctx["chief_name"] = sig.get("name", ctx["chief_name"])
            ctx["chief_rank"] = sig.get("rank", ctx["chief_rank"])
        elif tag == "warehouse":
            ctx["given_name"] = sig.get("name", "")
            ctx["given_rank"] = sig.get("rank", "")
            ctx["warehouse_name"] = sig.get("name", ctx["warehouse_name"])
            ctx["warehouse_rank"] = sig.get("rank", ctx["warehouse_rank"])
        elif tag == "clerk":
            ctx["clerk_name"] = sig.get("name", ctx["clerk_name"])
            ctx["clerk_rank"] = sig.get("rank", ctx["clerk_rank"])
    # received = одержувач
    ctx.setdefault("given_name",    ctx.get("warehouse_name", ""))
    ctx.setdefault("given_rank",    ctx.get("warehouse_rank", ""))
    ctx["received_name"] = recipient_name
    ctx["received_rank"] = recipient_rank

    # Таблиця позицій
    total = 0.0
    rows_html = ""
    for i, it in enumerate(items, 1):
        qty = float(it.get("actual_qty") or it.get("planned_qty") or 0)
        price = float(it.get("price") or 0)
        summ = round(qty * price, 2)
        total += summ
        serial = it.get("serial_numbers") or ""
        name_cell = it.get("item_name") or it.get("name") or ""
        if serial:
            name_cell += f"<br><small>{serial}</small>"
        rows_html += (
            f'<tr><td style="text-align:center">{i}</td>'
            f'<td>{name_cell}</td>'
            f'<td style="text-align:center">{it.get("category","")}</td>'
            f'<td style="text-align:center">{it.get("unit_of_measure","шт")}</td>'
            f'<td style="text-align:center">{_fmt_qty(qty)}</td>'
            f'<td style="text-align:right">{_fmt_money(price)}</td>'
            f'<td style="text-align:right">{_fmt_money(summ)}</td></tr>'
        )

    ctx["total_sum"]       = _fmt_money(total)
    ctx["total_sum_words"] = amount_words(total)
    ctx["table:items_list"] = (
        '<table border="1" cellpadding="4" cellspacing="0" '
        'style="width:100%;border-collapse:collapse;font-size:inherit">'
        '<tr style="background:#f0f0f0;font-weight:bold">'
        '<th style="width:30px">№</th><th>Найменування</th><th style="width:40px">Кат.</th>'
        '<th style="width:40px">Од.</th><th style="width:55px">Кількість</th>'
        '<th style="width:80px">Ціна, грн</th><th style="width:80px">Сума, грн</th></tr>'
        + rows_html
        + f'<tr><td colspan="6" style="text-align:right;font-weight:bold">Разом:</td>'
        f'<td style="text-align:right;font-weight:bold">{_fmt_money(total)}</td></tr>'
        '</table>'
    )

    # Блок підписантів
    ctx["table:signatories"] = _signatories_block(signatories, ctx, recipient_name, recipient_rank)

    return ctx


# ─────────────────────────────────────────────────────────────
#  КОНТЕКСТ ДЛЯ РОЗДАВАЛЬНОЇ ВІДОМОСТІ
# ─────────────────────────────────────────────────────────────

def _rv_context(data: dict, conn) -> dict:
    """
    data: dict з полями РВ (sheet row + matrix).
    Ключі: sheet (dict), items (list), rows (list), qty (dict {(row_id, item_id): qty})
    """
    ctx = _base_context()
    sheet = data.get("sheet", {})

    doc_date_raw = sheet.get("doc_date") or sheet.get("created_at", "")
    if doc_date_raw:
        try:
            d = date.fromisoformat(str(doc_date_raw)[:10])
            ctx["invoice_date"]  = d.strftime("%d.%m.%Y")
            ctx["doc_date_full"] = date_full(d)
        except ValueError:
            ctx["invoice_date"] = str(doc_date_raw)[:10]
    else:
        ctx["invoice_date"] = ""

    ctx["invoice_number"] = sheet.get("number") or "б/н"
    ctx["base_document"]  = sheet.get("base_document") or ""

    # Підписанти РВ — плоскі поля
    ctx["given_name"]     = sheet.get("given_by_name") or ctx.get("warehouse_name", "")
    ctx["given_rank"]     = sheet.get("given_by_rank") or ctx.get("warehouse_rank", "")
    ctx["received_name"]  = sheet.get("received_by_name") or ""
    ctx["received_rank"]  = sheet.get("received_by_rank") or ""
    if sheet.get("chief_name"):
        ctx["chief_name"] = sheet["chief_name"]
    if sheet.get("chief_rank"):
        ctx["chief_rank"] = sheet["chief_rank"]
    if sheet.get("clerk_name"):
        ctx["clerk_name"] = sheet["clerk_name"]
    if sheet.get("clerk_rank"):
        ctx["clerk_rank"] = sheet["clerk_rank"]

    # Таблиця матриці
    items = data.get("items", [])
    rows  = data.get("rows", [])
    qty   = data.get("qty", {})

    col_headers = "".join(
        f'<th style="writing-mode:vertical-rl;min-width:30px;padding:2px 4px">'
        f'{it.get("item_name","")}<br><small>{it.get("unit","шт")}</small></th>'
        for it in items
    )

    table_rows = ""
    for i, row in enumerate(rows, 1):
        person = f'{row.get("rank","")} {row.get("full_name","")}'.strip()
        cells = ""
        for it in items:
            q = qty.get((row["id"], it["id"]), 0)
            received = row.get("received", 0)
            cell_style = "text-align:center;background:#e8f5e9" if received else "text-align:center"
            cells += f'<td style="{cell_style}">{_fmt_qty(q) if q else ""}</td>'
        table_rows += f"<tr><td>{i}</td><td>{person}</td>{cells}</tr>"

    ctx["table:items_list"] = (
        '<table border="1" cellpadding="3" cellspacing="0" '
        'style="width:100%;border-collapse:collapse;font-size:inherit">'
        f'<tr style="background:#f0f0f0;font-weight:bold">'
        f'<th style="width:25px">№</th><th>Прізвище, ім\'я, по батькові</th>'
        f'{col_headers}</tr>'
        + table_rows
        + '</table>'
    )

    sigs_raw = [
        {"role": "Начальник речової служби", "rank": ctx["chief_rank"], "name": ctx["chief_name"]},
        {"role": "Здав",                     "rank": ctx["given_rank"], "name": ctx["given_name"]},
        {"role": "Прийняв",                  "rank": ctx["received_rank"], "name": ctx["received_name"]},
        {"role": "Діловод РС",               "rank": ctx["clerk_rank"], "name": ctx["clerk_name"]},
    ]
    ctx["table:signatories"] = _signatories_block(sigs_raw, ctx)

    return ctx


# ─────────────────────────────────────────────────────────────
#  ДЕМО-КОНТЕКСТ (для попереднього перегляду шаблонів)
# ─────────────────────────────────────────────────────────────

def _demo_context() -> dict:
    ctx = _base_context()
    today = date.today()

    ctx.update({
        "invoice_number":  "2026/1/РС",
        "invoice_date":    today.strftime("%d.%m.%Y"),
        "doc_date_full":   date_full(today),
        "base_document":   "Наказ командира № 42 від 01.03.2026",
        "total_sum":       "3 550,00",
        "total_sum_words": "три тисячі п'ятсот п'ятдесят гривень 00 копійок",
        "valid_until":     today.strftime("%d.%m.%Y"),
        "recipient_name":  "Петренко Петро Петрович",
        "recipient_rank":  "рядовий",
        "recipient_unit":  "1 рота 1 батальйону",
        "given_name":      ctx.get("warehouse_name") or "Коваль К.К.",
        "given_rank":      ctx.get("warehouse_rank") or "сержант",
        "received_name":   "Петренко П.П.",
        "received_rank":   "рядовий",
    })
    ctx.setdefault("chief_name",     "Іваненко І.І.")
    ctx.setdefault("chief_rank",     "капітан")
    ctx.setdefault("warehouse_name", "Коваль К.К.")
    ctx.setdefault("warehouse_rank", "сержант")
    ctx.setdefault("clerk_name",     "Бондар Б.Б.")
    ctx.setdefault("clerk_rank",     "ст. солдат")
    ctx.setdefault("unit_name",      "")
    ctx.setdefault("service_name",   "")

    ctx["table:items_list"] = (
        '<table border="1" cellpadding="4" cellspacing="0" style="width:100%;border-collapse:collapse">'
        '<tr style="background:#f0f0f0"><th>№</th><th>Найменування</th><th>Кат.</th>'
        '<th>Од.</th><th>Кількість</th><th>Ціна, грн</th><th>Сума, грн</th></tr>'
        '<tr><td>1</td><td>Берці літні</td><td>I</td><td>пара</td><td>1</td><td>2 500,00</td><td>2 500,00</td></tr>'
        '<tr><td>2</td><td>Футболка польова</td><td>II</td><td>шт</td><td>3</td><td>350,00</td><td>1 050,00</td></tr>'
        '<tr><td colspan="6" style="text-align:right"><strong>Разом:</strong></td>'
        '<td><strong>3 550,00</strong></td></tr>'
        '</table>'
    )
    ctx["table:signatories"] = (
        '<table style="width:100%">'
        f'<tr><td style="width:50%">{ctx["chief_rank"]} {ctx["chief_name"]}</td>'
        f'<td>Начальник речової служби</td></tr>'
        f'<tr><td>{ctx["warehouse_rank"]} {ctx["warehouse_name"]}</td><td>Здав</td></tr>'
        '<tr><td>рядовий Петренко П.П.</td><td>Прийняв</td></tr>'
        f'<tr><td>{ctx["clerk_rank"]} {ctx["clerk_name"]}</td><td>Діловод РС</td></tr>'
        '</table>'
    )
    return ctx


# ─────────────────────────────────────────────────────────────
#  ЗАМІНЮВАЧ ШОРТКОДІВ
# ─────────────────────────────────────────────────────────────

def _apply_context(html: str, ctx: dict) -> str:
    """Замінює всі {{key}} в html значеннями з ctx."""
    for key, value in ctx.items():
        html = html.replace("{{" + key + "}}", str(value) if value is not None else "")
    return html


# ─────────────────────────────────────────────────────────────
#  ПУБЛІЧНИЙ API
# ─────────────────────────────────────────────────────────────

def render_demo(html: str) -> str:
    """Рендер HTML із демоданими (для preview шаблонів)."""
    return _apply_context(html, _demo_context())


def render_doc(doc_type: str, data: dict, conn=None) -> str:
    """
    Рендер документа з реальними даними.

    doc_type: 'invoice' | 'rv' | ...
    data: dict з даними документа (залежить від типу)
    conn: з'єднання з БД (потрібне для деяких типів)
    """
    if doc_type == "invoice":
        ctx = _invoice_context(data, conn)
    elif doc_type == "rv":
        ctx = _rv_context(data, conn)
    else:
        # Для нереалізованих типів — базовий контекст
        ctx = _base_context()

    html = data.get("html", "")
    return _apply_context(html, ctx)


def get_template_html(conn, doc_type: str, tpl_id: int = None) -> tuple:
    """
    Отримати HTML шаблону і метадані.
    Якщо tpl_id=None — бере дефолтний для doc_type.
    Повертає (html, template_row) або (None, None) якщо не знайдено.
    """
    if tpl_id:
        t = conn.execute("SELECT * FROM doc_templates WHERE id=?", (tpl_id,)).fetchone()
    else:
        t = conn.execute(
            "SELECT * FROM doc_templates WHERE doc_type=? AND default_for_type=1 LIMIT 1",
            (doc_type,)
        ).fetchone()
    if not t:
        return None, None
    try:
        grid = json.loads(t["grid_data"] or "{}")
        return grid.get("html", ""), t
    except Exception:
        return "", t


# ─────────────────────────────────────────────────────────────
#  ДОПОМІЖНІ
# ─────────────────────────────────────────────────────────────

def _fmt_money(v) -> str:
    try:
        return f"{float(v):,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        return "0,00"


def _fmt_qty(v) -> str:
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(round(f, 3))
    except (TypeError, ValueError):
        return "0"


def _signatories_block(signatories: list, ctx: dict,
                       recipient_name: str = "", recipient_rank: str = "") -> str:
    """Блок підписантів у вигляді таблиці."""
    rows = []
    for sig in signatories:
        if isinstance(sig, dict):
            role = sig.get("role", "")
            rank = sig.get("rank", "")
            name = sig.get("name", "")
        else:
            continue
        if not name:
            continue
        rows.append(f'<tr><td style="width:50%;padding:4px 0">{rank} {name}</td>'
                    f'<td style="padding:4px 0">{role}</td></tr>')
    if not rows:
        # fallback — базові підписанти
        chief = f'{ctx.get("chief_rank","")} {ctx.get("chief_name","")}'.strip()
        if chief:
            rows.append(f'<tr><td style="width:50%;padding:4px 0">{chief}</td>'
                        f'<td style="padding:4px 0">Начальник речової служби</td></tr>')
    return f'<table style="width:100%">{"".join(rows)}</table>'
