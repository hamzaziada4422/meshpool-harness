"""Helpers for permission decisions: which shell commands a tool call runs, whether a command is a single plain
command, the "Allow in this chat" patterns for one, the Supervised read-only shortcut, and the saved rule file.
The chained-command matcher for saved rules stays in agent/core.py (`_rule_hit`); this module adds what it needs."""
from __future__ import annotations

import inspect
import os
import re
import shlex
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness import fsio

_SUBCMD = frozenset("git npm npx pnpm yarn bun cargo go docker kubectl make uv pip pip3 poetry dotnet gradle mvn gh "
                    "brew terraform helm".split())
# Programs that run another program or code given to them: a chat grant on one would cover whatever it runs
# ("Allow timeout" then covers `timeout 5 bash -c "rm -rf ~"`; "Allow find" covers `find . -exec rm -rf {} ;`).
_NO_GRANT = frozenset("bash sh zsh dash fish eval exec env xargs sudo su doas ssh perl ruby php "
                      "timeout nice nohup command time stdbuf watch find awk gawk mawk sed parallel "
                      "busybox chroot unshare nsenter setsid script strace ltrace".split())
_RUNNERS = {"npm", "yarn", "pnpm"}
# Runners that start whatever program follows them: a grant names that program too ("uv run pytest", "npx eslint"),
# else "Allow uv run" covered `uv run bash -c "rm -rf ~"`. docker and kubectl run or exec a command in a container.
_EXEC_VERBS = {"uv": {"run"}, "poetry": {"run"}, "pipenv": {"run"}, "pdm": {"run"}, "hatch": {"run"},
               "conda": {"run"}, "pixi": {"run"}, "npm": {"exec", "x"}, "pnpm": {"exec", "dlx"},
               "yarn": {"exec", "dlx", "node"}, "bun": {"x"}, "pipx": {"run"}, "dotnet": {"exec"}}
_EXEC_PROGS = frozenset({"npx", "bunx", "uvx", "pnpx"})
_NO_GRANT_VERBS = {"docker": {"run", "exec"}, "kubectl": {"run", "exec", "debug"}}
# Two-word commands whose second word is the real verb: a grant names all three words ("docker compose ps"), else
# "Allow docker compose" covered `docker compose exec web sh -c ...` (security review, 0.0.18). "exec" verbs run the
# program that follows (`uv tool run` is uvx); "no" verbs run a command in a container: never granted.
_NESTED = {("uv", "tool"): {"exec": {"run"}, "no": set()},
           ("docker", "compose"): {"exec": set(), "no": {"run", "exec"}},
           ("docker", "container"): {"exec": set(), "no": {"run", "exec", "start", "attach"}},
           ("docker", "image"): {"exec": set(), "no": set()}}
_OKREDIR = re.compile(r"\d?>\s*/dev/null|\d?>&\d|&>\s*/dev/null")


# A tool name in a saved rule or a hook matcher stands for every tool that does that job: the lean (Codex-style) lanes
# run "shell" and "apply_patch", the classic lanes "bash" and "edit". Keys are lower case (a Claude Code plugin writes
# "Bash", "Edit|Write"). One map for hooks (agent/extras.py) and saved allow and deny rules (agent/core.py _rule_hit).
# The "c171" profile runs Codex 0.171's "exec_command": a bash rule or hook covers it too.
_BASH_FAMILY = {"bash", "bash_many", "bash_start", "shell", "exec_command", "probe", "trace"}
TOOL_FAMILY = {
    "bash": _BASH_FAMILY,
    "shell": _BASH_FAMILY,
    "exec_command": _BASH_FAMILY,
    "edit": {"edit", "apply_patch", "notebook_edit"},
    "multiedit": {"edit", "apply_patch"},
    "write": {"write_file", "apply_patch"},
    "write_file": {"write_file", "apply_patch"},
    "apply_patch": {"apply_patch", "edit", "write_file"},
    "read": {"read_file"},
    "notebookedit": {"notebook_edit"},
}

# A command that deletes recursively, rewrites git history, wipes a disk, pipes the web into a shell or uses sudo: it
# always needs a person's yes (agent/core.py _destructive) and is never re-run unasked (core/tools.py _recheckable).
# git's global options may stand before the subcommand (git --no-pager reset --hard, git -C . push --force).
_GIT = r"\bgit(?:\s+(?:-[Cc]\s*(?:\"[^\"]*\"|'[^']*'|\S+)|--?[\w-]+(?:=\S*)?))*\s+"
DESTRUCTIVE = re.compile(
    r"(\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+|--recursive|--force)|" + _GIT +
    r"(push\s+.*(--force|-f\b|--delete)|reset\s+--hard|clean\s+-[a-z]*f|branch\s+-D|checkout\s+--\s|checkout\s+\.(\s|$)|restore\s+(--\S+\s+)*\.(\s|$)|worktree\s+remove\s+.*(-f\b|--force)|stash\s+(drop|clear))"
    r"|\bdd\s+if=|\b(mkfs|shred|wipefs|fdisk|diskutil\s+erase)\b|\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|fi)?sh\b"
    r"|\bchmod\s+-R\s+777|\bsudo\b|>\s*/dev/sd|\bdrop\s+(table|database)\b|\bformat\s+[a-z]:|\bRemove-Item\b.*-Recurse|\bdel\s+/[sq]\b|\brmdir\s+/s\b)",
    re.I)


# Recursive deletes that only clear caches: rm -rf __pycache__ (dev6 traces: the agent tidying up after its tests).
_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis"})


def _temp_roots() -> list[Path]:
    import tempfile
    out = []
    for t in {tempfile.gettempdir(), os.environ.get("TMPDIR") or "", "/tmp"}:
        if t and os.path.isdir(t):
            out.append(Path(os.path.realpath(t)))
    return out


def _within(p: Path, base: Path, strict: bool) -> bool:
    return base in p.parents or (not strict and p == base)


def scratch_rm(cmd: str, root, workdir: str | None = None) -> bool:
    """True when every part of `cmd` that DESTRUCTIVE matches is an rm that only clears scratch, so it needs no yes:
    - rm without -r of named files inside the project or a temp folder (what apply_patch's Delete File does unasked);
    - rm -r of a Python cache folder (__pycache__, .pytest_cache, ...) inside the project;
    - rm -r of a literal path strictly inside a temp folder.
    dev6 traces: `rm -f debug_slug.py`, `rm -rf __pycache__` were refused in headless auto mode ("No one is available
    to approve this"), 1-3 wasted calls each, 12 times in 37 runs. Anything else stays destructive: $ or backticks,
    ~, a target that is only wildcards, the project or temp folder itself, a path that leaves them, rm behind another
    program (sudo, xargs), or a cd whose target is not a plain path."""
    if not cmd or not DESTRUCTIVE.search(cmd) or re.search(r"[$`~]", cmd):
        return False
    # every destructive match in the WHOLE command must be an rm: a pattern that only matches across pieces
    # (curl ... | sh, wget -qO- ... | bash) is never cleared by judging the pieces one by one (PR #19 review)
    if any(not m.group(0).lstrip().lower().startswith("rm") for m in DESTRUCTIVE.finditer(cmd)):
        return False
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>()\n")
        lex.whitespace = " \t\r"
        toks = list(lex)
    except ValueError:
        return False
    segs, cur = [], []
    for t in toks:
        if t and set(t) <= set(";&|<>()\n"):
            if set(t) & set("<>()"):
                return False                       # redirections, subshells: not judged here
            segs.append(cur)
            cur = []
        else:
            cur.append(t)
    segs.append(cur)
    r = Path(os.path.realpath(str(root)))
    temps = _temp_roots()
    cwd = Path(os.path.realpath(os.path.join(str(r), workdir))) if workdir else r
    for w in segs:
        if not w:
            continue
        if w[0] == "cd":
            if len(w) != 2 or any(ch in w[1] for ch in "*?[") or w[1] == "-":
                return False
            cwd = Path(os.path.realpath(os.path.join(str(cwd), w[1])))
            continue
        if not DESTRUCTIVE.search(shlex.join(w)):
            continue
        if os.path.basename(w[0]) != "rm":
            return False
        flags, targets, opts = [], [], True
        for a in w[1:]:
            if opts and a == "--":
                opts = False
            elif opts and a.startswith("-") and a != "-":
                flags.append(a)
            else:
                targets.append(a)
        if not targets:
            return False
        rec = any(f in ("--recursive",) or (not f.startswith("--") and set(f[1:]) & {"r", "R"}) for f in flags)
        if any(f.startswith("--") and f not in ("--recursive", "--force", "--verbose") for f in flags):
            return False
        for t in targets:
            name = os.path.basename(t.rstrip("/"))
            if not name or set(name) <= set("*?.[]") or ".." in Path(t).parts:
                return False                       # rm -rf *, rm -rf ., rm -rf ../x
            if any(ch in t for ch in "*?["):
                return False                       # a wildcard (rm -f *.py): what it matches is not known here
            if ".git" in Path(t).parts or name == ".git":
                return False                       # the repository's own history (rm -f .git/HEAD)
            p = Path(os.path.realpath(os.path.join(str(cwd), t)))
            if p == r or p in r.parents:
                return False                       # the project or a folder holding it
            if rec and not (_within(p, r, strict=True) and name in _CACHE_DIRS):
                return False                       # a recursive delete: only a cache folder in the project (/tmp is
            if not _within(p, r, strict=True) and \
                    not any(_within(p, tb, strict=True) for tb in temps):   # shared: other projects live there)
                return False                       # neither the project nor a temp folder
    return True


def commands_of(name: str, args: dict) -> list[str]:
    """The shell command strings a tool call runs ([] for a tool that runs none)."""
    from meshpool_harness.core import tools as T
    args = args or {}
    n = (name or "").replace("-", "_")
    if n in ("bash", "bash_start", "probe", "trace"):
        c = args.get("command") or args.get("cmd")
        return [str(c)] if c else []
    if n == "bash_many":
        return [str(c) for c in args.get("commands") or [] if c]
    if n == "shell":
        c = T.shell_script(args.get("command"))
        return [c] if c else []
    if n == "exec_command":                       # Codex 0.171 (profile "c171"): the script is `cmd`
        c = args.get("cmd")
        return [str(c)] if c else []
    return []


def _scan(cmd: str):
    """(unquoted text with quoted parts blanked, compound?). A quote that never closes counts as compound."""
    out, q, i, compound = [], "", 0, False
    while i < len(cmd):
        ch = cmd[i]
        if q:
            if ch == "\\" and q == '"' and i + 1 < len(cmd):
                out.append("  "); i += 2; continue
            if ch == q:
                q = ""
            elif ch == "`" or (ch == "$" and cmd[i + 1:i + 2] == "(" and q == '"'):
                compound = True
            out.append(" ")
        elif ch in "'\"":
            q = ch; out.append(" ")
        elif ch == "\\":
            out.append("  "); i += 1
        else:
            out.append(ch)
            if ch in ";|\n`" or (ch == "$" and cmd[i + 1:i + 2] == "(") or (
                    ch == "&" and cmd[i - 1:i] != ">" and cmd[i + 1:i + 2] != ">"):
                compound = True
        i += 1
    return "".join(out), compound or bool(q)


def compound(cmd: str) -> bool:
    """More than one command, a substitution, or a background job: any of ; && || | & newline $( and backticks."""
    return _scan(cmd)[1]


def writes(cmd: str) -> bool:
    """Output redirection (other than to /dev/null or 2>&1) or a heredoc, outside quotes."""
    s = _OKREDIR.sub(" ", _scan(cmd)[0])
    return ">" in s or "<<" in s or "<(" in s


def _tokens(cmd: str):
    try:
        return shlex.split(cmd)
    except ValueError:
        return None


def grant_patterns(cmd: str) -> list[str]:
    """What "Allow in this chat" remembers for one plain command: the program, plus its subcommand where the program
    has them ("git status", "npm run build", "python -m pytest"). [] for a compound command or a risky program."""
    if compound(cmd) or writes(cmd):
        return []
    w = _tokens(cmd)
    while w and re.match(r"^[A-Za-z_]\w*=", w[0]):
        w = w[1:]
    if not w:
        return []
    prog, rest = os.path.basename(w[0]), w[1:]
    if prog in _NO_GRANT:
        return []
    pos = [a for a in rest if not a.startswith("-")]
    if pos and (prog, pos[0]) in _NESTED:
        rule = _NESTED[(prog, pos[0])]
        if len(pos) < 2 or rest[:2] != pos[:2]:
            return []                     # no verb, or an option before it (docker compose -f x run ...): not judged
        verb, inner = pos[1], rest[2:]
        if verb in rule["no"]:
            return []
        if verb in rule["exec"]:
            if not inner or inner[0].startswith("-"):
                return []
            return [f"{prog} {pos[0]} {verb} " + p for p in grant_patterns(shlex.join(inner))]
        return [f"{prog} {pos[0]} {verb}"]
    if prog in _EXEC_PROGS or (pos and pos[0] in _EXEC_VERBS.get(prog, ())):
        head = [prog] if prog in _EXEC_PROGS else [prog, pos[0]]
        inner = rest if prog in _EXEC_PROGS else rest[1:]
        if prog not in _EXEC_PROGS and rest[:1] != pos[:1]:
            return []                     # an option before the verb (uv --directory x run ...): not judged
        if not inner or inner[0].startswith("-"):
            return []                     # options of the runner (npx -y, uvx --from): what runs is not plain
        return [" ".join(head) + " " + p for p in grant_patterns(shlex.join(inner))]
    if pos and pos[0] in _NO_GRANT_VERBS.get(prog, ()):
        return []
    if prog in _SUBCMD:
        if not pos:
            return []
        if prog in _RUNNERS and pos[0] in ("run", "run-script") and len(pos) > 1:
            return [f"{prog} run {pos[1]}"]
        return [f"{prog} {pos[0]}"]
    if re.match(r"^(python|node)[\d.]*$", prog):
        if rest[:1] == ["-m"] and len(rest) > 1:
            return [f"{prog} -m {rest[1]}"]
        return [f"{prog} {rest[0]}"] if rest and not rest[0].startswith("-") else []
    return [prog]


def granted(cmd: str, patterns) -> bool:
    """A later command is covered by a chat grant only when it is one plain command that starts with a pattern."""
    c = cmd.strip()
    if not patterns or not c or "$" in c or compound(c) or writes(c):
        return False                    # any $ (ANSI-C $'...' quoting can hide a ; from the compound check)
    w = _tokens(c)
    while w and re.match(r"^[A-Za-z_]\w*=", w[0]):
        w = w[1:]
    if not w or os.path.basename(w[0]) in _NO_GRANT:
        return False                    # a wrapper runs whatever follows it: never covered by a grant
    return any(c == p or c.startswith(p + " ") for p in patterns)


def _inside(arg: str, root, cwd=None) -> bool:
    """The path resolves inside the project with symlinks followed: a relative `docs/id_rsa` where docs -> ~/.ssh is
    outside. A relative path is taken from cwd (default: the project root). A drive-relative Windows path (C:x,
    relative to that drive's own current folder) is never inside, nor is a drive or UNC path when checked off
    Windows."""
    p = os.path.expanduser(arg)
    if re.match(r"^[A-Za-z]:(?![\\/])", p):
        return False
    if (re.match(r"^[A-Za-z]:[\\/]", p) or p.startswith("\\")) and os.name != "nt":
        return False                                  # a drive or UNC path is never inside a POSIX project root
    try:
        rp = Path(os.path.realpath(p if os.path.isabs(p) else os.path.join(str(cwd or root), p)))
        r = Path(os.path.realpath(str(root)))
        return rp == r or r in rp.parents
    except (OSError, ValueError):
        return False


def _arg_paths(a: str) -> list[str]:
    """The paths one argument may name: a plain word itself; an option's value after = (--file=/x); and for an option
    with / \\ or ~ in it, every tail after its dash(es), so `file -f/etc/passwd` and `grep -rf~/.ssh/x` are checked."""
    if not a.startswith("-"):
        return [a]
    out = [a.split("=", 1)[1]] if "=" in a else []
    if any(ch in a for ch in "/\\~"):
        body = a.lstrip("-")
        if not a.startswith("--"):
            out += [body[i:] for i in range(1, len(body))]
        out = [v for v in out if v not in ("/", "\\")]          # cut -d/ : a delimiter, not the root folder
    return [v for v in out if v]


def safe_read(cmd: str, root, cwd=None) -> bool:
    """H-15: a single plain read-only command (tools.read_only, which already refuses writes and unknown programs)
    whose paths stay inside the project. Compound commands, substitutions and `$` never qualify. cwd: the folder the
    command runs in (a shell call's workdir); it must be inside the project too, and relative paths start there."""
    from meshpool_harness.core import tools as T
    if not cmd or "$" in cmd or compound(cmd) or writes(cmd) or not T.read_only(cmd):
        return False
    if cwd is not None and not _inside(str(cwd), root):
        return False                      # `cat id_rsa` with workdir ~/.ssh reads outside the project
    w = _tokens(cmd)
    if not w or re.match(r"^[A-Za-z_]\w*=", w[0]):
        return False
    prog, rest = os.path.basename(w[0]), w[1:]
    if prog == "git" and not T.git_config_safe(root):
        return False                      # the repository's own config can run programs (fsmonitor, pager, hooks)
    pos = [a for a in rest if not a.startswith("-")]
    if prog == "tree" and any(a.startswith("-") and not a.startswith("--") and "o" in a for a in rest):
        return False
    if prog == "uniq" and len(pos) > 1:
        return False
    if prog in ("xxd", "less") and (prog == "less" or "-r" in rest or len(pos) > 1):
        return False
    return all(_inside(v, root, cwd) for a in rest for v in _arg_paths(a))


# Commands that reach the network or install software: they still ask, and run with the network when allowed.
NETWORK = re.compile(
    r"(?:^|[\s;&|(`])(?:curl|wget|ssh|scp|sftp|rsync|nc|ncat|netcat|telnet|ftp|http|https|aria2c|gh|brew|apt|apt-get|"
    r"dnf|yum|snap|winget|choco|npx|bunx|pnpx|uvx|ollama)(?=$|[\s;&|)])"
    r"|\b(?:pip3?|python3?\s+-m\s+pip|uv\s+pip|conda|mamba|gem|cargo|go|composer|poetry|pipx)\s+(?:install|download|add|get|update|upgrade|publish)\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:i|install|ci|add|update|upgrade|publish|login|dlx|create|x|exec)\b"
    r"|\buv\s+(?:add|sync|lock|tool\s+install|run\s+--with)\b"
    r"|\bgit\s+(?:clone|fetch|pull|push|submodule|ls-remote|remote\s+update)\b"
    r"|\bdocker\s+(?:pull|push|login|build|run)\b"
    # calls in a script that reach the network (0.0.19: any "https://" in the text asked, so an XML namespace string
    # in a Python script read "Use the internet (schemas.openxmlformats.org)?" and a commit message with a link asked
    # too). A URL that is only text (a namespace, a message, a comment) is not network use; these calls are.
    r"|\brequests\s*\.\s*(?:get|post|put|patch|delete|head|options|request|Session)\b|\burlopen\s*\("
    r"|\burllib\.request\b|\burllib[23]\b|\burlretrieve\s*\(|\bhttp\.client\.HTTPS?Connection\b|\bhttplib2\.Http\b"
    r"|\bhttpx\s*\.\s*(?:get|post|put|patch|delete|head|request|stream|Client|AsyncClient)\b|\baiohttp\.ClientSession\b"
    r"|\bpycurl\.Curl\b|\bwebsockets\.connect\b|\bsocket\s*\.\s*(?:create_connection|socket)\b"
    r"|\b(?:ftplib\.FTP|smtplib\.SMTP|poplib\.POP3|imaplib\.IMAP4|telnetlib\.Telnet|paramiko\.SSHClient)"
    r"|\bfetch\s*\(\s*['\"`]?(?:https?:|//)|\baxios\b|\bXMLHttpRequest\b|\bnew\s+WebSocket\b|\bhttps?\.(?:get|request)\s*\("
    r"|\bread_(?:csv|excel|json|html|table|parquet|xml)\s*\(\s*['\"]https?://|\bload_dataset\s*\("
    r"|\b(?:Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer)\b|\b(?:iwr|irm)\s+['\"]?https?://"
    r"|(?:^|[\s;&|(])(?:xdg-open|open|start)\s+['\"]?https?://"
    # builds that fetch their dependencies (review, 0.0.18): run offline they fail, and nobody is asked
    r"|\bcargo\s+(?:build|run|test|check|fetch|update|install|doc|bench|add|vendor|publish)\b"
    r"|\bgo\s+(?:build|test|run|mod|generate|work|vet)\b|\bgit\s+lfs\b"
    r"|(?:^|[;&|(\n]\s*)(?:\./)?(?:mvn|mvnw|gradle|gradlew|sbt|lein|bundle|pod|carthage|hf|huggingface-cli)(?=\s|$)"
    r"|\b(?:swift\s+(?:build|package|run|test)|dotnet\s+(?:restore|build|run|test|add|publish|tool)|"
    r"(?:flutter|dart)\s+pub|deno\s+(?:run|install|cache))\b"
    r"|(?:^|[;&|(\n]\s*)(?:yarn|pnpm|bun)\s*(?=$|[;&|)\n])", re.I)

# The home folder named without a literal path: $HOME, %USERPROFILE%, a bare cd, Path.home() in a script (review,
# 0.0.18: `cd && cat .ssh/id_rsa` and `cat $HOME/.aws/credentials` ran without a question).
_HOME_REF = re.compile(
    r"\$\{?(?:HOME|USERPROFILE|HOMEPATH|HOMEDRIVE|APPDATA|LOCALAPPDATA|XDG_CONFIG_HOME|XDG_DATA_HOME)\b"
    r"|%(?:USERPROFILE|HOMEPATH|HOMEDRIVE|APPDATA|LOCALAPPDATA)%|\$env:|\bexpanduser\b|\bPath\.home\b|\bgetpwuid\b"
    r"|\bos\.environ\b|\bgetenv\b|\bhomedir\b"
    r"|(?:^|[;&|(\n]\s*)(?:cd|pushd|chdir)(?=\s*$|\s*[;&|)\n]|\s+-(?:\s|$|[;&|)]))", re.I)
_WINPATH = re.compile(r"(?<![\w])[A-Za-z]:[\\/][^\s'\"|;&<>]*")
# Deletes never run unasked (review, 0.0.18): Undo skips ignored, large and very many files, often the data files.
# rm of named scratch (temp files, caches, a named file in the project: scratch_rm) still runs.
DELETES = re.compile(
    r"(?:^|[\s;&|(`])(?:rm|rmdir|unlink|shred|truncate|srm)(?=$|[\s;&|)])"
    r"|\bfind\b[^;&|\n]*\s-(?:delete\b|exec(?:dir)?\s+(?:\S*/)?rm\b)"
    r"|\bshutil\.rmtree\b|\bos\.(?:remove|unlink|rmdir|removedirs)\b|\.unlink\s*\(|\.rmdir\s*\(|\bRemove-Item\b"
    r"|\bfs\.(?:rm|unlink|rmdir)\b|\.(?:rmSync|unlinkSync|rmdirSync)\s*\(|\bunlink\s*\(", re.I)


def _deletes_unasked_ok(cmd: str, root, cwd=None, made=None) -> bool:
    """Every delete in cmd is a plain rm that scratch_rm clears (named files in the project or temp, caches), and no
    named file in the project that it deletes is one the user already had: only files this chat made (`made`, full
    paths) or that are not there. Omar, 0.0.19: `rm sample.csv` ran without a question while Settings said "asks
    before deleting"."""
    hits = [m.group(0).strip(" ;&|(`\n").lower() for m in DELETES.finditer(cmd)]
    if not hits:
        return True
    if any(h != "rm" for h in hits) or re.search(r"(?<![\w-])rm\s+[^;&|\n]*[*?\[]", cmd):
        return False                          # rmdir, find -delete, a script's rmtree, a wildcard: ask
    if not scratch_rm(re.sub(r"(?<![\w-])rm\s+(?!-)", "rm -f ", cmd), root,
                      None if cwd is None else os.path.relpath(str(cwd), str(root))):
        return False
    r = Path(os.path.realpath(str(root)))
    mine = {os.path.realpath(str(m)) for m in (made or ())}
    for t in _rm_targets(cmd, r, cwd):
        if t.name in _CACHE_DIRS or not _within(t, r, strict=True):
            continue                          # a cache folder, or a temp file (scratch_rm checked where it is)
        if (t.exists() or t.is_symlink()) and str(t) not in mine:
            return False                      # the user's own file: deleting it asks
    return True


def _rm_targets(cmd: str, root: Path, cwd=None) -> list:
    """The paths the rm commands in cmd name, resolved from where each one runs (a `cd` before it counts)."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>()\n")
        lex.whitespace = " \t\r"
        toks = list(lex)
    except ValueError:
        return []
    here = Path(os.path.realpath(str(cwd))) if cwd is not None else root
    out, seg = [], []
    for t in toks + [";"]:
        if t and set(t) <= set(";&|<>()\n"):
            if seg and seg[0] == "cd" and len(seg) == 2:
                here = Path(os.path.realpath(os.path.join(str(here), seg[1])))
            elif seg and os.path.basename(seg[0]) == "rm":
                opts = True
                for a in seg[1:]:
                    if opts and a == "--":
                        opts = False
                    elif not (opts and a.startswith("-") and a != "-"):
                        out.append(Path(os.path.realpath(os.path.join(str(here), a))))
            seg = []
        else:
            seg.append(t)
    return out


_PATHLIKE = re.compile(r"(?<![\w.:/-])(~[\w.~/-]*|/[\w.~/-]*|\.\./[\w.~/-]*)")


def _home() -> Path | None:
    try:
        return Path(os.path.realpath(os.path.expanduser("~")))
    except (OSError, ValueError):
        return None


def sandbox_auto_ok(cmd: str, root, cwd=None, made=None) -> bool:
    """A command that may run without a question inside the OS sandbox (writes only in the project and temp folders,
    network cut): it does not reach the network or install software, and every path it names is in the project, a
    temp folder, or outside the user's home folder (programs, system files). A path elsewhere in the home folder
    (~/.ssh, ~/Documents outside the project) still asks, as do deletes of the user's files (`made`: the files this
    chat made, which it may delete) and git's risky verbs (GIT_ASK). Compound commands are fine: the sandbox confines
    them all."""
    if not cmd or not cmd.strip() or NETWORK.search(cmd) or _HOME_REF.search(cmd) or GIT_ASK.search(cmd):
        return False
    if cwd is not None and not _inside(str(cwd), root):
        return False
    if re.search(_GIT + r"commit\b[^;&|\n]*--amend\b", cmd) and amend_pushed(root):
        return False
    if not _deletes_unasked_ok(cmd, root, cwd, made):
        return False
    # paths from the words of the command (when it parses) and from anywhere in its text, heredoc bodies and
    # `python -c` code included: '/home/u/.ssh/id_rsa' inside a script is a path like any other
    toks = _tokens(cmd.replace("&&", " ").replace("||", " ").replace("|", " ").replace(";", " ")) or []
    # a lone "/" found in the text is division in code (a / b), not the root folder; as a word (find /) it counts
    cands = [v for a in toks for v in _arg_paths(a)] + [v for v in _PATHLIKE.findall(cmd) if v != "/"]
    cands += _WINPATH.findall(cmd)                    # C:\Users\me\.ssh (no sandbox there: Windows)
    home, temps = _home(), _temp_roots()
    for v in cands:
        v = v.strip("'\"")
        if not (v.startswith(("/", "~", "..", "./")) or "/" in v or "\\" in v):
            continue                                  # a word, not a path
        if _inside(v, root, cwd):
            continue
        try:
            rp = Path(os.path.realpath(os.path.expanduser(v) if os.path.isabs(os.path.expanduser(v))
                                       else os.path.join(str(cwd or root), v)))
        except (OSError, ValueError):
            return False
        # the user's own files outside the project, or a folder above them (find /, ls /home). Checked before the
        # temp folders: a home folder can sit under one.
        if home is not None and (rp == home or home in rp.parents or rp in home.parents):
            return False
    return True


# git verbs that always ask: the network, history rewrites, clean, and git's own config and hooks (a hook or a config
# value runs programs later, outside any sandbox).
GIT_ASK = re.compile(
    _GIT + r"(?:push|pull|fetch|clone|clean|rebase|filter-branch|filter-repo|submodule|update-ref|replace|prune|gc"
    r"|reflog\s+(?:expire|delete)|remote\s+(?:add|set-url|remove|rm|rename|prune|update|set-head|set-branches)"
    r"|reset\s+(?:[^;&|\n]*\s)?--(?:hard|merge|keep)|config(?!\s+(?:--get\S*|--list|-l)\b))\b"
    r"|\.git[\\/]+(?:hooks|config)\b|\bcore\.hooksPath\b", re.I)


def amend_pushed(root) -> bool:
    """HEAD is already on a remote branch, so amending it rewrites history others may have (or git cannot tell)."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(root), "branch", "-r", "--contains", "HEAD"], capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return r.returncode != 0 or bool(r.stdout.strip())


def _segments(cmd: str) -> list[str]:
    """The commands of a chain (&& || ; | and newlines), split where the shell splits, never inside quotes."""
    masked, out, at = _scan(cmd)[0], [], 0
    for m in re.finditer(r"&&|\|\||[;|\n]", masked):
        out.append(cmd[at:m.start()])
        at = m.end()
    out.append(cmd[at:])
    return [s.strip() for s in out if s.strip()]


_COMMIT_VALUE = {"-m", "--message", "-F", "--file", "-C", "-c", "--reuse-message", "--reedit-message", "--author",
                 "--date", "--fixup", "--squash", "-t", "--template", "--cleanup", "--trailer"}
_COMMIT_BAD = {"-p", "--patch", "--interactive", "-i", "--include", "-o", "--only", "-e", "--edit", "--pathspec-from-file"}


def _git_local_seg(w: list, root, cwd) -> bool:
    """One `git <verb> ...` (already split into words) that only writes the local repository."""
    if len(w) < 2 or w[0] != "git":
        return False
    verb, rest = w[1], w[2:]
    inside = lambda a: _inside(a, root, cwd)                     # noqa: E731
    exists = lambda a: os.path.lexists(os.path.join(str(cwd or root), os.path.expanduser(a)))   # noqa: E731
    if verb == "add":
        if any(a in ("-p", "--patch", "-i", "--interactive", "-e", "--edit") or a.startswith("--pathspec-from")
               for a in rest):
            return False
        return all(inside(a) for a in rest if not a.startswith("-"))
    if verb == "commit":
        skip = False
        for a in rest:
            if skip:
                skip = False
                continue
            if a in _COMMIT_BAD or a.split("=")[0] in _COMMIT_BAD:
                return False
            if a in _COMMIT_VALUE or (re.match(r"^-[a-zA-Z]*[mFCct]$", a) and not a.startswith("--")):
                skip = True                                     # its value follows (-m msg, -am msg, -F file)
                if a.endswith(("F", "t")) or a in ("--file", "--template"):
                    skip = False                                # a file: the next word is checked as a path
                continue
            if a.startswith("--amend") and amend_pushed(root):
                return False                                    # rewrites a commit that is already pushed
            if not a.startswith("-") and not inside(a):
                return False
        return True
    if verb == "stash":
        sub = rest[0] if rest and not rest[0].startswith("-") else "push"
        if sub not in ("push", "save", "list", "show", "apply", "pop", "create", "store"):
            return False                                        # drop, clear, branch: ask
        return all(inside(a) for a in rest[1:] if not a.startswith("-") and sub == "push" and "--" in rest)
    if verb == "reset":
        return "--soft" in rest and not any(a in ("--hard", "--merge", "--keep", "--mixed") for a in rest)
    if verb == "restore":
        paths = [a for a in rest if not a.startswith("-")]
        return bool(paths) and all(a not in (".", ":/", "*") and inside(a) for a in paths) and \
            not any(a.startswith(("--pathspec-from", "-p", "--patch")) for a in rest)
    if verb in ("checkout", "switch"):
        if any(a in ("-B", "-C", "--force", "-f", "--orphan", "-p", "--patch", "--force-create") for a in rest):
            return False
        if any(a in ("-b", "-c", "--create") for a in rest):    # a new branch (from here or a start point)
            return True
        if verb == "switch":
            return False                                        # switching branches: ask
        files = rest[rest.index("--") + 1:] if "--" in rest else [a for a in rest if not a.startswith("-")]
        return bool(files) and all(a not in (".", ":/", "*") and inside(a) and exists(a) for a in files)
    if verb == "branch":
        pos = [a for a in rest if not a.startswith("-")]
        return 1 <= len(pos) <= 2 and all(a in ("--track", "--no-track", "-q", "--quiet") for a in rest
                                          if a.startswith("-"))
    return False


def git_local_ok(cmd: str, root, cwd=None) -> bool:
    """A command made only of local git writes in the project's repository and plain reads: add, commit (--amend only
    while the commit is not pushed), stash, reset --soft, restore, checkout of a file, and a new branch (git branch x,
    checkout -b, switch -c). The repository's config and hooks must not run programs (tools.git_config_safe), no
    path leaves the project, and nothing expands ($, backticks) or redirects. Push, pull, fetch, clone, reset --hard,
    clean, rebase, filter-branch and git config still ask."""
    from meshpool_harness.core import tools as T
    if not cmd or not cmd.strip() or re.search(r"[$`\r\x00-\x08\x0b-\x1f]", cmd) or writes(cmd) or \
            GIT_ASK.search(cmd) or NETWORK.search(cmd) or DESTRUCTIVE.search(cmd):
        return False
    if cwd is not None and not _inside(str(cwd), root):
        return False
    wrote = False
    for seg in _segments(cmd):
        if T.git_read_only(seg) or safe_read(seg, root, cwd):
            continue
        w = _tokens(seg)
        if not w or not _git_local_seg(w, root, cwd):
            return False
        wrote = True
    return wrote and T.git_config_safe(root)


def undo_covers(path, root) -> str:
    """"" when Undo (checkpoints.py) keeps the file's contents and can put it back after a change; else why not."""
    from meshpool_harness import checkpoints as CK
    try:
        r = Path(os.path.realpath(str(root)))
        p = Path(os.path.realpath(str(path)))
        rel = p.relative_to(r)
    except (OSError, ValueError):
        return "it is outside the folder"
    if any(x in CK.SKIP_DIRS for x in rel.parts[:-1]):
        return "Undo does not keep files in that folder"
    try:
        if p.exists() and p.stat().st_size > CK.MAX_FILE:
            return f"Undo does not keep files over {CK.MAX_FILE // 1_000_000} MB"
    except OSError:
        return "it cannot be read"
    rules = CK._ignore_rules(r)
    parts = rel.as_posix().split("/")
    if any(CK._ignored("/".join(parts[:i]), i < len(parts), rules) for i in range(1, len(parts) + 1)):
        return "the folder's .gitignore lists it, so Undo does not keep it"
    return ""


_OFFICE_FILE = re.compile(r"[^\s'\"<>|;&()]+\.(?:pptx?|pptm|docx?|docm|xlsx?|xlsm|xlsb|odp|odt|ods|rtf)\b", re.I)
# a command that can replace or rewrite a file it names: copy or move over it, redirect into it, or a script that saves
_REWRITES = re.compile(r"(?:^|[\s;&|(])(?:cp|mv|tee|dd|install|rsync|ln|truncate|soffice|libreoffice|unoconv|pandoc)(?=\s)"
                       r"|\.save\s*\(|\bto_excel\b|\bsave_as\b|\bSaveAs\b|open\([^)]*['\"][wa]b?\+?['\"]", re.I)


def office_lock(path) -> bool:
    """The file is open in Word, Excel, PowerPoint (they leave ~$name, or ~$ and the name less its first one or two
    letters) or LibreOffice (.~lock.name#) right now."""
    try:
        f = Path(path)
        d, n = f.parent, f.name
        return any((d / x).exists() for x in ("~$" + n, "~$" + n[1:], "~$" + n[2:], ".~lock." + n + "#"))
    except (OSError, ValueError):
        return False


def open_office_write(cmd: str, root, cwd=None) -> str:
    """The open Office file a command may overwrite ("" for none): persona run, 0.0.18, `cp new.pptx Q3.pptx` replaced
    the deck the user had open in PowerPoint, without a question once ordinary work stopped asking. Such a command
    asks again; a command that only reads the open file does not."""
    if not cmd or not (writes(cmd) or _REWRITES.search(cmd)):
        return ""
    for m in _OFFICE_FILE.finditer(cmd):
        v = os.path.expanduser(m.group(0).strip("'\""))
        f = v if os.path.isabs(v) else os.path.join(str(cwd or root), v)
        if office_lock(f):
            return os.path.basename(f)
    return ""


def approve_with_scope(approve, kind, title, detail, scope: str):
    """Call an approver, passing `scope` only when its signature accepts it."""
    try:
        ps = inspect.signature(approve).parameters
        ok = "scope" in ps or any(p.kind is p.VAR_KEYWORD for p in ps.values())
    except (TypeError, ValueError):
        ok = False
    return approve(kind, title, detail, scope=scope) if ok and scope else approve(kind, title, detail)


# --- the saved rule file (G-05) -------------------------------------------------------------------------------

def user_path() -> Path:
    return C.home() / "permissions.json"


def project_path(root) -> Path:
    return Path(root) / ".meshpool" / "permissions.json"


def check_rule(rule) -> str:
    """"" when the rule is acceptable, else why not."""
    if not isinstance(rule, str) or not rule.strip():
        return "The rule is empty."
    if len(rule) > 300 or "\n" in rule or "\r" in rule:
        return "A rule is one line of at most 300 characters."
    if rule.strip().startswith("bash:") and not rule.strip()[5:].strip():
        return "A bash rule needs a command pattern, for example bash:npm test*."
    return ""


def list_rules(path) -> dict:
    d = fsio.read_json(path, default={}) if Path(path).exists() else {}
    d = d if isinstance(d, dict) else {}
    return {k: [x for x in (d.get(k) or []) if isinstance(x, str)] for k in ("allow", "ask", "deny")}


def _edit(path, kind: str, rule: str, add: bool) -> dict:
    if kind not in ("allow", "ask", "deny"):
        raise ValueError("kind is allow, ask or deny")
    rule = rule.strip()
    if add and check_rule(rule):
        raise ValueError(check_rule(rule))

    def fn(d):
        d = d if isinstance(d, dict) else {}
        cur = [x for x in (d.get(kind) or []) if isinstance(x, str)]
        if add and rule not in cur:
            cur.append(rule)
        if not add:
            cur = [x for x in cur if x != rule]
        d[kind] = cur
        d.setdefault("allow", []); d.setdefault("deny", [])
        return d
    fsio.update_json(path, fn, default={})
    return list_rules(path)


def add_rule(path, kind: str, rule: str) -> dict:
    return _edit(path, kind, rule, True)


def remove_rule(path, kind: str, rule: str) -> dict:
    return _edit(path, kind, rule, False)
