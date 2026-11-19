"""The LibreOffice resolver: the packaged app uses only its own copy; developers opt in to the system one."""
import sys
from pathlib import Path

import pytest

from meshpool_harness import office_runtime as o


def _fake_exe(folder: Path, key: str) -> str:
    p = folder / o._EXE[sys.platform]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    return str(p)


@pytest.fixture(autouse=True)
def _private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "meshpool-home"))     # status files never land in the repo


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake install: bundled folder, data folder and a 'system' LibreOffice, all in tmp_path."""
    key = "win-x64"
    monkeypatch.setattr(sys, "platform", "win32")
    bundled = tmp_path / "app" / "vendor" / "libreoffice"
    monkeypatch.setattr(o, "bundled_dir", lambda: bundled)
    system = tmp_path / "system" / "soffice"
    system.parent.mkdir()
    system.write_text("x")
    monkeypatch.setattr(o, "_system_copy", lambda: str(system))
    for v in ("MH_SOFFICE", "MH_ALLOW_SYSTEM_SOFFICE"):
        monkeypatch.delenv(v, raising=False)
    return {"key": key, "bundled": bundled, "system": str(system), "tmp": tmp_path}


def test_packaged_ignores_system_copy_even_with_the_dev_flag(env, monkeypatch):
    monkeypatch.setattr(o, "is_packaged", lambda: True)
    monkeypatch.setenv("MH_ALLOW_SYSTEM_SOFFICE", "1")
    assert o.resolve() == (None, "")
    assert o.find_soffice() is None


def test_packaged_uses_the_bundled_copy(env, monkeypatch):
    monkeypatch.setattr(o, "is_packaged", lambda: True)
    exe = _fake_exe(env["bundled"], env["key"])
    assert o.resolve() == (exe, "bundled")




def test_mh_soffice_wins_over_everything(env, monkeypatch):
    monkeypatch.setattr(o, "is_packaged", lambda: True)
    _fake_exe(env["bundled"], env["key"])
    mine = env["tmp"] / "mine" / "soffice"
    mine.parent.mkdir()
    mine.write_text("x")
    monkeypatch.setenv("MH_SOFFICE", str(mine))
    assert o.resolve() == (str(mine), "MH_SOFFICE")


def test_mh_soffice_pointing_nowhere_is_ignored(env, monkeypatch):
    monkeypatch.setattr(o, "is_packaged", lambda: True)
    monkeypatch.setenv("MH_SOFFICE", str(env["tmp"] / "nope"))
    exe = _fake_exe(env["bundled"], env["key"])
    assert o.resolve()[0] == exe


def test_source_checkout_needs_the_opt_in_for_the_system_copy(env, monkeypatch):
    monkeypatch.setattr(o, "is_packaged", lambda: False)
    assert o.resolve() == (None, "")
    monkeypatch.setenv("MH_ALLOW_SYSTEM_SOFFICE", "1")
    path, source = o.resolve()
    assert path == env["system"] and "system" in source



def test_wrappers_follow_the_resolver(env, monkeypatch):
    from engines.forge.common import find_soffice as forge_find
    from meshpool_harness import config
    from meshpool_harness.agent import office_mcp_server as srv
    monkeypatch.setattr(o, "is_packaged", lambda: True)
    assert config.find_soffice() is None and forge_find() is None and srv._soffice() is None
    exe = _fake_exe(env["bundled"], env["key"])
    assert config.find_soffice() == exe == forge_find() == srv._soffice()


def test_doctor_row_says_which_copy(env, monkeypatch):
    from meshpool_harness import config
    monkeypatch.setattr(o, "is_packaged", lambda: True)
    _fake_exe(env["bundled"], env["key"])
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)      # sys.platform is faked: keep the rest off the OS
    row = next(r for r in config.system_check() if r["name"] == "LibreOffice")
    assert row["ok"] and "ships with Meshpool" in row["detail"]



def test_no_runtime_download_code_in_the_app():
    src = Path(o.__file__).read_text(encoding="utf-8")
    assert "urllib" not in src and "urlopen" not in src and "subprocess" not in src
