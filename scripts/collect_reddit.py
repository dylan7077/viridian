"""Grab labelled graded-card images from Reddit and store them RAW on disk for later
local processing (crop / OCR-validate / dedup / train happen at home, not here).

Read-only via Reddit's public JSON API (descriptive User-Agent, polite rate limit). For
every image post in grading subreddits we parse the claimed grade + company from the title
and save the raw image plus a metadata record. No cropping or OCR on the box — keep
everything; sort it out locally. Resumable (skips images/posts already grabbed), rate
limited, logs progress — built to sit on the VPS unattended.

Output:
    data/raw_collect/reddit/<md5>.jpg          raw images
    data/raw_collect/metadata.jsonl            one JSON line per image

Usage: python scripts/collect_reddit.py [--max 8000] [--sleep 1.5]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import traceback

import requests
from requests.auth import HTTPBasicAuth

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

OUT = config.DATA_DIR / "raw_collect"
IMGDIR = OUT / "reddit"
META = OUT / "metadata.jsonl"

UA = "viridian-grader-dataset/0.3 (research; contact dylroberts82@gmail.com)"

# Reddit blocks unauthenticated JSON now (429). Application-only OAuth (client_credentials)
# needs just a free app's id + secret — no user login. Set these in the env / .env.
RID = os.getenv("REDDIT_CLIENT_ID", "").strip()
RSECRET = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()

_token = {"val": None, "exp": 0}


def reddit_token(sess):
    """App-only OAuth bearer token (cached until it nears expiry)."""
    if _token["val"] and time.time() < _token["exp"] - 60:
        return _token["val"]
    r = sess.post("https://www.reddit.com/api/v1/access_token",
                  auth=HTTPBasicAuth(RID, RSECRET),
                  data={"grant_type": "client_credentials"},
                  headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    j = r.json()
    _token["val"] = j["access_token"]
    _token["exp"] = time.time() + j.get("expires_in", 3600)
    return _token["val"]


def notify(msg):
    """Post a one-line progress update to Discord (best-effort)."""
    if not WEBHOOK:
        return
    try:
        requests.post(WEBHOOK, json={"content": msg}, timeout=10)
    except Exception:
        pass

SUBS = ["PSAcard", "gradedcards", "PokemonTCG", "PokeInvesting", "mintbat",
        "pkmntcgcollections", "PokemonCardValue", "PokeInvesting", "cardgrading"]
# Lead with the grades the histogram is short on; "returns/results" threads add spread.
QUERIES = ["PSA 10 pokemon", "PSA 9 pokemon", "PSA 8 pokemon", "PSA 7 pokemon",
           "PSA 6 pokemon", "PSA 5 pokemon", "PSA 4 pokemon", "PSA 3 pokemon",
           "PSA 2 pokemon", "BGS 9.5 pokemon", "BGS 9 pokemon", "BGS 8.5 pokemon",
           "CGC 9 pokemon", "CGC 10 pokemon", "grading returns", "psa results",
           "grading results", "psa came back"]

GRADE_RE = re.compile(r"\b(PSA|BGS|CGC|SGC)\s*[-:]?\s*(10|[1-9](?:\.5)?)\b", re.I)


def parse_grade(title):
    m = GRADE_RE.search(title or "")
    return (m.group(1).upper(), float(m.group(2))) if m else (None, None)


def image_urls(post):
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
        for im in (post.get("preview") or {}).get("images") or []:
            src = im.get("source", {}).get("url")
            if src:
                urls.append(src.replace("&amp;", "&"))
    return urls[:6]


def listing(sess, sub, query, after):
    tok = reddit_token(sess)
    r = sess.get(f"https://oauth.reddit.com/r/{sub}/search",
                 params={"q": query, "restrict_sr": 1, "sort": "new", "limit": 100,
                         "t": "all", **({"after": after} if after else {})},
                 headers={"User-Agent": UA, "Authorization": f"Bearer {tok}"}, timeout=20)
    if r.status_code == 401:                     # token expired mid-run -> refresh once
        _token["val"] = None
        return listing(sess, sub, query, after)
    if r.status_code != 200:
        return [], None, r.status_code
    d = r.json().get("data", {})
    return [c["data"] for c in d.get("children", [])], d.get("after"), 200


def load_seen():
    md5s, posts = set(), set()
    if META.exists():
        with open(META) as f:
            for line in f:
                try:
                    j = json.loads(line)
                    md5s.add(j.get("md5"))
                    posts.add(j.get("permalink"))
                except Exception:
                    pass
    return md5s, posts


def hist(meta_path):
    from collections import Counter
    c = Counter()
    if meta_path.exists():
        with open(meta_path) as f:
            for line in f:
                try:
                    c[int(round(json.loads(line)["grade"]))] += 1
                except Exception:
                    pass
    return dict(sorted(c.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=8000)
    ap.add_argument("--sleep", type=float, default=1.5)
    args = ap.parse_args()
    if not (RID and RSECRET):
        print("ERROR: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET (free app at "
              "reddit.com/prefs/apps -> 'script' type). Aborting.", flush=True)
        sys.exit(2)
    IMGDIR.mkdir(parents=True, exist_ok=True)
    md5s, posts = load_seen()
    sess = requests.Session(); sess.headers["User-Agent"] = UA
    mf = open(META, "a")
    added = dl = 0
    t0 = time.time()
    last_ping = 0

    def log(m):
        print(f"[{int(time.time()-t0):6d}s] {m}", flush=True)

    log(f"start. have {len(md5s)} images, {len(posts)} posts. target +{args.max}.")
    notify(f"📥 **Reddit collector started** on the VPS — have {len(md5s)} images, "
           f"targeting +{args.max}. I'll report progress here.")
    try:
        for sub in SUBS:
            for query in QUERIES:
                after = None
                for _ in range(10):
                    if added >= args.max:
                        raise StopIteration
                    try:
                        items, after, code = listing(sess, sub, query, after)
                    except Exception as e:
                        log(f"{sub}/{query!r} err {e}"); time.sleep(args.sleep * 3); break
                    if code == 429:
                        log("rate limited; backing off"); time.sleep(30); continue
                    time.sleep(args.sleep)
                    if not items:
                        break
                    for post in items:
                        if post.get("permalink") in posts:
                            continue
                        posts.add(post.get("permalink"))
                        company, grade = parse_grade(post.get("title", ""))
                        if grade is None:
                            continue
                        for url in image_urls(post):
                            try:
                                resp = sess.get(url, timeout=20)
                                if resp.status_code != 200 or len(resp.content) < 3000:
                                    continue
                                md5 = hashlib.md5(resp.content).hexdigest()
                                dl += 1
                                if md5 in md5s:
                                    continue
                                md5s.add(md5)
                                ext = ".png" if resp.content[:4] == b"\x89PNG" else ".jpg"
                                (IMGDIR / f"{md5}{ext}").write_bytes(resp.content)
                                mf.write(json.dumps({
                                    "md5": md5, "file": f"reddit/{md5}{ext}", "source": "reddit",
                                    "sub": sub, "company": company, "grade": grade,
                                    "title": post.get("title", "")[:300],
                                    "permalink": post.get("permalink"),
                                    "created_utc": post.get("created_utc"), "url": url}) + "\n")
                                mf.flush()
                                added += 1
                                if added % 50 == 0:
                                    log(f"+{added} (dl {dl}) hist={hist(META)}")
                                if time.time() - last_ping > 600:   # Discord every ~10 min
                                    last_ping = time.time()
                                    notify(f"📈 **+{added}** new images (downloaded {dl}). "
                                           f"By grade: `{hist(META)}`")
                                time.sleep(args.sleep)
                            except Exception:
                                continue
    except StopIteration:
        pass
    except Exception:
        log("FATAL\n" + traceback.format_exc())
    finally:
        mf.close()
        log(f"DONE +{added} new (downloaded {dl}). histogram: {hist(META)}")
        notify(f"✅ **Reddit collector finished** — added **{added}** new images "
               f"(downloaded {dl}).\nBy grade: `{hist(META)}`")


if __name__ == "__main__":
    main()
