"""Deleting a chat is done by the server (soft delete, then purge after a grace), not by the page; Undo keeps the
running task; a delete during a reopen does not bring the chat back. M06-01, M06-02, M06-04, F-04."""
import json
import threading
import time

import pytest

from meshpool_harness.ui import agent_api as A


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_DELETE_GRACE_S", "0.5")
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.SESSIONS.mkdir()
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "DELETED", set())
    monkeypatch.setattr(A, "_METAS", {})
    monkeypatch.setattr(A, "_ROWS", {})
    monkeypatch.setattr(A, "_DEL_TIMERS", {})
    A.list_dirty()
    return tmp_path


def _chat(sid="abc123", title="Hello"):
    d = A.SESSIONS / sid
    d.mkdir()
    (d / "session.json").write_text(json.dumps({"id": sid, "title": title, "workdir": str(A.SESSIONS.parent),
                                                "updated": 1, "messages": [{"role": "user", "content": title}]}))
    A.write_meta(sid, title=title)
    A.list_dirty()
    return d


class FakeAgent:
    title = "Working"
    deleted = False
    stops = 0

    def stop(self):
        FakeAgent.stops += 1


class FakeSession:
    def __init__(self, sid, state="working"):
        self.id, self.state, self.agent = sid, state, FakeAgent()
        self.workdir, self.kind, self.harness = str(A.SESSIONS.parent), "code", {"id": "x"}
        self.deleted, self.closed = False, 0
        self.lock, self.events = threading.RLock(), []

    def close(self):
        self.closed += 1
        self.agent.stop()
        self.state = "idle"


def test_delete_is_done_by_the_server_without_the_page(home):
    d = _chat()
    A.thread_action("abc123", {"action": "delete"})
    assert d.is_dir()                                   # still there during the grace (Undo window)
    assert A.listing() == []
    time.sleep(1.2)
    assert not d.exists()
    # an app restart: nothing in memory, the meta says deleted a while ago
    d2 = _chat("def456")
    A.write_meta("def456", deleted=True, deleted_at=time.time() - 60)
    A.LIVE.clear(); A.DELETED.clear(); A._DEL_TIMERS.clear()
    assert A.purge_due() == ["def456"]
    assert not d2.exists()


def test_undo_delete_keeps_the_running_task(home):
    d = _chat()
    s = FakeSession("abc123")
    A.LIVE["abc123"] = s
    FakeAgent.stops = 0
    A.thread_action("abc123", {"action": "delete"})
    A.thread_action("abc123", {"action": "undelete"})
    time.sleep(1.0)
    assert FakeAgent.stops == 0 and s.state == "working" and s.closed == 0
    assert d.is_dir() and "abc123" in A.LIVE
    m = A.read_meta("abc123")
    assert "deleted" not in m and "deleted_at" not in m
    assert [r["id"] for r in A.listing()] == ["abc123"]


def test_undelete_after_purge_says_so(home):
    _chat()
    A.thread_action("abc123", {"action": "delete"})
    time.sleep(1.2)
    with pytest.raises((ValueError, KeyError)) as e:
        A.thread_action("abc123", {"action": "undelete"})
    assert "already deleted" in str(e.value) or "no longer exists" in str(e.value)


def test_delete_during_get_rebuild_does_not_resurrect(home, monkeypatch):
    _chat()
    built, release, made = threading.Event(), threading.Event(), []

    class Slow(FakeSession):
        def __init__(self, workdir=None, resume_id=None, **kw):
            super().__init__(resume_id)
            made.append(self)
            built.set()
            release.wait(10)

    monkeypatch.setattr(A, "Session", Slow)
    out = {}

    def opener():
        try:
            A.get("abc123")
        except KeyError as e:
            out["err"] = e
    t = threading.Thread(target=opener)
    t.start()
    assert built.wait(5)
    A.thread_action("abc123", {"action": "delete"})
    A._purge("abc123")
    release.set()
    t.join(10)
    assert "err" in out and "abc123" not in A.LIVE
    assert made[0].closed >= 1


def test_deleted_chat_hidden_from_list_and_search(home):
    _chat(title="needlephrase here")
    assert [r["id"] for r in A.listing()] == ["abc123"] and A.search("needlephrase")
    A.thread_action("abc123", {"action": "delete"})
    assert A.listing() == [] and A.search("needlephrase") == []
    with pytest.raises(KeyError):
        A.get("abc123")
    A.thread_action("abc123", {"action": "undelete"})
    assert A.search("needlephrase")


def test_discard_only_without_a_user_message(home):
    d = _chat()
    with pytest.raises(ValueError):
        A.thread_action("abc123", {"action": "discard"})
    e = A.SESSIONS / "new111"
    e.mkdir()
    (e / "session.json").write_text(json.dumps({"id": "new111", "workdir": str(home), "messages": []}))
    A.write_meta("new111", title="x")
    A.thread_action("new111", {"action": "discard"})
    assert not e.exists() and d.exists()
