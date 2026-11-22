"""The registry of files Meshpool made: path, checksum and a structure snapshot, so a later edit by the user can be
noticed (teach.py). Only files registered here are ever read for that. Nothing leaves the computer.

<Meshpool data folder>/artifacts/registry.jsonl, one JSON line per registration; the last line for a path is its
baseline. Re-registering a path (Meshpool changed the file again, or the user answered a "teach" card) moves the
baseline forward.

Snapshots are structure only, never content: for a deck the case, length and fonts of titles and which shapes each
slide has; for a workbook fonts, number formats, column widths and sheet names; for a document heading styles and
fonts.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

from meshpool_harness import config as C

KINDS = {".pptx": "pptx", ".xlsx": "xlsx", ".docx": "docx"}
MAX_CELLS = 6000
MAX_ROWS = 4000


def path() -> Path:
    return C.home() / "artifacts" / "registry.jsonl"


def kind_of(p: str | Path) -> str:
    return KINDS.get(Path(str(p)).suffix.lower(), "")


def checksum(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _key(p: str | Path) -> str:
    try:
        return str(Path(str(p)).expanduser().resolve())
    except OSError:
        return str(p)


def _rows() -> list[dict]:
    out = []
    try:
        for line in path().read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(ValueError):
                out.append(json.loads(line))
    except OSError:
        pass
    return out


def baselines() -> dict[str, dict]:
    """path -> its latest registration (files that no longer exist are kept: they just cannot be read)."""
    out: dict = {}
    for r in _rows():
        out[r["path"]] = r
    return out


def get(p: str | Path) -> dict | None:
    return baselines().get(_key(p))


def is_registered(p: str | Path) -> bool:
    return get(p) is not None


def register(p: str | Path, chat_id: str = "", incognito: bool = False) -> dict | None:
    """Record a file Meshpool just made or changed. Returns the row, or None for a file type it does not track,
    a missing file, or an incognito chat (which leaves no trace)."""
    if incognito or not kind_of(p) or not Path(str(p)).is_file():
        return None
    try:
        snap = snapshot(p)
        row = {"path": _key(p), "kind": kind_of(p), "checksum": checksum(p), "snapshot": snap,
               "chat_id": chat_id or "", "ts": round(time.time(), 2)}
        path().parent.mkdir(parents=True, exist_ok=True)
        with path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
    except Exception:  # noqa: BLE001 - registering never breaks the turn that made the file
        return None


def forget(p: str | Path) -> None:
    """Drop a file from the registry (rewrites the log without it)."""
    k = _key(p)
    rows = [r for r in _rows() if r["path"] != k]
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


# ---------- snapshots ------------------------------------------------------------------------------------------------
def case_of(text: str) -> str:
    """"sentence", "title", "upper", "lower" or "" when the text is too short or ambiguous to say."""
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text or "")
    if len(words) < 3:
        return ""
    if text.isupper():
        return "upper"
    if text.islower():
        return "lower"
    rest = words[1:]
    long_rest = [w for w in rest if len(w) > 3]
    if not long_rest:
        return ""
    caps = sum(1 for w in long_rest if w[0].isupper())
    share = caps / len(long_rest)
    if share >= 0.7:
        return "title"
    if share <= 0.25 and words[0][0].isupper():
        return "sentence"
    return ""


def snapshot(p: str | Path) -> dict:
    k = kind_of(p)
    if k == "pptx":
        return _snap_pptx(p)
    if k == "xlsx":
        return _snap_xlsx(p)
    if k == "docx":
        return _snap_docx(p)
    raise ValueError("Not a file type Meshpool tracks.")


def _norm_shape(name: str) -> str:
    return re.sub(r"[\s_-]*\d+$", "", (name or "").strip().lower())


def _snap_pptx(p) -> dict:
    from pptx import Presentation
    prs = Presentation(str(p))
    slides = []
    for s in prs.slides:
        title = ""
        with contextlib.suppress(Exception):
            if s.shapes.title is not None and s.shapes.title.has_text_frame:
                title = s.shapes.title.text_frame.text.strip()
        fonts, colors, shapes, runs = Counter(), Counter(), [], 0
        for sh in s.shapes:
            shapes.append(_norm_shape(sh.name))
            if not getattr(sh, "has_text_frame", False):
                continue
            for para in sh.text_frame.paragraphs:
                for r in para.runs:
                    if not r.text.strip():
                        continue
                    runs += 1
                    if r.font.name:
                        fonts[r.font.name] += 1
                    with contextlib.suppress(Exception):
                        if r.font.color is not None and r.font.color.type is not None and r.font.color.rgb is not None:
                            colors[str(r.font.color.rgb).upper()] += 1
        slides.append({"title": title[:200], "case": case_of(title), "words": len(title.split()),
                       "runs": runs, "fonts": dict(fonts), "colors": dict(colors), "shapes": shapes})
    return {"kind": "pptx", "slides": slides}


def _snap_xlsx(p) -> dict:
    from openpyxl import load_workbook
    wb = load_workbook(str(p), data_only=False)
    sheets = {}
    for ws in wb.worksheets:
        fonts, fmts, cells = Counter(), {}, 0
        for row in ws.iter_rows(max_row=MAX_ROWS):
            for c in row:
                if c.value is None:
                    continue
                cells += 1
                if cells > MAX_CELLS:
                    break
                if c.font is not None and c.font.name:
                    fonts[c.font.name] += 1
                if isinstance(c.value, (int, float)) or (isinstance(c.value, str) and c.value.startswith("=")):
                    fmts[c.coordinate] = c.number_format
            if cells > MAX_CELLS:
                break
        widths = {k: round(float(d.width), 1) for k, d in ws.column_dimensions.items() if d.width}
        sheets[ws.title] = {"fonts": dict(fonts), "formats": fmts, "widths": widths, "cells": cells}
    return {"kind": "xlsx", "sheets": sheets}


def _snap_docx(p) -> dict:
    from docx import Document
    d = Document(str(p))
    heads, body_fonts, runs = [], Counter(), 0
    for para in d.paragraphs:
        st = para.style.name if para.style is not None else ""
        txt = para.text.strip()
        if not txt:
            continue
        fonts = Counter()
        for r in para.runs:
            f = r.font.name or (para.style.font.name if para.style is not None else None)
            if r.text.strip():
                runs += 1
            if f and r.text.strip():
                fonts[f] += 1
        if st.startswith("Heading") or st == "Title":
            heads.append({"style": st, "text": txt[:200], "case": case_of(txt), "words": len(txt.split()),
                          "fonts": dict(fonts)})
        else:
            body_fonts.update(fonts)
    return {"kind": "docx", "headings": heads, "body_fonts": dict(body_fonts), "runs": runs}
