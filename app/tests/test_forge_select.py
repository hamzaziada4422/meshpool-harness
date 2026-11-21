"""Best of N: candidates are built, graded and the best one becomes the build's deck."""
import json
from pathlib import Path

from engines.forge import select as SEL


class _Grader:
    def __init__(self, means):
        self.means = list(means)

    def chat(self, **kw):
        m = self.means.pop(0)
        body = {k: m for k in SEL.KEYS}
        body["worst"] = "thin charts"
        return {"choices": [{"message": {"content": json.dumps(body)}}]}


def test_best_of_keeps_the_best_graded_deck(tmp_path, monkeypatch):
    monkeypatch.setattr("engines.forge.review.render_pngs", lambda p, width_px=1100: [])

    def fake_build(brief, out_dir, client=None, task_id="", **kw):
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "deck.pptx").write_text(task_id)
        return {"pptx": str(d / "deck.pptx"), "rules": {}}

    r = SEL.best_of("brief", str(tmp_path), 3, fake_build, client=_Grader([6.0, 7.5, 6.8]))
    assert r["select"]["chosen"] == 2 and (tmp_path / "deck.pptx").read_text() == "forge-cand2"
    assert json.loads((tmp_path / "select.json").read_text())["chosen"] == 2
    assert (tmp_path / ".candidates" / "1" / "deck.pptx").exists()


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("MH_BEST_OF", raising=False)
    assert SEL.wanted() == 1
    monkeypatch.setenv("MH_BEST_OF", "3")
    assert SEL.wanted() == 3
