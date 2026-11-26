"""Release review item 18: a workbook written with openpyxl has formulas and no stored results. The preview calculates
them (once per file version, through the kept LibreOffice) and a turn that wrote the file stores the results in it."""
from __future__ import annotations

import os
import shutil
import time

import pytest

from meshpool_harness import chats, office_runtime, xl_values
from meshpool_harness.agent import core
from meshpool_harness.ui import previews as P
from meshpool_harness.ui import workbench as W
from test_om_bugs018 import _idle, home, proj as om_proj  # noqa: F401

needs_office = pytest.mark.skipif(
    not (os.environ.get("MH_ALLOW_SYSTEM_SOFFICE") == "1" and shutil.which("soffice")) and not office_runtime.find_soffice(),
    reason="LibreOffice is not available here")


def make_book(path):
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Area"
    ws.append(["Item", "Width", "Height", "Area"])
    for i, (w, h) in enumerate([(2, 3), (4, 5), (6, 7)], start=2):
        ws.append([f"r{i}", w, h, f"=B{i}*C{i}"])
    ws.append(["Total", None, None, "=SUM(D2:D4)"])
    ws["A1"].font = Font(bold=True)
    ws["D2"].number_format = "0.00"
    wb.save(path)


@needs_office
def test_the_preview_shows_computed_numbers_and_keeps_the_formula_on_hover(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    make_book(root / "a.xlsx")
    sheet = P.meta(str(root), "a.xlsx")["sheets"][0]
    assert [r[3] for r in sheet["rows"]] == ["Area", "6.00", "20", "42", "68"]
    assert sheet["formulas"]["1:3"] == "=B2*C2" and sheet["formulas"]["4:3"] == "=SUM(D2:D4)"
    t = time.time()
    P.meta(str(root), "a.xlsx")                                               # the same version: from the cache
    assert time.time() - t < 1


@needs_office
def test_fill_stores_the_results_and_keeps_formulas_and_formatting(tmp_path):
    import openpyxl
    f = tmp_path / "b.xlsx"
    make_book(f)
    assert xl_values.fill(f) == 4
    v = openpyxl.load_workbook(f, data_only=True)["Area"]
    assert [v[f"D{i}"].value for i in range(2, 6)] == [6, 20, 42, 68]
    w = openpyxl.load_workbook(f)["Area"]
    assert w["D2"].value == "=B2*C2" and w["D5"].value == "=SUM(D2:D4)"
    assert w["A1"].font.bold and w["D2"].number_format == "0.00"
    assert xl_values.fill(f) == 0                                            # nothing left to do
    t = time.time()                                                           # the kept session is warm now
    g = tmp_path / "c.xlsx"
    make_book(g)
    assert xl_values.fill(g) == 4
    assert time.time() - t < 1, time.time() - t


@needs_office
def test_a_turn_that_wrote_a_workbook_leaves_cached_values_in_it(home, om_proj, monkeypatch):
    import openpyxl
    calls = []

    def fake(self, turn):
        for k, v in {"in": 100, "cached": 0, "out": 10, "calls": 1}.items():
            turn[k] += v
            self.usage[k] += v
        make_book(om_proj / "area.xlsx")                                      # as a python or write path would
        calls.append(1)
        return {"role": "assistant", "content": "Made area.xlsx."}, False
    monkeypatch.setattr(core.Agent, "_chat", fake)
    v = chats.create({"workdir": str(om_proj), "no_mcp": True})
    s = chats.get(v["id"])
    s.send("make a workbook with areas")
    _idle(s, 60)
    d = openpyxl.load_workbook(om_proj / "area.xlsx", data_only=True)["Area"]
    assert [d[f"D{i}"].value for i in range(2, 6)] == [6, 20, 42, 68]
    assert openpyxl.load_workbook(om_proj / "area.xlsx")["Area"]["D2"].value == "=B2*C2"
