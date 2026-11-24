"""Sheets engine on an edit or derivation of a workbook the user has (sort, filter, add a column ...): the output keeps
the source's sheets and layout. No Cover tab, no title or blank rows above the header, no Checks tab, unless asked;
"build me a model" jobs keep the banker structure. OfficeBench: "sort score.xlsx descending by midterm1 into
sorted_score.xlsx" came back as Cover / Sheet1 (title row, blank row, header) / Checks. No model calls."""
import os
import shlex
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engines  # noqa: E402
from engines.sheets import engine as E  # noqa: E402
from meshpool_harness.core.client import MockClient, tool_call  # noqa: E402
from meshpool_harness.office_intent import asks_for, derive_job  # noqa: E402

ROWS = [("name", "midterm1", "midterm2"), ("Ann", 70, 80), ("Bo", 90, 60), ("Cy", 50, 70), ("Di", 85, 90)]
TASK = "Sort score.xlsx descending by midterm1 into sorted_score.xlsx"

SORT = """from openpyxl import load_workbook
wb = load_workbook("score.xlsx")
ws = wb.active
head, *rows = [list(r) for r in ws.iter_rows(values_only=True)]
rows.sort(key=lambda r: r[1], reverse=True)
for i, r in enumerate([head] + rows, 1):
    for j, v in enumerate(r, 1):
        ws.cell(i, j, v)
wb.save("sorted_score.xlsx")
"""

BANKER = """from openpyxl import load_workbook
wb = load_workbook("score.xlsx")
ws = wb.active
data = [list(r) for r in ws.iter_rows(values_only=True)]
ws.title = "Sheet1"
ws.delete_rows(1, ws.max_row)
ws["A1"] = "Scores sorted by midterm 1"
for i, r in enumerate([data[0]] + sorted(data[1:], key=lambda r: -r[1]), 3):
    for j, v in enumerate(r, 1):
        ws.cell(i, j, v)
wb.create_sheet("Cover", 0)
wb.create_sheet("Checks")
wb.active = 0
wb.save("sorted_score.xlsx")
"""


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("MH_SHEETS_JOB", "MH_SHEETS_SOURCES", "MH_SHEETS_ASKS", "MH_ENGINE_MAX_TURNS", "MH_DOSSIER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MH_DOSSIER", "0")


def _score(folder: Path) -> Path:
    wb = Workbook()
    for r in ROWS:
        wb.active.append(r)
    wb.save(folder / "score.xlsx")
    return folder / "score.xlsx"


def _check_cmd(path: str) -> str:
    return f"PYTHONPATH={shlex.quote(str(ROOT))} {shlex.quote(sys.executable)} -m engines.sheets.check {path}"


def test_job_kind_rules():
    assert derive_job(TASK, ["/x/score.xlsx"])
    assert derive_job("Add a Total column to grades.xlsx", [])                    # named in the request
    assert derive_job("Turn orders.csv into a workbook with one row per order", ["/x/orders.csv"])
    assert not derive_job("Build a three-statement financial model from hist.xlsx", ["/x/hist.xlsx"])
    assert not derive_job("Make a sales dashboard from sales.xlsx", ["/x/sales.xlsx"])
    assert not derive_job("Build a 12-month budget for a coffee shop", [])         # nothing to derive from
    assert asks_for(TASK) == set()
    assert asks_for("Sort it and add a cover sheet and a checks tab with a title row") == {"cover", "checks", "title"}
    assert "rename" in asks_for("Rename Sheet1 in data.xlsx to Sales")


def test_sort_into_new_file_keeps_one_sheet_with_the_header_in_row_1(tmp_path):
    """The regression: the engine's prompt is the edit prompt (no banker workbook), its finish gate refuses the
    Cover / title row / Checks version, and the delivered file is one sheet "Sheet" with the header in row 1."""
    src = _score(tmp_path)
    c = MockClient([
        tool_call("write_file", {"path": "banker.py", "content": BANKER}),
        tool_call("bash", {"command": f"{shlex.quote(sys.executable)} banker.py"}),
        tool_call("bash", {"command": _check_cmd("sorted_score.xlsx")}),            # the checker CLI: derive gate
        tool_call("write_file", {"path": "sort.py", "content": SORT}),
        tool_call("bash", {"command": f"{shlex.quote(sys.executable)} sort.py"}),
        tool_call("bash", {"command": _check_cmd("sorted_score.xlsx")}),
        tool_call("finish", {"answer": "Sorted by midterm1, descending.", "evidence": [_check_cmd("sorted_score.xlsx")],
                             "artifacts": ["sorted_score.xlsx"]}),
    ])
    r = E.solve(TASK, workdir=str(tmp_path), task_id="t", max_turns=12, client=c, model="mock", files=[str(src)])
    system = c.requests[0]["messages"][0]["content"]
    assert system == E.DERIVE_SYSTEM and "banker_workbook" not in system and "Cover" in system
    tools = [m["content"] for m in c.requests[3]["messages"] if m["role"] == "tool"]
    assert "keep_structure Cover" in tools[-1] and "keep_structure Checks" in tools[-1]
    assert "header is in row 3, not row 1" in tools[-1] and "[exit 1]" in tools[-1]
    assert r["ok"] is True, r["answer"]
    wb = load_workbook(tmp_path / "sorted_score.xlsx")
    assert wb.sheetnames == ["Sheet"] and wb.active.title == "Sheet"
    got = [list(x) for x in wb["Sheet"].iter_rows(values_only=True)]
    assert got[0] == list(ROWS[0]) and [x[1] for x in got[1:]] == [90, 85, 70, 50]
    assert "MH_SHEETS_JOB" not in os.environ                                         # restored after the run


def test_derive_gate_accepts_plain_and_refuses_banker_structure(tmp_path, monkeypatch):
    src = _score(tmp_path)
    plain = tmp_path / "plain.xlsx"
    wb = load_workbook(src)
    wb.save(plain)
    monkeypatch.setenv("MH_SHEETS_JOB", "derive")
    monkeypatch.setenv("MH_SHEETS_SOURCES", str(src))
    ws = type("W", (), {"root": tmp_path})()
    assert E.finish_check("", [str(plain)], ws) is None                  # no formulas, no Cover: fine for an edit
    assert E.derive_issues(str(plain), [str(src)], set()) == []
    wb = load_workbook(src)
    wb.active.title = "Scores"                                           # every source sheet name lost
    wb.active.insert_rows(1, 2)
    wb.active["A1"] = "Scores"
    wb.create_sheet("Cover", 0)
    bad = tmp_path / "bad.xlsx"
    wb.save(bad)
    kinds = [i["cell"] for i in E.derive_issues(str(bad), [str(src)], set())]
    assert kinds == ["Cover", "", "Scores!A3"]          # Cover tab, no source sheet name, header moved down
    issues = E.derive_issues(str(bad), [str(src)], {"cover", "rename", "title"})
    assert issues == []                                                  # asked for: allowed
    wb = load_workbook(src)
    wb.active.insert_rows(1, 2)
    wb.active["A1"] = "Scores"
    wb.save(bad)
    assert [i["cell"] for i in E.derive_issues(str(bad), [str(src)], set())] == ["Sheet!A3"]   # title row above


def test_build_jobs_keep_the_banker_gate(tmp_path, monkeypatch):
    """A model built from nothing still needs its Cover tab: the job kind only relaxes edits."""
    plain = tmp_path / "m.xlsx"
    wb = Workbook()
    wb.active["A1"] = "x"
    wb.save(plain)
    ws = type("W", (), {"root": tmp_path})()
    out = E.finish_check("", [str(plain)], ws)
    assert out and "no Cover tab" in out
    c = MockClient([tool_call("bash", {"command": "echo hi"})])
    E.solve("Build a three-statement financial model for ACME", workdir=str(tmp_path / "b"), task_id="t",
            max_turns=1, client=c, model="mock")
    assert c.requests[0]["messages"][0]["content"] == E.SYSTEM


def test_engine_turn_cap(monkeypatch):
    assert engines.max_turns(50) == 50                                   # unset: the default stays
    monkeypatch.setenv("MH_ENGINE_MAX_TURNS", "20")
    assert engines.max_turns(50) == 20 and engines.max_turns(4) == 4     # a cap, never a raise
    monkeypatch.setenv("MH_ENGINE_MAX_TURNS", "junk")
    assert engines.max_turns(30) == 30


def test_sheets_engine_honours_the_turn_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_ENGINE_MAX_TURNS", "3")
    c = MockClient([tool_call("bash", {"command": f"echo explore {i}"}) for i in range(10)])
    r = E.solve("Build a model", workdir=str(tmp_path), task_id="t", client=c, model="mock")
    assert r["reason"] == "turn cap 3 reached; no workbook saved" and len(c.requests) == 3
