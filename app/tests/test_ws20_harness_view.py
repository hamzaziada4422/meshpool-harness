"""An app chat running on Pi or Codex (ACP) stays readable after its first turn (K-03 bench: every poll returned
HTTP 500, so the chat never showed as finished)."""
import json
import types

from meshpool_harness.ui import agent_api as A


def _chat(tmp_path, monkeypatch, messages):
    monkeypatch.setattr(A, "SESSIONS", tmp_path)
    (tmp_path / "c1").mkdir()
    (tmp_path / "c1" / "session.json").write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}))
    return types.SimpleNamespace(id="c1", state="idle", pendings={}, _saved_mtime=0.0,
                                 agent=types.SimpleNamespace(messages=messages))


def test_placeholder_message_list_is_not_stale_and_does_not_raise(tmp_path, monkeypatch):
    s = _chat(tmp_path, monkeypatch, [None])          # what PiRpcAgent and AcpAgent keep
    assert A._stale(s) is False


def test_meshpool_agent_still_detects_another_writer(tmp_path, monkeypatch):
    s = _chat(tmp_path, monkeypatch, [{"role": "system", "content": "x"}])
    assert A._stale(s) is True                       # saved file has a message this copy does not


def test_pi_and_acp_agents_use_the_placeholder():
    import inspect
    from meshpool_harness.harnesses import acp, pi_rpc
    assert "self.messages = [None]" in inspect.getsource(pi_rpc) and "self.messages = [None]" in inspect.getsource(acp)
