"""Repository trust checks: licence, no committed caches, security policy, version agreement.

These read files in the checkout. Each test names the gap it closes.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent

if sys.version_info >= (3, 11):
    import tomllib
else:                                    # pragma: no cover
    import tomli as tomllib


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _pyproject() -> dict:
    return tomllib.loads(_text("app/pyproject.toml"))


# ---- licence: the repo had no root LICENSE ---------------------------------------------------------------------------
def test_root_licence_is_apache_2_and_is_referenced():
    lic = _text("LICENSE")
    assert lic.lstrip().startswith("Apache License") and "Version 2.0, January 2004" in lic
    assert "END OF TERMS AND CONDITIONS" in lic
    assert _pyproject()["project"]["license"] == "Apache-2.0"
    notice = _text("NOTICE")
    assert "Apache License, Version 2.0" in notice and "THIRD_PARTY_NOTICES.md" in notice
    assert (ROOT / "THIRD_PARTY_NOTICES.md").exists()
    readme = _text("README.md")
    assert "](LICENSE)" in readme and "Apache License 2.0" in readme


def test_spdx_licence_string_needs_setuptools_77():
    """A `license = "<SPDX>"` string is rejected by setuptools before 77."""
    req = _pyproject()["build-system"]["requires"]
    m = next(re.match(r"setuptools>=(\d+)", r) for r in req if r.startswith("setuptools"))
    assert m and int(m.group(1)) >= 77


# ---- caches: cache/docread/ with document text was committed at the repo root ----------------------------------------
def _home(monkeypatch, tmp_path, meshpool_home):
    from meshpool_harness import config as C
    monkeypatch.setenv("MESHPOOL_HOME", str(meshpool_home))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(C.sys, "platform", "linux")


def _in_repo(p: Path) -> bool:
    p = p.resolve()
    return p == ROOT.resolve() or ROOT.resolve() in p.parents


def test_docread_cache_is_under_meshpool_home_not_the_repo(tmp_path, monkeypatch):
    from meshpool_harness import docread as D
    _home(monkeypatch, tmp_path, tmp_path / "home")
    d = D._cache_dir()
    assert d == tmp_path / "home" / "cache" / "docread"
    assert not _in_repo(d)


def test_docread_cache_leaves_a_checkout_used_as_meshpool_home(tmp_path, monkeypatch):
    """MESHPOOL_HOME pointing at the checkout (or a folder inside it) must not put document text where git sees it."""
    from meshpool_harness import docread as D
    for inside in (ROOT, ROOT / ".mhhome"):
        _home(monkeypatch, tmp_path, inside)
        d = D._cache_dir()
        assert not _in_repo(d), d
        assert d == tmp_path / "xdg" / "meshpool" / "cache" / "docread"


def test_reading_a_file_writes_its_cache_outside_the_repo(tmp_path, monkeypatch):
    from meshpool_harness import docread as D
    _home(monkeypatch, tmp_path, ROOT)
    monkeypatch.delenv("MH_DOC_CACHE", raising=False)
    f = tmp_path / "names.csv"
    f.write_text("name\nalice\nbob\n", encoding="utf-8")
    text = D.extract(f)
    assert "alice" in text
    key = D._cache_key(f, D.MAX, None, None, "auto", "auto")
    assert (tmp_path / "xdg" / "meshpool" / "cache" / "docread" / (key + ".txt")).read_text(encoding="utf-8") == text
    assert not (ROOT / "cache" / "docread" / (key + ".txt")).exists()


def test_no_cache_folder_outside_the_checkout_means_no_cache(tmp_path, monkeypatch):
    from meshpool_harness import config as C
    from meshpool_harness import docread as D
    monkeypatch.setenv("MESHPOOL_HOME", str(ROOT))
    monkeypatch.setattr(C, "user_home_default", lambda: ROOT / "user")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(ROOT))
    f = tmp_path / "a.csv"
    f.write_text("x\n1\n", encoding="utf-8")
    assert "1" in D.extract(f)                       # still reads; it just does not keep the text
    assert not (ROOT / "meshpool").exists() and not (ROOT / "user").exists()


def test_cache_folder_is_gitignored_and_not_tracked():
    import subprocess
    assert "/cache/" in _text(".gitignore").splitlines()
    tracked = subprocess.run(["git", "ls-files", "cache"], cwd=ROOT, capture_output=True, text=True)
    if tracked.returncode == 0:                      # a source tarball has no git: the .gitignore line is the check
        assert tracked.stdout.strip() == ""
