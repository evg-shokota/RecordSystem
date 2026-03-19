"""
core/utils.py — Спільні утиліти (форматування, нумерація)

Author: White
"""
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3


def next_doc_number(
    conn: "sqlite3.Connection",
    doc_type: str,
    default_suffix: str,
) -> tuple[str, int, int, str]:
    """
    Повертає наступний унікальний номер документа для поточного року.

    Використовує таблицю doc_sequences (PRIMARY KEY doc_type + year),
    атомарно збільшує лічильник всередині відкритої транзакції.

    Args:
        conn:           відкрите з'єднання з БД (не commit-ить само по собі)
        doc_type:       тип документа ('invoice', 'rv', 'write_off', 'exploit_act', ...)
        default_suffix: суфікс за замовченням якщо налаштування не задано

    Returns:
        (number, year, sequence_num, suffix)
        number — готовий рядок виду "2025/1/РС"
    """
    from core.settings import get_setting

    year   = date.today().year
    suffix = get_setting(f"{doc_type}_suffix", default_suffix)

    row = conn.execute(
        "SELECT sequence, suffix FROM doc_sequences WHERE doc_type=? AND year=?",
        (doc_type, year),
    ).fetchone()

    if row:
        seq    = row["sequence"]
        suffix = row["suffix"] or suffix
        conn.execute(
            "UPDATE doc_sequences SET sequence=?, updated_at=datetime('now','localtime') "
            "WHERE doc_type=? AND year=?",
            (seq + 1, doc_type, year),
        )
    else:
        seq = 1
        conn.execute(
            "INSERT INTO doc_sequences (doc_type, year, sequence, suffix) VALUES (?,?,2,?)",
            (doc_type, year, suffix),
        )

    number = f"{year}/{seq}/{suffix}"
    return number, year, seq, suffix
