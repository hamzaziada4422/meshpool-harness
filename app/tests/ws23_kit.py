"""Shared fixtures for the ws23 chunk B/C tests: a temp Meshpool home, a scripted model, one finished chat helper."""
import json
import time

import pytest

from meshpool_harness import chats
from meshpool_harness.agent import core
from meshpool_harness.ui import agent_api as A


def call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test-key-123456")
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    sessions = tmp_path / "runs" / "sessions"
    monkeypatch.setattr(core, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(chats, "SESSIONS", sessions)
    for k in ("MESHPOOL_FRONTEND", "MH_PERMISSION_MODE", "MH_AGENT_PROFILE", "MH_REASONING_EFFORT", "MH_OTEL",
              "MH_OTEL_ENDPOINT", "MH_OTEL_CONTENT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    A.LIVE.clear()
    yield
    for s in list(A.LIVE.values()):
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
    A.LIVE.clear()


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def script(monkeypatch, *replies):
    """The model says `replies` in order (a dict is the assistant message; a str is plain text)."""
    it = iter([say(r) if isinstance(r, str) else r for r in replies])
    seen = []

    def fake(self, turn):
        self.emit({"type": "model_start"})
        for k, v in {"in": 100, "cached": 0, "out": 10, "calls": 1}.items():
            turn[k] += v
            self.usage[k] += v
        self.emit({"type": "usage", "in": 100, "cached": 0, "out": 10, "calls": 1, "model": "m"})
        seen.append(list(self.messages))
        self.emit({"type": "model_end"})
        return next(it), False
    monkeypatch.setattr(core.Agent, "_chat", fake)
    return seen


def finish(s, text, timeout=20):
    """Send `text` to the chat and wait for the turn to end."""
    s.send(text)
    end = time.time() + timeout
    time.sleep(0.05)
    with s.changed:
        while s.state in ("working", "waiting") and time.time() < end:
            s.changed.wait(0.2)
    assert s.state not in ("working", "waiting")


def new_chat(proj, **kw):
    v = chats.create({"workdir": str(proj), "no_mcp": True, **kw})
    return chats.get(v["id"], no_mcp=True)
