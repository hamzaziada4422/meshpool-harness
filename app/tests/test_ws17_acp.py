"""Outside harnesses (ACP: Hermes, Codex, OpenCode; Pi over RPC) against fake binaries, so their state machines run in
CI: Stop while starting, Stop then a new prompt, messages added while Pi starts or streams, Pi crashing mid-turn, Undo,
per-chat config, connector tokens and Stop reaching sub-agents."""
import json
import os
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness.harnesses import acp as ACP  # noqa: E402
from meshpool_harness.harnesses.acp import ACPAgent  # noqa: E402

FAKE_ACP = textwrap.dedent('''
    import json, os, sys, threading
    store, log = sys.argv[1], sys.argv[2]
    lock = threading.Lock()
    def send(o):
        with lock:
            sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    def upd(sid, u): send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})
    def known(): return json.load(open(store)) if os.path.exists(store) else []
    def note(m):
        with open(log, "a") as f: f.write(json.dumps(m) + "\\n")
    mine, slow = set(), {}
    for line in sys.stdin:
        m = json.loads(line)
        if "method" not in m:
            continue
        meth, mid, p = m["method"], m.get("id"), m.get("params", {})
        note({"method": meth, "params": {k: v for k, v in p.items() if k != "mcpServers"},
              "env": {k: os.environ.get(k) for k in ("CODEX_HOME", "OPENCODE_CONFIG", "MESHPOOL_HERMES_MODEL")}})
        if meth == "initialize":
            if os.environ.get("FAKE_INIT_HANG"):
                continue                                   # a harness stuck starting (a sign-in, a slow download)
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentInfo": {"name": "fake"},
                  "agentCapabilities": {"loadSession": True}}})
        elif meth == "session/new":
            sid = "s%d" % (len(known()) + 1)
            json.dump(known() + [sid], open(store, "w"))
            mine.add(sid)
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": sid}})
        elif meth == "session/load":
            sid = p["sessionId"]
            if sid not in known():
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Session not found"}})
                continue
            mine.add(sid)
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif meth in ("session/set_model", "session/set_mode"):
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif meth == "session/cancel":
            for sid, rid in list(slow.items()):
                slow.pop(sid)
                reason = "refusal" if os.environ.get("FAKE_REFUSE_CANCEL") else "cancelled"
                threading.Timer(float(os.environ.get("FAKE_CANCEL_DELAY", "0")), send,
                                args=({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": reason}},)).start()
        elif meth == "session/prompt":
            sid = p["sessionId"]
            text = p["prompt"][0]["text"]
            key = text.split("\\n\\n\\u200b")[0].split("\\n\\n")[-1]
            if sid not in mine:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Session not found"}})
            elif "SLOW" in key:
                slow[sid] = mid                            # runs until session/cancel
            elif "USAGE" in key:
                upd(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "counted"}})
                send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn", "usage": {
                    "inputTokens": 100, "outputTokens": 20, "cachedReadTokens": 40, "totalTokens": 120}}})
            else:
                upd(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "fresh:" + key}})
                send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
''')

FAKE_PI = textwrap.dedent('''
    import json, os, sys, threading, time
    args = sys.argv[1:]
    sd = args[args.index("--session-dir") + 1]
    log = os.environ["FAKE_PI_LOG"]
    cont = "--continue" in args
    out = threading.Lock()
    def send(o):
        with out:
            sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    def note(o):
        with open(log, "a") as f: f.write(json.dumps(o) + "\\n")
    note({"start": True, "continue": cont, "agent_dir": os.environ.get("PI_CODING_AGENT_DIR")})
    steering, lock, abort = [], threading.Lock(), threading.Event()
    def qupd(): send({"type": "queue_update", "steering": list(steering), "followUp": []})
    def take():
        with lock:
            got = list(steering); steering.clear()
        if got:
            qupd()
            for g in got:
                um = {"role": "user", "content": [{"type": "text", "text": g}]}
                send({"type": "message_start", "message": um}); send({"type": "message_end", "message": um})
        return got
    def say(text):
        for i in range(0, len(text), 4):
            send({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": text[i:i + 4]}})
        send({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
              "usage": {"input": 10, "output": 5, "cacheRead": 2}, "stopReason": "stop"}})
    def run(msg):
        abort.clear()
        with open(os.path.join(sd, "s.jsonl"), "a") as f: f.write(json.dumps({"user": msg}) + "\\n")
        send({"type": "agent_start"})
        got = take()                                       # Pi reads queued steering when a run starts
        if ("CRASH" in msg and not os.path.exists(os.path.join(sd, "crashed"))) or os.environ.get("FAKE_PI_CRASH_ALWAYS"):
            open(os.path.join(sd, "crashed"), "w").close()
            send({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "half a rep"}})
            time.sleep(0.2)
            os._exit(3)
        if "STREAM" in msg:
            t0 = time.time()
            while not abort.is_set() and time.time() - t0 < 20:
                send({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "."}})
                got = take()
                if got:
                    break
                time.sleep(0.02)
            if abort.is_set():
                send({"type": "agent_end", "messages": []}); return
            say("got:" + "|".join(got))
        else:
            say("echo:" + msg.split("\\n\\n")[-1][:200] + ("" if not got else " +" + "|".join(got)))
        more = take()
        if more:
            say("also:" + "|".join(more))
        send({"type": "agent_end", "messages": []})
    for line in sys.stdin:
        m = json.loads(line); t = m.get("type"); rid = m.get("id")
        note({"cmd": t, "message": m.get("message")})
        def ok(data=None):
            send(dict({"type": "response", "id": rid, "command": t, "success": True}, **({"data": data} if data is not None else {})))
        if t == "get_state":
            ok({"model": {"id": "deepseek-v4.1-flash"}})
        elif t == "get_available_models":
            ok({"models": []})
        elif t == "steer":
            with lock: steering.append(m["message"])
            qupd(); ok()
        elif t == "abort":
            abort.set(); ok()
        elif t == "prompt":
            ok(); threading.Thread(target=run, args=(m["message"],), daemon=True).start()
        else:
            ok({})
''')


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    for k in ("FAKE_INIT_HANG", "FAKE_REFUSE_CANCEL", "FAKE_CANCEL_DELAY"):
        monkeypatch.delenv(k, raising=False)
    C.ensure_home()
    return tmp_path


@pytest.fixture()
def fake_acp(home):
    script = home / "fake_acp.py"
    script.write_text(FAKE_ACP)
    proj = home / "proj"
    proj.mkdir()
    log = home / "acp.jsonl"
    manifest = {"id": "fake", "name": "Fake", "mode": "cowork", "type": "acp", "command": sys.executable,
                "args": [str(script), str(home / "store.json"), str(log)], "office": False, "share_mcp": False}
    agents = []

    def make(**kw):
        evs = kw.pop("events", None)
        a = ACPAgent(dict(manifest, **kw.pop("manifest", {})), str(proj),
                     emit=(evs.append if evs is not None else None), **kw)
        agents.append(a)
        return a

    def calls(method=None):
        rows = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        return [r for r in rows if method is None or r["method"] == method]
    yield make, calls, manifest
    for a in agents:
        a.close()


# ---------- ACP -------------------------------------------------------------------------------------------------

def test_m23_01_stop_while_the_harness_starts_ends_the_turn_at_once(fake_acp, monkeypatch):
    make, calls, _ = fake_acp
    monkeypatch.setenv("FAKE_INIT_HANG", "1")
    evs = []
    a = make(mode="auto", events=evs)
    t = threading.Thread(target=a.send, args=("hello",), daemon=True)
    t.start()
    for _ in range(100):
        if calls("initialize"):
            break
        time.sleep(0.05)
    t0 = time.time()
    a.stop()
    t.join(10)
    assert not t.is_alive() and time.time() - t0 < 3            # was: up to 300 s (the initialize timeout)
    assert any(e["type"] == "interrupted" for e in evs) and not any(e["type"] == "error" for e in evs)
    assert a._p is None                                         # the half-started harness did not stay behind
    monkeypatch.delenv("FAKE_INIT_HANG")
    assert a.send("again") == "fresh:again"                     # the next message starts it again


def test_m23_03_a_refusal_after_stop_on_a_resumed_session_is_not_sent_again(fake_acp, monkeypatch):
    make, calls, _ = fake_acp
    first = make(mode="auto")
    assert first.send("hi") == "fresh:hi"
    first.close()
    monkeypatch.setenv("FAKE_REFUSE_CANCEL", "1")
    a = make(mode="auto")
    a.loaded_from = first.acp_session                           # a chat reopened after a restart
    t = threading.Thread(target=a.send, args=("SLOW job",), daemon=True)
    t.start()
    for _ in range(100):
        if len(calls("session/prompt")) == 2:
            break
        time.sleep(0.05)
    a.stop()
    t.join(10)
    assert not t.is_alive()
    assert len(calls("session/prompt")) == 2                    # was: a fresh session got the stopped message again
    assert len(calls("session/new")) == 1


def test_acp_prompt_usage_reaches_the_turn_and_the_chat(fake_acp):
    make, _, _ = fake_acp
    evs = []
    a = make(mode="auto", events=evs)
    assert a.send("USAGE please") == "counted"
    end = [e for e in evs if e["type"] == "turn_end"][-1]
    assert end["turn"]["in"] == 140 and end["turn"]["out"] == 20 and end["turn"]["cached"] == 40
    assert a.usage["in"] == 140 and a.usage["out"] == 20


def test_ws04_one_ledger_row_per_acp_turn(fake_acp):
    """The harness's usage is in the chat's own counts (ws17) and in the usage ledger (ws04): once each, not twice."""
    from meshpool_harness.core import usage
    make, _, _ = fake_acp
    a = make(mode="auto", events=[])
    a.usage_task = usage.start_task(chat="ws04-acp", label="turn")
    assert a.send("USAGE please") == "counted"
    t = a.usage_task.totals()
    assert sum(b["calls"] for b in t["by_source"].values()) == 1
    assert a.usage["in"] == 140 and a.usage["out"] == 20      # not doubled by the ledger row


def test_m05_05_undo_gives_the_harness_a_session_without_the_undone_turns(fake_acp):
    from meshpool_harness.ui import agent_api as A
    make, calls, manifest = fake_acp
    a = make(mode="auto")
    s = A.Session.__new__(A.Session)
    s.events, s.lock, s.id, s.epoch, s.state = [], threading.RLock(), a.id, 0, "idle"
    s.harness, s.agent = manifest, a
    s.emit = lambda ev: s.events.append(dict(ev, i=len(s.events)))
    s._persist = lambda **kw: None
    a.emit_cb = s.emit
    s.cp = type("CP", (), {"revert_to_before": lambda self, n: {"message_index": None},
                         "turns": lambda self: []})()
    assert a.send("keep this") == "fresh:keep this"
    assert a.send("undo this") == "fresh:undo this"
    s.revert(2)                                                 # Undo the second turn
    assert a.send("third") == "fresh:third"
    sessions = calls("session/new")
    assert len(sessions) == 2                                   # a new harness session after Undo
    last = calls("session/prompt")[-1]["params"]
    assert last["sessionId"] != calls("session/prompt")[0]["params"]["sessionId"]
    text = last["prompt"][0]["text"]
    assert "keep this" in text and "undo this" not in text      # it knows the chat as it now is


def test_m16_04_codex_and_opencode_get_a_config_of_their_own_per_chat(fake_acp, monkeypatch):
    make, calls, _ = fake_acp
    C.write({"MESHPOOL_API_KEY": "mk-test-123456"})
    a1 = make(mode="auto", manifest={"prepare": "codex"})
    a2 = make(mode="auto", manifest={"prepare": "codex"})
    a1.model, a2.model = "model-one", "model-two"
    t1 = threading.Thread(target=a1.send, args=("x",))
    t2 = threading.Thread(target=a2.send, args=("y",))
    t1.start(), t2.start()
    t1.join(30), t2.join(30)
    homes = {}
    for c in calls("initialize"):
        h = Path(c["env"]["CODEX_HOME"])
        homes[h] = (h / "config.toml").read_text()
    assert len(homes) == 2                                       # was: both chats on one shared config.toml
    assert sorted('model = "model-one"' in t or 'model = "model-two"' in t for t in homes.values()) == [True, True]
    assert {('model-one' in t) for t in homes.values()} == {True, False}
    from meshpool_harness import harnesses as HR
    e1, e2 = HR.prepare_opencode("m-a", "c1"), HR.prepare_opencode("m-b", "c2")
    assert e1["OPENCODE_CONFIG"] != e2["OPENCODE_CONFIG"]
    assert "m-a" in Path(e1["OPENCODE_CONFIG"]).read_text() and "m-b" in Path(e2["OPENCODE_CONFIG"]).read_text()


def test_m23_05_hermes_config_names_no_chat_s_provider(home):
    pytest.importorskip("yaml")
    from meshpool_harness import harnesses as HR
    C.write({"MESHPOOL_API_KEY": "mk-test-123456"})
    seen, errors = [], []

    def go(model):
        try:
            env = HR.prepare_hermes(model)
            seen.append((model, env, (HR.hermes_home() / "config.yaml").read_text()))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=go, args=(m,)) for m in ("model-one", "model-two") * 4]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not errors
    texts = {t for _, _, t in seen}
    assert len(texts) == 1                                       # every chat writes the same file ...
    text = texts.pop()
    assert "model-one" not in text and "model-two" not in text and "mk-test" not in text
    for model, env, _ in seen:                                   # ... and each Hermes reads its own model and key
        m = HR.hermes_model(env)
        assert m["default"] == model and m["api_key"] == "mk-test-123456"


def test_m14_02_connectors_reach_harnesses_through_the_refreshing_bridge(home, monkeypatch):
    from meshpool_harness.agent import mcp as M
    monkeypatch.setattr(M, "active_config", lambda path=None: {
        "github": {"url": "http://127.0.0.1:9/mcp", "headers": {"Authorization": "Bearer old-token"}, "connector": True},
        "docs": {"url": "http://127.0.0.1:9/open"}})
    monkeypatch.setattr(M, "recently_failed", lambda name, spec: None)
    rows = {r["name"]: r for r in ACP.mcp_servers_for_acp([str(home)], "chat1")}
    assert "old-token" not in json.dumps(rows)                  # was: the token went to the harness as a header
    assert rows["github"]["args"][-2:] == ["meshpool_harness.agent.connector_bridge", "github"]
    assert rows["docs"]["url"] == "http://127.0.0.1:9/open"     # an open server without credentials stays direct

    accepted = {"token": "Bearer old-token"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.headers.get("Authorization") != accepted["token"]:
                self.send_response(401)
                self.end_headers()
                return
            if "id" not in body:
                self.send_response(202)
                self.end_headers()
                return
            res = {"initialize": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "gh", "version": "1"}},
                   "tools/list": {"tools": [{"name": "whoami", "description": "Who am I",
                                             "inputSchema": {"type": "object", "properties": {}}}]},
                   "tools/call": {"content": [{"type": "text", "text": "octocat"}]}}.get(body["method"], {})
            out = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": res}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from meshpool_harness import mcp_apps as CN
        from meshpool_harness.agent import connector_bridge as CB
        url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"
        monkeypatch.setattr(CN, "servers", lambda: {"github": {"url": url, "headers": {"Authorization": "Bearer old-token"},
                                                                "connector": True, "timeout": 30}})
        refused = []

        def refresh(cid, failed_headers=None):                  # ws12's single-flight refresh
            refused.append((failed_headers or {}).get("Authorization"))
            return {"Authorization": "Bearer new-token"}
        monkeypatch.setattr(CN, "refresh_headers", refresh)
        server = CB.connect("github")
        tools = CB.tools_for(server)
        accepted["token"] = "Bearer new-token"                  # an hour later: the old token is refused
        assert tools["whoami"].fn()[0]["text"] == "octocat"     # the bridge fetched a fresh one and went on
        assert refused == ["Bearer old-token"]                  # telling the refresh which token was refused
        server.close()
    finally:
        srv.shutdown()


def test_m25_01_stop_reaches_sub_agents_without_a_cancel_notification(home, monkeypatch):
    from meshpool_harness import assistant as AS
    from meshpool_harness.agent import assistant_mcp_server as AMS
    seen = {}

    def fake_run(tasks, folder, max_parallel, cancelled=None):
        t0 = time.time()
        while time.time() - t0 < 10:
            if cancelled():
                seen["stopped_after"] = time.time() - t0
                return "stopped"
            time.sleep(0.05)
        return "ran on"
    monkeypatch.setattr(AS, "run_subtasks", fake_run)
    monkeypatch.setenv("MESHPOOL_STOP_FILE", str(ACP.stop_file("chat-x")))
    a = ACPAgent({"id": "fake", "name": "Fake", "command": sys.executable}, str(home), session_id="chat-x")
    threading.Timer(0.5, a.stop).start()                        # the chat's Stop; the harness forwards nothing
    assert AMS.run_subtasks([{"task": "look"}]) == "stopped"
    assert seen["stopped_after"] < 2
    # a Stop from before the call does not stop the next one
    monkeypatch.setattr(AS, "run_subtasks", lambda tasks, folder, mp, cancelled=None: str(cancelled()))
    assert AMS.run_subtasks([{"task": "look"}]) == "False"


def test_m25_01_meshpool_servers_of_an_outside_harness_get_the_chat_s_stop_file(home, monkeypatch):
    from meshpool_harness.agent import mcp as M
    monkeypatch.setattr(M, "active_config", lambda path=None: {
        "meshpool-assistant": {"command": "python", "args": ["-m", "meshpool_harness.agent.assistant_mcp_server"]}})
    monkeypatch.setattr(M, "recently_failed", lambda name, spec: None)
    row = ACP.mcp_servers_for_acp([str(home)], "chat-y")[0]
    env = {e["name"]: e["value"] for e in row["env"]}
    assert env["MESHPOOL_STOP_FILE"] == str(ACP.stop_file("chat-y"))


# ---------- meshpool acp (Meshpool's own agent served over ACP) -------------------------------------------------

def test_m36_01_a_prompt_right_after_cancel_does_not_revive_the_cancelled_turn(home):
    from meshpool_harness.agent import acp_server as AS
    srv = AS.Server()
    replies, running, overlap = {}, [], []

    class Stub:
        _stop = False

        def send(self, text):
            self._stop = False                                  # as Agent.send does
            running.append(text)
            if len(running) > 1:
                overlap.append(list(running))
            t0 = time.time()
            while "long" in text and not self._stop and time.time() - t0 < 2:
                time.sleep(0.02)
            running.remove(text)

        def stop(self):
            self._stop = True
    srv.reply = lambda rid, result=None, error=None: replies.__setitem__(rid, result or error)
    srv.sessions["s"] = {"id": "s", "busy": False, "agent": Stub(), "calls": {}}
    srv.handle({"jsonrpc": "2.0", "id": 1, "method": "session/prompt",
                "params": {"sessionId": "s", "prompt": [{"type": "text", "text": "long task"}]}})
    time.sleep(0.2)
    srv.handle({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "s"}})
    srv.handle({"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
                "params": {"sessionId": "s", "prompt": [{"type": "text", "text": "short one"}]}})
    for _ in range(100):
        if len(replies) == 2:
            break
        time.sleep(0.05)
    assert replies[1] == {"stopReason": "cancelled"}           # was: end_turn, after running on beside prompt 2
    assert replies[2] == {"stopReason": "end_turn"}
    assert not overlap


# ---------- Pi --------------------------------------------------------------------------------------------------

@pytest.fixture()
def fake_pi(home, monkeypatch):
    from meshpool_harness.harnesses import pi_rpc
    script = home / "fake_pi.py"
    script.write_text(FAKE_PI)
    log = home / "pi.jsonl"
    monkeypatch.setenv("FAKE_PI_LOG", str(log))
    monkeypatch.setattr(pi_rpc, "node_exe", lambda: sys.executable)
    monkeypatch.setattr(pi_rpc, "pi_cli", lambda: script)
    proj = home / "proj"
    proj.mkdir(exist_ok=True)
    agents = []

    def make(evs, **kw):
        a = pi_rpc.PiAgent({"id": "pi", "type": "pi"}, str(proj), emit=kw.pop("emit", evs.append), mode="auto", **kw)
        agents.append(a)
        return a

    def rows():
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    yield make, rows
    for a in agents:
        a.close()


def test_m24_01_adding_a_message_while_pi_streams_does_not_freeze_the_chat(fake_pi):
    make, _ = fake_pi
    chat_lock, evs = threading.RLock(), []                      # Session.lock, which Session.emit takes

    def emit(ev):
        with chat_lock:
            evs.append(ev)
    a = make(evs, emit=emit)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", a.send("STREAM please")), daemon=True)
    t.start()
    for _ in range(200):
        if any(e["type"] == "text_delta" for e in list(evs)):
            break
        time.sleep(0.05)
    with chat_lock:                                             # Session.send steers under its lock
        time.sleep(0.3)                                         # Pi streams on: its next event waits for the lock
        t0 = time.time()
        a.steer("and this too")
        took = time.time() - t0
    t.join(20)
    assert took < 5                                             # was: 30 s, then PiError
    assert [e for e in evs if e["type"] == "user" and e.get("steer")][0]["text"] == "and this too"
    assert "and this too" in out["r"]                           # Pi got it and answered it
    assert not any(e["type"] == "error" for e in evs)


def test_m24_02_a_pi_crash_mid_turn_is_reported_and_resumed(fake_pi):
    make, rows = fake_pi
    evs = []
    a = make(evs)
    r = a.send("CRASH now")
    starts = [x for x in rows() if x.get("start")]
    assert len(starts) == 2 and starts[1]["continue"]           # restarted on its own session
    assert any(e["type"] == "status" and "stopped unexpectedly" in e["text"] for e in evs)
    assert "Carry on from where you left off" in r              # the answer comes from the resumed run
    assert not any(e["type"] == "error" for e in evs)


def test_m24_02_a_second_crash_in_the_same_turn_is_an_error(fake_pi, monkeypatch):
    make, rows = fake_pi
    monkeypatch.setenv("FAKE_PI_CRASH_ALWAYS", "1")
    evs = []
    a = make(evs)
    a.send("CRASH twice")
    assert len([x for x in rows() if x.get("start")]) == 2      # restarted once, not forever
    assert any(e["type"] == "error" and "exited again" in e["text"] for e in evs)


def test_m24_04_a_message_added_while_pi_starts_is_not_refused(fake_pi, monkeypatch):
    from meshpool_harness.harnesses import pi_rpc
    make, rows = fake_pi
    real = pi_rpc.prepare
    monkeypatch.setattr(pi_rpc, "prepare", lambda root, chat="": (time.sleep(1.0), real(root, chat))[1])
    evs = []
    a = make(evs)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", a.send("hello")), daemon=True)
    t.start()
    time.sleep(0.3)
    a.steer("early addition")                                   # was: PiError "Pi stopped" (a 500 in the app)
    t.join(20)
    assert "early addition" in out["r"]
    assert len([e for e in evs if e["type"] == "user" and e.get("steer")]) == 1


def test_pi_a_message_pi_queued_after_its_run_ended_is_answered(fake_pi):
    make, rows = fake_pi
    evs = []
    a = make(evs)
    assert a.send("hello").startswith("echo:hello")
    a.steer("late one")                                         # Pi had just finished: it queues it
    assert a.has_steers()
    a.resume_steers()                                           # what the chat does when the turn ends
    assert not a.has_steers()
    assert any(e["type"] == "assistant" and "late one" in e["text"] for e in evs)
    assert len([e for e in evs if e["type"] == "user"]) == 2    # the added message is shown once, not again


def test_m24_03_each_pi_chat_gets_its_own_config(home, monkeypatch):
    from meshpool_harness.agent import mcp as M
    from meshpool_harness.harnesses import pi_rpc
    monkeypatch.setattr(M, "active_config", lambda path=None: {
        "meshpool-assistant": {"command": "python", "args": ["-m", "meshpool_harness.agent.assistant_mcp_server"]}})
    monkeypatch.setattr(M, "recently_failed", lambda name, spec: None)
    p1, p2 = home / "p1", home / "p2"
    p1.mkdir(), p2.mkdir()
    out, errors = {}, []

    def go(root, chat):
        try:
            out[chat] = pi_rpc.prepare(root, chat)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=go, args=(p, c)) for p, c in ((p1, "c1"), (p2, "c2"))]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not errors and out["c1"] != out["c2"]
    for chat, root in (("c1", p1), ("c2", p2)):
        mcp = json.loads((out[chat] / "mcp.json").read_text())
        env = mcp["mcpServers"]["meshpool-assistant"]["env"]
        assert env["MESHPOOL_PROJECT"] == str(root.resolve())   # was: whichever chat launched last
        settings = json.loads((out[chat] / "settings.json").read_text())
        assert settings["skills"] == [str(pi_rpc.pi_home() / "skills" / "meshpool")]


def test_m05_05_undo_in_a_pi_chat_restarts_pi_without_the_undone_turns(fake_pi):
    make, rows = fake_pi
    evs = []
    a = make(evs)
    a.send("keep this")
    a.send("undo this")
    a.events = [e for e in a.events if "undo this" not in json.dumps(e)]   # what Session.revert leaves
    a.forget_session()
    r = a.send("third")
    starts = [x for x in rows() if x.get("start")]
    assert not starts[-1]["continue"]                           # a new Pi session ...
    prompt = [x for x in rows() if x.get("cmd") == "prompt"][-1]["message"]
    assert "keep this" in prompt and "undo this" not in prompt  # ... that knows the chat as it now is
    assert r.startswith("echo:")


def test_pi_stop_while_starting_sends_nothing(fake_pi, monkeypatch):
    from meshpool_harness.harnesses import pi_rpc
    make, rows = fake_pi
    real = pi_rpc.prepare
    monkeypatch.setattr(pi_rpc, "prepare", lambda root, chat="": (time.sleep(0.8), real(root, chat))[1])
    evs = []
    a = make(evs)
    t = threading.Thread(target=a.send, args=("hello",), daemon=True)
    t.start()
    time.sleep(0.2)
    a.stop()
    t.join(20)
    assert not [x for x in rows() if x.get("cmd") == "prompt"]
    assert any(e["type"] == "interrupted" for e in evs) and not any(e["type"] == "error" for e in evs)


def test_m03_02_a_message_queued_after_stop_runs_and_its_chip_clears(fake_acp, monkeypatch):
    from meshpool_harness import harnesses as HR
    from meshpool_harness.ui import agent_api as A
    make, calls, manifest = fake_acp
    monkeypatch.setenv("FAKE_CANCEL_DELAY", "1.0")              # the stopped turn takes a moment to wind down
    monkeypatch.setenv("MH_AUTO_RESUME", "0")
    HR.add(json.dumps({k: manifest[k] for k in ("id", "name", "mode", "command", "args", "office", "share_mcp")}))
    s = A.Session(str(Path(manifest["args"][0]).parent / "proj"), harness="fake", kind="cowork")
    try:
        for _ in range(100):
            if s.state == "idle":
                break
            time.sleep(0.05)
        s.send("SLOW job")
        for _ in range(200):
            if calls("session/prompt"):
                break
            time.sleep(0.05)
        s.stop()
        r = s.send("next one")
        assert r["queued"]                                      # the user is told it will run
        for _ in range(300):
            if s.state == "idle" and len(calls("session/prompt")) >= 2:
                break
            time.sleep(0.05)
        texts = [e.get("text", "") for e in s.events if e.get("type") == "assistant"]
        assert any("fresh:next one" in t for t in texts)        # was: dropped silently
        assert [e for e in s.events if e.get("type") == "queued"][-1]["n"] == 0   # the chip clears
    finally:
        s.close()
