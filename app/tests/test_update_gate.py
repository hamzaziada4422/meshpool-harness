"""The update run gate and restart handshake (M11-02, M11-03, M12-03, M11-07, M12-02)."""
import json
import os
import subprocess
import sys
import threading
import time
import types
import urllib.request

import pytest

from meshpool_harness import assistant as AS
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import server as SV


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    yield
    if hasattr(A, "resume"):
        A.resume()


def _session(state="idle"):
    s = object.__new__(A.Session)
    s.state = state
    s.lock = threading.RLock()
    s.harness = {"type": "auto"}
    s.agent = types.SimpleNamespace(model="x", grade_now=lambda: None)
    s._capability_turn = lambda text: []
    return s


def test_turn_started_during_update_is_refused(monkeypatch):
    busy = types.SimpleNamespace(state="working")
    monkeypatch.setitem(A.LIVE, "busy", busy)
    assert A.quiesce() is False                      # a working chat keeps the gate open
    assert A.QUIESCING[0] is False
    monkeypatch.delitem(A.LIVE, "busy")
    assert A.quiesce() is True
    s = _session()
    with pytest.raises(ValueError, match="restarting to install an update"):
        s._start_turn("hello", None)
    with pytest.raises(ValueError, match="restarting to install an update"):
        s.grade()
    assert s.state == "idle"                         # nothing was claimed
    A.resume()
    A._claim_working(s)                              # the gate is open again
    assert s.state == "working"


def test_scheduler_waits_while_quiescing(monkeypatch):
    calls = []

    class Stop(BaseException):
        pass

    def stop(_):
        raise Stop()

    monkeypatch.setattr(AS, "due", lambda: calls.append(1) or [])
    monkeypatch.setattr(time, "sleep", stop)
    assert A.quiesce()
    with pytest.raises(Stop):
        A.scheduler_loop()
    assert calls == []                               # due tasks were not even asked for
    A.resume()
    with pytest.raises(Stop):
        A.scheduler_loop()
    assert calls == [1]


def test_restarted_copy_waits_for_old_pid(monkeypatch):
    from meshpool_harness import cli
    from meshpool_harness import updater as UP
    from meshpool_harness.ui import desktop as D
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(4)"])
    try:
        def wait_for_exit(pid, timeout):
            end = time.time() + timeout
            while time.time() < end:
                if child.poll() is not None:
                    return True
                time.sleep(0.05)
            return False
        monkeypatch.setattr(UP, "wait_for_exit", wait_for_exit, raising=False)
        monkeypatch.setattr(D, "run", lambda: 0)
        monkeypatch.setenv("MESHPOOL_RESTART_WAIT", str(child.pid))
        monkeypatch.delenv("MESHPOOL_RESTARTED", raising=False)
        assert cli.main(["app"]) == 0
        assert child.poll() is not None, "main returned while the old process was still alive"
        assert os.environ.get("MESHPOOL_RESTARTED") == "1"
        assert "MESHPOOL_RESTART_WAIT" not in os.environ
    finally:
        child.kill()
        os.environ.pop("MESHPOOL_RESTARTED", None)


def test_desktop_restart_waits_for_old_instance(monkeypatch):
    from meshpool_harness import config as C
    from meshpool_harness.ui import desktop as D
    answers = iter(["http://127.0.0.1:1/"] * 3)
    asked = []

    def running():
        asked.append(1)
        return next(answers, None)
    shown = []
    for name in ("_hide_child_consoles", "_log_to_file", "_bundled_tools", "ensure_cli"):
        monkeypatch.setattr(D, name, lambda *a, **k: None)
    monkeypatch.setattr(C, "ensure_home", lambda: None)
    monkeypatch.setattr(C, "load_into_env", lambda *a, **k: None)
    C.runs_dir().mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(D, "_running_instance", running)
    monkeypatch.setattr(D, "_show_running", lambda url: shown.append(url) or True)
    monkeypatch.setattr(D, "open_window", lambda url: shown.append(url))
    monkeypatch.setattr(D, "_native_window", lambda url: True)
    monkeypatch.setattr(D, "_window_process", lambda url: "closed")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(SV, "ON_SHOW", [])
    monkeypatch.setattr(SV, "ON_QUIT", [])
    monkeypatch.setattr(SV, "serve", lambda port, **kw: (kw["on_ready"](port), 0)[1])
    monkeypatch.setattr(SV, "_shutdown", lambda: None)
    monkeypatch.setenv("MESHPOOL_RESTARTED", "1")
    assert D.run() == 0
    assert shown == []                               # the old instance was waited out, not shown and exited on
    assert len(asked) == 4
    assert "MESHPOOL_RESTARTED" not in os.environ


@pytest.fixture
def server():
    httpd = SV._Server(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _post(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"X-Meshpool-Token": SV.TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def test_healthz_has_pid_and_boot(server):
    h = _get(server + "/healthz")
    assert h["app"] == "meshpool" and h["pid"] == os.getpid() and h["boot"] == SV.BOOT


def _fake_update(monkeypatch, log, apply_ok=True, gate=None):
    from meshpool_harness import autoupdate as AU
    from meshpool_harness import updater as UP

    class Busy(Exception):
        pass
    lock = threading.Lock()

    class InstallLock:
        def __enter__(self):
            if not lock.acquire(blocking=False):
                raise Busy("An update is already being installed.")

        def __exit__(self, *a):
            lock.release()

    def install():
        log.append("install")
        if gate:
            gate.wait(10)
        return {"ok": True, "action": "staged", "stage": "/s", "tag": "v9", "latest": "9", "packages": []}
    monkeypatch.setattr(UP, "UpdateBusy", Busy, raising=False)
    monkeypatch.setattr(UP, "install_lock", InstallLock, raising=False)
    monkeypatch.setattr(UP, "install", install, raising=False)
    monkeypatch.setattr(UP, "discard_stage", lambda p: log.append("discard"), raising=False)
    monkeypatch.setattr(UP, "apply_staged", lambda st: log.append("apply") or (
        {"ok": True, "action": "restart", "message": "m"} if apply_ok else {"ok": False, "error": "swap failed"}),
        raising=False)
    done = threading.Event()
    monkeypatch.setattr(UP, "restart", lambda on_quit=(): (log.append("restart"), done.set()), raising=False)
    monkeypatch.setattr(AU, "mark_installing", lambda tag, latest, mode: log.append("mark"), raising=False)
    monkeypatch.setattr(AU, "install_finished", lambda ok, error="": log.append(f"finished:{ok}:{error}"), raising=False)
    real_q = A.quiesce
    monkeypatch.setattr(A, "quiesce", lambda: log.append("quiesce") or real_q())
    return done


def test_update_install_endpoint_applies_then_restarts_and_is_exclusive(server, monkeypatch):
    log = []
    gate = threading.Event()
    done = _fake_update(monkeypatch, log, gate=gate)
    first = {}
    t = threading.Thread(target=lambda: first.update(_post(server + "/api/update/install")))
    t.start()
    for _ in range(100):
        if "install" in log:
            break
        time.sleep(0.05)
    second = _post(server + "/api/update/install")          # while the first holds the install lock
    assert second["ok"] is False and "already being installed" in second["error"]
    gate.set()
    t.join(15)
    assert first == {"ok": True, "action": "restarting", "boot": SV.BOOT}
    assert done.wait(5)
    assert log == ["install", "mark", "quiesce", "apply", "restart"]
    assert A.QUIESCING[0] is True                           # the gate stays closed: the process is leaving


def test_failed_apply_reopens_the_gate(server, monkeypatch):
    log = []
    _fake_update(monkeypatch, log, apply_ok=False)
    r = _post(server + "/api/update/install")
    assert r["ok"] is False and r["error"] == "swap failed"
    assert A.QUIESCING[0] is False
    assert "restart" not in log and log[-1] == "finished:False:swap failed"


def test_install_refused_while_a_task_runs(server, monkeypatch):
    log = []
    _fake_update(monkeypatch, log)
    monkeypatch.setitem(A.LIVE, "busy", types.SimpleNamespace(state="working"))
    r = _post(server + "/api/update/install")
    assert r["ok"] is False and "still running" in r["error"]
    assert log == ["install", "mark", "quiesce", "discard",
                   "finished:False:A task is still running. Update when it finishes."]
    assert A.QUIESCING[0] is False


def test_exception_after_mark_installing_is_recorded_and_cleaned_up(server, monkeypatch):
    # M12-02: an error before the gate closed must not leave the state "installing" forever
    log = []
    _fake_update(monkeypatch, log)

    def boom():
        raise RuntimeError("disk exploded")
    monkeypatch.setattr(A, "quiesce", boom)
    r = _post(server + "/api/update/install")
    assert r == {"ok": False, "error": "disk exploded"}
    assert log == ["install", "mark", "discard", "finished:False:disk exploded"]
    assert A.QUIESCING[0] is False


def test_restart_failure_reopens_the_gate_and_says_restart(server, monkeypatch):
    from meshpool_harness import updater as UP
    log = []
    _fake_update(monkeypatch, log)
    done = threading.Event()

    def bad_restart(on_quit=()):
        log.append("restart")
        done.set()
        raise OSError("cannot spawn")
    monkeypatch.setattr(UP, "restart", bad_restart)
    r = _post(server + "/api/update/install")
    assert r["ok"] is True and r["action"] == "restarting"
    assert done.wait(5)
    for _ in range(100):
        if log[-1].startswith("finished"):
            break
        time.sleep(0.05)
    assert log[-1] == ("finished:False:meshpool was updated but could not restart itself. "
                       "Quit and open it again.")
    assert A.QUIESCING[0] is False
