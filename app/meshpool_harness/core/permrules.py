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
    if not patterns or not c or compound(c) or writes(c):
        return False
    return any(c == p or c.startswith(p + " ") for p in patterns)


def _inside(arg: str, root) -> bool:
    p = os.path.expanduser(arg)
    absolute = (p.startswith(("/", "\\")) or os.path.isabs(p)
                or bool(re.match(r"^[A-Za-z]:[\\/]", p)))      # C:/..., C:\..., UNC, also when checked on Linux
    if not (absolute or arg.startswith("~") or ".." in arg):
        return True
    if (re.match(r"^[A-Za-z]:[\\/]", p) or p.startswith("\\")) and os.name != "nt":
        return False                                  # a drive or UNC path is never inside a POSIX project root
    try:
        rp = Path(os.path.realpath(p if os.path.isabs(p) else os.path.join(str(root), p)))
        r = Path(os.path.realpath(str(root)))
        return rp == r or r in rp.parents
    except (OSError, ValueError):
        return False


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
    for a in rest:
        v = a.split("=", 1)[1] if a.startswith("-") and "=" in a else a
        if (not a.startswith("-") or "=" in a) and not _inside(v, root):
            return False
    return True


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
