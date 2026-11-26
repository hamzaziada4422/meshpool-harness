"""Daniel (consultant persona), 0.0.19: the first-pass deck and memo were not client-ready. Each test drives the real
engines with a scripted plan (no model is called) and reads the built file with python-pptx / python-docx.

Items: 1 the brief's brand items (footer, navy, amber, Georgia) reach the deck and the QA loop holds them; 2 speaker
notes are presenter prose only; 3 visible flaws the checks now catch; 4 the check details name what failed."""
from __future__ import annotations

import copy
import json
import re
import zipfile
from pathlib import Path

import pytest
from pptx import Presentation

PLAN = json.loads((Path(__file__).parent / "fixtures" / "daniel019" / "slides.json").read_text())
BRIEF = ("Make a 6-slide client deck from this report for the leadership team at a mid-size packaging manufacturer "
         "that is deciding whether to start predictive maintenance. I'm presenting as Ridgeway Advisory, so brand it "
         "as ours: deep navy (#14304F) with an amber accent (#E8A33D), Georgia headings, clean white background. "
         "Footer should say 'Ridgeway Advisory | Confidential' with slide numbers, and must not mention any tool or "
         "product name. Add short, clean speaker notes on every slide that I can read aloud. Use defaults.")
FACTS = ("\n\nFacts\n- F6: 47% of mid-market plants used predictive maintenance in 2025, up from 18% in 2021; Germany "
         "58%, Netherlands 51%, Italy 42%, Poland 31%\n- F7: adopters cut unplanned downtime by a median 23% and "
         "maintenance spend by 14% within 24 months\n- F9: pilot-first pays back in 19 months with 71% success; "
         "plant-wide 31 months, 44%\n")
FOOTER = "Ridgeway Advisory | Confidential"


class Scripted:
    """The model's side of a build, scripted: the storyline and each slide come from Daniel's first-pass plan."""

    def __init__(self, plan):
        self.plan = {s["node"]: s for s in plan}

    def chat(self, *, model, messages, **kw):
        sys_, user = messages[0]["content"], messages[-1]["content"]
        if "engagement manager" in sys_:
            body = {"governing_thought": "Start with one pilot line", "structure": "SCR",
                    "slides": [{"node": s["node"], "title": s["title"], "archetype": s["archetype"],
                                "section": s.get("section", ""), "evidence": ["F6", "F7", "F9"]}
                               for s in self.plan.values()]}
        elif "ONE consulting slide" in sys_:
            x = json.loads(user.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
            body = copy.deepcopy(self.plan.get(x.get("node")) or x)
        else:
            body = {"edits": []}
        return {"choices": [{"message": {"content": json.dumps(body)}}]}


def _theme(path):
    with zipfile.ZipFile(path) as z:
        x = z.read("ppt/theme/theme1.xml").decode()
    cols = dict(re.findall(r"<a:(dk1|dk2|accent1|accent2|accent3)><a:srgbClr val=\"([0-9A-F]{6})\"", x))
    fonts = dict(re.findall(r"<a:(major|minor)Font><a:latin typeface=\"([^\"]+)\"", x))
    return cols, fonts


def _notes(path):
    return [s.notes_slide.notes_text_frame.text if s.has_notes_slide else "" for s in Presentation(path).slides]


@pytest.fixture()
def offline(monkeypatch):
    """A build with no reviewers, no research and no LibreOffice render (the checks read the file itself)."""
    from engines.forge import build as B
    from engines.forge import common as CM
    monkeypatch.setattr(B, "_review_on", lambda client, rules: False)
    monkeypatch.setenv("MH_PERSONAS", "0")
    monkeypatch.setenv("MH_DOSSIER", "0")
    monkeypatch.setenv("MH_CLARITY", "0")
    monkeypatch.setattr(CM, "find_soffice", lambda *a, **k: None)
    rules = CM.load_rules()
    rules = copy.deepcopy(rules)
    rules["repair"]["max_rounds"] = 1
    monkeypatch.setattr(B, "load_rules", lambda *a, **k: rules)
    return B


@pytest.fixture(scope="module")
def daniel_deck(tmp_path_factory):
    """Daniel's exact brief through the slides engine (engines/forge/engine.py), on the scripted plan."""
    mp = pytest.MonkeyPatch()
    from engines.forge import build as B
    from engines.forge import common as CM
    mp.setattr(B, "_review_on", lambda client, rules: False)
    mp.setenv("MH_PERSONAS", "0")
    mp.setenv("MH_DOSSIER", "0")
    mp.setenv("MH_ASK_AUTO", "1")
    mp.setattr(CM, "find_soffice", lambda *a, **k: None)
    rules = copy.deepcopy(CM.load_rules())
    rules["repair"]["max_rounds"] = 1
    mp.setattr(B, "load_rules", lambda *a, **k: rules)
    from engines.forge.engine import solve
    work = tmp_path_factory.mktemp("daniel019")
    res = solve(BRIEF + FACTS, workdir=str(work), task_id="d019", client=Scripted(PLAN), files=[])
    mp.undo()
    out = Path(res["artifacts"][0])
    return {"res": res, "pptx": str(out), "qa": json.loads((out.parent / "qa.json").read_text())}


# ---------------------------------------------------------------------------------------------------- item 1
def test_the_briefs_brand_items_are_in_the_built_deck(daniel_deck):
    """The brief asked for footer 'Ridgeway Advisory | Confidential', navy #14304F, amber #E8A33D and Georgia
    headings. The first pass had a page-number-only footer (none on slide 1), a bright blue accent and Segoe UI."""
    p = daniel_deck["pptx"]
    prs = Presentation(p)
    assert len(prs.slides) == 6
    for i, sl in enumerate(prs.slides, 1):
        txt = " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)
        assert FOOTER in txt, (i, txt[-200:])
    cols, fonts = _theme(p)
    assert cols["dk2"] == "14304F" and cols["dk1"] == "14304F", cols
    assert cols["accent1"] == "E8A33D" and cols["accent3"] == "E8A33D", cols
    assert fonts["major"] == "Georgia", fonts
    # the accent is the highlighted series: the chart's focus bar is drawn in accent1 (now amber)
    charts = [sh.chart for sl in prs.slides for sh in sl.shapes if getattr(sh, "has_chart", False) and sh.has_chart]
    assert charts and any(b'val="accent1"' in c.part.blob for c in charts)
    assert daniel_deck["qa"]["rules"]["P28"] == "pass", daniel_deck["qa"]["rules"]


def test_the_qa_loop_fixes_a_brand_item_the_render_missed_in_the_same_job(tmp_path, offline):
    """The request's brand is verified on the built file (rule P28); when an item did not hold, the job fixes the file
    itself and checks again, instead of leaving it to a follow-up turn."""
    from engines.forge import requested as RQ
    spec = RQ.parse(BRIEF)
    plan = [s for s in PLAN if s["archetype"] in ("cover", "grid")][:3]
    # the brand reaches the checks but not the renderer: no theme, no footer (the first pass, in effect)
    res = offline.build(BRIEF + FACTS, str(tmp_path), slides_wanted=3, client=Scripted(plan), task_id="t",
                        brand={"asked": spec})
    assert res["rules"]["P28"] == "pass", res["rules"]
    assert RQ.check(res["pptx"], spec) == []
    cols, fonts = _theme(res["pptx"])
    assert cols["accent1"] == "E8A33D" and fonts["major"] == "Georgia"
    for sl in Presentation(res["pptx"]).slides:
        assert FOOTER in " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)


def test_brand_items_are_read_from_the_users_and_the_agents_wording():
    from engines.forge import requested as RQ
    for text in (BRIEF, "BRANDING: Ridgeway Advisory. Deep navy #14304F as primary colour, amber #E8A33D as the "
                        "accent, Georgia for all headings. Footer on every slide must read exactly "
                        "'Ridgeway Advisory | Confidential' with slide numbers."):
        sp = RQ.parse(text)
        assert sp["colors"] == {"primary": "14304F", "accent": "E8A33D"}, sp
        assert sp["fonts"]["major"] == "Georgia" and sp["footer"] == FOOTER and sp["confidential"]
    assert RQ.parse("A deck about our plant in Georgia with 2025 numbers.") == {}
