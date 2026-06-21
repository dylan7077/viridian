# Night session status — read this first

Branch `night-grading-improvements` (NOT merged/pushed — your call). Run `bash scripts/run_tests.sh`
to verify (all pass). Full detail in `FINDINGS.md`; this is the TL;DR.

## What's solid and verified
- **Won't crash / won't fall over**: empty/tiny/malformed/non-image uploads, non-card photos
  (wall/paper/face flagged, not graded as "PSA 10"), pricing-API outage — all handled, no 500s.
  Load-tested: concurrent grades serialize + rate-limit, no OOM, server stays up.
- **Grades on what's validated**: centering (PSA-locked tolerances) + surface (separates real
  damage by +2.2 grades on your labeled cards). Corners/edges are reference-only (proven noise).
- **Capture quality gate**: warns on blur / glare / bad exposure / low-resolution before grading.
- **Pricing fixed**: EUR now uses current market (base Charizard €687 vs the old stale €513).
- **Deterministic**: same photo → same grade every time. Works on web, Discord bot, and CLI.

## Needs YOU (can't be done unsupervised)
1. **Roboflow dataset** (turns corners/edges from noise into a real signal): get a free key at
   app.roboflow.com → `ROBOFLOW_API_KEY=xxx .venv/bin/python scripts/eval_roboflow_grader.py`.
   The pipeline is built and waiting on that one key.
2. **Centering leeway decision**: the table grades 60/40 as a 10 (strict PSA = 9). Generous —
   keep it or tighten `grading._centering_to_grade`. Flagged, locked by a test so it won't drift.
3. **Merge**: say the word and I'll merge to `main` / open a PR.

## Honest limits (don't oversell)
- Corner/edge sub-grades aren't reliable from a single photo — fixed only by the trained model
  (needs the Roboflow data or a PSA-labeled GPU retrain). The existing CNN is mis-calibrated.
- Surface can over-flag holo/foil; weighted conservatively to limit harm.
- Sell it as "honest centering + surface screening with retake guidance," not "full PSA sub-grades."

## How to verify my work
```
bash scripts/run_tests.sh                 # all pipeline + robustness tests
python3 scripts/validate_labeled.py       # accuracy vs your labeled cards
git log --oneline 3b1a66d..HEAD           # everything I changed, with reasons
```
