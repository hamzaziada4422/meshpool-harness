"""Taking back a message sent while the agent works."""
from meshpool_harness.agent import core as K


def test_agent_take_back_before_it_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "s")
    ev = []
    a = K.Agent(str(tmp_path), mode="auto", emit=ev.append)
    a.steer("also check the README\n\n​(style note)")
    a.steer("and the changelog")
    assert a.take_back() == "and the changelog"
    assert ev[-1] == {"type": "unsteer", "text": "and the changelog"}
    assert a._take_inbox() is True and ev[-1] == {"type": "steer_read", "n": 1}
    assert "check the README" in a.messages[-1]["content"]
    assert a.take_back() is None                                    # already read


def test_session_take_back_from_the_queue():
    from meshpool_harness.ui import agent_api as A
    s = A.Session.__new__(A.Session)
    import threading
    s.lock, s.queue, events = threading.Lock(), [("first", None), ("second", None)], []
    s.emit = events.append
    s.agent = object()
    assert s.take_back() == {"text": "second"} and events[-1] == {"type": "queued", "text": "first", "n": 1}
    assert s.take_back() == {"text": "first"} and events[-1]["n"] == 0
    assert s.take_back() == {"text": None}
