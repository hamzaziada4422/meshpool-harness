"""A fake OpenAI-compatible server for the small cost tests (no network, no model).

It answers chat completions and the Responses API, streamed or not, following a script: the first N requests that
carry tools get a tool call (a shell command), the next one a final answer. Every request body is kept, so a test can
look at exactly what a harness sends (system prompt, tool schema, history) and how many requests it made.

    fake = FakeServer(steps=["ls -la", "cat notes.txt"], final="Done.").start()
    fake.base            # http://127.0.0.1:PORT/v1  (or /c/<name>/v1 for a separate conversation: fake.base_for("x"))
    fake.requests        # one dict per model request: path, conv, stream, body, step, kind, tools, ...
    fake.count("x")      # model requests of conversation x

Usage in replies is computed from the request size (chars / 3.7, the estimate used across these tools) with the
previous request of the same conversation as the cached prefix, so token counts grow the way a real provider's do.
"""
from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHARS_PER_TOKEN = 3.7


def est_tokens(chars: int) -> int:
    return int(math.ceil(chars / CHARS_PER_TOKEN))


def _tool_name(t: dict) -> str:
    return (t.get("function") or {}).get("name") or t.get("name") or ""


def _tool_params(t: dict) -> dict:
    return ((t.get("function") or {}).get("parameters") or t.get("parameters") or {}).get("properties") or {}


def pick_tool(tools: list) -> tuple[str, str, dict] | None:
    """(tool name, argument name, schema of that argument) of the shell-like tool in a request's tool list."""
    by = {_tool_name(t): t for t in tools}
    for name in ("shell", "bash", "exec_command", "shell_command", "run_command"):
        if name in by:
            props = _tool_params(by[name])
            for arg in ("command", "cmd"):
                if arg in props:
                    return name, arg, props[arg]
    return None


def tool_args(arg: str, schema: dict, cmd: str) -> dict:
    if (schema or {}).get("type") == "array":
        return {arg: ["bash", "-lc", cmd]}
    return {arg: cmd}


def request_text_chars(body: dict) -> dict:
    """What a request spends, in characters, split the way the cost report does: system/developer text, tool
    schemas, everything else (history and the user's message). Works for chat completions and the Responses API."""
    tools = body.get("tools") or []
    tools_chars = len(json.dumps(tools, separators=(",", ":"))) if tools else 0
    system = 0
    msgs = body.get("messages")
    if msgs is None:
        msgs = body.get("input") if isinstance(body.get("input"), list) else \
            ([{"role": "user", "content": body["input"]}] if body.get("input") else [])
    if body.get("instructions"):
        system += len(body["instructions"])
    rest = 0
    for m in msgs:
        size = len(json.dumps(m, separators=(",", ":"), ensure_ascii=False))
        if isinstance(m, dict) and m.get("role") in ("system", "developer"):
            c = m.get("content")
            system += size if not c else len(c if isinstance(c, str) else json.dumps(c, separators=(",", ":")))
        else:
            rest += size
    total = len(json.dumps({k: v for k, v in body.items() if k not in ("tools",)}, separators=(",", ":"),
                           ensure_ascii=False)) + tools_chars
    return {"system": system, "tools": tools_chars, "rest": rest, "total": total}


class FakeServer:
    def __init__(self, steps: list[str] | None = None, final: str = "Done. The change is made and checked.",
                 port: int = 0, usage: bool = True, trailing_newline: bool = True, delay: float = 0.0):
        self.steps = list(steps or [])
        self.final = final
        self.usage = usage
        self.trailing_newline = trailing_newline       # False: the last SSE event has no newline after it
        self.delay = delay
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self._prev: dict = {}                          # conv -> prompt tokens of its previous request
        self._main: dict = {}                          # conv -> requests with tools so far (the script position)
        self.srv = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]

    def start(self) -> "FakeServer":
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def base_for(self, conv: str) -> str:
        return f"http://127.0.0.1:{self.port}/c/{conv}/v1"

    def of(self, conv: str | None = None) -> list[dict]:
        with self.lock:
            return [r for r in self.requests if conv is None or r["conv"] == conv]

    def count(self, conv: str | None = None) -> int:
        return len(self.of(conv))

    # ---- replies ----
    def _decide(self, conv: str, body: dict) -> tuple[str, dict]:
        """("tool", {name, args}) or ("text", {text}) for this request."""
        tools = body.get("tools") or []
        with self.lock:
            step = self._main.get(conv, 0)
            if tools:
                self._main[conv] = step + 1
        if tools and step < len(self.steps):
            picked = pick_tool(tools)
            if picked:
                name, arg, schema = picked
                return "tool", {"name": name, "args": tool_args(arg, schema, self.steps[step])}
        return "text", {"text": self.final if tools else "ok"}

    def _usage(self, conv: str, body: dict, out_text: str) -> dict:
        chars = request_text_chars(body)["total"]
        prompt = est_tokens(chars)
        with self.lock:
            cached = min(self._prev.get(conv, 0), prompt)
            self._prev[conv] = prompt
        return {"prompt": prompt, "cached": cached, "completion": max(1, est_tokens(len(out_text)))}

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _conv(self):
                parts = self.path.split("?")[0].split("/")
                if len(parts) > 3 and parts[1] == "c":
                    return parts[2], "/" + "/".join(parts[4:])
                return "", self.path.split("?")[0][3:] if self.path.startswith("/v1") else self.path

            def _send(self, status: int, payload, ctype="application/json"):
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                _, rest = self._conv()
                if rest.rstrip("/").endswith("/models") or rest.rstrip("/") == "/models":
                    return self._send(200, {"data": [{"id": "fake-model", "object": "model",
                                                      "max_input_tokens": 128000}]})
                self._send(404, {"error": "not found"})

            def _sse(self, events: list[tuple[str, dict]], done: bool):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                payload = b""
                for i, (name, ev) in enumerate(events):
                    last = i == len(events) - 1 and not done
                    head = f"event: {name}\n" if name else ""
                    payload += (head + "data: " + json.dumps(ev) + ("\n\n" if not last or fake.trailing_newline
                                                                      else "")).encode()
                if done:
                    payload += b"data: [DONE]\n\n" if fake.trailing_newline else b"data: [DONE]"
                for i in range(0, len(payload), 700):          # several chunks, split anywhere (even mid-line)
                    c = payload[i:i + 700]
                    self.wfile.write(f"{len(c):x}\r\n".encode() + c + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")

            def do_POST(self):
                conv, rest = self._conv()
                if "chunked" in (self.headers.get("transfer-encoding") or "").lower():
                    raw = b""
                    while True:
                        size = int(self.rfile.readline().strip() or b"0", 16)
                        if not size:
                            self.rfile.readline()
                            break
                        raw += self.rfile.read(size)
                        self.rfile.readline()
                else:
                    raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    return self._send(400, {"error": "bad json"})
                if fake.delay:
                    time.sleep(fake.delay)
                kind = "responses" if rest.split("?")[0].endswith("/responses") else "chat"
                if not (rest.split("?")[0].endswith(("/responses", "/chat/completions"))):
                    return self._send(404, {"error": f"no route {rest}"})
                what, d = fake._decide(conv, body)
                out_text = d["text"] if what == "text" else json.dumps(d["args"])
                u = fake._usage(conv, body, out_text)
                stream = bool(body.get("stream"))
                with fake.lock:
                    fake.requests.append({"path": rest, "raw_path": self.path, "conv": conv, "stream": stream, "kind": kind, "body": body,
                                          "step": fake._main.get(conv, 0), "tools": body.get("tools") or [],
                                          "what": what, "usage": u, "headers": dict(self.headers.items())})
                usage_chat = {"prompt_tokens": u["prompt"], "completion_tokens": u["completion"],
                              "total_tokens": u["prompt"] + u["completion"],
                              "prompt_tokens_details": {"cached_tokens": u["cached"]}}
                usage_resp = {"input_tokens": u["prompt"], "input_tokens_details": {"cached_tokens": u["cached"]},
                              "output_tokens": u["completion"], "total_tokens": u["prompt"] + u["completion"]}
                cid = f"fake-{len(fake.requests)}"
                if kind == "chat":
                    msg = {"role": "assistant", "content": d["text"]} if what == "text" else {
                        "role": "assistant", "content": None,
                        "tool_calls": [{"id": f"call_{cid}", "type": "function",
                                        "function": {"name": d["name"], "arguments": json.dumps(d["args"])}}]}
                    fin = "stop" if what == "text" else "tool_calls"
                    if not stream:
                        full = {"id": cid, "object": "chat.completion", "model": body.get("model", "fake"),
                                "choices": [{"index": 0, "message": msg, "finish_reason": fin}]}
                        if fake.usage:
                            full["usage"] = usage_chat
                        return self._send(200, full)
                    ev = [("", {"id": cid, "object": "chat.completion.chunk", "model": body.get("model", "fake"),
                                "choices": [{"index": 0, "delta": {"role": "assistant"}}]})]
                    if what == "text":
                        t = d["text"]
                        ev += [("", {"id": cid, "choices": [{"index": 0, "delta": {"content": t[:len(t) // 2]}}]}),
                               ("", {"id": cid, "choices": [{"index": 0, "delta": {"content": t[len(t) // 2:]}}]})]
                    else:
                        a = json.dumps(d["args"])
                        tc = lambda part, first: {"index": 0, **({"id": f"call_{cid}", "type": "function"} if first else {}),  # noqa: E731
                                                  "function": {**({"name": d["name"]} if first else {}), "arguments": part}}
                        ev += [("", {"id": cid, "choices": [{"index": 0, "delta": {"tool_calls": [tc(a[:6], True)]}}]}),
                               ("", {"id": cid, "choices": [{"index": 0, "delta": {"tool_calls": [tc(a[6:], False)]}}]})]
                    ev.append(("", {"id": cid, "choices": [{"index": 0, "delta": {}, "finish_reason": fin}]}))
                    if fake.usage and (body.get("stream_options") or {}).get("include_usage"):
                        ev.append(("", {"id": cid, "choices": [], "usage": usage_chat}))
                    return self._sse(ev, done=True)
                # Responses API
                item = ({"type": "message", "id": f"msg_{cid}", "role": "assistant", "status": "completed",
                         "content": [{"type": "output_text", "text": d["text"]}]} if what == "text" else
                        {"type": "function_call", "id": f"fc_{cid}", "call_id": f"call_{cid}", "name": d["name"],
                         "arguments": json.dumps(d["args"]), "status": "completed"})
                resp = {"id": cid, "object": "response", "status": "completed", "model": body.get("model", "fake"),
                        "output": [item]}
                if fake.usage:
                    resp["usage"] = usage_resp
                if not stream:
                    return self._send(200, resp)
                ev = [("response.created", {"type": "response.created", "response": {"id": cid, "status": "in_progress",
                                                                                      "output": [], "usage": None}}),
                      ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": item}),
                      ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
                      ("response.completed", {"type": "response.completed", "response": resp})]
                return self._sse(ev, done=False)

        return H
