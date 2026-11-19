"""Build the V2 suite: admit a task only if it fails as given and passes with its reference solution.

    python testkit/loop/v2/build.py            # validates every task, writes suites/v2_dev.json and suites/v2_eval.json

The reference solutions stay in this folder; the suite files given to the harnesses contain only the workspace,
the prompt and the hidden tests (written into the workspace after the harness finishes).
"""
import glob
import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import bench  # noqa: E402
import common  # noqa: E402


def check(t: dict) -> tuple[bool, str]:
    wd = bench.setup_workdir(t)
    before, why0 = bench.grade(t, "", wd)
    shutil.rmtree(wd, ignore_errors=True)
    wd = bench.setup_workdir(t)
    for rel, body in t["ref"].items():
        if rel == "__answer__":
            continue
        p = Path(wd) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    after, why1 = bench.grade(t, t["ref"].get("__answer__", ""), wd)
    shutil.rmtree(wd, ignore_errors=True)
    if before:
        return False, "passes as given"
    if not after:
        return False, "reference fails: " + why1[-300:]
    return True, why0[:80]


def main():
    for f in sorted(glob.glob(str(HERE / "t_*.py"))):
        importlib.import_module(Path(f).stem)
    good, bad = [], []
    for t in common.TASKS:
        t["suite"] = "v2"
        ok, why = check(t)
        (good if ok else bad).append(t)
        print(f"{'ok ' if ok else 'BAD'} {t['id']:32} {t['category']:14} {t['split']:4} {'' if ok else why}", flush=True)
    for split in ("dev", "eval"):
        rows = [{k: v for k, v in t.items() if k != "ref"} for t in good if t["split"] == split]
        (HERE.parent / "suites" / f"v2_{split}.json").write_text(json.dumps(rows, indent=1))
    print(f"\n{len(good)} admitted ({sum(t['split'] == 'dev' for t in good)} dev), {len(bad)} rejected")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
