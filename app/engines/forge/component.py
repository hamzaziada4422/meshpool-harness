"""Library components: a layout from the private slide library, drawn as native, editable shapes in the deck's theme.

A library item is a one-slide .pptx in $MESHPOOL_KB. A `library` slide names one item and gives the text for its
slots; the renderer re-expresses the item's shapes (never a picture) in theme colours and theme fonts, scales them
into the body, writes the slot texts and leaves the rest to the rules (P5 type floors, P6 theme colours, P8 overlaps
declared with the `ov_` prefix, P9 zero pictures, P17 group depth). Nothing from the library is stored in the repo; it
is read at render time only, and nothing that names a library item is written into shape names, alt text or notes.

    {"archetype": "library", "item": "<library id>", "slots": {"17": "Where the money goes", "10": "01"},
     "title": "...", "subtitle": "...", "takeaway": {"text": "..."}, "bullets": ["..."], "source": {"text": "..."},
     "fallback": {an ordinary slide dict, drawn instead when the library is off or the item cannot be used}}

`slots` keys are the shape's index in the item's top-level shape order (what the slot labels call `k`); a value is the
text (lines separated by newlines) or, for a table, a list of rows. Every other text in the item is cleared.

Off unless MESHPOOL_KB is set and the slide asks for it. Colour mapping (restyle): near white -> bg1, light grey or
tint -> bg2, near black -> tx1, dark colour -> tx2, saturated mid tones -> accent1 for the first hue, accent3 for the
second, accent2 and accent4 for further hues, mid greys -> accent6, light greys -> accent5, pastel tones -> accent4.
"""
from __future__ import annotations

import colorsys
import copy
import io
import re
import zipfile
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Inches

from . import kb
from . import parts as P
from . import render as R

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PN = "http://schemas.openxmlformats.org/presentationml/2006/main"
RN = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMU = 914400
MIN_PT, MAX_PT = 10.0, 44.0     # 44: a template's big KPI figures stay big (a 28 pt cap shrank them)
MAX_LINE = 19050                       # 1.5 pt: P9 forbids heavier unconnected rules
MAX_SCALE = 1.6

COLOR_TAGS = {f"{{{A}}}{t}" for t in ("srgbClr", "sysClr", "prstClr", "scrgbClr", "hslClr", "schemeClr")}
BAD_TAGS = {f"{{{A}}}{t}" for t in ("gradFill", "effectLst", "effectDag", "scene3d", "sp3d", "blipFill", "blip",
                                    "pattFill", "latin", "ea", "cs", "sym", "buFont", "hlinkClick", "hlinkHover",
                                    "hlinkMouseOver", "extLst")} | {f"{{{PN}}}{t}" for t in
                                                                    ("pic", "style", "custDataLst", "extLst")}
SKIP_PH = {"sldNum", "dt", "ftr"}
# 140 preset named colours that matter (the rest map by their luminance through the default branch)
PRST = {"black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF", "yellow": "FFFF00",
        "gray": "808080", "grey": "808080", "silver": "C0C0C0", "orange": "FFA500", "navy": "000080",
        "dkGray": "A9A9A9", "ltGray": "D3D3D3", "dkBlue": "00008B", "dkRed": "8B0000", "dkGreen": "006400",
        "ltBlue": "ADD8E6", "ltGreen": "90EE90", "ltYellow": "FFFFE0", "cyan": "00FFFF", "magenta": "FF00FF"}
SYS = {"windowText": "000000", "window": "FFFFFF", "menuText": "000000", "btnFace": "F0F0F0", "btnText": "000000",
       "grayText": "808080"}
NS_SCHEME = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
SLOTS = {"bg1": "lt1", "bg2": "lt2", "tx1": "dk1", "tx2": "dk2", "accent1": "accent1", "accent2": "accent2",
         "accent3": "accent3", "accent4": "accent4", "accent5": "accent5", "accent6": "accent6"}
DEFAULT_THEME = {"dk1": "0A1A2F", "lt1": "FFFFFF", "dk2": "0A2540", "lt2": "EEF3FA", "accent1": "1E5BD8",
                 "accent2": "D6E3F8", "accent3": "29B6E8", "accent4": "1A73B8", "accent5": "BFBFBF",
                 "accent6": "6B6B6B"}
HUE_SLOTS = ("accent1", "accent3", "accent2", "accent4")


class Unusable(Exception):
    """The item cannot be re-expressed (nested groups, nothing left, a colour that will not map)."""


def a(t):
    return f"{{{A}}}{t}"


def pn(t):
    return f"{{{PN}}}{t}"


def local(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


# ------------------------------------------------------------------ loading
_BYTES: dict = {}


def _path(item_id):
    if not item_id or not kb.available():
        return None
    return kb.item_file(str(item_id))


def load(item_id):
    """The item's one-slide Presentation, or None when the knowledge base is off or the item is missing."""
    p = _path(item_id)
    if p is None:
        return None
    key = (str(p), p.stat().st_mtime_ns)
    if key not in _BYTES:
        _BYTES.clear()
        _BYTES[key] = p.read_bytes()
    try:
        prs = Presentation(io.BytesIO(_BYTES[key]))
    except Exception:  # noqa: BLE001 - a damaged file is an unusable item
        return None
    return prs if len(prs.slides) >= 1 else None


# ------------------------------------------------------------------ colours
def _lum(hexv: str) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexv[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _ratio(h1: str, h2: str) -> float:
    l1, l2 = sorted((_lum(h1), _lum(h2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _hls(hexv):
    r, g, b = (int(hexv[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, s, l


def _hex(r, g, b):
    return "".join(f"{max(0, min(255, int(round(v * 255)))):02X}" for v in (r, g, b))


def source_theme(prs) -> tuple[dict, dict]:
    """(slot -> hex, colour map) of the item's own theme, used only to tell what its scheme colours looked like."""
    theme, cmap = {}, {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
    try:
        master = prs.slide_masters[0]
        cm = master._element.find(pn("clrMap"))
        if cm is not None:
            for k in cmap:
                if cm.get(k):
                    cmap[k] = cm.get(k)
        for rel in master.part.rels.values():
            if rel.reltype.endswith("/theme"):
                root = etree.fromstring(rel.target_part.blob)
                cs = root.find(f".//{a('clrScheme')}")
                for ch_ in cs:
                    c = ch_[0]
                    theme[local(ch_)] = (c.get("lastClr") or c.get("val") or "000000") if local(c) == "sysClr" \
                        else (c.get("val") or "000000")
    except Exception:  # noqa: BLE001
        pass
    return theme, cmap


def target_theme(sl) -> dict:
    """The deck's palette (slot -> hex) read from the slide's theme part; the engine's default when unreadable."""
    try:
        master = sl.slide_layout.slide_master
        for rel in master.part.rels.values():
            if rel.reltype.endswith("/theme"):
                root = etree.fromstring(rel.target_part.blob)
                cs = root.find(f".//{a('clrScheme')}")
                out = dict(DEFAULT_THEME)
                for ch_ in cs:
                    c = ch_[0]
                    out[local(ch_)] = ((c.get("lastClr") or "000000") if local(c) == "sysClr" else c.get("val")
                                       or "000000")
                return out
    except Exception:  # noqa: BLE001
        pass
    return dict(DEFAULT_THEME)


class Mapper:
    """Colour -> theme slot, by role. The first saturated hue of an item gets accent1, the second accent3, then
    accent2 and accent4; later hues share the nearest earlier one."""

    def __init__(self, src_theme=None, clrmap=None):
        self.src = src_theme or {}
        self.cmap = clrmap or NS_SCHEME
        self.hues: list[float] = []

    def effective(self, c) -> str:
        """The hex a colour element shows (transforms applied); '000000' when it cannot be resolved."""
        t = local(c)
        if t == "srgbClr":
            base = (c.get("val") or "000000")
        elif t == "sysClr":
            base = c.get("lastClr") or SYS.get(c.get("val"), "000000")
        elif t == "prstClr":
            base = PRST.get(c.get("val"), "808080")
        elif t == "scrgbClr":
            base = _hex(*(int(c.get(k) or 0) / 100000 for k in ("r", "g", "b")))
        elif t == "hslClr":
            r, g, b = colorsys.hls_to_rgb(int(c.get("hue") or 0) / 21600000, int(c.get("lum") or 0) / 100000,
                                          int(c.get("sat") or 0) / 100000)
            base = _hex(r, g, b)
        else:                                                  # schemeClr
            v = c.get("val") or "tx1"
            v = self.cmap.get(v, v)
            base = self.src.get(v) or {"lt1": "FFFFFF", "dk1": "000000", "lt2": "EEEEEE", "dk2": "333333"}.get(v)
            if base is None:
                base = "808080" if v.startswith("accent") else "000000"
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", base or ""):
            base = "000000"
        h, s, l = _hls(base)
        h /= 360
        for m in c:
            t2, v = local(m), int(m.get("val") or 0) / 100000
            if t2 == "lumMod":
                l *= v
            elif t2 == "lumOff":
                l += v
            elif t2 == "satMod":
                s *= v
            elif t2 == "satOff":
                s += v
            elif t2 == "tint":
                l = l + (1 - l) * (1 - v)
            elif t2 == "shade":
                l *= v
        l, s = max(0.0, min(1.0, l)), max(0.0, min(1.0, s))
        return _hex(*colorsys.hls_to_rgb(h, l, s))

    def role(self, hexv: str) -> str:
        h, s, l = _hls(hexv)
        if l >= 0.965:
            return "bg1"
        if s < 0.13:                                            # greys
            if l >= 0.82:
                return "bg2"
            if l <= 0.18:
                return "tx1"
            return "accent5" if l >= 0.62 else "accent6"
        if l >= 0.86:
            return "bg2"
        if l <= 0.12:
            return "tx1"
        if l <= 0.30:
            return "tx2"
        if l >= 0.64 or s < 0.2:
            return "accent4" if l >= 0.64 else "accent6"
        for i, hh in enumerate(self.hues):
            d = abs(h - hh)
            if min(d, 360 - d) <= 35:
                return HUE_SLOTS[min(i, len(HUE_SLOTS) - 1)]
        self.hues.append(h)
        return HUE_SLOTS[min(len(self.hues) - 1, len(HUE_SLOTS) - 1)]

    def convert(self, c):
        """Replace colour element c by a schemeClr of its role (children, transforms and alpha dropped)."""
        new = etree.Element(a("schemeClr"), val=self.role(self.effective(c)))
        par = c.getparent()
        par.replace(c, new)
        return new


# ------------------------------------------------------------------ small XML helpers
def _xfrm(el):
    t = local(el)
    if t in ("sp", "cxnSp"):
        sp = el.find(pn("spPr"))
        return sp.find(a("xfrm")) if sp is not None else None
    if t == "grpSp":
        sp = el.find(pn("grpSpPr"))
        return sp.find(a("xfrm")) if sp is not None else None
    if t == "graphicFrame":
        return el.find(pn("xfrm"))
    return None


def _rect(el):
    x = _xfrm(el)
    if x is None:
        return None
    o, e = x.find(a("off")), x.find(a("ext"))
    if o is None or e is None:
        return None
    return int(o.get("x")), int(o.get("y")), int(e.get("cx")), int(e.get("cy"))


def _set_rect(el, x, y, w, h):
    xf = _xfrm(el)
    xf.find(a("off")).set("x", str(int(round(x))))
    xf.find(a("off")).set("y", str(int(round(y))))
    xf.find(a("ext")).set("cx", str(max(0, int(round(w)))))
    xf.find(a("ext")).set("cy", str(max(0, int(round(h)))))


def _cnv(el):
    for t in ("nvSpPr", "nvGrpSpPr", "nvCxnSpPr", "nvGraphicFramePr"):
        nv = el.find(pn(t))
        if nv is not None:
            return nv.find(pn("cNvPr"))
    return None


def _text_of(el) -> str:
    return "".join(t.text or "" for t in el.iter(a("t"))).strip()


def _spPr(el):
    return el.find(pn("spPr"))


def _fill_el(spPr):
    if spPr is None:
        return None
    for ch in spPr:
        if local(ch) in ("solidFill", "noFill", "gradFill", "pattFill", "blipFill", "grpFill"):
            return ch
    return None


def _insert_after_geom(spPr, el):
    pos = 0
    for i, ch in enumerate(spPr):
        if local(ch) in ("xfrm", "custGeom", "prstGeom"):
            pos = i + 1
    spPr.insert(pos, el)


def _grad_to_solid(g):
    gs = g.find(f"{a('gsLst')}/{a('gs')}")
    solid = etree.Element(a("solidFill"))
    if gs is not None and len(gs):
        solid.append(copy.deepcopy(gs[0]))
    else:
        etree.SubElement(solid, a("schemeClr"), val="bg2")
    return solid


def _patt_to_solid(g):
    fg = g.find(a("fgClr"))
    solid = etree.Element(a("solidFill"))
    if fg is not None and len(fg):
        solid.append(copy.deepcopy(fg[0]))
    else:
        etree.SubElement(solid, a("schemeClr"), val="bg2")
    return solid


def _depth_check(el):
    for g in el.iter(pn("grpSp")):
        if g.find(f".//{pn('grpSp')}") is not None:
            raise Unusable("a group is nested inside a group")


# ------------------------------------------------------------------ restyle
def _is_table(el) -> bool:
    return local(el) == "graphicFrame" and el.find(f".//{a('tbl')}") is not None


def _first_color(node):
    """The first colour child of a fill-like node (solidFill, fillRef, fontRef ...)."""
    if node is None:
        return None
    for ch in node:
        if etree.QName(ch).namespace == A and ch.tag in COLOR_TAGS:
            return ch
    return None


def _resolve_style(el):
    """Replace p:style by explicit fill, line and text colour where the shape has none of its own."""
    st = el.find(pn("style"))
    if st is None:
        return
    spPr = _spPr(el)
    ref = lambda n: st.find(a(n))  # noqa: E731
    if spPr is not None and local(el) == "sp":
        fr = ref("fillRef")
        if _fill_el(spPr) is None:
            c = _first_color(fr)
            idx = int(fr.get("idx") or 0) if fr is not None else 0
            if idx > 0 and c is not None:
                f = etree.Element(a("solidFill"))
                f.append(copy.deepcopy(c))
            else:
                f = etree.Element(a("noFill"))
            _insert_after_geom(spPr, f)
    lr = ref("lnRef")
    if spPr is not None:
        ln = spPr.find(a("ln"))
        idx = int(lr.get("idx") or 0) if lr is not None else 0
        c = _first_color(lr)
        has_fill = ln is not None and any(local(x) in ("solidFill", "noFill", "gradFill", "pattFill") for x in ln)
        if not has_fill:
            if ln is None:
                ln = etree.Element(a("ln"))
                kids = [i for i, ch in enumerate(spPr) if local(ch) in ("xfrm", "custGeom", "prstGeom", "solidFill",
                                                                         "noFill", "gradFill", "pattFill", "blipFill")]
                spPr.insert(max(kids) + 1 if kids else 0, ln)
            if idx > 0 and c is not None:
                if ln.get("w") is None:
                    ln.set("w", str({1: 9525, 2: 15875}.get(idx, 19050)))
                f = etree.Element(a("solidFill"))
                f.append(copy.deepcopy(c))
                ln.insert(0, f)
            else:
                ln.insert(0, etree.Element(a("noFill")))
    fnt = ref("fontRef")
    fc = _first_color(fnt)
    if fc is not None:
        for tag in ("rPr", "endParaRPr"):
            for r in el.iter(a(tag)):
                if _first_fill_child(r) is None:
                    _rpr_color(r, copy.deepcopy(fc))
    el.remove(st)


def _first_fill_child(r):
    for ch in r:
        if local(ch) in ("solidFill", "noFill", "gradFill", "pattFill", "blipFill"):
            return ch
    return None


def _rpr_color(r, c):
    f = etree.Element(a("solidFill"))
    f.append(c)
    pos = 1 if (len(r) and local(r[0]) == "ln") else 0
    r.insert(pos, f)


def _prune(el, k: int) -> bool:
    """Drop what cannot be a native shape inside el; False when el itself should go. Raises Unusable on nesting."""
    t = local(el)
    if t == "pic":
        return False
    cnv = _cnv(el)
    if cnv is not None and cnv.get("hidden") in ("1", "true"):
        return False
    if t == "graphicFrame" and not _is_table(el):
        return False
    if t == "sp":
        sp = _spPr(el)
        if sp is not None and sp.find(a("blipFill")) is not None:
            return False
    if t == "grpSp":
        for ch in list(el):
            if local(ch) in ("sp", "grpSp", "cxnSp", "graphicFrame", "pic") and not _prune(ch, k):
                el.remove(ch)
            elif local(ch) == "graphicFrame":
                el.remove(ch)
        return any(local(ch) in ("sp", "grpSp", "cxnSp") for ch in el)
    return True


def _clean_generic(el):
    """Remove relationships, fonts, effects, 3-D and extensions from the whole subtree; gradients and patterns
    become solid fills of their first stop."""
    for g in list(el.iter(a("gradFill"))):
        g.getparent().replace(g, _grad_to_solid(g))
    for g in list(el.iter(a("pattFill"))):
        g.getparent().replace(g, _patt_to_solid(g))
    for node in list(el.iter()):
        if not isinstance(node.tag, str) or node.getparent() is None:
            continue
        if node.tag in BAD_TAGS and node is not el:
            par = node.getparent()
            if par is not None:
                par.remove(node)
            continue
        for k in list(node.attrib):
            if k.startswith(f"{{{RN}}}"):
                del node.attrib[k]
    for sh in el.iter(pn("cNvPr")):
        for k in ("descr", "title"):
            sh.attrib.pop(k, None)
    for t in ("bodyPr",):
        for bp in el.iter(a(t)):
            for ch in list(bp):
                if local(ch) in ("normAutofit", "spAutoFit", "noAutofit", "prstTxWarp", "scene3d", "sp3d"):
                    bp.remove(ch)
    for t in ("txBody",):
        pass
    # Wingdings and Symbol bullets need their font; use a plain bullet instead
    for bu in el.iter(a("buChar")):
        ch = bu.get("char") or ""
        if ch and (ord(ch[0]) > 0xE000 or ord(ch[0]) < 0x20 or ch in "üqnl§Ø"):
            bu.set("char", "•")


def _inherit_ph_sizes(sh, prs) -> dict:
    """Sizes (and bold) a placeholder inherits through layout and master, by paragraph level: {lvl: (sz, b)}."""
    out: dict = {}
    node = sh
    for _ in range(3):
        node = getattr(node, "_base_placeholder", None)
        if node is None:
            break
        ls = node._element.find(f"{pn('txBody')}/{a('lstStyle')}")
        if ls is None:
            continue
        for lvl in range(1, 10):
            d = ls.find(f"{a(f'lvl{lvl}pPr')}/{a('defRPr')}")
            if d is not None and (lvl - 1) not in out and d.get("sz"):
                out[lvl - 1] = (d.get("sz"), d.get("b"))
    try:
        ph = sh._element.find(f".//{pn('ph')}")
        typ = ph.get("type") if ph is not None else None
        style = "titleStyle" if typ in ("title", "ctrTitle") else ("otherStyle" if typ in ("subTitle",) else "bodyStyle")
        if typ is None or typ == "body" or typ == "obj":
            style = "bodyStyle"
        tx = prs.slide_masters[0]._element.find(pn("txStyles"))
        st = tx.find(pn(style)) if tx is not None else None
        if st is not None:
            for lvl in range(1, 10):
                d = st.find(f"{a(f'lvl{lvl}pPr')}/{a('defRPr')}")
                if d is not None and (lvl - 1) not in out and d.get("sz"):
                    out[lvl - 1] = (d.get("sz"), d.get("b"))
    except Exception:  # noqa: BLE001
        pass
    return out


def _bake_placeholders(slide, prs) -> list:
    """(k, element) of every top-level shape, with placeholder geometry and sizes written into the element itself.
    Slide number, date and footer placeholders are left out (the deck draws its own footer)."""
    pairs = []
    for k, sh in enumerate(slide.shapes):
        el = sh._element
        ph = el.find(f".//{pn('nvPr')}/{pn('ph')}") if local(el) == "sp" else None
        if ph is not None:
            if ph.get("type") in SKIP_PH:
                continue
            try:
                l, t, w, h = sh.left, sh.top, sh.width, sh.height
            except Exception:  # noqa: BLE001
                continue
            if None in (l, t, w, h):
                continue
            el = copy.deepcopy(el)
            sp = el.find(pn("spPr"))
            if sp is None:
                continue
            xf = sp.find(a("xfrm"))
            if xf is None:
                xf = etree.Element(a("xfrm"))
                sp.insert(0, xf)
                etree.SubElement(xf, a("off"))
                etree.SubElement(xf, a("ext"))
            xf.find(a("off")).set("x", str(int(l)))
            xf.find(a("off")).set("y", str(int(t)))
            xf.find(a("ext")).set("cx", str(int(w)))
            xf.find(a("ext")).set("cy", str(int(h)))
            inh = _inherit_ph_sizes(sh, prs)
            tb = el.find(pn("txBody"))
            if tb is not None and inh:
                ls = tb.find(a("lstStyle"))
                if ls is None:
                    ls = etree.Element(a("lstStyle"))
                    tb.insert(1, ls)
                for lvl, (sz, b) in inh.items():
                    pp = ls.find(a(f"lvl{lvl + 1}pPr"))
                    if pp is None:
                        pp = etree.SubElement(ls, a(f"lvl{lvl + 1}pPr"))
                    d = pp.find(a("defRPr"))
                    if d is None:
                        d = etree.SubElement(pp, a("defRPr"))
                    d.set("sz", d.get("sz") or sz)
                    if b and d.get("b") is None:
                        d.set("b", b)
            nv = el.find(f"{pn('nvSpPr')}/{pn('nvPr')}")
            nv.remove(nv.find(pn("ph")))
        pairs.append((k, el))
    return pairs


def _base_size(el, lvl: int) -> float:
    """The size a paragraph of this shape shows when its run sets none: the shape's own list style, else 18 pt."""
    ls = el.find(f"{pn('txBody')}/{a('lstStyle')}")
    if ls is not None:
        d = ls.find(f"{a(f'lvl{min(lvl, 8) + 1}pPr')}/{a('defRPr')}")
        if d is not None and d.get("sz"):
            return int(d.get("sz")) / 100
    return 18.0


def _bake_sizes(el):
    """Give every run, paragraph end and list-style level an explicit size (nothing inherited is left to scale)."""
    for txb in el.iter(pn("txBody"), a("txBody")):
        for p_ in txb.findall(a("p")):
            ppr = p_.find(a("pPr"))
            lvl = int(ppr.get("lvl") or 0) if ppr is not None else 0
            base = _base_size(txb.getparent(), lvl) if txb.getparent() is not None else 18.0
            bold = None
            ls = txb.find(a("lstStyle"))
            if ls is not None:
                d = ls.find(f"{a(f'lvl{min(lvl, 8) + 1}pPr')}/{a('defRPr')}")
                if d is not None:
                    bold = d.get("b")
            for r in list(p_.findall(a("r"))) + list(p_.findall(a("fld"))):
                rpr = r.find(a("rPr"))
                if rpr is None:
                    rpr = etree.Element(a("rPr"), lang="en-US")
                    r.insert(0, rpr)
                if rpr.get("sz") is None:
                    rpr.set("sz", str(int(round(base * 100))))
                if bold and rpr.get("b") is None:
                    rpr.set("b", bold)
            end = p_.find(a("endParaRPr"))
            if end is not None and end.get("sz") is None:
                end.set("sz", str(int(round(base * 100))))


def _default_colors(el):
    """Runs with no colour of their own take the list style's colour, else the text colour (tx1)."""
    for txb in el.iter(pn("txBody"), a("txBody")):
        ls = txb.find(a("lstStyle"))
        for p_ in txb.findall(a("p")):
            ppr = p_.find(a("pPr"))
            lvl = int(ppr.get("lvl") or 0) if ppr is not None else 0
            col = None
            if ls is not None:
                d = ls.find(f"{a(f'lvl{min(lvl, 8) + 1}pPr')}/{a('defRPr')}")
                if d is not None:
                    f = d.find(a("solidFill"))
                    col = _first_color(f)
            for r in p_.findall(a("r")) + p_.findall(a("fld")):
                rpr = r.find(a("rPr"))
                if rpr is not None and _first_fill_child(rpr) is None:
                    c = copy.deepcopy(col) if col is not None else etree.Element(a("schemeClr"), val="tx1")
                    _rpr_color(rpr, c)


def _map_colors(el, mapper: Mapper):
    for c in list(el.iter(*COLOR_TAGS)):
        if c.getparent() is None:
            continue
        par = c.getparent()
        if local(par) in ("fillRef", "lnRef", "fontRef", "effectRef"):
            par.remove(c)
            continue
        mapper.convert(c)
    for h in list(el.iter(a("highlight"))):             # a highlighter colour has no role in the theme
        h.getparent().remove(h)


def _table_style(el, mapper):
    """The table style is not carried: header and banded rows get explicit theme fills and a rule under each row."""
    tbl = el.find(f".//{a('tbl')}")
    tp = tbl.find(a("tblPr"))
    first = tp is not None and tp.get("firstRow") in ("1", "true")
    band = tp is not None and tp.get("bandRow") in ("1", "true")
    if tp is not None:
        for ch in list(tp):
            if local(ch) == "tableStyleId":
                tp.remove(ch)
    for ri, tr in enumerate(tbl.findall(a("tr"))):
        for tc in tr.findall(a("tc")):
            pr = tc.find(a("tcPr"))
            if pr is None:
                pr = etree.SubElement(tc, a("tcPr"))
            has = any(local(ch) in ("solidFill", "noFill") for ch in pr)
            head = first and ri == 0
            if not has:
                role = "tx2" if head else ("bg2" if band and ri % 2 == 1 else None)
                if role:
                    f = etree.Element(a("solidFill"))
                    etree.SubElement(f, a("schemeClr"), val=role)
                    pr.append(f)
            if pr.find(a("lnB")) is None:
                ln = etree.Element(a("lnB"), w="6350")
                f = etree.SubElement(ln, a("solidFill"))
                etree.SubElement(f, a("schemeClr"), val="accent5")
                pos = sum(1 for ch in pr if local(ch) in ("lnL", "lnR", "lnT"))
                pr.insert(pos, ln)
            if head:
                for r in tc.iter(a("rPr")):
                    fl = _first_fill_child(r)
                    if fl is None or (_first_color(fl) is not None and _first_color(fl).get("val") == "tx1"):
                        if fl is not None:
                            r.remove(fl)
                        c = etree.Element(a("schemeClr"), val="bg1")
                        _rpr_color(r, c)


def _drop_diagonals(el) -> bool:
    """An unconnected diagonal line is decoration (P9): False when el should be removed."""
    if local(el) != "cxnSp":
        return True
    r = _rect(el)
    nv = el.find(f"{pn('nvCxnSpPr')}/{pn('cNvCxnSpPr')}")
    glued = nv is not None and (nv.find(a("stCxn")) is not None or nv.find(a("endCxn")) is not None)
    if r and not glued and r[2] > 0.02 * EMU and r[3] > 0.02 * EMU and min(r[2], r[3]) > 0.02 * max(r[2], r[3]):
        return False
    return True


def _finalize_names(el, k, ids: list):
    kinds = {"sp": "Shape", "grpSp": "Group", "cxnSp": "Line", "graphicFrame": "Table"}
    n = 0
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        if node.tag == pn("cNvPr"):
            parent_el = node.getparent().getparent()
            kind = kinds.get(local(parent_el), "Shape")
            if local(parent_el) == "sp" and _text_of(parent_el):
                kind = "Text"
            node.set("name", f"ov_Lib{k}_{kind}" if parent_el is el else f"ov_Lib{k}_{n}_{kind}")
            node.set("_old", node.get("id") or "")
            ids.append(node)
            n += 1


def _scan(el):
    """Anything a theme-only, picture-free slide forbids that is still in the shape: the item is unusable."""
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        t = node.tag
        if t in BAD_TAGS and t != f"{{{PN}}}style" or t in COLOR_TAGS and local(node) != "schemeClr":
            raise Unusable(f"{local(node)} left in the shape")
        if t in COLOR_TAGS and node.get("val") not in SLOTS and node.get("val") not in ("tx1", "bg1", "bg2", "tx2"):
            raise Unusable("a colour outside the theme")
        for k in node.attrib:
            if k.startswith(f"{{{RN}}}"):
                raise Unusable("a relationship is left in the shape")


def _clamp_sz(v: float) -> int:
    return int(round(max(MIN_PT, min(MAX_PT, v)) * 2)) * 50


def _scale_el(el, scale: float, top: bool = True):
    """Fonts (clamped 10-44 pt), insets, spacing, indents, line weights and table dimensions by `scale`."""
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        t = local(node)
        if t in ("rPr", "endParaRPr", "defRPr") and node.get("sz"):
            node.set("sz", str(_clamp_sz(int(node.get("sz")) / 100 * scale)))
        elif t == "bodyPr":
            for k, d in (("lIns", 91440), ("rIns", 91440), ("tIns", 45720), ("bIns", 45720)):
                node.set(k, str(int(int(node.get(k) if node.get(k) is not None else d) * scale)))
        elif t == "pPr":
            for k in ("marL", "marR", "indent"):
                if node.get(k):
                    node.set(k, str(int(int(node.get(k)) * scale)))
        elif t in ("spcBef", "spcAft"):
            sp = node.find(a("spcPts"))
            if sp is not None:
                sp.set("val", str(int(int(sp.get("val")) * scale)))
        elif t == "lnSpc":
            if node.find(a("spcPts")) is not None:
                node.getparent().remove(node)
        elif t in ("ln", "lnL", "lnR", "lnT", "lnB") and node.get("w"):
            node.set("w", str(max(3175, min(MAX_LINE, int(int(node.get("w")) * scale)))))
        elif t == "tcPr":
            for k in ("marL", "marR", "marT", "marB"):
                if node.get(k):
                    node.set(k, str(int(int(node.get(k)) * scale)))
        elif t == "gridCol":
            node.set("w", str(int(int(node.get("w")) * scale)))
        elif t == "tr":
            node.set("h", str(int(int(node.get("h")) * scale)))


def restyle(elements, scale: float = 1.0, theme: dict | None = None, finish: bool = True, src_theme=None,
            clrmap=None):
    """Deep-copy top-level shapes (p:sp, p:grpSp, p:cxnSp and table graphicFrames only) as native, theme-only shapes.

    Pictures, charts, SmartArt, OLE and media are dropped; an item whose group nests deeper than one level raises
    Unusable. Every colour becomes a schemeClr by role (see the module doc); gradients become the first stop's solid
    fill; effects, shadows and 3-D go; p:style becomes explicit fill, line and text colour; typefaces go (theme
    fonts); every shape is named ov_Lib<k>_... (a declared overlay); sizes are multiplied by `scale` and clamped
    to 10-28 pt (finish=False leaves geometry, sizes and widths for the caller to scale).
    `elements` are elements or (k, element) pairs; the result has the same form. `theme` (slot -> hex) is the
    deck's palette, used to keep text readable on the fill behind it."""
    theme = theme or DEFAULT_THEME
    mapper = Mapper(src_theme, clrmap)
    pairs = [(i, e) if not isinstance(e, tuple) else e for i, e in enumerate(elements)]
    out = []
    for k, el0 in pairs:
        if local(el0) not in ("sp", "grpSp", "cxnSp", "graphicFrame"):
            continue
        el = copy.deepcopy(el0)
        if not _prune(el, k):
            continue
        _depth_check(el)
        _bake_sizes(el)
        for node in list(el.iter(pn("sp"), pn("cxnSp"))):
            _resolve_style(node)
        _clean_generic(el)
        for node in list(el.iter(pn("cxnSp"))):
            if node is not el and not _drop_diagonals(node):
                node.getparent().remove(node)
        if local(el) == "cxnSp" and not _drop_diagonals(el):
            continue
        if _is_table(el):
            _table_style(el, mapper)
        _default_colors(el)
        _map_colors(el, mapper)
        out.append((k, el))
    if finish:
        for k, el in out:
            _scale_el(el, scale)
            _contrast([el], theme)
            _scan(el)
    plain = bool(elements) and not isinstance(elements[0], tuple)
    return [e for _, e in out] if plain else out


# ------------------------------------------------------------------ contrast
def _slot_hex(role: str, theme: dict) -> str:
    return theme.get(SLOTS.get(role, "dk1"), "000000")


def _flatten(el, parent=None):
    """(element, x, y, w, h) in slide EMU, z-order, descending one group level."""
    r = _rect(el)
    if r is None:
        return
    yield el, *r
    if local(el) == "grpSp":
        xf = _xfrm(el)
        co, ce = xf.find(a("chOff")), xf.find(a("chExt"))
        if co is None or ce is None or not int(ce.get("cx")) or not int(ce.get("cy")):
            return
        sx, sy = r[2] / int(ce.get("cx")), r[3] / int(ce.get("cy"))
        for ch in el:
            if local(ch) in ("sp", "cxnSp", "grpSp", "graphicFrame"):
                cr = _rect(ch)
                if cr is None:
                    continue
                yield ch, r[0] + (cr[0] - int(co.get("x"))) * sx, r[1] + (cr[1] - int(co.get("y"))) * sy, \
                    cr[2] * sx, cr[3] * sy


def _own_fill_role(el):
    sp = _spPr(el)
    f = _fill_el(sp)
    if f is not None and local(f) == "solidFill":
        c = _first_color(f)
        return c.get("val") if c is not None else None
    return None


def _contrast(elements, theme, base_role="bg1"):
    """Text on a fill (or on the shape beneath it) that would be hard to read flips to bg1 or tx1."""
    flat = []
    for el in elements:
        flat.extend(_flatten(el))
    for i, (el, x, y, w, h) in enumerate(flat):
        if local(el) not in ("sp", "graphicFrame") or not _text_of(el):
            continue
        back = _own_fill_role(el) if local(el) == "sp" else None
        if back is None:
            cx, cy = x + w / 2, y + h / 2
            back = base_role
            for el2, x2, y2, w2, h2 in flat[:i]:
                if local(el2) == "sp" and x2 <= cx <= x2 + w2 and y2 <= cy <= y2 + h2:
                    r2 = _own_fill_role(el2)
                    if r2 is not None:
                        back = r2
        bh = _slot_hex(back, theme)
        for rpr in el.iter(a("rPr")):
            f = rpr.find(a("solidFill"))
            c = _first_color(f)
            if c is None:
                continue
            if _ratio(_slot_hex(c.get("val"), theme), bh) < 4.5:
                best = "bg1" if _ratio(theme.get("lt1", "FFFFFF"), bh) >= _ratio(theme.get("dk1", "000000"), bh) \
                    else "tx1"
                if _ratio(_slot_hex(best, theme), bh) > _ratio(_slot_hex(c.get("val"), theme), bh):
                    c.set("val", best)


# ------------------------------------------------------------------ text: slots, clearing, fitting
def _slot_map(slots) -> dict:
    """{index: {"text": ..., "prov": ...}} from {"k": text} or [{"k", "text"}]; bad entries are skipped (schema_errors
    names them)."""
    out: dict = {}
    if isinstance(slots, dict):
        it = [(k, v) for k, v in slots.items()]
    elif isinstance(slots, list):
        it = [(e.get("k"), e) for e in slots if isinstance(e, dict)]
    else:
        return out
    for k, v in it:
        try:
            k = int(str(k).strip().lstrip("k"))
        except (TypeError, ValueError):
            continue
        prov = None
        if isinstance(v, dict):
            prov = v.get("prov")
            v = v.get("text", v.get("rows"))
        if v is None:
            continue
        out[k] = {"text": v if isinstance(v, list) else str(v), "prov": prov}
    return out


def _clear_text(el):
    for txb in el.iter(pn("txBody"), a("txBody")):
        paras = txb.findall(a("p"))
        for p_ in paras:
            for ch in list(p_):
                if local(ch) in ("r", "fld", "br"):
                    p_.remove(ch)
        for p_ in paras[1:]:
            txb.remove(p_)


def _template_rpr(txb):
    for p_ in txb.findall(a("p")):
        r = p_.find(a("r"))
        if r is not None and r.find(a("rPr")) is not None:
            return copy.deepcopy(r.find(a("rPr")))
    for p_ in txb.findall(a("p")):
        e = p_.find(a("endParaRPr"))
        if e is not None:
            n = copy.deepcopy(e)
            n.tag = a("rPr")
            return n
    return etree.Element(a("rPr"), lang="en-US", sz="1800")


def _write_slot(el, text: str):
    """Replace the shape's text with `text` (lines = paragraphs), keeping the first run's look and the paragraph
    properties of the matching template paragraph."""
    txb = el.find(pn("txBody"))
    if txb is None:
        return
    paras = txb.findall(a("p"))
    base = _template_rpr(txb)
    ppr_t = []
    for p_ in paras:
        pp = p_.find(a("pPr"))
        r = p_.find(a("r"))
        rp = r.find(a("rPr")) if r is not None else None
        ppr_t.append((copy.deepcopy(pp) if pp is not None else None, copy.deepcopy(rp) if rp is not None else base))
    lines = [ln for ln in re.sub(r"\*\*", "", text).replace("\r", "").split("\n")] or [""]
    for p_ in paras:
        txb.remove(p_)
    for i, line in enumerate(lines):
        pp, rp = ppr_t[min(i, len(ppr_t) - 1)] if ppr_t else (None, base)
        np_ = etree.SubElement(txb, a("p"))
        if pp is not None:
            np_.append(copy.deepcopy(pp))
        r = etree.SubElement(np_, a("r"))
        r.append(copy.deepcopy(rp))
        etree.SubElement(r, a("t")).text = line
    bp = txb.find(a("bodyPr"))
    if bp is not None:
        bp.set("wrap", "square")


def _write_table(el, value):
    rows = value
    if isinstance(value, str):
        rows = [[c.strip() for c in ln.split("|")] for ln in value.split("\n") if ln.strip()]
    tbl = el.find(f".//{a('tbl')}")
    for ri, tr in enumerate(tbl.findall(a("tr"))):
        for ci, tc in enumerate(tr.findall(a("tc"))):
            txb = tc.find(a("txBody"))
            if txb is None:
                continue
            txt = ""
            if ri < len(rows) and isinstance(rows[ri], (list, tuple)) and ci < len(rows[ri]):
                txt = str(rows[ri][ci])
            elif ri < len(rows) and isinstance(rows[ri], str) and ci == 0:
                txt = rows[ri]
            if txt:
                _write_cell(txb, txt)
            else:
                _clear_text(tc)


def _write_cell(txb, text):
    base = _template_rpr(txb)
    p0 = txb.find(a("p"))
    pp = p0.find(a("pPr")) if p0 is not None else None
    for p_ in txb.findall(a("p")):
        txb.remove(p_)
    np_ = etree.SubElement(txb, a("p"))
    if pp is not None:
        np_.append(copy.deepcopy(pp))
    r = etree.SubElement(np_, a("r"))
    r.append(base)
    etree.SubElement(r, a("t")).text = re.sub(r"\*\*", "", text)


def _visible(el) -> bool:
    """True when the shape draws something of its own (a fill or a line)."""
    sp = _spPr(el)
    if sp is None:
        return False
    f = _fill_el(sp)
    if f is not None and local(f) == "solidFill":
        return True
    ln = sp.find(a("ln"))
    if ln is not None and ln.find(a("solidFill")) is not None and (ln.get("w") is None or int(ln.get("w")) > 0):
        return True
    return False


def _has_text(el) -> bool:
    return bool(_text_of(el))


def _prune_empty(el, had_text=frozenset(), slide_area=0) -> bool:
    """After clearing: drop text-only shapes with no text, small filled shapes whose template text was cleared
    (a badge or header box left empty reads as a defect; large cards stay), and groups with nothing left; True keeps
    el."""
    t = local(el)
    if t == "grpSp":
        for ch in list(el):
            if local(ch) in ("sp", "cxnSp", "grpSp", "graphicFrame") and not _prune_empty(ch, had_text, slide_area):
                el.remove(ch)
        if not any(local(ch) in ("sp", "cxnSp", "grpSp", "graphicFrame") for ch in el):
            return False
        _fit_group(el)
        return True
    if t == "sp":
        if _has_text(el):
            return True
        if id(el) in had_text and slide_area:
            r = _rect(el)
            if r and r[2] * r[3] < 0.04 * slide_area:
                return False
        return _visible(el)
    return True


def _fit_group(grp):
    """Shrink a group's frame to its remaining children."""
    xf = _xfrm(grp)
    co, ce = xf.find(a("chOff")), xf.find(a("chExt"))
    if co is None or ce is None or not int(ce.get("cx")) or not int(ce.get("cy")):
        return
    kids = [_rect(c) for c in grp if local(c) in ("sp", "cxnSp", "grpSp", "graphicFrame") and _rect(c)]
    if not kids:
        return
    x0, y0 = min(k[0] for k in kids), min(k[1] for k in kids)
    x1, y1 = max(k[0] + k[2] for k in kids), max(k[1] + k[3] for k in kids)
    o, e = xf.find(a("off")), xf.find(a("ext"))
    sx, sy = int(e.get("cx")) / int(ce.get("cx")), int(e.get("cy")) / int(ce.get("cy"))
    nx = int(o.get("x")) + (x0 - int(co.get("x"))) * sx
    ny = int(o.get("y")) + (y0 - int(co.get("y"))) * sy
    o.set("x", str(int(nx)))
    o.set("y", str(int(ny)))
    e.set("cx", str(max(1, int((x1 - x0) * sx))))
    e.set("cy", str(max(1, int((y1 - y0) * sy))))
    co.set("x", str(x0))
    co.set("y", str(y0))
    ce.set("cx", str(max(1, x1 - x0)))
    ce.set("cy", str(max(1, y1 - y0)))


GEOM_FACTOR = {"ellipse": 0.74, "diamond": 0.5, "triangle": 0.5, "rtTriangle": 0.5, "hexagon": 0.8, "octagon": 0.85,
               "donut": 0.5, "chevron": 0.7, "homePlate": 0.85, "pentagon": 0.8, "parallelogram": 0.8,
               "trapezoid": 0.75, "flowChartTerminator": 0.9, "plus": 0.5, "star5": 0.5, "cloud": 0.6}


def _shape_blocks(el, mult=(1.0, 1.0)):
    """(blocks, (iw, ih)) of a text shape: paragraph texts with their sizes, and the room its text has (inches).
    `mult` maps a group child's own coordinates to the slide."""
    txb = el.find(pn("txBody"))
    r = _rect(el)
    if txb is None or r is None:
        return None, None
    r = (r[0], r[1], r[2] * mult[0], r[3] * mult[1])
    bp = txb.find(a("bodyPr"))
    if bp is not None and min(r[2], r[3]) < 0.7 * EMU:          # a small badge or label: tight insets
        for k_ in ("lIns", "rIns", "tIns", "bIns"):
            bp.set(k_, str(min(int(bp.get(k_) or 91440), 18288)))
    ins = [int(bp.get(k) if bp is not None and bp.get(k) is not None else d)
           for k, d in (("lIns", 91440), ("tIns", 45720), ("rIns", 91440), ("bIns", 45720))]
    w, h = r[2] / EMU, r[3] / EMU
    rot = int(_xfrm(el).get("rot") or 0)
    vert = bp is not None and (bp.get("vert") or "horz") not in ("horz",)
    if vert or (rot // 60000) % 180 in range(80, 100):
        w, h = h, w
    g = el.find(f"{pn('spPr')}/{a('prstGeom')}")
    f = GEOM_FACTOR.get(g.get("prst"), 1.0) if g is not None else 1.0
    iw, ih = w * f - (ins[0] + ins[2]) / EMU, h * f - (ins[1] + ins[3]) / EMU
    blocks = []
    for p_ in txb.findall(a("p")):
        runs = p_.findall(a("r"))
        text = "".join((r_.findtext(a("t")) or "") for r_ in runs)
        rp = runs[0].find(a("rPr")) if runs else None
        ppr = p_.find(a("pPr"))
        bullet = ppr is not None and (ppr.find(a("buChar")) is not None or ppr.find(a("buAutoNum")) is not None)
        sb = ppr.find(f"{a('spcBef')}/{a('spcPts')}") if ppr is not None else None
        blocks.append({"text": text, "size": (int(rp.get("sz")) / 100 if rp is not None and rp.get("sz") else 18),
                       "bold": bool(rp is not None and rp.get("b") in ("1", "true")), "bullet": bullet,
                       "space_before": int(sb.get("val")) / 100 if sb is not None else 0, "numbers": False})
    return blocks, (iw, ih)


def _apply_blocks(el, blocks, keep: list):
    txb = el.find(pn("txBody"))
    paras = txb.findall(a("p"))
    for i, p_ in enumerate(paras):
        if i not in keep:
            txb.remove(p_)
    live = [p_ for i, p_ in enumerate(paras) if i in keep]
    for p_, b in zip(live, blocks):
        runs = p_.findall(a("r"))
        old = int(runs[0].find(a("rPr")).get("sz")) / 100 if runs and runs[0].find(a("rPr")) is not None else b["size"]
        if runs:
            runs[0].find(a("t")).text = b["text"]
            for r_ in runs[1:]:
                p_.remove(r_)
        for r_ in runs[:1]:
            r_.find(a("rPr")).set("sz", str(int(round(b["size"] * 100))))
        end = p_.find(a("endParaRPr"))
        if end is not None and old != b["size"]:
            end.set("sz", str(int(round(b["size"] * 100))))


def _fit_shape(el, k, s, ctx, n, mult=(1.0, 1.0)):
    """Cut text that does not fit its shape at the floor size (10 pt); what was left out goes to the notes."""
    blocks, room = _shape_blocks(el, mult)
    if not blocks or not any(b["text"].strip() for b in blocks):
        return
    iw, ih = room
    if iw <= 0.05 or ih <= 0.05:
        return
    if R._need(blocks, iw) <= ih + 0.04:
        return
    for i, b in enumerate(blocks):
        b["_i"] = i
    bl, dropped = R._trim_to_fit(blocks, iw, ih, MIN_PT)
    if dropped:
        R._cut_note(s, dropped)
        ctx.miss(n, f"slot {k} of the library layout", max(3, sum(len(d.split()) for d in dropped)))
    keep = [b["_i"] for b in bl]
    cut_words = 0
    for b in bl:                                           # a single paragraph can still be too long: cut its tail
        b.pop("_i", None)
    while R._need(bl, iw) > ih + 0.04:
        j = max(range(len(bl)), key=lambda q: len(bl[q]["text"].split()))
        words = bl[j]["text"].split()
        if len(words) <= 1:
            break
        cut = max(1, len(words) // 10)
        cut_words += cut
        bl[j]["text"] = " ".join(words[:-cut])
    if cut_words:
        s["notes"] = (s.get("notes", "") + f"\n[QA] The text of slot {k} of the library layout was cut by about "
                      f"{cut_words} words to fit its shape.").strip()
        ctx.miss(n, f"slot {k} of the library layout", cut_words)
    _apply_blocks(el, bl, keep)


def _fit_table(el, k, s, ctx, n):
    """Table cells: rows grow to hold their text (the frame follows); cell text is never cut."""
    tbl = el.find(f".//{a('tbl')}")
    cols = [int(g.get("w")) for g in tbl.find(a("tblGrid"))]
    total = 0
    for tr in tbl.findall(a("tr")):
        need = int(tr.get("h"))
        for ci, tc in enumerate(tr.findall(a("tc"))):
            txb = tc.find(a("txBody"))
            if txb is None or ci >= len(cols):
                continue
            pr = tc.find(a("tcPr"))
            mar = lambda key, d: int(pr.get(key) if pr is not None and pr.get(key) else d)  # noqa: E731
            w = (cols[ci] - mar("marL", 91440) - mar("marR", 91440)) / EMU
            blocks = []
            for p_ in txb.findall(a("p")):
                runs = p_.findall(a("r"))
                rp = runs[0].find(a("rPr")) if runs else None
                blocks.append({"text": "".join(r_.findtext(a("t")) or "" for r_ in runs),
                               "size": int(rp.get("sz")) / 100 if rp is not None and rp.get("sz") else 12,
                               "bold": bool(rp is not None and rp.get("b") in ("1", "true")), "numbers": False})
            if w > 0.1 and any(b["text"] for b in blocks):
                need = max(need, int((R._need(blocks, w) + (mar("marT", 45720) + mar("marB", 45720)) / EMU) * EMU))
        tr.set("h", str(need))
        total += need
    r = _rect(el)
    _set_rect(el, r[0], r[1], sum(cols), total)


# ------------------------------------------------------------------ analysis of an item (cached)
_ANALYSIS: dict = {}


def _dims(prs):
    return int(prs.slide_width), int(prs.slide_height)


def _prepare(item_id, ctx_texts=None):
    """Load the item and restyle its shapes at scale 1: (prs, slide, [(k, element)], mapper pieces) or raise Unusable."""
    prs = load(item_id)
    if prs is None:
        raise Unusable("the item is not in the library")
    slide = prs.slides[0]
    pairs = _bake_placeholders(slide, prs)
    src, cmap = source_theme(prs)
    kept = restyle(pairs, 1.0, None, finish=False, src_theme=src, clrmap=cmap)
    if not kept:
        raise Unusable("nothing in the item can be drawn as shapes")
    return prs, slide, kept


def analyse(item_id) -> dict:
    """{"ok": bool, "reason": str, "slots": [k, ...]}: which top-level shapes of the item take text. Cached."""
    p = _path(item_id)
    if p is None:
        return {"ok": False, "reason": "the slide library is off or the item is not in it", "slots": []}
    key = (str(p), p.stat().st_mtime_ns)
    if key in _ANALYSIS:
        return _ANALYSIS[key]
    try:
        _, _, kept = _prepare(item_id)
        slots = [k for k, el in kept if (local(el) == "sp" and _has_text(el)) or _is_table(el)]
        res = {"ok": True, "reason": "", "slots": slots}
    except Unusable as e:
        res = {"ok": False, "reason": str(e), "slots": []}
    except Exception as e:  # noqa: BLE001 - a malformed item is an unusable item, never a crash
        res = {"ok": False, "reason": f"the item could not be read ({type(e).__name__})", "slots": []}
    if len(_ANALYSIS) > 64:
        _ANALYSIS.clear()
    _ANALYSIS[key] = res
    return res


# ------------------------------------------------------------------ place
def _inside(r, b) -> float:
    """The share of rectangle r that lies inside rectangle b."""
    ix = min(r[0] + r[2], b[0] + b[2]) - max(r[0], b[0])
    iy = min(r[1] + r[3], b[1] + b[3]) - max(r[1], b[1])
    return (ix * iy) / (r[2] * r[3]) if ix > 0 and iy > 0 and r[2] * r[3] else 0.0


def _merge_text(kept, W, H):
    """A text box laid over an empty filled shape moves into the shape (P17: one object to edit, not two). A card that
    holds several elements stays as drawn."""
    out = list(kept)
    for k, tb in kept:
        rt = _rect(tb)
        if local(tb) != "sp" or not _has_text(tb) or _own_fill_role(tb) is not None or rt is None:
            continue
        for k2, bx in kept:
            rb = _rect(bx)
            if bx is tb or local(bx) != "sp" or _has_text(bx) or _own_fill_role(bx) is None or rb is None \
                    or rb[2] * rb[3] > 0.6 * W * H or _inside(rt, rb) < 0.8:
                continue
            if any(o is not bx and o is not tb and local(o) != "cxnSp" and _rect(o) and _inside(_rect(o), rb) >= 0.8
                   for _, o in kept):
                continue
            new = copy.deepcopy(tb.find(pn("txBody")))
            bp = new.find(a("bodyPr"))
            tin = [int(bp.get(q) if bp.get(q) is not None else d) for q, d in
                   (("lIns", 91440), ("tIns", 45720), ("rIns", 91440), ("bIns", 45720))]
            bp.set("lIns", str(max(0, rt[0] - rb[0]) + tin[0]))
            bp.set("tIns", str(max(0, rt[1] - rb[1]) + tin[1]))
            bp.set("rIns", str(max(0, rb[0] + rb[2] - rt[0] - rt[2]) + tin[2]))
            bp.set("bIns", str(max(0, rb[1] + rb[3] - rt[1] - rt[3]) + tin[3]))
            old = bx.find(pn("txBody"))
            if old is not None:
                bx.replace(old, new)
            else:
                bx.find(pn("spPr")).addnext(new)
            out.remove((k, tb))
            break
    return out


def _full_bleed(r, W, H):
    return r[2] * r[3] >= 0.85 * W * H


def _chrome(el, W, H, theme=None) -> bool:
    """Text-free full-slide backgrounds in light tones, and thin bands along the slide's top and bottom edge."""
    if _has_text(el) or local(el) not in ("sp", "grpSp"):
        return False
    r = _rect(el)
    if r is None:
        return False
    if _full_bleed(r, W, H):
        return _own_fill_role(el) in (None, "bg1", "bg2")
    thin = r[2] >= 0.85 * W and r[3] <= 0.2 * H
    return thin and (r[1] + r[3] / 2 < 0.15 * H or r[1] + r[3] / 2 > 0.85 * H)


def _place(sl, item_id, box, slots, s, ctx, n) -> tuple[bool, str]:
    try:
        prs, slide, kept = _prepare(item_id)
    except Unusable as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"the item could not be read ({type(e).__name__})"
    W, H = _dims(prs)
    smap = _slot_map(slots)
    theme = target_theme(sl)
    # 1. texts: slot text in, every other text cleared (group children included)
    had_text = {id(e) for _, el in kept for e in el.iter() if local(e) == "sp" and _has_text(e)}
    for k, el in kept:
        if _is_table(el):
            if k in smap:
                _write_table(el, smap[k]["text"])
                if smap[k].get("prov") or s.get("prov"):
                    pass
            else:
                _clear_text(el)
        elif local(el) == "sp" and k in smap and isinstance(smap[k]["text"], str) and _has_text(el):
            _write_slot(el, smap[k]["text"])
            ctx.track(n, smap[k]["text"], smap[k].get("prov") or s.get("prov"))
        else:
            _clear_text(el)
    kept = [(k, el) for k, el in kept if _prune_empty(el, had_text, W * H)]
    kept = [(k, el) for k, el in kept if not _chrome(el, W, H)]
    kept = _merge_text(kept, W, H)
    if not kept:
        return False, "no shape of the item is left after the empty ones are removed"
    rects = [_rect(el) for _, el in kept]
    rects = [r for r in rects if r]
    if not rects:
        return False, "the item's shapes have no geometry"
    x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
    x1, y1 = max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects)
    bw, bh = max(1, x1 - x0) / EMU, max(1, y1 - y0) / EMU
    bx, by, bwid, bhgt = box
    scale = min(bwid / bw, bhgt / bh, MAX_SCALE)
    if scale < 0.3:
        return False, "the item is too large and detailed for the space"
    dx = bx * EMU + (bwid * EMU - (x1 - x0) * scale) / 2 - x0 * scale
    dy = by * EMU + (bhgt * EMU - (y1 - y0) * scale) / 2 - y0 * scale
    # 2. scale, then keep text readable and fitted
    placed = []
    for k, el in kept:
        r = _rect(el)
        if r is None:
            continue
        _scale_el(el, scale)
        _set_rect(el, r[0] * scale + dx, r[1] * scale + dy, r[2] * scale, r[3] * scale)
        placed.append((k, el))
    _contrast([el for _, el in placed], theme)
    for k, el in placed:
        mult = (1.0, 1.0)
        if local(el) == "grpSp":
            xf = _xfrm(el)
            ce = xf.find(a("chExt"))
            mult = (int(xf.find(a("ext")).get("cx")) / max(1, int(ce.get("cx"))),
                    int(xf.find(a("ext")).get("cy")) / max(1, int(ce.get("cy"))))
        for node in el.iter(pn("sp")) if local(el) == "grpSp" else [el]:
            if local(node) == "sp" and _has_text(node):
                _fit_shape(node, k, s, ctx, n, mult)
            elif _is_table(node):
                _fit_table(node, k, s, ctx, n)
    try:
        for k, el in placed:
            _scan(el)
    except Unusable as e:
        return False, str(e)
    # 3. names, ids, glue
    tree = sl.shapes._spTree
    nid = sl.shapes._next_shape_id
    remap, cnvs = {}, []
    for k, el in placed:
        _finalize_names(el, k, cnvs)
    for c in cnvs:
        remap[c.get("_old")] = str(nid)
        c.set("id", str(nid))
        del c.attrib["_old"]
        nid += 1
    for k, el in placed:
        for tag in ("stCxn", "endCxn"):
            for g in list(el.iter(a(tag))):
                if g.get("id") in remap:
                    g.set("id", remap[g.get("id")])
                else:
                    g.getparent().remove(g)
        tree.insert_element_before(el, "p:extLst")
    return True, ""


def place(slide, item_id, box, slots, s, ctx, n) -> bool:
    """Draw the library item inside box (x, y, w, h inches) of `slide` with the slot texts. True on success; False
    when the item cannot be used (the reason is in s["_library_why"])."""
    ok, why = _place(slide, item_id, box, slots, s, ctx, n)
    if not ok:
        s["_library_why"] = why
    return ok


# ------------------------------------------------------------------ the archetype
def _texts(s) -> list[str]:
    out = []
    for v in (_slot_map(s.get("slots")) or {}).values():
        t = v["text"]
        if isinstance(t, list):
            out += [" | ".join(map(str, r)) if isinstance(r, (list, tuple)) else str(r) for r in t]
        elif t.strip():
            out += [ln.strip() for ln in re.sub(r"\*\*", "", t).split("\n") if ln.strip()]
    return out


def _plain_fallback(s) -> dict:
    texts = _texts(s)
    tk = s.get("takeaway")
    return {"archetype": "argument_only", "title": s.get("title", ""),
            "blocks": [{"text": (tk.get("text") if isinstance(tk, dict) else (tk or "")),
                        "bullets": [{"text": t} for t in texts[:8]] or [{"text": "[GAP]"}]}]}


def _fallback_dict(s, why: str) -> dict:
    fb = s.get("fallback")
    if not (isinstance(fb, dict) and fb.get("archetype") and fb.get("archetype") != "library"
            and fb.get("archetype") not in R.DIVIDERS):
        fb = _plain_fallback(s)
    fb = dict(fb)
    for key in ("title", "subtitle", "source", "footnotes", "node", "id", "section", "density", "title_prov"):
        if s.get(key) is not None and fb.get(key) is None:
            fb[key] = s[key]
    fb = R.normalize(fb)
    fb["notes"] = (s.get("notes", "") + f"\n[QA] The library layout was not used: {why}.").strip()
    return fb


def schema_errors(s: dict) -> list[str]:
    """Precise messages for a library slide. The knowledge base being off is not an error (a note at draw time)."""
    errs = []
    item = s.get("item")
    if not isinstance(item, str) or not item.strip():
        errs.append("'item' is missing: name the library layout to draw (its id, a string)")
    slots = s.get("slots")
    keys: list = []
    if slots is None or slots == {} or slots == []:
        errs.append("'slots' is missing or empty: give the text for the layout's slots as {slot index: text}")
    elif isinstance(slots, dict):
        keys = list(slots.keys())
    elif isinstance(slots, list):
        for i, e in enumerate(slots, 1):
            if not isinstance(e, dict) or "k" not in e or "text" not in e:
                errs.append(f"slots entry {i}: needs 'k' (the slot index) and 'text'")
            else:
                keys.append(e["k"])
    else:
        errs.append("'slots' must be an object {slot index: text} or a list of {k, text}, not "
                    f"{type(slots).__name__}")
    ks = []
    for k in keys:
        try:
            ks.append(int(str(k).strip().lstrip("k")))
        except ValueError:
            errs.append(f"slot key '{k}' is not a slot index (a whole number)")
    fb = s.get("fallback")
    if fb is not None and not isinstance(fb, dict):
        errs.append("'fallback' must be a slide object {archetype, title, ...}")
    elif isinstance(fb, dict) and fb.get("archetype") in (None, "", "library"):
        errs.append("'fallback' needs an 'archetype' other than library (the slide drawn when the layout cannot be)")
    if ks and isinstance(item, str) and item.strip():
        info = analyse(item.strip())
        if info["ok"]:
            bad = sorted(k for k in set(ks) if k not in info["slots"])
            if bad:
                errs.append(f"slot {', '.join(map(str, bad))} {'is' if len(bad) == 1 else 'are'} not a slot of the "
                            f"layout; its slots are {info['slots']}")
    return errs


def fallback(s: dict) -> dict:
    """A library slide that fails its schema keeps its fallback slide (or its slot texts as a plain argument)."""
    s = dict(s)
    return _fallback_dict(s, "the slide did not meet the library layout's requirements")


def _clear_body(sl):
    for shp in list(sl.shapes):
        if not shp.is_placeholder:
            shp._element.getparent().remove(shp._element)


def _draw_fallback(ctx, s, sl, n, why):
    fb = _fallback_dict(s, why)
    errs = R.schema_errors(fb)
    if errs:
        fb = R.fallback(fb)
    arch = fb.get("archetype")
    fn = R.ARCHETYPES.get(arch)
    if fn is None or arch == "library":
        fb = _plain_fallback(s) | {"notes": fb.get("notes", "")}
        arch, fn = "argument_only", R.ARCHETYPES["argument_only"]
    sl._element.find(qn("p:cSld")).set("name", re.sub(r"arch:\w+", f"arch:{arch}",
                                                      sl._element.find(qn("p:cSld")).get("name") or ""))
    fn(ctx, fb, sl, n)
    for key in ("notes", "source", "footnotes"):
        if fb.get(key) is not None:
            s[key] = fb[key]


def a_library(ctx, s, sl, n):
    """A library layout drawn as native shapes in the body, with the takeaway and bullets in the right third."""
    item = (s.get("item") or "").strip() if isinstance(s.get("item"), str) else ""
    if not kb.available():
        return _draw_fallback(ctx, s, sl, n, "the slide library is off")
    info = analyse(item) if item else {"ok": False, "reason": "no item was named", "slots": []}
    if not info["ok"]:
        return _draw_fallback(ctx, s, sl, n, info["reason"])
    R._header(ctx, s, sl, n)
    tk = s.get("takeaway")
    tk = tk if isinstance(tk, dict) else ({"text": str(tk)} if tk else {})
    bullets = [b if isinstance(b, dict) else {"text": str(b)} for b in s.get("bullets") or []]
    notes = s.get("footnotes") or []
    notes_h = R._notes_h([R.footnote_text(t) for t in notes]) if notes else 0
    bottom = R.FOOT_RULE_Y - 0.08 - notes_h if notes else R.BODY_B
    if notes:
        ctx.notes_top = R.FOOT_RULE_Y - 0.03 - notes_h
    side = bool(tk.get("text") or bullets)
    boxes = P.regions("main_side" if side else "full", R.BODY_Y + 0.05, bottom)
    before = len(sl.shapes._spTree)
    ok = place(sl, item, boxes[0], s.get("slots"), s, ctx, n)
    if not ok:
        why = s.pop("_library_why", "the layout could not be drawn")
        _clear_body(sl)
        return _draw_fallback(ctx, s, sl, n, why)
    s.pop("_library_why", None)
    if side:
        part = {"type": "text", "lead": tk.get("text", ""), "prov": tk.get("prov"), "bullets": bullets}
        P.p_text(ctx, sl, n, part, boxes[1], "editorial", 2, s, None)
    return None
