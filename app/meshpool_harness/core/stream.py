"""Transport helpers shared by every model-reply reader: SSE framing with a keep-alive clock, a body read with a
total deadline, a streaming <think> splitter, request-body encoding, Retry-After parsing and message preparation.

The chat assembly (wire.sse_to_chat), the Anthropic assembler (anthropic_wire.StreamAssembler) and the reply
normaliser (wire.normalize_reply) live elsewhere; this module only moves bytes and text.
"""
from __future__ import annotations

import datetime as _dt
import email.utils
import json
import math
import re
import time


class StreamCut(RuntimeError):
    """The stream ended before the reply was complete. The message starts with "HTTP 0:" so classify_error reads it
    as transient and the call is retried."""

    def __init__(self, detail: str = ""):
        super().__init__("HTTP 0: the stream ended before the reply was complete" + (f" ({detail})" if detail else ""))


def _is_ping(event: str, data: str) -> bool:
    if event == "ping":
        return True
    if '"ping"' not in data:
        return False
    try:
        v = json.loads(data)
    except ValueError:
        return False
    return isinstance(v, dict) and v.get("type") == "ping"


def iter_sse(lines, *, data_idle: float | None = None, clock=time.monotonic, check=None):
    """Yield (event, data) per dispatched SSE event. Keep-alives (comments, blank lines, ping events) do not count as
    data: if only those arrive for more than data_idle seconds the read ends, so a server that never answers cannot
    hold a call forever. `check` runs once per line (the agent raises on Stop there)."""
    event, buf = "message", []
    last_data = clock()

    def dispatch():
        nonlocal event, buf, last_data
        ev, data = event, "\n".join(buf)
        had = bool(buf)
        event, buf = "message", []
        if had and not _is_ping(ev, data):
            last_data = clock()
        return (ev, data) if had else None

    for raw in lines:
        if check:
            check()
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        line = line.rstrip("\r\n")
        # a data line is data by itself; only the lines around it can be keep-alives
        if data_idle and not buf and not line.startswith("data") and clock() - last_data > data_idle:
            raise RuntimeError(f"HTTP 0: connection problem (timed out: only keep-alives for {int(data_idle)} s)")
        if line == "":
            out = dispatch()
            if out:
                yield out
            continue
        if line.startswith(":"):
            continue
        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if name == "data":
            buf.append(value)
        elif name == "event":
            event = value or "message"
    out = dispatch()                                     # lenient: a last event without its blank line
    if out:
        yield out


# ---- <think> splitting (H-17) --------------------------------------------------------------------------------------

_OPEN = ("<think>", "<thinking>")
_CLOSE = ("</think>", "</thinking>")


class ThinkSplitter:
    """Streaming splitter: text inside <think>..</think> goes to reasoning, the rest to content. A tag cut across
    chunks is held back until it is complete."""

    def __init__(self):
        self.inside, self.hold, self.opened, self.closed = False, "", False, False
        self._skip_ws = False                            # the blank lines between </think> and the answer

    def feed(self, text: str) -> tuple[str, str]:
        buf, content, reasoning = self.hold + text, [], []
        self.hold = ""
        while buf:
            tags = _CLOSE if self.inside else _OPEN
            hits = [(buf.find(t), t) for t in tags if t in buf]
            if hits:
                i, t = min(hits, key=lambda h: (h[0], -len(h[1])))
                (reasoning if self.inside else content).append(buf[:i])
                buf = buf[i + len(t):]
                self.inside = not self.inside
                if self.inside:
                    self.opened = True
                else:
                    self.closed, self._skip_ws = True, True
                continue
            keep = 0                                     # longest suffix that could still become a tag
            for t in tags:
                for n in range(min(len(t) - 1, len(buf)), keep, -1):
                    if buf.endswith(t[:n]):
                        keep = n
                        break
            head, self.hold = (buf[:-keep], buf[-keep:]) if keep else (buf, "")
            (reasoning if self.inside else content).append(head)
            break
        c = "".join(content)
        if self._skip_ws and c:
            c = c.lstrip()
            self._skip_ws = not c
        return c, "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        h, self.hold = self.hold, ""
        return ("", h) if self.inside else (h, "")



def read_body(r, deadline_s: float, clock=time.monotonic) -> bytes:
    """Read a whole body, but give up once deadline_s has passed in total (a socket timeout only bounds each read)."""
    start, parts = clock(), []
    read = getattr(r, "read1", None) or (lambda: r.read(65536))
    while True:
        if clock() - start > deadline_s:
            raise RuntimeError(f"HTTP 0: connection problem (timed out: the reply body took longer than {deadline_s:g} s)")
        chunk = read()
        if not chunk:
            return b"".join(parts)
        parts.append(chunk)




_SURR = re.compile(r"[\ud800-\udfff]")


def _clean(o):
    if isinstance(o, str):
        return _SURR.sub("�", o)
    if isinstance(o, dict):
        return {_clean(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def encode_body(body) -> bytes:
    """JSON request body as UTF-8 with no lone surrogates anywhere (a strict server answers 400 to \\ud800)."""
    return json.dumps(_clean(body), ensure_ascii=False).encode("utf-8")


def _header(headers, name: str):
    for k in (name, name.lower(), "-".join(p.capitalize() for p in name.split("-"))):
        try:
            v = headers.get(k)
        except Exception:                                # noqa: BLE001
            return None
        if v is not None:
            return str(v).strip()
    return None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def retry_after_seconds(headers) -> float | None:
    """Seconds the server asks us to wait: retry-after-ms, Retry-After delta-seconds, or an HTTP date."""
    if not headers:
        return None
    ms = _num(_header(headers, "retry-after-ms"))
    if ms is not None:
        return max(0.0, ms / 1000)
    v = _header(headers, "retry-after")
    if not v:
        return None
    s = _num(v)
    if s is not None:
        return max(0.0, s)
    try:
        dt = email.utils.parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return max(0.0, dt.timestamp() - time.time())


def prepare_messages(messages) -> list:
    """reasoning_content stays only on assistant turns after the last user message (it belongs to the tool loop in
    progress); older turns lose it. The input is never changed."""
    last_user = max((i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "user"), default=-1)
    out = []
    for i, m in enumerate(messages):
        if isinstance(m, dict) and "reasoning_content" in m and not (m.get("role") == "assistant" and i > last_user):
            m = {k: v for k, v in m.items() if k != "reasoning_content"}
        out.append(m)
    return out
