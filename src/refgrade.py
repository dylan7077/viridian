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
        # A failed download (error body, truncated file) must not poison the card
        # forever — drop the bad cache so the next call can re-fetch.
        try:
            path.unlink()
        except OSError:
            pass
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


def _rounded_corner_mask(size: int, r: int) -> np.ndarray:
    """Card-material mask for a TOP-LEFT rounded corner patch (True = card)."""
    yy, xx = np.mgrid[0:size, 0:size]
    outside = (xx < r) & (yy < r) & ((xx - r) ** 2 + (yy - r) ** 2 > r * r)
    return ~outside


def assess_defects(user_card: np.ndarray, ref: np.ndarray) -> Optional[dict]:
    """TAG-style defect read: align the clean reference onto the photo and compare.

    Everything printed (art, frame, holo pattern, text) exists in BOTH images and
    cancels; genuine damage exists only in the photo and survives:
      * whitening — photo pixel bright + desaturated where the aligned print is a
        saturated colour (the yellow-border case). A white/silver border can't
        false-positive because the reference is white there too.
      * dark damage — markedly darker than the aligned print (dirt, crease shadow).
      * surface scuff — small bright features (top-hat) in the photo that the
        reference print does NOT have, so print lines / design edges cancel.

    Aggregated per region: 4 corners, 4 edge strips, inner surface. Returns None
    when alignment isn't trustworthy — callers keep the blind heuristics.
    Thresholds calibrated on the labeled clean/damaged populations
    (scripts/calibrate_refdefects.py); grade maps live in _REF_* below."""
    H, inliers = _homography(ref, user_card)
    if H is None or inliers < 30:
        return None
    # Work in the REFERENCE's canonical frame, not the photo's: warping the photo onto
    # the ref means every zone (border strips, corner arcs) sits at known canonical
    # coordinates with full reference data underneath. The other direction leaves the
    # ref slightly inset from the photo's cut edge — killing coverage exactly in the
    # outer strips where whitening lives.
    h, w = CARD_H, CARD_W
    Hinv = np.linalg.inv(H)
    user_w = cv2.warpPerspective(user_card, Hinv, (w, h))
    cover = cv2.warpPerspective(
        np.full(user_card.shape[:2], 255, np.uint8), Hinv, (w, h)) > 200
    if cover.mean() < 0.7:
        return None
    ref_w = ref if ref.shape[:2] == (h, w) else cv2.resize(ref, (w, h))

    hsv_u = cv2.cvtColor(user_w, cv2.COLOR_BGR2HSV)
    hsv_r = cv2.cvtColor(ref_w, cv2.COLOR_BGR2HSV)
    vu = hsv_u[..., 2].astype(np.int16)
    su = hsv_u[..., 1].astype(np.int16)
    vr = hsv_r[..., 2].astype(np.int16)
    sr = hsv_r[..., 1].astype(np.int16)
    # Exposure-match the photo's brightness to the reference for the DARK test only
    # (shadow vs print). Saturation is deliberately NOT normalised: holo reflections
    # skew a photo's saturation median arbitrarily, so a global match destroys the
    # signal — whitening uses an absolute "bare cardboard" test instead.
    mu, mr = float(np.median(vu[cover])), float(np.median(vr[cover]))
    vu_n = np.clip(vu * (mr / mu), 0, 255).astype(np.int16) if mu > 1 else vu

    # The homography is good to ~1-2px; at high-contrast print boundaries that
    # residual alone creates huge pixel diffs. Compare each photo pixel against the
    # most-forgiving value in the reference's 5x5 neighbourhood instead — real
    # damage blobs survive, misregistration slivers don't.
    k5 = np.ones((5, 5), np.uint8)
    vr_lo = cv2.erode(vr.astype(np.uint8), k5).astype(np.int16)
    ref_whiteish = ((sr < 50) & (vr > 180)).astype(np.uint8)
    ref_white_near = cv2.dilate(ref_whiteish, k5) > 0

    # Whitening, SELF-CALIBRATED: sample how THIS photo renders the border colour
    # (median over the mid-edge border bands — canonical coords, never holo, robust
    # to damage in the band) and flag pixels that lost that colour: far below the
    # photo's own border saturation but just as bright. Absolute thresholds fail
    # here — a bright exposure washes a yellow border to "bright+desat" everywhere.
    # If the border itself renders desaturated (silver/white frame, blown yellow),
    # whitening is unmeasurable and honestly reads 0.
    # ponytail: colour-border cards only; dark-border whitening needs its own model.
    o = int(min(h, w) * 0.015)
    bb = int(min(h, w) * 0.035)
    mid_h = slice(int(h * 0.35), int(h * 0.65))
    mid_w = slice(int(w * 0.35), int(w * 0.65))

    def band(m):
        return np.concatenate([m[o:o + bb, mid_w].ravel(), m[h - o - bb:h - o, mid_w].ravel(),
                               m[mid_h, o:o + bb].ravel(), m[mid_h, w - o - bb:w - o].ravel()])

    bs, bv = float(np.median(band(su))), float(np.median(band(vu)))
    if bs > 90:
        white = (su < 0.35 * bs) & (vu > bv - 30) & ~ref_white_near & cover
    else:
        white = np.zeros((h, w), bool)
    dark = (vu_n < vr_lo - 75) & cover
    defect = white | dark

    # Surface: photo-only small bright features (scratch/scuff), print cancelled.
    gu = cv2.cvtColor(user_w, cv2.COLOR_BGR2GRAY)
    gr_ = cv2.cvtColor(ref_w, cv2.COLOR_BGR2GRAY)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    th_u = cv2.morphologyEx(gu, cv2.MORPH_TOPHAT, k)
    th_r = cv2.dilate(cv2.morphologyEx(gr_, cv2.MORPH_TOPHAT, k), k5)
    scuff_map = (th_u > 30) & (th_r < 18) & cover

    size = int(min(h, w) * 0.09)
    r = int(min(h, w) * 0.055)
    base = _rounded_corner_mask(size, r)
    cmasks = [base, base[:, ::-1], base[::-1, :], base[::-1, ::-1]]     # TL, TR, BL, BR
    spots = [(0, 0), (w - size, 0), (0, h - size), (w - size, h - size)]

    def frac(m, ys, xs, region=None):
        patch = m[ys, xs]
        cov = cover[ys, xs] if region is None else (cover[ys, xs] & region)
        n = int(cov.sum())
        return float((patch & cov).sum()) / n if n > 50 else 0.0

    # In the canonical frame the corner arcs sit at known coordinates, so the rounded
    # mask tracks the real print rounding and per-pixel comparison works at corners too.
    corner_fr = [frac(defect, slice(y, y + size), slice(x, x + size), mk)
                 for (x, y), mk in zip(spots, cmasks)]
    o, t = int(min(h, w) * 0.015), int(min(h, w) * 0.035)
    strips = [(slice(o, o + t), slice(None)), (slice(h - o - t, h - o), slice(None)),
              (slice(None), slice(o, o + t)), (slice(None), slice(w - o - t, w - o))]
    edge_fr = [frac(defect, ys, xs) for ys, xs in strips]
    inner = (slice(int(h * 0.10), int(h * 0.90)), slice(int(w * 0.08), int(w * 0.92)))
    surf_fr = frac(scuff_map | dark, *inner)

    conf = "high" if inliers >= 60 else "medium"
    return {
        "corners": {"grade": _ref_frac_to_grade(max(corner_fr), _REF_CORNER_THR),
                    "per_corner": [round(x, 4) for x in corner_fr],
                    "confidence": conf, "method": "reference"},
        "edges": {"grade": _ref_frac_to_grade(max(edge_fr), _REF_EDGE_THR),
                  "per_edge": [round(x, 4) for x in edge_fr],
                  "confidence": conf, "method": "reference"},
        "surface": {"grade": _ref_frac_to_grade(surf_fr, _REF_SURF_THR),
                    "scuff": round(surf_fr, 4),
                    "confidence": conf, "method": "reference"},
        "inliers": inliers,
    }


# Grade maps: worst-region defect fraction -> PSA-style sub-grade. Calibrated on the
# labeled populations (clean fronts must land 9-10; damaged must drop). See
# scripts/calibrate_refdefects.py output in FINDINGS.md before changing.
_REF_CORNER_THR = [(0.010, 10), (0.035, 9), (0.08, 8), (0.15, 7), (0.25, 6), (0.38, 5), (0.50, 4)]
_REF_EDGE_THR = [(0.010, 10), (0.035, 9), (0.08, 8), (0.15, 7), (0.25, 6), (0.38, 5), (0.50, 4)]
# Measured: clean surf med 0.0011 / p75 0.033; damaged med 0.020 (18x median sep).
_REF_SURF_THR = [(0.008, 10), (0.020, 9), (0.045, 8), (0.09, 7), (0.15, 6), (0.24, 5), (0.34, 4)]


def _ref_frac_to_grade(frac: float, table) -> int:
    for thr, g in table:
        if frac <= thr:
            return g
    return 3


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
