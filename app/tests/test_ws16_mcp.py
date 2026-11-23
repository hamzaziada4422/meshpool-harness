"""ws16: MCP lifecycle. Stop and timeouts cancel the call at the server, a dead server is restarted once, a start that
only timed out is not cached as failed, close during a start leaves no process, pipes and process trees are cleaned
up, the terminal's environment reaches servers, many chats keep the helper count bounded."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness.agent import mcp as M  # noqa: E402

SERVER = str(Path(__file__).resolve().parent / "ws16_stdio_server.py")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("MH_LOGIN_ENV", "0")
    monkeypatch.setenv("WS16_DIR", str(tmp_path))
    yield tmp_path
    M.stop_all()


def _spec(home, **kw):
    return {"command": sys.executable, "args": [SERVER, str(home)], **kw}


def _server(home, name="ws16", **kw):
    s = M.Server(name, _spec(home, **kw), home / "logs", [str(home)])
    s.start(20)
    assert s.status == "connected", s.error
    return s


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                         # a zombie is not running
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except OSError:
        return True


def _wait(cond, t=8.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def _log(home) -> str:
    f = home / "server.log"
    return f.read_text() if f.exists() else ""


# ---------- M22-01 / G-13 / OSS-22: Stop and timeout send notifications/cancelled ----------

def test_stop_sends_cancelled_to_stdio_server(home):
    s = _server(home)
    box = {}
    def go():
        try:
            s.call_with_images("slow", {})
        except M.Cancelled as e:
            box["e"] = str(e)
    th = threading.Thread(target=go, daemon=True)
    th.start()
    time.sleep(0.5)
    assert s.abort_calls() == 1
    th.join(5)
    assert not th.is_alive() and "cancelled" in box["e"]
    assert _wait(lambda: "cancelled" in _log(home))
    assert "ran slow to the end" not in _log(home)
    s.close()


def test_manager_reports_stopped_call_and_possible_partial_work(home):
    m = M.Manager(roots=[str(home)], path=home / "none.json", extra={"ws16": _spec(home)}).start(use_health=False)
    name = next(n for n in m.map if n.endswith("slow"))
    box = {}
    th = threading.Thread(target=lambda: box.update(r=m.call_with_images(name, {})), daemon=True)
    th.start()
    time.sleep(0.5)
    m.abort_calls()
    th.join(5)
    (text, failed), _ = box["r"]
    assert failed and "Stopped" in text and "file" in text
    m.close()


def test_timeout_raises_and_cancels(home):
    s = _server(home, timeout=0.6)
    with pytest.raises(M.MCPError, match="no reply"):
        s.call_with_images("slow", {})
    assert _wait(lambda: "cancelled" in _log(home))
    s.close()


# ---------- M22-04: a dead server is restarted once ----------

def test_dead_idle_server_restarts_on_next_call(home):
    s = _server(home)
    pid = int(s.call("pid", {})[0])
    os.kill(pid, signal.SIGKILL)
    assert _wait(lambda: not s._entry.t.alive)
    text, failed = s.call("echo", {})
    assert (text, failed) == ("ok", False)
    assert int(s.call("pid", {})[0]) != pid
    s.close()


def test_write_to_dead_server_restarts_and_retries_once(home):
    s = _server(home)
    pid = int(s.call("pid", {})[0])
    os.kill(pid, signal.SIGKILL)
    _wait(lambda: not _alive(pid))
    s._entry.t.alive = True                        # the reader has not noticed yet: the write is what fails
    text, failed = s.call("echo", {})
    assert (text, failed) == ("ok", False)
    s.close()


def test_server_that_dies_during_a_call_is_not_retried(home):
    s = _server(home)
    with pytest.raises(M.MCPError, match="exited"):
        s.call("die", {})
    s.close()


def test_private_server_restarts_when_dead(home):
    s = _server(home, name="browser-ws16")         # "browser" servers are not pooled
    assert s._entry is None
    pid = int(s.call("pid", {})[0])
    os.kill(pid, signal.SIGKILL)
    assert _wait(lambda: not s.t.alive)
    assert s.call("echo", {}) == ("ok", False)
    s.close()


# ---------- F-06: readable error, never the raw attribute error ----------

def test_exit_reason_is_readable_text(home):
    t = M._Stdio("x", {"command": sys.executable, "args": ["-c", "import sys; print('boom: bad key', file=sys.stderr)"]},
                 home / "logs", [])
    time.sleep(0.5)
    with pytest.raises(M.MCPError) as ei:
        t.request(1, "initialize", {}, 3)
    assert "reader_error" not in str(ei.value) and "exited" in str(ei.value) and "bad key" in str(ei.value)
    t.close()


# ---------- M22-06: a start that only timed out is not remembered as failed ----------

def test_timeouts_are_not_cached_as_failures(home):
    spec = {"command": "x"}
    M.record_health({"slow": (spec, False, "no reply to initialize within 25 s"),
                     "cold": (spec, False, "did not start within 45 s"),
                     "bad": (spec, False, "command not found: x")})
    assert M.recently_failed("slow", spec) is None
    assert M.recently_failed("cold", spec) is None
    assert M.recently_failed("bad", spec) == "command not found: x"


def test_slow_start_times_out_without_a_failure_record(home, monkeypatch):
    monkeypatch.setenv("MH_MCP_INIT_S", "1")
    monkeypatch.setenv("WS16_INIT_DELAY", "4")
    m = M.Manager(roots=[str(home)], path=home / "none.json", extra={"ws16": _spec(home)}).start(timeout=30)
    assert m.servers["ws16"].status == "error"
    assert "no reply" in m.servers["ws16"].error
    assert M.recently_failed("ws16", _spec(home)) is None
    m.close()


# ---------- OSS-21: first response capped at 25 s (configurable); cold starts do not block ----------

def test_first_response_budget(monkeypatch):
    monkeypatch.delenv("MH_MCP_INIT_S", raising=False)
    assert M.first_response_seconds({"command": "python"}) == 25
    assert M.first_response_seconds({"command": "python", "init_timeout": 7}) == 7
    monkeypatch.setenv("MH_MCP_INIT_S", "12")
    assert M.first_response_seconds({"command": "python"}) == 12
    assert M.first_response_seconds({"command": "npx"}) >= 120          # a cold download gets longer, in the background
    assert M.first_response_seconds({"command": "/usr/bin/uvx.exe"}) >= 120


def test_cold_start_reports_still_starting_and_connects_later(home, monkeypatch):
    npx = home / "uvx"
    npx.write_text(f"#!/bin/sh\nexec {sys.executable} {SERVER} {home}\n")
    npx.chmod(0o755)
    monkeypatch.setenv("WS16_INIT_DELAY", "3")
    monkeypatch.setenv("MH_MCP_INIT_S", "1")
    m = M.Manager(roots=[str(home)], path=home / "none.json",
                  extra={"cold": {"command": str(npx), "args": []}}).start(timeout=1, use_health=False)
    st = {r["name"]: r for r in m.status()}["cold"]
    assert st["status"] == "starting" and "still starting" in st["error"]
    assert not m.schemas()
    assert _wait(lambda: m.servers["cold"].status == "connected", 10)
    assert m.schemas()                              # the tools appear without restarting the chat
    m.close()


# ---------- M06-03: close before the start returns leaves no process ----------

def _procs_with(marker: str) -> list[int]:
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if marker.encode() in (d / "cmdline").read_bytes() and int(d.name) != os.getpid():
                    out.append(int(d.name))
            except OSError:
                pass
    return [p for p in out if _alive(p)]


def test_close_during_start_stops_what_it_started(home, monkeypatch):
    monkeypatch.setenv("WS16_INIT_DELAY", "6")
    m = M.Manager(roots=[str(home)], path=home / "none.json", extra={"ws16": _spec(home), "browser-ws16": _spec(home)})
    th = threading.Thread(target=lambda: m.start(timeout=30), daemon=True)
    th.start()
    assert _wait(lambda: _procs_with(str(home)), 5)
    t0 = time.time()
    m.close()
    assert time.time() - t0 < 4, "close waited for the start to finish"
    th.join(10)
    assert _wait(lambda: not _procs_with(str(home)), 8), _procs_with(str(home))


# ---------- P-10: pipes are closed with the helper ----------

def _fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_fd_count_returns_to_baseline(home):
    s = _server(home)
    s.close()
    M.stop_all()
    base = _fds()
    for i in range(6):
        s = _server(home, name=f"fd{i}")
        s.call("echo", {})
        s.close()
    assert _wait(lambda: _fds() <= base + 1)


# ---------- M27-02 / H-18: the whole process tree ends ----------

def test_close_kills_children_of_the_server(home):
    s = _server(home)
    child = int(s.call("spawn", {})[0])
    assert _alive(child)
    s.close()
    assert _wait(lambda: not _alive(child)), "the child of the server survived"


def test_windows_uses_taskkill_tree(monkeypatch):
    calls = []

    class P:
        pid = 4242

        def poll(self):
            return None

        def kill(self):
            calls.append("kill")

    from meshpool_harness.core import tools as T
    monkeypatch.setattr(T.subprocess, "run", lambda a, **k: calls.append(a))
    monkeypatch.setattr(os, "name", "nt")
    try:
        M.kill_tree(P())
    finally:
        monkeypatch.undo()
    assert calls and calls[0][:3] == ["taskkill", "/F", "/T"] and "4242" in calls[0]


# ---------- P-09: the reaper runs from app start ----------

def test_reaper_runs_without_any_mcp_use(monkeypatch):
    M._REAPER_STOP.set()
    time.sleep(0.7)
    M._REAPER.clear()
    M._REAPER_THREAD.clear()
    M.start_reaper()
    assert M._REAPER_THREAD and M._REAPER_THREAD[0].is_alive()
    time.sleep(1.2)                                 # an empty pool must not end it
    assert M._REAPER_THREAD[0].is_alive()


def test_idle_helper_is_reaped(home, monkeypatch):
    monkeypatch.setenv("MH_MCP_IDLE_S", "1")
    M._REAPER_STOP.set()                            # a reaper started earlier still sleeps on the old interval
    time.sleep(0.7)
    M._REAPER.clear()
    M._REAPER_THREAD.clear()
    M.start_reaper()
    s = _server(home)
    pid = int(s.call("pid", {})[0])
    assert _wait(lambda: not _alive(pid), 10)
    assert s.call("echo", {}) == ("ok", False)      # and starts again on the next call
    s.close()


# ---------- S-13 / B11 / P-08 / H-20: many chats, bounded helpers, higher fd limit ----------

def test_raise_file_limit(monkeypatch):
    import resource
    seen = []
    monkeypatch.setattr(resource, "getrlimit", lambda r: (256, 10240))
    monkeypatch.setattr(resource, "setrlimit", lambda r, v: seen.append(v))
    M.raise_file_limit()
    assert seen == [(4096, 10240)]
    seen.clear()
    monkeypatch.setattr(resource, "getrlimit", lambda r: (256, 1024))
    M.raise_file_limit()
    assert seen == [(1024, 1024)]
    seen.clear()
    monkeypatch.setattr(resource, "getrlimit", lambda r: (8192, 8192))
    M.raise_file_limit()
    assert seen == []


def test_thirty_chats_share_bounded_helpers(home):
    ms = [M.Manager(roots=[str(home)], path=home / "none.json", extra={"ws16": _spec(home)}, chat=f"c{i}").start()
          for i in range(30)]
    for m in ms:
        name = next(n for n in m.map if n.endswith("echo"))
        assert m.call(name, {}) == ("ok", False)
    assert len(_procs_with(SERVER)) <= 2           # one shared helper, not thirty
    base = _fds()
    for m in ms:
        m.close()
    assert _wait(lambda: not _procs_with(SERVER), 8)
    assert _fds() <= base


# ---------- H-16: the terminal's environment ----------

def test_login_shell_env_is_imported_once(home, monkeypatch):
    fake = home / "fakesh"
    fake.write_text('#!/bin/sh\nexec env FAKE_FROM_RC=hello PATH="/opt/fake/bin:$PATH" /bin/sh -c "$2"\n')
    fake.chmod(0o755)
    monkeypatch.setenv("MH_LOGIN_ENV", "1")
    monkeypatch.setenv("MH_LOGIN_SHELL", str(fake))
    monkeypatch.delenv("MESHPOOL_RESOLVING_ENV", raising=False)
    M._LOGIN_ENV.clear()
    try:
        env = M.child_env()
        assert env["FAKE_FROM_RC"] == "hello"
        assert env["PATH"].startswith("/opt/fake/bin")
        assert "MESHPOOL_RESOLVING_ENV" not in env
        assert M.login_shell_env() is M.login_shell_env()           # resolved once
    finally:
        M._LOGIN_ENV.clear()


def test_only_meshpool_provider_keys_are_withheld(home, monkeypatch):
    seen = {}

    def fake_popen(args, **kw):
        seen.update(kw["env"])
        raise FileNotFoundError

    monkeypatch.setattr(M.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("MESHPOOL_API_KEY", "mine")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "from-terminal")
    monkeypatch.setattr(M, "login_shell_env", lambda: {"MY_SERVICE_TOKEN": "from-terminal"})
    with pytest.raises(M.MCPError):
        M._Stdio("x", {"command": "sh", "args": []}, home / "logs", [])
    assert "MESHPOOL_API_KEY" not in seen
    assert seen["MY_SERVICE_TOKEN"] == "from-terminal"
