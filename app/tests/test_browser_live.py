"""The browser stays out of the way: headless by default (no system Chrome or Edge first), shown inside the app by the
live view (agent/browser_live.py, ui/browser_api.py), sign-in pages open in the app, and the app window prefers the
bundled Chromium. Mocked where it can be; a real headless Chromium where there is one (skipped when there is none)."""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_helpers as H  # noqa: E402
from meshpool_harness import browser_use as BU  # noqa: E402
from meshpool_harness.agent import browser_live as BL  # noqa: E402
from meshpool_harness.agent import browser_mcp_server as BS  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for k in ("MH_BROWSER_USE", "MH_BROWSER_CDP_URL", "MH_BROWSER_EXE", "MH_BROWSER_APP", "MH_BROWSER_HEADLESS",
              "MH_BROWSER_CHANNEL", "MH_RUNS_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))


# ---------- the setting ------------------------------------------------------------------------------------------------

def test_headless_is_the_default_and_the_setting_turns_it_off(monkeypatch):
    assert BU.headless() is True
    assert BU.describe()["show_window"] is False
    monkeypatch.setenv("MH_BROWSER_HEADLESS", "0")
    assert BU.headless() is False and BU.describe()["show_window"] is True
    monkeypatch.setenv("MH_BROWSER_HEADLESS", "1")
    assert BU.headless() is True


def test_settings_page_saves_show_window_across_a_restart():
    from meshpool_harness import config as C
    from meshpool_harness.ui import settings_v2 as SV
    out = SV.set_browser_use("meshpool", show_window=True)
    assert out["show_window"] is True and C.read().get("MH_BROWSER_HEADLESS") == "0"
    os.environ.pop("MH_BROWSER_HEADLESS", None)                      # a new launch: only the settings file is left
    C.load_into_env(override=True)
    assert BU.headless() is False
    out = SV.set_browser_use("meshpool", show_window=False)          # off: saved as empty, so the default applies
    assert out["show_window"] is False and BU.headless() is True
    os.environ.pop("MH_BROWSER_HEADLESS", None)
    C.load_into_env(override=True)
    assert BU.headless() is True
    with pytest.raises(ValueError):
        BU.validate({"MH_BROWSER_HEADLESS": "maybe"})


# ---------- which browser is launched --------------------------------------------------------------------------------

class _FakeChromium:
    """fail: the channels that are missing here ("chromium" is meshpool's own Chromium, started by Playwright)."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def launch_persistent_context(self, prof, **kw):
        self.calls.append(kw)
        if kw.get("channel") in self.fail:
            raise RuntimeError("Executable doesn't exist at /x" if kw.get("channel") == "chromium" else "no such channel")
        return SimpleNamespace(set_default_timeout=lambda *_: None)


def _browser(chromium, monkeypatch):
    monkeypatch.setattr(BU, "chromium_programs", lambda *a, **k: [])     # no Chromium found on disk: Playwright's own
    monkeypatch.delenv("MH_TEST_CHROMIUM", raising=False)
    monkeypatch.delenv("MH_CHROMIUM_PATH", raising=False)
    b = BS.Browser()
    b.pw = SimpleNamespace(chromium=chromium)
    return b


def test_default_launch_is_bundled_chromium_headless_never_a_system_channel(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")                 # where Chrome and Edge used to come first
    ch = _FakeChromium()
    b = _browser(ch, monkeypatch)
    b._launch_meshpool(BU.headless(), own=False)
    # the full Chromium (channel "chromium"), hidden: never the headless shell, never a system browser
    assert len(ch.calls) == 1 and ch.calls[0]["headless"] is True and ch.calls[0]["channel"] == "chromium"
    assert b.channel == "chromium" and not b.notice


def test_system_chrome_is_only_a_last_resort_and_is_said(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    ch = _FakeChromium(fail={"chromium"})
    b = _browser(ch, monkeypatch)
    monkeypatch.setattr(b, "_install_chromium", lambda: (_ for _ in ()).throw(BS.ToolFailed("offline")))
    b._launch_meshpool(True, own=False)
    assert [c.get("channel") for c in ch.calls] == ["chromium", "chrome"] and all(c["headless"] for c in ch.calls)
    assert "Google Chrome" in b.notice and b.channel == "chrome"


def test_packaged_app_never_falls_back_to_a_system_browser(monkeypatch):
    ch = _FakeChromium(fail={"chromium"})
    b = _browser(ch, monkeypatch)
    with pytest.raises(BS.ToolFailed):
        b._launch_meshpool(True, own=True)
    assert ch.calls and all(c.get("channel") == "chromium" for c in ch.calls)


# ---------- the app window ---------------------------------------------------------------------------------------------

def test_app_window_tries_the_bundled_chromium_before_the_users_browser(monkeypatch, tmp_path):
    from meshpool_harness import bundled
    from meshpool_harness.ui import desktop as D
    own = tmp_path / "browsers" / "chromium-1200" / "chrome-linux" / "chrome"
    own.parent.mkdir(parents=True)
    own.write_text("x")
    monkeypatch.setattr(bundled, "browsers_dir", lambda: tmp_path / "browsers")
    monkeypatch.setattr(bundled, "browser_needs_newer_macos", lambda: "")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(D.shutil, "which", lambda n: "/usr/bin/" + n if n == "google-chrome" else None)
    c = D._app_candidates()
    assert c[0][0] == str(own) and c[1][0] == "/usr/bin/google-chrome"


def test_app_window_macos_uses_the_bundled_app_and_skips_it_on_macos_11(monkeypatch, tmp_path):
    from meshpool_harness import bundled
    from meshpool_harness.ui import desktop as D
    app = tmp_path / "browsers" / "chromium-1200" / "chrome-mac" / "Chromium.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Chromium").write_text("x")     # the program inside, as Playwright installs it
    monkeypatch.setattr(bundled, "browsers_dir", lambda: tmp_path / "browsers")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(bundled, "browser_needs_newer_macos", lambda: "")
    assert D._app_candidates()[0][:3] == ["open", "-na", str(app)]
    monkeypatch.setattr(bundled, "browser_needs_newer_macos", lambda: "Browser use needs macOS 13 or later")
    assert all(str(app) not in c for c in D._app_candidates())


# ---------- sign-in pages open in the app ------------------------------------------------------------------------------

def test_sign_in_page_opens_in_the_app_not_the_system_browser(monkeypatch):
    from meshpool_harness.ui import browser_api as BA
    from meshpool_harness.ui import server as SRV
    opened, system = [], []
    monkeypatch.setattr(BA, "open_signin", lambda u: opened.append(u))
    monkeypatch.setattr(SRV.webbrowser, "open", lambda u: system.append(u))
    assert SRV._open_web("https://example.com/auth") is True
    assert opened == ["https://example.com/auth"] and not system


def test_sign_in_falls_back_to_the_system_browser_only_without_an_engine(monkeypatch):
    from meshpool_harness.ui import browser_api as BA
    from meshpool_harness.ui import server as SRV
    system = []
    monkeypatch.setattr(SRV.webbrowser, "open", lambda u: system.append(u))
    monkeypatch.setattr(BA, "open_signin", lambda u: (_ for _ in ()).throw(RuntimeError("no engine")))
    assert SRV._open_web("https://example.com/b") is False and system == ["https://example.com/b"]
    with pytest.raises(ValueError):
        SRV._open_web("file:///etc/passwd")


def test_address_checks():
    assert BL.web_address("example.com") == "https://example.com"
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", ""):
        with pytest.raises(ValueError):
            BL.web_address(bad)


def test_manager_tells_the_browser_server_its_chat():
    from meshpool_harness.agent import mcp as M
    m = M.Manager(roots=["."], chat="abc-1")
    spec = {"command": "python", "args": ["-m", "meshpool_harness.agent.browser_mcp_server"], "env": {"A": "1"}}
    assert m._with_chat(spec)["env"] == {"A": "1", "MH_CHAT_ID": "abc-1"}
    other = {"command": "python", "args": ["-m", "something_else"]}
    assert m._with_chat(other) is other


# ---------- the live view against a real headless Chromium -------------------------------------------------------------

CHANNEL, WHY = H.find_chromium()
real = pytest.mark.skipif(bool(WHY), reason=WHY or "")

PAGE = """<title>Live test</title><h1>Live test</h1>
<button id=b onclick="document.title='clicked '+(++n)" style="position:absolute;left:100px;top:100px;width:120px;height:40px">Click me</button>
<input id=t style="position:absolute;left:100px;top:200px;width:200px;height:30px" oninput="document.getElementById('o').textContent=this.value">
<div id=o style="position:absolute;top:260px"></div><div style="height:4000px"></div>
<script>var n=0;document.getElementById('t').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('o').textContent+='|enter'})</script>"""


@pytest.fixture
def live(monkeypatch, tmp_path):
    if CHANNEL:
        monkeypatch.setenv("MH_BROWSER_CHANNEL", CHANNEL)
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    srv, base = H.start_site()
    H.PAGES["/live"] = PAGE
    b = BS.Browser()
    ls = BL.LiveServer(b, "chat-42").start()
    try:
        b.run(lambda: (b.ensure(), b.page.goto(base + "/live")), timeout=90)
        yield b, ls, base
    finally:
        ls.stop()
        b.run(b.shutdown, timeout=30)
        srv.shutdown()
        H.PAGES.pop("/live", None)


def _get(ls, path, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{ls.httpd.server_address[1]}{path}",
                                 headers={"X-Live-Token": ls.token if token is None else token})
    return urllib.request.urlopen(req, timeout=20)


@real
def test_default_browser_is_headless(live):
    b, ls, base = live
    assert BU.headless() and b.channel in ("chromium", "chromium:cached", CHANNEL or "chromium")
    ua = b.run(lambda: b.page.evaluate("navigator.userAgent"))
    assert "Headless" in ua


@real
def test_channel_needs_the_token_and_is_listed_for_the_app(live):
    b, ls, base = live
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(ls, "/state", token="wrong")
    assert e.value.code == 403
    e = BL.find("chat-42")
    assert e and e["port"] == ls.httpd.server_address[1] and e["token"] == ls.token
    assert BL.find("other-chat") is None
    st, data, _ = BL.remote("chat-42", "GET", "/state")
    assert st == 200 and json.loads(data)["open"] is True


@real
def test_frame_state_click_type_scroll(live):
    b, ls, base = live
    st = json.loads(_get(ls, "/state").read())
    assert st["open"] and st["title"] == "Live test" and st["url"].endswith("/live") and st["width"] > 300
    r = _get(ls, "/frame")
    data = r.read()
    assert r.status == 200 and data[:2] == b"\xff\xd8"                     # a JPEG
    info = json.loads(r.headers["X-Frame-Info"])
    assert _get(ls, "/frame?known=" + info["id"]).status == 204             # unchanged: nothing sent again
    live_page = ls.live
    live_page.act({"type": "click", "x": 150, "y": 120})
    assert json.loads(_get(ls, "/state").read())["title"] == "clicked 1"
    live_page.act({"type": "click", "x": 150, "y": 215})
    live_page.act({"type": "text", "text": "hello"})
    live_page.act({"type": "key", "key": "Enter"})
    assert b.run(lambda: b.page.inner_text("#o")) == "hello|enter"
    live_page.act({"type": "scroll", "x": 400, "y": 400, "dx": 0, "dy": 600})
    assert b.run(lambda: b.page.wait_for_function("scrollY > 100", timeout=5000) and True)
    with pytest.raises(ValueError):
        live_page.act({"type": "goto", "url": "file:///etc/passwd"})
    with pytest.raises(ValueError):
        live_page.act({"type": "key", "key": "rm -rf"})
    live_page.act({"type": "goto", "url": base + "/second"})
    assert json.loads(_get(ls, "/state").read())["title"] == "Second"


@real
def test_stopping_removes_the_registry_file(live, tmp_path):
    b, ls, base = live
    f = Path(ls.file)
    assert f.is_file()
    ls.stop()
    assert not f.exists() and BL.find("chat-42") is None


@real
def test_sign_in_browser_runs_in_the_app_process(monkeypatch, tmp_path):
    from meshpool_harness.ui import browser_api as BA
    if CHANNEL:
        monkeypatch.setenv("MH_BROWSER_CHANNEL", CHANNEL)
    srv, base = H.start_site()
    try:
        BA.open_signin(base + "/login")
        import time
        for _ in range(60):
            code, data, *_ = BA.handle("GET", SimpleNamespace(path="/api/browser/frame", query="src=signin"))
            if code == 200:
                break
            time.sleep(0.5)
        assert code == 200 and data[:2] == b"\xff\xd8"
        st = BA.handle("GET", SimpleNamespace(path="/api/browser/state", query="src=signin"))[1]
        assert st["title"] == "Log in"
        assert (tmp_path / "home" / "browser-profile" / "signin").is_dir()     # its own profile, not the agent's
        BA.handle("POST", SimpleNamespace(path="/api/browser/close"), b'{"src": "signin"}')
    finally:
        BA.close_signin()
        srv.shutdown()


def test_the_sign_in_panel_has_an_explicit_way_to_use_the_system_browser():
    from pathlib import Path
    ui = Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui"
    assert "/api/browser/system-open" in (ui / "browser_api.py").read_text(encoding="utf-8")
    js = (ui / "preview.js").read_text(encoding="utf-8")
    assert 'id="bSystem"' in js and "Open it in my browser" in js
