"""Computer use: every tool on the macOS and Windows backends with the operating system mocked (a fake Quartz module that
records events; a fake ctypes.windll), the coordinate mapping, the self-check on every platform, and the doctor row.
The real thing needs a real Mac or Windows PC: docs/plan/BROWSER_COMPUTER_USE.md has the manual check list."""
import ctypes
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import computer_mcp_server as CU  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(CU.time, "sleep", lambda s: None)
    monkeypatch.setitem(CU.STATE, "scale", 1.0)
    monkeypatch.setattr(CU, "_OS", None)


# ======================================= macOS =======================================================================
class FakeQuartz:
    kCGEventFlagMaskCommand, kCGEventFlagMaskControl = 1 << 20, 1 << 18
    kCGEventFlagMaskAlternate, kCGEventFlagMaskShift = 1 << 19, 1 << 17
    kCGEventMouseMoved, kCGEventLeftMouseDragged = 5, 6
    kCGEventLeftMouseDown, kCGEventLeftMouseUp = 1, 2
    kCGEventRightMouseDown, kCGEventRightMouseUp = 3, 4
    kCGEventOtherMouseDown, kCGEventOtherMouseUp = 25, 26
    kCGMouseButtonLeft, kCGMouseButtonRight, kCGMouseButtonCenter = 0, 1, 2
    kCGMouseEventClickState, kCGScrollEventUnitLine, kCGHIDEventTap = 1, 1, 0

    def __init__(self):
        self.posted, self.screen_ok = [], True

    def CGMainDisplayID(self):
        return 1

    def CGDisplayBounds(self, _):
        return types.SimpleNamespace(size=types.SimpleNamespace(width=1440, height=900))

    def CGPreflightScreenCaptureAccess(self):
        return self.screen_ok

    def CGEventCreateMouseEvent(self, src, kind, pos, button):
        return {"t": "mouse", "kind": kind, "pos": tuple(pos), "button": button, "click": 0}

    def CGEventSetIntegerValueField(self, ev, field, value):
        assert field == self.kCGMouseEventClickState
        ev["click"] = value

    def CGEventCreateScrollWheelEvent(self, src, unit, n, *wheels):
        return {"t": "scroll", "unit": unit, "wheels": wheels}

    def CGEventCreateKeyboardEvent(self, src, code, down):
        return {"t": "key", "code": code, "down": down, "flags": 0, "text": None}

    def CGEventKeyboardSetUnicodeString(self, ev, n, s):
        ev["text"], ev["n"] = s, n

    def CGEventSetFlags(self, ev, flags):
        ev["flags"] = flags

    def CGEventPost(self, tap, ev):
        assert tap == self.kCGHIDEventTap
        self.posted.append(ev)


@pytest.fixture
def mac(monkeypatch):
    q = FakeQuartz()
    monkeypatch.setitem(sys.modules, "Quartz", q)
    monkeypatch.setattr(CU, "_mac_accessibility", lambda: True)
    m = CU.Mac()
    monkeypatch.setattr(CU, "_OS", m)
    return m


def kinds(m):
    return [(e["kind"], e["pos"], e["click"]) for e in m.Q.posted if e["t"] == "mouse"]


def test_mac_click_variants(mac):
    Q = mac.Q
    CU.click(100, 50)
    assert kinds(mac) == [(Q.kCGEventMouseMoved, (100, 50), 0), (Q.kCGEventLeftMouseDown, (100, 50), 1),
                          (Q.kCGEventLeftMouseUp, (100, 50), 1)]
    Q.posted.clear()
    CU.click(10, 20, double=True)
    assert [(k, c) for k, _, c in kinds(mac)][1:] == [(Q.kCGEventLeftMouseDown, 1), (Q.kCGEventLeftMouseUp, 1),
                                                       (Q.kCGEventLeftMouseDown, 2), (Q.kCGEventLeftMouseUp, 2)]
    Q.posted.clear()
    CU.click(1, 2, button="right")
    assert [k for k, _, _ in kinds(mac)][1:] == [Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp]
    assert [e["button"] for e in Q.posted][1:] == [Q.kCGMouseButtonRight] * 2
    Q.posted.clear()
    CU.click(1, 2, button="middle")
    assert [k for k, _, _ in kinds(mac)][1:] == [Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp]
    Q.posted.clear()
    CU.click(1, 2, button="nonsense")                      # an unknown button is a left click, not a crash
    assert kinds(mac)[1][0] == Q.kCGEventLeftMouseDown


def test_mac_move_and_drag(mac):
    Q = mac.Q
    CU.move_mouse(30, 40)
    assert kinds(mac) == [(Q.kCGEventMouseMoved, (30, 40), 0)]
    Q.posted.clear()
    CU.drag(10, 10, 110, 60)
    ks = kinds(mac)
    assert ks[0][0] == Q.kCGEventMouseMoved and ks[1] == (Q.kCGEventLeftMouseDown, (10, 10), 0)
    drags = [k for k in ks if k[0] == Q.kCGEventLeftMouseDragged]
    assert len(drags) == 20 and drags[-1][1] == (110, 60) and drags[0][1][0] < drags[-1][1][0]
    assert ks[-1] == (Q.kCGEventLeftMouseUp, (110, 60), 0)


def test_mac_scroll(mac):
    CU.scroll(5, 6, down=3)
    ev = [e for e in mac.Q.posted if e["t"] == "scroll"][0]
    assert ev["wheels"] == (-3, 0) and ev["unit"] == mac.Q.kCGScrollEventUnitLine
    assert kinds(mac)[0][1] == (5, 6)                      # the pointer goes to the spot first
    mac.Q.posted.clear()
    CU.scroll(5, 6, down=-2, right=4)
    assert [e for e in mac.Q.posted if e["t"] == "scroll"][0]["wheels"] == (2, -4)


def test_mac_type_text_in_chunks_and_unicode(mac):
    out = CU.type_text("héllo wörld, this is a longer sentence 😀")
    assert out == "Typed 40 characters."
    keys = [e for e in mac.Q.posted if e["t"] == "key"]
    downs = [e for e in keys if e["down"]]
    assert "".join(e["text"] for e in downs) == "héllo wörld, this is a longer sentence 😀"
    assert len(keys) == 2 * len(downs) and all(e["n"] <= 16 for e in downs)
    assert [e for e in downs if "😀" in e["text"]][0]["n"] >= 2        # counted in UTF-16 units, not characters


def test_mac_press_keys_and_combos(mac):
    Q = mac.Q
    CU.press_key("enter")
    CU.press_key("cmd+c")
    CU.press_key("ctrl+shift+t")
    CU.press_key("cmd+-")                                  # the minus key itself, not an empty name
    CU.press_key("cmd+a delete")                           # several, separated by spaces
    CU.press_key("f5 up")
    seq = [(e["code"], e["down"], e["flags"]) for e in Q.posted]
    assert seq[:2] == [(36, True, 0), (36, False, 0)]
    assert seq[2:4] == [(8, True, Q.kCGEventFlagMaskCommand), (8, False, Q.kCGEventFlagMaskCommand)]
    assert seq[4][2] == Q.kCGEventFlagMaskControl | Q.kCGEventFlagMaskShift and seq[4][0] == 17
    assert seq[6][0] == 27 and seq[6][2] == Q.kCGEventFlagMaskCommand
    assert [s[0] for s in seq[8:]] == [0, 0, 51, 51, 96, 96, 126, 126]
    with pytest.raises(CU.ToolFailed, match="Unknown key"):
        CU.press_key("cmd+nonsense")
    with pytest.raises(CU.ToolFailed, match="Unknown modifier"):
        CU.press_key("hyper+c")


def test_parse_combo():
    assert CU._parse_combo("Cmd+Shift+4") == (["cmd", "shift"], "4")
    assert CU._parse_combo("ctrl-alt-delete") == (["ctrl", "alt"], "delete")
    assert CU._parse_combo("-") == ([], "-") and CU._parse_combo("cmd+-") == (["cmd"], "-")
    assert CU._parse_combo("ctrl+-") == (["ctrl"], "-")
    with pytest.raises(CU.ToolFailed):
        CU._parse_combo("  ")


def _fake_screencapture(monkeypatch, size=(2880, 1800), rc=0):
    from PIL import Image
    calls = []

    def run(args, **kw):
        calls.append(args)
        if rc == 0:
            Image.new("RGB", size, (10, 120, 200)).save(args[-1], "PNG")
        return subprocess.CompletedProcess(args, rc, b"", b"")
    monkeypatch.setattr(CU.subprocess, "run", run)
    return calls


def test_mac_screenshot_scales_retina_to_screen_units(mac, monkeypatch):
    calls = _fake_screencapture(monkeypatch)
    res = CU.screenshot()
    assert calls[0][:5] == ["screencapture", "-x", "-m", "-t", "png"]
    assert res[0]["type"] == "text" and "Screenshot 1366x854" in res[0]["text"]
    assert res[1]["type"] == "image" and res[1]["mimeType"] == "image/png"
    assert abs(CU.STATE["scale"] - 1440 / 1366) < 1e-9
    CU.click(1366, 854)                                    # the far corner of the screenshot is the far corner of the screen
    assert kinds(mac)[0][1] == pytest.approx((1440, 900), abs=1)       # within the screenshot's own rounding


def test_mac_screenshot_without_permission_says_where_to_fix_it(mac, monkeypatch):
    calls = _fake_screencapture(monkeypatch)
    mac.Q.screen_ok = False
    with pytest.raises(CU.ToolFailed, match=r"Screen Recording") as e:
        CU.screenshot()
    assert "System Settings > Privacy & Security" in str(e.value) and not calls      # nothing was captured
    mac.Q.screen_ok = True
    _fake_screencapture(monkeypatch, rc=1)
    with pytest.raises(CU.ToolFailed, match="blocked the screenshot"):
        CU.screenshot()


def test_mac_open_app(mac, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(CU.subprocess, "run", lambda a, **kw: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    assert CU.open_app("Notes").startswith("Opened Notes.")
    assert CU.open_app("https://example.com/a?b=1&c=2").startswith("Opened")      # a web address: plain open
    f = tmp_path / "report.pdf"
    f.write_text("x")
    CU.open_app(str(f))                                                             # a file: plain open
    assert calls == [["open", "-a", "Notes"], ["open", "--", "https://example.com/a?b=1&c=2"], ["open", "--", str(f)]]
    with pytest.raises(CU.ToolFailed):
        CU.open_app("-W")                                                           # never read as an option
    with pytest.raises(CU.ToolFailed):
        CU.open_app("  ")
    monkeypatch.setattr(CU.subprocess, "run", lambda a, **kw: subprocess.CompletedProcess(a, 1, "", "Unable to find application"))
    with pytest.raises(CU.ToolFailed, match="Unable to find application"):
        CU.open_app("NoSuchApp")


def test_mac_screen_info_and_front_app(mac, monkeypatch):
    app = types.SimpleNamespace(localizedName=lambda: "Finder")
    ws = types.SimpleNamespace(sharedWorkspace=lambda: types.SimpleNamespace(frontmostApplication=lambda: app))
    monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace(NSWorkspace=ws))
    monkeypatch.setattr(CU.sys, "platform", "darwin")
    info = CU.screen_info()
    assert "Screen 1440x900 (macOS points)" in info and "Front app: Finder" in info and "Control is not allowed" not in info
    monkeypatch.setattr(CU, "_mac_accessibility", lambda: False)
    assert "Accessibility" in CU.screen_info()


def test_mac_without_accessibility_every_action_says_how_to_allow_it(mac, monkeypatch):
    monkeypatch.setattr(CU, "_mac_accessibility", lambda: False)
    for fn, args in ((CU.click, (1, 1)), (CU.move_mouse, (1, 1)), (CU.drag, (1, 1, 2, 2)), (CU.scroll, (1, 1)),
                     (CU.type_text, ("x",)), (CU.press_key, ("enter",))):
        with pytest.raises(CU.ToolFailed, match="Accessibility"):
            fn(*args)
    assert not mac.Q.posted


# ======================================= Windows =====================================================================
@pytest.fixture
def win(monkeypatch):
    user32 = MagicMock()
    user32.GetSystemMetrics.side_effect = lambda i: (1920, 1080)[i]
    user32.VkKeyScanW.side_effect = lambda ch: 0x0100 | ord(chr(ch).upper())
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=user32, shcore=MagicMock()), raising=False)
    w = CU.Win()
    monkeypatch.setattr(CU, "_OS", w)
    return w


def sent(w):
    """Every INPUT given to SendInput, per call, as (vk, scan, flags) tuples."""
    out = []
    for c in w.u.SendInput.call_args_list:
        n, arr, size = c.args
        assert size == ctypes.sizeof(w.INPUT) and len(arr) == n
        out.append([(i.u.ki.wVk, i.u.ki.wScan, i.u.ki.dwFlags) for i in arr])
    return out


def test_win_click_variants(win):
    CU.click(100, 50)
    assert win.u.SetCursorPos.call_args_list[0] == call(100, 50)
    assert win.u.mouse_event.call_args_list == [call(0x02, 0, 0, 0, 0), call(0x04, 0, 0, 0, 0)]
    win.u.mouse_event.reset_mock()
    CU.click(1, 2, double=True)
    assert win.u.mouse_event.call_args_list == [call(0x02, 0, 0, 0, 0), call(0x04, 0, 0, 0, 0)] * 2
    win.u.mouse_event.reset_mock()
    CU.click(1, 2, button="right")
    assert win.u.mouse_event.call_args_list == [call(0x08, 0, 0, 0, 0), call(0x10, 0, 0, 0, 0)]
    win.u.mouse_event.reset_mock()
    CU.click(1, 2, button="middle")
    assert win.u.mouse_event.call_args_list == [call(0x20, 0, 0, 0, 0), call(0x40, 0, 0, 0, 0)]


def test_win_move_and_drag(win):
    CU.move_mouse(7.6, 8.2)
    assert win.u.SetCursorPos.call_args_list == [call(7, 8)]
    win.u.SetCursorPos.reset_mock()
    CU.drag(10, 10, 110, 60)
    pos = [c.args for c in win.u.SetCursorPos.call_args_list]
    assert pos[0] == (10, 10) and pos[-1] == (110, 60) and len(pos) == 21
    assert win.u.mouse_event.call_args_list == [call(0x02, 0, 0, 0, 0), call(0x04, 0, 0, 0, 0)]


def test_win_scroll(win):
    CU.scroll(5, 6, down=3)
    assert win.u.SetCursorPos.call_args_list == [call(5, 6)]
    assert win.u.mouse_event.call_args_list == [call(0x0800, 0, 0, (-360) & 0xFFFFFFFF, 0)]   # a wheel DWORD: down is negative
    win.u.mouse_event.reset_mock()
    CU.scroll(5, 6, down=-2, right=1)
    assert win.u.mouse_event.call_args_list == [call(0x0800, 0, 0, 240, 0), call(0x01000, 0, 0, 120, 0)]


def test_win_type_text_sends_unicode_key_pairs(win):
    assert CU.type_text("Hi é") == "Typed 4 characters."
    calls = sent(win)
    assert len(calls) == 4 and all(len(c) == 2 for c in calls)
    assert [c[0][1] for c in calls] == [ord("H"), ord("i"), ord(" "), ord("é")]
    assert all(c[0][2] == 0x0004 and c[1][2] == 0x0006 and c[0][0] == 0 for c in calls)    # KEYEVENTF_UNICODE, then + KEYUP
    win.u.SendInput.reset_mock()
    CU.type_text("😀")                                                                      # a surrogate pair: two units
    assert [c[0][1] for c in sent(win)] == [0xD83D, 0xDE00]


def test_win_press_keys_and_combos(win):
    CU.press_key("enter")
    assert sent(win)[-1] == [(0x0D, 0, 0), (0x0D, 0, 2)]
    CU.press_key("ctrl+shift+t")
    assert sent(win)[-1] == [(0x11, 0, 0), (0x10, 0, 0), (0x54, 0, 0), (0x54, 0, 2), (0x10, 0, 2), (0x11, 0, 2)]
    CU.press_key("cmd+d")                                                    # cmd is the Windows key
    assert sent(win)[-1][0] == (0x5B, 0, 0) and sent(win)[-1][1] == (0x44, 0, 0)
    CU.press_key("alt+f4 f5 pagedown")
    assert [c[0][0] for c in sent(win)[-3:]] == [0x12, 0x74, 0x22]
    CU.press_key("ctrl+-")                                                   # the minus key
    assert [s[0] for s in sent(win)[-1]] == [0x11, 0x2D, 0x2D, 0x11]
    with pytest.raises(CU.ToolFailed, match="Unknown key"):
        CU.press_key("ctrl+nonsense")


def test_win_screenshot_scale_and_pillow_fallback(win, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(win.u.GetSystemMetrics, "side_effect", lambda i: (3840, 2160)[i])
    monkeypatch.setitem(sys.modules, "mss", None)                          # mss not installed (the app does not ship it)
    grabbed = []
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda *a, **k: grabbed.append(1) or Image.new("RGB", (3840, 2160), "white"))
    win.u.GetForegroundWindow.return_value = 7
    win.u.GetWindowTextW.side_effect = lambda h, buf, n: setattr(buf, "value", "Untitled - Notepad")
    res = CU.screenshot()
    assert grabbed and "Screenshot 1366x768" in res[0]["text"] and "Front app: Untitled - Notepad" in res[0]["text"]
    assert res[1]["mimeType"] == "image/png"
    CU.click(683, 384)
    assert win.u.SetCursorPos.call_args_list[0] == call(1920, 1079)         # int(683*2.8111), int(384*2.8111)
    info = CU.screen_info()
    assert "Screen 3840x2160 (pixels)" in info


def test_win_grab_uses_mss_when_present(win, monkeypatch):
    shot = types.SimpleNamespace(size=(4, 2), bgra=bytes([255, 0, 0, 255]) * 8)        # blue pixels, BGRA
    m = MagicMock()
    m.monitors = [{}, {"left": 0, "top": 0, "width": 4, "height": 2}]
    m.grab.return_value = shot
    fake_mss = types.SimpleNamespace(mss=lambda: types.SimpleNamespace(__enter__=lambda s: m, __exit__=lambda *a: None))

    class Ctx:
        def __enter__(self):
            return m

        def __exit__(self, *a):
            return False
    fake_mss.mss = lambda: Ctx()
    monkeypatch.setitem(sys.modules, "mss", fake_mss)
    img = win.grab()
    assert img.size == (4, 2) and img.getpixel((0, 0)) == (0, 0, 255)       # BGRA is read as blue, not red
    m.grab.assert_called_once_with(m.monitors[1])


def test_win_open_app(win, monkeypatch):
    opened = []
    monkeypatch.setattr(CU.os, "startfile", lambda n: opened.append(n), raising=False)
    assert CU.open_app("notepad").startswith("Opened notepad.")
    assert opened == ["notepad"]
    for bad in ("x&calc", "a|b", "%COMSPEC%"):
        with pytest.raises(CU.ToolFailed, match="Refused"):
            CU.open_app(bad)
    # ShellExecute does not know it, but it is a program on PATH: started as an argument list, never through cmd
    def fail(n):
        raise OSError("not found")
    started = []
    monkeypatch.setattr(CU.os, "startfile", fail, raising=False)
    monkeypatch.setattr(CU.shutil, "which", lambda n: r"C:\tools\mytool.exe")
    monkeypatch.setattr(CU.subprocess, "Popen", lambda a, **kw: started.append((a, kw)), raising=False)
    CU.open_app("mytool")
    assert started == [([r"C:\tools\mytool.exe"], {"creationflags": 0x00000010})]
    monkeypatch.setattr(CU.shutil, "which", lambda n: r"C:\tools\run.bat")        # a script would run through cmd.exe
    with pytest.raises(CU.ToolFailed, match="Could not open"):
        CU.open_app("run")


def test_win_desktop_probe(win, monkeypatch):
    win.u.OpenInputDesktop.return_value = 0                                       # locked screen / secure desktop
    assert CU._win_desktop() == (False, "")
    win.u.OpenInputDesktop.return_value = 99
    win.u.GetUserObjectInformationW.side_effect = lambda h, idx, buf, size, need: setattr(buf, "value", "Default") or 1
    assert CU._win_desktop() == (True, "Default")
    win.u.GetUserObjectInformationW.side_effect = lambda h, idx, buf, size, need: setattr(buf, "value", "Winlogon") or 1
    assert CU._win_desktop() == (False, "Winlogon")
    win.u.OpenInputDesktop.side_effect = OSError("no user32")
    assert CU._win_desktop() == (None, "")


# ======================================= the self-check ==============================================================
def test_status_macos(monkeypatch):
    monkeypatch.setattr(CU, "_mac_screen_access", lambda: True)
    monkeypatch.setattr(CU, "_mac_accessibility", lambda: True)
    monkeypatch.setattr(CU, "_have", lambda m: True)
    st = CU.computer_status("darwin")
    assert st["platform"] == "macos" and st["supported"] and st["ready"] and "ready on macOS" in st["summary"]
    monkeypatch.setattr(CU, "_mac_screen_access", lambda: False)
    monkeypatch.setattr(CU, "_mac_accessibility", lambda: False)
    st = CU.computer_status("darwin")
    assert not st["ready"]
    by = {i["name"]: i for i in st["items"]}
    assert by["Screen Recording"]["ok"] is False and "System Settings > Privacy & Security > Screen Recording" in by["Screen Recording"]["fix"]
    assert "System Settings > Privacy & Security > Accessibility" in by["Accessibility"]["fix"]
    monkeypatch.setattr(CU, "_mac_screen_access", lambda: None)                   # cannot tell: reported, never "ok"
    monkeypatch.setattr(CU, "_mac_accessibility", lambda: True)
    st = CU.computer_status("darwin")
    assert not st["ready"] and {i["name"]: i["ok"] for i in st["items"]}["Screen Recording"] is None
    text = CU.format_status(st)
    assert "unknown" in text and "To fix:" in text


def test_status_windows(monkeypatch):
    monkeypatch.setattr(CU, "_have", lambda m: True)
    monkeypatch.setattr(CU, "_win_desktop", lambda: (True, "Default"))
    st = CU.computer_status("win32")
    assert st["platform"] == "windows" and st["ready"]
    assert any("none needed" in i["detail"] for i in st["items"])                   # no permissions to ask for
    monkeypatch.setattr(CU, "_win_desktop", lambda: (False, ""))
    st = CU.computer_status("win32")
    assert not st["ready"] and "locked" in st["summary"] and "Unlock" in CU.format_status(st)


def test_status_linux_is_plain_about_it():
    st = CU.computer_status("linux")
    assert st["platform"] == "linux" and not st["supported"] and not st["ready"]
    assert "not supported on Linux yet" in st["summary"]
    assert "not supported on Linux yet" in CU.TOOLS["computer_check"].fn()
    with pytest.raises(CU.ToolFailed, match="not supported on Linux yet"):
        CU.os_()


def test_the_check_is_a_tool_and_is_in_the_doctor_and_the_capabilities(monkeypatch, tmp_path):
    assert "computer_check" in CU.TOOLS
    from meshpool_harness import config as C
    rows = {r["name"]: r for r in C.system_check()}
    assert "Computer use" in rows and rows["Computer use"]["optional"]
    monkeypatch.setattr(CU, "computer_status", lambda platform=None: {"ready": False, "summary": "needs attention",
                                                                    "items": [{"name": "Accessibility", "fix": "Allow it."}]})
    row = {r["name"]: r for r in C.system_check()}["Computer use"]
    assert row["ok"] is False and "Accessibility: Allow it." in row["fix"]
    from meshpool_harness.ui import settings_v2 as SV
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    cap = next(c for c in SV.capabilities() if c["id"] == "computer")
    assert cap["computer_status"]["summary"] == "needs attention"
