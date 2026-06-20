"""Pre-cache every indexed card's image into data/card_images.

ORB re-ranking only runs when the shortlist's candidate images are already on
disk (matching.CardIndex skips ORB otherwise, to avoid slow HTTP at request
time). This downloads them ahead of time so identification uses ORB — which is
far more accurate than pHash alone — without any runtime download cost.

Resumable: skips images already cached. Safe to run repeatedly as the index grows.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.IMAGE_CACHE.mkdir(exist_ok=True)


def main():
    cards = json.loads(config.INDEX_PATH.read_text())
    total = len(cards)
    have = {p.stem for p in config.IMAGE_CACHE.glob("*.jpg")}
    print(f"{total} cards in index, {len(have)} already cached.")

    done = 0
    for c in cards:
        cid, url = c.get("id"), c.get("image")
        if not cid or not url or cid in have:
            continue
        path = config.IMAGE_CACHE / f"{cid}.jpg"
        try:
            data = requests.get(url, timeout=(4, 10)).content
            path.write_bytes(data)
            done += 1
            if done % 100 == 0:
                print(f"  cached {done} new "
                      f"({len(have) + done}/{total})...")
            time.sleep(0.02)
        except Exception:
            continue
    print(f"Done. Cached {done} new images. "
          f"Total cached: {len(list(config.IMAGE_CACHE.glob('*.jpg')))}")


if __name__ == "__main__":
    main()
