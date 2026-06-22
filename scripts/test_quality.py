"""Ground-truth check for the capture-quality gate: a sharp clean card passes, and each
degradation (blur / glare / too-dark) raises the matching warning. Guards the garbage-in
gate so it neither nags on good photos nor stays silent on bad ones.
Run: python3 scripts/test_quality.py
"""
import sys, glob
from pathlib import Path
import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from src import grading  # noqa: E402


def warped_clean():
    for f in sorted(glob.glob(str(Path(__file__).resolve().parent.parent /
                                  "data" / "card_images" / "*.jpg")))[:5]:
        img = cv2.imread(f)
        if img is not None:
            return cv2.resize(img, (config.CARD_W, config.CARD_H))
    raise SystemExit("no reference card images found")


def has(warnings, word):
    return any(word in w.lower() for w in warnings)


def main():
    base = warped_clean()
    q = grading.assess_capture_quality(base)
    print(f"sharp clean: ok={q['ok']} blur={q['blur']} glare={q['glare']} bright={q['brightness']}")
    assert q["ok"], f"a sharp clean card should pass the gate, got {q['warnings']}"

    qb = grading.assess_capture_quality(cv2.GaussianBlur(base, (9, 9), 0))
    print(f"blurred:     ok={qb['ok']} blur={qb['blur']}  -> {qb['warnings']}")
    assert has(qb["warnings"], "focus"), "blurred card must raise a focus warning"

    glared = base.copy()
    glared[80:300, 80:360] = (255, 255, 255)        # big blown-out glare patch
    qg = grading.assess_capture_quality(glared)
    print(f"glare:       ok={qg['ok']} glare={qg['glare']}  -> {qg['warnings']}")
    assert has(qg["warnings"], "glare"), "glare patch must raise a glare warning"

    qd = grading.assess_capture_quality((base * 0.12).astype(base.dtype))
    print(f"too dark:    ok={qd['ok']} bright={qd['brightness']}  -> {qd['warnings']}")
    assert has(qd["warnings"], "dark"), "dark card must raise a darkness warning"

    # low-resolution: a small card in the source photo should warn, a large one shouldn't
    qlo = grading.assess_capture_quality(base, source_card_px=400)
    qhi = grading.assess_capture_quality(base, source_card_px=1100)
    print(f"low-res 400px: -> {qlo['warnings']}")
    assert has(qlo["warnings"], "low-resolution"), "small card_px must warn low-resolution"
    assert not has(qhi["warnings"], "low-resolution"), "1100px card must not warn low-res"

    print("\nCapture gate verified: passes good cards, flags blur / glare / darkness / low-res.")


if __name__ == "__main__":
    main()
