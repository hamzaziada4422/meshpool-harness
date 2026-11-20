"""Layered diagrams: the pyramid (stacked bands narrowing to the top) and the funnel (centred bars narrowing by stage).

Design numbers (proportions only, measured from the golden items "pyramid_funnel" x_x_slidesalad_..._s011 (10 x 5.62 in)
and sc_sc02383_..._s003 (20 x 11.25 in); nothing copied):
  - pyramid: five bands 0.76 in high on a 0.76 in pitch (the band widths grow 0.77 in per level, a straight-sided
    triangle), base 3.86 in (0.39 of the slide width) and 3.8 in tall (height about equal to the base), text boxes
    2.08 x 0.78 in (one per band), leader lines 1.25 pt and 0.6-0.7 in long. Here: base at most 0.45 of the box and
    5.2 in, height at most 1.4 x the base, 0.06 in between bands, leader hairlines 0.75 pt, text on one left edge.
  - funnel: four bars 1.51 in high (0.134 of the slide height) on a 1.87 in pitch (gap 0.24 of a bar), widths 10.02,
    7.88, 5.70, 3.56 in (equal steps of 0.21 of the widest, narrowest 0.355), side text 3.58 x 0.67 in. Here: bars at
    most 0.95 in, equal steps down to 0.4 of the widest, a 0.26 in gap when the conversion rate sits between the
    bars (rate in 10 pt bold) or 0.07 in when it does not, the text beside on one left edge.
Everything is fitted by measurement; text that does not fit at the floor is cut, noted and reported.
"""
from __future__ import annotations

import re

from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from .. import parts as P
from .. import render as R
from . import register
from .radial import _body, _too_long, fit_box, _common_step, _grow_step, _hl, _items, _scaled, _ttl

HAIR = 0.75            # leader lines, pt
FLAT = 0.4             # the narrowest funnel bar, as a share of the widest


# ------------------------------------------------------------------ pyramid

def _pyramid_check(p, i) -> list[str]:
    lv = p.get("levels")
    errs = []
    if not isinstance(lv, list) or not 3 <= len(lv) <= 5:
        errs.append(f"'levels' needs 3 to 5 entries, top first (got {len(lv) if isinstance(lv, list) else 0}): fewer "
                    "is a list and more than 5 bands is too thin to read; merge neighbouring levels")
        lv = lv if isinstance(lv, list) else []
    for j, st in enumerate(lv):
        t = _ttl(st)
        if not t:
            errs.append(f"level {j + 1} has no 'title'")
        elif len(t.split()) > 6 or len(t) > 48:
            errs.append(f"level {j + 1} title is {len(t.split())} words: keep level titles to 6 words or fewer and "
                        "move the rest to 'text'")
    errs += _too_long(lv, "level", ((3, 30), (4, 26), (5, 12)))
    hl = p.get("highlight")
    if hl is not None and (not isinstance(hl, int) or isinstance(hl, bool) or not 0 <= hl < max(len(lv), 1)):
        errs.append(f"'highlight' is {hl!r}: use the 0-based index of a level, top first (0 to {max(len(lv) - 1, 0)})")
    return errs


def p_pyramid(ctx, sl, n, part, box, look, i, s, card):
    """Stacked bands forming one pyramid (a triangle on top, trapezoids below) on the left, each level's title and
    text to its right on the same band, all on one left edge, joined to the band by a hairline."""
    x, y, w, h = box
    lv = _items(part.get("levels"), 5, i, s, "pyramid levels")
    k = len(lv)
    titles = [_ttl(d) for d in lv]
    bodies = [_body(d) for d in lv]
    hl = _hl(part, k)
    text_w = min(5.6, max(2.6, w - 0.45 * w - 0.4))
    pw = min(5.2, 0.45 * w, w - text_w - 0.4)
    ph = min(h, 1.4 * pw)                                    # the pyramid is at most 1.4 x as tall as it is wide
    g = 0.06
    bh = (ph - (k - 1) * g) / k
    if bh < 0.32:
        raise ValueError(f"the box ({w:.1f} x {h:.1f} in) is too low for {k} pyramid levels")
    t_sz, b_sz = R.bump(12), R.bump(11)
    blks = [[{"text": t, "size": t_sz, "bold": True}] + ([{"text": b, "size": b_sz, "space_before": 3}] if b else [])
            for t, b in zip(titles, bodies)]
    tw = min(text_w, w - pw - 0.4)
    step = _common_step(blks, tw - 0.04, bh - 0.06)
    step = step or _grow_step(blks, tw - 0.04, bh - 0.06, 2 if any(bodies) else 5)
    if not any(bodies):                                      # titles alone: the column is as wide as they are
        tw = min(tw, max(R._need_w(t, max(10, t_sz - step), True) for t in titles) + 0.25)
    group = pw + 0.4 + tw
    x0 = x + (w - group) / 2
    ya = y + (h - ph) / 2                                    # the apex
    cx = x0 + pw / 2
    tx = x0 + pw + 0.4
    for j in range(k):
        yt = ya + j * (bh + g)
        yb = yt + bh
        wt, wb = pw * (yt - ya) / ph, pw * (yb - ya) / ph
        on = hl == j
        if j == 0:
            shp = P._shape(sl, MSO_SHAPE.ISOSCELES_TRIANGLE, cx - wb / 2, yt, wb, bh, f"ov_PyramidLevel{i}_1",
                           fill=TC.ACCENT_1 if on else TC.TEXT_2)
        else:
            shp = P._shape(sl, MSO_SHAPE.TRAPEZOID, cx - wb / 2, yt, wb, bh, f"ov_PyramidLevel{i}_{j + 1}",
                           fill=TC.ACCENT_1 if on else TC.TEXT_2)
            shp.adjustments[0] = ((wb - wt) / 2) / min(wb, bh)
        yc = yt + bh / 2
        half = pw * (yc - ya) / ph / 2
        R._rule(sl, cx + half + 0.1, yc, tx - 0.1 - (cx + half + 0.1), TC.ACCENT_5, HAIR, f"ov_PyramidLine{i}_{j + 1}")
        bl = _scaled(blks[j], step)
        bl[0].update(color=TC.ACCENT_1 if on else TC.TEXT_2, numbers=False)
        if len(bl) > 1:
            bl[1]["prov"] = lv[j].get("prov")
        box_ = P._shape(sl, MSO_SHAPE.RECTANGLE, tx, yt, tw, bh, f"ov_PyramidText{i}_{j + 1}")
        fit_box(ctx, sl, n, s, box_, bl, tw, bh, f"level {j + 1} of part {i}", MSO_ANCHOR.MIDDLE,
                  (0.02, 0.03, 0.02, 0.03))


# ------------------------------------------------------------------ funnel

_SUFFIX = {"k": 1e3, "m": 1e6, "mm": 1e6, "b": 1e9, "bn": 1e9}


def _num(v):
    """A number from '12,000', '$4.5M', '3.2k', 12000; None for anything else (words, ranges, empty)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(r"\s*[$€£]?\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*([kKmMbB][nN]?|mm)?\s*", str(v))
    if not m:
        return None
    return float(m.group(1).replace(",", "")) * _SUFFIX.get((m.group(2) or "").lower(), 1.0)


def _label(d) -> str:
    return P._plain(d.get("label") or d.get("title") or d.get("name") or "").strip() if isinstance(d, dict) else \
        P._plain(d).strip()


def _value(d) -> str:
    if not isinstance(d, dict) or d.get("value") is None:
        return ""
    v = d["value"]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,}"
    return P._plain(v).strip()


def _funnel_check(p, i) -> list[str]:
    st = p.get("stages")
    errs = []
    if not isinstance(st, list) or not 3 <= len(st) <= 6:
        errs.append(f"'stages' needs 3 to 6 entries, widest first (got {len(st) if isinstance(st, list) else 0}): "
                    "merge stages or split the funnel in two")
        st = st if isinstance(st, list) else []
    nums = []
    for j, d in enumerate(st):
        if not _label(d):
            errs.append(f"stage {j + 1} has no 'label'")
        elif len(_label(d).split()) > 6 or len(_label(d)) > 40:
            errs.append(f"stage {j + 1} label is {len(_label(d).split())} words: keep stage labels to 6 words or "
                        "fewer and move the rest to 'text'")
        nums.append(_num(d.get("value")) if isinstance(d, dict) else None)
    errs += _too_long([d if isinstance(d, dict) else {} for d in st], "stage", ((3, 30), (4, 26), (6, 12)))
    given = [j for j, d in enumerate(st) if isinstance(d, dict) and d.get("value") not in (None, "")]
    if given and len(given) != len(st):
        errs.append("some stages have a 'value' and some do not: give every stage a value or none")
    bad = [j + 1 for j in given if nums[j] is None]
    if p.get("rates") and (not given or bad):
        errs.append("'rates' needs a numeric 'value' on every stage"
                    + (f" (stage {bad[0]} value {st[bad[0] - 1].get('value')!r} is not a number: use 12,000, 4.5M or 3k)"
                       if bad else ""))
    ok = [j for j in range(len(st)) if nums[j] is not None]
    for a, b in zip(ok, ok[1:]):
        if nums[b] > nums[a]:
            errs.append(f"stage {b + 1} value {st[b].get('value')!r} is larger than stage {a + 1} "
                        f"({st[a].get('value')!r}): a funnel narrows; reorder the stages or check the numbers")
            break
    hl = p.get("highlight")
    if hl is not None and (not isinstance(hl, int) or isinstance(hl, bool) or not 0 <= hl < max(len(st), 1)):
        errs.append(f"'highlight' is {hl!r}: use the 0-based index of a stage, widest first (0 to {max(len(st) - 1, 0)})")
    return errs


def _both(a, b):
    pv = [p_ for d in (a, b) for p_ in (d.get("prov") or [])]
    return list(dict.fromkeys(pv)) or None


def _rate(a, b) -> str:
    r = 100.0 * b / a if a else 0.0
    return f"{r:.0f}%" if r >= 9.5 or r == 0 else f"{r:.1f}%"


def p_funnel(ctx, sl, n, part, box, look, i, s, card):
    """Centred bars narrowing in equal steps (to 0.4 of the widest), value and label inside each bar (one line, or two
    when the narrow bars need it), the step text beside the funnel on one left edge, and between the bars the
    conversion rate to the next stage as a small label when every value is a number."""
    x, y, w, h = box
    st = _items(part.get("stages"), 6, i, s, "funnel stages")
    k = len(st)
    labels = [_label(d) for d in st]
    values = [_value(d) for d in st]
    nums = [_num(d.get("value")) for d in st]
    bodies = [_body(d) for d in st]
    hl = _hl(part, k)
    numeric = all(v is not None for v in nums) and all(values)
    show = numeric and part.get("rates") is not False and k >= 2
    if part.get("rates") and not numeric:
        P._note(s, f"part {i}: conversion rates left out: every stage needs a numeric value")
    gap = 0.26 if show else 0.07
    bh = min(0.95, (h - (k - 1) * gap) / k)
    if show and bh < 0.36:                                   # a rate needs 0.24 in (10 pt and its line)
        gap = 0.24
        bh = min(0.95, (h - (k - 1) * gap) / k)
        if bh < 0.32:
            show, gap = False, 0.07
            bh = min(0.95, (h - (k - 1) * gap) / k)
            P._note(s, f"part {i}: conversion rates left out: the box is too low for {k} stages with rates")
    if bh < 0.28:                                            # 10 pt text needs 0.2 in and a margin
        raise ValueError(f"the box ({w:.1f} x {h:.1f} in) is too low for {k} funnel stages")
    H = k * bh + (k - 1) * gap
    y0 = y + (h - H) / 2
    has_text = any(bodies)
    if has_text:
        tw = min(5.0, 0.42 * w)
        fw = min(6.4, w - tw - 0.5)
    else:
        tw, fw = 0.0, min(0.85 * w, 9.0)
    group = fw + (0.5 + tw if has_text else 0)
    x0 = x + (w - group) / 2
    cx = x0 + fw / 2
    widths = [fw * (1 - (1 - FLAT) * j / max(1, k - 1)) for j in range(k)]
    # one type size for every bar: the largest at which all labels fit on one line, else on two
    big = (R.bump(13) + 3, R.bump(13) + 1) if bh >= 0.7 else ()       # tall bars: larger type when it fits
    sizes = big + (R.bump(13), R.bump(12), R.bump(11), 10)
    pick = None
    for z in sizes:
        one_h = 0.05 + R._pitch(z) / 72
        lines1 = [[{"text": (f"**{v}**  " if v else "") + lab, "size": z, "numbers": False}]
                  for v, lab in zip(values, labels)]
        if all(R._need(b, wd - 0.3) <= one_h + 0.01 for b, wd in zip(lines1, widths)):
            pick = ("one", z)
            break
        lines2 = [[{"text": f"**{v}**" if v else lab, "size": z + 1, "numbers": False}] +
                  ([{"text": lab, "size": z, "numbers": False}] if v else []) for v, lab in zip(values, labels)]
        if all(R._need(b, wd - 0.3) <= bh - 0.06 for b, wd in zip(lines2, widths)):
            pick = ("two", z)
            break
    if pick is None:
        pick = ("two" if bh >= 0.55 else "one", 10)
    mode, z = pick
    tz = R.bump(11)                                           # the step text: grows a little when the bars are tall
    if has_text and all(R._need([{"text": b, "size": tz + 2}], tw - 0.04) <= 0.88 * (bh - 0.06) for b in bodies if b):
        tz += 2
    for j in range(k):
        yt = y0 + j * (bh + gap)
        on = hl == j
        bar = P._shape(sl, MSO_SHAPE.RECTANGLE, cx - widths[j] / 2, yt, widths[j], bh, f"ov_FunnelBar{i}_{j + 1}",
                       fill=TC.ACCENT_1 if on else TC.TEXT_2)
        v, lab, prov = values[j], labels[j], st[j].get("prov")
        if mode == "one":
            bl = [{"text": (f"**{v}**  " if v else "") + lab, "size": z, "numbers": False, "color": TC.BACKGROUND_1,
                   "align": PP_ALIGN.CENTER, "prov": prov}]
        else:
            bl = [{"text": f"**{v}**" if v else lab, "size": z + 1, "numbers": False, "color": TC.BACKGROUND_1,
                   "align": PP_ALIGN.CENTER, "prov": prov}]
            if v:
                bl.append({"text": lab, "size": z, "numbers": False, "color": TC.BACKGROUND_1,
                           "align": PP_ALIGN.CENTER})
        fit_box(ctx, sl, n, s, bar, bl, widths[j], bh, f"stage {j + 1} of part {i}", MSO_ANCHOR.MIDDLE,
                  (0.1, 0.03, 0.1, 0.03))
        if show and j < k - 1:
            lab_w = 1.6
            r_ = P._shape(sl, MSO_SHAPE.RECTANGLE, cx - lab_w / 2, yt + bh, lab_w, gap, f"ov_FunnelRate{i}_{j + 1}")
            fit_box(ctx, sl, n, s, r_, [{"text": f"↓ {_rate(nums[j], nums[j + 1])}", "size": R.bump(10, 0.5),
                                          "bold": True, "numbers": False, "color": TC.TEXT_2,
                                          "align": PP_ALIGN.CENTER, "prov": _both(st[j], st[j + 1])}], lab_w, gap,
                      f"the rate after stage {j + 1} of part {i}",
                      MSO_ANCHOR.MIDDLE, (0.02, 0.0, 0.02, 0.0))
        if has_text and bodies[j]:
            tx = x0 + fw + 0.5
            yc = yt + bh / 2
            R._rule(sl, cx + widths[j] / 2 + 0.1, yc, tx - 0.1 - (cx + widths[j] / 2 + 0.1), TC.ACCENT_5, HAIR,
                    f"ov_FunnelLine{i}_{j + 1}")
            bl = [{"text": bodies[j], "size": tz, "prov": prov}]
            tb = P._shape(sl, MSO_SHAPE.RECTANGLE, tx, yt, tw, bh, f"ov_FunnelText{i}_{j + 1}")
            fit_box(ctx, sl, n, s, tb, bl, tw, bh, f"text of stage {j + 1} of part {i}", MSO_ANCHOR.MIDDLE,
                      (0.02, 0.03, 0.02, 0.03))


register("pyramid", p_pyramid, ("levels",), _pyramid_check, min_h=2.4, archetypes=("pyramid",))
register("funnel", p_funnel, ("stages",), _funnel_check, min_h=2.4, archetypes=("funnel",))
