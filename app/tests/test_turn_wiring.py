"""A turn started by a message added late gets its own checkpoint; Meshpool's agent books only what its tools wrote;
Undo keeps earlier turns' events (and so their Undo buttons). M05-02, M05-01/M05-03, X-05."""
from __future__ import annotations

import time

import pytest

from meshpool_harness.agent.core import Agent
from meshpool_harness.ui import agent_api as A


class FakeAgent(Agent):
    """The real agent object (messages, save, emit, checkpoint) with the model loop replaced by scripted writes."""
    script: list = []            # per send: [(path, content)] written through the tools
    late: list = []              # per resume_steers: [(path, content)]
    outside_file = None          # a file the user creates in the middle of a turn, not through a tool

    def _write(self, path, content, name="write_file"):
        cp = getattr(self, "checkpoint", None)
        import contextlib
        with (cp.tool(name, {"path": path}) if cp else contextlib.nullcontext()):
            (self.root / path).write_text(content)

    def send(self, text, images=None):
        self._stop = False
        self.messages.append({"role": "user", "content": text})
        self.emit({"type": "user", "text": text.split("\n\n\u200b")[0], "images": [], "plan": False, "mi": len(self.messages) - 1})
        for path, content in (self.script.pop(0) if self.script else []):
            self._write(path, content)
        if FakeAgent.outside_file:
            (self.root / FakeAgent.outside_file).write_text("by the user\n")
        self.messages.append({"role": "assistant", "content": "done " + text})
        self.emit({"type": "assistant", "text": "done " + text})
        self.save()
        self.emit({"type": "turn_end"})
        return "done"

    def has_steers(self):
        return bool(self.late)

    def resume_steers(self):
        batch = self.late.pop(0)
        self.messages.append({"role": "user", "content": "[Added by the user while you were working] more"})
        for path, content in batch:
            self._write(path, content)
        self.messages.append({"role": "assistant", "content": "done more"})
        self.emit({"type": "assistant", "text": "done more"})
        self.emit({"type": "turn_end"})
        return "done more"


def SS_read_all(sid):
    return A._saved(sid)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    import meshpool_harness.agent.core as core
    monkeypatch.setattr(core, "SESSIONS", A.SESSIONS)
    A.SESSIONS.mkdir()
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "_METAS", {})
    monkeypatch.setattr(A, "Agent", FakeAgent)
    monkeypatch.setenv("MH_CHECKS", "0")
    FakeAgent.script, FakeAgent.late, FakeAgent.outside_file = [], [], None
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "keep.txt").write_text("keep\n")
    return proj


def _session(proj):
    s = A.Session(workdir=str(proj), harness="meshpool-code", kind="code")
    s._mcp_ready.set()
    return s


def _wait(s, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if s.state == "idle":
            return
        time.sleep(0.05)
    pytest.fail("the turn did not finish")


def _run(s, text):
    s.send(text)
    time.sleep(0.2)
    _wait(s)


def test_late_added_message_gets_its_own_turn_and_undo(env):
    """M05-02: files written by the turn that answers a late addition are booked to a turn of their own."""
    FakeAgent.script = [[("a.txt", "first\n")]]
    FakeAgent.late = [[("late.txt", "late\n")]]
    s = _session(env)
    assert s.agent.checkpoint is s.cp and s.cp.mode == "journal"
    _run(s, "make a")
    turns = s.cp.turns()
    assert len(turns) == 2, turns
    assert [c["path"] for c in turns[0]["changed"]] == ["a.txt"]
    assert [c["path"] for c in turns[1]["changed"]] == ["late.txt"]
    assert turns[1]["title"] == "Added message"
    cps = [e for e in s.events if e["type"] == "checkpoint"]
    assert len(cps) == 2 and cps[1]["turns"][0]["n"] == turns[1]["n"]
    assert A.read_meta(s.id)["turn_seq"] == turns[1]["n"]
    s.revert(turns[1]["n"])
    assert not (env / "late.txt").exists() and (env / "a.txt").exists()


def test_journal_books_only_what_the_tools_wrote(env):
    """M05-01/M05-03: a file the user creates during the turn is not the agent's and survives Undo."""
    FakeAgent.script = [[("a.txt", "x\n")]]
    FakeAgent.outside_file = "mine.txt"
    s = _session(env)
    _run(s, "make a")
    t = s.cp.turns()[0]
    assert [c["path"] for c in t["changed"]] == ["a.txt"]
    s.revert(t["n"])
    assert (env / "mine.txt").read_text() == "by the user\n" and not (env / "a.txt").exists()


def test_revert_keeps_earlier_turns_events(env):
    """X-05: the transcript is cut at the turn, not rebuilt from messages, so earlier turns keep turn_end and checkpoint."""
    FakeAgent.script = [[("a.txt", "1\n")], [("b.txt", "2\n")]]
    s = _session(env)
    _run(s, "make a")
    _run(s, "make b")
    t1, t2 = s.cp.turns()
    assert t2["event_index"] > t1["event_index"] >= 0
    s.revert(t2["n"])
    kinds = [e["type"] for e in s.events]
    assert kinds.count("turn_end") == 1 and kinds.count("checkpoint") == 1
    cp = next(e for e in s.events if e["type"] == "checkpoint")
    assert cp["turns"][0]["n"] == t1["n"] and cp["turns"][0]["changed"]
    users = [e for e in s.events if e["type"] == "user"]
    assert [u["text"] for u in users] == ["make a"]
    assert [e["i"] for e in s.events] == list(range(len(s.events)))
    assert (env / "a.txt").exists() and not (env / "b.txt").exists()
    # and the saved transcript agrees
    saved = A._transcript(s.id)["events"]
    assert sum(1 for e in saved if e["type"] == "checkpoint") == 1


def test_undo_no_longer_refused_when_another_chat_shared_the_folder(env):
    """The shared_turns refusal is gone: attribution is by tool, ambiguous paths are kept with a note."""
    FakeAgent.script = [[("a.txt", "1\n")]]
    s = _session(env)
    _run(s, "make a")
    A.write_meta(s.id, shared_turns=[1])         # what the old code wrote when two chats worked in one folder
    r = s.revert(s.cp.turns()[0]["n"])
    assert r["deleted"] == ["a.txt"]


def test_real_run_tool_books_the_tool_call(env):
    """core._run_tool wraps the dispatch in checkpoint.tool: a real write_file call is booked, an error still returns."""
    import json
    s = _session(env)
    a = s.agent
    a.mode = "auto"
    s.cp.begin_turn("t", len(a.messages), 0)
    out = a._run_tool({"id": "c1", "function": {"name": "write_file",
                                                "arguments": json.dumps({"path": "w.txt", "content": "hi\n"})}})
    assert "ERROR" not in out and (env / "w.txt").read_text() == "hi\n"
    (env / "mine.txt").write_text("the user, between two tool calls\n")      # never booked: it is not a tool's write
    out = a._run_tool({"id": "c2", "function": {"name": "read_file", "arguments": json.dumps({"path": "keep.txt"})}})
    bad = a._run_tool({"id": "c3", "function": {"name": "write_file", "arguments": "{}"}})
    assert bad.startswith("ERROR")                                  # the failure propagates as before
    s.cp.end_turn()
    assert s.cp.mode == "journal" and [c["path"] for c in s.cp.turns()[0]["changed"]] == ["w.txt"]


def test_user_event_carries_message_index_and_children_have_no_checkpoint(env):
    s = _session(env)
    a = s.agent
    n = len(a.messages)
    a._turn = lambda: ""
    a.send("hi")
    ev = [e for e in s.events if e["type"] == "user"][-1]
    assert ev["mi"] == n and a.messages[ev["mi"]]["role"] == "user"
    child = Agent(str(env), profile="code")                        # what delegate builds: a fresh Agent
    assert getattr(child, "checkpoint", None) is None and a.checkpoint is s.cp


def test_live_session_rewind_and_fork(env):
    """G-10 on a live Session: the event number maps to its message; transcript, messages and files agree."""
    FakeAgent.script = [[("a.txt", "1\n")], [("b.txt", "2\n")]]
    s = _session(env)
    _run(s, "make a")
    _run(s, "make b")
    second = [e for e in s.events if e["type"] == "user"][1]["i"]
    first = [e for e in s.events if e["type"] == "user"][0]["i"]
    new = s.fork(first)                                     # up to the end of the first exchange
    d = A._saved(new)
    assert [m["content"].split("\n")[0] for m in d["messages"] if m["role"] == "assistant"] == ["done make a"]
    assert len(SS_read_all(s.fork(second))["messages"]) == 4        # from the last message: the whole chat
    assert (env / "b.txt").exists()
    r = s.rewind(second, files=True)
    assert r["text"] == "make b" and not (env / "b.txt").exists() and (env / "a.txt").exists()
    assert [e["text"] for e in s.events if e["type"] == "user"] == ["make a"]
    assert len(s.agent.messages) == 3 and s.epoch > 0
    assert [t["n"] for t in s.cp.turns()] == [1]
    with pytest.raises(ValueError):
        s.rewind(0)                                          # not a user message


def test_user_edit_while_a_command_waits_for_approval_is_not_booked(env):
    """M05-01: the scan window of a shell command opens only once the user allowed it. Files the user edits while the
    approval card waits are theirs, and Undo must not touch them."""
    import json
    s = _session(env)
    a = s.agent
    s.cp.begin_turn("t", len(a.messages), 0)

    def permit(name, args):
        (env / "mine.txt").write_text("typed by the user while the approval card waited\n")
        return True, ""
    a._permit = permit
    a._run_tool({"id": "c1", "function": {"name": "bash", "arguments": json.dumps(
        {"command": "echo agent > agent.txt"})}})
    s.cp.end_turn()
    changed = [c["path"] for c in s.cp.turns()[0]["changed"]]
    assert "agent.txt" in changed and "mine.txt" not in changed, changed
    s.cp.revert_to_before(s.cp.turns()[0]["n"])
    assert (env / "mine.txt").exists() and not (env / "agent.txt").exists()
