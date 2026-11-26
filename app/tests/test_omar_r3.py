"""Omar (developer persona), round 3: a doubled history move, the verification line, a bool where a command goes,
local commits written with a heredoc, tips, and a promise followed by a malformed call. Scripted model, no network."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core as A  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _agent(root, replies, mode="auto"):
    a = A.Agent(str(root), mode=mode)
    seq, seen = list(replies), []

    def fake(base, key, body, stream):
        seen.append(body)
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake
    return a, seen


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def _repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "o@x.io")
    _git(root, "config", "user.name", "o")
    for n in "abc":
        (root / f"{n}.txt").write_text(n)
        _git(root, "add", ".")
        _git(root, "commit", "-qm", f"add {n}")


# ---------------------------------------------------------------------------------------------- 1: a doubled reset
def test_1_the_same_history_move_twice_in_one_reply_runs_once(tmp_path):
    _repo(tmp_path)
    reset = {"command": "git reset --mixed HEAD~1"}
    a, seen = _agent(tmp_path, [
        {"role": "assistant", "content": "", "tool_calls": [_call(1, "bash", reset), _call(2, "bash", reset)]},
        {"role": "assistant", "content": "Done."}])
    a.send("undo the last commit")
    assert _git(tmp_path, "rev-list", "--count", "HEAD").strip() == "2"      # one commit undone, not two
    tools = [m["content"] for m in a.messages if m.get("role") == "tool"]
    assert tools[1].startswith("Skipped: this is the same command you just ran."), tools


def test_1_a_look_in_between_allows_the_same_move_again(tmp_path):
    _repo(tmp_path)
    reset = {"command": "git reset --mixed HEAD~1"}
    a, _ = _agent(tmp_path, [
        {"role": "assistant", "content": "", "tool_calls": [_call(1, "bash", reset),
                                                          _call(2, "bash", {"command": "git log --oneline"}),
                                                          _call(3, "bash", reset)]},
        {"role": "assistant", "content": "Done."}])
    a.send("undo two commits")
    assert _git(tmp_path, "rev-list", "--count", "HEAD").strip() == "1"


def test_1_history_key_names_the_moves():
    for c in ("git reset --hard HEAD~1", "git rebase main", "git checkout main", "git revert abc123",
              "git commit --amend --no-edit", "git branch -D old", "git stash drop", "cd x && git reset --mixed HEAD~1"):
        assert A._history_key([c]), c
    for c in ("git status", "git checkout -- a.py", "git commit -m x", "git branch", "git log", "ls"):
        assert not A._history_key([c]), c
