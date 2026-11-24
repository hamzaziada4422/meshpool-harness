"""Real-world benchmark runner: Meshpool (shipped default) vs Codex CLI vs Pi, same model, same meter, same hour.

    python testkit/realworld/run.py --out /tmp/claude-0/rw/run --tasks all --arms mp,codex,pi --reps 1 \
        --mp-app /tmp/claude-0/rw/mp-head/app --task-python /tmp/claude-0/rw/taskvenv/bin/python --budget 3

Arms (every one points its base URL at the metering proxy testkit/loop/small_meter.py, path /m/<task>/<arm>/<rep>/v1):
  mp     `python -m meshpool_harness -p <prompt> --auto --output-format stream-json` from --mp-app (a fresh worktree of
         HEAD), private MESHPOOL_HOME, no MH_* switch other than MH_MODEL.
  codex  the real Codex CLI (`codex exec --json`, stdin closed), Responses wire, private CODEX_HOME.
  pi     Pi through Meshpool's Pi integration, as testkit/loop/bench.py's "pi" arm (pi_child.py).

Jobs run interleaved (for each rep, each task, the three arms in a rotating order) on one pool of --parallel workers,
so the arms share the hour. Budget: the meter refuses model calls (HTTP 402) once --budget USD (prices from
testkit/bench3/prices.json) is spent; an HTTP 429 from the gateway (budget_exceeded) stops new jobs at once.
Writes <out>/results.jsonl (one row per task x arm x rep), <out>/meter.jsonl (every request), <out>/run.json, and
<out>/logs/<task>.<arm>.<rep>.log (the agent's trace). Re-running resumes.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(ROOT / "testkit" / "loop"), str(ROOT / "testkit" / "bench3")):
    if p not in sys.path:
        sys.path.insert(0, p)

import arms as A                       # noqa: E402  (bench3: run_proc, kill_group)
import small_codex                     # noqa: E402
from small_meter import Meter          # noqa: E402

UPSTREAM = "https://llm.meshpool.ai/v1"
ARMS = ("mp", "codex", "pi")


def base_env(path_prepend: list[str]) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("MH_") and not k.startswith("MESHPOOL_")}
    e["PATH"] = os.pathsep.join([*path_prepend, e.get("PATH", "")])
    e.pop("PYTHONPATH", None)
    return e


def mp_home(home: Path, base_url: str, model: str) -> dict:
    home.mkdir(parents=True, exist_ok=True)
    (home / "meshpool.config").write_text(f"MESHPOOL_API_KEY=dummy\nMESHPOOL_BASE_URL={base_url}\nMH_MODEL={model}\n")
    return {"MESHPOOL_HOME": str(home), "MESHPOOL_API_KEY": "dummy", "MESHPOOL_BASE_URL": base_url, "MH_MODEL": model}


def _last_result(log: Path) -> dict:
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return {}
    for ln in reversed(lines):
        ln = ln.strip()
        if ln.startswith("{") and '"type": "result"' in ln:
            try:
                return json.loads(ln)
            except ValueError:
                continue
    return {}


def run_mp(*, prompt, workdir, home, base_url, model, timeout, log, env, mp_app, python, **_):
    e = dict(env, **mp_home(Path(home), base_url, model), PYTHONPATH=str(mp_app))
    cmd = [python, "-m", "meshpool_harness", "-p", prompt, "--auto", "--output-format", "stream-json"]
    r = A.run_proc(cmd, cwd=workdir, env=e, timeout=timeout, log=log)
    res = _last_result(Path(log))
    r["reply"] = str(res.get("result") or "")
    if res and not res.get("ok", True) and not r.get("error"):
        r["error"] = "agent reported failure"
    return r


def run_codex(*, prompt, workdir, home, base_url, model, timeout, log, env, **_):
    small_codex.CACHE.mkdir(parents=True, exist_ok=True)
    ch = Path(tempfile.mkdtemp(prefix="codex-home-", dir=str(small_codex.CACHE)))
    last = ch / "last.txt"
    (ch / "config.toml").write_text(
        f'model = "{model}"\nmodel_provider = "meter"\napproval_policy = "never"\nsandbox_mode = "danger-full-access"\n'
        f'model_context_window = 128000\n\n[model_providers.meter]\nname = "meter"\nbase_url = "{base_url}"\n'
        f'env_key = "METER_KEY"\nwire_api = "responses"\nrequest_max_retries = 2\nstream_max_retries = 2\n')
    cmd = (small_codex.find_codex() or ["codex"]) + ["exec", "--json", "--skip-git-repo-check",
                                                     "--output-last-message", str(last), prompt]
    r = A.run_proc(cmd, cwd=workdir, env=dict(env, CODEX_HOME=str(ch), METER_KEY="dummy"), timeout=timeout, log=log)
    r["reply"] = last.read_text(errors="replace").strip() if last.exists() else ""
    shutil.rmtree(ch, ignore_errors=True)
    return r


def run_pi(*, prompt, workdir, home, base_url, model, timeout, log, env, mp_app, python, **_):
    e = dict(env, **mp_home(Path(home), base_url, model), PYTHONPATH=str(mp_app))
    pf = Path(home) / "prompt.txt"
    pf.write_text(prompt, encoding="utf-8")
    r = A.run_proc([python, str(HERE / "pi_child.py"), str(pf), str(workdir)], cwd=workdir, env=e, timeout=timeout,
                   log=log)
    res = _last_result(Path(log))
    r["reply"] = str(res.get("result") or "")
    if res.get("error") and not r.get("error"):
        r["error"] = str(res["error"])[:300]
    return r


RUNNERS = {"mp": run_mp, "codex": run_codex, "pi": run_pi}


def _git(args, cwd):
    subprocess.run(["git", "-c", "user.email=bench@local", "-c", "user.name=bench"] + args, cwd=cwd,
                   capture_output=True, text=True, timeout=120)


def prepare(task, wd: Path) -> None:
    if wd.exists():
        if task.teardown:
            task.teardown(wd)
        shutil.rmtree(wd, ignore_errors=True)
    if task.category == "swe":
        wd.parent.mkdir(parents=True, exist_ok=True)
        task.setup(wd)
        return
    wd.mkdir(parents=True)
    task.setup(wd)
    _git(["init", "-q"], wd)
    _git(["add", "-A"], wd)
    _git(["commit", "-q", "-m", "initial", "--allow-empty"], wd)


def versions(mp_app: Path, python: str) -> dict:
    def out(cmd, **kw):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, **kw)
            return ((r.stdout or r.stderr).strip().splitlines() or ["?"])[-1]
        except Exception as e:  # noqa: BLE001
            return f"unavailable ({e})"
    pi_pkg = mp_app.parent / "pi" / "node_modules" / "@earendil-works" / "pi-coding-agent" / "package.json"
    return {"codex": out((small_codex.find_codex() or ["codex"]) + ["--version"]),
            "pi": json.loads(pi_pkg.read_text()).get("version") if pi_pkg.exists() else "missing",
            "meshpool_app": str(mp_app), "meshpool_git": out(["git", "-C", str(mp_app), "rev-parse", "HEAD"]),
            "meshpool_version": out([python, "-m", "meshpool_harness", "--version"], cwd=str(mp_app),
                                    env=dict(base_env([]), PYTHONPATH=str(mp_app))),
            "python": python}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tasks", default="all", help="all, a category (swe, office, everyday) or task ids, comma list")
    ap.add_argument("--arms", default="mp,codex,pi")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--rep-start", type=int, default=1)
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--spent-before", type=float, default=0.0, help="USD already spent by earlier invocations")
    ap.add_argument("--mp-app", required=True)
    ap.add_argument("--python", default=sys.executable, help="Python that runs Meshpool and the Pi child")
    ap.add_argument("--task-python", default="", help="python first on PATH for the agents and used by graders")
    ap.add_argument("--upstream", default=UPSTREAM)
    ap.add_argument("--settle", type=float, default=3.0)
    a = ap.parse_args(argv)

    if a.task_python:
        os.environ["RW_TASK_PYTHON"] = a.task_python
    from testkit.realworld import tasks as T
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tasks = T.get(a.tasks)
    arms = [x for x in a.arms.split(",") if x]
    prices = json.loads((ROOT / "testkit" / "bench3" / "prices.json").read_text())
    price = prices[a.model]
    meter = Meter(a.upstream, price, max_usd=max(0.0, a.budget - a.spent_before)).start()
    mp_app = Path(a.mp_app).resolve()
    res_path, meter_path = out / "results.jsonl", out / "meter.jsonl"
    done = set()
    if res_path.exists():
        for line in res_path.read_text().splitlines():
            try:
                r = json.loads(line)
                done.add((r["task"], r["arm"], int(r["rep"])))
            except (ValueError, KeyError):
                pass
    runinfo = out / "run.json"
    info = json.loads(runinfo.read_text()) if runinfo.exists() else {"invocations": []}
    info["invocations"].append({
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "model": a.model,
        "price_used": price, "upstream": a.upstream, "budget_usd": a.budget, "spent_before": a.spent_before,
        "parallel": a.parallel, "reps": [a.rep_start, a.rep_start + a.reps - 1], "tasks": [t.id for t in tasks],
        "arms": arms, "versions": versions(mp_app, a.python), "task_python": a.task_python})
    runinfo.write_text(json.dumps(info, indent=1))

    jobs = []
    k = 0
    for rep in range(a.rep_start, a.rep_start + a.reps):
        for t in tasks:
            order = arms[k % len(arms):] + arms[:k % len(arms)]       # rotate who starts, task by task
            k += 1
            for arm in order:
                if (t.id, arm, rep) not in done:
                    jobs.append((t, arm, rep))
    print(f"{len(jobs)} jobs ({len(done)} done); meter on :{meter.port}; budget left ${meter.max_usd:.2f}", flush=True)

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
            for r in new:
                if r["status"] == 429:
                    abort["why"] = f"HTTP 429 from the gateway on {r['task']}/{r['arm']}: {r.get('error', '')} - stopping"

    def job(t, arm, rep):
        if abort["why"] or meter.exceeded:
            return None
        key = (t.id, arm, str(rep))
        wd = out / "work" / t.id / arm / str(rep)
        home = out / "home" / t.id / arm / str(rep)
        log = out / "logs" / f"{t.id}.{arm}.{rep}.log"
        shutil.rmtree(home, ignore_errors=True)
        pp = list(t.path_prepend) + ([str(Path(a.task_python).parent)] if a.task_python else [])
        try:
            prepare(t, wd)
            res = RUNNERS[arm](prompt=t.prompt, workdir=wd, home=home, base_url=meter.base_for(t.id, arm, rep),
                               model=a.model, timeout=t.timeout, log=log, env=base_env(pp), mp_app=mp_app,
                               python=a.python)
        except Exception as e:  # noqa: BLE001
            res = {"reply": "", "secs": 0.0, "error": f"{type(e).__name__}: {e}", "timed_out": False}
        time.sleep(a.settle)
        try:
            g = t.grade(wd, res.get("reply", ""))
        except Exception as e:  # noqa: BLE001
            g = {"ok": False, "detail": f"grader crashed: {e}"}
        if t.category == "swe":                       # keep the patch for the loss analysis, then drop the worktree
            diff = subprocess.run(["git", "diff", "HEAD"], cwd=wd, capture_output=True, text=True).stdout
            (out / "logs" / f"{t.id}.{arm}.{rep}.patch").write_text(diff[-40000:])
            if t.teardown:
                t.teardown(wd)
            shutil.rmtree(wd, ignore_errors=True)
        tot = meter.totals().get(key, {})
        row = {"task": t.id, "category": t.category, "arm": arm, "rep": rep, "model": a.model, "ok": bool(g.get("ok")),
               "detail": str(g.get("detail", ""))[:500], "secs": res.get("secs"),
               "timed_out": bool(res.get("timed_out")), "error": str(res.get("error") or "")[:300],
               "calls": tot.get("calls", 0), "failed_calls": tot.get("failed", 0), "input": tot.get("prompt", 0),
               "cached": tot.get("cached", 0), "output": tot.get("completion", 0), "cost": round(tot.get("usd", 0.0), 6),
               "budget_blocked": key in meter.blocked_keys, "reply_tail": (res.get("reply") or "")[-300:]}
        dump_meter()
        with lock, open(res_path, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"[{t.id} {arm} #{rep}] ok={row['ok']} calls={row['calls']} secs={row['secs']} ${row['cost']:.4f} "
              f"spent=${meter.spent:.3f} {row['detail'][:80]}", flush=True)
        return row

    with ThreadPoolExecutor(max_workers=max(1, a.parallel)) as ex:
        list(ex.map(lambda j: job(*j), jobs))
    dump_meter()
    print(f"spent this invocation: ${meter.spent:.4f}; blocked requests: {meter.blocked}", flush=True)
    if abort["why"]:
        print(abort["why"], flush=True)
    meter.close()
    return 2 if abort["why"] else 0


if __name__ == "__main__":
    sys.exit(main())
