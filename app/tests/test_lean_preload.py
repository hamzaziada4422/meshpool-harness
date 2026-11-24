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


def test_a_scratch_file_write_is_allowed_but_is_not_project_progress(tmp_path):
    from meshpool_harness.core import tools as T
    ws = T.Workspace(str(tmp_path), str(tmp_path / "run"))
    d = ws._scratch({})["TMPDIR"]
    e0 = ws.edits
    ws.apply_patch(f"*** Begin Patch\n*** Add File: {d}/check.py\n+print(1)\n*** End Patch")
    ws.write_file(f"{d}/check2.py", "print(2)\n")
    assert ws.edits == e0 and not ws.turn_files                       # written, but nothing counted as progress
    ws.write_file("real.py", "x = 1\n")
    assert ws.edits == e0 + 1


def test_calls_after_a_failed_edit_in_the_same_turn_are_skipped(tmp_path):
    import json
    from tests.test_turnloop_caps import _agent
    replies = iter([
        {"tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "apply_patch", "arguments": json.dumps(
                {"input": "*** Begin Patch\n*** Update File: missing.py\n@@\n-x\n+y\n*** End Patch"})}},
            {"id": "b", "type": "function", "function": {"name": "shell", "arguments": json.dumps(
                {"command": ["bash", "-lc", "echo ran > ran.txt"]})}}]},
        {"content": "done"},
    ])
    a = _agent(tmp_path, lambda n, b: next(replies))
    a.send("go")
    tool = {m["tool_call_id"]: m["content"] for m in a.messages if m.get("role") == "tool"}
    assert str(tool["a"]).startswith("ERROR") or "Failed" in str(tool["a"])
    assert tool["b"].startswith("Not run: an edit earlier in this turn failed")
    assert not (a.root / "ran.txt").exists()


def _ws(tmp_path):
    from meshpool_harness.core.tools import Workspace
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "a.py").write_text("x = 1\n")
    return Workspace(str(proj), str(tmp_path / "run")), proj


def test_rereading_an_unchanged_file_is_answered_from_memory(tmp_path):
    ws, proj = _ws(tmp_path)
    first = ws.shell(["bash", "-lc", "cat a.py"])
    assert "x = 1" in first
    again = ws.shell(["bash", "-lc", "cat a.py"])
    assert "unchanged since you printed it" in again and "x = 1" not in again
    (proj / "a.py").write_text("x = 2\n")
    assert "x = 2" in ws.shell(["bash", "-lc", "cat a.py"])     # changed: a real read


def test_preloaded_file_is_not_printed_again(tmp_path):
    from meshpool_harness.agent import preload
    ws, proj = _ws(tmp_path)
    preload.build(proj, "fix a.py", seen=lambda p, t: ws.remember_text(p, t, "the <workspace> block at the start"))
    out = ws.shell(["bash", "-lc", "cat a.py"])
    assert "the <workspace> block" in out and "x = 1" not in out
    assert "x = 1" in ws.shell(["bash", "-lc", "cat a.py | head -5"])   # anything but a plain whole-file read runs


def test_recheck_reruns_the_last_check_after_a_patch(tmp_path):
    ws, proj = _ws(tmp_path)
    ws.recheck = True
    ws.shell(["bash", "-lc", "python3 -c 'import a; print(\"x is\", a.x)'"])
    out = ws.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 5\n*** End Patch")
    assert out.startswith("Success.") and "Meshpool re-ran your last check" in out and "x is 5" in out


def test_recheck_skips_writes_and_off_by_default(tmp_path):
    ws, proj = _ws(tmp_path)
    ws.shell(["bash", "-lc", "python3 -c 'print(1)'"])
    assert "re-ran" not in ws.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n*** End Patch")
    ws2, _ = _ws(tmp_path / "second")
    ws2.recheck = True
    ws2.shell(["bash", "-lc", "python3 -c 'print(1)' > out.txt"])
    assert "re-ran" not in ws2.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 3\n*** End Patch")
