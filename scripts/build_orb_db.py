"""Precompute a global ORB descriptor database for every indexed card.

Pools ORB descriptors from every cached card image into one big array (plus an
owner array mapping each descriptor back to its card). The matcher (src/orb_index)
loads this and matches a scan against ALL cards at once via feature voting, so the
correct card is found regardless of how a perceptual hash would rank it.

Reuses data/card_images (the pre-cache). Resumable-ish: rebuilds from whatever is
cached. Re-run as the index/cache grows.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

NFEATURES = 400
WIDTH = 480
FIELDS = ("id", "name", "number", "set", "image", "rarity")


def main():
    orb = cv2.ORB_create(nfeatures=NFEATURES)
    index = json.loads(config.INDEX_PATH.read_text())
    cards_meta, descs, owners = [], [], []

    for c in index:
        p = config.IMAGE_CACHE / f"{c['id']}.jpg"
        if not p.exists():
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        s = WIDTH / g.shape[1]
        if s < 1:
            g = cv2.resize(g, None, fx=s, fy=s)
        _, d = orb.detectAndCompute(g, None)
        if d is None or len(d) < 8:
            continue
        ci = len(cards_meta)
        cards_meta.append({k: c.get(k) for k in FIELDS})
        descs.append(d)
        owners.append(np.full(len(d), ci, np.int32))
        if len(cards_meta) % 500 == 0:
            print(f"  processed {len(cards_meta)} cards...")

    if not descs:
        print("No cached images found — run scripts/precache_images.py first.")
        return
    desc = np.vstack(descs)
    owner = np.concatenate(owners)
    out = config.DATA_DIR / "orb_db.npz"
    tmp = config.DATA_DIR / "orb_db.tmp.npz"
    # write to a temp file then atomically replace, so the live server never
    # reads a half-written DB while hot-reloading.
    np.savez(tmp, desc=desc, owner=owner, cards=np.array(cards_meta, dtype=object))
    os.replace(tmp, out)
    print(f"Done. {len(cards_meta)} cards, {desc.shape[0]} descriptors -> {out}")
    return len(cards_meta)


if __name__ == "__main__":
    main()
