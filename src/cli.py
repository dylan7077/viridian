"""Offline tester: grade a local image without Discord or the web app.

    python -m src.cli path/to/card.jpg
"""
import sys
import json

import cv2

from src import engine


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m src.cli <image_path>")
    img = cv2.imread(sys.argv[1])
    if img is None:
        raise SystemExit(f"Could not read image: {sys.argv[1]}")
    result = engine.process_image(img)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
