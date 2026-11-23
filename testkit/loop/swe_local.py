"""SWE-bench Verified without Docker: the pure-Python SymPy tasks, run in a local checkout, graded the official way.

    python testkit/loop/swe_local.py --n 12 --seed 7 --min-version 1.9      # writes suites/swe_sympy.json

Each task is a real SymPy issue. The harness works in a git worktree of SymPy at the issue's base commit; then the
official test patch is applied (the agent never sees it) and every FAIL_TO_PASS and PASS_TO_PASS test must pass, as
in SWE-bench's own grading. Commands run with a dedicated Python (~/.cache/meshpool-swe/venv: mpmath, pytest) first on
PATH, so `python` works for every harness the same way. The SymPy clone is shared; worktrees are removed after.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
import os
CACHE = Path(os.environ.get("MESHPOOL_SWE_DIR") or (Path.home() / ".cache" / "meshpool-swe"))
REPO = CACHE / "sympy"
VENV_BIN = CACHE / "venv" / "bin"


def _list(v) -> list:
    return v if isinstance(v, list) else json.loads(v)


def vkey(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def prepare(n: int, seed: int, min_version: str) -> list[dict]:
    from datasets import load_dataset
    rows = [r for r in load_dataset("SWE-bench/SWE-bench_Verified", split="test")
            if r["repo"] == "sympy/sympy" and vkey(r["version"]) >= vkey(min_version)]
    random.Random(seed).shuffle(rows)
    out = []
    for r in rows[:n]:
        files = sorted(set(re.findall(r"^\+\+\+ b/(\S+)", r["test_patch"], re.M)))
        out.append({
            "id": r["instance_id"], "mode": "code",
            "prompt": ("Resolve this GitHub issue in the repository in the current folder (SymPy). Make the change in "
                       "the library code; do not add or edit tests.\n\n<issue>\n" + r["problem_statement"].strip()
                       + "\n</issue>"),
            "repo": {"path": "${MESHPOOL_SWE_DIR}/sympy", "commit": r["base_commit"]},
            "path_prepend": "${MESHPOOL_SWE_DIR}/venv/bin",    # expanded by bench.load_suites
            "max_steps": 60,
            "grade": {"swe": {"test_patch": r["test_patch"], "test_files": files,
                              "f2p": _list(r["FAIL_TO_PASS"]), "p2p": _list(r["PASS_TO_PASS"])},
                      "timeout": 900},
        })
    return out


def worktree(task: dict, dest: str) -> None:
    subprocess.run(["git", "-C", task["repo"]["path"], "worktree", "add", "--detach", "-f", dest,
                    task["repo"]["commit"]], capture_output=True, check=True)


def drop_worktree(task: dict, dest: str) -> None:
    subprocess.run(["git", "-C", task["repo"]["path"], "worktree", "remove", "--force", dest], capture_output=True)


def grade(task: dict, workdir: str) -> tuple[bool, str]:
    g = task["grade"]["swe"]
    p = Path(workdir) / ".swe_test.patch"
    p.write_text(g["test_patch"])
    # the agent must not have touched the test files: restore them, then apply the official patch
    subprocess.run(["git", "checkout", "HEAD", "--", *g["test_files"]], cwd=workdir, capture_output=True)
    a = subprocess.run(["git", "apply", "--whitespace=nowarn", str(p)], cwd=workdir, capture_output=True, text=True)
    if a.returncode:
        return False, "test patch did not apply: " + a.stderr[-200:]
    py = str(Path(task.get("path_prepend", "")) / "python") if task.get("path_prepend") else "python3"
    r = subprocess.run([py, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rA", "--no-header", "-o",
                        "addopts=", *g["test_files"]], cwd=workdir, capture_output=True, text=True,
                       timeout=task["grade"].get("timeout", 900))
    status = {}
    for line in r.stdout.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR)\s+(\S+)", line)
        if m:
            status[m.group(2).split("::")[-1].split("[")[0]] = m.group(1)
    need = g["f2p"] + g["p2p"]
    bad = [t for t in need if status.get(t.split("::")[-1].split("[")[0].split(" ")[0]) != "PASSED"]
    if bad:
        return False, f"{len(bad)}/{len(need)} required tests not passing (e.g. {bad[0][:80]})"
    return True, "ok"


def validate(tasks: list[dict], want: int) -> list[dict]:
    """Keep tasks the local Python can grade fairly: the code as it was fails, the official fix passes."""
    import sys
    sys.path.insert(0, str(HERE))
    import bench
    from datasets import load_dataset
    gold = {r["instance_id"]: r["patch"] for r in load_dataset("SWE-bench/SWE-bench_Verified", split="test")}
    keep = []
    for t in tasks:
        res = []
        for fix in (False, True):
            wd = bench.setup_workdir(t)
            if fix:
                (Path(wd) / ".gold.patch").write_text(gold[t["id"]])
                subprocess.run(["git", "apply", ".gold.patch"], cwd=wd, capture_output=True)
            try:
                res.append(grade(t, wd)[0])
            except subprocess.TimeoutExpired:
                res.append(None)
            drop_worktree(t, wd)
        ok = res == [False, True]
        print(f"{t['id']}: {'valid' if ok else 'skipped (cannot be graded fairly here)'}", flush=True)
        if ok:
            keep.append(t)
            if len(keep) >= want:
                break
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--min-version", default="1.9")
    ap.add_argument("--pool", type=int, default=30, help="candidates to validate to find n gradable tasks")
    a = ap.parse_args()
    tasks = validate(prepare(a.pool, a.seed, a.min_version), a.n)
    (HERE / "suites" / "swe_sympy.json").write_text(json.dumps(tasks, indent=1))
    print(f"wrote {len(tasks)} tasks: {', '.join(t['id'] for t in tasks)}")


if __name__ == "__main__":
    main()
