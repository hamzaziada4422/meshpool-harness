"""The app's own Chromium is found and started on every system, and no launch needs Playwright's headless shell.

v0.0.18 on macOS: the browser tool asked for chromium_headless_shell-1255 inside Meshpool.app, which ships only the
full Chromium (packaging/build_all.py installs it with --no-shell), and the lookup knew only the old Chromium.app name,
not Playwright's current "Google Chrome for Testing.app". These tests use the real folder names Playwright 1.65 uses
(driver/package: chrome-mac-arm64/Google Chrome for Testing.app, chrome-win64/chrome.exe, chrome-linux64/chrome) and
the older ones, then start a real Chromium from a folder laid out like the app's, with no headless shell in it.
"""
import http.server
import os
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from meshpool_harness import browser_use as BU

APP = Path(__file__).resolve().parents[1]
MAC_NEW = "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
MAC_X64 = "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
MAC_OLD = "chrome-mac/Chromium.app/Contents/MacOS/Chromium"


def _stub(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return p


# ---------- the lookup, with each system's real folder names ----------------------------------------------------------
# The app's window and the browser tool set PLAYWRIGHT_BROWSERS_PATH to the app's own browsers folder (ui/desktop.py,
# agent/browser_mcp_server.py), so most of these name the folder that way.

@pytest.mark.parametrize("rel", [MAC_NEW, MAC_X64, MAC_OLD])
def test_mac_app_layout_is_found_with_chrome_for_testing_and_old_names(tmp_path, rel):
    own = tmp_path / "Meshpool.app" / "Contents" / "Resources" / "app" / "vendor" / "browsers"
    exe = _stub(own, f"chromium-1255/{rel}")
    (own / "chromium_headless_shell-1255").mkdir()          # nothing in it: the app never needs it
    got = BU.find_playwright_chromium("darwin", {"PLAYWRIGHT_BROWSERS_PATH": str(own)}, str(tmp_path / "home"))
    assert got == str(exe)


def test_mac_the_apps_own_comes_before_the_users_cache_then_the_cache_is_used(tmp_path):
    home = tmp_path / "home"
    cache = home / "Library" / "Caches" / "ms-playwright"
    in_cache = _stub(cache, f"chromium-1300/{MAC_NEW}")      # newer, but not the app's
    own = tmp_path / "browsers"
    in_app = _stub(own, f"chromium-1255/{MAC_NEW}")
    env = {"PLAYWRIGHT_BROWSERS_PATH": str(own)}
    assert BU.find_playwright_chromium("darwin", env, str(home)) == str(in_app)
    # an app whose browsers folder is empty: the user's cache, with the Chrome for Testing name
    (own / "chromium-1255").rename(own / "gone")
    assert BU.find_playwright_chromium("darwin", env, str(home)) == str(in_cache)


def test_the_packaged_apps_folder_is_searched_first_even_with_no_variable_set(tmp_path, monkeypatch):
    from meshpool_harness import bundled
    home = tmp_path / "home"
    _stub(home / ".cache" / "ms-playwright", "chromium-1300/chrome-linux64/chrome")
    own = tmp_path / "app" / "vendor" / "browsers"
    in_app = _stub(own, "chromium-1255/chrome-linux64/chrome")
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    monkeypatch.setattr(bundled, "browsers_dir", lambda: own)
    assert BU.find_playwright_chromium("linux", {}, str(home)) == str(in_app)


@pytest.mark.parametrize("rel", ["chrome-win64/chrome.exe", "chrome-win/chrome.exe"])
def test_windows_layout_is_found_with_new_and_old_names(tmp_path, rel):
    own = tmp_path / "Meshpool" / "app" / "vendor" / "browsers"
    exe = _stub(own, f"chromium-1255/{rel}")
    assert BU.find_playwright_chromium("win32", {"PLAYWRIGHT_BROWSERS_PATH": str(own)}, str(tmp_path)) == str(exe)


def test_windows_the_app_comes_before_the_cache_in_localappdata():
    local = "C:\\Users\\u\\AppData\\Local"
    app = "C:\\Program Files\\Meshpool\\app\\vendor\\browsers"
    files = [app + "\\chromium-1255\\chrome-win64\\chrome.exe",
             local + "\\ms-playwright\\chromium-1300\\chrome-win64\\chrome.exe"]

    def glob(pat):
        return [f for f in files if __import__("fnmatch").fnmatch(f.replace("\\", "/"), pat.replace("\\", "/"))]
    env = {"LOCALAPPDATA": local, "PLAYWRIGHT_BROWSERS_PATH": app}
    assert BU.find_playwright_chromium("win32", env, "C:\\Users\\u", glob) == files[0]
    assert BU.find_playwright_chromium("win32", {"LOCALAPPDATA": local}, "C:\\Users\\u", glob) == files[1]


@pytest.mark.parametrize("rel", ["chrome-linux64/chrome", "chrome-linux/chrome", "chrome-linux-arm64/chrome"])
def test_linux_layout_is_found_with_new_and_old_names(tmp_path, rel):
    own = tmp_path / "Meshpool" / "app" / "vendor" / "browsers"
    exe = _stub(own, f"chromium-1255/{rel}")
    env = {"PLAYWRIGHT_BROWSERS_PATH": str(own)}
    assert BU.find_playwright_chromium("linux", env, str(tmp_path / "home")) == str(exe)


def test_the_revision_playwright_wants_wins_over_a_newer_one_in_the_same_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(BU, "_REVISION", "1255")
    want = _stub(tmp_path, "chromium-1255/chrome-linux64/chrome")
    _stub(tmp_path, "chromium-1300/chrome-linux64/chrome")
    assert BU.find_chromium_in(str(tmp_path), "linux") == str(want)
    monkeypatch.setattr(BU, "_REVISION", "1400")             # not there: the newest
    assert BU.find_chromium_in(str(tmp_path), "linux").endswith("chromium-1300/chrome-linux64/chrome")


def _playwright_chromium_paths() -> dict:
    """{"mac-arm64": "chrome-mac-arm64/Google Chrome for Testing.app/...", ...}: where the installed playwright
    package itself puts the full Chromium on each system, read from its driver (registry EXECUTABLE_PATHS)."""
    import playwright
    pkg = Path(playwright.__file__).parent / "driver" / "package" / "lib"
    for f in [pkg / "coreBundle.js", *sorted(pkg.rglob("registry/index.js"))]:
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        at = text.find("EXECUTABLE_PATHS = {")
        if at < 0:
            continue
        block = re.search(r'"chromium":\s*\{(.*?)\}', text[at:], re.S).group(1)
        out = {}
        for key, arr in re.findall(r'"([\w-]+)":\s*\[([^\]]*)\]', block):
            out[key] = "/".join(re.findall(r'"([^"]+)"', arr))
        return out
    return {}


def test_the_lookup_matches_every_path_the_installed_playwright_uses():
    """A Playwright upgrade that renames the Chromium folder or app (as Chrome for Testing did) fails here, not on a
    customer's computer."""
    pytest.importorskip("playwright")
    paths = _playwright_chromium_paths()
    assert {"mac-arm64", "mac-x64", "win-x64", "linux-x64"} <= set(paths), paths
    import fnmatch
    for key, rel in paths.items():
        system = {"mac": "darwin", "win": "win32", "linux": "linux"}[key.split("-")[0]]
        full = f"chromium-{BU.playwright_revision()}/{rel}"
        pats = BU.CHROMIUM_PROGRAMS[BU._system(system)]
        assert any(fnmatch.fnmatch(full, p) for p in pats), (key, full, pats)


def test_playwright_is_pinned_to_the_version_that_was_read_and_tested():
    """The build installs packaging/requirements-cli.txt: an unpinned playwright took whatever was newest at build
    time, and with it a Chromium revision and folder names nobody had tried (v0.0.18)."""
    root = APP.parent
    req = (root / "packaging" / "requirements-cli.txt").read_text(encoding="utf-8")
    pin = re.search(r"^playwright==([\d.]+)", req, re.M)
    assert pin, "playwright must be pinned with == in packaging/requirements-cli.txt"
    record = (root / "packaging" / "mac" / "verified_minos.json").read_text(encoding="utf-8")
    assert f'"playwright=={pin.group(1)}"' in record          # its macOS build was read (test_macos_floor.py)


def test_the_revision_is_read_from_the_installed_playwright_package():
    pytest.importorskip("playwright")
    BU._REVISION = None
    assert re.fullmatch(r"\d+", BU.playwright_revision())


# ---------- one way to launch, never the headless shell -----------------------------------------------------------------

class _PW:
    """Playwright as it behaves with the app's layout: no headless shell, so a hidden launch that names neither a
    channel nor a program fails the way v0.0.18 did."""

    def __init__(self):
        self.calls = []
        self.chromium = self

    def _go(self, kw):
        self.calls.append(kw)
        if kw.get("headless") and not kw.get("channel") and not kw.get("executable_path"):
            raise RuntimeError("BrowserType.launch: Executable doesn't exist at /app/vendor/browsers/"
                               "chromium_headless_shell-1255/chrome-headless-shell-mac-arm64/chrome-headless-shell")
        return SimpleNamespace(kw=kw, set_default_timeout=lambda *_: None, pages=[])

    def launch(self, **kw):
        return self._go(kw)

    def launch_persistent_context(self, prof, **kw):
        return self._go(dict(kw, prof=prof))


def _clean(monkeypatch, programs):
    for k in ("MH_CHROMIUM_PATH", "MH_TEST_CHROMIUM", "MH_BROWSER_CHANNEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(BU, "chromium_programs", lambda *a, **k: list(programs), raising=False)


def test_options_are_the_found_programs_then_playwrights_full_build_never_the_shell(tmp_path, monkeypatch):
    own, cache, mine, test = (_stub(tmp_path, n) for n in ("own/chrome", "cache/chrome", "mine/chrome", "test/chrome"))
    _clean(monkeypatch, [str(own), str(cache)])
    assert BU.chromium_launch_options() == [{"executable_path": str(own)}, {"executable_path": str(cache)},
                                            {"channel": "chromium"}]
    monkeypatch.setenv("MH_CHROMIUM_PATH", str(mine))
    monkeypatch.setenv("MH_TEST_CHROMIUM", str(test))
    assert [o.get("executable_path") for o in BU.chromium_launch_options()] == [str(mine), str(own), str(cache), str(test), None]
    _clean(monkeypatch, [])
    assert BU.chromium_launch_options() == [{"channel": "chromium"}]        # Playwright's own full build, headless


def test_a_program_that_will_not_start_falls_through_to_the_next_and_the_error_is_playwrights(tmp_path, monkeypatch):
    broken, good = _stub(tmp_path, "a/chrome"), _stub(tmp_path, "b/chrome")
    _clean(monkeypatch, [str(broken), str(good)])
    calls = []

    def launch(**kw):
        calls.append(kw)
        if kw.get("executable_path") == str(broken):
            raise RuntimeError("error while loading shared libraries: libnss3.so")
        if kw.get("channel") == "chromium":
            raise RuntimeError("BrowserType.launch: Executable doesn't exist at /pw/chromium-1255/chrome-linux64/chrome")
        return "browser"
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    assert BU.launch_chromium(pw, args=["--x"]) == "browser"
    assert [c.get("executable_path") for c in calls] == [str(broken), str(good)]
    assert all(c["headless"] is True and c["args"] == ["--x"] for c in calls)
    assert BU.LAST_LAUNCH == {"executable_path": str(good)}
    # none starts: the error is the one of Playwright's own build, so a source install knows to download it
    _clean(monkeypatch, [str(broken)])
    with pytest.raises(RuntimeError, match="Executable doesn't exist"):
        BU.launch_chromium(pw)


def test_a_playwright_without_the_chromium_channel_name_uses_its_default(monkeypatch):
    _clean(monkeypatch, [])
    calls = []

    def launch(**kw):
        calls.append(kw)
        if kw.get("channel"):
            raise RuntimeError('Unsupported chromium channel "chromium"')
        return "browser"
    assert BU.launch_chromium(SimpleNamespace(chromium=SimpleNamespace(launch=launch))) == "browser"
    assert calls == [{"headless": True, "channel": "chromium"}, {"headless": True}]


def test_packaged_browser_tool_starts_hidden_without_the_headless_shell(tmp_path, monkeypatch):
    from meshpool_harness.agent import browser_mcp_server as BS
    exe = _stub(tmp_path, f"browsers/chromium-1255/{MAC_NEW}")
    _clean(monkeypatch, [str(exe)])
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    b = BS.Browser()
    b.pw = _PW()
    b._launch_meshpool(True, own=True)
    assert b.pw.calls[-1]["executable_path"] == str(exe) and b.pw.calls[-1]["headless"] is True
    assert b.channel == "chromium"
    # nothing found at all: Playwright's own full Chromium, still never the shell
    _clean(monkeypatch, [])
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home2"))
    b2 = BS.Browser()
    b2.pw = _PW()
    b2._launch_meshpool(True, own=True)
    assert b2.pw.calls == [dict(b2.pw.calls[0], channel="chromium")] and "executable_path" not in b2.pw.calls[0]


def test_every_launch_in_the_app_goes_through_the_one_helper(monkeypatch, tmp_path):
    from meshpool_harness import review_interact as RI
    from meshpool_harness import ui_audit as UA
    from meshpool_harness.agent import browser_mcp_server as BS
    from meshpool_harness.agent import look as L
    seen = []
    monkeypatch.setattr(BU, "launch_chromium", lambda pw, headless=True, user_data_dir=None, **kw:
                        seen.append((headless, user_data_dir, kw)) or "browser")
    pw = _PW()
    assert L._launch(pw) == "browser" and seen[-1][2]["args"] == RI.LAUNCH_ARGS
    assert RI.launch_browser(pw) == "browser"
    assert UA.launcher(pw)() == "browser"
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    b = BS.Browser()
    b.pw = pw
    assert b._launch(tmp_path / "prof", headless=True) == "browser" and seen[-1][1] == tmp_path / "prof"
    assert len(seen) == 4 and pw.calls == []


def test_no_other_code_starts_chromium_by_itself():
    """The browser tool, browser_use, the look review, previews, ui_audit, the browser engine (engines/browser/ctl.py)
    and the app window all find and start Chromium through browser_use. Only the helper itself, and the browser tool
    when a channel or program was chosen on purpose (installed browser, MH_BROWSER_CHANNEL), call Playwright directly."""
    allowed = {"meshpool_harness/browser_use.py": 2, "meshpool_harness/agent/browser_mcp_server.py": 1}
    found = {}
    for base in ("meshpool_harness", "engines"):
        for f in (APP / base).rglob("*.py"):
            n = len(re.findall(r"chromium\.launch(?:_persistent_context)?\(", f.read_text(encoding="utf-8")))
            if n:
                found[f.relative_to(APP).as_posix()] = n
    assert {k: v for k, v in found.items() if k != "meshpool_harness/browser_use.py"} == \
        {k: v for k, v in allowed.items() if k != "meshpool_harness/browser_use.py"}
    assert "meshpool_harness/browser_use.py" in found
    src = (APP / "meshpool_harness" / "ui" / "desktop.py").read_text(encoding="utf-8")
    assert "find_chromium_in" in src and "Chromium.app\"))" not in src


def test_the_build_leaves_the_headless_shell_out_and_the_release_smoke_starts_the_browser():
    """About 280 MB the app does not need: the full Chromium runs hidden too. The release smoke must start it."""
    build = (APP.parent / "packaging" / "build_all.py").read_text(encoding="utf-8")
    assert '"install", "chromium", "--no-shell"' in build
    smoke = (APP.parent / "packaging" / "smoke.py").read_text(encoding="utf-8")
    assert "    browser(proj)\n" in smoke and "BM.browser_open(url)" in smoke and "launch_browser(pw)" in smoke


def test_app_window_on_mac_opens_the_bundled_chrome_for_testing_app(monkeypatch, tmp_path):
    from meshpool_harness import bundled
    from meshpool_harness.ui import desktop as D
    _stub(tmp_path / "browsers", f"chromium-1255/{MAC_NEW}")
    monkeypatch.setattr(bundled, "browsers_dir", lambda: tmp_path / "browsers")
    monkeypatch.setattr(bundled, "browser_needs_newer_macos", lambda: "")
    monkeypatch.setattr(sys, "platform", "darwin")
    app = tmp_path / "browsers" / "chromium-1255" / "chrome-mac-arm64" / "Google Chrome for Testing.app"
    assert D._bundled_chromium()[0][:3] == ["open", "-na", str(app)]


def test_the_settings_check_of_the_packaged_app_says_when_its_browser_is_missing(monkeypatch, tmp_path):
    pytest.importorskip("playwright")
    from meshpool_harness import bundled
    from meshpool_harness import config as C
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    monkeypatch.setattr(bundled, "browser_needs_newer_macos", lambda: "")
    monkeypatch.setattr(BU, "find_playwright_chromium", lambda *a, **k: "")
    row = next(r for r in C.system_check() if r["name"] == "Browser use")
    assert row["ok"] is False and "reinstall" in row["fix"].lower()
    monkeypatch.setattr(BU, "find_playwright_chromium", lambda *a, **k: str(tmp_path / "chrome"))
    row = next(r for r in C.system_check() if r["name"] == "Browser use")
    assert row["ok"] is True and row["detail"].startswith("ready")


# ---------- a real Chromium from a folder laid out like the app's ------------------------------------------------------

def _real_chromium() -> str:
    for p in (os.environ.get("MH_TEST_CHROMIUM") or "", BU.find_playwright_chromium()):
        if p and os.path.isfile(p):
            return p
    return ""


REAL = _real_chromium()
needs_real = pytest.mark.skipif(not REAL, reason="no Chromium on this computer to start")


def _pw_revision() -> str:
    """The Chromium revision of the installed playwright package, read here (not with the code under test)."""
    import json
    import playwright
    f = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    return next(b["revision"] for b in json.loads(f.read_text())["browsers"] if b["name"] == "chromium")


def _app_layout(tmp_path: Path) -> tuple[Path, Path]:
    """<tmp>/browsers/chromium-<rev>/<chrome-*> linked to a real Chromium's folder: the app's layout, no headless shell."""
    pytest.importorskip("playwright")
    real = Path(REAL).resolve()
    top = next(p for p in [real, *real.parents] if p.name.startswith("chrome-"))
    rev = _pw_revision()
    own = tmp_path / "browsers"
    (own / f"chromium-{rev}").mkdir(parents=True)
    try:
        (own / f"chromium-{rev}" / top.name).symlink_to(top, target_is_directory=True)
    except OSError as e:
        pytest.skip(f"cannot make a folder link here: {e}")
    exe = own / f"chromium-{rev}" / top.name / real.relative_to(top)
    assert not list(own.glob("chromium_headless_shell-*"))
    return own, exe


def _as_packaged(monkeypatch, tmp_path, own):
    from meshpool_harness import bundled
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    monkeypatch.setattr(bundled, "browsers_dir", lambda: own)
    monkeypatch.setattr(bundled, "browser_needs_newer_macos", lambda: "")
    for k in ("PLAYWRIGHT_BROWSERS_PATH", "MH_TEST_CHROMIUM", "MH_CHROMIUM_PATH", "MH_BROWSER_CHANNEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))          # no user cache: only what the app ships
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    monkeypatch.setenv("MH_BROWSER_HEADLESS", "1")
    monkeypatch.setenv("MH_BROWSER_USE", "meshpool")


PAGE = b"""<!doctype html><title>Tool page</title><h1>Browser tools</h1>
<label>Name <input aria-label=Name></label><button onclick="o.textContent='Hi '+document.querySelector('input').value">Greet</button>
<p id=o>nobody</p>"""


@pytest.fixture
def local_site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@needs_real
def test_browser_tool_runs_end_to_end_from_the_apps_layout_with_no_headless_shell(tmp_path, monkeypatch, local_site):
    from meshpool_harness.agent import browser_mcp_server as BS
    own, exe = _app_layout(tmp_path)
    _as_packaged(monkeypatch, tmp_path, own)
    b = BS.Browser()
    monkeypatch.setattr(BS, "B", b)
    try:
        snap = BS.browser_open(local_site)
        assert "Browser tools" in snap
        assert BU.LAST_LAUNCH == {"executable_path": str(exe)}        # the app's own, not a cache
        refs = {name: ref for ref, name in re.findall(r'\[(e\d+)\] \w+ "(Name|Greet)"', snap)}
        BS.browser_type(refs["Name"], "meshpool")
        BS.browser_click(refs["Greet"])
        assert "Hi meshpool" in BS.browser_snapshot()
        shot = BS.browser_screenshot()
        assert any(isinstance(p, dict) and p.get("type") == "image" and p.get("data") for p in shot)
    finally:
        b.run(b.shutdown, timeout=60)


@needs_real
def test_look_review_and_previews_render_from_the_apps_layout(tmp_path, monkeypatch, local_site):
    from playwright.sync_api import sync_playwright
    from meshpool_harness import review_interact as RI
    from meshpool_harness import ui_audit as UA
    own, exe = _app_layout(tmp_path)
    _as_packaged(monkeypatch, tmp_path, own)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(own))   # as the app's window sets it (ui/desktop.py)
    with sync_playwright() as pw:
        for launch in (lambda: RI.launch_browser(pw), UA.launcher(pw)):
            b = launch()
            try:
                assert BU.LAST_LAUNCH == {"executable_path": str(exe)}
                pg = b.new_page()
                pg.goto("data:text/html,<h1>rendered</h1>")
                assert pg.inner_text("h1") == "rendered"
            finally:
                b.close()
