"""Small files for the in-app preview checks (testkit/ux_previews.js): a deck, a workbook, a PDF, a page, a note, a logo.
Usage: python testkit/make_preview_fixtures.py DIR   (needs python-pptx, python-docx, openpyxl; LibreOffice for the PDF)"""
import shutil
import subprocess
import sys
from pathlib import Path

d = Path(sys.argv[1])
d.mkdir(parents=True, exist_ok=True)

from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402

pr = Presentation()
pr.slide_width, pr.slide_height = Inches(13.333), Inches(7.5)
for t in ("Q3 review", "Revenue by region", "Costs", "Risks", "Next steps"):
    s = pr.slides.add_slide(pr.slide_layouts[1])
    s.shapes.title.text = t
    s.placeholders[1].text = "North grew 12 percent\nSouth held flat\nEast launched in June"
pr.save(d / "deck.pptx")

import openpyxl  # noqa: E402

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Revenue"
ws.append(["Region", "Q1", "Q2", "Q3", "Total"])
for i, r in enumerate(["North", "South", "East", "West"], 2):
    ws.append([r, 100 * i, 120 * i, 90 * i, f"=SUM(B{i}:D{i})"])
wb.create_sheet("Costs").append(["Item", "Amount"])
wb["Costs"].append(["Hosting", 4200.5])
wb.save(d / "model.xlsx")

from docx import Document  # noqa: E402

doc = Document()
doc.add_heading("Quarterly report", 1)
for _ in range(6):
    doc.add_paragraph("Revenue grew in every region this quarter. " * 8)
doc.save(d / "report.docx")
soffice = shutil.which("soffice")
if soffice:
    subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(d), str(d / "report.docx")],
                   capture_output=True, timeout=180)
if not (d / "report.pdf").exists():
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    for _ in range(3):
        pdf.new_page(595, 842)
    pdf.save(str(d / "report.pdf"))
(d / "page.html").write_text("<!doctype html><html><body style='font-family:sans-serif;padding:24px'><h1>Hello</h1>"
                             "<button id=b onclick=\"this.textContent='clicked'\">Click me</button></body></html>")
(d / "notes.md").write_text("# Notes\n\nSome **bold** text and `code`.\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
(d / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"><rect width="200" height="120" '
                            'fill="#1F6FD0"/><circle cx="100" cy="60" r="40" fill="#fff"/></svg>')
print("fixtures in", d)
