"""Build the perceptual-hash card index from TCGdex (https://tcgdex.dev).

TCGdex covers the newest sets (incl. 2026) that other databases lack, needs no
API key, and returns every card's image in a single request per set. We compute a
pHash for each card image and write data/index.json. Resumable: re-running skips
cards already indexed.

Examples
--------
    python scripts/build_index.py                 # everything (slow, resumable)
    python scripts/build_index.py --sets base1,me04
    python scripts/build_index.py --limit 500
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image
import imagehash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

API = config.TCGDEX_API
# Optional progress webhook. Set DISCORD_WEBHOOK_URL in your environment (or .env)
# to mirror build progress into a Discord channel; unset = no notifications.
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
_LAST_WEBHOOK = 0


def webhook(msg: str):
    global _LAST_WEBHOOK
    if not DISCORD_WEBHOOK:
        return
    if time.time() - _LAST_WEBHOOK < 10:
        return
    _LAST_WEBHOOK = time.time()
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
    except Exception:
        pass


def get_json(url: str):
    last = None
    for attempt in range(5):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            wait = 2 * (attempt + 1)
            print(f"  {url} failed ({e}); retry in {wait}s...")
            time.sleep(wait)
    raise last


def download_and_hash(card: dict, image_base: str) -> dict | None:
    """Download a card image, compute pHash, cache the image for the bot."""
    try:
        url_low = image_base + "/low.png"
        r = requests.get(url_low, timeout=(5, 15))
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        ph = str(imagehash.phash(img))
        # cache image for the bot (saves re-download at runtime)
        cid = card["id"]
        cache_path = config.IMAGE_CACHE / f"{cid}.jpg"
        if not cache_path.exists():
            url_high = image_base + "/high.png"
            high = requests.get(url_high, timeout=(5, 15))
            if high.status_code == 200:
                cache_path.write_bytes(high.content)
        return {"id": cid, "ph": ph}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", help="comma-separated TCGdex set ids (e.g. base1,me04)")
    ap.add_argument("--limit", type=int, default=0, help="max cards to index")
    args = ap.parse_args()

    existing = {}
    if config.INDEX_PATH.exists():
        for c in json.loads(config.INDEX_PATH.read_text()):
            existing[c["id"]] = c
    print(f"Starting with {len(existing)} cards already indexed.", flush=True)

    sets = get_json(f"{API}/sets")
    name_by_id = {s["id"]: s.get("name") for s in sets}
    set_ids = args.sets.split(",") if args.sets else [s["id"] for s in sets]
    print(f"Indexing {len(set_ids)} sets.", flush=True)

    added = 0
    t0 = time.time()
    try:
        for sid in set_ids:
            print(f"  [{len(existing)} cards] set: {sid}...", flush=True)
            detail = get_json(f"{API}/sets/{sid}")
            sname = detail.get("name") or name_by_id.get(sid)
            pending = []
            for card in detail.get("cards", []):
                if args.limit and len(existing) + len(pending) >= args.limit:
                    break
                if card["id"] in existing:
                    continue
                base = card.get("image")
                if not base:
                    continue
                pending.append((card, base))

            if not pending:
                continue

            with ThreadPoolExecutor(max_workers=24) as pool:
                futures = {pool.submit(download_and_hash, c, b): c
                           for c, b in pending}
                for fut in as_completed(futures):
                    result = fut.result()
                    if result is None:
                        continue
                    card = futures[fut]
                    existing[card["id"]] = {
                        "id": card["id"],
                        "name": card.get("name"),
                        "number": card.get("localId"),
                        "set": sname,
                        "image": (card["image"] or "") + "/high.png",
                        "phash": result["ph"],
                    }
                    added += 1
                if added % 10 == 0:
                    config.INDEX_PATH.write_text(json.dumps(list(existing.values())))
                if added % 100 == 0:
                    now = time.strftime("%H:%M")
                    pct = len(existing) / 18500 * 100
                    eta_mins = (18500 - len(existing)) / max(1, added / ((time.time() - t0) / 60))
                    eta = time.strftime("%H:%M", time.localtime(time.time() + eta_mins * 60))
                    print(f"  [{now}] {added} new (total {len(existing)}) — "
                          f"{pct:.0f}% — ETA {eta}", flush=True)
                if added % 500 == 0 and added > 0:
                    webhook(f"**Indexing**: {len(existing)} cards ({pct:.0f}%) — ETA {eta}")
            time.sleep(0.03)
            if args.limit and len(existing) >= args.limit:
                break
    except KeyboardInterrupt:
        print("Stopping early.")
    except Exception as e:
        print(f"Aborted on error: {e}. Saving progress so far.")

    elapsed = time.time() - t0
    config.INDEX_PATH.write_text(json.dumps(list(existing.values())))
    print(f"[{time.strftime('%H:%M')}] Done. Added {added} in {elapsed/60:.0f}m. "
          f"Index now holds {len(existing)} cards at {config.INDEX_PATH}", flush=True)


if __name__ == "__main__":
    main()
