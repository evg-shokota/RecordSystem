"""
core/military_logic.py — бізнес-логіка розрахунку строків носки та планування
Наказ МОУ №232 від 29.04.2016, Інструкція (z0768-16).

Типи служби:
  mobilized — мобілізований (кат. 5): строк від дати першої видачі циклу,
              борг згорає по закінченні циклу.
  contract  — контрактник (кат. 1-4): строк від дати норми (зарахування),
              борг не згорає.

Author: White
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Optional


# ── Допоміжні ────────────────────────────────────────────────────────────────

def wear_years_to_months(wear_years) -> int:
    """Конвертує строк носіння з років у місяці. 0 → 0 (до зносу)."""
    return int(round((wear_years or 0) * 12))


def _add_months(d: date, months: int) -> date:
    """Додає місяці до дати (враховує різну кількість днів у місяцях)."""
    month = d.month - 1 + months
    year  = d.year + month // 12
    month = month % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _parse_date(val) -> Optional[date]:
    """Перетворює рядок ISO або date у date. Повертає None при помилці."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except (ValueError, TypeError):
        return None


# ── Основні функції ───────────────────────────────────────────────────────────

def get_next_issue_date(
    service_type: str,
    cycle_start_date,        # дата першої видачі поточного циклу
    norm_date,               # дата зарахування на норму (для контрактників)
    wear_months: int,
    today: Optional[date] = None,
) -> Optional[date]:
    """
    Розраховує дату наступної видачі по позиції.

    Мобілізований:
        next = cycle_start_date + wear_months
    Контрактник:
        next = norm_date + N * wear_months  (перший цикл що ще не настав)

    Returns: date або None якщо недостатньо даних.
    """
    if not wear_months or wear_months <= 0:
        return None

    today_dt = today or date.today()

    if service_type == "contract":
        base = _parse_date(norm_date)
        if not base:
            return None
        # Знаходимо перший майбутній або поточний цикл
        next_dt = base
        while next_dt <= today_dt:
            next_dt = _add_months(next_dt, wear_months)
        return next_dt
    else:
        # mobilized — від дати першої видачі циклу
        base = _parse_date(cycle_start_date)
        if not base:
            return None
        return _add_months(base, wear_months)


def is_cycle_active(
    service_type: str,
    cycle_start_date,
    norm_date,
    wear_months: int,
    today: Optional[date] = None,
) -> bool:
    """
    Чи активний поточний цикл (строк носки ще не минув).

    Мобілізований: активний якщо cycle_start_date + wear > today
    Контрактник:   завжди активний (строк іде незалежно від видачі),
                   але перевіряємо що не вийшли за межі поточного інтервалу.
    """
    today = today or date.today()
    if not wear_months or wear_months <= 0:
        return True  # без строку — до зносу

    next_dt = get_next_issue_date(service_type, cycle_start_date, norm_date, wear_months)
    if next_dt is None:
        return False
    return next_dt > today


def get_cycle_status(
    service_type: str,
    cycle_start_date,
    norm_date,
    wear_months: int,
    issued_qty: float,
    norm_qty: float,
    today: Optional[date] = None,
) -> dict:
    """
    Повний статус циклу по одній позиції однієї особи.

    Returns dict:
        status:        'ok' | 'partial' | 'overdue' | 'expired' | 'not_issued'
        next_issue_date: date | None
        days_left:     int | None  (від'ємний = прострочено)
        debt_qty:      float  (для контрактника — борг; для мобілізованого — 0 якщо цикл минув)
        cycle_active:  bool
        color:         'green' | 'yellow' | 'orange' | 'red' | 'grey'
    """
    today = today or date.today()
    issued_qty = float(issued_qty or 0)
    norm_qty   = float(norm_qty or 0)

    next_dt   = get_next_issue_date(service_type, cycle_start_date, norm_date, wear_months)
    active    = is_cycle_active(service_type, cycle_start_date, norm_date, wear_months, today)
    days_left = (next_dt - today).days if next_dt else None

    # Борг
    raw_debt = norm_qty - issued_qty
    if service_type == "contract":
        debt_qty = max(0.0, raw_debt)  # борг зберігається завжди
    else:
        # мобілізований: борг тільки якщо цикл ще активний
        debt_qty = max(0.0, raw_debt) if active else 0.0

    # Статус і колір
    if norm_qty <= 0:
        status, color = "ok", "grey"
    elif issued_qty <= 0:
        if not _parse_date(cycle_start_date) and not _parse_date(norm_date):
            status, color = "not_issued", "red"
        else:
            status, color = "not_issued", "red"
    elif debt_qty > 0:
        status, color = "partial", "orange"
    elif days_left is not None and days_left <= 0:
        status, color = "overdue", "yellow"
    elif days_left is not None and days_left <= 30:
        status, color = "ok", "yellow"
    else:
        status, color = "ok", "green"

    return {
        "status":          status,
        "next_issue_date": next_dt.isoformat() if next_dt else None,
        "days_left":       days_left,
        "debt_qty":        debt_qty,
        "cycle_active":    active,
        "color":           color,
    }


def calc_attestat_row(
    service_type: str,
    cycle_start_date,
    norm_date,
    wear_months: int,
    issued_qty: float,
    norm_qty: float,
    last_issue_date,
    today: Optional[date] = None,
) -> dict:
    """
    Визначає що виводити в рядку атестату по одній позиції.

    Правила:
      - Повна видача → кількість по нормі + дата наступного отримання
      - Неповна видача → кількість виданого + дата останньої видачі
      - Не видавалось → не виводити (att_show=False)

    Returns dict:
        att_show:       bool  — виводити в атестат
        att_qty:        float — кількість для атестату
        att_date:       str   — дата для колонки "Дата видачі" (ISO)
        att_date_label: str   — людський формат DD.MM.YYYY
        is_partial:     bool
    """
    today      = today or date.today()
    issued_qty = float(issued_qty or 0)
    norm_qty   = float(norm_qty or 0)
    last_dt    = _parse_date(last_issue_date)

    # Не видавалось — не виводимо
    if issued_qty <= 0 or last_dt is None:
        return {"att_show": False, "att_qty": 0, "att_date": None,
                "att_date_label": "", "is_partial": False}

    is_partial = norm_qty > 0 and issued_qty < norm_qty

    if is_partial:
        # Неповна видача: скільки реально видано + дата останньої видачі
        att_qty  = issued_qty
        att_date = last_dt
    else:
        # Повна видача: кількість по нормі + дата наступного отримання
        att_qty = norm_qty if norm_qty > 0 else issued_qty
        next_dt = get_next_issue_date(service_type, cycle_start_date, norm_date, wear_months)
        att_date = next_dt if next_dt else last_dt

    att_date_label = att_date.strftime("%d.%m.%Y") if att_date else ""

    return {
        "att_show":       True,
        "att_qty":        att_qty,
        "att_date":       att_date.isoformat() if att_date else None,
        "att_date_label": att_date_label,
        "is_partial":     is_partial,
    }


def get_debt_summary(
    service_type: str,
    norm_date,
    wear_months: int,
    issued_qty: float,
    norm_qty: float,
    today: Optional[date] = None,
) -> dict:
    """
    Підсумок боргу для контрактника.
    Для мобілізованих повертає debt_qty=0.

    Returns dict:
        debt_qty:    float
        debt_cycles: int    — скільки повних циклів не отримав
        since_date:  str    — від якої дати рахується борг
    """
    today      = today or date.today()
    issued_qty = float(issued_qty or 0)
    norm_qty   = float(norm_qty or 0)

    if service_type != "contract" or norm_qty <= 0 or wear_months <= 0:
        return {"debt_qty": 0.0, "debt_cycles": 0, "since_date": None}

    base = _parse_date(norm_date)
    if not base:
        return {"debt_qty": 0.0, "debt_cycles": 0, "since_date": None}

    # Скільки повних циклів пройшло
    cycles = 0
    dt = base
    while _add_months(dt, wear_months) <= today:
        dt = _add_months(dt, wear_months)
        cycles += 1

    total_should_have = norm_qty * (cycles + 1) if cycles > 0 else norm_qty
    debt = max(0.0, total_should_have - issued_qty)

    return {
        "debt_qty":    debt,
        "debt_cycles": cycles,
        "since_date":  base.isoformat(),
    }
