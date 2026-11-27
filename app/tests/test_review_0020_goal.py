"""Independent review of 0.0.20, goal mode: each test failed on the release candidate (942007e5) and passes
with its fix. Fake models only."""
from __future__ import annotations

import json

import pytest

from meshpool_harness.agent import goal as G
from meshpool_harness.ui import agent_api as A
from tests.test_goal_mode import _out, _st, chat  # noqa: F401  (chat: a real Session on a fake model)


# ---------- goal mode ---------------------------------------------------------------------------------------------------
def test_an_outage_never_runs_a_goal_past_its_budget():
    st = _st(turns=30)
    d = G.decide(st, _out(error="net"))
    assert d["action"] == "stopped" and d["budget"] == "turns", d
    assert G.decide(_st(turns=3), _out(error="net"))["action"] == "wait"          # inside the budget: waited out


def test_the_session_stops_a_goal_at_its_budget_during_an_outage(chat, monkeypatch):
    s = chat()
    s.gs, s.goal, s._goal_turn = _st(turns=29), "ship the report", True
    monkeypatch.setattr(s, "_goal_outcome", lambda evs: dict(_out(error="net"), changed=False, word=""))
    paused = []
    monkeypatch.setattr(s, "_pause", lambda *a, **k: paused.append(a) or ("again", []))
    assert s._goal_next(len(s.events)) is None and paused == []
    assert s.gs["status"] == "stopped" and s.gs["reason"] == "budget" and s.gs["turns"] == 30


def test_stop_on_a_turn_that_started_before_the_goal_pauses_the_goal(chat):
    s = chat()
    s.gs, s.goal = _st(turns=0), "ship the report"
    s._goal_turn, s._stop_req = False, True
    assert s._turn_epilogue(s.workdir, len(s.events), False) is None
    assert s.gs["status"] == "paused" and s.gs["reason"] == "you stopped it" and s.gs["turns"] == 0


def test_cancel_only_cancels_a_countdown(chat):
    s = chat()
    s.gs = _st(status="met")
    with pytest.raises(ValueError):
        s.goal_action("cancel_resume")
    assert s.gs["status"] == "met"


@pytest.mark.parametrize("bad", [
    {"goal": "x", "rubric": [1, 2]},
    {"goal": "x", "rubric": "c1"},
    {"goal": "x", "budgets": [], "rubric": []},
    {"goal": "x", "history": [3], "rubric": []},
    {"goal": "x", "turns": "many", "rubric": []},
    {"goal": ["x"]},
])
def test_a_damaged_goal_file_is_no_goal_and_never_a_crash(tmp_path, bad):
    (tmp_path / "goal.json").write_text(json.dumps(bad))
    assert G.load(tmp_path) is None


def test_resume_goals_skips_a_damaged_file_and_opens_the_rest(chat, monkeypatch):
    s, other = chat(), chat()
    G.save(chat.sessions / s.id, _st())
    (chat.sessions / other.id / "goal.json").write_text(json.dumps({"goal": "x", "status": "working", "rubric": [7]}))
    opened = []
    monkeypatch.setattr(A, "get", lambda sid, no_mcp=False: opened.append(sid))
    assert A.resume_goals() == [s.id] and opened == [s.id]


def test_a_rubric_changed_on_disk_pauses_the_goal_before_any_turn(tmp_path):
    st = _st()
    G.save(tmp_path, st)
    raw = json.loads((tmp_path / "goal.json").read_text())
    raw["rubric"][0]["text"] = "anything at all"
    (tmp_path / "goal.json").write_text(json.dumps(raw))
    got = G.load(tmp_path)
    assert got["tampered"] and got["status"] == "paused" and "outside meshpool" in got["reason"]
