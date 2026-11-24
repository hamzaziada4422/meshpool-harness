"""Sheets engine number formats (K-01: invoices built by the engine showed every amount as a date: the engine wrote
the token "num2" as the Excel format code, and Excel reads its "m" as a month)."""
import pytest
from openpyxl import Workbook
from openpyxl.styles.numbers import is_date_format

from engines.sheets import builder as B


@pytest.mark.parametrize("tok", ["num2", "num0", "pct2", "usd0", "text", "date", "usd", "usd2", "int", "num", "pct"])
def test_tokens_map_to_real_codes(tok):
    code = B.fmt_code(tok)
    assert code != tok or tok == "General"
    assert is_date_format(code) == (tok == "date")


def test_excel_strings_pass_through():
    assert B.fmt_code('#,##0.00') == '#,##0.00' and B.fmt_code("General") == "General"


def test_unknown_token_is_refused():
    with pytest.raises(ValueError, match="unknown number format 'num3'"):
        B.fmt_code("num3")


def test_input_cell_amounts_are_numbers_not_dates():
    ws = Workbook().active
    c = B.input_cell(ws, "A1", 1140.0, "num2")
    t = B.input_cell(ws, "A2", "INV-2001", "text")
    f = B.formula(ws, "A3", "=SUM(A1:A1)", "num2")
    assert c.number_format == '#,##0.00;(#,##0.00)' and not c.is_date
    assert t.number_format == "@" and not f.is_date
