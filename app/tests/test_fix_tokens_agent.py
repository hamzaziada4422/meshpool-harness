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


def test_identical_call_with_identical_result_is_refused_the_fourth_time(tmp_path):
    a = _agent(tmp_path)
    runs = []
    a._run_tool = lambda c, cut=False: runs.append(1) or "[exit 1]\nsame output"
    g = {}
    call = {"id": "1", "function": {"name": "shell", "arguments": '{"command": ["bash", "-lc", "echo hi"]}'}}
    other = {"id": "2", "function": {"name": "apply_patch", "arguments": "{}"}}
    outs = [a._repeat_guard(call, g) for _ in range(6)]
    assert len(runs) == 3 and all("Not run" in o for o in outs[3:])
    # by default alternating with another call is not caught (the measured behaviour) ...
    runs.clear()
    g = {}
    seq = [a._repeat_guard(x, g) for _ in range(6) for x in (call, other)]
    assert len(runs) == 12 and not any("Not run" in o for o in seq)


def test_repeat_window_switch_catches_alternation_and_spares_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_REPEAT_WINDOW", "1")
    a = _agent(tmp_path)
    runs = []
    a._run_tool = lambda c, cut=False: runs.append(1) or "[exit 1]\nsame output"
    call = {"id": "1", "function": {"name": "shell", "arguments": '{"command": ["bash", "-lc", "echo hi"]}'}}
    other = {"id": "2", "function": {"name": "apply_patch", "arguments": "{}"}}
    g = {}
    seq = [a._repeat_guard(x, g) for _ in range(6) for x in (call, other)]
    assert len(runs) == 6 and sum("Not run" in o for o in seq) == 6
    runs.clear()
    g = {}
    for _ in range(8):                                 # a file changing in between is progress: never refused
        a._repeat_guard(call, g)
        a.ws.edits += 1
    assert len(runs) == 8


def test_a_long_run_without_progress_ends_with_a_best_effort_answer(tmp_path):
    a = _agent(tmp_path)
    g = {"hist": [], "doom_at": -10, "seen": set(), "edits": a.ws.edits, "edits0": a.ws.edits, "last": 0}
    note = ""
    for step in range(1, 40):
        note = a._v3_notes(step, [{"function": {"name": "shell", "arguments": f'{{"n": {step}}}'}}], g) or note
        if g.get("hard_wrap"):
            break
    assert g.get("hard_wrap") and "Do not call another tool" in note
    assert step == 36                                 # not before 36 steps without a changed file


def test_chat_lane_carries_fewer_tools_and_keeps_the_full_set_once_a_task_comes(tmp_path):
    full = _prefix_tokens(_agent(tmp_path, "work"))
    a = _agent(tmp_path, "work")
    a.lane = "chat"
    names = {t["function"]["name"] for t in a.tool_schemas()}
    assert "delegate_task" not in names and "todo_write" not in names and {"read_file", "bash", "web_search"} <= names
    lite = _prefix_tokens(a)
    assert lite < full - 1500                         # at least 1.5k tokens less than the full set
    a.lane = "task"
    assert len(a.tool_schemas()) > len(names)
    a.lane = "chat"
    assert len(a.tool_schemas()) > len(names)         # no flipping back: the prefix stays stable for the chat


# ---- ModelCaps: one capability layer, request shape per kind of endpoint -------------------------------------------

def _caps(base, model, **env):
    from meshpool_harness.core import modelcaps as MC
    MC.reset()
    return MC.resolve(base, model)


@pytest.mark.parametrize("base,model,wire,reasoning,cache,hints", [
    ("https://api.openai.com/v1", "gpt-5.6-terra", "chat", "effort", "auto", True),
    ("https://my.openai.azure.com/openai/v1", "my-gpt-5-deployment", "chat", "none", "auto", True),   # unknown name: safe
    ("https://api.anthropic.com/v1", "claude-sonnet-5.5", "messages", "none", "explicit", False),
    ("http://localhost:11434/v1", "llama3.1:8b", "chat", "none", "none", False),                      # Ollama
    ("http://localhost:1234/v1", "gpt-oss-20b", "chat", "none", "none", False),                       # LM Studio
    ("https://strict.example.com/v1", "some-model", "chat", "none", "none", False),
])
def test_modelcaps_per_endpoint_kind(base, model, wire, reasoning, cache, hints):
    c = _caps(base, model)
    assert (c.wire, c.reasoning, c.cache, c.cache_hints) == (wire, reasoning, cache, hints)


def test_reasoning_effort_is_explicit_low_for_chat_medium_for_work_and_overridable(monkeypatch):
    monkeypatch.setenv("MH_AUTO_EFFORT", "1")
    c = _caps("https://api.openai.com/v1", "gpt-5.6-terra")
    assert c.effort("chat") == "low" and c.effort("task") == "medium"
    monkeypatch.setenv("MH_REASONING_EFFORT", "high")
    assert c.effort("task") == "high"
    monkeypatch.setenv("MH_REASONING_EFFORT", "off")
    assert c.effort("task") == ""
    assert _caps("https://api.deepseek.com", "deepseek-v4.1-flash").effort("task") == ""   # no param it does not define


def test_user_override_beats_name_and_host_hints(monkeypatch):
    monkeypatch.setenv("MH_AUTO_EFFORT", "1")
    monkeypatch.setenv("MH_CAPS", json.dumps({"reasoning": "effort", "efforts": ["low", "high"], "cache": "none"}))
    c = _caps("https://strict.example.com/v1", "my-azure-deployment")
    assert c.reasoning == "effort" and c.effort("task") == "high" or c.effort("task") == "low"
    assert c.cache == "none" and c.source["reasoning"] == "user"


def test_body_carries_reasoning_effort_only_for_models_that_take_it(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_AUTO_EFFORT", "1")
    monkeypatch.setenv("MESHPOOL_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test")
    from meshpool_harness.core import modelcaps as MC
    MC.reset()
    a = _agent(tmp_path)
    a.model = "gpt-5.6-terra"
    assert a._body().get("reasoning_effort") == "medium"
    a.lane = "chat"
    assert a._body().get("reasoning_effort") == "low"
    a.model = "deepseek-v4.1-flash"
    assert "reasoning_effort" not in a._body()


def test_two_consecutive_bodies_share_a_byte_identical_prefix(tmp_path):
    a = _agent(tmp_path)
    b1 = a._body()
    a.messages.append({"role": "user", "content": "do the thing"})
    b2 = a._body()
    a.messages.append({"role": "assistant", "content": "ok"})
    a.messages.append({"role": "user", "content": "and then"})
    b3 = a._body()
    assert json.dumps(b1["tools"]) == json.dumps(b2["tools"]) == json.dumps(b3["tools"])
    p1 = json.dumps(b2["messages"][:-1])[:-1]
    assert json.dumps(b3["messages"]).startswith(p1)
    assert b1["messages"][0] == b2["messages"][0] == b3["messages"][0]       # the system prompt never changes


def test_strict_server_that_400s_on_a_field_is_retried_without_it_and_remembers(tmp_path, monkeypatch):
    import http.server
    import threading
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(sorted(body))
            if "reasoning_effort" in body:
                out, code = b'{"error":{"message":"Unrecognized request argument supplied: reasoning_effort"}}', 400
            else:
                out, code = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"},
                                                      "finish_reason": "stop"}], "usage": {"prompt_tokens": 5,
                                                      "completion_tokens": 1}}).encode(), 200
            self.send_response(code)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("MESHPOOL_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_CAPS", json.dumps({"reasoning": "effort", "efforts": ["low", "medium"]}))
    monkeypatch.setenv("MH_STREAM", "0")
    monkeypatch.setenv("MH_REASONING_EFFORT", "low")
    from meshpool_harness.core import modelcaps as MC
    MC.reset()
    core._REFUSED.clear()
    try:
        a = _agent(tmp_path)
        a.model = "strict-model"
        a.messages.append({"role": "user", "content": "hi"})
        a.emit = lambda ev: None
        msg, _ = a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
        assert msg["content"] == "ok"
        real = [x for x in seen if "messages" in x]
        assert "reasoning_effort" in real[0] and "reasoning_effort" not in real[-1]
        n = len([x for x in seen if "messages" in x])
        a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
        real = [x for x in seen if "messages" in x]
        assert len(real) == n + 1 and "reasoning_effort" not in real[-1]       # remembered: right the first time
    finally:
        srv.shutdown()
        core._REFUSED.clear()


def test_usage_with_reasoning_tokens_counts_all_output_tokens(tmp_path, monkeypatch):
    # completion_tokens already includes reasoning tokens on OpenAI/Azure; budgets must use that number
    from meshpool_harness.core import modelcaps as MC
    MC.reset()
    MC.observe_usage("https://api.openai.com/v1", "gpt-5.6-terra",
                     {"prompt_tokens": 100, "completion_tokens": 900,
                      "completion_tokens_details": {"reasoning_tokens": 800},
                      "prompt_tokens_details": {"cached_tokens": 64}})
    c = MC.resolve("https://api.openai.com/v1", "gpt-5.6-terra")
    assert c.usage_cached and c.usage_reasoning


def test_old_long_tool_call_arguments_are_stubbed_in_the_request_only(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_ELIDE_OLD_CALLS", "1")
    a = _agent(tmp_path)
    big = json.dumps({"input": "*** Begin Patch\n" + "+x = 1\n" * 400 + "*** End Patch"})
    def call(i, args):
        return {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function",
                                                                     "function": {"name": "apply_patch", "arguments": args}}]}
    for i in range(6):
        a.messages.append(call(i, big))
        a.messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
    msgs = a._body()["messages"]
    calls = [m for m in msgs if m.get("tool_calls")]
    assert len(json.dumps(calls[0])) < 600 and len(json.dumps(calls[-1])) > 2500       # old ones shortened, newest intact
    json.loads(calls[0]["tool_calls"][0]["function"]["arguments"])                       # still valid JSON for strict gateways
    assert len(a.messages[1]["tool_calls"][0]["function"]["arguments"]) > 2500          # the saved chat is untouched


def test_a_second_identical_failure_gets_a_change_of_approach_note(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    a._run_tool = lambda c, cut=False: "[exit 124]\ntimed out"
    c0 = {"id": "1", "function": {"name": "shell", "arguments": '{"command": ["bash", "-lc", "python slow.py"]}'}}
    g0 = {}
    a._repeat_guard(c0, g0)
    assert "Do not repeat it" not in a._repeat_guard(c0, g0)                           # default: off
    monkeypatch.setenv("MH_FAILURE_HINT", "1")
    a._run_tool = lambda c, cut=False: "[exit 124]\ntimed out"
    g = {}
    call = {"id": "1", "function": {"name": "shell", "arguments": '{"command": ["bash", "-lc", "python slow.py"]}'}}
    first = a._repeat_guard(call, g)
    second = a._repeat_guard(call, g)
    assert "Do not repeat it" not in first and "Do not repeat it" in second


def test_same_command_with_only_numbers_changed_counts_as_a_repeat(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    cmd = lambda n: {"id": str(n), "function": {"name": "shell", "arguments": json.dumps(  # noqa: E731
        {"command": ["bash", "-lc", f"timeout 10 python -c 'import sync; sync.fetch_all({n})'"]})}}
    a._run_tool = lambda c, cut=False: "[exit 124]\ntimed out"
    g = {}
    assert not any("Not run" in a._repeat_guard(cmd(n), g) for n in range(1, 9))      # default: off
    monkeypatch.setenv("MH_REPEAT_SHAPE", "1")
    a._run_tool = lambda c, cut=False: "[exit 124]\ntimed out"
    g = {}
    outs = [a._repeat_guard({"id": str(n), "function": {"name": "shell", "arguments": json.dumps(
        {"command": ["bash", "-lc", f"timeout 10 python -c 'import sync; sync.fetch_all({n})'"]})}}, g) for n in range(1, 9)]
    assert sum("Not run" in o for o in outs) >= 4


def test_finish_check_and_scan_rules_are_opt_in(monkeypatch):
    base = core._codex_prompt()
    assert "Before you finish" not in base and "find /" not in base
    monkeypatch.setenv("MH_FINISH_CHECK", "1")
    assert "Before you finish" in core._codex_prompt()
    monkeypatch.delenv("MH_FINISH_CHECK")
    monkeypatch.setenv("MH_SCAN_RULE", "1")
    assert "find /" in core._codex_prompt() and "Before you finish" not in core._codex_prompt()


def test_default_prompt_is_the_measured_one():
    assert len(core._codex_prompt()) == 14085          # the after2 build's system prompt, byte for byte in size


def test_auto_reasoning_effort_is_opt_in(monkeypatch):
    c = _caps("https://api.openai.com/v1", "gpt-5.6-terra")
    monkeypatch.delenv("MH_AUTO_EFFORT", raising=False)
    monkeypatch.delenv("MH_REASONING_EFFORT", raising=False)
    assert c.effort("task") == ""
    monkeypatch.setenv("MH_AUTO_EFFORT", "1")
    assert c.effort("task") == "medium" and c.effort("chat") == "low"
    monkeypatch.setenv("MH_REASONING_EFFORT", "high")      # an explicit setting always works
    monkeypatch.delenv("MH_AUTO_EFFORT")
    assert c.effort("task") == "high"


def _server_with(statuses, seen):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            code = statuses.pop(0) if statuses else 200
            seen.append(code)
            body = (json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 5, "completion_tokens": 1}}) if code == 200
                    else '{"error":{"message":"Authentication Error"}}').encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _chat_against(tmp_path, monkeypatch, statuses):
    seen = []
    srv = _server_with(statuses, seen)
    monkeypatch.setenv("MESHPOOL_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_STREAM", "0")
    a = _agent(tmp_path)
    a.messages.append({"role": "user", "content": "hi"})
    a.emit = lambda ev: None
    a._sleep = lambda s: None
    return a, seen, srv


def test_a_spurious_401_is_retried_but_a_bad_key_still_fails_fast(tmp_path, monkeypatch):
    a, seen, srv = _chat_against(tmp_path, monkeypatch, [401])
    try:
        msg, _ = a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
        assert msg["content"] == "ok" and seen.count(401) == 1                 # one retry for any key
    finally:
        srv.shutdown()
    a, seen, srv = _chat_against(tmp_path, monkeypatch, [401, 401, 401, 401])
    try:
        with pytest.raises(RuntimeError, match="rejected the API key"):
            a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
        assert 2 <= seen.count(401) <= 3                  # a key never accepted: one retry (plus the health probe), then say so
    finally:
        srv.shutdown()


def test_a_key_that_worked_earlier_gets_two_retries(tmp_path, monkeypatch):
    a2, seen2, srv2 = _chat_against(tmp_path, monkeypatch, [401, 401])
    try:
        a2._key_ok = "k"
        msg, _ = a2._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
        assert msg["content"] == "ok" and seen2.count(401) == 2
    finally:
        srv2.shutdown()


def test_hosted_endpoints_are_not_probed_with_local_server_urls(monkeypatch):
    from meshpool_harness.core import context as CX
    urls = []
    monkeypatch.setattr(CX, "_http_json", lambda url, body=None, key="": urls.append(url) or {})
    CX.probe("https://llm.meshpool.ai/v1", "deepseek-v4.1-flash", "k")
    assert urls == ["https://llm.meshpool.ai/v1/models"]
    urls.clear()
    CX.probe("http://localhost:11434/v1", "llama3", "k")
    assert any(u.endswith("/api/show") for u in urls)


def test_a_repeated_poll_that_succeeds_is_never_refused(tmp_path):
    a = _agent(tmp_path)
    a._run_tool = lambda c, cut=False: "[exit 0]\nstatus: pending"
    g = {}
    call = {"id": "1", "function": {"name": "shell", "arguments": '{"command": ["bash", "-lc", "gh run view 1"]}'}}
    assert not any("Not run" in a._repeat_guard(call, g) for _ in range(8))


def test_independent_grader_skips_a_small_code_task_whose_checks_passed(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    a.ws.edits = 2
    a.ws.commands = [{"cmd": "python3 t.py", "code": 0, "after_edit": 2, "kind": "run"}]
    assert a._small_code_verified()
    a.ws.commands = [{"cmd": "python3 t.py", "code": 1, "after_edit": 2, "kind": "run"}]
    assert not a._small_code_verified()                       # a failing last run still gets the review
    a.ws.commands = [{"cmd": "python3 t.py", "code": 0, "after_edit": 1, "kind": "run"}]
    assert not a._small_code_verified()                       # the run was before the last edit
    w = _agent(tmp_path, "work")
    w.ws.edits = 1
    w.ws.commands = [{"cmd": "x", "code": 0, "after_edit": 1, "kind": "run"}]
    assert not w._small_code_verified()                       # documents and decks keep the review
    from meshpool_harness.agent import grader as GR
    monkeypatch.setenv("MH_GRADER", "1")
    gate_calls = []
    monkeypatch.setattr(GR, "run", lambda *x, **k: gate_calls.append(1) or {"error": "x"})
    a.ws.commands = [{"cmd": "python3 t.py", "code": 0, "after_edit": 2, "kind": "run"}]
    g = {"msg0": 0, "t0": 0}
    monkeypatch.setattr(a, "_grade_info", lambda g, f: {"files": ["a.py"], "request": "add a function", "calls": 5,
                                                          "todos": [], "research": 0, "final": f})
    assert a._grade_gate(g, {"in": 0, "cached": 0, "out": 0, "calls": 0}, "done") == (False, "done") and not gate_calls
    monkeypatch.setenv("MH_GRADER_SMALL", "1")
    a._grade_gate(g, {"in": 0, "cached": 0, "out": 0, "calls": 0}, "done")
    assert gate_calls                                         # the switch brings it back
