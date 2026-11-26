"""SR-10: a ChatGPT sign-in (npm exec @openai/codex login) left running for good.

A meshpool that was killed (or crashed) could not stop the sign-in it had started: its watchdog thread and its quit
hook died with it, and the next meshpool only saw "a sign-in is already open". The sign-in now carries its own time
limit (coreutils timeout), and a new meshpool stops one whose app is gone. Fake codex commands only."""
import os
import shutil
import subprocess
import sys
import time

import pytest

from meshpool_harness import config as C
from meshpool_harness import fsio
from meshpool_harness import harnesses as H
from test_login_ws12 import alive, fake_npx, recorded, wait_dead  # noqa: F401

posix = pytest.mark.skipif(os.name == "nt", reason="fake npx is a shell script")


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


@posix
def test_a_login_left_by_a_killed_app_is_stopped(fake_npx):  # noqa: F811
    left = subprocess.Popen([H.npx(), "-y", H.CODEX_CLI, "login"], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        fsio.atomic_write_json(C.home() / "codex-login.json",
                               {"pid": left.pid, "started": time.time(), "owner": _dead_pid()})
        assert H.reap_stale_login() is True
        assert wait_dead(left.pid) and not (C.home() / "codex-login.json").exists()
        (npx_pid, child), = recorded(fake_npx)
        assert wait_dead(child)                          # and what it started
    finally:
        left.kill()
        left.wait()


@posix
def test_a_login_of_a_running_app_or_another_process_is_left_alone(fake_npx):  # noqa: F811
    left = subprocess.Popen([H.npx(), "-y", H.CODEX_CLI, "login"], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    other = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        fsio.atomic_write_json(C.home() / "codex-login.json",
                               {"pid": left.pid, "started": time.time(), "owner": os.getppid()})
        assert H.reap_stale_login() is False and alive(left.pid)          # its app is still running
        fsio.atomic_write_json(C.home() / "codex-login.json",
                               {"pid": other.pid, "started": time.time(), "owner": _dead_pid()})
        assert H.reap_stale_login() is False and alive(other.pid)         # the pid is not a sign-in any more
    finally:
        for p in (left, other):
            p.kill()
            p.wait()


@posix
def test_the_login_stops_by_itself_without_the_apps_watchdog(fake_npx, monkeypatch):  # noqa: F811
    if not shutil.which("timeout"):
        pytest.skip("no coreutils timeout here")
    monkeypatch.setattr(H, "LOGIN_TIMEOUT_S", 1)
    monkeypatch.setattr(H, "_login_watchdog", lambda proc: None)          # as if the app had been killed
    H.login("codex")
    (npx_pid, child), = recorded(fake_npx)
    assert wait_dead(npx_pid, 8) and wait_dead(child, 8)


@posix
def test_the_stop_route_stops_the_login(fake_npx, monkeypatch):  # noqa: F811
    """POST /api/harness-login/stop: the page calls it when the user picks another option or onboarding ends."""
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from meshpool_harness.ui import server as S
    H.login("codex")
    (npx_pid, child), = recorded(fake_npx)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/harness-login/stop", data=b"{}",
                                     headers={"X-Meshpool-Token": S.TOKEN, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            assert json.loads(r.read())["ok"] is True
        assert wait_dead(npx_pid) and wait_dead(child)
    finally:
        httpd.shutdown()
