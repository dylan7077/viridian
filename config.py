"""Central configuration and paths for the Pokemon card grader."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # python-dotenv not installed yet
    pass

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

INDEX_PATH = DATA_DIR / "index.json"
IMAGE_CACHE = DATA_DIR / "card_images"

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
POKEMONTCG_API_KEY = os.getenv("POKEMONTCG_API_KEY", "")

# Channel the bot posts grade RESULTS into (e.g. #grading-results), keeping the
# submission channel (#grade-a-card) clean. When set and different from where the bot
# was mentioned, the bot posts the result there and leaves a short ack in place. Unset
# -> the bot replies in the channel it was mentioned (original behaviour).
DISCORD_RESULTS_CHANNEL_ID = os.getenv("DISCORD_RESULTS_CHANNEL_ID", "").strip()

# Base URL of the web app — the bot uses it to link to the corner-align tool.
WEB_BASE_URL = os.getenv("WEB_BASE_URL", "http://localhost:8000").rstrip("/")

# Perceptual-hash hamming distance threshold for a confident card match.
MATCH_MAX_DISTANCE = int(os.getenv("MATCH_MAX_DISTANCE", "22"))

# Slab OCR is ~60% of a grade's compute but only matters for graded slabs. When this
# is on, an obvious raw card (no PSA-style red label band) skips the OCR entirely.
# OFF by default: we shadow-log the cheap signal vs. real slab reads first, so we can
# confirm it never skips a true slab before trusting it. Set SLAB_OCR_GATE=1 to enable.
SLAB_OCR_GATE = os.getenv("SLAB_OCR_GATE", "0") not in ("0", "false", "False", "")

# ORB feature-matching re-rank of the pHash shortlist (robust to phone photos).
USE_ORB = os.getenv("USE_ORB", "1") not in ("0", "false", "False")
ORB_SHORTLIST = int(os.getenv("ORB_SHORTLIST", "6"))    # pHash candidates to re-rank
ORB_MIN_GOOD = int(os.getenv("ORB_MIN_GOOD", "12"))     # good matches for confidence
# pHash shortlist size fed to the global-ORB matcher. Matching against ~200 of 20k
# cards is ~140x faster than scanning all of them, with no real accuracy loss.
ORB_PHASH_SHORTLIST = int(os.getenv("ORB_PHASH_SHORTLIST", "200"))

# Canonical size we warp every detected card to before analysis.
# Pokemon cards are 2.5" x 3.5" (a 5:7 ratio).
CARD_W = 500
CARD_H = 700

POKEMONTCG_API = "https://api.pokemontcg.io/v2"   # legacy, no longer used

# TCGdex — open card database with newer sets + pricing, no API key required.
TCGDEX_API = "https://api.tcgdex.net/v2/en"
