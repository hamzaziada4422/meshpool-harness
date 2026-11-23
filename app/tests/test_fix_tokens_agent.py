"""Bounds on what every model call carries before the conversation starts (offline, characters / 3.5 = tokens)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import core  # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MH_CODEX_PROMPT", raising=False)
    monkeypatch.setenv("MH_ENGINE_STYLE", "codex")


def _prefix_tokens(agent) -> float:
    body = agent._body()
    chars = sum(len(m.get("content") or "") for m in body["messages"] if m["role"] == "system")
    return (chars + len(json.dumps(body.get("tools") or []))) / 3.5


def _agent(tmp_path, profile="code"):
    return core.Agent(str(tmp_path), mode="auto", profile=profile, sessions_dir=str(tmp_path / "s"))


def test_codex_engine_prefix_is_bounded(tmp_path):
    a = _agent(tmp_path)
    assert a._style()
    assert _prefix_tokens(a) < 5000                 # was about 7000 before the prompt was trimmed
    names = [t["function"]["name"] for t in a.tool_schemas()]
    assert set(names) == {"shell", "apply_patch", "update_plan"}


def test_trimmed_prompt_keeps_the_rules_that_matter():
    text = core._codex_prompt()
    for must in ("apply_patch", "update_plan", "root cause", "Do not guess", "Validating your work", "# Tool Guidelines"):
        assert must.lower() in text.lower(), must
    assert "Preamble messages" not in text and "High-quality plans" not in text
    assert len(text) < 16500


def test_full_prompt_is_still_available(monkeypatch):
    monkeypatch.setenv("MH_CODEX_PROMPT", "full")
    assert "Preamble messages" in core._codex_prompt()


def test_non_codex_lanes_prefix_does_not_grow(tmp_path):
    assert _prefix_tokens(_agent(tmp_path, "work")) < 7500


# ---- request shape per wire, against a local fake server and the Anthropic translator -------------------------------

def _fake_server(captured):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            captured.append((self.path, dict(self.headers), self.rfile.read(n).decode("utf-8")))
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 1}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_plain_openai_compatible_server_gets_no_cache_fields(tmp_path, monkeypatch):
    got = []
    srv = _fake_server(got)
    base = f"http://127.0.0.1:{srv.server_port}/v1"
    monkeypatch.setenv("MESHPOOL_BASE_URL", base)
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    try:
        a = _agent(tmp_path)
        body = a._body()
        assert "prompt_cache_key" in body                      # the agent adds the hint ...
        resp, _ = a._post(base, "k", body, False)
        sent = json.loads(got[-1][2])
        assert "prompt_cache_key" not in sent and "user" not in sent       # ... and drops it for an unknown server
        assert "cache_control" not in got[-1][2]
        assert resp["choices"][0]["message"]["content"] == "ok"
    finally:
        srv.shutdown()


def test_anthropic_request_has_cache_breakpoints_and_tolerates_missing_usage():
    from meshpool_harness.core import anthropic_wire as AW
    body = {"model": "m", "max_tokens": 100, "messages": [
        {"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                              "function": {"name": "shell", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "out"}],
        "tools": [{"type": "function", "function": {"name": "shell", "parameters": {"type": "object"}}},
                  {"type": "function", "function": {"name": "apply_patch", "parameters": {"type": "object"}}}]}
    out = AW.to_request(dict(body))
    assert out["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert out["tools"][-1]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in out["tools"][0]
    assert out["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert json.dumps(out).count("cache_control") == 3
    # the reply without any cache fields still gives a usable usage block
    r = AW.from_response({"content": [{"type": "text", "text": "x"}], "usage": {"input_tokens": 5, "output_tokens": 2}})
    assert r["usage"]["prompt_tokens"] == 5 and r["usage"]["prompt_tokens_details"]["cached_tokens"] == 0


def test_anthropic_cache_markers_can_be_turned_off(monkeypatch):
    from meshpool_harness.core import anthropic_wire as AW
    monkeypatch.setenv("MH_ANTHROPIC_CACHE", "0")
    out = AW.to_request({"model": "m", "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                         "tools": [{"type": "function", "function": {"name": "t", "parameters": {}}}]})
    assert "cache_control" not in json.dumps(out)
