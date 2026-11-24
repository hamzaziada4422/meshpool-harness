"""Lean Work (on by default; MH_LEAN_WORK=0 turns it off): Work chats run as lean core (Codex style, short prompt plus
a Work section), keeping the office and web tools. With 0, Work chats run the full Cowork lane. No network and no model
calls (the transport is scripted)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.hrouter import profiles as PR  # noqa: E402
from meshpool_harness.hrouter.route import Route  # noqa: E402

CODEX3 = ["shell", "apply_patch", "update_plan"]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", "none")
    for var in ("MH_LEAN_WORK", "MH_ENGINE_STYLE", "MH_HARNESS_PROFILE", "MH_CORE_PROMPT_FILE", "MH_APPEND_PROMPT"):
        monkeypatch.delenv(var, raising=False)


def _work(tmp_path, profile="core", spec="data", **kw):
    a = Agent(str(tmp_path), mode="auto", profile="work", emit=lambda e: None, **kw)
    a.set_route(Route(spec=spec, family=spec.split(".")[0], base="work", profile=profile,
                      settings=PR.settings(profile)), "what is the total revenue?")
    return a


def _names(a):
    return [t["function"]["name"] for t in a.tool_schemas()]


class _Mcp:
    def schemas(self):
        mk = lambda n: {"type": "function", "function": {"name": n, "description": "", "parameters": {  # noqa: E731
            "type": "object", "properties": {}}}}
        return [mk("mcp__demo__calculate"), mk("mcp__meshpool_office__make_deck"),
                mk("mcp__meshpool_office__make_document"), mk("mcp__meshpool_web__web_search")]

    def has(self, name):
        return name.startswith("mcp__")


def test_flag_off_leaves_work_chats_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LEAN_WORK", "0")
    a = _work(tmp_path)
    assert not a._style() and not a._lean() and not a._codex()
    assert a.messages[0]["content"].startswith("You are Meshpool Cowork")
    names = _names(a)
    assert {"bash", "read_file", "office_task", "web_research"} <= set(names) and "shell" not in names
    a.mcp = _Mcp()
    assert "mcp__meshpool_office__make_deck" in _names(a)


def test_on_by_default_and_only_0_turns_it_off(tmp_path, monkeypatch):
    for d in "abc":
        (tmp_path / d).mkdir()
    assert _work(tmp_path / "a")._lean()                 # unset: on (work1)
    monkeypatch.setenv("MH_LEAN_WORK", "")
    assert _work(tmp_path / "b")._lean()
    monkeypatch.setenv("MH_LEAN_WORK", "0")
    assert not _work(tmp_path / "c")._style()


def test_flag_on_work_runs_lean_core_with_the_work_section(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    a = _work(tmp_path)
    assert a._style() and a._lean() and a._codex()
    sp = a.messages[0]["content"]
    assert sp.startswith(core._codex_prompt(lean=True))                     # prompt-core.md, then the Work section
    assert "## Data answers" in sp and "Compute every number with code over the full data" in sp
    assert "count the words before you answer" in sp and "office_task" in sp
    assert "<!--" not in sp and "NOTICE" not in sp and "You are Meshpool Cowork" not in sp
    assert "[Harness:" not in sp and "todo_write" not in sp
    section = sp.split("## Data answers", 1)[1].split("## Files and lookups", 1)[0]
    assert len(section.split()) <= 120
    assert len(sp) < len(core.WORK_SYSTEM)                                    # far smaller than the Work prompt


def test_flag_on_is_lean_whatever_the_route_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    for profile in ("full", "codex", "core"):
        a = _work(tmp_path, profile=profile)
        assert a._style() and a._lean(), profile
    b = Agent(str(tmp_path), mode="auto", profile="work")                    # no route at all
    assert b._lean() and "## Data answers" in b.messages[0]["content"]


def test_flag_on_work_keeps_its_office_and_web_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    a = _work(tmp_path)
    assert _names(a) == CODEX3 + core.LEAN_WORK_TOOLS
    assert a._tool_names() == CODEX3 + ["office_task", "web_research", "fetch_url"]
    a.mcp = _Mcp()
    assert not a.office_on
    names = _names(a)                         # the office server loads when a message asks for a file, as in Code
    assert "mcp__meshpool_office__make_deck" not in names and "office_task" in names
    assert "mcp__demo__calculate" in names and "mcp__meshpool_web__web_search" not in names   # built-in web tools
    a.office_on = True
    assert "mcp__meshpool_office__make_deck" in _names(a) and "mcp__meshpool_office__make_document" in _names(a)
    a.plan = True                                                            # plan mode keeps its read-only tools
    assert not a._codex() and "read_file" in _names(a)


def test_flag_on_does_not_touch_code_chats(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    c = Agent(str(tmp_path), mode="auto")
    c.set_route(Route(spec="code.debug", family="code", sub="debug", base="code", profile="core",
                      settings=PR.settings("core")), "fix it")
    assert c._lean() and c.messages[0]["content"] == core._codex_prompt(lean=True)
    assert "## Data answers" not in c.messages[0]["content"] and _names(c) == CODEX3
    c.mcp = _Mcp()
    assert "mcp__meshpool_office__make_deck" not in _names(c)                # Code: office tools only once asked
    f = Agent(str(tmp_path), mode="auto")
    f.set_route(Route(spec="code.debug", family="code", sub="debug", base="code", profile="full",
                      settings=PR.settings("full")), "fix it")
    assert not f._style() and not f._lean()


def test_first_message_is_lean_and_bash_is_an_unknown_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    (tmp_path / "orders.csv").write_text("id,amount\n1,10\n1,10\n2,5\n")
    a = _work(tmp_path)
    seq = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "bash", "arguments": json.dumps({"command": "echo hi"})}}]},
        {"role": "assistant", "content": "15"}]
    bodies = []

    def fake(base, key, body, stream):
        bodies.append(json.loads(json.dumps(body)))
        msg = seq.pop(0)
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, bool(msg.get("content"))
    a._post = fake
    assert a.send("What is the total amount in orders.csv?") == "15"
    first = bodies[0]["messages"][1]["content"]
    assert "<environment_context>" in first and "[Harness:" not in first and "orders.csv" in first   # preload
    assert [t["function"]["name"] for t in bodies[0]["tools"]] == CODEX3 + core.LEAN_WORK_TOOLS
    out = [m["content"] for m in a.messages if m["role"] == "tool"][0]
    assert "unknown tool 'bash'" in out
    assert a._grade_gate({}, {}, "15") == (False, "15")                      # no independent grader


def test_hand_built_decks_and_workbooks_are_refused_through_shell_too(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    a = _work(tmp_path)
    deck = ["python3", "-c", "from pptx import Presentation; p=Presentation(); p.slides.add_slide(l)"]
    assert "add_slides" in a._engine_only("shell", {"command": deck})
    assert "make_spreadsheet" in a._engine_only("shell", {"command": "python3 -c 'from openpyxl import Workbook; Workbook()'"})
    assert "make_spreadsheet" in a._engine_only("apply_patch", {"input": "*** Add File: m.py\n+wb = Workbook()\n"})
    assert a._engine_only("shell", {"command": ["python3", "-c", "import pandas as pd; print(1)"]}) == ""
    assert Agent(str(tmp_path), mode="auto")._engine_only("shell", {"command": deck}) == ""   # Code: free


def test_work_section_file_keeps_the_attribution_comment():
    d = Path(core.__file__).resolve().parents[2] / "defaults" / "engines" / "codex"
    text = (d / "prompt-work.md").read_text(encoding="utf-8")
    assert text.startswith("<!--") and "Apache License, Version 2.0" in text and "prompt-core.md" in text
    assert "not sent to the model" in text


def test_office_direct_switch_allows_hand_built_files_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    a = _work(tmp_path)
    hand = {"command": ["bash", "-lc", "python3 -c 'from openpyxl import Workbook; wb = Workbook(); wb.save(\"b.xlsx\")'"]}
    assert a._engine_only("shell", hand)                          # default: the engines own workbooks
    assert "not python-docx, openpyxl or python-pptx by hand" in a.messages[0]["content"]
    monkeypatch.setenv("MH_OFFICE_DIRECT", "1")
    (tmp_path / "d").mkdir()
    b = _work(tmp_path / "d")
    assert b._engine_only("shell", hand) == ""
    sysmsg = b.messages[0]["content"]
    assert "write it yourself in one Python script" in sysmsg and "by hand" not in sysmsg
