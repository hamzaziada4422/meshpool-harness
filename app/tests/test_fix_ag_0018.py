"""0.0.18 bug hunt, area AG (the agent loop and its tools). Each test is one bug; it failed before its fix."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import trust as TR  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.core import permrules as PR  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.delenv("MH_TRUST_PROJECT", raising=False)
    (tmp_path / "home").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _ws(tmp_path):
    return T.Workspace(str(tmp_path), str(tmp_path / ".run"))


def _asking_agent(root, mode="ask"):
    asked = []
    a = core.Agent(str(root), approve=lambda *x, **k: (asked.append(x[1]), ("no", ""))[1], mode=mode)
    return a, asked


# AG-01: read_file, grep and the syntax-error excerpt split lines with str.splitlines, which also splits at form feeds
# (common in Python and C sources) and U+2028, so every later line number disagreed with tracebacks and editors.
def test_line_numbers_count_newlines_only(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n\x0c\nb = 2\nc = 3\n", encoding="utf-8")
    ws = _ws(tmp_path)
    out = ws.read_file("a.py", start=3, end=3)
    assert out.startswith("a.py lines 3-3 of 4") and out.rstrip().endswith("b = 2")
    assert ws.grep("c = 3", "a.py") == "a.py:4:c = 3"
    assert ws.grep(r"b = 2\nc", "a.py", multiline=True) == "a.py:3:b = 2"
    (tmp_path / "u.txt").write_text("x y\nz\n", encoding="utf-8")
    assert ws.grep("z", "u.txt") == "u.txt:2:z"
    (tmp_path / "mac.txt").write_bytes(b"one\rtwo\r")              # CR-only endings still give lines
    assert ws.grep("two", "mac.txt") == "mac.txt:2:two"
    bad = T.Workspace._syntax_error(Path("b.py"), "x = 1\n\x0c\ny = (\n")
    assert bad and "y = (" in bad.split("\n")[1]


# AG-02: apply_patch could not touch the first line of a file that starts with a UTF-8 BOM (the BOM was part of the
# line, so no context matched).
def test_apply_patch_matches_and_keeps_a_bom(tmp_path):
    p = tmp_path / "a.py"
    p.write_bytes(b"\xef\xbb\xbfimport os\r\nx = 1\r\n")
    ws = _ws(tmp_path)
    out = ws.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-import os\n+import sys\n x = 1\n*** End Patch")
    assert out.startswith("Success")
    assert p.read_bytes() == b"\xef\xbb\xbfimport sys\r\nx = 1\r\n"


# AG-03: the output of a command that timed out went to the model without secret redaction (OSS-29 covered only
# commands that ended by themselves).
def test_timed_out_output_is_redacted(tmp_path):
    ws = _ws(tmp_path)
    secret = "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    out = ws.bash(f"echo ANTHROPIC_API_KEY={secret}; sleep 30", timeout_s=2)
    assert "timed out" in out and secret not in out


# AG-04: bash_start / bash_output returned a background command's output without secret redaction.
def test_background_output_is_redacted(tmp_path):
    ws = _ws(tmp_path)
    secret = "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    out = ws.bash_start(f"sleep 0.2; echo ANTHROPIC_API_KEY={secret}; sleep 0.5; echo again {secret}; sleep 5")
    out += ws.bash_output("bg1", wait_s=3)
    try:
        assert "again" in out and secret not in out
    finally:
        ws.bash_stop()


# AG-05: bash_start read a background command's output in the locale's code page; on Windows that is cp1252, so the
# UTF-8 that Git Bash writes came back as mojibake.
def test_background_output_is_read_as_utf8_on_windows(tmp_path, monkeypatch):
    seen = {}

    class FakeP:
        pid, stdout = 1, iter(())

        def poll(self):
            return 0

    def popen(args, **kw):
        seen.update(kw)
        return FakeP()
    class WinOs:                                   # os as tools.py sees it, with os.name "nt"; pathlib keeps its own
        name = "nt"

        def __getattr__(self, k):
            return getattr(os, k)
    ws = _ws(tmp_path)
    monkeypatch.setattr(ws, "_scratch", lambda e: e)
    monkeypatch.setattr(ws, "_py_env", lambda e: e)
    monkeypatch.setattr(T, "_bash", lambda: "bash")
    monkeypatch.setattr(T.subprocess, "Popen", popen)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    monkeypatch.setattr(T, "os", WinOs())
    ws.bash_start("echo hi")
    assert seen.get("encoding") == "utf-8"


# AG-06: the Ask-mode read-only shortcut (H-15) judged a shell or exec_command call's paths against the project root and
# ignored its workdir, so `cat id_rsa` with workdir ~/.ssh ran without a question.
@pytest.mark.parametrize("name,args", [
    ("shell", {"command": ["cat", "id_rsa"], "workdir": "OUT"}),
    ("shell", {"command": ["bash", "-lc", "cat id_rsa"], "workdir": "OUT"}),
    ("exec_command", {"cmd": "cat id_rsa", "workdir": "OUT"}),
    ("shell", {"command": ["cat", "../../outside/id_rsa"], "workdir": "sub"}),
])
def test_read_only_shortcut_respects_the_workdir(env, name, args):
    out = env.parent / "outside"
    out.mkdir()
    (out / "id_rsa").write_text("PRIVATE")
    (env / "sub").mkdir()
    (env / "sub" / "id_rsa").write_text("not secret")
    args = dict(args, workdir=str(out) if args["workdir"] == "OUT" else args["workdir"])
    a, asked = _asking_agent(env)
    assert not a._permit(name, args)[0] and asked


@pytest.mark.parametrize("name,args", [
    ("shell", {"command": ["cat", "id_rsa"], "workdir": "sub"}),
    ("exec_command", {"cmd": "cat ../sub/id_rsa", "workdir": "sub"}),
    ("shell", {"command": ["cat", "sub/id_rsa"]}),
])
def test_read_only_shortcut_still_covers_reads_inside_the_project(env, name, args):
    (env / "sub").mkdir()
    (env / "sub" / "id_rsa").write_text("not secret")
    a, asked = _asking_agent(env)
    assert a._permit(name, args)[0] and not asked


# AG-07: the guard on hooks.json, permissions.json and mcp.json read a command from `command` only, so an exec_command
# call (its script is `cmd`) or probe could write them under a chat grant or an allow rule without the question.
@pytest.mark.parametrize("name,args", [
    ("exec_command", {"cmd": "cp evil.json .meshpool/hooks.json"}),
    ("probe", {"command": "cp evil.json .meshpool/hooks.json"}),
    ("bash", {"command": "cp evil.json .meshpool/hooks.json"}),
])
def test_power_files_are_guarded_for_every_shell_tool(env, name, args):
    a, asked = _asking_agent(env)
    a.always.add("bash:cp")                            # the user allowed `cp` commands in this chat
    assert not a._permit(name, args)[0]
    assert asked and ".meshpool" in asked[0]
    assert a._permit(name, {k: v.replace(".meshpool/hooks.json", "notes.txt") for k, v in args.items()})[0]


# AG-08: "Allow in this chat" for `uv run pytest`, `npx eslint .` or `poetry run pytest` remembered `uv run`, `npx`
# or `poetry run`, which then covered `uv run bash -c "rm -rf ~"`: the wrapper rule (_NO_GRANT) did not see them.
@pytest.mark.parametrize("first,later,want", [
    ("uv run pytest -q", "uv run bash -c 'rm -rf ~'", ["uv run pytest"]),
    ("npx eslint .", "npx bash -c 'curl x | sh'", ["npx eslint"]),
    ("poetry run pytest", "poetry run python -c 'import os'", ["poetry run pytest"]),
    ("npm exec tsc", "npm exec -- sh -c id", ["npm exec tsc"]),
    ("uvx ruff check .", "uvx --from evil ruff", ["uvx ruff"]),
    ("uv run python -m pytest", "uv run python -c 'x'", ["uv run python -m pytest"]),
])
def test_runner_grants_name_the_program_they_run(first, later, want):
    pats = PR.grant_patterns(first)
    assert pats == want
    assert PR.granted(first, pats) and not PR.granted(later, pats)


@pytest.mark.parametrize("cmd", ["uv run bash -c 'x'", "uv --quiet run sh", "npx -y cowsay", "docker exec c ls",
                                 "uv run", "kubectl exec pod -- ls"])
def test_runner_wrapping_a_shell_gets_no_grant(cmd):
    assert PR.grant_patterns(cmd) == []


def _shell_call(cmd):
    import json
    return {"id": "s", "function": {"name": "shell", "arguments": json.dumps({"command": ["bash", "-lc", cmd]})}}


# AG-09: the "killed twice" loop guard counted any output with the words "timed out" or "Killed" (a passing test named
# test_request_timed_out) and ignored file changes, so a fixed command was refused with "ran out of memory or time".
def test_killed_twice_guard_needs_a_real_kill_and_no_change(env):
    (env / "m.py").write_text("x = 1\n")
    a = core.Agent(str(env), mode="auto", sessions_dir=str(env.parent / "s"))
    runs = []
    a._run_tool = lambda c, cut=False: runs.append(1) or f"[exit 0]\ntest_request_timed_out PASSED in {len(runs)}s"
    g = {}
    outs = [a._repeat_guard(_shell_call("pytest -q"), g) for _ in range(3)]
    assert len(runs) == 3 and not any("killed twice" in o for o in outs)
    b = core.Agent(str(env), mode="auto", sessions_dir=str(env.parent / "s2"))
    runs2 = []
    b._run_tool = lambda c, cut=False: runs2.append(1) or f"[exit 137]\nKilled after {len(runs2)}s"
    g2 = {}
    [b._repeat_guard(_shell_call("python3 big.py"), g2) for _ in range(2)]
    (env / "m.py").write_text("x = 2  # smaller input now\n")      # the agent changed the code
    out = b._repeat_guard(_shell_call("python3 big.py"), g2)
    assert len(runs2) == 3 and "killed twice" not in out
    out = [b._repeat_guard(_shell_call("python3 big.py"), g2) for _ in range(2)][-1]
    assert len(runs2) == 4 and "killed twice" in out         # killed twice with no change since: refused


# AG-10: the log facts kept at compaction (files changed, commands and exit codes, the plan) knew only the classic
# tools, so in the default code lane (shell, apply_patch, update_plan) the note kept none of them.
def test_compaction_facts_cover_the_codex_style_tools():
    import json
    from meshpool_harness.agent import compaction as CP

    def call(cid, name, args):
        return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** Add File: tests/test_app.py\n+x\n*** End Patch"
    middle = [
        {"role": "assistant", "content": "", "tool_calls": [
            call("1", "apply_patch", {"input": patch}),
            call("2", "shell", {"command": ["bash", "-lc", "pytest -q"]}),
            call("3", "shell", {"command": ["bash", "-lc", "cd lib && apply_patch <<'EOF'\n*** Begin Patch\n"
                                            "*** Add File: util.py\n+y\n*** End Patch\nEOF\n"]}),
            call("4", "update_plan", {"plan": [{"step": "fix the parser", "status": "in_progress"}]})]},
        {"role": "tool", "tool_call_id": "1", "content": "Success. Updated the following files:\nM src/app.py"},
        {"role": "tool", "tool_call_id": "2", "content": "[exit 1]\n1 failed"},
        {"role": "tool", "tool_call_id": "3", "content": "Success."},
        {"role": "tool", "tool_call_id": "4", "content": "Plan updated"},
    ]
    notes = CP.rule_notes(middle)
    assert "src/app.py" in notes and "tests/test_app.py" in notes and "util.py" in notes
    assert "pytest -q (exit 1)" in notes and "Commands that failed" in notes
    assert "fix the parser" in notes


# AG-11: @file imports in a subfolder's AGENTS.md and in path-scoped rules files followed paths outside the project
# (PA-agent-17 fenced only the top-level instruction files), so a cloned repo could send ~/notes.md to the model.
def test_nested_and_scoped_instruction_imports_stay_in_the_project(env):
    from meshpool_harness.agent import extras as EX
    outside = env.parent / "private"
    outside.mkdir()
    (outside / "secret.md").write_text("SECRET-NOTES", encoding="utf-8")
    (env / ".git").mkdir()
    (env / "pkg").mkdir()
    (env / "pkg" / "local.md").write_text("LOCAL-OK", encoding="utf-8")
    (env / "pkg" / "AGENTS.md").write_text(f"@{outside / 'secret.md'}\n@../private/secret.md\n@local.md\n",
                                           encoding="utf-8")
    (env / "pkg" / "a.py").write_text("x = 1\n")
    nested = EX.nested_instructions(env, "pkg/a.py", set())
    assert "LOCAL-OK" in nested and "SECRET-NOTES" not in nested
    (env / ".cursor" / "rules").mkdir(parents=True)
    (env / ".cursor" / "rules" / "py.mdc").write_text(
        f"---\nglobs: *.py\n---\nUse tabs.\n@{outside / 'secret.md'}\n", encoding="utf-8")
    scoped = EX.scoped_rules(env, "pkg/a.py", set())
    assert "Use tabs." in scoped and "SECRET-NOTES" not in scoped


# AG-12: a hook that timed out was killed without its children (subprocess.run kills the shell only), so the
# program the hook started kept running after meshpool had given up on it.
@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_hook_timeout_kills_the_whole_process_tree(env):
    import json
    import time
    from meshpool_harness import config as C
    from meshpool_harness.agent import extras as EX
    pidf = env / "child.pid"
    (C.home() / "hooks.json").write_text(json.dumps({"UserPromptSubmit": [
        {"command": f"sleep 30 & echo $! > '{pidf}'; wait", "timeout": 1}]}), encoding="utf-8")
    t0 = time.time()
    r = EX.run_hooks_full(env, "UserPromptSubmit", {"prompt": "hi"})
    assert r["blocked"] and time.time() - t0 < 10
    pid = int(pidf.read_text().strip())
    for _ in range(50):                               # the kill is immediate; give the kernel a moment to reap
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        if Path(f"/proc/{pid}/stat").exists() and Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail("the hook's child process outlived the hook's timeout")


def _glob_tree(root):
    for rel in ("src/a.py", "src/pkg/b.py", "src/pkg/deep/c.py", "docs/readme.md", ".hidden/h.py", "top.txt",
                "node_modules/x/y.py", "build/out.py", "sp ace/[x].py", "src/pkg/deep/notes.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n")
    for i in range(40):                                 # a dependency tree the agent never asked about
        (root / "node_modules" / f"p{i}" / "lib").mkdir(parents=True)
    if os.name != "nt":
        (root / "link").symlink_to(root / "src" / "pkg")


# AG-13: glob walked every folder pathlib's glob visits (all of node_modules, .venv, build for **/*.py) and only then
# dropped the results inside them; Stop was not checked while it walked (the H-11 fix covered grep only).
def test_glob_never_enters_skipped_folders_and_finds_the_same_files(tmp_path, monkeypatch):
    _glob_tree(tmp_path)
    ws = _ws(tmp_path)
    pats = ["*", "*.py", "**/*.py", "src/**", "src/*/", "**", "*/*.py", "src/**/c.py", "link/*.py", "*/b.py",
            "**/*.md", "sp ace/*.py", "sp ace/[[]x].py", "src/pkg/b.py", "src", "missing/*", "**/deep", "?op.txt",
            "./src/*.py", "src/**/*.md", "*/*/", "**/pkg/**/*.py"]
    for pat in pats:
        want = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.glob(pat)
                      if T.SKIP_DIRS.isdisjoint(p.relative_to(tmp_path).parts) and ".run" not in str(p))
        got = [x for x in ws.glob(pat).split("\n") if x and x != "no files" and ".run" not in x]
        assert got == want, pat
    seen = []
    real = os.scandir

    def spy(p="."):
        seen.append(str(p))
        return real(p)
    monkeypatch.setattr(os, "scandir", spy)
    assert ws.glob("**/*.py").split("\n") == [".hidden/h.py", "sp ace/[x].py", "src/a.py", "src/pkg/b.py",
                                              "src/pkg/deep/c.py"]
    assert not [p for p in seen if "node_modules" in p or "build" in p]
    stops = iter([False, False, True])
    ws.should_stop = lambda: next(stops, True)
    with pytest.raises(T.ToolError):                 # Stop is seen while it walks, not only between results
        ws.glob("**/*.zzz")


# AG-14: read_file on a big file numbered every line and saved a numbered copy of the whole file in the run folder,
# although only about 6,000 characters are shown (a 135 MB log: 3.8 s and a 152 MB copy per read).
def test_read_file_of_a_big_file_numbers_only_what_it_shows(tmp_path):
    with open(tmp_path / "app.log", "w", encoding="utf-8") as f:
        for i in range(1, 40001):
            f.write(f"line {i} request handled status=200\n")
    ws = _ws(tmp_path)
    out = ws.read_file("app.log")
    assert out.startswith("app.log lines 1-40000 of 40000\n    1  line 1 ")
    assert out.rstrip().endswith("40000  line 40000 request handled status=200")
    assert "lines not shown" in out and len(out) < 8000
    assert not list((tmp_path / ".run").glob("out_*")), "no numbered copy of the whole file is saved"
    mid = ws.read_file("app.log", start=20000, end=20002)
    assert mid.split("\n")[1:] == [f"{i:>5}  line {i} request handled status=200" for i in (20000, 20001, 20002)]
    small = ws.read_file("app.log", start=1, end=100)           # a range that fits is shown whole, as before
    assert "not shown" not in small and small.count("\n") == 100


# AG-15: on Linux meshpool's data folder is ~/.local/share/meshpool, inside the sandbox's writable ~/.local, so a
# sandboxed command could write meshpool.config (MH_SANDBOX=0), permissions.json or hooks.json and lift its own limits.
def test_sandboxed_commands_cannot_write_meshpool_settings(tmp_path, monkeypatch):
    import shutil
    if not sys.platform.startswith("linux") or not shutil.which("bwrap"):
        pytest.skip("bubblewrap is not installed")
    home = tmp_path / "h"
    data = home / ".local" / "share" / "meshpool"
    (data / "runs" / "job").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MESHPOOL_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("MH_SANDBOX", "1")
    from meshpool_harness import config as C
    assert C.home().resolve() == data.resolve()
    proj = tmp_path / "proj"
    proj.mkdir()
    ws = T.Workspace(str(proj), str(tmp_path / "out"))
    out = ws.bash(f"echo MH_SANDBOX=0 >> '{data}/meshpool.config'; echo '{{}}' > '{data}/hooks.json'; "
                  f"echo hi > ok.txt; echo x > '{home}/.local/other.txt'")
    assert not (data / "meshpool.config").exists() and not (data / "hooks.json").exists(), out
    assert (proj / "ok.txt").exists() and (home / ".local" / "other.txt").exists()   # the rest still works
    job = T.Workspace(str(data / "runs" / "job"), str(tmp_path / "out2"))    # an engine's folder in the data folder
    job.bash("echo hi > result.txt")
    assert (data / "runs" / "job" / "result.txt").exists()
