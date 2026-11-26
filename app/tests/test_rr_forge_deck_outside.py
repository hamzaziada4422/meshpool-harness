"""Release review item 7: add_slides("~/Desktop/Board.pptx") changed that deck in place, outside the chat's folder,
where Undo cannot put it back. A deck is now changed in place only inside the folder the chat's checkpoints cover;
outside it the slides go into a copy "<name> (updated).pptx" in the chat's folder, and the answer says so."""
from __future__ import annotations

import json
import shutil
import sys

from pptx import Presentation

from test_daniel019_round2 import _add_one, quiet_build, r2_deck  # noqa: F401


def test_deck_target_in_place_only_inside_the_chat_folder(tmp_path):
    from engines.forge.engine import deck_target
    chat, desk = tmp_path / "chat", tmp_path / "Desktop"
    (chat / "sub").mkdir(parents=True)
    desk.mkdir()
    inside, outside = chat / "sub" / "Board.pptx", desk / "Board.pptx"
    inside.write_bytes(b"x")
    outside.write_bytes(b"x")
    assert deck_target(inside, "add a slide", chat) == (inside, "in_place")
    assert deck_target(outside, "add a slide", chat) == (chat / "Board (updated).pptx", "outside")
    (chat / "Board (updated).pptx").write_bytes(b"x")
    assert deck_target(outside, "add a slide", chat) == (chat / "Board (updated 2).pptx", "outside")
    assert deck_target(outside, "add a slide", None) == (desk / "Board (updated).pptx", "outside")   # no Undo known


def test_a_deck_inside_the_chat_folder_is_changed_in_place(tmp_path, quiet_build, r2_deck):  # noqa: F811
    chat = tmp_path / "chat"
    (chat / "decks").mkdir(parents=True)
    (tmp_path / "work").mkdir()
    deck = chat / "decks" / "Board.pptx"
    shutil.copy(r2_deck, deck)
    res = _add_one(deck, tmp_path, root=str(chat))
    assert res["artifacts"] == [str(deck)], res
    assert len(Presentation(str(deck)).slides) == 8
    assert "changed in place" in res["answer"]


def test_a_deck_outside_the_chat_folder_is_copied_into_it(tmp_path, quiet_build, r2_deck, monkeypatch):  # noqa: F811
    chat, desk = tmp_path / "chat", tmp_path / "Desktop"
    chat.mkdir()
    desk.mkdir()
    (tmp_path / "work").mkdir()
    deck = desk / "Board.pptx"
    shutil.copy(r2_deck, deck)
    before = deck.read_bytes()
    monkeypatch.setenv("MH_CHECKPOINT_ROOT", str(chat))               # as the office job sets it
    res = _add_one(deck, tmp_path, root=None)
    copy = chat / "Board (updated).pptx"
    assert res["artifacts"] == [str(copy)], res
    assert deck.read_bytes() == before                                 # the user's deck is untouched
    assert len(Presentation(str(copy)).slides) == 8
    assert not list(desk.glob("*(updated*"))
    assert ("saved them in a copy, Board (updated).pptx in this chat's folder: Board.pptx is outside this chat's "
            "folder, where Undo could not put it back, so it was not changed") in res["answer"], res["answer"]


def test_the_office_job_tells_the_engine_the_chat_folder(tmp_path, monkeypatch):
    from meshpool_harness.agent import office_jobs as OJ
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_CHECKPOINT_ROOT", str(tmp_path / "stale"))   # never another chat's folder
    chat = tmp_path / "chat"
    chat.mkdir()
    jid = OJ.new_job("slides", "[add to deck: x.pptx] add one", [], chat, scope="rr7")
    OJ.JOBS[jid]["cmd"] = [sys.executable, "-c",
                           "import json, os; print(json.dumps({'root': os.environ.get('MH_CHECKPOINT_ROOT')}))"]
    OJ.run(jid, timeout=30)
    assert OJ.JOBS[jid]["result"] == {"root": str(chat.resolve())}, (OJ.JOBS[jid].get("stderr"), json.dumps(
        OJ.JOBS[jid].get("result")))
