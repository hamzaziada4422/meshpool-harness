"""Things a power user expects from a coding agent, in one place:

  instructions   MESHPOOL.md / AGENTS.md / CLAUDE.md from the user's home, every parent folder up to the project's
                 git root, and the project folder (all of them, widest first), plus `@file` imports inside them.
  hooks          commands that run at set moments. ~/.meshpool/hooks.json and <project>/.meshpool/hooks.json:
                 {"PreToolUse": [{"matcher": "bash", "command": "./check.sh"}], "PostToolUse": [...],
                  "UserPromptSubmit": [...], "Stop": [...], "SessionStart": [...]}
                 The command gets the event as JSON on stdin. For PreToolUse and UserPromptSubmit, exit code 2 blocks
                 and what it printed to stderr goes back to the agent (or the user). The project's hooks.json runs
                 only after the user trusts the folder (trust.py); the user's own always runs.
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

FILES = ("MESHPOOL.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md")
LOCAL = ("MESHPOOL.local.md", "CLAUDE.local.md")         # personal notes kept out of git, read beside the main file
RULE_DIRS = (".meshpool/rules", ".claude/rules", ".cursor/rules")
OTHER = (".cursorrules", ".windsurfrules", ".github/copilot-instructions.md", ".clinerules")   # other agents' files
MAX_FILE, MAX_ALL = 20000, 40000
EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop", "SessionStart", "SessionEnd",
          "PreCompact", "PostCompact", "SubagentStart", "SubagentStop", "Notification", "WorktreeCreate",
          "WorktreeRemove")


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


def _imports(text: str, base: Path, seen: set, depth: int = 0) -> str:
    """Lines that are only `@relative/path.md` are replaced by that file's text (up to three levels)."""
    def sub(m):
        t = (base / m.group(1)).resolve()
        if depth >= 3 or t in seen or not t.is_file():
            return m.group(0)
        seen.add(t)
        return _imports(_read(t), t.parent, seen, depth + 1)
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
        main = next((d / n for n in FILES if (d / n).is_file()), None)    # one file per folder, in priority order
        if main:
            out.append((str(d), main))
        elif d == top:                                  # no Meshpool, Codex or Claude file: another agent's, if any
            other = next((d / n for n in OTHER if (d / n).is_file()), None)
            if other:
                out.append((str(d), other))
        for n in LOCAL:
            if (d / n).is_file():
                out.append((str(d) + " (yours, not in git)", d / n))
    for rd in RULE_DIRS:                                # every rules file of the project, in name order
        base = top / rd
        if base.is_dir():
            for f in sorted(base.rglob("*.md")) + sorted(base.rglob("*.mdc")):
                if f.is_file() and not f.is_symlink():
                    out.append((f"{top} ({rd})", f))
    return out


def instruction_text(root: Path) -> str:
    parts, total = [], 0
    for where, f in instruction_files(root):
        body = _imports(_read(f), f.parent, {f.resolve()}).strip()
        if not body or total + len(body) > MAX_ALL:
            continue
        total += len(body)
        parts.append(f"Instructions from {f.name} for {where} (follow them; the nearest to the project wins):\n{body}")
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def nested_instructions(root: Path, path: str, seen: set) -> str:
    """Instruction files (AGENTS.md, CLAUDE.md...) in the folders between the project and path, below the project
    itself, the first time the agent works there: a subfolder's own rules apply when work reaches it."""
    if not path or os.environ.get("MH_NESTED_INSTRUCTIONS") == "0":
        return ""
    root = Path(root).resolve()
    try:
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else root / p).resolve()
    except (OSError, RuntimeError, ValueError):
        return ""
    d = p if p.is_dir() else p.parent
    chain = [x for x in (d, *d.parents) if root in x.parents]          # strictly inside the project
    out = []
    for folder in reversed(chain):
        for name in FILES:
            f = folder / name
            try:
                if f.is_symlink() or not f.is_file() or f.resolve() in seen:
                    continue
            except OSError:
                continue
            seen.add(f.resolve())
            body = _imports(_read(f), f.parent, {f.resolve()}).strip()
            if body:
                out.append(f"[Instructions from {f.relative_to(root).as_posix()} for work in "
                           f"{folder.relative_to(root).as_posix()}/ (follow them there):]\n{body[:MAX_FILE]}")
    return "\n\n".join(out)


# ---------------------------------------------------------------- hooks
def hooks(root: Path) -> dict:
    """The user's own hooks, plus the project's when the user trusts the folder (see trust.py)."""
    from meshpool_harness import trust as TR
    out: dict = {e: [] for e in EVENTS}
    if os.environ.get("MH_HOOKS", "1") == "0":           # Settings: turn every hook off
        return out
    files = [C.home() / "hooks.json"]
    if TR.is_trusted(root):
        files.append(Path(root) / ".meshpool" / "hooks.json")
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for e in EVENTS:
            for h in (d.get(e) or []):
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    out[e].append(h)
    return out


def _tool_matches(matcher: str, name: str, args: dict) -> bool:
    if not matcher or matcher == "*":
        return True
    cmd = str(args.get("command") or "")
    for m in matcher.split("|"):
        m = m.strip()
        if m.startswith("bash:"):
            if cmd and fnmatch.fnmatchcase(cmd, m[5:].strip()):
                return True
        elif fnmatch.fnmatchcase(name, m) or (m == "bash" and name in ("bash", "bash_many", "bash_start", "shell")):
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
        if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure") and \
                not _tool_matches(h.get("matcher", ""), tool, args or {}):
            continue
        try:
            r = subprocess.run(h["command"], shell=True, cwd=str(root), input=data, capture_output=True, text=True,
                               timeout=int(h.get("timeout") or timeout), env=C.scrub_env(dict(os.environ)))
        except (subprocess.TimeoutExpired, OSError) as e:
            notes.append(f"[hook {h['command'][:40]} failed: {e}]")
            continue
        can_block = event in ("PreToolUse", "UserPromptSubmit")
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
                data = p.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:2000]:
                parts.append(f"[@{rel}] binary file, {len(data)} bytes (use a tool to read it)")
                continue
            body = data.decode("utf-8", errors="replace")
            body = body[:max(0, min(20000, limit - used))]
            used += len(body)
            parts.append(f"[@{rel}]\n{body}")
    return (text + "\n\n<attached_files>\n" + "\n\n".join(parts) + "\n</attached_files>") if parts else text
