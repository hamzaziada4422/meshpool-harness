"""A chat that asks for a PowerPoint (or a Word document, workbook or PDF) gets one, whichever agent it runs on.

Found by the owner on 0.0.7: "can you create a ppt on coffee" was answered with "I can't". Three causes: the pace
check took it for a quick question and its note says "Do not create or change files"; Meshpool Code in Codex style
had no office tools; and a chat could not change agent without starting over.
"""
import json

import pytest

from meshpool_harness import capabilities as CAP
from meshpool_harness import config as C
from meshpool_harness import harnesses as HR
from meshpool_harness import pace as PACE


@pytest.mark.parametrize("text, want", [
    ("can you create a ppt on coffee", ["presentation"]),
    ("make a powerpoint about our Q3 results", ["presentation"]),
    ("I need slides for a talk about coffee", ["presentation"]),
    ("give me a pitch deck for investors", ["presentation"]),
    ("write a memo about the launch", ["document"]),
    ("create an excel model of our revenue", ["spreadsheet"]),
    ("export the results as xlsx", ["spreadsheet"]),
    ("turn this into a PDF", ["pdf"]),
    ("can you make me a word doc and a spreadsheet", ["document", "spreadsheet"]),
    ("hi", []),
    ("summarize this pdf", []),                 # a file the user has: reading, not making
    ("review my slides", []),
    ("fix the bug in deck.py", []),
    ("what are good presentation skills", []),
])
def test_what_a_message_asks_to_make(text, want):
    assert CAP.needs(text) == want


@pytest.mark.parametrize("text", ["can you create a ppt on coffee", "could you make an xlsx of this",
                                  "can you do a quick deck on X"])
def test_a_file_to_make_is_never_a_quick_question(text):
    """The quick note says "Do not create or change files": a deck request must never get it."""
    assert PACE.classify(text) == "normal"
    assert PACE.classify("what is the capital of France") == "quick"     # quick questions still are


def test_every_meshpool_agent_can_make_office_files():
    for h in ({"id": "meshpool-work", "type": "builtin"}, {"id": "meshpool-code", "type": "builtin"},
              {"id": "pi", "type": "pi"}, {"id": "hermes", "type": "acp"}):
        assert CAP.of_harness(h) == CAP.OFFICE, h
    assert CAP.of_harness({"id": "custom", "type": "acp", "office": False}) == frozenset()


def test_pick_keeps_a_capable_agent_and_moves_off_one_that_cannot():
    ready = {"meshpool-work": {"id": "meshpool-work", "type": "builtin"},
             "custom": {"id": "custom", "type": "acp", "office": False},
             "pi": {"id": "pi", "type": "pi"}}
    assert CAP.pick(["presentation"], "pi", [], ready) == "pi"                          # it can: it stays
    assert CAP.pick(["presentation"], "custom", ["pi", "meshpool-work"], ready) == "pi"  # Auto's order among those that can
    assert CAP.pick([], "custom", [], ready) == "custom"                                 # nothing to make: no move
    assert CAP.pick(["presentation"], "custom", [], {"custom": ready["custom"]}) is None  # none can: say so


def test_the_note_says_it_can_and_names_the_tools():
    n = CAP.note(["presentation"])
    assert n.startswith(CAP.SEP) and "make_deck" in n and "Do not answer that you cannot" in n
    assert CAP.note([]) == ""


class _MCP:
    def __init__(self, names):
        self.names = names

    def schemas(self):
        return [{"type": "function", "function": {"name": n, "description": "", "parameters": {"type": "object"}}}
                for n in self.names]


def test_codex_style_shows_office_tools_only_once_a_message_asks_for_a_file(tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    a = core.Agent(str(tmp_path), profile="code")
    monkeypatch.setattr(a, "_codex", lambda: True)
    a.mcp = _MCP(["mcp__meshpool_office__make_deck", "mcp__meshpool-assistant__search_chats", "mcp__github__search"])
    names = [t["function"]["name"] for t in a.tool_schemas()]
    assert "mcp__meshpool_office__make_deck" not in names and "mcp__github__search" in names
    assert "mcp__meshpool-assistant__search_chats" not in names
    a.office_on = True
    names = [t["function"]["name"] for t in a.tool_schemas()]
    assert "mcp__meshpool_office__make_deck" in names and "mcp__meshpool-assistant__search_chats" not in names


@pytest.fixture
def chat(tmp_path, monkeypatch):
    from meshpool_harness.ui import agent_api as A
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    sessions = tmp_path / "mh" / "runs" / "sessions"
    monkeypatch.setitem(A.Agent.__init__.__globals__, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(A.Session, "_start_mcp", lambda self: None)
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    proj = tmp_path / "proj"
    proj.mkdir()
    return A, proj


def test_a_chat_on_an_agent_that_cannot_moves_with_its_history(chat, monkeypatch):
    """The chat runs on an agent without office tools; a PowerPoint request moves it to one that has them, in the same
    chat, with what was said so far, and says which agent took over."""
    A, proj = chat
    from meshpool_harness import harness_router as HRT
    s = A.Session(workdir=str(proj), harness="meshpool-work", kind="cowork")
    s.harness = {"id": "custom", "name": "Custom Agent", "type": "acp", "office": False}   # as if it ran on one
    s.emit({"type": "user", "text": "hi"})
    s.emit({"type": "assistant", "text": "Hello! How can I help?"})
    monkeypatch.setattr(HRT, "_ready", lambda kind: {"meshpool-work": HR.get("meshpool-work"),
                                                     "custom": dict(s.harness)})
    monkeypatch.setattr(HRT, "route", lambda text, kind: {"ranking": [{"harness": "meshpool-work"}]})
    caps = s._capability_turn("can you create a ppt on coffee")
    assert caps == ["presentation"]
    assert s.harness["id"] == "meshpool-work" and isinstance(s.agent, A.Agent)
    assert [m["role"] for m in s.agent.messages[1:]] == ["user", "assistant"]          # the chat so far went along
    assert s.agent.office_on is True and s.agent.id == s.id                           # same chat, office tools on
    st = [e for e in s.events if e.get("handoff")]
    assert st and "Custom Agent cannot make PowerPoint presentations" in st[-1]["handoff"]["reason"]
    saved = json.loads((A.SESSIONS / s.id / "session.json").read_text())
    assert saved["harness"] == "meshpool-work"                                        # reopens on the new agent


def test_a_chat_on_a_capable_agent_stays_and_gets_the_tools(chat, monkeypatch):
    A, proj = chat
    s = A.Session(workdir=str(proj), harness="meshpool-code", kind="code")
    agent = s.agent
    assert s._capability_turn("make a deck about our architecture") == ["presentation"]
    assert s.agent is agent and s.harness["id"] == "meshpool-code" and agent.office_on is True
    assert not any(e.get("handoff") for e in s.events)
    assert s._capability_turn("fix the failing test") == []


def test_when_no_agent_can_the_chat_says_so(chat, monkeypatch):
    A, proj = chat
    from meshpool_harness import harness_router as HRT
    s = A.Session(workdir=str(proj), harness="meshpool-work", kind="cowork")
    s.harness = {"id": "custom", "name": "Custom Agent", "type": "acp", "office": False}
    monkeypatch.setattr(HRT, "_ready", lambda kind: {"custom": dict(s.harness)})
    monkeypatch.setattr(HRT, "route", lambda text, kind: {"ranking": []})
    assert s._capability_turn("make a powerpoint") == []
    assert any("No agent in Meshpool can make PowerPoint presentations" in (e.get("text") or "") for e in s.events)


def test_switching_agent_mid_chat_keeps_the_chat(chat):
    """Picking another agent in a chat used to start a new chat; now the chat continues there with its history."""
    A, proj = chat
    s = A.Session(workdir=str(proj), harness="meshpool-work", kind="cowork")
    s.emit({"type": "user", "text": "plan a launch"})
    s.emit({"type": "assistant", "text": "Here is a plan."})
    cid = s.id
    s.switch_harness("meshpool-code")
    assert s.id == cid and s.harness["id"] == "meshpool-code" and s.agent.profile == "code"
    assert [m["content"] for m in s.agent.messages[1:]] == ["plan a launch", "Here is a plan."]
    assert any("You switched this chat to Meshpool Code" in (e.get("handoff") or {}).get("reason", "") for e in s.events)
    with pytest.raises(ValueError, match="Auto picks"):
        s.switch_harness("meshpool-auto")
    s.state = "working"
    with pytest.raises(ValueError, match="before switching"):
        s.switch_harness("meshpool-work")
