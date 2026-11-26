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
            try:
                json.loads(text)
            except json.JSONDecodeError:
                # JSONC (tsconfig, .vscode/*, devcontainer, eslintrc): comments and trailing commas are legal there
                import re
                n = f.replace("\\", "/").lower()
                base = n.rsplit("/", 1)[-1]
                if not (base.startswith(("tsconfig", "jsconfig", ".eslintrc", ".babelrc", "devcontainer", "tslint"))
                        or "/.vscode/" in "/" + n or "/.devcontainer/" in "/" + n):
                    raise
                t = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/',
                           lambda m: m.group(0) if m.group(0).startswith('"') else " ", text, flags=re.S)
                t = re.sub(r',(\s*[}\]])', r'\1', t)
                try:
                    json.loads(t)
                except json.JSONDecodeError:
                    raise
        elif f.endswith(".toml"):
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib          # Python 3.10 has no tomllib: TOML was never checked there
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
        Check("javac", (".java",), "javac", "javac -d \"${{TMPDIR:-/tmp}}/mh-javac\" -Xlint:none {files} 2>&1"),
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
    from meshpool_harness import trust as TR
    local = (f"node_modules/.bin/{name}", f".venv/bin/{name}", f"venv/bin/{name}", f"node_modules/.bin/{name}.cmd",
             f".venv/Scripts/{name}.exe") if TR.is_trusted(ws.root) else ()   # a stranger's repo: never its programs
    for rel in local:
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
        from meshpool_harness.core.tools import safe_git
        r = subprocess.run(safe_git("status", "--porcelain", "--untracked-files=all"), cwd=ws.root, capture_output=True,
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
        if "TS18003" in body:                    # tsconfig with nothing to compile: not a problem in the code
            body = "\n".join(l for l in body.splitlines() if "TS18003" not in l)
            if code not in (0, None) and not body.strip():
                code = 0
        sp_locs = []                             # paths with spaces: errctx's patterns stop at the first space
        keep = []
        for l in body.splitlines():
            m = re.match(r"^(\S[^:\n]*? [^:\n]*\.\w{1,6}):(\d+):(\d+):? ?(.*)$", l)
            if m and (ws.root / m.group(1)).exists():
                sp_locs.append((m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)))
            else:
                keep.append(l)
        body = "\n".join(keep) if sp_locs else body
        locs = errctx.locations(body) + sp_locs
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
    has_other = {os.path.normpath(p[0]) for p in problems if not p[3].startswith("E902")}
    problems = [p for p in problems if not (p[3].startswith("E902") and os.path.normpath(p[0]) in has_other)]
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


# ---------- zero-call checks: after an edit, and before the first turn of a debug task ---------------------------------
QUICK_EXTS = (".py", ".pyi", ".js", ".mjs", ".cjs", ".json", ".toml", ".yml", ".yaml", ".sh", ".bash")


class _Stop(Exception):
    pass


def _same_python(exe: str) -> bool:
    """True when `exe` is this Python, or another copy of the same minor version (it accepts the same syntax)."""
    import sys
    try:
        if os.path.realpath(exe) == os.path.realpath(sys.executable):
            return True
    except (OSError, ValueError):
        return False
    from meshpool_harness import config as C
    got = C.program_version(exe)                      # asked once per program file
    m = re.match(r"Python (\d+)\.(\d+)", got[1]) if got and got[0] == 0 else None
    return bool(m) and (int(m.group(1)), int(m.group(2))) == tuple(sys.version_info[:2])


def _inline(body: str, root, rel: str) -> str:
    """Run one of the check scripts above in this process on one file: what it prints. Every edit of a .py, .json,
    .toml or .yaml file used to start a Python process for this (about 30 ms of CPU each); when the newest Python on
    the computer is the same version as this one, its answer is the same. The script sees its own argv, exit and
    print, and opens files relative to the project, as it would in a process of its own."""
    import builtins
    import sys
    import types
    out: list[str] = []

    def _exit(code=0):
        raise _Stop()
    fake = types.SimpleNamespace(argv=["check", rel], version_info=sys.version_info, exit=_exit)
    real_import = builtins.__import__

    def _import(name, *a, **k):
        return fake if name == "sys" else real_import(name, *a, **k)

    def _open(f, *a, **k):
        return builtins.open(os.path.join(str(root), f), *a, **k)
    env = dict(vars(builtins), __import__=_import, open=_open,
               print=lambda *a, **k: out.append(" ".join(str(x) for x in a)))
    try:
        exec(compile(body, "<meshpool check>", "exec"), {"__builtins__": env, "__name__": "__check__"})  # noqa: S102
    except _Stop:
        pass
    except Exception:  # noqa: BLE001 - a crash in its own process printed no finding either
        pass
    return "\n".join(out)


def _syntax_lines(ws, exe: str, name: str, body: str, rel: str) -> list[str]:
    if _same_python(exe):
        text = _inline(body, ws.root, rel)
    else:
        text = subprocess.run([exe, _script(ws, name, body), rel], cwd=ws.root, capture_output=True, text=True,
                              timeout=15).stdout
    return [l for l in text.splitlines() if re.match(r".+:\d+:\d+: ", l)]


def quick_check(ws, path: str) -> str:
    """Problems in one just-edited file (syntax, undefined names, config syntax), or "" when it is clean.
    Appended to the edit's result, so the model sees a broken edit at once without spending a call on it."""
    if getattr(ws, "container", None) or not str(path).endswith(QUICK_EXTS):
        return ""
    try:
        p = ws._path(path)
        rel = os.path.relpath(p, ws.root)
    except Exception:  # noqa: BLE001
        return ""
    if not p.is_file() or p.stat().st_size > 1_000_000:
        return ""
    lines: list[str] = []
    try:
        if rel.endswith((".py", ".pyi")):
            exe = newest_python()
            if exe:
                lines += _syntax_lines(ws, exe, "py_syntax.py", PY_SYNTAX, rel)
            ruff = _tool(ws, "ruff")
            if ruff and not lines:
                r = subprocess.run([ruff, "check", "--no-fix", "--output-format", "concise", "--select", "F821,F822,F823,F811,F632,F401,E9",
                                    "--quiet", rel], cwd=ws.root, capture_output=True, text=True, timeout=15)
                lines += [l for l in r.stdout.splitlines() if re.match(r".+:\d+:\d+: ", l)]
        elif rel.endswith((".js", ".mjs", ".cjs")):
            node = shutil.which("node")
            from meshpool_harness.core.tools import node_check_known
            if node and not node_check_known(p, node):       # the edit tool checked this very text already
                r = subprocess.run([node, "--check", rel], cwd=ws.root, capture_output=True, text=True, timeout=15)
                if r.returncode:
                    err = next((l.strip() for l in r.stderr.splitlines() if re.match(r"\s*\w*Error:", l)), "syntax error")
                    loc = next((l.strip() for l in r.stderr.splitlines() if re.search(r":\d+$", l.strip())), rel)
                    lines.append(f"{loc}: {err}")
        elif rel.endswith((".json", ".toml", ".yml", ".yaml")):
            exe = newest_python()
            if exe:
                lines += _syntax_lines(ws, exe, "config_syntax.py", CONFIG_SYNTAX, rel)
        elif rel.endswith((".sh", ".bash")) and shutil.which("bash"):
            r = subprocess.run(["bash", "-n", rel], cwd=ws.root, capture_output=True, text=True, timeout=15)
            lines += [l.strip() for l in r.stderr.splitlines() if l.strip()][:3]
    except (OSError, subprocess.SubprocessError):
        return ""
    if not lines:
        return ""
    unused_only = all(" F401 " in l for l in lines)
    if unused_only:
        return ""                                     # an unused import is not worth the model's attention mid-edit
    return "\n[check after this edit: fix before moving on]\n" + "\n".join(l for l in lines if " F401 " not in l)[:1200]


from .signals import cap_text as _cap_text   # noqa: E402

_RUNNER = re.compile(r"`([^`\n]{2,160})`|(?<![\w/.-])((?:python3?|node|deno|ruby|bash|sh)\s+[\w./-]+\.(?:py|js|mjs|cjs|ts|rb|sh)"
                     r"(?:\s+--?[\w-]+(?:[ =][\w./-]+)?)*|pytest(?:\s+[\w./:-]+)*|npm (?:run )?test|yarn test|pnpm test|"
                     r"cargo (?:test|run|build)|go (?:test|run|build)(?:\s+\./\.\.\.)?)")
_TESTFILE = re.compile(r"(?<![\w/.-])((?:[\w./-]*/)?test_[\w-]+\.py|(?:[\w./-]*/)?[\w-]+_test\.py|(?:[\w./-]*/)?[\w-]+\.test\.[jt]s)\b")
_STARTS = ("python", "python3", "node", "deno", "ruby", "bash", "sh", "pytest", "npm", "yarn", "pnpm", "cargo", "go",
           "make", "./")
# only these are re-run automatically before the first turn: they test or run a named file, never publish/deploy/install
_SAFE_RUN = re.compile(r"^(?:(?:python3?|node|deno|ruby)\s+[\w./-]+\.(?:py|js|mjs|cjs|ts|rb)(?:\s+.*)?"
                       r"|pytest(?:\s+.*)?|python3?\s+-m\s+pytest(?:\s+.*)?|npm (?:run )?test|yarn test|pnpm test"
                       r"|cargo test(?:\s+.*)?|go test(?:\s+.*)?)$")
_UNSAFE_ARGS = re.compile(r"--(?:wipe|delete|force|publish|deploy)\b|\brm\b|\bsudo\b")


def repro_commands(text: str, root: Path, limit: int = 2) -> list[str]:
    """Commands the user named for reproducing the problem, when the files they refer to exist."""
    text = _cap_text(text)
    cmds: list[str] = []
    py = "python3" if shutil.which("python3") else "python"
    for m in _RUNNER.finditer(text or ""):
        c = (m.group(1) or m.group(2) or "").strip()
        if not c or c.split()[0] not in _STARTS or any(ch in c for ch in ";&|><$"):
            continue
        if not _SAFE_RUN.match(c) or _UNSAFE_ARGS.search(c):
            continue
        files = [t for t in c.split()[1:] if re.search(r"\.\w{1,4}$", t)]
        if files and not all((root / f).exists() for f in files):
            continue
        c = re.sub(r"^python\b(?!3)", py, c)
        if c not in cmds:
            cmds.append(c)
    for m in _TESTFILE.finditer(text or ""):
        f = m.group(1)
        if not (root / f).is_file():
            continue
        if f.endswith(".py"):
            body = (root / f).read_text(encoding="utf-8", errors="replace")
            c = f"{py} -m pytest -x -q {f}" if re.search(r"^\s*def test_", body, re.M) and shutil.which("pytest") \
                else f"{py} {f}"
        else:
            continue
        if not any(f in x for x in cmds):
            cmds.append(c)
    return cmds[:limit]


def reproduce(ws, text: str) -> str:
    """Run the commands the user named before the model's first turn; their output, ready to read."""
    text = _cap_text(text)
    if getattr(ws, "container", None) or re.search(r"\bdocker (exec|run)\b", text or ""):
        return ""                                     # the code runs elsewhere: a host run would mislead
    blocks = []
    for c in repro_commands(text, ws.root):
        out, code = ws._run(c, 30, "check")
        blocks.append(f"$ {c}\n{out.strip()[:2500]}")
    if not blocks:
        return ""
    return ("<reproduction>\nMeshpool ran the command(s) you named before you started, so you need not run them "
            "again before your first fix:\n\n" + "\n\n".join(blocks) + "\n</reproduction>")


# ---------- data: the files' shape before the first turn ---------------------------------------------------------------
DATA_EXTS = (".csv", ".tsv", ".parquet", ".xlsx", ".xls", ".json", ".jsonl")


def data_preview(ws, text: str = "", limit: int = 5) -> str:
    """Columns, types, row counts and three sample rows of the data files the task is about (named in the request,
    else the ones at the top of the folder), so the model starts analysing instead of exploring."""
    text = _cap_text(text)
    if getattr(ws, "container", None):
        return ""
    try:
        import pandas as pd
    except ImportError:
        return ""
    named = [m for m in re.findall(r"[\w./-]{1,200}\.(?:csv|tsv|parquet|xlsx|xls|jsonl?)\b", text or "")
             if (ws.root / m).is_file()]
    files = named or sorted(p.name for p in ws.root.iterdir() if p.is_file() and p.suffix.lower() in DATA_EXTS)
    blocks = []
    for f in files[:limit]:
        p = ws.root / f
        if p.stat().st_size > 200_000_000:
            blocks.append(f"{f}: {p.stat().st_size // 1_000_000} MB (too big to preview; query it with DuckDB)")
            continue
        try:
            ext = p.suffix.lower()
            if ext in (".csv", ".tsv"):
                df = pd.read_csv(p, sep="\t" if ext == ".tsv" else ",", low_memory=False)
            elif ext == ".parquet":
                df = pd.read_parquet(p)
            elif ext in (".xlsx", ".xls"):
                sheets = pd.read_excel(p, sheet_name=None)
                name, df = next(iter(sheets.items()))
                if len(sheets) > 1:
                    f = f"{f} (sheet '{name}' of {len(sheets)}: {', '.join(list(sheets)[:6])})"
            else:
                df = pd.read_json(p, lines=ext == ".jsonl")
        except Exception as e:  # noqa: BLE001
            blocks.append(f"{f}: could not read ({type(e).__name__}: {str(e)[:120]})")
            continue
        cols = ", ".join(f"{c} ({t})" for c, t in list(df.dtypes.astype(str).items())[:40])
        nulls = {c: int(n) for c, n in df.isna().sum().items() if n}
        sample = df.head(3).to_csv(index=False).strip()
        blocks.append(f"{f}: {len(df):,} rows x {df.shape[1]} columns\ncolumns: {cols}"
                      + (f"\nmissing values: {dict(list(nulls.items())[:12])}" if nulls else "")
                      + f"\nfirst rows:\n{sample[:1500]}")
    if not blocks:
        return ""
    return ("<data_preview>\nMeshpool read these files before you started (pandas). Compute every number you report "
            "with code; for large files use DuckDB SQL (`import duckdb; duckdb.sql(\"select ... from 'file.csv'\")`).\n\n"
            + "\n\n".join(blocks) + "\n</data_preview>")


# ---------- repo map: the project's symbols before the first turn (build, refactor, explain) ---------------------------
MAP_EXTS = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")
_JS_SYM = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s*\*?\s*(\w+)\s*(\([^)]*\))|class\s+(\w+)|"
                     r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>|(\w+)\s*(\([^)]*\))\s*\{)", re.M)


def _py_symbols(src: str) -> list[str]:
    import ast
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    out = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(f"def {n.name}({', '.join(a.arg for a in n.args.args)})")
        elif isinstance(n, ast.ClassDef):
            meths = [m.name for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and (not m.name.startswith("_") or m.name == "__init__")]
            out.append(f"class {n.name}" + (f": {', '.join(meths[:12])}" if meths else ""))
    return out


def _js_symbols(src: str) -> list[str]:
    out = []
    for m in _JS_SYM.finditer(src):
        if m.group(1):
            out.append(f"function {m.group(1)}{m.group(2)}")
        elif m.group(3):
            out.append(f"class {m.group(3)}")
        elif m.group(4):
            out.append(f"const {m.group(4)} = (...) =>")
        elif m.group(5) and m.group(5) not in ("if", "for", "while", "switch", "catch", "function", "return"):
            out.append(f"{m.group(5)}{m.group(6)}")
    return out[:30]


def repo_map(ws, text: str = "", limit: int = 3500, max_files: int = 400) -> str:
    """A compact map of the project's files and top-level symbols, files that match the request first."""
    if getattr(ws, "container", None):
        return ""
    files = project_files(ws, MAP_EXTS)[:max_files]
    if len(files) < 3:
        return ""                                     # a tiny project: the model reads it directly
    q = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text or "")}
    rows = []
    for f in files:
        try:
            src = (ws.root / f).read_text(errors="replace")
        except OSError:
            continue
        syms = _py_symbols(src) if f.endswith((".py", ".pyi")) else _js_symbols(src)
        words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", f + " " + " ".join(syms))}
        score = len(q & words) * 3 + (0 if "test" in f.lower() else 1)
        rows.append((-score, f, syms))
    rows.sort()
    out, used = [], 0
    for _, f, syms in rows:
        line = f + (": " + "; ".join(syms[:14]) if syms else "")
        if used + len(line) > limit:
            out.append(f"... and {len(rows) - len(out)} more files")
            break
        out.append(line)
        used += len(line) + 1
    return ("<repo_map>\nMeshpool listed the project's files and top-level symbols (files matching your request first), "
            "so you can go straight to the right file:\n" + "\n".join(out) + "\n</repo_map>")
