"""The forge default template: a 16:9 master with real placeholders and a theme that carries every colour and font.

Built from python-pptx's default template (so it opens everywhere), then:
- slide size 13.333 x 7.5 in, every master/layout placeholder rescaled to it;
- theme fonts: serif headings (major) and sans body (minor), the pattern measured in the reference decks;
- theme colours: our own palette (not any firm's), one emphasis colour (accent1) and muted tones;
- title style: 24 pt, theme heading font, dk2, bottom-anchored so one- and two-line titles share a baseline.
A company master can replace it (engines/forge/brand.py): the renderer needs a title-only and a title-slide layout,
found by name or by their placeholders.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.util import Emu, Inches

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = {"a": A, "p": P}

FONTS = {"major": "Segoe UI", "minor": "Segoe UI"}     # clean and modern; titles are set bold
# Font pairs we can measure exactly (metric-compatible files are installed for each) and every Office install has.
FONT_PAIRS = {"segoe": {"major": "Segoe UI", "minor": "Segoe UI"}, "serif": {"major": "Georgia", "minor": "Arial"}, "sans": {"major": "Arial", "minor": "Arial"},
              "calibri": {"major": "Calibri", "minor": "Calibri"}, "cambria": {"major": "Cambria", "minor": "Calibri"}}
# roles: dk1 text and title, dk2 navy (callouts, darkest heat), lt2 grey half-panel, accent1 emphasis blue (numbers),
# accent2 a pale accent tint (the one highlighted row, dark text on it), accent3 cyan (the focus item in a chart), accent4 mid blue (heat),
# accent5 rule grey, accent6 muted text. Our own palette; roles follow the reference grammar.
COLORS = {"dk1": "0A1A2F", "lt1": "FFFFFF", "dk2": "0A2540", "lt2": "EEF3FA", "accent1": "1E5BD8",
          "accent2": "D6E3F8", "accent3": "29B6E8", "accent4": "1A73B8", "accent5": "BFBFBF", "accent6": "6B6B6B",
          "hlink": "1E5BD8", "folHlink": "6B6B6B"}
# Named palettes (our own values). Slot roles are the same in every palette, so the renderer never changes:
# dk1 text, dk2 darkest brand tone (callouts, bands), lt2 panel grey, accent1 emphasis, accent2 the one highlight,
# accent3 focus item, accent4 secondary series, accent5 rules, accent6 muted text.
PALETTES = {
    "navy":     COLORS,
    "ocean":    {**COLORS, "dk1": "101828", "dk2": "0B1F4D", "lt2": "EEF2F7", "accent1": "2563EB", "accent2": "F59E0B",
                 "accent3": "14B8A6", "accent4": "60A5FA", "accent5": "CBD5E1", "accent6": "64748B"},
    "forest":   {**COLORS, "dk1": "142019", "dk2": "0F3D2E", "lt2": "EEF3EF", "accent1": "1F7A4D", "accent2": "E9A23B",
                 "accent3": "4CB782", "accent4": "8FBF9F", "accent5": "C9D3CC", "accent6": "5F6F66"},
    "burgundy": {**COLORS, "dk1": "1F1416", "dk2": "5A1A2B", "lt2": "F4EFEF", "accent1": "9B2335", "accent2": "D4A017",
                 "accent3": "C2566B", "accent4": "B9848F", "accent5": "D8CCCE", "accent6": "6F6164"},
    "graphite": {**COLORS, "dk1": "1A1A1A", "dk2": "2B2B2B", "lt2": "F0F0F0", "accent1": "E8590C", "accent2": "FFD43B",
                 "accent3": "F08C4B", "accent4": "8C8C8C", "accent5": "CFCFCF", "accent6": "6B6B6B"},
    "mono":     {**COLORS, "dk1": "111111", "dk2": "111111", "lt2": "F2F2F2", "accent1": "1D4ED8", "accent2": "BFDBFE",
                 "accent3": "1D4ED8", "accent4": "9CA3AF", "accent5": "D1D5DB", "accent6": "6B7280"},
}
# Content-slide frames. "band": a dark title band across the top (the text colour, dk1) with the title in white and
# an accent rule under it, drawn in the "Title Only" layout so slides carry no extra shapes. None: the hairline grammar.
FRAMES = ("band",)
BAND_H, BAND_RULE_H = 1.16, 0.06                       # inches; the body starts at 1.55 in either frame
SLOTS = ("dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink")


def theme_spec(theme=None) -> dict:
    """{"colors": {...}, "fonts": {"major","minor"}} from a palette name, a dict (palette/fonts names, or slot -> hex
    overrides such as {"palette": "ocean", "accent1": "#7C3AED", "fonts": "sans"}), or None (navy, Segoe UI)."""
    import re as _re
    t = theme if isinstance(theme, dict) else {"palette": theme} if isinstance(theme, str) else {}
    pal = t.get("palette") if t.get("palette") in PALETTES else "navy"
    colors = dict(PALETTES[pal])
    for k in SLOTS:
        v = str(t.get(k, "")).lstrip("#")
        if _re.fullmatch(r"[0-9A-Fa-f]{6}", v):
            colors[k] = v.upper()
    fonts = t.get("fonts")
    fonts = FONT_PAIRS.get(fonts, fonts) if isinstance(fonts, str) else (fonts or FONT_PAIRS["segoe"])
    fonts = {"major": fonts.get("major", FONTS["major"]), "minor": fonts.get("minor", FONTS["minor"])}
    frame = t.get("frame") if t.get("frame") in FRAMES else None
    out = {"palette": pal, "colors": colors, "fonts": fonts}
    if frame:                                            # only when asked, so existing template caches stay valid
        out["frame"] = frame
    return out


W_IN, H_IN = 13.333, 7.5
# per user: a template another local user left in a shared /tmp cannot be replaced (and must not be trusted)
CACHE = Path(__import__("tempfile").gettempdir()) / f"forge_template_v2_{__import__('engines.icons', fromlist=['_user_tag'])._user_tag()}.pptx"


def _theme_part(prs):
    m = prs.slide_masters[0]
    return next(r.target_part for r in m.part.rels.values() if r.reltype.endswith("/theme"))


def _set_theme(prs, spec=None):
    spec = spec or theme_spec()
    part = _theme_part(prs)
    x = etree.fromstring(part.blob)
    cs = x.find(".//a:clrScheme", NS)
    cs.set("name", "Forge")
    for el in cs:
        name = etree.QName(el).localname
        if name in spec["colors"]:
            for c in list(el):
                el.remove(c)
            etree.SubElement(el, f"{{{A}}}srgbClr", val=spec["colors"][name])
    fs = x.find(".//a:fontScheme", NS)
    fs.set("name", "Forge")
    for kind in ("major", "minor"):
        lat = fs.find(f"a:{kind}Font/a:latin", NS)
        lat.set("typeface", spec["fonts"][kind])
    part._blob = etree.tostring(x, xml_declaration=True, encoding="UTF-8", standalone=True)


def _scale_shapes(holder, sx: float):
    for sh in holder.shapes:
        if sh.left is not None:
            sh.left = Emu(int(sh.left * sx))
            sh.width = Emu(int(sh.width * sx))


def _style_master(prs):
    m = prs.slide_masters[0]
    txs = m._element.find(".//p:txStyles", NS)
    t1 = txs.find("p:titleStyle/a:lvl1pPr", NS)
    t1.set("algn", "l")
    ls = t1.find("a:lnSpc", NS)
    if ls is None:
        ls = etree.Element(f"{{{A}}}lnSpc")
        t1.insert(0, ls)
        etree.SubElement(ls, f"{{{A}}}spcPct", val="90000")
    d = t1.find("a:defRPr", NS)
    d.set("sz", "2500")
    d.set("b", "1")
    for c in list(d):
        if etree.QName(c).localname in ("solidFill",):
            d.remove(c)
    sf = etree.SubElement(d, f"{{{A}}}solidFill")
    etree.SubElement(sf, f"{{{A}}}schemeClr", val="tx1")
    d.insert(0, sf)
    body = txs.find("p:bodyStyle", NS)
    for lvl, sz in (("lvl1pPr", "1400"), ("lvl2pPr", "1200"), ("lvl3pPr", "1200"), ("lvl4pPr", "1200"), ("lvl5pPr", "1200")):
        el = body.find(f"a:{lvl}", NS)
        if el is not None:
            el.find("a:defRPr", NS).set("sz", sz)
    # title placeholder geometry on the master and every layout: same box everywhere, bottom-anchored
    for holder in [m] + list(m.slide_layouts):
        for ph in holder.placeholders:
            t = ph._element.find(".//p:ph", NS).get("type")
            if t == "title":
                ph.left, ph.top, ph.width, ph.height = Inches(0.6), Inches(0.3), Inches(12.133), Inches(0.95)
                bp = ph._element.find(".//a:bodyPr", NS)
                if bp is not None:
                    bp.set("anchor", "b")
            elif t in ("body", None) and holder is m:
                ph.left, ph.width = Inches(0.6), Inches(12.133)


def _band_layout(prs):
    """The "Title Only" layout gets the band: a full-width rectangle in the text colour, an accent rule under it, and
    the title placeholder centred in it in white."""
    lay = next(lay_ for lay_ in prs.slide_masters[0].slide_layouts if lay_.name == "Title Only")
    tree = lay.shapes._spTree
    emu = lambda v: str(int(round(v * 914400)))       # noqa: E731
    for k, (nm, y, h, clr) in enumerate((("Band", 0.0, BAND_H, "tx1"), ("BandRule", BAND_H, BAND_RULE_H, "accent1"))):
        from pptx.oxml import parse_xml
        el = parse_xml(
            f'<p:sp xmlns:p="{P}" xmlns:a="{A}"><p:nvSpPr><p:cNvPr id="{90 + k}" name="{nm}"/><p:cNvSpPr/><p:nvPr/>'
            f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="{emu(y)}"/><a:ext cx="{emu(W_IN)}" cy="{emu(h)}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:schemeClr val="{clr}"/></a:solidFill>'
            f'<a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/>'
            f'</a:p></p:txBody></p:sp>')
        tree.insert(2 + k, el)                           # behind the placeholders
    for ph in lay.placeholders:
        if ph._element.find(".//p:ph", NS).get("type") == "title":
            ph.left, ph.top, ph.width, ph.height = Inches(0.6), Inches(0.12), Inches(12.133), Inches(BAND_H - 0.2)
            bp = ph._element.find(".//a:bodyPr", NS)
            if bp is not None:
                bp.set("anchor", "ctr")


def default_template(theme=None) -> str:
    """Path of the forge template for a theme (built once per theme into the temp folder)."""
    import hashlib
    import json as _json
    spec = theme_spec(theme)
    key = hashlib.sha1(_json.dumps(spec, sort_keys=True).encode()).hexdigest()[:10]
    path = _build(key, _json.dumps(spec, sort_keys=True))
    if not Path(path).exists():                         # the temp folder was cleaned under a running build (a
        _build.cache_clear()                            # relay build crashed on a deleted template): build it again
        path = _build(key, _json.dumps(spec, sort_keys=True))
    return path


@lru_cache(maxsize=16)
def _build(key: str, spec_json: str) -> str:
    import json as _json
    spec = _json.loads(spec_json)
    out = CACHE.with_name(f"{CACHE.stem}_{key}.pptx")
    prs = Presentation()
    sx = Inches(W_IN) / prs.slide_width
    prs.slide_width, prs.slide_height = Inches(W_IN), Inches(H_IN)
    m = prs.slide_masters[0]
    _scale_shapes(m, sx)
    for lay in m.slide_layouts:
        _scale_shapes(lay, sx)
    _set_theme(prs, spec)
    _style_master(prs)
    if spec.get("frame") == "band":
        _band_layout(prs)
    import os
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.tmp.pptx")
    prs.save(tmp)                                       # written whole, then moved: a concurrent build never reads
    os.replace(tmp, out)                                # a half-written template
    return str(out)


def layout(prs, name: str):
    """A layout by name; in a company master with other names, the layout that holds the same placeholders
    (engines/forge/brand.py find_layout)."""
    from .brand import find_layout
    return find_layout(prs, name)
