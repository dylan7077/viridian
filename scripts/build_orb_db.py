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


def available_commit_bytes():
    """Windows' remaining commit budget, or None when unavailable."""
    if os.name != "nt":
        return None
    import ctypes
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                    ("total_pagefile", ctypes.c_ulonglong), ("available_pagefile", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong)]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return status.available_pagefile
    return None


def main():
    free_commit = available_commit_bytes()
    if free_commit is not None and free_commit < 2 * 1024**3:
        print("Not enough free system commit for a safe ORB rebuild; retry when more is available.", file=sys.stderr)
        return 1
    cv2.setNumThreads(1)
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
        return 1
    desc = np.vstack(descs)
    owner = np.concatenate(owners)
    out = config.DATA_DIR / "orb_db.npz"
    tmp = config.DATA_DIR / "orb_db.tmp.npz"
    # write to a temp file then atomically replace, so the live server never
    # reads a half-written DB while hot-reloading.
    np.savez(tmp, desc=desc, owner=owner, cards=np.array(cards_meta, dtype=object))
    os.replace(tmp, out)
    print(f"Done. {len(cards_meta)} cards, {desc.shape[0]} descriptors -> {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (MemoryError, cv2.error) as exc:
        print(f"ORB build failed; the previous database was not replaced: {exc}", file=sys.stderr)
        sys.exit(1)
