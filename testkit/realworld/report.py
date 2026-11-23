"""Report for a real-world run: per-arm and per-category tables, paired Meshpool/Codex and Meshpool/Pi ratios with
bootstrap intervals, the tasks where Meshpool loses most, and the verdict line.

    python testkit/realworld/report.py <run dir> --md docs/results/realworld/REALWORLD.md [--notes notes.json] \
        [--copy-md other.md] [--title "..."]

notes.json (optional, written by hand after reading the traces): {"<task>": "why Meshpool lost there"}.
Verdict: PASS when Meshpool's $ per solved task is at least 20% below BOTH Codex's and Pi's, and Meshpool solves at
least as many tasks as each.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

ARMS = ("mp", "codex", "pi")
NAMES = {"mp": "Meshpool", "codex": "Codex CLI", "pi": "Pi"}
CATS = ("swe", "office", "everyday")


def load(run: Path) -> list[dict]:
    rows = {}
    for line in (run / "results.jsonl").read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        rows[(r["task"], r["arm"], r["rep"])] = r          # last row wins (a rerun replaces)
    # a (task, rep) counts only when every arm has a row and none was cut short by the meter's budget: an unpaired or
    # budget-truncated run would bias the comparison
    groups = defaultdict(list)
    for r in rows.values():
        groups[(r["task"], r["rep"])].append(r)
    keep = [r for g in groups.values() if {x["arm"] for x in g} >= set(ARMS) and not any(x.get("budget_blocked") for x in g)
            for r in g]
    load.dropped = len(rows) - len(keep)
    return keep


def agg(rs: list[dict]) -> dict:
    n = len(rs)
    s = sum(1 for r in rs if r["ok"])
    cost = sum(r["cost"] or 0 for r in rs)
    return {"n": n, "solved": s, "cost": cost, "per_task": cost / n if n else 0, "per_solved": cost / s if s else None,
            "calls": sum(r["calls"] for r in rs) / n if n else 0, "input": sum(r["input"] for r in rs) / n if n else 0,
            "cached": sum(r["cached"] for r in rs) / n if n else 0, "output": sum(r["output"] for r in rs) / n if n else 0,
            "secs": sum(r["secs"] or 0 for r in rs) / n if n else 0,
            "errors": sum(1 for r in rs if r.get("error")), "timeouts": sum(1 for r in rs if r.get("timed_out"))}


def paired(rows, a, b):
    by = defaultdict(dict)
    for r in rows:
        by[(r["task"], r["rep"])][r["arm"]] = r
    return [(v[a], v[b]) for k, v in sorted(by.items()) if a in v and b in v]


def boot(pairs, stat, iters=4000, seed=7):
    """Point estimate and 95% percentile interval of stat(pairs) under resampling of the paired (task, rep) units."""
    rng = random.Random(seed)
    pt = stat(pairs)
    vals = []
    for _ in range(iters):
        smp = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        v = stat(smp)
        if v is not None:
            vals.append(v)
    vals.sort()
    if not vals or pt is None:
        return pt, None, None
    return pt, vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))]


def ratio_cost(ps):
    d = sum(b["cost"] for _, b in ps)
    return sum(a["cost"] for a, _ in ps) / d if d else None


def ratio_per_solved(ps):
    sa, sb = sum(a["ok"] for a, _ in ps), sum(b["ok"] for _, b in ps)
    if not sa or not sb:
        return None
    ca, cb = sum(a["cost"] for a, _ in ps), sum(b["cost"] for _, b in ps)
    return (ca / sa) / (cb / sb) if cb else None


def ratio_secs(ps):
    d = sum(b["secs"] or 0 for _, b in ps)
    return sum(a["secs"] or 0 for a, _ in ps) / d if d else None


def ratio_calls(ps):
    d = sum(b["calls"] for _, b in ps)
    return sum(a["calls"] for a, _ in ps) / d if d else None


def fm(x, d=4):
    return "-" if x is None else f"${x:.{d}f}"


def fr(t):
    pt, lo, hi = t
    if pt is None:
        return "-"
    return f"{pt:.2f}x ({lo:.2f}-{hi:.2f})" if lo is not None else f"{pt:.2f}x"


def losses(rows, k=5):
    """Tasks (summed over reps) where Meshpool does worst against the better of Codex and Pi: first by solves lost,
    then by extra dollars."""
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[r["task"]][r["arm"]].append(r)
    out = []
    for t, d in by.items():
        if "mp" not in d:
            continue
        mp_s = sum(r["ok"] for r in d["mp"])
        mp_c = sum(r["cost"] for r in d["mp"]) / len(d["mp"])
        others = {a: (sum(r["ok"] for r in d[a]), sum(r["cost"] for r in d[a]) / len(d[a])) for a in ("codex", "pi")
                  if d.get(a)}
        if not others:
            continue
        best_s = max(s for s, _ in others.values())
        cheapest = min(c for _, c in others.values())
        out.append({"task": t, "category": d["mp"][0]["category"], "solve_gap": best_s - mp_s, "mp_cost": mp_c,
                    "best_other_cost": cheapest, "excess": mp_c - cheapest, "mp_solved": mp_s, "runs": len(d["mp"]),
                    "others": others, "detail": d["mp"][-1]["detail"][:160]})
    out.sort(key=lambda x: (-x["solve_gap"], -x["excess"]))
    return out[:k]


def render(rows, title, notes, meta) -> str:
    L = [f"# {title}", ""]
    reps = sorted({r["rep"] for r in rows})
    tasks = sorted({r["task"] for r in rows})
    L.append(f"{len(tasks)} tasks x {len(reps)} run(s) per arm; same model ({meta.get('model', '?')}) through "
             f"llm.meshpool.ai, same metering proxy (testkit/loop/small_meter.py), concurrency {meta.get('parallel', '?')}, "
             f"arms interleaved task by task in the same hour. Cost = metered tokens at "
             f"testkit/bench3/prices.json ({meta.get('price', '')}). Versions: {meta.get('versions', '')}."
             + (f" {meta['dropped']} unpaired or budget-cut runs left out." if meta.get("dropped") else ""))
    L.append("")
    A = {a: agg([r for r in rows if r["arm"] == a]) for a in ARMS}
    L += ["## Headline", "", "| Arm | Solved | $/task | $/solved | Calls/task | Input tok/task | Cached tok/task | "
          "Output tok/task | s/task | Errors | Timeouts |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in ARMS:
        x = A[a]
        if not x["n"]:
            continue
        L.append(f"| {NAMES[a]} | {x['solved']}/{x['n']} | {fm(x['per_task'])} | {fm(x['per_solved'])} | "
                 f"{x['calls']:.1f} | {x['input']:,.0f} | {x['cached']:,.0f} | {x['output']:,.0f} | {x['secs']:.0f} | "
                 f"{x['errors']} | {x['timeouts']} |")
    L += ["", "## Per category", "", "| Category | " + " | ".join(f"{NAMES[a]} solved | {NAMES[a]} $/solved"
                                                                for a in ARMS) + " |",
          "|---|" + "---|" * (2 * len(ARMS))]
    for c in CATS:
        cells = []
        for a in ARMS:
            x = agg([r for r in rows if r["arm"] == a and r["category"] == c])
            cells += [f"{x['solved']}/{x['n']}" if x["n"] else "-", fm(x["per_solved"]) if x["n"] else "-"]
        L.append(f"| {c} | " + " | ".join(cells) + " |")
    L += ["", "## Paired ratios (Meshpool / other, same task and run; 95% bootstrap over task-runs)", "",
          "| vs | pairs | Solves (MP vs them) | Cost ratio | $/solved ratio | Calls ratio | Time ratio |",
          "|---|---|---|---|---|---|---|"]
    ratios = {}
    for b in ("codex", "pi"):
        ps = paired(rows, "mp", b)
        if not ps:
            continue
        sa, sb = sum(x["ok"] for x, _ in ps), sum(y["ok"] for _, y in ps)
        only_a = sum(1 for x, y in ps if x["ok"] and not y["ok"])
        only_b = sum(1 for x, y in ps if y["ok"] and not x["ok"])
        ratios[b] = {"cost": boot(ps, ratio_cost), "ps": boot(ps, ratio_per_solved), "calls": boot(ps, ratio_calls),
                     "secs": boot(ps, ratio_secs), "sa": sa, "sb": sb}
        L.append(f"| {NAMES[b]} | {len(ps)} | {sa} vs {sb} (only MP {only_a}, only them {only_b}) | "
                 f"{fr(ratios[b]['cost'])} | {fr(ratios[b]['ps'])} | {fr(ratios[b]['calls'])} | {fr(ratios[b]['secs'])} |")
    for c in CATS:
        line = []
        for b in ("codex", "pi"):
            ps = [(x, y) for x, y in paired(rows, "mp", b) if x["category"] == c]
            if ps:
                line.append(f"vs {NAMES[b]} cost {fr(boot(ps, ratio_cost))}, $/solved {fr(boot(ps, ratio_per_solved))}")
        if line:
            L.append(f"\n- {c}: " + "; ".join(line))
    L += ["", "## Where Meshpool loses most", "",
          "Ranked by solves lost against the better of Codex/Pi, then by extra $ per run against the cheaper of them.", "",
          "| Task | Cat | MP solved | MP $/run | cheaper other $/run | Codex (solved, $/run) | Pi (solved, $/run) | Reason (from traces) |",
          "|---|---|---|---|---|---|---|---|"]
    for x in losses(rows):
        o = x["others"]
        L.append(f"| {x['task']} | {x['category']} | {x['mp_solved']}/{x['runs']} | {fm(x['mp_cost'])} | "
                 f"{fm(x['best_other_cost'])} | {o.get('codex', ('-', None))[0]}, {fm(o.get('codex', (0, None))[1])} | "
                 f"{o.get('pi', ('-', None))[0]}, {fm(o.get('pi', (0, None))[1])} | "
                 f"{notes.get(x['task'], x['detail'])} |")
    L += ["", "## Every run", "", "| Task | Cat | Rep | Arm | ok | $ | calls | s | detail |", "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (CATS.index(r["category"]), r["task"], r["rep"], ARMS.index(r["arm"]))):
        L.append(f"| {r['task']} | {r['category']} | {r['rep']} | {r['arm']} | {'Y' if r['ok'] else 'n'} | "
                 f"{r['cost']:.4f} | {r['calls']} | {r['secs'] or 0:.0f} | {(r['error'] or r['detail'])[:90]} |")
    # verdict
    L += [""]
    mp, cx, pi = A["mp"], A["codex"], A["pi"]
    complete = all(A[a]["n"] for a in ARMS)
    if complete and mp["per_solved"] is not None:
        c_ok = cx["per_solved"] is not None and mp["per_solved"] <= 0.8 * cx["per_solved"]
        p_ok = pi["per_solved"] is not None and mp["per_solved"] <= 0.8 * pi["per_solved"]
        s_ok = mp["solved"] >= cx["solved"] and mp["solved"] >= pi["solved"]
        rc = mp["per_solved"] / cx["per_solved"] if cx["per_solved"] else float("nan")
        rp = mp["per_solved"] / pi["per_solved"] if pi["per_solved"] else float("nan")
        v = "PASS" if (c_ok and p_ok and s_ok) else "FAIL"
        L.append(f"VERDICT: {v} - Meshpool $/solved {fm(mp['per_solved'])} = {rc:.2f}x Codex ({fm(cx['per_solved'])}) "
                 f"and {rp:.2f}x Pi ({fm(pi['per_solved'])}) [need <= 0.80x both]; solved {mp['solved']} vs Codex "
                 f"{cx['solved']} / Pi {pi['solved']} [need >= each]"
                 + ("" if meta.get("final") else " (PARTIAL: not every category/run is in yet)"))
    else:
        L.append("VERDICT: INCOMPLETE - not every arm has results")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--md", required=True)
    ap.add_argument("--copy-md", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--title", default="Real-world benchmark: Meshpool vs Codex CLI vs Pi")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--preamble", default="", help="markdown file inserted after the title block")
    a = ap.parse_args(argv)
    run = Path(a.run)
    rows = load(run)
    info = json.loads((run / "run.json").read_text())
    inv = info["invocations"][-1]
    meta = {"model": inv["model"], "parallel": inv["parallel"], "price": inv["price_used"],
            "versions": inv["versions"], "final": a.final, "dropped": getattr(load, "dropped", 0)}
    notes = json.loads(Path(a.notes).read_text()) if a.notes else {}
    md = render(rows, a.title, notes, meta)
    if a.preamble:
        head, rest = md.split("\n## Headline", 1)
        md = head + "\n" + Path(a.preamble).read_text() + "\n## Headline" + rest
    for p in filter(None, (a.md, a.copy_md)):
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(md)
    print(md.splitlines()[-1])


if __name__ == "__main__":
    main()
