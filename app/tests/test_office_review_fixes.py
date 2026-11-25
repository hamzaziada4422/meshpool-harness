"""Fixes from a review of the office engines: engine jobs that outlived their chat, LibreOffice runs left behind on a
timeout, recalculation scratch folders, a reused build after its input changed, all-zero charts passing the data check
and sign-flipped numbers passing the trace check. Stub processes stand in for the engines and LibreOffice; no model
calls, no network."""
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meshpool_harness.agent import office_jobs as OJ  # noqa: E402

posix = pytest.mark.skipif(os.name == "nt", reason="process groups (POSIX)")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                              # a zombie is gone for our purposes
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _wait_for(cond, secs=15.0) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


def _stub_job(pidfile: Path, guard: bool = False) -> list[str]:
    """A long-running engine job: it starts a grandchild (the stand-in for soffice) and sleeps; the grandchild's pid
    goes to pidfile once everything is up."""
    code = textwrap.dedent(f"""
        import subprocess, sys, time
        sys.path.insert(0, {str(ROOT)!r})
        if {guard!r}:
            from meshpool_harness.agent.office_jobs import guard_from_env
            guard_from_env()
        g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        open({str(pidfile)!r}, "w").write(str(g.pid))
        time.sleep(300)
    """)
    return [sys.executable, "-c", code]


@pytest.fixture
def jobs():
    saved = dict(OJ.JOBS)
    OJ.JOBS.clear()
    yield
    OJ.kill_jobs()
    OJ.JOBS.clear()
    OJ.JOBS.update(saved)


# ---------------------------------------------------------------------------------------------- engine job lifetime
@posix
def test_kill_jobs_ends_a_running_job_and_what_it_started(tmp_path, jobs):
    jid = OJ.new_job("sheets", "Build a budget", [], tmp_path)
    OJ.JOBS[jid]["cmd"] = _stub_job(tmp_path / "g.pid")
    t = threading.Thread(target=OJ.run, args=(jid,), kwargs={"timeout": 120}, daemon=True)
    t.start()
    assert _wait_for(lambda: (tmp_path / "g.pid").exists() and (tmp_path / "g.pid").read_text())
    grand = int((tmp_path / "g.pid").read_text())
    OJ.kill_jobs()
    t.join(15)
    assert not t.is_alive() and OJ.JOBS[jid]["done"]
    assert _wait_for(lambda: not _alive(grand), 10)


SERVER = """
import sys, threading
sys.path.insert(0, {root!r})
from pathlib import Path
from meshpool_harness.agent import office_jobs as OJ, office_mcp_server as O
jid = OJ.new_job("sheets", "Build a budget", [], Path({tmp!r}))
OJ.JOBS[jid]["cmd"] = {cmd!r}
threading.Thread(target=OJ.run, args=(jid,), daemon=True).start()
O.main()
"""


def _server(tmp_path: Path, pidfile: Path):
    env = dict(os.environ, HOME=str(tmp_path), MESHPOOL_HOME=str(tmp_path / "mh"), PYTHONPATH=str(ROOT))
    src = SERVER.format(root=str(ROOT), tmp=str(tmp_path), cmd=_stub_job(pidfile))
    p = subprocess.Popen([sys.executable, "-c", src], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, env=env, start_new_session=True, text=True)
    assert _wait_for(lambda: pidfile.exists() and pidfile.read_text(), 30)
    p.stdin.write('{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n')      # the server is serving: answers a ping
    p.stdin.flush()
    import select
    assert select.select([p.stdout], [], [], 60)[0], "the server did not answer"
    assert '"id": 1' in p.stdout.readline()
    return p, int(pidfile.read_text())


@posix
@pytest.mark.parametrize("how", ["client_gone", "sigterm"])
def test_office_server_ends_its_jobs_when_it_ends(tmp_path, how):
    """The jobs run in their own process groups, so the app's kill of the server's tree missed them: they kept
    running after the chat closed or the app quit. Now the server ends them when its client goes away or on SIGTERM."""
    p, grand = _server(tmp_path, tmp_path / "g.pid")
    try:
        if how == "client_gone":
            p.stdin.close()
        else:
            p.send_signal(signal.SIGTERM)
        p.wait(20)
        assert _wait_for(lambda: not _alive(grand), 10)
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.kill(grand, signal.SIGKILL)
        except OSError:
            pass


@posix
def test_a_job_ends_itself_when_the_process_that_started_it_is_killed(tmp_path):
    """SIGKILL of the server (or the app) leaves nobody to enforce the timeout: the job's own guard ends it."""
    pidfile = tmp_path / "g.pid"
    runner = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        from meshpool_harness.agent import office_jobs as OJ
        jid = OJ.new_job("sheets", "Build a budget", [], Path({str(tmp_path)!r}))
        OJ.JOBS[jid]["cmd"] = {_stub_job(pidfile, guard=True)!r}
        OJ.run(jid, timeout=600)
    """)
    p = subprocess.Popen([sys.executable, "-c", runner], env=dict(os.environ, PYTHONPATH=str(ROOT)))
    try:
        assert _wait_for(lambda: pidfile.exists() and pidfile.read_text(), 30), "the job did not start its guard"
        grand = int(pidfile.read_text())
        p.kill()
        p.wait(10)
        assert _wait_for(lambda: not _alive(grand), 15)
    finally:
        p.kill()


@posix
def test_a_job_ends_itself_at_its_deadline(tmp_path):
    pidfile = tmp_path / "g.pid"
    env = dict(os.environ, MH_JOB_DEADLINE=str(time.time() + 2), MH_JOB_PARENT=str(os.getpid()))
    p = subprocess.Popen(_stub_job(pidfile, guard=True), env=env, start_new_session=True)
    try:
        assert _wait_for(lambda: pidfile.exists() and pidfile.read_text(), 30)
        grand = int(pidfile.read_text())
        assert p.wait(20) is not None
        assert _wait_for(lambda: not _alive(grand), 10)
    finally:
        p.kill()


def test_stop_in_the_client_ends_the_office_server_job(tmp_path, jobs, monkeypatch):
    """The server's job ran without should_stop, so a Stop (notifications/cancelled) left it building."""
    from meshpool_harness.agent import mcp_stdio
    from meshpool_harness.agent import office_mcp_server as O
    real = OJ.new_job

    def new_job(*a, **k):
        jid = real(*a, **k)
        OJ.JOBS[jid]["cmd"] = [sys.executable, "-c", "import time; time.sleep(300)"]
        return jid
    monkeypatch.setattr(OJ, "new_job", new_job)
    monkeypatch.setattr(O, "WAIT", 0.5)
    O._CALL.project = str(tmp_path)
    mcp_stdio._CALL.cancel = ev = threading.Event()
    try:
        text, _ = O._engine("sheets", "Build a 12-month budget for a coffee shop", None)
        assert text.startswith("Still working")
        jid = next(iter(OJ.JOBS))
        ev.set()
        assert _wait_for(lambda: OJ.JOBS[jid].get("done"), 10)
        assert OJ.JOBS[jid].get("stopped")
    finally:
        O._CALL.project = None
        mcp_stdio._CALL.cancel = None


def test_a_changed_input_is_a_new_request(tmp_path, jobs, monkeypatch):
    monkeypatch.setattr("engines.forge.tools.qa_summary", lambda files: "")
    src = tmp_path / "sales.csv"
    src.write_text("month,revenue\nJan,10\n")
    fake = ("import json, pathlib; pathlib.Path('m.xlsx').write_bytes(b'x'); "
            "print(json.dumps({'ok': True, 'steps': [{'answer': 'ok', 'artifacts': ['m.xlsx']}]}))")
    real = OJ.new_job

    def new_job(*a, **k):
        jid = real(*a, **k)
        OJ.JOBS[jid]["cmd"] = [sys.executable, "-c", fake]
        return jid
    monkeypatch.setattr(OJ, "new_job", new_job)
    req = "Build a forecast from sales.csv"
    jid, how = OJ.start("sheets", req, [str(src)], tmp_path, scope="c")
    assert how == ""
    OJ.run(jid, timeout=30)
    assert OJ.start("sheets", req, [str(src)], tmp_path, scope="c") == (jid, "done")     # unchanged: reused
    src.write_text("month,revenue\nJan,10\nFeb,12\n")
    jid2, how2 = OJ.start("sheets", req, [str(src)], tmp_path, scope="c")
    assert how2 == "" and jid2 != jid                                                 # changed: built again


# ---------------------------------------------------------------------------------------------- LibreOffice recalc
def _fake_soffice(tmp_path: Path, body: str) -> str:
    exe = tmp_path / "soffice"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(0o755)
    return str(exe)


@posix
def test_recalc_timeout_ends_soffice_bin_too(tmp_path, monkeypatch):
    from engines.sheets import qa
    from meshpool_harness import office_runtime
    pidfile = tmp_path / "bin.pid"
    exe = _fake_soffice(tmp_path, f"sleep 300 &\necho $! > {pidfile}\nsleep 300\n")   # soffice starts soffice.bin
    monkeypatch.setattr(office_runtime, "find_soffice", lambda: exe)
    monkeypatch.setattr(qa, "RECALC_TIMEOUT_S", 1)
    with pytest.raises(subprocess.TimeoutExpired):
        qa.recalc(str(tmp_path / "in.xlsx"), str(tmp_path / "out"))
    bin_pid = int(pidfile.read_text())
    assert _wait_for(lambda: not _alive(bin_pid), 10)


@posix
def test_check_leaves_no_recalc_scratch_folder(tmp_path, monkeypatch):
    import tempfile
    from openpyxl import Workbook
    from engines.sheets import qa
    from meshpool_harness import office_runtime
    # a stand-in converter: copies the workbook into --outdir (the last two arguments are the folder and the file)
    exe = _fake_soffice(tmp_path, 'for a; do prev=$last; last=$a; done\ncp "$last" "$prev/"\n')
    monkeypatch.setattr(office_runtime, "find_soffice", lambda: exe)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    wb = Workbook()
    wb.active["A1"], wb.active["A2"] = 1, "=A1+1"
    wb.save(tmp_path / "w.xlsx")
    r = qa.check(str(tmp_path / "w.xlsx"))
    assert r["stats"]["recalculated"]
    assert qa.value(str(tmp_path / "w.xlsx"), "Sheet!A1") == 1
    assert [p.name for p in scratch.iterdir() if p.name.startswith("recalc_")] == []


# ---------------------------------------------------------------------------------------------- P22 and number trace
def test_all_zero_chart_values_do_not_fit_at_any_scale():
    from engines.forge.data_pages import _values_fit
    assert _values_fit([(1200, 0.0, 0), (1350, 0.0, 0), (1410, 0.0, 0)])[1]           # was a fit "in millions"
    assert _values_fit([(1200, 0.0, 0), (1350, 1.0, 0)])[1]
    assert _values_fit([(1200, 1.2, 1), (1350, 1.4, 1)]) == (1e3, [])                 # thousands, rounded: still fine


def test_signs_flip_only_when_the_words_say_down():
    from engines.data.checks import trace_numbers
    assert trace_numbers("Sales grew 5.2% to a record.", "pct_change -5.2")             # a fall reported as growth
    assert trace_numbers("Profit was $12,000.", "diff -12000.0")
    assert trace_numbers("Sales fell 5.2%, down $12,000.", "pct_change -5.2\ndiff -12000.0") == []
    assert trace_numbers("Margin declined by 3.4 points", "delta -3.4") == []
