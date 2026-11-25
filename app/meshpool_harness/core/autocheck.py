"""G-15: an optional lint/test pass after a Code chat's edits (MH_AUTO_CHECK=lint|test|both|off, default off).

Detects the project's own tools (ruff / flake8 for Python, eslint for JavaScript, pytest -x -q on the tests that belong
to the touched files, npm test) and runs them once at the end of a turn; the failures are given back to the agent once.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

MODES = ("lint", "test", "both")


def mode() -> str:
    v = os.environ.get("MH_AUTO_CHECK", "").strip().lower()
    if not v:
        try:
            from meshpool_harness import config as C
            v = str(C.read().get("MH_AUTO_CHECK", "")).strip().lower()
        except Exception:  # noqa: BLE001
            v = ""
    return v if v in MODES else "off"


def _tests_for(root: Path, files: list[Path]) -> list[str]:
    out = []
    for f in files:
        if f.suffix != ".py":
            continue
        if f.name.startswith("test_") or f.name.endswith("_test.py"):
            out.append(str(f))
            continue
        for d in (root / "tests", root / "test", f.parent / "tests", f.parent):
            t = d / f"test_{f.name}"
            if t.is_file():
                out.append(str(t))
                break
    return list(dict.fromkeys(out))


def detect(root, files, which_mode: str, which=shutil.which) -> list[list[str]]:
    """The commands to run, as argv lists, for the touched files. Empty when the project has no tool we know."""
    root = Path(root)
    fs = [Path(f) for f in files if Path(f).is_file()]
    py = [str(f) for f in fs if f.suffix == ".py"]
    js = [str(f) for f in fs if f.suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs")]
    cmds: list[list[str]] = []
    if which_mode in ("lint", "both"):
        if py and which("ruff") and any((root / n).exists() for n in ("ruff.toml", ".ruff.toml", "pyproject.toml")):
            cmds.append(["ruff", "check", *py])
        if js and (root / "node_modules" / ".bin" / "eslint").exists():
            cmds.append([str(root / "node_modules" / ".bin" / "eslint"), *js])
    if which_mode in ("test", "both"):
        tests = _tests_for(root, fs)
        if tests and (which("pytest") or which("python3") or which("python")):
            cmds.append(["pytest", "-x", "-q", *tests] if which("pytest") else [which("python3") or "python", "-m", "pytest", "-x", "-q", *tests])
        elif not tests and not py and js and (root / "package.json").is_file() and which("npm"):
            cmds.append(["npm", "test", "--silent"])
    return cmds


def run(root, files, which_mode: str | None = None, timeout: float = 120, runner=subprocess.run) -> str:
    """'' when everything passes (or nothing could run); otherwise the failing output, ready to give the agent."""
    m = which_mode or mode()
    if m == "off":
        return ""
    bad = []
    for argv in detect(root, files, m):
        try:
            r = runner(argv, cwd=str(root), capture_output=True, text=True, timeout=timeout, errors="replace",
                       **({"creationflags": 0x08000000} if os.name == "nt" else {}))
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode:
            bad.append(f"$ {shlex.join(argv)}\n{(r.stdout + r.stderr).strip()[-3000:]}")
    if not bad:
        return ""
    return ("[Meshpool] The automatic check after your edits failed. Fix these and stop; it will not be run again "
            "this turn:\n" + "\n\n".join(bad))
