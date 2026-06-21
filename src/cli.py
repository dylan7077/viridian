"""Offline tester: grade a local image without Discord or the web app.

    python -m src.cli path/to/card.jpg
"""
import sys
import json
from pathlib import Path

from src import engine


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m src.cli <image_path>")
    try:
        data = Path(sys.argv[1]).read_bytes()
    except OSError as e:
        raise SystemExit(f"Could not read image: {sys.argv[1]} ({e})")
    # Route through the same hardened pipeline as web/bot (OOM pixel-cap + empty/tiny guards),
    # so the CLI isn't the one path that decodes a 50MP image unbounded.
    result = engine.process_bytes(data)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
