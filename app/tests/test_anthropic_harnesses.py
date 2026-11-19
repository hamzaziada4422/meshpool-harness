"""Pi and Hermes on an Anthropic Messages key endpoint: each uses its own Anthropic client instead of chat completions
(which an Anthropic-only endpoint does not serve). Switching back to an OpenAI-compatible endpoint drops it again."""
import json

import pytest


def _key(tmp_path, monkeypatch, base, wire):
    mh = tmp_path / "mh"
    mh.mkdir(exist_ok=True)
    (mh / "meshpool.config").write_text(f"MESHPOOL_BASE_URL={base}\nMESHPOOL_API_KEY=sk-test\nMH_API_WIRE={wire}\n")
    monkeypatch.setenv("MESHPOOL_HOME", str(mh))
    for k in ("MESHPOOL_BASE_URL", "MESHPOOL_API_KEY", "MH_API_WIRE", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def test_pi_uses_anthropic_messages_on_an_anthropic_endpoint(tmp_path, monkeypatch):
    from meshpool_harness.harnesses import pi_rpc
    _key(tmp_path, monkeypatch, "https://api.anthropic.com/v1", "anthropic")
    prov = json.loads((pi_rpc.prepare(tmp_path) / "models.json").read_text())["providers"]["meshpool"]
    assert prov["api"] == "anthropic-messages"
    assert prov["baseUrl"] == "https://api.anthropic.com"          # Anthropic's SDK adds /v1/messages itself
    assert prov["apiKey"] == "$MESHPOOL_API_KEY" and "compat" not in prov


def test_pi_keeps_chat_completions_on_an_openai_endpoint(tmp_path, monkeypatch):
    from meshpool_harness.harnesses import pi_rpc
    _key(tmp_path, monkeypatch, "https://llm.meshpool.ai/v1", "openai")
    prov = json.loads((pi_rpc.prepare(tmp_path) / "models.json").read_text())["providers"]["meshpool"]
    assert prov["api"] == "openai-completions" and prov["baseUrl"] == "https://llm.meshpool.ai/v1"


def test_hermes_api_mode_follows_the_key_endpoint(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    import yaml
    from meshpool_harness import harnesses as HR
    _key(tmp_path, monkeypatch, "https://api.anthropic.com/v1", "anthropic")
    HR.prepare_hermes("key:claude-sonnet-5-5")
    m = yaml.safe_load((HR.hermes_home() / "config.yaml").read_text())["model"]
    assert m["api_mode"] == "anthropic_messages" and m["base_url"] == "https://api.anthropic.com/v1"
    assert m["provider"] == "custom" and m["api_key"] == "sk-test"
    _key(tmp_path, monkeypatch, "https://llm.meshpool.ai/v1", "openai")
    HR.prepare_hermes("key:deepseek-v4.1-flash")
    m = yaml.safe_load((HR.hermes_home() / "config.yaml").read_text())["model"]
    assert "api_mode" not in m and m["base_url"] == "https://llm.meshpool.ai/v1"


def test_bundled_hermes_gets_the_anthropic_sdk(tmp_path):
    """hermes_requirements.py includes the anthropic extra, which api_mode anthropic_messages needs."""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "pyproject.toml").write_text('[project]\nname="h"\ndependencies=["openai==2.24.0"]\n'
                                             '[project.optional-dependencies]\nacp=["agent-client-protocol==0.9.0"]\n'
                                             'mcp=["mcp==2.0.0"]\nanthropic=["anthropic==0.87.0"]\nweb=["x"]\n')
    out = subprocess.run([sys.executable, str(root / "packaging" / "hermes_requirements.py"), str(tmp_path)],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == ["openai==2.24.0", "agent-client-protocol==0.9.0", "mcp==2.0.0", "anthropic==0.87.0"]
