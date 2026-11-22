"""Meshpool checks and looks at anything visual: a UI audit that finds real defects and passes the shipped starter,
and a viewer that turns web pages, images, PDFs and Office files into pictures."""
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

from meshpool_harness import ui_audit as UA  # noqa: E402
from meshpool_harness import view as V  # noqa: E402

APP = Path(__file__).resolve().parents[1]
BAD = """<!doctype html><html><body style="margin:0">
<div style="width:900px;height:40px;background:#fff;color:#ccc">faint text on white that nobody can read</div>
<button style="width:20px;height:20px"><svg width="10" height="10"></svg></button>
<img src="missing.png" width="50" height="50">
<h1>Title</h1><h4>Skipped a level</h4>
<script>console.error("boom")</script></body></html>"""


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        try:
            UA.launcher(p)().close()
        except Exception:  # noqa: BLE001
            pytest.skip("no Chromium on this computer")
        yield p


def test_the_audit_finds_what_is_wrong_in_plain_words(pw, tmp_path):
    f = tmp_path / "bad.html"
    f.write_text(BAD, encoding="utf-8")
    r = UA.audit(pw, str(f), views=("desktop",), schemes=("light",), shots_dir=tmp_path / "s")
    rules = {x["rule"] for x in r["findings"]}
    print(sorted(rules))
    assert {"console", "broken-image"} <= rules
    assert "No problems found" not in r["text"] and "MUST FIX" in r["text"]
    assert any(x["rule"] == "console" and "boom" in x["msg"] for x in r["findings"])
    assert Path(r["shots"][0]).exists()


def test_the_starter_page_passes_everywhere(pw, tmp_path):
    r = UA.audit(pw, str(APP / "defaults/skills/build-ui/starter.html"), shots_dir=tmp_path)
    assert "No problems found" in r["text"], r["text"]
    assert len(r["runs"]) == 6          # three widths, light and dark


def test_the_skills_ship_with_the_starter_and_credit_the_method():
    for name in ("build-ui", "ui-review"):
        t = (APP / "defaults/skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert t.startswith("---\nname: " + name) and "ui_audit" in t and "—" not in t and "–" not in t
    assert (APP / "defaults/skills/build-ui/starter.html").exists()
    assert "Leonxlnx" in (APP.parent / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")


def test_view_output_draws_pages_images_tables_and_pdfs(pw, tmp_path):
    launch = UA.launcher(pw)
    (tmp_path / "a.html").write_text("<h1>Quarterly report</h1><p>Revenue grew 12 percent.</p>", encoding="utf-8")
    (tmp_path / "t.csv").write_text("name,amount\nAcme,10\nGlobex,20\n", encoding="utf-8")
    (tmp_path / "d.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="#09f"/></svg>', encoding="utf-8")
    web = V.render(str(tmp_path / "a.html"), out_dir=tmp_path / "o", launch=launch)
    assert web["kind"] == "web" and web["shots"][0][1].stat().st_size > 1000
    assert V.render(str(tmp_path / "t.csv"), out_dir=tmp_path / "o", launch=launch)["kind"] == "table"
    assert V.render(str(tmp_path / "d.svg"), out_dir=tmp_path / "o", launch=launch)["shots"]
    img = V.render(str(web["shots"][0][1]), out_dir=tmp_path / "o2")
    assert img["kind"] == "image" and "pixels" in img["notes"][0]
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(300, 200)
    pdf.save(str(tmp_path / "p.pdf"))
    r = V.render(str(tmp_path / "p.pdf"), out_dir=tmp_path / "o3")
    assert r["kind"] == "pdf" and len(r["shots"]) == 1 and "1 page" in r["notes"][0]


def test_view_output_says_what_it_cannot_draw_and_what_is_missing(tmp_path):
    (tmp_path / "x.bin").write_bytes(b"\x00")
    with pytest.raises(ValueError, match="can't draw"):
        V.render(str(tmp_path / "x.bin"))
    with pytest.raises(FileNotFoundError):
        V.render(str(tmp_path / "gone.png"))
    assert V._pick("2-4", 10) == [1, 2, 3] and V._pick(None, 20) == list(range(8)) and V._pick([1, 99], 3) == [0]


def test_office_files_become_pictures(tmp_path):
    from engines.forge.common import find_soffice
    if not find_soffice():
        pytest.skip("LibreOffice is not installed")
    pptx = pytest.importorskip("pptx")
    d = pptx.Presentation()
    d.slides.add_slide(d.slide_layouts[5]).shapes.title.text = "Hello"
    d.save(str(tmp_path / "s.pptx"))
    r = V.render(str(tmp_path / "s.pptx"), out_dir=tmp_path / "o")
    assert r["kind"] == "office" and r["shots"][0][1].exists()


def test_the_browser_server_offers_both_tools():
    from meshpool_harness.agent import browser_mcp_server as BM
    from meshpool_harness.harnesses.safe_tools import SAFE_MCP
    assert {"ui_audit", "view_output"} <= set(BM.TOOLS)
    assert {"mcp__browser__ui_audit", "mcp__browser__view_output"} <= SAFE_MCP
