"""Safe entry point for apps that embed the harness.

`run_task` never raises for a failed task. Gateway errors, model errors, tool crashes and bad input all come back as
{"ok": False, "error": "..."}, so a bad network moment or a broken task cannot take the host app down. It does not
call sys.exit, and it writes only inside `workdir` (plus a temporary session folder it removes) unless the app sets MH_TIMING_LOG or
MH_USAGE_LOG.

    from meshpool_harness import run_task
    result = run_task("Fix the failing test in tests/test_api.py", workdir="/path/to/repo")
    if not result["ok"]:
        show(result["error"])

Notes for app authors:
- Settings are environment variables and are process-wide. Run tasks that need different settings in separate
  processes.
- The browser engine starts a Chromium daemon (engines/browser/ctl.py) that outlives the call. Stop it with
  `python -m engines.browser.ctl stop` when the app shuts down.
- KeyboardInterrupt and SystemExit are not swallowed, so the app can still shut down normally.
"""
from __future__ import annotations

import importlib
import os
import time
import traceback

ENGINES = ("code", "browser", "data", "sheets", "slides", "forge", "agent", "agent-work")


def run_task(task: str, *, workdir: str, engine: str = "code", task_id: str | None = None,
             client=None, **kw) -> dict:
    """Run one task with the chosen engine and always return a dict: ok, answer, reason, turns, usage, error."""
    t0 = time.time()
    base = {"ok": False, "answer": "", "reason": "error", "turns": 0, "usage": {}, "error": None, "wall_s": 0.0}
    try:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task text is empty")
        if engine not in ENGINES:
            raise ValueError(f"unknown engine {engine!r}; choose one of {', '.join(ENGINES)}")
        if not workdir or not os.path.isdir(workdir):
            raise ValueError(f"workdir does not exist: {workdir!r}")
        if engine in ("agent", "agent-work"):           # the app's own chat agent, headless (what users run)
            out = _run_agent(task, workdir, "work" if engine == "agent-work" else "code", **kw)
        else:
            mod = importlib.import_module(f"engines.{engine}.engine")
            out = mod.solve(task, workdir=workdir, task_id=task_id or f"app-{int(t0)}", client=client, **kw)
        res = dict(base, **(out if isinstance(out, dict) else {"answer": str(out)}))
        res["ok"] = bool(res.get("ok"))
        res["error"] = out.get("error") if engine in ("agent", "agent-work") and isinstance(out, dict) else None
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:                          # noqa: BLE001 - the whole point: never crash the host app
        res = dict(base, error=f"{type(e).__name__}: {e}",
                   detail="".join(traceback.format_exception(type(e), e, e.__traceback__))[-2000:])
    res["wall_s"] = round(time.time() - t0, 1)
    return res


def _run_agent(task: str, workdir: str, profile: str, max_steps: int = 60, model: str | None = None, **_) -> dict:
    """Run the app's chat agent (agent/core.py) on one task with no person in the loop: every tool is allowed
    (permission mode "auto"), no MCP servers. Its session files go to a temporary folder that is removed afterwards
    (MH_RUN_SESSIONS keeps them in that folder instead): inside the project, the agent found and read its own
    session.json and commands log, three or four wasted calls per task in the loop's traces. The result has the same
    keys as the engines, so benchmarks can score the agent users actually run."""
    import shutil
    import tempfile
    keep = os.environ.get("MH_RUN_SESSIONS")
    sessions = keep or tempfile.mkdtemp(prefix="mh-run-")       # removed below: no stray files anywhere
    try:
        return _run_agent_in(task, workdir, profile, max_steps, model, sessions)
    finally:
        if not keep:
            shutil.rmtree(sessions, ignore_errors=True)


def _run_agent_in(task: str, workdir: str, profile: str, max_steps: int, model: str | None, sessions: str) -> dict:
    from meshpool_harness.agent.core import Agent
    events: list = []
    a = Agent(workdir, mode="auto", max_steps=max_steps, profile=profile, emit=events.append, sessions_dir=sessions)
    if model:
        a.model = model
    route = None
    from meshpool_harness.hrouter.route import _keys
    if os.environ.get("MH_HROUTER", "1") != "0":   # the specialised harness, as the app picks it (0: the base agent)
        from meshpool_harness import hrouter as HRR
        from meshpool_harness import pace as PACE
        route = HRR.route(task, kind="code" if profile == "code" else "cowork", workdir=workdir)
        a.set_route(route, task)
        a.lane = "chat" if route.lane == "chat" and PACE.classify(task) == "quick" else \
            (route.lane if route.lane != "chat" else "task")
    answer = a.send(task)
    errors = [e.get("text", "") for e in events if e.get("type") == "error"]
    steps = sum(1 for e in events if e.get("type") == "assistant")
    capped = any("Stopped after" in x for x in errors)
    reason = "turn cap" if capped else ("error" if errors else "finished")
    usage = {"prompt_tokens": a.usage.get("in", 0), "cached_tokens": a.usage.get("cached", 0),
             "completion_tokens": a.usage.get("out", 0), "model_calls": a.usage.get("calls", 0),
             **{k: v for k, v in a.usage.items() if k.startswith("full_miss")}}
    out = {"ok": bool(answer.strip()) and not errors, "answer": answer, "reason": reason, "turns": steps,
           "usage": usage, "route": route.spec if route else None,
           "route_keys": _keys(route) if route else [], "profile": route.profile if route else None, "model": a.current_model(),
           "receipt": getattr(a, "last_receipt", ""), "trace": getattr(a, "trace", [])[-120:],
           "plan": a.plan_state()}
    if errors and not capped:
        out["error"] = errors[-1][:500]
    return out
