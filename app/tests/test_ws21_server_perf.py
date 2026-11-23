"""ws21: P-04 (LibreOffice tree kill), P-07/H-14 (listing, Usage, search and resume from the small head), P-13 (listen
backlog), H-23 (log rotation), M26-01 (terminal commands die at quit)."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from meshpool_harness import logfiles
from meshpool_harness import session_store as SS
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import server as S
from meshpool_harness.ui import workbench as W

posix = pytest.mark.skipif(os.name == "nt", reason="process groups")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                            # a zombie is already dead
        return "Z" not in Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return True


def test_p13_the_listen_backlog_is_128():
    assert S._Server.request_queue_size == 128


@posix
def test_p04_a_hung_libreoffice_is_killed_with_everything_it_started(tmp_path, monkeypatch):
    from engines.forge import common as FC
    pidfile = tmp_path / "child.pid"
    fake = tmp_path / "soffice"
    fake.write_text(f"#!/bin/sh\nsleep 300 &\necho $! > {pidfile}\nsleep 300\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(FC, "find_soffice", lambda: str(fake))
    src = tmp_path / "x.docx"
    src.write_bytes(b"junk")
    t0 = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        FC.soffice_convert(str(src), "pdf", str(tmp_path / "out"), timeout=1)
    assert time.time() - t0 < 20
    pid = int(pidfile.read_text())
    for _ in range(40):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid), "the process LibreOffice started is still running"


@posix
def test_p04_kill_all_soffice_stops_runs_in_flight(tmp_path):
    import threading
    from engines.forge import common as FC
    pidfile = tmp_path / "p.pid"
    fake = tmp_path / "run.sh"
    fake.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nsleep 300\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    res = []
    th = threading.Thread(target=lambda: res.append(FC._run_tree([str(fake)], timeout=120)), daemon=True)
    th.start()
    for _ in range(50):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.1)
    pid = int(pidfile.read_text())
    FC.kill_all_soffice()
    th.join(10)
    assert not th.is_alive() and not _alive(pid)


def test_h23_a_rotating_file_keeps_a_few_files_of_bounded_size(tmp_path):
    f = logfiles.RotatingFile(tmp_path / "app.log", max_bytes=2000, backups=2)
    for i in range(300):
        f.write(f"line {i} " + "x" * 40 + "\n")
    f.close()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["app.log", "app.log.1", "app.log.2"], files
    assert all(p.stat().st_size <= 2100 for p in tmp_path.iterdir())


@posix
def test_h23_a_child_process_log_rotates_while_it_runs(tmp_path):
    log = logfiles.PipedLog(tmp_path / "srv.log", max_bytes=20_000, backups=2)
    p = subprocess.Popen([sys.executable, "-c", "import sys\nfor i in range(3000): sys.stderr.write('e%d %s\\n' % (i, 'y' * 60))"],
                         stderr=log)
    log.release()
    p.wait(30)
    log.flush()
    time.sleep(0.3)
    log.flush()
    files = sorted(q.name for q in tmp_path.iterdir())
    assert "srv.log.1" in files and "srv.log.3" not in files, files
    assert sum(q.stat().st_size for q in tmp_path.iterdir()) < 3 * 21_000 + 200
    log.close()


def test_h23_mcp_and_app_logs_go_through_the_rotating_writer():
    root = Path(__file__).resolve().parents[1] / "meshpool_harness"
    assert "PipedLog" in (root / "agent" / "mcp.py").read_text(encoding="utf-8")
    assert "logfiles" in (root / "ui" / "desktop.py").read_text(encoding="utf-8")


@posix
def test_m26_01_quit_kills_terminal_commands_even_when_a_chat_cannot_close(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: tmp_path)
    monkeypatch.setattr(W, "_shell_cmd", lambda c, s="": [sys.executable, "-c", c])
    v = W.term_run(str(tmp_path), "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)")
    t = W.TERMS[v["id"]]

    class Bad:
        def close(self):
            raise RuntimeError("cannot close")
    monkeypatch.setitem(A.LIVE, "bad-chat", Bad())
    try:
        A.close_all()
    finally:
        A.LIVE.pop("bad-chat", None)
    t["p"].wait(10)
    assert t["p"].poll() is not None


# ---- P-07 / H-14 ------------------------------------------------------------------------------------------------
@pytest.fixture
def sessions(tmp_path, monkeypatch):
    s = tmp_path / "sessions"
    s.mkdir()
    monkeypatch.setattr(A, "SESSIONS", s)
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    A._METAS.clear()
    A._ROWS.clear()
    A.list_dirty()
    return s


def _chat(root: Path, i: int, text: str = "hello", workdir: str = "/w"):
    d = root / f"c{i:04d}"
    SS.write_snapshot(d, {"id": d.name, "title": f"Chat {i}", "workdir": workdir, "updated": 1000 + i, "harness": "meshpool-code",
                          "model": "m", "usage": {"in": 10, "cached": 0, "out": 5, "calls": 1},
                          "messages": [{"role": "user", "content": f"{text} {i}"}]})


def test_p07_the_list_has_no_400_cap_and_pages_with_load_more(sessions):
    for i in range(450):
        _chat(sessions, i)
    assert len(A.listing()) == 450
    page = A.listing_page(limit=300, offset=0)
    assert len(page["sessions"]) == 300 and page["more"] is True and page["total"] == 450
    assert page["sessions"][0]["updated"] > page["sessions"][-1]["updated"]
    rest = A.listing_page(limit=300, offset=300)
    assert len(rest["sessions"]) == 150 and rest["more"] is False


def test_h14_usage_reads_the_head_not_every_chat(sessions, monkeypatch):
    for i in range(12):
        _chat(sessions, i)

    monkeypatch.setattr("meshpool_harness.agent.core.SESSIONS", sessions)
    full = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: full.append(self.name) or real(self, *a, **k))
    r = W.usage()
    assert r["total"]["in"] == 120 and len(r["threads"]) == 12
    assert "session.json" not in full, "a chat was read in full"


def test_h14_search_skips_chats_that_cannot_match_without_parsing_them(sessions, monkeypatch):
    for i in range(30):
        _chat(sessions, i)
    _chat(sessions, 99, text="needle-word")
    reads = []
    real = SS.read
    monkeypatch.setattr(SS, "read", lambda d: reads.append(d) or real(d))
    hits = A.search("needle-word")
    assert [h["id"] for h in hits] == ["c0099"] and len(reads) == 1


def test_h14_resume_reads_only_the_chat_it_resumes(sessions, monkeypatch, tmp_path):
    from meshpool_harness.agent.core import Agent
    work = tmp_path / "w"
    work.mkdir()
    for i in range(15):
        _chat(sessions, i, workdir=str(work) if i == 7 else "/other")
    monkeypatch.setattr("meshpool_harness.agent.core.SESSIONS", sessions)
    reads = []
    real = SS.read
    monkeypatch.setattr(SS, "read", lambda d: reads.append(Path(d).name) or real(d))
    try:
        Agent.resume(str(work), model="m")
    except Exception:  # noqa: BLE001 - building the agent itself is not under test
        pass
    assert reads.count("c0007") >= 1 and len(set(reads)) == 1, reads


def test_h29_the_server_reuses_its_address_except_on_windows():
    assert S._Server.allow_reuse_address == (os.name != "nt")


@posix
def test_m26_02_terminal_stop_escalates_to_sigkill(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: tmp_path)
    monkeypatch.setattr(W, "_shell_cmd", lambda c, s="": [sys.executable, "-c", c])
    v = W.term_run(str(tmp_path), "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)")
    t = W.TERMS[v["id"]]
    time.sleep(0.5)
    W._term_kill(t, wait=0.5)
    t["p"].wait(10)
    assert t["p"].poll() == -9
