"""
modules/import_cards/matcher.py — fuzzy-матчинг назв майна з А5027 до item_dictionary

Порогові значення (зі spec):
  > 90%  — зелений (auto)       → score_class = "success"
  60–90% — жовтий (підтвердити) → score_class = "warning"
  < 60%  — червоний (вручну)    → score_class = "danger"
"""
from __future__ import annotations

from typing import Optional

try:
    from rapidfuzz import process as rfprocess, fuzz as rffuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False


def _score_class(score: Optional[float]) -> str:
    if score is None:
        return "danger"
    if score >= 90:
        return "success"
    if score >= 60:
        return "warning"
    return "danger"


def match_items(
    items: list[dict],
    dict_items: list[dict],
) -> list[dict]:
    """
    Для кожного предмета з картки знаходить найближчий збіг у item_dictionary.

    Параметри:
        items      — список dict з parser.py: [{"name_raw": ..., ...}, ...]
        dict_items — список dict: [{"id": int, "name": str}, ...]

    Повертає список dict (копії items з доданими полями):
        {
            ...оригінальні поля...,
            "match_id":    int|None,    # id в item_dictionary або None
            "match_name":  str|None,    # назва знайденого предмета
            "match_score": float|None,  # 0–100
            "score_class": str,         # "success"|"warning"|"danger"
        }
    """
    if not dict_items:
        return [
            {**it, "match_id": None, "match_name": None,
             "match_score": None, "score_class": "danger"}
            for it in items
        ]

    # Будуємо словник choices: {name: id}
    choices = {d["name"]: d["id"] for d in dict_items}
    choices_names = list(choices.keys())

    result = []
    for it in items:
        name_raw = (it.get("name_raw") or "").strip()
        if not name_raw:
            result.append({**it, "match_id": None, "match_name": None,
                           "match_score": None, "score_class": "danger"})
            continue

        if _HAS_RAPIDFUZZ:
            # token_sort_ratio краще для реордерованих слів ("кепі бойове" vs "бойове кепі")
            match = rfprocess.extractOne(
                name_raw,
                choices_names,
                scorer=rffuzz.token_sort_ratio,
                score_cutoff=0,
            )
            if match:
                best_name, score, _ = match
                best_id = choices[best_name]
            else:
                best_name, score, best_id = None, None, None
        else:
            # Запасний варіант без rapidfuzz: простий пошук підрядка
            best_name = None
            best_id   = None
            score     = None
            name_lower = name_raw.lower()
            for d_name, d_id in choices.items():
                if name_lower in d_name.lower() or d_name.lower() in name_lower:
                    best_name = d_name
                    best_id   = d_id
                    score     = 70.0  # умовний
                    break

        result.append({
            **it,
            "match_id":    best_id,
            "match_name":  best_name,
            "match_score": round(score, 1) if score is not None else None,
            "score_class": _score_class(score),
        })

    return result
