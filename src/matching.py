"""Identify which card is in a photo.

Two stages:
  1. **pHash** — a fast perceptual-hash nearest-neighbour gives a shortlist of
     candidate cards (data/index.json, built by scripts/build_index.py).
  2. **ORB** — optional re-ranking of that shortlist by actual local feature
     matches against each candidate's image. ORB is robust to the perspective and
     lighting differences between a phone photo and a clean scan, which pHash is
     not. Candidate images are cached under data/card_images for speed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image
import imagehash
import requests

import config

_ORB = cv2.ORB_create(nfeatures=900)
_BF = cv2.BFMatcher(cv2.NORM_HAMMING)


def _orb_features(bgr: np.ndarray):
    """Detect ORB keypoints/descriptors on a width-normalised grayscale image."""
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    scale = 360.0 / g.shape[1]
    if scale < 1:
        g = cv2.resize(g, None, fx=scale, fy=scale)
    return _ORB.detectAndCompute(g, None)


def _good_matches(qdesc, cand_desc) -> int:
    """Lowe-ratio-test count of good matches between two descriptor sets."""
    if qdesc is None or cand_desc is None or len(cand_desc) < 2:
        return 0
    pairs = _BF.knnMatch(qdesc, cand_desc, k=2)
    good = 0
    for pr in pairs:
        if len(pr) == 2 and pr[0].distance < 0.75 * pr[1].distance:
            good += 1
    return good


class CardIndex:
    def __init__(self, path=config.INDEX_PATH):
        self.path = path
        self.cards = []
        self.cache = config.IMAGE_CACHE
        self.cache.mkdir(exist_ok=True)
        if path.exists():
            self.cards = json.loads(path.read_text())
            for c in self.cards:
                c["_hash"] = imagehash.hex_to_hash(c["phash"])

    def __len__(self):
        return len(self.cards)

    @staticmethod
    def _hash_bgr(bgr: np.ndarray) -> imagehash.ImageHash:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return imagehash.phash(Image.fromarray(rgb))

    def match(self, card_bgr: np.ndarray, top_n: int = 3) -> list[dict]:
        """Return up to top_n nearest cards by pHash, each {card, distance}."""
        if not self.cards:
            return []
        h = self._hash_bgr(card_bgr)
        scored = sorted(self.cards, key=lambda c: h - c["_hash"])
        return [{"card": {k: v for k, v in c.items() if not k.startswith("_")},
                 "distance": int(h - c["_hash"])} for c in scored[:top_n]]

    def _candidate_image(self, card: dict) -> Optional[np.ndarray]:
        """Load a candidate card's image from cache, downloading once if needed."""
        cid = card.get("id")
        if not cid:
            return None
        path = self.cache / f"{cid}.jpg"
        if path.exists():
            return cv2.imread(str(path))
        url = card.get("image")
        if not url:
            return None
        try:
            data = requests.get(url, timeout=(3, 6)).content
            path.write_bytes(data)
            return cv2.imread(str(path))
        except Exception:
            return None

    def _candidates_cached(self, shortlist: list[dict]) -> bool:
        """Check if candidate images are already cached (avoids slow HTTP at runtime)."""
        return all((self.cache / f"{c['card']['id']}.jpg").exists()
                   for c in shortlist)

    def best(self, card_bgr: np.ndarray) -> Optional[dict]:
        """Best match. pHash shortlist, then ORB re-rank when enabled.

        Returns {card, distance, orb_score?, method}. ``method`` is 'orb' or
        'phash'; low-confidence results are flagged by the caller via distance /
        orb_score.
        """
        shortlist = self.match(card_bgr, top_n=config.ORB_SHORTLIST)
        if not shortlist:
            return None
        if not config.USE_ORB or len(shortlist) == 1:
            r = dict(shortlist[0]); r["method"] = "phash"
            return r

        # Skip ORB if images aren't cached — don't block on HTTP at runtime.
        if not self._candidates_cached(shortlist):
            r = dict(shortlist[0]); r["method"] = "phash"
            return r

        qkp, qdesc = _orb_features(card_bgr)
        best, best_score = None, -1
        for cand in shortlist:
            img = self._candidate_image(cand["card"])
            if img is None:
                continue
            desc = _orb_features(img)[1]
            score = 0 if desc is None else _good_matches(qdesc, desc)
            if score > best_score:
                best, best_score = cand, score

        # If ORB found real structure, trust it; else fall back to pHash top.
        if best_score >= config.ORB_MIN_GOOD:
            r = dict(best); r["orb_score"] = int(best_score); r["method"] = "orb"
            return r
        r = dict(shortlist[0])
        r["orb_score"] = int(best_score)
        r["method"] = "phash"
        return r
