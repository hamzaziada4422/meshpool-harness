"""Undo of an office-tool edit in the chat's project folder (persona run, Maya's Flowers): "raise all prices by 10%"
went through edit_office_file (set_range, in_place) and Undo then said "No files in this chat's folder had changed. Not
restored: Bouquet Price List.xlsx (changed since...)". The file kept the new prices. Cause: at the end of the turn
meshpool wrote the formula results into the workbook (core._cache_formula_values, xl_values.fill), outside any tool
call, so the turn's record held the hash from before that write and Undo took meshpool's own write for the user's.

These tests run the real agent loop with a scripted model and the real office MCP server (its own process)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import xl_values  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import mcp as M  # noqa: E402
from meshpool_harness.agent import office_mcp_server as O  # noqa: E402
from meshpool_harness.checkpoints import Checkpoints  # noqa: E402
from meshpool_harness.ui import agent_api as A  # noqa: E402

BOOK = "Bouquet Price List.xlsx"
PRICES = [69, 42, 48, 150, 65, 38, 55, 72, 88, 135, 28, 59]
EDITED = "Bouquet Price List (edited).xlsx"


def _values(path: Path) -> dict:
    """{(sheet, cell): value}: what Excel, Quick Look and a phone show for a cell (the cached results of formulas)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        return {(ws.title, c.coordinate): c.value for ws in wb.worksheets for row in ws.iter_rows() for c in row}
    finally:
        wb.close()


def _compute_stub(path, timeout=25):
    """What LibreOffice would calculate for the formula cells with no stored result (no LibreOffice needed)."""
    need = xl_values._uncached(path)
    if not need:
        return None
    wb = openpyxl.load_workbook(path)
    try:
        prices = [wb["Inputs"][f"C{r}"].value for r in range(4, 16)]
        out = {}
        for (sheet, cell), f in need.items():
            if f.startswith("=Inputs!C"):
                out[(sheet, cell)] = wb["Inputs"][f[len("=Inputs!"):]].value
            elif f.startswith("=MIN("):
                out[(sheet, cell)] = min(prices)
            elif f.startswith("=MAX("):
                out[(sheet, cell)] = max(prices)
        return out or None
    finally:
        wb.close()


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_GRADER", "0")
    monkeypatch.setenv("MH_LOOK_REVIEW", "0")
    monkeypatch.setenv("MH_LEARN", "0")
    C.ensure_home()


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    """Maya's folder with the price list the sheets engine made: formulas on Outputs and Checks, results stored."""
    p = tmp_path / "Maya's Flowers"
    p.mkdir()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inputs"
    ws["A3"], ws["C3"] = "Bouquet", "Price"
    for i, v in enumerate(PRICES):
        ws[f"A{4 + i}"], ws[f"C{4 + i}"] = f"Bouquet {i + 1}", v
    out = wb.create_sheet("Outputs")
    for i in range(12):
        out[f"A{4 + i}"], out[f"C{4 + i}"] = f"=Inputs!A{4 + i}", f"=Inputs!C{4 + i}"
    chk = wb.create_sheet("Checks")
    chk["B4"], chk["B5"] = "=MIN(Inputs!C4:C15)", "=MAX(Inputs!C4:C15)"
    wb.save(p / BOOK)
    monkeypatch.setattr(xl_values, "_compute", _compute_stub)
    assert xl_values.fill(p / BOOK) > 0                # as the engine's turn left it: results stored
    monkeypatch.setenv("MESHPOOL_PROJECT", str(p))
    return p


@pytest.fixture()
def office(proj, tmp_path):
    """The real office MCP server, in its own process, as the app runs it."""
    cfg = tmp_path / "mcp.json"
    env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1]), "MESHPOOL_HOME": str(tmp_path / "home"),
           "MESHPOOL_PROJECT": str(proj), "PYTHONDONTWRITEBYTECODE": "1"}
    cfg.write_text(json.dumps({"mcpServers": {"meshpool-office": {
        "command": sys.executable, "args": ["-m", "meshpool_harness.agent.office_mcp_server"], "env": env}}}))
    m = M.Manager(roots=[str(proj)], path=cfg).start(timeout=60, use_health=False)
    names = [t["function"]["name"] for t in m.schemas()]
    tool = next((n for n in names if n.endswith("edit_office_file")), None)
    if tool is None:
        m.close()
        pytest.skip("the office MCP server did not start here: " + str(m.status()))
    yield m, tool
    m.close()


def _call(name, args, cid):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _script(monkeypatch, replies):
    """A scripted model: one reply per request, then "Done."."""
    replies = list(replies)

    def fake(self_, base, key, body, stream):
        nxt = replies.pop(0) if replies else {"role": "assistant", "content": "Done."}
        return {"choices": [{"message": nxt, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, False
    monkeypatch.setattr(core.Agent, "_post", fake)


def _raise_prices(proj, office, monkeypatch, tmp_path, *, in_place=True, ask="Raise all prices by 10%."):
    """One turn, as the user ran it: the model edits the workbook with set_range, the user clicks Allow."""
    m, tool = office
    new = [[round(v * 1.1, 2)] for v in PRICES]
    args = {"path": BOOK, "operations": [{"op": "set_range", "sheet": "Inputs", "start": "C4", "values": new}]}
    if in_place is not None:
        args["in_place"] = in_place
    _script(monkeypatch, [_call(tool, args, "c1"), {"role": "assistant", "content": "All 12 prices are 10% higher."}])
    asked = []
    a = core.Agent(str(proj), mcp=m, emit=lambda e: None, mode="ask",
                   approve=lambda *x, **k: (asked.append(x[1]), ("yes", ""))[1])
    cp = Checkpoints(str(proj), tmp_path / "store", mode="journal")
    a.checkpoint = cp
    cp.begin_turn(ask, 1, 0)
    a.send(ask)
    cp.end_turn()
    assert asked, "the edit was put to the user"
    return cp, [v[0] for v in new]


def _prices(path: Path) -> list:
    v = _values(path)
    return [v[("Inputs", f"C{r}")] for r in range(4, 16)]


def test_undo_of_an_in_place_set_range_puts_the_original_prices_back(proj, office, monkeypatch, tmp_path):
    before = (proj / BOOK).read_bytes()
    cp, new = _raise_prices(proj, office, monkeypatch, tmp_path)
    assert _prices(proj / BOOK) == new
    assert not (proj / EDITED).exists()
    r = cp.revert_to_before(1)
    assert (proj / BOOK).read_bytes() == before, r
    assert _prices(proj / BOOK) == PRICES
    assert r["restored"] == [BOOK] and r["kept"] == [], r
    msg, _ = A.undo_message(r)
    assert msg == f"Undone. Restored 1 file ({BOOK}).", msg


def test_undo_also_takes_back_the_formula_results_meshpool_wrote_at_the_end_of_the_turn(proj, office, monkeypatch,
                                                                                         tmp_path):
    """xl_values.fill rewrote the workbook after the edit was booked: Undo then saw a new hash and kept the file."""
    filled = []
    real = xl_values.fill
    monkeypatch.setattr(xl_values, "fill", lambda p, *a, **k: filled.append(Path(p).name) or real(p, *a, **k))
    before = _values(proj / BOOK)
    cp, new = _raise_prices(proj, office, monkeypatch, tmp_path)
    assert filled == [BOOK]
    v = _values(proj / BOOK)
    assert v[("Outputs", "C7")] == new[3] and v[("Checks", "B5")] == max(new)     # results written at turn end
    r = cp.revert_to_before(1)
    assert _values(proj / BOOK) == before, r
    assert r["restored"] == [BOOK] and not r["kept"], r
    msg, detail = A.undo_message(r)
    assert "Restored 1 file" in msg and "Not restored" not in msg and "had changed" not in msg, msg


def test_a_file_the_user_changed_after_the_turn_is_kept_and_named(proj, office, monkeypatch, tmp_path):
    cp, new = _raise_prices(proj, office, monkeypatch, tmp_path)
    wb = openpyxl.load_workbook(proj / BOOK)
    wb["Inputs"]["C4"] = 99                              # Maya changes one price herself after the turn
    wb.save(proj / BOOK)
    mine = (proj / BOOK).read_bytes()
    r = cp.revert_to_before(1)
    assert (proj / BOOK).read_bytes() == mine
    assert r["kept"] == [BOOK] and not r["restored"], r
    msg, detail = A.undo_message(r)
    assert "No files in this chat's folder had changed" not in msg, msg
    assert BOOK in msg and "changed after" in msg, msg
    assert BOOK in detail, detail


def test_an_edit_without_in_place_changes_the_file_itself_when_no_app_has_it_open(proj, office, monkeypatch,
                                                                                   tmp_path):
    """The second try: the model left out in_place, a "(edited)" copy appeared beside the file the user asked to
    change (the approval card had said "Change Bouquet Price List.xlsx?"), and Undo left the copy behind."""
    before = (proj / BOOK).read_bytes()
    cp, new = _raise_prices(proj, office, monkeypatch, tmp_path, in_place=None)
    assert not (proj / EDITED).exists(), sorted(x.name for x in proj.iterdir())
    assert _prices(proj / BOOK) == new
    r = cp.revert_to_before(1)
    assert (proj / BOOK).read_bytes() == before and r["restored"] == [BOOK], r
    assert sorted(x.name for x in proj.iterdir()) == [BOOK]


def test_a_workbook_open_in_another_app_still_gets_a_copy(proj, monkeypatch):
    """PF-02 stays: with the lock file of an app that has it open, the edit goes to a copy, and the tool says why."""
    (proj / f".~lock.{BOOK}#").write_text("x")
    msg, err = O.edit_office_file(BOOK, [{"op": "set_cell", "sheet": "Inputs", "cell": "C4", "value": 1}])
    assert not err and (proj / EDITED).exists(), msg
    assert _prices(proj / BOOK) == PRICES


def test_an_explicit_copy_is_still_a_copy(proj):
    msg, err = O.edit_office_file(BOOK, [{"op": "set_cell", "sheet": "Inputs", "cell": "C4", "value": 1}],
                                  in_place=False)
    assert not err and (proj / EDITED).exists() and _prices(proj / BOOK) == PRICES, msg
