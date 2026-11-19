"""Fit the harness scorecard (app/defaults/harness_scorecard.json) from bench results.

    python testkit/loop/harness_scorecard.py RUN_DIR [RUN_DIR ...] [--out app/defaults/harness_scorecard.json]
                                             [--md docs/results/harness_scorecard.md] [--include-eval]

Reads results.jsonl files. An arm is a harness id: routed -> "meshpool", profile:<name> -> "meshpool:<name>", codex,
codex-routed, opencode, opencode-routed, pi, pi-routed, hermes, hermes-routed (a "@model" suffix is dropped). Other
arms (base, v1.., meta) are skipped: "meta" rows are the router's own output, not evidence for it.

Each row's task is mapped to a category with the stage-1 classifier on the task prompt (looked up in
testkit/loop/suites by suite and task id), or from the row's route when the prompt is unknown. Per model id (and
pooled over models as "*") and category it writes one row per harness:

    {"harness", "solved", "tasks", "cost_per_task", "median_s"}

Fit on dev, measure on eval: rows from the held-out eval suite (suite or task id starting v2_eval / in v2_eval.json)
are refused unless --include-eval, so a scorecard never learns from the tasks it is later measured on.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = Path(os.environ["MH_BENCH_APP"]) if os.environ.get("MH_BENCH_APP") else HERE.parents[1] / "app"
EVAL_SUITES = ("v2_eval",)
HARNESS_ARMS = ("codex", "opencode", "pi", "hermes")


def harness_of(arm: str) -> str | None:
    a = (arm or "").split("@")[0]
    if a == "routed":
        return "meshpool"
    if a.startswith("profile:"):
        return "meshpool:" + a.split(":", 1)[1]
    base = a[:-7] if a.endswith("-routed") else a
    return a if base in HARNESS_ARMS else None


def load_rows(dirs: list[str]) -> list[dict]:
    rows = []
    for d in dirs:
        p = Path(d)
        for f in ([p] if p.is_file() else sorted(p.rglob("results.jsonl"))):
            for line in f.read_text().splitlines():
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def load_tasks(suites_dir: Path | None = None) -> dict:
    """(suite, task id) -> task, for every suite file."""
    out = {}
    for p in sorted((suites_dir or HERE / "suites").glob("*.json")):
        try:
            for t in json.loads(p.read_text()):
                out[(p.stem, t["id"])] = t
        except (ValueError, KeyError, TypeError):
            continue
    return out


def is_eval(row: dict, eval_ids: set) -> bool:
    return (row.get("suite") in EVAL_SUITES or str(row.get("task", "")) in eval_ids)


def category_of_row(row: dict, tasks: dict) -> str:
    sys.path.insert(0, str(APP))
    from meshpool_harness.hrouter import classify as CL
    t = tasks.get((row.get("suite"), row.get("task")))
    if t and t.get("prompt"):
        cat, _, _ = CL.stage1(t["prompt"], files=list((t.get("files") or {})),
                              kind="code" if t.get("mode", "code") == "code" else "cowork")
        return cat
    route = str(row.get("route") or "")
    return route if route in CL.CATEGORIES else ""


def fit(rows: list[dict], tasks: dict | None = None, include_eval: bool = False, pooled: bool = True) -> dict:
    tasks = tasks if tasks is not None else load_tasks()
    eval_ids = {tid for (s, tid) in tasks if s in EVAL_SUITES}
    agg = defaultdict(lambda: {"solved": 0, "tasks": 0, "usd": 0.0, "secs": []})
    refused = 0
    for r in rows:
        hid = harness_of(r.get("arm", ""))
        if not hid or r.get("skipped"):
            continue
        if is_eval(r, eval_ids) and not include_eval:
            refused += 1
            continue
        cat = category_of_row(r, tasks)
        if not cat:
            continue
        for model in ([r.get("model") or "?"] + (["*"] if pooled else [])):
            a = agg[(model, cat, hid)]
            a["tasks"] += 1
            a["solved"] += bool(r.get("pass"))
            a["usd"] += float(r.get("usd") or 0)
            a["secs"].append(float(r.get("secs") or 0))
    out: dict = {}
    for (model, cat, hid), a in sorted(agg.items()):
        out.setdefault(model, {}).setdefault(cat, []).append(
            {"harness": hid, "solved": a["solved"], "tasks": a["tasks"],
             "cost_per_task": round(a["usd"] / a["tasks"], 6), "median_s": round(statistics.median(a["secs"]), 1)})
    if refused:
        print(f"refused {refused} eval-suite rows (use --include-eval to fit on them)", file=sys.stderr)
    return out


def markdown(card: dict) -> str:
    lines = ["# Harness scorecard", "",
             "Solved / tasks, cost per task (USD) and median seconds, per model, category and harness.", ""]
    for model, cats in card.items():
        lines += [f"## {model}", "", "| category | harness | solved | rate | $/task | median s |",
                  "|---|---|---|---|---|---|"]
        for cat in sorted(cats):
            best = max(r["solved"] / r["tasks"] for r in cats[cat])
            for r in sorted(cats[cat], key=lambda r: (-r["solved"] / r["tasks"], r["cost_per_task"])):
                mark = " **" if r["solved"] / r["tasks"] == best else " "
                lines.append(f"| {cat} |{mark}{r['harness']}{mark.strip()} | {r['solved']}/{r['tasks']} | "
                             f"{r['solved'] / r['tasks']:.0%} | {r['cost_per_task']:.4f} | {r['median_s']:.0f} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", help="run folders (or results.jsonl files)")
    ap.add_argument("--out", default="", help="write the scorecard JSON here (default: print)")
    ap.add_argument("--md", default="", help="write a markdown table here")
    ap.add_argument("--include-eval", action="store_true", help="also fit on held-out eval rows (do not: measure there)")
    ap.add_argument("--no-pooled", action="store_true", help="no '*' entry pooled over models")
    a = ap.parse_args(argv)
    card = fit(load_rows(a.dirs), include_eval=a.include_eval, pooled=not a.no_pooled)
    text = json.dumps({"_about": "fitted by testkit/loop/harness_scorecard.py", "_version": 1, **card}, indent=1) + "\n"
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out}")
    else:
        print(text)
    if a.md:
        Path(a.md).write_text(markdown(card) + "\n")
        print(f"wrote {a.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
