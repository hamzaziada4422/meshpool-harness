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
_NO_GRANT = frozenset("bash sh zsh dash fish eval exec env xargs sudo su doas ssh perl ruby php".split())
_RUNNERS = {"npm", "yarn", "pnpm"}
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
    return any(c == p or c.startswith(p + " ") for p in patterns)


def _inside(arg: str, root) -> bool:
    """The path resolves inside the project with symlinks followed: a relative `docs/id_rsa` where docs -> ~/.ssh is
    outside. A drive-relative Windows path (C:x, relative to that drive's own current folder) is never inside, nor is
    a drive or UNC path when checked off Windows."""
    p = os.path.expanduser(arg)
    if re.match(r"^[A-Za-z]:(?![\\/])", p):
        return False
    if (re.match(r"^[A-Za-z]:[\\/]", p) or p.startswith("\\")) and os.name != "nt":
        return False                                  # a drive or UNC path is never inside a POSIX project root
    try:
        rp = Path(os.path.realpath(p if os.path.isabs(p) else os.path.join(str(root), p)))
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


def safe_read(cmd: str, root) -> bool:
    """H-15: a single plain read-only command (tools.read_only, which already refuses writes and unknown programs)
    whose paths stay inside the project. Compound commands, substitutions and `$` never qualify."""
    from meshpool_harness.core import tools as T
    if not cmd or "$" in cmd or compound(cmd) or writes(cmd) or not T.read_only(cmd):
        return False
    w = _tokens(cmd)
    if not w or re.match(r"^[A-Za-z_]\w*=", w[0]):
        return False
    prog, rest = os.path.basename(w[0]), w[1:]
    pos = [a for a in rest if not a.startswith("-")]
    if prog == "tree" and any(a.startswith("-") and not a.startswith("--") and "o" in a for a in rest):
        return False
    if prog == "uniq" and len(pos) > 1:
        return False
    if prog in ("xxd", "less") and (prog == "less" or "-r" in rest or len(pos) > 1):
        return False
    return all(_inside(v, root) for a in rest for v in _arg_paths(a))


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
    return {k: [x for x in (d.get(k) or []) if isinstance(x, str)] for k in ("allow", "deny")}


def _edit(path, kind: str, rule: str, add: bool) -> dict:
    if kind not in ("allow", "deny"):
        raise ValueError("kind is allow or deny")
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
