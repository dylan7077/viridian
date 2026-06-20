"""Background maintenance — keeps the global ORB matcher current, hands-off.

Loops forever:
  1. Pre-cache any card images not yet downloaded (covers newly scraped cards).
  2. If the cached image set grew since the last build, rebuild orb_db (atomic).

The live server hot-reloads orb_db automatically (OrbIndex.maybe_reload), so new
cards become matchable with no restart. Run once in the background:

    nohup python scripts/maintain_index.py > /tmp/maintain.log 2>&1 &

Tune the cycle with MAINTAIN_INTERVAL (seconds, default 600).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import config            # noqa: E402
import precache_images   # noqa: E402
import build_orb_db      # noqa: E402

META = config.DATA_DIR / "orb_db.meta"
INTERVAL = int(os.getenv("MAINTAIN_INTERVAL", "600"))


def cached_count() -> int:
    return len(list(config.IMAGE_CACHE.glob("*.jpg")))


def last_built() -> int:
    try:
        return int(META.read_text().strip())
    except Exception:
        return -1


def main():
    print(f"maintenance loop started (every {INTERVAL}s)")
    while True:
        try:
            precache_images.main()
        except Exception as e:
            print("precache error:", e)

        n = cached_count()
        if n != last_built():
            print(f"cached set changed ({last_built()} -> {n}); rebuilding orb_db...")
            try:
                build_orb_db.main()
                META.write_text(str(n))
                print("orb_db rebuilt; server will hot-reload on next scan.")
            except Exception as e:
                print("build error:", e)
        else:
            print(f"no change ({n} cached); orb_db already up to date.")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
