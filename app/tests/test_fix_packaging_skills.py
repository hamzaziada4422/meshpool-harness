"""Regression checks for packaging and shipped-skill fixes: every default SKILL.md is strict YAML, no raw ~~placeholders
are left in skill bodies, secret and generated paths stay ignored, and the runtime packages the code imports are declared."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "app" / "defaults" / "skills"


def test_every_default_skill_front_matter_is_strict_yaml():
    yaml = pytest.importorskip("yaml")
    bad = []
    for p in sorted(SKILLS.glob("*/SKILL.md")):
        m = re.match(r"---\r?\n(.*?)\r?\n---", p.read_text(encoding="utf-8"), re.S)
        try:
            d = yaml.safe_load(m.group(1))
            assert isinstance(d, dict) and d.get("name") == p.parent.name and d.get("description")
        except Exception:  # noqa: BLE001
            bad.append(p.parent.name)
    assert not bad, bad


def test_no_raw_tilde_placeholders_in_skill_instructions():
    left = [p.parent.name for p in SKILLS.glob("*/SKILL.md") if "~~" in p.read_text(encoding="utf-8")]
    assert not left, left


def test_runtime_dependencies_are_declared():
    need = ["psutil", "pypdf", "pypdfium2", "pdfminer.six", "xlrd", "duckdb", "tomli"]
    for f in ("app/pyproject.toml", "app/requirements.txt", "packaging/requirements-cli.txt"):
        text = (ROOT / f).read_text(encoding="utf-8").lower()
        assert all(n in text for n in need), (f, [n for n in need if n not in text])


def test_generated_and_secret_paths_are_gitignored():
    ig = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for line in ("/pi/", ".env", "app/sessions/", ".pytest-cache/", "app/Meshpool outputs/"):
        assert line in ig, line


def test_testkit_has_no_personal_paths():
    hits = [str(p) for p in (ROOT / "testkit").rglob("*")
            if p.is_file() and p.suffix in {".json", ".py", ".sh", ".md"}
            and "/Users/hamzaziada" in p.read_text(encoding="utf-8", errors="ignore")]
    assert not hits, hits


def test_install_ps1_forwards_its_arguments():
    assert (ROOT / "install.ps1").read_text(encoding="utf-8").rstrip().endswith("} @args")
