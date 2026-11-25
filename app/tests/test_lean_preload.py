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


def test_a_reread_is_printed_again_once_the_text_has_left_the_history(tmp_path):
    from meshpool_harness.agent.core import Agent
    (tmp_path / "a.py").write_text("def total_price(items):\n    return sum(item.price * item.qty for item in items)\n")
    a = Agent(str(tmp_path), mode="auto", emit=lambda e: None)
    text = (tmp_path / "a.py").read_text()
    a.ws.remember_text(tmp_path / "a.py", text, "an earlier read")
    a.messages.append({"role": "tool", "tool_call_id": "t", "content": "[exit 0]\n" + text})
    assert "is unchanged since" in a.ws._reread("cat a.py", None)          # still in the conversation: pointer
    a.messages[-1]["content"] = "[output trimmed]"                         # compaction dropped it
    assert a.ws._reread("cat a.py", None) == ""                            # printed for real again
def test_recheck_reruns_the_last_check_after_a_patch(tmp_path):
    ws, proj = _ws(tmp_path)
    ws.recheck = True
    ws.get_mode = lambda: "auto"                    # full access: commands already run unasked
    ws.shell(["bash", "-lc", "python3 -c 'import a; print(\"x is\", a.x)'"])
    out = ws.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 5\n*** End Patch")
    assert out.startswith("Success.") and "Meshpool re-ran your last check" in out and "x is 5" in out
    ws.get_mode = lambda: "ask"                     # asking before commands: a re-run would be an unasked run
    assert "re-ran" not in ws.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 5\n+x = 6\n*** End Patch")


def test_recheck_skips_writes_and_off_by_default(tmp_path):
    ws, proj = _ws(tmp_path)
    ws.shell(["bash", "-lc", "python3 -c 'print(1)'"])
    assert "re-ran" not in ws.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n*** End Patch")
    ws2, _ = _ws(tmp_path / "second")
    ws2.recheck = True
    ws2.get_mode = lambda: "auto"
    ws2.shell(["bash", "-lc", "python3 -c 'print(1)' > out.txt"])
    assert "re-ran" not in ws2.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 3\n*** End Patch")


def test_recheck_skips_a_slow_check(tmp_path, monkeypatch):
    """broad2 (swe_sympy): a slow test run re-run after every patch cost 2.3x Codex's time."""
    ws, proj = _ws(tmp_path)
    ws.recheck = True
    ws.get_mode = lambda: "auto"
    ws.shell(["bash", "-lc", "python3 -c 'import a; print(\"x is\", a.x)'"])
    ws._last_check()["secs"] = 25.0                 # as if that check took 25 s
    assert "re-ran" not in ws.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 5\n*** End Patch")
    monkeypatch.setenv("MH_RECHECK_MAX_S", "0")     # 0: re-run whatever the check's time
    assert "x is 6" in ws.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 5\n+x = 6\n*** End Patch")


def test_large_codebase_gets_the_read_first_line(tmp_path, monkeypatch):
    (tmp_path / "s").mkdir()
    small = preload.build(_repo(tmp_path / "s"), "Fix parse() in roman.py")
    assert "large codebase" not in small
    big = tmp_path / "b"
    big.mkdir()
    for i in range(preload.MAX_LIST + 5):
        (big / f"m{i}.py").write_text("x = 1\n")
    out = preload.build(big, "Fix m3")
    assert "This is a large codebase" in out and out.endswith("</workspace>")
    monkeypatch.setenv("MH_LARGE_REPO_NOTE", "0")
    assert "large codebase" not in preload.build(big, "Fix m3")


def test_step_budget_counts_every_step_and_notes_on_crossing(tmp_path, monkeypatch):
    from meshpool_harness.agent.core import Agent
    a = Agent(str(tmp_path), mode="auto", emit=lambda e: None)
    monkeypatch.setattr(a, "_lean", lambda: True)
    monkeypatch.setenv("MH_STEP_NOTE", "3")
    monkeypatch.setenv("MH_STEP_WRAP", "8")
    g = {}
    notes = [a._step_budget(g) for _ in range(9)]
    assert "Stop probing" in notes[2] and "Finish now" in notes[5] and "Tools are off" in notes[7]
    assert sum(bool(n) for n in notes) == 3 and g["hard_wrap"]
    monkeypatch.delenv("MH_STEP_NOTE")
    monkeypatch.delenv("MH_STEP_WRAP")
    assert not any(a._step_budget({}) for _ in range(50))          # off by default


def test_cycle_note_after_repeated_failing_patch_rounds(tmp_path, monkeypatch):
    """MH_CYCLE_NOTE=n: after n project edits each followed by a failing check, one note to find the cause."""
    from meshpool_harness.agent.core import Agent
    a = Agent(str(tmp_path), mode="auto")
    call = {"function": {"name": "shell", "arguments": "{}"}}
    g = {}
    assert a._cycle_note(call, g) == ""                           # off by default
    monkeypatch.setenv("MH_CYCLE_NOTE", "3")
    for e in (1, 2):
        a.ws.edits = e
        a.ws.commands = [{"cmd": "python -m pytest t.py", "code": 1, "after_edit": e}]
        assert a._cycle_note(call, g) == ""
    a.ws.commands = [{"cmd": "ls", "code": 2, "after_edit": 2}]     # not a check
    assert a._cycle_note(call, g) == ""
    a.ws.edits = 3
    a.ws.commands = [{"cmd": "python -m pytest t.py", "code": 1, "after_edit": 3}]
    assert "Stop patching" in a._cycle_note(call, g)
    assert a._cycle_note(call, g) == ""                           # once per turn


def test_large_repo_style_codex_switch(tmp_path, monkeypatch):
    """A code chat in a large repo runs Codex 0.171's request by default (swe7); MH_LARGE_REPO_STYLE=core keeps the lean
    core, =codex the plain Codex design (no lean prompt, preload or re-check)."""
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.hrouter import profiles as PR
    from meshpool_harness.hrouter.route import Route
    small, big = tmp_path / "s", tmp_path / "b"
    small.mkdir()
    big.mkdir()
    (small / "a.py").write_text("x = 1\n")
    for i in range(preload.MAX_LIST + 5):
        (big / f"m{i}.py").write_text("x = 1\n")
    assert preload.is_large(big) and not preload.is_large(small)

    def chat(root):
        a = Agent(str(root), mode="auto", profile="code", emit=lambda e: None)
        a.set_route(Route(spec="code.debug", family="code", sub="debug", base="code", profile="core",
                          settings=PR.settings("core")), "fix it")
        return a
    d = chat(big)                                               # default: Codex 0.171's request on a large repo
    assert d._c171() and not d._lean() and chat(small)._lean() and not chat(small)._c171()
    assert d.tool_schemas()[0]["function"]["name"] == "exec_command"
    monkeypatch.setenv("MH_LARGE_REPO_STYLE", "core")
    assert chat(big)._lean() and chat(small)._lean()           # =core: both lean
    monkeypatch.setenv("MH_LARGE_REPO_STYLE", "codex")
    b = chat(big)
    assert chat(small)._lean() and not b._lean() and b._codex() and b._bare_codex()
    assert b.messages[0]["content"] != chat(small).messages[0]["content"]     # Codex's own prompt, not the lean one
