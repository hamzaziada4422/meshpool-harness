"""Pure rendering for the terminal UI: strings in, lines out. No I/O, no clock, no globals, so every piece can be
tested at a fixed width and drawn to a screenshot.

  Style            colors (24-bit, 256 or none) and text attributes
  vlen / wrap      display width and word wrapping that know about ANSI codes and wide characters
  markdown(...)    one source line of an assistant reply -> terminal lines (bold, `code`, lists, fences, tables)
  shell_block      a shell tool call as a filled, full-width block
  tool_line        any other tool call as one compact line
  header, notice_lines, rule, counters, ...   the screen furniture
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata

from meshpool_harness.agent import brand as B

# ---------- colors -------------------------------------------------------------------------------------------------
FILL = (38, 94, 23)            # tool block fill (#265e17)
POOL = B.POOL                  # Meshpool blue (#1670E8)
SKY = B.SKY                    # lighter blue, readable on a dark terminal (#5EA8FF)
CODE = (198, 120, 221)         # inline code (#c678dd)
CMD = (140, 190, 255)          # /commands
YELLOW = (240, 200, 80)
GREEN = (110, 200, 110)
RED = (236, 100, 112)
GRAY = (136, 136, 136)

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class Style:
    """Colors for one terminal. depth: 0 (none), 256, or 24 (24-bit), from brand.depth(). `bg` is a fill color that
    every styled piece re-applies after its reset, so styled text inside a filled block keeps the fill."""

    def __init__(self, depth: int, bg=None):
        self.depth = depth
        self.on = bool(depth)
        self.bg = bg

    def _w(self, code: str, s: str) -> str:
        if not self.on or not s:
            return s
        return f"\x1b[{code}m{s}\x1b[0m" + (B.bg(self.bg, self.depth) if self.bg else "")

    def rgb(self, rgb, s: str, bold: bool = False) -> str:
        if not self.on or not s:
            return s
        return ("\x1b[1m" if bold else "") + B.fg(rgb, self.depth) + s + "\x1b[0m" + \
            (B.bg(self.bg, self.depth) if self.bg else "")

    def on_bg(self, bg) -> Style:
        return Style(self.depth, bg)

    # semantic colors
    def blue(self, s): return self.rgb(POOL if self.depth == 24 else SKY, s)      # kept: the old prompt accent
    def accent(self, s): return self.rgb(SKY, s)
    def amber(self, s): return self.rgb(YELLOW, s)
    yellow = amber
    def red(self, s): return self.rgb(RED, s)
    def green(self, s): return self.rgb(GREEN, s)
    def cmd(self, s): return self.rgb(CMD, s)
    def code(self, s): return self.rgb(CODE, s)
    def gray(self, s): return self.rgb(GRAY, s)
    def dim(self, s): return self._w("2", s)
    def bold(self, s): return self._w("1", s)
    def italic(self, s): return self._w("3", s)
    def inverse(self, s): return self._w("7", s)


# ---------- width --------------------------------------------------------------------------------------------------

_UNSAFE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[PX^_][^\x1b]*(?:\x1b\\)?|\x1b\[[0-?]*[ -/]*[@-~]?|"
                     r"\x1b[ -/]*[0-~]?|[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe_text(s: str) -> str:
    """Text from the model or a tool for the terminal: no escape sequences (clipboard writes, screen clears, titles,
    links) and no control characters except new line and tab."""
    return _UNSAFE.sub("", s) if isinstance(s, str) else s


def strip(s: str) -> str:
    return ANSI.sub("", s)


def cw(ch: str) -> int:
    """Display columns of one character."""
    if unicodedata.combining(ch) or ch in "\u200b\u200d\ufe0f":
        return 0
    # Marks that sit on the letter before them take no column even when their combining class is 0 (Thai and
    # Devanagari vowel signs), and neither do format characters (the Persian zero-width non-joiner, direction
    # marks) or Hangul medial vowels. The soft hyphen is the one format character a terminal draws.
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Me") or (cat == "Cf" and ch != "\u00ad") or "\u1160" <= ch <= "\u11ff":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def vlen(s: str) -> int:
    s = strip(s)
    if s.isascii():                     # every ASCII character is one column (cw): no per-character lookups
        return len(s)
    return sum(cw(c) for c in s)


def clip(s: str, width: int, ell: str = "…") -> str:
    """Cut an ANSI string to `width` columns, keeping its codes."""
    if vlen(s) <= width:
        return s
    out, n, i = [], 0, 0
    limit = max(0, width - vlen(ell))
    while i < len(s):
        m = ANSI.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        w = cw(s[i])
        if n + w > limit:
            break
        out.append(s[i])
        n += w
        i += 1
    return "".join(out) + ell + ("\x1b[0m" if "\x1b[" in s else "")


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - vlen(s))


def right(left: str, rt: str, width: int) -> str:
    """`left` at the left edge and `rt` at the right edge of a `width`-column line (right wins when they collide)."""
    gap = width - vlen(left) - vlen(rt)
    if gap < 1:
        left = clip(left, max(0, width - vlen(rt) - 1))
        gap = max(1, width - vlen(left) - vlen(rt))
    return left + " " * gap + rt


def hard_wrap(s: str, width: int) -> list[str]:
    """Plain text split at `width` columns (for code and other text that must not reflow)."""
    out, cur, n = [], "", 0
    for ch in s:
        w = cw(ch)
        if n + w > width and cur:
            out.append(cur)
            cur, n = "", 0
        cur += ch
        n += w
    return out + [cur]


def word_wrap(s: str, width: int) -> list[int]:
    """Where each row starts when `s` is word-wrapped at `width` columns (greedy: a row breaks before the word that
    does not fit; a word wider than a row is cut). Rows before the last never change as text is added, so a streamed
    paragraph is wrapped from the start of its last row only (OM-08)."""
    width = max(1, width)
    starts, n, i, L = [0], 0, 0, len(s)
    while i < L:
        j = i
        while j < L and s[j] != " ":
            j += 1
        word = s[i:j]
        ww = vlen(word)
        if n and n + ww > width:                   # the word goes to a new row
            starts.append(i)
            n = 0
        if ww > width:                             # longer than a row: cut it
            for k, ch in enumerate(word):
                c = cw(ch)
                if n + c > width and n:
                    starts.append(i + k)
                    n = 0
                n += c
        else:
            n += ww
        while j < L and s[j] == " ":
            j += 1
            n += 1
        i = j
    return starts


def exec_output(out: str) -> str:
    """The output of an exec_command result without the lines meant for the model (Chunk ID, Wall time, Process
    exited, Original token count, Output:): the screen shows the command's own output (OM-08)."""
    m = re.match(r"Chunk ID: [0-9a-f]+\nWall time: [^\n]*\n(?:Process exited with code -?\d+\n)?"
                 r"(?:Original token count: \d+\n)?Output:\n?", out or "")
    return out[m.end():] if m else out


# ---------- markdown -----------------------------------------------------------------------------------------------
_INLINE = re.compile(
    r"(?P<code>(`+)(?P<c>.+?)\2)|(?P<b>\*\*(?P<bt>.+?)\*\*)|(?P<b2>__(?P<b2t>.+?)__)|"
    r"(?P<i>(?<![\w*])\*(?!\s)(?P<it>[^*]+?)(?<!\s)\*(?![\w*]))|(?P<link>\[(?P<lt>[^\]]+)\]\((?P<lu>[^)\s]+)\))")


def inline_tokens(text: str) -> list[tuple[str, str]]:
    """[(text, kind)] with kind in plain, code, bold, italic, url."""
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append((_arrows(text[pos:m.start()]), "plain"))
        if m.group("code"):
            out.append((m.group("c").strip() if m.group("c").strip() else m.group("c"), "code"))
        elif m.group("b"):
            out.extend(_bold(m.group("bt")))
        elif m.group("b2"):
            out.extend(_bold(m.group("b2t")))
        elif m.group("i"):
            out.append((_arrows(m.group("it")), "italic"))
        else:
            out.append((m.group("lt"), "plain"))
            out.append((f"({m.group('lu')})", "url"))
        pos = m.end()
    if pos < len(text):
        out.append((_arrows(text[pos:]), "plain"))
    return out


def _bold(inner: str) -> list[tuple[str, str]]:
    """Bold text may hold `code`: the code keeps its color."""
    return [(t, "bold" if k == "plain" else k) for t, k in inline_tokens(inner)]


def _arrows(s: str) -> str:
    return s.replace(" -> ", " → ").replace(" => ", " ⇒ ")


def _style_word(st: Style, word: str, kind: str) -> str:
    if kind == "code":
        return st.code(word)
    if kind == "bold":
        return st.bold(word)
    if kind == "italic":
        return st.italic(word)
    if kind == "url":
        return st.dim(word)
    return word


def wrap_inline(text: str, width: int, st: Style, first: str = "", rest: str = "") -> list[str]:
    """Inline markdown wrapped to `width`: `first` starts the first line, `rest` every later one (a hanging indent)."""
    words = []                                   # (display text, styled text, joined-to-previous)
    prev_space = True
    for tok, kind in inline_tokens(text):
        parts = re.split(r"( +)", tok) if kind != "code" else [tok]
        for part in parts:
            if part == "":
                continue
            if part.strip() == "" and kind != "code":
                prev_space = True
                continue
            for k, piece in enumerate(hard_wrap(part, max(8, width - vlen(rest) - 1)) if vlen(part) > max(8, width - vlen(rest) - 1) else [part]):
                words.append((piece, _style_word(st, piece, kind), (not prev_space) and k == 0 or k > 0))
            prev_space = False
    lines, cur, n = [], first, vlen(first)
    started = False
    for plain, styled, joined in words:
        w = vlen(plain)
        sep = 0 if (joined or not started) else 1
        if started and n + sep + w > width:
            lines.append(cur)
            cur, n, started, sep = rest, vlen(rest), False, 0
        cur += (" " * sep) + styled
        n += sep + w
        started = True
    lines.append(cur)
    return lines


class MdState:
    def __init__(self):
        self.fence = ""            # the opening fence while inside a code block
        self.lang = ""


def markdown(line: str, width: int, st: Style, state: MdState) -> list[str]:
    """One source line of markdown -> terminal lines. Headings become bold run-in text, bullets become hyphens with a
    two-column hanging indent, code blocks are indented, tables keep their cells."""
    width = max(20, width)
    raw = line.rstrip("\n")
    m = re.match(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)", raw)
    if m and (not state.fence or raw.strip().startswith(state.fence[:3])):
        if state.fence:
            state.fence = ""
            return [st.dim("  " + "─" * 8)] if st.on else []
        state.fence, state.lang = m.group(1), m.group(2)
        return [st.dim("  " + state.lang)] if state.lang else []
    if state.fence:
        return ["  " + seg for seg in hard_wrap(raw.expandtabs(4), width - 3)]
    if not raw.strip():
        return [""]
    h = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", raw)
    if h:
        return wrap_inline("**" + h.group(1) + "**", width, st) if "**" not in h.group(1) else wrap_inline(h.group(1), width, st)
    if re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", raw):
        return [st.dim("─" * min(width, 40))]
    q = re.match(r"^\s{0,3}>\s?(.*)$", raw)
    if q:
        return [st.dim("│ ") + ln for ln in wrap_inline(q.group(1), width - 2, st)]
    li = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", raw)
    if li:
        indent = " " * min(8, len(li.group(1).expandtabs(2)))
        bullet = "-" if li.group(2) in "-*+" else li.group(2)
        lead = f"{indent}{bullet} "
        return wrap_inline(li.group(3), width, st, first=lead if not st.on else indent + st.dim(bullet) + " ",
                           rest=" " * len(lead))
    if raw.lstrip().startswith("|") and raw.rstrip().endswith("|"):
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            return []
        return [clip("  ".join(_cell(st, c) for c in cells), width)]
    return wrap_inline(raw.strip() if not raw.startswith("    ") else raw, width, st)


def _cell(st: Style, c: str) -> str:
    return "".join(_style_word(st, t, k) for t, k in inline_tokens(c))


# ---------- tool blocks --------------------------------------------------------------------------------------------

def fmt_took(seconds: float) -> str:
    return f"Took {seconds:.1f}s"


def collapse(lines: list[str], limit: int) -> tuple[list[str], int]:
    """(lines to show, number hidden): long output is cut from the top, the tail stays."""
    if limit and len(lines) > limit:
        return lines[-limit:], len(lines) - limit
    return lines, 0


def receipt_line(ln: str, st: Style, width: int) -> str:
    """One line of the turn's receipt ("**Changed:** a.py (+1 -0)") for a terminal: no literal asterisks. The label is
    bold when the terminal has color and plain text when it has not (-p to a file or a pipe); the rest is dim."""
    m = re.match(r"\s*\*\*([^*]+?)\*\*\s*(.*)$", ln)
    if not m:
        return st.dim(clip(ln.replace("**", ""), width))
    label, rest = m.group(1), m.group(2)
    rest = clip(rest.replace("**", ""), max(10, width - len(label) - 1))
    return st.bold(label) + " " + st.dim(rest)


def guard(fn, st: Style, *a, **k):
    """OSS-12: a renderer that throws must not end the session: show a small placeholder instead."""
    try:
        return fn(*a, **k)
    except Exception:  # noqa: BLE001
        msg = ["  " + st.dim("Could not show this item.")]
        return (msg, 0) if fn is shell_block else msg


def shell_block(command: str, output: str, took: float | None, st: Style, width: int, meta: str = "",
                limit: int = 10, ok: bool = True, expand: bool = True) -> tuple[list[str], int]:
    """A shell call as a full-width block: bold `$ command` (with dim metadata at the end of the last line), a blank
    line, the output (dim), a blank line, `Took 2.6s`. Returns (lines, hidden output line count). expand: say that
    Ctrl+O shows the hidden lines (not in a -p run, where no key can be pressed)."""
    inner = max(4, width - 2)
    fill = st.on_bg(FILL) if st.on else st
    cmd_lines = []
    first = True
    for src in command.rstrip().splitlines() or [""]:
        text = ("$ " if first else "") + src
        first = False
        cmd_lines.extend(hard_wrap(text, inner))
    body = [fill.bold(c) for c in cmd_lines]
    if meta:
        tail = " " + meta
        if vlen(strip(body[-1])) + len(tail) <= inner:
            body[-1] += fill.dim(tail)
        else:
            body.append(fill.dim(meta))
    out_lines = [ln.expandtabs(4) for ln in output.rstrip().splitlines()] if output.strip() else []
    shown, hidden = collapse(out_lines, limit)
    rows = list(body)
    if shown or hidden:
        rows.append("")
        if hidden:
            rows.append(fill.dim(f"… ({hidden} earlier line{'s' if hidden != 1 else ''}"
                                 + (", ctrl+o to expand)" if expand else ")")))
        for ln in shown:
            for seg in hard_wrap(ln, inner):
                rows.append(fill.dim(seg) if ok else fill.red(seg))
    if took is not None:
        rows += ["", fill.dim(fmt_took(took))]
    pad_row = ""
    rows = [clip(r, max(1, width - 2)) for r in rows]          # a very narrow window: never wider than the screen
    rows = [pad_row] + rows + [pad_row]
    if not st.on:                                 # no color: no fill, the block is set off by blank lines and indent
        return [""] + ["  " + strip(r) if r else "" for r in rows[1:-1]] + [""], hidden
    bgc = B.bg(FILL, st.depth)
    return [""] + [bgc + pad(" " + r, width) + "\x1b[0m" if r else bgc + " " * width + "\x1b[0m" for r in rows] + [""], hidden


TOOL_LABEL = {"read_file": "Read", "write_file": "Write", "edit": "Edit", "grep": "Search", "glob": "Find",
              "web_search": "Web search", "web_research": "Research", "fetch_url": "Fetch", "office_task": "Office",
              "diagnostics": "Check", "use_skill": "Skill", "find_skill": "Find skill", "delegate_task": "Delegate"}
SHELL_TOOLS = {"bash", "bash_start", "bash_many", "terminal", "shell", "exec_command"}


def diff_counts(diff: str) -> tuple[int, int]:
    add = rem = 0
    for ln in (diff or "").splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            add += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            rem += 1
    return add, rem


def tool_label(name: str, mcp: bool = False) -> str:
    if mcp:
        parts = name.split("__")
        return f"{parts[-1]} ({parts[1]})" if len(parts) >= 3 else name
    return TOOL_LABEL.get(name, name)


def tool_line(name: str, summary: str, output: str, ok: bool, took: float | None, st: Style, width: int,
              diff: str = "", mcp: bool = False) -> list[str]:
    """One compact line for a non-shell tool: `● Edit  path  +3 -1  Took 0.0s`; a failure adds its first message line."""
    label = tool_label(name, mcp)
    lines_out = [ln for ln in (output or "").splitlines() if ln.strip()]
    detail = ""
    if name == "edit" or (name == "write_file" and diff):
        a, r = diff_counts(diff)
        if a or r:
            detail = st.green(f"+{a}") + " " + st.red(f"-{r}")
    elif name == "read_file":
        n = len((output or "").splitlines())
        detail = st.dim(f"{n} line{'s' if n != 1 else ''}") if ok and n else ""
    elif name in ("grep", "glob"):
        n = len(lines_out)
        detail = st.dim("no matches" if not n else f"{n} match{'es' if n != 1 else ''}") if ok else ""
    elif name in ("web_search", "web_research", "fetch_url") and ok and lines_out:
        detail = st.dim(f"{len(lines_out)} lines")
    mark = st.green("●") if ok else st.red("●")
    tail = ("  " + st.dim(fmt_took(took))) if took is not None else ""
    head = f"{mark} {st.bold(label)}"
    room = width - vlen(head) - vlen(tail) - (vlen(detail) + 2 if detail else 0) - 3
    target = clip(summary.replace("\n", " "), max(10, room))
    line = head + "  " + target + (("  " + detail) if detail else "") + tail
    out = [clip(line, width)]
    if not ok and lines_out:
        out.append("  " + st.red(clip(lines_out[0], width - 4)))
    return out


# ---------- screen furniture ---------------------------------------------------------------------------------------

def logo(depth: int) -> list[str]:
    """The Meshpool mark in three terminal rows (six pixel rows, 11 columns): the blue ribbon M of brand/mark.png,
    sampled down and drawn with half blocks."""
    src = B.MARK
    cols, prow = 11, 6
    w, h = len(src[0]) // 6, len(src)
    px = []
    for y in range(prow):
        row = []
        for x in range(cols):
            hit = None
            for yy in range(int(y * h / prow), max(int(y * h / prow) + 1, int((y + 1) * h / prow))):
                for xx in range(int(x * w / cols), max(int(x * w / cols) + 1, int((x + 1) * w / cols))):
                    c = src[yy][xx * 6:xx * 6 + 6]
                    if c != "......" and hit is None:
                        hit = B._rgb(c)
            row.append(hit)
        px.append(row)
    out = []
    for y in range(0, prow, 2):
        line = ""
        for x in range(cols):
            t, b = px[y][x], px[y + 1][x]
            if t is None and b is None:
                line += " "
            elif not depth:
                line += "█" if t and b else ("▀" if t else "▄")
            elif b is None:
                line += B.fg(t, depth) + "▀\x1b[0m"
            elif t is None:
                line += B.fg(b, depth) + "▄\x1b[0m"
            else:
                line += B.fg(t, depth) + B.bg(b, depth) + "▀\x1b[0m"
        out.append(line)
    return out


def header(st: Style, width: int, version: str, model: str, provider: str, cwd: str) -> list[str]:
    """Mark on the left, three lines of text beside it: name and version, model and provider, folder."""
    text = [st.bold("Meshpool") + " " + st.dim("v" + version), st.dim(f"{model} · {provider}"), st.dim(cwd)]
    if width < 40:
        return [clip(t, width) for t in text]
    mark = logo(st.depth)
    return ["  " + mark[i] + "  " + clip(text[i], max(10, width - 17)) for i in range(3)]


def notice_lines(st: Style, width: int, notices: list[str], show: int = 1) -> list[str]:
    out = []
    for n in notices[:show]:
        out.extend(wrap_inline(n, width - 2, Style(0), first="  ", rest="  "))
    out = [_hilite_cmds(st, ln) for ln in out]
    hidden = len(notices) - show
    if hidden > 0:
        out.append("  " + st.dim(f"{hidden} more notice{'s' if hidden != 1 else ''} hidden"))
    return out


def _hilite_cmds(st: Style, line: str) -> str:
    return re.sub(r"(?<![\w/.])/[a-z][a-z-]*", lambda m: st.cmd(m.group(0)), line)


def rule(st: Style, width: int, color=None) -> str:
    bar = "─" * width
    return st.rgb(color, bar) if (color and st.on) else (st.dim(bar) if st.on else bar)


def status_right(st: Style, width: int, items: list[tuple[str, str]]) -> str:
    """Right-aligned notices above the input box, ` · ` separated. items: (kind, text); kind is err, ok or dim."""
    if not items:
        return ""
    paint = {"err": st.red, "ok": st.green, "dim": st.dim, "warn": st.amber}
    parts = [paint.get(k, st.dim)(t) for k, t in items]
    s = st.dim(" · ").join(parts)
    return " " * max(0, width - 2 - vlen(s)) + s


def human(n: float) -> str:
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            v = n / div
            return f"{v:.1f}{unit}" if v < 100 else f"{v:.0f}{unit}"
    return str(int(n))


MODE_NAMES = {"ask": "ask", "edits": "accept edits", "auto": "full access"}


def mode_hint(st: Style, mode: str, agents: int = 0) -> str:
    from meshpool_harness import config as C
    if mode in C.MODE_INFO:                  # the web app's words (config.MODE_INFO), one line each
        seg = ("" if mode == "ask" else "⏵⏵ ") + f"{C.MODE_INFO[mode][0]}: {C.MODE_FOOTER[mode]}"
    else:
        seg = mode
    s = " " + st.amber(seg) + st.dim(" (shift+tab to cycle)")
    if agents:
        s += st.dim(f" · {agents} background agent{'s' if agents != 1 else ''}")
    return s


def counters(st: Style, width: int, u: dict, ctx_tokens: int, window: int, mode: str, model: str, effort: str = "",
             cost: float | None = None) -> str:
    """`↑in ↓out R cached CH hit% $cost ctx%/window (mode)` on the left, `model • effort` on the right. The context
    share turns yellow above 80%."""
    fresh = max(0, u.get("in", 0) - u.get("cached", 0))
    left = []
    if u.get("calls"):
        hit = round(100 * u["cached"] / u["in"], 1) if u.get("in") else 0
        left += [f"↑{human(fresh)}", f"↓{human(u.get('out', 0))}"]
        if width >= 72:
            left += [f"R{human(u['cached'])}", f"CH{hit:g}%"]
        if cost is not None:
            left.append(f"${cost:.3f}")
        if window:
            pct = 100 * ctx_tokens / window
            seg = f"{pct:.1f}%/{human(window)}"
            left.append(st.amber(seg) if pct > 80 else seg)
    plain = [x if "\x1b" in x else st.dim(x) for x in left]
    mode_txt = st.dim(f"({mode})")
    l = (" ".join(plain) + " " if plain else "") + mode_txt
    r = st.dim(model + (f" • {effort}" if effort else ""))
    return right(l, r, width) if width >= vlen(l) + vlen(r) + 2 else l


def spinner_line(st: Style, frame: int, label: str, secs: int, hint: str = "ctrl+c to interrupt") -> str:
    glyph = "·✢✳✶✻✽"[frame % 6]
    return st.accent(glyph) + " " + label + st.dim(f"… ({secs}s · {hint})")


def slash_menu(st: Style, width: int, rows: list[tuple[str, str, str]], sel: int) -> list[str]:
    """Filtered command list under the input box: (name, argument hint, description), the selected row highlighted."""
    out = []
    namew = max(len(n) + (len(a) + 1 if a else 0) for n, a, _ in rows) + 2 if rows else 0
    for i, (n, a, d) in enumerate(rows):
        label = "/" + n + (" " + a if a else "")
        line = "  " + pad(label, namew + 1) + d
        line = clip(line, width)
        out.append(st.accent(line) if i == sel else st.dim(line))
    return out


def choice_lines(st: Style, width: int, title: str, detail: list[str], options: list[str], sel: int,
                 hint: str = "") -> list[str]:
    """A compact inline question: title, optional detail lines, numbered choices with the selected one marked."""
    out = [st.amber(clip(title, width - 2))]
    out += ["  " + clip(d, width - 4) for d in detail]
    for i, o in enumerate(options):
        mark = st.accent("❯") if i == sel else " "
        label = f"{i + 1}. {o}"
        out.append(f" {mark} " + (st.accent(label) if i == sel else label))
    if hint:
        out.append(st.dim("  " + hint))
    return [clip(x, width) for x in out]


def context_window(model: str, known: dict | None = None) -> int:
    """Context size in tokens: MH_CONTEXT_WINDOW, a size the endpoint reported for this model, a size in the name
    (`1m`, `200k`), else a stated default of 128k."""
    try:
        if os.environ.get("MH_CONTEXT_WINDOW"):
            return int(float(os.environ["MH_CONTEXT_WINDOW"]))
    except ValueError:
        pass
    bare = (model or "").split(":", 1)[-1]
    if known and known.get(bare):
        return int(known[bare])
    m = re.search(r"(\d+(?:\.\d+)?)([mk])\b", bare.lower())
    if m:
        return int(float(m.group(1)) * (1_000_000 if m.group(2) == "m" else 1_000))
    return 128_000


# ---- OSS-30: a console that cannot show the interface's glyphs (Windows cp1252, a redirected stdout) -------------------
GLYPHS = "❯…─│·•✓✗↑↓✢✳✶✻✽"
_ASCII = {"❯": ">", "…": "...", "─": "-", "│": "|", "·": "-", "•": "*", "✓": "v", "✗": "x", "↑": "^", "↓": "v",
          "✢": "*", "✳": "*", "✶": "*", "✻": "*", "✽": "*", "—": "-", "–": "-", "“": '"', "”": '"', "’": "'"}
ASCII = [False]                      # True: this terminal shows the plain-text look


def prepare_streams(streams=None) -> bool:
    """Called once at CLI start. A stream whose encoding cannot write the interface's glyphs is switched to UTF-8 with
    errors="replace" (so nothing can crash on a character), and the interface falls back to ASCII glyphs. Returns True
    when every stream could already show them."""
    ok = True
    for st in streams or (sys.stdout, sys.stderr):
        try:
            GLYPHS.encode(getattr(st, "encoding", None) or "ascii")
            continue
        except (UnicodeEncodeError, LookupError):
            ok = False
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    ASCII[0] = not ok
    return ok


def safe(text: str) -> str:
    """The text, with plain-ASCII stand-ins for the glyphs when this terminal cannot show them."""
    return text.translate(str.maketrans(_ASCII)) if ASCII[0] else text
