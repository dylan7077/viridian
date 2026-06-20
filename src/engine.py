"""High-level pipeline shared by every front-end (Discord bot, web app, CLI).

    image bytes / BGR array  ->  grade  ->  identify  ->  price  ->  result dict

Keeping this in one place means the bot and the website always agree.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import cv2
import numpy as np

import config

_log = logging.getLogger("viridian.slab")
from src import grading, pricing, slab, graded_pricing, refgrade, onnx_grader
from src.matching import CardIndex
from src.orb_index import OrbIndex


def _to_data_uri(img: np.ndarray) -> Optional[str]:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return None
    return "data:image/png;base64," + base64.b64encode(buf).decode()

# Loaded once and reused (the index can be large).
_index: Optional[CardIndex] = None


def get_index() -> CardIndex:
    global _index
    if _index is None:
        _index = CardIndex()
    return _index


_orb_index: Optional[OrbIndex] = None


def get_orb_index() -> Optional[OrbIndex]:
    global _orb_index
    if _orb_index is None:
        _orb_index = OrbIndex()
    return _orb_index


def decode_image(data: bytes) -> Optional[np.ndarray]:
    arr = np.frombuffer(data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _apply_real_graded(value: dict, card: dict, grade) -> None:
    """Override the rough graded estimate with real PSA prices (eBay/Cardmarket)
    when the graded-price API has them. Leaves the raw estimate untouched."""
    if not value or not value.get("ok") or not value.get("values"):
        return
    try:
        g = graded_pricing.get_graded(card.get("name"), card.get("number"),
                                      card.get("id"), card.get("set"), grade)
    except Exception:
        g = None
    if not g:
        return
    usd = g["usd"] if g["usd"] is not None else (g["eur"] / 0.92 if g["eur"] else None)
    eur = g["eur"] if g["eur"] is not None else (usd * 0.92 if usd else None)
    real = {"USD": usd, "EUR": eur, "GBP": (usd * 0.79 if usd else None)}
    for e in value["values"]:
        if real.get(e["currency"]) is not None:
            e["graded"] = round(real[e["currency"]], 2)
    value["graded_source"] = g["source"]
    value["graded_real"] = True


def process_image(img: np.ndarray, corners=None) -> dict:
    """Run the full grade + identify + price pipeline on a BGR image.

    corners (optional): 4 [x, y] points NORMALISED to 0..1, supplied by the
    manual align tool. Converted to pixels and used for the warp.
    """
    manual = None
    if corners and len(corners) == 4:
        h, w = img.shape[:2]
        try:
            manual = [[float(x) * w, float(y) * h] for x, y in corners]
        except Exception:
            manual = None
    result = grading.grade_card(img, manual_corners=manual)
    if not result.detected:
        return {
            "ok": False,
            "stage": "detect",
            "message": "Couldn't find a card in that photo. Try a flat, well-lit "
                       "shot of the whole card on a contrasting background.",
        }

    out: dict = {
        "ok": True,
        "grade": {
            "overall": result.overall,
            "centering": result.centering,
            "corners": result.corners,
            "edges": result.edges,
            "surface": result.surface,
        },
        "match": None,
        "value": None,
        "detection": result.detect_info,
        "overlay": _to_data_uri(grading.annotate_detection(
            result.warped, result.centering, result.corners, result.edges)),
    }

    # Trained CNN prediction (ONNX, fast CPU). Surfaced ALONGSIDE the heuristic grade for
    # now — not replacing it — until the model is trusted. Inert if the model isn't present.
    try:
        cnn = onnx_grader.grade(result.warped)
        if cnn:
            out["grade"]["cnn"] = cnn
    except Exception:
        pass

    cov = result.detect_info.get("coverage", 1.0)
    if cov < 0.20:
        out["detection_warning"] = (
            f"Only {cov:.0%} of the photo was identified as the card — it may be "
            "cropped or zoomed in. Frame the whole card with a margin around it.")

    # If it's a graded slab, read PSA's printed grade — authoritative, no need to
    # re-grade the card through the plastic case. The OCR is ~60% of a grade's compute,
    # so gate it on a cheap red-label-band check (only when SLAB_OCR_GATE is on). We
    # ALWAYS shadow-log real slab reads with that signal first, so we can confirm the
    # gate never skips a true slab before enabling it.
    maybe_slab = slab.looks_like_psa_slab(img)
    label = None
    if not (config.SLAB_OCR_GATE and not maybe_slab):
        try:
            label = slab.read_slab_label(img)
        except Exception:
            label = None
    if label and label.get("grade") is not None:
        di = result.detect_info or {}
        _log.info("slab read: grade=%s | gate signal maybe_slab=%s cov=%s fit=%s q=%s",
                  label["grade"], maybe_slab, di.get("coverage"),
                  di.get("aspect_fit"), di.get("quality"))
    grade_for_value = result.overall
    if label and label.get("grade") is not None:
        out["slab"] = label
        grade_for_value = label["grade"]

    def _present(card):
        """Show + price a confidently identified card."""
        out["match"] = card
        c = card["card"]
        cid = c.get("id")
        # Now that the card is known, re-measure centering by aligning it to its clean
        # reference image — reliable on holo/full-art where the blind heuristic isn't.
        price_grade = grade_for_value
        try:
            ref = refgrade.load_reference(cid, c.get("image"))
            if ref is not None:
                rc = refgrade.measure_centering(result.warped, ref)
                if rc:
                    g = out["grade"]
                    g["centering"] = rc
                    g["overall"] = grading._combine(rc, g["corners"], g["edges"], g["surface"])
                    out["overlay"] = _to_data_uri(grading.annotate_detection(
                        result.warped, rc, g["corners"], g["edges"]))
                    if not (label and label.get("grade") is not None):
                        price_grade = g["overall"]      # price off the refined grade
        except Exception:
            pass
        if cid:
            out["value"] = pricing.get_card_value(cid, price_grade)
            _apply_real_graded(out["value"], c, price_grade)

    def _unsure(guess_card=None):
        """Explain *why* we couldn't identify and how to fix it — never guess.

        The message is tailored to the likely cause: a poor read of the card
        (bad crop / low contrast background) vs. a clean read that simply didn't
        match anything confidently."""
        out["uncertain"] = True
        di = result.detect_info or {}
        poor_read = di.get("quality") == "low" or di.get("coverage", 1.0) < 0.5
        if poor_read:
            out["match_warning"] = (
                "I couldn't get a clean outline of the card — it blended into the "
                "background. Put it on a darker, plain surface, fill the frame, and "
                "shoot straight down, then try again.")
            out["unsure_reason"] = "bad_crop"
        else:
            msg = "I read the card clearly but couldn't confidently match it."
            if guess_card and guess_card.get("name"):
                num = guess_card.get("number")
                out["guess"] = {"name": guess_card.get("name"),
                                "set": guess_card.get("set"), "number": num}
                msg += (f" Closest guess: {guess_card.get('name')}"
                        f"{' #' + num if num else ''} — but I'm not sure enough to call it.")
            msg += " It may be glare on a holo, or a set that isn't in my index yet."
            out["match_warning"] = msg
            out["unsure_reason"] = "weak_match"

    index = get_index()
    oi = get_orb_index()

    # Primary: pHash narrows 20k cards → a shortlist, then ORB confirms against just
    # those (sub-second, vs ~50s scanning all). NEVER guess — only present a confident
    # match; otherwise say we couldn't identify it.
    if oi and oi.ok and len(index):
        shortlist = index.match(result.warped, top_n=config.ORB_PHASH_SHORTLIST)
        ids = [s["card"].get("id") for s in shortlist if s.get("card")]
        gr = oi.query_shortlist(result.warped, ids)
        if gr and gr.get("confident"):
            _present({"card": gr["card"], "method": "orb", "distance": 0,
                      "orb_score": gr["orb_score"], "confidence": gr.get("confidence"),
                      "confident": True})
        else:
            _unsure(gr.get("card") if gr else None)
    elif oi and oi.ok:
        # No pHash index loaded → fall back to the (slow) global ORB scan.
        gr = oi.query(result.warped)
        if gr and gr.get("confident"):
            _present({"card": gr["card"], "method": "orb_global", "distance": 0,
                      "orb_score": gr["orb_score"], "confidence": gr.get("confidence"),
                      "confident": True})
        else:
            _unsure(gr.get("card") if gr else None)
    elif len(index):
        # Legacy fallback (no global DB yet): pHash shortlist + per-card ORB, gated.
        try:
            for s in index.match(result.warped, top_n=config.ORB_SHORTLIST):
                index._candidate_image(s["card"])
        except Exception:
            pass
        b = index.best(result.warped)
        confident = b and (b.get("method") == "orb"
                           or b["distance"] <= config.MATCH_MAX_DISTANCE)
        if confident:
            b["confident"] = True
            _present(b)
        else:
            _unsure(b.get("card") if b else None)
    else:
        out["match_warning"] = (
            "Card index is empty. Run scripts/build_index.py to enable identification.")

    return out


def process_bytes(data: bytes, corners=None) -> dict:
    img = decode_image(data)
    if img is None:
        return {"ok": False, "stage": "decode", "message": "Unreadable image file."}
    return process_image(img, corners=corners)


def detect_corners_bytes(data: bytes):
    """Auto-detected card corners (normalised) to seed the manual align tool."""
    img = decode_image(data)
    if img is None:
        return None
    try:
        return grading.detect_corners(img)
    except Exception:
        return None
