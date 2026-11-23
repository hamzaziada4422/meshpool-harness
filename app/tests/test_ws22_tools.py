"""ws22: shell and file tools, sandbox, secrets (S-06, G-03, G-01, G-02, OSS-17, H-01, H-08, H-11, H-22, H-27, H-28,
G-15, OSS-14, OSS-15, OSS-18, OSS-28, OSS-29) and the ACP items OSS-02 / OSS-36."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import fsio, winproc  # noqa: E402
from meshpool_harness.core import autocheck as AC  # noqa: E402
from meshpool_harness.core import sandbox as SB  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402

PY = sys.executable


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_SANDBOX", "0")
    C.ensure_home()
    proj = tmp_path / "proj"
    proj.mkdir()
    return T.Workspace(proj, tmp_path / ".run")


# ---- H-08, H-27 (already fixed on the base: proving tests) -------------------------------------------------------

def test_h08_both_pipes_drained_and_output_capped(ws):
    code = "import sys\nfor _ in range(60):\n sys.stdout.write('o'*100000); sys.stderr.write('e'*100000)"
    t0 = time.time()
    r = ws._proc([PY, "-c", code], dict(os.environ), 30)
    assert time.time() - t0 < 20 and r.returncode == 0
    assert 0 < len(r.stdout) < 3 * T.OUTPUT_TAIL + T.OUTPUT_HEAD + 200 and "dropped" in r.stdout and r.stderr.startswith("e")


def test_h27_crlf_and_bom_survive_a_round_trip(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    text, eol, bom = T.read_src(p)
    assert (text, eol, bom) == ("one\ntwo\n", "\r\n", True)
    T.write_src(p, "one\nTWO\n", eol, bom)
    assert p.read_bytes() == b"\xef\xbb\xbfone\r\nTWO\r\n"


# ---- S-06 / G-03 -------------------------------------------------------------------------------------------------

def test_s06_secret_named_variables_are_not_in_the_agents_shell(ws, monkeypatch):
    for k in ("OPENAI_API_KEY", "MY_APP_TOKEN", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY", "STRIPE_AUTH"):
        monkeypatch.setenv(k, "v-" + k.lower())
    monkeypatch.setenv("KEEP_ME_TOKEN", "allowed-value")
    monkeypatch.setenv("MH_SHELL_ENV_ALLOW", "keep_me_token")
    out = ws.bash("env | grep -iE 'token|key|password|secret|auth|^PATH='")
    for k in ("OPENAI_API_KEY", "MY_APP_TOKEN", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY", "STRIPE_AUTH"):
        assert k not in out
    assert "KEEP_ME_TOKEN=" in out and "PATH=" in out


def test_s06_scrub_env_keeps_ordinary_names():
    e = C.scrub_env({"PATH": "/bin", "HOME": "/h", "SSH_AUTH_SOCK": "/s", "GH_TOKEN": "x", "KEYBOARD": "us"})
    assert set(e) == {"PATH", "HOME", "SSH_AUTH_SOCK", "KEYBOARD"}
    ok = {"GIT_CONFIG_KEY_0": "a", "GIT_AUTHOR_NAME": "n", "MAX_THINKING_TOKENS": "9"}   # git and sizes are not secrets
    assert C.scrub_env(dict(ok)) == ok


def test_settings_expose_the_new_keys():
    names = {f[0] for f in C.FIELDS}
    assert {"MH_SANDBOX", "MH_SANDBOX_NETWORK", "MH_SHELL_ENV_ALLOW", "MH_AUTO_CHECK", "MH_ACP_IDLE_S"} <= names


# ---- G-01 / G-02 / OSS-17 ----------------------------------------------------------------------------------------

def _which(n):
    return "/usr/bin/" + n


def test_g01_sandbox_is_on_by_default_except_in_full_access(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "h"))
    monkeypatch.delenv("MH_SANDBOX", raising=False)
    on = SB.wrap(["bash", "-c", "ls"], tmp_path, platform="linux", which=_which, mode="ask")
    assert on[0] == "bwrap"
    assert SB.wrap(["bash", "-c", "ls"], tmp_path, platform="linux", which=_which, mode="auto") == ["bash", "-c", "ls"]
    monkeypatch.setenv("MH_SANDBOX", "0")
    assert SB.wrap(["x"], tmp_path, platform="linux", which=_which, mode="ask") == ["x"]
    monkeypatch.setenv("MH_SANDBOX", "1")
    assert SB.wrap(["x"], tmp_path, platform="linux", which=_which, mode="auto")[0] == "bwrap"


def test_g02_network_off_option(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_SANDBOX", "1")
    monkeypatch.delenv("MH_SANDBOX_NETWORK", raising=False)
    assert "--unshare-net" not in SB.wrap(["x"], tmp_path, platform="linux", which=_which)
    monkeypatch.setenv("MH_SANDBOX_NETWORK", "0")
    assert "--unshare-net" in SB.wrap(["x"], tmp_path, platform="linux", which=_which)
    mac = SB.wrap(["x"], tmp_path, platform="darwin", which=_which)
    assert "(deny network*)" in mac[2]
    monkeypatch.delenv("MH_SANDBOX_NETWORK")
    assert "(deny network*)" not in SB.wrap(["x"], tmp_path, platform="darwin", which=_which)[2]


def test_g01_unavailable_says_so_and_windows_says_not_available(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MH_SANDBOX", "1")
    SB._SAID.clear()
    assert SB.wrap(["x"], tmp_path, platform="win32", which=lambda n: None) == ["x"]
    assert "not available on Windows" in capsys.readouterr().err
    assert "bubblewrap" in SB.unavailable_notice("linux")


def test_g01_cli_flags(monkeypatch):
    src = (Path(C.__file__).parent / "cli.py").read_text(encoding="utf-8")
    assert '"--sandbox"' in src and '"--no-network"' in src and "MH_SANDBOX_NETWORK" in src


@pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("bwrap"), reason="needs bubblewrap")
def test_g01_redirection_cannot_escape_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_SANDBOX", "1")
    proj = tmp_path / "proj"
    proj.mkdir()
    outside = Path.home() / "ws22_escape_probe.txt"
    w = T.Workspace(proj, tmp_path / ".run")
    w.bash(f"echo hi > {outside}; echo ok > inside.txt")
    assert not outside.exists() and (proj / "inside.txt").exists()


# ---- H-01 --------------------------------------------------------------------------------------------------------

def test_h01_a_background_child_does_not_hold_the_call(ws):
    t0 = time.time()
    out = ws.bash(f"{PY} -m http.server 0 &\necho started $!", 60)
    took = time.time() - t0
    pid = int(re.search(r"started (\d+)", out).group(1))
    try:
        assert took < 8, f"the call waited {took:.0f}s for the background server"
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def test_h01_plain_background_child_keeping_the_pipes(ws):
    t0 = time.time()
    out = ws.bash("sleep 30 &\necho hello", 60)
    assert time.time() - t0 < 8 and "hello" in out and "background process" in out
    subprocess.run(["pkill", "-f", "sleep 30"], capture_output=True)


# ---- H-11 --------------------------------------------------------------------------------------------------------

def test_h11_grep_and_glob_skip_heavy_folders_and_big_files(ws):
    r = ws.root
    for d in ("node_modules", ".venv", "build", "dist", "target", ".git", "src"):
        (r / d).mkdir()
        (r / d / "f.py").write_text("needle\n")
    (r / "src" / "big.txt").write_text("needle " + "x" * (T.MAX_GREP_FILE + 10))
    out = ws.grep("needle", ".")
    assert "src/f.py" in out.replace("\\", "/") and "node_modules" not in out and "dist" not in out and "big.txt" not in out
    g = ws.glob("**/*.py")
    assert "src" in g and "node_modules" not in g and ".venv" not in g and "target" not in g


def test_h11_stop_cuts_the_walk(ws):
    for i in range(30):
        (ws.root / f"d{i}").mkdir()
        (ws.root / f"d{i}" / "a.txt").write_text("needle")
    seen = []
    ws.should_stop = lambda: (seen.append(1), len(seen) > 3)[1]
    with pytest.raises(T.ToolError, match="stopped"):
        ws.grep("needle", ".")
    assert len(seen) < 20


# ---- H-22 --------------------------------------------------------------------------------------------------------

def test_h22_nul_redirect_rewritten_and_reserved_names_refused(ws):
    assert T.fix_nul_redirect("dir 2>nul") == "dir 2> /dev/null"
    assert T.fix_nul_redirect("a >nul && b >NUL") == "a > /dev/null && b > /dev/null"
    assert T.fix_nul_redirect("cat nul.txt; echo nullable >out") == "cat nul.txt; echo nullable >out"
    for bad in ("nul", "sub/CON.txt", "aux", "COM1.log"):
        with pytest.raises(T.ToolError, match="reserved"):
            ws.write_file(bad, "x")
    ws.write_file("null.txt", "ok")


# ---- H-28 --------------------------------------------------------------------------------------------------------

def test_h28_every_spawn_gets_no_window_on_windows(monkeypatch):
    seen = []

    class FakePopen:
        def __init__(self, *a, **kw):
            seen.append(kw.get("creationflags", 0))
    monkeypatch.setattr(os, "name", "nt")
    assert winproc.install(FakePopen) is True
    FakePopen(["x"])
    FakePopen(["x"], creationflags=0x200)
    FakePopen(["x"], creationflags=0x10)                 # asks for its own console: left alone
    assert seen[0] & 0x08000000 and seen[1] == 0x200 | 0x08000000 and seen[2] == 0x10
    monkeypatch.setattr(os, "name", "posix")
    assert winproc.install(type("P", (), {"__init__": lambda self: None})) is False


def test_h28_the_package_installs_the_shim_and_no_site_bypasses_popen():
    root = Path(C.__file__).parent
    assert "_winproc.install()" in (root / "__init__.py").read_text(encoding="utf-8")
    bad = []
    for f in root.rglob("*.py"):
        t = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"os\.(popen|spawn\w*)\(|create_subprocess_(exec|shell)|ctypes\.windll\.shell32\.ShellExecute", t) \
                and f.name not in ("desktop.py",):
            bad.append(f.name)
    assert bad == [], f"spawn sites that skip subprocess.Popen: {bad}"


# ---- G-15 --------------------------------------------------------------------------------------------------------

def test_g15_off_by_default_and_runs_detected_checks_once(tmp_path, monkeypatch):
    monkeypatch.delenv("MH_AUTO_CHECK", raising=False)
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "h"))
    assert AC.mode() == "off" and AC.run(tmp_path, [], None) == ""
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg.py").write_text("x = 1\n")
    (tmp_path / "tests" / "test_pkg.py").write_text("def test_a():\n    assert 0\n")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    files = [tmp_path / "pkg.py"]
    cmds = AC.detect(tmp_path, files, "test", which=lambda n: "/x/" + n)
    assert cmds and cmds[0][:3] == ["pytest", "-x", "-q"] and str(tmp_path / "tests" / "test_pkg.py") in cmds[0]
    assert AC.detect(tmp_path, files, "lint", which=lambda n: "/x/" + n)[0][:2] == ["ruff", "check"]

    class R:
        returncode, stdout, stderr = 1, "FAILED test_pkg.py::test_a", ""
    msg = AC.run(tmp_path, files, "test", runner=lambda *a, **k: R)
    assert "automatic check" in msg and "FAILED" in msg
    monkeypatch.setenv("MH_AUTO_CHECK", "both")
    assert AC.mode() == "both"


# ---- OSS-14 / OSS-15 ---------------------------------------------------------------------------------------------

def test_oss14_edit_fails_when_the_file_is_not_changed(ws, monkeypatch):
    ws.write_file("a.txt", "hello world\n")
    ws.read_file("a.txt")
    monkeypatch.setattr(T, "write_src", lambda *a, **k: None)           # a write that silently does nothing
    with pytest.raises(T.ToolError, match="does not hold the new text|failed while writing"):
        ws.edit("a.txt", "hello", "bye")
    assert (ws.root / "a.txt").read_text() == "hello world\n"


def test_oss15_write_is_atomic_and_keeps_the_mode(tmp_path, monkeypatch):
    p = tmp_path / "run.sh"
    p.write_text("#!/bin/sh\necho 1\n")
    os.chmod(p, 0o755)
    T.write_src(p, "#!/bin/sh\necho 2\n")
    assert p.read_text().endswith("echo 2\n") and (p.stat().st_mode & 0o777) == 0o755
    assert [x.name for x in tmp_path.iterdir()] == ["run.sh"]

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(fsio, "_replace", boom)
    with pytest.raises(OSError):
        T.write_src(p, "NEW")
    assert p.read_text().endswith("echo 2\n") and [x.name for x in tmp_path.iterdir()] == ["run.sh"]   # never truncated


# ---- OSS-18 ------------------------------------------------------------------------------------------------------

def test_oss18_destructive_git_commands(tmp_path):
    from meshpool_harness.agent.core import Agent, _destructive
    for c in ("git checkout .", "git restore .", "git worktree remove -f ../w", "git worktree remove --force w",
              "git stash drop", "git stash clear", "rm -rf .", "rm -rf /home/u/proj"):
        assert _destructive({"command": c}), c
    for c in ("git checkout main", "git restore a.py", "git worktree list", "git stash", "git status"):
        assert not _destructive({"command": c}), c
    a = Agent(str(tmp_path), mode="auto", approve=lambda k, t, d: ("no", ""))
    ok, why = a._permit("bash", {"command": "git checkout ."})        # Full access still asks
    assert ok is False
    assert a._permit("bash", {"command": "ls"})[0] is True


# ---- OSS-28 ------------------------------------------------------------------------------------------------------

def test_oss28_drive_letter_paths_are_normalised():
    assert T.fix_win_path("/C:\\Users\\me\\a.txt") == "C:\\Users\\me\\a.txt"
    assert T.fix_win_path("/C:/Users/me") == "C:/Users/me"
    assert T.fix_win_path("file:///D:/x/y.py") == "D:/x/y.py"
    assert T.fix_win_path("/usr/local/x") == "/usr/local/x" and T.fix_win_path("src/a.py") == "src/a.py"
    a = T.validate("read_file", json.dumps({"path": "/X:\\proj\\a.py"}), list(T.SCHEMAS))
    assert a["path"] == "X:\\proj\\a.py"


# ---- OSS-29 ------------------------------------------------------------------------------------------------------

def test_oss29_secrets_are_redacted_from_tool_output_and_session_files(ws, tmp_path):
    key = "sk-" + "a1B2" * 8
    gh = "ghp_" + "x" * 36
    out = ws.bash(f"echo {key}; echo GITHUB_TOKEN={gh}; echo OPENAI_API_KEY=abcdefgh12345; echo plain text")
    assert key not in out and gh not in out and "abcdefgh12345" not in out and "plain text" in out
    assert key not in (ws.run_dir / "commands.log").read_text()
    from meshpool_harness import session_store as SS
    assert key not in SS._dumps({"role": "tool", "content": f"seen {key} here"})
    assert C.redact_secrets("api_key_note = os.environ['X_KEY']") == "api_key_note = os.environ['X_KEY']"
