"""Extended PDF measurement on tiny synthetic decks built with pymupdf (no reference PDFs needed)."""
import pytest

fitz = pytest.importorskip("pymupdf")

from engines.forge.measure_pdf import measure  # noqa: E402

W, H = 960, 540  # 13.33 x 7.5 in


def _title(pg, text="Revenue grew strongly across all three regions during the last financial year", size=24):
    pg.insert_textbox(fitz.Rect(54, 30, 880, 120), text, fontsize=size, fontname="helv")


def _bullets(pg, y=150, n=6, x=54):
    for i in range(n):
        pg.insert_text((x, y + i * 22), f"- bullet number {i} with a few ordinary words to fill the line", fontsize=14)


def _pdf(tmp_path, builders):
    doc = fitz.open()
    for b in builders:
        b(doc.new_page(width=W, height=H))
    p = tmp_path / "t.pdf"
    doc.save(p)
    return str(p)


def _text_page(pg):
    _title(pg)
    _bullets(pg)


def _chart_page(pg):
    _title(pg)
    pg.insert_text((54, 150), "Chart of growth in revenue by year for the group, in millions of euros", fontsize=12)
    base = 480
    for i, h in enumerate([60, 110, 170, 240, 300]):
        pg.draw_rect(fitz.Rect(100 + i * 90, base - h, 150 + i * 90, base), color=None, fill=(0.1, 0.3, 0.6))
    pg.draw_line((90, base), (600, base), color=(0, 0, 0))


def _table_page(pg):
    _title(pg)
    pg.insert_text((54, 150), "Table of results by region and quarter, with totals shown in the final row", fontsize=12)
    for i in range(6):
        y = 180 + i * 50
        pg.draw_line((54, y), (900, y), color=(0.4, 0.4, 0.4), width=0.8)
        for c in range(4):
            pg.insert_text((60 + c * 200, y + 30), f"r{i}c{c}", fontsize=12)


def _empty_page(pg):
    _title(pg)
    pg.insert_text((54, 150), "A page with one short paragraph and then nothing else at all, as a stress test.", fontsize=12)


def _two_line_title(pg):
    pg.insert_textbox(fitz.Rect(54, 30, 500, 140),
                      "Revenue grew strongly across all three regions during the last financial year", fontsize=24, fontname="helv")
    _bullets(pg)


def _pages(tmp_path, builders):
    return measure(_pdf(tmp_path, builders), extended=True)["pages"]


def test_default_output_has_no_extended_keys(tmp_path):
    r = measure(_pdf(tmp_path, [_text_page]))
    assert "title_lines" not in r["pages"][0] and "extended" not in r["summary"]
    assert "has_chart" not in r["method"]


def test_title_lines_and_font_sizes(tmp_path):
    one, two = _pages(tmp_path, [_text_page, _two_line_title])
    assert one["title_lines"] == 1 and two["title_lines"] == 2
    assert one["n_font_sizes"] == 2          # 24 pt title, 14 pt bullets
    assert one["page_w_in"] == pytest.approx(13.333, abs=0.01)


def test_margins(tmp_path):
    p = _pages(tmp_path, [_text_page])[0]
    assert p["margin_left_in"] == pytest.approx(54 / 72, abs=0.1)
    assert p["margin_top_in"] == pytest.approx(0.5, abs=0.25)
    assert p["margin_right_frac"] > 0.05 and p["margin_bottom_frac"] > 0.4
    assert p["margin_left_frac"] == pytest.approx(p["margin_left_in"] * 72 / W, abs=0.01)


def test_empty_share_orders_pages(tmp_path):
    text, empty = _pages(tmp_path, [_text_page, _empty_page])
    assert 0 <= text["empty_share"] <= 1
    assert empty["empty_share"] > 0.6          # almost the whole body band is blank
    assert empty["empty_share"] > text["empty_share"]


def test_color_count(tmp_path):
    text, chart = _pages(tmp_path, [_text_page, _chart_page])
    assert text["color_count"] == 0            # black text only
    assert chart["color_count"] == 1           # one blue fill; black axis excluded


def test_chart_and_table_flags(tmp_path):
    text, chart, table = _pages(tmp_path, [_text_page, _chart_page, _table_page])
    assert chart["has_chart"] and not chart["has_table"]
    assert table["has_table"] and not table["has_chart"]
    assert not text["has_chart"] and not text["has_table"]


def test_equal_boxes_are_not_a_chart(tmp_path):
    def cards(pg):
        _title(pg)
        pg.insert_text((54, 150), "Four equal cards in a row are layout and not a chart at all.", fontsize=12)
        for i in range(4):
            pg.draw_rect(fitz.Rect(60 + i * 220, 250, 260 + i * 220, 450), color=None, fill=(0.8, 0.2, 0.2))
    assert not _pages(tmp_path, [cards])[0]["has_chart"]


def test_summary_and_method(tmp_path):
    r = measure(_pdf(tmp_path, [_text_page, _chart_page, _table_page]), extended=True)
    ext = r["summary"]["extended"]
    assert ext["has_chart_share"] == pytest.approx(0.33, abs=0.01) and ext["has_table_share"] == pytest.approx(0.33, abs=0.01)
    assert "heuristic" in r["method"]["has_chart"] and "heuristic" in r["method"]["has_table"]
