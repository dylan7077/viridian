"""Collect labelled graded-card images from Reddit into the training set.

Read-only via Reddit's public JSON API (a descriptive User-Agent, polite rate limit).
For each image post in grading subreddits: parse the claimed grade from the title, then
OCR the slab label to VALIDATE it (titles lie — the printed grade is ground truth), crop
the card with the production detector, perceptual-hash dedup, and append to the same
`data/training/manifest.csv`. Resumable (skips phashes already in the manifest) and safe to
run unattended — designed to sit on the VPS and fill the histogram while you're away.

Usage:
    python scripts/scrape_reddit.py [--max 3000] [--sleep 2.0] [--require-ocr]
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import time
import traceback

import cv2
import imagehash
import numpy as np
import requests
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src import grading, slab

OUT = config.DATA_DIR / "training"
IMG = OUT / "images"
MANIFEST = OUT / "manifest.csv"
COLS = ["phash", "file", "side", "aspect", "grade", "label_raw", "source"]

UA = "viridian-grader-dataset/0.2 (research; contact dylroberts82@gmail.com)"

# Grading subreddits + grade-targeted queries. We lead with the grades our histogram is
# short on (mint 8-10 and the middle 5-7); low grades come from "grading returns" threads.
SUBS = ["PSAcard", "gradedcards", "PokemonTCG", "PokeInvesting", "mintbat", "pkmntcgcollections"]
QUERIES = ["PSA 10", "PSA 9", "PSA 8", "PSA 7", "PSA 6", "PSA 5", "PSA 4", "PSA 3",
           "BGS 9.5", "BGS 9", "BGS 8.5", "CGC 9", "grading returns", "psa results"]

GRADE_RE = re.compile(r"\b(PSA|BGS|CGC|SGC)\s*[-:]?\s*(10|[1-9](?:\.5)?)\b", re.I)


def parse_grade(title):
    m = GRADE_RE.search(title or "")
    if not m:
        return None, None
    return m.group(1).upper(), int(round(float(m.group(2))))


def image_urls(post):
    """All direct image URLs from a post (direct link, i.redd.it, gallery, preview)."""
    urls = []
    u = post.get("url_overridden_by_dest") or post.get("url") or ""
    if re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", u, re.I):
        urls.append(u)
    if post.get("is_gallery") and isinstance(post.get("media_metadata"), dict):
        for m in post["media_metadata"].values():
            s = (m or {}).get("s", {})
            if s.get("u"):
                urls.append(s["u"].replace("&amp;", "&"))
    if not urls:
        prev = (post.get("preview") or {}).get("images") or []
        if prev:
            src = prev[0].get("source", {}).get("url")
            if src:
                urls.append(src.replace("&amp;", "&"))
    return urls[:4]


def listing(session, sub, query, after):
    url = f"https://www.reddit.com/r/{sub}/search.json"
    params = {"q": query, "restrict_sr": 1, "sort": "new", "limit": 100, "t": "all"}
    if after:
        params["after"] = after
    r = session.get(url, params=params, timeout=20)
    if r.status_code != 200:
        return [], None
    d = r.json().get("data", {})
    return [c["data"] for c in d.get("children", [])], d.get("after")


def load_seen():
    seen = set()
    if MANIFEST.exists():
        with open(MANIFEST) as f:
            for row in csv.DictReader(f):
                seen.add(row["phash"])
    return seen


def histogram():
    from collections import Counter
    c = Counter()
    if MANIFEST.exists():
        with open(MANIFEST) as f:
            for row in csv.DictReader(f):
                c[row["grade"] or "unlabelled"] += 1
    return dict(sorted(c.items(), key=lambda x: (x[0] == "unlabelled", x[0])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=3000, help="stop after this many NEW images")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds between requests (be polite)")
    ap.add_argument("--require-ocr", action="store_true",
                    help="keep only images whose slab OCR confirms a grade (highest quality)")
    args = ap.parse_args()

    IMG.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    write_header = not MANIFEST.exists()
    added = downloaded = ocr_ok = 0
    t0 = time.time()

    def log(msg):
        print(f"[{int(time.time()-t0):5d}s] {msg}", flush=True)

    log(f"start. existing manifest: {sum(histogram().values())} rows. target +{args.max} new.")
    out = open(MANIFEST, "a", newline="")
    w = csv.writer(out)
    if write_header:
        w.writerow(COLS)

    try:
        for sub in SUBS:
            for query in QUERIES:
                after = None
                for page in range(10):                  # up to ~1000 posts per (sub,query)
                    if added >= args.max:
                        raise StopIteration
                    try:
                        posts, after = listing(sess, sub, query, after)
                    except Exception as e:
                        log(f"listing {sub}/{query!r} error: {e}")
                        time.sleep(args.sleep * 2)
                        break
                    time.sleep(args.sleep)
                    if not posts:
                        break
                    for post in posts:
                        company, grade = parse_grade(post.get("title", ""))
                        if grade is None:
                            continue
                        for url in image_urls(post):
                            try:
                                resp = sess.get(url, timeout=20)
                                if resp.status_code != 200 or len(resp.content) < 2000:
                                    continue
                                arr = np.frombuffer(resp.content, np.uint8)
                                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                                if img is None:
                                    continue
                                downloaded += 1
                                # OCR-validate against the printed slab label
                                ocr_grade = None
                                try:
                                    lab = slab.read_slab_label(img)
                                    ocr_grade = (lab or {}).get("grade")
                                except Exception:
                                    pass
                                validated = ocr_grade is not None and int(ocr_grade) == grade
                                if validated:
                                    ocr_ok += 1
                                elif args.require_ocr:
                                    continue
                                # crop the card + dedup
                                card = grading.detect_card(img)
                                ph = str(imagehash.phash(Image.fromarray(
                                    cv2.cvtColor(card, cv2.COLOR_BGR2RGB))))
                                if ph in seen:
                                    continue
                                seen.add(ph)
                                cv2.imwrite(str(IMG / f"{ph}.jpg"), card)
                                w.writerow([ph, f"{ph}.jpg", "front", "overall", grade,
                                            f"reddit:{company}{grade}{'+ocr' if validated else ''}",
                                            f"reddit:{sub}"])
                                out.flush()
                                added += 1
                                if added % 25 == 0:
                                    log(f"+{added} new (dl {downloaded}, ocr-confirmed {ocr_ok}) | {histogram()}")
                                time.sleep(args.sleep)
                            except Exception:
                                continue
    except StopIteration:
        pass
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
    finally:
        out.close()
        log(f"DONE. added {added} new images (downloaded {downloaded}, ocr-confirmed {ocr_ok}).")
        log(f"final histogram: {histogram()}")


if __name__ == "__main__":
    main()
