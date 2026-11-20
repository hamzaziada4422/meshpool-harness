"""The slide knowledge base: a private library of slide items and reference measurements, read from MESHPOOL_KB.

The knowledge base is private study material (reference decks and free template packs). It lives outside the repo
and is never committed. This module reads the folder named by the MESHPOOL_KB environment variable and nothing else:

    MESHPOOL_KB/library/components.db     items (id, scope, category, tier, ...) and an FTS5 index (the original table;
                                          never rewritten, except that `ingest` adds rows of its own group)
    MESHPOOL_KB/library/labels.sqlite     what this module adds: quality scores, vision labels, features, the golden set
    MESHPOOL_KB/library/golden.jsonl      the golden set (best items, near duplicates removed)
    MESHPOOL_KB/thumbs/<id>.webp          one thumbnail per item

When MESHPOOL_KB is unset or the folder is missing, every call returns an empty result and Forge behaves exactly as
without it; the log says once that the knowledge base is off. Items cut from the user's own uploads (group
`examples_uploaded`) are left out of every result unless a call asks for them.

    python -m engines.forge.kb search "process flow 5 steps" --limit 5
    python -m engines.forge.kb score | labels FILE.jsonl... | golden | audit | similar ID | priors ARCHETYPE
    python -m engines.forge.kb ingest FOLDER_OF_PPTX
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("forge.kb")
_WARNED = {"off": False}
UPLOADS = "examples_uploaded"
GOOD_TIERS = ("consulting_grade", "business")
_STOP = {"a", "an", "the", "of", "for", "and", "or", "with", "in", "on", "to", "slide", "slides"}
PLACEHOLDER = re.compile(r"(?i)lorem ipsum|click to (add|edit)|your (text|title|company|logo)|\b(insert|add) (text|title)"
                         r"|\[[^\]]{0,40}\]|\bxx+\b|text here|title here|sample text")

# Forge archetype -> knowledge base categories that show the same kind of page (for priors and lookups)
ARCH_CATEGORIES = {
    "executive_summary": ["executive_summary", "text_layout"], "agenda": ["agenda_toc"], "cover": ["cover_title"],
    "section_divider": ["section_divider"], "closing": ["closing_contact", "timeline_roadmap"],
    "bar_takeaway": ["chart"], "column_trend": ["chart"], "line_trend": ["chart"], "stacked_composition": ["chart"],
    "waterfall_bridge": ["chart"], "football_field": ["chart"], "benchmark": ["chart", "comparison"],
    "table_focus": ["table"], "table_highlight": ["table"], "challenge_matrix": ["comparison", "table"],
    "text_matrix": ["comparison", "table"], "exhibit_argument": ["chart", "text_layout"],
    "kpi_tiles": ["kpi_metrics"], "process_steps": ["process_flow"], "timeline_roadmap": ["timeline_roadmap"],
    "gantt": ["gantt", "timeline_roadmap"], "gantt_plan": ["gantt", "timeline_roadmap"],
    "matrix_2x2": ["matrix_framework"], "risk_matrix": ["risk_matrix", "matrix_framework"],
    "matrix_3x3": ["matrix_framework", "risk_matrix"], "issue_tree": ["issue_tree", "hierarchy_org"],
    "org_chart": ["hierarchy_org"], "cycle": ["cycle"], "hub_spoke": ["hub_spoke", "cycle"],
    "pyramid": ["pyramid_funnel"], "funnel": ["pyramid_funnel"], "venn": ["venn_overlap"],
    "quote_voice": ["quote_callout"], "options_evaluation": ["comparison"], "grid": ["comparison", "kpi_metrics"],
}


def root() -> Path | None:
    """The knowledge base folder, or None when MESHPOOL_KB is unset or missing."""
    p = os.environ.get("MESHPOOL_KB", "").strip()
    if not p:
        return None
    r = Path(p).expanduser()
    return r if (r / "library" / "components.db").is_file() else None


def available() -> bool:
    """True when the knowledge base is set up. Logs once when it is off."""
    if root() is not None:
        return True
    if not _WARNED["off"]:
        _WARNED["off"] = True
        log.info("slide knowledge base off: MESHPOOL_KB is not set or has no library/components.db")
    return False


def _labels_path(r: Path) -> Path:
    return r / "library" / "labels.sqlite"


def connect(write: bool = False) -> sqlite3.Connection | None:
    """components.db read-only, with labels.sqlite attached as `lab` (created on write)."""
    r = root()
    if r is None:
        return None
    con = sqlite3.connect(f"file:{r / 'library' / 'components.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    lp = _labels_path(r)
    if write or lp.exists():
        con.execute("ATTACH DATABASE ? AS lab", (str(lp),))
        _ensure_labels(con)
    return con


def _ensure_labels(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS lab.scores(id TEXT PRIMARY KEY, score REAL, parts TEXT);
        CREATE TABLE IF NOT EXISTS lab.labels(id TEXT PRIMARY KEY, category TEXT, subcategory TEXT, descr TEXT,
            quality INT, usable INT, style TEXT, source TEXT);
        CREATE TABLE IF NOT EXISTS lab.features(id TEXT PRIMARY KEY, dhash TEXT, vec TEXT);
        CREATE TABLE IF NOT EXISTS lab.golden(id TEXT PRIMARY KEY, rank INT);
        CREATE TABLE IF NOT EXISTS lab.ingested(id TEXT PRIMARY KEY, grp TEXT, scope TEXT, category TEXT,
            subcategory TEXT, source TEXT, source_file TEXT, slide_no INT, title TEXT, text_sample TEXT, n_nodes INT,
            presets TEXT, colors TEXT, fonts TEXT, w_in REAL, h_in REAL, area_pct REAL, hash TEXT, file TEXT,
            thumb TEXT, tier TEXT);
    """)


_ITEM_COLS = ("id, grp, scope, category, subcategory, source, source_file, slide_no, title, text_sample, n_nodes, "
              "presets, colors, fonts, w_in, h_in, area_pct, hash, file, thumb, tier")


def _items_sql(con) -> str:
    """All items: the original table plus ingested rows, with labels and scores when present."""
    has_lab = any(r[1] == "lab" for r in con.execute("PRAGMA database_list"))
    base = f"SELECT rowid AS rid, {_ITEM_COLS} FROM main.items"
    if has_lab:
        base += f" UNION ALL SELECT -rowid AS rid, {_ITEM_COLS} FROM lab.ingested"
        return (f"SELECT b.*, COALESCE(l.category, b.category) AS cat, l.quality, l.usable, l.descr, l.style, "
                f"s.score, g.rank AS golden FROM ({base}) b LEFT JOIN lab.labels l ON l.id = b.id "
                f"LEFT JOIN lab.scores s ON s.id = b.id LEFT JOIN lab.golden g ON g.id = b.id")
    return (f"SELECT b.*, b.category AS cat, NULL AS quality, NULL AS usable, NULL AS descr, NULL AS style, "
            f"NULL AS score, NULL AS golden FROM ({base}) b")


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["category_original"] = d.get("category")
    d["category"] = d.pop("cat", None) or d.get("category")
    d.pop("rid", None)
    return d


def search(category: str | None = None, scope: str | None = None, text: str | None = None, tier=None,
           limit: int = 8, golden: bool | None = None, min_quality: int | None = None,
           include_uploads: bool = False) -> list[dict]:
    """Library items, best first (golden, then label quality, then score). category matches the vision label when one
    exists, else the original heuristic category; tier is a name or a list; text: plain words matched against the
    category, label, title and item text (items matching at least half the words, most matches first). Empty when the
    knowledge base is off."""
    if not available():
        return []
    con = connect()
    try:
        where, args = [], []
        if not include_uploads:
            where.append("grp != ?")
            args.append(UPLOADS)
        if category:
            where.append("cat = ?")
            args.append(category)
        if scope:
            where.append("scope = ?")
            args.append(scope)
        if tier:
            tiers = [tier] if isinstance(tier, str) else list(tier)
            where.append(f"tier IN ({','.join('?' * len(tiers))})")
            args += tiers
        if golden:
            where.append("golden IS NOT NULL")
        if min_quality:
            where.append("quality >= ?")
            args.append(int(min_quality))
        sql = f"SELECT * FROM ({_items_sql(con)})"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += (" ORDER BY (golden IS NULL), COALESCE(quality, 0) DESC, COALESCE(score, 0) DESC, "
                "CASE tier WHEN 'consulting_grade' THEN 0 WHEN 'business' THEN 1 ELSE 2 END, id")
        rows = con.execute(sql, args)
        words = [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP]
        if not words:
            return [_row(r) for r in rows.fetchmany(int(limit))]
        need = max(1, math.ceil(len(words) / 2))         # at least half the words, more matches first
        ranked = []
        for i, r in enumerate(rows):
            blob = " ".join(str(r[k] or "") for k in ("cat", "subcategory", "descr", "title", "text_sample"))
            blob = blob.lower().replace("_", " ")
            hit = sum(1 for w in words if re.search(rf"\b{re.escape(w)}", blob))
            if hit >= need:
                ranked.append((-hit, i, r))
        ranked.sort(key=lambda t: (t[0], t[1]))
        return [_row(r) for _, _, r in ranked[:int(limit)]]
    finally:
        con.close()


def item(item_id: str) -> dict | None:
    if not available():
        return None
    con = connect()
    try:
        r = con.execute(f"SELECT * FROM ({_items_sql(con)}) WHERE id = ?", (item_id,)).fetchone()
        return _row(r) if r else None
    finally:
        con.close()


def thumbnail(item_id: str) -> Path | None:
    """The item's thumbnail file, or None."""
    it = item(item_id)
    r = root()
    if not it or r is None:
        return None
    p = r / (it.get("thumb") or f"thumbs/{item_id}.webp")
    return p if p.is_file() else None


def item_file(item_id: str) -> Path | None:
    it = item(item_id)
    r = root()
    if not it or r is None or not it.get("file"):
        return None
    p = r / it["file"]
    return p if p.is_file() else None


# ---------------------------------------------------------------- image features (similar, near-duplicate removal)

def features(img) -> tuple[int, list[float]]:
    """(dHash, vector) of a thumbnail: a 64-bit difference hash, then a coarse colour histogram (4x4x4) and an 8x6 ink
    layout grid, each L2-normalised. Deterministic, numpy and PIL only."""
    import numpy as np
    from PIL import Image
    im = img if isinstance(img, Image.Image) else Image.open(img)
    im = im.convert("RGB")
    g = np.asarray(im.convert("L").resize((9, 8)), dtype=float)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    dh = int("".join("1" if b else "0" for b in bits), 2)
    a = np.asarray(im.resize((64, 48)), dtype=int)
    q = (a // 64).reshape(-1, 3)
    hist = np.bincount(q[:, 0] * 16 + q[:, 1] * 4 + q[:, 2], minlength=64).astype(float)
    ink = (255 - np.asarray(im.convert("L").resize((8, 6)), dtype=float)) / 255.0
    def unit(v):
        n = float(np.linalg.norm(v))
        return v / n if n else v
    vec = np.concatenate([unit(hist), unit(ink.flatten())])
    return dh, [round(float(x), 4) for x in vec]


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _cos(a, b) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def build_features(ids: list[str] | None = None, progress=None) -> int:
    """Compute and store features for items with a thumbnail (all items, or ids). Returns the number stored."""
    con = connect(write=True)
    if con is None:
        return 0
    r = root()
    try:
        rows = con.execute(f"SELECT id, thumb FROM ({_items_sql(con)})").fetchall()
        have = {x[0] for x in con.execute("SELECT id FROM lab.features")}
        n = 0
        for k, row in enumerate(rows):
            if (ids is not None and row["id"] not in ids) or row["id"] in have or not row["thumb"]:
                continue
            p = r / row["thumb"]
            if not p.is_file():
                continue
            try:
                dh, vec = features(p)
            except Exception:                              # an unreadable thumbnail is skipped, not fatal
                continue
            con.execute("INSERT OR REPLACE INTO lab.features VALUES (?,?,?)", (row["id"], f"{dh:016x}", json.dumps(vec)))
            n += 1
            if progress and k % 500 == 0:
                progress(k, len(rows))
        con.commit()
        return n
    finally:
        con.close()


def similar(item_id: str, k: int = 5, include_uploads: bool = False) -> list[tuple[str, float]]:
    """The k items that look most like item_id (features computed on demand), as (id, similarity 0..1)."""
    if not available():
        return []
    build_features()                                   # once per library: later calls only add new items
    con = connect(write=True)
    try:
        me = con.execute("SELECT dhash, vec FROM lab.features WHERE id = ?", (item_id,)).fetchone()
        if not me:
            return []
        mv, md = json.loads(me["vec"]), int(me["dhash"], 16)
        grp = {r[0]: r[1] for r in con.execute(f"SELECT id, grp FROM ({_items_sql(con)})")}
        out = []
        for r in con.execute("SELECT id, dhash, vec FROM lab.features WHERE id != ?", (item_id,)):
            if not include_uploads and grp.get(r["id"]) == UPLOADS:
                continue
            s = 0.7 * _cos(mv, json.loads(r["vec"])) + 0.3 * (1 - hamming(md, int(r["dhash"], 16)) / 64)
            out.append((r["id"], round(s, 4)))
        out.sort(key=lambda x: -x[1])
        return out[:k]
    finally:
        con.close()


# ---------------------------------------------------------------- deterministic quality score

def score_pptx(path: str | Path, tier: str | None = None, area_pct: float | None = None) -> dict:
    """Quality score 0..1 of one library item from its file: tier, text to shape ratio, placeholder text, shapes off the
    slide, overlapping text boxes, size, colour and font counts. A file that cannot be read scores 0."""
    from pptx import Presentation
    from pptx.util import Emu
    parts: dict[str, float] = {}
    try:
        prs = Presentation(str(path))
        sl = prs.slides[0]
    except Exception:
        return {"score": 0.0, "parts": {"readable": 0.0}}
    W, H = prs.slide_width or Emu(12192000), prs.slide_height or Emu(6858000)
    shapes = list(sl.shapes)
    texts = [sh.text_frame.text for sh in shapes if sh.has_text_frame and sh.text_frame.text.strip()]
    words = sum(len(t.split()) for t in texts)
    n = max(1, len(shapes))
    off = sum(1 for sh in shapes if sh.left is not None and sh.width is not None and
              (sh.left < -W * 0.01 or sh.top < -H * 0.01 or sh.left + sh.width > W * 1.01 or sh.top + sh.height > H * 1.01))
    boxes = [(sh.left, sh.top, sh.left + sh.width, sh.top + sh.height) for sh in shapes
             if sh.has_text_frame and sh.text_frame.text.strip() and sh.width and sh.height and sh.left is not None]
    overl = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            ix = min(a[2], b[2]) - max(a[0], b[0])
            iy = min(a[3], b[3]) - max(a[1], b[1])
            if ix > 0 and iy > 0 and ix * iy > 0.2 * min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])):
                overl += 1
    xml = sl._element.xml
    colors = set(re.findall(r'srgbClr val="([0-9A-Fa-f]{6})"', xml)) | set(re.findall(r'schemeClr val="(\w+)"', xml))
    fonts = set(re.findall(r'typeface="([^"+][^"]*)"', xml))
    ph = sum(1 for t in texts if PLACEHOLDER.search(t))
    parts["tier"] = {"consulting_grade": 1.0, "business": 0.7, "general": 0.3}.get(tier or "", 0.4)
    ratio = words / n
    parts["text_ratio"] = 1.0 if 1.0 <= ratio <= 12 else (0.6 if ratio > 12 else 0.5 + 0.5 * ratio)
    parts["placeholder"] = 1.0 - min(1.0, ph / max(1, len(texts)))
    parts["on_slide"] = 1.0 - min(1.0, off / n)
    parts["overlap"] = 1.0 - min(1.0, overl / max(1, len(boxes)))
    parts["size"] = 1.0 if area_pct is None or area_pct >= 8 else max(0.2, area_pct / 8)
    parts["colors"] = 1.0 if len(colors) <= 6 else max(0.2, 1 - (len(colors) - 6) / 10)
    parts["fonts"] = 1.0 if len(fonts) <= 2 else max(0.2, 1 - (len(fonts) - 2) / 4)
    parts["gradients"] = 0.5 if "<a:gradFill" in xml else 1.0
    w = {"tier": 2, "text_ratio": 1, "placeholder": 1, "on_slide": 1, "overlap": 1.5, "size": 1, "colors": 1,
         "fonts": 1, "gradients": 0.5}
    s = sum(parts[k] * w[k] for k in w) / sum(w.values())
    return {"score": round(s, 3), "parts": {k: round(v, 2) for k, v in parts.items()},
            "words": words, "shapes": len(shapes), "placeholders": ph, "colors": len(colors), "fonts": len(fonts)}


def score_all(progress=None, include_uploads: bool = False) -> int:
    """Score every library item that has no score yet. Returns the number scored."""
    con = connect(write=True)
    if con is None:
        return 0
    r = root()
    try:
        done = {x[0] for x in con.execute("SELECT id FROM lab.scores")}
        rows = con.execute(f"SELECT id, file, tier, area_pct, grp FROM ({_items_sql(con)})").fetchall()
        n = 0
        for k, row in enumerate(rows):
            if row["id"] in done or (row["grp"] == UPLOADS and not include_uploads):
                continue
            p = r / (row["file"] or "")
            res = score_pptx(p, row["tier"], row["area_pct"]) if row["file"] and p.is_file() else \
                {"score": 0.0, "parts": {"readable": 0.0}}
            thumb_ok = (r / "thumbs" / f"{row['id']}.webp").is_file()
            if not thumb_ok:
                res["score"] = round(res["score"] * 0.8, 3)
                res["parts"]["rendered"] = 0.0
            con.execute("INSERT OR REPLACE INTO lab.scores VALUES (?,?,?)", (row["id"], res["score"], json.dumps(res)))
            n += 1
            if progress and k % 500 == 0:
                progress(k, len(rows))
                con.commit()
        con.commit()
        return n
    finally:
        con.close()


# ---------------------------------------------------------------- vision labels and the golden set

def import_labels(records: list[dict], source: str = "vision") -> int:
    """Store vision labels ({"id", "category", "subcategory", "desc", "quality", "usable", "style"}) in labels.sqlite.
    The original items table is never changed."""
    con = connect(write=True)
    if con is None:
        return 0
    try:
        n = 0
        for x in records:
            if not x.get("id"):
                continue
            con.execute("INSERT OR REPLACE INTO lab.labels VALUES (?,?,?,?,?,?,?,?)",
                        (x["id"], x.get("category"), x.get("subcategory"), x.get("desc") or x.get("descr"),
                         int(x["quality"]) if x.get("quality") is not None else None,
                         None if x.get("usable") is None else int(bool(x["usable"])),
                         json.dumps(x.get("style") or []), source))
            n += 1
        con.commit()
        return n
    finally:
        con.close()


def build_golden(min_quality: int = 4, max_items: int = 600, dup_hamming: int = 6, dup_cos: float = 0.97,
                 per_category_cap: int = 90) -> list[dict]:
    """The golden set: labelled quality >= min_quality and usable, best first, near duplicates removed (dHash within
    dup_hamming bits and feature similarity >= dup_cos), at most per_category_cap per category. Written to
    labels.sqlite and library/golden.jsonl. Returns the chosen items."""
    if not available():
        return []
    con = connect(write=True)
    r = root()
    try:
        rows = [dict(x) for x in con.execute(
            f"SELECT * FROM ({_items_sql(con)}) WHERE grp != ? AND quality >= ? AND usable = 1 "
            "ORDER BY quality DESC, COALESCE(score, 0) DESC, id", (UPLOADS, min_quality))]
        con.execute("DELETE FROM lab.golden")
        con.commit()
    finally:
        con.close()
    build_features([x["id"] for x in rows])
    con = connect(write=True)
    try:
        feats = {x["id"]: (int(x["dhash"], 16), json.loads(x["vec"]))
                 for x in con.execute("SELECT id, dhash, vec FROM lab.features")}
        chosen, per_cat, dropped = [], {}, 0
        for x in rows:
            cat = x["cat"]
            if per_cat.get(cat, 0) >= per_category_cap:
                continue
            f = feats.get(x["id"])
            if f and any(c in feats and hamming(f[0], feats[c][0]) <= dup_hamming and _cos(f[1], feats[c][1]) >= dup_cos
                         for c in (y["id"] for y in chosen)):
                dropped += 1
                continue
            chosen.append(x)
            per_cat[cat] = per_cat.get(cat, 0) + 1
            if len(chosen) >= max_items:
                break
        for i, x in enumerate(chosen):
            con.execute("INSERT OR REPLACE INTO lab.golden VALUES (?,?)", (x["id"], i + 1))
        con.commit()
    finally:
        con.close()
    out = [_row_dict(x) for x in chosen]
    with open(r / "library" / "golden.jsonl", "w") as f:
        for x in out:
            f.write(json.dumps(x) + "\n")
    log.info("golden set: %d items, %d near duplicates dropped", len(out), dropped)
    return out


def _row_dict(x: dict) -> dict:
    keep = ("id", "scope", "cat", "subcategory", "source", "tier", "quality", "score", "descr", "style", "file", "thumb",
            "w_in", "h_in", "area_pct", "n_nodes", "colors", "fonts")
    d = {k: x.get(k) for k in keep}
    d["category"] = d.pop("cat")
    return d


def audit() -> str:
    """A Markdown report of the library: counts before and after cleaning, per category and tier. Also written to
    MESHPOOL_KB/reports/library_audit.md."""
    if not available():
        return "knowledge base off"
    con = connect(write=True)
    r = root()
    try:
        rows = [dict(x) for x in con.execute(f"SELECT * FROM ({_items_sql(con)}) WHERE grp != ?", (UPLOADS,))]
    finally:
        con.close()
    lines = ["# Library audit", "", f"Items (uploads excluded): {len(rows)}", ""]
    def table(title, key):
        cnt: dict = {}
        for x in rows:
            k = key(x)
            c = cnt.setdefault(k, {"all": 0, "labelled": 0, "q45": 0, "usable": 0, "golden": 0})
            c["all"] += 1
            c["labelled"] += x["quality"] is not None
            c["q45"] += (x["quality"] or 0) >= 4
            c["usable"] += bool(x["usable"])
            c["golden"] += x["golden"] is not None
        lines.extend([f"## {title}", "", "| | items | labelled | quality 4-5 | usable | golden |", "|---|---|---|---|---|---|"])
        for k, c in sorted(cnt.items(), key=lambda kv: -kv[1]["all"]):
            lines.append(f"| {k} | {c['all']} | {c['labelled']} | {c['q45']} | {c['usable']} | {c['golden']} |")
        lines.append("")
    table("By tier", lambda x: x["tier"])
    table("By category (vision label where present, else original)", lambda x: x["cat"])
    table("By original heuristic category", lambda x: x["category"])
    sc = sorted(x["score"] for x in rows if x["score"] is not None)
    if sc:
        lines += ["## Deterministic score", "", f"scored {len(sc)}; median {sc[len(sc) // 2]:.2f}; "
                  f"p10 {sc[len(sc) // 10]:.2f}; p90 {sc[9 * len(sc) // 10]:.2f}", ""]
    txt = "\n".join(lines)
    (r / "reports").mkdir(exist_ok=True)
    (r / "reports" / "library_audit.md").write_text(txt)
    return txt


# ---------------------------------------------------------------- priors

@lru_cache(maxsize=1)
def reference_stats() -> dict:
    """Reference deck measurements (numbers only), shipped with the engine; available without the knowledge base."""
    p = Path(__file__).parent / "data" / "reference_stats.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def priors(archetype: str, kind: str = "all") -> dict:
    """What strong slides look like for this archetype, as numbers: the reference deck distributions (title, words,
    empty space, margins; kind "all", "consulting" or "financial_services"), and, when the knowledge base is set up,
    the golden items' proportions for the archetype's categories (count, aspect, node count, colour count)."""
    rs = reference_stats().get("groups", {})
    g = rs.get("all" if kind == "all" else f"kind:{kind}", {})
    keys = ("title_size_pt_norm", "title_lines", "title_words", "body_words", "empty_share", "text_margin_left_in",
            "n_font_sizes", "color_count")
    out = {"archetype": archetype, "categories": ARCH_CATEGORIES.get(archetype, []),
           "reference": {k: g[k] for k in keys if k in g}, "library": None}
    if archetype and available() and out["categories"]:
        con = connect()
        try:
            cats = out["categories"]
            sql = (f"SELECT * FROM ({_items_sql(con)}) WHERE grp != ? AND cat IN ({','.join('?' * len(cats))}) "
                   "AND (golden IS NOT NULL OR (quality IS NULL AND tier IN ('consulting_grade','business')))")
            rows = [dict(x) for x in con.execute(sql, (UPLOADS, *cats))]
        finally:
            con.close()
        def med(xs):
            xs = sorted(x for x in xs if x is not None)
            return xs[len(xs) // 2] if xs else None
        out["library"] = {
            "items": len(rows), "golden": sum(1 for x in rows if x["golden"] is not None),
            "aspect_median": med([x["w_in"] / x["h_in"] for x in rows if x["w_in"] and x["h_in"]]),
            "nodes_median": med([x["n_nodes"] for x in rows]),
            "colors_median": med([len(json.loads(x["colors"] or "[]")) for x in rows]),
            "subcategories": _top([x["subcategory"] for x in rows if x["subcategory"]], 8),
        }
    return out


def _top(xs, n):
    c: dict = {}
    for x in xs:
        c[x] = c.get(x, 0) + 1
    return [k for k, _ in sorted(c.items(), key=lambda kv: -kv[1])[:n]]


# ---------------------------------------------------------------- CLI

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m engines.forge.kb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("text", nargs="?")
    s.add_argument("--category")
    s.add_argument("--scope")
    s.add_argument("--tier", action="append")
    s.add_argument("--golden", action="store_true")
    s.add_argument("--limit", type=int, default=8)
    sub.add_parser("score")
    lb = sub.add_parser("labels")
    lb.add_argument("files", nargs="+")
    gd = sub.add_parser("golden")
    gd.add_argument("--max", type=int, default=600)
    sub.add_parser("audit")
    sm = sub.add_parser("similar")
    sm.add_argument("id")
    sm.add_argument("-k", type=int, default=5)
    pr = sub.add_parser("priors")
    pr.add_argument("archetype")
    pr.add_argument("--kind", default="all")
    ig = sub.add_parser("ingest")
    ig.add_argument("folder")
    ig.add_argument("--no-thumbs", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if a.cmd != "priors" and not available():
        print("knowledge base off: set MESHPOOL_KB to the slide_engine_kb folder")
        return 1
    if a.cmd == "search":
        for x in search(a.category, a.scope, a.text, a.tier, a.limit, golden=a.golden or None):
            print(f"{x['id']:<52} {x['category']:<18} {x['scope']:<13} {x['tier']:<16} q={x['quality']} "
                  f"s={x['score']} {(x['descr'] or x['subcategory'] or '')[:50]}")
    elif a.cmd == "score":
        print("scored", score_all(progress=lambda k, n: print(f"  {k}/{n}", flush=True)))
    elif a.cmd == "labels":
        recs = [json.loads(line) for f in a.files for line in open(f) if line.strip()]
        print("labels stored", import_labels(recs))
    elif a.cmd == "golden":
        print("golden items", len(build_golden(max_items=a.max)))
    elif a.cmd == "audit":
        print(audit())
    elif a.cmd == "similar":
        for i, s_ in similar(a.id, a.k):
            print(f"{s_:.3f}  {i}")
    elif a.cmd == "priors":
        print(json.dumps(priors(a.archetype, a.kind), indent=1))
    elif a.cmd == "ingest":
        from .kb_ingest import ingest
        print(json.dumps(ingest(a.folder, thumbs=not a.no_thumbs), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
