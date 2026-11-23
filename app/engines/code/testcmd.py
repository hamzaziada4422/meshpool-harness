"""Model-free test-runner discovery: probe the repo and its container once, tell the agent the exact command form.

Found on SWE-bench Verified: Django repos have no pytest; agents burned their whole turn budget searching for a way to
run tests (`tests/runtests.py` is the runner). Every repo family has its own convention, so detect it up front.
"""
from __future__ import annotations

import subprocess
import shutil
from pathlib import Path


def _cexec(container, cdir, cmd, timeout=60):
    try:
        r = subprocess.run(["docker", "exec", "-w", cdir, container, "bash", "-lc", cmd], capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode
    except subprocess.TimeoutExpired:
        return 124


def discover(host_root: str, container: str | None, cdir: str | None) -> str:
    root = Path(host_root)

    def run(code: str) -> int | None:                # exit code of `python -c code`; None: no python to ask
        if container:
            rc = _cexec(container, cdir, f"python3 -c '{code}' 2>/dev/null || python -c '{code}' 2>/dev/null")
            return None if rc == 127 else rc
        py = shutil.which("python") or shutil.which("python3")      # python3 only on macOS 12.3+/Debian/Ubuntu
        if not py:
            return None
        try:                                         # list args: no shell syntax, so it works under Windows too
            return subprocess.run([py, "-c", code], cwd=root, capture_output=True,
                                  timeout=60).returncode
        except subprocess.TimeoutExpired:
            return 1
        except OSError:
            return None
    lines = []
    if (root / "tests" / "runtests.py").exists() and (root / "django").is_dir():
        lines.append("This is Django: run tests with `python tests/runtests.py <app_label or dotted test path> "
                     "--verbosity 1` from the repo root, e.g. `python tests/runtests.py i18n.tests.MiscTests` "
                     "(paths are relative to tests/, dotted, without .py). pytest is not the runner here.")
    elif (root / "bin" / "test").exists() and (root / "sympy").is_dir():
        lines.append("This is SymPy: run tests with `bin/test <path/to/test_file.py> -k <name>` "
                     "(pytest may also work: check once).")
    if not lines and not any((root / f).exists() for f in ("setup.py", "pyproject.toml", "setup.cfg", "pytest.ini",
                                                          "tox.ini", "requirements.txt")):
        other = [(("go.mod",), "Go: run tests with `go test ./...` (or `go test ./pkg -run TestName`)."),
                 (("package.json",), "Node: run tests with `npm test` (see the scripts in package.json for the "
                                     "runner, e.g. jest or vitest, to run one file)."),
                 (("pom.xml",), "Maven: run tests with `mvn test` (one class: `mvn test -Dtest=ClassName`)."),
                 (("build.gradle", "build.gradle.kts"), "Gradle: run tests with `./gradlew test` (one class: "
                                                        "`--tests ClassName`)."),
                 (("Cargo.toml",), "Rust: run tests with `cargo test` (one test: `cargo test name`).")]
        for names, msg in other:
            if any((root / n).exists() for n in names):
                return msg
    rc = run("import pytest")
    has_pytest = rc == 0
    if rc is None:                                   # could not run python at all: say so, do not claim pytest is absent
        lines.append("Could not probe for pytest here. Try `python -m pytest -q <path>` (or `python3 -m pytest`); "
                     "if that says pytest is missing, use `python -m unittest <dotted.module.path>`.")
    elif not lines:
        if has_pytest:
            lines.append("Run tests with `python -m pytest -q <path/to/test_file.py>::<test_name>`.")
        else:
            lines.append("pytest is NOT installed in this environment. Run tests with "
                         "`python -m unittest <dotted.module.path>` or the project's own runner (look for "
                         "runtests.py, tox.ini, Makefile); do not install packages.")
    elif not has_pytest and rc is not None:
        lines.append("pytest is not installed; do not try to install it.")
    return " ".join(lines)
