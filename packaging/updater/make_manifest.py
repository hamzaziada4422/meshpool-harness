#!/usr/bin/env python3
"""Write update-manifest.json for a release (see docs/plan/AUTO_UPDATE.md).

  make_manifest.py --version 0.2.0 --runtime-file packaging/requirements-pinned.txt [--runtime-file ...]
                   [--previous PREV/update-manifest.json] -o dist/update-manifest.json

runtime_fingerprint is a hash of the files that define what is bundled besides app code (pinned packages, the list
of bundled tools and their versions, the Python version). runtime_changed is false only when a previous manifest is
given and has the same fingerprint; with no previous manifest it is true, so the full installer is used.
The manifest must then be listed in SHA256SUMS.txt, or the app ignores it.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def fingerprint(files) -> str:
    h = hashlib.sha256()
    for f in sorted(files):
        h.update(Path(f).name.encode() + b"\0" + Path(f).read_bytes() + b"\0")
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--runtime-file", action="append", default=[], required=True)
    ap.add_argument("--previous")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    fp = fingerprint(a.runtime_file)
    prev = None
    if a.previous and Path(a.previous).is_file():
        try:
            prev = json.loads(Path(a.previous).read_text()).get("runtime_fingerprint")
        except ValueError:
            prev = None
    changed = prev != fp
    Path(a.out).write_text(json.dumps({"version": a.version, "runtime_changed": changed, "runtime_fingerprint": fp,
                                       "min_manifest": 1}, indent=1) + "\n")
    print(f"update-manifest.json: runtime_changed={changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
