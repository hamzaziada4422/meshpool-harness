"""The independent grader (agent/grader.py): trigger and skip rules, JSON reading, the rework loop, failure handling,
limits, read-only guarantees, the review event and the headless JSON. No network: the model is a script."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import core, grader as GR  # noqa: E402
# Agent is looked up on the module at call time: test_agent_v3 reloads core, which leaves an imported class stale.


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_GRADER", "1")
    monkeypatch.delenv("MH_MODEL_FAST", raising=False)


def call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


def todos(*items):
    return call("todo_write", {"items": [{"content": c, "status": s} for c, s in items]}, "t1")


def item(req, status="met", ev="looked at it"):
    return {"requirement": req, "status": status, "evidence": ev}


def verdict(items, v=None, acc=None, fix=None, blockers=None, reasons=None):
    bad = any(i["status"] == "not_met" for i in items)
    return json.dumps({"expectations": [i["requirement"] for i in items],
                       "verdict": v or ("partial" if bad else "pass"),
                       "acceptance": acc or ("would_send_back" if bad else "accept"),
                       "acceptance_reasons": reasons or [], "items": items, "blockers": blockers or [],
                       "fix": fix or []})


class Model:
    """Scripted model: one queue for the main agent, one for the grader (told apart by its system prompt)."""
    def __init__(self, monkeypatch, main, grader):
        self.main, self.grader, self.grader_calls, self.grader_bodies = list(main), list(grader), 0, []
        model = self

        def fake(self_, base, key, body, stream):
            if body["messages"][0]["content"].startswith("You are the INDEPENDENT REVIEWER"):
                model.grader_calls += 1
                model.grader_bodies.append(json.loads(json.dumps(body)))
                q = model.grader
            else:
                q = model.main
            nxt = q.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            msg = say(nxt) if isinstance(nxt, str) else nxt
            return {"choices": [{"message": msg, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, False
        monkeypatch.setattr(core.Agent, "_post", fake)


def agent(tmp_path, **kw):
    ev = []
    return core.Agent(str(tmp_path), emit=ev.append, mode="auto", **kw), ev


def reviews(ev):
    return [e for e in ev if e["type"] == "review"]


PLAN3 = todos(("write a.txt", "completed"), ("write b.txt", "completed"), ("check", "completed"))


# ---------- when it runs ---------------------------------------------------------------------------------------------
def test_large_rules():
    assert "plan" in GR.large({"todos": [{}, {}, {}], "calls": 2, "final": "", "files": [], "request": "hi"})
    assert "approved" in GR.large({"after_plan": True})
    assert "12 tool steps" in GR.large({"calls": 12})
    assert GR.large({"calls": 5, "files": ["a.py"], "request": "please build a small tool"})
    assert not GR.large({"calls": 5, "files": ["a.py"], "request": "why does this fail?"})
    assert not GR.large({"calls": 2, "files": ["a.py"], "request": "make it blue"})      # too short a task
    assert GR.large({"calls": 5, "final": "x" * 2000, "request": "research X"})           # long answer after work
    assert GR.large({"calls": 4, "research": 4, "final": "y" * 900, "request": "what is the state of X?"})
    assert not GR.large({"todos": [{}, {}], "calls": 3, "final": "ok", "request": "what is 2+2"})


def test_short_task_is_not_graded(tmp_path, monkeypatch):
    m = Model(monkeypatch, [call("write_file", {"path": "a.txt", "content": "1"}), say("Done.")], [])
    a, ev = agent(tmp_path)
    assert a.send("explain the folder") == "Done."
    assert m.grader_calls == 0 and not reviews(ev)


def test_simple_question_is_not_graded(tmp_path, monkeypatch):
    m = Model(monkeypatch, [say("Four.")], [])
    a, ev = agent(tmp_path)
    assert a.send("what is 2+2?") == "Four."
    assert m.grader_calls == 0 and not reviews(ev)


def test_switch_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_GRADER", "0")
    m = Model(monkeypatch, [PLAN3, say("Done.")], [])
    a, ev = agent(tmp_path)
    assert a.send("do three things") == "Done."
    assert m.grader_calls == 0 and not reviews(ev)


def test_plan_mode_skips(tmp_path, monkeypatch):
    m = Model(monkeypatch, [say("1. a 2. b")], [])
    a, ev = agent(tmp_path)
    a.plan = True
    a.send("plan it")
    assert m.grader_calls == 0


# ---------- pass, rework, failure -------------------------------------------------------------------------------------
def test_pass_adds_section_and_event(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("A")
    items = [item("a.txt exists", ev="read_file a.txt: A"), item("plan items done")]
    m = Model(monkeypatch, [PLAN3, say("All done.")], [call("read_file", {"path": "a.txt"}), verdict(items)])
    a, ev = agent(tmp_path)
    out = a.send("make a.txt")
    assert out.startswith("All done.") and "Independent check: all 2 requirements met" in out
    assert "Independent check" in a.messages[-1]["content"]
    r = reviews(ev)
    assert len(r) == 1
    e = r[0]
    assert {"verdict", "acceptance", "items", "met", "total", "text", "round", "secs", "tokens", "reason",
            "expectations", "blockers", "fix", "error", "rework"} <= set(e)
    assert e["verdict"] == "pass" and e["met"] == 2 and e["tokens"]["calls"] == 2 and e["reason"].startswith("a plan")
    assert a.usage["calls"] == 2 + 2           # the grader's calls count toward the chat's usage


def test_agent_claims_done_file_missing_then_fixed(tmp_path, monkeypatch):
    bad = [item("report.md exists", "not_met", "glob report.md: no match"), item("plan items done")]
    good = [item("report.md exists", ev="read_file: 3 lines"), item("plan items done")]
    m = Model(monkeypatch,
              [PLAN3, say("Everything is done, report.md is written."),
               call("write_file", {"path": "report.md", "content": "a\nb\nc\n"}, "w1"), say("Now report.md exists.")],
              [call("glob", {"pattern": "report.md"}), verdict(bad, fix=["Create report.md"]),
               call("read_file", {"path": "report.md"}), verdict(good)])
    a, ev = agent(tmp_path)
    out = a.send("write report.md and finish")
    assert (tmp_path / "report.md").exists()
    r = reviews(ev)
    assert [x["verdict"] for x in r] == ["partial", "pass"] and r[0]["rework"] is True and r[1]["round"] == 1
    inj = [x["content"] for x in a.messages if x["role"] == "user" and x["content"].startswith("[Independent review]")]
    assert len(inj) == 1 and "report.md exists" in inj[0] and "Create report.md" in inj[0]
    assert "Independent check: all 2 requirements met after 1 fix round" in out
    assert out.startswith("Everything is done") is False         # the reply returned is the fixed one


def test_rework_is_capped_at_two_rounds(tmp_path, monkeypatch):
    bad = verdict([item("x", "not_met", "missing")], fix=["do x"])
    m = Model(monkeypatch, [PLAN3, say("done 1"), say("done 2"), say("done 3")], [bad, bad, bad])
    a, ev = agent(tmp_path)
    out = a.send("do x")
    assert m.grader_calls == 3 and [e["rework"] for e in reviews(ev)] == [True, True, False]
    assert "Not done: x" in out and "all 1" not in out


def test_grader_error_never_blocks(tmp_path, monkeypatch):
    Model(monkeypatch, [PLAN3, say("Finished.")], [RuntimeError("HTTP 500 upstream")] * 4)
    a, ev = agent(tmp_path)
    out = a.send("do it")
    assert out.startswith("Finished.") and "could not run" in out and "not independently checked" in out
    e = reviews(ev)[0]
    assert e["verdict"] == "error" and e["error"] and not e["items"]


def test_bad_json_is_repaired_by_a_second_ask(tmp_path, monkeypatch):
    m = Model(monkeypatch, [PLAN3, say("Done.")], ["I think it is fine, mostly.", verdict([item("a")])])
    a, ev = agent(tmp_path)
    out = a.send("do it")
    assert "all 1 requirement met" in out and m.grader_calls == 2


def test_unreadable_twice_says_could_not_run(tmp_path, monkeypatch):
    Model(monkeypatch, [PLAN3, say("Done.")], ["nope", "still nope"])
    a, ev = agent(tmp_path)
    out = a.send("do it")
    assert "could not run" in out and "could not be read" in out


def test_step_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_GRADER_STEPS", "3")
    (tmp_path / "a.txt").write_text("x")
    loop = [call("read_file", {"path": "a.txt", "start": i}, f"r{i}") for i in range(20)]
    m = Model(monkeypatch, [PLAN3, say("Done.")], loop[:3] + [verdict([item("a", "unverified", "ran out")])])
    a, ev = agent(tmp_path)
    a.send("do it")
    assert m.grader_calls <= 3 + 1          # three steps, then one tool-less call for the verdict
    assert "Could not check: a" in reviews(ev)[0]["text"]


def test_token_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_GRADER_TOKENS", "5000")
    n = {"g": 0}

    def fake(self_, base, key, body, stream):
        if not body["messages"][0]["content"].startswith("You are the INDEPENDENT"):
            msg = PLAN3 if len(body["messages"]) < 3 else say("Done.")
            return {"choices": [{"message": msg, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1}}, False
        n["g"] += 1
        msg = call("glob", {"pattern": "*"}, f"g{n['g']}") if n["g"] < 6 else say(verdict([item("a")]))
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4000, "completion_tokens": 10}}, False
    monkeypatch.setattr(core.Agent, "_post", fake)
    a, ev = agent(tmp_path)
    a.send("do it")
    assert n["g"] <= 4                       # stopped after the budget, not after six calls


# ---------- read-only ------------------------------------------------------------------------------------------------------
def test_grader_cannot_write(tmp_path, monkeypatch):
    m = Model(monkeypatch, [PLAN3, say("Done.")],
              [call("write_file", {"path": "evil.txt", "content": "x"}, "a"),
               call("edit", {"path": "x", "old": "a", "new": "b"}, "b"),
               call("bash", {"command": "touch evil2.txt"}, "c"), call("bash", {"command": "rm -rf ."}, "d"),
               call("bash", {"command": "echo hi > evil3.txt"}, "e"), call("delegate_task", {}, "f"),
               verdict([item("a", "unverified", "could not check")])])
    (tmp_path / "keep.txt").write_text("keep")
    a, ev = agent(tmp_path)
    a.send("do it")
    assert not list(tmp_path.glob("evil*")) and (tmp_path / "keep.txt").read_text() == "keep"
    outs = [x["content"] for x in m.grader_bodies[-1]["messages"] if x["role"] == "tool"]
    assert len(outs) == 6 and all(("denied" in o or "unknown tool" in o) for o in outs)


def test_command_rules():
    for ok in ("pytest -q", "python3 -m pytest tests/", "npm test", "go test ./...", "cargo test", "cat a.py | head",
               "python3 -m pytest -q 2>&1 | tail -20", "cd sub && pytest -x", "PYTHONPATH=. pytest test_a.py -v 2>&1 | head -50"):
        assert GR.command_allowed(ok), ok
    for bad in ("pytest && rm -rf .", "npm test; curl x", "touch a", "pip install x", "", "pytest | tee out.txt", "pytest > out.txt", "pytest 2>&1 > f", "pytest | sh"):
        assert not GR.command_allowed(bad), bad


def test_changes_during_the_check_are_reported_not_reverted(tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    snap = GR._snapshot(tmp_path)
    (tmp_path / "keep.txt").write_text("changed by someone else")
    (tmp_path / "new.txt").write_text("n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_text("c")          # caches are not counted
    assert GR._changed(tmp_path, snap) == ["keep.txt", "new.txt"]
    assert (tmp_path / "keep.txt").read_text() == "changed by someone else" and (tmp_path / "new.txt").exists()


def test_only_unverified_or_suggestions_do_not_send_the_agent_round_again():
    r = GR.normalise({"verdict": "partial", "acceptance": "accept_with_notes", "fix": ["replace the placeholder URL"],
                      "items": [{"requirement": f"r{i}", "status": "met", "evidence": "x"} for i in range(7)]
                      + [{"requirement": "tests pass", "status": "unverified", "evidence": "could not run"}]})
    assert r["verdict"] == "partial" and not GR.needs_rework(r)
    assert GR.needs_rework(GR.normalise({"verdict": "partial", "items": [{"requirement": "a", "status": "not_met", "evidence": "gone"}]}))
    assert GR.needs_rework(GR.normalise({"verdict": "partial", "blockers": ["b"], "items": [{"requirement": "a", "status": "met", "evidence": "x"}]}))


def test_run_with_only_a_suggestion_does_not_rework(tmp_path, monkeypatch):
    items = [item("a"), item("b", "unverified", "no tool")]
    m = Model(monkeypatch, [PLAN3, say("Done.")], [verdict(items, v="partial", fix=["tidy the URL"])])
    a, ev = agent(tmp_path)
    out = a.send("do it")
    assert m.grader_calls == 1 and not reviews(ev)[0]["rework"] and "Could not check: b" in out


# ---------- reading the JSON --------------------------------------------------------------------------------------------------
def test_parse_tolerates_fences_trailing_commas_and_cut_off():
    raw = '```json\n{"verdict": "pass", "items": [{"requirement": "a", "status": "met", "evidence": "x",},],}\n```'
    assert GR.normalise(GR.parse(raw))["verdict"] == "pass"
    cut = '{"verdict": "partial", "items": [{"requirement": "a", "status": "not_met", "evidence": "gone"}], "fix": ["do it'
    r = GR.normalise(GR.parse(cut))
    assert r and r["not_met"] == 1 and r["fix"] == ["do it"]
    assert GR.parse("no json here") is None and GR.normalise({"verdict": "pass", "items": []}) is None
    assert GR.normalise(GR.parse("Sure: {'verdict': 'pass', 'items': [{'requirement': 'a', 'status': 'met', 'evidence': 'e'}]}"))


def test_normalise_is_strict():
    r = GR.normalise({"verdict": "pass", "acceptance": "accept", "items": [
        {"requirement": "a", "status": "met", "evidence": "x"}, {"requirement": "b", "status": "not_met", "evidence": "y"}]})
    assert r["verdict"] == "partial" and r["acceptance"] != "accept"                  # never a pass with an open item
    r = GR.normalise({"verdict": "pass", "items": [{"requirement": "a", "status": "met", "evidence": ""}]})
    assert r["verdict"] == "partial" and r["items"][0]["status"] == "unverified"       # met without evidence is not met
    assert not GR.needs_rework(r)                                                        # nothing the agent can fix


# ---------- Cowork: deliverables, acceptance ---------------------------------------------------------------------------------
class FakeOffice:
    """The office MCP server (read side), plus a tool the grader must never get."""
    def __init__(self, docs):
        self.docs, self.calls, self.last_images = docs, [], []

    def has(self, n):
        return n.startswith("mcp__meshpool_office__")

    def schemas(self):
        return [{"type": "function", "function": {"name": "mcp__meshpool_office__" + t, "description": t,
                                                  "parameters": {"type": "object", "properties": {
                                                      "path": {"type": "string"}}}}} for t in ("read_document", "make_deck")]

    def call(self, name, args):
        self.calls.append(name)
        if name.endswith("read_document"):
            return self.docs.get(args.get("path"), "(not found)"), False
        return "made", False


def cowork_case(tmp_path, monkeypatch, doc, main_final, items, **vk):
    open_items = any(i["status"] == "not_met" for i in items)
    m = Model(monkeypatch, [PLAN3, say(main_final)] + ([say("Fixed it.")] if open_items else []),
              [call("mcp__meshpool_office__read_document", {"path": "deck.pptx"}),
               call("mcp__meshpool_office__make_deck", {"path": "x"}, "z"), verdict(items, **vk)]
              + ([verdict([item(i["requirement"]) for i in items])] if open_items else []))
    ev = []
    a = core.Agent(str(tmp_path), emit=ev.append, mode="auto", profile="work", mcp=FakeOffice({"deck.pptx": doc}))
    return a, ev, m, a.send("Make a 3 slide deck: problem, solution, pricing.")


def injected(a):
    return [x["content"] for x in a.messages if x["role"] == "user" and x["content"].startswith("[Independent review]")]


def test_cowork_deck_missing_requested_section(tmp_path, monkeypatch):
    a, ev, m, out = cowork_case(tmp_path, monkeypatch, "Slide 1 Problem. Slide 2 Solution.", "The deck is ready.",
                                [item("A pricing slide", "not_met", "read_document: no slide mentions pricing")],
                                acc="would_send_back", reasons=["There is no pricing slide, which you asked for."],
                                fix=["Add a pricing slide"])
    r = reviews(ev)[0]
    assert r["verdict"] == "partial" and r["acceptance"] == "would_send_back" and "pricing" in r["acceptance_reasons"][0]
    assert "mcp__meshpool_office__read_document" in a.mcp.calls
    assert "mcp__meshpool_office__make_deck" not in a.mcp.calls          # the grader cannot make files
    assert injected(a) and "pricing slide" in injected(a)[0]
    assert "Independent check: all 1 requirement met after 1 fix round" in out


def test_cowork_workbook_wrong_total(tmp_path, monkeypatch):
    a, ev, m, out = cowork_case(tmp_path, monkeypatch, "Total: 410 (rows sum to 400)", "Workbook done, total 410.",
                                [item("Total equals the sum of the rows", "not_met", "rows sum to 400, cell says 410")],
                                fix=["Fix the total to 400"])
    assert injected(a) and "rows sum to 400" in injected(a)[0]


def test_cowork_report_with_unsourced_claim(tmp_path, monkeypatch):
    a, ev, m, out = cowork_case(tmp_path, monkeypatch, "Market grew 40% (no source)", "Report ready.",
                                [item("Every claim has a source link", "not_met", "40% growth has no source")],
                                fix=["Cite a source for the 40% claim or remove it"])
    assert injected(a) and "40% growth has no source" in injected(a)[0]


def test_cowork_answer_in_wrong_language(tmp_path, monkeypatch):
    a, ev, m, out = cowork_case(tmp_path, monkeypatch, "x", "Here is your summary.",
                                [item("Answer is in German, as asked", "not_met", "the answer is in English")],
                                fix=["Rewrite in German"])
    assert injected(a) and "German" in injected(a)[0]


def test_long_research_answer_without_plan_or_files_is_graded(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    calls = [call("read_file", {"path": "a.txt"}, f"r{i}") for i in range(4)]
    m = Model(monkeypatch, calls + [say("word " * 500)], [verdict([item("covers the question")])])
    a, ev = agent(tmp_path)
    out = a.send("research the topic thoroughly")
    assert reviews(ev) and "Independent check: all 1 requirement met" in out and reviews(ev)[0]["reason"].startswith("a long")


def test_grader_sees_only_the_evidence(tmp_path, monkeypatch):
    m = Model(monkeypatch, [PLAN3, say("The secret plan was X.")], [verdict([item("a")])])
    a, ev = agent(tmp_path)
    a.messages.append({"role": "user", "content": "earlier thing"})
    a.messages.append(say("earlier answer SECRETREPLY"))
    a.send("do the thing")
    first = m.grader_bodies[0]["messages"]
    assert [x["role"] for x in first] == ["system", "user"]
    u = first[1]["content"]
    assert "do the thing" in u and "The secret plan was X." in u and "SECRETREPLY" not in u and "write a.txt" in u
    assert first[0]["content"].startswith("You are the INDEPENDENT REVIEWER")
    assert {t["function"]["name"] for t in m.grader_bodies[0]["tools"]} == {"read_file", "grep", "glob", "bash"}


def test_fast_model_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_MODEL_FAST", "fast-model")
    m = Model(monkeypatch, [PLAN3, say("Done.")], [verdict([item("a")])])
    a, ev = agent(tmp_path)
    a.send("do it")
    assert m.grader_bodies[0]["model"].endswith("fast-model") and reviews(ev)[0]["model"] == "fast-model"


# ---------- on demand and headless ---------------------------------------------------------------------------------------------
def test_grade_now_forces_a_check_without_rework(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_GRADER", "0")           # even with the switch off, the user's own request runs
    Model(monkeypatch, [say("Hello.")], [verdict([item("said hello", "met", "reply says Hello")])])
    a, ev = agent(tmp_path)
    a.send("say hello")
    assert not reviews(ev)
    r = a.grade_now()
    assert r["verdict"] == "pass" and r["forced"] and reviews(ev)[0]["forced"]


def test_headless_json_has_review(tmp_path, monkeypatch):
    import io
    from meshpool_harness.agent import tui
    out = io.StringIO()                            # the headless result goes to sys.__stdout__: not the real one, whose
    monkeypatch.setattr(sys, "__stdout__", out)    # state earlier tests of a full run can change
    Model(monkeypatch, [PLAN3, say("Done.")], [verdict([item("a")])])
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    tui.main(str(tmp_path), prompt="make a thing", auto=True, no_mcp=True, json_out=True)
    res = json.loads(out.getvalue().strip().splitlines()[-1])
    assert res["type"] == "result" and res["review"]["verdict"] == "pass" and res["review"]["met"] == 1
    assert "Independent check" in res["result"]
