"""Harness router: task classifier (signals -> one model call), scorecard selection, fitting, Route fields."""
import json
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(ROOT / "testkit" / "loop"))

from meshpool_harness.hrouter import classify as CL  # noqa: E402
from meshpool_harness.hrouter import harness_select as HS  # noqa: E402
from meshpool_harness.hrouter.route import route as route_fn  # noqa: E402

CONFIDENT = "the login test is failing since yesterday, fix it"
VAGUE = "Add GBP to the money formatter."


class FakeClient:
    def __init__(self, answer="code.build.feature", raises=False):
        self.answer, self.raises, self.calls = answer, raises, []

    def chat(self, **kw):
        self.calls.append(kw)
        if self.raises:
            raise RuntimeError("endpoint down")
        return {"choices": [{"message": {"content": self.answer}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 100}}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    CL.clear_cache()
    for k in ("MH_CLASSIFY", "MH_CLASSIFY_LOCAL", "MH_CLASSIFY_THRESHOLD", "MH_HARNESS_ROUTER", "MH_HARNESS_SCORECARD"):
        monkeypatch.delenv(k, raising=False)
    yield
    CL.clear_cache()


# ---------- classifier ----------------------------------------------------------------------------------------------
def test_confident_request_is_stage_1_and_makes_no_call():
    c = FakeClient()
    out = CL.classify(CONFIDENT, client=c)
    assert out["stage"] == "signals" and out["category"] == "code.debug" and not c.calls and not out["usage"]


def test_low_confidence_asks_the_model_once_with_tight_params():
    c = FakeClient("code.build.feature")
    out = CL.classify(VAGUE, client=c, model="m1")
    assert out["stage"] == "model" and out["category"] == "code.build.feature"
    assert len(c.calls) == 1 and c.calls[0]["max_tokens"] == 12 and c.calls[0]["temperature"] == 0.0
    assert c.calls[0]["model"] == "m1"
    assert out["usage"] == {"prompt_tokens": 900, "cached_tokens": 100, "completion_tokens": 5, "model_calls": 1}


def test_always_mode_and_threshold(monkeypatch):
    monkeypatch.setenv("MH_CLASSIFY", "always")
    c = FakeClient("code.refactor")
    assert CL.classify(CONFIDENT, client=c)["stage"] == "model" and len(c.calls) == 1
    monkeypatch.delenv("MH_CLASSIFY")
    CL.clear_cache()
    monkeypatch.setenv("MH_CLASSIFY_THRESHOLD", "0.99")        # nothing is that sure: the model decides
    c2 = FakeClient("code.debug")
    assert CL.classify(CONFIDENT, client=c2)["stage"] == "model" and len(c2.calls) == 1


@pytest.mark.parametrize("answer,expect", [("code.debug.perf", "code.debug.perf"), ("  Code.Debug  \n", "code.debug"),
                                           ("The category is code.test.", "code.test"), ("ambiguous-build", "ambiguous-build"),
                                           ("code.debuggg", ""), ("banana", ""), ("", "")])
def test_parse_validates_ids(answer, expect):
    assert CL.parse(answer) == expect


def test_invalid_answer_or_error_falls_back_to_stage_1():
    s1 = CL.classify(VAGUE, client=FakeClient("whatever"))
    assert s1["stage"] == "signals" and s1["category"] == "ambiguous-build" and not s1["usage"]
    CL.clear_cache()
    s2 = CL.classify(VAGUE, client=FakeClient(raises=True))
    assert s2["stage"] == "signals" and s2["category"] == "ambiguous-build"


def test_cache_per_prompt_hash():
    c = FakeClient("code.build.feature")
    a = CL.classify(VAGUE, client=c)
    b = CL.classify(VAGUE, client=c)
    assert len(c.calls) == 1 and b["cached"] and b["category"] == a["category"] and b["usage"] == {}


def test_local_stage_uses_embedder_and_never_the_network(monkeypatch):
    monkeypatch.setenv("MH_CLASSIFY_LOCAL", "1")
    vocab = ["slow", "fast", "money", "format"]

    def embed(xs):       # a bag of four words: enough to give the nearest example a high cosine
        return [[float(x.lower().count(w)) + 0.01 for w in vocab] for x in xs]
    monkeypatch.setattr(CL, "_EMB", {"fn": embed})
    c = FakeClient()
    out = CL.classify("slow slow money", client=c)
    assert out["stage"] in ("local", "signals") and not c.calls
    assert CL.local_classify("anything")[0] in CL.CATEGORIES
    monkeypatch.setattr(CL, "_EMB", {"fn": None})              # no fastembed: falls back to the model call
    CL.clear_cache()
    assert CL.classify(VAGUE, client=FakeClient("data"))["stage"] == "model"


def test_card_has_every_category_and_no_eval_prompts():
    text = CL.card("some request", "languages: python; files: 3; git repo: yes")
    for cid in CL.IDS:
        assert f"- {cid}:" in text
    assert "Request: some request" in text and "git repo: yes" in text
    assert len(CL.card("x" * 5000)) < 2000 + 1500 + 3000          # request truncated to ~1500 chars
    ev = json.loads((ROOT / "testkit" / "loop" / "suites" / "v2_eval.json").read_text())
    blob = " ".join(text.lower().split()) + " " + " ".join(" ".join(e).lower() for _, e in CL.CATEGORIES.values())
    for t in ev:
        p = " ".join(t["prompt"].lower().split())
        for i in range(0, max(1, len(p) - 40), 20):          # no 40-character stretch of any eval prompt
            assert p[i:i + 40] not in blob, t["id"]
        assert p[:60] not in blob


# ---------- scorecard selection -------------------------------------------------------------------------------------
def row(h, solved, tasks, cost=0.01, sec=60):
    return {"harness": h, "solved": solved, "tasks": tasks, "cost_per_task": cost, "median_s": sec}


ALL = {"meshpool", "pi", "codex", "hermes", "opencode"}


def test_best_solve_rate_wins():
    card = {"*": {"code.debug": [row("meshpool", 6, 10), row("pi", 8, 10, cost=0.5)]}}
    assert HS.pick(card, "m", "code.debug", ALL)["harness"] == "pi"


def test_ties_within_one_task_go_to_cheaper_then_faster():
    card = {"*": {"code.debug": [row("meshpool", 9, 10, cost=0.02), row("pi", 10, 10, cost=0.05),
                                 row("hermes", 9, 10, cost=0.02, sec=30)]}}
    assert HS.pick(card, "m", "code.debug", ALL)["harness"] == "hermes"          # cost tie -> faster
    assert HS.pick(card, "m", "code.debug", ALL, tie=0)["harness"] == "pi"       # strict
    card["*"]["code.debug"][1]["cost_per_task"] = 0.001
    assert HS.pick(card, "m", "code.debug", ALL)["harness"] == "pi"              # within a task and cheapest


def test_min_tasks_inherits_parent_category():
    card = {"*": {"code.debug.perf": [row("pi", 2, 2)],                          # too little evidence
                  "code.debug": [row("meshpool", 5, 10), row("codex", 8, 10)]}}
    got = HS.pick(card, "m", "code.debug.perf", ALL)
    assert got["harness"] == "codex" and got["level"] == "code.debug" and "inherited from code.debug" in got["reason"]
    card["*"]["code.debug.perf"] = [row("pi", 3, 3)]
    assert HS.pick(card, "m", "code.debug.perf", ALL)["harness"] == "pi"
    assert HS.parents("code.build.algorithm") == ["code.build.algorithm", "code.build", "code"]
    assert HS.parents("ambiguous-build") == ["ambiguous-build", "code.build", "code"]
    assert HS.parents("data") == ["data"]


def test_unavailable_harnesses_are_filtered():
    card = {"*": {"code.debug": [row("codex", 10, 10), row("meshpool", 6, 10)]}}
    assert HS.pick(card, "m", "code.debug", {"meshpool"})["harness"] == "meshpool"
    assert HS.pick(card, "m", "code.debug", {"pi"}) is None
    assert HS.pick({"*": {"code.debug": [row("codex-routed", 9, 10)]}}, "m", "code.debug", {"codex"})["harness"] == "codex-routed"


def test_model_entry_beats_star_fallback():
    card = {"m1": {"code.debug": [row("pi", 9, 10)]}, "*": {"code.debug": [row("codex", 9, 10)]}}
    assert HS.pick(card, "m1", "code.debug", ALL)["harness"] == "pi"
    assert HS.pick(card, "other", "code.debug", ALL)["harness"] == "codex"


def test_availability_respects_unsupported(monkeypatch):
    from meshpool_harness import harnesses as HR
    monkeypatch.setattr(HR, "unsupported", lambda h: "not shipped" if h in HR.NOT_SHIPPED else "")
    assert "codex" not in HS.available() and "opencode" not in HS.available() and "meshpool" in HS.available()


def test_select_end_to_end_default_and_scorecard(monkeypatch, tmp_path):
    out = HS.select(CONFIDENT, None, "m", card={}, avail={"meshpool"})
    assert out["harness"].startswith("meshpool") and out["category"] == "code.debug" and out["classifier_tokens"] == {}
    card = {"*": {"code.debug": [row("pi-routed", 9, 10)]}}
    out = HS.select(CONFIDENT, None, "m", card=card, avail={"meshpool", "pi"})
    assert out["harness"] == "pi-routed" and out["profile"] == "" and "pi-routed solved 9/10" in out["reason"]
    card = {"*": {"code.debug": [row("meshpool:lean", 9, 10)]}}
    out = HS.select(CONFIDENT, None, "m", card=card, avail={"meshpool"})
    assert out["harness"] == "meshpool:lean" and out["profile"] == "lean"
    out = HS.select(VAGUE, None, "m", client=FakeClient("code.build.feature"), card={}, avail={"meshpool"})
    assert out["category"] == "code.build.feature" and out["classifier_tokens"]["prompt_tokens"] == 900


# ---------- Route fields --------------------------------------------------------------------------------------------
def test_route_fields_off_by_default(monkeypatch):
    monkeypatch.setattr(HS, "select", lambda *a, **k: pytest.fail("selector must not run"))
    r = route_fn(CONFIDENT, workdir=None, kind="code")
    assert r.harness == "" and r.harness_reason == "" and r.category == ""


def test_route_fields_when_enabled(monkeypatch):
    monkeypatch.setenv("MH_HARNESS_ROUTER", "1")
    monkeypatch.setattr(HS, "load_scorecard", lambda: {"*": {"code.debug": [row("pi", 9, 10)]}})
    monkeypatch.setattr(HS, "available", lambda: {"meshpool", "pi"})
    r = route_fn(CONFIDENT, workdir=None, kind="code")
    assert r.harness == "pi" and r.category == "code.debug" and "pi solved 9/10" in r.harness_reason
    assert r.to_dict()["harness"] == "pi"


# ---------- fitting -------------------------------------------------------------------------------------------------
def test_fit_scorecard_on_synthetic_results(tmp_path):
    import harness_scorecard as HSC
    tasks = {("v2_dev", "t1"): {"id": "t1", "prompt": "The login test is failing since yesterday, fix it.", "files": {}},
             ("v2_dev", "t2"): {"id": "t2", "prompt": "Customers get a Traceback: KeyError in pricing.py", "files": {}},
             ("v2_eval", "e1"): {"id": "e1", "prompt": "The checkout test is failing, fix it.", "files": {}}}
    rows = []
    for arm, passes, usd in (("routed", [1, 1], 0.02), ("pi-routed", [1, 0], 0.01), ("profile:lean@m", [1, 1], 0.015),
                             ("codex", [0, 0], 0.05), ("base", [1, 1], 0.0), ("meta", [1, 1], 0.0)):
        for tid, ok in zip(("t1", "t2"), passes):
            rows.append({"suite": "v2_dev", "task": tid, "arm": arm, "model": "m", "pass": bool(ok), "usd": usd, "secs": 10.0})
    rows.append({"suite": "v2_eval", "task": "e1", "arm": "pi", "model": "m", "pass": True, "usd": 0.01, "secs": 5})
    card = HSC.fit(rows, tasks=tasks)
    got = {r["harness"]: r for r in card["m"]["code.debug"]}
    assert set(got) == {"meshpool", "pi-routed", "meshpool:lean", "codex"}          # base, meta, eval refused
    assert got["meshpool"]["solved"] == 2 and got["meshpool"]["tasks"] == 2 and got["pi-routed"]["solved"] == 1
    assert got["meshpool"]["cost_per_task"] == 0.02 and got["meshpool"]["median_s"] == 10.0
    assert "*" in card and {r["harness"] for r in card["*"]["code.debug"]} == set(got)
    assert "pi" not in got
    with_eval = HSC.fit(rows, tasks=tasks, include_eval=True)
    assert "pi" in {r["harness"] for r in with_eval["m"]["code.debug"]}
    md = HSC.markdown(card)
    assert "| code.debug |" in md and "meshpool:lean" in md

    # the CLI writes a file the selector can read
    d = tmp_path / "run"
    d.mkdir()
    (d / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    out = tmp_path / "card.json"
    assert HSC.main([str(d), "--out", str(out), "--md", str(tmp_path / "c.md")]) == 0
    assert json.loads(out.read_text())["_version"] == 1


def test_routing_decision_and_outcome_are_logged(tmp_path, monkeypatch):
    """Observability: each routed request leaves a decision line and an outcome line (no request text, no key)."""
    import json
    from meshpool_harness.hrouter import harness_select as HS
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_ROUTING_LOG", raising=False)
    monkeypatch.setenv("MH_CLASSIFY_THRESHOLD", "0")          # rules only: this test is about the log, not the classifier
    d = HS.select("Fix the crash: KeyError 'total' when an order has no lines", None, "m1", card={"*": {}}, avail={"meshpool"})
    assert d["decision_id"] and d["route_ms"] >= 0
    HS.log_outcome(d["decision_id"], ok=True, secs=3.2, usage={"prompt_tokens": 10, "completion_tokens": 2})
    rows = [json.loads(x) for x in (tmp_path / "routing-log.jsonl").read_text().splitlines()]
    assert [r["type"] for r in rows] == ["decision", "outcome"] and rows[0]["id"] == rows[1]["id"]
    assert "KeyError" not in json.dumps(rows) and rows[0]["prompt_len"] > 10
    monkeypatch.setenv("MH_ROUTING_LOG", "0")
    HS.log_outcome("x", ok=False, secs=1)
    assert len((tmp_path / "routing-log.jsonl").read_text().splitlines()) == 2


def test_slow_endpoint_never_holds_up_routing(monkeypatch):
    """The routing call is a hint: past its deadline the request goes on with the zero-token answer."""
    import time
    from meshpool_harness.hrouter import classify as CL

    class Slow:
        def chat(self, **kw):
            time.sleep(5)
            return {"choices": [{"message": {"content": "code.debug"}}]}
    monkeypatch.setenv("MH_CLASSIFY_TIMEOUT", "0.3")
    t0 = time.time()
    assert CL.ask_model("fix it", "", Slow(), "m") is None and time.time() - t0 < 2
