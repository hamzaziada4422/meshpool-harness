"""Office direct: a formula that swaps a year the request names for TODAY()/NOW() is caught before the answer
(SpreadsheetBench sb-39515, f133d912: 3 of 6 reps wrote YEAR(TODAY())-1 for "the previous year (2022)" and failed)."""
import openpyxl

from meshpool_harness import capabilities as CAP
from meshpool_harness.agent import core as C

ASK = ("The current formula only extracts the value for the current year (2023), but I need it to extract the value "
       "for January of the previous year (2022) for all rows of that year.")


def _book(path, formula):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["B1"], ws["O2"] = "Mar", 2023, formula
    wb.save(path)


def test_today_in_place_of_a_named_year_is_caught(tmp_path):
    _book(tmp_path / "out.xlsx", '=INDEX($C2:$N2,MATCH(IF($B2=YEAR(TODAY())-1,"Jan",$A2),$C$1:$N$1,0))')
    msg = C._volatile_date_miss(ASK, tmp_path, 0)
    assert "out.xlsx" in msg and "literal" in msg


def test_literal_years_pass(tmp_path):
    _book(tmp_path / "out.xlsx", '=INDEX($C2:$N2,IF($B2=2022,1,MATCH($A2,$C$1:$N$1,0)))')
    assert C._volatile_date_miss(ASK, tmp_path, 0) == ""


def test_a_request_for_todays_date_may_use_today(tmp_path):
    _book(tmp_path / "out.xlsx", "=TODAY()")
    assert C._volatile_date_miss("Add a column that shows today's date next to each row", tmp_path, 0) == ""


def test_files_older_than_the_turn_are_ignored(tmp_path):
    _book(tmp_path / "in.xlsx", "=NOW()")
    import os
    os.utime(tmp_path / "in.xlsx", (1, 1))
    assert C._volatile_date_miss(ASK, tmp_path, 10) == ""


def test_each_sheet_with_a_volatile_formula_is_named(tmp_path):
    wb = openpyxl.Workbook()
    wb.active["A1"] = "=YEAR(NOW())"
    wb.create_sheet("S2")["B2"] = "=TODAY()-1"
    wb.save(tmp_path / "two.xlsx")
    msg = C._volatile_date_miss(ASK, tmp_path, 0)
    assert "two.xlsx!Sheet!A1" in msg and "two.xlsx!S2!B2" in msg
