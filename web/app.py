"""Local web front-end for Viridian Grading Lab.

Run with:  uvicorn web.app:app --reload --port 8000
Then open: http://localhost:8000
"""
from __future__ import annotations

import asyncio
import base64
import collections
import html
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool

import config
from src import engine, pricing, activity, discord_webhook, db, share

STATIC = Path(__file__).resolve().parent / "static"
DEBUG_DIR = config.DATA_DIR / "debug"
DEBUG_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Viridian Grading Lab")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ── eBay Marketplace Account Deletion endpoint (required for Production keys) ──
# eBay verifies ownership with a hash challenge, then POSTs account-deletion notices.
# We store no eBay user data, so deletions are a no-op 200. URL + token must match the
# eBay developer portal exactly.
import hashlib
import os as _os

_EBAY_TOKEN = _os.getenv("EBAY_VERIFICATION_TOKEN", "")
_EBAY_ENDPOINT = "https://94.72.104.185.sslip.io/ebay/deletion"


@app.get("/ebay/deletion")
async def ebay_challenge(challenge_code: str = ""):
    h = hashlib.sha256((challenge_code + _EBAY_TOKEN + _EBAY_ENDPOINT).encode())
    return JSONResponse({"challengeResponse": h.hexdigest()})


@app.post("/ebay/deletion")
async def ebay_deletion(request: Request):
    return Response(status_code=200)


# ── Per-IP rate limiting ────────────────────────────────────────────────
# In-process sliding-window limiter — one uvicorn worker behind Caddy, so an
# in-memory store is sufficient (no Redis). Grading is the expensive, serialized,
# memory-heavy path, so it gets a strict budget; everything else under /api gets a
# generous flood-stop that still allows the 4s health poll + library browsing.
# Each rule is (window_seconds, max_requests); all rules in a bucket must pass.
_RL_RULES = {
    "grade": [(60, 6), (3600, 40)],
    # /api/detect runs the same heavy CV decode as grading, so it gets its own strict
    # budget — not the generous flood-stop the cheap JSON endpoints share.
    "detect": [(60, 20)],
    "api": [(60, 300)],
}
_rl_hits: "dict[tuple[str, str], collections.deque]" = collections.defaultdict(collections.deque)
_rl_last_sweep = [0.0]


def _client_ip(request: Request) -> str:
    """Real client IP — Caddy forwards it in X-Forwarded-For (first hop)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _rl_bucket(path: str) -> "str | None":
    if path == "/api/grade":
        return "grade"
    if path == "/api/detect":
        return "detect"
    if path.startswith("/api/"):
        return "api"
    return None


def _rl_sweep(now: float) -> None:
    """Drop stale per-IP deques so the table can't grow unbounded."""
    if now - _rl_last_sweep[0] < 300:
        return
    _rl_last_sweep[0] = now
    for key, dq in list(_rl_hits.items()):
        horizon = now - _RL_RULES[key[1]][-1][0]
        while dq and dq[0] < horizon:
            dq.popleft()
        if not dq:
            del _rl_hits[key]


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    bucket = _rl_bucket(request.url.path)
    if not bucket:
        return await call_next(request)
    now = time.time()
    _rl_sweep(now)
    rules = _RL_RULES[bucket]
    dq = _rl_hits[(_client_ip(request), bucket)]
    while dq and dq[0] < now - rules[-1][0]:      # prune past the widest window
        dq.popleft()
    for window, limit in rules:
        cutoff = now - window
        if sum(1 for t in dq if t >= cutoff) >= limit:
            retry = max(1, int(window - (now - dq[0]))) if dq else window
            msg = ("You're grading a little fast — give it a moment and try again."
                   if bucket == "grade" else "Too many requests — slow down for a moment.")
            return JSONResponse(
                {"ok": False, "rate_limited": True, "message": msg},
                status_code=429, headers={"Retry-After": str(retry)})
    dq.append(now)
    return await call_next(request)


@app.on_event("startup")
def _startup():
    """Init the grades DB and load the card index.
    The heavy ORB index is loaded lazily on first grading request —
    it needs 1-2GB of RAM and will OOM on services with less memory."""
    db.init_db()
    engine.get_index()


# HTML pages: tell the browser to revalidate each load so a redeploy / edit is
# picked up immediately (the pages reference versioned ?v= assets for the rest).
_NO_CACHE = {"Cache-Control": "no-cache"}


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC / name, headers=_NO_CACHE)


@app.get("/")
def index():
    return _page("index.html")


@app.get("/about")
def about():
    return _page("about.html")


# ── In-memory index cache ───────────────────────────────────────────────
# The index file is ~3.5MB / 20k cards. Re-reading + re-parsing it on every
# /api/cards request (one per keystroke) and every /api/health poll is wasteful.
# Cache the parsed list and the derived set list, invalidating on file mtime so a
# live index build is still reflected without a restart.
_CACHE: dict = {"mtime": None, "cards": [], "sets": [], "set_counts": {}}


def _get_index() -> tuple[list, list, dict]:
    """Return (cards, sorted_sets, set_counts), reparsing only on file change."""
    try:
        mtime = config.INDEX_PATH.stat().st_mtime
    except OSError:
        return [], [], {}
    if _CACHE["mtime"] != mtime:
        try:
            cards = json.loads(config.INDEX_PATH.read_text())
        except Exception:
            return _CACHE["cards"], _CACHE["sets"], _CACHE["set_counts"]
        counts: dict[str, int] = {}
        for c in cards:
            counts[c.get("set", "")] = counts.get(c.get("set", ""), 0) + 1
        _CACHE.update(mtime=mtime, cards=cards,
                      sets=sorted(counts), set_counts=counts)
    return _CACHE["cards"], _CACHE["sets"], _CACHE["set_counts"]


def _live_index_count() -> int:
    """Current card count (cheap once the index is cached in memory)."""
    cards, _, _ = _get_index()
    return len(cards)


@app.get("/library")
def library():
    return _page("library.html")


@app.get("/activity")
def activity_page():
    return _page("activity.html")


@app.get("/api/activity")
def list_activity(limit: int = 24):
    """Recent grades from the website and the Discord bot, newest first."""
    return {"items": activity.recent(limit)}


@app.get("/api/stats/overview")
def stats_overview():
    """Site-wide scan stats (scans over time + grade distribution) for the graphs."""
    return activity.overview()


@app.get("/api/cards")
def list_cards(q: str = "", set: str = "", page: int = 1, per_page: int = 100):
    """Paginated card index, searchable by name / set / number."""
    cards, all_sets, set_counts = _get_index()

    # filter
    if q:
        ql = q.lower()
        cards = [c for c in cards if ql in c.get("name", "").lower()
                 or ql in c.get("set", "").lower()
                 or ql in c.get("number", "")]
    if set:
        cards = [c for c in cards if c.get("set") == set]

    # clamp page into range so out-of-bounds requests return the last page, not empty
    per_page = max(1, min(per_page, 200))
    total = len(cards)
    pages = max(1, -(-total // per_page))    # ceil
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    chunk = cards[start:start + per_page]
    return {"cards": chunk, "total": total, "page": page, "pages": pages,
            "sets": all_sets, "set_counts": set_counts, "indexed": total}


# ── Per-card market price (the conversion hook in the library modal) ─────
# Each TCGdex lookup is a slow (~1s) network call, so cache results per card.
# Prices barely move intra-day; a few-hour TTL keeps the modal instant on reopen.
_PRICE_CACHE: dict[str, tuple[float, dict]] = {}
_PRICE_TTL = 6 * 3600
# The graded tier we headline in the library modal.
_SHOWN_GRADE = 10


def _price_payload(card_id: str) -> dict:
    """Raw + estimated PSA-10 value in every currency (USD/EUR/GBP), for the modal."""
    data = pricing.get_card_value(card_id, grade=_SHOWN_GRADE)
    if not data.get("ok"):
        return {"ok": False}
    currencies = []
    for v in data.get("values", []) or []:
        if v.get("raw") is None:
            continue
        currencies.append({
            "currency": v["currency"], "symbol": v.get("symbol", ""),
            "raw": v["raw"], "graded": v.get("graded"),
        })
    if not currencies:
        return {"ok": False, "rarity": data.get("rarity")}
    return {"ok": True, "grade": _SHOWN_GRADE,
            "rarity": data.get("rarity"), "currencies": currencies}


@app.get("/api/price/{card_id}")
async def card_price(card_id: str):
    now = time.time()
    hit = _PRICE_CACHE.get(card_id)
    if hit and now - hit[0] < _PRICE_TTL:
        return hit[1]
    payload = await run_in_threadpool(_price_payload, card_id)
    if payload.get("ok"):           # only cache real hits, so transient API errors retry
        _PRICE_CACHE[card_id] = (now, payload)
    return payload


@app.get("/api/align-image/{token}")
def align_image(token: str):
    """Serve a photo the bot saved, so the web corner-align tool can pre-load it."""
    import re
    if not re.fullmatch(r"[A-Fa-f0-9]{6,32}", token):
        return JSONResponse({"ok": False}, status_code=404)
    p = config.DATA_DIR / "debug" / "align" / f"{token}.jpg"
    if not p.exists():
        return JSONResponse({"ok": False}, status_code=404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/api/card/{card_id}")
def get_card(card_id: str):
    """One card record from the index by id — used to deep-link into the library modal."""
    cards, _, _ = _get_index()
    for c in cards:
        if c.get("id") == card_id:
            return {"ok": True, "card": c}
    return JSONResponse({"ok": False}, status_code=404)


# ── Shareable grade snapshots ───────────────────────────────────────────
_SHARE_RE = re.compile(r"[A-Za-z0-9_-]{6,32}")


@app.get("/api/share/{token}")
def api_share(token: str):
    """The slim grade payload behind a share link, for client-side rendering."""
    if not _SHARE_RE.fullmatch(token):
        return JSONResponse({"ok": False}, status_code=404)
    data = share.get(token)
    if not data:
        return JSONResponse({"ok": False}, status_code=404)
    return {"ok": True, "share": data}


def _share_og(d: dict) -> dict:
    """OpenGraph title/description/image for a share snapshot (scrapers see this)."""
    name = d.get("name") or "Pokémon card"
    bits = [name]
    if d.get("set"):
        num = f" #{d['number']}" if d.get("number") else ""
        bits.append(f"{d['set']}{num}")
    grade = d.get("grade")
    if grade is not None:
        label = f"PSA {grade}" if d.get("slab") else f"PSA {grade} (est.)"
        title = f"{label} — {name}"
    else:
        title = f"{name} — graded on Viridian"
    # Headline value from USD if present.
    desc = "Graded free on Viridian — the card, an estimated grade, and live value."
    usd = next((v for v in d.get("values", []) if v.get("currency") == "USD"
                or v.get("symbol") == "$"), None)
    if usd:
        sym = usd.get("symbol") or "$"
        parts = []
        if usd.get("raw") is not None:
            parts.append(f"raw {sym}{usd['raw']:,.2f}")
        if usd.get("graded") is not None:
            tag = "graded" if d.get("graded_real") else "PSA 10 est."
            parts.append(f"{tag} {sym}{usd['graded']:,.2f}")
        if parts:
            desc = " · ".join(bits[1:] + parts) or desc
    return {"title": title, "description": desc,
            "image": d.get("image") or "", "url": f"{config.WEB_BASE_URL}/g/{d.get('token','')}"}


@app.get("/g/{token}")
def share_page(token: str):
    """Public share page with server-rendered OpenGraph tags for rich unfurls."""
    d = share.get(token) if _SHARE_RE.fullmatch(token) else None
    tmpl = (STATIC / "share.html").read_text()
    if not d:
        og = {"title": "Viridian — Pokémon card grading",
              "description": "Grade a Pokémon card from a single photo — free.",
              "image": f"{config.WEB_BASE_URL}/static/logo.svg",
              "url": f"{config.WEB_BASE_URL}/g/{token}"}
    else:
        og = _share_og(d)
    e = lambda s: html.escape(str(s or ""), quote=True)
    filled = (tmpl
              .replace("__OG_TITLE__", e(og["title"]))
              .replace("__OG_DESC__", e(og["description"]))
              .replace("__OG_IMAGE__", e(og["image"]))
              .replace("__OG_URL__", e(og["url"])))
    return HTMLResponse(filled, headers=_NO_CACHE)


# Same-origin proxy for card art, so the share-image <canvas> isn't tainted by a
# cross-origin draw (which would block toBlob/export). Locked to the card CDN.
_PROXY_PREFIX = "https://assets.tcgdex.net/"


def _fetch_remote_image(u: str) -> "tuple[str, bytes] | None":
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Viridian/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            ct = r.headers.get("Content-Type", "image/png")
            if not ct.startswith("image/"):
                return None
            return ct, r.read(6_000_000)
    except Exception:
        return None


@app.get("/api/proxy-image")
async def proxy_image(u: str):
    if not u.startswith(_PROXY_PREFIX):
        return JSONResponse({"ok": False}, status_code=400)
    got = await run_in_threadpool(_fetch_remote_image, u)
    if not got:
        return JSONResponse({"ok": False}, status_code=502)
    ct, body = got
    return Response(content=body, media_type=ct,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/health")
def health():
    # index_size = searchable (loaded at startup); indexed = live count on disk.
    return {"ok": True,
            "index_size": len(engine.get_index()),
            "indexed": _live_index_count()}


# Cap uploads before they reach the CV decode. A detect/grade request holds the whole
# image in memory through OpenCV, so an oversized upload is a per-request memory bomb;
# reject anything over this up front (covers both /api/detect and /api/grade).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


async def _read_upload(file: UploadFile) -> "bytes | None":
    """Read an upload, returning None if it exceeds the size cap (checked before decode)."""
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        return None
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return None
    return data


def _too_large() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "too_large": True,
         "message": "That image is too large — please use one under 10 MB."},
        status_code=413)


# Bound CV concurrency on the detect path. Unlike grading (fully serialized by
# _grade_lock), detect can run a few at once, but each one runs the same heavy OpenCV
# decode + corner detection (~195 MB in flight), so without a cap a flood oversubscribes
# CPU and can OOM the box. Allow a small number concurrently; fast-reject the rest as busy.
_detect_sem = asyncio.Semaphore(2)


@app.post("/api/detect")
async def detect(file: UploadFile = File(...)):
    if _detect_sem.locked():
        return JSONResponse(
            {"ok": False, "busy": True,
             "message": "Auto-detect is busy right now — give it a moment and try again."},
            status_code=503)
    async with _detect_sem:
        data = await _read_upload(file)
        if data is None:
            return _too_large()
        corners = await run_in_threadpool(engine.detect_corners_bytes, data)
        return {"corners": corners}


def _save_align(data: bytes) -> str:
    """Save an upload under a token so the corner-align tool can re-open it later."""
    import uuid
    token = uuid.uuid4().hex[:16]
    d = config.DATA_DIR / "debug" / "align"
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{token}.jpg").write_bytes(data)
    except Exception:
        return ""
    return token


# One grade at a time: a single CV/ORB grade nearly fills the machine's RAM, so two
# concurrent grades OOM-kill it. Serialize them, and fast-reject extras with a clear
# "busy" rather than crashing the whole site.
_grade_lock = asyncio.Lock()


@app.post("/api/grade")
async def grade(file: UploadFile = File(...), corners: str = Form(None),
                source: str = Form("web")):
    if _grade_lock.locked():
        return JSONResponse(
            {"ok": False, "busy": True,
             "message": "Another card is being graded right now — give it a few "
                        "seconds and try again."},
            status_code=503)
    async with _grade_lock:
        return await _do_grade(file, corners, source)


async def _do_grade(file: UploadFile, corners, source):
    data = await _read_upload(file)
    if data is None:
        return _too_large()
    # Optional manual-align corners: 4 [x,y] points normalised to 0..1.
    pts = None
    if corners:
        try:
            pts = json.loads(corners)
        except Exception:
            pts = None
    # Offload the blocking CV + ORB downloads to a thread so the event loop
    # (and /api/health polling) stays responsive while a card is processed.
    result = await run_in_threadpool(engine.process_bytes, data, pts)
    # Everything below is bonus side-effects (archive, share, activity feed, webhook, index
    # count) on an ALREADY-computed grade. None of it may 500 the response — a paid grade must
    # return even if the DB is locked, disk is full, or Discord is down. Each helper mostly
    # self-protects; this is the backstop.
    try:
        _save_debug(data, result)
        # Persistently archive anything we couldn't confidently identify, so failures can
        # be reviewed and the matcher fixed later. Unlike _save_debug (last-only), this
        # accumulates one file per failure.
        if result.get("uncertain") or not result.get("match"):
            _save_unidentified(data, result)
        # Token to re-open this photo in the corner-align tool, and the live index count
        # (so the Discord bot — a thin client — can show it without its own index file).
        result["align_token"] = _save_align(data)
        result["indexed"] = _live_index_count()
        # Shareable snapshot (only for confidently-identified cards) — token + rich link.
        token = await run_in_threadpool(share.create, result)
        if token:
            result["share_token"] = token
            result["share_url"] = f"{config.WEB_BASE_URL}/g/{token}"
        # Record to the shared feed. The Discord bot posts source="bot" through this same
        # endpoint, so both surfaces land in one DB-backed feed.
        src = "bot" if source == "bot" else "web"
        entry = await run_in_threadpool(activity.record, src, result)
        # Mirror WEBSITE grades to the #grading-results feed via the webhook. Bot grades are
        # NOT mirrored here — the bot posts them into that channel itself (with link buttons),
        # so mirroring would double-post.
        if entry and src == "web" and discord_webhook.enabled():
            await run_in_threadpool(discord_webhook.post_result, result, "the website")
    except Exception:
        logging.getLogger("viridian").exception("post-grade side-effect failed (grade still returned)")
    return JSONResponse(result)


def _save_debug(upload: bytes, result: dict) -> None:
    """Persist the last submission so it can be inspected after the fact."""
    try:
        (DEBUG_DIR / "last_upload.jpg").write_bytes(upload)
        if result.get("overlay"):
            b64 = result["overlay"].split(",", 1)[1]
            (DEBUG_DIR / "last_overlay.png").write_bytes(base64.b64decode(b64))
        slim = {k: v for k, v in result.items() if k != "overlay"}
        (DEBUG_DIR / "last_result.json").write_text(json.dumps(slim, indent=2, default=str))
    except Exception:
        pass


def _save_unidentified(upload: bytes, result: dict) -> None:
    """Archive an upload we couldn't confidently identify, plus a JSON sidecar.

    Accumulates one pair per failure under data/debug/unidentified/ so we can always
    look back at *what* was sent and *why* the matcher failed, and fix it later."""
    try:
        d = DEBUG_DIR / "unidentified"
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        (d / f"{stamp}.jpg").write_bytes(upload)
        if result.get("overlay"):
            try:
                b64 = result["overlay"].split(",", 1)[1]
                (d / f"{stamp}-overlay.png").write_bytes(base64.b64decode(b64))
            except Exception:
                pass
        # Slim sidecar: everything useful for diagnosis, minus the heavy overlay blob.
        detail = {
            "ts": stamp,
            "uncertain": result.get("uncertain"),
            "unsure_reason": result.get("unsure_reason"),
            "match_warning": result.get("match_warning"),
            "detection_warning": result.get("detection_warning"),
            "guess": result.get("guess"),
            "detection": result.get("detection"),
            "scores": result.get("scores"),
            "slab": result.get("slab"),
        }
        (d / f"{stamp}.json").write_text(json.dumps(detail, indent=2, default=str))
    except Exception:
        pass
