"""Release review (UI): Undo and Rewind hold the chat's turn, so a message sent while they run is queued and answered
after them instead of being lost; a finished reply's text_delta events are folded in place (event numbers never move);
POST /show accepts only a real JSON request from no other origin. No model calls: the agent's model call is replaced."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from meshpool_harness import chats, checkpoints
from meshpool_harness.agent import core
from meshpool_harness.ui import agent_api as A


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test-key-123456")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MH_SLOTS_DIR", str(tmp_path / "slots"))
    sessions = tmp_path / "runs" / "sessions"
    sessions.mkdir(parents=True)
    for mod in (core, A, chats):
        monkeypatch.setattr(mod, "SESSIONS", sessions)

    def fake(self, turn):                     # the model: streams its reply in a few pieces, then answers
        for k, v in {"in": 1, "cached": 0, "out": 1, "calls": 1}.items():
            turn[k] += v
            self.usage[k] += v
        last = str([m for m in self.messages if m["role"] == "user"][-1]["content"]).split("\n")[0][:30]
        reply = "reply to " + last
        for piece in (reply[:5], reply[5:12], reply[12:]):
            self.emit({"type": "text_delta", "text": piece})
        return {"role": "assistant", "content": reply}, False
    monkeypatch.setattr(core.Agent, "_chat", fake)
    proj = tmp_path / "proj"
    proj.mkdir()
    v = chats.create({"workdir": str(proj), "no_mcp": True})
    s = chats.get(v["id"], no_mcp=True)
    yield s
    try:
        s.close()
    except Exception:  # noqa: BLE001
        pass


def _wait(s, secs=20):
    end = time.time() + secs
    time.sleep(0.05)
    while s.state in ("working", "waiting", "starting") and time.time() < end:
        time.sleep(0.02)
    assert s.state == "idle", s.state


def _users(s):
    return [e["text"] for e in s.events if e.get("type") == "user"]


def _slow_restore(monkeypatch, secs=0.8):
    orig = checkpoints.Checkpoints.revert_to_before

    def slow(self, turn):
        time.sleep(secs)                      # restoring files takes a moment
        return orig(self, turn)
    monkeypatch.setattr(checkpoints.Checkpoints, "revert_to_before", slow)


def _three(s):
    for t in ("m1", "m2", "m3"):
        s.send(t)
        _wait(s)
    assert _users(s) == ["m1", "m2", "m3"]


def _during(s, op, text):
    """Run `op` (an Undo or a Rewind) on a thread and send `text` while it restores the files."""
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("r", op()))
    th.start()
    time.sleep(0.3)
    assert s.state == "working", "the Undo / Rewind must hold the turn while it runs"
    r = s.send(text)
    th.join(10)
    _wait(s)
    return r, out.get("r")


def test_a_message_sent_during_undo_is_queued_and_answered_after_it(chat, monkeypatch):
    s = chat
    _three(s)
    _slow_restore(monkeypatch)
    r, _ = _during(s, lambda: s.revert(3), "typed during undo")
    assert r.get("queued") is True and not r.get("added")
    assert _users(s) == ["m1", "m2", "typed during undo"]
    said = [str(m["content"]).split("\n")[0] for m in s.agent.messages[1:]]
    assert said[-2:] == ["typed during undo", "reply to typed during undo"]
    assert "m3" not in said


def test_a_message_sent_during_rewind_is_queued_and_answered_after_it(chat, monkeypatch):
    s = chat
    _three(s)
    ei = [e["i"] for e in s.events if e.get("type") == "user"][1]          # rewind to just before "m2"
    _slow_restore(monkeypatch)
    r, rw = _during(s, lambda: s.rewind(ei, files=True), "typed during rewind")
    assert rw["text"] == "m2"
    assert r.get("queued") is True
    assert _users(s) == ["m1", "typed during rewind"]
    said = [str(m["content"]).split("\n")[0] for m in s.agent.messages[1:]]
    assert said == ["m1", "reply to m1", "typed during rewind", "reply to typed during rewind"]


def test_undo_and_rewind_refuse_while_a_turn_runs(chat):
    s = chat
    _three(s)
    tok = s._claim_turn()
    try:
        with pytest.raises(ValueError, match="Stop the agent"):
            s.revert(1)
        with pytest.raises(ValueError, match="Stop the agent"):
            s.rewind(0)
    finally:
        s._end_turn(tok, notice=False)
    assert _users(s) == ["m1", "m2", "m3"]


def test_finished_replies_keep_no_text_deltas_and_event_numbers_hold(chat):
    s = chat
    _three(s)
    evs = s.events
    assert [e for e in evs if e.get("type") == "text_delta" and e.get("text")] == []     # nothing of the stream kept
    folded = [e for e in evs if e.get("type") == "text_delta"]
    assert len(folded) == 9 and all(e is A._DELTA_GONE for e in folded)                  # in place: one shared marker
    for p, e in enumerate(evs):
        if e is not A._DELTA_GONE:
            assert e["i"] == p                                                           # positions never moved
    v = s.view(0)
    assert v["total"] == len(evs) and not any(e.get("type") == "text_delta" for e in v["events"])
    assert [e["text"] for e in v["events"] if e.get("type") == "assistant"] == [
        "reply to m1", "reply to m2", "reply to m3"]
    mid = s.view(3)                                                                       # a poll from the middle
    assert mid["total"] == len(evs) and all("i" in e for e in mid["events"])
    # event numbers still name the right message: rewind by the third message's number
    ei = [e["i"] for e in evs if e.get("type") == "user"][2]
    assert s.rewind(ei, files=False)["text"] == "m3"
    assert _users(s) == ["m1", "m2"]


def _bare_session():
    s = A.Session.__new__(A.Session)        # the class supports this (old callers and tests): no agent, no files
    s.events, s.listeners = [], []
    s.lock = threading.RLock()
    s.changed = threading.Condition(s.lock)
    return s


def _sent(s, since=0):
    """What Session.view sends the page (a bare session has no agent for the rest of view)."""
    return [e for e in s.events[since:] if not e.get("gone")]


def test_a_reply_cut_off_without_its_assistant_event_keeps_its_text():
    s = _bare_session()
    s.emit({"type": "user", "text": "hi"})
    for t in ("Hel", "lo ", "the"):
        s.emit({"type": "text_delta", "text": t})
    s.emit({"type": "tool_start", "id": "1", "name": "x"})
    for t in ("re", "!"):
        s.emit({"type": "text_delta", "text": t})
    s.emit({"type": "turn_end"})
    shown = [e["text"] for e in _sent(s) if e.get("type") == "text_delta"]
    assert shown == ["Hello the", "re!"]                     # each streamed run is one delta: the page shows the same
    assert len(s.events) == 8 and [e.get("i") for e in _sent(s)] == [0, 1, 4, 5, 7]
    # and a reply whose assistant event arrived: its deltas are dropped from what the page is sent
    s.emit({"type": "text_delta", "text": "abc"})
    s.emit({"type": "stream_reset"})
    s.emit({"type": "text_delta", "text": "def"})
    s.emit({"type": "assistant", "text": "def"})
    assert [e.get("type") for e in _sent(s, 8)] == ["stream_reset", "assistant"]


# ---------- POST /show: the second launch's "come to the front" -------------------------------------------------------
@pytest.fixture
def show():
    from meshpool_harness.ui import server as SV
    httpd = SV._Server(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def post(ctype, origin=None):
        h = {"Content-Type": ctype}
        if origin is not None:
            h["Origin"] = origin
        req = urllib.request.Request(f"http://127.0.0.1:{port}/show", data=b"{}", method="POST", headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None
    post.port = port
    yield post
    httpd.shutdown()


def test_show_needs_a_real_json_request_from_no_other_origin(show):
    assert show("application/json")[0] == 200                                # what the second launch sends
    assert show("application/json; charset=utf-8")[0] == 200
    assert show("application/json", f"http://127.0.0.1:{show.port}")[0] == 200
    assert show("text/plain; x=json")[0] == 403                              # a simple request: no preflight
    assert show("text/plain;application/json")[0] == 403
    assert show("application/jsonx")[0] == 403
    assert show("application/json", "https://evil.example")[0] == 403        # a page on another site
    assert show("application/json", "null")[0] == 403
    assert show("application/json", "http://127.0.0.1:1")[0] == 403          # another local port is another origin
