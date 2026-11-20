"""Tree diagrams: the issue tree (a question or metric broken into drivers, left to right) and the org chart (top down).

Both are grid parts (parts.py) built from native rectangles in theme colours and axis-aligned hairlines (a vertical
spine per parent and short horizontals), text written into its box, one type size and one text layout for all the
boxes of a level, fitted by measurement: text that does not fit at the floor is cut word by word, noted in the speaker
notes and reported as a content failure.

Proportions taken from the golden library items (strategyu crimson s016 issue tree, s107 org chart; proportions only):
  issue tree  box 2.2-2.5 x 0.95 in (about 2.4:1), columns 0.6-0.8 in apart, a spine in the middle of the gap (0.30-0.35
              in out of the parent, 0.35-0.5 in into the child), boxes 0.2 in apart, links 1.5 pt grey, root and the
              focus node in the accent colour.
  org chart   root 2.8 x 0.95, level 1 boxes 2.05 x 0.95 with 0.38 in between, 0.35 in of link above each level, a
              horizontal bus of 1.5 pt, level 2 boxes 1.85 x 0.55 stacked 0.15 in apart under their parent.
"""
from __future__ import annotations

import math

from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from .. import parts as P
from .. import render as R
from . import register, txt

FLOOR = P.FLOOR
LINK = TC.ACCENT_5
LINK_PT = 1.25


# ------------------------------------------------------------------ shared by the tree-like diagrams (and the Gantt)

def clean(v) -> str:
    """A label as one plain line (no markup, no stray whitespace)."""
    return " ".join(P._plain(txt(v)).split())


def lab(v) -> str:
    """The label of a node given as text or as {label, ...}."""
    return clean(v.get("label")) if isinstance(v, dict) else clean(v)


def node_blocks(label, sub, size, one, fg, sfg, align, k=None, with_sub=True):
    """The text of a box: a bold label and an optional value line (two paragraphs, or one when the box is low). k
    keeps only the first k words of the label (the rest is cut)."""
    words = label.split()
    lab = label if k is None or k >= len(words) else " ".join(words[:k]) + "…"
    base = {"numbers": False, "align": align, "size": size}
    if sub and with_sub:
        if one:
            return [dict(base, text=f"**{lab}**  {sub}", color=fg, _m=True)]
        return [dict(base, text=lab, bold=True, color=fg), dict(base, text=sub, color=sfg, space_before=1)]
    return [dict(base, text=lab, bold=True, color=fg)]


def need(blocks, iw) -> float:
    """Height of blocks (a one-paragraph label with a value is measured as if all bold: the safe side)."""
    return R._need([dict(b, bold=True) if b.get("_m") else b for b in blocks], iw)


def pick_style(items, size0):
    """The largest size (then two paragraphs before one) at which every item (label, sub, iw, ih) fits; one paragraph
    only when label and value share one line. Falls back to the floor with two paragraphs (the value is cut first).
    Returns (size, one_paragraph)."""
    has_sub = any(it[1] for it in items)
    items = [it for it in items if it[1]] or items         # boxes with a value decide; a bare label that is too long
    for sz in range(int(size0), FLOOR - 1, -1):         # for its box is cut on its own, it does not shrink the rest
        for one in ((False, True) if has_sub else (False,)):
            line = 0.052 + R._pitch(sz) / 72
            if all(need(node_blocks(la, su, sz, one, None, None, None), iw) <= (min(ih, line) if one and su else ih)
                   for la, su, iw, ih in items):
                return sz, one
    return FLOOR, False


def fit_node(ctx, n, s, label, sub, iw, ih, size, one, what, fg, sfg, align):
    """Blocks for one box at `size` or smaller; when they do not fit at the floor the value goes first, then the label
    loses words from its end ("…"); what was cut is noted. Always returns blocks (at least the first word)."""
    words = label.split()

    def note(k, with_sub):
        cut = ([sub] if sub and not with_sub else []) + ([" ".join(words[k:])] if k < len(words) else [])
        if cut:
            R._cut_note(s if s is not None else {}, cut)
            if ctx is not None:
                ctx.miss(n, what, max(3, sum(len(c.split()) for c in cut)))

    # the value goes before the label loses words: full text at decreasing sizes, then without the value, then words off
    plan = ([(True, len(words))] if sub else []) + [(False, k) for k in range(len(words), 0, -1)]
    for j, (with_sub, k) in enumerate(plan):
        sizes = range(int(size), FLOOR - 1, -1) if (j == 0 or (j == 1 and sub)) else (FLOOR,)
        for sz in sizes:
            bl = node_blocks(label, sub, sz, one, fg, sfg, align, k, with_sub)
            if need(bl, iw) <= ih:
                note(k, with_sub)
                return bl
    note(1, False)
    return node_blocks(label, sub, FLOOR, one, fg, sfg, align, 1, False)


def draw_box(ctx, sl, n, s, kind, x, y, w, h, name, label, sub, size, one, what, fill, fg, sfg, line=None,
             align=PP_ALIGN.LEFT, margins=(0.09, 0.03, 0.09, 0.03), weight=0.75, prov=None):
    shp = P._shape(sl, kind, x, y, w, h, name, fill=fill, line=line, weight=weight)
    bl = fit_node(ctx, n, s, label, sub, w - margins[0] - margins[2], h - margins[1] - margins[3], size, one, what,
                  fg, sfg, align)
    for b in bl:
        b["prov"] = prov
    P._in_shape(shp, bl, ctx, n, margins, MSO_ANCHOR.MIDDLE)
    return shp


def alloc(nat, mins, avail):
    """Column widths: nat scaled down to fit `avail`, none below its minimum (water filling)."""
    k = len(nat)
    out = [None] * k
    free = list(range(k))
    left = avail
    while free:
        tot = sum(nat[j] for j in free)
        scale = min(1.0, left / tot) if tot else 1.0
        low = [j for j in free if nat[j] * scale < mins[j]]
        if not low:
            for j in free:
                out[j] = nat[j] * scale
            break
        for j in low:
            out[j] = mins[j]
            left -= mins[j]
            free.remove(j)
    return [o if o is not None else mins[j] for j, o in enumerate(out)]


def _words_w(label, size=FLOOR):
    return max([R._need_w(wd, size, True) for wd in label.split()] or [0.0])


# ------------------------------------------------------------------ nodes

class Node:
    def __init__(self, label, sub, depth, prov=None):
        self.label, self.sub, self.depth, self.prov = label, sub, depth, prov
        self.kids: list[Node] = []
        self.row = 0.0
        self.parent = None


def _raw_kids(v):
    return [c for c in (v.get("children") or [])] if isinstance(v, dict) and isinstance(v.get("children"), list) else []


def _sub_of(v, keys):
    if isinstance(v, dict):
        for k in keys:
            if v.get(k) not in (None, ""):
                return clean(v[k])
    return ""


def _build(v, depth, max_depth, keys, dropped):
    node = Node(lab(v), _sub_of(v, keys), depth, v.get("prov") if isinstance(v, dict) else None)
    for c in _raw_kids(v):
        if depth >= max_depth:
            dropped.append(lab(c))
            continue
        kid = _build(c, depth + 1, max_depth, keys, dropped)
        kid.parent = node
        node.kids.append(kid)
    return node


def _walk(node):
    yield node
    for k in node.kids:
        yield from _walk(k)


def _leaves(node):
    return [x for x in _walk(node) if not x.kids and x.parent is not None]


def _leaves_of(node):
    return [x for x in _walk(node) if not x.kids]


def _prune_leaves(root, cap, dropped):
    """Drop trailing leaves until at most `cap` remain (a parent left without children becomes a leaf)."""
    while len(_leaves(root)) > cap:
        last = _leaves(root)[-1]
        last.parent.kids.remove(last)
        dropped.append(last.label)


def _found(root, label):
    lab = clean(label).casefold()
    return next((x for x in _walk(root) if x.label.casefold() == lab), None) if lab else None


def _note_dropped(ctx, n, s, dropped, what):
    if dropped:
        R._cut_note(s if s is not None else {}, dropped)
        if ctx is not None:
            ctx.miss(n, what, max(3, sum(len(d.split()) for d in dropped)))


# ------------------------------------------------------------------ issue tree

IT_MAX_LEAVES = 12
IT_MAX_DEPTH = 3
IT_MIN_PITCH = 0.32           # a one-line box at the floor: 0.2 in of text, 0.06 margins, 0.05 between boxes


def _it_check(p, i) -> list[str]:
    errs = []
    if not lab(p.get("root")):
        errs.append("needs 'root': the question or metric the tree breaks down")
    br = p.get("branches")
    if not isinstance(br, list) or not br:
        errs.append("needs 'branches': a list of {label, value?, children?}")
        return errs
    labels = []
    leaves = 0

    def walk(v, path, depth):
        nonlocal leaves
        if isinstance(v, str):
            v = {"label": v}
        if not isinstance(v, dict) or not lab(v):
            errs.append(f"{path}: needs a 'label'")
            return
        labels.append(lab(v).casefold())
        kids = v.get("children")
        if kids is not None and not isinstance(kids, list):
            errs.append(f"{path}.children: must be a list")
            kids = None
        if depth >= IT_MAX_DEPTH and kids:
            errs.append(f"{path}: has children {IT_MAX_DEPTH + 1} levels below the root; at most {IT_MAX_DEPTH} "
                        "levels fit: merge the lowest level into its parent")
            return
        if kids:
            for j, c in enumerate(kids):
                walk(c, f"{path}.children[{j}]", depth + 1)
        else:
            leaves += 1

    for j, b in enumerate(br):
        walk(b, f"branches[{j}]", 1)
    if leaves > IT_MAX_LEAVES:
        errs.append(f"{leaves} leaves: at most {IT_MAX_LEAVES} fit one tree; merge or drop {leaves - IT_MAX_LEAVES}")
    hl = p.get("highlight")
    if hl and clean(hl).casefold() != lab(p.get("root")).casefold() and clean(hl).casefold() not in labels:
        errs.append(f"highlight {clean(hl)!r} matches no node label; use the label of the root or of one node exactly")
    return errs


def p_issue_tree(ctx, sl, n, part, box, look, i, s, card):
    """Root at the left, one column per level, leaves stacked evenly; each parent is centred on its children and joined
    to them by a spine of hairlines. One type size for every box (two paragraphs, or one when the boxes are low)."""
    x, y, w, h = box
    dropped: list[str] = []
    root = _build(part.get("root"), 0, IT_MAX_DEPTH, ("value",), dropped)
    root.kids = [k for k in (_build(b, 1, IT_MAX_DEPTH, ("value",), dropped) for b in part.get("branches") or []
                             if lab(b))]
    for k in root.kids:
        k.parent = root
    _note_dropped(ctx, n, s, dropped, f"levels below the third in part {i}")
    dropped = []
    cap = min(IT_MAX_LEAVES, max(1, int((h + 0.05) // IT_MIN_PITCH)))
    _prune_leaves(root, cap, dropped)
    _note_dropped(ctx, n, s, dropped, f"leaves of the tree in part {i}")
    if not root.kids:
        return
    hl = _found(root, part.get("highlight"))
    nodes = list(_walk(root))
    depth = max(nd.depth for nd in nodes)
    ncol = depth + 1
    leaves = _leaves(root)
    for r, lf in enumerate(leaves):                       # rows: leaves in order, parents centred on their children
        lf.row = float(r)
    for nd in reversed(nodes):
        if nd.kids:
            nd.row = (nd.kids[0].row + nd.kids[-1].row) / 2
    # rows
    L = len(leaves)
    gy = max(0.05, min(0.2, 0.15 * h / L))
    pitch = h / L
    bh = pitch - gy
    if bh > 1.0:
        bh, pitch = 1.0, 1.0 + 0.2
    total = L * pitch - (pitch - bh)
    oy = y + (h - total) * 0.4
    # columns: natural width of the widest box in each, scaled to the room, then the room that is left goes to the gaps
    size0 = R.bump(12)
    marg = (0.09, 0.03, 0.09, 0.03)
    nat, mins = [], []
    for c in range(ncol):
        col = [nd for nd in nodes if nd.depth == c]
        wid = max(R._need_w(nd.label, size0, True) + (R._need_w(nd.sub, size0) + 0.12 if nd.sub else 0.0) for nd in col)
        nat.append(min(1.9 if c == 0 and ncol > 2 else 3.0, max(1.4, wid + 0.22)))   # the root grows taller, not wider
        mins.append(max(1.0, max(_words_w(nd.label) for nd in col) + 0.2))
    gx = 0.45
    if sum(nat) + (ncol - 1) * gx > w:
        gx = 0.3 if ncol <= 3 else 0.24
    cw = alloc(nat, mins, w - (ncol - 1) * gx)
    spare = w - sum(cw) - (ncol - 1) * gx
    if spare > 0.01 and ncol > 1:                         # first the links get longer (up to 0.9), then the boxes a
        add = min(spare / (ncol - 1), 0.9 - gx)           # little wider (a third at most); the rest is air at the sides
        gx += max(0.0, add)
        spare = w - sum(cw) - (ncol - 1) * gx
    if spare > 0.01:
        cw = [min(3.4, c + min(spare / ncol, c / 3)) for c in cw]
        spare = w - sum(cw) - (ncol - 1) * gx
    ox = x + max(0.0, spare) / 2
    colx = [ox + sum(cw[:c]) + c * gx for c in range(ncol)]
    ih = bh - marg[1] - marg[3]
    size, one = pick_style([(nd.label, nd.sub, cw[nd.depth] - marg[0] - marg[2], ih) for nd in nodes], size0)
    cy = lambda nd: oy + nd.row * pitch + bh / 2                                            # noqa: E731
    cnt = [0]

    def ln():
        cnt[0] += 1
        return cnt[0]

    # links first (they sit behind the boxes): a stub out of the parent, a spine, a stub into each child
    for nd in nodes:
        if not nd.kids:
            continue
        px = colx[nd.depth] + cw[nd.depth]
        kx = colx[nd.depth + 1]
        ys = [cy(k) for k in nd.kids]
        if len(nd.kids) == 1:
            R._rule(sl, px, ys[0], kx - px, LINK, LINK_PT, f"ov_ITLink{i}_{ln()}")
            continue
        sx = px + (kx - px) / 2
        R._rule(sl, px, cy(nd), sx - px, LINK, LINK_PT, f"ov_ITStub{i}_{ln()}")
        P._vline(sl, sx, min(ys), max(ys) - min(ys), LINK, LINK_PT, f"ov_ITSpine{i}_{ln()}")
        for j, yy in enumerate(ys):
            R._rule(sl, sx, yy, kx - sx, LINK, LINK_PT, f"ov_ITBranch{i}_{ln()}")
    surf = P._surface(look)
    for k, nd in enumerate(nodes):
        if nd is hl and nd is not root:
            fill, fg, sfg = TC.ACCENT_1, TC.BACKGROUND_1, TC.BACKGROUND_1
        elif nd is root:
            fill, fg, sfg = TC.TEXT_2, TC.BACKGROUND_1, TC.BACKGROUND_1
        else:
            fill, fg, sfg = surf, TC.TEXT_1, TC.TEXT_2
        hh = bh                                           # a parent may grow over the rows of its own leaves
        if nd.kids:
            rows_ = [lf.row for lf in _leaves_of(nd)]
            ext = (max(rows_) - min(rows_)) * pitch + bh
            want = need(node_blocks(nd.label, nd.sub, size, one, None, None, None),
                        cw[nd.depth] - marg[0] - marg[2]) + marg[1] + marg[3]
            hh = min(ext, max(bh, want))
        draw_box(ctx, sl, n, s, MSO_SHAPE.RECTANGLE, colx[nd.depth], cy(nd) - hh / 2, cw[nd.depth], hh,
                 f"ov_ITNode{i}_{k + 1}", nd.label, nd.sub, size, one, f"{nd.label!r} in part {i}", fill, fg, sfg,
                 margins=marg, prov=nd.prov)


register("issue_tree", p_issue_tree, ("branches",), _it_check, min_h=3.0, archetypes=("issue_tree",))


# ------------------------------------------------------------------ org chart

OC_MAX_CHILDREN = 16
OC_MAX_ROW = 8


def _oc_check(p, i) -> list[str]:
    errs = []
    root = p.get("root")
    if not lab(root):
        errs.append("needs 'root': {label, role?} (the person or unit at the top)")
    kids = p.get("children")
    if not isinstance(kids, list) or not kids:
        errs.append("needs 'children': a list of {label, role?, children?}")
        return errs
    labels = [lab(root).casefold()] if lab(root) else []
    for j, c in enumerate(kids):
        if isinstance(c, str):
            c = {"label": c}
        if not isinstance(c, dict) or not lab(c):
            errs.append(f"children[{j}]: needs a 'label'")
            continue
        labels.append(lab(c).casefold())
        gk = c.get("children")
        if gk is not None and not isinstance(gk, list):
            errs.append(f"children[{j}].children: must be a list")
            continue
        for g, d in enumerate(gk or []):
            if isinstance(d, str):
                d = {"label": d}
            if not isinstance(d, dict) or not lab(d):
                errs.append(f"children[{j}].children[{g}]: needs a 'label'")
                continue
            labels.append(lab(d).casefold())
            if d.get("children"):
                errs.append(f"children[{j}].children[{g}]: has children; an org chart has 3 levels (root, children, "
                            "their children): fold the lowest level into the label or the role")
    if len(kids) > OC_MAX_CHILDREN:
        errs.append(f"{len(kids)} children: at most {OC_MAX_CHILDREN} fit (two rows of {OC_MAX_ROW}); group or drop "
                    f"{len(kids) - OC_MAX_CHILDREN}")
    hl = p.get("highlight")
    if hl and clean(hl).casefold() not in labels:
        errs.append(f"highlight {clean(hl)!r} matches no box label; use a label exactly")
    return errs


def p_org_chart(ctx, sl, n, part, box, look, i, s, card):
    """Root on top, its children in one row (at most 8; a wider row wraps into a second row under the same parent,
    joined by a rail on the left), their children stacked under them as an indented list. Elbow links are hairlines."""
    x, y, w, h = box
    dropped: list[str] = []
    rv = part.get("root")
    root = Node(lab(rv), _sub_of(rv, ("role", "value")), 0, rv.get("prov") if isinstance(rv, dict) else None)
    kids = []
    for c in part.get("children") or []:
        if lab(c):
            kids.append(_build(c, 1, 2, ("role", "value"), dropped))
    if len(kids) > OC_MAX_CHILDREN:
        dropped += [k.label for k in kids[OC_MAX_CHILDREN:]]
        kids = kids[:OC_MAX_CHILDREN]
    _note_dropped(ctx, n, s, dropped, f"levels below the third in part {i}")
    for k in kids:
        k.parent = root
    root.kids = kids
    if not kids:
        return
    hl = _found(root, part.get("highlight"))
    size0 = R.bump(12)
    marg = (0.08, 0.03, 0.08, 0.03)
    k = len(kids)
    # one row, or rows of `per` boxes that stay at least `target` wide (never below the longest word)
    minw = max(1.15, max(_words_w(c.label) for c in kids) + 0.22)
    target = max(minw, 1.3)
    gx = 0.3 if k <= 4 else 0.2
    per = next((m for m in range(min(OC_MAX_ROW, k), 1, -1) if (w - (m - 1) * gx) / m >= target), 2 if k > 1 else 1)
    nrows = math.ceil(k / per)
    per = math.ceil(k / nrows)
    rail = 0.22 if nrows > 1 else 0.0
    bw = min(2.8, (w - 2 * rail - (per - 1) * gx) / per)
    rows = [kids[r * per:(r + 1) * per] for r in range(nrows)]
    gk_cap = max((len(c.kids) for c in kids), default=0)
    rw = min(w, max(1.8, min(3.2, max(R._need_w(root.label, size0, True), R._need_w(root.sub, size0)) + 0.3)))

    def plan(t, sk):
        r0, cr, gh = max(0.42, 0.9 * t), max(0.42, 0.9 * t), max(0.3, 0.5 * min(t, 1.2))
        v = max(0.2, 0.4 * min(t, 1.3))
        band = [cr + (0.14 + min(sk, max(len(c.kids) for c in rw_) ) * (gh + 0.08) - 0.08
                      if any(c.kids for c in rw_) and sk else 0.0) for rw_ in rows]
        return r0, cr, gh, v, band, r0 + v + sum(band) + (nrows - 1) * v

    ts = (1.5, 1.4, 1.3, 1.2, 1.1, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.0)
    sk = gk_cap
    while True:
        t = next((t for t in ts if plan(t, sk)[5] <= h), None)
        if t is not None:
            break
        if sk > 0:
            sk -= 1
            continue
        t = 0.0
        break
    r0, cr, gh, v, band, total = plan(t, sk)
    if sk < gk_cap:
        cut = [g.label for c in kids for g in c.kids[sk:]]
        for c in kids:
            c.kids = c.kids[:sk]
        _note_dropped(ctx, n, s, cut, f"the boxes under the second level in part {i}")
    oy = y + 0.03 + max(0.0, h - total - 0.03) * 0.35
    cx = x + w / 2
    # one type size and layout per level
    ihr, ihc, ihg = r0 - 0.06, cr - 0.06, gh - 0.06
    gw_ = bw - 0.2 - marg[0] - marg[2]
    sz0, one0 = pick_style([(root.label, root.sub, rw - marg[0] - marg[2], ihr)], size0)
    sz1, one1 = pick_style([(c.label, c.sub, bw - marg[0] - marg[2], ihc) for c in kids], size0)
    gks = [g for c in kids for g in c.kids]
    sz2, one2 = pick_style([(g.label, g.sub, gw_, ihg) for g in gks], min(size0, sz1)) if gks else (sz1, False)
    surf = P._surface(look)

    def style(nd, base):
        if nd is hl and nd is not root:
            return TC.ACCENT_1, TC.BACKGROUND_1, TC.BACKGROUND_1, None
        if nd is root:
            return TC.TEXT_2, TC.BACKGROUND_1, TC.BACKGROUND_1, None
        if base == 2:
            return TC.BACKGROUND_1, TC.TEXT_1, TC.TEXT_2, TC.ACCENT_5
        return surf, TC.TEXT_1, TC.TEXT_2, None

    # geometry
    rowleft = x + (w - (per * bw + (per - 1) * gx)) / 2
    railx = rowleft - max(0.1, min(gx, 0.2) / 2 + 0.02)
    top_r = []
    yy = oy + r0 + v
    for r in range(nrows):
        top_r.append(yy)
        yy += band[r] + v
    root_b = oy + r0
    # links (behind the boxes)
    nm = [0]

    def nm_(tag):
        nm[0] += 1
        return f"ov_OC{tag}{i}_{nm[0]}"

    cxs = [[rowleft + j * (bw + gx) + bw / 2 for j in range(len(rw_))] for rw_ in rows]
    bus = [top_r[r] - v / 2 for r in range(nrows)]
    if k == 1:
        P._vline(sl, cxs[0][0], root_b, top_r[0] - root_b, LINK, LINK_PT, nm_("Trunk"))
        cx = cxs[0][0] if abs(cxs[0][0] - cx) > 0.01 else cx
    else:
        P._vline(sl, cx, root_b, bus[0] - root_b, LINK, LINK_PT, nm_("Trunk"))
        for r in range(nrows):
            lo = railx if nrows > 1 else min(cxs[r][0], cx)
            hi = max(cxs[r][-1], cx if r == 0 else cxs[r][-1])
            if hi - lo > 0.01:
                R._rule(sl, lo, bus[r], hi - lo, LINK, LINK_PT, nm_("Bus"))
            if r > 0:
                P._vline(sl, railx, bus[r - 1], bus[r] - bus[r - 1], LINK, LINK_PT, nm_("Rail"))
            for cxx in cxs[r]:
                P._vline(sl, cxx, bus[r], top_r[r] - bus[r], LINK, LINK_PT, nm_("Drop"))
    ind = 0.2 if bw >= 1.7 else 0.16
    for r, rw_ in enumerate(rows):
        for j, c in enumerate(rw_):
            if not c.kids:
                continue
            bx = rowleft + j * (bw + gx)
            sx = bx + ind * 0.45
            y0 = top_r[r] + cr
            ycs = [y0 + 0.14 + g * (gh + 0.08) + gh / 2 for g in range(len(c.kids))]
            P._vline(sl, sx, y0, ycs[-1] - y0, LINK, LINK_PT, nm_("Spine"))
            for yc in ycs:
                R._rule(sl, sx, yc, bx + ind - sx, LINK, LINK_PT, nm_("Stub"))
    # boxes
    fill, fg, sfg, line = style(root, 0)
    draw_box(ctx, sl, n, s, MSO_SHAPE.RECTANGLE, cx - rw / 2, oy, rw, r0, f"ov_OCRoot{i}", root.label, root.sub, sz0,
             one0, f"{root.label!r} in part {i}", fill, fg, sfg, line, PP_ALIGN.CENTER, marg, prov=root.prov)
    for r, rw_ in enumerate(rows):
        for j, c in enumerate(rw_):
            bx = rowleft + j * (bw + gx)
            fill, fg, sfg, line = style(c, 1)
            draw_box(ctx, sl, n, s, MSO_SHAPE.RECTANGLE, bx, top_r[r], bw, cr, f"ov_OCBox{i}_{r + 1}_{j + 1}",
                     c.label, c.sub, sz1, one1, f"{c.label!r} in part {i}", fill, fg, sfg, line, PP_ALIGN.CENTER, marg, prov=c.prov)
            for g, gn in enumerate(c.kids):
                fill, fg, sfg, line = style(gn, 2)
                draw_box(ctx, sl, n, s, MSO_SHAPE.RECTANGLE, bx + ind, top_r[r] + cr + 0.14 + g * (gh + 0.08),
                         bw - ind, gh, f"ov_OCSub{i}_{r + 1}_{j + 1}_{g + 1}", gn.label, gn.sub, sz2, one2,
                         f"{gn.label!r} in part {i}", fill, fg, sfg, line, PP_ALIGN.LEFT,
                         (0.08, 0.02, 0.06, 0.02), 0.75, prov=gn.prov)


register("org_chart", p_org_chart, ("children",), _oc_check, min_h=3.0, archetypes=("org_chart",))
