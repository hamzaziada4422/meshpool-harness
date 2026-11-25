"""Hooks: the newer events, exec form, async, once, input rewrite and SessionStart matchers."""
import json
import sys
import time

from meshpool_harness.agent import extras as EX
from meshpool_harness.agent.core import Agent


def _trusted(monkeypatch, tmp_path, hooks: dict):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    from meshpool_harness import trust as TR
    TR.trust(tmp_path)
    (tmp_path / ".meshpool").mkdir(exist_ok=True)
    (tmp_path / ".meshpool" / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")


def test_new_events_are_known():
    for e in ("PermissionDenied", "StopFailure", "DirectoryAdded", "CwdChanged", "InstructionsLoaded"):
        assert e in EX.EVENTS


def test_exec_form_runs_without_a_shell(tmp_path, monkeypatch):
    _trusted(monkeypatch, tmp_path, {"Stop": [{"command": sys.executable,
                                               "args": ["-c", "print('a;b && c')"]}]})
    assert EX.run_hooks(tmp_path, "Stop", {}) == (False, "a;b && c")


def test_bad_args_are_skipped(tmp_path, monkeypatch):
    _trusted(monkeypatch, tmp_path, {"Stop": [{"command": "echo x", "args": "not a list"}]})
    assert EX.hooks(tmp_path)["Stop"] == []


def test_once_runs_once_a_session(tmp_path, monkeypatch):
    _trusted(monkeypatch, tmp_path, {"Stop": [{"command": "echo hi", "once": True}]})
    assert EX.run_hooks(tmp_path, "Stop", {"session": "s1"})[1] == "hi"
    assert EX.run_hooks(tmp_path, "Stop", {"session": "s1"})[1] == ""
    assert EX.run_hooks(tmp_path, "Stop", {"session": "s2"})[1] == "hi"


def test_async_hooks_do_not_wait_or_report(tmp_path, monkeypatch):
    _trusted(monkeypatch, tmp_path, {"Stop": [{"command": "sleep 1; echo done > async.flag", "async": True}]})
    t = time.time()
    assert EX.run_hooks(tmp_path, "Stop", {}) == (False, "")
    assert time.time() - t < 0.8
    for _ in range(40):
        if (tmp_path / "async.flag").exists():
            break
        time.sleep(0.1)
    assert (tmp_path / "async.flag").exists()


def test_session_start_matcher_picks_the_source(tmp_path, monkeypatch):
    _trusted(monkeypatch, tmp_path, {"SessionStart": [{"matcher": "compact", "command": "echo after-summary"},
                                                      {"matcher": "startup", "command": "echo fresh"}]})
    assert EX.run_hooks(tmp_path, "SessionStart", {"source": "startup"})[1] == "fresh"
    assert EX.run_hooks(tmp_path, "SessionStart", {"source": "compact"})[1] == "after-summary"
    assert EX.run_hooks(tmp_path, "SessionStart", {})[1] == "fresh"


def test_pre_tool_use_can_rewrite_the_call_and_rules_judge_the_new_one(tmp_path, monkeypatch):
    out = json.dumps({"updatedInput": {"command": "rm -rf build"}})
    _trusted(monkeypatch, tmp_path, {"PreToolUse": [{"matcher": "bash", "command": f"echo '{out}'"}]})
    (tmp_path / "home" / "permissions.json").write_text(json.dumps({"deny": ["bash:rm *"]}), encoding="utf-8")
    a = Agent(str(tmp_path), approve=lambda *x: ("yes", ""), mode="auto")
    args = {"command": "ls"}
    ok, why = a._permit("bash", args)
    assert args["command"] == "rm -rf build"
    assert not ok and "rule" in why


def test_permission_denied_hook_sees_the_call(tmp_path, monkeypatch):
    _trusted(monkeypatch, tmp_path, {"PermissionDenied": [{"matcher": "bash",
                                                           "command": "cat > denied.json; echo try a dry run"}]})
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    from meshpool_harness.agent import core as K
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "sessions")
    a = Agent(str(tmp_path), approve=lambda *x: ("no", "not now"), mode="ask")
    a._turn_no = 1
    out = a._run_tool({"id": "c", "type": "function",
                       "function": {"name": "bash", "arguments": json.dumps({"command": "make deploy"})}})
    assert "denied" in out and "try a dry run" in out
    d = json.loads((tmp_path / "denied.json").read_text())
    assert d["event"] == "PermissionDenied" and d["args"]["command"] == "make deploy" and "not now" in d["reason"]


def test_directory_added_hook(tmp_path, monkeypatch):
    proj = tmp_path
    other = tmp_path.parent / (tmp_path.name + "-other")
    other.mkdir()
    _trusted(monkeypatch, proj, {"DirectoryAdded": [{"command": "cat > added.json"}]})
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    from meshpool_harness.agent import core as K
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "sessions")
    a = Agent(str(proj), mode="auto")
    a.add_dir(str(other))
    assert json.loads((proj / "added.json").read_text())["path"] == str(other.resolve())
