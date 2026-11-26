"""SR-01: LibreOffice run by the agent's shell inside the sandbox crashed on its first start.

The sandbox keeps ~/.config read-only, and LibreOffice writes its user profile there at every start, so
`soffice --headless --convert-to pdf deck.pptx` died with a DeploymentException ("LibreOffice crashed")."""
import os
import shutil
import sys
from pathlib import Path

import pytest

from meshpool_harness.core import sandbox as SB


def test_linux_wrap_gives_office_a_writable_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_SANDBOX", "1")
    monkeypatch.delenv("UserInstallation", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    out = SB.wrap(["bash", "-c", "ls"], tmp_path / "proj", platform="linux", which=lambda n: "/usr/bin/" + n)
    assert "--setenv" in out
    i = out.index("--setenv")
    assert out[i + 1] == "UserInstallation"
    prof = Path(out[i + 2][len("file://"):])
    assert prof.is_dir() and str(prof).startswith(str(home / ".cache"))
    binds = [out[j + 1] for j, x in enumerate(out) if x == "--bind"]
    assert str(prof) in binds
    assert out[-3:] == ["bash", "-c", "ls"]


def test_user_setting_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_SANDBOX", "1")
    monkeypatch.setenv("UserInstallation", "file:///somewhere/else")
    out = SB.wrap(["bash", "-c", "ls"], tmp_path, platform="linux", which=lambda n: "/usr/bin/" + n)
    assert "--setenv" not in out


def test_mac_wrap_sets_the_profile_too(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_SANDBOX", "1")
    monkeypatch.delenv("UserInstallation", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    out = SB.wrap(["bash", "-c", "ls"], tmp_path, platform="darwin", which=lambda n: "/usr/bin/" + n)
    assert out[:2] == ["sandbox-exec", "-p"] and out[3] == "/usr/bin/env" and out[4].startswith("UserInstallation=")
    assert "sandbox-office-profile" in out[2] and out[-3:] == ["bash", "-c", "ls"]


def _deck(path: Path) -> None:
    from pptx import Presentation
    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[1])
    s.shapes.title.text = "Q3 Quarterly Review"
    s.placeholders[1].text = "Revenue up 12 percent"
    p.save(str(path))


def test_sandboxed_soffice_converts_a_deck_on_the_first_try(monkeypatch, tmp_path):
    """Through the same path the agent's shell uses (Workspace.exec_command), with the user's real HOME."""
    soffice = shutil.which("soffice")
    if not sys.platform.startswith("linux") or not shutil.which("bwrap") or not soffice:
        pytest.skip("needs bubblewrap and LibreOffice")
    if os.environ.get("MH_ALLOW_SYSTEM_SOFFICE") != "1":
        pytest.skip("system LibreOffice not allowed (MH_ALLOW_SYSTEM_SOFFICE=1)")
    from meshpool_harness.core import tools as T
    monkeypatch.setenv("MH_SANDBOX", "1")
    monkeypatch.delenv("UserInstallation", raising=False)
    home = Path.home()
    if str(home.resolve()).startswith("/tmp"):
        pytest.skip("HOME is under /tmp, which the sandbox keeps writable")
    proj = tmp_path / "proj"
    proj.mkdir()
    _deck(proj / "deck.pptx")
    ws = T.Workspace(str(proj), str(tmp_path / "out"))
    out = ws.exec_command("soffice --headless --convert-to pdf --outdir . deck.pptx 2>&1; echo rc=$?",
                          yield_time_ms=120000)
    assert "DeploymentException" not in out and "Application Error" not in out, out
    assert (proj / "deck.pdf").is_file() and (proj / "deck.pdf").stat().st_size > 1000, out
