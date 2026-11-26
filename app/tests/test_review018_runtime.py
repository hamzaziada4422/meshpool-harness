"""Fixes from the 0.0.18 pre-release review (agent and runtime slice)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import docread as DR  # noqa: E402
from meshpool_harness.agent import extras as EX  # noqa: E402


def test_utf16_without_a_bom_is_read_as_utf16_not_as_utf8_with_nuls():
    for enc in ("utf-16-le", "utf-16-be"):
        assert DR.decode_text("ab,cd\nef,gh\n".encode(enc)) == "ab,cd\nef,gh\n"
    assert DR.decode_text("naïve,€\n".encode("utf-8")) == "naïve,€\n"          # UTF-8 unchanged
    assert DR.decode_text("caf\xe9\n".encode("cp1252")) == "café\n"           # and the cp1252 fallback


def test_the_reader_cache_key_changes_with_the_reader_version(tmp_path, monkeypatch):
    f = tmp_path / "a.csv"
    f.write_text("x\n")
    k = DR._cache_key(f)
    monkeypatch.setattr(DR, "READER_VERSION", DR.READER_VERSION + 1)
    assert DR._cache_key(f) != k                    # an upgrade re-reads files an older reader cached


def test_nested_instructions_may_import_from_anywhere_in_a_non_git_project(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "style.md").write_text("Use British spelling.\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "AGENTS.md").write_text("Rules for sub.\n@../docs/style.md\n")
    out = EX.nested_instructions(tmp_path, "sub/x.py", set())
    assert "Rules for sub." in out and "British spelling" in out
