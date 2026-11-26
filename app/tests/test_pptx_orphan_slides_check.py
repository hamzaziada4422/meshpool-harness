"""Pre-answer check: a deck written this turn that still carries slides its slide list no longer shows. Live check
for 0.0.18 (testkit/bench3/public/decks.py deck_reorder): asked to delete a "DRAFT - do not present" slide, the agent
removed it from presentation.xml's slide list only, so its text stayed in the file the user sends on."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

pptx = pytest.importorskip("pptx")

from meshpool_harness.agent import core as K  # noqa: E402
from testkit.bench3.public import decks as D  # noqa: E402


def _deck(tmp_path):
    D.build_deck(tmp_path / "deck.pptx")
    return pptx.Presentation(tmp_path / "deck.pptx")


def test_a_slide_removed_from_the_list_only_is_named(tmp_path):
    t0 = time.time() - 1
    prs = _deck(tmp_path)
    lst = prs.slides._sldIdLst
    lst.remove(list(lst)[5])                       # what the failing runs did
    prs.save(tmp_path / "deck.pptx")
    note = K._pptx_orphan_slides_miss(tmp_path, t0)
    assert "deck.pptx still contains 1 slide" in note and "DRAFT - do not present" in note and "drop_rel" in note


def test_a_properly_deleted_slide_and_an_untouched_deck_say_nothing(tmp_path):
    t0 = time.time() - 1
    prs = _deck(tmp_path)
    assert K._pptx_orphan_slides_miss(tmp_path, t0) == ""
    lst = prs.slides._sldIdLst
    sid = list(lst)[5]
    prs.part.drop_rel(sid.rId)
    lst.remove(sid)
    prs.save(tmp_path / "deck.pptx")
    assert K._pptx_orphan_slides_miss(tmp_path, t0) == ""
    assert K._pptx_orphan_slides(tmp_path / "missing.pptx") == []          # unreadable: no note, no error


def test_a_deck_not_written_this_turn_is_left_alone(tmp_path):
    prs = _deck(tmp_path)
    lst = prs.slides._sldIdLst
    lst.remove(list(lst)[5])
    prs.save(tmp_path / "deck.pptx")
    assert K._pptx_orphan_slides_miss(tmp_path, time.time() + 5) == ""
