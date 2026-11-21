"""Add a folder of PowerPoint files to the slide knowledge base (MESHPOOL_KB): one item per slide and per large
top-level group, deduplicated by structure, with a thumbnail, a heuristic category and a quality score.

    python -m engines.forge.kb ingest FOLDER [--no-thumbs]

Everything is written inside MESHPOOL_KB (library/ingested/<category>/<id>.pptx, thumbs/<id>.webp and rows in
library/labels.sqlite, table `ingested`), never into the repo. The original items table is not changed. Vision labels
can be added later with `python -m engines.forge.kb labels`.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMU = 914400

KEYWORDS = [  # (category, pattern over the item's text), first match wins
    ("agenda_toc", r"\b(agenda|contents|table of contents)\b"),
    ("risk_matrix", r"\b(likelihood|probability)\b.*\bimpact\b|\bimpact\b.*\b(likelihood|probability)\b|risk matrix"),
    ("gantt", r"\bgantt\b|\bworkstream"),
    ("timeline_roadmap", r"\b(timeline|roadmap|milestone|phase \d|q[1-4] ?20\d\d|20[1-3]\d\s*[-–]\s*20[1-3]\d)\b"),
    ("hierarchy_org", r"\b(ceo|cfo|coo|org(anisation|anization)? chart|reports to|head of)\b"),
    ("issue_tree", r"\b(issue tree|driver tree|logic tree|hypothesis tree)\b"),
    ("pyramid_funnel", r"\b(pyramid|funnel)\b"),
    ("cycle", r"\b(cycle|circular|flywheel|loop)\b"),
    ("venn_overlap", r"\bvenn\b"),
    ("matrix_framework", r"\b(swot|strengths|weaknesses|2x2|matrix|porter|bcg)\b"),
    ("process_flow", r"\b(step \d|process|stage \d|workflow)\b"),
    ("team_profile", r"\b(our team|meet the team|founder|team members)\b"),
    ("closing_contact", r"\b(thank you|contact us|questions\?)\b"),
    ("kpi_metrics", r"\b(kpi|key metrics|at a glance)\b"),
]


def struct_hash(node) -> str:
    """Hash of a shape tree's structure: geometry, nesting and fills, without ids, names, positions or text."""
    n = copy.deepcopy(node)
    for el in n.iter():
        if not isinstance(el.tag, str):
            continue
        for k in list(el.attrib):
            if k in ("id", "name", "descr", "title", "x", "y", "uniqueId") or k.startswith("{%s}" % R):
                del el.attrib[k]
        if el.tag == f"{{{A}}}t":
            el.text = ""
    return hashlib.sha1(etree.tostring(n, method="c14n")).hexdigest()[:16]


def _one_slide(src: bytes, keep: int) -> bytes:
    """The deck with only slide `keep` (0-based). python-pptx writes only parts still reachable, so the other slides,
    their notes, media and charts are dropped with them."""
    from pptx import Presentation
    prs = Presentation(io.BytesIO(src))
    lst = prs.slides._sldIdLst
    for i, sid in reversed(list(enumerate(list(lst)))):
        if i != keep:
            prs.part.drop_rel(sid.get(f"{{{R}}}id"))
            lst.remove(sid)
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def _only_shape(one: bytes, shape_index: int) -> bytes:
    """A one-slide deck reduced to one top-level shape (a component)."""
    from pptx import Presentation
    prs = Presentation(io.BytesIO(one))
    tree = prs.slides[0].shapes._spTree
    kids = [k for k in tree if etree.QName(k).localname not in ("nvGrpSpPr", "grpSpPr", "extLst")]
    for i, k in enumerate(kids):
        if i != shape_index:
            tree.remove(k)
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def describe(data: bytes) -> dict:
    """Metadata of a one-slide deck: text, node count, preset geometry counts, colours, fonts, bounding box, charts and
    tables, and a heuristic category."""
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    sl = prs.slides[0]
    W, H = prs.slide_width, prs.slide_height
    tree = sl.shapes._spTree
    xml = etree.tostring(tree).decode()
    texts = [t.text for t in tree.iter(f"{{{A}}}t") if t.text and t.text.strip()]
    presets: dict[str, int] = {}
    for g in tree.iter(f"{{{A}}}prstGeom"):
        presets[g.get("prst")] = presets.get(g.get("prst"), 0) + 1
    boxes = [(s.left, s.top, s.left + s.width, s.top + s.height) for s in sl.shapes
             if s.left is not None and s.width is not None]
    if boxes:
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    else:
        x0 = y0 = x1 = y1 = 0
    charts = len(re.findall(r'graphicData uri="[^"]*/chart"', xml))
    tables = xml.count("<a:tbl>") + xml.count(":tbl>") // 2
    title = next((s.text_frame.text for s in sl.shapes if s.is_placeholder and s.has_text_frame
                  and s.placeholder_format.type in (1, 3) and s.text_frame.text.strip()), "")
    text = " ".join(texts)
    cat = "chart" if charts else ("table" if tables else None)
    if cat is None:
        low = text.lower()
        cat = next((c for c, pat in KEYWORDS if re.search(pat, low)), None)
    if cat is None:
        if presets.get("chevron", 0) + presets.get("homePlate", 0) >= 3:
            cat = "process_flow"
        elif sum(presets.values()) <= 3 and len(texts) <= 1:
            cat = "shape_basic"
        else:
            cat = "text_layout" if len(text.split()) > 40 else "other"
    return {"title": title[:200], "text_sample": text[:400], "n_nodes": sum(1 for _ in sl.shapes), "presets": presets,
            "colors": sorted(set(re.findall(r'srgbClr val="([0-9A-Fa-f]{6})"', xml)))[:12],
            "fonts": sorted(set(re.findall(r'typeface="([^"+][^"]*)"', xml)))[:6],
            "w_in": round((x1 - x0) / EMU, 3), "h_in": round((y1 - y0) / EMU, 3),
            "area_pct": round(100 * (x1 - x0) * (y1 - y0) / max(1, W * H), 1), "category": cat,
            "hash": struct_hash(tree)}


def components(data: bytes, min_area_pct: float = 6.0, min_children: int = 3) -> list[int]:
    """Top-level groups worth keeping as components: at least min_children shapes and min_area_pct of the slide."""
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    W, H = prs.slide_width, prs.slide_height
    tree = prs.slides[0].shapes._spTree
    kids = [k for k in tree if etree.QName(k).localname not in ("nvGrpSpPr", "grpSpPr", "extLst")]
    out = []
    for i, k in enumerate(kids):
        if etree.QName(k).localname != "grpSp":
            continue
        n = sum(1 for c in k if etree.QName(c).localname in ("sp", "grpSp", "graphicFrame", "pic", "cxnSp"))
        ext = k.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm/{{{A}}}ext")
        if ext is None or n < min_children:
            continue
        if 100 * int(ext.get("cx")) * int(ext.get("cy")) / max(1, W * H) >= min_area_pct:
            out.append(i)
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:48] or "deck"


def thumbnails(files: list[Path], out_dir: Path, width: int = 320, chunk: int = 40, timeout: int = 120,
               single_timeout: int = 40) -> dict[str, Path]:
    """One WebP thumbnail per one-slide deck (LibreOffice -> PDF -> pypdfium2). Converts in chunks with a private
    profile; a chunk that hangs is killed (its whole process group) and its files retried one at a time with a short
    timeout, so one stuck file never blocks the batch. Returns {file stem: thumbnail path}."""
    from .common import find_soffice
    import pypdfium2 as pdfium
    soffice = find_soffice()
    if not soffice or not files:
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)
    done: dict[str, Path] = {}

    def convert(batch, tmp, limit):
        cmd = [soffice, "--headless", f"-env:UserInstallation=file://{tmp}/profile", "--convert-to", "pdf",
               "--outdir", tmp] + [str(f) for f in batch]
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            p.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()

    for i in range(0, len(files), chunk):
        part = files[i:i + chunk]
        tmp = tempfile.mkdtemp(prefix="kbthumb_")
        try:
            convert(part, tmp, timeout)
            for f in part:
                pdf = Path(tmp) / (f.stem + ".pdf")
                if not pdf.exists():
                    convert([f], tmp, single_timeout)
                if not pdf.exists():
                    continue
                doc = pdfium.PdfDocument(str(pdf))
                try:
                    pg = doc[0]
                    im = pg.render(scale=width / pg.get_width()).to_pil().convert("RGB")
                finally:
                    doc.close()
                t = out_dir / (f.stem + ".webp")
                im.save(t, quality=70)
                done[f.stem] = t
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return done


def ingest(folder: str, thumbs: bool = True, tier: str = "business", with_components: bool = True) -> dict:
    """Ingest every .pptx/.potx under folder into MESHPOOL_KB. Returns counts."""
    from . import kb
    r = kb.root()
    if r is None:
        return {"error": "MESHPOOL_KB is not set or has no library/components.db"}
    lib = r / "library" / "ingested"
    con = kb.connect(write=True)
    try:
        seen = {row[0] for row in con.execute("SELECT hash FROM lab.ingested WHERE hash IS NOT NULL")}
        have = {row[0] for row in con.execute("SELECT id FROM lab.ingested")}
    finally:
        con.close()
    stats = {"files": 0, "slides": 0, "items": 0, "duplicates": 0, "errors": 0, "thumbnails": 0}
    new: list[dict] = []
    for f in sorted(Path(folder).rglob("*")):
        if f.suffix.lower() not in (".pptx", ".potx") or f.name.startswith("~$"):
            continue
        stats["files"] += 1
        src = f.read_bytes()
        try:
            from pptx import Presentation
            n_slides = len(Presentation(io.BytesIO(src)).slides)
        except Exception:
            stats["errors"] += 1
            continue
        stem = _slug(f.stem)
        for k in range(n_slides):
            stats["slides"] += 1
            try:
                one = _one_slide(src, k)
                pieces = [("full_slide", None, one)]
                if with_components:
                    pieces += [("partial_slide", j, _only_shape(one, j)) for j in components(one)]
            except Exception:
                stats["errors"] += 1
                continue
            for scope, j, data in pieces:
                try:
                    d = describe(data)
                except Exception:
                    stats["errors"] += 1
                    continue
                key = f"{d['hash']}:{round(d['w_in'] * 4)}:{round(d['h_in'] * 4)}"
                if key in seen:
                    stats["duplicates"] += 1
                    continue
                seen.add(key)
                iid = f"ing_{stem}_s{k + 1:03d}" + (f"_c{j:02d}" if j is not None else "")
                if iid in have:
                    continue
                rel = Path("library") / "ingested" / d["category"] / f"{iid}.pptx"
                (r / rel).parent.mkdir(parents=True, exist_ok=True)
                (r / rel).write_bytes(data)
                new.append({**d, "id": iid, "scope": scope, "source": stem, "source_file": f.name, "slide_no": k + 1,
                            "file": str(rel), "hash": key, "tier": tier})
    if thumbs and new:
        got = thumbnails([r / x["file"] for x in new], r / "thumbs")
        stats["thumbnails"] = len(got)
    con = kb.connect(write=True)
    try:
        for x in new:
            thumb = f"thumbs/{x['id']}.webp" if (r / "thumbs" / f"{x['id']}.webp").is_file() else None
            con.execute("INSERT OR REPLACE INTO lab.ingested VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (x["id"], "ingested", x["scope"], x["category"], "", x["source"], x["source_file"],
                         x["slide_no"], x["title"], x["text_sample"], x["n_nodes"], json.dumps(x["presets"]),
                         json.dumps(x["colors"]), json.dumps(x["fonts"]), x["w_in"], x["h_in"], x["area_pct"],
                         x["hash"], x["file"], thumb, x["tier"]))
            s = kb.score_pptx(r / x["file"], x["tier"], x["area_pct"])
            if thumb is None:
                s["score"] = round(s["score"] * 0.8, 3)
            con.execute("INSERT OR REPLACE INTO lab.scores VALUES (?,?,?)", (x["id"], s["score"], json.dumps(s)))
        con.commit()
    finally:
        con.close()
    stats["items"] = len(new)
    return stats
