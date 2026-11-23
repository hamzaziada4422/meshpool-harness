"""Key endpoint setup: test (wire auto-detect, model list), save (no stale lists), logins found on this computer."""
import base64
import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from meshpool_harness import config as C
from meshpool_harness import provider_setup as PS
from meshpool_harness import providers as PV
from meshpool_harness.core import anthropic_wire as AW

ENV = ("MESHPOOL_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "MESHPOOL_BASE_URL",
       "MH_PROVIDER", "MH_MODEL", "MH_API_WIRE", "MH_HARNESS_CODE", "CODEX_HOME", "MH_SUBAGENT_MODEL")


class Mock:
    """A mock endpoint: style "openai" (Bearer, {data:[{id}]}), "anthropic" (x-api-key + version, display_name),
    "nolist" (404 on /models) and "v1only" (OpenAI style, only under /v1)."""

    def __init__(self, style, key="sk-good-key-1234"):
        self.style, self.key, self.seen = style, key, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                path = self.path.split("?")[0]
                bearer = (self.headers.get("Authorization") or "").replace("Bearer ", "")
                xkey = self.headers.get("x-api-key") or ""
                if path == "/redir":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/steal")
                    self.end_headers()
                    return
                if outer.style == "nolist" or not path.endswith("/models") or \
                        (outer.style == "v1only" and not path.startswith("/v1/")):
                    return self._j(404, {"error": "nope"})
                if outer.style == "anthropic":
                    if xkey != outer.key or not self.headers.get("anthropic-version"):
                        return self._j(401, {"type": "error", "error": {"type": "authentication_error"}})
                    return self._j(200, {"data": [{"type": "model", "id": "claude-x-1", "display_name": "Claude X"},
                                                  {"type": "model", "id": "claude-y-2", "display_name": "Claude Y"}],
                                         "has_more": False, "first_id": "claude-x-1", "last_id": "claude-y-2"})
                if bearer != outer.key:
                    return self._j(401, {"error": {"message": "bad key"}})
                self._j(200, {"object": "list", "data": [{"id": "gpt-b", "object": "model", "context_length": 8000},
                                                         {"id": "gpt-a", "object": "model"}]})

            def _j(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    for k in ENV:
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))        # no real claude on PATH
    PS._TESTS.clear()
    return tmp_path


@pytest.fixture
def servers():
    made = []

    def mk(style, key="sk-good-key-1234"):
        m = Mock(style, key)
        made.append(m)
        return m
    yield mk
    for m in made:
        m.close()


def test_normalise_base():
    n = PS.normalise_base
    assert n("https://api.openai.com") == "https://api.openai.com/v1"
    assert n("api.anthropic.com/") == "https://api.anthropic.com/v1"
    assert n("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1"
    assert n("https://llm.example.com/v1/") == "https://llm.example.com/v1"
    assert n("localhost:11434") == "http://localhost:11434"
    with pytest.raises(PS.SetupError):
        n("")


def test_detects_openai_wire_and_lists_models(home, servers):
    m = servers("openai")
    r = PS.test(m.url + "/v1", "sk-good-key-1234")
    assert r["ok"] and r["wire"] == "openai" and r["error"] == ""
    assert [x["id"] for x in r["models"]] == ["gpt-a", "gpt-b"]
    assert r["models"][1]["context"] == 8000 and r["latency_ms"] >= 0
    assert "sk-good" not in json.dumps(r)


def test_detects_anthropic_wire(home, servers):
    m = servers("anthropic")
    r = PS.test(m.url + "/v1", "sk-good-key-1234")
    assert r["ok"] and r["wire"] == "anthropic"
    assert r["models"][0] == {"id": "claude-x-1", "name": "Claude X"}
    paths = [h for p, h in m.seen]
    assert any(h.get("x-api-key") == "sk-good-key-1234" for h in paths)


def test_explicit_wire_probes_only_that_wire(home, servers):
    m = servers("anthropic")
    r = PS.test(m.url + "/v1", "sk-good-key-1234", wire="openai")
    assert not r["ok"] and "rejected (401)" in r["error"]
    assert all("x-api-key" not in h and "authorization" in h for p, h in m.seen)


def test_adds_v1_when_server_only_serves_it(home, servers):
    m = servers("v1only")
    m.key = "sk-good-key-1234"
    m.style = "v1only"
    r = PS.test(m.url, "sk-good-key-1234")
    assert r["ok"] and r["base_url"] == m.url + "/v1" and len(r["models"]) == 2


def test_plain_language_errors(home, servers):
    m = servers("openai")
    r = PS.test(m.url + "/v1", "sk-wrong-key-99999")
    assert not r["ok"] and r["error"] == "The key was rejected (401). Check it and try again."
    r = PS.test("http://127.0.0.1:9", "sk-whatever-1234")                      # nothing listens there
    assert not r["ok"] and r["error"].startswith("No server at that address")
    nl = servers("nolist")
    r = PS.test(nl.url + "/v1", "sk-whatever-1234")
    assert r["ok"] and r["models"] == [] and r["error"] == \
        "Connected, but this endpoint does not list models: type a model name."
    r = PS.test("https://api.example.com/v1", "")
    assert not r["ok"] and "Enter the API key" in r["error"]
    r = PS.test("https://api.example.com/v1", "sk-••••-masked")
    assert not r["ok"] and "masked" in r["error"]


def test_key_never_follows_a_redirect(home, servers):
    m = servers("openai")
    status, data, err = PS._get(m.url + "/redir", {"Authorization": "Bearer sk-secret-secret-1"})
    assert status == 302 and err == ""            # not followed: the key stays with the given host


def test_saved_key_used_only_for_the_same_endpoint(home, servers):
    m = servers("openai")
    PS.save(m.url + "/v1", "sk-good-key-1234", "openai", "gpt-a", allow_custom=True)
    assert PS.test(m.url + "/v1")["ok"]                                          # key omitted: the saved one
    r = PS.test("https://api.other.example/v1")                                  # other host: never the saved key
    assert not r["ok"] and "not sent anywhere else" in r["error"]
    assert not any("api.other" in p for p, h in m.seen)


def test_switching_endpoints_saves_without_stale_list_errors(home, servers):
    o, a = servers("openai"), servers("anthropic")
    PS.test(o.url + "/v1", "sk-good-key-1234")
    cur = PS.save(o.url + "/v1", "sk-good-key-1234", "openai", "gpt-a")
    assert cur["default_model"] == "gpt-a" and cur["wire"] == "openai"
    # the model list of the OpenAI endpoint is now stale for the new one; saving must not look at it
    r = PS.test(a.url + "/v1", "sk-good-key-1234")
    assert r["wire"] == "anthropic"
    cur = PS.save(a.url + "/v1", "sk-good-key-1234", "anthropic", "claude-x-1")
    assert cur["wire"] == "anthropic" and cur["default_model"] == "claude-x-1" and cur["base_url"] == a.url + "/v1"
    assert PV.key_wire() == "anthropic" and PV.key_base() == a.url + "/v1"
    # without a prior test at all, any non-empty model saves
    cur = PS.save(o.url + "/v1", "sk-good-key-1234", "openai", "whatever-model")
    assert cur["default_model"] == "whatever-model"


def test_save_validates_against_a_fresh_test_of_the_same_endpoint_only(home, servers):
    o = servers("openai")
    PS.test(o.url + "/v1", "sk-good-key-1234")
    with pytest.raises(PS.SetupError, match="not in this endpoint's model list"):
        PS.save(o.url + "/v1", "sk-good-key-1234", "openai", "typo-model")
    cur = PS.save(o.url + "/v1", "sk-good-key-1234", "openai", "typo-model", allow_custom=True)
    assert cur["default_model"] == "typo-model"
    with pytest.raises(PS.SetupError, match="Pick the default model"):
        PS.save(o.url + "/v1", "sk-good-key-1234", "openai", "  ")


def test_save_keeps_key_when_omitted_and_never_returns_it(home, servers):
    o = servers("openai")
    PS.save(o.url + "/v1", "sk-good-key-1234", "openai", "gpt-a", allow_custom=True)
    cur = PS.save(o.url + "/v1", None, "openai", "gpt-b", allow_custom=True)
    assert cur["key_set"] and cur["key_hint"] == "1234" and cur["default_model"] == "gpt-b"
    assert "sk-good" not in json.dumps(cur) and "sk-good" not in json.dumps(PS.current())
    assert C.read()["MESHPOOL_API_KEY"] == "sk-good-key-1234"
    # a different host without a key must not borrow the saved one
    with pytest.raises(PS.SetupError, match="Enter the API key"):
        PS.save("https://api.other.example/v1", None, "openai", "m")
    assert C.read()["MESHPOOL_BASE_URL"] == o.url + "/v1"                      # nothing half-saved


def test_save_clears_old_picker_cache_and_subagent_model(home, servers):
    from meshpool_harness.ui import workbench as W
    o = servers("openai")
    C.write({"MH_SUBAGENT_MODEL": "old-endpoint-model"})
    W._MODELS.update(t=9e12, key="x", rows=[{"id": "old"}], error="")
    PS.save(o.url + "/v1", "sk-good-key-1234", "openai", "gpt-a", allow_custom=True)
    assert W._MODELS["rows"] == [] and C.read()["MH_SUBAGENT_MODEL"] == ""


def test_existing_settings_still_work(home, monkeypatch):
    C.write({"MESHPOOL_API_KEY": "mk-123456789", "MESHPOOL_BASE_URL": "https://llm.meshpool.ai/v1",
             "MH_MODEL": "deepseek-v4.1-flash"})
    cur = PS.current()
    assert cur["wire"] == "openai" and cur["key_set"] and cur["key_hint"] == "6789"
    C.write({"MESHPOOL_BASE_URL": "https://api.anthropic.com/v1"})
    assert PS.current()["wire"] == "anthropic"                                  # guessed from the host


# ---------- logins -------------------------------------------------------------------------------------------------

def _jwt(claims):
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{b({'alg': 'none'})}.{b(claims)}.sig"


def test_codex_auth_json_detected_read_only(home):
    r = PS.chatgpt_login()
    assert r["kind"] == "chatgpt" and r["found"] is False
    (home / "codex").mkdir()
    f = home / "codex" / "auth.json"
    f.write_text(json.dumps({"tokens": {
        "access_token": _jwt({"exp": 9999999999}), "refresh_token": "rt-secret-1",
        "id_token": _jwt({"email": "sam@example.com",
                          "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}})}}))
    before = f.read_bytes()
    r = PS.chatgpt_login()
    assert r["found"] and r["usable"] and r["account"] == "sam@example.com" and r["plan"] == "plus"
    assert "rt-secret" not in json.dumps(PS.logins()) and f.read_bytes() == before
    f.write_text(json.dumps({"OPENAI_API_KEY": "sk-abc"}))
    r = PS.chatgpt_login()
    assert r["found"] and not r["usable"] and "API key" in r["note"]


def test_use_chatgpt_switches_provider(home):
    with pytest.raises(PS.SetupError, match="No ChatGPT sign-in"):
        PS.use("chatgpt")
    (home / "codex").mkdir()
    (home / "codex" / "auth.json").write_text(json.dumps({"tokens": {"access_token": _jwt({"exp": 9999999999}),
                                                                      "refresh_token": "rt"}}))
    r = PS.use("chatgpt")
    assert r["ok"] and r["provider"] == "chatgpt" and C.read()["MH_PROVIDER"] == "chatgpt"
    assert C.read()["MH_MODEL"].startswith("chatgpt:")


def test_env_keys_detected_and_copied_only_on_confirm(home, servers, monkeypatch):
    rows = {r["env"]: r for r in PS.env_keys()}
    assert not rows["ANTHROPIC_API_KEY"]["found"] and not rows["OPENAI_API_KEY"]["found"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-envkey-9876")
    rows = {r["env"]: r for r in PS.env_keys()}
    a = rows["ANTHROPIC_API_KEY"]
    assert a["found"] and a["wire"] == "anthropic" and a["base_url"] == "https://api.anthropic.com/v1"
    assert a["key_hint"] == "9876" and "envkey" not in json.dumps(PS.logins())
    m = servers("anthropic", key="sk-ant-envkey-9876")
    r = PS.test(m.url + "/v1", env="ANTHROPIC_API_KEY")                          # the key is resolved server-side
    assert r["ok"] and r["wire"] == "anthropic"
    with pytest.raises(PS.SetupError, match="Confirm"):
        PS.save(m.url + "/v1", None, "anthropic", "claude-x-1", env="ANTHROPIC_API_KEY")
    assert "MESHPOOL_API_KEY" not in C.read()
    PS.save(m.url + "/v1", None, "anthropic", "claude-x-1", env="ANTHROPIC_API_KEY", confirm_env=True)
    assert C.read()["MESHPOOL_API_KEY"] == "sk-ant-envkey-9876"


def test_claude_code_cli_detected_and_offered_as_agent(home, monkeypatch):
    assert PS.claude_code()["found"] is False
    from meshpool_harness import harnesses as HR
    assert "claude-agent" not in {h["id"] for h in HR.load_all()}
    b = home / "bin"
    b.mkdir()
    exe = b / "claude"
    exe.write_text("#!/bin/sh\necho '2.3.4 (Claude Code)'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    r = PS.claude_code()
    assert r["found"] and r["version"] == "2.3.4" and r["kind"] == "claude_code"
    row = next(h for h in HR.load_all() if h["id"] == "claude-agent")
    assert row["name"] == "Claude Code" and row["mode"] == "code" and "claude-agent-acp" in " ".join(row["args"])
    assert PS.use("claude_code")["harness"] == "claude-agent" and C.read()["MH_HARNESS_CODE"] == "claude-agent"
    with pytest.raises(PS.SetupError):
        PS.use("nonsense")


def test_claude_subscription_files_never_read(home, monkeypatch):
    (home / ".claude").mkdir()
    (home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "sk-ant-oat-secret"}}')
    monkeypatch.setenv("HOME", str(home))
    assert "oat-secret" not in json.dumps(PS.logins())


# ---------- the Anthropic wire --------------------------------------------------------------------------------------

def test_anthropic_wire_round_trip():
    body = {"model": "claude-x-1", "max_tokens": 100, "temperature": 0.2,
            "messages": [{"role": "system", "content": "be brief"},
                         {"role": "user", "content": "list files"},
                         {"role": "assistant", "content": None, "tool_calls": [
                             {"id": "t1", "type": "function", "function": {"name": "ls", "arguments": "{\"p\": \".\"}"}}]},
                         {"role": "tool", "tool_call_id": "t1", "content": "a.txt"},
                         {"role": "user", "content": "thanks"}],
            "tools": [{"type": "function", "function": {"name": "ls", "description": "d",
                                                        "parameters": {"type": "object", "properties": {}}}}]}
    r = AW.to_request(body)
    assert (r["system"] if isinstance(r["system"], str) else r["system"][0]["text"]) == "be brief" and r["max_tokens"] == 100 and r["tools"][0]["name"] == "ls"
    assert [m["role"] for m in r["messages"]] == ["user", "assistant", "user"]
    assert r["messages"][1]["content"][0] == {"type": "tool_use", "id": "t1", "name": "ls", "input": {"p": "."}}
    assert [b["type"] for b in r["messages"][2]["content"]] == ["tool_result", "text"]
    out = AW.from_response({"content": [{"type": "text", "text": "hi"},
                                        {"type": "tool_use", "id": "t2", "name": "ls", "input": {"p": "x"}}],
                            "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 3}})
    ch = out["choices"][0]
    assert ch["finish_reason"] == "tool_calls" and ch["message"]["content"] == "hi"
    assert json.loads(ch["message"]["tool_calls"][0]["function"]["arguments"]) == {"p": "x"}
    assert out["usage"]["prompt_tokens"] == 5 and out["usage"]["completion_tokens"] == 3


def test_simple_ledger_speaks_messages_on_an_anthropic_endpoint(home, monkeypatch):
    """A chat call on a saved Anthropic endpoint goes to /messages with x-api-key, not /chat/completions."""
    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen["path"], seen["h"] = self.path, {k.lower(): v for k, v in self.headers.items()}
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            b = json.dumps({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                            "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        PS.save(f"http://127.0.0.1:{srv.server_address[1]}/v1", "sk-ant-aaaa-1111", "anthropic", "claude-x-1")
        from meshpool_harness.core.wire import SimpleLedger
        r = SimpleLedger("t").chat(model="claude-x-1", messages=[{"role": "user", "content": "hi"}], max_tokens=5)
    finally:
        srv.shutdown()
    assert seen["path"] == "/v1/messages" and seen["h"].get("x-api-key") == "sk-ant-aaaa-1111"
    assert "authorization" not in seen["h"] and seen["body"]["messages"][0]["role"] == "user"
    assert r["choices"][0]["message"]["content"] == "ok"


def test_claude_code_not_offered_in_the_installed_app(monkeypatch, tmp_path):
    """Its ACP adapter would need npx, which the installed app never runs: hide it there (source checkouts keep it)."""
    from meshpool_harness import bundled
    from meshpool_harness import provider_setup as PS
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\necho 9.9.9\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + __import__("os").environ.get("PATH", ""))
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    assert PS.claude_code()["found"] is False


def test_the_chat_agent_speaks_the_messages_wire_to_an_anthropic_style_endpoint(tmp_path, monkeypatch):
    """Saving wire=anthropic must change what the agent loop sends, not only the connection test."""
    from meshpool_harness.agent.core import Agent
    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got["path"], got["hdr"] = self.path, {k.lower(): v for k, v in self.headers.items()}
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = json.dumps({"id": "m1", "model": "claude-x", "stop_reason": "end_turn",
                              "content": [{"type": "text", "text": "hello"}],
                              "usage": {"input_tokens": 5, "output_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    monkeypatch.setenv("MESHPOOL_BASE_URL", base)
    monkeypatch.setenv("MH_API_WIRE", "anthropic")
    a = Agent(str(tmp_path), mode="auto")
    resp, streamed = a._post(base, "sk-good-key-1234", {"model": "claude-x", "stream": True, "messages": [
        {"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]}, True)
    srv.shutdown()
    assert got["path"] == "/v1/messages" and got["hdr"]["x-api-key"] == "sk-good-key-1234"
    assert "authorization" not in got["hdr"] and "stream" not in got["body"]
    assert got["body"]["system"][0]["text"] == "be brief" and got["body"]["messages"][0]["role"] == "user"
    assert resp["choices"][0]["message"]["content"] == "hello" and streamed is False
