"""Library components (engines.forge.component): a library layout drawn as native, theme-only shapes. The library item
is built here, in the test (a tiny synthetic one-slide .pptx with a group, a gradient, a shadow, a picture, a text box
with placeholder text and a connector); nothing from the real knowledge base is read or committed."""
import io
import re
import sqlite3
import sys
import warnings
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from engines.forge import component as C  # noqa: E402
from engines.forge import ooxml_check as OX  # noqa: E402
from engines.forge import render as R  # noqa: E402
from engines.forge import validate_pptx as VP  # noqa: E402

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
COLS = ("id, grp, counted, scope, category, subcategory, source, source_file, slide_no, title, text_sample, n_nodes, "
        "presets, colors, fonts, w_in, h_in, area_pct, slide_w, slide_h, hash, file, thumb, bytes, tier")
SRC = {"text": "Company accounts 2021-2025"}


def _rect(sl, x, y, w, h, rgb, text=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    s = sl.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = RGBColor.from_string(rgb)
    s.line.fill.background()
    if text:
        s.text_frame.text = text
        s.text_frame.paragraphs[0].runs[0].font.size = Pt(16)
        s.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
    return s


def _item(path, nested=False, with_group=True):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    tb = sl.shapes.add_textbox(Inches(0.6), Inches(0.5), Inches(6), Inches(0.8))           # k 0: the heading slot
    tb.text_frame.text = "Lorem ipsum"
    tb.text_frame.paragraphs[0].runs[0].font.size = Pt(32)
    tb.text_frame.paragraphs[0].runs[0].font.name = "Impact"
    if with_group:                                                                           # k 1: a group of 3
        g = sl.shapes.add_group_shape()
        for i, rgb in enumerate(("1F4E79", "C0392B", "7F8C8D")):
            r = g.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.6 + 2.1 * i), Inches(2), Inches(1.9),
                                   Inches(1.2))
            r.fill.solid()
            r.fill.fore_color.rgb = RGBColor.from_string(rgb)
            r.text_frame.text = "child lorem"
        if nested:
            g.shapes.add_group_shape().shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(1), Inches(1))
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (200, 30, 30)).save(buf, "PNG")
    buf.seek(0)
    sl.shapes.add_picture(buf, Inches(11), Inches(0.4), Inches(1.5), Inches(1.5))            # k 2: a picture
    card = _rect(sl, 0.6, 3.6, 6, 1.6, "2E86C1", "Card lorem")                               # k 3: gradient + shadow
    sp = card._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}spPr")
    solid = sp.find(f"{{{A}}}solidFill")
    grad = etree.fromstring(
        f'<a:gradFill xmlns:a="{A}"><a:gsLst><a:gs pos="0"><a:srgbClr val="2E86C1"/></a:gs>'
        f'<a:gs pos="100000"><a:srgbClr val="0B3C5D"/></a:gs></a:gsLst></a:gradFill>')
    sp.replace(solid, grad)
    sp.append(etree.fromstring(f'<a:effectLst xmlns:a="{A}"><a:outerShdw blurRad="50800" dist="38100">'
                               f'<a:srgbClr val="000000"/></a:outerShdw></a:effectLst>'))
    sp.append(etree.fromstring(f'<a:scene3d xmlns:a="{A}"><a:camera prst="orthographicFront"/>'
                               f'<a:lightRig rig="threePt" dir="t"/></a:scene3d>'))
    cn = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(0.6), Inches(5.6), Inches(6.6), Inches(5.6))   # k 4
    cn.line.color.rgb = RGBColor(0x99, 0x99, 0x99)
    body = sl.shapes.add_textbox(Inches(7), Inches(3.6), Inches(5.5), Inches(1.6))           # k 5: a body slot
    body.text_frame.text = "Lorem ipsum dolor sit amet"
    body.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
    body.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x22, 0x22, 0x22)
    sl.shapes.add_textbox(Inches(0.6), Inches(7.0), Inches(2), Inches(0.3)).text_frame.text = "Click to edit footer"
    prs.save(path)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    r = tmp_path / "kb"
    (r / "library" / "sourced").mkdir(parents=True)
    (r / "thumbs").mkdir()
    con = sqlite3.connect(r / "library" / "components.db")
    con.execute("CREATE TABLE items(id TEXT PRIMARY KEY, grp TEXT, counted INT, scope TEXT, category TEXT, subcategory "
                "TEXT, source TEXT, source_file TEXT, slide_no INT, title TEXT, text_sample TEXT, n_nodes INT, presets "
                "TEXT, colors TEXT, fonts TEXT, w_in REAL, h_in REAL, area_pct REAL, slide_w INT, slide_h INT, hash "
                "TEXT, file TEXT, thumb TEXT, bytes INT, tier TEXT)")
    con.execute("CREATE VIRTUAL TABLE items_fts USING fts5(id UNINDEXED, category, subcategory, title, text_sample, "
                "content='')")
    for iid, kw in (("lib_ok", {}), ("lib_nested", {"nested": True})):
        f = r / "library" / "sourced" / f"{iid}.pptx"
        _item(f, **kw)
        vals = {"id": iid, "grp": "sourced", "scope": "full_slide", "category": "text_layout", "subcategory": "",
                "source": "pack", "title": "t", "text_sample": "t", "n_nodes": 6, "presets": "{}", "colors": "[]",
                "fonts": "[]", "w_in": 12.0, "h_in": 6.0, "file": f"library/sourced/{iid}.pptx",
                "thumb": f"thumbs/{iid}.webp", "tier": "business"}
        cols = [c.strip() for c in COLS.split(",")]
        con.execute(f"INSERT INTO items VALUES ({','.join('?' * len(cols))})", [vals.get(c) for c in cols])
    con.commit()
    con.close()
    monkeypatch.setenv("MESHPOOL_KB", str(r))
    return r


FALLBACK = {"archetype": "argument_only", "title": "Margins widened because cost fell faster than prices",
            "blocks": [{"text": "", "bullets": [{"text": "Unit cost fell 9% on the new line"},
                                                {"text": "Prices fell 4% in the same period"}]}]}


def _slide(item="lib_ok", **kw):
    s = {"archetype": "library", "item": item, "title": "Three levers lift margin by four points",
         "slots": {"0": "Margin levers", "3": "Procurement savings", "5": "Unit cost fell 9% while prices fell 4%"},
         "source": SRC, "fallback": FALLBACK}
    s.update(kw)
    return s


def _deck(tmp_path, slides, name="d.pptx"):
    out = str(tmp_path / name)
    res = R.render(slides, out, carry=False, density="balanced")
    return out, res


def _xml(path):
    z = zipfile.ZipFile(path)
    return {n: z.read(n).decode("utf8", "ignore") for n in z.namelist()}, z


def _slide_xml(path):
    parts, _ = _xml(path)
    return next(v for k, v in parts.items() if re.match(r"ppt/slides/slide\d+\.xml$", k))


def test_item_drawn_as_native_theme_shapes(lib, tmp_path):
    out, res = _deck(tmp_path, [_slide()])
    parts, z = _xml(out)
    xml = _slide_xml(out)
    assert not [e for e in res["errors"] if "library" in e.lower()], res["errors"]
    assert "<p:pic" not in xml and "blip" not in xml
    assert not [n for n in z.namelist() if n.startswith("ppt/media/")]
    for bad in ("srgbClr", "gradFill", "effectLst", "scene3d", "sp3d", "sysClr", "prstClr", "outerShdw", "Impact"):
        assert bad not in xml, bad
    lib_xml = "".join(etree.tostring(sh._element).decode() for sh in Presentation(out).slides[0].shapes
                      if sh.name.startswith("ov_Lib"))
    assert lib_xml and "typeface" not in lib_xml and "<a:latin" not in lib_xml        # theme fonts only
    assert "Lorem" not in xml and "lorem" not in xml and "Click to edit" not in xml
    for t in ("Margin levers", "Procurement savings", "Unit cost fell 9% while prices fell 4%"):
        assert t in xml
    prs = Presentation(out)
    sl = prs.slides[0]
    names = [s.name for s in sl.shapes]
    lib_names = [n for n in names if n.startswith("ov_Lib")]
    assert lib_names, names
    frame = {"FrameTop", "Source", "SourceRule", "Footnotes", "TitleRule", "FrameBottom", "Footer", "PageNumber", "Firm"}
    for s in sl.shapes:                                           # header and footer shapes, or declared overlays
        assert s.name.startswith("ov_") or s.is_placeholder or s.name in frame or s.name.startswith("role_"), s.name
    ids = [s.shape_id for s in sl.shapes]
    assert len(ids) == len(set(ids))
    for s in sl.shapes:
        if s.shape_type == 6:                                                         # a group: depth one
            assert not [c for c in s._element.iter("{http://schemas.openxmlformats.org/presentationml/2006/main}grpSp")
                        if c is not s._element]
    assert "ov_Lib" in "".join(names) and "lib_ok" not in xml
    assert OX.check(out)["ok"], OX.check(out)["problems"]


def test_rules_pass(lib, tmp_path):
    out, res = _deck(tmp_path, [_slide()])
    v = VP.validate(out, VP.load_rules(), render=False, density="balanced")
    for rule in ("P6", "P9", "P17"):
        assert v["rules"][rule] == "pass", [f for f in v["failures"] if f["rule"] == rule]


def test_group_text_cleared_and_text_color_readable(lib, tmp_path):
    out, _ = _deck(tmp_path, [_slide()])
    xml = _slide_xml(out)
    assert "child" not in xml
    prs = Presentation(out)
    for sh in prs.slides[0].shapes:
        if sh.name.startswith("ov_Lib") and sh.has_text_frame and "Procurement" in sh.text_frame.text:
            r = sh.text_frame.paragraphs[0].runs[0]
            assert r.font.size.pt >= 10


def test_restyle_unit(lib):
    prs = C.load("lib_ok")
    assert prs is not None
    pairs = list(enumerate(sh._element for sh in prs.slides[0].shapes))
    out = C.restyle(pairs, 0.8, None)
    ks = [k for k, _ in out]
    assert 2 not in ks                                          # the picture is gone; k keeps the original index
    assert ks[:2] == [0, 1]
    xml = "".join(etree.tostring(e).decode() for _, e in out)
    assert "srgbClr" not in xml and "gradFill" not in xml and "effectLst" not in xml and "Impact" not in xml
    szs = [int(m) for m in re.findall(r'sz="(\d+)"', xml)]
    assert szs and all(1000 <= s <= 2800 for s in szs), szs
    with pytest.raises(C.Unusable):
        n = Presentation(str(lib / "library" / "sourced" / "lib_nested.pptx"))
        C.restyle(list(enumerate(sh._element for sh in n.slides[0].shapes)), 1.0, None)


def test_analyse_and_schema_errors(lib):
    info = C.analyse("lib_ok")
    assert info["ok"] and info["slots"] == [0, 3, 5, 6]          # the group, picture and connector are not slots
    assert not C.analyse("lib_nested")["ok"]
    assert C.schema_errors(_slide()) == []
    assert any("'item' is missing" in e for e in C.schema_errors(_slide(item=None)))
    assert any("'slots'" in e for e in C.schema_errors(_slide(slots="text")))
    assert any("not a slot" in e and "9" in e for e in C.schema_errors(_slide(slots={"9": "x"})))
    assert any("not a slot" in e and "1" in e for e in C.schema_errors(_slide(slots=[{"k": 1, "text": "x"}])))
    assert any("'k'" in e for e in C.schema_errors(_slide(slots=[{"text": "x"}])))
    assert R.schema_errors(_slide(item="")) and any("'item'" in e for e in R.schema_errors(_slide(item="")))


def test_slots_as_list(lib, tmp_path):
    s = _slide(slots=[{"k": 0, "text": "List form"}, {"k": 5, "text": "Second line\nThird line"}])
    out, _ = _deck(tmp_path, [s])
    xml = _slide_xml(out)
    assert "List form" in xml and "Second line" in xml and "Third line" in xml


def test_text_too_long_is_cut_and_noted(lib, tmp_path):
    s = _slide(slots={"0": "Margin levers", "5": " ".join(["detail"] * 400)})
    out, res = _deck(tmp_path, [s])
    notes = Presentation(out).slides[0].notes_slide.notes_text_frame.text
    assert "[QA]" in notes
    assert any("slot" in e and "library layout" in e for e in res["errors"])


def test_off_without_knowledge_base_draws_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("MESHPOOL_KB", raising=False)
    assert C.load("lib_ok") is None
    out, res = _deck(tmp_path, [_slide()])
    prs = Presentation(out)
    sl = prs.slides[0]
    txt = " ".join(s.text_frame.text for s in sl.shapes if s.has_text_frame)
    assert "Unit cost fell 9% on the new line" in txt
    assert "arch:argument_only" in sl._element.find(
        "{http://schemas.openxmlformats.org/presentationml/2006/main}cSld").get("name")
    notes = sl.notes_slide.notes_text_frame.text
    assert "[QA]" in notes and "library" in notes and "off" in notes
    assert not [s for s in sl.shapes if s.name.startswith("ov_Lib")]


def test_bad_item_and_missing_item_draw_fallback(lib, tmp_path):
    for item in ("lib_nested", "does_not_exist"):
        out, res = _deck(tmp_path, [_slide(item=item)], name=f"{item}.pptx")
        sl = Presentation(out).slides[0]
        txt = " ".join(s.text_frame.text for s in sl.shapes if s.has_text_frame)
        assert "Unit cost fell 9% on the new line" in txt, item
        assert "[QA]" in sl.notes_slide.notes_text_frame.text
        assert item not in sl.notes_slide.notes_text_frame.text and item not in _slide_xml(out)


def test_no_fallback_given_uses_slot_texts(lib, tmp_path, monkeypatch):
    monkeypatch.delenv("MESHPOOL_KB", raising=False)
    s = _slide()
    s.pop("fallback")
    out, _ = _deck(tmp_path, [s])
    xml = _slide_xml(out)
    assert "Procurement savings" in xml and "Margin levers" in xml


def test_takeaway_and_bullets_in_side_column(lib, tmp_path):
    s = _slide(takeaway={"text": "Procurement is the biggest lever"}, bullets=["Renegotiate top 20 contracts"])
    out, _ = _deck(tmp_path, [s])
    prs = Presentation(out)
    sl = prs.slides[0]
    comp = [sh for sh in sl.shapes if sh.name.startswith("ov_Lib")]
    side = [sh for sh in sl.shapes if sh.has_text_frame and "biggest lever" in sh.text_frame.text]
    assert comp and side
    right = max(sh.left + sh.width for sh in comp)
    assert right <= side[0].left + 10
