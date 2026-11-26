"""Browser use, end to end: Meshpool's browser MCP server is started the way the agent starts it (agent/mcp.py), and
every browser tool is exercised against a small multi-page site on localhost, in a real (headless) Chromium.
Skipped when this computer has no Chromium that launches."""
import base64
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_helpers as H  # noqa: E402

CHANNEL, WHY = H.find_chromium()
pytestmark = pytest.mark.skipif(bool(WHY), reason=WHY or "browser available")


@pytest.fixture(scope="module")
def site():
    srv, base = H.start_site()
    yield base
    srv.shutdown()


@pytest.fixture
def dirs(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    return home, project


@pytest.fixture
def srv(dirs):
    s = H.server_for(*dirs, CHANNEL)
    yield s
    s.close()


def call(s, tool, **args):
    text, failed = s.call(tool, args)
    assert not failed, f"{tool} failed: {text}"
    return text


def test_tools_are_listed(srv):
    names = {t["name"] for t in srv.tools}
    assert {"browser_open", "browser_snapshot", "browser_click", "browser_type", "browser_select", "browser_press",
            "browser_scroll", "browser_back", "browser_tabs", "browser_screenshot", "browser_wait",
            "browser_close"} <= names


def test_open_and_read_the_page(srv, site):
    snap = call(srv, "browser_open", url=site + "/")
    assert "Page: Test site" in snap and f"Address: {site}/" in snap
    assert 'a "Log in" -> /login' in snap and 'a "Controls" -> /controls' in snap
    assert "Visible text:" in snap and "Test site" in snap.split("Visible text:")[1]
    assert "Interactive elements" in call(srv, "browser_snapshot")
    # a bare host gets https:// (nothing listens, so it must fail with a readable error, not hang)
    text, failed = srv.call("browser_open", {"url": "127.0.0.1:9/nothing"})
    assert failed and text


def test_click_type_select_check_press_scroll_back(srv, site):
    snap = call(srv, "browser_open", url=site + "/controls")
    # click by number: the counter button
    snap = call(srv, "browser_click", ref=H.ref(snap, r'button "Add one"'))
    assert "Count: 1" in snap
    snap = call(srv, "browser_click", ref=H.ref(snap, r'button "Add one"'), double=True)
    assert "Count: 3" in snap
    # the number may be given as [e5] too
    snap = call(srv, "browser_click", ref="[" + H.ref(snap, r'button "Add one"') + "]")
    assert "Count: 4" in snap
    # type
    snap = call(srv, "browser_type", ref=H.ref(snap, r'"Name"'), text="Ada Lovelace")
    assert 'value="Ada Lovelace"' in snap
    snap = call(srv, "browser_type", ref=H.ref(snap, r'"Name"'), text="Grace Hopper")      # replaces, not appends
    assert 'value="Grace Hopper"' in snap and "Ada" not in snap.split("Visible text:")[0]
    # select by the option's text
    snap = call(srv, "browser_select", ref=H.ref(snap, r"select"), option="Green")
    assert 'selected="Green"' in snap and "Red | Green | Blue" in snap
    # check a box by clicking it
    assert "(unchecked)" in snap
    snap = call(srv, "browser_click", ref=H.ref(snap, r"checkbox"))
    assert "(checked)" in snap and "(unchecked)" not in snap
    # the page really received all of it
    snap = call(srv, "browser_click", ref=H.ref(snap, r'button "Show state"'))
    assert "State: name=Grace Hopper;color=Green;agree=true" in snap
    # keys: focus the field, press Enter and ArrowDown, and a shortcut
    snap = call(srv, "browser_click", ref=H.ref(snap, r'"Press keys here"'))
    assert "Key: Enter" in call(srv, "browser_press", key="Enter")
    assert "Key: ArrowDown" in call(srv, "browser_press", key="ArrowDown")
    assert "Key: A" in call(srv, "browser_press", key="Control+A")                # a shortcut reaches the page
    # submit=true presses Enter in the field: the search form loads the echo page
    snap = call(srv, "browser_snapshot")
    snap = call(srv, "browser_type", ref=H.ref(snap, r'"Search"'), text="meshpool", submit=True)
    assert "You searched: meshpool" in snap and "/echo?q=meshpool" in snap
    snap = call(srv, "browser_back")
    assert "Address: " + site + "/controls" in snap
    # scroll down and up
    assert "Scroll: 0 of" in snap
    down = call(srv, "browser_scroll", direction="down", pages=1)
    assert "Scroll: 0 of" not in down
    scrolled = int(down.split("Scroll: ")[1].split(" of")[0])
    assert scrolled > 300
    up = call(srv, "browser_scroll", direction="up", pages=5)
    assert "Scroll: 0 of" in up
    # follow a link, then back
    snap = call(srv, "browser_click", ref=H.ref(snap, r"Second page"))
    assert "Page: Second" in snap
    assert "Page: Controls" in call(srv, "browser_back")


def test_late_content_needs_a_wait(srv, site):
    snap = call(srv, "browser_open", url=site + "/late")
    assert "Loading..." in snap and "Late content arrived" not in snap
    t0 = time.time()
    snap = call(srv, "browser_wait", seconds=3)
    assert "Late content arrived" in snap and time.time() - t0 >= 2.5


def test_screenshot_is_a_png(srv, site, dirs):
    call(srv, "browser_open", url=site + "/controls")
    text, failed = srv.call("browser_screenshot", {})
    assert not failed and "Screenshot of" in text
    png = base64.b64decode(srv.call_with_images("browser_screenshot", {"full_page": True})[1][0]["data"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000
    shots = list((dirs[1] / "Meshpool outputs" / "screenshots").glob("browser-*.png"))
    assert shots and shots[0].stat().st_size > 2000          # saved in the project, where the user finds it


def test_new_tab_and_tabs(srv, site):
    snap = call(srv, "browser_open", url=site + "/controls")
    snap = call(srv, "browser_click", ref=H.ref(snap, r"Open in new tab"))
    assert "opened a new tab" in snap and "I am the new tab" in snap and "Tabs open: 2" in snap
    tabs = call(srv, "browser_tabs")
    assert tabs.startswith("Tabs:") and "0: Controls" in tabs and "1: New tab page" in tabs and "(current)" in tabs
    snap = call(srv, "browser_tabs", switch_to=0)
    assert "Switched to tab 0" in snap and "Page: Controls" in snap
    tabs = call(srv, "browser_tabs", close=1)
    assert "\n1:" not in tabs and "0: Controls" in tabs


def test_close_and_start_again(srv, site):
    call(srv, "browser_open", url=site + "/")
    assert call(srv, "browser_close") == "Browser closed."
    assert "Page: Test site" in call(srv, "browser_open", url=site + "/")      # a new window, no error


def _sign_in(s, site):
    snap = call(s, "browser_open", url=site + "/login")
    snap = call(s, "browser_type", ref=H.ref(snap, r'"User"'), text="alice")
    snap = call(s, "browser_type", ref=H.ref(snap, r'"Password"'), text="secret")
    assert 'value="secret"' not in snap                       # passwords are never echoed into the snapshot
    snap = call(s, "browser_click", ref=H.ref(snap, r'button[^\n]*"Sign in"'))
    assert "Signed in as alice" in snap and f"Address: {site}/dashboard" in snap


def test_sign_in_survives_the_agent_closing_the_server(dirs, site):
    """Sign in, close the server the way the agent does (agent/mcp.py: stdin closed, then terminate), start a new
    server on the same profile folder: still signed in. Right after the sign-in, before Chromium's own 30 s flush."""
    s1 = H.server_for(*dirs, CHANNEL)
    _sign_in(s1, site)
    s1.close()
    s2 = H.server_for(*dirs, CHANNEL)
    try:
        assert "Signed in as alice" in call(s2, "browser_open", url=site + "/dashboard")
    finally:
        s2.close()


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM handling is POSIX; on Windows the agent closes stdin first")
def test_sign_in_survives_sigterm(dirs, site):
    s1 = H.server_for(*dirs, CHANNEL)
    _sign_in(s1, site)
    s1.t.p.terminate()                                        # what mcp.py does when stdin did not end the server
    s1.t.p.wait(timeout=15)
    s2 = H.server_for(*dirs, CHANNEL)
    try:
        assert "Signed in as alice" in call(s2, "browser_open", url=site + "/dashboard")
    finally:
        s2.close()
        s1.close()


def test_a_second_profile_is_a_different_sign_in(dirs, tmp_path, site):
    """Sign-ins belong to the profile folder (the Meshpool data folder), not to the machine."""
    s1 = H.server_for(*dirs, CHANNEL)
    _sign_in(s1, site)
    s1.close()
    other = H.server_for(tmp_path / "other-home", dirs[1], CHANNEL)
    try:
        assert "Signed out" in call(other, "browser_open", url=site + "/dashboard")
    finally:
        other.close()


def test_site_lists_hold_for_every_navigation_not_just_the_first(site, dirs):
    port = site.rsplit(":", 1)[1]
    s = H.server_for(*dirs, CHANNEL, extra_env={"MH_FETCH_ALLOW": "127.0.0.1"})
    try:
        text, failed = s.call("browser_open", {"url": f"http://localhost:{port}/dashboard"})
        assert failed and "read only" in text                               # a site not on the list: refused
        text, failed = s.call("browser_open", {"url": f"{site}/hop?to=http://localhost:{port}/dashboard"})
        assert failed or "Dashboard" not in text                             # the redirect is stopped too
    finally:
        s.close()
