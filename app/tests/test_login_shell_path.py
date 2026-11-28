"""The agent's login shell keeps the app's own Python first on PATH (release 0.0.20, the Mac tool health check).

exec_command runs `bash -lc`. On macOS the login profile runs path_helper, which puts /usr/bin ahead of the PATH it
was given, so `python3` ran the system's Python, without openpyxl or python-pptx, and a chat could not make a
workbook or a deck. The app's own folders go back in front after the profile."""
import os
import shutil
import sys
from pathlib import Path

import pytest

from meshpool_harness import config as C
from meshpool_harness.core import tools as T

posix = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="a POSIX login shell")


def _login_profile_resets_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text("export PATH=/usr/bin:/bin\n")   # what path_helper does to the order
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]))


@posix
def test_python3_in_the_packaged_apps_login_shell_is_the_apps_python(tmp_path, monkeypatch):
    _login_profile_resets_path(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "packaged", lambda: True)
    proj = tmp_path / "proj"
    proj.mkdir()
    out = T.Workspace(str(proj), str(proj / ".run")).exec_command(
        "python3 -c 'import sys; print(\"prefix=\" + sys.prefix)'")
    assert f"prefix={sys.prefix}" in out, out


@posix
def test_a_source_checkout_keeps_the_login_shells_own_path(tmp_path, monkeypatch):
    _login_profile_resets_path(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "packaged", lambda: False)
    proj = tmp_path / "proj"
    proj.mkdir()
    out = T.Workspace(str(proj), str(proj / ".run")).exec_command("echo path=$PATH")
    assert "path=/usr/bin:/bin" in out, out
