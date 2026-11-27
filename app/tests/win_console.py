"""The Windows console, faked on any system, so the terminal UI's Windows key reader can be tested here: prompt_toolkit's
own Windows modules (ConsoleInputReader, raw_mode, wait_for_handles) are loaded with a stand-in for kernel32 that
hands out key records, the way ReadConsoleInputW does."""
from __future__ import annotations

import ctypes
import sys
import types

SHIFT = 0x10


class Kernel32:
    def __init__(self):
        self.batches: list = []          # each batch: what one ReadConsoleInputW call returns
        self.mode = 0x1F7                # processed, line and echo input on, as a console starts

    def GetStdHandle(self, n):
        return 1

    def GetConsoleMode(self, h, p):
        p.contents.value = self.mode
        return 1

    def SetConsoleMode(self, h, mode):
        self.mode = mode.value if hasattr(mode, "value") else int(mode)
        return 1

    def WaitForMultipleObjects(self, n, arr, wait_all, timeout):
        return 0 if self.batches else 0x102

    def ReadConsoleInputW(self, h, p, max_count, read):
        from prompt_toolkit.win32_types import INPUT_RECORD
        batch = self.batches.pop(0) if self.batches else []
        for i, (ch, vk, state) in enumerate(batch):
            rec = INPUT_RECORD()
            rec.EventType = 1
            ev = rec.Event.KeyEvent
            ev.KeyDown, ev.RepeatCount, ev.VirtualKeyCode, ev.ControlKeyState = 1, 1, vk, state
            ev.uChar.UnicodeChar = ch
            p.contents[i] = rec
        read.contents.value = len(batch)
        return 1


def key(ch: str, vk: int = 0, state: int = 0):
    return (ch, vk, state)


def install(monkeypatch) -> Kernel32:
    """Load prompt_toolkit's Windows input modules against a fake kernel32; returns it (put batches in .batches)."""
    k = Kernel32()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=k), raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(get_osfhandle=lambda fd: 1))
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True, fileno=lambda: 0))
    for m in ("prompt_toolkit.input.win32", "prompt_toolkit.eventloop.win32"):
        monkeypatch.delitem(sys.modules, m, raising=False)   # loaded fresh with the fake; dropped again afterwards
    import prompt_toolkit.eventloop.win32  # noqa: F401
    import prompt_toolkit.input.win32  # noqa: F401
    for m in ("prompt_toolkit.input.win32", "prompt_toolkit.eventloop.win32"):
        monkeypatch.setitem(sys.modules, m, sys.modules[m])
    return k


def raw_keys(monkeypatch):
    """(RawKeys reading the fake Windows console, the fake kernel32)."""
    from meshpool_harness.agent import tui_input as K
    k = install(monkeypatch)
    r = K.RawKeys.__new__(K.RawKeys)
    r.win, r.pending, r._mode, r._got, r.fd, r._reader = True, [], None, [], None, None
    return r, k


def read_all(r, k, batches) -> list:
    k.batches = [list(b) for b in batches]
    out = []
    while k.batches or r.pending:
        got = r.read(timeout=0)
        if got is not None:
            out.append(got)
    return out
