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


# ---------- meshpool's own Chromium ------------------------------------------------------------------------------------
# Where the full Chromium sits inside a Playwright browsers folder. Playwright 1.49 and later download Chrome for Testing
# (chrome-mac-arm64 or chrome-mac-x64/Google Chrome for Testing.app, chrome-win64, chrome-linux64, chrome-linux-arm64),
# older ones Chromium (chrome-mac/Chromium.app, chrome-win, chrome-linux). Both are matched. Only the full browser: the
# app ships no headless shell (packaging/build_all.py installs with --no-shell), and the full one runs headless too.
CHROMIUM_PROGRAMS = {
    "mac": ("chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"),
    "win": ("chromium-*/chrome-win*/chrome.exe",),
    "linux": ("chromium-*/chrome-linux*/chrome",),
}


def _system(platform: str) -> str:
    return "mac" if platform == "darwin" else "win" if platform.startswith("win") else "linux"


def playwright_revision() -> str:
    """The Chromium revision the installed playwright package was made for ("" when unknown). Read from its
    browsers.json, without importing playwright."""
    global _REVISION
    if _REVISION is None:
        _REVISION = ""
        try:
            import importlib.util
            spec = importlib.util.find_spec("playwright")
            if spec and spec.origin:
                f = os.path.join(os.path.dirname(spec.origin), "driver", "package", "browsers.json")
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                _REVISION = next((str(b.get("revision", "")) for b in data.get("browsers", [])
                                  if b.get("name") == "chromium"), "")
        except Exception:  # noqa: BLE001
            _REVISION = ""
    return _REVISION


_REVISION: str | None = None


def _bundled_browsers() -> str:
    """The app's own browsers folder (app/vendor/browsers) in the packaged app, else ""."""
    try:
        from meshpool_harness import bundled
        return str(bundled.browsers_dir()) if bundled.packaged() else ""
    except Exception:  # noqa: BLE001
        return ""


def chromium_roots(platform: str | None = None, env: dict | None = None, home: str | None = None,
                   bundled: str | None = None) -> list[str]:
    """The folders searched for a Chromium, in order: the one inside the app, PLAYWRIGHT_BROWSERS_PATH, then the
    user's own Playwright cache (and, on Linux, the usual container folders)."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or os.path.expanduser("~")
    roots = [bundled if bundled is not None else _bundled_browsers()]
    if env.get("PLAYWRIGHT_BROWSERS_PATH") and env["PLAYWRIGHT_BROWSERS_PATH"] != "0":
        roots.append(env["PLAYWRIGHT_BROWSERS_PATH"])
    sysname = _system(platform)
    if sysname == "mac":
        roots.append(posixpath.join(home, "Library", "Caches", "ms-playwright"))
    elif sysname == "win":
        if env.get("LOCALAPPDATA"):
            roots.append(ntpath.join(env["LOCALAPPDATA"], "ms-playwright"))
    else:
        if env.get("XDG_CACHE_HOME"):
            roots.append(posixpath.join(env["XDG_CACHE_HOME"], "ms-playwright"))
        roots += [posixpath.join(home, ".cache", "ms-playwright"), "/opt/pw-browsers", "/ms-playwright"]
    return list(dict.fromkeys(r for r in roots if r))


def find_chromium_in(root: str, platform: str | None = None, glob=None) -> str:
    """The full Chromium inside one Playwright browsers folder ("" when none): the revision the playwright package
    wants when it is there, else the newest."""
    import glob as _glob
    glob = glob or _glob.glob
    want = playwright_revision()
    hits = []
    for pat in CHROMIUM_PROGRAMS[_system(platform or sys.platform)]:
        for path in glob(os.path.join(root, pat)):
            revs = re.findall(r"chromium-(\d+)", path.replace("\\", "/"))
            rev = revs[-1] if revs else "0"
            hits.append((rev == want, int(rev), path))
    return max(hits)[2] if hits else ""


def chromium_programs(platform: str | None = None, env: dict | None = None, home: str | None = None, glob=None,
                      bundled: str | None = None) -> list[str]:
    """Every full Chromium found, best first: one per folder of chromium_roots, in that order."""
    out = []
    for r in chromium_roots(platform, env, home, bundled):
        hit = find_chromium_in(r, platform, glob)
        if hit and hit not in out:
            out.append(hit)
    return out


def find_playwright_chromium(platform: str | None = None, env: dict | None = None, home: str | None = None,
                             glob=None, bundled: str | None = None) -> str:
    """The full Chromium to use ("" when none): the app's own first, then one a Playwright install left on this
    computer (any revision: the Python package wants one exact revision, so a computer with another one would
    otherwise download a second copy). Arguments exist for tests with fake folders."""
    hits = chromium_programs(platform, env, home, glob, bundled)
    return hits[0] if hits else ""


def chromium_launch_options(env: dict | None = None) -> list[dict]:
    """How to start Chromium, best first, as keyword arguments for Playwright's launch or launch_persistent_context.
    Every one is the full browser, never Playwright's separate headless shell, which the app does not ship: a program
    that was found is started by its path (with headless=True Playwright runs it in Chromium's own headless mode), and
    last comes channel "chromium", Playwright's own full build of its revision (when that is missing too, the error
    says "Executable doesn't exist", which is what starts a download in a source install). MH_CHROMIUM_PATH names a
    program to use first; MH_TEST_CHROMIUM one to try after those found."""
    env = os.environ if env is None else env
    paths = [env.get("MH_CHROMIUM_PATH") or "", *chromium_programs(env=env), env.get("MH_TEST_CHROMIUM") or ""]
    out = [{"executable_path": p} for p in dict.fromkeys(p for p in paths if p and os.path.isfile(p))]
    return out + [{"channel": "chromium"}]


LAST_LAUNCH: dict = {}


def launch_chromium(pw, headless: bool = True, user_data_dir=None, **kw):
    """Start meshpool's Chromium with a Playwright instance (pw): a Browser, or a persistent context when user_data_dir
    is given. Every launch in the app goes through here, so all of them find the same Chromium the same way and none
    needs the headless shell. Each option of chromium_launch_options is tried in turn; the error raised is the one of
    Playwright's own build (or the last one), so "Executable doesn't exist" still reaches the caller."""
    last, tried = None, []
    for opts in chromium_launch_options():
        try:
            if user_data_dir is not None:
                got = pw.chromium.launch_persistent_context(str(user_data_dir), headless=headless, **opts, **kw)
            else:
                got = pw.chromium.launch(headless=headless, **opts, **kw)
            LAST_LAUNCH.clear()
            LAST_LAUNCH.update(opts)                 # which one started (packaging/smoke.py checks it is the app's own)
            return got
        except Exception as e:  # noqa: BLE001 - the next one may work (missing libraries, another revision)
            last = e
            tried.append((opts.get("executable_path") or "Playwright's own Chromium (channel chromium)", e))
    if last is not None and "xecutable doesn't exist" not in str(last) and "channel" in str(last).lower():
        # a Playwright older than the "chromium" channel name: its default is the full browser
        LAST_LAUNCH.clear()
        if user_data_dir is not None:
            return pw.chromium.launch_persistent_context(str(user_data_dir), headless=headless, **kw)
        return pw.chromium.launch(headless=headless, **kw)
    raise ChromiumNotStarted(not_started_message(tried))


class ChromiumNotStarted(RuntimeError):
    """No Chromium could be started. The message names every program tried, why each failed, and what to do."""


def _why(e) -> str:
    """The first plain line of a Playwright error (its stack and its boxed install advice left out)."""
    for line in str(e).splitlines():
        line = line.strip()
        if line and not line.startswith(("╔", "║", "╚", "Call log", "=")):
            return re.sub(r"^BrowserType\.\w+:\s*", "", line)[:300]
    return type(e).__name__


def not_started_message(tried: list) -> str:
    """Says which programs were tried, why each did not start, and the one thing to do about it."""
    whys = [_why(e) for _, e in tried]
    lines = ["meshpool's browser could not start. Tried:"] + [f"- {what}: {why}" for (what, _), why in zip(tried, whys)]
    own = _bundled_browsers()
    found = [w for w, _ in tried if not w.startswith("Playwright's own")]
    every = " ".join(whys).lower()
    if "already in use" in every or "processsingleton" in every or ("profile" in every and "in use" in every):
        todo = "another window of meshpool's browser is using the same profile folder. Close it, then try again."
    elif "shared librar" in every or "cannot open shared object" in every:
        todo = ("this computer lacks a system library Chromium needs (named above). Install it with the system's "
                "package manager, then try again.")
    elif own and not any(w.startswith(own) for w in found):
        todo = f"the browser that ships with meshpool is missing from {own}. Reinstall meshpool."
    elif own:
        todo = "reinstall meshpool; if it still fails, report the lines above."
    else:
        todo = "run `meshpool install-browser` to download Chromium, then try again."
    return "\n".join(lines + [f"What to do: {todo}"])


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
