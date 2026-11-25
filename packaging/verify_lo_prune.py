"""Proof that a pruned LibreOffice still does everything Meshpool asks of it (packaging/lo_prune.py).

Usage (needs the dev venv: python-pptx, python-docx, openpyxl, Pillow, pypdfium2):
    python packaging/verify_lo_prune.py /path/to/pruned/soffice [--compare /path/to/full/soffice]

It builds a PowerPoint (text, picture, native chart, a Segoe UI run), an Excel workbook with formulas and a Word file,
then runs the same code paths the app uses, with MH_SOFFICE pointing at the given soffice:
  pptx -> pdf and pptx -> png   engines.forge.common.soffice_convert (the render checks and the slide previews)
  xlsx -> recalculated xlsx     engines.sheets.qa.recalc and engines.forge.validate_xlsx.recalc
  docx -> pdf, pdf -> docx      office_mcp_server.convert_document
and checks the results (page count, text, picture and chart pixels, formula values, embedded fonts). With --compare the
slides are also drawn by the unpruned copy and the two images must agree. Exit status 0 = everything passed.
"""
from __future__ import annotations

import argparse
import os
import warnings
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def make_files(d: Path) -> dict[str, Path]:
    from PIL import Image, ImageDraw
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches, Pt

    img = Image.new("RGB", (400, 240), (30, 90, 160))
    ImageDraw.Draw(img).ellipse((120, 40, 280, 200), fill=(240, 180, 40))
    img.save(d / "pic.png")
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Quarterly results"
    tb = s.shapes.add_textbox(Inches(0.6), Inches(1.4), Inches(6), Inches(1.2))
    p = tb.text_frame.paragraphs[0]
    r = p.add_run()
    r.text = "Revenue grew 18% on Segoe UI body text"
    r.font.name, r.font.size = "Segoe UI", Pt(22)
    s.shapes.add_picture(str(d / "pic.png"), Inches(0.6), Inches(3.0), Inches(4.5))
    cd = CategoryChartData()
    cd.categories = ["Q1", "Q2", "Q3", "Q4"]
    cd.add_series("Revenue", (12, 15, 19, 24))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(7), Inches(1.4), Inches(5.8), Inches(4.5), cd)
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Second slide"
    body = s2.placeholders[1].text_frame
    body.text = "Bullet one"
    for txt, lvl in (("Sub point", 1), ("Bullet two", 0)):
        para = body.add_paragraph()
        para.text, para.level = txt, lvl
    s2.notes_slide.notes_text_frame.text = "Speaker notes for the second slide"
    # third slide: a table, shapes with gradient and arrow, a pie chart, Arabic text, a hyperlink
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    s3 = prs.slides.add_slide(prs.slide_layouts[6])
    tbl = s3.shapes.add_table(4, 3, Inches(0.5), Inches(0.5), Inches(6), Inches(2)).table
    for i in range(4):
        for j in range(3):
            tbl.cell(i, j).text = f"cell {i}.{j}"
    box = s3.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(7), Inches(0.5), Inches(3), Inches(1.2))
    box.fill.gradient()
    box.fill.gradient_angle = 90
    box.fill.gradient_stops[0].color.rgb = RGBColor(0x20, 0x60, 0xA0)
    box.fill.gradient_stops[1].color.rgb = RGBColor(0xA0, 0xD0, 0xF0)
    box.text_frame.text = "Rounded box"
    s3.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(10.4), Inches(0.5), Inches(2), Inches(1.2))
    ar = s3.shapes.add_textbox(Inches(0.5), Inches(3), Inches(6), Inches(1))
    rr = ar.text_frame.paragraphs[0].add_run()
    rr.text = "\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645"
    rr.font.name, rr.font.size = "Noto Sans Arabic", Pt(24)
    link = s3.shapes.add_textbox(Inches(0.5), Inches(4.2), Inches(6), Inches(0.6)).text_frame.paragraphs[0].add_run()
    link.text = "A hyperlink"
    link.hyperlink.address = "https://example.com"
    pc = CategoryChartData()
    pc.categories = ["A", "B", "C"]
    pc.add_series("Share", (50, 30, 20))
    s3.shapes.add_chart(XL_CHART_TYPE.PIE, Inches(7), Inches(2.2), Inches(5), Inches(4.5), pc)
    pptx = d / "deck.pptx"
    prs.save(pptx)

    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import PatternFill
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation
    import datetime
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    for i in range(1, 6):
        ws[f"A{i}"] = i * 10
        ws[f"B{i}"] = f"=A{i}*1.5"
        ws[f"F{i}"] = ["north", "south", "north", "east", "south"][i - 1]
    ws["A7"] = "=SUM(A1:A5)"
    ws["B7"] = "=SUM(B1:B5)"
    ws["C7"] = "=IF(B7>200,\"big\",\"small\")"
    ws["D7"] = "=NPV(0.1,A1:A5)"
    ws["E7"] = "=VLOOKUP(30,A1:B5,2,FALSE)"
    ws["A8"] = datetime.date(2026, 1, 31)
    ws["B8"] = "=EDATE(A8,1)"                         # Calc's Analysis add-in functions (EDATE, EOMONTH, NETWORKDAYS ...)
    ws["C8"] = "=EOMONTH(A8,1)"
    ws["D8"] = "=NETWORKDAYS(A8,A8+13)"
    ws["E8"] = "=SUMIFS(A1:A5,F1:F5,\"north\")"
    ws["A9"] = "=IFERROR(INDEX(F1:F5,MATCH(40,A1:A5,0)),\"none\")"
    ws["B9"] = "=TEXT(1234.5,\"#,##0.00\")"
    ws["C9"] = "=SUMPRODUCT((F1:F5=\"south\")*A1:A5)"
    ws["D9"] = "=Total_A*2"
    wb.defined_names["Total_A"] = DefinedName("Total_A", attr_text="Model!$A$7")
    ws.conditional_formatting.add("A1:A5", CellIsRule(operator="greaterThan", formula=["25"], fill=PatternFill("solid", bgColor="FFCC00")))
    dv = DataValidation(type="list", formula1='"north,south,east"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("F1:F5")
    ws.auto_filter.ref = "A1:B5"
    ch = BarChart()
    ch.add_data(Reference(ws, min_col=1, min_row=1, max_row=5))
    ws.add_chart(ch, "H2")
    ws2 = wb.create_sheet("Other")
    ws2["A1"] = "=Model!A7+Model!B7"
    xlsx = d / "book.xlsx"
    wb.save(xlsx)

    import docx
    from docx.shared import Inches as DI
    doc = docx.Document()
    sec = doc.sections[0]
    sec.header.paragraphs[0].text = "Header text"
    sec.footer.paragraphs[0].text = "Footer text"
    doc.add_heading("Proposal", 1)
    doc.add_paragraph("The quick brown fox jumps over the lazy dog. " * 6)
    doc.add_paragraph("First item", style="List Number")
    doc.add_paragraph("Second item", style="List Number")
    doc.add_picture(str(d / "pic.png"), width=DI(2.5))
    t = doc.add_table(rows=3, cols=3)
    t.style = "Table Grid"
    for i, row in enumerate(t.rows):
        for j, c in enumerate(row.cells):
            c.text = f"r{i}c{j}"
    t.cell(0, 0).merge(t.cell(0, 1))
    doc.add_page_break()
    doc.add_heading("Appendix", 2)
    doc.add_paragraph("Second page text")
    docx_path = d / "memo.docx"
    doc.save(docx_path)
    return {"pptx": pptx, "xlsx": xlsx, "docx": docx_path}


def pdf_text_and_pages(pdf: Path) -> tuple[int, str]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    txt = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    return len(doc), txt


def pdf_fonts(pdf: Path) -> str:
    exe = shutil.which("pdffonts")
    if not exe:
        return ""
    return subprocess.run([exe, str(pdf)], capture_output=True, text=True).stdout


def render_first_page(pdf: Path, scale: float = 1.0):
    import pypdfium2 as pdfium
    return pdfium.PdfDocument(str(pdf))[0].render(scale=scale).to_pil().convert("RGB")


def colour_share(im, rgb, tol=40) -> float:
    px = im.getdata()
    n = sum(1 for p in px if all(abs(p[i] - rgb[i]) <= tol for i in range(3)))
    return n / (im.width * im.height)


def run(soffice: str, work: Path, files: dict[str, Path]) -> dict:
    """Run every conversion with MH_SOFFICE=soffice. Returns {name: (ok, detail)} and the outputs."""
    os.environ["MH_SOFFICE"] = soffice
    os.environ.pop("MH_ALLOW_SYSTEM_SOFFICE", None)
    from engines.forge.common import soffice_convert
    from engines.forge import validate_xlsx
    from engines.sheets import qa
    res: dict = {}
    out = work / "out"
    out.mkdir(parents=True, exist_ok=True)

    def step(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {str(e)[-300:]}"
        res[name] = (ok, detail)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)

    def pptx_pdf():
        pdf = soffice_convert(str(files["pptx"]), "pdf", str(out))
        n, txt = pdf_text_and_pages(pdf)
        res["pptx.pdf"] = pdf
        fonts = pdf_fonts(pdf)
        im = render_first_page(pdf)
        im.save(out / "deck-page1.png")
        res["page1"] = im
        blue, gold = colour_share(im, (30, 90, 160)), colour_share(im, (240, 180, 40))
        ok = (n == 3 and "Quarterly results" in txt and "Revenue grew" in txt and "Second slide" in txt
              and "cell 3.2" in txt and "Rounded box" in txt and "Sub point" in txt and blue > 0.01 and gold > 0.003)
        return ok, f"{n} pages, picture pixels blue {blue:.3f} gold {gold:.3f}; fonts: " + \
            ", ".join(sorted({ln.split()[0].split('+')[-1] for ln in fonts.splitlines()[2:] if ln.strip()})[:6])

    def chart_drawn():
        im = res.get("page1")
        if im is None:
            return False, "no render"
        # the chart is on the right half; python-pptx's default series colour is an accent blue-ish tone: count
        # non-white, non-text pixels in the chart area
        box = im.crop((int(im.width * 0.52), int(im.height * 0.18), int(im.width * 0.98), int(im.height * 0.80)))
        nonwhite = sum(1 for p in box.getdata() if sum(p) < 720) / (box.width * box.height)
        return nonwhite > 0.03, f"{nonwhite:.3f} of the chart area is drawn"

    def segoe_to_selawik():
        pdf = res.get("pptx.pdf")
        if not pdf:
            return False, "no pdf"
        fonts = pdf_fonts(Path(pdf))
        if not fonts:
            return True, "pdffonts not installed, not checked"
        return "Selawik" in fonts, "Selawik embedded" if "Selawik" in fonts else fonts[-300:]

    def pptx_png():
        png = soffice_convert(str(files["pptx"]), "png", str(out))
        from PIL import Image
        im = Image.open(png).convert("RGB")
        blue = colour_share(im, (30, 90, 160))
        return im.width >= 400 and blue > 0.01, f"{im.width}x{im.height}, picture pixels blue {blue:.3f}"

    def xlsx_recalc(fn_name, fn):
        def go():
            outp = fn()
            from openpyxl import load_workbook
            wbv = load_workbook(outp, data_only=True)
            ws = wbv["Model"]
            got = (ws["A7"].value, ws["B7"].value, ws["C7"].value, round(ws["D7"].value or 0, 4), ws["E7"].value)
            want = (150, 225, "big", round(sum(a * 10 / 1.1 ** a for a in range(1, 6)), 4), 45)
            extra = (ws["B8"].value, ws["C8"].value, ws["D8"].value, ws["E8"].value, ws["A9"].value, ws["B9"].value,
                     ws["C9"].value, ws["D9"].value, wbv["Other"]["A1"].value)
            want_x = (46081, 46081, 10, 40, "east", "1,234.50", 70, 300, 375)      # 46081 = 28 Feb 2026 as a serial
            return got == want and extra == want_x, f"{got} {extra}"
        return go

    def docx_pdf():
        pdf = soffice_convert(str(files["docx"]), "pdf", str(out))
        n, txt = pdf_text_and_pages(pdf)
        res["docx.pdf"] = pdf
        return (n == 2 and "Proposal" in txt and "quick brown fox" in txt and "r2c2" in txt and "Header text" in txt
                and "Footer text" in txt and "Second page text" in txt), f"{n} page(s), text ok"

    def convert_tool():
        os.environ["MESHPOOL_PROJECT"] = str(work / "proj")
        (work / "proj").mkdir(exist_ok=True)
        from meshpool_harness.agent import office_mcp_server as M
        shutil.copy2(files["docx"], work / "proj" / "memo.docx")
        msg, err = M.convert_document(str(work / "proj" / "memo.docx"), "pdf")
        ok1 = (not err) and "Saved" in msg
        pdf = next((work / "proj").rglob("memo*.pdf"), None)
        if not ok1 or not pdf:
            return False, f"docx->pdf: {msg[-200:]}"
        # pdf -> docx goes through LibreOffice's PDF import (writer_pdf_import); the words must come across
        msg2, err2 = M.convert_document(str(pdf), "docx")
        import zipfile
        text = ""
        for cand in sorted((work / "proj").rglob("memo*.docx")):
            try:
                text += zipfile.ZipFile(cand).read("word/document.xml").decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
        ok2 = (not err2) and "Proposal" in text and "quick brown fox" in text and "r2c2" in text
        msg3, err3 = M.convert_document(str(pdf), "pptx")                   # nothing can turn a PDF into slides
        return ok2 and bool(err3) and "not to pptx" in msg3, \
            f"docx->pdf ok; pdf->docx {'keeps the text' if ok2 else 'LOST THE TEXT: ' + msg2[-100:]}; pdf->pptx refused"

    step("pptx -> pdf (text, picture, fonts)", pptx_pdf)
    step("pptx chart is drawn", chart_drawn)
    step("Segoe UI drawn with bundled Selawik", segoe_to_selawik)
    step("pptx -> png", pptx_png)
    step("xlsx recalc (sheets.qa.recalc)", xlsx_recalc("qa", lambda: qa.recalc(str(files["xlsx"]), str(out / "qa"))))
    step("xlsx recalc (forge validate_xlsx.recalc)", xlsx_recalc("forge", lambda: validate_xlsx.recalc(str(files["xlsx"]), str(out / "forge"))))
    step("docx -> pdf", docx_pdf)
    step("convert_document tool (docx -> pdf -> docx)", convert_tool)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("soffice")
    ap.add_argument("--compare", help="the unpruned soffice: its slide drawing must match")
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="lo-verify-"))
    os.environ.setdefault("MH_SLOTS_DIR", str(work / "slots"))        # the app code imported here keeps its files out of the real temp folder
    files = make_files(work)
    print(f"pruned: {a.soffice}")
    r = run(a.soffice, work / "pruned", files)
    ok = all(v[0] for v in r.values() if isinstance(v, tuple))
    if a.compare and not ok:
        print("not comparing: the pruned copy already failed")
    if a.compare and ok:
        print(f"full:   {a.compare}")
        f = run(a.compare, work / "full", files)
        from PIL import ImageChops
        import pypdfium2 as pdfium
        worst = 0.0
        for name in ("pptx.pdf", "docx.pdf"):
            a_, b_ = pdfium.PdfDocument(str(r[name])), pdfium.PdfDocument(str(f[name]))
            same_text = pdf_text_and_pages(Path(r[name])) == pdf_text_and_pages(Path(f[name]))
            for i in range(min(len(a_), len(b_))):
                d = ImageChops.difference(a_[i].render(scale=1).to_pil().convert("RGB"),
                                          b_[i].render(scale=1).to_pil().convert("RGB")).convert("L")
                share = sum(1 for p_ in d.getdata() if p_ > 48) / (d.width * d.height)
                worst = max(worst, share)
            print(f"  {name}: {len(a_)} vs {len(b_)} pages, text {'identical' if same_text else 'DIFFERENT'}")
            ok = ok and same_text and len(a_) == len(b_)
        print(f"  pixels that differ between pruned and full, worst page: {worst:.4%}")
        ok = ok and worst < 0.01
        same = {k: v[1][:40] for k, v in r.items() if isinstance(v, tuple)}
        full = {k: v[1][:40] for k, v in f.items() if isinstance(v, tuple)}
        if same != full:
            print("  differences in results (pruned vs full):")
            for k in same:
                if same[k] != full.get(k):
                    print(f"    {k}: {same[k]!r} vs {full.get(k)!r}")
            ok = ok and all(r[k][0] == f[k][0] for k in r if isinstance(r[k], tuple))
    print("ALL PASSED" if ok else "FAILED")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
