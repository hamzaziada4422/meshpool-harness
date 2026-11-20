"""Argument columns drawn as designed points (render.draw_points): icon cards, a 2x2 of boxes for four short points,
stacked cards otherwise, and the plain text column when the points do not fit. Nothing is cut."""
from pptx import Presentation

from engines.forge import compose as C
from engines.forge import density as D
from engines.forge import render as R
from engines.forge import validate_pptx as VP

SRC = {"text": "Source: company accounts 2021-2025"}
CHART = {"kind": "column", "categories": ["2021", "2022", "2023", "2024"],
         "series": [{"name": "Margin", "values": [8.1, 9.0, 10.4, 12.2]}], "unit": "%"}


def _slide(blocks, **kw):
    s = {"archetype": "exhibit_argument", "id": "s1", "node": "1", "title": "Four levers explain the margin gain",
         "title_right": "Mix and scale did most of the work", "exhibit": {"title": "Operating margin", "unit": "%"},
         "chart": CHART, "blocks": blocks, "callout": {"bullets": ["**Scale and mix** carry the gain"]}, "source": SRC}
    s.update(kw)
    return s


def _render(slides, tmp_path):
    out = tmp_path / "deck.pptx"
    r = R.render(slides, str(out), carry=False, density="balanced", fix_labels=False)
    return r, Presentation(str(out)), str(out)


def _cards(sl):
    return [sh for sh in sl.shapes if sh.name.startswith("ov_PointCard")]


def _text(sl):
    return " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)


FOUR = [{"label": "Scale", "text": "Volume up 38% spread fixed costs", "prov": ["F1"]},
        {"label": "Mix", "text": "Premium lines rose to 41% of sales", "prov": ["F2"]},
        {"label": "Procurement", "text": "Freight contracts cut unit cost 6%", "prov": ["F3"]},
        {"label": "Price", "text": "List prices held flat across markets", "prov": ["F4"]}]


def test_four_short_points_make_a_2x2_of_icon_boxes(tmp_path):
    r, prs, out = _render([_slide(FOUR)], tmp_path)
    assert r["errors"] == []
    sl = prs.slides[0]
    cards = _cards(sl)
    assert len(cards) == 4
    assert len({round(c.left / 914400, 2) for c in cards}) == 2 and len({round(c.top / 914400, 2) for c in cards}) == 2
    assert sum(sh.name.startswith("ov_IconRing_") for sh in sl.shapes) == 4      # an icon per point
    for b in FOUR:
        assert b["label"] in _text(sl) and b["text"].split()[0] in _text(sl)
    v = VP.validate(out, VP.load_rules(), render=False, density="balanced")
    for rule in ("P5", "P6", "P7", "P8", "P9", "P17"):
        assert v["rules"][rule] == "pass", [f for f in v["failures"] if f["rule"] == rule]


def test_longer_points_stack_as_cards_and_keep_every_word(tmp_path):
    blocks = [{"label": "Retention follows speed", "text": "Accounts live within two weeks retain 138% of revenue",
               "bullets": [{"text": "Slow starts retain 92%, a 46 point gap"}]},
              {"label": "Install time is the bottleneck", "text": "Install takes 9 weeks today across regions",
               "bullets": [{"text": "Two regional hubs would cut it to 5 weeks"}]},
              {"label": "Payback improves", "text": "Customer payback falls from 14 to 10 months"}]
    r, prs, _ = _render([_slide(blocks)], tmp_path)
    sl = prs.slides[0]
    cards = _cards(sl)
    assert r["errors"] == [] and len(cards) == 3
    assert len({c.left for c in cards}) == 1                                    # one column of cards
    tops = [c.top for c in cards]
    assert tops == sorted(tops)
    assert "Slow starts retain 92%" in _text(sl) and "Payback improves" in _text(sl)


def test_a_column_too_long_for_cards_stays_text(tmp_path):
    long = "Customer payback falls from fourteen to ten months when the install team works from regional hubs " * 2
    blocks = [{"text": long, "bullets": [{"text": long[:150]}, {"text": long[:150]}]} for _ in range(3)]
    r, prs, _ = _render([_slide(blocks)], tmp_path)
    assert _cards(prs.slides[0]) == []


def test_points_off_draws_the_text_column(tmp_path):
    R.POINTS["on"] = False
    try:
        _, prs, _ = _render([_slide(FOUR)], tmp_path)
    finally:
        R.POINTS["on"] = True
    assert _cards(prs.slides[0]) == []


def test_one_block_with_bullets_makes_an_intro_and_points():
    intro, items = R.point_items([{"text": "Three things changed", "bullets": ["Price", {"text": "Mix", "prov": ["F1"]},
                                                                              "Scale"]}])
    assert intro == "Three things changed" and [i["lead"] for i in items] == ["Price", "Mix", "Scale"]
    assert items[1]["prov"] == ["F1"]
    intro, items = R.point_items([{"label": "A", "text": "x", "bullets": ["y"]}, {"text": "z"}])
    assert intro == "" and items[0]["label"] == "A" and items[0]["bullets"][0]["text"] == "y"


def test_a_diagram_side_column_of_points_is_drawn_as_cards(tmp_path):
    s = {"archetype": "risk_matrix", "id": "s1", "node": "1", "title": "Two supply risks need owners this quarter",
         "items": [{"label": f"Supplier fails {k}", "x": 1 + k % 3, "y": 1 + k // 3} for k in range(5)],
         "takeaway": {"text": "Supply carries most of the exposure"},
         "bullets": [{"text": "A second source cuts the worst case by $9M across the two plants"},
                     {"text": "Freight contracts reprice in January, so locking rates now saves $3M"},
                     {"text": "Key engineer retention protects the ERP cutover date"}],
         "source": SRC}
    r, prs, out = _render([s], tmp_path)
    sl = prs.slides[0]
    assert r["errors"] == [] and len(_cards(sl)) == 3
    assert "Supply carries most of the exposure" in _text(sl) and "locking rates now saves $3M" in _text(sl)
    v = VP.validate(out, VP.load_rules(), render=False, density="balanced")
    for rule in ("P5", "P7", "P8"):
        assert v["rules"][rule] == "pass", [f for f in v["failures"] if f["rule"] == rule]
    one = dict(s, bullets=s["bullets"][:1])
    _, prs, _ = _render([one], tmp_path)
    assert _cards(prs.slides[0]) == []                                          # one point stays text


def test_icons_follow_the_first_meaning_in_the_text():
    assert C.meaning_icon("Leaders grew volume 38% and kept take rates low") == R.icon_name("trending-up")
    assert C.meaning_icon("Corridor charges 0.9% across 4,200 businesses") == R.icon_name("tag")
    assert C.meaning_icon("Shoppers already tried a plant-based milk") == R.icon_name("users")
    assert C.meaning_icon("Plant 3 adds capacity") == R.icon_name("factory")
    for _, name in C.MEANINGS:
        assert (R.ICONS / f"{R.icon_name(name)}.svg").exists(), name


def test_density_keeps_four_short_points_and_caps_long_ones():
    assert len(D.condense(_slide([dict(b) for b in FOUR]), "balanced")["blocks"]) == 4
    long = [{"text": "word " * 20} for _ in range(4)]
    assert len(D.condense(_slide(long), "balanced")["blocks"]) == 3
