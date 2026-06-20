"""Collect labelled graded-card images from Reddit — ONE search per process invocation.

Long-lived browser scraping through a proxy wedges (a stalled page-load hangs the whole
run at 0% CPU). So this does exactly one (subreddit, query, sort) search with a fresh
browser and exits; the bash driver (run_collect.sh) loops all combos with a hard `timeout`
per call, so any hang is killed and the next search starts clean.

Reddit's JSON API needs OAuth and eBay hard-blocks the VPS; the Reddit web app renders
fine via stealth Chrome (patchright) + the residential proxy. We extract title + permalink
+ preview image, parse the grade from the title, and download the image DIRECT from
preview.redd.it (no proxy — but the URL is SIGNED, so use it byte-for-byte; editing it
403s). Raw image + metadata go to /opt/viridian/data/raw_collect/.

Usage:  collect_reddit_browser.py --sub PSAcard --query "PSA 10" --sort top --t year
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from patchright.sync_api import sync_playwright

PROJ = Path("/opt/viridian")
OUT = PROJ / "data" / "raw_collect"
IMGDIR = OUT / "reddit"
META = OUT / "metadata.jsonl"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
GRADE_RE = re.compile(r"\b(PSA|BGS|CGC|SGC)\s*[-:]?\s*(10|[1-9](?:\.5)?)\b", re.I)
EXTRACT = """()=>{
 const out=[],seen=new Set();
 document.querySelectorAll("a[href*='/comments/']").forEach(a=>{
   const title=(a.innerText||'').trim(), href=a.getAttribute('href');
   if(!title||!href||seen.has(href)) return; seen.add(href);
   let n=a,img='';
   for(let i=0;i<6&&n;i++){ n=n.parentElement; if(!n) break;
     const im=n.querySelector("img[src*='redd.it'],img[src*='preview']");
     if(im){ img=im.getAttribute('src')||im.getAttribute('data-src')||''; break; } }
   out.push({title,href,img});
 });
 return out;
}"""


def load_env():
    for cand in (Path("/opt/viridian-stock/.env"), PROJ / ".env"):
        if cand.exists():
            for line in cand.read_text().splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def proxy_cfg():
    raw = os.environ.get("PROXY_URL", "").strip()
    if not raw:
        return None
    u = urlparse(raw)
    return {"server": f"http://{u.hostname}:{u.port}", "username": u.username, "password": u.password}


def load_seen():
    md5s, posts = set(), set()
    if META.exists():
        for line in META.read_text().splitlines():
            try:
                j = json.loads(line); md5s.add(j.get("md5")); posts.add(j.get("permalink"))
            except Exception:
                pass
    return md5s, posts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub", required=True)
    ap.add_argument("--query", required=True)
    ap.add_argument("--sort", default="top")
    ap.add_argument("--t", default="year")
    ap.add_argument("--scrolls", type=int, default=6)
    args = ap.parse_args()
    load_env()
    IMGDIR.mkdir(parents=True, exist_ok=True)
    md5s, posts = load_seen()
    dl = httpx.Client(headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
    added = 0

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=tempfile.mkdtemp(), channel="chrome", headless=False,
            no_viewport=True, proxy=proxy_cfg(), args=["--no-sandbox"])
        ctx.set_default_timeout(40000)
        page = ctx.new_page()
        items = []
        try:
            url = (f"https://www.reddit.com/r/{args.sub}/search/"
                   f"?q={args.query.replace(' ', '%20')}&sort={args.sort}&t={args.t}")
            page.goto(url, wait_until="domcontentloaded", timeout=40000)
            for _ in range(args.scrolls):
                page.mouse.wheel(0, 3200); page.wait_for_timeout(1800)
                items = page.evaluate(EXTRACT)
                if len(items) > 25:
                    break
        except Exception as e:
            print(f"nav err {args.sub}/{args.query}: {e}", flush=True)
        try:
            page.close(); ctx.close()
        except Exception:
            pass

    mf = open(META, "a")
    for it in items:
        href = it.get("href")
        if not href or href in posts:
            continue
        posts.add(href)
        m = GRADE_RE.search(it.get("title", ""))
        img = (it.get("img") or "").replace("&amp;", "&")     # signed: do NOT edit
        if not m or not img or "redd.it" not in img:
            continue
        grade = int(round(float(m.group(2))))
        try:
            r = dl.get(img)
            if r.status_code != 200 or len(r.content) < 4000:
                continue
            md5 = hashlib.md5(r.content).hexdigest()
            if md5 in md5s:
                continue
            md5s.add(md5)
            (IMGDIR / f"{md5}.jpg").write_bytes(r.content)
            mf.write(json.dumps({
                "md5": md5, "file": f"reddit/{md5}.jpg", "source": "reddit", "sub": args.sub,
                "company": m.group(1).upper(), "grade": grade,
                "title": it["title"][:300], "permalink": href, "img": img}) + "\n")
            mf.flush(); added += 1
        except Exception:
            continue
    mf.close()
    print(f"OK {args.sub}/{args.query}/{args.sort}: +{added} (scanned {len(items)})", flush=True)


if __name__ == "__main__":
    main()
