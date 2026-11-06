"""Resume the public jyesr dataset as quarantined research material.

This source has no stated license and its label provenance is ambiguous. Downloading
it does not establish rights to train, distribute, or deploy a model on its images.
Files stay as archives until provenance and permitted uses have been reviewed.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path

import requests

REV = "02e6b335b59b67e2d2df678ce8fcff4b1a0b1d8a"
REPO = "jyesr/pokemon-tcg-grading"
ROOT = Path(__file__).resolve().parent.parent / "data" / "datasets" / "quarantine" / "jyesr-pokemon-tcg-grading"
CHUNK = 8 * 1024 * 1024


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    response = session.get(f"https://huggingface.co/api/datasets/{REPO}/tree/{REV}",
                           params={"recursive": "true", "expand": "true"}, timeout=60)
    response.raise_for_status()
    files = [x for x in response.json() if x.get("path", "").endswith(".tar")]
    for entry in sorted(files, key=lambda x: x["path"]):
        name, total = entry["path"], entry["size"]
        expected = entry["lfs"]["oid"]
        final = ROOT / name
        partial = ROOT / (name + ".partial")
        if final.exists():
            if final.stat().st_size != total or digest(final) != expected:
                raise RuntimeError(f"Existing {name} has an unexpected checksum; inspect it manually")
            print(f"Verified {name}", flush=True)
            continue
        size = partial.stat().st_size if partial.exists() else 0
        if size > total:
            raise RuntimeError(f"Partial file exceeds expected size: {partial}")
        url = f"https://huggingface.co/datasets/{REPO}/resolve/{REV}/{name}"
        with partial.open("ab") as out:
            while size < total:
                end = min(total - 1, size + CHUNK - 1)
                for attempt in range(5):
                    try:
                        with session.get(url, params={"download": "true", "part": size},
                                         headers={"Range": f"bytes={size}-{end}"},
                                         timeout=(20, 90), stream=True) as r:
                            r.raise_for_status()
                            want = f"bytes {size}-{end}/{total}"
                            if r.status_code != 206 or r.headers.get("Content-Range") != want:
                                raise ValueError(f"Unexpected range response for {name}")
                            # Spool to disk before appending so interrupted ranges can be
                            # retried without holding multiple megabytes in RAM.
                            with tempfile.TemporaryFile(dir=ROOT) as chunk:
                                received = 0
                                for block in r.iter_content(chunk_size=64 * 1024):
                                    chunk.write(block)
                                    received += len(block)
                                if received != end - size + 1:
                                    raise ValueError(f"Incomplete range for {name}")
                                chunk.seek(0)
                                while block := chunk.read(64 * 1024):
                                    out.write(block)
                                out.flush()
                        size = end + 1
                        if size % (128 * 1024 * 1024) == 0 or size == total:
                            print(f"{name}: {size / total:.1%}", flush=True)
                        break
                    except (requests.RequestException, ValueError) as exc:
                        if attempt == 4:
                            raise RuntimeError(f"Failed {name} at {size}: {exc}") from exc
                        time.sleep(2 * (attempt + 1))
        actual = digest(partial)
        if actual != expected:
            raise RuntimeError(f"Checksum mismatch for {name}; retained {partial}")
        os.replace(partial, final)
        print(f"Verified {name}: {actual}", flush=True)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    main()
