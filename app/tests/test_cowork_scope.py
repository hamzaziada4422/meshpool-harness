"""0.0.13: Cowork delivers what was asked, at the size asked: one deliverable, no extra plan or tracker files, research only
as deep as the request needs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import skills as SK  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent, WORK_SYSTEM  # noqa: E402

APP = Path(__file__).resolve().parents[1]
CHANGED = ["email-newsletter", "blog-post", "daily-plan", "briefing-interview", "project-plan", "competitor-analysis",
           "research-brief", "meeting-prep", "seo-content-brief", "feedback-synthesis", "survey-analysis", "interview-plan"]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")


def test_work_prompt_says_do_the_asked_thing_at_the_size_asked():
    for phrase in ("ONE deliverable", "plan document", "tracker", "Research only as deep", "Ask only when truly blocked"):
        assert phrase in WORK_SYSTEM, phrase
    assert "—" not in WORK_SYSTEM


@pytest.mark.parametrize("name", CHANGED)
def test_changed_skills_are_plain_and_refresh_old_installs(name):
    t = (APP / "defaults/skills" / name / "SKILL.md").read_text(encoding="utf-8")
    assert "—" not in t and "–" not in t, name
    assert name in SK.REFRESHED_DEFAULTS and len(SK.REFRESHED_DEFAULTS[name]) == 1
    import hashlib
    assert hashlib.sha256(t.encode()).hexdigest() not in SK.REFRESHED_DEFAULTS[name]   # the old text, not this one


def test_newsletter_and_plan_skills_no_longer_ask_for_extra_files():
    news = (APP / "defaults/skills/email-newsletter/SKILL.md").read_text(encoding="utf-8")
    assert "One deliverable" in news and "plain-text version too" not in news
    plan = (APP / "defaults/skills/project-plan/SKILL.md").read_text(encoding="utf-8")
    assert "Do not build a workbook" in plan and "Ask once if the goal or deadline is missing" not in plan
    comp = (APP / "defaults/skills/competitor-analysis/SKILL.md").read_text(encoding="utf-8")
    assert "not both" in comp and "top five" not in comp


def test_an_old_unedited_skill_is_replaced_and_an_edited_one_kept(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "blog-post").mkdir(parents=True)
    (src / "blog-post" / "SKILL.md").write_text("new", encoding="utf-8")
    (dst / "blog-post").mkdir(parents=True)
    # reproduce the shipped 0.0.12 bytes through git history is not possible here, so use the registered hash
    import hashlib
    old_text = b"old text"
    SK.REFRESHED_DEFAULTS["blog-post"] = SK.REFRESHED_DEFAULTS["blog-post"] | {hashlib.sha256(old_text).hexdigest()}
    try:
        (dst / "blog-post" / "SKILL.md").write_bytes(old_text)
        SK._refresh_unedited_defaults(src, dst)
        assert (dst / "blog-post" / "SKILL.md").read_text(encoding="utf-8") == "new"
        (dst / "blog-post" / "SKILL.md").write_text("the user edited this", encoding="utf-8")
        SK._refresh_unedited_defaults(src, dst)
        assert (dst / "blog-post" / "SKILL.md").read_text(encoding="utf-8") == "the user edited this"
    finally:
        SK.REFRESHED_DEFAULTS["blog-post"] = {h for h in SK.REFRESHED_DEFAULTS["blog-post"] if h != hashlib.sha256(old_text).hexdigest()}


def test_lookup_budget_notes(tmp_path):
    a = Agent(str(tmp_path), mode="auto", profile="work")
    a._turn_no = 1
    notes = [a._lookup_note("fetch_url", {"url": f"https://example.com/{i}"}) for i in range(1, 17)]
    assert notes[6] == "" and "8 lookups" in notes[7] and notes[8] == ""
    assert "Stop researching" in notes[15]
    assert a._lookup_note("read_file", {"path": "x"}) == ""
    a._turn_no = 2                                           # a new turn starts the count again
    assert a._lookup_note("web_search", {"query": "x"}) == ""


def test_a_fetch_of_a_search_engine_page_is_told_to_use_web_search(tmp_path):
    a = Agent(str(tmp_path), mode="auto", profile="work")
    a._turn_no = 1
    n = a._lookup_note("fetch_url", {"url": "https://www.bing.com/search?q=offsite+agenda&first=741"})
    assert "use web_search" in n
