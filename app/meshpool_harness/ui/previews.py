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
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from meshpool_harness import config as C
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
    except Exception as e:  # noqa: BLE001 - shown in the pane
        msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        _fail(key, msg[:300])


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


def _xlsx_sheets(p: Path) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    out = []
    try:
        for ws in wb.worksheets[:MAX_SHEETS]:
            rows, cols, more = [], 0, False
            for i, row in enumerate(ws.iter_rows(max_col=MAX_COLS, values_only=True)):
                if i >= MAX_ROWS:
                    more = True
                    break
                cells = [_cell(v) for v in row]
                while cells and cells[-1] == "":
                    cells.pop()
                cols = max(cols, len(cells))
                rows.append(cells)
            while rows and not rows[-1]:
                rows.pop()
            out.append({"name": ws.title, "rows": [r + [""] * (cols - len(r)) for r in rows], "cols": cols, "more": more,
                        "wide": (ws.max_column or 0) > MAX_COLS})
        extra = len(wb.worksheets) - MAX_SHEETS
    finally:
        wb.close()
    if extra > 0:
        out.append({"name": f"+{extra} more", "rows": [], "cols": 0, "more": False, "wide": False})
    return out


def _csv_sheet(p: Path) -> list[dict]:
    raw = p.read_bytes()[:SHEET_MAX_BYTES]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    delim = "\t" if p.suffix.lower() == ".tsv" else ","
    if p.suffix.lower() == ".csv":
        try:
            delim = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            pass
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
            n = _pdf_pages(p)
        except Exception:  # noqa: BLE001
            raise ValueError("This PDF is damaged or protected, so it cannot be previewed here. Open it in its app.") from None
        return base | {"kind": "pages", "state": "ready", "pages": min(n, MAX_PAGES), "total": n}
    if k == "office":
        return base | {"kind": "pages"} | _office_state(p)
    if k == "sheet":
        if st.st_size > SHEET_MAX_BYTES:
            return base | {"kind": "none", "note": "This file is too large to preview. Meshpool cannot show it here."}
        if p.suffix.lower() in (".xlsx", ".xlsm"):
            try:
                return base | {"state": "ready", "sheets": _xlsx_sheets(p)}
            except Exception:  # noqa: BLE001 - a workbook openpyxl cannot read: let LibreOffice draw it
                import zipfile
                if not zipfile.is_zipfile(p):
                    raise ValueError("This workbook is damaged, so it cannot be previewed. Open it in its app.") from None
                return base | {"kind": "pages"} | _office_state(p)
        return base | {"state": "ready", "sheets": _csv_sheet(p)}
    if k == "image":
        return base | {"state": "ready", "mime": "image/svg+xml" if p.suffix.lower() == ".svg" else None}
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
    total = _pdf_pages(pdf)
    if not 0 <= page < min(total, MAX_PAGES):
        raise ValueError("No such page.")
    width = max(MIN_WIDTH, min(MAX_WIDTH, width))
    d = _dir() / key
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"p{page}_{width}.png"
    if f.exists():
        try:
            f.touch()
            return f.read_bytes()
        except OSError:
            pass
    data = _render(pdf, page, width)
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(f)
    if k == "pdf":
        _prune()
    return data
