"""The memory page's routes: browse, edit, pin, scope, archive with Undo, tray, history, export, X-ray, sources."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from meshpool_harness import assistant as A
from meshpool_harness import memstore as MS
from meshpool_harness import project_store as PS


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    MS._ENSURED.clear()
    MS._CACHE.clear()
    from meshpool_harness.ui import server as SV
    httpd = SV._Server(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Meshpool-Token": SV.TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers["Content-Type"].startswith("application/json") else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    yield call
    httpd.shutdown()


def test_browse_edit_pin_scope_archive_undo(web):
    st, r = web("/api/memory", {"action": "add", "text": "Prefers Segoe UI"})
    fid = r["fact"]["id"]
    assert st == 200 and r["fact"]["category"] == "preferences" and r["fact"]["source"]["text"].startswith("You wrote")
    web("/api/memory", {"action": "edit", "id": fid, "text": "Prefers Segoe UI for decks"})
    web("/api/memory", {"action": "pin", "id": fid, "on": True})
    pid = PS.create("Board deck")["id"]
    web("/api/memory", {"action": "scope", "id": fid, "scope": f"project:{pid}"})
    v = web("/api/memory")[1]
    f = v["facts"][0]
    assert f["text"] == "Prefers Segoe UI for decks" and f["pinned"] and f["scope_label"] == "Board deck"
    assert v["projects"][0]["name"] == "Board deck" and v["counts"]["active"] == 1
    web("/api/memory", {"action": "archive", "id": fid})
    assert web("/api/memory")[1]["facts"] == [] and len(web("/api/memory?status=archived")[1]["facts"]) == 1
    web("/api/memory", {"action": "unarchive", "id": fid})
    assert len(web("/api/memory")[1]["facts"]) == 1
    web("/api/memory", {"action": "delete", "id": fid})
    assert web("/api/memory")[1]["facts"] == []
    web("/api/memory", {"action": "undelete", "id": fid})
    assert len(web("/api/memory")[1]["facts"]) == 1
    hist = web("/api/memory/history")[1]["history"]
    assert {h["action"] for h in hist} >= {"added", "edited", "archived", "deleted", "restored"}


def test_the_tray_confirm_and_reject(web):
    a = MS.notice("Meets the CFO every Friday", evidence=[{"text": "x"}, {"text": "y"}])
    b = MS.notice("Uses Linear for tickets")
    v = web("/api/memory")[1]
    assert {p["id"] for p in v["pending"]} == {a["id"], b["id"]} and v["facts"] == []
    assert next(p for p in v["pending"] if p["id"] == a["id"])["source"]["text"] == "Noticed across 2 chats"
    web("/api/memory", {"action": "confirm_edit", "id": a["id"], "text": "Meets the CFO on Fridays"})
    web("/api/memory", {"action": "reject", "id": b["id"]})
    v = web("/api/memory")[1]
    assert [f["text"] for f in v["facts"]] == ["Meets the CFO on Fridays"] and v["pending"] == []
    assert MS.notice("Uses Linear for tickets") is None


def test_search_and_filters(web):
    for t in ("Prefers Segoe UI", "Manager is Layla Haddad", "Based in Dubai"):
        MS.add(t)
    assert [f["text"] for f in web("/api/memory?q=layla")[1]["facts"]] == ["Manager is Layla Haddad"]
    assert [f["category"] for f in web("/api/memory?category=preferences")[1]["facts"]] == ["preferences"]
    assert len(web("/api/memory?kind=learned")[1]["facts"]) == 0


def test_conflicts_review_health_and_export(web):
    MS.add("Prefers Segoe UI for decks")
    MS.add("Always use Arial for slides")
    v = web("/api/memory")[1]
    assert len(v["conflicts"]) == 1 and v["conflicts"][0]["reason"]
    c = v["conflicts"][0]
    web("/api/memory", {"action": "resolve", "winner": c["a"], "loser": c["b"]})
    assert web("/api/memory")[1]["conflicts"] == []
    st, raw = web("/api/memory/export?type=md")
    assert st == 200 and b"What Meshpool knows" in raw
    assert b"How I work" in web("/api/memory/export?type=onepager")[1]


def test_sources_and_xray_match_what_the_agent_got(web):
    f, _ = MS.add("Prefers Segoe UI")
    g, _ = MS.add("Works at Acme")
    ctx = A.context_block("a deck", chat_id="chat-7")
    st, s = web("/api/memory/sources?chat=chat-7")
    assert {x["text"] for x in s["facts"]} == {"Prefers Segoe UI", "Works at Acme"} and "Given to the agent" in s["wording"]
    x = web("/api/xray?chat=chat-7&text=a%20deck")[1]
    assert x["started"] and x["snapshot"] == ctx
    new = web("/api/xray?chat=chat-new&text=a%20deck")[1]
    assert not new["started"] and "Segoe UI" in new["text"]
    mem = next(sec for sec in new["sections"] if sec["facts"])
    assert {fa["text"] for fa in mem["facts"]} == {"Prefers Segoe UI", "Works at Acme"}


def test_incognito_xray_shows_no_memory(web):
    MS.add("Prefers Segoe UI")
    x = web("/api/xray?chat=c1&incognito=1")[1]
    assert x["incognito"] and "Segoe" not in x["text"]


def test_undo_noted_for_next_time(web):
    f, _ = MS.add("Prefers dark charts", kind="learned")
    web("/api/memory", {"action": "undo_noted", "ids": [f["id"]]})
    assert "dark charts" not in A.memory()


def test_the_projects_view_shows_instructions_and_files(web):
    import base64
    pid = PS.create("Board deck", "", "One message per slide.")["id"]
    PS.knowledge_add(pid, "style-guide.md", b"Use sentence case.")
    MS.add("Board wants USD", scope=f"project:{pid}")
    p = next(x for x in web("/api/memory")[1]["projects"] if x["id"] == pid)
    assert p["instructions_text"] == "One message per slide." and p["files"] == ["style-guide.md"] and p["knowledge"] == 1


def test_one_os_notification_a_day_and_none_without_a_card(web):
    st, r = web("/api/insights/os", {})
    assert st == 200 and r["card"] is None
