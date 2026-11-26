"""Run scheduled tasks while the app is closed: `meshpool scheduler install` registers a small job with the operating
system (cron on Linux, launchd on macOS, Task Scheduler on Windows) that runs `meshpool run-due` every 10 minutes.
Due tasks are claimed under the same cross-process lock the app uses, so a task never runs twice."""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

TAG = "# meshpool-scheduler"
PLIST = "ai.meshpool.scheduler"


def command() -> list[str]:
    return [sys.executable, "-m", "meshpool_harness", "run-due"]


def _data_home() -> Path:
    from meshpool_harness import config as C
    return C.home()


def _app_dir() -> Path:
    from meshpool_harness import config as C
    return C.APP


def _job_env() -> dict:
    """What the job needs that cron/launchd/Task Scheduler do not provide: where the package lives (the CLI install
    is not pip-installed) and where this user's data folder is (the desktop apps keep it outside the install)."""
    return {"PYTHONPATH": str(_app_dir()), "MESHPOOL_HOME": str(_data_home())}


def _run(cmd, **kw):
    # Decode with replacement: schtasks writes the OEM code page, crontab the locale's; a stray byte must not raise.
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          encoding="oem" if os.name == "nt" else None, **kw)


def _windowless(exe: str) -> str:
    """pythonw.exe next to the running interpreter, so the 10-minute job does not flash a console window."""
    p = Path(exe)
    if p.name.lower() == "python.exe" and (p.with_name("pythonw.exe")).exists():
        return str(p.with_name("pythonw.exe"))
    return exe


def _crontab_lines(run) -> list:
    """The user's current crontab, line by line; [] when they have none. Any other failure of crontab -l raises:
    rewriting from an empty list would delete every other entry the user has."""
    cur = run(["crontab", "-l"])
    if cur.returncode == 0:
        return (cur.stdout or "").splitlines()
    if "no crontab" in ((cur.stderr or "") + (cur.stdout or "")).lower():
        return []
    raise RuntimeError("could not read your crontab, so it was left as it is: "
                       + ((cur.stderr or cur.stdout or "").strip()[-200:] or f"crontab -l exited {cur.returncode}"))


def install(run=_run, platform: str | None = None, home: Path | None = None) -> str:
    try:
        return _install(run, platform, home)
    except (OSError, ValueError) as e:                          # no crontab / launchctl / schtasks, undecodable output
        raise RuntimeError(f"Could not register the scheduler: {e}") from e


def _install(run, platform, home) -> str:
    plat = platform or sys.platform
    cmd = command()
    env = _job_env()
    if plat.startswith("win"):
        # A tiny VBScript sets the environment and the folder, then runs pythonw hidden (schtasks /TR is length-limited).
        d = Path(home) if home else _data_home()
        d.mkdir(parents=True, exist_ok=True)
        vb = d / "meshpool-scheduler.vbs"
        q = lambda x: str(x).replace('"', '""')  # noqa: E731
        text = ('Set sh = CreateObject("WScript.Shell")\r\nSet e = sh.Environment("Process")\r\n'
                + "".join(f'e("{k}") = "{q(v)}"\r\n' for k, v in env.items())
                + f'sh.CurrentDirectory = "{q(d)}"\r\n'
                + 'sh.Run """' + q(_windowless(cmd[0])) + '"" -m meshpool_harness run-due", 0, True\r\n')
        # Windows Script Host reads a script as the ANSI code page unless it starts with a UTF-16 BOM: UTF-8 turned a
        # non-ASCII profile path (C:\Users\José) into mojibake and every scheduled run failed. UTF-16LE with its BOM,
        # written as bytes so no newline translation doubles the \r.
        vb.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
        tr = f'wscript.exe //B //Nologo "{vb}"'
        r = run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "10", "/TN", "Meshpool scheduler", "/TR", tr])
        if r.returncode:
            raise RuntimeError("Task Scheduler refused: " + (r.stderr or r.stdout)[-200:])
        return "Registered with Task Scheduler (every 10 minutes)."
    if plat == "darwin":
        from xml.sax.saxutils import escape as x
        home = home or Path.home()
        p = home / "Library" / "LaunchAgents" / f"{PLIST}.plist"
        p.parent.mkdir(parents=True, exist_ok=True)
        args = "".join(f"<string>{x(c)}</string>" for c in cmd)
        envx = "".join(f"<key>{x(k)}</key><string>{x(v)}</string>" for k, v in env.items())
        p.write_text('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
                     f"<key>Label</key><string>{PLIST}</string><key>ProgramArguments</key><array>{args}</array>"
                     f"<key>EnvironmentVariables</key><dict>{envx}</dict>"
                     f"<key>WorkingDirectory</key><string>{x(str(_data_home()))}</string>"
                     "<key>StartInterval</key><integer>600</integer><key>RunAtLoad</key><false/></dict></plist>", encoding="utf-8")
        run(["launchctl", "unload", str(p)])
        r = run(["launchctl", "load", str(p)])
        if r.returncode:
            raise RuntimeError("launchd refused: " + (r.stderr or r.stdout)[-200:])
        return "Registered with launchd (every 10 minutes)."
    import shlex
    line = (f"cd {shlex.quote(str(_data_home()))} && env " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
            + " " + " ".join(shlex.quote(c) for c in cmd)).replace("%", "\\%")   # % is special in a crontab line
    lines = [x for x in _crontab_lines(run) if TAG not in x]
    lines.append(f"*/10 * * * * {line} >/dev/null 2>&1 {TAG}")
    r = run(["crontab", "-"], input="\n".join(lines) + "\n")
    if r.returncode:
        raise RuntimeError("crontab refused: " + (r.stderr or r.stdout)[-200:])
    return "Registered with cron (every 10 minutes)."


def uninstall(run=_run, platform: str | None = None, home: Path | None = None) -> str:
    try:
        return _uninstall(run, platform, home)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"Could not remove the scheduler: {e}") from e


def _uninstall(run, platform, home) -> str:
    plat = platform or sys.platform
    if plat.startswith("win"):
        run(["schtasks", "/Delete", "/F", "/TN", "Meshpool scheduler"])
        with contextlib.suppress(OSError):
            ((Path(home) if home else _data_home()) / "meshpool-scheduler.vbs").unlink()
    elif plat == "darwin":
        p = (home or Path.home()) / "Library" / "LaunchAgents" / f"{PLIST}.plist"
        run(["launchctl", "unload", str(p)])
        if p.exists():
            p.unlink()
    else:
        old = _crontab_lines(run)
        lines = [x for x in old if TAG not in x]
        if len(lines) != len(old):                       # only when meshpool's line is there: nothing else changes
            run(["crontab", "-"], input="\n".join(lines) + ("\n" if lines else ""))
    return "Removed. Scheduled tasks run only while Meshpool is open."


def installed(run=_run, platform: str | None = None, home: Path | None = None) -> bool:
    plat = platform or sys.platform
    try:
        if plat.startswith("win"):
            return run(["schtasks", "/Query", "/TN", "Meshpool scheduler"]).returncode == 0
        if plat == "darwin":
            return ((home or Path.home()) / "Library" / "LaunchAgents" / f"{PLIST}.plist").exists()
        cur = run(["crontab", "-l"])
        return cur.returncode == 0 and TAG in (cur.stdout or "")
    except (OSError, ValueError):
        return False


def available(platform: str | None = None) -> bool:
    """Whether this computer has the tool the registration needs (cron on Linux, always on macOS and Windows)."""
    import shutil
    plat = platform or sys.platform
    return True if plat.startswith("win") or plat == "darwin" else bool(shutil.which("crontab"))


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
