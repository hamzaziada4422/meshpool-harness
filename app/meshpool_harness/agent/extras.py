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
import shutil
import subprocess
import threading
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness.core.permrules import TOOL_FAMILY as _TOOL_FAMILY

FILES = ("MESHPOOL.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md")
LOCAL = ("MESHPOOL.local.md", "CLAUDE.local.md")         # personal notes kept out of git, read beside the main file
RULE_DIRS = (".meshpool/rules", ".claude/rules", ".cursor/rules")
OTHER = (".cursorrules", ".windsurfrules", ".github/copilot-instructions.md", ".clinerules")   # other agents' files
MAX_FILE, MAX_ALL = 20000, 40000
EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop", "SessionStart", "SessionEnd",
          "PreCompact", "PostCompact", "SubagentStart", "SubagentStop", "Notification", "WorktreeCreate",
          "WorktreeRemove", "PermissionDenied", "StopFailure", "DirectoryAdded", "InstructionsLoaded")
_ONCE: set = set()                                       # hooks marked "once" that already ran (per session)
_ONCE_LOCK = threading.Lock()


# ---------------------------------------------------------------- instructions
def _git_root(root: Path) -> Path:
    for p in (root, *root.parents):
        if (p / ".git").exists():
            return p
    return root


def excluded(f: Path) -> bool:
    """MH_INSTRUCTIONS_EXCLUDE: comma-separated patterns of instruction and rules files never loaded, matched
    against the file's full path (*/legacy/AGENTS.md, **/.cursor/rules/*)."""
    pats = [p.strip() for p in os.environ.get("MH_INSTRUCTIONS_EXCLUDE", "").split(",") if p.strip()]
    if not pats:
        return False
    full = Path(f).expanduser().resolve().as_posix() if not isinstance(f, str) else f
    return any(fnmatch.fnmatchcase(full, p) or fnmatch.fnmatchcase(full, "*/" + p.lstrip("/")) or
               fnmatch.fnmatchcase(Path(full).name, p) for p in pats)


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
                if f.is_file() and not f.is_symlink() and not rule_globs(f):   # path-scoped ones come later
                    out.append((f"{top} ({rd})", f))
    return out


def rule_globs(f: Path) -> list[str]:
    """The file patterns a rules file is limited to: paths: (Claude Code) or globs: (Cursor), unless it says
    alwaysApply: true. Empty: it always applies."""
    from meshpool_harness import skills as SK
    text = _read(f)
    if not text.startswith("---"):
        return []
    meta, _ = SK.parse(text)
    if str(meta.get("alwaysApply", "")).strip().lower() == "true":
        return []
    manual = str(meta.get("alwaysApply", "")).strip().lower() == "false" and not (meta.get("globs") or meta.get("paths")
                                                                                  or "\npaths:" in text)
    raw = meta.get("paths") or meta.get("globs") or ""
    if not raw and text.find("\npaths:") > 0:            # a YAML list under paths:
        try:
            import yaml
            doc = yaml.safe_load(text[3:text.find("\n---", 3)])
            raw = doc.get("paths") or doc.get("globs") or "" if isinstance(doc, dict) else ""
        except Exception:  # noqa: BLE001
            raw = ""
    items = raw if isinstance(raw, list) else str(raw).strip("[]").split(",")
    out = [str(x).strip().strip("'\"") for x in items if str(x).strip().strip("'\"")]
    return out or (["(only when asked for)"] if manual else [])     # a manual Cursor rule: never loaded by itself


def _glob_match(rel: str, pat: str) -> bool:
    import fnmatch
    pat = pat.removeprefix("./").lstrip("/")
    if fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(rel, pat.replace("**/", "")):
        return True
    return "/" not in pat and fnmatch.fnmatchcase(rel.rsplit("/", 1)[-1], pat)   # *.ts matches at any depth


def scoped_rules(root: Path, path: str, seen: set) -> str:
    """Path-scoped rules files whose patterns match path, the first time work touches a matching file."""
    root = Path(root).resolve()
    try:
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else root / p).resolve()
        top = _git_root(root)
        rel = p.relative_to(top).as_posix()
    except (OSError, RuntimeError, ValueError):
        return ""
    out = []
    for rd in RULE_DIRS:
        base = top / rd
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.md")) + sorted(base.rglob("*.mdc")):
            if f.is_symlink() or not f.is_file() or f.resolve() in seen or excluded(f):
                continue
            pats = rule_globs(f)
            if pats and any(_glob_match(rel, g) for g in pats):
                seen.add(f.resolve())
                from meshpool_harness import skills as SK
                body = SK.parse(_imports(_read(f), f.parent, {f.resolve()}, fence=top.resolve()))[1].strip()   # in the project
                if body:
                    out.append(f"[Rules from {f.relative_to(top).as_posix()} for files like {', '.join(pats)} "
                               f"(follow them for {rel}):]\n{body[:MAX_FILE]}")
    return "\n\n".join(out)


def instruction_text(root: Path) -> str:
    parts, total = [], 0
    for where, f in instruction_files(root):
        if excluded(f):
            continue
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
                if f.is_symlink() or not f.is_file() or f.resolve() in seen or excluded(f):
                    continue
            except OSError:
                continue
            seen.add(f.resolve())
            body = _imports(_read(f), f.parent, {f.resolve()}, fence=_git_root(f.parent).resolve()).strip()   # imports stay in the project
            if body:
                out.append(f"[Instructions from {f.relative_to(root).as_posix()} for work in "
                           f"{folder.relative_to(root).as_posix()}/ (follow them there):]\n{body[:MAX_FILE]}")
    return "\n\n".join(out)


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
    are honoured only after the user trusted the folder (/trust, meshpool trust, the app's Trust this folder, or
    MH_TRUST_PROJECT=1). One store for all of them: trust.py."""
    from meshpool_harness import trust as TR
    return TR.is_trusted(root)


def trust_project(root) -> None:
    from meshpool_harness import trust as TR
    TR.trust(root)


def untrusted_config(root) -> list[str]:
    """Project files that would add allow rules or hooks but are ignored until the folder is trusted."""
    if project_trusted(root):
        return []
    return [n for n in ("permissions.json", "hooks.json") if (Path(root) / ".meshpool" / n).is_file()]


def hooks(root: Path) -> dict:
    """The user's own hooks, plus the project's when the user trusts the folder (see trust.py)."""
    from meshpool_harness import trust as TR
    out: dict = {e: [] for e in EVENTS}
    if os.environ.get("MH_HOOKS", "1") == "0":           # Settings: turn every hook off
        return out
    # <home>/hooks.json and the hooks of plugins the user installed are global; a project's own file needs its trust
    files = ([C.home() / "hooks.json"] + sorted((C.home() / "plugin-hooks").glob("*.json"))
             + ([Path(root) / ".meshpool" / "hooks.json"] if project_trusted(root) else []))
    # Asked before and after every tool call: each file's parse is kept while the file is unchanged
    from meshpool_harness import fsio
    for f in files:
        for e, hs in fsio.parse_cached(f, _hook_file, "hooks", missing={}).items():
            out[e] += [dict(h) for h in hs]
    return out


def _hook_file(raw: bytes) -> dict:
    """{event: [hook, ...]} of one hooks file; {} when it is not a JSON object."""
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    out: dict = {}
    for e in EVENTS:
        rows = d.get(e) or []
        for h in (rows if isinstance(rows, list) else []):
            if isinstance(h, dict) and isinstance(h.get("command"), str) and \
                    (h.get("args") is None or isinstance(h.get("args"), list)):
                out.setdefault(e, []).append(h)
    return out


def _hook_cmd(h: dict):
    """(what to run, shell?) for a hook. "args" (a list) runs the program directly with those arguments, never
    through a shell. "shell": "bash" or "powershell" picks the shell; "windows" replaces the command on Windows."""
    cmd = str(h.get("windows") or h["command"]) if os.name == "nt" and isinstance(h.get("windows"), str) \
        else h["command"]
    if isinstance(h.get("args"), list):
        return [cmd, *[str(a) for a in h["args"]]], False
    sh = str(h.get("shell") or "").lower()
    if sh == "powershell":
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", cmd], False
    if sh == "bash" and os.name != "nt" and shutil.which("bash"):
        return [shutil.which("bash"), "-c", cmd], False
    return cmd, True


def _run_hook(cmd, shell=False, cwd=None, input=None, timeout=None, env=None, **_):
    """subprocess.run(cmd, capture_output=True, text=True) for a hook, in its own process group, so a timeout ends
    the program the hook started too: subprocess.run kills only the shell and left `sh -c "python check.py"`'s
    python running."""
    extra = {"creationflags": 0x00000200} if os.name == "nt" else {"start_new_session": True}
    with subprocess.Popen(cmd, shell=shell, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **extra) as p:
        try:
            out, err = p.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            from meshpool_harness.core.tools import _kill_tree
            _kill_tree(p)
            try:
                p.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
            raise subprocess.TimeoutExpired(cmd, timeout) from None
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def _matches(event: str, h: dict, tool: str, args: dict, payload: dict) -> bool:
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied"):
        return _tool_matches(h.get("matcher", ""), tool, args)
    if event == "SessionStart" and h.get("matcher"):     # startup or compact
        src = str(payload.get("source") or "startup")
        return any(fnmatch.fnmatchcase(src, m.strip()) for m in str(h["matcher"]).split("|"))
    return True
# A hook names a tool the way Claude Code or Meshpool's classic tools do; the lean (Codex-style) lanes run "shell" and
# "apply_patch", the "c171" profile "exec_command" (its script is `cmd`, read through permrules.commands_of). Each
# family matches every tool that does that job, in any letter case ("Bash", "Edit|Write" from a Claude Code plugin;
# "bash", "edit" from a Meshpool hooks file). Saved permission rules use the same map.


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
    """Run the hooks for an event. Returns {"blocked", "allow", "message", "updated"}. A hook blocks with exit code 2
    (stderr is the reason) or by printing JSON {"decision": "block", "reason": "..."}; it can also print
    {"decision": "allow"} to skip the question for this call (never for destructive commands), {"additionalContext":
    "..."} to add a note the agent sees, or, for PreToolUse, {"updatedInput": {...}} to change the call's arguments
    (the saved rules then judge the changed call). Only PreToolUse and UserPromptSubmit can block. A hook with
    "async": true runs in the background and its output is not read; one with "once": true runs once a session."""
    out = {"blocked": False, "allow": False, "message": "", "updated": None}
    hs = hooks(root)[event]
    if not hs:
        return out
    data = json.dumps(dict(payload, event=event, tool=tool, args=args or {}, cwd=str(root)), ensure_ascii=False, default=str)
    notes = []
    for h in hs:
        if not _matches(event, h, tool, args or {}, payload):
            continue
        if h.get("once"):
            key = (str(payload.get("session") or os.getpid()), event, h["command"], str(h.get("args")))
            with _ONCE_LOCK:
                if key in _ONCE:
                    continue
                _ONCE.add(key)
        cmd, use_shell = _hook_cmd(h)
        try:
            secs = float(str(h.get("timeout") or timeout).strip().rstrip("sS") or timeout)
        except ValueError:
            secs = timeout
        run = dict(shell=use_shell, cwd=str(root), input=data, capture_output=True, text=True,
                   timeout=secs, env=C.scrub_env(dict(os.environ)))
        if h.get("async"):                               # fire and forget: never waits, never blocks
            def _bg(cmd=cmd, run=run):
                try:
                    _run_hook(cmd, **run)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            threading.Thread(target=_bg, daemon=True).start()
            continue
        can_block = event in ("PreToolUse", "UserPromptSubmit")
        try:
            secs = float(str(h.get("timeout") or timeout).strip().rstrip("sS") or timeout)
        except ValueError:
            secs = timeout
        try:
            r = _run_hook(cmd, **dict(run, timeout=secs))
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
                if event == "PreToolUse" and isinstance(d.get("updatedInput"), dict):
                    out["updated"] = dict(out["updated"] or {}, **d["updatedInput"])
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


# ---------------------------------------------------------------- built-in review commands
REVIEW_LEVELS = {
    "quick": (5, "Look at the changed lines and the lines right around them. Report only findings you are sure of."),
    "standard": (10, "Read each changed file around the edits, not only the diff lines."),
    "deep": (25, "Read each changed file, the code that calls what changed, and its tests. Run the tests if there is an "
                 "obvious command. Think through empty, large and failing inputs and concurrent use."),
}


def review_prompt(arg: str = "") -> str:
    """/review [quick|standard|deep] [--max N] [what]: the request for a code review at that depth, with a cap on
    the number of findings (5, 10 and 25 by default)."""
    words = str(arg or "").split()
    level = "standard"
    if words and words[0].lower() in REVIEW_LEVELS:
        level = words.pop(0).lower()
    cap = REVIEW_LEVELS[level][0]
    if "--max" in words:
        i = words.index("--max")
        try:
            cap = max(1, min(100, int(words[i + 1])))
            del words[i:i + 2]
        except (IndexError, ValueError):
            del words[i]
    target = " ".join(words).strip()
    what = f"Review {target}." if target else ("Review the current changes: git status, git diff and git diff --staged "
                                               "(or, with no changes, the last commit).")
    return (f"{what} {REVIEW_LEVELS[level][1]} Look for wrong logic, unhandled errors and edge cases, security problems "
            "(secrets, injection, unsafe file or shell use), breaking changes and behaviour that changed without a "
            f"test. Report at most {cap} findings, most severe first, each with the file and line, what goes wrong "
            "and when, and a concrete fix. Say plainly if you found nothing serious. Do not edit any file.")


def simplify_prompt(arg: str = "") -> str:
    """/simplify [what]: make changed code simpler, with the same behaviour."""
    target = str(arg or "").strip()
    what = f"Look at {target}." if target else "Look at the current changes (git diff and git diff --staged)."
    return (f"{what} Make the changed code simpler without changing what it does: reuse helpers the project already "
            "has instead of new copies, remove dead code and needless layers, and fix plain inefficiencies. Leave "
            "public names and formats as they are. Do not hunt for bugs. Make the edits, run the tests if there is an "
            "obvious command, and list what you changed.")


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
