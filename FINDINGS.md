# Grading reliability — findings (2026-06-21)

Evidence-based assessment of what the CV grader can and can't do, and the only real path
to TAG-like reliability. Measured with `scripts/grade_testset.py` (30-photo set) and over
200/22,020 reference scans in `data/card_images/`.

## Validated against REAL labeled cards (2026-06-21, `scripts/validate_labeled.py`)
Corrects the earlier "no ground truth" claim — there IS coarse labeled data (clean vs
damaged) in `~/Documents/card data/`. Measured:
- **Surface heuristic WORKS**: 49 real surface-damaged fronts vs 30 clean — scuff 0.250 vs
  0.131, surface grade 6.4 vs 8.6, **+2.2 grade separation** (81% balanced acc at scuff 0.18).
- **Corner/edge heuristics are NOISE**: clean vs labeled-damaged backs separate by **−0.1
  (corners) and −0.5 (edges)** — zero predictive signal, confirming the synthetic finding.
- **Action taken**: `_combine` now EXCLUDES corners/edges from the grade math (still reported
  as low-conf info) and keeps centering (anchor) + surface (down-pull). Result: overall
  clean-vs-damaged separation **+1.4 → +1.6**, and clean cards stopped being false-penalised
  (mean 8.5 → 8.9). Verified offline against the labeled cache.

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

## The existing trained CNN doesn't fix it (yet)
`data/training/grader.onnx` is wired in (`onnx_grader`, surfaced alongside the heuristic).
Evaluated on the same reference scans, it shows the **same calibration problem**: clean
cards score corners 5.3 / edges 4.9 / surface 6.4 mean (centering 8.8 is better but still
dips to 6.1). On real photos it broadly agrees with the heuristics, including their
pessimism. So the model as currently trained is **not yet a trustworthy sub-grade source** —
it needs retraining against real PSA/CGC-labeled photos (and the training labels validated;
note: `data/card_images` catalog scans are *assumed* near-mint, not verified). Until then,
keep the CNN inert/alongside, not driving the grade.

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

## Resources & the unblock (2026-06-21)
The corner/edge gap needs labeled defect data. Surveyed what's online:
- **[Roboflow "Card Grader"](https://universe.roboflow.com/group-6-major-project/card-grader)** — ~600 imgs labeled for edge/corner wear + scratches. THE relevant set for our gap. Requires a FREE Roboflow API key to export (confirmed: no keyless download).
- **[crimsonthinker/psa_pokemon_cards](https://github.com/crimsonthinker/psa_pokemon_cards)** — VGG16 dual-branch grader, ~0.5 MAE. Code only (no data/weights shipped). Best architecture to copy.
- **[jshan9078/PSAGradePredictor](https://github.com/jshan9078/PSAGradePredictor)** — dual-branch ResNet + **ordinal-aware loss** (the right loss for 1-10 grades). Code only.
- **[PSA grading standards](https://www.psacard.com/gradingstandards)** — exact tolerances (front: 55/45=PSA10, 60/40=9, 65/35=8) to verify `_centering_to_grade`.
- HF `tooni/pokemoncards` = catalog metadata only (no grades) — not useful.

**Ready-to-run pipeline** (`scripts/eval_roboflow_grader.py`): pulls the Card Grader set and
reports whether its labels cover corners/edges/surface and if there's enough to train.
Blocked only on a free key. To unblock (30 sec):
```
# 1. sign up free at https://app.roboflow.com  -> Settings -> copy Private API Key
# 2. then:
ROBOFLOW_API_KEY=xxxxx .venv/bin/python scripts/eval_roboflow_grader.py
```
Offline self-test (no key needed): `python3 scripts/eval_roboflow_grader.py --selftest`

NOTE: installing `roboflow` swapped opencv-python -> opencv-python-headless in the venv
(roboflow forces it). Harmless — grader is server-side, cv2 4.10 works, all tests pass; and
headless is actually leaner for the VPS. requirements.txt unchanged.

## Decision to make: centering leeway (2026-06-21)
`_centering_to_grade` maps 60/40 -> PSA 10, but strict PSA is 60/40 = 9 (PSA10 = 55/45). The
repo applies a documented ~5% leeway = slightly generous. Generous grades risk disappointing
buyers who later submit to PSA and get one grade lower. It's defensible (PSA tolerances are
"approximately" and graders apply leeway), but it's YOUR call: keep it, or tighten the table in
grading._centering_to_grade to match PSA exactly. Locked by scripts/test_centering.py so it
can't drift silently. Identification + price + grade verified working end-to-end (e.g. 1.jpg ->
"Froakie" Chaos Rising, confident 0.98, grade 10, priced).

## Known minor limitation: resolution sensitivity (2026-06-21)
Grades can shift by ~1 across EXTREME input-resolution differences (e.g. a card's centering
read 8 at 2200px vs 10 at 700px; surface catches more scuff at higher res). Root cause:
detection precision + source detail vary with resolution, even though the warp is fixed-size.
Mitigated in practice: decode caps every upload at 2200px and typical phone photos exceed that,
so normal uploads all normalize to 2200px and grade consistently (verified deterministic at a
fixed resolution). Only genuinely small uploads (<~1500px native) drift. Not fixed — a
detection-resolution-normalisation change is riskier than this edge case warrants. If it
matters later: standardise the working resolution before detection, or add a "low-resolution"
capture warning (the gate already warns on blur/glare/exposure).

## Known edge: centering measurement on hard framings (2026-06-21)
On an unusual framing (card with a large uniform background margin) the heuristic centering
can read confidently wrong (e.g. 22/78 where the card is actually ~50/50) — the documented
"hard tail" of single-photo centering. Mitigations in place: (1) once a card is IDENTIFIED,
centering is re-measured against its clean reference image (refgrade), which is reliable —
this is the common path; (2) the gradient `frame` method handles the L/R axis well. Verified:
0/20 real test photos hit low-confidence centering (all `reference` or `frame`). The failure
needs a hard framing AND an identification miss simultaneously. The UI now caveats any
low-confidence centering ("low confidence — reshoot flat"). A real fix = the centering-measure
rewrite (risky, many prior attempts) — out of scope for an unsupervised pass.
