"""E2E autonomy (0.0.12): failed MCP results that say so, paging notes, lazy tool loading with many tools, a reply that
stops mid-job gets pushed on, and the prompt rules. No network: the transport is replaced by a script."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core, mcp_assist as MA  # noqa: E402
from meshpool_harness.agent.core import Agent, _stopped_early  # noqa: E402


@pytest.fixture(autouse=True)
def _sessions_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeMcp:
    """Looks like agent/mcp.py's Manager for what the agent uses: schemas, has, call."""

    def __init__(self, tools: dict, replies: dict | None = None):
        self.tools, self.replies, self.calls, self.last_images = tools, replies or {}, [], []

    def schemas(self):
        return [{"type": "function", "function": {"name": n, "description": f"[MCP server 'x'] {d}",
                                                  "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}}
                for n, d in self.tools.items()]

    def has(self, name):
        return name in self.tools

    def call(self, name, args):
        self.calls.append((name, args))
        return self.replies.get(name, ("ok", False))


def _agent(tmp_path, replies, mcp=None, mode="auto"):
    a = Agent(str(tmp_path), mode=mode, mcp=mcp)
    seq, seen = list(replies), []

    def fake(base, key, body, stream):
        seen.append(body)
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake
    return a, seen


# ---------- failures that do not look like failures ------------------------------------------------------------------
@pytest.mark.parametrize("text,bad", [
    ("Error: booking failed (code E42)", True),
    ("FAILED: no such file", True),
    ('{"ok": false, "error": "closing requires a note"}', True),
    ('{"success": false}', True),
    ('{"status": "error", "message": "x"}', True),
    ('{"error": "bad cursor"}', True),
    ("Traceback (most recent call last):\n  File x", False),   # 0.0.13: multi-line text is content, not a verdict
    ('{"ok": true, "ticket_id": "T-1"}', False),
    ('{"tickets": [], "error": null}', False),
    ('{"error": "partial", "results": [1, 2]}', False),
    ("Error handling in Python is done with try", False),
    ("3 tickets found", False),
    ("", False),
])
def test_soft_failure(text, bad):
    assert MA.soft_failure(text) is bad


def test_classify_marks_flagged_and_soft_failures_and_leaves_good_results():
    pre, post, ok = MA.classify("503 upstream timed out", True)
    assert pre.startswith("ERROR") and "FAILED" in post and "3 times" in post and not ok
    pre, post, ok = MA.classify('{"ok": false, "error": "x"}', False)
    assert not ok and "reports a failure" in post
    assert MA.classify('{"ok": true}', False) == ("", "", True)


def test_assist_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("MH_MCP_ASSIST", "0")
    assert MA.classify("boom", True) == ("", "", False)
    assert MA.classify('{"ok": false}', False) == ("", "", True)


def test_page_hint_names_the_next_page():
    h = MA.page_hint(json.dumps({"tickets": [1], "next_cursor": "3"}))
    assert "next_cursor" in h and '"3"' in h
    assert "more" in MA.page_hint('{"items": [], "has_more": true}')
    assert MA.page_hint('{"items": [], "next_cursor": null}') == ""
    assert MA.page_hint("plain text") == ""


def test_agent_tells_the_model_a_mcp_call_failed(tmp_path):
    mcp = FakeMcp({"mcp__x__book": "book"}, {"mcp__x__book": ("start must have a timezone", True)})
    a, seen = _agent(tmp_path, [{"content": "", "tool_calls": [_call(1, "mcp__x__book", {"id": "1"})]},
                                {"content": "gave up"}], mcp)
    a.send("book it")
    tool = [m for m in a.messages if m.get("role") == "tool"][0]["content"]
    assert tool.startswith("ERROR (tool call failed)") and "timezone" in tool and "call again" in tool
    assert a.trace[0]["ok"] is False


def test_agent_notes_a_second_page(tmp_path):
    out = json.dumps({"tickets": [{"id": 1}], "next_cursor": "5"})
    mcp = FakeMcp({"mcp__x__list": "list"}, {"mcp__x__list": (out, False)})
    a, _ = _agent(tmp_path, [{"content": "", "tool_calls": [_call(1, "mcp__x__list", {})]}, {"content": "ok"}], mcp)
    a.send("list them")
    tool = [m for m in a.messages if m.get("role") == "tool"][0]["content"]
    assert tool.startswith("{") and "not the last page" in tool and "next_cursor" in tool


def test_the_page_after_the_last_cursor_says_it_was_the_last(tmp_path):
    p1 = json.dumps({"tickets": [{"id": 1}], "next_cursor": "5"})
    p2 = json.dumps({"tickets": [{"id": 6}]})
    mcp = FakeMcp({"mcp__x__list": "list"})
    outs = [(p1, False), (p2, False)]
    mcp.call = lambda name, args: outs.pop(0)
    a, _ = _agent(tmp_path, [{"content": "", "tool_calls": [_call(1, "mcp__x__list", {})]},
                             {"content": "", "tool_calls": [_call(2, "mcp__x__list", {"cursor": "5"})]},
                             {"content": "ok"}], mcp)
    a.send("list them")
    tools = [m["content"] for m in a.messages if m.get("role") == "tool"]
    assert "not the last page" in tools[0] and "That was the last page" in tools[1]


# ---------- many tools: an index and on-demand schemas -------------------------------------------------------------------
def _many(n):
    t = {f"mcp__crm__op_{i}": f"operation number {i} on widgets" for i in range(n)}
    t["mcp__crm__close_ticket"] = "Close a support ticket with a resolution note"
    t["mcp__crm__find_invoice"] = "Find an invoice by customer"
    t["mcp__meshpool_tools__calculate"] = "Calculate"
    return t


def _names(body):
    return [t["function"]["name"] for t in body["tools"]]


def test_few_tools_are_all_sent(tmp_path):
    a, seen = _agent(tmp_path, [{"content": "hi"}], FakeMcp(_many(5)))
    a.send("hello")
    assert "mcp__crm__close_ticket" in _names(seen[0]) and MA.FIND not in _names(seen[0])


def test_many_tools_show_an_index_and_load_on_demand(tmp_path):
    mcp = FakeMcp(_many(80), {"mcp__crm__close_ticket": ("closed", False)})
    a, seen = _agent(tmp_path, [
        {"content": "", "tool_calls": [_call(1, MA.FIND, {"query": "close ticket"})]},
        {"content": "", "tool_calls": [_call(2, "mcp__crm__close_ticket", {"id": "T-9"})]},
        {"content": "closed T-9"}], mcp)
    a.send("close ticket T-9")
    first = _names(seen[0])
    assert MA.FIND in first and "mcp__crm__close_ticket" not in first and "mcp__meshpool_tools__calculate" in first
    find = [t for t in seen[0]["tools"] if t["function"]["name"] == MA.FIND][0]["function"]["description"]
    assert "mcp__crm__close_ticket: Close a support ticket" in find
    lookup = [m for m in a.messages if m.get("role") == "tool"][0]["content"]
    assert "mcp__crm__close_ticket" in lookup and "parameters" in lookup
    assert "mcp__crm__close_ticket" in _names(seen[1])            # loaded for the next request
    assert mcp.calls == [("mcp__crm__close_ticket", {"id": "T-9"})]
    assert a.trace[0]["ok"] is True


def test_calling_a_tool_that_is_not_loaded_loads_it_and_asks_again(tmp_path):
    mcp = FakeMcp(_many(80))
    a, seen = _agent(tmp_path, [
        {"content": "", "tool_calls": [_call(1, "mcp__crm__find_invoice", {"id": "x"})]},
        {"content": "", "tool_calls": [_call(2, "mcp__crm__find_invoice", {"id": "x"})]},
        {"content": "found"}], mcp)
    a.send("find the invoice")
    first = [m for m in a.messages if m.get("role") == "tool"][0]["content"]
    assert "not loaded" in first and "Call it again" in first
    assert mcp.calls == [("mcp__crm__find_invoice", {"id": "x"})]       # only the second call ran


def test_lazy_search_ranks_by_name_and_description():
    lz = MA.LazyTools()
    sch = [{"type": "function", "function": {"name": n, "description": d, "parameters": {}}} for n, d in _many(70).items()]
    out = lz.search(sch, "invoice")
    assert "mcp__crm__find_invoice" in out and "mcp__crm__find_invoice" in lz.active
    assert "No connected tool matches" in lz.search(sch, "zebra giraffe")
    assert "mcp__crm__close_ticket" in lz.search(sch, names=["close_ticket"])


def test_lazy_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_MCP_LAZY", "0")
    a, seen = _agent(tmp_path, [{"content": "hi"}], FakeMcp(_many(80)))
    a.send("hello")
    assert "mcp__crm__close_ticket" in _names(seen[0]) and MA.FIND not in _names(seen[0])


# ---------- a reply that stops mid-job -----------------------------------------------------------------------------------
@pytest.mark.parametrize("text,early", [
    ("Tests pass. Now I'll update the README.", True),
    ("Fixed the bug.\n\nNext I will check the docs.", True),
    ("I've changed it. Shall I proceed with the refactor?", True),
    ("Let me look at the other files.", True),
    ("All done. Let me know if you want changes.", False),
    ("Done. The report is in out/report.md.", False),
    ("I will need your API key to continue.", False),
    ("", False),
])
def test_stopped_early(text, early):
    assert _stopped_early(text) is early


def test_a_promise_without_a_tool_call_is_pushed_on(tmp_path):
    a, seen = _agent(tmp_path, [
        {"content": "", "tool_calls": [_call(1, "glob", {"pattern": "*.nothing"})]},
        {"content": "Nothing yet. Now I'll create the file."},
        {"content": "", "tool_calls": [_call(2, "write_file", {"path": "a.txt", "content": "x"})]},
        {"content": "Created a.txt."}])
    out = a.send("make a.txt")
    assert out == "Created a.txt." and (tmp_path / "a.txt").read_text() == "x"
    pushes = [m for m in a.messages if m.get("role") == "user" and "no tool call came with it" in str(m.get("content"))]
    assert len(pushes) == 1


def test_a_pure_answer_is_not_pushed(tmp_path):
    a, seen = _agent(tmp_path, [{"content": "Sure. I'll explain: it is a loop."}])
    assert a.send("what is a loop?").startswith("Sure")
    assert len(seen) == 1                                  # no tool ran this turn: no nudge


def test_the_push_happens_at_most_twice(tmp_path):
    a, seen = _agent(tmp_path, [
        {"content": "", "tool_calls": [_call(1, "glob", {"pattern": "*"})]},
        {"content": "Now I'll do it."}, {"content": "Now I'll do it."}, {"content": "Now I'll do it."}])
    a.send("do something")
    assert len(seen) == 4 and a.messages[-1]["content"] == "Now I'll do it."


# ---------- the prompt --------------------------------------------------------------------------------------------------
def test_prompts_carry_the_finish_the_job_rules(tmp_path):
    for profile in ("code", "work"):
        text = Agent(str(tmp_path), profile=profile).system_prompt()
        assert "Finishing the job" in text and "next page" in text and "Never test by doing extra" in text


def test_prompt_rules_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "E2E", False)
    assert "Finishing the job" not in Agent(str(tmp_path)).system_prompt()


def test_cowork_prompt_no_longer_asks_to_confirm_assumptions_at_the_end(tmp_path):
    text = core.WORK_SYSTEM
    assert "Before you finish, confirm anything important you assumed" not in text
    assert "A vague request" in text


# ---------- the headless CLI can run Cowork --------------------------------------------------------------------------------
def test_cli_profile_flag_reaches_the_agent(monkeypatch):
    import os
    from meshpool_harness import cli
    import meshpool_harness.agent.tui as tui
    seen = {}
    monkeypatch.delenv("MH_AGENT_PROFILE", raising=False)
    monkeypatch.setattr(tui, "main", lambda *a, **k: seen.update(profile=os.environ.get("MH_AGENT_PROFILE")) or 0)
    assert cli.main(["-p", "hello", "--profile", "work", "--auto"]) == 0
    assert seen["profile"] == "work"
    monkeypatch.delenv("MH_AGENT_PROFILE", raising=False)


def test_headless_run_has_no_ask_user_tool(tmp_path):
    a, seen = _agent(tmp_path, [{"content": "hi"}])
    a.headless = True
    a.send("hello")
    assert "ask_user" not in _names(seen[0])
    b, seen = _agent(tmp_path, [{"content": "hi"}])
    b.send("hello")
    assert "ask_user" in _names(seen[0])                    # an interactive session keeps it


def test_the_same_lookup_three_times_gets_a_note(tmp_path):
    same = {"content": "", "tool_calls": [_call(1, "glob", {"pattern": "*.nothing"})]}
    other = {"content": "", "tool_calls": [_call(2, "glob", {"pattern": "*.other"})]}
    a, seen = _agent(tmp_path, [same, other, same, other, same, {"content": "stopped"}])
    a.send("look around")
    notes = [m for m in a.messages if m.get("role") == "user" and "same glob call three times" in str(m.get("content"))]
    assert len(notes) == 1


def test_repeating_a_successful_write_is_flagged_but_reads_are_not(tmp_path):
    mcp = FakeMcp({"mcp__x__inv2_create": "create", "mcp__x__tix_query": "q"},
                  {"mcp__x__inv2_create": ('{"ok": true, "invoice_id": "I-1"}', False),
                   "mcp__x__tix_query": ('{"tickets": []}', False)})
    a, _ = _agent(tmp_path, [{"content": "", "tool_calls": [_call(1, "mcp__x__inv2_create", {"id": "a"}), _call(2, "mcp__x__tix_query", {})]},
                             {"content": "", "tool_calls": [_call(3, "mcp__x__inv2_create", {"id": "a"}), _call(4, "mcp__x__tix_query", {})]},
                             {"content": "done"}], mcp)
    a.send("invoice it")
    tools = [m["content"] for m in a.messages if m.get("role") == "tool"]
    assert "done twice" not in tools[0] and "done twice" in tools[2]
    assert "done twice" not in tools[1] and "done twice" not in tools[3]


def test_read_like_names():
    for n in ("mcp__crm__crm_acct_find", "mcp__x__tix_query", "mcp__x__deals_page", "mcp__x__cal_free", "mcp__x__get_user"):
        assert MA.read_like(n), n
    for n in ("mcp__x__inv2_create", "mcp__x__cal_book", "mcp__x__tix_set_status", "mcp__x__send_message"):
        assert not MA.read_like(n), n


def test_an_empty_page_says_stop_even_without_a_next_cursor_before_it():
    _, post, ok = MA.classify('{"tickets": []}', False)
    assert ok and "last page" in post
    assert MA.classify('{"tickets": [{"id": 1}]}', False)[1] == ""


def test_the_same_result_over_and_over_is_cut_off():
    h = {}
    notes = [MA.same_result(h, "t", '{"tickets": []}') for _ in range(8)]
    assert notes[2] == ("", False) and notes[3][0] and not notes[3][1] and notes[7][1]
    assert MA.same_result(h, "t", "something new") == ("", False)


def test_an_agent_that_probes_an_empty_listing_is_stopped(tmp_path):
    mcp = FakeMcp({"mcp__x__list": "list"}, {"mcp__x__list": ('{"tickets": []}', False)})
    calls = [{"content": "", "tool_calls": [_call(i, "mcp__x__list", {"page": str(i)})]} for i in range(1, 10)]
    a, _ = _agent(tmp_path, calls + [{"content": "done"}], mcp)
    a.send("list")
    last = [m["content"] for m in a.messages if m.get("role") == "tool"][-1]
    assert "same result" in last and "tickets" not in last
