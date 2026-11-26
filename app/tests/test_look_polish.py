"""Rough edges of the look review (agent/look.py), found by a verifier: one final answer, the user's own choices win,
a Checked line that names what was found, one "[meshpool]" header, and no tool that only writes a file counted as a
look. No network and no paid model: the model is a script, the page check is a stand-in or the real headless Chromium."""
import io
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_look_review import (BUGGY, OVER, CONSOLE, ASK, Model, _A, _env, agent, fake_render, needs_browser,  # noqa: F401,E402
                                    overwrite, proj, reviews, text_of, write)
from meshpool_harness.agent import look as L  # noqa: E402
from meshpool_harness.agent.tui import Terminal  # noqa: E402


def _two_rounds(proj, monkeypatch):
    render = fake_render([[OVER]])                                      # the problem never goes away
    monkeypatch.setattr(L, "render_web", render)
    Model(monkeypatch, [write("index.html", BUGGY), "Made it.",
                        overwrite("index.html", BUGGY + "<!-- 1 -->\n", "w2"), "Tried.",
                        overwrite("index.html", BUGGY + "<!-- 2 -->\n", "w3"), "Tried again."])
    a, ev = agent(proj)
    return a, ev, a.send(ASK)


# ---------- 1: one final answer -----------------------------------------------------------------------------------------
def test_each_review_round_marks_the_reply_before_it_as_a_first_answer(proj, monkeypatch):
    a, ev, final = _two_rounds(proj, monkeypatch)
    kinds = [("round" if e["type"] == "review_round" else e["text"]) for e in ev
             if e["type"] == "review_round" or (e["type"] == "assistant" and not e.get("has_tools"))]
    assert kinds[:5] == ["Made it.", "round", "Tried.", "round", "Tried again."], kinds
    assert final.startswith("Tried again.") and "Checked:" in final
    mine = [m["content"] for m in a.messages if m["role"] == "assistant" and isinstance(m["content"], str)]
    assert "Made it." in mine and "Tried." in mine                      # the model's own history is whole


def test_print_mode_writes_the_last_answer_only(proj, monkeypatch):
    a, ev, final = _two_rounds(proj, monkeypatch)
    out, err = io.StringIO(), io.StringIO()
    t = Terminal(print_mode=True, out=out, err=err, interactive=False)
    for e in ev:
        t.emit(dict(e, streamed=False) if e["type"] == "assistant" else e)
    text = out.getvalue()
    assert "Tried again." in text and "Checked:" not in text, text          # stdout: the answer alone
    assert "Made it." not in text and "Tried." not in text.replace("Tried again.", ""), text
    assert [ln for ln in err.getvalue().splitlines() if ln.startswith("Checked:")]   # the Checked line: stderr
    assert len([ln for ln in err.getvalue().splitlines() if ln.startswith("Checked:")]) == 1


def test_print_mode_still_writes_an_answer_that_no_review_follows():
    out = io.StringIO()
    t = Terminal(print_mode=True, out=out, err=io.StringIO(), interactive=False)
    for e in ({"type": "assistant", "text": "Four.", "streamed": False, "has_tools": False},
              {"type": "turn_end", "turn": 1, "session": {}}):
        t.emit(e)
    assert out.getvalue().strip() == "Four."


def test_the_json_result_holds_the_last_answer(proj, monkeypatch):
    from meshpool_harness.agent.tui_session import TermFrontend
    a, ev, final = _two_rounds(proj, monkeypatch)
    fe = TermFrontend.__new__(TermFrontend)
    fe.s = type("S", (), {"agent": a, "lock": __import__("threading").Lock(), "events": ev})()
    assert fe.reply() == final and fe.reply().startswith("Tried again.") and "Checked:" in fe.reply()
    assert fe.answer().startswith("Tried again.") and "Checked:" not in fe.answer()    # -p: the answer alone
    assert fe.checked() == a.last_look["line"] and fe.checked().startswith("Checked:")


# ---------- 2: the user's explicit choices win --------------------------------------------------------------------------
ASK_BUY = "make a page for Petal with a Buy now button that does nothing yet"
USE = ["The 'Buy now' button does nothing when clicked", "The 'Send' button does nothing when clicked"]
STEP = [{"action": "click 'Buy now'", "ok": False, "note": "nothing visible changed (the request mentions buy)"}]


def test_a_control_the_request_calls_a_placeholder_is_not_a_finding():
    f, s = L.apply_choices(USE, STEP, ASK_BUY)
    assert f == [USE[1]] and s == []
    for words in ("is a placeholder", "is inert", "is a dummy", "is not wired up", "does nothing for now"):
        assert L.apply_choices(USE[:1], [], f"a Buy now button that {words}" if "is" not in words[:3] else
                               f"the Buy now button {words}")[0] == [], words
    assert L.apply_choices(USE, STEP, "a page with a Buy now button")[0] == USE     # no such choice: still reported
    assert L.apply_choices(["The unnamed thing does nothing"], [], ASK_BUY)[0] == ["The unnamed thing does nothing"]


def test_the_review_message_says_the_users_choices_win():
    msg = L.message("web", {"label": "index.html", "url": "file:///x/index.html", "files": []},
                    {"findings": [], "steps": [], "shots": []}, ASK_BUY, False)
    assert "explicit choices win over these checks" in msg["content"]


BUY = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
       "content=\"width=device-width\"><title>Petal</title></head><body><main><h1>Petal flowers</h1>"
       "<p>Fresh bouquets every morning.</p><button id=\"buy\">Buy now</button></main></body></html>\n")


@needs_browser
@pytest.mark.parametrize("ask,flagged", [(ASK_BUY, False), ("make a page for Petal with a Buy now button", True)])
def test_real_page_dead_button_follows_the_request(proj, monkeypatch, ask, flagged):
    pytest.importorskip("meshpool_harness.review_interact")
    Model(monkeypatch, [write("index.html", BUY), "Made index.html.", "Noted."])
    a, _ = agent(proj)
    a.send(ask)
    t = text_of(reviews(a)[0])
    assert ("'Buy now'" in t.split("What meshpool measured")[1].split("Check it against")[0]) is flagged, t


# ---------- 3: the Checked line names what was found --------------------------------------------------------------------
CONTRAST = {"rule": "contrast", "sel": 'a.btn "See prices"', "msg": "contrast 2.1:1, needs 4.5:1", "views": ["desktop"]}
FOCUS = "Keyboard focus is invisible on the nav links (6 of 8 elements checked): Home, Menu"


def test_checked_line_names_what_was_fixed_and_what_is_open():
    st = {"rounds": 1, "changed": True, "first": {"findings": [CONTRAST, CONSOLE], "interact": [FOCUS]},
          "last": {"findings": [], "interact": [FOCUS]}}
    line = L.summary_line("web", st)
    assert line == ("Checked: looked at the page at desktop and phone width; fixed low contrast on 'See prices' and a "
                    "console error; still open: keyboard focus is invisible on the nav links."), line
    st["last"]["interact"] = []
    assert L.summary_line("web", st).endswith("fixed low contrast on 'See prices', a console error and keyboard focus "
                                              "is invisible on the nav links.")


def test_checked_line_without_findings_does_not_say_updated_after_the_review():
    st = {"rounds": 1, "changed": True, "first": {"findings": []}, "last": {"findings": []}}
    line = L.summary_line("web", st)
    assert "updated it after the review" not in line and "no measured problems" not in line
    assert "changed it after comparing it with your request" in line


def test_checked_line_in_a_real_turn(proj, monkeypatch):
    render = fake_render([[CONTRAST, CONSOLE], []])
    monkeypatch.setattr(L, "render_web", render)
    Model(monkeypatch, [write("index.html", BUGGY), "Made it.", overwrite("index.html", BUGGY + "<!-- 1 -->\n"), "Fixed."])
    a, _ = agent(proj)
    assert "fixed low contrast on 'See prices' and a console error." in a.send(ASK)


# ---------- 4: one header in round two ----------------------------------------------------------------------------------
def test_round_two_has_one_meshpool_header(proj, monkeypatch):
    a, _, _ = _two_rounds(proj, monkeypatch)
    second = text_of(reviews(a)[1])
    assert second.startswith(L.AGAIN), second[:200]
    assert second.count("[meshpool]") == 1, second[:300]
    assert "Before you call this done, look at what you made" in second


# ---------- 6: only a real look counts ----------------------------------------------------------------------------------
@pytest.mark.parametrize("tool,counts", [("mcp__meshpool-office__make_deck", True), ("mcp__meshpool-office__check_deck", True),
                                         ("make_report", False), ("mcp__x__build_deck", False),
                                         ("mcp__x__build_report", False), ("mcp__x__build_model", False)])
def test_only_tools_that_draw_pages_skip_the_review(proj, tool, counts):
    from tests.test_look_review import _docx
    _docx(proj / "deck.docx")
    a = _A(proj)
    a.trace = [{"tool": "bash", "ok": True}, {"tool": tool, "ok": True}]
    got = L.doc_target(a, {"t0": time.time() - 5, "trace0": 0}, [proj / "deck.docx"])
    assert (got is None) is counts
