"""Run scheduled tasks while the app is closed: `meshpool scheduler install` registers a small job with the operating
system (cron on Linux, launchd on macOS, Task Scheduler on Windows) that runs `meshpool run-due` every 10 minutes.
Due tasks are claimed under the same cross-process lock the app uses, so a task never runs twice."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TAG = "# meshpool-scheduler"
PLIST = "ai.meshpool.scheduler"


def command() -> list[str]:
    return [sys.executable, "-m", "meshpool_harness", "run-due"]


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def install(run=_run, platform: str | None = None, home: Path | None = None) -> str:
    plat = platform or sys.platform
    cmd = command()
    line = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    if plat.startswith("win"):
        r = run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "10", "/TN", "Meshpool scheduler", "/TR", line])
        if r.returncode:
            raise RuntimeError("Task Scheduler refused: " + (r.stderr or r.stdout)[-200:])
        return "Registered with Task Scheduler (every 10 minutes)."
    if plat == "darwin":
        home = home or Path.home()
        p = home / "Library" / "LaunchAgents" / f"{PLIST}.plist"
        p.parent.mkdir(parents=True, exist_ok=True)
        args = "".join(f"<string>{c}</string>" for c in cmd)
        p.write_text('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
                     f"<key>Label</key><string>{PLIST}</string><key>ProgramArguments</key><array>{args}</array>"
                     "<key>StartInterval</key><integer>600</integer><key>RunAtLoad</key><false/></dict></plist>", encoding="utf-8")
        run(["launchctl", "unload", str(p)])
        r = run(["launchctl", "load", str(p)])
        if r.returncode:
            raise RuntimeError("launchd refused: " + (r.stderr or r.stdout)[-200:])
        return "Registered with launchd (every 10 minutes)."
    cur = run(["crontab", "-l"])
    lines = [x for x in (cur.stdout if cur.returncode == 0 else "").splitlines() if TAG not in x]
    lines.append(f"*/10 * * * * {line} >/dev/null 2>&1 {TAG}")
    r = run(["crontab", "-"], input="\n".join(lines) + "\n")
    if r.returncode:
        raise RuntimeError("crontab refused: " + (r.stderr or r.stdout)[-200:])
    return "Registered with cron (every 10 minutes)."


def uninstall(run=_run, platform: str | None = None, home: Path | None = None) -> str:
    plat = platform or sys.platform
    if plat.startswith("win"):
        run(["schtasks", "/Delete", "/F", "/TN", "Meshpool scheduler"])
    elif plat == "darwin":
        p = (home or Path.home()) / "Library" / "LaunchAgents" / f"{PLIST}.plist"
        run(["launchctl", "unload", str(p)])
        if p.exists():
            p.unlink()
    else:
        cur = run(["crontab", "-l"])
        lines = [x for x in (cur.stdout if cur.returncode == 0 else "").splitlines() if TAG not in x]
        run(["crontab", "-"], input="\n".join(lines) + ("\n" if lines else ""))
    return "Removed. Scheduled tasks run only while Meshpool is open."


def run_due() -> int:
    """Run every task that is due, one after another, and wait for each to finish. Returns how many ran."""
    import time

    from meshpool_harness import assistant as AS
    from meshpool_harness import config as C
    from meshpool_harness.ui import agent_api as A
    C.load_into_env()
    os.environ["MESHPOOL_HEADLESS"] = "1"
    n = 0
    for t in AS.due():
        try:
            sid = A.run_scheduled(t)
            s = A.get(sid)
            end = time.time() + 3600
            while s.state in ("working", "waiting", "starting") and time.time() < end:
                time.sleep(2)
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"could not run {t.get('name')}: {e}", file=sys.stderr)
    return n
