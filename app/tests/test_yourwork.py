"""Your work: ranges, incognito left out, honest numbers, memory timeline, concrete suggestions, and a recap image
drawn from counts and categories only."""
import base64
import io
import json
import time

import pytest

from meshpool_harness import assistant as AS
from meshpool_harness import memstore as MS
from meshpool_harness import yourwork as YW
from meshpool_harness.hrouter import outcomes as OUT
from tests.summaries_fixtures import chat, setup

DAY = 86400


@pytest.fixture
def home(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setenv("MH_LEARN", "0")
    MS._ENSURED.clear()
    MS._CACHE.clear()
    return tmp_path


def _turn(sid, ts, spec="docs.slides", harness="meshpool-work", model="m1", tin=1000, tout=200):
    OUT._append({"ts": ts, "chat": sid, "spec": spec, "harness": harness, "model": model, "secs": 10,
                 "tokens_in": tin, "tokens_cached": 100, "tokens_out": tout, "calls": 3})


def test_empty_home_gives_an_empty_page(home):
    d = YW.build(3)
    assert d["empty"] and d["totals"]["chats"] == 0 and d["suggestions"] == [] and d["gallery"] == []


def test_ranges_cut_by_time(home):
    now = time.time()
    chat(home, "n1", ["Budget slides for March"], ["ok"], updated=now - 10 * DAY)
    chat(home, "n2", ["Hiring plan"], ["ok"], updated=now - 60 * DAY)
    chat(home, "n3", ["Old research on tariffs"], ["ok"], updated=now - 300 * DAY)
    got = {m: YW.build(m)["totals"]["chats"] for m in (1, 3, 6, 12)}
    assert got == {1: 1, 3: 2, 6: 2, 12: 3}
    assert YW.build(7)["months"] == 3                          # an unknown range falls back to 3 months


def test_incognito_chats_are_not_counted_anywhere(home):
    now = time.time()
    chat(home, "a1", ["Quarterly plan"], ["ok"], updated=now - DAY, files=["plan.docx"])
    chat(home, "a2", ["secret merger research"], ["ok"], updated=now - DAY, incognito=True, files=["merger.docx"])
    _turn("a1", now - DAY)
    _turn("a2", now - DAY)
    d = YW.build(1)
    assert d["totals"]["chats"] == 1 and d["totals"]["turns"] == 1
    assert "secret" not in json.dumps(d) and "merger" not in json.dumps(d)
    assert [g["name"] for g in d["gallery"]] == ["plan.docx"]


def test_honest_numbers_tokens_only_no_invented_cost_or_time(home):
    now = time.time()
    chat(home, "t1", ["Make a deck"], ["ok"], updated=now - DAY)
    _turn("t1", now - DAY, tin=1000, tout=250)
    _turn("t1", now - DAY + 60, tin=500, tout=50)
    u = YW.build(1)["usage"]
    assert u["tokens"] == 1800 and u["measured_turns"] == 2 and u["cost_usd"] is None
    assert u["routing_savings_usd"] is None
    assert "time_saved" not in json.dumps(YW.build(1))


def test_kinds_agents_models_and_when(home):
    now = time.time() - 2 * DAY
    chat(home, "k1", ["Slides for Q3"], ["ok"], updated=now, harness="meshpool-work", model="mA")
    chat(home, "k2", ["Fix the parser"], ["ok"], updated=now, harness="meshpool-code", model="mB")
    for i in range(3):
        _turn("k1", now + i, spec="docs.slides", harness="meshpool-work")
    _turn("k2", now, spec="code.debug", harness="meshpool-code")
    d = YW.build(1)
    assert d["kinds"][0] == {"label": "Slides", "count": 3}
    assert {a["name"] for a in d["agents"]} == {"Meshpool Work", "Meshpool Code"}
    assert {m["name"] for m in d["models"]} == {"mA", "mB"}
    assert d["when"]["busiest_weekday"] and d["when"]["busiest_hour"] is not None
    assert sum(d["when"]["hour"]) == 4 and sum(d["when"]["weekday"]) == 4


def test_topics_cluster_from_titles_and_summaries(home):
    now = time.time() - DAY
    for i, t in enumerate(["Churn analysis for Gulf", "Churn dashboard", "Churn follow up email", "Lunch ideas"]):
        chat(home, f"x{i}", [t], ["ok"], updated=now)
    topics = YW.build(1)["topics"]
    assert topics[0]["topic"] == "churn" and topics[0]["chats"] == 3


def test_skills_and_connectors_used(home):
    now = time.time() - DAY
    chat(home, "s1", ["Use $humanizer on this"], ["ok"], updated=now, tools=["mcp__jira__search", "mcp__meshpool-web__fetch"])
    chat(home, "s2", ["Another"], ["ok"], updated=now, tools=["mcp__jira__create", "mcp__notion__read"])
    d = YW.build(1)
    assert {"name": "jira", "chats": 2} in d["connectors"]
    assert not any("meshpool" in c["name"] for c in d["connectors"])


def test_teaching_timeline_comes_from_memory_history(home):
    MS.add("Prefers Segoe UI")
    f, _ = MS.add("Works at Acme")
    MS.update(f["id"], text="Works at Acme Gulf")
    d = YW.build(1)
    assert [t["what"] for t in d["taught"]] == ["Changed", "Learned", "Learned"]
    AS.save_settings(memory_on=False)
    assert YW.build(1)["taught"] == []
    AS.save_settings(memory_on=True)


def test_suggestions_are_concrete_and_have_evidence(home):
    now = time.time() - DAY
    for i in range(3):
        chat(home, f"w{i}", [f"Send the weekly sales report for region {i} to the finance team"], ["ok"], updated=now - i)
    for i in range(3):
        chat(home, f"b{i}", [f"Budget line {i}", ], ["ok"], title=f"Budget review {i}", updated=now - i)
    s = YW.build(1)["suggestions"]
    kinds = [x["kind"] for x in s]
    assert "schedule" in kinds and len(s) <= 3
    sched = next(x for x in s if x["kind"] == "schedule")
    assert sched["evidence"] and sched["action"]["payload"]["prompt"].startswith("Send the weekly sales report")
    proj = [x for x in s if x["kind"] == "project"]
    assert proj and proj[0]["action"]["payload"]["chat_ids"]


def test_suggestions_stay_quiet_with_no_pattern(home):
    now = time.time() - DAY
    for i, t in enumerate(["Pasta recipe", "Tax question", "Flight to Cairo"]):
        chat(home, f"q{i}", [t], ["ok"], updated=now)
    assert YW.build(1)["suggestions"] == []


def test_recap_uses_counts_and_categories_only(home):
    now = time.time() - DAY
    chat(home, "r1", ["Confidential merger memo for ACME-CORP"], ["ok"], updated=now, files=["acme-merger.docx"])
    MS.add("Prefers Segoe UI and keeps a private vault")
    _turn("r1", now)
    inputs = YW.recap_inputs(1)
    blob = json.dumps(inputs).lower()
    for leak in ("merger", "acme", "confidential", "memo", "segoe", "vault", ".docx"):
        assert leak not in blob
    assert set(inputs) == {"months", "chats", "turns", "deliverables", "kinds", "agents", "busiest_weekday",
                           "busiest_hour", "taught", "tokens"}


def test_recap_png_is_a_real_image_with_no_metadata_and_a_preview(home):
    from PIL import Image
    now = time.time() - DAY
    chat(home, "i1", ["Plan"], ["ok"], updated=now)
    _turn("i1", now)
    png = YW.recap_png(1)
    im = Image.open(io.BytesIO(png))
    assert im.format == "PNG" and im.size == (1080, 1350) and not getattr(im, "text", {})
    pv = YW.recap_preview(1)
    assert base64.b64decode(pv["png_b64"])[:8] == b"\x89PNG\r\n\x1a\n" and pv["inputs"]["chats"] == 1
    empty = YW.recap_png(3, inputs=dict(pv["inputs"], kinds=[], agents=[], busiest_weekday="", busiest_hour=None))
    assert Image.open(io.BytesIO(empty)).size == (1080, 1350)
