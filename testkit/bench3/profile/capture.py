"""Fake OpenAI-compatible upstream that records every request body and returns a scripted reply.

Serves /<anything>/v1/{chat/completions,responses,models}, streamed or not.  The arm is taken from the URL
(/c/<arm>/v1/...), every request body is appended to <out>/<arm>.jsonl as
{"seq", "t", "path", "stream", "headers", "body"}.  Reply: if the request offers tools and the conversation has no tool
result yet -> one tool call running `ls` with whatever shell tool is offered; otherwise the text "done".
Standalone:  python capture.py --port 8099 --out captures
Library:     srv = CaptureServer(out).start(); srv.base_for("pi") -> "http://127.0.0.1:<port>/c/pi/v1"
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SHELL_NAMES = ("bash", "shell", "exec_command", "shell_command", "local_shell", "terminal", "run_command", "execute",
               "run_shell_command", "exec")


def usage_chat(n_bytes: int) -> dict:
    """Realistic usage (bytes/4 prompt tokens): Meshpool compares usage with what it sent and shrinks its context window
    and tool schemas when the server seems to have cut the prompt, so a fixed tiny number would change what it sends."""
    n = max(1, n_bytes // 4)
    return {"prompt_tokens": n, "completion_tokens": 20, "total_tokens": n + 20, "prompt_tokens_details": {"cached_tokens": 0}}


def usage_resp(n_bytes: int) -> dict:
    n = max(1, n_bytes // 4)
    return {"input_tokens": n, "output_tokens": 20, "total_tokens": n + 20, "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0}}


def tool_defs(body: dict) -> list[dict]:
    """Normalise chat (tools[].function) and Responses (tools[] flat) tool lists to {name, parameters, type}."""
    out = []

    def walk(ts):
        for t in ts or []:
            if not isinstance(t, dict):
                continue
            if t.get("type") == "namespace":
                walk(t.get("tools"))
                continue
            f = t.get("function") if isinstance(t.get("function"), dict) else t
            out.append({"name": f.get("name") or t.get("type") or "", "parameters": f.get("parameters") or {},
                        "type": t.get("type", "function")})
    walk(body.get("tools"))
    for it in body.get("input") if isinstance(body.get("input"), list) else []:     # Codex 0.171: tools ride in input
        if isinstance(it, dict) and it.get("type") == "additional_tools":
            walk(it.get("tools"))
    return out


def pick_shell(tools: list[dict]):
    """(tool name, arguments dict) for a harmless `ls`, or None."""
    by = {t["name"]: t for t in tools}
    if "exec" in by and by["exec"]["type"] == "custom":          # Codex code-mode: free-form JS input
        return "exec", "text('ls')"
    cands = [n for n in SHELL_NAMES if n in by] + [n for n in by if re.search(r"(bash|shell|terminal)", n) and n not in SHELL_NAMES]
    for n in cands:
        props = (by[n]["parameters"] or {}).get("properties") or {}
        key = next((k for k in ("command", "cmd", "script") if k in props), None)
        if key is None:
            key = next(iter(props), "command")
        typ = (props.get(key) or {}).get("type")
        args = {key: ["ls"] if typ == "array" else "ls"}
        return n, args
    return None


def count_tool_results(body: dict) -> int:
    n = sum(1 for m in body.get("messages") or [] if m.get("role") in ("tool", "function"))
    n += sum(1 for it in body.get("input") if isinstance(body.get("input"), list) and isinstance(it, dict)
             and str(it.get("type", "")).endswith("_output")) if isinstance(body.get("input"), list) else 0
    return n


def plan(body: dict, calls: int = 1):
    """calls: how many `ls` tool calls the scripted model makes before it answers "done"."""
    tools = tool_defs(body)
    if tools and count_tool_results(body) < calls:
        sh = pick_shell(tools)
        if sh:
            return ("tool", sh[0], sh[1])
    return ("text", "done")


class CaptureServer:
    def __init__(self, out: Path, port: int = 0, delay: float = 0.0, calls: int = 1):
        self.calls = calls
        self.delay = delay            # seconds a reply is held back: lets MCP servers finish starting, as a real model would
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.seq: dict[str, int] = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, data: bytes, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, json.dumps({"object": "list", "data": [
                    {"id": m, "object": "model", "owned_by": "fake"} for m in ("deepseek-v4.1-flash", "gpt-6-luna", "fake-model")]}).encode())

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                if self.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {"_raw": raw[:2000].decode(errors="replace")}
                m = re.match(r"/c/([^/]+)/", self.path)
                arm = m.group(1) if m else "unknown"
                with outer.lock:
                    outer.seq[arm] = outer.seq.get(arm, 0) + 1
                    seq = outer.seq[arm]
                    with open(outer.out / f"{arm}.jsonl", "a") as f:
                        f.write(json.dumps({"seq": seq, "t": round(time.time(), 2), "path": self.path,
                                            "stream": bool(body.get("stream")), "bytes": len(raw),
                                            "headers": {k: v for k, v in self.headers.items()
                                                        if k.lower() not in ("authorization",)},
                                            "body": body}) + "\n")
                time.sleep(outer.delay)
                p = plan(body, outer.calls)
                if self.path.rstrip("/").endswith("/responses"):
                    return self._responses(body, p)
                return self._chat(body, p)

            # ---------------------------------------------------------------- chat completions
            def _chat(self, body, p):
                cid = f"call_{time.time_ns()}"
                if p[0] == "tool":
                    msg = {"role": "assistant", "content": None, "tool_calls": [{"id": cid, "type": "function",
                           "function": {"name": p[1], "arguments": json.dumps(p[2])}}]}
                    fin = "tool_calls"
                else:
                    msg, fin = {"role": "assistant", "content": p[1]}, "stop"
                if not body.get("stream"):
                    return self._send(200, json.dumps({"id": "x", "object": "chat.completion", "model": body.get("model"),
                                                       "choices": [{"index": 0, "message": msg, "finish_reason": fin}],
                                                       "usage": usage_chat(len(json.dumps(body)))}).encode())
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()

                def ev(d):
                    self.wfile.write(b"data: " + json.dumps(d).encode() + b"\n\n")
                    self.wfile.flush()
                base = {"id": "x", "object": "chat.completion.chunk", "model": body.get("model")}
                ev({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}}]})
                if p[0] == "tool":
                    ev({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": cid, "type": "function",
                        "function": {"name": p[1], "arguments": json.dumps(p[2])}}]}}]})
                else:
                    ev({**base, "choices": [{"index": 0, "delta": {"content": p[1]}}]})
                ev({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": fin}]})
                ev({**base, "choices": [], "usage": usage_chat(len(json.dumps(body)))})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

            # ---------------------------------------------------------------- responses API
            def _responses(self, body, p):
                rid, mid, fid = f"resp_{time.time_ns()}", f"msg_{time.time_ns()}", f"fc_{time.time_ns()}"
                if p[0] == "tool" and isinstance(p[2], str):
                    item = {"type": "custom_tool_call", "id": fid, "call_id": f"call_{time.time_ns()}", "name": p[1],
                            "input": p[2], "status": "completed"}
                elif p[0] == "tool":
                    item = {"type": "function_call", "id": fid, "call_id": f"call_{time.time_ns()}", "name": p[1],
                            "arguments": json.dumps(p[2]), "status": "completed"}
                else:
                    item = {"type": "message", "id": mid, "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": p[1], "annotations": []}]}
                final = {"id": rid, "object": "response", "status": "completed", "model": body.get("model"),
                         "output": [item], "usage": usage_resp(len(json.dumps(body)))}
                if not body.get("stream"):
                    return self._send(200, json.dumps(final).encode())
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                n = [0]

                def ev(t, d):
                    d = {"type": t, "sequence_number": n[0], **d}
                    n[0] += 1
                    self.wfile.write(f"event: {t}\ndata: {json.dumps(d)}\n\n".encode())
                    self.wfile.flush()
                skel = {k: v for k, v in final.items() if k != "output"}
                ev("response.created", {"response": {**skel, "status": "in_progress", "output": []}})
                ev("response.in_progress", {"response": {**skel, "status": "in_progress", "output": []}})
                if item["type"] == "custom_tool_call":
                    ev("response.output_item.added", {"output_index": 0, "item": {**item, "input": "", "status": "in_progress"}})
                    ev("response.custom_tool_call_input.delta", {"item_id": fid, "output_index": 0, "delta": item["input"]})
                    ev("response.custom_tool_call_input.done", {"item_id": fid, "output_index": 0, "input": item["input"]})
                    ev("response.output_item.done", {"output_index": 0, "item": item})
                elif item["type"] == "function_call":
                    ev("response.output_item.added", {"output_index": 0, "item": {**item, "arguments": "", "status": "in_progress"}})
                    ev("response.function_call_arguments.delta", {"item_id": fid, "output_index": 0, "delta": item["arguments"]})
                    ev("response.function_call_arguments.done", {"item_id": fid, "output_index": 0, "arguments": item["arguments"]})
                    ev("response.output_item.done", {"output_index": 0, "item": item})
                else:
                    ev("response.output_item.added", {"output_index": 0, "item": {**item, "content": [], "status": "in_progress"}})
                    ev("response.content_part.added", {"item_id": mid, "output_index": 0, "content_index": 0,
                                                       "part": {"type": "output_text", "text": "", "annotations": []}})
                    ev("response.output_text.delta", {"item_id": mid, "output_index": 0, "content_index": 0, "delta": p[1]})
                    ev("response.output_text.done", {"item_id": mid, "output_index": 0, "content_index": 0, "text": p[1]})
                    ev("response.content_part.done", {"item_id": mid, "output_index": 0, "content_index": 0,
                                                      "part": item["content"][0]})
                    ev("response.output_item.done", {"output_index": 0, "item": item})
                ev("response.completed", {"response": final})
                self.close_connection = True

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def base_for(self, arm: str) -> str:
        return f"http://127.0.0.1:{self.port}/c/{arm}/v1"

    def close(self):
        self.httpd.shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--out", default="captures")
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--calls", type=int, default=1)
    a = ap.parse_args()
    s = CaptureServer(Path(a.out), a.port, a.delay, a.calls)
    print("base:", s.base_for("<arm>"))
    s.httpd.serve_forever()
