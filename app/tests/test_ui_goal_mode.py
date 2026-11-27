"""Goal mode in the web app (goal.js): the goal card above the thread in each state, the rubric editor, the restart
countdown with Cancel, and one goal run end to end: /goal, the rubric, work, a check below the bar, more work, met.
Real browser, real app server, the mock model (testkit/mock_llm.py: "goal demo")."""
from __future__ import annotations

import json
import time

import pytest

from test_ui_ws19 import app, make_page  # noqa: F401  (the fixtures)


def _state(**kw):
    rubric = [{"id": "c1", "text": "plan.md exists", "check": "open it", "weight": 2, "must": True, "needs_user": False},
              {"id": "c2", "text": "plan.md has the launch date and owner", "check": "read it", "weight": 3, "must": True,
               "needs_user": False},
              {"id": "c3", "text": "plan.md lists three risks", "check": "count", "weight": 2, "must": False,
               "needs_user": False},
              {"id": "c4", "text": "No placeholder text", "check": "search", "weight": 1, "must": False,
               "needs_user": False}]
    now = time.time()
    st = {"goal": "Write the launch plan until it is a 10/10", "mode": "chat", "bar": 10, "status": "working",
          "reason": "", "question": "", "rubric": rubric, "rubric_hash": "ab" * 32, "frozen_at": now - 60,
          "draft_until": 0, "started": now - 60, "ended": 0, "turns": 3, "checks": 2, "spent": 0.42, "elapsed": 725,
          "budgets": {"usd": 2.0, "hours": 4, "turns": 30, "checks": 10}, "resume_at": 0, "now": now,
          "history": [{"n": 1, "score": 4.4, "error": False}, {"n": 2, "score": 7.5, "error": False}],
          "findings": {"items": [{"id": "c1", "status": "met", "evidence": "plan.md is there"},
                                 {"id": "c2", "status": "not_met", "evidence": "no owner yet"},
                                 {"id": "c3", "status": "met", "evidence": "3 risks"},
                                 {"id": "c4", "status": "unverified", "evidence": "could not search"}]}}
    st.update(kw)
    return st


def _show(pg, st):
    pg.evaluate("""st => { S.cur = {id: 'fake-goal-chat', view: {id: 'fake-goal-chat', state: 'idle', goal_state: st}, q: []};
                           renderGoal010(S.cur); }""", st)


def test_the_goal_card_shows_the_goal_the_score_and_each_criterion(make_page):  # noqa: F811
    pg = make_page()
    _show(pg, _state())
    card = pg.locator("#goalCard")
    assert card.count() == 1
    text = card.inner_text()
    assert "Write the launch plan until it is a 10/10" in text and "Working" in text
    assert "7.5/10" in text.replace("\n", "") and "bar 10" in text
    assert "4.4 → 7.5" in text and "Turn 3 of 30" in text and "2 of 10 checks" in text and "$0.42 of $2.00" in text
    assert "12 min" in text
    pg.click("#goalCard .gc-crit summary")
    rows = pg.eval_on_selector_all("#goalCard .gc-list li", "els => els.map(e => [e.className, e.innerText])")
    assert [r[0] for r in rows] == ["gc-met", "gc-not_met", "gc-met", "gc-unverified"]
    assert rows[1][1].startswith("Not met") and "must" in rows[1][1] and rows[3][1].startswith("Could not check")
    # the card sits above the thread, not inside it
    assert pg.evaluate("document.querySelector('#goalCard').nextElementSibling.id") == "conv"
    assert pg.evaluate("!document.querySelector('#col #goalCard')")
    assert [b.inner_text() for b in card.locator(".gc-acts button").all()] == ["End goal"]
    assert not pg.errors


@pytest.mark.parametrize("kw,state,buttons", [
    ({"status": "checking"}, "Checking", ["End goal"]),
    ({"status": "paused", "reason": "it needs you", "question": "Which launch date should it use?"},
     "Paused: it needs you", ["Resume", "End goal"]),
    ({"status": "met", "history": [{"n": 1, "score": 4.4}, {"n": 2, "score": 10}]}, "Met", ["Close"]),
    ({"status": "stopped", "reason": "budget"}, "Stopped at the budget", ["Close"]),
    ({"status": "stopped", "reason": "stuck: 2 turns in a row without doing anything"},
     "Stopped: stuck: 2 turns in a row without doing anything", ["Resume", "Close"]),
])
def test_the_goal_card_states(make_page, kw, state, buttons):  # noqa: F811
    pg = make_page()
    _show(pg, _state(**kw))
    assert pg.inner_text("#goalCard .gc-state") == state
    assert [b.inner_text() for b in pg.locator("#goalCard .gc-acts button").all()] == buttons
    if kw.get("question"):
        assert pg.inner_text("#goalCard .gc-q") == kw["question"]
    if kw["status"] in ("met", "stopped", "paused"):
        assert pg.evaluate("document.querySelector('#goalCard .gc-crit').open")
    pg.evaluate("renderGoal010(null)")
    assert pg.locator("#goalCard").count() == 0                 # no chat open: no card


def test_the_rubric_is_open_for_edits_with_a_countdown_and_typing_is_never_redrawn(make_page):  # noqa: F811
    pg = make_page()
    st = _state(status="review", rubric_hash="", frozen_at=0, draft_until=time.time() + 47, history=[], findings={},
                turns=0, checks=0)
    _show(pg, st)
    assert "Rubric open for edits" in pg.inner_text("#goalCard .gc-state")
    assert pg.locator("#goalCard .gc-edit li").count() == 4
    cd = pg.inner_text("#goalCard .gc-cd")
    assert cd.startswith("in ") and 40 <= int(cd.split()[1]) <= 47
    pg.fill("#goalCard .gc-edit li:nth-child(2) .gc-in", "plan.md has the launch date, owner and budget")
    _show(pg, dict(st, now=time.time()))                        # a poll arrives while the user types
    assert pg.input_value("#goalCard .gc-edit li:nth-child(2) .gc-in") == "plan.md has the launch date, owner and budget"
    pg.click("#goalCard .gc-add")
    pg.fill("#goalCard .gc-edit li:nth-child(5) .gc-in", "The plan fits on one page")
    pg.check("#goalCard .gc-edit li:nth-child(5) .gc-mustc")
    sent = []

    def answer(route):
        sent.append(route.request.post_data)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"goal": "x", "goal_state": None}))
    pg.route("**/api/agent/fake-goal-chat/goal", answer)
    pg.click("#goalCard [data-gc=start]")
    pg.wait_for_function("() => !document.querySelector('#goalCard')")
    req = json.loads(sent[-1])
    assert req["op"] == "start"
    rub = req["rubric"]
    assert rub[1]["text"] == "plan.md has the launch date, owner and budget"
    assert rub[4] == {"text": "The plan fits on one page", "check": "", "must": True, "weight": 1}


def test_after_a_restart_the_goal_resumes_after_a_countdown_with_a_cancel_button(make_page):  # noqa: F811
    pg = make_page()
    _show(pg, _state(resume_at=time.time() + 23))
    rs = pg.inner_text("#goalCard .gc-rs")
    assert rs.startswith("Resuming the goal in ") and 20 <= int(rs.split()[4]) <= 23
    assert [b.inner_text() for b in pg.locator("#goalCard .gc-acts button").all()] == ["Cancel", "End goal"]
    time.sleep(1.2)
    assert pg.inner_text("#goalCard .gc-rs") != rs              # it counts down


def test_the_goal_card_fits_a_phone_screen(make_page):  # noqa: F811
    pg = make_page(width=390, height=800)
    _show(pg, _state(status="paused", reason="it needs you", question="Which launch date should it use?"))
    over = pg.evaluate("() => { const c = document.querySelector('#goalCard'); return c.scrollWidth - c.clientWidth; }")
    assert over <= 1
    box = pg.locator("#goalCard").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390


def test_a_goal_check_line_in_the_thread_survives_a_reload(make_page):  # noqa: F811
    pg = make_page()
    pg.evaluate("""() => { S.cur = {id: 'x', view: {id: 'x', state: 'idle'}, q: [], turn: 0}; applyEv({type: 'goal_check', n: 1,
        score: 4.4, bar: 10, met: false, text: 'Check 1: 4.4/10. Below the bar of 10. Not met: plan.md has the owner.'}); }""")
    assert "Check 1: 4.4/10. Below the bar of 10." in pg.inner_text("#col .note.goalcheck")
    pg.evaluate("applyEv({type: 'status', goal: true, text: 'Rubric frozen: 4 criteria.'})")
    assert "Rubric frozen" not in pg.inner_text("#col")          # the card says it; the thread stays quiet
    pg.evaluate("applyEv({type: 'assistant', text: 'Finished plan.md.\\nGOAL: READY'})")
    assert "GOAL: READY" not in pg.inner_text("#col")


def test_a_goal_end_to_end_rubric_work_check_below_the_bar_more_work_met(make_page, app):  # noqa: F811
    pg = make_page()
    pg.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["proj"])
    pg.fill("#input", "/goal goal demo: write the launch plan in plan.md until it is a 10/10")
    pg.press("#input", "Enter")
    pg.wait_for_function("() => document.querySelector('#goalCard .gc-edit li')", timeout=60000)
    assert pg.locator("#goalCard .gc-edit li").count() == 4
    assert pg.locator("#col .msg-user").count() == 0             # the goal is not sent as a message before the freeze
    pg.click("#goalCard [data-gc=start]")
    try:
        pg.wait_for_function("() => S.cur && S.cur.view && S.cur.view.goal_state && S.cur.view.goal_state.status === 'met'",
                             timeout=120000)
    except Exception:  # noqa: BLE001
        pytest.fail("the goal did not reach met\n" + app["log"].read_text(errors="replace")[-3000:])
    pg.wait_for_function("() => document.querySelectorAll('#col .note.goalcheck').length === 2", timeout=30000)
    lines = pg.eval_on_selector_all("#col .note.goalcheck", "els => els.map(e => e.innerText)")
    assert "Below the bar of 10" in lines[0] and "It meets the bar" in lines[1]
    card = pg.inner_text("#goalCard")
    assert "Met" in card and "10/10" in card.replace("\n", "") and "→ 10" in card
    pg.wait_for_function("() => document.body.innerText.includes('Met: 10/10 after 2 checks.')", timeout=30000)
    cid = pg.evaluate("S.cur.id")
    pg.reload()
    pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
    pg.evaluate("id => openThread(id)", cid)
    pg.wait_for_function("() => document.querySelectorAll('#col .note.goalcheck').length === 2", timeout=30000)
    pg.wait_for_function("() => document.querySelector('#goalCard') && document.querySelector('#goalCard').innerText.includes('Met')",
                         timeout=30000)
    assert not pg.errors
