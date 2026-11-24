"""make_pdf draws real charts (K-01 office quality: the PDF report had a text chart only)."""
import sys
from pathlib import Path

import pytest

from meshpool_harness.agent import office_mcp_server as O

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from testkit.gradekit import pdf_chart_evidence  # noqa: E402

MD = """# Sales

| Region | Revenue |
|---|---|
| West | 13274.6 |
| East | 11537.7 |

```chart
{"type": "%s", "title": "Revenue by region", "labels": ["West", "East", "North", "South"],
 "values": [13274.6, 11537.7, 10723.2, 10030.3]}
```
"""


@pytest.mark.parametrize("kind", ["bar", "line", "pie"])
def test_chart_block_is_a_real_chart(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    msg, err = O.make_pdf(MD % kind, "report.pdf")
    assert not err, msg
    ev = pdf_chart_evidence(tmp_path / "report.pdf")
    assert ev["chart"], ev


def test_without_chart_block_no_chart(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    O.make_pdf("# Sales\n\nWest ████ 13274\n", "report.pdf")
    assert not pdf_chart_evidence(tmp_path / "report.pdf")["chart"]


def test_bad_chart_block_saves_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    msg, err = O.make_pdf('```chart\n{"labels": ["a", "b"], "values": [1]}\n```\n', "report.pdf")
    assert err and "one value per label" in msg
    assert not (tmp_path / "report.pdf").exists()


def test_image_embed(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    from PIL import ImageDraw
    im = Image.new("RGB", (400, 250), "white")
    dr = ImageDraw.Draw(im)
    for k, (h, c) in enumerate([(200, "navy"), (150, "orange"), (120, "green")]):
        dr.rectangle([40 + k * 110, 240 - h, 120 + k * 110, 240], fill=c)
    im.save(tmp_path / "chart.png")
    msg, err = O.make_pdf("# Sales\n\n![Revenue by region](chart.png)\n", "report.pdf")
    assert not err, msg
    assert pdf_chart_evidence(tmp_path / "report.pdf")["chart"]


def test_image_outside_project_refused(tmp_path, monkeypatch):
    from PIL import Image
    proj, other = tmp_path / "p", tmp_path / "o"
    proj.mkdir(), other.mkdir()
    Image.new("RGB", (40, 40), "red").save(other / "x.png")
    monkeypatch.setenv("MESHPOOL_PROJECT", str(proj))
    msg, err = O.make_pdf(f"![x]({other / 'x.png'})\n", "report.pdf")
    assert err and "outside" in msg


def test_tool_description_names_chart_block():
    assert "```chart" in O.TOOLS["make_pdf"][1]
