"""Offline overhead check: what does each harness send on every model call, with no network and no model?

    python testkit/loop/small.py --offline           # the same, from the small-test CLI
    python testkit/loop/small_offline.py             # table + budget check; exit 1 when a budget is broken

A fake OpenAI-compatible server (small_fake.py) replays a scripted 6-step task (small_recorded.json): five shell calls,
then the final answer. The real Meshpool agent runs against it once per lane (code, work, chat, data), in its own
process with a clean settings folder, and so does the Codex CLI when it is installed. Every request body is kept, and
we measure what a model call costs before the task itself matters:

  system     system / developer prompt (tokens)         tools   tool schemas (tokens)
  fixed      system + tools: resent on every call        call1 / callN   whole request at the first / last call
  growth     tokens added per call by the history       run $   cost of the 6 calls at list price, ideal prefix cache

Tokens are chars / 3.7 of the compact JSON on the wire (the estimate the cost reports use): deterministic, no model
needed, and comparable between harnesses. budgets are in small_budgets.json (limit = measured + 10%).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = Path(os.environ["MH_BENCH_APP"]) if os.environ.get("MH_BENCH_APP") else HERE.parents[1] / "app"
sys.path.insert(0, str(HERE))

from small_fake import FakeServer, est_tokens, request_text_chars  # noqa: E402

PRICES = {"in": 0.15, "cached": 0.003, "out": 0.60}
LANES = ("code", "work", "chat", "data")
RECORDED = json.loads((HERE / "small_recorded.json").read_text(encoding="utf-8"))
BUDGETS_PATH = HERE / "small_budgets.json"


def write_files(wd: str) -> None:
    for rel, body in RECORDED["files"].items():
        p = Path(wd) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


def _clean_env(home: str, base: str) -> dict:
    """The child's environment: the shipped defaults (no MH_* settings of this shell leak in), the fake endpoint."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MH_", "MESHPOOL_"))}
    env.update(MESHPOOL_HOME=home, MESHPOOL_BASE_URL=base, MESHPOOL_API_KEY="sk-offline-" + "x" * 24,
               MH_MODEL="deepseek-v4.1-flash", MH_FAILURES_LOG="off", MH_LEARN="0", MH_RECOVER_MIN="0",
               MH_ROUTING_LOG="0", MH_FETCH_LOGOS="0", MH_SLOTS_DIR=os.path.join(home, "slots"),
               MH_CLASSIFY_TIMEOUT="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    return env


def child(lane: str, base: str, out: str) -> None:
    """In the child process: run the real agent on the lane's recorded task."""
    sys.path.insert(0, str(APP))
    from meshpool_harness import run_task
    spec = RECORDED["lanes"][lane]
    wd = tempfile.mkdtemp(prefix=f"mhsmall-{lane}-")
    write_files(wd)
    try:
        res = run_task(spec["prompt"], workdir=wd, engine=spec["engine"], max_steps=12)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    Path(out).write_text(json.dumps({"route": res.get("route"), "profile": res.get("profile"), "ok": res.get("ok"),
                                     "error": res.get("error"), "answer": (res.get("answer") or "")[:200]}))


def summarize(reqs: list[dict], prices: dict = PRICES) -> dict:
    """Per-call overhead numbers from the captured requests of one conversation."""
    main = [r for r in reqs if r["tools"]]
    side = [r for r in reqs if not r["tools"]]
    if not main:
        return {"calls": 0, "side_calls": len(side), "error": "no request with tools reached the fake server"}
    per = [request_text_chars(r["body"]) for r in main]
    totals = [est_tokens(c["total"]) for c in per]
    first = per[0]
    fixed = est_tokens(first["system"] + first["tools"])
    usd, prev = 0.0, 0
    for r, t in zip(main, totals):                       # ideal prefix cache: the previous request's prompt is cached
        fresh, cached = max(0, t - prev), min(prev, t)
        usd += (fresh * prices["in"] + cached * prices["cached"] + r["usage"]["completion"] * prices["out"]) / 1e6
        prev = t
    return {"calls": len(main), "side_calls": len(side), "side_tokens": sum(est_tokens(request_text_chars(r["body"])["total"]) for r in side),
            "system_tokens": est_tokens(first["system"]), "tools_tokens": est_tokens(first["tools"]),
            "tool_count": len(main[0]["tools"]), "fixed_tokens": fixed, "call1_tokens": totals[0],
            "calln_tokens": totals[-1], "per_call_tokens": totals,
            "growth_per_call": round((totals[-1] - totals[0]) / max(1, len(totals) - 1), 1),
            "run_usd": round(usd, 6)}


def measure_meshpool(lanes=LANES, timeout: float = 90.0) -> dict:
    """lane -> summary, one child process per lane, all lanes against one fake server (a conversation each)."""
    fake = FakeServer(steps=RECORDED["steps"], final=RECORDED["final"]).start()
    homes = {l: tempfile.mkdtemp(prefix=f"mhsmall-home-{l}-") for l in lanes}
    outs = {l: os.path.join(homes[l], "result.json") for l in lanes}
    procs, results = {}, {}
    try:
        for l in lanes:
            procs[l] = subprocess.Popen([sys.executable, "-I", str(HERE / "small_offline.py"), "--child", l,
                                         fake.base_for(l), outs[l]], env=_clean_env(homes[l], fake.base_for(l)),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for l, p in procs.items():
            try:
                _, err = p.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                _, err = p.communicate()
                err = "timeout\n" + (err or "")
            s = summarize(fake.of(l))
            try:
                meta = json.loads(Path(outs[l]).read_text())
            except (OSError, ValueError):
                meta = {"error": "child crashed: " + (err or "")[-300:]}
            s.update(route=meta.get("route"), profile=meta.get("profile"), ok=meta.get("ok"))
            if meta.get("error") and not s.get("error"):
                s["agent_error"] = meta["error"]
            results[l] = s
    finally:
        fake.close()
        for h in homes.values():
            shutil.rmtree(h, ignore_errors=True)
    return results


def measure_codex(timeout: float = 120.0) -> dict | None:
    """The Codex CLI against the same script. None when Codex is not installed."""
    import small_codex as CX
    cmd = CX.find_codex()
    if not cmd or cmd[0] == "npx":                       # offline mode never downloads anything
        return None
    fake = FakeServer(steps=RECORDED["steps"], final=RECORDED["final"]).start()
    wd = tempfile.mkdtemp(prefix="mhsmall-codex-")
    write_files(wd)
    try:
        r = CX.run(cmd, RECORDED["codex"]["prompt"], wd, fake.base_for("codex"), "deepseek-v4.1-flash", timeout)
        s = summarize(fake.of("codex"))
        s.update(version=CX.version(cmd), ok=bool(r["answer"]), agent_error=r["error"])
        return s
    finally:
        fake.close()
        shutil.rmtree(wd, ignore_errors=True)


def load_budgets() -> dict:
    return json.loads(BUDGETS_PATH.read_text()) if BUDGETS_PATH.exists() else {}


def check_budgets(results: dict, budgets: dict | None = None) -> list[str]:
    """Human-readable failures (empty list: all budgets hold)."""
    budgets = load_budgets() if budgets is None else budgets
    bad = []
    for lane, b in (budgets.get("lanes") or {}).items():
        r = results.get(lane)
        if not r or not r.get("calls"):
            bad.append(f"{lane}: no measurement ({(r or {}).get('error') or (r or {}).get('agent_error') or 'missing'})")
            continue
        if r["fixed_tokens"] > b["fixed_tokens_limit"]:
            bad.append(f"{lane}: fixed overhead {r['fixed_tokens']} tokens/call > limit {b['fixed_tokens_limit']} "
                       f"(was {b['measured_fixed_tokens']} when the limit was set)")
        if r["growth_per_call"] > b["growth_per_call_limit"]:
            bad.append(f"{lane}: context growth {r['growth_per_call']} tokens/call > limit {b['growth_per_call_limit']}")
        if r["calls"] != len(RECORDED["steps"]) + 1:
            bad.append(f"{lane}: {r['calls']} model calls for the scripted 6-step task (expected 6)")
    return bad


def render(results: dict, codex: dict | None) -> str:
    rows = [("lane", "profile", "tools", "system", "tools tok", "fixed", "call1", "callN", "growth", "calls", "side", "run $")]
    for l in LANES:
        r = results.get(l) or {}
        if not r.get("calls"):
            rows.append((l, "-", "-", "-", "-", "-", "-", "-", "-", "0", str(r.get("side_calls", 0)), "-"))
            continue
        rows.append((l, str(r.get("profile") or r.get("route") or "-"), str(r["tool_count"]), str(r["system_tokens"]),
                     str(r["tools_tokens"]), str(r["fixed_tokens"]), str(r["call1_tokens"]), str(r["calln_tokens"]),
                     f"{r['growth_per_call']:+.0f}", str(r["calls"]), str(r["side_calls"]), f"${r['run_usd']:.4f}"))
    if codex and codex.get("calls"):
        rows.append(("codex", (codex.get("version") or "").split()[-1], str(codex["tool_count"]), str(codex["system_tokens"]),
                     str(codex["tools_tokens"]), str(codex["fixed_tokens"]), str(codex["call1_tokens"]),
                     str(codex["calln_tokens"]), f"{codex['growth_per_call']:+.0f}", str(codex["calls"]),
                     str(codex["side_calls"]), f"${codex['run_usd']:.4f}"))
    w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = ["  ".join(c.ljust(w[i]) if i < 2 else c.rjust(w[i]) for i, c in enumerate(r)) for r in rows]
    out.insert(1, "  ".join("-" * x for x in w))
    return "\n".join(out)


def run_offline(codex: bool = True, lanes=LANES) -> tuple[dict, dict | None, list[str]]:
    t0 = time.time()
    res = measure_meshpool(lanes)
    cx = measure_codex() if codex else None
    print(f"OFFLINE overhead per model call, scripted 6-step task, tokens = chars/3.7 ({time.time() - t0:.1f}s, no network)")
    print(render(res, cx))
    notes = []
    code = res.get("code") or {}
    if cx and cx.get("calls") and code.get("calls"):
        r = code["fixed_tokens"] / max(1, cx["fixed_tokens"])
        notes.append(f"code lane fixed overhead is {r:.2f}x Codex's ({code['fixed_tokens']} vs {cx['fixed_tokens']} tokens per call)")
    elif codex and not cx:
        notes.append("Codex CLI not installed: its prompt was not measured (set CODEX_BIN or npm i -g @openai/codex)")
    elif cx and not cx.get("calls"):
        notes.append(f"Codex run produced no measurable request: {cx.get('error') or cx.get('agent_error')}")
    for l, r in res.items():
        if r.get("agent_error"):
            notes.append(f"{l}: agent reported: {r['agent_error'][:120]}")
    bad = check_budgets(res)
    for n in notes:
        print("note:", n)
    if bad:
        for b in bad:
            print("BUDGET FAIL:", b)
    else:
        print(f"BUDGETS OK ({', '.join(sorted((load_budgets().get('lanes') or {})))} within limit, see small_budgets.json)")
    return res, cx, bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", nargs=3, metavar=("LANE", "BASE", "OUT"))
    ap.add_argument("--no-codex", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--write-budgets", action="store_true", help="set limits to the measured value + 10%%")
    a = ap.parse_args()
    if a.child:
        child(*a.child)
        sys.exit(0)
    res, cx, bad = run_offline(codex=not a.no_codex)
    if a.write_budgets:
        b = {"_doc": "Per-call overhead budgets for the offline check: limit = measured value + 10%. Re-measure with "
                     "`python testkit/loop/small_offline.py --write-budgets` when a change of prompt or tools is intended.",
             "set_at": time.strftime("%Y-%m-%d", time.gmtime()), "token_estimate": "chars/3.7",
             "lanes": {l: {"measured_fixed_tokens": r["fixed_tokens"], "fixed_tokens_limit": int(r["fixed_tokens"] * 1.10),
                           "measured_growth_per_call": r["growth_per_call"],
                           "growth_per_call_limit": int(max(r["growth_per_call"], 1) * 1.10) + 1}
                       for l, r in res.items() if r.get("calls")}}
        if cx and cx.get("calls"):
            b["codex_reference"] = {"fixed_tokens": cx["fixed_tokens"], "version": cx.get("version")}
        BUDGETS_PATH.write_text(json.dumps(b, indent=1))
        print("wrote", BUDGETS_PATH)
    if a.json:
        Path(a.json).write_text(json.dumps({"meshpool": res, "codex": cx}, indent=1))
    sys.exit(1 if bad else 0)
