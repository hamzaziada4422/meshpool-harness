"""Things a power user expects from a coding agent, in one place:

  instructions   MESHPOOL.md / AGENTS.md / CLAUDE.md from the user's home, every parent folder up to the project's
                 git root, and the project folder (all of them, widest first), plus `@file` imports inside them.
  hooks          commands that run at set moments. ~/.meshpool/hooks.json and <project>/.meshpool/hooks.json:
                 {"PreToolUse": [{"matcher": "bash", "command": "./check.sh"}], "PostToolUse": [...],
                  "UserPromptSubmit": [...], "Stop": [...], "SessionStart": [...]}
                 The command gets the event as JSON on stdin. For PreToolUse and UserPromptSubmit, exit code 2 blocks
                 and what it printed to stderr goes back to the agent (or the user).
  commands       /name from <project>/.meshpool/commands/name.md or ~/.meshpool/commands/name.md. $ARGUMENTS (and
                 $1, $2) are filled from what follows the name.
  mentions       @path in a message attaches that file (or lists that folder) so no read call is needed.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness.core.permrules import TOOL_FAMILY as _TOOL_FAMILY

FILES = ("MESHPOOL.md", "AGENTS.md", "CLAUDE.md")
MAX_FILE, MAX_ALL = 20000, 40000
EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SessionStart", "PreCompact", "SubagentStop")


# ---------------------------------------------------------------- instructions
def _git_root(root: Path) -> Path:
    for p in (root, *root.parents):
        if (p / ".git").exists():
            return p
    return root


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE]
    except OSError:
        return ""


def _imports(text: str, base: Path, seen: set, depth: int = 0, fence: Path | None = None) -> str:
    """Lines that are only `@relative/path.md` are replaced by that file's text (up to three levels). With a `fence`
    (a project's instruction file) only files inside that folder are imported."""
    def sub(m):
        t = (base / m.group(1)).resolve()
        if depth >= 3 or t in seen or not t.is_file():
            return m.group(0)
        if fence is not None:
            try:
                t.relative_to(fence)
            except ValueError:
                return m.group(0)
        seen.add(t)
        return _imports(_read(t), t.parent, seen, depth + 1, fence)
    return re.sub(r"^@(\S+\.(?:md|txt))\s*$", sub, text, flags=re.M)


def instruction_files(root: Path) -> list[tuple[str, Path]]:
    """[(where it applies, file)] widest first: the user's own, then parent folders, then the project."""
    out: list[tuple[str, Path]] = []
    home = C.home()
    for n in FILES[:1]:
        if (home / n).is_file():
            out.append(("you (all projects)", home / n))
    top = _git_root(root)
    chain = [root]
    for p in root.parents:
        chain.append(p)
        if p == top:
            break
    for d in reversed(chain):
        for n in FILES:
            if (d / n).is_file():
                out.append((str(d), d / n))
                break                                   # one file per folder, in priority order
    return out


def instruction_text(root: Path) -> str:
    parts, total = [], 0
    for where, f in instruction_files(root):
        try:
            f.resolve().relative_to(C.home().resolve())
            fence = None                                    # the user's own file may import from anywhere
        except (ValueError, OSError):
            fence = _git_root(f.parent).resolve()           # a project's file stays inside the project
        body = _imports(_read(f), f.parent, {f.resolve()}, fence=fence).strip()
        if not body or total + len(body) > MAX_ALL:
            continue
        total += len(body)
        parts.append(f"Instructions from {f.name} for {where} (follow them; the nearest to the project wins):\n{body}")
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


# ---------------------------------------------------------------- hooks
def _trust_file() -> Path:
    return C.home() / "trusted_projects.json"


def _norm(root) -> str:
    try:
        return str(Path(root).expanduser().resolve())
    except OSError:
        return str(root)


def project_trusted(root) -> bool:
    """A project's own .meshpool/permissions.json "allow" rules and hooks.json commands (which run shell commands)
    are honoured only after the user trusted the folder (/trust, or MH_TRUST_PROJECT=1)."""
    if os.environ.get("MH_TRUST_PROJECT") == "1":
        return True
    try:
        d = json.loads(_trust_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, list) and _norm(root) in d


def trust_project(root) -> None:
    try:
        d = json.loads(_trust_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = []
    d = [x for x in d if isinstance(x, str)] if isinstance(d, list) else []
    if _norm(root) not in d:
        d.append(_norm(root))
    f = _trust_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def untrusted_config(root) -> list[str]:
    """Project files that would add allow rules or hooks but are ignored until the folder is trusted."""
    if project_trusted(root):
        return []
    return [n for n in ("permissions.json", "hooks.json") if (Path(root) / ".meshpool" / n).is_file()]


def hooks(root: Path) -> dict:
    out: dict = {e: [] for e in EVENTS}
    # <home>/hooks.json and the hooks of plugins the user installed are global; a project's own file needs its trust
    files = ([C.home() / "hooks.json"] + sorted((C.home() / "plugin-hooks").glob("*.json"))
             + ([Path(root) / ".meshpool" / "hooks.json"] if project_trusted(root) else []))
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        for e in EVENTS:
            for h in (d.get(e) or []):
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    out[e].append(h)
    return out


# A hook names a tool the way Claude Code or Meshpool's classic tools do; the lean (Codex-style) lanes run "shell" and
# "apply_patch". Each family matches every tool that does that job, in any letter case ("Bash", "Edit|Write" from a
# Claude Code plugin; "bash", "edit" from a Meshpool hooks file). Saved permission rules use the same map.


def _hook_commands(name: str, args: dict) -> list[str]:
    """Every command text a bash: matcher is tested against: each command a call runs (bash_many's `commands` one by
    one, shell's script), and the call's own `command` as it was written."""
    from meshpool_harness.core import permrules as PR
    out = list(PR.commands_of(name, args))
    cmd = args.get("command") or ""
    if isinstance(cmd, list):                       # shell takes an argv list (["bash", "-lc", "rm -rf x"])
        cmd = cmd[-1] if len(cmd) >= 3 and str(cmd[-2]) in ("-c", "-lc") else " ".join(map(str, cmd))
    if cmd:
        out.append(str(cmd))
    out += [str(c) for c in args.get("commands") or [] if c and isinstance(c, str)]
    return list(dict.fromkeys(out))


def _tool_matches(matcher: str, name: str, args: dict) -> bool:
    if not matcher or matcher == "*":
        return True
    cmds = _hook_commands(name, args or {})
    for m in matcher.split("|"):
        m = m.strip()
        if m.lower().startswith("bash:") or m.lower().startswith("bash("):
            pat = m[5:].strip() if m[4] == ":" else (m[5:-1] if m.endswith(")") else m[5:]).strip()
            if any(fnmatch.fnmatchcase(c, pat) for c in cmds):
                return True
        elif fnmatch.fnmatchcase(name, m) or fnmatch.fnmatchcase(name.lower(), m.lower()) \
                or name in _TOOL_FAMILY.get(m.lower(), ()):
            return True
    return False


def run_hooks_full(root: Path, event: str, payload: dict, tool: str = "", args: dict | None = None, timeout: int = 30) -> dict:
    """Run the hooks for an event. Returns {"blocked", "allow", "message"}. A hook blocks with exit code 2 (stderr is
    the reason) or by printing JSON {"decision": "block", "reason": "..."}; it can also print {"decision": "allow"} to
    skip the question for this call (never for destructive commands), or {"additionalContext": "..."} to add a note
    the agent sees. Only PreToolUse and UserPromptSubmit can block."""
    out = {"blocked": False, "allow": False, "message": ""}
    hs = hooks(root)[event]
    if not hs:
        return out
    data = json.dumps(dict(payload, event=event, tool=tool, args=args or {}, cwd=str(root)), ensure_ascii=False, default=str)
    notes = []
    for h in hs:
        if event in ("PreToolUse", "PostToolUse") and not _tool_matches(h.get("matcher", ""), tool, args or {}):
            continue
        can_block = event in ("PreToolUse", "UserPromptSubmit")
        try:
            secs = float(str(h.get("timeout") or timeout).strip().rstrip("sS") or timeout)
        except ValueError:
            secs = timeout
        try:
            r = subprocess.run(h["command"], shell=True, cwd=str(root), input=data, capture_output=True, text=True,
                               timeout=secs, env=C.scrub_env(dict(os.environ)))
        except (subprocess.TimeoutExpired, OSError) as e:
            if can_block:                                 # a check that did not finish is not a pass
                out.update(blocked=True, message=f"The hook {h['command'][:40]} failed ({e}), so this was stopped.")
                return out
            notes.append(f"[hook {h['command'][:40]} failed: {e}]")
            continue
        if r.returncode == 2 and can_block:
            out.update(blocked=True, message=(r.stderr or r.stdout).strip() or "A hook blocked this.")
            return out
        text = r.stdout.strip()
        if text.startswith("{"):
            try:
                d = json.loads(text)
            except ValueError:
                d = None
            if isinstance(d, dict):
                dec = str(d.get("decision") or "").lower()
                if dec == "block" and can_block:
                    out.update(blocked=True, message=str(d.get("reason") or "A hook blocked this."))
                    return out
                if dec == "allow" and event == "PreToolUse":
                    out["allow"] = True
                if d.get("additionalContext"):
                    notes.append(str(d["additionalContext"])[:2000])
                continue
        if text:
            notes.append(text[:2000])
    out["message"] = "\n".join(notes)
    return out


def run_hooks(root: Path, event: str, payload: dict, tool: str = "", args: dict | None = None, timeout: int = 30):
    """(blocked, message): see run_hooks_full."""
    r = run_hooks_full(root, event, payload, tool, args, timeout)
    return r["blocked"], r["message"]


# ---------------------------------------------------------------- custom commands
def command_dirs(root: Path) -> list[Path]:
    return [Path(root) / ".meshpool" / "commands", C.home() / "commands"]


def commands(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for d in reversed(command_dirs(root)):               # the project's own wins over the user's
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                out[f.stem.lower()] = f
    return out


def expand_command(text: str, root: Path) -> str | None:
    """'/review src/app.py' -> the command file's text with $ARGUMENTS filled in, or None when there is no such command."""
    t = text.strip()
    if not t.startswith("/") or t.startswith("//"):
        return None
    name, _, rest = t[1:].partition(" ")
    f = commands(root).get(name.lower())
    if not f:
        return None
    body = _read(f)
    if body.startswith("---"):                            # front matter (description) is for the menu, not the model
        end = body.find("\n---", 3)
        body = body[end + 4:] if end > 0 else body
    words = rest.split()
    for i, w in enumerate(words[:9], 1):
        body = body.replace(f"${i}", w)
    body = re.sub(r"\$\d", "", body)
    return (body.replace("$ARGUMENTS", rest.strip()).strip() + ("" if "$ARGUMENTS" in _read(f) or not rest.strip() else "\n\n" + rest.strip()))


# ---------------------------------------------------------------- @file mentions
_MENTION = re.compile(r"(?<![\w@./])@((?:\.{0,2}/)?[\w.\-/]+[\w])")


def expand_mentions(text: str, root: Path, limit: int = 60000) -> str:
    """Each @path that names a file inside the project (or a folder) is attached after the message."""
    root = Path(root).resolve()
    seen, parts, used = set(), [], 0
    for m in _MENTION.finditer(text):
        rel = m.group(1)
        p = (root / rel).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            continue
        if p in seen or not p.exists() or rel.count("/") == 0 and "." not in rel and not p.is_dir():
            continue
        seen.add(p)
        if p.is_dir():
            names = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())[:80]
            parts.append(f"[@{rel}] folder contents: " + ", ".join(names))
        else:
            try:
                size = p.stat().st_size
                with open(p, "rb") as fh:
                    data = fh.read(100000)                  # never the whole of a multi-GB log
            except OSError:
                continue
            if b"\0" in data[:2000]:
                parts.append(f"[@{rel}] binary file, {size} bytes (use a tool to read it)")
                continue
            body = data.decode("utf-8", errors="replace")
            body = body[:max(0, min(20000, limit - used))]
            used += len(body)
            parts.append(f"[@{rel}]\n{body}")
    return (text + "\n\n<attached_files>\n" + "\n\n".join(parts) + "\n</attached_files>") if parts else text
