"""Harness profiles: resolution order, extends, env precedence, the default equals today's behaviour, the forced
profile, per-step methods, and the route carrying its profile. No network: the model transport is a script."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core, planning as P  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core import review as RV  # noqa: E402
from meshpool_harness.hrouter import profiles as PR  # noqa: E402
import importlib  # noqa: E402

R = importlib.import_module("meshpool_harness.hrouter.route")      # the package's `route` is the function
Route = R.Route

LONG = ("Build a task tracker CLI:\n1. models.py with a Task dataclass\n2. storage.py that saves to tasks.json\n"
        "3. cli.py with add, list and done commands\n4. tests in test_tracker.py\n5. a README.md with usage")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))         # no data-folder profiles.json
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", "none")                 # unit tests start from no assignment at all
    for v in ("MH_PLAN", "MH_REVIEW", "MH_MUTATION", "MH_COMPACT_CHARS", "MH_LEAN_TOOLS", "MH_SPECS_FILE",
              "MH_PROFILES_FILE", "MH_PROFILE", "MH_HARNESS_PROFILE", "MH_PLAN_EVERY"):
        monkeypatch.delenv(v, raising=False)


def conf(tmp_path, monkeypatch, data):
    p = tmp_path / "profiles.json"
    p.write_text(json.dumps(data))
    monkeypatch.setenv("MH_PROFILES_FILE", str(p))


def rt(spec="code.build", **kw):
    return Route(spec=spec, family=spec.split(".")[0], sub=spec.split(".")[-1], base="code", **kw)


# ---------- definitions -------------------------------------------------------------------------------------------
def test_builtin_profiles():
    assert PR.settings("full") == {"plan": "auto", "review": True, "mutation": True, "compact_chars": 160000,
                                   "methods": None, "lean_tools": True, "step_methods": False, "engine": "meshpool",
                                   "engine_style": "meshpool", "lean_core": False, "codex171": False}
    lean = PR.settings("lean")
    assert (lean["plan"], lean["review"], lean["mutation"], lean["compact_chars"], lean["methods"]) == (
        "off", False, False, 160000, "v2")
    assert PR.settings("lean+plan")["plan"] == "auto" and PR.settings("lean+plan")["review"] is False
    assert PR.settings("lean+review")["review"] is True and PR.settings("lean+review")["methods"] == "v2"
    assert PR.settings("nope") is None


def test_extends_chain_and_cycles(tmp_path, monkeypatch):
    conf(tmp_path, monkeypatch, {"profiles": {
        "a": {"extends": "lean+review", "compact_chars": 50000, "engine": "pi", "step_methods": True},
        "b": {"extends": "a", "review": False, "plan": "on"},
        "x": {"extends": "y"}, "y": {"extends": "x", "mutation": False}, "bad": {"engine": "weird", "plan": "maybe"}}})
    b = PR.settings("b")
    assert b["compact_chars"] == 50000 and b["engine"] == "pi" and b["review"] is False
    assert b["plan"] == "on" and b["methods"] == "v2" and b["step_methods"] is True
    assert PR.settings("x")["mutation"] is False                       # a cycle ends instead of looping
    assert PR.settings("bad")["engine"] == "meshpool" and PR.settings("bad")["plan"] == "auto"


# ---------- resolution --------------------------------------------------------------------------------------------
def test_default_without_a_file_is_full():
    r = R.route("fix the failing test in test_a.py", kind="code")
    assert r.profile == "full" and r.settings == PR.settings("full") and r.engine == "meshpool"
    assert "profile" in r.to_dict() and r.chip == r.label


def test_resolution_order_most_specific_first(tmp_path, monkeypatch):
    conf(tmp_path, monkeypatch, {"default": "lean", "assign": {
        "code.debug": "lean+plan", "code.debug.perf": "lean+review", "code.debug.python": "full",
        "code.debug.perf.python": "lean"}})
    assert PR.resolve(rt("code.debug"))[0] == "lean+plan"
    assert PR.resolve(rt("code.debug", variant="perf"))[0] == "lean+review"
    assert PR.resolve(rt("code.debug", language="python"))[0] == "full"
    assert PR.resolve(rt("code.debug", variant="perf", language="python"))[0] == "lean"
    assert PR.resolve(rt("code.build"))[0] == "lean"                    # nothing assigned: the default
    assert PR.resolve(rt("research", variant="", language=""))[0] == "lean"


def test_unknown_names_fall_back(tmp_path, monkeypatch):
    conf(tmp_path, monkeypatch, {"default": "ghost", "assign": {"code.build": "ghost2"}})
    assert PR.resolve(rt("code.build"))[0] == "full"


def test_file_layers_later_wins(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "profiles.json").write_text(json.dumps({"default": "lean", "assign": {"code.build": "lean"},
                                                    "profiles": {"mine": {"plan": "on"}}}))
    assert PR.resolve(rt("code.build"))[0] == "lean"
    conf(tmp_path, monkeypatch, {"assign": {"code.build": "mine"}})
    assert PR.resolve(rt("code.build"))[0] == "mine" and PR.resolve(rt("code.test"))[0] == "lean"


def test_forced_profile(tmp_path, monkeypatch):
    conf(tmp_path, monkeypatch, {"assign": {"code.build": "lean+plan"}})
    monkeypatch.setenv("MH_PROFILE", "lean")
    assert PR.resolve(rt("code.build"))[0] == "lean"
    monkeypatch.delenv("MH_PROFILE")
    monkeypatch.setenv("MH_HARNESS_PROFILE", "lean+review")
    assert R.route("write a new function in utils.py that parses dates", kind="code").profile == "lean+review"
    monkeypatch.setenv("MH_HARNESS_PROFILE", "unknown")                 # not a profile: ignored
    assert PR.resolve(rt("code.build"))[0] == "lean+plan"


def test_mh_profile_still_names_a_model_profile(monkeypatch):
    from meshpool_harness import profiles as MP
    monkeypatch.setenv("MH_PROFILE", "lean")
    assert MP.for_model("deepseek-v4.1-flash", "lean") is not None       # a harness name does not break the lookup


def test_route_carries_profile_and_chip(tmp_path, monkeypatch):
    conf(tmp_path, monkeypatch, {"assign": {"code.debug": "lean"}})
    r = R.route("Traceback (most recent call last):\n  File \"a.py\", line 3\nValueError: bad", kind="code")
    assert r.spec == "code.debug" and r.profile == "lean" and r.settings["plan"] == "off"
    assert r.chip.endswith("[lean]") and Route(**r.to_dict()).profile == "lean"      # survives a saved session
    pi = Route(spec="chat", family="chat", settings={"engine": "pi"})
    assert pi.engine == "pi"


# ---------- env precedence ----------------------------------------------------------------------------------------
def test_env_flags_win_over_the_profile(monkeypatch):
    lean = rt(settings=PR.settings("lean"), profile="lean")
    e = PR.effective(lean)
    assert (e["plan"], e["review"], e["mutation"]) == ("off", False, False)
    monkeypatch.setenv("MH_PLAN", "1")
    monkeypatch.setenv("MH_REVIEW", "1")
    monkeypatch.setenv("MH_MUTATION", "1")
    monkeypatch.setenv("MH_COMPACT_CHARS", "5000")
    monkeypatch.setenv("MH_LEAN_TOOLS", "0")
    e = PR.effective(lean)
    assert (e["plan"], e["review"], e["mutation"], e["compact_chars"], e["lean_tools"]) == ("on", True, True, 5000, False)
    monkeypatch.setenv("MH_REVIEW", "0")
    assert not RV.enabled("code.build", rt(settings=PR.settings("full")))


def test_profile_drives_plan_and_review():
    full, lean = rt(settings=PR.settings("full")), rt(settings=PR.settings("lean"))
    assert P.needed(full, LONG) and not P.needed(lean, LONG)
    assert RV.enabled("code.build", full) and not RV.enabled("code.build", lean)
    assert RV.enabled("code.build") and not RV.enabled("research")       # no route: today's default


def test_default_equals_today(monkeypatch):
    """No profile data at all behaves as before: plan auto, review on for the review specs, defaults for the rest."""
    bare = rt()
    assert P.mode(bare) == "auto" and P.mode() == "auto"
    assert RV.enabled("code.build", bare) and not RV.enabled("code.explain", bare)
    e = PR.effective(None)
    assert e["mutation"] and e["lean_tools"] and e["compact_chars"] == 160000
    r = R.route("add a --verbose flag to the CLI", kind="code")
    assert R.addon(r) == R.addon(Route(**{**r.to_dict(), "profile": "", "settings": {}}))      # built-in texts


# ---------- methods -----------------------------------------------------------------------------------------------
def test_methods_set_selects_the_texts(monkeypatch, tmp_path):
    base = rt("code.build")
    lean = rt("code.build", settings=PR.settings("lean"))
    v2 = json.loads((Path(__file__).resolve().parents[1] / "defaults" / "methods" / "v2.json").read_text())
    assert v2["specs"]["code.build"]["addon"] in R.addon(lean)
    assert v2["specs"]["code.build"]["addon"] not in R.addon(base)
    assert "Correctness first" in R.addon(base)
    f = tmp_path / "specs.json"                                          # an explicit env file beats the profile
    f.write_text(json.dumps({"specs": {"code.build": {"addon": "ENV WINS"}}}))
    monkeypatch.setenv("MH_SPECS_FILE", str(f))
    assert "ENV WINS" in R.addon(lean) and "ENV WINS" in R.addon(base)


def test_method_summary_is_one_sentence():
    s = R.method_summary(rt("code.debug"))
    assert s.startswith("Debugging: match the effort to the bug.") and "\n" not in s and len(s) <= 200
    assert R.method_summary(rt("docs", ) ) == ""


# ---------- the agent reads the route's settings ------------------------------------------------------------------
def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _agent(tmp_path, route):
    a = Agent(str(tmp_path), mode="auto")
    a.emit = lambda e: None
    a.set_route(route, "x")
    return a


def test_agent_reads_route_settings(tmp_path, monkeypatch):
    a = _agent(tmp_path, rt("code.refactor", settings=PR.settings("lean")))
    assert a.hsettings()["mutation"] is False and a._dropped() >= {"delegate_task"}
    assert a._mutation_feedback() == ""                       # the profile turns it off (env not set)
    monkeypatch.setenv("MH_LEAN_TOOLS", "0")
    assert a._dropped() == set()
    a2 = _agent(tmp_path, rt("code.refactor", settings={**PR.settings("full"), "lean_tools": False}))
    assert a2._dropped() == set()


def test_compact_limit_from_profile(tmp_path, monkeypatch):
    a = _agent(tmp_path, rt(settings={**PR.settings("full"), "compact_chars": 1234}))
    assert a.hsettings()["compact_chars"] == 1234
    monkeypatch.setenv("MH_COMPACT_CHARS", "99")
    assert a.hsettings()["compact_chars"] == 99


# ---------- per-step methods --------------------------------------------------------------------------------------
def _todo(i, *items):
    return _call(i, "todo_write", {"items": [{"content": c, "status": s} for c, s in items]})


def _run(tmp_path, settings, replies):
    a = Agent(str(tmp_path), mode="auto")
    a.emit = lambda e: None
    a.set_route(rt("code.build", settings=settings, profile="x"), LONG)
    seq, bodies = list(replies), []

    def fake(base, key, body, stream):
        bodies.append(body)
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake
    a.send(LONG)
    return [m["content"] for m in a.messages if m["role"] == "user" and "For this step" in str(m["content"])]


STEPS = [("write the failing test for the parser", "in_progress"), ("fix the bug in the parser", "pending")]


def test_step_method_line_once_per_sub_harness(tmp_path, monkeypatch):
    seen = []

    def fake_route(text, **kw):
        seen.append(text)
        spec = "code.test" if "test" in text else "code.debug" if "fix" in text or "bug" in text else "code.build"
        return Route(spec=spec, family="code", sub=spec.split(".")[1], base="code")
    monkeypatch.setattr("meshpool_harness.hrouter.route", fake_route)
    plan = lambda i, a, b: {"content": "", "tool_calls": [_todo(i, *[(c, s) for c, s in (a, b)])]}   # noqa: E731
    s1 = [("write the failing test for the parser", "in_progress"), ("fix the bug in the parser", "pending")]
    s2 = [("write the failing test for the parser", "completed"), ("fix the bug in the parser", "in_progress")]
    s3 = [("write the failing test for the parser", "completed"), ("fix the bug in the parser", "completed"),
          ("another test for the lexer", "in_progress")]
    s4 = [("write the failing test for the parser", "completed"), ("fix the bug in the parser", "in_progress"),
          ("another test for the lexer", "pending")]
    replies = [plan(1, *s1[:2]), plan(2, *s2[:2]),
               {"content": "", "tool_calls": [_todo(3, *s3)]}, {"content": "", "tool_calls": [_todo(4, *s4)]},
               {"content": "done"}]
    lines = _run(tmp_path, {**PR.settings("full"), "step_methods": True}, replies)
    assert len(lines) == 2                                    # code.test once (steps 1 and 3), code.debug once
    assert "Tests" in lines[0] and "Debug" in lines[1]
    assert all(len(l) < 330 and "\n" not in l.strip() for l in lines)


def test_step_methods_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr("meshpool_harness.hrouter.route", lambda text, **kw: Route(
        spec="code.test", family="code", sub="test", base="code"))
    replies = [{"content": "", "tool_calls": [_todo(1, *STEPS)]}, {"content": "done"}]
    assert _run(tmp_path, PR.settings("full"), replies) == []


def test_shipped_assignment(monkeypatch):
    """app/defaults/profiles.json: lean core for every route, the configuration measured on the 52 eval tasks against
    Codex (final20-21: 196/208 vs 186/208, cost 0.81x, time 0.82x). No per-route overrides: they were not part of
    that run. The previous default stays available as "codex"."""
    monkeypatch.delenv("MH_PROFILES_DEFAULTS", raising=False)
    for text, kind in (("Fix the crash: KeyError total when an order has no lines", "code"),
                       ("Write thorough tests for slug.slugify in test_slug.py", "code"),
                       ("What was the total revenue in orders.csv?", "cowork")):
        assert R.route(text, kind=kind).profile == "core"
    assert PR.settings("core")["lean_core"] is True
    s = PR.settings("codex")
    assert s["plan"] == "off" and s["review"] is False and s["mutation"] is False and s.get("engine_style") == "codex"

def test_codex_review_profile_keeps_codex_style_and_turns_review_on():
    from meshpool_harness.hrouter import profiles as PR
    s = PR.settings("codex+review")
    assert s["engine_style"] == "codex" and s["review"] is True and s["mutation"] is False and s["plan"] == "off"
    assert PR.settings("codex")["review"] is False                  # the default codex profile is unchanged
