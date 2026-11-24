"""0.0.13: audit and view pictures go to a temp folder outside the project, not into "Meshpool outputs"."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import browser_mcp_server as BM  # noqa: E402


def test_pictures_do_not_land_in_the_project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    shots = tmp_path / "shots"
    monkeypatch.setenv("MESHPOOL_PROJECT", str(proj))
    monkeypatch.setenv("MH_SHOTS_DIR", str(shots))
    for sub in ("ui-audit", "views"):
        d = BM._outputs_dir(sub)
        assert d.is_dir() and shots in d.parents and sub == d.name
        assert proj not in d.parents
    assert list(proj.iterdir()) == []                           # nothing left behind in the user's folder


def test_old_folders_are_cleaned(tmp_path, monkeypatch):
    shots = tmp_path / "shots"
    old = shots / "abc" / "999999"
    old.mkdir(parents=True)
    t = time.time() - 3 * 24 * 3600
    os.utime(old, (t, t))
    monkeypatch.setenv("MH_SHOTS_DIR", str(shots))
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    monkeypatch.setattr(BM, "_pruned", False)
    BM._outputs_dir("views")
    assert not old.exists()
