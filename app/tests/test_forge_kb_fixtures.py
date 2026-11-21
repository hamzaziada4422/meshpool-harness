"""The frozen forge_kb briefs (renderer-only A/B, docs/results/2026-11-20-forge-kb-baseline.md) stay usable: the split
is 8 dev and 4 test with no overlap, and every deck renders without errors or fallbacks."""
import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "forge_kb_briefs"


def _dirs():
    return sorted(d for d in FIX.iterdir() if d.is_dir())


def test_split_is_eight_dev_four_test_disjoint():
    sp = json.loads((FIX / "split.json").read_text())
    assert len(sp["dev"]) == 8 and len(sp["test"]) == 4
    assert not set(sp["dev"]) & set(sp["test"])
    assert sorted(sp["dev"] + sp["test"]) == [d.name for d in _dirs()]


@pytest.mark.parametrize("d", _dirs(), ids=lambda d: d.name)
def test_fixture_renders_without_errors(d, tmp_path):
    from engines.forge.render import render
    slides = json.loads((d / "slides.json").read_text())
    assert slides[0]["archetype"] == "cover" and slides[-1]["archetype"] in ("closing", "next_steps")
    r = render(slides, str(tmp_path / "deck.pptx"), carry=False, density="balanced", fix_labels=False)
    assert r["errors"] == []
    assert (d / "brief.md").read_text().count("\nF1:") == 1
