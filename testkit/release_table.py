"""Per-suite Meshpool-vs-Codex table for the release notes, from the frozen round's raw rows.

Usage: python testkit/release_table.py --a ARM [--b codex] SUITE=FILE[,FILE...] [SUITE=...]
Runs testkit/verify_paired.py on each suite (paired bootstrap over tasks) and prints one Markdown row per suite:
solved, solve-rate difference with its 95% interval, cost per solved as a multiple of Codex with its interval,
median per-task time ratio, and whether the owner's bar is met. Losses are printed like wins; nothing is dropped.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(files, a, b):
    out = subprocess.run([sys.executable, str(HERE / "verify_paired.py"), *files, "--a", a, "--b", b],
                         capture_output=True, text=True, check=True).stdout
    g = lambda pat: re.search(pat, out, re.M)                                   # noqa: E731
    sa, sb = g(rf"^\s*{re.escape(a)}: solved (\d+)/(\d+)"), g(rf"^\s*{re.escape(b)}: solved (\d+)/(\d+)")
    d = g(r"solve-rate diff ([+-][\d.]+) pts \(95% CI ([+-][\d.]+) to ([+-][\d.]+)\)")
    c = g(r"cost per solved ratio ([\d.]+|inf)x \(95% CI ([\d.]+)-([\d.]+)\)")
    t = g(r"median per-task time ratio ([\d.]+|nan)x")
    bar = g(r"^BAR: (.*)$")
    return (f"{sa[1]}/{sa[2]} vs {sb[1]}/{sb[2]}", f"{d[1]} ({d[2]} to {d[3]})", f"{c[1]}x ({c[2]}-{c[3]})",
            f"{t[1]}x", bar[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", default="codex")
    ap.add_argument("suites", nargs="+", help="SUITE=FILE[,FILE...]")
    o = ap.parse_args()
    print("| Suite | Solved (Meshpool vs Codex) | Solve-rate diff, pts (95% CI) | Cost per solved vs Codex (95% CI) "
          "| Median time vs Codex | Bar |")
    print("|---|---|---|---|---|---|")
    for spec in o.suites:
        name, files = spec.split("=", 1)
        print("| " + " | ".join((name,) + run(files.split(","), o.a, o.b)) + " |")


if __name__ == "__main__":
    main()
