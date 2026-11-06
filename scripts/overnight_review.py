"""After collection stops, audit downloaded archives and run offline checks."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.continue_collection import ROOT, STATUS, state, wait_for_process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-pid", type=int, default=0)
    args = parser.parse_args()
    STATUS.mkdir(parents=True, exist_ok=True)
    state("overnight", "waiting_for_collection", pid=args.wait_pid)
    wait_for_process(args.wait_pid)
    state("overnight", "reviewing")
    env = os.environ.copy()
    env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", DISCORD_WEBHOOK_URL="")
    results = []
    jobs = [
        ("grading_archive_audit", [sys.executable, "-u", "scripts/audit_grading_archives.py"]),
        ("regression_tests", [sys.executable, "-m", "pytest", "-q", "scripts/test_download_grading_research.py",
                              "scripts/test_grading_archive_audit.py",
                              "scripts/test_centering_dataset.py", "scripts/test_index_builder.py",
                              "scripts/test_reliability.py", "scripts/test_training_split.py"]),
    ]
    for name in ("test_centering", "test_defects", "test_quality", "test_robustness", "test_pricing"):
        jobs.append((name, [sys.executable, f"scripts/{name}.py"]))
    with (STATUS / "overnight_review.log").open("a", encoding="utf-8", buffering=1) as log:
        for name, command in jobs:
            print(f"\n--- {name} ---", file=log, flush=True)
            try:
                rc = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=3600).returncode
                results.append({"check": name, "exit_code": rc})
            except subprocess.TimeoutExpired:
                results.append({"check": name, "exit_code": None, "error": "timed out"})
    collection = {}
    for name in ("catalog", "research"):
        try:
            collection[name] = json.loads((STATUS / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            collection[name] = {"status": "unknown"}
    report = {"completed_utc": datetime.now(timezone.utc).isoformat(),
              "collection": collection, "checks": results,
              "limitations": ["Dataset labels and image rights remain unverified; no grade model was promoted.",
                              "A passing automated check is not a real-photo grading accuracy study."]}
    path = STATUS / "overnight_report.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(temp, path)
    state("overnight", "complete" if all(r["exit_code"] == 0 for r in results) else "completed_with_failures",
          report=str(path))


if __name__ == "__main__":
    main()
