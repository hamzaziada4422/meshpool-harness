import time
from meshpool_harness.core.client import LiveClient
from meshpool_harness.router import router
from meshpool_harness.router.rules import decide

VAGUE = "summarize this"


class Boom:
    def __init__(self):
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        raise RuntimeError("HTTP 503 upstream")


def test_failed_hint_keeps_rules_engines():
    eng, conf, _ = decide(VAGUE, [], None, None)
    assert conf < 0.6
    c = Boom()
    r = router.route(VAGUE, client=c)
    assert r.engines == eng
    assert r.used_model_call is True
    assert "routing hint failed" in r.reason and "RuntimeError" in r.reason


def test_failed_hint_on_live_client_is_bounded(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = LiveClient()
    calls = []

    class Stub:
        def chat(self, **kw):
            calls.append(1)
            raise RuntimeError("HTTP 502 upstream")

    c.ledger = Stub()
    r = router.route(VAGUE, client=c)
    assert len(calls) <= 2
    assert r.engines == decide(VAGUE, [], None, None)[0]
    assert "routing hint failed" in r.reason
