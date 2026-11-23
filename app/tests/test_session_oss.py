"""Session files stay small, a failed save is surfaced, and tool calls stay paired (OSS-05 OSS-35 OSS-11)."""
import base64
import errno
import os

import pytest

from meshpool_harness import chat_import as CI
from meshpool_harness import fsio
from meshpool_harness import session_store as SS
from meshpool_harness.agent import core
from meshpool_harness.agent.core import Agent
from meshpool_harness.agent.history_repair import repair_tool_pairs
from meshpool_harness.ui import agent_api as A


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setattr(CI, "_sessions", lambda: tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")


def _img(i):
    return "data:image/png;base64," + base64.b64encode(bytes([i % 256]) * (1024 * 1024)).decode()


def _agent(tmp_path, emit=None):
    return Agent(str(tmp_path), approve=None, mode="auto", emit=emit)


def _imsg(u):
    return {"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": u}}]}


def test_images_out_of_line(tmp_path):
    a = _agent(tmp_path)
    urls = [_img(i) for i in range(10)]
    a.messages += [_imsg(u) for u in urls] + [_imsg(urls[0])]            # the first image twice
    a.save(full=True)
    assert (a.dir / "session.json").stat().st_size < 50_000
    assert len(list((a.dir / "blobs").iterdir())) == 10
    assert a.messages[1]["content"][1]["image_url"]["url"] == urls[0]    # memory untouched
    b = Agent.resume(str(tmp_path), a.id)
    assert [m["content"][-1]["image_url"]["url"] for m in b.messages[1:]] == urls + [urls[0]]
    assert SS.read(a.dir)["messages"][0]["content"][1]["image_url"]["url"].startswith("meshpool-blob:")


def test_journal_images_missing_blob_and_old_inline(tmp_path):
    a = _agent(tmp_path)
    a.save(full=True)
    a.messages.append(_imsg(_img(3)))
    a.save()
    assert 0 < SS.journal_size(a.dir) < 5000
    assert SS.read(a.dir, resolve_blobs=True)["messages"][0]["content"][1]["image_url"]["url"] == _img(3)
    for f in (a.dir / "blobs").iterdir():
        f.unlink()
    assert SS.read(a.dir, resolve_blobs=True)["messages"][0]["content"][1] == {
        "type": "text", "text": "[image no longer available]"}
    old = {"id": "old", "messages": [_imsg("data:image/jpg;base64,QUJD")]}
    fsio.atomic_write_json(a.dir.parent / "old" / "session.json", old)
    assert SS.read(a.dir.parent / "old", True)["messages"] == old["messages"]


def test_blob_gc(tmp_path):
    a = _agent(tmp_path)
    a.messages.append(_imsg(_img(1)))
    a.save(full=True)
    a.messages.pop()
    for f in (a.dir / "blobs").iterdir():
        os.utime(f, (1, 1))
    a.save(full=True)
    assert list((a.dir / "blobs").iterdir()) == []


def test_listing_uses_head_not_messages(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    a.title = "hello"
    a.messages.append(_imsg(_img(2)))
    a.save(full=True)
    assert SS.read_head(a.dir)["count"] == 1
    monkeypatch.setattr(SS, "read", lambda *x, **k: (_ for _ in ()).throw(AssertionError("loaded messages")))
    A._ROWS.clear()
    A._LIST_CACHE[:] = [0, None, None]
    base, _, _ = A._disk_rows()
    assert a.id in base


def test_save_error_surfaced_once(tmp_path, monkeypatch):
    ev = []
    a = _agent(tmp_path, emit=ev.append)
    a.messages.append({"role": "user", "content": "one"})
    a.save(full=True)
    before = (a.dir / "session.json").read_bytes()
    real = fsio.atomic_write_bytes

    def boom(*x, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(fsio, "atomic_write_bytes", boom)
    a.messages.append({"role": "user", "content": "two"})
    a.save(full=True)
    a.save(full=True)
    errs = [e for e in ev if e.get("save_error")]
    assert len(errs) == 1 and "No space left on device" in errs[0]["text"] and a.save_error
    assert (a.dir / "session.json").read_bytes() == before
    monkeypatch.setattr(fsio, "atomic_write_bytes", real)
    a.save(full=True)
    assert a.save_error is None and ev[-1]["text"] == "Saved again."
    assert len(SS.read(a.dir)["messages"]) == 2


def _call(i):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": i, "type": "function", "function": {"name": "t", "arguments": "{}"}}]}


def _res(i, c="r"):
    return {"role": "tool", "tool_call_id": i, "content": c}


def test_repair_cases():
    sysm = {"role": "system", "content": "s"}
    ok = [sysm, {"role": "user", "content": "q"}, _call("a"), _res("a")]
    out, ch = repair_tool_pairs(ok)
    assert out is ok and not ch
    orphan = [sysm, _res("z"), {"role": "user", "content": "q"}, _call("a"), _res("a"), _res("gone")]
    out, ch = repair_tool_pairs(orphan)
    assert ch and [m["role"] for m in out] == ["system", "user", "assistant", "tool"] and len(orphan) == 6
    out, ch = repair_tool_pairs([sysm, _call("a"), {"role": "user", "content": "next"}])
    assert out[2]["tool_call_id"] == "a" and "interrupted" in out[2]["content"] and out[3]["role"] == "user"
    out, _ = repair_tool_pairs([sysm, _call("a"), _res("a", "first"), _res("a", "second")])
    assert [m.get("content") for m in out[2:]] == ["first"]


def test_load_import_and_request_repaired(tmp_path):
    a = _agent(tmp_path)
    a.messages += [{"role": "user", "content": "q"}, _call("a"), {"role": "user", "content": "after kill"}, _res("zz")]
    a.save(full=True)
    assert [m["role"] for m in A._saved(a.id)["messages"]] == ["user", "assistant", "tool", "user"]
    assert [m["role"] for m in Agent.resume(str(tmp_path), a.id).messages[1:]] == ["user", "assistant", "tool", "user"]
    body = a._body()                                                    # outgoing copy is clean, memory is not mutated
    assert [m.get("tool_call_id") for m in body["messages"] if m.get("role") == "tool"] == ["a"]
    assert any(m.get("tool_call_id") == "zz" for m in a.messages)
    cv = {"source": "x", "id": "1", "workdir": str(tmp_path), "kind": "code", "title": "t", "created": 1, "updated": 1,
          "messages": [_res("q", "o"), {"role": "user", "content": "hi"}, _call("b")]}
    CI.write_threads([cv])
    d = SS.read(next((tmp_path / "_sessions").glob("imp-*")))
    assert [m["role"] for m in d["messages"]] == ["user", "assistant", "tool"]


def test_head_older_than_snapshot_is_ignored(tmp_path):
    """A session.json rewritten by a build that writes no head file must not be listed from the stale head."""
    import os
    import time
    from meshpool_harness import session_store as SS
    d = tmp_path / "chat"
    SS.write_snapshot(d, {"id": "c", "title": "old title", "messages": []})
    assert SS.read_head(d)["title"] == "old title"
    time.sleep(0.01)
    (d / "session.json").write_text('{"id": "c", "title": "new title", "messages": []}')   # an older build's save
    st = os.stat(d / "session.json")
    os.utime(d / SS.HEAD, ns=(st.st_atime_ns, st.st_mtime_ns - 1_000_000))
    assert SS.read_head(d) is None
