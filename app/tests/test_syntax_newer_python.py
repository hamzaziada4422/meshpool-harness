"""The edit-time syntax check judges Python code with the newest Python that would run it, not only MeshPool's own
(3.12 in the app): valid 3.14 syntax such as `except A, B:` must not be rejected in a project that runs 3.14."""
import sys
from pathlib import Path

import pytest

from meshpool_harness.core import tools as T

NEW_SYNTAX = "try:\n    pass\nexcept ValueError, TypeError:\n    pass\n"     # PEP 758 (3.14)


def _fake_python(path: Path, version: str, accepts: bool):
    """A stand-in for a newer interpreter: reports `version`, and accepts or rejects any code on stdin."""
    path.parent.mkdir(parents=True, exist_ok=True)
    major, minor = version.split(".")
    path.write_text(f"""#!/bin/sh
case "$2" in
  *version_info*) echo {major} {minor}; exit 0 ;;
esac
cat > /dev/null
exit {0 if accepts else 1}
""")
    path.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in interpreter is a shell script")
@pytest.mark.skipif(sys.version_info >= (3, 14), reason="this interpreter already parses 3.14 syntax")
def test_project_venv_with_a_newer_python_accepts_new_syntax(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "_PY_VERSIONS", {})
    monkeypatch.setattr("meshpool_harness.hrouter.packs.newest_python", lambda: None)
    proj = tmp_path / "proj"
    _fake_python(proj / ".venv" / "bin" / "python", "3.14", accepts=True)
    assert T.Workspace._syntax_error(proj / "m.py", NEW_SYNTAX) is None


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in interpreter is a shell script")
@pytest.mark.skipif(sys.version_info >= (3, 14), reason="this interpreter already parses 3.14 syntax")
def test_without_a_newer_python_the_error_stands(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "_PY_VERSIONS", {})
    monkeypatch.setattr("meshpool_harness.hrouter.packs.newest_python", lambda: None)
    err = T.Workspace._syntax_error(tmp_path / "m.py", NEW_SYNTAX)
    assert err and "line 3" in err


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in interpreter is a shell script")
def test_a_real_syntax_error_is_still_rejected_when_the_newer_python_also_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "_PY_VERSIONS", {})
    monkeypatch.setattr("meshpool_harness.hrouter.packs.newest_python", lambda: None)
    proj = tmp_path / "proj"
    _fake_python(proj / ".venv" / "bin" / "python", "3.99", accepts=False)
    assert "line 1" in T.Workspace._syntax_error(proj / "m.py", "def f(:\n    pass\n")
