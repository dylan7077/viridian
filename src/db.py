"""Persistent storage for the grades/activity feed.

One SQLAlchemy engine, two deployments:

  * **Local / dev** — defaults to a SQLite file at ``data/viridian.db`` (WAL mode so
    the web app and the Discord bot can write concurrently). A real transactional DB
    that survives restarts, unlike the old JSON file.
  * **Production** — set ``DATABASE_URL`` to a managed Postgres URL (Supabase / Neon /
    Railway, etc.) and nothing else changes. Data then lives off-box and survives
    redeploys.

The card *catalog* (``index.json``) stays a bundled file — it's static reference data
and doesn't belong in the application database.
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config


def _url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{config.DATA_DIR / 'viridian.db'}"
    # Some hosts hand out the legacy "postgres://" scheme SQLAlchemy no longer accepts.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = _url()
_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class Grade(Base):
    """One graded card, from either the website or the Discord bot."""
    __tablename__ = "grades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(16))            # "web" | "bot"
    card_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    set_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    grade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    slab: Mapped[bool] = mapped_column(Boolean, default=False)
    image: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    graded: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class Share(Base):
    """A snapshot of one grade result, addressable by a short token for sharing.

    Stores the slim render payload as JSON so /g/<token> can re-render the card,
    grade and value without re-running the grader."""
    __tablename__ = "shares"

    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[int] = mapped_column(Integer, index=True)
    data: Mapped[str] = mapped_column(Text)


def init_db() -> None:
    """Create tables (idempotent), enable SQLite WAL, and migrate any legacy JSON feed."""
    Base.metadata.create_all(engine)
    if _is_sqlite:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    _migrate_legacy_json()


def _migrate_legacy_json() -> None:
    """One-time import of an existing data/activity.json so we don't lose the old feed."""
    path = config.DATA_DIR / "activity.json"
    if not path.exists():
        return
    import json
    try:
        items = json.loads(path.read_text())
        if not isinstance(items, list) or not items:
            return
    except Exception:
        return
    with SessionLocal() as s:
        if s.scalar(select(func.count()).select_from(Grade)):
            return                                   # already have data — don't double-import
        for it in reversed(items):                   # oldest first so ids ascend with time
            s.add(Grade(
                ts=it.get("ts", 0), source=it.get("source", "web"),
                card_id=it.get("id"), name=it.get("name") or "Unidentified card",
                set_name=it.get("set"), number=it.get("number"), grade=it.get("grade"),
                slab=bool(it.get("slab")), image=it.get("image"),
                raw=it.get("raw"), graded=it.get("graded"),
            ))
        s.commit()
    # Keep the JSON as a backup so nothing is destroyed by the migration.
    try:
        path.rename(path.with_suffix(".json.migrated"))
    except Exception:
        pass
