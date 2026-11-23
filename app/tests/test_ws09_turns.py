"""ws09: the chat turn state machine (Session._claim_turn / _set_state / _end_turn) and what hangs off it.

Real Sessions on a real built-in Agent whose send() is replaced by a fake that blocks on an Event, so every test
controls the timing of a turn. Each test names the audit item it covers and fails on the code before the fix.
"""
import json
import threading
import time

import pytest

from meshpool_harness import config as C


@pytest.fixture
def A(tmp_path, monkeypatch):
    from meshpool_harness.ui import agent_api as api
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    monkeypatch.setenv("MH_AUTO_RESUME", "0")
    for k in C.ENDPOINT_NAMES:                               # turns copy the settings file into os.environ and remember
        monkeypatch.setenv(k, "x")                           # it in config's globals; setenv first so teardown removes
        monkeypatch.delenv(k)                                # whatever a test's turn copied in
    monkeypatch.setattr(C, "_INJECTED", set())               # it in config's globals: each test starts from its own file
    monkeypatch.setattr(C, "SHELL", {})
    (tmp_path / "mh").mkdir()
    sessions = tmp_path / "mh" / "runs" / "sessions"
    monkeypatch.setitem(api.Agent.__init__.__globals__, "SESSIONS", sessions)
    monkeypatch.setattr(api, "SESSIONS", sessions)
    monkeypatch.setattr(api.Session, "_start_mcp", lambda self: self._mcp_ready.set())
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    (tmp_path / "proj").mkdir()
    api.PROJ = tmp_path / "proj"
    return api


def wait_for(fn, timeout=20.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


class Turns:
    """Stands in for agent.send: records every body, emits what a real turn emits, blocks until released.
    modes[i] for the i-th turn: "honour" (default: Stop ends it after `delay` s) or "ignore" (only release ends it)."""

    def __init__(self, s, modes=(), delay=0.0, hold=True, reply="ok", on_send=None):
        self.s, self.sent, self.gates, self.modes = s, [], [], list(modes)
        self.delay, self.hold, self.reply, self.on_send = delay, hold, reply, on_send
        s.agent.send = self.send

    def send(self, body, images=None):
        a = self.s.agent
        a._stop = False
        n = len(self.sent)
        self.sent.append(body)
        gate = threading.Event()
        self.gates.append(gate)
        mode = self.modes[n] if n < len(self.modes) else "honour"
        a.emit({"type": "user", "text": body.split("​")[0]})
        if self.on_send:
            self.on_send(n)
        while self.hold and not gate.is_set():
            if a._stop and mode == "honour":
                time.sleep(self.delay)
                a.emit({"type": "interrupted", "text": "Stopped."})
                break
            time.sleep(0.01)
        a.emit({"type": "assistant", "text": self.reply})
        a.emit({"type": "turn_end", "turn": {}, "session": {}})

    def release(self, n=-1):
        self.gates[n].set()


def mk(A, starting=False, cls=None, **kw):
    s = (cls or A.Session)(workdir=str(A.PROJ), harness="meshpool-code", kind="code")
    s.ctx_pending = False
    if not starting:
        s.state = "idle"
    return s


def idle(s, t=20.0):
    wait_for(lambda: s.state == "idle", t, "the chat to go idle")


# ---- M01-01 -----------------------------------------------------------------------------------------------------
def test_two_concurrent_sends_run_one_turn(A):
    s = mk(A)
    t, steers = Turns(s), []
    s.agent.steer = lambda body, images=None: steers.append(body)
    s._capability_turn = lambda text: (time.sleep(0.4), [])[1]       # the slow wait both sends used to sit in
    bar = threading.Barrier(2)

    def go(x):
        bar.wait()
        s.send(x)
    ths = [threading.Thread(target=go, args=(x,)) for x in ("first", "second")]
    [x.start() for x in ths]
    [x.join(5) for x in ths]
    wait_for(lambda: len(t.sent) >= 1, what="the turn")
    time.sleep(0.6)
    assert len(t.sent) == 1, "two turns ran on one agent"
    assert len(steers) == 1                                          # the loser was added to the running turn
    t.release()
    idle(s)


# ---- M01-03 -----------------------------------------------------------------------------------------------------
def test_epilogue_exception_still_ends_the_turn(A, monkeypatch):
    s = mk(A)
    Turns(s, hold=False)

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(A, "write_meta", full)
    s.send("hello")
    idle(s)                                                          # old code: the thread died, "working" forever
    s.stop()
    try:
        s.revert(1)
    except Exception as e:  # noqa: BLE001  whatever else Undo says, it must not say the agent still runs
        assert "Stop the agent" not in str(e)


# ---- M01-04 -----------------------------------------------------------------------------------------------------
def test_follow_up_refused_by_key_check_goes_idle(A):
    s = mk(A)
    s.goal = "ship it"

    def mask(n):
        if n == 0:                                                   # the key gets masked while the first turn runs
            C.write({"MESHPOOL_API_KEY": "sk-abc••••"})
    t = Turns(s, hold=False, reply="Did part one.\nGOAL: CONTINUE with part two", on_send=mask)
    s.send("do the thing")
    idle(s)                                                          # old code: the follow-up had no thread
    assert len(t.sent) == 1
    assert any(e["type"] == "error" and "masked" in e["text"] for e in s.events)


# ---- M01-05 -----------------------------------------------------------------------------------------------------
def test_early_return_turn_is_not_running_after_restart(A):
    C.write({"MESHPOOL_API_KEY": "sk-abc••••"})
    s = mk(A)
    Turns(s)
    s.send("hello")
    idle(s)
    tr = json.loads((A.SESSIONS / s.id / "transcript.json").read_text(encoding="utf-8"))
    assert tr["running"] is False
    assert any(e["type"] == "turn_end" and e.get("synthetic") for e in tr["events"])
    s.close()
    s2 = A.Session(resume_id=s.id)                                   # a restart: no "closed while this was running"
    assert not any(e.get("restarted") for e in s2.events)


# ---- M01-06 -----------------------------------------------------------------------------------------------------
def test_mcp_start_cannot_overwrite_a_running_turn(A, monkeypatch):
    class FakeManager:
        config_error = ""

        def __init__(self, *a, **k):
            pass

        def start(self, timeout=0):
            return self

        def status(self):
            return []
    monkeypatch.setattr(A.M, "Manager", FakeManager)
    holder = {}

    class Race(A.Session):
        """A turn that starts exactly between the MCP thread's check of the state and its write."""
        _arm = False

        @property
        def state(self):
            v = self.__dict__.get("_st")
            if self._arm and v == "starting" and threading.current_thread().name == "mcp-test":
                self._arm = False
                th = threading.Thread(target=lambda: self.send("hi"))
                th.start()
                holder["turns"].started.wait(0.8)
            return v

        @state.setter
        def state(self, v):
            self.__dict__["_st"] = v
    s = mk(A, starting=True, cls=Race)
    t = holder["turns"] = Turns(s)
    t.started = threading.Event()
    s.agent.send = lambda body, images=None: (t.started.set(), t.send(body, images))[1]
    s._arm = True
    th = threading.Thread(target=s._start_mcp_servers, name="mcp-test")
    th.start()
    th.join(10)
    wait_for(lambda: len(t.sent) == 1, what="the turn")
    assert s.state == "working"                                      # old code: overwritten with "idle"
    t.release()
    idle(s)


# ---- M02-02 -----------------------------------------------------------------------------------------------------
def test_approval_ids_are_unique_across_restarts(A):
    ids = []
    for opener in (lambda: mk(A), None):
        s = opener() if opener else A.Session(resume_id=ids[0][0])
        th = threading.Thread(target=lambda: s.approve("bash", "Run?", "ls"))
        th.start()
        wait_for(lambda: s.pendings, what="the card")
        aid = next(iter(s.pendings))
        ids.append((s.id, aid))
        s.answer(aid, "yes")
        th.join(5)
    assert ids[0][1] != ids[1][1]
    assert all(isinstance(i, str) and i.startswith("ap-") for _, i in ids)


# ---- M02-03 / M07-01 / P-03 -----------------------------------------------------------------------------------------
def test_resume_closes_open_approval_question_and_steps(A):
    s = mk(A)
    sid = s.id
    s.close()
    evs = [{"type": "user", "text": "go"},
           {"type": "tool_start", "id": "t1", "name": "bash", "summary": "ls"},
           {"type": "approval", "id": 1, "kind": "bash", "title": "Run?", "detail": "ls", "options": None},
           {"type": "question", "id": "q1", "questions": [{"question": "Which?"}], "timeout_s": 60},
           {"type": "question", "id": "q0", "questions": [{"question": "Old?"}], "timeout_s": 0},
           {"type": "tool_start", "id": "t2", "name": "bash", "summary": "pwd"},
           {"type": "tool_end", "id": "t2", "name": "bash", "ok": True, "output": "/"}]
    (A.SESSIONS / sid / "transcript.json").write_text(json.dumps({"events": evs, "running": False}), encoding="utf-8")
    s1 = A.Session(resume_id=sid)
    by = lambda t: [e for e in s1.events if e["type"] == t]           # noqa: E731
    assert [(e["id"], e["decision"], e["closed"]) for e in by("approval_done")] == [(1, "stopped", True)]
    assert [(e["id"], e["source"], e["closed"]) for e in by("question_done")] == [("q1", "stopped", True)]
    ends = [e for e in by("tool_end") if e.get("stopped")]
    assert [e["id"] for e in ends] == ["t1"] and ends[0]["ok"] is False
    assert [e.get("synthetic") for e in by("turn_end")] == [True]
    n = len(s1.events)
    s1.close()
    s2 = A.Session(resume_id=sid)                                    # opened again: nothing more to close
    assert len(s2.events) == n
    assert json.loads((A.SESSIONS / sid / "transcript.json").read_text(encoding="utf-8"))["running"] is False


def test_resume_does_not_relabel_steps_of_finished_turns(A):
    s = mk(A)
    sid = s.id
    s.close()
    evs = [{"type": "user", "text": "one"},
           {"type": "tool_start", "id": "t1", "name": "bash", "summary": "ls"},
           {"type": "turn_end"},
           {"type": "user", "text": "two"},
           {"type": "tool_start", "id": "t2", "name": "bash", "summary": "pwd"}]
    (A.SESSIONS / sid / "transcript.json").write_text(json.dumps({"events": evs, "running": False}), encoding="utf-8")
    s1 = A.Session(resume_id=sid)
    ends = [e for e in s1.events if e["type"] == "tool_end"]
    assert [e["id"] for e in ends] == ["t2"] and ends[0].get("stopped")


# ---- M02-05 -----------------------------------------------------------------------------------------------------
def test_allow_in_this_chat_survives_reap_restart_and_handoff(A):
    s = mk(A)
    s.agent.always.add("bash")                                       # what the agent does on "Allow in this chat"
    assert A.read_meta(s.id)["always"] == ["bash"]
    s.close()                                                        # the reaper
    s2 = A.Session(resume_id=s.id)                                   # a restart
    assert "bash" in s2.agent.always
    s2.switch_harness("meshpool-work")                               # a handoff builds a new agent
    assert "bash" in s2.agent.always
    # an ACP harness's card: "Allow always" is remembered per kind and answered without a card next time
    opts = [{"id": "o1", "label": "Allow", "kind": "allow_once"}, {"id": "o2", "label": "Always", "kind": "allow_always"},
            {"id": "o3", "label": "No", "kind": "reject_once"}]
    out = []
    th = threading.Thread(target=lambda: out.append(s2.approve("bash", "Run this command?", "ls -la", opts)))
    th.start()
    wait_for(lambda: s2.pendings, what="the card")
    s2.answer(next(iter(s2.pendings)), "o2")
    th.join(5)
    assert out == [("o2", "")] and "acp:bash" in A.read_meta(s2.id)["always"]
    assert s2.approve("bash", "Run this command?", "git status", opts) == ("o1", "") and not s2.pendings
    s3 = A.Session(resume_id=s2.id)                                  # and after a restart
    assert s3.approve("bash", "Run this command?", "git status", opts) == ("o1", "")
    th = threading.Thread(target=lambda: s3.approve("bash", "Run this command?", "rm -rf /", opts), daemon=True)
    th.start()                                                       # never for a destructive command
    wait_for(lambda: s3.pendings, what="a card for the destructive command")
    s3.stop()
    th.join(5)


def test_acp_allow_always_for_one_tool_does_not_allow_another(A):
    s = mk(A)
    opts = [{"id": "o1", "label": "Allow", "kind": "allow_once"}, {"id": "o2", "label": "Always", "kind": "allow_always"},
            {"id": "o3", "label": "No", "kind": "reject_once"}]
    out = []
    th = threading.Thread(target=lambda: out.append(s.approve("mcp", "Use tool A?", "", opts)))
    th.start()
    wait_for(lambda: s.pendings, what="the card")
    s.answer(next(iter(s.pendings)), "o2")
    th.join(5)
    assert out == [("o2", "")]
    assert s.approve("mcp", "Use tool A?", "", opts) == ("o1", "") and not s.pendings   # the same tool: no card
    th = threading.Thread(target=lambda: s.approve("mcp", "Use tool B?", "", opts), daemon=True)
    th.start()                                                       # another tool: a card
    wait_for(lambda: s.pendings, what="a card for tool B")
    s.stop()
    th.join(5)


# ---- M03-01 -----------------------------------------------------------------------------------------------------
def test_message_sent_right_after_stop_runs_as_its_own_turn(A):
    s = mk(A)
    t = Turns(s, delay=0.5)
    s.send("one")
    wait_for(lambda: len(t.sent) == 1, what="turn one")
    s.stop()
    r = s.send("two")                                                # the agent is still stopping
    assert r == {"queued": True, "after_stop": True}
    wait_for(lambda: len(t.sent) == 2, what="the message to run")    # old code: steered into the stopping turn
    assert t.sent[1].startswith("two") and not s.agent.has_steers()
    t.release()
    idle(s)


# ---- M03-03 -----------------------------------------------------------------------------------------------------
def test_send_now_on_a_slow_stop_still_runs(A):
    s = mk(A)
    t = Turns(s, delay=2.0)                                          # the agent takes 2 s to honour Stop
    s.send("one")
    wait_for(lambda: len(t.sent) == 1, what="turn one")
    t0 = time.time()
    r = s.send("now", interrupt=True)
    assert time.time() - t0 < 1.0 and r == {"queued": True, "after_stop": True}
    wait_for(lambda: len(t.sent) == 2, 8, "the message to run as its own turn")
    assert t.sent[1].startswith("now")
    t.release()
    idle(s)


# ---- M03-04 -----------------------------------------------------------------------------------------------------
def test_interrupt_flag_does_not_leak_to_next_stop(A):
    s = mk(A)
    t = Turns(s, modes=["ignore", "honour"])                         # turn one ends on its own, never "interrupted"
    s.send("one")
    wait_for(lambda: len(t.sent) == 1, what="turn one")
    threading.Timer(0.3, t.release, args=(0,)).start()
    s.send("two", interrupt=True)
    wait_for(lambda: len(t.sent) == 2, what="turn two")
    s.stop()                                                         # an unrelated Stop of the next turn
    idle(s)
    stops = [e for e in s.events if e["type"] == "interrupted"]
    assert [e["text"] for e in stops] == ["Stopped."]                # old code: "Stopped the previous request."


# ---- M08-01 -----------------------------------------------------------------------------------------------------
def test_send_while_starting_waits_for_mcp_then_runs(A, monkeypatch):
    monkeypatch.setattr(A.Session, "_start_mcp", lambda self: None)  # the servers are still starting
    s = mk(A, starting=True)
    t = Turns(s, hold=False)
    assert s.state == "starting" and not s._mcp_ready.is_set()
    assert s.send("hello") == {"queued": False}                      # accepted
    time.sleep(0.6)
    assert t.sent == [] and s.state == "working"                     # but it waits for the tools
    s._mcp_ready.set()
    wait_for(lambda: len(t.sent) == 1, what="the turn to run once the servers are up")
    idle(s)
    # Stop ends the wait: nothing runs, the turn ends cleanly
    s2 = mk(A, starting=True)
    t2 = Turns(s2, hold=False)
    s2.send("hello")
    time.sleep(0.2)
    s2.stop()
    idle(s2)
    assert t2.sent == []
    assert any(e["type"] == "interrupted" for e in s2.events) and s2.events[-1]["type"] == "turn_end"


# ---- M23-02 -----------------------------------------------------------------------------------------------------
def test_late_permission_answer_does_not_revive_the_turn(A):
    s = mk(A)
    t = Turns(s)
    s.send("one")
    wait_for(lambda: len(t.sent) == 1, what="the turn")
    th = threading.Thread(target=lambda: s.approve("bash", "Run?", "ls"))
    th.start()
    wait_for(lambda: s.pendings and s.state == "waiting", what="the card")
    aid = next(iter(s.pendings))
    t.release()                                                      # the turn ends with the card still open
    idle(s)
    try:
        s.answer(aid, "yes")                                         # a late answer from the page
    except ValueError:
        pass
    th.join(5)
    assert not th.is_alive()
    assert s.state == "idle"                                         # old code: back to "working" for good


# ---- X-06 -------------------------------------------------------------------------------------------------------
def test_retry_notice_is_resolved_when_the_call_succeeds(A):
    s = mk(A)
    s.emit({"type": "status", "text": "Rate limited by the endpoint (5 s so far). Waiting 12 s."})
    assert s.events[-1].get("retry") is True
    s.emit({"type": "status", "text": "Just a note."})
    assert not s.events[-1].get("retry")
    s.emit({"type": "usage", "in": 1})
    assert [e["type"] for e in s.events[-2:]] == ["retry_ok", "usage"]
    s.emit({"type": "usage", "in": 2})
    assert s.events[-1]["type"] == "usage" and s.events[-2]["type"] == "usage"        # once only
    tok = s._claim_turn()                                            # a turn that ends in an error: no retry_ok
    s.emit({"type": "status", "text": "Model call failed. Trying again (2 of 5)…"})
    assert s.events[-1]["retry"] is True
    s._end_turn(tok)
    s.emit({"type": "usage", "in": 3})
    assert s.events[-1]["type"] == "usage" and s.events[-2]["type"] != "retry_ok"
