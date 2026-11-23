"""The readers of the usage ledger: the Usage page, usage by day, receipts, outcome rows, run_task and bench cost.
A chat with one chat call and 41 nested engine calls, plus one call on a model with no known price."""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from meshpool_harness.core import usage as U

APP = Path(__file__).resolve().parents[1]
CHAT = "chat-readers-1"
CHEAP, BIG, UNKNOWN = "deepseek-v4.1-flash", "claude-sonnet-5-5", "mystery-model-x"
RAW = {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 400}}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_ENDPOINT_PRICES", "0")
    for k in ("MH_PRICE_IN", "MH_PRICE_OUT", "MH_PRICE_CACHED", "MH_PRICES", "MH_BUDGET_USD", "MH_BUDGET_PRICES",
              "MH_USAGE_TASK", "MH_USAGE_SOURCE", "MH_CHAT_ID", "MH_USAGE_LEDGER"):
        monkeypatch.delenv(k, raising=False)
    U._CFG.update(key=None, val={}, at=0.0)
    U.flush()
    from meshpool_harness.agent import core as AC
    monkeypatch.setattr(AC, "SESSIONS", tmp_path / "sessions")
    yield tmp_path
    U.flush()


def _rec(model, source, chat=CHAT, raw=RAW):
    U.record(model, raw, source=source, task=U.Task("t-" + chat, chat=chat))


def _ledger(tmp_path):
    """1 chat call, 41 engine calls and 1 unpriced call in one chat whose session says "auto" on an agent."""
    _rec(CHEAP, "chat")
    for _ in range(41):
        _rec(BIG, "engine:sheets")
    _rec(UNKNOWN, "helper:titles")
    U.flush()
    sd = tmp_path / "sessions" / CHAT
    sd.mkdir(parents=True)
    (sd / "session.json").write_text(json.dumps({
        "id": CHAT, "title": "Edit the budget", "model": "auto", "harness": "meshpool-work", "updated": time.time(),
        "usage": {"in": 1000, "cached": 400, "out": 100, "calls": 1}}), encoding="utf-8")


def test_usage_page_lists_models_not_agents(home):                    # F-22
    from meshpool_harness.ui import workbench as W
    _ledger(home)
    bm = W.usage()["by_model"]
    assert set(bm) == {CHEAP, BIG, UNKNOWN}
    assert bm[BIG]["calls"] == 41 and bm[BIG]["in"] == 41000 and bm[BIG]["out"] == 4100
    assert bm[CHEAP]["calls"] == 1 and bm[CHEAP]["threads"] == 1
    assert bm[UNKNOWN]["unpriced_calls"] == 1 and bm[UNKNOWN]["calls"] == 1


def test_chat_row_and_totals_include_nested_calls(home):              # S-02
    from meshpool_harness.ui import workbench as W
    _ledger(home)
    u = W.usage()
    (row,) = u["threads"]
    assert row["id"] == CHAT and row["title"] == "Edit the budget" and row["legacy"] is False
    assert row["calls"] == 43 and row["in"] == 43000 and row["out"] == 4300
    t = u["total"]
    known = U.cost_of(CHEAP, U.normalize(RAW))[0] + 41 * U.cost_of(BIG, U.normalize(RAW))[0]
    assert t["calls"] == 43 and t["unpriced_calls"] == 1 and t["cost"] == pytest.approx(known)
    assert u["by_source"]["engine:sheets"]["calls"] == 41


def test_chats_before_the_ledger_are_legacy_and_not_named_by_agent(home):
    from meshpool_harness.ui import workbench as W
    _ledger(home)
    sd = home / "sessions" / "old-chat"
    sd.mkdir()
    (sd / "session.json").write_text(json.dumps({
        "id": "old-chat", "title": "Old", "model": "auto", "harness": "meshpool-work", "updated": 5.0,
        "usage": {"in": 500, "cached": 0, "out": 50, "calls": 3}}), encoding="utf-8")
    u = W.usage()
    old = next(r for r in u["threads"] if r["id"] == "old-chat")
    assert old["legacy"] is True and old["calls"] == 3
    assert u["total"]["calls"] == 46
    assert u["by_model"][W._LEGACY_MODEL]["calls"] == 3
    assert "auto" not in u["by_model"] and "meshpool-work" not in u["by_model"]


def test_usage_has_the_gateway_snapshot(monkeypatch):                # P-12
    from meshpool_harness.core import gateway
    from meshpool_harness.ui import workbench as W
    snap = [{"endpoint": "llm.meshpool.ai", "inflight": 3, "limit": 16, "cap": 16, "waiting": 2, "degraded": False,
             "down_models": []}]
    monkeypatch.setattr(gateway, "snapshot", lambda: snap)
    assert W.usage()["gateway"] == snap


def test_usage_by_day_has_cost_from_the_ledger(home):                # S-02, W-14
    from meshpool_harness.ui import settings_v2 as SV
    _ledger(home)
    day = SV.usage_by_day()[0]
    assert day["day"] == time.strftime("%Y-%m-%d") and day["calls"] == 43 and day["unpriced_calls"] == 1
    assert day["cost"] > 0 and day["in"] == 43000


# ---- receipts

def _out_row(**kw):
    from meshpool_harness.hrouter import outcomes as OUT
    r = {"ts": 1.0, "chat": "c1", "spec": "docs.slides", "harness": "meshpool-work", "model": CHEAP, "secs": 42.4,
         "tokens_in": 20000, "tokens_cached": 15000, "tokens_out": 3000, "calls": 6, "error": False, "stopped": False}
    r.update(kw)
    OUT._append(r)


def test_receipt_shows_ledger_cost_unpriced_and_old_rows():           # W-14
    from meshpool_harness import receipts as RC
    _out_row(chat="priced", cost_usd=0.4234, unpriced_calls=0)
    _out_row(chat="mixed", cost_usd=0.4234, unpriced_calls=1)
    _out_row(chat="none", cost_usd=None, unpriced_calls=2)
    _out_row(chat="old")
    assert "$0.42" in RC.receipt_for("priced")["text"] and "unpriced" not in RC.receipt_for("priced")["text"]
    assert "$0.42 + 1 unpriced call" in RC.receipt_for("mixed")["text"]
    r = RC.receipt_for("none")
    assert "cost not known (unpriced model)" in r["text"] and r["cost"] is None and "$" not in r["text"]
    assert "not measured" in RC.receipt_for("old")["text"]
    assert RC.receipt_for("old")["cost_label"] == "not measured"


# ---- outcome rows

class _Agent:
    def __init__(self, usage):
        self.usage = usage
        self.model = CHEAP
        self.route = None

    def current_model(self):
        return self.model


def test_outcome_row_carries_nested_tokens_and_cost():                # K-05
    from meshpool_harness.hrouter import outcomes as OUT
    before = {"in": 100, "cached": 0, "out": 10, "calls": 1, "cost": 0.01, "unpriced_calls": 0}
    after = {"in": 1_170_100, "cached": 900_000, "out": 20_010, "calls": 43, "cost": 0.51, "unpriced_calls": 1}
    a = _Agent(after)
    a._task = U.Task("t-k05", chat="c-k05")
    sess = SimpleNamespace(agent=a, id="c-k05", events=[], harness={"id": "meshpool-work"}, t_turn=time.time(),
                           auto_reason="")
    OUT.log_turn(sess, SimpleNamespace(spec="sheets.edit"), before)
    row = json.loads(OUT.path().read_text().splitlines()[-1])
    assert row["tokens_in"] == 1_170_000 and row["calls"] == 42
    assert row["cost_usd"] == pytest.approx(0.50) and row["unpriced_calls"] == 1


def test_outcome_row_cost_none_when_nothing_was_priced():
    from meshpool_harness.hrouter import outcomes as OUT
    a = _Agent({"in": 10, "cached": 0, "out": 1, "calls": 2, "cost": 0.0, "unpriced_calls": 2})
    sess = SimpleNamespace(agent=a, id="c-np", events=[], harness={"id": "x"}, t_turn=time.time(), auto_reason="")
    OUT.log_turn(sess, None, {})
    assert json.loads(OUT.path().read_text().splitlines()[-1])["cost_usd"] is None


def test_refit_averages_cost():
    from meshpool_harness.hrouter import outcomes as OUT
    _out_row(chat="a", cost_usd=0.2, unpriced_calls=0)
    _out_row(chat="b", cost_usd=0.4, unpriced_calls=0)
    _out_row(chat="c")
    assert OUT.refit()["docs.slides|meshpool-work"]["avg_cost_usd"] == pytest.approx(0.3)


def test_harness_select_log_outcome_stores_cost(monkeypatch):
    from meshpool_harness.hrouter import harness_select as HS
    rows = []
    monkeypatch.setattr(HS, "_append", rows.append)
    HS.log_outcome("d1", ok=True, secs=3.0, usage={"prompt_tokens": 9, "model_calls": 2, "cost_usd": 0.07,
                                                    "unpriced_calls": 1})
    assert rows[0]["usage"]["cost_usd"] == 0.07 and rows[0]["usage"]["unpriced_calls"] == 1


# ---- run_task and the CLI

def test_run_agent_usage_has_cost_and_by_model(monkeypatch):
    from meshpool_harness import api
    from meshpool_harness.agent import core as AC

    class Fake:
        def __init__(self, workdir, **kw):
            self.usage = {}
            self._task = None

        def send(self, task):
            self._task = U.Task("t-api", chat="c-api")
            for m in (CHEAP, BIG, BIG, UNKNOWN):
                U.record(m, RAW, source="chat" if m == CHEAP else "engine:sheets", task=self._task)
            tot = self._task.totals()
            self.usage = {"in": tot["in"], "cached": tot["cached"], "out": tot["out"], "calls": tot["calls"],
                          "cost": tot["cost"], "unpriced_calls": tot["unpriced_calls"], "unreported_calls": 0,
                          "cache_write": 0, "reasoning": 0}
            return "done"

        def current_model(self):
            return CHEAP

        def plan_state(self):
            return {}
    monkeypatch.setattr(AC, "Agent", Fake)
    monkeypatch.setenv("MH_HROUTER", "0")
    out = api._run_agent_in("t", str(APP), "code", 5, None, str(APP))
    u = out["usage"]
    assert u["model_calls"] == 4 and u["unpriced_calls"] == 1 and u["cost_usd"] > 0
    assert set(u["by_model"]) == {CHEAP, BIG, UNKNOWN} and u["by_model"][BIG]["calls"] == 2
    assert u["by_model"][UNKNOWN]["cost_usd"] is None
    assert "unreported_calls" in u and "cache_write_tokens" in u and "reasoning_tokens" in u


def test_usage_dict_cost_none_when_all_unpriced():
    from meshpool_harness import api
    a = SimpleNamespace(usage={"in": 5, "cached": 0, "out": 1, "calls": 2, "cost": 0.0, "unpriced_calls": 2})
    assert api.usage_dict(a)["cost_usd"] is None


# ---- bench

def test_bench_cost_prefers_the_run_ledger():
    sys.path.insert(0, str(APP.parent / "testkit" / "loop"))
    import bench
    prices = {"in": 1.0, "cached": 0.1, "out": 2.0}
    row = {"prompt_tokens": 1_000_000, "cached_tokens": 0, "completion_tokens": 0}
    assert bench.cost(row, prices) == pytest.approx(1.0)
    assert bench.cost({**row, "usd_ledger": 0.25, "unpriced_calls": 0}, prices) == pytest.approx(0.25)
    assert bench.cost({**row, "usd_ledger": 0.25, "unpriced_calls": 2}, prices) == pytest.approx(1.0)
    assert bench.cost({**row, "usd_ledger": 0.25, "unpriced_calls": 0, "metered": True}, prices) == pytest.approx(1.0)


def test_your_work_cost_comes_from_the_ledger(home, monkeypatch):     # W-14
    from meshpool_harness import yourwork as YW
    from tests.summaries_fixtures import chat, setup
    setup(home, monkeypatch)
    now = time.time()
    chat(home, "yw1", ["Edit the budget sheet"], ["ok"], updated=now - 3600)
    _rec(CHEAP, "chat", chat="yw1")
    _rec(BIG, "engine:sheets", chat="yw1")
    _rec(UNKNOWN, "helper:titles", chat="yw1")
    _rec(BIG, "chat", chat="not-listed")
    U.flush()
    u = YW.build(3)["usage"]
    assert u["unpriced_calls"] == 1 and u["tokens_in"] == 3000 and u["cost_usd"] > 0
    assert "every model call" in u["note"]


def test_config_inflight_default_is_automatic():
    from meshpool_harness import config as C
    f = next(x for x in C.FIELDS if x[0] == "MH_MAX_INFLIGHT")
    assert f[2] == "" and "16" in f[3]
