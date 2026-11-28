"""Independent review of 0.0.20, packaging: each test failed on the release candidate (942007e5) and passes
with its fix. Windows files are checked by reading them; NSIS and Windows cannot run here."""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


# ---------- packaging -----------------------------------------------------------------------------------------------------
def test_the_windows_installer_installs_updates_and_removes_the_bundled_uv():
    """uv/ (0.0.20) was unpacked into the staging folder but never swapped in, so the installed app had no uv, and the
    uninstaller left it behind. Every top-level folder win_app makes must be swapped, kept for rollback, dropped and
    removed, and its programs stopped before an update (read only: NSIS cannot run here)."""
    nsi = (ROOT / "packaging/nsis/installer.nsi").read_text()
    uni = (ROOT / "packaging/nsis/uninstaller.nsi").read_text()
    ps = (ROOT / "packaging/nsis/winsetup.ps1").read_text()
    build = (ROOT / "packaging/build_all.py").read_text()
    win_app = build.split("def win_app", 1)[1].split("\ndef ", 1)[0]
    assert 'uv_tool(root / "uv", "win")' in win_app
    for folder in ("app", "runtime", "git", "node", "harness-docs", "bin", "uv"):
        for macro in ("MP_SWAP", "MP_DROP_OLD", "MP_ROLLBACK"):
            assert f"!insertmacro {macro} {folder}\n" in nsi, (macro, folder)
        for text in (nsi, uni):
            assert f'RMDir /r "$INSTDIR\\{folder}"' in text and f'RMDir /r "$INSTDIR\\{folder}.old"' in text, folder
    assert '"uv\\"' in ps


def _load_build_all():
    spec = importlib.util.spec_from_file_location("mp_build_all_review", ROOT / "packaging" / "build_all.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def test_the_windows_build_gives_mingit_a_sleep(tmp_path, monkeypatch):
    """MinGit has no sleep.exe, so `sleep 2` failed in the Windows app's bash. The build writes a small sleep script
    beside bash.exe that waits with Windows PowerShell. Checked here with sh and a stand-in powershell.exe (Windows
    itself cannot run here)."""
    ba = _load_build_all()
    deps = tmp_path / "deps"
    ub = deps / "git-win" / "usr" / "bin"
    ub.mkdir(parents=True)
    (ub / "sh.exe").write_bytes(b"MZbash")
    monkeypatch.setattr(ba, "DEPS", deps)
    root = tmp_path / "Meshpool"
    root.mkdir()
    ba.git_win(root)
    shim = root / "git" / "usr" / "bin" / "sleep"
    data = shim.read_bytes()
    assert data.startswith(b"#!/bin/sh\n") and b"\r" not in data
    assert subprocess.run(["sh", "-n", str(shim)]).returncode == 0
    win = tmp_path / "Windows"
    ps = win / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    ps.parent.mkdir(parents=True)
    ps.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
    ps.chmod(0o755)
    env = dict(os.environ, SYSTEMROOT=str(win))

    def run(*args):
        return subprocess.run(["sh", str(shim), *args], capture_output=True, text=True, env=env)
    r = run("1.5", "2m", ".5", "3.")
    assert r.returncode == 0, r.stderr
    cmd = r.stdout.strip().splitlines()[-1]
    expr = re.search(r"1000 \* \((.*)\)\)\)\)$", cmd).group(1)
    assert re.fullmatch(r"[0-9.+*]+", expr) and eval(expr) == 1.5 + 120 + 0.5 + 3          # noqa: S307 - digits only
    for bad in ("abc", "1x", "1..2", "", "$(id)", "1;id"):
        r = run(bad)
        assert r.returncode == 1 and "invalid time interval" in r.stderr and not r.stdout, bad
    assert run().returncode == 1
    (ub / "sleep.exe").write_bytes(b"MZ")                      # a MinGit that has one keeps its own
    root2 = tmp_path / "Meshpool2"
    root2.mkdir()
    ba.git_win(root2)
    assert not (root2 / "git" / "usr" / "bin" / "sleep").exists()
