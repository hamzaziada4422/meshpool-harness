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
