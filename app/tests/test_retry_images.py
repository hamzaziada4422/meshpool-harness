"""Release review nit: Try again dropped the message's images and held the request thread while the old turn stopped
(up to 25 s). The images go again, and a running turn is waited for in the background: the request returns at once
and the new attempt streams in."""
import base64
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from meshpool_harness.agent import core  # noqa: E402
from test_rescore2_behaviour import _idle, _until, chat, home  # noqa: E402,F401

PNG = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()


def _images_in(body) -> int:
    n = 0
    for m in body.get("messages") or []:
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            n += sum(1 for p in m["content"] if isinstance(p, dict) and p.get("type") == "image_url")
    return n


def test_try_again_while_a_turn_hangs_returns_at_once_and_sends_the_images_again(chat, monkeypatch):
    A, proj, made = chat
    calls, release = [], threading.Event()

    def fake(self_, base, key, body, stream):
        calls.append(body)
        if len(calls) == 1:                  # a hung connection: it notices the stop only once it is released
            release.wait(30)
            self_._check_stop()
        return {"choices": [{"message": {"role": "assistant", "content": "A red square."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3}}, False
    monkeypatch.setattr(core.Agent, "_post", fake)
    monkeypatch.setenv("MH_RETRY_STOP_S", "8")
    v = A.create({"kind": "code", "harness": "meshpool-code", "task": "describe", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.send("what is in this picture?", images=[{"name": "shot.png", "mime": "image/png", "data": PNG}])
    assert _until(lambda: len(calls) == 1 and s.state == "working", 30)
    first = _images_in(calls[0])
    assert first == 1, calls[0]

    t0 = time.perf_counter()
    try:
        r = A.session_action(s.id, "retry", {})
        took = time.perf_counter() - t0
    finally:
        release.set()
    assert r["sent"] == {"retrying": True}, r["sent"]
    assert took < 1.0, took                                    # the request thread is not held while it stops
    assert _until(lambda: len(calls) == 2, 30)
    _idle(s)
    assert _images_in(calls[1]) == 1, calls[1]                 # the picture went again
    users = [e for e in s.events if e.get("type") == "user"]
    assert len(users) == 1 and users[0].get("images") == ["shot.png"], users


def test_try_again_on_an_idle_chat_sends_the_images_again(chat, monkeypatch):
    A, proj, made = chat
    calls = []

    def fake(self_, base, key, body, stream):
        calls.append(body)
        return {"choices": [{"message": {"role": "assistant", "content": "A red square."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3}}, False
    monkeypatch.setattr(core.Agent, "_post", fake)
    v = A.create({"kind": "code", "harness": "meshpool-code", "task": "describe", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.send("what is in this picture?", images=[{"name": "shot.png", "mime": "image/png", "data": PNG}])
    _idle(s)
    s._last_images = None                                      # read back from the model's copy, as after a restart
    A.session_action(s.id, "retry", {})
    assert _until(lambda: len(calls) == 2, 30)
    _idle(s)
    assert _images_in(calls[0]) == 1 and _images_in(calls[1]) == 1, (calls[0], calls[1])
