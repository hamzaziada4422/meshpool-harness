import json
import os
import subprocess
import sys

import pytest

from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import firstrun_api as FR
from meshpool_harness.ui import hub, settings_v2 as SV2, workbench as W


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    s = tmp_path / "sessions"
    s.mkdir()
    monkeypatch.setattr(A, "SESSIONS", s)
    A._METAS.clear()
    A._ROWS.clear()
    return s


def _save(sessions, sid, messages=None, **kw):
    d = sessions / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "session.json").write_text(json.dumps({"id": sid, "messages": messages or [], **kw}), encoding="utf-8")


def test_saved_messages_keeps_first_user_message(sessions):
    _save(sessions, "a", [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}])
    assert [m["role"] for m in A.saved_messages("a")] == ["user", "assistant"]
    _save(sessions, "b", [{"role": "system", "content": "s"}, {"role": "user", "content": "hello"}])
    assert [m["role"] for m in A.saved_messages("b")] == ["user"]
    assert A.saved_messages("missing") == []
    evs = hub._events("a")
    assert any(e.get("type") == "user" for e in evs)


def test_task_row_carries_schedule_fields():
    t = {"id": "x", "name": "n", "when": "weekly", "time": "16:45", "day": "fri", "minutes": 30,
         "at": "", "only_on_change": True, "enabled": True}
    r = hub._task_row(t, {})
    assert (r["time"], r["day"], r["minutes"], r["only_on_change"]) == ("16:45", "fri", 30, True)


def test_scan_does_not_stop_at_deep_directory(tmp_path, monkeypatch):
    for i in range(5):
        (tmp_path / f"d{i}" / "b" / "c" / "d").mkdir(parents=True)
        (tmp_path / f"d{i}" / "f.xlsx").write_text("x")
    (tmp_path / "node_modules" / "p").mkdir(parents=True)
    (tmp_path / "node_modules" / "p" / "x.xlsx").write_text("x")
    monkeypatch.setattr(FR, "_allowed", lambda f: tmp_path)
    assert FR.scan(str(tmp_path))["files"] == 5


def test_clear_chats_beyond_listing_cap(sessions):
    for i in range(450):
        _save(sessions, f"c{i:03d}")
    _save(sessions, "sub-1")
    assert SV2.clear_chats() == 450
    assert [p.name for p in sessions.iterdir()] == ["sub-1"]


def test_listing_and_search_skip_rows_without_id(sessions):
    _save(sessions, "good", [{"role": "user", "content": "findme"}])
    (sessions / "bad").mkdir()
    (sessions / "bad" / "session.json").write_text(json.dumps({"messages": [{"role": "user", "content": "findme"}]}))
    (sessions / "list").mkdir()
    (sessions / "list" / "session.json").write_text("[1]")
    assert "good" in {r["id"] for r in A.listing()}
    assert [h["id"] for h in A.search("findme")] == ["good"]


def test_rename_to_blank_clears_title(sessions):
    _save(sessions, "t")
    A.thread_action("t", {"action": "rename", "title": "Mine"})
    assert A.read_meta("t")["title"] == "Mine"
    A.thread_action("t", {"action": "rename", "title": "  "})
    assert not A.read_meta("t").get("title")


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def test_git_status_paths_with_spaces(tmp_path, monkeypatch):
    if not W.git_exe():
        pytest.skip("no git")
    _git(tmp_path, "init")
    (tmp_path / "my file.txt").write_text("x")
    (tmp_path / "old name.txt").write_text("y")
    _git(tmp_path, "add", "old name.txt")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "m")
    _git(tmp_path, "mv", "old name.txt", "new name.txt")
    monkeypatch.setattr(W, "check_root", lambda r: tmp_path)
    paths = sorted(f["path"] for f in W.git_status(str(tmp_path))["files"])
    assert paths == ["my file.txt", "new name.txt"]


def test_git_timeout_is_readable(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "git_exe", lambda: "git")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 1)
    monkeypatch.setattr(W.subprocess, "run", boom)
    with pytest.raises(ValueError, match="took too long"):
        W._git(tmp_path, "push", timeout=1)


@pytest.mark.skipif(os.name == "nt", reason="posix process groups")
def test_term_stop_all_kills_running_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: tmp_path)
    monkeypatch.setattr(W, "_shell_cmd", lambda c, s="": [sys.executable, "-c", c])
    v = W.term_run(str(tmp_path), "import time; time.sleep(60)")
    t = W.TERMS[v["id"]]
    W.term_stop_all()
    t["p"].wait(5)
    assert t["p"].poll() is not None


def test_scheduled_run_with_missing_folder_fails(tmp_path):
    with pytest.raises(ValueError, match="moved or deleted"):
        A.run_scheduled({"id": "t", "prompt": "p", "folder": str(tmp_path / "gone")})


def test_test_connection_strips_bearer_prefix(monkeypatch):
    from meshpool_harness import cli
    from meshpool_harness.ui import server as S
    seen = {}
    monkeypatch.setattr(cli, "ping", lambda model, base=None, key=None: seen.update(key=key) or {"ok": True})
    monkeypatch.setattr(S, "_key_follows_host", lambda v: None)
    S.test_connection({"MESHPOOL_API_KEY": "  Bearer sk-abcdefghijklmnopqrstuvwxyz0123456789 "})
    assert seen.get("key") == "sk-abcdefghijklmnopqrstuvwxyz0123456789"
