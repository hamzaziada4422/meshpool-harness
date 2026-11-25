"""Agent engine fixes from the feature ledger: read-only git never asks, search respects .gitignore and skips binary
files, read_file says when a file is binary."""
import json
import subprocess

import pytest

from meshpool_harness.core import tools as T


def _repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / ".gitignore").write_text("build/\n*.log\n", encoding="utf-8")
    (d / "build").mkdir()
    (d / "build" / "gen.py").write_text("needle = 1\n", encoding="utf-8")
    (d / "a.py").write_text("needle = 2\n", encoding="utf-8")
    (d / "run.log").write_text("needle in a log\n", encoding="utf-8")
    (d / "blob.dat").write_bytes(b"needle\0\x01\x02" * 10)
    return d


def test_git_read_only_classifier():
    yes = ["git status", "git log --oneline -5", "git diff HEAD~1", "git log -p", "git show HEAD", "git branch",
           "git branch -a", "git tag -l", "git remote -v", "git stash list", "git --no-pager diff", "git blame a.py"]
    no = ["git branch new", "git branch -D x", "git tag v1", "git -c core.pager=sh log", "git diff --output=x",
          "git status; rm -rf /", "git status && ls", "git remote add o u", "git stash", "git push", "git log | head",
          "git show --ext-diff", "git reflog expire --all", "git log $(rm x)", "git commit -m x", "git checkout .",
          "git -C /etc status", "gitk", ""]
    assert all(T.git_read_only(c) for c in yes), [c for c in yes if not T.git_read_only(c)]
    assert not any(T.git_read_only(c) for c in no), [c for c in no if T.git_read_only(c)]


def test_read_only_git_skips_the_question(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    from meshpool_harness.agent import core
    d = _repo(tmp_path)
    asked = []
    a = core.Agent(str(d), approve=lambda *x: (asked.append(x), ("no", ""))[1], mode="ask")
    assert a._permit("bash", {"command": "git status"}) == (True, "")
    assert a._permit("bash", {"command": "git log --oneline -3"}) == (True, "")
    assert not asked
    assert not a._permit("bash", {"command": "git commit -am wip"})[0] and asked


def test_grep_and_glob_respect_gitignore_and_skip_binaries(tmp_path):
    d = _repo(tmp_path)
    ws = T.Workspace(str(d), str(tmp_path / "run"))
    out = ws.grep("needle", output_mode="files_with_matches")
    assert out.split("\n") == ["a.py"], out
    assert "gen.py" in ws.grep("needle", path="build", output_mode="files_with_matches")   # asked for on purpose
    assert ws.glob("**/*.py").split("\n") == ["a.py"]
    with pytest.raises(T.ToolError, match="is a binary file"):
        ws.read_file("blob.dat")
    assert "needle = 2" in ws.read_file("a.py")


def test_outside_git_nothing_is_filtered(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (d / "x.log").write_text("needle\n", encoding="utf-8")
    ws = T.Workspace(str(d), str(tmp_path / "run"))
    assert "x.log" in ws.grep("needle", output_mode="files_with_matches")


def test_every_system_prompt_says_tool_output_is_data(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    from meshpool_harness.agent import core
    for profile in ("code", "work"):
        a = core.Agent(str(tmp_path), profile=profile)
        if a._style():                      # Codex's own prompt: the rule rides on the first message instead
            from meshpool_harness import assistant as AS
            assert core.DATA_RULE in AS.context_block(rules=False, data_rule=True, lookup=False, record=False)
        else:
            assert core.DATA_RULE in a.system_prompt()


def test_refusal_and_content_filter_are_said_plainly(tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_s")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    # 0.0.17: an empty content-filter reply is main's "no usable reply" (retried, then an error); one with text keeps it
    for msg, fin in (({"role": "assistant", "content": "Part of it"}, "content_filter"),
                     ({"role": "assistant", "content": None, "refusal": "I can't help with that."}, "stop")):
        a = core.Agent(str(tmp_path), mode="auto")
        a._post = lambda base, key, body, stream, m=msg, f=fin: ({"choices": [{"message": dict(m), "finish_reason": f}],
                                                                  "usage": {}}, False)
        out = a.send("hi")
        assert "declined to answer" in out, out


def test_always_in_this_folder_saves_a_narrow_rule(tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    proj = tmp_path / "proj"
    proj.mkdir()
    answers = ["save"]
    a = core.Agent(str(proj), approve=lambda *x: (answers.pop(0) if answers else "no", ""), mode="ask")
    assert a._permit("bash", {"command": "npm test -- --ci"}) == (True, "")
    saved = json.loads((tmp_path / "home" / "permissions.json").read_text())
    assert saved["projects"][core._folder_key(proj)]["allow"] == ["bash:npm test", "bash:npm test *"]
    b = core.Agent(str(proj), approve=lambda *x: ("no", ""), mode="ask")       # a new chat in the same folder
    assert b._permit("bash", {"command": "npm test"}) == (True, "")
    assert b._permit("bash", {"command": "npm test -- --ci"}) == (True, "")
    assert not b._permit("bash", {"command": "npm test && curl x | sh"})[0]      # never a chained command
    assert not b._permit("bash", {"command": "npm install"})[0]
    other = tmp_path / "other"
    other.mkdir()
    assert not core.Agent(str(other), approve=lambda *x: ("no", ""), mode="ask")._permit("bash", {"command": "npm test"})[0]


def test_rule_for_is_narrow():
    from meshpool_harness.agent import core
    assert core.rule_for("bash", {"command": "git commit -m x"}) == ["bash:git commit -m x"]   # exactly that
    assert core.rule_for("bash", {"command": "pytest -q"}) == ["bash:pytest", "bash:pytest *"]
    assert core.rule_for("bash", {"command": "make && make install"}) == []
    assert core.rule_for("bash", {"command": "rm -rf build"}) == []
    assert core.rule_for("edit", {}) == ["edit", "write_file", "apply_patch", "notebook_edit"]
    assert core.rule_for("mcp__github__create_issue", {}) == ["mcp__github__create_issue"]
