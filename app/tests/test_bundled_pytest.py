"""pytest in the app (first-hour run, 0.0.18): a Code chat told the user to `pip install pytest`.

pytest (MIT, with pluggy and iniconfig, MIT) is now installed into the bundled Python like the app's other packages.
That alone was not enough: pip writes the build machine's Python path into every command it installs, so in the
unpacked app `pytest` (and ruff, jupyter...) said "not found". The build now rewrites those commands to start the
python next to them (packaging/bundled_tools.relocatable_scripts; Windows: relocatable_launchers)."""
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from meshpool_harness.agent import core
from meshpool_harness.agent.core import Agent

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging"))
import bundled_tools as BT  # noqa: E402

posix = pytest.mark.skipif(os.name == "nt", reason="the POSIX commands of the Linux and Mac apps")


def _requirement_names(path: Path) -> set:
    return {line.split("#")[0].strip().split(">")[0].split("=")[0].split(";")[0].strip().lower()
            for line in path.read_text(encoding="utf-8").splitlines() if line.split("#")[0].strip()}


def test_pytest_is_installed_into_the_bundled_python_and_read_for_the_mac():
    assert "pytest" in _requirement_names(ROOT / "packaging" / "requirements-cli.txt")
    record = json.loads((ROOT / "packaging" / "mac" / "verified_minos.json").read_text(encoding="utf-8"))
    for arch in ("arm64", "x86_64"):
        names = {k.split("==")[0] for k in record[arch]}
        assert {"pytest", "pluggy", "iniconfig", "packaging", "pygments"} <= names, arch   # pytest's dependencies too
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for name in ("pytest", "pluggy", "iniconfig"):
        row = next(ln for ln in notices.splitlines() if ln.startswith(f"| {name} "))
        assert row.rstrip().endswith("| MIT |"), row
    listed = (ROOT / "packaging" / "python-packages.txt").read_text(encoding="utf-8").split()
    assert {"pytest", "pluggy", "iniconfig"} <= set(listed)


def test_the_build_rewrites_the_commands_of_every_bundled_python():
    ba = (ROOT / "packaging" / "build_all.py").read_text(encoding="utf-8")
    assert 'relocatable_scripts(root / "python" / "bin", RT["linux"] / "bin")' in ba
    assert 'relocatable_scripts(root / "Resources/python/bin", RT[arch] / "bin")' in ba
    assert 'relocatable_launchers(root / "runtime" / "Scripts")' in ba


def _bundle(tmp_path, long_form: bool) -> tuple[Path, Path]:
    """A Python bin folder as the release leaves it: `python3` (here this test's own Python) and `pytest`, a command
    pip wrote while the runtime sat in deps/rt-linux/python on the build machine. Returns (build bin, bundle bin)."""
    build = tmp_path / "runner" / "deps" / "rt-linux" / "python" / "bin"
    bundle = tmp_path / "Meshpool" / "python" / "bin"
    bundle.mkdir(parents=True)
    (bundle / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')   # this test's Python, with pytest
    (bundle / "python3").chmod(0o755)
    body = "import re, sys\nfrom pytest import console_main\nsys.exit(console_main())\n"
    head = (f"#!/bin/sh\n'''exec' {build}/python3 \"$0\" \"$@\"\n' '''\n" if long_form else f"#!{build}/python3\n")
    (bundle / "pytest").write_text(head + body)
    (bundle / "pytest").chmod(0o755)
    (bundle / "pip3").write_text("#!/bin/sh\n'''exec' \"$(dirname -- \"$(realpath -- \"$0\")\")/python3.12\" \"$0\" \"$@\"\n"
                                 "' '''\nprint('pbs own')\n")        # python-build-standalone's own form: left alone
    return build, bundle


def _scripted(agent, replies):
    seq = list(replies)

    def fake(base, key, body, stream):
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, False
    agent._post = fake


def _code_chat_runs_pytest(proj: Path, monkeypatch) -> str:
    """A Code chat in `proj` whose model runs `pytest -q`: the command's output as the agent saw it."""
    monkeypatch.setattr(core, "SESSIONS", proj.parent / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    a = Agent(str(proj), mode="auto", approve=lambda *x, **k: ("yes", ""))
    _scripted(a, [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": "pytest -q"})}}]},
        {"role": "assistant", "content": "The tests pass."}])
    a.send("run the tests")
    return next(m["content"] for m in a.messages if m["role"] == "tool")


@posix
@pytest.mark.parametrize("long_form", [False, True], ids=["shebang", "sh-exec form"])
def test_a_code_chat_runs_pytest_in_the_unpacked_app(tmp_path, monkeypatch, long_form):
    build, bundle = _bundle(tmp_path, long_form)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "test_math.py").write_text("def test_add():\n    assert 1 + 1 == 2\n")
    monkeypatch.setenv("PATH", f"{bundle}{os.pathsep}/usr/bin{os.pathsep}/bin")   # the launcher puts it first
    out = _code_chat_runs_pytest(proj, monkeypatch)
    # what the shipped app did: no Python there. Each shell words it its own way: "not found" (dash, bash 5.2:
    # "required file not found") or "bad interpreter: No such file or directory" (bash 5.3, the CI runner)
    assert "1 passed" not in out and out.startswith("[exit ") and any(
        w in out for w in ("not found", "bad interpreter", "No such file")), out
    changed = BT.relocatable_scripts(bundle, build)
    assert [p.name for p in changed] == ["pytest"]
    assert (bundle / "pip3").read_text().endswith("print('pbs own')\n")
    assert os.access(bundle / "pytest", os.X_OK)
    out = _code_chat_runs_pytest(proj, monkeypatch)
    assert "1 passed" in out and "pip install" not in out, out
    assert BT.relocatable_scripts(bundle, build) == []                          # a second build pass changes nothing


def test_windows_launchers_find_the_python_next_to_them(tmp_path):
    """A pip .exe launcher is the launcher program, a #! line with the build machine's python.exe, then a zip with
    __main__.py. The line becomes <launcher_dir>\\..\\python.exe and the zip still opens."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("__main__.py", "from pytest import console_main\nconsole_main()\n")
    scripts = tmp_path / "runtime" / "Scripts"
    scripts.mkdir(parents=True)
    exe = scripts / "pytest.exe"
    exe.write_bytes(b"MZ launcher code #!not-this\n" + b'#!D:\\a\\meshpool\\deps\\rt-win\\python\\python.exe\n'
                    + buf.getvalue())
    (scripts / "notes.exe").write_bytes(b"MZ a program with no zip")
    assert [p.name for p in BT.relocatable_launchers(scripts)] == ["pytest.exe"]
    data = exe.read_bytes()
    assert b'#!"<launcher_dir>\\..\\python.exe"\n' in data and b"deps\\rt-win" not in data
    assert data.startswith(b"MZ launcher code #!not-this\n")
    with zipfile.ZipFile(exe) as z:
        assert "console_main" in z.read("__main__.py").decode()
