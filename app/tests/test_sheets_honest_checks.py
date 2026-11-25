"""A Checks tab must check the figures, and say so when they do not tie.

End-to-end failure: a simple budget's check was written on the Checks tab as =B12-SUM(B10:G10), so it read the
Checks tab's own empty cells, showed OK, and the agent told the user the checks tie. Now:
- B.add_check qualifies bare references with sheet= and refuses them without it;
- workbook QA fails a check formula that reads only empty cells, or only typed values on its own tab;
- a check that does not tie is reported after recalculation.
And the "LibreOffice is not installed" wording appears only when no soffice exists at all."""
import shutil
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.sheets import builder as B  # noqa: E402
from engines.sheets import check as CLI  # noqa: E402
from engines.sheets import qa  # noqa: E402
from meshpool_harness import office_runtime as OR  # noqa: E402

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]


def _budget(tmp_path, *, check_formula=None, sheet=None, rent=1200, total_formula="=SUM(B10:G10)"):
    """Budget tab: monthly costs in B10:G10, total in B12. Returns the saved path."""
    wb = B.banker_workbook("Household budget", "Six-month budget", middle=("Budget",))
    ws = wb["Budget"]
    B.header(ws, 9, ["Item", *MONTHS])
    ws["A10"] = "Rent ($)"
    for i in range(6):
        B.input_cell(ws, f"{'BCDEFG'[i]}10", rent)
    ws["A12"] = "Total rent ($)"
    B.formula(ws, "B12", total_formula, "usd")
    B.add_check(wb, "Total ties to the monthly rows", check_formula or "='Budget'!B12-SUM('Budget'!B10:G10)",
                **({"sheet": sheet} if sheet else {}))
    p = tmp_path / "budget.xlsx"
    wb.save(p)
    return str(p)


def _hollow_book(tmp_path) -> str:
    """The end-to-end workbook as it was saved: the check formula written verbatim on the Checks tab."""
    p = _budget(tmp_path)
    wb = load_workbook(p)
    wb["Checks"]["B4"] = "=B12-SUM(B10:G10)"
    wb.save(p)
    return p


# ---------- 1. the builder writes checks that point at the figures ----------------------------------------------------
def test_add_check_refuses_a_bare_reference_that_would_read_the_checks_tab():
    wb = B.banker_workbook("t", "p", middle=("Budget",))
    with pytest.raises(ValueError, match="no sheet name"):
        B.add_check(wb, "Total ties", "=B12-SUM(B10:G10)")
    assert wb["Checks"]["B4"].value is None                       # nothing half-written


def test_add_check_sheet_qualifies_every_bare_reference(tmp_path):
    p = _budget(tmp_path, check_formula="=B12-SUM(B10:G10)", sheet="Budget")
    assert load_workbook(p)["Checks"]["B4"].value == "='Budget'!B12-SUM('Budget'!B10:G10)"


def test_add_check_leaves_names_and_qualified_references_alone():
    wb = B.banker_workbook("t", "p", middle=("Budget", "Calc"))
    B.add_check(wb, "x", "=IF(ISERROR(CHOOSE(scenario,0,0,0)),1,0)+Calc!C4-$B$2", sheet="Budget")
    assert wb["Checks"]["B4"].value == "=IF(ISERROR(CHOOSE(scenario,0,0,0)),1,0)+Calc!C4-'Budget'!$B$2"
    with pytest.raises(ValueError, match="no sheet named"):
        B.add_check(wb, "y", "=B1", sheet="Nope")


# ---------- 2. the QA guard -----------------------------------------------------------------------------------------
def test_qa_fails_a_check_that_reads_only_empty_cells_on_its_own_tab(tmp_path):
    hits = qa.hollow_checks(load_workbook(_hollow_book(tmp_path)))
    assert [(h["cell"], h["kind"]) for h in hits] == [("Checks!B4", "check_refs_empty")]
    assert "'Budget'!B12" in hits[0]["detail"]


def test_qa_fails_a_check_that_reads_only_typed_values_on_its_own_tab(tmp_path):
    p = _budget(tmp_path)
    wb = load_workbook(p)
    ch = wb["Checks"]
    ch["K10"], ch["K11"] = 7200, 7200                              # numbers typed on the Checks tab
    ch["B4"] = "=K10-K11"
    wb.save(p)
    hits = qa.hollow_checks(load_workbook(p))
    assert [(h["cell"], h["kind"]) for h in hits] == [("Checks!B4", "check_refs_own_sheet")]


def test_qa_accepts_a_check_on_the_figures_and_the_status_and_master_cells(tmp_path):
    assert qa.hollow_checks(load_workbook(_budget(tmp_path))) == []


def test_qa_follows_defined_names_to_empty_cells(tmp_path):
    p = _budget(tmp_path)
    wb = load_workbook(p)
    B.define_name(wb, "budget_total", "'Budget'!$H$40")              # a name pointing at an empty cell
    wb["Checks"]["B4"] = "=budget_total-0"
    wb.save(p)
    assert [h["kind"] for h in qa.hollow_checks(load_workbook(p))] == ["check_refs_empty"]


def test_check_gate_rejects_the_end_to_end_workbook(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "recalc", lambda *a, **k: (_ for _ in ()).throw(OR.Unavailable()))   # structure only
    r = qa.check(_hollow_book(tmp_path))
    assert any(i["check"] == "check_refs_empty" and i["cell"] == "Checks!B4" for i in r["issues"])


def test_forge_workbook_rules_fail_the_hollow_check_too(tmp_path):
    """The general agent's workbook QA (forge X12) applies the same guard, not only the sheets engine."""
    from engines.forge.validate_xlsx import validate
    fr = validate(_hollow_book(tmp_path), do_recalc=False)
    hit = [f for f in fr["failures"] if f["check"] == "check_refs_empty"]
    assert hit and hit[0]["rule"] == "X12" and hit[0]["cell"] == "B4" and fr["rules"]["X12"] == "fail"


# ---------- 3. a check that does not tie is reported after recalculation ----------------------------------------------
@pytest.mark.skipif(not OR.find_soffice(), reason="needs LibreOffice (MH_ALLOW_SYSTEM_SOFFICE=1 in a checkout)")
def test_a_check_that_does_not_tie_is_reported_after_recalculation(tmp_path):
    p = _budget(tmp_path, total_formula="=SUM(B10:F10)")            # the total leaves out June
    r = qa.check(p)
    assert r["stats"]["recalculated"]
    bad = [i for i in r["issues"] if i["check"] == "check_not_tying"]
    assert {i["cell"] for i in bad} == {"Checks!C4", "Checks!F3"}
    row = next(i for i in bad if i["cell"] == "Checks!C4")
    assert "Total ties to the monthly rows" in row["detail"] and "do not tie" in row["detail"]


@pytest.mark.skipif(not OR.find_soffice(), reason="needs LibreOffice (MH_ALLOW_SYSTEM_SOFFICE=1 in a checkout)")
def test_a_check_that_ties_is_not_reported(tmp_path):
    r = qa.check(_budget(tmp_path))
    assert r["stats"]["recalculated"]
    assert not [i for i in r["issues"] if i["check"].startswith("check_")]


@pytest.mark.skipif(not OR.find_soffice(), reason="needs LibreOffice (MH_ALLOW_SYSTEM_SOFFICE=1 in a checkout)")
def test_the_checker_cli_rejects_both_failures(tmp_path, capsys):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert CLI.main([_hollow_book(tmp_path / "a")]) == 1
    assert "check_refs_empty Checks!B4" in capsys.readouterr().out
    assert CLI.main([_budget(tmp_path / "b", total_formula="=SUM(B10:F10)")]) == 1
    out = capsys.readouterr().out
    assert "check_not_tying Checks!C4" in out and "PASS" not in out


# ---------- the reason values were not recalculated ------------------------------------------------------------------
def _no_bundled(monkeypatch, system: str | None, packaged: bool = False):
    monkeypatch.delenv("MH_SOFFICE", raising=False)
    monkeypatch.delenv("MH_ALLOW_SYSTEM_SOFFICE", raising=False)
    monkeypatch.setattr(OR, "_bundled_exe", lambda: None)
    monkeypatch.setattr(OR, "_system_copy", lambda: system)
    monkeypatch.setattr(OR, "is_packaged", lambda: packaged)


def test_a_system_libreoffice_meshpool_does_not_use_is_not_called_missing(monkeypatch):
    _no_bundled(monkeypatch, "/usr/bin/soffice")
    why = OR.unavailable_reason()
    assert "not installed" not in why and "bundled LibreOffice is not available" in why
    assert "MH_ALLOW_SYSTEM_SOFFICE=1" in why
    _no_bundled(monkeypatch, "/usr/bin/soffice", packaged=True)
    assert "does not use the LibreOffice installed on this computer" in OR.unavailable_reason()


def test_not_installed_only_when_there_is_no_soffice_at_all(monkeypatch):
    _no_bundled(monkeypatch, None)
    assert OR.unavailable_reason() == "LibreOffice is not installed"
    monkeypatch.setattr(OR, "_bundled_exe", lambda: "/opt/meshpool/soffice")
    assert OR.unavailable_reason() == ""


def test_the_raised_error_carries_the_real_reason_and_is_still_recognised(monkeypatch):
    _no_bundled(monkeypatch, "/usr/bin/soffice")
    e = OR.Unavailable()
    assert "not installed" not in str(e) and OR.is_unavailable(e)
    assert OR.is_unavailable(RuntimeError("LibreOffice not installed"))           # older text still recognised
    assert not OR.is_unavailable(RuntimeError("conversion failed"))


def _plain_book(tmp_path) -> str:
    wb = Workbook()
    ws = wb.active
    ws["A1"], ws["B1"], ws["B2"] = "x", 1, "=B1*2"
    p = tmp_path / "plain.xlsx"
    wb.save(p)
    return str(p)


@pytest.mark.skipif(shutil.which("soffice") is None and shutil.which("libreoffice") is None,
                    reason="needs a system LibreOffice on PATH")
def test_checker_cli_names_the_real_reason(tmp_path, monkeypatch, capsys):
    from engines.sheets import engine as E
    p = _plain_book(tmp_path)
    monkeypatch.setattr(E, "finish_check", lambda *a, **k: None)
    monkeypatch.setattr(OR, "_bundled_exe", lambda: None)
    monkeypatch.delenv("MH_SOFFICE", raising=False)
    monkeypatch.delenv("MH_ALLOW_SYSTEM_SOFFICE", raising=False)       # a system copy exists but is not used
    assert CLI.main([p]) == 0
    out = capsys.readouterr().out
    assert "not installed" not in out
    assert "values were not recalculated (Meshpool's bundled LibreOffice is not available" in out


def test_forge_qa_summary_names_the_real_reason(tmp_path, monkeypatch):
    from engines.forge import tools as FT
    _no_bundled(monkeypatch, "/usr/bin/soffice")
    line = FT.qa_summary([_plain_book(tmp_path)])
    assert "not installed" not in line
    assert "values not recalculated (Meshpool's bundled LibreOffice is not available" in line
    _no_bundled(monkeypatch, None)
    assert "values not recalculated (LibreOffice is not installed)" in FT.qa_summary([_plain_book(tmp_path)])
