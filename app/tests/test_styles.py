"""Writing styles: presets, the user's own styles, the default, a chat's choice, and the hidden note on every message."""
import json
import threading
import time

import pytest

from meshpool_harness import styles as ST
from meshpool_harness.ui import agent_api as A

SEP = "\n\n​"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MESHPOOL_API_KEY", raising=False)
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "none.config"))     # no key from a real config file
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    return tmp_path


# ---------- presets, custom styles, the default -------------------------------------------------------------------
def test_presets_exist_and_normal_is_empty():
    by = {s["id"]: s for s in ST.all_styles()}
    assert [s["name"] for s in ST.all_styles()][:4] == ["Normal", "Concise", "Explanatory", "Formal"]
    assert by["normal"]["instruction"] == ""
    for k in ("concise", "explanatory", "formal"):
        ins = by[k]["instruction"]
        assert by[k]["preset"] and 2 <= ins.count(".") <= 4 and "—" not in ins and "–" not in ins
    assert ST.default_id() == "normal"


def test_custom_crud_and_persistence(home):
    a = ST.create("  Plain   and warm ", "Write short, warm sentences.")
    assert a["name"] == "Plain and warm" and a["id"].startswith("c_")
    f = home / "home" / "styles.json"
    assert json.loads(f.read_text())["custom"][0]["id"] == a["id"]
    assert [s["id"] for s in ST.custom_styles()] == [a["id"]]
    ST.update(a["id"], "Plain", "Write plainly.")
    assert ST.get(a["id"])["name"] == "Plain" and ST.get(a["id"])["instruction"] == "Write plainly."
    assert ST.listing()["styles"][-1]["id"] == a["id"]                      # survives a fresh read of the file
    ST.delete(a["id"])
    assert ST.get(a["id"]) is None and ST.custom_styles() == []


def test_custom_validation_and_presets_are_fixed():
    with pytest.raises(ValueError):
        ST.create("", "x")
    with pytest.raises(ValueError):
        ST.create("Name", "   ")
    with pytest.raises(ValueError):
        ST.create("concise", "duplicate of a preset name")
    a = ST.create("Mine", "Be mine.")
    with pytest.raises(ValueError):
        ST.create("MINE", "same name, other case")
    for fn in (lambda: ST.update("concise", "x", "y"), lambda: ST.delete("formal"),
               lambda: ST.update("c_nope", "x", "y"), lambda: ST.delete("c_nope"), lambda: ST.set_default("c_nope")):
        with pytest.raises(ValueError):
            fn()
    assert ST.get(a["id"])


def test_default_style_persists_and_resets_when_deleted(home):
    a = ST.create("Mine", "Be mine.")
    ST.set_default(a["id"])
    assert ST.default_id() == a["id"] and ST.listing()["default"] == a["id"]
    assert json.loads((home / "home" / "styles.json").read_text())["default"] == a["id"]
    ST.delete(a["id"])
    assert ST.default_id() == "normal"


def test_a_damaged_file_reads_as_no_custom_styles(home):
    (home / "home").mkdir()
    (home / "home" / "styles.json").write_text("{not json", encoding="utf-8")
    assert ST.default_id() == "normal" and ST.custom_styles() == []
    assert ST.create("After", "Still works.")["name"] == "After"


# ---------- the note -----------------------------------------------------------------------------------------------
def test_note_is_hidden_and_normal_adds_nothing():
    assert ST.note("normal") == "" and ST.note("") == "" and ST.note("c_deleted") == ""
    n = ST.note("concise")
    assert n.startswith(SEP) and "Concise" in n and ST.get("concise")["instruction"] in n
    assert (("hello" + n).split("​")[0]).strip() == "hello"            # what the chat shows


def test_chat_without_a_choice_follows_the_default():
    a = ST.create("Mine", "Be mine.")
    ST.set_default(a["id"])
    assert "Be mine." in ST.note("") and "Be mine." in ST.note(None)
    assert ST.note("normal") == ""                                          # an explicit Normal beats the default


# ---------- a chat's style: saved in meta, added to the message body ----------------------------------------------
class StubAgent:
    def __init__(self):
        self.sent, self.messages, self.usage, self.model = [], [], {}, "m"
        self.mode, self.title, self.id = "ask", "t", "x"

    def send(self, body, images=None):
        self.sent.append(body)


class StubPi(StubAgent):
    def steer(self, body, images=None):
        self.sent.append(body)


def make_session(sid, kind, tmp_path):
    (A.SESSIONS / sid).mkdir(parents=True, exist_ok=True)
    s = A.Session.__new__(A.Session)
    s.events, s.lock, s.epoch = [], threading.RLock(), 1
    s.id, s.workdir, s.plan, s.goal, s.scheduled, s.queue = sid, str(tmp_path), False, "", None, []
    s.state, s.ctx_pending, s.pendings, s.cp, s.mcp, s._stop_req = "idle", False, {}, None, None, False
    s.mode, s.kind, s._goal_idle, s._goal_net = "ask", "code", 0, 0
    s.harness = {"id": kind, "name": kind, "type": kind}
    s.agent = StubPi() if kind in ("pi", "acp") else StubAgent()
    s.touched = time.time()
    s.emit = lambda ev: s.events.append(ev)
    return s


def wait_idle(s):
    end = time.time() + 10
    while s.state != "idle" and time.time() < end:
        time.sleep(0.02)
    assert s.state == "idle", "the turn did not finish"


LONG = ("Please review the whole repository, then write a detailed migration plan with every step ordered by risk, "
        "and explain how each part of the work will be checked afterwards.")


@pytest.mark.parametrize("kind", ["meshpool", "pi", "acp"])
def test_chat_style_note_reaches_every_agent_type(kind, tmp_path):
    s = make_session("chat-" + kind, kind, tmp_path)
    s.set_style("concise")
    assert A.read_meta(s.id)["style"] == "concise"                         # saved: it survives a restart
    s.send(LONG)
    wait_idle(s)
    body = s.agent.sent[-1]
    assert body.split("​")[0].strip() == LONG                          # the visible part is the user's text
    assert SEP + "[Meshpool] Writing style for your replies: Concise." in body
    assert ST.get("concise")["instruction"] in body.split("​", 1)[1]


@pytest.mark.parametrize("kind", ["meshpool", "pi"])
def test_normal_adds_no_note(kind, tmp_path):
    s = make_session("n-" + kind, kind, tmp_path)
    s.set_style("normal")
    s.send(LONG)
    wait_idle(s)
    assert "Writing style" not in s.agent.sent[-1]


def test_style_follows_the_chat_and_changes_between_messages(tmp_path):
    s = make_session("chg", "meshpool", tmp_path)
    s.set_style("formal")
    s.send(LONG)
    wait_idle(s)
    s.set_style("explanatory")
    s.send(LONG)
    wait_idle(s)
    assert "Formal." in s.agent.sent[0] and "Explanatory." in s.agent.sent[1] and "Formal." not in s.agent.sent[1]
    # a new Session object for the same chat (a restart) reads the same choice from meta
    assert make_session("chg", "meshpool", tmp_path).style_id() == "explanatory"


def test_unknown_style_is_refused_and_a_deleted_one_reads_as_normal(tmp_path):
    s = make_session("gone", "meshpool", tmp_path)
    with pytest.raises(ValueError):
        s.set_style("c_missing")
    a = ST.create("Temp", "Be temporary.")
    s.set_style(a["id"])
    ST.delete(a["id"])
    s.send(LONG)
    wait_idle(s)
    assert "Writing style" not in s.agent.sent[-1]


def test_new_chat_keeps_the_default_it_started_with(tmp_path):
    a = ST.create("Mine", "Be mine.")
    ST.set_default(a["id"])
    s = make_session("dflt", "meshpool", tmp_path)
    assert s.style_id() == "" and "Be mine." in s._style_note()             # no choice: the default applies
    A.write_meta("dflt", style=ST.default_id())                             # what create() stores
    ST.set_default("normal")
    assert "Be mine." in s._style_note()                                    # later default changes do not move it


def test_view_reports_the_style(tmp_path):
    s = make_session("v1", "meshpool", tmp_path)
    s.set_style("formal")
    assert s.style_id() == "formal"
    a = ST.create("Mine", "Be mine.")
    ST.set_default(a["id"])
    assert make_session("v2", "meshpool", tmp_path).style_id() == ""        # view() shows the default for it
    assert A._default_style() == a["id"]


# ---------- a writing sample becomes instructions -------------------------------------------------------------------
def test_sample_without_a_key_keeps_the_text_and_says_so():
    r = ST.from_sample("I write short lines. No fluff. Ever.")
    assert r["generated"] is False and r["instruction"] == "I write short lines. No fluff. Ever."
    assert "kept as the instructions" in r["message"]


def test_sample_needs_text():
    with pytest.raises(ValueError):
        ST.from_sample("   ")


class FakeClient:
    def __init__(self, text="Write in short, warm sentences. Avoid jargon.", boom=False, slow=0):
        self.text, self.boom, self.slow, self.calls = text, boom, slow, []

    def chat(self, **kw):
        self.calls.append(kw)
        if self.slow:
            time.sleep(self.slow)
        if self.boom:
            raise RuntimeError("down")
        return {"choices": [{"message": {"content": self.text}}]}


def test_sample_is_one_cheap_model_call():
    c = FakeClient()
    r = ST.from_sample("A sample of my writing. It is plain.", client=c, model="cheap-model")
    assert r == {"instruction": "Write in short, warm sentences. Avoid jargon.", "generated": True,
                 "message": r["message"]}
    assert len(c.calls) == 1 and c.calls[0]["model"] == "cheap-model" and c.calls[0]["max_tokens"] <= 400
    assert "A sample of my writing." in c.calls[0]["messages"][0]["content"]


def test_sample_falls_back_when_the_call_fails_or_is_empty_or_slow(monkeypatch):
    for c in (FakeClient(boom=True), FakeClient(text="   ")):
        r = ST.from_sample("my text", client=c)
        assert r["generated"] is False and r["instruction"] == "my text"
    monkeypatch.setenv("MH_STYLE_TIMEOUT", "0.2")
    t0 = time.time()
    r = ST.from_sample("my text", client=FakeClient(slow=3))
    assert r["generated"] is False and time.time() - t0 < 2


# ---------- the API ----------------------------------------------------------------------------------------------
def test_http_endpoints(tmp_path):
    import urllib.request
    from meshpool_harness.ui import server as SRV
    srv = SRV.ThreadingHTTPServer(("127.0.0.1", 0), SRV.Handler) if hasattr(SRV, "Handler") else None
    if srv is None:
        pytest.skip("server handler is not importable under this name")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Meshpool-Token": SRV.TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        st, d = call("/api/styles")
        assert st == 200 and [s["id"] for s in d["styles"]][:2] == ["normal", "concise"] and d["default"] == "normal"
        st, d = call("/api/styles", {"action": "create", "name": "Mine", "instruction": "Be mine."})
        cid = d["styles"][-1]["id"]
        assert st == 200
        st, d = call("/api/styles", {"action": "update", "id": cid, "name": "Mine 2", "instruction": "Be mine two."})
        assert d["styles"][-1]["name"] == "Mine 2"
        st, d = call("/api/styles", {"action": "default", "id": cid})
        assert d["default"] == cid
        st, d = call("/api/styles", {"action": "sample", "sample": "Short lines."})
        assert st == 200 and d["generated"] is False
        st, d = call("/api/styles", {"action": "delete", "id": "concise"})
        assert st == 400 and "built-in" in d["error"]
        st, d = call("/api/styles", {"action": "delete", "id": cid})
        assert st == 200 and d["default"] == "normal"
    finally:
        srv.shutdown()
