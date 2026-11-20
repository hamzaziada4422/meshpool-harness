"""Forge renderer v2: slide JSON -> native .pptx in the reference consulting grammar. Deterministic.

The grammar (measured on McKinsey reference pages; docs/deck-engine/design-grammar.md) lives here, so no model
output can fall back to a card-template look:
- structure from white space and hairline rules (under the title, under column headers, between rows, above the
  source), never from tinted cards;
- emphasis is typographic: number phrases in body text are bold accent blue, **marked phrases** are bold, blocks
  open with a bold lead line, bullets are small accent squares;
- one highlight per exhibit: a coral table row, a cyan focus point, a navy "so what" box with an arrow;
- heavy bold serif title in the text colour with a full-width rule; exhibit subtitle in regular sans under it;
- footer system: footnotes, rule, source line, firm label and page number.
Theme colours and theme fonts only; native charts and tables. Writes <out>.pptx and <out>.provenance.json.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import BubbleChartData, CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_TICK_LABEL_POSITION
from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from .common import numbers as number_tokens, positive_axis_ids
from .density import condense
from .template import default_template, layout

ICONS = Path(__file__).resolve().parents[1] / "slides" / "icons"
ICON_CACHE = Path(__import__("tempfile").gettempdir()) / "forge_icons"

LEFT, RIGHT = 0.6, 12.733
COLS, GUT = 24, 0.125            # 24-column grid (reference decks use a fine grid); span()/colx() take 12-column units
CW = (RIGHT - LEFT - GUT * (COLS - 1)) / COLS
TITLE_RULE_Y, SUB_Y, BODY_Y, BODY_B = 1.42, 1.06, 1.55, 6.62
FOOTNOTE_Y, FOOT_RULE_Y, SRC_Y = 6.62, 7.02, 7.06
# type scale measured on the reference pages (pt): one title size everywhere, body size set by density
T = {"title": 25, "sub": 18, "note": 9, "src": 9, "tag": 9, "label": 12, "body": 12, "lead": 12, "cell": 12,
     "head": 14, "num": 20}
DENSE = {"cell": 10, "lead": 10, "head": 14, "num": 20, "label": 10}            # challenge matrix (page 8)
ARGUMENT = {"intro": 14, "bullet": 12, "callout": 12, "exhibit": 16, "unit": 12}  # exhibit + argument (page 3)
FIRM = "Meshpool"
BRAND = {"firm": None, "footer": None, "logo": None, "footer_right": None}   # the company look for this render (engines/forge/brand.py)
_BASE = {"T": dict(T), "DENSE": dict(DENSE), "ARGUMENT": dict(ARGUMENT)}
DENSITY = {"mode": "balanced", "bump": 1}       # deck density (engines/forge/density.py); set by render()


def set_density(mode: str | None) -> str:
    """Apply a density mode's type scale to T, DENSE and ARGUMENT in place (they are read at draw time, like FACE)."""
    from .density import DEFAULT, SCALES, normalize as _norm
    mode = _norm(mode) or DEFAULT
    sc = SCALES[mode]
    for name, table in (("T", T), ("DENSE", DENSE), ("ARGUMENT", ARGUMENT)):
        table.clear()
        table.update(_BASE[name])
        table.update(sc[name])
    DENSITY.update(mode=mode, bump=sc["bump"])
    return mode


def bump(size: float, k: float = 1.0) -> float:
    """A part's base size in the current density mode (read 12 -> balanced 13 -> present 16); k < 1 for labels in
    tight places (matrix axes, harvey column heads)."""
    return size + int(round(DENSITY["bump"] * k))
LEADING = 1.08                   # exact line pitch as a multiple of size (12 pt text on 13 pt, as in the reference);
                                 # exact points render identically in PowerPoint and LibreOffice, proportional does not


def _pitch(size, factor=LEADING):
    return round(size * factor * 2) / 2
_B = r"(?<![\w\-'/.])"                       # not glued to a word, a range or a date
_E = r"(?![\w\-%/])"
NUM_RE = re.compile(
    _B + r"~?[-+]?\d+(?:\.\d+)?%" + r"(?![\w\-/])"                                   # 34%, ~2%
    + r"|" + _B + r"~?[$€£]\d[\d,]*(?:\.\d+)?(?:\s?(?:K|M|B|bn|million|billion)\b)?(?:/year|/day)?"  # $1.5 billion
    + r"|" + _B + r"\d[\d,]*(?:\.\d+)?\s?(?:million|billion|thousand)\b\+?"                 # 1 million+
    + r"|" + _B + r"\d+(?:st|nd|rd|th)\b"                                                      # 3rd
    + r"|" + _B + r"\d{1,3}(?:,\d{3})+" + _E                                                  # 10,000
    + r"|" + _B + r"\d+\.\d+" + _E                                                           # 4.3, 33.7
    + r"|[¼½¾⅓⅔]")
ORD_RE = re.compile(r"^(\d+)(st|nd|rd|th)$")


def colx24(k: int) -> float:
    return LEFT + k * (CW + GUT)


def span24(a: int, b: int) -> tuple[float, float]:
    return colx24(a), colx24(b) + CW - colx24(a)


def colx(k: int) -> float:
    """Left edge of 12-column unit k."""
    return colx24(2 * k)


def span(a: int, b: int) -> tuple[float, float]:
    """(x, width) covering 12-column units a..b inclusive."""
    return span24(2 * a, 2 * b + 1)


# ------------------------------------------------------------------ rich text
def _set_font(run, size, color=TC.TEXT_1, bold=False, italic=False, heading=False, sup=False):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.theme_color = color
    rpr = run._r.get_or_add_rPr()
    if sup:
        rpr.set("baseline", "30000")
    old = rpr.find(qn("a:latin"))
    if old is not None:
        rpr.remove(old)
    rpr.append(rpr.makeelement(qn("a:latin"), {"typeface": "+mj-lt" if heading else "+mn-lt"}))


def _segments(text: str, numbers: bool, base: str = "plain"):
    """(str, style): **x** bold (num if x is a number phrase), ^x superscript, bare number phrases num."""
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            inner = part[2:-2]
            whole = numbers and re.search(r"\d|[¼½¾]", inner)    # a bold phrase carrying a number is an emphasis
            out += _segments(inner, False, "num" if whole else "bold")
            continue
        for chunk in re.split(r"(\^\d{1,2}\b|\^[a-z]\b)", part):     # footnote marks only (^1, ^a), not ^TNX
            if not chunk:
                continue
            if chunk.startswith("^"):
                out.append((chunk[1:], "sup_" + base))
                continue
            if not numbers:
                out.append((chunk, base))
                continue
            last = 0
            for m in NUM_RE.finditer(chunk):
                if m.start() > last:
                    out.append((chunk[last:m.start()], base))
                om = ORD_RE.match(m.group(0))
                if om:
                    out += [(om.group(1), "num"), (om.group(2), "sup_num")]
                else:
                    out.append((m.group(0), "num"))
                last = m.end()
            if last < len(chunk):
                out.append((chunk[last:], base))
    return out


MINUS = re.compile(r"(?<![\w.\-/])-(?=\$?\d)")             # "-38%" is a minus, not a hyphen (typeset as U+2212)
ACCENTS_MAX = 2                                         # marked phrases per paragraph: more is a highlighter, not emphasis


def _rich(p, text, size, color=TC.TEXT_1, bold=False, numbers=True, heading=False, italic=False, accent=True):
    text = MINUS.sub("\u2212", str(text)).replace(" -> ", " to ").replace("->", " to ")   # "18% -> 11%" reads "18% to 11%" (an arrow glyph falls back to
    # another face in the house font and overlaps)
    marked = 0
    for seg, st in _segments(text, numbers):
        if st.endswith("bold") and not heading and color in (TC.TEXT_1, None):
            marked += 1
            if marked > ACCENTS_MAX:
                st = "sup_" if st.startswith("sup_") else "plain"
        r = p.add_run()
        r.text = seg
        sup = st.startswith("sup_")
        st = st[4:] if sup else st
        if st == "num":       # numbers are plain text: a deck where every figure is blue or bold reads as machine-made;
            if NUMBER_ACCENT:  # emphasis is the one **phrase** the writer marks
                _set_font(r, size, TC.ACCENT_1, True, italic, heading, sup)
            else:
                _set_font(r, size, color, bold, italic, heading, sup)
        elif st == "bold":
            if not heading and color in (TC.TEXT_1, None) and accent:   # a marked phrase is set in
                _set_font(r, size, TC.ACCENT_1, False, italic, heading, sup)   # the accent colour, not in bold
            else:
                _set_font(r, size, color, True, italic, heading, sup)
        else:
            _set_font(r, size, color, bold, italic, heading, sup)


def _bullet(p, level=0, color="accent1"):
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(int(Inches(0.2 + 0.2 * level))))
    pPr.set("indent", str(int(-Inches(0.2))))
    clr = pPr.makeelement(qn("a:buClr"), {})
    clr.append(clr.makeelement(qn("a:schemeClr"), {"val": color}))
    pPr.append(clr)
    pPr.append(pPr.makeelement(qn("a:buSzPct"), {"val": "90000"}))
    pPr.append(pPr.makeelement(qn("a:buFont"), {"typeface": FACE["body"]}))
    pPr.append(pPr.makeelement(qn("a:buChar"), {"char": "•"}))   # a small round accent dot


def _box(sl, x, y, w, h, name, anchor=MSO_ANCHOR.TOP, pad=0.0):
    tb = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    bp = tf._txBody.find(qn("a:bodyPr"))
    for c in list(bp):
        bp.remove(c)
    tf.margin_left = tf.margin_right = Inches(pad)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    tf.vertical_anchor = anchor
    return tb


def _shape_text(shp, blocks, ctx, n, area, anchor=MSO_ANCHOR.TOP):
    """Write the body into the shape itself (its own text frame, insets set so the text sits in `area`), so a person
    editing the deck edits one object, not a text box floating on a box."""
    sx, sy = shp.left / 914400, shp.top / 914400
    sw, sh = shp.width / 914400, shp.height / 914400
    x, y, w, h = area
    tf = shp.text_frame
    tf.word_wrap = True
    bp = tf._txBody.find(qn("a:bodyPr"))
    for c in list(bp):
        bp.remove(c)
    for k in list(bp.attrib):
        del bp.attrib[k]
    tf.margin_left, tf.margin_top = Inches(max(0.0, x - sx)), Inches(max(0.0, y - sy))
    tf.margin_right, tf.margin_bottom = Inches(max(0.0, sx + sw - x - w)), Inches(max(0.0, sy + sh - y - h))
    tf.vertical_anchor = anchor
    for b in blocks:
        b.setdefault("align", PP_ALIGN.LEFT)
    _write(shp, blocks, ctx, n)
    return shp


def _write(tb, blocks, ctx=None, n=None):
    """blocks: dicts {text, size, color, bold, bullet, level, space_before, align, italic, numbers, heading, prov}."""
    tf = tb.text_frame
    for i, b in enumerate(blocks):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        if b.get("bullet"):
            _bullet(p, b.get("level", 0))
        if b.get("align") is not None:
            p.alignment = b["align"]
        if b.get("space_before"):
            p.space_before = Pt(b["space_before"])
        p.line_spacing = Pt(_pitch(b.get("size", T["body"]), b.get("line_spacing", LEADING)))
        _rich(p, b["text"], b.get("size", T["body"]), b.get("color", TC.TEXT_1), b.get("bold", False),
              b.get("numbers", True), b.get("heading", False), b.get("italic", False))
        if ctx is not None and n is not None:
            ctx.track(n, b["text"], b.get("prov"))
    return tb


def _strip_style(shape):
    st = shape._element.find(qn("p:style"))
    if st is not None:
        shape._element.remove(st)


def _rule(sl, x, y, w, color=TC.ACCENT_5, weight=0.75, name="Rule"):
    c = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x), Inches(y), Inches(x + w), Inches(y))
    c.name = name
    _strip_style(c)
    c.line.color.theme_color = color
    c.line.width = Pt(weight)
    return c


def _fill(sl, x, y, w, h, color, name, shape=MSO_SHAPE.RECTANGLE):
    s = sl.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    s.name = name
    _strip_style(s)
    s.fill.solid()
    s.fill.fore_color.theme_color = color
    s.line.fill.background()
    return s


def _alt(shape, text):
    shape._element.find(".//" + qn("p:cNvPr")).set("descr", text[:250])


ICON_MEANINGS = {"safety": "shield-check", "congestion": "traffic-cone", "traffic": "traffic-cone", "equity": "scale",
                 "access": "accessibility", "curbside": "store", "curb": "store", "parking": "square-parking",
                 "ridership": "bus", "transit": "train-front", "health": "heart-pulse", "money": "circle-dollar-sign",
                 "funding": "circle-dollar-sign", "cost": "circle-dollar-sign", "people": "users", "workforce": "users",
                 "growth": "trending-up", "decline": "trending-down", "risk": "triangle-alert", "time": "clock",
                 "ai": "cpu", "technology": "cpu", "deal": "handshake", "deals": "handshake", "scale": "maximize",
                 "scope": "layers", "capital": "landmark", "valuation": "gauge", "diligence": "search-check",
                 "integration": "puzzle", "region": "globe", "regions": "globe", "industry": "factory"}


ICON_ALIASES = {"money": "banknote", "revenue": "banknote", "cost": "receipt", "growth": "trending-up",
                "decline": "trending-down", "customer": "users", "customers": "users", "people": "users",
                "team": "users", "risk": "shield-alert", "time": "clock", "calendar": "calendar", "market": "store",
                "retail": "shopping-cart", "data": "database", "analysis": "chart-column", "chart": "chart-column",
                "target": "target", "goal": "target", "idea": "lightbulb", "strategy": "compass",
                "partner": "handshake", "partners": "handshake", "law": "scale", "legal": "scale",
                "location": "map-pin", "map": "map", "country": "globe", "global": "globe", "process": "workflow",
                "build": "hammer", "factory": "factory", "product": "package", "delivery": "truck",
                "logistics": "truck", "service": "headset", "support": "headset", "energy": "zap",
                "heat": "thermometer-sun", "price": "tag", "check": "circle-check", "question": "circle-help",
                "warning": "triangle-alert", "security": "shield-check", "search": "search",
                "digital": "monitor-smartphone", "phone": "smartphone", "governance": "landmark",
                "finance": "landmark", "investment": "piggy-bank", "phase": "milestone", "launch": "rocket",
                "plan": "list-checks", "rival": "swords", "rivals": "swords", "competitor": "swords",
                "competitors": "swords", "competition": "swords", "competitive": "swords", "recommend": "lightbulb",
                "recommendation": "lightbulb", "recommendations": "lightbulb", "next": "arrow-right",
                "opportunity": "sparkles", "opportunities": "sparkles", "summary": "file-text", "overview": "file-text",
                "operations": "settings", "operating": "settings", "action": "list-checks", "actions": "list-checks",
                "stand": "map-pin", "today": "map-pin", "situation": "map-pin", "current": "map-pin",
                "future": "telescope", "outlook": "telescope", "forecast": "telescope", "roadmap": "map",
                "implementation": "rocket", "execution": "rocket", "sales": "chart-column", "performance": "gauge",
                "financial": "landmark", "financials": "landmark", "budget": "wallet", "win": "trophy",
                "position": "compass", "options": "git-fork", "option": "git-fork", "case": "briefcase",
                "approach": "route", "method": "route", "methodology": "route", "findings": "search-check",
                "insights": "lightbulb", "context": "book-open", "background": "book-open"}


@lru_cache(maxsize=1)
def _icon_names() -> tuple:
    return tuple(sorted(p.stem for p in ICONS.glob("*.svg")))


def icon_name(key: str | None) -> str:
    """The Lucide icon for a meaning or name: deck meanings first, then aliases, then the closest icon name."""
    k = (key or "").strip().lower().replace("_", "-").replace(" ", "-")
    for w in re.split(r"[-\s]+", k):
        if w in ICON_MEANINGS and (ICONS / f"{ICON_MEANINGS[w]}.svg").exists():
            return ICON_MEANINGS[w]
    names = _icon_names()
    if k in names:
        return k
    if k in ICON_ALIASES:
        return ICON_ALIASES[k]
    words = [w for w in re.split(r"[^a-z0-9]+", k) if len(w) > 2]
    for w in words:                                           # a meaning word anywhere in a phrase ("how rivals respond")
        for v in (w, w[:-1] if w.endswith("s") else w):
            if v in ICON_MEANINGS and (ICONS / f"{ICON_MEANINGS[v]}.svg").exists():
                return ICON_MEANINGS[v]
            if v in ICON_ALIASES:
                return ICON_ALIASES[v]
    for w in words:
        hits = [n for n in names if n == w or n.startswith(w + "-")]
        if hits:
            return min(hits, key=len)
    return "circle-dot"


@lru_cache(maxsize=256)
def _icon_png(name: str, hexcolor: str) -> str | None:
    """The icon as a PNG in the colour asked for. Masks pre-rendered from the Lucide SVGs ship with the app
    (engines/slides/icons_png) and are coloured with Pillow: the Cairo library the SVGs need is not on Mac or
    Windows, and decks there came out with no icons at all."""
    nm = icon_name(name)
    ICON_CACHE.mkdir(exist_ok=True)
    out = ICON_CACHE / f"{nm}-{hexcolor}.png"
    if out.exists():
        return str(out)
    mask = ICONS.with_name("icons_png") / f"{nm}.png"
    if mask.exists():
        from PIL import Image
        a = Image.open(mask).convert("L")
        im = Image.new("RGBA", a.size, "#" + hexcolor)
        im.putalpha(a)
        im.save(out)
        return str(out)
    src = ICONS / f"{nm}.svg"
    if not src.exists():
        return None
    try:
        import cairosvg
    except Exception:  # noqa: BLE001
        return None
    if not out.exists():
        svg = src.read_text().replace("currentColor", f"#{hexcolor}").replace('stroke-width="2"', 'stroke-width="1.25"')
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out), output_width=192, output_height=192)
    return str(out)


ICON_SET = {"name": None}       # the deck's icon set (render(icon_set=...) or theme["icon_set"]); None: Lucide, PNG
SVG_BLIP_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"
ASVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"


def _set_icons(icon_set: str | None) -> str | None:
    """Check and apply the deck's icon set (any set of engines/icons); None keeps the Lucide PNG default."""
    s = (str(icon_set).strip().lower() or None) if icon_set else None
    if s:
        from engines.icons import SETS
        if s not in SETS:
            raise ValueError(f"Unknown icon set {icon_set!r}. Sets: {', '.join(SETS)}")
    ICON_SET["name"] = s
    return s


@lru_cache(maxsize=512)
def icon_ref(key: str | None, icon_set: str | None = None) -> tuple[str, str]:
    """(set, icon name) for a meaning in one set. Lucide: icon_name() (deck meanings, aliases, closest name). Other
    sets: the name itself when the set has it (for a deck meaning, when the Lucide icon is tagged with it too, so
    "scale" keeps meaning "maximize"); otherwise the library's search for the
    meaning together with the Lucide icon it maps to and that icon's tags, so "risk" finds each set's warning sign
    and "congestion" its traffic cone."""
    s = icon_set or "lucide"
    lucide = icon_name(key)
    if s == "lucide":
        return "lucide", lucide
    from engines import icons as IC
    k = (key or "").strip().lower().replace("_", "-").replace(" ", "-")
    own = IC._lookup(k, s) if k else None
    tags = next((r["tags"] for r in IC.index("lucide") if r["name"] == lucide), [])
    remapped = any(w in ICON_MEANINGS or w in ICON_ALIASES for w in k.split("-"))
    if own and (not remapped or k == lucide or k in tags):     # "money" -> the set's own "money" icon
        return s, own
    query = " ".join([k.replace("-", " "), lucide.replace("-", " ")] + list(tags[:6]))
    words = set(IC._words(query))
    subject = set(IC._words(" ".join([lucide.replace("-", " ")] + list(tags[:6])))) if remapped else words

    def fit(r):                                    # the icon's subject (first name word) should be in the query
        nw = IC._words(r["name"].replace("-", " "))
        return (r["score"] + (4 if nw and nw[0] in subject else 0) + (5 if r["name"] == lucide else 0)
                - 2 * sum(1 for w in nw if w not in words))
    hits = IC.search(query, set=s, limit=10) or IC.search("circle dot", set=s, limit=1)
    return s, (max(hits, key=fit)["name"] if hits else (own or ""))


@lru_cache(maxsize=256)
def _set_png(name: str, hexcolor: str, icon_set: str) -> str | None:
    """PNG fallback for an icon of another set, drawn with Pillow (engines/icons/raster.py: no Cairo, so it works on
    Mac and Windows too)."""
    from engines.icons.raster import png_bytes
    svg = _icon_svg(name, hexcolor, icon_set)
    if not svg:
        return None
    ICON_CACHE.mkdir(exist_ok=True)
    out = ICON_CACHE / f"{icon_set}-{name}-{hexcolor}.png"
    if not out.exists():
        tmp = out.with_suffix(".tmp.png")
        tmp.write_bytes(png_bytes(svg.decode("utf-8"), hexcolor, 192))
        tmp.replace(out)
    return str(out)


@lru_cache(maxsize=256)
def _icon_svg(name: str, hexcolor: str, icon_set: str) -> bytes | None:
    """The icon's SVG in `hexcolor` (PowerPoint does not resolve currentColor), 2px strokes thinned to 1.25px like
    the Lucide PNGs."""
    from engines import icons as IC
    txt = IC.svg(name, icon_set)
    if not txt:
        return None
    txt = txt.replace("currentColor", f"#{hexcolor}").replace('stroke-width="2"', 'stroke-width="1.25"')
    if "<?xml" not in txt[:10]:
        txt = '<?xml version="1.0" encoding="UTF-8"?>' + txt
    return txt.encode("utf-8")


def _embed_svg(pic, blob: bytes) -> None:
    """Add the SVG beside the PNG (a:blip/a:extLst/asvg:svgBlip, how PowerPoint stores an SVG picture): PowerPoint
    2016+ draws the crisp, recolourable SVG; older readers and LibreOffice draw the PNG. One part per distinct SVG."""
    from lxml import etree
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.opc.package import Part
    slide_part = pic.part
    pkg = slide_part.package
    seen = pkg.__dict__.setdefault("_forge_svg_parts", {})
    part = seen.get(blob)
    if part is None:
        part = Part(pkg.next_partname("/ppt/media/image%d.svg"), "image/svg+xml", pkg, blob)
        seen[blob] = part
    rid = slide_part.relate_to(part, RT.IMAGE)
    blip = pic._element.find(".//" + qn("a:blip"))
    ext_lst = blip.find(qn("a:extLst"))
    if ext_lst is None:
        ext_lst = etree.SubElement(blip, qn("a:extLst"))
    ext = etree.SubElement(ext_lst, qn("a:ext"), uri=SVG_BLIP_URI)
    el = etree.SubElement(ext, f"{{{ASVG}}}svgBlip", nsmap={"asvg": ASVG})
    el.set(qn("r:embed"), rid)


SLOT_TC = {"dk1": TC.TEXT_1, "lt1": TC.BACKGROUND_1, "dk2": TC.TEXT_2, "lt2": TC.BACKGROUND_2, "accent1": TC.ACCENT_1,
           "accent2": TC.ACCENT_2, "accent3": TC.ACCENT_3, "accent4": TC.ACCENT_4, "accent5": TC.ACCENT_5,
           "accent6": TC.ACCENT_6}


def icon_svg_text(name: str, iset: str) -> str | None:
    """The icon's SVG source (Lucide from the bundled files, other sets from engines/icons)."""
    if iset == "lucide":
        src = ICONS / f"{name}.svg"
        if src.exists():
            return src.read_text()
    from engines import icons as IC
    return IC.svg(name, iset)


def draw_icon(sl, key, x, y, d, color, meaning="", shape_name=None, icon_set=None):
    """A line icon as one native vector shape (engines/forge/vector.py) in a theme colour: no picture, editable, and
    it recolours with the theme. Alt text icon:<set>:<name>:<meaning> (P9 counts icons by it). None when the set has
    no such icon."""
    from .vector import add_svg
    chosen = icon_set if icon_set is not None else ICON_SET.get("name")
    iset, nm = icon_ref(key, chosen)
    text = icon_svg_text(nm, iset) if nm else None
    if not text:
        return None
    label = f"icon:lucide:{key}:{meaning or key}" if chosen is None else f"icon:{iset}:{nm}:{meaning or key}"
    return add_svg(sl, text, x, y, d, d, shape_name or f"ov_Icon_{key}", color, alt=label, stroke_scale=0.625)


def _icon(sl, name, x, y, d, meaning="", muted=False):
    """Line icon (Lucide, ISC; or the deck's icon set) in the accent colour, drawn as a native vector shape, with no
    ring around it (a ring per icon reads as a template); muted draws it in the rule grey (an agenda's other
    sections)."""
    pad = d * 0.1
    draw_icon(sl, name, x + pad, y + pad, d - 2 * pad, TC.ACCENT_5 if muted else TC.ACCENT_1, meaning,
              f"ov_Icon_{name}")


FACE = {"head": "Segoe UI", "body": "Segoe UI"}
METRIC = (("Segoe UI", "Selawik"), ("Segoe UI Semibold", "Selawik"), ("Georgia", "Gelasio"), ("Arial", "Liberation Sans"), ("Calibri", "Carlito"), ("Cambria", "Caladea"),
          ("Times New Roman", "Liberation Serif"), ("Helvetica", "Liberation Sans"))


def _font(role: str, bold: bool = False):
    from .common import font_file
    return font_file(FACE[role], bold, False, METRIC)[0]


def _need(blocks, w, safety: float = 0.98) -> float:
    """Height the blocks need at their set sizes (Arial metrics via Liberation Sans). Rows ruled underneath measure
    with safety 0.94: the renderer can set a line about 5% wider than measured, and a wrap there crosses the rule."""
    from .common import font_file, wrap_lines
    reg, bold = _font("body"), _font("body", True)
    h = 0.05
    for b in blocks:
        size = b.get("size", T["body"])
        ind = 0.2 + 0.2 * b.get("level", 0) if b.get("bullet") else 0
        plain = re.sub(r"\*\*|\^\w+", "", b["text"])
        lines = wrap_lines(plain, w - ind, size, bold if b.get("bold") else reg, safety)
        h += lines * _pitch(size, b.get("line_spacing", LEADING)) / 72 + (b.get("space_before") or 0) / 72
    return h


@lru_cache(maxsize=16)
def _line_factor(path) -> float:
    """A table row's height per point of type for this face: 1.15 measured for Arial (natural line 1.118 em), scaled
    by the face's own ascent + descent, so a taller face such as Segoe UI gets taller rows instead of overflowing."""
    try:
        from PIL import ImageFont
        a, d = ImageFont.truetype(str(path).split("|")[0], 1000).getmetrics()
        return max(1.15, round(1.15 * (a + d) / 1000 / 1.118, 3))
    except Exception:  # noqa: BLE001
        return 1.15


def _cont_title(t: str) -> str:
    head = t.split(":")[0] if ":" in t and len(t.split(":")[0]) < 60 else " ".join(t.split()[:8])
    return head.rstrip(" .…") + " (continued)"


def _balanced(text: str, width: float, size: float) -> tuple[str, str]:
    """(line 1, line 2) for a title that wraps to two lines with one or two words left on the second: broken where the
    two lines are closest in width. ("text", "") when it fits one line or already wraps evenly."""
    from .common import wrap_lines
    f = _font("head", True)
    words = text.split()
    if len(words) < 4 or wrap_lines(text, width, size, f, 0.97) != 2:
        return text, ""
    best = None
    for k in range(2, len(words) - 1):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        if wrap_lines(a, width, size, f, 0.97) > 1 or wrap_lines(b, width, size, f, 0.97) > 1:
            continue
        d = abs(_need_w(a, size, True) - _need_w(b, size, True))
        if best is None or d < best[0]:
            best = (d, a, b)
    if best is None:
        return text, ""
    natural_tail = len(words) - next((k for k in range(len(words), 0, -1)
                                      if wrap_lines(" ".join(words[:k]), width, size, f, 0.97) == 1), len(words))
    return (best[1], best[2]) if natural_tail <= 2 else (text, "")


def _title_size(text: str, width: float, lines_max: int = 2):
    """25 pt, then 22, then 20: the largest size at which the title fits in two lines (Georgia Bold metrics)."""
    from .common import font_file, wrap_lines
    f = _font("head", True)
    for size in (T["title"], 22, 20):
        if wrap_lines(text, width - 0.2, size, f, 0.97) <= lines_max:
            return size
    return None


def _fit(blocks, w, h, floor):
    """Shrink every block by 0.5 pt steps (keeping relative sizes) until it fits or reaches `floor`.
    Returns (blocks, fits). The renderer never lets text run past its box silently: a miss is reported."""
    import copy
    for step in range(0, 5):
        bl = copy.deepcopy(blocks)
        for b in bl:
            b["size"] = max(floor, b.get("size", T["body"]) - step)
        if _need(bl, w) <= h:
            return bl, True
    return bl, False


# ------------------------------------------------------------------ frame
class Ctx:
    def __init__(self, prov):
        self.prov = prov
        self.overflow = []
        self.last_side = -9          # last slide whose chart had its text in a side panel (composition variety)
        self.last_arg = (-9, "")      # last text-led slide and its form (columns or rows): neighbours never share one

    def miss(self, n, where, words):
        self.overflow.append(f"slide {n}: {where} does not fit at the minimum size; cut about {words} words")

    def track(self, n, text, prov):
        ids = [p for p in (prov or []) if p]
        if not text or not ids:
            return
        for raw, _, _ in number_tokens(re.sub(r"\*\*|\^\w+", "", text)):     # the extractor P13 reads with
            self.prov.setdefault(str(n), []).append({"text": raw, "prov": ids[0]})


def _header(ctx, s, sl, n, title_w=None):
    t = sl.shapes.title
    sub = bool(s.get("subtitle"))
    band = FRAME["band"]
    tracker = str(s.get("tracker") or "").strip()[:3] if band else ""
    secno = s["_section"].split(" | ")[0] if (not band and s.get("_section") and title_w is None) else ""
    if (tracker or secno) and title_w is None:         # the section number sits in the top right corner
        title_w = span24(0, 21)[1]                     # ends on a column edge (P7)
    # always write the full geometry: setting one dimension on an inherited placeholder resets the others
    t.left, t.width = Inches(LEFT), Inches(title_w or (RIGHT - LEFT))
    if band:
        from .template import BAND_H
        t.top, t.height = Inches(0.12), Inches(BAND_H - 0.2)
    else:
        t.top, t.height = (Inches(0.2), Inches(0.86)) if sub else (Inches(0.42), Inches(0.92))
    tf = t.text_frame
    tf.text = ""
    if not band:                                       # the title starts on the body's left edge, not inset from it
        tf.margin_left = 0                             # (the right inset stays: a wide face sets past it)
    title = re.sub(r"\*\*", "", s["title"])
    tsize = _title_size(title, title_w or (RIGHT - LEFT))
    if tsize is None:
        ctx.miss(n, "the title", max(3, len(title.split()) // 4))
        tsize = 20
    if band:                                           # white on the band; "Section | message" in two tones
        head, sep, rest = title.partition(" | ")
        if sep and 1 <= len(head.split()) <= 4:
            _rich(tf.paragraphs[0], head + " | ", tsize, TC.ACCENT_3, True, numbers=False, heading=True)
            title = rest
        _rich(tf.paragraphs[0], title, tsize, TC.BACKGROUND_1, True, numbers=False, heading=True)
        if tracker:
            _write(_box(sl, RIGHT - 0.6, 0.12, 0.6, 0.5, "ov_Tracker", MSO_ANCHOR.TOP),
                   [{"text": tracker, "size": 28, "bold": True, "numbers": False, "color": TC.ACCENT_3,
                     "align": PP_ALIGN.RIGHT}])
    else:
        first, second = _balanced(title, (title_w or (RIGHT - LEFT)) - 0.2, tsize)
        _rich(tf.paragraphs[0], first + (" " if second else ""), tsize, TC.TEXT_1, True, numbers=False, heading=True)
        if second:                                     # two balanced lines, never one word left on the second
            tf.paragraphs[0].add_line_break()
            _rich(tf.paragraphs[0], second, tsize, TC.TEXT_1, True, numbers=False, heading=True)
        # never an eyebrow (a small label over the title): the user's standing rule. The section shows as its
        # number in the top right corner only.
        if secno:                                       # the section number, large, on the title's line, right
            ty_ = (t.top + t.height) / 914400            # the title's foot (it sits on its rule)
            _write(_box(sl, RIGHT - 0.9, ty_ - 0.62, 0.9, 0.6, "ov_SectionNo", MSO_ANCHOR.BOTTOM),
                   [{"text": secno, "size": 26, "bold": True, "numbers": False, "color": TC.ACCENT_1,
                     "align": PP_ALIGN.RIGHT, "heading": True}])
    ctx.track(n, s["title"], s.get("title_prov"))
    w = title_w or (RIGHT - LEFT)
    if s.get("subtitle"):                             # one line: 18 pt, stepping down to 14 pt
        sub_t = s["subtitle"]
        one = lambda t, z: _need([{"text": t, "size": z}], w * 0.86) <= z * 1.2 / 72 + 0.1  # noqa: E731  margin:
                                                       # the renderer's face can set a little wider than measured
        ssz = next((z for z in (T["sub"], 16, 15, 14) if one(sub_t, z)), 14)
        if not one(sub_t, ssz):                       # two lines would run up into the title: keep the head of the
            head = None                               # label (the unit): at a colon or dash first, then a comma
            for pat in (r"\.\s", r"\s*(?::|;|\s[–—-]\s)", r"\s*(?:(?<!\d),|,(?!\d)|\s\()"):   # sentence first;
                # never inside a number ($7,500)
                for c in reversed([m.start() for m in re.finditer(pat, sub_t)]):
                    h_ = sub_t[:c].rstrip(" ,;:")
                    if h_.count("(") == h_.count(")") and one(h_, 14):
                        head = h_
                        break
                if head:
                    break
            if head and len(head.split()) >= 2:
                s["notes"] = (s.get("notes", "") + "\n[QA] subtitle shortened to one line; in full: " + sub_t).strip()
                sub_t = head
                ssz = next((z for z in (T["sub"], 16, 15, 14) if one(sub_t, z)), 14)
        _write(_box(sl, LEFT, 1.24 if band else SUB_Y, w, 0.3 if band else 0.33, "role_subtitle_Subtitle",
                    MSO_ANCHOR.BOTTOM), [{"text": sub_t, "size": min(ssz, 16) if band else ssz, "numbers": False,
                                         "bold": band}])
        if ssz == 14 and _need([{"text": sub_t, "size": 14}], w) > 0.35:
            ctx.miss(n, "the subtitle", max(3, len(sub_t.split()) // 3))
    if not band:                         # the white frame: an accent rule under the title (or its subtitle)
        _rule(sl, LEFT, TITLE_RULE_Y, (RIGHT - LEFT) if title_w is None or secno else w, TC.ACCENT_1, 1.5,
              "TitleRule")
    x = RIGHT - (1.0 if secno else 0.0)                 # tags sit left of the section number
    for k, tag in enumerate(reversed((s.get("tags") or [])[:2] if not sub else [])):
        tw = 0.1 + 0.083 * len(tag)
        x -= tw
        _write(_box(sl, x, 0.06, tw, 0.24, f"ov_Tag{k}"),
               [{"text": tag, "size": T["tag"], "bold": True, "numbers": False, "align": PP_ALIGN.RIGHT}])
        _rule(sl, x, 0.31, tw, TC.TEXT_1, 0.75, f"ov_TagRule{k}")
        x -= 0.3


_NOTE_MARK = re.compile(r"^\s*(?:\^?\d{1,2}[.):]?|[¹²³⁴⁵⁶⁷⁸⁹]+)\s+(?=\S)")
_NOTE_TAIL = re.compile(r"\s*(?:\^\d{1,2}|[¹²³⁴⁵⁶⁷⁸⁹]+)\s*$")


_META_NOTE = re.compile(r"(?i)not (?:stated|given|provided|available|disclosed|in the (?:sources|facts|evidence))|"
                        r"(?:sources?|facts|evidence) (?:do(?:es)? not|did not|don't) (?:state|say|give|show)|"
                        r"\bis excluded\b|not a stated fact|no (?:external )?source")


def footnote_text(t) -> str:
    """A footnote without the marker the writer typed ("1. ", "^1 ", "¹ ") or a stray one at its end: the footer
    numbers the notes itself."""
    return _NOTE_TAIL.sub("", _NOTE_MARK.sub("", str(t or ""))).strip()


def _notes_h(notes) -> float:
    """Height the footnotes take above the source rule (one line per ~190 characters, 8 pt)."""
    return (0.155 * max(1, (len("    ".join(map(str, notes))) // 170) + 1) + 0.05) if notes else 0.0


def _footer(ctx, s, sl, n):
    notes = [footnote_text(t) for t in s.get("footnotes") or [] if footnote_text(t)]
    meta = [x for x in notes if _META_NOTE.search(x)]     # notes about what the sources lack are for the presenter
    notes = [x for x in notes if x not in meta][:2]       # at most two short notes on the page
    if meta:
        s["notes"] = (s.get("notes", "") + "\n[QA] Note moved off the slide: " + " | ".join(meta)).strip()
    lc = getattr(ctx, "label_col_notes", None)
    ctx.label_col_notes = None
    if notes and lc:                                  # dense matrix: footnotes in the bottom of the label column
        _write(_box(sl, lc[0], FOOT_RULE_Y - 0.06 - 0.14 * len(notes) - 0.05, 2.0, 0.14 * len(notes) + 0.05,
                    "Footnotes", MSO_ANCHOR.BOTTOM),
               [{"text": f"{i + 1}.  {t}", "size": T["note"], "numbers": False} for i, t in enumerate(notes)])
    elif notes:
        top = getattr(ctx, "notes_top", None) or FOOTNOTE_Y
        ctx.notes_top = None
        _write(_box(sl, LEFT, top, RIGHT - LEFT, FOOT_RULE_Y - top - 0.03, "Footnotes", MSO_ANCHOR.BOTTOM),
               [{"text": "    ".join(f"{i + 1}  {t}" for i, t in enumerate(notes)), "size": T["note"], "numbers": False}])
    src = s.get("source") or {}
    if src.get("text"):
        _rule(sl, LEFT, FOOT_RULE_Y, RIGHT - LEFT, TC.TEXT_1, 0.5, "SourceRule")
        from .common import is_rtl as _rtl
        t = (src["text"] if re.match(r"(?i)^(source|note|المصدر|ملاحظة)", src["text"].strip()) else
             ("المصدر: " if _rtl(src["text"]) else "Source: ") + src["text"])
        sx, sw = span(0, 9)
        sb = [{"text": t, "size": T["src"], "numbers": False}]
        if _need(sb, sw) > 0.2:                       # a source is one line (BCG): what will not fit
            parts_ = re.split(r"(?<=[;,])\s+", t)    # (a writer pasting the fact list) goes to the notes, clause by
            keep = parts_[0]                          # clause, never cut mid-word
            for c_ in parts_[1:]:
                if _need([dict(sb[0], text=keep + " " + c_ + " and others")], sw) > 0.2:
                    break
                keep += " " + c_
            rest = t[len(keep):].strip()
            if rest:
                s["notes"] = (s.get("notes", "") + "\n[QA] Source line shortened; the rest: " + rest).strip()
                sb = [dict(sb[0], text=keep.rstrip(" ;,") + " and others")]
        _write(_box(sl, sx, SRC_Y, sw, 0.34, "Source"), sb)
    edges = [colx24(i) + CW for i in range(COLS)]
    snap = lambda x: max([e for e in edges if e <= x + 1e-6] or [colx(10)])  # noqa: E731 - box edges on the grid
    right = snap(BRAND["footer_right"] - 0.1) if BRAND.get("footer_right") else RIGHT   # master artwork there
    lw = _logo(sl, right, SRC_Y + 0.01, 0.24, 1.5, "ov_Logo")
    if lw:                                               # the logo carries the name; the page number sits before it
        right = snap(right - lw - 0.1)
        label = f"{n}"
    else:
        label = f"{n}" if BRAND.get("footer_right") else f"{_firm_label(s)}      {n}"
    x = RIGHT
    for k, tag in enumerate(reversed(getattr(ctx, "stamps", None) or [])):   # the deck's stamp, on every content
        tw = 0.12 + _need_w(tag, T["tag"], True)                               # slide, above the title glyphs
        x -= tw
        _write(_box(sl, x, 0.02, tw, 0.2 if FRAME_TOP else 0.18, f"ov_Tag{k}", MSO_ANCHOR.MIDDLE if FRAME_TOP
                    else MSO_ANCHOR.BOTTOM),
               [{"text": tag, "size": T["tag"], "bold": True, "numbers": False, "align": PP_ALIGN.RIGHT}])
        if not FRAME_TOP:
            _rule(sl, x, 0.21, tw, TC.TEXT_1, 0.75, f"ov_TagRule{k}")
        x -= 0.3 if not FRAME_TOP else 0.12
    if FRAME_TOP and not FRAME["band"]:                 # the deck's frame: a hairline across the top of every content
        _rule(sl, LEFT, 0.12, snap(x - LEFT) if x < RIGHT else RIGHT - LEFT, TC.TEXT_1, 0.75, "FrameTop")
    if right - colx(10) >= 0.3:
        _write(_box(sl, colx(10), SRC_Y, right - colx(10), 0.25, "Firm"),
               [{"text": label, "size": T["src"] + 1, "numbers": False, "align": PP_ALIGN.RIGHT}])


# ------------------------------------------------------------------ charts
def _bubble_drawn(ctx, sl, n, c, x, y, w, h):
    """Native bubble chart (editable data, axes crossing at reference values) with think-cell style labels: a
    collision-free solver picks each label's spot (never over another label, a bubble or the tick labels), and the
    label is the chart's own data label moved there, so it stays attached to its point when the data is edited."""
    pts = c["points"]
    (x0, x1), (y0, y1) = c["x_range"], c["y_range"]
    tick = T["label"]
    title_h = 0.3 if c.get("x_title") else 0.0
    pl, pb = x + 0.55, y + h - 0.32 - title_h             # plot left (after y tick labels), plot bottom (above x ticks)
    pw, ph = x + w - pl - 0.05, pb - y - 0.05
    to = lambda xv, yv: (pl + (xv - x0) / (x1 - x0) * pw, pb - (yv - y0) / (y1 - y0) * ph)
    xc = c.get("x_cross", x0)
    yc = c.get("y_cross", y0)
    ax_x, _ = to(xc, y0)
    _, ax_y = to(x0, yc)
    # one native bubble chart; its plot area is pinned to the geometry the label solver works in
    cd = BubbleChartData()
    se_ = cd.add_series(c.get("series_name", "items"))
    for p in pts:
        se_.add_data_point(p["x"], p["y"], p.get("size", 1))
    h_frame = h - title_h
    gf = sl.shapes.add_chart(XL_CHART_TYPE.BUBBLE, Inches(x), Inches(y), Inches(w), Inches(h_frame), cd)
    positive_axis_ids(gf.chart)
    gf.name = "Chart"
    ch = gf.chart
    ch.has_legend = False
    ch.has_title = False
    ch.font.size = Pt(tick)
    ch.font.color.theme_color = TC.TEXT_1
    plot = ch.plots[0]
    smax = max(p.get("size", 1) for p in pts)
    dmax = c.get("max_diameter", 0.95)
    plot.bubble_scale = int(max(10, min(300, round(dmax / (0.25 * min(pw, ph)) * 100))))
    ser = plot.series[0]
    for i, p in enumerate(pts):
        pt = ser.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.theme_color = TC.ACCENT_1 if p.get("focus") else (TC.ACCENT_5 if p.get("muted") else TC.TEXT_2)
        pt.format.line.color.theme_color = TC.BACKGROUND_1
        pt.format.line.width = Pt(0.75)
    xm, ym = c.get("x_major", (x1 - x0) / 7), c.get("y_major", (y1 - y0) / 7)
    for ax, (lo, hi), major in ((ch.category_axis, (x0, x1), xm), (ch.value_axis, (y0, y1), ym)):
        ax.has_major_gridlines = False
        ax.tick_labels.font.size = Pt(tick)
        ax.format.line.color.theme_color = TC.TEXT_1
        ax.format.line.width = Pt(0.75)
        ax.tick_label_position = XL_TICK_LABEL_POSITION.LOW
        ax.minimum_scale, ax.maximum_scale, ax.major_unit = lo, hi, major
    # python-pptx's crosses_at is "the value on THIS axis where the other axis crosses" (written on the other axis)
    ch.value_axis.crosses_at = yc                            # the x axis crosses the y axis at y = y_cross
    ch.category_axis.crosses_at = xc
    pa = ch._chartSpace.find(".//" + qn("c:plotArea"))
    lay = pa.find(qn("c:layout"))
    if lay is None:
        lay = pa.makeelement(qn("c:layout"), {})
        pa.insert(0, lay)
    for k_ in list(lay):
        lay.remove(k_)
    ml = lay.makeelement(qn("c:manualLayout"), {})
    for tag, val in (("c:layoutTarget", "inner"), ("c:xMode", "edge"), ("c:yMode", "edge"), ("c:x", (pl - x) / w),
                     ("c:y", (pb - ph - y) / h_frame), ("c:w", pw / w), ("c:h", ph / h_frame)):
        ml.append(ml.makeelement(qn(tag), {"val": f"{val:.5f}" if isinstance(val, float) else val}))
    lay.append(ml)
    occupied = [(pl - 0.1, pb, pw + 0.2, 0.3), (x, y, 0.53, h)]     # tick-label bands
    leaders = []
    circles = []
    for i, p in enumerate(pts):
        cx, cy = to(p["x"], p["y"])
        d = max(0.07, dmax * (p.get("size", 1) / smax) ** 0.5)
        circles.append((cx, cy, d / 2))
    for a in c.get("annotations") or []:
        ax_, ay_ = to(a["x"], a["y"])
        _write(_box(sl, ax_ + 0.06, ay_ - 0.12, 1.0, 0.24, "ov_Annot"),
               [{"text": a["text"], "size": tick, "numbers": False, "line_spacing": 1.1}])
        occupied.append((ax_ + 0.06, ay_ - 0.12, 0.7, 0.24))

    def cost(rect, own, own_ok=False):
        bx, by, bw_, bh_ = rect
        c_ = 0.0
        if bx < x or by < y - 0.05 or bx + bw_ > x + w + 0.1 or by + bh_ > pb:
            c_ += 100
        for k_, (ox, oy, ow, oh) in enumerate(occupied):
            ix = min(bx + bw_, ox + ow) - max(bx, ox)
            iy = min(by + bh_, oy + oh) - max(by, oy)
            if ix > 0 and iy > 0:
                c_ += 150 + ix * iy * 100                         # overlapping text or tick labels: never
        for (sx_, sy_, ex_, ey_) in leaders:                      # never sit on an earlier leader line
            for t in (0.2, 0.4, 0.6, 0.8):
                px_, py_ = sx_ + t * (ex_ - sx_), sy_ + t * (ey_ - sy_)
                if bx < px_ < bx + bw_ and by < py_ < by + bh_:
                    c_ += 20
                    break
        for j, (cx_, cy_, r_) in enumerate(circles):
            if j == own and own_ok:
                continue
            nx, ny = min(max(cx_, bx), bx + bw_), min(max(cy_, by), by + bh_)
            dd = ((nx - cx_) ** 2 + (ny - cy_) ** 2) ** 0.5
            if dd < r_ + 0.03:
                c_ += 60 + (r_ + 0.03 - dd) * 80                   # covering a bubble: very bad
        # crossing an axis line is tolerable but discouraged
        if bx < ax_x < bx + bw_:
            c_ += 3
        if by < ax_y < by + bh_:
            c_ += 3
        return c_
    pts = [dict(p) for p in pts]
    for i in range(len(pts)):                                # coincident points share one label
        for j in range(i + 1, len(pts)):
            if pts[i].get("label") and pts[j].get("label") and abs(circles[i][0] - circles[j][0]) < 0.05 \
                    and abs(circles[i][1] - circles[j][1]) < 0.05:
                keep_, drop_ = (i, j) if circles[i][2] >= circles[j][2] else (j, i)
                short = lambda t: t.split(" and ")[0].split(",")[0] if len(t) > 16 else t
                pts[keep_]["label"] = f"{short(pts[keep_]['label'])} / {short(pts[drop_]['label'])}"
                pts[drop_]["label"] = None
    order = sorted(range(len(pts)), key=lambda i: (not pts[i].get("focus"), -pts[i].get("size", 1)))
    size = tick
    for i in order:
        p = pts[i]
        if not p.get("label"):
            continue
        cx, cy, r = circles[i]
        focus = bool(p.get("focus"))
        best, best_score = None, 1e9
        for size in (tick, tick - 1):
            cand, score = _place(p, i, size, focus, cx, cy, r, circles, occupied, leaders, cost)
            if score < best_score - 0.5:
                best, best_score, used = cand, score, size
            if best_score < 4:
                break
        size = used
        pos, bx, by, lw, lh = best
        if __import__("os").environ.get("FORGE_LABEL_DEBUG"):
            print("LABEL", p["label"][:26], pos, size, round(bx, 2), round(by, 2), "score", round(best_score, 1))
        inside = pos == "c"
        color = TC.ACCENT_1 if focus else (TC.BACKGROUND_1 if inside else TC.TEXT_1)
        lsize = size - (2 if inside else 0)
        words, lines_, cur = p["label"].split(), [], ""
        for wd in words:                                   # the solver's line breaks, kept in the native label
            trial = (cur + " " + wd).strip()
            if cur and _need_w(trial, lsize, focus) > lw - 0.16 + 1e-6:
                lines_.append(cur)
                cur = wd
            else:
                cur = trial
        lines_.append(cur)
        dl = ser.points[i].data_label
        dl.has_text_frame = True
        tf_ = dl.text_frame
        for li, line in enumerate(lines_):
            para = tf_.paragraphs[0] if li == 0 else tf_.add_paragraph()
            para.alignment = PP_ALIGN.CENTER
            _rich(para, line, lsize, color, focus, numbers=False)
        bp_ = tf_._txBody.find(qn("a:bodyPr"))
        if bp_ is not None:
            bp_.set("wrap", "none")                         # the solver's line breaks are final: no re-wrapping
        dl.position = XL_LABEL_POSITION.CENTER
        dLbl = dl._dLbl
        lay_ = dLbl.find(qn("c:layout"))
        if lay_ is None:
            lay_ = dLbl.makeelement(qn("c:layout"), {})
            dLbl.insert(1, lay_)
        for k_ in list(lay_):
            lay_.remove(k_)
        mlx = lay_.makeelement(qn("c:manualLayout"), {})
        mlx.append(mlx.makeelement(qn("c:x"), {"val": f"{(bx + lw / 2 - cx) / w:.5f}"}))
        mlx.append(mlx.makeelement(qn("c:y"), {"val": f"{(by + lh / 2 - cy) / h_frame:.5f}"}))
        lay_.append(mlx)
        occupied.append((bx, by, lw, lh))
    if c.get("x_title"):                                   # below the chart frame, not laid over it
        _write(_box(sl, x + w - 3.2, y + h_frame + 0.02, 3.2, 0.26, "XTitle"),
               [{"text": c["x_title"], "size": tick, "numbers": False, "align": PP_ALIGN.RIGHT}])
    data = "; ".join(f"{p.get('label') or 'point'}: x={p['x']}, y={p['y']}, size={p.get('size', 1)}" for p in pts)
    _alt(gf, f"Bubble chart. {data}"[:900])
    return gf


def _place(p, i, size, focus, cx, cy, r, circles, occupied, leaders, cost):
    """Best label rectangle for point i at `size`: returns ((pos, x, y, w, h), score)."""
    one = _need_w(p["label"], size, focus) + 0.16
    words = p["label"].split()
    longest = max(_need_w(wd, size, focus) for wd in words) + 0.16
    cands = []
    inner = _need_w(p["label"], size - 2, focus)              # inside label, two points smaller, white
    if inner < 2 * r * 0.95 and (size - 2) * 1.3 / 72 < r:
        cands.append(("c", cx - r, cy - (size - 1) * 0.65 / 72 - 0.03, 2 * r, (size - 1) * 1.3 / 72 + 0.06))
    for lw in sorted({min(one, 2.4), max(longest, min(one / 2 + 0.35, 2.4)), max(longest, min(one / 3 + 0.4, 2.4))}):
        lines = 1 if lw >= one - 0.01 else (2 if lw >= one / 2 + 0.2 else 3)
        lh = lines * _pitch(size, 1.1) / 72 + 0.06
        for k in range(-30, 21):                      # grid search around the bubble: nearest free spot wins
            for m in range(-16, 17):
                bx_, by_ = cx + k * 0.1, cy + m * 0.08 - lh / 2
                if bx_ < cx < bx_ + lw and by_ < cy < by_ + lh:
                    continue
                pos = "r" if bx_ >= cx else ("l" if bx_ + lw <= cx else ("a" if by_ < cy else "b"))
                cands.append((pos, bx_, by_, lw, lh))

    def dist(cd):
        _, bx_, by_, lw_, lh_ = cd
        nx, ny = min(max(cx, bx_), bx_ + lw_), min(max(cy, by_), by_ + lh_)
        return max(0.0, ((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5 - r)
    def crossing(cd):
        """Leader from the bubble to the label: penalise passing through other bubbles or labels."""
        _, bx_, by_, lw_, lh_ = cd
        if dist(cd) <= 0.15:
            return 0.0
        ex, ey = min(max(cx, bx_), bx_ + lw_), min(max(cy, by_), by_ + lh_)
        pen = 0.0
        vx, vy = ex - cx, ey - cy
        L = vx * vx + vy * vy or 1
        for j, (qx, qy, qr) in enumerate(circles):
            if j == i:
                continue
            t = max(0.0, min(1.0, ((qx - cx) * vx + (qy - cy) * vy) / L))
            if ((cx + t * vx - qx) ** 2 + (cy + t * vy - qy) ** 2) ** 0.5 < qr:
                pen += 8
        for (ax_, ay_, bx_, by_) in leaders:                  # leaders never cross each other
            d1 = (bx_ - ax_) * (cy - ay_) - (by_ - ay_) * (cx - ax_)
            d2 = (bx_ - ax_) * (ey - ay_) - (by_ - ay_) * (ex - ax_)
            d3 = (ex - cx) * (ay_ - cy) - (ey - cy) * (ax_ - cx)
            d4 = (ex - cx) * (by_ - cy) - (ey - cy) * (bx_ - cx)
            if d1 * d2 < 0 and d3 * d4 < 0:
                pen += 25
        for (ox, oy, ow, oh) in occupied[2:]:                # other labels
            for t in (0.25, 0.5, 0.75):
                px_, py_ = cx + t * vx, cy + t * vy
                if ox < px_ < ox + ow and oy < py_ < oy + oh:
                    pen += 8
                    break
        return pen
    def ambiguous(cd):
        _, bx_, by_, lw_, lh_ = cd
        if cd[0] == "c":
            return 0.0
        mine = dist(cd)
        for j, (qx, qy, qr) in enumerate(circles):
            if j == i or (abs(qx - cx) < 0.05 and abs(qy - cy) < 0.05):
                continue
            nx, ny = min(max(qx, bx_), bx_ + lw_), min(max(qy, by_), by_ + lh_)
            if max(0.0, ((nx - qx) ** 2 + (ny - qy) ** 2) ** 0.5 - qr) < mine + 0.1:
                return 25.0
        return 0.0
    best = min(cands, key=lambda cd: cost(cd[1:], i, cd[0] == "c") + 6 * dist(cd) + crossing(cd) + ambiguous(cd)
               + (0.0 if cd[0] == "c" else 0.05))
    score = cost(best[1:], i, best[0] == "c") + 6 * dist(best) + crossing(best) + ambiguous(best)
    return best, score


def _need_w(text, size, bold=False) -> float:
    from .common import font_file, text_width_in
    f = _font("body", bold)
    return text_width_in(re.sub(r"\*\*|\^\w+", "", text), size, f)


_KIND = {"bar": XL_CHART_TYPE.BAR_CLUSTERED, "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
         "stacked_column": XL_CHART_TYPE.COLUMN_STACKED, "stacked_bar": XL_CHART_TYPE.BAR_STACKED,
         "line": XL_CHART_TYPE.LINE_MARKERS, "waterfall": XL_CHART_TYPE.COLUMN_STACKED}


BAR_SLOT_MAX = 0.62        # in per category for horizontal bars: bars stay about 0.4in thick, never slabs


def _chart_box(kind, ncat, nser, x, y, w, h):
    """Size the chart frame to its data: a 3-bar chart is not stretched into slabs across the slide."""
    per = 1 + 0.45 * (max(1, nser) - 1)
    legend = 0.35 if nser > 1 else 0
    if kind in ("bar", "stacked_bar"):
        h = min(h, ncat * BAR_SLOT_MAX * (per if kind == "bar" else 1) + 0.45 + legend)
    return x, y, w, h


def _stands_up(kind, cats) -> bool:
    """A few bars read better as columns standing up, full height (labels wrap under them)."""
    return kind == "bar" and (len(cats) <= 5 and max((len(str(v)) for v in cats), default=0) <= 22
                              or len(cats) <= 4 and max((len(str(v)) for v in cats), default=0) <= 34)


def chart_natural_h(c: dict, w: float) -> float | None:
    """The height a chart's data sets (horizontal bars: one slot per category; a football field: one row per
    method), or None for charts that take the height they are given (columns, lines, waterfalls)."""
    kind = c.get("kind", "column")
    cats = c.get("categories") or []
    nser = len(c.get("series") or [])
    if kind == "range":
        return 0.42 + 0.34 + 0.7 * max(1, len(cats))
    if kind in ("bar", "stacked_bar") and not _stands_up(kind, cats):
        return _chart_box(kind, len(cats), nser, 0, 0, w, 99.0)[3]
    return None


def _gap_for(kind, ncat, nser, w, h):
    """Gap width (% of a bar) that keeps columns at most ~0.95in and bars ~0.45in thick inside a full frame."""
    clustered = kind in ("bar", "column")
    dim = (h if kind in ("bar", "stacked_bar") else w) * 0.85
    slot = dim / max(1, ncat)
    cap = 0.38 if kind in ("bar", "stacked_bar") else 0.7       # slim bars, as in a hand-made exhibit
    group = cap * (max(1, nser) if clustered else 1)
    return int(max(50, min(400, (slot / group - 1) * 100)))


RANGE_PLOT = (0.30, 0.10, 0.66, 0.84)                 # football field: room for method names on the left
OVERLAY = "ov_"                                        # rules.yaml layout.overlap_declare_prefix: placed by the data


def fmt_value(v: float, unit: str = "", decimals: int | None = None) -> str:
    """Banker number style: $24.37, 12.5x, 38.0%, (4.2) for negatives; NM for None."""
    if v is None:
        return "NM"
    u = (unit or "").strip()
    d = decimals if decimals is not None else (2 if u in ("$", "$/sh") else 1)
    body = f"{abs(v):,.{d}f}"
    if u in ("$", "$/sh", "$m", "$mm", "$bn"):
        body = "$" + body + {"$m": "m", "$mm": "mm", "$bn": "bn"}.get(u, "")
    elif u in ("x", "%"):
        body += u
    elif u:
        body += " " + u
    return f"({body})" if v < 0 else body


def _nice_bounds(lo: float, hi: float) -> tuple[float, float]:
    import math
    span_ = max(hi - lo, abs(hi) * 0.05, 1e-9)
    step = 10 ** math.floor(math.log10(span_ / 4))
    for m in (1, 2, 2.5, 5, 10):
        if span_ / (step * m) <= 6:
            step *= m
            break
    a = math.floor((lo - 0.12 * span_) / step) * step
    b = math.ceil((hi + 0.12 * span_) / step) * step
    return (max(0.0, a) if lo >= 0 else a), b


def _range_chart(ctx, sl, n, c, x, y, w, h, name="FootballField"):
    """Football field, drawn the banker's way: one floating bar per method (low to high) as a native stacked bar
    chart (editable), methods sorted by their midpoint (highest first), the low and high written at the bar ends, a
    value axis with ticks under the bars, and every reference price (offer, current price) as a labelled vertical
    line. The axis spans every value, markers included. Returns the frame; its drawn height is `gf.drawn_h`."""
    order = sorted(range(len(c["categories"])), key=lambda j: -(float(c["series"][0]["values"][j])
                                                                + float(c["series"][1]["values"][j])))
    cats = [str(c["categories"][j]) for j in order]
    lows = [float(c["series"][0]["values"][j]) for j in order]
    highs = [float(c["series"][1]["values"][j]) for j in order]
    unit, dec = c.get("unit", "$"), c.get("decimals")
    marks = range_markers(c)
    pts = lows + highs + [m["value"] for m in marks]
    amin, amax = _nice_bounds(min(pts), max(pts))
    head = 0.42 if marks else 0.1                              # marker labels above the bars
    axis = 0.34                                                # tick labels under them
    slot = max(0.5, min(0.85, (h - head - axis) / max(1, len(cats))))
    ph = slot * len(cats)
    h = head + ph + axis
    lab_w = max(_need_w(t, T["label"]) for t in cats) + 0.25   # method names on the left
    px0 = max(0.18, min(0.42, lab_w / w))
    plot = (px0, head / h, 0.97 - px0 - 0.03, ph / h)
    cd = CategoryChartData()
    cd.categories = cats
    cd.add_series("Low", lows)
    cd.add_series("Range", [hi - lo for lo, hi in zip(lows, highs)])
    gf = sl.shapes.add_chart(XL_CHART_TYPE.BAR_STACKED, Inches(x), Inches(y), Inches(w), Inches(h), cd)
    positive_axis_ids(gf.chart)
    gf.name = name
    ch = gf.chart
    ch.has_title = False
    ch.has_legend = False
    ch.font.size = Pt(T["label"])
    ch.font.color.theme_color = TC.TEXT_1
    pa = ch._chartSpace.find(".//" + qn("c:plotArea"))
    lay = pa.find(qn("c:layout"))
    if lay is None:
        lay = pa.makeelement(qn("c:layout"), {})
        pa.insert(0, lay)
    for e in list(lay):
        lay.remove(e)
    ml = lay.makeelement(qn("c:manualLayout"), {})
    for tag, val in (("c:layoutTarget", "inner"), ("c:xMode", "edge"), ("c:yMode", "edge"), ("c:x", round(plot[0], 4)),
                     ("c:y", round(plot[1], 4)), ("c:w", round(plot[2], 4)), ("c:h", round(plot[3], 4))):
        ml.append(ml.makeelement(qn(tag), {"val": str(val)}))
    lay.append(ml)
    pl = ch.plots[0]
    pl.gap_width = 60
    pl.overlap = 100
    va, ca = ch.value_axis, ch.category_axis
    va.minimum_scale, va.maximum_scale = amin, amax
    va.has_major_gridlines = True                              # faint verticals: values read against the axis
    va.major_gridlines.format.line.color.theme_color = TC.ACCENT_5
    va.major_gridlines.format.line.width = Pt(0.5)
    va.visible = True
    va.tick_labels.number_format = ('"$"#,##0' if str(unit).startswith("$") else "#,##0") + \
        {"x": '"x"', "%": '"%"'}.get(str(unit), "")
    va.tick_labels.number_format_is_linked = False
    va.tick_label_position = XL_TICK_LABEL_POSITION.NONE    # the bars carry their values; ticks would meet the
    va.format.line.fill.background()                        # marker labels above the plot
    ca.reverse_order = True
    ca.tick_labels.font.size = Pt(T["label"])
    ca.format.line.color.theme_color = TC.TEXT_1
    base, rng = pl.series
    base.format.fill.background()
    base.format.line.fill.background()
    rng.format.fill.solid()
    rng.format.fill.fore_color.theme_color = TC.ACCENT_4
    hl = {str(v) for v in c.get("highlight_categories") or []}
    for j, cat in enumerate(cats):
        if cat in hl:
            f = rng.points[j].format
            f.fill.solid()
            f.fill.fore_color.theme_color = TC.ACCENT_3
    px = lambda v: x + w * (plot[0] + plot[2] * (v - amin) / (amax - amin))  # noqa: E731
    top = y + head
    band = ph / len(cats)
    pre = OVERLAY                                              # data-placed labels: exempt from the grid snap
    placed = []                                                # marker labels, staggered when they would touch
    for k, mk in enumerate(sorted(marks, key=lambda m: m["value"])):
        mx = px(mk["value"])
        val = fmt_value(mk["value"], unit, dec)
        shown = re.sub(r"[^\d.]", "", val).rstrip(".")
        name_ = mk["label"] or "Reference"
        lab = name_ if shown and shown in re.sub(r"[^\d.]", "", name_) else f"{name_}: {val}"   # never "$48: $48"
        tw = _need_w(lab, T["label"], True) + 0.12
        row = 0 if not placed or all(abs(mx - p_) > (tw + w_) / 2 + 0.05 for p_, w_, r_ in placed if r_ == 0) else 1
        placed.append((mx, tw, row))
        ly = y + (0.0 if row else 0.2) if head > 0.3 else y
        lx = min(max(mx - tw / 2, x), x + w - tw)
        _write(_box(sl, lx, ly, tw, 0.2, f"{pre}MarkerLabel{k + 1}", MSO_ANCHOR.BOTTOM),
               [{"text": lab, "size": T["label"], "bold": True, "align": PP_ALIGN.CENTER, "numbers": False,
                 "color": TC.ACCENT_1 if k == len(marks) - 1 else TC.TEXT_1}], ctx, n)
        ln = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(mx), Inches(ly + 0.21), Inches(mx),
                                     Inches(top + ph + 0.02))
        ln.name = f"{pre}Marker{k + 1}"
        ln.line.color.theme_color = TC.ACCENT_1 if k == len(marks) - 1 else TC.TEXT_1
        ln.line.width = Pt(1.5)
        ln.line.dash_style = 4                                 # dash

    def label(xx, cy, txt, name, right):
        tw = _need_w(txt, T["label"]) + 0.1                    # as wide as its text, opaque: the marker runs behind
        bx = _box(sl, xx - tw if right else xx, cy - 0.12, tw, 0.24, name, MSO_ANCHOR.MIDDLE)
        bx.fill.solid()
        bx.fill.fore_color.theme_color = TC.BACKGROUND_1
        _write(bx, [{"text": txt, "size": T["label"], "align": PP_ALIGN.RIGHT if right else PP_ALIGN.LEFT,
                     "numbers": False}], ctx, n)
    for j, (lo, hi) in enumerate(zip(lows, highs)):
        cy = top + band * (j + 0.5)
        label(px(lo) - 0.04, cy, fmt_value(lo, unit, dec), f"{pre}RangeLow{j + 1}", True)
        label(px(hi) + 0.04, cy, fmt_value(hi, unit, dec), f"{pre}RangeHigh{j + 1}", False)
    _alt(gf, c.get("alt") or "Valuation ranges by method (football field)"
         + (f"; {', '.join(m['label'] for m in marks)} marked" if marks else ""))
    gf.drawn_h = h
    return gf


def _decimals(vals) -> int:
    """Decimals the data needs (0, 1 or 2): 12 -> 0, 12.5 -> 1, 0.25 -> 2."""
    for d in (0, 1):
        if all(abs(v - round(v, d)) < 1e-9 for v in vals):
            return d
    return 2


def _wf_totals(vals: list) -> tuple[set, float]:
    """Which bars of a bridge are totals: the start, the end, and any middle value equal to the running total there
    (a subtotal such as free cash flow between operating cash flow and its uses). Returns (indices, running total
    before the end bar)."""
    tot, run = {0, len(vals) - 1}, float(vals[0]) if vals else 0.0
    for i in range(1, len(vals) - 1):
        v = float(vals[i])
        if abs(v - run) <= max(0.015 * abs(run), 0.05) and abs(v) > 1e-9:
            tot.add(i)                                      # a subtotal: drawn from zero, not added again
        else:
            run += v
    return tot, run


def _waterfall_style(plot, vals, spans, dec, usfx):
    """Totals grey, increases and decreases in their own colours; each bar labelled once with its signed value."""
    base, above, below = plot.series
    base.format.fill.background()
    base.format.line.fill.background()
    totals, _ = _wf_totals(vals)
    for j, v in enumerate(vals):
        total = j in totals
        colr = TC.TEXT_2 if total else (TC.ACCENT_4 if v >= 0 else TC.ACCENT_2)
        lo, hi = spans[j]
        for se in (above, below):
            f = se.points[j].format
            f.fill.solid()
            f.fill.fore_color.theme_color = colr
        num = f"{abs(v):,.{dec}f}{usfx}"
        txt = ("-" + num if v < 0 else num) if total else ("+" if v >= 0 else "-") + num
        lab_se = above if hi > 0 or lo == hi else below
        dl = lab_se.points[j].data_label
        dl.has_text_frame = True
        dl.text_frame.text = txt
        dl.position = XL_LABEL_POSITION.CENTER
        for f in (dl.font, dl.text_frame.paragraphs[0].runs[0].font):   # the rich text run carries its own font
            f.size = Pt(T["label"])
            f.color.theme_color = TC.TEXT_1 if colr == TC.ACCENT_2 else TC.BACKGROUND_1   # dark on the light highlight


def _no_wrap(dl) -> None:
    """A data label stays on one line: LibreOffice wraps "22.20%" to "22.20 / %" unless the label's body says no."""
    try:
        bp = dl._element.get_or_add_txPr().find(qn("a:bodyPr"))
        if bp is not None:
            bp.set("wrap", "none")
    except Exception:  # noqa: BLE001 - styling only
        pass


def _chart(ctx, sl, n, c, x, y, w, h, name="Chart"):
    c = _clean_chart(c)
    if _small_multiples(c):                               # one chart per measure, each on its own scale
        cats, series = [str(v) for v in c["categories"]], c["series"]
        gap = 0.35
        sw = (w - gap * (len(cats) - 1)) / len(cats)
        gf = None
        for j, cat in enumerate(cats):
            sx = x + j * (sw + gap)
            _write(_box(sl, sx, y, sw, 0.3, f"ov_MultipleTitle{j + 1}"),
                   [{"text": cat, "size": T["label"], "bold": True, "numbers": False}])
            vals = [se["values"][j] if j < len(se.get("values") or []) else None for se in series]
            sub = {"kind": "column", "categories": [se.get("name", "") for se in series],
                   "series": [{"name": cat, "values": vals}], "highlight_categories": [series[-1].get("name", "")],
                   "unit": "%" if re.search(r"%|rate|share|accuracy|percent", cat, re.I) and
                   all(isinstance(v, (int, float)) and 0 <= v <= 100 for v in vals) else "",
                   "prov": c.get("prov")}
            if all(isinstance(v, (int, float)) for v in vals) and len(vals) == 2 and vals[0]:
                sub["delta"] = {"from": sub["categories"][0], "to": sub["categories"][1]}
            gf = _chart(ctx, sl, n, sub, sx, y + 0.32, sw, h - 0.32, name=f"{name}_{j + 1}")
        return gf
    if c.get("kind") == "range":
        return _range_chart(ctx, sl, n, c, x, y, w, h)
    if c.get("kind") == "stats":
        return _stats(ctx, sl, n, c, x, y, w, h)
    if c.get("kind") == "bubble":
        return _bubble_drawn(ctx, sl, n, c, x, y, w, h)
    kind = c.get("kind", "column")
    cats = [str(v) for v in c["categories"]]
    series = c["series"]
    if _stands_up(kind, cats):
        kind = "column"                                   # a few short bars read better standing up, full height
    x, y, w, h = _chart_box(kind, len(cats), len(series), x, y, w, h)
    cd = CategoryChartData()
    cd.categories = cats
    allv = [float(v) for se in series for v in se["values"] if isinstance(v, (int, float))]
    dec = c.get("decimals")
    dec = int(dec) if dec is not None else _decimals(allv)
    fmt = "#,##0" + ("." + "0" * dec if dec else "")
    u = str(c.get("unit", "")).strip()
    usfx = {"%": "%", "x": "x", "pp": " pp"}.get(u.lower(), "")
    if usfx:                                                   # the unit rides on the label: 38%
        fmt = f'{fmt}"{usfx}";-{fmt}"{usfx}"'
    if kind == "waterfall":
        # stacked columns: an invisible base, then the visible part above zero and the part below zero. Each bar
        # spans the running total before and after its step (start and end totals span from zero), so steps that
        # take the running total below zero draw where they belong; labels are the signed step values.
        vals = [float(v or 0) for v in series[0]["values"]]
        spans, run = [], 0.0
        totals, _ = _wf_totals(vals)
        for i, v in enumerate(vals):
            if i in totals:                                # start, subtotals and end stand on zero
                spans.append((min(0.0, v), max(0.0, v)))
                run = v if i < len(vals) - 1 else run
            else:
                spans.append((min(run, run + v), max(run, run + v)))
                run += v
        base, above, below = [], [], []
        for lo, hi in spans:
            if lo >= 0:
                base.append(lo), above.append(hi - lo), below.append(None)
            elif hi <= 0:
                base.append(hi), above.append(None), below.append(lo - hi)
            else:                                              # crosses zero: one piece each side
                base.append(None), above.append(hi), below.append(lo)
        cd.add_series("base", base)
        cd.add_series(series[0]["name"], above, fmt)
        cd.add_series(series[0]["name"] + " (below zero)", below, fmt)
    else:
        for se in series:
            cd.add_series(se["name"], [None if v is None else float(v) for v in se["values"]], fmt)
    gf = sl.shapes.add_chart(_KIND.get(kind, XL_CHART_TYPE.COLUMN_CLUSTERED), Inches(x), Inches(y), Inches(w),
                             Inches(h), cd)
    positive_axis_ids(gf.chart)
    gf.name = name
    ch = gf.chart
    ch.has_title = False
    ch.font.size = Pt(T["label"])
    ch.font.color.theme_color = TC.TEXT_1
    plot = ch.plots[0]
    multi = len(series) > 1 and kind != "waterfall"
    ch.has_legend = multi
    if multi:
        ch.legend.position = XL_LEGEND_POSITION.TOP
        ch.legend.include_in_layout = False
    try:
        plot.gap_width = _gap_for(kind, len(cats), len(series), w, h)
        c["_gap"] = plot.gap_width / 100                  # the annotations need each bar's real width
        va = ch.value_axis
        va.has_major_gridlines = False
        va.visible = False
        ca = ch.category_axis
        ca.tick_labels.font.size = Pt(T["label"])
        ca.format.line.color.theme_color = TC.TEXT_1
        if kind in ("bar", "stacked_bar"):
            ca.reverse_order = True
        ca.tick_label_position = XL_TICK_LABEL_POSITION.LOW     # labels stay clear of negative bars
        ends = [e for sp in spans for e in sp] if kind == "waterfall" else allv
        if kind in ("bar", "column", "stacked_bar", "stacked_column", "waterfall") and ends:
            if min(ends) >= 0:                                 # the axis is hidden: bars must start at zero, or
                va.minimum_scale = 0                           # auto-scaling exaggerates the differences
                top_ = max(c.get("_shared_max") or 0, max(ends))   # small multiples on a slide share one scale
                if kind in ("bar", "column") and top_ > 0:        # room past the longest bar for its outside label:
                    lab = max(len(f"{v:,.{dec}f}{usfx}") for v in allv)   # without it the label wraps ("22.20 / %")
                    va.maximum_scale = top_ * (1 + (0.04 + 0.022 * lab if kind == "bar" else 0.12))
            elif max(ends) <= 0:
                va.maximum_scale = 0
    except Exception:
        pass
    hl = {str(v) for v in c.get("highlight_categories") or []}
    palette = [TC.TEXT_2, TC.ACCENT_1, TC.ACCENT_5, TC.ACCENT_4]   # navy, the accent, grey: never three blues
    if kind == "waterfall":
        _waterfall_style(plot, vals, spans, dec, usfx)
        _alt(gf, c.get("alt") or f"waterfall chart of {series[0]['name']} ({c.get('unit', '')})")
        return gf
    for i, s in enumerate(plot.series):
        k = i
        if kind == "line":
            s.format.line.color.theme_color = palette[k % 4]
            s.format.line.width = Pt(2)
            s.smooth = False
        else:
            s.format.fill.solid()
            s.format.fill.fore_color.theme_color = palette[k % 4]
            if not multi and hl:                          # one accent bar, the context in light grey
                s.format.fill.fore_color.theme_color = TC.ACCENT_5
            if not multi:
                vals_k = series[k]["values"]
                for j, cat in enumerate(cats):
                    neg = j < len(vals_k) and isinstance(vals_k[j], (int, float)) and vals_k[j] < 0
                    if cat in hl or neg:
                        # the focus point in the accent; a fall in the pale highlight
                        f = s.points[j].format
                        f.fill.solid()
                        f.fill.fore_color.theme_color = TC.ACCENT_2 if neg and cat not in hl else TC.ACCENT_1
        s.data_labels.show_value = True
        s.data_labels.number_format = fmt
        s.data_labels.number_format_is_linked = False
        s.data_labels.font.size = Pt(T["label"])
        _no_wrap(s.data_labels)
        if kind in ("column", "bar"):                     # clustered too: labels outside, in text colour
            s.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
            s.data_labels.font.color.theme_color = TC.TEXT_1
        elif kind == "line":
            s.data_labels.position = XL_LABEL_POSITION.ABOVE
            s.data_labels.font.color.theme_color = TC.TEXT_1
            vals = [v for v in series[k]["values"]]
            if len(vals) > 12:                            # long series: label the points the story needs, not all
                nums = [(j, v) for j, v in enumerate(vals) if isinstance(v, (int, float))]
                keep = {nums[0][0], nums[-1][0], max(nums, key=lambda t: t[1])[0], min(nums, key=lambda t: t[1])[0]}
                keep |= {j for j, cat in enumerate(cats) if cat in hl}
                s.data_labels.show_value = False
                for j in sorted(keep):
                    dl = s.points[j].data_label
                    dl.show_value = True
                    dl.position = XL_LABEL_POSITION.ABOVE
                    dl.font.size = Pt(T["label"])
                    dl.font.bold = j == nums[-1][0]
        else:
            s.data_labels.font.color.theme_color = TC.BACKGROUND_1
    if kind == "column" and not multi and allv and min(allv) >= 0 and 2 <= len(cats) <= 12:
        try:
            _annotate_columns(ctx, sl, n, gf, c, cats, series[0], x, y, w, h, dec, u)
        except Exception:                                 # noqa: BLE001 - an annotation never breaks the chart
            pass
    _alt(gf, c.get("alt") or f"{kind} chart of {', '.join(se['name'] for se in series)} ({c.get('unit', '')})")
    return gf


_PERIOD = re.compile(r"(?i)^\s*(?:(?:19|20)\d{2}|fy\s?\d{2,4}|q[1-4]|h[12])\b")
_TARGET_CAT = re.compile(r"(?i)\b(target|goal|plan|budget)\b")
PLOT_FR = (0.02, 0.03, 0.96, 0.85)                       # x, y, w, h of an annotated column chart's plot area


def _delta_pair(d, cats, vals):
    """chart "delta": {"from": category, "to": category, "label"?}: the two bars a difference arrow joins."""
    if not isinstance(d, dict):
        return None
    idx = lambda v: (int(v) if isinstance(v, int) and 0 <= v < len(cats)       # noqa: E731
                     else next((j for j, cv in enumerate(cats) if str(cv).strip().lower() == str(v).strip().lower()),
                           None))
    j0, j1 = idx(d.get("from")), idx(d.get("to"))
    if j0 is None or j1 is None or j0 == j1 or not vals[j0] or vals[j0] <= 0:
        return None
    return j0, j1, _txt(d.get("label") or "")


def _delta_label(a: float, b: float, unit: str, dec) -> str:
    """The difference the way a consultant says it: a multiple from 2x, else the change in points or units."""
    r = b / a
    mult = lambda q: f"{q:.0f}x" if q >= 10 else re.sub(r"\.0x$", "x", f"{q:.1f}x")   # noqa: E731
    if r >= 2:
        return mult(r)
    if 0 < r <= 0.5:
        return mult(1 / r) + " lower"
    dv = b - a
    sign = "+" if dv >= 0 else "\u2212"
    if (unit or "").strip() == "%":
        return f"{sign}{abs(dv):.{dec or 0}f} pts"
    return sign + fmt_value(abs(dv), unit, dec)


def _annotate_columns(ctx, sl, n, gf, c, cats, se, x, y, w, h, dec, unit):
    """think-cell's grammar on a single-series column chart, drawn as native shapes over it: a CAGR arrow over a run of
    periods, a dashed target line (chart "target"), and a target category drawn as a dashed outline, not a solid bar.
    The plot area is fixed so each bar's place is known."""
    vals = [float(v) if isinstance(v, (int, float)) else None for v in se["values"]]
    if any(v is None for v in vals):
        return
    k = len(vals)
    periods = k >= 3 and all(_PERIOD.match(cv) for cv in cats) and vals[0] > 0 and vals[-1] > 0 \
        and c.get("cagr", True) is not False
    target = c.get("target") if isinstance(c.get("target"), (int, float)) else None
    tcat = [j for j, cv in enumerate(cats) if _TARGET_CAT.search(cv)]
    if tcat and target is not None:                       # a target bar already shows the goal: no second, dashed
        target = None                                     # line through the labels
    delta = _delta_pair(c.get("delta"), cats, vals)
    desc = [_txt(d) for d in (c.get("descriptors") or [])][:k] if isinstance(c.get("descriptors"), list) else []
    if delta and periods:
        periods = False                                   # one arrow per chart: the asked-for difference wins
    if not (periods or target is not None or tcat or delta or any(desc)):
        return
    top = max([c.get("_shared_max") or 0, max(vals)] + ([target] if target is not None else []))
    M = top * (1.42 if periods else 1.5 if delta else 1.18)   # headroom: an arrow and its pill ride above the
                                                              # tallest label, clear of the chart's heading
    ch = gf.chart
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale = 0, M
    pa = ch._chartSpace.find(".//" + qn("c:plotArea"))
    lay = pa.find(qn("c:layout"))
    if lay is None:
        lay = pa.makeelement(qn("c:layout"), {})
        pa.insert(0, lay)
    for e in list(lay):
        lay.remove(e)
    ml = lay.makeelement(qn("c:manualLayout"), {})
    for tag, val in (("c:layoutTarget", "inner"), ("c:xMode", "edge"), ("c:yMode", "edge"), ("c:x", PLOT_FR[0]),
                     ("c:y", PLOT_FR[1]), ("c:w", PLOT_FR[2]), ("c:h", PLOT_FR[3])):
        ml.append(ml.makeelement(qn(tag), {"val": str(val)}))
    lay.append(ml)
    px, py, pw, ph = x + w * PLOT_FR[0], y + h * PLOT_FR[1], w * PLOT_FR[2], h * PLOT_FR[3]
    dh = 0.0
    if any(desc):                                         # what each category is, in a line or three under its name
        dz = max(9, T["label"] - 2)
        dh = max(_need([{"text": d_, "size": dz}], w / k - 0.12) for d_ in desc if d_) + 0.06
        cat_extra = max(_need([{"text": str(cv), "size": T["label"]}], w / k - 0.1) for cv in cats) - \
            _need([{"text": "A", "size": T["label"]}], 1.0)
        dh += max(0.0, cat_extra) + 0.04                 # a category name that wraps never runs into its descriptor
        dh = min(dh, h * 0.28)
        ph -= dh
        ml.find(qn("c:h")).set("val", str(round(ph / h, 4)))
    if delta and ph > 0.5:                                # a short plot needs room in inches above the tallest
        M = max(M, top / (1 - min(0.6, (1.0 if ph >= 1.8 else 0.8) / ph)))   # label for the arrow and its pill (a
        va.maximum_scale = M                              # scale was not enough on a 2 in chart)
    band = pw / k
    cx = lambda j: px + band * (j + 0.5)                  # noqa: E731
    yv = lambda v: py + ph * (1 - v / M)                  # noqa: E731
    prov = c.get("prov") or getattr(ctx, "slide_prov", None) or ["calc"]   # derived from the chart's sourced data
    for j in tcat:                                        # a target is a dashed outline, not a bar of fact
        f = ch.plots[0].series[0].points[j].format
        f.fill.background()
        f.line.color.theme_color = TC.ACCENT_1
        f.line.width = Pt(1.25)
        f.line.dash_style = 4                             # MSO_LINE.DASH
    if periods:
        n_years = k - 1
        g = (vals[-1] / vals[0]) ** (1 / n_years) - 1
        lab = f"CAGR {'+' if g >= 0 else ''}{g * 100:.1f}%"
        ya = yv(top) - 0.5
        x0, x1 = cx(0), cx(k - 1)
        for i_, (a_, b_) in enumerate((((x0, yv(vals[0]) - 0.36), (x0, ya)), ((x0, ya), (x1, ya)),
                                       ((x1, ya), (x1, yv(vals[-1]) - 0.4)))):      # stops above the value label
            ln = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(a_[0]), Inches(a_[1]), Inches(b_[0]),
                                         Inches(b_[1]))
            ln.name = f"ov_CagrArrow{i_ + 1}"
            ln.line.color.theme_color = TC.TEXT_2
            ln.line.width = Pt(1)
            if i_ == 2:
                end = ln.line._get_or_add_ln()
                end.append(end.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
        bw = _need_w(lab, T["label"], True) + 0.24
        box = _fill(sl, (x0 + x1) / 2 - bw / 2, ya - 0.15, bw, 0.3, TC.BACKGROUND_1, "ov_CagrLabel",
                    MSO_SHAPE.ROUNDED_RECTANGLE)
        box.line.color.theme_color = TC.TEXT_2
        box.line.width = Pt(0.75)
        _shape_text(box, [{"text": lab, "size": T["label"], "bold": True, "numbers": False, "color": TC.TEXT_2,
                           "align": PP_ALIGN.CENTER}], ctx, n, ((x0 + x1) / 2 - bw / 2, ya - 0.15, bw, 0.3),
                    MSO_ANCHOR.MIDDLE)
        ctx.track(n, lab, prov)
    if delta:                                             # think-cell's difference arrow: from one bar's top to the
        j0, j1, lab = delta                               # other's, the multiple or the gap in a pill on the line
        lab = lab or _delta_label(vals[j0], vals[j1], unit, dec)
        bwid = band / (1 + (c.get("_gap") if c.get("_gap") is not None else 0.6)) / 2   # half a bar
        lo, hi = (j0, j1) if vals[j1] >= vals[j0] else (j1, j0)
        side = -1 if j1 > j0 else 1                       # corner to corner: from the first bar's far top corner to
        a_ = (cx(j0) - side * bwid, yv(vals[j0]) - 0.06)  # the other bar's near one, clear of both value labels
        b_ = (cx(j1) + side * bwid, yv(vals[j1]) - 0.06)
        bw = _need_w(lab, T["label"] + 1, True) * 1.15 + 0.4
        between = vals[min(j0, j1): max(j0, j1) + 1]
        if abs(j1 - j0) > 1 and ph < 1.8:                 # a short plot has no room for an elbow: the gap sits in a
            segs = []                                     # pill over the bar it lands on
            mx, my = cx(j1), max(py + 0.17, yv(vals[j1]) - 0.62)
        elif abs(j1 - j0) > 1:                            # bars in between: an elbow above all their labels (the
            ya = max(py + 0.25, yv(max(between)) - 0.5)   # think-cell difference arrow), the pill on its top run
            segs = [((cx(j0), yv(vals[j0]) - 0.34), (cx(j0), ya)), ((cx(j0), ya), (cx(j1), ya)),
                    ((cx(j1), ya), (cx(j1), yv(vals[j1]) - 0.34))]
            mx, my = (cx(j0) + cx(j1)) / 2, ya
        else:
            segs = [(a_, b_)]
            mx = (a_[0] + b_[0]) / 2                      # the pill rides above both bars' value labels, never on
            my = max(py + 0.2, yv(max(vals[j0], vals[j1])) - 0.55)   # them, and never above the plot
        for k_, (p0, p1) in enumerate(segs):
            ln = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(p0[0]), Inches(p0[1]), Inches(p1[0]),
                                         Inches(p1[1]))
            ln.name = "ov_DeltaArrow" + (str(k_ + 1) if len(segs) > 1 else "")
            ln.line.color.theme_color = TC.ACCENT_1
            ln.line.width = Pt(1.25)
            if k_ == len(segs) - 1:
                end = ln.line._get_or_add_ln()
                end.append(end.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
        if target is not None and abs(my - yv(target)) < 0.3:   # the pill never sits on the target line or its
            my = min(my, yv(target)) - 0.32                      # label: it rides above them
        box = _fill(sl, mx - bw / 2, my - 0.17, bw, 0.34, TC.BACKGROUND_1, "ov_DeltaLabel", MSO_SHAPE.OVAL)
        box.line.color.theme_color = TC.ACCENT_1
        box.line.width = Pt(1)
        _shape_text(box, [{"text": lab, "size": T["label"] + 1, "bold": True, "numbers": False,
                           "color": TC.ACCENT_1, "align": PP_ALIGN.CENTER}], ctx, n,
                    (mx - bw / 2, my - 0.17, bw, 0.34), MSO_ANCHOR.MIDDLE)
        box.text_frame.word_wrap = False                  # the multiple stays on one line
        ctx.track(n, lab, prov)
    if any(desc):
        dz = max(9, T["label"] - 2)
        for j, d_ in enumerate(desc):
            if d_:
                _write(_box(sl, px + band * j + 0.04, y + h - dh, band - 0.08, dh, f"ov_CatNote{j + 1}",
                            MSO_ANCHOR.BOTTOM),
                       [{"text": d_, "size": dz, "numbers": False, "color": TC.TEXT_2, "align": PP_ALIGN.CENTER}],
                       ctx, n)
    if target is not None:
        yt = yv(target)
        ln = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(px), Inches(yt), Inches(px + pw), Inches(yt))
        ln.name = "ov_TargetLine"
        ln.line.color.theme_color = TC.ACCENT_1
        ln.line.width = Pt(1.25)
        ln.line.dash_style = 4
        lab = "Target " + fmt_value(target, unit, dec)
        lw_ = _need_w(lab, T["label"], True) + 0.1
        lx_ = px                                          # at the line's start, or at its end when the first bar's
        if abs(yt - (yv(vals[0]) - 0.2)) < 0.4:           # value label sits there (Target 83% met the 84% label)
            lx_ = px + pw - lw_
        _write(_box(sl, lx_, yt - 0.3, lw_, 0.26, "ov_TargetLabel", MSO_ANCHOR.BOTTOM),
               [{"text": lab, "size": T["label"], "bold": True, "numbers": False, "color": TC.ACCENT_1}], ctx, n)
        ctx.track(n, lab, prov)


# ------------------------------------------------------------------ tables (hairline grammar)
def _borders(cell, bottom=None, top=None, left=None, right=None):
    """Each side: (scheme colour, weight pt) or None (no line). Written first in tcPr, as the schema requires."""
    tcPr = cell._tc.get_or_add_tcPr()
    for side in ("lnL", "lnR", "lnT", "lnB"):
        el = tcPr.find(qn(f"a:{side}"))
        if el is not None:
            tcPr.remove(el)
    for i, (side, spec) in enumerate((("lnL", left), ("lnR", right), ("lnT", top), ("lnB", bottom))):
        ln = tcPr.makeelement(qn(f"a:{side}"), {"w": str(int(Pt(spec[1]))) if spec else "0"})
        if spec:
            sf = ln.makeelement(qn("a:solidFill"), {})
            sf.append(sf.makeelement(qn("a:schemeClr"), {"val": spec[0]}))
            ln.append(sf)
        else:
            ln.append(ln.makeelement(qn("a:noFill"), {}))
        tcPr.insert(i, ln)


def _plain(t) -> str:
    """Text as it renders: markup removed (a ^1 marker is a small superscript, not two characters)."""
    return re.sub(r"\*\*|\^\w+", "", str(t))


def _cellnum(v) -> float:
    """A table cell as a number: $1,234.5 -> 1234.5; (2.5%) and -2.5% -> -2.5. ValueError when it is not one."""
    txt = str(v).strip()
    neg = txt.startswith("(") and txt.endswith(")") or txt.startswith("-")
    num = float(re.sub(r"[^\d.]", "", txt))
    return -num if neg else num


_NA_CELL = re.compile(r"(?i)^\s*(?:(?:n/?a|nm|none)\b|[-—]\s*$|\[gap[^\]]*\]|not (?:stated|available|given|known|"
                      r"measured)\b)")


def _drop_na_columns(t: dict) -> dict:
    """A column most of whose cells say nothing (N/A, not stated) is dropped: a grid of N/A reads as machine filler
    (BCG never shows one). The first column and a column the highlighted row fills stay."""
    cols, rows = t.get("columns") or [], [r for r in t.get("rows") or [] if isinstance(r, list)]
    hl0 = set(t.get("highlight_rows") or [])
    empty = [i for i, r in enumerate(rows) if len(r) > 1 and i not in hl0
             and all(_NA_CELL.match(str(c)) for c in r[1:])]
    if empty and len(rows) - len(empty) >= 2:              # a row of nothing but N/A says nothing: it goes
        keep_r = [i for i in range(len(rows)) if i not in empty]
        t = dict(t, rows=[rows[i] for i in keep_r],
                 highlight_rows=[keep_r.index(i) for i in hl0 if i in keep_r],
                 italic_rows=[keep_r.index(i) for i in t.get("italic_rows") or [] if i in keep_r])
        rows = t["rows"]
    if len(cols) < 3 or len(rows) < 2 or t.get("groups") or t.get("col_widths"):
        return t
    hl = set(t.get("highlight_rows") or [])
    drop = [j for j in range(1, len(cols))
            if sum(bool(_NA_CELL.match(str(r[j]))) for r in rows if j < len(r)) * 2 > len(rows)
            and not any(j < len(rows[i]) and not _NA_CELL.match(str(rows[i][j])) for i in hl if i < len(rows))]
    if not drop or len(cols) - len(drop) < 2:
        return t
    keep = [j for j in range(len(cols)) if j not in drop]
    return dict(t, columns=[cols[j] for j in keep], rows=[[r[j] for j in keep if j < len(r)] for r in rows],
                _dropped_cols=[cols[j] for j in drop])


def _table(ctx, sl, n, t, x, y, w, h, dry=False):
    t = _drop_na_columns(t)
    groups = t.get("groups")
    cols, rows = t["columns"], t["rows"]
    hdr = 2 if groups else 1
    nrows = hdr + len(rows)
    size_ = t.get("size") or T["cell"]
    from .common import font_file, wrap_lines
    f_reg, f_bold = _font("body"), _font("body", True)
    lf = _line_factor(f_reg)                                      # 1.15 for Arial; taller faces (Segoe UI) more
    pad_r = 0.045
    row_min = lambda sz: sz * lf / 72 + pad_r                     # LibreOffice/PowerPoint minimum, measured
    widths = t.get("col_widths")
    colw = ([w * cw / sum(widths) for cw in widths] if widths else
            [w * 0.32] + [(w * 0.68) / max(1, len(cols) - 1)] * (len(cols) - 1))

    def col_head_h(sz):
        """Column-header row: as many lines as its most-wrapped (bold, size + 2) heading."""
        lines = max([wrap_lines(_plain(c), max(0.3, colw[ci] - 0.22), sz + 2, f_bold, 0.97) for ci, c in enumerate(cols)]
                    or [1])
        return lines * (sz + 2) * (lf + 0.10) / 72 + 0.08
    head_h = lambda sz: col_head_h(sz) + ((2 if any(len(g.get("label", "")) > 14 * g.get("span", 1) for g in groups)
                                            else 1) * (sz + 2) * 1.25 / 72 + 0.08 if groups else 0)
    bold_rows = set(t.get("highlight_rows") or []) | ({len(rows) - 1} if t.get("total_row") else set())

    def row_heights(sz, base):
        """Each row as tall as its most-wrapped cell (bold rows measured bold)."""
        out = []
        for i, r in enumerate(rows):
            f = f_bold if i in bold_rows else f_reg
            lines = max([wrap_lines(_plain(c), max(0.3, colw[ci] - 0.22), sz, f, 0.97) for ci, c in enumerate(r[:len(cols)])]
                        or [1])
            out.append(max(base, lines * sz * lf / 72 + pad_r))
        return out
    from .common import text_width_in

    def words_fit(sz):
        """Every word of every heading (bold, sz + 2) and cell (sz) fits its column: else it breaks mid-word."""
        for ci in range(len(cols)):
            room = max(0.3, colw[ci] - 0.3) * 0.94          # cell margins, and LibreOffice sets wider than measured
            heads = re.findall(r"\S+", _plain(cols[ci]))
            if any(text_width_in(wd, sz + 2, f_bold) > room for wd in heads):
                return False
            cells = [wd for r in rows if ci < len(r) for wd in re.findall(r"\S+", _plain(r[ci]))]
            if any(text_width_in(wd, sz, f_bold if ci == 0 else f_reg) > room for wd in cells):
                return False
        return True
    while size_ > 10 and (sum(row_heights(size_, row_min(size_))) + head_h(size_) > h or not words_fit(size_)):
        size_ -= 1
    base_size = T["cell"]
    if not t.get("_fixed_size") and size_ >= base_size:            # a short table in a tall box: a step or two up
        while (size_ < base_size + 3 and sum(row_heights(size_ + 1, row_min(size_ + 1))) + head_h(size_ + 1)
               + 0.18 * len(rows) <= h * 0.92 and words_fit(size_ + 1)):   # the type (whole words only), so the
            size_ += 1                                                     # page has no empty band
    t["size"] = size_
    hs = row_heights(size_, row_min(size_))
    if sum(hs) + head_h(size_) > h + 0.01 and len(rows) > 3:     # still too tall: carry rows to a continuation
        acc, fit = head_h(size_), 0
        for hv in hs:
            if acc + hv > h - (0.25 if t.get("total_row") else 0):
                break
            acc += hv
            fit += 1
        fit = max(3, fit)
        parts = -(-len(rows) // fit)                              # split evenly: never a stub continuation
        keep = max(3, -(-len(rows) // parts))
        ctx.carry_rows = rows[keep:] if not dry else ["dry"]
        rows = rows[:keep]
        nrows = hdr + len(rows)
        t["total_row"] = False                                    # the total row travels with the last part
    hs = row_heights(size_, row_min(size_))
    room = h - head_h(size_) - sum(hs)                            # rows stretch to fill the space they have,
    if room > 0:                                                  # a little air per row, never stretched rows:
        pad = t.get("_pad", 0.08 if room < 0.35 * h else 0.18)    # a short table gets a different composition
        hs = [hv + min(pad, room / len(hs)) for hv in hs]
    if dry:
        return float("inf") if ctx.carry_rows else sum(hs) + head_h(size_)
    gf = sl.shapes.add_table(nrows, len(cols), Inches(x), Inches(y), Inches(w), Inches(sum(hs) + head_h(size_)))
    gf.name = "Table"
    tbl = gf.table
    tblPr = tbl._tbl.find(qn("a:tblPr"))
    for k in list(tblPr.attrib):
        del tblPr.attrib[k]
    sid = tblPr.find(qn("a:tableStyleId"))
    if sid is not None:
        sid.text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"          # No Style, No Grid
    for ci, cw in enumerate(colw):
        tbl.columns[ci].width = Inches(cw)
    total = bool(t.get("total_row"))
    for ri, hv in enumerate(hs):
        tbl.rows[hdr + ri].height = Inches(hv)
    gh = 0.0
    if groups:                                                   # header heights measured on renders
        two = any(len(g.get("label", "")) > 14 * g.get("span", 1) for g in groups)
        gh = (2 if two else 1) * (size_ + 2) * 1.25 / 72 + 0.08
        tbl.rows[0].height = Inches(gh)
    hh = col_head_h(size_)
    tbl.rows[hdr - 1].height = Inches(hh)
    hl, it = set(t.get("highlight_rows") or []), set(t.get("italic_rows") or [])
    hgs = t.get("heat_grid")                                # diverging scales, one per block of rows, around a base
    hgs = [hgs] if isinstance(hgs, dict) else [h_ for h_ in hgs or [] if isinstance(h_, dict)]
    blk_vals = []
    for h_ in hgs:
        rr = set(h_.get("rows") or range(len(rows)))
        vv = []
        for r_i_, row in enumerate(rows):
            if r_i_ in rr:
                for v in row[1:]:
                    try:
                        vv.append(_cellnum(v))
                    except ValueError:
                        pass
        blk_vals.append(vv)

    def block(r_i_):
        for h_, vv in zip(hgs, blk_vals):
            if r_i_ in set(h_.get("rows") or range(len(rows))):
                return h_, vv
        return None, []
    heat = {}
    for k, v in (t.get("heat") or {}).items():                    # model-supplied heat values: numbers only
        try:
            nums = [float(z) for z in v if isinstance(z, (int, float)) or re.match(r"^-?[\d.]+$", str(z))]
            if nums:
                heat[int(k)] = nums
        except (TypeError, ValueError):
            continue
    size = size_

    def put(cell, text, bold=False, italic=False, align=PP_ALIGN.CENTER, color=TC.TEXT_1, sz=size, anchor=MSO_ANCHOR.MIDDLE):
        cell.margin_left = cell.margin_right = Inches(0.1)   # a 0.2 in gutter
        cell.margin_top = cell.margin_bottom = Inches(0.0)
        cell.vertical_anchor = anchor
        p = cell.text_frame.paragraphs[0]
        p.alignment = align
        p.line_spacing = Pt(_pitch(sz))      # exact, as in body text: a taller face (Segoe UI) cannot grow the rows
        _rich(p, str(text), sz, color, bold, numbers=False, italic=italic, accent=False)   # in a table the accent
        p._p.get_or_add_endParaRPr().set("sz", str(int(sz * 100)))   # colour is the recommended row's alone
    if groups:
        ci = 1
        for g in groups:
            sp = g.get("span", 1)
            if g.get("label"):
                if sp > 1:
                    tbl.cell(0, ci).merge(tbl.cell(0, ci + sp - 1))
                put(tbl.cell(0, ci), g["label"], True, align=PP_ALIGN.LEFT if g.get("left") else PP_ALIGN.CENTER,
                    sz=size + 2, anchor=MSO_ANCHOR.BOTTOM)
            for k in range(ci, ci + sp):
                _borders(tbl.cell(0, k), bottom=("tx1", 0.75) if g.get("label") else None)
            ci += sp
        _borders(tbl.cell(0, 0))
    def _num_col(ci):                                  # a column of numbers aligns right, words align left
        body = [str(r[ci]) for r in rows if ci < len(r) and str(r[ci]).strip() and str(r[ci]).strip() != "n/a"]
        return bool(body) and sum(1 for v_ in body if _NUMCELL.match(_plain(v_).replace("−", "-"))) >= 0.6 * len(body)
    col_align = [PP_ALIGN.LEFT if ci == 0 or not _num_col(ci) else PP_ALIGN.RIGHT for ci in range(len(cols))]
    hl_col = next((j for j, c in enumerate(cols) if j > 0 and re.search(r"(?i)\brecommend", _plain(c))), None) \
        if not hl else None                               # options as columns: the recommended one is tinted
    for ci, c in enumerate(cols):
        cell = tbl.cell(hdr - 1, ci)
        if ci == hl_col:
            cell.fill.solid()
            cell.fill.fore_color.theme_color = TC.BACKGROUND_2
        put(cell, c, True, align=col_align[ci], sz=size + (2 if not groups else 2), anchor=MSO_ANCHOR.BOTTOM,
            color=TC.ACCENT_1 if ci == hl_col else TC.TEXT_1)
        _borders(cell, bottom=("accent1", 1.0))
    for r_i, row in enumerate(rows):
        ri = hdr + r_i
        last = r_i == len(rows) - 1
        for ci in range(len(cols)):
            v = row[ci] if ci < len(row) else ""
            cell = tbl.cell(ri, ci)
            on = r_i in hl
            color = TC.TEXT_1
            hg, hg_vals = block(r_i)
            if hg and ci > 0:
                try:
                    fv = _cellnum(v)
                except ValueError:
                    fv = None
                centre = float(hg.get("center", 0))
                base = (r_i, ci) == tuple(hg.get("base") or (-1, -1))
                if fv is not None and hg_vals:
                    span_ = max(abs(max(hg_vals) - centre), abs(centre - min(hg_vals)), 1e-9)
                    k_ = (fv - centre) / span_                          # -1 .. 1 around the base case
                    cell.fill.solid()
                    if abs(k_) < 0.02 or base:
                        cell.fill.fore_color.theme_color = TC.BACKGROUND_2
                    else:
                        cell.fill.fore_color.theme_color = TC.ACCENT_4 if k_ > 0 else TC.ACCENT_2
                        cell.fill.fore_color.brightness = round(0.8 - 0.8 * min(1.0, abs(k_)), 2)
                    color = TC.BACKGROUND_1 if k_ > 0.6 and not base else TC.TEXT_1   # white only on deep blue
                put(cell, v, base, False, PP_ALIGN.CENTER, color)
                _borders(cell, bottom=("bg1", 1.5), top=("bg1", 1.5), left=("bg1", 1.5), right=("bg1", 1.5))
                if base:
                    _borders(cell, bottom=("tx1", 2.0), top=("tx1", 2.0), left=("tx1", 2.0), right=("tx1", 2.0))
                ctx.track(n, str(v), t.get("prov"))
                continue
            if ci == hl_col:                             # the recommended column: a light tint, accent text
                cell.fill.solid()
                cell.fill.fore_color.theme_color = TC.BACKGROUND_2
                color = TC.ACCENT_1
            elif on:                                     # the recommended row: accent text between accent rules,
                cell.fill.background()                   # no fill
                color = TC.ACCENT_1
            elif ci in heat and not (total and last):
                vals = sorted(heat[ci])
                try:
                    fv = _cellnum(v)
                    q = sum(1 for z in vals if z <= fv) / len(vals)
                    band = min(3, int(q * 4 - 1e-9))
                    shade = [(TC.ACCENT_3, 0.65), (TC.ACCENT_3, 0.0), (TC.ACCENT_4, 0.0), (TC.TEXT_2, 0.0)][band]
                    cell.fill.solid()
                    cell.fill.fore_color.theme_color = shade[0]
                    if shade[1]:
                        cell.fill.fore_color.brightness = shade[1]
                    color = TC.BACKGROUND_1 if band >= 1 else TC.TEXT_1
                except ValueError:
                    cell.fill.background()
            else:
                cell.fill.background()
            put(cell, v, on or (total and last), r_i in it, col_align[ci], color)
            if ci in heat and not on:
                # white gutters between heat cells, like separate bars
                _borders(cell, bottom=("bg1", 2.0), top=("bg1", 2.0), left=("bg1", 3.0), right=("bg1", 3.0))
            elif on:
                _borders(cell, top=("accent1", 1.5), bottom=("accent1", 1.5))
            else:
                _borders(cell, bottom=("tx1", 0.75) if last else ("accent5", 0.5))
            ctx.track(n, str(v), t.get("prov"))
    _alt(gf, t.get("alt") or "Table")
    gf.rendered_h = sum(hs) + hh + gh
    return gf


# ------------------------------------------------------------------ schema gate (model output -> renderable)
def _txt(v):
    if isinstance(v, dict):
        return str(v.get("text") or v.get("lead") or v.get("value") or "")
    return "" if v is None else str(v)


_META_CELL = re.compile(r"(?i)^\s*(?:(?:n/?a|nm)\s*[:\-–,(]\s*.*|.*\b(?:not stated|not (?:available|given|provided|"
                        r"disclosed|reported|measured)(?: in the (?:sources|facts|evidence|brief))?|no data|unknown|"
                        r"tbd)\s*\.?\s*)$")   # a cell that only reports a gap ("N/A: impact not stated") reads n/a


def _cell(v):
    """A table cell the model wrote as {lead, bullets}: keep the detail, not just the lead. A cell that only says the
    sources are silent ("Not stated") reads n/a: commentary about the evidence belongs in the notes, not the page."""
    t = _cell_text(v)
    if not _META_CELL.match(t):
        return t
    head = re.sub(r"\s*[:;,]\s*[^:;,]*\b(?:not (?:stated|available|given|provided|disclosed|reported|measured)|no data|"
                  r"unknown|tbd)\b[^:;,]*$", "", t).strip(" :;,")       # a figure then a gap note: keep the figure
    head = re.sub(r"\s*[:;,]\s*(?:all|each|every)\s+\w+$", "", head)    # ... and no dangling "all clinics" tail
    return head if re.search(r"\d", head) and not re.match(r"(?i)^\s*n/?a\b", head) else "n/a"


def _cell_text(v):
    if isinstance(v, dict) and v.get("bullets"):
        detail = "; ".join(_txt(b) if isinstance(b, dict) else str(b) for b in v["bullets"])
        lead = _txt(v)
        return f"{lead}: {detail}" if lead else detail
    return _txt(v)


_GAP_RE = re.compile(r"\s*[\(\[]?\[GAP:?\s*([^\]]*)\][\)\]]?")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9*$~(])")


def tidy_prose(t: str) -> str:
    """Repair what removing words leaves behind: empty bold markers, doubled spaces, a space before punctuation,
    stacked punctuation (':.', ',.') and a sentence that ends on a dangling 'but'/'and'/'of'."""
    t = re.sub(r"\*\*\s*\*\*", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,;:])\s*([.!?])", r"\2", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"[,;:]?\s+\b(but|and|or|of|with|to|because|while|from|by|for)\s*([.!?])?$",
               lambda m: m.group(2) or "", t.strip(), flags=re.I)
    return t.strip()


def drop_gap_sentences(text: str, gaps: list | None = None) -> str:
    """Prose with a [GAP] marker: the whole sentence that leaned on the missing figure goes (a sentence with a hole
    in it reads as broken: 'market value of $5.5B but.'); what the gap was is recorded in `gaps`."""
    if "[GAP" not in text:
        return text
    if gaps is not None:
        gaps.extend(m.group(1).strip() for m in _GAP_RE.finditer(text))
    keep = []
    for sent in _SENT.split(text):
        if "[GAP" not in sent:
            keep.append(sent)
            continue
        parts = re.split(r"(?<=[,;])\s+", sent)                  # "Margin is 28.9%, net debt [GAP]." keeps
        good = [x for x in parts if "[GAP" not in x]              # "Margin is 28.9%." when what is left is a
        if good and parts[0] in good:                             # leading clause that stands on its own
            head = tidy_prose(" ".join(good)).rstrip(",;")
            from engines.forge.density import _is_clause
            if _is_clause(head):
                keep.append(head if head.endswith((".", "!", "?")) else head + ".")
    return tidy_prose(" ".join(keep))


def _emptied(d: dict) -> bool:
    """A bullet or block whose text was all gap and that has nothing else to show."""
    if not any(k in d for k in ("text", "lead")):
        return False
    words = "".join(str(d.get(k) or "") for k in ("text", "lead", "label", "value")).strip()
    return not words and not any(d.get(k) for k in ("bullets", "items", "chart", "table", "steps"))


_GAPCELL = re.compile(r"\[GAP[^\]]*\]")
_NA = re.compile(r"(?i)^\s*(n/a|n\.a\.|not available)\s*$")     # a dash is a nil in a finance table, not a gap
_VALUE_HEAD = re.compile(r"(?i)^\s*(value|amount|figure|terms?|number|total|price|size|\$|€|£)\b")


def _is_na(c) -> bool:
    c = re.sub(r"\s*(\^\w+|[¹²³⁴⁵⁶⁷⁸⁹⁰]+)\s*$", "", str(c or ""))     # "n/a¹" is still n/a
    return bool(_NA.match(c)) or bool(_GAPCELL.search(c) and not _GAPCELL.sub("", c).strip())


def _table_gaps(t: dict, s: dict) -> dict:
    """A table's gaps, honestly: [GAP] cells read N/A; a row with no value at all goes (in a key-value table such as
    deal terms, a row whose Value is N/A goes: "Equity value: N/A" is not a term); a column that is mostly N/A goes,
    with a footnote that names the few values it had; whatever N/A is left is footnoted. What was dropped is
    recorded in the notes. Never invents a value."""
    t = dict(t)
    cols, rows = list(t.get("columns") or []), [list(r) for r in t.get("rows") or []]
    gapped = any(_GAPCELL.search(c) for r in rows for c in r)
    if not rows or len(cols) < 2 or not any(_GAPCELL.search(c) or _is_na(c) for r in rows for c in r[1:]):
        return t
    rows = [[_GAPCELL.sub("N/A", c) if _GAPCELL.search(c) and _is_na(c) else _GAPCELL.sub("not available", c)
             for c in r] for r in rows]
    na = lambda c: _is_na(c)                               # noqa: E731
    keyval = len(cols) <= 4 and any(_VALUE_HEAD.match(c) for c in cols[1:2])
    note = lambda m: s.__setitem__("notes", (s.get("notes", "") + "\n[QA] " + m).strip())  # noqa: E731
    hl = set(t.get("highlight_rows") or [])
    keep, gone = [], []
    for i, r in enumerate(rows):
        vals = r[1:len(cols)]
        if (vals and all(na(c) for c in vals)) or (keyval and len(r) > 1 and na(r[1])):
            gone.append(i)
        else:
            keep.append(i)
    if gone and len(keep) >= 1 and not t.get("heat_grid"):
        note("table rows left out, no value in the sources: " + "; ".join(str(rows[i][0]) for i in gone))
        remap = {old: new for new, old in enumerate(keep)}
        for key in ("highlight_rows", "italic_rows"):
            if t.get(key):
                t[key] = [remap[i] for i in t[key] if i in remap]
        if t.get("total_row") and len(rows) - 1 in gone:
            t["total_row"] = False
        rows = [rows[i] for i in keep]
    feet = []
    if not t.get("groups") and len(rows) >= 2:
        drop = []
        for ci in range(1, len(cols)):
            col = [r[ci] if ci < len(r) else "" for r in rows]
            k = sum(1 for c in col if na(c))
            if k >= 2 and k / len(col) >= 0.5 and len(cols) - len(drop) > 2:   # half or more N/A: the column goes
                drop.append(ci)
                have = [(re.sub(r"\*\*", "", str(r[0])), r[ci]) for r in rows if ci < len(r) and not na(r[ci])]
                feet.append(f"{cols[ci]}: " + ("; ".join(f"{b} for {a}" for a, b in have[:3]) + "; " if have else "")
                            + "not available in the source data for the other rows")
        if drop:
            note("table columns left out, mostly not available: " + ", ".join(cols[ci] for ci in drop))
            keepc = [ci for ci in range(len(cols)) if ci not in drop]
            cols = [cols[ci] for ci in keepc]
            rows = [[r[ci] for ci in keepc if ci < len(r)] for r in rows]
            if t.get("col_widths") and len(t["col_widths"]) == len(keepc) + len(drop):
                t["col_widths"] = [t["col_widths"][ci] for ci in keepc]
            if isinstance(t.get("heat"), dict):
                t["heat"] = {str(keepc.index(int(k))): v for k, v in t["heat"].items()
                             if str(k).isdigit() and int(k) in keepc}
    if gapped and any(na(c) for r in rows for c in r[1:]):
        feet.append("N/A: not available in the source data")
        note("table contains values not available in the sources")
    if feet:
        s["footnotes"] = list(s.get("footnotes") or []) + feet
    t["columns"], t["rows"] = cols, rows
    return t


def normalize(s: dict) -> dict:
    """Coerce the shapes models commonly produce into the renderer's shapes (never invents content)."""
    s = dict(s)
    ex = s.get("exhibit")
    if not s.get("chart") and isinstance(ex, dict) and isinstance(ex.get("chart"), dict):
        s["chart"] = ex["chart"]                          # the model nested the chart in the exhibit: same chart
    if s.get("archetype") == "football_field" and isinstance(s.get("chart"), dict):
        s["chart"] = dict(s["chart"], kind="range")
    if isinstance(s.get("chart"), dict) and _is_bridge(s["chart"]):
        s["chart"] = dict(s["chart"], kind="waterfall")   # start + steps = end: draw it as the bridge it is
    t = s.get("table")
    if isinstance(t, dict):
        t = dict(t)
        cell_prov = [p for r in t.get("rows") or [] if isinstance(r, list) for c in r if isinstance(c, dict)
                     for p in (c.get("prov") or []) if isinstance(c.get("prov"), list)]
        if cell_prov:                                     # sources the model put on cells stay the table's sources
            t["prov"] = list(dict.fromkeys(list(t.get("prov") or []) + cell_prov))
        t["columns"] = [_txt(c) for c in t.get("columns") or []]
        t["rows"] = [[_cell(c) for c in (r if isinstance(r, list) else [r])] for r in t.get("rows") or []]
        s["table"] = _table_gaps(t, s)
    if isinstance(s.get("parts"), list):                  # a grid's table parts get the same treatment
        ps = []
        for prt in s["parts"]:
            if isinstance(prt, dict) and prt.get("type") == "table":
                inner = isinstance(prt.get("table"), dict)
                tb = dict(prt["table"] if inner else prt)
                tb["columns"] = [_txt(c) for c in tb.get("columns") or []]
                tb["rows"] = [[_cell(c) for c in (r if isinstance(r, list) else [r])] for r in tb.get("rows") or []]
                tb = _table_gaps(tb, s)
                prt = dict(prt, table=tb) if inner else dict(prt, **tb)
            ps.append(prt)
        s["parts"] = ps
    for key in ("bullets",):
        if isinstance(s.get(key), list):
            s[key] = [b if isinstance(b, dict) else {"text": str(b)} for b in s[key]]
    for r in s.get("rows") or []:
        if isinstance(r, dict):
            r["cells"] = [c if isinstance(c, dict) else {"lead": "", "bullets": [str(c)]} for c in r.get("cells") or []]
    if isinstance(s.get("callout"), list):
        s["callout"] = {"bullets": s["callout"]}
    if isinstance(s.get("source"), str):
        s["source"] = {"text": s["source"]}
    gaps = []
    gap_re = re.compile(r"\s*[\(\[]?\[GAP:?\s*([^\]]*)\][\)\]]?")

    def scrub(v):                                          # prose: a gap is removed and noted, never shown
        if isinstance(v, str) and "[GAP" in v:
            return drop_gap_sentences(v, gaps)
        if isinstance(v, list):
            out = []                                       # a bullet that was only a gap goes, not an empty dot
            for x in v:
                y = scrub(x)
                had_gap = "[GAP" in json.dumps(x, default=str)
                if had_gap and (y == "" or (isinstance(y, dict) and _emptied(y))):
                    continue
                out.append(y)
            return out
        if isinstance(v, dict):
            return {k: (x if k in ("table", "chart", "prov", "notes") else scrub(x)) for k, x in v.items()}
        return v
    for k in list(s):
        if k not in ("table", "chart", "prov", "notes", "footnotes"):   # sources too: a footer showed it
            s[k] = scrub(s[k])
    if isinstance(s.get("footnotes"), list):               # footnotes keep their numbering: the gap is named
        s["footnotes"] = [tidy_prose(gap_re.sub(" not available in the sources", f)) if isinstance(f, str) else f
                          for f in s["footnotes"]]
    if gaps:
        s["notes"] = (s.get("notes", "") + "\n[QA] Not available in the sources, left out of the text: "
                      + "; ".join(g for g in gaps if g)).strip()
    for k in ("title", "title_right"):                       # no "…" continuation: reviewers read it as cut off
        if isinstance(s.get(k), str):
            s[k] = re.sub(r"^\s*(?:\.\.\.|…)\s*", "", re.sub(r"\s*(?:\.\.\.|…)\s*$", "", s[k]))
            if k == "title_right" and s[k][:1].islower():
                s[k] = s[k][:1].upper() + s[k][1:]
    for k in ("title", "title_right"):
        if isinstance(s.get(k), str) and s[k].rstrip().endswith(".") and not s[k].rstrip().endswith("..."):
            s[k] = s[k].rstrip()[:-1]
    if isinstance(s.get("footnotes"), str):
        s["footnotes"] = [s["footnotes"]]
    if isinstance(s.get("footnotes"), list):                # {"text", "prov"} from some writers: the text is the
        s["footnotes"] = [str(f.get("text") or "") if isinstance(f, dict) else f   # note; a dict crashed the page
                          for f in s["footnotes"]]           # and its fallback, leaving a title over a blank body
        s["footnotes"] = [f for f in s["footnotes"] if not isinstance(f, str) or f.strip()]
    return s


REQUIRED = {"closing": ["steps"], "next_steps": ["steps"], "challenge_matrix": ["columns", "rows"], "exhibit_argument": ["chart", "blocks"],
            "table_focus": ["table"], "bar_takeaway": ["chart"], "column_trend": ["chart"], "line_trend": ["chart"],
            "waterfall_bridge": ["chart"], "stacked_composition": ["chart"], "football_field": ["chart"],
            "benchmark": ["benchmark"], "executive_summary": ["points"],
            "grid": ["parts"]}


_BRIDGE_END = re.compile(r"(?i)total|offer|end|pro ?forma|implied|result|=|price|value|net|after|exit|target")


def _is_bridge(c: dict) -> bool:
    """A single-series bar/column chart whose first value plus the middle steps equals its last value, ending on a
    total-like category (unaffected price + premium = offer price): a bridge, drawn as a waterfall."""
    if c.get("kind") not in (None, "bar", "column") or len(c.get("series") or []) != 1:
        return False
    cats = [str(x) for x in c.get("categories") or []]
    v = c["series"][0].get("values") if isinstance(c["series"][0], dict) else None
    if not v or len(v) != len(cats) or not 3 <= len(v) <= 8 or not all(isinstance(x, (int, float)) for x in v):
        return False
    if not _BRIDGE_END.search(cats[-1]) or abs(v[-1]) < 1e-9:
        return False
    return abs(v[0] + sum(v[1:-1]) - v[-1]) <= 0.002 * abs(v[-1]) and any(abs(x) > 0 for x in v[1:-1])


FF_MIN_METHODS = 3          # a football field is a comparison of methods: fewer than three is a range, not a field


def range_problems(c: dict) -> list[str]:
    """Football field: two series (low, high), one value each per method, >= 3 methods, and every range and marker in
    one unit (a per-share offer against enterprise-value ranges in $B is not one chart)."""
    se = [x for x in c.get("series") or [] if isinstance(x, dict)]
    cats = c.get("categories") or []
    if len(se) != 2 or any(len(x.get("values") or []) != len(cats) for x in se):
        return ["a football field needs two series (low, high) with one value per method"]
    if any(not isinstance(v, (int, float)) for x in se for v in x["values"]):
        return ["football field values must be numbers"]
    bad = [cats[i] for i, (a, b) in enumerate(zip(se[0]["values"], se[1]["values"])) if a > b]
    if bad:
        return [f"low above high for {', '.join(map(str, bad))}"]
    out = []
    if len(cats) < FF_MIN_METHODS:
        out.append(f"a football field with {len(cats)} method{'s' if len(cats) != 1 else ''} is not a valuation "
                   f"comparison; it needs >= {FF_MIN_METHODS} methods (comps, precedents, DCF, ...) in the offer's unit")
    lo, hi = min(se[0]["values"]), max(se[1]["values"])
    for m in range_markers(c):
        v = m["value"]
        if hi > 0 and (v > 2.5 * hi or (lo > 0 and v < lo / 2.5)):
            out.append(f"the {m['label'] or 'marker'} ({v:g}) is not in the ranges' unit ({lo:g}-{hi:g}); per-share "
                       "values and enterprise values cannot share one axis")
            break
    if re.search(r"(?i)\$\s?b\b|bn|billion|\bEV\b|enterprise", " ".join(map(str, cats))) and \
            re.search(r"(?i)per share|/\s*sh", json.dumps(c.get("unit", "")) + json.dumps(range_markers(c))):
        out.append("the methods mix enterprise values and per-share values; one unit per football field")
    return out


def range_markers(c: dict) -> list[dict]:
    """The vertical reference lines of a football field (offer, current price, ...): `markers` or one `marker`."""
    ms = c.get("markers") if isinstance(c.get("markers"), list) else [c.get("marker")] if c.get("marker") else []
    out = []
    for m in ms:
        if isinstance(m, dict) and isinstance(m.get("value"), (int, float)):
            out.append({"label": str(m.get("label") or "").strip(), "value": float(m["value"])})
    return out


def _clean_chart(c: dict) -> dict:
    """A category with no value in any series is dropped (a site not measured yet is not a bar of zero, and it made
    the whole chart fall back to two bare figures)."""
    if not isinstance(c, dict) or c.get("kind") in ("bubble", "range", "stats", "waterfall"):
        return c
    cats, series = list(c.get("categories") or []), [se for se in c.get("series") or [] if isinstance(se, dict)]
    keep = [j for j in range(len(cats)) if any(j < len(se.get("values") or []) and
                                                isinstance(se["values"][j], (int, float)) for se in series)]
    if len(keep) == len(cats) or len(keep) < 2:
        return c
    out = dict(c, categories=[cats[j] for j in keep],
               series=[dict(se, values=[se["values"][j] for j in keep]) for se in series])
    if isinstance(c.get("descriptors"), list):
        out["descriptors"] = [c["descriptors"][j] for j in keep if j < len(c["descriptors"])]
    return out


MIXED_UNITS = ("mixed", "various", "multiple", "different units", "several")


def _small_multiples(c: dict) -> bool:
    """Measures with different units as categories of one clustered chart (picks per hour beside an accuracy %):
    drawn as one small chart per measure, each on its own scale."""
    u = str(c.get("unit") or "").strip().lower()
    return (c.get("kind") in ("column", "bar", None) and len(c.get("series") or []) >= 2
            and 2 <= len(c.get("categories") or []) <= 4 and (u in MIXED_UNITS or " and " in u))


def chart_problems(c: dict) -> list[str]:
    """Charts that would mislead: one bar, mixed units in one series, ranks drawn as quantities, short lines."""
    c = _clean_chart(c)
    if not c or c.get("kind") == "bubble" or (isinstance(c, dict) and _small_multiples(c)):
        return []
    if c.get("kind") == "range":
        return range_problems(c)
    cats = [str(x) for x in c.get("categories") or []]
    series = [se for se in c.get("series") or [] if isinstance(se, dict)]
    out = []
    vals = [float(v) for se in series for v in se.get("values") or [] if isinstance(v, (int, float))]
    # a parenthetical with digits is a qualifier ("Premium (35%)", "Unaffected (5 Nov 2026)"), not a unit
    units = {m.group(0).lower() for cat in cats
             for m in re.finditer(r"\(([^)\d]*)\)|%|\$|thousand|million|share", re.sub(r"\([^)]*\d[^)]*\)", "", cat), re.I)}
    bad = [v for se in series for v in se.get("values") or []
           if not isinstance(v, (int, float)) and not (v is None and c.get("kind") == "line")]
    if bad:
        out.append(f"{len(bad)} chart values are not numbers (gap markers or text); chart only the points the facts "
                   "support, or show the numbers you have as a stat stack")
    if c.get("kind") == "waterfall" and len(series) == 1:
        v = [x for x in series[0].get("values") or [] if isinstance(x, (int, float))]
        end_run = _wf_totals(v)[1] if len(v) >= 3 else 0.0
        if len(v) >= 3 and abs(end_run - v[-1]) > max(0.015 * abs(v[-1]), 0.05):
            out.append(f"the bridge does not add up: {v[0]:g} plus the steps (and any subtotals) comes to "
                       f"{end_run:g}, not the end value {v[-1]:g}; a waterfall needs a start total, signed steps in "
                       "the same unit, subtotals equal to the running total, and an end total")
    if len(cats) < 2 and c.get("kind") != "waterfall":
        out.append("a chart with one bar shows nothing; give >= 3 comparable categories or use a stat stack")
    sunits = {bool(re.search(r"%|cagr|share|rate|growth", str(se.get("name", "")), re.I)) for se in series}
    if len(series) > 1 and len(sunits) > 1:
        out.append("the series mix a percentage with an absolute measure; one chart, one unit")
    if len(units) > 1 or (len(series) == 1 and vals and min(abs(v) for v in vals) > 0
                          and max(abs(v) for v in vals) / min(abs(v) for v in vals) > (10 if len(cats) <= 3 else 40)
                          and len(cats) <= 5
                          and c.get("kind") != "waterfall" and not c.get("delta")):   # a stated multiple is the point
        out.append("the chart mixes different measures or units in one series; show one measure in one unit")
    if len(series) == 1 and vals and sorted(vals) == [float(i) for i in range(1, len(vals) + 1)]:
        out.append("the values are ranks 1..n, not quantities; rank in a table or ordered list instead")
    if c.get("kind") == "line" and len(cats) < 4:
        out.append("a line needs >= 4 periods; use columns for fewer")
    return out


def _as_written(v: float, text: str) -> str | None:
    """The figure as the slide's own text writes it ($65.37 per share -> $65.37/sh, $83.0bn, 35%)."""
    if not text:
        return None
    forms = {f"{v:,.2f}", f"{v:,.1f}", f"{v:,.0f}" if v == int(v) else None, f"{v:g}"} - {None}
    for f in sorted(forms, key=len, reverse=True):
        m = re.search(r"(\$?)" + re.escape(f) + r"(?![\d.])\s?((?i:bn|tn|mn|m|b|k)\b|%|x\b|/sh|per share)?", text)
        if m and (m.group(1) or m.group(2)):
            unit = {"per share": "/sh"}.get(m.group(2) or "", m.group(2) or "")
            return f"{m.group(1)}{f}{unit}"
    return None


def stat_stack(c: dict, text: str = "") -> dict:
    """Unrelated headline numbers as big emphasised figures with labels (the consulting way). Each figure keeps its
    unit and precision, taken from how the slide's own text writes it when it does: $48.42/sh, $83.0bn, 35%."""
    items = []
    cunit = str(c.get("unit", "") or "").strip()
    dec = c.get("decimals")
    # the chart's own unit: a ratio/percentage suffix, or a currency with an optional magnitude ($m, €bn, £)
    usfx = {"%": "", "x": "x", "pp": " pp"}.get(cunit.lower())
    cm = re.fullmatch(r"([$€£¥])?\s*(bn|mm|m|k|tn|b)?(\s*/\s*sh)?", cunit, re.I) if usfx is None else None
    other = " " + cunit if cunit and usfx is None and not cm else ""     # a plain unit: 450 stores
    for se in c.get("series") or []:
        for cat, v in zip(c.get("categories") or [], se.get("values") or []):
            if not isinstance(v, (int, float)):
                continue
            cat = str(cat)                                  # categories may be numbers (years)
            ctx_ = f"{cat} {se.get('name', '')}"
            money = bool(cm and (cm.group(1) or cm.group(2)))    # "$M", "€bn": a currency unit is never a percent,
            pct = cunit == "%" or (usfx is None and not other and not money and abs(v) <= 100   # whatever the label
                                   and bool(re.search(r"%|share of|percent|premium|margin|growth", ctx_, re.I)))
            per_share = bool(re.search(r"/\s*sh|per share|price", ctx_, re.I) or (cm and cm.group(3)))
            cur = (cm.group(1) if cm and cm.group(1) else
                   "$" if not (pct or usfx or other) and re.search(r"\$|dollar|price|value|cost|revenue|debt|capex",
                                                                   ctx_ + " " + cunit, re.I) else "")
            mag = ("" if per_share or pct or usfx or other else
                   ((cm.group(2) or "") if (cm.group(2) or "").upper() in ("B", "M") else (cm.group(2) or "").lower())
                   if cm and cm.group(2)
                   else "bn" if re.search(r"\$?bn|billion", ctx_, re.I)
                   else "m" if re.search(r"\$m\b|million", ctx_, re.I) else "")
            d = (dec if isinstance(dec, int) and (v != int(v) or abs(v) < 10) else   # the chart's decimals suit its
                 (2 if per_share and v != int(v) else (1 if v != int(v) else 0)))    # small values: $6,510, not .00
            if per_share:
                d = 2
            num = ("-" if v < 0 else "") + cur + f"{abs(v):,.{d}f}"          # -$4.2m, not $-4.2m
            if not mag and not pct and not per_share and abs(v) >= 100_000:  # a big figure reads $9.2M, never
                q, sfx = (abs(v) / 1e9, "B") if abs(v) >= 1e9 else (abs(v) / 1e6, "M") if abs(v) >= 1e6 \
                    else (abs(v) / 1e3, "K")                                  # $9,200,000 (it wraps in a tile)
                num = ("-" if v < 0 else "") + cur + re.sub(r"\.0$", "", f"{q:.1f}" if q < 100 else f"{q:.0f}") + sfx
            tail = ("/sh" if per_share else "") + (usfx or "") + other
            lab = re.sub(r"^\s*\d+[.)]\s*", "", re.sub(r"\s*\([^)]*\)", "", cat)).strip()
            if len(c.get("series") or []) > 1:
                lab = f"{lab}: {se.get('name', '')}"
            written = _as_written(v, text)
            items.append({"value": written or f"{num}{mag}{'%' if pct else ''}{tail}", "label": lab})
    if len(items) > 6:                                  # keep the first and the last (start and end of a story)
        items = items[:5] + items[-1:]
    return {"kind": "stats", "items": items}


def _stats(ctx, sl, n, c, x, y, w, h):
    """Big figures with labels, spread over the whole exhibit area with hairlines between them."""
    items = c.get("items") or []
    k = max(1, len(items))
    rh = min(1.2, h / k)
    y += (h - rh * k) / 2                                   # a short stack sits centred, not stranded at the top
    big = rh >= 1.25
    vsize, lsize = (40, 16) if big else (28, 14)
    vw = 2.6 if big else 1.9
    for i, it in enumerate(items):
        yy = y + i * rh
        _write(_box(sl, x, yy + 0.02, vw, rh - 0.1, f"role_subtitle_Stat{i + 1}", MSO_ANCHOR.MIDDLE),
               [{"text": it["value"], "size": vsize, "bold": True, "heading": True, "numbers": False,
                 "color": TC.ACCENT_1}])
        ctx.track(n, str(it["value"]), c.get("prov"))   # the chart's figures keep its sources (P13)
        _write(_box(sl, x + vw + 0.1, yy + 0.02, w - vw - 0.1, rh - 0.1, f"StatLabel{i + 1}", MSO_ANCHOR.MIDDLE),
               [{"text": it["label"], "size": lsize, "numbers": False}])
        if i < k - 1:
            _rule(sl, x, yy + rh, w, TC.ACCENT_5, 0.5, f"StatRule{i + 1}")


def schema_errors(s: dict) -> list[str]:
    """Precise, fixable messages for the repair loop."""
    a = s.get("archetype")
    errs = [f"'{k}' is missing or empty (required for {a})" for k in REQUIRED.get(a, []) if not s.get(k)]
    from .diagrams import ARCHETYPES as DA
    if a in DA:
        from .diagrams import schema_errors as diagram_errors
        return errs + diagram_errors(s)
    if a == "library":                                # a layout from the private slide library (component.py)
        from .component import schema_errors as library_errors
        return errs + library_errors(s)
    if a == "layout":                                 # a built-in layout (layouts.py)
        from .layouts import schema_errors as layout_errors
        return errs + layout_errors(s)
    if a == "grid":                                   # parts library: each part is checked on its own
        from .parts import grid_errors
        return errs + grid_errors(s)
    t = s.get("table")
    if a in ("table_focus", "table_highlight") and isinstance(t, dict):
        if len(t.get("rows") or []) < 3:
            errs.append("a table needs at least 3 rows of sourced data; with fewer, choose another archetype")
        w = len(t.get("columns") or [])
        cells = [str(c) for r in t.get("rows") or [] for c in r[1:]]
        if cells and sum(1 for c in cells if "[GAP" in c or c.strip().lower() in ("n/a", "")) > 0.3 * len(cells):
            errs.append("over 30% of the table's values are missing; show only rows and columns the facts support")
        if any(len(r) != w for r in t.get("rows") or []):
            errs.append(f"every table row needs {w} cells (one per column)")
    if a == "challenge_matrix":
        rows = s.get("rows") or []
        if not 3 <= len(rows) <= 5:
            errs.append(f"challenge_matrix needs 3-5 rows, got {len(rows)}")
        for i, r in enumerate(rows, 1):
            cells = r.get("cells") or []
            if len(cells) != 2 or not all(c.get("lead") for c in cells if isinstance(c, dict)):
                errs.append(f"row {i}: needs exactly 2 cells, each with a 'lead' sentence and 1-3 bullets")
    if a == "benchmark" and isinstance(s.get("benchmark"), dict):
        k = len(s["benchmark"].get("companies") or [])
        for p in s["benchmark"].get("panels") or []:
            if isinstance(p, dict) and len(p.get("values") or []) != k:
                errs.append(f"benchmark panel '{p.get('title', '')}' needs one value per company ({k}), "
                            f"got {len(p.get('values') or [])}")
    if a == "exhibit_argument" and not s.get("title_right"):
        errs.append("'title_right' is missing: the right half's title continues the left title's thought")
    c = s.get("chart") or {}
    if c and c.get("kind") not in ("bubble", "stats") and (not c.get("categories") or not c.get("series")):
        errs.append("chart needs 'categories' and 'series' [{name, values}] with one value per category")
    errs += chart_problems(c) if c.get("kind") != "stats" else []
    return errs


def football_fallback(s: dict) -> dict:
    """A football field without >= 3 comparable methods becomes figures beside the argument (a grid of stats and
    text): each method's range and each reference price as a figure, the takeaway and bullets as the argument."""
    c = s.get("chart") or {}
    unit, dec = c.get("unit", "$"), c.get("decimals")
    items = [{"value": fmt_value(m["value"], unit, dec), "label": re.sub(r"\s*[$€£]?\d[\d,.]*\s*$", "", m["label"])
              or "Reference price", "prov": c.get("prov")} for m in range_markers(c)]
    se = [x for x in c.get("series") or [] if isinstance(x, dict)]
    if len(se) == 2:
        for cat, lo, hi in zip(c.get("categories") or [], se[0].get("values") or [], se[1].get("values") or []):
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                val = fmt_value(lo, unit, dec) if abs(hi - lo) < 1e-9 else \
                    f"{fmt_value(min(lo, hi), unit, dec)}–{fmt_value(max(lo, hi), unit, dec)}"    # never "$48.42–$48.42"
                items.append({"value": val, "label": str(cat), "prov": c.get("prov")})
    seen, uniq = set(), []                                     # one figure per thing: a price drawn both as a marker
    for it in items:                                           # and as a "range" shows once (the marker's value)
        key = re.sub(r"[^a-z]", "", it["label"].lower().replace("price", ""))
        if key in seen or any(it["value"] == u["value"] for u in uniq):
            continue
        seen.add(key)
        uniq.append(it)
    items = uniq
    tk = s.get("takeaway") or {}
    text = {"type": "text", "lead": tk.get("text", ""), "prov": tk.get("prov"),
            "bullets": [b if isinstance(b, dict) else {"text": str(b)} for b in s.get("bullets") or []]}
    out = {k: v for k, v in s.items() if k not in ("chart", "takeaway", "bullets")}
    out["notes"] = (s.get("notes", "") + "\n[QA] football field shown as figures: "
                    + "; ".join(chart_problems(c))).strip()
    if not items:
        return dict(out, archetype="argument_only", blocks=[{"text": text["lead"], "bullets": text["bullets"]}])
    parts = [{"type": "stats", "heading": c.get("title") or s.get("subtitle") or "Valuation reference points",
              "items": items[:4]}]
    if text["lead"] or text["bullets"]:
        parts.append(text)
    out.update(archetype="grid", layout="side_main" if len(parts) == 2 else "full", look="editorial", parts=parts)
    out.pop("subtitle", None)
    return out


def fallback(s: dict) -> dict:
    """When a slide cannot satisfy its archetype, render the evidence it has in the simplest faithful layout."""
    s = dict(s)
    if s.get("archetype") == "grid":
        from .parts import grid_fallback
        return grid_fallback(s)
    from .diagrams import ARCHETYPES as DA
    if s.get("archetype") in DA:
        from .diagrams import fallback as diagram_fallback
        return diagram_fallback(s)
    if s.get("archetype") == "library":
        from .component import fallback as library_fallback
        return library_fallback(s)
    if s.get("archetype") == "layout":
        from .layouts import fallback as layout_fallback
        return layout_fallback(s)
    c = s.get("chart") or {}
    if c.get("kind") == "range" and chart_problems(c):
        return football_fallback(s)
    if c and chart_problems(c):                        # a misleading chart becomes a stat stack, layout kept
        st = stat_stack(c, json.dumps({k: v for k, v in s.items() if k not in ("chart", "table")}, ensure_ascii=False))
        tk = s.get("takeaway")
        st["prov"] = c.get("prov") or s.get("title_prov") or (tk.get("prov") if isinstance(tk, dict) else None)
        if len(st["items"]) < 2:                       # one number is not an exhibit: full-width argument instead
            s["chart"] = None
            s["archetype"] = "argument_only"
            if not s.get("blocks"):
                s["blocks"] = [{"text": "", "bullets": list(s.get("bullets") or [])}]
            if s.get("title_right"):                        # the right half's title leads the text as its intro
                tr = s.pop("title_right").strip(" .…")      # line (glued onto the title it made two sentences)
                s["intro"] = f"**{tr[0].upper() + tr[1:]}**."
            return s                                        # the callout stays a callout (so-what band)
        s["chart"] = st
        if s.get("archetype") in ("exhibit_argument",) and s.get("blocks") and s.get("title_right"):
            return s
        s["archetype"] = "bar_takeaway"
        return s
    if s.get("archetype") in ("table_focus", "table_highlight") and isinstance(s.get("table"), dict):
        t = s["table"]
        keep = [r for r in t.get("rows") or [] if not any("[GAP" in str(c) for c in r[1:])]
        s["blocks"] = [{"text": (s.get("takeaway") or {}).get("text", ""), "bullets": [
            {"text": f"**{r[0]}**: " + "; ".join(f"{h} {v}" for h, v in zip(t["columns"][1:], r[1:]))} for r in keep[:6]]}]
        s["archetype"] = "argument_only"
        return s
    if s.get("archetype") == "challenge_matrix" and s.get("rows"):
        s["blocks"] = [{"text": f"**{_txt(r.get('label'))}**", "bullets": [
            b for cell in r.get("cells") or [] for b in ([{"text": cell.get("lead", "")}] if cell.get("lead") else [])
            + list(cell.get("bullets") or [])]} for r in s["rows"]]
        s["archetype"] = "argument_only"
        return s
    if s.get("chart") and (s["chart"].get("categories") or s["chart"].get("points")):
        s["archetype"] = "bar_takeaway"
        if not s.get("bullets"):
            s["bullets"] = [b for blk in s.get("blocks") or [] for b in (blk.get("bullets") or [])][:4]
        return s
    bullets = [b for blk in s.get("blocks") or [] for b in (blk.get("bullets") or [])] + list(s.get("bullets") or [])
    bullets += [b for b in (s.get("callout") or {}).get("bullets", [])]
    s["archetype"] = "argument_only"
    s["blocks"] = s.get("blocks") or [{"text": "", "bullets": bullets}]
    return s


def a_argument_only(ctx, s, sl, n):
    """Words-led slide, composed with a visual per block (compose.py): stat figures, a process or icons."""
    from .compose import argument_layout
    argument_layout(ctx, s, sl, n)


# ------------------------------------------------------------------ archetypes
def a_cover(ctx, s, sl, n):
    """Opening (and end) slide: short accent bar, large serif title, subtitle, hairline, date line, firm label.
    The title stays in the layout's title placeholder so outline view and accessibility tools see it."""
    clean = lambda t: re.sub(r"\*\*|\^\w+", "", t or "")
    x, w = LEFT, colx24(17) - LEFT
    from .common import font_file, wrap_lines
    f = _font("head", True)
    size = next((z for z in ((44, 40, 36, 32, 28) if COVER_DARK else (36, 32, 28))
                 if wrap_lines(clean(s["title"]), w - 0.2, z, f, 0.97) <= 3), 28)
    lines = wrap_lines(clean(s["title"]), w - 0.2, size, f, 0.97)
    th = lines * _pitch(size, 1.05) / 72 + 0.3
    ty = (4.25 if COVER_DARK else 3.35) - th              # title block ends on a fixed line, whatever its length; on the
                                                           # dark cover the block sits at the optical middle of the page
    logo = BRAND.get("logo")                               # full-bleed navy: the title owns the page, no empty white;
    dark = COVER_DARK and not (logo and Path(logo).exists())   # a company logo drawn for white paper keeps the white cover
    ink, soft = (TC.BACKGROUND_1, TC.BACKGROUND_2) if dark else (TC.TEXT_1, TC.TEXT_2)
    if dark:
        bg = _fill(sl, 0, 0, 13.333, 7.5, TC.TEXT_2, "ov_CoverField")
        sp = bg._element
        sp.getparent().remove(sp)
        sl.shapes._spTree.insert(2, sp)                    # behind everything else on the page
    _fill(sl, x, ty - 0.32, 0.9, 0.06, TC.ACCENT_1, "CoverBar")
    t = sl.shapes.title
    t.left, t.top, t.width, t.height = Inches(x), Inches(ty), Inches(w + 0.5), Inches(th)   # slack past the wrap width
    tf = t.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.BOTTOM
    tf.margin_left = tf.margin_right = Inches(0)
    tf.text = clean(s["title"])
    ctx.track(n, clean(s["title"]), s.get("title_prov"))          # cover figures are sourced like any title (P13)
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.LEFT
        p.line_spacing = Pt(_pitch(size, 1.05))
        for r in p.runs:
            _set_font(r, size, ink, True, heading=True)
    sub = clean(s.get("subtitle"))
    tw, sw_ = set(re.findall(r"[a-z0-9$.]+", clean(s["title"]).lower())), re.findall(r"[a-z0-9$.]+", sub.lower())
    if sw_ and sum(w_ in tw for w_ in sw_) / len(sw_) > 0.6:
        sub = ""                                           # a subtitle that only repeats the title is dropped
    ctx.track(n, sub, s.get("subtitle_prov") or s.get("title_prov"))
    for ph in list(sl.placeholders):
        if ph.placeholder_format.idx == 1:
            if sub:
                ph.left, ph.top, ph.width, ph.height = Inches(x), Inches(ty + th + 0.15), Inches(w), Inches(1.0)
                ph.text_frame.word_wrap = True
                ph.text_frame.vertical_anchor = MSO_ANCHOR.TOP
                ph.text_frame.margin_left = Inches(0)
                ph.text = sub
                for p in ph.text_frame.paragraphs:
                    p.alignment = PP_ALIGN.LEFT
                    for r in p.runs:
                        _set_font(r, 16, soft)
            else:
                ph._element.getparent().remove(ph._element)
    _rule(sl, LEFT, 6.55, RIGHT - LEFT, TC.ACCENT_1 if dark else TC.ACCENT_5, 0.5, "CoverRule")
    if s.get("date"):
        _write(_box(sl, LEFT, 6.65, 6.0, 0.3, "Date"), [{"text": clean(s["date"]), "size": 11, "numbers": False,
                                                         "color": soft}])
    if not dark:                                           # a logo drawn for white paper stays off the dark field
        _logo(sl, RIGHT, 0.5, 0.55, 2.6, "ov_Logo", x_left=LEFT)
    _write(_box(sl, colx(9), 6.65, RIGHT - colx(9), 0.3, "Firm"),
           [{"text": s.get("firm") or BRAND.get("firm") or FIRM, "size": 11, "bold": True, "numbers": False,
             "align": PP_ALIGN.RIGHT, "color": ink}])


def _as_columns(ctx, s, sl, n, items, mode):
    """Items drawn as columns under their markers (compose: numbered chevrons or icons), the body's full height."""
    from .compose import _columns
    notes = s.get("footnotes") or []
    notes_h = (0.155 * max(1, (len("    ".join(notes)) // 170) + 1) + 0.05) if notes else 0
    bottom = FOOT_RULE_Y - 0.06 - notes_h if notes else BODY_B
    if notes:
        ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h
    y = BODY_Y + 0.1
    _columns(ctx, s, sl, n, items, mode, y, bottom - y)


def a_closing(ctx, s, sl, n):
    """Closing slide: the next steps as numbered rows (action, detail, owner/timing) spread over the body; next to a
    table-like slide (plan_forms), a numbered process: a chevron per step, its detail, then owner and timing."""
    _header(ctx, s, sl, n)
    steps = [st if isinstance(st, dict) else {"action": str(st)} for st in (s.get("steps") or [])][:6]
    if s.get("_form") == "columns" and 2 <= len(steps) <= 5:
        items = []
        for st in steps:
            bl = []
            if st.get("detail"):
                bl.append({"text": st["detail"], "prov": st.get("prov")})
            bl += [{"text": m if isinstance(m, str) else _txt(m), "prov": st.get("prov")}
                   for m in (st.get("milestones") or [])[:2]]
            who = [v for v in (st.get("owner"), st.get("timing")) if v]
            items.append({"lead": str(st.get("action", "")).replace("**", ""), "bullets": bl,
                          "prov": st.get("prov"), "foot": who})
        _as_columns(ctx, s, sl, n, items, "process")
        return
    has_when = any(st.get("owner") or st.get("timing") for st in steps)
    nx = LEFT
    ax, aw = span24(1, 7)
    dx, dw = (span24(8, 18) if has_when else span24(8, 23))
    wx, ww = span24(19, 23)
    y0 = TITLE_RULE_Y + 0.06
    heads = [(ax, aw, s.get("columns", ["Action"])[0] if s.get("columns") else "Next step"),
             (dx, dw, "What it involves")] + ([(wx, ww, "Owner and timing")] if has_when else [])
    for x, w, t in heads:
        _write(_box(sl, x, y0, w, 0.3, f"role_subtitle_Head{len(t)}", MSO_ANCHOR.BOTTOM),
               [{"text": t, "size": T["head"], "bold": True, "numbers": False}])
    _rule(sl, LEFT, y0 + 0.36, RIGHT - LEFT, TC.ACCENT_1, 0.75, "HeadRule")
    top, bottom = y0 + 0.46, FOOT_RULE_Y - 0.1
    notes = s.get("footnotes") or []
    if notes:
        notes_h = 0.155 * max(1, (len("    ".join(notes)) // 170) + 1) + 0.05
        bottom -= notes_h + 0.06
        ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h

    def cells(st, size):
        a = [{"text": st.get("action", ""), "size": size + (4 if size > T["body"] else 2), "bold": True, "numbers": False,
              "prov": st.get("prov")}]                         # an action that names an amount cites it (P13)
        d = [{"text": st.get("detail", ""), "size": size, "prov": st.get("prov")}] if st.get("detail") else []
        d += [{"text": m if isinstance(m, str) else _txt(m), "size": size - 1, "bullet": True, "space_before": 3,
               "prov": st.get("prov")} for m in (st.get("milestones") or [])[:3]]
        wh = [{"text": " · ".join(v for v in (st.get("owner"), st.get("timing")) if v), "size": size,
               "numbers": False, "color": TC.TEXT_2}] if has_when else []
        return a, d, wh
    size = T["body"] + (2 if len(steps) <= 3 else 0)          # few steps: larger type before larger gaps
    keep_ms = 3
    while True:
        steps_ = [dict(st, milestones=(st.get("milestones") or [])[:keep_ms]) for st in steps]
        laid = [cells(st, size) for st in steps_]
        need = [max(_need(a, aw), _need(d, dw) if d else 0, _need(wh, ww) if wh else 0, 0.4) + 0.12 for a, d, wh in laid]
        if sum(need) <= bottom - top:
            break
        if size > 10:
            size -= 1
        elif keep_ms > 0:                                    # at the floor: milestones go to the notes, not off-page
            keep_ms -= 1
        else:
            break
    if keep_ms == 3:                                         # room to spare: larger type before larger gaps
        for z in range(size + 1, size + 4):
            bigger = [cells(st, z) for st in steps_]
            nb = [max(_need(a, aw), _need(d, dw) if d else 0, _need(wh, ww) if wh else 0, 0.4) + 0.12
                  for a, d, wh in bigger]
            if sum(nb) > 0.8 * (bottom - top):
                break
            size, laid, need = z, bigger, nb
    if keep_ms < 3:
        cut = [f"{st.get('action')}: " + "; ".join((st.get("milestones") or [])[keep_ms:]) for st in steps
               if (st.get("milestones") or [])[keep_ms:]]
        s["notes"] = (s.get("notes", "") + "\nMilestones not shown: " + " | ".join(cut)).strip()
    if sum(need) > bottom - top:
        ctx.miss(n, "the next steps", int((sum(need) - (bottom - top)) * 30))
    gap = max(0.0, (bottom - top - sum(need)) / max(1, len(steps)))   # the rows share the body: no block of white
    y = top                                                           # under the last step
    for i, ((a, d, wh), h) in enumerate(zip(laid, need)):
        rh = h + gap
        if i % 2 == 0 and gap > 0.15:                                 # alternate rows on a light band
            _fill(sl, LEFT, y, RIGHT - LEFT, rh - 0.02, TC.BACKGROUND_2, f"ov_RowBand{i + 1}")
        mid = MSO_ANCHOR.MIDDLE if gap > 0.15 else MSO_ANCHOR.TOP       # a tall row centres its content
        _write(_box(sl, nx, y + 0.04, 0.5, rh - 0.1, f"role_numeral_{i + 1}", mid),
               [{"text": str(i + 1), "size": T["num"], "bold": True, "heading": True, "numbers": False,
                 "color": TC.ACCENT_1}])
        _write(_box(sl, ax, y + 0.04, aw, rh - 0.1, f"Action{i + 1}", mid), a, ctx, n)
        if d:
            _write(_box(sl, dx, y + 0.04, dw, rh - 0.1, f"Detail{i + 1}", mid), d, ctx, n)
        if wh:
            _write(_box(sl, wx, y + 0.04, ww, rh - 0.1, f"When{i + 1}", mid), wh, ctx, n)
        y += rh
        if i < len(steps) - 1:
            _rule(sl, ax, y - 0.02, RIGHT - ax, TC.ACCENT_5, 0.5, f"StepRule{i + 1}")


def a_challenge_matrix(ctx, s, sl, n):
    """Numbered rows (serif numeral, line icon, two-weight label) x 2 columns of lead + bullets, hairlines; next to a
    table-like slide (plan_forms), one icon column per row: its label, then each lens's lead and evidence."""
    _header(ctx, s, sl, n)
    rows_ = [r for r in s.get("rows") or [] if isinstance(r, dict)]
    if s.get("_form") == "columns" and 2 <= len(rows_) <= 4:
        heads = [h for h in (s.get("columns") or [])[1:3]] + ["", ""]
        items = []
        for r in rows_:
            bl = []
            for k, c in enumerate((r.get("cells") or [])[:2]):
                c = c if isinstance(c, dict) else {"lead": str(c)}
                head = re.sub(r"^[A-Z]\s*[-–]\s*", "", str(heads[k] or "")).strip()
                head = head if len(head.split()) <= 3 else ""        # "Today", "Action"; a long lens name goes
                lead_ = str(c.get("lead") or "")
                if lead_ and not repeats(lead_, r.get("label", ""), 0.7):   # a lead that repeats the label goes
                    bl.append({"text": (f"**{head}:** " if head else "") + lead_, "prov": c.get("prov")})
                for b in (c.get("bullets") or [])[:1]:
                    b = b if isinstance(b, dict) else {"text": str(b)}
                    if not repeats(b["text"], c.get("lead", ""), 0.7):     # a line that repeats its lead goes
                        bl.append({"text": b["text"], "prov": b.get("prov")})
            items.append({"lead": str(r.get("label", "")).replace("**", ""), "bullets": bl,
                          "prov": r.get("prov")})
        _as_columns(ctx, s, sl, n, items, "icons")
        return
    lx, lw = span24(0, 5)
    ax, aw = span24(6, 14)
    bx, bw = span24(15, 23)
    heads = (s.get("columns") or ["", "", ""]) + ["", "", ""]
    y0 = TITLE_RULE_Y + 0.06                         # the reference starts the column heads right under the rule
    for x, w, t in ((lx, lw, heads[0]), (ax, aw, heads[1]), (bx, bw, heads[2])):
        _write(_box(sl, x, y0, w, 0.3, f"role_subtitle_Head{len(t)}", MSO_ANCHOR.BOTTOM),
               [{"text": t, "size": DENSE["head"], "bold": True, "numbers": False}])
    _rule(sl, LEFT, y0 + 0.36, RIGHT - LEFT, TC.ACCENT_1, 0.75, "HeadRule")
    rows = s.get("rows") or []

    def blocks(c):
        bl = []
        if c.get("lead"):
            bl.append({"text": c["lead"], "size": DENSE["lead"], "bold": True, "numbers": False, "prov": c.get("prov")})
        for b in c.get("bullets") or []:
            b = b if isinstance(b, dict) else {"text": b}
            bl.append({"text": b["text"], "size": DENSE["cell"], "bullet": True, "space_before": 3, "prov": b.get("prov")})
        return bl
    top = y0 + 0.42
    avail = FOOT_RULE_Y - 0.06 - top                 # rows run to the source rule; footnotes sit in the label column
    notes_len = len(" ".join(s.get("footnotes") or []))
    if s.get("footnotes") and notes_len <= 90:
        ctx.label_col_notes = (lx, lw)
    elif s.get("footnotes"):                         # long notes take a full-width band above the source rule
        avail -= 0.14 * (notes_len // 200 + 1) + 0.06
        ctx.notes_top = FOOT_RULE_Y - 0.03 - (0.14 * (notes_len // 200 + 1) + 0.04)

    def fit_rows(rs, size):
        out = []
        for r in rs:
            cells = (r.get("cells") or []) + [{}, {}]
            ba, bb = blocks(cells[0]), blocks(cells[1])
            for b in ba + bb:
                b["size"] = size
            lab = [{"text": r.get("label", ""), "size": size, "bold": True}]
            out.append((ba, bb, max(_need(ba, aw, 0.94), _need(bb, bw, 0.94), _need(lab, lw - 0.85, 0.94), 0.45) + 0.08))
        return out
    size = DENSE["cell"]
    laid = fit_rows(rows, size)
    while sum(x[2] for x in laid) > avail and size > s.get("min_size", 10):
        size -= 1
        laid = fit_rows(rows, size)
    grow_to = T["cell"] + (2 * DENSITY["bump"] if DENSITY["mode"] == "read" else max(4, 2 * DENSITY["bump"]))
    while size < grow_to:                                  # short content: larger type before larger gaps (a dense
                                                           # send-ahead keeps its type scale)
        bigger = fit_rows(rows, size + 1)
        if sum(x[2] for x in bigger) > avail * 0.97:
            break
        size, laid = size + 1, bigger
    keep = len(rows)
    while keep > 1 and sum(x[2] for x in laid[:keep]) > avail:
        keep -= 1                                          # carry the rest to a continuation slide
    if keep < len(rows):
        ctx.carry = dict(s, rows=rows[keep:], title=_cont_title(s["title"]), number_from=s.get("number_from", 1) + keep,
                         _cont=True)
    gap = max(0.0, (avail - sum(x[2] for x in laid[:keep])) / max(1, keep))   # the rows share the body
    y = top
    first = s.get("number_from", 1)
    for i, (r, (ba, bb, need)) in enumerate(zip(rows[:keep], laid[:keep])):
        rh = need + gap
        if i % 2 == 0 and gap > 0.15:                                 # alternate rows on a light band
            _fill(sl, LEFT, y, RIGHT - LEFT, rh, TC.BACKGROUND_2, f"ov_RowBand{i + 1}")
        mid = gap > 0.15                                      # a tall row centres its content
        lab_h = min(rh - 0.1, _need([{"text": r.get("label", ""), "size": size, "bold": True}], lw - 0.85) + 0.06)
        top_ = y + ((rh - max(lab_h, 0.5)) / 2 if mid else 0.0)
        _write(_box(sl, lx, top_, 0.42, 0.5, f"role_numeral_{i + 1}"),
               [{"text": str(first + i), "size": DENSE["num"], "bold": True, "heading": True, "numbers": False}])
        if r.get("icon"):
            _icon(sl, r["icon"], lx + 0.36, top_ + 0.03, 0.36, re.sub(r"\*\*", "", r.get("label", "")))
        _write(_box(sl, lx + 0.85, top_ + 0.04, lw - 0.85, lab_h, f"Label{i + 1}"),
               [{"text": r.get("label", ""), "size": size, "numbers": False}])
        anc = MSO_ANCHOR.MIDDLE if mid else MSO_ANCHOR.TOP
        _write(_box(sl, ax, y + 0.06, aw, rh - 0.1, f"A{i + 1}", anc), ba, ctx, n)
        _write(_box(sl, bx, y + 0.06, bw, rh - 0.1, f"B{i + 1}", anc), bb, ctx, n)
        y += rh
        if i < keep - 1:
            _rule(sl, lx + 0.85, y, RIGHT - lx - 0.85, TC.ACCENT_5, 0.5, f"RowRule{i + 1}")


def sowhat_dark(ctx) -> bool:
    """The deck's so-what boxes alternate: navy, then light with an accent bar (BCG keeps its dark box rare)."""
    k = getattr(ctx, "_sowhats", 0)
    ctx._sowhats = k + 1
    return k % 2 == 0


def a_exhibit_argument(ctx, s, sl, n):
    """Exhibit on white (left); the argument on a grey half-panel (right) ending in a navy 'so what' with an arrow."""
    ctx.last_side = n                                   # a chart slide after it takes the other composition
    lxx, lw = span24(0, 12)
    split = colx24(13) + 0.05
    panel = _fill(sl, split, TITLE_RULE_Y + 0.02, 13.333 - split, FOOT_RULE_Y - 0.08 - TITLE_RULE_Y,   # under the
                  TC.BACKGROUND_2, "ov_ArgumentPanel")                                              # white top
    _header(ctx, s, sl, n)                              # the standard frame: a full-width title, its rule and the
    rx, rw = span24(14, 23)                             # section number, like every content slide
    tr = s.get("title_right", "")
    pt_y = BODY_Y + 0.08                                # where the argument starts in the panel
    if tr:                                              # the panel's heading inside the panel, level with the
        hb_ = [{"text": tr, "size": ARGUMENT["exhibit"], "bold": True, "heading": True, "color": TC.ACCENT_1,
                "numbers": False, "line_spacing": 1.0}]   # exhibit's heading opposite (BCG: one baseline per row)
        hh_ = _need(hb_, rw) + 0.08
        _write(_box(sl, rx, BODY_Y - 0.05, rw, hh_, "role_subtitle_Right"), hb_)
        pt_y = BODY_Y - 0.05 + hh_ + 0.1
    ex = s.get("exhibit") or {}
    y = BODY_Y - 0.05
    if ex.get("title"):
        tb_ = [{"text": ex["title"], "size": ARGUMENT["exhibit"], "bold": True, "numbers": False}]
        if _need(tb_, lw * 0.88) > 0.4:                   # a long exhibit title steps down before it wraps
            tb_[0]["size"] = ARGUMENT["exhibit"] - 2
        th_ = max(0.32, _need(tb_, lw * 0.88) + 0.06)      # measured narrower: the renderer's face sets bold a little
                                                           # wider, and the unit line under a wrapped title never runs
                                                           # into it
        _write(_box(sl, LEFT, y, lw, th_, "role_subtitle_ExhibitTitle"), tb_)
        y += th_
    if ex.get("unit"):
        ub_ = [{"text": ex["unit"], "size": ARGUMENT["unit"], "numbers": False}]
        uh_ = max(0.26, _need(ub_, lw) + 0.02)
        _write(_box(sl, LEFT, y, lw, uh_, "role_subtitle_ExhibitUnit"), ub_)
        y += uh_ + 0.02
    if ex.get("title") or ex.get("unit"):              # the exhibit's subtitle carries the line, not the titles
        _rule(sl, LEFT, y, lw, TC.TEXT_1, 0.75, "SubtitleRule")
        y += 0.06
    notes = s.get("footnotes") or []                   # the exhibit runs down to the footnotes' first line
    foot = BODY_B - (FOOT_RULE_Y - 0.08 - _notes_h(notes)) if notes else 0
    if notes:
        ctx.notes_top = FOOT_RULE_Y - 0.03 - _notes_h(notes)
    _chart(ctx, sl, n, s["chart"], LEFT, y, lw, BODY_B - y - foot)
    blocks = []
    for blk in s.get("blocks") or []:
        blocks.append({"text": blk["text"], "size": ARGUMENT["intro"], "numbers": False, "space_before": 8, "prov": blk.get("prov")})
        for b in blk.get("bullets") or []:
            b = b if isinstance(b, dict) else {"text": b}
            blocks.append({"text": b["text"], "size": ARGUMENT["bullet"], "bullet": True, "level": 1, "space_before": 3,
                           "prov": b.get("prov")})
    if blocks:
        blocks[0]["space_before"] = 0
    co = dict(s.get("callout") or {})
    said = " ".join(str(b_.get("text") if isinstance(b_, dict) else b_) for blk in s.get("blocks") or []
                    if isinstance(blk, dict) for b_ in [blk] + list(blk.get("bullets") or []))
    if co.get("bullets") and said:                    # a so-what that repeats the points says nothing new
        keep_ = [b_ for b_ in co["bullets"] if not repeats(str(b_.get("text") if isinstance(b_, dict) else b_),
                                                            said, 0.75)]
        co["bullets"] = keep_
    one_ = len(co.get("bullets") or []) == 1             # a single so-what line carries no bullet glyph
    cb = [{"text": b["text"] if isinstance(b, dict) else b, "size": ARGUMENT["callout"], "bullet": not one_,
           "color": TC.BACKGROUND_1, "numbers": False, "space_before": 5,
           "prov": b.get("prov") if isinstance(b, dict) else None} for b in co.get("bullets") or []]
    if cb:                                             # the so-what keeps readable type (12 pt at least): a line
        cb, cut_ = _trim_to_fit(cb, rw - 0.3, 1.3, 12)  # that does not fit goes to the notes
        _cut_note(s, cut_)
    ch = (_need(cb, rw - 0.3) + 0.28) if cb else 0
    cy = BODY_B - foot - ch
    space = cy - pt_y - 0.04
    fits_points = bool(draw_points(ctx, s, sl, n, rx, pt_y, rw, cy - 0.15 - (pt_y),
                                   s.get("blocks"), dry=True))
    blocks, ok = _fit(blocks, rw - 0.1, space, 10)
    if not ok:
        ctx.miss(n, "the argument column", max(5, int(sum(len(b["text"].split()) for b in blocks) * 0.15)))
        blocks, dropped = _trim_to_fit(blocks, rw - 0.1, space, 10)
        _cut_note(s, dropped)
        ok = True
    fitted = min((b["size"] for b in blocks if b.get("bullet")), default=ARGUMENT["bullet"])
    if not fits_points:                                # the text column shrank: the callout follows, never under 12
        for b in cb:
            b["size"] = max(12, min(b["size"], fitted))
    if cb:                                             # callout shrinks with the argument: re-measure and re-seat it
        ch = _need(cb, rw - 0.3) + 0.28
        cy = BODY_B - foot - ch
    if not ok:
        ctx.miss(n, "the argument column", max(5, int(sum(len(b["text"].split()) for b in blocks) * 0.15)))
    if not (POINTS["on"] and fits_points and
            draw_points(ctx, s, sl, n, rx, pt_y, rw, cy - 0.15 - (pt_y), s.get("blocks"))):
        _shape_text(panel, blocks, ctx, n, (rx, pt_y, rw, space - 0.13))   # the argument lives in the panel
    if cb:
        aw_ = max(0.35, rx - split + 0.03)               # the arrow lives in the gutter: never over the chart's labels
        if sowhat_dark(ctx):
            arr = _fill(sl, rx - aw_ + 0.03, cy + ch / 2 - 0.3, aw_, 0.6, TC.TEXT_2, "ov_SoWhatArrow",
                        MSO_SHAPE.PENTAGON)
            arr.rotation = 180
            box = _fill(sl, rx, cy, rw, ch, TC.TEXT_2, "ov_SoWhat")
        else:                                            # every other so-what: a white box with an accent bar and
            for b_ in cb:                                # dark text (a navy box on every slide reads as one template)
                b_["color"] = TC.TEXT_1
            box = _fill(sl, rx, cy, rw, ch, TC.BACKGROUND_1, "ov_SoWhat")
            _fill(sl, rx, cy, 0.07, ch, TC.ACCENT_1, "ov_SoWhatBar")
        _shape_text(box, cb, ctx, n, (rx + 0.12, cy + 0.06, rw - 0.2, ch - 0.1), MSO_ANCHOR.MIDDLE)


POINT_PAD, POINT_GAP, POINT_MARK = 0.15, 0.12, 0.42


def point_items(blocks) -> tuple[str, list[dict]]:
    """(intro, points) of an argument column: one point per block (its text the lead, its bullets the detail); a
    single block with 2+ bullets makes its text the intro and each bullet a point."""
    norm = lambda b: b if isinstance(b, dict) else {"text": str(b)}  # noqa: E731
    blocks = [norm(b) for b in blocks or [] if b]
    blocks = [b for b in blocks if (b.get("text") or "").strip() or b.get("bullets")]
    if len(blocks) == 1 and len(blocks[0].get("bullets") or []) >= 2:
        return blocks[0], [{"lead": norm(x)["text"], "bullets": [], "prov": norm(x).get("prov"),
                                              "label": norm(x).get("label") or ""} for x in blocks[0]["bullets"]]
    out = []
    for b in blocks:
        bl = [norm(x) for x in b.get("bullets") or []]
        lead = b.get("text") or (bl.pop(0)["text"] if bl else "")
        out.append({"lead": lead, "bullets": bl, "prov": b.get("prov"), "label": (b.get("label") or "").strip()})
    return "", out


def _point_blocks(it, lead_size, det_size):
    """A point's paragraphs: its header (when it has one) in the accent colour, then the lead (bold without a
    header), then the detail lines."""
    out = []
    if it.get("label"):
        out.append({"text": it["label"], "size": lead_size, "bold": True, "numbers": False, "color": TC.TEXT_2})
    head = not it.get("label") and bool(it["bullets"])         # a lead over detail lines is the point's header
    alone = not it.get("label") and not it["bullets"]          # a one-line point reads at the lead size
    out.append({"text": it["lead"], "size": lead_size if head or alone else det_size, "bold": head, "numbers": False,
                "prov": it.get("prov"), "space_before": 2 if it.get("label") else 0})
    for b in it["bullets"]:
        out.append({"text": b["text"], "size": det_size, "bullet": True, "space_before": 3, "prov": b.get("prov")})
    return out


def _point_marker(ctx, sl, n, j, icon, x, y, d, meaning):
    if icon:
        _icon(sl, icon, x, y, d, meaning)
        return
    b = _fill(sl, x, y, d, d, TC.ACCENT_1, f"ov_PointBadge{j + 1}", MSO_SHAPE.OVAL)
    _shape_text(b, [{"text": str(j + 1), "size": T["body"], "bold": True, "numbers": False,
                     "color": TC.BACKGROUND_1, "align": PP_ALIGN.CENTER}], ctx, n, (x, y, d, d), MSO_ANCHOR.MIDDLE)


def draw_points(ctx, s, sl, n, x, y, w, h, blocks, card=TC.BACKGROUND_1, dry=False, fill=True,
                layout="auto") -> float:
    """An argument column drawn as designed points, not a bullet list: 2-4 cards, each with an icon chosen by its
    meaning (numbered badges when not every point has one), a bold lead and its detail. Four short points make a 2x2
    of boxes; otherwise the cards stack and share the height. A one-block column's text is the intro line above them.
    Returns the height used, or 0 when the points do not fit at the 10 pt floor or there are not 2-4 of them (the
    caller then writes the column as text). Nothing is cut."""
    from .compose import MAX_ICONS, item_icons, plain
    intro, items = point_items(blocks)
    if not 2 <= len(items) <= 4 or w < 2.6:
        return 0.0
    icons = item_icons([dict(it, lead=f"{it.get('label', '')} {it['lead']}") for it in items])
    if not all(icons) or len(items) > MAX_ICONS:
        icons = [None] * len(items)
    ib, ih = [], 0.0
    if isinstance(intro, dict):                                # the intro line keeps its sources (P13)
        intro, intro_prov = intro.get("text") or "", intro.get("prov")
    else:
        intro_prov = None
    if intro:
        ib = [{"text": intro, "size": ARGUMENT["intro"], "bold": True, "numbers": False, "prov": intro_prov}]
        ih = _need(ib, w) + 0.1
    room = h - ih
    d = POINT_MARK
    short = all(len(plain(it["lead"]).split()) <= 16 and sum(len(plain(b["text"]).split()) for b in it["bullets"]) <= 14
                for it in items)
    sizes = ((ARGUMENT["intro"], ARGUMENT["bullet"]), (ARGUMENT["bullet"], ARGUMENT["bullet"]),
             (ARGUMENT["bullet"], 13), (13, 12), (12, 12))      # never under 12 pt: detail goes to the notes first
    plan = None
    if layout == "row":                                        # a row of cards under a full-width exhibit
        k = len(items)
        cw = (w - POINT_GAP * (k - 1)) / k
        tw = cw - 2 * POINT_PAD
        for drop in (False, True):
            its = [dict(it, bullets=[]) for it in items] if drop else items
            for ls, ds in sizes:
                need = max(_need(_point_blocks(it, ls, ds), tw) for it in its) + d + 0.1 + 2 * POINT_PAD
                if need <= room:
                    plan = ("row", ls, ds, need, its)
                    break
            if plan:
                break
        if plan is None:
            return 0.0
        if dry:
            return h
        if ib:
            _write(_box(sl, x, y, w, ih, "ov_PointIntro"), ib, ctx, n)
        _, ls, ds, _, its = plan
        if its is not items:
            _cut_note(s, [b["text"] for it in items for b in it["bullets"]])
        y0 = y + ih
        for j, it in enumerate(its):
            cx = x + j * (cw + POINT_GAP)
            box = _fill(sl, cx, y0, cw, room, card, f"ov_PointCard{j + 1}")
            ty = y0 + POINT_PAD + d + 0.1
            _shape_text(box, _point_blocks(it, ls, ds), ctx, n, (cx + POINT_PAD, ty, tw, y0 + room - POINT_PAD - ty),
                        MSO_ANCHOR.TOP)
            _point_marker(ctx, sl, n, j, icons[j], cx + POINT_PAD, y0 + POINT_PAD, d, plain(it["lead"])[:60])
        return h
    if len(items) == 4 and short and w >= 4.2:                 # four short points: a 2x2 of boxes
        cw, chh = (w - POINT_GAP) / 2, (room - POINT_GAP) / 2
        tw = cw - 2 * POINT_PAD
        for ls, ds in sizes:
            need = max(_need(_point_blocks(it, ls, ds), tw) for it in items) + d + 0.1 + 2 * POINT_PAD
            if need <= chh:
                plan = ("grid", ls, ds, need)
                break
    dropped_detail = False
    if plan is None:                                           # stacked cards, marker on the left
        tw = w - 2 * POINT_PAD - d - 0.12
        for drop in (False, True):
            its = [dict(it, bullets=[]) for it in items] if drop else items
            for ls, ds in sizes:
                hs = [max(_need(_point_blocks(it, ls, ds), tw) + 0.22, d + 0.26) for it in its]
                if sum(hs) + POINT_GAP * (len(its) - 1) <= room:
                    plan = ("stack", ls, ds, hs)
                    break
            if plan:
                if drop and not dry:
                    _cut_note(s, [b["text"] for it in items for b in it["bullets"]])
                items, dropped_detail = its, drop
                break
    if plan is None:
        return 0.0
    if dry:
        return h
    if ib:
        _write(_box(sl, x, y, w, ih, "ov_PointIntro"), ib, ctx, n)
    y0 = y + ih
    kind, ls, ds = plan[0], plan[1], plan[2]
    if kind == "grid":
        cw, chh = (w - POINT_GAP) / 2, (room - POINT_GAP) / 2
        for j, it in enumerate(items):
            cx, cy = x + (j % 2) * (cw + POINT_GAP), y0 + (j // 2) * (chh + POINT_GAP)
            box = _fill(sl, cx, cy, cw, chh, card, f"ov_PointCard{j + 1}")
            ty = cy + POINT_PAD + d + 0.1                      # the text is written into the card itself (P17)
            _shape_text(box, _point_blocks(it, ls, ds), ctx, n,
                        (cx + POINT_PAD, ty, cw - 2 * POINT_PAD, cy + chh - POINT_PAD - ty), MSO_ANCHOR.TOP)
            _point_marker(ctx, sl, n, j, icons[j], cx + POINT_PAD, cy + POINT_PAD, d, plain(it["lead"])[:60])
        return ih + room
    hs = plan[3]
    spare = room - sum(hs) - POINT_GAP * (len(items) - 1)
    grow = spare / len(items) if fill else min(spare / len(items), 0.6)   # the cards share the column's height:
                                                                          # the column ends where its neighbour does
    k_ = getattr(ctx, "_point_slides", 0)                  # the deck alternates its point columns: boxed cards,
    ctx._point_slides = k_ + 1                             # then open rows between hairlines (BCG varies the two;
    open_ = k_ % 2 == 1                                    # cards on every slide read as one template)
    cy = y0
    for j, (it, hh) in enumerate(zip(items, hs)):
        hh += grow
        box = _fill(sl, x, cy, w, hh, card, f"ov_PointCard{j + 1}")
        if open_:
            box.fill.background()
            if j < len(items) - 1:
                _rule(sl, x, cy + hh + POINT_GAP / 2, w, TC.ACCENT_5, 0.75, f"ov_PointRule{j + 1}")
        tx = x + POINT_PAD + d + 0.12                          # the text is written into the card itself (P17)
        _shape_text(box, _point_blocks(it, ls, ds), ctx, n, (tx, cy + 0.08, x + w - POINT_PAD - tx, hh - 0.16),
                    MSO_ANCHOR.MIDDLE)
        _point_marker(ctx, sl, n, j, icons[j], x + POINT_PAD, cy + (hh - d) / 2, d, plain(it["lead"])[:60])
        cy += hh + POINT_GAP
    return cy - POINT_GAP - y


_NUMCELL = re.compile(r"^\s*([+\-−]?)\s*([$€£]?)\s*(\d[\d,]*(?:\.\d+)?)\s*(%|pp|x|[KMBT]|bn|m)?\s*$", re.I)


def _table_as_chart(t: dict):
    """A small table whose value columns are numbers in one unit -> clustered bar/column chart data (or None)."""
    rows = t.get("rows") or []
    if t.get("total_row") and rows:                    # a total is not a category: it would dwarf the bars
        rows = rows[:-1]
    cols = t.get("columns") or []
    if not 2 <= len(rows) <= 6 or not 2 <= len(cols) <= 4:    # at most 3 value columns: nothing is left out
        return None
    units, series = set(), []
    for ci in range(1, len(cols)):
        parsed = []
        for r in rows:
            m = _NUMCELL.match(_plain(r[ci]).replace("−", "-")) if ci < len(r) else None
            if not m:
                return None                            # a text or blank cell: the table says more than a chart
            sign, cur, num, unit = m.groups()
            parsed.append((-1 if sign in ("-", "−") else 1) * float(num.replace(",", "")))
            units.add((cur or "") + (unit or "").lower())
        series.append({"name": _plain(cols[ci]), "values": parsed})
    if len(units) != 1:                                # one chart, one unit; otherwise keep the table
        return None
    units = units.pop()
    cats = [_plain(r[0]) for r in rows]
    kind = "bar" if max(len(c) for c in cats) > 14 else "column"
    decimals = _decimals([v for se in series for v in se["values"]])
    hl = [cats[i] for i in t.get("highlight_rows") or [] if i < len(cats)] if len(series) == 1 else []
    return {"kind": kind, "categories": cats, "series": series, "unit": units, "decimals": decimals,
            "highlight_categories": hl, "alt": f"{kind} chart: " + "; ".join(se["name"] for se in series)}


def _trim_to_fit(blocks, w, h, floor):
    """Fit at or above `floor`; if the text still does not fit, drop trailing lines (bullets first) until it does.
    Returns (blocks, dropped texts). A finished slide never overflows; what was cut goes to the notes."""
    bl, ok = _fit(blocks, w, h, floor)
    dropped = []
    while not ok and len(bl) > 1:
        k = max((i for i, b in enumerate(bl) if b.get("bullet")), default=len(bl) - 1)
        dropped.insert(0, bl.pop(k)["text"])
        bl, ok = _fit(bl, w, h, floor)
    return bl, dropped


def _cut_note(s, dropped):
    if dropped:
        s["notes"] = (s.get("notes", "") + "\n[QA] Did not fit and was left out: " + " | ".join(dropped)).strip()


def _grow(blocks, w, h, max_bump=2):
    """Short text steps up the type scale (at most two points) instead of leaving the column half empty."""
    import copy
    best = blocks
    for bump in range(1, max_bump + 1):
        trial = copy.deepcopy(blocks)
        for b in trial:
            b["size"] = b.get("size", T["body"]) + bump
        if _need(trial, w) > h * 0.9:
            break
        best = trial
    return best


PANEL_PAD = 0.18
THIN = 0.10               # a composition that leaves more than this share of the body empty is thin: it grows
RHYTHM_MAX = 0.55         # the largest gap between two blocks when a thin slide spreads its rhythm
TABLE_PADS = (0.16,)      # air a table row may take past its text (P20 allows 0.25in, measured on Arial)


def _fill_text(blocks, w, h, max_bump=5):
    """Grow text to use its box: up to max_bump points, then more space between paragraphs, while it fits 92%."""
    import copy
    best = copy.deepcopy(blocks)
    for step in (0, 1):                                    # one step up the type scale at most (P4): bullets body ->
        trial = copy.deepcopy(blocks)                      # head, leads head -> sub; no in-between sizes
        for b in trial:
            lead_ = b.get("bold") and not b.get("bullet")
            b["size"] = ((T["head"], T["sub"]) if lead_ else (T["body"], T["head"]))[step]
        if _need(trial, w) > 0.92 * h:
            break
        best = trial
    for gap in (8, 10, 12, 14, 16):
        trial = copy.deepcopy(best)
        for b in trial[1:]:
            b["space_before"] = max(b.get("space_before", 0), gap)
        if _need(trial, w) > 0.92 * h:
            break
        best = trial
    return best


PANEL_FILL = 0.8          # a panel's text fills at least this share of its interior: it grows, then the panel shrinks


def _panel(ctx, s, sl, n, x, y, w, h, lead, bullets, name="ov_Panel", dry=False, anchor="top", grow=True,
           shrink=False):
    """The so-what panel: a shaded box holding the takeaway (bold, full width) and the bullets (one or two columns).
    The text first steps up the type scale (and paragraph spacing) to use the room it has; with shrink=True the panel
    then shrinks to its text, so it never shows an empty interior: `h` is the most it may take, not what it takes. anchor "bottom"
    keeps the panel's foot at y + h (a band under an exhibit), "top" keeps its head at y. Returns the height drawn
    (with dry=True nothing is drawn: the composition is measured first). Text that cannot fit goes to the notes."""
    if h < 0.35 or not (lead or bullets):
        return 0.0
    iw = w - 2 * PANEL_PAD
    two = len(bullets) >= 3 and iw > 6
    if not two:
        blocks, dropped = _trim_to_fit(lead + bullets, iw, h - 0.24, 10)
        blocks = _fill_text(blocks, iw, h - 0.24) if grow else blocks
        ph = min(h, _need(blocks, iw) / 0.95 + 0.24) if (shrink or dry) else h
        if dry:
            return ph
        _cut_note(s, dropped)
        y0 = y + h - ph if anchor == "bottom" else y
        box = _fill(sl, x, y0, w, ph, TC.BACKGROUND_2, name)
        _shape_text(box, blocks, ctx, n, (x + PANEL_PAD, y0 + 0.12, iw, ph - 0.24), MSO_ANCHOR.MIDDLE)
        return ph
    lh = 0.0
    if lead:
        lead = [dict(b, size=T["head"]) for b in lead]
        lh = min(_need(lead, iw) + 0.18, 0.35 * h)
    cw = w / 2
    bh = h - lh
    icw, ibh = cw - 1.5 * PANEL_PAD, bh - 0.24
    cols = [bullets[: (len(bullets) + 1) // 2], bullets[(len(bullets) + 1) // 2:]]
    fitted, dropped = [], []
    for c in cols:
        c2, d = _trim_to_fit(c, icw, ibh, 10)
        fitted.append(c2)
        dropped += d
    bump_ok = 0                                        # both columns grow by the same step, on the type scale
    for bump in (0, T["head"] - T["body"]) if grow else (0,):
        if all(_need([dict(b, size=b.get("size", T["body"]) + bump) for b in c], icw) <= 0.92 * ibh for c in fitted):
            bump_ok = bump
        else:
            break
    fitted = [[dict(b, size=b.get("size", T["body"]) + bump_ok) for b in c] for c in fitted]
    if shrink or dry:
        bh = min(bh, max(_need(c, icw) for c in fitted) / 0.95 + 0.24)
    ph = lh + bh
    if dry:
        return ph
    _cut_note(s, dropped)
    y0 = y + h - ph if anchor == "bottom" else y
    if lead:
        top = _fill(sl, x, y0, w, lh, TC.BACKGROUND_2, name + "Lead")
        _shape_text(top, lead, ctx, n, (x + PANEL_PAD, y0 + 0.12, iw, lh - 0.12), MSO_ANCHOR.BOTTOM)
    for i, c in enumerate(fitted):
        cx = x + i * cw
        box = _fill(sl, cx, y0 + lh, cw, bh, TC.BACKGROUND_2, f"{name}Col{i + 1}")
        _shape_text(box, c, ctx, n, (cx + (PANEL_PAD if i == 0 else PANEL_PAD / 2), y0 + lh + 0.12, icw, bh - 0.24),
                    MSO_ANCHOR.MIDDLE)
    return ph


def a_table_focus(ctx, s, sl, n):
    _header(ctx, s, sl, n)
    t = s["table"]
    y = TITLE_RULE_Y + 0.12
    if s.get("legend"):
        _write(_box(sl, colx(6), y, RIGHT - colx(6), 0.3, "role_subtitle_Legend"),
               [{"text": s["legend"], "size": T["label"], "numbers": False, "align": PP_ALIGN.RIGHT}])
        y += 0.36
    tk = s.get("takeaway") or {}
    notes = s.get("footnotes") or []
    notes_h = (0.155 * max(1, (len("    ".join(notes)) // 170) + 1) + 0.05) if notes else 0
    tk_h = 0.34 if tk.get("text") else 0
    bottom = FOOT_RULE_Y - 0.04 - notes_h - tk_h
    ctx.carry_rows = None
    t = dict(t)
    bullets = [b if isinstance(b, dict) else {"text": b} for b in s.get("bullets") or []]
    natural = _table(ctx, sl, n, dict(t), LEFT, y, RIGHT - LEFT, bottom - y, dry=True)
    ctx.carry_rows = None
    hgt = FOOT_RULE_Y - 0.04 - notes_h - y
    tx, tw = span(0, 7)
    ax, aw = span(8, 11)
    as_chart = _table_as_chart(t) if bullets and not t.get("keep_table") else None
    if as_chart and DENSITY["mode"] == "present" and len(as_chart["series"]) > 1:
        as_chart = None                                    # presenting: one measure per chart, else the table
    side = None
    if bullets and not as_chart and SPLIT_TABLES["on"] and len(t.get("columns") or []) <= 4 \
            and natural < 0.72 * (bottom - y):
        side = _table(ctx, sl, n, dict(t), tx, y, tw, hgt, dry=True)   # measured at the width it will be drawn
        ctx.carry_rows = None
        if side == float("inf"):
            side = None
    side_t = None
    if side and not as_chart:                              # grow the table for its column first; a table that still
        base = t.get("size") or T["cell"]                  # fills little of the height goes full width with a panel
        top = min(20, base + (5 if DENSITY["mode"] == "present" else 4))
        for z in range(top, base - 1, -1):
            for pad in TABLE_PADS:
                need = _table(ctx, sl, n, dict(t, size=z, _pad=pad), tx, y, tw, hgt, dry=True)
                ctx.carry_rows = None
                if need <= 0.9 * hgt:
                    side_t = dict(t, size=z, _pad=pad) if z != base else dict(t, _pad=pad)
                    break
            if side_t is not None:
                break
        if side_t is None or need < 0.55 * hgt:
            side = None
        elif need < (1 - 2 * THIN) * hgt:                  # a side composition that would still end high: the
            pb = (([{"text": tk["text"], "size": T["head"], "bold": True}] if tk.get("text") else [])  # full-width
                  + [{"text": b["text"], "size": T["body"], "bullet": True, "space_before": 6} for b in bullets])
            ph = _panel(ctx, s, sl, n, ax, y, aw, hgt, [], pb, dry=True)   # table, its panel under it, if that
                                                                           # uses the body better
            if max(need, ph) < (1 - 2 * THIN) * hgt:
                side = None
    if as_chart or side:
        pts_ = [{"text": tk.get("text", ""), "bullets": bullets, "prov": tk.get("prov")}]
        if as_chart and POINTS["on"] and 2 <= len(bullets) <= 4 and s.get("_form") != "side" and \
                (s.get("_form") == "band" or ctx.last_side == n - 1):
            row_h = 0.42 * hgt                             # after a chart-and-column slide: chart across the page,
            if draw_points(ctx, s, sl, n, LEFT, y + hgt - row_h, RIGHT - LEFT, row_h, pts_,   # points in a row
                           card=TC.BACKGROUND_2, dry=True, layout="row"):
                _chart(ctx, sl, n, as_chart, LEFT, y, RIGHT - LEFT, hgt - row_h - 0.2)
                ctx.track(n, " ".join(" ".join(map(str, r)) for r in t["rows"]), t.get("prov"))
                draw_points(ctx, s, sl, n, LEFT, y + hgt - row_h, RIGHT - LEFT, row_h, pts_, card=TC.BACKGROUND_2,
                            layout="row")
                ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h if notes else None
                return
        if as_chart:                                       # a small all-number table reads better as a chart
            ctx.last_side = n
            gf = _chart(ctx, sl, n, as_chart, tx, y, tw, hgt)
            ctx.track(n, " ".join(" ".join(map(str, r)) for r in t["rows"]), t.get("prov"))
            eh = gf.height / 914400
        else:                                              # short table: table left, its argument right
            eh = _table(ctx, sl, n, dict(side_t), tx, y, tw, hgt).rendered_h
        lead = ([{"text": tk["text"], "size": T["head"], "bold": True, "numbers": False, "prov": tk.get("prov")}]
                if tk.get("text") else [])
        bl = [{"text": b["text"], "size": T["body"], "bullet": True, "space_before": 6, "prov": b.get("prov")}
              for b in bullets]
        # the panel aligns to the exhibit's height (taller when its text needs it); when the exhibit is short of the
        # body, the panel's text steps up the scale and runs on down the body, so the page does not end high
        ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h if notes else None
        pts = [{"text": tk.get("text", ""), "bullets": bullets, "prov": tk.get("prov")}]
        if POINTS["on"] and draw_points(ctx, s, sl, n, ax, y, aw, eh, pts, card=TC.BACKGROUND_2):
            return                                         # the points end where the exhibit ends
        need = _panel(ctx, s, sl, n, ax, y, aw, hgt, [], lead + bl, dry=True, grow=False)
        _panel(ctx, s, sl, n, ax, y, aw, max(eh, min(hgt, need)), [], lead + bl)   # never shorter than the exhibit
        return
    # Full width (wide or long table): the table at its natural size, then the so-what panel down to the bottom of
    # the body with the takeaway and bullets, grown to fill it. No dead block under a short table.
    bottom_all = FOOT_RULE_Y - 0.04 - notes_h
    lead = ([{"text": tk["text"], "size": T["head"], "bold": True, "numbers": False, "prov": tk.get("prov")}]
            if tk.get("text") else [])
    bl = [{"text": b["text"], "size": T["body"], "bullet": True, "space_before": 4, "prov": b.get("prov")}
          for b in bullets]
    reserve = 0.0
    if lead or bl:                                      # the panel as tall as its text: the table takes the rest
        reserve = _panel(ctx, s, sl, n, LEFT, 0, RIGHT - LEFT, 0.45 * (bottom_all - y), lead, bl, dry=True,
                         grow=False) + 0.2
    bottom = bottom_all - reserve
    if len(t.get("rows") or []) <= 14:                  # room to spare: up the type scale (two steps past the
        base = min(t.get("size") or T["cell"], T["cell"])  # heading size at most), then row air; a size the writer
                                                           # gave is where the table may start, not a cap
        done = False
        for z in range(T["head"] + 2, base - 1, -1):
            for pad in TABLE_PADS:
                need = _table(ctx, sl, n, dict(t, size=z, _pad=pad), LEFT, y, RIGHT - LEFT, bottom - y, dry=True)
                ctx.carry_rows = None
                if need <= 0.95 * (bottom - y):             # margin for header wrapping the dry run cannot see
                    t, done = dict(t, size=z, _pad=pad), True
                    break
            if done:
                break
    gf = _table(ctx, sl, n, t, LEFT, y, RIGHT - LEFT, bottom - y)
    if ctx.carry_rows:
        ctx.carry = dict(s, table=dict(s["table"], rows=ctx.carry_rows, highlight_rows=[]), bullets=[],
                         title=_cont_title(s["title"]), _cont=True)
    ptop = min(bottom + 0.1, y + gf.rendered_h + 0.2)
    if lead or bl:                                      # what the grown panel leaves: a larger gap above it (capped)
        ph = _panel(ctx, s, sl, n, LEFT, ptop, RIGHT - LEFT, bottom_all - ptop, lead, bl, dry=True)
        ptop += max(0.0, min(RHYTHM_MAX - 0.2, bottom_all - ptop - ph))
    _panel(ctx, s, sl, n, LEFT, ptop, RIGHT - LEFT, bottom_all - ptop, lead, bl, shrink=True)
    ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h if notes else None


def a_chart_takeaway(ctx, s, sl, n):
    """Chart with its takeaway. The text goes beside the chart when it fills that column; when it is short, the chart
    takes the full width and the takeaway sits in a band under it, so neither leaves a dead block."""
    _header(ctx, s, sl, n)
    tk = s.get("takeaway") or {}
    bullets = [b if isinstance(b, dict) else {"text": b} for b in s.get("bullets") or []]
    lead = ([{"text": tk["text"], "size": T["head"], "bold": True, "numbers": False, "prov": tk.get("prov")}]
            if tk.get("text") else [])
    bl = [{"text": b["text"], "size": T["body"], "bullet": True, "space_before": 6, "prov": b.get("prov")}
          for b in bullets]
    body_h = BODY_B - BODY_Y
    bx, bw = span(8, 11)
    long_series = len((s["chart"] or {}).get("categories") or []) >= 8
    short = len((s["chart"] or {}).get("categories") or []) <= 5   # a few bars never take the whole slide
    # Composition variety: a chart slide straight after another side-panel chart slide takes the full width with its
    # so-what in a band underneath (when the text fits a band), so a run of chart pages never reads as one template.
    lx_, lw_ = span(0, 3)
    cw_ = (RIGHT - colx(4) - GUT * (1 if len(bl) >= 3 else 0)) / (2 if len(bl) >= 3 else 1)
    halves = [bl] if len(bl) < 3 else [bl[: (len(bl) + 1) // 2], bl[(len(bl) + 1) // 2:]]
    band_fits = bool(lead) and max([_need(lead, lw_)] + [_need(c, cw_) for c in halves]) + 0.04 <= 0.4 * body_h
    if (POINTS["on"] and 2 <= len(bullets) <= 4 and s.get("layout") != "side" and s.get("_form") not in
            ("side", "mirror") and not short
            and (s.get("_form") == "band" or ctx.last_side == n - 1)):   # after a chart-and-column slide: the
        pts = [{"text": tk.get("text", ""), "bullets": bullets, "prov": tk.get("prov")}]   # row of cards under it,
        row_h = next((f * body_h for f in (0.26, 0.32, 0.38, 0.44)                       # only as tall as needed
                      if draw_points(ctx, s, sl, n, LEFT, BODY_B - f * body_h, RIGHT - LEFT, f * body_h, pts,
                                     card=TC.BACKGROUND_2, dry=True, layout="row")), 0.44 * body_h)
        if draw_points(ctx, s, sl, n, LEFT, BODY_B - row_h, RIGHT - LEFT, row_h, pts, card=TC.BACKGROUND_2,
                       dry=True, layout="row"):
            _chart(ctx, sl, n, s["chart"], LEFT, BODY_Y, RIGHT - LEFT, body_h - row_h - 0.2)
            draw_points(ctx, s, sl, n, LEFT, BODY_B - row_h, RIGHT - LEFT, row_h, pts, card=TC.BACKGROUND_2,
                        layout="row")
            return
    vary = ((s.get("layout") == "band" or ctx.last_side == n - 1) and band_fits and s.get("layout") != "side"
            and s.get("_form") not in ("side", "mirror") and not short)
    if not vary and (not long_series or _need(lead + bl, bw) >= 0.62 * body_h or not (lead or bl)):
        mirror = s.get("_form") == "mirror"
        ctx.last_side = -9 if mirror else n
        # The split follows the text: a narrower column for less text (the chart gains the width), then a step up
        # the type scale; the renderer never adds gaps to fake fullness.
        import copy
        best = None
        for split in ((6, 7) if short else (8, 9)):     # a short chart: half the slide, the argument the rest
            tx, tw = span(split, 11)
            for bump in (0, 1, 2):
                trial = copy.deepcopy(lead + bl)
                for b in trial:
                    b["size"] = b.get("size", T["body"]) + bump
                need = _need(trial, tw)
                if need > body_h - 0.1:
                    break
                best = (split, trial, need)
                if need >= 0.62 * body_h:
                    break
            if best and best[0] == split and best[2] >= 0.62 * body_h:
                break
        split, blocks_, _ = best or ((6 if short else 8), lead + bl, 0)
        x, w = span(0, split - 1)
        bx, bw = span(split, 11)
        if mirror:                                      # the argument on the left, the chart on the right
            bx, bw = span(0, 11 - split)
            x, w = span(12 - split, 11)
        _chart(ctx, sl, n, s["chart"], x, BODY_Y, w, body_h)
        pts = [{"text": tk.get("text", ""), "bullets": bullets, "prov": tk.get("prov")}]
        if POINTS["on"] and 2 <= len(bullets) <= 4 and \
                draw_points(ctx, s, sl, n, bx, BODY_Y, bw, body_h, pts, card=TC.BACKGROUND_2):
            return                                          # the takeaway, then a card per point, down to the foot
        for b in blocks_:                                   # the panel grows the text itself, from the base sizes
            b["size"] = T["head"] if b.get("bold") and not b.get("bullet") else T["body"]
        _panel(ctx, s, sl, n, bx, BODY_Y, bw, body_h, [], blocks_)
        return
    # band under a full-width chart: the takeaway on the left, the bullets beside it in one or two columns
    lx, lw = LEFT + PANEL_PAD, span(0, 3)[1] - PANEL_PAD
    cols = [bl] if len(bl) < 3 else [bl[: (len(bl) + 1) // 2], bl[(len(bl) + 1) // 2:]]
    cx0 = colx(4)
    cw = (RIGHT - PANEL_PAD - cx0 - GUT * (len(cols) - 1)) / len(cols)
    lead = [dict(b, size=T["head"] + 1) for b in lead]
    for b in bl:
        b["size"] = T["body"] + 1
    need = max([_need(lead, lw)] + [_need(c, cw) for c in cols if c])
    if need > 0.4 * body_h:
        for b in bl:
            b["size"] = T["body"]
        need = max([_need(lead, lw)] + [_need(c, cw) for c in cols if c])
    band = min(need + 0.3, 0.45 * body_h)
    cy = BODY_B - band
    _chart(ctx, sl, n, s["chart"], LEFT, BODY_Y, RIGHT - LEFT, cy - BODY_Y - 0.15)
    box = _fill(sl, LEFT, cy, RIGHT - LEFT, band, TC.BACKGROUND_2, "ov_Panel")
    _shape_text(box, lead, ctx, n, (lx, cy + 0.12, lw, band - 0.24), MSO_ANCHOR.MIDDLE)
    for i, c in enumerate(cols):
        if c:
            _write(_box(sl, cx0 + i * (cw + GUT), cy + 0.12, cw, band - 0.24, f"ov_PanelCol{i + 1}",
                        MSO_ANCHOR.MIDDLE), c, ctx, n)


_STOP = set("a an the of to for and or in on at by with is are be its it this that as from into than then will "
            "should would can could must we our their his her decision today decide approve s".split())


def _tokens(t: str) -> list[str]:
    t = re.sub(r"\*\*|\^\w+", "", str(t or "")).lower().replace("’", "'")
    words = (w.strip(".-/") for w in re.findall(r"[a-z0-9$€£~.%/-]+", t))
    return [w for w in words if w and w not in _STOP]


def repeats(text: str, title: str, share: float = 0.8) -> bool:
    """Does `text` substantially repeat `title`? At least `share` of the shorter one's content words (normalised:
    no bold marks, case, stop words, or the "Decision today:" label) appear in the other."""
    a, b = _tokens(text), _tokens(title)
    if not a or not b:
        return False
    short, other = (a, set(b)) if len(a) <= len(b) else (b, set(a))
    return sum(1 for w in short if w in other) / len(short) >= share


def a_executive_summary(ctx, s, sl, n):
    """Executive summary: the answer first. Numbered rows, each a bold claim on the left (the section's message) and
    its evidence on the right, hairlines between rows; an optional dark band at the foot with what to decide."""
    _header(ctx, s, sl, n)
    pts = [p if isinstance(p, dict) else {"lead": str(p)} for p in (s.get("points") or [])][:5]
    ask = s.get("decision") or (s.get("callout") or {}).get("text") or ""
    if ask and repeats(re.sub(r"(?i)^\s*decision today:\s*", "", ask), s.get("title", ""), 0.55):   # the title
        # already is the answer: a band saying it again
        s["notes"] = (s.get("notes", "") + "\n[QA] decision band left out: it repeats the title: " + ask).strip()
        ask = ""
    top, bottom = BODY_Y + 0.1, BODY_B
    if ask:
        ab = [{"text": ask, "size": T["head"] + 2, "bold": True, "numbers": False, "color": TC.BACKGROUND_1}]
        ah = min(1.1, _need(ab, RIGHT - LEFT - 0.6) + 0.3)
        band = _fill(sl, LEFT, bottom - ah, RIGHT - LEFT, ah, TC.TEXT_2, "ov_Decision")
        _shape_text(band, ab, ctx, n, (LEFT + 0.3, bottom - ah + 0.05, RIGHT - LEFT - 0.6, ah - 0.1), MSO_ANCHOR.MIDDLE)
        bottom -= ah + 0.2
    if not pts:
        return
    lx, lw = span24(1, 9)
    dx, dw = span24(10, 23)
    mets = [m for m in (s.get("metrics") or []) if isinstance(m, dict) and str(m.get("value") or "").strip()][:3]
    said_ = " ".join(_plain(_txt(x)) for p_ in s.get("points") or [] if isinstance(p_, dict)
                     for x in [p_.get("lead") or ""] + list(p_.get("bullets") or []))
    fresh = [m for m in mets if _plain(str(m["value"])).strip() not in said_]
    mets = fresh if len(fresh) >= 2 else []           # a panel that repeats the points says it twice: leave it out
    if mets:                                              # the decision's size, large in a panel beside the points
        dx, dw = span24(10, 18)
        mx, mw = span24(19, 23)
        _fill(sl, mx, top - 0.05, RIGHT - mx, bottom - top + 0.05, TC.BACKGROUND_2, "ov_MetricsPanel")   # on the
        mx, mw = mx + 0.14, mw - 0.14                     # grid, a full gutter from the points; its figures inset
        mh = (bottom - top) / len(mets)
        for i, m in enumerate(mets):
            my = top + i * mh
            vb = [{"text": str(m["value"]), "size": T["num"] + 6, "bold": True, "heading": True, "numbers": False,
                   "color": TC.ACCENT_1}]
            ctx.track(n, str(m["value"]), m.get("prov") or s.get("title_prov"))
            lb_ = [{"text": str(m.get("label") or ""), "size": T["body"], "numbers": False}]
            lh_ = _need(lb_, mw - 0.1) + 0.06
            off = max(0.12, (mh - 0.62 - lh_) / 2)        # the figure and its label centred in their share of the
            _write(_box(sl, mx, my + off, mw - 0.1, 0.62, f"role_numeral_Metric{i + 1}"), vb)   # panel: no blank
            _write(_box(sl, mx, my + off + 0.62, mw - 0.1, max(0.3, min(lh_, mh - off - 0.66)),  # foot under each
                        f"MetricLabel{i + 1}"), lb_)
            if i < len(mets) - 1:
                _rule(sl, mx, my + mh, mw - 0.1, TC.ACCENT_5, 0.5, f"MetricRule{i + 1}")
    size = T["body"] + 2

    def rows(z):
        out = []
        for p in pts:
            a = [{"text": p.get("lead", ""), "size": z + 3, "bold": True, "numbers": False}]
            d = [{"text": b["text"] if isinstance(b, dict) else str(b), "size": z, "bullet": True, "space_before": 3,
                  "prov": (b.get("prov") if isinstance(b, dict) else None) or p.get("prov")}
                 for b in (p.get("bullets") or [])[:3]]
            out.append((a, d, max(_need(a, lw), _need(d, dw) if d else 0) + 0.2))
        return out
    laid = rows(size)
    while sum(h for *_, h in laid) > bottom - top and size > 13:   # never under 13 pt (house style)
        size -= 1
        laid = rows(size)
    while sum(h for *_, h in laid) > bottom - top:                   # then evidence lines go to the notes, the
        longest = max(range(len(pts)), key=lambda k: len(pts[k].get("bullets") or []))   # longest row's last first
        bl_ = list(pts[longest].get("bullets") or [])
        if not bl_:
            break
        _cut_note(s, [bl_[-1]["text"] if isinstance(bl_[-1], dict) else str(bl_[-1])])
        pts[longest] = dict(pts[longest], bullets=bl_[:-1])
        laid = rows(size)
    if sum(h for *_, h in laid) > bottom - top:
        ctx.miss(n, "the executive summary", int((sum(h for *_, h in laid) - (bottom - top)) * 30))
    gap = max(0.0, (bottom - top - sum(h for *_, h in laid)) / len(laid))
    from .compose import item_icons
    icons = item_icons([{"lead": p.get("lead", ""), "bullets": []} for p in pts]) if len(pts) <= 4 else []
    if not icons or not all(icons):
        icons = [None] * len(pts)                                  # one look per slide: all icons or all numbers
    y = top
    tracker = len(pts) >= 2                               # the points are the deck's numbered sections
    for i, (a, d, h) in enumerate(laid):
        rh = h + gap
        if icons[i] and not tracker:
            _icon(sl, icons[i], LEFT, y + 0.08, 0.42, re.sub(r"\*\*", "", pts[i].get("lead", ""))[:60])
        else:                                             # "01": the same mark as the section number on the slide
            _write(_box(sl, LEFT, y + 0.06, 0.5, 0.5, f"role_numeral_{i + 1}"),    # that proves this point
                   [{"text": f"{i + 1:02d}" if tracker else str(i + 1), "size": T["num"], "bold": True,
                     "heading": True, "numbers": False, "color": TC.ACCENT_1}])
        _write(_box(sl, lx, y + 0.08, lw, rh - 0.12, f"Claim{i + 1}"), a, ctx, n)
        if d:
            _write(_box(sl, dx, y + 0.08, dw, rh - 0.12, f"Evidence{i + 1}"), d, ctx, n)
        y += rh
        if i < len(laid) - 1:
            _rule(sl, LEFT, y, (dx + dw if mets else RIGHT) - LEFT, TC.ACCENT_5, 0.5, f"SummaryRule{i + 1}")


def a_waterfall_bridge(ctx, s, sl, n):
    a_chart_takeaway(ctx, dict(s, chart=dict(s["chart"], kind="waterfall")), sl, n)


BENCH_LABEL_W = 1.9                                   # names (and logos) column of a benchmark page
BENCH_ROW_MAX = 0.6                                   # a peer's row at most: bars about 0.33in thick at gap 55%


def _bench_panel(ctx, sl, n, c, x, y, w, h, rows_n, name):
    """One multiple across the peers: native horizontal bars in a fixed order (the same order in every panel), a
    Median bar at the foot, a dashed median line across the peers and dotted 25th / 75th percentile lines. Values
    that are not meaningful show NM, not a bar. Returns the bar centres (y) for the shared label column."""
    vals = [None if v is None else float(v) for v in c["values"]]
    if len(vals) != rows_n - 1:
        raise ValueError(f"panel '{c.get('title', '')}' has {len(vals)} values for {rows_n - 1} companies")
    med = c.get("median")
    pts = [v for v in vals if v is not None] + ([float(med)] if med is not None else [])
    unit, dec = c.get("unit", "x"), c.get("decimals", 1)
    shown = [0.0 if v is None else v for v in vals] + ([float(med)] if med is not None else [])
    hi = max(pts) if pts else 1.0
    lo = min(0.0, min(pts)) if pts else 0.0
    amax = _nice_bounds(lo, hi)[1]
    PL = (0.02, 0.78)                                      # plot x and width (fractions of the frame)
    px = lambda v: x + w * (PL[0] + PL[1] * (v - lo) / (amax - lo))  # noqa: E731
    band = h / rows_n                                      # one slot per label row (the Median row even without one)
    centres = [y + band * (j + 0.5) for j in range(rows_n)]
    peers_bottom = y + band * len(vals)
    for key, dash, wt, colr in (("p25", 3, 0.75, TC.TEXT_2), ("p75", 3, 0.75, TC.TEXT_2), ("median", 4, 1.5, TC.TEXT_1)):
        v = c.get(key)                                     # drawn first: the bars and their labels sit on top
        if v is None:
            continue
        lx = px(float(v))
        ln = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(lx), Inches(y), Inches(lx), Inches(peers_bottom))
        ln.name = f"ov_{name}{key.capitalize()}"
        ln.line.color.theme_color = colr
        ln.line.width = Pt(wt)
        ln.line.dash_style = dash
    cd = CategoryChartData()
    cd.categories = [str(i + 1) for i in range(rows_n)]
    fmt = f'0.{"0" * dec}"{unit}";-0.{"0" * dec}"{unit}"' if unit in ("x", "%") else f"0.{'0' * dec}"
    cd.add_series(c.get("title", "Value"), shown + [None] * (rows_n - len(shown)), fmt)
    gf = sl.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(x), Inches(y), Inches(w), Inches(h), cd)
    positive_axis_ids(gf.chart)
    gf.name = f"ov_{name}"
    ch = gf.chart
    ch.has_title = ch.has_legend = False
    ch.font.size = Pt(T["label"])
    pa = ch._chartSpace.find(".//" + qn("c:plotArea"))
    lay = pa.find(qn("c:layout"))
    if lay is None:
        lay = pa.makeelement(qn("c:layout"), {})
        pa.insert(0, lay)
    for e in list(lay):
        lay.remove(e)
    ml = lay.makeelement(qn("c:manualLayout"), {})
    for tag, val in (("c:layoutTarget", "inner"), ("c:xMode", "edge"), ("c:yMode", "edge"), ("c:x", PL[0]),
                     ("c:y", 0.0), ("c:w", PL[1]), ("c:h", 1.0)):
        ml.append(ml.makeelement(qn(tag), {"val": str(val)}))
    lay.append(ml)
    plot = ch.plots[0]
    plot.gap_width = 55
    va, ca = ch.value_axis, ch.category_axis
    va.minimum_scale, va.maximum_scale = lo, amax
    va.visible = False
    va.has_major_gridlines = False
    ca.reverse_order = True
    ca.tick_label_position = XL_TICK_LABEL_POSITION.NONE
    ca.format.line.color.theme_color = TC.TEXT_1
    se = plot.series[0]
    se.format.fill.solid()
    se.format.fill.fore_color.theme_color = TC.ACCENT_4
    se.data_labels.show_value = True
    se.data_labels.number_format = fmt
    _no_wrap(se.data_labels)
    se.data_labels.number_format_is_linked = False
    se.data_labels.position = XL_LABEL_POSITION.INSIDE_END    # inside the bar end, white: clear of the lines
    se.data_labels.font.size = Pt(T["label"])
    se.data_labels.font.color.theme_color = TC.BACKGROUND_1
    hl = set(c.get("highlight") or [])
    lab_w = _need_w(f"{max(abs(v) for v in shown) if shown else 0:.{dec}f}{unit}", T["label"]) + 0.08
    for j, v in enumerate(shown):                          # a bar too short for its number gets it outside
        if (j >= len(vals) or vals[j] is not None) and abs(px(v) - px(0 if lo <= 0 else lo)) < lab_w:
            dl = se.points[j].data_label
            dl.position = XL_LABEL_POSITION.OUTSIDE_END
            dl.font.size = Pt(T["label"])
            dl.font.color.theme_color = TC.TEXT_1
    for j in range(len(shown)):
        if j == len(vals) or j in hl:                     # the Median bar and the subject company stand out
            f = se.points[j].format
            f.fill.solid()
            f.fill.fore_color.theme_color = TC.TEXT_2 if j == len(vals) else TC.ACCENT_1
        if j < len(vals) and vals[j] is None:             # not meaningful: no bar, no number, an NM note
            dls = se._element.find(qn("c:dLbls"))
            d_ = dls.makeelement(qn("c:dLbl"), {})
            d_.append(d_.makeelement(qn("c:idx"), {"val": str(j)}))
            d_.append(d_.makeelement(qn("c:delete"), {"val": "1"}))
            dls.insert(0, d_)
            _write(_box(sl, px(lo) + 0.04, centres[j] - 0.12, 0.6, 0.24, f"ov_{name}NM{j + 1}", MSO_ANCHOR.MIDDLE),
                   [{"text": "NM", "size": T["label"], "italic": True, "numbers": False}], ctx, n)
    _alt(gf, c.get("alt") or f"{c.get('title', 'Multiple')} by company with the median and quartiles")
    return centres


def a_benchmark(ctx, s, sl, n):
    """Peer benchmarking without a table: the companies lined up once (name and optional logo), one panel per
    multiple or metric beside each other in the same order, each with its median line and Median bar, the
    subject company highlighted; the so-what panel under them."""
    _header(ctx, s, sl, n)
    b = s["benchmark"]
    names = [str(v) for v in b["companies"]]
    panels = b["panels"][:4]
    logos = b.get("logos")
    if logos is None:                                  # fetched marks for real companies (logos.py), drawn as vectors
        from .logos import for_names
        logos = for_names(names)
    tk = s.get("takeaway") or {}
    lead = ([{"text": tk["text"], "size": T["head"], "bold": True, "numbers": False, "prov": tk.get("prov")}]
            if tk.get("text") else [])
    bl = [{"text": x["text"] if isinstance(x, dict) else str(x), "size": T["body"], "bullet": True,
           "space_before": 4, "prov": x.get("prov") if isinstance(x, dict) else None} for x in s.get("bullets") or []]
    notes = s.get("footnotes") or []
    notes_h = (0.155 * max(1, (len("    ".join(notes)) // 170) + 1) + 0.05) if notes else 0
    bottom_all = FOOT_RULE_Y - 0.04 - notes_h
    rows_n = len(names) + 1
    head_h = 0.42
    # one composition: the panel's own height first (shrink-wrapped), the peers' rows take the rest (up to 0.62in a
    # row, so bars stay bars), and the panel follows the bars at one gap
    need = (_panel(ctx, s, sl, n, LEFT, 0, RIGHT - LEFT, 0.45 * (bottom_all - BODY_Y), lead, bl, dry=True, grow=False)
            if (lead or bl) else 0)
    # a thin page gives the room to the bars first (up to BENCH_ROW_MAX a row: bars stay well under the P20 slab
    # limit), then to the panel's type, then to the gap between them (capped)
    room = bottom_all - BODY_Y - head_h - (need + 0.25 if need else 0)
    want_h = min(rows_n * BENCH_ROW_MAX, max(rows_n * 0.36, room))
    top = BODY_Y + head_h
    pgap = 0.25
    if lead or bl:
        rest = bottom_all - (top + want_h + pgap)
        pgap += max(0.0, min(RHYTHM_MAX - pgap,
                             rest - _panel(ctx, s, sl, n, LEFT, 0, RIGHT - LEFT, rest, lead, bl, dry=True)))
    gap = 0.3
    pw = (RIGHT - LEFT - BENCH_LABEL_W - gap * (len(panels) - 1)) / len(panels)
    band = want_h / rows_n
    for j, nm_ in enumerate(names + [b.get("median_label", "Median")]):   # the shared label column, top to bottom
        cy = top + band * (j + 0.5)
        lx, lw_ = LEFT, BENCH_LABEL_W - 0.1
        logo = logos.get(nm_)
        if logo and Path(logo).exists():
            d = min(0.3, band * 0.8)
            lw = logo_shape(sl, logo, lx, cy - d / 2, d, 1.2, f"ov_Logo{j + 1}", f"{nm_} logo")
            if lw:
                lx, lw_ = lx + lw + 0.06, lw_ - lw - 0.06
        is_med = j == len(names)
        hl = j in set(b.get("highlight") or [])
        blk = [{"text": nm_, "size": T["body"], "bold": is_med or hl, "numbers": False, "italic": is_med}]
        fits = lambda: _need(blk, lw_) <= band + 0.06     # noqa: E731  _need's own 0.05 pad; two lines fit a row
        ok = fits()                             # a long name ("Meridian Gauges (Apex Capital, 2025)") wrapped onto
        words = nm_.split()                     # three lines and ran into the rows above and below: shorten it with an
        while not ok and len(words) > 1:        # ellipsis at full size (P5); the full name stays in the alt text
            words = words[:-1]
            blk[0]["text"] = " ".join(words).rstrip(",(") + "…"
            ok = fits()
        box = _box(sl, lx, cy - band / 2, lw_, band, f"ov_BenchName{j + 1}", MSO_ANCHOR.MIDDLE)
        _write(box, blk, ctx, n)
        if blk[0]["text"] != nm_:
            _alt(box, nm_)
    for i, pn in enumerate(panels):
        x = LEFT + BENCH_LABEL_W + i * (pw + gap)
        _write(_box(sl, x, BODY_Y, pw, head_h - 0.06, f"ov_BenchHead{i + 1}", MSO_ANCHOR.BOTTOM),
               [{"text": pn.get("title", ""), "size": T["head"], "bold": True, "numbers": False}], ctx, n)
        _rule(sl, x, top - 0.04, pw, TC.TEXT_1, 0.75, f"ov_BenchRule{i + 1}")
        _bench_panel(ctx, sl, n, dict(pn, highlight=b.get("highlight")), x, top, pw, want_h, rows_n, f"Bench{i + 1}")
        ctx.track(n, " ".join(str(v) for v in pn.get("values") or []), pn.get("prov"))
    ptop = top + want_h + pgap
    _panel(ctx, s, sl, n, LEFT, ptop, RIGHT - LEFT, bottom_all - ptop, lead, bl, shrink=True)
    ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h if notes else None


def a_football_field(ctx, s, sl, n):
    """Valuation summary: the football field across the full width (methods sorted, reference prices as labelled
    lines), the takeaway band directly under it. Only reached with >= 3 methods in one unit (else football_fallback)."""
    _header(ctx, s, sl, n)
    tk = s.get("takeaway") or {}
    lead = ([{"text": tk["text"], "size": T["head"], "bold": True, "numbers": False, "prov": tk.get("prov")}]
            if tk.get("text") else [])
    bl = [{"text": b["text"] if isinstance(b, dict) else str(b), "size": T["body"], "bullet": True, "space_before": 4,
           "prov": b.get("prov") if isinstance(b, dict) else None} for b in s.get("bullets") or []]
    notes = s.get("footnotes") or []
    notes_h = (0.155 * max(1, (len("    ".join(notes)) // 170) + 1) + 0.05) if notes else 0
    bottom = FOOT_RULE_Y - 0.04 - notes_h
    need = _panel(ctx, s, sl, n, LEFT, 0, RIGHT - LEFT, 0.5 * (bottom - BODY_Y), lead, bl, dry=True, grow=False)
    gf = _range_chart(ctx, sl, n, dict(s["chart"], kind="range"), LEFT, BODY_Y, RIGHT - LEFT,
                      bottom - BODY_Y - need - 0.25)
    top = BODY_Y + gf.drawn_h + 0.25
    _panel(ctx, s, sl, n, LEFT, top, RIGHT - LEFT, bottom - top, lead, bl, shrink=True)
    ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h if notes else None


def a_agenda(ctx, s, sl, n):
    """Agenda, and the section tracker that opens each section: one row per section with a filled number disc, a line
    icon, the section name, the claim it proves and its first page, hairlines between rows. With "current" set, that
    row sits on a grey band and the other rows are greyed out, so the reader sees where the deck is (the
    reference decks' tracker). Sections come from the storyline, not from the model, so they always match the deck."""
    secs = [x for x in (s.get("sections") or []) if isinstance(x, dict) and x.get("title")][:7]
    s = dict(s, title=s.get("title") or "Agenda", subtitle=None)
    _header(ctx, s, sl, n)
    cur = str(s.get("current") or "")
    if not secs:
        return
    top, bottom = TITLE_RULE_Y + 0.25, BODY_B - 0.1
    rh = min(1.15, (bottom - top) / len(secs))
    big = rh >= 0.95
    name_sz, det_sz = (22, 14) if big else (18, 12)
    d = min(0.62, rh - 0.3)                                    # number disc
    ix, nx = LEFT + d + 0.3, LEFT + 2 * d + 0.6                # icon, then name
    px = RIGHT - 1.2                                           # page column
    nw = colx24(11) - 0.1 - nx
    dx, dw = colx24(12), px - 0.2 - colx24(12)
    for i, sec in enumerate(secs):
        y = top + i * rh
        no = str(sec.get("no") or i + 1)
        on = not cur or no == cur
        if cur and on:
            _fill(sl, LEFT - 0.15, y + 0.04, RIGHT - LEFT + 0.3, rh - 0.08, TC.BACKGROUND_2, f"ov_Current{i + 1}")
        cy = y + (rh - d) / 2
        disc = _fill(sl, LEFT, cy, d, d, TC.ACCENT_1 if on else TC.ACCENT_5, f"ov_AgendaNo{i + 1}", MSO_SHAPE.OVAL)
        _shape_text(disc, [{"text": no.zfill(2) if no.isdigit() else no, "size": 15 if big else 13, "bold": True,
                            "numbers": False, "color": TC.BACKGROUND_1, "align": PP_ALIGN.CENTER}],
                    ctx, n, (LEFT, cy, d, d), MSO_ANCHOR.MIDDLE)
        ic = next((icon_name(c) for c in (sec.get("icon"), sec["title"], sec.get("detail"))
                   if c and icon_name(c) != "circle-dot"), "circle-dot")
        _icon(sl, ic, ix, y + (rh - d * 0.85) / 2, d * 0.85, sec["title"], muted=not on)
        col = TC.TEXT_1 if on else TC.ACCENT_6
        _write(_box(sl, nx, y, nw, rh, f"AgendaName{i + 1}", MSO_ANCHOR.MIDDLE),
               [{"text": re.sub(r"\*\*", "", sec["title"]), "size": name_sz, "bold": True, "numbers": False, "color": col}])
        if sec.get("detail"):
            _write(_box(sl, dx, y, dw, rh, f"AgendaDetail{i + 1}", MSO_ANCHOR.MIDDLE),
                   [{"text": re.sub(r"\*\*", "", sec["detail"]), "size": det_sz, "numbers": False,
                     "color": TC.TEXT_2 if on else TC.ACCENT_5}], ctx, n)
        if sec.get("page"):
            _write(_box(sl, px, y, RIGHT - px, rh, f"AgendaPage{i + 1}", MSO_ANCHOR.MIDDLE),
                   [{"text": f"p. {sec['page']}", "size": det_sz, "numbers": False, "align": PP_ALIGN.RIGHT,
                     "color": TC.TEXT_2 if on else TC.ACCENT_5}])
        if i < len(secs) - 1 and not cur:
            _rule(sl, LEFT, y + rh, RIGHT - LEFT, TC.ACCENT_5, 0.5, f"AgendaRule{i + 1}")


def agenda_sections(slides: list[dict]) -> list[dict]:
    """The deck's sections in order, from the slides' "section" / "section_no" (set from the storyline): name, the
    claim of the section's first slide, its first page and an icon meaning. Covers, agendas and dividers are skipped."""
    out, seen = [], {}
    for i, s in enumerate(slides, 1):
        name = (s.get("section") or "").strip()
        if not name or s.get("archetype") in ("cover", "agenda", "section_divider", "end", "executive_summary"):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen[key] = True
        out.append({"no": str(len(out) + 1), "title": name, "detail": re.sub(r"\*\*|\^\w+", "", s.get("title") or ""),
                    "page": i, "icon": s.get("section_icon") or name})
    return out


def a_grid(ctx, s, sl, n):
    """A slide assembled from parts (chart, table, stats, text, callout, process, timeline, matrix2x2, harvey, quote)
    on a grid layout; see parts.py. Imported here, not at the top, because parts.py imports this module."""
    from .parts import a_grid as _a_grid
    return _a_grid(ctx, s, sl, n)


ARCHETYPES = {"argument_only": a_argument_only, "cover": a_cover, "section_divider": a_cover, "challenge_matrix": a_challenge_matrix,
              "text_matrix": a_challenge_matrix, "exhibit_argument": a_exhibit_argument, "table_focus": a_table_focus,
              "table_highlight": a_table_focus, "bar_takeaway": a_chart_takeaway, "column_trend": a_chart_takeaway,
              "line_trend": a_chart_takeaway, "stacked_composition": a_chart_takeaway,
              "waterfall_bridge": a_waterfall_bridge, "football_field": a_football_field, "benchmark": a_benchmark, "closing": a_closing, "next_steps": a_closing, "end": a_cover,
              "grid": a_grid, "agenda": a_agenda,
              "executive_summary": a_executive_summary}
DIVIDERS = {"cover", "section_divider", "appendix_divider", "end"}


def _register_diagram_archetypes():
    """risk_matrix, matrix_3x3, gantt, issue_tree, org_chart, cycle, hub_spoke, pyramid, funnel (diagrams/).
    Registered on first render (the diagrams package imports this module)."""
    from .diagrams import ARCHETYPES as DA, a_diagram
    for a in DA:
        ARCHETYPES.setdefault(a, a_diagram)


def _register_library_archetype():
    """library: a layout from the private slide library drawn as native shapes (component.py). Registered on first
    render; it needs no diagram module."""
    from .component import a_library
    ARCHETYPES.setdefault("library", a_library)


def _register_layout_archetype():
    """layout: a built-in layout (engines/forge/data/layouts.json) drawn as native shapes (layouts.py). Registered on
    first render, like library."""
    from .layouts import a_layout
    ARCHETYPES.setdefault("layout", a_layout)


FRAME_TOP = os.environ.get("FORGE_FRAME", "white") == "lines"   # default: a white top (no hairline across it)
COVER_DARK = os.environ.get("FORGE_COVER", "dark") == "dark"      # "light": the white cover
NUMBER_ACCENT = os.environ.get("FORGE_NUMBER_ACCENT", "0") == "1"   # legacy: every number in accent blue
RTL_FONT = "Arial"           # the complex-script face: ships Arabic glyphs on Windows and macOS
DIRECTIONAL = {"homePlate", "chevron", "rightArrow", "leftArrow", "notchedRightArrow", "stripedRightArrow",
               "rightArrowCallout", "leftArrowCallout", "pentagon", "triangle", "rtTriangle", "curvedRightArrow"}


def apply_rtl(prs) -> int:
    """Every paragraph that is mostly Arabic reads right to left: rtl on the paragraph, right alignment (a centred
    paragraph stays centred), the Arabic language tag and a complex-script face on each run. Deterministic, after
    layout, so any model's Arabic text renders correctly. Returns the number of paragraphs changed."""
    from .common import is_rtl
    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    n = 0
    W = prs.slide_width
    for sl in prs.slides:                    # a slide whose title reads right to left is mirrored as a whole: the
        title = next((sh for sh in sl.shapes if sh.is_placeholder and sh.placeholder_format.type in (1, 3)), None)
        ttext = title.text_frame.text if title is not None and title.has_text_frame else ""
        if not ttext:                        # first column, step 1 and the panel order start on the right
            ttext = " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)[:400]
        if is_rtl(ttext) and not any((sh.name or "").startswith("rtl:mirrored") for sh in sl.shapes):
            for sh in sl.shapes:
                if sh.left is not None and sh.width is not None:
                    sh.left = int(W - sh.left - sh.width)
                geom = sh._element.find(f".//{A}prstGeom")
                if geom is not None and geom.get("prst") in DIRECTIONAL:     # arrows and chevrons point the other way
                    xfrm = sh._element.find(f".//{A}xfrm")
                    if xfrm is not None:
                        if xfrm.get("flipH") == "1":
                            del xfrm.attrib["flipH"]
                        else:
                            xfrm.set("flipH", "1")
            mark = sl.shapes[0] if len(sl.shapes) else None
            if mark is not None:
                mark.name = "rtl:mirrored " + (mark.name or "")
    for sl in prs.slides:
        for p in sl._element.iter(f"{A}p"):
            text = "".join(t.text or "" for t in p.iter(f"{A}t"))
            if not is_rtl(text):
                continue
            ppr = p.find(f"{A}pPr")
            if ppr is None:
                ppr = p.makeelement(f"{A}pPr", {})
                p.insert(0, ppr)
            ppr.set("rtl", "1")
            if ppr.get("algn") in (None, "l", "just"):
                ppr.set("algn", "r")
            for r in p.iter(f"{A}r", f"{A}fld"):
                rpr = r.find(f"{A}rPr")
                if rpr is None:
                    rpr = r.makeelement(f"{A}rPr", {})
                    r.insert(0, rpr)
                rpr.set("lang", rpr.get("lang") if (rpr.get("lang") or "").startswith("ar") else "ar-SA")
                cs = rpr.find(f"{A}cs")
                if cs is None:
                    cs = rpr.makeelement(f"{A}cs", {})
                    order = ["ln", "noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill", "effectLst",
                             "effectDag", "highlight", "uLnTx", "uLn", "uFillTx", "uFill", "latin", "ea"]
                    after = [c for c in rpr if c.tag.split("}")[-1] in order]
                    (after[-1].addnext(cs) if after else rpr.insert(0, cs))
                cs.set("typeface", RTL_FONT)
            n += 1
    return n


def render(slides: list[dict], out_path: str, template: str | None = None, fix_labels: bool = True,
           carry: bool = True, theme=None, density: str | None = None, brand: dict | None = None,
           icon_set: str | None = None) -> dict:
    """Render, then (when LibreOffice is available) re-render up to 3 times moving only chart labels that collide.
    carry=False (a fixed slide count): content longer than its slide is never continued on an extra slide; what
    does not fit goes to the speaker notes and is reported, so the content model can cut or merge it.
    theme: a palette name ("navy", "ocean", "forest", "burgundy", "graphite", "mono") or a dict with "palette",
    "fonts" ("serif", "sans", "calibri", "cambria") and slot -> hex overrides; colours stay theme slots, so the deck
    recolours in PowerPoint (Design > Variants) too.
    density: "present", "balanced" (default) or "read" (density.py): the type scale and how much text a slide keeps
    on the page; what a mode cuts goes to the speaker notes. A slide's own "density" overrides the deck's.
    brand: {"firm", "footer", "logo"} from a company profile (engines/forge/brand.py render_kwargs): the firm label,
    and the logo in the footer of every page and on the cover.
    icon_set: one icon set for the whole deck ("phosphor", "material", "carbon", ... engines/icons SETS), also read
    from theme["icon_set"]; icons are then embedded as SVG with a PNG fallback. Default: Lucide as PNG."""
    from .template import theme_spec
    try:                                                   # zero images unless the rules allow pictures (P9)
        from .validate_pptx import load_rules
        PICTURES["allow"] = bool(load_rules()["pptx"].get("images", {}).get("allow_pictures", False)) or \
            os.environ.get("MH_ALLOW_PICTURES") == "1"
    except Exception:                                      # noqa: BLE001 - unreadable rules: the strict default
        PICTURES["allow"] = False
    _set_icons(icon_set or (theme.get("icon_set") if isinstance(theme, dict) else None))
    BRAND.update({"firm": None, "footer": None, "logo": None, "footer_right": None,
                  **{k: v for k, v in (brand or {}).items() if k in BRAND}})
    DENSITY["deck"] = set_density(density)
    spec = theme_spec(theme)
    FRAME["band"] = spec.get("frame") == "band"
    FACE["head"], FACE["body"] = spec["fonts"]["major"], spec["fonts"]["minor"]
    template = template or default_template(theme)
    res = _render(slides, out_path, template, carry)
    if not fix_labels:
        return res
    try:
        for _ in range(3):
            moved = _relabel(slides, out_path)
            if not moved:
                break
            res = _render(slides, out_path, template, carry)
    except Exception as e:                                   # QA aid only: never fail the build on it
        res["errors"].append(f"label check skipped: {type(e).__name__}")
    finally:
        set_density(DENSITY["deck"])
    return res


FRAME = {"band": False}         # the deck's content-slide frame (template.FRAMES): "band" = dark title band
SPLIT_TABLES = {"on": False}  # a short table beside its argument; off: the table runs full width, its so-what under it
POINTS = {"on": True}      # argument columns as designed points (icon cards); off: the text column as before
PICTURES = {"allow": False}     # zero images (rules pptx.images.allow_pictures): raster logos are left out


def logo_shape(sl, path, x, y, h, max_aspect, name, alt, exact_w=None) -> float:
    """A logo at height h: an SVG logo as one native vector shape in the text colour (zero images, recolours with the
    theme); a raster logo only when pictures are allowed. Returns the drawn width in inches (0: not drawn)."""
    p = Path(path)
    if p.suffix.lower() == ".svg":
        from .vector import add_svg_colors, parse_svg, tight_box
        try:
            txt = p.read_text()
            box = tight_box(parse_svg(txt))                 # the mark's own bounds, not its canvas
            w = exact_w or min(h * box[2] / max(box[3], 1e-6), h * max_aspect)
            if not exact_w and w < h * box[2] / max(box[3], 1e-6):      # too wide for the room: keep the aspect
                h = w * box[3] / box[2]
            shp = add_svg_colors(sl, txt, x, y, w, h, name, "logo:" + re.sub(r"\s+logo$", "", alt))   # own colours
            return w if shp is not None else 0.0
        except Exception:                                  # a broken logo never breaks the deck
            return 0.0
    if not PICTURES["allow"]:
        return 0.0
    pic = sl.shapes.add_picture(str(p), Inches(x), Inches(y), height=Inches(h)) if exact_w is None else \
        sl.shapes.add_picture(str(p), Inches(x), Inches(y), Inches(exact_w), Inches(h))
    pic.name = name
    _alt(pic, alt)
    return pic.width / 914400


def _firm_label(s) -> str:
    """The footer label: the company's footer line when it fits the label column, else its name, else the default."""
    foot = BRAND.get("footer") or ""
    if foot and len(foot) <= 30:
        return foot
    return s.get("firm") or BRAND.get("firm") or FIRM


def _logo_size(path: str, h: float, max_w: float) -> tuple[float, float]:
    if str(path).lower().endswith(".svg"):
        from .vector import parse_svg, tight_box
        _, _, w0, h0 = tight_box(parse_svg(Path(path).read_text()))
    else:
        from PIL import Image
        with Image.open(path) as im:
            w0, h0 = im.size
    w = h * w0 / max(1, h0)
    if w > max_w:
        h, w = h * max_w / w, max_w
    return w, h


def _logo(sl, x_right: float, y: float, h: float, max_w: float, name: str, x_left: float | None = None) -> float:
    """Place the brand logo (right-aligned at x_right, or left-aligned at x_left); returns its width (0 if none)."""
    p = BRAND.get("logo")
    if not p or not Path(p).exists():
        return 0.0
    try:
        w, hh = _logo_size(p, h, max_w)
    except Exception:                                      # an unreadable image never breaks the deck
        return 0.0
    x = x_left if x_left is not None else x_right - w
    return logo_shape(sl, p, x, y + (h - hh) / 2, hh, w / max(hh, 1e-6), name,
                      f"{BRAND.get('firm') or 'Company'} logo", exact_w=w)


_CYCLE = ["right", "left", "above", "below"]


def _relabel(slides, out_path) -> int:
    """Render to PDF, find chart labels whose text collides with other text, move each to its next position."""
    import tempfile
    from .common import soffice_convert
    from .validate_pptx import _pdf_spans
    with tempfile.TemporaryDirectory() as td:
        pages = _pdf_spans(str(soffice_convert(out_path, "pdf", td)))
    moved = 0
    for (pw, spans), s in zip(pages, slides):
        c = s.get("chart") or {}
        if c.get("kind") != "bubble":
            continue
        k = 13.333 / pw
        boxes = [(t, (b[0] * k, b[1] * k, b[2] * k, b[3] * k)) for t, b in spans]
        for p in c["points"]:
            lab = p.get("label")
            if not lab:
                continue
            first = lab.split()[0]
            mine = [bx for t, bx in boxes if t.strip().startswith(first)]
            hit = False
            for (x0, y0, x1, y1) in mine:
                for t, (a0, b0, a1, b1) in boxes:
                    if t.strip().startswith(first) or not t.strip():
                        continue
                    if min(x1, a1) - max(x0, a0) > 0.03 and min(y1, b1) - max(y0, b0) > 0.04:
                        hit = True
            if hit:
                cur = p.get("label_pos") or "right"
                nxt = _CYCLE[(_CYCLE.index(cur) + 1) % 4] if cur in _CYCLE else "right"
                tried = p.setdefault("_tried", [cur])
                if nxt not in tried:
                    p["label_pos"] = nxt
                    tried.append(nxt)
                    moved += 1
    return moved


def _tabular(s: dict) -> bool:
    """A slide whose body is rows of text across the page: a table slide, or a grid with a table, Harvey-ball or
    text-only rows part."""
    a = s.get("archetype")
    return a in ("table_focus", "table_highlight") or (a == "grid" and any(
        isinstance(p, dict) and p.get("type") in ("table", "harvey") for p in s.get("parts") or []))


def _flex_rows(s: dict) -> bool:
    """A rows slide that can be drawn as columns instead: 2-4 risks or issues, or 2-4 next steps."""
    a = s.get("archetype")
    if a == "challenge_matrix":
        return 2 <= len(s.get("rows") or []) <= 4
    if a in ("closing", "next_steps"):
        return 2 <= len(s.get("steps") or []) <= 5
    return False


def _chart_kind(s: dict) -> str | None:
    """'flex' for a chart slide that can draw its points beside the chart or under it, 'short' for a chart of a few
    bars (beside its argument, on the left or mirrored), 'side' for one that always has a column beside its exhibit
    (exhibit_argument), None otherwise."""
    if not isinstance(s, dict):
        return None
    if s.get("archetype") == "exhibit_argument" or s.get("layout") == "side":   # asked for: kept
        return "side"
    if s.get("archetype") in CHART_SIDE and isinstance(s.get("chart"), dict) and \
            len(s["chart"].get("categories") or []) <= 5:
        return "short"
    if s.get("archetype") in CHART_SIDE and 2 <= len(s.get("bullets") or []) <= 4:
        return "flex"
    if s.get("archetype") in ("table_focus", "table_highlight") and 2 <= len(s.get("bullets") or []) <= 4 \
            and isinstance(s.get("table"), dict) and not s["table"].get("keep_table"):
        try:                                             # a small all-number table is drawn as a chart
            if _table_as_chart(normalize(s)["table"]):
                return "flex"
        except Exception:                                # noqa: BLE001 - a table the renderer will report on
            return None
    return None


def plan_chart_forms(slides: list) -> list:
    """Runs of chart slides alternate a column beside the chart ("side") and a row of cards under it ("band"), so two
    chart slides in a row never share a composition; fixed side pages (exhibit + argument) anchor the alternation."""
    out = list(slides)
    i = 0
    while i < len(out):
        if not _chart_kind(out[i]):
            i += 1
            continue
        j = i
        while j + 1 < len(out) and _chart_kind(out[j + 1]):
            j += 1
        want = None
        options = {"side": ["side"], "flex": ["side", "band"], "short": ["side", "mirror"]}
        for k in range(j, i - 1, -1):                    # from the end of the run back: alternate around fixed pages
            kind = _chart_kind(out[k])
            form = next((f for f in options[kind] if f != want), options[kind][0])
            if kind != "side":
                out[k] = dict(out[k], _form=form)
            want = form
        i = j + 1
    return out


def plan_forms(slides: list) -> list:
    """No two slides in a row look alike (house style): a risk matrix or next-steps slide next to a table-like slide
    is drawn as columns (icon columns, a numbered process), banded rows are used once per deck at most, and runs of
    chart slides alternate their composition."""
    slides = plan_chart_forms(slides)
    secs: dict = {}
    for k_, s in enumerate(slides):                       # "01 | Section" over every content slide's title
        if isinstance(s, dict) and s.get("archetype") not in ("cover", "section_divider", "agenda", "end",
                                                              "executive_summary") \
                and str(s.get("section") or "").strip() and str(s.get("section")).strip().lower() != "cover":
            # (the summary carries no number: its numbered points ARE the sections that follow)
            name = re.sub(r"\s+", " ", str(s["section"]).strip())
            secs.setdefault(name.lower(), len(secs) + 1)
            slides[k_] = dict(s, _section=f"{secs[name.lower()]:02d} | {name[:1].upper() + name[1:]}")
    out, prev_tab, rows_used = [], False, 0
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            out.append(s)
            continue
        nxt = slides[i + 1] if i + 1 < len(slides) and isinstance(slides[i + 1], dict) else {}
        if _flex_rows(s):
            if prev_tab or _tabular(nxt) or rows_used:
                out.append(dict(s, _form="columns"))
                prev_tab = False
                continue
            rows_used += 1
            out.append(s)
            prev_tab = True
            continue
        prv = slides[i - 1] if i > 0 and isinstance(slides[i - 1], dict) else {}
        side_next = nxt.get("archetype") == "exhibit_argument" or nxt.get("_form") == "side"
        side_prev = prv.get("archetype") == "exhibit_argument" or prv.get("_form") == "side"
        if s.get("archetype") == "grid" and (side_next or side_prev) and any(
                isinstance(p_, dict) and p_.get("type") == "chart" for p_ in s.get("parts") or []):
            s = dict(s, _form="mirror")                  # a chart grid beside a chart-and-column page: chart right
        out.append(s)
        prev_tab = _tabular(s)
    return out


CHART_SIDE = ("bar_takeaway", "column_trend", "line_trend", "stacked_composition", "waterfall_bridge")


def _render(slides: list[dict], out_path: str, template: str | None = None, carry: bool = True) -> dict:
    _register_diagram_archetypes()
    _register_library_archetype()
    _register_layout_archetype()
    prs = Presentation(template or default_template())
    for sid in list(prs.slides._sldIdLst):
        prs.part.drop_rel(sid.rId)
        prs.slides._sldIdLst.remove(sid)
    ctx = Ctx({})
    ctx.design = {}                                       # slide number -> which path drew it (design.py)
    errors = []
    queue = plan_forms(list(slides))
    n = 0
    from collections import Counter
    used = Counter(str(t) for s0 in slides if s0.get("archetype") not in DIVIDERS
                   for t in set(s0.get("tags") or []) if str(t).strip())
    deck_stamps = [t for t, k in used.most_common() if k >= 2][:2]   # "Preliminary" marks the deck: every slide (P19)
    while queue:
        s = queue.pop(0)
        mode = set_density(s.get("density") or DENSITY.get("deck"))
        own = [str(t) for t in s.get("tags") or [] if str(t).strip() and str(t) not in deck_stamps]
        ctx.stamps = (deck_stamps + own)[:2]               # a slide's own qualifier stays on that slide
        s = dict(condense(normalize(s), mode), tags=[])
        spec = s.pop("_design", None)                      # a design-first layout spec (design.py), if any
        n += 1
        ctx.carry = None
        if s.get("archetype") not in DIVIDERS and not s.get("_cont") and schema_errors(s):
            errors += [f"slide {n} ({s.get('archetype')}): {e}" for e in schema_errors(s)]
            s = fallback(s)
            if spec:
                ctx.design[n] = {"path": "archetype", "why": "the slide failed its schema; the archetype fallback "
                                 "draws it"}
                spec = None
        arch = s.get("archetype")
        fn = ARCHETYPES.get(arch)
        if fn is None:
            errors.append(f"slide {n}: archetype '{arch}' is not in the library {sorted(ARCHETYPES)}")
            fn = a_chart_takeaway if s.get("chart") else a_challenge_matrix
        if arch == "section_divider" and s.get("sections"):    # a divider in a deck with an agenda: the tracker
            arch, fn = "agenda", a_agenda
        sl = prs.slides.add_slide(layout(prs, "Title Slide" if arch in DIVIDERS else "Title Only"))
        sl._element.find(qn("p:cSld")).set("name", f"arch:{arch} node:{s.get('node', '')} density:{mode}"
                                           + (" computed:1" if s.get("fixed") else ""))
        src_ = s.get("source") if isinstance(s.get("source"), dict) else {}
        chart_ = s.get("chart") if isinstance(s.get("chart"), dict) else {}
        ctx.slide_prov = chart_.get("prov") or s.get("title_prov") or src_.get("prov")   # what derived figures
                                                                                          # drawn on the slide cite
        try:
            if spec:
                if not _designed(ctx, s, sl, n, spec, mode, arch):
                    fn(ctx, s, sl, n)
            else:
                fn(ctx, s, sl, n)
            if arch not in DIVIDERS:
                _footer(ctx, s, sl, n)
        except (KeyError, TypeError, IndexError, ValueError, AttributeError, ZeroDivisionError) as e:
            errors.append(f"slide {n} ({arch}): missing or malformed field: {type(e).__name__}: {e}")
            for shp in list(sl.shapes):                     # never leave a half-drawn slide: redraw it simply
                if not shp.is_placeholder:
                    shp._element.getparent().remove(shp._element)
            fb = fallback(dict(s, chart=None) if s.get("chart") else s)
            if fb.get("archetype") == arch:
                bl = [b for blk in s.get("blocks") or [] for b in [{"text": blk.get("text", "")}] + list(blk.get("bullets") or [])]
                fb = dict(s, archetype="argument_only", chart=None,
                          blocks=[{"text": "", "bullets": bl + list(s.get("bullets") or [])}])
            try:
                sl._element.find(qn("p:cSld")).set("name", f"arch:{fb['archetype']} node:{s.get('node', '')} density:{mode}")
                ARCHETYPES[fb["archetype"]](ctx, fb, sl, n)
                _footer(ctx, fb, sl, n)
            except Exception as e2:
                errors.append(f"slide {n}: fallback layout failed too: {type(e2).__name__}: {e2}")
        if s.get("notes"):
            sl.notes_slide.notes_text_frame.text = s["notes"]
        if ctx.carry and carry:
            queue.insert(0, ctx.carry)
            errors.append(f"slide {n}: rows carried to a continuation slide (content longer than one slide)")
        elif ctx.carry:                                     # fixed slide count: never an extra slide
            left = ctx.carry.get("rows") or (ctx.carry.get("table") or {}).get("rows") or []
            txt = "; ".join(_txt(r.get("label")) if isinstance(r, dict) else " | ".join(map(str, r)) for r in left)
            prev = sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else ""
            sl.notes_slide.notes_text_frame.text = (prev + f"\n[QA] Did not fit on the slide and left out: {txt}").strip()
            errors.append(f"slide {n}: rows carried to a continuation slide (content longer than one slide); "
                          f"{len(left)} rows left out because the slide count is fixed")
    errors += ctx.overflow
    apply_rtl(prs)
    _reading_order(prs)
    prs.save(out_path)
    try:                                                    # P4: at most the density's sizes per text role
        from .typescale import harmonize
        harmonize(out_path)
    except Exception as e:  # noqa: BLE001 - a QA pass never fails the render
        errors.append(f"type-scale pass skipped: {type(e).__name__}: {e}")
    prov_path = str(Path(out_path).with_suffix("")) + ".provenance.json"
    Path(prov_path).write_text(json.dumps({"slides": ctx.prov}, indent=1))
    return {"slides": n, "errors": errors, "provenance": prov_path, "design": ctx.design}


def _reading_order(prs) -> None:
    """Screen readers and the tab order follow the shape tree, so it runs top to bottom (P18). A stable sort on each
    shape's top edge: a panel or band starts above the text drawn on it, so it stays behind that text; shapes with
    the same top keep their drawing order."""
    tags = ("sp", "grpSp", "graphicFrame", "cxnSp", "pic")
    for sl in prs.slides:
        tree = sl.shapes._spTree
        shapes = [sh for sh in sl.shapes if sh._element.tag.rsplit("}", 1)[-1] in tags]
        tops = [round((sh.top if sh.top is not None else 0) / 9144) for sh in shapes]   # 0.01in steps
        title = [sh.is_placeholder and sh.placeholder_format.type in (1, 3) for sh in shapes]   # TITLE, CENTER_TITLE
        ti = title.index(True) if any(title) else None
        behind = set()                                     # fills drawn under the title (a full-bleed cover, a band)
        if ti is not None:                                 # stay under it: the title is read first, never hidden
            t = shapes[ti]
            for i in range(ti):
                sh = shapes[i]
                if (not (sh.has_text_frame and sh.text_frame.text.strip()) and None not in (sh.left, sh.top, t.left, t.top)
                        and sh.left < t.left + t.width and t.left < sh.left + sh.width
                        and sh.top < t.top + t.height and t.top < sh.top + sh.height):
                    behind.add(i)
        rank = [0 if i in behind else 1 if title[i] else 2 for i in range(len(shapes))]
        order = sorted(range(len(shapes)), key=lambda i: (rank[i], tops[i] if rank[i] == 2 else 0, i))
        if order == list(range(len(shapes))):
            continue
        anchor = shapes[-1]._element.getnext()           # extLst (if any) stays last
        els = [shapes[i]._element for i in order]
        for el in els:
            tree.remove(el)
        for el in els:
            if anchor is not None:
                anchor.addprevious(el)
            else:
                tree.append(el)


def _designed(ctx, s, sl, n, spec, mode, arch) -> bool:
    """Draw the slide from its design spec (design.py). On a miss the half-drawn shapes are removed, what the attempt
    reported is forgotten, and False lets the archetype draw it; the reason is kept in ctx.design."""
    from .design import draw_design
    before, notes = len(ctx.overflow), s.get("notes")
    try:
        draw_design(ctx, s, sl, n, spec)
        sl._element.find(qn("p:cSld")).set("name", f"arch:{arch} node:{s.get('node', '')} density:{mode} design:image")
        ctx.design[n] = {"path": "image", "layout": spec.get("layout")}
        return True
    except Exception as e:  # noqa: BLE001 - any failure of the rebuild: the archetype draws the slide
        for shp in list(sl.shapes):
            if not shp.is_placeholder:
                shp._element.getparent().remove(shp._element)
        del ctx.overflow[before:]
        if notes is None:
            s.pop("notes", None)
        else:
            s["notes"] = notes
        ctx.design[n] = {"path": "archetype", "why": f"{type(e).__name__}: {e}"[:300]}
        return False

