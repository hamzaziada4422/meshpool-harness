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
APP = Path(os.environ["MH_BENCH_APP"]) if os.environ.get("MH_BENCH_APP") else HERE.parents[1] / "app"
LOCK = threading.Lock()
ARMS = {
    "base": {"MH_HROUTER": "0", "MH_SKILLS": "full"},
    "routed": {"MH_HROUTER": "1", "MH_SKILLS": ""},
    "routed-fast": {"MH_HROUTER": "1", "MH_SKILLS": ""},
    "v1": {"MH_HROUTER": "1", "MH_SKILLS": "", "MH_BENCH_APP": str(Path.home() / ".cache" / "meshpool-v1" / "app")},
    "routed-full": {"MH_HROUTER": "1", "MH_SKILLS": "", "MH_LEAN_TOOLS": "0"},   # every tool: the lean A/B
}


# ---------- tasks --------------------------------------------------------------------------------------------------
EXTRA_ENV: dict = {}       # arm -> extra environment (--arm-env), e.g. a candidate harness's MH_SPECS_FILE


def load_suites(names: list[str]) -> list[dict]:
    out = []
    for n in names:
        p = HERE / "suites" / f"{n}.json"
        for t in json.loads(p.read_text()):
            t["suite"] = n
            out.append(t)
    return out


def setup_workdir(task: dict) -> str:
    if task.get("repo"):                               # a real repository at a commit (swe_local.py)
        import swe_local
        d = tempfile.mkdtemp(prefix=f"mhloop-{task['id']}-")
        os.rmdir(d)
        swe_local.worktree(task, d)
        return d
    d = tempfile.mkdtemp(prefix=f"mhloop-{task['id']}-")
    for rel, body in (task.get("files") or {}).items():
        p = Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    if task.get("git", True):
        git = ["git", "-c", "user.email=l@l", "-c", "user.name=loop"]
        subprocess.run(["git", "init", "-q"], cwd=d)
        subprocess.run(["git", "add", "-A"], cwd=d)
        subprocess.run(git + ["commit", "-qm", "task", "--allow-empty"], cwd=d)
        for step in task.get("history") or []:          # later commits: e.g. the one that introduced a regression
            for rel, body in step["files"].items():
                p = Path(d) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body)
            subprocess.run(["git", "add", "-A"], cwd=d)
            subprocess.run(git + ["commit", "-qm", step["msg"]], cwd=d)
    return d


def grade(task: dict, answer: str, workdir: str) -> tuple[bool, str]:
    """Code-graded: the answer's facts and length, or the task's own check command in its folder."""
    g = task["grade"]
    if g.get("dabench"):                               # InfiAgent-DABench's own closed-form rule
        import dabench
        return dabench.grade(task, answer)
    if g.get("swe"):                                   # official SWE-bench tests, applied only now
        import swe_local
        try:
            return swe_local.grade(task, workdir)
        except subprocess.TimeoutExpired:
            return False, "tests timed out"
    words = len(re.findall(r"\S+", answer or ""))
    if "max_words" in g and words > g["max_words"]:
        return False, f"too long ({words} words > {g['max_words']})"
    for pat in g.get("must", []):
        if not re.search(pat, answer or "", re.I | re.S):
            return False, f"missing /{pat}/"
    for pat in g.get("must_not", []):
        if re.search(pat, answer or "", re.I | re.S):
            return False, f"has /{pat}/"
    for rel, body in (g.get("hidden") or {}).items():   # tests the agent never saw: written only now
        hp = Path(workdir) / rel
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(body)
    if g.get("check"):
        try:
            r = subprocess.run(g["check"], shell=True, cwd=workdir, capture_output=True, text=True,
                               timeout=g.get("timeout", 120))
        except subprocess.TimeoutExpired:
            return False, f"check timed out after {g.get('timeout', 120)}s"
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
    if arm.split("@")[0] in ("pi", "pi-routed"):
        res = run_pi_agent(task, wd, routed=arm.startswith("pi-routed"))
    elif arm.split("@")[0] in ACP_ARMS:
        res = run_acp_agent(arm.split("@")[0], task, wd)
    else:
        res = run_task(task["prompt"], workdir=wd, engine=engine, max_steps=task.get("max_steps", int(os.environ.get("MH_BENCH_MAX_STEPS", "60"))))
    ok, why = grade(task, res.get("answer", ""), wd)
    row = {"task": task["id"], "suite": task["suite"], "arm": arm, "pass": ok, "why": why,
           "secs": round(time.time() - t0, 1), "route": res.get("route"), "model": res.get("model"),
           "error": res.get("error"), "answer_words": len((res.get("answer") or "").split()),
           "trace": res.get("trace") or [], **{
               k: res.get("usage", {}).get(k, 0) for k in ("prompt_tokens", "cached_tokens", "completion_tokens",
                                                            "model_calls")}}
    if task.get("repo"):                               # keep the patch, to learn from failures
        row["patch"] = subprocess.run(["git", "diff", "HEAD", "--", ".", ":!.swe_test.patch", ":!.gold.patch"],
                                      cwd=wd, capture_output=True, text=True).stdout[-20000:]
    Path(out_path).write_text(json.dumps(row))
    if task.get("repo"):
        import swe_local
        swe_local.drop_worktree(task, wd)
    shutil.rmtree(wd, ignore_errors=True)


def run_pi_agent(task: dict, wd: str, routed: bool) -> dict:
    """Pi through Meshpool's own Pi integration (what users run), on the same endpoint and model. routed: the harness
    router's method rides on the first message, as the app sends it."""
    from meshpool_harness.harnesses import PI
    from meshpool_harness.harnesses.pi_rpc import PiAgent
    events: list = []
    a = PiAgent(dict(PI), wd, mode="auto", emit=events.append)
    if os.environ.get("MH_MODEL"):
        a.model = os.environ["MH_MODEL"]
    body, route = task["prompt"], None
    if routed:
        from meshpool_harness import hrouter as HRR
        from meshpool_harness.hrouter.route import addon
        r = HRR.route(task["prompt"], kind="code" if task.get("mode", "code") == "code" else "cowork", workdir=wd)
        route = r.spec
        if addon(r):
            body += "\n\n\u200b" + addon(r).strip()
    try:
        answer = a.send(body)
    finally:
        try:
            a.close()
        except Exception:  # noqa: BLE001
            pass
    errors = [e.get("text", "") for e in events if e.get("type") == "error"]
    return {"answer": answer, "route": route, "model": a.model, "error": errors[-1][:500] if errors else None,
            "usage": {"prompt_tokens": a.usage["in"], "cached_tokens": a.usage["cached"],
                      "completion_tokens": a.usage["out"], "model_calls": a.usage["calls"]}}


ACP_ARMS = ("codex", "opencode", "hermes")


def run_acp_agent(hid: str, task: dict, wd: str) -> dict:
    """Codex, OpenCode or Hermes through Meshpool's own ACP integration (what users run), set up by the app for the
    same endpoint and model. Tokens come from the metering proxy, not from the agent."""
    from meshpool_harness import harnesses as HR
    from meshpool_harness.harnesses.acp import ACPAgent
    model = os.environ.get("MH_MODEL") or None
    events: list = []
    man = HR.get(hid, model)
    a = ACPAgent(man, wd, mode="auto", emit=events.append, approve=lambda *a, **k: ("yes", ""))
    if model and hasattr(a, "set_model"):
        try:
            a.set_model(model)
        except Exception:  # noqa: BLE001
            a.model = model
    try:
        answer = a.send(task["prompt"])
    finally:
        try:
            a.close()
        except Exception:  # noqa: BLE001
            pass
    errors = [e.get("text", "") for e in events if e.get("type") == "error"]
    if not answer:
        answer = "".join(e.get("text", "") for e in events if e.get("type") == "assistant")
    if not errors and re.search(r"stream disconnected|error sending request|unexpected status \d{3}|"
                                r"Exceeded retry limit", answer or ""):
        errors = [answer.strip()[-300:]]               # the agent printed its failure as its reply
    return {"answer": answer, "model": model, "error": errors[-1][:500] if errors else None, "usage": {}}


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


def key_spend(model: str) -> float | None:
    """The key's total spend so far (LiteLLM's x-litellm-key-spend header), from a one-token call. None if the
    endpoint does not say. LiteLLM writes spend in the background, so read it again after a short wait."""
    import urllib.request
    base = (os.environ.get("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")
    body = json.dumps({"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "1"}]}).encode()
    r = urllib.request.Request(base + "/chat/completions", data=body, headers={
        "Authorization": f"Bearer {os.environ.get('MESHPOOL_API_KEY', '')}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            v = resp.headers.get("x-litellm-key-spend")
            return float(v) if v not in (None, "") else None
    except Exception:  # noqa: BLE001
        return None


LEDGER = os.path.expanduser("~/.meshpool-bench-ledger.json")
CAP_USD = float(os.environ.get("MH_BENCH_CAP_USD", "25"))   # total for all runs from the ledger's start
MARGIN_USD = 0.50                                            # stop before the cap, not after it


def cap_left(model: str) -> float | None:
    """Dollars left under the all-runs cap, from the key's billed spend (x-litellm-key-spend). The first call
    records the starting spend. None when the endpoint does not report spend (then only --budget applies)."""
    now = key_spend(model)
    if now is None:
        return None
    if os.environ.get("MH_BENCH_START_SPEND"):        # CI: the starting spend is committed in ci.json
        led = {"start_spend": float(os.environ["MH_BENCH_START_SPEND"]), "cap_usd": CAP_USD}
        return float(led["cap_usd"]) - (now - led["start_spend"])
    try:
        led = json.load(open(LEDGER))
    except (OSError, ValueError):
        led = {"start_spend": now, "cap_usd": CAP_USD, "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        json.dump(led, open(LEDGER, "w"), indent=1)
    return float(led.get("cap_usd", CAP_USD)) - (now - float(led["start_spend"])) - float(led.get("other_spend", 0))


def cost(row: dict, prices: dict) -> float:
    prices = (prices.get("by_model") or {}).get(row.get("model") or "", prices)
    fresh = max(0, row.get("prompt_tokens", 0) - row.get("cached_tokens", 0))
    return (fresh * prices["in"] + row.get("cached_tokens", 0) * prices["cached"]
            + row.get("completion_tokens", 0) * prices["out"]) / 1e6


def _stay_awake() -> None:
    """macOS: a sleeping laptop pauses the run and its clock keeps going (5-hour "tasks"). caffeinate holds the
    machine awake for as long as this process lives."""
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(args) -> int:
    _stay_awake()
    tasks = load_suites(args.suites.split(","))
    if args.tasks:
        tasks = [t for t in tasks if t["id"] in set(args.tasks.split(","))]
    arms = args.arms.split(",")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    res_path = out / "results.jsonl"
    done = set()
    if res_path.exists():
        for line in res_path.read_text().splitlines():
            r = json.loads(line)
            done.add((r["task"], r["arm"], r.get("rep", 0)))
    prices = {"in": args.price_in, "cached": args.price_cached, "out": args.price_out, "by_model": {}}
    if args.prices and Path(args.prices).exists():           # models.json from models.py: each model's own prices
        for m in json.loads(Path(args.prices).read_text()):
            p = m.get("price") or {}
            if "in" in p and "out" in p:
                prices["by_model"][m["id"]] = {"in": p["in"], "out": p["out"], "cached": p.get("cached", p["in"] / 10)}
    models = [m for m in (args.models or "").split(",") if m.strip()] or [os.environ.get("MH_MODEL", "")]
    spent = [sum(cost(json.loads(l), prices) for l in res_path.read_text().splitlines())] if res_path.exists() else [0.0]

    guard = {"n": 0, "stop": False}

    def one(task, arm, model, rep=0):
        label = f"{arm}@{model}" if len(models) > 1 else arm
        if (task["id"], label, rep) in done:
            return
        if spent[0] >= args.budget or guard["stop"]:
            return
        with LOCK:
            guard["n"] += 1
            check = guard["n"] % 10 == 1           # the all-runs cap, read from billed spend every 10 tasks
        if check and not args.no_cap:
            left = cap_left(model or os.environ.get("MH_MODEL", "deepseek-v4.1-flash"))
            if left is not None and left < MARGIN_USD:
                guard["stop"] = True
                print(f"STOP: ${left:.2f} left under the ${CAP_USD:g} cap; no more tasks start.", flush=True)
                return
        if False:
            pass
        else:
            env = dict(os.environ, **ARMS.get(arm, {"MH_HROUTER": "1"}), **EXTRA_ENV.get(arm, {}))
            if model:
                env["MH_MODEL"] = model
            if args.meter_port:
                env["MESHPOOL_BASE_URL"] = f"http://127.0.0.1:{args.meter_port}/m/{task['id']}/{label}/{rep}/v1"
            if task.get("path_prepend"):                 # the task's own Python first on PATH, for every harness
                env["PATH"] = task["path_prepend"] + os.pathsep + env.get("PATH", "")
            home = tempfile.mkdtemp(prefix="mhhome-")    # one data folder per run: agents never share state
            env["MESHPOOL_HOME"] = home
            if arm == "routed-fast" and args.fast_model:
                env["MH_MODEL_FAST"] = args.fast_model
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                tmp = f.name
            try:
                cp = subprocess.run([sys.executable, __file__, "--child", json.dumps(task), arm, tmp], env=env,
                                    timeout=args.timeout, capture_output=True, text=True)
                row = json.loads(Path(tmp).read_text() or "{}")
                if "pass" not in row:                  # the run crashed before grading: keep why
                    row.update(task=task["id"], suite=task["suite"], arm=arm, **{"pass": False}, why="crash",
                               error=("crash: " + (cp.stderr or cp.stdout or "")[-600:]).strip())
            except (subprocess.TimeoutExpired, ValueError):
                row = {"task": task["id"], "suite": task["suite"], "arm": arm, "pass": False, "why": "timeout",
                       "secs": args.timeout}
            finally:
                Path(tmp).unlink(missing_ok=True)
                shutil.rmtree(env.get("MESHPOOL_HOME", ""), ignore_errors=True) if env.get("MESHPOOL_HOME", "").startswith(
                    tempfile.gettempdir()) else None
        row["arm"], row["base_arm"], row["rep"] = label, arm, rep
        if args.meter_port:                            # the meter's count, the same for every harness
            import meter
            m = meter.totals(meter_log).get((task["id"], label, str(rep)))
            if m:
                row.update(prompt_tokens=m["prompt"], cached_tokens=m["cached"], completion_tokens=m["completion"],
                           model_calls=m["calls"], metered=True)
        row.setdefault("model", model)
        row["usd"] = round(cost(row, prices), 6)
        with LOCK:
            spent[0] += row["usd"]
            with res_path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        print(f"{row['arm']:12} {row['task']:28} {'PASS' if row['pass'] else 'fail':4} {row.get('secs', 0):6}s "
              f"${row['usd']:.4f}  {row.get('route') or ''}  {'' if row['pass'] else row.get('why', '')[:80]}",
              flush=True)

    meter_log = str(out / "meter.jsonl")
    if args.meter_port and os.environ.get("MESHPOOL_BASE_URL", "").startswith("http"):
        import meter
        meter.start(args.meter_port, os.environ["MESHPOOL_BASE_URL"], meter_log)   # every arm measured alike
    for a in arms:                                     # download npx agents now, not inside a timed run
        base = a.split("@")[0]
        if base in ("codex", "opencode"):
            sys.path.insert(0, str(APP))
            from meshpool_harness import harnesses as HR
            man = HR.CODEX if base == "codex" else HR.OPENCODE
            subprocess.run([man["command"], *man["args"][:2], "--version"], capture_output=True, timeout=600)
    spend_path = out / "spend.json"
    actual = json.loads(spend_path.read_text()) if spend_path.exists() else {}
    for m in models:                                   # one model at a time, so the key's spend can be split
        if spent[0] >= args.budget:
            print(f"budget reached (${spent[0]:.2f}); skipping {m}", flush=True)
            continue
        s0 = key_spend(m) if m and not args.no_spend else None
        # arms of one task side by side (same hour, same cache state), repeated to tell a real difference from luck
        jobs = [(t, a, m, k) for k in range(args.repeats) for t in tasks for a in arms]
        with ThreadPoolExecutor(args.parallel) as ex:
            list(ex.map(lambda j: one(*j), jobs))
        if s0 is not None:
            time.sleep(args.spend_wait)                # LiteLLM books spend in the background
            s1 = key_spend(m)
            if s1 is not None:
                actual[m] = round(actual.get(m, 0) + (s1 - s0), 6)
                spend_path.write_text(json.dumps(actual, indent=1))
                est = sum(json.loads(l).get("usd", 0) for l in res_path.read_text().splitlines()
                          if json.loads(l).get("model") == m)
                spent[0] += max(0.0, actual[m] - est)  # the budget follows real spend, not the estimate
                print(f"{m}: key spend ${actual[m]:.4f} (estimate ${est:.4f})", flush=True)
    labels = [f"{a}@{m}" if len(models) > 1 else a for m in models for a in arms]
    return report(out, labels, args.slack)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence interval for a solve rate (Wilson score)."""
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return max(0.0, c - h), min(1.0, c + h)


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided McNemar p: b = pairs only arm A solved, c = pairs only arm B solved."""
    from math import comb
    n = b + c
    if not n:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def ratio_ci(pairs: list[tuple[float, float]], iters: int = 2000) -> tuple[float, float, float]:
    """Ratio of totals (A/B) with a paired bootstrap 95% interval; pairs are (a, b) per task and repeat."""
    import random
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs or not sum(b for _, b in pairs):
        return float("nan"), float("nan"), float("nan")
    rnd = random.Random(11)
    est = sum(a for a, _ in pairs) / sum(b for _, b in pairs)
    vals = []
    for _ in range(iters):
        smp = [pairs[rnd.randrange(len(pairs))] for _ in pairs]
        sb = sum(b for _, b in smp)
        if sb:
            vals.append(sum(a for a, _ in smp) / sb)
    vals.sort()
    return est, vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def compare(by: dict, keys: list, a: str, b: str) -> dict:
    """Arm a against arm b over the (task, repeat) pairs both ran: quality by McNemar, cost and time by paired
    bootstrap. A difference counts only when it is beyond chance (p < 0.05, or the 95% interval excludes 1)."""
    pairs = [(by[(t, a, r)], by[(t, b, r)]) for t, r in keys if (t, a, r) in by and (t, b, r) in by]
    only_a = sum(x["pass"] and not y["pass"] for x, y in pairs)
    only_b = sum(y["pass"] and not x["pass"] for x, y in pairs)
    p = mcnemar_p(only_a, only_b)
    solved = [(x, y) for x, y in pairs if x["pass"] and y["pass"]]     # cost and time where both solved
    timed = [(x, y) for x, y in solved if x.get("secs", 0) < 3600 and y.get("secs", 0) < 3600]   # not across a sleep
    cost = ratio_ci([(x.get("usd", 0), y.get("usd", 0)) for x, y in solved])
    secs = ratio_ci([(x.get("secs", 0), y.get("secs", 0)) for x, y in timed])
    quality = ("better" if only_a > only_b and p < 0.05 else "worse" if only_b > only_a and p < 0.05 else "tie")
    cheaper = "cheaper" if cost[2] < 1 else "costlier" if cost[1] > 1 else "same cost"
    faster = "faster" if secs[2] < 1 else "slower" if secs[1] > 1 else "same speed"
    return {"n": len(pairs), "only_a": only_a, "only_b": only_b, "p": p, "quality": quality, "cost": cost,
            "secs": secs, "cheaper": cheaper, "faster": faster}


def report(out: Path, arms: list[str] | None = None, slack: int = 1) -> int:
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines()]
    for r in rows:                                     # rows from a crashed run (older bench): count as errors
        if "pass" not in r:
            r.update({"pass": False, "why": "crash", "error": r.get("error") or "crash (no output kept)"})
        r.setdefault("task", "?")
        r.setdefault("suite", "?")
    rows = [r for r in rows if r["task"] != "?"]
    sp = out / "spend.json"
    actual = json.loads(sp.read_text()) if sp.exists() else {}
    for m, real in actual.items():                     # scale each model's token estimates to the billed total
        est = sum(r.get("usd", 0) for r in rows if r.get("model") == m)
        if est > 0 and real > 0:
            for r in rows:
                if r.get("model") == m:
                    r["usd"] = r.get("usd", 0) * real / est
    for r in rows:
        r.setdefault("rep", 0)
    arms = arms or sorted({r["arm"] for r in rows})
    by = {(r["task"], r["arm"], r["rep"]): r for r in rows}
    keys = sorted({(r["task"], r["rep"]) for r in rows})
    keys = [k for k in keys if all((k[0], a, k[1]) in by for a in arms)]   # paired: every arm ran it
    reps = len({r for _, r in keys}) or 1
    lines = [f"# Harness loop: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", "",
             f"{len({t for t, _ in keys})} tasks x {reps} repeat(s), run by every arm ({', '.join(arms)}). Same model "
             "and hour per pair, graded by code. Intervals are 95%; a difference counts only when it is beyond chance.",
             ("Cost: billed key spend per model, split between arms by tokens: "
              + ", ".join(f"{m} ${v:.4f}" for m, v in actual.items())) if actual else
             "Cost: estimated from tokens at list prices (no billed spend available).", "",
             "| Arm | Solved | Solve rate (95% CI) | Errors | $ per solved | s per solved | Out tokens | Calls |",
             "|---|---|---|---|---|---|---|---|"]
    stats = {}
    for a in arms:
        rs = [by[(t, a, r)] for t, r in keys]
        k = sum(x["pass"] for x in rs)
        lo, hi = wilson(k, len(rs))
        usd, secs = sum(x.get("usd", 0) for x in rs), sum(x.get("secs", 0) for x in rs)
        errs = sum(bool(x.get("error")) or x.get("why") == "timeout" for x in rs)
        stats[a] = {"solved": k, "usd": usd, "secs": secs, "errors": errs, "n": len(rs)}
        n = max(1, len(rs))
        lines.append(f"| {a} | {k}/{len(rs)} | {k / n:.0%} ({lo:.0%}-{hi:.0%}) | {errs} | ${usd / max(1, k):.4f} | "
                     f"{secs / max(1, k):.1f} | {sum(x.get('completion_tokens', 0) for x in rs) / n:.0f} | "
                     f"{sum(x.get('model_calls', 0) for x in rs) / n:.1f} |")
    suites = sorted({r["suite"] for r in rows})
    lines += ["", "Solved per suite:", "", "| Suite | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for s_ in suites:
        ks = [k for k in keys if by[(k[0], arms[0], k[1])]["suite"] == s_]
        lines.append(f"| {s_} | " + " | ".join(f"{sum(by[(t, a, r)]['pass'] for t, r in ks)}/{len(ks)}" for a in arms)
                     + " |")
    if reps > 1:                                       # one-offs: tasks an arm solved in some repeats, not all
        flaky = []
        for a in arms:
            for t in sorted({t for t, _ in keys}):
                ps = [by[(t, a, r)]["pass"] for tt, r in keys if tt == t]
                if 0 < sum(ps) < len(ps):
                    flaky.append(f"{a}: {t} {sum(ps)}/{len(ps)}")
        lines += ["", "Flaky (solved in some repeats only): " + ("; ".join(flaky) or "none")]
    code = 0
    pairs = []                                         # Meshpool (routed) against every other harness, per model
    for a in arms:
        if a.split("@")[0] != "routed":
            continue
        model = a.split("@", 1)[1] if "@" in a else ""
        for b in arms:
            if b != a and b.split("@")[0] != "routed" and (b.split("@", 1)[1] if "@" in b else "") == model:
                pairs.append((a, b, model))
        for b in arms:                                 # and the routed variants against each other (A/B of a change)
            if b != a and b.split("@")[0].startswith("routed-") and (b.split("@", 1)[1] if "@" in b else "") == model:
                pairs.append((a, b, model))
    head = ["", "## Meshpool against each harness, same model", "",
            "| Model | vs | Quality | Cost (Meshpool / them) | Time (Meshpool / them) |", "|---|---|---|---|---|"]
    for a, b, model in pairs:
        if a not in stats or b not in stats:
            continue
        c = compare(by, keys, a, b)
        f = lambda t: "n/a" if t[0] != t[0] else f"{t[0]:.2f}x ({t[1]:.2f}-{t[2]:.2f})"   # noqa: E731
        head.append(f"| {model or '-'} | {b.split('@')[0]} | {c['quality']} ({c['only_a']} vs {c['only_b']}, "
                    f"p={c['p']:.2f}) | {c['cheaper']} {f(c['cost'])} | {c['faster']} {f(c['secs'])} |")
        lines += ["", f"**{a} vs {b}**{' on ' + model if model else ''}: quality {c['quality'].upper()} "
                      f"({c['only_a']} pairs only {a} solved, {c['only_b']} only {b}, p={c['p']:.3f}); "
                      f"cost {c['cheaper']} {f(c['cost'])}; time {c['faster']} {f(c['secs'])}"]
        if b.split("@")[0] == "base" and c["quality"] == "worse":
            code = 1                                   # a real quality regression fails the run
    if len(head) > 5:                                  # the head-to-head first, the detail after
        at = next(i for i, l in enumerate(lines) if l.startswith("| Arm |"))
        lines[at:at] = head[1:] + ["", "## Every harness in detail", ""]
    broken = [a for a in arms if stats[a].get("errors", 0) >= max(2, stats[a]["n"] // 2)]
    if broken:
        lines += ["", "Not comparable (most tasks ended on an endpoint or model error): " + ", ".join(broken)]
    text = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(text)
    print(text)
    return code


def load_key_file(path: str = os.path.expanduser("~/.meshpool-bench.env")) -> bool:
    """Local runs: the endpoint and key from a private file (never printed). Its values win over the environment,
    whose key may be a masked copy."""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return False
    for line in lines:
        k, _, v = line.strip().partition("=")
        v = v.strip().strip('"').strip("'")
        if k in ("MESHPOOL_API_KEY", "MESHPOOL_BASE_URL") and v:
            os.environ[k] = v
    return True


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3], sys.argv[4])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="base,routed")
    ap.add_argument("--suites", default="chat,debug,build")
    ap.add_argument("--out", default="/tmp/mhloop")
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--budget", type=float, default=1.5)
    ap.add_argument("--slack", type=int, default=1)
    ap.add_argument("--tasks", default="", help="comma list of task ids (default: every task in the suites)")
    ap.add_argument("--repeats", type=int, default=1, help="run every task this many times per arm")
    ap.add_argument("--fast-model", default="")
    ap.add_argument("--models", default="", help="comma list: every arm runs on each model")
    ap.add_argument("--prices", default="", help="models.json from models.py (per-model prices)")
    ap.add_argument("--no-spend", action="store_true", help="do not read the key's billed spend")
    ap.add_argument("--no-cap", action="store_true", help="skip the all-runs cap (mock endpoints only)")
    ap.add_argument("--cap-status", action="store_true", help="show spend against the all-runs cap and exit")
    ap.add_argument("--meter-port", type=int, default=18990, help="metering proxy port (0: trust each harness)")
    ap.add_argument("--spend-wait", type=int, default=45)
    ap.add_argument("--price-in", type=float, default=0.15)
    ap.add_argument("--price-cached", type=float, default=0.003)
    ap.add_argument("--price-out", type=float, default=0.60)
    ap.add_argument("--report", default="")
    ap.add_argument("--arm-env", action="append", default=[], help="ARM:KEY=VAL (repeatable): extra env for one arm")
    a = ap.parse_args()
    for spec in a.arm_env:
        arm, _, kv = spec.partition(":")
        k, _, v = kv.partition("=")
        EXTRA_ENV.setdefault(arm, {})[k] = v
    load_key_file()
    if not os.environ.get("MH_BENCH_START_SPEND") and not os.path.exists(LEDGER):
        start = key_spend(os.environ.get("MH_MODEL", "deepseek-v4.1-flash"))
        if start is not None:                         # first run anywhere: say the starting point loudly
            print(f"CAP START: key spend is ${start:.4f}; the ${CAP_USD:g} cap counts from here", flush=True)
    if a.cap_status:
        left = cap_left(os.environ.get("MH_MODEL", "deepseek-v4.1-flash"))
        led = json.load(open(LEDGER)) if os.path.exists(LEDGER) else {}
        print("endpoint does not report spend" if left is None else
              f"spent ${float(led.get('cap_usd', CAP_USD)) - left:.2f} of ${float(led.get('cap_usd', CAP_USD)):g} "
              f"since {led.get('set_at')}; ${left:.2f} left")
        return
    if a.report:
        sys.exit(report(Path(a.report)))
    sys.exit(run(a))


if __name__ == "__main__":
    main()
