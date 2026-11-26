"""One LibreOffice per meshpool process, kept running between conversions.

Every deck render, workbook recalculation, preview and document conversion used to start a fresh LibreOffice with a
new profile: half a second of CPU on a fast Linux machine, several seconds on a laptop, and a dozen processes each
time. Here one LibreOffice is started with --accept and a small worker (lo_worker.py, in LibreOffice's own Python,
which has the uno module) converts each file in it. A conversion then costs only the conversion itself.

- Started on first use, closed after MH_LO_IDLE_S seconds unused (default 300) and when meshpool exits.
- One job at a time (LibreOffice is single-threaded inside); callers wait their turn.
- Its own throwaway profile, made once per start with the bundled fonts and the always-recalculate settings
  (engines.forge.common._profile_fonts). It never touches the user's own LibreOffice or its profile.
- Any failure (no Python with uno, a crash, a timeout, a file it cannot open) returns None or raises, and the caller
  runs the one-off conversion as before. A session that cannot start is not tried again for LO_RETRY_S.
- Documents are opened hidden, read-only, with macros never run and links never updated.
- MH_LO_RESIDENT=0 turns it off.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

START_S = 60.0
LO_RETRY_S = 600.0
WORKER = Path(__file__).with_name("lo_worker.py")


def enabled() -> bool:
    return os.environ.get("MH_LO_RESIDENT", "1") != "0"


def idle_s() -> float:
    try:
        return max(5.0, float(os.environ.get("MH_LO_IDLE_S", "300")))
    except ValueError:
        return 300.0


def _group_kw() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x08000000}   # no window
    return {"start_new_session": True}


_PY: dict = {}


def real_office(exe: str) -> bool:
    """A LibreOffice install, not a stand-in script: soffice.bin (soffice.bin.exe) beside it, or the Mac app layout."""
    try:
        here = Path(os.path.realpath(exe)).parent
    except OSError:
        return False
    return ((here / "soffice.bin").is_file() or (here / "soffice.bin.exe").is_file()
            or (here.name == "MacOS" and (here.parent / "Resources").is_dir() and (here.parent / "Info.plist").is_file()))


def lo_python(exe: str) -> str | None:
    """A Python that can import uno and drive this LibreOffice: its own (program/python, python.exe, or the Mac app's
    Resources/python), MH_LO_PYTHON, or for a system LibreOffice the system python3. Checked once per program."""
    try:
        st = os.stat(exe)
        key = (os.path.realpath(exe), st.st_mtime_ns)
    except OSError:
        return None
    if key in _PY:
        return _PY[key]
    here = Path(exe).resolve().parent
    cands = [os.environ.get("MH_LO_PYTHON", ""), str(here / "python.exe"), str(here / "python"),
             str(here.parent / "Resources" / "python")]
    if not str(here).startswith(str(Path(__file__).resolve().parents[1])):   # a system copy, not the bundled one
        cands += ["/usr/bin/python3", shutil.which("python3") or "", sys.executable]   # Linux: python3-uno
    found = None
    for c in dict.fromkeys(x for x in cands if x):
        if not os.path.isfile(c):
            continue
        try:
            r = subprocess.run([c, "-c", "import uno"], capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                               **({"creationflags": 0x08000000} if os.name == "nt" else {}))
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            found = c
            break
    _PY[key] = found
    return found


class ConvertError(RuntimeError):
    pass


class Session:
    def __init__(self, exe: str, py: str):
        self.exe, self.py = exe, py
        self.lock = threading.Lock()
        self.office = self.worker = None
        self.profile: Path | None = None
        self.lines: queue.Queue | None = None
        self.last = time.time()
        self.next_id = 0
        self.starts = self.jobs = 0
        self.broken_until = 0.0
        self._idle: threading.Thread | None = None

    # ---------------------------------------------------------------- life
    def alive(self) -> bool:
        return bool(self.office and self.worker and self.office.poll() is None and self.worker.poll() is None)

    def _start(self) -> None:
        from engines.forge.common import _profile_fonts
        self.profile = Path(tempfile.mkdtemp(prefix="meshpool-lo-"))
        _profile_fonts(self.profile)
        pipe = f"meshpool_{os.getpid()}_{uuid.uuid4().hex[:12]}"
        self.office = subprocess.Popen(
            [self.exe, f"-env:UserInstallation={self.profile.as_uri()}", "--headless", "--invisible", "--norestore",
             "--nologo", "--nodefault", "--nolockcheck", f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_group_kw())
        self.worker = subprocess.Popen([self.py, str(WORKER), pipe, str(START_S)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                       bufsize=1, **_group_kw())
        self.lines = q = queue.Queue()
        out = self.worker.stdout

        def pump():
            for line in out:
                q.put(line)
            q.put(None)
        threading.Thread(target=pump, daemon=True, name="meshpool-lo-reader").start()
        first = self._read(START_S + 10)
        if not first or not first.get("ready"):
            self._stop()
            raise ConvertError("the LibreOffice session did not start: " + str((first or {}).get("error", "no answer")))
        self.starts += 1

    def _stop(self) -> None:
        from engines.forge.common import _kill_tree
        w, o, prof = self.worker, self.office, self.profile
        self.worker = self.office = self.profile = None
        if w is not None:
            try:
                w.stdin.close()
            except OSError:
                pass
            _kill_tree(w)
        if o is not None:
            _kill_tree(o)
            try:
                o.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        if prof is not None:
            shutil.rmtree(prof, ignore_errors=True)

    def close(self) -> None:
        with self.lock:
            self._stop()

    def _read(self, timeout: float):
        try:
            line = self.lines.get(timeout=max(0.1, timeout))
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            return json.loads(line)
        except ValueError:
            return {"ok": False, "error": "unreadable answer"}

    def _arm_idle(self) -> None:
        if self._idle is not None and self._idle.is_alive():
            return

        def watch():
            while True:
                time.sleep(min(30.0, idle_s() / 3))
                if self.office is None:
                    return
                if time.time() - self.last >= idle_s() and self.lock.acquire(blocking=False):
                    try:
                        if time.time() - self.last >= idle_s():
                            self._stop()
                            return
                    finally:
                        self.lock.release()
        self._idle = threading.Thread(target=watch, daemon=True, name="meshpool-lo-idle")
        self._idle.start()

    # ---------------------------------------------------------------- work
    def convert(self, src: str, out: str, ext: str, filt: str = "", options: str = "", infilter: str = "",
                recalc: bool = False, timeout: float = 240) -> None:
        # a long job (a 240 s deck render) must not make a preview or a recalculation wait its whole length first:
        # busy, the caller runs its own one-off conversion as before the kept session (review, 0.0.18)
        if not self.lock.acquire(timeout=max(1.0, min(float(timeout), BUSY_WAIT_S))):
            raise ConvertError("the LibreOffice session is busy")
        try:
            self._convert(src, out, ext, filt, options, infilter, recalc, timeout)
        finally:
            self.lock.release()

    def _convert(self, src, out, ext, filt, options, infilter, recalc, timeout) -> None:
        if time.time() < self.broken_until:
            raise ConvertError("the LibreOffice session failed recently")
        if not self.alive():
            self._stop()
            try:
                self._start()
            except Exception:
                self.broken_until = time.time() + LO_RETRY_S
                raise
        self.next_id += 1
        req = {"id": self.next_id, "src": src, "out": out, "ext": ext, "filter": filt, "options": options,
               "infilter": infilter, "recalc": recalc}
        try:
            self.worker.stdin.write(json.dumps(req) + "\n")
            self.worker.stdin.flush()
        except OSError as e:
            self._stop()
            raise ConvertError(f"the LibreOffice session stopped: {e}") from e
        rep = self._read(timeout)
        self.last = time.time()
        if rep is None or rep.get("id") != req["id"]:
            self._stop()                                 # stuck on a file, or gone: the next call starts afresh
            raise ConvertError("the LibreOffice session stopped answering")
        self._arm_idle()
        if not rep.get("ok"):
            raise ConvertError(str(rep.get("error") or "conversion failed"))
        self.jobs += 1


_SESSIONS: dict = {}
_LOCK = threading.Lock()
BUSY_WAIT_S = 30.0


def session(exe: str) -> Session | None:
    if not enabled() or not exe or not real_office(exe):
        return None
    with _LOCK:
        s = _SESSIONS.get(exe)
        if s is None:
            py = lo_python(exe)
            if not py:
                return None
            s = _SESSIONS[exe] = Session(exe, py)
        return s


def convert(exe: str, src: str, to: str, out_dir: str, timeout: float = 240, recalc: bool = False,
            infilter: str = "") -> Path | None:
    """Convert src the way `soffice --convert-to <to> --outdir <out_dir>` would, in the kept LibreOffice. Returns the
    file it wrote, or None when the session is not available or failed; the caller then runs the one-off command."""
    s = session(exe)
    if s is None:
        return None
    ext, filt, options = (str(to).split(":", 2) + ["", ""])[:3]
    out = Path(out_dir) / (Path(src).stem + "." + ext)
    part = out.with_name(f"{out.stem}.meshpool-{os.getpid()}-{uuid.uuid4().hex[:6]}.{ext}")   # never over src
    try:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        s.convert(os.path.abspath(src), os.path.abspath(part), ext, filt, options, infilter, recalc, timeout)
        if not part.is_file():
            return None
        os.replace(part, out)
        return out
    except Exception:  # noqa: BLE001 - the one-off run is the fallback
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def stats() -> dict:
    return {exe: {"starts": s.starts, "jobs": s.jobs, "running": s.alive()} for exe, s in _SESSIONS.items()}


def close_all() -> None:
    for s in list(_SESSIONS.values()):
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


def kill_all() -> None:
    """Stop every kept LibreOffice now, without waiting for a conversion in progress (the app is closing). A
    conversion that starts one after this is not orphaned: its worker ends LibreOffice once the app's pipe closes."""
    for s in list(_SESSIONS.values()):
        try:
            s._stop()
        except Exception:  # noqa: BLE001
            pass


atexit.register(close_all)
