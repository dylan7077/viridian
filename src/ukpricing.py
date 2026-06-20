"""Real UK (GBP) prices from eBay's Browse API — live ebay.co.uk listings.

eBay GB listings are the closest real signal for what a UK buyer pays, raw or graded, in
actual GBP — replacing the flat USD×0.79 FX guess. OAuth2 client-credentials (app token,
cached ~2h) + Browse API on the GB marketplace.

ponytail: active-listing median, not sold-median — Marketplace Insights (sold) needs eBay
approval. Asking prices sit a little above sold; fine for a ballpark, swap in Insights if
you get access.

Needs EBAY_CLIENT_ID + EBAY_CLIENT_SECRET in .env (App ID + Cert ID from the eBay dev portal).
"""
from __future__ import annotations

import base64
import os
import statistics
import time
from functools import lru_cache
from typing import Optional

import requests

import config  # noqa: F401  (loads .env)

_CID = os.getenv("EBAY_CLIENT_ID", "")
_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
_OAUTH = "https://api.ebay.com/identity/v1/oauth2/token"
_BROWSE = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_tok = {"v": None, "exp": 0.0}


def available() -> bool:
    return bool(_CID and _SECRET)


def _token() -> Optional[str]:
    if not available():
        return None
    if _tok["v"] and time.time() < _tok["exp"]:
        return _tok["v"]
    try:
        auth = base64.b64encode(f"{_CID}:{_SECRET}".encode()).decode()
        r = requests.post(_OAUTH, timeout=12,
                          headers={"Authorization": f"Basic {auth}",
                                   "Content-Type": "application/x-www-form-urlencoded"},
                          data={"grant_type": "client_credentials",
                                "scope": "https://api.ebay.com/oauth/api_scope"})
        r.raise_for_status()
        d = r.json()
        _tok["v"] = d["access_token"]
        _tok["exp"] = time.time() + int(d.get("expires_in", 7200)) - 60
        return _tok["v"]
    except Exception:
        return None


@lru_cache(maxsize=512)
def _median_gbp(query: str) -> Optional[float]:
    tok = _token()
    if not tok:
        return None
    try:
        r = requests.get(_BROWSE, timeout=12,
                         headers={"Authorization": f"Bearer {tok}",
                                  "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"},
                         params={"q": query, "limit": 50,
                                 "filter": "buyingOptions:{FIXED_PRICE},itemLocationCountry:GB"})
        r.raise_for_status()
        items = r.json().get("itemSummaries") or []
        prices = [float(i["price"]["value"]) for i in items
                  if (i.get("price") or {}).get("currency") == "GBP" and i["price"].get("value")]
        return round(statistics.median(prices), 2) if prices else None
    except Exception:
        return None


def get_uk_price(name: str, number=None, grade=None) -> Optional[dict]:
    """Real GBP medians from eBay GB: {'raw','graded','source'} or None if unavailable."""
    if not available() or not name:
        return None
    num = f" {number}" if number else ""
    raw = _median_gbp(f"{name}{num} pokemon card")
    graded = _median_gbp(f"{name}{num} pokemon psa {grade}") if grade else None
    if raw is None and graded is None:
        return None
    return {"raw": raw, "graded": graded, "source": "eBay GB median (active)"}


if __name__ == "__main__":  # ponytail: smallest live check — needs creds in .env
    print("available:", available())
    print(get_uk_price("Charizard", "4", 10))
