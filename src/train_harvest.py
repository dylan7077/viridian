"""Harvest REAL, reference-grounded training data from production grades.

The bundled training set (data/training/) is 99.7% synthetic — clean scans with
algorithmic damage bolted on — and FINDINGS.md already proved it gives near-zero
predictive signal for corners/edges (the CNN trained on it is display-only for that
reason). What's actually missing is real photos with real labels.

Every time a photo aligns CONFIDENTLY to its card's clean reference
(refgrade.assess_defects, inliers >= 60 = "high" per refgrade's own bar), the aligned
per-corner/edge/surface defect fractions aren't a guess — they're a direct pixel
measurement against a known-clean reference. That measurement IS a usable label. Save
(photo, label) pairs here so a future retrain can learn from real-world photos —
real lighting, glare, phone quality — instead of synthetic damage.

Inert by default in spirit: never raises, never blocks a grade, costs one JPEG write.
"""
from __future__ import annotations

import json
import time
from typing import Optional

import cv2
import numpy as np

import config

_DIR = config.DATA_DIR / "training_real"
_DIR.mkdir(exist_ok=True)
_MIN_INLIERS = 60           # refgrade's own "high confidence" bar — only trustworthy labels
# ponytail: flat-file cap, no dedup/balancing — fine until volume is large enough to
# retrain; a real pipeline would dedupe near-identical photos and balance grades/sets.
HARVEST_CAP = 50_000


def maybe_harvest(warped: np.ndarray, card_id: Optional[str], defects: Optional[dict],
                  centering: Optional[dict], inliers: int) -> None:
    """Save a (photo, reference-grounded label) pair when confidence is high enough."""
    if inliers < _MIN_INLIERS or warped is None or not card_id:
        return
    try:
        if sum(1 for _ in _DIR.glob("*.jpg")) >= HARVEST_CAP:
            return
        stamp = f"{card_id}_{int(time.time() * 1000)}"
        ok = cv2.imwrite(str(_DIR / f"{stamp}.jpg"), warped, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return
        label = {
            "card_id": card_id, "inliers": int(inliers),
            "corners": (defects or {}).get("corners"),
            "edges": (defects or {}).get("edges"),
            "surface": (defects or {}).get("surface"),
            "centering": centering,
        }
        (_DIR / f"{stamp}.json").write_text(json.dumps(label))
    except Exception:
        pass


def count() -> int:
    try:
        return sum(1 for _ in _DIR.glob("*.jpg"))
    except Exception:
        return 0
