"""Look up a dollar value for a card via the TCGdex API (https://tcgdex.dev).

TCGdex returns fresh TCGplayer (USD) and Cardmarket (EUR) prices, and covers the
newest sets. Prices are *raw* (ungraded) market prices; a graded card is worth
more, so we apply a rough multiplier to estimate a graded value — a ballpark, not
an appraisal. The raw market price is real data; the graded estimate is heuristic.
"""
from __future__ import annotations

from typing import Optional

import requests

import config

# Rough premium of a PSA-graded copy over the raw market price.
GRADE_MULTIPLIER = {
    10: 5.0, 9: 2.2, 8: 1.5, 7: 1.2, 6: 1.0,
    5: 0.95, 4: 0.9, 3: 0.85, 2: 0.8, 1: 0.75,
}


# Approximate FX from USD; EUR is shown from real Cardmarket data when available.
FX_FROM_USD = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79}
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}


def _usd_market(pricing: dict) -> Optional[float]:
    tp = pricing.get("tcgplayer") or {}
    order = ["holofoil", "reverse-holofoil", "normal",
             "1st-edition-holofoil", "1st-edition"]
    skip = {"unit", "updated"}
    for v in order + [k for k in tp if k not in order and k not in skip]:
        d = tp.get(v)
        if isinstance(d, dict) and d.get("marketPrice"):
            return float(d["marketPrice"])
    return None


def _eur_market(pricing: dict) -> Optional[float]:
    cm = pricing.get("cardmarket") or {}
    # Prefer CURRENT market value over the all-time average. Cardmarket `avg` is the all-time
    # mean sell price, which badly understates appreciated vintage (base Charizard: avg €513
    # vs trend €687). `trend` is the current price; avg30/avg7 are recent; `avg` is the stale
    # last resort.
    for k in ("trend", "avg30", "avg7", "avg"):
        if cm.get(k):
            return float(cm[k])
    return None


def get_card_value(card_id: str, grade: Optional[int]) -> dict:
    """Pricing for a TCGdex card in USD/EUR/GBP, with graded estimates."""
    try:
        r = requests.get(f"{config.TCGDEX_API}/cards/{card_id}", timeout=15)
        r.raise_for_status()
        card = r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    pricing = card.get("pricing") or {}
    usd = _usd_market(pricing)
    eur = _eur_market(pricing)
    base_usd = usd if usd is not None else (eur / FX_FROM_USD["EUR"] if eur else None)

    image = card.get("image") or ""
    out = {
        "ok": True,
        "name": card.get("name"),
        "set": (card.get("set") or {}).get("name"),
        "number": card.get("localId"),
        "rarity": card.get("rarity"),
        "image": (image + "/high.png") if image else None,
        "values": [],
    }
    if base_usd is None:
        return out

    mult = GRADE_MULTIPLIER.get(grade) if grade is not None else None
    out["graded_multiplier"] = mult
    # Real GBP from eBay UK (active median) — replaces the ×0.79 FX guess when available.
    try:
        from src import ukpricing
        uk = ukpricing.get_uk_price(card.get("name"), card.get("localId"), grade)
    except Exception:
        uk = None
    for cur in ("USD", "EUR", "GBP"):
        raw = eur if (cur == "EUR" and eur is not None) else base_usd * FX_FROM_USD[cur]
        entry = {"currency": cur, "symbol": SYMBOL[cur], "raw": round(raw, 2)}
        if mult is not None:
            entry["graded"] = round(raw * mult, 2)
        if cur == "GBP" and uk:
            if uk.get("raw") is not None:
                entry["raw"] = uk["raw"]
            if uk.get("graded") is not None:
                entry["graded"] = uk["graded"]
            entry["source"] = uk["source"]
            out["uk_real"] = True
        out["values"].append(entry)
    return out


def search_card(name: str, number: Optional[str] = None) -> list[dict]:
    """Free-text search fallback when image matching is unavailable."""
    try:
        params = {"name": name}
        if number:
            params["localId"] = number
        r = requests.get(f"{config.TCGDEX_API}/cards", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []
