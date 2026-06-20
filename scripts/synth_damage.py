"""Synthesise labelled training data from CLEAN cards.

Thresholds can't grade and real labelled data is scarce/imbalanced (we have clean cards +
a flood of grade-3, almost nothing in the middle). But a clean card is a known grade-10,
and we can *degrade* it to any target grade with controllable defects — giving balanced,
perfectly-labelled examples across the whole 1–10 scale, for free, from the 145 clean backs
and the 20k catalog renders.

For each base image we SAMPLE target sub-grades (uniform → balanced), then apply matching
defects + realistic photo augmentation (glare, lighting, rotation, noise) so the model
learns condition invariant to capture conditions. Output: degraded image + per-aspect labels.

Usage: python scripts/synth_damage.py <clean_dir> <side front|back> [--per 8]
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src import grading

W, H = config.CARD_W, config.CARD_H
OUT = config.DATA_DIR / "training"
IMG = OUT / "images"
MANIFEST = OUT / "manifest.csv"
COLS = ["phash", "file", "side", "aspect", "grade", "label_raw", "source"]
rng = random.Random()


# ---- defect synthesis (severity 0=pristine .. 1=destroyed) -------------------
def _band_noise(shape, density, lo=200):
    """Random chalky-white speckles, density 0..1."""
    m = np.random.rand(*shape) < density
    out = np.zeros((*shape, 3), np.uint8)
    out[m] = np.random.randint(lo, 255, size=(m.sum(), 3))
    return out, m


def apply_corner_wear(card, grade):
    """Chalky white fray CONCENTRATED at the corner tip, frizzing inward — like real
    corner whitening, not scattered specks."""
    sev = (10 - grade) / 10
    h, w = card.shape[:2]
    out = card.copy()
    sz = int(min(h, w) * 0.14)
    maxd = sz
    corners = [(0, 0), (w - sz, 0), (0, h - sz), (w - sz, h - sz)]
    yy, xx = np.mgrid[0:sz, 0:sz]
    for rx, ry in random.sample(corners, k=rng.randint(1, 4)):
        tipx, tipy = (0 if rx == 0 else sz - 1), (0 if ry == 0 else sz - 1)
        dist = np.sqrt((xx - tipx) ** 2 + (yy - tipy) ** 2)
        thr = sev * maxd * rng.uniform(0.5, 1.15)
        # dense at the tip, fraying out (probabilistic, irregular)
        m = (dist < thr) & (np.random.rand(sz, sz) < np.clip(1 - dist / (thr + 1), 0, 1))
        white = np.random.randint(205, 250, (sz, sz, 3)).astype(np.uint8)
        r = out[ry:ry + sz, rx:rx + sz]
        r[m] = white[m]
    return out


def apply_edge_wear(card, grade):
    """Chalky white band hugging the very cut edge, irregular depth per position — real
    edge whitening frays from the edge inward, it isn't speckled across a strip."""
    sev = (10 - grade) / 10
    h, w = card.shape[:2]
    out = card.copy()
    maxd = max(2, int(min(h, w) * 0.05))
    rr = np.arange(h)[:, None]
    cc = np.arange(w)[None, :]
    for side in random.sample(["top", "bottom", "left", "right"], k=rng.randint(1, 4)):
        if side in ("top", "bottom"):
            d = (sev * maxd * np.random.uniform(0.15, 1.0, w) * (np.random.rand(w) < 0.85)).astype(int)
            m = (rr < d[None, :]) if side == "top" else (rr >= h - d[None, :])
        else:
            d = (sev * maxd * np.random.uniform(0.15, 1.0, h) * (np.random.rand(h) < 0.85)).astype(int)
            m = (cc < d[:, None]) if side == "left" else (cc >= w - d[:, None])
        white = np.random.randint(205, 250, (h, w, 3)).astype(np.uint8)
        out[m] = white[m]
    return out


def apply_surface(card, grade):
    sev = (10 - grade) / 10
    out = card.copy().astype(np.int16)
    h, w = card.shape[:2]
    # scratches: thin bright/dark lines across the interior
    for _ in range(int(sev * 30)):
        x1, y1 = rng.randint(0, w), rng.randint(0, h)
        x2, y2 = x1 + rng.randint(-60, 60), y1 + rng.randint(-60, 60)
        col = rng.choice([235, 30])
        cv2.line(out, (x1, y1), (x2, y2), (col, col, col), 1)
    # overall scuff haze
    out += (np.random.randn(h, w, 1) * sev * 18).astype(np.int16)
    return np.clip(out, 0, 255).astype(np.uint8)


def apply_centering(card, grade):
    """Re-frame the card off-centre so the border ratio matches the target grade."""
    sev = (10 - grade) / 10                       # 0..1 -> up to ~90/10 offset
    off = sev * 0.30                              # max 30% shift of the border
    dx = rng.uniform(-off, off) * W
    dy = rng.uniform(-off, off) * H
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    border = int(W * 0.06)
    big = cv2.copyMakeBorder(card, border, border, border, border, cv2.BORDER_REPLICATE)
    big = cv2.warpAffine(big, M, (big.shape[1], big.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    return cv2.resize(big[border:border + H, border:border + W], (W, H))


def photo_aug(card):
    out = card.copy()
    if rng.random() < 0.6:                         # glare blob
        g = out.copy()
        cv2.circle(g, (rng.randint(0, W), rng.randint(0, H)),
                   rng.randint(40, 130), (255, 255, 255), -1)
        out = cv2.addWeighted(out, 0.78, cv2.GaussianBlur(g, (61, 61), 0), 0.22, 0)
    out = cv2.convertScaleAbs(out, alpha=rng.uniform(0.8, 1.2), beta=rng.uniform(-25, 25))
    if rng.random() < 0.5:
        a = rng.uniform(-6, 6)
        M = cv2.getRotationMatrix2D((W / 2, H / 2), a, 1.0)
        out = cv2.warpAffine(out, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
    out = np.clip(out.astype(np.int16) + np.random.randn(H, W, 1) * rng.uniform(2, 8), 0, 255).astype(np.uint8)
    return out


def synth_one(clean):
    """Make one degraded example + its labels from a clean (grade-10) card."""
    g = {a: rng.randint(1, 10) for a in ("centering", "corners", "edges", "surface")}
    card = cv2.resize(clean, (W, H))
    card = apply_centering(card, g["centering"])
    card = apply_corner_wear(card, g["corners"])
    card = apply_edge_wear(card, g["edges"])
    card = apply_surface(card, g["surface"])
    card = photo_aug(card)
    g["overall"] = min(g.values())                 # PSA-style: weakest aspect caps
    return card, g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clean_dir")
    ap.add_argument("side")
    ap.add_argument("--per", type=int, default=8, help="synthetic examples per clean image")
    ap.add_argument("--limit", type=int, default=0, help="cap base images (0=all)")
    ap.add_argument("--stride", type=int, default=1, help="use every Nth base (diverse sampling)")
    ap.add_argument("--no-detect", action="store_true",
                    help="base images are already clean full-card (catalog renders) — skip detection")
    args = ap.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    import glob
    bases = sorted(glob.glob(os.path.join(args.clean_dir, "*.jpg")) +
                   glob.glob(os.path.join(args.clean_dir, "*.jpeg")) +
                   glob.glob(os.path.join(args.clean_dir, "*.png")))
    if args.stride > 1:
        bases = bases[::args.stride]
    if args.limit:
        bases = bases[:args.limit]
    write_header = not MANIFEST.exists()
    n = 0
    with open(MANIFEST, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(COLS)
        for bi, bp in enumerate(bases):
            img = cv2.imread(bp)
            if img is None:
                continue
            if args.no_detect:
                det = cv2.resize(img, (W, H))      # catalog renders are already the clean card
            else:
                det = grading.detect_back(img) if args.side == "back" else grading.detect_card(img)
            for k in range(args.per):
                card, g = synth_one(det)
                fid = f"synth_{args.side}_{bi:05d}_{k}"
                cv2.imwrite(str(IMG / f"{fid}.jpg"), card)
                # one manifest row per aspect (so per-aspect training can read it)
                for aspect in ("centering", "corners", "edges", "surface", "overall"):
                    w.writerow([fid, f"{fid}.jpg", args.side, aspect, g[aspect],
                                f"synth:{aspect}{g[aspect]}", "synth"])
                n += 1
            if (bi + 1) % 25 == 0:
                print(f"{bi+1}/{len(bases)} bases, {n} synth images", flush=True)
    print(f"DONE: {n} synthetic images from {len(bases)} clean bases.")


if __name__ == "__main__":
    main()
