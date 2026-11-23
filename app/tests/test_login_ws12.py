"""ws12 sign-in process: M15-03 (one tracked ChatGPT sign-in, stopped on timeout and quit), F-11 (meshpool login
without a terminal does not say there is no key)."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from meshpool_harness import config as C
from meshpool_harness import harnesses as H

APP = Path(__file__).resolve().parents[1]
posix = pytest.mark.skipif(os.name == "nt", reason="fake npx is a shell script")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def wait_dead(pid: int, secs: float = 5) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


@pytest.fixture
def fake_npx(tmp_path, monkeypatch):
    """A fake `npx` that records its pid and its child's pid, and stays up like a waiting `codex login`."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    starts = tmp_path / "starts.txt"
    script = bindir / "npx"
    script.write_text(f"#!/bin/sh\nsleep 60 &\necho \"$$ $!\" >> {starts}\nsleep 60\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    monkeypatch.setattr(H, "unsupported", lambda hid: "")
    monkeypatch.setattr(H, "npx", lambda: str(script))
    getattr(H, "stop_login", lambda: None)()
    yield starts
    getattr(H, "stop_login", lambda: None)()
    for line in (starts.read_text().splitlines() if starts.exists() else []):
        for pid in map(int, line.split()):
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass


def recorded(starts):
    time.sleep(0.3)
    return [tuple(map(int, ln.split())) for ln in starts.read_text().splitlines()] if starts.exists() else []


@posix
def test_m15_03_double_click_starts_one(fake_npx):
    a = H.login("codex")
    b = H.login("codex")
    assert a["ok"] and not a.get("already")
    assert b["ok"] and b["already"] is True
    assert len(recorded(fake_npx)) == 1


@posix
def test_m15_03_stop_login_kills_tree(fake_npx):
    H.login("codex")
    (npx_pid, child), = recorded(fake_npx)
    pidfile = C.home() / "codex-login.json"
    assert pidfile.is_file() and alive(child)
    getattr(H, "stop_login", lambda: None)()
    assert wait_dead(npx_pid) and wait_dead(child)
    assert not pidfile.exists()
    assert not H.login("codex").get("already")      # and a new sign-in can start


@posix
def test_m15_03_timeout_stops_it(fake_npx, monkeypatch):
    monkeypatch.setattr(H, "LOGIN_TIMEOUT_S", 1)
    H.login("codex")
    (npx_pid, child), = recorded(fake_npx)
    assert wait_dead(npx_pid, 8) and wait_dead(child, 8)
    end = time.time() + 5
    while time.time() < end and (H._LOGIN["proc"] is not None or (C.home() / "codex-login.json").exists()):
        time.sleep(0.05)
    assert H._LOGIN["proc"] is None and not (C.home() / "codex-login.json").exists()
    assert not H.login("codex").get("already")
    assert len(recorded(fake_npx)) == 2


@posix
def test_m15_03_second_instance_sees_running_login(fake_npx):
    from meshpool_harness import fsio
    other = subprocess.Popen(["sleep", "60"])
    try:
        fsio.atomic_write_json(C.home() / "codex-login.json", {"pid": other.pid, "started": time.time()})
        r = H.login("codex")
        assert r["already"] is True and not recorded(fake_npx)
        other.kill()
        other.wait()
        r = H.login("codex")                              # the recorded pid is dead: stale, so it starts
        assert not r.get("already") and len(recorded(fake_npx)) == 1
    finally:
        other.kill()
        other.wait()


def test_m15_03_stale_record_with_reused_pid_does_not_block(fake_npx):
    """A record left by a crashed app whose pid now belongs to an unrelated process must not block sign-in forever."""
    from meshpool_harness import fsio
    other = subprocess.Popen(["sleep", "60"])
    try:
        fsio.atomic_write_json(C.home() / "codex-login.json",
                               {"pid": other.pid, "started": time.time() - H.LOGIN_TIMEOUT_S - 120})
        r = H.login("codex")
        assert not r.get("already") and len(recorded(fake_npx)) == 1
        (C.home() / "codex-login.json").write_text("{")          # a damaged record names no sign-in
        getattr(H, "stop_login", lambda: None)()
        r = H.login("codex")
        assert not r.get("already")
    finally:
        other.kill()
        other.wait()


# ---- F-11 -----------------------------------------------------------------------------------------------------

def run_login(tmp_path):
    env = {**os.environ, "MESHPOOL_HOME": str(tmp_path), "CODEX_HOME": str(tmp_path), "HOME": str(tmp_path)}
    for k in ("MESHPOOL_API_KEY", "OPENAI_API_KEY", "MESHPOOL_CONFIG", "MESHPOOL_BASE_URL"):
        env.pop(k, None)
    return subprocess.run([sys.executable, "-m", "meshpool_harness", "login"], cwd=APP, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)


def test_f11_login_without_terminal_does_not_claim_no_key(tmp_path):
    (tmp_path / "meshpool.config").write_text("MESHPOOL_API_KEY=sk-test-1234\n")
    r = run_login(tmp_path)
    out = r.stdout + r.stderr
    assert "No API key" not in out
    assert "terminal" in out
    assert r.returncode == 1


def test_f11_login_without_terminal_and_no_key_says_terminal_needed(tmp_path):
    r = run_login(tmp_path)
    out = r.stdout + r.stderr
    assert "terminal" in out and "MESHPOOL_API_KEY" in out
    assert "No API key" not in out
    assert r.returncode == 1
