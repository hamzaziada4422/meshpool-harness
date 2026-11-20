"""Gantt plan: a label column, a period header, hairline grid rules, one bar per row, milestones as small diamonds.

A grid part (parts.py): native rectangles and diamonds in theme colours, text written into its shapes and fitted by
measurement (labels that do not fit at the floor lose words from their end, noted in the speaker notes and reported
as a content failure). Rows that cannot fit the height at the minimum pitch are left out and noted.

Proportions taken from the golden library items (strategyu crimson s089 pilot plan, s088 roadmap; proportions only):
  slide 12.1 x 4.6 in of plan: label column 3.5 in (29%), a header of 0.35 in, rows 0.58 in apart, bars 0.32 in high
  (55% of the row), no rounded ends, period rules 0.75 pt grey, milestone diamonds 0.40 in, one accent colour for the
  focus bar and the milestones.
"""
from __future__ import annotations

import math

from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from .. import parts as P
from .. import render as R
from . import register
from .tree import FLOOR, clean, fit_node

MAX_ROWS = 14
MIN_PERIODS, MAX_PERIODS = 3, 16
MIN_PITCH = 0.26              # a one-line label at the floor: 0.2 in of text and a little air
MAX_PITCH = 0.7               # reference: 0.58
GRID = TC.ACCENT_5
GRID_PT = 0.5


def _idx(v, periods):
    """Index of a period from an index (0-based) or its label; None when it is neither."""
    if isinstance(v, bool) or v is None:
        return None
    names = [p.casefold() for p in periods]
    if isinstance(v, float) and v == int(v):
        v = int(v)
    if isinstance(v, int):
        if 0 <= v < len(periods):
            return v
        return names.index(str(v)) if str(v) in names else None
    t = clean(v).casefold()
    if t in names:
        return names.index(t)
    return int(t) if t.isdigit() and int(t) < len(periods) else None


def _where(periods):
    return f"periods {periods[0]!r} to {periods[-1]!r}, or an index 0 to {len(periods) - 1}"


def _gantt_check(p, i) -> list[str]:
    errs = []
    raw = p.get("periods")
    if not isinstance(raw, list) or not MIN_PERIODS <= len(raw) <= MAX_PERIODS:
        errs.append(f"periods: needs {MIN_PERIODS} to {MAX_PERIODS} period labels (got "
                    f"{len(raw) if isinstance(raw, list) else 'none'}); use coarser periods if there are more")
        return errs
    periods = [clean(v) for v in raw]
    if not all(periods):
        errs.append("periods: every period needs a label")
        return errs
    dup = sorted({v for v in periods if periods.count(v) > 1})
    if dup:
        errs.append(f"periods: duplicate label {dup[0]!r}; labels must be unique so that rows can refer to them")
    rows = p.get("rows")
    if not isinstance(rows, list) or not rows:
        errs.append("needs 'rows': a list of {label, start, end, group?, milestones?, highlight?}")
        return errs
    if len(rows) > MAX_ROWS:
        errs.append(f"{len(rows)} rows: at most {MAX_ROWS} fit a plan; merge or drop {len(rows) - MAX_ROWS}")
    for j, r in enumerate(rows):
        if not isinstance(r, dict) or not clean(r.get("label")):
            errs.append(f"rows[{j}]: needs a 'label'")
            continue
        who = f"rows[{j}] ({clean(r['label'])!r})"
        has_bar = r.get("start") is not None or r.get("end") is not None
        ms = r.get("milestones")
        if ms is not None and not isinstance(ms, list):
            errs.append(f"{who}: 'milestones' must be a list of {{at, label}}")
            ms = None
        if not has_bar and not ms:
            errs.append(f"{who}: needs 'start' and 'end', or at least one milestone")
        if has_bar:
            a, b = _idx(r.get("start"), periods), _idx(r.get("end"), periods)
            for key, val, got in (("start", r.get("start"), a), ("end", r.get("end"), b)):
                if val is None:
                    errs.append(f"{who}: needs '{key}' (a bar has a start and an end, both inclusive)")
                elif got is None:
                    errs.append(f"{who}: {key} {val!r} is not one of the {_where(periods)}")
            if a is not None and b is not None and b < a:
                errs.append(f"{who}: ends in {periods[b]!r} before it starts in {periods[a]!r}; swap start and end")
        for g, m in enumerate(ms or []):
            if not isinstance(m, dict) or not clean(m.get("label")):
                errs.append(f"{who}.milestones[{g}]: needs 'at' and a short 'label'")
            elif _idx(m.get("at"), periods) is None:
                errs.append(f"{who}.milestones[{g}] ({clean(m['label'])!r}): at {m.get('at')!r} is not one of the "
                            f"{_where(periods)}")
        if r.get("group") is not None and not isinstance(r.get("group"), str):
            errs.append(f"{who}: 'group' must be the workstream name as text")
    return errs


def _header_plan(periods, pw, size0):
    """(size, lines, stride): the largest header type at which every period label fits its column in one or two
    lines; else labels on every stride-th period only."""
    for lines in (1, 2):
        for size in dict.fromkeys((size0, FLOOR)):
            ok = all(max([R._need_w(wd, size, True) for wd in lb.split()] or [0]) + 0.06 <= pw
                     and P._lines(lb, pw - 0.06, size, True) <= lines for lb in periods)
            if ok:
                return size, lines, 1
    need = max(R._need_w(lb, FLOOR, True) for lb in periods) + 0.1
    return FLOOR, 1, max(2, math.ceil(need / pw))


def p_gantt(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    periods = [clean(v) for v in part.get("periods") or []][:MAX_PERIODS]
    NP = len(periods)
    rows = []
    cut = []
    for r in part.get("rows") or []:
        if not isinstance(r, dict) or not clean(r.get("label")):
            continue
        a, b = _idx(r.get("start"), periods), _idx(r.get("end"), periods)
        bar = (min(a, b), max(a, b)) if a is not None and b is not None else None
        ms = [(_idx(m.get("at"), periods), clean(m.get("label"))) for m in r.get("milestones") or []
              if isinstance(m, dict) and _idx(m.get("at"), periods) is not None]
        if bar is None and not ms:
            continue
        rows.append({"label": clean(r.get("label")), "bar": bar, "ms": sorted(ms), "hl": bool(r.get("highlight")), "prov": r.get("prov"),
                     "group": clean(r.get("group")) if r.get("group") else ""})
    if len(rows) > MAX_ROWS:
        cut += [r["label"] for r in rows[MAX_ROWS:]]
        rows = rows[:MAX_ROWS]
    size0 = R.bump(12, 0.5)
    hsize0 = R.bump(11, 0.5)

    def lines_of(rs):
        out, g = [], None
        for r in rs:
            if r["group"] and r["group"] != g:
                out.append(("group", r))
            g = r["group"] or g
            out.append(("row", r))
        return out

    # header height depends on the period width, which depends on the label column, which depends on the label size
    cap = 0.35 * w
    grouped = any(r["group"] for r in rows)
    indent = 0.14 if grouped else 0.0
    sz = size0
    while True:
        nat = max([R._need_w(r["label"], sz, r["hl"]) + indent for r in rows] +
                  [R._need_w(r["group"], sz, True) for r in rows if r["group"]] + [0.8]) + 0.18
        if nat <= cap or sz <= FLOOR:
            break
        sz -= 1
    lw = min(cap, nat)
    x0 = x + lw
    pw = (w - lw) / NP
    hs, hlines, stride = _header_plan(periods, pw, hsize0)
    hh = hlines * R._pitch(hs) / 72 + 0.14
    # rows: as many as fit at the minimum pitch
    while True:
        ls = lines_of(rows)
        if len(ls) * MIN_PITCH <= h - hh - 0.04 or len(rows) <= 1:
            break
        cut.append(rows.pop()["label"])
    if cut:
        R._cut_note(s if s is not None else {}, cut)
        if ctx is not None:
            ctx.miss(n, f"rows of the plan in part {i}", max(3, 2 * len(cut)))
    ls = lines_of(rows)
    L = len(ls)
    pitch = max(MIN_PITCH, min(MAX_PITCH, (h - hh - 0.04) / L))
    total = hh + L * pitch
    oy = y + (h - total) * 0.3
    gy0 = oy + hh                                          # the grid starts under the header
    bh = max(0.12, min(0.34, pitch * 0.55))
    dd = bh                                                # same top as the bar: the diamond stays above it
    surf = P._surface(look)
    n_ = [0]

    def nm(tag):
        n_[0] += 1
        return f"ov_G{tag}{i}_{n_[0]}"

    ry = lambda k: gy0 + k * pitch                                                      # noqa: E731
    # group bands (behind everything), then the hairlines
    for k, (kind, r) in enumerate(ls):
        if kind == "group":
            band = P._shape(sl, MSO_SHAPE.RECTANGLE, x, ry(k), w, pitch, nm("Group"), fill=surf)
            bl = fit_node(ctx, n, s, r["group"], "", lw - 0.14, pitch - 0.04, sz, False, f"group {r['group']!r}",
                          TC.TEXT_2, TC.TEXT_2, PP_ALIGN.LEFT)
            P._in_shape(band, bl, ctx, n, (0.08, 0.02, w - lw + 0.06, 0.02), MSO_ANCHOR.MIDDLE)
    R._rule(sl, x, gy0, w, TC.TEXT_1, 0.75, nm("TopRule"))
    for k in range(1, L + 1):
        R._rule(sl, x, ry(k), w, GRID, GRID_PT, nm("Row"))
    segs, k0 = [], 0                                       # period rules run through the rows, not the group bands
    for k, (kind, _) in enumerate(ls + [("group", None)]):
        if kind == "group":
            if k > k0:
                segs.append((k0, k))
            k0 = k + 1
    for a, b in segs:
        for j in range(NP + 1):
            P._vline(sl, x0 + j * pw, ry(a), (b - a) * pitch, GRID, GRID_PT, nm("Period"))
    # header labels
    for j, lb in enumerate(periods):
        if j % stride:
            continue
        cx0 = x0 + j * pw
        wcell = min(stride * pw, x + w - cx0)
        al = PP_ALIGN.CENTER if stride == 1 else PP_ALIGN.LEFT
        if stride > 1 and wcell < R._need_w(lb, hs, True) + 0.1:       # the last label keeps its width, flush right
            wcell = R._need_w(lb, hs, True) + 0.1
            cx0, al = x + w - wcell, PP_ALIGN.RIGHT
        cell = P._shape(sl, MSO_SHAPE.RECTANGLE, cx0, oy, wcell, hh, nm("Head"))
        bl = [{"text": lb, "size": hs, "bold": True, "numbers": False, "color": TC.TEXT_2, "align": al}]
        bl = P._fit_trim(bl, wcell - 0.06, hh - 0.06, s, f"period {lb!r} of part {i}", ctx, n)
        P._in_shape(cell, bl, ctx, n, (0.03, 0.03, 0.03, 0.03), MSO_ANCHOR.MIDDLE)
    # rows
    for k, (kind, r) in enumerate(ls):
        if kind == "group":
            continue
        top = ry(k)
        cy = top + pitch / 2
        ind = indent if grouped and r["group"] else 0.0
        lab = P._shape(sl, MSO_SHAPE.RECTANGLE, x, top, lw, pitch, nm("Label"))
        bl = fit_node(ctx, n, s, r["label"], "", lw - 0.1 - ind - 0.04, pitch - 0.04, sz, False,
                      f"row {r['label']!r} of part {i}", TC.TEXT_1, TC.TEXT_1, PP_ALIGN.LEFT)
        if not r["hl"]:
            for b in bl:
                b["bold"] = False
        P._in_shape(lab, bl, ctx, n, (0.04 + ind, 0.02, 0.06, 0.02), MSO_ANCHOR.MIDDLE)
        bar_l = bar_r = None
        if r["bar"]:
            a, b = r["bar"]
            bar_l, bar_r = x0 + a * pw + 0.02, x0 + (b + 1) * pw - 0.02
            P._shape(sl, MSO_SHAPE.RECTANGLE, bar_l, cy - bh / 2, bar_r - bar_l, bh, nm("Bar"),
                     fill=TC.ACCENT_1 if r["hl"] else TC.TEXT_2)
        msz = R.bump(11, 0.5)
        xs = [x0 + (at + 0.5) * pw for at, _ in r["ms"]]
        for m, (at, text) in enumerate(r["ms"]):
            mx = xs[m]
            P._shape(sl, MSO_SHAPE.DIAMOND, mx - dd / 2, cy - dd / 2, dd, dd, nm("Milestone"), fill=TC.ACCENT_1,
                     line=TC.BACKGROUND_1, weight=1.0)
            on_bar = bar_l is not None and bar_l - dd / 2 <= mx <= bar_r + dd / 2
            start_r = max(mx + dd / 2, bar_r if on_bar else 0) + 0.05
            lim_r = (xs[m + 1] - dd / 2 - 0.05) if m + 1 < len(xs) else x + w
            end_l = min(mx - dd / 2, bar_l if on_bar else 1e9) - 0.05
            lim_l = (xs[m - 1] + dd / 2 + 0.05) if m else x0
            wneed = R._need_w(text, msz) + 0.14
            av_r, av_l = lim_r - start_r, end_l - lim_l
            right = av_r >= wneed or av_r >= av_l
            wlab = max(0.3, min(wneed, av_r if right else av_l))
            lx = start_r if right else end_l - wlab
            box_ = P._shape(sl, MSO_SHAPE.RECTANGLE, lx, top, wlab, pitch, nm("MLabel"),
                            fill=TC.BACKGROUND_2 if P.LOOKS[look]["card"] else TC.BACKGROUND_1)   # clears the rules
            bl = fit_node(ctx, n, s, text, "", wlab - 0.1, pitch - 0.04, msz, False,
                          f"milestone {text!r} of part {i}", TC.TEXT_1, TC.TEXT_1, PP_ALIGN.LEFT if right else PP_ALIGN.RIGHT)
            for b in bl:
                b["bold"] = False
            P._in_shape(box_, bl, ctx, n, (0.05, 0.02, 0.05, 0.02), MSO_ANCHOR.MIDDLE)


register("gantt", p_gantt, ("rows",), _gantt_check, min_h=3.0, archetypes=("gantt", "gantt_plan"))
