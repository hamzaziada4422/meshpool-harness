import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_trace_numbers_sign_scale_hyphen():
    from engines.data.checks import trace_numbers
    assert trace_numbers("Sales fell 5.2%, down $12,000.", "pct_change -5.2\ndiff -12000.0") == []
    assert trace_numbers("Revenue was 1.2 million.", "total 1200000") == []
    assert trace_numbers("COVID-19 cut sales", "x 5") == []
    assert trace_numbers("Revenue was 777.", "x 5")        # still catches real fabrications


def test_credit_amount_parsing():
    from engines.credit.engine import parse_request as P
    assert P("ACME has $50m of revenue and wants a $10m term loan")["amount"] == 10.0
    assert P("term loan of €5m at 6%")["amount"] == 5.0
    assert "amount" not in P("need a loan")
    assert "ticker" not in P("Loan of $5m (USD) for 7 years")
    assert P("Ticker: AAPL loan $10m")["ticker"] == "AAPL"
    assert P("Borrower with fixed assets; need $3m").get("floating", True) is True
    assert P("a fixed-rate loan of $3m")["floating"] is False


def test_parse_cell_locales():
    from engines.credit.analysis import _parse_cell
    assert _parse_cell("1.000,5") == (1000.5, True)
    assert _parse_cell("12,5") == (12.5, True)
    assert _parse_cell("1,234") == (1234.0, True)
    assert _parse_cell("(1,234.5)") == (-1234.5, True)
    assert _parse_cell("12%")[1] is False


def test_from_table_errors_units_and_encoding(tmp_path):
    from engines.credit import analysis as A
    bad = tmp_path / "bad.csv"
    bad.write_text("Item,Q1,Q2\nRevenue,1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        A.from_table(str(bad))
    ok = tmp_path / "ok.csv"
    ok.write_bytes("($ in thousands)\nItem,FY2023\nRevenue,\"5,000\"\nM\xfcller GmbH,3\n".encode("cp1252"))
    fin = A.from_table(str(ok))
    assert fin.v("FY2023", "revenue") == pytest.approx(5.0)


def test_split_passages_pages_and_find_source_filter():
    from engines.research.library import split_passages
    long = "x " * 120
    txt = f"\n## Page 1\n{long}\n\n## Page 2\nShort first para 42.\n\nSecond paragraph " + "y " * 100
    out = split_passages(txt)
    assert {p for _, p, _ in out} >= {1, 2}
    assert any("Short first para 42" in t for t, _, _ in out)


def test_docx_tables(tmp_path):
    docx = pytest.importorskip("docx")
    from engines.research.library import _to_docx
    p = _to_docx("# T\n\n| A | B |\n|---|---|\n| 1 | **2** |\n", tmp_path / "x.docx")
    d = docx.Document(p)
    assert len(d.tables) == 1 and d.tables[0].cell(1, 1).text == "2"
    assert all("|" not in q.text and "**" not in q.text for q in d.paragraphs)


def test_define_name_validation_and_check_tol():
    from engines.sheets import builder as B
    wb = B.banker_workbook("t", "p", "me", "2024-01-01")
    for bad in ("Q1", "my rate", "TAX2", "R1C1"):
        with pytest.raises(ValueError):
            B.define_name(wb, bad, "'Inputs'!$B$4")
    B.define_name(wb, "rate_2025", "'Inputs'!$B$4")
    B.add_check(wb, "a", "=1-1")
    B.add_check(wb, "b", "=0.4", tol=0.5)
    assert "<=D5" in wb["Checks"]["C5"].value
    assert wb["Checks"]["D5"].value == 0.5


def test_pipeline_applies_not_hijacked():
    from engines.sheets.pipeline import applies
    assert applies("Capital allocation: buybacks vs dividends") == "capital_allocation"
    assert applies("Build a three-statement model and DCF for ACME including share buybacks") is None
    assert applies("Forecast EPS; assume a special dividend of $1") is None


def test_qa_recalc_failure_is_dict(monkeypatch, tmp_path):
    import subprocess
    from openpyxl import Workbook
    from engines.sheets import qa
    wb = Workbook()
    wb.active["A1"] = "=1+1"
    p = tmp_path / "a.xlsx"
    wb.save(p)

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "soffice")
    monkeypatch.setattr(qa, "recalc", boom)
    r = qa.check(str(p))
    assert all(isinstance(i, dict) and "check" in i for i in r["issues"])
    assert any(i["check"] == "recalc_failed" for i in r["issues"])


def test_memory_learn_writes_to_home(tmp_path, monkeypatch):
    from engines.review import memory as M
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))

    class C:
        def chat(self, **k):
            return {"choices": [{"message": {"content": '{"lessons": ["use f-strings -> not plain strings"]}'}}]}
    added = M.learn("excel", [{"persona": "md", "issues": [{"severity": "major", "issue": "x"}]}], C(), "m")
    assert added and (tmp_path / "memory" / "lessons_excel.md").exists()
    assert not (M.HERE / "lessons_excel.md").exists()


def test_testcmd_no_python_not_false_claim(monkeypatch, tmp_path):
    from engines.code import testcmd
    monkeypatch.setattr(testcmd.shutil, "which", lambda n: None)
    assert "NOT installed" not in testcmd.discover(str(tmp_path), None, None)


def test_fit_value_dd_mm():
    from engines.browser import ctl

    class L:
        def get_attribute(self, *a, **k):
            return "date"
    assert ctl._fit_value(L(), "25/12/2024") == "2024-12-25"
    assert ctl._fit_value(L(), "12/25/2024") == "2024-12-25"
    assert ctl._fit_value(L(), "04/03/2018") == "2018-04-03"
