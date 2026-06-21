#!/usr/bin/env python3
"""Ground-truth check for centering measurement. We have no hand-labeled grades for the
photo set, so we synthesize cards with KNOWN border widths and assert measure_centering
recovers them. This is what lets us change the measurement and prove it got better, not
just different. Run: python3 scripts/test_centering.py
"""
import sys
from pathlib import Path
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import grading  # noqa: E402


def make_card(L, R, T, B, w=600, h=820, border=(40, 200, 230)):
    """A fake warped card: solid coloured border of the given per-side widths, with a
    textured inner 'design' so the gradient frame-finder has a real edge to lock onto.
    Mimics a yellow-bordered Pokemon card cropped to its edges."""
    card = np.full((h, w, 3), border, np.uint8)
    inner = np.empty((h - T - B, w - L - R, 3), np.uint8)
    # textured content (not flat) so the frame->design gradient is strong on all 4 sides
    rng = np.linspace(20, 235, inner.shape[1], dtype=np.uint8)
    inner[:] = np.dstack([np.tile(rng, (inner.shape[0], 1))] * 3)
    inner[::7] = (10, 10, 10)        # horizontal lines -> strong vertical (T/B) gradient too
    card[T:h - B, L:w - R] = inner
    return card


def axis_pct(a, b):
    return round(100 * max(a, b) / (a + b), 1)


def check(L, R, T, B, tol=4.0):
    card = make_card(L, R, T, B)
    r = grading.measure_centering(card)
    assert r["ok"], f"measure failed for L{L} R{R} T{T} B{B}: {r.get('note')}"
    want_lr, want_tb = axis_pct(L, R), axis_pct(T, B)
    got_lr = float(r["left_right"].split("/")[0])
    got_lr = max(got_lr, 100 - got_lr)
    got_tb = float(r["top_bottom"].split("/")[0])
    got_tb = max(got_tb, 100 - got_tb)
    dlr, dtb = abs(got_lr - want_lr), abs(got_tb - want_tb)
    status = "ok " if dlr <= tol and dtb <= tol else "OFF"
    print(f"  [{status}] L{L} R{R} T{T} B{B}  ({r['method']:>6})  "
          f"L/R want {want_lr} got {r['left_right']} (d{dlr:.0f})  "
          f"T/B want {want_tb} got {r['top_bottom']} (d{dtb:.0f})")
    return dlr <= tol, dtb <= tol


def main():
    cases = [
        (50, 50, 60, 60),     # perfectly centered
        (40, 60, 60, 60),     # L/R off, T/B centered  -> lr 60/40
        (30, 70, 60, 60),     # L/R more off           -> lr 70/30
        (50, 50, 40, 80),     # T/B off, L/R centered  -> tb 67/33   <-- the axis we suspect is broken
        (50, 50, 30, 90),     # T/B more off
        (35, 65, 45, 75),     # both axes off
    ]
    lr_ok = tb_ok = 0
    print("synthetic centering recovery (tol 4%):")
    for c in cases:
        a, b = check(*c)
        lr_ok += a; tb_ok += b
    n = len(cases)
    print(f"\nL/R axis recovered {lr_ok}/{n} | T/B axis recovered {tb_ok}/{n}")
    if tb_ok < n:
        print("  -> T/B measurement is the weak axis (suspected). This is the fix target.")
    assert lr_ok == n, "L/R centering regressed — this axis must stay accurate."


if __name__ == "__main__":
    main()
