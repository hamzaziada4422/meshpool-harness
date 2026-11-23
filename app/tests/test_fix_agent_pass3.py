import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import mcp as M  # noqa: E402
from meshpool_harness.agent import tui as T  # noqa: E402


@pytest.mark.parametrize("tok,bad", [("xxx", True), ("your-token", True), ("<token>", True), ("", True),
                                     ("ghp_abc\n", True), ("ghp a b", True), ("proxy-injected", True),
                                     ("ghp_" + "a" * 36, False), ("github_pat_11AAAA_bbbb", False)])
def test_token_problem(tok, bad):
    spec = {"url": "https://x", "headers": {"Authorization": f"Bearer {tok}"}}
    assert bool(M.token_problem(spec)) == bad


def test_invalid_github_token_server_is_not_started(monkeypatch, tmp_path):
    from meshpool_harness import mcp_apps as CN
    monkeypatch.setattr(CN, "servers", lambda: {"github": {"url": "https://x", "headers": {"Authorization": "Bearer xxx"}}})
    cfg = M.active_config(tmp_path / "none.json")
    assert cfg["github"]["disabled"] and "meshpool connect github" in cfg["github"]["description"]


def test_login_without_terminal_says_it_needs_one(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MESHPOOL_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(T.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(T.C, "load_into_env", lambda: None)
    assert T.ask_key(force=True) is False
    err = capsys.readouterr().err
    assert "interactive terminal" in err and "MESHPOOL_API_KEY" in err
