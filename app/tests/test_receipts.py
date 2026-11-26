"""The per-task receipt: numbers come from the outcome log, cost only from the user's own prices."""
import json
import time

import pytest

from meshpool_harness import receipts as RC
from meshpool_harness.hrouter import outcomes as OUT


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    for k in ("MH_PRICE_IN", "MH_PRICE_OUT", "MH_PRICE_CACHED"):
        monkeypatch.delenv(k, raising=False)
    yield tmp_path


def _row(chat, **kw):
    r = {"ts": 1.0, "chat": chat, "spec": "docs.slides", "harness": "meshpool-work", "model": "deepseek-v4.1-flash",
         "secs": 42.4, "tokens_in": 20000, "tokens_cached": 15000, "tokens_out": 3000, "calls": 6,
         "reason": "It is the best measured agent for decks", "error": False, "stopped": False}
    r.update(kw)
    OUT._append(r)


def test_no_turn_no_receipt():
    assert RC.receipt_for("none") is None


def test_receipt_reads_the_last_turn_and_marks_are_not_turns():
    _row("c1", secs=10.0)
    _row("c1", secs=42.4)
    OUT.mark("c1", "undo")
    r = RC.receipt_for("c1")
    assert r["turn"] == 1 and r["secs"] == 42.4 and r["calls"] == 6
    assert RC.receipt_for("c1", 0)["secs"] == 10.0 and RC.receipt_for("c1", -2)["secs"] == 10.0
    assert RC.receipt_for("c1", 5) is None
    assert RC.receipt_for("other") is None


def test_without_prices_cost_is_not_measured_and_tokens_are_shown():
    _row("c2")
    r = RC.receipt_for("c2")
    assert r["cost"] is None and r["cost_label"] == "not measured"
    assert "$" not in r["text"] and "23.0k tokens, cost not measured" in r["text"]
    assert r["text"].startswith("42 s")


def test_cost_comes_from_the_users_prices(monkeypatch):
    monkeypatch.setenv("MH_PRICE_IN", "0.30")
    monkeypatch.setenv("MH_PRICE_OUT", "1.20")
    monkeypatch.setenv("MH_PRICE_CACHED", "0.03")
    _row("c3")
    r = RC.receipt_for("c3")
    assert r["cost"] == round((5000 * 0.30 + 15000 * 0.03 + 3000 * 1.20) / 1e6, 4) == 0.0056
    # under a cent reads "<$0.01" in the line (one cost format, 0.0.18 design pass); the exact cost stays in the field
    assert r["cost_label"] == "" and "<$0.01" in r["text"] and "$0.0056" not in r["text"]


def test_the_agent_model_and_reason_are_in_the_line():
    _row("c4")
    t = RC.receipt_for("c4")["text"]
    # the model is named as the composer names it; meshpool's own agent is not named (0.0.18 design pass)
    assert "DeepSeek V4.1 Flash" in t and "it is the best measured agent for decks" in t and "Meshpool Work" not in t


def test_a_turn_without_a_reason_says_nothing_about_why():
    _row("c5", reason="")
    t = RC.receipt_for("c5")["text"]
    assert t.endswith("DeepSeek V4.1 Flash") and "because" not in t and "chosen" not in t


def test_a_broken_log_line_is_skipped():
    _row("c6")
    with OUT.path().open("a") as f:
        f.write("not json\n")
    assert RC.receipt_for("c6")["secs"] == 42.4


def test_log_turn_stores_the_reason():
    class A:
        usage = {"in": 10, "cached": 0, "out": 5, "calls": 1}
        model = "m"

    class S:
        id = "c7"
        agent = A()
        events = []
        harness = {"id": "meshpool-work"}
        t_turn = time.time()
        auto_reason = "chosen for decks"
    OUT.log_turn(S(), None, {})
    assert json.loads(OUT.path().read_text().splitlines()[-1])["reason"] == "chosen for decks"
    assert RC.receipt_for("c7")["reason"] == "chosen for decks"


def test_the_router_note_reads_in_plain_words():
    assert RC.plain_reason("docs words (1)") == "chosen for documents"
    assert RC.plain_reason("Fits documents best.") == "fits documents best"
    assert RC.plain_reason("") == ""
