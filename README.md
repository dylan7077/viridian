# Viridian — Card Grading Lab

**GRADE WITHOUT GUESSWORK.**

A locally-run Pokémon card grader. Snap a photo (web app or Discord bot) and
Viridian flattens the card, measures **centering** against PSA tolerances,
estimates condition, **identifies** the exact card, and returns an
**estimated grade + market value** — all on your own machine.

---

## In plain English

Think of Viridian as a **personal, automated card-grading station** that lives
on your computer instead of in a lab.

1. **You take a photo** of a Pokémon card — from the website, your phone via
   Discord, or the command line.
2. **It finds the card** in the photo and straightens it out, as if you'd
   scanned it perfectly flat and face-on.
3. **It measures the card** the way a grader would — chiefly how well-**centered**
   the print is inside its borders (the one thing a camera can measure
   precisely), plus rough reads on corners, edges and surface wear.
4. **It works out *which* card it is** by comparing your photo against a local
   database of ~20,000 real cards — so it knows it's, say, *Charizard, Base Set,
   #4* and not just "a fire card."
5. **It gives you a grade (1–10) and a value** — what the card is roughly worth
   raw, and what it might fetch graded.

If you photograph a card already sealed in a **PSA slab**, it just reads the
printed grade off the label instead of re-grading it.

Everything runs offline except the price/card-data lookups. Nothing is uploaded
to a grading company; there are no fees and no waiting weeks for results.

> **Honesty first.** Of PSA's four criteria, only **centering** is truly
> measurable with classical computer vision — it's a geometric border-ratio
> measurement and Viridian does it for real. **Corners, edges and surface are
> heuristic estimates** (and an experimental trained model, see below), not
> certified sub-grades. Values come from [pokemontcg.io](https://pokemontcg.io)
> market prices, refined with real graded sold-comps where available — a
> ballpark, not an appraisal. Viridian is for personal use and is **not
> affiliated with or endorsed by PSA**.

---

## What you get

- **Web app** — a dark, "techbio" styled site (inspired by cure51.com) with:
  - drag-and-drop **or live camera capture**, plus a manual corner-align tool
    for tricky photos,
  - a holographic card display and shareable result links (`/g/<token>`),
  - a **Library** of browsable cards and an **Activity** feed + stats of recent
    grades.
- **Discord bot** — DM or post a card photo from your phone, get a grade + value
  reply with a generated result card.
- **CLI** — grade a local image for quick testing.

All three share one engine (`src/engine.py`), so they always agree.

---

## Setup

```bash
cd pokemon-grader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env
```

### Build the card index (needed for identification + pricing)

This downloads card metadata + images from pokemontcg.io and computes a
perceptual hash (and ORB features) for each, so Viridian can recognise your
card. The full database is large; start small:

```bash
# A few sets to try it out (fast):
python scripts/build_index.py --sets base1,base2,swsh1

# Or cap the count:
python scripts/build_index.py --limit 1000

# Everything (slow — tens of thousands of cards). Resumable; re-run anytime:
python scripts/build_index.py
```

> Tip: a free [pokemontcg.io API key](https://dev.pokemontcg.io/) in `.env`
> raises your rate limit and speeds this up.

---

## Run

### Web app
```bash
uvicorn web.app:app --reload --port 8000
# open http://localhost:8000
```

### Discord bot
1. Create an app + bot at <https://discord.com/developers/applications>,
   enable the **Message Content Intent**, and copy the token into `.env`.
2. ```bash
   python -m src.bot
   ```
3. Post a card photo in a channel the bot can see (or DM it).

### CLI (quick test, no index needed for grading)
```bash
python -m src.cli path/to/card.jpg
```

---

## How grading works

Every front-end feeds the same pipeline in `src/engine.py`:

The overall grade is driven by the signals that are **validated against real labeled cards**
(centering + surface); corners/edges are shown for reference but don't move the grade, because
on single photos they don't reliably separate clean from damaged (see `FINDINGS.md`).

| Step | What happens | Reliability |
|------|--------------|-------------|
| 0. Capture check | Flags blur / glare / bad exposure and asks for a retake before grading garbage | guards quality |
| 1. Detect & flatten | Hough-line / contour detection → perspective warp to a face-on card (manual corners override it if you align by hand) | solid |
| 2. Centering | Inner frame located; border ratios mapped to PSA tolerances | **measured · counts** |
| 3. Surface | Scratch/scuff density (top-hat) — validated to separate real surface damage by ~2 grades | rough · **counts** |
| 4. Corners / edges | "Whitening" at corners/edges — **reported for reference only**, excluded from the grade (non-predictive on single photos) | experimental |
| 5. Trained grader | An ONNX CNN predicts a grade too, shown **alongside** (not replacing) — not yet trusted (mis-calibrated, needs retraining on labeled photos) | experimental |
| 6. Identify | pHash narrows ~20k cards to a shortlist, then ORB feature matching confirms the exact card — or honestly says "couldn't match" rather than guessing | robust to phone photos |
| 7. Re-measure centering | Once identified, centering is re-measured against the card's clean reference image — reliable even on holo / full-art | **measured** |
| 8. Slab shortcut | If it's a sealed PSA slab, OCR reads the printed grade and skips re-grading | authoritative |
| 9. Value | pokemontcg.io market price × grade multiplier, overridden by **real graded sold-comps** (eBay / Cardmarket) when available | ballpark → real |

Tune match strictness with `MATCH_MAX_DISTANCE` / ORB settings in `.env`.

Run `python3 scripts/test_*.py` to verify the pipeline (centering, surface, capture gate,
robustness) and `scripts/validate_labeled.py` to re-check accuracy against labeled cards.

---

## Roadmap / upgrade paths

- **Trained sub-grades:** the ONNX CNN (`src/onnx_grader.py`, trained via
  `scripts/train_grader.py`) is the path to making corners / edges / surface as
  trustworthy as centering. Currently advisory.
- **True graded values:** keep expanding real sold-comp coverage so the rough
  multiplier is only a fallback.
- **Publish:** the web app is a standard FastAPI service (Dockerfile + fly.toml
  included) — deploy behind any host when you're ready to share it.

---

## Credits & inspiration

- [simeydotme/pokemon-cards-css](https://github.com/simeydotme/pokemon-cards-css) — holographic card effects
- [chase-mew/pokemon-tcg-pocket-cards](https://github.com/chase-mew/pokemon-tcg-pocket-cards) — open card data
- [prateekt/pokemon-card-recognizer](https://github.com/prateekt/pokemon-card-recognizer) — card recognition reference
- [pokemontcg.io](https://pokemontcg.io) — card metadata & market pricing
- [cure51.com](https://www.cure51.com) — design inspiration

---

## Project layout

```
pokemon-grader/
├─ config.py              # paths, env, tunables
├─ requirements.txt
├─ .env.example
├─ src/
│  ├─ engine.py           # shared pipeline (grade → identify → price)
│  ├─ grading.py          # CV: detect, centering, corners, edges, surface
│  ├─ onnx_grader.py      # trained CNN grade (experimental, runs on CPU)
│  ├─ matching.py         # pHash card identification
│  ├─ orb_index.py        # ORB feature matching / confirmation
│  ├─ refgrade.py         # re-measure centering vs reference image
│  ├─ slab.py             # PSA slab detection + label OCR
│  ├─ pricing.py          # raw market value lookup
│  ├─ graded_pricing.py   # real graded sold-comp prices
│  ├─ ukpricing.py        # GBP pricing
│  ├─ db.py / activity.py # grade history + activity feed
│  ├─ share.py / sharecard.py / slab image rendering
│  ├─ bot.py              # Discord front-end
│  └─ cli.py              # local image tester
├─ scripts/
│  ├─ build_index.py      # build data/index.json (pHash)
│  ├─ build_orb_db.py     # build the ORB feature database
│  ├─ train_grader.py     # train the CNN grader
│  └─ …                   # dataset, scraping, profiling, maintenance tools
└─ web/
   ├─ app.py              # FastAPI server (grade, library, activity, share APIs)
   └─ static/             # home, library, activity, share pages + styles
```
