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
def test_cache_folder_is_gitignored_and_not_tracked():
    import subprocess
    assert "/cache/" in _text(".gitignore").splitlines()
    tracked = subprocess.run(["git", "ls-files", "cache"], cwd=ROOT, capture_output=True, text=True)
    if tracked.returncode == 0:                      # a source tarball has no git: the .gitignore line is the check
        assert tracked.stdout.strip() == ""
