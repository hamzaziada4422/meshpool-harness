"""Independent check of a paired Meshpool-vs-Codex benchmark from its raw rows (results.jsonl).

Recomputes, without trusting any summary: solve rate per arm, the paired solve-rate difference, cost per solved task
and its ratio, per-task time ratio, each with a paired bootstrap over tasks (reps of a task stay together), and the
data checks a fair comparison needs: the same tasks and rep counts in both arms, no duplicate or missing rows, errors
per arm.

Usage: python testkit/verify_paired.py RESULTS.jsonl [MORE.jsonl ...] --a ARM --b codex [--boot 4000]
Several files are pooled (for example two rounds on the same commit).
Bar (owner): cost per solved <= 0.80x with CI upper < 1.0; solve-rate difference >= 0 with CI lower > -5 points;
time not worse (median per-task ratio <= 1.0).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict


def load(paths):
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if line:
                    r = json.loads(line)
                    r["_src"] = f"{p}:{i + 1}"
                    rows.append(r)
    return rows


def passed(r):
    """Code suites record "pass"; office suites (bench3) record "ok"."""
    return bool(r.get("pass") if "pass" in r else r.get("ok"))


def cost(r):
    for k in ("usd", "cost", "cost_usd"):
        if r.get(k) is not None:
            return float(r[k])
    return 0.0


def paired_point(by, tasks, a, b):
    """Solve-rate difference (points), cost per solved task as a multiple of b's, and the median of per-task time
    ratios (each task's mean seconds over its reps, a over b), over `tasks`. by: (arm, task) -> runs."""
    sa = na = sb = nb = 0
    ca = cb = 0.0
    tr = []
    for t in tasks:
        ra, rb = by[(a, t)], by[(b, t)]
        sa += sum(1 for r in ra if passed(r))
        sb += sum(1 for r in rb if passed(r))
        na += len(ra)
        nb += len(rb)
        ca += sum(cost(r) for r in ra)
        cb += sum(cost(r) for r in rb)
        ta = statistics.mean(r.get("secs") or 0 for r in ra)
        tb = statistics.mean(r.get("secs") or 0 for r in rb)
        if tb > 0:
            tr.append(ta / tb)
    rate = 100 * (sa / max(1, na) - sb / max(1, nb))
    cps = (ca / max(1, sa)) / (cb / max(1, sb)) if sb and sa else float("inf")
    return rate, cps, statistics.median(tr) if tr else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", default="codex")
    ap.add_argument("--boot", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rows = load(a.files)
    by = defaultdict(list)                      # (arm, task) -> runs
    seen = set()
    dups = []
    for r in rows:
        key = (r.get("arm"), r.get("task"), r.get("rep"), r["_src"].split(":")[0])
        if key in seen:
            dups.append(key)
        seen.add(key)
        by[(r.get("arm"), r.get("task"))].append(r)
    tasks_a = {t for (arm, t) in by if arm == a.a}
    tasks_b = {t for (arm, t) in by if arm == a.b}
    common = sorted(tasks_a & tasks_b)
    print(f"rows {len(rows)}  arms {sorted({r.get('arm') for r in rows})}")
    print(f"tasks: {a.a} {len(tasks_a)}, {a.b} {len(tasks_b)}, paired {len(common)}")
    if tasks_a ^ tasks_b:
        print("  UNPAIRED tasks (excluded):", sorted(tasks_a ^ tasks_b))
    if dups:
        print("  DUPLICATE rows:", dups[:10])
    uneven = [t for t in common if len(by[(a.a, t)]) != len(by[(a.b, t)])]
    if uneven:
        print("  UNEVEN rep counts:", [(t, len(by[(a.a, t)]), len(by[(a.b, t)])) for t in uneven])
    for arm in (a.a, a.b):
        rs = [r for t in common for r in by[(arm, t)]]
        errs = [r for r in rs if r.get("error")]
        solved = sum(1 for r in rs if passed(r))
        usd = sum(cost(r) for r in rs)
        print(f"{arm:>10}: solved {solved}/{len(rs)} ({100 * solved / max(1, len(rs)):.1f}%)  $ {usd:.4f}  "
              f"$/solved {usd / max(1, solved):.5f}  median s {statistics.median(r.get('secs') or 0 for r in rs):.1f}  "
              f"errors {len(errs)}")

    def stats(ts):
        return paired_point(by, ts, a.a, a.b)

    rnd = random.Random(a.seed)
    point = stats(common)
    boots = [stats([rnd.choice(common) for _ in common]) for _ in range(a.boot)]

    def ci(i):
        v = sorted(b[i] for b in boots if b[i] == b[i] and b[i] != float("inf"))
        return v[int(0.025 * len(v))], v[int(0.975 * len(v)) - 1]
    (r_lo, r_hi), (c_lo, c_hi), (t_lo, t_hi) = ci(0), ci(1), ci(2)
    print(f"solve-rate diff {point[0]:+.1f} pts (95% CI {r_lo:+.1f} to {r_hi:+.1f})")
    print(f"cost per solved ratio {point[1]:.2f}x (95% CI {c_lo:.2f}-{c_hi:.2f})")
    print(f"median per-task time ratio {point[2]:.2f}x (95% CI {t_lo:.2f}-{t_hi:.2f})")
    ok_cost = point[1] <= 0.80 and c_hi < 1.0
    ok_q = point[0] >= 0 and r_lo > -5
    ok_t = point[2] <= 1.0
    print(f"BAR: cost {'PASS' if ok_cost else 'FAIL'}, quality {'PASS' if ok_q else 'FAIL'}, "
          f"time {'PASS' if ok_t else 'FAIL'} -> {'ALL PASS' if ok_cost and ok_q and ok_t else 'NOT MET'}")


if __name__ == "__main__":
    main()
