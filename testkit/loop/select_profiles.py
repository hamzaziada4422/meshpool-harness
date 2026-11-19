"""Choose the harness profile for each kind of task from measured bench results.

    python testkit/loop/select_profiles.py RUN_DIR_OR_results.jsonl [...] [--out app/defaults/profiles.json]
        [--md decisions.md] [--arm-profile ARM=PROFILE ...] [--min-tasks 3] [--min-saving 0.15]

Input: results.jsonl rows from bench.py whose arms are harness profiles ("profile:<name>" arms, arms named like the
profile, or a mapping with --arm-profile ARM=PROFILE). Each row's route keys (route_keys; else the route spec; else
the suite's category) say which groups a task belongs to: spec, spec.variant, spec.language, spec.variant.language,
the same keys app/defaults/profiles.json assigns on.

Only tasks that every profile ran are compared (a profile that skipped a task would otherwise look cheap). A task's
numbers per profile are the mean over its repeats. For a group with at least --min-tasks tasks the winner is chosen
lexicographically: (1) most tasks solved, (2) lowest total cost, (3) lowest median time. The overall best profile (all
tasks, same rule) becomes "default". Noise guard: a group gets its own assignment only if its winner solves more than
the default does on that group, or ties it and is at least --min-saving (15%) cheaper; otherwise it inherits the
default.

Select on dev, measure on eval: rows of suites named like "*eval*" (testkit/loop/suites/v2_eval.json) are dropped
unless --include-eval is passed, so the choice cannot be fitted to the held-out set by accident.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

SUITE_CATEGORY = {"chat": "chat", "debug": "code.debug", "build": "code.build", "hard": "code.build",
                  "swe": "code.debug", "research": "research", "data": "data", "docs": "docs", "browser": "browser",
                  "terminal": "code.terminal", "deep": "code.build"}


def load(paths: list[str]) -> list[dict]:
    rows = []
    for p in map(Path, paths):
        files = sorted(p.rglob("results.jsonl")) if p.is_dir() else [p]
        for f in files:
            for line in f.read_text().splitlines():
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def profile_of(row: dict, mapping: dict) -> str:
    arm = (row.get("base_arm") or row.get("arm") or "").split("@")[0]
    if arm in mapping:
        return mapping[arm]
    return arm.split(":", 1)[1] if arm.startswith("profile:") else arm


def keys_of(row: dict) -> list[str]:
    """Every group key a task belongs to (general to specific)."""
    ks = row.get("route_keys")
    if isinstance(ks, list) and ks:
        return [str(k) for k in ks]
    r = row.get("route")
    if isinstance(r, dict):                      # a whole Route as a dict
        spec, var, lang = r.get("spec", ""), r.get("variant", ""), r.get("language", "")
        ks = [spec] + ([f"{spec}.{var}"] if var else []) + ([f"{spec}.{lang}"] if lang else []) + \
             ([f"{spec}.{var}.{lang}"] if var and lang else [])
        return [k for k in ks if k]
    if isinstance(r, str) and r:
        return [r]
    cat = SUITE_CATEGORY.get(row.get("suite", ""))
    return [cat] if cat else []


def collect(rows: list[dict], mapping: dict, arms: set | None = None, include_eval: bool = False):
    """{task: {profile: {"solved": 0..1, "usd": mean, "secs": mean}}} and {task: [group keys]}."""
    acc: dict = defaultdict(lambda: defaultdict(lambda: {"p": [], "usd": [], "secs": []}))
    keys: dict = {}
    skipped = 0
    for r in rows:
        if not include_eval and "eval" in str(r.get("suite", "")).lower():
            skipped += 1
            continue
        prof = profile_of(r, mapping)
        if not prof or (arms and prof not in arms):
            continue
        t = r["task"]
        a = acc[t][prof]
        a["p"].append(1.0 if r.get("pass") else 0.0)
        a["usd"].append(float(r.get("usd") or 0))
        a["secs"].append(float(r.get("secs") or 0))
        keys.setdefault(t, keys_of(r))
    tasks = {t: {p: {"solved": statistics.mean(a["p"]), "usd": statistics.mean(a["usd"]),
                     "secs": statistics.mean(a["secs"])} for p, a in per.items()} for t, per in acc.items()}
    return tasks, keys, skipped


def score(tasks: dict, ids: list[str], profile: str) -> tuple[float, float, float]:
    rows = [tasks[t][profile] for t in ids]
    return (round(sum(r["solved"] for r in rows), 6), round(sum(r["usd"] for r in rows), 6),
            round(statistics.median(r["secs"] for r in rows), 3) if rows else 0.0)


def best(tasks: dict, ids: list[str], profiles: list[str]) -> str:
    """Most solved, then lowest total cost, then lowest median time (ties: name, so the choice is stable)."""
    def k(p):
        s, c, t = score(tasks, ids, p)
        return (-s, c, t, p)
    return min(profiles, key=k)


def select(tasks: dict, keys: dict, min_tasks: int = 3, min_saving: float = 0.15, default: str | None = None) -> dict:
    """{"default": name, "assign": {key: profile}, "groups": [decision rows], "profiles": [...], "n_tasks": n}."""
    profiles = sorted({p for per in tasks.values() for p in per})
    common = sorted(t for t, per in tasks.items() if all(p in per for p in profiles))
    if not profiles or not common:
        return {"default": default or "full", "assign": {}, "groups": [], "profiles": profiles, "n_tasks": 0}
    dflt = default or best(tasks, common, profiles)
    groups: dict = defaultdict(list)
    for t in common:
        for k in keys.get(t, []):
            groups[k].append(t)
    assign, table = {}, []
    for k in sorted(groups):
        ids = groups[k]
        if len(ids) < min_tasks:
            continue
        sc = {p: score(tasks, ids, p) for p in profiles}
        win = best(tasks, ids, profiles)
        ds, dc, _ = sc[dflt]
        ws, wc, _ = sc[win]
        if win == dflt:
            chosen, why = dflt, "default is the best here"
        elif ws > ds:
            chosen, why = win, f"solves {ws:g} vs {ds:g} for the default"
        elif ws == ds and dc > 0 and wc <= dc * (1 - min_saving):
            chosen, why = win, f"ties on solved ({ws:g}) and costs {100 * (1 - wc / dc):.0f}% less"
        else:
            chosen = dflt
            why = (f"{win} is ahead but inside the noise (solved {ws:g} vs {ds:g}, cost "
                   f"{wc:.4f} vs {dc:.4f}); inherits the default")
        if chosen != dflt:
            assign[k] = chosen
        table.append({"key": k, "n": len(ids), "scores": sc, "winner": win, "chosen": chosen, "why": why})
    return {"default": dflt, "assign": assign, "groups": table, "profiles": profiles, "n_tasks": len(common),
            "default_score": score(tasks, common, dflt)}


def markdown(res: dict, min_tasks: int, min_saving: float) -> str:
    ps = res["profiles"]
    out = [f"# Profile selection ({res['n_tasks']} tasks run by every profile)", "",
           f"Default: **{res['default']}**. Rule per group: most solved, then lowest total cost, then lowest median "
           f"time; a group gets its own profile only if it solves more than the default or ties and costs "
           f"{min_saving:.0%}+ less. Groups with fewer than {min_tasks} tasks are not listed.", "",
           "Cell: solved / total cost USD / median seconds.", "",
           "| group | n | " + " | ".join(ps) + " | chosen | why |", "|---|---|" + "---|" * len(ps) + "---|---|"]
    for g in res["groups"]:
        cells = [f"{g['scores'][p][0]:g} / {g['scores'][p][1]:.4f} / {g['scores'][p][2]:g}" for p in ps]
        mark = g["chosen"] + (" (own)" if g["chosen"] != res["default"] else " (default)")
        out.append(f"| {g['key']} | {g['n']} | " + " | ".join(cells) + f" | {mark} | {g['why']} |")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+", help="results.jsonl files or run folders")
    ap.add_argument("--out", default="", help="write the assignment here (profiles.json); default: print it")
    ap.add_argument("--md", default="", help="write the decision table here (default: print it)")
    ap.add_argument("--arm-profile", action="append", default=[], metavar="ARM=PROFILE")
    ap.add_argument("--arms", default="", help="only these profiles (comma list)")
    ap.add_argument("--min-tasks", type=int, default=3)
    ap.add_argument("--min-saving", type=float, default=0.15)
    ap.add_argument("--default", default="", help="force the default profile instead of the overall best")
    ap.add_argument("--include-eval", action="store_true", help="also read rows of *eval* suites (the held-out set)")
    a = ap.parse_args(argv)
    mapping = dict(s.split("=", 1) for s in a.arm_profile)
    arms = {s for s in a.arms.split(",") if s} or None
    tasks, keys, skipped = collect(load(a.results), mapping, arms, a.include_eval)
    if skipped:
        print(f"skipped {skipped} rows of *eval* suites (select on dev, measure on eval; --include-eval overrides)",
              file=sys.stderr)
    res = select(tasks, keys, a.min_tasks, a.min_saving, a.default or None)
    prof = {"default": res["default"], "assign": res["assign"]}
    if a.out and Path(a.out).exists():                      # keep custom profile definitions already there
        try:
            old = json.loads(Path(a.out).read_text())
            if isinstance(old.get("profiles"), dict):
                prof["profiles"] = old["profiles"]
        except (OSError, ValueError):
            pass
    prof.setdefault("profiles", {})
    md = markdown(res, a.min_tasks, a.min_saving)
    text = json.dumps(prof, indent=2) + "\n"
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out}")
    else:
        print(text)
    if a.md:
        Path(a.md).write_text(md)
        print(f"wrote {a.md}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
