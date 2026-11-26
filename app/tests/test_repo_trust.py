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


# ---- security policy: SECURITY.md was three bullets with no contact, versions or threat model --------------------------
def test_security_policy_names_contact_versions_times_and_real_risks():
    s = _text("SECURITY.md")
    for heading in ("## Supported versions", "## Reporting a problem", "## Threat model", "## Running it safely"):
        assert heading in s
    assert "/security/advisories/new" in s                         # a private channel, not "the maintainers"
    assert "working days" in s                                     # a response time
    assert "0.0.x" in s
    for risk in ("shell commands", "mcp-auth.json", "MCP server", "container"):
        assert risk in s, risk


def test_issue_chooser_sends_security_reports_to_a_private_advisory():
    import yaml
    cfg = yaml.safe_load(_text(".github/ISSUE_TEMPLATE/config.yml"))
    links = [l["url"] for l in cfg.get("contact_links", [])]
    assert any(u.endswith("/hamzaziada4422/meshpool-harness/security/advisories/new") for u in links)


# ---- evidence: the headline numbers must recompute from stored raw rows -----------------------------------------------
def _verify_paired(*files: str, a: str = "MP", b: str = "codex") -> str:
    import subprocess
    cp = subprocess.run([sys.executable, str(ROOT / "testkit" / "verify_paired.py"), *[str(ROOT / f) for f in files],
                         "--a", a, "--b", b], capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stderr
    return cp.stdout


def test_readme_swe_bench_claim_recomputes_from_the_stored_rows():
    m = re.search(r"SWE-bench Verified \(SymPy\) (\d+) vs (\d+) solved at ([0-9.]+)x", _text("README.md"))
    assert m, "the README's SWE-bench claim changed shape; update this test with it"
    out = _verify_paired("docs/results/release016/swe_sympy/results.jsonl")
    assert re.search(rf"MP: solved {m.group(1)}/24\b", out)
    assert re.search(rf"codex: solved {m.group(2)}/24\b", out)
    assert f"cost per solved ratio {m.group(3)}x" in out
    bench = _text("docs/BENCHMARKS.md")
    assert "1c0b6df59a3928f51ebfde171c07dba5feedbbdf" in bench                       # the exact commit
    assert "verify_paired.py docs/results/release016/swe_sympy/results.jsonl" in bench


def test_readme_browser_claim_is_not_stated_as_measured():
    """No raw rows exist for the MiniWoB++ runs, so the README may not present them as a measured result."""
    readme = _text("README.md")
    row = next(l for l in readme.splitlines() if l.startswith("| **Browser use**"))
    assert "unverified" in row
    assert "$0.0009 per solved task against $0.0025" not in readme


def test_benchmarks_link_the_loop_history_branch():
    assert "/tree/loop-history" in _text("docs/BENCHMARKS.md")


# ---- doc/reality: one version number, one routing table ---------------------------------------------------------------
def test_version_agrees_across_pyproject_package_and_readme():
    """The badge reads the latest GitHub release; the release workflow refuses a tag that is not v<__init__ version>
    (test_ledger017_packaging). This pins the other three places to each other."""
    from meshpool_harness import __version__
    assert _pyproject()["project"]["version"] == __version__
    m = re.search(r"Meshpool is a beta \(currently ([0-9][^)]*)\)", _text("README.md"))
    assert m and m.group(1) == __version__


def test_routing_table_the_docs_name_exists_and_no_csv_contradicts_it():
    import subprocess
    assert "`app/defaults/routing.json`" in _text("docs/ROUTING.md")
    assert (ROOT / "app" / "defaults" / "routing.json").is_file()
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if tracked.returncode == 0:
        assert not [f for f in tracked.stdout.splitlines() if f.endswith("routing.csv")]
