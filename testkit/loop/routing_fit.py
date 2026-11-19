"""Turn loop results into Auto's routing table: which harness to use, per task category and per model.

    python testkit/loop/routing_fit.py RUN_DIR [RUN_DIR ...] [--write app/defaults/routing.json] [--min-n 10]

Reads results.jsonl from each run (repeats and several runs add up; more data, tighter intervals). Suites map to
routing categories; arms map to harness ids. For each (model, category, harness) it records the solve rate, its
Wilson 95% lower bound, cost and time per solved task and n. With --write it puts them under "measured" in
routing.json; harness_router uses a measured entry instead of the hand-set score when it has at least --min-n tasks.

The score Auto ranks by is the lower bound of the solve rate (x100): a harness must be good *and* measured enough to
be trusted. A lucky 3/3 does not beat a steady 58/60.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bench import wilson  # noqa: E402

SUITE_CATEGORY = {"chat": "general", "debug": "code", "build": "code", "hard": "code", "swe": "code",
                  "research": "research", "data": "data", "docs": "documents", "browser": "browser",
                  "terminal": "terminal"}
ARM_HARNESS = {"routed": None, "pi": "pi", "pi-routed": "pi", "codex": "codex", "opencode": "opencode",
               "hermes": "hermes"}      # routed -> meshpool-code or meshpool-work by category; base is history


def harness_for(arm: str, category: str) -> str | None:
    a = arm.split("@")[0]
    if a == "routed":
        return "meshpool-work" if category in ("general", "research", "documents", "data", "gtm") else "meshpool-code"
    return ARM_HARNESS.get(a)


def load(dirs: list[str]) -> list[dict]:
    rows = []
    for d in dirs:
        for p in Path(d).rglob("results.jsonl"):
            for line in p.read_text().splitlines():
                r = json.loads(line)
                if "suite" not in r and "instance_id" in r:        # swe_ab rows
                    r = {"suite": "swe", "arm": r.get("arm", ""), "model": r.get("model", ""), "task": r["instance_id"],
                         "pass": bool(r.get("resolved")), "secs": r.get("secs", 0), "usd": r.get("usd", 0)}
                rows.append(r)
    return rows


def fit(rows: list[dict]) -> dict:
    agg = defaultdict(lambda: {"n": 0, "k": 0, "usd": 0.0, "secs": 0.0})
    for r in rows:
        cat = SUITE_CATEGORY.get(r.get("suite", ""))
        hid = harness_for(r.get("arm", ""), cat or "")
        model = r.get("model") or ""
        if not cat or not hid or not model:
            continue
        if r.get("arm", "").split("@")[0] == "pi" and r.get("suite") == "chat" and not r.get("answer_words"):
            continue                       # Pi chat rows from before its reply capture was fixed
        a = agg[(model, cat, hid)]
        a["n"] += 1
        a["k"] += bool(r.get("pass"))
        if r.get("pass"):
            a["usd"] += r.get("usd", 0) or 0
            a["secs"] += r.get("secs", 0) or 0
    out: dict = {}
    for (model, cat, hid), a in sorted(agg.items()):
        lo, hi = wilson(a["k"], a["n"])
        out.setdefault(model, {}).setdefault(cat, {})[hid] = {
            "n": a["n"], "solved": a["k"], "score": round(100 * lo, 1), "rate": round(a["k"] / a["n"], 3),
            **({"cost_usd": round(a["usd"] / a["k"], 6), "seconds": round(a["secs"] / a["k"], 1)} if a["k"] else {})}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--write", default="")
    ap.add_argument("--min-n", type=int, default=10)
    a = ap.parse_args()
    m = fit(load(a.dirs))
    print(json.dumps(m, indent=1))
    if a.write:
        p = Path(a.write)
        t = json.loads(p.read_text())
        t["measured"] = m
        t["measured_min_n"] = a.min_n
        t["measured_source"] = "testkit/loop (same tasks, model and hour per pair; score = Wilson 95% lower bound x100)"
        p.write_text(json.dumps(t, indent=2) + "\n")
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
