"""M29-03 / M29-04: the outcome log judges each turn on its own events, and undo, retry and thumbs hit one turn."""
import json
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from meshpool_harness import config as C
from meshpool_harness.hrouter import outcomes as OUT


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    yield tmp_path


def _fake(events, chat="c1"):
    agent = SimpleNamespace(usage={"in": 10, "cached": 0, "out": 5, "calls": 1}, model="m")
    return SimpleNamespace(agent=agent, events=events, id=chat, harness={"id": "meshpool-work"},
                           t_turn=time.time(), auto_reason="")


def _rows():
    return [json.loads(x) for x in OUT.path().read_text().splitlines()]


def _row(chat, turn, ts, **kw):
    r = {"ts": ts, "chat": chat, "turn": turn, "spec": "chat", "harness": "h", "secs": 1, "tokens_out": 1,
         "error": False, "stopped": False}
    r.update(kw)
    OUT._append(r)


def _mark(chat, outcome, turn, ts):
    OUT._append({"ts": ts, "chat": chat, "outcome": outcome, **({"turn": turn} if turn is not None else {})})


def _fail(key="chat|h"):
    return OUT.refit()[key]["fail_rate"]


def test_good_turn_after_failed_turn_is_not_failed():
    evs = [{"type": "user"}, {"type": "error"}, {"type": "turn_end"}, {"type": "user"}, {"type": "assistant"},
           {"type": "turn_end"}]
    OUT.log_turn(_fake(evs), None, {}, ev0=3, turn=2)
    assert _rows()[-1]["error"] is False and _rows()[-1]["turn"] == 2
    evs2 = evs + [{"type": "user"}, {"type": "error"}, {"type": "turn_end"}]
    OUT.log_turn(_fake(evs2), None, {}, ev0=6, turn=3)
    assert _rows()[-1]["error"] is True
    OUT.log_turn(_fake([{"type": "user"}, {"type": "interrupted"}]), None, {}, ev0=0, turn=1)
    assert _rows()[-1]["stopped"] is True


def test_undo_marks_turn_failed(home, monkeypatch):
    from meshpool_harness.ui import agent_api as A
    sessions = home / "mh" / "runs" / "sessions"
    monkeypatch.setitem(A.Agent.__init__.__globals__, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(A.Session, "_start_mcp", lambda self: self._mcp_ready.set())
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    proj = home / "proj"
    proj.mkdir()
    s = A.Session(workdir=str(proj), harness="meshpool-code", kind="code")
    s.cp = SimpleNamespace(revert_to_before=lambda n: {"restored": [], "deleted": []}, turns=lambda: [])
    s.state = "idle"
    s.events = [{"type": "user", "i": 0}, {"type": "assistant", "i": 1}, {"type": "user", "i": 2},
                {"type": "assistant", "i": 3}]
    OUT.log_turn(_fake(s.events[:2], s.id), None, {}, ev0=0, turn=1)
    OUT.log_turn(_fake(s.events, s.id), None, {}, ev0=2, turn=2)
    s.revert(2)
    undo = [r for r in _rows() if r.get("outcome") == "undo"]
    assert [r["turn"] for r in undo] == [2] and undo[0]["chat"] == s.id
    assert OUT.refit()["None|meshpool-work"]["fail_rate"] == 0.5


def test_retry_mark_only_fails_that_turn():
    _row("c1", 1, 1.0)
    _row("c1", 2, 2.0)
    _mark("c1", "retry", 2, 3.0)
    assert _fail() == 0.5


def test_retry_after_undo_counts_the_turn_once():
    _row("c1", 1, 1.0)
    _row("c1", 2, 2.0)
    _mark("c1", "undo", 2, 3.0)
    _mark("c1", "retry", 2, 3.1)
    assert _fail() == 0.5


def test_a_turn_logged_after_an_undo_is_not_failed_by_it():
    _row("c1", 1, 1.0)
    _row("c1", 2, 2.0)
    _mark("c1", "undo", 2, 3.0)
    _row("c1", 2, 4.0)                    # the redone turn reuses the number
    assert _fail() == round(1 / 3, 3)


def test_thumbs_latest_wins():
    _row("c1", 1, 1.0)
    _mark("c1", "down", 1, 2.0)
    assert _fail() == 1.0
    _mark("c1", "up", 1, 3.0)
    assert _fail() == 0.0


def test_mark_without_turn_hits_latest_turn_only():
    _row("c1", 1, 1.0)
    _row("c1", 2, 2.0)
    _mark("c1", "down", None, 3.0)
    assert _fail() == 0.5
    _row("c2", 1, 4.0)
    assert _fail() == round(1 / 3, 3)     # another chat is untouched


def test_feedback_endpoint_passes_turn(monkeypatch):
    from meshpool_harness.ui import agent_api as API
    from meshpool_harness.ui import receipt_api as RA
    monkeypatch.setattr(API, "exists", lambda sid: sid == "c9")    # the endpoint answers only for a real chat
    body = json.dumps({"chat": "c9", "outcome": "down", "turn": 3}).encode()
    assert RA.handle("POST", urlparse("/api/receipt/feedback"), body)[0] == 200
    m = _rows()[-1]
    assert m["chat"] == "c9" and m["outcome"] == "down" and m["turn"] == 3
    RA.handle("POST", urlparse("/api/receipt/feedback"), json.dumps({"chat": "c9", "outcome": "up"}).encode())
    assert "turn" not in _rows()[-1]


def test_rotation_keeps_the_newest_half(monkeypatch):
    monkeypatch.setattr(OUT, "MAX_BYTES", 600)
    for i in range(30):
        _row("c1", i, float(i))
    rs = _rows()
    assert 0 < len(rs) < 30 and rs[-1]["turn"] == 29
