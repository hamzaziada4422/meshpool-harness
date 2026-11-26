"""A local relay for agents that can only send a plain Bearer key (Pi, Hermes, OpenCode), so they can use an endpoint
that wants something else: Azure OpenAI and Azure AI Foundry take the key in an api-key header and, on the classic
route, the deployment name in the path and an api-version in the query.

  http://127.0.0.1:<port>/v1/chat/completions   forwarded to the key endpoint in settings, streamed or not
  http://127.0.0.1:<port>/v1/models             the model (deployment) set in Meshpool: Azure has no list to give

The agent holds only a random token made for this Meshpool process; the real key stays here. The relay listens on
127.0.0.1 only, goes only to the saved key endpoint (providers.key_base()), and never follows a redirect.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from meshpool_harness.core import endpoint as EP

MAX_BODY = 64 * 1024 * 1024
_LOCK = threading.Lock()
_STATE: dict = {"server": None, "url": "", "token": ""}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _authorized(self) -> bool:
        got = self.headers.get("Authorization", "")
        return hmac.compare_digest(got.encode(), f"Bearer {_STATE['token']}".encode())

    def _json(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code: int, msg: str, kind: str = "api_error"):
        self.close_connection = True          # the request body may be unread: it must not be read as the next request
        self._json(code, {"error": {"message": msg, "type": kind}})

    def do_GET(self):
        if not self._authorized():
            return self._error(401, "Missing or wrong relay token.", "authentication_error")
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            from meshpool_harness import providers as PV
            model = PV.split(PV._cfg("MH_MODEL"))[1] or PV.DEFAULT_MODEL
            return self._json(200, {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "azure"}]})
        self._error(404, "Not found.")

    def do_POST(self):
        if not self._authorized():
            return self._error(401, "Missing or wrong relay token.", "authentication_error")
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            return self._error(404, "Not found.")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return self._error(413 if n > MAX_BODY else 400, "Bad request size.", "invalid_request_error")
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError:
            return self._error(400, "The body is not JSON.", "invalid_request_error")
        if not isinstance(body, dict):
            return self._error(400, "The body must be a JSON object.", "invalid_request_error")
        from meshpool_harness import providers as PV
        base, key = PV.key_base(), PV.key()
        kind, ver = PV.key_kind(), PV.key_api_version()
        if not key:
            return self._error(401, "No API key is set in Meshpool.", "authentication_error")
        model = str(body.get("model") or "")
        url, out, headers = EP.chat_request(base, key, body, kind=kind, api_version=ver)
        req = urllib.request.Request(url, data=json.dumps(out).encode(), method="POST",
                                     headers={"User-Agent": "Meshpool", **headers})
        try:
            r = EP.urlopen(req, float(os.environ.get("MH_AGENT_FIRST_BYTE_S", "120")))
        except urllib.error.HTTPError as e:
            raw = e.read(200_000)
            return self._error(e.code, EP.explain(e.code, raw, kind=kind, host=PV.host(base), model=model, key=key,
                                                  api_version=ver or EP.caps(kind)["version"]))
        except (urllib.error.URLError, OSError) as e:
            return self._error(502, f"Could not reach {PV.host(base)}: {e}")
        with r:
            ctype = r.headers.get("Content-Type", "application/json")
            if "text/event-stream" not in ctype:
                try:
                    data = r.read()
                except OSError as e:                 # a timeout or a dropped connection mid-reply: still answer
                    return self._error(502, f"{PV.host(base)} stopped answering: {e}")
                self.send_response(r.status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    line = r.readline()
                    if not line:
                        break
                    self.wfile.write(line)
                    self.wfile.flush()
            except OSError:                          # the agent went away, or the upstream stalled past the timeout
                pass
            self.close_connection = True


def ensure() -> tuple[str, str]:
    """(base URL ending in /v1, bearer token) of this process's relay, started on first use."""
    with _LOCK:
        if _STATE["server"] is None:
            srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            srv.daemon_threads = True
            _STATE.update(server=srv, token=secrets.token_urlsafe(32),
                          url=f"http://127.0.0.1:{srv.server_address[1]}/v1")
            threading.Thread(target=srv.serve_forever, name="key-relay", daemon=True).start()
        return _STATE["url"], _STATE["token"]


def stop() -> None:
    with _LOCK:
        srv = _STATE["server"]
        _STATE.update(server=None, url="", token="")
    if srv is not None:
        srv.shutdown()
        srv.server_close()
