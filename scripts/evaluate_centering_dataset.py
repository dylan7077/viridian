"""Compare Viridian with GemSnipe's published measurements, when exact photos exist.

Place the exact, verified photo for annotation 123 at --images/123.jpg (or .png).
Listing IDs alone are insufficient: several rows refer to the same listing and may
represent different photos. GemSnipe's labels are CV measurements, not human-verified
ground truth. Missing, unreadable and unmeasurable examples are counted separately.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import engine, grading


def evaluate(manifest: Path, images: Path) -> dict:
    rows = list(csv.DictReader(manifest.open(encoding="utf-8-sig", newline="")))
    result = {"rows": len(rows), "missing": 0, "unreadable": 0, "unmeasurable": 0,
              "compared": 0, "comparisons": []}
    for row in rows:
        annotation = row["annotation_id"]
        candidates = [images / f"{annotation}{ext}" for ext in (".jpg", ".jpeg", ".png")]
        files = [p for p in candidates if p.is_file()]
        if not files:
            result["missing"] += 1
            continue
        if len(files) > 1:
            raise ValueError(f"Multiple image files for annotation {annotation}; resolve ambiguity")
        image = engine.decode_image(files[0].read_bytes())
        if image is None:
            result["unreadable"] += 1
            continue
        try:
            card = grading.detect_card(image)
            measurement = grading.measure_centering(card)
            if not measurement.get("ok"):
                result["unmeasurable"] += 1
                continue
            lr = float(measurement["left_right"].split("/")[0])
            tb = float(measurement["top_bottom"].split("/")[0])
            ref_lr = float(row["left_right_pct_left_share"])
            ref_tb = float(row["top_bottom_pct_top_share"])
            result["comparisons"].append({"annotation_id": annotation,
                "lr_abs_error_pp": abs(lr - ref_lr), "tb_abs_error_pp": abs(tb - ref_tb),
                "method": measurement.get("method"), "confidence": measurement.get("confidence")})
            result["compared"] += 1
        except (ValueError, TypeError, KeyError, AttributeError, cv2.error):
            result["unmeasurable"] += 1
    if result["compared"]:
        for axis in ("lr", "tb"):
            key = f"{axis}_abs_error_pp"
            result[f"{axis}_mean_abs_error_pp"] = sum(c[key] for c in result["comparisons"]) / result["compared"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/datasets/gemsnipe-centering/gemsnipe-card-centering-measurements.csv"))
    parser.add_argument("--images", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.manifest, args.images), indent=2))


if __name__ == "__main__":
    main()
