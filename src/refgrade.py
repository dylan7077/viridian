"""Reference-based grading — measure grade signals by aligning the *identified* card to
its clean catalog image, instead of guessing from the photo alone.

Blind heuristics can't tell a card's frame from its artwork (every layout differs), so
centering was only ~60% right on perfectly-centered reference cards. Once the card is
identified we have its reference image, which changes the game:

  * **Centering** — ORB-align the reference to the photo (a homography robust to holo,
    glare and tilt; hundreds of inliers even on foil). The reference is *known* centered,
    so we map a symmetric reference frame through the homography: any asymmetry in the
    result is the real physical miscut, with no frame-detection guesswork. Both axes,
    reliable, across normal / holo / reverse / full-art.

  * **Wear / surface** (follow-up) — the holo pattern is in *both* images, so it cancels
    in the aligned difference; only genuine whitening / damage survives.

Returns None when it can't align confidently, so the caller falls back to the heuristic.
"""
from __future__ import annotations

import os
from typing import Optional

import cv2
import numpy as np
import requests

import config
from src import grading

CARD_W, CARD_H = config.CARD_W, config.CARD_H
_B0 = int(CARD_W * 0.084)            # nominal border px of a centered card (~8.4%)
_REF_DIR = config.DATA_DIR / "ref_cache"

_orb = cv2.ORB_create(2000)
_bf = cv2.BFMatcher(cv2.NORM_HAMMING)


def load_reference(card_id: str, image_url: str) -> Optional[np.ndarray]:
    """Fetch + cache the card's clean reference image, resized to the canonical card.
    Cached on disk by card id so a card is only downloaded once."""
    if not card_id or not image_url:
        return None
    _REF_DIR.mkdir(exist_ok=True)
    path = _REF_DIR / f"{card_id}.png"
    if not path.exists():
        try:
            r = requests.get(image_url, timeout=20)
            r.raise_for_status()
            path.write_bytes(r.content)
        except Exception:
            return None
    img = cv2.imread(str(path))
    if img is None:
        return None
    return cv2.resize(img, (CARD_W, CARD_H))


def _homography(ref: np.ndarray, user: np.ndarray):
    """ORB + RANSAC homography mapping reference → user card. Returns (H, inliers)."""
    k1, d1 = _orb.detectAndCompute(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), None)
    k2, d2 = _orb.detectAndCompute(cv2.cvtColor(user, cv2.COLOR_BGR2GRAY), None)
    if d1 is None or d2 is None:
        return None, 0
    matches = _bf.knnMatch(d1, d2, k=2)
    good = [a for a, b in (m for m in matches if len(m) == 2) if a.distance < 0.75 * b.distance]
    if len(good) < 15:
        return None, 0
    src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if H is None:
        return None, 0
    return H, int(mask.sum())


def measure_centering(user_card: np.ndarray, ref: np.ndarray) -> Optional[dict]:
    """Centering from reference alignment. Same shape as grading.measure_centering, with
    method='reference'. Returns None to fall back to the heuristic when alignment is weak."""
    H, inliers = _homography(ref, user_card)
    if H is None or inliers < 25:
        return None

    # Map a symmetric reference frame; asymmetry out == real miscut.
    frame = np.float32([[_B0, _B0], [CARD_W - _B0, _B0],
                        [CARD_W - _B0, CARD_H - _B0], [_B0, CARD_H - _B0]])
    mp = cv2.perspectiveTransform(frame.reshape(-1, 1, 2), H).reshape(-1, 2)
    left = float(mp[:, 0].min())
    right = float(CARD_W - mp[:, 0].max())
    top = float(mp[:, 1].min())
    bottom = float(CARD_H - mp[:, 1].max())
    # A sane alignment keeps the frame inside the card with a real border on every side.
    if min(left, right, top, bottom) < 2 or max(left, right) > 0.45 * CARD_W \
            or max(top, bottom) > 0.45 * CARD_H:
        return None

    lr = 100 * max(left, right) / (left + right)
    tb = 100 * max(top, bottom) / (top + bottom)
    worst = max(lr, tb)

    def fmt(a, b):
        return f"{round(100 * a / (a + b))}/{round(100 * b / (a + b))}"

    return {
        "ok": True,
        "grade": grading._centering_to_grade(worst),
        "left_right": fmt(left, right),
        "top_bottom": fmt(top, bottom),
        "worst_pct": round(worst, 1),
        "confidence": "high" if inliers >= 60 else "medium",
        "method": "reference",
        "inliers": inliers,
        "frame": {"lx": int(mp[:, 0].min()), "rx": int(mp[:, 0].max()),
                  "ty": int(mp[:, 1].min()), "by": int(mp[:, 1].max())},
        "borders_px": {"left": int(left), "right": int(right),
                       "top": int(top), "bottom": int(bottom)},
    }
