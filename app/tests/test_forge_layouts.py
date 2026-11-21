"""Built-in layouts (engines/forge/layouts.py, data/layouts.json): the shipped file holds abstract geometry and roles
only (no template text, colours, fonts, pictures, custom geometry or source names), the archetype draws them natively in
the deck's theme, and the search finds one for a slide's message. The data is generated locally from the private
library; these tests read only the shipped file."""
import json
import re
import sys
import warnings
import zipfile
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from engines.forge import layouts as LY  # noqa: E402
from engines.forge import ooxml_check as OX  # noqa: E402
from engines.forge import render as R  # noqa: E402
from engines.forge import validate_pptx as VP  # noqa: E402

RAW = LY.DATA.read_text()
ROLES = {"bg1", "bg2", "tx1", "tx2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6"}
SRC = {"text": "Source: company accounts 2021-2025"}


def _text_for(role, holds, max_words):
    if role in ("number", "value"):
        return "42%"
    words = "Revenue grows faster than costs across the core markets every quarter this year".split()
    k = 2 if role in ("label", "heading", "tag", "axis", "legend", "caption") else min(max_words or 8, 8)
    return " ".join(words[:max(1, min(k, max_words or k))])


def _slots(lay):
    out = {}
    for i, (role, holds, mw) in enumerate(lay["slots"], 1):
        if role == "chart":
            out[str(i)] = {"kind": "column", "categories": ["2021", "2022", "2023"],
                           "series": [{"name": "Sales", "values": [10, 14, 18]}]}
        elif role == "table":
            out[str(i)] = [["Item", "2022", "2023"], ["Alpha", "1", "2"], ["Beta", "3", "4"], ["Gamma", "5", "6"]]
        else:
            out[str(i)] = _text_for(role, holds, mw)
    return out


def _slide(lay, **kw):
    s = {"archetype": "layout", "layout": lay["id"], "title": "Revenue grows faster than costs in the core",
         "slots": _slots(lay), "source": SRC}
    s.update(kw)
    return s


def _pick(k=5):
    """k layouts from k different categories (fewest slots first), chosen from the shipped data."""
    seen, out = set(), []
    for lay in sorted(LY.load(), key=lambda x: (len(x["slots"]), x["id"])):
        if lay["c"] not in seen and len(lay["slots"]) >= 3 and len(lay["sh"]) <= 40:
            seen.add(lay["c"])
            out.append(lay)
    return out[:k]


def _render(slides, tmp_path):
    out = tmp_path / "deck.pptx"
    res = R.render(slides, str(out), carry=False, density="balanced", fix_labels=False)
    return str(out), res


# ------------------------------------------------------------------ the shipped file
def test_layouts_load_and_are_well_formed():
    lays = LY.load()
    assert len(lays) >= 50
    ids = [x["id"] for x in lays]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[0-9a-f]{6,10}", i) for i in ids)
    assert LY.get(ids[0])["id"] == ids[0] and LY.get("nope") is None
    for lay in lays:
        assert lay["slots"], lay["id"]                                   # every spec has slots
        assert lay["sh"] and lay["n"] and lay["u"] and lay["c"] and lay["r"] > 0
        n = len(lay["slots"])
        used = set()
        for o in lay["sh"]:
            assert len(o["b"]) == 4 and all(isinstance(v, int) and 0 <= v <= LY.UNIT for v in o["b"]), (lay["id"], o)
            for key in ("f", "l", "tc"):
                assert o.get(key, "bg1") in ROLES, (lay["id"], key, o.get(key))
            if o["p"] not in ("line", "chart", "table"):
                MSO_SHAPE.from_xml(o["p"])                               # a preset name PowerPoint knows
            if "s" in o:
                assert 1 <= o["s"] <= n, (lay["id"], o)
                used.add(o["s"])
        assert used == set(range(1, n + 1)), (lay["id"], sorted(set(range(1, n + 1)) - used))
        for role, holds, mw in lay["slots"]:
            assert role and holds and isinstance(mw, int)


def test_shipped_file_holds_no_colours_fonts_pictures_or_sources():
    low = RAW.lower()
    assert "srgbclr" not in low and "custgeom" not in low and "blipfill" not in low and "<p:pic" not in low
    assert not re.search(r"#[0-9a-f]{6}\b", low)
    for font in ("arial", "calibri", "cambria", "georgia", "helvetica", "times new roman", "verdana", "tahoma",
                 "segoe", "garamond", "lato", "roboto", "open sans", "typeface", "font"):
        assert not re.search(rf"\b{font}\b", low), font
    for word in ("strategyu", "deckary", "slidescarnival", "presentationgo", "showeet", "slidesalad", "smartsheet",
                 "mckinsey", "bcg", "boston consulting"):
        assert word not in low, word
    assert not re.search(r"_s\d+", low), "an item id (…_s012) is in the file"
    assert not re.search(r"\b(crimson|navy|orange|serif)\b", low)       # descriptions name theme roles, not colours
    assert not re.search(r"\.(png|jpe?g|emf|svg|pptx)\b", low)
    for lay in LY.load():
        assert set(lay) <= {"id", "n", "u", "st", "fw", "dn", "c", "a", "r", "g", "slots", "sh"}
        for o in lay["sh"]:
            assert set(o) <= {"p", "b", "f", "l", "lw", "d", "e", "r", "fh", "fv", "a", "s", "fs", "bd", "al", "an", "v",
                              "tc", "tx", "ck", "tb", "ic"}, o


# ------------------------------------------------------------------ search
def test_search_finds_a_relevant_layout():
    hits = LY.search("options against criteria")
    assert hits and re.search(r"(?i)option|criteria|compar|2x2", hits[0]["name"] + hits[0]["use_when"]), hits[:2]
    steps = LY.search("four steps of a process")
    assert steps and re.search(r"(?i)step|process|phase|cycle|stage", steps[0]["name"] + steps[0]["use_when"]), steps[:2]
    assert LY.search("of the and") == []
    only = LY.search("timeline of milestones", category="timeline_roadmap", limit=2)
    assert only and all(h["category"] == "timeline_roadmap" for h in only) and len(only) <= 2


# ------------------------------------------------------------------ schema
def test_schema_errors_are_precise():
    lay = next(x for x in LY.load() if any(s[0] in ("label", "heading") and s[2] <= 4 for s in x["slots"]))
    k = next(i for i, s in enumerate(lay["slots"], 1) if s[0] in ("label", "heading") and s[2] <= 4)
    base = _slide(lay)
    assert R.schema_errors(base) == []
    errs = R.schema_errors(dict(base, layout="zzzzzz"))
    assert any("unknown layout 'zzzzzz'" in e for e in errs), errs
    errs = R.schema_errors(dict(base, slots={**base["slots"], "99": "x"}))
    assert any("slot 99 is not a slot of layout" in e and f"1 to {len(lay['slots'])}" in e for e in errs), errs
    errs = R.schema_errors(dict(base, slots={**base["slots"], str(k): "one two three four five six seven"}))
    assert any(f"slot {k} has 7 words, over its limit of {lay['slots'][k - 1][2]}" in e for e in errs), errs
    assert any("'slots' is missing" in e for e in R.schema_errors(dict(base, slots={})))
    assert any("not a slot number" in e for e in R.schema_errors(dict(base, slots={"x": "y"})))
    assert any("'layout' is missing" in e for e in R.schema_errors({k_: v for k_, v in base.items() if k_ != "layout"}))


def test_fallback_is_a_plain_argument_slide():
    lay = _pick(1)[0]
    bad = _slide(lay, layout="zzzzzz", takeaway={"text": "The core is growing"})
    fb = R.fallback(bad)
    assert fb["archetype"] == "argument_only" and fb["title"] == bad["title"]
    assert any("Revenue" in b["text"] for b in fb["blocks"][0]["bullets"])
    assert "[QA]" in fb["notes"]


# ------------------------------------------------------------------ drawing
def test_five_layouts_render_clean(tmp_path):
    picks = _pick(5)
    assert len(picks) == 5 and len({x["c"] for x in picks}) == 5
    slides = [_slide(x) for x in picks]
    for s in slides:
        assert R.schema_errors(s) == [], (s["layout"], R.schema_errors(s))
    out, res = _render(slides, tmp_path)
    assert res["errors"] == [], res["errors"]
    z = zipfile.ZipFile(out)
    assert not [n for n in z.namelist() if n.startswith("ppt/media/")]
    prs = Presentation(out)
    for sl in prs.slides:
        xml = "".join(zipfile.ZipFile(out).read(n).decode("utf8", "ignore") for n in z.namelist()
                      if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        assert "<p:pic" not in xml and "srgbClr" not in xml and "custGeom" not in xml
        lay_shapes = [s for s in sl.shapes if s.name.startswith("ov_Lay")]
        assert lay_shapes
        for s in sl.shapes:                                                  # header and footer, or declared overlays
            assert s.is_placeholder or s.name.startswith("ov_") or s.name in {
                "FrameTop", "Source", "SourceRule", "Footnotes", "TitleRule", "FrameBottom", "Footer", "PageNumber",
                "Firm", "Title"} or s.name.startswith("role_"), s.name
    assert OX.check(out)["ok"], OX.check(out)["problems"]
    v = VP.validate(out, VP.load_rules(), render=False, density="balanced")
    for rule in ("P6", "P9", "P17"):
        assert v["rules"][rule] == "pass", [f for f in v["failures"] if f["rule"] == rule]


def test_unfilled_slots_leave_no_empty_text_holders(tmp_path):
    lay = next(x for x in _pick(5) if len(x["slots"]) >= 4)
    keep = {"1": _slots(lay)["1"], "2": _slots(lay)["2"]}
    out, res = _render([_slide(lay, slots=keep)], tmp_path)
    assert res["errors"] == []
    prs = Presentation(out)
    for sh in prs.slides[0].shapes:
        if sh.name.startswith("ov_Lay") and sh.shape_type == 17:              # a text box: never an empty one
            assert sh.text_frame.text.strip(), sh.name


def test_takeaway_and_bullets_go_in_the_right_third(tmp_path):
    lay = next(x for x in _pick(5))
    out, res = _render([_slide(lay, takeaway={"text": "The core is growing"}, bullets=["Costs are flat", "Mix improves"])],
                       tmp_path)
    assert res["errors"] == []
    prs = Presentation(out)
    width = prs.slide_width
    texts = [sh for sh in prs.slides[0].shapes if sh.has_text_frame and "The core is growing" in sh.text_frame.text]
    assert texts and texts[0].left > width * 0.6
    drawn = [sh for sh in prs.slides[0].shapes if sh.name.startswith("ov_Lay")]
    assert max(sh.left + sh.width for sh in drawn) <= width * 0.68


def test_text_is_fitted_and_cut_text_is_noted(tmp_path):
    lay = next(x for x in _pick(5) if any(s[0] == "body" for s in x["slots"]))
    i = next(i for i, s in enumerate(lay["slots"], 1) if s[0] == "body")
    slots = _slots(lay)
    slots[str(i)] = " ".join(["word"] * lay["slots"][i - 1][2])
    out, res = _render([_slide(lay, slots=slots)], tmp_path)
    assert res["errors"] == []
    for sh in Presentation(out).slides[0].shapes:
        if sh.name.startswith("ov_Lay") and sh.has_text_frame:
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    assert r.font.size is None or r.font.size.pt >= 10, (sh.name, r.font.size.pt)


# ------------------------------------------------------------------ public notes
def test_public_notes_are_current_and_linked(tmp_path):
    from engines.forge import vault as V
    n = LY.notes(tmp_path)
    assert n == len(LY.load())
    committed = V.PUBLIC / "layouts"
    for p in tmp_path.glob("*.md"):
        assert (committed / p.name).read_text() == p.read_text(), f"run: python -m engines.forge.layouts notes ({p.name})"
    assert len(list(committed.glob("*.md"))) == len(list(tmp_path.glob("*.md")))
    lay = LY.load()[0]
    note = (tmp_path / f"{lay['id']}.md").read_text()
    assert note.startswith('---\ntype: "forge-layout"') and "Slots:" in note and "[[layouts-" in note
    for p in tmp_path.glob("*.md"):                                          # every [[link]] points at a note
        for link in re.findall(r"\[\[([^\]]+)\]\]", p.read_text()):
            assert link in V.ARCHETYPES or link in V.FAMILIES or (tmp_path / f"{link}.md").exists(), (p.name, link)
    low = "".join(p.read_text() for p in tmp_path.glob("*.md")).lower()
    for word in ("strategyu", "deckary", "slidescarnival", "presentationgo", "showeet", "slidesalad", "smartsheet",
                 "mckinsey", "bcg"):
        assert word not in low
    assert not re.search(r"_s\d+", low)


def test_offered_layouts_render_clean(tmp_path):
    """The layouts offered to the model pass the layout rules on the render (testkit/forge_layout_gate.py wrote the
    list); the held-back ones still render when a deck names them, but the storyline and the tools never offer them."""
    import importlib.util
    from engines.forge.common import find_soffice
    if not find_soffice():
        pytest.skip("needs LibreOffice: P1 and P20 measure the render")
    spec = importlib.util.spec_from_file_location("gate", Path(__file__).resolve().parents[2] / "testkit" /
                                                  "forge_layout_gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    offered = list(LY.offered())
    assert 30 <= len(offered) < len(LY.load())
    assert gate.failures(offered, str(tmp_path / "offered.pptx")) == {}
    assert all(h["id"] in LY.offered_ids() for h in LY.search("process steps timeline options market", limit=50))
    from engines.forge.build import layout_catalogue
    cat = layout_catalogue()
    held = [x["id"] for x in LY.load() if x["id"] not in LY.offered_ids()]
    assert all(f"{i}:" in cat for i in LY.offered_ids()) and not any(f"\n{i}:" in cat for i in held)
