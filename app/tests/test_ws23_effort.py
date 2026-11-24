"""G-08: one reasoning-effort setting (MH_REASONING_EFFORT, --effort, /effort, per chat) reaching every wire."""
import json
import os

import pytest

from meshpool_harness import chats, cli
from meshpool_harness.agent import core
from meshpool_harness.core import anthropic_wire as AW
from meshpool_harness.core import chatgpt as CG
from meshpool_harness.core import modelcaps as MC
from meshpool_harness.ui import agent_api as A
from ws23_kit import finish, home, new_chat, proj  # noqa: F401

CAPS = json.dumps({"reasoning": "effort", "efforts": ["low", "medium", "high"]})


def capture(monkeypatch, caps=CAPS):
    """Real request building; the HTTP send is replaced and records the body."""
    monkeypatch.setenv("MH_CAPS", caps)
    MC._CACHE.clear()
    sent = []

    def fake_post(self, base, key, body, stream, tok=None):
        sent.append(dict(body))
        return ({"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 5, "completion_tokens": 1}}, False)
    monkeypatch.setattr(core.Agent, "_post", fake_post)
    return sent


def test_chat_wire_gets_the_chats_effort(monkeypatch, proj):
    sent = capture(monkeypatch)
    s = new_chat(proj, effort="low")
    assert s.effort == "low" and s.agent.effort == "low" and s.view(0)["effort"] == "low"
    finish(s, "hello")
    assert sent and sent[0]["reasoning_effort"] == "low"


def test_setting_applies_and_a_chat_overrides_it(monkeypatch, proj):
    sent = capture(monkeypatch)
    monkeypatch.setenv("MH_REASONING_EFFORT", "high")
    s = new_chat(proj)
    finish(s, "one")
    assert sent[-1]["reasoning_effort"] == "high"
    chats.session_action(s.id, "effort", {"effort": "medium"})
    finish(s, "two")
    assert sent[-1]["reasoning_effort"] == "medium"
    chats.session_action(s.id, "effort", {"effort": "off"})
    finish(s, "three")
    assert "reasoning_effort" not in sent[-1]
    with pytest.raises(ValueError):
        chats.session_action(s.id, "effort", {"effort": "extreme"})


def test_unsupported_level_is_reported_once_and_never_sent(monkeypatch, proj):
    sent = capture(monkeypatch, caps=json.dumps({"reasoning": "none"}))
    s = new_chat(proj, effort="low")
    finish(s, "one")
    finish(s, "two")
    assert all("reasoning_effort" not in b for b in sent)
    notes = [e for e in s.view(0)["events"] if e["type"] == "status" and "does not take effort 'low'" in e["text"]]
    assert len(notes) == 1


def test_effort_persists_across_reopen(monkeypatch, proj):
    capture(monkeypatch)
    s = new_chat(proj)
    s.set_effort("high")
    sid = s.id
    s.close()
    A.LIVE.clear()
    again = chats.get(sid, no_mcp=True)
    assert again.effort == "high" and again.agent.effort == "high" and chats.read_meta(sid)["effort"] == "high"


def test_responses_wire_names_it_reasoning_effort():
    from meshpool_harness.core.wire import SimpleLedger
    extra = {"reasoning_effort": "low"}
    # the app's agent turns it into reasoning.effort on the Responses call
    import inspect
    assert 'extra["reasoning"] = {"effort": extra.pop("reasoning_effort")}' in inspect.getsource(core.Agent)


def test_anthropic_wire_maps_effort():
    body = {"model": "claude-opus-4-5", "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "high",
            "temperature": 0.3}
    out = AW.to_request(body, cache=False)
    assert out["output_config"] == {"effort": "high"} and "reasoning_effort" not in out and "thinking" not in out
    old = AW.to_request(dict(body, model="claude-sonnet-4-5", reasoning_effort="medium", max_tokens=1000), cache=False)
    assert old["thinking"] == {"type": "enabled", "budget_tokens": 8192} and old["max_tokens"] > 8192
    assert "temperature" not in old and "reasoning_effort" not in old
    assert "thinking" not in AW.to_request({"model": "claude-opus-4-5", "messages": []}, cache=False)


def test_chatgpt_payload_carries_reasoning_effort():
    out = CG.to_request({"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "low"}, "gpt-x")
    assert out["reasoning"] == {"effort": "low"}
    assert "reasoning" not in CG.to_request({"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]}, "gpt-x")


def test_cli_flag_reaches_the_chat_body_and_the_environment(monkeypatch):
    got = {}
    from meshpool_harness.agent import tui
    monkeypatch.setattr(tui, "main", lambda *a, **k: got.update(k) or 0)
    monkeypatch.setenv("MH_EFFORT", "medium")                       # the old name still works
    monkeypatch.setenv("MH_REASONING_EFFORT", "")                   # so monkeypatch restores what cli.main sets
    assert cli.main(["-p", "hi"]) == 0
    assert os.environ["MH_REASONING_EFFORT"] == "medium"
    assert cli.main(["-p", "hi", "--effort", "high"]) == 0
    assert got["effort"] == "high" and os.environ["MH_REASONING_EFFORT"] == "high"


def test_codex_model_id_uses_the_effort_level():
    from meshpool_harness.harnesses import acp as ACP
    sent = []
    a = ACP.ACPAgent.__new__(ACP.ACPAgent)
    a.model, a.effort, a.acp_session, a.model_now = "deepseek-v4.1-flash", "high", "s1", "deepseek-v4.1-flash[medium]"
    a.available_models = [{"id": "deepseek-v4.1-flash[medium]"}, {"id": "deepseek-v4.1-flash[high]"}]
    a.manifest = {"name": "Codex"}
    a._request = lambda method, params, timeout=0: sent.append(params["modelId"])
    a._apply_model()
    assert sent == ["deepseek-v4.1-flash[high]"] and a.model_now == "deepseek-v4.1-flash[high]"


def test_settings_page_lists_the_effort_field():
    from meshpool_harness import settings_api as SA
    assert "MH_REASONING_EFFORT" in {r["key"] for r in SA.values()}
