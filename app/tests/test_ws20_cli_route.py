"""X-02: a chat created without a task (terminal CLI, `meshpool -p`, ACP) is routed on its first message, so the
default CLI runs the shipped default profile ("codex"), not FULL. The model call is stubbed."""
import pytest

from meshpool_harness.agent import core as A



@pytest.fixture(autouse=True)
def _shipped(monkeypatch, tmp_path):
    from meshpool_harness import config as C
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", str(C.APP / "defaults" / "profiles.json"))   # opt back in (conftest: none)
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    for v in ("MH_HARNESS_PROFILE", "MH_PROFILE", "MH_AUTO_ROUTE", "MH_ENGINE_STYLE", "MH_REVIEW", "MH_MUTATION", "MH_PLAN"):
        monkeypatch.delenv(v, raising=False)

    def fake_chat(self, turn):
        turn["calls"] += 1
        return {"role": "assistant", "content": "done"}, False
    monkeypatch.setattr(A.Agent, "_chat", fake_chat)


def _agent(tmp_path, **kw):
    wd = tmp_path / "w"
    wd.mkdir(exist_ok=True)
    return A.Agent(str(wd), mode="auto", profile="code", sessions_dir=str(tmp_path / "s"), **kw)


def test_cli_default_routes_to_codex(tmp_path):
    a = _agent(tmp_path)
    assert a.route is None
    a.send("fix the failing test in calc.py")
    assert a.route is not None
    s = a.hsettings()
    assert s["engine_style"] == "codex" and s["review"] is False
    assert a.messages[0]["content"] == a.system_prompt()          # routed prompt, set before the first user message
    first = a.messages[0]["content"]
    a.send("and the second one too")
    assert a.messages[0]["content"] == first                      # byte-stable: the prompt cache keeps hitting


def test_route_survives_save_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "s")
    a = _agent(tmp_path)
    a.send("fix the failing test in calc.py")
    b = A.Agent.resume(str(a.root), session_id=a.id, sessions_dir=str(tmp_path / "s"), mode="auto")
    assert b is not None and b.route is not None and b.hsettings()["engine_style"] == "codex"


def test_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_AUTO_ROUTE", "0")
    a = _agent(tmp_path)
    a.send("fix the failing test in calc.py")
    assert a.route is None and a.hsettings()["engine_style"] == "meshpool"


def test_plan_mode_not_routed(tmp_path):
    a = _agent(tmp_path)
    a.plan = True
    a.send("plan a refactor of calc.py")
    assert a.route is None


def test_prior_history_not_routed(tmp_path):
    a = _agent(tmp_path)
    a.messages.append({"role": "user", "content": "earlier"})
    a.messages.append({"role": "assistant", "content": "ok"})
    a.send("now fix calc.py")
    assert a.route is None


def test_empty_text_not_routed(tmp_path):
    a = _agent(tmp_path)
    a._auto_route("   ")
    assert a.route is None


def test_forced_profile_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_HARNESS_PROFILE", "full")
    a = _agent(tmp_path)
    a.send("fix the failing test in calc.py")
    assert a.hsettings()["engine_style"] == "meshpool"


def test_delegate_and_grader_not_routed(tmp_path):
    d = _agent(tmp_path)
    d._delegate_depth = 1
    d.send("look into calc.py")
    assert d.route is None

    class G(A.Agent):
        _is_grader = True
    wd = tmp_path / "w"
    g = G(str(wd), mode="auto", profile="code", sessions_dir=str(tmp_path / "s2"))
    g.send("review calc.py")
    assert g.route is None
