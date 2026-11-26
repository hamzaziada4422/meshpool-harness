"""Desktop mode: what the Mac app and the Windows app run (`python -m meshpool_harness app`).

- The interface opens in a native window (WKWebView on macOS, WebView2 on Windows). Closing the window quits.
- Settings and runs live in the user's app-data folder (MESHPOOL_HOME).
- One instance: opening the app again brings the running window to the front.
- If the native window cannot start, the page opens as a Chrome or Edge app window instead.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from meshpool_harness import config as C

NO_WINDOW = 0x08000000


def _hide_child_consoles():
    """Under pythonw.exe every console child (bash, git, npx) would flash a window. Stop that."""
    if os.name != "nt":
        return
    orig = subprocess.Popen.__init__
    if getattr(orig, "_meshpool", False):
        return

    def init(self, *a, **kw):
        kw["creationflags"] = (kw.get("creationflags") or 0) | NO_WINDOW
        orig(self, *a, **kw)
    init._meshpool = True
    subprocess.Popen.__init__ = init


def _bundled_chromium() -> list[list[str]]:
    """The app's own Chromium (app/vendor/browsers) as an app window command, [] when it is not there or cannot run on
    this computer (its Chromium needs macOS 13; the app itself opens on macOS 11)."""
    try:
        from meshpool_harness import bundled
        root = bundled.browsers_dir()
        if bundled.browser_needs_newer_macos() or not root.is_dir():
            return []
        flags = _APP_FLAGS
        if sys.platform == "darwin":
            apps = sorted(root.glob("chromium-*/chrome-mac*/Chromium.app"))
            return [["open", "-na", str(apps[-1]), "--args", *flags]] if apps else []
        pat = "chromium-*/chrome-win*/chrome.exe" if os.name == "nt" else "chromium-*/chrome-linux*/chrome"
        own = sorted(root.glob(pat))
        return [[str(own[-1]), *flags]] if own else []
    except Exception:  # noqa: BLE001  the installed browsers below still work
        return []


_APP_FLAGS = ["--app={url}", "--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
              "--window-size=1380,900"]


def _app_candidates() -> list[list[str]]:
    """Commands that open a URL as a chromeless app window, best first: the app's own Chromium on every platform, then,
    only as a last resort, a Chrome, Edge or Brave the user already has. {url} and {profile} get filled in."""
    flags = _APP_FLAGS
    out = _bundled_chromium()
    if sys.platform == "darwin":
        for name in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"):
            for base in (Path("/Applications"), Path.home() / "Applications"):
                app = base / f"{name}.app"
                if app.exists():
                    out.append(["open", "-na", str(app), "--args", *flags])
    elif os.name == "nt":
        roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        rels = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
                r"BraveSoftware\Brave-Browser\Application\brave.exe"]
        for rel in rels:
            for r in roots:
                if r and (Path(r) / rel).exists():
                    out.append([str(Path(r) / rel), *flags])
    else:
        for b in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            p = shutil.which(b)
            if p:
                out.append([p, *flags])
    return out


def open_window(url: str) -> None:
    profile = str(C.runs_dir() / "window-profile")
    for cmd in _app_candidates():
        try:
            subprocess.Popen([c.format(url=url, profile=profile) for c in cmd], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            return
        except OSError:
            continue
    if sys.platform == "darwin":
        subprocess.Popen(["open", url])
    elif os.name == "nt":
        os.startfile(url)  # noqa: S606
    else:
        webbrowser.open(url)


def _running_instance() -> str | None:
    p = C.runs_dir() / ".app.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{int(d['port'])}/"
        with urllib.request.urlopen(url + "healthz", timeout=2) as r:
            if json.loads(r.read()).get("app") == "meshpool":
                return url
    except Exception:
        return None
    return None


def _show_running(url: str) -> bool:
    try:
        req = urllib.request.Request(url + "show", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return bool(json.loads(r.read()).get("native"))
    except Exception:
        return False


def _log_to_file() -> None:
    """An app started from Finder or the Start menu has no console. Keep a log in the data folder."""
    try:
        if sys.stdout is not None and sys.stdout.isatty():
            return
    except (AttributeError, ValueError):
        pass
    log = C.runs_dir() / "app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        from meshpool_harness.logfiles import PipedLog, RotatingFile   # H-23: the app log rotates by size while it runs
        if os.name != "nt":
            f = PipedLog(log)                  # native output (fd 1 and 2) goes through the same rotating file
            sys.stdout = sys.stderr = f.sink
            try:
                os.dup2(f.fileno(), 1)
                os.dup2(f.fileno(), 2)
            except OSError:
                pass
            f.release()
        else:
            sys.stdout = sys.stderr = RotatingFile(log)
    except OSError:
        return


def _mac_environment() -> None:
    """Apps opened from Finder get a bare PATH and run as the launcher binary. Fix both for child processes."""
    py = os.environ.get("MESHPOOL_PYTHON")
    if py and Path(py).exists():
        sys.executable = py                    # helpers (Hermes, MCP servers, engines) run on the bundled python3
    path = os.environ.get("PATH", "")
    try:
        shell = os.environ.get("SHELL") or "/bin/zsh"
        r = subprocess.run([shell, "-ilc", 'printf "__MP__%s" "$PATH"'], capture_output=True, text=True,
                           timeout=6, stdin=subprocess.DEVNULL)
        login = r.stdout.rsplit("__MP__", 1)[-1].strip() if "__MP__" in r.stdout else ""
    except (OSError, subprocess.TimeoutExpired):
        login = ""
    parts = []
    for chunk in (str(Path(sys.executable).parent), login, path,
                  "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"):
        for x in chunk.split(":"):
            if x and x not in parts:
                parts.append(x)
    os.environ["PATH"] = ":".join(parts)


def _bundled_tools() -> None:
    """Point agents at the tools that ship inside the app (Git and Bash on Windows, the browser engine)."""
    root = C.APP.parent
    if os.name == "nt":
        git = root / "git"
        bash = git / "usr" / "bin" / "bash.exe"
        if bash.exists():
            os.environ.setdefault("MH_BASH", str(bash))
            os.environ.setdefault("HERMES_GIT_BASH_PATH", str(bash))
            extra = [str(git / "cmd"), str(git / "usr" / "bin")]
            os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])
    # Node.js, Git and LibreOffice's folder ship with the app and go FIRST on PATH: the app uses its own copies, never a
    # Node or Git the customer installed (meshpool_harness/bundled.py).
    from meshpool_harness import bundled
    bundled.use_bundled_tools()
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled.browsers_dir() if bundled.packaged() else C.home() / "browsers"))


WINDOW: list = [None]


def _native_window(url: str) -> bool:
    """Show the app in a native window and block until it closes. False if no native window could open."""
    try:
        import webview
    except Exception as e:  # noqa: BLE001
        print(f"Native window unavailable ({e}); using a browser window.")
        return False
    gui = "cocoa" if sys.platform == "darwin" else "edgechromium" if os.name == "nt" else None
    try:
        logging.getLogger("pywebview").setLevel(logging.WARNING)
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        dark = _prefers_dark()
        from meshpool_harness.ui.window import window_size
        win = webview.create_window("Meshpool", url, background_color="#0B131F" if dark else "#F5F8FC",
                                    text_select=True, **window_size(webview))
        WINDOW[0] = win
        kw = {"gui": gui, "private_mode": False, "storage_path": str(C.runs_dir() / "webview")}
        ico = C.APP.parent / "Meshpool.ico"
        if os.name == "nt" and ico.exists():
            kw["icon"] = str(ico)
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WIN_AUMID)
            except Exception:  # noqa: BLE001
                pass
        webview.start(**kw)
        return True
    except Exception as e:  # noqa: BLE001
        WINDOW[0] = None
        print(f"Native window failed ({type(e).__name__}: {e}); using a browser window.")
        return False


def _prefers_dark() -> bool:
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True,
                               timeout=3)
            return "Dark" in r.stdout
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:  # noqa: BLE001
        return False
    return False


CHILD: list = [None]                       # Windows: the window process
QUITTING = [False]                         # the app is quitting (Quit button or idle): a closed window is expected


def _window_process(url: str) -> str:
    """Windows: show the window in a child process. Returns 'closed' when the user closed it, 'failed' when it could
    not open (no working WebView2), so the caller can fall back to an Edge app window."""
    import time
    env = dict(os.environ, MESHPOOL_DARK="1" if _prefers_dark() else "0",
               MESHPOOL_WEBVIEW_DATA=str(C.runs_dir() / "webview"),
               MESHPOOL_WINDOW_LOG=str(C.runs_dir() / "window.log"))
    exe = sys.executable
    pw = Path(exe).with_name("pythonw.exe")
    if pw.exists():
        exe = str(pw)
    t0 = time.time()
    try:
        p = subprocess.Popen([exe, "-m", "meshpool_harness.ui.window", url, str(C.APP.parent / "Meshpool.ico")],
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except OSError as e:
        print(f"Native window failed to start: {e}")
        return "failed"
    CHILD[0] = p
    rc = p.wait()
    CHILD[0] = None
    if QUITTING[0] or (rc == 0 and time.time() - t0 > 2):
        return "closed"
    print(f"Native window exited with code {rc}; using a browser window.")
    return "failed"


def _focus_windows() -> bool:
    try:
        import ctypes
        u = ctypes.windll.user32
        h = u.FindWindowW(None, "Meshpool")
        if not h:
            return False
        u.ShowWindow(h, 9)                             # SW_RESTORE
        u.SetForegroundWindow(h)
        return True
    except Exception:  # noqa: BLE001
        return False


def show_window() -> bool:
    if CHILD[0] is not None:
        return _focus_windows()
    w = WINDOW[0]
    if not w:
        return False
    try:
        w.restore()
        w.show()
        w.on_top = True
        w.on_top = False
    except Exception:  # noqa: BLE001
        pass
    return True


def close_window() -> None:
    QUITTING[0] = True
    p = CHILD[0]
    if p is not None:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
    w = WINDOW[0]
    if w:
        try:
            w.destroy()
        except Exception:  # noqa: BLE001
            pass


def run() -> int:
    if not os.environ.get("MESHPOOL_HOME"):
        os.environ["MESHPOOL_HOME"] = str(C.user_home_default())
    _hide_child_consoles()
    C.ensure_home()
    _log_to_file()
    if sys.platform == "darwin":
        _mac_environment()
    C.load_into_env()
    _bundled_tools()
    ensure_cli()
    import time
    url = _running_instance()
    restarted = os.environ.pop("MESHPOOL_RESTARTED", None)
    if url and restarted:                                      # started by an update restart: the old app is closing
        end = time.time() + 90
        while url and time.time() < end:
            time.sleep(0.5)
            url = _running_instance()
    if url:
        if not _show_running(url):
            open_window(url)
        return 0
    from meshpool_harness.ui import server as SV

    ready = threading.Event()
    box: dict = {}

    def on_ready(port: int):
        (C.runs_dir() / ".app.json").write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
        box["port"] = port
        ready.set()

    SV.NATIVE[0] = True
    SV.ON_SHOW.append(show_window)
    SV.ON_QUIT.append(close_window)
    t = threading.Thread(target=lambda: box.update(rc=SV.serve(int(os.environ.get("MH_UI_PORT", "8765")),
                                                                open_browser=False, desktop=True,
                                                                on_ready=on_ready)), daemon=True)
    t.start()
    try:
        if not ready.wait(60):
            print("The Meshpool server did not start. See the lines above.")
            return 1
        url = SV.app_url(box["port"])                  # the launch key: only this window gets the API token
        if os.name == "nt":
            shown = _window_process(url) == "closed"    # blocks until the window closes
        else:
            shown = _native_window(url)                # blocks until the window closes
        if shown:
            SV._shutdown()
            t.join(10)
        else:
            SV.NATIVE[0] = False
            open_window(url)
            t.join()
        return 0
    finally:
        try:
            (C.runs_dir() / ".app.json").unlink()
        except OSError:
            pass


# ---------- helpers the page calls ------------------------------------------------------------------------------

def has_folder_picker() -> bool:
    """False on a machine with no native folder dialog (e.g. Linux without zenity, browser mode)."""
    return WINDOW[0] is not None or sys.platform == "darwin" or os.name == "nt" or bool(shutil.which("zenity"))


class PickerUnavailable(Exception):
    """The native folder dialog could not open at all (no display, no helper): not the same as the user cancelling."""


def choose_folder(start: str = "") -> str:
    """Native folder picker. Returns '' when cancelled; raises PickerUnavailable when the dialog cannot open (F-05)."""
    start = start if start and Path(start).is_dir() else str(Path.home())
    w = WINDOW[0]
    if w is not None:                          # the app's own window: a native sheet attached to it
        try:
            r = w.create_file_dialog(20, directory=start)
            return str(r[0]) if r else ""
        except Exception:  # noqa: BLE001
            pass
    try:
        if sys.platform == "darwin":
            q = str(start).replace("\\", "\\\\").replace('"', '\\"')
            script = (f'POSIX path of (choose folder with prompt "Choose a project folder for Meshpool" '
                      f'default location (POSIX file "{q}"))')
            r = subprocess.run(["osascript", "-e", "activate", "-e", script], capture_output=True, text=True,
                               timeout=600)
            return r.stdout.strip().rstrip("/") if r.returncode == 0 else ""
        if os.name == "nt":
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "$f.Description = 'Choose a project folder for Meshpool';"
                  "$f.SelectedPath = $env:MESHPOOL_PICK_START;"
                  "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true};"
                  "if ($f.ShowDialog($owner) -eq 'OK') { Write-Output $f.SelectedPath }")
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                               timeout=600, creationflags=NO_WINDOW, env=dict(os.environ, MESHPOOL_PICK_START=str(start)))
            return r.stdout.strip()
        if shutil.which("zenity"):
            r = subprocess.run(["zenity", "--file-selection", "--directory", f"--filename={start}/"],
                               capture_output=True, text=True, timeout=600)
            if r.returncode != 0 and r.stderr.strip() and not r.stdout.strip():     # a cancel prints nothing
                raise PickerUnavailable(r.stderr.strip()[:200])
            return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except OSError as e:
        raise PickerUnavailable(str(e)) from None
    return ""


def default_project() -> Path:
    return Path.home() / "Meshpool Projects"


def show_in_folder(path: Path) -> None:
    """Show a file or folder in the system file manager (Finder, File Explorer). Only the explicit "Show in ..."
    context-menu item calls this. It selects a file where the system can and never opens the file itself."""
    path = Path(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
    elif os.name == "nt":
        if path.is_file():
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(str(path))  # noqa: S606  a folder: File Explorer
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def reveal(path: Path) -> None:
    """Hand a path to the operating system. Nothing in the app calls this on a normal click any more: files and
    folders open inside Meshpool (ui/files_api.py). Kept for callers that explicitly want the system's own app."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # noqa: S606
    else:
        try:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise ValueError(f"No file manager is installed here. The folder is {path}") from None


def _cli_path() -> str:
    """The meshpool terminal command shipped with the app. The launcher names it; on Windows it is bin\\meshpool.cmd
    next to the app folder, which also covers a launcher built before the command moved there."""
    cli = os.environ.get("MESHPOOL_CLI", "")
    if os.name == "nt" and not (cli and Path(cli).exists()):
        alt = C.APP.parent / "bin" / "meshpool.cmd"
        if alt.exists():
            return str(alt)
    return cli


def cli_status() -> dict:
    cli = _cli_path()
    if not cli or not Path(cli).exists():
        return {"available": False}
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                cur = winreg.QueryValueEx(k, "Path")[0]
        except OSError:
            cur = ""
        d = str(Path(cli).parent)
        installed = d.lower() in [x.strip().lower() for x in cur.split(";")]
    else:
        want = os.path.realpath(cli)
        cands = [Path("/usr/local/bin/meshpool"), Path("/opt/homebrew/bin/meshpool"),
                 Path.home() / ".local" / "bin" / "meshpool"]
        hit = next((c for c in cands if c.exists() and os.path.realpath(c) == want), None)
        installed = hit is not None
        # In ~/.local/bin it works only in terminal windows opened after the PATH line was added.
        return {"available": True, "installed": installed, "path": cli,
                "new_window": bool(hit and hit.parent == Path.home() / ".local" / "bin")}
    return {"available": True, "installed": installed, "path": cli}


def ensure_cli() -> None:
    """Make the meshpool terminal command available without asking (the installer usually did it already)."""
    try:
        st = cli_status()
        if sys.platform == "darwin" and not st.get("path", "").startswith(
                ("/Applications/", str(Path.home() / "Applications") + "/")):
            return                             # a copy outside Applications (a download, a build): leave PATH alone
        if st.get("available") and not st.get("installed"):
            install_cli()
    except Exception:  # noqa: BLE001  best effort; Settings shows the button if this did not work
        pass


def install_cli(admin: bool = False) -> dict:
    cli = _cli_path()
    if not cli or not Path(cli).exists():
        raise ValueError("The terminal command is only available in the installed app.")
    if sys.platform == "darwin" and (cli.startswith("/Volumes/") or "/AppTranslocation/" in cli):
        raise ValueError("Drag Meshpool to your Applications folder first, open it from there, then try again.")
    if os.name == "nt":
        import ctypes
        import winreg
        d = str(Path(cli).parent)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                cur, kind = winreg.QueryValueEx(k, "Path")
            except OSError:
                cur, kind = "", winreg.REG_EXPAND_SZ
            parts = [x for x in cur.split(";") if x]
            if d.lower() not in [x.lower() for x in parts]:
                winreg.SetValueEx(k, "Path", 0, kind, ";".join(parts + [d]))
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
        return {"ok": True, "message": "Done. Open a new terminal, go to a project folder and type: meshpool"}
    # 1. A folder every terminal already has on its PATH (macOS lists /usr/local/bin in /etc/paths), so the command
    #    works at once, in windows that are already open too.
    for d in (Path("/usr/local/bin"), Path("/opt/homebrew/bin")):
        if d.is_dir() and os.access(d, os.W_OK) and (d != Path("/opt/homebrew/bin") or
                                                     (Path.home() / ".zprofile").exists()):
            _link(d / "meshpool", cli)
            return {"ok": True, "message": "Done. In any terminal window, go to a project folder and type: meshpool"}
    # 2. From the Settings button on a Mac: /usr/local/bin with the administrator password, like other apps do.
    if admin and sys.platform == "darwin":
        q = cli.replace("'", "'\\''")                                  # quoted for the shell...
        sh = f"mkdir -p /usr/local/bin && ln -sf '{q}' /usr/local/bin/meshpool"
        sh = sh.replace("\\", "\\\\").replace('"', '\\"')          # ...then for the AppleScript string
        r = subprocess.run(["osascript", "-e", f'do shell script "{sh}" with administrator privileges'],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return {"ok": True, "message": "Done. In any terminal window, go to a project folder and type: meshpool"}
    # 3. ~/.local/bin, added to PATH in the shell start-up files; new terminal windows pick it up.
    bindir = Path.home() / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    _link(bindir / "meshpool", cli)
    added = []
    line = 'export PATH="$HOME/.local/bin:$PATH"  # added by Meshpool'
    shell = Path(os.environ.get("SHELL") or ("/bin/zsh" if sys.platform == "darwin" else "")).name
    mine = {"zsh": (".zshrc",), "bash": (".bashrc",)}.get(shell, ())       # only the user's own shell's start-up file
    for rc in (Path.home() / n for n in mine):
        txt = rc.read_text(encoding="utf-8", errors="replace") if rc.exists() else ""
        if ".local/bin" not in txt:
            with open(rc, "a", encoding="utf-8") as f:
                f.write(("\n" if txt and not txt.endswith("\n") else "") + line + "\n")
            added.append(rc.name)
    return {"ok": True, "message": "Done. Open a new terminal window, go to a project folder and type: meshpool"
            + (f" (PATH added to {', '.join(added)})" if added else "")}


def _link(link: Path, target: str) -> None:
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target)


# ---------- OS permissions ----------------------------------------------------------------------------------------
# macOS asks per app for screen and input control. The app requests them from inside Meshpool.app, so they are
# granted to "Meshpool" in System Settings and apply to everything the agents run (computer use, browser use, shell).

_PANES = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "disk": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
}


def _mac_ax(prompt: bool) -> bool:
    import ctypes
    import ctypes.util
    ax = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
    if not prompt:
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(ax.AXIsProcessTrusted())
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    key = ctypes.c_void_p.in_dll(ax, "kAXTrustedCheckOptionPrompt")
    true = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
    cf.CFDictionaryCreate.restype = ctypes.c_void_p
    cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                      ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
    keys = (ctypes.c_void_p * 1)(key.value)
    vals = (ctypes.c_void_p * 1)(true.value)
    d = cf.CFDictionaryCreate(None, keys, vals, 1, None, None)
    ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
    return bool(ax.AXIsProcessTrustedWithOptions(d))


def _mac_screen(request: bool) -> bool:
    import ctypes
    import ctypes.util
    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    fn = cg.CGRequestScreenCaptureAccess if request else cg.CGPreflightScreenCaptureAccess
    fn.restype = ctypes.c_bool
    return bool(fn())


def _mac_disk() -> bool | None:
    for p in (Path.home() / "Library/Safari/Bookmarks.plist", Path.home() / "Library/Mail",
              Path("/Library/Application Support/com.apple.TCC/TCC.db")):
        if p.exists():
            try:
                if p.is_dir():
                    next(iter(p.iterdir()), None)
                else:
                    with open(p, "rb") as f:
                        f.read(1)
                return True
            except PermissionError:
                return False
            except OSError:
                continue
    return None


def _mac_folders(request: bool) -> bool | None:
    """Desktop, Documents and Downloads are protected on macOS. Listing them makes macOS ask once for each."""
    ok = True
    for name in ("Desktop", "Documents", "Downloads"):
        p = Path.home() / name
        if not p.exists():
            continue
        try:
            next(iter(p.iterdir()), None)
        except PermissionError:
            ok = False
        except OSError:
            pass
    return ok


def _mac_notify(title: str, body: str) -> bool:
    """Post a macOS notification in Meshpool's own name: from Meshpool.app's process (the launcher runs Python inside
    the app, so the main bundle is Meshpool.app) through Foundation's notification centre. False when this process is
    not Meshpool.app (a source checkout, the terminal app, a helper process): nothing is shown then.

    It used to run `osascript -e 'display notification ...'`. macOS files those under the scripting app, not Meshpool,
    so every "Done: ..." with the reply text arrived as another app's notification (users saw TextEdit or Script
    Editor), clicking it opened that app, and Meshpool's own notification setting could not turn them off."""
    try:
        from Foundation import NSBundle, NSUserNotification, NSUserNotificationCenter
    except Exception:  # noqa: BLE001 - no PyObjC: not the packaged app
        return False
    from meshpool_harness.autoupdate import MAC_BUNDLE_ID
    bundle = NSBundle.mainBundle()
    if not bundle or str(bundle.bundleIdentifier() or "") != MAC_BUNDLE_ID:
        return False
    center = NSUserNotificationCenter.defaultUserNotificationCenter()
    if center is None:
        return False
    n = NSUserNotification.alloc().init()
    n.setTitle_(title)
    if body:
        n.setInformativeText_(body)
    center.deliverNotification_(n)
    return True


WIN_AUMID = "Meshpool.Desktop"          # the app window's AppUserModelID (SetCurrentProcessExplicitAppUserModelID)
WIN_AUMID_KEY = "Software\\Classes\\AppUserModelId\\" + WIN_AUMID   # HKCU; also in installer.nsi
_POWERSHELL_AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


def _win_register_aumid() -> bool:
    """Register Meshpool's AppUserModelID for the current user, so a toast sent with it shows as "Meshpool" with its
    icon and has its own switch in Settings > Notifications. Toasts used to be sent with PowerShell's ID, so every
    "Done: ..." showed as "Windows PowerShell" and clicking it opened PowerShell. An unregistered ID makes Windows drop
    the toast without an error, so this returns False on any failure and the caller keeps PowerShell's ID then.
    HKCU only (no admin); the installer writes the same key and the uninstaller removes it."""
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WIN_AUMID_KEY) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "Meshpool")
            ico = C.APP.parent / "Meshpool.ico"
            if ico.is_file():
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(ico))
        return True
    except Exception:  # noqa: BLE001
        return False


def notify(title: str, body: str = "") -> None:
    """A desktop notification (best effort; never raises). macOS: only in Meshpool.app's own name (_mac_notify)."""
    title, body = (title or "Meshpool")[:120], (body or "")[:240]
    try:
        if sys.platform == "darwin":
            _mac_notify(title, body)
        elif os.name == "nt":
            sender = WIN_AUMID if _win_register_aumid() else _POWERSHELL_AUMID
            ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
                  "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                  "$x = $t.GetElementsByTagName('text'); $x.Item(0).AppendChild($t.CreateTextNode($env:MP_T)) > $null;"
                  "$x.Item(1).AppendChild($t.CreateTextNode($env:MP_B)) > $null;"
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:MP_ID).Show("
                  "[Windows.UI.Notifications.ToastNotification]::new($t))")
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             env=dict(os.environ, MP_T=title, MP_B=body, MP_ID=sender), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=0x08000000)
    except Exception:  # noqa: BLE001
        pass


def permissions() -> dict:
    rows = []
    if sys.platform == "darwin":
        def safe(fn, *a):
            try:
                return fn(*a)
            except Exception:
                return None
        rows = [
            {"id": "folders", "name": "Desktop, Documents and Downloads", "ok": safe(_mac_folders, False),
             "why": "Lets agents read and save files in these folders. macOS asks once for each."},
            {"id": "notifications", "name": "Notifications", "ok": None,
             "why": "Tells you when a long task or a scheduled task finishes, or needs your approval."},
            {"id": "accessibility", "name": "Accessibility", "ok": safe(_mac_ax, False),
             "why": "Lets computer use click, type and use the keyboard in other apps."},
            {"id": "screen", "name": "Screen Recording", "ok": safe(_mac_screen, False),
             "why": "Lets computer use see the screen. macOS asks you to reopen Meshpool after you allow it."},
            {"id": "disk", "name": "Full Disk Access", "ok": _mac_disk(),
             "why": "Optional. Lets agents read files in protected places such as Mail, Messages and other apps' data."},
            {"id": "automation", "name": "Automation", "ok": None,
             "why": "macOS asks the first time an agent controls another app (for example Finder or Chrome). Allow it then."},
        ]
        note = "Meshpool asks macOS for these in its own name, so they appear as Meshpool in System Settings."
    elif os.name == "nt":
        from meshpool_harness.config import find_bash
        rows = [{"id": "shell", "name": "Bash shell", "ok": bool(find_bash()),
                 "why": "Agents run commands with Git Bash. Install Git for Windows (git-scm.com) if this is off; "
                        "without it they use the Windows command prompt."}]
        note = ("Windows does not need extra permissions: agents run commands and control apps as you. "
                "Windows may ask to allow network access the first time a tool listens on a port.")
    else:
        note = "Linux does not need extra permissions."
    return {"platform": sys.platform, "rows": rows, "note": note}


def request_permission(pid: str) -> dict:
    if sys.platform != "darwin":
        if pid in ("notifications", "all"):
            notify("Meshpool notifications are on", "You will hear from Meshpool when long tasks finish.")
        return permissions()
    if pid == "all":                                  # ask for everything macOS lets an app request in one go
        for one in ("folders", "notifications", "accessibility", "screen"):
            try:
                request_permission(one)
            except Exception:  # noqa: BLE001
                pass
        return permissions()
    try:
        if pid == "folders":
            _mac_folders(True)
        elif pid == "notifications":
            notify("Meshpool notifications are on", "You will hear from Meshpool when long tasks finish.")
        elif pid == "accessibility":
            if not _mac_ax(True):
                subprocess.Popen(["open", _PANES["accessibility"]])
        elif pid == "screen":
            if not _mac_screen(True):
                subprocess.Popen(["open", _PANES["screen"]])
        elif pid in _PANES:
            subprocess.Popen(["open", _PANES[pid]])
    except Exception:
        if pid in _PANES:
            subprocess.Popen(["open", _PANES[pid]])
    return permissions()
