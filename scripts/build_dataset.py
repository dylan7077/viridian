"""Build / extend the grading training set.

Ingests labelled card images from one or more source folders into a flat training set:
crop the card (reuse the production detector), perceptual-hash dedup, parse a label from
the filename, and append to a manifest. Source folders carry weak labels in their names —
TCG condition terms (nm/lp/mp/hp/dmg) and defect types (badcorner, surfacedmg, …) — which
we normalise to a rough PSA-style grade and/or an aspect. Exact grades come later from
scraped slab labels; this seeds the set and, crucially, prints the per-grade histogram so
we can see how trainable the data is before investing in training.

Usage:
    python scripts/build_dataset.py "<folder>:<side>" ["<folder>:<side>" ...]
      side = front | back
Run again any time to add sources; dedup keeps it idempotent.
"""
from __future__ import annotations

import csv
import os
import sys
from collections import Counter

import cv2
import imagehash
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src import grading

OUT_DIR = config.DATA_DIR / "training"
IMG_DIR = OUT_DIR / "images"
MANIFEST = OUT_DIR / "manifest.csv"
COLS = ["phash", "file", "side", "aspect", "grade", "label_raw", "source"]

# TCG condition term  ->  rough PSA-style overall grade.
COND = {"nm": 9, "mint": 10, "good": 9, "lp": 7, "mp": 5, "hp": 3,
        "played": 4, "heavy": 3, "faded": 4, "dmg": 2, "damaged": 2,
        "rip": 1, "holyshit": 1, "damn": 2, "bent": 3}
# Defect type in filename  ->  (aspect it implicates, rough grade for that aspect).
DEFECT = {"badcorner": ("corners", 3), "badedge": ("edges", 3), "badtop": ("edges", 4),
          "badbent": ("overall", 3), "badback": ("overall", 3), "marks": ("surface", 4),
          "lines": ("surface", 4), "circles": ("surface", 4), "white": ("edges", 4),
          "surfacedmg": ("surface", 3), "surfdmg": ("surface", 3), "surf": ("surface", 3),
          "badtext": ("surface", 5), "scratch": ("surface", 3),
          "crease": ("surface", 2), "dent": ("surface", 3)}


def parse_label(name: str):
    """(aspect, grade, raw) from a filename, or (None, None, raw) if unlabelled."""
    low = name.lower()
    for key, (aspect, grade) in DEFECT.items():
        if key in low:
            return aspect, grade, key
    for key, grade in COND.items():
        # match as a token, not a substring of an unrelated word
        if low == key or low.startswith(key) or f"_{key}" in low or f"-{key}" in low:
            return "overall", grade, key
    return None, None, ""


def load_manifest_phashes():
    seen = set()
    if MANIFEST.exists():
        with open(MANIFEST) as f:
            for row in csv.DictReader(f):
                seen.add(row["phash"])
    return seen


def main(sources):
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    seen = load_manifest_phashes()
    new_rows = []
    counts = Counter()
    for spec in sources:
        folder, _, side = spec.partition(":")
        side = side or "front"
        files = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                 if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")]
        print(f"[{folder}] {len(files)} images (side={side})", flush=True)
        for i, fp in enumerate(files):
            img = cv2.imread(fp)
            if img is None:
                continue
            try:
                # backs get the blue-field detector (robust on wood / busy / window bg)
                card = grading.detect_back(img) if side == "back" else grading.detect_card(img)
            except Exception:
                continue
            ph = str(imagehash.phash(Image.fromarray(cv2.cvtColor(card, cv2.COLOR_BGR2RGB))))
            if ph in seen:
                continue
            seen.add(ph)
            aspect, grade, raw = parse_label(os.path.basename(fp))
            out = IMG_DIR / f"{ph}.jpg"
            cv2.imwrite(str(out), card)
            new_rows.append([ph, out.name, side, aspect or "", grade if grade is not None else "",
                             raw, os.path.basename(folder)])
            counts[grade if grade is not None else "unlabelled"] += 1
            if (i + 1) % 20 == 0:
                print(f"  {i+1}/{len(files)}", flush=True)

    write_header = not MANIFEST.exists()
    with open(MANIFEST, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(COLS)
        w.writerows(new_rows)

    print(f"\nAdded {len(new_rows)} new images. Manifest: {MANIFEST}")
    total = Counter()
    with open(MANIFEST) as f:
        for row in csv.DictReader(f):
            total[row["grade"] or "unlabelled"] += 1
    print("=== grade histogram (whole manifest) ===")
    for k in sorted(total, key=lambda x: (x == "unlabelled", x)):
        print(f"  grade {k:>10}: {total[k]}")
    print(f"  TOTAL: {sum(total.values())}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
