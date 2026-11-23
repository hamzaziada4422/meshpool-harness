import json
import threading
import time

import pytest

from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import workbench as W


@pytest.fixture
def chats(tmp_path, monkeypatch):
    s = tmp_path / "sessions"
    monkeypatch.setattr(A, "SESSIONS", s)
    A._METAS.clear()
    A._ROWS.clear()
    A._RAW.clear()
    A._RAW_BYTES[0] = 0
    A.list_dirty()
    for i in range(300):
        d = s / f"c{i:04d}"
        d.mkdir(parents=True)
        msgs = [{"role": "user", "content": "hello " * 100}, {"role": "assistant", "content": "x" * 2000}] * 5
        (d / "session.json").write_text(json.dumps({"id": d.name, "title": f"t{i}", "messages": msgs,
                                                    "updated": time.time() - i}), encoding="utf-8")
    return s


def test_listing_is_cached_and_cheap(chats):
    first = A.listing()
    assert len(first) == 300
    t = time.perf_counter()
    for _ in range(50):
        A.listing()
    per_call = (time.perf_counter() - t) / 50
    assert per_call < 0.02                       # was ~7 ms of stats per call; cached it is well under 1 ms


def test_listing_sees_a_rename_at_once(chats):
    A.listing()
    A.write_meta("c0001", title="Renamed")
    assert next(r for r in A.listing() if r["id"] == "c0001")["title"] == "Renamed"
    A.thread_action("c0002", {"action": "delete"})
    assert "c0002" not in {r["id"] for r in A.listing()}


def test_search_text_is_cached(chats):
    assert A.search("zzzz") == []
    assert len(A._RAW) == 300
    t = time.perf_counter()
    A.search("zzzz")
    assert time.perf_counter() - t < 0.5


def test_git_status_calls_are_coalesced(tmp_path, monkeypatch):
    calls = []

    def fake(root):
        calls.append(root)
        time.sleep(0.1)
        return {"repo": True, "branch": "main"}
    monkeypatch.setattr(W, "_git_status", fake)
    W._GS.clear()
    ts = [threading.Thread(target=W.git_status, args=("/x",)) for _ in range(6)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert len(calls) == 1
    W.git_status("/x", fresh=True)
    assert len(calls) == 2


def test_event_stream_wakes_on_emit_without_polling(tmp_path):
    s = A.Session.__new__(A.Session)
    s.lock = threading.RLock()
    s.changed = threading.Condition(s.lock)
    s.events = []
    s._question_state = lambda ev: None
    s._persist = lambda *a, **k: None
    s._persist_soon = lambda: None
    got = []

    def waiter():
        with s.lock:
            t = time.perf_counter()
            s.changed.wait(5)
            got.append(time.perf_counter() - t)
    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(0.2)
    s.emit({"type": "assistant", "text": "hi"})
    th.join(3)
    assert got and got[0] < 2
