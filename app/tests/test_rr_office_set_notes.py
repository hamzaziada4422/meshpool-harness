"""Release review item 8: edit_office_file set_notes ran the user's own notes through the engine's cleaner. "Thank the
engine team." became "", the slide's notes were wiped, and the tool still said "notes set"."""
from __future__ import annotations

from pptx import Presentation

from meshpool_harness.agent import office_mcp_server as O


def _deck(tmp_path, notes="Old notes the user had."):
    prs = Presentation()
    sl = prs.slides.add_slide(prs.slide_layouts[5])
    sl.shapes.title.text = "Results"
    sl.notes_slide.notes_text_frame.text = notes
    f = tmp_path / "d.pptx"
    prs.save(str(f))
    return f


def _notes(f):
    return Presentation(str(f)).slides[0].notes_slide.notes_text_frame.text


def test_the_users_own_notes_are_kept_as_written(tmp_path):
    f = _deck(tmp_path)
    for text in ("Thank the engine team.", "The storyboard was corrected here; see the brief and the JSON schema",
                 "Before circulating, check the wording of slide 2 (F3)."):
        msg, err = O.edit_office_file(str(f), [{"op": "set_notes", "slide": 1, "text": text}], in_place=True)
        assert not err and "notes set on slide 1" in msg, msg
        assert _notes(f) == text


def test_only_qa_lines_come_out(tmp_path):
    f = _deck(tmp_path)
    msg, err = O.edit_office_file(str(f), [{"op": "set_notes", "slide": 1,
                                             "text": "[QA] title shortened\nThank the engine team.\n  [QA: fit] x"}],
                                  in_place=True)
    assert not err and _notes(f) == "Thank the engine team.", (msg, _notes(f))


def test_no_success_is_reported_when_nothing_was_set(tmp_path):
    f = _deck(tmp_path)
    before = f.read_bytes()
    for text in ("", "   ", "[QA] only a check line"):
        msg, err = O.edit_office_file(str(f), [{"op": "set_notes", "slide": 1, "text": text}], in_place=True)
        assert err, msg
        assert "notes set" not in msg and "Changed" not in msg and "notes not set on slide 1" in msg, msg
        assert f.read_bytes() == before and _notes(f) == "Old notes the user had."
    msg, err = O.edit_office_file(str(f), [{"op": "set_notes", "slide": 1, "text": ""}], in_place=False)
    assert err and not (tmp_path / "d (edited).pptx").exists(), msg      # no copy that changes nothing


def test_one_good_op_and_one_empty_note(tmp_path):
    f = _deck(tmp_path)
    msg, err = O.edit_office_file(str(f), [{"op": "replace_text", "find": "Results", "replace": "Q4 results"},
                                           {"op": "set_notes", "slide": 1, "text": ""}], in_place=True)
    assert not err and "Not done: notes not set on slide 1" in msg, msg
    assert _notes(f) == "Old notes the user had."
    assert Presentation(str(f)).slides[0].shapes.title.text == "Q4 results"
