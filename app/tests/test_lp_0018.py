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
