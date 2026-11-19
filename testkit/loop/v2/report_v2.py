"""V2 results report: quality, cost and speed for every harness, by category, with paired statistics.

    python testkit/loop/v2/report_v2.py RUN_DIR [--title T] [--suite-file suites/v2_eval.json] > report.md
"""
import argparse
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import bench  # noqa: E402

NAMES = {"routed": "Meshpool V2", "v1": "Meshpool V1", "pi": "Pi", "codex": "Codex", "opencode": "OpenCode",
         "hermes": "Hermes"}
ORDER = ["routed", "v1", "codex", "opencode", "pi", "hermes"]
SLEEP_S = 3600


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))] if xs else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--title", default="V2 evaluation")
    ap.add_argument("--suite-file", default="")
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(Path(a.run) / "results.jsonl")]
    for r in rows:
        r.setdefault("rep", 0)
        if "pass" not in r:
            r.update({"pass": False, "why": "crash", "error": r.get("error") or "crash"})
    meta = {}
    if a.suite_file:
        meta = {t["id"]: t for t in json.load(open(a.suite_file))}
    arms = [x for x in ORDER if any(r["arm"] == x for r in rows)]
    by = {(r["task"], r["arm"], r["rep"]): r for r in rows}
    keys = sorted({(r["task"], r["rep"]) for r in rows})
    keys = [k for k in keys if all((k[0], x, k[1]) in by for x in arms)]
    excl = [r for r in rows if r.get("secs", 0) >= SLEEP_S]
    out = [f"## {a.title}", "",
           f"{len({t for t, _ in keys})} tasks run by every harness ({', '.join(NAMES[x] for x in arms)}), DeepSeek V4.1 "
           "Flash, one run each, graded by hidden tests or official answers. Tokens are from the shared metering "
           f"proxy. Runs left out of timing (over {SLEEP_S}s, e.g. across a sleep): {len(excl)}.", "",
           "| Harness | Solved | Rate (95% CI) | Errors | $ / attempted | $ / solved | Median s | Mean s | P90 s | "
           "Input tok / task | Output tok / task | Calls / task |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in arms:
        rs = [by[(t, x, r)] for t, r in keys]
        k = sum(r["pass"] for r in rs)
        lo, hi = bench.wilson(k, len(rs))
        usd = sum(r.get("usd", 0) for r in rs)
        secs = [r["secs"] for r in rs if r.get("secs", 0) < SLEEP_S]
        errs = sum(bool(r.get("error")) for r in rs)
        n = max(1, len(rs))
        out.append(f"| {NAMES[x]} | {k}/{len(rs)} | {k / n:.0%} ({lo:.0%}–{hi:.0%}) | {errs} | ${usd / n:.4f} | "
                   f"${usd / max(1, k):.4f} | {st.median(secs):.1f} | {st.mean(secs):.1f} | {p90(secs):.1f} | "
                   f"{sum(r.get('prompt_tokens', 0) for r in rs) / n:,.0f} | "
                   f"{sum(r.get('completion_tokens', 0) for r in rs) / n:,.0f} | "
                   f"{sum(r.get('model_calls', 0) for r in rs) / n:.1f} |")
    out += ["", "Meshpool V2 against each harness on the same tasks (quality: McNemar exact; cost and time: paired "
            "bootstrap 95% interval of the ratio, over tasks both solved):", "",
            "| vs | Solved only by Meshpool V2 | Solved only by them | Quality | Cost ratio | Time ratio |",
            "|---|---|---|---|---|---|"]
    for x in arms:
        if x == "routed":
            continue
        c = bench.compare(by, keys, "routed", x)
        f = lambda t: "n/a" if t[0] != t[0] else f"{t[0]:.2f}× ({t[1]:.2f}–{t[2]:.2f})"   # noqa: E731
        q = {"better": "**better** (significant)", "worse": "**worse** (significant)",
             "tie": "tie" if c["only_a"] == c["only_b"] else f"tie (not significant, p={c['p']:.2f})"}[c["quality"]]
        out.append(f"| {NAMES[x]} | {c['only_a']} | {c['only_b']} | {q} | {c['cheaper']} {f(c['cost'])} | "
                   f"{c['faster']} {f(c['secs'])} |")
    cats = defaultdict(list)
    for t, r in keys:
        cats[(meta.get(t) or {}).get("category") or by[(t, arms[0], r)].get("suite", "?")].append((t, r))
    if len(cats) > 1:
        out += ["", "Solved by category:", "", "| Category | Tasks | " + " | ".join(NAMES[x] for x in arms) + " |",
                "|---|---|" + "---|" * len(arms)]
        for cat in sorted(cats):
            ks = cats[cat]
            out.append(f"| {cat} | {len(ks)} | " + " | ".join(
                f"{sum(by[(t, x, r)]['pass'] for t, r in ks)}" for x in arms) + " |")
        out += ["", "Cost per attempted task by category ($):", "",
                "| Category | " + " | ".join(NAMES[x] for x in arms) + " |", "|---|" + "---|" * len(arms)]
        for cat in sorted(cats):
            ks = cats[cat]
            out.append(f"| {cat} | " + " | ".join(
                f"{sum(by[(t, x, r)].get('usd', 0) for t, r in ks) / len(ks):.4f}" for x in arms) + " |")
    diffs = defaultdict(list)
    for t, r in keys:
        diffs[(meta.get(t) or {}).get("difficulty", "")].append((t, r))
    if len([d for d in diffs if d]) > 1:
        out += ["", "Solved by difficulty:", "", "| Difficulty | Tasks | " + " | ".join(NAMES[x] for x in arms) + " |",
                "|---|---|" + "---|" * len(arms)]
        for d in sorted(diffs):
            ks = diffs[d]
            out.append(f"| {d} | {len(ks)} | " + " | ".join(f"{sum(by[(t, x, r)]['pass'] for t, r in ks)}" for x in arms) + " |")
    fails = defaultdict(list)
    for t, r in keys:
        for x in arms:
            if not by[(t, x, r)]["pass"]:
                fails[x].append(t.replace("v2-", ""))
    out += ["", "Unsolved tasks per harness:", ""] + [f"- {NAMES[x]}: {', '.join(fails[x]) or 'none'}" for x in arms]
    print("\n".join(out))


if __name__ == "__main__":
    main()
