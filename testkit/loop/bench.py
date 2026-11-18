"""The harness loop: are the routed harnesses actually better? Same tasks, same model, same hour, graded by code.

    python testkit/loop/bench.py --arms base,routed --suites chat,debug,build --out /tmp/loop
    python testkit/loop/bench.py --report /tmp/loop

Arms (each task runs in its own process, so settings never leak between arms):
  base     the app agent as it shipped: no harness router, full skills list in every prompt (MH_HROUTER=0, MH_SKILLS=full)
  routed   the harness router: specialised harness, language pack + diagnostics, skills on demand, concise answers
  routed-fast  routed, plus MH_MODEL_FAST for chat-lane messages (set --fast-model)
  pi / codex   the frontier harnesses, when installed and configured for the same endpoint (--arms pi,codex)

Suites are small, deterministic and graded by code (no judge model): an answer must contain the right fact and stay
short; a bug must be fixed so the task's own check passes. Every row records pass, seconds, tokens and estimated cost.

Budget: the run stops starting new tasks once the estimated spend passes --budget (default $1.50). Prices per 1M
tokens come from --price-in/--price-cached/--price-out (defaults: DeepSeek V4.1 Flash, off-peak).

Verdict (the loop's exit code): 1 if the routed arm solves fewer tasks than base by more than --slack (default 1),
so a regression fails the nightly run; the summary also shows cost and time per solved task.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1] / "app"
LOCK = threading.Lock()
ARMS = {
    "base": {"MH_HROUTER": "0", "MH_SKILLS": "full"},
    "routed": {"MH_HROUTER": "1", "MH_SKILLS": ""},
    "routed-fast": {"MH_HROUTER": "1", "MH_SKILLS": ""},
}


# ---------- tasks --------------------------------------------------------------------------------------------------
def load_suites(names: list[str]) -> list[dict]:
    out = []
    for n in names:
        p = HERE / "suites" / f"{n}.json"
        for t in json.loads(p.read_text()):
            t["suite"] = n
            out.append(t)
    return out


def setup_workdir(task: dict) -> str:
    d = tempfile.mkdtemp(prefix=f"mhloop-{task['id']}-")
    for rel, body in (task.get("files") or {}).items():
        p = Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    if task.get("git", True):
        subprocess.run(["git", "init", "-q"], cwd=d)
        subprocess.run(["git", "add", "-A"], cwd=d)
        subprocess.run(["git", "-c", "user.email=l@l", "-c", "user.name=loop", "commit", "-qm", "task", "--allow-empty"],
                       cwd=d)
    return d


def grade(task: dict, answer: str, workdir: str) -> tuple[bool, str]:
    """Code-graded: the answer's facts and length, or the task's own check command in its folder."""
    g = task["grade"]
    words = len(re.findall(r"\S+", answer or ""))
    if "max_words" in g and words > g["max_words"]:
        return False, f"too long ({words} words > {g['max_words']})"
    for pat in g.get("must", []):
        if not re.search(pat, answer or "", re.I | re.S):
            return False, f"missing /{pat}/"
    for pat in g.get("must_not", []):
        if re.search(pat, answer or "", re.I | re.S):
            return False, f"has /{pat}/"
    if g.get("check"):
        r = subprocess.run(g["check"], shell=True, cwd=workdir, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return False, "check failed: " + (r.stdout + r.stderr).strip()[-300:]
    for rel, pat in (g.get("file_must") or {}).items():
        p = Path(workdir) / rel
        if not p.exists() or not re.search(pat, p.read_text(errors="replace"), re.S):
            return False, f"{rel} lacks /{pat}/"
    return True, "ok"


# ---------- one task on one arm (child process) --------------------------------------------------------------------
def child(task_json: str, arm: str, out_path: str) -> None:
    task = json.loads(task_json)
    sys.path.insert(0, str(APP))
    from meshpool_harness import run_task
    wd = setup_workdir(task)
    t0 = time.time()
    engine = "agent" if task.get("mode", "code") == "code" else "agent-work"
    res = run_task(task["prompt"], workdir=wd, engine=engine, max_steps=task.get("max_steps", 30))
    ok, why = grade(task, res.get("answer", ""), wd)
    row = {"task": task["id"], "suite": task["suite"], "arm": arm, "pass": ok, "why": why,
           "secs": round(time.time() - t0, 1), "route": res.get("route"), "model": res.get("model"),
           "error": res.get("error"), "answer_words": len((res.get("answer") or "").split()), **{
               k: res.get("usage", {}).get(k, 0) for k in ("prompt_tokens", "cached_tokens", "completion_tokens",
                                                            "model_calls")}}
    Path(out_path).write_text(json.dumps(row))
    shutil.rmtree(wd, ignore_errors=True)


def run_pi(task: dict, arm: str) -> dict:
    """A frontier harness through its own print mode, on the same endpoint and model (best effort)."""
    wd = setup_workdir(task)
    exe = shutil.which(arm)
    if not exe:
        return {"task": task["id"], "suite": task["suite"], "arm": arm, "pass": False, "why": f"{arm} not installed",
                "secs": 0, "skipped": True}
    cmd = [exe, "-p", task["prompt"]] if arm == "pi" else [exe, "exec", "--full-auto", task["prompt"]]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=900)
    ok, why = grade(task, r.stdout, wd)
    shutil.rmtree(wd, ignore_errors=True)
    return {"task": task["id"], "suite": task["suite"], "arm": arm, "pass": ok, "why": why,
            "secs": round(time.time() - t0, 1), "answer_words": len(r.stdout.split())}


def cost(row: dict, prices: dict) -> float:
    fresh = max(0, row.get("prompt_tokens", 0) - row.get("cached_tokens", 0))
    return (fresh * prices["in"] + row.get("cached_tokens", 0) * prices["cached"]
            + row.get("completion_tokens", 0) * prices["out"]) / 1e6


def run(args) -> int:
    tasks = load_suites(args.suites.split(","))
    arms = args.arms.split(",")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    res_path = out / "results.jsonl"
    done = set()
    if res_path.exists():
        for line in res_path.read_text().splitlines():
            r = json.loads(line)
            done.add((r["task"], r["arm"]))
    prices = {"in": args.price_in, "cached": args.price_cached, "out": args.price_out}
    spent = [sum(cost(json.loads(l), prices) for l in res_path.read_text().splitlines())] if res_path.exists() else [0.0]

    def one(task, arm):
        if (task["id"], arm) in done:
            return
        if spent[0] >= args.budget:
            return
        if arm in ("pi", "codex"):
            row = run_pi(task, arm)
        else:
            env = dict(os.environ, **ARMS.get(arm, {}))
            if arm == "routed-fast" and args.fast_model:
                env["MH_MODEL_FAST"] = args.fast_model
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                tmp = f.name
            try:
                subprocess.run([sys.executable, __file__, "--child", json.dumps(task), arm, tmp], env=env,
                               timeout=args.timeout, capture_output=True, text=True)
                row = json.loads(Path(tmp).read_text() or "{}")
            except (subprocess.TimeoutExpired, ValueError):
                row = {"task": task["id"], "suite": task["suite"], "arm": arm, "pass": False, "why": "timeout",
                       "secs": args.timeout}
            finally:
                Path(tmp).unlink(missing_ok=True)
        row["usd"] = round(cost(row, prices), 6)
        with LOCK:
            spent[0] += row["usd"]
            with res_path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        print(f"{row['arm']:12} {row['task']:28} {'PASS' if row['pass'] else 'fail':4} {row.get('secs', 0):6}s "
              f"${row['usd']:.4f}  {row.get('route') or ''}  {'' if row['pass'] else row.get('why', '')[:80]}",
              flush=True)

    jobs = [(t, a) for t in tasks for a in arms]     # arms of one task side by side: same hour, same cache state
    with ThreadPoolExecutor(args.parallel) as ex:
        list(ex.map(lambda ta: one(*ta), jobs))
    return report(out, arms, args.slack)


def report(out: Path, arms: list[str] | None = None, slack: int = 1) -> int:
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines()]
    arms = arms or sorted({r["arm"] for r in rows})
    tasks = sorted({r["task"] for r in rows})
    by = {(r["task"], r["arm"]): r for r in rows}
    both = [t for t in tasks if all((t, a) in by for a in arms)]   # paired: only tasks every arm finished
    lines = [f"# Harness loop: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", "",
             f"{len(both)} tasks run by every arm ({', '.join(arms)}). Same model, same hour, graded by code.", "",
             "| Arm | Solved | $ per solved | s per solved | Out tokens per task | Model calls per task |",
             "|---|---|---|---|---|---|"]
    stats = {}
    for a in arms:
        rs = [by[(t, a)] for t in both]
        solved = sum(r["pass"] for r in rs)
        usd = sum(r.get("usd", 0) for r in rs)
        secs = sum(r.get("secs", 0) for r in rs)
        outt = sum(r.get("completion_tokens", 0) for r in rs)
        calls = sum(r.get("model_calls", 0) for r in rs)
        stats[a] = {"solved": solved, "usd": usd, "secs": secs}
        n = max(1, len(rs))
        lines.append(f"| {a} | {solved}/{len(rs)} | ${usd / max(1, solved):.4f} | {secs / max(1, solved):.1f} | "
                     f"{outt / n:.0f} | {calls / n:.1f} |")
    suites = sorted({r["suite"] for r in rows})
    lines += ["", "Per suite (solved):", "", "| Suite | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for s in suites:
        ts = [t for t in both if by[(t, arms[0])]["suite"] == s]
        lines.append(f"| {s} | " + " | ".join(f"{sum(by[(t, a)]['pass'] for t in ts)}/{len(ts)}" for a in arms) + " |")
    only = {a: [t for t in both if by[(t, a)]["pass"] and not all(by[(t, b)]["pass"] for b in arms)] for a in arms}
    lines += ["", "Solved by some arms but not all: " + "; ".join(f"{a}: {', '.join(v) or 'none'}" for a, v in only.items())]
    code = 0
    if "base" in stats and "routed" in stats:
        b, r = stats["base"], stats["routed"]
        verdict = ("BETTER" if r["solved"] > b["solved"] or (r["solved"] == b["solved"] and r["usd"] < b["usd"])
                   else "WORSE" if r["solved"] < b["solved"] - slack else "SAME")
        lines += ["", f"Verdict, routed vs base: **{verdict}** (solved {r['solved']} vs {b['solved']}, "
                      f"cost ${r['usd']:.4f} vs ${b['usd']:.4f}, time {r['secs']:.0f}s vs {b['secs']:.0f}s)"]
        code = 1 if verdict == "WORSE" else 0
    text = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(text)
    print(text)
    return code


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3], sys.argv[4])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="base,routed")
    ap.add_argument("--suites", default="chat,debug,build")
    ap.add_argument("--out", default="/tmp/mhloop")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--budget", type=float, default=1.5)
    ap.add_argument("--slack", type=int, default=1)
    ap.add_argument("--fast-model", default="")
    ap.add_argument("--price-in", type=float, default=0.15)
    ap.add_argument("--price-cached", type=float, default=0.003)
    ap.add_argument("--price-out", type=float, default=0.60)
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    if a.report:
        sys.exit(report(Path(a.report)))
    sys.exit(run(a))


if __name__ == "__main__":
    main()
