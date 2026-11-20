"""Zero images: SVG icons and logos as native vector shapes (engines/forge/vector.py), and P9 failing any picture."""
import zipfile

import pytest
from pptx import Presentation
from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.util import Inches

from engines.forge import vector as V


def _close(a, b, tol=1e-6):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def test_path_commands_absolute_and_relative():
    subs = V.parse_path("M2 2 h4 v4 H2 Z m10 0 l2 2 L16 2 z")
    assert len(subs) == 2
    assert subs[0][:4] == [("M", (2, 2)), ("L", (6, 2)), ("L", (6, 6)), ("L", (2, 6))] and subs[0][-1] == ("Z",)
    assert subs[1][0] == ("M", (12, 2)) and subs[1][1] == ("L", (14, 4))
    q = V.parse_path("M0 0 Q 5 10 10 0 T 20 0")[0]                     # quadratics become cubics, T reflects
    assert q[1][0] == "C" and _close(q[1][3], (10, 0)) and _close(q[2][3], (20, 0))
    s_ = V.parse_path("M0 0 C 0 5 5 5 5 0 S 10 -5 10 0")[0]
    assert _close(s_[2][1], (5, -5))                                      # S mirrors the last control point


def test_arcs_become_cubics_that_end_where_the_arc_ends():
    sub = V.parse_path("M 0 10 A 10 10 0 0 1 20 10")[0]                  # a half circle over the top
    assert all(seg[0] == "C" for seg in sub[1:]) and len(sub) == 3       # two quarter-circle cubics
    assert _close(sub[-1][3], (20, 10)) and _close(sub[1][3], (10, 0), 1e-6)


def test_shapes_and_transforms():
    d = V.parse_svg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                    'stroke-width="2"><circle cx="12" cy="12" r="4"/><rect x="2" y="2" width="4" height="4" rx="1"/>'
                    '<g transform="translate(10 0)"><line x1="0" y1="0" x2="2" y2="2"/></g>'
                    '<polygon points="1,1 3,1 2,3" fill="black" stroke="none"/></svg>')
    assert d["box"] == (0, 0, 24, 24) and len(d["paths"]) == 4
    circle, rect, line, tri = d["paths"]
    assert circle["stroke"] and not circle["fill"] and circle["width"] == 2
    assert line["subs"][0][0] == ("M", (10, 0)) and line["subs"][0][1] == ("L", (12, 2))
    assert tri["fill"] and not tri["stroke"]


def _slide():
    prs = Presentation()
    return prs, prs.slides.add_slide(prs.slide_layouts[6])


def test_icon_is_one_native_shape_in_a_theme_colour(tmp_path):
    from engines.icons import svg
    prs, sl = _slide()
    shp = V.add_svg(sl, svg("banknote", "lucide"), 1, 1, 0.5, 0.5, "ov_Icon_money", TC.ACCENT_1,
                    alt="icon:lucide:money:money")
    xml = shp._element.xml
    assert "<a:custGeom>" in xml and "a:cubicBezTo" in xml and 'fill="none"' in xml and "schemeClr" in xml
    assert "srgbClr" not in xml and 'cap="rnd"' in xml
    filled = V.add_svg(sl, svg("bank", "bootstrap"), 2, 1, 0.5, 0.5, "ov_Icon_bank", TC.TEXT_1, alt="icon:bootstrap:bank:x")
    assert "<a:solidFill>" in filled._element.xml
    out = tmp_path / "v.pptx"
    prs.save(out)
    assert not [n for n in zipfile.ZipFile(out).namelist() if n.startswith("ppt/media/")]
    from engines.forge.ooxml_check import check
    assert check(str(out))["ok"]
    assert V.add_svg(sl, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>', 0, 0, 1, 1, "x",
                     TC.TEXT_1) is None


def test_measure_counts_vector_icons_and_p9_fails_pictures(tmp_path):
    from PIL import Image
    from engines.forge.measure_pptx import measure
    from engines.forge.validate_pptx import load_rules, validate
    from engines.icons import svg
    prs, sl = _slide()
    for i in range(5):
        V.add_svg(sl, svg("users", "lucide"), 1 + i, 3, 0.4, 0.4, f"ov_Icon_{i}", TC.TEXT_1, alt="icon:lucide:people:p")
    png = tmp_path / "p.png"
    Image.new("RGB", (40, 40), "red").save(png)
    sl.shapes.add_picture(str(png), Inches(1), Inches(5), Inches(2), Inches(2)).name = "Photo"
    out = tmp_path / "m.pptx"
    prs.save(out)
    rules = load_rules()
    m = measure(str(out), rules)
    s = m["slides"][0]
    assert len(s["icons"]) == 5 and s["pictures"] == 1
    fails = [f for f in validate(str(out), rules, render=False)["failures"] if f["rule"] == "P9"]
    assert any(f["check"] == "pictures" for f in fails) and any(f["check"] == "too_many_icons" for f in fails)
    assert rules["pptx"]["images"]["allow_pictures"] is False


def test_logos_svg_as_vector_raster_left_out(tmp_path, monkeypatch):
    from PIL import Image
    from engines.forge import render as R
    prs, sl = _slide()
    svgf = tmp_path / "logo.svg"
    svgf.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 10"><rect width="40" height="10"/></svg>')
    pngf = tmp_path / "logo.png"
    Image.new("RGB", (80, 20), "blue").save(pngf)
    monkeypatch.setitem(R.PICTURES, "allow", False)
    w = R.logo_shape(sl, svgf, 1, 1, 0.3, 5, "ov_Logo", "Acme logo")
    assert w == pytest.approx(1.2) and sl.shapes[-1].shape_type != 13
    assert R.logo_shape(sl, pngf, 1, 2, 0.3, 5, "ov_Logo2", "Acme logo") == 0.0
    monkeypatch.setitem(R.PICTURES, "allow", True)
    assert R.logo_shape(sl, pngf, 1, 2, 0.3, 5, "ov_Logo2", "Acme logo") > 0 and sl.shapes[-1].shape_type == 13
