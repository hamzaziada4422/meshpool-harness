"""Which browser Meshpool drives (MH_BROWSER_USE): meshpool (default), installed, attach. Detection with fake
executables for every platform, the settings, the error messages, and real runs of "installed" and "attach" with the
Chromium on this computer (skipped when there is none)."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_helpers as H  # noqa: E402
from meshpool_harness import browser_use as BU  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for k in ("MH_BROWSER_USE", "MH_BROWSER_CDP_URL", "MH_BROWSER_EXE", "MH_BROWSER_APP"):
        monkeypatch.setenv(k, "")                      # empty = not set; undone after the test
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "settings-home"))     # no settings file: the defaults


# ---------- detection (fake executables, any platform) -------------------------------------------------------------

def test_macos_paths():
    have = {"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Users/me/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"}
    got = BU.find_installed("darwin", {}, exists=have.__contains__, home="/Users/me")
    assert [g["key"] for g in got] == ["chrome", "brave"]
    assert got[0]["path"] == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert got[1]["path"].startswith("/Users/me/Applications/")


def test_windows_paths():
    env = {"ProgramFiles": r"C:\Program Files", "ProgramFiles(x86)": r"C:\Program Files (x86)",
           "LOCALAPPDATA": r"C:\Users\me\AppData\Local"}
    have = {r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Users\me\AppData\Local\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"}
    got = BU.find_installed("win32", env, exists=have.__contains__)
    assert [g["key"] for g in got] == ["chrome", "edge", "brave"]
    assert got[0]["path"] == r"C:\Users\me\AppData\Local\Google\Chrome\Application\chrome.exe"
    assert BU.find_installed("win32", {}, exists=have.__contains__) == []       # no variables, nothing guessed


def test_linux_uses_the_path():
    progs = {"google-chrome": "/usr/bin/google-chrome", "brave-browser": "/opt/brave/brave-browser"}
    got = BU.find_installed("linux", {}, exists=lambda p: True, which=progs.get)
    assert [(g["key"], g["path"]) for g in got] == [("chrome", "/usr/bin/google-chrome"),
                                                    ("brave", "/opt/brave/brave-browser")]
    assert BU.find_installed("linux", {}, exists=lambda p: True, which=lambda n: None) == []


def test_fake_executables_on_disk(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "microsoft-edge"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake.parent))
    got = BU.find_installed("linux")
    assert [g["key"] for g in got] == ["edge"] and got[0]["path"] == str(fake)


def test_pick_installed_follows_the_settings(monkeypatch, tmp_path):
    found = [{"key": "chrome", "name": "Google Chrome", "path": "/c"}, {"key": "edge", "name": "Microsoft Edge", "path": "/e"}]
    monkeypatch.setattr(BU, "find_installed", lambda **kw: found)
    assert BU.pick_installed()["key"] == "chrome"
    monkeypatch.setenv("MH_BROWSER_APP", "edge")
    assert BU.pick_installed()["key"] == "edge"
    monkeypatch.setenv("MH_BROWSER_APP", "brave")                   # asked for, not there: no silent other browser
    assert BU.pick_installed() is None and "Brave" in BU.installed_missing_message()
    exe = tmp_path / "mybrowser"
    exe.write_text("x")
    monkeypatch.setenv("MH_BROWSER_EXE", str(exe))
    assert BU.pick_installed()["path"] == str(exe)
    monkeypatch.setenv("MH_BROWSER_EXE", str(tmp_path / "gone"))
    assert BU.pick_installed() is None and str(tmp_path / "gone") in BU.installed_missing_message()


# ---------- the setting ----------------------------------------------------------------------------------------------

def test_mode_defaults_to_meshpool_and_ignores_junk(monkeypatch):
    assert BU.mode() == "meshpool" and BU.cdp_url() == "http://127.0.0.1:9222"
    monkeypatch.setenv("MH_BROWSER_USE", "Installed")
    assert BU.mode() == "installed"
    monkeypatch.setenv("MH_BROWSER_USE", "firefox")
    assert BU.mode() == "meshpool"
    monkeypatch.setenv("MH_BROWSER_CDP_URL", "http://127.0.0.1:9333/")
    assert BU.cdp_url() == "http://127.0.0.1:9333"


def test_setting_falls_back_to_the_settings_file(monkeypatch, tmp_path):
    from meshpool_harness import config as C
    (tmp_path / "settings-home").mkdir()
    C.write({"MH_BROWSER_USE": "attach"})
    assert BU.mode() == "attach"
    monkeypatch.setenv("MH_BROWSER_USE", "installed")               # the environment (a shell override) wins
    assert BU.mode() == "installed"


def test_validate():
    BU.validate({"MH_BROWSER_USE": "attach", "MH_BROWSER_CDP_URL": "http://127.0.0.1:9222", "MH_BROWSER_APP": "edge"})
    BU.validate({"MH_BROWSER_USE": "", "MH_BROWSER_CDP_URL": ""})
    for bad in ({"MH_BROWSER_USE": "firefox"}, {"MH_BROWSER_CDP_URL": "localhost:9222"}, {"MH_BROWSER_APP": "safari"}):
        with pytest.raises(ValueError):
            BU.validate(bad)


def test_settings_api_carries_the_fields(monkeypatch, tmp_path):
    from meshpool_harness import config as C
    keys = {f[0] for f in C.FIELDS}
    assert {"MH_BROWSER_USE", "MH_BROWSER_CDP_URL"} <= keys
    from meshpool_harness.ui import settings_v2 as SV
    d = SV.browser_use()
    assert d["mode"] == "meshpool" and [m["id"] for m in d["modes"]] == ["meshpool", "installed", "attach"]
    assert "everyday profile" in " ".join(m["desc"] for m in d["modes"])        # the limit is stated in the setting
    cap = next(c for c in SV.capabilities() if c["id"] == "browser")
    assert cap["browser_use"]["mode"] == "meshpool"


def test_set_browser_use_validates_and_writes(monkeypatch, tmp_path):
    (tmp_path / "settings-home").mkdir()
    from meshpool_harness import config as C
    from meshpool_harness.ui import settings_v2 as SV
    out = SV.set_browser_use("attach", "http://127.0.0.1:9444")
    assert out["mode"] == "attach" and out["cdp_url"] == "http://127.0.0.1:9444"
    assert os.environ["MH_BROWSER_USE"] == "attach"
    assert C.read()["MH_BROWSER_USE"] == "attach" and C.read()["MH_BROWSER_CDP_URL"] == "http://127.0.0.1:9444"
    with pytest.raises(ValueError):
        SV.set_browser_use("chrome-profile")
    with pytest.raises(ValueError):
        SV.set_browser_use("attach", "not a url")
    assert SV.set_browser_use("meshpool")["mode"] == "meshpool"


# ---------- attach: errors ---------------------------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_probe_says_what_to_do_when_nothing_listens():
    url = f"http://127.0.0.1:{_free_port()}"
    msg = BU.probe_cdp(url)
    assert url in msg and "--remote-debugging-port=" in msg and "--user-data-dir" in msg
    assert "Settings > Browser use" in msg and "Meshpool's browser" in msg


def test_probe_rejects_a_server_that_is_not_a_browser():
    class H_(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"hello": "world"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = HTTPServer(("127.0.0.1", 0), H_)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert "not a browser's debugging port" in BU.probe_cdp(f"http://127.0.0.1:{srv.server_address[1]}")
    finally:
        srv.shutdown()
    assert "not a debugging address" in BU.probe_cdp("localhost:9222")


# ---------- real runs ---------------------------------------------------------------------------------------------------

CHANNEL, WHY = H.find_chromium()
EXE = H.chromium_exe() if not WHY else ""
needs_browser = pytest.mark.skipif(bool(WHY or not EXE), reason=WHY or "no Chromium program found to use as the installed browser")


@pytest.fixture(scope="module")
def site():
    srv, base = H.start_site()
    yield base
    srv.shutdown()


def _fresh(tmp_path, **env):
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir(exist_ok=True)
    return H.server_for(home, project, CHANNEL, extra_env=env), home


@needs_browser
def test_installed_browser_with_a_meshpool_profile(tmp_path, site):
    s, home = _fresh(tmp_path, MH_BROWSER_USE="installed", MH_BROWSER_EXE=EXE)
    try:
        snap = s.call("browser_open", {"url": site + "/login"})[0]
        assert "Page: Log in" in snap
        snap = s.call("browser_type", {"ref": H.ref(snap, r'"User"'), "text": "bob"})[0]
        snap = s.call("browser_click", {"ref": H.ref(snap, r'button[^\n]*"Sign in"')})[0]
        assert "Signed in as bob" in snap
    finally:
        s.close()
    assert (home / "browser-profile" / "installed-custom").is_dir()      # Meshpool's folder, not the everyday profile
    s2, _ = _fresh(tmp_path, MH_BROWSER_USE="installed", MH_BROWSER_EXE=EXE)
    try:
        assert "Signed in as bob" in s2.call("browser_open", {"url": site + "/dashboard"})[0]
    finally:
        s2.close()


def test_installed_browser_missing_is_an_error_not_a_fallback(tmp_path):
    gone = str(tmp_path / "no-such-browser")
    s, home = _fresh(tmp_path, MH_BROWSER_USE="installed", MH_BROWSER_EXE=gone)
    try:
        text, failed = s.call("browser_open", {"url": "about:blank"})
        assert failed and gone in text and "Meshpool's browser" in text
        assert not (home / "browser-profile" / "chromium").exists()      # it did not quietly start Meshpool's browser
    finally:
        s.close()


@pytest.fixture
def debug_browser(tmp_path):
    """A browser started by "the user" with --remote-debugging-port, on a profile folder of its own."""
    udd = tmp_path / "user-profile"
    udd.mkdir()
    p = subprocess.Popen([EXE, "--headless=new", "--no-sandbox", "--remote-debugging-port=0", f"--user-data-dir={udd}",
                          "--no-first-run", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    f = udd / "DevToolsActivePort"
    end = time.time() + 20
    while time.time() < end and not (f.exists() and len(f.read_text().split()) >= 2):
        time.sleep(0.1)
    assert f.exists(), "the test browser did not open its debugging port"
    port = int(f.read_text().split()[0])
    yield p, f"http://127.0.0.1:{port}"
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()


@needs_browser
def test_attach_to_a_browser_the_user_started(tmp_path, site, debug_browser):
    proc, url = debug_browser
    assert BU.probe_cdp(url) == ""
    s, home = _fresh(tmp_path, MH_BROWSER_USE="attach", MH_BROWSER_CDP_URL=url)
    try:
        snap = s.call("browser_open", {"url": site + "/controls"})[0]
        snap = s.call("browser_click", {"ref": H.ref(snap, r'button "Add one"')})[0]
        assert "Count: 1" in snap
        text, failed = s.call("browser_close", {})
        assert not failed and "stays open" in text
        assert proc.poll() is None and BU.probe_cdp(url) == ""            # closing only disconnects: the browser lives
        snap = s.call("browser_open", {"url": site + "/second"})[0]       # and the tool can connect again
        assert "Page: Second" in snap
    finally:
        s.close()
    assert proc.poll() is None                                            # also after the agent closed the server
    assert not (home / "browser-profile").exists()                        # attach never creates a Meshpool profile


def test_attach_with_nothing_listening_says_so(tmp_path):
    url = f"http://127.0.0.1:{_free_port()}"
    s, _ = _fresh(tmp_path, MH_BROWSER_USE="attach", MH_BROWSER_CDP_URL=url)
    try:
        t0 = time.time()
        text, failed = s.call("browser_open", {"url": "about:blank"})
        assert failed and url in text and "--remote-debugging-port" in text and "Meshpool's browser" in text
        assert time.time() - t0 < 20                                      # a clear answer, not Playwright's 30 s wait
    finally:
        s.close()


# ---------- the packaged app's own Chromium ---------------------------------------------------------------------------

@pytest.mark.skipif(bool(WHY) or CHANNEL != "", reason=WHY or "Playwright's own Chromium is not what launches here")
def test_packaged_build_finds_its_bundled_chromium(tmp_path, monkeypatch):
    """The driver reads PLAYWRIGHT_BROWSERS_PATH once, when it starts: the packaged app must set it first."""
    from meshpool_harness import bundled
    from meshpool_harness.agent import browser_mcp_server as BS
    where = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not where:
        pytest.skip("PLAYWRIGHT_BROWSERS_PATH is not set here")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    monkeypatch.setattr(bundled, "browsers_dir", lambda: Path(where))
    monkeypatch.setattr(bundled, "browser_needs_newer_macos", lambda: "")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_BROWSER_HEADLESS", "1")
    monkeypatch.delenv("MH_BROWSER_CHANNEL", raising=False)
    b = BS.Browser()
    try:
        b.run(b.ensure, timeout=90)
        assert b.channel == "chromium" and (tmp_path / "browser-profile" / "chromium").is_dir()
    finally:
        b.run(b.shutdown, timeout=30)
