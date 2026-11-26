"""One deliverable, one card (persona run). Lina saw "3 files changed" and three identical region_summary.xlsx cards;
Sara saw the PDF card twice. Server side: the turn's changed list counts distinct files. Browser side: the row of
cards drawn at turn end is found again when the checkpoint comes, even after the receipt line and the chips went in
between, so a second row is never added. The browser test is skipped when no Chromium can start here."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from meshpool_harness.ui import workbench as W  # noqa: E402
from test_wf_ui018 import _fake_chat, app, page  # noqa: E402,F401  (the real app and a browser page)


def test_three_job_copies_of_one_workbook_are_one_change(tmp_path):
    for job in ("j1", "j2", "j3"):
        d = tmp_path / "Meshpool outputs" / job
        d.mkdir(parents=True)
        (d / "region_summary.xlsx").write_bytes(f"book {job}".encode())
    turns = [{"n": 3, "changed": [{"path": f"Meshpool outputs/{j}/region_summary.xlsx", "status": "added"}
                                  for j in ("j1", "j2", "j3")]}]
    got = [c["path"] for c in W.own_changes(turns, tmp_path)[0]["changed"]]
    assert got == ["Meshpool outputs/j3/region_summary.xlsx"], got


def test_a_file_listed_twice_and_its_job_copy_count_once(tmp_path):
    (tmp_path / "Meshpool outputs" / "j1").mkdir(parents=True)
    (tmp_path / "Meshpool outputs" / "j1" / "Board_Update_Q3.pdf").write_bytes(b"old")
    (tmp_path / "Board_Update_Q3.pdf").write_bytes(b"new")
    (tmp_path / "Board_Update_Q3.pptx").write_bytes(b"deck")     # a card is only for a file that exists
    turns = [{"n": 1, "changed": [{"path": "Board_Update_Q3.pdf", "status": "added"},
                                  {"path": "Meshpool outputs/j1/Board_Update_Q3.pdf", "status": "added"},
                                  {"path": "Board_Update_Q3.pdf", "status": "added"},
                                  {"path": "Board_Update_Q3.pptx", "status": "modified"}]}]
    got = [c["path"] for c in W.own_changes(turns, tmp_path)[0]["changed"]]
    assert got == ["Board_Update_Q3.pdf", "Board_Update_Q3.pptx"], got


def test_the_turns_cards_stay_one_row_when_the_receipt_comes_first(page):
    """Sara: turn_end drew a row with the PDF, the receipt and the chips went in right under the footer, and the
    checkpoint drew a second row (PDF and deck) above them: the PDF card twice."""
    _fake_chat(page, "t-cards-once", view={"workdir": page.app["work"]})
    got = page.evaluate("""() => { const cur = S.cur;
        applyEv({type: "user", text: "Make the PDF", i: 0});
        cur.tpaths = ["Board_Update_Q3.pdf"];                // what the convert tool said it saved
        cur.view.turns = [{n: 3, changed: [{path: "Board_Update_Q3 (edited).pptx", status: "added"},
                                           {path: "Board_Update_Q3.pdf", status: "added"}]}];
        for (const e of [{type: "assistant", text: "Done.", i: 1},
                         {type: "turn_end", i: 2},
                         {type: "task_receipt", secs: 219, cost: 0.02, calls: 8, i: 3},
                         {type: "checkpoint", turns: [cur.view.turns[0]], i: 4}]) applyEv(e);
        const rows = [...document.querySelectorAll('#col .pvcards')];
        return {rows: rows.length, names: [...document.querySelectorAll('#col .pvcard .pvcm b')].map(b => b.textContent)};
    }""")
    assert got["rows"] == 1, got
    assert sorted(got["names"]) == ["Board_Update_Q3 (edited).pptx", "Board_Update_Q3.pdf"], got
    assert page.errors == [], page.errors
