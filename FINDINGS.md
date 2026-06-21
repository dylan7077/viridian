# Grading reliability — findings (2026-06-21)

Evidence-based assessment of what the CV grader can and can't do, and the only real path
to TAG-like reliability. Measured with `scripts/grade_testset.py` (30-photo set) and over
200/22,020 reference scans in `data/card_images/`.

## What's reliable
- **Card detection**: 30/30 photos detected and warped (Hough-primary). Solid.
- **Centering, L/R axis**: the objective, reproducible metric. Synthetic cards with known
  border widths are recovered exactly (`scripts/test_centering.py`, 6/6 L/R and T/B).

## What's NOT reliable (don't present these as real sub-grades)
- **Centering, T/B axis on real photos**: the gradient frame-finder goes ~neutral on the
  vertical axis by design (asymmetric card layout), so nearly every photo reports T/B
  `50/50`. The colour-border path *can* measure T/B (proven on synthetics) but isn't the
  one that runs on a tight crop. Net: centering is effectively **L/R-only** on real photos.
- **Corners / edges (whitening)**: systematically pessimistic AND near-blind to real wear.
  - On 200 pristine reference scans (perfect by definition): corners mean **5.45 / median 5**
    (only 1/200 scored 10), edges mean 7.24. The detector reads *artwork near the border* as
    wear — false positives, not real defects.
  - Recall on real fray is ~0: `_wear_frac` is *relative* (whitening must out-bright its
    neighbours), so whitening that fills the sampled region reads as "uniformly bright =
    expected". Painted partial fray was detected 0–1/12.
  - Tried a thin-perimeter-band measure: fixed clean-card false positives (corners
    5.45 → **9.47** mean on scans) but on real *photos* it sits on crop-bleed/glare at the
    very edge and swung grades unpredictably (8→4, 10→7 alongside the wins). Reverted —
    it swaps one false-positive source for another; not a net win on the real use case.
- **Surface**: the one heuristic with a real (if weak) signal — top-hat scratch density
  detects heavy minority-fill scratches (`test_defects.py`: surface 9 → 7). Still runs
  pessimistic on holo/foil and clean cards (mean ~7).

## Why (the root cause)
TAG/PSA-grade reliability comes mostly from **controlled capture**: a fixed scanner,
identical lighting every time, very high DPI, and multiple light angles that reveal surface
defects. From a single uncontrolled phone photo, corner/edge/surface defects are not
separable from artwork, glare, and lighting — no amount of classic-CV tuning closes that.

## Recommended path (in order)
1. **Constrain capture** — the highest-leverage, near-zero-compute lever. Enforce dark
   contrasting background, fill-frame, shoot-straight, even lighting (the code already
   rejects bad crops — make it a hard quality gate and guide the user). This alone improves
   reliability more than any algorithm change.
2. **Lead with centering (L/R)** as the headline grade + confidence; stop presenting
   corners/edges/surface as if they're real sub-grades (they're labeled low-confidence in
   code — reflect that in the UI too).
3. **Surface/corner defects need a trained model**, not heuristics — a small CNN on labeled
   PSA/TAG-graded card photos. That's the only route to real sub-grades from photos. Until
   then, an LLM-vision *screening hint* ("possible edge whitening top-left", clearly labeled
   "not a grade") is the honest stopgap.

## Guardrails added this session
- `scripts/grade_testset.py` — full-pipeline table + JSON snapshot, `--diff` vs last run,
  de-dupes byte-identical images (the photo set is 20 unique; 21–30 duplicate 11–20).
- `scripts/test_centering.py` — synthetic ground truth for centering (L/R + T/B).
- `scripts/test_defects.py` — guards surface-scratch detection; documents the corner/edge
  limits as executable notes.
- `src/engine.py` — decode now caps pixels (2200px long edge) before CV: closes the
  decompression-bomb OOM hole the 10 MB byte cap missed.
