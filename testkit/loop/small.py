"""Small, cheap cost tests: is Meshpool cheaper than Codex, without running the whole 52-task eval?

    python testkit/loop/small.py                                  # quick: 4 tasks, meshpool vs codex, ~3 min, cents
    python testkit/loop/small.py --set cost8 --repeat 2           # the 8 tasks of the cost report, twice
    python testkit/loop/small.py --tasks 6 --arms meshpool,codex,v2 --budget 0.40 --json out.json
    python testkit/loop/small.py --offline                        # no network: per-call prompt/tool overhead + budgets

Same tasks, graders and price table as the full benchmark (suites/v2_eval.json + suites/chat.json, bench.py's grade()
and cost formula, deepseek-v4.1-flash at 0.15 / 0.003 / 0.60 USD per 1M tokens in / cached / out). Every arm talks to
the endpoint through a metering proxy (small_meter.py) that counts each request, streamed or not, with cached tokens.

Arms:  meshpool  the app's agent as users run it (routed harness, bench arm "routed")
       codex     the real Codex CLI (`codex exec`), same endpoint, Responses wire
       v2        the frozen v2 build in ~/.cache/meshpool-v2 (as the full benchmark's "v2" arm)
       any other bench.py arm name (base, routed, pi, opencode, codex-acp for Codex through the ACP adapter, ...)

Endpoint: MESHPOOL_BASE_URL (default https://llm.meshpool.ai/v1) and MESHPOOL_API_KEY; a key injected by a proxy
works with no variable set. Any OpenAI-compatible gateway works, Azure OpenAI included (see testkit/SMALL_TESTS.md).

The verdict is a number from a handful of runs: with fewer than 8 task pairs there is no interval and the line says so.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bench  # noqa: E402  (task format, graders, arms)
import small_codex as CX  # noqa: E402
import small_meter as SM  # noqa: E402

DEFAULT_MODEL = "deepseek-v4.1-flash"
PRICES = {"in": 0.15, "cached": 0.003, "out": 0.60}        # per 1M tokens, same as bench.py

QUICK = ["v2-alg-topo-cycle", "v2-dbg-retry", "v2-amb-search-normalize", "chat-explain"]
COST8 = ["v2-alg-topo-cycle", "v2-dbg-retry", "v2-mf-atomic-transfer", "v2-te-business-days",
         "v2-data-cohort-retention", "v2-rf-split-invoice", "v2-amb-search-normalize", "chat-explain"]
COST20 = COST8 + ["v2-alg-expr", "v2-alg-k-hop-path", "v2-dbg-tz-daily", "v2-dbg-stale-cache", "v2-mf-config-layers",
                  "v2-mf-rate-limit", "v2-perf-rolling-max", "v2-perf-n-plus-one", "v2-rf-break-cycle",
                  "v2-te-money-split", "v2-data-messy-join", "v2-data-cagr-gaps"]
SETS = {"quick": QUICK, "cost8": COST8, "cost20": COST20}
# hard spend ceilings in list-price USD (billed spend on the Meshpool gateway is about 2x list)
BUDGETS = {"quick": 0.25, "cost8": 0.60, "cost20": 1.50}
BOOT_MIN = 8                                                 # pairs needed before an interval is printed


# ---------- tasks ---------------------------------------------------------------------------------------------------
def all_tasks() -> list[dict]:
    return bench.load_suites(["v2_eval", "chat"])


def pick_tasks(spec_ids: list[str], pool: list[dict]) -> list[dict]:
    by = {t["id"]: t for t in pool}
    out = []
    for i in spec_ids:
        t = by.get(i) or by.get("v2-" + i)
        if not t:
            raise SystemExit(f"unknown task id {i!r}; ids look like v2-alg-topo-cycle or chat-explain")
        out.append(t)
    return out


def first_n(n: int, pool: list[dict]) -> list[dict]:
    """The first n tasks of one fixed order: the preset tasks first (quick, then cost8, then cost20), then the rest of
    the eval in file order. --tasks 4 is therefore the quick set."""
    order = list(dict.fromkeys(QUICK + COST8 + COST20 + [t["id"] for t in pool]))
    return pick_tasks(order[:n], pool)


# ---------- statistics ----------------------------------------------------------------------------------------------
def per_solved(rows: list[dict]) -> float:
    solved = sum(1 for r in rows if r["pass"])
    return sum(r["usd"] for r in rows) / solved if solved else float("nan")


def ratio_stats(pairs: list[tuple[dict, dict]], iters: int = 4000, seed: int = 7) -> dict:
    """Subject/reference cost ratio over paired (task, repeat) runs: dollars per solved task, so a failed run's cost
    counts but its solve does not. The interval is a paired bootstrap over the pairs (95%), only with >= BOOT_MIN."""
    a, b = [p[0] for p in pairs], [p[1] for p in pairs]
    sa, sb = sum(r["pass"] for r in a), sum(r["pass"] for r in b)
    ra, rb = per_solved(a), per_solved(b)
    ratio = ra / rb if rb and not math.isnan(ra) and not math.isnan(rb) else float("nan")
    tot_a, tot_b = sum(r["usd"] for r in a), sum(r["usd"] for r in b)
    out = {"n": len(pairs), "solved_a": sa, "solved_b": sb, "ratio": ratio, "usd_a": tot_a, "usd_b": tot_b,
           "ratio_total_usd": tot_a / tot_b if tot_b else float("nan"), "lo": None, "hi": None,
           "significant": False, "interval": False}
    if len(pairs) >= BOOT_MIN and not math.isnan(ratio):
        rnd, vals = random.Random(seed), []
        for _ in range(iters):
            s = [pairs[rnd.randrange(len(pairs))] for _ in pairs]
            x, y = per_solved([p[0] for p in s]), per_solved([p[1] for p in s])
            if y and not math.isnan(x) and not math.isnan(y):
                vals.append(x / y)
        if len(vals) > 100:
            vals.sort()
            out["lo"], out["hi"] = vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))]
            out["interval"] = True
            out["significant"] = out["hi"] < 1.0 or out["lo"] > 1.0
        only_a = sum(p[0]["pass"] and not p[1]["pass"] for p in pairs)
        only_b = sum(p[1]["pass"] and not p[0]["pass"] for p in pairs)
        out["mcnemar_p"] = bench.mcnemar_p(only_a, only_b)
    return out


def verdict_line(subject: str, ref: str, st: dict) -> str:
    n, ratio = st["n"], st["ratio"]
    if math.isnan(ratio):
        head, rtxt = "NOT CHEAPER", "n/a"
    else:
        head, rtxt = ("CHEAPER" if ratio < 1.0 else "NOT CHEAPER"), f"{ratio:.2f}"
    tail = f"{n} tasks, solved {st['solved_a']} vs {st['solved_b']}"
    if st["interval"]:
        tail += f"; 95% CI {st['lo']:.2f}-{st['hi']:.2f}" + ("" if st["significant"] else ", not significant")
    else:
        tail += f"; n<{BOOT_MIN}, no interval, not significant"
    return f"VERDICT: {head} (ratio {rtxt}, {tail})"


# ---------- running -------------------------------------------------------------------------------------------------
def meshpool_arm_env(arm: str) -> dict:
    return bench.arm_env({"meshpool": "routed", "codex-acp": "codex"}.get(arm, arm))


def bench_arm(task: dict, arm: str, base: str, key: str, model: str, timeout: float) -> dict:
    """An arm that bench.py's child process runs (the app agent, v2, pi, ACP agents), pointed at the meter."""
    real = {"meshpool": "routed", "codex-acp": "codex"}.get(arm, arm)
    env = dict(os.environ, **meshpool_arm_env(arm))
    env.update(MESHPOOL_BASE_URL=base, MESHPOOL_API_KEY=key)
    if model:
        env["MH_MODEL"] = model
    if env.get("MH_BENCH_APP") and not (Path(env["MH_BENCH_APP"]) / "meshpool_harness").exists():
        return {"pass": False, "why": "arm unavailable", "secs": 0, "error": f"{arm}: {env['MH_BENCH_APP']} is missing"}
    if task.get("path_prepend"):
        env["PATH"] = task["path_prepend"] + os.pathsep + env.get("PATH", "")
    home = tempfile.mkdtemp(prefix="mhhome-")
    env["MESHPOOL_HOME"] = home
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    t0 = time.time()
    try:
        cp = subprocess.run([sys.executable, str(HERE / "bench.py"), "--child", json.dumps(task), real, tmp], env=env,
                            timeout=timeout, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        row = json.loads(Path(tmp).read_text() or "{}")
        if "pass" not in row:
            row.update({"pass": False, "why": "crash", "error": ("crash: " + (cp.stderr or cp.stdout or "")[-400:]).strip()})
    except (subprocess.TimeoutExpired, ValueError):
        row = {"pass": False, "why": "timeout", "secs": round(time.time() - t0, 1)}
    finally:
        Path(tmp).unlink(missing_ok=True)
        shutil.rmtree(home, ignore_errors=True)
    return {k: row.get(k) for k in ("pass", "why", "secs", "error", "route", "model")}


def codex_arm(task: dict, base: str, model: str, timeout: float, cmd: list[str]) -> dict:
    wd = bench.setup_workdir(task)
    t0 = time.time()
    try:
        r = CX.run(cmd, task["prompt"], wd, base, model, timeout)
        ok, why = bench.grade(task, r["answer"], wd)
        return {"pass": ok, "why": why if not r["error"] or ok else (why + " | " + r["error"])[:200],
                "secs": round(time.time() - t0, 1), "error": r["error"], "model": model}
    finally:
        shutil.rmtree(wd, ignore_errors=True)


class Runner:
    def __init__(self, args, meter: SM.Meter, key: str, model: str, codex_cmd):
        self.a, self.meter, self.key, self.model, self.codex_cmd = args, meter, key, model, codex_cmd
        self.rows: list[dict] = []
        self.lock = threading.Lock()
        self.stop = False

    def job(self, task: dict, arm: str, rep: int) -> None:
        if self.stop or self.meter.exceeded:
            return self._add({"task": task["id"], "arm": arm, "rep": rep, "skipped": "budget", "pass": False,
                              "usd": 0.0, "secs": 0})
        base = self.meter.base_for(task["id"], arm, rep)
        if arm == "codex":
            row = codex_arm(task, base, self.model, self.a.timeout, self.codex_cmd)
        else:
            row = bench_arm(task, arm, base, self.key, self.model, self.a.timeout)
        m = self.meter.totals().get((task["id"], arm, str(rep)),
                                    {"calls": 0, "failed": 0, "prompt": 0, "cached": 0, "completion": 0, "usd": 0.0,
                                     "usage_missing": 0})
        row.update(task=task["id"], arm=arm, rep=rep, calls=m["calls"], failed_calls=m["failed"],
                   prompt_tokens=m["prompt"], cached_tokens=m["cached"], completion_tokens=m["completion"],
                   usd=round(m["usd"], 6), usage_missing=m["usage_missing"])
        if (task["id"], arm, str(rep)) in self.meter.blocked_keys:
            row["skipped"] = "budget"                      # cut off mid-task by the hard budget: not a result
        self._add(row)

    def _add(self, row: dict) -> None:
        with self.lock:
            self.rows.append(row)
        if row.get("skipped"):
            print(f"{row['arm']:9} {row['task']:26} skipped (budget)", flush=True)
            return
        miss = "  [no usage in replies]" if row.get("usage_missing") else ""
        print(f"{row['arm']:9} {row['task']:26} {'PASS' if row['pass'] else 'fail':4} {row.get('secs') or 0:6.1f}s "
              f"{row['calls']:3} calls ${row['usd']:.4f}{miss}  "
              f"{'' if row['pass'] else (row.get('why') or '')[:70]}", flush=True)


# ---------- report --------------------------------------------------------------------------------------------------
def fmt_int(n) -> str:
    return f"{int(n):,}"


def table(rows: list[dict], arms: list[str], subject: str | None, ref: str | None, tasks: list[dict]) -> str:
    lines = []
    hdr = f"{'task':26} {'arm':9} {'ok':4} {'in':>9} {'cached':>9} {'out':>7} {'calls':>5} {'$/task':>8} {'secs':>6}  ratio"
    lines += [hdr, "-" * len(hdr)]
    by = {(r["task"], r["arm"], r["rep"]): r for r in rows if not r.get("skipped")}
    reps = sorted({r["rep"] for r in rows})
    for t in tasks:
        for rep in reps:
            for arm in arms:
                r = by.get((t["id"], arm, rep))
                if not r:
                    continue
                ratio = ""
                if subject and ref and arm == subject and (t["id"], ref, rep) in by:
                    o = by[(t["id"], ref, rep)]
                    ratio = f"{r['usd'] / o['usd']:.2f}" if o["usd"] else "n/a"
                    ratio += "" if r["pass"] == o["pass"] else "  (solved differs)"
                name = t["id"] + (f"#{rep + 1}" if len(reps) > 1 else "")
                lines.append(f"{name[:26]:26} {arm[:9]:9} {'PASS' if r['pass'] else 'fail':4} "
                             f"{fmt_int(r['prompt_tokens']):>9} {fmt_int(r['cached_tokens']):>9} "
                             f"{fmt_int(r['completion_tokens']):>7} {r['calls']:>5} {r['usd']:>8.4f} {r.get('secs') or 0:>6.1f}  {ratio}")
    return "\n".join(lines)


def arm_summary(rows: list[dict], arms: list[str]) -> str:
    hdr = f"{'arm':9} {'solved':>8} {'in/task':>9} {'cached/task':>12} {'out/task':>9} {'calls/task':>10} {'$/task':>8} {'$/solved':>9} {'secs/task':>9}"
    out = [hdr, "-" * len(hdr)]
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm and not r.get("skipped")]
        if not rs:
            continue
        n, solved = len(rs), sum(r["pass"] for r in rs)
        ps = per_solved(rs)
        out.append(f"{arm:9} {f'{solved}/{n}':>8} {fmt_int(sum(r['prompt_tokens'] for r in rs) / n):>9} "
                   f"{fmt_int(sum(r['cached_tokens'] for r in rs) / n):>12} {fmt_int(sum(r['completion_tokens'] for r in rs) / n):>9} "
                   f"{sum(r['calls'] for r in rs) / n:>10.1f} {sum(r['usd'] for r in rs) / n:>8.4f} "
                   f"{('%.4f' % ps) if not math.isnan(ps) else 'n/a':>9} {sum(r.get('secs') or 0 for r in rs) / n:>9.1f}")
    return "\n".join(out)


def report(rows: list[dict], arms: list[str], tasks: list[dict], meter: SM.Meter, budget: float, args) -> tuple[str, dict]:
    done = [r for r in rows if not r.get("skipped")]
    subject = ref = None
    if "codex" in arms and len(arms) > 1:
        ref = "codex"
        subject = next((a for a in arms if a != "codex"), None)
    elif len(arms) >= 2:
        subject, ref = arms[0], arms[1]
    print("\n" + table(rows, arms, subject, ref, tasks))
    print("\n" + arm_summary(rows, arms))
    result: dict = {"rows": rows, "spent_usd_list": round(meter.spent, 5), "budget_usd": budget, "blocked_requests": meter.blocked}
    extra = []
    by = {(r["task"], r["arm"], r["rep"]): r for r in done}
    if ref:                                           # every non-reference arm against the reference
        for arm in arms:
            if arm == ref:
                continue
            pairs = [(by[(t, arm, rep)], by[(t, ref, rep)]) for (t, a, rep) in by if a == arm and (t, ref, rep) in by]
            if pairs:
                result.setdefault("ratios", {})[arm] = ratio_stats(pairs)
                if arm != subject:
                    st = result["ratios"][arm]
                    extra.append(f"also {arm}/{ref}: ratio {st['ratio']:.2f} on {st['n']} tasks, solved {st['solved_a']} vs {st['solved_b']}")
    print(f"\nspend: ${meter.spent:.4f} at list price (budget ${budget:.2f}, hard stop); "
          f"billed spend can be higher on a gateway that marks up (about 2x on Meshpool's).")
    if meter.blocked:
        print(f"BUDGET STOP: {meter.blocked} request(s) were refused after the budget was spent; those task runs are excluded.")
    problems = [f"{r['arm']} {r['task']}: {(r.get('error') or '')[:100]}" for r in done if r.get("error") and not r["pass"]][:4]
    for p in problems:
        print("error:", p)
    if any(r.get("usage_missing") for r in done):
        print("warning: some replies carried no usage block; those tokens are missing from the table (cost is an underestimate).")
    for e in extra:
        print(e)
    if not subject or not ref or subject not in result.get("ratios", {}):
        line = "VERDICT: n/a (needs two arms with finished tasks, e.g. --arms meshpool,codex)"
        print("\n" + line)
        result["verdict"] = line
        return line, result
    st = result["ratios"][subject]
    if st["interval"]:
        print(f"\nNOISE: paired bootstrap over {st['n']} task runs, 95% interval for {subject}/{ref} $ per solved: "
              f"{st['lo']:.2f} to {st['hi']:.2f}" + (" (excludes 1.0: a real difference)" if st["significant"]
                                                    else " (spans 1.0: not significant, this run cannot tell them apart)")
              + f"; solve-rate McNemar p={st.get('mcnemar_p', 1.0):.2f}")
    else:
        print(f"\nNOISE: {st['n']} task run(s), below {BOOT_MIN}: no interval. One run per task is a smoke test of the "
              "direction, not a measurement; use --set cost20 --repeat 2 for an interval.")
    if st["solved_a"] < st["solved_b"]:
        print(f"WARNING: {subject} solved fewer tasks ({st['solved_a']} vs {st['solved_b']}): compare quality before cost.")
    if ratio_total_differs(st):
        print(f"(total $ ratio, ignoring solves: {st['ratio_total_usd']:.2f})")
    line = verdict_line(subject, ref, st)
    result["verdict"] = line
    print("\n" + line)
    return line, result


def ratio_total_differs(st: dict) -> bool:
    return not math.isnan(st["ratio"]) and not math.isnan(st["ratio_total_usd"]) and abs(st["ratio"] - st["ratio_total_usd"]) > 0.005


# ---------- main ----------------------------------------------------------------------------------------------------
def preflight(meter: SM.Meter, model: str, arms: list[str]) -> str | None:
    """One tiny request per wire the arms need, through the meter: a wrong key, URL or model fails here, not after
    eight tasks. Returns an error text or None."""
    import urllib.error
    import urllib.request

    def post(path: str, body: dict) -> str | None:
        r = urllib.request.Request(meter.base_for("preflight", "pre", 0) + path, data=json.dumps(body).encode(),
                                   headers={"content-type": "application/json", "authorization": "Bearer " + os.environ.get("MESHPOOL_API_KEY", "proxy-injected-key")})
        try:
            urllib.request.urlopen(r, timeout=60).read()
            return None
        except urllib.error.HTTPError as e:
            return f"HTTP {e.code} from {path}: {e.read()[:240].decode('utf-8', 'replace')}"
        except Exception as e:  # noqa: BLE001
            return f"{path}: {e}"
    err = post("/chat/completions", {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "1"}]})
    if err:
        return err
    if "codex" in arms:
        err = post("/responses", {"model": model, "max_output_tokens": 16, "input": "1"})
        if err:
            return "the Codex arm needs the Responses API (POST /responses): " + err
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Small cost tests: is Meshpool cheaper than Codex? (see testkit/SMALL_TESTS.md)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--tasks", type=int, help="the first N tasks of the fixed order (4 = quick, 8 = cost8, 20 = cost20)")
    g.add_argument("--set", choices=sorted(SETS), help="a preset: quick (4 tasks), cost8, cost20 (default quick)")
    ap.add_argument("--ids", help="comma-separated task ids (v2- prefix optional), instead of --tasks/--set")
    ap.add_argument("--arms", default="meshpool,codex", help="comma-separated: meshpool, codex, v2, or any bench.py arm")
    ap.add_argument("--budget", type=float, help="hard stop in list-price USD (default by preset: quick 0.25, cost8 0.60, cost20 1.50)")
    ap.add_argument("--repeat", type=int, default=1, help="runs per task and arm (more repeats: tighter interval)")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=300, help="seconds per task run")
    ap.add_argument("--model", default=os.environ.get("MH_MODEL") or DEFAULT_MODEL, help="model or deployment name")
    ap.add_argument("--json", help="write rows, summary and verdict here")
    ap.add_argument("--price-in", type=float, default=PRICES["in"])
    ap.add_argument("--price-cached", type=float, default=PRICES["cached"])
    ap.add_argument("--price-out", type=float, default=PRICES["out"])
    ap.add_argument("--api-version", default=os.environ.get("AZURE_OPENAI_API_VERSION", ""), help="Azure: query api-version=")
    ap.add_argument("--offline", action="store_true", help="no network: per-call overhead per lane and budgets")
    ap.add_argument("--no-preflight", action="store_true")
    ap.add_argument("--list", action="store_true", help="print the selected tasks and exit")
    a = ap.parse_args(argv)

    if a.offline:
        import small_offline
        return 1 if small_offline.run_offline()[2] else 0

    pool = all_tasks()
    if a.ids:
        tasks, preset = pick_tasks([i.strip() for i in a.ids.split(",") if i.strip()], pool), None
    elif a.tasks:
        tasks, preset = first_n(a.tasks, pool), {4: "quick", 8: "cost8", 20: "cost20"}.get(a.tasks)
    else:
        preset = a.set or "quick"
        tasks = pick_tasks(SETS[preset], pool)
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    if a.list:
        print("\n".join(t["id"] for t in tasks))
        return 0
    if len(tasks) > 20:
        print(f"note: {len(tasks)} tasks is a big run; the full eval is bench.py --suites v2_eval", file=sys.stderr)
    budget = a.budget if a.budget is not None else (BUDGETS.get(preset) or round(0.03 * len(tasks) * len(arms) * a.repeat + 0.05, 2))
    prices = {"in": a.price_in, "cached": a.price_cached, "out": a.price_out}
    upstream = (os.environ.get("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")
    key = os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY") or "proxy-injected-key"
    os.environ["MESHPOOL_API_KEY"] = key
    for var in ("NO_PROXY", "no_proxy"):               # the harnesses talk to the local meter: never through a proxy
        cur = [x for x in os.environ.get(var, "").split(",") if x]
        os.environ[var] = ",".join(cur + [h for h in ("127.0.0.1", "localhost") if h not in cur])
    azure = urlsplit(upstream).hostname or ""
    azure = azure.endswith((".openai.azure.com", ".cognitiveservices.azure.com", ".services.ai.azure.com"))
    meter = SM.Meter(upstream, prices, max_usd=budget, azure_key=key if azure else None,
                     extra_query=f"api-version={a.api_version}" if a.api_version else "").start()
    codex_cmd = None
    if "codex" in arms:
        codex_cmd = CX.find_codex()
        if not codex_cmd:
            print("error: the Codex CLI is not installed (npm i -g @openai/codex, or set CODEX_BIN)", file=sys.stderr)
            return 2
        if codex_cmd[0] == "npx":
            subprocess.run(codex_cmd + ["--version"], capture_output=True, timeout=600, stdin=subprocess.DEVNULL)   # download now, not inside a timed run
    print(f"small cost test: {len(tasks)} task(s) x {len(arms)} arm(s) x {a.repeat} repeat(s); model {a.model}; "
          f"endpoint {upstream}{' (azure auth)' if azure else ''}; budget ${budget:.2f} list price; "
          f"parallel {a.parallel}; prices in/cached/out {prices['in']}/{prices['cached']}/{prices['out']} per 1M")
    if codex_cmd:
        print("codex:", CX.version(codex_cmd))
    if not a.no_preflight:
        err = preflight(meter, a.model, arms)
        if err:
            print("PREFLIGHT FAILED:", err, file=sys.stderr)
            print("Check MESHPOOL_BASE_URL / MESHPOOL_API_KEY / --model (see testkit/SMALL_TESTS.md); --no-preflight skips this.", file=sys.stderr)
            return 2
    runner = Runner(a, meter, key, a.model, codex_cmd)
    jobs = [(t, arm, rep) for rep in range(a.repeat) for t in tasks for arm in arms]
    t0 = time.time()
    ex = ThreadPoolExecutor(a.parallel)
    try:
        list(ex.map(lambda j: runner.job(*j), jobs))
    except KeyboardInterrupt:
        runner.stop = True
        print("\ninterrupted: reporting what finished", file=sys.stderr)
        ex.shutdown(wait=False, cancel_futures=True)
    else:
        ex.shutdown()
    print(f"\nfinished in {time.time() - t0:.0f}s")
    line, result = report(runner.rows, arms, tasks, meter, budget, a)
    result["config"] = {"tasks": [t["id"] for t in tasks], "arms": arms, "repeat": a.repeat, "model": a.model,
                        "endpoint": upstream, "prices_per_1M": prices, "secs": round(time.time() - t0, 1)}
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1, default=str))
    meter.close()
    return 0 if not line.startswith("VERDICT: n/a") else 2


if __name__ == "__main__":
    sys.exit(main())
