"""One command: Meshpool, Codex CLI and Pi on the same model, through the same metering proxy, on the same tasks.

    python testkit/bench3/run.py --out docs/results/v4/<name> --tasks all|id,id --arms codex,pi,mp-cli,mp-work,mp-code \
        --reps 3 --model gpt-6-luna --parallel 4 --budget 30 --mp-src app [--upstream URL] [--python PATH]

Writes <out>/results.jsonl (one row per task x arm x rep), <out>/meter.jsonl (every model request), <out>/run.json
(environment), then run report.py <out>. Re-running resumes: finished (task, arm, rep) are skipped unless --rerun.
"""
from __future__ import annotations

import argparse
import datetime
import importlib
import importlib.util
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(HERE), str(ROOT), str(ROOT / "testkit" / "loop")):
    if p not in sys.path:
        sys.path.insert(0, p)

import arms as A                      # noqa: E402
from small_meter import Meter         # noqa: E402  (the one metering proxy; see testkit/loop/small_meter.py)

UPSTREAM = "https://llm.meshpool.ai/v1"


def load_prices(path: Path | None = None) -> dict:
    d = json.loads((path or HERE / "prices.json").read_text())
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_tasks(spec: str | None, module: str | None):
    if module and module.endswith(".py"):
        s = importlib.util.spec_from_file_location("bench3_tasks_ext", module)
        m = importlib.util.module_from_spec(s)
        sys.modules["bench3_tasks_ext"] = m
        s.loader.exec_module(m)
    else:
        m = importlib.import_module(module or "testkit.bench3.tasks")
    if spec in (None, "", "all"):
        return list(m.TASKS)
    return list(m.get(spec))


def _git(args, cwd):
    return subprocess.run(["git", "-c", "user.email=bench@local", "-c", "user.name=bench"] + args, cwd=cwd,
                          capture_output=True, text=True, timeout=120).stdout.strip()


def prepare_workdir(task, wd: Path) -> None:
    import shutil
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    task.setup(wd)
    try:
        _git(["init", "-q"], wd)
        _git(["add", "-A"], wd)
        _git(["commit", "-q", "-m", "initial", "--allow-empty"], wd)
    except Exception:  # noqa: BLE001
        pass


def versions(mp_src: str, python: str) -> dict:
    def out(cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            return (r.stdout or r.stderr).strip().splitlines()[0]
        except Exception as e:  # noqa: BLE001
            return f"unavailable ({e})"
    src = Path(mp_src).resolve()
    return {"codex": out(A.codex_cmd() + ["--version"]), "pi": out(["pi", "--version"]),
            "meshpool_src": str(src), "meshpool_git": out(["git", "-C", str(src), "rev-parse", "HEAD"]),
            "python": python}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--tasks-module", help="module name or .py path with TASKS and get(); default testkit.bench3.tasks")
    ap.add_argument("--arms", default="")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--model", default="gpt-6-luna")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--budget", type=float, default=30.0, help="USD; the meter refuses model calls once spent")
    ap.add_argument("--mp-src", default="app")
    ap.add_argument("--python", default=A.DEFAULT_PYTHON)
    ap.add_argument("--upstream", default=UPSTREAM)
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--settle", type=float, default=4.0, help="seconds to wait after an arm ends for late nested calls")
    ap.add_argument("--timeout", type=int, default=0, help="override every task's timeout (seconds)")
    a = ap.parse_args(argv)

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks(a.tasks, a.tasks_module)
    wanted = [x for x in a.arms.split(",") if x] or None
    prices = load_prices()
    price = prices.get(a.model)
    meter = Meter(a.upstream, price or {"in": 0.0, "cached": 0.0, "out": 0.0}, max_usd=a.budget).start()
    res_path, meter_path = out / "results.jsonl", out / "meter.jsonl"

    done = set()
    if res_path.exists() and not a.rerun:
        for line in res_path.read_text().splitlines():
            try:
                r = json.loads(line)
                done.add((r["task"], r["arm"], int(r["rep"])))
            except (ValueError, KeyError):
                pass
    (out / "run.json").write_text(json.dumps({
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "model": a.model,
        "prices": prices, "price_used": price, "upstream": a.upstream, "budget_usd": a.budget, "reps": a.reps,
        "tasks": [t.id for t in tasks], "arms": wanted, "versions": versions(a.mp_src, a.python),
        "meter": "testkit/loop/small_meter.py (no reasoning-token field; reasoning is reported as null)"}, indent=1))

    jobs = []
    for rep in range(1, a.reps + 1):                    # same task + rep: arms side by side
        for t in tasks:
            for arm in A.arms_for(t.kind, wanted):
                if (t.id, arm, rep) not in done:
                    jobs.append((t, arm, rep))
    print(f"{len(jobs)} jobs ({len(done)} already done); meter on port {meter.port}", flush=True)

    lock = threading.Lock()
    dumped = [0]
    abort = {"why": ""}

    def dump_meter():
        with lock:
            rows = list(meter.calls)
            new, dumped[0] = rows[dumped[0]:], len(rows)
            if new:
                with open(meter_path, "a") as f:
                    for r in new:
                        f.write(json.dumps(r) + "\n")
            if any(r["status"] == 429 for r in new):
                abort["why"] = "HTTP 429 from the gateway (budget_exceeded?): stopping"

    def job(t, arm, rep):
        if abort["why"] or meter.exceeded:
            return None
        key = (t.id, arm, str(rep))
        wd = out / "work" / t.id / arm / str(rep)
        home = out / "home" / t.id / arm / str(rep)
        log = out / "logs" / f"{t.id}.{arm}.{rep}.log"
        try:
            prepare_workdir(t, wd)
            res = A.run_arm(arm, prompt=t.prompt, workdir=wd, home=home, base_url=meter.base_for(t.id, arm, rep),
                            model=a.model, timeout=a.timeout or t.timeout, log=log, mp_src=a.mp_src, python=a.python,
                            task_kind=t.kind)
        except Exception as e:  # noqa: BLE001
            res = {"reply": "", "secs": 0.0, "rc": None, "error": f"{type(e).__name__}: {e}", "log": str(log),
                   "timed_out": False}
        time.sleep(a.settle)                            # late calls from engines / graders the agent started
        try:
            g = t.grade(wd, res.get("reply", ""))
        except Exception as e:  # noqa: BLE001
            g = {"ok": False, "detail": f"grader crashed: {type(e).__name__}: {e}", "passed": 0, "total": 0}
        tot = meter.totals().get(key, {})
        row = {"task": t.id, "kind": t.kind, "arm": arm, "rep": rep, "model": a.model, "ok": bool(g.get("ok")),
               "detail": str(g.get("detail", ""))[:500], "passed": g.get("passed"), "total": g.get("total"),
               "secs": res.get("secs"), "timed_out": bool(res.get("timed_out")), "error": res.get("error") or "",
               "calls": tot.get("calls", 0), "failed_calls": tot.get("failed", 0), "input": tot.get("prompt", 0),
               "cached": tot.get("cached", 0), "output": tot.get("completion", 0), "reasoning": None,
               "cost": (tot.get("usd", 0.0) if price else None), "log": res.get("log", "")}
        dump_meter()
        with lock, open(res_path, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"[{t.id} {arm} #{rep}] ok={row['ok']} calls={row['calls']} secs={row['secs']} cost={row['cost']}",
              flush=True)
        return row

    with ThreadPoolExecutor(max_workers=max(1, a.parallel)) as ex:
        list(ex.map(lambda j: job(*j), jobs))
    dump_meter()
    if meter.blocked:
        print(f"budget: {meter.blocked} model requests refused after ${meter.spent:.4f} spent", flush=True)
    if abort["why"]:
        print(abort["why"], flush=True)
    meter.close()
    return 2 if abort["why"] else 0


if __name__ == "__main__":
    sys.exit(main())
