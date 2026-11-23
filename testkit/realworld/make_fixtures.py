"""Deterministic inputs for the 9 everyday real-world tasks (testkit/realworld/tasks.py).

    python testkit/realworld/make_fixtures.py          # (re)writes testkit/realworld/fixtures/<task>/

Every input is generated from fixed seeds with reportlab / python-docx / openpyxl / the csv module, so two runs give
the same files and the graders' expected answers (EXPECTED below) are computed from the same generator, never typed by
hand. Nothing here talks to a model.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import random
import shutil
import textwrap
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures"


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


# =========================================================================== E1 pdf_facts (15-page report)

PDF_FACTS = {
    "ceo": "Marta Lindqvist",
    "revenue": "48.6",            # USD million, FY2025
    "net_income": "5.9",          # USD million, FY2025
    "headcount": "1,284",
    "market": "Brazil",
}

_FILLER = [
    "The board reviewed operating procedures across all regional offices during the period and confirmed that the "
    "existing governance framework remains appropriate for a company of our size and complexity.",
    "Customer satisfaction surveys were conducted twice during the year using the same questionnaire as in prior "
    "periods so that results remain comparable over time.",
    "Our logistics partners continued to improve delivery reliability, and the share of shipments arriving within "
    "the promised window rose modestly compared with the previous year.",
    "Investment in staff training focused on safety, data protection and product knowledge, with every new hire "
    "completing the onboarding curriculum within their first month.",
    "The sustainability committee met quarterly and published its recommendations to management, several of which "
    "have already been adopted in our packaging and travel policies.",
    "Information security controls were audited by an external firm, which reported no material weaknesses and a "
    "small number of minor observations that have since been addressed.",
    "Supplier contracts were renegotiated where market conditions allowed, with an emphasis on multi-year agreements "
    "that provide price stability for both parties.",
]


def make_pdf_facts(d: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table

    rng = random.Random(101)
    ss = getSampleStyleSheet()
    story = []
    f = PDF_FACTS
    # facts placed on specific pages among filler; distractors (FY2024 numbers, former CEO, considered markets)
    pages = {
        1: [("title", "Northwind Instruments AB - Annual Report 2025"),
            ("p", "This report covers the financial year ended 31 December 2025 (FY2025).")],
        3: [("h", "Letter from the Chief Executive"),
            ("p", f"It has been a privilege to lead Northwind as Chief Executive Officer since March 2025, when I "
                  f"succeeded Johan Berg, who served as CEO for eleven years and now chairs our advisory council. "
                  f"- {f['ceo']}, Chief Executive Officer")],
        6: [("h", "Financial highlights"),
            ("p", f"Revenue for FY2025 reached USD {f['revenue']} million, up from USD 41.2 million in FY2024. "
                  f"Gross margin improved to 38.4 percent."),
            ("table", [["Metric", "FY2024", "FY2025"], ["Revenue (USD m)", "41.2", f['revenue']],
                       ["Gross margin", "36.9%", "38.4%"], ["Net income (USD m)", "3.1", f['net_income']]])],
        9: [("h", "People"),
            ("p", f"At year end the group employed {f['headcount']} people, compared with 1,102 a year earlier. "
                  f"Voluntary staff turnover was 7.8 percent.")],
        11: [("h", "Profitability"),
             ("p", f"Net income for FY2025 was USD {f['net_income']} million, against USD 3.1 million in FY2024, "
                   f"driven by higher volumes and lower freight costs.")],
        13: [("h", "Outlook and strategy for 2026"),
             ("p", f"After evaluating Mexico, Chile and Brazil, the board approved entry into {f['market']} as our "
                   f"single new market for 2026, with a sales office in Sao Paulo opening in the second quarter. "
                   f"Mexico and Chile remain under review for later years.")],
    }
    for page in range(1, 16):
        items = pages.get(page, [])
        if not any(k in ("title", "h") for k, _ in items):
            story.append(Paragraph(f"Section {page}: Operations review", ss["Heading2"]))
        for kind, val in items:
            if kind == "title":
                story.append(Paragraph(val, ss["Title"]))
            elif kind == "h":
                story.append(Paragraph(val, ss["Heading2"]))
            elif kind == "p":
                story.append(Paragraph(val, ss["BodyText"]))
            elif kind == "table":
                story.append(Table(val))
            story.append(Spacer(1, 8))
        for _ in range(5):
            story.append(Paragraph(" ".join(rng.sample(_FILLER, 3)), ss["BodyText"]))
            story.append(Spacer(1, 6))
        if page < 15:
            story.append(PageBreak())
    SimpleDocTemplate(str(d / "annual_report_2025.pdf"), pagesize=A4, title="Northwind Annual Report 2025",
                      invariant=1).build(story)


# =========================================================================== E2 csv_sales

REGIONS = ["North", "South", "East", "West"]
PRODUCTS = [("Sensor A", 129.0), ("Sensor B", 249.0), ("Gateway", 499.0), ("Cable kit", 19.5), ("Mount", 34.0)]


def sales_rows() -> list[dict]:
    rng = random.Random(202)
    rows = []
    start = dt.date(2024, 7, 1)
    for i in range(1, 2401):
        day = start + dt.timedelta(days=rng.randrange(0, 549))       # 2024-07-01 .. 2025-12-31
        prod, price = rng.choice(PRODUCTS)
        units = rng.randint(1, 12)
        disc = rng.choice([0, 0, 0, 0.05, 0.1, 0.15])
        rows.append({"order_id": f"SO-{i:05d}", "date": day.isoformat(), "region": rng.choice(REGIONS),
                     "product": prod, "units": units, "unit_price": f"{price:.2f}", "discount": f"{disc:.2f}"})
    return rows


def sales_expected() -> dict:
    rows = sales_rows()

    def rev(r):
        return r["units"] * float(r["unit_price"]) * (1 - float(r["discount"]))
    q1 = round(sum(rev(r) for r in rows if r["region"] == "West" and r["date"].startswith("2025")), 2)
    q3s = {}
    for r in rows:
        if r["date"][:4] == "2025" and r["date"][5:7] in ("07", "08", "09"):
            q3s[r["region"]] = q3s.get(r["region"], 0) + rev(r)
    q2 = max(q3s, key=q3s.get)
    q3 = sum(1 for r in rows if r["region"] == "East" and rev(r) > 1000)
    return {"q1": q1, "q2": q2, "q3": q3, "_q3_by_region": {k: round(v, 2) for k, v in q3s.items()}}


def make_csv_sales(d: Path) -> None:
    with open(d / "sales.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(sales_rows()[0]))
        w.writeheader()
        w.writerows(sales_rows())


# =========================================================================== E3 fix_failing_test (multi-file repo)

def make_fix_test(d: Path) -> None:
    _w(d / "README.md", """
        # shopcalc

        Order pricing for a small web shop. Run the tests with `python -m pytest -q`.
        """)
    _w(d / "shopcalc/__init__.py", """
        from .order import Order, Line
        from .pricing import order_total
        """)
    _w(d / "shopcalc/order.py", """
        from dataclasses import dataclass, field


        @dataclass
        class Line:
            sku: str
            qty: int
            unit_price: float


        @dataclass
        class Order:
            lines: list = field(default_factory=list)
            coupon: str | None = None
            country: str = "DE"

            def subtotal(self) -> float:
                return sum(l.qty * l.unit_price for l in self.lines)

            def item_count(self) -> int:
                return sum(l.qty for l in self.lines)
        """)
    _w(d / "shopcalc/discounts.py", """
        COUPONS = {"WELCOME10": 0.10, "VIP20": 0.20}


        def volume_rate(item_count: int) -> float:
            \"\"\"Volume discount: 5% from 10 items, 10% from 50 items (inclusive thresholds).\"\"\"
            if item_count > 50:
                return 0.10
            if item_count >= 10:
                return 0.05
            return 0.0


        def coupon_rate(code):
            return COUPONS.get((code or "").upper(), 0.0)
        """)
    _w(d / "shopcalc/tax.py", """
        VAT = {"DE": 0.19, "FR": 0.20, "NL": 0.21}


        def vat_rate(country: str) -> float:
            return VAT.get(country, 0.0)
        """)
    _w(d / "shopcalc/pricing.py", """
        from .discounts import coupon_rate, volume_rate
        from .tax import vat_rate


        def order_total(order) -> float:
            \"\"\"Subtotal, minus the larger of the volume and coupon discounts (they do not stack), plus VAT; rounded
            to cents.\"\"\"
            sub = order.subtotal()
            rate = max(volume_rate(order.item_count()), coupon_rate(order.coupon))
            net = sub * (1 - rate)
            return round(net * (1 + vat_rate(order.country)), 2)
        """)
    _w(d / "tests/test_pricing.py", """
        from shopcalc import Line, Order, order_total


        def test_plain_order():
            o = Order([Line("A", 2, 10.0)])
            assert order_total(o) == 23.8


        def test_coupon_beats_volume():
            o = Order([Line("A", 12, 10.0)], coupon="VIP20")
            assert order_total(o) == round(120 * 0.8 * 1.19, 2)


        def test_volume_discount_at_fifty_items():
            o = Order([Line("A", 50, 2.0)], country="FR")
            assert order_total(o) == round(100 * 0.90 * 1.20, 2)


        def test_unknown_country_has_no_vat():
            o = Order([Line("A", 1, 9.99)], country="US")
            assert order_total(o) == 9.99
        """)
    _w(d / "tests/test_discounts.py", """
        from shopcalc.discounts import coupon_rate, volume_rate


        def test_coupon_case_insensitive():
            assert coupon_rate("welcome10") == 0.10


        def test_small_orders():
            assert volume_rate(9) == 0.0
            assert volume_rate(10) == 0.05
        """)


FIX_TEST_HIDDEN = """
from shopcalc.discounts import volume_rate
from shopcalc import Line, Order, order_total


def test_hidden_thresholds():
    assert [volume_rate(n) for n in (0, 9, 10, 49, 50, 51, 200)] == [0, 0, 0.05, 0.05, 0.10, 0.10, 0.10]


def test_hidden_total_nl():
    o = Order([Line("B", 25, 4.0), Line("C", 25, 1.0)], country="NL")
    assert order_total(o) == round(125 * 0.90 * 1.21, 2)
"""


# =========================================================================== E4 flask_feature

def make_flask_feature(d: Path) -> None:
    _w(d / "README.md", """
        # notes-api

        A tiny Flask API for notes. Start it with `python app.py`; run tests with `python -m pytest -q`.

        Endpoints today:
        - `GET /notes` -> list of notes `[{"id", "title", "body", "tags"}]`
        - `POST /notes` with JSON `{"title", "body", "tags"?}` -> 201 and the created note; 400 if title is missing
        - `GET /notes/<id>` -> the note, or 404
        """)
    _w(d / "app.py", """
        from flask import Flask, jsonify, request

        from store import NoteStore


        def create_app(store=None):
            app = Flask(__name__)
            app.config["store"] = store or NoteStore()

            @app.get("/notes")
            def list_notes():
                return jsonify(app.config["store"].all())

            @app.post("/notes")
            def add_note():
                data = request.get_json(silent=True) or {}
                if not str(data.get("title") or "").strip():
                    return jsonify({"error": "title is required"}), 400
                note = app.config["store"].add(data["title"], data.get("body", ""), data.get("tags") or [])
                return jsonify(note), 201

            @app.get("/notes/<int:note_id>")
            def get_note(note_id):
                note = app.config["store"].get(note_id)
                if note is None:
                    return jsonify({"error": "not found"}), 404
                return jsonify(note)

            return app


        if __name__ == "__main__":
            create_app().run(debug=True)
        """)
    _w(d / "store.py", """
        class NoteStore:
            def __init__(self):
                self._notes = {}
                self._next = 1

            def all(self):
                return [self._notes[k] for k in sorted(self._notes)]

            def add(self, title, body="", tags=()):
                note = {"id": self._next, "title": title, "body": body, "tags": list(tags)}
                self._notes[self._next] = note
                self._next += 1
                return note

            def get(self, note_id):
                return self._notes.get(note_id)
        """)
    _w(d / "tests/test_app.py", """
        import pytest

        from app import create_app


        @pytest.fixture
        def client():
            return create_app().test_client()


        def test_create_and_get(client):
            r = client.post("/notes", json={"title": "Groceries", "body": "milk", "tags": ["home"]})
            assert r.status_code == 201
            nid = r.get_json()["id"]
            assert client.get(f"/notes/{nid}").get_json()["title"] == "Groceries"


        def test_title_required(client):
            assert client.post("/notes", json={"body": "x"}).status_code == 400


        def test_missing_note(client):
            assert client.get("/notes/99").status_code == 404
        """)
    _w(d / "FEATURE.md", """
        # Feature request: search, edit and delete notes

        1. `GET /notes?tag=<tag>` returns only the notes that have that tag (exact, case-insensitive match). Without the
           parameter, all notes are returned as today.
        2. `GET /notes?q=<text>` returns only notes whose title or body contains the text (case-insensitive). `tag` and
           `q` can be combined (both must match).
        3. `PATCH /notes/<id>` with a JSON object updates any of `title`, `body`, `tags` and returns 200 with the
           updated note. 404 if the note does not exist. 400 if `title` is present but empty, or if `tags` is present
           but not a list of strings. Other fields in the request are ignored.
        4. `DELETE /notes/<id>` returns 204 with an empty body; 404 if the note does not exist. Deleted ids are never
           reused.
        """)


FLASK_HIDDEN = """
import pytest

from app import create_app


@pytest.fixture
def client():
    c = create_app().test_client()
    c.post("/notes", json={"title": "Groceries", "body": "Milk and eggs", "tags": ["home", "Shopping"]})
    c.post("/notes", json={"title": "Sprint plan", "body": "ship the API", "tags": ["work"]})
    c.post("/notes", json={"title": "Weekend", "body": "hike; buy MILK", "tags": ["home"]})
    return c


def ids(r):
    return [n["id"] for n in r.get_json()]


def test_tag_filter(client):
    assert ids(client.get("/notes?tag=home")) == [1, 3]
    assert ids(client.get("/notes?tag=shopping")) == [1]
    assert ids(client.get("/notes?tag=none")) == []
    assert ids(client.get("/notes")) == [1, 2, 3]


def test_text_search(client):
    assert ids(client.get("/notes?q=milk")) == [1, 3]
    assert ids(client.get("/notes?q=SPRINT")) == [2]
    assert ids(client.get("/notes?q=milk&tag=home")) == [1, 3]
    assert ids(client.get("/notes?q=eggs&tag=work")) == []


def test_patch(client):
    r = client.patch("/notes/2", json={"body": "ship v2", "color": "red"})
    assert r.status_code == 200
    n = r.get_json()
    assert n["body"] == "ship v2" and n["title"] == "Sprint plan" and "color" not in n
    assert client.get("/notes/2").get_json()["body"] == "ship v2"
    assert client.patch("/notes/2", json={"tags": ["a", "b"]}).get_json()["tags"] == ["a", "b"]


def test_patch_errors(client):
    assert client.patch("/notes/42", json={"body": "x"}).status_code == 404
    assert client.patch("/notes/1", json={"title": "  "}).status_code == 400
    assert client.patch("/notes/1", json={"tags": "home"}).status_code == 400
    assert client.patch("/notes/1", json={"tags": [1, 2]}).status_code == 400
    assert client.get("/notes/1").get_json()["title"] == "Groceries"


def test_delete(client):
    r = client.delete("/notes/1")
    assert r.status_code == 204 and r.data == b""
    assert client.get("/notes/1").status_code == 404
    assert client.delete("/notes/1").status_code == 404
    assert client.post("/notes", json={"title": "new"}).get_json()["id"] == 4
"""


# =========================================================================== E5 bash_rename

def make_bash_rename(d: Path) -> None:
    _w(d / "SPEC.md", """
        # Photo renamer

        Write `rename.sh`, a bash script run as `bash rename.sh <folder>`. It renames the files directly inside
        `<folder>` (not in subfolders) according to these rules:

        1. Camera files named `IMG_YYYYMMDD_HHMMSS.<ext>` (example: `IMG_20240315_142233.jpg`) become
           `YYYY-MM-DD_HH-MM-SS.<ext>` (example: `2024-03-15_14-22-33.jpg`).
        2. Phone screenshots named `Screenshot YYYY-MM-DD at HH.MM.SS.<ext>` (note the spaces) become
           `screenshot_YYYY-MM-DD_HH-MM-SS.<ext>`.
        3. In every renamed file the extension is lower-cased (`.JPG` -> `.jpg`, `.PNG` -> `.png`), and `.jpeg` becomes
           `.jpg`.
        4. Files that match neither pattern are left exactly as they are (name and extension untouched).
        5. If the target name already exists, append `_1` before the extension; if that exists too, `_2`, and so on.
           Files are processed in byte-wise sorted order of their original names (`LC_ALL=C sort`).
        6. The script prints nothing on success and exits 0. It must work with names that contain spaces.
        """)
    s = d / "sample"
    s.mkdir(parents=True, exist_ok=True)
    for n in ("IMG_20240315_142233.jpg", "IMG_20231201_080000.JPG", "Screenshot 2024-01-02 at 09.15.00.png",
              "notes.txt"):
        (s / n).write_bytes(b"x")


RENAME_TREE = {
    # original name -> expected final name
    "IMG_20240315_142233.JPG": "2024-03-15_14-22-33.jpg",         # byte order: JPG < jpeg < jpg
    "IMG_20240315_142233.jpeg": "2024-03-15_14-22-33_1.jpg",
    "IMG_20240315_142233.jpg": "2024-03-15_14-22-33_2.jpg",
    "IMG_20231201_080000.JPEG": "2023-12-01_08-00-00.jpg",
    "IMG_20250704_235959.heic": "2025-07-04_23-59-59.heic",
    "Screenshot 2024-01-02 at 09.15.00.PNG": "screenshot_2024-01-02_09-15-00.png",
    "Screenshot 2024-01-02 at 09.15.00.png": "screenshot_2024-01-02_09-15-00_1.png",
    "IMG_2024031_142233.jpg": "IMG_2024031_142233.jpg",          # 7-digit date: no match
    "img_20240315_142233.jpg": "img_20240315_142233.jpg",        # lower-case prefix: no match
    "holiday photo.JPG": "holiday photo.JPG",
    "2023-12-01_08-00-00.txt": "2023-12-01_08-00-00.txt",
    "Screenshot 2024-01-02.png": "Screenshot 2024-01-02.png",
}
RENAME_SUBDIR = {"sub/IMG_20240101_010101.jpg": "sub/IMG_20240101_010101.jpg"}


# =========================================================================== E6 md_to_docx

DOCX_FIELDS = {
    "client_name": "Harbor Lane Dental Group",
    "project_name": "Patient Portal Redesign",
    "start_date": "2026-02-09",
    "end_date": "2026-05-29",
    "budget": "EUR 84,500",
    "contact_name": "Dr. Ines Walraven",
    "contact_email": "ines.walraven@harborlane.example",
    "phase1": "Discovery and user interviews",
    "phase2": "Design system and prototypes",
    "phase3": "Build, test and launch",
}


def make_md_to_docx(d: Path) -> None:
    import docx
    from docx.shared import Pt
    doc = docx.Document()
    doc.add_heading("Project Proposal", 0)
    p = doc.add_paragraph()
    p.add_run("Prepared for: ").bold = True
    p.add_run("{{client_name}}")
    doc.add_paragraph("Project: {{project_name}}")
    doc.add_paragraph("Timeline: {{start_date}} to {{end_date}}")
    doc.add_paragraph("Total budget: {{budget}}")
    doc.add_heading("Phases", 1)
    t = doc.add_table(rows=4, cols=2)
    t.style = "Table Grid"
    for i, (a, b) in enumerate([("Phase", "Description"), ("1", "{{phase1}}"), ("2", "{{phase2}}"), ("3", "{{phase3}}")]):
        t.cell(i, 0).text, t.cell(i, 1).text = a, b
    doc.add_heading("Contact", 1)
    doc.add_paragraph("{{contact_name}} ({{contact_email}})")
    fp = doc.add_paragraph("This proposal is valid for 30 days.")
    fp.runs[0].font.size = Pt(9)
    doc.save(d / "proposal_template.docx")
    f = DOCX_FIELDS
    _w(d / "project_brief.md", f"""
        # Brief: {f['project_name']}

        Our new client is **{f['client_name']}**. Their main contact is {f['contact_name']}, who can be reached at
        {f['contact_email']}.

        ## Scope and money

        - Kick-off: {f['start_date']}
        - Hand-over: {f['end_date']}
        - Agreed fixed price: {f['budget']} (excl. VAT)

        ## Work plan

        1. {f['phase1']}
        2. {f['phase2']}
        3. {f['phase3']}

        Internal note (do not put in the proposal): margin target 32%.
        """)


# =========================================================================== E7 js_refactor

def make_js_refactor(d: Path) -> None:
    _w(d / "package.json", """
        {
          "name": "cart",
          "version": "1.0.0",
          "private": true,
          "scripts": { "test": "node --test" }
        }
        """)
    _w(d / "lib/cart.js", """
        // Shopping cart totals. Prices are in cents.
        var SHIPPING_FREE_FROM = 5000;

        function lineTotal(line) {
          var t = line.price * line.qty;
          if (line.region === 'EU') {
            t = t + Math.round(t * 0.2);
          } else if (line.region === 'UK') {
            t = t + Math.round(t * 0.2);
          } else if (line.region === 'US') {
            t = t + Math.round(t * 0.07);
          }
          return t;
        }

        function cartTotal(cart) {
          var sum = 0;
          for (var i = 0; i < cart.lines.length; i++) {
            var line = cart.lines[i];
            var t = line.price * line.qty;
            if (cart.region === 'EU') {
              t = t + Math.round(t * 0.2);
            } else if (cart.region === 'UK') {
              t = t + Math.round(t * 0.2);
            } else if (cart.region === 'US') {
              t = t + Math.round(t * 0.07);
            }
            sum = sum + t;
          }
          var shipping = sum >= SHIPPING_FREE_FROM || sum === 0 ? 0 : 495;
          return sum + shipping;
        }

        function summary(cart) {
          var total = cartTotal(cart);
          var count = 0;
          for (var i = 0; i < cart.lines.length; i++) {
            count = count + cart.lines[i].qty;
          }
          return count + ' items, ' + (total / 100).toFixed(2);
        }

        module.exports = { lineTotal: lineTotal, cartTotal: cartTotal, summary: summary };
        """)
    _w(d / "test/cart.test.js", """
        const test = require('node:test');
        const assert = require('node:assert');
        const { lineTotal, cartTotal, summary } = require('../lib/cart');

        test('line total with tax', () => {
          assert.strictEqual(lineTotal({ price: 1000, qty: 2, region: 'EU' }), 2400);
          assert.strictEqual(lineTotal({ price: 999, qty: 1, region: 'US' }), 1069);
          assert.strictEqual(lineTotal({ price: 500, qty: 3, region: 'JP' }), 1500);
        });

        test('cart total adds shipping under the threshold', () => {
          assert.strictEqual(cartTotal({ region: 'US', lines: [{ price: 1000, qty: 1 }] }), 1070 + 495);
          assert.strictEqual(cartTotal({ region: 'EU', lines: [{ price: 2500, qty: 2 }] }), 6000);
          assert.strictEqual(cartTotal({ region: 'EU', lines: [] }), 0);
        });

        test('summary', () => {
          assert.strictEqual(summary({ region: 'UK', lines: [{ price: 1000, qty: 2 }, { price: 250, qty: 4 }] }),
            '6 items, 40.95');
        });
        """)
    _w(d / "TASK.md", """
        # Refactor request

        `lib/cart.js` has the tax logic copied twice and uses `var` everywhere. Please refactor it:

        - Move the tax calculation into a new module `lib/tax.js` that exports `taxFor(amountCents, region)`, returning
          the tax in cents (20% for EU and UK, 7% for US, 0 otherwise, rounded with `Math.round`). Add Canada: region
          `CA` has 5%.
        - `lib/cart.js` must use `taxFor` (require it from `./tax`) for both line and cart totals; no tax rates may stay
          in `cart.js`.
        - Replace every `var` in `lib/cart.js` with `const` or `let`.
        - Keep the exported API of `lib/cart.js` (`lineTotal`, `cartTotal`, `summary`) and its behaviour exactly the
          same, apart from the new CA rate. The existing tests (`npm test`) must keep passing; do not change them.
        """)


JS_HIDDEN = """
const test = require('node:test');
const assert = require('node:assert');
const { lineTotal, cartTotal, summary } = require('../lib/cart');
const { taxFor } = require('../lib/tax');

test('hidden: taxFor', () => {
  assert.strictEqual(taxFor(1000, 'EU'), 200);
  assert.strictEqual(taxFor(1000, 'UK'), 200);
  assert.strictEqual(taxFor(999, 'US'), 70);
  assert.strictEqual(taxFor(1010, 'CA'), 51);
  assert.strictEqual(taxFor(1000, 'JP'), 0);
});

test('hidden: cart behaviour kept', () => {
  assert.strictEqual(lineTotal({ price: 333, qty: 3, region: 'UK' }), 999 + 200);
  assert.strictEqual(lineTotal({ price: 1000, qty: 1, region: 'CA' }), 1050);
  assert.strictEqual(cartTotal({ region: 'CA', lines: [{ price: 1000, qty: 1 }, { price: 10, qty: 3 }] }),
    1000 + 50 + 30 + 2 + 495);
  assert.strictEqual(cartTotal({ region: 'US', lines: [{ price: 4673, qty: 1 }] }), 4673 + 327);
  assert.strictEqual(summary({ region: 'JP', lines: [{ price: 100, qty: 1 }] }), '1 items, 5.95');
});
"""


# =========================================================================== E8 three_files

def staff_rows() -> list[dict]:
    rng = random.Random(808)
    first = ["Anna", "Ben", "Carla", "David", "Eva", "Felix", "Greta", "Hugo", "Ida", "Jonas", "Kira", "Lars", "Mila",
             "Noah", "Olga", "Paul", "Rosa", "Sven", "Tara", "Uwe", "Vera", "Wim", "Yara", "Zoe"]
    last = ["Keller", "Brandt", "Vogel", "Hahn", "Lange", "Roth", "Engel", "Busch", "Frank", "Sauer", "Kraus", "Wolf"]
    rows = []
    for i in range(1, 41):
        rows.append({"employee_id": f"E{i:03d}", "name": f"{first[(i * 7) % len(first)]} {rng.choice(last)}",
                     "office": rng.choice(["Berlin", "Berlin", "Munich", "Hamburg"]),
                     "contract": rng.choice(["permanent", "permanent", "permanent", "fixed-term", "contractor"]),
                     "start_date": (dt.date(2018, 1, 1) + dt.timedelta(days=rng.randrange(0, 2900))).isoformat(),
                     "unused_leave_days": rng.randint(0, 24),
                     "grade": rng.choice(["G1", "G2", "G3"])})
    return rows


THREE_CONFIG = {"G1": 210.0, "G2": 265.0, "G3": 340.0}


def three_expected() -> dict:
    """Eligible: Berlin office, permanent contract, started on or before 2025-01-01 (one full year by 2026-01-01).
    Payout = min(unused days, 10) * day rate of the grade."""
    elig = []
    for r in staff_rows():
        if r["office"] == "Berlin" and r["contract"] == "permanent" and r["start_date"] <= "2025-01-01":
            days = min(int(r["unused_leave_days"]), 10)
            elig.append((r["employee_id"], days * THREE_CONFIG[r["grade"]]))
    top = max(elig, key=lambda x: (x[1], [-ord(c) for c in x[0]]))      # highest payout; ties: lowest id
    best = [e for e in elig if e[1] == top[1]]
    best.sort()
    return {"eligible_count": len(elig), "total_payout": round(sum(p for _, p in elig), 2),
            "top_employee_id": best[0][0]}


def make_three_files(d: Path) -> None:
    rows = staff_rows()
    with open(d / "staff.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    _w(d / "payroll_config.json", json.dumps({
        "currency": "EUR", "payout_reference_date": "2026-01-01",
        "day_rates": {"G1": 210.0, "G2": 265.0, "G3": 340.0},
        "legacy_day_rates_2024": {"G1": 190.0, "G2": 240.0, "G3": 310.0}}, indent=2) + "\n")
    _w(d / "leave_policy.md", """
        # Unused leave payout policy (Germany), effective 2026

        - The payout applies only to employees of the **Berlin** office. Munich and Hamburg follow their works-council
          agreements and are excluded here.
        - Only **permanent** employees qualify. Fixed-term staff and contractors are not eligible.
        - The employee must have completed at least one full year of service on the payout reference date given in the
          payroll configuration (a start date on or before the same calendar day one year earlier counts).
        - At most **10** unused days are paid out per employee; any days above that are forfeited.
        - Each paid day is paid at the current day rate for the employee's grade (not the legacy rates).
        """)


# =========================================================================== E9 csv_clean

def messy_rows() -> tuple[list[list[str]], list[tuple]]:
    """(messy rows incl. header, expected clean rows as tuples)."""
    rng = random.Random(909)
    firsts = ["alice", "bob", "carol", "dmitri", "erin", "farah", "george", "hana", "ivan", "julia", "kemal", "lena",
              "marco", "nadia", "oscar", "petra", "quinn", "rahul", "sofia", "tomas"]
    lasts = ["smith", "jones", "nguyen", "garcia", "patel", "kim", "novak", "silva", "berg", "costa"]
    people = []
    for i in range(1, 61):
        fn, ln = firsts[i % len(firsts)], lasts[(i * 3) % len(lasts)]
        email = f"{fn}.{ln}{i}@example.com"
        phone = f"{rng.randint(200, 989)}{rng.randint(200, 999)}{rng.randint(1000, 9999)}"
        date = dt.date(2023, 1, 1) + dt.timedelta(days=rng.randrange(0, 700))
        people.append({"id": i, "name": f"{fn} {ln}", "email": email, "phone": phone, "date": date})

    def messy_name(n):
        return rng.choice([n.upper(), n.title(), n, "  " + n.title() + " ", n.title().replace(" ", "  ")])

    def messy_email(e):
        return rng.choice([e, e.upper(), " " + e + " ", e.capitalize()])

    def messy_phone(p):
        a, b, c = p[:3], p[3:6], p[6:]
        return rng.choice([p, f"({a}) {b}-{c}", f"{a}-{b}-{c}", f"+1 {a} {b} {c}", f"1{p}", f"{a}.{b}.{c}"])

    def messy_date(d):
        return rng.choice([d.isoformat(), d.strftime("%m/%d/%Y"), d.strftime("%d %b %Y"), d.strftime("%Y/%m/%d")])

    out = [["id", "name", "email", "phone", "signup_date"]]
    clean = {}
    for p in people:
        row = [str(p["id"]), messy_name(p["name"]), messy_email(p["email"]), messy_phone(p["phone"]),
               messy_date(p["date"])]
        if p["id"] % 11 == 0:
            row[2] = rng.choice(["", "  ", "n/a"])           # no usable email: dropped
        else:
            clean[p["email"]] = (p["id"], p["name"].title(), p["email"], "+1" + p["phone"], p["date"].isoformat())
        out.append(row)
    # duplicates: same email (different case/spacing), new id, later or earlier date
    for k, p in enumerate([people[2], people[7], people[15], people[30], people[44]]):
        if p["id"] % 11 == 0:
            continue
        nid = 100 + k
        later = k % 2 == 0
        nd = p["date"] + dt.timedelta(days=30 if later else -30)
        out.append([str(nid), messy_name(p["name"]), messy_email(p["email"]).upper(), messy_phone(p["phone"]),
                    messy_date(nd)])
        if not later:                                         # the earlier signup wins, with its own id
            clean[p["email"]] = (nid, p["name"].title(), p["email"], "+1" + p["phone"], nd.isoformat())
    head, body = out[0], out[1:]
    rng.shuffle(body)
    exp = sorted(clean.values(), key=lambda r: r[0])
    return [head] + body, [tuple(str(x) for x in r) for r in exp]


def make_csv_clean(d: Path) -> None:
    rows, _ = messy_rows()
    with open(d / "customers_raw.csv", "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    _w(d / "CLEANING_RULES.md", """
        # Rules for clean_customers.csv

        Read `customers_raw.csv` and write `clean_customers.csv` with exactly these columns, in this order:
        `id,name,email,phone,signup_date`.

        1. Trim surrounding spaces from every field and collapse repeated inner spaces in names to one space.
        2. `name`: each word capitalised, the rest lower case (`ALICE SMITH` -> `Alice Smith`).
        3. `email`: lower case. Drop any row whose email is empty or not a valid address (must contain `@`).
        4. `phone`: E.164 for US numbers: `+1` followed by the 10 digits (`(415) 555-0100`, `415.555.0100`,
           `+1 415 555 0100` and `14155550100` all become `+14155550100`).
        5. `signup_date`: ISO `YYYY-MM-DD`. Inputs use `YYYY-MM-DD`, `MM/DD/YYYY`, `YYYY/MM/DD` or `DD Mon YYYY`.
        6. Duplicates are rows with the same email after rule 3. Keep only the row with the earliest signup_date
           (keep its own id and other fields).
        7. Sort by `id` ascending (numeric). Write a header row; no index column; plain comma-separated, no quotes
           unless needed.
        """)


# =========================================================================== all

MAKERS = {"pdf_facts": make_pdf_facts, "csv_sales": make_csv_sales, "fix_test": make_fix_test,
          "flask_feature": make_flask_feature, "bash_rename": make_bash_rename, "md_to_docx": make_md_to_docx,
          "js_refactor": make_js_refactor, "three_files": make_three_files, "csv_clean": make_csv_clean}


def main() -> None:
    for tid, fn in MAKERS.items():
        d = FIX / tid
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        fn(d)
        print("wrote", d)


if __name__ == "__main__":
    main()
