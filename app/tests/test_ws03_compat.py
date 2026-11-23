"""ws03 compat (W-02 W-03 W-04 S-11 H-02 H-05 H-06 H-09 H-19 H-21 G-14 P-02 P-06): the real Agent against real HTTP
mock servers that answer the way Ollama, vLLM, Gemini, Mistral and strict gateways answer. No model, no network."""
import http.server
import json
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core import modelcaps as MC  # noqa: E402

_REAL_PROBE = Agent._probe                                        # before conftest stubs it per test
MODEL = "mock-model"


class Server:
    """A chat-completions server on 127.0.0.1. chat(body, n) -> (status, json | str); models: the /models answer."""

    def __init__(self, chat, models=None, tls=None):
        self.reqs, self.times, self.chat, self.models = [], [], chat, models
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _send(self, code, obj):
                raw = (obj if isinstance(obj, str) else json.dumps(obj)).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if outer.models is not None and self.path.split("?")[0].rstrip("/").endswith("/models"):
                    return self._send(200, outer.models)
                self._send(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.reqs.append(body)
                outer.times.append(time.time())
                code, obj = outer.chat(body, len(outer.reqs))
                self._send(code, obj)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        if tls:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.url = f"{'https' if tls else 'http'}://127.0.0.1:{self.port}/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def chats(self):
        return [r for r in self.reqs if "messages" in r]


def reply(text="", calls=None, prompt_tokens=100, finish=None):
    msg = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = calls
    return 200, {"choices": [{"message": msg, "finish_reason": finish or ("tool_calls" if calls else "stop")}],
                 "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 10}}


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_AGENT_V3", "1")
    monkeypatch.setenv("MH_STREAM", "0")
    monkeypatch.setenv("MH_PROBE_CONTEXT", "0")
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "128000")          # a test sets its own when the window matters
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    sleeps = []
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: sleeps.append(s))
    servers = []
    from meshpool_harness.core import context as CTX
    CTX.forget()                                                    # windows learned by an earlier test
    core._REFUSED.clear()

    def serve(chat, models=None, tls=None):
        s = Server(chat, models, tls)
        servers.append(s)
        monkeypatch.setenv("MESHPOOL_BASE_URL", s.url)
        return s
    serve.sleeps = sleeps
    serve.tmp = tmp_path
    yield serve
    for s in servers:
        s.close()
    CTX.forget()
    core._REFUSED.clear()


def make_agent(tmp_path, model=MODEL):
    a = Agent(str(tmp_path / "proj"), mode="auto") if (tmp_path / "proj").exists() else Agent(str(tmp_path), mode="auto")
    a.model = model
    return a


def turn():
    return {"in": 0, "cached": 0, "out": 0, "calls": 0}


def has_tools(r):
    return bool(r.get("tools"))


# ----------------------------------------------------------------------------------------------------- W-02

def test_w02_ollama_without_tools_falls_back_to_text_tools(env, tmp_path):
    (tmp_path / "hello.txt").write_text("the secret word is plum")

    def chat(body, n):
        if body.get("tools"):
            return 400, {"error": "registry.ollama.ai/library/gemma:2b does not support tools"}
        if "the secret word is plum" not in json.dumps(body["messages"]):
            return reply('Let me look.\n<tool_call>{"name": "read_file", "arguments": {"path": "hello.txt"}}</tool_call>')
        return reply("The word is plum.")
    srv = env(chat)
    a = make_agent(tmp_path)
    assert "plum" in a.send("what is in hello.txt?")
    rs = srv.chats()
    assert has_tools(rs[0]) and not any(has_tools(r) for r in rs[1:])      # one refused request, then no tools
    assert len(rs) == 3
    assert any(m["role"] == "tool" and "plum" in str(m["content"]) for m in a.messages)    # the tool really ran
    MC.reset()                                                      # a new process: only the disk file is left
    srv.reqs.clear()
    b = make_agent(tmp_path)
    b.send("again")
    assert not has_tools(srv.chats()[0])                            # learned and persisted: nothing is refused twice


def test_w02_vllm_auto_tool_choice_error_falls_back(env, tmp_path):
    def chat(body, n):
        if body.get("tools") or body.get("tool_choice"):
            return 400, {"object": "error", "message": "\"auto\" tool choice requires --enable-auto-tool-choice and "
                         "--tool-call-parser to be set", "type": "BadRequestError", "param": None, "code": 400}
        return reply("plain answer")
    srv = env(chat)
    a = make_agent(tmp_path)
    assert a.send("hello") == "plain answer"
    rs = srv.chats()
    assert len(rs) == 2 and has_tools(rs[0]) and not has_tools(rs[1]) and "tool_choice" not in rs[1]
    assert not [x for x in env.sleeps if x >= 1]                    # no backoff: the fix was named, not guessed


def test_w02_text_tool_protocol_is_described_to_the_model(env, tmp_path):
    def chat(body, n):
        return reply("ok") if not body.get("tools") else (400, "HTTP 400: does not support tools")
    srv = env(chat)
    make_agent(tmp_path).send("hello")
    sys_text = json.dumps(srv.chats()[-1]["messages"][0])
    assert "<tool_call>" in sys_text and "read_file" in sys_text    # the model is told how to ask for a tool


# ----------------------------------------------------------------------------------------------------- W-03

def _cap_server(env, limit=4096):
    def chat(body, n):
        mt = body.get("max_tokens") or body.get("max_completion_tokens") or 0
        if mt > limit:
            return 400, {"error": {"message": f"max_tokens is too large: {mt}. This model supports at most {limit} "
                                   f"completion tokens, whereas you provided {mt}.", "type": "invalid_request_error"}}
        return reply("done")
    return env(chat)


def test_w03_output_cap_is_learned_without_compacting(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "128000")
    srv = _cap_server(env)
    a = make_agent(tmp_path)
    events = []
    a.emit = events.append
    for i in range(3):
        a.messages += [{"role": "user", "content": f"old question {i}"}, {"role": "assistant", "content": f"old answer {i}"}]
    n0 = len(a.messages)
    assert a.send("hello") == "done"
    rs = srv.chats()
    assert len(rs) == 2 and rs[0]["max_tokens"] > 4096 and rs[1]["max_tokens"] == 4096
    assert len(a.messages) == n0 + 2                                # nothing summarized, nothing dropped
    assert not [e for e in events if e.get("type") == "status" and re.search(r"longer than the model|were shortened|summarized\b(?! above)", e["text"])]
    MC.reset()
    srv.reqs.clear()
    make_agent(tmp_path).send("again")
    assert [r["max_tokens"] for r in srv.chats()] == [4096]         # the next chat is right the first time


def test_w03_vllm_small_window_output_error_lowers_max_tokens(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "128000")

    def chat(body, n):
        mt = body.get("max_tokens") or body.get("max_completion_tokens") or 0
        if mt > 4096 - 120:
            return 400, {"object": "error", "message": f"'max_tokens' or 'max_completion_tokens' is too large: {mt}. This "
                         f"model's maximum context length is 4096 tokens and your request has 120 input tokens "
                         f"({mt} > 4096 - 120).", "type": "BadRequestError", "param": None, "code": 400}
        return reply("done")
    srv = env(chat)
    a = make_agent(tmp_path)
    a.messages += [{"role": "user", "content": "old question"}, {"role": "assistant", "content": "old answer"}]
    n0 = len(a.messages)
    assert a.send("hello") == "done"
    rs = srv.chats()
    assert len(rs) == 2 and rs[1]["max_tokens"] <= 4096 - 120 and len(a.messages) == n0 + 2


def test_w03_models_endpoint_window_makes_the_first_request_fit(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MH_PROBE_CONTEXT", "1")
    monkeypatch.delenv("MESHPOOL_CONTEXT_WINDOW")

    def chat(body, n):
        mt = body.get("max_tokens") or body.get("max_completion_tokens") or 0
        if mt > 4096 - 120:
            return 400, {"object": "error", "message": "too large (max_tokens > 4096 - 120)"}
        return reply("done")
    srv = env(chat, models={"object": "list", "data": [{"id": MODEL, "object": "model", "max_model_len": 4096}]})
    assert make_agent(tmp_path).send("hello") == "done"
    rs = srv.chats()
    assert len(rs) == 1 and rs[0]["max_tokens"] <= 4096 - 120       # vLLM's max_model_len was read: no refused call


# ----------------------------------------------------------------------------------------------------- W-04

GEMINI = {"error": {"code": 400, "message": "Invalid JSON payload received. Unknown name \"prompt_cache_key\": Cannot find "
          "field.\nInvalid JSON payload received. Unknown name \"user\": Cannot find field.", "status": "INVALID_ARGUMENT"}}
PYDANTIC = {"detail": [{"type": "extra_forbidden", "loc": ["body", "prompt_cache_key"], "msg": "Extra inputs are not "
            "permitted", "input": "x"}]}


def _strict(env, monkeypatch, code, err):
    monkeypatch.setenv("MH_CAPS", '{"cache_hints": true}')           # the endpoint is sent the cache fields
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "128000")

    def chat(body, n):
        return (code, err) if "prompt_cache_key" in body else reply("done")
    return env(chat)


@pytest.mark.parametrize("code,err", [(400, GEMINI), (422, PYDANTIC)], ids=["gemini", "pydantic422"])
def test_w04_refused_field_is_dropped_once_without_backoff(env, tmp_path, monkeypatch, code, err):
    srv = _strict(env, monkeypatch, code, err)
    t0 = time.time()
    assert make_agent(tmp_path).send("hello") == "done"
    rs = srv.chats()
    assert len(rs) == 2 and "prompt_cache_key" in rs[0] and "prompt_cache_key" not in rs[1]
    assert time.time() - t0 < 2 and not [x for x in env.sleeps if x >= 1]
    MC.reset()
    srv.reqs.clear()
    make_agent(tmp_path).send("again")
    assert len(srv.chats()) == 1                                    # remembered on disk: one request


def test_w04_gemini_unknown_prompt_cache_key_is_dropped(env, tmp_path, monkeypatch):
    srv = _strict(env, monkeypatch, 400, GEMINI)
    a = make_agent(tmp_path)
    assert a.send("hello") == "done" and len(srv.chats()) == 2


def test_w04_422_extra_forbidden_is_not_resent_with_backoff(env, tmp_path, monkeypatch):
    srv = _strict(env, monkeypatch, 422, PYDANTIC)
    a = make_agent(tmp_path)
    assert a.send("hello") == "done" and len(srv.chats()) == 2 and not [x for x in env.sleeps if x >= 1]


def test_w04_unfixable_422_raises_at_once(env, tmp_path):
    srv = env(lambda body, n: (422, {"detail": "Unprocessable Entity"}))
    a = make_agent(tmp_path)
    with pytest.raises(RuntimeError, match="422"):
        a._chat(turn())
    assert len(srv.chats()) == 1 and not env.sleeps


def test_w04_the_same_refusal_after_its_fix_is_reported(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MH_CAPS", '{"cache_hints": true}')
    srv = env(lambda body, n: (422, PYDANTIC))                      # refuses the field even when it is gone
    a = make_agent(tmp_path)
    with pytest.raises(RuntimeError, match="422"):
        a._chat(turn())
    assert len(srv.chats()) <= 3 and not [x for x in env.sleeps if x >= 1]


# ----------------------------------------------------------------------------------------------------- S-11

def _selfsigned(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("openssl"):
        pytest.skip("openssl is not installed")
    crt, key = str(tmp_path / "c.pem"), str(tmp_path / "k.pem")
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
                        "-days", "1", "-keyout", key, "-out", crt], capture_output=True)
    if r.returncode:
        pytest.skip("openssl could not make a certificate")
    return crt, key


def test_s11_self_signed_tls_fails_fast(env, tmp_path, monkeypatch):
    srv = env(lambda body, n: reply("never"), tls=_selfsigned(tmp_path))
    monkeypatch.setenv("MH_RECOVER_MIN", "30")                      # an outage would wait half an hour
    monkeypatch.setattr(Agent, "_probe", _REAL_PROBE)
    a = make_agent(tmp_path)
    t0 = time.time()
    with pytest.raises(RuntimeError, match="(?i)certificate"):
        a._chat(turn())
    assert time.time() - t0 < 10 and not srv.reqs and not [x for x in env.sleeps if x >= 1]


def test_s11_probe_reports_tls_and_the_wait_stops(env, tmp_path, monkeypatch):
    from meshpool_harness.core import gateway as GW
    srv = env(lambda body, n: reply("never"), tls=_selfsigned(tmp_path))
    got = core._probe_once(srv.url, "k", MODEL)
    assert got.startswith("tls")
    h = GW.health(srv.url, "k", MODEL)
    t0 = time.time()
    with pytest.raises(RuntimeError, match="(?i)certificate"):
        h.check(lambda: got)
    h2 = GW.health(srv.url, "k2", MODEL)
    h2._mark_down()
    with pytest.raises(RuntimeError, match="(?i)certificate"):
        h2.wait_until_up(lambda: got, lambda s: None, lambda e: None, 1800, lambda: None)
    assert time.time() - t0 < 5


def test_s11_live_client_names_the_certificate(env, tmp_path, monkeypatch):
    from meshpool_harness.core.client import LiveClient
    srv = env(lambda body, n: reply("never"), tls=_selfsigned(tmp_path))
    t0 = time.time()
    with pytest.raises(RuntimeError, match="(?i)certificate"):
        LiveClient("phase_2_build").chat(model=MODEL, messages=[{"role": "user", "content": "hi"}])
    assert time.time() - t0 < 10 and not srv.reqs


# ----------------------------------------------------------------------------------------------------- H-02

def test_h02_ollama_cut_prompt_is_resent_fitted(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "32768")          # the harness believes 32k; Ollama's num_ctx is 2048

    def chat(body, n):
        est = int(len(json.dumps(body["messages"])) / 3.5)
        return reply("seen", prompt_tokens=min(est, 2048))          # Ollama keeps the last 2048 tokens, no error
    srv = env(chat)
    a = make_agent(tmp_path)
    events = []
    a.emit = events.append
    a.messages += [{"role": "user", "content": "background " + "lorem ipsum " * 5000}, {"role": "assistant", "content": "ok"}]
    assert a.send("what did I paste?") == "seen"
    rs = srv.chats()
    assert len(rs) == 2 and len(json.dumps(rs[1])) < len(json.dumps(rs[0])) * 0.6
    assert [e for e in events if e.get("type") == "status" and "OLLAMA_CONTEXT_LENGTH" in e["text"]]
    assert sum(1 for e in events if e.get("type") == "assistant") == 1     # the cut reply was never shown
    assert a.messages[-1]["content"] == "seen"


def test_h02_a_still_cut_prompt_keeps_the_second_reply(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "32768")
    srv = env(lambda body, n: reply(f"r{n}", prompt_tokens=700))    # always far below what was sent
    a = make_agent(tmp_path)
    a.messages += [{"role": "user", "content": "x " * 20000}, {"role": "assistant", "content": "ok"}]
    assert a.send("go") == "r2" and len(srv.chats()) == 2           # one resend, never a loop


# ----------------------------------------------------------------------------------------------------- H-05

def _strict_template(body, n):
    prev = None
    for i, m in enumerate(body["messages"]):
        if m["role"] == "system" and i:
            return 400, {"error": {"message": "System message must be at the beginning."}}
        if m["role"] in ("user", "assistant") and m["role"] == prev:
            return 400, {"error": {"message": "Conversation roles must alternate user/assistant/user/assistant/..."}}
        prev = m["role"] if m["role"] != "system" else prev
    return reply("fine")


def test_h05_two_user_messages_are_merged_for_strict_templates(env, tmp_path):
    srv = env(_strict_template)
    a = make_agent(tmp_path)
    a.messages += [{"role": "user", "content": "start"}, {"role": "assistant", "content": "ok"},
                   {"role": "user", "content": "do the thing"}, {"role": "user", "content": "[Meshpool] note: be brief"}]
    n0 = len(a.messages)
    assert a.send("next") == "fine"
    rs = srv.chats()
    assert len(rs) == 1                                             # right the first time: normalisation is always on
    users = [m for m in rs[0]["messages"] if m["role"] == "user"]
    assert "[Meshpool] note: be brief" in users[-1]["content"] and "next" in users[-1]["content"]
    assert len(a.messages) == n0 + 2                                # the stored history is untouched


# ----------------------------------------------------------------------------------------------------- H-06

def test_h06_local_slow_first_byte_is_waited_for(env, tmp_path, monkeypatch):
    def chat(body, n):
        time.sleep(2)
        return reply("late but fine")
    srv = env(chat)
    for k in ("MH_AGENT_FIRST_BYTE_S", "MH_AGENT_IDLE_S"):
        monkeypatch.setenv(k, "1")
    a = make_agent(tmp_path)
    body = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}
    with pytest.raises(RuntimeError, match="(?i)timed out"):         # the old floor: a cloud-sized wait
        a._post(srv.url, "k", body, False)
    monkeypatch.setenv("MH_LOCAL_FIRST_BYTE_S", "6")
    monkeypatch.setenv("MH_LOCAL_IDLE_S", "6")
    resp, _s = a._post(srv.url, "k", body, False)
    assert resp["choices"][0]["message"]["content"] == "late but fine"
    cloud = "https://api.example.com/v1"                              # the same delay against a hosted endpoint
    assert MC.first_byte_timeout(cloud, MODEL, 1) == 1 and MC.first_byte_timeout(srv.url, MODEL, 1) == 6


def test_h06_probe_timeout_and_learned_first_byte(env, monkeypatch):
    monkeypatch.delenv("MH_LOCAL_PROBE_S", raising=False)
    assert MC.probe_timeout("http://127.0.0.1:1/v1", 30) == 120 and MC.probe_timeout("https://api.example.com/v1", 30) == 30
    MC.observe_first_byte("http://127.0.0.1:1/v1", "m", 100)
    assert MC.first_byte_timeout("http://127.0.0.1:1/v1", "m", 10) >= 300      # three times the slowest first byte seen


# ----------------------------------------------------------------------------------------------------- H-09

def _window_server(env, window):
    state = {"errors": 0}

    def chat(body, n):
        est = int((len(json.dumps(body["messages"])) + len(json.dumps(body.get("tools") or []))) / 3.5)
        if est + 64 > window:
            state["errors"] += 1
            return 400, {"error": {"message": f"This model's maximum context length is {window} tokens. However, your "
                                   f"messages resulted in {est} tokens. Please reduce the length of the messages."}}
        return reply("answered")
    srv = env(chat)
    srv.state = state
    return srv


def test_h09_overflow_that_compaction_cannot_fix_recovers(env, tmp_path, monkeypatch):
    monkeypatch.delenv("MESHPOOL_CONTEXT_WINDOW")                   # the harness guesses 8k for an unknown local model
    srv = _window_server(env, 3000)                                 # the server really holds less
    a = make_agent(tmp_path)
    assert a.send("PASTE " + "lorem ipsum dolor " * 11500) == "answered"       # 200k characters, the newest message
    assert srv.state["errors"] >= 1                                              # it did overflow first
    before = srv.state["errors"]
    MC.reset()
    assert a.send("and now a short question") == "answered"
    assert srv.state["errors"] == before                                         # the next message works first time


# ----------------------------------------------------------------------------------------------------- H-19

def _mistral(body, n):
    seen = []
    for m in body["messages"]:
        for c in m.get("tool_calls") or []:
            seen.append(c["id"])
            if not re.fullmatch(r"[A-Za-z0-9]{9}", c["id"]):
                return 400, {"object": "error", "message": f"Tool call id was {c['id']} but must be a-z, A-Z, 0-9, with "
                             "a length of 9.", "type": "invalid_request_error"}
    if len(seen) != len(set(seen)):
        return 400, {"object": "error", "message": "Tool call ids must be unique."}
    return reply("fine")


def _foreign_history(a):
    def tc(cid, name, args):
        return {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function", "function": {
            "name": name, "arguments": json.dumps(args)}}]}
    a.messages += [{"role": "user", "content": "start"},
                   tc("toolu_01AbCdEfGhIjKlMnOpQrStUv", "read_file", {"path": "a"}),
                   {"role": "tool", "tool_call_id": "toolu_01AbCdEfGhIjKlMnOpQrStUv", "content": "A"},
                   tc("call_0", "read_file", {"path": "b"}), {"role": "tool", "tool_call_id": "call_0", "content": "B"},
                   tc("call_0", "read_file", {"path": "c"}), {"role": "tool", "tool_call_id": "call_0", "content": "C"},
                   {"role": "assistant", "content": "read them"}]


def test_h19_tool_ids_from_another_provider_are_repaired(env, tmp_path):
    srv = env(_mistral)
    a = make_agent(tmp_path)
    _foreign_history(a)
    assert a.send("continue") == "fine"
    assert len(srv.chats()) == 2                                    # one refusal naming the id rule, then the repair
    MC.reset()
    srv.reqs.clear()
    b = make_agent(tmp_path)
    _foreign_history(b)
    assert b.send("continue") == "fine" and len(srv.chats()) == 1   # remembered: right the first time
    ids = [c["id"] for m in b.messages if m.get("tool_calls") for c in m["tool_calls"]]
    assert "toolu_01AbCdEfGhIjKlMnOpQrStUv" in ids                  # the stored history keeps its own ids


# ----------------------------------------------------------------------------------------------------- H-21

MCP_TOOL = {"type": "function", "function": {"name": "mcp__x__lookup", "description": "look something up", "parameters": {
    "$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "additionalProperties": False,
    "$defs": {"Opt": {"type": "object", "properties": {"k": {"type": "string", "format": "uri"}}}},
    "properties": {"q": {"type": "string"}, "opt": {"$ref": "#/$defs/Opt"}}, "required": ["q", "gone"]}}}


def _gemini_schema(body, n):
    def bad(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("additionalProperties", "$ref", "$defs", "$schema", "format") and path.endswith("parameters") \
                        or k in ("additionalProperties", "$ref", "$defs", "$schema"):
                    return path, k
                r = bad(v, f"{path}.{k}")
                if r:
                    return r
        elif isinstance(node, list):
            for i, v in enumerate(node):
                r = bad(v, f"{path}[{i}]")
                if r:
                    return r
    for i, t in enumerate(body.get("tools") or []):
        r = bad(t["function"]["parameters"], f"tools[{i}].function_declarations[0].parameters")
        if r:
            return 400, {"error": {"code": 400, "message": f"Invalid JSON payload received. Unknown name \"{r[1]}\" at "
                                   f"'{r[0]}': Cannot find field."}}
    return reply("fine")


def test_h21_mcp_schema_is_sanitized_for_gemini(env, tmp_path, monkeypatch):
    orig = Agent.tool_schemas
    monkeypatch.setattr(Agent, "tool_schemas", lambda self: orig(self) + [MCP_TOOL])
    srv = env(_gemini_schema)
    a = make_agent(tmp_path)
    assert a.send("hi") == "fine"
    rs = srv.chats()
    assert len(rs) <= 2
    mcp = [t for t in rs[-1]["tools"] if t["function"]["name"] == "mcp__x__lookup"][0]["function"]["parameters"]
    assert "$defs" not in json.dumps(mcp) and "$ref" not in json.dumps(mcp) and mcp["required"] == ["q"]
    MC.reset()
    srv.reqs.clear()
    assert make_agent(tmp_path).send("again") == "fine" and len(srv.chats()) == 1


# ----------------------------------------------------------------------------------------------------- G-14 / P-02

def _long_run(env, tmp_path, monkeypatch, window, n=40):
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", str(window))
    state = {"errors": 0}

    def chat(body, n_):
        est = int(len(json.dumps(body)) / 3.5)
        if est > window:
            state["errors"] += 1
            return 400, {"error": {"message": f"This model's maximum context length is {window} tokens. However, your "
                                   f"messages resulted in {est} tokens."}}
        last = body["messages"][-1]
        text = str(last.get("content"))
        m = re.match(r"read file (\d+)", text) if last["role"] == "user" else None
        if m and body.get("tools"):
            return reply("", [call("read_file", {"path": f"f{m.group(1)}.txt"}, f"id{m.group(1)}")])
        return reply("done")
    srv = env(chat)
    for i in range(n):
        (tmp_path / f"f{i}.txt").write_text((f"line {i} " * 30 + "\n") * 14)       # about 3000 characters each
    a = make_agent(tmp_path)
    for i in range(n):
        assert a.send(f"read file {i}")
    return srv, state, a


def test_g14_small_windows_run_40_exchanges(env, tmp_path, monkeypatch):
    srv, state, a = _long_run(env, tmp_path, monkeypatch, 8192)
    assert state["errors"] == 0 and len(srv.chats()) >= 80


def test_p02_32k_window_does_not_die_at_exchange_16(env, tmp_path, monkeypatch):
    srv, state, a = _long_run(env, tmp_path, monkeypatch, 32768)
    assert state["errors"] == 0 and len(srv.chats()) >= 80


# ----------------------------------------------------------------------------------------------------- P-06

def test_p06_image_prefix_stays_cacheable(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "1000000")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    a = make_agent(tmp_path)
    prev, fr = None, []
    for i in range(20):
        a.messages.append({"role": "assistant", "content": f"step {i}"})
        a.messages.append({"role": "user", "content": [
            {"type": "text", "text": f"Image returned by screenshot {i}:"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{i:04d}" + "A" * 3000}}]})
        cur = json.dumps(a._body()["messages"])
        if prev and i >= 6:                                         # once more than 4 images are in the chat
            common = len(__import__("os").path.commonprefix([prev, cur]))
            fr.append(common / len(prev))
        prev = cur
    assert sum(fr) / len(fr) >= 0.6                                 # the old cut moved every call: about 0.42


# ----------------------------------------------------------------------------------------------------- caps by host and port

def test_learned_facts_are_kept_per_host_and_port():
    MC.learn("http://127.0.0.1:11434/v1", "m", no_tools=True)
    assert MC.resolve("http://127.0.0.1:11434/v1", "m").no_tools
    assert not MC.resolve("http://127.0.0.1:8000/v1", "m").no_tools          # another server on the same machine
    MC.reset()
    assert MC.resolve("http://127.0.0.1:11434/v1", "m").no_tools             # read back from the file


# ----------------------------------------------------------------------------------------------------- LiveClient

def test_live_client_output_cap_is_learned_and_applied_first(env, monkeypatch):
    from meshpool_harness.core.client import LiveClient
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "128000")
    srv = _cap_server(env)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u" * 6000}]
    r = LiveClient("phase_2_build").chat(model=MODEL, messages=msgs, max_tokens=8192)
    assert r["choices"][0]["message"]["content"] == "done"
    rs = srv.chats()
    assert [x["max_tokens"] for x in rs][-1] == 4096 and rs[0]["messages"] == rs[1]["messages"]   # not shrunk
    MC.reset()
    srv.reqs.clear()
    LiveClient("phase_2_build").chat(model=MODEL, messages=msgs, max_tokens=8192)
    assert [x["max_tokens"] for x in srv.chats()] == [4096]


def test_live_client_vllm_output_error_is_not_a_long_conversation(env, monkeypatch):
    from meshpool_harness.core.client import LiveClient
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "128000")

    def chat(body, n):
        mt = body.get("max_tokens") or 0
        if mt > 4096 - 120:
            return 400, {"object": "error", "message": f"'max_tokens' or 'max_completion_tokens' is too large: {mt}. This "
                         f"model's maximum context length is 4096 tokens and your request has 120 input tokens "
                         f"({mt} > 4096 - 120)."}
        return reply("done")
    srv = env(chat)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u" * 6000}]
    LiveClient("phase_2_build").chat(model=MODEL, messages=msgs, max_tokens=8192)
    rs = srv.chats()
    assert len(rs) == 2 and rs[1]["max_tokens"] <= 4096 - 120 and rs[0]["messages"] == rs[1]["messages"]


def test_live_client_no_tools_is_learned_and_applied_first(env):
    from meshpool_harness.core.client import LiveClient
    srv = env(lambda body, n: (400, {"error": "x does not support tools"}) if body.get("tools") else reply("plain"))
    tools = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
    msgs = [{"role": "user", "content": "hi"}]
    LiveClient("phase_2_build").chat(model=MODEL, messages=msgs, tools=tools)
    assert [bool(r.get("tools")) for r in srv.chats()] == [True, False]
    MC.reset()
    srv.reqs.clear()
    LiveClient("phase_2_build").chat(model=MODEL, messages=msgs, tools=tools)
    assert [bool(r.get("tools")) for r in srv.chats()] == [False]


# ----------------------------------------------------------------------------------------------------- W-02 (/models)

def test_w02_models_without_tools_parameter_sends_no_tools_first(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MH_PROBE_CONTEXT", "1")
    monkeypatch.delenv("MESHPOOL_CONTEXT_WINDOW")
    MC.reset()
    srv = env(lambda body, n: (400, {"error": "x does not support tools"}) if body.get("tools") else reply("plain"),
              models={"object": "list", "data": [{"id": MODEL, "supported_parameters": ["temperature", "max_tokens"]}]})
    assert make_agent(tmp_path).send("hello") == "plain"
    rs = srv.chats()
    assert len(rs) == 1 and not has_tools(rs[0])                    # zero refused requests
