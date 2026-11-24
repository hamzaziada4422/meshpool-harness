"""Saved rules: "always ask" rules (any mode), and the rules page's API (list, add, remove)."""
import json

import pytest

from meshpool_harness.agent import core
from meshpool_harness.ui import features_api as FA


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    for k in ("MH_TRUST_PROJECT", "MH_ALLOW", "MH_DENY", "MH_ASK"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "home").mkdir()
    (tmp_path / "proj").mkdir()
    return tmp_path


def _rules(env, **lists):
    (env / "home" / "permissions.json").write_text(json.dumps(lists))


def test_an_ask_rule_asks_even_with_full_access(env):
    _rules(env, ask=["bash:git push*"])
    asked = []
    a = core.Agent(str(env / "proj"), approve=lambda *x: (asked.append(x), ("no", ""))[1], mode="auto")
    assert not a._permit("bash", {"command": "git push origin main"})[0] and asked
    assert "says to ask" in asked[0][1]
    assert a._permit("bash", {"command": "git status"})[0]                     # others: full access as before
    b = core.Agent(str(env / "proj"), approve=lambda *x: ("yes", ""), mode="auto")
    assert b._permit("bash", {"command": "git push origin main"})[0]           # the user said yes


def test_an_ask_rule_beats_an_allow_rule_and_covers_each_command(env):
    _rules(env, allow=["bash:git push*"], ask=["bash:git push*"])
    asked = []
    a = core.Agent(str(env / "proj"), approve=lambda *x: (asked.append(x), ("no", ""))[1], mode="ask")
    assert not a._permit("bash", {"command": "git push"})[0] and asked
    assert not a._permit("bash_many", {"commands": ["ls", "git push -f"]})[0]


def test_an_ask_rule_with_nobody_to_ask_refuses(env):
    _rules(env, ask=["edit"])
    a = core.Agent(str(env / "proj"), approve=None, mode="auto")
    ok, why = a._permit("edit", {"path": "a.py", "old": "x", "new": "y"})
    assert not ok and "nobody to ask" in why


def test_rules_api_lists_adds_and_removes(env):
    _rules(env, allow=["edit"], projects={"/some/folder": {"allow": ["bash:npm test"]}})
    st, r = FA.rules_get()
    assert r["allow"] == ["edit"] and r["ask"] == [] and r["folders"] == [{"folder": "/some/folder", "allow": ["bash:npm test"]}]
    st, r = FA.rules_post({"action": "add", "list": "ask", "rule": "bash:git push*"})
    assert r["ask"] == ["bash:git push*"]
    st, r = FA.rules_post({"action": "remove", "list": "allow", "rule": "bash:npm test", "folder": "/some/folder"})
    assert r["folders"] == []
    st, r = FA.rules_post({"action": "remove", "list": "allow", "rule": "edit"})
    assert r["allow"] == []
    saved = json.loads((env / "home" / "permissions.json").read_text())
    assert saved["ask"] == ["bash:git push*"] and saved["projects"]["/some/folder"]["allow"] == []
    with pytest.raises(ValueError):
        FA.rules_post({"action": "remove", "list": "deny", "rule": "nope"})
    with pytest.raises(ValueError):
        FA.rules_post({"action": "add", "list": "sometimes", "rule": "edit"})
    with pytest.raises(ValueError):
        FA.rules_post({"action": "add", "list": "ask", "rule": "a\nb"})


def test_a_broken_rules_file_is_never_overwritten(env):
    (env / "home" / "permissions.json").write_text("{not json")
    with pytest.raises(ValueError, match="left as it is"):
        FA.rules_post({"action": "add", "list": "ask", "rule": "edit"})
    assert (env / "home" / "permissions.json").read_text() == "{not json"
