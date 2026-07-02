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
from src import grading, pricing, slab, graded_pricing, refgrade, onnx_grader, backgrade
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


# A 10 MB JPEG can decompress to 50+ megapixels (a "decompression bomb"); OpenCV holds
# the full array plus working copies, so RAM scales with *pixels*, not file bytes — that's
# the real OOM lever, not the byte cap. Downscale any decoded image to this long edge before
# any CV runs. 2200px ≈ ~1500px of card height — plenty for centering + edge-whitening on a
# phone photo. ponytail: fixed ceiling; raise it only if measurement accuracy demands more.
_MAX_LONG_EDGE = 2200


def decode_image(data: bytes) -> Optional[np.ndarray]:
    if not data:                       # empty upload: imdecode would THROW on an empty buffer
        return None
    arr = np.frombuffer(data, np.uint8)
    try:
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except cv2.error:                  # malformed/garbage bytes can raise instead of returning None
        return None
    if img is None:
        return None
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest > _MAX_LONG_EDGE:
        s = _MAX_LONG_EDGE / longest
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return img


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


def _px_corners(corners, img: np.ndarray):
    """Convert 4 normalised (0..1) corner points to pixel coords for the warp."""
    if corners and len(corners) == 4:
        h, w = img.shape[:2]
        try:
            return [[float(x) * w, float(y) * h] for x, y in corners]
        except Exception:
            return None
    return None


def _worst(a, b):
    """PSA-style worst-of: a card is only as good as its worse side. None-safe."""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _identify(warped: np.ndarray, index: CardIndex, oi: OrbIndex) -> Optional[dict]:
    """Identify with retry tiers — only paid when the straight match isn't confident.

    A clean read can still miss for three known reasons, each with a cheap fix:
      * the warp came out upside-down (a rectangle is orientation-ambiguous) → rot180
      * holo glare / washed contrast killed the features → CLAHE-normalised retry
      * the right card wasn't in pHash's top shortlist → one wider-shortlist retry
    Returns the first confident result (tagged with which variant found it), else the
    best unconfident guess for the "closest guess" message.
    """
    def q(img, top_n=config.ORB_PHASH_SHORTLIST):
        shortlist = index.match(img, top_n=top_n)
        ids = [s["card"].get("id") for s in shortlist if s.get("card")]
        return oi.query_shortlist(img, ids)

    # Both orientations ALWAYS scored: near-symmetric art can confidently match the
    # WRONG card when the photo is upside-down, so an early return on the first
    # confident hit is a trap — the true orientation out-votes the false one instead.
    up, down = q(warped), q(cv2.rotate(warped, cv2.ROTATE_180))
    both = [(n, g) for n, g in (("as-is", up), ("rot180", down)) if g]
    conf = [(n, g) for n, g in both if g.get("confident")]
    if conf:
        name, gr = max(conf, key=lambda ng: ng[1].get("orb_score", 0))
        gr["variant"] = name
        return gr

    # Retry tiers, only paid on a miss: CLAHE for holo glare / washed contrast, then
    # one wider pHash shortlist in case the right card wasn't in the top slice.
    # ponytail: 5x shortlist as the single "wide" retry; a full-index scan is ~50s, not worth it.
    tries = []
    try:
        l, a, b = cv2.split(cv2.cvtColor(warped, cv2.COLOR_BGR2LAB))
        l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
        tries.append(("deglare", cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR),
                      config.ORB_PHASH_SHORTLIST))
    except Exception:
        pass
    tries.append(("wide", warped, config.ORB_PHASH_SHORTLIST * 5))

    best = max((g for _, g in both), key=lambda g: g.get("confidence", 0), default=None)
    for name, img, top_n in tries:
        gr = q(img, top_n)
        if gr and gr.get("confident"):
            gr["variant"] = name
            return gr
        if gr and (best is None or gr.get("confidence", 0) > best.get("confidence", 0)):
            best = gr
    return best


def process_image(img: np.ndarray, corners=None, back_img: "np.ndarray | None" = None,
                  back_corners=None) -> dict:
    """Run the full grade + identify + price pipeline on a BGR image.

    corners (optional): 4 [x, y] points NORMALISED to 0..1, supplied by the
    manual align tool. Converted to pixels and used for the warp.

    back_img (optional): the back-of-card photo. When supplied, the back is graded
    too (centering/whitening/surface vs the uniform blue back) and the headline
    grade becomes the PSA-style worst-of the two sides.
    """
    # Too small to hold a gradeable card — reject with a clear message rather than
    # returning a meaningless grade for a thumbnail / 1x1 / icon.
    h0, w0 = img.shape[:2]
    if min(h0, w0) < 120:
        return {"ok": False, "stage": "too_small",
                "message": "That image is too small to grade — upload a full-size photo "
                           "of the card."}

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
        "capture": result.capture,
        "overlay": _to_data_uri(grading.annotate_detection(
            result.warped, result.centering, result.corners, result.edges)),
    }
    # Warn on a poor-quality photo (blur / glare / exposure) — the grade is only as good
    # as the input, so tell the user to retake rather than silently grading garbage.
    if result.capture and not result.capture.get("ok", True):
        out["capture_warning"] = " ".join(result.capture.get("warnings", []))

    # Trained CNN prediction (ONNX, fast CPU). Surfaced ALONGSIDE the heuristic grade for
    # now — not replacing it — until the model is trusted. Inert if the model isn't present.
    try:
        cnn = onnx_grader.grade(result.warped)
        if cnn:
            out["grade"]["cnn"] = cnn
    except Exception:
        pass

    # ── Back-of-card grading (PSA-style worst-of) ──────────────────────────────
    # White wear pops against the uniform blue back, and back-centering aligns to one
    # canonical reference — so the back is graded separately, then the headline grade
    # becomes the worst of the two sides (the front-only number is kept as front_overall).
    back_overall = None
    if back_img is not None:
        bres = grading.grade_card(back_img, manual_corners=_px_corners(back_corners, back_img))
        if not bres.detected:
            return {"ok": False, "stage": "detect_back",
                    "message": "Couldn't find a card in the BACK photo. Use a flat, "
                               "well-lit shot of the whole card back on a contrasting "
                               "background."}
        back = backgrade.grade_back(bres.warped)
        back_overall = grading._combine(back["centering"], back["corners"],
                                        back["edges"], back["surface"])
        out["grade"]["back"] = {
            "overall": back_overall, "centering": back["centering"],
            "corners": back["corners"], "edges": back["edges"], "surface": back["surface"],
        }
        out["grade"]["front_overall"] = out["grade"]["overall"]
        out["grade"]["overall"] = _worst(out["grade"]["overall"], back_overall)
        out["back_overlay"] = _to_data_uri(grading.annotate_detection(
            bres.warped, back["centering"], back["corners"], back["edges"]))
        if bres.capture and not bres.capture.get("ok", True):
            out["back_capture_warning"] = " ".join(bres.capture.get("warnings", []))

    di0 = result.detect_info or {}
    cov = di0.get("coverage", 1.0)
    method = di0.get("method")
    fit = di0.get("aspect_fit", 1.0)
    quality = di0.get("quality")
    # "Is this even a card?" guard. A non-card photo (wall, paper, object) still produces a
    # grade because detection falls back to the whole frame — embarrassing in a demo. The
    # detector already flags these: method 'fullframe' = nothing found; or a low-quality,
    # non-card-shaped (low aspect_fit) detection. Warn honestly; real cards are quality=good.
    if method == "fullframe" or (quality == "low" and fit < 0.55):
        out["detection_warning"] = (
            "Couldn't clearly find a card in this photo — this grade may not be meaningful. "
            "Make sure the whole card is visible, flat, against a contrasting background.")
        out["card_uncertain"] = True
    elif cov < 0.20:
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
    grade_for_value = out["grade"]["overall"]   # combined (worst-of) when a back was graded
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
                    if back_img is not None:
                        g["front_overall"] = g["overall"]
                        g["overall"] = _worst(g["overall"], back_overall)
                    out["overlay"] = _to_data_uri(grading.annotate_detection(
                        result.warped, rc, g["corners"], g["edges"]))
                    if not (label and label.get("grade") is not None):
                        price_grade = g["overall"]      # price off the refined grade
        except Exception:
            pass
        if cid:
            # Pricing is a network call and a bonus, not the product — never let it turn a
            # successful grade into a 500. On any failure the grade still returns (value=None).
            try:
                out["value"] = pricing.get_card_value(cid, price_grade)
                _apply_real_graded(out["value"], c, price_grade)
            except Exception:
                pass

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
        gr = _identify(result.warped, index, oi)
        if gr and gr.get("confident"):
            _present({"card": gr["card"], "method": "orb", "distance": 0,
                      "orb_score": gr["orb_score"], "confidence": gr.get("confidence"),
                      "confident": True, "variant": gr.get("variant"),
                      "print_uncertain": gr.get("print_uncertain", False)})
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


def process_bytes(data: bytes, corners=None, back_data: "bytes | None" = None,
                  back_corners=None) -> dict:
    img = decode_image(data)
    if img is None:
        return {"ok": False, "stage": "decode", "message": "Unreadable image file."}
    back_img = decode_image(back_data) if back_data else None
    if back_data and back_img is None:
        return {"ok": False, "stage": "decode_back",
                "message": "Unreadable back-of-card image file."}
    return process_image(img, corners=corners, back_img=back_img, back_corners=back_corners)


def detect_corners_bytes(data: bytes):
    """Auto-detected card corners (normalised) to seed the manual align tool."""
    img = decode_image(data)
    if img is None:
        return None
    try:
        return grading.detect_corners(img)
    except Exception:
        return None
