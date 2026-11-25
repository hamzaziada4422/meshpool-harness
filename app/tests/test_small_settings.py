"""Jujutsu and Sapling stores skipped by search, summary instructions, instruction files excluded by pattern, extra
provider headers and query parameters, terminal title."""
import io
import json

from meshpool_harness.agent import compaction as CP
from meshpool_harness.agent import extras as EX
from meshpool_harness.core import endpoint as EP
from meshpool_harness.core.tools import Workspace


def test_search_skips_jj_and_sl(tmp_path):
    for d in (".jj", ".sl", "src"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "a.txt").write_text("needle\n")
    ws = Workspace(str(tmp_path), str(tmp_path / "run"))
    out = ws.grep("needle", ".")
    assert "src" in out and ".jj" not in out and ".sl" not in out
    found = ws.glob("**/a.txt")
    assert "src" in found and ".jj" not in found and ".sl" not in found


class _Client:
    def __init__(self):
        self.seen = None

    def chat(self, **kw):
        self.seen = kw["messages"][0]["content"]
        return {"choices": [{"message": {"content": "note"}}]}


def test_summary_instructions_text_or_file(tmp_path, monkeypatch):
    c = _Client()
    monkeypatch.setenv("MH_COMPACT_PROMPT", "Keep every SQL query verbatim.")
    CP.summarize([{"role": "user", "content": "x"}], "task", client=c, model="m")
    assert c.seen.startswith("Keep every SQL query verbatim.")
    f = tmp_path / "keep.md"
    f.write_text("Keep the test names.")
    monkeypatch.setenv("MH_COMPACT_PROMPT", str(f))
    CP.summarize([{"role": "user", "content": "x"}], "task", client=c, model="m")
    assert c.seen.startswith("Keep the test names.")
    monkeypatch.setenv("MH_COMPACT_PROMPT", str(tmp_path / "missing.md"))
    CP.summarize([{"role": "user", "content": "x"}], "task", client=c, model="m")
    assert c.seen == CP.SYSTEM


def test_instruction_files_can_be_excluded(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "legacy").mkdir()
    (repo / "AGENTS.md").write_text("Top rules.")
    (repo / "legacy" / "AGENTS.md").write_text("Old rules.")
    (repo / ".cursor" / "rules").mkdir(parents=True)
    (repo / ".cursor" / "rules" / "style.md").write_text("Cursor style.")
    assert "Cursor style" in EX.instruction_text(repo)
    monkeypatch.setenv("MH_INSTRUCTIONS_EXCLUDE", "*/.cursor/rules/*, legacy/AGENTS.md")
    t = EX.instruction_text(repo)
    assert "Top rules" in t and "Cursor style" not in t
    assert EX.nested_instructions(repo, "legacy/x.py", set()) == ""
    monkeypatch.delenv("MH_INSTRUCTIONS_EXCLUDE")
    assert "Old rules" in EX.nested_instructions(repo, "legacy/x.py", set())


def test_extra_headers_never_replace_the_key(monkeypatch):
    monkeypatch.setenv("MH_EXTRA_HEADERS", json.dumps({"X-Team": "data", "Authorization": "Bearer evil",
                                                       "Bad Name": "x", "X-Inject": "a\r\nb"}))
    url, body, hdrs = EP.chat_request("https://api.example.com/v1", "real-key", {"model": "m", "messages": []})
    assert hdrs["X-Team"] == "data" and "real-key" in hdrs.get("Authorization", "")
    assert "Bad Name" not in hdrs and "X-Inject" not in hdrs
    monkeypatch.setenv("MH_EXTRA_HEADERS", "X-One: 1; X-Two: 2")
    assert EP.extra_headers() == {"X-One": "1", "X-Two": "2"}


def test_extra_query(monkeypatch):
    monkeypatch.setenv("MH_EXTRA_QUERY", "?api-version=2025-01-01&tier=low")
    url, _, _ = EP.chat_request("https://api.example.com/v1", "k", {"model": "m", "messages": []})
    assert url.endswith("api-version=2025-01-01&tier=low") and url.count("?") == 1
    assert EP.with_query("https://x/y?a=1") == "https://x/y?a=1&api-version=2025-01-01&tier=low"


def test_terminal_title_only_on_a_real_terminal(monkeypatch):
    from meshpool_harness.agent import tui as TU

    class Tty(io.StringIO):
        def isatty(self):
            return True
    t = TU.Terminal.__new__(TU.Terminal)
    t.out, t.cwd, t.keys_factory, t.interactive = Tty(), "/home/me/proj", object(), True
    monkeypatch.setenv("TERM", "xterm")
    t.set_title("fix tests", busy=True)
    assert t.out.getvalue() == "\x1b]0;● meshpool · proj · fix tests\x07"
    t.out = io.StringIO()
    t.set_title("x")
    assert t.out.getvalue() == ""
    t.out = Tty()
    monkeypatch.setenv("MH_TERM_TITLE", "0")
    t.set_title("x")
    assert t.out.getvalue() == ""


class _FakeMcp:
    def __init__(self, ann):
        self.ann = ann

    def annotations(self, name):
        return self.ann.get(name, {})

    def has(self, name):
        return name in self.ann


def test_destructive_connector_tools_ask_even_with_full_access(tmp_path, monkeypatch):
    from meshpool_harness.agent import core as K
    from meshpool_harness.agent.core import Agent
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "sessions")
    asked = []
    a = Agent(str(tmp_path), approve=lambda kind, title, detail, *x: asked.append(title) or ("yes", ""), mode="auto")
    a.mcp = _FakeMcp({"mcp__crm__delete_contact": {"destructiveHint": True},
                      "mcp__crm__list_contacts": {"readOnlyHint": True}})
    assert a._permit("mcp__crm__list_contacts", {})[0] is True and not asked
    assert a._permit("mcp__crm__delete_contact", {"id": 1})[0] is True
    assert asked and "deletes or overwrites" in asked[0]
    a.always.add("mcp__crm__delete_contact")
    asked.clear()
    a._permit("mcp__crm__delete_contact", {"id": 2})
    assert asked                                            # "always" never covers it
    monkeypatch.setenv("MH_MCP_DESTRUCTIVE_ASK", "0")
    asked.clear()
    assert a._permit("mcp__crm__delete_contact", {"id": 3})[0] is True and not asked
    monkeypatch.delenv("MH_MCP_DESTRUCTIVE_ASK")
    b = Agent(str(tmp_path), approve=None, mode="auto")     # headless with full access: as before, it runs
    b.mcp = a.mcp
    assert b._permit("mcp__crm__delete_contact", {"id": 4})[0] is True
