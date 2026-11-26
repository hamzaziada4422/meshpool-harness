"""The independent reviewer in the look step (agent/lookreview.py): when a turn made a page or a file, a separate model
call with a fresh context takes the seat of the person who would really use it, may ask meshpool to try up to four
actions in the same browser, gives a verdict, and the builder fixes what it found.

No network and no paid model: the builder and the reviewer are scripts behind Agent._post, or testkit/mock_llm.py on
localhost. The web tests use the headless Chromium meshpool already uses and are skipped without one."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness.agent import core, look as L, lookreview as LR  # noqa: E402
from tests.test_look_review import (ASK, BUGGY, CONSOLE, FIXED, OVER, _chrome_children, _shots, agent, call,  # noqa: E402
                                    fake_render, needs_browser, overwrite, reviews, say, text_of, write)

PERSONA = "a shopper on her phone looking for fresh bouquets at the Petal flower shop"
SECRET = "BUILDER-ONLY-MARKER-91c4"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_LOOK_REVIEW", "1")
    monkeypatch.delenv("MH_LOOK_REVIEWER", raising=False)
    monkeypatch.setenv("MH_GRADER", "0")
    monkeypatch.delenv("MH_VISION", raising=False)
    C.ensure_home()


@pytest.fixture()
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def _is_reviewer(body) -> bool:
    return str(body["messages"][0].get("content", "")).startswith("You are a reviewer. You did not build")


def verdict(problems=(), ok=None, persona=PERSONA, can=None):
    ok = (not problems) if ok is None else ok
    return {"persona": persona, "can_do_the_job": ok if can is None else can, "pass": ok,
            "problems": [dict(zip(("what", "where", "severity"), p)) for p in problems]}


class Both:
    """A scripted builder and a scripted reviewer behind Agent._post. The reviewer is told apart by its instructions."""
    def __init__(self, monkeypatch, builder, reviewer):
        self.builder, self.reviewer = list(builder), list(reviewer)
        self.b_bodies, self.r_bodies = [], []
        me = self

        def fake(self_, base, key, body, stream):
            if _is_reviewer(body):
                me.r_bodies.append(body)
                nxt = me.reviewer.pop(0) if me.reviewer else "{}"
                msg = say(nxt if isinstance(nxt, str) else json.dumps(nxt))
            else:
                me.b_bodies.append(body)
                nxt = me.builder.pop(0) if me.builder else say("Done.")
                if callable(nxt):
                    nxt = nxt({t["function"]["name"] for t in body.get("tools") or []})
                msg = say(nxt) if isinstance(nxt, str) else nxt
            return {"choices": [{"message": msg, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, False
        monkeypatch.setattr(core.Agent, "_post", fake)


def body_text(body) -> str:
    out = []
    for m in body["messages"]:
        c = m.get("content")
        out.append(c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict)))
    return "\n".join(out)


def n_images(body) -> int:
    return sum(p.get("type") == "image_url" for m in body["messages"] if isinstance(m.get("content"), list)
               for p in m["content"])


def two_rounds(monkeypatch, tmp_path, reviewer, render=None, actions_fn=None, builder=None):
    """A builder that writes a page, is sent back once with the verdict, fixes it; a reviewer that follows its script."""
    render = render or fake_render([[OVER, CONSOLE], []], _shots(tmp_path))
    monkeypatch.setattr(L, "render_web", render)
    both = Both(monkeypatch, builder or [write("index.html", BUGGY), f"Made it. {SECRET}",
                                         overwrite("index.html", FIXED), "Fixed it. The page is index.html."], reviewer)
    return both, render


# ---------- a fresh context ----------------------------------------------------------------------------------------------
def test_reviewer_call_has_a_fresh_context_and_none_of_the_builders(proj, tmp_path, monkeypatch):
    both, _ = two_rounds(monkeypatch, tmp_path, [{"persona": PERSONA, "actions": []},
                                                  verdict([("the page scrolls sideways", "banner", "blocker")]),
                                                  verdict()])
    a, _ = agent(proj)
    a.send(ASK)
    sys_prompt = a.messages[0]["content"]
    assert len(both.r_bodies) == 3 and len(sys_prompt) > 500
    for b in both.r_bodies:
        assert [m["role"] for m in b["messages"]] == ["system", "user"]       # no history at all
        assert not b.get("tools") and b["messages"][0]["content"] == LR.SYSTEM
        txt = body_text(b)
        assert SECRET not in txt and sys_prompt[:300] not in txt
        assert "Made it." not in txt and "write_file" not in txt and "[meshpool] Before you call" not in txt
        assert ASK in txt                                                       # the user's request is given
    assert both.r_bodies[0]["model"] == both.b_bodies[0]["model"]              # the chat's own model and endpoint


def test_reviewer_text_never_shows_as_an_assistant_reply(proj, tmp_path, monkeypatch):
    both, _ = two_rounds(monkeypatch, tmp_path, [{"persona": PERSONA, "actions": []},
                                                  verdict([("the page scrolls sideways", "banner", "blocker")]),
                                                  verdict()])
    a, ev = agent(proj)
    final = a.send(ASK)
    shown = [e.get("text", "") for e in ev if e["type"] == "assistant"]
    assert shown and not any("can_do_the_job" in t or '"problems"' in t or '"actions"' in t for t in shown), shown
    assert not any("can_do_the_job" in text_of(m) for m in a.messages if m["role"] == "assistant")
    assert "can_do_the_job" not in final


# ---------- who it is, and what it asks to see --------------------------------------------------------------------------
def test_persona_is_derived_kept_for_round_two_and_shown_in_the_note(proj, tmp_path, monkeypatch):
    both, _ = two_rounds(monkeypatch, tmp_path, [{"persona": PERSONA, "actions": []},
                                                  verdict([("the page scrolls sideways", "banner", "blocker")]),
                                                  verdict()])
    a, ev = agent(proj)
    final = a.send(ASK)
    notes = [e for e in ev if e["type"] == "status" and e.get("persona")]
    assert [n["persona"] for n in notes] == [PERSONA] and notes[0]["look"] and notes[0]["text"].startswith("Checking it as: " + PERSONA)
    rv = reviews(a)
    assert len(rv) == 1 and PERSONA in text_of(rv[0])                          # the builder is told who looked
    assert f"You are: {PERSONA}" in body_text(both.r_bodies[1]) and f"You are: {PERSONA}" in body_text(both.r_bodies[2])
    assert "First name the person" not in body_text(both.r_bodies[2])          # the persona string is kept, not re-derived
    assert len(both.r_bodies) == 3                                              # plan + verdict, then verdict again
    assert final.rstrip().endswith("Checked: " + PERSONA + " tried it; fixed the page scrolls sideways.")


def test_action_requests_run_and_their_results_go_back_to_the_reviewer(proj, tmp_path, monkeypatch):
    asked = []

    def actions_fn(hold, target, actions, out_dir, tag):
        asked.append(actions)
        return [{"action": LR.action_text(actions[0]), "ok": False, "note": "nothing visibly changed", "shot": None},
                {"action": LR.action_text(actions[1]), "ok": True, "note": "new on the page: Thanks, we got it", "shot": None}]
    plan = {"persona": PERSONA, "actions": [{"do": "click", "target": "Order now", "width": "phone"},
                                            {"do": "fill", "target": "email", "text": "x", "submit": True}] +
            [{"do": "look_at", "target": f"part {i}"} for i in range(5)]}
    both, _ = two_rounds(monkeypatch, tmp_path, [plan, verdict([("Order now does nothing", "hero", "blocker")]), verdict()])
    a, _ = agent(proj)
    monkeypatch.setattr(LR, "default_actions", actions_fn)
    a.send(ASK)
    assert len(asked) == 1 and len(asked[0]) == LR.MAX_ACTIONS == 4            # at most four, one chance
    assert asked[0][0] == {"do": "click", "target": "Order now", "text": "", "submit": False, "width": "phone"}
    assert asked[0][1]["do"] == "fill" and asked[0][1]["text"] == "x" and asked[0][1]["submit"] is True
    final_call = body_text(both.r_bodies[1])
    assert "click 'Order now' at phone width" in final_call and "DID NOT WORK" in final_call
    assert "nothing visibly changed" in final_call and "Thanks, we got it" in final_call
    assert "actions: up to" not in final_call and "actions: up to" in body_text(both.r_bodies[0])
    assert "Order now" in text_of(reviews(a)[0]) and "nothing visibly changed" in text_of(reviews(a)[0])


@needs_browser
def test_actions_run_in_the_browser_that_drew_the_pictures_and_it_is_closed_after(proj, tmp_path, monkeypatch):
    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            "content=\"width=device-width\"><title>Cakes</title></head><body><main><h1>Sweet Crumb bakery</h1>"
            "<button id=\"o\" onclick=\"document.getElementById('m').textContent='Thanks, your cake is ordered'\">"
            "Order now</button><p id=\"m\"></p><form onsubmit=\"event.preventDefault();"
            "document.getElementById('m').textContent='Sent '+this.email.value\"><label>Email "
            "<input name=\"email\" type=\"email\"></label><button type=\"submit\">Send</button></form>"
            "<button id=\"d\">Delivery date</button></main></body></html>\n")
    plan = {"persona": "a customer on her phone ordering a birthday cake from the bakery",
            "actions": ["click 'Order now' at phone width", "fill the email field with 'a@b.co' and submit",
                        {"do": "click", "target": "Delivery date"}, {"do": "click", "target": "Nope"}]}
    both = Both(monkeypatch, [write("index.html", page), "Made index.html."], [plan, verdict()])
    a, _ = agent(proj)
    a.send("make a one-page site for a small bakery with an order button")
    sent = body_text(both.r_bodies[1])
    assert "ok: click 'Order now' at phone width -> new on the page: Thanks, your cake is ordered" in sent, sent
    assert "Sent a@b.co" in sent
    assert "DID NOT WORK: click 'Delivery date' -> nothing visibly changed" in sent
    assert "could not find 'Nope'" in sent
    assert n_images(both.r_bodies[1]) >= 4                                      # page pictures and the action pictures
    assert not _chrome_children()                                               # one browser, closed after


@needs_browser
def test_look_at_tells_a_reviewer_with_no_picture_what_is_there(proj, tmp_path, monkeypatch):
    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            "content=\"width=device-width\"><title>Cakes</title></head><body><main><h1>Sweet Crumb</h1>"
            "<section id=\"hours\"><h2>Hours</h2><p>Mon to Sat 7am to 4pm</p></section></main></body></html>\n")
    plan = {"persona": PERSONA, "actions": [{"do": "look_at", "target": "Hours", "width": "phone"}]}
    both = Both(monkeypatch, [write("index.html", page), "Made it."], [plan, verdict()])
    a, _ = agent(proj)
    from meshpool_harness import providers as PV
    a._no_vision_models = {PV.split(a.current_model())[1]}
    a.send("make a page for the bakery with its hours")
    assert "it shows: Hours Mon to Sat 7am to 4pm" in body_text(both.r_bodies[1])
    assert not _chrome_children()


def test_a_missing_browser_is_said_not_hidden(proj, tmp_path, monkeypatch):
    plan = {"persona": PERSONA, "actions": [{"do": "click", "target": "Order now"}]}
    both, _ = two_rounds(monkeypatch, tmp_path, [plan, verdict(), ])       # a fake render: no browser is held open
    a, _ = agent(proj)
    a.send(ASK)
    assert "not tried: the browser was not available" in body_text(both.r_bodies[1])


# what a Claude model behind the adapter really sent in the live run: the plan reply carries extra keys and "problem"
# instead of "what", and the verdict reply has a stray "]" before "pass" (it used to make the whole review fall back)
RECORDED_PLAN = ('{"persona": "A customer on her phone, looking at the bakery\'s menu and placing a pickup order.", '
                 '"pass": false, "problems": [{"problem": "no confirmation after placing an order", "severity": "blocker"}], '
                 '"actions": [{"do": "look_at", "target": "Menu", "width": "phone"}, {"do": "look_at", "target": "Pickup date", '
                 '"width": "phone"}]}')
RECORDED_VERDICT = ('{"persona": "A customer on her phone, looking at the bakery\'s menu and placing a pickup order", '
                    '"can_do_the_job": false, "problems": [{"what": "no confirmation is shown after the order form is sent", '
                    '"where": "Place an Order form", "severity": "blocker"}, {"what": "no visible keyboard focus on the '
                    'Pickup date field", "where": "order form", "severity": "major"}, {"what": "the phone layout was not '
                    'confirmed", "where": "phone width", "severity": "minor"}]], "pass": false}')


def test_replies_recorded_from_the_adapter_are_read():
    d = LR.parse_json(RECORDED_VERDICT)
    assert d and d["pass"] is False and len(d["problems"]) == 3                 # the stray "]" is left out
    v = LR.normalize_verdict(d, "make a bakery site", "web", "x")
    assert [p["severity"] for p in v["problems"]] == ["blocker", "major", "minor"] and not v["pass"]
    plan = LR.parse_json(RECORDED_PLAN)
    assert [a["do"] for a in LR.plan_actions(plan["actions"])] == ["look_at", "look_at"]
    one = LR.normalize_verdict(plan, "make a bakery site", "web", "x")
    assert one["problems"][0]["what"] == "no confirmation after placing an order"   # "problem" is read as "what"
    assert LR.parse_json('{"a": "x]]", "b": [1, 2]]}') == {"a": "x]]", "b": [1, 2]}   # brackets inside text are kept


def test_a_recorded_verdict_with_a_stray_bracket_no_longer_ends_the_review(proj, tmp_path, monkeypatch):
    both, _ = two_rounds(monkeypatch, tmp_path, [RECORDED_PLAN, RECORDED_VERDICT, verdict(persona="A customer on her phone, "
                         "looking at the bakery's menu and placing a pickup order")])
    a, _ = agent(proj)
    final = a.send(ASK)
    t = text_of(reviews(a)[0])
    assert "[blocker] no confirmation is shown after the order form is sent" in t and "measured only" not in final
    assert final.rstrip().endswith("fixed no confirmation is shown after the order form is sent, no visible keyboard "
                                   "focus on the Pickup date field and the phone layout was not confirmed.")


# ---------- when it stops -----------------------------------------------------------------------------------------------
def test_a_pass_ends_the_loop_with_no_second_model_loop(proj, tmp_path, monkeypatch):
    render = fake_render([[]], _shots(tmp_path))
    monkeypatch.setattr(L, "render_web", render)
    both = Both(monkeypatch, [write("index.html", FIXED), "Made it."], [{"persona": PERSONA, "actions": []}, verdict()])
    a, _ = agent(proj)
    final = a.send(ASK)
    assert reviews(a) == [] and len(both.b_bodies) == 2 and len(render.calls) == 1
    assert len(both.r_bodies) == 2
    assert final.rstrip().endswith("Checked: " + PERSONA + " tried it; no problems found."), final


def test_at_most_two_rounds_when_the_reviewer_never_passes(proj, tmp_path, monkeypatch):
    bad = verdict([("still scrolls sideways", "banner", "blocker")])
    render = fake_render([[OVER]], _shots(tmp_path))
    monkeypatch.setattr(L, "render_web", render)
    page = [BUGGY + f"<!-- {i} -->\n" for i in range(5)]
    both = Both(monkeypatch, [write("index.html", page[0]), "Made it.", overwrite("index.html", page[1], "w2"), "Tried.",
                              overwrite("index.html", page[2], "w3"), "Tried again.", overwrite("index.html", page[3], "w4"),
                              "And again."], [{"persona": PERSONA, "actions": []}, bad, bad, bad, bad])
    a, _ = agent(proj)
    final = a.send(ASK)
    assert len(reviews(a)) == 2 and len(both.r_bodies) == 3                    # plan, verdict, verdict: no third verdict
    assert len(render.calls) == 3
    assert "still open: still scrolls sideways" in final and PERSONA in final, final


def test_blockers_reach_the_builder_first_with_the_persona(proj, tmp_path, monkeypatch):
    v = verdict([("the logo is a little small", "header", "minor"), ("I cannot send the order", "order form", "blocker"),
                 ("the prices are hard to read", "menu", "major")])
    both, _ = two_rounds(monkeypatch, tmp_path, [{"persona": PERSONA, "actions": []}, v, verdict()])
    a, _ = agent(proj)
    a.send(ASK)
    t = text_of(reviews(a)[0])
    assert t.index("[blocker] I cannot send the order") < t.index("[major] the prices") < t.index("[minor] the logo")
    assert "(where: order form)" in t and PERSONA in t and "could they do what they came to do: no" in t
    assert "sideways" in t                                                      # the measured findings are still there
    assert "explicit choices win" in t and "Does it do what was asked" not in t   # the reviewer replaced the rubric
    assert "look at what you made" in t and t.startswith("[meshpool]")


def test_explicit_user_choices_win_over_the_reviewer(proj, tmp_path, monkeypatch):
    ask = ASK + ". the 'Buy now' button is a placeholder and does nothing yet"
    render = fake_render([[]], _shots(tmp_path))
    monkeypatch.setattr(L, "render_web", render)
    both = Both(monkeypatch, [write("index.html", FIXED), "Made it."],
                [{"persona": PERSONA, "actions": []},
                 verdict([("The 'Buy now' button does nothing when clicked", "hero", "blocker")], ok=False)])
    a, _ = agent(proj)
    final = a.send(ask)
    assert reviews(a) == [] and "no problems found" in final                    # the placeholder is not a problem


# ---------- cost: the receipt counts the extra calls --------------------------------------------------------------------
def test_receipt_counts_the_reviewers_calls_and_tokens(proj, tmp_path, monkeypatch):
    both, _ = two_rounds(monkeypatch, tmp_path, [{"persona": PERSONA, "actions": []},
                                                  verdict([("the page scrolls sideways", "banner", "blocker")]),
                                                  verdict()])
    a, ev = agent(proj)
    a.send(ASK)
    end = [e for e in ev if e["type"] == "turn_end"][-1]
    builder_calls, reviewer_calls = len(both.b_bodies), len(both.r_bodies)
    assert reviewer_calls == 3
    assert end["turn"]["calls"] == builder_calls + reviewer_calls
    assert end["turn"]["in"] == 100 * (builder_calls + reviewer_calls) and end["turn"]["out"] == 10 * (builder_calls + reviewer_calls)
    looks = [e for e in ev if e["type"] == "usage" and e.get("source") == "look"]
    assert len(looks) == 3 and all(e["aux"] for e in looks)                    # shown as side calls, never the chat's context
    stats = a.last_look["reviewer"]
    assert stats["calls"] == 3 and stats["in"] == 300 and stats["out"] == 30 and stats["secs"] >= 0


# ---------- it can fail without hurting the turn -------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["I think it looks fine to me.", "{}", '{"persona": "", "pass": true}',
                                 '{"hello": 1}'])
def test_a_bad_reviewer_reply_falls_back_to_the_measured_findings(proj, tmp_path, monkeypatch, bad):
    both, _ = two_rounds(monkeypatch, tmp_path, [bad, bad, bad])
    a, ev = agent(proj)
    final = a.send(ASK)
    rv = reviews(a)
    assert len(rv) == 1
    t = text_of(rv[0])
    assert "sideways" in t and "initGallery" in t and "Does it do what was asked" in t   # today's message
    assert "separate reviewer" not in t
    assert "Checked: measured only" in final, final
    assert not any(e.get("persona") for e in ev if e["type"] == "status")
    assert len(both.r_bodies) == 1                                              # it does not keep asking a reviewer that failed


def test_a_failing_reviewer_call_falls_back_too(proj, tmp_path, monkeypatch):
    render = fake_render([[OVER, CONSOLE], []], _shots(tmp_path))
    monkeypatch.setattr(L, "render_web", render)
    Both(monkeypatch, [write("index.html", BUGGY), "Made it.", overwrite("index.html", FIXED), "Fixed."], [])
    real = core.Agent._post

    def post(self, base, key, body, stream, tok=None):
        if _is_reviewer(body):
            raise RuntimeError("HTTP 500: boom")
        return real(self, base, key, body, stream)
    monkeypatch.setattr(core.Agent, "_post", post)
    a, _ = agent(proj)
    final = a.send(ASK)
    assert len(reviews(a)) == 1 and "Checked: measured only" in final, final


# ---------- images, files, switches -------------------------------------------------------------------------------------
def test_pictures_go_to_a_model_that_takes_images_and_not_to_a_text_only_one(proj, tmp_path, monkeypatch):
    both, _ = two_rounds(monkeypatch, tmp_path, [{"persona": PERSONA, "actions": []}, verdict(), ])
    a, _ = agent(proj)
    a.send(ASK)
    assert n_images(both.r_bodies[0]) == 2 and "Pictures attached" in body_text(both.r_bodies[0])
    assert n_images(both.r_bodies[1]) == 2


def test_text_only_models_get_no_images(proj, tmp_path, monkeypatch):
    both, _ = two_rounds(monkeypatch, tmp_path, [{"persona": PERSONA, "actions": []}, verdict(), ])
    a, _ = agent(proj)
    from meshpool_harness import providers as PV
    a._no_vision_models = {PV.split(a.current_model())[1]}
    a.send(ASK)
    assert len(both.r_bodies) == 2
    for b in both.r_bodies:
        assert n_images(b) == 0 and all(isinstance(m["content"], str) for m in b["messages"])
        assert "cannot see pictures" in body_text(b) and "sideways" in body_text(b)   # findings and the log only


def test_a_docx_gets_a_reader_persona_and_no_action_requests(proj, tmp_path, monkeypatch):
    memo = "a finance director reading this memo before a board meeting"
    seen = []

    def render_doc(path, out_dir, tag="r1"):
        seen.append(path)
        return {"findings": [], "shots": _shots(tmp_path)[:1], "text": "[page 1 of 1]\nSpend report\nCache hit 82%"}
    monkeypatch.setattr(L, "render_doc", render_doc)
    script = ("import docx; d = docx.Document(); d.add_heading('Spend report', 1); d.add_paragraph('Cache hit 82%'); "
              "d.save('spend.docx')")

    def make(cid):
        def pick(names):
            if "exec_command" in names:
                return call("exec_command", {"cmd": f"python3 -c \"{script}\""}, cid)
            return call("bash" if "bash" in names else "shell",
                        {"command": f"python3 -c \"{script}\""} if "bash" in names else
                        {"command": ["bash", "-lc", f"python3 -c \"{script}\""]}, cid)
        return pick
    both = Both(monkeypatch, [make("m1"), "Saved spend.docx.", make("m2"), "Added the date."],
                [verdict([("the cache hit rate has no date", "page 1", "major")], persona=memo, ok=False),
                 verdict(persona=memo)])
    a, _ = agent(proj)
    final = a.send("write a word memo with the spend report for the board")
    assert (proj / "spend.docx").exists() and seen
    first = body_text(both.r_bodies[0])
    assert "First name the person who would use this" in first and "actions: up to" not in first
    assert "This is a file, not a page" in first and "Spend report" in first
    assert n_images(both.r_bodies[0]) == 1
    t = text_of(reviews(a)[0])
    assert memo in t and "[major] the cache hit rate has no date" in t
    assert f"You are: {memo}" in body_text(both.r_bodies[1])
    assert "Checked: " + memo + " looked it over; fixed the cache hit rate has no date" in final, final


def test_engine_qa_that_drew_the_pages_still_skips_the_look_and_the_reviewer(proj, monkeypatch):
    from meshpool_harness.agent import look
    from docx import Document
    d = Document()
    d.add_paragraph("hello")
    d.save(proj / "deck.docx")

    class A:
        root = str(proj)
        trace = [{"tool": "bash", "ok": True}, {"tool": "mcp__meshpool-office__check_deck", "ok": True}]
        ws = type("W", (), {"commands": []})()
    import time
    assert look.doc_target(A(), {"t0": time.time() - 5, "trace0": 0}, [proj / "deck.docx"]) is None


def test_off_switches(proj, tmp_path, monkeypatch):
    for k, v in (("MH_LOOK_REVIEW", "0"), ("MH_LOOK_REVIEWER", "0")):
        monkeypatch.setenv("MH_LOOK_REVIEW", "1")
        monkeypatch.delenv("MH_LOOK_REVIEWER", raising=False)
        monkeypatch.setenv(k, v)
        render = fake_render([[OVER]], _shots(tmp_path))
        monkeypatch.setattr(L, "render_web", render)
        both = Both(monkeypatch, [write("index.html", BUGGY), "Made it.", "Right as it is."], [])
        a, _ = agent(proj)
        final = a.send(ASK)
        assert both.r_bodies == [], k                                           # no reviewer call at all
        if k == "MH_LOOK_REVIEW":
            assert render.calls == [] and "Checked" not in final
        else:
            assert len(reviews(a)) == 1 and "Does it do what was asked" in text_of(reviews(a)[0])
            assert "Checked: looked at the page" in final
        (proj / "index.html").unlink()


# ---------- reading the reviewer -----------------------------------------------------------------------------------------
def test_parse_and_normalise():
    d = LR.parse_json('Sure.\n```json\n{"persona": "a nurse", "pass": true, "problems": [{"what": "x {y}", "severity": "critical"}]}\n```')
    v = LR.normalize_verdict(d, "make a rota", "doc")
    assert v["problems"][0]["severity"] == "blocker"                            # an alias is read
    assert v["pass"] is False and v["can_do_the_job"] is False                  # a blocker cannot pass
    assert LR.normalize_verdict({"persona": "a nurse", "pass": True, "problems": [{"what": "ugly", "severity": "minor"}]},
                                "r", "doc")["pass"] is True
    assert LR.parse_json("no json here") is None and LR.parse_json("{broken") is None
    assert LR.normalize_verdict({"pass": True}, "r", "doc") is None             # no persona: not a verdict
    assert LR.normalize_verdict({"persona": "a nurse", "pass": True, "problems": [
        {"what": "a", "severity": "major"}]}, "r", "doc")["pass"] is False


def test_the_persona_is_the_end_user_never_the_requester():
    p, replaced = LR.clean_persona("the person who asked for the page", "make a bakery page", "web")
    assert replaced and p == "a first-time visitor to this page"
    p, replaced = LR.clean_persona("the user", "make a memo", "doc")
    assert replaced and p == "someone opening this file for the first time"
    p, replaced = LR.clean_persona("a customer on her phone", "make a bakery page", "web")
    assert (p, replaced) == ("a customer on her phone", False)
    p, replaced = LR.clean_persona("the owner checking sales", "make a dashboard for me, I will use it daily", "web")
    assert replaced is False                                                    # the request says the writer is the user


def test_the_reviewers_instructions_name_the_rules():
    s = LR.SYSTEM
    assert "You did not build" in s and "never the person who wrote the request" in s
    assert "request's own choices win" in s and '"blocker"' in s and "one JSON object" in s
    assert "Step 1" in s and "Step 2" in s and "Do not judge as a designer or a developer" in s
    assert "—" not in s and "–" not in s                                  # plain punctuation


def test_action_sentences_are_read():
    assert [a["do"] for a in LR.plan_actions([{"do": "tap", "target": "Buy"}, "dance wildly"])] == ["click"]
    acts = LR.plan_actions(["click 'Order now'", "fill the email field with 'x' and submit", "open the menu at phone width",
                            "look at the footer", "dance wildly"])
    assert [a["do"] for a in acts] == ["click", "fill", "open_menu", "look_at"]
    assert acts[0]["target"] == "Order now" and acts[1]["text"] == "x" and acts[1]["submit"] and acts[1]["target"] == "email"
    assert acts[2]["width"] == "phone"


# ---------- the same on the mock model server ------------------------------------------------------------------------------
@pytest.fixture()
def mock_server(tmp_path, monkeypatch):
    import socket
    import subprocess
    import time
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    log = tmp_path / "mock.jsonl"
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[2] / "testkit" / "mock_llm.py"), str(port), str(log)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    C.write({"MESHPOOL_API_KEY": "test-key-1234", "MESHPOOL_BASE_URL": f"http://127.0.0.1:{port}/v1"})
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MESHPOOL_BASE_URL", f"http://127.0.0.1:{port}/v1")
    yield port, log
    p.kill()


@needs_browser
def test_mock_server_full_loop_with_the_independent_reviewer(proj, mock_server):
    port, log = mock_server
    a, ev = agent(proj, "work")
    final = a.send("landing page demo: " + ASK)
    assert "initGallery" not in (proj / "index.html").read_text()
    assert len(reviews(a)) == 1 and PERSONA in text_of(reviews(a)[0])
    assert "Checked: " + PERSONA + " tried it; fixed sideways scrolling on the phone" in final, final
    sent = [json.loads(x) for x in log.read_text().splitlines()]
    reviewer = [r for r in sent if r["n_tools"] == 0 and r["n_msgs"] == 2]          # no tools, one system and one user message
    assert len(reviewer) == 3 and len(sent) >= 5
    assert not _chrome_children()
