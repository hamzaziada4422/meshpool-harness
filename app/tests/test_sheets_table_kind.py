"""A list, table or simple summary is one sheet with the data first (Maya's Flowers): a 12-row bouquet price list came
back with Cover, Inputs, Calc, Outputs and Checks tabs, range columns and a formula legend, and opened on the Cover.
Only a real model (forecast, budget with assumptions, scenarios) gets the model tabs. Scripted model, no live calls."""
import shlex
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.sheets import engine as E  # noqa: E402
from meshpool_harness.core.client import MockClient, tool_call  # noqa: E402
from meshpool_harness.office_intent import asks_for, table_job  # noqa: E402

TASK = "make a price list for 12 bouquets with name, size and price"
BOUQUETS = [(f"Bouquet {i}", "Large" if i % 2 else "Small", 20 + 5 * i) for i in range(1, 13)]

# What the model wrote before: the banker structure, opening on the Cover.
BANKER = f"""import sys; sys.path.insert(0, {str(ROOT)!r})
from engines.sheets import builder as B
wb = B.banker_workbook("Price list", "Bouquet prices")
ws = wb["Outputs"]
B.header(ws, 3, ["Bouquet", "Size", "Price ($)", "Range low", "Range high"])
for i, (n, s, p) in enumerate({BOUQUETS!r}, 4):
    ws.cell(i, 1, n); ws.cell(i, 2, s); ws.cell(i, 3, p); ws.cell(i, 4, p * 0.9); ws.cell(i, 5, p * 1.1)
wb.active = 0
wb.save("price_list.xlsx")
"""

TABLE = f"""import sys; sys.path.insert(0, {str(ROOT)!r})
from engines.sheets import builder as B
wb = B.table_workbook("Price list")
B.table(wb.active, {BOUQUETS!r}, columns=["Bouquet", "Size", "Price ($)"], formats={{"Price ($)": "usd2"}})
wb.save("price_list.xlsx")
"""


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("MH_SHEETS_JOB", "MH_SHEETS_SOURCES", "MH_SHEETS_ASKS", "MH_SHEETS_REQUEST", "MH_ENGINE_MAX_TURNS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MH_DOSSIER", "0")


def _check(path: str) -> str:
    return f"PYTHONPATH={shlex.quote(str(ROOT))} {shlex.quote(sys.executable)} -m engines.sheets.check {path}"


def test_the_request_picks_the_plan():
    assert table_job(TASK)
    assert table_job("Price list spreadsheet for 12 bouquets. Columns: Bouquet name, What's in it, Price (USD).")
    assert table_job("a staff contact list with name, phone and email")
    assert not table_job("Build a 12-month budget for a coffee shop with assumptions")
    assert not table_job("a 3-year revenue forecast with base, bull and bear scenarios")
    assert not table_job("Build a DCF valuation for ACME")


def test_a_price_list_is_one_sheet_with_the_data_first(tmp_path):
    """The engine gives the table plan; its gate refuses the five-tab banker book, and the delivered file is one sheet
    with the header in row 1, opening on it."""
    c = MockClient([
        tool_call("write_file", {"path": "banker.py", "content": BANKER}),
        tool_call("bash", {"command": f"{shlex.quote(sys.executable)} banker.py"}),
        tool_call("bash", {"command": _check("price_list.xlsx")}),
        tool_call("write_file", {"path": "build_workbook.py", "content": TABLE}),
        tool_call("bash", {"command": f"{shlex.quote(sys.executable)} build_workbook.py"}),
        tool_call("bash", {"command": _check("price_list.xlsx")}),
        tool_call("finish", {"answer": "A price list for 12 bouquets.", "evidence": [_check("price_list.xlsx")],
                             "artifacts": ["price_list.xlsx"]}),
    ])
    r = E.solve(TASK, workdir=str(tmp_path), task_id="t", max_turns=12, client=c, model="mock")
    system = c.requests[0]["messages"][0]["content"]
    assert system == E.TABLE_SYSTEM and "banker_workbook" not in system
    refused = [m["content"] for m in c.requests[3]["messages"] if m["role"] == "tool"][-1]
    assert "table_structure" in refused and "Cover" in refused and "[exit 1]" in refused
    assert r["ok"] is True, r["answer"]
    wb = load_workbook(tmp_path / "price_list.xlsx")
    assert wb.sheetnames == ["Price list"] and wb.active.title == "Price list"
    rows = list(wb.active.iter_rows(values_only=True))
    assert rows[0] == ("Bouquet", "Size", "Price ($)") and len(rows) == 13 and rows[1] == BOUQUETS[0]


def test_a_model_keeps_the_model_plan(tmp_path):
    c = MockClient([tool_call("bash", {"command": "echo hi"})])
    E.solve("Build a 12-month budget for a coffee shop with assumptions", workdir=str(tmp_path), task_id="t",
            max_turns=1, client=c, model="mock")
    assert c.requests[0]["messages"][0]["content"] == E.SYSTEM


def test_table_writes_a_large_frame_in_one_step(tmp_path):
    pd = pytest.importorskip("pandas")
    from engines.sheets import builder as B
    df = pd.DataFrame({"region": ["North", "South"] * 25000, "revenue": [1.5, 2.0] * 25000})
    wb = B.table_workbook("Data")
    t = B.table(wb.active, df, formats={"revenue": "usd2"}, total={"revenue": "sum"})
    assert (t["first"], t["last"], t["total_row"]) == (2, 50001, 50002)
    assert wb.active["B50002"].value == "=SUM(B2:B50001)" and wb.active.freeze_panes == "A2"


def test_a_sheet_named_in_the_request_is_a_rename_it_asks_for(tmp_path):
    """priya018: "a summary workbook ... One sheet 'Summary'" from rooms.xlsx; the checker refused the new name as
    an edit that lost the source's sheet names, and the sheet stayed "Rooms"."""
    from openpyxl import Workbook
    asked = ("Create a summary workbook of room areas from rooms.xlsx. One sheet 'Summary' with columns Room, "
             "Length (m), Width (m), Area (m2) and a total row.")
    assert "rename" in asks_for(asked)
    assert "rename" in asks_for("Put it on a sheet called Totals")
    assert "rename" not in asks_for("Sort it and add a cover sheet and a checks tab")
    src = Workbook()
    src.active.title = "Rooms"
    src.active.append(("Room", "Length (m)", "Width (m)"))
    src.active.append(("Kitchen", 4, 3.5))
    src.save(tmp_path / "rooms.xlsx")
    out = Workbook()
    out.active.title = "Summary"
    out.active.append(("Room", "Length (m)", "Width (m)", "Area (m2)"))
    out.active.append(("Kitchen", 4, 3.5, "=B2*C2"))
    out.save(tmp_path / "rooms (edited).xlsx")
    assert E.derive_issues(str(tmp_path / "rooms (edited).xlsx"), [str(tmp_path / "rooms.xlsx")],
                           asks_for(asked)) == []
