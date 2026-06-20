"""Lightweight load test for the live Viridian site.

Fires concurrent requests at the read endpoints (what a browsing user hits) and
reports success rate, throughput, and latency percentiles. Grading is excluded by
default — it's a slow CV job, separately rate-limited by the single machine.

    python -m scripts.stress [BASE_URL] [CONCURRENCY] [DURATION_SECONDS]
"""
import asyncio
import sys
import time

import aiohttp

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://viridian.fly.dev"
CONCURRENCY = int(sys.argv[2]) if len(sys.argv) > 2 else 30
DURATION = int(sys.argv[3]) if len(sys.argv) > 3 else 20

# Weighted mix of realistic read traffic.
PATHS = [
    "/api/health",
    "/api/cards?per_page=100",
    "/api/cards?q=char&per_page=100",
    "/api/cards?set=Base%20Set&per_page=100",
    "/api/activity?limit=24",
    "/api/price/base1-4",
    "/library",
    "/activity",
]


async def worker(session, deadline, lats, errors, counts):
    i = 0
    while time.monotonic() < deadline:
        path = PATHS[i % len(PATHS)]
        i += 1
        t0 = time.monotonic()
        try:
            async with session.get(BASE + path) as r:
                await r.read()
                dt = (time.monotonic() - t0) * 1000
                lats.append(dt)
                counts[r.status] = counts.get(r.status, 0) + 1
                if r.status >= 400:
                    errors.append(f"{r.status} {path}")
        except Exception as e:
            errors.append(f"EXC {path}: {type(e).__name__}")


async def main():
    lats, errors, counts = [], [], {}
    deadline = time.monotonic() + DURATION
    timeout = aiohttp.ClientTimeout(total=30)
    conn = aiohttp.TCPConnector(limit=CONCURRENCY)
    print(f"Load test → {BASE}  concurrency={CONCURRENCY}  duration={DURATION}s\n")
    async with aiohttp.ClientSession(timeout=timeout, connector=conn) as s:
        await asyncio.gather(*[worker(s, deadline, lats, errors, counts)
                               for _ in range(CONCURRENCY)])

    lats.sort()
    n = len(lats)
    def pct(p): return lats[min(n - 1, int(n * p / 100))] if n else 0
    total = n + len([e for e in errors if e.startswith("EXC")])
    print(f"requests:     {total}")
    print(f"throughput:   {total / DURATION:.1f} req/s")
    print(f"status codes: {counts}")
    print(f"errors:       {len(errors)}")
    if n:
        print(f"latency ms:   p50={pct(50):.0f}  p90={pct(90):.0f}  "
              f"p99={pct(99):.0f}  max={lats[-1]:.0f}")
    for e in errors[:8]:
        print("  !", e)


asyncio.run(main())
