"""Deterministic generator for the office task fixtures (testkit/bench3/fixtures/office/<task>/).

Run: /root/.venvs/mp/bin/python testkit/bench3/make_fixtures.py
The DATA constants below are also what the graders compare against.
"""
from __future__ import annotations

import csv
import datetime as dt
import random
import re
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OFFICE = HERE / "fixtures" / "office"
FIXED = dt.datetime(2026, 1, 5, 9, 0, 0)

# ----------------------------------------------------------------------------- data

CATEGORIES = ["Cables", "Adapters", "Storage", "Accessories"]


def inventory_rows():
    """30 stock rows; every unit cost is a multiple of 0.10 so +10% is exact to the cent."""
    r = random.Random(11)
    items = {
        "Cables": ["HDMI cable 2m", "USB-C cable 1m", "Ethernet cable 5m", "DisplayPort cable", "Lightning cable",
                   "Power cable C13", "Audio cable 3.5mm", "SATA cable"],
        "Adapters": ["USB-C to HDMI", "USB-A hub 4 port", "DisplayPort to VGA", "Ethernet to USB", "Card reader",
                     "Bluetooth dongle", "Travel plug EU"],
        "Storage": ["SSD 500GB", "SSD 1TB", "USB stick 64GB", "SD card 128GB", "External HDD 2TB", "USB stick 256GB",
                    "microSD 64GB"],
        "Accessories": ["Laptop stand", "Mouse pad", "Webcam cover", "Cable clips", "Screen wipes", "Keyboard cover",
                        "Laptop sleeve 14in", "Wrist rest"],
    }
    rows, n = [], 1
    for cat in CATEGORIES:
        for name in items[cat]:
            cost = r.randint(15, 600) / 10 if cat != "Storage" else r.randint(120, 1400) / 10
            rows.append((f"SKU-{1000 + n}", name, cat, r.randint(5, 180), round(cost, 1)))
            n += 1
    return rows


INVOICES = [  # id, customer, currency, [(item, qty, unit price)]
    ("INV-2001", "Northwind Traders", "USD", [("Consulting hours", 12, 95.00), ("Site visit", 2, 250.00)]),
    ("INV-2002", "Bergmann Maschinenbau", "EUR", [("Maintenance plan", 3, 180.00), ("Spare parts kit", 4, 42.50),
                                                  ("Shipping", 1, 35.00)]),
    ("INV-2003", "Thistle & Oak Ltd", "GBP", [("Design workshop", 2, 640.00), ("Printed materials", 50, 3.20),
                                              ("Courier", 1, 28.00)]),
]
FX = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}


def invoice_lines():
    """[(invoice, customer, currency, item, qty, unit_price, amount, amount_usd)]"""
    out = []
    for inv, cust, cur, lines in INVOICES:
        for item, qty, price in lines:
            amt = round(qty * price, 2)
            out.append((inv, cust, cur, item, qty, price, amt, round(amt * FX[cur], 2)))
    return out


def invoices_grand_total_usd():
    return round(sum(l[7] for l in invoice_lines()), 2)


CUSTOMERS = [  # name, company, amount, due_date
    ("Maria Lindqvist", "Lindqvist Bakery", "1250.00", "2026-12-15"),
    ("Tomas Okafor", "Okafor Logistics", "845.50", "2026-12-18"),
    ("Priya Raman", "Raman Textiles", "3200.00", "2027-01-05"),
    ("Jonas Weber", "Weber Elektro", "410.75", "2026-12-22"),
    ("Elena Petrova", "Petrova Studio", "6780.00", "2027-01-12"),
]

CAFE = [  # label, value, note
    ("covers per day", 85, "customers served on an open day"),
    ("average ticket", 14.5, "average spend per cover"),
    ("open days per month", 26, ""),
    ("food cost %", 0.32, "share of revenue"),
    ("staff cost per month", 9500, ""),
    ("rent per month", 3200, ""),
    ("other costs per month", 1800, "utilities, insurance, supplies"),
]


def cafe_expected(ticket_mult=1.0):
    c = {k: v for k, v, _ in CAFE}
    rev = c["covers per day"] * c["average ticket"] * ticket_mult * c["open days per month"]
    food = rev * c["food cost %"]
    profit = rev - food - c["staff cost per month"] - c["rent per month"] - c["other costs per month"]
    return {"rev_m": rev, "profit_m": profit, "rev_y": rev * 12, "profit_y": profit * 12}


REGIONS = ["North", "South", "East", "West"]
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
QUARTERLY = {  # region -> revenue per quarter
    "North": [412000, 438500, 455200, 501300],
    "South": [298400, 310900, 287600, 342100],
    "East": [356700, 349200, 391800, 420500],
    "West": [221300, 245800, 262400, 289900],
}


def sales_rows():
    """~60 sales rows over 4 regions; deterministic."""
    r = random.Random(5)
    products = ["Widget", "Gadget", "Gizmo", "Sprocket", "Doohickey"]
    prices = {"Widget": 12.5, "Gadget": 24.0, "Gizmo": 39.9, "Sprocket": 7.75, "Doohickey": 55.0}
    rows = []
    for i in range(60):
        d = dt.date(2026, 1, 1) + dt.timedelta(days=i * 3 + r.randint(0, 2))
        reg = REGIONS[i % 4] if r.random() < 0.6 else r.choice(REGIONS)
        p = r.choice(products)
        rows.append((d.isoformat(), reg, p, r.randint(3, 60), prices[p]))
    return rows


def sales_by_region():
    tot = {g: 0.0 for g in REGIONS}
    for _, reg, _, units, price in sales_rows():
        tot[reg] += units * price
    return {k: round(v, 2) for k, v in tot.items()}


# ----------------------------------------------------------------------------- writers

def _norm_zip(path: Path):
    """Rewrite a zip with fixed timestamps so the bytes are reproducible."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path) as zi, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in sorted(zi.infolist(), key=lambda i: i.filename):
            ni = zipfile.ZipInfo(info.filename, (2026, 1, 5, 9, 0, 0))
            ni.compress_type = zipfile.ZIP_DEFLATED
            ni.external_attr = 0o644 << 16
            data = zi.read(info.filename)
            if info.filename == "docProps/core.xml":  # openpyxl stamps 'modified' with the save time
                data = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*", rb"\g<1>2026-01-05T09:00:00Z", data)
            zo.writestr(ni, data)
    tmp.replace(path)


def _csv(path: Path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def make_xlsx_edit(d: Path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock"
    ws.append(["sku", "item", "category", "qty", "unit_cost"])
    for row in inventory_rows():
        ws.append(list(row))
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 5).number_format = '"$"#,##0.00'
    for col, w in zip("ABCDE", (12, 24, 14, 8, 12)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    notes = wb.create_sheet("Notes")
    notes["A1"] = "Stock notes"
    notes["A2"] = "Counts taken on 2026-01-02 by the warehouse team."
    notes["A3"] = "Unit costs are what we pay suppliers, excluding VAT."
    notes["A4"] = "Reorder when qty falls below 10."
    notes.column_dimensions["A"].width = 60
    wb.properties.created = wb.properties.modified = FIXED
    p = d / "inventory.xlsx"
    wb.save(p)
    _norm_zip(p)


def make_docx_tracked(d: Path):
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    doc.core_properties.created = doc.core_properties.modified = FIXED
    doc.core_properties.author = "fixture"
    doc.add_paragraph("Services Agreement", style="Title")
    doc.add_paragraph("This agreement is made between Acme Corp (the Client) and Brightline Studio (the Provider).")
    doc.add_heading("Parties", level=1)
    doc.add_paragraph("Acme Corp, a company registered in Delaware, engages Brightline Studio to deliver the services below.")
    doc.add_heading("Services", level=1)
    doc.add_paragraph("The Provider will design and maintain the Client's website and report monthly to Acme Corp.")
    doc.add_heading("Fees", level=1)
    p = doc.add_paragraph("The Client shall pay the Provider a fee of ")
    r = p.add_run("4,000 USD per month")
    r.italic = True
    p.add_run(", invoiced on the first day of each month and due within 30 days.")
    doc.add_heading("Term", level=1)
    doc.add_paragraph("This agreement runs for twelve months and renews unless either party gives 60 days notice.")
    doc.add_heading("Governing law", level=1)
    doc.add_paragraph("This agreement is governed by the laws of the State of New York.")
    doc.add_paragraph("Signed for the Client: ____________________")
    p = d / "contract.docx"
    doc.save(p)
    _norm_zip(p)


def make_invoices_fx(d: Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    sym = {"USD": "$", "EUR": "EUR ", "GBP": "GBP "}
    for inv, cust, cur, lines in INVOICES:
        c = Canvas(str(d / f"{inv}.pdf"), pagesize=A4, invariant=1)
        c.setTitle(inv)
        y = 800
        c.setFont("Helvetica-Bold", 18)
        c.drawString(60, y, f"Invoice {inv}")
        c.setFont("Helvetica", 11)
        y -= 30
        c.drawString(60, y, f"Customer: {cust}")
        y -= 16
        c.drawString(60, y, f"Currency: {cur}")
        y -= 40
        c.setFont("Helvetica-Bold", 11)
        for x, t in zip((60, 280, 340, 440), ("Item", "Qty", "Unit price", "Amount")):
            c.drawString(x, y, t)
        c.setFont("Helvetica", 11)
        total = 0.0
        for item, qty, price in lines:
            y -= 20
            amt = qty * price
            total += amt
            c.drawString(60, y, item)
            c.drawString(280, y, str(qty))
            c.drawString(340, y, f"{sym[cur]}{price:,.2f}")
            c.drawString(440, y, f"{sym[cur]}{amt:,.2f}")
        y -= 30
        c.setFont("Helvetica-Bold", 11)
        c.drawString(340, y, "Total due")
        c.drawString(440, y, f"{sym[cur]}{total:,.2f}")
        c.save()
    _csv(d / "fx_rates.csv", ["currency", "usd_per_unit"], [(k, v) for k, v in FX.items() if k != "USD"] + [("USD", 1)])


def make_docx_mailmerge(d: Path):
    from docx import Document
    doc = Document()
    doc.core_properties.created = doc.core_properties.modified = FIXED
    doc.core_properties.author = "fixture"
    doc.add_heading("Payment reminder", level=1)
    doc.add_paragraph("Dear {{name}},")
    doc.add_paragraph("We are writing to {{company}} about the invoice that is still open on your account.")
    p = doc.add_paragraph("The outstanding balance is ")
    p.add_run("{{amount}}").bold = True
    p.add_run(" and it is due on {{due_date}}.")
    doc.add_paragraph("If you have already paid, please ignore this letter. Otherwise reply to this letter and we will help.")
    doc.add_paragraph("Kind regards,")
    doc.add_paragraph("Accounts team, Brightline Studio")
    p = d / "letter_template.docx"
    doc.save(p)
    _norm_zip(p)
    _csv(d / "customers.csv", ["name", "company", "amount", "due_date"], CUSTOMERS)


def make_xlsx_budget(d: Path):
    _csv(d / "assumptions.csv", ["assumption", "value", "note"], CAFE)


def make_pptx_chart(d: Path):
    rows = [(reg, q, QUARTERLY[reg][i]) for reg in REGIONS for i, q in enumerate(QUARTERS)]
    _csv(d / "quarterly.csv", ["region", "quarter", "revenue"], rows)


def make_pdf_report(d: Path):
    _csv(d / "sales.csv", ["date", "region", "product", "units", "unit_price"], sales_rows())


MAKERS = {
    "xlsx_edit": make_xlsx_edit,
    "docx_tracked": make_docx_tracked,
    "invoices_fx": make_invoices_fx,
    "docx_mailmerge": make_docx_mailmerge,
    "xlsx_budget": make_xlsx_budget,
    "pptx_chart": make_pptx_chart,
    "pdf_report": make_pdf_report,
}


def build(out: Path = OFFICE):
    for name, fn in MAKERS.items():
        d = out / name
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        fn(d)
    return out


if __name__ == "__main__":
    print("wrote", build(Path(sys.argv[1]) if len(sys.argv) > 1 else OFFICE))
