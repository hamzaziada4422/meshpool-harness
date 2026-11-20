"""Diagram parts and archetypes: matrices, trees, Gantt plans, cycles, hub and spoke, pyramids and funnels.

Each diagram is a grid part (parts.py): native shapes in theme colours, text written into its shapes, fitted by
measurement (text that still does not fit at the floor size is cut, noted in the speaker notes and reported to the
content model as a content failure). A diagram can fill a grid region like any other part, or a whole slide through
its archetype, which draws the diagram on the left two thirds and the takeaway and bullets on the right third (or
across the slide when there is no argument).

    {"archetype": "risk_matrix", "title": "...", "x_axis": "Likelihood", "y_axis": "Impact",
     "items": [{"label": "Supplier failure", "x": 3, "y": 3}], "takeaway": {"text": "..."}, "bullets": [...]}

A module registers a diagram with `register(part_type, draw, needs, check, min_h, archetypes)`; this package imports
its modules at the end, so the registry is complete once `engines.forge.diagrams` is imported.

Design numbers were taken from the golden library items and the reference decks (proportions and spacing only; no
shape, text or image was copied): see docs/deck-engine/engine-spec.md, "Diagram archetypes".
"""
from __future__ import annotations

from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from .. import parts as P
from .. import render as R

DRAW: dict = {}          # part type -> draw(ctx, sl, n, part, box, look, i, s, card)
NEEDS: dict = {}         # part type -> keys of which at least one must be non-empty
CHECKS: dict = {}        # part type -> check(part, i) -> list of error messages
MIN_H: dict = {}         # part type -> smallest height in inches it can be drawn at
ARCHETYPES: dict = {}    # archetype name -> part type


def register(part_type, draw, needs, check=None, min_h=2.0, archetypes=()):
    DRAW[part_type] = draw
    NEEDS[part_type] = tuple(needs)
    CHECKS[part_type] = check or (lambda p, i: [])
    MIN_H[part_type] = min_h
    for a in archetypes or (part_type,):
        ARCHETYPES[a] = part_type


# ------------------------------------------------------------------ shared helpers

def txt(v) -> str:
    return P._txt(v)


def label_box(ctx, sl, n, s, shp, blocks, w, h, what, anchor=MSO_ANCHOR.MIDDLE, margins=(0.06, 0.04, 0.06, 0.04),
              grow=0):
    """Fit blocks into a shape (grow up to `grow` type steps first, then cut what does not fit at the floor) and
    write them into it."""
    iw, ih = w - margins[0] - margins[2], h - margins[1] - margins[3]
    bl = R._grow(blocks, iw, ih, grow) if grow else blocks
    bl = P._fit_trim(bl, iw, ih, s, what, ctx, n)
    P._in_shape(shp, bl, ctx, n, margins, anchor)
    return shp


def text_at(ctx, sl, n, s, x, y, w, h, blocks, name, what, anchor=MSO_ANCHOR.TOP, align=None):
    """A free text box (no fill) fitted to (w, h)."""
    if align is not None:
        for b in blocks:
            b.setdefault("align", align)
    bl = P._fit_trim(blocks, w, h, s, what, ctx, n)
    R._write(R._box(sl, x, y, w, h, name, anchor), bl, ctx, n)


def connect(sl, a, b, name, color=TC.TEXT_2, weight=0.75, arrow=False, a_site=None, b_site=None):
    """A straight connector glued to shapes a and b (glued lines are structure, not decoration, for P9)."""
    from pptx.enum.shapes import MSO_CONNECTOR
    from pptx.util import Pt
    c = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, 0, 0, 1, 1)
    c.begin_connect(a, a_site if a_site is not None else 3)
    c.end_connect(b, b_site if b_site is not None else 1)
    c.name = name
    R._strip_style(c)
    c.line.color.theme_color = color
    c.line.width = Pt(weight)
    if arrow:
        P._arrow(c)
    return c


# ------------------------------------------------------------------ archetype wrapper

ARGUMENT_KEYS = ("takeaway", "bullets", "callout")


def as_grid(s: dict) -> dict:
    """The diagram archetype as a grid slide: the diagram part, and a text part with the takeaway and bullets (main
    two thirds and side third), or the diagram across the slide when there is no argument."""
    kind = ARCHETYPES[s["archetype"]]
    part = {k: v for k, v in s.items() if k not in ("archetype", "title", "subtitle", "source", "notes", "node", "id",
                                                    "section", "density", "footnotes", "gaps", "tags", "title_prov",
                                                    *ARGUMENT_KEYS, "layout", "look", "parts")}
    part.update(type=kind, heading=s.get("exhibit_title") or s.get("subtitle") or "")
    part.pop("exhibit_title", None)
    tk = s.get("takeaway")
    tk = tk if isinstance(tk, dict) else ({"text": str(tk)} if tk else {})
    bullets = [b if isinstance(b, dict) else {"text": str(b)} for b in s.get("bullets") or []]
    parts = [part]
    if tk.get("text") or bullets:
        parts.append({"type": "text", "heading": "", "lead": tk.get("text", ""), "prov": tk.get("prov"),
                      "bullets": bullets})
    if isinstance(s.get("callout"), dict) and (s["callout"].get("bullets") or s["callout"].get("text")):
        parts.append(dict(s["callout"], type="callout"))
    out = {k: v for k, v in s.items() if k in ("title", "source", "notes", "node", "id", "section", "density",
                                                "footnotes", "gaps", "tags", "title_prov", "_cont")}
    lay = "full" if len(parts) == 1 else ("main_side" if len(parts) == 2 else "main_stack")
    out.update(archetype="grid", layout=s.get("layout") or lay, look=s.get("look") or "editorial", parts=parts)
    return out


def a_diagram(ctx, s, sl, n):
    """Archetype entry point: draws the slide as its grid, keeping the archetype's name on the slide (P15)."""
    g = as_grid(s)
    P.a_grid(ctx, g, sl, n)
    for k in ("notes",):                                  # what the grid noted (cuts, layout changes) stays on the slide
        if g.get(k):
            s[k] = g[k]


def schema_errors(s: dict) -> list[str]:
    """Precise messages for a diagram archetype (the same checks as its part)."""
    kind = ARCHETYPES.get(s.get("archetype"))
    if kind is None:
        return []
    part = as_grid(s)["parts"][0]
    return [e.replace("part 1 ", "") for e in part_errors(part, 1)]


def part_errors(p, i) -> list[str]:
    t = p.get("type")
    if not any(p.get(k) for k in NEEDS[t]):
        return [f"part {i} ({t}): needs one of {', '.join(repr(k) for k in NEEDS[t])}"]
    return [f"part {i} ({t}): {e}" for e in CHECKS[t](p, i)]


def fallback(s: dict) -> dict:
    """A diagram slide that fails its schema keeps every part that can be drawn faithfully (its words as text)."""
    return P.grid_fallback(as_grid(s))


# ------------------------------------------------------------------ 3x3 and risk matrix

LEVELS = {"low": 1, "l": 1, "1": 1, "medium": 2, "med": 2, "m": 2, "moderate": 2, "2": 2, "high": 3, "h": 3, "3": 3}


def level(v) -> int | None:
    """1, 2 or 3 from a number (1-3) or a word (low, medium, high)."""
    if isinstance(v, (int, float)) and 1 <= v <= 3:
        return int(round(v))
    return LEVELS.get(str(v).strip().lower()) if v is not None else None


def _matrix_check(p, i) -> list[str]:
    items = p.get("items") or []
    bad = [j + 1 for j, it in enumerate(items) if not isinstance(it, dict) or level(it.get("x")) is None
           or level(it.get("y")) is None]
    errs = []
    if bad:
        errs.append(f"items {bad}: each needs 'label', 'x' and 'y', each 1-3 or low/medium/high")
    if len(items) > 12:
        errs.append(f"{len(items)} items: at most 12 fit a 3x3 matrix; group or drop the minor ones")
    return errs


def _axis_words(v, default):
    lab, lo, hi = P._axis(v or default)
    return lab or default, lo or "Low", hi or "High"


def p_matrix3(ctx, sl, n, part, box, look, i, s, card):
    """A 3x3 grid (risk matrix: likelihood across, impact up). Cells are shaded by severity (x + y) with theme slots;
    each item is a numbered marker in its cell with its label beside it, at one type size for the whole grid. Cell
    labels (optional, `cells` 3x3, top row first) name the zones."""
    x, y, w, h = box
    risk = part.get("type") == "risk_matrix" or bool(part.get("risk"))
    xl, xlo, xhi = _axis_words(part.get("x_axis"), "Likelihood" if risk else "")
    yl, ylo, yhi = _axis_words(part.get("y_axis"), "Impact" if risk else "")
    items = [it for it in part.get("items") or [] if isinstance(it, dict)][:12]
    gw = max(R._need_w(t, R.bump(10, 0.5), True) for t in (ylo, yhi)) + 0.28   # the y axis column: Low and High only
    top = 0.3 if yl else 0.0                                                   # the y axis name above the grid
    bot = 0.42                                                                 # the x axis band
    gap = 0.05
    cw, ch = (w - gw - 2 * gap) / 3, (h - top - bot - 2 * gap) / 3
    # severity (x + y, 2 to 6) in three bands: low 2-3 light, medium 4 mid, high 5-6 accent with knockout text
    band = lambda sev: 0 if sev <= 3 else (1 if sev == 4 else 2)            # noqa: E731
    fills = (TC.BACKGROUND_2, TC.ACCENT_5, TC.ACCENT_1) if risk else (TC.BACKGROUND_2,) * 3
    cells = part.get("cells") or []
    groups: dict = {}
    for k, it in enumerate(items):
        groups.setdefault((level(it.get("x")), level(it.get("y"))), []).append((k + 1, it))
    size = R.bump(11, 0.5)
    gy0 = y + top
    if yl:
        R._write(R._box(sl, x + gw, y, w - gw, top - 0.04, f"ov_M3YName{i}", MSO_ANCHOR.BOTTOM),
                 [{"text": f"↑ {yl}", "size": R.bump(11, 0.5), "bold": True, "numbers": False}])
    for cx_ in (1, 2, 3):
        for cy_ in (1, 2, 3):
            gx = x + gw + (cx_ - 1) * (cw + gap)
            gy = gy0 + (3 - cy_) * (ch + gap)
            hot = risk and band(cx_ + cy_) == 2
            cell = P._shape(sl, MSO_SHAPE.RECTANGLE, gx, gy, cw, ch, f"ov_M3Cell{i}_{cx_}{cy_}",
                            fill=fills[band(cx_ + cy_)])
            fg = TC.BACKGROUND_1 if hot else TC.TEXT_1
            bl = []
            name = None
            if len(cells) == 3 and isinstance(cells[3 - cy_], list) and len(cells[3 - cy_]) == 3:
                name = cells[3 - cy_][cx_ - 1]
            if name:
                bl.append({"text": txt(name), "size": R.bump(10, 0.5), "bold": True, "numbers": False,
                           "color": TC.BACKGROUND_1 if hot else TC.TEXT_2})
            for num, it in groups.get((cx_, cy_), []):
                bl.append({"text": f"**{num}** {txt(it.get('label'))}", "size": size, "numbers": False,
                           "color": fg, "space_before": 3, "prov": it.get("prov")})
            if bl:
                label_box(ctx, sl, n, s, cell, bl, cw, ch, f"cell {cx_},{cy_} of part {i}", MSO_ANCHOR.TOP,
                          (0.1, 0.08, 0.08, 0.06), grow=2)
    # axes: an arrow up the left edge and along the bottom, Low and High at the ends, the x axis name centred
    gh = 3 * ch + 2 * gap
    P._vline(sl, x + gw - 0.06, gy0, gh, TC.TEXT_1, 0.75, f"ov_M3YAxis{i}", arrow=True)
    for lab, yy, an, tag in ((yhi, gy0, MSO_ANCHOR.TOP, "High"), (ylo, gy0 + gh - 0.24, MSO_ANCHOR.BOTTOM, "Low")):
        R._write(R._box(sl, x, yy, gw - 0.14, 0.24, f"ov_M3YEnd{i}_{tag}", an),
                 [{"text": lab, "size": R.bump(10, 0.5), "bold": True, "numbers": False, "align": PP_ALIGN.RIGHT}])
    by = gy0 + gh + 0.06
    ax = R._rule(sl, x + gw, by, w - gw, TC.TEXT_1, 0.75, f"ov_M3XAxis{i}")
    P._arrow(ax)
    lw = max(R._need_w(t, R.bump(10, 0.5), True) for t in (xlo, xhi)) + 0.1
    R._write(R._box(sl, x + gw, by + 0.04, lw, 0.24, f"ov_M3XLow{i}"),
             [{"text": xlo, "size": R.bump(10, 0.5), "bold": True, "numbers": False}])
    if xl:
        R._write(R._box(sl, x + gw + lw, by + 0.04, w - gw - 2 * lw, 0.24, f"ov_M3XName{i}"),
                 [{"text": f"{xl} →", "size": R.bump(11, 0.5), "bold": True, "numbers": False,
                   "align": PP_ALIGN.CENTER}])
    R._write(R._box(sl, x + w - lw, by + 0.04, lw, 0.24, f"ov_M3XHigh{i}"),
             [{"text": xhi, "size": R.bump(10, 0.5), "bold": True, "numbers": False, "align": PP_ALIGN.RIGHT}])


register("risk_matrix", p_matrix3, ("items",), _matrix_check, min_h=3.0, archetypes=("risk_matrix",))
register("matrix3x3", p_matrix3, ("items", "cells"), _matrix_check, min_h=3.0, archetypes=("matrix_3x3",))


# ------------------------------------------------------------------ the other diagram modules register themselves
from . import layers, radial, tree, gantt  # noqa: E402,F401
