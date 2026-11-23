"""A single-deployment endpoint in front of a real OpenAI-compatible gateway, for reproducing what a customer's
Azure-style endpoint does: only the deployed model exists (every other model id gets 404 DeploymentNotFound), and a
reasoning model rejects sampling parameters (400 "Unsupported parameter: 'temperature'"). Allowed requests are
forwarded upstream unchanged. Every request is logged as one JSON line (path, model, status, params sent).

    python testkit/single_deploy_proxy.py --port 8791 --model gpt-6-luna --reject temperature,top_p \
        --upstream https://llm.meshpool.ai/v1 --log /tmp/proxy.jsonl

Then point the app at it: MESHPOOL_BASE_URL=http://127.0.0.1:8791/v1 MESHPOOL_API_KEY=any MH_MODEL=gpt-6-luna.
The upstream key comes from UPSTREAM_API_KEY (any value when a network proxy injects the real one).
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None
LOCK = threading.Lock()


def _log(rec: dict) -> None:
    if not ARGS.log:
        return
    with LOCK, open(ARGS.log, "a") as f:
        f.write(json.dumps(rec) + "\n")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, status: int, payload: bytes, ctype: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            body = {"object": "list", "data": [{"id": m, "object": "model"} for m in ARGS.models]}
            return self._send(200, json.dumps(body).encode())
        return self._send(404, b'{"error": {"code": "NotFound", "message": "not found"}}')

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        model = body.get("model")
        sent = sorted(k for k in body if k in ("temperature", "top_p", "presence_penalty", "frequency_penalty",
                                               "max_tokens", "max_completion_tokens", "max_output_tokens", "stream",
                                               "response_format", "text", "tools"))
        rec = {"t": round(time.time(), 3), "path": self.path, "model": model, "params": sent,
               "task": self.headers.get("X-Harness-Task-Id", "")}
        if model not in ARGS.models:
            rec["status"] = 404
            _log(rec)
            err = {"error": {"code": "DeploymentNotFound",
                             "message": "The API deployment for this resource does not exist. If you created the "
                                        "deployment within the last 5 minutes, please wait a moment and try again."}}
            return self._send(404, json.dumps(err).encode())
        bad = [p for p in ARGS.reject if p in body]
        if bad:
            rec["status"] = 400
            _log(rec)
            err = {"error": {"message": f"Unsupported parameter: '{bad[0]}' is not supported with this model.",
                             "type": "invalid_request_error", "param": bad[0], "code": "unsupported_parameter"}}
            return self._send(400, json.dumps(err).encode())
        url = ARGS.upstream.rstrip("/") + self.path[self.path.index("/v1") + 3:] if "/v1" in self.path \
            else ARGS.upstream.rstrip("/") + self.path
        req = urllib.request.Request(url, data=raw, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('UPSTREAM_API_KEY', 'x')}"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                status, payload, ctype = r.status, r.read(), r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            status, payload, ctype = e.code, e.read(), "application/json"
        except Exception as e:  # noqa: BLE001
            status, payload, ctype = 502, json.dumps({"error": {"message": str(e)}}).encode(), "application/json"
        rec["status"] = status
        _log(rec)
        self._send(status, payload, ctype)


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--model", action="append", dest="models", required=True)
    ap.add_argument("--reject", default="temperature")
    ap.add_argument("--upstream", default="https://llm.meshpool.ai/v1")
    ap.add_argument("--log", default="")
    ARGS = ap.parse_args()
    ARGS.reject = [p for p in ARGS.reject.split(",") if p]
    ThreadingHTTPServer(("127.0.0.1", ARGS.port), H).serve_forever()


if __name__ == "__main__":
    main()
