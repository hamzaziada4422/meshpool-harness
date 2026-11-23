"""Crash-safe chat storage (M09-01 G-12 M23-04 M09-02 P-05)."""
import errno
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from meshpool_harness import fsio
from meshpool_harness import session_store as SS
from meshpool_harness.agent import core
from meshpool_harness.agent.core import Agent

APP = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")


def _agent(tmp_path):
    return Agent(str(tmp_path), approve=None, mode="auto")


def _snap(n):
    return {"id": "c1", "title": "t", "messages": [{"role": "user", "content": f"m{i}"} for i in range(n)]}


_CHILD = r"""
import sys, json
sys.path.insert(0, {app!r})
from pathlib import Path
from meshpool_harness import session_store as SS
d = Path(sys.argv[1]); mode = sys.argv[2]
msgs = []
i = 0
while True:
    i += 1
    msgs.append({{"role": "user", "content": "x" * 3000 + str(i)}})
    data = {{"id": "c1", "messages": list(msgs)}}
    if mode == "old":
        (d / "session.json").write_text(json.dumps(data))
    elif i % 5 == 1:
        SS.write_snapshot(d, data)
    else:
        SS.append_journal(d, len(msgs) - 1, msgs[-1:], {{"updated": i}})
    open(str(d / "done"), "w").write(str(i))
"""


def _run_child(d, mode):
    script = d.parent / f"child_{mode}.py"
    script.write_text(_CHILD.format(app=str(APP)))
    return subprocess.Popen([sys.executable, str(script), str(d), mode])


def test_kill_during_save_never_loses_the_chat(tmp_path):
    rnd = random.Random(7)
    for k in range(20):
        d = tmp_path / f"chat{k}"
        d.mkdir()
        p = _run_child(d, "new")
        time.sleep(rnd.uniform(0.15, 0.5))
        p.send_signal(signal.SIGKILL)
        p.wait()
        done = int((d / "done").read_text() or 0) if (d / "done").exists() else 0
        if done == 0:
            continue
        got = SS.read(d)
        assert got is not None, f"run {k}: chat vanished"
        assert len(got["messages"]) >= done - 1, (k, done, len(got["messages"]))


def test_old_in_place_writer_is_gone():
    base = APP / "meshpool_harness"
    for f in ("harnesses/pi_rpc.py", "harnesses/acp.py", "chat_import.py", "agent/core.py"):
        src = (base / f).read_text(encoding="utf-8")
        assert not re.search(r'session\.json"\)\.write_text', src), f
        assert "session.json.tmp" not in src, f
    for f in ("harnesses/pi_rpc.py", "harnesses/acp.py", "chat_import.py"):
        assert "SS.write_snapshot" in (base / f).read_text(encoding="utf-8"), f


def test_disk_full_keeps_the_previous_session(tmp_path, monkeypatch):
    d = tmp_path / "c"
    SS.write_snapshot(d, _snap(3))
    real = os.fsync

    def boom(fd):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        SS.write_snapshot(d, _snap(9))
    monkeypatch.setattr(os, "fsync", real)
    assert len(SS.read(d)["messages"]) == 3


def test_agent_save_swallows_disk_full_and_keeps_chat(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    a.messages.append({"role": "user", "content": "hello"})
    a.save()
    a.messages.append({"role": "assistant", "content": "more"})

    def full(*a_, **k):
        raise OSError(errno.ENOSPC, "full")
    monkeypatch.setattr(fsio, "atomic_write_bytes", full)
    a.save(full=True)
    assert [m["content"] for m in SS.read(a.dir)["messages"]] == ["hello"]


def test_corrupt_session_recovers_from_backup_and_is_quarantined(tmp_path):
    d = tmp_path / "c"
    SS.write_snapshot(d, _snap(2))
    SS.write_snapshot(d, _snap(4))
    raw = (d / "session.json").read_bytes()
    (d / "session.json").write_bytes(raw[: len(raw) // 2])
    got = SS.read(d)
    assert len(got["messages"]) == 2
    assert list(d.glob("session.json.corrupt-*"))


def test_concurrent_transcript_persist_threads(tmp_path):
    d = tmp_path / "t"
    logs = [SS.TranscriptLog(d), SS.TranscriptLog(d)]
    errs = []

    def work(k):
        try:
            for i in range(500):
                logs[k].append([{"type": "x", "k": k, "i": i}])
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=work, args=(k,)) for k in (0, 1)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    evs = [json.loads(x) for x in (d / "transcript.jsonl").read_text().splitlines()]
    assert len(evs) == 1000
    assert {(e["k"], e["i"]) for e in evs} == {(k, i) for k in (0, 1) for i in range(500)}


def test_torn_journal_and_transcript_last_line_is_ignored(tmp_path):
    d = tmp_path / "c"
    SS.write_snapshot(d, _snap(2))
    SS.append_journal(d, 2, [{"role": "user", "content": "m2"}], {"title": "new"})
    with open(d / "session.journal", "ab") as f:
        f.write(b'{"from": 3, "messages": [{"role": "us')
    got = SS.read(d)
    assert len(got["messages"]) == 3 and got["title"] == "new"
    SS.append_journal(d, 3, [{"role": "user", "content": "m3"}], {})      # appending after a torn line still works
    assert len(SS.read(d)["messages"]) == 4
    log = SS.TranscriptLog(d)
    log.append([{"type": "a"}, {"type": "b"}])
    with open(d / "transcript.jsonl", "ab") as f:
        f.write(b'{"type": "c", "te')
    assert [e["type"] for e in SS.TranscriptLog(d).read()["events"]] == ["a", "b"]
    SS.TranscriptLog(d).append([{"type": "d"}])
    assert [e["type"] for e in SS.TranscriptLog(d).read()["events"]] == ["a", "b", "d"]


def test_old_format_session_and_transcript_still_load(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    (d / "session.json").write_text(json.dumps(_snap(2)))
    (d / "transcript.json").write_text(json.dumps({"events": [{"type": "user"}], "running": True}))
    assert len(SS.read(d)["messages"]) == 2
    assert SS.TranscriptLog(d).read() == {"events": [{"type": "user"}], "running": True}
    SS.TranscriptLog(d).append([{"type": "assistant"}])        # the old events carry over
    assert [e["type"] for e in SS.TranscriptLog(d).read()["events"]] == ["user", "assistant"]
    assert SS.read(tmp_path / "nothing") is None and SS.TranscriptLog(tmp_path / "nothing").read() is None


def _bytes_written(monkeypatch):
    tot = [0]
    real_bytes, real_line = fsio.atomic_write_bytes, SS._append_line

    def wb(path, data, **k):
        tot[0] += len(data)
        return real_bytes(path, data, **k)

    def wl(p, line):
        tot[0] += len(line.encode("utf-8"))
        return real_line(p, line)
    monkeypatch.setattr(fsio, "atomic_write_bytes", wb)
    monkeypatch.setattr(SS, "_append_line", wl)
    return tot


def test_mid_turn_saves_append_instead_of_rewriting(tmp_path, monkeypatch):
    tot = _bytes_written(monkeypatch)
    a = _agent(tmp_path)
    a.messages.append({"role": "user", "content": "go"})
    a.save()
    for i in range(300):
        a.messages.append({"role": "assistant", "content": "y" * 2000 + str(i)})
        a.save()
    final = len(json.dumps(a.messages))
    assert tot[0] <= 4 * final, (tot[0], final)
    assert len(SS.read(a.dir)["messages"]) == 301
    tot[0] = 0
    log = SS.TranscriptLog(tmp_path / "tr")
    for i in range(3000):
        log.append([{"type": "assistant", "text": "z" * 200, "n": i}])
    assert tot[0] <= 4 * (3000 * 230)


def test_resume_replays_journal_after_crash(tmp_path):
    a = _agent(tmp_path)
    a.messages.append({"role": "user", "content": "one"})
    a.save(full=True)
    a.messages.append({"role": "assistant", "content": "two"})
    a.save()
    a.messages.append({"role": "user", "content": "three"})
    a.save()                                                   # no final snapshot: the process "dies" here
    assert (a.dir / "session.journal").exists()
    b = Agent.resume(str(tmp_path), a.id)
    assert [m["content"] for m in b.messages[1:]] == ["one", "two", "three"]


def test_changed_prefix_forces_a_full_snapshot(tmp_path):
    a = _agent(tmp_path)
    a.messages.append({"role": "user", "content": "one"})
    a.save()
    a.messages[-1]["content"] += " + env"                      # the last saved message was edited in place
    a.messages.append({"role": "assistant", "content": "two"})
    a.save()
    assert not (a.dir / "session.journal").exists()
    assert SS.read(a.dir)["messages"][0]["content"] == "one + env"
