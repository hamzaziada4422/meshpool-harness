"""A tiny terminal emulator for tests and screenshots: feed it what a program wrote to a terminal, read back the
screen as text or as HTML (colors and attributes kept).

Handles printable and wide characters, \\n (as CR LF), \\r, cursor up/down/forward/back, erase in line/display,
SGR (16/256/24-bit colors, bold, dim, italic, inverse). Everything else (modes like bracketed paste) is ignored.
The screen has no height limit, so scrolling never loses a line.
"""
from __future__ import annotations

import html
import re
import unicodedata

_CSI = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")
BASIC = [(0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16), (36, 114, 200), (188, 63, 188), (17, 168, 205),
         (229, 229, 229), (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67), (59, 142, 234),
         (214, 112, 214), (41, 184, 219), (255, 255, 255)]
DEFAULT_FG, DEFAULT_BG = (229, 229, 229), (30, 30, 30)


def xterm256(n: int):
    if n < 16:
        return BASIC[n]
    if n < 232:
        n -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return steps[n // 36], steps[(n // 6) % 6], steps[n % 6]
    v = 8 + 10 * (n - 232)
    return v, v, v


def width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class Screen:
    def __init__(self, cols: int = 100):
        self.cols = cols
        self.rows: list[list] = [[]]
        self.r = self.c = 0
        self.pending = False
        self.style = (None, None, False, False, False, False)     # fg, bg, bold, dim, italic, inverse

    # -- grid
    def _row(self, r: int) -> list:
        while len(self.rows) <= r:
            self.rows.append([])
        return self.rows[r]

    def _put(self, ch: str):
        w = width(ch)
        if w == 0:
            return
        if self.pending or self.c + w > self.cols:
            self.r, self.c, self.pending = self.r + 1, 0, False
        row = self._row(self.r)
        while len(row) < self.c:
            row.append((" ", (None, None, False, False, False, False)))
        cell = (ch, self.style)
        if len(row) > self.c:
            row[self.c] = cell
        else:
            row.append(cell)
        if w == 2:
            if len(row) > self.c + 1:
                row[self.c + 1] = ("", self.style)
            else:
                row.append(("", self.style))
        self.c += w
        if self.c >= self.cols:
            self.c, self.pending = self.cols - 1, True
            if w == 2:
                self.c = self.cols - 1

    def _sgr(self, params: str):
        fg, bg, bold, dim, ital, inv = self.style
        p = [int(x) if x else 0 for x in params.split(";")] if params else [0]
        i = 0
        while i < len(p):
            n = p[i]
            if n == 0:
                fg, bg, bold, dim, ital, inv = None, None, False, False, False, False
            elif n == 1:
                bold = True
            elif n == 2:
                dim = True
            elif n == 3:
                ital = True
            elif n == 7:
                inv = True
            elif n == 22:
                bold = dim = False
            elif n == 23:
                ital = False
            elif n == 27:
                inv = False
            elif 30 <= n <= 37:
                fg = BASIC[n - 30]
            elif 90 <= n <= 97:
                fg = BASIC[n - 90 + 8]
            elif 40 <= n <= 47:
                bg = BASIC[n - 40]
            elif n == 39:
                fg = None
            elif n == 49:
                bg = None
            elif n in (38, 48):
                if i + 1 < len(p) and p[i + 1] == 2 and i + 4 < len(p):
                    col = (p[i + 2], p[i + 3], p[i + 4])
                    i += 4
                elif i + 2 < len(p) and p[i + 1] == 5:
                    col = xterm256(p[i + 2])
                    i += 2
                else:
                    col = None
                if n == 38:
                    fg = col
                else:
                    bg = col
            i += 1
        self.style = (fg, bg, bold, dim, ital, inv)

    def feed(self, data: str):
        i = 0
        while i < len(data):
            ch = data[i]
            if ch == "\x1b":
                m = _CSI.match(data, i)
                if not m:
                    i += 1
                    continue
                i = m.end()
                params, fin = m.group(1), m.group(2)
                n = int(params) if params.isdigit() else 1
                if fin == "m":
                    self._sgr(params)
                elif fin == "A":
                    self.r, self.pending = max(0, self.r - n), False
                elif fin == "B":
                    self.r, self.pending = self.r + n, False
                elif fin == "C":
                    self.c, self.pending = min(self.cols - 1, self.c + n), False
                elif fin == "D":
                    self.c, self.pending = max(0, self.c - n), False
                elif fin == "G":
                    self.c, self.pending = max(0, n - 1), False
                elif fin == "K":
                    mode = int(params) if params.isdigit() else 0
                    row = self._row(self.r)
                    if mode == 0:
                        del row[self.c:]
                    elif mode == 2:
                        row.clear()
                elif fin == "J":
                    mode = int(params) if params.isdigit() else 0
                    if mode == 0:
                        del self._row(self.r)[self.c:]
                        del self.rows[self.r + 1:]
                    elif mode in (2, 3):
                        self.rows, self.r, self.c = [[]], 0, 0
                elif fin == "H":
                    self.r, self.c, self.pending = 0, 0, False
                continue
            i += 1
            if ch == "\n":
                self.r, self.c, self.pending = self.r + 1, 0, False
                self._row(self.r)
            elif ch == "\r":
                self.c, self.pending = 0, False
            elif ch == "\b":
                self.c = max(0, self.c - 1)
            elif ch == "\t":
                self.c = min(self.cols - 1, (self.c // 8 + 1) * 8)
            elif ch >= " ":
                self._put(ch)

    # -- output
    def lines(self) -> list[str]:
        out = ["".join(c[0] for c in row).rstrip() for row in self.rows]
        while out and not out[-1]:
            out.pop()
        return out

    def text(self) -> str:
        return "\n".join(self.lines())

    def cell_styles(self, row: int):
        return self.rows[row] if row < len(self.rows) else []

    def html(self, title: str = "", font_px: int = 15, pad_rows: int = 0) -> str:
        """A page with the screen drawn in a monospace font on the terminal background."""
        rows = list(self.rows)
        while rows and not rows[-1]:
            rows.pop()
        body = []
        for row in rows:
            cells = list(row) + [(" ", (None, None, False, False, False, False))] * max(0, self.cols - len(row))
            cells = cells[:self.cols]
            line, run, key = [], "", None
            for ch, sty in cells:
                if ch == "":
                    continue
                if sty != key:
                    if run:
                        line.append(self._span(run, key))
                    run, key = "", sty
                run += ch
            if run:
                line.append(self._span(run, key))
            body.append("".join(line))
        css = (f"body{{margin:0;background:rgb{DEFAULT_BG};}}pre{{margin:0;padding:10px 12px;color:rgb{DEFAULT_FG};"
               f"font:{font_px}px/1.3 'DejaVu Sans Mono','Menlo','Consolas',monospace;display:inline-block;}}"
               "span{white-space:pre}")
        return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title><style>{css}</style>"
                f"<pre>{chr(10).join(body)}</pre>")

    @staticmethod
    def _span(text: str, sty) -> str:
        fg, bg, bold, dim, ital, inv = sty
        if inv:
            fg, bg = bg or DEFAULT_BG, fg or DEFAULT_FG
        css = []
        if dim:                                   # a dimmer foreground, not transparency (the fill must stay solid)
            f0, b0 = fg or DEFAULT_FG, bg or DEFAULT_BG
            fg = tuple(int(f0[k] * .62 + b0[k] * .38) for k in range(3))
        if fg:
            css.append(f"color:rgb{fg}")
        if bg:
            css.append(f"background:rgb{bg}")
        if bold:
            css.append("font-weight:700")
        if ital:
            css.append("font-style:italic")
        if bg:                                    # fill the whole line box so stacked rows touch
            css.append("display:inline-block;height:1.3em;vertical-align:top")
        return f'<span style="{";".join(css)}">{html.escape(text)}</span>' if css else html.escape(text)


def render(data: str, cols: int = 100) -> Screen:
    s = Screen(cols)
    s.feed(data)
    return s
