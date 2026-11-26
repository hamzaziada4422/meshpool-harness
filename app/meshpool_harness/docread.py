"""Read the files people work with as text an agent can use: PDF, Word, PowerPoint, Excel, CSV, Power BI (.pbix,
.pbit), OpenDocument, notebooks, email, HTML and more. Code and plain text read as they are.

extract(path) returns Markdown-ish text. Every reader is optional: a missing library or a damaged file gives a
clear message, never a crash.
"""
from __future__ import annotations

import csv
import io
import json
import re
import os
import sys
import threading
import zipfile
from pathlib import Path

DOC_EXT = {".msg", ".zip",".pdf", ".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".pbix", ".pbit",
           ".odt", ".ods", ".odp", ".rtf", ".ipynb", ".eml", ".html", ".htm", ".epub", ".doc", ".ppt"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
MAX = 200_000


def is_document(p: Path) -> bool:
    return p.suffix.lower() in DOC_EXT


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXT


# One read at a time per core pair: several chats reading PDFs at once must not use every core.
_SLOTS = threading.BoundedSemaphore(max(1, (os.cpu_count() or 2) // 2))
PDF_PAGES = 80                           # pages read per call when the caller does not name pages
_CACHE_MAX = 300                         # files kept in the cache folder
_NOT_CACHED = ("Could not read", "Meshpool cannot read", "[Meshpool] ")
# Bumped whenever a reader changes what it returns: the cache outlives upgrades, and a file read by an older reader
# kept its old text until the file itself changed (review, 0.0.18: tracked changes, footnotes, RTF, CSV encodings).
READER_VERSION = 19


def _cache_dir() -> Path:
    from meshpool_harness import config as C
    return C.home() / "cache" / "docread"


def _cache_key(p: Path, *args) -> str:
    import hashlib
    st = p.stat()
    return hashlib.sha1(repr((READER_VERSION, str(p.resolve()), st.st_mtime_ns, st.st_size, PDF_PAGES, *args))
                        .encode("utf-8")).hexdigest()


def _cache_get(key: str) -> str | None:
    try:
        f = _cache_dir() / (key + ".txt")
        txt = f.read_text(encoding="utf-8")
        os.utime(f, None)                  # recently used stays
        return txt
    except OSError:
        return None


def _cache_put(key: str, text: str) -> None:
    if not text or text.startswith(_NOT_CACHED) or "OCR did not run" in text:
        return                             # a failure or a partial OCR must be tried again, not remembered
    try:
        d = _cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, d / (key + ".txt"))
        files = sorted(d.glob("*.txt"), key=lambda f: f.stat().st_mtime)
        for f in files[:max(0, len(files) - _CACHE_MAX)]:
            f.unlink(missing_ok=True)
    except OSError:
        pass


def extract(path: str | Path, max_chars: int = MAX, sheet: str | None = None, pages: str | None = None,
            lang: str = "auto", ocr: str = "auto") -> str:
    """ocr: "auto" reads scanned pages and pictures with OCR, "always" also OCRs pages that have some text
    (mixed scans), "never" skips OCR. The text of a file is kept on disk keyed by its path, size and modified time,
    so reading it again (or in another chat) costs nothing."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"file not found: {path}")
    if os.environ.get("MH_DOC_CACHE", "1") == "0":
        return _extract(p, max_chars, sheet, pages, lang, ocr)
    try:
        key = _cache_key(p, max_chars, sheet, pages, lang, ocr)
    except OSError:
        return _extract(p, max_chars, sheet, pages, lang, ocr)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    with _SLOTS:
        hit = _cache_get(key)              # another chat may have just read it while this one waited its turn
        if hit is not None:
            return hit
        out = _extract(p, max_chars, sheet, pages, lang, ocr)
    _cache_put(key, out)
    return out


OOXML = {".docx", ".docm", ".dotx", ".pptx", ".pptm", ".ppsx", ".potx", ".xlsx", ".xlsm", ".xltx"}


def ole_kind(p: Path) -> str:
    """For a file with an Office 2007+ name that is not a zip package: "encrypted" when it is a password-protected
    Office file (an OLE container with an EncryptedPackage stream), "ole" for an old binary Office file saved under a
    new name, else ""."""
    try:
        with open(p, "rb") as fh:
            if fh.read(8) != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                return ""
        try:
            import olefile
            with olefile.OleFileIO(str(p)) as ole:
                return "encrypted" if ole.exists("EncryptedPackage") or ole.exists("EncryptionInfo") else "ole"
        except Exception:  # noqa: BLE001  no olefile, or a damaged container: the signature is enough to say so
            return "encrypted"
    except OSError:
        return ""


def _protected(p: Path) -> str | None:
    """A plain sentence for a password-protected or misnamed Office file (the parsers said "Package not found")."""
    kind = ole_kind(p) if p.suffix.lower() in OOXML else ""
    if kind == "encrypted":
        return (f"{p.name} is password-protected (encrypted), so its text cannot be read. Ask the user to remove the "
                "password (in Office: File, Info, Protect) and save it again, or to share a copy without one.")
    if kind == "ole":
        return (f"{p.name} is an old binary Office file saved with a {p.suffix} name. Open it in Office and save it as "
                f"{p.suffix}, or convert it with LibreOffice, then read that.")
    return None


def _extract(p: Path, max_chars: int, sheet, pages, lang: str, ocr: str) -> str:
    ext = p.suffix.lower()
    locked = _protected(p)
    if locked:
        return locked
    fn = {".pdf": _pdf, ".docx": _docx, ".docm": _docx, ".pptx": _pptx, ".pptm": _pptx, ".xlsx": _xlsx,
          ".xlsm": _xlsx, ".xls": _xls, ".csv": _csv, ".tsv": _csv, ".pbix": _pbix, ".pbit": _pbix,
          ".odt": _odf, ".ods": _odf, ".odp": _odf, ".rtf": _rtf, ".ipynb": _ipynb, ".eml": _eml,
          ".html": _html, ".htm": _html, ".epub": _epub, ".msg": _msg, ".zip": _zip}.get(ext)
    if ext in (".doc", ".ppt"):
        return (f"{p.name} is an old binary Office file ({ext}). Save it as {ext}x in Word or PowerPoint, or convert it "
                f"with LibreOffice (soffice --headless --convert-to {ext[1:]}x), then read that.")
    if ext in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".mkv", ".avi", ".webm"}:
        return (f"{p.name} is an audio or video file ({p.stat().st_size // 1_000_000} MB). Meshpool does not transcribe "
                "media yet; ask for a transcript file, or describe what you need from it.")
    if ext in IMAGE_EXT:
        from meshpool_harness import ocr
        ocr._heif()
        size = f"{p.stat().st_size // 1024} KB"
        try:
            from PIL import Image
            with Image.open(p) as im:
                size = f"{im.width}x{im.height} px, {size}"
        except Exception:  # noqa: BLE001
            pass
        return f"# {p.name}\nImage, {size}.\n\n" + ocr.describe(p, p.name, lang)
    if not fn:                                     # plain text: only what is shown is read (a 3 GB log is not loaded)
        with open(p, "rb") as fh:
            raw = fh.read(max_chars * 4 + 4)
        text, size = raw.decode("utf-8", errors="replace"), p.stat().st_size
        if size > len(raw) or len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... cut at {max_chars} characters of a {size:,}-byte file. Read it in parts.]"
        return text
    try:
        kw = {}
        if fn in (_xlsx, _xls) and sheet:
            kw["sheet"] = sheet
        if fn is _pdf:
            kw.update(pages=pages, lang=lang, ocr_mode=ocr)
        if fn in (_docx, _pptx):
            kw["ocr_mode"] = ocr
        out = fn(p, **kw)
    except ImportError as e:
        return f"Meshpool cannot read {ext} files in this install ({e})."
    except Exception as e:  # noqa: BLE001  damaged or unusual files
        if ext in OOXML and _opens(p):             # the reader's limit, not a broken file (Daniel, 0.0.18: "could not
            return (f"meshpool's reader could not read part of {p.name} ({type(e).__name__}: {e}). The file itself "  # parse it")
                    "opens: this is a limit of the reader, not a damaged file. Do not tell the user the file is "
                    "broken; read it another way (python-pptx, python-docx or openpyxl in a command).")
        return f"Could not read {p.name}: {type(e).__name__}: {e}"
    if len(out) > max_chars:
        out = out[:max_chars] + f"\n\n[... cut at {max_chars} characters of {len(out)}. Ask for specific pages or sheets.]"
    if ext in (".pdf", ".docx", ".pptx"):
        try:                                       # charts drawn as pictures have no text: say where they are (Daniel,
            from meshpool_harness import docfigures as DF    # 0.0.18: "The report has no barriers chart")
            fig = DF.note(p, DF.in_pages(DF.figures(p), pages if ext != ".docx" else None))
            if fig:
                out += "\n\n" + fig
        except Exception:  # noqa: BLE001 - the note is extra; the text stands without it
            pass
    return out


def _opens(p: Path) -> bool:
    """True when an Office file is a whole zip package with its main part: it opens, whatever a reader made of it."""
    try:
        with zipfile.ZipFile(p) as z:
            names = set(z.namelist())
            return z.testzip() is None and bool(names & {"word/document.xml", "ppt/presentation.xml", "xl/workbook.xml"})
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
        return False


# ---------- PDF ---------------------------------------------------------------------------------------------------

def _page_set(spec: str | None, n: int) -> list[int]:
    if not spec:
        return list(range(n))
    out = []
    for part in str(spec).replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out += range(max(1, int(a or 1)) - 1, min(n, int(b or n)))
        elif part:
            out.append(int(part) - 1)
    return [i for i in out if 0 <= i < n]


def _pdf(p: Path, pages: str | None = None, lang: str = "auto", ocr_mode: str = "auto") -> str:
    from pypdf import PdfReader
    r = PdfReader(str(p))
    if r.is_encrypted:
        try:
            ok = int(r.decrypt(""))                # 0 (NOT_DECRYPTED) means a password is needed; it does not raise
        except Exception:  # noqa: BLE001
            ok = 0
        if not ok:
            return f"{p.name} is password-protected."
    n = len(r.pages)
    meta = r.metadata or {}
    head = f"# {p.name}\n{n} pages" + (f", title: {meta.get('/Title')}" if meta.get("/Title") else "") + "\n"
    idx = _page_set(pages, n)
    if not pages and len(idx) > PDF_PAGES:        # a long PDF in one go is slow and floods the context: a first part
        head += (f"\nShowing pages 1 to {PDF_PAGES}; {len(idx) - PDF_PAGES} more pages are not read yet. "
                 f"Ask for them with pages=\"{PDF_PAGES + 1}-{min(n, 2 * PDF_PAGES)}\" and so on.\n")
        idx = idx[:PDF_PAGES]
    texts: list[str] = []
    miner, tries = True, 0
    for i in idx:
        try:
            t = r.pages[i].extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        if len(t.strip()) < 20 and miner and tries < 6:   # each try re-reads the whole file: only a few
            tries += 1
            try:                                   # pdfminer handles some layouts pypdf misses
                from pdfminer.high_level import extract_text
                t2 = extract_text(str(p), page_numbers=[i]) or ""
                t = t2 if len(t2.strip()) > len(t.strip()) else t
                if not t2.strip():
                    miner = False                  # a scan: pdfminer finds nothing and re-reads the file every time
            except Exception:  # noqa: BLE001
                miner = False
        texts.append(t.strip())
    # The text layer is always kept. "auto" OCRs only pages with no or almost no text (scans, perhaps with a stamped
    # page number); "always" OCRs every page as well and adds what it reads next to the text layer.
    blank = [i for i, t in zip(idx, texts) if not t]
    if ocr_mode == "always":
        want = blank + [i for i, t in zip(idx, texts) if t]
    elif ocr_mode == "auto":
        want = [i for i, t in zip(idx, texts) if len(t) < 50]
    else:
        want = []
    has_text = {i: bool(t) for i, t in zip(idx, texts)}
    want = sorted(dict.fromkeys(want), key=lambda i: (has_text[i], i))     # pages with no text first
    ocr_text: dict[int, str] = {}
    if want:
        try:
            from meshpool_harness import ocr
            if not ocr.available():
                raise RuntimeError("OCR is not included in this install")
            for i, img in ocr.pdf_page_images(p, sorted(want[:40])):
                ocr_text[i] = ocr.describe(img, f"page {i + 1}", lang)
            if ocr_text:
                head += f"\n{len(ocr_text)} page{'s' if len(ocr_text) != 1 else ''} read with OCR.\n"
            elif len(blank) == len(idx):
                head += "\nNo text layer and no page images found in this PDF.\n"
            if len(want) > 40:
                head += "\nOnly the first 40 pages were read with OCR; ask for specific pages for the rest.\n"
        except Exception as e:  # noqa: BLE001
            if blank:
                head += (f"\n{len(blank)} of {len(idx)} pages have no text layer (probably scanned) and OCR did not "
                         f"run ({e}).\n")
            elif ocr_mode == "always":
                head += f"\nOCR did not run ({e}); the text layer is shown.\n"
    parts = []
    for i, t in zip(idx, texts):
        o = ocr_text.get(i)
        found = o.split("\n", 1)[1] if o and o.startswith("Text in ") and "\n" in o else ""
        if o is not None and not t:
            parts.append(f"\n## Page {i + 1} (scanned, read with OCR)\n" + (found or o).strip())
        elif found:
            parts.append(f"\n## Page {i + 1}\n{t}\n\n[text in the page image (OCR)]\n{found.strip()}")
        else:
            parts.append(f"\n## Page {i + 1}\n{t}")
    return head + "\n".join(parts)


# ---------- Word ---------------------------------------------------------------------------------------------------

def _picture_text(blobs: list[tuple[str, bytes]], ocr_mode: str) -> list[str]:
    """OCR the larger pictures inside a Word or PowerPoint file (screenshots, scanned pages, charts as images)."""
    if ocr_mode == "never" or not blobs:
        return []
    try:
        from meshpool_harness import ocr
        if not ocr.available():
            return []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for name, data in sorted(blobs, key=lambda b: -len(b[1]))[:8]:
        if len(data) < 30_000 and ocr_mode != "always":
            continue
        try:
            r = ocr.read_image(data)
        except Exception:  # noqa: BLE001
            continue
        if len(r["text"].strip()) > 15:
            out.append(f"[text in picture {name} (OCR)]\n{r['text']}")
    return out


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


def _w_text(el) -> str:
    """The text of a Word paragraph (or note) as it reads with every tracked change accepted: runs inside hyperlinks,
    content controls, fields and insertions count; deleted and moved-away text does not, and the fallback copy of a
    drawing is not read twice."""
    out = []

    def walk(e):
        for c in e:
            tag = c.tag if isinstance(c.tag, str) else ""
            if tag in (_W + "del", _W + "moveFrom", _MC_FALLBACK, _W + "rPr", _W + "pPr", _W + "instrText"):
                continue
            if tag == _W + "t":
                out.append(c.text or "")
            elif tag == _W + "tab":
                out.append("\t")
            elif tag in (_W + "br", _W + "cr"):
                out.append("\n")
            elif tag == _W + "noBreakHyphen":
                out.append("-")
            else:
                walk(c)
    walk(el)
    return "".join(out)


def _paras_text(root) -> str:
    """The paragraphs under root (a table cell, a header, a note) joined by spaces; a text box's paragraphs are read
    once, as part of the paragraph that holds them."""
    return " ".join(t for t in (_w_text(x).strip() for x in root.iter(_W + "p")
                                if next(x.iterancestors(_W + "p"), None) is None) if t)


def _docx_blocks(parent, d, out: list) -> None:
    """Paragraphs and tables of a body (or a content control inside it) in order, as Markdown."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in parent.iterchildren():
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag == _W + "p":
            t = _w_text(child).strip()
            if not t:
                continue
            para = Paragraph(child, d)
            try:
                st = (para.style.name if para.style is not None else "") or ""
            except Exception:  # noqa: BLE001  a style id the styles part does not define
                st = ""
            if st.startswith("Heading"):
                lvl = re.sub(r"\D", "", st) or "1"
                out.append("#" * min(6, int(lvl) + 1) + " " + t)
            elif st.startswith("List") or st.startswith("Bullet"):
                out.append("- " + t)
            else:
                out.append(t)
        elif tag == _W + "tbl":
            out.append(_md_table([[_paras_text(c._tc) for c in row.cells] for row in Table(child, d).rows]))
        elif tag in (_W + "sdt", _W + "customXml"):          # content controls: a table of contents, a form field
            content = child.find(_W + "sdtContent") if tag == _W + "sdt" else child
            if content is not None:
                _docx_blocks(content, d, out)


def _docx_notes(p: Path) -> list[str]:
    """Footnotes and endnotes, which python-docx does not read: "[^1] text" lines under their own headings."""
    from lxml import etree
    out = []
    try:
        with zipfile.ZipFile(str(p)) as z:
            names = set(z.namelist())
            for part, kind, title in (("word/footnotes.xml", "footnote", "Footnotes"),
                                      ("word/endnotes.xml", "endnote", "Endnotes")):
                if part not in names:
                    continue
                root = etree.fromstring(z.read(part), etree.XMLParser(resolve_entities=False, no_network=True))
                rows = []
                for n in root.iter(_W + kind):
                    if n.get(_W + "type") in ("separator", "continuationSeparator", "continuationNotice"):
                        continue
                    t = _paras_text(n)
                    if t:
                        rows.append(f"[^{n.get(_W + 'id')}] {t}")
                if rows:
                    out.append(f"\n## {title}\n" + "\n".join(rows))
    except Exception:  # noqa: BLE001  notes are extra: a damaged part never fails the read
        return []
    return out


def _docx(p: Path, ocr_mode: str = "auto") -> str:
    import docx
    d = docx.Document(str(p))
    out = [f"# {p.name}"]
    _docx_blocks(d.element.body, d, out)
    revs = [sum(1 for e in d.element.body.iter(_W + t) if e.getparent().tag != _W + "rPr") for t in ("ins", "del")]
    shown = set()
    for s in d.sections:                    # every section's header and footer (a linked one is the same text)
        for label, part in (("header", s.header), ("footer", s.footer), ("first page header", s.first_page_header),
                            ("first page footer", s.first_page_footer), ("even page header", s.even_page_header),
                            ("even page footer", s.even_page_footer)):
            if part.is_linked_to_previous:
                continue
            t = _paras_text(part._element)
            if t and (label, t) not in shown:
                shown.add((label, t))
                out.append(f"[{label}] {t}")
    out += _docx_notes(p)
    if revs[0] or revs[1]:
        out.append(f"[tracked changes: {revs[0]} insertion(s) and {revs[1]} deletion(s) not yet accepted; the text "
                   "above reads as if they were accepted]")
    blobs = [(Path(r.target_ref).name, r.target_part.blob) for r in d.part.rels.values()
             if "image" in r.reltype and not r.is_external]
    out += _picture_text(blobs, ocr_mode)
    try:
        comments = [c.text for c in d.comments] if hasattr(d, "comments") else []
        if comments:
            out.append("\n## Comments\n" + "\n".join(f"- {c}" for c in comments))
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(out)


def _num(v) -> str:
    """A cell or chart value as text. Floats keep their full precision (15 significant digits, so 15432198.75 stays
    15432198.75 and 0.1 + 0.2 shows 0.3); whole numbers lose the trailing .0."""
    if isinstance(v, float) and not isinstance(v, bool):
        return f"{v:.15g}"
    return "" if v is None else str(v)


def _md_table(rows: list[list[str]]) -> str:
    rows = [[str(c).replace("|", "\\|").replace("\n", " ") for c in r] for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    return "\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in rows[1:]])


# ---------- PowerPoint ----------------------------------------------------------------------------------------------

def _pptx(p: Path, ocr_mode: str = "auto") -> str:
    from pptx import Presentation
    prs = Presentation(str(p))
    out = [f"# {p.name}\n{len(prs.slides)} slides"]
    blobs = []
    for i, s in enumerate(prs.slides, 1):
        lines = []
        try:
            title = s.shapes.title.text.strip() if s.shapes.title is not None and s.shapes.title.has_text_frame else ""
        except Exception:  # noqa: BLE001
            title = ""
        if not title:                              # a title drawn as a plain shape named "Title 1" (no placeholder)
            title = next((sh.text_frame.text.strip() for sh in s.shapes if str(sh.name).lower().startswith("title")
                          and getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()), "")
        for sh in s.shapes:
            lines += _shape_text(sh, title)
        notes = ""
        if s.has_notes_slide and s.notes_slide.notes_text_frame is not None:
            notes = s.notes_slide.notes_text_frame.text.strip()
        hidden = " [hidden slide: not shown in the slide show]" if s._element.get("show") in ("0", "false") else ""
        out.append(f"\n## Slide {i}: {title or '(no title)'}{hidden}\n" + "\n".join(lines)
                   + (f"\n[notes] {notes}" if notes else ""))
        for sh in s.shapes:
            if _stype(sh) == 13:
                try:
                    blobs.append((f"on slide {i}", sh.image.blob))
                except Exception:  # noqa: BLE001
                    pass
    out += _picture_text(blobs, ocr_mode)
    return "\n".join(out)


def _stype(sh):
    """A shape's type, or None for one python-pptx cannot classify (a shape with no geometry, ink, an OLE object):
    shape_type raises NotImplementedError for those, and one such shape made the whole deck unreadable (Daniel,
    0.0.18: "unrecognized shape type", 2 of 7 slides seen)."""
    try:
        return sh.shape_type
    except Exception:  # noqa: BLE001
        return None


def _shape_text(sh, title: str) -> list[str]:
    try:
        return _shape_text1(sh, title)
    except Exception:  # noqa: BLE001 - an odd shape is skipped, never the slide
        return []


def _shape_text1(sh, title: str) -> list[str]:
    out = []
    if (_stype(sh) == 6 or sh._element.tag.endswith("}grpSp")) and hasattr(sh, "shapes"):          # group
        for x in sh.shapes:
            out += _shape_text(x, title)
        return out
    if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
        for para in sh.text_frame.paragraphs:
            t = "".join(r.text for r in para.runs).strip()
            if t and t != title:
                out.append(("  " * para.level) + "- " + t)
    if getattr(sh, "has_table", False) and sh.has_table:
        out.append(_md_table([[c.text for c in row.cells] for row in sh.table.rows]))
    if getattr(sh, "has_chart", False) and sh.has_chart:
        ch = sh.chart
        try:
            cats = list(ch.plots[0].categories)
            series = [(s.name, list(s.values)) for s in ch.plots[0].series]
            rows = [["", *[str(c) for c in cats]]] + [[n, *[_num(v) for v in vals]]
                                                      for n, vals in series]
            out.append("[chart]\n" + _md_table(rows))
        except Exception:  # noqa: BLE001
            out.append("[chart]")
    if _stype(sh) == 13:
        out.append(f"[picture: {sh.name}]")
    return out


# ---------- Excel and CSV -----------------------------------------------------------------------------------------

def _xlsx(p: Path, sheet: str | None = None, rows: int = 200) -> str:
    import openpyxl
    vals = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
    forms = None
    try:
        try:
            forms = openpyxl.load_workbook(str(p), data_only=False, read_only=True)
        except Exception:  # noqa: BLE001
            forms = None
        return _xlsx_text(p, vals, forms, sheet, rows)
    finally:
        for wb in (vals, forms):
            if wb is not None:
                try:
                    wb.close()
                except Exception:  # noqa: BLE001
                    pass


def _sheet_state(ws) -> str:
    st = getattr(ws, "sheet_state", "visible") or "visible"
    return {"hidden": "hidden", "veryHidden": "very hidden"}.get(st, "")


def _xlsx_text(p: Path, vals, forms, sheet: str | None, rows: int) -> str:
    names = vals.sheetnames
    listed = [n + (f" ({_sheet_state(vals[n])})" if _sheet_state(vals[n]) else "") for n in names]
    out = [f"# {p.name}\nSheets: " + ", ".join(listed)]
    if sheet and sheet not in names:
        match = [n for n in names if n.strip().lower() == str(sheet).strip().lower()]
        if not match:
            return f"{p.name} has no sheet named {sheet!r}. Sheets: {', '.join(listed)}"
        sheet = match[0]
    for name in ([sheet] if sheet else names):
        ws = vals[name]
        if not hasattr(ws, "iter_rows"):            # a chart sheet: a chart on its own tab, no cells (it failed the read)
            out.append(f"\n## Chart sheet: {name} (a chart, no cells)")
            continue
        try:
            fws = forms[name] if forms else None
        except KeyError:
            fws = None
        # read-only sheets saved without a <dimension> report max_row/max_column as None: count while reading
        size = f" ({ws.max_row} rows x {ws.max_column} columns)" if ws.max_row and ws.max_column else ""
        state = _sheet_state(ws)
        out.append(f"\n## Sheet: {name}{size}" + (f" [{state} sheet: not shown in Excel]" if state else ""))
        table, nform, more = [], 0, False
        frows = fws.iter_rows(max_row=rows) if fws is not None else None
        for r_i, row in enumerate(ws.iter_rows(values_only=True)):
            if r_i >= rows:
                more = True
                break
            frow = next(frows, None) if frows is not None else None
            cells = []
            for c_i, v in enumerate(row):
                f = frow[c_i].value if frow is not None and c_i < len(frow) else None
                s = _num(v)
                if isinstance(f, str) and f.startswith("="):
                    nform += 1
                    s = f"{s} [{f}]" if s else f"[{f}]"
                cells.append(s)
            table.append(cells)
        while table and not any(table[-1]):
            table.pop()
        out.append(_md_table(table) if table else "(empty)")
        if more:
            total = f" of {ws.max_row}" if ws.max_row else ""
            out.append(f"[first {rows}{total} rows; ask for a sheet or use analyze_data for the rest]")
        if nform:
            out.append(f"[{nform} formulas shown in brackets after their values]")
    return "\n".join(out)


def _xls(p: Path, sheet: str | None = None, rows: int = 200) -> str:
    import xlrd
    wb = xlrd.open_workbook(str(p))
    hidden = {s.name: {1: "hidden", 2: "very hidden"}.get(s.visibility, "") for s in wb.sheets()}
    listed = [n + (f" ({hidden[n]})" if hidden.get(n) else "") for n in wb.sheet_names()]
    out = [f"# {p.name}\nSheets: " + ", ".join(listed)]
    if sheet and sheet not in wb.sheet_names():
        return f"{p.name} has no sheet named {sheet!r}. Sheets: {', '.join(listed)}"
    for name in ([sheet] if sheet else wb.sheet_names()):
        sh = wb.sheet_by_name(name)
        out.append(f"\n## Sheet: {name} ({sh.nrows} rows x {sh.ncols} columns)"
                   + (f" [{hidden[name]} sheet: not shown in Excel]" if hidden.get(name) else ""))
        out.append(_md_table([[str(c) for c in sh.row_values(r)] for r in range(min(rows, sh.nrows))]) or "(empty)")
    return "\n".join(out)


def decode_text(raw: bytes, cut: bool = False) -> str:
    """Bytes of a text file (CSV, TSV) as text, whatever program wrote it: a BOM names the encoding (Excel's
    "Unicode text" is UTF-16), else UTF-8, else UTF-16 without a BOM, else Windows-1252 (Excel on Windows: euro sign,
    curly quotes, accents). cut: raw is only the start of the file, so a character split at the end is dropped."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"),
                     (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            body = raw[len(bom):]
            if cut and enc.startswith("utf-16"):
                body = body[:len(body) - len(body) % 2]
            return body.decode(enc, errors="replace")
    # UTF-16 with no byte-order mark first: NUL bytes are valid UTF-8, so plain ASCII text saved as UTF-16 "decoded"
    # as UTF-8 with a NUL after every letter (review, 0.0.18)
    head = raw[:4000]
    if len(head) >= 4 and head[1::2].count(0) > len(head) // 4 and head[0::2].count(0) < len(head) // 20:
        return raw[:len(raw) - len(raw) % 2].decode("utf-16-le", errors="replace")
    if len(head) >= 4 and head[0::2].count(0) > len(head) // 4 and head[1::2].count(0) < len(head) // 20:
        return raw[:len(raw) - len(raw) % 2].decode("utf-16-be", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        if cut and e.start >= len(raw) - 3 and e.reason == "unexpected end of data":
            return raw[:e.start].decode("utf-8", errors="replace")
    return raw.decode("cp1252", errors="replace")


def sniff_delimiter(text: str, default: str = ",") -> str:
    """The delimiter of a CSV sample (comma, semicolon, tab or bar), else default."""
    if not text.strip():
        return default
    try:
        return csv.Sniffer().sniff(text[:8000], delimiters=",;\t|").delimiter
    except csv.Error:                              # one column, or no consistent delimiter
        return default


def _csv(p: Path, rows: int = 300) -> str:
    size = p.stat().st_size
    with open(p, "rb") as fh:                      # only the start of a big file, never all of it
        raw = decode_text(fh.read(5_000_000), cut=size > 5_000_000)
    delim = "\t" if p.suffix.lower() == ".tsv" else sniff_delimiter(raw)
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))     # a huge cell must not fail the whole file
    data = list(csv.reader(io.StringIO(raw), delimiter=delim))
    cut = p.stat().st_size > 5_000_000
    out = f"# {p.name}\n{'at least ' if cut else ''}{len(data)} rows x {max((len(r) for r in data), default=0)} columns\n\n" + _md_table(data[:rows])
    if len(data) > rows:
        out += f"\n[first {rows} of {len(data)} rows; use analyze_data for the full file]"
    if p.stat().st_size > 5_000_000:
        out += "\n[only the first 5 MB of this file were read; use analyze_data for the full file]"
    return out


# ---------- Power BI --------------------------------------------------------------------------------------------------

def _pbix(p: Path) -> str:
    out = [f"# {p.name}"]
    with zipfile.ZipFile(str(p)) as z:
        names = z.namelist()
        lay = next((n for n in names if n.lower() == "report/layout"), None)
        if lay:
            try:
                L = json.loads(z.read(lay).decode("utf-16-le", errors="replace").lstrip("\ufeff"))
                out.append(f"\n## Report: {len(L.get('sections') or [])} pages")
                for sec in L.get("sections") or []:
                    out.append(f"\n### Page: {sec.get('displayName', sec.get('name'))}")
                    for vc in sec.get("visualContainers") or []:
                        try:
                            cfg = json.loads(vc.get("config") or "{}")
                            sv = cfg.get("singleVisual") or {}
                            vt = sv.get("visualType", "visual")
                            fields = []
                            for role, proj in (sv.get("projections") or {}).items():
                                fields += [f"{role}: {x.get('queryRef')}" for x in proj]
                            title = ""
                            for o in ((sv.get("vcObjects") or {}).get("title") or []):
                                t = (((o.get("properties") or {}).get("text") or {}).get("expr") or {}).get("Literal", {})
                                title = str(t.get("Value", "")).strip("'")
                            out.append(f"- {vt}" + (f" \"{title}\"" if title else "") + (f": {'; '.join(fields)}" if fields else ""))
                        except ValueError:
                            continue
            except ValueError:
                out.append("(the report layout could not be read)")
        schema = next((n for n in names if n.lower() == "datamodelschema"), None)
        if schema:                                  # .pbit templates keep the model as JSON
            try:
                M = json.loads(z.read(schema).decode("utf-16-le", errors="replace").lstrip("\ufeff"))
                out.append(_model_from_schema(M.get("model") or {}))
            except ValueError:
                pass
    if p.suffix.lower() == ".pbix":
        try:
            out.append(_model_from_pbixray(p))
        except ImportError:
            out.append("\n(The data model needs the pbixray library, which is not in this install.)")
        except Exception as e:  # noqa: BLE001
            out.append(f"\n(The data model could not be decoded: {type(e).__name__}: {e})")
    return "\n".join(out)


def _model_from_schema(m: dict) -> str:
    out = ["\n## Data model"]
    for t in m.get("tables") or []:
        cols = [c.get("name") for c in t.get("columns") or [] if not str(c.get("name", "")).startswith("RowNumber")]
        out.append(f"\n### Table: {t.get('name')}\nColumns: {', '.join(cols)}")
        for ms in t.get("measures") or []:
            expr = ms.get("expression")
            expr = "\n".join(expr) if isinstance(expr, list) else expr
            out.append(f"- Measure {ms.get('name')} = {expr}")
    rels = m.get("relationships") or []
    if rels:
        out.append("\n### Relationships\n" + "\n".join(f"- {r.get('fromTable')}[{r.get('fromColumn')}] -> "
                                                       f"{r.get('toTable')}[{r.get('toColumn')}]" for r in rels))
    return "\n".join(out)


def _model_from_pbixray(p: Path, rows: int = 20) -> str:
    from pbixray import PBIXRay
    m = PBIXRay(str(p))
    out = ["\n## Data model"]
    try:
        out.append(f"Size: {m.size / 1e6:.1f} MB, tables: {', '.join(map(str, m.tables))}")
    except Exception:  # noqa: BLE001
        pass
    sch = getattr(m, "schema", None)
    if sch is not None and len(sch):
        out.append("\n### Columns\n" + _md_table([list(map(str, sch.columns))] + sch.head(400).astype(str).values.tolist()))
    for attr, title in (("dax_measures", "DAX measures"), ("dax_columns", "Calculated columns"),
                        ("relationships", "Relationships"), ("power_query", "Power Query (M)")):
        try:
            df = getattr(m, attr)
        except Exception:  # noqa: BLE001
            continue
        if df is None or not len(df):
            continue
        out.append(f"\n### {title}")
        for rec in df.to_dict("records")[:200]:
            out.append("- " + "; ".join(f"{k}: {v}" for k, v in rec.items() if v is not None and str(v) != "nan"))
    for t in list(m.tables)[:12]:
        try:
            df = m.get_table(t)
            out.append(f"\n### Sample of {t} ({len(df)} rows)\n" + _md_table([list(map(str, df.columns))] +
                                                                           df.head(rows).astype(str).values.tolist()))
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(out)


# ---------- other formats -----------------------------------------------------------------------------------------

def _xml_text(data: bytes) -> str:
    t = re.sub(rb"<text:(p|h)[^>]*>", b"\n", data)
    t = re.sub(rb"<table:table-row[^>]*>", b"\n", t)
    t = re.sub(rb"<table:table-cell[^>]*>", b" | ", t)
    end = t.rfind(b">") + 1
    t = re.sub(rb"<[^>]+>", b"", t[:end]) + t[end:]
    import html
    return html.unescape(t.decode("utf-8", errors="replace"))


def _odf(p: Path) -> str:
    with zipfile.ZipFile(str(p)) as z:
        return f"# {p.name}\n" + re.sub(r"\n{3,}", "\n\n", _xml_text(z.read("content.xml"))).strip()


_RTF_TOKEN = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([\s\S])|([{}])|([^\\{}\r\n]+)|[\r\n]+")
# groups whose text is not part of the document: tables of fonts, colours and styles, metadata, pictures, field codes
_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "listtable", "listoverridetable", "rsidtbl",
             "generator", "xmlnstbl", "themedata", "colorschememapping", "datastore", "latentstyles", "fldinst",
             "objdata", "header", "footer", "headerl", "headerr", "headerf", "footerl", "footerr", "footerf",
             "expandedcolortbl", "filetbl", "revtbl", "mmathPr", "pgdsctbl", "listtext", "pntext"}
_RTF_CHARS = {"par": "\n", "line": "\n", "sect": "\n\n", "page": "\n\n", "row": "\n", "cell": " | ", "tab": "\t",
              "emdash": "\u2014", "endash": "\u2013", "bullet": "\u2022", "lquote": "\u2018", "rquote": "\u2019",
              "ldblquote": "\u201c", "rdblquote": "\u201d", "emspace": " ", "enspace": " ", "qmspace": " "}


def _rtf_text(s: str) -> str:
    """The text of an RTF document: \\'e9 and \\u8364 decoded in the file's code page, the font, colour and style
    tables, pictures and field codes left out (a regex left "Helvetica;" and dropped every accented letter)."""
    out, pend = [], bytearray()
    codepage, uc, skip_n = "cp1252", 1, 0
    stack: list[tuple[bool, int]] = []
    skip = False
    star = False

    def flush():
        if pend:
            out.append(bytes(pend).decode(codepage, errors="replace"))
            pend.clear()
    for m in _RTF_TOKEN.finditer(s):
        word, arg, hexb, sym, brace, text = m.groups()
        if brace == "{":
            stack.append((skip, uc))
            star = False
            continue
        if brace == "}":
            flush()
            skip, uc = stack.pop() if stack else (False, 1)
            skip_n = 0
            continue
        if word is not None:
            if word in _RTF_SKIP or star:
                skip, star = True, False
            if skip:
                continue
            if word == "ansicpg" and arg:
                try:
                    codepage = "cp" + str(int(arg))
                    "".encode(codepage)
                except (LookupError, ValueError):
                    codepage = "cp1252"
            elif word == "uc" and arg:
                uc = max(0, int(arg))
            elif word == "u" and arg:
                flush()
                n = int(arg)
                out.append(chr(n + 65536 if n < 0 else n))
                skip_n = uc
            elif word in _RTF_CHARS:
                flush()
                out.append(_RTF_CHARS[word])
            continue
        if sym is not None:
            if sym == "*":
                star = True
                continue
            if skip:
                continue
            if skip_n:
                skip_n -= 1
                continue
            flush()
            out.append({"~": "\u00a0", "_": "-", "-": "", "\n": "\n", "\r": "\n"}.get(sym, sym))
            continue
        if skip:
            continue
        if hexb is not None:
            if skip_n:
                skip_n -= 1
            else:
                pend.append(int(hexb, 16))
            continue
        if text:
            flush()
            if skip_n:
                drop = min(skip_n, len(text))
                text, skip_n = text[drop:], skip_n - drop
            out.append(text)
    flush()
    return re.sub(r"\n{3,}", "\n\n", "".join(out))


def _rtf(p: Path) -> str:
    s = p.read_bytes()[:50_000_000].decode("latin-1")      # RTF is 7-bit: other bytes are escaped as \'hh
    return f"# {p.name}\n" + _rtf_text(s).strip()


def _ipynb(p: Path) -> str:
    nb = json.loads(p.read_text(encoding="utf-8"))
    out = [f"# {p.name}"]
    for i, c in enumerate(nb.get("cells") or [], 1):
        src = "".join(c.get("source") or [])
        if c.get("cell_type") == "code":
            out.append(f"\n## Cell {i} (code)\n```\n{src}\n```")
            for o in c.get("outputs") or []:
                txt = "".join(o.get("text") or (o.get("data") or {}).get("text/plain") or [])
                if txt:
                    out.append("Output:\n```\n" + txt[:2000] + "\n```")
        else:
            out.append(f"\n## Cell {i} ({c.get('cell_type')})\n{src}")
    return "\n".join(out)


def _eml(p: Path) -> str:
    import email
    from email import policy
    m = email.message_from_bytes(p.read_bytes(), policy=policy.default)
    head = "\n".join(f"{k}: {m[k]}" for k in ("From", "To", "Cc", "Date", "Subject") if m[k])
    body = m.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = _strip_html(text)
    atts = [a.get_filename() for a in m.iter_attachments()]
    return f"# {p.name}\n{head}\n\n{text}" + (f"\n\nAttachments: {', '.join(filter(None, atts))}" if atts else "")


def _strip_html(s: str) -> str:
    import html
    s = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", s)
    end = s.rfind(">") + 1                   # text after the last '>' holds no tag; keeps this linear on bad input
    s = re.sub(r"<[^>]+>", " ", s[:end]) + s[end:]
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()


HTML_MAX = 20_000_000


def _html(p: Path) -> str:
    with open(p, "rb") as fh:                      # a huge saved page or log: the start is read, not all of it
        raw = fh.read(HTML_MAX + 1)
    text = _strip_html(raw[:HTML_MAX].decode("utf-8", errors="replace"))
    if len(raw) > HTML_MAX:
        text += f"\n\n[only the first {HTML_MAX // 1_000_000} MB of this page were read]"
    return text


def _epub(p: Path) -> str:
    out = [f"# {p.name}"]
    with zipfile.ZipFile(str(p)) as z:
        for n in sorted(x for x in z.namelist() if x.lower().endswith((".xhtml", ".html", ".htm"))):
            t = _strip_html(z.read(n).decode("utf-8", errors="replace"))
            if t:
                out.append(t)
    return "\n\n".join(out)


def _msg(p: Path) -> str:
    """Outlook .msg: sender, recipients, subject, body and attachment names (OLE property streams)."""
    import olefile
    ole = olefile.OleFileIO(str(p))

    def prop(code: str, base: str = "") -> str:
        for suffix, enc in (("001F", "utf-16-le"), ("001E", "latin-1")):
            name = f"{base}__substg1.0_{code}{suffix}"
            if ole.exists(name):
                return ole.openstream(name).read().decode(enc, errors="replace").rstrip("\x00")
        return ""
    head = [f"From: {prop('0C1A')} <{prop('5D01') or prop('0C1F')}>", f"To: {prop('0E04')}", f"Cc: {prop('0E03')}",
            f"Subject: {prop('0037')}"]
    body = prop("1000")
    if not body:
        html = ""
        if ole.exists("__substg1.0_10130102"):
            html = ole.openstream("__substg1.0_10130102").read().decode("utf-8", errors="replace")
        body = _strip_html(html) if html else ""
    atts = []
    for entry in ole.listdir(streams=False, storages=True):
        if entry and entry[0].startswith("__attach_version1.0_") and len(entry) == 1:
            n = prop("3707", entry[0] + "/") or prop("3704", entry[0] + "/")
            if n:
                atts.append(n)
    ole.close()
    return f"# {p.name}\n" + "\n".join(h for h in head if not h.endswith(": ") and h != "From:  <>") + \
        f"\n\n{body.strip()}" + (f"\n\nAttachments: {', '.join(atts)}" if atts else "")


def _zip(p: Path) -> str:
    with zipfile.ZipFile(str(p)) as z:
        rows = [i for i in z.infolist() if not i.is_dir()]
        out = [f"# {p.name}\n{len(rows)} files. Extract it (for example with bash: unzip) to read one."]
        out += [f"- {i.filename} ({i.file_size:,} bytes)" for i in rows[:300]]
        if len(rows) > 300:
            out.append(f"... and {len(rows) - 300} more")
    return "\n".join(out)
