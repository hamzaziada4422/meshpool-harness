"""Figures in a PDF, Word or PowerPoint file: where they are, a note that says so, and the pages as pictures.

A chart drawn as a picture has no text, so a text reader misses it. Daniel (0.0.18, round 2) dropped a report with
three chart images and asked about "the barriers chart": the agent read only the text and said "The report has no
barriers chart". Now the reader says where the figures are, and a model that takes images gets the pages.

figures(path) -> [{"page": 3, "unit": "page", "what": "...", "head": "2. Barriers to adoption", "text": "..."}]
note(path, figs) -> the text that lists them, for every model
page_images(path, figs, cap) -> [{"page": 3, "mime": "image/png", "data": base64, "label": "..."}]
for_question(root, messages, question, see) -> (note, images) for a question that names a chart or figure
"""
from __future__ import annotations

import base64
import io
import re
import tempfile
import threading
from pathlib import Path

EXTS = {".pdf", ".docx", ".pptx"}
CAP = 6                         # page images per call
WIDTH = 1000                    # pixels across: about 120 dpi for A4, enough to read a chart's labels
SCAN_PAGES = 120                # pages looked at for figures in one file
FIG_WORDS = re.compile(r"\b(?:charts?|figures?|fig\.|graphs?|diagrams?|images?|pictures?|plots?|exhibits?|"
                       r"infographics?|visuals?|illustrations?)\b", re.I)
_MEMO: dict = {}
_LOCK = threading.Lock()


def asks_about_figures(text: str) -> bool:
    """True when a message names a chart, figure, graph, diagram or image."""
    return bool(FIG_WORDS.search(str(text or "").split("\n\n​")[0]))


def _head(text: str) -> str:
    for ln in str(text or "").splitlines():
        ln = " ".join(ln.split())
        if len(ln) >= 3:
            return ln[:60]
    return ""


# ---------- PDF -----------------------------------------------------------------------------------------------------
_PATH_OPS = re.compile(rb"[\s\d.\-](?:re|l|c|v|y|m)\s")


def _xobjects(res, depth: int = 0) -> tuple[list, int]:
    """(images as (width, height), path operators in forms) in a page's resources, forms included."""
    imgs, ops = [], 0
    if res is None or depth > 3:
        return imgs, ops
    try:
        res = res.get_object()
        xo = res.get("/XObject")
        xo = xo.get_object() if xo is not None else {}
    except Exception:  # noqa: BLE001
        return imgs, ops
    for v in list(xo.values())[:60]:
        try:
            o = v.get_object()
            sub = o.get("/Subtype")
            if sub == "/Image":
                imgs.append((int(o.get("/Width") or 0), int(o.get("/Height") or 0)))
            elif sub == "/Form":
                try:
                    ops += len(_PATH_OPS.findall(o.get_data()[:2_000_000]))
                except Exception:  # noqa: BLE001
                    pass
                a, b = _xobjects(o.get("/Resources"), depth + 1)
                imgs += a
                ops += b
        except Exception:  # noqa: BLE001
            continue
    return imgs, ops


def _pdf_figs(p: Path) -> list[dict]:
    from pypdf import PdfReader
    r = PdfReader(str(p))
    if r.is_encrypted:
        try:
            if not int(r.decrypt("")):
                return []
        except Exception:  # noqa: BLE001
            return []
    out = []
    for i, page in enumerate(r.pages[:SCAN_PAGES]):
        try:
            imgs, ops = _xobjects(page.get("/Resources"))
            big = [(w, h) for w, h in imgs if w >= 120 and h >= 80]
            try:
                data = page.get_contents().get_data() if page.get_contents() is not None else b""
            except Exception:  # noqa: BLE001
                data = b""
            ops += len(_PATH_OPS.findall(data[:2_000_000]))
            if not big and ops < 400:
                continue
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                text = ""
            words = len(text.split())
            if big:
                if words < 8:
                    continue                 # a scanned page: the reader OCRs it, it is not a figure on a text page
                pw, ph = float(page.mediabox.width or 1), float(page.mediabox.height or 1)
                if len(big) == 1 and big[0][0] >= 1000 and abs(big[0][0] / max(1, big[0][1]) - pw / ph) < 0.05:
                    continue                 # a searchable scan: one page-sized picture under an OCR text layer
                what = "a picture (a chart, diagram or photo)"
            elif words < 400:
                what = "a drawing (probably a chart or diagram)"
            else:
                continue                     # a long text page with table rules, not a figure
            out.append({"page": i + 1, "unit": "page", "what": what, "head": _head(text), "text": text.lower()})
        except Exception:  # noqa: BLE001 - one odd page never stops the rest
            continue
    return out


# ---------- PowerPoint and Word ---------------------------------------------------------------------------------------
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _pptx_figs(p: Path) -> list[dict]:
    from pptx import Presentation
    prs = Presentation(str(p))
    out = []
    for i, s in enumerate(prs.slides, 1):
        el = s._element
        pics = [x for x in el.iter(_P + "pic") if any(True for _ in x.iter(_A + "blip"))]
        if not pics:
            continue
        text = " ".join(t.text or "" for t in el.iter(_A + "t"))
        out.append({"page": i, "unit": "slide", "what": "a picture (a chart, diagram or photo)" if len(pics) == 1
                    else f"{len(pics)} pictures (charts, diagrams or photos)", "head": _head(text),
                    "text": text.lower()})
    return out


def _docx_figs(p: Path) -> list[dict]:
    """Pictures in a Word file. Pages are not known without laying it out: each is placed after its heading."""
    import docx
    d = docx.Document(str(p))
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    out, head, n = [], "", 0
    for para in d.element.body.iter(W + "p"):
        style = para.find(f"{W}pPr/{W}pStyle")
        t = "".join(x.text or "" for x in para.iter(W + "t")).strip()
        if t and style is not None and str(style.get(W + "val", "")).lower().startswith(("heading", "title")):
            head = t[:60]
        if any(True for _ in para.iter(_A + "blip")) or any(True for _ in para.iter("{urn:schemas-microsoft-com:vml}imagedata")):
            n += 1
            out.append({"page": None, "unit": "picture", "n": n, "what": "a picture (a chart, diagram or photo)",
                        "head": head, "text": head.lower()})
    return out


def figures(path) -> list[dict]:
    """Where the figures are in a PDF, Word or PowerPoint file ([] for none, or for any other file). Remembered per
    version of the file."""
    p = Path(path)
    if p.suffix.lower() not in EXTS:
        return []
    try:
        st = p.stat()
        key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    with _LOCK:
        if key in _MEMO:
            return [dict(f) for f in _MEMO[key]]
    try:
        fn = {".pdf": _pdf_figs, ".pptx": _pptx_figs, ".docx": _docx_figs}[p.suffix.lower()]
        figs = fn(p)
    except Exception:  # noqa: BLE001 - a reader problem means no note, never a failed read
        figs = []
    with _LOCK:
        if len(_MEMO) > 200:
            _MEMO.clear()
        _MEMO[key] = figs
    return [dict(f) for f in figs]


def in_pages(figs: list, pages: str | None) -> list:
    """The figures on the pages a read asked for ("1-3,7"); all when it named none."""
    if not pages:
        return figs
    want: set[int] = set()
    for part in str(pages).replace(" ", "").split(","):
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                want.update(range(int(a or 1), int(b or 100000) + 1))
            elif part:
                want.add(int(part))
        except ValueError:
            return figs
    return [f for f in figs if f.get("page") is None or f["page"] in want]


def _where(f: dict) -> str:
    if f.get("page") is None:
        return f"picture {f.get('n', 1)}" + (f" (after \"{f['head']}\")" if f.get("head") else "")
    return f"{f['unit']} {f['page']}" + (f" ({f['head']})" if f.get("head") else "")


def note(path, figs: list) -> str:
    """The figure note for every model, with images or without: where the figures are, and that a figure the model
    cannot see is still there."""
    if not figs:
        return ""
    name = Path(path).name
    lines = [f"[Figures] {name} has figures that are pictures, not text, so their numbers and labels may be missing "
             "from the text:"]
    lines += [f"- {_where(f)}: {f['what']}. See the page image." for f in figs[:20]]
    if len(figs) > 20:
        lines.append(f"- and {len(figs) - 20} more.")
    spots = [_where(f).split(" (")[0] for f in figs[:6]]
    where = spots[0] if len(spots) == 1 else ", ".join(spots[:-1]) + " and " + spots[-1]
    lines.append(f"When the page images are shown to you, read each figure from its image. When they are not, say "
                 f"that the file has a chart or figure on {where} that you cannot read as text. Never say that the "
                 "file has no chart or figure.")
    return "\n".join(lines)


# ---------- pictures of the pages -------------------------------------------------------------------------------------
def _render_pdf(pdf: Path, pages: list[int], width: int = WIDTH) -> dict[int, bytes]:
    import pypdfium2 as pdfium
    out: dict[int, bytes] = {}
    doc = pdfium.PdfDocument(str(pdf))
    try:
        for n in pages:
            if not 1 <= n <= len(doc):
                continue
            try:
                pg = doc[n - 1]
                im = pg.render(scale=width / max(1.0, pg.get_width())).to_pil().convert("RGB")
                buf = io.BytesIO()
                im.save(buf, "PNG", optimize=True)
                out[n] = buf.getvalue()
            except Exception:  # noqa: BLE001 - one bad page does not stop the rest
                continue
    finally:
        doc.close()
    return out


def _office_pdf(p: Path, tmp: str) -> Path | None:
    """The file as a PDF, through the kept LibreOffice (engines.forge.common.soffice_convert)."""
    import sys
    app = Path(__file__).resolve().parents[1]
    if str(app) not in sys.path:
        sys.path.insert(0, str(app))
    try:
        from engines.forge.common import soffice_convert
        pdf = soffice_convert(str(p), "pdf", tmp)
        return pdf if pdf and Path(pdf).is_file() else None
    except Exception:  # noqa: BLE001 - no LibreOffice: the note alone
        return None


def page_images(path, figs: list, cap: int = CAP, width: int = WIDTH) -> list[dict]:
    """The pages that hold these figures as PNG pictures, at most `cap`, in page order. [] when they cannot be drawn
    (no PDF renderer, no LibreOffice for a Word or PowerPoint file)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in EXTS or not figs:
        return []
    try:
        with tempfile.TemporaryDirectory(prefix="mh_fig_") as tmp:
            if ext == ".pdf":
                pdf, pages = p, sorted({f["page"] for f in figs if f.get("page")})
            else:
                pdf = _office_pdf(p, tmp)
                if pdf is None:
                    return []
                if ext == ".pptx":
                    pages = sorted({f["page"] for f in figs if f.get("page")})
                else:                         # Word: the pages are known once it is laid out
                    pages = [f["page"] for f in _pdf_figs(pdf)]
            pages = pages[:cap]
            got = _render_pdf(pdf, pages, width)
    except Exception:  # noqa: BLE001
        return []
    unit = "slide" if ext == ".pptx" else "page"
    return [{"page": n, "mime": "image/png", "data": base64.b64encode(got[n]).decode("ascii"),
             "label": f"{p.name}, {unit} {n}:"} for n in pages if n in got]


# ---------- a question about a chart -----------------------------------------------------------------------------------
_STOP = {"chart", "charts", "figure", "figures", "graph", "graphs", "diagram", "image", "images", "picture", "looking",
         "what", "which", "where", "when", "does", "show", "shows", "this", "that", "their", "there", "they", "with",
         "from", "about", "report", "file", "document", "deck", "slide", "page", "have", "your", "into", "them"}


def _words(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z][a-z-]{3,}", str(text or "").lower()) if w not in _STOP}


def docs_in_chat(root, messages: list, question: str = "") -> list[Path]:
    """The PDF, Word and PowerPoint files this chat is about, newest first: named in the question, attached to a
    message ("Attached files (in ...): a.pdf, b.docx") or read with read_document."""
    root = Path(root or ".")
    seen: list[Path] = []

    def add(name: str):
        name = str(name or "").strip().strip("`'\"")
        if not name or Path(name).suffix.lower() not in EXTS:
            return
        q = Path(name)
        q = q if q.is_absolute() else root / q
        try:
            if q.is_file() and q.resolve() not in [x.resolve() for x in seen]:
                seen.append(q)
        except OSError:
            pass
    own = str(question or "").split("\n\n​")[0]
    for m in re.findall(r"[\w .()\-]+?\.(?:pdf|docx|pptx)\b", own, re.I):
        words = m.split()                     # "the chart in report.pdf": the name is the shortest tail that exists
        for k in range(1, len(words) + 1):
            n = len(seen)
            add(" ".join(words[-k:]))
            if len(seen) > n:
                break
    if seen:
        return seen[:2]
    import json
    for m in reversed(messages or []):
        c = m.get("content")
        text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict)) \
            if isinstance(c, list) else ""
        if m.get("role") == "user":
            for line in re.findall(r"Attached files \(in [^)]*\): ([^\n]+)", text):
                for nm in line.split(", "):
                    add(nm)
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if str(fn.get("name", "")).endswith("read_document"):
                try:
                    add((json.loads(fn.get("arguments") or "{}") or {}).get("path", ""))
                except (ValueError, AttributeError):
                    pass
        if len(seen) >= 2:
            break
    return seen[:2]


TEXT_CAP = 30_000               # characters of an attached file's text that go with the message


def chat_folder_files(question: str) -> list[str]:
    """The names on the "Attached files (in this chat's folder): ..." line of a message: files attached to a chat that
    has no folder of its own choosing."""
    own = str(question or "").split("\n\n​")[0]
    out: list[str] = []
    for line in re.findall(r"Attached files \(in this chat's folder\): ([^\n]+)", own):
        out += [x.strip() for x in line.split(", ") if x.strip()]
    return out


def for_attachments(root, question: str, see: bool, cap: int = CAP) -> tuple[str, list]:
    """A chat with no folder of its own: its PDF, Word and PowerPoint attachments reach the model as text, with the
    pages that hold figures as images (Daniel, round 3: the model saw three chart pages, no text, and asked him to
    paste the report). ("", []) when the message has no such file."""
    docs = []
    for name in chat_folder_files(question):
        q = Path(name)
        q = q if q.is_absolute() else Path(root or ".") / q
        try:
            if q.suffix.lower() in EXTS and q.is_file():
                docs.append(q)
        except OSError:
            pass
    if not docs:
        return "", []
    from meshpool_harness import docread
    parts = []
    for d in docs[:2]:
        try:
            text = docread.extract(d, max_chars=TEXT_CAP)
        except Exception:  # noqa: BLE001 - a file that cannot be read is left to read_document
            continue
        text = str(text or "").strip()
        if not text:
            continue
        more = ("\n[Meshpool] The text above is cut here. Read the rest with read_document." if len(text) >= TEXT_CAP
                else "")
        parts.append(f"[Meshpool] The text of the attached file {d.name}:\n{text}{more}")
    note_, images = for_question(root, [], question, see, cap, force=True)
    return "\n\n".join(parts + ([note_] if note_ else [])), images


def for_question(root, messages: list, question: str, see: bool, cap: int = CAP, force: bool = False) -> tuple[str, list]:
    """For a message that names a chart or figure: (a note on where the figures are in this chat's documents, the
    page pictures when the model takes images). ("", []) for any other message, or when no document has figures.
    force: for a message that names no figure but comes with an attached file (for_attachments)."""
    if not force and not asks_about_figures(question):
        return "", []
    want = _words(question)
    notes, images = [], []
    for doc in docs_in_chat(root, messages, question):
        figs = figures(doc)
        if not figs:
            continue
        ranked = sorted(figs, key=lambda f: (-len(want & _words(f.get("text", ""))), f.get("page") or 0))
        pick = sorted(ranked[:max(1, cap - len(images))], key=lambda f: f.get("page") or 0)
        got = page_images(doc, pick, cap=max(0, cap - len(images))) if see and len(images) < cap else []
        images += got
        n = note(doc, figs)
        if see and got:
            n += "\nThe pages with figures follow as images: " + ", ".join(g["label"].rstrip(":") for g in got) + "."
        elif see:
            n += "\nThe pages could not be drawn as pictures here, so you cannot see the figures."
        else:
            n += "\nThis model cannot see images, so you cannot see the figures."
        notes.append(n)
    if not notes:
        return "", []
    head = "The attached file has figures." if force else "The user asks about a chart or figure."
    return f"[Meshpool] {head}\n" + "\n\n".join(notes), images[:cap]
