"""Fixes for items left over from 0.0.18 (ID prefix LP). One test per item."""
import os
import sys
import time

import pytest

from meshpool_harness.core import tools as T


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"        # a zombie has ended
    except OSError:
        return False


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


_FAKE_DOCKER = r'''#!{py}
"""A stand-in for the docker client: `docker exec` runs the command here, in its own session, the way a real
container process is out of reach of the local client. Each call's arguments are recorded."""
import json, os, subprocess, sys
with open({log!r}, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
a = sys.argv[1:]
assert a[0] == "exec", a
a = a[1:]
cwd = None
while a and a[0].startswith("-"):
    if a[0] == "-w":
        cwd = a[1]
        a = a[2:]
    else:
        a = a[1:]
a = a[1:]                                   # the container name
p = subprocess.Popen(a, cwd=cwd, start_new_session=True)
sys.exit(p.wait())
'''


@pytest.mark.skipif(os.name == "nt" or not os.path.isdir("/proc"), reason="uses /proc and a shell script")
def test_lp01_container_command_ends_inside_the_container_and_output_is_capped(tmp_path, monkeypatch):
    """LP-01: on a timeout the command inside the container is killed too, and a runaway producer is capped."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    fake = bindir / "docker"
    fake.write_text(_FAKE_DOCKER.format(py=sys.executable, log=str(log)))
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    work = tmp_path / "w"
    work.mkdir()
    ws = T.Workspace(str(work), str(tmp_path / ".run"), container="box", container_dir=str(work), mounted=True)
    pidfile = work / "inner.pid"

    out, code = ws._run(f"echo $$ > {pidfile}; exec sleep 60", 1, "bash")
    assert code == 124 and "timed out" in out
    pid = int(pidfile.read_text().strip())
    assert _wait(lambda: not _alive(pid)), "the command kept running inside the container after the timeout"
    calls = log.read_text()
    assert '"exec"' in calls and "box" in calls

    # an endless producer: the output is cut off at the cap and the command ends well before its timeout
    monkeypatch.setattr(T, "OUTPUT_KILL_CHARS", 200_000)
    t0 = time.time()
    out, code = ws._run("yes meshpool", 60, "bash", raw=True)
    assert "output stopped" in out
    assert time.time() - t0 < 30


def test_lp02_run_due_exits_nonzero_when_a_task_could_not_start(monkeypatch, capsys, tmp_path):
    """LP-02: `meshpool run-due` exits 1 when a task failed to start and still prints each failure; the cron line
    keeps sending its output to /dev/null so the exit code is harmless there."""
    import subprocess

    from meshpool_harness import assistant as AS
    from meshpool_harness import cli
    from meshpool_harness import config as C
    from meshpool_harness import scheduler_os as SO
    from meshpool_harness.ui import agent_api as A

    class _Done:
        state = "done"

    def start(t):
        if t["name"] == "broken":
            raise ValueError("folder gone")
        return "s1"

    monkeypatch.setenv("MESHPOOL_HEADLESS", "1")
    monkeypatch.setattr(C, "load_into_env", lambda *a, **k: None)
    monkeypatch.setattr(AS, "due", lambda: [{"name": "broken"}, {"name": "fine"}])
    monkeypatch.setattr(A, "run_scheduled", start)
    monkeypatch.setattr(A, "get", lambda sid: _Done())

    def fake_exit(code):
        raise SystemExit(code)
    monkeypatch.setattr(os, "_exit", fake_exit)
    with pytest.raises(SystemExit) as e:
        cli.main(["run-due"])
    assert e.value.code == 1
    io = capsys.readouterr()
    assert "ran 1 scheduled task(s)" in io.out and "could not run broken: folder gone" in io.err

    monkeypatch.setattr(AS, "due", lambda: [{"name": "fine"}])
    with pytest.raises(SystemExit) as e:
        cli.main(["run-due"])
    assert e.value.code == 0

    seen = {}

    def run(cmd, input=None, **kw):
        seen["tab"] = input
        return subprocess.CompletedProcess(cmd, 0 if input is not None else 1, "", "no crontab for user")
    monkeypatch.setattr(SO, "_data_home", lambda: tmp_path)
    SO.install(run=run, platform="linux")
    assert ">/dev/null 2>&1" in seen["tab"]


def test_lp03_permissions_list_shows_ask_rules(tmp_path, monkeypatch, capsys):
    """LP-03: `meshpool permissions list` shows the ask rules the agent reads, and remove takes them out."""
    import json

    from meshpool_harness import cli
    from meshpool_harness.agent import core
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    for k in ("MH_ALLOW", "MH_DENY", "MH_ASK"):
        monkeypatch.delenv(k, raising=False)
    (home / "permissions.json").write_text(json.dumps({"allow": ["read_file"], "ask": ["bash:git push*"]}))
    proj = tmp_path / "p"
    assert core._rules(proj)["ask"] == ["bash:git push*"]          # what the agent goes by
    assert cli.main(["permissions", "list", "--dir", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "ask   bash:git push*" in out and "allow read_file" in out
    assert cli.main(["permissions", "remove", "bash:git push*"]) == 0
    assert core._rules(proj)["ask"] == []


_OPENS = {"path": None, "n": 0}


def _count_opens(event, args):
    if event == "open" and _OPENS["path"] and args and str(args[0]) == _OPENS["path"]:
        _OPENS["n"] += 1


def test_lp04_providers_read_the_settings_file_once_while_unchanged(tmp_path, monkeypatch):
    """LP-04: the base, key, kind and api-version of one model call do not each read the settings file again; a
    change to the file is still seen."""
    from meshpool_harness import config as C
    from meshpool_harness import providers as PV
    cfg = tmp_path / "meshpool.config"
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(cfg))
    for k in ("MESHPOOL_API_KEY", "MESHPOOL_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY",
              "AZURE_OPENAI_ENDPOINT", "MH_API_KIND", "MH_API_VERSION", "MH_API_WIRE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(C, "_INJECTED", set())
    monkeypatch.setattr(C, "SHELL", {})

    from meshpool_harness import fsio
    monkeypatch.setattr(fsio, "RACY_S", 0.0)      # the cache's guard for a file written in the last 2 s: not here

    def write(base):
        cfg.write_text(f"MESHPOOL_API_KEY=test-key-one\nMESHPOOL_BASE_URL={base}\n")

    def one_call():
        return PV.key_base(), PV.key(), PV.key_kind(), PV.key_api_version()

    write("https://one.example.test/v1")
    sys.addaudithook(_count_opens)                # stays installed; it counts only while a path is set
    _OPENS.update(path=str(cfg), n=0)
    try:
        assert one_call()[:2] == ("https://one.example.test/v1", "test-key-one")
        first = _OPENS["n"]
        assert first <= 1, f"one model call read the settings file {first} times"
        _OPENS["n"] = 0
        one_call()
        assert _OPENS["n"] == 0, "an unchanged settings file was read again"
        cfg.write_text("MESHPOOL_API_KEY=test-key-two\nMESHPOOL_BASE_URL=https://two.example.test/v1/extra\n")
        assert one_call()[:2] == ("https://two.example.test/v1/extra", "test-key-two")
    finally:
        _OPENS["path"] = None


def test_lp05_old_uploads_nothing_points_at_are_removed(tmp_path, monkeypatch):
    """LP-05: uploads older than 30 days are removed at start-up unless a company profile or project names them;
    a damaged profile file keeps everything."""
    import json

    from meshpool_harness.ui import server as S
    home = tmp_path / "home"
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    ups = tmp_path / "runs" / "_uploads"
    monkeypatch.setattr(S, "UPLOADS", ups)
    old = time.time() - 40 * 86400

    def upload(name, age):
        d = ups / name
        d.mkdir(parents=True)
        f = d / "deck.pptx"
        f.write_bytes(b"x")
        for p in (f, d):
            os.utime(p, (age, age))
        return f
    stale = upload("aaaa0001", old)
    kept_by_profile = upload("aaaa0002", old)
    kept_by_project = upload("aaaa0003", old)
    fresh = upload("aaaa0004", time.time() - 86400)
    (home / "brand").mkdir(parents=True)
    (home / "brand" / "profiles.json").write_text(json.dumps(
        {"active": "p1", "profiles": {"p1": {"id": "p1", "logo": str(kept_by_profile)}}}))
    pd = home / "projects" / "p_0123456789"
    pd.mkdir(parents=True)
    (pd / "project.json").write_text(json.dumps({"id": pd.name, "link": {"type": "folder",
                                                                          "path": str(kept_by_project.parent)}}))
    assert S.prune_uploads() == ["aaaa0001"]
    assert not stale.exists() and kept_by_profile.exists() and kept_by_project.exists() and fresh.exists()

    upload("aaaa0005", old)
    (home / "brand" / "profiles.json").write_text("{not json")
    assert S.prune_uploads() == []                       # cannot tell what the profiles name: keep everything


def test_lp06_macos_sandbox_keeps_the_data_folder_read_only(tmp_path, monkeypatch):
    """LP-06: when MH_SANDBOX_WRITE covers meshpool's data folder, the Seatbelt profile denies writes there after its
    allow rules, and gives back only the folder the command works in (as bwrap does since AG-15)."""
    from meshpool_harness.core import sandbox as SB
    data = (tmp_path / "Library" / "meshpool").resolve()
    job = data / "runs" / "job"
    job.mkdir(parents=True)
    monkeypatch.setenv("MESHPOOL_HOME", str(data))
    monkeypatch.setenv("MH_SANDBOX", "1")
    monkeypatch.setenv("MH_SANDBOX_WRITE", str(tmp_path / "Library"))
    which = lambda name: "/usr/bin/" + name  # noqa: E731

    def profile(root):
        argv = SB.wrap(["bash", "-c", "true"], root, platform="darwin", which=which)
        assert argv[:2] == ["sandbox-exec", "-p"]
        return argv[2]
    proj = (tmp_path / "proj").resolve()
    proj.mkdir()
    p = profile(proj)
    allow_lib = p.index(f'(subpath "{tmp_path / "Library"}")')
    deny = p.index(f'(deny file-write* (subpath "{data}"))')
    assert deny > allow_lib                                # the later rule wins in Seatbelt
    assert p.rfind(f'(subpath "{data}') == deny + len('(deny file-write* ')   # nothing in it is given back
    p = profile(job)                                       # an engine's folder inside the data folder
    deny = p.index(f'(deny file-write* (subpath "{data}"))')
    assert p.rindex(f'(subpath "{job}")') > deny

    monkeypatch.setattr(SB, "_writable", lambda root: [str(root)])   # nothing covers the data folder: as before
    assert "(deny file-write* (subpath" not in profile(proj)
