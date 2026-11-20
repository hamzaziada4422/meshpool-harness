"""Built-in layouts: distilled slide structures that ship with the engine and are drawn by Forge in the deck's theme.

Each layout in data/layouts.json is abstract geometry and roles only: preset shape names, boxes as integers in 1/200 of
the layout's content box, theme colour roles (bg1, bg2, tx1, tx2, accent1..6), alignment, a relative type size and a
description of each text slot (what it holds, how many words). It holds no text, no colour values, no fonts, no
pictures and no custom geometry, and nothing that names where it was distilled from. data/layouts.json is generated
locally from the private slide library (the generator is not part of the repo); this module only reads it.

    {"archetype": "layout", "layout": "<id>", "slots": {"1": "text", "2": "text"}, "title": "...",
     "takeaway": {"text": "..."}, "bullets": ["..."], "source": {"text": "..."}}

`slots` maps a slot number to its text (lines separated by newlines; a line starting "- " is a bullet). A chart slot
takes a chart ({"kind", "categories", "series"}), a table slot takes rows (the first row is the header). Find a layout
with `search(text)`; `get(id)` returns its spec. The layout is drawn into the body (two thirds when the slide has a
takeaway or bullets, which go in the right third, else the full body) at one uniform scale. Every shape is a native
preset shape in theme colours (MSO_THEME_COLOR), named ov_Lay..., with its text written into it; an empty text-only shape
is not drawn. Text is fitted by measurement (10 pt floor); what does not fit is cut, noted with [QA] and reported.
"""
from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

DATA = Path(__file__).resolve().parent / "data" / "layouts.json"
OFFERED = DATA.with_name("layouts_offered.json")     # the layouts that render clean (testkit/forge_layout_gate.py)
UNIT = 200
ROLE = {"bg1": TC.BACKGROUND_1, "bg2": TC.BACKGROUND_2, "tx1": TC.TEXT_1, "tx2": TC.TEXT_2, "accent1": TC.ACCENT_1,
        "accent2": TC.ACCENT_2, "accent3": TC.ACCENT_3, "accent4": TC.ACCENT_4, "accent5": TC.ACCENT_5,
        "accent6": TC.ACCENT_6}
NOMINAL = {"s": 12, "m": 14, "l": 20, "x": 28}          # type size by bucket, at a content box about 10 in wide
STEPS = (10, 11, 12, 14, 16, 18, 20, 24, 28, 32)
FLOOR = 10
STRETCH = 2.5                                            # most a layout is stretched down to fill the body
ALIGN = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}
ANCHOR = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}
GEOM_FACTOR = {"ellipse": 0.74, "diamond": 0.5, "triangle": 0.5, "rtTriangle": 0.5, "hexagon": 0.8, "octagon": 0.85,
               "donut": 0.5, "chevron": 0.7, "homePlate": 0.85, "pentagon": 0.8, "parallelogram": 0.8,
               "trapezoid": 0.75, "flowChartTerminator": 0.9, "plus": 0.5, "star5": 0.5, "cloud": 0.6,
               "flowChartDecision": 0.5, "sun": 0.5}
_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are", "by", "with", "this", "that", "how",
         "what", "show", "slide", "from", "into", "against"}


# ------------------------------------------------------------------ the data
@lru_cache(maxsize=1)
def load() -> tuple:
    """Every shipped layout (a tuple of dicts), in category and name order."""
    return tuple(json.loads(DATA.read_text())["layouts"]) if DATA.is_file() else ()


@lru_cache(maxsize=1)
def offered_ids() -> frozenset:
    """Ids of the layouts offered to the model. Every layout still renders when a deck names it."""
    if not OFFERED.is_file():
        return frozenset(x["id"] for x in load())
    return frozenset(json.loads(OFFERED.read_text()).get("ids", []))


def offered() -> tuple:
    """The layouts offered to the model, in load order."""
    ok = offered_ids()
    return tuple(x for x in load() if x["id"] in ok)


@lru_cache(maxsize=1)
def _by_id() -> dict:
    return {lay["id"]: lay for lay in load()}


def get(layout_id) -> dict | None:
    return _by_id().get(str(layout_id).strip()) if layout_id is not None else None


def categories() -> dict:
    out: dict = {}
    for lay in load():
        out.setdefault(lay["c"], []).append(lay["id"])
    return out


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def search(text: str, category: str | None = None, limit: int = 5, every: bool = False) -> list[dict]:
    """Offered layouts (every=True: all shipped ones) ranked for a slide's message by word overlap with the use-when
    line (3), the name (2) and the structure and framework (1), longer descriptions discounted:
    [{"id", "name", "use_when", "category", "slots", "score"}]."""
    words = [_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 2]
    if not words:
        return []
    out = []
    for lay in (load() if every else offered()):
        if category and lay["c"] != category and lay.get("a") != category:
            continue
        u, nm = lay["u"].lower(), lay["n"].lower()
        rest = f"{lay['st']} {lay['fw']} {lay['c'].replace('_', ' ')} {lay['a'].replace('_', ' ')}".lower()
        score = 0
        for w in words:
            pat = rf"\b{re.escape(w)}"
            score += 3 if re.search(pat, u) else 2 if re.search(pat, nm) else 1 if re.search(pat, rest) else 0
        if score:
            score = round(score / (1 + 0.15 * len(u.split())) * 4, 1)
            out.append({"id": lay["id"], "name": lay["n"], "use_when": lay["u"], "category": lay["c"],
                        "slots": len(lay["slots"]), "score": score})
    out.sort(key=lambda x: (-x["score"], x["name"], x["id"]))
    return out[:limit]


# ------------------------------------------------------------------ slide JSON
def _slot_value(v):
    """(kind, payload, prov) of a slot value: text (str), rows (list) or chart (dict with series)."""
    prov = None
    if isinstance(v, dict) and "series" in v:
        return "chart", v, v.get("prov")
    if isinstance(v, dict):
        prov = v.get("prov")
        v = v.get("text", v.get("rows"))
    if isinstance(v, list):
        return "rows", v, prov
    if v is None:
        return "text", "", prov
    return "text", str(v), prov


def _slots_of(s: dict) -> dict:
    """{slot number (int): value} from {"1": text} or [{"k": 1, "text": ...}]; bad keys are left to schema_errors."""
    raw = s.get("slots")
    out: dict = {}
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = [(e.get("k"), e) for e in raw if isinstance(e, dict)]
    else:
        return out
    for k, v in items:
        try:
            out[int(str(k).strip().lstrip("k"))] = v
        except (TypeError, ValueError):
            continue
    return out


def _words(t) -> int:
    return len(re.sub(r"\*\*|\^\w+", "", str(t)).split())


def schema_errors(s: dict) -> list[str]:
    """Precise messages: unknown layout, unknown slot, too many words for a slot, a slot of the wrong kind."""
    errs = []
    lid = s.get("layout")
    lay = get(lid) if isinstance(lid, str) else None
    if not isinstance(lid, str) or not lid.strip():
        errs.append("'layout' is missing: name a built-in layout by its id (find one with the layout search)")
    elif lay is None:
        near = difflib.get_close_matches(lid.strip(), list(_by_id()), n=3)
        errs.append(f"unknown layout '{lid}': there is no layout with that id" +
                    (f" (did you mean {', '.join(near)}?)" if near else "; search by what the slide says"))
    raw = s.get("slots")
    if raw is None or raw == {} or raw == []:
        errs.append("'slots' is missing or empty: give the text for the layout's slots as {slot number: text}")
        return errs
    if isinstance(raw, list):
        for i, e in enumerate(raw, 1):
            if not isinstance(e, dict) or "k" not in e or "text" not in e and "rows" not in e:
                errs.append(f"slots entry {i}: needs 'k' (the slot number) and 'text'")
    elif not isinstance(raw, dict):
        return errs + [f"'slots' must be an object {{slot number: text}}, not {type(raw).__name__}"]
    keys = raw.keys() if isinstance(raw, dict) else [e.get("k") for e in raw if isinstance(e, dict)]
    for k in keys:
        try:
            int(str(k).strip().lstrip("k"))
        except ValueError:
            errs.append(f"slot key '{k}' is not a slot number (a whole number from 1)")
    if lay is None:
        return errs
    n = len(lay["slots"])
    for k, v in sorted(_slots_of(s).items()):
        if not 1 <= k <= n:
            errs.append(f"slot {k} is not a slot of layout '{lay['id']}' ({lay['n']}); its slots are 1 to {n}")
            continue
        role, holds, max_words = lay["slots"][k - 1]
        kind, val, _ = _slot_value(v)
        if role == "chart":
            if kind != "chart" or not val.get("categories") or not val.get("series"):
                errs.append(f"slot {k} is a chart ({holds}): give {{'kind', 'categories', 'series': "
                            "[{'name', 'values'}]}")
        elif role == "table":
            if kind != "rows" or len(val) < 2 or not all(isinstance(r, list) for r in val):
                errs.append(f"slot {k} is a table ({holds}): give rows as a list of lists, the header row first")
        elif kind != "text":
            errs.append(f"slot {k} ({role}: {holds}) takes text, not {kind}")
        elif max_words and _words(val) > max_words:
            errs.append(f"slot {k} has {_words(val)} words, over its limit of {max_words} ({role}: {holds}); "
                        "shorten it")
    return errs


def _texts(s) -> list[str]:
    out = []
    for _, v in sorted(_slots_of(s).items()):
        kind, val, _ = _slot_value(v)
        if kind == "text" and val.strip():
            out += [ln.strip(" -•") for ln in re.sub(r"\*\*", "", val).split("\n") if ln.strip(" -•")]
        elif kind == "rows":
            out += [" | ".join(map(str, r)) for r in val if isinstance(r, list)]
    return out


def fallback(s: dict) -> dict:
    """A layout slide that fails its schema keeps its words: a plain argument slide from the takeaway and the slot
    texts."""
    from . import render as R
    tk = s.get("takeaway")
    texts = _texts(s)
    bl = [b if isinstance(b, dict) else {"text": str(b)} for b in (s.get("bullets") or [])]
    fb = {"archetype": "argument_only", "title": s.get("title", ""),
          "blocks": [{"text": tk.get("text") if isinstance(tk, dict) else (tk or ""),
                      "bullets": [{"text": t} for t in texts[:8]] + bl or [{"text": "[GAP]"}]}]}
    for key in ("subtitle", "source", "footnotes", "node", "id", "section", "density", "title_prov"):
        if s.get(key) is not None:
            fb[key] = s[key]
    fb = R.normalize(fb)
    fb["notes"] = (s.get("notes", "") + "\n[QA] The layout was not used: the slide did not meet its requirements."
                   ).strip()
    return fb


# ------------------------------------------------------------------ drawing
def _theme_hex(sl):
    from . import component as C
    th = C.target_theme(sl)
    return lambda role: C._slot_hex(role, th), C._ratio


def _quant(size: float) -> int:
    return max(q for q in STEPS if q <= max(size, FLOOR))


def _blocks(text: str, size: float, bold: bool, color, align) -> list[dict]:
    out = []
    for ln in str(text).split("\n"):
        if not ln.strip():
            continue
        bullet = bool(re.match(r"^\s*[-•▪]\s+", ln))
        out.append({"text": re.sub(r"^\s*[-•▪]\s+", "", ln).strip(), "size": size, "bold": bold, "numbers": False,
                    "color": color, "bullet": bullet, "align": align, "space_before": 2 if out else 0})
    return out


def _set_prst(shp, o: dict):
    """Adjustments of the preset (a:avLst) from the spec; the preset itself comes from add_shape."""
    adj = o.get("a")
    if not adj:
        return
    g = shp._element.spPr.find(qn("a:prstGeom"))
    av = g.find(qn("a:avLst"))
    if av is None:
        av = g.makeelement(qn("a:avLst"), {})
        g.append(av)
    for c in list(av):
        av.remove(c)
    for name, val in adj.items():
        av.append(av.makeelement(qn("a:gd"), {"name": name, "fmla": f"val {int(val)}"}))


def _line(sl, o, x, y, w, h, name):
    from . import render as R
    fh, fv = o.get("fh"), o.get("fv")
    bx, ex = (x + w, x) if fh else (x, x + w)
    by, ey = (y + h, y) if fv else (y, y + h)
    if abs(ex - bx) < 0.02 and abs(ey - by) < 0.02:
        return None
    c = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(bx), Inches(by), Inches(ex), Inches(ey))
    c.name = name
    R._strip_style(c)
    c.line.color.theme_color = ROLE.get(o.get("l"), TC.TEXT_2)
    c.line.width = Pt(max(0.5, min(1.5, o.get("lw", 0.75))))
    ln = c.line._get_or_add_ln()
    if o.get("d"):
        ln.append(ln.makeelement(qn("a:prstDash"), {"val": "dash"}))
    ends = o.get("e", "")
    if "h" in ends:
        ln.append(ln.makeelement(qn("a:headEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    if "t" in ends:
        ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return c


def _backdrop(drawn, cx, cy):
    """The fill role behind a point: the topmost drawn filled shape that contains it, else the slide (bg1)."""
    for (bx, by, bw, bh, role) in reversed(drawn):
        if role and bx <= cx <= bx + bw and by <= cy <= by + bh:
            return role
    return "bg1"


def a_layout(ctx, s, sl, n):
    """Draw a built-in layout in the body with the slot texts, and the takeaway and bullets in the right third."""
    from . import parts as P
    from . import render as R
    from .diagrams.radial import fit_box
    lay = get(s.get("layout"))
    if lay is None or not _slots_of(s):
        fb = fallback(s)
        return _draw_fallback(ctx, s, sl, n, fb)
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
    bx, by, bw, bh = boxes[0]
    asp = lay["r"]
    fw, fh = (bw, bw / asp) if asp > bw / bh else (bh * asp, bh)
    fh_nat = fh
    fh = min(bh, fh * STRETCH)            # a wide layout is stretched down (gaps and boxes grow) so the body fills
    x0, y0 = (bx, by) if side else (bx + (bw - fw) / 2, by + (bh - fh) / 2)       # beside an argument: top left
    hexof, ratio = _theme_hex(sl)
    slots = {k: _slot_value(v) for k, v in _slots_of(s).items() if 1 <= k <= len(lay["slots"])}
    mult = max(0.85, min(1.15, fw / 10.0))

    def geom(o):
        b = o["b"]
        x, y, w, h = x0 + b[0] / UNIT * fw, y0 + b[1] / UNIT * fh, b[2] / UNIT * fw, b[3] / UNIT * fh
        if fh > fh_nat and o["p"] not in ("line", "chart", "table") and _keeps_shape(o, fw, fh_nat):
            h0 = b[3] / UNIT * fh_nat                           # round shapes stay round, centred in their band
            y, h = y + (h - h0) / 2, h0
        return x, y, w, h

    # pass 1: what is drawn, and the largest size each text slot allows
    plan, drawn_fill = [], []
    for i, o in enumerate(lay["sh"]):
        x, y, w, h = geom(o)
        k = o.get("s")
        kind, val, prov = slots.get(k, ("text", "", None)) if k else ("text", "", None)
        filled = (kind == "text" and str(val).strip()) or (kind == "rows" and val) or (kind == "chart" and val)
        item = {"i": i, "o": o, "box": (x, y, w, h), "kind": kind, "val": val, "prov": prov, "text": bool(filled)}
        p = o["p"]
        if p in ("chart", "table"):
            if filled:
                plan.append(item)
            continue
        if p != "line":
            solid = "f" in o or "l" in o
            small = min(w, h) < 0.7
            if k and not filled and (not solid or small or o.get("ic")):
                continue                                        # an empty text holder, or a badge without its text
        if p != "line" and k and filled and kind == "text":
            tx = o.get("tx")
            if tx:
                tbx = (x0 + tx[0] / UNIT * fw, y0 + tx[1] / UNIT * fh, tx[2] / UNIT * fw, tx[3] / UNIT * fh)
                mg = (min(0.3, max(0.0, tbx[0] - x)), max(0.0, tbx[1] - y),         # a wide side inset held an icon
                      min(0.3, max(0.0, x + w - tbx[0] - tbx[2])), max(0.0, y + h - tbx[1] - tbx[3]))
            else:
                m = 0.02 if min(w, h) < 0.7 else 0.08
                mg = (m, min(0.04, m), m, min(0.04, m))
            f = GEOM_FACTOR.get(p, 1.0)
            iw = max(0.1, w * f - mg[0] - mg[2])
            ih = max(0.1, h * f - mg[1] - mg[3])
            back = o.get("f") or _backdrop(drawn_fill, x + w / 2, y + h / 2)
            item.update(mg=mg, iw=iw, ih=ih, back=back)
            rolev = o.get("tc")
            cands = [rolev] if rolev else []
            cands += ["tx1", "bg1"] if ratio(hexof("tx1"), hexof(back)) >= ratio(hexof("bg1"), hexof(back)) \
                else ["bg1", "tx1"]
            item["color"] = next((r for r in cands if ratio(hexof(r), hexof(back)) >= (3.0 if r == rolev else 0)), "tx1")
            nominal = NOMINAL[o.get("fs", "m")] * mult
            size = _quant(nominal)
            bl = _blocks(val, size, bool(o.get("bd")), ROLE[item["color"]], ALIGN[o.get("al", "l")])
            while size > FLOOR and R._need(bl, iw) > ih:
                size = _quant(size - 1)
                for b in bl:
                    b["size"] = size
            item["size"] = size
        if p != "line" and (o.get("f")):
            drawn_fill.append((x, y, w, h, o["f"]))
        plan.append(item)
    uniform: dict = {}
    for it in plan:                                             # one size per type bucket, the smallest any slot needs
        if "size" in it:
            b = it["o"].get("fs", "m")
            uniform[b] = min(uniform.get(b, 99), it["size"])

    # pass 2: draw
    n0 = len(sl.shapes)
    for it in plan:
        o, (x, y, w, h), p = it["o"], it["box"], it["o"]["p"]
        name = f"ov_Lay{it['i'] + 1}"
        if p == "line":
            _line(sl, o, x, y, w, h, name)
            continue
        if p == "chart":
            c = dict(it["val"])
            c.setdefault("kind", o.get("ck", "col"))
            R._chart(ctx, sl, n, c, x, y, w, h, name)
            continue
        if p == "table":
            rows = [[str(c) for c in r] for r in it["val"]]
            t = {"columns": rows[0], "rows": rows[1:]}
            before = len(sl.shapes)
            R._table(ctx, sl, n, t, x, y, w, h)
            for shp in list(sl.shapes)[before:]:
                shp.name = name                                  # a declared overlay: it sits where the layout puts it
            continue
        shp = sl.shapes.add_shape(MSO_SHAPE.from_xml(p), Inches(x), Inches(y), Inches(max(w, 0.02)),
                                  Inches(max(h, 0.02)))
        shp.name = name
        R._strip_style(shp)
        _set_prst(shp, o)
        if o.get("f"):
            shp.fill.solid()
            shp.fill.fore_color.theme_color = ROLE[o["f"]]
        else:
            shp.fill.background()
        if o.get("l"):
            shp.line.color.theme_color = ROLE[o["l"]]
            shp.line.width = Pt(max(0.5, min(1.5, o.get("lw", 0.75))))
            if o.get("d"):
                ln = shp.line._get_or_add_ln()
                ln.append(ln.makeelement(qn("a:prstDash"), {"val": "dash"}))
        else:
            shp.line.fill.background()
        if o.get("r"):
            shp.rotation = o["r"]
        xfrm = shp._element.spPr.find(qn("a:xfrm"))
        if o.get("fh"):
            xfrm.set("flipH", "1")
        if o.get("fv"):
            xfrm.set("flipV", "1")
        if "iw" in it and it["text"]:
            size = uniform.get(o.get("fs", "m"), it["size"])
            bl = _blocks(it["val"], size, bool(o.get("bd")), ROLE[it["color"]], ALIGN[o.get("al", "l")])
            for b in bl:
                b["prov"] = it["prov"]
            mg = it["mg"]
            fit_box(ctx, sl, n, s, shp, bl, w * GEOM_FACTOR.get(p, 1.0), h * GEOM_FACTOR.get(p, 1.0),
                    f"slot {o['s']} of the layout", ANCHOR[o.get("an", "t")], mg)
            if o.get("v"):
                bp = shp._element.txBody.find(qn("a:bodyPr"))
                bp.set("vert", "vert" if o["v"] == 1 else "vert270")
    _keep_layers(sl, n0)
    if side:
        part = {"type": "text", "lead": tk.get("text", ""), "prov": tk.get("prov"), "bullets": bullets, "_spread": True}
        P.p_text(ctx, sl, n, part, boxes[1], "editorial", 2, s, None)
    return None


def _keeps_shape(o, fw, fh_nat) -> bool:
    """A shape whose look depends on its proportions (circles, diamonds, squares near 1:1) is not stretched."""
    if o["p"] in GEOM_FACTOR or o["p"] in ("ellipse", "donut", "pie", "arc", "blockArc"):
        return True
    w, h = o["b"][2] / UNIT * fw, o["b"][3] / UNIT * fh_nat
    return o["p"] not in ("rect", "roundRect", "snip1Rect", "round2SameRect") and 0.8 <= w / max(h, 1e-6) <= 1.25


def _keep_layers(sl, n0):
    """The renderer's reading-order pass sorts a slide's shapes by their top edge, which would put a badge under the
    line it sits on when the line starts lower. When that would change what is on top of what, the layout's shapes
    become one group (depth 1, allowed by P17), which the pass moves as a unit and leaves its own order alone."""
    shp = [x for x in list(sl.shapes)[n0:] if x._element.tag.rsplit("}", 1)[-1] in ("sp", "cxnSp")]
    tops = [round((x.top or 0) / 9144) for x in shp]
    for i in range(len(shp)):
        a = shp[i]
        for j in range(i + 1, len(shp)):
            if tops[j] >= tops[i]:
                continue
            b = shp[j]                                           # drawn above a, but sorted below it
            if a._element.tag.endswith("cxnSp"):
                continue
            if min(a.left + a.width, b.left + b.width) - max(a.left, b.left) > 9144 and \
                    min(a.top + a.height, b.top + b.height) - max(a.top, b.top) > 9144:
                g = sl.shapes.add_group_shape(shapes=shp)
                g.name = "ov_LayGroup"
                return g
    return None


def _draw_fallback(ctx, s, sl, n, fb):
    from . import render as R
    errs = R.schema_errors(fb)
    if errs:
        fb = R.fallback(fb)
    arch = fb.get("archetype")
    sl._element.find(qn("p:cSld")).set("name", re.sub(r"arch:\w+", f"arch:{arch}",
                                                      sl._element.find(qn("p:cSld")).get("name") or ""))
    R.ARCHETYPES[arch](ctx, fb, sl, n)
    for key in ("notes", "source", "footnotes"):
        if fb.get(key) is not None:
            s[key] = fb[key]


# ------------------------------------------------------------------ public vault notes
def notes(dest=None) -> int:
    """One Markdown note per shipped layout, a map per category and an index, in docs/deck-engine/vault/layouts/ (the
    same front matter and [[links]] as vault.build_public). Our own descriptions only. Returns the number of layout
    notes."""
    from . import vault as V
    dest = Path(dest or V.PUBLIC / "layouts")
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.md"):
        old.unlink()
    cats: dict = {}
    n = 0
    for lay in load():
        arch = lay["a"] if lay["a"] in V.ARCHETYPES else V.ALIAS.get(lay["a"], "grid" if lay["a"] else "")
        cat = f"layouts-{lay['c'].replace('_', '-')}"
        slots = "\n".join(f"- slot {i} ({r}, up to {m} words): {h}" if r not in ("chart", "table") else
                          f"- slot {i} ({r}): {h}" for i, (r, h, m) in enumerate(lay["slots"], 1))
        body = [f"# {lay['n']}", "", f"When to use: {lay['u']}", ""]
        if lay["st"]:
            body += [f"Structure: {lay['st']}", ""]
        if lay["fw"]:
            body += [f"Framework: {lay['fw']}", ""]
        body += ["Slots:", slots, "", f"Drawn by the layout archetype: {{\"archetype\": \"layout\", \"layout\": "
                 f"\"{lay['id']}\", \"slots\": {{...}}}}", "",
                 f"Related Forge archetype: [[{arch}]]" if arch else "", "", f"Category: [[{cat}]]", ""]
        front = {"type": "forge-layout", "id": lay["id"], "category": lay["c"], "density": lay["dn"],
                 "forge_archetype": arch, "framework": lay["fw"], "aspect": lay["r"], "slots": len(lay["slots"]),
                 "reviewed": bool(lay["g"])}
        (dest / f"{lay['id']}.md").write_text(V._front(front) + "\n".join(body))
        cats.setdefault(cat, []).append(lay)
        n += 1
    for cat, ls in sorted(cats.items()):
        (dest / f"{cat}.md").write_text(
            V._front({"type": "map", "category": cat}) + f"# {cat.replace('layouts-', '').replace('-', ' ')}\n\n" +
            "\n".join(f"- [[{lay['id']}]]: {lay['n']}. {lay['u']}" for lay in ls) + "\n")
    (dest / "index.md").write_text(
        V._front({"type": "map", "category": "layouts"}) +
        "# Built-in layouts\n\nDistilled slide structures Forge draws in the deck's theme, found with the layout search. "
        "One note per layout, grouped by category.\n\n" +
        "\n".join(f"- [[{c}]] ({len(ls)})" for c, ls in sorted(cats.items())) + "\n")
    return n


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m engines.forge.layouts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("notes")
    sr = sub.add_parser("search")
    sr.add_argument("text")
    a = ap.parse_args(argv)
    if a.cmd == "notes":
        print("notes", notes())
    else:
        for x in search(a.text):
            print(f"{x['score']:>5} {x['id']} {x['category']:<18} {x['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
