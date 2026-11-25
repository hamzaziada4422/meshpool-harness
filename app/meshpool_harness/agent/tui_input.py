"""Keyboard input for the terminal UI, standard library only: termios on macOS and Linux, msvcrt on Windows.

A key is a str: one character is itself, a longer str is a name ("up", "enter", "shift_tab", "ctrl_o"...), and a pasted
block is the tuple ("paste", text). `parse_keys` turns raw terminal input into keys, so tests feed the same bytes a
terminal sends (for example "\\x1b[Z" for Shift+Tab) through `FakeKeys`.
"""
from __future__ import annotations

import os
import sys

_CSI = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "Z": "shift_tab"}
_TILDE = {"1": "home", "7": "home", "4": "end", "8": "end", "3": "delete", "5": "pageup", "6": "pagedown"}
_CTRL = {"\x01": "ctrl_a", "\x02": "ctrl_b", "\x03": "ctrl_c", "\x04": "ctrl_d", "\x05": "ctrl_e", "\x06": "ctrl_f",
         "\x0b": "ctrl_k", "\x0c": "ctrl_l", "\x0e": "ctrl_n", "\x0f": "ctrl_o", "\x10": "ctrl_p", "\x15": "ctrl_u",
         "\x17": "ctrl_w", "\x1a": "ctrl_z", "\x12": "ctrl_r", "\x07": "ctrl_g", "\x13": "ctrl_s", "\t": "tab", "\x7f": "backspace", "\x08": "backspace", "\r": "enter",
         "\n": "enter"}


def parse_keys(data: str) -> list:
    out, i, n = [], 0, len(data)
    while i < n:
        c = data[i]
        if c == "\x1b":
            if i + 1 >= n:
                out.append("esc")
                i += 1
            elif data[i + 1] == "[":
                j = i + 2
                while j < n and not (0x40 <= ord(data[j]) <= 0x7e):
                    j += 1
                body, final = data[i + 2:j], data[j] if j < n else ""
                i = j + 1
                if final == "~" and body == "200":
                    end = data.find("\x1b[201~", i)
                    text = data[i:end if end >= 0 else n]
                    out.append(("paste", text.replace("\r\n", "\n").replace("\r", "\n")))
                    i = end + 6 if end >= 0 else n
                elif final == "~":
                    out.append(_TILDE.get(body.split(";")[0], "unknown"))
                elif final in "CD" and body.endswith((";3", ";5", ";9")):   # Alt or Ctrl with an arrow: a word
                    out.append("word_right" if final == "C" else "word_left")
                else:
                    out.append(_CSI.get(final, "unknown"))
            elif data[i + 1] == "O" and i + 2 < n:
                out.append(_CSI.get(data[i + 2], "unknown"))
                i += 3
            elif data[i + 1] in "\r\n":
                out.append("alt_enter")
                i += 2
            elif data[i + 1] in "bfd\x7f":              # Alt+B, Alt+F: a word back or on; Alt+D, Alt+Backspace: delete one
                out.append({"b": "word_left", "f": "word_right", "d": "word_delete", "\x7f": "word_backspace"}[data[i + 1]])
                i += 2
            else:
                out.append("esc")
                i += 1
        else:
            out.append(_CTRL.get(c, c if c >= " " else "unknown"))
            i += 1
    return out


class FakeKeys:
    """A scripted key source: FakeKeys("\\x1b[Z/mo", "\\r"), FakeKeys(["up", "enter"]); a callable runs when reached."""

    def __init__(self, *chunks):
        self.queue: list = []
        for ch in chunks:
            self.queue += parse_keys(ch) if isinstance(ch, str) else [ch] if callable(ch) else list(ch)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, timeout=None):
        while self.queue and callable(self.queue[0]):
            self.queue.pop(0)()                      # a callable in the script runs here (tests look at the screen)
        if not self.queue:
            raise EOFError
        return self.queue.pop(0)


class RawKeys:
    """Raw keys from the real terminal. Entering switches to cbreak mode (no echo, no line buffering) but keeps Ctrl+C
    as a signal, and turns off IEXTEN so Ctrl+O reaches the program on macOS; leaving restores the terminal."""

    def __init__(self, fd=None):
        self.fd = sys.stdin.fileno() if fd is None else fd
        self.pending: list = []
        self.saved = None
        self.win = os.name == "nt"

    def __enter__(self):
        if self.win:
            return self
        import termios
        self.saved = termios.tcgetattr(self.fd)
        new = termios.tcgetattr(self.fd)
        new[3] &= ~(termios.ICANON | termios.ECHO | termios.IEXTEN)
        new[0] &= ~termios.IXON                        # Ctrl+S and Ctrl+Q are keys here, not flow control
        new[6][termios.VMIN], new[6][termios.VTIME] = 1, 0
        termios.tcsetattr(self.fd, termios.TCSADRAIN, new)
        sys.stdout.write("\x1b[?2004h")             # bracketed paste: a pasted block arrives as one piece
        sys.stdout.flush()
        return self

    def __exit__(self, *a):
        if self.win:
            return False
        import termios
        try:
            sys.stdout.write("\x1b[?2004l")
            sys.stdout.flush()
        finally:
            if self.saved is not None:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
        return False

    def read(self, timeout=None):
        if self.pending:
            return self.pending.pop(0)
        if self.win:
            keys = self._read_win()
        else:
            import select
            if timeout is not None and not select.select([self.fd], [], [], timeout)[0]:
                return None
            data = os.read(self.fd, 4096)
            if not data:
                raise EOFError
            text = data.decode("utf-8", "replace")
            while text.startswith("\x1b[200~") and "\x1b[201~" not in text:     # a paste split across reads
                more = os.read(self.fd, 65536)
                if not more:
                    break
                text += more.decode("utf-8", "replace")
            keys = parse_keys(text)
        if not keys:
            return None
        self.pending = keys[1:]
        return keys[0]

    def _read_win(self):
        import msvcrt
        c = msvcrt.getwch()
        if c in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return [{"H": "up", "P": "down", "K": "left", "M": "right", "G": "home", "O": "end", "S": "delete",
                     "\x0f": "shift_tab"}.get(code, "unknown")]
        return parse_keys(c)


def raw_available() -> bool:
    """True when stdin is a real terminal this module can put into raw mode."""
    try:
        return os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        return False
