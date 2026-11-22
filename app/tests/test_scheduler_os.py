import subprocess
import sys

from meshpool_harness import scheduler_os as SO


def _fake(calls, crontab=""):
    def run(cmd, **kw):
        calls.append((cmd, kw.get("input")))
        return subprocess.CompletedProcess(cmd, 0, stdout=crontab if cmd[:2] == ["crontab", "-l"] else "", stderr="")
    return run


def test_cron_entry_is_added_once_and_removed():
    calls = []
    SO.install(run=_fake(calls, "0 1 * * * backup\n"), platform="linux")
    written = [i for c, i in calls if c == ["crontab", "-"]][0]
    assert "backup" in written and written.count("meshpool-scheduler") == 1 and "run-due" in written
    calls.clear()
    SO.uninstall(run=_fake(calls, written), platform="linux")
    left = [i for c, i in calls if c == ["crontab", "-"]][0]
    assert "backup" in left and "meshpool" not in left


def test_windows_and_mac_registration(tmp_path):
    calls = []
    SO.install(run=_fake(calls), platform="win32")
    assert calls[0][0][0] == "schtasks" and "/MO" in calls[0][0]
    calls.clear()
    SO.install(run=_fake(calls), platform="darwin", home=tmp_path)
    assert (tmp_path / "Library/LaunchAgents/ai.meshpool.scheduler.plist").exists()
    assert sys.executable in (tmp_path / "Library/LaunchAgents/ai.meshpool.scheduler.plist").read_text()


def test_the_cli_knows_the_scheduler_commands():
    from meshpool_harness import cli
    assert {"run-due", "scheduler"} <= cli.COMMANDS


def test_an_unattended_run_never_waits_for_an_approval(monkeypatch):
    from meshpool_harness.ui import agent_api as A
    monkeypatch.setenv("MESHPOOL_HEADLESS", "1")
    s = A.Session.__new__(A.Session)
    decision, note = s.approve("bash", "Run?", "ls")
    assert decision == "no" and "unattended" in note
