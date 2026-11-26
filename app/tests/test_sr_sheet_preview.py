"""SR-02, SR-03, SR-04: the spreadsheet preview, as a finance user saw it.

SR-02: numbers showed without their format (412000 where Excel shows 412,000).
SR-03: a formula with a cached value showed its value, but nothing told the page what the formula was.
SR-04: hidden sheets looked like any other sheet."""
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from meshpool_harness.ui import previews as P
from meshpool_harness.ui import workbench as W


@pytest.fixture
def proj(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    P._JOBS.clear()
    return root


def budget(path: Path, cached: dict | None = None) -> None:
    """A budget like the tester's: #,##0 numbers, SUM totals, a hidden Assumptions sheet. ``cached`` puts a cached
    value on formula cells (as Excel does when it saves), keyed by cell reference."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws["A1"] = "Northwind Ltd - 2026 operating budget (actuals)"
    ws.append([])
    ws.append(["Cost line", "Q1", "Q2", "Q3", "YTD"])
    for r, (name, q) in enumerate([("Salaries", (412000, 418500, 431200)), ("Rent", (54000, 54000, 55800))], start=4):
        ws.append([name, *q, f"=SUM(B{r}:D{r})"])
        for c in "BCDE":
            ws[f"{c}{r}"].number_format = "#,##0"
    ws.append(["Total", "=SUM(B4:B5)", "=SUM(C4:C5)", "=SUM(D4:D5)", "=SUM(E4:E5)"])
    for c in "BCDE":
        ws[f"{c}6"].number_format = "#,##0"
    ws["B7"] = 0.031
    ws["B7"].number_format = "0.0%"
    ws["C7"] = -1234.5
    ws["C7"].number_format = "#,##0.00;(#,##0.00)"
    ws["D7"] = 2026                                     # General: no digit groups, as in Excel
    hid = wb.create_sheet("Assumptions")
    hid.append(["Inflation", 0.031])
    hid.sheet_state = "hidden"
    wb.save(path)
    if cached:
        tmp = path.with_suffix(".tmp")
        with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    xml = data.decode()
                    for ref, val in cached.items():
                        xml, n = re.subn(rf'(<c r="{ref}"[^>]*>)<f>([^<]*)</f><v\s*/?>(</v>)?', rf"\1<f>\2</f><v>{val}</v>", xml)
                        assert n == 1, ref
                    data = xml.encode()
                zout.writestr(item, data)
        shutil.move(tmp, path)


def test_cached_values_show_in_the_cell_format_with_the_formula_beside(proj):
    budget(proj / "b.xlsx", cached={"B6": 466000, "C6": 472500, "D6": 487000, "E6": 1425500})
    s = P.meta(str(proj), "b.xlsx")["sheets"][0]
    assert s["rows"][3][:4] == ["Salaries", "412,000", "418,500", "431,200"]       # SR-02
    assert s["rows"][5][:5] == ["Total", "466,000", "472,500", "487,000", "1,425,500"]
    assert s["formulas"]["5:1"] == "=SUM(B4:B5)" and s["formulas"]["5:4"] == "=SUM(E4:E5)"   # SR-03: shown on hover
    assert s["rows"][6][1:4] == ["3.1%", "(1,234.50)", "2026"]


def test_a_formula_never_calculated_still_shows_the_formula(proj, monkeypatch):
    """Without LibreOffice to calculate it (the preview computes it otherwise: test_rr_xlsx_values.py)."""
    from meshpool_harness import xl_values
    monkeypatch.setattr(xl_values, "values", lambda *a, **k: None)
    budget(proj / "b.xlsx")
    s = P.meta(str(proj), "b.xlsx")["sheets"][0]
    assert s["rows"][5][:2] == ["Total", "=SUM(B4:B5)"]
    assert "5:1" not in s.get("formulas", {})


def test_hidden_sheets_are_marked(proj):
    budget(proj / "b.xlsx")
    sheets = P.meta(str(proj), "b.xlsx")["sheets"]
    assert [x["name"] for x in sheets] == ["Budget", "Assumptions"]
    assert not sheets[0].get("hidden") and sheets[1]["hidden"] is True


@pytest.mark.parametrize("v,fmt,want", [
    (412000, "#,##0", "412,000"),
    (1234.5, "#,##0.00", "1,234.50"),
    (-1234.5, "#,##0.00", "-1,234.50"),
    (-1234, "#,##0_);[Red](#,##0)", "(1,234)"),
    (0.5, "0%", "50%"),
    (1234.5, '"$"#,##0.00', "$1,234.50"),
    (3.5, '"$"#,##0.00', "$3.50"),                     # SR-14: a price list (shown as 3.5)
    (3.5, "$#,##0.00", "$3.50"),
    (3.5, "_($* #,##0.00_);_($* (#,##0.00);_($* \"-\"??_);_(@_)", "$3.50"),
    (0, "_($* #,##0.00_);_($* (#,##0.00);_($* \"-\"??_);_(@_)", "$-"),
    (1234.5, "[$€-407]#,##0.00", "€1,234.50"),
    (1234567, '#,##0,"K"', "1,235K"),
    (1.5, "0.##", "1.5"),
    (120.5, "General", "120.5"),
    (12345.678, "0.00E+00", "12345.678"),            # not covered: the plain number, never a wrong one
    (0.25, "# ?/?", "0.25"),
    (-0.0001, "0.00", "0.00"),
])
def test_number_formats(v, fmt, want):
    assert P._number(v, fmt) == want
