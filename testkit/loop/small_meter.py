"""A metering proxy for the small cost tests: every model request of every arm is counted, streamed or not.

    m = Meter("https://llm.meshpool.ai/v1", prices, max_usd=0.30)
    m.start()
    base = m.base_for("v2-dbg-retry", "codex", 0)        # point the harness at this as its base URL
    ...
    m.totals()[("v2-dbg-retry", "codex", "0")]           # {"calls": 7, "prompt": ..., "cached": ..., "completion": ...}

Why a second meter next to meter.py: the first Codex measurement reported "1.0 calls/task" because the number came from
Codex's own `turn.completed` event, which is one aggregate usage record per turn, not per request. This proxy counts
what actually crossed the wire. It also fixes the ways a per-request count can go wrong:

  * a streamed reply whose last event has no trailing newline (the usage event is the last one) is still parsed;
  * a request body sent with Transfer-Encoding: chunked is read (no Content-Length), gzip bodies are inspected;
  * usage comes from every wire: chat completions, the Responses API (response.completed), Anthropic messages
    (message_start + message_delta), with cached tokens from prompt_tokens_details, input_tokens_details,
    prompt_cache_hit_tokens or cache_read_input_tokens;
  * a reply with no usage block is still a call (flagged "usage_missing"), a refused/failed request is counted apart
    ("failed"), never as a call and never silently dropped;
  * the budget is enforced here: once spend passes max_usd, new requests get HTTP 402 and never reach the model.

Dependencies: the standard library only.
"""
from __future__ import annotations

import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOP = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding", "keep-alive", "te", "trailer",
       "upgrade", "proxy-authorization", "proxy-connection", "expect"}
MODEL_PATH = re.compile(r"/(chat/completions|responses|messages|completions)(/compact)?$")


def merge_usage(acc: dict, u: dict) -> dict:
    """Fold one usage object into the stream's running one: later values replace earlier ones key by key (Anthropic
    sends input tokens in message_start and output tokens in message_delta; the Responses API and chat completions
    send one complete object near the end)."""
    for k, v in (u or {}).items():
        if v is None:
            continue
        if isinstance(v, dict):
            acc[k] = {**(acc.get(k) or {}), **{a: b for a, b in v.items() if b is not None}}
        else:
            acc[k] = v
    return acc


def usage_numbers(u: dict) -> dict:
    """prompt / cached / completion tokens from a (merged) usage object of any supported wire. prompt includes the
    cached part (OpenAI convention); Anthropic's input_tokens excludes cache reads and writes, so they are added."""
    if not u:
        return {}
    cached = 0
    for det in (u.get("prompt_tokens_details"), u.get("input_tokens_details")):
        if isinstance(det, dict):
            cached = max(cached, int(det.get("cached_tokens") or 0))
    cached = max(cached, int(u.get("prompt_cache_hit_tokens") or 0))
    if "cache_read_input_tokens" in u or "cache_creation_input_tokens" in u:           # Anthropic messages
        read = int(u.get("cache_read_input_tokens") or 0)
        prompt = int(u.get("input_tokens") or 0) + read + int(u.get("cache_creation_input_tokens") or 0)
        cached = max(cached, read)
    else:
        prompt = int(u.get("prompt_tokens", u.get("input_tokens", 0)) or 0)
    comp = int(u.get("completion_tokens", u.get("output_tokens", 0)) or 0)
    return {"prompt": prompt, "cached": min(cached, prompt) if prompt else cached, "completion": comp}


def usage_in_event(obj) -> dict:
    """The usage object an SSE event or a JSON reply carries, or {}."""
    if not isinstance(obj, dict):
        return {}
    for cand in (obj.get("usage"), (obj.get("response") or {}).get("usage") if isinstance(obj.get("response"), dict) else None,
                 (obj.get("message") or {}).get("usage") if isinstance(obj.get("message"), dict) else None):
        if isinstance(cand, dict) and cand:
            return cand
    return {}


def cost_usd(n: dict, prices: dict) -> float:
    """The bench.py formula: fresh input + cached input + output, per 1M tokens."""
    fresh = max(0, n.get("prompt", 0) - n.get("cached", 0))
    return (fresh * prices["in"] + n.get("cached", 0) * prices["cached"] + n.get("completion", 0) * prices["out"]) / 1e6


def _read_chunked(rfile) -> bytes:
    out = b""
    while True:
        size_line = rfile.readline().strip()
        if not size_line:
            break
        size = int(size_line.split(b";")[0], 16)
        if size == 0:
            while rfile.readline().strip():            # trailers
                pass
            break
        out += rfile.read(size)
        rfile.readline()
    return out


def _decode_body(body: bytes, enc: str) -> bytes | None:
    """The request body as plain bytes for inspection, or None when it cannot be decoded (it is forwarded as sent)."""
    enc = (enc or "").lower().strip()
    try:
        if not enc or enc == "identity":
            return body
        if enc == "gzip":
            return gzip.decompress(body)
        if enc == "deflate":
            return zlib.decompress(body)
        if enc == "zstd":
            import zstandard                              # optional: Codex may compress request bodies
            return zstandard.ZstdDecompressor().stream_reader(__import__("io").BytesIO(body)).read()
    except Exception:  # noqa: BLE001
        return None
    return None


class SSE:
    """Incremental server-sent-events reader that never loses the last, unterminated line."""

    def __init__(self):
        self.buf = b""
        self.usage: dict = {}
        self.events = 0

    def _line(self, line: bytes) -> None:
        line = line.strip()
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            return
        try:
            obj = json.loads(data)
        except ValueError:
            return
        self.events += 1
        merge_usage(self.usage, usage_in_event(obj))

    def feed(self, chunk: bytes) -> None:
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            self._line(line)

    def close(self) -> None:
        if self.buf:
            self._line(self.buf)
            self.buf = b""


class Meter:
    def __init__(self, upstream: str, prices: dict, max_usd: float | None = None, port: int = 0,
                 azure_key: str | None = None, extra_query: str = ""):
        """azure_key: send it as the `api-key` header instead of Authorization (Azure OpenAI key auth);
        extra_query: appended to every upstream URL (e.g. "api-version=2025-04-01-preview")."""
        self.upstream = upstream.rstrip("/")
        self.azure_key = azure_key
        self.extra_query = extra_query.lstrip("?&")
        self.blocked_keys: set = set()       # (task, arm, rep) that had a request refused for budget
        self.prices = prices
        self.max_usd = max_usd
        self.lock = threading.Lock()
        self.calls: list[dict] = []          # one row per model request that reached the endpoint (any status)
        self.blocked = 0                     # requests refused because the budget was spent
        self.spent = 0.0
        self.srv = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]

    # ---- public ----
    def start(self) -> "Meter":
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    def base_for(self, task: str, arm: str, rep: int | str = 0) -> str:
        return f"http://127.0.0.1:{self.port}/m/{task}/{arm}/{rep}/v1"

    @property
    def exceeded(self) -> bool:
        return self.max_usd is not None and self.spent >= self.max_usd

    def totals(self) -> dict:
        """(task, arm, rep) -> calls (successful requests), failed (non-2xx), prompt, cached, completion, usd,
        usage_missing (successful calls whose reply carried no usage block)."""
        out: dict = {}
        with self.lock:
            rows = list(self.calls)
        for r in rows:
            if not MODEL_PATH.search(r["path"].split("?")[0]):
                continue
            t = out.setdefault((r["task"], r["arm"], str(r["rep"])),
                               {"calls": 0, "failed": 0, "prompt": 0, "cached": 0, "completion": 0, "usd": 0.0,
                                "usage_missing": 0})
            if 200 <= r["status"] < 300:
                t["calls"] += 1
                t["usage_missing"] += 1 if r.get("usage_missing") else 0
                for f in ("prompt", "cached", "completion"):
                    t[f] += r[f]
                t["usd"] += r["usd"]
            else:
                t["failed"] += 1
        return out

    # ---- proxy ----
    def _handler(self):
        meter = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, BrokenPipeError):
                    pass

            def _route(self):
                sp = urlsplit(self.path)
                parts = sp.path.split("/")                   # /m/<task>/<arm>/<rep>/v1/<rest>
                if len(parts) > 5 and parts[1] == "m":
                    rest = "/" + "/".join(parts[6:])
                    meta = {"task": parts[2], "arm": parts[3], "rep": parts[4]}
                else:
                    rest = sp.path[3:] if sp.path.startswith("/v1") else sp.path
                    meta = {"task": "", "arm": "", "rep": ""}
                return meta, rest + (("?" + sp.query) if sp.query else "")

            def _body(self) -> bytes | None:
                if "chunked" in (self.headers.get("transfer-encoding") or "").lower():
                    return _read_chunked(self.rfile)
                n = int(self.headers.get("content-length") or 0)
                return self.rfile.read(n) if n else None

            def _reply(self, status: int, payload: dict) -> None:
                msg = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)

            def _log(self, meta, rest, model, status, t0, use, extra=None):
                n = {"prompt": 0, "cached": 0, "completion": 0, **(use or {})}
                row = {**meta, "path": rest, "model": model, "status": status, "secs": round(time.time() - t0, 2),
                       **n, "usd": cost_usd(n, meter.prices), "usage_missing": not use, **(extra or {})}
                with meter.lock:
                    meter.calls.append(row)
                    if 200 <= status < 300:
                        meter.spent += row["usd"]

            def _go(self, method: str):
                meta, rest = self._route()
                body = self._body()
                t0, model, stream = time.time(), "", False
                plain = _decode_body(body, self.headers.get("content-encoding")) if body else None
                if plain and ("json" in (self.headers.get("content-type") or "") or plain[:1] == b"{"):
                    try:
                        j = json.loads(plain)
                        model, stream = str(j.get("model", "")), bool(j.get("stream"))
                        if stream and rest.split("?")[0].endswith("/chat/completions") and plain is body:
                            j.setdefault("stream_options", {})["include_usage"] = True   # ask for the usage chunk
                            body = json.dumps(j).encode()
                    except ValueError:
                        pass
                is_model_call = bool(MODEL_PATH.search(rest.split("?")[0])) and method == "POST"
                if is_model_call and meter.exceeded:
                    with meter.lock:
                        meter.blocked += 1
                        meter.blocked_keys.add((meta["task"], meta["arm"], str(meta["rep"])))
                    return self._reply(402, {"error": {"type": "budget", "message":
                                                       f"meter: budget of ${meter.max_usd:.2f} spent; request refused"}})
                hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
                if meter.azure_key:
                    hdrs = {k: v for k, v in hdrs.items() if k.lower() not in ("authorization", "api-key")}
                    hdrs["api-key"] = meter.azure_key
                if meter.extra_query:
                    rest += ("&" if "?" in rest else "?") + meter.extra_query
                if body is not None:
                    hdrs["Content-Length"] = str(len(body))
                req = urllib.request.Request(meter.upstream + rest, data=body, method=method, headers=hdrs)
                try:
                    resp = urllib.request.urlopen(req, timeout=900)
                except urllib.error.HTTPError as e:
                    resp = e
                except Exception as e:  # noqa: BLE001
                    if is_model_call:
                        self._log(meta, rest, model, 502, t0, None, {"error": str(e)[:200]})
                    return self._reply(502, {"error": f"meter: upstream unreachable: {e}"})
                status = resp.status if hasattr(resp, "status") else resp.code
                ctype = resp.headers.get("content-type", "")
                self.send_response(status)
                for k, v in resp.headers.items():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                err = ""
                logged = False

                def log_once(u: dict) -> None:           # the row is written BEFORE the reply is finished, so a client
                    nonlocal logged                      # that exits on the last byte never reads totals() too early
                    if is_model_call and not logged:
                        logged = True
                        self._log(meta, rest, model, status, t0, u, {"error": err} if err else None)

                if "text/event-stream" in ctype:
                    sse = SSE()
                    self.send_header("transfer-encoding", "chunked")
                    self.end_headers()
                    try:
                        while True:
                            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                            if not chunk:
                                break
                            sse.feed(chunk)
                            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        err = "client closed the stream early"
                    except Exception as e:  # noqa: BLE001
                        err = f"stream: {e}"[:200]
                    sse.close()
                    log_once(usage_numbers(sse.usage))
                    try:
                        self.wfile.write(b"0\r\n\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    data = resp.read()
                    use: dict = {}
                    if data.lstrip()[:5] in (b"data:", b"event"):          # a gateway that streams anyway
                        sse = SSE()
                        sse.feed(data)
                        sse.close()
                        use = usage_numbers(sse.usage)
                    else:
                        try:
                            use = usage_numbers(usage_in_event(json.loads(data or b"{}")))
                        except ValueError:
                            pass
                    log_once(use)
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            def do_POST(self):
                self._go("POST")

            def do_GET(self):
                self._go("GET")

        return H
