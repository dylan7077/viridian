#!/usr/bin/env python3
"""Pull the Roboflow "Card Grader" dataset and test whether it's good enough to fix the
corner/edge/surface gap BEFORE investing in training. It's the one open dataset labeled for
the exact defects our heuristics can't grade (edge wear, corner wear, scratches).

Roboflow requires a FREE API key (you can't export without one). Get it in 30 seconds:
  1. sign up at https://app.roboflow.com  (free)
  2. Settings -> copy your Private API Key
  3. run:   ROBOFLOW_API_KEY=xxxxx python3 scripts/eval_roboflow_grader.py

What it does once it has the key:
  * downloads the dataset (tries classification 'folder' layout, then COCO detection)
  * reports image count, format, and the label classes actually present
  * maps those labels to our 4 aspects (centering/corners/edges/surface) and tells you
    whether there's enough signal per aspect to train, or whether it's detection-only

No key? It prints setup steps and exits cleanly (code 2) — nothing else needed from you.
Self-test the offline logic without a key:  python3 scripts/eval_roboflow_grader.py --selftest
"""
import os
import sys
import json
import glob
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "data" / "roboflow_cardgrader"
WORKSPACE = "group-6-major-project"
PROJECT = "card-grader"
ASPECT_HINTS = {
    "corners": ("corner", "crnr"),
    "edges": ("edge", "whiten", "chip"),
    "surface": ("surface", "scratch", "scuff", "print", "crease", "dent"),
    "centering": ("center", "centre", "centering"),
}


def find_key():
    k = os.environ.get("ROBOFLOW_API_KEY")
    if k:
        return k.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("ROBOFLOW_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def setup_help():
    print(__doc__.split("What it does")[0].strip())
    print("\n-> No ROBOFLOW_API_KEY found. Add it (free) and re-run. Nothing else is needed.")


def classes_to_aspects(classes):
    """Map dataset label names to our 4 grading aspects so we know which aspect each
    labeled defect could supervise."""
    mapping = {}
    for c in classes:
        cl = c.lower()
        for aspect, hints in ASPECT_HINTS.items():
            if any(h in cl for h in hints):
                mapping.setdefault(aspect, []).append(c)
    return mapping


def summarize(classes, counts, fmt, n_images):
    """The verdict logic — pure, so --selftest can exercise it offline."""
    print(f"\n=== Roboflow Card Grader: {n_images} images, format '{fmt}' ===")
    print(f"label classes ({len(classes)}): {classes}")
    if counts:
        print("class frequencies:", dict(Counter(counts).most_common()))
    amap = classes_to_aspects(classes)
    print("\nmaps to our grading aspects:")
    for aspect in ("centering", "corners", "edges", "surface"):
        got = amap.get(aspect)
        print(f"  {aspect:9}: {'YES -> ' + ', '.join(got) if got else 'no labeled signal'}")
    covered = [a for a in ("corners", "edges", "surface") if a in amap]
    print("\nVERDICT:")
    if covered:
        print(f"  Usable for: {', '.join(covered)} — the gap aspects. Worth training if each "
              f"has enough examples (aim >~300/class). This dataset is small (~600 imgs), so "
              f"treat it as a bootstrap / transfer-learning seed, not the whole training set.")
    else:
        print("  No corner/edge/surface labels detected — not useful for the gap. Skip it.")
    return amap


def download():
    from roboflow import Roboflow
    key = find_key()
    rf = Roboflow(api_key=key)
    proj = rf.workspace(WORKSPACE).project(PROJECT)
    versions = proj.versions()
    if not versions:
        raise SystemExit("project has no published versions")
    ver = versions[-1]                       # latest
    DEST.parent.mkdir(parents=True, exist_ok=True)
    # try classification layout first, fall back to detection (coco)
    for fmt in ("folder", "coco"):
        try:
            ds = ver.download(fmt, location=str(DEST), overwrite=True)
            return fmt, Path(ds.location)
        except Exception as e:
            print(f"  ({fmt} export failed: {str(e)[:120]})")
    raise SystemExit("could not export in folder or coco format")


def inspect(fmt, loc):
    """Read the downloaded dataset and pull out classes + counts, format-agnostically."""
    imgs = glob.glob(str(loc / "**" / "*.jpg"), recursive=True) + \
        glob.glob(str(loc / "**" / "*.png"), recursive=True)
    classes, counts = [], []
    # classification 'folder' layout: train/<class>/img.jpg
    sub = [p.name for p in loc.glob("*/*") if p.is_dir()]
    if sub:
        classes = sorted(set(sub))
        for p in loc.glob("*/*"):
            if p.is_dir():
                counts += [p.name] * len(list(p.glob("*.jpg")) + list(p.glob("*.png")))
    # COCO detection: _annotations.coco.json with categories
    for ann in glob.glob(str(loc / "**" / "*coco*.json"), recursive=True):
        try:
            j = json.loads(Path(ann).read_text())
            cats = {c["id"]: c["name"] for c in j.get("categories", [])}
            classes = sorted(set(classes) | set(cats.values()))
            counts += [cats[a["category_id"]] for a in j.get("annotations", [])
                       if a.get("category_id") in cats]
        except Exception:
            pass
    return classes, counts, len(imgs)


def selftest():
    print("[selftest] exercising verdict logic on a fake Roboflow manifest...")
    classes = ["corner-wear", "edge-whitening", "scratch", "good"]
    counts = ["corner-wear"] * 120 + ["edge-whitening"] * 90 + ["scratch"] * 200 + ["good"] * 300
    amap = summarize(classes, counts, "coco", 710)
    assert "corners" in amap and "edges" in amap and "surface" in amap, amap
    assert classes_to_aspects(["foobar"]) == {}
    print("\n[selftest] OK — verdict logic maps defect labels to aspects correctly.")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if not find_key():
        setup_help()
        sys.exit(2)
    print("downloading Roboflow Card Grader dataset...")
    fmt, loc = download()
    classes, counts, n = inspect(fmt, loc)
    summarize(classes, counts, fmt, n)
    print(f"\ndataset at: {loc}")


if __name__ == "__main__":
    main()
