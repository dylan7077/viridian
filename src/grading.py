"""Computer-vision grading of a Pokemon card photo.

PSA grades on four criteria: centering, corners, edges, surface.

Only *centering* is truly objective and measurable with classical CV — it is a
geometric border-ratio measurement. Corners, edges and surface are assessed with
heuristics here and reported with low confidence: treat them as a rough estimate,
not a real PSA sub-grade. The honest output is "a centering-driven estimate".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

import config


# --------------------------------------------------------------------------- #
# Card detection + perspective correction
# --------------------------------------------------------------------------- #
def _order_points(pts: np.ndarray) -> np.ndarray:
    """Return 4 points ordered: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]      # top-left has smallest x+y
    rect[2] = pts[np.argmax(s)]      # bottom-right has largest x+y
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]   # top-right has smallest y-x
    rect[3] = pts[np.argmax(diff)]   # bottom-left has largest y-x
    return rect


def _warp_to_card(img: np.ndarray, quad: np.ndarray) -> np.ndarray:
    rect = _order_points(quad)
    dst = np.array(
        [[0, 0], [config.CARD_W - 1, 0],
         [config.CARD_W - 1, config.CARD_H - 1], [0, config.CARD_H - 1]],
        dtype="float32",
    )
    m = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(img, m, (config.CARD_W, config.CARD_H))


CARD_ASPECT = 2.5 / 3.5   # ≈ 0.714, short side / long side


def _quad_from_contour(c: np.ndarray) -> np.ndarray:
    """Prefer a clean 4-point approximation; fall back to the oriented box."""
    peri = cv2.arcLength(c, True)
    for eps in (0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(c, eps * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2).astype("float32")
    return cv2.boxPoints(cv2.minAreaRect(c)).astype("float32")


def _aspect_fitness(quad: np.ndarray) -> float:
    """1.0 when the quad's aspect matches a real card, decaying away from it."""
    tl, tr, br, bl = _order_points(quad)
    wd = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    ht = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    if min(wd, ht) < 1:
        return 0.0
    ar = min(wd, ht) / max(wd, ht)
    return max(0.0, 1 - abs(ar - CARD_ASPECT) / 0.30)


def _edge_candidate(small, frame_area):
    gray = cv2.bilateralFilter(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 9, 75, 75)
    edges = cv2.dilate(cv2.Canny(gray, 30, 120), np.ones((3, 3), np.uint8), 1)
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) > 0.12 * frame_area]
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    return _quad_from_contour(c), cv2.contourArea(c) / frame_area


def _segment_candidate(small, frame_area):
    """Separate the card from the background by COLOUR distance from the
    background. The background colour is estimated from the photo's outer border
    (table, wall, sleeve), so this isolates the card whether it sits on wood,
    white, or any roughly uniform surface — where plain edge detection fails."""
    blur = cv2.GaussianBlur(small, (7, 7), 0)
    b = 12
    border = np.concatenate([blur[:b].reshape(-1, 3), blur[-b:].reshape(-1, 3),
                             blur[:, :b].reshape(-1, 3), blur[:, -b:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(blur.astype(np.int16) - bg, axis=2)
    dist = np.clip(dist, 0, 255).astype(np.uint8)
    # Otsu split of "far from background" (card) vs "near background"
    thr, mask = cv2.threshold(dist, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) > 0.12 * frame_area]
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    # fill holes so the inner art doesn't fragment the card blob
    filled = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(filled, [c], -1, 255, -1)
    c = max(cv2.findContours(filled, cv2.RETR_EXTERNAL,
                             cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
    return _quad_from_contour(c), cv2.contourArea(c) / frame_area


def _yellow_candidate(small, frame_area):
    """Locate the card directly by its yellow border. The yellow forms a frame
    ring; its outer contour is the card's outer edge — so this grabs just the
    card even inside a graded **slab** (ignoring the label and plastic case), and
    its 4 corners give a perspective-correcting homography when the card is
    tilted / not flat. Only fires on yellow-bordered cards."""
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    ym = cv2.inRange(hsv, (18, 60, 70), (42, 255, 255))
    ym = cv2.morphologyEx(ym, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    cnts, _ = cv2.findContours(ym, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) > 0.04 * frame_area]
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    filled = np.zeros(ym.shape, np.uint8)        # fill ring -> solid card blob
    cv2.drawContours(filled, [c], -1, 255, -1)
    c = max(cv2.findContours(filled, cv2.RETR_EXTERNAL,
                             cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
    return _quad_from_contour(c), cv2.contourArea(c) / frame_area


def _grabcut_candidate(small, frame_area):
    """Foreground isolation that ignores the background entirely. GrabCut models
    the card vs. the surface as separate colour distributions and graph-cuts them
    apart — so it finds the card even when its border barely contrasts with the
    surface (e.g. a light card on white marble), where edge/colour detectors fail.

    Heavier than the others (~1s), so it's only run as a fallback when the fast
    detectors come back with a weak, background-contaminated crop."""
    H, W = small.shape[:2]
    mask = np.zeros((H, W), np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    # Seed: assume the card occupies most of the frame, the outer ~6% is surface.
    rect = (int(W * 0.06), int(H * 0.06), int(W * 0.88), int(H * 0.88))
    cv2.grabCut(small, mask, rect, bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
    fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) > 0.12 * frame_area]
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    filled = np.zeros(fg.shape, np.uint8)        # solid blob, no inner holes
    cv2.drawContours(filled, [c], -1, 255, -1)
    c = max(cv2.findContours(filled, cv2.RETR_EXTERNAL,
                             cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
    return _quad_from_contour(c), cv2.contourArea(c) / frame_area


def _blue_back_quad(img: np.ndarray):
    """Locate a Pokémon card BACK by its blue field, returning a validated quad or None.

    Every back is a large, saturated-blue rectangle; bright windows, wood grain and
    patterned mats are NOT blue, so masking the blue isolates the card exactly where the
    generic foreground/edge detectors grab the background instead. We close hard over the
    Poké Ball / logo / text so the blue field becomes one solid blob, then fit a quad and
    accept it only if it's a real card shape — otherwise None, so the caller falls back."""
    h, w = img.shape[:2]
    scale = 1000.0 / max(h, w)
    small = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img.copy()
    frame_area = small.shape[0] * small.shape[1]
    hsv = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (85, 45, 40), (140, 255, 255))
    if (mask > 0).mean() < 0.06:                 # too little blue — not a back, bail
        return None
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((35, 35), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) > 0.10 * frame_area]
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    filled = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(filled, [c], -1, 255, -1)
    c = max(cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0],
            key=cv2.contourArea)
    quad = _quad_from_contour(c)
    if _aspect_fitness(quad) < 0.55:             # not card-shaped — let the caller fall back
        return None
    return quad / scale


def detect_back(img: np.ndarray, return_debug: bool = False):
    """Detect + flatten a card BACK. Tries the blue-field detector first (robust where the
    generic one grabs a bright window or wood grain), falling back to detect_card when no
    confident blue card is found — so it's never worse than the generic detector."""
    quad = _blue_back_quad(img)
    if quad is not None:
        warped = _warp_to_card(img, quad)
        if return_debug:
            fit = _aspect_fitness(_order_points(quad))
            return warped, {"method": "blueback", "coverage": 1.0,
                            "aspect_fit": round(float(fit), 3), "quality": "good",
                            "quad": (_order_points(quad)).tolist()}
        return warped
    return detect_card(img, return_debug=return_debug)


def _line_intersection(a, b):
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-6:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    return px, py


def _hough_candidate(small, frame_area):
    """Find the card by its four straight edges via the Hough transform.

    A trading card has four long, straight, roughly-perpendicular borders; a couch
    weave, wood grain or lamp glare do not. We detect line segments, split them into
    near-horizontal and near-vertical groups, take the extreme line on each side and
    intersect them into a tight, perspective-correct quad. This handles tilt natively
    and crops right to the card edge — where colour/contour detectors leave background
    wedges that wreck the centering and edge measurements. Returns (quad, coverage) in
    `small` coords, or None when four clean sides aren't found (busy/low-contrast)."""
    H, W = small.shape[:2]
    gray = cv2.bilateralFilter(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 9, 75, 75)
    edges = cv2.dilate(cv2.Canny(gray, 40, 120), np.ones((3, 3), np.uint8), 1)
    diag = np.hypot(H, W)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=70,
                            minLineLength=diag * 0.22, maxLineGap=40)
    if lines is None:
        return None
    hor, ver = [], []
    for l in lines[:, 0, :]:
        x1, y1, x2, y2 = l
        ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if ang < 35 or ang > 145:
            hor.append(l)
        elif abs(ang - 90) < 35:
            ver.append(l)
    if len(hor) < 2 or len(ver) < 2:
        return None
    top = min(hor, key=lambda l: (l[1] + l[3]) / 2)
    bot = max(hor, key=lambda l: (l[1] + l[3]) / 2)
    left = min(ver, key=lambda l: (l[0] + l[2]) / 2)
    right = max(ver, key=lambda l: (l[0] + l[2]) / 2)
    corners = [_line_intersection(top, left), _line_intersection(top, right),
               _line_intersection(bot, right), _line_intersection(bot, left)]
    if any(c is None for c in corners):
        return None
    quad = np.array(corners, dtype="float32")
    # Reject anything that isn't a plausible card: must be convex, fill a real slice
    # of the frame, and have a card-like aspect once ordered.
    if not cv2.isContourConvex(quad.astype(np.int32)):
        return None
    cov = cv2.contourArea(quad) / frame_area
    if cov < 0.10 or cov > 0.99 or _aspect_fitness(quad) < 0.55:
        return None
    return quad, cov


def detect_card(img: np.ndarray, return_debug: bool = False):
    """Find the card in a photo and warp it flat to the canonical size.

    Detectors, scored by aspect-ratio fitness × coverage:
      * **hough** — the card's four straight edges (primary): tight, deskewed crop
        even when the card fills less than half a hand-held photo. Background wedges
        from a loose crop are what wreck centering/edges, so a clean quad wins here.
      * **yellow** — the yellow border itself (grabs the card inside a slab and
        handles tilt); boosted so it wins over the slab outline when present.
      * **segment** — colour distance from the background (card on a plain table).
      * **edge** — contours (card on a busy/contrasty background).
      * **grabcut** — foreground isolation, run only as a fallback when the fast
        detectors give a weak crop (low coverage or non-card shape).
    Falls back to the whole frame if none is plausible. The returned debug carries
    `coverage`, `aspect_fit`, and a `quality` flag so callers can warn on a weak read.
    """
    h, w = img.shape[:2]
    scale = 1000.0 / max(h, w)
    if scale < 1:
        small = cv2.resize(img, None, fx=scale, fy=scale)
    else:                       # already small enough — don't upscale or rescale coords
        small, scale = img.copy(), 1.0
    frame_area = small.shape[0] * small.shape[1]

    # Hough edges first — when it locks onto four clean sides it gives the tightest,
    # most deskewed crop, so prefer it outright over the colour/contour detectors.
    try:
        hough = _hough_candidate(small, frame_area)
    except Exception:
        hough = None
    if hough is not None:
        hquad, hcov = hough
        hfit = _aspect_fitness(hquad)
        quad = _order_points(hquad) / scale
        warped = _warp_to_card(img, quad)
        quality = "good" if (hcov >= 0.20 and hfit >= 0.60) else "low"
        if return_debug:
            return warped, {"method": "hough", "coverage": round(float(hcov), 3),
                            "aspect_fit": round(float(hfit), 3), "quality": quality,
                            "quad": quad.tolist()}
        return warped

    cands = []          # (score, cov, quad, name, fit)
    yellow_pick = None
    # (name, function, min coverage)
    detectors = (("yellow", _yellow_candidate, 0.04),
                 ("segment", _segment_candidate, 0.12),
                 ("edge", _edge_candidate, 0.12))
    for name, fn, min_cov in detectors:
        try:
            res = fn(small, frame_area)
        except Exception:
            res = None
        if not res:
            continue
        quad, cov = res
        fit = _aspect_fitness(quad)
        # A clear card-aspect yellow region IS the card (even inside a slab) —
        # prefer it outright over the slab/background outline. Require it to fill a
        # real fraction of the frame: a true yellow border (filled ring) is ~30%+ of
        # the card area, whereas a small yellow accent (an energy symbol on a modern
        # silver-bordered card) is only a few %, and must NOT hijack detection.
        if name == "yellow" and fit > 0.62 and cov > 0.30:
            yellow_pick = (cov, quad, fit)
        if min_cov < cov < 0.99 and fit > 0.45:
            cands.append((fit * min(cov, 0.8), cov, quad, name, fit))

    # If no confident yellow border and the best fast crop is weak — too small a
    # slice of the frame or not card-shaped — fall back to GrabCut foreground
    # isolation, which ignores the background instead of relying on its contrast.
    best_cov = max((c[1] for c in cands), default=0.0)
    best_score = max((c[0] for c in cands), default=0.0)
    if not yellow_pick and (not cands or best_cov < 0.55 or best_score < 0.45):
        try:
            gc = _grabcut_candidate(small, frame_area)
        except Exception:
            gc = None
        if gc:
            gq, gcov = gc
            gfit = _aspect_fitness(gq)
            if gfit > 0.45 and 0.12 < gcov < 0.99:
                cands.append((gfit * min(gcov, 0.8), gcov, gq, "grabcut", gfit))

    if yellow_pick:
        coverage, quad, method, fit = (yellow_pick[0], yellow_pick[1],
                                       "yellow", yellow_pick[2])
    elif cands:
        cands.sort(key=lambda t: t[0], reverse=True)
        _, coverage, quad, method, fit = cands[0]
    else:
        quad = np.array([[0, 0], [small.shape[1] - 1, 0],
                         [small.shape[1] - 1, small.shape[0] - 1],
                         [0, small.shape[0] - 1]], dtype="float32")
        method, coverage, fit = "fullframe", 1.0, 0.0

    # A trustworthy read needs both a decent slice of the frame and a card shape.
    quality = "good" if (coverage >= 0.45 and fit >= 0.60) else "low"

    quad = quad / scale
    warped = _warp_to_card(img, quad)
    if return_debug:
        return warped, {"method": method, "coverage": round(float(coverage), 3),
                        "aspect_fit": round(float(fit), 3), "quality": quality,
                        "quad": quad.tolist()}
    return warped


# --------------------------------------------------------------------------- #
# Centering (the measurable, reliable metric)
# --------------------------------------------------------------------------- #
def _centering_to_grade(worst_pct: float) -> int:
    """Map the worst-axis (most off-center) centering % to a PSA grade.

    PSA grades centering on "the percent of difference at the most off-center
    part of the card" — i.e. the worse of the L/R and T/B axes (not the average).
    Front tolerances per PSA, including the documented 5% leeway for NM-7+:
        10 ≤60/40 · 9 ≤65/35 · 8 ≤70/30 · 7 ≤75/25 · 6 ≤80/20 · 5 ≤85/15 ...
    Sources: PSA grading standards; pokegradeuk reports; acegrading scale.
    """
    table = [(60, 10), (65, 9), (70, 8), (75, 7), (80, 6),
             (85, 5), (88, 4), (90, 3), (93, 2)]
    for pct, grade in table:
        if worst_pct <= pct:
            return grade
    return 1


def _border_widths(mask, w, h, bg_present, lo=0.30, hi=0.70, gap=6):
    """Border width on each side by scanning inward from each edge across a central
    band of rows/cols, taking the median run of border-coloured pixels.

    This replaces an older hole-based search that required the inner content to be a
    single clean hole ≥25% of the card — which failed on essentially every real card,
    because artwork that shares the border colour (a yellow Pokémon inside a yellow
    border, silver UI inside a silver border, text, holo) fragments that hole. Scanning
    inward from each edge instead is immune to interior content.

    `bg_present` widens the leading-skip so a tilted/loose crop's background wedge is
    stepped over before the real border is measured; on a tight crop only the thin
    white cut-edge is skipped. Returns (left,right,top,bottom) px, or None if a side
    can't be resolved (e.g. background bleed swallowed it — honestly unmeasurable)."""
    m = mask > 0

    def run(lm, maxskip):
        n, i = len(lm), 0
        while i < n and i < maxskip and not lm[i]:   # skip cut-edge / background bleed
            i += 1
        start, last, miss = i, i - 1, 0
        while i < n:
            if lm[i]:
                last, miss = i, 0
            else:
                miss += 1
                if miss > gap:
                    break
            i += 1
        return max(0, last - start + 1)

    skip = int(min(w, h) * 0.15) if bg_present else gap
    rows = range(int(h * lo), int(h * hi))
    cols = range(int(w * lo), int(w * hi))
    left   = float(np.median([run(m[r, :],    skip) for r in rows]))
    right  = float(np.median([run(m[r, ::-1], skip) for r in rows]))
    top    = float(np.median([run(m[:, c],    skip) for c in cols]))
    bottom = float(np.median([run(m[::-1, c], skip) for c in cols]))
    if min(left, right, top, bottom) < 1:
        return None
    if max(left, right) > 0.4 * w or max(top, bottom) > 0.4 * h:
        return None
    return left, right, top, bottom


def _estimate_bg(card):
    """(bg_colour, present): the warped frame's extreme-corner colour, and whether it
    looks like real background — the uniform wedges a loose/tilted crop leaves behind.
    When absent (a tight crop), callers skip background rejection so a card's own
    border isn't mistaken for background."""
    h, w = card.shape[:2]
    c = int(min(h, w) * 0.06)
    corners = np.concatenate([card[:c, :c].reshape(-1, 3), card[:c, -c:].reshape(-1, 3),
                              card[-c:, :c].reshape(-1, 3), card[-c:, -c:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    spread = float(np.median(np.linalg.norm(corners.astype(np.int16) - bg, axis=1)))
    return bg, spread < 45


def _generic_border_mask(card):
    """Mask pixels near the border colour, sampled from the middle of each edge
    (corners can clip background on a rotated photo). Works for any uniform
    border colour — silver, blue, etc. — not just yellow."""
    h, w = card.shape[:2]
    band = max(int(min(h, w) * 0.025), 4)
    mw = slice(int(w * 0.35), int(w * 0.65))
    mh = slice(int(h * 0.35), int(h * 0.65))
    samp = np.concatenate([
        card[:band, mw].reshape(-1, 3), card[-band:, mw].reshape(-1, 3),
        card[mh, :band].reshape(-1, 3), card[mh, -band:].reshape(-1, 3)])
    bc = np.median(samp, axis=0)
    diff = np.linalg.norm(card.astype(np.int16) - bc, axis=2)
    return (diff < 50).astype(np.uint8) * 255


def _frame_widths_gradient(card, lo=0.30, hi=0.70):
    """Border width per side by finding the first strong straight edge inward from each
    card edge (the frame → printed-design transition), via gradient projection.

    Border *colour* is unreliable on modern cards — silver/holo frames, name banners and
    art all blur a colour mask. The frame→content boundary is a straight line, though, so
    we project the perpendicular gradient over a central band and take the first strong
    peak in from each edge. Independent of border colour, so it works on silver, gold,
    holo and yellow alike. Returns (left,right,top,bottom) px, or None if a side is flat
    (full-art / no frame). The left/right axis is the dependable signal (the frame is
    L–R symmetric); top/bottom sits near-neutral on the vertically-asymmetric layout, so
    it rarely dominates the worst-axis grade — which is the honest behaviour."""
    h, w = card.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(card, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    colp = gx[int(h * lo):int(h * hi)].sum(0)
    rowp = gy[:, int(w * lo):int(w * hi)].sum(0)

    def first(prof, dim):
        a, b = int(dim * 0.03), int(dim * 0.45)
        seg = prof[a:b]
        if len(seg) < 3:
            return None
        idx = np.where(seg > seg.max() * 0.45)[0]
        return a + int(idx[0]) if len(idx) else None

    L, Rr = first(colp, w), first(colp[::-1], w)
    T, Bb = first(rowp, h), first(rowp[::-1], h)
    if None in (L, Rr, T, Bb):
        return None
    left, right, top, bottom = float(L), float(Rr), float(T), float(Bb)
    if max(left, right) > 0.45 * w or max(top, bottom) > 0.45 * h:
        return None
    return left, right, top, bottom


def measure_centering(card: np.ndarray) -> dict:
    """Measure the border width on each side — what PSA centering grades.

    Primary: a gradient frame-finder (border-colour-independent) that locates the
    frame→design edge on a clean, deskewed crop. Fallback: the yellow / generic
    colour-border scan for cases it can't resolve. PSA grades on the most off-center
    axis (worst of L/R, T/B), not the average — so we report that.
    """
    h, w = card.shape[:2]
    hsv = cv2.cvtColor(card, cv2.COLOR_BGR2HSV)
    _, bg_present = _estimate_bg(card)

    borders, method = None, None
    if not bg_present:                      # gradient needs a clean, tight crop
        borders = _frame_widths_gradient(card)
        method = "frame"
    ymask = cv2.inRange(hsv, (18, 60, 70), (42, 255, 255))
    if borders is None and (ymask > 0).mean() >= 0.10:
        borders = _border_widths(ymask, w, h, bg_present)
        method = "yellow"
    if borders is None:
        borders = _border_widths(_generic_border_mask(card), w, h, bg_present)
        method = "border"
    if borders is None:
        return {"ok": False, "grade": None,
                "note": "Couldn't isolate a border to measure (full-art card, "
                        "glare, or low contrast). Try a flatter, well-lit photo."}

    left, right, top, bottom = borders
    lr = 100 * max(left, right) / (left + right)
    tb = 100 * max(top, bottom) / (top + bottom)
    worst = max(lr, tb)

    def fmt(a, b):
        return f"{round(100*a/(a+b))}/{round(100*b/(a+b))}"

    return {
        "ok": True,
        "grade": _centering_to_grade(worst),
        "left_right": fmt(left, right),
        "top_bottom": fmt(top, bottom),
        "worst_pct": round(worst, 1),
        "confidence": ("high" if method == "yellow" else "medium") if not bg_present else "low",
        "axis_note": "L/R is the reliable axis; T/B is approximate on modern layouts",
        "method": method,
        "frame": {"lx": int(left), "rx": int(w - right),
                  "ty": int(top), "by": int(h - bottom)},
        "borders_px": {"left": int(left), "right": int(right),
                       "top": int(top), "bottom": int(bottom)},
    }


# --------------------------------------------------------------------------- #
# Corners / edges / surface (heuristic, low confidence)
# --------------------------------------------------------------------------- #
def _whitening_to_grade(frac: float) -> int:
    for thr, grade in [(0.005, 10), (0.02, 9), (0.05, 8), (0.10, 7),
                       (0.18, 6), (0.28, 5), (0.40, 4)]:
        if frac <= thr:
            return grade
    return 3


def _wear_frac(v_patch, s_patch, bgr_patch=None, bg=None, region=None) -> float:
    """Fraction of a patch showing wear: chalky whitening OR dark/dirty damage.

    Whitening is measured *relative to the patch's own brightness*, not against a
    fixed threshold. A pixel only counts as wear if it's a desaturated outlier —
    markedly brighter than this edge's median. That way a uniformly bright border
    (white/silver), holo/foil shine and reverse-holo sparkle are read as "expected"
    rather than damage, while real white fraying — bright where its surroundings are
    not — still stands out. `bg` (with the colour patch) drops crop-bleed background;
    `region` restricts the count to true card pixels (e.g. a rounded-corner mask)."""
    valid = np.ones(v_patch.shape, bool) if region is None else region.copy()
    if bg is not None and bgr_patch is not None:
        valid &= np.linalg.norm(bgr_patch.astype(np.int16) - bg, axis=2) >= 40
    n = int(valid.sum())
    if n < 10:
        return 0.0
    med = float(np.median(v_patch[valid]))
    hi = max(med + 35, 180)                      # whitening must out-bright its surroundings
    white = (v_patch > hi) & (s_patch < 45) & valid
    dark = (v_patch < min(med - 60, 55)) & valid
    return float((white | dark).sum()) / n


def _rounded_corner_mask(size: int, r: int) -> np.ndarray:
    """Boolean (size×size) mask of card material for a TOP-LEFT rounded corner: True
    everywhere except the wedge outside the corner's rounding arc. Sampling through
    this follows the real card shape, so a square box no longer grabs the background
    that sits beyond a Pokémon card's rounded corner."""
    yy, xx = np.mgrid[0:size, 0:size]
    outside = (xx < r) & (yy < r) & ((xx - r) ** 2 + (yy - r) ** 2 > r * r)
    return ~outside


def assess_corners(card: np.ndarray) -> dict:
    """Estimate corner wear from whitening (light fraying) and dark/dirty damage,
    sampling through a rounded-corner mask so only real card material is measured."""
    h, w = card.shape[:2]
    hsv = cv2.cvtColor(card, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    bg, present = _estimate_bg(card)
    bg = bg if present else None
    size = int(min(h, w) * 0.09)
    r = int(min(h, w) * 0.055)
    base = _rounded_corner_mask(size, r)
    masks = [base, base[:, ::-1], base[::-1, :], base[::-1, ::-1]]   # TL, TR, BL, BR
    spots = [(0, 0), (w - size, 0), (0, h - size), (w - size, h - size)]
    wear = [_wear_frac(v[ry:ry + size, rx:rx + size], s[ry:ry + size, rx:rx + size],
                       card[ry:ry + size, rx:rx + size], bg, mk)
            for (rx, ry), mk in zip(spots, masks)]
    return {"grade": _whitening_to_grade(max(wear)),
            "per_corner": [round(x, 3) for x in wear], "confidence": "low"}


def assess_edges(card: np.ndarray) -> dict:
    """Estimate edge wear (whitening or dark damage) along the four borders. Strips
    are inset off the very edge so the white cut-edge and any crop bleed don't read
    as wear, and background colour is dropped."""
    h, w = card.shape[:2]
    hsv = cv2.cvtColor(card, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    bg, present = _estimate_bg(card)
    bg = bg if present else None
    o = int(min(h, w) * 0.02)                    # skip the cut-edge / bleed sliver
    t = int(min(h, w) * 0.03)
    strips = [(slice(o, o + t), slice(None)), (slice(h - o - t, h - o), slice(None)),
              (slice(None), slice(o, o + t)), (slice(None), slice(w - o - t, w - o))]
    wear = [_wear_frac(v[ys, xs], s[ys, xs], card[ys, xs], bg) for ys, xs in strips]
    return {"grade": _whitening_to_grade(max(wear)),
            "per_edge": [round(x, 3) for x in wear], "confidence": "low"}


def _scuff_to_grade(scuff: float) -> int:
    for thr, grade in [(0.08, 10), (0.13, 9), (0.18, 8), (0.24, 7), (0.30, 6),
                       (0.37, 5), (0.44, 4), (0.50, 3), (0.58, 2)]:
        if scuff <= thr:
            return grade
    return 1


def assess_surface(card: np.ndarray) -> dict:
    """Estimate surface condition from scratch/scuff density.

    A morphological top-hat / black-hat highlights small bright and dark features
    (scratches, scuffing, print lines) against the smooth print, independent of
    the large artwork. Heavy holo wear scores high; a clean card scores low.
    """
    h, w = card.shape[:2]
    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    inner = gray[int(h * 0.12):int(h * 0.88), int(w * 0.10):int(w * 0.90)]
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    tophat = cv2.morphologyEx(inner, cv2.MORPH_TOPHAT, k)
    blackhat = cv2.morphologyEx(inner, cv2.MORPH_BLACKHAT, k)
    scuff = float(((tophat > 28) | (blackhat > 28)).mean())
    blown = float((inner > 248).mean())
    return {"grade": _scuff_to_grade(scuff), "scuff": round(scuff, 3),
            "blown_highlights": round(blown, 3), "confidence": "low"}


# --------------------------------------------------------------------------- #
# Capture quality (garbage-in gate)
# --------------------------------------------------------------------------- #
def assess_capture_quality(card: np.ndarray, source_card_px: Optional[int] = None) -> dict:
    """Flag a photo too poor to grade reliably, BEFORE trusting any measurement. A blurry
    or glare-blown shot still detects geometrically but yields garbage centering/surface —
    the real reliability killer is bad input, not the algorithm (see FINDINGS.md).

    Measured on the fixed-size warped card so thresholds are stable across photos:
      * blur — variance of the Laplacian. Sharp cards run 240-800; out-of-focus drops < 80.
      * glare — fraction of blown, desaturated highlights (holo/lamp glare).
      * exposure — median brightness too dark / washed out.
      * resolution — `source_card_px` is the card's height in the ORIGINAL photo; below
        ~700px the grade drifts (detection precision + lost detail), so warn rather than
        silently grade inconsistently (see the resolution-sensitivity note in FINDINGS).
    ponytail: thresholds calibrated on this warp size; retune if the warp dims change."""
    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(card, cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    glare = float(((v > 250) & (s < 30)).mean())
    bright = float(np.median(v))

    warnings = []
    if blur < 60:
        warnings.append("Photo looks out of focus — hold steady and retake.")
    if glare > 0.03:
        warnings.append("Glare is washing out part of the card — tilt away from the light.")
    if bright < 45:
        warnings.append("Photo is too dark — use more even light.")
    elif bright > 225:
        warnings.append("Photo is overexposed — reduce the light or move back.")
    if source_card_px is not None and source_card_px < 700:
        warnings.append("The card is small/low-resolution in this photo — fill the frame "
                        "and get closer for a more precise grade.")
    return {"ok": not warnings, "blur": round(blur, 1),
            "glare": round(glare, 3), "brightness": round(bright, 1),
            "card_px": source_card_px, "warnings": warnings}


# --------------------------------------------------------------------------- #
# Combined result
# --------------------------------------------------------------------------- #
@dataclass
class GradeResult:
    overall: Optional[int]
    centering: dict
    corners: dict
    edges: dict
    surface: dict
    detected: bool = True
    warped: Optional[np.ndarray] = field(default=None, repr=False)
    detect_info: dict = field(default_factory=dict)
    capture: dict = field(default_factory=dict)


# How much to trust a sub-grade by its stated confidence.
_TRUST = {"high": 1.0, "medium": 0.65, "low": 0.30}


def _combine(centering, corners, edges, surface) -> Optional[int]:
    """Anchor the overall on the signals that actually predict condition.

    Validated against real labeled cards (scripts/validate_labeled.py): centering and
    SURFACE separate clean from damaged (surface +2.2 grades), but corner/edge heuristics
    show ~0 separation (corners -0.1, edges -0.5) — noise that only false-penalises good
    cards. So corners/edges are excluded from the grade math (still reported as low-conf
    info via GradeResult); the overall is a confidence-weighted blend of centering (anchor)
    + surface (down-pull evidence). A low-confidence surface read only pulls DOWN on strong
    evidence, never hard-caps. `corners`/`edges` args are kept for signature compatibility."""
    cen_conf = centering.get("confidence")
    cen_trust = _TRUST.get(cen_conf, 0.65)
    # Centering only anchors when it's a trustworthy read; a loose-crop centering is
    # an artifact, so it drops to a weak input rather than driving the grade.
    cen_base = 0.55 if cen_trust >= 0.5 else 0.20

    subs = []   # (grade, base_weight, trust)
    if centering.get("grade") is not None:
        subs.append((centering["grade"], cen_base, cen_trust))
    # BLIND corner/edge heuristics are EXCLUDED from the grade math. Validated against real
    # labeled cards (scripts/validate_labeled.py): ~0 clean-vs-damaged separation (corners
    # -0.1, edges -0.5) — noise that only false-penalises good cards. They stay REPORTED as
    # low-confidence info. Surface DOES separate real damage (+2.2 grades, 81% balanced acc),
    # so it remains the down-pull evidence alongside centering.
    #
    # REFERENCE-aligned corner/edge reads (method='reference', refgrade.assess_defects) are
    # a different animal: the print cancels in the aligned diff, so whitening/damage is a
    # real measurement — those DO count, calibrated on the labeled populations.
    wear = [(surface["grade"], 0.30, _TRUST.get(surface.get("confidence"), 0.30))]
    for sub in (corners, edges):
        if sub.get("method") == "reference" and sub.get("grade") is not None:
            wear.append((sub["grade"], 0.25, _TRUST.get(sub.get("confidence"), 0.30)))
    subs += wear

    wsum = sum(bw * tr for _, bw, tr in subs)
    if wsum == 0:
        return None
    anchor = sum(g * bw * tr for g, bw, tr in subs) / wsum

    # A low-confidence WEAR signal may pull the grade down partially, and only on a
    # clear defect — never hard-cap. (A noisy centering is not treated as evidence.)
    penalty = 0.0
    for g, _, tr in wear:
        if tr < 0.5 and g <= 4:
            penalty = max(penalty, (anchor - g) * 0.35)
    # Only a reliably-measured severe defect caps the grade near that sub-grade.
    cap = 10
    for g, _, tr in subs:
        if tr >= 0.5 and g <= 3:
            cap = min(cap, g + 1)
    # PSA-style: one reliably-measured weak area drops the card to (about) that grade —
    # "a card is only as good as its worst feature", with the documented one-grade
    # leniency. Only reference-quality wear reads earn this power; heuristics never cap.
    for g, _, tr in wear:
        if tr >= 0.65 and g <= 8:
            cap = min(cap, g + 1)

    overall = min(anchor - penalty, cap)
    return int(max(1, min(10, round(overall))))


# --------------------------------------------------------------------------- #
# Visualization overlay (annotated detection image)
# --------------------------------------------------------------------------- #
_GREEN = (161, 224, 52)    # viridian accent, BGR
_GOLD = (120, 201, 231)
_WHITE = (240, 243, 234)


def _put(img, text, org, color=_WHITE, scale=0.42):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def annotate_detection(card: np.ndarray, centering: dict,
                       corners: dict, edges: dict) -> np.ndarray:
    """Draw the analysis overlay: centering frame, border measurements,
    corner sample boxes and edge strips on the flattened card."""
    vis = card.copy()
    h, w = vis.shape[:2]

    # translucent edge strips (where edge wear is sampled)
    t = int(min(h, w) * 0.03)
    overlay = vis.copy()
    for r in [(0, 0, w, t), (0, h - t, w, h), (0, 0, t, h), (w - t, 0, w, h)]:
        cv2.rectangle(overlay, (r[0], r[1]), (r[2], r[3]), _GREEN, -1)
    cv2.addWeighted(overlay, 0.14, vis, 0.86, 0, vis)

    # corner sample boxes
    size = int(min(h, w) * 0.09)
    for cx, cy in [(0, 0), (w - size, 0), (0, h - size), (w - size, h - size)]:
        cv2.rectangle(vis, (cx, cy), (cx + size, cy + size), _GOLD, 2)

    # centering frame + border measurements
    if centering.get("ok"):
        f, b = centering["frame"], centering["borders_px"]
        lx, rx, ty, by = f["lx"], f["rx"], f["ty"], f["by"]
        mx, my = w // 2, h // 2
        cv2.rectangle(vis, (lx, ty), (rx, by), _GREEN, 2)
        cv2.line(vis, (0, my), (lx, my), _GREEN, 1)
        cv2.line(vis, (rx, my), (w, my), _GREEN, 1)
        cv2.line(vis, (mx, 0), (mx, ty), _GREEN, 1)
        cv2.line(vis, (mx, by), (mx, h), _GREEN, 1)
        _put(vis, f"{b['left']}", (4, my - 6))
        _put(vis, f"{b['right']}", (rx + (w - rx) // 2 - 10, my - 6))
        _put(vis, f"{b['top']}", (mx + 5, max(ty // 2, 12)))
        _put(vis, f"{b['bottom']}", (mx + 5, by + (h - by) // 2))
        _put(vis, f"L/R {centering['left_right']}", (8, 20), _GREEN, 0.5)
        _put(vis, f"T/B {centering['top_bottom']}", (8, 40), _GREEN, 0.5)
    return vis


def warp_from_corners(img: np.ndarray, corners) -> np.ndarray:
    """Warp to the canonical card using 4 user-supplied corners (pixel coords,
    any order). Lets the user align the card manually for a perfect crop."""
    return _warp_to_card(img, np.array(corners, dtype="float32"))


def detect_corners(img: np.ndarray):
    """Auto-detect the card's 4 corners, normalised to 0..1 and ordered
    TL, TR, BR, BL — used to seed the manual align tool. None if nothing found."""
    _, dbg = detect_card(img, return_debug=True)
    quad = dbg.get("quad")
    if not quad or len(quad) != 4:
        return None
    rect = _order_points(np.array(quad, dtype="float32"))
    h, w = img.shape[:2]
    return [[float(x) / w, float(y) / h] for x, y in rect]


def grade_card(img: np.ndarray, manual_corners=None) -> GradeResult:
    """Full grading pipeline for a BGR image. If manual_corners (4 pixel points)
    are given, use them for the warp instead of auto-detection."""
    if manual_corners is not None and len(manual_corners) == 4:
        card = warp_from_corners(img, manual_corners)
        dbg = {"method": "manual", "coverage": 1.0,
               "quad": [[float(x), float(y)] for x, y in manual_corners]}
    else:
        card, dbg = detect_card(img, return_debug=True)
    if card is None:
        return GradeResult(None, {"ok": False}, {}, {}, {}, detected=False)

    centering = measure_centering(card)
    corners = assess_corners(card)
    edges = assess_edges(card)
    surface = assess_surface(card)
    overall = _combine(centering, corners, edges, surface)
    # Card's height in the ORIGINAL photo (mean of the quad's two vertical sides) — drives
    # the low-resolution capture warning. None if no quad (manual w/o detection edge cases).
    card_px = None
    quad = dbg.get("quad")
    if quad and len(quad) == 4:
        try:
            r = _order_points(np.array(quad, dtype="float32"))   # TL,TR,BR,BL
            card_px = int(round((np.linalg.norm(r[3] - r[0]) + np.linalg.norm(r[2] - r[1])) / 2))
        except Exception:
            card_px = None
    return GradeResult(overall, centering, corners, edges, surface,
                       warped=card, detect_info=dbg,
                       capture=assess_capture_quality(card, card_px))
