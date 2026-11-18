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
    assert "[Harness: Debug · Python]" in text and "Reproduce first" in text and "pytest" in text


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
    assert got["turns"] == 3 and got["fail_rate"] == 1.0 and got["median_secs"] == 5
