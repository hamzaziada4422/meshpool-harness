"""SR-11: while the model service was down, each check added a new line to the chat (7 of them for one outage), each
with the raw "HTTP 0: <urlopen error [Errno 111] Connection refused>". The checks now say which attempt comes next,
in plain words, and carry "down" so the page keeps one line and updates it (test_sr_ui.py checks the page)."""
from meshpool_harness.agent import core
from meshpool_harness.agent.core import Agent
from test_agent_robustness import _big, _ok, _turn


def test_endpoint_down_checks_are_one_updatable_line(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RECOVER_MIN", "30")
    monkeypatch.setattr(core.GW, "BACKOFF_S", 0.01)
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)
    _big(a)
    refused = "HTTP 0: <urlopen error [Errno 111] Connection refused>"
    probes, events, calls = iter([refused, refused, refused, "ok"]), [], []
    a.emit = events.append

    def post(base, key, body, stream):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(refused)
        return _ok()
    a._post = post
    a._probe = lambda base, key, model: next(probes)
    msg, _ = a._chat(_turn())
    assert msg["content"] == "ok"
    down = [e for e in events if e.get("type") == "status" and e.get("down") is True]
    assert len(down) >= 2
    assert all("urlopen" not in e["text"] and "Errno" not in e["text"] for e in down)
    tries = [e["attempt"] for e in down if "attempt" in e]
    assert tries == sorted(tries) and len(set(tries)) == len(tries) >= 2
    assert "attempt 2" in [e for e in down if "attempt" in e][0]["text"]
    back = [e for e in events if e.get("type") == "status" and e.get("down") is False]
    assert len(back) == 1 and "answering again" in back[0]["text"]
