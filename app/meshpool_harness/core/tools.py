"""Core tools: strict schemas, validation with precise errors, sandboxed execution in the run's workdir."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def _bash():
    from meshpool_harness.config import find_bash
    return find_bash()

MAX_TOOL_CHARS = 8000
HEAD, TAIL = 3000, 3000
_CAP_LOCK = threading.Lock()

_RO_PROGS = {"cat", "head", "tail", "ls", "find", "grep", "egrep", "fgrep", "rg", "wc", "file", "stat", "which",
             "type", "echo", "printf", "pwd", "uname", "du", "df", "tree", "nl", "sort", "uniq",
             "cut", "tr", "diff", "cmp", "md5sum", "sha256sum", "sha1sum", "xxd", "od", "hexdump", "strings", "jq",
             "readlink", "realpath", "basename", "dirname", "id", "whoami", "date", "ps", "true", "column", "less",
             "awk", "sed", "git", "pip", "pip3", "python", "python3", "node", "npm", "cargo", "go", "java", "gcc",
             "rustc", "ldd", "nproc", "free", "hostname", "lsb_release", "getconf", "test", "["}
_RO_GIT = {"log", "show", "diff", "status", "grep", "ls-files", "blame", "rev-parse", "branch", "remote", "describe",
           "--no-pager", "-C", "shortlog", "cat-file", "ls-tree", "tag"}


def read_only(cmd: str) -> bool:
    """Conservative check that a shell command only reads (safe to run beside others). Unknown means False."""
    import re
    import shlex
    s = re.sub(r"\d?>\s*/dev/null|2>&1|>&2", " ", cmd)
    if re.search(r"[>`]|\$\(|<<|\bxargs\b|-exec\b|-delete\b|\bsudo\b", s):
        return False
    for seg in re.split(r"\|\|?|&&|;|\n", s):
        try:
            w = shlex.split(seg)
        except ValueError:
            return False
        while w and re.match(r"^[A-Za-z_]\w*=", w[0]):             # FOO=bar cmd
            w = w[1:]
        if not w:
            continue
        prog, rest = w[0].rsplit("/", 1)[-1], w[1:]
        if prog not in _RO_PROGS:
            return False
        if prog == "sed" and any(a.startswith("-i") or a == "--in-place" for a in rest):
            return False
        if prog == "awk" and "system" in seg:
            return False
        if prog == "git" and not (rest and rest[0] in _RO_GIT):
            return False
        if prog in ("pip", "pip3") and not (rest and rest[0] in ("show", "list", "freeze", "--version", "-V")):
            return False
        if prog in ("python", "python3", "node", "npm", "cargo", "go", "java", "gcc", "rustc") and not (
                rest and (rest[0] in ("--version", "-V", "version", "-version")
                          or rest[:3] in (["-m", "pip", "show"], ["-m", "pip", "list"]))):
            return False
    return True


_PATCH_CMDS = ("apply_patch", "applypatch")
_HEREDOC = re.compile(r"\A\s*(?:cd\s+(?P<cd>'[^']*'|\"[^\"]*\"|[^\s&;|<>]+)\s*&&\s*)?(?:apply_patch|applypatch)\s*"
                      r"<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)[ \t]*\n(?P<body>.*)\n[ \t]*(?P=tag)[ \t]*\n?\s*\Z", re.S)


def shell_script(argv) -> str:
    """The script a shell tool call runs: argv as the command list Codex sends, or a string."""
    import shlex
    if isinstance(argv, str):
        return argv
    argv = [str(a) for a in argv or []]
    if len(argv) >= 3 and os.path.basename(argv[0]) in ("bash", "sh", "zsh") and argv[1] in ("-c", "-lc"):
        return argv[2]
    return argv[0] if len(argv) == 1 else shlex.join(argv)


def patch_from_shell(argv):
    """(patch text, working folder or None) when a shell call is Codex's apply_patch through the shell, as the Codex
    prompt shows it: ["apply_patch", "<patch>"], or a bash script `[cd dir &&] apply_patch <<'EOF' ... EOF`. Else None."""
    if isinstance(argv, (list, tuple)) and len(argv) == 2 and str(argv[0]) in _PATCH_CMDS:
        return str(argv[1]), None
    script = shell_script(argv)
    m = _HEREDOC.match(script)
    if not m:
        return None
    cd = (m.group("cd") or "").strip("'\"") or None
    return m.group("body"), cd


_BG_ALL: set = set()


def _kill_tree(p) -> None:
    """End a process and everything it started."""
    import signal
    if p.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=0x08000000)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def _kill_all_background() -> None:
    for p in list(_BG_ALL):
        _kill_tree(p)


__import__("atexit").register(_kill_all_background)


class AllStr(str):
    """An `old` text that edit replaces everywhere (replace_all)."""


def _fn(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": req, "additionalProperties": False}}}


S = {"type": "string"}
I = {"type": "integer"}
SCHEMAS = {
    "read_file": _fn("read_file", "Read a file. Text and code come back with line numbers (use start/end, 1-based, "
                     "for large files). PDF, Word, PowerPoint, Excel, CSV, Power BI (.pbix/.pbit), OpenDocument, "
                     "notebooks and email files come back as readable text; images and scanned PDFs are read with "
                     "OCR in many languages. For a PDF, start/end pick pages.",
                     {"path": S, "start": I, "end": I}, ["path"]),
    "grep": _fn("grep", "Search file contents with a regular expression. Returns path:line:text. Options: `context` "
                        "(lines around each match, or `before` and `after`), `output_mode` (content, files_with_matches "
                        "or count), `head_limit` (most results, default 400), `ignore_case`, `multiline` (a pattern may "
                        "span lines).",
                {"pattern": S, "path": S, "glob": S, "context": I, "before": I, "after": I,
                 "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
                 "head_limit": I, "ignore_case": {"type": "boolean"}, "multiline": {"type": "boolean"}}, ["pattern"]),
    "notebook_edit": _fn("notebook_edit", "Edit a Jupyter notebook (.ipynb) by cell. mode replace (change cell `cell`'s "
                         "source), insert (new cell before index `cell`; use the cell count to add at the end) or "
                         "delete. Cells are numbered from 0; read the notebook first to see them. Outputs of a "
                         "replaced code cell are cleared.",
                         {"path": S, "cell": I, "source": S, "mode": {"type": "string", "enum": ["replace", "insert", "delete"]},
                          "cell_type": {"type": "string", "enum": ["code", "markdown"]}}, ["path", "cell"]),
    "glob": _fn("glob", "List files matching a glob pattern relative to the workdir, e.g. '**/*.py'.",
                {"pattern": S}, ["pattern"]),
    "edit": _fn("edit", "Replace exactly one occurrence of `old` with `new` in a file (or every occurrence with "
                        "`replace_all`, to rename something). Fails if `old` is missing, "
                        "not unique, the same as `new`, or the file changed since you last read it. To make several "
                        "changes, put them all in one call: `edits` for one file, `changes` for several files "
                        "(all-or-nothing).",
                {"path": S, "old": S, "new": S, "replace_all": {"type": "boolean"},
                 "edits": {"type": "array", "items": {"type": "object", "properties": {"old": S, "new": S, "replace_all": {"type": "boolean"}},
                                                      "required": ["old", "new"]}},
                 "changes": {"type": "array", "items": {"type": "object", "properties": {
                     "path": S, "old": S, "new": S,
                     "edits": {"type": "array", "items": {"type": "object", "properties": {"old": S, "new": S},
                                                          "required": ["old", "new"]}}}, "required": ["path"]}}},
                []),
    "write_file": _fn("write_file", "Create or overwrite a file. Set append=true to add to the end (for large files written in parts).",
                      {"path": S, "content": S, "append": {"type": "boolean"}}, ["path", "content"]),
    "bash": _fn("bash", "Run a shell command in the workdir. Output is capped; full output is saved to a file.",
                {"command": S, "timeout_s": I}, ["command"]),
    "bash_start": _fn("bash_start", "Start a long-running command in the background (a dev server, a watcher, a slow "
                                    "build) and return at once with an id. Read what it prints with bash_output and end "
                                    "it with bash_stop. It keeps running between turns until stopped or Meshpool closes.",
                      {"command": S, "name": S}, ["command"]),
    "bash_output": _fn("bash_output", "Read the new output of a background command started with bash_start, and say "
                                      "whether it is still running (or its exit code). `wait_s` waits up to that many "
                                      "seconds for output first. Without an id, lists every background command.",
                       {"id": S, "wait_s": I}, []),
    "bash_stop": _fn("bash_stop", "Stop a background command (and everything it started). Without an id, stops all of them.",
                     {"id": S}, []),
    "bash_many": _fn("bash_many", "Run several independent shell commands in ONE call (for example: read 3-5 file "
                                  "ranges, grep several patterns, check versions). Each runs on its own, even if an "
                                  "earlier one fails; you get every output, labelled.",
                     {"commands": {"type": "array", "items": S}, "timeout_s": I}, ["commands"]),
    "probe": _fn("probe", "Run a test or a script and get a diagnosis instead of raw output: the failing tests, the "
                          "exception, and the failing frames in this project's code with their local variable values "
                          "(library frames dropped). Use it to see the bug before you edit, and after an edit to see "
                          "what still fails. Counts as a check for finish.",
                 {"command": S, "timeout_s": I}, ["command"]),
    "trace": _fn("trace", "Run a Python test or script (python file.py, python -m mod, pytest ...) and list the "
                          "project's own functions that ran, in first-call order with call counts, then the output "
                          "tail. Use it to find where to edit before you change anything.",
                 {"command": S, "timeout_s": I}, ["command"]),
    "run_tests": _fn("run_tests", "Run a test or check command. Its exit code counts as evidence for finish.",
                     {"command": S, "timeout_s": I}, ["command"]),
    "finish": _fn("finish", "Finish the task. `evidence` must name checks you actually ran in this session "
                            "(the exact command) that passed after your last edit.",
                  {"answer": S, "evidence": {"type": "array", "items": S}, "artifacts": {"type": "array", "items": S}},
                  ["answer", "evidence"]),
    "todo": _fn("todo", "Write or update your todo list for this task: the concrete requirements and steps, each with "
                        "done true/false. Call it again to update. finish is refused while items are not done.",
                {"items": {"type": "array", "items": {"type": "object", "properties": {"text": S, "done": {"type": "boolean"}},
                                                      "required": ["text", "done"], "additionalProperties": False}}},
                ["items"]),
    "load_skill": _fn("load_skill", "Load detailed instructions for a skill listed in the skill index.",
                      {"name": S}, ["name"]),
    "need_context": _fn("need_context", "Ask the orchestrator for files or information outside your job packet "
                                        "instead of guessing.",
                        {"paths": {"type": "array", "items": S}, "reason": S}, ["reason"]),
}
CORE = ["read_file", "grep", "glob", "edit", "write_file", "bash", "run_tests", "finish"]

# The Codex tool set (engine style "codex", see agent/core.py): shell, apply_patch, update_plan. The shell and plan
# definitions follow Codex's (command as an argv list, workdir, timeout_ms; plan items with a step and a status); the
# apply_patch format text is Codex's own (app/defaults/engines/codex/prompt.md), as a JSON function because the chat
# API has no freeform grammar tools.
CODEX_TOOLS = ["shell", "apply_patch", "update_plan"]
_PATCH_HELP = (
    "Edit files with a patch. `input` is the whole patch: '*** Begin Patch', one or more file operations, "
    "'*** End Patch'. Operations: '*** Add File: <path>' (every following line starts with +), "
    "'*** Delete File: <path>', '*** Update File: <path>' (optionally followed by '*** Move to: <new path>'), then "
    "hunks that start with '@@' (optionally '@@ <a line such as a function header that comes before the change>') "
    "and have lines starting with ' ' (unchanged context), '-' (removed) or '+' (added). Give about 3 lines of "
    "context before and after each change; '*** End of File' marks a change at the end of a file. Paths are relative "
    "to the project folder. The patch is applied to all files or to none.")
SCHEMAS["shell"] = _fn(
    "shell", "Runs a shell command and returns its output. The command is an argv list, for example "
             '["bash", "-lc", "pytest -q"]; a plain string is run with bash. Set `workdir` instead of using cd '
             'when you can. `apply_patch` can also be called through this tool as ["apply_patch", "<patch>"].',
    {"command": {"type": "array", "items": S, "description": "The command to execute"},
     "workdir": {"type": "string", "description": "The working directory to execute the command in"},
     "timeout_ms": {"type": "number", "description": "The timeout for the command in milliseconds"}}, ["command"])
SCHEMAS["apply_patch"] = _fn("apply_patch", _PATCH_HELP, {"input": {"type": "string", "description":
                             "The entire contents of the apply_patch command"}}, ["input"])
SCHEMAS["update_plan"] = _fn(
    "update_plan", "Updates the task plan.\nProvide an optional explanation and a list of plan items, each with a "
                   "step and status.\nAt most one step can be in_progress at a time.",
    {"explanation": S,
     "plan": {"type": "array", "description": "The list of steps", "items": {"type": "object", "properties": {
         "step": S, "status": {"type": "string", "description": "One of: pending, in_progress, completed",
                               "enum": ["pending", "in_progress", "completed"]}},
         "required": ["step", "status"], "additionalProperties": False}}}, ["plan"])


def _delegate_schema():
    from meshpool_harness.agent.delegate import schema
    return schema(12)


SCHEMAS["delegate_task"] = _delegate_schema()


def schemas(names):
    return [SCHEMAS[n] for n in names]


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def clean(text: str) -> str:
    """Tool output can contain binary bytes (e.g. reading a .db file); upstream APIs reject NUL/control chars and
    lone surrogates with HTTP 400, which used to crash the run."""
    return _CTRL.sub("\ufffd", text) if isinstance(text, str) else text


class ToolError(Exception):
    pass


def validate(name: str, raw_args, allowed: list[str]) -> dict:
    if name not in allowed:
        raise ToolError(f"unknown tool '{name}'. Available: {', '.join(allowed)}")
    if isinstance(raw_args, str) and raw_args.strip() in ("", "null"):
        raw_args = "{}"                          # some models/gateways send "" for a no-argument call
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError as e:
        raise ToolError(f"arguments for {name} are not valid JSON ({e.msg} at char {e.pos}). "
                        f"Send a single JSON object.")
    if not isinstance(args, dict):
        raise ToolError(f"arguments for {name} must be a JSON object")
    if (name == "write_file" and isinstance(args.get("content"), (dict, list))
            and str(args.get("path", "")).lower().endswith(".json")):
        args["content"] = json.dumps(args["content"], indent=2, ensure_ascii=False) + "\n"   # models often send JSON as an object
    if name == "shell" and isinstance(args.get("command"), str):        # a plain string: run it with bash
        args["command"] = ["bash", "-lc", args["command"]]
    sch = SCHEMAS[name]["function"]["parameters"]
    for k in sch["required"]:
        if k not in args:
            raise ToolError(f"{name}: missing required argument '{k}'")
    for k, v in args.items():
        if k not in sch["properties"]:
            raise ToolError(f"{name}: unknown argument '{k}'; allowed: {', '.join(sch['properties'])}")
        t = sch["properties"][k]["type"]
        ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
              "number": isinstance(v, (int, float)) and not isinstance(v, bool),
              "boolean": isinstance(v, bool), "array": isinstance(v, list)}[t]
        if not ok:
            raise ToolError(f"{name}: argument '{k}' must be {t}, got {type(v).__name__}")
    return args



_PY_VERSIONS: dict = {}


_BOM = b"\xef\xbb\xbf"


def read_src(p: Path) -> tuple[str, str, bool]:
    """(text, line ending, has BOM) of a text file, read as bytes so nothing is translated on any platform. When
    the file is mostly CRLF the text comes back with plain \\n (what the model reads and writes); write_src puts the
    CRLF back. Undecodable bytes survive the round trip (surrogateescape)."""
    raw = p.read_bytes()
    bom = raw.startswith(_BOM)
    text = raw[3 if bom else 0:].decode("utf-8", "surrogateescape")
    crlf = text.count("\r\n")
    if crlf and crlf * 2 > text.count("\n"):
        return text.replace("\r\n", "\n"), "\r\n", bom
    return text, "\n", bom


def write_src(p: Path, text: str, eol: str = "\n", bom: bool = False, append: bool = False) -> None:
    """Write text with the file's own line ending and BOM, byte for byte (Python's text mode would turn every \\n
    into CRLF on Windows and drop a CRLF file's endings elsewhere)."""
    if eol == "\r\n":
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("utf-8", "surrogateescape")
    if append:
        with p.open("ab") as f:
            f.write(data)
    else:
        p.write_bytes((_BOM if bom else b"") + data)


def shown(text: str) -> str:
    """Text safe to show the model: bytes that are not UTF-8 become U+FFFD."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _newer_pythons(path: Path) -> list:
    """Interpreters newer than this one that could run `path`: its project's virtual environment (the nearest
    .venv / venv above it), then the newest python3.x on PATH. Each is asked its version once."""
    import sys
    cands = []
    for d in [path.parent, *path.parent.parents][:8]:
        for rel in (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe", "venv/Scripts/python.exe"):
            if (d / rel).exists():
                cands.append(str(d / rel))
    try:
        from meshpool_harness.hrouter.packs import newest_python
        np_ = newest_python()
        if np_:
            cands.append(np_)
    except Exception:  # noqa: BLE001
        pass
    out = []
    for exe in dict.fromkeys(cands):
        if exe not in _PY_VERSIONS:
            try:
                r = subprocess.run([exe, "-c", "import sys;print(sys.version_info[0], sys.version_info[1])"],
                                   capture_output=True, text=True, timeout=10)
                _PY_VERSIONS[exe] = tuple(int(x) for x in r.stdout.split()[:2]) if r.returncode == 0 else ()
            except (OSError, subprocess.SubprocessError, ValueError):
                _PY_VERSIONS[exe] = ()
        if _PY_VERSIONS[exe] and _PY_VERSIONS[exe] > sys.version_info[:2]:
            out.append(exe)
    return out


def _parses_with_newer_python(path: Path, text: str) -> bool:
    for exe in _newer_pythons(path):
        try:
            r = subprocess.run([exe, "-c", "import sys; compile(sys.stdin.read(), sys.argv[1], 'exec', dont_inherit=True)",
                                str(path)], input=text, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False

class Workspace:
    """Executes tools inside workdir, tracks reads/writes/commands for guards."""

    def __init__(self, workdir: str, run_dir: str, skills: dict[str, str] | None = None,
                 container: str | None = None, container_dir: str | None = None, mounted: bool = True):
        """container: run bash/run_tests natively inside this Docker container (cwd container_dir), so the model
        never writes nested `docker exec ... bash -lc '...'` quoting. mounted=True: workdir is bind-mounted at
        container_dir (file tools use the host copy); mounted=False: file tools read/write inside the container."""
        self.container, self.cdir, self.mounted = container, container_dir, mounted
        self.root = Path(workdir).resolve()
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.skills = skills or {}
        self.seen: dict[str, str] = {}        # path -> content hash the agent last saw/wrote
        self.original: dict[str, str] = {}    # path -> content hash when the agent first saw it (revert check)
        self.commands: list[dict] = []        # executed commands with exit codes and edit counter
        self.edits = 0
        self.written: set[str] = set()
        self.created: set[str] = set()        # files the agent created (new tests are allowed)
        self.turn_files: set[str] = set()     # files changed in the current turn (the review receipt)
        self._n = 0

    # -- helpers
    def _path(self, p: str, write=False) -> Path:
        if self.container and self.mounted and self.cdir and os.path.isabs(p) and (p == self.cdir or p.startswith(self.cdir.rstrip("/") + "/")):
            p = str(self.root / os.path.relpath(p, self.cdir))     # container path -> host copy
        q = (self.root / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
        if write and self.root not in q.parents and q != self.root:
            raise ToolError(f"writes must stay inside the workdir {self.root}; got {q}")
        if write and ".git" in q.relative_to(self.root).parts:
            # git runs settings and hooks from here (core.fsmonitor, hooks/): a write there can run any command
            raise ToolError("agents cannot change files inside .git; use git commands instead")
        return q

    @staticmethod
    def _hash(text: str) -> str:
        # line endings and a BOM do not count: read_file/edit see \\n text, apply_patch sees the raw text
        return hashlib.sha1(text.lstrip("\ufeff").replace("\r\n", "\n").encode("utf-8", "replace")).hexdigest()

    def _cap(self, text: str, label: str) -> str:
        text = clean(text)
        if len(text) <= MAX_TOOL_CHARS:
            return text
        with _CAP_LOCK:                          # bash_many may run commands in threads (MH_PARALLEL_MANY)
            self._n += 1
            n = self._n
        f = self.run_dir / f"out_{n:03d}_{label}.txt"
        try:                                     # the task may delete .run (e.g. git clean): recreate it
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8", errors="replace")
        except OSError:
            return text[:HEAD] + f"\n... [{len(text) - HEAD - TAIL} chars truncated] ...\n" + text[-TAIL:]
        return (text[:HEAD] + f"\n... [{len(text) - HEAD - TAIL} chars truncated; full output in {f}] ...\n"
                + text[-TAIL:])

    # -- container (unmounted) file access
    def _cpath(self, p: str) -> str:
        return p if p.startswith("/") else f"{self.cdir.rstrip('/')}/{p}"

    def _cexec(self, args: list[str], input_text: str | None = None, timeout=120):
        cmd = ["docker", "exec"] + (["-i"] if input_text is not None else []) + ["-w", self.cdir, self.container, *args]
        return subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout, errors="replace")

    def _cread(self, path: str) -> str:
        r = self._cexec(["cat", self._cpath(path)])
        if r.returncode:
            raise ToolError(f"file not found or unreadable in container: {path}")
        return r.stdout

    def _cwrite(self, path: str, text: str, append=False):
        cp = self._cpath(path)
        r = self._cexec(["sh", "-c", f'mkdir -p "$(dirname "{cp}")" && cat {">>" if append else ">"} "{cp}"'], input_text=text)
        if r.returncode:
            raise ToolError(f"could not write {path} in container: {r.stderr[-200:]}")

    # -- tools
    def read_file(self, path, start=None, end=None):
        if self.container and not self.mounted:
            text = self._cread(path)
            self.seen[self._cpath(path)] = self._hash(text)
            self.original.setdefault(self._cpath(path), self.seen[self._cpath(path)])
            lines = text.splitlines()
            s, e = max(1, start or 1), min(len(lines), end or len(lines))
            body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(s, e + 1))
            return self._cap(f"{path} lines {s}-{e} of {len(lines)}\n{body}", "read")
        p = self._path(path)
        if not p.exists():
            raise ToolError(f"file not found: {path}")
        if p.is_dir():
            raise ToolError(f"{path} is a directory; use glob")
        from meshpool_harness import docread
        if docread.is_image(p):
            return self._cap(docread.extract(p), "read")
        if docread.is_document(p) and p.suffix.lower() not in (".html", ".htm", ".csv", ".tsv", ".ipynb"):
            pages = f"{start or 1}-{end or ''}" if (start or end) else None
            return self._cap(docread.extract(p, pages=pages), "read")
        text = read_src(p)[0]
        self.seen[str(p)] = self._hash(text)
        self.original.setdefault(str(p), self.seen[str(p)])
        lines = shown(text).splitlines()
        s = max(1, start or 1)
        e = min(len(lines), end or len(lines))
        if s > len(lines) and lines:
            return f"{path}: file has {len(lines)} lines (start={s} is past the end)"
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(s, e + 1))
        return self._cap(f"{path} lines {s}-{e} of {len(lines)}\n{body}", "read")

    def _inside(self, p, roots=None) -> bool:
        """p resolves inside the project (or one of roots)."""
        try:
            q = Path(p).resolve()
            return any(q.is_relative_to(Path(r).resolve()) for r in (roots or [self.root]))
        except (OSError, ValueError):
            return False

    def grep(self, pattern, path=".", glob=None, inside_only=False, context=0, before=0, after=0,
             output_mode="content", head_limit=400, ignore_case=False, multiline=False):
        """inside_only: skip files that resolve outside the project (a symlink), before anything is read or saved;
        a list of folders instead of True allows those folders as well. context/before/after: lines around a match.
        output_mode: content, files_with_matches or count. head_limit: most results returned."""
        try:
            head = max(1, min(int(head_limit or 400), 2000))
            before = max(0, min(int(before or context or 0), 20))
            after = max(0, min(int(after or context or 0), 20))
        except (TypeError, ValueError):
            raise ToolError("context, before, after and head_limit must be whole numbers")
        if output_mode not in ("content", "files_with_matches", "count"):
            raise ToolError("output_mode must be content, files_with_matches or count")
        if self.container and not self.mounted:
            inc = f"--include='{glob}'" if glob else ""
            ctx = f"-B{before} -A{after}" if (before or after) and output_mode == "content" else ""
            mode = {"files_with_matches": "-l", "count": "-c"}.get(output_mode, "")
            case = "-i" if ignore_case else ""
            out, _ = self._run(f"grep -rnE {case} {mode} {ctx} {inc} --exclude-dir=.git --exclude-dir=node_modules -e "
                               f"{json.dumps(pattern)} {json.dumps(path)} | head -{head}", 60, "grep", record=False)
            return out
        try:
            rx = re.compile(pattern, (re.I if ignore_case else 0) | (re.M | re.S if multiline else 0))
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        base = self._path(path)
        if not base.exists():
            raise ToolError(f"path not found: {path}")
        files = [base] if base.is_file() else [f for f in base.rglob("*") if f.is_file()]
        out, matched, truncated = [], 0, False
        for f in files:
            rel = f.relative_to(self.root) if self.root in f.parents else f
            if any(part in (".git", "node_modules", "__pycache__", ".venv") for part in f.parts):
                continue
            if glob and not fnmatch.fnmatch(str(rel), glob) and not fnmatch.fnmatch(f.name, glob):
                continue
            if inside_only and not self._inside(f, inside_only if isinstance(inside_only, (list, tuple)) else None):
                continue
            try:
                text = f.read_text(errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            lines = text.splitlines()
            hits: list[int] = []                                   # 1-based line numbers that start a match
            if multiline:
                for m in rx.finditer(text):
                    hits.append(text.count("\n", 0, m.start()) + 1)
            else:
                hits = [i for i, line in enumerate(lines, 1) if rx.search(line)]
            if not hits:
                continue
            matched += 1
            if output_mode == "files_with_matches":
                out.append(str(rel))
            elif output_mode == "count":
                out.append(f"{rel}:{len(hits)}")
            else:
                shown: set[int] = set()
                last = 0
                for h in hits:
                    lo, hi = max(1, h - before), min(len(lines), h + after)
                    if (before or after) and last and lo > last + 1:
                        out.append("--")
                    for n in range(lo, hi + 1):
                        if n in shown:
                            continue
                        shown.add(n)
                        out.append(f"{rel}{':' if n in hits else '-'}{n}{':' if n in hits else '-'}{lines[n - 1][:300]}")
                    last = max(last, hi)
            if len(out) >= head:
                truncated = True
                out = out[:head]
                break
        if truncated:
            out.append("... more results truncated; narrow the pattern or raise head_limit")
        return self._cap("\n".join(out) or "no matches", "grep")

    def glob(self, pattern, inside_only=False):
        """inside_only: leave out what resolves outside the project (through a symlinked folder or file)."""
        if self.container and not self.mounted:
            out, _ = self._run(f"find . -path ./.git -prune -o -path './{pattern.lstrip('./')}' -print 2>/dev/null | head -2000"
                               if "/" in pattern else f"find . -name {json.dumps(pattern.split('/')[-1])} -not -path './.git/*' | head -2000",
                               60, "glob", record=False)
            return out
        if os.path.isabs(pattern) or re.match(r"^[A-Za-z]:", pattern) or ".." in Path(pattern).parts:
            raise ToolError("glob patterns are relative to the working directory (no absolute paths or '..')")
        try:
            res = sorted(str(p.relative_to(self.root)) for p in self.root.glob(pattern)
                         if ".git" not in p.parts and "node_modules" not in p.parts
                         and (not inside_only or self._inside(p)))
        except (NotImplementedError, ValueError) as e:
            raise ToolError(f"invalid glob pattern: {e}")
        return self._cap("\n".join(res[:2000]) or "no files", "glob")

    @staticmethod
    def _syntax_error(path: Path, text: str) -> str | None:
        """Lint on edit: the exact place a Python, JSON or JavaScript file stops parsing, shown the way the compiler
        shows it (line, column, the line itself and a caret), so the agent fixes it in the same step instead of
        finding out when the code runs. None when the file parses or the language is not checked."""
        def where(line: int, col: int, msg: str) -> str:
            lines = text.splitlines()
            src = lines[line - 1] if 0 < line <= len(lines) else ""
            caret = " " * max(0, min(col, len(src) + 1) - 1) + "^"
            return f"line {line}, column {col}: {msg}\n    {src}\n    {caret}"
        suf = path.suffix.lower()
        if suf in (".py", ".pyw"):
            try:
                compile(text, str(path), "exec", dont_inherit=True)
                return None
            except SyntaxError as e:     # Python's own message: "'(' was never closed", "invalid syntax. Perhaps you forgot a comma?"
                # MeshPool's own Python (3.12 in the app) does not know newer syntax (3.14: `except A, B:`, t-strings).
                # The code is judged by the newest Python that would run it: the project's venv, else the newest
                # python3.x on this computer. Without a newer one the code cannot run here either, so the error stands.
                if _parses_with_newer_python(path, text):
                    return None
                return where(e.lineno or 0, e.offset or 1, e.msg)
            except (ValueError, TypeError):
                return None
        if suf == ".json":
            try:
                json.loads(text) if text.strip() else None
                return None
            except json.JSONDecodeError as e:
                return where(e.lineno, e.colno, e.msg)
        if suf in (".js", ".mjs", ".cjs"):
            node = shutil.which("node")
            if not node:
                return None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=suf, delete=False, encoding="utf-8", errors="surrogateescape") as f:
                    f.write(text)
                r = subprocess.run([node, "--check", f.name], capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                return None
            finally:
                try:
                    os.unlink(f.name)
                except (OSError, NameError):
                    pass
            if r.returncode == 0:
                return None
            m = re.search(r":(\d+)\n", r.stderr)
            msg = next((x for x in r.stderr.splitlines() if "Error" in x), "syntax error").strip()
            return where(int(m.group(1)), 1, msg) if m else msg
        return None

    NOOP_EDIT = ("edit rejected: `new` is identical to `old`, so nothing would change and {path} is unchanged. Put "
                 "the changed text in `new` (keep `old` exactly as it is in the file).")

    _LINENO = re.compile(r"^ *\d+(?: {2}|$)")    # read_file prints f"{i:>5}  {line}"

    @classmethod
    def _strip_line_numbers(cls, text: str) -> str | None:
        """`text` without read_file's line-number prefixes when every line has one, else None."""
        lines = text.split("\n")
        body = lines[:-1] if len(lines) > 1 and lines[-1] == "" else lines
        if not body or not all(cls._LINENO.match(ln) for ln in body):
            return None
        out = [cls._LINENO.sub("", ln, count=1) for ln in body]
        return "\n".join(out) + ("\n" if len(body) != len(lines) else "")

    @staticmethod
    def _numbered(lines: list[str], a: int, b: int) -> str:
        return "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(a, b + 1))

    @staticmethod
    def _closest_region(lines: list[str], old: str) -> str:
        """About 8 lines of the file most like `old`, numbered the way read_file numbers them ('' when nothing is close)."""
        import difflib
        k = max(1, len(old.splitlines()))
        if not lines or len(lines) > 20000:
            return ""
        target = "\n".join(ln.rstrip() for ln in old.splitlines())
        best, best_i = 0.0, 0
        sm = difflib.SequenceMatcher(autojunk=False)
        sm.set_seq2(target)
        for i in range(max(1, len(lines) - k + 1)):
            sm.set_seq1("\n".join(ln.rstrip() for ln in lines[i:i + k]))
            if sm.real_quick_ratio() <= best or sm.quick_ratio() <= best:
                continue
            r = sm.ratio()
            if r > best:
                best, best_i = r, i
        if best < 0.2:
            return ""
        a = best_i + 1
        b = min(len(lines), a + 7)
        return f"\nClosest text in the file (lines {a}-{b}):\n{Workspace._numbered(lines, a, b)}"

    @staticmethod
    def _indent_of(line: str) -> int:
        e = line.expandtabs(4)
        return len(e) - len(e.lstrip(" "))

    @classmethod
    def _fuzzy_matches(cls, lines: list[str], old: str, level: int):
        """Start indexes (and indentation offset) of regions of `lines` equal to `old` after normalising: level 1 =
        trailing whitespace; level 2 = also tabs as 4 spaces and a uniform indentation difference."""
        olines = [ln.rstrip() for ln in old.splitlines()]
        k = len(olines)
        if k == 0 or not any(olines):
            return []
        if level == 1:
            return [(i, 0) for i in range(len(lines) - k + 1)
                    if all(lines[i + j].rstrip() == olines[j] for j in range(k))]
        o_ind = min((cls._indent_of(x) for x in olines if x.strip()), default=0)
        o_norm = [x.expandtabs(4)[o_ind:].rstrip() if x.strip() else "" for x in olines]
        out = []
        for i in range(len(lines) - k + 1):
            w = lines[i:i + k]
            if any(bool(w[j].strip()) != bool(olines[j].strip()) for j in range(k)):
                continue
            w_ind = min((cls._indent_of(x) for x in w if x.strip()), default=0)
            if all((w[j].expandtabs(4)[w_ind:].rstrip() if w[j].strip() else "") == o_norm[j] for j in range(k)):
                out.append((i, w_ind - o_ind))
        return out

    @classmethod
    def _plan_edit(cls, text: str, old: str, new: str, path: str):
        """The file text after replacing `old` with `new`, and a note ('' for an exact match). Raises ToolError with
        guidance when `old` is missing or ambiguous."""
        if text.count(old) == 0:
            stripped = cls._strip_line_numbers(old)
            if stripped is not None and stripped:
                old = stripped
                n2 = cls._strip_line_numbers(new)
                if n2 is not None:
                    new = n2
        n = text.count(old)
        if n == 1:
            return text.replace(old, new, 1), ""
        if n > 1:
            starts = [m.start() for m in re.finditer(re.escape(old), text)]
            at = ", ".join(str(text.count("\n", 0, s) + 1) for s in starts[:10])
            more = f" (and {len(starts) - 10} more)" if len(starts) > 10 else ""
            raise ToolError(f"`old` text occurs {n} times in {path}, starting at lines {at}{more}; include more "
                            f"surrounding lines to make it unique")
        keep = text.splitlines(keepends=True)
        lines = [ln.rstrip("\r\n") for ln in keep]
        for level in (1, 2):
            hits = cls._fuzzy_matches(lines, old, level)
            if len(hits) == 1:
                i, off = hits[0]
                k = len(old.splitlines())
                nl = [ln.rstrip() for ln in new.splitlines()]
                if level == 2:
                    nl = [" " * max(0, cls._indent_of(x) + off) + x.lstrip() if x.strip() else "" for x in nl]
                eol = "\n"
                last = keep[i + k - 1]
                if last.endswith("\r\n"):
                    eol = "\r\n"
                tail = eol if last.endswith(("\n", "\r")) and nl else ""
                repl = eol.join(nl) + tail
                return "".join(keep[:i]) + repl + "".join(keep[i + k:]), " (matched after normalising whitespace)"
            if len(hits) > 1:
                at = ", ".join(str(i + 1) for i, _ in hits[:10])
                raise ToolError(f"`old` text matches {len(hits)} places in {path} after normalising whitespace, "
                                f"starting at lines {at}; include more surrounding lines to make it unique")
        raise ToolError(f"`old` text not found in {path}. Copy it exactly from read_file output "
                        f"(without the line-number prefix)." + cls._closest_region(lines, old))

    @staticmethod
    def _edit_items(spec: dict, where: str) -> list[tuple[str, str]]:
        """The (old, new) pairs of one edit spec: its own old/new first, then its `edits` list, in order. An `old`
        with replace_all set comes back as an AllStr, which _plan_many replaces everywhere."""
        items = []
        if spec.get("old") is not None or spec.get("new") is not None:
            o = spec.get("old")
            items.append((AllStr(o) if spec.get("replace_all") and isinstance(o, str) else o, spec.get("new")))
        extra = spec.get("edits")
        if extra is not None:
            if not isinstance(extra, list):
                raise ToolError(f"`edits` for {where} must be a list of {{old, new}} objects")
            for j, it in enumerate(extra, 1):
                if not isinstance(it, dict):
                    raise ToolError(f"edit {j} for {where} must be an object with `old` and `new`")
                o = it.get("old")
                items.append((AllStr(o) if it.get("replace_all") and isinstance(o, str) else o, it.get("new")))
        if not items:
            raise ToolError(f"nothing to edit in {where}: give `old` and `new`, or an `edits` list")
        for j, (o, n) in enumerate(items, 1):
            if not isinstance(o, str) or not isinstance(n, str) or o == "":
                raise ToolError(f"edit {j} of {len(items)} for {where} needs a non-empty string `old` and a string `new`")
        return items

    def _plan_many(self, text: str, items: list[tuple[str, str]], path: str, unread: bool):
        """All `items` applied in order to `text`: (final text, notes). Each must match once in the text as it stands
        after the previous ones. Raises ToolError naming the failing item; nothing is written here."""
        notes, n = [], len(items)
        for j, (old, new) in enumerate(items, 1):
            tag = f"edit {j} of {n} in {path}: " if n > 1 else ""
            if old == new:
                raise ToolError(tag + self.NOOP_EDIT.format(path=path))
            if unread and (text.count(old) != 1 or isinstance(old, AllStr)):
                raise ToolError(f"read {path} before editing it (an unread file can only be edited when `old` "
                                f"matches exactly once)" + (f" [{tag.strip(' :')}]" if tag else ""))
            if isinstance(old, AllStr) and text.count(old) > 1:        # replace_all: every occurrence, counted
                notes.append(f"replaced {text.count(old)} occurrences")
                text = text.replace(str(old), new)
                continue
            try:
                text, note = self._plan_edit(text, str(old), new, path)
            except ToolError as e:
                raise ToolError(tag + str(e) + (f"\nNothing was written; {path} is unchanged." if n > 1 else "")) from None
            notes.append(note)
        return text, notes

    def _load(self, path: str):
        """(key, text, path object|None, (eol, bom)) for a file about to be edited, host or container."""
        if self.container and not self.mounted:
            return self._cpath(path), self._cread(path), None, ("\n", False)
        p = self._path(path, write=True)
        if not p.exists():
            raise ToolError(f"file not found: {path}")
        text, eol, bom = read_src(p)
        return str(p), text, p, (eol, bom)

    def edit(self, path=None, old=None, new=None, edits=None, changes=None, replace_all=False):
        if changes is not None:
            if not isinstance(changes, list) or not changes or not all(isinstance(c, dict) for c in changes):
                raise ToolError("`changes` must be a non-empty list of {path, old, new} or {path, edits} objects")
            specs = list(changes)
            if path is not None:
                specs.insert(0, {"path": path, "old": old, "new": new, "edits": edits})
        elif path is None:
            raise ToolError("edit needs `path` with `old` and `new` (or `edits`), or `changes` for several files")
        else:
            specs = [{"path": path, "old": old, "new": new, "edits": edits, "replace_all": bool(replace_all)}]
        grouped: dict[str, list] = {}
        for sp in specs:
            pth = sp.get("path")
            if not isinstance(pth, str) or not pth:
                raise ToolError("every change needs a `path`")
            grouped.setdefault(pth, []).extend(self._edit_items(sp, pth))
        # validate everything first
        plans = []     # (path, key, p, before, after, notes, n_items)
        for pth, items in grouped.items():
            key, text, p, fmt = self._load(pth)
            unread = key not in self.seen
            if not unread and self.seen[key] != self._hash(text):
                raise ToolError(f"{pth} changed since you last read it (stale edit). Read it again, then edit.")
            after, notes = self._plan_many(text, items, pth, unread)
            if p is not None:
                bad = self._syntax_error(p, after) if not self._syntax_error(p, text) else None
                if bad:
                    raise ToolError(f"edit rejected: it would make {pth} stop parsing. Nothing was written; "
                                    f"{pth} is unchanged. Fix `new` at the marked place and retry.\n{bad}")
            plans.append((pth, key, p, text, after, notes, len(items), fmt))
        # write, restoring what was written if one write fails
        done = []
        try:
            for pth, key, p, before, after, _n, _c, fmt in plans:
                done.append((pth, p, before, fmt))
                if p is None:
                    self._cwrite(pth, after)
                else:
                    write_src(p, after, *fmt)
        except Exception as e:  # noqa: BLE001
            for pth, p, before, fmt in done:
                try:
                    self._cwrite(pth, before) if p is None else write_src(p, before, *fmt)
                except Exception:  # noqa: BLE001
                    pass
            raise ToolError(f"edit failed while writing ({e}); files already written were restored") from None
        for pth, key, p, before, after, _n, _c, _f in plans:
            self.seen[key] = self._hash(after)
            self.edits += 1
            self.written.add(key)
            self.turn_files.add(key)
        if len(plans) == 1:
            pth, _k, _p, _b, _a, notes, count, _f = plans[0]
            if count == 1 and edits is None:
                return f"edited {pth}{notes[0]}"
            fuzzy = sum(1 for x in notes if x)
            return f"edited {pth}: {count} edits applied" + (f" ({fuzzy} matched after normalising whitespace)" if fuzzy else "")
        total = sum(pl[6] for pl in plans)
        lines = [f"edited {len(plans)} files: {total} edits applied"]
        lines += [f"- {pl[0]}: {pl[6]} edit{'s' if pl[6] != 1 else ''}" for pl in plans]
        return "\n".join(lines)

    def notebook_edit(self, path=None, cell=None, source="", mode="replace", cell_type="code"):
        if not path or cell is None:
            raise ToolError("notebook_edit needs `path` and `cell`")
        p = self._path(path, write=True)
        if not p.is_file():
            raise ToolError(f"{path} does not exist")
        try:
            nb = json.loads(read_src(p)[0])
            cells = nb["cells"]
        except (ValueError, KeyError):
            raise ToolError(f"{path} is not a notebook (.ipynb JSON with cells)")
        n, i = len(cells), int(cell)
        lines = (source or "").splitlines(keepends=True)
        if mode == "insert":
            if not 0 <= i <= n:
                raise ToolError(f"cell {i} is out of range: the notebook has {n} cells (0 to {n} for insert)")
            new = {"cell_type": cell_type, "metadata": {}, "source": lines}
            if cell_type == "code":
                new.update({"execution_count": None, "outputs": []})
            cells.insert(i, new)
            did = f"inserted a {cell_type} cell at {i}"
        else:
            if not 0 <= i < n:
                raise ToolError(f"cell {i} is out of range: the notebook has {n} cells (0 to {n - 1})")
            if mode == "delete":
                cells.pop(i)
                did = f"deleted cell {i}"
            elif mode == "replace":
                cells[i]["source"] = lines
                if cells[i].get("cell_type") == "code":
                    cells[i]["outputs"], cells[i]["execution_count"] = [], None
                did = f"replaced cell {i}"
            else:
                raise ToolError("mode must be replace, insert or delete")
        self.write_file(path, json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
        return f"{did} in {path}; it now has {len(cells)} cells"

    def write_file(self, path, content, append=False):
        if self.container and not self.mounted:
            if subprocess.run(["docker", "exec", self.container, "test", "-e", self._cpath(path)]).returncode != 0:
                self.created.add(self._cpath(path))
            elif self._cpath(path) not in self.original:
                self.original[self._cpath(path)] = self._hash(self._cread(path))
            self._cwrite(path, content, append)
            self.seen[self._cpath(path)] = self._hash(self._cread(path))
            self.edits += 1
            self.written.add(self._cpath(path))
            self.turn_files.add(self._cpath(path))
            return f"wrote {path} ({len(content)} chars{', appended' if append else ''}) in the container"
        p = self._path(path, write=True)
        fmt = ("\n", False)                           # a new file: plain \\n endings on every platform
        if not p.exists():
            self.created.add(str(p))
        elif p.is_file():
            text, eol, bom = read_src(p)                # an existing file keeps its line endings and BOM
            fmt = (eol, bom)
            if str(p) not in self.original:
                self.original[str(p)] = self._hash(text)
        p.parent.mkdir(parents=True, exist_ok=True)
        if append and p.exists():
            write_src(p, content, fmt[0], append=True)
        else:
            write_src(p, content.lstrip("\ufeff") if fmt[1] else content, *fmt)
        after = read_src(p)[0]
        self.seen[str(p)] = self._hash(after)
        self.edits += 1
        self.written.add(str(p))
        self.turn_files.add(str(p))
        bad = self._syntax_error(p, after) if p.is_file() else None
        return f"wrote {path} ({len(content)} chars{', appended' if append else ''})" + (
            f"\nIt does not parse yet; fix it next:\n{bad}" if bad else "")

    should_stop = None          # set by the agent: a callable that says the user pressed Stop

    def _proc(self, args, env, timeout_s, shell=False, encoding=None, cwd=None):
        """Run a command the user can stop at any moment: the whole process tree is killed on Stop or timeout."""
        import signal
        kw = {"creationflags": 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
        p = subprocess.Popen(args, shell=shell, cwd=cwd or self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True, errors="replace", encoding=encoding, **kw)
        box: dict = {}

        def pump():
            box["out"], box["err"] = p.communicate()
        th = threading.Thread(target=pump, daemon=True)
        th.start()
        end = time.time() + timeout_s
        why = None
        while th.is_alive():
            th.join(0.2)
            if th.is_alive() and self.should_stop and self.should_stop():
                why = "stopped"
            elif th.is_alive() and time.time() > end:
                why = "timeout"
            if why:
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                                       creationflags=0x08000000)
                    else:
                        os.killpg(p.pid, signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    p.kill()
                th.join(5)
                break
        out, err = box.get("out") or "", box.get("err") or ""
        if why == "timeout":
            raise subprocess.TimeoutExpired(args, timeout_s, output=out)
        rc = 130 if why == "stopped" else p.returncode
        if why == "stopped":
            err = (err + "\n[stopped by the user]").lstrip()
        return subprocess.CompletedProcess(args, rc, out, err)

    def _run(self, command, timeout_s, kind, record=True, raw=False, ctx=True, cwd=None):
        cap = 1800 if os.environ.get("MH_MODE") == "terminal" else 900   # terminal tasks: builds, training runs
        timeout_s = max(1, min(int(timeout_s or (300 if kind == 'check' else 180)), cap))
        t0 = time.time()
        try:
            from meshpool_harness import config as _C
            if _C.packaged():                                       # the user's own tools, not Meshpool's Python
                env = _C.user_env()
            else:
                env = _C.scrub_env(dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))  # stale .pyc after same-size edits hides fixes
                proj = str(Path(__file__).resolve().parents[2])      # engine helper libraries importable anywhere
                env["PYTHONPATH"] = proj + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            if self.container and not command.lstrip().startswith("docker "):
                cdir = self.cdir if not cwd else self._cpath(os.path.relpath(cwd, self.root).replace(os.sep, "/"))
                r = subprocess.run(["docker", "exec", "-w", cdir, self.container, "bash", "-lc", command],
                                   capture_output=True, text=True, timeout=timeout_s, errors="replace")
            else:
                env.setdefault("PYTHONUTF8", "1")
                bash = _bash()
                if bash and os.name == "nt":                     # Git Bash: keep the working folder
                    env["CHERE_INVOKING"] = "1"
                    r = self._proc([bash, "-c", command], env, timeout_s, encoding="utf-8", cwd=cwd)
                elif bash:
                    from meshpool_harness.core import sandbox as _SB
                    r = self._proc(_SB.wrap([bash, "-c", command], self.root, cwd=cwd), env, timeout_s, cwd=cwd)
                elif os.name == "nt":                            # no Git Bash: PowerShell, not cmd.exe
                    r = self._proc(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                    "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command],
                                   env, timeout_s, encoding="utf-8", cwd=cwd)
                else:
                    r = self._proc(command, env, timeout_s, shell=True, cwd=cwd)
            code, out = r.returncode, (r.stdout + ("\n" + r.stderr if r.stderr else ""))
        except subprocess.TimeoutExpired as e:
            code = 124
            out = ((e.stdout or "") if isinstance(e.stdout, str) else "") + f"\n[timed out after {timeout_s}s]"
        if not record:
            return self._cap(out or "no matches", kind), code
        self.commands.append({"cmd": command.strip(), "code": code, "kind": kind, "after_edit": self.edits,
                              "secs": round(time.time() - t0, 1), "tail": out[-600:]})
        try:                                     # full outputs: evidence for number tracing; the task may have
            self.run_dir.mkdir(parents=True, exist_ok=True)   # deleted .run (git clean), so recreate it
            with (self.run_dir / "commands.log").open("a", encoding="utf-8", errors="replace") as f:
                f.write(f"\n$ {command.strip()}\n[exit {code}]\n{out[:200000]}\n")
        except OSError:
            pass
        if raw:                                  # probe/trace post-process the full text themselves
            return f"[exit {code}]\n{out}", code
        return self._cap(f"[exit {code}]\n{out}", kind) + (self._where(out, code, command) if ctx else ""), code

    def _where(self, out, code, command) -> str:
        """MH_ERROR_CONTEXT: excerpts of the project code a failing command points to (core/errctx.py)."""
        from . import errctx
        on = errctx.enabled() or getattr(self, "error_context", False)   # the routed code harnesses turn it on
        return errctx.context(out, self, command) if code not in (0, 124) and on else ""

    def bash(self, command, timeout_s=None):
        return self._run(command, timeout_s, "bash")[0]

    # ---- background commands: started now, read and stopped later (a dev server, a watcher)
    MAX_BG = 6
    BG_KEEP = 400_000                      # characters of output kept per command

    def bash_start(self, command, name=None):
        if self.container and not self.mounted:
            raise ToolError("background commands run on this computer, not inside a container")
        bg = self.__dict__.setdefault("_bg", {})
        if sum(1 for e in bg.values() if e["p"].poll() is None) >= self.MAX_BG:
            raise ToolError(f"{self.MAX_BG} background commands are already running; stop one with bash_stop first")
        from meshpool_harness import config as _C
        if _C.packaged():
            env = _C.user_env()
        else:
            env = _C.scrub_env(dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            proj = str(Path(__file__).resolve().parents[2])
            env["PYTHONPATH"] = proj + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.setdefault("PYTHONUTF8", "1")
        env["PYTHONUNBUFFERED"] = "1"
        bash = _bash()
        kw = {"creationflags": 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
        if bash:
            args, shell = [bash, "-c", command], False
            if os.name != "nt":
                from meshpool_harness.core import sandbox as _SB
                args = _SB.wrap(args, self.root)
        elif os.name == "nt":
            args, shell = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command], False
        else:
            args, shell = command, True
        p = subprocess.Popen(args, shell=shell, cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, errors="replace", **kw)
        _BG_ALL.add(p)
        n = self.__dict__["_bg_n"] = self.__dict__.get("_bg_n", 0) + 1
        bid = f"bg{n}"
        entry = {"p": p, "command": command.strip(), "name": (name or "").strip()[:40], "text": [], "size": 0,
                 "read": 0, "t0": time.time(), "lock": threading.Lock()}
        bg[bid] = entry

        def pump():
            for line in p.stdout:
                with entry["lock"]:
                    entry["text"].append(line)
                    entry["size"] += len(line)
                    while entry["size"] > self.BG_KEEP and len(entry["text"]) > 1:      # keep the newest output
                        entry["size"] -= len(entry["text"].pop(0))
                        entry["read"] = max(0, entry["read"] - 1)
        threading.Thread(target=pump, daemon=True).start()
        time.sleep(0.4)                                            # a command that fails at once says so now
        return (f"started {bid}{' (' + entry['name'] + ')' if entry['name'] else ''}, pid {p.pid}. "
                f"Read its output with bash_output id={bid}; stop it with bash_stop id={bid}.\n" + self._bg_read(bid, entry))

    def _bg_read(self, bid, entry):
        with entry["lock"]:
            new = "".join(entry["text"][entry["read"]:])
            entry["read"] = len(entry["text"])
        rc = entry["p"].poll()
        state = "running" if rc is None else f"exited with code {rc}"
        return f"[{bid} {state}]\n" + (self._cap(new, "bash") if new else "(no new output)")

    def bash_output(self, id=None, wait_s=0):
        bg = self.__dict__.get("_bg", {})
        if not id:
            if not bg:
                return "no background commands"
            return "\n".join(f"{b}: {'running' if e['p'].poll() is None else 'exited ' + str(e['p'].poll())}  "
                             f"{e['name'] or e['command'][:60]}" for b, e in bg.items())
        e = bg.get(id)
        if e is None:
            raise ToolError(f"no background command {id}. Running: {', '.join(bg) or 'none'}")
        try:
            wait = max(0, min(int(wait_s or 0), 60))
        except (TypeError, ValueError):
            wait = 0
        end = time.time() + wait
        while wait and time.time() < end and e["p"].poll() is None and e["read"] >= len(e["text"]):
            time.sleep(0.2)
        return self._bg_read(id, e)

    def bash_stop(self, id=None):
        bg = self.__dict__.get("_bg", {})
        ids = [id] if id else list(bg)
        if id and id not in bg:
            raise ToolError(f"no background command {id}. Running: {', '.join(bg) or 'none'}")
        stopped = []
        for b in ids:
            _kill_tree(bg[b]["p"])
            stopped.append(b)
        return ("stopped " + ", ".join(stopped)) if stopped else "no background commands"

    def bash_many(self, commands, timeout_s=None):
        """Several independent commands in one tool call: one turn instead of many (Terminus 2 / KIRA style).
        MH_PARALLEL_MANY=1: when every command is read-only (see read_only), run them at the same time; the turn then
        takes as long as the slowest command instead of the sum. Output order and format are unchanged."""
        cmds = [c for c in commands if isinstance(c, str) and c.strip()][:8]

        def one(cmd):
            if self.should_stop and self.should_stop():
                return f"$ {cmd}\n[not run: stopped by the user]"
            out, code = self._run(cmd, timeout_s, "bash", ctx=False)
            body = out.split("\n", 1)[1] if out.startswith("[exit ") else out
            if len(body) > 2500:
                body = body[:1200] + f"\n... [{len(body) - 2400} chars cut; run it alone for the full output] ...\n" + body[-1200:]
            return f"$ {cmd}\n[exit {code}]\n{body}" + self._where(out, code, cmd)
        if os.environ.get("MH_PARALLEL_MANY") == "1" and len(cmds) > 1 and all(read_only(c) for c in cmds):
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(cmds))) as ex:
                parts = list(ex.map(one, cmds))
        else:
            parts = [one(c) for c in cmds]
        return "\n\n".join(parts) or "no commands given"

    def probe(self, command, timeout_s=None):
        """MH_OBSERVE: run a check and return a failure digest (core/observe.py). Recorded as a check."""
        from .observe import digest, probe_command
        out, code = self._run(probe_command(command), timeout_s, "check", raw=True)
        return digest(out) + self._where(out, code, command)

    def trace(self, command, timeout_s=None):
        """MH_OBSERVE: run a Python command under a project-only profiler (core/observe.py)."""
        from .observe import trace_command
        wrapped = trace_command(command)
        if wrapped is None:
            raise ToolError("trace runs one Python command: python file.py ..., python -m module ..., or pytest ... "
                            "(optionally after `cd dir &&`), without pipes or redirects")
        out, _ = self._run(wrapped, timeout_s, "bash", raw=True)
        i = out.rfind("=== trace:")
        if i < 0:
            return self._cap(out, "trace")
        return self._cap((out[:i][-1500:] + "\n" + out[i:])[-7000:], "trace")

    def run_tests(self, command, timeout_s=None):
        return self._run(command, timeout_s, "check")[0]

    # -- the Codex tools (engine style "codex")
    def shell(self, command, workdir=None, timeout_ms=None):
        """Codex's shell tool: command (argv list or string), workdir, timeout_ms. Runs through the same path as bash
        (timeout, stop, output cap, error context). An apply_patch given through the shell is applied as a patch."""
        hit = patch_from_shell(command)
        if hit:
            patch, cd = hit
            return self.apply_patch(patch, base=cd or workdir)
        script = shell_script(command)
        if not script.strip():
            raise ToolError("shell: `command` is empty")
        try:
            from .applypatch import parse
            parse(script)
        except Exception:  # noqa: BLE001 - not a patch: an ordinary command
            pass
        else:
            raise ToolError("patch detected without explicit call to apply_patch. Rerun as apply_patch with the "
                            "patch as `input` (or [\"apply_patch\", \"<patch>\"] here).")
        cwd = None
        if workdir:
            d = self._path(str(workdir))
            if not d.is_dir() and not (self.container and not self.mounted):
                raise ToolError(f"workdir not found: {workdir}")
            cwd = d
        secs = None
        if timeout_ms is not None:
            secs = max(1, -(-int(timeout_ms) // 1000))
        return self._run(script, secs, "bash", cwd=cwd)[0]

    def apply_patch(self, input, base=None):
        """Codex's apply_patch: every hunk is checked and every new file built in memory first; only then are the files
        written, so a patch that cannot be applied changes nothing. Code files that would stop parsing are refused."""
        from .applypatch import PatchError, apply_update, parse
        try:
            hunks = parse(input)
        except PatchError as e:
            raise ToolError(str(e)) from None
        remote = bool(self.container and not self.mounted)
        base = str(base) if base else ""
        shown: dict = {}                              # key -> path as the patch names it

        def loc(path: str):
            full = os.path.join(base, path) if base and not os.path.isabs(path) else path
            if remote:
                return self._cpath(full), None
            p = self._path(full, write=True)
            return str(p), p

        files: dict = {}                              # key -> [path object, text before or None, text after or None]
        order: list = []

        def get(path: str):
            key, p = loc(path)
            if key not in files:
                if remote:
                    r = self._cexec(["cat", key])
                    before = r.stdout if r.returncode == 0 else None
                elif p.is_file():
                    before = p.read_bytes().decode("utf-8", "surrogateescape")
                else:
                    before = None
                files[key] = [p, before, before]
                order.append(key)
            shown.setdefault(key, path)
            return key, files[key]

        added, modified, deleted = [], [], []
        for h in hunks:
            if h.kind == "add":
                key, f = get(h.path)
                f[2] = h.contents
                added.append(h.path)
            elif h.kind == "delete":
                key, f = get(h.path)
                if f[2] is None:
                    raise ToolError(f"Failed to delete file {h.path}: no such file")
                f[2] = None
                deleted.append(h.path)
            else:
                key, f = get(h.path)
                if f[2] is None:
                    raise ToolError(f"Failed to read file to update {h.path}: no such file")
                try:
                    new = apply_update(f[2], h.path, h.chunks)
                except PatchError as e:
                    raise ToolError(str(e)) from None
                if h.move and h.move != h.path:
                    k2, f2 = get(h.move)
                    f2[2], f[2] = new, None
                    modified.append(h.move)
                else:
                    f[2] = new
                    modified.append(h.path)
        for key in order:                             # a code file may not stop parsing because of the patch
            p, before, after = files[key]
            if after is None or p is None or after == before:
                continue
            bad = self._syntax_error(p, after)
            if bad and (before is None or not self._syntax_error(p, before)):
                raise ToolError(f"apply_patch rejected: it would make {shown[key]} stop parsing. Nothing was "
                                f"written. Fix the patch at the marked place and retry.\n{bad}")
        done: list = []                               # write, restoring everything if one write fails
        try:
            for key in order:
                p, before, after = files[key]
                if after == before:
                    continue
                done.append(key)
                if remote:
                    if after is None:
                        self._cexec(["rm", "-f", key])
                    else:
                        self._cwrite(key, after)
                elif after is None:
                    p.unlink()
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(after.encode("utf-8", "surrogateescape"))
        except Exception as e:  # noqa: BLE001
            for key in done:
                p, before, after = files[key]
                try:
                    if remote:
                        self._cexec(["rm", "-f", key]) if before is None else self._cwrite(key, before)
                    elif before is None:
                        p.unlink(missing_ok=True)
                    else:
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(before.encode("utf-8", "surrogateescape"))
                except Exception:  # noqa: BLE001
                    pass
            raise ToolError(f"apply_patch failed while writing ({e}); the files written so far were restored") from None
        for key in done:
            p, before, after = files[key]
            if before is None and after is not None:
                self.created.add(key)
            elif before is not None and key not in self.original:
                self.original[key] = self._hash(before)
            if after is None:
                self.seen.pop(key, None)
            else:
                self.seen[key] = self._hash(after)
                self.written.add(key)
            self.turn_files.add(key)
            self.edits += 1
        lines = ["Success. Updated the following files:"]
        lines += [f"A {x}" for x in added] + [f"M {x}" for x in modified] + [f"D {x}" for x in deleted]
        return "\n".join(lines)

    def all_output(self) -> str:
        p = self.run_dir / "commands.log"
        return p.read_text(errors="replace") if p.exists() else ""

    def todo(self, items):
        self.todos = [{"text": str(i.get("text", ""))[:200], "done": bool(i.get("done"))} for i in items
                      if isinstance(i, dict)][:30]
        left = [t for t in self.todos if not t["done"]]
        return ("todo saved: " + f"{len(self.todos) - len(left)}/{len(self.todos)} done" + "".join(
            f"\n- [{'x' if t['done'] else ' '}] {t['text']}" for t in self.todos))

    def load_skill(self, name):
        if name not in self.skills:
            raise ToolError(f"unknown skill '{name}'. Available: {', '.join(self.skills) or 'none'}")
        return self.skills[name]
