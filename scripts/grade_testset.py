#!/usr/bin/env python3
"""Run the full grading pipeline over a folder of card photos and print a table +
JSON snapshot. The snapshot lets us diff a code change against the prior run to prove
it helped (or didn't) instead of eyeballing one card. No framework — just the pipeline.

Usage:
    python3 scripts/grade_testset.py [dir-or-glob]      # default: ~/Documents 1..30.jpg
    python3 scripts/grade_testset.py --diff             # compare to last snapshot

ponytail: the test set is the 30 hand-shot cards; point it elsewhere via argv.
"""
import sys, json, glob, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import engine, grading  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "debug" / "testset_snapshot.json"


def find_images(arg):
    if arg and os.path.isdir(arg):
        pats = [os.path.join(arg, e) for e in ("*.jpg", "*.jpeg", "*.png")]
        files = sorted(sum((glob.glob(p) for p in pats), []))
    elif arg:
        files = sorted(glob.glob(arg))
    else:
        d = os.path.expanduser("~/Documents")
        files = [os.path.join(d, f"{i}.jpg") for i in range(1, 31)]
        files = [f for f in files if os.path.exists(f)]
    return files


def grade_one(path):
    img = engine.decode_image(Path(path).read_bytes())
    if img is None:
        return {"file": os.path.basename(path), "error": "decode"}
    try:
        r = grading.grade_card(img)
    except Exception as e:
        return {"file": os.path.basename(path), "error": f"{type(e).__name__}: {e}"}
    c = r.centering
    return {
        "file": os.path.basename(path),
        "detected": r.detected,
        "method": r.detect_info.get("method"),
        "coverage": round(float(r.detect_info.get("coverage", 0)), 2),
        "overall": r.overall,
        "cen_grade": c.get("grade"),
        "lr": c.get("left_right"),
        "tb": c.get("top_bottom"),
        "worst_pct": c.get("worst_pct"),
        "cen_conf": c.get("confidence"),
        "cen_method": c.get("method"),
        "corners": r.corners.get("grade"),
        "edges": r.edges.get("grade"),
        "surface": r.surface.get("grade"),
    }


def print_table(rows):
    hdr = ["file", "det", "method", "cov", "ovr", "cen", "lr", "tb", "worst", "conf", "cnr", "edg", "srf"]
    keys = ["file", "detected", "method", "coverage", "overall", "cen_grade",
            "lr", "tb", "worst_pct", "cen_conf", "corners", "edges", "surface"]
    w = [max(len(hdr[i]), *(len(str(r.get(keys[i], ""))) for r in rows)) for i in range(len(hdr))]
    line = lambda vals: "  ".join(str(v).ljust(w[i]) for i, v in enumerate(vals))
    print(line(hdr))
    print(line(["-" * x for x in w]))
    for r in rows:
        if "error" in r:
            print(r["file"].ljust(w[0]), " ERROR:", r["error"])
            continue
        print(line([r.get(k, "") for k in keys]))


def summary(rows):
    ok = [r for r in rows if not r.get("error")]
    det = [r for r in ok if r.get("detected")]
    meas = [r for r in det if r.get("cen_grade") is not None]
    print(f"\n{len(rows)} cards | detected {len(det)}/{len(rows)} | "
          f"centering measured {len(meas)}/{len(rows)} | errors "
          f"{sum(1 for r in rows if r.get('error'))}")


def diff(rows):
    if not SNAPSHOT.exists():
        print("No prior snapshot to diff."); return
    old = {r["file"]: r for r in json.loads(SNAPSHOT.read_text())}
    fields = ["detected", "overall", "cen_grade", "lr", "tb", "cen_conf", "corners", "edges", "surface"]
    changed = 0
    for r in rows:
        o = old.get(r["file"])
        if not o:
            continue
        d = [(f, o.get(f), r.get(f)) for f in fields if o.get(f) != r.get(f)]
        if d:
            changed += 1
            print(f"{r['file']}: " + ", ".join(f"{f} {a}->{b}" for f, a, b in d))
    print(f"\n{changed} cards changed vs snapshot." if changed else "\nNo changes vs snapshot.")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_diff = "--diff" in sys.argv
    rows = [grade_one(p) for p in find_images(args[0] if args else None)]
    if not rows:
        print("No images found."); return
    print_table(rows)
    summary(rows)
    if do_diff:
        diff(rows)
    else:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(rows, indent=1))
        print(f"\nSnapshot written: {SNAPSHOT}")


if __name__ == "__main__":
    main()
