"""Daniel (consultant persona), round 3: board decks and memos from a research report, built-in engine path.
No model is called; the plan is scripted (fixtures/daniel019).

1 every slide's title is in a real title placeholder (outline view, accessibility).
2 speaker notes by default (40 to 120 words a slide); only "no notes" turns them off.
5 the deck-writer prompt: no caveats about things the request did not ask about, no "N/A" on a slide.
7 the source line drops the filename once the report's title is known."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
from pptx import Presentation

from test_daniel019_decks import BRIEF, FACTS, PLAN, Scripted, offline  # noqa: F401

NOTES_SENTENCE = "Add short, clean speaker notes on every slide that I can read aloud. "


def _plan_without_notes():
    plan = copy.deepcopy(PLAN)
    for s in plan:
        s.pop("notes", None)
    return plan


def _build(offline, tmp_path, brief, plan, n=4):
    plan = plan[:n]
    res = offline.build(brief + FACTS, str(tmp_path), slides_wanted=len(plan), client=Scripted(plan), task_id="r3")
    return res["pptx"]


# ---------------------------------------------------------------------------------------------------- item 1
def test_every_slide_title_is_in_a_title_placeholder(tmp_path, offline):
    path = _build(offline, tmp_path, BRIEF.replace(NOTES_SENTENCE, ""), PLAN, n=6)
    prs = Presentation(path)
    assert len(prs.slides) >= 4
    for i, sl in enumerate(prs.slides, 1):
        t = sl.shapes.title
        assert t is not None, f"slide {i} has no title placeholder"
        assert t.text_frame.text.strip(), f"slide {i}: empty title"


# ---------------------------------------------------------------------------------------------------- item 2
def _words(sl):
    return len(sl.notes_slide.notes_text_frame.text.split()) if sl.has_notes_slide else 0


def test_notes_are_written_by_default_when_the_request_does_not_mention_them(tmp_path, offline):
    brief = BRIEF.replace(NOTES_SENTENCE, "")
    assert "notes" not in brief.lower()
    path = _build(offline, tmp_path, brief, _plan_without_notes(), n=5)
    for i, sl in enumerate(Presentation(path).slides, 1):
        w = _words(sl)
        assert 25 <= w <= 125, (i, w)
        txt = sl.notes_slide.notes_text_frame.text
        assert not re.search(r"\b[FCWO]\d{1,3}\b|prov|json|\[GAP", txt, re.I), txt


def test_no_notes_turns_them_off(tmp_path, offline):
    brief = BRIEF.replace(NOTES_SENTENCE, "No notes please. ")
    path = _build(offline, tmp_path, brief, PLAN, n=4)           # the plan carries notes; the request says none
    assert all(_words(sl) == 0 for sl in Presentation(path).slides)


def test_notes_wanted_reads_the_request():
    from engines.forge import notes as NT
    for off in ("No notes please", "without speaker notes", "skip the notes", "don't add notes", "no speaker notes"):
        assert not NT.wanted(f"Make a deck. {off}."), off
    for on in ("Make a board deck.", "Add speaker notes.", "Take note of the numbers.", "a notes page"):
        assert NT.wanted(on), on


# ---------------------------------------------------------------------------------------------------- item 6
LONG = ["Unclear ROI and business case", "Legacy PLC integration", "Data quality and availability",
        "Skills shortage in maintenance teams", "Vendor lock-in concerns", "Cybersecurity of connected assets"]


def _chart_deck(tmp_path, extra=None):
    from engines.forge.render import render
    s = copy.deepcopy(PLAN[2])
    ch = {"kind": "column", "categories": LONG, "series": [{"name": "Plants citing the barrier, %",
          "values": [58, 52, 47, 41, 33, 28]}], "unit": "%", "decimals": 0, **(extra or {})}
    s["parts"][0]["chart"] = ch
    path = tmp_path / "c.pptx"
    render([PLAN[0], s], str(path), fix_labels=False)
    return [sh.chart for sl in Presentation(str(path)).slides for sh in sl.shapes if getattr(sh, "has_chart", False)]


def test_long_category_names_are_never_tilted_to_45_degrees(tmp_path):
    """Slides 6 and 8 of Daniel's deck had category labels at 45 degrees. A column chart whose names do not fit under the
    columns is drawn as bars (names lie flat); an annotated one sets its labels horizontal so they wrap."""
    for extra in (None, {"target": 40}):
        for ch in _chart_deck(tmp_path, extra)[:1]:          # the first chart is the one built here
            xml = ch._chartSpace.xml
            cat = re.search(r"<c:catAx>.*?</c:catAx>", xml, re.S).group(0)
            bar = re.search(r'<c:barDir val="(\w+)"', xml).group(1)
            flat = 'rot="0"' in cat
            if extra is None:
                assert bar == "bar", bar
            else:
                assert bar == "col" and flat, (bar, cat[-400:])
            assert 'rot="-2700000"' not in cat


# ---------------------------------------------------------------------------------------------------- item 7
def test_the_source_line_drops_the_filename_in_brackets_when_the_report_title_is_known():
    from engines.forge.render import plain_sources
    slides = [{"source": {"text": "Source: Northfield Research, Industry Benchmark Report 2025 "
                                  "(Northfield_PdM_Benchmark_2025.pdf)."}},
              {"source": {"text": "Northfield Research, Industry Benchmark Report 2025 (survey of 412 plant managers; "
                                  "Northfield_PdM_Benchmark_2025.pdf); Eurostat 2024"}},
              {"source": {"text": "Northfield_PdM_Benchmark_2025.pdf"}},
              {"source": {"text": "Q3 notes (draft.docx)"}}]
    plain_sources(slides, "")
    t = [s["source"]["text"] for s in slides]
    assert t[0] == "Source: Northfield Research, Industry Benchmark Report 2025.", t[0]
    assert t[1] == "Northfield Research, Industry Benchmark Report 2025 (survey of 412 plant managers); Eurostat 2024", t[1]
    assert "pdf" not in t[2] and t[2].startswith("Northfield Research"), t[2]    # only a file name: the deck's source
    assert t[3] == "Q3 notes (draft.docx)", t[3]                                 # no report title: the name stays


def test_a_built_deck_has_no_filename_in_a_source_footer(tmp_path, offline):
    plan = copy.deepcopy(PLAN[:4])
    for s in plan:
        if isinstance(s.get("source"), dict):
            s["source"]["text"] = ("Northfield Research, Industry Benchmark Report 2025 "
                                   "(Northfield_PdM_Benchmark_2025.pdf)")
    path = _build(offline, tmp_path, BRIEF, plan, n=4)
    texts = [sh.text_frame.text for sl in Presentation(path).slides for sh in sl.shapes if sh.has_text_frame]
    assert any("Northfield Research, Industry Benchmark Report 2025" in t for t in texts), texts[:5]
    assert not any(".pdf" in t for t in texts), [t for t in texts if ".pdf" in t]


# ---------------------------------------------------------------------------------------------------- item 5
def test_the_deck_writer_prompt_bans_caveats_nobody_asked_for_and_na_on_a_slide(tmp_path, offline):
    """Replies hedged about things never asked ("The report does not say how many plants a typical board member
    oversees", "Those figures are N/A here"). The slide writer is told: no such caveats, no N/A on a slide; missing data
    is left out or said once in plain words."""
    seen = []

    class Recording(Scripted):
        def chat(self, *, model, messages, **kw):
            seen.append(messages[0]["content"])
            return super().chat(model=model, messages=messages, **kw)
    plan = PLAN[:3]
    offline.build(BRIEF + FACTS, str(tmp_path), slides_wanted=len(plan), client=Recording(plan), task_id="r3p")
    writers = [t for t in seen if "ONE consulting slide" in t]
    assert writers, "no slide writer prompt was sent"
    for t in writers:
        assert "Write no caveats about anything the request did not ask about" in t, t[-1500:]
        assert 'Never write "N/A"' in t
        assert "said once in plain words" in t
        assert "shown as N/A" not in t and "shows as N/A" not in t and "a gap in\n  a table is N/A" not in t


def test_a_gap_is_not_printed_as_na_or_not_stated_on_a_slide(tmp_path, offline):
    """The checks still hold: a table with a gap prints no N/A or "not stated" line."""
    plan = copy.deepcopy(PLAN[:4])
    path = _build(offline, tmp_path, BRIEF, plan, n=4)
    for i, sl in enumerate(Presentation(path).slides, 1):
        txt = " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)
        assert not re.search(r"(?i)\bN/A\b|not stated|does not say", txt), (i, txt[:300])


# ---------------------------------------------------------------------------------------------------- item 3
def _scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    from meshpool_harness import project_store as PS
    return Path(PS.new_scratch())


ASK = ("Make a 6 to 8 slide board deck from this report. Include a cover, an executive summary, charts built from the "
       "report's numbers, and next steps. Save it as Board_Deck.pptx.")


def test_an_attached_pdf_reaches_the_model_as_text_and_figure_pages_in_a_chat_with_no_folder(tmp_path, monkeypatch):
    """Daniel attached the benchmark PDF to a fresh chat: the model saw three chart pages and no text, and asked him to
    paste the report. Now the text goes with the message, with the figure pages as images."""
    monkeypatch.setenv("MH_DOC_CACHE", "0")
    from test_daniel018_r2 import _images, _report, _script, _text
    from meshpool_harness import run_task
    wd = _scratch(monkeypatch, tmp_path)
    _report(wd / "report.pdf")
    seen = []
    _script(monkeypatch, [{"role": "assistant", "content": "ok"}], seen)
    run_task(ASK + "\n\nAttached files (in this chat's folder): report.pdf", workdir=str(wd), engine="agent")
    last = seen[0][-1:]
    t = _text(last)
    assert "Adoption reached 47% in 2025, up from 18% in 2021" in t, t[-1500:]       # page 1's text
    assert "Unclear ROI is the most cited barrier" in t                                # page 2's text
    assert _images(last) == 1 and "report.pdf, page 2" in t                            # the figure page as an image


def test_a_file_asked_for_in_a_chat_with_no_folder_is_saved_in_the_chats_own_folder(tmp_path, monkeypatch):
    from meshpool_harness import nofolder as NF
    wd = _scratch(monkeypatch, tmp_path)
    n = NF.note(wd, ASK)
    assert str(wd) in n and "save the file there" in n and "Add a folder" in n
    assert NF.note(wd, "What is the capital of France?") == ""
    assert NF.note(tmp_path, ASK) == ""                                    # a folder the user chose: no note


def test_the_note_reaches_the_model_in_a_chat_with_no_folder(tmp_path, monkeypatch):
    from test_daniel018_r2 import _script, _text
    from meshpool_harness import run_task
    wd = _scratch(monkeypatch, tmp_path)
    seen = []
    _script(monkeypatch, [{"role": "assistant", "content": "ok"}], seen)
    run_task(ASK, workdir=str(wd), engine="agent")
    assert "save the file there" in _text(seen[0][-1:])


def test_a_reply_that_sends_the_user_elsewhere_becomes_a_plain_offer():
    from meshpool_harness import nofolder as NF
    bad = ("The deck would use navy and amber.\n\nTo get the file, run this in a session that has a shell and file "
           "writing. I can also write the slide text here.")
    assert NF.wants_folder(bad)
    out = NF.plain(bad)
    assert "session that has" not in out and "Add a folder" in out and "navy and amber" in out, out
    assert not NF.wants_folder("Here is your summary.")


def test_the_session_adds_an_add_a_folder_action_under_such_a_reply(tmp_path, monkeypatch):
    import threading
    from meshpool_harness.ui import agent_api as A
    wd = _scratch(monkeypatch, tmp_path)
    s = A.Session.__new__(A.Session)
    s.workdir = str(wd)
    got = []
    s.listeners = [got.append]
    s.lock = threading.RLock()
    s.changed = threading.Condition(s.lock)
    s.events, s.state, s.queue = [], "idle", []
    for k, v in dict(_open_deltas=False, _open_thoughts=False, _retry_open=False, _interrupting=None, _turn_id=0,
                     _turn_open=False, pendings={}, harness={"type": "x", "id": "x"}).items():
        setattr(s, k, v)
    s._persist = s._persist_soon = lambda *a, **k: None
    s._question_state = lambda ev: None
    s.emit({"type": "assistant", "text": "To get the file, run this in a session that has a shell."})
    kinds = [e["type"] for e in got]
    assert kinds == ["assistant", "offer"], kinds
    assert "session that has" not in got[0]["text"] and got[1]["action"] == "add_folder"


# ---------------------------------------------------------------------------------------------------- item 4
from test_wf_ui018 import _fake_chat, app, page  # noqa: E402,F401  (the real app and a browser page)


def test_after_undo_the_message_is_in_the_focused_box_with_the_caret_at_the_end_and_enter_sends_it(page):
    """Daniel: Undo put his message back as a draft, but Enter did nothing because the box was not focused."""
    _fake_chat(page)
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); S.attachments = []; "
                  "window.__sent = 0; window.sendNow = () => { window.__sent++; }; }")
    page.evaluate("() => fillInput('Change the title on slide 1 to: New title', {back: true})")
    st = page.evaluate("() => { const i = document.querySelector('#input'); return {id: document.activeElement && "
                       "document.activeElement.id, a: i.selectionStart, b: i.selectionEnd, n: i.value.length}; }")
    assert st["id"] == "input", st
    assert st["a"] == st["b"] == st["n"] > 0, st
    page.keyboard.press("Enter")
    assert page.evaluate("window.__sent") == 1
    assert page.errors == [], page.errors
