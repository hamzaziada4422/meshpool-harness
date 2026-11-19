"""The "codex" engine style: Codex's apply_patch, shell and update_plan, its base instructions, and its loop (no review,
mutation or plan rounds), under MeshPool's routing. No network and no model calls (the transport is scripted)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core import applypatch as AP  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402
from meshpool_harness.hrouter import profiles as PR  # noqa: E402
from meshpool_harness.hrouter.route import Route, addon  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", "none")
    monkeypatch.delenv("MH_ENGINE_STYLE", raising=False)
    monkeypatch.delenv("MH_HARNESS_PROFILE", raising=False)


def ws(tmp_path):
    return T.Workspace(str(tmp_path), str(tmp_path / ".run"))


def patch(*body):
    return "*** Begin Patch\n" + "\n".join(body) + "\n*** End Patch"


# ---------- apply_patch: parser and applier ------------------------------------------------------------------------
def test_update_with_context_header_and_unchanged_lines(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n\ndef g():\n    x = 1\n    return x\n")
    out = ws(tmp_path).apply_patch(patch("*** Update File: a.py", "@@ def g():", "     x = 1", "-    return x",
                                         "+    return x + 1"))
    assert out == "Success. Updated the following files:\nM a.py"
    assert (tmp_path / "a.py").read_text() == "def f():\n    return 1\n\ndef g():\n    x = 1\n    return x + 1\n"


def test_add_delete_move_and_several_files(tmp_path):
    (tmp_path / "old.txt").write_text("one\ntwo\n")
    (tmp_path / "gone.txt").write_text("bye\n")
    (tmp_path / "keep.txt").write_text("a\nb\n")
    w = ws(tmp_path)
    out = w.apply_patch(patch("*** Add File: new/dir/n.txt", "+hello", "+", "+world",
                              "*** Delete File: gone.txt",
                              "*** Update File: old.txt", "*** Move to: moved/o.txt", "@@", "-two", "+2",
                              "*** Update File: keep.txt", "@@", " a", "+a2", " b"))
    assert out == ("Success. Updated the following files:\nA new/dir/n.txt\nM moved/o.txt\nM keep.txt\nD gone.txt")
    assert (tmp_path / "new/dir/n.txt").read_text() == "hello\n\nworld\n"
    assert not (tmp_path / "gone.txt").exists() and not (tmp_path / "old.txt").exists()
    assert (tmp_path / "moved/o.txt").read_text() == "one\n2\n"
    assert (tmp_path / "keep.txt").read_text() == "a\na2\nb\n"
    assert w.edits == 5 and str((tmp_path / "new/dir/n.txt").resolve()) in w.created


def test_context_matches_after_whitespace_and_punctuation_differences(tmp_path):
    (tmp_path / "w.py").write_text("if x:\n\treturn  \u201cq\u201d \n\nprint(1)  \n")
    ws(tmp_path).apply_patch(patch("*** Update File: w.py", "@@", " if x:", '-\treturn  "q"', '+\treturn "r"'))
    assert (tmp_path / "w.py").read_text() == 'if x:\n\treturn "r"\n\nprint(1)  \n'
    (tmp_path / "i.py").write_text("class A:\n        def m(self):\n            pass\n")     # indentation differs
    ws(tmp_path).apply_patch(patch("*** Update File: i.py", "@@", "-def m(self):", "+        def n(self):"))
    assert "def n(self)" in (tmp_path / "i.py").read_text()


def test_exact_match_beats_fuzzy_match():
    assert AP.seek_sequence(["x ", "x"], ["x"], 0, False) == 1
    assert AP.seek_sequence(["x", "y", "x"], ["x"], 1, False) == 2
    assert AP.seek_sequence(["a", "b", "c"], ["b", "c"], 0, True) == 1
    assert AP.seek_sequence(["a"], ["a", "b"], 0, False) is None


def test_failure_leaves_every_file_untouched(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    (tmp_path / "b.txt").write_text("x\ny\n")
    w = ws(tmp_path)
    with pytest.raises(T.ToolError) as e:
        w.apply_patch(patch("*** Add File: c.txt", "+new", "*** Update File: a.txt", "@@", "-one", "+1",
                            "*** Update File: b.txt", "@@", "-nothing like this", "+z"))
    assert "Failed to find expected lines in b.txt:\nnothing like this" in str(e.value)
    assert (tmp_path / "a.txt").read_text() == "one\ntwo\n" and (tmp_path / "b.txt").read_text() == "x\ny\n"
    assert not (tmp_path / "c.txt").exists() and w.edits == 0
    with pytest.raises(T.ToolError, match="Failed to find context 'def nope' in a.txt"):
        w.apply_patch(patch("*** Update File: a.txt", "@@ def nope", "-one", "+1"))
    with pytest.raises(T.ToolError, match="Failed to delete file zzz.txt"):
        w.apply_patch(patch("*** Delete File: zzz.txt"))
    with pytest.raises(T.ToolError, match="Failed to read file to update zzz.txt"):
        w.apply_patch(patch("*** Update File: zzz.txt", "@@", "-a", "+b"))


def test_a_write_failure_rolls_back(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("one\n")
    (tmp_path / "b.txt").write_text("two\n")
    real = Path.write_bytes
    calls = []

    def flaky(self, data):
        calls.append(self.name)
        if self.name == "b.txt":
            raise OSError("disk full")
        return real(self, data)
    monkeypatch.setattr(Path, "write_bytes", flaky)
    with pytest.raises(T.ToolError, match="restored"):
        ws(tmp_path).apply_patch(patch("*** Update File: a.txt", "@@", "-one", "+1",
                                       "*** Update File: b.txt", "@@", "-two", "+2"))
    monkeypatch.undo()
    assert (tmp_path / "a.txt").read_text() == "one\n" and (tmp_path / "b.txt").read_text() == "two\n"


def test_syntax_check_rejects_a_patch_that_breaks_code(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    (tmp_path / "n.txt").write_text("x\n")
    w = ws(tmp_path)
    with pytest.raises(T.ToolError) as e:
        w.apply_patch(patch("*** Update File: n.txt", "@@", "-x", "+y",
                            "*** Update File: m.py", "@@", " def f():", "-    return 1", "+    return (1"))
    assert "stop parsing" in str(e.value) and "m.py" in str(e.value) and "Nothing was written" in str(e.value)
    assert (tmp_path / "m.py").read_text() == "def f():\n    return 1\n" and (tmp_path / "n.txt").read_text() == "x\n"
    with pytest.raises(T.ToolError, match="stop parsing"):
        w.apply_patch(patch("*** Add File: bad.json", "+{oops"))
    assert not (tmp_path / "bad.json").exists()
    (tmp_path / "broken.py").write_text("def f(:\n")                  # already broken: a patch may still touch it
    w.apply_patch(patch("*** Update File: broken.py", "@@", "-def f(:", "+def f(:  # still"))


def test_chunk_without_old_lines_appends_and_eof_marker_and_heredoc_wrapper(tmp_path):
    (tmp_path / "t.txt").write_text("a\nb\nc\n")
    w = ws(tmp_path)
    w.apply_patch(patch("*** Update File: t.txt", "@@", "+z"))                      # nothing to match: at the end
    assert (tmp_path / "t.txt").read_text() == "a\nb\nc\nz\n"
    w.apply_patch(patch("*** Update File: t.txt", "@@", "-z", "+Z", "*** End of File"))
    assert (tmp_path / "t.txt").read_text() == "a\nb\nc\nZ\n"
    w.apply_patch("<<'EOF'\n" + patch("*** Update File: t.txt", " a", "-b", "+B") + "\nEOF")  # first chunk without @@
    assert (tmp_path / "t.txt").read_text() == "a\nB\nc\nZ\n"


def test_line_endings_and_missing_final_newline(tmp_path):
    (tmp_path / "c.txt").write_bytes(b"one\r\ntwo\r\nthree")
    ws(tmp_path).apply_patch(patch("*** Update File: c.txt", "@@", "-two", "+2", "+2b"))
    assert (tmp_path / "c.txt").read_bytes() == b"one\r\n2\r\n2b\r\nthree\r\n"


def test_parser_errors_follow_upstream_texts(tmp_path):
    w = ws(tmp_path)
    with pytest.raises(T.ToolError, match="The first line of the patch must be '\\*\\*\\* Begin Patch'"):
        w.apply_patch("hello")
    with pytest.raises(T.ToolError, match="The last line of the patch must be '\\*\\*\\* End Patch'"):
        w.apply_patch("*** Begin Patch\n*** Add File: a\n+x")
    with pytest.raises(T.ToolError, match="Invalid patch hunk on line 2"):
        w.apply_patch("*** Begin Patch\nnonsense\n*** End Patch")
    with pytest.raises(T.ToolError, match="is empty"):
        w.apply_patch(patch("*** Update File: a.txt"))
    with pytest.raises(T.ToolError, match="Every line should start with"):
        w.apply_patch(patch("*** Update File: a.txt", "@@", "oops"))
    with pytest.raises(T.ToolError, match="inside the workdir"):
        w.apply_patch(patch("*** Add File: ../escape.txt", "+x"))
    with pytest.raises(T.ToolError, match=".git"):
        w.apply_patch(patch("*** Add File: .git/hooks/pre-commit", "+x"))
    assert not list(tmp_path.parent.glob("escape.txt"))


# ---------- shell ----------------------------------------------------------------------------------------------------
def test_shell_runs_argv_string_workdir_and_timeout(tmp_path):
    (tmp_path / "sub").mkdir()
    w = ws(tmp_path)
    assert w.shell(["bash", "-lc", "echo hi && exit 3"]).startswith("[exit 3]\nhi")
    assert "[exit 0]" in w.shell(["printf", "%s", "a b"]) and "a b" in w.shell(["printf", "%s", "a b"])
    assert str(tmp_path / "sub") in w.shell("pwd", workdir="sub")
    out = w.shell(["bash", "-lc", "sleep 5"], timeout_ms=1200)
    assert out.startswith("[exit 124]") and "timed out after 2s" in out
    with pytest.raises(T.ToolError, match="workdir not found"):
        w.shell("true", workdir="nope")
    assert [c["cmd"] for c in w.commands][:1] == ["echo hi && exit 3"]


def test_shell_applies_a_patch_given_through_it(tmp_path):
    (tmp_path / "f.txt").write_text("1\n2\n")
    w = ws(tmp_path)
    body = patch("*** Update File: f.txt", "@@", "-1", "+one")
    assert w.shell(["apply_patch", body]).endswith("M f.txt")
    script = "apply_patch <<'EOF'\n" + patch("*** Add File: g.txt", "+g") + "\nEOF\n"
    assert w.shell(["bash", "-lc", script]).endswith("A g.txt")
    (tmp_path / "d").mkdir()
    script = "cd d && apply_patch <<EOF\n" + patch("*** Add File: h.txt", "+h") + "\nEOF"
    assert w.shell(["bash", "-lc", script]).endswith("A h.txt") and (tmp_path / "d/h.txt").read_text() == "h\n"
    assert (tmp_path / "f.txt").read_text() == "one\n2\n"
    with pytest.raises(T.ToolError, match="without explicit call to apply_patch"):
        w.shell(["bash", "-lc", body])
    assert T.validate("shell", {"command": "ls -la"}, ["shell"])["command"] == ["bash", "-lc", "ls -la"]
    assert T.validate("shell", {"command": ["ls"], "timeout_ms": 1500.5}, ["shell"])["timeout_ms"] == 1500.5
    with pytest.raises(T.ToolError, match="timeout_ms"):
        T.validate("shell", {"command": ["ls"], "timeout_ms": "soon"}, ["shell"])


# ---------- the agent in codex style --------------------------------------------------------------------------------
def _route(spec="code.debug", profile="codex", **kw):
    fam = spec.split(".")[0]
    return Route(spec=spec, family=fam, sub=spec.split(".")[-1], base="code" if fam == "code" else "work",
                 profile=profile, settings=PR.settings(profile), **kw)


def _script(agent, replies):
    seq = list(replies)
    agent.bodies = []

    def fake(base, key, body, stream):
        agent.bodies.append(json.loads(json.dumps(body)))
        msg = seq.pop(0)
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, bool(msg.get("content"))
    agent._post = fake


def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _agent(tmp_path, mode="auto", approve=None, route=True, emit=None, **kw):
    a = Agent(str(tmp_path), mode=mode, approve=approve, emit=emit or (lambda e: None), **kw)
    if route:
        a.set_route(_route(), "fix the failing test")
    return a


def test_the_codex_profile_is_defined_and_the_default_style_is_unchanged():
    s = PR.settings("codex")
    assert s["engine_style"] == "codex" and s["engine"] == "meshpool"
    assert (s["plan"], s["review"], s["mutation"], s["step_methods"]) == ("off", False, False, False)
    assert PR.settings("full")["engine_style"] == "meshpool" and PR.settings("lean-nope") is None
    assert PR._clean({"engine_style": "nonsense"})["engine_style"] == "meshpool"
    shipped = json.loads((Path(core.__file__).resolve().parents[2] / "defaults" / "profiles.json").read_text())
    assert shipped["profiles"]["codex"]["engine_style"] == "codex"
    assert shipped["default"] == "lean-stop" and "codex" not in shipped["assign"].values()   # not assigned anywhere


def test_system_prompt_is_codex_text_with_attribution_kept_in_the_file(tmp_path):
    a = _agent(tmp_path)
    sp = a.messages[0]["content"]
    assert sp.startswith("You are a coding agent running in MeshPool")
    assert "Please keep going until the query is completely resolved" in sp              # upstream wording
    assert "Use the `apply_patch` tool to edit files" in sp and "*** Begin Patch" in sp and "## `update_plan`" in sp
    assert "Codex CLI, an open source project led by OpenAI" in sp                       # says what it is built on
    assert "<!--" not in sp and "NOTICE" not in sp and "You are Meshpool" not in sp
    assert "[Harness:" not in sp and "todo_write" not in sp and "Be honest" not in sp
    d = Path(core.__file__).resolve().parents[2] / "defaults" / "engines" / "codex"
    text = (d / "prompt.md").read_text(encoding="utf-8")
    assert text.startswith("<!--") and "Apache License, Version 2.0" in text and "097558c1bdfc6318cb0ce80a64816ca4d61c7814" in text
    assert "Apache License" in (d / "LICENSE").read_text() and "OpenAI Codex" in (d / "NOTICE").read_text()
    assert "begin_patch" in (d / "apply_patch.lark").read_text()
    third = (Path(core.__file__).resolve().parents[3] / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "openai/codex" in third and "Apache-2.0" in third


def test_project_instructions_still_reach_the_codex_prompt(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use tabs.\n")
    assert "Use tabs." in _agent(tmp_path).messages[0]["content"]


def test_without_the_profile_nothing_changes(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    a.set_route(_route(profile="full"), "x")
    assert a.messages[0]["content"].startswith("You are Meshpool") and not a._codex()
    names = [t["function"]["name"] for t in a.tool_schemas()]
    assert "bash" in names and "shell" not in names and "apply_patch" not in names


def test_work_chats_and_plan_mode_keep_their_own_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_ENGINE_STYLE", "codex")
    w = Agent(str(tmp_path), mode="auto", profile="work")
    assert not w._codex() and w.messages[0]["content"].startswith("You are Meshpool Cowork")
    c = Agent(str(tmp_path), mode="auto")
    c.set_route(_route(), "x")
    assert c._codex()
    c.plan = True
    assert "read_file" in [t["function"]["name"] for t in c.tool_schemas()]


class _Mcp:
    last_images: list = []

    def schemas(self):
        mk = lambda n: {"type": "function", "function": {"name": n, "description": "", "parameters": {  # noqa: E731
            "type": "object", "properties": {}}}}
        return [mk("mcp__demo__calculate"), mk("mcp__meshpool_office__make_deck"), mk("mcp__meshpool_web__web_search")]

    def has(self, name):
        return name.startswith("mcp__")

    def call(self, name, args):
        return "= 15.0", False


def test_tool_list_is_exactly_the_codex_set_plus_mcp(tmp_path):
    a = _agent(tmp_path)
    assert [t["function"]["name"] for t in a.tool_schemas()] == ["shell", "apply_patch", "update_plan"]
    a.mcp = _Mcp()
    assert [t["function"]["name"] for t in a.tool_schemas()] == ["shell", "apply_patch", "update_plan",
                                                                 "mcp__demo__calculate"]
    assert a._tool_names() == ["shell", "apply_patch", "update_plan"]
    a.mode = "auto"
    _script(a, [_call("bash", {"command": "echo hi"}), {"role": "assistant", "content": "done"}])
    a.send("go")
    out = [m["content"] for m in a.messages if m["role"] == "tool"][0]
    assert "unknown tool 'bash'" in out and "shell, apply_patch, update_plan" in out          # no MeshPool tools


def test_route_method_rides_on_the_first_message_not_the_system_prompt(tmp_path):
    (tmp_path / "t.py").write_text("print(1)\n")
    route = Route(spec="code.debug", family="code", sub="debug", base="code", profile="codex",
                  settings=PR.settings("codex"))
    a = Agent(str(tmp_path), mode="auto")
    a.set_route(route, "fix it")
    _script(a, [{"role": "assistant", "content": "ok"}, {"role": "assistant", "content": "again"}])
    a.send("fix the bug in t.py")
    first = a.bodies[0]["messages"]
    assert first[0]["role"] == "system" and "[Harness:" not in first[0]["content"]
    user = first[1]["content"]
    assert user.startswith("fix the bug in t.py")
    method = addon(route).strip()
    assert method and method in user and "<environment_context>" in user and f"<cwd>{a.root}</cwd>" in user
    assert "<approval_policy>never</approval_policy>" in user                                 # auto: Codex's "never"
    a.send("and again")
    second = a.bodies[1]["messages"]
    assert second[0] == first[0] and second[1] == first[1] and "<environment_context>" not in second[3]["content"]
    assert [e for e in [m.get("content") for m in a.messages if m["role"] == "user"]].count(user) == 1


def test_ask_mode_is_on_request_and_asks_before_shell_and_patch(tmp_path):
    (tmp_path / "f.txt").write_text("1\n")
    asked = []

    def approve(kind, title, detail):
        asked.append((kind, title, detail))
        return "yes", ""
    a = _agent(tmp_path, mode="ask", approve=approve)
    _script(a, [_call("shell", {"command": ["bash", "-lc", "echo hi"], "workdir": "."}),
                _call("apply_patch", {"input": patch("*** Update File: f.txt", "@@", "-1", "+2")}, "c2"),
                _call("update_plan", {"plan": [{"step": "x", "status": "pending"}]}, "c3"),
                {"role": "assistant", "content": "done"}])
    a.send("go")
    assert "<approval_policy>on-request</approval_policy>" in a.bodies[0]["messages"][1]["content"]
    assert asked[0][0] == "bash" and asked[0][1] == "Run this command?" and "echo hi" in asked[0][2]
    assert asked[1][0] == "edit" and "f.txt" in asked[1][1] and "+2" in asked[1][2]
    assert len(asked) == 2                                    # the plan tool needs no approval
    assert (tmp_path / "f.txt").read_text() == "2\n"


def test_a_denied_shell_call_does_not_run(tmp_path):
    a = _agent(tmp_path, mode="ask", approve=lambda *x: ("no", "not that"))
    _script(a, [_call("shell", {"command": ["touch", "x"]}), {"role": "assistant", "content": "ok"}])
    a.send("go")
    assert not (tmp_path / "x").exists()
    assert "denied" in [m["content"] for m in a.messages if m["role"] == "tool"][0]


def test_accept_edits_mode_lets_patches_through_but_asks_for_commands(tmp_path):
    (tmp_path / "f.txt").write_text("1\n")
    asked = []
    a = _agent(tmp_path, mode="edits", approve=lambda k, t, d: (asked.append(k) or ("yes", "")))
    via_shell = ["apply_patch", patch("*** Update File: f.txt", "@@", "-2", "+3")]
    _script(a, [_call("apply_patch", {"input": patch("*** Update File: f.txt", "@@", "-1", "+2")}),
                _call("shell", {"command": via_shell}, "c2"),
                _call("shell", {"command": ["true"]}, "c3"), {"role": "assistant", "content": "ok"}])
    a.send("go")
    assert asked == ["bash"] and (tmp_path / "f.txt").read_text() == "3\n"


def test_update_plan_emits_the_plan_event_and_feeds_the_panel(tmp_path):
    events = []
    a = _agent(tmp_path, emit=events.append)
    steps = [{"step": "Read the code", "status": "completed"}, {"step": "Fix the bug", "status": "in_progress"},
             {"step": "Run the tests", "status": "pending"}]
    _script(a, [_call("update_plan", {"explanation": "start", "plan": steps}),
                _call("update_plan", {"plan": [{"step": "x", "status": "nope"}]}, "c2"),
                {"role": "assistant", "content": "ok"}])
    a.send("go")
    plans = [e for e in events if e["type"] == "plan"]
    assert len(plans) == 1 and plans[0]["total"] == 3 and plans[0]["done"] == 1 and plans[0]["current"] == 2
    assert plans[0]["items"][1] == {"content": "Fix the bug", "status": "in_progress"}
    outs = [m["content"] for m in a.messages if m["role"] == "tool"]
    assert outs[0] == "Plan updated" and "status 'nope'" in outs[1]
    assert a.plan_state()["current"] == 2 and a.plan_state()["done"] == 1
    assert "Fix the bug" in json.dumps(__import__("meshpool_harness.agent.planning", fromlist=["x"]).panel(a._todos))


def test_no_review_mutation_or_plan_rounds_and_no_verify_nudge(tmp_path, monkeypatch):
    (tmp_path / "t.py").write_text("x = 1\n")
    a = _agent(tmp_path)
    monkeypatch.setattr(a, "_aux_complete", lambda *x, **k: pytest.fail("a review round ran"))
    monkeypatch.setattr(a, "_mutation_feedback", lambda: pytest.fail("a mutation round ran"))
    s = a.hsettings()
    assert (s["review"], s["mutation"], s["plan"]) == (False, False, "off")
    long_task = "1. do a\n2. do b\n3. do c\n4. do d\n" + "x" * 1000
    _script(a, [_call("apply_patch", {"input": patch("*** Update File: t.py", "@@", "-x = 1", "+x = 2")}),
                _call("shell", {"command": ["bash", "-lc", "exit 1"]}, "c2"),
                _call("update_plan", {"plan": [{"step": "later", "status": "pending"}]}, "c3"),
                {"role": "assistant", "content": "Done."}])        # the failing check and the open plan: no extra round
    assert a.send(long_task) == "Done."
    assert len(a.bodies) == 4
    first_user = a.bodies[0]["messages"][1]["content"]
    assert "todo_write" not in first_user and "needs a plan" not in first_user and "[Meshpool]" not in "".join(
        str(m.get("content")) for m in a.messages if m["role"] == "user")


def test_the_loop_guard_and_compaction_still_work_in_codex_style(tmp_path):
    a = _agent(tmp_path)
    _script(a, [_call("shell", {"command": ["echo", "same"]}, f"c{i}") for i in range(4)]
            + [{"role": "assistant", "content": "ok"}])
    a.send("go")
    outs = [m["content"] for m in a.messages if m["role"] == "tool"]
    assert "same" in outs[0] and outs[2].startswith("[Meshpool] Not run: you already made this call")
    assert "read_file" not in outs[2]                                      # it must not name a tool that is not there
    big = "z" * 200
    a.messages += [{"role": "tool", "tool_call_id": "t", "content": big * 100}] * 30
    a.messages.append({"role": "user", "content": "x" * 200000})
    a._maybe_compact()
    assert core._context_size(a.messages) < 200000 + 100000
