"""Tools a Claude Code user expects: grep with context, output modes, limits, case and multiline; edit with replace_all."""
import pytest

from meshpool_harness.core.tools import SCHEMAS, ToolError, Workspace


def _ws(tmp_path, files, read=True):
    ws = Workspace(str(tmp_path), str(tmp_path / "out"))
    for n, c in files.items():
        (tmp_path / n).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / n).write_text(c)
        if read:
            ws.read_file(n)
    return ws


SRC = "one\ntwo\nTARGET here\nfour\nfive\nsix\nseven\nTarget again\nnine\n"


def test_grep_default_is_unchanged(tmp_path):
    ws = _ws(tmp_path, {"a.txt": SRC})
    assert ws.grep("TARGET") == "a.txt:3:TARGET here"


def test_grep_context_lines_and_separators(tmp_path):
    ws = _ws(tmp_path, {"a.txt": SRC})
    out = ws.grep("(?i)target", context=1).splitlines()
    assert out == ["a.txt-2-two", "a.txt:3:TARGET here", "a.txt-4-four", "--",
                   "a.txt-7-seven", "a.txt:8:Target again", "a.txt-9-nine"]
    out = ws.grep("TARGET", before=2, after=0).splitlines()
    assert out == ["a.txt-1-one", "a.txt-2-two", "a.txt:3:TARGET here"]


def test_grep_output_modes_limit_and_case(tmp_path):
    ws = _ws(tmp_path, {"a.txt": SRC, "b.txt": "nothing\n", "sub/c.txt": "target\n"})
    assert sorted(ws.grep("target", ignore_case=True, output_mode="files_with_matches").splitlines()) == ["a.txt", "sub/c.txt"]
    assert sorted(ws.grep("target", ignore_case=True, output_mode="count").splitlines()) == ["a.txt:2", "sub/c.txt:1"]
    assert ws.grep("target") == "sub/c.txt:1:target"                       # case matters unless asked
    many = _ws(tmp_path / "m", {"x.txt": "hit\n" * 50})
    out = many.grep("hit", head_limit=5).splitlines()
    assert len(out) == 6 and out[-1].startswith("... more results truncated")
    with pytest.raises(ToolError):
        ws.grep("x", output_mode="nope")


def test_grep_multiline_pattern_spans_lines(tmp_path):
    ws = _ws(tmp_path, {"a.py": "def f():\n    return 1\n\ndef g():\n    return 2\n"})
    assert ws.grep(r"def f\(\):\n\s+return 1", multiline=True) == "a.py:1:def f():"


def test_the_grep_schema_offers_the_options():
    props = SCHEMAS["grep"]["function"]["parameters"]["properties"]
    assert {"context", "before", "after", "output_mode", "head_limit", "ignore_case", "multiline"} <= set(props)
    assert "replace_all" in SCHEMAS["edit"]["function"]["parameters"]["properties"]


def test_edit_replace_all_renames_everywhere_and_counts(tmp_path):
    ws = _ws(tmp_path, {"a.py": "old_name = 1\nprint(old_name)\nold_name += 1\n"})
    with pytest.raises(ToolError):
        ws.edit("a.py", old="old_name", new="new_name")                    # still refused without replace_all
    r = ws.edit("a.py", old="old_name", new="new_name", replace_all=True)
    assert (tmp_path / "a.py").read_text() == "new_name = 1\nprint(new_name)\nnew_name += 1\n"
    assert "3 occurrences" in r


def test_replace_all_needs_a_read_first_and_a_match(tmp_path):
    ws = _ws(tmp_path, {"a.py": "x = 1\nx = 2\n"}, read=False)
    with pytest.raises(ToolError, match="read"):
        ws.edit("a.py", old="x", new="y", replace_all=True)
    ws.read_file("a.py")
    with pytest.raises(ToolError):
        ws.edit("a.py", old="zzz", new="y", replace_all=True)
    assert (tmp_path / "a.py").read_text() == "x = 1\nx = 2\n"


def test_replace_all_works_inside_an_edits_list_and_with_one_match(tmp_path):
    ws = _ws(tmp_path, {"a.py": "a = 1\nb = a\nc = a\nkeep = 0\n"})
    ws.edit("a.py", edits=[{"old": "= a", "new": "= z", "replace_all": True}, {"old": "keep", "new": "stay"}])
    assert (tmp_path / "a.py").read_text() == "a = 1\nb = z\nc = z\nstay = 0\n"
    ws2 = _ws(tmp_path / "w", {"b.py": "only = 1\n"})
    ws2.edit("b.py", old="only", new="solo", replace_all=True)
    assert (tmp_path / "w" / "b.py").read_text() == "solo = 1\n"
