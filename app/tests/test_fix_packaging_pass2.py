"""Pass 2 packaging fixes: --version, login --help, olefile declared, the PDF measurer's readable error, honest README."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_version_flag(capsys):
    from meshpool_harness import __version__, cli
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"meshpool {__version__}"


def test_login_help_does_not_prompt(capsys):
    from meshpool_harness import cli
    assert cli.main(["login", "--help"]) == 0
    assert "usage: meshpool login" in capsys.readouterr().out.lower()


def test_olefile_is_declared_everywhere():
    for f in ("app/pyproject.toml", "app/requirements.txt", "packaging/requirements-cli.txt"):
        assert "olefile" in (ROOT / f).read_text(encoding="utf-8")


def test_measure_pdf_without_pymupdf_gives_a_readable_error(monkeypatch):
    from engines.forge import measure_pdf
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    with pytest.raises(RuntimeError, match="pip install pymupdf"):
        measure_pdf.measure("x.pdf")


def test_readme_does_not_promise_bundled_ocr():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "with offline OCR" not in text and "pip install rapidocr onnxruntime" in text
