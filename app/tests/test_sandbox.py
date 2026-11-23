from meshpool_harness.core import sandbox as SB


def test_off_when_switched_off(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_SANDBOX", "0")      # G-01: on by default now; 0 turns it off
    assert SB.wrap(["bash", "-c", "ls"], tmp_path) == ["bash", "-c", "ls"]


def test_linux_writes_only_in_the_project_and_temp(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_SANDBOX", "1")
    out = SB.wrap(["bash", "-c", "ls"], tmp_path, platform="linux", which=lambda n: "/usr/bin/" + n)
    assert out[0] == "bwrap" and out[-3:] == ["bash", "-c", "ls"]
    assert out[1:4] == ["--ro-bind", "/", "/"]                      # everything readable, nothing writable by default
    binds = [out[i + 1] for i, x in enumerate(out) if x == "--bind"]
    assert str(tmp_path.resolve()) in binds and "/tmp" in binds and "/etc" not in binds and "/" not in binds


def test_mac_profile_denies_writes_outside_the_project(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_SANDBOX", "1")
    out = SB.wrap(["bash", "-c", "ls"], tmp_path, platform="darwin", which=lambda n: "/usr/bin/" + n)
    assert out[:2] == ["sandbox-exec", "-p"] and "(deny file-write*)" in out[2] and str(tmp_path.resolve()) in out[2]


def test_missing_tool_runs_unwrapped(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_SANDBOX", "1")
    assert SB.wrap(["x"], tmp_path, platform="linux", which=lambda n: None) == ["x"]
    assert SB.wrap(["x"], tmp_path, platform="win32", which=lambda n: None) == ["x"]


def test_a_real_command_cannot_write_outside_the_project(monkeypatch, tmp_path):
    import shutil
    import sys
    import pytest
    if not sys.platform.startswith("linux") or not shutil.which("bwrap"):
        pytest.skip("bubblewrap is not installed")
    from meshpool_harness.core import tools as T
    monkeypatch.setenv("MH_SANDBOX", "1")
    proj = tmp_path / "proj"
    proj.mkdir()
    outside = tmp_path.parent / "sbx_outside_probe.txt"
    ws = T.Workspace(str(proj), str(tmp_path / "out"))
    ws.bash("echo hi > inside.txt")
    assert (proj / "inside.txt").exists()
    out = ws.bash(f"echo no > {outside}; echo rc=$?")
    assert not outside.exists() or "/tmp" in str(outside)          # pytest's tmp is under /tmp, which stays writable
    out = ws.bash("echo no > /etc/sbx_probe.txt; echo rc=$?")
    assert "Read-only file system" in out and not __import__("os").path.exists("/etc/sbx_probe.txt")
