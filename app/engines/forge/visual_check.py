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


def _p24(n, sl, out):
    texts = [(sh, _rect(sh)) for sh in sl.shapes if sh.has_text_frame and sh.text_frame.text.strip() and _rect(sh)]
    empty = []
    for sh, r in texts:                                   # a column heading with nothing under it
        if not sh.name.startswith("role_subtitle_Head"):
            continue
        under = [o for o, q in texts if o is not sh and not o.name.startswith(("role_subtitle_Head", "ov_Tag"))
                 and o.name not in ("Firm", "Source", "Footnotes") and q[1] >= r[3] - 0.02
                 and r[0] - 0.05 <= (q[0] + q[2]) / 2 <= r[2] + 0.05]
        if not under:
            empty.append(f"the column '{sh.text_frame.text.strip()[:30]}'")
    for sh in sl.shapes:                                  # a table column without a value
        if getattr(sh, "has_table", False) and sh.has_table:
            tb = sh.table
            rows = list(tb.rows)
            if len(rows) < 2:
                continue
            for ci in range(len(tb.columns)):
                head = tb.cell(0, ci).text.strip()
                if head and not any(tb.cell(ri, ci).text.strip() for ri in range(1, len(rows))):
                    empty.append(f"the table column '{head[:30]}'")
    for sh in sl.placeholders:                            # an empty placeholder ("Click to add text")
        try:
            t = sh.placeholder_format.type
        except Exception:  # noqa: BLE001
            continue
        name = str(t).split(".")[-1].split(" ")[0].upper()
        if sh.has_text_frame and not sh.text_frame.text.strip() and not any(
                k in name for k in ("SLIDE_NUMBER", "DATE", "FOOTER", "PICTURE", "CHART", "TABLE", "MEDIA")):
            empty.append(f"an empty {name.lower().replace('_', ' ')} placeholder")
    if empty:
        out.append(F("P24", "empty_box", f"Slide {n}: {'; '.join(empty[:3])} is empty. Fill it or drop it.", slide=n,
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
    for n, sl in enumerate(prs.slides, 1):
        for fn in (_p23, _p24, _p25):
            try:
                fn(n, sl, out)
            except Exception:  # noqa: BLE001 - a check that cannot read a shape never breaks validation
                pass
        try:
            _p26(n, sl, out, face)
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
