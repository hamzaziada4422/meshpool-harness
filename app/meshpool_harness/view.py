"""Look at anything visual: turn a file the agent made (web page, SVG, image, PDF, slides, workbook, document, CSV)
into PNG pictures it can inspect. Models that can see get the pictures; models that cannot get the text read out of
them (OCR) plus the page size, so every visual output can still be checked.

render(path, pages, out_dir, launch) -> {"shots": [(label, Path)], "notes": [str]}
"""
from __future__ import annotations

import csv
import html
import tempfile
from pathlib import Path

IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
WEB = {".html", ".htm", ".svg"}
OFFICE = {".pptx", ".ppt", ".docx", ".doc", ".odt", ".odp", ".ods", ".xlsx", ".xls", ".rtf"}
TABLE = {".csv", ".tsv"}
MAX_PAGES = 8


def kind(path: str) -> str:
    s = Path(path).suffix.lower()
    if s in IMAGES:
        return "image"
    if s in WEB:
        return "web"
    if s == ".pdf":
        return "pdf"
    if s in OFFICE:
        return "office"
    if s in TABLE:
        return "table"
    return ""


def _pick(pages, n: int) -> list[int]:
    """0-based page numbers: all up to MAX_PAGES by default, or the ones asked for ("1,3" or [1, 3] or "2-4")."""
    if not pages:
        return list(range(min(n, MAX_PAGES)))
    want: list[int] = []
    items = pages if isinstance(pages, (list, tuple)) else str(pages).split(",")
    for it in items:
        s = str(it).strip()
        if "-" in s:
            a, _, b = s.partition("-")
            if a.strip().isdigit() and b.strip().isdigit():
                want += range(int(a), int(b) + 1)
        elif s.isdigit():
            want.append(int(s))
    return [p - 1 for p in dict.fromkeys(want) if 0 < p <= n][:MAX_PAGES]


def _pdf_pages(pdf: Path, pages, out: Path, stem: str, width: int = 1200) -> tuple[list, list]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    try:
        n = len(doc)
        shots = []
        for i in _pick(pages, n):
            page = doc[i]
            f = out / f"{stem}-p{i + 1:02d}.png"
            page.render(scale=width / page.get_width()).to_pil().convert("RGB").save(f, optimize=True)
            shots.append((f"page {i + 1} of {n}", f))
        notes = [f"{n} page{'s' if n != 1 else ''} in all."] + ([f"Showing the first {MAX_PAGES}; ask for others with pages."] if n > MAX_PAGES and not pages else [])
        return shots, notes
    finally:
        doc.close()


def _table_html(path: Path) -> str:
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.reader(fh, delimiter=delim))[:200]
    head, body = (rows[0], rows[1:]) if rows else ([], [])
    tr = lambda cells, tag: "<tr>" + "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in cells) + "</tr>"
    return ("<!doctype html><meta charset=utf-8><style>body{font:14px system-ui;margin:16px}table{border-collapse:collapse}"
            "td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}th{background:#f3f3f3}</style><table>"
            + tr(head, "th") + "".join(tr(r, "td") for r in body) + "</table>")


def _screenshot_page(launch, url: str, out_file: Path, width: int, full: bool = True):
    browser = launch()
    try:
        ctx = browser.new_context(viewport={"width": width, "height": 800}, device_scale_factor=1)
        page = ctx.new_page()
        page.goto(url, wait_until="load", timeout=20000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(out_file), full_page=full)
        ctx.close()
    finally:
        browser.close()


def render(path: str, pages=None, out_dir: Path | None = None, launch=None, width: int = 1280) -> dict:
    p = Path(path).expanduser()
    if not p.is_absolute():
        import os
        p = Path(os.environ.get("MESHPOOL_PROJECT") or Path.cwd()) / p
    if not p.exists():
        raise FileNotFoundError(f"There is no file at {p}.")
    k = kind(str(p))
    if not k:
        raise ValueError(f"I can't draw a {p.suffix or 'file like this'}. It works for web pages, SVG, images, PDF, "
                         "slides, documents, workbooks and CSV.")
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="view_"))
    out.mkdir(parents=True, exist_ok=True)
    stem = p.stem
    shots: list = []
    notes: list[str] = []
    if k == "image":
        from PIL import Image
        with Image.open(p) as im:
            notes.append(f"Image, {im.width} by {im.height} pixels.")
            f = out / f"{stem}.png"
            im.convert("RGB").save(f, optimize=True)
        shots.append(("image", f))
    elif k == "pdf":
        shots, notes = _pdf_pages(p, pages, out, stem)
    elif k == "office":
        from engines.forge.common import soffice_convert
        with tempfile.TemporaryDirectory() as tmp:
            pdf = soffice_convert(str(p), "pdf", tmp)
            shots, notes = _pdf_pages(pdf, pages, out, stem)
    elif k == "web":
        if launch is None:
            raise RuntimeError("No browser is available to draw this page.")
        f = out / f"{stem}.png"
        url = p.resolve().as_uri()
        if p.suffix.lower() == ".svg":                       # a bare SVG document cannot be screenshotted whole
            wrap = out / f"{stem}-svg.html"
            wrap.write_text(f'<!doctype html><body style="margin:0;background:#fff"><img src="{url}" style="max-width:100%"></body>', encoding="utf-8")
            url = wrap.as_uri()
        _screenshot_page(launch, url, f, width)
        shots.append((f"full page at {width} px wide", f))
    elif k == "table":
        if launch is None:
            raise RuntimeError("No browser is available to draw this table.")
        tmp_html = out / f"{stem}-table.html"
        tmp_html.write_text(_table_html(p), encoding="utf-8")
        f = out / f"{stem}.png"
        _screenshot_page(launch, tmp_html.as_uri(), f, 1100)
        shots.append(("first 200 rows", f))
    return {"kind": k, "shots": shots, "notes": notes, "file": str(p)}


def reading(shots: list) -> str:
    """The text in each picture (OCR), for models that cannot see images. Empty when OCR is not installed."""
    try:
        from meshpool_harness import ocr
        if not ocr.available():
            return ""
    except Exception:  # noqa: BLE001
        return ""
    parts = []
    for label, f in shots[:MAX_PAGES]:
        parts.append(f"[{label}] " + ocr.describe(f, Path(f).name))
    return "\n".join(parts)
