"""A metering proxy: every harness's model calls go through it, so all of them are measured the same way.

    python testkit/loop/meter.py --port 18990 --upstream https://llm.meshpool.ai/v1 --log /tmp/meter.jsonl

A harness is pointed at http://127.0.0.1:PORT/m/<task>/<arm>/<rep>/v1 (its normal base URL setting); the proxy
forwards each request to UPSTREAM unchanged except that streamed chat requests ask for the usage chunk
(stream_options.include_usage), and logs one JSON line per call:

    {"task", "arm", "rep", "path", "model", "status", "secs", "prompt", "cached", "completion"}

Harnesses report tokens differently or not at all (ACP agents send none); the proxy reads them from the endpoint's
own replies, for chat completions and the Responses API, streamed or not. Billed spend per model then scales the
token counts to dollars (bench.py).
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
HOP = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding", "keep-alive"}


def usage_of(obj: dict) -> dict:
    """prompt/cached/completion from a chat-completions or Responses API usage object."""
    u = obj.get("usage") or (obj.get("response") or {}).get("usage") or {}
    if not u:
        return {}
    prompt = u.get("prompt_tokens", u.get("input_tokens", 0)) or 0
    det = u.get("prompt_tokens_details") or u.get("input_tokens_details") or {}
    cached = det.get("cached_tokens", 0) or u.get("prompt_cache_hit_tokens", 0) or u.get("cache_read_input_tokens", 0) or 0
    comp = u.get("completion_tokens", u.get("output_tokens", 0)) or 0
    return {"prompt": int(prompt), "cached": int(cached), "completion": int(comp)}


def make_handler(upstream: str, log_path: str):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def handle(self):                              # a client that hangs up mid-stream is not an error to print
            try:
                super().handle()
            except (ConnectionResetError, BrokenPipeError):
                pass

        def _route(self):
            parts = self.path.split("/")
            # /m/<task>/<arm>/<rep>/v1/...
            if len(parts) > 5 and parts[1] == "m":
                return {"task": parts[2], "arm": parts[3], "rep": parts[4]}, "/" + "/".join(parts[6:])
            return {"task": "", "arm": "", "rep": ""}, self.path[3:] if self.path.startswith("/v1") else self.path

        def _go(self, method: str):
            meta, rest = self._route()
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else None
            model, stream = "", False
            if body and "json" in (self.headers.get("content-type") or ""):
                try:
                    j = json.loads(body)
                    model, stream = str(j.get("model", "")), bool(j.get("stream"))
                    if stream and rest.endswith("/chat/completions"):
                        j.setdefault("stream_options", {})["include_usage"] = True
                        body = json.dumps(j).encode()
                except ValueError:
                    pass
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            req = urllib.request.Request(upstream.rstrip("/") + rest, data=body, method=method, headers=hdrs)
            t0, use, status = time.time(), {}, 0
            try:
                resp = urllib.request.urlopen(req, timeout=900)
            except urllib.error.HTTPError as e:
                resp = e
            except Exception as e:  # noqa: BLE001
                with LOCK, open(log_path, "a") as f:       # a failed call is still a call: count it
                    f.write(json.dumps({**meta, "path": rest, "model": model, "status": 502,
                                        "secs": round(time.time() - t0, 2), "prompt": 0, "cached": 0,
                                        "completion": 0, "error": str(e)[:200]}) + "\n")
                self.send_response(502)
                msg = json.dumps({"error": f"meter: upstream unreachable: {e}"}).encode()
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            status = resp.status if hasattr(resp, "status") else resp.code
            self.send_response(status)
            ctype = resp.headers.get("content-type", "")
            for k, v in resp.headers.items():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            if "text/event-stream" in ctype:
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                buf = b""
                while True:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.startswith(b"data:"):
                            try:
                                u = usage_of(json.loads(line[5:].strip() or b"{}"))
                                if u:
                                    use = u
                            except ValueError:
                                pass
                self.wfile.write(b"0\r\n\r\n")
            else:
                data = resp.read()
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                try:
                    use = usage_of(json.loads(data or b"{}"))
                except ValueError:
                    pass
            row = {**meta, "path": rest, "model": model, "status": status, "secs": round(time.time() - t0, 2),
                   **{"prompt": 0, "cached": 0, "completion": 0, **use}}
            with LOCK, open(log_path, "a") as f:
                f.write(json.dumps(row) + "\n")

        def do_POST(self):
            self._go("POST")

        def do_GET(self):
            self._go("GET")
    return H


def start(port: int, upstream: str, log_path: str) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(upstream, log_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def totals(log_path: str) -> dict:
    """(task, arm, rep) -> {calls, prompt, cached, completion} from the meter log."""
    out: dict = {}
    try:
        lines = open(log_path).read().splitlines()
    except OSError:
        return out
    for line in lines:
        r = json.loads(line)
        if not r.get("path", "").endswith(("/chat/completions", "/responses", "/messages")):
            continue
        k = (r["task"], r["arm"], str(r["rep"]))
        t = out.setdefault(k, {"calls": 0, "prompt": 0, "cached": 0, "completion": 0})
        t["calls"] += 1
        for f in ("prompt", "cached", "completion"):
            t[f] += r.get(f, 0)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18990)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--log", default="/tmp/meter.jsonl")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.upstream, a.log))
    srv.serve_forever()
