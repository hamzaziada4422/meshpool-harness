"""Small context windows: a fake OpenAI-style server that enforces a token limit (characters / 3.5 over the messages and
tool schemas) runs a multi-step coding task through the one-shot loop at 4k, 8k and 32k."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from meshpool_harness.core import context as CTX
from meshpool_harness.core import loop as L
from meshpool_harness.core.client import LiveClient, classify_error
from meshpool_harness.core.tools import Workspace


def toks(messages, tools):
    return int((len(json.dumps(messages, ensure_ascii=False)) + len(json.dumps(tools or [], ensure_ascii=False))) / 3.5)


class Fake:
    def __init__(self, limit, steps, silent=False):
        self.limit, self.steps, self.n, self.silent = limit, steps, 0, False
        self.seen, self.errors, self.silent, self.summaries = [], 0, silent, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if not self.path.endswith("/chat/completions"):
                    self.send_response(404)
                    self.end_headers()
                    return
                need = toks(body["messages"], body.get("tools")) + int(body.get("max_tokens") or 0)
                outer.seen.append(need)
                if need > outer.limit:
                    outer.errors += 1
                    msg = (f"This model's maximum context length is {outer.limit} tokens. However, you requested "
                           f"{need} tokens. Please reduce the length of the messages.")
                    out = json.dumps({"error": {"message": msg, "code": "context_length_exceeded"}}).encode()
                    self.send_response(400)
                elif body["messages"][0]["content"].startswith("You write a handoff note"):
                    outer.summaries += 1                            # a compaction call: answered in text, no script step
                    msg = {"role": "assistant", "content": "Done: read files. Next: finish."}
                    out = json.dumps({"choices": [{"message": msg, "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 10}}).encode()
                    self.send_response(200)
                else:
                    step = outer.steps[min(outer.n, len(outer.steps) - 1)]
                    outer.n += 1
                    msg = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": f"c{outer.n}", "type": "function",
                        "function": {"name": step[0], "arguments": json.dumps(step[1])}}]}
                    out = json.dumps({"choices": [{"message": msg, "finish_reason": "tool_calls"}],
                                      "usage": {"prompt_tokens": toks(body["messages"], body.get("tools")),
                                                "completion_tokens": 30}}).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def stop(self):
        self.srv.shutdown()


STEPS = [("grep", {"pattern": "def "}), ("read_file", {"path": "big.py"}), ("read_file", {"path": "big.py", "start": 1, "end": 400}),
         ("bash", {"command": "cat big.py | head -c 30000"}), ("grep", {"pattern": "x"}),
         ("edit", {"path": "small.py", "old": "return 1", "new": "return 2"}),
         ("bash", {"command": "python small.py"}), ("read_file", {"path": "big.py"}),
         ("bash", {"command": "cat big.py"}), ("finish", {"answer": "done", "evidence": []})]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    CTX.forget()
    monkeypatch.setenv("MH_STREAM", "0")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.delenv("MH_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("MESHPOOL_CONTEXT_WINDOW", raising=False)
    monkeypatch.setenv("MH_CACHE_KEY", "0")
    monkeypatch.setenv("MH_API_WIRE", "openai")        # other tests may leave an Anthropic wire in the environment
    monkeypatch.setenv("MH_WIRE", "chat")
    yield
    CTX.forget()


def project(tmp_path):
    (tmp_path / "big.py").write_text("\n".join(f"def func_{i}(x):\n    return x + {i}  # padding padding padding" for i in range(1500)),
                                     encoding="utf-8")
    (tmp_path / "small.py").write_text("def f():\n    return 1\n\nprint(f())\n", encoding="utf-8")
    return Workspace(str(tmp_path), str(tmp_path / ".run"))


@pytest.mark.parametrize("win", [4096, 8192, 32768])
def test_task_completes_inside_the_window(tmp_path, monkeypatch, win):
    srv = Fake(win, STEPS)
    monkeypatch.setenv("MESHPOOL_BASE_URL", srv.url)
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", str(win))
    told = []
    cl = LiveClient("phase_2_build")
    cl.notify = told.append
    ws = project(tmp_path)
    r = L.run("Fix small.py so f returns 2. " * 3, client=cl, model="local-model", profile=None, ws=ws,
              ctx={"task_id": "t"}, system_text="You are a coding agent. " * 400, require_check=False,
              caps=L.Caps(max_turns=30))
    srv.stop()
    assert r.ok and r.answer == "done", (r.reason, srv.errors, srv.seen[-3:])
    assert srv.errors == 0 and max(srv.seen) <= win
    assert (tmp_path / "small.py").read_text(encoding="utf-8").count("return 2") == 1
    assert bool(told) == (win <= CTX.SMALL)                      # one notice, only for small windows


def test_unknown_local_model_learns_window_from_the_error(tmp_path, monkeypatch):
    srv = Fake(4096, STEPS)
    monkeypatch.setenv("MESHPOOL_BASE_URL", srv.url)
    monkeypatch.setenv("MH_PROBE_CONTEXT", "0")
    assert CTX.window("mystery-model", srv.url) == CTX.DEFAULT_LOCAL      # local, unknown: conservative 8k
    ws = project(tmp_path)
    cl = LiveClient("phase_2_build")
    cl.notify = lambda t: None
    # the first request is built for 8k; the server says 4096; the client must shrink and retry at once
    big = [{"role": "system", "content": "s" * 200}, {"role": "user", "content": "u" * 15000}]
    r = cl.chat(model="mystery-model", messages=big, max_tokens=2048)
    srv.stop()
    assert r["choices"][0]["message"]["tool_calls"]
    assert srv.errors <= 2 and srv.n == 1
    assert CTX.window("mystery-model", srv.url) == 4096


def test_single_huge_message_is_cut_not_fatal(tmp_path, monkeypatch):
    srv = Fake(4096, STEPS)
    monkeypatch.setenv("MESHPOOL_BASE_URL", srv.url)
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "4096")
    cl = LiveClient("phase_2_build")
    cl.notify = lambda t: None
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "x" * 200_000}]
    r = cl.chat(model="m", messages=msgs)
    srv.stop()
    assert r["choices"] and srv.errors == 0 and msgs[1]["content"] == "x" * 200_000


def test_fit_keeps_tool_pairs_whole():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
    for i in range(40):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": "x" * 500})}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 3000})
    out, _t, _o = CTX.fit(msgs, None, 4096)
    assert toks(out, None) <= 4096
    ids = {tc["id"] for m in out for tc in m.get("tool_calls") or []}
    for m in out:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in ids                    # no result without its call
    assert out[0]["role"] == "system" and out[1]["role"] == "user"


def test_budgets_scale_with_window():
    assert CTX.tool_output_chars(4096) < CTX.tool_output_chars(8192) < CTX.tool_output_chars(32768)
    assert CTX.tool_output_chars(4096) <= 4096 * 0.1 * 3.5 + 1
    assert CTX.tool_output_chars(131072) == 8000
    assert CTX.compact_chars(8192, 800, 600_000) < 8192 * 0.7 * 3.5
    assert CTX.compact_chars(131072, 800, 160_000) == 160_000


def test_window_sources(monkeypatch):
    assert CTX.window("claude-sonnet-x", "https://api.example.com/v1", probe_endpoint=False) == 200_000
    assert CTX.window("qwen2.5-coder:7b-8k", "http://localhost:11434/v1", probe_endpoint=False) == 8192
    assert CTX.window("some-model", "https://api.example.com/v1", probe_endpoint=False) == CTX.DEFAULT_HOSTED == 200_000
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "16384")
    assert CTX.window("claude-sonnet-x") == 16384
    assert CTX.window_info("claude-sonnet-x") == (16384, "override")


def test_ollama_and_lmstudio_metadata():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            out = json.dumps({"parameters": "num_ctx 6144\nstop x", "model_info": {"llama.context_length": 131072}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            out = json.dumps({"data": [{"id": "m1", "context_length": 12000}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    assert CTX.probe(base, "m1") == 6144
    srv.shutdown()


def test_error_messages_are_context_errors():
    for m in ["meshpool HTTP 400: This model's maximum context length is 4096 tokens",
              "HTTP 500: the request exceeds the available context size (n_ctx = 4096)",
              "HTTP 400: prompt is too long: 9000 tokens > 8192", "HTTP 400: context_length_exceeded",
              "HTTP 400: Trying to keep the first 5000 tokens when context overflows. The context length of only 4096"]:
        assert classify_error(m) == "context", m
    assert classify_error("HTTP 429: rate limit, too many tokens per minute") == "rate_limit"
    assert CTX.parse_limit("maximum context length is 4096 tokens") == 4096


def test_silent_truncation_is_learned():
    cl = LiveClient.__new__(LiveClient)
    sent = [{"role": "user", "content": "x" * 40000}]
    cl._check_silent_cut("ollama-model", sent, None, {"usage": {"prompt_tokens": 2048}}, 8192)
    assert CTX.window("ollama-model", "http://localhost:11434/v1", probe_endpoint=False) < 8192


def test_compaction_budgets_fit_small_windows():
    from meshpool_harness.agent import compaction as CP
    b = CP.budgets(4096)
    assert b["reply"] <= 512 and b["max_log"] < 4096 * 3.5 * 0.4
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
    for i in range(12):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "[exit 0]\nout"})
    seen = []

    class Cl:
        def chat(self, **kw):
            seen.append(kw)
            return {"choices": [{"message": {"content": "summary"}}]}
    r = CP.compact_history(msgs, client=Cl(), model="m", win=4096)
    assert r and seen[0]["max_tokens"] <= 512
    ids = {tc["id"] for m in msgs for tc in m.get("tool_calls") or []}
    assert all(m["tool_call_id"] in ids for m in msgs if m.get("role") == "tool")


@pytest.mark.parametrize("model,tokens", [
    ("claude-sonnet-5-5", 1_000_000), ("claude-opus-4-6", 1_000_000), ("claude-opus-4-1", 200_000),
    ("claude-sonnet-4-5", 200_000), ("deepseek-v4.1-flash", 1_048_576), ("deepseek-chat", 131_072),
    ("gpt-5", 400_000), ("gpt-4.1-mini", 1_047_576), ("gpt-4o", 128_000), ("o3-mini", 200_000), ("gpt-4", 8192),
    ("gemini-2.5-pro", 1_048_576), ("kimi-k3", 1_048_576), ("kimi-k2-0711", 131_072), ("glm-4.6", 200_000),
    ("llama-3.1-70b", 131_072), ("llama3-8b", 8192), ("qwen2.5-coder-32b", 32_768), ("mistral-small", 32_768)])
def test_model_table(model, tokens):
    assert CTX.window_info(model, "https://api.example.com/v1", probe_endpoint=False) == (tokens, "table")


def test_provider_metadata_beats_the_table_and_is_labelled():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            out = json.dumps({"data": [{"id": "claude-sonnet-5-5", "context_length": 321000},
                                       {"id": "other", "max_input_tokens": 77000}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    assert CTX.window_info("claude-sonnet-5-5", base) == (321000, "provider")
    assert CTX.window_info("other", base) == (77000, "provider")
    assert "from provider" in CTX.describe("other", base)
    srv.shutdown()


def test_context_target_caps_the_working_set(monkeypatch):
    assert CTX.target(1_000_000) == 120_000
    assert CTX.target(100_000) == 70_000
    assert CTX.target(8192) == int(8192 * 0.7)
    monkeypatch.setenv("MH_CONTEXT_TARGET", "300k")
    assert CTX.target(1_000_000) == 300_000
    monkeypatch.setenv("MH_CONTEXT_TARGET", "50000")
    assert CTX.target(1_000_000) == 50_000
    monkeypatch.delenv("MH_CONTEXT_TARGET")
    # compaction starts near the target, not at 70% of a 1M window; the hard window still takes a big single read
    chars = CTX.compact_chars(1_000_000, 5000, 10**9)
    assert chars < 120_000 * 3.5
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "x" * 900_000}]
    out, _t, _o = CTX.fit(msgs, None, 1_000_000)
    assert out is msgs                                         # 900 KB is inside a 1M-token window: sent as it is


def test_describe_and_check_row(monkeypatch):
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "1000000")
    assert CTX.describe("m") .startswith("context 1,000k (your setting)")
    from meshpool_harness import config as C
    row = [r for r in C.system_check() if r["name"] == "Context window"][0]
    assert "1,000k" in row["detail"]


def _models_server(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            state["hits"] += 1
            if state["hits"] <= state["fail_first"]:
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            out = json.dumps({"data": [{"id": "claude-haiku-5.5", "max_input_tokens": 200000},
                                       {"id": "gpt-5.6-luna", "max_input_tokens": 922000},
                                       {"id": "gpt-oss-120b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


def test_failed_models_fetch_is_not_remembered(monkeypatch):
    state = {"hits": 0, "fail_first": 1}
    srv, base = _models_server(state)
    monkeypatch.setattr(CTX, "RETRY_AFTER_S", 0.0)
    monkeypatch.setattr(CTX, "_is_local", lambda b: False)           # a remote gateway: only /models is asked
    first = CTX.window_info("claude-haiku-5.5", base)
    assert first == (1_000_000, "table")                                # the fetch failed: the table answers meanwhile
    assert CTX.window_info("claude-haiku-5.5", base) == (200_000, "provider")   # retried, now the provider's number
    assert CTX.window_info("gpt-5.6-luna", base) == (922_000, "provider")      # not the table's 400k
    assert CTX.window_info("gpt-oss-120b", base)[1] == "table"       # listed without a size: the table fills in
    assert CTX.window_info("gpt-oss-120b", base)[0] in (128_000, 131_072)
    srv.shutdown()


def test_failure_blocks_retries_for_a_minute(monkeypatch):
    state = {"hits": 0, "fail_first": 99}
    srv, base = _models_server(state)
    monkeypatch.setattr(CTX, "_is_local", lambda b: False)
    for _ in range(5):
        CTX.window_info("claude-haiku-5.5", base)
    srv.shutdown()
    assert state["hits"] == 1                                         # no hammering while it is down


def test_ollama_style_probes_only_for_local_endpoints(monkeypatch):
    seen = []
    monkeypatch.setattr(CTX, "probe", lambda b, m, k="": seen.append(b) or None)
    monkeypatch.setattr(CTX, "_listed", lambda b, k: {})
    CTX.window_info("some-model", "https://llm.example.com/v1")
    assert seen == []
    CTX.window_info("some-model", "http://localhost:11434/v1")
    assert seen == ["http://localhost:11434/v1"]
    assert CTX.PROBE_TIMEOUT_S >= 8


def test_local_detection_never_includes_remote_hosts():
    for u in ["https://llm.meshpool.ai/v1", "https://api.example.com:8000/v1", "https://api.openai.com/v1",
              "http://172.217.0.1/v1", "http://8.8.8.8:11434/v1"]:
        assert not CTX._is_local(u), u
    for u in ["http://localhost:8080/v1", "http://127.0.0.1:1234/v1", "http://192.168.1.5:11434/v1",
              "http://gpu-box:11434/v1", "http://[::1]:11434/v1", "http://172.20.0.3/v1"]:
        assert CTX._is_local(u), u


def test_show_route_is_asked_once_per_endpoint_and_a_404_is_remembered(tmp_path, monkeypatch):
    from meshpool_harness.core import modelcaps as MC
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    MC.reset()
    hits = []

    class H(BaseHTTPRequestHandler):                  # a localhost proxy that has no Ollama routes: plain 404 pages
        def log_message(self, *a):
            pass

        def _nf(self):
            hits.append(self.path)
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
        do_GET = do_POST = _nf
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    for m in ["a", "b", "c", "d", "e"]:
        assert CTX.probe(base, m) is None
    assert sum(1 for p in hits if p == "/api/show") == 1 and sum(1 for p in hits if p.startswith("/api/v0/")) == 1
    CTX.forget()                                      # a new process: the 404 is read back from the saved memory
    CTX.probe(base, "z")
    assert sum(1 for p in hits if p == "/api/show") == 1
    srv.shutdown()
    MC.reset()


def test_refused_fields_have_one_source(tmp_path, monkeypatch):
    from meshpool_harness.agent import core as A
    from meshpool_harness.core import modelcaps as MC
    from meshpool_harness import providers as PV
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    MC.reset()
    A._REFUSED.clear()
    body = {"model": "kimi-x", "temperature": 0.0, "max_tokens": 100, "messages": []}
    assert A._adapt_params(body, "HTTP 400: Unsupported parameter: 'temperature'")
    assert "temperature" not in body and A._REFUSED["kimi-x"] == {"temperature"}
    assert "temperature" in MC.refused(PV.key_base(), "kimi-x")           # the same record ModelCaps/LiveClient read
    MC.learn_refused(PV.key_base(), "other", "top_p")
    b2 = A._apply_refused({"model": "other", "top_p": 1, "max_tokens": 5})
    assert "top_p" not in b2
    A._REFUSED.clear()
    assert A._REFUSED.get("kimi-x", ()) == () and not MC.refused(PV.key_base(), "kimi-x")
    MC.reset()
