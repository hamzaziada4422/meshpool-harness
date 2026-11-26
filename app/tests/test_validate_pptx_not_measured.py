"""Release review nit: when the deck opens but the measure step fails, P0 is "not measured" (not_run), not "fail"; a
deck that does not open still fails P0."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _deck(p: Path) -> Path:
    from pptx import Presentation
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[5]).shapes.title.text = "Revenue grew 12 percent"
    prs.save(p)
    return p


def test_a_deck_that_opens_but_was_not_measured_is_not_a_p0_failure(tmp_path, monkeypatch):
    from engines.forge import validate_pptx as VP

    def broken(*a, **k):
        raise RuntimeError("measure broke")
    monkeypatch.setattr(VP, "measure", broken)
    res = VP.validate(str(_deck(tmp_path / "d.pptx")), render=False)
    assert res["rules"]["P0"] == "not_run", res["rules"]["P0"]
    assert not any(f["rule"] == "P0" and f.get("status", "fail") == "fail" for f in res["failures"])


def test_a_deck_that_does_not_open_still_fails_p0(tmp_path):
    from engines.forge import validate_pptx as VP
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a zip")
    assert VP.validate(str(bad), render=False)["rules"]["P0"] == "fail"
