"""A tiny task list for smoke runs and tests: create hello.py that prints hello."""
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Task:
    id: str
    kind: str
    prompt: str
    setup: Callable
    grade: Callable
    timeout: int = 300


def _setup(wd: Path) -> None:
    (wd / "README.txt").write_text("empty project\n")


def _grade(wd: Path, reply: str) -> dict:
    f = Path(wd) / "hello.py"
    if not f.exists():
        return {"ok": False, "detail": "no hello.py", "passed": 0, "total": 1}
    r = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, timeout=30, cwd=str(wd))
    ok = r.stdout.strip().lower() == "hello"
    return {"ok": ok, "detail": f"stdout={r.stdout.strip()[:80]!r}", "passed": int(ok), "total": 1}


TASKS = [Task("hello", "code", "Create a file hello.py in the current folder that prints exactly: hello. Then say done.",
              _setup, _grade)]


def get(ids):
    return [t for t in TASKS if not ids or ids == "all" or t.id in ids.split(",")]
