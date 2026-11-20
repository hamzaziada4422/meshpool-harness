"""Diagram archetypes (engines/forge/diagrams): the registry and its wiring into the grid, the renderer, the schema,
the content model's archetype guide, fill checks and the density caps; the 3x3 and risk matrix in short, normal and
long versions. The tree, Gantt, radial and layer diagrams have their own test files."""
import json
from pathlib import Path

import pytest
from pptx import Presentation

from engines.forge import diagrams as D
from engines.forge import parts as P
from engines.forge import render as R

ALL = ("risk_matrix", "matrix_3x3", "gantt", "issue_tree", "org_chart", "cycle", "hub_spoke", "pyramid", "funnel")


def _risk(n_items=6, label="Supplier fails"):
    return {"archetype": "risk_matrix", "id": "s1", "node": "n1",
            "title": "Two supply risks need owners this quarter", "subtitle": "Risks by likelihood and impact",
            "items": [{"label": f"{label} {k}", "x": 1 + k % 3, "y": 1 + (k // 3) % 3} for k in range(n_items)],
            "takeaway": {"text": "Supply carries most of the exposure"},
            "bullets": [{"text": "A second source cuts the worst case by $9M"}, {"text": "Lock freight rates now"}],
            "source": {"text": "Source: risk register"}}


def _render(slides, tmp_path):
    out = tmp_path / "deck.pptx"
    r = R.render(slides, str(out), carry=False, density="balanced", fix_labels=False)
    return r, Presentation(str(out))


def test_every_diagram_archetype_is_registered_and_wired():
    for a in ALL:
        assert a in D.ARCHETYPES, a
    P._register_diagrams()
    R._register_diagram_archetypes()
    for a, kind in D.ARCHETYPES.items():
        assert R.ARCHETYPES[a] is D.a_diagram
        assert kind in P.DRAW and kind in P.FLEX and kind in P.PART_TYPES
    assert D.ARCHETYPES["gantt_plan"] == D.ARCHETYPES["gantt"]


def test_density_caps_and_dense_floor_cover_every_diagram():
    from engines.forge.validate_pptx import load_rules
    r = load_rules()["pptx"]
    for a in ALL:
        assert a in r["density"]["by_archetype"]
        for mode in ("balanced", "present"):
            assert a in r["density"]["modes"][mode]["by_archetype"], (mode, a)
        assert a in r["text_fit"]["dense_archetypes"]


def test_archetype_guide_and_fill_checks_know_the_diagrams():
    from engines.forge.build import arch_help, fill_checks
    g = arch_help("balanced")
    for a in ALL:
        assert a + ":" in g or a + " " in g, a
    thin = dict(_risk(2), takeaway=None, bullets=[])
    msgs = fill_checks([thin], "balanced")
    assert any("at least 3" in m for m in msgs[1]) and any("takeaway" in m for m in msgs[1])
    full = dict(_risk(4), bullets=[{"text": "A second source cuts the worst case by $9M across the two plants"},
                                   {"text": "Freight contracts reprice in January, so locking rates now saves $3M"},
                                   {"text": "Key engineer retention costs $1.2M and protects the ERP cutover date"}])
    assert not fill_checks([full], "balanced")
    assert any("thin" in m for m in fill_checks([_risk(4)], "balanced")[1])


@pytest.mark.parametrize("n_items,label", [(3, "Risk"), (7, "Supplier fails"), (12, "A long risk name " * 4)])
def test_risk_matrix_short_normal_long(tmp_path, n_items, label):
    s = _risk(n_items, label)
    r, prs = _render([s], tmp_path)
    assert r["errors"] == []
    sl = prs.slides[0]
    assert sl._element.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}cSld").get(
        "name").startswith("arch:risk_matrix")
    cells = [sh for sh in sl.shapes if sh.name.startswith("ov_M3Cell")]
    assert len(cells) == 9
    assert all(sh.name.startswith(("ov_", "Title", "Subtitle", "Source", "Rule", "Foot", "Page", "Firm")) or
               not sh.name.startswith("M3") for sh in sl.shapes)
    xml = sl._element.xml
    assert "srgbClr" not in xml and "gradFill" not in xml
    text = " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)
    if n_items == 12:                                  # long labels: cut to fit, never overflowing, and noted
        assert "QA" in (sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else "") or "A long risk" in text
    else:
        assert all(f"{it['label']}" in text for it in s["items"])


def test_risk_matrix_in_a_grid_half(tmp_path):
    part = dict({k: v for k, v in _risk(5).items() if k in ("items",)}, type="risk_matrix", heading="Risks")
    s = {"archetype": "grid", "id": "s1", "node": "n1", "title": "Supply risks lead the register this quarter",
         "layout": "halves", "parts": [part, {"type": "text", "lead": "Supply carries the exposure",
                                              "bullets": ["Second source cuts the worst case"]}],
         "source": {"text": "Source: register"}}
    r, prs = _render([s], tmp_path)
    assert r["errors"] == [] and sum(sh.name.startswith("ov_M3Cell") for sh in prs.slides[0].shapes) == 9


def test_matrix_3x3_with_zone_names(tmp_path):
    s = dict(_risk(4), archetype="matrix_3x3", x_axis="Ease, low -> high", y_axis="Value, low -> high",
             cells=[["Plan", "Do next", "Do first"], ["Park", "Plan", "Do next"], ["Drop", "Park", "Plan"]])
    r, prs = _render([s], tmp_path)
    assert r["errors"] == []
    assert "Do first" in " ".join(sh.text_frame.text for sh in prs.slides[0].shapes if sh.has_text_frame)


def test_bad_items_get_precise_messages_and_a_faithful_fallback(tmp_path):
    s = _risk(3)
    s["items"][1] = {"label": "No coordinates"}
    errs = R.schema_errors(s)
    assert any("items [2]" in e and "'x' and 'y'" in e for e in errs)
    assert any("at most 12" in e for e in R.schema_errors(_risk(13)))
    r, prs = _render([s], tmp_path)                    # the schema failure is reported and the words still show
    assert any("risk_matrix" in e for e in r["errors"])
    assert "No coordinates" in " ".join(sh.text_frame.text for sh in prs.slides[0].shapes if sh.has_text_frame)


def test_schema_describes_the_diagram_fields():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(R.__file__).parent / "schemas" / "slide.schema.json").read_text())
    jsonschema.validate(_risk(4), schema)
    jsonschema.validate({"archetype": "issue_tree", "id": "s", "node": "n", "title": "Price drives the gap",
                         "root": "Margin", "branches": [{"label": "Price", "children": [{"label": "Mix"}]}]}, schema)
    jsonschema.validate({"archetype": "funnel", "id": "s", "node": "n", "title": "Most leads drop at trial",
                         "stages": [{"label": "Visits", "value": 1000}, {"label": "Trials"}, {"label": "Buys"}]},
                        schema)
