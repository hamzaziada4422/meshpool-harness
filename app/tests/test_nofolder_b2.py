"""B2 (0.0.18 release review): the no-folder rewrite runs on every assistant event in a no-folder chat, so it must be
fast on long replies, leave correct replies alone, and never cut inside a word or a file name."""
import threading
import time

from meshpool_harness import nofolder as NF


def test_a_long_adversarial_reply_is_checked_in_well_under_a_fifth_of_a_second():
    unit = "we run the numbers on each tool and use the session data, then try again with more rows, "
    bad = (unit * 300)[:20_000]
    worst = ("run this in a session that has a shell and use the terminal environment with file writing " * 300)[:20_000]
    for text in (bad, worst, bad.replace(",", ".x"), "a." * 10_000):
        t0 = time.perf_counter()
        NF.wants_folder(text)
        NF.plain(text)
        assert time.perf_counter() - t0 < 0.2, len(text)


def test_correct_replies_are_left_unchanged():
    for good in ("Run the tests in a terminal with the shell command `pytest -q`.",
                 "Here is the chart. You can run the tool in a terminal with the shell to rebuild it from plot.py."):
        assert not NF.wants_folder(good), good
        assert NF.plain(good) == good


def test_a_real_deflection_is_still_rewritten_and_file_names_stay_whole():
    bad = ("I wrote the outline from notes.md.\n\nTo get the file, run this in a session that has a shell and file "
           "writing. I can also write the slide text here.")
    assert NF.wants_folder(bad)
    out = NF.plain(bad)
    assert "session that has" not in out and NF.OFFER in out, out
    assert "notes.md." in out and "I can also write the slide text here." in out, out
    two = "Open another session with a terminal to save deck.pptx. Or use an environment that has file writing."
    out = NF.plain(two)
    assert out.count(NF.OFFER) == 1 and "deck.pptx" not in out and ".pptx" not in out, out


def test_a_turn_that_made_a_file_is_not_rewritten():
    bad = "Saved report.docx. To change it later, run this in a session that has a shell."
    assert NF.plain(bad, made_file=True) == bad
    assert not NF.wants_folder(bad, made_file=True)


def _session(wd):
    from meshpool_harness.ui import agent_api as A
    s = A.Session.__new__(A.Session)
    s.workdir = str(wd)
    got = []
    s.listeners = [got.append]
    s.lock = threading.RLock()
    s.changed = threading.Condition(s.lock)
    s.events, s.state, s.queue = [], "idle", []
    for k, v in dict(_open_deltas=False, _open_thoughts=False, _retry_open=False, _interrupting=None, _turn_id=0,
                     _turn_open=False, pendings={}, harness={"type": "x", "id": "x"}).items():
        setattr(s, k, v)
    s._persist = s._persist_soon = lambda *a, **k: None
    s._question_state = lambda ev: None
    return s, got


def test_the_session_keeps_the_reply_when_the_turn_wrote_a_file(tmp_path, monkeypatch):
    from meshpool_harness import project_store as PS
    monkeypatch.setattr(PS, "is_scratch", lambda p: True)
    s, got = _session(tmp_path)
    s.events = [{"type": "user", "text": "make a deck"},
                {"type": "tool_start", "id": 1, "name": "make_presentation"},
                {"type": "tool_end", "id": 1, "ok": True}]
    reply = "Saved deck.pptx. To edit it by hand, run python in a session that has a shell."
    s.emit({"type": "assistant", "text": reply})
    assert [e["type"] for e in got] == ["assistant"] and got[0]["text"] == reply
