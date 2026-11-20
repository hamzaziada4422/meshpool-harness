"""Radial diagrams: the cycle (steps on a ring, arrows round the ring) and hub and spoke (a hub with spokes around it).

Design numbers (proportions only, measured from the golden items "cycle" strategyu_..._crimson_s092 and "hub_spoke"
..._crimson_s080_c01; nothing copied):
  - cycle (13.33 x 7.5 slide): four circles of 1.4 in on a ring of 3.8 in diameter (node = 0.37 of the ring, centre
    to centre 2.7 in, so 1.3 in between neighbouring edges), ring line 3 pt, a bold centre label 2.0 x 0.6 in, the
    step list at x 7.7 in, 5.0 in wide, each item a 0.4 in title and a 0.5 in text on a 1.1 in pitch, one step in
    the accent colour. Here: nodes at most 1.5 in, about 0.3 of the box height, arrows 1 pt glued between the
    nodes with at least 0.45 in between edges, list in even slots.
  - hub and spoke (4.9 x 5.44 in figure): hub 1.70 in (0.35 of the width), six spokes of 1.44 in (0.29) on a radius of
    2.0 in (0.43 in between hub and spoke edges), links 1.5 pt, spoke outline 2 pt. Here: links 1.25 pt in the light
    grey (ACCENT_5), at least 0.3 in between hub and spokes, hub widened to fit its label.
Shapes are sized by measurement: the node size is the largest at which every title fits, with arrows at least 0.45 in
long; the step text goes beside the ring when it fits in an even slot, else inside rounded nodes.
"""
from __future__ import annotations

import math

from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from .. import parts as P
from .. import render as R
from . import label_box, register

CAP_D = 1.5            # largest circle diameter, in
ARROW_GAP = 0.45       # shortest arrow between two nodes, in
SPOKE_GAP = 0.12       # nearest two spokes, in
HUB_CLEAR = 0.3        # spoke edge to hub edge, in
CYCLE_WORDS = ((3, 30), (4, 26), (6, 12))   # words of text per step by step count
HUB_WORDS = ((4, 30), (6, 26), (8, 18))
CIRCLE_TEXT = 0.707    # the text rectangle of an ellipse is the inscribed square


# ------------------------------------------------------------------ small helpers

def _ttl(d) -> str:
    if isinstance(d, dict):
        return P._plain(d.get("title") or d.get("label") or d.get("name") or "").strip()
    return P._plain(d).strip()


def _body(d) -> str:
    return P._plain(d.get("text") or d.get("body") or "").strip() if isinstance(d, dict) else ""


def _items(v, cap, i, s, what):
    out = [d if isinstance(d, dict) else {"title": str(d)} for d in (v or []) if d]
    if len(out) > cap:
        P._note(s, f"part {i}: {what} left out: " + "; ".join(_ttl(d) for d in out[cap:]))
        out = out[:cap]
    return out


def fit_box(ctx, sl, n, s, shp, blocks, w, h, what, anchor=MSO_ANCHOR.MIDDLE, margins=(0.06, 0.04, 0.06, 0.04)):
    """label_box, plus one thing it cannot do: a single paragraph that still does not fit at the floor loses its
    trailing words (noted and reported like a dropped line) instead of running past the shape."""
    iw, ih = w - margins[0] - margins[2], h - margins[1] - margins[3]
    bl, ok = R._fit(blocks, iw, ih, 10)
    if not ok and len(blocks) == 1:
        words = P._plain(blocks[0]["text"]).split()
        kept = len(words)
        while kept > 1:
            kept -= 1
            trial = [dict(blocks[0], text=" ".join(words[:kept]) + "…")]
            if R._fit(trial, iw, ih, 10)[1]:
                break
        cut = words[kept:]
        R._cut_note(s if s is not None else {}, [" ".join(cut)])
        ctx.miss(n, what, max(3, len(cut)))
        blocks = [dict(blocks[0], text=" ".join(words[:kept]) + "…")]
    return label_box(ctx, sl, n, s, shp, blocks, w, h, what, anchor, margins)


def cap_words(k, table):
    """Words of step text per item that still render clean in a half-slide box, by item count (measured with the
    longest titles the checks accept): table is ((max items, words), ...)."""
    return next((c for n_, c in table if k <= n_), table[-1][1])


def _too_long(items, what, table):
    k = len(items)
    cap = cap_words(k, table)
    return [f"{what} {j + 1} text is {len(_body(d).split())} words: with {k} {what}s at most {cap} words fit a "
            f"half-slide box; shorten it" + (" or use fewer" if cap < 30 else "") + (f" {what}s" if cap < 30 else "")
            for j, d in enumerate(items) if len(_body(d).split()) > cap]


def _hl(part, k):
    v = part.get("highlight")
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < k else None


def _xywh(shp):
    return shp.left / 914400, shp.top / 914400, shp.width / 914400, shp.height / 914400


def _scaled(blocks, step, floor=10):
    import copy
    out = copy.deepcopy(blocks)
    for b in out:
        b["size"] = max(floor, b.get("size", 12) - step)
    return out


def _common_step(blks, w, h, floor=10):
    """The smallest number of points to take off every block list so that all of them fit (w, h): one type size for
    the whole figure."""
    step = 0
    for step in range(0, 5):
        if all(R._need(_scaled(b, step, floor), w) <= h for b in blks):
            return step
    return step


def _grow_step(blks, w, h, up=2):
    """A negative step: how many points a figure's text may grow (at most `up`) and still fill no more than 88% of its
    slot, so a roomy figure does not sit in small type."""
    for z in range(up, 0, -1):
        if all(R._need(_scaled(b, -z), w) <= 0.88 * h for b in blks):
            return -z
    return 0


def _word_fits(text, size, w, bold=True) -> bool:
    return all(R._need_w(wd, size, bold) * 1.12 <= w for wd in text.split())


def _sites(kind, x, y, w, h):
    """Connection sites of an ellipse (8, from the top counter-clockwise) or a rectangle (top, left, bottom, right)."""
    q = math.sqrt(0.5)
    v = ([(0, -1), (-q, -q), (-1, 0), (-q, q), (0, 1), (q, q), (1, 0), (q, -q)] if kind == "oval"
         else [(0, -1), (-1, 0), (0, 1), (1, 0)])
    cx, cy = x + w / 2, y + h / 2
    return [(cx + a * w / 2, cy + b * h / 2) for a, b in v], (cx, cy)


def _glue(sl, a, ka, b, kb, name, color=TC.TEXT_2, weight=1.0, arrow=False):
    """A straight connector glued to the sites of a and b that face each other (stCxn and endCxn set, ends placed on
    the real site points so every renderer draws it the same)."""
    pa, ca = _sites(ka, *_xywh(a))
    pb, cb = _sites(kb, *_xywh(b))
    dx, dy = cb[0] - ca[0], cb[1] - ca[1]
    n = math.hypot(dx, dy) or 1.0

    def best(pts, c, ux, uy):
        return max(range(len(pts)), key=lambda j: ((pts[j][0] - c[0]) * ux + (pts[j][1] - c[1]) * uy)
                   / (math.hypot(pts[j][0] - c[0], pts[j][1] - c[1]) or 1.0))

    ia, ib = best(pa, ca, dx / n, dy / n), best(pb, cb, -dx / n, -dy / n)
    c = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, 0, 0, 1, 1)
    c._connect_begin_to(a, ia)
    c._connect_end_to(b, ib)
    c.begin_x, c.begin_y = Inches(pa[ia][0]), Inches(pa[ia][1])
    c.end_x, c.end_y = Inches(pb[ib][0]), Inches(pb[ib][1])
    c.name = name
    R._strip_style(c)
    c.line.color.theme_color = color
    c.line.width = Pt(weight)
    if arrow:
        P._arrow(c)
    return c


# ------------------------------------------------------------------ ring planning

def _plan(titles, bodies, area, hub=None, inside=False):
    """The plan at the comfortable arrow length, else (a short box) at shorter arrows down to 0.24 in."""
    plan = None
    for gap, clear in ((ARROW_GAP, HUB_CLEAR), (0.32, 0.24), (0.24, 0.16)):
        plan = _plan_at(titles, bodies, area, hub, inside, gap, clear)
        if plan["ok"]:
            break
    return plan


def _plan_at(titles, bodies, area, hub, inside, gap, clear=HUB_CLEAR):
    """Node size and centres for k nodes on an ellipse that fits `area`: circles when every title fits inside one,
    else rounded rectangles (which also carry the step text when `inside`). The largest nodes at which neighbours
    keep `gap` between them, other nodes 0.1 in, and (with a hub) HUB_CLEAR to the hub. Returns a dict."""
    dx, dy, dw, dh = area
    k = len(titles)
    cxm, cym = dx + dw / 2, dy + dh / 2
    t_sz, b_sz = R.bump(12), R.bump(11)

    offs = (0.0, -180.0 / k) if k % 2 == 0 else (0.0,)      # even rings also try a flat start: TL, TR, BR, BL

    def ring(nw, nh, off=0.0):
        A, B = (dw - nw) / 2, (dh - nh) / 2
        if A < 0.3 or B < 0.3:
            return None
        r = 1.5 + 0.7 * max(0.0, min(1.0, (dw / dh - 1) / 2))      # a wide box gets a wider ring
        A, B = min(A, r * B), min(B, 1.5 * A)
        raw = [(math.cos(math.radians(-90 + off + 360 * j / k)), math.sin(math.radians(-90 + off + 360 * j / k)))
               for j in range(k)]
        x0, x1 = min(p_[0] for p_ in raw), max(p_[0] for p_ in raw)  # the nodes span the ring's whole extent, so a
        y0, y1 = min(p_[1] for p_ in raw), max(p_[1] for p_ in raw)  # ring of 3 or a flat start uses its box
        return [(cxm + A * (2 * (u - x0) / ((x1 - x0) or 1) - 1), cym + B * (2 * (v - y0) / ((y1 - y0) or 1) - 1))
                for u, v in raw], A, B

    def sep(p, q, nw, nh, oval):
        if oval:
            return math.hypot(p[0] - q[0], p[1] - q[1]) - nw
        gx, gy = abs(p[0] - q[0]) - nw, abs(p[1] - q[1]) - nh
        return max(gx, gy) if (gx < 0 and gy < 0) else math.hypot(max(gx, 0), max(gy, 0))

    def feasible(nw, nh, oval):
        for off in offs:
            r = ring(nw, nh, off)
            if r is None:
                continue
            pts, A, B = r
            if any(sep(pts[a], pts[b], nw, nh, oval) < ((min(SPOKE_GAP, gap) if hub else gap)
                                                         if (b == a + 1 or (a == 0 and b == k - 1)) else 0.1)
                   for a in range(k) for b in range(a + 1, k)):
                continue
            if hub:
                hx_, hy_ = sum(p_[0] for p_ in pts) / k, sum(p_[1] for p_ in pts) / k
                if any(sep((hx_, hy_), p_, (nw + hub[0]) / 2, (nh + hub[1]) / 2, False) < HUB_CLEAR for p_ in pts):
                    continue
            return pts
        return None

    if not inside:                                           # circles: the smallest that holds every title, up to
        def holds(d):                                        # a comfortable size, the largest at which the gaps work
            iw, ih = CIRCLE_TEXT * d - 0.08, CIRCLE_TEXT * d - 0.06
            sizes = []
            for t in titles:
                bl, fit = R._fit([{"text": t, "size": t_sz, "bold": True}], iw, ih, 10)
                if not (fit and _word_fits(t, bl[0]["size"], iw)):
                    return None
                sizes.append(bl[0]["size"])
            size = min(sizes)
            return size if all(_word_fits(t, size, iw) for t in titles) else None

        d_min = 0.7
        while d_min <= CAP_D and holds(d_min) is None:
            d_min += 0.05
        if d_min <= CAP_D:
            comfy = 0.3 * min(dw, dh) if hub is None else 0.28 * min(dw, dh) + 0.1
            d = max(d_min, min(CAP_D, comfy))
            while d >= d_min - 1e-9:
                pts = feasible(d, d, True)
                if pts:
                    return {"kind": "oval", "nw": d, "nh": d, "pts": pts, "t": holds(d), "b": b_sz, "ok": True}
                d -= 0.05
    # rounded rectangles: the biggest type at which some width works, then the widest such width
    longest = max((R._need_w(wd, 10, True) for t in titles for wd in t.split()), default=0.5)
    cap_w = 3.4 if inside else 2.8
    last = None
    for step in range(0, 4):
        ts, bs = max(10, t_sz - step), max(10, b_sz - step)
        one_line = max((R._need_w(tt, ts, True) for tt in titles), default=1.0) + 0.5   # titles alone: no wider than
        pw = min(cap_w, 0.42 * dw, one_line) if not inside else min(cap_w, 0.42 * dw)    # the longest title needs
        while pw >= max(1.0, longest * 1.2 + 0.28):
            iw = pw - 0.24
            blks = [[{"text": t, "size": ts, "bold": True}] + ([{"text": b, "size": bs, "space_before": 3}]
                                                             if (inside and b) else [])
                    for t, b in zip(titles, bodies)]
            need = max(R._need(bl, iw) for bl in blks)
            nh = max(0.4, need + 0.16)
            if nh <= 0.42 * dh:
                last = (pw, nh, ts, bs)
                pts = feasible(pw, nh, False)
                if pts:
                    return {"kind": "rect", "nw": pw, "nh": nh, "pts": pts, "t": ts, "b": bs, "ok": True}
            pw -= 0.1
    pw, nh, ts, bs = last or (1.2, 0.6, 10, 10)               # nothing keeps its gaps: the smallest, text trimmed
    r = ring(pw, nh, offs[-1]) or ([(cxm, cym)] * k, 0.0, 0.0)
    return {"kind": "rect", "nw": pw, "nh": nh, "pts": r[0], "t": ts, "b": bs, "ok": False}


def _node(ctx, sl, n, s, plan, j, cx, cy, name, fill, fg, title, body, what, sub_fg=None, prov=None):
    """One node: a circle or a rounded rectangle with its text written into it."""
    nw, nh = plan["nw"], plan["nh"]
    oval = plan["kind"] == "oval"
    shp = P._shape(sl, MSO_SHAPE.OVAL if oval else MSO_SHAPE.ROUNDED_RECTANGLE, cx - nw / 2, cy - nh / 2, nw, nh, name,
                   fill=fill)
    if not oval:
        shp.adjustments[0] = min(0.5, 0.14 / min(nw, nh) * (1 if body else 2.2))
    bl = [{"text": title, "size": plan["t"], "bold": True, "numbers": False, "color": fg, "align": PP_ALIGN.CENTER}]
    if body:
        bl.append({"text": body, "size": plan["b"], "space_before": 3, "color": sub_fg or fg,
                   "align": PP_ALIGN.CENTER, "prov": prov})
    if oval:
        fit_box(ctx, sl, n, s, shp, bl, CIRCLE_TEXT * nw, CIRCLE_TEXT * nh, what, MSO_ANCHOR.MIDDLE,
                  (0.03, 0.03, 0.03, 0.03))
    else:
        fit_box(ctx, sl, n, s, shp, bl, nw, nh, what, MSO_ANCHOR.MIDDLE, (0.12, 0.05, 0.12, 0.05))
    return shp


def _slots(ctx, sl, n, s, x, y, w, h, texts, hl, name, what, anchor=MSO_ANCHOR.MIDDLE):
    """Titles with text, one per even slot of a column (no fill): the list that sits beside a figure. Returns the
    common type step used."""
    k = len(texts)
    t_sz, b_sz = R.bump(12), R.bump(11)
    slot = h / k
    blks = [[{"text": t, "size": t_sz, "bold": True, "numbers": False}] +
            ([{"text": b, "size": b_sz, "space_before": 3}] if b else []) for t, b, _ in texts]
    step = _common_step(blks, w - 0.04, slot - 0.06)
    step = step or _grow_step(blks, w - 0.04, slot - 0.06)
    for j, ((t, b, prov), bl) in enumerate(zip(texts, blks)):
        bl = _scaled(bl, step)
        bl[0]["color"] = TC.ACCENT_1 if hl == j else TC.TEXT_2
        if len(bl) > 1:
            bl[1]["prov"] = prov
        shp = P._shape(sl, MSO_SHAPE.RECTANGLE, x, y + j * slot, w, slot, f"{name}{j + 1}")
        fit_box(ctx, sl, n, s, shp, bl, w, slot, f"{what} {j + 1}", anchor, (0.02, 0.03, 0.02, 0.03))
    return step


# ------------------------------------------------------------------ cycle

def _cycle_check(p, i) -> list[str]:
    steps = p.get("steps")
    errs = []
    if not isinstance(steps, list) or not 3 <= len(steps) <= 6:
        errs.append(f"'steps' needs 3 to 6 entries (got {len(steps) if isinstance(steps, list) else 0}): a cycle of "
                    "two reads as a toggle and of seven or more as a list; use process or a table instead")
        steps = steps if isinstance(steps, list) else []
    for j, st in enumerate(steps):
        t = _ttl(st)
        if not t:
            errs.append(f"step {j + 1} has no 'title': every node on the ring needs a short title")
        elif len(t.split()) > 5 or len(t) > 40:
            errs.append(f"step {j + 1} title is {len(t.split())} words ({len(t)} characters): keep node titles to 5 words "
                        "or fewer and move the rest to 'text'")
    errs += _too_long(steps, "step", CYCLE_WORDS)
    hl = p.get("highlight")
    if hl is not None and (not isinstance(hl, int) or isinstance(hl, bool) or not 0 <= hl < max(len(steps), 1)):
        errs.append(f"'highlight' is {hl!r}: use the 0-based index of a step (0 to {max(len(steps) - 1, 0)})")
    c = p.get("center")
    if c is not None and (not isinstance(c, str) or len(c.split()) > 4):
        errs.append("'center' must be a label of at most 4 words (it sits inside the ring)")
    return errs


def _free_box(pts, nw, nh, cx, cy, pad=0.12):
    """The largest box (width <= 2.6, height <= 1.1 in) centred on (cx, cy) that keeps `pad` clear of every node and
    of the straight arrows between consecutive nodes; None when even 0.9 x 0.4 in does not fit."""
    k = len(pts)
    chords = []
    for j in range(k):
        (x1, y1), (x2, y2) = pts[j], pts[(j + 1) % k]
        chords += [(x1 + (x2 - x1) * u / 24, y1 + (y2 - y1) * u / 24) for u in range(25)]

    def clear(iw, ih):
        hx, hy = iw / 2 + pad, ih / 2 + pad
        if any(abs(px - cx) < hx + nw / 2 and abs(py - cy) < hy + nh / 2 for px, py in pts):
            return False
        return not any(abs(px - cx) < hx and abs(py - cy) < hy for px, py in chords)

    best = None
    w_ = 2.6
    while w_ >= 0.9:
        h_ = 1.1
        while h_ >= 0.4:
            if clear(w_, h_) and (best is None or w_ * h_ > best[0] * best[1]):
                best = (w_, h_)
            h_ -= 0.1
        w_ -= 0.1
    return best


def p_cycle(ctx, sl, n, part, box, look, i, s, card):
    """Steps as nodes evenly spaced on an ellipse that fits the box, one glued arrow from each to the next and from
    the last back to the first. The step text goes in a list beside the ring when it fits an even slot (title bold,
    one type size), else inside rounded nodes; with no text the nodes carry the titles alone."""
    x, y, w, h = box
    steps = _items(part.get("steps"), 6, i, s, "cycle steps")
    k = len(steps)
    titles = [_ttl(st) for st in steps]
    bodies = [_body(st) for st in steps]
    hl = _hl(part, k)
    center = P._plain(part.get("center") or "").strip()
    surf = P._surface(look)
    mode, area, lst = "plain", (x, y, w, h), None
    if any(bodies):
        lw = min(5.0, 0.45 * w)
        dw = w - lw - 0.4
        slot = h / k
        t_sz, b_sz = R.bump(12), R.bump(11)
        blks = [[{"text": t, "size": t_sz, "bold": True}] + ([{"text": b, "size": b_sz, "space_before": 3}] if b else [])
                for t, b in zip(titles, bodies)]
        step = _common_step(blks, lw - 0.04, slot - 0.06)
        fits = max(R._need(_scaled(bl, step), lw - 0.04) for bl in blks) <= slot - 0.06 and step <= 1
        besides = dw >= 2.3
        if fits and besides:
            mode = "beside"
        else:                                                # rounded nodes that carry the text, if they fit the box
            plan_in = _plan(titles, bodies, area, inside=True)
            mode = "inside" if plan_in["ok"] or not besides else "beside"   # else the list beside, text trimmed
        if mode == "beside":
            x0 = x + (w - (dw + 0.4 + lw)) / 2
            area, lst = (x0, y, dw, h), (x0 + dw + 0.4, lw)
    plan = _plan(titles, bodies, area, inside=(mode == "inside"))
    if not plan["ok"]:
        raise ValueError(f"the box ({w:.1f} x {h:.1f} in) is too small for a cycle of {k} steps")
    pts = plan["pts"]
    shapes = []
    carries = mode == "inside"
    for j in range(k):
        on = hl == j
        fill = TC.ACCENT_1 if on else (surf if carries else TC.TEXT_2)
        fg = TC.BACKGROUND_1 if (on or not carries) else TC.TEXT_2
        shapes.append(_node(ctx, sl, n, s, plan, j, pts[j][0], pts[j][1], f"ov_CycleNode{i}_{j + 1}", fill, fg,
                            titles[j], bodies[j] if carries else "", f"step {j + 1} of part {i}",
                            sub_fg=TC.BACKGROUND_1 if on else TC.TEXT_1, prov=steps[j].get("prov")))
    for j in range(k):
        _glue(sl, shapes[j], plan["kind"], shapes[(j + 1) % k], plan["kind"], f"ov_CycleArrow{i}_{j + 1}",
              TC.TEXT_2, 1.0, arrow=True)
    if center:                                               # inside the ring, clear of the nodes and the arrows
        cx, cy = sum(p_[0] for p_ in pts) / k, sum(p_[1] for p_ in pts) / k
        box_ = _free_box(pts, plan["nw"], plan["nh"], cx, cy)
        if box_:
            iw, ih = box_
            c = P._shape(sl, MSO_SHAPE.RECTANGLE, cx - iw / 2, cy - ih / 2, iw, ih, f"ov_CycleCenter{i}")
            fit_box(ctx, sl, n, s, c, [{"text": center, "size": R.bump(14), "bold": True, "numbers": False,
                                         "color": TC.TEXT_2, "align": PP_ALIGN.CENTER}], iw, ih,
                      f"the centre label of part {i}", MSO_ANCHOR.MIDDLE, (0.02, 0.02, 0.02, 0.02))
        else:
            P._note(s, f"part {i}: the centre label '{center}' did not fit inside the ring and was left out")
    if mode == "beside":
        _slots(ctx, sl, n, s, lst[0], y, lst[1], h, [(t, b, st.get("prov")) for t, b, st in zip(titles, bodies, steps)],
               hl, f"ov_CycleText{i}_", f"step text of part {i}")


# ------------------------------------------------------------------ hub and spoke

def _hub_check(p, i) -> list[str]:
    errs = []
    if not P._plain(p.get("hub") or "").strip():
        errs.append("'hub' is missing: give the label of the centre shape (a few words)")
    elif len(P._plain(p["hub"]).split()) > 8:
        errs.append(f"'hub' is {len(P._plain(p['hub']).split())} words: keep the centre label to 8 words or fewer")
    sp = p.get("spokes")
    if not isinstance(sp, list) or not 3 <= len(sp) <= 8:
        errs.append(f"'spokes' needs 3 to 8 entries (got {len(sp) if isinstance(sp, list) else 0}): fewer than 3 is a "
                    "line, more than 8 should be grouped")
        sp = sp if isinstance(sp, list) else []
    for j, st in enumerate(sp):
        t = _ttl(st)
        if not t:
            errs.append(f"spoke {j + 1} has no 'title'")
        elif len(t.split()) > 6 or len(t) > 48:
            errs.append(f"spoke {j + 1} title is {len(t.split())} words: keep spoke titles to 6 words or fewer and "
                        "move the rest to 'text'")
    errs += _too_long(sp, "spoke", HUB_WORDS)
    hl = p.get("highlight")
    if hl is not None and (not isinstance(hl, int) or isinstance(hl, bool) or not 0 <= hl < max(len(sp), 1)):
        errs.append(f"'highlight' is {hl!r}: use the 0-based index of a spoke (0 to {max(len(sp) - 1, 0)})")
    return errs


def p_hub_spoke(ctx, sl, n, part, box, look, i, s, card):
    """A hub shape in the middle with the spokes around it, each joined to the hub by a glued connector. Spokes with
    no text are circles or pills on a ring (titles inside); spokes with text sit in two columns, left and right of
    the hub, as boxes that hold their title and text, so nothing overlaps and the text is never outside its shape."""
    x, y, w, h = box
    sp = _items(part.get("spokes"), 8, i, s, "spokes")
    k = len(sp)
    titles = [_ttl(d) for d in sp]
    bodies = [_body(d) for d in sp]
    hub_t = P._plain(part.get("hub") or "").strip()
    hl = _hl(part, k)
    surf = P._surface(look)
    link, link_w = TC.ACCENT_5, 1.25
    t_hub = R.bump(13)

    def hub_dims(hh):
        """A hub at least hh high, widened until the longest word of its label fits inside the ellipse."""
        size = t_hub
        while True:
            need = max(R._need_w(wd, size, True) for wd in hub_t.split()) * 1.15 + 0.1
            hw = max(hh, need / CIRCLE_TEXT)
            if (hw <= 2.0 * hh and hw <= 0.36 * w) or size <= 10:       # not wider than a third of the box
                return hw, size
            size -= 1

    def draw_hub(cx, cy, hw, hh, size):
        hub = P._shape(sl, MSO_SHAPE.OVAL, cx - hw / 2, cy - hh / 2, hw, hh, f"ov_HubShape{i}", fill=TC.TEXT_2)
        bl = [{"text": hub_t, "size": size, "bold": True, "numbers": False, "color": TC.BACKGROUND_1,
               "align": PP_ALIGN.CENTER}]
        fit_box(ctx, sl, n, s, hub, bl, CIRCLE_TEXT * hw, CIRCLE_TEXT * hh, f"the hub of part {i}", MSO_ANCHOR.MIDDLE,
                  (0.03, 0.03, 0.03, 0.03))
        return hub

    if not any(bodies):                                      # radial: titles inside the nodes, if the ring fits
        top_d = min(1.7, 0.34 * min(w, h) + 0.2)
        plan, dims, d = None, None, top_d
        for d in (top_d, 1.4, 1.2, 1.05, 0.9):
            if d > top_d + 1e-6:
                continue
            dims = hub_dims(d)
            plan = _plan(titles, bodies, (x, y, w, h), hub=(dims[0], d))
            if plan["ok"]:
                break
        if plan["ok"]:
            hw, size = dims
            pts = plan["pts"]
            hub = draw_hub(sum(p_[0] for p_ in pts) / k, sum(p_[1] for p_ in pts) / k, hw, d, size)
            nodes = []
            for j in range(k):
                on = hl == j
                nodes.append(_node(ctx, sl, n, s, plan, j, pts[j][0], pts[j][1], f"ov_Spoke{i}_{j + 1}",
                                   TC.ACCENT_1 if on else surf, TC.BACKGROUND_1 if on else TC.TEXT_2, titles[j], "",
                                   f"spoke {j + 1} of part {i}"))
            for j in range(k):
                _glue(sl, hub, "oval", nodes[j], plan["kind"], f"ov_SpokeLink{i}_{j + 1}", link, link_w)
            return
    # two columns of boxes (title + text inside), hub in the middle, links from the hub's side sites; a narrow box
    # takes one column to the right of the hub instead
    hh = max(0.7, min(1.4, 0.22 * w + 0.1, 0.4 * h))
    hw, hsize = hub_dims(hh)
    gc = min(0.45, max(0.25, 0.1 * h))                      # a short box takes the width for the boxes, not the links
    word = max((R._need_w(wd, 10, True) for tt in titles for wd in tt.split()), default=0.5)
    cw = min(3.6, (w - hw - 2 * gc) / 2)
    two = cw >= word * 1.15 + 0.3
    if two:
        left = (k + 1) // 2
        cols = [list(range(0, left)), list(range(left, k))]
        total = 2 * cw + hw + 2 * gc
    else:
        cols = [[], list(range(k))]
        cw = min(3.6, w - hw - gc)
        if cw < word * 1.15 + 0.3:                           # a title word would break inside its box
            raise ValueError(f"the box ({w:.1f} x {h:.1f} in) is too narrow for the spoke titles")
        total = hw + gc + cw
    rows = max(len(c) for c in cols)
    x0 = x + (w - total) / 2
    gap = 0.12
    avail = min(1.5, (h - gap * (rows - 1)) / rows)
    t_sz, b_sz = R.bump(12), R.bump(11)
    blks = [[{"text": t_, "size": t_sz, "bold": True}] + ([{"text": b_, "size": b_sz, "space_before": 3}] if b_ else [])
            for t_, b_ in zip(titles, bodies)]
    step = _common_step(blks, cw - 0.28, avail - 0.08)
    step = step or _grow_step(blks, cw - 0.28, avail - 0.08)
    need = max(R._need(_scaled(bl, step), cw - 0.28) for bl in blks)
    rh = min(avail, max(0.75, need + 0.3, 0.8 * avail))     # about the slot, never taller than the text calls for
    if not any(bodies):
        rh = min(avail, max(0.5, need + 0.3, 0.6 * avail))   # titles alone: slimmer boxes
    if rh < (0.45 if any(bodies) else 0.34):
        raise ValueError(f"the box ({w:.1f} x {h:.1f} in) is too low for {rows} rows of spokes")
    cy = y + h / 2
    hub = draw_hub((x0 + cw + gc + hw / 2) if two else (x0 + hw / 2), cy, hw, hh, hsize)
    boxes = {}
    for ci, col in enumerate(cols):
        bx = x0 if ci == 0 else (x0 + cw + 2 * gc + hw if two else x0 + hw + gc)
        colh = len(col) * rh + (len(col) - 1) * gap
        by0 = cy - colh / 2
        for r_, j in enumerate(col):
            on = hl == j
            shp = P._shape(sl, MSO_SHAPE.RECTANGLE, bx, by0 + r_ * (rh + gap), cw, rh, f"ov_Spoke{i}_{j + 1}",
                           fill=TC.ACCENT_1 if on else surf)
            b2 = _scaled(blks[j], step)
            b2[0].update(color=TC.BACKGROUND_1 if on else TC.TEXT_2, numbers=False)
            if len(b2) > 1:
                b2[1].update(color=TC.BACKGROUND_1 if on else TC.TEXT_1, prov=sp[j].get("prov"))
            fit_box(ctx, sl, n, s, shp, b2, cw, rh, f"spoke {j + 1} of part {i}", MSO_ANCHOR.MIDDLE,
                      (0.14, 0.04, 0.14, 0.04))
            boxes[j] = shp
    for j in range(k):
        _glue(sl, hub, "oval", boxes[j], "rect", f"ov_SpokeLink{i}_{j + 1}", link, link_w)


register("cycle", p_cycle, ("steps",), _cycle_check, min_h=2.4, archetypes=("cycle",))
register("hub_spoke", p_hub_spoke, ("spokes",), _hub_check, min_h=2.4, archetypes=("hub_spoke",))
