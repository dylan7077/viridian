"""Evaluate card-outline detection against a YOLO polygon dataset.

Scores the largest labeled card in each image; this single-card detector cannot
be evaluated as a multi-card detector. This is an exploratory in-domain check,
not an independent or random sample of user photos.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import engine, grading


def main():
    cv2.setNumThreads(1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/datasets/antokun-pokemon-card-segmentation/unpacked"))
    parser.add_argument("--output", type=Path, default=Path("data/detection_evaluation.json"))
    args = parser.parse_args()
    samples = []
    failures = []
    image_paths = sorted((args.dataset / "images" / "train").glob("*.jpg"))
    for number, image_path in enumerate(image_paths, 1):
        if number % 10 == 0:
            print(f"Evaluating {number}/{len(image_paths)}", file=sys.stderr, flush=True)
        label_path = args.dataset / "labels" / "train" / f"{image_path.stem}.txt"
        try:
            image = engine.decode_image(image_path.read_bytes())
            if image is None:
                raise ValueError("unreadable image")
            polygons = []
            for line in label_path.read_text().splitlines():
                values = [float(x) for x in line.split()]
                if len(values) >= 7 and len(values) % 2 == 1 and values[0] == 0:
                    points = np.array(values[1:], np.float32).reshape(-1, 2)
                    if np.isfinite(points).all() and (points >= 0).all() and (points <= 1).all():
                        polygons.append(points)
            if not polygons:
                raise ValueError("no valid card polygon")
            polygon = max(polygons, key=cv2.contourArea)
            _, info = grading.detect_card(image, return_debug=True)
            h, w = image.shape[:2]
            size = (512, 512)
            truth = np.zeros(size, np.uint8)
            prediction = np.zeros(size, np.uint8)
            gt_points = np.rint(polygon * 511).astype(np.int32)
            pred_points = np.rint(np.asarray(info["quad"]) * [511 / w, 511 / h]).astype(np.int32)
            cv2.fillPoly(truth, [gt_points], 1)
            cv2.fillPoly(prediction, [pred_points], 1)
            intersection = int(np.logical_and(truth, prediction).sum())
            union = int(np.logical_or(truth, prediction).sum())
            samples.append({"image": image_path.name, "iou": intersection / union if union else 0,
                            "method": info["method"], "quality": info["quality"],
                            "annotated_cards": len(polygons)})
        except (OSError, ValueError, TypeError, KeyError, cv2.error) as exc:
            failures.append({"image": image_path.name, "error": str(exc)})
    scores = [s["iou"] for s in samples]
    report = {"dataset": str(args.dataset), "evaluated": len(samples), "failures": failures,
              "mean_iou": float(np.mean(scores)) if scores else None,
              "median_iou": float(np.median(scores)) if scores else None,
              "iou_at_least_0_5": sum(x >= .5 for x in scores),
              "iou_at_least_0_75": sum(x >= .75 for x in scores), "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, indent=2))


if __name__ == "__main__":
    main()
