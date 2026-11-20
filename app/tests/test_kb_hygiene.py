"""The repo is public: no slide knowledge base content may land in it (reference decks, template files, library
items, thumbnails). These checks catch the usual ways it would slip in."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg", ".emf", ".wmf"}


def _tracked() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        return [ROOT / p for p in out.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):          # not a git checkout (a source zip): walk instead
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


def test_no_large_pptx_in_repo():
    big = [str(p.relative_to(ROOT)) for p in _tracked()
           if p.suffix.lower() in (".pptx", ".potx", ".ppt") and p.exists() and p.stat().st_size > 1_000_000]
    assert not big, f"PowerPoint files over 1 MB are not allowed in the repo (knowledge base content?): {big}"


def test_no_images_in_forge_data():
    data = ROOT / "app" / "engines" / "forge" / "data"
    imgs = [str(p.relative_to(ROOT)) for p in _tracked()
            if data in p.parents and p.suffix.lower() in IMAGES]
    assert not imgs, f"no images under app/engines/forge/data (numbers only): {imgs}"


def test_no_decks_pdfs_or_thumbnails_in_fixtures():
    fx = ROOT / "app" / "tests" / "fixtures"
    bad = [str(p.relative_to(ROOT)) for p in _tracked()
           if fx in p.parents and p.suffix.lower() in IMAGES | {".pptx", ".potx", ".pdf", ".ppt"}]
    assert not bad, f"test fixtures must be synthetic text or generated in the test, not files: {bad}"


def test_env_kb_is_ignored():
    gi = (ROOT / ".gitignore").read_text()
    assert ".env.kb" in gi
