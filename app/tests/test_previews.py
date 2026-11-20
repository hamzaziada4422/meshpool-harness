"""In-app file previews (ui/previews.py, GET /api/preview): root check, tables, PDF pages, Office -> PDF -> PNG, cache."""
import io
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from meshpool_harness import office_runtime
from meshpool_harness.ui import previews as P
from meshpool_harness.ui import server as S
from meshpool_harness.ui import workbench as W

PNG = b"\x89PNG\r\n\x1a\n"
needs_office = pytest.mark.skipif(
    not (os.environ.get("MH_ALLOW_SYSTEM_SOFFICE") == "1" and shutil.which("soffice")) and not office_runtime.find_soffice(),
    reason="LibreOffice is not available here (set MH_ALLOW_SYSTEM_SOFFICE=1 with soffice on PATH, or use the bundled copy)")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(W, "known_roots", lambda: [root])      # the root check itself stays the real one
    P._JOBS.clear()
    return root


@pytest.fixture
def http(proj):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def get(root, path, **extra):
        q = "&".join(f"{k}={v}" for k, v in {"root": root, "path": path, **extra}.items())
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/preview?{q}", headers={"X-Meshpool-Token": S.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()
    yield get
    httpd.shutdown()


def make_pdf(path: Path, pages=3):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(400, 300)
    pdf.save(str(path))
    pdf.close()


def make_deck(path: Path, slides=3):
    from pptx import Presentation
    pr = Presentation()
    for i in range(slides):
        s = pr.slides.add_slide(pr.slide_layouts[1])
        s.shapes.title.text = f"Slide {i + 1}"
        s.placeholders[1].text = "Point one\nPoint two"
    pr.save(str(path))


def wait_ready(root, rel, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = P.meta(str(root), rel)
        if m["state"] != "working":
            return m
        time.sleep(0.4)
    raise AssertionError("conversion did not finish")


# ------------------------------------------------------------------ the root check
def test_paths_outside_the_root_are_refused(proj, tmp_path, http):
    (tmp_path / "secret.txt").write_text("nope")
    (proj / "ok.txt").write_text("fine")
    for bad in ("../secret.txt", str(tmp_path / "secret.txt"), "../../etc/passwd"):
        with pytest.raises(ValueError):
            P.meta(str(proj), bad)
        code, _, body = http(str(proj), bad)
        assert code == 400 and b"outside" in body.lower()
        code, _, _ = http(str(proj), bad, page=0)
        assert code == 400
    code, _, body = http(str(tmp_path), "secret.txt")                     # a folder that is not a project
    assert code == 400 and b"not one of your projects" in body
    code, _, body = http(str(proj), "ok.txt")
    assert code == 200 and json.loads(body)["text"] == "fine"


def test_a_symlink_out_of_the_root_is_refused(proj, tmp_path):
    (tmp_path / "secret.txt").write_text("nope")
    (proj / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(ValueError):
        P.meta(str(proj), "link.txt")


def test_the_endpoint_needs_the_token(proj):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        (proj / "a.txt").write_text("x")
        base = f"http://127.0.0.1:{httpd.server_address[1]}/api/preview?root={proj}&path=a.txt"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base, timeout=5)
        assert e.value.code == 403
        assert urllib.request.urlopen(base + "&t=" + S.TOKEN, timeout=5).status == 200      # the <img> form
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ tables
def test_xlsx_becomes_sheets_of_rows(proj):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["Region", "Q1", "Q2", "Total"])
    ws.append(["North", 100, 120.5, "=B2+C2"])
    wb.create_sheet("<b>Notes").append(["<script>alert(1)</script>", None])
    wb.save(proj / "m.xlsx")
    m = P.meta(str(proj), "m.xlsx")
    assert m["kind"] == "sheet" or "sheets" in m
    assert [s["name"] for s in m["sheets"]] == ["Revenue", "<b>Notes"]
    assert m["sheets"][0]["rows"][0] == ["Region", "Q1", "Q2", "Total"]
    assert m["sheets"][0]["rows"][1][:3] == ["North", "100", "120.5"]
    assert m["sheets"][1]["rows"][0][0] == "<script>alert(1)</script>"       # raw text: the page sets it with textContent


def test_xlsx_is_capped_at_500_rows_and_50_columns(proj):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in range(700):
        ws.append(list(range(60)))
    wb.save(proj / "big.xlsx")
    s = P.meta(str(proj), "big.xlsx")["sheets"][0]
    assert len(s["rows"]) == 500 and s["cols"] == 50 and s["more"] and s["wide"]


def test_csv_and_tsv_become_a_table(proj):
    (proj / "d.csv").write_text("name;score\nann;3\nbob;4\n", encoding="utf-8")
    (proj / "d.tsv").write_text("a\tb\n1\t2\n", encoding="utf-8")
    assert P.meta(str(proj), "d.csv")["sheets"][0]["rows"] == [["name", "score"], ["ann", "3"], ["bob", "4"]]
    assert P.meta(str(proj), "d.tsv")["sheets"][0]["rows"] == [["a", "b"], ["1", "2"]]


def test_text_html_markdown_and_binary(proj):
    (proj / "p.html").write_text("<h1>x</h1>")
    (proj / "n.md").write_text("# T")
    (proj / "a.py").write_text("print(1)")
    (proj / "b.dat").write_bytes(b"\0\1\2")
    (proj / "i.svg").write_text("<svg/>")
    assert P.meta(str(proj), "p.html")["kind"] == "html"
    assert P.meta(str(proj), "n.md")["kind"] == "md"
    assert P.meta(str(proj), "a.py")["text"] == "print(1)"
    assert P.meta(str(proj), "b.dat")["kind"] == "none"
    m = P.meta(str(proj), "i.svg")
    assert m["kind"] == "image" and "text" not in m


# ------------------------------------------------------------------ pdf
def test_pdf_page_count_and_png(proj, http):
    make_pdf(proj / "r.pdf", 3)
    m = P.meta(str(proj), "r.pdf")
    assert m["kind"] == "pages" and m["pages"] == 3 and m["state"] == "ready"
    code, ctype, body = http(str(proj), "r.pdf", page=1, w=800)
    assert code == 200 and ctype == "image/png" and body.startswith(PNG)
    from PIL import Image
    assert Image.open(io.BytesIO(body)).size[0] == 800
    code, _, _ = http(str(proj), "r.pdf", page=3)                           # past the end
    assert code == 400


def test_pages_are_capped_in_count_and_width(proj):
    make_pdf(proj / "long.pdf", P.MAX_PAGES + 5)
    m = P.meta(str(proj), "long.pdf")
    assert m["pages"] == P.MAX_PAGES and m["total"] == P.MAX_PAGES + 5
    from PIL import Image
    assert Image.open(io.BytesIO(P.page_png(str(proj), "long.pdf", 0, 99999))).size[0] <= P.MAX_WIDTH
    with pytest.raises(ValueError):
        P.page_png(str(proj), "long.pdf", P.MAX_PAGES)


def test_a_damaged_pdf_is_an_error_not_a_crash(proj, http):
    (proj / "bad.pdf").write_bytes(b"%PDF-1.4 not really")
    code, _, body = http(str(proj), "bad.pdf")
    assert code in (400, 500) and json.loads(body)["error"]


def test_png_cache_is_reused_and_a_changed_file_is_rendered_again(proj):
    make_pdf(proj / "c.pdf", 2)
    a = P.page_png(str(proj), "c.pdf", 0, 600)
    files = list(P._dir().rglob("p0_600.png"))
    assert len(files) == 1
    stamp = files[0].stat().st_mtime_ns
    time.sleep(0.02)
    assert P.page_png(str(proj), "c.pdf", 0, 600) == a
    assert list(P._dir().rglob("p0_600.png"))[0].read_bytes() == a
    make_pdf(proj / "c.pdf", 4)                                              # edited: new size and mtime, new key
    assert P.meta(str(proj), "c.pdf")["pages"] == 4
    P.page_png(str(proj), "c.pdf", 0, 600)
    assert len(list(P._dir().rglob("p0_600.png"))) == 2 and stamp


# ------------------------------------------------------------------ Office
@needs_office
def test_pptx_renders_to_page_images(proj, http):
    make_deck(proj / "deck.pptx", 3)
    t0 = time.time()
    first = P.meta(str(proj), "deck.pptx")
    assert first["kind"] == "pages" and first["state"] == "working"
    assert time.time() - t0 < 5, "the first call must return at once, not wait for LibreOffice"
    m = wait_ready(proj, "deck.pptx")
    assert m["state"] == "ready" and m["pages"] == 3
    code, ctype, body = http(str(proj), "deck.pptx", page=2, w=1000)
    assert code == 200 and ctype == "image/png" and body.startswith(PNG)
    assert len(body) > 2000


@needs_office
def test_office_conversion_is_cached_and_not_repeated(proj, monkeypatch):
    make_deck(proj / "d2.pptx", 2)
    wait_ready(proj, "d2.pptx")
    calls = []
    import engines.forge.common as FC
    real = FC.soffice_convert
    monkeypatch.setattr(FC, "soffice_convert", lambda *a, **k: calls.append(a) or real(*a, **k))
    m = P.meta(str(proj), "d2.pptx")
    assert m["state"] == "ready" and m["pages"] == 2 and not calls            # served from the cache, no second conversion
    assert P.page_png(str(proj), "d2.pptx", 1, 500).startswith(PNG)
    assert not calls
    make_deck(proj / "d2.pptx", 4)                                           # edited: converted again
    assert wait_ready(proj, "d2.pptx")["pages"] == 4 and len(calls) == 1


@needs_office
def test_docx_renders(proj):
    from docx import Document
    d = Document()
    d.add_heading("Hello", 1)
    d.add_paragraph("A paragraph of text.")
    d.save(proj / "r.docx")
    assert wait_ready(proj, "r.docx")["pages"] >= 1


def test_office_conversions_are_one_at_a_time(proj, monkeypatch):
    """Two decks asked for together: the second waits for the first (LibreOffice is heavy), neither blocks the caller."""
    import engines.forge.common as FC
    make_deck(proj / "a.pptx", 1)
    make_deck(proj / "b.pptx", 1)
    active, peak = [0], [0]
    lock = threading.Lock()

    def fake(src, to, out, timeout=0):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.4)
        with lock:
            active[0] -= 1
        Path(out).mkdir(parents=True, exist_ok=True)
        make_pdf(Path(out) / (Path(src).stem + ".pdf"), 1)
        return Path(out) / (Path(src).stem + ".pdf")
    monkeypatch.setattr(FC, "soffice_convert", fake)
    monkeypatch.setattr("meshpool_harness.office_runtime.find_soffice", lambda: "/fake/soffice")
    t0 = time.time()
    assert P.meta(str(proj), "a.pptx")["state"] == "working" and P.meta(str(proj), "b.pptx")["state"] == "working"
    assert time.time() - t0 < 1
    assert wait_ready(proj, "a.pptx")["state"] == "ready" and wait_ready(proj, "b.pptx")["state"] == "ready"
    assert peak[0] == 1


def test_a_conversion_that_fails_or_times_out_is_an_error_state(proj, monkeypatch):
    import subprocess

    import engines.forge.common as FC
    make_deck(proj / "x.pptx", 1)
    monkeypatch.setattr("meshpool_harness.office_runtime.find_soffice", lambda: "/fake/soffice")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("soffice", 1)
    monkeypatch.setattr(FC, "soffice_convert", boom)
    P.meta(str(proj), "x.pptx")
    m = wait_ready(proj, "x.pptx")
    assert m["state"] == "error" and "took longer" in m["error"]


def test_without_libreoffice_office_files_say_so(proj, monkeypatch):
    make_deck(proj / "x.pptx", 1)
    monkeypatch.setattr("meshpool_harness.office_runtime.find_soffice", lambda: None)
    m = P.meta(str(proj), "x.pptx")
    assert m["state"] == "error" and "LibreOffice" in m["error"]
