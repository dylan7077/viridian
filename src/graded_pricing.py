"""Real PSA-graded prices from pokemon-tcg-api (RapidAPI / tcggo).

Covers ~2009+ sets. We look up the identified card by name, match on the TCGdex
id (`tcgid`) or set+number, and read the PSA grade's eBay sold-median (USD, the
gold standard for graded value) and Cardmarket graded (EUR) price. Used to
override the rough raw×multiplier graded estimate when real data exists; vintage
cards not covered here simply fall back to the existing estimate.
"""
from __future__ import annotations

import os
from functools import lru_cache

import requests

import config  # noqa: F401  (ensures .env is loaded)

_HOST = "pokemon-tcg-api.p.rapidapi.com"
_API = f"https://{_HOST}"
_KEY = os.getenv("POKEMON_API_KEY", "")


def _headers():
    return {"x-rapidapi-host": _HOST, "x-rapidapi-key": _KEY}


@lru_cache(maxsize=512)
def _search(name: str) -> tuple:
    try:
        r = requests.get(f"{_API}/cards", params={"name": name, "limit": 50},
                         headers=_headers(), timeout=12)
        r.raise_for_status()
        d = r.json()
        data = d.get("data", []) if isinstance(d, dict) else d
        return tuple(data or ())
    except Exception:
        return ()


def _pick(cards, tcgid, number, set_name):
    if tcgid:
        for c in cards:
            if c.get("tcgid") == tcgid:
                return c
    setl = (set_name or "").lower()
    num = str(number)
    for c in cards:
        if str(c.get("card_number")) == num:
            ep = ((c.get("episode") or {}).get("name") or "").lower()
            if setl and (setl in ep or ep in setl):
                return c
    return None


def get_graded(name, number, tcgid, set_name, grade):
    """Return {'usd','eur','source'} for the PSA grade, or None if unavailable."""
    if not _KEY or not name or grade is None:
        return None
    card = _pick(_search(name), tcgid, number, set_name)
    if not card:
        return None
    prices = card.get("prices") or {}
    usd = eur = source = None

    eb = ((prices.get("ebay") or {}).get("graded") or {}).get("psa") or {}
    slot = eb.get(str(grade)) or {}
    if slot.get("median_price"):
        usd = float(slot["median_price"])
        n = slot.get("sample_size")
        source = f"eBay PSA {grade} sold median" + (f" (n={n})" if n else "")

    cm = ((prices.get("cardmarket") or {}).get("graded") or {}).get("psa") or {}
    if cm.get(f"psa{grade}"):
        eur = float(cm[f"psa{grade}"])
        source = source or f"Cardmarket PSA {grade}"

    if usd is None and eur is None:
        return None
    return {"usd": usd, "eur": eur, "source": source}
