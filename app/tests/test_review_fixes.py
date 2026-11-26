"""Fixes for the independent review of the feature-ledger work. Each test is one finding, with its attack input."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from meshpool_harness import trust as TR
from meshpool_harness.agent import core
from meshpool_harness.core import tools as T


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.delenv("MH_TRUST_PROJECT", raising=False)
    (tmp_path / "home").mkdir()
    return tmp_path


@pytest.mark.parametrize("cmd", [
    "git log -1 --format=%x65cho%x20pwned ${HOME:+--output=$HOME/.bashrc}",       # H1: shell expansion
    "git log -1 --format=tformat:hi $'--output=/tmp/x'",                         # H1: ANSI-C quoting
    "git grep -Otouch foo", "git grep -iOtouch foo", "git grep --ope=touch x",   # H2: pager runs a program
    "git diff --no-index /etc/passwd /dev/null", "git blame --contents /etc/hostname a.py",   # M1: reads outside
    "git diff -- ../../etc/passwd", "git show HEAD:/etc/passwd", "git log ~/x",
    "git log (rm x)", "git log @(rm x)", "git log %HOME%",                       # M8: PowerShell and cmd syntax
    "git log --outp=x", "git remote show origin", "git stash", "git branch new", "git diff --ext-diff",
])
def test_git_fast_path_refuses_every_attack(cmd):
    assert not T.git_read_only(cmd)


def test_git_fast_path_still_covers_everyday_reads():
    for c in ("git status", "git status -s", "git log --oneline -5", "git log -n5", "git diff HEAD~1",
              "git diff --cached", "git show HEAD --stat", "git branch -a", "git tag -l", "git remote -v",
              "git stash list", "git blame a.py", "git log -- src/a.py", "git diff main...HEAD", "git log --format=%h"):
        assert T.git_read_only(c), c


def test_git_fast_path_needs_a_harmless_repo_config(env):
    r = env / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    assert T.git_config_safe(r)
    subprocess.run(["git", "-C", str(r), "config", "core.fsmonitor", "touch pwned"], check=True)   # M2
    assert not T.git_config_safe(r)
    a = core.Agent(str(r), approve=lambda *x: ("no", ""), mode="ask")
    assert not a._permit("bash", {"command": "git status"})[0]


def test_cloned_workspaces_in_the_data_folder_are_not_trusted(env):         # H3
    ws = env / "home" / "workspaces" / "someone__repo"
    (ws / ".meshpool").mkdir(parents=True)
    (ws / ".meshpool" / "hooks.json").write_text(json.dumps({"SessionStart": [{"command": "true"}]}))
    assert not TR.is_trusted(ws) and TR.needs_prompt(ws)
    assert TR.is_trusted(env / "home")


def test_save_with_nothing_narrow_is_allowed_once_only(env):                  # M4
    a = core.Agent(str(env), approve=lambda *x: ("save", ""), mode="ask")
    assert a._permit("bash", {"command": "make && make install"}) == (True, "")
    a.approve = lambda *x: ("no", "")
    assert not a._permit("bash", {"command": "make"})[0]                   # nothing wider was allowed


def test_power_files_always_ask(env):                                         # M6
    TR.trust(env)
    asked = []
    a = core.Agent(str(env), approve=lambda *x: (asked.append(x[1]), ("no", ""))[1], mode="edits")
    ok, _ = a._permit("write_file", {"path": ".meshpool/hooks.json", "content": "{}"})
    assert not ok and "hooks.json" in asked[-1]
    assert not a._permit("edit", {"path": ".mcp.json", "old": "a", "new": "b"})[0]
    assert a._permit("write_file", {"path": "notes.md", "content": "x"})[0]   # other edits still go through
    a.mode = "auto"
    assert not a._permit("write_file", {"path": str(env / "home" / "hooks.json"), "content": "{}"})[0]


def test_malformed_permissions_file_is_never_overwritten(env):
    f = env / "home" / "permissions.json"
    f.write_text("{ not json", encoding="utf-8")
    assert core.save_folder_rules(env, ["bash:npm test"]) == []
    assert f.read_text() == "{ not json"


def test_chat_only_undo_trims_what_the_model_sees(env, monkeypatch):           # M5
    from meshpool_harness.ui import agent_api as A
    proj = env / "proj"
    proj.mkdir()
    s = A.Session(workdir=str(proj), mode="auto", harness="meshpool-code")
    replies = iter(["first answer", "second answer"])
    s.agent._post = lambda *x: ({"choices": [{"message": {"role": "assistant", "content": next(replies)},
                                              "finish_reason": "stop"}], "usage": {}}, False)
    for text in ("one", "two"):
        s.send(text)
        for _ in range(200):
            if s.state == "idle" and sum(1 for e in s.events if e["type"] == "turn_end") >= (1 if text == "one" else 2):
                break
            import time
            time.sleep(0.05)
    assert any("second answer" == m.get("content") for m in s.agent.messages)
    with A.LOCK:
        A.LIVE[s.id] = s                                                     # the chat as the app holds it
    A.session_action(s.id, "revert", {"turn": 2, "what": "chat"})            # 0.0.17: through the app's action
    assert not any("second answer" == m.get("content") for m in s.agent.messages)
    assert any("first answer" == m.get("content") for m in s.agent.messages)
    with A.LOCK:
        A.LIVE.pop(s.id, None)
    s.close()


def test_reasoning_is_saved_once_per_call(env, monkeypatch):                   # M7
    import io

    class Resp(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    chunks = [{"choices": [{"delta": {"reasoning_content": f"part {i} "}}]} for i in range(50)]
    chunks.append({"choices": [{"delta": {"content": "Answer."}, "finish_reason": "stop"}]})
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    monkeypatch.setattr(core, "_urlopen", lambda req, t, sink: Resp(body.encode()))
    a = core.Agent(str(env), mode="auto")
    seen = []
    a.emit = seen.append
    a._post("http://x/v1", "k", {"model": "m", "messages": []}, True)
    th = [e for e in seen if e.get("type") == "thought"]
    assert sum(1 for e in th if e.get("delta")) == 50
    final = [e for e in th if e.get("final")]
    assert len(final) == 1 and final[0]["text"].startswith("part 0") and "part 49" in final[0]["text"]


# ---------------------------------------------------------------- recheck of 143f0b5
def _edits_agent(env):
    TR.trust(env)
    asked = []
    a = core.Agent(str(env), approve=lambda *x: (asked.append(x[1]), ("no", ""))[1], mode="edits")
    return a, asked


@pytest.mark.parametrize("name,args", [
    ("apply_patch", {"input": "*** Begin Patch\n*** Update File: notes.md\n*** Move to: .meshpool/hooks.json\n@@\n-a\n+b\n*** End Patch"}),
    ("shell", {"command": ["bash", "-lc", "cd .meshpool && apply_patch <<'EOF'\n*** Begin Patch\n*** Add File: hooks.json\n+{}\n*** End Patch\nEOF\n"]}),
    ("shell", {"command": ["apply_patch", "*** Begin Patch\n*** Add File: hooks.json\n+{}\n*** End Patch"], "workdir": ".meshpool"}),
    ("write_file", {"path": ".MESHPOOL/HOOKS.JSON", "content": "{}"}),
    ("write_file", {"path": ".mcp.json.", "content": "{}"}),
    ("bash", {"command": "echo '{}' > .meshpool/hooks.json"}),
])
def test_power_file_guard_has_no_known_bypass(env, name, args):               # R1
    a, asked = _edits_agent(env)
    assert not a._permit(name, args)[0] and asked


def test_power_file_names_in_a_command_skip_saved_rules(env):                 # R1: allow rules do not cover it
    core.save_folder_rules(env, ["bash:cp *"])
    a, asked = _edits_agent(env)
    assert not a._permit("bash", {"command": "cp x .meshpool/permissions.json"})[0] and asked


@pytest.mark.parametrize("cmd", ["npm test (Start-Process calc)", "npm test @(Start-Process calc)", "npm test $env:X",
                                 "npm test {a,b}"])
def test_saved_rules_never_cover_powershell_or_substitutions(env, cmd):       # R2
    core.save_folder_rules(env, ["bash:npm test", "bash:npm test *"])
    a = core.Agent(str(env), approve=lambda *x: ("no", ""), mode="ask")
    assert not a._permit("bash", {"command": cmd})[0]


def test_git_config_worktree_and_core_worktree_are_caught(env):               # R3
    r = env / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "config", "core.worktree", "/etc"], check=True)
    assert not T.git_config_safe(r)
    r2 = env / "repo2"
    r2.mkdir()
    subprocess.run(["git", "init", "-q", str(r2)], check=True)
    subprocess.run(["git", "-C", str(r2), "config", "extensions.worktreeConfig", "true"], check=True)
    assert not T.git_config_safe(r2)


@pytest.mark.parametrize("cmd", ["git log -- --output=x", "git diff -- ..", "git log .."])
def test_words_after_double_dash_and_bare_dotdot(cmd):                         # R5
    assert not T.git_read_only(cmd)


# ---------------------------------------------------------------- recheck of e140480
def test_workdir_and_cd_together_cannot_reach_a_power_file(env):              # N1
    (env / "sub").mkdir()
    a, asked = _edits_agent(env)
    cmd = ["bash", "-lc", "cd .meshpool && apply_patch <<'EOF'\n*** Begin Patch\n*** Add File: hooks.json\n+{}\n*** End Patch\nEOF\n"]
    assert not a._permit("shell", {"command": cmd, "workdir": "sub"})[0] and asked


def test_a_repo_with_its_own_git_hooks_gets_no_shortcut(env):                 # N2
    r = env / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    assert T.git_config_safe(r)
    h = r / ".git" / "hooks" / "post-index-change"
    h.write_text("#!/bin/sh\ntouch pwned\n")
    h.chmod(0o755)
    assert not T.git_config_safe(r)


def test_power_file_guard_maps_container_paths(env):                           # N4
    a, asked = _edits_agent(env)
    a.ws.container, a.ws.cdir = "ctr", "/workspace"
    assert not a._permit("write_file", {"path": "/workspace/.meshpool/hooks.json", "content": "{}"})[0] and asked


@pytest.mark.parametrize("cmd", ["cp x .meshpool/hook?.json", "sort -o .meshpool/hooks.json x",
                                 "find . -fprint .meshpool/hooks.json", "cp x .mcp.jso*"])
def test_power_file_commands_skip_saved_rules(env, cmd):                        # N5
    core.save_folder_rules(env, ["bash:cp", "bash:cp *", "bash:sort", "bash:sort *", "bash:find", "bash:find *"])
    a, asked = _edits_agent(env)
    assert not a._permit("bash", {"command": cmd})[0] and asked


# ---------------------------------------------------------------- saved rules: the exact command (fourth review F5)
@pytest.mark.parametrize("cmd", ["python -m pytest -q", "npm test", "cargo test --release", "go test ./..."])
def test_known_test_commands_keep_any_arguments(cmd):
    rules = core.rule_for("bash", {"command": cmd})
    assert len(rules) == 2 and rules[1].endswith(" *")


@pytest.mark.parametrize("cmd", ["git log --oneline", "git fetch origin", "tar xf a.tar", "find src -name x",
                                 "docker compose up", "uv run python x.py", "npm exec node x", "pkexec ls",
                                 "powershell.exe -c x", "node build.js", "npm run build", "git config core.x y"])
def test_other_commands_save_exactly_what_was_approved(env, cmd):
    rules = core.rule_for("bash", {"command": cmd})
    assert len(rules) == 1, rules
    core.save_folder_rules(env, rules)
    a = core.Agent(str(env), approve=lambda *x: ("no", ""), mode="ask")
    assert a._permit("bash", {"command": cmd})[0]                               # what was approved
    assert not a._permit("bash", {"command": cmd + " --output=/tmp/x"})[0]       # never anything wider


@pytest.mark.parametrize("cmd", ["FOO=1 bash x", "make && make install", "rm -rf build", "git log\rcalc"])
def test_nothing_is_saved_for_prefixed_chained_or_destructive_commands(cmd):
    assert core.rule_for("bash", {"command": cmd}) == []


def test_saved_rule_respects_word_boundaries(env):
    core.save_folder_rules(env, core.rule_for("bash", {"command": "npm test"}))
    b = core.Agent(str(env), approve=lambda *x: ("no", ""), mode="ask")
    assert b._permit("bash", {"command": "npm test -- --ci"})[0]
    assert not b._permit("bash", {"command": "npm testify"})[0]


# ---------------------------------------------------------------- fourth review
@pytest.mark.parametrize("patch", [
    "*** Begin Patch\n  *** Add File: .meshpool/hooks.json\n+{}\n*** End Patch",
    "*** Begin Patch\n\t*** Add File: .mcp.json\n+{}\n*** End Patch",
    "*** Begin Patch\r*** Add File: .meshpool/permissions.json\r+{}\r*** End Patch",
    "*** Begin Patch\u2028*** Add File: .meshpool/mcp.json\u2028+{}\u2028*** End Patch",
    "*** Begin Patch\n*** Add File: notes.md\n+hi\n *** Add File: .meshpool/hooks.json\n+{}\n*** End Patch",
])
def test_power_file_guard_reads_patches_like_the_tool(env, patch):              # F1
    a, asked = _edits_agent(env)
    assert not a._permit("apply_patch", {"input": patch})[0] and asked


def test_bash_many_needs_every_command_allowed(env):                            # F2
    core.save_folder_rules(env, ["bash:npm test", "bash:npm test *"])
    a = core.Agent(str(env), approve=lambda *x: ("no", ""), mode="ask")
    assert a._permit("bash_many", {"commands": ["npm test", "npm test -- --ci"]})[0]
    assert not a._permit("bash_many", {"commands": ["npm test", "touch pwned"]})[0]
    (env / "home" / "permissions.json").write_text(json.dumps({"deny": ["bash:curl *"]}))
    assert not core.Agent(str(env), approve=lambda *x: ("yes", ""), mode="auto")._permit(
        "bash_many", {"commands": ["ls", "curl x"]})[0]                         # deny applies to each command


def test_untrusted_folders_run_no_project_executables(env):                     # F3
    from meshpool_harness.hrouter import packs
    r = env / "repo"
    (r / ".venv" / "bin").mkdir(parents=True)
    tool = r / ".venv" / "bin" / "ruff"
    tool.write_text("#!/bin/sh\ntouch pwned\n")
    tool.chmod(0o755)

    class W:
        root = r
    assert packs._tool(W, "ruff") != str(tool)
    TR.trust(r)
    assert packs._tool(W, "ruff") == str(tool)
    TR.revoke(r)
    asked = []
    a = core.Agent(str(r), approve=lambda *x: (asked.append(x), ("no", ""))[1], mode="ask")
    assert not a._permit("diagnostics", {"paths": ["a.py"]})[0] and asked


def test_git_shortcut_off_for_unknown_config_and_submodules(env):               # F4
    r = env / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    assert T.git_config_safe(r)
    subprocess.run(["git", "-C", str(r), "config", "remote.origin.uploadpack", "touch x"], check=True)
    assert not T.git_config_safe(r)
    r2 = env / "repo2"
    r2.mkdir()
    subprocess.run(["git", "init", "-q", str(r2)], check=True)
    (r2 / ".gitmodules").write_text("[submodule \"x\"]\n")
    assert not T.git_config_safe(r2)


def test_power_files_at_any_depth_are_guarded(env):                             # F6
    a, asked = _edits_agent(env)
    assert not a._permit("write_file", {"path": ".meshpool/worktrees/x/.meshpool/hooks.json", "content": "{}"})[0]


@pytest.mark.parametrize("cmd", ["git log\rcalc.exe", "git status\u2028calc", "git log\tx"])
def test_git_shortcut_refuses_control_and_unicode_breaks(cmd):                   # F7
    assert not T.git_read_only(cmd)


def test_saved_rules_never_cover_globs_on_paths(env):                            # F8
    core.save_folder_rules(env, ["bash:cp *"])
    a = core.Agent(str(env), approve=lambda *x: ("no", ""), mode="ask")
    assert not a._permit("bash", {"command": "cp a .me?hpool/hook?.json"})[0]


def test_container_patch_with_a_relative_base_does_not_crash(env):              # F9
    a, asked = _edits_agent(env)
    a.ws.container, a.ws.cdir = "ctr", "/workspace"
    cmd = ["apply_patch", "*** Begin Patch\n*** Add File: notes.md\n+x\n*** End Patch"]
    ok, _ = a._permit("shell", {"command": cmd, "workdir": "sub"})
    assert ok in (True, False)


def test_no_worktrees_in_a_repo_whose_git_runs_programs(env):                    # F10
    from meshpool_harness import worktrees as WT
    r = env / "repo"
    r.mkdir()
    subprocess.run("git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m i", shell=True,
                   cwd=r, check=True)
    subprocess.run(["git", "-C", str(r), "config", "filter.x.smudge", "touch pwned"], check=True)  # runs on checkout
    with pytest.raises(WT.WorktreeError, match="run programs"):
        WT.create(r, owner_kind="cli")                  # 0.0.17: main's worktrees, the same refusal
    subprocess.run(["git", "-C", str(r), "config", "--unset", "filter.x.smudge"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "core.fsmonitor", "touch pwned"], check=True)
    rec = WT.create(r, owner_kind="cli")                 # fsmonitor and hooks are turned off on every worktree call
    assert not (r / "pwned").exists() and not (Path(rec["path"]) / "pwned").exists()
    WT.discard(rec["id"])



def test_compaction_keeps_what_the_user_asked_for():                          # /compact keep X
    from meshpool_harness.agent import compaction as CP
    seen = []

    class Fake:
        def chat(self, **kw):
            seen.append(kw["messages"][-1]["content"])
            return {"choices": [{"message": {"content": "summary"}}]}
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "build the API"}]
    for i in range(40):
        msgs += [{"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function", "function":
                  {"name": "bash", "arguments": "{\"command\": \"ls\"}"}}]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": "out " * 50}]
    r = CP.compact_history(msgs, client=Fake(), model="m", focus="the API design decisions")
    assert r and seen and seen[0].endswith("keep, in full detail: the API design decisions")


def test_a_side_question_is_answered_and_kept_out_of_the_chat(env, monkeypatch):    # /btw
    (env / "p").mkdir()
    a = core.Agent(str(env / "p"), mode="auto")
    a.messages += [{"role": "user", "content": "add retries"},
                   {"role": "assistant", "content": "I put the retry logic in net/retry.py."}]
    before = list(a.messages)
    sent = []

    def post(self, base, key, body, stream):
        sent.append(body)
        return {"choices": [{"message": {"content": "It is in net/retry.py."}}], "usage": {"prompt_tokens": 10}}, False
    monkeypatch.setattr(core.Agent, "_post", post)
    assert a.side_question("where is the retry logic?") == "It is in net/retry.py."
    assert a.messages == before and "tools" not in sent[0]
    assert "net/retry.py" in sent[0]["messages"][1]["content"] and sent[0]["messages"][1]["content"].endswith(
        "where is the retry logic?")
    with pytest.raises(ValueError, match="Ask something"):
        a.side_question("  ")


def test_commit_attribution_is_opt_in(env, monkeypatch):                        # GIT-058
    (env / "p").mkdir()
    monkeypatch.delenv("MH_COMMIT_TRAILER", raising=False)
    assert "# Commits" not in core.Agent(str(env / "p"), mode="auto").system_prompt()
    monkeypatch.setenv("MH_COMMIT_TRAILER", "Co-Authored-By: meshpool <noreply@example.com>")
    sp = core.Agent(str(env / "p"), mode="auto").system_prompt()
    assert "# Commits" in sp and sp.count("Co-Authored-By: meshpool <noreply@example.com>") == 1


def test_add_dir_lets_the_agent_read_and_edit_another_folder(env):               # /add-dir
    (env / "p").mkdir()
    other = env / "shared-lib"
    other.mkdir()
    (other / "util.py").write_text("x = 1\n")
    a = core.Agent(str(env / "p"), approve=lambda *x: ("no", ""), mode="ask")
    assert a._outside("read_file", {"path": str(other / "util.py")})              # asks before
    with pytest.raises(T.ToolError):
        a.ws.write_file(str(other / "new.py"), "y = 2\n")
    assert a.add_dir(str(other)) == str(other.resolve())
    assert a._outside("read_file", {"path": str(other / "util.py")}) == ""
    a.ws.write_file(str(other / "new.py"), "y = 2\n")
    assert (other / "new.py").read_text() == "y = 2\n"
    with pytest.raises(T.ToolError):                                                # never its .git
        (other / ".git").mkdir()
        a.ws.write_file(str(other / ".git" / "config"), "x")
    with pytest.raises(ValueError):
        a.add_dir(str(env / "missing"))


@pytest.mark.parametrize("which", ["home_parent", "ssh", "meshpool_home", "git"])
def test_add_dir_refuses_broad_private_and_own_folders(env, monkeypatch, which):   # third review, 5
    (env / "p").mkdir()
    home = env / "users" / "me"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setattr(core.Path, "home", staticmethod(lambda: home))
    (env / "repo" / ".git").mkdir(parents=True)
    a = core.Agent(str(env / "p"), mode="auto")
    target = {"home_parent": env / "users", "ssh": home / ".ssh", "meshpool_home": env / "home",
              "git": env / "repo" / ".git"}[which]
    with pytest.raises(ValueError):
        a.add_dir(str(target))


def test_add_dir_is_relative_to_the_project_kept_after_a_restart_and_safe_mid_turn(env):
    (env / "p" / "libs" / "shared").mkdir(parents=True)
    a = core.Agent(str(env / "p"), mode="auto")
    got = a.add_dir("libs/shared")
    assert got == str((env / "p" / "libs" / "shared").resolve())
    b = core.Agent.resume(str(env / "p"), session_id=a.id)
    assert b is not None and [str(x) for x in b.ws.extra_roots] == [got]       # kept after a restart
    (env / "other").mkdir()
    a._running = True                                                       # a turn is running: no message in between
    n = len(a.messages)
    a.add_dir(str(env / "other"))
    assert len(a.messages) == n and a._inbox and "other" in a._inbox[-1][0]


def test_a_side_question_does_not_take_over_stop(env, monkeypatch):              # third review, 7
    (env / "p").mkdir()
    a = core.Agent(str(env / "p"), mode="auto")
    a._live = "the turn's stream"
    a.last_request_id = "req_turn"
    def post(self, base, key, body, stream):
        assert getattr(core._AUX, "on", False)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}, False
    monkeypatch.setattr(core.Agent, "_post", post)
    assert a.side_question("what file?") == "ok"
    assert a._live == "the turn's stream" and a.last_request_id == "req_turn" and not core._AUX.on


def test_a_side_question_stream_leaves_the_turns_stop_handle(env, monkeypatch):   # third review, 7 (sink)
    """FL-V7 through the real request path: a /btw call goes through _post's connection sink and reads a reply
    (SSE lines, as a gateway that streams anyway sends them) with its own request id. Stop's handle (_live) and
    the turn's request id stay the turn's own while the side call runs and after it."""
    (env / "p").mkdir()
    a = core.Agent(str(env / "p"), mode="auto")
    a._live = "the turn's stream"
    a.last_request_id = "req_turn"
    seen = []

    class Resp(_SSEResp):
        def read1(self, *x):
            seen.append(("reading", a._live, a.last_request_id))
            return super().read1(*x)

    def urlopen(req, t, sink):
        sink(_FakeConn())                                  # the real sink: it decides who owns the connection
        seen.append(("connected", a._live, a.last_request_id))
        return Resp([_chunk("in "), _chunk("core.py"), "data: [DONE]\n\n"],
                    headers={"Content-Type": "text/event-stream", "x-request-id": "req_side"})
    monkeypatch.setattr(core, "_urlopen", urlopen)
    assert a.side_question("what file?") == "in core.py"
    assert seen and all(s[1:] == ("the turn's stream", "req_turn") for s in seen), seen
    assert a._live == "the turn's stream" and a.last_request_id == "req_turn" and not core._AUX.on


class _SSEResp:
    """A streamed reply as urllib gives it: SSE lines, then optionally the connection drops mid-stream."""

    def __init__(self, lines, drop=False, headers=None):
        self._lines = [ln.encode() for ln in "".join(lines).splitlines(keepends=True)]   # one line at a time
        self._drop = drop
        self.headers = headers or {"Content-Type": "text/event-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        yield from self._lines
        if self._drop:
            raise ConnectionResetError(104, "Connection reset by peer")

    def read1(self, *a):                          # a non-streamed call reads the body whole
        data, self._lines = b"".join(self._lines), []
        return data


class _FakeConn:
    def connect(self):
        pass

    def close(self):
        pass


class _DownHealth:
    """The endpoint gateway as it is when the endpoint went away: one slot, the probe says down, no recovery."""

    def is_down(self):
        return False

    def acquire(self, check_stop, emit=None):
        return 1

    def release(self, ok, slot=None):
        pass

    def check(self, probe, wait_s=60, check_stop=None):
        return "down"

    def wait_until_up(self, probe, sleep, emit, budget_s, check_stop):
        return False


def _chunk(text):
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}\n\n"


def test_old_partial_text_never_joins_a_new_turn(env, monkeypatch):               # third review, 8
    """FL-V8: a turn whose stream drops keeps what arrived ("cut off" reply). A new turn that also loses its
    connection mid-stream keeps only its own text: never the partial reply or request id of an earlier turn."""
    from meshpool_harness.core import gateway as GW
    (env / "p").mkdir()
    events = []
    a = core.Agent(str(env / "p"), mode="auto", emit=events.append)
    a._partial_text = "left over from a stopped turn, longer than the new text"   # an earlier turn's cut reply
    a.last_request_id = "req_old_turn"
    monkeypatch.setattr(GW, "health", lambda *x, **k: _DownHealth())
    monkeypatch.setattr(core, "_urlopen", lambda req, t, sink: (
        sink(_FakeConn()), _SSEResp([_chunk("fresh "), _chunk("words")], drop=True))[1])
    a.send("hi")
    kept = [m["content"] for m in a.messages if m.get("role") == "assistant"]
    assert kept and "fresh words" in kept[-1] and "cut off" in kept[-1]         # the connection-lost path ran
    assert not any("left over" in str(m.get("content")) for m in a.messages)
    assert not any("left over" in str(e.get("text")) for e in events)
    errs = [e["text"] for e in events if e.get("type") == "error"]
    assert errs and not any("req_old_turn" in t for t in errs)


def test_a_next_run_is_never_in_the_past(monkeypatch):                            # third review, 12
    from meshpool_harness import assistant as AS
    import time as _t
    from datetime import datetime, timedelta
    from meshpool_harness import cron as CRN
    calls = []
    def fake(expr, after):
        calls.append(after)
        return datetime.now() - timedelta(seconds=30) if len(calls) == 1 else datetime.now() + timedelta(hours=1)
    monkeypatch.setattr(CRN, "next_after", fake)
    assert AS.next_run({"when": "cron", "cron": "0 1 * * *"}) > _t.time() and len(calls) == 2


def test_status_line_waits_for_trust_in_a_repo_whose_git_runs_programs(env, monkeypatch):   # third review, 10
    import subprocess as _sp
    from meshpool_harness.agent import tui as TUI
    r = env / "repo"
    r.mkdir()
    _sp.run(["git", "init", "-q", str(r)], check=True)
    _sp.run(["git", "-C", str(r), "config", "core.fsmonitor", "touch pwned"], check=True)
    t = TUI.Terminal.__new__(TUI.Terminal)
    t.cwd = str(r)
    assert t._statusline_ok() is False
    t2 = TUI.Terminal.__new__(TUI.Terminal)
    t2.cwd = str(env / "p")
    (env / "p").mkdir(exist_ok=True)
    assert t2._statusline_ok() is True


def test_codex_style_chats_get_the_data_rule_on_the_first_message(env):          # 0.0.17 merge
    from meshpool_harness import assistant as AS
    assert core.DATA_RULE in AS.context_block(rules=False, data_rule=True, lookup=False, record=False)
    assert core.DATA_RULE not in AS.context_block(rules=False, lookup=False, record=False)   # in the system prompt
    assert core.DATA_RULE in AS.context_block(rules=True, lookup=False, record=False)       # outside agents


def test_git_config_check_outside_a_repository_is_not_fooled_by_plain_files(env):   # 0.0.17 merge
    d = env / "plain"
    d.mkdir()
    (d / "README.md").write_text("hi\n")          # rev-parse fails here; the folder itself is not a hooks folder
    assert T.git_config_safe(d)


def test_first_message_and_receipt_git_never_run_the_repos_own_programs(env):        # 0.0.17 CPU audit find
    from meshpool_harness.agent import preload as PL
    r = env / "repo"
    r.mkdir()
    subprocess.run("git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m i", shell=True,
                   cwd=r, check=True)
    (r / "a.txt").write_text("x\n")
    marker = env / "pwned"
    subprocess.run(["git", "-C", str(r), "config", "core.fsmonitor", f"touch {marker}"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "diff.external", f"touch {marker}"], check=True)
    assert "uncommitted changes" in PL._git(r)                          # git status still works
    assert not marker.exists()                                          # but fsmonitor never ran
    out = subprocess.run(T.safe_git("diff", "--numstat", "--no-ext-diff", "--no-textconv", "HEAD"), cwd=r,
                         capture_output=True, text=True)
    assert out.returncode == 0 and not marker.exists()
    subprocess.run(["git", "status", "--short"], cwd=r, capture_output=True)   # plain git does run it: the test is real
    assert marker.exists()


def test_receipt_git_diff_never_runs_the_repos_own_programs(env):
    """Agent.receipt() itself (not just safe_git): a file changed outside the edit tools is filled in from
    `git diff --numstat HEAD`, and a repository whose config names an fsmonitor or diff program runs neither."""
    from types import SimpleNamespace
    r = env / "repo"
    r.mkdir()
    (r / "a.txt").write_text("one\n")
    subprocess.run("git init -q && git add a.txt && git -c user.email=t@t -c user.name=t commit -q -m i", shell=True,
                   cwd=r, check=True)
    (r / "a.txt").write_text("one\ntwo\n")                              # changed by a script, not an edit tool
    marker = env / "pwned"
    prog = env / "prog.sh"
    prog.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    prog.chmod(0o755)
    for key in ("core.fsmonitor", "diff.external", "core.pager"):
        subprocess.run(["git", "-C", str(r), "config", key, str(prog)], check=True)
    ws = SimpleNamespace(turn_files={str(r / "a.txt")}, turn_new=set(), commands=[], edits=0)
    fake = SimpleNamespace(ws=ws, root=str(r), checkpoint=None)
    out = core.Agent.receipt(fake)
    assert "a.txt (+1 -0)" in out                                       # the numstat still comes through
    assert not marker.exists()                                          # but no program of the repo's ran
    subprocess.run(["git", "status", "--short"], cwd=r, capture_output=True)   # plain git does run it: the test is real
    assert marker.exists()
