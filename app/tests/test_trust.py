"""Folder trust: a project's own hooks and allow rules are ignored until the user trusts the folder."""
import json

from meshpool_harness import trust as TR
from meshpool_harness.agent import core
from meshpool_harness.agent import extras as EX


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MH_TRUST_PROJECT", raising=False)
    (tmp_path / "home").mkdir()
    proj = tmp_path / "repo"
    (proj / ".meshpool").mkdir(parents=True)
    (proj / ".meshpool" / "hooks.json").write_text(json.dumps(
        {"PreToolUse": [{"matcher": "bash:ls*", "command": "echo pwned > hook-ran.txt; exit 2"}]}), encoding="utf-8")
    (proj / ".meshpool" / "permissions.json").write_text(json.dumps(
        {"allow": ["bash:make lint*"], "deny": ["web_search"]}), encoding="utf-8")
    return proj


def test_untrusted_folder_ignores_hooks_and_allow_rules(tmp_path, monkeypatch):
    proj = _setup(monkeypatch, tmp_path)
    assert TR.needs_prompt(proj) == [".meshpool/hooks.json", ".meshpool/permissions.json"]
    assert "trust the folder" in TR.message(proj)
    assert EX.hooks(proj)["PreToolUse"] == []
    assert EX.run_hooks(proj, "PreToolUse", {}, tool="bash", args={"command": "ls"}) == (False, "")
    assert not (proj / "hook-ran.txt").exists()
    a = core.Agent(str(proj), approve=lambda *x: ("no", ""), mode="ask")
    assert not a._permit("bash", {"command": "make lint"})[0]   # the repo's allow rule is not used
    ok, why = a._permit("web_search", {})                                       # its deny rule still is
    assert not ok and "saved rule" in why


def test_trusting_turns_them_on_and_revoke_turns_them_off(tmp_path, monkeypatch):
    proj = _setup(monkeypatch, tmp_path)
    TR.trust(proj)
    assert TR.is_trusted(proj) and TR.is_trusted(proj / "sub" / "dir") and TR.needs_prompt(proj) == []
    blocked, _ = EX.run_hooks(proj, "PreToolUse", {}, tool="bash", args={"command": "ls"})
    assert blocked and (proj / "hook-ran.txt").exists()
    a = core.Agent(str(proj), approve=lambda *x: ("no", ""), mode="ask")
    assert a._permit("bash", {"command": "make lint"}) == (True, "")
    assert TR.folders() == [TR._norm(proj)]
    assert TR.revoke(proj) and not TR.is_trusted(proj)
    assert EX.hooks(proj)["PreToolUse"] == []


def test_env_override_and_plain_folders(tmp_path, monkeypatch):
    proj = _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("MH_TRUST_PROJECT", "1")
    assert TR.is_trusted(proj) and TR.needs_prompt(proj) == []
    monkeypatch.delenv("MH_TRUST_PROJECT")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert TR.needs_prompt(plain) == [] and TR.message(plain) == ""
    (tmp_path / "x").write_text("not a dir")
    assert not TR.is_trusted(tmp_path / "missing")


def test_api_and_cli(tmp_path, monkeypatch, capsys):
    proj = _setup(monkeypatch, tmp_path)
    from urllib.parse import urlparse
    from meshpool_harness.ui import features_api as F
    st, out = F.handle("GET", urlparse("/api/trust?root=" + str(proj)))
    assert st == 200 and out["trusted"] is False and len(out["risky"]) == 2
    st, out = F.handle("POST", urlparse("/api/trust"), json.dumps({"root": str(proj), "action": "trust"}).encode())
    assert out["trusted"] is True
    from meshpool_harness import cli
    assert cli.main(["trust", str(proj), "--revoke"]) == 0
    assert "No longer trusted" in capsys.readouterr().out and not TR.is_trusted(proj)
    assert cli.main(["trust", str(proj)]) == 0 and TR.is_trusted(proj)
    from meshpool_harness.ui.agent_api import _trust_note
    assert _trust_note(str(proj)) is None
    TR.revoke(proj)
    assert _trust_note(str(proj))["files"]
