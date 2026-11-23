import json
import subprocess
import threading
import time

import pytest

from meshpool_harness import config as C
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import previews, receipt_api, server, workbench as W


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    sessions = tmp_path / "mh" / "runs" / "sessions"
    monkeypatch.setitem(A.Agent.__init__.__globals__, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(A.Session, "_start_mcp", lambda self: self._mcp_ready.set())
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    A._METAS.clear()
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def test_concurrent_get_builds_one_session(monkeypatch):
    built = []

    class Fake:
        def __init__(self, resume_id=None):
            built.append(resume_id)
            time.sleep(0.2)

        def close(self):
            built.append("closed")
    monkeypatch.setattr(A, "Session", Fake)
    monkeypatch.setattr(A, "LIVE", {})
    res = []
    ts = [threading.Thread(target=lambda: res.append(A.get("same-1"))) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert built == ["same-1"] and len({id(r) for r in res}) == 1


def test_send_failure_shows_error_and_keeps_message(chat):
    s = A.Session(workdir=str(chat), harness="meshpool-code", kind="code")

    def boom(*a, **k):
        raise KeyError("data")
    s.agent.send = boom
    s._start_turn("hello there", None)
    end = time.time() + 10
    while s.state in ("working", "waiting") and time.time() < end:
        time.sleep(0.05)
    types = [e["type"] for e in s.events]
    assert "error" in types and any(e["type"] == "user" and "hello there" in e["text"] for e in s.events)
    assert s.state == "idle"


def test_actions_on_missing_chat_are_404(chat):
    with pytest.raises(KeyError):
        A.thread_action("ghost-1", {"action": "pin"})
    code, _ = receipt_api.handle("POST", type("U", (), {"path": "/api/receipt/feedback"})(),
                                 json.dumps({"chat": "ghost-1", "outcome": "up"}).encode())
    assert code == 404


def test_save_settings_rejects_bad_base_url():
    with pytest.raises(ValueError, match="http"):
        server.save_settings({"values": {"MESHPOOL_BASE_URL": "not a url"}})


def test_attach_keeps_dotfiles_and_limits_length(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: tmp_path)
    assert W.attach(str(tmp_path), ".gitignore", b"x")["path"] == ".gitignore"
    r = W.attach(str(tmp_path), "a" * 300 + ".txt", b"x")
    assert r["path"].endswith(".txt") and len(r["path"].encode()) <= 255


def test_write_file_to_folder_is_a_value_error(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: tmp_path)
    (tmp_path / "w").mkdir()
    with pytest.raises(ValueError, match="folder"):
        W.write_file(str(tmp_path), "w", "x")


def test_detached_head_can_create_branch(tmp_path, monkeypatch):
    if not W.git_exe():
        pytest.skip("no git")
    monkeypatch.setattr(W, "check_root", lambda r: tmp_path)

    def g(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path, check=True,
                       capture_output=True)
    g("init")
    (tmp_path / "a").write_text("1")
    g("add", "a")
    g("commit", "-m", "1")
    g("checkout", "--detach")
    (tmp_path / "a").write_text("2")
    g("commit", "-am", "2")
    st = W.git_action(str(tmp_path), {"action": "checkout", "branch": "feat/x", "create": True})
    assert st["branch"] == "feat/x"
    g("checkout", "--detach")
    with pytest.raises(ValueError, match="branch first"):
        W.git_action(str(tmp_path), {"action": "push"})


def test_csv_with_huge_cell_previews(tmp_path):
    p = tmp_path / "big.csv"
    p.write_text("a,b\n" + "x" * 300_000 + ",2\n", encoding="utf-8")
    sh = previews._csv_sheet(p)
    assert sh[0]["rows"][1][1] == "2" and len(sh[0]["rows"][1][0]) <= 2000


def test_list_dir_reports_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: tmp_path)
    for i in range(2010):
        (tmp_path / f"f{i}").write_text("")
    r = W.list_dir(str(tmp_path), "")
    assert r["truncated"] and len(r["entries"]) == 2000


def test_json_body_must_be_object():
    h = server.Handler.__new__(server.Handler)
    h._body = lambda: b"[1]"
    with pytest.raises(ValueError):
        h._json()


def test_missing_xdg_open_is_readable(monkeypatch, tmp_path):
    from meshpool_harness.ui import desktop
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.os, "name", "posix")

    def nope(*a, **k):
        raise FileNotFoundError("xdg-open")
    monkeypatch.setattr(desktop.subprocess, "Popen", nope)
    with pytest.raises(ValueError, match="file manager"):
        desktop.reveal(tmp_path)
