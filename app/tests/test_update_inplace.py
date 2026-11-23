"""The in-place code update: nothing swaps while the app runs, the version on disk decides what is "newer", the swap is
journalled and repaired at the next start, new packages go in after the swap and are rolled back with it, and two
installers cannot interleave. No network: releases are local archives; the kill test uses a real subprocess."""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

from meshpool_harness import updater as U

APP_DIR = Path(__file__).resolve().parents[1]          # the real app folder: where meshpool_recover.py etc. live


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "data"))
    U._CACHE.clear()
    yield
    U._CACHE.clear()


def _release(tmp_path, ver, files=None, name="src.tgz"):
    root = tmp_path / f"rel{ver}" / f"meshpool-harness-{ver}"
    for rel, text in {"app/meshpool_harness/__init__.py": f'__version__ = "{ver}"\n',
                      "app/engines/e.py": f"engine {ver}", **(files or {})}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    tgz = tmp_path / name
    with tarfile.open(tgz, "w:gz") as t:
        t.add(root, arcname=root.name)
    return tgz


def _installed(tmp_path, ver="0.1.0"):
    app = tmp_path / "inst" / "app"
    (app / "meshpool_harness").mkdir(parents=True)
    (app / "meshpool_harness" / "__init__.py").write_text(f'__version__ = "{ver}"\n')
    (app / "engines").mkdir()
    (app / "engines" / "e.py").write_text(f"engine {ver}")
    return app


def _v(ver, k="cli"):
    return {"ok": True, "newer": True, "kind": k, "current": "0.1.0", "latest": ver, "tag": f"v{ver}",
            "assets": [{"name": U.SRC_ASSET, "url": "src"}, {"name": U.SUMS, "url": "s"}], "url": "u",
            "pending_restart": False}


def _wire(monkeypatch, app, tgz, ver="0.2.0", running="0.1.0"):
    """The installed copy is `app`, the running code says `running`, GitHub offers v<ver> and serves `tgz`."""
    monkeypatch.setattr(U, "APP", app)
    monkeypatch.setattr(U, "SRC", "")
    monkeypatch.setattr(U.MH, "__version__", running)
    monkeypatch.setattr(U, "kind", lambda: "cli")
    rel = [{"tag_name": f"v{ver}", "html_url": "u",
            "assets": [{"name": U.SRC_ASSET, "browser_download_url": "src"},
                       {"name": U.SUMS, "browser_download_url": "s"}]}]
    monkeypatch.setattr(U, "_get", lambda *a, **k: io.BytesIO(json.dumps(rel).encode()))
    monkeypatch.setattr(U, "_sums", lambda v: {U.SRC_ASSET: U._sha256(tgz)})
    calls = []

    def download(url, dst):
        calls.append(url)
        shutil.copy(tgz, dst)
        return dst
    monkeypatch.setattr(U, "_download", download)
    return calls


def _version(app):
    return (app / "meshpool_harness" / "__init__.py").read_text().split('"')[1]


# ------------------------------------------------------------------ M11-01

def test_update_now_does_not_swap_running_code(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    _wire(monkeypatch, app, _release(tmp_path, "0.2.0"))
    r = U.install()
    assert r["ok"] and r["action"] == "staged", r
    assert _version(app) == "0.1.0" and (app / "engines" / "e.py").read_text() == "engine 0.1.0"
    stage = Path(r["stage"])
    assert stage.parent == app and stage.name.startswith(".update-v0.2.0-") and (stage / ".complete").is_file()
    assert (stage / "meshpool_harness" / "__init__.py").read_text() == '__version__ = "0.2.0"\n'
    r2 = U.apply_staged(r)
    assert r2["ok"] and r2["action"] == "restart", r2
    assert _version(app) == "0.2.0" and (app / "engines" / "e.py").read_text() == "engine 0.2.0"
    assert sorted(p.name for p in app.iterdir()) == ["engines", "meshpool_harness"]     # no stage, backup or journal


def test_a_stage_without_its_marker_is_refused(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    _wire(monkeypatch, app, _release(tmp_path, "0.2.0"))
    r = U.install()
    (Path(r["stage"]) / ".complete").unlink()
    r2 = U.apply_staged(r)
    assert not r2["ok"] and _version(app) == "0.1.0"
    assert not (app / U.JOURNAL).exists()


def test_disk_version_prevents_reinstall(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    calls = _wire(monkeypatch, app, _release(tmp_path, "0.2.0"))
    v = U.check(force=True)
    assert v["newer"] and not v["pending_restart"] and v["tags"] == ["v0.2.0"] and v["checked_at"]
    assert U.apply_staged(U.install())["ok"]
    calls.clear()
    v = U.check(force=True)
    assert v["ok"] and not v["newer"] and v["pending_restart"], v
    assert v["current"] == "0.1.0" and v["tags"] == ["v0.2.0"]           # "current" is still what runs
    r = U.install()
    assert r["ok"] and r["action"] == "restart" and "0.2.0" in r["message"] and "Restart" in r["message"], r
    assert calls == []                                                     # nothing was downloaded again


def test_check_passes_its_timeout_to_the_request(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    _wire(monkeypatch, app, _release(tmp_path, "0.2.0"))
    seen = []
    monkeypatch.setattr(U, "_get", lambda url, timeout=20: seen.append(timeout) or io.BytesIO(b"[]"))
    U.check(force=True, timeout=5)
    U.check(force=True)
    assert seen == [5, 20]


# ------------------------------------------------------------------ M11-05

def _pkg_setup(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    (app / "requirements-cli.txt").write_text("pandas\n")
    tgz = _release(tmp_path, "0.2.0", {"packaging/requirements-cli.txt": "pandas\nnewdep\n"})
    _wire(monkeypatch, app, tgz)
    return app


def _fake_pip(monkeypatch, app, results):
    """Records (what the requirements file said, the code version on disk at that moment) for each pip call."""
    log = []

    def run(cmd, **k):
        req = Path(cmd[-1])
        log.append((req.read_text(), _version(app)))
        return subprocess.CompletedProcess(cmd, results[len(log) - 1], "", "no matching distribution")
    monkeypatch.setattr(U.subprocess, "run", run)
    return log


def test_packages_installed_after_swap_and_rolled_back(monkeypatch, tmp_path):
    app = _pkg_setup(monkeypatch, tmp_path)
    # 1. pip runs after the folders are swapped
    log = _fake_pip(monkeypatch, app, [0])
    assert U.apply_staged(U.install())["ok"]
    assert log == [("pandas\nnewdep\n", "0.2.0")]
    assert (app / "requirements-cli.txt").read_text() == "pandas\nnewdep\n"

    # 2. pip fails: the old folders come back and the old package list is installed again
    app = _installed(tmp_path / "second")
    (app / "requirements-cli.txt").write_text("pandas\n")
    tgz = _release(tmp_path / "second", "0.2.0", {"packaging/requirements-cli.txt": "pandas\nnewdep\n"})
    _wire(monkeypatch, app, tgz)
    log = _fake_pip(monkeypatch, app, [1, 0])
    r = U.apply_staged(U.install())
    assert not r["ok"] and "previous version" in r["error"] and "reinstalled" in r["error"], r
    assert log == [("pandas\nnewdep\n", "0.2.0"), ("pandas\n", "0.1.0")]
    assert _version(app) == "0.1.0" and (app / "engines" / "e.py").read_text() == "engine 0.1.0"
    assert sorted(p.name for p in app.iterdir()) == ["engines", "meshpool_harness", "requirements-cli.txt"]
    assert (app / "requirements-cli.txt").read_text() == "pandas\n"

    # 3. both pip calls fail: the message says the packages may be partly new
    app = _installed(tmp_path / "third")
    (app / "requirements-cli.txt").write_text("pandas\n")
    tgz = _release(tmp_path / "third", "0.2.0", {"packaging/requirements-cli.txt": "pandas\nnewdep\n"})
    _wire(monkeypatch, app, tgz)
    _fake_pip(monkeypatch, app, [1, 1])
    r = U.apply_staged(U.install())
    assert not r["ok"] and "partly new" in r["error"] and _version(app) == "0.1.0", r


def test_a_failed_swap_never_runs_pip(monkeypatch, tmp_path):
    app = _pkg_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(U.subprocess, "run", lambda *a, **k: pytest.fail("pip ran although the swap failed"))
    staged = U.install()
    real = U.Path.rename

    def rename(self, target):
        if self.name == "engines" and self.parent.name.startswith(".update-"):    # the 2nd folder
            raise PermissionError("in use")
        return real(self, target)
    monkeypatch.setattr(U.Path, "rename", rename)
    r = U.apply_staged(staged)
    assert not r["ok"] and "Nothing was changed" in r["error"], r
    assert _version(app) == "0.1.0" and (app / "engines" / "e.py").read_text() == "engine 0.1.0"
    assert not (app / U.JOURNAL).exists()


# ------------------------------------------------------------------ M11-06

def _fake_app_with_recovery(tmp_path):
    app = tmp_path / "killed" / "app"
    (app / "meshpool_harness").mkdir(parents=True)
    (app / "meshpool_harness" / "__init__.py").write_text('__version__ = "0.1.0"\n')
    (app / "engines").mkdir()
    (app / "engines" / "e.py").write_text("engine 0.1.0")
    for f in ("meshpool_recover.py", "sitecustomize.py"):
        shutil.copy2(APP_DIR / f, app / f)
    return app


_KILLED_CHILD = """
import os, shutil, sys
from pathlib import Path
real_app, app, tgz, home = sys.argv[1:5]
os.environ["MESHPOOL_HOME"] = home
sys.path.insert(0, real_app)
from meshpool_harness import updater as U
U.APP = Path(app)
U.SRC = Path(tgz).as_uri()
U._download = lambda url, dst: shutil.copy(tgz, dst) and dst
v = {"ok": True, "newer": True, "kind": "cli", "latest": "0.2.0", "tag": "v0.2.0", "assets": [], "url": "u"}
st = U.prepare(v)
assert st["ok"], st
pkg = Path(app) / "meshpool_harness"
real_rename, real_os_rename = Path.rename, os.rename

def die_after(path):
    if Path(path) == pkg:                 # the old package was just renamed away: the new one is not in yet
        os._exit(9)

def rename(self, target):
    r = real_rename(self, target)
    die_after(self)
    return r

def os_rename(a, b, *x, **k):
    r = real_os_rename(a, b, *x, **k)
    die_after(a)
    return r
Path.rename, os.rename = rename, os_rename
U.apply_staged(st)
os._exit(0)
"""


def test_kill_between_renames_is_recovered_at_start(tmp_path):
    app = _fake_app_with_recovery(tmp_path)
    tgz = _release(tmp_path, "0.2.0")
    script = tmp_path / "child.py"
    script.write_text(_KILLED_CHILD)
    home = tmp_path / "data"
    p = subprocess.run([sys.executable, str(script), str(APP_DIR), str(app), str(tgz), str(home)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 9, p.stderr[-800:]
    assert not (app / "meshpool_harness").exists()              # the state that used to be unrecoverable
    assert (app / U.JOURNAL).exists()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(app)
    q = subprocess.run([sys.executable, "-c", "import meshpool_harness, sys; print(meshpool_harness.__version__)"],
                       capture_output=True, text=True, timeout=60, env=env, cwd=str(tmp_path))
    assert q.returncode == 0 and q.stdout.strip() == "0.1.0", q.stderr[-800:]
    assert not (app / U.JOURNAL).exists()
    note = json.loads((app / ".update-recovered.json").read_text())
    assert "previous version" in note["message"] and note["time"]
    assert (app / "engines" / "e.py").read_text() == "engine 0.1.0"
    assert sorted(p.name for p in app.iterdir() if p.name.startswith(".")) == [".update-recovered.json"]


def test_recover_handles_every_pair_state(tmp_path):
    import meshpool_recover as R
    app = tmp_path / "app"
    app.mkdir()
    (app / "a.old").mkdir()
    (app / "a.old" / "f").write_text("old a")            # destination missing, backup present
    (app / "b").mkdir()
    (app / "b" / "f").write_text("new b")                # destination present, backup present
    (app / "b.old").mkdir()
    (app / "b.old" / "f").write_text("old b")
    (app / "c").mkdir()
    (app / "c" / "f").write_text("new c")                # did not exist before the update
    (app / "d").mkdir()
    (app / "d" / "f").write_text("untouched d")          # existed, never moved
    (app / ".update-v1-aa").mkdir()
    pairs = [{"dest": str(app / n), "backup": str(app / f"{n}.old"), "existed": e}
             for n, e in (("a", True), ("b", True), ("c", False), ("d", True))]
    (app / R.JOURNAL).write_text(json.dumps({"pairs": pairs, "tag": "v1", "pid": 0}))
    msg = R.recover(app)
    assert msg and "v1" in msg
    assert (app / "a" / "f").read_text() == "old a" and (app / "b" / "f").read_text() == "old b"
    assert not (app / "c").exists() and (app / "d" / "f").read_text() == "untouched d"
    assert not (app / ".update-v1-aa").exists() and not (app / R.JOURNAL).exists()
    assert R.recover(app) is None and R.recover(tmp_path / "nowhere") is None


def test_recover_leaves_a_swap_in_progress_in_another_process_alone(tmp_path):
    import meshpool_recover as R
    app = tmp_path / "app"
    app.mkdir()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (app / R.JOURNAL).write_text(json.dumps({"pairs": [], "pid": child.pid, "time": time.time()}))
        assert R.recover(app) is None and (app / R.JOURNAL).exists()
    finally:
        child.kill()
        child.wait()
    assert R.recover(app) and not (app / R.JOURNAL).exists()


def test_sitecustomize_chains_and_never_raises(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    shutil.copy2(APP_DIR / "sitecustomize.py", a / "sitecustomize.py")
    shutil.copy2(APP_DIR / "meshpool_recover.py", a / "meshpool_recover.py")
    (b / "sitecustomize.py").write_text(f"open({str(tmp_path / 'chained')!r}, 'w').write('yes')\n")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join([str(a), str(b)])

    def run():
        return subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True, timeout=60, env=env)
    p = run()
    assert p.returncode == 0 and p.stdout.strip() == "ok", p.stderr
    assert (tmp_path / "chained").read_text() == "yes"                  # the shadowed sitecustomize still ran
    (tmp_path / "chained").unlink()
    (a / "meshpool_recover.py").write_text("raise RuntimeError('broken')\n")        # recovery itself blows up
    (a / ".update-journal.json").write_text("{ not json")
    p = run()
    assert p.returncode == 0 and p.stdout.strip() == "ok", p.stderr
    assert (tmp_path / "chained").read_text() == "yes"
    (b / "sitecustomize.py").write_text("raise RuntimeError('the next one is broken too')\n")
    p = run()
    assert p.returncode == 0 and p.stdout.strip() == "ok", p.stderr
    (a / "meshpool_recover.py").unlink()                                 # and without the module at all
    assert run().returncode == 0


# ------------------------------------------------------------------ M11-07

_HOLDER = """
import os, sys
sys.path.insert(0, sys.argv[1])
from meshpool_harness import updater as U
with U.install_lock():
    print("held", flush=True)
    sys.stdin.readline()
"""


def test_two_concurrent_installs(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    _wire(monkeypatch, app, _release(tmp_path, "0.2.0"))
    v = _v("0.2.0")
    # two threads: the second installer is turned away while the first one downloads
    inside, go = threading.Event(), threading.Event()
    real_dl = U._download

    def slow(url, dst):
        inside.set()
        assert go.wait(30)
        return real_dl(url, dst)
    monkeypatch.setattr(U, "_download", slow)
    first = {}
    t = threading.Thread(target=lambda: first.update(r=U.prepare(v)))
    t.start()
    assert inside.wait(30)
    with pytest.raises(U.UpdateBusy, match="already being installed"):
        U.prepare(v)
    with pytest.raises(U.UpdateBusy):
        with U.install_lock():
            pass
    assert U.install()["busy"] is True                                   # install() reports it instead of raising
    go.set()
    t.join(30)
    assert first["r"]["ok"], first
    # a second PROCESS is turned away while the first holds the lock, and the lock is free again afterwards
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(APP_DIR)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, env={**os.environ, "MESHPOOL_HOME": str(tmp_path / "data")})
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(U.UpdateBusy):
            U.prepare(v)
    finally:
        proc.stdin.close()
        proc.wait(30)
    with U.install_lock():
        pass
    # the lock is re-entrant inside one thread (the server holds it around prepare + apply_staged)
    monkeypatch.setattr(U, "_download", real_dl)
    with U.install_lock():
        r = U.prepare(v)
        assert r["ok"] and U.apply_staged(r)["ok"]


def test_stage_folders_are_unique_per_prepare(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    _wire(monkeypatch, app, _release(tmp_path, "0.2.0"))
    (app / ".engines.new").mkdir()                                       # the old shared staging folder
    a = U.prepare(_v("0.2.0"))
    b = U.prepare(_v("0.2.0"))
    assert a["ok"] and b["ok"] and a["stage"] != b["stage"]
    assert not Path(a["stage"]).exists() and Path(b["stage"]).is_dir()   # the stale stage of the same tag is swept
    assert not (app / ".engines.new").exists()
    U.discard_stage(b["stage"])
    assert not Path(b["stage"]).exists()
    U.discard_stage(app / "engines")                                     # only .update-* folders are ever deleted
    assert (app / "engines").is_dir()


def test_staged_top_level_files_are_copied(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    (app / "meshpool_recover.py").write_text("old recover")
    _wire(monkeypatch, app, _release(tmp_path, "0.2.0", {"app/meshpool_recover.py": "new recover",
                                                         "app/sitecustomize.py": "new site"}))
    assert U.apply_staged(U.install())["ok"]
    assert (app / "meshpool_recover.py").read_text() == "new recover"
    assert (app / "sitecustomize.py").read_text() == "new site"


# ------------------------------------------------------------------ M11-03

def test_restart_passes_pid_and_bounds_quit_hooks(monkeypatch):
    popen = []
    monkeypatch.setattr(U.subprocess, "Popen", lambda cmd, **k: popen.append(k) or None)
    exits = []
    monkeypatch.setattr(U.os, "_exit", exits.append)
    monkeypatch.setattr(U, "QUIT_DEADLINE", 0.5)
    ran, release = [], threading.Event()
    hooks = [lambda: ran.append("fast"), lambda: release.wait(30), lambda: ran.append("after the hung one")]
    t0 = time.monotonic()
    try:
        U.restart(on_quit=hooks)
        took = time.monotonic() - t0
    finally:
        release.set()
    assert popen and popen[0]["env"]["MESHPOOL_RESTART_WAIT"] == str(os.getpid())
    assert exits == [0] and 0.4 <= took < 5, took
    assert ran == ["fast"]                                               # the hung hook did not hold the quit


# ------------------------------------------------------------------ wait_for_exit

def test_wait_for_exit(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    threading.Thread(target=p.wait, daemon=True).start()                 # reap it like a real parent would
    t0 = time.monotonic()
    assert U.wait_for_exit(p.pid, 10) is True
    assert 0.2 <= time.monotonic() - t0 < 5
    assert U.wait_for_exit(p.pid, 0.1) is True                           # already gone
    q = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        t0 = time.monotonic()
        assert U.wait_for_exit(q.pid, 0.3) is False
        assert time.monotonic() - t0 < 3
    finally:
        q.kill()
        q.wait()


def test_timeout_raised_by_the_body_is_not_reported_as_busy():
    # a socket timeout inside the locked body (a slow download) must stay a TimeoutError, not "already being installed"
    with pytest.raises(TimeoutError):
        with U.install_lock():
            raise TimeoutError("timed out")
    with U.install_lock():                                               # and the lock was released
        pass
