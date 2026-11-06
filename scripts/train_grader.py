"""Multi-aspect grading model (centering / corners / edges / surface).

Trains on the synthetic set (each aspect balanced 1–10) and evaluates on REAL clean cards
(should predict HIGH on every aspect) — the honest transfer test. Images are PRELOADED into
RAM once (decode-once) so CPU epochs are model-bound, not disk-bound.

Usage: python scripts/train_grader.py --epochs 6 [--real-clean <dir> --side back] [--max-train N]
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import random
import re
import sys
import time
from collections import defaultdict

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from torchvision import models

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src import grading

ASPECTS = ["centering", "corners", "edges", "surface"]
IMG_DIR = config.DATA_DIR / "training" / "images"
MANIFEST = config.DATA_DIR / "training" / "manifest.csv"
MODEL_OUT = config.DATA_DIR / "training" / "grader.pt"
SIZE = 96
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)


def load_synth():
    by_file = defaultdict(dict)
    for r in csv.DictReader(open(MANIFEST)):
        if r["source"] == "synth" and r["aspect"] in ASPECTS and r["grade"]:
            by_file[r["file"]][r["aspect"]] = float(r["grade"])
    items = []
    for f, g in by_file.items():
        if all(a in g for a in ASPECTS) and (IMG_DIR / f).exists():
            items.append((str(IMG_DIR / f), [g[a] for a in ASPECTS]))
    return items


def preload(items):
    """Decode + resize once into a uint8 tensor [N,SIZE,SIZE,3] + labels [N,4]."""
    X = np.empty((len(items), SIZE, SIZE, 3), np.uint8)
    Y = np.empty((len(items), len(ASPECTS)), np.float32)
    for i, (p, g) in enumerate(items):
        im = cv2.imread(p)
        X[i] = cv2.cvtColor(cv2.resize(im, (SIZE, SIZE)), cv2.COLOR_BGR2RGB)
        Y[i] = g
        if (i + 1) % 4000 == 0:
            print(f"  preloaded {i+1}/{len(items)}", flush=True)
    return torch.from_numpy(X), torch.from_numpy(Y)


def norm(xb):
    # uint8 NHWC -> float NCHW normalized
    return (xb.permute(0, 3, 1, 2).float() / 255.0 - MEAN.to(xb.device)) / STD.to(xb.device)


def split_by_base(items, seed=0):
    """Keep all variants of a synthetic base card in one partition."""
    groups = defaultdict(list)
    for path, labels in items:
        match = re.fullmatch(r"synth_(front|back)_([0-9a-f]{64})_(\d+)\.jpg", os.path.basename(path))
        if not match:
            raise ValueError(f"Cannot establish synthetic base identity: {path}. "
                             "Regenerate legacy synthetic data with synth_damage.py.")
        groups[match.group(2)].append((path, labels))
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    if len(keys) < 2:
        raise ValueError("At least two distinct synthetic base cards are required")
    n_val = max(1, int(len(keys) * 0.15))
    return ([item for key in keys[n_val:] for item in groups[key]],
            [item for key in keys[:n_val] for item in groups[key]])


def build_model():
    m = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT)
    m.classifier[-1] = nn.Linear(m.classifier[-1].in_features, len(ASPECTS))
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--bs", type=int, default=64)
    ap.add_argument("--max-train", type=int, default=0)
    ap.add_argument("--real-clean", default="")
    ap.add_argument("--side", default="back")
    ap.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    ap.add_argument("--output", type=str, default=str(MODEL_OUT), help="output checkpoint path")
    args = ap.parse_args()
    torch.manual_seed(0)
    torch.set_num_threads(os.cpu_count() or 4)

    items = load_synth()
    train_items, val_items = split_by_base(items)
    if args.max_train:
        train_items = train_items[:args.max_train]
    device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available() else
                          "cpu" if args.device == "auto" else args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable in this PyTorch installation")
    print(f"Device: {device}; synthetic-only evaluation, grouped by base card", flush=True)
    print(f"{len(train_items)} train / {len(val_items)} validation images. preloading…", flush=True)
    t0 = time.time()
    Xv, Yv = preload(val_items)
    Xt, Yt = preload(train_items)
    print(f"preloaded in {time.time()-t0:.0f}s", flush=True)
    dl_tr = DataLoader(TensorDataset(Xt, Yt), batch_size=args.bs, shuffle=True)
    dl_va = DataLoader(TensorDataset(Xv, Yv), batch_size=128)

    model = build_model().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4)
    lossf = nn.SmoothL1Loss()
    for ep in range(args.epochs):
        model.train(); t = time.time()
        for xb, yb in dl_tr:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            lossf(model(norm(xb)), yb).backward()
            opt.step()
        model.eval(); errs = []
        with torch.no_grad():
            for xb, yb in dl_va:
                xb, yb = xb.to(device), yb.to(device)
                errs.append((model(norm(xb)) - yb).abs())
        mae = torch.cat(errs).mean(0)
        print(f"epoch {ep+1}/{args.epochs} ({time.time()-t:.0f}s)  val MAE  " +
              "  ".join(f"{a}={mae[i]:.2f}" for i, a in enumerate(ASPECTS)), flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    torch.save(model.state_dict(), args.output)
    print(f"saved {args.output}", flush=True)

    if args.real_clean:
        files = sorted(glob.glob(os.path.join(args.real_clean, "*.jpg")) +
                       glob.glob(os.path.join(args.real_clean, "*.jpeg")))[:60]
        model.eval(); preds = []
        with torch.no_grad():
            for fp in files:
                card = (grading.detect_back if args.side == "back" else grading.detect_card)(cv2.imread(fp))
                x = torch.from_numpy(cv2.cvtColor(cv2.resize(card, (SIZE, SIZE)), cv2.COLOR_BGR2RGB))[None]
                preds.append(model(norm(x.to(device))).squeeze(0).cpu().numpy())
        preds = np.array(preds)
        print(f"\n=== REAL CLEAN transfer test ({len(files)} cards — should be ~8-10) ===")
        for i, a in enumerate(ASPECTS):
            print(f"  {a:9} mean={preds[:,i].mean():.1f}  (>=8: {int((preds[:,i]>=8).sum())}/{len(files)})")


if __name__ == "__main__":
    main()
