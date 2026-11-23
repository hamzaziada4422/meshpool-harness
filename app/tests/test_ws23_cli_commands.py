"""ws23 chunk B (CLI-01): every app action has one shared function, and the CLI calls the same one the route does.

In-process, temp home, scripted model. Each test fails on the code before the change (no shared functions, no commands)."""
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

from meshpool_harness import chats, cli, cli_app, usage_view
from meshpool_harness.agent import tui as T
from meshpool_harness.ui import server as SRV
from ws23_kit import finish, home, new_chat, proj, script  # noqa: F401


def run(capsys, *argv):
    rc = cli.main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# ---------- 1. routes call the shared functions ---------------------------------------------------------------------
def test_routes_are_one_line_calls_to_the_shared_functions():
    src = inspect.getsource(SRV)
    for needle in ("A.session_action(", "SK.api_action(", "PS.api_action(", "AS.api_action(", "W.set_default_model(",
                   "UV.summary(", "SA.save_settings("):
        assert needle in src, needle
    for gone in ("s_.set_plan(", "SK.toggle(", "PS.memory_clear(", "AS.add_task("):   # the inline logic moved out
        assert gone not in src, gone


def test_shared_functions_match_what_the_server_returns(monkeypatch, proj):
    from meshpool_harness import assistant as AS
    from meshpool_harness import settings_api as SA
    assert SRV.assistant_view() == AS.api_view()
    assert SRV.get_settings()["fields"] == SA.values()
    s = new_chat(proj)
    v = chats.session_action(s.id, "plan", {"on": True, "since": 0})
    assert v["plan"] is True and s.plan is True and chats.read_meta(s.id)["plan"] is True
    with pytest.raises(ValueError):
        chats.session_action(s.id, "nonsense", {})


# ---------- 2. chats ---------------------------------------------------------------------------------------------
def test_chats_cli_roundtrip(monkeypatch, proj, capsys):
    a, b = new_chat(proj), new_chat(proj)
    rc, out, _ = run(capsys, "chats", "list", "--json")
    ids = {r["id"] for r in json.loads(out)}
    assert rc == 0 and {a.id, b.id} <= ids
    assert run(capsys, "chats", "rename", a.id, "Quarterly", "plan")[0] == 0
    assert next(r for r in chats.listing() if r["id"] == a.id)["title"] == "Quarterly plan"
    assert run(capsys, "chats", "pin", a.id)[0] == 0
    assert next(r for r in chats.listing() if r["id"] == a.id)["pinned"]
    assert run(capsys, "chats", "archive", b.id)[0] == 0
    assert b.id not in {r["id"] for r in json.loads(run(capsys, "chats", "list", "--json")[1])}
    assert b.id in {r["id"] for r in json.loads(run(capsys, "chats", "list", "--all", "--json")[1])}
    assert run(capsys, "chats", "delete", a.id)[0] == 0
    assert a.id not in {r["id"] for r in chats.listing()}


def test_fork_from_cli_and_from_the_app_action(monkeypatch, proj, capsys):
    script(monkeypatch, "one done", "two done")
    s = new_chat(proj)
    finish(s, "first request")
    finish(s, "second request")
    sid = s.id
    rc, out, err = run(capsys, "chats", "fork", sid, "--turn", "1", "--json")
    assert rc == 0, err
    new = json.loads(out)["id"]
    assert new != sid
    users = [e for e in chats.view(new, 0)["events"] if e["type"] == "user"]
    assert len(users) == 1 and "first request" in users[0]["text"]
    assert chats.read_meta(new)["forked_from"]["sid"] == sid
    orig = [e for e in chats.view(sid, 0)["events"] if e["type"] == "user"]
    assert len(orig) == 2                                         # the original is unchanged
    again = chats.thread_action(sid, {"action": "fork", "message_index": None})["id"]     # the app's menu item
    assert again not in (sid, new) and len([e for e in chats.view(again, 0)["events"] if e["type"] == "user"]) == 2
    rc, _, err = run(capsys, "chats", "fork", sid, "--turn", "9")
    assert rc == 1 and err.startswith("error:") and "no turn 9" in err


def test_slash_fork_and_rewind_call_chat_rewind(monkeypatch):
    from meshpool_harness import chat_rewind as CR
    calls = []
    monkeypatch.setattr(CR, "points", lambda sid, session=None: [{"turn": 1, "message_index": 4, "text": "hi"}])
    monkeypatch.setattr(CR, "rewind", lambda sid, mi, files=True, session=None: calls.append(("rewind", mi)) or {"text": "hi"})
    monkeypatch.setattr(CR, "fork", lambda sid, mi=None, session=None: calls.append(("fork", mi)) or "new1")
    assert run_text(["chats", "fork", "abc", "--turn", "1"])[1].startswith("forked abc -> new1")
    assert ("fork", 4) in calls

    class Fake:
        term = type("Tm", (), {"prefill": "", "p": lambda *a, **k: None, "st": type("St", (), {"dim": staticmethod(str)})})()
        s = type("S", (), {"id": "abc"})()
        _turn_pick = lambda self, arg, title: 1  # noqa: E731
        _rebind = lambda self: None  # noqa: E731
        _need_shared = lambda self: True  # noqa: E731
    f = Fake()
    T.Session.cmd_rewind(f, "1")
    assert ("rewind", 4) in calls and f.term.prefill == "hi"


def run_text(argv):
    return cli_app.run_text(argv)


# ---------- 3. projects, memory, skills, schedule change the stores the app reads --------------------------------------
def test_projects_memory_skills_schedule_cli(tmp_path, capsys):
    from meshpool_harness import assistant as AS
    from meshpool_harness import memstore, project_store as PS, skills as SK
    rc, out, _ = run(capsys, "projects", "new", "Acme", "--json")
    pid = json.loads(out)["id"]
    assert PS.detail(pid)["name"] == "Acme"
    assert run(capsys, "projects", "memory", pid, "add", "prefers", "tables")[0] == 0
    assert "prefers tables" in PS.memory(pid)
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    assert run(capsys, "projects", "knowledge", pid, "add", str(f))[0] == 0
    assert [k["name"] for k in PS.detail(pid)["knowledge"]] == ["notes.txt"]
    assert run(capsys, "projects", "delete", pid)[0] == 0 and not PS.get_meta(pid)

    rc, out, _ = run(capsys, "memory", "add", "I like short answers", "--json")
    fid = json.loads(out)["fact"]["id"]
    assert any(x["id"] == fid for x in memstore.load())
    assert "short answers" in run(capsys, "memory", "search", "short")[1]
    assert run(capsys, "memory", "forget", fid)[0] == 0
    assert not any(x["id"] == fid for x in memstore.load())

    body = tmp_path / "body.md"
    body.write_text("Say hello.")
    assert run(capsys, "skills", "new", "Greeter", "--description", "greets people", "--body-file", str(body))[0] == 0
    sid = next(x["id"] for x in SK.load_all() if "greet" in x["id"].lower())
    assert run(capsys, "skills", "disable", sid)[0] == 0
    assert not next(x for x in SK.load_all() if x["id"] == sid).get("enabled", True)
    assert run(capsys, "skills", "remove", sid)[0] == 0
    assert not any(x["id"] == sid for x in SK.load_all())

    assert run(capsys, "schedule", "add", "--name", "Brief", "--prompt", "summarise mail", "--when", "daily",
               "--time", "08:15")[0] == 0
    t = next(x for x in AS.tasks() if x["name"] == "Brief")
    assert t["prompt"] == "summarise mail"
    assert run(capsys, "schedule", "disable", t["id"])[0] == 0
    assert not next(x for x in AS.tasks() if x["id"] == t["id"])["enabled"]
    assert run(capsys, "schedule", "delete", t["id"])[0] == 0
    assert not any(x["id"] == t["id"] for x in AS.tasks())


# ---------- 4. usage is the ledger's, not the CLI's ----------------------------------------------------------------------
def test_usage_cli_uses_shared_summary(monkeypatch, capsys):
    monkeypatch.setattr(usage_view, "summary", lambda **kw: {
        "usage": {"total": {"calls": 7, "in": 1234, "cached": 5, "out": 99, "cost": 0.5, "unpriced_calls": 0},
                  "by_model": {"mx": {"calls": 7, "cost": 0.5}}}, "days": [], "key": {}})
    rc, out, _ = run(capsys, "usage")
    assert rc == 0 and "7 model calls" in out and "1,234 input" in out and "$0.5000" in out and "mx" in out
    src = inspect.getsource(cli_app)
    assert "cost_of" not in src and "price_for" not in src and "receipts import" not in src
    assert "core import usage" not in src and "U.summarize" not in src


def test_usage_view_delegates_to_the_ledger(monkeypatch):
    from meshpool_harness.core import usage as U
    seen = []
    monkeypatch.setattr(U, "summarize", lambda recs: seen.append(list(recs)) or {
        "by_model": {}, "by_source": {}, "by_chat": {}, **{k: 0 for k in ("in", "cached", "out", "calls", "cost",
                                                                         "unpriced_calls", "unreported_calls", "cache_write",
                                                                         "reasoning")}})
    r = usage_view.summary(parts=("usage",))
    assert seen and "total" in r["usage"]


# ---------- 5. agents and models ---------------------------------------------------------------------------------------
def test_agents_models_cli(capsys, monkeypatch):
    rc, out, _ = run(capsys, "agents", "list", "--json")
    assert json.loads(out) == json.loads(json.dumps(chats.harness_info(), default=str))
    from meshpool_harness import config as C
    from meshpool_harness.ui import workbench as W
    monkeypatch.setattr(W, "models", lambda refresh=False: {"models": [], "default": "x", "favorites": []})
    assert run(capsys, "models", "default", "provider:my-model")[0] == 0
    assert C.read().get("MH_MODEL") == "provider:my-model"


def test_settings_cli_masks_secrets_and_validates(capsys):
    assert run(capsys, "settings", "set", "MH_REASONING_EFFORT=low")[0] == 0
    assert "MH_REASONING_EFFORT=low" in run(capsys, "settings", "get", "MH_REASONING_EFFORT")[1]
    rc, out, _ = run(capsys, "settings", "get", "MESHPOOL_API_KEY")
    assert "sk-test-key" not in out
    rc, _, err = run(capsys, "settings", "set", "NOT_A_SETTING=1")
    assert rc == 1 and err.startswith("error:") and "Unknown setting" in err


# ---------- 6. help and slash-command completeness (B7) --------------------------------------------------------------------
MATRIX_SLASH = {"model", "plan", "goal", "style", "agent", "mode", "compact", "grade", "undo", "diff", "rewind", "fork",
                "chats", "resume", "rename", "delete", "attach", "memory", "project", "skills", "mcp", "connect",
                "schedule", "settings", "usage", "receipt", "checks", "worktree", "import", "effort", "auto"}


def test_every_matrix_row_has_a_command_and_help_lists_it(capsys):
    names = {c[0] for c in T.COMMANDS}
    assert MATRIX_SLASH <= names, MATRIX_SLASH - names
    assert {"tokens", "cost"} <= set(T.ALIASES) and T.ALIASES["cost"] == "usage"
    for n in names:
        assert hasattr(T.Session, "cmd_" + n), n
    shown = []
    fake = type("F", (), {"term": type("Tm", (), {"st": type("St", (), {"cmd": staticmethod(str), "dim": staticmethod(str)}),
                                                  "scroll": lambda self, rows: shown.extend(rows)})()})()
    T.Session.cmd_help(fake, "")
    text = "\n".join(shown)
    for n in MATRIX_SLASH:
        assert f"/{n}" in text, n


def test_meshpool_help_names_every_command(capsys):
    assert cli.main(["help"]) == 0
    out = capsys.readouterr().out
    for c in sorted(cli.COMMANDS - {"app", "acp"}):
        assert re.search(rf"^\s+{re.escape(c)}\s", out, re.M), c
    for c in cli.COMMANDS:
        assert c in cli._HELP, c
        assert cli.main([c, "-h"]) == 0 and capsys.readouterr().out.startswith(("Usage", "usage", ""))
    assert cli.main(["help", "telemetry"]) == 0 and "MH_OTEL_CONTENT" in capsys.readouterr().out
    assert "permissions" in out and "effort" in cli._HELP["agent"]


def test_connect_list_and_instructions(monkeypatch, capsys):
    from meshpool_harness import assistant as AS
    from meshpool_harness.agent import connect_cli
    shown = []
    monkeypatch.setattr(connect_cli, "listing", lambda: shown.append(1) or 0)
    assert cli.main(["connect", "--list"]) == 0 and shown
    assert run(capsys, "settings", "instructions", "Always", "reply", "briefly")[0] == 0
    assert AS.instructions().strip() == "Always reply briefly"


def test_slash_commands_run_the_same_handlers_as_the_cli(monkeypatch, capsys):
    from meshpool_harness.agent.tui_session import SharedCommands
    shown = []
    St = type("St", (), {"amber": staticmethod(str), "dim": staticmethod(str)})
    fake = type("F", (), {"term": type("Tm", (), {"st": St, "scroll": lambda self, rows: shown.extend(rows)})(),
                          "_cli": SharedCommands._cli, "s": type("S", (), {"id": "sid1"})(),
                          "_need_shared": lambda self: True})()
    seen = []
    monkeypatch.setattr(cli_app, "main", lambda argv: seen.append(argv) or print("ok-output") or 0)
    SharedCommands.cmd_skills(fake, "enable abc")
    SharedCommands.cmd_memory(fake, "add I like tea")
    SharedCommands.cmd_project(fake, "use p_1234567890")
    SharedCommands.cmd_settings(fake, "MH_REASONING_EFFORT high")
    SharedCommands.cmd_rename(fake, "New title")
    assert seen == [["skills", "enable", "abc"], ["memory", "add", "I like tea"], ["chats", "move", "sid1", "p_1234567890"],
                    ["settings", "set", "MH_REASONING_EFFORT=high"], ["chats", "rename", "sid1", "New title"]]
    assert any("ok-output" in x for x in shown)


# ---------- 7. /worktree forwards to the worktree CLI ------------------------------------------------------------------------
def test_worktree_slash_forwards_args(monkeypatch):
    from meshpool_harness import worktrees as WT
    got = []
    monkeypatch.setattr(WT, "cli", lambda argv, out=None: got.append(argv) or print("listed", file=out) or 0)
    shown = []
    fake = type("F", (), {"term": type("Tm", (), {"scroll": lambda self, rows: shown.extend(rows)})()})()
    T.Session.cmd_worktree(fake, "diff wt1 --stat")
    assert got == [["diff", "wt1", "--stat"]] and any("listed" in x for x in shown)
