"""Adversarial-input guard: the web/bot grade path must NEVER crash on a bad upload — it
must return a clean {ok: False, ...} dict. Caught a real crash (empty bytes threw in
cv2.imdecode) and bogus grades on 1x1 thumbnails. Run: python3 scripts/test_robustness.py
"""
import sys, io
from pathlib import Path
import numpy as np
import cv2
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import engine  # noqa: E402


def png(arr):
    b = io.BytesIO()
    Image.fromarray(arr).save(b, format="PNG")
    return b.getvalue()


def cases():
    a = np.random.RandomState(0).randint(0, 255, (800, 600, 3), np.uint8)
    _, jb = cv2.imencode(".jpg", a)
    return {
        "empty bytes": b"",
        "non-image junk": b"not an image" * 50,
        "truncated jpeg": jb.tobytes()[:len(jb) // 2],
        "1x1 px": png(np.zeros((1, 1, 3), np.uint8)),
        "tiny 8x8": png(np.zeros((8, 8, 3), np.uint8)),
        "grayscale": png(np.zeros((400, 300), np.uint8)),
        "rgba": png(np.zeros((400, 300, 4), np.uint8)),
        "extreme aspect": png(np.zeros((20, 2000, 3), np.uint8)),
    }


def main():
    bad = []
    for name, data in cases().items():
        try:
            out = engine.process_bytes(data)
        except Exception as e:
            bad.append(f"{name}: CRASHED ({type(e).__name__}: {str(e)[:50]})")
            continue
        if not (isinstance(out, dict) and "ok" in out):
            bad.append(f"{name}: returned non-standard {type(out)}")
            continue
        # garbage/degenerate inputs must NOT come back as a confident grade
        if name in ("empty bytes", "non-image junk", "1x1 px", "tiny 8x8") and out.get("ok"):
            bad.append(f"{name}: should be rejected but ok=True (grade={out.get('grade')})")
        print(f"  {name:16} -> ok={out.get('ok')} stage={out.get('stage')}")
    assert not bad, "robustness failures:\n  " + "\n  ".join(bad)
    print("\nAll adversarial inputs handled cleanly (no crash, no bogus grade).")

    # Dependency-failure resilience: a price-API outage (bad wifi at a sale) must NOT break
    # an otherwise-successful grade — pricing is a bonus, the grade is the product.
    import os
    from src import pricing
    photo = os.path.expanduser("~/Documents/1.jpg")
    if os.path.exists(photo):
        orig = pricing.get_card_value
        pricing.get_card_value = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down"))
        try:
            out = engine.process_bytes(Path(photo).read_bytes())
        finally:
            pricing.get_card_value = orig
        assert out.get("ok") and out.get("grade", {}).get("overall") is not None, \
            "grade must survive a pricing outage"
        print("Pricing-outage resilience: grade still returns (value degrades to None).")

    # Non-card guard: a wall / blank paper must be flagged uncertain, not graded as a card.
    import cv2
    wall = png(np.full((1500, 2000, 3), 130, np.uint8))
    paper = np.full((1500, 2000, 3), 40, np.uint8)
    cv2.rectangle(paper, (600, 400), (1400, 1100), (235, 235, 235), -1)
    for nm, data in (("wall", wall), ("blank paper", png(paper))):
        out = engine.process_bytes(data)
        assert out.get("card_uncertain"), f"{nm} should be flagged card_uncertain"
    if os.path.exists(photo):           # a real card must NOT be flagged uncertain
        out = engine.process_bytes(Path(photo).read_bytes())
        assert not out.get("card_uncertain"), "a real card was wrongly flagged uncertain"
    print("Non-card guard: walls/paper flagged uncertain, real card is not.")


if __name__ == "__main__":
    main()
