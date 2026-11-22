"""Insights: every detector fires on its positive case and stays silent on near misses; ranking, dedupe, expiry,
budget, mute, throttle, quiet hours, privacy switches, noise."""
import json
import time
from datetime import datetime

import pytest

from meshpool_harness import assistant as A
from meshpool_harness import insights as I
from meshpool_harness.ui import insights_api as API

from insights_fixtures import DAY, NOW, WEEKLY, chat, corp, heavy_use, turn, weekly, write_chat


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("MH_INSIGHTS", raising=False)
    I._SCAN.clear()
    A.save_settings(memory_on=True)
    yield tmp_path


def use(monkeypatch, c):
    monkeypatch.setattr(I, "corpus", lambda now=None: c)
    I._SCAN.clear()


def types(cards):
    return sorted(x["type"] for x in cards)


# ---------- detectors: positive, then near misses -------------------------------------------------------------------------
def test_schedule_fires_on_a_weekly_habit_and_prefills_the_task():
    cards = I.detect_schedule(corp(weekly(4)))
    assert len(cards) == 1
    p = cards[0]["action"]["payload"]
    assert cards[0]["type"] == "schedule" and p["prompt"] == WEEKLY and p["only_on_change"] is True
    assert p["when"] == "weekly" and p["day"] in I.rhythm([c["ts"] for c in weekly(4)])["day"] and "4 times" in cards[0]["body"]
    assert len(cards[0]["evidence"]) == 4 and cards[0]["evidence"][0]["chat_id"]
    assert cards[0]["where"] == "home" and cards[0]["expires"] - cards[0]["created"] == 7 * DAY


def test_schedule_stays_silent_on_near_misses():
    assert I.detect_schedule(corp(weekly(2))) == []                                         # only twice
    irregular = [chat("a", NOW - 30 * DAY, WEEKLY), chat("b", NOW - 29 * DAY, WEEKLY), chat("c", NOW - 5 * DAY, WEEKLY)]
    assert I.detect_schedule(corp(irregular)) == []                                         # no rhythm
    assert I.detect_schedule(corp(weekly(4), tasks=[{"prompt": WEEKLY}])) == []             # already scheduled
    runs = [chat(c["id"], c["ts"], WEEKLY, scheduled="t1") for c in weekly(4)]
    assert I.detect_schedule(corp(runs)) == []                                              # those are scheduled runs


def test_skill_fires_on_a_repeated_procedure_and_a_repeated_correction():
    t = "Pull the vendor invoices from the folder, match them to purchase orders and flag mismatches in a sheet"
    proc = [chat("a", NOW - 30 * DAY, t, steps=5), chat("b", NOW - 26 * DAY, t, steps=4), chat("c", NOW - 3 * DAY, t, steps=6)]
    cards = I.detect_skill(corp(proc))
    assert len(cards) == 1 and cards[0]["action"]["kind"] == "draft_skill" and len(cards[0]["action"]["payload"]["chat_ids"]) == 3
    corr = [chat(f"k{i}", NOW - i * 3 * DAY, "Make a deck", texts=["Make a deck", "Never put an eyebrow label above the slide title"])
            for i in range(3)]
    cards = I.detect_skill(corp(corr))
    assert len(cards) == 1 and "same correction in 3 chats" in cards[0]["body"].lower()


def test_skill_stays_silent_on_near_misses():
    t = "Pull the vendor invoices from the folder, match them to purchase orders and flag mismatches in a sheet"
    assert I.detect_skill(corp([chat("a", NOW - 9 * DAY, t, steps=5), chat("b", NOW - 3 * DAY, t, steps=5)])) == []   # twice
    simple = [chat("a", NOW - 30 * DAY, t, steps=1), chat("b", NOW - 26 * DAY, t, steps=1), chat("c", NOW - 3 * DAY, t, steps=1)]
    assert I.detect_skill(corp(simple)) == []                                                          # one step: not a procedure
    same = [chat("z", NOW, "x", texts=["Never use em dashes"] * 3)]
    assert I.detect_skill(corp(same)) == []                                                            # one chat, said thrice


BG = ("Our client is a Gulf logistics group with fifty depots, three languages and a strict style guide that bans em "
      "dashes and wants sentence case titles. Reports go to the board each quarter and always lead with cash. ")


def test_project_fires_on_repeated_background_outside_a_project():
    cs = [chat(f"p{i}", NOW - i * DAY, f"Context: {BG}\n\nNow draft email {i}") for i in range(3)]
    cs = [dict(c, texts=[f"Context for the job.\n\n{BG}\n\nNow draft email {i}"]) for i, c in enumerate(cs)]
    cards = I.detect_project(corp(cs))
    assert len(cards) == 1 and cards[0]["action"]["kind"] == "make_project" and "3 chats" in cards[0]["body"]
    assert "Gulf logistics" in cards[0]["action"]["payload"]["instructions"]


def test_project_stays_silent_on_near_misses():
    mk = lambda n, proj="": [dict(chat(f"p{i}", NOW - i * DAY, "x", project=proj), texts=[f"Hello.\n\n{BG}\n\nJob {i}"]) for i in range(n)]  # noqa: E731
    assert I.detect_project(corp(mk(2))) == []                                       # twice only
    assert I.detect_project(corp(mk(3, "p_a"))) == []                                # already in a project
    short = [chat(f"s{i}", NOW - i * DAY, "Our client is a logistics group. Be brief.") for i in range(3)]
    assert I.detect_project(corp(short)) == []                                       # short, not a pasted background


def test_route_fires_when_one_agent_keeps_failing_and_another_does_better():
    rows = [turn(i, "docs.slides", "meshpool-work", failed=i in (1, 3, 5)) for i in range(6)] + \
           [turn(20 + i, "docs.slides", "meshpool-code", failed=False) for i in range(4)]
    cards = I.detect_route(corp(outcomes=rows))
    assert len(cards) == 1 and cards[0]["action"]["payload"]["harness"] == "meshpool-code"
    assert "3 of the last 6" in cards[0]["body"]


def test_route_marks_count_as_failures_and_cheaper_equal_quality_fires():
    rows = [turn(i, "docs.sheets", "meshpool-work") for i in range(6)]
    for i in (0, 2, 4):
        rows.append({"ts": NOW - 1000 + i + 0.5, "chat": "c" + str(i), "outcome": "undo"})
    rows += [turn(30 + i, "docs.sheets", "pi") for i in range(4)]
    assert len(I.detect_route(corp(outcomes=rows))) == 1
    cheap = [turn(i, "docs.analyze", "meshpool-work", tin=4000, tout=2000) for i in range(10)] + \
            [turn(50 + i, "docs.analyze", "pi", tin=1500, tout=700) for i in range(5)]
    cards = I.detect_route(corp(outcomes=cheap))
    assert len(cards) == 1 and "fewer tokens" in cards[0]["title"] and "no more failures" in cards[0]["body"]


def test_route_stays_silent_on_near_misses():
    two = [turn(i, "docs.slides", "meshpool-work", failed=i in (1, 3)) for i in range(6)] + \
          [turn(20 + i, "docs.slides", "meshpool-code") for i in range(4)]
    assert I.detect_route(corp(outcomes=two)) == []                                   # only 2 of 6
    nobetter = [turn(i, "docs.slides", "meshpool-work", failed=i in (1, 3, 5)) for i in range(6)] + \
               [turn(20 + i, "docs.slides", "pi", failed=i < 3) for i in range(4)]
    assert I.detect_route(corp(outcomes=nobetter)) == []                              # the other is no better
    worse = [turn(i, "docs.analyze", "meshpool-work", tin=4000, tout=2000) for i in range(10)] + \
            [turn(50 + i, "docs.analyze", "pi", failed=i < 2, tin=1000, tout=500) for i in range(5)]
    assert I.detect_route(corp(outcomes=worse)) == []                                 # cheaper but worse: never offered
    short = [turn(i, "docs.analyze", "meshpool-work", tin=4000, tout=2000) for i in range(9)] + \
            [turn(50 + i, "docs.analyze", "pi", tin=1000, tout=500) for i in range(5)]
    assert I.detect_route(corp(outcomes=short)) == []                                 # under 10 turns of that spec


def test_connect_fires_on_pasted_service_content():
    cs = [chat("a", NOW - DAY, "Look at https://acme.atlassian.net/browse/OPS-12 and fix it"),
          chat("b", NOW - 2 * DAY, "Tickets OPS-3 and OPS-9 are blocked, summarize them")]
    cards = I.detect_connect(corp(cs, connector=lambda s: {"name": "Jira", "connected": False, "id": "atlassian"}))
    assert len(cards) == 1 and cards[0]["title"] == "Connect Jira?" and cards[0]["action"]["payload"]["connector"] == "atlassian"


def test_connect_stays_silent_on_near_misses():
    jira = lambda s: {"name": "Jira", "connected": False, "id": "atlassian"}  # noqa: E731
    one = [chat("a", NOW, "See https://acme.atlassian.net/browse/OPS-12")]
    assert I.detect_connect(corp(one, connector=jira)) == []                          # one chat
    two = [chat("a", NOW, "See https://acme.atlassian.net/browse/OPS-12"), chat("b", NOW, "And OPS-3 plus OPS-4")]
    assert I.detect_connect(corp(two, connector=lambda s: {"name": "Jira", "connected": True, "id": "atlassian"})) == []
    noise = [chat("a", NOW, "COVID-19 and ISO-9001 rules"), chat("b", NOW, "RFC-2119 and PEP-8, also COVID-19 and COVID-20")]
    assert I.detect_connect(corp(noise, connector=jira)) == []                        # not tickets


def test_connect_ignores_a_service_the_catalog_does_not_have():
    cs = [chat("a", NOW, "https://x.slack.com/archives/C123ABC/p1700000000"), chat("b", NOW, "https://y.slack.com/archives/C123ABC/p1700000001")]
    assert I.detect_connect(corp(cs, connector=lambda s: None)) == []
    assert len(I.detect_connect(corp(cs))) == 1


def _rerun_corp(new_name="Q4_sales.xlsx", new_mtime=NOW - DAY, header=None, prior_in="Q3_sales.xlsx"):
    prior = chat("old", NOW - 5 * DAY, "Summarize sales by region in one slide", project="p_a", inputs=[prior_in])
    ff = {"p_a": [{"name": prior_in, "path": "/f/" + prior_in, "mtime": NOW - 6 * DAY},
                  {"name": new_name, "path": "/f/" + new_name, "mtime": new_mtime}]}
    return corp([prior], folder_files=ff, header=header or (lambda p: None))


def test_rerun_fires_on_a_new_file_that_matches_an_analyzed_one():
    cards = I.detect_rerun(_rerun_corp())
    assert len(cards) == 1 and cards[0]["action"]["kind"] == "new_chat"
    assert cards[0]["action"]["payload"]["files"] == ["/f/Q4_sales.xlsx"] and "Summarize sales" in cards[0]["action"]["payload"]["prompt"]


def test_rerun_stays_silent_on_near_misses():
    assert I.detect_rerun(_rerun_corp(new_name="Q4_costs.xlsx")) == []                 # a different file
    assert I.detect_rerun(_rerun_corp(new_mtime=NOW - 9 * DAY)) == []                  # older than the analysis
    assert I.detect_rerun(_rerun_corp(new_name="Q4_sales.csv")) == []                  # a different type
    hd = lambda p: ("a", "b", "c") if "Q3" in p else ("x", "y", "z")                   # noqa: E731
    assert I.detect_rerun(_rerun_corp(header=hd)) == []                                # same name, other structure


def test_teach_fires_on_consistent_edits_only():
    t = {"file": "d.pptx", "chat_id": "c1", "kind": "deck", "count": 9, "total": 9, "rules": ["Sentence case titles", "Arial"],
         "label": "You changed all 9 slide titles to sentence case and the font to Arial. Make these your defaults?"}
    cards = I.detect_teach(corp(teach=[t]))
    assert len(cards) == 1 and cards[0]["title"].startswith("You changed all 9") and cards[0]["action"]["payload"]["rules"]
    assert I.detect_teach(corp(teach=[dict(t, count=1)])) == []                          # a one-off edit is noise
    assert I.detect_teach(corp(teach=[dict(t, rules=[])])) == []


def test_remember_fires_on_a_collaborator_and_a_tool_and_is_quiet_otherwise():
    cs = [chat(f"r{i}", NOW - i * DAY, LAYLA[i]) for i in range(4)]
    cards = I.detect_remember(corp(cs))
    assert len(cards) == 1 and cards[0]["action"]["payload"]["text"] == "Works regularly with Layla Haddad"
    assert I.detect_remember(corp(cs[:3])) == []                                          # three is not a pattern
    days = [chat(f"d{i}", NOW - i * DAY, f"Email Monday summary {i} for Friday") for i in range(6)]
    assert I.detect_remember(corp(days)) == []                                            # weekdays are not people
    tools = [chat(f"t{i}", NOW - i * DAY, f"Update the Jira board for sprint {i}") for i in range(5)]
    assert len(I.detect_remember(corp(tools))) == 1


def test_followup_chips_follow_the_output_type():
    assert [c["label"] for c in I.followup_chips("deck")] == ["Add speaker notes", "One-page memo"]
    assert [c["label"] for c in I.followup_chips("workbook")] == ["Sensitivity table", "Chart the trend"]
    assert [c["label"] for c in I.followup_chips("code")] == ["Write tests", "Open a pull request"]
    assert len(I.followup_chips("research")) == 1 and I.followup_chips("") == []
    deck = chat("c1", NOW, "Make a deck on coffee", outputs=["coffee.pptx"])
    cards = I.detect_followup(corp([deck]), "c1")
    assert len(cards) == 2 and all(c["where"] == "turn" and c["action"]["kind"] == "send" for c in cards)
    assert I.detect_followup(corp([chat("c2", NOW, "Hi there friend")]), "c2") == []
    res = chat("c3", NOW, "Research coffee prices", tools=["web_research"])
    assert I.output_kind(res) == "research"
    sched = chat("c4", NOW, "Weekly", outputs=["a.xlsx"], scheduled="t1")
    assert I.detect_followup(corp([sched]), "c4")[0]["where"] == "task"


# ---------- cards at the boundaries: budget, ranking, dedupe, expiry, mute, throttle ---------------------------------------
LAYLA = ["Email Layla Haddad the revised launch timeline", "Ask Layla Haddad which vendor won the tender",
         "Tell Layla Haddad the audit slipped to March", "Meeting with Layla Haddad on pricing tiers, prep notes please",
         "Send it to Layla Haddad once the budget sheet is checked"]
HABITS = ["Summarize the weekly pricing report for the GCC region and list the three biggest changes",
          "Pull this week's hiring tracker and tell me which roles are behind plan",
          "Reconcile the open invoices against the bank export and flag anything unmatched",
          "Draft the Friday forecast note for the leadership team from the pipeline sheet",
          "Collect survey responses from the shared folder and chart the satisfaction score",
          "List tender deadlines due in the next two weeks and who owns each one",
          "Compare payroll totals with last month and explain any jump over five percent",
          "Check the audit log export for failed logins and write a short security digest"]


def many_cards(n=6):
    """n distinct weekly habits, so budgets bind."""
    cs = []
    for i in range(n):
        cs += weekly(4, text=HABITS[i], prefix=f"h{i}_", start=NOW - 40 * DAY + i * 3600)
    return cs


def _mixed(n_sched=3):
    """Several weekly habits (one idea: schedule) plus a pasted background (another idea: project)."""
    cs = many_cards(n_sched)
    pj = [chat(f"p{i}", NOW - i * DAY, "x") for i in range(3)]
    pj = [dict(c, texts=[f"Context for the job.\n\n{BG}\n\nNow draft email {i}"]) for i, c in enumerate(pj)]
    return cs + pj


def test_home_shows_at_most_two_one_per_type_and_the_same_on_a_second_look(monkeypatch):
    use(monkeypatch, corp(_mixed(4)))
    first = I.cards_for("home", now=NOW)
    assert 1 <= len(first) <= 2 and len({c["type"] for c in first}) == len(first)      # never two of a kind
    again = I.cards_for("home", now=NOW + 60)
    assert [c["id"] for c in again] == [c["id"] for c in first]                  # stable, not reshuffled
    assert len([r for r in I._log_rows() if r["event"] == "shown"]) == len(first)  # logged once each
    for c in first:                                                             # dismissing does not buy more that day
        I.act(c["id"], "dismissed", NOW + 120)
    assert I.cards_for("home", now=NOW + 180) == []
    nxt = I.cards_for("home", now=NOW + DAY)                                     # the next day, new cards, never the old ones
    assert not {c["id"] for c in nxt} & {c["id"] for c in first} and len(nxt) <= 2


def test_nothing_to_suggest_means_nothing_is_shown(monkeypatch):
    use(monkeypatch, corp([chat("a", NOW - DAY, "Hello there")]))
    assert I.cards_for("home", now=NOW) == []


def test_ranking_prefers_a_type_the_user_accepts(monkeypatch):
    cs = many_cards(1) + [chat(f"pj{i}", NOW - i * DAY, "x") for i in range(3)]
    cs = many_cards(1) + [dict(chat(f"pj{i}", NOW - i * DAY, "x"), texts=[f"Hi.\n\n{BG}\n\nJob {i}"]) for i in range(3)]
    use(monkeypatch, corp(cs))
    for i in range(6):                                                           # the user keeps accepting projects
        I._log(f"old{i}", "project", "shown", NOW - DAY)
        I._log(f"old{i}", "project", "accepted", NOW - DAY)
    got = I.cards_for("home", now=NOW)
    assert got[0]["type"] == "project"


def test_dedupe_one_card_per_pattern(monkeypatch):
    use(monkeypatch, corp(weekly(4)))
    I.scan(force=True)
    I.scan(force=True)
    assert len(I._cards()) == 1
    assert len(I.cards_for("home", now=NOW)) == 1


def test_cards_expire_after_seven_days(monkeypatch):
    use(monkeypatch, corp(weekly(4)))
    assert len(I.cards_for("home", now=NOW)) == 1
    monkeypatch.setattr(I, "corpus", lambda now=None: corp())                    # the pattern is gone from the data
    I._SCAN.clear()
    assert I.cards_for("home", now=NOW + 8 * DAY) == []
    assert [r["event"] for r in I._log_rows()][-1] == "expired"


def test_followup_chips_end_at_the_next_message(monkeypatch, home):
    deck = chat("c1", NOW, "Make a deck on coffee", outputs=["coffee.pptx"])
    use(monkeypatch, corp([deck]))
    got = I.cards_for("chat:c1", now=NOW)
    assert len(got) == 2
    write_chat(home / "runs", dict(deck, texts=["Make a deck on coffee", "Thanks, now shorten it"]))
    I._SCAN.clear()
    assert I.cards_for("chat:c1", now=NOW + 60) == []


def test_a_turn_end_has_chips_plus_at_most_one_other_card(monkeypatch):
    t = "Pull the vendor invoices from the folder and match them to purchase orders and flag mismatches"
    cs = [chat(f"s{i}", NOW - d * DAY, t, steps=5, outputs=["x.xlsx"] if i == 2 else []) for i, d in enumerate((30, 26, 1))]
    cs += [chat(f"j{i}", NOW - i * DAY, f"Look at https://acme.atlassian.net/browse/OPS-{i}0") for i in range(2)]
    cs[2] = dict(cs[2], texts=[t] + ["See https://x.slack.com/archives/C1/p1700000000"])
    use(monkeypatch, corp(cs))
    got = I.cards_for("turn:s2", now=NOW)
    others = [c for c in got if c["type"] != "followup"]
    assert len([c for c in got if c["type"] == "followup"]) == 2 and len(others) <= 1


def test_mute_stops_the_type_and_undo_brings_it_back(monkeypatch):
    use(monkeypatch, corp(weekly(4)))
    card = I.cards_for("home", now=NOW)[0]
    r = I.act(card["id"], "muted", NOW)
    assert r["ok"] and not I.type_on("schedule")
    view = I.settings_view()
    assert view["history"][0]["card_id"] == card["id"]
    I._SCAN.clear()
    assert I.cards_for("home", now=NOW + 1) == []
    assert I.undo_mute(view["history"][0]["id"])["ok"] and I.type_on("schedule")


def test_a_dismissed_card_never_comes_back(monkeypatch):
    use(monkeypatch, corp(weekly(4)))
    card = I.cards_for("home", now=NOW)[0]
    I.act(card["id"], "dismissed", NOW)
    for d in range(1, 12):
        I._SCAN.clear()
        assert I.cards_for("home", now=NOW + d * DAY) == []


def test_auto_throttle_after_ten_showings_under_15_percent(monkeypatch):
    use(monkeypatch, corp())
    for i in range(10):
        I._log(f"c{i}", "connect", "shown", NOW)
    I._log("c0", "connect", "accepted", NOW)
    I.settings_view()
    I._check_throttle("connect")
    assert I.acceptance()["connect"]["rate"] == 0.1 and not I.type_on("connect")
    n = I.settings_view()["notices"]
    assert len(n) == 1 and "stopped" in n[0]["text"]
    API.handle_post("/api/insights/settings", {"action": "ack_notices"})
    assert I.settings_view()["notices"] == []                                      # said once
    API.handle_post("/api/insights/settings", {"type": "connect", "on": True})
    assert I.type_on("connect")
    for i in range(10):                                                           # 20% is not throttled
        I._log(f"d{i}", "rerun", "shown", NOW)
    for i in range(2):
        I._log(f"d{i}", "rerun", "accepted", NOW)
    I._check_throttle("rerun")
    assert I.type_on("rerun")


def test_reset_learning_clears_counts_and_throttles(monkeypatch):
    use(monkeypatch, corp())
    for i in range(10):
        I._log(f"c{i}", "connect", "shown", NOW)
    I._check_throttle("connect")
    I.reset_learning()
    assert I.type_on("connect") and I.acceptance()["connect"]["shown"] == 0


# ---------- switches and privacy ------------------------------------------------------------------------------------------
def test_quiet_hours_hide_everything(monkeypatch):
    use(monkeypatch, corp(weekly(4)))
    I.save_settings(quiet_hours={"on": True, "start": "22:00", "end": "07:00"})
    at = lambda h: datetime(2026, 11, 22, h, 30).timestamp()  # noqa: E731
    assert I.quiet_now(at(23)) and I.quiet_now(at(3)) and not I.quiet_now(at(12))
    assert I.cards_for("home", now=at(23)) == []
    assert len(I.cards_for("home", now=at(12))) == 1
    I.save_settings(quiet_hours={"on": True, "start": "09:00", "end": "17:00"})
    assert I.quiet_now(at(12)) and not I.quiet_now(at(20))
    assert I.take_os_notification(at(12)) is None


def test_one_os_notification_a_day(monkeypatch):
    use(monkeypatch, corp(many_cards(3)))
    I.cards_for("home", now=NOW)
    assert I.take_os_notification(NOW) is not None
    assert I.take_os_notification(NOW + 60) is None
    assert I.take_os_notification(NOW + DAY) is not None


def test_env_and_setting_switch_everything_off(monkeypatch):
    use(monkeypatch, corp(weekly(4)))
    monkeypatch.setenv("MH_INSIGHTS", "0")
    assert not I.enabled() and I.cards_for("home", now=NOW) == [] and I.scan(force=True) == [] and I.scan_after_turn("c") == []
    monkeypatch.delenv("MH_INSIGHTS")
    I.save_settings(insights_on=False)
    assert I.cards_for("home", now=NOW) == []
    I.save_settings(insights_on=True)
    assert len(I.cards_for("home", now=NOW)) == 1


def test_memory_off_removes_memory_derived_cards(monkeypatch):
    cs = [chat(f"r{i}", NOW - i * DAY, LAYLA[i]) for i in range(4)]
    teach = [{"file": "d.pptx", "chat_id": "c1", "kind": "deck", "count": 9, "total": 9, "rules": ["Arial"]}]
    use(monkeypatch, corp(cs, teach=teach))
    assert types(I.cards_for("home", now=NOW)) == ["remember", "teach"]
    A.save_settings(memory_on=False)
    I._SCAN.clear()
    assert I.cards_for("home", now=NOW + 1) == []


def test_incognito_chats_are_not_read_and_get_no_cards(monkeypatch, home):
    c = corp([dict(c, incognito=True) for c in weekly(4)])
    assert I.detect_schedule(c) == []                                              # corpus drops incognito chats
    deck = chat("inc", NOW, "Make a deck", outputs=["a.pptx"], incognito=True)
    write_chat(home / "runs", deck)
    use(monkeypatch, corp([chat("inc", NOW, "Make a deck", outputs=["a.pptx"])]))
    assert I.cards_for("chat:inc", now=NOW) == [] and I.scan_after_turn("inc") == []


def test_accepting_a_remember_card_adds_a_pending_fact_never_an_active_one(monkeypatch):
    from meshpool_harness import memstore as MS
    cs = [chat(f"r{i}", NOW - i * DAY, LAYLA[i]) for i in range(4)]
    use(monkeypatch, corp(cs))
    card = I.cards_for("home", now=NOW)[0]
    r = I.act(card["id"], "accepted", NOW)
    f = MS.get(r["fact_id"])
    assert f["status"] == "pending" and f["kind"] == "noticed"
    assert "Layla" not in A.memory()


def test_scan_after_turn_moves_a_chat_card_to_the_turn_end(monkeypatch, home):
    cs = weekly(3, prefix="q") + [chat("q3", NOW - 100, WEEKLY, outputs=["r.xlsx"])]
    cs = [dict(c, ts=NOW - (28 - 7 * i) * DAY) for i, c in enumerate(cs[:3])] + [dict(cs[3], ts=NOW - DAY * 0.001)]
    cs[3]["ts"] = NOW - 100
    cs[2]["ts"] = NOW - 7 * DAY
    cs[1]["ts"] = NOW - 14 * DAY
    cs[0]["ts"] = NOW - 21 * DAY
    for c in cs:
        write_chat(home / "runs", c)
    use(monkeypatch, corp(cs))
    got = I.scan_after_turn("q3", now=NOW)
    assert "followup" in types(got) and len([c for c in got if c["type"] != "followup"]) <= 1


def test_settings_view_lists_every_type_with_rates():
    v = I.settings_view()
    assert [t["type"] for t in v["types"]] == list(I.TYPES) and all("rate" in t for t in v["types"])
    code, body = API.handle_get("/api/insights/settings")
    assert code == 200 and body["insights_on"] is True
    code, body = API.handle_post("/api/insights/settings", {"type": "route", "on": False})
    assert not next(t for t in body["types"] if t["type"] == "route")["on"]
    code, body = API.handle_post("/api/insights/settings", {"quiet_hours": {"on": True, "start": "21:00", "end": "06:30"}})
    assert body["quiet_hours"] == {"on": True, "start": "21:00", "end": "06:30"}
    assert API.handle_post("/api/insights/settings", {"type": "nope"})[0] == 400
    assert API.handle_post("/api/insights/act", {"id": "ins_missing", "event": "dismissed"})[0] == 404


def test_api_cards_and_act_round_trip(monkeypatch):
    use(monkeypatch, corp(weekly(4)))
    code, body = API.handle_get("/api/insights", "where=home")
    assert code == 200 and len(body["cards"]) == 1
    cid = body["cards"][0]["id"]
    code, r = API.handle_post("/api/insights/act", {"id": cid, "event": "accepted"})
    assert code == 200 and r["action"]["kind"] == "schedule"
    assert API.handle_get("/api/insights", "where=home")[1]["cards"] == []


# ---------- noise -------------------------------------------------------------------------------------------------------------
def test_a_fresh_install_shows_nothing(monkeypatch):
    assert I.cards_for("home") == [] and I.cards_for("chat:nothing") == [] and I.scan_after_turn("x") == []
    assert I.settings_view()["notices"] == []


def test_a_disk_chat_round_trip(home):
    rec = chat("disk1", NOW, "Summarize the Q3 numbers", steps=3, inputs=["Q3_sales.xlsx"], outputs=["sum.pptx"], project="p1")
    write_chat(home / "runs", rec)
    got = I.chat_record("disk1")
    assert got["texts"] == ["Summarize the Q3 numbers"] and got["project_id"] == "p1" and got["steps"] >= 3
    assert "sum.pptx" in got["outputs"] and "Q3_sales.xlsx" in got["inputs"] and "hidden" not in got["texts"][0]
    assert [c["id"] for c in I.load_chats()] == ["disk1"]


def test_heavy_use_stays_within_budget_and_never_repeats_a_dismissed_card(monkeypatch):
    c = heavy_use()
    assert len(c.chats) >= 250
    use(monkeypatch, c)
    cands = I.scan(force=True)
    cand_types = {x["type"] for x in cands if x["type"] != "followup"}
    assert len([x for x in cands if x["type"] != "followup"]) >= 4, cand_types       # more waiting than the budget allows
    seen, dismissed = set(), set()
    for day in range(10):
        now = NOW + day * DAY
        I._SCAN.clear()
        shown = I.cards_for("home", now=now)
        assert len(shown) <= 3
        assert not {x["id"] for x in shown} & dismissed                                 # a dismissed card never returns
        for x in shown:
            I.act(x["id"], "dismissed", now)
            dismissed.add(x["id"])
        seen |= {x["id"] for x in shown}
    assert len(seen) >= 3 and "schedule" not in {x["type"] for x in cands}               # the habit was already scheduled


def test_heavy_use_has_a_card_for_each_planted_pattern(monkeypatch):
    use(monkeypatch, heavy_use())
    got = {x["type"] for x in I.scan(force=True)}
    assert {"project", "connect", "rerun", "teach"} <= got


def test_default_on_matches_the_measured_precision(tmp_path, monkeypatch):
    """Types ship on only when precision on the labelled situations is 0.8 or better (docs/plan/0.0.10-precision.md)."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("insights_precision", Path(__file__).resolve().parents[2] / "testkit" / "insights_precision.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = mod.evaluate()
    for t in I.TYPES:
        assert I.DEFAULT_ON[t] == (res[t]["precision"] >= 0.8), (t, res[t])
        assert res[t]["n"] >= 29
