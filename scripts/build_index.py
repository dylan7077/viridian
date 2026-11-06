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
import tempfile
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


class ProviderUnavailable(Exception):
    """Stop the pass when the provider asks us to back off."""


def save_index(existing):
    """Publish a complete JSON file so readers never see a partial write."""
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=config.DATA_DIR,
                                         delete=False) as tmp:
            name = tmp.name
            json.dump(list(existing.values()), tmp)
        os.replace(name, config.INDEX_PATH)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


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
            high.raise_for_status()
            # A 200 HTML error or corrupt image must not poison the cache/index.
            with Image.open(io.BytesIO(high.content)) as image:
                image.verify()
            cache_path.write_bytes(high.content)
        return {"id": cid, "ph": ph}
    except Exception as exc:
        if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in (429, 503):
            raise ProviderUnavailable(f"TCGdex returned HTTP {exc.response.status_code}; retry later") from exc
        print(f"  Could not index {card.get('id', '?')}: {exc}", flush=True)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", help="comma-separated TCGdex set ids (e.g. base1,me04)")
    ap.add_argument("--limit", type=int, default=0, help="max cards to index")
    ap.add_argument("--workers", type=int, default=6, help="concurrent image downloads (default: 6)")
    args = ap.parse_args()
    if not 1 <= args.workers <= 24:
        ap.error("--workers must be between 1 and 24")
    config.IMAGE_CACHE.mkdir(parents=True, exist_ok=True)

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
    failed = False
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
                if card["id"] in existing and (config.IMAGE_CACHE / f"{card['id']}.jpg").is_file():
                    continue
                base = card.get("image")
                if not base:
                    continue
                pending.append((card, base))

            if not pending:
                continue

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(download_and_hash, c, b): c
                           for c, b in pending}
                for fut in as_completed(futures):
                    try:
                        result = fut.result()
                    except ProviderUnavailable:
                        for pending_future in futures:
                            pending_future.cancel()
                        raise
                    if result is None:
                        failed = True
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
                # Persist after every set, independent of how many cards succeeded.
                save_index(existing)
                if added % 100 == 0:
                    now = time.strftime("%H:%M")
                    print(f"  [{now}] {added} new (total {len(existing)})", flush=True)
                if added % 500 == 0 and added > 0:
                    webhook(f"**Indexing**: {len(existing)} cards")
            time.sleep(0.03)
            if args.limit and len(existing) >= args.limit:
                break
    except KeyboardInterrupt:
        print("Stopping early.")
    except Exception as e:
        failed = True
        print(f"Aborted on error: {e}. Saving progress so far.")

    elapsed = time.time() - t0
    save_index(existing)
    print(f"[{time.strftime('%H:%M')}] Done. Added {added} in {elapsed/60:.0f}m. "
          f"Index now holds {len(existing)} cards at {config.INDEX_PATH}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
