"""Harness profile "c171": the request Codex CLI 0.171 sends (docs/results/codex171-request), reproduced for code chats.
Its base instructions (prompt-171.md), one tool (exec_command) with Codex's result format, and its environment context
as a user message of its own. No network and no model calls (the transport is scripted)."""
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import extras as EX  # noqa: E402
from meshpool_harness.agent import tui_session as TS  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.checkpoints import Checkpoints  # noqa: E402
from meshpool_harness.core import compat as CP  # noqa: E402
from meshpool_harness.core import permrules as PR  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402
from meshpool_harness.hrouter import profiles as PROF  # noqa: E402
from meshpool_harness.hrouter.route import Route  # noqa: E402

CAPTURE = Path(core.__file__).resolve().parents[3] / "docs" / "results" / "codex171-request"
PROMPT = Path(core.__file__).resolve().parents[2] / "defaults" / "engines" / "codex" / "prompt-171.md"
MASK = re.compile(r"\AChunk ID: [0-9a-f]{6}\nWall time: \d+\.\d{4} seconds\n")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", "none")
    monkeypatch.setenv("MH_SANDBOX", "off")
    for var in ("MH_ENGINE_STYLE", "MH_HARNESS_PROFILE", "MH_PROFILE", "MH_ALLOW", "MH_DENY", "MH_STEP_MAX_TOKENS"):
        monkeypatch.delenv(var, raising=False)


def _proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir(exist_ok=True)
    return p


def _route(profile="c171", spec="code.debug"):
    return Route(spec=spec, family="code", sub=spec.split(".")[-1], base="code", profile=profile,
                 settings=PROF.settings(profile))


def _agent(root, mode="auto", approve=None, emit=None):
    a = Agent(str(root), mode=mode, approve=approve, emit=emit or (lambda e: None))
    a.set_route(_route(), "fix the failing test")
    return a


def _script(agent, replies):
    seq = list(replies)
    agent.bodies = []

    def fake(base, key, body, stream):
        agent.bodies.append(json.loads(json.dumps(body)))
        msg = seq.pop(0)
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, bool(msg.get("content"))
    agent._post = fake


def _call(args, cid="c1", name="exec_command"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _ws(root):
    return T.Workspace(str(root), str(root / ".run"))


def _outs(agent):
    return [m["content"] for m in agent.messages if m["role"] == "tool"]


# ---------- the profile -------------------------------------------------------------------------------------------
def test_c171_is_opt_in_and_extends_codex(monkeypatch):
    s = PROF.settings("c171")
    assert s["codex171"] is True and s["engine_style"] == "codex" and s["lean_core"] is False
    assert (s["plan"], s["review"], s["mutation"]) == ("off", False, False)
    for name in ("full", "codex", "core", "lean"):
        assert PROF.settings(name)["codex171"] is False
    shipped = json.loads((Path(core.__file__).resolve().parents[2] / "defaults" / "profiles.json").read_text())
    assert shipped["default"] == "core" and shipped["profiles"]["c171"] == {"extends": "codex", "codex171": True}
    assert PROF.forced() == ""
    monkeypatch.setenv("MH_PROFILE", "c171")
    assert PROF.forced() == "c171"
    monkeypatch.delenv("MH_PROFILE")
    monkeypatch.setenv("MH_HARNESS_PROFILE", "c171")
    assert PROF.forced() == "c171"


def test_mh_profile_c171_reaches_a_chat_routed_on_its_first_message(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_PROFILE", "c171")
    monkeypatch.delenv("MH_AUTO_ROUTE", raising=False)      # conftest turns it off for unit tests
    a = Agent(str(_proj(tmp_path)), mode="auto", emit=lambda e: None)
    _script(a, [{"role": "assistant", "content": "Done."}])
    a.send("Fix the off-by-one bug in pager.py")
    assert a.route is not None and a.route.profile == "c171" and a._c171()
    assert [t["function"]["name"] for t in a.bodies[0]["tools"]] == ["exec_command"]


def test_other_profiles_are_unchanged(tmp_path):
    for profile, tools in (("codex", ["shell", "apply_patch", "update_plan"]), ("core", ["shell", "apply_patch",
                                                                                          "update_plan"])):
        a = Agent(str(_proj(tmp_path)), mode="auto")
        a.set_route(_route(profile), "x")
        assert not a._c171() and [t["function"]["name"] for t in a.tool_schemas()] == tools
    w = Agent(str(_proj(tmp_path)), mode="auto", profile="work")
    w.set_route(Route(spec="data", family="data", sub="", base="work", profile="c171",
                      settings=PROF.settings("c171")), "x")
    assert not w._c171()                                   # code chats only


# ---------- the prompt --------------------------------------------------------------------------------------------
def test_prompt_is_the_captured_text_with_only_the_documented_changes(tmp_path):
    captured = (CAPTURE / "01-developer.txt").read_text(encoding="utf-8")
    ours = core._c171_prompt()
    diff = [ln for ln in difflib.ndiff(captured.splitlines(keepends=True), ours.splitlines(keepends=True))
            if ln[:2] in ("- ", "+ ")]
    assert sorted(diff) == sorted([
        "- You are a coding agent running in the Codex CLI, a terminal-based coding assistant. Codex CLI is an open "
        "source project led by OpenAI. You are expected to be precise, safe, and helpful.\n",
        "+ You are a coding agent running in Meshpool, a coding assistant that works in the user's project folder on "
        "their own computer. Meshpool's agent is built on the design of Codex CLI, an open source project led by "
        "OpenAI. You are expected to be precise, safe, and helpful.\n",
        "- Within this context, Codex refers to the open-source agentic coding interface (not the old Codex language "
        "model built by OpenAI).\n",
        "- \n",
    ])
    assert ours.endswith("hunks of a file.\n\n") and captured.endswith("hunks of a file.\n\n")
    text = PROMPT.read_text(encoding="utf-8")
    assert text.startswith("<!--\nNOTICE:") and "Apache License, Version 2.0" in text and "0.171" in text
    assert text.endswith(ours) and "<!--" not in ours and "NOTICE" not in ours
    sp = _agent(_proj(tmp_path)).messages[0]["content"]
    assert sp == ours                                       # nothing else in the system prompt


def test_project_instructions_follow_the_prompt(tmp_path):
    root = _proj(tmp_path)
    (root / "AGENTS.md").write_text("Use tabs.\n")
    sp = _agent(root).messages[0]["content"]
    assert sp == (core._c171_prompt().rstrip("\n") + f"\n\n# AGENTS.md instructions for {root.resolve()}\n\n"
                  "<INSTRUCTIONS>\nUse tabs.\n\n</INSTRUCTIONS>")


# ---------- the tool ----------------------------------------------------------------------------------------------
def test_tool_schema_equals_the_captured_exec_command():
    captured = next(t for t in json.loads((CAPTURE / "tools.json").read_text()) if t.get("name") == "exec_command")
    ours = T.SCHEMAS["exec_command"]
    assert ours["type"] == "function" and captured["strict"] is False
    assert ours["function"] == {k: captured[k] for k in ("name", "description", "parameters")}
    assert json.dumps(ours["function"]["parameters"]) == json.dumps(captured["parameters"])   # key order too


@pytest.mark.parametrize("sample,code", [("success", 0), ("failure", 128)])
def test_result_matches_the_captured_outputs_byte_for_byte(tmp_path, sample, code):
    want = json.loads((CAPTURE / "outputs.json").read_text())[sample][0]["output"]
    body = want.split("\nOutput:\n", 1)[1]
    root = _proj(tmp_path)
    (root / "out.txt").write_text(body, encoding="utf-8")
    got = _ws(root).exec_command(f"cat out.txt; exit {code}")
    assert MASK.match(got) and MASK.match(want)
    assert MASK.sub("", got) == MASK.sub("", want)


def test_truncated_result_matches_the_captured_long_output(tmp_path):
    want = json.loads((CAPTURE / "outputs.json").read_text())["long"][0]["output"]
    body = want.split("\nOutput:\n", 1)[1]
    head, tail = body.split("…19415 chars truncated…")
    head = head.split("\n\n", 1)[1]                           # after the warning and the line count
    newlines = 1010 - (head + tail).count("\n")               # the original had 1010 lines and 29,415 bytes
    filler = ("x" * 28 + "\n") * newlines
    middle = filler + "y" * (19415 - len(filler))
    original = head + middle + tail
    assert len(original.encode()) == 29415 and original.count("\n") == 1010
    root = _proj(tmp_path)
    (root / "long.txt").write_text(original, encoding="utf-8")
    got = _ws(root).exec_command("cat long.txt")
    assert MASK.sub("", got) == MASK.sub("", want)


def test_truncation_budget_head_tail_and_characters():
    out = T.exec_result("a" * 30 + "\n" + "b" * 30 + "\n", 0, 1.5, max_output_tokens=5, chunk_id="abc123")
    assert out == ("Chunk ID: abc123\nWall time: 1.5000 seconds\nProcess exited with code 0\nOriginal token count: 16\n"
                   "Output:\nWarning: truncated output (original token count: 16)\nTotal output lines: 2\n\n"
                   "aaaaaaaaaa…42 chars truncated…bbbbbbbbb\n")
    short = T.exec_result("hi\n", 1, 0.0, chunk_id="000000")
    assert short == "Chunk ID: 000000\nWall time: 0.0000 seconds\nProcess exited with code 1\nOriginal token count: 1\nOutput:\nhi\n"
    big = "z" * 50000
    capped = T.exec_result(big, 0, 0, max_output_tokens=100000)             # the 10,000-byte policy still applies
    assert "…40000 chars truncated…" in capped and capped.count("z") == 10000
    assert "…" not in T.exec_result("q" * 9999, 0, 0)
    wide = T.exec_result("é" * 20, 0, 0, max_output_tokens=4)            # 40 bytes, 16-byte budget
    text = wide.split("Output:\n", 1)[1]
    assert text.endswith("é" * 4 + "…12 chars truncated…" + "é" * 4)
    assert T.exec_exit_code(capped) == 0 and T.exec_exit_code("[exit 0]\n") is None


def test_exec_command_runs_in_workdir_with_scratch_tmpdir_and_the_users_python(tmp_path, monkeypatch):
    root = _proj(tmp_path)
    (root / "sub").mkdir()
    app = str(Path(core.__file__).resolve().parents[2])
    monkeypatch.setenv("PYTHONPATH", app + ":" + str(tmp_path / "mine"))
    a = _agent(root)
    assert a.ws.engine_libs is False
    out = a.ws.exec_command('pwd; echo "T=$TMPDIR"; echo "P=$PYTHONPATH"', workdir="sub")
    lines = out.split("Output:\n", 1)[1].splitlines()
    assert lines[0] == str((root / "sub").resolve())
    scratch = a.ws.__dict__["_scratch_dir"]
    assert lines[1] == f"T={scratch}" and "meshpool-scratch-" in scratch
    assert lines[2] == "P=" + str(tmp_path / "mine")                        # Meshpool's app folder is not on it
    with pytest.raises(T.ToolError, match="workdir not found"):
        a.ws.exec_command("true", workdir="nope")
    slow = a.ws.exec_command("sleep 5", yield_time_ms=1000)
    assert "Process exited with code 124" in slow and "timed out after 1s" in slow
    assert a.ws.commands[0]["cmd"].startswith("pwd;")                      # in the command log like bash and shell


def test_apply_patch_heredoc_through_exec_command_is_applied(tmp_path):
    root = _proj(tmp_path)
    (root / "f.txt").write_text("1\n")
    w = _ws(root)
    out = w.exec_command("apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: f.txt\n@@\n-1\n+one\n*** End Patch\nEOF")
    assert "Process exited with code 0" in out and out.endswith("Success. Updated the following files:\nM f.txt\n")
    assert (root / "f.txt").read_text() == "one\n"


# ---------- permissions, hooks, destructive commands -----------------------------------------------------------
def test_permission_families_cover_exec_command():
    assert "exec_command" in PR.TOOL_FAMILY["bash"] and "exec_command" in PR.TOOL_FAMILY["shell"]
    assert PR.commands_of("exec_command", {"cmd": "ls -la"}) == ["ls -la"]
    assert core._destructive({"cmd": "rm -rf build"}) and not core._destructive({"cmd": "ls"})
    assert core._rule_hit(["bash:rm *"], "exec_command", {"cmd": "ls && rm -rf x"}, deny=True) == "bash:rm *"
    assert core._rule_hit(["bash"], "exec_command", {"cmd": "ls"}, deny=True) == "bash"
    assert core._rule_hit(["Bash"], "exec_command", {"cmd": "ls"}) == "Bash"                # any letter case
    for matcher, args, hit in (("Bash", {"cmd": "ls"}, True), ("bash:rm *", {"cmd": "rm -rf x"}, True),
                               ("bash:rm *", {"cmd": "ls"}, False), ("Edit|Write", {"cmd": "ls"}, False),
                               ("exec_command", {"cmd": "ls"}, True)):
        assert EX._tool_matches(matcher, "exec_command", args) is hit
    rep = TS._denied({"title": "destructive", "kind": "bash", "detail": json.dumps({"cmd": "rm -rf  build"})})
    assert rep == {"kind": "bash", "title": "Run this destructive command?", "destructive": True,
                   "command": "rm -rf build"}


def test_deny_rule_and_hook_block_exec_command(tmp_path, monkeypatch):
    root = _proj(tmp_path)
    monkeypatch.setenv("MH_DENY", "bash:touch *")
    a = _agent(root)
    _script(a, [_call({"cmd": "touch denied.txt"}), {"role": "assistant", "content": "ok"}])
    a.send("go")
    assert not (root / "denied.txt").exists() and "saved rule 'bash:touch *'" in _outs(a)[0]
    monkeypatch.delenv("MH_DENY")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "hooks.json").write_text(json.dumps({"PreToolUse": [
        {"matcher": "Bash", "command": f"{sys.executable} -c \"import sys; print('no shells', file=sys.stderr); sys.exit(2)\""}]}))
    b = _agent(root)
    _script(b, [_call({"cmd": "touch hooked.txt"}), {"role": "assistant", "content": "ok"}])
    b.send("go")
    assert not (root / "hooked.txt").exists() and "Blocked by a hook" in _outs(b)[0] and "no shells" in _outs(b)[0]


def test_ask_edits_and_auto_modes(tmp_path):
    root = _proj(tmp_path)
    asked = []

    def approve(kind, title, detail):
        asked.append((kind, title, detail))
        return "no", "not now"
    a = _agent(root, mode="ask", approve=approve)
    assert a._permit("exec_command", {"cmd": "ls -la"}) == (True, "")              # a read needs no question
    _script(a, [_call({"cmd": "touch x.txt", "workdir": "."}), {"role": "assistant", "content": "ok"}])
    a.send("go")
    assert asked == [("bash", "Run this command?", "cd . && touch x.txt")]
    assert not (root / "x.txt").exists() and "The user denied this action" in _outs(a)[0]
    e = _agent(root, mode="edits", approve=approve)
    assert e._permit("exec_command", {"cmd": "make"})[0] is False and asked[-1][2] == "make"   # commands still ask
    f = _agent(root, mode="auto")
    assert f._permit("exec_command", {"cmd": "touch y"}) == (True, "")
    assert f._permit("exec_command", {"cmd": "rm -rf build"})[0] is False        # no one to approve a destructive one
    seen = []
    g = _agent(root, mode="auto", approve=lambda k, t, d: (seen.append((t, d)) or ("no", "")))
    g._permit("exec_command", {"cmd": "git reset --hard", "workdir": "sub"})
    assert seen == [("Run this destructive command?", "cd sub && git reset --hard")]


# ---------- a whole turn ------------------------------------------------------------------------------------------
def test_scripted_turn_writes_with_a_heredoc_and_undo_restores(tmp_path):
    root = _proj(tmp_path)
    (root / "pager.py").write_text("def pages(n):\n    return n\n")
    events = []
    a = _agent(root, emit=events.append)
    cp = a.checkpoint = Checkpoints(str(root), tmp_path / "store", mode="journal", shared=tmp_path / "shared")
    heredoc = ("cat > pager.py <<'EOF'\ndef pages(n):\n    return max(1, n)\nEOF\n"
               "cat > test_pager.py <<'EOF'\nfrom pager import pages\nassert pages(0) == 1\nEOF\npython test_pager.py")
    _script(a, [_call({"cmd": heredoc}), {"role": "assistant", "content": "Fixed `pages` and checked it."}])
    cp.begin_turn("fix pager", len(a.messages), 0)
    edits0 = a.ws.edits
    assert a.send("Make pages() return at least 1.") == "Fixed `pages` and checked it."
    cp.end_turn()
    assert (root / "pager.py").read_text() == "def pages(n):\n    return max(1, n)\n"
    assert (root / "test_pager.py").exists() and a.ws.edits == edits0 + 1
    out = _outs(a)[0]
    assert re.match(r"Chunk ID: [0-9a-f]{6}\nWall time: \d+\.\d{4} seconds\nProcess exited with code 0\n"
                    r"Original token count: 0\nOutput:\n\Z", out)
    for body in a.bodies:                                     # every request: exactly Codex 0.171's one tool
        assert [t["function"]["name"] for t in body["tools"]] == ["exec_command"]
    first = a.bodies[0]["messages"]
    assert [m["role"] for m in first] == ["system", "user", "user"]
    assert first[0]["content"] == core._c171_prompt()
    env = first[1]["content"]
    cap = (CAPTURE / "03-user.txt").read_text(encoding="utf-8")
    shape = lambda s: re.sub(r"<(cwd|root|current_date|timezone)>[^<]*<", r"<\1>~<", s)   # noqa: E731
    assert shape(env) == shape(cap) and f"<cwd>{a.root}</cwd>" in env and f"<root>{a.root}</root>" in env
    assert first[2]["content"] == "Make pages() return at least 1."            # nothing rides on the task
    second = a.bodies[1]["messages"]
    assert second[:3] == first and second[3]["tool_calls"][0]["function"]["name"] == "exec_command"
    assert [m for m in a.messages if m["role"] == "user"] == [{"role": "user", "content": "Make pages() return at least 1."}]
    assert not any("[Harness:" in str(m.get("content")) or "<workspace>" in str(m.get("content")) for m in first)
    assert not a.ws.recheck and a._bare_codex() and not a._lean()
    assert a._grade_gate({}, {}, "Done.") == (False, "Done.")                    # no grader
    start = next(e for e in events if e["type"] == "tool_start")
    assert start["name"] == "exec_command" and start["summary"].startswith("cat > pager.py")
    turn = cp.turns()[0]
    assert sorted(c["path"] for c in turn["changed"]) == ["pager.py", "test_pager.py"]
    cp.revert_to_before(turn["n"])
    assert (root / "pager.py").read_text() == "def pages(n):\n    return n\n" and not (root / "test_pager.py").exists()


def test_failing_command_and_the_loop_guard(tmp_path, monkeypatch):
    root = _proj(tmp_path)
    (root / "a.txt").write_text("x\n")
    a = _agent(root)
    _script(a, [_call({"cmd": "cat a.txt"}, f"c{i}") for i in range(4)] + [_call({"cmd": "exit 3"}, "c9"),
                                                                          {"role": "assistant", "content": "ok"}])
    a.send("go")
    outs = _outs(a)
    assert outs[0].endswith("Output:\nx\n") and outs[2].startswith("[Meshpool] Not run: you already made this call")
    assert "what the command printed" in outs[2]
    assert "Process exited with code 3" in outs[-1]
    assert a.trace[-1]["tool"] == "exec_command" and a.trace[-1]["ok"] is False
    assert a._FAILED.match(outs[-1]) and not a._FAILED.match(outs[0])


def test_environment_context_message_is_not_merged_into_the_task():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "<environment_context>\n</environment_context>"},
            {"role": "user", "content": "task"}, {"role": "user", "content": "more"}]
    out = CP.normalize_roles(msgs)
    assert [m["content"] for m in out] == ["s", "<environment_context>\n</environment_context>", "task\n\nmore"]
    strict = CP.normalize_roles(msgs, strict=True)                           # a strict template still gets one
    assert len(strict) == 1 and strict[0]["content"].index("<environment_context>") < strict[0]["content"].index("task")


def test_no_step_cap_for_whole_file_writes(tmp_path):
    a = _agent(_proj(tmp_path))
    assert a._body()["max_tokens"] > 4096
    b = Agent(str(_proj(tmp_path)), mode="auto")
    b.set_route(_route("codex"), "x")
    assert b._body()["max_tokens"] == 4096
