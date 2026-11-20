"""Forge diagram archetypes: cycle, hub and spoke (radial.py), pyramid and funnel (layers.py). Native shapes, glued
connectors, text fitted by measurement (what is cut goes to the notes), precise schema messages. No LibreOffice here."""
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from pptx import Presentation  # noqa: E402
from pptx.oxml.ns import qn  # noqa: E402

from engines.forge import diagrams as D  # noqa: E402
from engines.forge import render as R  # noqa: E402
from engines.forge.diagrams import layers, radial  # noqa: E402


def _steps(k, text=True, words=7):
    base = ["Plan", "Build", "Run", "Learn", "Scale", "Retire"]
    body = " ".join(["detail"] * words)
    return [dict(title=base[j], **({"text": f"Step {j + 1} {body}"} if text else {})) for j in range(k)]


def _levels(k, text=True, words=7):
    base = ["Vision", "Strategy", "Programmes", "Operations", "Routines"]
    body = " ".join(["detail"] * words)
    return [dict(title=base[j], **({"text": f"Level {j + 1} {body}"} if text else {})) for j in range(k)]


def _stages(k, values=True, text=False, label="Visits"):
    base = ["Visits", "Leads", "Qualified", "Proposals", "Negotiation", "Won"]
    vals = [120000, 18000, 4500, 1300, 600, 390]
    out = []
    for j in range(k):
        d = {"label": base[j] if label == "Visits" else label}
        if values:
            d["value"] = f"{vals[j]:,}"
        if text:
            d["text"] = "Short note for this stage"
        out.append(d)
    return out


CASES = {
    "cycle": {
        "short": {"steps": _steps(3, text=False)},
        "normal": {"steps": _steps(4), "center": "Monthly", "highlight": 2},
        "long": {"steps": [dict(s, title=t) for s, t in zip(_steps(6, words=10),
                                                              ["Discover customer needs", "Design the offer",
                                                               "Pilot with five customers", "Scale to every region",
                                                               "Measure and learn", "Retire what fails"])]},
    },
    "hub_spoke": {
        "short": {"hub": "Core", "spokes": _steps(3, text=False)},
        "normal": {"hub": "Our company", "spokes": _steps(4), "highlight": 0},
        "long": {"hub": "Customer", "spokes": _steps(6, words=12)},
    },
    "pyramid": {
        "short": {"levels": _levels(3, text=False)},
        "normal": {"levels": _levels(4), "highlight": 1},
        "long": {"levels": _levels(5, words=9)},
    },
    "funnel": {
        "short": {"stages": _stages(3, values=False)},
        "normal": {"stages": _stages(4), "rates": True, "highlight": 3},
        "long": {"stages": _stages(6, text=True), "rates": True},
    },
}
PREFIX = {"cycle": "ov_CycleNode", "hub_spoke": "ov_Spoke1_", "pyramid": "ov_PyramidLevel", "funnel": "ov_FunnelBar"}
COUNT = {"cycle": lambda c: len(c["steps"]), "hub_spoke": lambda c: len(c["spokes"]),
         "pyramid": lambda c: len(c["levels"]), "funnel": lambda c: len(c["stages"])}


def _slide(arch, fields):
    return dict(archetype=arch, id="s1", node="n1", title="The diagram shows how the pieces fit together",
                subtitle="Illustrative", takeaway={"text": "Each part has an owner"},
                bullets=[{"text": "Review it every quarter"}], source={"text": "Source: test"}, **fields)


def _render(slides, tmp_path, density="balanced"):
    out = tmp_path / "d.pptx"
    r = R.render(slides, str(out), carry=False, density=density, fix_labels=False)
    return r, Presentation(str(out))


def _named(sl, prefix):
    return [shp for shp in sl.shapes if shp.name.startswith(prefix)]


def _notes(sl):
    return sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else ""


def _cxn(sl, prefix):
    """Connectors named prefix*, in name order (the renderer sorts shapes into reading order)."""
    els = [el for el in sl.shapes._spTree.iter(qn("p:cxnSp"))
           if el.find(qn("p:nvCxnSpPr")).find(qn("p:cNvPr")).get("name").startswith(prefix)]
    return sorted(els, key=lambda el: el.find(qn("p:nvCxnSpPr")).find(qn("p:cNvPr")).get("name"))


@pytest.mark.parametrize("arch", sorted(CASES))
@pytest.mark.parametrize("size", ["short", "normal", "long"])
def test_renders_cleanly_with_its_shapes(arch, size, tmp_path):
    fields = CASES[arch][size]
    r, prs = _render([_slide(arch, fields)], tmp_path)
    assert r["errors"] == []
    sl = prs.slides[0]
    assert f"arch:{arch}" in sl._element.find(qn("p:cSld")).get("name")
    assert len(_named(sl, PREFIX[arch])) == COUNT[arch](fields)
    mine = [shp for shp in sl.shapes if shp.name.startswith("ov_")]
    assert mine, "the diagram draws shapes named ov_*"
    for shp in mine:                                         # nothing below 10 pt, written into the shapes
        if shp.has_text_frame and shp.text_frame.text.strip():
            sizes = [run.font.size.pt for p in shp.text_frame.paragraphs for run in p.runs if run.font.size]
            assert sizes and min(sizes) >= 10


@pytest.mark.parametrize("size", ["short", "normal", "long"])
def test_cycle_arrows_are_glued_and_close_the_ring(size, tmp_path):
    fields = CASES["cycle"][size]
    _, prs = _render([_slide("cycle", fields)], tmp_path)
    sl = prs.slides[0]
    arrows = _cxn(sl, "ov_CycleArrow")
    assert len(arrows) == len(fields["steps"])              # the last goes back to the first
    ids = {shp.name: shp.shape_id for shp in sl.shapes}
    nodes = [ids[f"ov_CycleNode1_{j + 1}"] for j in range(len(fields["steps"]))]
    for j, el in enumerate(arrows):
        nv = el.find(qn("p:nvCxnSpPr")).find(qn("p:cNvCxnSpPr"))
        st, en = nv.find(qn("a:stCxn")), nv.find(qn("a:endCxn"))
        assert st is not None and en is not None
        assert int(st.get("id")) == nodes[j] and int(en.get("id")) == nodes[(j + 1) % len(nodes)]
        assert el.find(qn("p:spPr")).find(qn("a:ln")).find(qn("a:tailEnd")) is not None      # an arrowhead


@pytest.mark.parametrize("size", ["short", "normal", "long"])
def test_hub_spokes_are_glued_to_hub_and_spoke(size, tmp_path):
    fields = CASES["hub_spoke"][size]
    _, prs = _render([_slide("hub_spoke", fields)], tmp_path)
    sl = prs.slides[0]
    links = _cxn(sl, "ov_SpokeLink")
    assert len(links) == len(fields["spokes"])
    hub = [shp for shp in sl.shapes if shp.name == "ov_HubShape1"]
    assert len(hub) == 1 and hub[0].text_frame.text.strip() == fields["hub"]
    spokes = {shp.shape_id for shp in _named(sl, "ov_Spoke1_")}
    for el in links:
        nv = el.find(qn("p:nvCxnSpPr")).find(qn("p:cNvCxnSpPr"))
        st, en = nv.find(qn("a:stCxn")), nv.find(qn("a:endCxn"))
        assert st is not None and en is not None
        assert int(st.get("id")) == hub[0].shape_id and int(en.get("id")) in spokes
        assert el.find(qn("p:spPr")).find(qn("a:ln")).find(qn("a:tailEnd")) is None         # plain links


def test_hub_spoke_text_sits_inside_its_spoke(tmp_path):
    fields = CASES["hub_spoke"]["normal"]
    _, prs = _render([_slide("hub_spoke", fields)], tmp_path)
    sl = prs.slides[0]
    txt = {shp.name: shp.text_frame.text for shp in _named(sl, "ov_Spoke1_")}
    assert any("Step 1" in v for v in txt.values()) and all(v.strip() for v in txt.values())


def test_pyramid_hairlines_are_axis_aligned_and_text_shares_a_left_edge(tmp_path):
    fields = CASES["pyramid"]["normal"]
    _, prs = _render([_slide("pyramid", fields)], tmp_path)
    sl = prs.slides[0]
    for el in _cxn(sl, "ov_PyramidLine"):
        assert int(el.find(qn("p:spPr")).find(qn("a:xfrm")).find(qn("a:ext")).get("cy")) == 0
    assert len({shp.left for shp in _named(sl, "ov_PyramidText")}) == 1
    kinds = [shp.auto_shape_type for shp in _named(sl, "ov_PyramidLevel")]
    assert str(kinds[0]).startswith("ISOSCELES_TRIANGLE") and all(str(k).startswith("TRAPEZOID") for k in kinds[1:])


def test_funnel_bars_narrow_and_show_conversion(tmp_path):
    fields = CASES["funnel"]["normal"]
    _, prs = _render([_slide("funnel", fields)], tmp_path)
    sl = prs.slides[0]
    bars = _named(sl, "ov_FunnelBar")
    widths = [b.width for b in bars]
    assert widths == sorted(widths, reverse=True) and widths[0] > widths[-1]
    centres = {round((b.left + b.width / 2) / 9144) for b in bars}
    assert len(centres) == 1                                  # centred
    rates = [shp.text_frame.text for shp in _named(sl, "ov_FunnelRate")]
    assert rates == ["↓ 15%", "↓ 25%", "↓ 29%"]


def test_funnel_without_rates_or_values(tmp_path):
    f = {"stages": _stages(4), "rates": False}
    _, prs = _render([_slide("funnel", f)], tmp_path)
    assert not _named(prs.slides[0], "ov_FunnelRate")
    _, prs = _render([_slide("funnel", {"stages": _stages(4, values=False)})], tmp_path)
    assert not _named(prs.slides[0], "ov_FunnelRate") and len(_named(prs.slides[0], "ov_FunnelBar")) == 4


def test_num_parses_values():
    assert layers._num("12,000") == 12000 and layers._num("$4.5M") == 4.5e6 and layers._num("3k") == 3000
    assert layers._num(7) == 7 and layers._num("n/a") is None and layers._num("") is None and layers._num(None) is None
    assert layers._rate(120000, 18000) == "15%" and layers._rate(1000, 42) == "4.2%"


def _grid(kind, fields):
    quarter = lambda: {"type": "text", "heading": "Note", "lead": "A lead", "bullets": [{"text": "one"}]}   # noqa: E731
    return {"archetype": "grid", "id": "s1", "node": "n1", "title": "Four parts share one slide", "layout": "quarters",
            "look": "editorial", "parts": [dict(type=kind, heading="Diagram", **fields), quarter(), quarter(), quarter()],
            "source": {"text": "Source: test"}}


def _long_words(j, n=26):
    return " ".join(f"extraordinarily{j}x{w}" for w in range(n))


OVERFULL = {                       # each passes its check (word caps by item count) but cannot fit a quarter-slide box
    "cycle": {"steps": [dict(title=f"Step {j}", text=_long_words(j)) for j in range(4)]},
    "hub_spoke": {"hub": "Hub", "spokes": [dict(title=f"Spoke {j}", text=_long_words(j)) for j in range(6)]},
    "pyramid": {"levels": [dict(title=f"Level {j}", text=_long_words(j)) for j in range(4)]},
    "funnel": {"stages": [dict(label=f"Stage {j}", value=f"{1000 - 100 * j}", text=_long_words(j)) for j in range(4)],
               "rates": True},
}


@pytest.mark.parametrize("kind", sorted(OVERFULL))
def test_text_that_does_not_fit_is_cut_and_noted_not_overflowed(kind, tmp_path):
    r, prs = _render([_grid(kind, OVERFULL[kind])], tmp_path)
    sl = prs.slides[0]
    assert any("does not fit at the minimum size" in e for e in r["errors"])        # reported as a content failure
    assert "Did not fit and was left out" in _notes(sl)
    for shp in sl.shapes:                                    # the shapes still hold only what fits
        if shp.name.startswith("ov_") and shp.has_text_frame:
            assert len(shp.text_frame.text.split()) < 30


@pytest.mark.parametrize("arch", sorted(CASES))
@pytest.mark.parametrize("density", ["read", "present"])
def test_other_densities_render_without_errors(arch, density, tmp_path):
    r, _ = _render([_slide(arch, CASES[arch]["normal"])], tmp_path, density)
    assert r["errors"] == []


def test_part_in_a_half_region_next_to_text(tmp_path):
    s = {"archetype": "grid", "id": "s1", "node": "n1", "title": "A cycle sits beside its explanation",
         "layout": "halves", "look": "editorial", "source": {"text": "Source: test"},
         "parts": [dict(type="cycle", heading="Cycle", **CASES["cycle"]["normal"]),
                   {"type": "text", "heading": "Reading it", "lead": "Each step feeds the next",
                    "bullets": [{"text": "One"}, {"text": "Two"}]}]}
    r, prs = _render([s], tmp_path)
    assert r["errors"] == []
    assert len(_named(prs.slides[0], "ov_CycleNode")) == 4


# ------------------------------------------------------------------ check messages

def _errs(kind, **p):
    return D.part_errors(dict(type=kind, **p), 1)


def test_cycle_check_messages():
    assert _errs("cycle", steps=_steps(4)) == []
    e = _errs("cycle", steps=_steps(2))
    assert len(e) == 1 and "3 to 6" in e[0] and "got 2" in e[0]
    assert any("step 2 has no 'title'" in m for m in _errs("cycle", steps=[{"title": "A"}, {"text": "x"}, {"title": "C"}]))
    long_title = {"title": "one two three four five six seven"}
    assert any("step 1 title is 7 words" in m for m in _errs("cycle", steps=[long_title, {"title": "B"}, {"title": "C"}]))
    assert any("'highlight' is 5" in m and "0 to 3" in m for m in _errs("cycle", steps=_steps(4), highlight=5))
    assert any("'center'" in m for m in _errs("cycle", steps=_steps(4), center="a b c d e f"))
    assert any("at most 30 words" in m for m in _errs("cycle", steps=[{"title": "A", "text": "w " * 31}, {"title": "B"},
                                                                       {"title": "C"}]))
    six = [{"title": f"S{j}", "text": "w " * 13} for j in range(6)]
    e = _errs("cycle", steps=six)
    assert len(e) == 6 and "with 6 steps at most 12 words fit a half-slide box" in e[0] and "use fewer steps" in e[0]


def test_hub_spoke_check_messages():
    assert _errs("hub_spoke", hub="Hub", spokes=_steps(5)) == []
    assert any("'hub' is missing" in m for m in _errs("hub_spoke", spokes=_steps(4)))
    assert any("3 to 8" in m and "got 2" in m for m in _errs("hub_spoke", hub="Hub", spokes=_steps(3)[:2]))
    assert any("3 to 8" in m and "got 9" in m for m in _errs("hub_spoke", hub="Hub", spokes=[{"title": "S"}] * 9))
    assert any("spoke 2 has no 'title'" in m for m in _errs("hub_spoke", hub="Hub", spokes=[{"title": "A"}, {}, {"title": "C"}]))
    assert any("'highlight' is -1" in m for m in _errs("hub_spoke", hub="Hub", spokes=_steps(4), highlight=-1))
    eight = [{"title": f"S{j}", "text": "w " * 19} for j in range(8)]
    assert any("with 8 spokes at most 18 words" in m for m in _errs("hub_spoke", hub="Hub", spokes=eight))


def test_pyramid_check_messages():
    assert _errs("pyramid", levels=_levels(4)) == []
    assert any("3 to 5" in m and "got 6" in m for m in _errs("pyramid", levels=[{"title": f"L{j}"} for j in range(6)]))
    assert any("level 1 has no 'title'" in m for m in _errs("pyramid", levels=[{}, {"title": "B"}, {"title": "C"}]))
    assert any("'highlight' is 4" in m and "0 to 3" in m for m in _errs("pyramid", levels=_levels(4), highlight=4))
    five = [{"title": f"L{j}", "text": "w " * 26} for j in range(5)]
    assert any("with 5 levels at most 12 words" in m for m in _errs("pyramid", levels=five))


def test_funnel_check_messages():
    assert _errs("funnel", stages=_stages(4), rates=True) == []
    assert any("3 to 6" in m and "got 2" in m for m in _errs("funnel", stages=_stages(2)))
    bad = _stages(4)
    bad[2]["value"] = "n/a"
    assert any("'rates' needs a numeric 'value'" in m and "stage 3" in m for m in _errs("funnel", stages=bad, rates=True))
    up = _stages(4)
    up[2]["value"] = "99,000"
    assert any("stage 3 value '99,000' is larger than stage 2" in m for m in _errs("funnel", stages=up))
    part = _stages(4)
    del part[1]["value"]
    assert any("some stages have a 'value'" in m for m in _errs("funnel", stages=part))
    six = [dict(s, text="w " * 13) for s in _stages(6)]
    assert any("with 6 stages at most 12 words" in m for m in _errs("funnel", stages=six))
    assert any("stage 2 has no 'label'" in m for m in _errs("funnel", stages=[{"label": "A"}, {}, {"label": "C"}]))


@pytest.mark.parametrize("arch,fields", [("cycle", {"steps": _steps(2)}), ("hub_spoke", {"spokes": _steps(4)}),
                                         ("pyramid", {"levels": _levels(2)}), ("funnel", {"stages": _stages(2)})])
def test_schema_errors_reach_the_archetype(arch, fields):
    assert D.schema_errors(_slide(arch, fields))


def test_archetypes_are_registered():
    for a, kind in (("cycle", "cycle"), ("hub_spoke", "hub_spoke"), ("pyramid", "pyramid"), ("funnel", "funnel")):
        assert D.ARCHETYPES[a] == kind and kind in D.DRAW and D.MIN_H[kind] >= 2.0
    assert radial.p_cycle and layers.p_funnel


@pytest.mark.parametrize("kind", ["cycle", "hub_spoke", "pyramid", "funnel"])
def test_a_box_too_small_for_the_ring_never_aborts_the_deck(kind, tmp_path):
    fields = {"cycle": {"steps": _steps(5)}, "hub_spoke": {"hub": "Company", "spokes": _steps(5)},
              "pyramid": {"levels": _levels(5)}, "funnel": {"stages": _stages(6), "rates": True}}[kind]
    s = _grid(kind, fields)
    s["look"] = "boxed"
    r, prs = _render([s], tmp_path)
    assert r["slides"] == 1 and len(prs.slides) == 1             # drawn, or its words shown as text and reported
    assert all(isinstance(e, str) for e in r["errors"])
