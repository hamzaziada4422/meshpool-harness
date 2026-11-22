"""Chat one-liners, project briefs and Catch me up: cached by input, rule-based offline, nothing for incognito."""
import json
import time

import pytest

from meshpool_harness import project_store as PS
from meshpool_harness import summaries as S
from meshpool_harness.core.client import MockClient
from tests.summaries_fixtures import chat, setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setenv("MH_LEARN", "0")                    # no model unless a test hands one in
    return tmp_path


def _client(text):
    return MockClient([{"role": "assistant", "content": json.dumps(text)}])


def test_offline_summary_is_the_first_request_trimmed(home):
    chat(home, "c1", ["Summarise the Q3 sales workbook in one slide. Keep it plain please."], ["Done."])
    assert S.summarise_chat("c1") == "Summarise the Q3 sales workbook in one slide."
    assert S.summary_for("c1") == "Summarise the Q3 sales workbook in one slide."
    assert json.loads((home / "home" / "summaries" / "chats.json").read_text())["c1"]["source"] == "rules"


def test_model_summary_is_cached_until_the_chat_changes(home):
    chat(home, "c2", ["Plan the board offsite"], ["Here is a plan."])
    c = _client({"summary": "Planned the board offsite agenda."})
    assert S.summarise_chat("c2", client=c) == "Planned the board offsite agenda."
    assert S.summarise_chat("c2", client=MockClient([])) == "Planned the board offsite agenda."   # no call: cached
    chat(home, "c2", ["Plan the board offsite", "Add a dinner"], ["Here is a plan.", "Added."])
    c2 = _client({"summary": "Planned the offsite with a dinner."})
    assert S.summarise_chat("c2", client=c2) == "Planned the offsite with a dinner."


def test_no_turns_and_incognito_get_no_summary_and_store_nothing(home):
    chat(home, "c3", [], [])
    chat(home, "c4", ["a secret plan"], ["ok"], incognito=True)
    c = MockClient([])                                      # any model call would raise
    assert S.summarise_chat("c3", client=c) == "" and S.summarise_chat("c4", client=c) == ""
    assert c.requests == [] and S.summary_for("c3") == "" and S.summary_for("c4") == ""
    f = home / "home" / "summaries" / "chats.json"
    assert not f.exists() or "secret" not in f.read_text()


def test_a_chat_that_turns_incognito_loses_its_stored_line(home):
    chat(home, "c5", ["Write the invoice report"], ["ok"])
    S.summarise_chat("c5")
    assert S.summary_for("c5")
    chat(home, "c5", ["Write the invoice report"], ["ok"], incognito=True)
    from meshpool_harness.ui import agent_api as A
    A._METAS.clear()
    S.summarise_chat("c5")
    assert S.summary_for("c5") == ""


def test_a_bad_model_answer_falls_back_to_rules(home):
    chat(home, "c6", ["Rename these files by date"], ["ok"])
    assert S.summarise_chat("c6", client=MockClient([{"role": "assistant", "content": "not json"}])) == \
        "Rename these files by date"


def test_dashes_never_reach_a_summary(home):
    chat(home, "c7", ["Make a deck"], ["ok"])
    assert "—" not in S.summarise_chat("c7", client=_client({"summary": "Made a deck — three slides."}))


def test_search_boost_uses_summaries(home):
    chat(home, "c8", ["Quarterly forecast for the Gulf region"], ["ok"])
    chat(home, "c9", ["Birthday party ideas"], ["ok"])
    S.summarise_chat("c8")
    S.summarise_chat("c9")
    b = S.search_boost("gulf forecast")
    assert "c8" in b and "c9" not in b and S.search_boost("") == {}


def test_refresh_idle_summarises_idle_chats_only_once(home):
    chat(home, "c10", ["Draft the supplier email"], ["ok"])
    assert S.refresh_idle(idle_secs=-1) == 1
    assert S.refresh_idle(idle_secs=-1) == 0
    chat(home, "c11", ["Brand new chat"], ["ok"])
    assert S.refresh_idle(idle_secs=3600) == 0               # still warm: left alone


def test_project_brief_rules_and_rebuild_only_on_change(home):
    pid = PS.create("Board deck", description="Quarterly board deck for the GCC business.")["id"]
    chat(home, "p1", ["Build the board deck"], ["We decided to use USD for every number. Next step: confirm the logo."],
         project_id=pid, files=["deck.pptx"])
    chat(home, "p2", ["Any questions on churn?"], [], project_id=pid)
    chat(home, "px", ["Unrelated chat"], ["ok"])
    b = S.project_brief(pid)
    assert b["what"].startswith("Quarterly board deck") and b["chats"] == 2
    assert any("USD" in d for d in b["decisions"])
    assert {t["chat_id"] for t in b["open_threads"]} == {"p1", "p2"}
    assert b["files"] and b["files"][0]["name"] == "deck.pptx" and b["last_activity"]
    again = S.project_brief(pid, client=MockClient([]))     # unchanged: no model call, same brief
    assert again["ts"] == b["ts"]
    chat(home, "p3", ["Add a churn slide"], ["Added."], project_id=pid)
    assert S.project_brief(pid)["chats"] == 3


def test_project_brief_uses_the_model_when_there_is_one_and_skips_incognito(home):
    pid = PS.create("Offsite")["id"]
    chat(home, "q1", ["Plan the offsite"], ["ok"], project_id=pid)
    chat(home, "q2", ["private thing"], ["ok"], project_id=pid, incognito=True)
    c = _client({"what": "Planning the offsite.", "decisions": ["Venue is Dubai"], "open_threads": ["Pick a date"]})
    b = S.project_brief(pid, client=c)
    assert b["source"] == "model" and b["decisions"] == ["Venue is Dubai"] and b["chats"] == 1
    assert "private" not in json.dumps(b)
    assert "private" not in c.requests[0]["messages"][1]["content"]


def test_catch_me_up_covers_the_time_since_the_last_visit(home):
    pid = PS.create("Offsite")["id"]
    now = time.time()
    chat(home, "r1", ["Old chat about venues"], ["ok"], project_id=pid, updated=now - 5000)
    S.touch_opened(pid, ts=now - 3000)
    chat(home, "r2", ["Book the caterer"], ["ok"], project_id=pid, updated=now - 100)
    r = S.catch_me_up(pid)
    assert [i["chat_id"] for i in r["items"]] == ["r2"] and "1 chat moved" in r["text"]
    assert S.last_opened(pid) >= now - 5                      # the visit is recorded
    again = S.catch_me_up(pid)
    assert again["items"] == [] and again["text"] == "Nothing new since you last looked."
    assert [i["chat_id"] for i in S.catch_me_up(pid, since_ts=now - 6000, touch=False)["items"]] == ["r2", "r1"]
