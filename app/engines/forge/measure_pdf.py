"""Measure a reference deck delivered as PDF (public consulting decks), to calibrate rules.yaml thresholds.

    python -m engines.forge.measure_pdf deck.pdf [--pages 53-76] [--out m.json]

A PDF has no masters, placeholders or chart parts, so only the visible properties are measured: text density,
title length and form, source lines, type scale, fonts, left edges, images and vector drawings. Heuristics are
named as such in the output (`method`), and every metric carries an evidence ID.
"""
from __future__ import annotations

import json
import re
import statistics as st
from collections import Counter
from pathlib import Path

from .common import evidence_id, sha256
from .validate_pptx import _has_verb

EN_STOP = {"the", "and", "of", "to", "in", "is", "for", "on", "are", "with", "by", "that", "from", "as", "be"}
SRC = re.compile(r"^\s*(source|sources|kilde|kilder|note|notes)\s*:", re.I)


def _pages(spec: str | None, n: int) -> list[int]:
    if not spec:
        return list(range(n))
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a) - 1, int(b or a)))
    return [p for p in out if 0 <= p < n]


def _mode_share(vals, tol=None):
    """Share of pages that match the most common value (within tol inches for positions): 1.0 = fully consistent."""
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    if tol is None:
        return round(Counter(vals).most_common(1)[0][1] / len(vals), 2)
    best = max(sum(1 for v in vals if abs(v - c) <= tol) for c in vals)
    return round(best / len(vals), 2)


# ---- extended per-page measurement (opt-in) -------------------------------------------------------------------------
EMPTY_BAND = (0.20, 0.90)      # body band as fractions of the page height
EMPTY_LIGHT = 225              # grey level above which a pixel counts as empty (same as whitespace.page_empty)
EMPTY_SIDE = 0.04              # side margin skipped when looking for the largest empty block (same as P20)
MAX_DRAWINGS = 4000            # chart/table heuristics read at most this many drawings per page


EXT_NUMERIC = ["title_lines", "n_font_sizes", "color_count", "empty_share", "margin_left_in", "margin_right_in",
               "margin_top_in", "margin_bottom_in"]
EXT_METHOD = {
    "title_lines": "distinct baselines (clustered at 0.5 x font size) among the title spans",
    "n_font_sizes": "distinct font sizes rounded to whole points among all text spans on the page",
    "margins": "extents of text spans, visible drawings and images, clipped to the page; objects covering >=90% of the "
               "page (backgrounds) and white-only fills are ignored; text_margin_* uses text spans only; full-bleed bands "
               "and images make the all-content margins zero",
    "empty_share": "largest empty rectangle (whitespace.largest_empty) in the body band 20%-90% of page height, side margin 4%, "
                   "on a grey bitmap rendered at <=40 dpi (<=800 px wide); pixels >225/255 count as empty, so light-grey "
                   "panels count as empty as in P20; white space inside a plot counts as empty",
    "color_count": "distinct colours quantised to 4 bits per channel among text colours and drawing fill/stroke colours, "
                   "excluding near-white (min channel >=235) and near-black (max channel <=40); greys count; raster images ignored",
    "has_chart": "heuristic on vector drawings: >=4 filled rects with a common baseline (or left edge), similar thickness and "
                 ">=3 distinct lengths, or a stroke-only path with >=6 segments (>=4 non-axis-aligned) wider than 10% of the "
                 "page; and <=60 words inside the region. Raster charts are not counted (unknown); pies and donuts are missed",
    "has_table": "heuristic: >=3 parallel horizontal rules (>=35% of page width, same extent, >=6pt apart, body band), or a "
                 "text grid of >=3 aligned columns of short spans (<=6 words) together in >=4 rows. Borderless tables with "
                 "ragged cells and raster tables are missed",
}


def _rgb255(c):
    """pymupdf colour (None, gray, rgb, cmyk tuple of 0..1 floats) -> (r, g, b) 0..255 or None."""
    if c is None:
        return None
    c = tuple(c)
    if len(c) == 1:
        c = (c[0],) * 3
    elif len(c) == 4:
        k = c[3]
        c = tuple((1 - v) * (1 - k) for v in c[:3])
    if len(c) != 3:
        return None
    return tuple(max(0, min(255, int(round(v * 255)))) for v in c)


def _near_white(rgb, t=235):
    return min(rgb) >= t


def _near_black(rgb, t=40):
    return max(rgb) <= t


def _cluster(vals, tol):
    """Cluster sorted numbers: a new cluster starts when the gap to the previous value exceeds tol."""
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def _title_line_count(title_sp):
    ys = [s["origin"][1] for s in title_sp]
    if not ys:
        return 0
    size = max(s["size"] for s in title_sp)
    return len(_cluster(ys, 0.5 * size))


def _color_count(spans, dr):
    cols = set()
    for s in spans:
        v = s["color"]
        rgb = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        if not _near_white(rgb) and not _near_black(rgb):
            cols.add(tuple(x >> 4 for x in rgb))
    for d in dr:
        for key in ("fill", "color"):
            rgb = _rgb255(d.get(key))
            if rgb and not _near_white(rgb) and not _near_black(rgb):
                cols.add(tuple(x >> 4 for x in rgb))
    return len(cols)


def _margins(pg, spans, dr, W, H):
    """Extents of all text, visible drawings and images, clipped to the page. Full-page backgrounds (>=90% of the
    page area) are ignored."""
    boxes = [tuple(s["bbox"]) for s in spans]
    for d in dr:
        r = d.get("rect")
        if r is None or (d.get("fill") is None and d.get("color") is None):
            continue
        if d.get("fill") is not None and d.get("color") is None:
            rgb = _rgb255(d["fill"])
            if rgb and _near_white(rgb, 250):
                continue
        boxes.append((r.x0, r.y0, r.x1, r.y1))
    for im in pg.get_image_info():
        boxes.append(tuple(im["bbox"]))
    xs0, ys0, xs1, ys1 = [], [], [], []
    for x0, y0, x1, y1 in boxes:
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1 <= x0 and y1 <= y0:
            continue
        if (x1 - x0) * (y1 - y0) >= 0.9 * W * H:
            continue
        xs0.append(x0), ys0.append(y0), xs1.append(x1), ys1.append(y1)
    if not xs0:
        return {}
    tb = [b for b in (tuple(s["bbox"]) for s in spans)]
    left, right, top, bottom = min(xs0), W - max(xs1), min(ys0), H - max(ys1)
    out = {"margin_left_in": left / 72, "margin_right_in": right / 72, "margin_top_in": top / 72,
           "margin_bottom_in": bottom / 72, "margin_left_frac": left / W, "margin_right_frac": right / W,
           "margin_top_frac": top / H, "margin_bottom_frac": bottom / H}
    if tb:
        out.update({"text_margin_left_in": min(b[0] for b in tb) / 72, "text_margin_right_in": (W - max(b[2] for b in tb)) / 72,
                    "text_margin_top_in": min(b[1] for b in tb) / 72, "text_margin_bottom_in": (H - max(b[3] for b in tb)) / 72})
    return {k: round(v, 4) for k, v in out.items()}


def _empty_share(pg, W, H, max_dpi=40, max_px=800):
    """Largest empty rectangle in the body band as a share of the band, on a rendered grey bitmap."""
    import numpy as np
    from .whitespace import largest_empty
    dpi = min(max_dpi, max_px / (W / 72))
    pix = pg.get_pixmap(dpi=dpi, colorspace="gray", alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    h, w = img.shape
    y0, y1 = int(EMPTY_BAND[0] * h), int(EMPTY_BAND[1] * h)
    x0, x1 = int(EMPTY_SIDE * w), int((1 - EMPTY_SIDE) * w)
    body = img[y0:y1, x0:x1] > EMPTY_LIGHT
    if body.size == 0:
        return None
    area = largest_empty(body)[0]
    return round(area / body.size, 4)


def _words_in(spans, box):
    x0, y0, x1, y1 = box
    n = 0
    for s in spans:
        b = s["bbox"]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            n += len(s["text"].split())
    return n


def _has_chart(dr, spans, W, H):
    """Heuristic. True when vector drawings look like a chart: >=4 filled axis-aligned rects with a common baseline
    (or left edge) and varying length, or a stroked polyline with many non-axis-aligned segments, and <=60 words
    inside the region. Large raster images are never counted (unknown)."""
    rects, lines = [], []
    for d in dr[:MAX_DRAWINGS]:
        filled = d.get("fill") is not None
        fill = _rgb255(d.get("fill")) if filled else None
        for it in d["items"]:
            if it[0] in ("re", "qu") and filled and fill and not _near_white(fill, 245):
                r = it[1].rect if it[0] == "qu" else it[1]
                w, h = r.width, r.height
                if w >= 1.5 and h >= 1.5 and w * h < 0.2 * W * H:
                    rects.append((r.x0, r.y0, r.x1, r.y1))
        if not filled and d.get("color") is not None:
            segs = [it for it in d["items"] if it[0] in ("l", "c")]
            if len(segs) >= 6:
                r = d["rect"]
                skew = sum(1 for it in segs if it[0] == "c" or (abs(it[1].x - it[-1].x) > 0.5 and abs(it[1].y - it[-1].y) > 0.5))
                if r.width >= 0.1 * W and r.height >= 0.01 * H and skew >= 4:
                    lines.append((r.x0, r.y0, r.x1, r.y1))
    for rr in lines:
        if _words_in(spans, rr) <= 60:
            return True
    for axis in (0, 1):             # 0: vertical bars (common bottom), 1: horizontal bars (common left edge)
        key = (lambda b: b[3]) if axis == 0 else (lambda b: b[0])
        length = (lambda b: b[3] - b[1]) if axis == 0 else (lambda b: b[2] - b[0])
        thick = (lambda b: b[2] - b[0]) if axis == 0 else (lambda b: b[3] - b[1])
        order = sorted(rects, key=key)
        groups, cur = [], []
        for b in order:
            if cur and key(b) - key(cur[-1]) > 1.5:
                groups.append(cur)
                cur = []
            cur.append(b)
        if cur:
            groups.append(cur)
        for g in groups:
            if len(g) < 4:
                continue
            ls = [length(b) for b in g]
            ts = [thick(b) for b in g]
            if max(ts) > 1.6 * min(ts):          # bars share a thickness; mixed-size boxes are layout, not bars
                continue
            if len(_cluster(ls, max(1.5, 0.03 * max(ls)))) < 3 or (max(ls) - min(ls)) < 0.12 * max(ls):
                continue
            box = (min(b[0] for b in g), min(b[1] for b in g), max(b[2] for b in g), max(b[3] for b in g))
            if _words_in(spans, box) <= 60:
                return True
    return False


def _has_table(dr, spans, W, H):
    """Heuristic. True when there are >=3 parallel horizontal rules (>=35% of the page width, same extent, >=6pt
    apart, inside the body band), or a text grid: >=3 aligned columns of short spans (<=6 words) present together in
    >=4 rows (left or right aligned)."""
    ylo, yhi = 0.15 * H, 0.92 * H
    segs = []
    for d in dr[:MAX_DRAWINGS]:
        stroked = d.get("color") is not None
        for it in d["items"]:
            if it[0] == "l" and stroked and abs(it[1].y - it[2].y) <= 0.6:
                segs.append((min(it[1].x, it[2].x), max(it[1].x, it[2].x), (it[1].y + it[2].y) / 2))
            elif it[0] == "re":
                r = it[1]
                if r.height <= 2.5 and r.width >= 0.35 * W and (stroked or d.get("fill") is not None):
                    segs.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
                elif stroked and r.width >= 0.35 * W and r.height > 2.5:
                    segs += [(r.x0, r.x1, r.y0), (r.x0, r.x1, r.y1)]
    segs = [s for s in segs if s[1] - s[0] >= 0.35 * W and ylo <= s[2] <= yhi]
    for ext in _cluster([s[0] for s in segs], 6):
        grp = [s for s in segs if ext[0] - 6 <= s[0] <= ext[-1] + 6]
        for ext2 in _cluster([s[1] for s in grp], 6):
            ys = [s[2] for s in grp if ext2[0] - 6 <= s[1] <= ext2[-1] + 6]
            if len(_cluster(ys, 6)) >= 3:
                return True
    cells = [s for s in spans if len(s["text"].split()) <= 6 and ylo <= s["bbox"][1] <= yhi]
    for ix in (0, 2):
        cols = [c for c in _cluster([s["bbox"][ix] for s in cells], 3) if len(c) >= 4]
        if len(cols) < 3:
            continue
        rows = {}
        for ci, c in enumerate(cols):
            for s in cells:
                if c[0] - 3 <= s["bbox"][ix] <= c[-1] + 3:
                    rows.setdefault(round(s["bbox"][3] / 3), set()).add(ci)
        if sum(1 for v in rows.values() if len(v) >= 3) >= 4:
            return True
    return False


def _extended_page(pg, spans, title_sp, dr, W, H):
    out = {"page_w_in": round(W / 72, 3), "page_h_in": round(H / 72, 3), "title_lines": _title_line_count(title_sp),
           "n_font_sizes": len({round(s["size"]) for s in spans}), "color_count": _color_count(spans, dr)}
    out.update(_margins(pg, spans, dr, W, H))
    try:
        out["empty_share"] = _empty_share(pg, W, H)
    except Exception:                       # a page that does not render is left out of the empty-share statistic
        out["empty_share"] = None
    out["has_chart"] = _has_chart(dr, spans, W, H)
    out["has_table"] = _has_table(dr, spans, W, H)
    return out


def measure(path: str, pages: str | None = None, extended: bool = False) -> dict:
    try:
        import pymupdf as fitz
    except ImportError as e:
        raise RuntimeError("Measuring a PDF needs the optional PyMuPDF package (AGPL, not shipped): pip install pymupdf") from e
    doc = fitz.open(path)
    sha = sha256(path)
    per = []
    fonts, sizes = Counter(), {"title": Counter(), "body": Counter(), "footnote": Counter()}
    english_hits = total_words = 0
    for i in _pages(pages, doc.page_count):
        pg = doc[i]
        W, H = pg.rect.width, pg.rect.height
        spans = []
        for b in pg.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for s in ln.get("spans", []):
                    if s["text"].strip():
                        spans.append(s)
        if not spans:
            per.append({"page": i + 1, "words": 0, "empty": True})
            continue
        top = [s for s in spans if s["bbox"][1] < H * 0.22]
        tmax = max((s["size"] for s in top), default=0)
        title_sp = [s for s in top if s["size"] >= tmax * 0.85 and s["size"] >= 14] if tmax else []
        title = " ".join(s["text"].strip() for s in title_sp)
        words = 0
        lefts = set()
        src = False
        for s in spans:
            role = "title" if s in title_sp else ("footnote" if s["size"] <= 9.5 else "body")
            sizes[role][round(s["size"])] += 1
            fonts[re.sub(r"^[A-Z]{6}\+", "", s["font"]).split("-")[0].split(",")[0]] += len(s["text"])
            if role == "body":
                words += len(s["text"].split())
            lefts.add(round(s["bbox"][0] / 72 / 0.05) * 0.05)
            if s["bbox"][1] > H * 0.8 and SRC.match(s["text"]):
                src = True
        text = " ".join(s["text"] for s in spans).lower()
        toks = re.findall(r"[a-zæøå]+", text)
        english_hits += sum(1 for w in toks if w in EN_STOP)
        total_words += len(toks)
        area = sum(abs((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1])) for r in pg.get_image_info())
        raster = area > 0.3 * W * H
        all_words = sum(len(s["text"].split()) for s in spans)
        tsz = max((s["size"] for s in title_sp), default=None)
        ty = min((s["bbox"][1] for s in title_sp), default=None)
        tx = min((s["bbox"][0] for s in title_sp), default=None)
        tfont = re.sub(r"^[A-Z]{6}\+", "", title_sp[0]["font"]).split("-")[0] if title_sp else None
        dr = pg.get_drawings()
        entry = {"page": i + 1, "title_size": round(tsz, 1) if tsz else None,
                    "title_top_in": round(ty / 72, 2) if ty is not None else None,
                    "title_left_in": round(tx / 72, 2) if tx is not None else None, "title_font": tfont,
                    "raster_exhibit": raster, "all_words": all_words, "aspect": round(W / H, 3), "title": title[:200], "title_words": len(title.split()),
                    "title_has_verb": _has_verb(title) if title else None, "words": words,
                    "distinct_left_edges": len(lefts), "source_line": src, "images": len(pg.get_images()),
                    "drawings": len(dr)}
        if extended and title and (all_words >= 15 or raster):
            entry.update(_extended_page(pg, spans, title_sp, dr, W, H))
        per.append(entry)
    content = [p for p in per if not p.get("empty") and p["title_words"] and (p["all_words"] >= 15 or p["raster_exhibit"])]
    english = english_hits > 0.06 * max(1, total_words)

    def stat(key, pages_):
        xs = [p[key] for p in pages_ if p.get(key) is not None]
        if not xs:
            return None
        xs.sort()
        return {"median": st.median(xs), "mean": round(st.mean(xs), 1), "p90": xs[int(0.9 * (len(xs) - 1))],
                "max": xs[-1]}
    titled = [p for p in content if p["title_words"]]
    summary = {
        "pages": len(per), "content_pages": len(content), "language_english": english,
        "aspect": Counter(p.get("aspect") for p in per).most_common(1)[0][0] if per else None,
        "body_words": stat("words", content), "title_words": stat("title_words", titled),
        "title_with_verb_share": (round(sum(1 for p in titled if p["title_has_verb"]) / len(titled), 2)
                                  if titled and english else None),
        "raster_exhibit_share": round(sum(1 for p in content if p["raster_exhibit"]) / len(content), 2) if content else None,
        "source_line_share": round(sum(1 for p in content if p["source_line"]) / len(content), 2) if content else None,
        "distinct_left_edges": stat("distinct_left_edges", content),
        "images_per_page": stat("images", content), "drawings_per_page": stat("drawings", content),
        "fonts_top": [f for f, _ in fonts.most_common(4)],
        "title_font_mode_share": _mode_share([p["title_font"] for p in content]),
        "title_size_mode_share": _mode_share([p["title_size"] for p in content]),
        "title_top_mode_share": _mode_share([p["title_top_in"] for p in content], 0.05),
        "title_left_mode_share": _mode_share([p["title_left_in"] for p in content], 0.05),
        "title_sizes": [s for s, _ in sizes["title"].most_common(3)],
        "body_sizes": [s for s, _ in sizes["body"].most_common(4)],
        "footnote_sizes": [s for s, _ in sizes["footnote"].most_common(3)],
    }
    if extended:
        summary["extended"] = {k: stat(k, content) for k in EXT_NUMERIC}
        for k in ("has_chart", "has_table"):
            xs = [p[k] for p in content if k in p]
            summary["extended"][k + "_share"] = round(sum(xs) / len(xs), 2) if xs else None
    method = {"title": "largest spans (>=85% of max, >=14pt) in the top 22% of the page",
              "body_words": "words in spans that are not title and >9.5pt", "content_pages": "has a title and >=15 words of any size, or a raster exhibit covering >30% of the page",
              "raster_exhibit": "embedded images cover >30% of the page; text inside them (incl. source lines) is invisible to this measurement, so text metrics are lower bounds",
              "source_line": "span in bottom 20% starting 'Source:'/'Note:' (or Danish 'Kilde:')",
              "left_edges": "span x0 rounded to 0.05in", "verb": "lexicon + inflection heuristic, English only"}
    if extended:
        method.update(EXT_METHOD)
    return {"file": str(path), "sha256": sha, "page_range": pages, "summary": summary, "method": method,
            "evidence": {k: evidence_id(sha, "pdf_" + k) for k in summary}, "pages": per}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages")
    ap.add_argument("--out")
    ap.add_argument("--extended", action="store_true", help="add title lines, margins, empty share, colours, chart/table flags")
    a = ap.parse_args()
    r = measure(a.pdf, a.pages, extended=a.extended)
    txt = json.dumps(r, indent=1)
    if a.out:
        Path(a.out).write_text(txt)
    print(json.dumps({"file": r["file"], "summary": r["summary"]}, indent=1))
