"""Back-of-card grading.

Every Pokémon card shares the same blue back, which turns the hardest *front* problems
into easy ones:

  * **Whitening** = bright, desaturated pixels. Against the uniform dark-blue field, edge
    and corner wear is maximum-contrast and needs *no* per-card reference — unlike the holo
    front, where white wear competes with the art. Graders flip cards to judge edges for
    exactly this reason.
  * **One canonical reference** for every card, with no holo, so alignment-based
    back-centering is trivial and robust (PSA grades back-centering too — often the limiter).

Thresholds here are provisional until tuned against real back photos; the *mechanism*
(white-on-blue) is what makes the back reliable where the front isn't.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

import config
from src import grading, refgrade

_BACK_REF: Optional[np.ndarray] = None
_REF_PATH = config.DATA_DIR / "refs" / "card_back.jpg"

# Whitening on the back is an ACHROMATIC outlier against the card's own blue field — NOT
# an absolute brightness. Real photos shift the field colour wildly with white balance
# (blue can read red-ish under warm light), so any fixed threshold flags the whole field.
# We measure saturation relative to the field's own median instead. (Validated on 87 real
# backs: this separates destroyed from pristine; glare still confounds the mid-range —
# fine-grained grading needs the learned model.)
_REL_SAT = 0.45          # whitening if saturation < 45% of the field's median
_MIN_SAT = 25            # ...but never below this absolute floor
_MIN_VAL = 110           # must be bright enough to be whitening, not shadow


def _back_ref() -> Optional[np.ndarray]:
    global _BACK_REF
    if _BACK_REF is None and _REF_PATH.exists():
        img = cv2.imread(str(_REF_PATH))
        if img is not None:
            _BACK_REF = cv2.resize(img, (config.CARD_W, config.CARD_H))
    return _BACK_REF


def _white_mask(card: np.ndarray) -> np.ndarray:
    """White-wear mask: pixels markedly less saturated than the card's own blue field
    (achromatic) yet bright. Adapts per photo, so it survives the wild white-balance
    swings in real back photos instead of flagging the whole field."""
    hsv = cv2.cvtColor(card, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    h, w = card.shape[:2]
    inner = hsv[int(h * 0.15):int(h * 0.85), int(w * 0.15):int(w * 0.85)]
    s_field = float(np.median(inner[:, :, 1]))
    v_field = float(np.median(inner[:, :, 2]))
    return (s < max(_MIN_SAT, s_field * _REL_SAT)) & (v > v_field * 0.95) & (v > _MIN_VAL)


def _assess_corners(card: np.ndarray, white: np.ndarray) -> dict:
    h, w = card.shape[:2]
    sz = int(min(h, w) * 0.10)
    r = int(min(h, w) * 0.06)
    base = grading._rounded_corner_mask(sz, r)
    masks = [base, base[:, ::-1], base[::-1, :], base[::-1, ::-1]]
    spots = [(0, 0), (w - sz, 0), (0, h - sz), (w - sz, h - sz)]
    wear = [float((white[ry:ry + sz, rx:rx + sz] & mk).sum()) / mk.sum()
            for (rx, ry), mk in zip(spots, masks)]
    return {"grade": grading._whitening_to_grade(max(wear)),
            "per_corner": [round(x, 3) for x in wear], "confidence": "low"}


def _assess_edges(card: np.ndarray, white: np.ndarray) -> dict:
    h, w = card.shape[:2]
    o = int(min(h, w) * 0.015)
    t = int(min(h, w) * 0.03)
    strips = [(slice(o, o + t), slice(None)), (slice(h - o - t, h - o), slice(None)),
              (slice(None), slice(o, o + t)), (slice(None), slice(w - o - t, w - o))]
    wear = [float(white[ys, xs].mean()) for ys, xs in strips]
    return {"grade": grading._whitening_to_grade(max(wear)),
            "per_edge": [round(x, 3) for x in wear], "confidence": "low"}


def grade_back(back_card: np.ndarray) -> dict:
    """Grade a warped back image: centering (aligned to the canonical back) plus
    edge/corner whitening and surface, measured against the uniform blue field.

    Returns sub-grades shaped like the front (grading.*), so the caller can combine
    front+back per aspect (PSA-style worst-of, or a front/back weighting)."""
    ref = _back_ref()
    centering = refgrade.measure_centering(back_card, ref) if ref is not None else None
    if centering:
        centering["method"] = "reference-back"
    white = _white_mask(back_card)
    return {
        "centering": centering or {"ok": False, "grade": None,
                                   "note": "Couldn't align the back to the reference."},
        "corners": _assess_corners(back_card, white),
        "edges": _assess_edges(back_card, white),
        "surface": grading.assess_surface(back_card),   # top-hat scuff on the uniform back
    }
