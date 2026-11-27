"""A deck with no source file (first-hour run, 0.0.18): "make a 5-slide deck about home batteries" came back with
market figures from a "Market Research Report 2034" the user never gave, and the reply said "I made none up".

With no file to take figures from, a figure that neither the request nor a cited web source nor a verified calc gives
is labelled illustrative on its slide, a source line naming a document nobody gave is not kept, the engine's answer
names those slides, and a reply that still says none were made up is corrected. Driven with a scripted plan (no model
is called) through the real slides engine; the built file is read with python-pptx."""
from __future__ import annotations

import copy
import json
import re

import pytest
from pptx import Presentation

from engines.forge import grounded as GR

BRIEF = "Make a 5-slide deck about the home battery market for our sales team. Use defaults."
FAKE_SOURCE = "Global Home Battery Market Research Report 2034"

PLAN = [
    {"id": "S0", "node": "0", "archetype": "cover", "title": "Home batteries are becoming a mainstream purchase",
     "subtitle": "A briefing for the sales team", "date": "November 2026", "notes": "Open with the question."},
    {"id": "S1", "node": "1", "archetype": "executive_summary",
     "title": "The market grows fast and buyers want backup power",
     "points": [{"lead": "The market reaches $17.8B by 2034", "bullets": [{"text": "A 14.6% CAGR from 2024"}]},
                {"lead": "Backup is the first reason to buy", "bullets": [{"text": "62% of buyers name outages"}]},
                {"lead": "Installers are the channel", "bullets": [{"text": "Most homes buy through a local installer"}]}],
     "source": {"text": FAKE_SOURCE}, "notes": "Three points for the team."},
    {"id": "S2", "node": "2", "archetype": "grid", "layout": "thirds", "look": "minimal",
     "title": "Sales of home batteries more than triple by 2034",
     "parts": [{"type": "chart", "heading": "Market size, $ billions",
                "chart": {"kind": "column", "categories": ["2024", "2027", "2030", "2034"],
                          "series": [{"name": "Market size, $B", "values": [4.6, 7.1, 11.2, 17.8]}], "unit": "$B",
                          "decimals": 1, "highlight_categories": ["2034"]}}],
     "takeaway": {"text": "The market grows each year, so **now is the time to build the channel**."},
     "source": {"text": FAKE_SOURCE}, "footnotes": [], "notes": "The market more than triples."},
    {"id": "S3", "node": "3", "archetype": "grid", "title": "Buyers want backup power first and savings second",
     "parts": [{"type": "stats", "heading": "Why households buy",
                "items": [{"value": "62%", "label": "name outages"}, {"value": "28%", "label": "name lower bills"},
                          {"value": "10%", "label": "name solar self-use"}]}],
     "source": {"text": FAKE_SOURCE}, "notes": "Lead with resilience."},
    {"id": "S4", "node": "4", "archetype": "closing", "title": "Train the team on backup-first selling",
     "decision": "Decision today: run a backup-first pitch with our installers.",
     "steps": [{"action": "Write the backup-first pitch", "detail": "One page for installers.", "owner": "Owner to confirm",
                "timing": ""},
               {"action": "Pilot it with two installers", "detail": "Track the questions buyers ask.",
                "owner": "Owner to confirm", "timing": ""}],
     "notes": "Close on the next step."},
]


class Scripted:
    """The model's side of the build: the storyline and each slide come from PLAN."""

    def __init__(self, plan):
        self.plan = {s["node"]: s for s in plan}

    def chat(self, *, model, messages, **kw):
        sys_, user = messages[0]["content"], messages[-1]["content"]
        if "engagement manager" in sys_:
            body = {"governing_thought": "Sell backup first", "structure": "SCR",
                    "slides": [{"node": s["node"], "title": s["title"], "archetype": s["archetype"], "evidence": []}
                               for s in self.plan.values()]}
        elif "ONE consulting slide" in sys_:
            x = json.loads(user.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
            body = copy.deepcopy(self.plan.get(x.get("node")) or x)
        else:
            body = {"edits": []}
        return {"choices": [{"message": {"content": json.dumps(body)}}]}


@pytest.fixture(scope="module")
def deck(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    from engines.forge import build as B
    from engines.forge import common as CM
    mp.setattr(B, "_review_on", lambda client, rules: False)
    for k, v in (("MH_PERSONAS", "0"), ("MH_DOSSIER", "0"), ("MH_ASK_AUTO", "1"), ("MH_RESEARCH", "0"),
                 ("MH_TODAY", "2026-11-27")):
        mp.setenv(k, v)
    mp.setattr(CM, "find_soffice", lambda *a, **k: None)
    rules = copy.deepcopy(CM.load_rules())
    rules["repair"]["max_rounds"] = 1
    mp.setattr(B, "load_rules", lambda *a, **k: rules)
    from engines.forge.engine import solve
    work = tmp_path_factory.mktemp("unsourced")
    res = solve(BRIEF, workdir=str(work), task_id="unsourced", client=Scripted(PLAN), files=[])
    mp.undo()
    pptx = res["artifacts"][0]
    slides = [" \n".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame) for sl in Presentation(pptx).slides]
    return {"res": res, "slides": slides, "pptx": pptx}


def test_figures_nobody_gave_are_labelled_illustrative_and_the_made_up_report_is_gone(deck):
    slides = deck["slides"]
    assert len(slides) == 5
    assert not any(FAKE_SOURCE in t or "2034" in t and "Report" in t for t in slides), slides
    for n in (2, 3, 4):                                      # the slides that carry the invented figures
        assert re.search(r"(?i)illustrative figures", slides[n - 1]), (n, slides[n - 1][-400:])
    assert not re.search(r"(?i)illustrative", slides[0]) and not re.search(r"(?i)illustrative", slides[4])


def test_the_engines_answer_names_the_slides_and_forbids_the_claim(deck):
    a = deck["res"]["answer"]
    assert "slides 2, 3, 4 are illustrative" in a and "never say the figures are real or that none were made up" in a
    assert "Every figure on the slides is from" not in a


def test_label_unsourced_keeps_what_the_request_or_a_cited_page_gives():
    """Nothing the request or a cited web source states is labelled: only what neither gives."""
    slides = [{"archetype": "grid", "title": "Prices fell 12% in 2025",
               "parts": [{"type": "stats", "items": [{"value": "12%", "label": "price fall"}]}],
               "source": {"text": "BloombergNEF battery price survey 2025"}},
              {"archetype": "grid", "title": "Installs reach 4.6 million homes",
               "source": {"text": "Global Home Battery Market Research Report 2034"}}]
    corpus = ("Make a deck. Prices fell 12% in 2025.\n"
              "F1: BloombergNEF battery price survey 2025 (https://about.bnef.com/x): pack prices fell 12% in 2025")
    assert GR.label_unsourced(slides, corpus, "Make a deck. Prices fell 12% in 2025.") == [2]
    assert slides[0]["source"]["text"].startswith("BloombergNEF") and not slides[0].get("footnotes")
    assert slides[1]["footnotes"] == [GR.ILLUSTRATIVE] and slides[1]["source"]["text"] == "Illustrative figures"


def test_a_deck_from_the_users_file_is_not_labelled():
    """A file (or a Facts section) was given: invented figures there still fail P29 and are rewritten; nothing is
    labelled away."""
    from engines.forge import build as B
    src = open(B.__file__, encoding="utf-8").read()
    assert "if not GR.has_sources(brief):" in src.split("def tidy():", 1)[1].split("def draw():", 1)[0]
    assert GR.has_sources("deck\n--- report.pdf ---\ntext") and GR.has_sources("deck\n\nFacts\n- F1: 47%")
    assert not GR.has_sources("Make a 5-slide deck about X.")


# ------------------------------------------------------------------ the reply
def _agent(tmp_path, monkeypatch, report: str, reply: str):
    from meshpool_harness.agent import core
    from meshpool_harness.agent.core import Agent
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    events = []
    a = Agent(str(tmp_path), mode="auto", emit=events.append, approve=lambda *x, **k: ("yes", ""))
    seq = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "mcp__meshpool-office__make_deck", "arguments": json.dumps({"request": BRIEF})}}]},
        {"role": "assistant", "content": reply}]
    a._post = lambda base, key, body, stream: ({"choices": [{"message": seq.pop(0), "finish_reason": "stop"}],
                                                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, False)
    a._run_tool = lambda c, cut=False: report            # the office server's report for the deck build
    return a, events


REPORT = ("The slides engine finished; its quality checks passed.\nFiles:\n- home-batteries.pptx\nWhat the build did "
          "(for you; tell the user in your own words): Built home-batteries.pptx: 5 slides. The figures on slides 2, 3, "
          "4 are illustrative: no file the user gave and no cited source states them, and those slides say so.")
REPLY = ("Your 5-slide deck is ready: home-batteries.pptx. Every number comes from market research, and I made none up. "
         "Open it to review.")


def test_a_reply_that_says_none_were_made_up_is_corrected(tmp_path, monkeypatch):
    a, events = _agent(tmp_path, monkeypatch, REPORT, REPLY)
    out = a.send(BRIEF)
    assert "made none up" not in out and "slides 2, 3 and 4 say so" in out, out
    assert out.startswith("Your 5-slide deck is ready") and out.endswith("Open it to review.")
    assert any(e.get("type") == "reply_trim" and e["text"] == out for e in events)


def test_a_true_claim_is_left_alone(tmp_path, monkeypatch):
    sourced = REPORT.replace("The figures on slides 2, 3, 4 are illustrative: no file the user gave and no cited "
                             "source states them, and those slides say so.", "Every figure on the slides is from "
                             "the request or the web sources cited on the slides.")
    a, _ = _agent(tmp_path, monkeypatch, sourced, REPLY)
    assert a.send(BRIEF) == REPLY


def test_the_claim_is_found_in_its_usual_wordings():
    from meshpool_harness.agent.core import _NONE_MADE_UP
    for s in ("I made none up.", "I didn't invent any figures.", "None of the figures were made up.",
              "Nothing is made up.", "No numbers were invented.", "I made no numbers up.", "I invented nothing."):
        assert _NONE_MADE_UP.search(s), s
    for s in ("The deck has 5 slides.", "Slides 2 to 4 are illustrative.", "I made the deck in your look."):
        assert not _NONE_MADE_UP.search(s), s
