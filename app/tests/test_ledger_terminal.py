"""Terminal and session features from the feature ledger: prompt history between sessions, the session list for
/resume, the /context breakdown, /export, effort levels and the thinking stream. The real terminal (Esc to stop,
Up arrow after a restart) is checked by testkit/tui_pty.py."""
import json

from meshpool_harness.agent import core
from meshpool_harness.agent import tui_more as TM


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    (tmp_path / "home").mkdir()


def test_history_is_kept_per_folder(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for t in ("first", "second", "second", "third"):
        TM.add_history(str(a), t)
    TM.add_history(str(b), "elsewhere")
    assert TM.load_history(str(a)) == ["first", "second", "third"]
    assert TM.load_history(str(b)) == ["elsewhere"]
    monkeypatch.setenv("MH_HISTORY", "0")
    assert TM.load_history(str(a)) == []


def test_sessions_list_and_resume(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    ag = core.Agent(str(tmp_path), mode="auto")
    ag._post = lambda *x: ({"choices": [{"message": {"role": "assistant", "content": "hi there"},
                                         "finish_reason": "stop"}], "usage": {}}, False)
    ag.send("hello world")
    rows = TM.sessions(str(tmp_path))
    assert rows and rows[0]["id"] == ag.id and rows[0]["messages"] >= 2
    back = core.Agent.resume(str(tmp_path), ag.id)
    assert back and TM.replies(back) == ["hi there"]
    md = TM.transcript(back)
    assert "## You" in md and "hello world" in md and "hi there" in md


def test_context_breakdown_names_the_parts(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    (tmp_path / "AGENTS.md").write_text("Use tabs. " * 200, encoding="utf-8")
    ag = core.Agent(str(tmp_path), mode="auto")
    rows = dict(TM.context_breakdown(ag))
    assert rows["Tool definitions"] > 100 and rows["Instruction files (MESHPOOL.md, AGENTS.md, CLAUDE.md)"] >= 400
    assert rows["System prompt"] > 100


# effort: main's levels and wiring are tested in test_ws23_effort.py


def test_thinking_streams_as_thought_events(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    import io

    class Resp(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    chunks = [{"choices": [{"delta": {"reasoning_content": "Let me think. "}}]},
              {"choices": [{"delta": {"content": "Answer."}, "finish_reason": "stop"}]}]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    monkeypatch.setattr(core, "_urlopen", lambda req, t, sink: Resp(body.encode()))
    ag = core.Agent(str(tmp_path), mode="auto")
    seen = []
    ag.emit = seen.append
    resp, streamed = ag._post("http://x/v1", "k", {"model": "m", "messages": []}, True)
    assert [e["text"] for e in seen if e.get("type") == "thought" and e.get("delta")] == ["Let me think. "]
    assert [e["text"] for e in seen if e.get("type") == "thought" and e.get("final")] == ["Let me think. "]
    assert resp["choices"][0]["message"]["content"] == "Answer."


def test_session_api_sets_effort(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    from meshpool_harness.ui import agent_api as A
    s = A.Session.__new__(A.Session)
    s.agent = core.Agent(str(tmp_path), mode="auto")
    s.id = s.agent.id
    s.set_effort("high")
    assert s.agent.effort == "high"
    import pytest
    with pytest.raises(ValueError):
        s.set_effort("extreme")
