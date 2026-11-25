import openpyxl
from meshpool_harness.agent.office_mcp_server import edit_office_file


def _book(tmp_path):
    p = tmp_path / "b.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "S"
    for r in range(1, 6):
        ws.append([r, r * 10])
    wb.save(p)
    return p


def test_aliases_and_new_ops(tmp_path):
    p = _book(tmp_path)
    msg, err = edit_office_file(str(p), [
        {"op": "set_formula", "sheet": "S", "cell": "C1", "formula": "SUM(A1:B1)"},
        {"op": "set_value", "sheet": "S", "cell": "D1", "value": 7},
        {"op": "clear_cell", "sheet": "S", "range": "B4:B5"},
        {"op": "delete_rows", "sheet": "S", "row": 2, "count": 1},
        {"op": "set_range", "sheet": "S", "start": "E1", "values": [[1, 2], [3, 4]]},
        {"op": "add_sheet", "name": "T"},
        {"op": "freeze_panes", "sheet": "S", "cell": "A2"},
    ], in_place=True)
    assert not err, msg
    wb = openpyxl.load_workbook(p); ws = wb["S"]
    assert ws["C1"].value == "=SUM(A1:B1)" and ws["D1"].value == 7
    assert ws["A2"].value == 3                     # row 2 deleted
    assert ws["B3"].value is None                  # old B4 cleared, shifted up
    assert ws["F2"].value == 4 and "T" in wb.sheetnames and ws.freeze_panes == "A2"


def test_unknown_op_lists_all(tmp_path):
    msg, err = edit_office_file(str(_book(tmp_path)), [{"op": "bogus"}], in_place=True)
    assert err and "delete_rows" in msg and "set_range" in msg
