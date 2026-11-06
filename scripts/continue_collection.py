"""Continue the two long downloads and rebuild ORB after the catalog finishes.

Windows helper: waits for the specified already-running workers before resuming,
so it never writes the same index or archive at the same time as those workers.
Individual logs and status files are written under data/collection_status/.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "data" / "collection_status"


def state(name, status, **extra):
    data = {"task": name, "status": status,
            "updated_utc": datetime.now(timezone.utc).isoformat(), **extra}
    target = STATUS / f"{name}.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, target)


def wait_for_process(pid):
    if not pid:
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if handle:
        try:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        finally:
            kernel.CloseHandle(handle)


def worker(name, pid, delay=0):
    try:
        state(name, "waiting_for_existing_worker", pid=pid)
        wait_for_process(pid)
        if delay:
            state(name, "cooling_down", retry_after_seconds=delay)
            time.sleep(delay)
        env = os.environ.copy()
        env["DISCORD_WEBHOOK_URL"] = ""
        env["OPENBLAS_NUM_THREADS"] = "1"
        if name == "catalog":
            command = [sys.executable, "-u", "scripts/build_index.py", "--workers", "3"]
        else:
            command = [sys.executable, "-u", "scripts/download_grading_research.py"]
        with (STATUS / f"{name}.log").open("a", encoding="utf-8", buffering=1) as log:
            rc = 1
            for attempt in range(1, 4):
                state(name, "running", attempt=attempt)
                rc = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                    stderr=subprocess.STDOUT).returncode
                if rc == 0:
                    break
                if attempt < 3:
                    delay_seconds = 900 if name == "catalog" else 120
                    state(name, "cooling_down", attempt=attempt, retry_after_seconds=delay_seconds)
                    time.sleep(delay_seconds)
            if name == "catalog":
                state(name, "building_orb", index_exit_code=rc)
                orb_rc = subprocess.run([sys.executable, "-u", "scripts/build_orb_db.py"],
                                        cwd=ROOT, env=env, stdout=log,
                                        stderr=subprocess.STDOUT).returncode
                state(name, "complete" if rc == 0 and orb_rc == 0 else "incomplete",
                      index_exit_code=rc, orb_exit_code=orb_rc)
            else:
                state(name, "complete" if rc == 0 else "incomplete", exit_code=rc)
    except Exception as exc:
        state(name, "failed", error=str(exc))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-index-pid", type=int, default=0)
    parser.add_argument("--wait-research-pid", type=int, default=0)
    parser.add_argument("--catalog-delay", type=int, default=0)
    args = parser.parse_args()
    STATUS.mkdir(parents=True, exist_ok=True)
    jobs = [threading.Thread(target=worker, args=("catalog", args.wait_index_pid, args.catalog_delay)),
            threading.Thread(target=worker, args=("research", args.wait_research_pid))]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join()


if __name__ == "__main__":
    main()
