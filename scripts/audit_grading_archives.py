"""Audit downloaded grading archives without extracting or training on them."""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "data/datasets/quarantine/jyesr-pokemon-tcg-grading"


def audit(dataset: Path) -> dict:
    with (dataset / "metadata.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    by_name = {r["filename"]: r for r in rows}
    records = collections.Counter()
    grades = collections.Counter()
    seen_names = collections.Counter()
    hashes = collections.defaultdict(list)
    bad = []
    archives = sorted(dataset.glob("data_shard_*.tar"))
    for archive in archives:
        print(f"Auditing {archive.name}", flush=True)
        with tarfile.open(archive, "r|") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                name = PurePosixPath(member.name).name
                if PurePosixPath(name).suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    records["non_image_entries"] += 1
                    continue
                records["images"] += 1
                seen_names[name] += 1
                metadata = by_name.get(name)
                if metadata:
                    records["metadata_matched"] += 1
                    grades[metadata.get("grade", "")] += 1
                else:
                    records["metadata_unmatched"] += 1
                    if len(bad) < 100:
                        bad.append(name)
                h = hashlib.sha256()
                stream = tf.extractfile(member)
                if stream is None:
                    records["unreadable"] += 1
                    continue
                while block := stream.read(256 * 1024):
                    h.update(block)
                hashes[h.hexdigest()].append((name, metadata.get("grade", "") if metadata else ""))
    duplicates = [entries for entries in hashes.values() if len(entries) > 1]
    conflicting = [entries for entries in duplicates if len({grade for _, grade in entries if grade}) > 1]
    return {"source": "jyesr/pokemon-tcg-grading", "license": "not stated; quarantined",
            "completed_archive_files": len(archives), "expected_archives": 8,
            "metadata_rows": len(rows), "unique_metadata_filenames": len(by_name),
            "images": dict(records), "grade_counts_for_matched_images": dict(grades),
            "duplicate_content_groups": len(duplicates),
            "duplicate_content_extra_files": sum(len(group) - 1 for group in duplicates),
            "duplicate_names": sum(count - 1 for count in seen_names.values() if count > 1),
            "duplicate_content_conflicting_grades": len(conflicting),
            "conflicting_grade_examples": conflicting[:20],
            "unmatched_filename_examples": bad,
            "metadata_names_not_in_downloaded_archives": len(set(by_name) - set(seen_names)),
            "note": "Missing counts are incomplete while fewer than eight verified archives are present. Grade provenance and image rights remain unverified."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=ROOT / "data/collection_status/grading_audit.json")
    args = parser.parse_args()
    result = audit(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".tmp")
    temp.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temp.replace(args.output)
    print(f"Audit written to {args.output}", flush=True)


if __name__ == "__main__":
    main()
