"""Workspace preload for the lean code lane: what an agent's first two or three calls would fetch, given up front.

Codex, Pi and OpenCode all open a task the same way: `ls`, `cat` the files the request names, `git log`/`git status`,
`python --version`. Each of those is a full model round trip (the whole prefix re-sent, a reply generated). Agentless and
Aider's repo map showed that handing the model the project's layout and the relevant source up front removes those
rounds without hurting solves. Here: the file list, the git state, the interpreter, and the full text of the files
most likely to matter, inside a fixed character budget. Everything is computed locally (no model call) and the block
rides on the first user message, so it is part of the cached prefix for every later call.

Off: MH_PRELOAD=0. Budget: MH_PRELOAD_CHARS (default 14000).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv", ".mypy_cache", ".ruff_cache",
             "dist", "build", ".tox", ".idea", ".vscode", ".next", ".meshpool", ".run", "target"}
TEXT_EXTS = {".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".toml", ".cfg", ".ini", ".yaml",
             ".yml", ".md", ".txt", ".csv", ".tsv", ".sh", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".h",
             ".cc", ".cpp", ".hpp", ".cs", ".swift", ".sql", ".html", ".css", ".xml", ".env.example", ""}
MAX_LIST = 200
MAX_FILE = 9000           # characters: a bigger file is listed with its size, not inlined


def _files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(filenames):
            if f.startswith(".") and f not in (".env.example",):
                continue
            out.append(Path(dirpath) / f)
            if len(out) >= 2000:
                return out
    return out


def _is_text(p: Path) -> bool:
    if p.suffix.lower() not in TEXT_EXTS:
        return False
    try:
        head = p.read_bytes()[:2048]
    except OSError:
        return False
    return b"\0" not in head


def _git(root: Path) -> str:
    if not (root / ".git").exists() or not shutil.which("git"):
        return "not a git repository"
    try:
        r = subprocess.run(["git", "log", "--oneline", "-5"], cwd=root, capture_output=True, text=True, timeout=5)
        log = r.stdout.strip() if r.returncode == 0 else ""
        s = subprocess.run(["git", "status", "--short"], cwd=root, capture_output=True, text=True, timeout=5)
        st = s.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = ["recent commits:\n" + log if log else "no commits yet"]
    if st:
        lines = st.splitlines()
        parts.append("uncommitted changes:\n" + "\n".join(lines[:20]) + (f"\n... {len(lines) - 20} more" if len(lines) > 20 else ""))
    return "\n".join(parts)


def _python() -> str:
    exe = shutil.which("python3") or shutil.which("python")
    if not exe:
        return ""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=3)
        return (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build(root, request: str = "", budget: int | None = None, seen=None) -> str:
    """seen(path, text): called for every file whose full text goes into the block."""
    root = Path(root)
    budget = budget or int(os.environ.get("MH_PRELOAD_CHARS", "14000"))
    files = _files(root)
    if not files:
        return ""
    rel = [p.relative_to(root).as_posix() for p in files]
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", request or "")}
    named = {m.lower() for m in re.findall(r"[\w./-]+\.\w+", request or "")}

    def score(i: int) -> tuple:
        r = rel[i]
        stem = Path(r).stem.lower()
        hit = (r.lower() in named or Path(r).name.lower() in named) * 100 + (stem in words) * 20
        try:
            size = files[i].stat().st_size
        except OSError:
            size = 10 ** 9
        return (-hit, "test" in r.lower(), size)

    order = sorted(range(len(files)), key=score)
    listing = []
    for r, p in zip(rel[:MAX_LIST], files[:MAX_LIST]):
        try:
            listing.append(f"{r} ({p.stat().st_size} B)")
        except OSError:
            listing.append(r)
    if len(rel) > MAX_LIST:
        listing.append(f"... and {len(rel) - MAX_LIST} more files")
    used = sum(len(x) + 1 for x in listing)
    shown = []
    for i in order:
        p = files[i]
        if not _is_text(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > MAX_FILE or used + len(text) > budget:
            continue
        shown.append(f'<file path="{rel[i]}">\n{text.rstrip()}\n</file>')
        if seen is not None:
            seen(p, text)
        used += len(text) + 30
    env = [x for x in (_python(), _git(root)) if x]
    out = ["<workspace>",
           "Meshpool read the project for you before your first turn: its files, the git state and the full text of "
           "the files below. They are current; do not list or cat them again.",
           "Files:\n" + "\n".join(listing)]
    if env:
        out.append("Environment: " + "\n".join(env))
    out += shown
    out.append("</workspace>")
    return "\n\n".join(out)
