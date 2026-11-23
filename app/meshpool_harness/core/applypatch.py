"""Codex's apply_patch: the patch parser and the line matcher, in Python, for Meshpool's "codex" engine style.

Derived from OpenAI Codex CLI (https://github.com/openai/codex), codex-rs/apply-patch/src/{parser,streaming_parser,
seek_sequence,file_update,text_file}.rs at commit 097558c1bdfc6318cb0ce80a64816ca4d61c7814. Copyright 2025 OpenAI,
Apache License 2.0 (app/defaults/engines/codex/LICENSE). Re-implemented in Python by Meshpool; error texts follow the
upstream ones so the model sees what it saw with Codex.

This module is pure: it turns a patch into hunks and a file's text plus the hunks' chunks into the new text. Reading
and writing files (all-or-nothing, syntax check, bookkeeping) is Workspace.apply_patch in core/tools.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BEGIN = "*** Begin Patch"
END = "*** End Patch"
ADD = "*** Add File: "
DELETE = "*** Delete File: "
UPDATE = "*** Update File: "
MOVE = "*** Move to: "
EOF_MARK = "*** End of File"
CTX = "@@ "
CTX_EMPTY = "@@"


class PatchError(Exception):
    """A patch that cannot be parsed or applied; str(e) is the text the model sees."""


@dataclass
class Chunk:
    ctx: str | None = None                       # the "@@ <line>" header: a line that narrows where the change is
    old: list = field(default_factory=list)
    new: list = field(default_factory=list)
    ctx_idx: list = field(default_factory=list)  # (index in old, index in new) of lines parsed as context
    eof: bool = False                            # "*** End of File": old must sit at the end of the file

    def context(self, line: str) -> None:
        self.ctx_idx.append((len(self.old), len(self.new)))
        self.old.append(line)
        self.new.append(line)


@dataclass
class Hunk:
    kind: str                                    # "add" | "delete" | "update"
    path: str
    contents: str = ""                           # add
    move: str | None = None                      # update: "*** Move to:"
    chunks: list = field(default_factory=list)   # update


def _bad_hunk(msg: str, n: int) -> PatchError:
    return PatchError(f"Invalid patch hunk on line {n}, {msg}")


def split_patch(text: str) -> list[str]:
    """The patch's lines between (and including) the markers. Lenient like upstream: surrounding blank space is
    ignored, and a heredoc wrapper (<<EOF ... EOF) around the patch is removed."""
    lines = text.strip().splitlines()
    def strict(ls):
        first = ls[0].strip() if ls else None
        last = ls[-1].strip() if ls else None
        if first == BEGIN and last == END:
            return ls
        if first != BEGIN:
            raise PatchError("Invalid patch: The first line of the patch must be '*** Begin Patch'")
        raise PatchError("Invalid patch: The last line of the patch must be '*** End Patch'")
    try:
        return strict(lines)
    except PatchError as first_err:
        if (len(lines) >= 4 and lines[0] in ("<<EOF", "<<'EOF'", '<<"EOF"') and lines[-1].endswith("EOF")):
            return strict(lines[1:-1])
        raise first_err


def parse(text: str) -> list[Hunk]:
    lines = split_patch(text)
    hunks: list[Hunk] = []
    mode, hunk_line = "start", 0
    ended = False

    def empty_check(line: str, n: int) -> None:
        if not hunks or hunks[-1].kind != "update":
            return
        h = hunks[-1]
        if not h.chunks and mode == "update":
            raise _bad_hunk(f"Update file hunk for path '{h.path}' is empty", hunk_line)
        if h.chunks and not h.chunks[-1].old and not h.chunks[-1].new:
            if line == END:
                raise _bad_hunk("Update hunk does not contain any lines", n)
            raise _bad_hunk(f"Unexpected line found in update hunk: '{line}'. Every line should start with ' ' "
                            "(context line), '+' (added line), or '-' (removed line)", n)

    def header(trimmed: str, n: int) -> bool:
        nonlocal mode, hunk_line, ended
        if trimmed == END:
            empty_check(trimmed, n)
            ended = True
            return True
        for mark, kind in ((ADD, "add"), (DELETE, "delete"), (UPDATE, "update")):
            if trimmed.startswith(mark):
                empty_check(trimmed, n)
                hunks.append(Hunk(kind, trimmed[len(mark):]))
                mode = kind
                if kind == "update":
                    hunk_line = n
                return True
        return False

    for n, raw in enumerate(lines, 1):
        line = raw[:-1] if raw.endswith("\r") else raw
        trimmed = line.strip()
        if ended:
            break
        if n == 1:
            mode = "started"
            continue
        if mode in ("started", "delete") or (mode == "add" and not line.startswith("+")):
            if header(trimmed, n):
                continue
            raise _bad_hunk(f"'{trimmed}' is not a valid hunk header. Valid hunk headers: '*** Add File: {{path}}', "
                            "'*** Delete File: {path}', '*** Update File: {path}'", n)
        if mode == "add":
            if header(trimmed, n):          # "+*** End Patch" is content, a bare marker line is handled above
                continue
            hunks[-1].contents += line[1:] + "\n"
            continue
        # update
        upd = line.rstrip()
        if header(upd, n):
            continue
        h = hunks[-1]
        chunks = h.chunks
        if chunks and chunks[-1].eof:
            if upd == "":
                continue
            if upd != CTX_EMPTY and not upd.startswith(CTX):
                raise _bad_hunk(f"Expected update hunk to start with a @@ context marker, got: '{line}'", n)
        if not chunks and h.move is None and upd.startswith(MOVE):
            h.move = upd[len(MOVE):]
            continue
        if (upd == CTX_EMPTY or upd.startswith(CTX)) and chunks and not chunks[-1].old and not chunks[-1].new:
            raise _bad_hunk(f"Unexpected line found in update hunk: '{line}'. Every line should start with ' ' "
                            "(context line), '+' (added line), or '-' (removed line)", n)
        if upd == CTX_EMPTY:
            chunks.append(Chunk())
            continue
        if upd.startswith(CTX):
            chunks.append(Chunk(ctx=upd[len(CTX):]))
            continue
        if upd == EOF_MARK:
            if chunks and not chunks[-1].old and not chunks[-1].new or not chunks:
                raise _bad_hunk("Update hunk does not contain any lines", n)
            chunks[-1].eof = True
            continue
        if line == "":
            if not chunks:
                chunks.append(Chunk())
            chunks[-1].context("")
            continue
        tag, rest = line[0], line[1:]
        if tag not in " +-":
            raise _bad_hunk(f"Unexpected line found in update hunk: '{line}'. Every line should start with ' ' "
                            "(context line), '+' (added line), or '-' (removed line)", n)
        if not chunks:
            chunks.append(Chunk())
        c = chunks[-1]
        if tag == " ":
            c.context(rest)
        elif tag == "+":
            c.new.append(rest)
        else:
            c.old.append(rest)
    if not ended:
        raise PatchError("Invalid patch: The last line of the patch must be '*** End Patch'")
    if not hunks:
        raise PatchError("Invalid patch: no file operations. Start each with '*** Add File: ', '*** Delete File: ' "
                         "or '*** Update File: '")
    return hunks


# ---------- matching -------------------------------------------------------------------------------------------
_PUNCT = {c: "-" for c in "‐‑‒–—―−"}
_PUNCT.update({c: "'" for c in "‘’‚‛"})
_PUNCT.update({c: '"' for c in "“”„‟"})
_PUNCT.update({c: " " for c in "            　"})


def _norm(s: str) -> str:
    return s.strip().translate(str.maketrans(_PUNCT))


def seek_sequence(lines: list, pattern: list, start: int, eof: bool) -> int | None:
    """Where `pattern` (a run of lines) sits in `lines` at or after `start`, matched with decreasing strictness:
    exact, ignoring trailing whitespace, ignoring surrounding whitespace, then also folding typographic dashes,
    quotes and spaces to ASCII. eof: try the end of the file first. None when it is not there."""
    if not pattern:
        return start
    if len(pattern) > len(lines):
        return None
    first = max(len(lines) - len(pattern), start) if eof else start
    last = len(lines) - len(pattern)
    for key in (lambda s: s, str.rstrip, str.strip, _norm):
        pat = [key(p) for p in pattern]
        for i in range(first, last + 1):
            if all(key(lines[i + k]) == pat[k] for k in range(len(pat))):
                return i
    return None


def compute_replacements(lines: list, path: str, chunks: list) -> list:
    reps: list = []
    idx = 0
    for ch in chunks:
        if ch.ctx is not None:
            at = seek_sequence(lines, [ch.ctx], idx, False)
            if at is None:
                raise PatchError(f"Failed to find context '{ch.ctx}' in {path}")
            idx = at + 1
        if not ch.old:
            reps.append((len(lines), 0, list(ch.new)))       # nothing to match: the lines go at the end of the file
            continue
        pat, new = ch.old, ch.new
        found = seek_sequence(lines, pat, idx, ch.eof)
        if found is None and pat and pat[-1] == "":
            pat = pat[:-1]                                     # the final newline of the region
            if new and new[-1] == "":
                new = new[:-1]
            found = seek_sequence(lines, pat, idx, ch.eof)
        if found is None:
            raise PatchError(f"Failed to find expected lines in {path}:\n" + "\n".join(ch.old))
        o = n = 0                                              # context lines stay in the file as they are
        for oc, nc in ch.ctx_idx:
            if oc >= len(pat) or nc >= len(new):
                break
            if o != oc or n != nc:
                reps.append((found + o, oc - o, new[n:nc]))
            o, n = oc + 1, nc + 1
        if o != len(pat) or n != len(new):
            reps.append((found + o, len(pat) - o, new[n:]))
        idx = found + len(pat)
    reps.sort(key=lambda r: r[0])
    return reps


_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")


def apply_update(original: str, path: str, chunks: list) -> str:
    """The file's new text. Each line keeps its own ending (LF, CRLF or CR); inserted lines use the first ending the
    file has; every line of the result ends with one, as upstream does."""
    src = []                                       # (text, ending)
    for m in _LINE.findall(original):
        t = m.rstrip("\r\n")
        src.append((t, m[len(t):]))
    pref = next((e for _, e in src if e), "\n")
    reps = compute_replacements([t for t, _ in src], path, chunks)
    out, pos = [], 0
    for start, old_len, new in reps:
        if start < pos:
            raise PatchError(f"Failed to find expected lines in {path}: two changes overlap")
        out += src[pos:start]
        out += [(t, pref) for t in new]
        pos = start + old_len
    out += src[pos:]
    return "".join(t + (e or pref) for t, e in out)
