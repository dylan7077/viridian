"""Tidy-rename card image files into a consistent scheme, preserving any human label
already in the filename and assigning clean sequential IDs. Does NOT invent labels.

  back_001.jpg                 (IMG_*/hash/numeric names -> just an ID)
  back_014_badcorner.jpeg      (a descriptive word in the old name is kept as the label)

Writes a reversible old->new map (rename_map.csv) in the folder BEFORE renaming, so the
operation can always be undone.

Usage: python scripts/rename_cards.py <folder> <prefix> [default_label]
"""
from __future__ import annotations

import csv
import os
import re
import sys

EXTS = (".jpg", ".jpeg", ".png", ".webp", ".jxl", ".heic", ".heif")


def label_of(filename: str):
    """A short alphabetic word the user put in the name = a label to preserve. Camera/
    hash/numeric names (IMG_1234, 710755496…, 20240102_…, 23213) carry no label."""
    b = re.sub(r"\.[^.]+$", "", filename).lower()
    if b.startswith("img") or re.fullmatch(r"[\d_]+", b) or re.search(r"\d{6,}", b) or len(b) > 18:
        return None
    m = re.fullmatch(r"([a-z]+[0-9]*)", b)
    return m.group(1) if m else None


def plan(folder: str, prefix: str, default_label):
    files = sorted(f for f in os.listdir(folder)
                   if os.path.splitext(f)[1].lower() in EXTS and not f.startswith(prefix + "_"))
    rows = []
    for i, f in enumerate(files, 1):
        ext = os.path.splitext(f)[1].lower()
        lab = label_of(f) or default_label
        new = f"{prefix}_{i:03d}" + (f"_{lab}" if lab else "") + ext
        rows.append((f, new, lab or ""))
    return rows


def main(folder, prefix, default_label=None):
    rows = plan(folder, prefix, default_label)
    if not rows:
        print("nothing to rename (already done?)")
        return
    map_path = os.path.join(folder, "rename_map.csv")
    with open(map_path, "w", newline="") as fh:        # write reversal map first
        w = csv.writer(fh); w.writerow(["old", "new", "label"]); w.writerows(rows)
    labeled = sum(1 for _, _, l in rows if l)
    for old, new, _ in rows:
        os.rename(os.path.join(folder, old), os.path.join(folder, new))
    print(f"renamed {len(rows)} files in {folder}")
    print(f"  labeled (preserved): {labeled}   unlabeled (id only): {len(rows)-labeled}")
    print(f"  reversal map: {map_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
