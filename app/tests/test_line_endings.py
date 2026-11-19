"""Edits keep each file's own line endings, BOM and bytes, on every platform (Python's text mode would turn every \\n
into CRLF on Windows and drop a CRLF file's endings on Mac and Linux)."""
from meshpool_harness.core import tools as T
from meshpool_harness.core.tools import Workspace


def _ws(tmp_path, name, data: bytes, read=True):
    (tmp_path / name).write_bytes(data)
    ws = Workspace(str(tmp_path), str(tmp_path / "out"))
    if read:
        ws.read_file(name)
    return ws


def test_crlf_file_stays_crlf_after_edit(tmp_path):
    ws = _ws(tmp_path, "a.py", b"a = 1\r\nb = 2\r\nc = 3\r\n")
    ws.edit("a.py", "b = 2", "b = 20\nb2 = 21")
    assert (tmp_path / "a.py").read_bytes() == b"a = 1\r\nb = 20\r\nb2 = 21\r\nc = 3\r\n"


def test_crlf_multi_line_old_matches_exactly(tmp_path):
    ws = _ws(tmp_path, "a.txt", b"one\r\ntwo\r\nthree\r\n")
    r = ws.edit("a.txt", "one\ntwo", "ONE\nTWO")
    assert "normalising" not in r                      # an exact match, not the fuzzy fallback
    assert (tmp_path / "a.txt").read_bytes() == b"ONE\r\nTWO\r\nthree\r\n"


def test_lf_file_stays_lf_even_on_windows(tmp_path, monkeypatch):
    ws = _ws(tmp_path, "a.py", b"a = 1\nb = 2\n")
    ws.edit("a.py", "a = 1", "a = 10")
    assert (tmp_path / "a.py").read_bytes() == b"a = 10\nb = 2\n"


def test_bom_kept(tmp_path):
    ws = _ws(tmp_path, "a.cs", b"\xef\xbb\xbfclass A {}\r\n")
    ws.edit("a.cs", "class A {}", "class B {}")
    assert (tmp_path / "a.cs").read_bytes() == b"\xef\xbb\xbfclass B {}\r\n"


def test_non_utf8_bytes_survive_an_edit(tmp_path):
    ws = _ws(tmp_path, "a.txt", b"caf\xe9\nx = 1\n")             # Latin-1, not UTF-8
    ws.edit("a.txt", "x = 1", "x = 2")
    assert (tmp_path / "a.txt").read_bytes() == b"caf\xe9\nx = 2\n"
    assert "�" in ws.read_file("a.txt")                      # the model sees a replacement character


def test_write_file_keeps_existing_crlf_and_new_files_are_lf(tmp_path):
    ws = _ws(tmp_path, "a.txt", b"x\r\ny\r\n")
    ws.write_file("a.txt", "p\nq\n")
    assert (tmp_path / "a.txt").read_bytes() == b"p\r\nq\r\n"
    ws.write_file("new.txt", "p\nq\n")
    assert (tmp_path / "new.txt").read_bytes() == b"p\nq\n"
    ws.write_file("a.txt", "r\n", append=True)
    assert (tmp_path / "a.txt").read_bytes() == b"p\r\nq\r\nr\r\n"


def test_write_src_never_translates(tmp_path):
    p = tmp_path / "f"
    T.write_src(p, "a\nb\n")
    assert p.read_bytes() == b"a\nb\n"
    T.write_src(p, "a\nb\n", "\r\n", True)
    assert p.read_bytes() == b"\xef\xbb\xbfa\r\nb\r\n"
    assert T.read_src(p) == ("a\nb\n", "\r\n", True)


def test_apply_patch_then_edit_is_not_stale_on_crlf(tmp_path):
    ws = _ws(tmp_path, "a.txt", b"one\r\ntwo\r\n")
    ws.apply_patch("*** Begin Patch\n*** Update File: a.txt\n@@\n-one\n+ONE\n*** End Patch")
    ws.edit("a.txt", "two", "TWO")
    assert (tmp_path / "a.txt").read_bytes() == b"ONE\r\nTWO\r\n"
