"""Keep chats for N days: older ones go, pinned and running ones stay; 0 keeps everything."""
import os
import time

from meshpool_harness.ui import agent_api as A


def _chat(root, sid, age_days, **meta):
    d = root / sid
    d.mkdir(parents=True)
    (d / "transcript.json").write_text("{}")
    if meta:
        A.write_meta(sid, **meta)
    t = time.time() - age_days * 86400
    for f in d.iterdir():
        os.utime(f, (t, t))


def test_old_chats_go_and_pinned_ones_stay(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SESSIONS", tmp_path)
    monkeypatch.setattr(A, "LIVE", {})
    _chat(tmp_path, "old1", 40)
    _chat(tmp_path, "old2", 40, pinned=True)
    _chat(tmp_path, "new1", 2)
    assert A.prune_old_chats(0) == [] and (tmp_path / "old1").exists()       # off: nothing happens
    gone = A.prune_old_chats(30)
    assert gone == ["old1"] and not (tmp_path / "old1").exists()
    assert (tmp_path / "old2").exists() and (tmp_path / "new1").exists()


def test_sub_agents_open_chats_and_short_values_are_spared(tmp_path, monkeypatch):    # third review, 9
    monkeypatch.setattr(A, "SESSIONS", tmp_path)
    monkeypatch.setattr(A, "LIVE", {"open1": object()})
    _chat(tmp_path, "sub-abc", 40)
    _chat(tmp_path, "open1", 40)
    _chat(tmp_path, "old3", 40)
    assert A.prune_old_chats(0.01) == []                                  # under a day: off
    assert A.prune_old_chats(30) == ["old3"]
    assert (tmp_path / "sub-abc").exists() and (tmp_path / "open1").exists()
