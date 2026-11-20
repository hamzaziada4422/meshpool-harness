"""SVG drawings as native PowerPoint shapes: an icon (or any small vector drawing) becomes one shape with custom
geometry (a:custGeom), stroked or filled in a theme colour. No picture part, so the deck has zero images, and the icon
is editable and recolours with the theme (Design > Variants) like every other shape.

Supported SVG: path (M L H V C S Q T A Z, absolute and relative), circle, ellipse, rect (with rx/ry), line, polyline,
polygon, g, and transform (matrix, translate, scale, rotate). Arcs and circles become cubic Bezier curves, so any
reader (PowerPoint, LibreOffice, Keynote, Google Slides) draws the same shape. Fill rules: subpaths in one filled path
are drawn with PowerPoint's alternate rule, which matches SVG evenodd and, for the icon sets Forge bundles, nonzero.
"""
from __future__ import annotations

import math
import re

from lxml import etree
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

SVG_NS = "{http://www.w3.org/2000/svg}"
UNITS = 10000                       # path coordinate space per viewBox side (integers in DrawingML)
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_CMD = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


# ------------------------------------------------------------------ geometry
def _mul(a, b):
    """3x2 affine matrices as (a, b, c, d, e, f): x' = a x + c y + e, y' = b x + d y + f."""
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3],
            a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


def _apply(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def parse_transform(t: str | None):
    m = (1, 0, 0, 1, 0, 0)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", t or ""):
        v = [float(x) for x in _NUM.findall(args)]
        if name == "matrix" and len(v) == 6:
            n = tuple(v)
        elif name == "translate":
            n = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale":
            n = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate":
            a = math.radians(v[0])
            r = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) == 3:
                r = _mul(_mul((1, 0, 0, 1, v[1], v[2]), r), (1, 0, 0, 1, -v[1], -v[2]))
            n = r
        else:
            continue
        m = _mul(m, n)
    return m


def _arc(p0, rx, ry, phi, large, sweep, p1):
    """SVG elliptical arc -> cubic Bezier segments [(c1, c2, end), ...] (the W3C endpoint-to-centre conversion)."""
    if rx == 0 or ry == 0 or p0 == p1:
        return [(p0, p1, p1)]
    rx, ry = abs(rx), abs(ry)
    cp, sp = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cx1, cy1 = co * rx * y1 / ry, -co * ry * x1 / rx
    cx = cp * cx1 - sp * cy1 + (p0[0] + p1[0]) / 2
    cy = sp * cx1 + cp * cy1 + (p0[1] + p1[1]) / 2

    def ang(u, v):
        a = math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])
        return a
    t1 = ang((1, 0), ((x1 - cx1) / rx, (y1 - cy1) / ry))
    dt = ang(((x1 - cx1) / rx, (y1 - cy1) / ry), ((-x1 - cx1) / rx, (-y1 - cy1) / ry))
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, math.ceil(abs(dt) / (math.pi / 2) - 1e-9))
    d = dt / n
    k = 4 / 3 * math.tan(d / 4)

    def pt(t):
        x, y = rx * math.cos(t), ry * math.sin(t)
        return (cp * x - sp * y + cx, sp * x + cp * y + cy)

    def dpt(t):
        x, y = -rx * math.sin(t), ry * math.cos(t)
        return (cp * x - sp * y, sp * x + cp * y)
    out = []
    for i in range(n):
        a, b = t1 + i * d, t1 + (i + 1) * d
        pa, pb, da, db = pt(a), pt(b), dpt(a), dpt(b)
        out.append(((pa[0] + k * da[0], pa[1] + k * da[1]), (pb[0] - k * db[0], pb[1] - k * db[1]),
                    p1 if i == n - 1 else pb))
    return out


def parse_path(d: str) -> list[list]:
    """SVG path data -> subpaths, each [("M", p), ("L", p), ("C", c1, c2, p), ("Z",)] in absolute coordinates."""
    toks = _CMD.findall(d or "")
    subs: list[list] = []
    cur = None
    pos = start = (0.0, 0.0)
    last_c = last_q = None
    cmd = None
    i = 0

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v
    while i < len(toks):
        if re.fullmatch(r"[A-Za-z]", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                if cur is not None:
                    cur.append(("Z",))
                pos = start
                last_c = last_q = None
                continue
        if cmd is None:
            break
        rel = cmd.islower()
        C = cmd.upper()
        ox, oy = pos if rel else (0.0, 0.0)
        if C == "M":
            pos = (ox + num(), oy + num())
            start = pos
            cur = [("M", pos)]
            subs.append(cur)
            cmd = "l" if rel else "L"              # further pairs are line-tos
            last_c = last_q = None
        elif C in "LHV":
            if C == "L":
                p = (ox + num(), oy + num())
            elif C == "H":
                p = ((pos[0] if rel else 0) + num(), pos[1])
            else:
                p = (pos[0], (pos[1] if rel else 0) + num())
            if cur is None:
                cur = [("M", pos)]
                subs.append(cur)
            cur.append(("L", p))
            pos = p
            last_c = last_q = None
        elif C in "CS":
            if C == "C":
                c1 = (ox + num(), oy + num())
            else:
                c1 = (2 * pos[0] - last_c[0], 2 * pos[1] - last_c[1]) if last_c else pos
            c2 = (ox + num(), oy + num())
            p = (ox + num(), oy + num())
            cur.append(("C", c1, c2, p))
            last_c, last_q, pos = c2, None, p
        elif C in "QT":
            if C == "Q":
                q = (ox + num(), oy + num())
            else:
                q = (2 * pos[0] - last_q[0], 2 * pos[1] - last_q[1]) if last_q else pos
            p = (ox + num(), oy + num())
            c1 = (pos[0] + 2 / 3 * (q[0] - pos[0]), pos[1] + 2 / 3 * (q[1] - pos[1]))
            c2 = (p[0] + 2 / 3 * (q[0] - p[0]), p[1] + 2 / 3 * (q[1] - p[1]))
            cur.append(("C", c1, c2, p))
            last_q, last_c, pos = q, None, p
        elif C == "A":
            rx, ry, phi = num(), num(), num()
            large, sweep = int(num()), int(num())
            p = (ox + num(), oy + num())
            for c1, c2, e in _arc(pos, rx, ry, phi, large, sweep, p):
                cur.append(("C", c1, c2, e))
            pos = p
            last_c = last_q = None
        else:
            break
    return [s for s in subs if len(s) > 1]


def _ellipse(cx, cy, rx, ry) -> list:
    k = 0.5522847498
    return [("M", (cx + rx, cy)),
            ("C", (cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry)),
            ("C", (cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy)),
            ("C", (cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry)),
            ("C", (cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy)), ("Z",)]


def _rect(x, y, w, h, rx, ry) -> list:
    if not rx and not ry:
        return [("M", (x, y)), ("L", (x + w, y)), ("L", (x + w, y + h)), ("L", (x, y + h)), ("Z",)]
    rx, ry = min(rx or ry, w / 2), min(ry or rx, h / 2)
    d = f"M{x + rx},{y} H{x + w - rx} A{rx},{ry} 0 0 1 {x + w},{y + ry} V{y + h - ry} A{rx},{ry} 0 0 1 {x + w - rx},{y + h}" \
        f" H{x + rx} A{rx},{ry} 0 0 1 {x},{y + h - ry} V{y + ry} A{rx},{ry} 0 0 1 {x + rx},{y} Z"
    return parse_path(d)[0]


def _f(el, k, default=0.0) -> float:
    v = el.get(k)
    try:
        return float(_NUM.match(v).group()) if v is not None else default
    except (AttributeError, ValueError):
        return default


def parse_svg(svg: str) -> dict:
    """{"box": (x, y, w, h), "paths": [{"subs", "fill", "stroke", "width", "cap", "join"}]} with the transforms
    applied. "fill"/"stroke" are booleans: what the drawing paints (currentColor or any colour counts, none does not)."""
    root = etree.fromstring(re.sub(r"<\?xml[^>]*>", "", svg).strip().encode())
    vb = [float(x) for x in _NUM.findall(root.get("viewBox") or "")] or [0, 0, _f(root, "width", 24),
                                                                         _f(root, "height", 24)]
    out = []

    def style(el, inh):
        st = dict(inh)
        for k in ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "fill-rule", "opacity"):
            if el.get(k) is not None:
                st[k] = el.get(k)
        for decl in (el.get("style") or "").split(";"):
            if ":" in decl:
                k, v = decl.split(":", 1)
                st[k.strip()] = v.strip()
        return st

    def walk(el, m, inh):
        tag = el.tag.replace(SVG_NS, "") if isinstance(el.tag, str) else ""
        if tag in ("defs", "clipPath", "mask", "title", "desc", "style", "linearGradient", "radialGradient"):
            return
        st = style(el, inh)
        m = _mul(m, parse_transform(el.get("transform")))
        subs = None
        if tag == "path":
            subs = parse_path(el.get("d", ""))
        elif tag == "circle":
            r = _f(el, "r")
            subs = [_ellipse(_f(el, "cx"), _f(el, "cy"), r, r)] if r > 0 else None
        elif tag == "ellipse":
            subs = [_ellipse(_f(el, "cx"), _f(el, "cy"), _f(el, "rx"), _f(el, "ry"))]
        elif tag == "rect":
            w, h = _f(el, "width"), _f(el, "height")
            subs = [_rect(_f(el, "x"), _f(el, "y"), w, h, _f(el, "rx"), _f(el, "ry"))] if w > 0 and h > 0 else None
        elif tag == "line":
            subs = [[("M", (_f(el, "x1"), _f(el, "y1"))), ("L", (_f(el, "x2"), _f(el, "y2")))]]
        elif tag in ("polyline", "polygon"):
            pts = [float(x) for x in _NUM.findall(el.get("points", ""))]
            pp = list(zip(pts[::2], pts[1::2]))
            if len(pp) >= 2:
                subs = [[("M", pp[0])] + [("L", p) for p in pp[1:]] + ([("Z",)] if tag == "polygon" else [])]
        if subs:
            def tr(seg):
                return (seg[0],) + tuple(_apply(m, p) for p in seg[1:])
            sc = math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1.0
            fill, stroke = st.get("fill", "black"), st.get("stroke", "none")
            if float(st.get("opacity", 1) or 1) > 0:
                out.append({"subs": [[tr(s) for s in sub] for sub in subs],
                            "fill": fill not in ("none", "transparent"),
                            "stroke": stroke not in ("none", "transparent"),
                            "width": float(_NUM.match(str(st.get("stroke-width", "1"))).group()) * sc,
                            "cap": st.get("stroke-linecap", "butt"), "join": st.get("stroke-linejoin", "miter")})
        for ch in el:
            walk(ch, m, st)

    walk(root, (1, 0, 0, 1, 0, 0), {})
    return {"box": tuple(vb[:4]), "paths": out}


# ------------------------------------------------------------------ DrawingML
def _pt(p, box, W, H):
    x, y, w, h = box
    return (int(round((p[0] - x) / w * W)), int(round((p[1] - y) / h * H)))


def add_svg(sl, svg: str, x: float, y: float, w: float, h: float, name: str, theme_color, alt: str = "",
            stroke_scale: float = 1.0, min_stroke_pt: float = 0.5):
    """Draw an SVG as one native shape at (x, y, w, h) inches in a theme colour. Stroked paths keep their stroke width
    (scaled to the drawn size, times stroke_scale); filled paths are filled. Returns the shape, or None when the SVG
    has nothing drawable."""
    d = parse_svg(svg)
    paths = [p for p in d["paths"] if p["fill"] or p["stroke"]]
    if not paths:
        return None
    box = d["box"]
    W = H = UNITS
    if box[2] != box[3]:                               # keep the aspect: the longer side gets UNITS
        W, H = (UNITS, int(UNITS * box[3] / box[2])) if box[2] > box[3] else (int(UNITS * box[2] / box[3]), UNITS)
    shp = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.name = name
    sp = shp._element
    if alt:
        sp.find(qn("p:nvSpPr")).find(qn("p:cNvPr")).set("descr", alt)
    for el in list(sp):                                # no style: theme colours set below, nothing inherited
        if el.tag == qn("p:style"):
            sp.remove(el)
    spPr = sp.find(qn("p:spPr"))
    prst = spPr.find(qn("a:prstGeom"))
    geom = etree.SubElement(spPr, qn("a:custGeom"))
    prst.addprevious(geom)
    spPr.remove(prst)
    for t in ("avLst", "gdLst", "ahLst", "cxnLst"):
        etree.SubElement(geom, qn(f"a:{t}"))
    rect = etree.SubElement(geom, qn("a:rect"), l="l", t="t", r="r", b="b")
    del rect
    lst = etree.SubElement(geom, qn("a:pathLst"))
    stroked = [p for p in paths if p["stroke"]]
    filled = any(p["fill"] for p in paths)
    for p in paths:
        pa = etree.SubElement(lst, qn("a:path"), w=str(W), h=str(H))
        if not p["fill"]:
            pa.set("fill", "none")
        if not p["stroke"]:
            pa.set("stroke", "0")
        for sub in p["subs"]:
            for seg in sub:
                if seg[0] == "M":
                    m = etree.SubElement(pa, qn("a:moveTo"))
                    etree.SubElement(m, qn("a:pt"), dict(zip(("x", "y"), map(str, _pt(seg[1], box, W, H)))))
                elif seg[0] == "L":
                    m = etree.SubElement(pa, qn("a:lnTo"))
                    etree.SubElement(m, qn("a:pt"), dict(zip(("x", "y"), map(str, _pt(seg[1], box, W, H)))))
                elif seg[0] == "C":
                    m = etree.SubElement(pa, qn("a:cubicBezTo"))
                    for q in seg[1:]:
                        etree.SubElement(m, qn("a:pt"), dict(zip(("x", "y"), map(str, _pt(q, box, W, H)))))
                else:
                    etree.SubElement(pa, qn("a:close"))
    if filled:
        shp.fill.solid()
        shp.fill.fore_color.theme_color = theme_color
    else:
        shp.fill.background()
    if stroked:
        sw = max(p["width"] for p in stroked)
        size_in = max(w, h) * max(W, H) / UNITS
        pt = max(min_stroke_pt, sw / max(box[2], box[3]) * size_in * 72 * stroke_scale)
        shp.line.color.theme_color = theme_color
        shp.line.width = Pt(pt)
        ln = shp.line._get_or_add_ln()
        cap = {"round": "rnd", "square": "sq"}.get(stroked[0]["cap"], "flat")
        ln.set("cap", cap)
        join = stroked[0]["join"]
        for t in ("a:round", "a:bevel", "a:miter"):
            for old in ln.findall(qn(t)):
                ln.remove(old)
        etree.SubElement(ln, qn("a:round" if join == "round" else ("a:bevel" if join == "bevel" else "a:miter")))
    else:
        shp.line.fill.background()
    tf = shp.text_frame
    tf.text = ""
    return shp


def emu_in(v) -> float:
    return Emu(v).inches
