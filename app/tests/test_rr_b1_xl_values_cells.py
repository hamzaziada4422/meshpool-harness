"""Release review B1: storing formula results matched across cells. A formula cell with no <v> let the lazy match run
on to the next empty <v>, and that whole span was written back as one cell, so the cells in between were deleted."""
from __future__ import annotations

import os
import shutil
import zipfile

import pytest

from meshpool_harness import office_runtime, xl_values

needs_office = pytest.mark.skipif(
    not (os.environ.get("MH_ALLOW_SYSTEM_SOFFICE") == "1" and shutil.which("soffice")) and not office_runtime.find_soffice(),
    reason="LibreOffice is not available here")

XML = '<c r="A1"><f>B1*10</f></c><c r="B1"><v>2</v></c><c r="A2"><f>B2*100</f><v></v></c>'


def test_each_cell_is_filled_on_its_own_and_no_cell_is_lost():
    out, n = xl_values._fill_xml(XML, {"A1": 20, "A2": 0})
    assert n == 2
    assert out == ('<c r="A1"><f>B1*10</f><v>20</v></c><c r="B1"><v>2</v></c>'
                   '<c r="A2"><f>B2*100</f><v>0</v></c>')


def test_only_formula_cells_with_an_empty_or_missing_value_change():
    xml = ('<row r="1"><c r="A1" s="3"/><c r="B1" s="1" t="str"><f>A1&amp;"x"</f><v>old</v></c>'
           '<c r="C1"><v>5</v></c><c s="2" r="D1"><f t="shared" ref="D1:D2" si="0">C1*2</f><v/></c></row>'
           '<cols><col min="1" max="2"/></cols>')
    out, n = xl_values._fill_xml(xml, {"A1": 1, "B1": "new", "C1": 9, "D1": 10})
    assert n == 1                                      # A1 has no formula, B1 has a value, C1 has no formula
    assert out == xml.replace('C1*2</f><v/></c>', 'C1*2</f><v>10</v></c>')


def _book_without_cached_values(path):
    """A workbook whose formula cells have no stored result: A1 with no <v> at all (as some writers leave it), A2 and
    B3 with an empty <v> (as openpyxl writes them), and plain cells between them."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"] = "=B1*10"
    ws["B1"] = 2
    ws["A2"] = "=B2*100"
    ws["B2"] = 3
    ws["A3"] = "label"
    ws["B3"] = "=A2+A1"
    ws["C3"] = 7
    wb.save(path)
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                text = data.decode()
                assert '<c r="A1"><f>B1*10</f><v></v></c>' in text
                data = text.replace('<c r="A1"><f>B1*10</f><v></v></c>', '<c r="A1"><f>B1*10</f></c>').encode()
            zout.writestr(info, data)
    os.replace(tmp, path)


@needs_office
def test_a_real_workbook_keeps_every_cell_and_gets_its_values(tmp_path):
    import openpyxl
    f = tmp_path / "book.xlsx"
    _book_without_cached_values(f)
    assert xl_values.fill(f) == 3
    v = openpyxl.load_workbook(f, data_only=True)["Data"]
    assert [[c.value for c in row] for row in v.iter_rows(min_row=1, max_row=3, max_col=3)] == [
        [20, 2, None], [300, 3, None], ["label", 320, 7]]
    w = openpyxl.load_workbook(f)["Data"]
    assert (w["A1"].value, w["A2"].value, w["B3"].value) == ("=B1*10", "=B2*100", "=A2+A1")
