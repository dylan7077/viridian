"""Shareable grade snapshots.

When a card is confidently identified, we persist a compact snapshot of the result
under a short token. ``/g/<token>`` re-renders it (card + grade + value) with rich
OpenGraph tags, so pasting the link into Discord, iMessage, X, etc. unfurls a preview —
the viral loop for a "grade your card" tool.

Only *identified* grades are shareable: an unknown card has no card image / value to
show, and nothing worth sharing.
"""
from __future__ import annotations

import json
import secrets
import time
from typing import Optional

from src import db


def _slim(result: dict) -> dict:
    """The minimal payload needed to re-render a grade on the share page."""
    grade = result.get("grade") or {}
    slab = result.get("slab") or {}
    match = result.get("match") or {}
    card = match.get("card") or {}
    slabbed = slab.get("grade") is not None
    overall = slab.get("grade") if slabbed else grade.get("overall")
    c = grade.get("centering") or {}
    val = result.get("value") or {}
    values = []
    for v in (val.get("values") or []):
        if v.get("raw") is None and v.get("graded") is None:
            continue
        values.append({
            "currency": v.get("currency"), "symbol": v.get("symbol"),
            "raw": v.get("raw"), "graded": v.get("graded"),
        })
    return {
        "ts": int(time.time()),
        "id": card.get("id"),
        "name": card.get("name"),
        "set": card.get("set"),
        "number": card.get("number"),
        "rarity": card.get("rarity"),
        "image": card.get("image"),
        "grade": overall,
        "slab": slabbed,
        "cert": slab.get("cert"),
        "centering": {
            "grade": c.get("grade"), "ok": bool(c.get("ok")),
            "left_right": c.get("left_right"), "top_bottom": c.get("top_bottom"),
        },
        "corners": (grade.get("corners") or {}).get("grade"),
        "edges": (grade.get("edges") or {}).get("grade"),
        "surface": (grade.get("surface") or {}).get("grade"),
        "values": values,
        "graded_real": val.get("graded_real") is True,
    }


def create(result: dict) -> Optional[str]:
    """Persist a shareable snapshot and return its token, or None if not shareable.

    Never raises — sharing is a nicety and must not break grading."""
    if not result.get("ok"):
        return None
    card = (result.get("match") or {}).get("card") or {}
    if not card.get("id") or not card.get("image"):
        return None
    payload = _slim(result)
    token = secrets.token_urlsafe(9)              # ~12 url-safe chars
    try:
        with db.SessionLocal() as s:
            s.add(db.Share(token=token, ts=payload["ts"], data=json.dumps(payload)))
            s.commit()
    except Exception:
        return None
    return token


def get(token: str) -> Optional[dict]:
    """Return a stored snapshot (with its token attached), or None."""
    try:
        with db.SessionLocal() as s:
            row = s.get(db.Share, token)
            if not row:
                return None
            data = json.loads(row.data)
            data["token"] = token
            return data
    except Exception:
        return None
