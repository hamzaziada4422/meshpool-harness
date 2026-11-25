"""ws18: computer use maps clicks from the first call (M32-06); Capabilities tells the truth about browser use and
computer use on this computer (F-27, F-37)."""
import json
import sys

import pytest

from meshpool_harness import config as C
from meshpool_harness.agent import computer_mcp_server as CU
from meshpool_harness.ui import settings_v2 as SV


class _Screen:
    def screen_size(self):
        return 2880, 1800


def test_click_before_any_screenshot_uses_the_screens_scale(monkeypatch):
    monkeypatch.setitem(CU.STATE, "scale", None)
    monkeypatch.setattr(CU, "_OS", _Screen())
    monkeypatch.setattr(CU, "MAX_W", 1440)
    assert CU._pt(100, 50) == (200.0, 100.0)             # a 2880-wide screen shown 1440 wide: twice, not 1.0
    monkeypatch.setitem(CU.STATE, "scale", None)
    monkeypatch.setattr(CU, "MAX_W", 1366)
    x, _ = CU._pt(100, 50)
    assert abs(x - 100 * 2880 / 1366) < 1e-9


def test_small_screen_scale_is_one(monkeypatch):
    class Small:
        def screen_size(self):
            return 1024, 768
    monkeypatch.setitem(CU.STATE, "scale", None)
    monkeypatch.setattr(CU, "_OS", Small())
    assert CU._pt(10, 20) == (10.0, 20.0)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    C.ensure_home()
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {
        "browser": {"command": "y"}, "computer-use": {"command": "z"}}}))
    return tmp_path


def test_linux_does_not_offer_computer_use(home, monkeypatch):
    monkeypatch.setattr(SV, "computer_status", lambda: {"supported": False, "ready": False,
                                                        "summary": "Computer use is not supported on Linux yet."})
    row = {c["id"]: c for c in SV.capabilities()}["computer"]
    assert row["available"] is False and row["on"] is False and "Linux" in row["reason"]
    with pytest.raises(ValueError, match="Linux"):
        SV.set_capability("computer", True)
    SV.set_capability("computer", False)                  # switching it off is always allowed


def test_supported_computer_use_stays_available(home, monkeypatch):
    monkeypatch.setattr(SV, "computer_status", lambda: {"supported": True, "ready": False, "summary": "needs attention"})
    row = {c["id"]: c for c in SV.capabilities()}["computer"]
    assert row["available"] is True and "reason" not in row


def test_browser_use_without_playwright_is_not_offered(home, monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n == "playwright" else real(n, *a))
    row = {c["id"]: c for c in SV.capabilities()}["browser"]
    assert row["available"] is False and row["on"] is False and "Playwright" in row["reason"]
    with pytest.raises(ValueError, match="Playwright"):
        SV.set_capability("browser", True)


def test_browser_use_with_a_missing_chosen_browser_says_so(home, monkeypatch):
    monkeypatch.setattr(SV, "browser_use", lambda: {"mode": "installed", "modes": [
        {"id": "meshpool", "name": "Meshpool", "available": True, "detail": "built in"},
        {"id": "installed", "name": "Installed", "available": False, "detail": "none found on this computer"}]})
    row = {c["id"]: c for c in SV.capabilities()}["browser"]
    assert row["available"] is False and "none found" in row["reason"]


def test_ui_shows_the_reason_and_disables_the_switch():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui" / "settings2.js").read_text()
    assert "f.reason" in js and "f.on && f.available" in js
