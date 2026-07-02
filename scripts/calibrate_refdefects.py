#!/usr/bin/env python3
"""Calibrate the reference-aligned defect thresholds (refgrade.assess_defects).

Runs the REAL labeled populations (same ones validate_labeled.py uses) through
identify → align → defect-read and prints the distribution of raw defect fractions
per population, so the _REF_*_THR grade maps are set from data, not guesses.

Clean fronts must land 9-10; surface-damaged fronts must drop.

Run:  python3 scripts/calibrate_refdefects.py
"""
import glob
import json
import os
import statistics as st
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import engine, grading, refgrade  # noqa: E402

HOME = os.path.expanduser("~")
CACHE = Path(__file__).resolve().parent.parent / "data" / "debug" / "refdefects_cache.json"


def populations():
    clean = [os.path.join(HOME, "Documents", f"{i}.jpg") for i in range(1, 31)]
    dmg = sum((glob.glob(os.path.join(HOME, "Documents", "card data", "front",
                                      "surfacedmg", e))
               for e in ("*.jpg", "*.jpeg", "*.webp", "*.png")), [])
    return {"clean": [p for p in clean if os.path.exists(p)], "surfdmg": dmg}


def measure(path):
    img = engine.decode_image(Path(path).read_bytes())
    if img is None:
        return {"skip": "unreadable"}
    r = grading.grade_card(img)
    if not r.detected:
        return {"skip": "not detected"}
    gr = engine._identify(r.warped, engine.get_index(), engine.get_orb_index())
    if not (gr and gr.get("confident")):
        return {"skip": "not identified"}
    c = gr["card"]
    ref = refgrade.load_reference(c.get("id"), c.get("image"))
    if ref is None:
        return {"skip": "no reference"}
    d = refgrade.assess_defects(r.warped, ref)
    if d is None:
        return {"skip": "no alignment"}
    return {"card": c.get("name"),
            "corner_max": max(d["corners"]["per_corner"]),
            "edge_max": max(d["edges"]["per_edge"]),
            "surf": d["surface"]["scuff"],
            "grades": {k: d[k]["grade"] for k in ("corners", "edges", "surface")}}


def main():
    rebuild = "--rebuild" in sys.argv
    cache = json.loads(CACHE.read_text()) if CACHE.exists() and not rebuild else {}
    for pop, paths in populations().items():
        for p in paths:
            if p not in cache:
                cache[p] = {"pop": pop, **measure(p)}
                print(".", end="", flush=True)
    CACHE.write_text(json.dumps(cache))
    print()

    for pop in ("clean", "surfdmg"):
        rows = [v for v in cache.values() if v["pop"] == pop and "skip" not in v]
        skipped = [v for v in cache.values() if v["pop"] == pop and "skip" in v]
        print(f"\n== {pop}: {len(rows)} measured, {len(skipped)} skipped "
              f"({[v['skip'] for v in skipped][:6]}...)")
        for key in ("corner_max", "edge_max", "surf"):
            xs = sorted(v[key] for v in rows)
            if not xs:
                continue
            q = lambda f: xs[min(len(xs) - 1, int(f * len(xs)))]
            print(f"  {key:<11} med {st.median(xs):.4f}  p75 {q(.75):.4f}  "
                  f"p90 {q(.90):.4f}  max {xs[-1]:.4f}")
        for k in ("corners", "edges", "surface"):
            gs = [v["grades"][k] for v in rows]
            if gs:
                print(f"  {k:<11} grades: med {st.median(gs)}  min {min(gs)}  "
                      f"<=8: {sum(g <= 8 for g in gs)}/{len(gs)}")


if __name__ == "__main__":
    main()
