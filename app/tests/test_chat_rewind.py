"""Rewind a chat to just before one of its messages, and fork it into a new chat, on a saved chat with no live session
(the CLI's case) and on outside-agent chats. G-10."""
from __future__ import annotations

import json
import threading

import pytest

from meshpool_harness import chat_rewind as CR
from meshpool_harness import session_store as SS
from meshpool_harness.checkpoints import Checkpoints
from meshpool_harness.ui import agent_api as A


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.SESSIONS.mkdir()
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "_METAS", {})
    monkeypatch.setattr(A, "_ROWS", {})
    A.list_dirty()
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _builtin_chat(proj, sid="c1"):
    """Two exchanges; each wrote a file through a checkpointed tool call. Saved the way the app saves it."""
    d = A.SESSIONS / sid
    d.mkdir()
    cp = Checkpoints(str(proj), d / "checkpoints", mode="journal")
    msgs, evs = [], []
    for k, name in enumerate(("one.txt", "two.txt"), 1):
        text = f"make the file {name}"
        mi = len(msgs) + 1                                   # + the system prompt that is not saved
        msgs.append({"role": "user", "content": text + "\n\n​hidden note"})
        evs.append({"type": "user", "text": text, "mi": mi})
        cp.begin_turn(text, mi, len(evs) - 1)
        with cp.tool("write_file", {"path": name}):
            (proj / name).write_text(f"made {k}\n")
        cp.end_turn()
        msgs.append({"role": "assistant", "content": f"Made {name}."})
        evs += [{"type": "assistant", "text": f"Made {name}."}, {"type": "turn_end"},
                {"type": "checkpoint", "turns": cp.turns()[-1:]}]
    SS.write_snapshot(d, {"id": sid, "workdir": str(proj), "title": "Two files", "created": 1, "updated": 2,
                          "harness": "meshpool-code", "kind": "code", "model": "m", "messages": msgs})
    SS.TranscriptLog(d).rewrite(evs, False)
    A.write_meta(sid, title="Two files", kind="code", mode="edits", style="plain", project_id="p1", turn_seq=cp.seq)
    return sid


def test_points_lists_the_user_messages(home):
    sid = _builtin_chat(home)
    pts = CR.points(sid)
    assert [p["message_index"] for p in pts] == [1, 3]
    assert [p["text"] for p in pts] == ["make the file one.txt", "make the file two.txt"]
    assert [p["event_index"] for p in pts] == [0, 4]
    assert [p["turn"] for p in pts] == [1, 2]


def test_rewind_saved_chat_cuts_conversation_and_reverts_files(home):
    sid = _builtin_chat(home)
    r = CR.rewind(sid, 3, files=True)
    assert r["text"] == "make the file two.txt"
    assert r["undo"]["deleted"] == ["two.txt"]
    assert not (home / "two.txt").exists() and (home / "one.txt").read_text() == "made 1\n"
    data = SS.read(A.SESSIONS / sid)
    assert [m["content"].split("\n")[0] if m["role"] == "user" else m["content"] for m in data["messages"]] == [
        "make the file one.txt", "Made one.txt."]
    evs = A._transcript(sid)["events"]
    assert [e["type"] for e in evs] == ["user", "assistant", "turn_end", "checkpoint"]     # turn 1 keeps its Undo
    assert evs[-1]["turns"][0]["n"] == 1
    assert [t["n"] for t in Checkpoints(str(home), A.SESSIONS / sid / "checkpoints", mode="journal").turns()] == [1]


def test_rewind_without_files_leaves_files(home):
    sid = _builtin_chat(home)
    r = CR.rewind(sid, 3, files=False)
    assert r["undo"] is None and (home / "two.txt").exists()
    assert len(SS.read(A.SESSIONS / sid)["messages"]) == 2


def test_rewind_to_the_first_message_empties_the_chat(home):
    sid = _builtin_chat(home)
    CR.rewind(sid, 1, files=True)
    assert SS.read(A.SESSIONS / sid)["messages"] == []
    assert not (home / "one.txt").exists() and not (home / "two.txt").exists()
    assert A._transcript(sid)["events"] == []


def test_rewind_refused_while_working(home):
    sid = _builtin_chat(home)

    class Busy:
        id, state, lock = sid, "working", threading.RLock()

    with pytest.raises(ValueError, match="Stop the agent"):
        CR.rewind(sid, 3, session=Busy())
    A.LIVE[sid] = Busy()                               # the live session is found without being passed
    with pytest.raises(ValueError, match="Stop the agent"):
        CR.rewind(sid, 3)
    assert (home / "two.txt").exists()


def test_rewind_unknown_message(home):
    sid = _builtin_chat(home)
    with pytest.raises(ValueError):
        CR.rewind(sid, 2)                              # an assistant message


def test_fork_up_to_a_message(home):
    sid = _builtin_chat(home)
    before = (A.SESSIONS / sid / "session.json").read_text()
    new = CR.fork(sid, 1)
    assert new != sid
    d = SS.read(A.SESSIONS / new)
    assert [m["role"] for m in d["messages"]] == ["user", "assistant"]
    assert d["workdir"] == str(home) and d["harness"] == "meshpool-code" and d["model"] == "m"
    assert d["title"] == "Fork of Two files"
    m = A.read_meta(new)
    assert m["title"] == "Fork of Two files" and m["forked_from"] == {"sid": sid, "message_index": 1}
    assert m["kind"] == "code" and m["mode"] == "edits" and m["style"] == "plain" and m["project_id"] == "p1"
    assert [e["type"] for e in A._transcript(new)["events"]] == ["user", "assistant"]
    assert not (A.SESSIONS / new / "checkpoints" / "turns.json").exists()       # no checkpoints copied
    assert (home / "two.txt").exists() and (A.SESSIONS / sid / "session.json").read_text() == before
    whole = CR.fork(sid)
    assert len(SS.read(A.SESSIONS / whole)["messages"]) == 4
    assert any(r["id"] == new for r in A.listing())


def _outside_chat(proj, sid="o1", harness="pi", kind="code"):
    d = A.SESSIONS / sid
    d.mkdir()
    evs = [{"type": "user", "text": "first"}, {"type": "assistant", "text": "reply 1"}, {"type": "turn_end"},
           {"type": "user", "text": "second"}, {"type": "assistant", "text": "reply 2"}, {"type": "turn_end"}]
    SS.write_snapshot(d, {"id": sid, "workdir": str(proj), "title": "Outside", "created": 1, "updated": 2,
                          "harness": harness, "kind": kind, "events": evs, "acp_session": "s-1"})
    SS.TranscriptLog(d).rewrite(evs, False)
    A.write_meta(sid, title="Outside", kind=kind)
    return sid


def test_fork_of_an_outside_agent_chat_continues_in_meshpool(home, monkeypatch):
    sid = _outside_chat(home)
    monkeypatch.setattr(CR, "_outside_type", lambda h: True)
    assert [p["message_index"] for p in CR.points(sid)] == [0, 2]
    new = CR.fork(sid, 0)
    d = SS.read(A.SESSIONS / new)
    assert d["harness"] == "meshpool-code" and [(m["role"], m["content"]) for m in d["messages"]] == [
        ("user", "first"), ("assistant", "reply 1")]
    cowork = _outside_chat(home, "o2", "codex", "cowork")
    assert SS.read(A.SESSIONS / CR.fork(cowork))["harness"] == "meshpool-work"


def test_rewind_outside_agent_chat_restarts_its_session(home, monkeypatch):
    sid = _outside_chat(home)
    monkeypatch.setattr(CR, "_outside_type", lambda h: True)
    r = CR.rewind(sid, 2, files=True)
    assert r["text"] == "second" and "forked" not in r
    d = SS.read(A.SESSIONS / sid)
    assert [e["text"] for e in d["events"] if e["type"] in ("user", "assistant")] == ["first", "reply 1"]
    assert "acp_session" not in d                        # a new session; the saved conversation is carried as context
    assert [e["type"] for e in A._transcript(sid)["events"]] == ["user", "assistant", "turn_end"]


def test_rewind_outside_agent_that_cannot_restart_forks(home, monkeypatch):
    sid = _outside_chat(home)
    monkeypatch.setattr(CR, "_outside_type", lambda h: True)

    class Odd:                                          # a live outside agent with no way to start over
        id, state, lock = sid, "idle", threading.RLock()
        harness = {"id": "pi", "type": "pi"}
        kind, workdir, cp, events = "code", str(home), None, [{"type": "user", "text": "first"},
                                                              {"type": "assistant", "text": "reply 1"},
                                                              {"type": "user", "text": "second"}]

        class agent:
            title = "Outside"
            events = []
    r = CR.rewind(sid, 2, session=Odd())
    assert r["forked"] and r["text"] == "second"
    assert [m["content"] for m in SS.read(A.SESSIONS / r["forked"])["messages"]] == ["first", "reply 1"]
    assert json.loads((A.SESSIONS / sid / "meta.json").read_text())["title"] == "Outside"


def test_rewind_after_compaction_renumbered_the_messages(home):
    """Compaction replaces older steps with a summary note and puts the current task back after it: the events' message
    numbers are stale, the message is found by what was said, and the note is not a message of the user's."""
    sid = "c9"
    d = A.SESSIONS / sid
    d.mkdir()
    note = "[Earlier steps of this task were summarized to save space. The summary is accurate.]"
    msgs = [{"role": "user", "content": "first task"}, {"role": "user", "content": note},
            {"role": "user", "content": "second task"}, {"role": "user", "content": "second task"},
            {"role": "assistant", "content": "done"}]
    evs = [{"type": "user", "text": "first task", "mi": 1}, {"type": "assistant", "text": "ok"}, {"type": "turn_end"},
           {"type": "user", "text": "second task", "mi": 9}, {"type": "assistant", "text": "done"}, {"type": "turn_end"}]
    SS.write_snapshot(d, {"id": sid, "workdir": str(home), "title": "T", "created": 1, "updated": 2,
                          "harness": "meshpool-code", "kind": "code", "messages": msgs})
    SS.TranscriptLog(d).rewrite(evs, False)
    assert [(p["message_index"], p["event_index"]) for p in CR.points(sid)] == [(1, 0), (3, 3)]
    r = CR.rewind(sid, 3, files=False)
    assert r["text"] == "second task"
    assert [m["content"][:10] for m in SS.read(d)["messages"]] == ["first task", "[Earlier s"]
    assert [e["type"] for e in A._transcript(sid)["events"]] == ["user", "assistant", "turn_end"]
