"""Read the PSA label on a graded slab via OCR.

A graded card is already graded — the grade and cert number are printed on the
label. For slabbed cards we flatten the slab, crop the top label band, OCR it,
and parse PSA's grade word (which maps directly to a number) and cert number.
This is accurate, unlike re-grading the card through the plastic case.
"""
from __future__ import annotations

import re

import cv2
import numpy as np
import pytesseract

from src import grading

# PSA grade words -> numeric grade, matched against a PUNCTUATION/SPACE-STRIPPED form
# of the OCR text (so OCR noise like "NM -MT" or "GEM-MT" still matches). Ordered
# most-specific first so e.g. "NMMT" (8) isn't shadowed by "NM" (7) or "EXMT" by "EX".
_GRADE_WORDS = [
    ("PRISTINE", 10), ("GEMMT", 10), ("GEMMINT", 10),
    ("NMMT", 8), ("EXMT", 6), ("VGEX", 4),
    ("MINT", 9), ("NM", 7), ("EX", 5), ("VG", 3), ("GOOD", 2), ("POOR", 1),
]


def _flatten_slab(img: np.ndarray):
    h, w = img.shape[:2]
    scale = 1000.0 / max(h, w)
    small = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img.copy()
    if scale >= 1:
        scale = 1.0
    fa = small.shape[0] * small.shape[1]
    res = grading._segment_candidate(small, fa) or grading._edge_candidate(small, fa)
    if not res:
        return None
    quad = res[0] / scale
    rect = grading._order_points(quad)
    W, H = 560, 880
    m = cv2.getPerspectiveTransform(
        rect, np.float32([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]]))
    return cv2.warpPerspective(img, m, (W, H))


_OCR_TARGET_W = 1100   # tesseract is fastest around this width for the label band


def _ocr(label_bgr: np.ndarray) -> str:
    g = cv2.cvtColor(label_bgr, cv2.COLOR_BGR2GRAY)
    # The band comes from a fixed 560x880 warp, so the old blind `fx=3` always built a
    # constant ~1680px image that tesseract was slow to read. Scale to a target width
    # (~2x here) — still well above tesseract's ~30-40px cap-height sweet spot, but
    # roughly half the pixels to OCR. Only upscales; never shrinks below source.
    if g.shape[1] < _OCR_TARGET_W:
        s = _OCR_TARGET_W / g.shape[1]
        g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    # A PSA label is white text on red -> after Otsu the text is the bright minority
    # on a dark field. tesseract wants dark text on light, so invert when mostly dark.
    if g.mean() < 110:
        g = cv2.bitwise_not(g)
    return pytesseract.image_to_string(g, config="--psm 6")


def _label_band_region(img: np.ndarray):
    """Crop just the red PSA label band, found by colour (robust to framing).

    Slab-outline detection (`_flatten_slab`) grabs the high-contrast card *inside*
    the case when the slab fills the frame, so its top band is card art, not the
    label. The red band is a reliable colour cue regardless of framing — locate its
    row/column extent and return that crop. Returns None if no red band is found.
    """
    try:
        h, w = img.shape[:2]
        scale = 700.0 / max(h, w)
        small = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img
        if scale >= 1:
            scale = 1.0
        sh, sw = small.shape[:2]
        region = small[: int(sh * 0.5)]                     # label sits in the top half
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        red = (cv2.inRange(hsv, (0, 90, 70), (12, 255, 255)) |
               cv2.inRange(hsv, (168, 90, 70), (180, 255, 255)))
        rows = np.where(red.mean(axis=1) / 255.0 >= 0.30)[0]
        if len(rows) < 4:
            return None
        y0, y1 = int(rows.min()), int(rows.max())
        cols = np.where(red[y0:y1 + 1].mean(axis=0) / 255.0 >= 0.20)[0]
        if len(cols) < 10:
            return None
        x0, x1 = int(cols.min()), int(cols.max())
        py, px = int((y1 - y0) * 0.25), int((x1 - x0) * 0.03)   # pad past the red edges
        y0, y1 = max(0, y0 - py), min(sh - 1, y1 + py)
        x0, x1 = max(0, x0 - px), min(sw - 1, x1 + px)
        Y0, Y1, X0, X1 = (int(round(v / scale)) for v in (y0, y1, x0, x1))
        crop = img[Y0:Y1, X0:X1]
        return crop if crop.size else None
    except Exception:
        return None


def looks_like_psa_slab(img: np.ndarray) -> bool:
    """Cheap (~few ms) pre-check: is there a PSA-style red label *band* near the top?

    A PSA label is a solid horizontal red strip, so we look for a run of rows in the
    top third that are mostly red — not just scattered red artwork (which a simple
    red-pixel-fraction test wrongly flags ~1/3 of raw cards). Tuned to favour false
    POSITIVES: when unsure it returns True so the OCR still runs and a real slab is
    never missed. Independent of the card detector, so it's a safe gate signal.
    """
    try:
        h, w = img.shape[:2]
        scale = 480.0 / max(h, w)
        small = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img
        top = small[: max(8, int(small.shape[0] * 0.30))]   # top third
        hsv = cv2.cvtColor(top, cv2.COLOR_BGR2HSV)
        red = (cv2.inRange(hsv, (0, 90, 70), (12, 255, 255)) |
               cv2.inRange(hsv, (168, 90, 70), (180, 255, 255)))
        row_red = red.mean(axis=1) / 255.0                  # red fraction per row
        band_rows = int((row_red >= 0.45).sum())            # rows that are mostly red
        return band_rows >= max(4, int(top.shape[0] * 0.05))
    except Exception:
        return True    # never skip OCR on an error


def _parse_label(text: str):
    """Parse a PSA label from OCR text, or None if it isn't one.

    A graded slab must show a real marker — the "PSA" wordmark or an 8-9 digit cert
    number — NOT just a grade word: raw cards routinely carry grade-like tokens in
    their name/text ("Charizard ex" -> EX, "...V" -> VG), so a grade word alone would
    falsely stamp them as slabs and override their real grade. The grade word is then
    matched on a punctuation/space-stripped form so OCR noise ("NM -MT") still resolves.
    """
    compact = re.sub(r"[^A-Z0-9]", "", text.upper())
    # cert from the SPACED text so adjacent card numbers don't merge into a fake run
    certs = re.findall(r"(?<!\d)\d{8,9}(?!\d)", text)
    cert = max(certs, key=len) if certs else None
    if "PSA" not in compact and cert is None:
        return None                       # no slab marker -> treat as a raw card
    grade = grade_word = None
    for word, val in _GRADE_WORDS:
        if word in compact:
            grade, grade_word = val, word
            break
    return {"is_slab": True, "grader": "PSA", "grade": grade,
            "grade_label": grade_word, "cert": cert, "raw_text": text.strip()}


def read_slab_label(img: np.ndarray):
    """Return {is_slab, grader, grade, grade_label, cert, raw_text} or None.

    Reads the red label band directly first (works even when the slab fills the
    frame, where slab-outline detection grabs the card), then falls back to
    flattening the whole slab and reading its top band. Returns the first crop that
    yields a numeric grade, else any PSA/cert-only read.
    """
    crops = []
    region = _label_band_region(img)
    if region is not None and region.size:
        crops.append(region)
    slab = _flatten_slab(img)
    if slab is not None:
        crops.append(slab[: int(slab.shape[0] * 0.23)])

    best = None
    for crop in crops:
        parsed = _parse_label(_ocr(crop))
        if parsed is None:
            continue
        if parsed.get("grade") is not None:
            return parsed                 # confident: a grade word was read
        best = best or parsed             # keep a PSA/cert-only read as fallback
    return best
