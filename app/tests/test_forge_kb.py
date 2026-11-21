"""engines.forge.kb and kb_ingest on a tiny synthetic knowledge base built in the test (no knowledge base file is ever
committed), plus a few checks on the real one when MESHPOOL_KB is set (needs_kb)."""
import json
import os
import sqlite3

import pytest
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches, Pt

from engines.forge import kb, kb_ingest

COLS = ("id, grp, counted, scope, category, subcategory, source, source_file, slide_no, title, text_sample, n_nodes, "
        "presets, colors, fonts, w_in, h_in, area_pct, slide_w, slide_h, hash, file, thumb, bytes, tier")


def _deck(path, texts, off_slide=False):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    for i, t in enumerate(texts):
        tb = sl.shapes.add_textbox(Inches(0.6 + 3 * i), Inches(1.5), Inches(2.8), Inches(1))
        tb.text_frame.text = t
        tb.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
    if off_slide:
        sl.shapes.add_textbox(Inches(12.8), Inches(7.2), Inches(3), Inches(1)).text_frame.text = "off"
    prs.save(path)


def _thumb(path, kind):
    im = Image.new("RGB", (320, 180), "white")
    d = ImageDraw.Draw(im)
    if kind == "bars":
        for i, h in enumerate((60, 100, 140)):
            d.rectangle([40 + 80 * i, 170 - h, 90 + 80 * i, 170], fill=(20, 40, 120))
    elif kind == "steps":
        for i in range(4):
            d.polygon([(10 + 75 * i, 70), (70 + 75 * i, 70), (85 + 75 * i, 90), (70 + 75 * i, 110), (10 + 75 * i, 110)],
                      fill=(200, 60, 50))
    im.save(path)


@pytest.fixture
def fake_kb(tmp_path, monkeypatch):
    r = tmp_path / "kb"
    (r / "library" / "sourced").mkdir(parents=True)
    (r / "thumbs").mkdir()
    con = sqlite3.connect(r / "library" / "components.db")
    con.execute("CREATE TABLE items(id TEXT PRIMARY KEY, grp TEXT, counted INT, scope TEXT, category TEXT, subcategory "
                "TEXT, source TEXT, source_file TEXT, slide_no INT, title TEXT, text_sample TEXT, n_nodes INT, presets "
                "TEXT, colors TEXT, fonts TEXT, w_in REAL, h_in REAL, area_pct REAL, slide_w INT, slide_h INT, hash "
                "TEXT, file TEXT, thumb TEXT, bytes INT, tier TEXT)")
    con.execute("CREATE VIRTUAL TABLE items_fts USING fts5(id UNINDEXED, category, subcategory, title, text_sample, "
                "content='')")
    rows = [("c1", "sourced", "chart", "consulting_grade", "bars", ["Revenue grew 12%", "Margin"]),
            ("c2", "sourced", "chart", "business", "bars", ["Revenue by region", "Cost"]),
            ("p1", "sourced", "process_flow", "business", "steps", ["Step 1 plan", "Step 2 build", "Step 3 run"]),
            ("p2", "sourced", "process_flow", "general", "steps", ["Lorem ipsum", "Click to add text"]),
            ("u1", "examples_uploaded", "process_flow", "example", "steps", ["Private step"])]
    for iid, grp, cat, tier, kind, texts in rows:
        f = r / "library" / "sourced" / f"{iid}.pptx"
        _deck(f, texts)
        _thumb(r / "thumbs" / f"{iid}.webp", kind)
        vals = {"id": iid, "grp": grp, "scope": "full_slide", "category": cat, "subcategory": "", "source": "pack",
                "title": texts[0], "text_sample": " ".join(texts), "n_nodes": len(texts), "presets": "{}",
                "colors": "[]", "fonts": '["Arial"]', "w_in": 12.0, "h_in": 6.0, "area_pct": 70.0,
                "file": f"library/sourced/{iid}.pptx", "thumb": f"thumbs/{iid}.webp", "tier": tier}
        cols = [c.strip() for c in COLS.split(",")]
        con.execute(f"INSERT INTO items VALUES ({','.join('?' * len(cols))})", [vals.get(c) for c in cols])
        rid = con.execute("SELECT max(rowid) FROM items").fetchone()[0]
        con.execute("INSERT INTO items_fts(rowid, id, category, subcategory, title, text_sample) VALUES (?,?,?,?,?,?)",
                    (rid, iid, cat, "", texts[0], " ".join(texts)))
    con.commit()
    con.close()
    monkeypatch.setenv("MESHPOOL_KB", str(r))
    return r


def test_off_without_env(monkeypatch, caplog):
    monkeypatch.delenv("MESHPOOL_KB", raising=False)
    kb._WARNED["off"] = False
    with caplog.at_level("INFO", logger="forge.kb"):
        assert kb.available() is False
        assert kb.available() is False
        assert kb.search(category="chart") == [] and kb.similar("x") == [] and kb.thumbnail("x") is None
    assert sum("knowledge base off" in m for m in caplog.messages) == 1          # said once
    p = kb.priors("process_steps")                     # the reference numbers ship with the engine
    assert p["library"] is None and p["reference"]["title_words"]["median"] > 0


def test_off_when_folder_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHPOOL_KB", str(tmp_path / "nope"))
    assert kb.available() is False and kb.search() == []


def test_search_filters_and_excludes_uploads(fake_kb):
    ids = [x["id"] for x in kb.search(category="process_flow", limit=10)]
    assert set(ids) == {"p1", "p2"}                     # the user's upload is never returned by default
    assert "u1" in [x["id"] for x in kb.search(category="process_flow", include_uploads=True, limit=10)]
    assert [x["id"] for x in kb.search(tier=["consulting_grade"])] == ["c1"]
    assert kb.search(text="process flow 3 steps")[0]["category"] == "process_flow"
    assert {x["id"] for x in kb.search(text="revenue region")} >= {"c2"}


def test_labels_override_category_and_rank(fake_kb):
    kb.import_labels([{"id": "p2", "category": "process_flow", "quality": 1, "usable": False},
                      {"id": "p1", "category": "process_flow", "quality": 5, "usable": True, "desc": "four chevrons"},
                      {"id": "c2", "category": "kpi_metrics", "quality": 3, "usable": True}])
    assert [x["id"] for x in kb.search(category="process_flow")][0] == "p1"
    assert [x["id"] for x in kb.search(category="kpi_metrics")] == ["c2"]
    assert kb.item("c2")["category_original"] == "chart"
    con = sqlite3.connect(fake_kb / "library" / "components.db")      # the original table is untouched
    assert con.execute("SELECT category FROM items WHERE id='c2'").fetchone()[0] == "chart"


def test_similar_finds_the_lookalike(fake_kb):
    near = kb.similar("c1", k=2)
    assert near[0][0] == "c2" and near[0][1] > near[1][1]
    assert all(i != "u1" for i, _ in kb.similar("p1", k=5))


def test_thumbnail_and_file(fake_kb):
    assert kb.thumbnail("c1").name == "c1.webp" and kb.item_file("c1").suffix == ".pptx"


def test_score_penalises_placeholders_and_off_slide(tmp_path):
    good, ph, off = tmp_path / "g.pptx", tmp_path / "p.pptx", tmp_path / "o.pptx"
    _deck(good, ["Costs fell 4% after the merger", "Margin rose"])
    _deck(ph, ["Lorem ipsum dolor", "Click to add title"])
    _deck(off, ["Costs fell 4% after the merger", "Margin rose"], off_slide=True)
    g, p, o = (kb.score_pptx(x, "business", 50) for x in (good, ph, off))
    assert g["score"] > p["score"] and g["score"] > o["score"]
    assert p["placeholders"] == 2 and kb.score_pptx(tmp_path / "missing.pptx")["score"] == 0


def test_score_all_and_golden_dedupes(fake_kb):
    assert kb.score_all() == 4                          # uploads are not scored
    kb.import_labels([{"id": i, "category": c, "quality": 5, "usable": True}
                      for i, c in (("c1", "chart"), ("c2", "chart"), ("p1", "process_flow"))])
    gold = kb.build_golden(min_quality=4)
    ids = [x["id"] for x in gold]
    assert "p1" in ids and len({"c1", "c2"} & set(ids)) == 1          # c1 and c2 look alike: one is kept
    lines = (fake_kb / "library" / "golden.jsonl").read_text().splitlines()
    assert len(lines) == len(gold)
    assert kb.search(golden=True, limit=10) and all(x["golden"] for x in kb.search(golden=True, limit=10))
    rep = kb.audit()
    assert "golden" in rep and (fake_kb / "reports" / "library_audit.md").exists()


def test_priors_with_kb(fake_kb):
    p = kb.priors("process_steps")
    assert p["categories"] == ["process_flow"] and p["library"]["items"] >= 1


def test_cli_search(fake_kb, capsys):
    assert kb.main(["search", "process", "--limit", "2"]) == 0
    assert "process_flow" in capsys.readouterr().out


# ---------------------------------------------------------------- ingest

def _source_deck(path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    for title in ("Agenda", "Agenda"):                  # the second slide repeats the first: one item
        sl = prs.slides.add_slide(blank)
        for i, t in enumerate((title, "Market", "Plan")):
            sl.shapes.add_textbox(Inches(1), Inches(1 + i), Inches(4), Inches(0.8)).text_frame.text = t
    sl = prs.slides.add_slide(blank)                    # a process with a large group of chevrons
    from pptx.enum.shapes import MSO_SHAPE
    grp = sl.shapes.add_group_shape()
    for i in range(4):
        s = grp.shapes.add_shape(MSO_SHAPE.CHEVRON, Inches(0.6 + 3 * i), Inches(3), Inches(2.9), Inches(1.2))
        s.text_frame.text = f"Step {i + 1}"
    sl.shapes.add_textbox(Inches(0.6), Inches(0.5), Inches(8), Inches(0.8)).text_frame.text = "How the process works"
    prs.save(path)


def test_ingest_dedupes_labels_and_scores(fake_kb, tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _source_deck(src / "Deck One.pptx")
    (src / "~$lock.pptx").write_bytes(b"x")             # Office lock files are skipped
    st = kb.main(["ingest", str(src), "--no-thumbs"])
    assert st == 0
    con = kb.connect()
    rows = {r["id"]: dict(r) for r in con.execute("SELECT * FROM lab.ingested")}
    scores = {r[0] for r in con.execute("SELECT id FROM lab.scores")}
    con.close()
    full = [i for i, r in rows.items() if r["scope"] == "full_slide"]
    assert len(full) == 2                               # 3 slides, one duplicate
    assert rows["ing_deck_one_s001"]["category"] == "agenda_toc"
    assert rows["ing_deck_one_s003"]["category"] == "process_flow"
    comp = [i for i, r in rows.items() if r["scope"] == "partial_slide"]
    assert comp and all(i in scores for i in rows)
    for i, r in rows.items():                            # each item is a one-slide deck inside MESHPOOL_KB
        assert len(Presentation(str(fake_kb / r["file"])).slides) == 1
    assert kb.search(category="agenda_toc")[0]["id"] == "ing_deck_one_s001"
    again = kb_ingest.ingest(str(src), thumbs=False)    # a second run adds nothing
    assert again["items"] == 0 and again["duplicates"] >= 2


def test_struct_hash_ignores_text_and_position():
    from lxml import etree
    a = etree.fromstring('<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                         'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:nvSpPr><p:cNvPr id="2" '
                         'name="A"/></p:nvSpPr><a:off x="1" y="2"/><a:t>one</a:t></p:sp>')
    b = etree.fromstring(etree.tostring(a).replace(b'id="2"', b'id="9"').replace(b">one<", b">two<")
                         .replace(b'x="1"', b'x="5"'))
    assert kb_ingest.struct_hash(a) == kb_ingest.struct_hash(b)


def test_thumbnails_with_libreoffice(tmp_path):
    from engines.forge.common import find_soffice
    if not find_soffice():
        pytest.skip("LibreOffice not available")
    f = tmp_path / "one.pptx"
    _deck(f, ["Hello"])
    got = kb_ingest.thumbnails([f], tmp_path / "t")
    assert got["one"].exists() and Image.open(got["one"]).size[0] == 320


# ---------------------------------------------------------------- the real knowledge base

@pytest.mark.needs_kb
def test_real_kb_counts_and_search():
    con = kb.connect()
    assert con.execute("SELECT count(*) FROM main.items").fetchone()[0] >= 8523
    con.close()
    hits = kb.search(category="process_flow", tier=list(kb.GOOD_TIERS), limit=5)
    assert len(hits) == 5 and all(h["grp"] != kb.UPLOADS for h in hits)
    assert kb.thumbnail(hits[0]["id"]) is not None
