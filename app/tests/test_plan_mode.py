"""Plan and follow: a long task writes a plan first, is reminded of its current step, and shows the plan.
No network: the model transport is a script."""
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import api  # noqa: E402
from meshpool_harness.agent import core, planning as P  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.hrouter.route import Route  # noqa: E402
from meshpool_harness.ui import agent_api as A  # noqa: E402
from meshpool_harness.ui import workbench as W  # noqa: E402

LONG = ("Build a task tracker CLI:\n1. models.py with a Task dataclass\n2. storage.py that saves to tasks.json\n"
        "3. cli.py with add, list and done commands\n4. tests in test_tracker.py\n5. a README.md with usage")
SHORT = "fix the off-by-one in utils.py"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.delenv("MH_PLAN", raising=False)
    monkeypatch.delenv("MH_PLAN_EVERY", raising=False)
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", "none")   # plan mode itself, not the shipped assignment (plan off)
    monkeypatch.delenv("MH_LEAN_TOOLS", raising=False)


def route(spec="code.build", lane="task", difficulty="normal", family="code", base="code"):
    return Route(spec=spec, family=family, sub=spec.split(".")[-1], lane=lane, difficulty=difficulty, base=base)


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _todo(i, *items):
    return _call(i, "todo_write", {"items": [{"content": c, "status": s} for c, s in items]})


def run(tmp_path, replies, text=LONG, rt=None, plan=False):
    a = Agent(str(tmp_path), mode="auto")
    events, seq, bodies = [], list(replies), []
    a.emit = events.append
    a.plan = plan
    if rt is not None:
        a.set_route(rt, text)

    def fake(base, key, body, stream):
        bodies.append(body)
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake
    out = a.send(text)
    return a, out, events, bodies


def last_user(body):
    return [m for m in body["messages"] if m["role"] == "user"][-1]["content"]


# ---------- deciding ----------------------------------------------------------------------------------------------
def test_short_fix_needs_no_plan():
    assert not P.needed(route("code.debug"), SHORT)
    assert not P.needed(route("code.build"), "add a --verbose flag to the CLI")
    assert not P.needed(route("chat", "chat", family="chat", base="work"), "what is a monad?")
    assert not P.needed(None, "hello")


def test_long_multi_requirement_build_needs_a_plan():
    assert P.needed(route("code.build"), LONG)
    assert P.needed(route("code.refactor"), "refactor a.py and b.py and c.py into one package with tests")
    assert P.needed(route("code.build", lane="deep"), "add search")
    assert P.needed(route("data", difficulty="hard", family="data", base="work"), "analyse it")
    assert P.needed(route("docs", family="docs", base="work"),
                    "Make: - a memo.docx - a deck.pptx - a model.xlsx for the Q3 board pack")
    assert P.needed(None, LONG)


def test_focused_specs_only_plan_for_an_explicit_list():
    assert not P.needed(route("code.debug", difficulty="hard"), "x" * 400)
    assert P.needed(route("code.test"), "write tests:\n- parser\n- lexer\n- emitter")


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("MH_PLAN", "0")
    assert not P.needed(route("code.build", lane="deep"), LONG)
    monkeypatch.setenv("MH_PLAN", "1")
    assert P.needed(route("code.debug"), SHORT)
    assert not P.needed(route("code.debug"), SHORT, plan_mode=True)         # approval mode untouched
    monkeypatch.setenv("MH_PLAN", "auto")
    assert not P.needed(route("code.debug"), SHORT)


# ---------- plan first --------------------------------------------------------------------------------------------
def test_first_call_carries_the_plan_instruction(tmp_path):
    a, out, ev, bodies = run(tmp_path, [{"content": "done"}], rt=route())
    first = last_user(bodies[0])
    assert first.startswith(LONG) and "[Meshpool] This task needs a plan" in first and "todo_write" in first
    assert bodies[0]["messages"][0]["role"] == "system" and "needs a plan" not in bodies[0]["messages"][0]["content"]


def test_short_task_gets_no_instruction_and_no_reminders(tmp_path):
    a, out, ev, bodies = run(tmp_path, [{"content": "", "tool_calls": [_call(i, "glob", {"pattern": "*"})]}
                                        for i in range(8)] + [{"content": "ok"}], text=SHORT, rt=route("code.debug"))
    assert "needs a plan" not in last_user(bodies[0])
    assert not [m for m in a.messages if m["role"] == "user" and "Plan:" in str(m["content"])]


def test_todo_write_kept_on_a_lean_route(tmp_path):
    rt = route("code.test")                                   # a focused spec drops todo_write
    names = lambda b: {t["function"]["name"] for t in b["tools"]}      # noqa: E731
    a, _, _, bodies = run(tmp_path, [{"content": "ok"}], text="write tests for the parser", rt=rt)
    assert "todo_write" not in names(bodies[0])
    a, _, _, bodies = run(tmp_path, [{"content": "ok"}], text="write tests:\n- parser\n- lexer\n- emitter", rt=rt)
    assert "todo_write" in names(bodies[0])


# ---------- follow ------------------------------------------------------------------------------------------------
def test_reminder_after_k_calls_names_the_step(tmp_path):
    plan = _todo(1, ("models", "completed"), ("storage", "in_progress"), ("cli", "pending"))
    calls = [{"content": "", "tool_calls": [_call(i, "glob", {"pattern": "*.py"})]} for i in range(2, 8)]
    a, out, ev, bodies = run(tmp_path, [{"content": "", "tool_calls": [plan]}] + calls + [{"content": "fin"}], rt=route())
    notes = [m["content"] for m in a.messages if m["role"] == "user" and "Update the plan" in str(m["content"])]
    assert len(notes) == 1
    assert "step 2 of 3" in notes[0] and "storage" in notes[0] and "Done so far: models" in notes[0]
    seen_at = [i for i, b in enumerate(bodies) if "Plan: step 2 of 3" in str(b["messages"][-1]["content"])]
    assert seen_at[0] == 7                                     # six tool calls after the plan, before the next call


def test_no_reminder_before_k(tmp_path):
    plan = _todo(1, ("a", "in_progress"), ("b", "pending"))
    calls = [{"content": "", "tool_calls": [_call(i, "glob", {"pattern": "*"})]} for i in range(2, 6)]
    a, *_ = run(tmp_path, [{"content": "", "tool_calls": [plan]}] + calls + [{"content": "x"}, {"content": "y"}],
                rt=route())
    assert not [m for m in a.messages if m["role"] == "user" and "Update the plan" in str(m["content"])]


def test_missing_plan_is_asked_for_once(tmp_path):
    calls = [{"content": "", "tool_calls": [_call(i, "glob", {"pattern": "*"})]} for i in range(1, 6)]
    a, *_ = run(tmp_path, calls + [{"content": "x"}], rt=route())
    asks = [m for m in a.messages if m["role"] == "user" and "No plan yet" in str(m["content"])]
    assert len(asks) == 1


def test_stale_plan_at_finish_gets_the_step_in_the_nudge(tmp_path):
    plan = _todo(1, ("a", "completed"), ("b", "in_progress"))
    a, out, *_ = run(tmp_path, [{"content": "", "tool_calls": [plan]}, {"content": "done"}, {"content": "b not needed"}],
                     rt=route())
    nudge = [m["content"] for m in a.messages if m["role"] == "user" and "not completed" in str(m["content"])]
    assert len(nudge) == 1 and "Plan: step 2 of 2" in nudge[0] and out == "b not needed"


def test_plan_can_be_rewritten(tmp_path):
    p1 = _todo(1, ("a", "in_progress"), ("b", "pending"))
    p2 = _todo(2, ("a", "completed"), ("c", "in_progress"), ("d", "pending"))
    a, out, ev, _ = run(tmp_path, [{"content": "", "tool_calls": [p1]}, {"content": "", "tool_calls": [p2]},
                                   {"content": "", "tool_calls": [_todo(3, ("a", "completed"), ("c", "completed"),
                                                                        ("d", "completed"))]}, {"content": "ok"}],
                        rt=route())
    plans = [e for e in ev if e["type"] == "plan"]
    assert [e["total"] for e in plans] == [2, 3, 3] and plans[1]["current"] == 2 and plans[2]["current"] == 0


# ---------- expose ------------------------------------------------------------------------------------------------
def test_plan_event_content(tmp_path):
    plan = _todo(1, ("models", "completed"), ("storage", "in_progress"), ("cli", "pending"))
    a, out, ev, _ = run(tmp_path, [{"content": "", "tool_calls": [plan]}, {"content": "ok"}, {"content": "ok"}], rt=route())
    e = [x for x in ev if x["type"] == "plan"][0]
    assert e["current"] == 2 and e["total"] == 3 and e["done"] == 1
    assert e["items"] == [{"content": "models", "status": "completed"}, {"content": "storage", "status": "in_progress"},
                          {"content": "cli", "status": "pending"}]
    assert e["entries"] == e["items"]                          # older front ends read entries


def test_current_step_falls_back_to_first_pending():
    assert P.state([{"content": "a", "status": "completed"}, {"content": "b", "status": "pending"},
                    {"content": "c", "status": "pending"}])["current"] == 2


def test_run_task_result_includes_plan(tmp_path, monkeypatch):
    plan = _todo(1, ("a", "completed"), ("b", "in_progress"))
    seq = [{"content": "", "tool_calls": [plan]}, {"content": "finished"}, {"content": "finished"}]

    def post(self, base, key, body, stream):
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    import meshpool_harness.agent.core as core_now           # the class run_task will use, even after a reload
    monkeypatch.setattr(core_now.Agent, "_post", post)
    monkeypatch.setenv("MH_RUN_SESSIONS", str(tmp_path / "s"))
    res = api.run_task(LONG, workdir=str(tmp_path), engine="agent")
    assert res["plan"]["total"] == 2 and res["plan"]["current"] == 2 and res["plan"]["items"][0]["status"] == "completed"
    seq[:] = [{"content": "hi"}]
    assert api.run_task("hello", workdir=str(tmp_path), engine="agent")["plan"] is None


def test_session_panel_has_a_plan_section(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(W, "_MODELS", {"t": 0, "key": "", "rows": [], "error": ""})
    todos = [{"content": "a", "status": "completed"}, {"content": "b", "status": "in_progress"},
             {"content": "c", "status": "pending"}]
    agent = SimpleNamespace(context=None, messages=[], current_model=lambda: "m", _delegations={}, _todos=todos)
    s = SimpleNamespace(id="s1", workdir=str(tmp_path), lock=threading.RLock(), events=[], mcp=None, agent=agent,
                        cp=None, state="idle", scheduled=None, stop=lambda: None)
    A.LIVE["s1"] = s
    try:
        pl = W.session_panel("s1")["plan"]
        assert pl["current"] == 2 and pl["total"] == 3 and pl["done"] == 1
        assert [x["state"] for x in pl["steps"]] == ["done", "current", "pending"]
        agent._todos = []
        s.events = [{"type": "plan", "entries": todos, "t": time.time(), "i": 0}]          # restored session: from events
        assert W.session_panel("s1")["plan"]["total"] == 3
        s.events = []
        assert W.session_panel("s1")["plan"] == {"steps": [], "current": 0, "total": 0, "done": 0}
    finally:
        A.LIVE.pop("s1", None)


# ---------- the approval plan mode is unaffected ------------------------------------------------------------------
def test_approval_plan_mode_unaffected(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_PLAN", "1")                         # even "always" does not touch the approval mode
    a, out, ev, bodies = run(tmp_path, [{"content": "1. step"}], plan=True, rt=route())
    first = last_user(bodies[0])
    assert first.startswith("[Plan mode]") and "needs a plan" not in first
    assert not a._plan_needed and a.plan
    assert {t["function"]["name"] for t in bodies[0]["tools"]} == {t["function"]["name"] for t in a.tool_schemas()}
