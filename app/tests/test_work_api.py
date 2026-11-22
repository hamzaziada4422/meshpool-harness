"""The routes behind Your work and the summaries."""
import time

import pytest

from meshpool_harness import project_store as PS
from meshpool_harness.ui import work_api as WA
from tests.summaries_fixtures import chat, setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setenv("MH_LEARN", "0")
    return tmp_path


def test_work_routes(home):
    chat(home, "w1", ["Make a deck"], ["ok"], updated=time.time() - 3600)
    st, d = WA.handle_get("/api/work", {"range": ["1"]})
    assert st == 200 and d["months"] == 1 and d["totals"]["chats"] == 1
    assert WA.handle_get("/api/work", {"range": "banana"})[1]["months"] == 3
    st, p = WA.handle_get("/api/work/recap", {"range": "1"})
    assert st == 200 and p["png_b64"] and p["inputs"]["chats"] == 1
    st, f = WA.handle_get("/api/work/recap.png", {"range": "1", "download": "1"})
    assert st == 200 and f["_bytes"][:4] == b"\x89PNG" and f["download"] and f["filename"].endswith(".png")
    assert WA.handles("/api/work/recap.png") and WA.handles("/api/summaries/chat") and not WA.handles("/api/chats")


def test_summary_routes(home):
    pid = PS.create("Offsite")["id"]
    chat(home, "s1", ["Plan the offsite"], ["ok"], project_id=pid)
    assert WA.handle_get("/api/summaries/chat", {"id": "s1"}) == (200, {"id": "s1", "summary": ""})
    assert WA.handle_get("/api/summaries/chat", {})[0] == 400
    st, b = WA.handle_get("/api/summaries/project", {"id": pid})
    assert st == 200 and b["chats"] == 1 and b["last_opened"] == 0
    assert WA.handle_get("/api/summaries/project", {"id": "nope"})[0] == 404
    st, r = WA.handle_post("/api/summaries/catchup", {"id": pid})
    assert st == 200 and len(r["items"]) == 1
    assert WA.handle_post("/api/summaries/catchup", {"id": pid})[1]["items"] == []
    assert WA.handle_post("/api/summaries/catchup", {})[0] == 400
    assert WA.handle_post("/api/summaries/opened", {"id": pid})[1]["previous"] > 0
    assert WA.handle_post("/api/summaries/other", {"id": pid})[0] == 404
