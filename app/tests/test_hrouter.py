"""Harness router v2: routes, language packs (diagnostics), on-demand skills, and the agent wiring."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.hrouter import route  # noqa: E402
from meshpool_harness.hrouter import packs, signals  # noqa: E402
from meshpool_harness.hrouter.route import addon  # noqa: E402

CASES = [  # (kind, text, spec, lane)
    ("cowork", 'Traceback (most recent call last):\n  File "app.py", line 3\nSyntaxError: invalid syntax', "code.debug", None),
    ("cowork", "what's the capital of Australia?", "chat", "chat"),
    ("cowork", "rewrite this email to sound friendlier: hey, send the files asap", "chat", "chat"),
    ("cowork", "Make a 10 slide deck on Nvidia's Q3 results for the board", "docs", "task"),
    ("cowork", "Analyze sales.csv and tell me which region declined", "data", None),
    ("cowork", "Research the competitive landscape for AI coding agents with sources", "research", "task"),
    ("cowork", "what is the latest news on the fed?", "chat", "chat"),
    ("cowork", "Give a regex that matches a 5-digit US ZIP code, only the regex.", "chat", "chat"),
    ("cowork", "Write a SQL query that counts orders per customer_id in table orders. Only the query.", "chat", "chat"),
    ("cowork", "every monday morning send me a summary of my unread emails", "automation", None),
    ("cowork", "log in to my bank website and download last month's statement", "browser", None),
    ("code", "add a --verbose flag to the CLI", "code.build", None),
    ("code", "the login test is failing since yesterday, fix it", "code.debug", None),
    ("code", "review my changes before I open the PR", "code.review", None),
    ("code", "refactor utils.py into smaller modules", "code.refactor", None),
    ("code", "write unit tests for the parser", "code.test", None),
    ("code", "install docker and set up the dev environment", "code.terminal", None),
    ("code", "how does the router in this repo work?", "code.explain", None),
]


@pytest.mark.parametrize("kind,text,spec,lane", CASES)
def test_routes(kind, text, spec, lane):
    r = route(text, kind=kind)
    assert r.spec == spec, (text, r)
    if lane:
        assert r.lane == lane, (text, r)
    assert r.ms < 50                                # signals only: no model call, no delay


def test_pasted_errors_name_the_language():
    assert route("error TS2322: Type 'string' is not assignable to type 'number'.", kind="code").language == "typescript"
    assert route("error[E0425]: cannot find value `x` in this scope\n --> src/main.rs:4:5").language == "rust"
    assert route("panic: runtime error: index out of range\n\ngoroutine 1 [running]:").language == "go"
    assert route('Exception in thread "main" java.lang.NullPointerException\n\tat Foo.bar(Foo.java:12)').language == "java"


def test_project_language_from_markers(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    assert signals.project_language(str(tmp_path)) == "rust"
    r = route("the build is broken", kind="code", workdir=str(tmp_path))
    assert r.spec == "code.debug" and r.language == "rust"


def test_user_pick_wins():
    r = route("hello there", force="code.review")
    assert r.spec == "code.review" and r.base == "code" and r.reason == "picked by the user"


def test_model_call_only_when_unsure():
    calls = []

    class Client:
        def chat(self, **kw):
            calls.append(kw)
            return {"choices": [{"message": {"content": '{"id": "chat", "confidence": 0.9}'}}]}
    r = route("ok, the thing from before, same as last time", client=Client())
    assert (r.used_model_call, r.spec) == (True, "chat") and len(calls) == 1
    calls.clear()
    route("what's the capital of Australia?", client=Client())
    assert not calls                               # sure from the rules: no call


def test_addon_carries_method_and_language_pack():
    r = route("fix this: SyntaxError: invalid syntax in app.py", kind="code")
    text = addon(r)
    assert "[Harness: Debug · Syntax · Python]" in text and "Reproduce" in text and "pytest" in text


# ---------- diagnostics ----------------------------------------------------------------------------------------------
@pytest.fixture
def broken(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("import os\n\ndef f(x)\n    return x + 1\n")
    (tmp_path / "conf.json").write_text('{"a": 1,}\n')
    (tmp_path / "ok.py").write_text("x = 1\n")
    return tmp_path


def _ws(root):
    from meshpool_harness.core.tools import Workspace
    return Workspace(str(root), str(root / ".run"))


def test_diagnostics_points_at_the_syntax_error(broken):
    out = packs.diagnostics(_ws(broken), ["pkg/a.py", "conf.json"])
    assert "pkg/a.py:3:" in out and "expected ':'" in out
    assert "conf.json:1:" in out and "JSONDecodeError" in out
    assert ">   3  def f(x)" in out                    # the failing line, marked
    assert out.count("pkg/a.py:3:") == 1               # one problem, even if two checkers report it


def test_diagnostics_clean_file(broken):
    assert "No problems found" in packs.diagnostics(_ws(broken), ["ok.py"])


def test_diagnostics_uses_the_newest_python():
    exe = packs.newest_python()
    assert exe and Path(exe).exists()


def test_diagnostics_defaults_to_changed_files(broken):
    subprocess.run(["git", "init", "-q"], cwd=broken, check=True)
    out = packs.diagnostics(_ws(broken))
    assert "pkg/a.py:3:" in out


# ---------- skills on demand -----------------------------------------------------------------------------------------
def test_skills_search_ranks_name_hits_first():
    from meshpool_harness import skills as SK
    rows = [{"name": "deck-review", "description": "Review a slide deck"},
            {"name": "email-writer", "description": "Write emails, including deck follow-ups"},
            {"name": "sql-helper", "description": "Write SQL"}]
    got = [s["name"] for s in SK.search("review my decks", rows=rows)]
    assert got[0] == "deck-review" and "sql-helper" not in got


def test_on_demand_catalog_is_short(monkeypatch):
    from meshpool_harness import skills as SK
    rows = [{"name": f"skill-{i}", "description": "x " * 100 + f"topic{i}"} for i in range(60)]
    monkeypatch.setattr(SK, "enabled", lambda: rows)
    monkeypatch.delenv("MH_SKILLS", raising=False)
    short = SK.catalog_text("help with topic7")
    monkeypatch.setenv("MH_SKILLS", "full")
    full = SK.catalog_text("help with topic7")
    assert len(short) < len(full) / 5 and "find_skill" in short and "skill-7" in short


# ---------- agent wiring ---------------------------------------------------------------------------------------------
def test_agent_runs_as_the_routed_harness_and_keeps_it_on_resume(tmp_path, monkeypatch):
    from meshpool_harness.agent import core as A
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    a = A.Agent(str(tmp_path), profile="code")
    r = route("Traceback (most recent call last):\nValueError: bad", kind="code")
    a.set_route(r, "fix it")
    assert "[Harness: Debug" in a.messages[0]["content"]
    assert "diagnostics" in a._tool_names()
    a.messages.append({"role": "user", "content": "x"})
    a.save()
    b = A.Agent.resume(str(tmp_path), a.id)
    assert b.route.spec == "code.debug" and "[Harness: Debug" in b.messages[0]["content"]


def test_fast_model_only_for_chat_lane(tmp_path, monkeypatch):
    from meshpool_harness.agent import core as A
    monkeypatch.setenv("MH_MODEL", "big")
    monkeypatch.setenv("MH_MODEL_FAST", "small")
    a = A.Agent(str(tmp_path), profile="work")
    assert a.current_model() == "big"
    a.lane = "chat"
    assert a.current_model() == "small"
    a.model = "user-pick"
    assert a.current_model() == "user-pick"            # the user's own choice always wins


def test_auto_uses_the_routed_category():
    from meshpool_harness import harness_router as HRT
    t = HRT.table()
    assert "terminal" in t["categories"]
    assert HRT.classify("make a deck")[0] != "terminal"
    # category overrides the keyword match
    r = HRT.route("make a deck", "code", category="terminal")
    assert r["category"] == "terminal"


def test_outcomes_log_and_refit(tmp_path, monkeypatch):
    from meshpool_harness.hrouter import outcomes as OUT
    monkeypatch.setattr(OUT, "path", lambda: tmp_path / "o.jsonl")
    for i in range(3):
        OUT._append({"ts": i, "chat": "c1", "spec": "chat", "harness": "meshpool-work", "secs": 4 + i,
                     "tokens_out": 100, "error": False, "stopped": False})
    OUT.mark("c1", "undo")
    got = OUT.refit()["chat|meshpool-work"]
    assert got["turns"] == 3 and got["fail_rate"] == round(1 / 3, 3) and got["median_secs"] == 5   # a mark hits one row (M29-04)


# ---------- zero-call checks ---------------------------------------------------------------------------------------
def test_quick_check_flags_a_broken_edit_and_stays_quiet_on_a_clean_one(tmp_path):
    ws = _ws(tmp_path)
    ws.write_file("a.py", "def f(x)\n    return x\n")
    assert "a.py:1:" in packs.quick_check(ws, "a.py") and "expected ':'" in packs.quick_check(ws, "a.py")
    ws.write_file("b.py", "import os\n\nx = 1\n")
    assert packs.quick_check(ws, "b.py") == ""          # an unused import is not worth a mid-edit warning
    ws.write_file("c.json", '{"a": 1,}')
    assert "JSONDecodeError" in packs.quick_check(ws, "c.json")
    assert packs.quick_check(ws, "notes.md") == ""


def test_reproduce_runs_the_named_command_only_when_its_file_exists(tmp_path):
    (tmp_path / "test_x.py").write_text("assert 1 == 2, 'boom'\n")
    ws = _ws(tmp_path)
    assert packs.repro_commands("test_x.py fails, fix it", tmp_path)[0].endswith("test_x.py")
    assert packs.repro_commands("run `python missing.py`", tmp_path) == []
    assert packs.repro_commands("`rm -rf / && python test_x.py`", tmp_path) == [packs.repro_commands("test_x.py", tmp_path)[0]]
    out = packs.reproduce(ws, "test_x.py fails")
    assert "<reproduction>" in out and "boom" in out and "[exit 1]" in out


# ---------- robustness: refused fields and spent quotas ---------------------------------------------------------------
def test_a_refused_field_is_removed_and_remembered():
    from meshpool_harness.agent import core as A
    A._REFUSED.clear()
    body = {"model": "kimi-x", "temperature": 0.2, "max_tokens": 10}
    assert A._adapt_params(body, 'HTTP 400: {"error":"This model does not support the `temperature` field."}')
    assert "temperature" not in body and A._REFUSED["kimi-x"] == {"temperature"}
    body = {"model": "o-x", "max_tokens": 10}
    assert A._adapt_params(body, "HTTP 400: Unsupported parameter: 'max_tokens' is not supported with this model. "
                                 "Use 'max_completion_tokens' instead.")
    assert body == {"model": "o-x", "max_completion_tokens": 10}
    assert not A._adapt_params({"model": "m", "temperature": 1}, "HTTP 400: messages[3] is invalid")
    assert not A._adapt_params({"model": "m", "temperature": 1}, "HTTP 500: temperature unsupported")


def test_spent_quota_is_not_retried():
    from meshpool_harness.core.client import classify_error
    for m in ("HTTP 400: Budget has been exceeded! Current cost: 20.01, Max budget: 20.0",
              "HTTP 429: You exceeded your current quota, please check your plan", "HTTP 402: Insufficient credits"):
        assert classify_error(m) == "quota", m
    assert classify_error("HTTP 429: Rate limit reached for requests") == "rate_limit"


def test_auto_prefers_measured_numbers_for_the_chat_model(monkeypatch):
    from meshpool_harness import harness_router as HRT
    t = HRT.table()
    t["measured_min_n"] = 10
    t["measured"] = {"m-1": {"code": {"pi": {"n": 40, "score": 90, "cost_usd": 0.0005, "seconds": 9},
                                      "meshpool-code": {"n": 40, "score": 91, "cost_usd": 0.002, "seconds": 12}},
                             "general": {"pi": {"n": 3, "score": 99}}}}
    monkeypatch.setattr(HRT, "table", lambda: t)
    monkeypatch.setattr(HRT, "_ready", lambda kind: {h: {"id": h, "name": h} for h in ("pi", "meshpool-code", "codex",
                                                                                         "meshpool-work", "hermes")})
    r = HRT.route("fix the bug", "code", category="code", model="m-1")
    assert r["harness"] == "pi" and "measured on this model" in r["reason"]   # same quality band, cheaper
    assert HRT.measured(t, "general", "m-1") == {}                            # 3 runs is not enough to trust
    assert HRT.route("fix the bug", "code", category="code", model="other")["harness"] != "" 


def test_lean_code_harness_drops_rarely_used_tools_and_keeps_the_prompt_consistent(tmp_path, monkeypatch):
    from meshpool_harness.agent import core as A
    monkeypatch.delenv("MH_LEAN_TOOLS", raising=False)
    a = A.Agent(str(tmp_path), profile="code")
    a.set_route(route("test_x.py fails, fix it", kind="code"), "test_x.py fails")
    names = [s["function"]["name"] for s in a.tool_schemas()]
    assert "diagnostics" in names and "edit" in names and "fetch_url" in names
    assert not {"delegate_task", "ask_user", "web_search", "web_research", "find_skill"} & set(names)
    assert "find_skill" not in a.system_prompt()
    monkeypatch.setenv("MH_LEAN_TOOLS", "0")
    assert "web_search" in [s["function"]["name"] for s in a.tool_schemas()]
    b = A.Agent(str(tmp_path), profile="code")
    b.set_route(route("add a --verbose flag to the CLI", kind="code"), "x")        # build keeps every tool
    monkeypatch.delenv("MH_LEAN_TOOLS")
    assert "web_search" in [s["function"]["name"] for s in b.tool_schemas()]


def test_a_masked_key_gets_one_clear_message():
    from meshpool_harness import providers as PV
    assert "masked" in PV.key_problem("sk-43Ux7\u2022\u2022\u2022\u2022")
    assert "masked" in PV.key_problem("sk-abc\u2026")
    assert PV.key_problem("sk-proj-AbC_123-xyz") == ""
    assert "never has" in PV.key_problem("sk-a\u00e9b")


def test_the_agent_says_so_instead_of_a_codec_error(tmp_path, monkeypatch):
    from meshpool_harness import run_task
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-43Ux7\u2022\u2022\u2022\u2022")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    r = run_task("hello", workdir=str(tmp_path), engine="agent-work")
    assert "masked" in (r.get("error") or ""), r


# ---------- memory v2 ----------------------------------------------------------------------------------------------
def test_recall_keeps_core_facts_and_picks_the_relevant_ones(tmp_path, monkeypatch):
    from meshpool_harness import assistant as AS
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    facts = ["My name is Hamza and I work at Meshpool.", "I prefer short answers."] + [
        f"Client {i} uses Salesforce for pipeline {i}." for i in range(80)] + ["Our pricing page lists three tiers."]
    AS.set_memory("\n".join(facts))
    got = AS.recall("update the pricing tiers copy", limit=400)
    assert "My name is Hamza" in got and "short answers" in got and "pricing page" in got
    assert "Client 5 " not in got and len(got) <= 400


def test_project_memory_lives_outside_the_project(tmp_path, monkeypatch):
    from meshpool_harness import assistant as AS
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    proj = tmp_path / "proj"
    proj.mkdir()
    assert "Saved" in AS.remember_project(str(proj), "Run the tests with make test.")
    assert "Already" in AS.remember_project(str(proj), "run the tests with make test.")
    assert AS.project_memory(str(proj)) == "Run the tests with make test."
    assert not any(proj.iterdir())
    block = AS.context_block("fix the failing test", str(proj))
    assert "<memory-context>" in block and "make test" in block


def test_scrub_removes_memory_blocks_even_when_split_across_deltas():
    from meshpool_harness import assistant as AS
    text = "Sure.\n<memory-context>\nsecret-ish fact\n</memory-context>\nThe answer is 4."
    assert AS.scrub(text) == "Sure.\nThe answer is 4." or "fact" not in AS.scrub(text)
    sc, out = AS.StreamScrubber(), ""
    for i in range(0, len(text), 3):
        out += sc.feed(text[i:i + 3])
    out += sc.flush()
    assert "fact" not in out and "memory-context" not in out and "The answer is 4." in out and out.startswith("Sure.")


@pytest.mark.parametrize("text,label", [
    ("parse_date worked last week and now returns the wrong month", "Debug · Regression"),
    ("find_duplicates is far too slow on our real data", "Debug · Performance"),
    ("python calc.py crashes with a SyntaxError", "Debug · Syntax · Python"),
    ("test_stats.py fails", "Debug · Test · Python"),
    ("Add a --count option to cli.py", "Build · CLI · Python"),
    ("Add cursor pagination to listItems", "Build · API"),
])
def test_sub_subtask_variants(text, label):
    assert route(text, kind="code").label == label


def test_data_driven_overrides_most_specific_wins(tmp_path, monkeypatch):
    import json
    from meshpool_harness.hrouter.route import dropped
    f = tmp_path / "specs.json"
    f.write_text(json.dumps({"specs": {
        "code.debug": {"addon_extra": "EXTRA-GENERAL"},
        "code.debug.perf": {"addon": "PERF-ONLY method", "drop": ["delegate_task"]},
        "code.debug.perf.python": {"addon_extra": "EXTRA-PY"}}}))
    monkeypatch.setenv("MH_SPECS_FILE", str(f))
    r = route("dedupe.py is far too slow", kind="code")
    text = addon(r)
    assert "PERF-ONLY method" in text and "EXTRA-GENERAL" in text and "EXTRA-PY" in text
    assert "Debugging: match the effort" not in text            # the variant's override replaced the base method
    assert dropped(r) == {"delegate_task"}


# ---------- the review receipt ---------------------------------------------------------------------------------------
def _scripted(monkeypatch, msgs):
    from meshpool_harness.agent import core as A
    it = iter(msgs)

    def fake_chat(self, turn):
        return next(it), False
    monkeypatch.setattr(A.Agent, "_chat", fake_chat)


def _call(name, args, cid):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_receipt_lists_changes_and_the_check_that_ran_after_them(tmp_path, monkeypatch):
    from meshpool_harness import run_task
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "init"], cwd=tmp_path)
    _scripted(monkeypatch, [_call("read_file", {"path": "a.py"}, "c1"),
                            _call("edit", {"path": "a.py", "old": "x = 1", "new": "x = 2"}, "c2"),
                            _call("bash", {"command": "true"}, "c3"),
                            {"role": "assistant", "content": "Done."}])
    r = run_task("change x", workdir=str(tmp_path), engine="agent")
    assert "**Changed:** a.py (+1 -1)" in r["receipt"] and "`true` passed" in r["receipt"] and r["answer"] == "Done.", r


def test_receipt_says_so_when_nothing_verified_the_change(tmp_path, monkeypatch):
    from meshpool_harness import run_task
    _scripted(monkeypatch, [_call("write_file", {"path": "new.py", "content": "y = 1\n"}, "c1"),
                            {"role": "assistant", "content": "Wrote it."}])
    r = run_task("make new.py", workdir=str(tmp_path), engine="agent")
    assert "new.py" in r["receipt"] and "not verified" in r["receipt"], r


def test_no_receipt_without_changes(tmp_path, monkeypatch):
    from meshpool_harness import run_task
    _scripted(monkeypatch, [{"role": "assistant", "content": "Canberra."}])
    assert run_task("capital of Australia?", workdir=str(tmp_path), engine="agent-work")["answer"] == "Canberra."


def test_independent_reads_run_together_and_answer_in_order(tmp_path, monkeypatch):
    import time as _t
    from meshpool_harness import run_task
    from meshpool_harness.core.tools import Workspace
    for n in "abc":
        (tmp_path / f"{n}.txt").write_text(n * 3)
    import threading as _th
    real = Workspace.read_file
    met = _th.Barrier(3, timeout=10)                   # all three reads must be in flight at once: no wall clock

    def slow(self, path, start=None, end=None):
        met.wait()                                     # sequential reads would break the barrier, not just run late
        return real(self, path, start, end)
    monkeypatch.setattr(Workspace, "read_file", slow)
    calls = {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{n}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": f"{n}.txt"})}}
        for n in "abc"]}
    seen = {}

    def capture(self, turn):
        if "msgs" not in seen:
            seen["msgs"] = True
            return calls, False
        seen["tools"] = [m for m in self.messages if m.get("role") == "tool"]
        return {"role": "assistant", "content": "ok"}, False
    from meshpool_harness.agent import core as A
    monkeypatch.setattr(A.Agent, "_chat", capture)
    run_task("read them", workdir=str(tmp_path), engine="agent")
    assert not met.broken
    assert [m["tool_call_id"] for m in seen["tools"]] == ["ca", "cb", "cc"]
    assert "aaa" in seen["tools"][0]["content"] and "ccc" in seen["tools"][2]["content"]


def test_edits_and_commands_keep_their_order():
    from meshpool_harness.agent.core import _batches
    c = lambda n, i: {"id": i, "function": {"name": n}}   # noqa: E731
    b = _batches([c("read_file", 1), c("grep", 2), c("edit", 3), c("read_file", 4), c("bash", 5), c("bash", 6)])
    assert [[x["id"] for x in g] for g in b] == [[1, 2], [3], [4], [5], [6]]


def test_repo_map_ranks_the_files_the_request_names(tmp_path):
    (tmp_path / "billing.py").write_text("class Invoice:\n    def total(self):\n        pass\n\ndef refund(order, amount):\n    pass\n")
    (tmp_path / "users.py").write_text("def create_user(name):\n    pass\n")
    (tmp_path / "web.js").write_text("export function render(page) {}\nclass Router {}\nconst go = (x) => x\n")
    m = packs.repo_map(_ws(tmp_path), "add a discount to the invoice total in billing")
    lines = m.splitlines()
    assert lines[2].startswith("billing.py: class Invoice: total; def refund(order, amount)")
    assert "web.js: function render(page); class Router; const go = (...) =>" in m
    assert packs.repo_map(_ws(tmp_path / "missing" if False else tmp_path), "") .startswith("<repo_map>")


def test_a_failing_check_after_a_change_is_not_a_finished_fix(tmp_path, monkeypatch):
    from meshpool_harness import run_task
    (tmp_path / "a.py").write_text("x = 1\n")
    _scripted(monkeypatch, [_call("read_file", {"path": "a.py"}, "c1"),
                            _call("edit", {"path": "a.py", "old": "x = 1", "new": "x = 2"}, "c2"),
                            _call("bash", {"command": "false"}, "c3"),
                            {"role": "assistant", "content": "Fixed."},          # premature: the check failed
                            _call("bash", {"command": "true"}, "c4"),
                            {"role": "assistant", "content": "Fixed; check passes."}])
    r = run_task("test_a.py fails, fix it", workdir=str(tmp_path), engine="agent")
    assert r["answer"] == "Fixed; check passes." and "`true` passed" in r["receipt"], r


def test_a_report_bug_in_a_code_project_is_code_not_documents(tmp_path):
    (tmp_path / "report.py").write_text("x = 1\n")
    (tmp_path / "store.py").write_text("y = 2\n")
    r = route("The daily sales report puts late-evening sales on the wrong day. Fix it.", kind="code",
              workdir=str(tmp_path))
    assert r.family == "code" and r.spec.startswith("code."), r
    assert route("Make a 10 slide deck on Nvidia", kind="code").spec == "docs"     # no project: still a deck
