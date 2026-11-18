"""Language packs: the fastest local checks for a language, and their output cut down to what the model needs.

A pack is a short prompt add-on plus a list of checks. The `diagnostics` tool runs the checks that exist on this
computer (or in the task's container), parses every location they report into `path:line:col  message`, and adds the
failing lines of the top three (core/errctx.py). The model gets a few hundred tokens instead of a raw compiler dump,
and never has to know each tool's flags.

Python checks use the newest interpreter on the machine (3.14+ when present: its syntax errors name the exact spot and
suggest the fix, e.g. "expected ':'", "did you mean 'import'?"), then Ruff's bug rules (pyflakes F, syntax E9), then
Pyright when the caller asks for a deep check.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from meshpool_harness.core import errctx

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".tox", ".mypy_cache",
             ".next", ".run", "site-packages"}
MAX_FILES = 400
MAX_LINES = 40              # problems listed in full; the rest are counted

# Compiles each file with the interpreter it runs on and prints `path:line:col: Kind: message`. Kept to syntax
# features of Python 3.6+, so any interpreter can run it.
PY_SYNTAX = r'''
import sys, json
bad = 0
for f in sys.argv[1:]:
    try:
        src = open(f, "rb").read()
    except OSError as e:
        print("%s:1:1: OSError: %s" % (f, e)); continue
    try:
        if f.endswith(".ipynb"):
            nb = json.loads(src.decode("utf-8", "replace"))
            for i, c in enumerate(nb.get("cells", [])):
                if c.get("cell_type") == "code":
                    code = "".join(c.get("source", []))
                    code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith(("%", "!")))
                    try:
                        compile(code, "%s[cell %d]" % (f, i + 1), "exec")
                    except SyntaxError as e:
                        bad += 1
                        print("%s:%s:%s: %s: %s (cell %d)" % (f, e.lineno or 1, e.offset or 0, type(e).__name__, e.msg, i + 1))
            continue
        compile(src, f, "exec", dont_inherit=True)
    except SyntaxError as e:
        bad += 1
        end = getattr(e, "end_offset", None)
        line = (e.text or "").rstrip("\n")
        print("%s:%s:%s: %s: %s" % (f, e.lineno or 1, e.offset or 0, type(e).__name__, e.msg))
        if line.strip():
            print("    " + line.strip()[:200])
    except ValueError as e:
        bad += 1
        print("%s:1:1: ValueError: %s" % (f, e))
print("python %d.%d.%d checked %d file(s)" % (sys.version_info[:3] + (len(sys.argv) - 1,)))
sys.exit(1 if bad else 0)
'''

CONFIG_SYNTAX = r'''
import sys, json
bad = 0
for f in sys.argv[1:]:
    try:
        text = open(f, encoding="utf-8").read()
        if f.endswith(".json"):
            json.loads(text)
        elif f.endswith(".toml"):
            try:
                import tomllib
            except ImportError:
                continue
            tomllib.loads(text)
        elif f.endswith((".yml", ".yaml")):
            try:
                import yaml
            except ImportError:
                continue
            list(yaml.safe_load_all(text))
    except json.JSONDecodeError as e:
        bad += 1; print("%s:%d:%d: JSONDecodeError: %s" % (f, e.lineno, e.colno, e.msg))
    except Exception as e:
        bad += 1
        m = getattr(e, "problem_mark", None)
        ln = (m.line + 1) if m else getattr(e, "lineno", 1) or 1
        print("%s:%d:1: %s: %s" % (f, ln, type(e).__name__, str(e).splitlines()[0][:200]))
sys.exit(1 if bad else 0)
'''


@dataclass
class Check:
    name: str
    exts: tuple                     # file extensions it looks at ("" = runs on the whole project)
    tool: str                       # executable that must exist ("" = always available)
    cmd: str                        # {files} and {exe} are filled in
    project: bool = False           # runs on the project, not on a file list
    deep: bool = False              # only when the caller asks for a deep check (slower)
    marker: str = ""                # project file that must exist (tsconfig.json, Cargo.toml, go.mod)


@dataclass
class Pack:
    lang: str
    prompt: str
    checks: list = field(default_factory=list)
    test_hint: str = ""


PY_PROMPT = ("Python: after each edit run diagnostics on the files you changed (syntax, undefined names), then the "
             "narrowest test (`python -m pytest path::test -x -q`). Read tracebacks bottom-up: the last project frame "
             "is usually the bug. Keep the project's Python version and style.")
TS_PROMPT = ("TypeScript/JavaScript: run diagnostics (tsc --noEmit) after edits; fix type errors at their source, never "
             "with `any` or `@ts-ignore` unless the user asks. Use the project's package manager (lockfile) and test "
             "runner (package.json scripts).")
PACKS = {
    "python": Pack("python", PY_PROMPT, [
        Check("python syntax", (".py", ".pyi", ".ipynb"), "", "{exe} {script} {files}"),
        Check("ruff (bugs)", (".py", ".pyi"), "ruff", "ruff check --no-fix --output-format concise --select F,E9 "
              "--quiet {files}"),
        Check("pyright", (".py",), "pyright", "pyright {files}", deep=True),
    ], "python -m pytest -x -q"),
    "typescript": Pack("typescript", TS_PROMPT, [
        Check("tsc", ("",), "tsc", "{exe} --noEmit --pretty false", project=True, marker="tsconfig.json"),
        Check("node --check", (".js", ".mjs", ".cjs"), "node", "for f in {files}; do node --check \"$f\" || true; done"),
    ], "npm test"),
    "javascript": Pack("javascript", TS_PROMPT, [
        Check("node --check", (".js", ".mjs", ".cjs"), "node", "for f in {files}; do node --check \"$f\" || true; done"),
        Check("tsc", ("",), "tsc", "{exe} --noEmit --pretty false", project=True, marker="tsconfig.json"),
    ], "npm test"),
    "rust": Pack("rust", "Rust: run diagnostics (cargo check) after edits and fix the first error first; the later "
                 "ones often follow from it. Prefer the compiler's suggested fix when it gives one.", [
        Check("cargo check", ("",), "cargo", "cargo check --message-format short --quiet 2>&1", project=True,
              marker="Cargo.toml"),
        Check("clippy", ("",), "cargo", "cargo clippy --message-format short --quiet 2>&1", project=True,
              deep=True, marker="Cargo.toml"),
    ], "cargo test"),
    "go": Pack("go", "Go: run diagnostics (go build, go vet) after edits; keep gofmt style.", [
        Check("go build", ("",), "go", "go build ./... 2>&1", project=True, marker="go.mod"),
        Check("go vet", ("",), "go", "go vet ./... 2>&1", project=True, marker="go.mod"),
    ], "go test ./..."),
    "java": Pack("java", "Java: run diagnostics after edits; use the project's build tool (Maven or Gradle) for "
                 "tests.", [
        Check("javac", (".java",), "javac", "javac -d \"${TMPDIR:-/tmp}/mh-javac\" -Xlint:none {files} 2>&1"),
    ], "mvn -q test"),
    "c": Pack("c", "C: run diagnostics (compiler syntax check) after edits.", [
        Check("cc -fsyntax-only", (".c", ".h"), "cc", "cc -fsyntax-only {files} 2>&1"),
    ]),
    "cpp": Pack("cpp", "C++: run diagnostics (compiler syntax check) after edits.", [
        Check("c++ -fsyntax-only", (".cc", ".cpp", ".cxx", ".hpp"), "c++", "c++ -fsyntax-only -std=c++20 {files} 2>&1"),
    ]),
    "csharp": Pack("csharp", "C#: run diagnostics (dotnet build) after edits.", [
        Check("dotnet build", ("",), "dotnet", "dotnet build -nologo -clp:NoSummary -v q 2>&1", project=True),
    ], "dotnet test"),
    "shell": Pack("shell", "Shell: check scripts with diagnostics (bash -n, shellcheck when present).", [
        Check("bash -n", (".sh", ".bash"), "bash", "for f in {files}; do bash -n \"$f\" 2>&1 | sed -E "
              "'s/^(.+): line ([0-9]+): /\\1:\\2:1: /'; done"),
        Check("shellcheck", (".sh", ".bash"), "shellcheck", "shellcheck -f gcc {files}"),
    ]),
}
CONFIG = Check("config syntax", (".json", ".toml", ".yml", ".yaml"), "", "{exe} {script} {files}")
LANGS = list(PACKS)


def newest_python() -> str | None:
    """The newest CPython on PATH (python3.20 down to python3.9), then python3 / python."""
    for minor in range(20, 8, -1):
        p = shutil.which(f"python3.{minor}")
        if p:
            return p
    return shutil.which("python3") or shutil.which("python")


def python_version(exe: str) -> str:
    try:
        r = subprocess.run([exe, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _tool(ws, name: str) -> str | None:
    """Where a check's executable is: the project's own (node_modules/.bin, .venv/bin) first, then PATH."""
    if not name:
        return ""
    for rel in (f"node_modules/.bin/{name}", f".venv/bin/{name}", f"venv/bin/{name}",
                f"node_modules/.bin/{name}.cmd", f".venv/Scripts/{name}.exe"):
        p = ws.root / rel
        if p.exists():
            return str(p)
    import sys
    beside = Path(sys.executable).parent / name          # pip-installed next to Meshpool's own Python (ruff)
    if beside.exists():
        return str(beside)
    return shutil.which(name)


def changed_files(ws) -> list[str]:
    """Files the agent wrote this session, then git's modified and untracked files, relative to the project."""
    out: list[str] = []
    for p in sorted(getattr(ws, "written", set()) | getattr(ws, "created", set())):
        try:
            rel = os.path.relpath(p, ws.root)
        except ValueError:
            continue
        if not rel.startswith("..") and (ws.root / rel).is_file():
            out.append(rel)
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ws.root, capture_output=True,
                           text=True, timeout=10)
        for line in r.stdout.splitlines():
            rel = line[3:].split(" -> ")[-1].strip().strip('"')
            if rel and (ws.root / rel).is_file() and rel not in out:
                out.append(rel)
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def project_files(ws, exts: tuple) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ws.root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.endswith(exts):
                out.append(os.path.relpath(os.path.join(dirpath, f), ws.root))
                if len(out) >= MAX_FILES:
                    return out
    return out


def languages_of(files: list[str]) -> list[str]:
    from .signals import EXT_LANG
    seen: list[str] = []
    for f in files:
        lang = EXT_LANG.get(Path(f).suffix)
        if lang in PACKS and lang not in seen:
            seen.append(lang)
    return seen


def _script(ws, name: str, body: str) -> str:
    d = Path(ws.run_dir) / "checks"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if not p.exists() or p.read_text() != body:
        p.write_text(body)
    return str(p)


def _q(files: list[str]) -> str:
    return " ".join(shlex.quote(f) for f in files)


def diagnostics(ws, paths: list[str] | None = None, language: str | None = None, deep: bool = False,
                timeout_s: int = 120) -> str:
    """Run a language's fast checks and return the problems as `path:line:col  message`, plus the failing lines."""
    files = [p for p in (paths or []) if p]
    if files:
        expanded: list[str] = []
        for p in files:
            q = (ws.root / p)
            if q.is_dir():
                expanded += [os.path.relpath(x, ws.root) for x in q.rglob("*") if x.is_file()
                             and not (set(x.relative_to(ws.root).parts) & SKIP_DIRS)][:MAX_FILES]
            else:
                expanded.append(p)
        files = expanded
    else:
        files = changed_files(ws)
    langs = [language] if language in PACKS else (languages_of(files) or [])
    if not langs:
        from .signals import project_language
        pl = project_language(str(ws.root))
        langs = [pl] if pl in PACKS else []
    if not langs and not any(f.endswith(CONFIG.exts) for f in files):
        return ("No language detected to check. Pass paths (files you changed) or language "
                f"(one of: {', '.join(LANGS)}).")
    ran, missing, problems, raw_tail = [], [], [], []
    container = bool(getattr(ws, "container", None))
    checks = [(lang, c) for lang in langs for c in PACKS[lang].checks]
    if any(f.endswith(CONFIG.exts) for f in files):
        checks.append(("config", CONFIG))
    for lang, c in checks:
        if c.deep and not deep:
            continue
        if c.marker and not (ws.root / c.marker).exists():
            continue
        targets: list[str] = []
        if not c.project:
            targets = [f for f in files if f.endswith(c.exts)]
            if not targets and not paths and lang != "config":
                targets = project_files(ws, c.exts)
            if not targets:
                continue
        exe = ""
        if c.name in ("python syntax", "config syntax"):
            exe = "python3" if container else (newest_python() or "")
            if not exe:
                missing.append(f"{c.name} (no python found)")
                continue
        elif c.tool:
            found = "x" if container else _tool(ws, c.tool)
            if not found:
                missing.append(f"{c.name} ({c.tool} not installed)")
                continue
            exe = c.tool if container else found
        if container and c.name in ("python syntax", "config syntax"):
            body = PY_SYNTAX if c.name == "python syntax" else CONFIG_SYNTAX
            cmd = f"python3 - {_q(targets)} <<'MH_EOF'\n{body}\nMH_EOF"
        else:
            script = ""
            if c.name == "python syntax":
                script = _script(ws, "py_syntax.py", PY_SYNTAX)
            elif c.name == "config syntax":
                script = _script(ws, "config_syntax.py", CONFIG_SYNTAX)
            tmpl = c.cmd
            if c.tool and tmpl.startswith(c.tool + " "):
                tmpl = "{exe}" + tmpl[len(c.tool):]       # the executable found (project's own, Meshpool's, PATH)
            tmpl = tmpl.replace("node --check", "{exe} --check") if c.tool == "node" else tmpl
            cmd = tmpl.format(exe=shlex.quote(exe) if exe else "", script=shlex.quote(script), files=_q(targets))
        out, code = ws._run(cmd, timeout_s, "check", raw=True)
        label = c.name
        if c.name == "python syntax" and not container:
            v = python_version(exe)
            label = f"python {v} syntax" if v else label
        ran.append(label)
        body = out.split("\n", 1)[1] if out.startswith("[exit") else out
        locs = errctx.locations(body)
        if not locs and code not in (0, None) and body.strip() and "checked" not in body.strip().splitlines()[-1]:
            raw_tail.append(f"[{label}]\n" + body.strip()[-800:])
        blines = body.splitlines()
        for path, line, col, msg in locs:
            if not msg.strip():                  # node --check: the error sits a few lines below the location
                at = next((i for i, l in enumerate(blines) if f"{path}:{line}" in l), -1)
                msg = next((l.strip() for l in blines[at + 1: at + 8] if re.match(r"\s*\w*(Error|Exception):", l)), "")
            try:
                if os.path.isabs(path) and Path(path).resolve().is_relative_to(ws.root):
                    path = os.path.relpath(Path(path).resolve(), ws.root)
            except (OSError, ValueError):
                pass
            problems.append((path, line, col, msg.strip()))
    seen, uniq = set(), []
    for p in problems:
        syntax = "syntax" in p[3].lower()          # the compiler and Ruff both report it: keep the compiler's
        k = (os.path.normpath(p[0]), p[1], "syntax" if syntax else p[3][:80])
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    head = f"Checks run: {', '.join(ran) or 'none'}."
    if missing:
        head += f" Not available: {', '.join(missing)}."
    if not uniq and not raw_tail:
        return head + (" No problems found." if ran else " Nothing was checked.")
    lines = [f"{p}:{ln}:{col}  {msg}" if col else f"{p}:{ln}  {msg}" for p, ln, col, msg in uniq[:MAX_LINES]]
    more = f"\n... and {len(uniq) - MAX_LINES} more" if len(uniq) > MAX_LINES else ""
    text = f"{head} {len(uniq)} problem(s):\n" + "\n".join(lines) + more
    if raw_tail:
        text += "\n\nUnparsed output:\n" + "\n\n".join(raw_tail)[:1500]
    where = errctx.context("\n".join(f"{p}:{ln}:{col}: {msg}" for p, ln, col, msg in uniq[:3]), ws)
    return text + where


SCHEMA = {"type": "function", "function": {
    "name": "diagnostics",
    "description": ("Check code for errors without running it: syntax (the newest Python, node), compilers and type "
                    "checkers (tsc, cargo check, go build/vet, javac, cc), Ruff bug rules, config files (JSON, TOML, "
                    "YAML). Returns each problem as path:line:col message with the failing lines. With no paths it "
                    "checks the files changed in this project. Use after edits and to locate a syntax or type error."),
    "parameters": {"type": "object", "properties": {
        "paths": {"type": "array", "items": {"type": "string"}, "description": "Files or folders; default: changed files"},
        "language": {"type": "string", "enum": LANGS, "description": "Default: from the files"},
        "deep": {"type": "boolean", "description": "Also run slower checks (pyright, clippy)"}},
        "required": [], "additionalProperties": False}}}


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)
