"""slide_layouts (engines/forge/tools.py): a read-only tool that names the archetypes that prove a slide's message
and the reference decks' numbers for them; pictures of library slides only on request and only when local."""
import os

from engines.forge import tools as T


def test_suggestions_follow_the_message():
    first = lambda m: T.suggest_layouts(m)[0]["archetype"]      # noqa: E731
    assert first("Two supply risks need owners this quarter") == "risk_matrix"
    assert first("Revenue grew 9.7% a year since 2023") == "column_trend"
    assert first("The cost base breaks down into four drivers") == "issue_tree"
    assert first("Leads drop off sharply between trial and purchase") == "funnel"
    assert first("A 36-month roadmap in three workstreams") == "gantt"
    assert first("Nothing here matches") == "exhibit_argument"
    assert len(T.suggest_layouts("risk roadmap drivers funnel cycle", limit=3)) == 3


def test_tool_text_without_the_knowledge_base(monkeypatch):
    monkeypatch.delenv("MESHPOOL_KB", raising=False)
    monkeypatch.delenv("MH_SHOW_REFERENCES", raising=False)
    text, err = T.slide_layouts("Two supply risks need owners this quarter", kind="consulting")
    assert not err and "risk_matrix" in text and "Reference pages (consulting)" in text
    assert "Reference picture" not in text and "Library:" not in text
    assert T.slide_layouts("  ")[1] is True


def test_pictures_only_when_asked_and_local(monkeypatch, tmp_path):
    from engines.forge import kb
    monkeypatch.setattr(kb, "available", lambda: True)
    monkeypatch.setattr(kb, "priors", lambda a, k="all": {"categories": ["risk_matrix"], "reference": {},
                                                          "library": None})
    monkeypatch.setattr(kb, "search", lambda **kw: [{"id": "x"}])
    pic = tmp_path / "x.webp"
    pic.write_bytes(b"x")
    monkeypatch.setattr(kb, "thumbnail", lambda i: pic)
    assert "Reference picture" not in T.slide_layouts("risk register")[0]
    monkeypatch.setenv("MH_SHOW_REFERENCES", "1")
    assert str(pic) in T.slide_layouts("risk register")[0]


def test_registered_as_a_read_only_office_tool():
    assert "slide_layouts" in T.TOOLS and T.TOOLS["slide_layouts"][3] == ["message"]
    from meshpool_harness.agent.core import PLAN_MCP
    from meshpool_harness.harnesses import safe_tools
    assert "mcp__meshpool_office__slide_layouts" in PLAN_MCP
    assert "mcp__meshpool_office__slide_layouts" in str(vars(safe_tools))
    from meshpool_harness.agent import office_mcp_server as O
    assert "slide_layouts" in O.TOOLS
