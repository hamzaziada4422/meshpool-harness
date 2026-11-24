"""Lean code lane: the workspace preload (agent/preload.py) and edits in the task's own scratch folder."""
import os
import subprocess
from pathlib import Path

from meshpool_harness.agent import preload


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "roman.py").write_text("def parse(s):\n    return 0\n")
    (tmp_path / "test_roman.py").write_text("import roman\n")
    (tmp_path / "data.bin").write_bytes(b"\0\1\2")
    (tmp_path / "big.py").write_text("x = 1\n" * 5000)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_preload_lists_files_and_inlines_small_text(tmp_path):
    out = preload.build(_repo(tmp_path), "Fix parse() in roman.py")
    assert out.startswith("<workspace>") and out.endswith("</workspace>")
    assert "roman.py (" in out and "data.bin (" in out and "big.py (" in out
    assert '<file path="roman.py">\ndef parse(s):' in out
    assert '<file path="data.bin">' not in out          # binary: listed, not inlined
    assert '<file path="big.py">' not in out            # over the per-file cap
    assert "no commits yet" in out


def test_preload_named_file_first_and_budget(tmp_path):
    root = _repo(tmp_path)
    out = preload.build(root, "Fix parse() in roman.py")
    assert out.index('<file path="roman.py">') < out.index('<file path="test_roman.py">')
    tight = preload.build(root, "Fix parse() in roman.py", budget=10)
    assert "<file " not in tight and "roman.py (" in tight


def test_preload_off_switch_and_empty(tmp_path):
    assert preload.build(tmp_path, "x") == ""


def test_scratch_folder_is_writable(tmp_path):
    from meshpool_harness.core.tools import ToolError, Workspace
    proj = tmp_path / "proj"
    proj.mkdir()
    ws = Workspace(str(proj), str(tmp_path / "run"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    ws.__dict__["_scratch_dir"] = str(scratch)
    assert ws._path(str(scratch / "check.py"), write=True) == (scratch / "check.py").resolve()
    try:
        ws._path(str(tmp_path / "elsewhere.py"), write=True)
    except ToolError:
        pass
    else:
        raise AssertionError("a write outside the project and the scratch folder must be refused")
