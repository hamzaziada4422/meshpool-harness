"""Browser use works the first time it is asked for: Playwright installs itself when missing, a Chromium already on the
computer is used before anything is downloaded, and a real page opens."""
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from meshpool_harness import browser_use as BU
from meshpool_harness.agent import browser_mcp_server as BM


def _fake(paths):
    return lambda pat: [p for p in paths if __import__("fnmatch").fnmatch(p, pat)]


def test_it_finds_the_newest_chromium_playwright_left_behind_on_each_system():
    linux = ["/home/u/.cache/ms-playwright/chromium-1200/chrome-linux/chrome",
             "/home/u/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"]
    assert BU.find_playwright_chromium("linux", {}, "/home/u", _fake(linux)).endswith("chromium-1243/chrome-linux64/chrome")
    mac = ["/Users/u/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium"]
    assert BU.find_playwright_chromium("darwin", {}, "/Users/u", _fake(mac)).endswith("MacOS/Chromium")
    win = ["C:\\Users\\u\\AppData\\Local\\ms-playwright\\chromium-1243\\chrome-win\\chrome.exe"]
    got = BU.find_playwright_chromium("win32", {"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"}, "C:\\Users\\u",
                                      lambda pat: win if "chrome-win" in pat else [])
    assert got.endswith("chrome.exe")
    assert BU.find_playwright_chromium("linux", {}, "/home/u", _fake([])) == ""


def test_the_browsers_path_variable_is_searched_first_and_zero_means_none():
    custom = ["/data/pw/chromium-1300/chrome-linux/chrome"]
    pick = BU.find_playwright_chromium("linux", {"PLAYWRIGHT_BROWSERS_PATH": "/data/pw"}, "/home/u", _fake(custom))
    assert pick == custom[0]
    assert BU.find_playwright_chromium("linux", {"PLAYWRIGHT_BROWSERS_PATH": "0"}, "/nowhere", _fake([])) == ""


def test_a_missing_playwright_package_is_installed_once_then_used(monkeypatch):
    calls = []
    real_import = __import__

    state = {"installed": False}

    def imp(name, *a, **k):
        if name.startswith("playwright") and not state["installed"]:
            raise ImportError("no playwright")
        if name.startswith("playwright"):
            m = types.ModuleType("playwright.sync_api")
            m.sync_playwright = lambda: "api"
            return m
        return real_import(name, *a, **k)

    def run(cmd, **k):
        calls.append(cmd)
        state["installed"] = True
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("builtins.__import__", imp)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("meshpool_harness.bundled.packaged", lambda: False)
    assert BM.load_playwright()() == "api"
    assert calls and calls[0][1:4] == ["-m", "pip", "install"] and "playwright>=1.40" in calls[0]


def test_when_it_cannot_install_the_message_says_what_to_run(monkeypatch):
    real_import = __import__

    def imp(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("no")
        return real_import(name, *a, **k)
    monkeypatch.setattr("builtins.__import__", imp)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "no network"))
    monkeypatch.setattr("meshpool_harness.bundled.packaged", lambda: False)
    with pytest.raises(BM.ToolFailed) as e:
        BM.load_playwright()
    assert "meshpool install-browser" in str(e.value) and "no network" in str(e.value)


def test_the_packaged_app_never_installs_anything(monkeypatch):
    real_import = __import__
    monkeypatch.setattr("builtins.__import__", lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n.startswith("playwright") else real_import(n, *a, **k))
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr("meshpool_harness.bundled.packaged", lambda: True)
    with pytest.raises(BM.ToolFailed, match="Reinstall Meshpool"):
        BM.load_playwright()
    assert ran == []


def test_an_existing_chromium_is_used_before_any_download(monkeypatch, tmp_path):
    b = BM.Browser()
    monkeypatch.setattr(BU, "find_playwright_chromium", lambda *a, **k: "/opt/pw/chromium-1/chrome")
    seen = {}
    b._launch = lambda prof, **kw: seen.update(kw) or "ctx"
    assert b._use_cached_chromium(True) is True
    assert seen["executable_path"] == "/opt/pw/chromium-1/chrome" and b.ctx == "ctx"
    monkeypatch.setattr(BU, "find_playwright_chromium", lambda *a, **k: "")
    assert BM.Browser()._use_cached_chromium(True) is False


def test_the_settings_check_says_it_sets_itself_up(monkeypatch):
    from meshpool_harness import config as C
    row = next(r for r in C.system_check() if r["name"] == "Browser use")
    assert row["ok"] is True and ("ready" in row["detail"] or "sets itself up" in row["detail"])


@pytest.mark.skipif(not BU.find_playwright_chromium(), reason="no Chromium on this computer to open a page with")
def test_a_real_page_opens_from_a_cold_start(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<html><body><h1>Hello UX</h1><button>Go</button><input aria-label=Name></body></html>")
    code = ("from meshpool_harness.agent import browser_mcp_server as B\n"
            f"print(B.browser_open('file://{page}'))\nprint(B.browser_snapshot())\nB.browser_close()\n")
    env = dict(os.environ, MESHPOOL_HOME=str(tmp_path / "home"), MH_BROWSER_HEADLESS="1")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=240,
                       cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr[-400:]
    assert '[e1] button "Go"' in r.stdout and "Hello UX" in r.stdout
