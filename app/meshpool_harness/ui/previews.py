"""In-app previews of the files a chat makes (the viewer pane in preview.js), behind GET /api/preview.

  Office files (.pptx .docx .doc .odt .odp .xls ...)  LibreOffice (the bundled copy) -> PDF -> PNG pages
  .pdf                                                 PNG pages straight from pypdfium2
  .xlsx .xlsm .csv .tsv                                rows and columns (a table per sheet; the page escapes them)
  .html .md .txt, code                                 the text
  images and .svg                                      the page fetches the bytes from /raw

Every file is read through the same root check as /api/file (W.check_root + W._inside), so nothing outside a project
or a chat's own folder is ever read. A conversion runs on one background thread at a time (LibreOffice is heavy),
never on the request thread: the page polls "working" until the PDF is cached. Pages are rendered on demand and
cached under <home>/previews/<key>/ where key covers path, modified time and size, so an edited file is rendered anew.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness.docread import ole_kind
from meshpool_harness.ui import workbench as W

MAX_PAGES = 60              # pages offered for one file
MAX_WIDTH = 1600            # px of a full page image
MIN_WIDTH = 120
MAX_ROWS, MAX_COLS, MAX_SHEETS = 500, 50, 30
MAX_TEXT = 2_000_000        # bytes of text sent for html, markdown and code
CONVERT_TIMEOUT = 120       # seconds for one LibreOffice conversion
KEEP_DIRS = 150             # cached files kept (oldest are dropped)
SHEET_MAX_BYTES = 40_000_000

OFFICE = {".pptx", ".ppt", ".pptm", ".ppsx", ".pps", ".potx", ".odp", ".docx", ".doc", ".docm", ".dotx", ".odt", ".rtf",
          ".xls", ".xlsb", ".ods", ".key", ".pages", ".numbers"}
SHEET = {".xlsx", ".xlsm", ".csv", ".tsv"}
IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".aac", ".flac"}
VIDEO = {".mp4", ".mov", ".m4v", ".webm", ".ogv"}
TEXT_SKIP = {".zip", ".gz", ".exe", ".dll", ".so", ".dylib", ".bin", ".mp4", ".mov", ".mp3", ".wav", ".7z", ".tar", ".db",
             ".sqlite", ".pyc", ".woff", ".woff2", ".ttf", ".otf", ".ico", ".psd", ".docx~"}

_PDFIUM = threading.Lock()      # pypdfium2 is not thread-safe
_CONVERT = threading.Lock()     # one LibreOffice conversion at a time
_JOBS: dict[str, dict] = {}     # cache key -> {"state": "working"|"error", "error": str, "t": float}
_JOBS_LOCK = threading.Lock()


def kind_of(p: Path) -> str:
    ext = p.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in OFFICE:
        return "office"
    if ext in SHEET:
        return "sheet"
    if ext in IMAGE:
        return "image"
    if ext in AUDIO:
        return "audio"
    if ext in VIDEO:
        return "video"
    if ext in (".html", ".htm"):
        return "html"
    if ext in (".md", ".markdown"):
        return "md"
    if ext in TEXT_SKIP:
        return "none"
    return "text"


def _dir() -> Path:
    d = C.home() / "previews"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(p: Path) -> str:
    st = p.stat()
    return hashlib.sha1(f"v1|{p}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8", "replace")).hexdigest()[:24]


def _prune() -> None:
    try:
        dirs = sorted((d for d in _dir().iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
        for d in dirs[KEEP_DIRS:]:
            shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def resolve(root: str, rel: str) -> Path:
    """The file, inside the chat's root. Raises ValueError for anything else (same check as /api/file)."""
    from meshpool_harness.ui import files_api as FA
    p = FA.safe(root, rel)[1]
    if not p.is_file():
        raise ValueError("That file is gone.")
    return p


# ------------------------------------------------------------------ PDF pages
_PAGES: dict = {}               # cache key -> page count: the strip asks for every page, and each ask opened the PDF


def _page_count(pdf: Path, key: str) -> int:
    """_pdf_pages, kept per cache key (path, modified time and size, or a converted PDF that never changes)."""
    n = _PAGES.get(key)
    if n is None:
        n = _pdf_pages(pdf)
        if len(_PAGES) > 500:
            _PAGES.clear()
        _PAGES[key] = n
    return n


def _pdf_pages(pdf: Path) -> int:
    import pypdfium2 as pdfium
    with _PDFIUM:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            return len(doc)
        finally:
            doc.close()


def _render(pdf: Path, page: int, width: int) -> bytes:
    import pypdfium2 as pdfium
    with _PDFIUM:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            pg = doc[page]
            w_pt = max(1.0, pg.get_width())
            img = pg.render(scale=min(6.0, max(0.2, width / w_pt))).to_pil()
        finally:
            doc.close()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue()


# ------------------------------------------------------------------ Office -> PDF (background, one at a time)
def _convert_job(src: Path, key: str) -> None:
    if sys.platform.startswith("linux"):          # background work: the chat and the page come first (this thread's
        try:                                      # priority, inherited by LibreOffice)
            os.nice(10)
        except OSError:
            pass
    out = _dir() / key
    try:
        with _CONVERT:
            if (out / "doc.pdf").exists():
                if not (out / "meta.json").exists():          # a crash between the two writes: finish the job
                    part = out / f"meta.{os.getpid()}.{threading.get_ident()}.tmp"
                    part.write_text(json.dumps({"total": _pdf_pages(out / "doc.pdf")}))
                    os.replace(part, out / "meta.json")
                with _JOBS_LOCK:
                    _JOBS.pop(key, None)
                return
            from engines.forge.common import soffice_convert
            tmp = _dir() / (key + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                made = soffice_convert(str(src), "pdf", str(tmp), timeout=CONVERT_TIMEOUT)
                n = _pdf_pages(made)
                out.mkdir(parents=True, exist_ok=True)
                made.replace(out / "doc.pdf")
                part = out / f"meta.{os.getpid()}.{threading.get_ident()}.tmp"
                part.write_text(json.dumps({"total": n}))   # written whole, then moved: a poll never reads an
                os.replace(part, out / "meta.json")           # empty meta.json (CI failed on that race)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        _prune()
        with _JOBS_LOCK:
            _JOBS.pop(key, None)
    except subprocess.TimeoutExpired:
        _fail(key, f"Converting {src.name} took longer than {CONVERT_TIMEOUT} seconds. Try again in a moment.")
    except Exception as e:  # noqa: BLE001 - shown in the pane, in plain words (B6: no paths, no parser errors)
        _fail(key, friendly_error(src, e))


def friendly_error(p: Path, e: BaseException) -> str:
    """A parser or converter failure as one plain sentence: no paths, no exception names, no tool output."""
    what = {".docx": "Word document", ".doc": "Word document", ".docm": "Word document", ".dotx": "Word document",
            ".odt": "document", ".rtf": "document", ".pdf": "PDF"}.get(p.suffix.lower(), "file")
    if isinstance(e, subprocess.TimeoutExpired):
        return f"Converting this {what} took too long. Try again in a moment."
    if isinstance(e, (OSError, MemoryError)) and not isinstance(e, (ValueError, RuntimeError)) and getattr(e, "errno", None) in (13, 1):
        return f"Meshpool is not allowed to read this {what}."
    return f"This {what} is damaged or is not what its name says, so it cannot be previewed here. Open it in its app."


def _fail(key: str, msg: str) -> None:
    with _JOBS_LOCK:
        _JOBS[key] = {"state": "error", "error": msg, "t": time.time()}


def _office_state(p: Path) -> dict:
    key = _key(p)
    done = _dir() / key / "meta.json"
    if done.exists() and (done.parent / "doc.pdf").exists():
        try:
            total = json.loads(done.read_text()).get("total", 0)
        except (ValueError, OSError):                      # caught mid-write by an older writer: still working
            return {"state": "working", "key": key}
        return {"state": "ready", "pages": min(total, MAX_PAGES), "total": total, "key": key}
    from meshpool_harness import office_runtime
    if not office_runtime.find_soffice():
        return {"state": "error", "error": f"Previewing {p.suffix} files needs LibreOffice, which is not available "
                                           f"({office_runtime.missing_reason()}). Meshpool cannot show it here."}
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job and job["state"] == "working":
            return {"state": "working"}
        if job and job["state"] == "error" and time.time() - job["t"] < 10:
            return {"state": "error", "error": job["error"]}
        _JOBS[key] = {"state": "working", "t": time.time()}
    threading.Thread(target=_convert_job, args=(p, key), daemon=True, name="preview-convert").start()
    return {"state": "working"}


# ------------------------------------------------------------------ tables
def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.10g}"
    if hasattr(v, "isoformat"):                 # datetime, date, time
        if hasattr(v, "hour") and hasattr(v, "year") and not (v.hour or v.minute or v.second):
            return v.date().isoformat()
        return v.isoformat(sep=" ") if hasattr(v, "year") else v.isoformat()
    return str(v)


def _sections(fmt: str) -> list[str]:
    """A number format split at the ';' that are not inside quotes or after a backslash."""
    out, cur, q, esc = [], "", False, False
    for ch in fmt:
        if esc:
            cur, esc = cur + ch, False
        elif ch == "\\":
            cur, esc = cur + ch, True
        elif ch == '"':
            cur, q = cur + ch, not q
        elif ch == ";" and not q:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


_DATEISH = re.compile(r"[yYmMdDhHsS]")


def _group(digits: str) -> str:
    """Thousands separators on a digit string that may have leading zeros."""
    parts = []
    while digits:
        parts.append(digits[-3:])
        digits = digits[:-3]
    return ",".join(reversed(parts))


def _number(v, fmt) -> str:
    """A number as the cell's number format shows it: digit groups, decimals, percent, currency text, a negative or
    zero section. The preview showed 412000 for a cell Excel shows as 412,000. Formats it does not cover (dates,
    fractions, scientific) and General keep the plain text."""
    if not fmt or isinstance(v, bool) or not isinstance(v, (int, float)) or fmt in ("General", "@"):
        return _cell(v)
    secs = _sections(str(fmt))
    sign = ""
    if v < 0 and len(secs) >= 2 and secs[1].strip():
        sec, v = secs[1], -v
    elif v == 0 and len(secs) >= 3:
        sec = secs[2]
    else:
        sec = secs[0]
        if v < 0:
            sign, v = "-", -v
    pre, num, post, i, gen = "", "", "", 0, False
    while i < len(sec):                                  # literal text before and after one run of digit placeholders
        ch = sec[i]
        if sec[i:i + 7].lower() == "general" and not num and not gen:
            gen, i = True, i + 7                         # General inside a longer format is the plain number, not the word
            continue
        if ch == '"':
            j = sec.find('"', i + 1)
            j = len(sec) if j < 0 else j
            lit, i = sec[i + 1:j], j + 1
        elif ch == "\\" and i + 1 < len(sec):
            lit, i = sec[i + 1], i + 2
        elif ch == "[":
            j = sec.find("]", i)
            j = len(sec) if j < 0 else j
            inner, i = sec[i + 1:j], j + 1
            lit = inner[1:].split("-", 1)[0] if inner.startswith("$") else ""     # [$EUR-407] is a currency, [Red] is not
        elif ch in "_*":
            lit, i = "", i + 2                           # padding and fill characters
        elif ch in "0#?.,":
            if num and post:                             # a second run of placeholders: not a format this covers
                return _cell(v if not sign else -v)
            num += ch
            i += 1
            continue
        elif ch in "eE" and num:
            return _cell(v if not sign else -v)
        elif _DATEISH.match(ch) or ch == "/":
            return _cell(v if not sign else -v)
        else:
            lit, i = ch, i + 1
        if num or gen:
            post += lit
        else:
            pre += lit
    if gen:
        return sign + pre + _cell(v) + post
    if not num:
        return pre + post if pre or post else _cell(v if not sign else -v)
    if "%" in pre + post:
        v = v * 100
    body = num.rstrip(",")
    v = v / (1000 ** (len(num) - len(body)))             # trailing commas scale by thousands
    whole, _, frac = body.partition(".")
    need = frac.count("0")
    places = sum(frac.count(c) for c in "0#?")
    text = f"{v:,.{places}f}" if "," in whole else f"{v:.{places}f}"
    if places > need:                                    # optional decimals (#, ?) drop trailing zeros
        a, _, b = text.partition(".")
        b = b[:need] + b[need:].rstrip("0")
        text = a + ("." + b if b else "")
    zeros = whole.count("0")
    if zeros:                                            # 00000 keeps leading zeros: 02139 stays 02139
        a, dot, b = text.partition(".")
        digits = a.replace(",", "").zfill(zeros)
        if "," in whole:
            digits = _group(digits)
        text = digits + dot + b
    if "0" not in whole:
        a, dot, b = text.partition(".")
        if a in ("0", ""):
            text = (dot + b) if b else ""
    if sign and text.strip("0.,") == "":
        sign = ""                                        # -0.001 shown as 0, not -0
    return sign + pre + text + post


def _xlsx_sheets(p: Path) -> list[dict]:
    """Each sheet's rows as text, as Excel shows them: the cached value of a formula (the formula itself when the file
    was never calculated), numbers in the cell's number format. A sheet also lists its formulas by "row:col" for
    the cells that show a value (the page shows them on hover) and says when it is hidden."""
    import openpyxl
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    wf = openpyxl.load_workbook(p, read_only=True, data_only=False)     # F-24: formulas with no cached value show as text
    out = []
    try:
        try:
            from meshpool_harness import xl_values
            calc = xl_values.values(p) or {}           # formulas with no stored result (openpyxl): computed once per file version
        except Exception:  # noqa: BLE001
            calc = {}
        for ws in wb.worksheets[:MAX_SHEETS]:
            rows, cols, more, forms = [], 0, False, {}
            try:
                frows = wf[ws.title].iter_rows(max_col=MAX_COLS, values_only=True)
            except Exception:  # noqa: BLE001
                frows = iter(())
            for i, row in enumerate(ws.iter_rows(max_col=MAX_COLS)):
                if i >= MAX_ROWS:
                    more = True
                    break
                frow = tuple(next(frows, ()))
                frow += (None,) * (len(row) - len(frow))
                cells = []
                for j, (c, f) in enumerate(zip(row, frow)):
                    v = getattr(c, "value", None)
                    if not isinstance(f, str):
                        f = getattr(f, "text", None)                                  # an array formula
                        if isinstance(f, str) and f and not f.startswith("="):
                            f = "=" + f
                    elif getattr(c, "data_type", None) != "f" and not f.startswith("="):
                        f = None                                                      # a text label, not a formula
                    if v is None and f and f.startswith("="):
                        from openpyxl.utils import get_column_letter
                        v = calc.get((ws.title, f"{get_column_letter(j + 1)}{i + 1}"))
                    if v is None and f and f.startswith("="):
                        cells.append(f)                  # never calculated and cannot be here: the formula itself
                        continue
                    if f and f.startswith("="):
                        forms[f"{i}:{j}"] = f[:2000]
                    try:
                        cells.append(_number(v, getattr(c, "number_format", None)))
                    except (ValueError, TypeError, OverflowError):
                        cells.append(_cell(v))
                while cells and cells[-1] == "":
                    cells.pop()
                cols = max(cols, len(cells))
                rows.append(cells)
            while rows and not rows[-1]:
                rows.pop()
            sheet = {"name": ws.title, "rows": [r + [""] * (cols - len(r)) for r in rows], "cols": cols, "more": more,
                     "wide": (ws.max_column or 0) > MAX_COLS}
            if forms:
                sheet["formulas"] = {k: v for k, v in forms.items() if int(k.split(":")[0]) < len(rows)}
            if getattr(ws, "sheet_state", "visible") != "visible":
                sheet["hidden"] = True
            out.append(sheet)
        extra = len(wb.worksheets) - MAX_SHEETS
    finally:
        wb.close()
        wf.close()
    if extra > 0:
        out.append({"name": f"+{extra} more", "rows": [], "cols": 0, "more": False, "wide": False})
    return out


def _csv_sheet(p: Path) -> list[dict]:
    from meshpool_harness.docread import decode_text, sniff_delimiter
    with open(p, "rb") as fh:
        raw = fh.read(SHEET_MAX_BYTES)
    text = decode_text(raw, cut=len(raw) >= SHEET_MAX_BYTES)   # UTF-16 from Excel, Windows-1252 euro signs and quotes
    delim = "\t" if p.suffix.lower() == ".tsv" else sniff_delimiter(text)
    try:                                          # a cell over 128 KB must not fail the whole preview
        csv.field_size_limit(max(csv.field_size_limit(), min(len(text) + 1, 2 ** 31 - 1)))
    except (OverflowError, ValueError):
        pass
    rows, cols, more = [], 0, False
    for i, row in enumerate(csv.reader(io.StringIO(text), delimiter=delim)):
        if i >= MAX_ROWS:
            more = True
            break
        rows.append([c[:2000] for c in row[:MAX_COLS]])
        cols = max(cols, len(rows[-1]))
    return [{"name": p.stem, "rows": [r + [""] * (cols - len(r)) for r in rows], "cols": cols, "more": more, "wide": False}]


# ------------------------------------------------------------------ public API
def meta(root: str, rel: str) -> dict:
    p = resolve(root, rel)
    st = p.stat()
    k = kind_of(p)
    base = {"name": p.name, "path": rel, "ext": p.suffix.lower().lstrip("."), "size": st.st_size, "mtime": st.st_mtime,
            "kind": k}
    if k == "pdf":
        try:
            n = _page_count(p, _key(p))
        except Exception:  # noqa: BLE001
            raise ValueError("This PDF is damaged or protected, so it cannot be previewed here. Open it in its app.") from None
        return base | {"kind": "pages", "state": "ready", "pages": min(n, MAX_PAGES), "total": n}
    if k == "office":
        if p.suffix.lower() in (".docx", ".docm", ".dotx", ".pptx", ".pptm", ".ppsx", ".potx"):
            import zipfile
            kind = "zip" if zipfile.is_zipfile(p) else ole_kind(p)     # an old binary file ("ole"): LibreOffice reads it
            if kind == "encrypted":
                raise ValueError("This file is password-protected, so it cannot be previewed here. Open it in its app.")
            if not kind:                                # B6: not even a package: say so before LibreOffice is asked
                raise ValueError(friendly_error(p, ValueError("not a package")))
        return base | {"kind": "pages"} | _office_state(p)
    if k == "sheet":
        if st.st_size > SHEET_MAX_BYTES:
            return base | {"kind": "none", "note": "This file is too large to preview. Meshpool cannot show it here."}
        if p.suffix.lower() in (".xlsx", ".xlsm"):
            try:
                return base | {"state": "ready", "sheets": _xlsx_sheets(p)}
            except Exception:  # noqa: BLE001 - a workbook openpyxl cannot read: let LibreOffice draw it
                import zipfile
                kind = "zip" if zipfile.is_zipfile(p) else ole_kind(p)
                if kind == "encrypted":
                    raise ValueError("This workbook is password-protected, so it cannot be previewed here. Open it in "
                                     "its app.") from None
                if not kind:
                    raise ValueError("This workbook is damaged, so it cannot be previewed. Open it in its app.") from None
                return base | {"kind": "pages"} | _office_state(p)
        return base | {"state": "ready", "sheets": _csv_sheet(p)}
    if k == "image":
        return base | {"state": "ready", "mime": "image/svg+xml" if p.suffix.lower() == ".svg" else None}
    if k in ("audio", "video"):
        return base | {"state": "ready"}
    if k == "none":
        return base | {"state": "ready", "note": "There is no preview for this kind of file. Meshpool cannot show it here."}
    if st.st_size > MAX_TEXT:
        return base | {"kind": "none", "state": "ready", "note": "This file is larger than 2 MB. Meshpool cannot show it here."}
    data = p.read_bytes()
    if b"\0" in data[:8000]:
        return base | {"kind": "none", "state": "ready", "note": "There is no preview for this kind of file. Meshpool cannot show it here."}
    return base | {"state": "ready", "text": data.decode("utf-8", errors="replace")}


def page_png(root: str, rel: str, page: int, width: int = MAX_WIDTH) -> bytes:
    """PNG of one page (0-based `page`), at most MAX_WIDTH wide. Cached by file path, mtime and size."""
    p = resolve(root, rel)
    k = kind_of(p)
    if k == "pdf":
        pdf, key = p, _key(p)
    elif k in ("office", "sheet"):
        st = _office_state(p)
        if st["state"] == "working":
            raise ValueError("Still converting. Try again in a moment.")
        if st["state"] == "error":
            raise ValueError(st["error"])
        key = st["key"]
        pdf = _dir() / key / "doc.pdf"
    else:
        raise ValueError("This file has no pages.")
    width = max(MIN_WIDTH, min(MAX_WIDTH, width))
    d = _dir() / key
    f = d / f"p{page}_{width}.png"
    if 0 <= page < MAX_PAGES and f.exists():       # rendered before from this very file: no need to open the PDF
        try:
            f.touch()
            return f.read_bytes()
        except OSError:
            pass
    total = _page_count(pdf, key)
    if not 0 <= page < min(total, MAX_PAGES):
        raise ValueError("No such page.")
    d.mkdir(parents=True, exist_ok=True)
    data = _render(pdf, page, width)
    # each request writes its own part file: two requests for one page (two tabs) shared one name, and the second
    # move found nothing there and failed the page
    tmp = f.with_name(f"{f.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(f)
    if k == "pdf":
        _prune()
    return data
