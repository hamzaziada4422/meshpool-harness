"""Daniel, 0.0.19, item 6: the deck job finished and wrote its file, then the last model turn came back empty, and the
chat showed a red "The model returned an empty reply" with no summary. Scripted model, no model calls."""
from __future__ import annotations

import json

from pptx import Presentation

from meshpool_harness import run_task
from meshpool_harness.agent import core as A


def _script(monkeypatch, msgs):
    it = iter(msgs)

    def fake_chat(self, turn):
        turn["calls"] += 1
        return next(it), False
    monkeypatch.setattr(A.Agent, "_chat", fake_chat)


def _deck(path, n):
    prs = Presentation()
    for _ in range(n):
        prs.slides.add_slide(prs.slide_layouts[6])
    prs.save(str(path))


def test_an_empty_last_reply_after_a_finished_deck_job_gives_a_plain_summary(tmp_path, monkeypatch):
    _deck(tmp_path / "Ridgeway_PdM_Client_Deck.pptx", 7)
    seen = []
    report = ("The slides engine finished; its quality checks passed.\nFiles:\n- Meshpool outputs/job/deck.pptx\n"
              "Saved as: Ridgeway_PdM_Client_Deck.pptx (the file the user asked for; edit this one).")
    monkeypatch.setattr(A.Agent, "_guarded_tool", lambda self, c, g, cut=False: seen.append(c) or report)
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "mcp__meshpool_office__make_deck", "arguments": json.dumps({"request": "a 7-slide deck"})}}]}
    _script(monkeypatch, [call, {"role": "assistant", "content": ""}])
    r = run_task("make the deck", workdir=str(tmp_path), engine="agent")
    assert seen and r["answer"] == "Made Ridgeway_PdM_Client_Deck.pptx, 7 slides.", r
    assert not r.get("error"), r


def test_an_empty_reply_with_no_job_is_still_an_error(tmp_path, monkeypatch):
    _script(monkeypatch, [{"role": "assistant", "content": ""}])
    r = run_task("hello", workdir=str(tmp_path), engine="agent")
    assert not r["answer"]


def test_the_job_file_is_read_from_each_engine_report(tmp_path):
    _deck(tmp_path / "d.pptx", 3)
    (tmp_path / "Meshpool outputs").mkdir()
    (tmp_path / "Meshpool outputs" / "m.docx").write_bytes(b"x")
    assert A._job_file("Added 1 slide to x.pptx and saved it as d.pptx (the original is unchanged).", tmp_path) \
        .endswith("d.pptx")
    assert A._job_file(f"Saved the Word document: {tmp_path}/Meshpool outputs/m.docx.", tmp_path).endswith("m.docx")
    assert A._job_file("Still working on the slides job (2 min so far).", tmp_path) == ""
    assert A._made_summary(str(tmp_path / "d.pptx")) == "Made d.pptx, 3 slides."
