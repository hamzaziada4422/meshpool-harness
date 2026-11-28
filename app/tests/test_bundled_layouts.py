"""The packaged app finds what the release build puts in each bundle: the real folder and file names of the Mac apps
(Apple silicon and Intel), the Windows install and the Linux download, built here as stub files. A lookup that only
matches a name the build does not produce (MinGit's mingw64 after it became ucrt64, python3 on Windows, the
LibreOffice fonts outside Linux) fails here instead of on a customer's computer."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from meshpool_harness import bundled, config

ROOT = Path(__file__).resolve().parents[2]

# Where packaging/build_all.py puts each bundle's root (config.APP.parent), and the files a lookup needs there.
RES = {"mac-arm64": "Meshpool.app/Contents/Resources", "mac-x64": "Meshpool.app/Contents/Resources",
       "win-x64": "Meshpool", "linux-x64": "Meshpool"}
LO_FONTS = {"mac-arm64": "LibreOffice.app/Contents/Resources/fonts/truetype",
            "mac-x64": "LibreOffice.app/Contents/Resources/fonts/truetype",
            "win-x64": "share/fonts/truetype", "linux-x64": "share/fonts/truetype"}
GIT = {"mac-arm64": ["git/bin/git"], "mac-x64": ["git/bin/git"], "linux-x64": ["git/bin/git"],
       # MinGit 2.56.1 (the pinned zip): cmd/git.exe, ucrt64/bin/git.exe, usr/bin/sh.exe (+ bash.exe from the build)
       "win-x64": ["git/cmd/git.exe", "git/ucrt64/bin/git.exe", "git/usr/bin/sh.exe", "git/usr/bin/bash.exe"]}
PLATS = list(RES)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _touch(p: Path, data: bytes = b"stub") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """layout(plat) -> the bundle root, with config.APP pointing at its app folder and the app counted as packaged."""
    def make(plat):
        root = tmp_path / plat / RES[plat]
        (root / "app").mkdir(parents=True)
        (root / ("runtime" if plat == "win-x64" else "python")).mkdir()
        for f in GIT[plat]:
            _touch(root / f)
        for face in ("Carlito-Regular", "Carlito-Bold", "LiberationSans-Regular", "LiberationSans-Bold"):
            _touch(root / "app" / "vendor" / "libreoffice" / LO_FONTS[plat] / f"{face}.ttf")
        monkeypatch.setattr(config, "APP", root / "app")
        monkeypatch.setattr(bundled, "packaged", lambda: True)
        return root
    return make


def _as(monkeypatch, plat):
    """The lookups' view of the platform. os.name is set only around calls that build no new Path (pathlib picks
    WindowsPath from it)."""
    monkeypatch.setattr(sys, "platform", {"mac-arm64": "darwin", "mac-x64": "darwin", "win-x64": "win32"}.get(plat, "linux"))


# ---------------------------------------------------------------- Git: MinGit's programs folder is on PATH
@pytest.mark.parametrize("plat", PLATS)
def test_the_folder_holding_the_real_git_is_put_on_path(layout, monkeypatch, plat):
    """bundled.git_dirs listed MinGit's mingw64/bin, but MinGit 2.56.1 (the pinned zip) has ucrt64/bin and no
    mingw64: on Windows only the cmd/ wrapper and usr/bin went on PATH, never the folder git.exe and its helpers
    (git-remote-https, git-credential-manager) are in."""
    root = layout(plat)
    _as(monkeypatch, plat)
    monkeypatch.setattr(os, "name", "nt" if plat == "win-x64" else "posix")
    dirs, git = bundled.git_dirs(), bundled.bundled_git()
    monkeypatch.undo()
    rel = [d.relative_to(root).as_posix() for d in dirs]
    want = {"win-x64": ["git/cmd", "git/ucrt64/bin", "git/usr/bin"]}.get(plat, ["git/bin"])
    assert rel == want
    assert git and Path(git).is_file() and root in Path(git).parents


def test_an_older_mingit_with_mingw64_still_works(tmp_path, monkeypatch):
    root = tmp_path / "Meshpool"
    for f in ("git/cmd/git.exe", "git/mingw64/bin/git.exe", "git/usr/bin/bash.exe"):
        _touch(root / f)
    monkeypatch.setattr(bundled, "root", lambda: root)
    monkeypatch.setattr(os, "name", "nt")
    rel = [d.relative_to(root).as_posix() for d in bundled.git_dirs()]
    monkeypatch.undo()
    assert rel == ["git/cmd", "git/mingw64/bin", "git/usr/bin"]


def test_the_pinned_mingit_has_the_folders_the_app_looks_for():
    """The pinned MinGit's programs folder is one bundled.git_dirs knows (checked against the zip, 2.56.1)."""
    bt = _load("mp_bundled_tools", ROOT / "packaging" / "bundled_tools.py")
    assert "2.56.1" in bt.MINGIT_URL                    # the zip whose layout GIT["win-x64"] copies
    assert "ucrt64" in bundled.MINGIT_PROGRAM_DIRS


# ---------------------------------------------------------------- the git panel uses the bundled Git
@pytest.mark.parametrize("plat", PLATS)
def test_the_git_panel_uses_the_git_inside_the_app(layout, monkeypatch, plat):
    """workbench.git_exe took /opt/homebrew/bin/git or /usr/local/bin/git first, so on a Mac with Homebrew the Git
    panel, clone and branch checks ran the customer's Git while everything else ran the app's own."""
    from meshpool_harness.ui import workbench
    root = layout(plat)
    _as(monkeypatch, plat)
    real = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: str(p) in ("/opt/homebrew/bin/git", "/usr/local/bin/git") or real(p))
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: "/usr/local/bin/" + n)
    monkeypatch.setattr(os, "name", "nt" if plat == "win-x64" else "posix")
    got = workbench.git_exe()
    monkeypatch.undo()
    assert got and root in Path(got).parents, got


# ---------------------------------------------------------------- python3 on Windows
def test_the_windows_runtime_answers_to_python3(tmp_path, monkeypatch):
    """Skills and models type python3 (transcribe-audio, notebooks). python-build-standalone's Windows Python has only
    python.exe and pythonw.exe, so on Windows python3 ran the Microsoft Store stub in WindowsApps. The build copies
    python.exe to python3.exe beside it."""
    ba = _load("mp_build_all_layouts", ROOT / "packaging" / "build_all.py")
    rt = tmp_path / "deps" / "rt-win" / "python"
    _touch(rt / "python.exe", b"MZpython")
    _touch(rt / "pythonw.exe", b"MZpythonw")
    _touch(rt / "python312.dll", b"MZdll")
    monkeypatch.setitem(ba.RT, "win", rt)
    root = tmp_path / "Meshpool"
    root.mkdir()
    ba.win_runtime(root)
    for name in ("python.exe", "pythonw.exe", "python3.exe"):
        assert (root / "runtime" / name).is_file(), name
    assert (root / "runtime" / "python3.exe").read_bytes() == b"MZpython"


def test_a_windows_python_without_python_exe_stops_the_build(tmp_path):
    bt = _load("mp_bundled_tools", ROOT / "packaging" / "bundled_tools.py")
    with pytest.raises(bt.BundleError, match="python.exe"):
        bt.add_python_aliases(tmp_path)


def test_the_alias_moves_the_runtime_so_updates_take_the_installer():
    """The python3.exe alias is made in bundled_tools.py, a file the release marks as runtime: a Windows copy that
    updates gets the full installer (with python3.exe), not only the in-place code update."""
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "--runtime-file packaging/bundled_tools.py" in wf
    assert "add_python_aliases" in (ROOT / "packaging" / "build_all.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the fonts decks are measured with
@pytest.mark.parametrize("plat", PLATS)
def test_office_faces_are_measured_with_the_fonts_in_the_apps_libreoffice(layout, monkeypatch, plat):
    """Calibri, Arial, Cambria and Times New Roman are measured with their metric twins (Carlito, Liberation,
    Caladea). Those were found only through fc-match or /usr/share/fonts (Linux), so on macOS and Windows every deck
    in those faces was measured at a guessed width, although the app's LibreOffice carries the files."""
    from engines.forge import common as CM
    root = layout(plat)
    _as(monkeypatch, plat)
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: None)          # no fc-match on macOS or Windows
    lo = root / "app" / "vendor" / "libreoffice" / LO_FONTS[plat]
    CM.font_file.cache_clear()
    try:
        assert CM.font_file("Calibri", False, False, (("Calibri", "Carlito"),)) == (str(lo / "Carlito-Regular.ttf"), False)
        assert CM.font_file("Arial", True, False, (("Arial", "Liberation Sans"),)) == (
            str(lo / "LiberationSans-Bold.ttf"), False)
        assert CM.font_file("Liberation Sans", False, False, ()) == (str(lo / "LiberationSans-Regular.ttf"), True)
        path, _ = CM.font_file("Segoe UI", False, False, ())                 # the engine's own Selawik still wins
        assert Path(path).name == "Selawik-Regular.ttf"
    finally:
        CM.font_file.cache_clear()


def test_a_source_checkout_without_a_bundled_libreoffice_is_unchanged(tmp_path, monkeypatch):
    from engines.forge import common as CM
    monkeypatch.setattr(config, "APP", tmp_path / "app")
    assert CM._bundled_office_font("Carlito", "") is None


# ---------------------------------------------------------------- LibreOffice's fonts from the Windows MSI
@pytest.mark.parametrize("fonts_dir", ["Fonts", "Windows/Fonts", "PFiles/Fonts"])
def test_the_msi_fonts_reach_share_fonts_truetype_wherever_the_unpack_put_them(tmp_path, fonts_dir):
    """Release 0.0.20: msiextract leaves the fonts in Fonts/, msiexec /a (the Windows build) elsewhere under TARGETDIR.
    The Windows smoke test failed with no Carlito. Every .ttf and .otf left outside the LibreOffice folder is copied."""
    bt = _load("mp_bundled_tools", ROOT / "packaging" / "bundled_tools.py")
    out, dest = tmp_path / "x", tmp_path / "dest"
    for f in ("Carlito-Regular.ttf", "LiberationSans-Bold.ttf", "readme.txt"):
        _touch(out / fonts_dir / f)
    (dest / "share" / "fonts" / "truetype").mkdir(parents=True)
    (dest / "share" / "fonts" / "truetype" / "LiberationSans-Bold.ttf").write_text("keep")
    assert bt.collect_msi_fonts(out, dest) == 1
    have = sorted(p.name for p in (dest / "share" / "fonts" / "truetype").iterdir())
    assert have == ["Carlito-Regular.ttf", "LiberationSans-Bold.ttf"]
    assert (dest / "share" / "fonts" / "truetype" / "LiberationSans-Bold.ttf").read_text() == "keep"
