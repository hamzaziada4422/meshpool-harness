"""Office direct: formulas written this turn are recalculated through LibreOffice before the answer (sb-56921)."""
import os
import time

import openpyxl
import pytest

from meshpool_harness import office_runtime
from meshpool_harness.agent import core as C

ASK = ("I am struggling to get a SUMPRODUCT formula to work correctly. In in.xlsx the formula in C15 yields an "
       "incorrect result. How do I fix this?")
OLD = "=SUMPRODUCT(--(MOD(COLUMN(C3:N3)-COLUMN(C3),4)=0)*($A$3:$A$10=B15)*(C3:N3))"
GOOD = "=SUMPRODUCT((MOD(COLUMN($C$3:$N$10)-COLUMN($C$3),4)=0)*($A$3:$A$10=B15)*$C$3:$N$10)"


@pytest.fixture(autouse=True)
def _lo(monkeypatch):
    monkeypatch.setenv("MH_ALLOW_SYSTEM_SOFFICE", "1")
    if not office_runtime.find_soffice():
        pytest.skip("no LibreOffice here")


def _book(path, c15):
    wb = openpyxl.Workbook()
    ws = wb.active
    rows = {3: (12345, {"C": 500, "G": 11}), 7: (12365, {"C": 14, "G": 260, "K": 20})}
    for r, (key, cells) in rows.items():
        ws[f"A{r}"] = key
        for col, v in cells.items():
            ws[f"{col}{r}"] = v
    ws["B15"], ws["C15"] = 12365, c15
    wb.save(path)


def _turn(tmp_path, c15):
    _book(tmp_path / "in.xlsx", OLD)
    os.utime(tmp_path / "in.xlsx", (1, 1))
    t0 = time.time() - 1
    _book(tmp_path / "out.xlsx", c15)
    C._recent_office.__dict__.pop("cache", None)
    return C._formula_result_miss(ASK, tmp_path, t0)


def test_an_error_after_recalculation_is_caught(tmp_path):
    msg = _turn(tmp_path, "=SUMPRODUCT(A3:A10*C3:N3)+FOO(1)")
    assert "C15" in msg and "error" in msg


def test_a_fix_that_gives_the_same_wrong_result_is_caught(tmp_path):
    msg = _turn(tmp_path, OLD.replace("--(", "("))
    assert "same results" in msg and "C15" in msg


def test_a_real_fix_passes(tmp_path):
    assert _turn(tmp_path, GOOD) == ""


def test_untouched_formulas_are_not_judged(tmp_path):
    assert _turn(tmp_path, OLD) == ""
