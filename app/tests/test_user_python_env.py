"""The user's project runs its own Python: no Meshpool app folder on PYTHONPATH (its sitecustomize.py and `tests`
package leaked into every command), bytecode allowed, and an edited module's cached bytecode dropped."""
import os
import subprocess
import sys
from pathlib import Path

from meshpool_harness.core import tools as T


def test_app_folder_is_never_on_the_users_pythonpath(monkeypatch):
    app = str(Path(T.__file__).resolve().parents[2])
    env = T._user_python_env({"PYTHONPATH": os.pathsep.join([app, "/opt/lib"]), "PYTHONDONTWRITEBYTECODE": "1"})
    assert env["PYTHONPATH"] == "/opt/lib" and "PYTHONDONTWRITEBYTECODE" not in env
    assert "PYTHONPATH" not in T._user_python_env({"PYTHONPATH": app})


def test_a_shell_command_does_not_load_meshpools_sitecustomize(tmp_path, monkeypatch):
    app = str(Path(T.__file__).resolve().parents[2])
    monkeypatch.setenv("PYTHONPATH", app)
    ws = T.Workspace(str(tmp_path), str(tmp_path / ".run"))
    ws.engine_libs = False                              # as the chat agent's workspace
    out, code = ws._run(f'"{sys.executable}" -c "import sys, os; print([p for p in sys.path if p.rstrip(os.sep) == '
                        f'{app!r}.rstrip(os.sep)])"', 30, "bash")
    assert code == 0 and "[]" in out, out


def test_an_edit_drops_the_modules_cached_bytecode(tmp_path):
    src = tmp_path / "m.py"
    src.write_text("X = 1\n")
    subprocess.run([sys.executable, "-c", "import m"], cwd=tmp_path, check=True, env={**os.environ, "PYTHONPATH": ""})
    assert list((tmp_path / "__pycache__").glob("m.*.pyc"))
    T.write_src(src, "X = 2\n")                        # same size, same second
    assert not list((tmp_path / "__pycache__").glob("m.*.pyc"))
    out = subprocess.run([sys.executable, "-c", "import m; print(m.X)"], cwd=tmp_path, capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": ""}).stdout
    assert out.strip() == "2"


def test_the_chat_agents_workspace_is_clean_and_an_engines_is_not(tmp_path):
    from meshpool_harness.agent.core import Agent
    assert Agent(str(tmp_path), mode="auto").ws.engine_libs is False
    assert T.Workspace(str(tmp_path), str(tmp_path / ".run")).engine_libs is True
