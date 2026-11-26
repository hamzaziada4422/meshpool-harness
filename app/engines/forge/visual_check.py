"""Visual checks a reader catches at a glance and the older rules did not (Daniel, 0.0.19: the QA loop said "all 19
passed" over a "Target 47%" label sitting on a bar, an empty "What it involves" column, a footnote starting
"months is the survey median...", a title wrapping to a lone "31", a source line cut to "... and others" and a
"Preliminary" stamp nobody asked for).

    failures = check(path, brief)          # rules P23-P27; deterministic, no LibreOffice needed

P23 chart labels clear of the bars: a text box drawn over a chart never sits on one of its bars.
P24 no empty column, box or placeholder: a column heading with nothing under it, a table column with no values, an
    empty text placeholder.
P25 footnotes and source lines are whole: a footnote never starts mid-sentence (lower case), a source line never ends
    "and others" or with an open parenthesis.
P26 titles wrap without a lone last word.
P27 no status stamp ("Preliminary", "Draft") the brief did not ask for (checked when the brief is known).

Round 2 (Daniel, the release candidate): what was still visible after those fixes.
P24 also fails a row of a grid or table left blank under a column the other rows fill ("What it involves" had text
    in rows 1 and 2, nothing in rows 3 and 4).
P30 source lines name the real source, once: no "the project fact base", "dossier" or fact ids in a source line or
    footnote, and no footnote that only repeats the source line.
P31 decorations, brackets and heads keep clear of the content: a pointer chevron squeezed against a panel, a
    difference bracket or its head within 0.1 in of a value label, a column head that wraps up into the section
    number or over the title rule.
P32 a two-column cover (title left, sections or figures right) spans at least 60% of its field.
P29 (nothing invented: a number, date or name the sources and the request do not give) is in grounded.py.
"""
from __future__ import annotations

import re

from .common import failure as F

EMU = 914400
NAMES = {
    "P23": "Chart labels clear of the bars",
    "P24": "No empty column, box or placeholder",
    "P25": "Footnotes and source lines are whole sentences",
    "P26": "Titles wrap without a lone last word",
    "P27": "No stamp the brief did not ask for",
    "P28": "The brand the request asks for (footer, colours, fonts)",
    "P29": "Nothing on the slides the sources or the request do not state",
    "P30": "Source lines name the real source, once",
    "P31": "Decorations, brackets and heads keep clear of the content",
    "P32": "The cover fills its page",
}
RULES = list(NAMES)


def _rect(sh):
    if sh.left is None or sh.width is None or sh.top is None or sh.height is None:
        return None
    return (sh.left / EMU, sh.top / EMU, (sh.left + sh.width) / EMU, (sh.top + sh.height) / EMU)


def _inter(a, b) -> tuple[float, float]:
    return min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])


def _float(el, path, ns) -> float | None:
    x = el.find(path, ns)
    try:
        return float(x.get("val")) if x is not None else None
    except (TypeError, ValueError):
        return None


C_NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}


def _bars(gf) -> list[tuple]:
    """Bar rectangles (x0, y0, x1, y1, label) of a clustered column chart whose plot area and value axis are fixed
    (the forge's annotated charts). Charts laid out by PowerPoint itself are skipped: their geometry is not known."""
    try:
        ch = gf.chart
        cs = ch._chartSpace
    except Exception:  # noqa: BLE001
        return []
    bar = cs.find(".//c:plotArea/c:barChart", C_NS)
    if bar is None or bar.find("c:barDir", C_NS) is None or bar.find("c:barDir", C_NS).get("val") != "col":
        return []
    grouping = bar.find("c:grouping", C_NS)
    if grouping is not None and grouping.get("val") not in ("clustered", None):
        return []
    ml = cs.find(".//c:plotArea/c:layout/c:manualLayout", C_NS)
    if ml is None:
        return []
    fx, fy, fw, fh = (_float(ml, f"c:{k}", C_NS) for k in ("x", "y", "w", "h"))
    if None in (fx, fy, fw, fh):
        return []
    sc = cs.find(".//c:valAx/c:scaling", C_NS)
    mx = _float(sc, "c:max", C_NS) if sc is not None else None
    mn = (_float(sc, "c:min", C_NS) if sc is not None else None) or 0.0
    if mx is None or mx <= mn:
        return []
    gap = _float(bar, "c:gapWidth", C_NS)
    gap = 150.0 if gap is None else gap
    x, y, w, h = gf.left / EMU, gf.top / EMU, gf.width / EMU, gf.height / EMU
    px, py, pw, ph = x + w * fx, y + h * fy, w * fw, h * fh
    try:
        series = list(ch.plots[0].series)
        cats = list(ch.plots[0].categories)
    except Exception:  # noqa: BLE001
        return []
    if not cats or not series:
        return []
    band = pw / len(cats)
    group = band / (1 + gap / 100)
    bw = group / len(series)
    yv = lambda v: py + ph * (1 - (v - mn) / (mx - mn))   # noqa: E731
    out = []
    for k, se in enumerate(series):
        for j, v in enumerate(se.values):
            if v is None or j >= len(cats):
                continue
            x0 = px + band * j + (band - group) / 2 + k * bw
            out.append((x0, yv(max(v, 0.0)), x0 + bw, yv(min(v, 0.0)), str(cats[j])))
    return out


def _p23(n, sl, out):
    frames = [sh for sh in sl.shapes if getattr(sh, "has_chart", False) and sh.has_chart]
    if not frames:
        return
    bars = [b for gf in frames for b in _bars(gf)]
    if not bars:
        return
    hits = []
    for sh in sl.shapes:
        if not sh.has_text_frame or not sh.text_frame.text.strip() or sh.name.startswith("ov_BarMark"):
            continue
        r = _rect(sh)
        if r is None:
            continue
        for b in bars:
            ix, iy = _inter(r, b)
            if ix > 0.04 and iy > 0.04:
                hits.append(f"'{sh.text_frame.text.strip()[:24]}' on the {b[4]} bar")
                break
    if hits:
        out.append(F("P23", "label_on_bar", f"Slide {n}: a label sits on a bar of the chart: {'; '.join(hits[:3])}. "
                     "Move the label clear of the bars.", slide=n, measured=len(hits), fix_scope="renderer"))


def _grid_gaps(sl, texts) -> list[str]:
    """Rows of a grid drawn as text boxes (the next steps: numbered rows under column heads) whose cell under a filled
    column is empty. Daniel, round 2: rows 3 and 4 of "What it involves" were blank while rows 1 and 2 had text, and
    the column check passed because the column was not empty as a whole."""
    heads = [(sh, r) for sh, r in texts if sh.name.startswith("role_subtitle_Head")]
    rows = sorted((r for sh, r in texts if re.fullmatch(r"role_numeral_\d+", sh.name)), key=lambda r: r[1])
    if len(heads) < 2 or len(rows) < 2:
        return []
    cells = [(sh, r) for sh, r in texts if not sh.name.startswith(("role_subtitle_Head", "role_numeral_", "ov_Tag"))
             and sh.name not in ("Firm", "Source", "Footnotes")]
    out = []
    for hs, hr in heads:
        filled = []
        for rr in rows:
            filled.append(any(hr[0] - 0.05 <= (q[0] + q[2]) / 2 <= hr[2] + 0.05
                              and rr[1] - 0.05 <= (q[1] + q[3]) / 2 <= rr[3] + 0.05 for _, q in cells))
        if any(filled) and not all(filled):
            gone = [str(i + 1) for i, f in enumerate(filled) if not f]
            out.append(f"the column '{hs.text_frame.text.strip()[:30]}' has no text in row{'s' if len(gone) > 1 else ''} "
                       f"{', '.join(gone)}")
    return out


def _p24(n, sl, out):
    texts = [(sh, _rect(sh)) for sh in sl.shapes if sh.has_text_frame and sh.text_frame.text.strip() and _rect(sh)]
    empty = []
    empty += _grid_gaps(sl, texts)
    for sh, r in texts:                                   # a column heading with nothing under it
        if not sh.name.startswith("role_subtitle_Head"):
            continue
        under = [o for o, q in texts if o is not sh and not o.name.startswith(("role_subtitle_Head", "ov_Tag"))
                 and o.name not in ("Firm", "Source", "Footnotes") and q[1] >= r[3] - 0.02
                 and r[0] - 0.05 <= (q[0] + q[2]) / 2 <= r[2] + 0.05]
        if not under:
            empty.append(f"the column '{sh.text_frame.text.strip()[:30]}' is empty")
    for sh in sl.shapes:                                  # a table column without a value
        if getattr(sh, "has_table", False) and sh.has_table:
            tb = sh.table
            rows = list(tb.rows)
            if len(rows) < 2:
                continue
            for ci in range(len(tb.columns)):
                head = tb.cell(0, ci).text.strip()
                body = [ri for ri in range(1, len(rows)) if tb.cell(ri, 0).text.strip() or ci == 0]
                full = [ri for ri in body if tb.cell(ri, ci).text.strip()]
                if head and not any(tb.cell(ri, ci).text.strip() for ri in range(1, len(rows))):
                    empty.append(f"the table column '{head[:30]}' is empty")
                elif head and ci and full and len(full) < len(body):   # a column filled in some rows, with
                    gone = [str(ri) for ri in body if ri not in full]                        # holes in it
                    empty.append(f"the table column '{head[:30]}' has empty cells in row"
                                 f"{'s' if len(gone) > 1 else ''} {', '.join(gone)}")
    for sh in sl.placeholders:                            # an empty placeholder ("Click to add text")
        try:
            t = sh.placeholder_format.type
        except Exception:  # noqa: BLE001
            continue
        name = str(t).split(".")[-1].split(" ")[0].upper()
        if sh.has_text_frame and not sh.text_frame.text.strip() and not any(
                k in name for k in ("SLIDE_NUMBER", "DATE", "FOOTER", "PICTURE", "CHART", "TABLE", "MEDIA")):
            empty.append(f"a {name.lower().replace('_', ' ')} placeholder is empty")
    if empty:
        out.append(F("P24", "empty_box", f"Slide {n}: {'; '.join(empty[:3])}. Fill it or drop it.", slide=n,
                     measured=len(empty), fix_scope="renderer"))


_LABEL = re.compile(r"^\s*(?:\d{1,2}\u00a0\u00a0|\d{1,2}\.\s\s|[\u00b9\u00b2\u00b3\u2074-\u2079]+\s*)")   # the note numbers the renderer writes


def _p25(n, sl, out):
    bad = []
    for sh in sl.shapes:
        if not sh.has_text_frame:
            continue
        if sh.name == "Footnotes":
            for p in sh.text_frame.paragraphs:
                for part in re.split(r"\s{4,}", p.text):      # notes side by side on one line
                    t = _LABEL.sub("", part).strip()
                    if t and (t[0].islower() or t[0] in ",;:)"):
                        bad.append(f"the footnote '{t[:40]}' starts mid-sentence")
        elif sh.name == "Source":
            t = sh.text_frame.text.strip()
            if re.search(r"(?i)\band others\s*$", t) or t.count("(") > t.count(")"):
                bad.append(f"the source line '... {t[-40:]}' is cut short")
    if bad:
        out.append(F("P25", "cut_sentence", f"Slide {n}: {'; '.join(bad[:3])}. Write it as a whole sentence.", slide=n,
                     measured=len(bad), fix_scope="renderer"))


def _lines(text: str, width: float, size: float, path) -> list[str]:
    from .common import text_width_in
    out = []
    for para in re.split(r"[\n\v\x0b]", text):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if not cur or text_width_in(trial, size, path) <= width * 0.97:
                cur = trial
            else:
                out.append(cur)
                cur = word
        if cur:
            out.append(cur)
    return out


def _p26(n, sl, out, face):
    from .common import font_file
    t = sl.shapes.title if sl.shapes.title is not None else None
    if t is None or not t.has_text_frame or not t.text_frame.text.strip():
        return
    sizes = [r.font.size.pt for p in t.text_frame.paragraphs for r in p.runs if r.font.size is not None]
    size = max(sizes) if sizes else 25.0
    try:
        from .render import METRIC
        path = font_file(face, True, False, METRIC)[0]
    except Exception:  # noqa: BLE001
        return
    tf = t.text_frame
    ml = (tf.margin_left if tf.margin_left is not None else 91440) / EMU
    mr = (tf.margin_right if tf.margin_right is not None else 91440) / EMU
    width = t.width / EMU - ml - mr
    text = "\n".join(p.text for p in tf.paragraphs)
    lines = _lines(text, width, size, path)
    if len(lines) >= 2 and len(lines[-1].split()) == 1 and len(text.split()) >= 4:
        out.append(F("P26", "orphan_word", f"Slide {n}: the title wraps and leaves '{lines[-1]}' alone on its last "
                     "line. Rebalance the lines or shorten the title.", slide=n, fix_scope="renderer"))


# ------------------------------------------------------------------ round 2 (Daniel): what still showed
def _vis_rect(sh):
    """The rectangle a shape covers on the page: a shape turned 90 or 270 degrees swaps its width and height."""
    r = _rect(sh)
    if r is None:
        return None
    rot = round(getattr(sh, "rotation", 0.0) or 0.0) % 180
    if rot == 90:
        cx, cy, w, h = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, r[2] - r[0], r[3] - r[1]
        return (cx - h / 2, cy - w / 2, cx + h / 2, cy + w / 2)
    return r


def _grow(r, d):
    return (r[0] - d, r[1] - d, r[2] + d, r[3] + d)


def _hits(a, b, tol=0.0) -> bool:
    ix, iy = _inter(a, b)
    return ix > tol and iy > tol


def _extent(sh, faces: dict):
    """Where a text box's text really sits (its box's width, the height of its wrapped lines), placed by its vertical
    anchor: text that needs more lines than the box holds grows out of the box (up, for a bottom-anchored head)."""
    from .common import font_file, text_width_in  # noqa: F401
    from .render import METRIC
    r = _rect(sh)
    if r is None or not sh.has_text_frame:
        return None
    tf = sh.text_frame
    m = lambda v, d: (v if v is not None else d) / EMU   # noqa: E731
    ml, mr, mt, mb = m(tf.margin_left, 91440), m(tf.margin_right, 91440), m(tf.margin_top, 45720), m(tf.margin_bottom, 45720)
    width = max(0.1, r[2] - r[0] - ml - mr)
    wrap = tf.word_wrap is not False
    h = 0.0
    for p in tf.paragraphs:
        runs = list(p.runs)
        if not p.text.strip():
            continue
        size = max([ru.font.size.pt for ru in runs if ru.font.size is not None] or [18.0])
        bold = any(ru.font.bold for ru in runs)
        lat = [ru._r.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}latin") for ru in runs]
        major = any(x is not None and str(x.get("typeface", "")).startswith("+mj") for x in lat)
        face = faces.get("major" if major else "minor") or "Segoe UI"
        try:
            path = font_file(face, bold, False, METRIC)[0]
        except Exception:  # noqa: BLE001
            path = None
        n = len(_lines(p.text, width, size, path)) if wrap else 1
        h += max(1, n) * size * 1.2 / 72
    h += mt + mb
    try:
        anchor = str(tf.vertical_anchor or "").upper()
    except Exception:  # noqa: BLE001
        anchor = ""
    if "BOTTOM" in anchor:
        return (r[0], r[3] - h, r[2], r[3])
    if "MIDDLE" in anchor:
        c = (r[1] + r[3]) / 2
        return (r[0], c - h / 2, r[2], c + h / 2)
    return (r[0], r[1], r[2], r[1] + h)


def _faces(path) -> dict:
    try:
        from .requested import _theme
        return _theme(path)[1]
    except Exception:  # noqa: BLE001
        return {}


_INTERNAL = re.compile(r"(?i)\b(?:project |internal |our )?fact ?base\b|\bfacts? (?:list|sheet|file|ids?)\b|\bdossier\b|"
                       r"\bstory ?(?:line|board)\b|\bprovenance\b|\bresearch notes\b|\bthe facts\b|"
                       r"\b(?:F|C|W|O)\d{1,3}\b|\bS\d{1,3}[CO]\d{1,3}\b")


def _plain(t: str) -> str:
    t = re.sub(r"(?i)^\s*(?:sources?|notes?)\s*:\s*", "", str(t or ""))
    t = re.sub(r"^\s*(?:\d{1,2}\.?\s+|[¹²³⁴-⁹]+\s*)", "", t)
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def same_source(note: str, source: str) -> bool:
    """A footnote that only repeats the source line (Daniel, round 2: slide 7 printed "Northfield Research, Industry
    Benchmark Report 2025; economics modelled from 37 case studies" twice, as a note and as the source)."""
    a, b = _plain(note), _plain(source)
    if not a or not b:
        return False
    if a == b or (len(a) >= 20 and a in b):
        return True
    wa, wb = set(a.split()), set(b.split())
    return len(wa) >= 4 and len(wa & wb) / len(wa) >= 0.9


def _p30(n, sl, out):
    bad = []
    src = next((sh.text_frame.text.strip() for sh in sl.shapes if sh.has_text_frame and sh.name == "Source"), "")
    for sh in sl.shapes:
        if not sh.has_text_frame or sh.name not in ("Source", "Footnotes"):
            continue
        t = sh.text_frame.text.strip()
        m = _INTERNAL.search(t)
        if m:
            bad.append(f"the {'source line' if sh.name == 'Source' else 'footnote'} says '{m.group(0)}', a word for the "
                       "working files, not the source: name the report or file")
        if sh.name == "Footnotes" and src:
            for p in sh.text_frame.paragraphs:
                for part in re.split(r"\s{4,}", p.text):
                    if part.strip() and same_source(part, src):
                        bad.append(f"the footnote '{part.strip()[:40]}' repeats the source line")
    if bad:
        out.append(F("P30", "source_words", f"Slide {n}: {'; '.join(bad[:3])}.", slide=n, measured=len(bad),
                     fix_scope="renderer"))


_DECOR = re.compile(r"^ov_(?:FlowPointer|Chevron|Pointer(?!Mark))")
_BRACKET = ("ov_DeltaArrow", "ov_Head_Delta", "ov_DeltaLabel", "ov_CagrArrow", "ov_Head_Cagr", "ov_CagrLabel",
            "ov_TargetLabel")
CLEAR = 0.06          # inches a decoration keeps from the content it sits beside
BRACKET_CLEAR = 0.1   # and a bracket, its arrow head or its label from a value label (0.08 in read as touching)


def _content(sl, skip=()):
    out = []
    for sh in sl.shapes:
        if sh in skip or _DECOR.match(sh.name or "") or (sh.name or "").startswith(("ov_PointerMark",) + _BRACKET):
            continue
        try:
            pic = sh.shape_type == 13
        except Exception:  # noqa: BLE001
            pic = False
        has = (sh.has_text_frame and sh.text_frame.text.strip()) or (getattr(sh, "has_chart", False) and sh.has_chart) \
            or (getattr(sh, "has_table", False) and sh.has_table) or pic
        r = _rect(sh)
        if has and r is not None:
            out.append((sh, r))
    return out


def _labels(gf) -> list[tuple]:
    """Value labels a column chart prints above its bars (outside end): (x0, y0, x1, y1, text)."""
    try:
        cs = gf.chart._chartSpace
    except Exception:  # noqa: BLE001
        return []
    dl = cs.find(".//c:ser/c:dLbls", C_NS)
    if dl is None:
        return []
    show = dl.find("c:showVal", C_NS)
    pos = dl.find("c:dLblPos", C_NS)
    if show is None or show.get("val") not in ("1", "true") or (pos is not None and pos.get("val") != "outEnd"):
        return []
    sz = dl.find(".//a:defRPr", {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"})
    size = int(sz.get("sz")) / 100 if sz is not None and sz.get("sz") else 10.0
    h = size * 1.2 / 72
    vals = []
    try:
        vals = [v for se in gf.chart.plots[0].series for v in se.values]
    except Exception:  # noqa: BLE001
        pass
    out = []
    for k, b in enumerate(_bars(gf)):
        v = vals[k] if k < len(vals) else None
        if v is None or v < 0:
            continue
        txt = f"{v:,.1f}".rstrip("0").rstrip(".")
        w = max(0.25, len(txt) * size * 0.6 / 72 + 0.06)
        cx = (b[0] + b[2]) / 2
        out.append((cx - w / 2, b[1] - 0.03 - h, cx + w / 2, b[1] - 0.03, txt))
    return out


def _p31(n, sl, out, faces):
    bad = []
    content = _content(sl)
    for sh in sl.shapes:                                  # a pointer or chevron squeezed against a panel
        if not _DECOR.match(sh.name or ""):
            continue
        d = _vis_rect(sh)
        hit = next((o for o, r in content if d is not None and _hits(_grow(d, CLEAR), r)), None)
        if hit is not None:
            what = hit.text_frame.text.strip()[:24] if hit.has_text_frame and hit.text_frame.text.strip() else \
                ("the chart" if getattr(hit, "has_chart", False) and hit.has_chart else hit.name)
            bad.append(f"the decoration {sh.name} sits on or against '{what}'")
    labels = [lb for gf in sl.shapes if getattr(gf, "has_chart", False) and gf.has_chart for lb in _labels(gf)]
    for sh in sl.shapes:                                  # a bracket, arrow head or its label against a value label
        if not (sh.name or "").startswith(_BRACKET):
            continue
        d = _vis_rect(sh)
        lb = next((lb for lb in labels if d is not None and _hits(_grow(d, BRACKET_CLEAR), lb[:4], -0.001)), None)
        if lb is not None:
            bad.append(f"{'the bracket' if 'Label' not in sh.name else 'the label'} "
                       f"{('' + sh.text_frame.text.strip()[:24] + ' ') if sh.has_text_frame and sh.text_frame.text.strip() else ''}"
                       f"touches the value label '{lb[4]}'")
    sec = next((sh for sh in sl.shapes if sh.name == "ov_SectionNo" and sh.has_text_frame), None)
    rule = next((sh for sh in sl.shapes if sh.name == "TitleRule"), None)
    rule_y = rule.top / EMU if rule is not None else None
    for sh in sl.shapes:                                  # text that grows out of its box into the section number
        if not sh.has_text_frame or not sh.text_frame.text.strip() or sh is sec or sh == sl.shapes.title:
            continue
        r = _rect(sh)
        if r is None or (rule_y is not None and r[1] < rule_y - 0.02):
            continue
        e = _extent(sh, faces)
        if e is None:
            continue
        if sec is not None and _hits(e, _rect(sec), 0.02):
            bad.append(f"'{sh.text_frame.text.strip()[:24]}' wraps up into the section number "
                       f"'{sec.text_frame.text.strip()}'")
        elif rule_y is not None and e[1] < rule_y - 0.04:
            bad.append(f"'{sh.text_frame.text.strip()[:24]}' wraps up over the title rule")
    if bad:
        out.append(F("P31", "crowded", f"Slide {n}: {'; '.join(list(dict.fromkeys(bad))[:3])}. Give it room or drop it.",
                     slide=n, measured=len(bad), fix_scope="renderer"))


COVER_FILL = 0.6      # the share of a two-column cover's field its content spans, top to bottom


def _p32(n, sl, out, faces):
    """A two-column cover (title on the left, the sections or figures on the right) fills its field: Daniel, round
    2, saw the content held in the middle half of the navy page, the top quarter empty."""
    names = [sh.name or "" for sh in sl.shapes]
    if not any(x.startswith(("ov_CoverSec", "role_numeral_CoverMetric")) for x in names):
        return
    rule = next((sh for sh in sl.shapes if sh.name == "CoverRule"), None)
    bottom = rule.top / EMU if rule is not None else 6.5
    ext = [e for sh in sl.shapes if sh.has_text_frame and sh.text_frame.text.strip() and sh.name not in ("Date", "Firm")
           for e in [_extent(sh, faces)] if e is not None and e[1] < bottom]
    ext += [r for sh in sl.shapes if (sh.name or "").startswith(("ov_CoverSecRule", "CoverMetricRule", "CoverBar"))
            for r in [_rect(sh)] if r is not None and r[1] < bottom]          # the list's hairlines and the bar
    if not ext:
        return
    top_, bot_ = min(e[1] for e in ext), max(e[3] for e in ext)
    share = (bot_ - top_) / bottom
    if share < COVER_FILL:
        out.append(F("P32", "empty_cover", f"Slide {n}: the cover's content spans {share:.0%} of its field "
                     f"({top_:.1f} in to {bot_:.1f} in of {bottom:.1f} in); the rest is empty. Spread the title "
                     "block and the list over the page.", slide=n, measured=round(share, 2), limit=COVER_FILL,
                     fix_scope="renderer"))


def check(path: str, brief: dict | None = None) -> list[dict]:
    """Failures for rules P23-P27 (P27 only when the brief says which stamps it allows)."""
    try:
        from pptx import Presentation
        prs = Presentation(path)
    except Exception as e:  # noqa: BLE001
        return [F(r, "visual", f"The visual checks could not read the deck ({type(e).__name__}).", status="not_run",
                  severity="info", fix_scope="renderer") for r in ("P23", "P24", "P25", "P26", "P27")]
    try:
        from .requested import _theme
        face = _theme(path)[1].get("major") or "Segoe UI"
    except Exception:  # noqa: BLE001
        face = "Segoe UI"
    out: list[dict] = []
    stamps = (brief or {}).get("stamps")
    from .requested import is_stamp, stamp_ok
    faces = _faces(path)
    for n, sl in enumerate(prs.slides, 1):
        for fn in (_p23, _p24, _p25, _p30):
            try:
                fn(n, sl, out)
            except Exception:  # noqa: BLE001 - a check that cannot read a shape never breaks validation
                pass
        try:
            _p26(n, sl, out, face)
        except Exception:  # noqa: BLE001
            pass
        for fn in (_p31, _p32):
            try:
                fn(n, sl, out, faces)
            except Exception:  # noqa: BLE001
                pass
        if stamps is not None:
            bad = [sh.text_frame.text.strip() for sh in sl.shapes if sh.has_text_frame and sh.name.startswith("ov_Tag")
                   and is_stamp(sh.text_frame.text) and not stamp_ok(sh.text_frame.text, set(stamps))]
            if bad:
                out.append(F("P27", "stray_stamp", f"Slide {n}: the stamp '{bad[0]}' was not asked for in the brief. "
                             "Remove it.", slide=n, fix_scope="renderer"))
    if stamps is None:
        out.append(F("P27", "stray_stamp", "No brief given; stamps were not checked.", status="not_run",
                     severity="info", fix_scope="renderer"))
    return out
