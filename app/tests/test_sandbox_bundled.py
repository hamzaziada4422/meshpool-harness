"""Linux without the distribution's bubblewrap (first-hour run, 0.0.18): in Ask mode every command asked, and the only
word on why was "[Meshpool] The command sandbox needs bubblewrap (install the bwrap package)" in the server log.

The Linux app now carries its own bubblewrap (static, built by packaging/linux/build_bwrap.sh into vendor/bwrap), used
when the system has none that starts. bubblewrap needs unprivileged user namespaces; where the kernel (or AppArmor)
refuses them, neither starts, and commands run without the sandbox exactly as on Windows: one question for ordinary
work in the folder, then only risky steps, with one plain notice in the chat that names the distribution's command."""
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from meshpool_harness.agent import core
from meshpool_harness.agent.core import Agent
from meshpool_harness.core import sandbox as SB

ROOT = Path(__file__).resolve().parents[2]
_WHICH = shutil.which
UBUNTU = 'NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\n'


def _fake_bwrap(path: Path, works: bool) -> str:
    """A stand-in bubblewrap: one that starts, or one the kernel refuses (what bwrap prints then)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + ("exit 0\n" if works else
                                      "echo 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n"))
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def linux(monkeypatch, tmp_path):
    """sandbox.py's view of a Linux computer: `system` is the bwrap on PATH (None: none), `own` the app's copy."""
    if os.name == "nt":
        pytest.skip("starts shell scripts as stand-in bubblewrap programs")
    SB._STARTS.clear()
    SB._SAID.clear()

    def setup(system=None, own=None):
        monkeypatch.setattr(SB.shutil, "which", lambda n: system if n == "bwrap" else _WHICH(n))
        monkeypatch.setattr(SB, "_bundled_bwrap", lambda: own)
    yield setup
    SB._STARTS.clear()


def test_the_apps_own_bubblewrap_is_used_when_the_system_has_none(linux, tmp_path):
    own = _fake_bwrap(tmp_path / "app" / "vendor" / "bwrap" / "bwrap", works=True)
    linux(system=None, own=own)
    assert SB.bwrap_program() == own and SB.available("linux")
    argv = SB.wrap(["bash", "-c", "ls"], tmp_path, platform="linux", mode="ask")
    assert argv[0] == own and argv[-3:] == ["bash", "-c", "ls"]


def test_the_systems_bubblewrap_comes_first_and_a_refused_one_is_skipped(linux, tmp_path):
    own = _fake_bwrap(tmp_path / "own" / "bwrap", works=True)
    good = _fake_bwrap(tmp_path / "usr" / "bin" / "bwrap", works=True)
    linux(system=good, own=own)
    assert SB.bwrap_program() == "bwrap"                     # by name: the system's (its AppArmor profile applies)
    SB._STARTS.clear()
    refused = _fake_bwrap(tmp_path / "usr2" / "bin" / "bwrap", works=False)
    linux(system=refused, own=own)
    assert SB.bwrap_program() == own
    assert "Permission denied" in SB.bwrap_starts(refused)[1]


def test_when_the_kernel_refuses_commands_run_without_it_and_the_notice_is_plain(linux, tmp_path, capsys):
    own = _fake_bwrap(tmp_path / "own" / "bwrap", works=False)
    linux(system=None, own=own)
    assert SB.bwrap_program() == "" and not SB.available("linux")
    assert SB.wrap(["bash", "-c", "ls"], tmp_path, platform="linux", mode="ask") == ["bash", "-c", "ls"]
    log = capsys.readouterr().err
    assert "Meshpool" not in log and "[meshpool]" in log and "install the bwrap package" not in log
    note = SB.unavailable_notice("linux", os_release=UBUNTU)
    assert "cannot start" in note and "user namespaces" in note and "Meshpool" not in note
    assert note.endswith("To turn the sandbox on, run: sudo apt install bubblewrap")    # Ubuntu: the package's profile
    assert "then only before risky steps" in note


def test_the_notice_names_the_command_for_each_distribution(linux):
    linux(system=None, own=None)
    for rel, cmd in ((UBUNTU, "sudo apt install bubblewrap"), ('ID=fedora\n', "sudo dnf install bubblewrap"),
                     ('ID="rocky"\nID_LIKE="rhel centos fedora"\n', "sudo dnf install bubblewrap"),
                     ('ID=arch\n', "sudo pacman -S bubblewrap"), ('ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n',
                                                                  "sudo zypper install bubblewrap"),
                     ('ID=alpine\n', "sudo apk add bubblewrap")):
        note = SB.unavailable_notice("linux", os_release=rel)
        assert note.startswith("The command sandbox needs bubblewrap") and note.endswith("run: " + cmd), (rel, note)
    assert "run:" not in SB.unavailable_notice("linux", os_release="ID=plan9\n")
    for plat in ("win32", "darwin", "linux"):
        assert not re.search(r"\bMeshpool\b", SB.unavailable_notice(plat, os_release=UBUNTU))


def _agent(root, monkeypatch, plat, answer=("always", "")):
    """An Ask-mode chat on a computer without a sandbox that can start: Windows, or Linux where bubblewrap cannot.
    The question flow has no platform branch; only whether the sandbox is available matters."""
    monkeypatch.setattr(core, "SESSIONS", root.parent / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MH_SANDBOX_AUTO", "1")
    monkeypatch.delenv("MH_SANDBOX", raising=False)
    monkeypatch.setattr(SB, "available", lambda *a, **k: plat == "darwin")   # Windows, and Linux without one
    monkeypatch.setattr(SB, "install_command", lambda os_release=None: "sudo apt install bubblewrap")
    monkeypatch.setattr(SB.shutil, "which", lambda n: None if n == "bwrap" else _WHICH(n))   # no system bwrap,
    monkeypatch.setattr(SB, "_bundled_bwrap", lambda: "/opt/Meshpool/app/vendor/bwrap/bwrap")      # the app's refused
    asked, said = [], []

    def ap(kind, title, detail, scope=""):
        asked.append(scope)
        return answer
    root.mkdir(parents=True, exist_ok=True)
    a = Agent(str(root), mode="ask", approve=ap)
    a.emit = lambda ev: said.append(ev)
    return a, asked, said


ORDINARY = ["python3 analyze.py data.csv > summary.txt", "ls -la && head -5 notes.md | wc -l", "npm run build"]
RISKY = ["rm -rf src", "curl https://example.com/x", "pip install pandas"]


def test_linux_without_a_sandbox_asks_as_windows_does_and_says_why_once_in_the_chat(tmp_path, monkeypatch):
    seen = {}
    for plat in ("win32", "linux"):
        core._NOTICED.clear()
        a, asked, said = _agent(tmp_path / plat / "proj", monkeypatch, plat)
        got = [a._permit("bash", {"command": c})[0] for c in ORDINARY + RISKY]
        seen[plat] = (got, list(asked))
        if plat == "linux":
            notes = [e["text"] for e in said if e.get("type") == "status"]
            assert len(notes) == 1 and "sudo apt install bubblewrap" in notes[0] and "Meshpool" not in notes[0], said
    assert seen["linux"] == seen["win32"]
    got, asked = seen["linux"]
    # one question for ordinary work, then every risky step asks
    assert asked[0].startswith("ordinary commands and edits in this folder") and len(asked) == 1 + len(RISKY)
    a2, asked2, said2 = _agent(tmp_path / "again" / "proj", monkeypatch, "linux")
    a2._permit("bash", {"command": "ls -la && wc -l notes.md"})
    assert not [e for e in said2 if e.get("type") == "status"]           # once per app run, not per chat


def test_the_one_yes_for_ordinary_work_outlives_the_chats_agent(tmp_path, monkeypatch):
    """The yes is kept in the chat's saved allow set (meta.json "always"), so a chat that was reaped, restarted or
    handed back does not ask again for every ordinary command."""
    a, asked, _ = _agent(tmp_path / "proj", monkeypatch, "linux")
    assert a._permit("bash", {"command": ORDINARY[0]})[0] and len(asked) == 1
    kept = set(a.always)
    b, asked_b, _ = _agent(tmp_path / "proj", monkeypatch, "linux", answer=("no", ""))
    b.always = set(kept)                                     # what agent_api._bind_always seeds a new agent with
    assert all(b._permit("bash", {"command": c})[0] for c in ORDINARY) and asked_b == []
    assert not b._permit("bash", {"command": "rm -rf src"})[0]       # a risky step still asks


# ------------------------------------------------------------------ packaging: the Linux app carries it
def test_the_linux_bundle_builds_and_carries_bubblewrap():
    bt = (ROOT / "packaging" / "bundled_tools.py").read_text(encoding="utf-8")
    assert re.search(r'BWRAP_SOURCE_SHA256 = "[0-9a-f]{64}"', bt) and re.search(r'LIBCAP_SOURCE_SHA256 = "[0-9a-f]{64}"', bt)
    script = (ROOT / "packaging" / "linux" / "build_bwrap.sh").read_text(encoding="utf-8")
    assert "-static" in script and "sha256sum -c" in script and "COPYING" in script and "License" in script
    assert os.access(ROOT / "packaging" / "linux" / "build_bwrap.sh", os.X_OK) or os.name == "nt"
    ba = (ROOT / "packaging" / "build_all.py").read_text(encoding="utf-8")
    linux_app = ba.split("def linux_app():", 1)[1].split("\ndef ", 1)[0]
    assert "bwrap_linux(root / \"app\")" in linux_app and 'app_dir / "vendor" / "bwrap"' in ba
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    row = next(ln for ln in notices.splitlines() if ln.startswith("| bubblewrap"))
    assert "LGPL-2.0-or-later" in row and "bubblewrap-0.11.0.tar.xz" in row and "libcap-2.76.tar.xz" in row
    health = (ROOT / "packaging" / "tool_health.py").read_text(encoding="utf-8")
    assert 'h.check("command sandbox (bubblewrap)"' in health and "skill_programs, sandbox)" in health


def test_the_release_health_check_passes_when_the_sandbox_confines_the_command(tmp_path, monkeypatch):
    """The check's command ended with `touch /usr/... && echo escaped`: confined, that touch fails, so the command
    exited 1 and the check failed exactly when the sandbox worked (a release built where namespaces are allowed would
    have stopped). The command exits 0 when confined now."""
    if os.name == "nt" or not sys.platform.startswith("linux"):
        pytest.skip("the sandbox check runs on Linux only")
    import importlib.util
    spec = importlib.util.spec_from_file_location("tool_health_t", ROOT / "packaging" / "tool_health.py")
    TH = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(TH)
    stub = tmp_path / "stubs"
    stub.mkdir()
    (stub / "touch").write_text("#!/bin/sh\necho \"touch: cannot touch '$1': Read-only file system\" >&2\nexit 1\n")
    (stub / "touch").chmod(0o755)
    fake = tmp_path / "bwrap"                     # confines the way bubblewrap's read-only root does: writes outside fail
    fake.write_text(f"#!/bin/sh\nwhile [ \"$1\" != -- ]; do shift; done; shift\nPATH='{stub}':\"$PATH\" exec \"$@\"\n")
    fake.chmod(0o755)
    monkeypatch.setattr(SB, "bwrap_program", lambda: str(fake))
    monkeypatch.setattr(SB, "wrap", lambda argv, root, mode="ask", **k: [str(fake), "--", *argv])
    h = TH.Health(tmp_path / "app", [])
    TH.sandbox(h)
    tool, status, detail = TH.RESULTS[-1]
    assert tool == "command sandbox (bubblewrap)" and status == "ok", (status, detail)


@pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("gcc") or not shutil.which("make")
                    or os.environ.get("MH_BUILD_BWRAP") != "1",
                    reason="builds bubblewrap from source (network, gcc): set MH_BUILD_BWRAP=1")
def test_build_bwrap_makes_a_static_program_that_sandboxes(tmp_path):
    import subprocess
    out = tmp_path / "bwrap"
    subprocess.run(["bash", str(ROOT / "packaging" / "linux" / "build_bwrap.sh"), str(out)], check=True, timeout=900)
    bw = str(out / "bwrap")
    assert subprocess.run(["readelf", "-d", bw], capture_output=True, text=True).stdout.count("NEEDED") == 0
    ok, why = SB.bwrap_starts(bw)
    if not ok:
        pytest.skip("this computer refuses user namespaces: " + why)
