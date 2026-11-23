"""ws08 routing: S-04 / D-04 (a Code chat never silently becomes a Work chat) and M29-02 (Auto skips an agent whose
process cannot start)."""
import sys
import threading
import time

import pytest

from meshpool_harness import config as C
from meshpool_harness import harness_router as HRT
from meshpool_harness import harnesses as HR

OFFICE = ["make a powerpoint about coffee", "build an excel model with formulas", "edit the contract.docx",
          "clean this csv and chart revenue"]


def _row(hid, mode, typ="builtin", **kw):
    return {"id": hid, "name": hid, "mode": mode, "type": typ, "available": True, "ready": True, **kw}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    return tmp_path


@pytest.fixture
def rows(monkeypatch, home):
    rs = [_row("meshpool-code", "code"), _row("meshpool-work", "cowork"), _row("pi", "code", "pi"),
          _row("hermes", "cowork", "acp")]
    monkeypatch.setattr(HR, "load_all", lambda: rs)
    monkeypatch.setattr(HR, "provider", lambda: "meshpool")
    return rs


# ---- S-04 / D-04 --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", OFFICE)
def test_office_prompt_in_a_code_chat_never_goes_to_a_cowork_agent(rows, text):
    r = HRT.route(text, "code")
    assert r["harness"] in ("meshpool-code", "pi"), r
    assert all(x["harness"] not in ("meshpool-work", "hermes") for x in r["ranking"])
    assert set(HRT._ready("code")) == {"meshpool-code", "pi"}


@pytest.mark.parametrize("text", OFFICE)
def test_a_work_chat_is_never_routed_to_a_code_agent(rows, text):
    assert HRT.route(text, "cowork")["harness"] in ("meshpool-work", "hermes")
    assert set(HRT._ready("cowork")) == {"meshpool-work", "hermes"}


def test_a_deck_in_a_work_chat_still_picks_meshpool_work(rows):
    assert HRT.route("make a powerpoint about coffee", "cowork")["harness"] == "meshpool-work"


@pytest.fixture
def chat(home, monkeypatch):
    from meshpool_harness.ui import agent_api as A
    sessions = home / "mh" / "runs" / "sessions"
    monkeypatch.setitem(A.Agent.__init__.__globals__, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(A.Session, "_start_mcp", lambda self: self._mcp_ready.set())
    proj = home / "proj"
    proj.mkdir()
    made = []
    yield A, proj, made
    for sid in made:
        s = A.LIVE.pop(sid, None)
        if s:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


def test_create_with_auto_in_code_mode_runs_an_office_task_on_meshpool_code(chat):
    A, proj, made = chat
    v = A.create({"kind": "code", "harness": "meshpool-auto", "task": "make a powerpoint about coffee",
                  "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    assert s.harness["id"] == "meshpool-code"
    using = next(e for e in v["events"] if e.get("using"))
    assert using["auto"]["reason"] and using["auto"]["harness"] == "meshpool-code"
    assert "back" not in using                      # Auto picked the mode's own agent: nothing to go back to


def test_a_non_default_pick_carries_a_way_back_to_the_modes_agent(chat, monkeypatch):
    A, proj, made = chat
    monkeypatch.setattr(HRT, "route", lambda text, kind, **k: {"harness": "meshpool-work", "name": "Meshpool Work",
                                                               "category": "data", "reason": "because"})
    v = A.create({"kind": "code", "harness": "meshpool-auto", "task": "fix the bug", "workdir": str(proj)})
    made.append(v["id"])
    using = next(e for e in v["events"] if e.get("using"))
    assert using["auto"]["harness"] == "meshpool-work"
    assert using["back"] == {"harness": "meshpool-code", "name": HR.get("meshpool-code")["name"]}


# ---- M29-02 -------------------------------------------------------------------------------------------------------

def test_health_records_expires_and_clears(home):
    from meshpool_harness.hrouter import health
    assert health.broken() == {}
    health.start_failed("x", "boom")
    health.start_failed("x", "boom again")
    row = health.broken()["x"]
    assert row["error"] == "boom again" and row["count"] == 2
    assert "x" not in health.broken(now=time.time() + health.RETRY_AFTER_S + 5)      # a fixed install comes back
    health.start_ok("x")
    assert health.broken() == {}


def test_health_never_raises_on_a_damaged_file(home):
    from meshpool_harness.hrouter import health
    p = health._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert health.broken() == {}
    health.start_failed("x", "boom")                 # must not raise; the damaged file is left alone
    health.start_ok("x")
    assert p.read_text(encoding="utf-8") == "{not json"


def test_concurrent_failures_lose_no_rows(home):
    from meshpool_harness.hrouter import health
    ts = [threading.Thread(target=health.start_failed, args=(f"h{i}", "e")) for i in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(health.broken()) == sorted(f"h{i}" for i in range(20))


def test_an_agent_that_cannot_start_is_recorded_and_auto_skips_it(home, monkeypatch):
    from meshpool_harness.harnesses.acp import ACPAgent
    from meshpool_harness.hrouter import health
    manifest = {"id": "zz-broken", "name": "Broken", "mode": "code", "type": "acp", "command": sys.executable,
                "args": ["-c", "import sys; sys.exit(3)"]}
    proj = home / "proj"
    proj.mkdir()
    events = []
    a = ACPAgent(manifest, str(proj), emit=events.append, approve=lambda *x, **k: ("allow_once", ""), mode="ask")
    try:
        a.send("hi")
    finally:
        a.close()
    assert any(e["type"] == "error" for e in events)
    assert a.start_error
    assert "zz-broken" in health.broken()
    rs = [_row("meshpool-code", "code"), dict(manifest, available=True, ready=True)]
    monkeypatch.setattr(HR, "load_all", lambda: rs)
    monkeypatch.setattr(HR, "provider", lambda: "meshpool")
    assert "zz-broken" not in HRT._ready("code") and "meshpool-code" in HRT._ready("code")
    health.start_ok("zz-broken")
    assert "zz-broken" in HRT._ready("code")
    health.start_failed("zz-broken", "old")         # an old failure no longer excludes
    monkeypatch.setattr(health, "RETRY_AFTER_S", -1)
    assert "zz-broken" in HRT._ready("code")


def test_an_auto_chat_whose_agent_cannot_start_moves_on_and_sends_again(chat, monkeypatch):
    A, proj, made = chat
    from meshpool_harness.hrouter import health
    v = A.create({"kind": "code", "harness": "meshpool-auto", "task": "fix the bug", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    assert s.harness["id"] == "meshpool-code"
    # pretend the first agent was something that failed to start
    s.agent.start_error = "could not start"
    health.start_failed("pi", "could not start")
    rs = [_row("meshpool-code", "code"), _row("pi", "code", "pi")]
    monkeypatch.setattr(HR, "load_all", lambda: rs)
    moved = []
    monkeypatch.setattr(s, "handoff", lambda hid, why="": moved.append(hid))
    s.harness = _row("pi", "code", "pi")           # the chat currently runs on Pi, which just failed
    assert s._start_failed_handoff("fix the bug") is True and moved == ["meshpool-code"]
    assert s._start_failed_handoff("fix the bug") is False      # once only
