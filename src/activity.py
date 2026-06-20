"""Shared 'recently graded' feed, written by both the web app and the Discord bot.

Every successful grade — whether it came through the website or the Discord bot — is
recorded here, so there is one place to see everything that has been checked. Backed by
the database in src.db (SQLite locally, Postgres in production); newest entries first.
"""
from __future__ import annotations

import time
from typing import Optional

from src import db


def _usd_entry(value: dict) -> dict:
    """Pull the USD price row out of an engine `value` dict, if present."""
    for v in (value or {}).get("values", []) or []:
        if v.get("currency") == "USD" or v.get("symbol") == "$":
            return v
    return {}


def summarize(source: str, result: dict) -> Optional[dict]:
    """Compact, UI-ready summary of a grade result, or None if it wasn't gradable."""
    if not result.get("ok"):
        return None
    grade = result.get("grade") or {}
    slab = result.get("slab")
    g = (slab.get("grade") if slab and slab.get("grade") is not None
         else grade.get("overall"))
    match = result.get("match") or {}
    card = match.get("card", {}) if match else {}
    usd = _usd_entry(result.get("value") or {})
    return {
        "ts": int(time.time()),
        "source": source,                       # "web" | "bot"
        "id": card.get("id"),                   # TCGdex id, e.g. "base1-4" (deep-link key)
        "name": card.get("name") or "Unidentified card",
        "set": card.get("set"),
        "number": card.get("number"),
        "grade": g,
        "slab": bool(slab),
        "image": card.get("image"),
        "raw": usd.get("raw"),
        "graded": usd.get("graded"),
    }


def _row_to_dict(r: "db.Grade") -> dict:
    return {
        "ts": r.ts, "source": r.source, "id": r.card_id, "name": r.name,
        "set": r.set_name, "number": r.number, "grade": r.grade, "slab": r.slab,
        "image": r.image, "raw": r.raw, "graded": r.graded,
    }


def record(source: str, result: dict) -> Optional[dict]:
    """Persist a grade to the shared feed. Never raises — logging must not break grading.

    Only *identified* cards are recorded; an unknown card (no match → no card id) has
    no name/image/value to show, so it's kept out of the public feed entirely."""
    entry = summarize(source, result)
    if entry is None or not entry.get("id"):
        return None
    try:
        with db.SessionLocal() as s:
            s.add(db.Grade(
                ts=entry["ts"], source=entry["source"], card_id=entry["id"],
                name=entry["name"], set_name=entry["set"], number=entry["number"],
                grade=entry["grade"], slab=entry["slab"], image=entry["image"],
                raw=entry["raw"], graded=entry["graded"],
            ))
            s.commit()
    except Exception:
        pass
    return entry


def recent(limit: int = 24) -> list[dict]:
    """Newest grades first."""
    try:
        from sqlalchemy import select
        with db.SessionLocal() as s:
            rows = s.scalars(
                select(db.Grade).order_by(db.Grade.ts.desc(), db.Grade.id.desc())
                .limit(max(0, limit))
            ).all()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


# ── Per-card scan statistics ────────────────────────────────────────────
# Every grade is one row, so "how many times has this card been scanned" and
# "what's its average grade" are plain aggregate queries over the grades table.

def _aggregate(rows) -> dict:
    """Shared roll-up over grade rows (each with .ts and .grade): total count,
    average grade, a continuous daily scans-over-time series (capped to the most
    recent 60 days), and a PSA 1..10 grade distribution. Bucketed in Python so it
    behaves identically on SQLite and Postgres (no DB-specific date functions)."""
    import datetime
    from collections import Counter

    grades = [r.grade for r in rows if r.grade is not None]
    dist = [0] * 10
    for g in grades:
        if 1 <= g <= 10:
            dist[g - 1] += 1

    by_day: Counter = Counter()
    for r in rows:
        by_day[datetime.datetime.utcfromtimestamp(r.ts).date()] += 1
    timeline = []
    if by_day:
        d, end = min(by_day), datetime.datetime.utcnow().date()
        while d <= end:
            timeline.append({"date": d.isoformat(), "count": by_day.get(d, 0)})
            d += datetime.timedelta(days=1)
        if len(timeline) > 60:
            timeline = timeline[-60:]

    return {
        "total": len(rows),
        "avg_grade": round(sum(grades) / len(grades), 1) if grades else None,
        "timeline": timeline,
        "distribution": dist,
    }


def overview() -> dict:
    """Site-wide scan stats across every recorded grade: total, average grade,
    scans-over-time and grade distribution (powers the activity-page graphs)."""
    try:
        from sqlalchemy import select
        with db.SessionLocal() as s:
            rows = s.execute(
                select(db.Grade.ts, db.Grade.grade)
                .where(db.Grade.card_id.is_not(None))
                .order_by(db.Grade.ts.asc())
            ).all()
    except Exception:
        return {"ok": False}
    return {"ok": True, **_aggregate(rows)}
