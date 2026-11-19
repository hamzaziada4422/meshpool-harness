import pytest

from meshpool_harness.core.tools import ToolError, Workspace


def _ws(tmp_path, name, content):
    ws = Workspace(str(tmp_path), str(tmp_path / "out"))
    (tmp_path / name).write_text(content)
    ws.read_file(name)
    return ws


def test_old_with_read_file_prefixes_is_stripped(tmp_path):
    ws = _ws(tmp_path, "a.py", "x = 1\ny = 2\nz = 3\n")
    old = "    2  y = 2\n    3  z = 3"
    new = "    2  y = 20\n    3  z = 30"
    assert ws.edit("a.py", old, new) == "edited a.py"
    assert (tmp_path / "a.py").read_text() == "x = 1\ny = 20\nz = 30\n"


def test_prefixed_old_with_plain_new(tmp_path):
    ws = _ws(tmp_path, "a.py", "x = 1\ny = 2\n")
    ws.edit("a.py", "    1  x = 1\n    2  y = 2", "x = 5\ny = 6")
    assert (tmp_path / "a.py").read_text() == "x = 5\ny = 6\n"


def test_trailing_whitespace_mismatch_is_matched(tmp_path):
    ws = _ws(tmp_path, "a.py", "def f():   \n    return 1  \n\nprint(f())\n")
    r = ws.edit("a.py", "def f():\n    return 1", "def f():\n    return 2")
    assert r == "edited a.py (matched after normalising whitespace)"
    assert (tmp_path / "a.py").read_text() == "def f():\n    return 2\n\nprint(f())\n"


def test_indentation_mismatch_reindents_new(tmp_path):
    src = "class A:\n    def m(self):\n        if True:\n            return 1\n        return 0\n"
    ws = _ws(tmp_path, "a.py", src)
    old = "if True:\n    return 1"                    # model dropped the 8-space indent
    new = "if True:\n    return 2\nelse:\n    return 3"
    r = ws.edit("a.py", old, new)
    assert "matched after normalising whitespace" in r
    assert (tmp_path / "a.py").read_text() == (
        "class A:\n    def m(self):\n        if True:\n            return 2\n        else:\n            return 3\n        return 0\n")


def test_tabs_versus_spaces(tmp_path):
    ws = _ws(tmp_path, "a.txt", "start\n\tone\n\t\ttwo\nend\n")
    r = ws.edit("a.txt", "    one\n        two", "    one\n        three")
    assert "normalising" in r
    assert (tmp_path / "a.txt").read_text() == "start\n    one\n        three\nend\n"


def test_ambiguous_normalised_match_is_not_applied(tmp_path):
    src = "def a():\n    x = 1\n\ndef b():\n        x = 1\n"
    ws = _ws(tmp_path, "a.py", src)
    with pytest.raises(ToolError) as e:
        ws.edit("a.py", "x = 1  \n", "x = 2\n")
    assert "after normalising whitespace" in str(e.value) and "lines 2, 5" in str(e.value)
    assert (tmp_path / "a.py").read_text() == src


def test_no_match_shows_closest_region(tmp_path):
    body = "\n".join(f"line{i} = {i}" for i in range(1, 40)) + "\n"
    ws = _ws(tmp_path, "a.py", body)
    with pytest.raises(ToolError) as e:
        ws.edit("a.py", "line20 = 21\nline21 = 21", "line20 = 0")
    msg = str(e.value)
    assert "not found" in msg and "line-number prefix" in msg
    assert "Closest text in the file (lines 20-27):" in msg
    assert "   20  line20 = 20" in msg and "   21  line21 = 21" in msg
    assert (tmp_path / "a.py").read_text() == body


def test_multiple_exact_occurrences_list_line_numbers(tmp_path):
    ws = _ws(tmp_path, "a.txt", "dup\nx\ndup\ny\nz\ndup\n")
    with pytest.raises(ToolError) as e:
        ws.edit("a.txt", "dup", "once")
    assert "3 times" in str(e.value) and "lines 1, 3, 6" in str(e.value)


def test_occurrence_lines_capped_at_ten(tmp_path):
    ws = _ws(tmp_path, "a.txt", "dup\n" * 15)
    with pytest.raises(ToolError) as e:
        ws.edit("a.txt", "dup", "once")
    assert "lines 1, 2, 3, 4, 5, 6, 7, 8, 9, 10" in str(e.value) and "11" not in str(e.value).split("lines")[1]


def test_syntax_check_still_rejects_fuzzy_edit(tmp_path):
    src = "def f(x):   \n    return (x +\n            1)\n"
    ws = _ws(tmp_path, "a.py", src)
    with pytest.raises(ToolError) as e:
        ws.edit("a.py", "def f(x):\n    return (x +", "def f(x:\n    return (x +")
    assert "stop parsing" in str(e.value)
    assert (tmp_path / "a.py").read_text() == src


def test_noop_and_read_before_edit_unchanged(tmp_path):
    ws = Workspace(str(tmp_path), str(tmp_path / "out"))
    (tmp_path / "a.txt").write_text("a\n")
    with pytest.raises(ToolError, match="identical"):
        ws.edit("a.txt", "a", "a")
    (tmp_path / "a.txt").write_text("a\na\n")        # unread: allowed only when `old` matches exactly once
    with pytest.raises(ToolError, match="read a.txt before editing"):
        ws.edit("a.txt", "a", "b")
