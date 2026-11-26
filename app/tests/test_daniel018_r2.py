"""Daniel (consultant persona), 0.0.18 release candidate, round 2: the agent-behaviour problems that kept him at 6 of
10. Charts the agent could not see, turns that stopped on a promise, a card for a deleted file, a chat title that
stayed the first sentence, a false "could not parse" and a turn that ended with no summary. Scripted model, no model
calls."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from pptx import Presentation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from meshpool_harness import run_task  # noqa: E402
from meshpool_harness.agent import core as A  # noqa: E402
from test_wf_ui018 import _fake_chat, app, page  # noqa: E402,F401  (the real app and a browser page)


def _script(monkeypatch, msgs, seen=None):
    it = iter(msgs)

    def fake_chat(self, turn):
        turn["calls"] += 1
        if seen is not None:
            seen.append(json.loads(json.dumps(self.messages)))
        return next(it), False
    monkeypatch.setattr(A.Agent, "_chat", fake_chat)


def _chart_png() -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (600, 300), "white")
    d = ImageDraw.Draw(im)
    for i, h in enumerate((220, 180, 150)):
        d.rectangle([60 + i * 170, 280 - h, 160 + i * 170, 280], fill=(20, 48, 79))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _report(path):
    """A two-page PDF: page 1 is text, page 2 has a short text and a bar chart drawn as a picture."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(72, 760, "Executive summary. This report surveys 412 plant managers in four countries.")
    c.drawString(72, 740, "Adoption reached 47% in 2025, up from 18% in 2021.")
    c.showPage()
    c.drawString(72, 760, "2. Barriers to adoption")
    c.drawString(72, 740, "Unclear ROI is the most cited barrier. The chart below shows the top three barriers.")
    c.drawImage(ImageReader(io.BytesIO(_chart_png())), 72, 420, width=420, height=210)
    c.showPage()
    c.save()


def _images(msgs) -> int:
    return sum(1 for m in msgs if isinstance(m.get("content"), list)
               for p in m["content"] if isinstance(p, dict) and p.get("type") == "image_url")


def _text(msgs) -> str:
    out = []
    for m in msgs:
        c = m.get("content")
        out.append(c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict)))
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------- item 1: charts
def test_the_reader_says_where_the_figures_are(tmp_path, monkeypatch):
    """read_document's text names the page with the chart, so no model can say there is no chart."""
    monkeypatch.setenv("MH_DOC_CACHE", "0")
    from meshpool_harness import docread
    _report(tmp_path / "report.pdf")
    text = docread.extract(tmp_path / "report.pdf")
    assert "[Figures]" in text and "page 2 (2. Barriers to adoption)" in text, text
    assert "cannot read as text" in text and "page 1" not in text.split("[Figures]")[1], text


def test_a_chart_question_sends_the_page_image_with_the_next_request(tmp_path, monkeypatch):
    """Daniel asked about "the barriers chart" and the agent said "The report has no barriers chart": only the text
    reached the model. A question that names a chart now carries the page with the chart."""
    _report(tmp_path / "report.pdf")
    seen = []
    _script(monkeypatch, [{"role": "assistant", "content": "The chart on page 2 shows three barriers."}], seen)
    run_task("Looking at the barriers chart, what are the top three barriers?\n\nAttached files (in the project "
             "folder): report.pdf", workdir=str(tmp_path), engine="agent")
    first = seen[0]
    assert _images(first[-1:]) == 1, "the question's request carries the page image"
    assert "report.pdf, page 2" in _text(first[-1:]), _text(first[-1:])[-800:]


def test_a_text_only_model_gets_the_figure_note_instead(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_VISION", "0")
    _report(tmp_path / "report.pdf")
    seen = []
    _script(monkeypatch, [{"role": "assistant", "content": "ok"}], seen)
    run_task("What does the chart show?\n\nAttached files (in the project folder): report.pdf",
             workdir=str(tmp_path), engine="agent")
    last = seen[0][-1:]
    assert _images(last) == 0
    t = _text(last)
    assert "page 2" in t and "cannot read as text" in t and "Never say that the file has no chart" in t, t[-800:]


def test_reading_a_report_with_a_chart_shows_the_model_its_page(tmp_path, monkeypatch):
    """The model read the PDF with read_document: the page with the chart follows as an image."""
    monkeypatch.setenv("MH_DOC_CACHE", "0")
    from meshpool_harness import docread
    _report(tmp_path / "report.pdf")
    out = docread.extract(tmp_path / "report.pdf")
    monkeypatch.setattr(A.Agent, "_guarded_tool", lambda self, c, g, cut=False: out)
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "mcp__meshpool-office__read_document", "arguments": json.dumps({"path": "report.pdf"})}}]}
    seen = []
    _script(monkeypatch, [call, {"role": "assistant", "content": "47% adopted."}], seen)
    run_task("Summarise report.pdf in two lines", workdir=str(tmp_path), engine="agent")
    assert _images(seen[1]) == 1 and "report.pdf, page 2:" in _text(seen[1]), _text(seen[1])[-600:]


# ------------------------------------------------------------------------------------ item 2 and 6: promises
def test_a_turn_that_stops_on_a_promise_is_nudged_once(tmp_path, monkeypatch):
    """"I'll check the folder and read the current deck..." ended the turn with no call. It is sent back once."""
    seen = []
    (tmp_path / "notes.txt").write_text("hello")
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "read_file", "arguments": json.dumps({"path": "notes.txt"})}}]}
    _script(monkeypatch, [{"role": "assistant", "content": "I'll check the folder and read the current deck to find "
                                                          "the slide 2 title."},
                          call, {"role": "assistant", "content": "The notes say hello."}], seen)
    r = run_task("What do my notes say?", workdir=str(tmp_path), engine="agent")
    assert r["answer"].strip().endswith("The notes say hello."), r
    nudges = [m for m in seen[-1] if m.get("role") == "user" and A.ACT_NUDGE in str(m.get("content"))]
    assert len(nudges) == 1, _text(seen[-1])[-600:]


def test_the_nudge_is_given_once_per_turn(tmp_path, monkeypatch):
    seen = []
    promise = {"role": "assistant", "content": "Next I will run the tests."}
    _script(monkeypatch, [promise, dict(promise), dict(promise)], seen)
    run_task("Fix the bug", workdir=str(tmp_path), engine="agent")
    assert sum(A.ACT_NUDGE in _text([m]) for m in seen[-1]) == 1
    assert not A._announces_action("If you want, I'll add a chart slide.")
    assert not A._announces_action("Shall I go ahead and run it?")
    assert A._announces_action("The build finished, so I'll check which files are in the folder.")


def test_a_promise_after_the_nudge_ends_with_what_the_job_made(tmp_path, monkeypatch):
    """The deck turn ended on "The build finished, so I'll check which files are in the folder...": no summary."""
    prs = Presentation()
    for _ in range(7):
        prs.slides.add_slide(prs.slide_layouts[6])
    prs.save(str(tmp_path / "Ridgeway_PdM_Client_Deck.pptx"))
    report = "The slides engine finished.\nSaved as: Ridgeway_PdM_Client_Deck.pptx (the file the user asked for)."
    monkeypatch.setattr(A.Agent, "_guarded_tool", lambda self, c, g, cut=False: report)
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "mcp__meshpool_office__make_deck", "arguments": json.dumps({"request": "a 7-slide deck"})}}]}
    promise = "The build finished, so I'll check which files are in the folder and run the deck checker."
    _script(monkeypatch, [call, {"role": "assistant", "content": promise}, {"role": "assistant", "content": promise}])
    r = run_task("make the deck", workdir=str(tmp_path), engine="agent")
    assert r["answer"].strip().endswith("Made Ridgeway_PdM_Client_Deck.pptx, 7 slides."), r


# ------------------------------------------------------------------------------------------- item 3: stale cards
def test_a_file_made_and_deleted_in_the_turn_has_no_card(tmp_path):
    from meshpool_harness.ui import workbench as W
    (tmp_path / "Ridgeway_PdM_Memo.docx").write_bytes(b"memo")
    turns = [{"n": 7, "changed": [{"path": "Ridgeway_PdM_Memo.docx", "status": "added"},
                                  {"path": "memo_check.pdf", "status": "added"}]}]
    assert [c["path"] for c in W.own_changes(turns, tmp_path)[0]["changed"]] == ["Ridgeway_PdM_Memo.docx"]
    evs = [{"type": "tool_end", "ok": True, "output": f"Saved: {tmp_path}/memo_check.pdf"},
           {"type": "tool_end", "ok": True, "output": f"Saved the Word document: {tmp_path}/Ridgeway_PdM_Memo.docx. "
                                                      "Clarity check: done."}]
    assert W.gone_files(evs, tmp_path) == ["memo_check.pdf"]
    assert [o["path"] for o in W.outputs_from(evs, turns, tmp_path)] == ["Ridgeway_PdM_Memo.docx"]


def test_the_page_drops_the_card_of_a_deleted_file(page):
    _fake_chat(page, "t-gone-card", view={"workdir": page.app["work"]})
    got = page.evaluate("""() => { const cur = S.cur;
        applyEv({type: "user", text: "Write the memo", i: 0});
        cur.tpaths = ["memo_check.pdf", "Ridgeway_PdM_Memo.docx"];
        cur.view.turns = [{n: 7, changed: [{path: "Ridgeway_PdM_Memo.docx", status: "added"}]}];
        for (const e of [{type: "assistant", text: "Done.", i: 1}, {type: "turn_end", i: 2},
                         {type: "checkpoint", turns: [cur.view.turns[0]], gone: ["memo_check.pdf"], i: 3}]) applyEv(e);
        return {names: [...document.querySelectorAll('#col .pvcard .pvcm b')].map(b => b.textContent),
                outs: outputFiles().map(o => o.path)};
    }""")
    assert got["names"] == ["Ridgeway_PdM_Memo.docx"], got
    assert "memo_check.pdf" not in got["outs"], got
    assert page.errors == [], page.errors


# -------------------------------------------------------------------------------------------- item 4: chat title
def test_a_project_chat_is_retitled_from_its_checkpoint_after_a_deck_and_a_memo(tmp_path, monkeypatch):
    """The chat kept "This is the Northfield bench...": the deck job said "Saved as: X" and "Files: - X", which the
    event scan did not read, so no deliverable was found. The checkpoint's added files name it."""
    from meshpool_harness.ui import agent_api as API
    monkeypatch.setattr(API, "SESSIONS", tmp_path)
    sid = "da018r2a5b6c"
    (tmp_path / sid).mkdir()
    root = tmp_path / "ClientWork"
    root.mkdir()
    (root / "Northfield_PdM_Benchmark_2025.pdf").write_bytes(b"%PDF")
    (root / "Ridgeway_PdM_Client_Deck.pptx").write_bytes(b"deck")
    turns = [{"n": 1, "changed": [{"path": "Northfield_PdM_Benchmark_2025.pdf", "status": "added"}]},
             {"n": 4, "changed": [{"path": "Ridgeway_PdM_Client_Deck.pptx", "status": "added"}]}]
    evs = [{"type": "user", "text": "This is the Northfield bench...\n\nAttached files (in the project folder): "
                                    "Northfield_PdM_Benchmark_2025.pdf"},
           {"type": "tool_end", "ok": True, "output": "Files:\n- deck.pptx\nSaved as: Ridgeway_PdM_Client_Deck.pptx "
                                                      "(the file the user asked for)."}]
    fake = SimpleNamespace(id=sid, incognito=False, workdir=str(root), events=evs, lock=__import__("threading").Lock(),
                           cp=SimpleNamespace(root=str(root), turns=lambda: turns))
    assert API.Session._retitle(fake, turns) == "Ridgeway PdM Client Deck"
    import os
    import time
    (root / "Ridgeway_PdM_Memo.docx").write_bytes(b"memo")
    os.utime(root / "Ridgeway_PdM_Memo.docx", (time.time() + 5, time.time() + 5))
    turns.append({"n": 7, "changed": [{"path": "Ridgeway_PdM_Memo.docx", "status": "added"},
                                      {"path": "memo_check.pdf", "status": "added"}]})
    assert API.Session._retitle(fake, turns) == "Ridgeway PdM: deck and memo"
    assert API.read_meta(sid)["title"] == "Ridgeway PdM: deck and memo"
    API.thread_action(sid, {"action": "rename", "title": "Northfield work"})
    assert API.Session._retitle(fake, turns) == "" and API.read_meta(sid)["title"] == "Northfield work"


# ------------------------------------------------------------------------------- item 5: an unusual pptx shape
def _odd_deck(path):
    """Seven slides; the last one's title is a plain shape with no geometry and no placeholder, which python-pptx
    cannot classify (Daniel's final deck, slide 7)."""
    from lxml import etree
    prs = Presentation()
    for i in range(7):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        s.shapes.add_textbox(0, 0, 100, 100).text_frame.text = f"Body of slide {i + 1}"
    sp = etree.fromstring(
        '<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:nvSpPr><p:cNvPr id="90" name="Title 1"/>'
        '<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="900000" cy="300000"/>'
        '</a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Cumulative net benefit reaches EUR 3.1m'
        '</a:t></a:r></a:p></p:txBody></p:sp>')
    prs.slides[6].shapes._spTree.append(sp)
    prs.save(str(path))


def test_a_deck_with_an_unclassified_shape_reads_all_its_slides(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_DOC_CACHE", "0")
    from meshpool_harness import docread
    _odd_deck(tmp_path / "deck.pptx")
    t = docread.extract(tmp_path / "deck.pptx")
    assert not t.startswith("Could not read"), t
    assert "7 slides" in t and t.count("## Slide") == 7 and "Body of slide 7" in t, t
    assert "## Slide 7: Cumulative net benefit reaches EUR 3.1m" in t, t


def test_the_deck_checker_measures_a_deck_with_an_unclassified_shape(tmp_path):
    from engines.forge.tools import check_deck
    _odd_deck(tmp_path / "deck.pptx")
    out, failed = check_deck(str(tmp_path / "deck.pptx"), render=False)
    assert "cannot read the deck" not in out and "unrecognized shape type" not in out, out
    assert not failed


def test_a_reader_failure_on_a_file_that_opens_is_not_called_a_broken_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_DOC_CACHE", "0")
    from meshpool_harness import docread
    _odd_deck(tmp_path / "deck.pptx")

    def boom(p, **kw):
        raise NotImplementedError("Shape instance of unrecognized shape type")
    monkeypatch.setattr(docread, "_pptx", boom)
    t = docread.extract(tmp_path / "deck.pptx")
    assert "not a damaged file" in t and "Do not tell the user the file is broken" in t, t


def _post_agent(tmp_path, replies, **kw):
    a = A.Agent(str(tmp_path), mode="auto", **kw)
    seq, seen = list(replies), []

    def fake(base, key, body, stream):
        seen.append(body)
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake
    return a, seen


def test_the_request_body_itself_carries_the_chart_page(tmp_path):
    """What goes over the wire: the request for the chart question has the page picture in it."""
    _report(tmp_path / "report.pdf")
    a, seen = _post_agent(tmp_path, [{"content": "Three barriers."}])
    a.send("What does the barriers chart in report.pdf show?")
    assert _images(seen[0]["messages"]) == 1, [m.get("role") for m in seen[0]["messages"]]


def test_no_nudge_in_plan_mode(tmp_path):
    a, seen = _post_agent(tmp_path, [{"content": "I'll check the folder first."}] * 3)
    a.plan = True
    a.send("Plan the deck changes")
    assert len(seen) == 1 and not any(A.ACT_NUDGE in _text([m]) for m in a.messages)
