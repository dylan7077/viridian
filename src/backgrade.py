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


def assess_back_defects(back_card: np.ndarray) -> Optional[dict]:
    """Reference-difference whitening/flaw read for the back: align the canonical back,
    then flag pixels that LOST the saturation the reference has there (real white wear),
    which cancels the inherently-light printed design (white Poké Ball, light swirls are
    light in the reference too). Cleaner than the blind white-on-blue mask and yields a
    flaw overlay. Returns front-shaped corners/edges/surface + overlay, or None if the
    back can't be aligned confidently."""
    ref = _back_ref()
    if ref is None:
        return None
    H, inliers = refgrade._homography(ref, back_card)
    if H is None or inliers < 25:
        return None
    h, w = config.CARD_H, config.CARD_W
    Hinv = np.linalg.inv(H)
    user_w = cv2.warpPerspective(back_card, Hinv, (w, h))
    cover = cv2.warpPerspective(np.full(back_card.shape[:2], 255, np.uint8), Hinv, (w, h)) > 200
    if cover.mean() < 0.7:
        return None
    hu = cv2.cvtColor(user_w, cv2.COLOR_BGR2HSV)
    hr = cv2.cvtColor(ref, cv2.COLOR_BGR2HSV)
    su, vu = hu[..., 1].astype(np.int16), hu[..., 2].astype(np.int16)
    sr, vr = hr[..., 1].astype(np.int16), hr[..., 2].astype(np.int16)
    k5 = np.ones((5, 5), np.uint8)
    # Whitening, SELF-CALIBRATED to THIS photo (mirrors refgrade.assess_defects — see its
    # comment for why). Comparing photo saturation straight against the REFERENCE's
    # saturation (the earlier version here) breaks on any photo with broad glare/exposure
    # shift: a glossy back photographed under a light source desaturates the WHOLE frame,
    # not just real wear, and the whole card reads as "whitened". Measured on 45 real back
    # photos: that bug alone put the median corner/edge grade at 3 (93% flagged <=8) even
    # on visually fine cards. Self-calibrating against this photo's OWN inner-design
    # saturation (like the front's own border-band baseline) fixes it — a uniform glare
    # shift moves the baseline too, so only REAL local colour loss stands out.
    ref_whiteish = ((sr < 50) & (vr > 180)).astype(np.uint8)   # naturally-light ref areas
    ref_white_near = cv2.dilate(ref_whiteish, k5) > 0                # (Poké Ball highlight)
    # Per-SIDE baseline: a directional glare/light source desaturates one side of a
    # glossy back far more than the others (measured on real photos — a global or
    # centre-sampled baseline doesn't compensate for that). Sample near each side's own
    # mid-band, same as the front's border-band self-calibration, so each strip is judged
    # against how THIS PHOTO renders colour on THAT SAME SIDE, not the opposite one.
    o2 = int(min(h, w) * 0.03)
    mh, mw = slice(int(h * 0.35), int(h * 0.65)), slice(int(w * 0.35), int(w * 0.65))
    side_bands = {
        "T": (slice(o2, o2 * 2), mw), "B": (slice(h - o2 * 2, h - o2), mw),
        "L": (mh, slice(o2, o2 * 2)), "R": (mh, slice(w - o2 * 2, w - o2)),
    }

    def side_baseline(ys, xs):
        patch, cpatch = su[ys, xs], cover[ys, xs]
        return float(np.median(patch[cpatch])) if cpatch.any() else 0.0

    white = np.zeros((h, w), bool)
    for ys, xs in side_bands.values():
        base = side_baseline(ys, xs)
        if base > 30:   # this side must render with SOME colour to measure loss against
            white[ys, xs] = ((su[ys, xs] < 0.5 * base) & (vu[ys, xs] > 120)
                             & ~ref_white_near[ys, xs] & cover[ys, xs])
    # Dark damage: markedly darker than the (exposure-matched) reference — dirt/crease.
    mu, mr = float(np.median(vu[cover])), float(np.median(vr[cover]))
    vu_n = np.clip(vu * (mr / mu), 0, 255).astype(np.int16) if mu > 1 else vu
    vr_lo = cv2.erode(vr.astype(np.uint8), k5).astype(np.int16)
    dark = (vu_n < vr_lo - 70) & cover
    defect = white | dark
    # Surface scuff: photo-only bright top-hat features, reference design cancelled.
    gu = cv2.cvtColor(user_w, cv2.COLOR_BGR2GRAY)
    gr = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    kk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    scuff = ((cv2.morphologyEx(gu, cv2.MORPH_TOPHAT, kk) > 30)
             & (cv2.dilate(cv2.morphologyEx(gr, cv2.MORPH_TOPHAT, kk), k5) < 18) & cover)

    sz, r = int(min(h, w) * 0.09), int(min(h, w) * 0.055)
    base = grading._rounded_corner_mask(sz, r)
    cmasks = [base, base[:, ::-1], base[::-1, :], base[::-1, ::-1]]
    spots = [(0, 0), (w - sz, 0), (0, h - sz), (w - sz, h - sz)]

    def frac(m, ys, xs, region=None):
        cov = cover[ys, xs] if region is None else (cover[ys, xs] & region)
        n = int(cov.sum())
        return float((m[ys, xs] & cov).sum()) / n if n > 50 else 0.0

    corner_fr = [frac(defect, slice(y, y + sz), slice(x, x + sz), mk)
                 for (x, y), mk in zip(spots, cmasks)]
    o, t = int(min(h, w) * 0.015), int(min(h, w) * 0.035)
    strips = [(slice(o, o + t), slice(None)), (slice(h - o - t, h - o), slice(None)),
              (slice(None), slice(o, o + t)), (slice(None), slice(w - o - t, w - o))]
    edge_fr = [frac(defect, ys, xs) for ys, xs in strips]
    inner = (slice(int(h * 0.10), int(h * 0.90)), slice(int(w * 0.08), int(w * 0.92)))
    surf_fr = frac(scuff | dark, *inner)

    overlay = refgrade._defect_overlay(user_w, defect, scuff, corner_fr, edge_fr, spots, sz, strips)
    conf = "high" if inliers >= 60 else "medium"
    T = refgrade
    return {
        "overlay_img": overlay,
        "corners": {"grade": T._ref_frac_to_grade(max(corner_fr), T._REF_CORNER_THR),
                    "per_corner": [round(x, 4) for x in corner_fr],
                    "confidence": conf, "method": "reference-back"},
        "edges": {"grade": T._ref_frac_to_grade(max(edge_fr), T._REF_EDGE_THR),
                  "per_edge": [round(x, 4) for x in edge_fr],
                  "confidence": conf, "method": "reference-back"},
        "surface": {"grade": T._ref_frac_to_grade(surf_fr, T._REF_SURF_THR),
                    "scuff": round(surf_fr, 4), "confidence": conf, "method": "reference-back"},
        "inliers": inliers,
    }


def grade_back(back_card: np.ndarray) -> dict:
    """Grade a warped back image: centering (aligned to the canonical back) plus
    edge/corner whitening and surface, measured against the uniform blue field.

    Returns sub-grades shaped like the front (grading.*), so the caller can combine
    front+back per aspect (PSA-style worst-of, or a front/back weighting)."""
    ref = _back_ref()
    centering = refgrade.measure_centering(back_card, ref) if ref is not None else None
    if centering:
        centering["method"] = "reference-back"
    no_center = {"ok": False, "grade": None, "note": "Couldn't align the back to the reference."}
    # DISABLED (2026-07-03): assess_back_defects's reference-difference whitening test
    # compares the photo's saturation straight against the REFERENCE's — measured on 45
    # real back photos, that broke badly under directional glare (a light source hitting
    # one side of a glossy back desaturates that side far more than the rest), putting the
    # median corner/edge grade at 3 even on visually fine cards. A self-calibrated retry
    # (per-side baseline, matching the front's proven approach) still didn't separate
    # cleanly — this needs a broader labeled set + more time, not a quick patch, while it's
    # live and actively wrong. Falling back to the blind heuristic below (weaker signal,
    # but not confidently, systematically broken) until it's properly recalibrated.
    white = _white_mask(back_card)
    return {
        "centering": centering or no_center,
        "corners": _assess_corners(back_card, white),
        "edges": _assess_edges(back_card, white),
        "surface": grading.assess_surface(back_card),   # top-hat scuff on the uniform back
    }
