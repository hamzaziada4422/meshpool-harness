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


def _git(*a, cwd):
    import os
    import subprocess
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def test_git_dirs_outside_the_project_are_writable(tmp_path):
    """A project in a subfolder of a repository, and a worktree copy, keep their git data outside the project folder:
    with the sandbox on, every commit there failed (read-only .git)."""
    import shutil
    import pytest
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    _git("init", "-q", cwd=repo)
    (repo / "sub" / "a.txt").write_text("a")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "one", cwd=repo)
    w = SB._writable((repo / "sub").resolve())
    assert str((repo / ".git").resolve()) in w                     # subfolder project: the repository's .git
    wt = tmp_path / "copy"
    _git("worktree", "add", "-q", str(wt), cwd=repo)
    w = SB._writable(wt.resolve())
    assert str((repo / ".git").resolve()) in w                     # the shared git dir (objects, refs)
    assert any(x.startswith(str((repo / ".git" / "worktrees").resolve())) for x in w)   # the copy's own git dir
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not any(x.endswith(".git") for x in SB._writable(plain.resolve()))


def test_git_dirs_are_kept_per_folder_until_its_git_changes(tmp_path, monkeypatch):
    """Every sandboxed command ran git rev-parse to find these folders. The answer is kept while the nearest .git is
    the same, and asked again when a repository appears, or a worktree's .git file points somewhere else."""
    import shutil
    import subprocess
    import pytest
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    SB._GIT_DIRS.clear()
    proj = tmp_path / "proj"
    proj.mkdir()
    runs = []
    real = subprocess.run

    def counting(cmd, *a, **kw):
        if "--git-common-dir" in cmd:
            runs.append(cmd)
        return real(cmd, *a, **kw)
    monkeypatch.setattr(subprocess, "run", counting)
    assert SB._git_dirs(proj) == [] and SB._git_dirs(proj) == []
    assert len(runs) == 1                                          # not a repository: asked once
    _git("init", "-q", cwd=proj)
    first = SB._git_dirs(proj)
    assert str((proj / ".git").resolve()) in first and len(runs) == 2   # a new .git: asked again
    for _ in range(5):
        assert SB._git_dirs(proj) == first
    assert len(runs) == 2                                          # then kept
    other = tmp_path / "other"
    other.mkdir()
    _git("init", "-q", cwd=other)
    shutil.rmtree(proj / ".git")
    (proj / ".git").write_text(f"gitdir: {other / '.git'}\n")
    assert str((other / ".git").resolve()) in SB._git_dirs(proj) and len(runs) == 3   # the .git file changed
