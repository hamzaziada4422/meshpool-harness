"""The pages new in the regrouped Settings: profile, capability switches, key spend (never the key), data export."""
import io
import json
import zipfile

import pytest

from meshpool_harness import assistant as AS
from meshpool_harness import config as C
from meshpool_harness.agent import mcp as M
from meshpool_harness.ui import settings_v2 as SV


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    C.ensure_home()
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"meshpool-web": {"command": "x"}, "browser": {"command": "y", "disabled": True}}}))
    return tmp_path


def test_profile_goes_into_the_first_message(home):
    assert SV.profile()["name"] == ""
    SV.save_profile(name="Sam  Rivera", address="Sam", language="Spanish")
    note = SV.profile_note()
    assert "Sam Rivera" in note and "address them as Sam" in note and "Spanish" in note
    assert "address them as Sam" in AS.context_block("hi")
    SV.save_profile(name="", address="", language="")
    assert SV.profile_note() == ""


def test_capabilities_are_real_switches(home):
    caps = {c["id"]: c for c in SV.capabilities()}
    assert caps["memory"]["on"] and caps["web"]["on"] and not caps["browser"]["on"]
    SV.set_capability("memory", False)
    assert AS.settings()["memory_on"] is False
    SV.set_capability("web", False)
    assert M.load_config()["meshpool-web"]["disabled"] is True
    SV.set_capability("browser", True)
    assert "disabled" not in M.load_config()["browser"]
    SV.set_capability("plan_first", True)
    assert SV.profile()["plan_first"] is True
    with pytest.raises(ValueError):
        SV.set_capability("nope", True)
    with pytest.raises(ValueError):
        SV.set_capability("computer", True)              # not set up in this config: said plainly, not faked


def test_spend_never_returns_the_key(home, monkeypatch):
    class R:
        headers = {"x-litellm-key-spend": "3.5", "x-litellm-key-max-budget": "20"}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-secret-secret-secret")
    monkeypatch.setattr("meshpool_harness.core.http.open", lambda *a, **k: R())
    SV._SPEND.update(t=0.0, v=None)
    r = SV.key_spend(force=True)
    assert r == {"available": True, "spent": 3.5, "budget": 20.0} and "secret" not in json.dumps(r)
    monkeypatch.setattr("meshpool_harness.core.http.open", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert SV.key_spend(force=True)["available"] is False


def test_export_has_chats_memory_and_projects(home):
    from meshpool_harness import project_store as PS
    AS.remember("The user likes tea")
    pid = PS.create("P", instructions="x")["id"]
    z = zipfile.ZipFile(io.BytesIO(SV.export_zip()))
    names = z.namelist()
    assert "memory/memory.md" in names and f"projects/{pid}/project.json" in names
    assert SV.data_info()["folder"] == str(C.home())
