"""Which browser Meshpool's browser use drives. Pure helpers (no Playwright import), used by the browser MCP server and
by the Settings API.

MH_BROWSER_USE:
  meshpool   (default) Meshpool's own browser: Chromium bundled in the app, hidden, shown inside the app's Browser
             panel (MH_BROWSER_HEADLESS=0 gives it a window of its own). A system Chrome or Edge is used only as a last
             resort, when no Chromium can be found or installed.
  installed  the engine of the Chrome, Edge or Brave on this computer, still with a Meshpool-owned profile folder.
             Chrome does not allow automation of the everyday profile, so sign-ins of your own browser are not shared.
  attach     connect over CDP to a browser you started with --remote-debugging-port=<port> (MH_BROWSER_CDP_URL).

installed and attach are opt-in and never fall back to another browser, in any build: when the chosen browser is not
there, the error says so and offers Meshpool's browser.
"""
from __future__ import annotations

import json
import ntpath
import os
import re
import posixpath
import shutil
import sys
import urllib.error
import urllib.request

MODES = ("meshpool", "installed", "attach")
DEFAULT_CDP_URL = "http://127.0.0.1:9222"

MODE_INFO = [
    {"id": "meshpool", "name": "Meshpool's browser",
     "desc": "A browser that Meshpool owns. You see it in the Browser panel inside Meshpool. Sign-ins you make in it "
             "are kept for next time. Your own browser and its sign-ins are never touched."},
    {"id": "installed", "name": "My installed Chrome, Edge or Brave",
     "desc": "Uses the browser program installed on this computer, but with a profile folder that belongs to Meshpool. "
             "It is your browser engine, not your everyday profile: Chrome does not allow automation of the everyday "
             "profile, so you sign in once in the Meshpool window and it is kept."},
    {"id": "attach", "name": "A browser I started myself",
     "desc": "Connects to a Chrome, Edge or Brave you started with --remote-debugging-port (and its own "
             "--user-data-dir). Meshpool then works in that window and leaves it open."},
]

# (key, label, mac app folder + program, windows relative path, linux program names)
_BROWSERS = [
    ("chrome", "Google Chrome", "Google Chrome.app/Contents/MacOS/Google Chrome",
     ("Google", "Chrome", "Application", "chrome.exe"), ("google-chrome-stable", "google-chrome")),
    ("edge", "Microsoft Edge", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
     ("Microsoft", "Edge", "Application", "msedge.exe"), ("microsoft-edge-stable", "microsoft-edge")),
    ("brave", "Brave", "Brave Browser.app/Contents/MacOS/Brave Browser",
     ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"), ("brave-browser-stable", "brave-browser", "brave")),
]
KEYS = tuple(b[0] for b in _BROWSERS)


def setting(key: str, default: str = "") -> str:
    """A browser setting: the environment first (a shell override, or what the app loaded), else the settings file,
    which the Settings page writes. Read at the moment the browser starts, so a change applies to the next start."""
    v = os.environ.get(key)
    if v is None or v == "":
        try:
            from meshpool_harness import config as C
            v = C.read().get(key, "")
        except Exception:  # noqa: BLE001
            v = ""
    return (v or default).strip()


def mode() -> str:
    m = setting("MH_BROWSER_USE", "meshpool").lower()
    return m if m in MODES else "meshpool"


def headless() -> bool:
    """Meshpool's own browser runs hidden by default: the page is shown inside the app (the Browser panel). It gets
    its own window only when the user turns on "Show the browser in its own window" (MH_BROWSER_HEADLESS=0)."""
    return setting("MH_BROWSER_HEADLESS", "1") != "0"


def cdp_url() -> str:
    return (setting("MH_BROWSER_CDP_URL", DEFAULT_CDP_URL) or DEFAULT_CDP_URL).rstrip("/")


# ---------- installed browsers ---------------------------------------------------------------------------------------

def find_installed(platform: str | None = None, env: dict | None = None, exists=None, which=None,
                   home: str | None = None) -> list[dict]:
    """Chrome, Edge and Brave found on this computer, in that order: [{key, name, path}]. The arguments exist so the
    search can be tested for another operating system with fake files."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    exists = exists or os.path.isfile
    which = which or shutil.which
    home = home or os.path.expanduser("~")
    out = []
    for key, label, mac, win, linux in _BROWSERS:
        cands: list[str] = []
        if platform == "darwin":
            cands = [posixpath.join(root, mac) for root in ("/Applications", posixpath.join(home, "Applications"))]
        elif platform.startswith("win"):
            for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
                if env.get(var):
                    cands.append(ntpath.join(env[var], *win))
        else:
            cands = [p for p in (which(n) for n in linux) if p]
        path = next((c for c in cands if exists(c)), "")
        if path:
            out.append({"key": key, "name": label, "path": path})
    return out


def find_playwright_chromium(platform: str | None = None, env: dict | None = None, home: str | None = None,
                             glob=None) -> str:
    """The newest Chromium that a Playwright install already left on this computer ("" when none). The Python package
    wants one exact revision, so a computer that has another revision (a container image, another project's cache)
    would download a second copy; this finds the one that is there. Arguments exist for tests with fake folders."""
    import glob as _glob
    glob = glob or _glob.glob
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or os.path.expanduser("~")
    roots = []
    if env.get("PLAYWRIGHT_BROWSERS_PATH") and env["PLAYWRIGHT_BROWSERS_PATH"] != "0":
        roots.append(env["PLAYWRIGHT_BROWSERS_PATH"])
    if platform == "darwin":
        roots.append(posixpath.join(home, "Library", "Caches", "ms-playwright"))
        pats = ["chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"]
    elif platform.startswith("win"):
        if env.get("LOCALAPPDATA"):
            roots.append(ntpath.join(env["LOCALAPPDATA"], "ms-playwright"))
        pats = ["chromium-*/chrome-win*/chrome.exe"]
    else:
        roots += [posixpath.join(home, ".cache", "ms-playwright"), "/opt/pw-browsers", "/ms-playwright"]
        pats = ["chromium-*/chrome-linux*/chrome"]
    hits = []
    for r in roots:
        for pat in pats:
            for path in glob(os.path.join(r, pat)):
                m = re.search(r"chromium-(\d+)", path)
                hits.append((int(m.group(1)) if m else 0, path))
    return max(hits)[1] if hits else ""


def pick_installed(**kw) -> dict | None:
    """The browser to launch: MH_BROWSER_EXE (a program path), else MH_BROWSER_APP (chrome, edge or brave), else the
    first one found."""
    exe = setting("MH_BROWSER_EXE")
    if exe:
        return {"key": "custom", "name": os.path.basename(exe), "path": exe} if os.path.isfile(exe) else None
    found = find_installed(**kw)
    want = setting("MH_BROWSER_APP").lower()
    if want in KEYS:
        return next((f for f in found if f["key"] == want), None)
    return found[0] if found else None


def installed_missing_message() -> str:
    exe, want = setting("MH_BROWSER_EXE"), setting("MH_BROWSER_APP").lower()
    if exe:
        return (f"Browser use is set to your installed browser, but {exe} does not exist. Fix the path, or switch "
                "Settings > Capabilities > Browser for browser use back to Meshpool's browser.")
    what = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "brave": "Brave"}.get(want, "Google Chrome, Microsoft Edge or Brave")
    return (f"Browser use is set to your installed browser, but {what} was not found on this computer. Install it, or "
            "choose Meshpool's browser under Settings > Capabilities > Browser for browser use (nothing is opened in the meantime).")


# ---------- attach ----------------------------------------------------------------------------------------------------

def attach_hint(url: str) -> str:
    port = url.rsplit(":", 1)[-1].split("/")[0] if url.count(":") >= 2 else "9222"
    return (f"Start your browser with --remote-debugging-port={port} and a profile folder of its own, for example "
            f"chrome --remote-debugging-port={port} --user-data-dir=<a folder for Meshpool>. Chrome 136 and later "
            "ignore the debugging port on the everyday profile, so --user-data-dir is required. Or choose Meshpool's "
            "browser under Settings > Capabilities > Browser for browser use.")


def probe_cdp(url: str, timeout: float = 2.0) -> str:
    """"" when a browser answers at url (its /json/version), else a message that says what to do."""
    if not url.startswith(("http://", "https://", "ws://", "wss://")):
        return f"{url!r} is not a debugging address. Use something like {DEFAULT_CDP_URL}."
    if url.startswith(("ws://", "wss://")):
        return ""                                   # a direct websocket address: Playwright reports a failure itself
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version", timeout=timeout) as r:
            info = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        why = getattr(e, "reason", e)
        return f"No browser is listening for remote debugging at {url} ({why}). " + attach_hint(url)
    if not isinstance(info, dict) or not (info.get("webSocketDebuggerUrl") or info.get("Browser")):
        return f"Something answers at {url}, but it is not a browser's debugging port. " + attach_hint(url)
    return ""


# ---------- what Settings shows ---------------------------------------------------------------------------------------

def describe() -> dict:
    """The setting, for the Settings page: the chosen mode, the modes with a note on each, the browsers found."""
    inst = find_installed()
    cur = mode()
    modes = []
    for m in MODE_INFO:
        row = dict(m)
        if m["id"] == "installed":
            row["available"] = bool(inst) or bool(setting("MH_BROWSER_EXE"))
            row["detail"] = ", ".join(i["name"] for i in inst) or "none found on this computer"
        elif m["id"] == "attach":
            row["available"] = True
            row["detail"] = cdp_url()
        else:
            row["available"] = True
            row["detail"] = "built in"
        modes.append(row)
    return {"mode": cur, "modes": modes, "cdp_url": cdp_url(), "installed": inst,
            "default_cdp_url": DEFAULT_CDP_URL, "show_window": not headless()}


def validate(values: dict) -> None:
    """Raises ValueError (shown in Settings) for a value that cannot work."""
    m = values.get("MH_BROWSER_USE")
    if m not in (None, "") and str(m).lower() not in MODES:
        raise ValueError("Browser use must be meshpool, installed or attach.")
    u = values.get("MH_BROWSER_CDP_URL")
    if u not in (None, "") and not str(u).strip().startswith(("http://", "https://", "ws://", "wss://")):
        raise ValueError(f"The debugging address must start with http:// (for example {DEFAULT_CDP_URL}).")
    h = values.get("MH_BROWSER_HEADLESS")
    if h not in (None, "") and str(h) not in ("0", "1"):
        raise ValueError("Show the browser in its own window must be on or off.")
    a = values.get("MH_BROWSER_APP")
    if a not in (None, "") and str(a).lower() not in KEYS:
        raise ValueError("Installed browser must be chrome, edge or brave (or empty for the first one found).")
