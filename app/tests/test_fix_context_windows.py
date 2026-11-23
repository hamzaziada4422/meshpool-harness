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
        self.seen, self.errors, self.silent = [], 0, silent
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
    assert CTX.window("claude-sonnet-x", "https://api.example.com/v1") == 200_000
    assert CTX.window("qwen2.5-coder:7b-8k", "http://localhost:11434/v1", probe_endpoint=False) == 8192
    assert CTX.window("some-model", "https://api.example.com/v1") == CTX.DEFAULT_HOSTED
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "16384")
    assert CTX.window("claude-sonnet-x") == 16384


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
