"""The Terminal panel's shell, in a real pseudo-terminal: colours, prompts that wait for an answer (sudo, git, npm init)
and full-screen programs work, and the window size follows the panel. ptyprocess runs it on Mac and Linux, pywinpty
on Windows. When neither can be imported, backend() is "" and the panel keeps the one-command mode
(workbench.term_run).

Only the person at the keyboard types into it. The page sends their keys to the /api/pty routes, which need the page's
token like /api/term does. The agent never writes here: its commands go through its own tools and their permission and
approval rules.

The transport is plain polling: the page asks for what is new since an offset, and the server waits up to a second
for output before it answers (read). Stop, a chat being deleted and the app quitting end the shell and every process
it started, on Windows too (kill_tree).
"""
from __future__ import annotations

import codecs
import os
import signal
import subprocess
import sys
import threading
import time
import uuid

BACKLOG = 1_000_000          # characters kept per shell, so a panel opened again shows what was printed
MAX_LIVE = 12                # shells running at once
MAX_INPUT = 65536            # characters in one /input request
CHUNK = 8192                 # output is kept in pieces of about this size; the oldest go first
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_LOCK = threading.Lock()
PTYS: dict[str, "Pty"] = {}


def backend() -> str:
    """"ptyprocess", "pywinpty", or "" when the panel must use the one-command mode. MH_TERMINAL_PTY=0 forces ""."""
    if os.environ.get("MH_TERMINAL_PTY", "").strip().lower() in ("0", "off", "no", "false"):
        return ""
    try:
        if os.name == "nt":
            import winpty  # noqa: F401
            return "pywinpty"
        import ptyprocess  # noqa: F401
        return "ptyprocess"
    except Exception:  # noqa: BLE001 - missing, or a broken native part: the one-command mode still works
        return ""


def info() -> dict:
    return {"pty": backend() or None}


def shell_argv(shell: str = "", nt: bool | None = None) -> list[str]:
    """The interactive shell for the panel: the login shell on Mac and Linux; PowerShell, Command Prompt or Git Bash
    on Windows (the same choices as the one-command mode)."""
    nt = os.name == "nt" if nt is None else nt
    if nt:
        bash = os.environ.get("MH_BASH")
        shell = shell or "powershell"
        if shell == "bash" and bash and os.path.exists(bash):
            return [bash, "--login", "-i"]
        if shell == "cmd":
            return [os.environ.get("COMSPEC") or "cmd.exe"]
        return ["powershell.exe", "-NoLogo"]
    sh = os.environ.get("SHELL") or "/bin/bash"
    if not os.path.exists(sh):
        sh = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"
    return [sh, "-l"]


def _env(argv: list[str]) -> dict:
    from meshpool_harness import config as C
    env = C.user_env()
    env.update(TERM="xterm-256color", COLORTERM="truecolor", TERM_PROGRAM="meshpool")
    for k in ("NO_COLOR", "COLUMNS", "LINES"):
        env.pop(k, None)
    if os.name == "nt":
        if argv[0] == os.environ.get("MH_BASH"):    # Git Bash (config.msys_lang)
            C.msys_lang(env)
    elif not any(env.get(k) for k in ("LC_ALL", "LC_CTYPE", "LANG")):
        env["LANG"] = "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8"   # an app started from the Dock has none
    return {str(k): str(v) for k, v in env.items()}


# ---------------------------------------------------------------- ending a whole process tree
def _descendants(pid: int) -> list[int]:
    """Every process the shell started: its children, theirs, and on Mac and Linux anything left in its session (a
    background job whose parent already exited)."""
    found: set[int] = set()
    try:
        import psutil
    except ImportError:
        return []
    try:
        found.update(c.pid for c in psutil.Process(pid).children(recursive=True))
    except psutil.Error:
        pass
    if os.name != "nt":
        me = os.getpid()
        for p in psutil.process_iter():
            try:
                if p.pid not in (me, pid) and os.getsid(p.pid) == pid:
                    found.add(p.pid)
            except OSError:
                pass
    found.discard(pid)
    return sorted(found)


def _alive(pid: int) -> bool:
    try:
        import psutil
        p = psutil.Process(pid)
        return p.status() != psutil.STATUS_ZOMBIE
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    except Exception:  # noqa: BLE001 - gone
        return False


def kill_tree(pid: int, grace: float = 1.0) -> None:
    """End a process and everything it started. Windows: taskkill /T, then any child it missed. Mac and Linux: SIGHUP
    and SIGTERM to the shell, its jobs and its session, then SIGKILL to whatever is still there after `grace`."""
    if not pid:
        return
    kids = _descendants(pid)
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=20,
                           creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
        for k in [pid, *kids]:
            if _alive(k):
                try:
                    import psutil
                    psutil.Process(k).kill()
                except Exception:  # noqa: BLE001
                    pass
        return
    targets = [pid, *kids]
    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGCONT):
        for k in targets:
            try:
                os.kill(k, sig)
            except OSError:
                pass
    end = time.time() + grace
    while time.time() < end and any(_alive(k) for k in targets):
        time.sleep(0.05)
    for k in {*targets, *_descendants(pid)}:          # asked again: a job may have started something meanwhile
        if _alive(k):
            try:
                os.kill(k, signal.SIGKILL)
            except OSError:
                pass


# ---------------------------------------------------------------- one shell
class Pty:
    def __init__(self, root: str, owner: str, shell: str, cols: int, rows: int):
        self.id = uuid.uuid4().hex[:12]
        self.root, self.owner, self.shell = root, owner, shell
        self.cols, self.rows = _clamp(cols, 20, 500, 80), _clamp(rows, 4, 300, 24)
        self.chunks: list[str] = []
        self.base = 0                    # offset of chunks[0] in everything the shell printed
        self.end = 0                     # offset after the last character
        self.size = 0
        self.rc: int | None = None
        self.start = time.time()
        self.cond = threading.Condition()
        self.closing = False
        self.kind = backend()
        self.argv = shell_argv(shell)
        env = _env(self.argv)
        if self.kind == "ptyprocess":
            from ptyprocess import PtyProcess
            self.proc = PtyProcess.spawn(self.argv, cwd=root, env=env, dimensions=(self.rows, self.cols))
        elif self.kind == "pywinpty":
            from winpty import PtyProcess as WinPty
            self.proc = WinPty.spawn(self.argv, cwd=root, env=env, dimensions=(self.rows, self.cols))
        else:
            raise ValueError("No terminal library is installed, so the terminal runs one command at a time.")
        self.pid = int(getattr(self.proc, "pid", 0) or 0)
        threading.Thread(target=self._pump, name=f"pty-{self.id}", daemon=True).start()

    def _pump(self):
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")    # a character split across two reads
        while True:
            try:
                data = self.proc.read(65536)
            except EOFError:
                break
            except OSError:
                break
            except Exception:  # noqa: BLE001 - a closed pywinpty handle raises its own errors
                break
            if not data:
                if not self._isalive():
                    break
                time.sleep(0.02)
                continue
            self._append(dec.decode(data) if isinstance(data, bytes) else data)
        rest = dec.decode(b"", final=True)
        if rest:
            self._append(rest)
        rc = self._exit_code()
        with self.cond:
            self.rc = rc
            self.cond.notify_all()

    def _isalive(self) -> bool:
        try:
            return bool(self.proc.isalive())
        except Exception:  # noqa: BLE001
            return False

    def _exit_code(self) -> int:
        for _ in range(40):                      # the reader can see the end a moment before the process is reaped
            if not self._isalive():
                break
            time.sleep(0.05)
        try:
            if self.kind == "ptyprocess" and not self._isalive():
                self.proc.wait()
        except Exception:  # noqa: BLE001
            pass
        st = getattr(self.proc, "exitstatus", None)
        sig = getattr(self.proc, "signalstatus", None)
        if st is not None:
            return int(st)
        return 128 + int(sig) if sig else (-1 if self.closing else 0)

    def _append(self, text: str):
        with self.cond:
            if self.chunks and len(self.chunks[-1]) < CHUNK:
                self.chunks[-1] += text
            else:
                self.chunks.append(text)
            self.end += len(text)
            self.size += len(text)
            while self.size > BACKLOG and len(self.chunks) > 1:
                old = self.chunks.pop(0)
                self.size -= len(old)
                self.base += len(old)
            self.cond.notify_all()

    def view(self, since: int = 0, wait: float = 0.0) -> dict:
        since = max(0, int(since))
        with self.cond:
            if since > self.end:                 # an offset from an older shell or server: start again from what is kept
                since = 0
            if since >= self.end and self.rc is None and wait > 0:
                self.cond.wait(min(wait, 1.0))
            start = max(since, self.base)
            out, pos = [], self.base
            for c in self.chunks:
                stop = pos + len(c)
                if stop > start:
                    out.append(c[max(0, start - pos):])
                pos = stop
            return {"id": self.id, "data": "".join(out), "next": self.end, "running": self.rc is None, "rc": self.rc,
                    "lost": start - since, "cols": self.cols, "rows": self.rows, "shell": self.shell,
                    "root": self.root}

    def write(self, data: str):
        if self.rc is not None:
            raise ValueError("The shell has ended. Start a new one.")
        data = str(data or "")[:MAX_INPUT]
        if not data:
            return
        if self.kind == "ptyprocess":
            self.proc.write(data.encode("utf-8"))
        else:
            self.proc.write(data)

    def resize(self, cols: int, rows: int):
        self.cols, self.rows = _clamp(cols, 20, 500, self.cols), _clamp(rows, 4, 300, self.rows)
        if self.rc is None:
            try:
                self.proc.setwinsize(self.rows, self.cols)
            except Exception:  # noqa: BLE001 - it ended in between
                pass

    def close(self, grace: float = 1.0):
        """End the shell and everything it started, and close the terminal."""
        self.closing = True
        if self.rc is None:
            kill_tree(self.pid, grace)
        try:
            if self.kind == "ptyprocess":
                self.proc.close(force=True)
            else:
                self.proc.terminate(True)
        except Exception:  # noqa: BLE001
            pass
        with self.cond:                           # the reader sees its end once the terminal is closed
            end = time.time() + 3
            while self.rc is None and time.time() < end:
                self.cond.wait(0.1)
            if self.rc is None:
                self.rc = -1
            self.cond.notify_all()


def _clamp(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- the module API server.py uses
def open_pty(root: str, owner: str = "", shell: str = "", cols: int = 80, rows: int = 24, new: bool = False) -> dict:
    """The shell of this chat in this folder: the running one (a panel opened again, a page reloaded), or a new one."""
    from meshpool_harness.ui import workbench as W
    r = str(W.check_root(root))
    with _LOCK:
        if not new:
            for p in PTYS.values():
                if p.owner == owner and p.root == r and p.rc is None and (not shell or p.shell == shell):
                    p.resize(cols, rows)
                    return p.view(0)
        for k in [k for k, p in PTYS.items() if p.rc is not None][:-8]:    # ended shells keep their text a while
            PTYS.pop(k, None)
        if sum(1 for p in PTYS.values() if p.rc is None) >= MAX_LIVE:
            raise ValueError(f"{MAX_LIVE} terminals are already open. Stop one first.")
        p = Pty(r, owner, shell, cols, rows)
        PTYS[p.id] = p
    return p.view(0)


def get(tid: str) -> Pty:
    p = PTYS.get(tid)
    if not p:
        raise KeyError(tid)
    return p


def read(tid: str, since: int = 0, wait: float = 0.0) -> dict:
    return get(tid).view(since, wait)


def write(tid: str, data: str) -> dict:
    p = get(tid)
    p.write(data)
    return {"ok": True}


def resize(tid: str, cols: int, rows: int) -> dict:
    p = get(tid)
    p.resize(cols, rows)
    return {"cols": p.cols, "rows": p.rows}


def stop(tid: str) -> dict:
    p = get(tid)
    p.close()
    return p.view(0)


def stop_owner(owner: str) -> None:
    """A chat is closed: its shells end."""
    if not owner:
        return
    _stop_many([p for p in list(PTYS.values()) if p.owner == owner])


def stop_all() -> None:
    """The app is quitting: no shell (or the dev server running in it) outlives it."""
    _stop_many(list(PTYS.values()))


def _stop_many(ps: list) -> None:
    ths = [threading.Thread(target=p.close, args=(0.5,), daemon=True) for p in ps if p.rc is None]
    for th in ths:                                # all at once: ten shells must not take ten seconds
        th.start()
    for th in ths:
        th.join(8)


def owns_live(owner: str) -> bool:
    return bool(owner) and any(p.owner == owner and p.rc is None for p in list(PTYS.values()))


def running_in(root: str) -> list[dict]:
    """The shells open in a folder, for the session panel's background list."""
    return [{"kind": "terminal", "id": p.id, "title": f"Terminal ({os.path.basename(p.argv[0])})",
             "elapsed": round(time.time() - p.start, 1)}
            for p in list(PTYS.values()) if os.path.realpath(p.root) == os.path.realpath(root) and p.rc is None]
