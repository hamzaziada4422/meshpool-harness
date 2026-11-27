"""Keyboard input for the terminal UI, read and parsed by prompt_toolkit.

prompt_toolkit does the hard parts: raw mode, UTF-8 split across reads (its incremental decoder), escape sequences and
bracketed paste (Vt100Parser), and on Windows the console's key records (ConsoleInputReader), where a letter such as à
and an arrow key are told apart by the key record itself and a multi-line paste arrives as one paste.

This module only names the keys for tui.py. A key is a str: one character is itself, a longer str is a name ("up",
"enter", "shift_tab", "ctrl_o"...), and a pasted block is the tuple ("paste", text). Tests feed the bytes a terminal
sends ("\\x1b[Z" for Shift+Tab) through `FakeKeys`, which sends them through prompt_toolkit's pipe input.
"""
from __future__ import annotations

import os
import select
import sys
import time

try:
    from prompt_toolkit.input.vt100_parser import Vt100Parser
    from prompt_toolkit.keys import Keys
except ImportError:          # not installed: raw_available() is False and the terminal UI reads lines with input()
    Vt100Parser = Keys = None

_NAMES: dict = {} if Keys is None else {
    Keys.ControlM: "enter", Keys.ControlJ: "enter", Keys.ControlI: "tab", Keys.BackTab: "shift_tab",
    Keys.ControlH: "backspace", Keys.Escape: "esc",
    Keys.Up: "up", Keys.Down: "down", Keys.Left: "left", Keys.Right: "right", Keys.Home: "home", Keys.End: "end",
    Keys.Delete: "delete", Keys.PageUp: "pageup", Keys.PageDown: "pagedown",
    Keys.ShiftUp: "up", Keys.ShiftDown: "down", Keys.ShiftLeft: "left", Keys.ShiftRight: "right",
    Keys.ShiftHome: "home", Keys.ShiftEnd: "end", Keys.ShiftDelete: "delete",
    Keys.ControlUp: "up", Keys.ControlDown: "down", Keys.ControlHome: "home", Keys.ControlEnd: "end",
    Keys.ControlLeft: "word_left", Keys.ControlRight: "word_right",
}
_CTRL = "abcdefgklnoprsuwz"          # Ctrl+letter keys this terminal uses: "ctrl_a" ...; the rest are "unknown"
_ALT: dict = {}               # Alt (Esc first) and a key: word moves and deletes, a new line
_IGNORED: set = set()
if Keys is not None:
    _NAMES.update({getattr(Keys, "Control" + c.upper()): "ctrl_" + c for c in _CTRL})
    _NAMES.update({Keys.ControlDelete: "delete", Keys.ControlShiftLeft: "left", Keys.ControlShiftRight: "right",
                   Keys.ControlShiftUp: "up", Keys.ControlShiftDown: "down", Keys.ControlShiftHome: "home",
                   Keys.ControlShiftEnd: "end"})     # what the old parser read them as
    _ALT.update({"b": "word_left", "f": "word_right", "d": "word_delete", Keys.ControlH: "ctrl_w", Keys.Left: "word_left",
                 Keys.Right: "word_right", Keys.ControlM: "alt_enter", Keys.ControlJ: "alt_enter"})
    _IGNORED.update({Keys.CPRResponse, Keys.Vt100MouseEvent, Keys.WindowsMouseEvent, Keys.Ignore, Keys.ScrollUp,
                     Keys.ScrollDown})


_CSI_FINAL = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "Z": "shift_tab"}
_TILDE = {"1": "home", "7": "home", "4": "end", "8": "end", "3": "delete", "5": "pageup", "6": "pagedown"}


def _char(k) -> bool:
    return isinstance(k, str) and not (Keys is not None and isinstance(k, Keys)) and len(k) == 1


def _csi(ps: list, i: int):
    """ps[i] is a "[" right after the Esc key's own "\\x1b": an escape sequence prompt_toolkit has no name for
    ("\\x1b[1;10D", "\\x1b[1;5Z"), read the way the old parser read it. (name, index after it), or None."""
    j, body = i + 1, ""
    while j < len(ps) and _char(ps[j].key) and (ps[j].key.isdigit() or ps[j].key == ";"):
        body += ps[j].key
        j += 1
    if j >= len(ps) or not _char(ps[j].key) or not ("\x40" <= ps[j].key <= "\x7e") \
            or not (body or ps[j].key in _CSI_FINAL):
        return None
    f = ps[j].key
    if f == "~":
        return _TILDE.get(body.split(";")[0], "unknown"), j + 1
    if f in "CD" and body.endswith((";3", ";5", ";9")):           # Alt or Ctrl with an arrow: a word
        return ("word_right" if f == "C" else "word_left"), j + 1
    return _CSI_FINAL.get(f, "unknown"), j + 1


def _paste(text: str) -> tuple:
    return ("paste", text.replace("\r\n", "\n").replace("\r", "\n"))


def names(presses) -> list:
    """prompt_toolkit KeyPress objects -> this terminal's key names."""
    out, ps, i = [], list(presses), 0
    while i < len(ps):
        k = ps[i].key
        i += 1
        if k in _IGNORED:
            continue
        if k == Keys.BracketedPaste:
            out.append(_paste(ps[i - 1].data))
        elif k == Keys.Escape and i < len(ps) and ps[i].key != Keys.Escape and ps[i].key not in _IGNORED:
            nxt = ps[i].key
            # Alt held with a key: prompt_toolkit gives an Escape whose data is the whole sequence ("\x1b[1;3A" for
            # Alt+Up), or "" on Windows (the console's Alt flag). Only data "\x1b" is the Esc key itself. Read as
            # "esc", Alt+Up cleared the typed line and stopped a running turn.
            mod = ps[i - 1].data != "\x1b"
            csi = None if mod or nxt != "[" else _csi(ps, i)
            if csi:                               # a sequence prompt_toolkit does not know: one key, not its letters
                out.append(csi[0])
                i = csi[1]
            elif nxt in _ALT:
                out.append(_ALT[nxt])
                i += 1
            elif _char(nxt) and nxt >= " ":
                out.append("unknown")             # any other Alt+letter: not Esc and then a letter
                i += 1
            elif mod:                             # Alt with an arrow, Home, Delete...: the key itself
                out.append(_NAMES.get(nxt, "unknown") if isinstance(nxt, Keys) else "unknown")
                i += 1
            else:
                out.append("esc")                 # Esc, then a key of its own (an arrow, Enter on its own...)
        elif isinstance(k, Keys):
            out.append(_NAMES.get(k, "unknown"))
        else:
            out.append(k if k >= " " else "unknown")
    return out


def parse_keys(data: str) -> list:
    """Raw terminal input -> key names, as one complete read (a trailing lone Esc is the Esc key)."""
    got: list = []
    p = Vt100Parser(got.append)
    p.feed(data)
    p.flush()
    return names(got)


class FakeKeys:
    """A scripted key source for tests: FakeKeys("\\x1b[Z/mo", "\\r"), FakeKeys(["up", "enter"]); a callable runs when
    reached. Each str goes through prompt_toolkit's pipe input (create_pipe_input) and its parser, like real keys."""

    def __init__(self, *chunks):
        self.script = list(chunks)
        self.queue: list = []
        self._pipe = None
        self._cm = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _through_pipe(self, text: str) -> list:
        if self._pipe is None:
            from prompt_toolkit.input import create_pipe_input
            self._cm = create_pipe_input()
            self._pipe = self._cm.__enter__()
        got: list = []
        data = text.encode("utf-8")
        for at in range(0, len(data), 8192):          # small writes (a pipe holds 64 KB); a character may be split
            self._pipe.send_bytes(data[at:at + 8192])
            while select.select([self._pipe.fileno()], [], [], 0)[0]:
                got += self._pipe.read_keys()
        got += self._pipe.flush_keys()                # the chunk is complete: a trailing Esc is the Esc key
        return names(got)

    def close(self):
        if self._cm is not None:
            self._cm.__exit__(None, None, None)
            self._cm = self._pipe = None

    def read(self, timeout=None):
        while not self.queue:
            if not self.script:
                self.close()
                raise EOFError
            item = self.script.pop(0)
            if callable(item):
                item()                                # a callable in the script runs here (tests look at the screen)
            elif isinstance(item, str):
                self.queue += self._through_pipe(item)
            else:
                self.queue += list(item)
        return self.queue.pop(0)

    def __del__(self):
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


class RawKeys:
    """Keys from the real terminal. Entering switches it to raw mode with prompt_toolkit (no echo, no line buffering)
    but keeps Ctrl+C a signal (ISIG on Mac and Linux, processed input on Windows), and turns off IEXTEN so Ctrl+O
    reaches the program on macOS; leaving restores the terminal."""

    def __init__(self, fd=None):
        self.win = os.name == "nt"
        self.pending: list = []
        self._mode = None
        self._got: list = []
        if self.win:
            self.fd = fd
            self._reader = None
        else:
            from prompt_toolkit.input.posix_utils import PosixStdinReader
            self.fd = sys.stdin.fileno() if fd is None else fd
            self._reader = PosixStdinReader(self.fd)          # its decoder keeps a character split between reads
            self._parser = Vt100Parser(self._got.append)

    def __enter__(self):
        if self.win:
            from prompt_toolkit.input.win32 import ConsoleInputReader
            self._reader = ConsoleInputReader(recognize_paste=True)    # a pasted block with line breaks: one paste
            self._mode = _win_raw_mode()
        else:
            self._mode = _posix_raw_mode(self.fd)
            sys.stdout.write("\x1b[?2004h")             # bracketed paste: a pasted block arrives as one piece
            sys.stdout.flush()
        self._mode.__enter__()
        return self

    def __exit__(self, *a):
        try:
            if not self.win:
                sys.stdout.write("\x1b[?2004l")
                sys.stdout.flush()
        finally:
            if self._mode is not None:
                self._mode.__exit__(None, None, None)
                self._mode = None
        return False

    def read(self, timeout=None):
        if self.pending:
            return self.pending.pop(0)
        presses = self._read_win(timeout) if self.win else self._read_posix(timeout)
        keys = names(presses or [])
        if not keys:
            return None
        self.pending = keys[1:]
        return keys[0]

    def _read_posix(self, timeout):
        if not select.select([self.fd], [], [], timeout)[0]:
            return None
        while True:
            text = self._reader.read(65536)
            if not text and self._reader.closed and not self._got:
                raise EOFError
            self._parser.feed(text)
            if self._got:
                break
            # nothing whole yet: a paste or an escape sequence split across reads, or a lone Esc
            pasting = getattr(self._parser, "_in_bracketed_paste", False)
            if select.select([self.fd], [], [], 1.0 if pasting else 0.05)[0] and not self._reader.closed:
                continue
            if not pasting or self._reader.closed:
                self._parser.flush()
                break
        got, self._got[:] = list(self._got), []
        return got

    def _read_win(self, timeout):
        from prompt_toolkit.eventloop.win32 import wait_for_handles
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            left = -1 if end is None else max(0, int((end - time.monotonic()) * 1000))
            if not wait_for_handles([self._reader.handle], left):
                return None
            got = list(self._reader.read())
            if got:
                return got
            if end is not None and time.monotonic() >= end:      # a key release or a focus event: nothing to read
                return None


def _posix_raw_mode(fd: int):
    import termios

    from prompt_toolkit.input.vt100 import raw_mode

    class KeepSignals(raw_mode):
        @classmethod
        def _patch_lflag(cls, attrs):
            return attrs & ~(termios.ECHO | termios.ICANON | termios.IEXTEN)

    return KeepSignals(fd)


def _win_raw_mode():
    from prompt_toolkit.input.win32 import raw_mode

    class KeepCtrlC(raw_mode):
        def _patch(self):
            from ctypes import windll
            enable_echo_input, enable_line_input = 0x0004, 0x0002
            windll.kernel32.SetConsoleMode(self.handle, self.original_mode.value & ~(enable_echo_input | enable_line_input))

    return KeepCtrlC()


def raw_available() -> bool:
    """True when stdin is a real terminal this module can put into raw mode (and prompt_toolkit is installed)."""
    if Keys is None:
        return False
    try:
        return os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        return False
