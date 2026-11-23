"""Optional sandbox for the agent's shell commands (MH_SANDBOX=1 in meshpool.config or the environment).

Commands can read everything but write only inside the project folder, the temporary folders and the usual tool caches
(pip, npm, cargo), so a mistaken or hostile command cannot damage the rest of the computer. Network access is left on.
Linux uses bubblewrap (bwrap) and macOS uses sandbox-exec; Windows, or a computer without the tool, runs commands
without it and says so once. This is a second line of defence next to approvals, hooks and the destructive-command rule.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_SAID = [False]


def enabled() -> bool:
    return os.environ.get("MH_SANDBOX", "").strip().lower() in ("1", "on", "true", "yes")


def _writable(root: Path) -> list[str]:
    home = Path.home()
    cands = [str(root), "/tmp", "/var/tmp", str(home / ".cache"), str(home / ".npm"), str(home / ".cargo"),
             str(home / ".local"), str(home / ".meshpool"), str(home / ".config" / "pip"), str(home / ".pnpm-store")]
    extra = os.environ.get("MH_SANDBOX_WRITE", "")
    cands += [x for x in extra.split(os.pathsep) if x]
    return [c for c in dict.fromkeys(cands) if os.path.isdir(c)]


def wrap(argv: list[str], root, platform: str | None = None, which=shutil.which, cwd=None) -> list[str]:
    """argv, wrapped so it can only write where it should. Unchanged when the sandbox is off or unavailable."""
    if not enabled():
        return argv
    plat = platform or sys.platform
    root = Path(root).resolve()
    if plat.startswith("linux") and which("bwrap"):
        out = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--die-with-parent", "--unshare-pid"]
        for w in _writable(root):
            out += ["--bind", w, w]
        return out + ["--chdir", str(Path(cwd).resolve() if cwd else root), "--"] + argv
    if plat == "darwin" and which("sandbox-exec"):
        allow = "".join(f'(subpath "{p}")' for p in _writable(root) + ["/private/tmp", "/private/var/folders"])
        profile = ('(version 1)(allow default)(deny file-write*)'
                   f'(allow file-write* {allow} (literal "/dev/null") (literal "/dev/dtracehelper") (regex #"^/dev/tty"))')
        return ["sandbox-exec", "-p", profile] + argv
    if not _SAID[0]:
        _SAID[0] = True
        print("[Meshpool] MH_SANDBOX is on but this computer has no bubblewrap (Linux) or sandbox-exec (macOS); "
              "commands run without it.", file=sys.stderr)
    return argv
