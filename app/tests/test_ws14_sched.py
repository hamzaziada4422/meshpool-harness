"""Audit ws14 (scheduled tasks): M18-01 .. M18-11, F-28."""
import json
import multiprocessing as mp
import os
import time
import types
from datetime import datetime, timedelta, timezone

import pytest

from meshpool_harness import assistant as AS
from meshpool_harness import config as C
from meshpool_harness.ui import agent_api as A


def _store():
    return C.home() / "schedules.json"


def _raw(rows):
    _store().write_text(json.dumps(rows))


def _rows():
    return json.loads(_store().read_text())


def _due_now(t, **kw):
    """Make a stored task due right now."""
    rows = _rows()
    for r in rows:
        if isinstance(r, dict) and r["id"] == t["id"]:
            r["next_run"] = time.time() - 5
            r.update(kw)
    _raw(rows)


def _future(days=2):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


# ---- M18-01 (already fixed): an unattended run takes no approval and no answer ---------------------------------------
def test_M18_01_unattended_run_refuses_approval_and_takes_defaults(monkeypatch, tmp_path):
    s = A.Session.__new__(A.Session)
    monkeypatch.setenv("MESHPOOL_HEADLESS", "1")
    decision, why = s.approve("command", "run it", "ls")
    assert decision == "no" and "unattended" in why
    from meshpool_harness.agent import core
    from meshpool_harness.agent.core import Agent
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_s")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    a = Agent(str(tmp_path), mode="ask")
    a.interactive = lambda: False                                      # what a scheduled run's chat sets
    events = []
    a.emit = events.append
    out = a._ask_user({"questions": [{"question": "Which?", "options": [{"label": "A", "recommended": True}, {"label": "B"}]}]})
    assert [e["source"] for e in events if e["type"] == "question_done"] == ["headless"] and "A" in out


# ---- M18-02, M18-07: one run at a time ---------------------------------------------------------------------------------
def test_M18_02_due_skips_a_task_whose_run_is_still_going():
    t = AS.add_task("every", "do it", "every", minutes=15)
    _due_now(t)
    first = AS.due()
    assert [x["id"] for x in first] == [t["id"]] and first[0]["claim"]
    _due_now(t)                                                        # fifteen minutes later, the run still works
    assert AS.due() == []                                              # not started a second time
    assert AS.claim(t["id"]) is None                                   # Run now is refused too (M18-07)
    AS.release(t["id"], first[0]["claim"])                             # the run ended
    _due_now(t)
    assert [x["id"] for x in AS.due()] == [t["id"]]


def test_M18_07_run_now_refused_while_running():
    t = AS.add_task("n", "do it", "daily", time_="09:00")
    tok = AS.claim(t["id"])
    assert tok and AS.claim(t["id"]) is None
    with pytest.raises(ValueError, match="still running"):
        A.run_scheduled(AS.tasks()[0])
    AS.release(t["id"], tok)


def test_M18_02_lease_expires_if_the_run_dies():
    t = AS.add_task("every", "do it", "every", minutes=15)
    tok = AS.claim(t["id"], ttl=-1)                                    # a process that died: its lease ran out
    assert AS.claim(t["id"]) is not None and tok


def _claim_worker(home, tid, barrier, out):
    os.environ["MESHPOOL_HOME"] = home
    from meshpool_harness import assistant as AS2
    barrier.wait(30)
    out.put(bool(AS2.claim(tid)))


def test_M18_02_claim_is_atomic_across_processes():
    t = AS.add_task("x", "p", "daily")
    ctx = mp.get_context("spawn")
    barrier, out = ctx.Barrier(4), ctx.Queue()
    ps = [ctx.Process(target=_claim_worker, args=(str(C.home()), t["id"], barrier, out)) for _ in range(4)]
    [p.start() for p in ps]
    got = [out.get(timeout=60) for _ in ps]
    [p.join(30) for p in ps]
    assert got.count(True) == 1


# ---- M18-03: a run that cannot start is not consumed ---------------------------------------------------------------------
def test_M18_03_failed_start_is_recorded_and_kept(tmp_path):
    t = AS.add_task("brief", "p", "daily", time_="09:00", folder=str(tmp_path / "gone"))
    _due_now(t)
    run = AS.due()[0]
    nxt = AS.tasks()[0]["next_run"]
    with pytest.raises(ValueError):
        A.run_scheduled(run)
    row = AS.tasks()[0]
    assert "moved or deleted" in row["last_error"]["text"]
    assert row["last_run"] is None and row["next_run"] == nxt and row["enabled"] and "lease" not in row
    from meshpool_harness.ui import hub
    assert "moved or deleted" in hub.scheduled()["tasks"][0]["error"]          # shown in the app, not only on the desktop


def test_M18_03_one_time_task_is_retried():
    t = AS.add_task("once", "p", "once", at=_future(1), folder="/definitely/not/here")
    _due_now(t)
    run = AS.due()[0]
    assert AS.tasks()[0]["enabled"] is False
    with pytest.raises(ValueError):
        A.run_scheduled(run)
    row = AS.tasks()[0]
    assert row["enabled"] and row["next_run"] > time.time() and row["last_run"] is None


# ---- M18-04: one bad row does not stop the others ----------------------------------------------------------------------------
def test_M18_04_malformed_row_does_not_break_due():
    good = AS.add_task("good", "p", "every", minutes=15)
    rows = _rows()
    rows.append({"id": "bad1", "name": "b", "prompt": "p", "when": "daily", "time": "99:99", "enabled": True, "next_run": time.time() - 9})
    rows.append({"id": "bad2", "name": "b", "prompt": "p", "when": "daily", "enabled": True, "next_run": "soon"})
    rows.append("not a task")
    _raw(rows)
    _due_now(good)
    got = AS.due()
    assert [x["id"] for x in got] == [good["id"]]
    by = {r["id"]: r for r in _rows() if isinstance(r, dict)}
    assert by["bad1"]["invalid"] and by["bad2"]["invalid"]
    assert "not a task" in _rows()                                     # left alone
    assert AS.due() == [] and AS.describe(by["bad1"]).endswith("needs fixing")


# ---- M18-05: a damaged file is never replaced ---------------------------------------------------------------------------------
def test_M18_05_corrupt_file_is_kept_and_blocks_writes():
    AS.add_task("keep", "p", "daily")
    bad = b'[{"id": "a", "name": "x"'
    _store().write_bytes(bad)
    assert AS.tasks() == [] and "damaged" in AS.tasks_problem()
    with pytest.raises(AS.TasksDamaged):
        AS.add_task("new", "p", "daily")
    assert _store().read_bytes() == bad                                # not replaced
    assert any(p.read_bytes() == bad for p in C.home().glob("schedules.json.corrupt-*"))
    assert AS.due() == []
    AS.reset_tasks()
    assert not _store().exists() and AS.tasks_problem() == ""
    assert AS.add_task("fresh", "p", "daily")["name"] == "fresh"


# ---- M18-06: the person takes over a scheduled chat ------------------------------------------------------------------------
def test_M18_06_own_message_clears_scheduled(monkeypatch):
    s = A.Session.__new__(A.Session)
    s.scheduled, s.state, s.queue, s.touched = {"id": "t1"}, "idle", [], 0
    s.lock, s._turn_id, s._stop_req, s._interrupting = __import__("threading").RLock(), 0, False, None
    s.events, s.pendings, s._retry_open, s._turn_open = [], {}, False, False
    s.changed = __import__("threading").Condition(s.lock)
    s.agent = types.SimpleNamespace()
    s.harness = {"type": "meshpool"}
    sent = []
    monkeypatch.setattr(s, "_start_turn", lambda *a, **k: (sent.append(a), setattr(s, "state", "idle")), raising=False)
    s._system_send = True
    s.send("hello")                                                    # the run's own prompt
    assert s.scheduled
    s._system_send = False
    s.send("my own message")
    assert s.scheduled is None and len(sent) == 2


# ---- M18-08: a late run says so; a task may refuse to run late -------------------------------------------------------------------
def test_M18_08_late_run_is_flagged_and_said(monkeypatch):
    t = AS.add_task("late", "check mail", "every", minutes=15)
    _due_now(t)
    rows = _rows()
    rows[0]["next_run"] = time.time() - 10 * 3600
    _raw(rows)
    run = AS.due()[0]
    assert run["late"] and run["late_s"] > 9 * 3600
    seen = {}

    class S:
        id = "s1"

        def emit(self, ev):
            seen["ev"] = ev

        def send(self, text, **k):
            seen["text"] = text
    monkeypatch.setattr(A, "create", lambda body: {"id": "s1"})
    monkeypatch.setattr(A, "get", lambda sid: S())
    monkeypatch.setattr(A, "write_meta", lambda *a, **k: seen.setdefault("meta", k))
    A.run_scheduled(run)
    assert "hours ago" in seen["ev"]["text"] and "hours ago" in seen["text"] and "(late)" in seen["meta"]["title"]


def test_M18_08_skip_if_missed_by_more_than():
    t = AS.add_task("skipper", "p", "every", minutes=15, skip_if_missed_h=2)
    rows = _rows()
    rows[0]["next_run"] = time.time() - 5 * 3600
    _raw(rows)
    assert AS.due() == []
    row = _rows()[0]
    assert row["next_run"] > time.time() and "more than 2" in row["skipped"]["why"] and row["last_run"] is None
    rows[0]["next_run"] = time.time() - 3600                           # an hour late is inside the limit
    _raw(rows)
    assert [x["id"] for x in AS.due()] == [t["id"]]


# ---- M18-09: a finished one-time task is Done ----------------------------------------------------------------------------------------
def test_M18_09_done_is_not_paused():
    t = AS.add_task("once", "p", "once", at=_future(1))
    _due_now(t)
    AS.due()
    row = AS.tasks()[0]
    assert AS.status(row) == "Done" and "done" in AS.describe(row) and "paused" not in AS.describe(row)
    with pytest.raises(ValueError, match="new date"):
        AS.update_task(t["id"], enabled=True)
    new = AS.update_task(t["id"], enabled=True, at=_future(3))
    assert new["next_run"] and new["next_run"] > time.time() and AS.status(new) == "Active"


# ---- M18-10: hourly and every-N-minutes across a clock change -----------------------------------------------------------------------
@pytest.fixture
def new_york(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    old = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc).timestamp()


def test_M18_10_hourly_at_the_fall_back(new_york):
    after = datetime(2026, 11, 1, 1, 30)                               # 01:30 EDT, the first time round (05:30 UTC)
    assert AS.next_run({"when": "hourly"}, after) == _utc(2026, 11, 1, 6, 0)          # 01:00 EST: the hour is not skipped
    assert AS.next_run({"when": "every", "minutes": 60}, after) == _utc(2026, 11, 1, 6, 30)


def test_M18_10_hourly_at_the_spring_forward(new_york):
    after = datetime(2026, 3, 8, 1, 30)                                # 01:30 EST (06:30 UTC)
    assert AS.next_run({"when": "hourly"}, after) == _utc(2026, 3, 8, 7, 0)           # 03:00 EDT, one hour on the clock
    assert AS.next_run({"when": "every", "minutes": 60}, after) == _utc(2026, 3, 8, 7, 30)
    nxt = AS.next_run({"when": "hourly"}, datetime(2026, 3, 8, 3, 10))
    assert nxt == _utc(2026, 3, 8, 8, 0)                               # then 04:00 EDT, nothing twice
    d = AS.next_run({"when": "daily", "time": "09:00"}, datetime(2026, 3, 8, 8, 0))
    assert d == _utc(2026, 3, 8, 13, 0)                                # 09:00 EDT the same day


def test_M18_10_hourly_is_a_whole_local_hour_normally(new_york):
    after = datetime(2026, 6, 10, 10, 20)
    assert datetime.fromtimestamp(AS.next_run({"when": "hourly"}, after)) == datetime(2026, 6, 10, 11, 0)


# ---- M18-11: delete by id ---------------------------------------------------------------------------------------------------------------
def test_M18_11_delete_by_name_needs_a_unique_name():
    a = AS.add_task("Report", "p1", "daily")
    b = AS.add_task("Report", "p2", "weekly")
    c = AS.add_task("Other", "p3", "daily")
    with pytest.raises(ValueError, match="Say which one"):
        AS.delete_task("Report")
    assert len(AS.tasks()) == 3
    from meshpool_harness.agent import assistant_mcp_server as MCP
    assert "Say which one" in MCP.delete_scheduled_task("report") and len(AS.tasks()) == 3
    assert AS.delete_task(a["id"]) and [t["id"] for t in AS.tasks()] == [b["id"], c["id"]]
    assert AS.delete_task("other") and [t["id"] for t in AS.tasks()] == [b["id"]]
    assert AS.delete_task("nothing") is False


# ---- F-28: a time that has passed -------------------------------------------------------------------------------------------------------
def test_F_28_past_one_time_task_is_refused_in_plain_words():
    past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    with pytest.raises(ValueError) as e:
        AS.add_task("p", "p", "once", at=past)
    assert "already passed" in str(e.value) and "`" not in str(e.value) and AS.tasks() == []
    with pytest.raises(ValueError) as e2:
        AS.add_task("p", "p", "once", at="tomorrow")
    assert "`" not in str(e2.value) and "at" not in str(e2.value).split()[:1]
    t = AS.add_task("p", "p", "once", at=_future(1))
    with pytest.raises(ValueError, match="already passed"):
        AS.update_task(t["id"], at=past)
    for bad in ({"when": "monthly"}, {"minutes": "x"}):
        with pytest.raises(ValueError) as e3:
            AS.update_task(t["id"], **bad)
        assert "must be" not in str(e3.value)
