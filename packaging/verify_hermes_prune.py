"""Proof that the pruned Hermes (bundled_tools.hermes_ignore) still runs: start its ACP adapter, create a session and send
a prompt that a local mock server answers (no model calls, no network). Twice: once on chat completions (an
OpenAI-compatible key endpoint) and once on the Anthropic Messages API (api_mode anthropic_messages, which
harnesses.prepare_hermes sets for an Anthropic key endpoint and which needs the bundled anthropic SDK).

    <python with Hermes' requirements> packaging/verify_hermes_prune.py <the app's vendor/hermes folder>

Exits 0 and prints OK when the adapter answers initialize, session/new and session/prompt with the mock's reply in
both modes."""
import json, os, subprocess, sys, tempfile, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import yaml

hermes = os.path.abspath(sys.argv[1])          # the adapter runs in a temp folder: a relative path would point nowhere
REPLY = "MOCK-REPLY-OK"
LIMIT = int(os.environ.get("MH_HERMES_CHECK_LIMIT", "180"))                                    # seconds one adapter run may take (a normal run takes under 20)
calls = {"chat": 0, "messages": 0}


def sse(w, event, data):
    w.write((f"event: {event}\n" if event else "") .encode() + b"data: " + json.dumps(data).encode() + b"\n\n")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        self._json({"data": [{"id": "x", "object": "model", "type": "model", "display_name": "x"}]})

    def do_POST(self):
        n = int(self.headers.get("content-length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.rstrip("/").endswith("/messages"):           # Anthropic Messages API
            calls["messages"] += 1
            usage = {"input_tokens": 5, "output_tokens": 3}
            if body.get("stream"):
                self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
                sse(self.wfile, "message_start", {"type": "message_start", "message": {
                    "id": "msg_1", "type": "message", "role": "assistant", "model": "x", "content": [],
                    "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 0}}})
                sse(self.wfile, "content_block_start", {"type": "content_block_start", "index": 0,
                                                        "content_block": {"type": "text", "text": ""}})
                sse(self.wfile, "content_block_delta", {"type": "content_block_delta", "index": 0,
                                                        "delta": {"type": "text_delta", "text": REPLY}})
                sse(self.wfile, "content_block_stop", {"type": "content_block_stop", "index": 0})
                sse(self.wfile, "message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                                                  "stop_sequence": None}, "usage": {"output_tokens": 3}})
                sse(self.wfile, "message_stop", {"type": "message_stop"})
            else:
                self._json({"id": "msg_1", "type": "message", "role": "assistant", "model": "x",
                            "content": [{"type": "text", "text": REPLY}], "stop_reason": "end_turn",
                            "stop_sequence": None, "usage": usage})
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):  # metadata probes (e.g. Ollama's /api/show):
            self.send_response(404); self.send_header("content-length", "0"); self.end_headers()   # as a real
            return                                                  # Anthropic or OpenAI endpoint answers them
        calls["chat"] += 1                                         # chat completions
        if body.get("stream"):
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            for ch in ({"choices": [{"index": 0, "delta": {"role": "assistant", "content": REPLY}}]},
                       {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}):
                ch.update(id="c1", object="chat.completion.chunk", model="x"); sse(self.wfile, None, ch)
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self._json({"id": "c1", "object": "chat.completion", "model": "x",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": REPLY}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})


srv = HTTPServer(("127.0.0.1", 0), H); port = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()


def run(mode: str) -> bool:
    """One ACP session and prompt with Hermes on `mode` ("chat" or "anthropic"). True when the mock's reply came back
    over the wire that mode should use."""
    home = tempfile.mkdtemp(prefix="hhome-")
    env = dict(os.environ, PYTHONPATH=hermes, HERMES_HOME=home, HERMES_BUNDLED_SKILLS=hermes + "/skills", PYTHONUTF8="1",
               HERMES_DISABLE_LAZY_INSTALLS="1", HERMES_ASK_EVERY_COMMAND="1", PYTHONDONTWRITEBYTECODE="1",
               NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "ANTHROPIC_API_KEY",
              "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        env.pop(k, None)
    model = {"provider": "custom", "base_url": f"http://127.0.0.1:{port}/v1", "default": "x", "api_key": "k"}
    if mode == "anthropic":
        model["api_mode"] = "anthropic_messages"                   # what harnesses.prepare_hermes writes
    with open(home + "/config.yaml", "w") as f:
        f.write(yaml.safe_dump({"model": model, "tools": {"tool_search": {"enabled": "off"}},
                                "security": {"allow_lazy_installs": False}}))
    before = dict(calls)
    # The adapter as `python -m acp_adapter`, plus a deadline: if it has not finished by then, Python prints where every
    # thread is (faulthandler) and exits, so a hang shows up in the log as stack traces instead of a stuck build.
    boot = ("import faulthandler, runpy, sys; faulthandler.dump_traceback_later(%d, exit=True); "
            "sys.argv = ['acp_adapter']; runpy.run_module('acp_adapter', run_name='__main__', alter_sys=True)" % LIMIT)
    p = subprocess.Popen([sys.executable, "-c", boot], cwd=tempfile.mkdtemp(), env=env, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    out, err = [], []
    threading.Thread(target=lambda: [err.append(l) for l in p.stderr], daemon=True).start()  # a chatty stderr cannot block it
    killer = threading.Timer(LIMIT + 30, p.kill)               # and if even that does not end it
    killer.daemon = True
    killer.start()

    def fail(why):
        try:
            p.kill(); p.wait(5)
        except Exception:
            pass
        time.sleep(1)                                              # the stderr reader catches up
        print(f"FAIL ({mode}): {why} (adapter exit code {p.poll()}). Its stderr:\n" + "".join(err)[-12000:], flush=True)
        return False

    def send(o):
        try:
            p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush(); return True
        except OSError:
            return False

    def recv(i, t=120):
        end = time.time() + t
        while time.time() < end:
            line = p.stdout.readline()
            if not line:
                return None
            try:
                m = json.loads(line)
            except Exception:
                continue
            out.append(m)
            if m.get("id") == i and "method" not in m:
                return m
        return None

    if not send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": 1, "clientCapabilities": {}}}) or recv(1) is None:
        return fail("no answer to initialize")
    if not send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": os.getcwd(), "mcpServers": []}}):
        return fail("exited before session/new")
    sid = ((recv(2, 90) or {}).get("result") or {}).get("sessionId")
    if not sid:
        return fail("no session from session/new")
    if not send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
                 "params": {"sessionId": sid, "prompt": [{"type": "text", "text": "say hi"}]}}):
        return fail("exited before session/prompt")
    r3 = recv(3, 120)
    text = "".join(m["params"]["update"].get("content", {}).get("text", "") for m in out
                   if m.get("method") == "session/update" and m["params"]["update"].get("sessionUpdate") == "agent_message_chunk")
    used = {k: calls[k] - before[k] for k in calls}
    want = "messages" if mode == "anthropic" else "chat"
    print(f"{mode}: prompt result {json.dumps(r3)[:120]} | text {text[:40]!r} | calls {used}")
    if not (r3 and "result" in r3 and REPLY in text):
        return fail("the prompt did not come back with the mock's reply")
    if not used[want] or any(v for k, v in used.items() if k != want):
        return fail(f"expected every model call on the {want} API, got {used}")
    killer.cancel()
    try:
        p.stdin.close(); p.wait(10)
    except Exception:
        p.kill()
    return True


ok = all([run("chat"), run("anthropic")])
print("OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
