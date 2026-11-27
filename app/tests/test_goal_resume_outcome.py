"""Goal and auto-resume read how a turn ENDED, not whether any error event appeared in it (Pi reports an error and then
retries by itself). No network."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.ui import agent_api as A  # noqa: E402

BUSY = {"type": "error", "text": "The model is busy right now (rate limited). (HTTP 429: limit reached)"}
RECOVERED = [BUSY, {"type": "text_delta", "text": "..."}, {"type": "assistant", "text": "Here is the answer."}]


def _session(goal=""):
    s = A.Session.__new__(A.Session)
    s.events, s.lock, s.goal, s.id, s.queue, s._stop_req = [], threading.Lock(), goal, "t-outcome", [], False
    s.emit = lambda ev: s.events.append(ev)
    s.agent = type("Ag", (), {"_stop": False})()
    s._goal_idle = 0
    return s


def _turn(s, *extra):
    n = len(s.events)
    s.events += [{"type": "user", "text": "go"}, *extra, {"type": "turn_end"}]
    return n


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(A, "RESUME_WAIT_S", 0.01)
    monkeypatch.setattr(A, "GOAL_NET_WAIT_S", 0.01)
    monkeypatch.setattr(A, "write_meta", lambda *a, **k: None)


def test_recovered_error_does_not_auto_resume():
    s = _session()
    assert s._resume_next(_turn(s, *RECOVERED)) is None
    assert not any("unavailable" in (e.get("text") or "") for e in s.events)


def test_turn_ending_on_net_error_still_resumes():
    s = _session()
    assert s._resume_next(_turn(s, {"type": "assistant", "text": "partial"}, BUSY)) == (A.RESUME_NEXT, [])


def test_goal_done_after_recovered_error():
    # 0.0.20: "GOAL: DONE" asks for the independent check; the check (faked here: all met) ends the goal
    s = _session("ship it")
    s.goal_checker = lambda s_, st, why: {"items": [{"id": c["id"], "status": "met", "evidence": "seen"}
                                                    for c in st["rubric"]], "gates": []}
    n = _turn(s, BUSY, {"type": "tool_end"}, {"type": "assistant", "text": "All set.\nGOAL: DONE"})
    assert s._goal_next(n) is None and s.goal == ""
    assert s.events[-1]["text"].startswith("Met: 10/10")


def test_goal_done_is_not_trusted_after_an_unrecovered_error():
    s = _session("ship it")
    n = _turn(s, {"type": "assistant", "text": "GOAL: DONE"}, {"type": "error", "text": "Something broke."})
    assert s._goal_next(n) is None and s.goal == "ship it"
    assert "an error stopped the turn" in s.events[-1]["text"]


def test_goal_continue_after_recovered_error_continues():
    s = _session("ship it")
    n = _turn(s, BUSY, {"type": "tool_end"}, {"type": "assistant", "text": "More to do.\nGOAL: CONTINUE"})
    assert s._goal_next(n) == (A.GOAL_NEXT, []) and s.goal == "ship it"


def test_goal_turn_ending_on_net_error_pauses_for_the_outage():
    s = _session("ship it")
    n = _turn(s, {"type": "tool_end"}, BUSY)
    assert s._goal_next(n) == (A.GOAL_NEXT, [])
    assert "model connection failed" in s.events[-1]["text"]


def test_turn_outcome_reads_the_ending():
    o = A._turn_outcome([BUSY, {"type": "tool_start"}])
    assert o["error"] is None and not o["net"]
    o = A._turn_outcome([{"type": "assistant", "text": "x"}, BUSY, {"type": "assistant", "text": " "}])
    assert o["error"] is BUSY and o["net"] and o["reply"] == " "
