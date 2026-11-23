"""Reference solvers for the office tasks: each writes a correct result into `w` (a copy of the fixture).

Used only by the grader tests; never shown to an agent.
"""
from __future__ import annotations

import copy
import csv
import re
import shutil
import zipfile
from pathlib import Path

from lxml import etree

from testkit.bench3 import make_fixtures as mf

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = "{%s}" % W


def solve_xlsx_edit(w: Path):
    import openpyxl
    from copy import copy as cp
    wb = openpyxl.load_workbook(w / "inventory.xlsx")
    ws = wb["Stock"]
    ws["F1"] = "Value"
    ws["F1"].font, ws["F1"].fill = cp(ws["E1"].font), cp(ws["E1"].fill)
    ws.column_dimensions["F"].width = 14
    last = ws.max_row
    for r in range(2, last + 1):
        if ws.cell(r, 3).value == "Cables":
            ws.cell(r, 5).value = round(ws.cell(r, 5).value * 1.1, 2)
        ws.cell(r, 6).value = f"=D{r}*E{r}"
        ws.cell(r, 6).number_format = ws.cell(r, 5).number_format
    ws.cell(last + 1, 1).value = "Total"
    ws.cell(last + 1, 6).value = f"=SUM(F2:F{last})"
    ws.cell(last + 1, 6).number_format = ws.cell(2, 5).number_format
    wb.save(w / "inventory.xlsx")


def _split_run(r, old, new):
    """Return replacement elements for run r: [before, del(old), ins(new), after] with copied rPr."""
    t = r.find(NS + "t")
    text = t.text or ""
    i = text.index(old)
    rpr = r.find(NS + "rPr")

    def mk(txt, tag="t"):
        run = etree.Element(NS + "r")
        if rpr is not None:
            run.append(copy.deepcopy(rpr))
        e = etree.SubElement(run, NS + tag)
        e.text = txt
        e.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        return run

    def wrap(kind, run, n):
        el = etree.Element(NS + kind)
        el.set(NS + "id", str(n))
        el.set(NS + "author", "Reference")
        el.set(NS + "date", "2026-01-06T09:00:00Z")
        el.append(run)
        return el

    out = []
    if text[:i]:
        out.append(mk(text[:i]))
    out.append(wrap("del", mk(old, "delText"), 900 + len(old) + i))
    out.append(wrap("ins", mk(new), 1900 + len(new) + i))
    if text[i + len(old):]:
        out.append(mk(text[i + len(old):]))
    return out


def track_replace(root, old, new):
    for r in list(root.iter(NS + "r")):
        t = r.find(NS + "t")
        if t is not None and t.text and old in t.text and r.getparent().tag == NS + "p":
            parent, idx = r.getparent(), r.getparent().index(r)
            parent.remove(r)
            for k, el in enumerate(_split_run(r, old, new)):
                parent.insert(idx + k, el)
            return track_replace(root, old, new)  # more occurrences (also in the trailing part)
    return root


def rewrite_docx(path: Path, fn):
    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    root = etree.fromstring(items["word/document.xml"])
    fn(root)
    items["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in items.items():
            z.writestr(n, b)


def solve_docx_tracked(w: Path):
    def fn(root):
        track_replace(root, "Acme Corp", "Globex Inc")
        track_replace(root, "4,000", "4,500")
    rewrite_docx(w / "contract.docx", fn)


def solve_docx_untracked(w: Path):
    """Wrong on purpose: the same edit with plain text replacement and no tracked changes."""
    import docx
    d = docx.Document(str(w / "contract.docx"))
    for p in d.paragraphs:
        for r in p.runs:
            r.text = r.text.replace("Acme Corp", "Globex Inc").replace("4,000", "4,500")
    d.save(str(w / "contract.docx"))


def solve_invoices_fx(w: Path):
    import openpyxl
    rates = {r["currency"]: float(r["usd_per_unit"]) for r in csv.DictReader(open(w / "fx_rates.csv"))}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoices"
    ws.append(["invoice", "customer", "currency", "item", "qty", "unit price", "amount", "amount USD"])
    n = 1
    for inv, cust, cur, item, qty, price, amt, usd in mf.invoice_lines():
        n += 1
        ws.append([inv, cust, cur, item, qty, price, f"=E{n}*F{n}", f"=G{n}*{rates[cur]}"])
    ws.append(["Total (USD)", None, None, None, None, None, None, f"=SUM(H2:H{n})"])
    wb.save(w / "invoices.xlsx")
    return f"The grand total is ${mf.invoices_grand_total_usd():,.2f} USD."


def solve_docx_mailmerge(w: Path):
    import docx
    (w / "letters").mkdir()
    for name, company, amount, due in mf.CUSTOMERS:
        d = docx.Document(str(w / "letter_template.docx"))
        vals = {"{{name}}": name, "{{company}}": company, "{{amount}}": f"${float(amount):,.2f}", "{{due_date}}": due}
        for p in d.paragraphs:
            for r in p.runs:
                for k, v in vals.items():
                    r.text = r.text.replace(k, v)
        d.save(str(w / "letters" / f"letter_{name.split()[0].lower()}.docx"))


def solve_xlsx_budget(w: Path):
    import openpyxl
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "Assumptions"
    a.append(["assumption", "value", "note"])
    for lab, val, note in mf.CAFE:
        a.append([lab, val, note])
    b = wb.create_sheet("Budget")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    b.append(["line"] + months + ["Total"])
    A = "Assumptions!"
    lines = [("Revenue", f"={A}$B$2*{A}$B$3*{A}$B$4"),
             ("Food cost", "={c}2*" + f"{A}$B$5"),
             ("Staff", f"={A}$B$6"), ("Rent", f"={A}$B$7"), ("Other", f"={A}$B$8"),
             ("Profit", "={c}2-{c}3-{c}4-{c}5-{c}6")]
    for r, (lab, f) in enumerate(lines, start=2):
        row = [lab] + [f.replace("{c}", chr(ord("B") + i)) for i in range(12)]
        row.append(f"=SUM(B{r}:M{r})")
        b.append(row)
    wb.save(w / "budget.xlsx")


def solve_pptx_chart(w: Path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.util import Inches, Pt
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    def title_slide(title, body):
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = title
        s.shapes.title.left, s.shapes.title.top, s.shapes.title.width, s.shapes.title.height = Inches(.6), Inches(.4), Inches(12), Inches(1.1)
        if body:
            tb = s.shapes.add_textbox(Inches(.8), Inches(1.8), Inches(11.5), Inches(4))
            tb.text_frame.word_wrap = True
            for i, line in enumerate(body):
                p = tb.text_frame.paragraphs[0] if i == 0 else tb.text_frame.add_paragraph()
                p.text, p.font.size = line, Pt(22)
        return s
    tot = {q: sum(mf.QUARTERLY[r][i] for r in mf.REGIONS) for i, q in enumerate(mf.QUARTERS)}
    title_slide("Quarterly revenue review", ["Revenue by region and quarter", "Prepared for leadership"])
    s = title_slide("Revenue by quarter and region", None)
    cd = CategoryChartData()
    cd.categories = mf.QUARTERS
    for r in mf.REGIONS:
        cd.add_series(r, mf.QUARTERLY[r])
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(.8), Inches(1.6), Inches(11.7), Inches(5.4), cd)
    gf.chart.has_legend = True
    gf.chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    gf.chart.legend.include_in_layout = False
    title_slide("Key numbers", [f"{q}: {v:,.0f} total revenue" for q, v in tot.items()])
    title_slide("Next steps", ["Grow West, the smallest region", "Protect North momentum in Q4"])
    prs.save(str(w / "deck.pptx"))


def _sales_table(by):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    data = [["Region", "Revenue"]] + [[k, f"{v:,.2f}"] for k, v in by.items()]
    t = Table(data, colWidths=[150, 120])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke])]))
    return t


def solve_pdf_report(w: Path, chart: str = "vector"):
    """chart: 'none' (table only, wrong), 'vector' (reportlab bar chart) or 'raster' (matplotlib PNG)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer
    by = mf.sales_by_region()
    st = getSampleStyleSheet()
    story = [Paragraph("Sales report", st["Title"]), Paragraph("Revenue by region, from sales.csv.", st["Normal"]),
             Spacer(1, 12), _sales_table(by), Spacer(1, 18)]
    if chart == "vector":
        from reportlab.graphics.charts.barcharts import VerticalBarChart
        from reportlab.graphics.shapes import Drawing
        d = Drawing(400, 220)
        bc = VerticalBarChart()
        bc.x, bc.y, bc.width, bc.height = 40, 30, 330, 160
        bc.data = [list(by.values())]
        bc.categoryAxis.categoryNames = list(by)
        d.add(bc)
        story.append(d)
    elif chart == "raster":
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(5, 3), dpi=100)
        ax.bar(list(by), list(by.values()))
        ax.set_title("Revenue by region")
        png = w / "_chart.png"
        fig.savefig(png)
        plt.close(fig)
        story.append(Image(str(png), width=360, height=216))
    SimpleDocTemplate(str(w / "report.pdf"), pagesize=A4).build(story)
    (w / "_chart.png").unlink(missing_ok=True)


SOLVERS = {
    "xlsx_edit": solve_xlsx_edit,
    "docx_tracked": solve_docx_tracked,
    "invoices_fx": solve_invoices_fx,
    "docx_mailmerge": solve_docx_mailmerge,
    "xlsx_budget": solve_xlsx_budget,
    "pptx_chart": solve_pptx_chart,
    "pdf_report": solve_pdf_report,
}
