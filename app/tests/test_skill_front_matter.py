"""Skill front matter: when_to_use, disable-model-invocation, user-invocable, arguments, disallowed-tools, paths."""
import json

import pytest

from meshpool_harness import skills as SK


def _skill(home, sid, front, body="Do the thing."):
    d = home / "skills" / sid
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {sid}\n{front}\n---\n{body}\n", encoding="utf-8")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "skills").mkdir(parents=True)
    monkeypatch.setattr(SK, "ensure_defaults", lambda: None)
    return tmp_path / "home"


def test_when_to_use_joins_the_description(home):
    _skill(home, "changelog", "description: Write a changelog entry.\nwhen_to_use: after a release is tagged")
    assert "Use when: after a release is tagged" in SK.catalog_text()
    assert SK.search("release tagged")[0]["id"] == "changelog"


def test_user_only_skill_is_hidden_from_the_model(home):
    _skill(home, "deploy", "description: Deploy to production.\ndisable-model-invocation: true")
    _skill(home, "lint", "description: Lint the code.")
    assert "deploy" not in SK.catalog_text() and "lint" in SK.catalog_text()
    assert SK.search("deploy production") == []
    with pytest.raises(ValueError, match="only when the user asks"):
        SK.instructions("deploy")
    assert "Deploy" in SK.instructions("deploy", by_user=True)
    assert "Do the thing" in SK.expand_mentions("please $deploy now")


def test_model_only_skill_is_not_expanded_from_a_mention(home):
    _skill(home, "internal", "description: Background facts.\nuser-invocable: false")
    assert SK.expand_mentions("use $internal") == "use $internal"
    assert "Do the thing" in SK.instructions("internal")


def test_arguments_are_filled_in(home):
    _skill(home, "fix-issue", "description: Fix an issue.\nargument-hint: [number] [priority]",
           body="Fix issue $1 at priority $2. All: $ARGUMENTS")
    out = SK.instructions("fix-issue", "123 high")
    assert "Fix issue 123 at priority high. All: 123 high" in out
    out = SK.expand_mentions("$fix-issue 77 low\nthanks")
    assert "Fix issue 77 at priority low" in out
    _skill(home, "plain", "description: Plain.")
    assert "Arguments: src/app.py" in SK.instructions("plain", "src/app.py")


def test_lists_in_front_matter(home):
    _skill(home, "safe", "description: Read only review.\ndisallowed-tools:\n  - bash\n  - edit\npaths: [\"*.sql\", migrations/**]")
    s = SK.get("safe")
    assert s["disallowed"] == ["bash", "edit"] and s["paths"] == ["*.sql", "migrations/**"]
    _skill(home, "csv", "description: x.\ndisallowed-tools: bash, write_file")
    assert SK.get("csv")["disallowed"] == ["bash", "write_file"]


def test_paths_suggest_a_skill_once(home):
    _skill(home, "sql-style", "description: Our SQL style.\npaths: [\"*.sql\"]")
    seen = set()
    assert "sql-style" in SK.for_path("db/q.sql", seen)
    assert SK.for_path("db/other.sql", seen) == ""
    assert SK.for_path("app.py", set()) == ""


def test_disallowed_tools_hold_for_the_turn(home, tmp_path, monkeypatch):
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.agent import core as K
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "sessions")
    _skill(home, "review-only", "description: Review without changing.\ndisallowed-tools: [bash, edit]")
    proj = tmp_path / "proj"
    proj.mkdir()
    a = Agent(str(proj), approve=lambda *x: ("yes", ""), mode="auto")
    a._turn_no = 1
    run = lambda n, ar: a._run_tool({"id": "c", "type": "function", "function": {"name": n, "arguments": json.dumps(ar)}})
    assert "Review without changing" in run("use_skill", {"name": "review-only"})
    ok, why = a._permit("bash", {"command": "ls"})
    assert not ok and "review-only" in why
    assert a._permit("write_file", {"path": "a.txt", "content": "x"})[0] is False
    assert a._permit("read_file", {"path": "a.txt"})[0] is True
    a._skill_block = {}                                   # a new turn starts clear
    assert a._permit("bash", {"command": "ls"})[0] is True
