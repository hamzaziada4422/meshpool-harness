"""Data behind the v3 shell: artifacts across chats, scheduled tasks as cards, search across chats, code sessions,
projects, artifacts and scheduled tasks, the account menu, unread flags and pinned projects. No network, no model."""
import json
import time

import pytest

from meshpool_harness import assistant as AS
from meshpool_harness import config as C
from meshpool_harness import project_store as PS
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import hub as HUB


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "userhome"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.SESSIONS.mkdir()
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "_METAS", {})
    monkeypatch.setattr(A, "_ROWS", {})
    monkeypatch.setattr(HUB, "_ART", {})
    C.ensure_home()
    return tmp_path


def make_session(home, sid, title, workdir, kind="cowork", files=(), updated=None, **meta):
    """A saved chat: session.json with the tool calls that wrote `files`, and its meta.json."""
    d = A.SESSIONS / sid
    d.mkdir(parents=True)
    calls, msgs = [], [{"role": "system", "content": "x"}, {"role": "user", "content": title}]
    for i, f in enumerate(files):
        calls.append({"id": f"c{i}", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": f, "content": "hi"})}})
    if calls:
        msgs.append({"role": "assistant", "content": "", "tool_calls": calls})
        msgs += [{"role": "tool", "tool_call_id": c["id"], "content": "Saved"} for c in calls]
    (d / "session.json").write_text(json.dumps({"id": sid, "title": title, "workdir": str(workdir), "updated": updated or time.time(),
                                                "kind": kind, "harness": "meshpool-work", "messages": msgs}))
    (d / "meta.json").write_text(json.dumps(dict({"kind": kind}, **meta)))


def test_artifacts_across_chats(home):
    w1, w2 = home / "w1", home / "w2"
    w1.mkdir(); w2.mkdir()
    (w1 / "report.docx").write_bytes(b"x" * 10)
    (w1 / "notes.py").write_text("print(1)")          # source code is not an artifact
    (w2 / "model.xlsx").write_bytes(b"y" * 20)
    make_session(home, "a1", "Write the report", w1, files=["report.docx", "notes.py"])
    make_session(home, "a2", "Build the model", w2, files=["model.xlsx", "gone.pdf"], project_id="")
    out = HUB.artifacts()
    names = {a["name"]: a for a in out["artifacts"]}
    assert set(names) == {"report.docx", "model.xlsx"}               # missing files and source code are left out
    assert names["report.docx"]["type"] == "Docs" and names["model.xlsx"]["type"] == "Sheets"
    assert names["report.docx"]["session_title"] == "Write the report"
    assert out["types"] == {"Docs": 1, "Sheets": 1}
    again = HUB.artifacts()                                            # served from the per-chat cache
    assert [a["id"] for a in again["artifacts"]] == [a["id"] for a in out["artifacts"]]


def test_code_session_edits_are_not_artifacts(home):
    w = home / "repo"
    w.mkdir()
    (w / "README.md").write_text("hi")
    d = A.SESSIONS / "c1"
    d.mkdir()
    call = {"id": "k", "type": "function", "function": {"name": "edit", "arguments": json.dumps({"path": "README.md", "old": "a", "new": "b"})}}
    (d / "session.json").write_text(json.dumps({"id": "c1", "title": "Fix readme", "workdir": str(w), "updated": time.time(), "kind": "code",
                                                "messages": [{"role": "system", "content": "x"}, {"role": "user", "content": "go"},
                                                             {"role": "assistant", "content": "", "tool_calls": [call]}, {"role": "tool", "tool_call_id": "k", "content": "ok"}]}))
    (d / "meta.json").write_text(json.dumps({"kind": "code"}))
    assert HUB.artifacts()["artifacts"] == []


def test_scheduled_cards(home):
    t = AS.add_task("Daily recap", "Summarise yesterday.", "daily", "09:30")
    off = AS.add_task("Friday wrap", "Wrap the week.", "weekly", "16:00", day="fri")
    AS.update_task(off["id"], enabled=False)
    make_session(home, "s1", "Daily recap (19 Nov 09:30)", home, scheduled=t["id"], unread=True)
    AS.set_thread(t["id"], "s1")
    rows = {r["name"]: r for r in HUB.scheduled()["tasks"]}
    assert rows["Daily recap"]["status"] == "Active" and "09:30" in rows["Daily recap"]["schedule"]
    assert rows["Daily recap"]["unread"] is True
    assert rows["Friday wrap"]["status"] == "Paused" and rows["Friday wrap"]["unread"] is False
    assert HUB.scheduled()["templates"], "the Templates tab has starters"


def test_unread_flag_and_seen_action(home):
    make_session(home, "u1", "Finished chat", home, unread=True)
    make_session(home, "u2", "Opened chat", home)
    rows = {r["id"]: r for r in A.listing()}
    assert rows["u1"]["unread"] is True and rows["u2"]["unread"] is False
    A.thread_action("u1", {"action": "seen"})
    assert {r["id"]: r for r in A.listing()}["u1"]["unread"] is False
    A.thread_action("u1", {"action": "unseen"})
    assert {r["id"]: r for r in A.listing()}["u1"]["unread"] is True


def test_search_empty_query_groups(home):
    make_session(home, "n1", "Waiting chat", home, unread=True)
    make_session(home, "n2", "Quiet chat", home)
    (home / "repo").mkdir()
    make_session(home, "n3", "Refactor parser", home / "repo", kind="code", unread=True)
    out = HUB.search("")
    keys = [g["key"] for g in out["groups"]]
    assert keys[0] == "attention" and "recents" in keys and keys[-1] == "actions"
    att = {i["id"]: i for i in out["groups"][0]["items"]}
    assert set(att) == {"n1", "n3"} and att["n3"]["kind"] == "code" and att["n1"]["kind"] == "chat"
    assert any(i["id"] == "new_session" for i in out["groups"][-1]["items"])
    only_code = HUB.search("", "code")
    assert [i["id"] for g in only_code["groups"] for i in g["items"]] == ["n3"]


def test_search_across_everything(home):
    w = home / "w"
    w.mkdir()
    (w / "budget.xlsx").write_bytes(b"z")
    make_session(home, "q1", "Plan the budget", w, files=["budget.xlsx"])
    make_session(home, "q2", "Something else", home)
    PS.create("Budget 2027", "Numbers")
    AS.add_task("Budget reminder", "Remind me about the budget.", "weekly", "10:00", day="mon")
    out = HUB.search("budget")
    by = {g["key"]: g for g in out["groups"]}
    assert [i["id"] for i in by["sessions"]["items"]] == ["q1"]
    assert by["projects"]["items"][0]["title"] == "Budget 2027"
    assert by["artifacts"]["items"][0]["title"] == "budget.xlsx"
    assert by["scheduled"]["items"][0]["title"] == "Budget reminder"
    only = HUB.search("budget", "projects")
    assert [g["key"] for g in only["groups"]] == ["projects"]
    assert HUB.search("zzzz-nothing")["groups"] == []


def test_account_menu_data(home):
    AS_profile = __import__("meshpool_harness.ui.settings_v2", fromlist=["x"])
    AS_profile.save_profile(name="Sam Rivera", address="Sam")
    make_session(home, "x1", "Chat", home, unread=True)
    a = HUB.account()
    assert a["first_name"] == "Sam" and a["initial"] == "S" and a["key_set"] is True and a["plan"] == "Meshpool key"
    assert a["device"]["connected"] is True and a["billing_url"].startswith("https://")
    assert a["chat_unread"] == 1 and a["code_waiting"] == 0


def test_pinned_projects(home):
    p = PS.create("Pinned one")
    before = PS.list_projects()[0]["updated"]
    time.sleep(0.01)
    q = PS.update(p["id"], pinned=True)
    assert q["pinned"] is True and PS.list_projects()[0]["pinned"] is True
    assert PS.list_projects()[0]["updated"] == before               # pinning is not activity
    assert PS.update(p["id"], pinned=False)["pinned"] is False


def test_search_shows_the_stored_pull_request_number(home):
    (home / "repo").mkdir()
    make_session(home, "p1", "Fix navigation", home / "repo", kind="code", unread=True, pr={"number": 39, "title": "Fix focus order", "draft": True})
    item = HUB.search("")["groups"][0]["items"][0]
    assert item["id"] == "p1" and item["sub"] == "PR #39"
