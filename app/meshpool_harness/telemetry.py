"""OpenTelemetry export (G-16): spans and metrics over OTLP/HTTP JSON, standard library only.

Off unless MH_OTEL=1 and an endpoint is set (OTEL_EXPORTER_OTLP_ENDPOINT or MH_OTEL_ENDPOINT; headers from
OTEL_EXPORTER_OTLP_HEADERS). Attributes carry ids, names, counts, durations and status; prompt, reply and tool-argument
text (cut to 2 KB) goes out only with MH_OTEL_CONTENT=1. Sending happens on a daemon thread with a 5 s timeout and every
error is swallowed, so a collector that is down never slows or breaks a turn.

One entry point for every front end: Session.__init__ adds `listener(session)` to its listeners, so the app, the terminal,
scheduled runs and every harness report the same way; `cli.run_request` wraps engine runs in `span("meshpool.run")`.
Cost is the receipt's own number, never recomputed.
"""
from __future__ import annotations

import atexit
import contextlib
import contextvars
import json
import os
import queue
import secrets
import threading
import time
import urllib.request

_Q: "queue.Queue" = queue.Queue(maxsize=5000)
_THREAD: list = [None]
_LOCK = threading.Lock()
_PARENT: contextvars.ContextVar = contextvars.ContextVar("mh_otel_span", default=None)


def endpoint() -> str:
    return (os.environ.get("MH_OTEL_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip().rstrip("/")


def enabled() -> bool:
    return os.environ.get("MH_OTEL", "").strip().lower() in ("1", "true", "yes", "on") and bool(endpoint())


def content_on() -> bool:
    return os.environ.get("MH_OTEL_CONTENT", "").strip() == "1"


def _text(v) -> str:
    return str(v or "")[:2048]


def _attr(k: str, v) -> dict:
    if isinstance(v, bool):
        val = {"boolValue": v}
    elif isinstance(v, int):
        val = {"intValue": str(v)}
    elif isinstance(v, float):
        val = {"doubleValue": v}
    else:
        val = {"stringValue": str(v)}
    return {"key": k, "value": val}


def _attrs(d: dict | None) -> list:
    return [_attr(k, v) for k, v in (d or {}).items() if v is not None]


def _resource() -> dict:
    try:
        from meshpool_harness import __version__ as ver
    except Exception:  # noqa: BLE001
        ver = ""
    fe = os.environ.get("MESHPOOL_FRONTEND") or "app"
    return {"attributes": _attrs({"service.name": "meshpool", "service.version": ver, "meshpool.frontend": fe})}


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    for part in os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "").split(","):
        k, _, v = part.partition("=")
        if k.strip() and v.strip():
            h[k.strip()] = v.strip()
    return h


def _post(path: str, doc: dict) -> None:
    try:
        req = urllib.request.Request(endpoint() + path, data=json.dumps(doc).encode(), method="POST", headers=_headers())
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:  # noqa: BLE001 - a collector that is down is not the user's problem
        pass


def _send(items: list) -> None:
    spans = [x[1] for x in items if x[0] == "span"]
    metrics = [x[1] for x in items if x[0] == "metric"]
    scope = {"name": "meshpool"}
    if spans:
        _post("/v1/traces", {"resourceSpans": [{"resource": _resource(), "scopeSpans": [{"scope": scope, "spans": spans}]}]})
    if metrics:
        _post("/v1/metrics", {"resourceMetrics": [{"resource": _resource(),
                                                   "scopeMetrics": [{"scope": scope, "metrics": metrics}]}]})


def _worker() -> None:
    while True:
        items = [_Q.get()]
        time.sleep(0.2)                              # batch what arrives in the next moment
        while True:
            try:
                items.append(_Q.get_nowait())
            except queue.Empty:
                break
        try:
            _send([i for i in items if i is not None])
        finally:
            for _ in items:
                _Q.task_done()


def _put(kind: str, doc: dict) -> None:
    with _LOCK:
        if _THREAD[0] is None:
            t = threading.Thread(target=_worker, name="meshpool-otel", daemon=True)
            t.start()
            _THREAD[0] = t
    try:
        _Q.put_nowait((kind, doc))
    except queue.Full:
        pass


def flush(timeout: float = 5.0) -> None:
    """Wait (up to `timeout`) for what is queued to be sent: at exit, and in tests."""
    if _THREAD[0] is None:
        return
    done = threading.Event()
    threading.Thread(target=lambda: (_Q.join(), done.set()), daemon=True).start()
    done.wait(timeout)


atexit.register(lambda: flush(2.0) if enabled() else None)


def _ns(t: float) -> str:
    return str(int(t * 1e9))


class _Span:
    def __init__(self, name: str, attrs: dict | None, parent: "_Span | None", trace: str | None = None):
        self.name, self.attrs = name, dict(attrs or {})
        self.trace = trace or (parent.trace if parent else secrets.token_hex(16))
        self.id, self.parent = secrets.token_hex(8), parent.id if parent else ""
        self.start, self.status = time.time(), 1

    def end(self, ok: bool = True, **attrs) -> None:
        self.attrs.update(attrs)
        doc = {"traceId": self.trace, "spanId": self.id, "name": self.name, "kind": 1,
               "startTimeUnixNano": _ns(self.start), "endTimeUnixNano": _ns(time.time()),
               "attributes": _attrs(self.attrs), "status": {"code": 1 if ok else 2}}
        if self.parent:
            doc["parentSpanId"] = self.parent
        _put("span", doc)


def start(name: str, attrs: dict | None = None, parent: "_Span | None" = None) -> "_Span | None":
    return _Span(name, attrs, parent) if enabled() else None


@contextlib.contextmanager
def span(name: str, attrs: dict | None = None):
    """A span around a block; nothing happens (and nothing is imported or sent) while telemetry is off."""
    sp = start(name, attrs, _PARENT.get())
    if sp is None:
        yield None
        return
    tok = _PARENT.set(sp)
    ok = True
    try:
        yield sp
    except BaseException:
        ok = False
        raise
    finally:
        _PARENT.reset(tok)
        sp.end(ok)


def counter(name: str, value, attrs: dict | None = None) -> None:
    if not enabled():
        return
    now = _ns(time.time())
    pt = {"attributes": _attrs(attrs), "startTimeUnixNano": now, "timeUnixNano": now}
    pt["asInt" if isinstance(value, int) else "asDouble"] = str(value) if isinstance(value, int) else float(value)
    _put("metric", {"name": name, "sum": {"aggregationTemporality": 1, "isMonotonic": True, "dataPoints": [pt]}})


class _Turn:
    """What one chat's events have opened so far: the turn, the model call, the tools."""

    def __init__(self, session):
        self.s, self.turn, self.call, self.tools, self.err = session, None, None, {}, ""


def on_event(st: "_Turn", ev: dict) -> None:
    """Map one Session event to spans and metrics. Called for every event; cheap when nothing applies."""
    t = ev.get("type")
    s = st.s
    base = {"meshpool.session": s.id, "meshpool.harness": (getattr(s, "harness", None) or {}).get("id", "")}
    if t == "user" and not ev.get("steer"):
        if st.turn:
            st.turn.end(True)
        st.err = ""
        a = dict(base, **{"meshpool.kind": getattr(s, "kind", "")})
        if content_on():
            a["meshpool.prompt"] = _text(ev.get("text"))
        st.turn = start("meshpool.turn", a)
    elif t == "model_start":
        model = ""
        try:
            model = str(s.agent.current_model() if hasattr(s.agent, "current_model") else getattr(s.agent, "model", "") or "")
        except Exception:  # noqa: BLE001
            pass
        st.call = start("meshpool.model_call", {"gen_ai.request.model": model}, st.turn)
    elif t == "model_end" and st.call:
        st.call.end(True)
        st.call = None
    elif t == "usage":
        model = ev.get("model") or ""
        for kind in ("in", "cached", "out"):
            counter("gen_ai.client.token.usage", int(ev.get(kind) or 0),
                    {"gen_ai.token.type": kind, "gen_ai.request.model": model, "meshpool.aux": bool(ev.get("aux"))})
        counter("meshpool.model.calls", int(ev.get("calls") or 1), {"gen_ai.request.model": model})
    elif t == "tool_start":
        a = {"tool.name": ev.get("name", ""), "meshpool.mcp": bool(ev.get("mcp"))}
        if content_on():
            a["meshpool.tool.args"] = _text(ev.get("summary"))
        st.tools[ev.get("id")] = start("meshpool.tool", a, st.turn)
    elif t == "tool_end":
        sp = st.tools.pop(ev.get("id"), None)
        counter("meshpool.tool.calls", 1, {"tool.name": ev.get("name", ""), "ok": bool(ev.get("ok"))})
        if sp:
            sp.attrs["ok"] = bool(ev.get("ok"))
            sp.end(bool(ev.get("ok")), denied=bool(ev.get("denied")))
    elif t == "assistant" and st.turn and content_on():
        st.turn.attrs["meshpool.reply"] = _text(ev.get("text"))
    elif t == "task_receipt":
        if isinstance(ev.get("cost"), (int, float)):
            counter("meshpool.cost.usd", float(ev["cost"]), {"gen_ai.request.model": str(ev.get("model") or "")})
    elif t == "error":
        st.err = "error"
    elif t in ("turn_end", "interrupted") and (t == "turn_end" or not ev.get("restarted")):
        if st.call:
            st.call.end(False)
            st.call = None
        for sp in st.tools.values():
            if sp:
                sp.end(False)
        st.tools.clear()
        if st.turn:
            st.turn.end(not st.err and t != "interrupted", **({"meshpool.interrupted": True} if t == "interrupted" else {}))
            st.turn = None


def listener(session):
    """The callable Session.__init__ adds to its listeners, or None while telemetry is off."""
    if not enabled():
        return None
    st = _Turn(session)

    def fn(ev: dict) -> None:
        try:
            on_event(st, ev)
        except Exception:  # noqa: BLE001 - telemetry never touches a turn
            pass
    return fn
