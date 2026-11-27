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



# ---------- the rubric lock: the bar and the budgets are in the hash, and the app keeps the hash in memory ------------
def _rewrite(sdir, **change):
    """What an agent with file tools can do: change goal.json and write a matching hash into it."""
    raw = json.loads((sdir / "goal.json").read_text())
    for k, v in change.items():
        if k == "text":
            raw["rubric"][0]["text"] = v
        elif k in G.CONTRACT_BUDGETS:
            raw["budgets"][k] = v
        else:
            raw[k] = v
    raw["rubric_hash"] = G.contract_hash(raw)
    (sdir / "goal.json").write_text(json.dumps(raw))


def test_the_bar_and_the_budgets_are_part_of_the_hash():
    st = _st()
    h = st["rubric_hash"]
    assert h == G.contract_hash(st)
    for k, v in (("bar", 6.0), ("turns", 500), ("usd", 99.0), ("hours", 100.0), ("checks", 150)):
        other = json.loads(json.dumps(st))
        (other["budgets"] if k != "bar" else other)[k] = v
        assert G.contract_hash(other) != h, k


@pytest.mark.parametrize("change", [{"text": "Anything is fine"}, {"bar": 3.0}, {"turns": 900}])
def test_a_goal_json_rewritten_with_a_matching_hash_is_caught(tmp_path, change):
    st = _st()
    G.save(tmp_path, st)
    G.remember(tmp_path, st)                                   # the rubric froze in this process
    assert not G.disk_changed(tmp_path) and not G.load(tmp_path).get("tampered")
    _rewrite(tmp_path, **change)
    assert G.disk_changed(tmp_path)
    got = G.load(tmp_path)
    assert got["tampered"] and got["status"] == "paused" and "outside meshpool" in got["reason"]
    G.forget(tmp_path)


def test_after_a_restart_the_first_load_sets_the_kept_hash(tmp_path):
    G.save(tmp_path, _st())
    G.forget(tmp_path)                                         # a new process: nothing kept yet
    assert not G.load(tmp_path).get("tampered")               # the first load takes the file's hash (the limit)
    _rewrite(tmp_path, bar=2.0)
    assert G.load(tmp_path)["tampered"] and G.disk_changed(tmp_path)
    G.forget(tmp_path)


def test_a_goal_json_rewritten_during_a_turn_pauses_the_goal_without_a_check(chat, monkeypatch):
    s = chat()
    s.gs, s.goal, s._goal_turn = _st(turns=1), "ship the report", True
    s._goal_save()                                             # frozen in this process: its hash is kept
    _rewrite(chat.sessions / s.id, bar=1.0)
    monkeypatch.setattr(s, "_goal_outcome", lambda evs: dict(_out(ready=True), changed=False, word="READY"))
    checks = []
    s.goal_checker = lambda s_, st, why: checks.append(why) or {}
    assert s._goal_next(len(s.events)) is None
    assert checks == [] and s.gs["status"] == "paused" and "outside meshpool" in s.gs["reason"]
    assert s.gs["bar"] == 10.0                                 # the app's own contract, never the rewritten one
    with pytest.raises(ValueError):
        s.goal_action("resume")


def test_resume_with_a_raised_budget_keeps_the_lock(chat, monkeypatch):
    s = chat()
    s.gs, s.goal = _st(turns=30, status="stopped", reason="budget", budget_hit="turns"), "ship the report"
    s._goal_save()
    monkeypatch.setenv("MH_GOAL_TURNS", "40")                  # the user raised the turn budget in Settings
    monkeypatch.setattr(s, "_goal_kick", lambda: False)
    s.goal_action("resume")
    sdir = chat.sessions / s.id
    assert s.gs["budgets"]["turns"] == 40 and not G.disk_changed(sdir) and not G.load(sdir).get("tampered")
