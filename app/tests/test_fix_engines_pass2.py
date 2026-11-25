import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_labels():
    from engines.credit.analysis import match_label as m
    assert m("Income before income taxes") == "pretax_income"
    assert m("Cash flow from operations") == "cfo"
    assert m("Property and equipment, net") == "ppe"
    assert m("Cash and cash equivalents") == "cash"
    assert m("Income taxes") == "income_tax"


def test_from_table_multi_sheet_and_duplicate_years(tmp_path):
    from openpyxl import Workbook
    from engines.credit import analysis as A
    wb = Workbook()
    wb.active.title = "Cover"
    wb.active["A1"] = "Acme"
    is_ = wb.create_sheet("IS")
    is_.append(["Line", "FY2023 Actual", "FY2023 Budget", "FY2024"])
    is_.append(["Revenue", 100, 999, 120])
    bs = wb.create_sheet("BS")
    bs.append(["Line", "FY2023", "FY2024"])
    bs.append(["Total assets", 500, 550])
    p = tmp_path / "a.xlsx"
    wb.save(p)
    fin = A.from_table(str(p))
    assert fin.years == ["FY2023", "FY2024"]
    assert fin.v("FY2023", "revenue") == 100 and fin.v("FY2024", "total_assets") == 550
    assert any("repeats year" in u for u in fin.sources["_unmapped"])


def test_sheet_titles():
    from engines.sheets import builder as B
    wb = B.banker_workbook("t", "p", middle=("Inputs", "Bob's Inputs", "Calc"))
    ws = wb["Bob's Inputs"]
    B.input_cell(ws, "B4", 5, name="x")
    assert "'Bob''s Inputs'" in str(wb.defined_names["x"].attr_text)
    with pytest.raises(ValueError):
        B.new_workbook(("A very long sheet name over thirty one chars",))
    with pytest.raises(ValueError):
        B.new_workbook(("a/b",))


def test_finish_check_bad_zip(tmp_path):
    from engines.sheets.engine import finish_check
    bad = tmp_path / "bad.xlsx"
    bad.write_text("a,b\n1,2\n")

    class WS:
        root = tmp_path
    assert "not a valid" in finish_check("", [str(bad)], WS())


def test_split_passages_no_midword_cut():
    from engines.research.library import split_passages
    text = "\n".join(f"line {i} revenue driven by data center demand grew strongly" for i in range(40))
    for t, _, _ in split_passages(text):
        assert not t.endswith(("d", "dat", "cent")) or t.endswith("demand")
        assert len(t) <= 900


def test_percent_words_and_ellipsis_quote():
    from engines.data.checks import trace_numbers, check_quotes
    assert trace_numbers("The value is 1,234.5 and 45 percent.", "v 1234.5 share 0.45") == []
    assert check_quotes('Quote: "... ... ... ..."', {"s": "anything at all here"})


def test_ctl_not_running(monkeypatch, tmp_path, capsys):
    from engines.browser import ctl
    monkeypatch.setenv("MH_BROWSER_PORT", "0")
    monkeypatch.setattr(ctl, "call", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("ctl.json")))
    assert ctl.main(["snapshot"]) == 1
    assert "not running" in capsys.readouterr().out


def test_testcmd_other_languages(tmp_path):
    from engines.code.testcmd import discover
    (tmp_path / "go.mod").write_text("module x\n")
    assert "go test" in discover(str(tmp_path), None, None)


def test_scores_as_strings():
    from engines.review.personas import parse_review
    import json
    r = parse_review(json.dumps({"scores": {"integrity": "9", "assumptions": "8.5"}, "issues": []}), "md", "workbook")
    assert r["scores"] == {"integrity": 9.0, "assumptions": 8.5}


def test_treasury_null_tenor():
    from engines.sheets import market as MK
    def ent(d, v):
        val = '<d:BC_10YEAR m:type="Edm.Double" m:null="true" />' if v is None else f'<d:BC_10YEAR m:type="Edm.Double">{v}</d:BC_10YEAR>'
        return f'<entry><content><m:properties><d:NEW_DATE m:type="Edm.DateTime">{d}T00:00:00</d:NEW_DATE>{val}</m:properties></content></entry>'
    xml = ent("2026-11-19", "4.10") + ent("2026-11-20", None) + ent("2026-11-21", "4.30")
    assert MK.treasury_yield(text=xml)["date"] == "2026-11-21"
    xml = ent("2026-11-19", "4.10") + ent("2026-11-20", "4.20") + ent("2026-11-21", None)
    assert MK.treasury_yield(text=xml)["date"] == "2026-11-20"


def test_unicode_file_stem_pattern():
    import re
    assert re.sub(r"[^\w]+", "_", "株式会社テスト").strip("_") == "株式会社テスト"


def test_retired_shares_guard():
    from engines.sheets import builder as B
    import inspect
    assert "=IF(" in inspect.getsource(B).split('line("retired"')[1][:120]


def test_dcf_block_prev_in_first_column_is_rejected():
    from engines.sheets import builder as B
    wb = B.banker_workbook("t", "p")
    ws = wb["Calc"]
    with pytest.raises(ValueError, match="no prior column"):
        B.dcf_block(ws, 3, [2026, 2027, 2028], [{"key": "vol", "start": 10, "formula": "{vol@prev}*2"}])
