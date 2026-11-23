"""D-01: every engine uses the user's configured model/provider unless a model is passed explicitly."""
import importlib

import pytest

from meshpool_harness import cli
from meshpool_harness import providers as PV
from meshpool_harness.core.client import MockClient
from meshpool_harness.router import router


def mock():
    return MockClient([{"role": "assistant", "content": "x"} for _ in range(3)])


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.delenv("MH_MODEL", raising=False)
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "none.config"))


# --- the resolver ---------------------------------------------------------------------------------------------

def test_configured_model_explicit_wins(monkeypatch):
    monkeypatch.setenv("MH_MODEL", "env-model")
    assert PV.configured_model("  chatgpt:gpt-x ") == "chatgpt:gpt-x"


def test_configured_model_env(monkeypatch):
    monkeypatch.setenv("MH_MODEL", "env-model")
    assert PV.configured_model() == "env-model"


def test_configured_model_settings_file(monkeypatch, tmp_path):
    cfg = tmp_path / "meshpool.config"
    cfg.write_text("MH_MODEL=cfg-model\n")
    monkeypatch.setenv("MESHPOOL_CONFIG", str(cfg))
    assert PV.configured_model() == "cfg-model"


def test_configured_model_default():
    assert PV.configured_model() == PV.DEFAULT_MODEL


# --- engines ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["general", "data", "research"])
def test_engine_uses_env_model(name, monkeypatch, tmp_path):
    monkeypatch.setenv("MH_MODEL", "user-model")
    c = mock()
    eng = importlib.import_module(f"engines.{name}.engine")
    try:
        eng.solve("say hi", workdir=str(tmp_path), task_id="t", client=c)
    except Exception:
        pass
    assert c.requests, name
    assert all(r["model"] == "user-model" for r in c.requests)


@pytest.mark.parametrize("name", ["general", "data", "research"])
def test_engine_explicit_model_wins(name, monkeypatch, tmp_path):
    monkeypatch.setenv("MH_MODEL", "user-model")
    c = mock()
    eng = importlib.import_module(f"engines.{name}.engine")
    try:
        eng.solve("say hi", workdir=str(tmp_path), task_id="t", client=c, model="other-model")
    except Exception:
        pass
    assert c.requests and all(r["model"] == "other-model" for r in c.requests)


class _Stop(Exception):
    pass


@pytest.mark.parametrize("name", ["code", "browser"])
def test_loop_engines_pass_model_to_run(name, monkeypatch, tmp_path):
    monkeypatch.setenv("MH_MODEL", "user-model")
    eng = importlib.import_module(f"engines.{name}.engine")
    seen = {}

    def fake_run(*a, **kw):
        seen["model"] = kw.get("model")
        raise _Stop

    monkeypatch.setattr(eng, "run", fake_run)
    if name == "browser":
        from engines.browser import ctl
        monkeypatch.setattr(ctl, "main", lambda argv: 0)
        monkeypatch.setattr(ctl, "call", lambda *a, **k: {})
    with pytest.raises(_Stop):
        eng.solve("do it", workdir=str(tmp_path), task_id="t", client=mock())
    assert seen["model"] == "user-model"


def test_credit_uses_env_model(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_MODEL", "user-model")
    from engines.credit import engine as E
    seen = []
    monkeypatch.setattr(E, "_llm_json", lambda client, model, *a, **k: seen.append(model) or {})
    try:
        E.solve("assess this borrower", workdir=str(tmp_path), task_id="t", client=mock())
    except Exception:
        pass
    assert seen and set(seen) == {"user-model"}


def test_research_grid_default(monkeypatch):
    monkeypatch.setenv("MH_MODEL", "user-model")
    from engines.research import grid
    import inspect
    assert "deepseek-v4.1-flash" not in inspect.getsource(grid)


def test_forge_build_model_follows_setting(monkeypatch):
    monkeypatch.delenv("FORGE_MODEL", raising=False)
    monkeypatch.setenv("MH_MODEL", "user-model")
    from engines.forge import build
    importlib.reload(build)
    assert build.MODEL == "user-model"


# --- cli --------------------------------------------------------------------------------------------------------

def test_run_request_passes_model(monkeypatch, tmp_path):
    seen = {}

    class Stub:
        @staticmethod
        def solve(task, **kw):
            seen.update(kw)
            return {"ok": True, "answer": "a", "artifacts": []}

    monkeypatch.setattr(cli, "_engine", lambda name: Stub)
    monkeypatch.setenv("MESHPOOL_API_KEY", "dummy")
    cli.run_request("hello", [], str(tmp_path), engine="general", client=mock(), model="chatgpt:gpt-x")
    assert seen["model"] == "chatgpt:gpt-x"


def test_run_subparser_has_model_flag(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "run_request", lambda *a, **kw: seen.update(kw) or {"ok": True})
    cli.main(["run", "hello", "--engine", "general", "--model", "m9", "--out", str(tmp_path)])
    assert seen["model"] == "m9"


# --- the app's office jobs --------------------------------------------------------------------------------------

def test_new_job_passes_model(tmp_path):
    from meshpool_harness.agent import office_jobs as OJ
    jid = OJ.new_job("sheets", "make a budget", [], tmp_path, model="m1")
    cmd = OJ.JOBS[jid]["cmd"]
    assert cmd[cmd.index("--model") + 1] == "m1"
    jid2 = OJ.new_job("sheets", "make a budget", [], tmp_path)
    assert "--model" not in OJ.JOBS[jid2]["cmd"]


def test_agent_office_passes_current_model(tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    from meshpool_harness.agent import office_jobs as OJ
    a = core.Agent(str(tmp_path), approve=lambda *x: ("yes", ""), mode="auto", sessions_dir=str(tmp_path / "s"))
    a.model = "chatgpt:gpt-picked"
    seen = {}
    monkeypatch.setattr(OJ, "check_request", lambda e, r: None)
    monkeypatch.setattr(OJ, "new_job", lambda *args, **kw: seen.update(kw) or "j1")
    monkeypatch.setattr(OJ, "run", lambda *a, **k: None)
    monkeypatch.setattr(OJ, "JOBS", {"j1": {}})
    monkeypatch.setattr(OJ, "report", lambda *a, **k: ("done", False))
    a.emit = lambda e: None
    a._office({"engine": "sheets", "request": "make a budget"})
    assert seen.get("model") == "chatgpt:gpt-picked"


# --- router -----------------------------------------------------------------------------------------------------

def test_router_resolves_model_at_call_time(monkeypatch):
    monkeypatch.setenv("MH_MODEL", "late-model")
    c = MockClient([{"role": "assistant", "content": '{"engines":["general"],"confidence":0.9}'}])
    router.route("summarize this", client=c)
    assert c.requests and c.requests[0]["model"] == "late-model"
