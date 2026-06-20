"""Lightweight per-stage timing profiler for one grade.

Calls the same engine functions `engine.process_image` uses, but wraps each
stage in a timer — so we can see where the ~Ns/grade actually goes before
changing any preprocessing. Does NOT go through /api/grade, so nothing is
written to the feed/DB or mirrored to Discord.

    python -m scripts.profile_grade /tmp/testcard.jpg [iters]

Stages: decode · grade_card (detect+warp+4 subgrades) · overlay encode ·
slab OCR · pHash shortlist · ORB confirm · pricing (network).
"""
from __future__ import annotations

import sys
import time
from statistics import mean

import config
from src import engine, grading, slab, pricing

T = lambda: time.perf_counter()


def _profile_once(data: bytes, warmed: bool) -> dict:
    stages: dict[str, float] = {}

    t = T(); img = engine.decode_image(data); stages["decode"] = T() - t
    if img is None:
        raise SystemExit("decode failed — not an image?")

    t = T(); result = grading.grade_card(img); stages["grade_card (detect+warp+grade)"] = T() - t
    if not result.detected:
        raise SystemExit("no card detected in the photo")

    t = T()
    engine._to_data_uri(grading.annotate_detection(
        result.warped, result.centering, result.corners, result.edges))
    stages["overlay encode (PNG)"] = T() - t

    t = T()
    try:
        slab.read_slab_label(img)
    except Exception:
        pass
    stages["slab OCR (tesseract)"] = T() - t

    index = engine.get_index()
    oi = engine.get_orb_index()

    t = T(); shortlist = index.match(result.warped, top_n=config.ORB_PHASH_SHORTLIST)
    stages["pHash shortlist (20k)"] = T() - t
    ids = [s["card"].get("id") for s in shortlist if s.get("card")]

    t = T(); gr = oi.query_shortlist(result.warped, ids); stages["ORB confirm (shortlist)"] = T() - t

    cid = gr.get("card", {}).get("id") if gr and gr.get("confident") else None
    t = T()
    if cid:
        pricing.get_card_value(cid, result.overall)
    stages["pricing (network/cache)"] = T() - t

    stages["_card"] = (gr.get("card", {}).get("name") if gr else None) or "—"
    stages["_confident"] = bool(gr and gr.get("confident"))
    return stages


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/testcard.jpg"
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    data = open(path, "rb").read()

    # warm the singletons + any lazy first-call cost (load index, tesseract spinup)
    print(f"warming… ({path})")
    first = _profile_once(data, warmed=False)

    runs = [_profile_once(data, warmed=True) for _ in range(iters)]
    keys = [k for k in first if not k.startswith("_")]
    print(f"\nidentified: {first['_card']}  (confident={first['_confident']})")
    print(f"warm mean of {iters} runs (cold first run in parens):\n")
    width = max(len(k) for k in keys)
    total = 0.0
    for k in keys:
        warm = mean(r[k] for r in runs) * 1000
        cold = first[k] * 1000
        total += warm
        bar = "█" * max(1, round(warm / 40))   # 1 block ≈ 40ms
        print(f"  {k.ljust(width)}  {warm:7.1f} ms  ({cold:7.1f} cold)  {bar}")
    print(f"  {'TOTAL (warm)'.ljust(width)}  {total:7.1f} ms")


if __name__ == "__main__":
    main()
