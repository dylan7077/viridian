"""Explain why a card photo did or didn't get identified.

Run after the bot saves data/debug/last_bot_upload.jpg:
    python -m scripts.diagnose_match
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

import config
from src import engine, grading


def main(path: str | None = None) -> None:
    img_path = Path(path) if path else config.DATA_DIR / "debug" / "last_bot_upload.jpg"
    if not img_path.exists():
        print(f"No image at {img_path} — send a card to the bot first.")
        return
    img = cv2.imread(str(img_path))
    if img is None:
        print(f"Couldn't read {img_path}")
        return
    print(f"Image: {img_path}  ({img.shape[1]}x{img.shape[0]})")

    # 1) Detection / warp
    g = grading.grade_card(img)
    if not g.detected:
        print("\n❌ DETECTION FAILED — no card found in the photo.")
        print("   → cause: background not contrasting, card cropped, or too blurry.")
        return
    cov = g.detect_info.get("coverage")
    print(f"\n✓ Detected. coverage={cov:.0%}" if cov is not None else "\n✓ Detected.")
    print(f"  overall grade estimate: PSA {g.overall}")

    # 2) ORB global match — the numbers behind the confidence gate
    oi = engine.get_orb_index()
    if not oi or not oi.ok:
        print("\n❌ ORB index not loaded (data/orb_db.npz missing).")
        return
    print(f"  index size: {len(oi)} cards")
    res = oi.query(g.warped)
    if not res:
        print("\n❌ NO MATCH AT ALL — zero feature votes.")
        print("   → the card likely isn't in the index, or the warp is unusable.")
        return

    card = res["card"]
    from src.orb_index import _MIN_VOTES, _MIN_LEAD, _MIN_RATIO, _MIN_CONF
    score, runner = res["orb_score"], res["runner_up"]
    lead = score - runner
    print("\n── ORB match result ─────────────────────────────")
    print(f"  best guess : {card.get('name')}  ·  {card.get('set')} #{card.get('number')}")
    print(f"  votes      : {score}   (need ≥ {_MIN_VOTES})        {'OK' if score >= _MIN_VOTES else 'FAIL'}")
    print(f"  runner-up  : {runner}")
    print(f"  lead       : {lead}   (need ≥ {_MIN_LEAD})        {'OK' if lead >= _MIN_LEAD else 'FAIL'}")
    print(f"  ratio      : {score / runner if runner else float('inf'):.1f}x (need ≥ {_MIN_RATIO}x)   "
          f"{'OK' if score >= runner * _MIN_RATIO else 'FAIL'}")
    print(f"  confidence : {res['confidence']}  (need ≥ {_MIN_CONF})   {'OK' if res['confidence'] >= _MIN_CONF else 'FAIL'}")
    print(f"  → CONFIDENT: {res['confident']}")

    print("\n── diagnosis ────────────────────────────────────")
    if res["confident"]:
        print("  This photo WOULD identify now. The earlier miss was likely a")
        print("  slightly different angle/glare/crop. Re-takes vary.")
    elif score < _MIN_VOTES:
        print("  Too few feature votes — the photo and the stored card image don't")
        print("  share enough detail. Usually: glare/reflection on a holo, blur,")
        print("  heavy crop, or the card simply isn't in the index.")
    elif score < runner * _MIN_RATIO or lead < _MIN_LEAD:
        print("  The top guess wasn't decisive enough vs. the runner-up — the photo")
        print("  matched several cards weakly. Sharper, flatter, glare-free shot helps.")
    else:
        print("  Just under the confidence floor. A cleaner photo (or a small gate")
        print("  tweak) would push it over.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
