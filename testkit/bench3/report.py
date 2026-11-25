"""Tables from <out>/results.jsonl: writes <out>/REPORT.md and <out>/summary.json.

    python testkit/bench3/report.py docs/results/v4/<name>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MP = ("mp-cli", "mp-work", "mp-code", "mp-on-pi", "mp-on-codex")
REF = ("codex", "pi")
ORDER = ["codex", "pi", "mp-work", "mp-code", "mp-on-pi", "mp-on-codex", "mp-cli"]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def rng(xs):
    xs = [x for x in xs if x is not None]
    return (min(xs), max(xs)) if xs else None


def load(out: Path) -> list:
    rows = []
    p = out / "results.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def cell(rs: list) -> dict:
    g = lambda k: [r.get(k) for r in rs]  # noqa: E731
    return {"n": len(rs), "passed": sum(1 for r in rs if r.get("ok")),
            "cost_mean": mean(g("cost")), "cost_range": rng(g("cost")),
            "secs_mean": mean(g("secs")), "secs_range": rng(g("secs")),
            "calls_mean": mean(g("calls")), "failed_calls_mean": mean(g("failed_calls")),
            "input_mean": mean(g("input")), "cached_mean": mean(g("cached")),
            "output_mean": mean(g("output")), "reasoning_mean": mean(g("reasoning")),
            "cost_sum": sum(x for x in g("cost") if x is not None),
            "secs_sum": sum(x for x in g("secs") if x is not None),
            "calls_sum": sum(x or 0 for x in g("calls")), "cost_unknown": sum(1 for x in g("cost") if x is None)}


def summarize(rows: list) -> dict:
    tasks = sorted({r["task"] for r in rows})
    arms = [a for a in ORDER if any(r["arm"] == a for r in rows)] + sorted(
        {r["arm"] for r in rows} - set(ORDER))
    per = {t: {a: cell([r for r in rows if r["task"] == t and r["arm"] == a]) for a in arms
               if any(r["task"] == t and r["arm"] == a for r in rows)} for t in tasks}
    ratios, loses = {}, []
    for t, byarm in per.items():
        for a in byarm:
            if a not in MP:
                continue
            for ref in REF:
                if ref in byarm:
                    c, rc = byarm[a]["cost_mean"], byarm[ref]["cost_mean"]
                    s, rs_ = byarm[a]["secs_mean"], byarm[ref]["secs_mean"]
                    ratios.setdefault(t, {})[f"{a}/{ref}"] = {
                        "cost": c / rc if c is not None and rc else None, "secs": s / rs_ if s and rs_ else None}
                    why = []
                    if c is not None and rc and c > rc:
                        why.append(f"cost {c / rc:.2f}x")
                    if s and rs_ and s > rs_:
                        why.append(f"time {s / rs_:.2f}x")
                    if byarm[a]["passed"] / byarm[a]["n"] < byarm[ref]["passed"] / byarm[ref]["n"]:
                        why.append(f"pass {byarm[a]['passed']}/{byarm[a]['n']} vs {byarm[ref]['passed']}/{byarm[ref]['n']}")
                    if why:
                        loses.append({"task": t, "arm": a, "vs": ref, "why": ", ".join(why)})
    tot = {a: cell([r for r in rows if r["arm"] == a]) for a in arms}
    return {"tasks": tasks, "arms": arms, "per_task": per, "ratios": ratios, "totals": tot, "loses": loses}


def f(x, fmt="{:.4f}", dash="-"):
    return dash if x is None else fmt.format(x)


def fr(r, fmt="{:.4f}"):
    return "-" if not r else f"{fmt.format(r[0])}-{fmt.format(r[1])}"


def render(s: dict, run: dict | None) -> str:
    L = ["# Benchmark report", ""]
    if run:
        v = run.get("versions", {})
        L += [f"Model `{run.get('model')}`, {run.get('date')}, codex `{v.get('codex')}`, pi `{v.get('pi')}`, "
              f"Meshpool source `{v.get('meshpool_src')}` @ `{str(v.get('meshpool_git'))[:10]}`. "
              "Every model call of every arm went through one metering proxy; cost = uncached input + cached input + "
              "output at the price table in `prices.json` (unknown model: no cost).", ""]
    L += ["## Per task and arm", "", "| task | arm | pass | cost mean (min-max) USD | calls | input | cached | output | reasoning | secs mean (min-max) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for t in s["tasks"]:
        for a, c in s["per_task"][t].items():
            L.append(f"| {t} | {a} | {c['passed']}/{c['n']} | {f(c['cost_mean'])} ({fr(c['cost_range'])}) | "
                     f"{f(c['calls_mean'], '{:.1f}')} | {f(c['input_mean'], '{:.0f}')} | {f(c['cached_mean'], '{:.0f}')} | "
                     f"{f(c['output_mean'], '{:.0f}')} | {f(c['reasoning_mean'], '{:.0f}')} | "
                     f"{f(c['secs_mean'], '{:.1f}')} ({fr(c['secs_range'], '{:.1f}')}) |")
    L += ["", "## Meshpool arms relative to Codex and Pi (mean over reps; below 1.00 is better)", "",
          "| task | arm vs reference | cost ratio | time ratio |", "|---|---|---|---|"]
    for t, d in s["ratios"].items():
        for k, v in d.items():
            L.append(f"| {t} | {k} | {f(v['cost'], '{:.2f}')} | {f(v['secs'], '{:.2f}')} |")
    L += ["", "## Totals per arm over all tasks", "",
          "| arm | pass | runs | cost sum USD | cost per run | calls | seconds sum | secs per run |", "|---|---|---|---|---|---|---|---|"]
    for a, c in s["totals"].items():
        n = c["n"] or 1
        unk = f" ({c['cost_unknown']} unpriced)" if c["cost_unknown"] else ""
        L.append(f"| {a} | {c['passed']}/{c['n']} | {c['n']} | {c['cost_sum']:.4f}{unk} | {c['cost_sum'] / n:.4f} | "
                 f"{c['calls_sum']} | {c['secs_sum']:.0f} | {c['secs_sum'] / n:.1f} |")
    L += ["", "## Where Meshpool loses", ""]
    L += [f"- {x['task']}: `{x['arm']}` vs `{x['vs']}`: {x['why']}" for x in s["loses"]] or ["- nowhere on this data."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0])
    s = summarize(load(out))
    rp = out / "run.json"
    run = json.loads(rp.read_text()) if rp.exists() else None
    (out / "REPORT.md").write_text(render(s, run))
    (out / "summary.json").write_text(json.dumps(s, indent=1))
    print(f"wrote {out / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
