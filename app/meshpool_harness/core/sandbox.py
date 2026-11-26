"""Sandbox for the agent's shell commands (G-01, G-02, OSS-17).

On by default where the computer has the tool, except in Full access (MH_SANDBOX=1 forces it on, MH_SANDBOX=0 off).
Commands can read everything but write only inside the project folder, the temporary folders and the usual tool caches
(pip, npm, cargo), so a mistaken or hostile command - including a shell redirection - cannot damage the rest of the
computer. MH_SANDBOX_NETWORK=0 (Settings, or `meshpool --no-network`) also cuts the network.
Linux uses bubblewrap (bwrap --ro-bind / / plus writable binds, --unshare-net) and macOS uses sandbox-exec with a
Seatbelt profile in the same shape Codex's (openai/codex, Apache-2.0) uses: allow default, deny file-write*, then allow
the listed folders; see THIRD_PARTY_NOTICES.md. Windows has neither, and says so once.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_SAID: set = set()
_ON = ("1", "on", "true", "yes")
_OFF = ("0", "off", "false", "no")


def _setting(name: str) -> str:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        try:
            from meshpool_harness import config as C
            v = str(C.read().get(name, "")).strip().lower()
        except Exception:  # noqa: BLE001
            v = ""
    return v


def requested(mode: str | None = None) -> bool:
    """Should this command be sandboxed if the computer can? Explicit setting wins; otherwise on unless Full access."""
    v = _setting("MH_SANDBOX")
    if v in _ON:
        return True
    if v in _OFF:
        return False
    return mode != "auto"


def enabled() -> bool:
    return requested(None)


def network_allowed() -> bool:
    return _setting("MH_SANDBOX_NETWORK") not in _OFF


def available(platform: str | None = None, which=shutil.which) -> bool:
    plat = platform or sys.platform
    return (plat.startswith("linux") and bool(which("bwrap"))) or (plat == "darwin" and bool(which("sandbox-exec")))


def _say(key: str, msg: str) -> None:
    if key not in _SAID:
        _SAID.add(key)
        print(f"[Meshpool] {msg}", file=sys.stderr)


def unavailable_notice(platform: str | None = None) -> str:
    plat = platform or sys.platform
    if plat.startswith("win"):
        return "The command sandbox is not available on Windows; commands run with your full access."
    if plat == "darwin":
        return "The command sandbox needs sandbox-exec, which this Mac does not have; commands run without it."
    return ("The command sandbox needs bubblewrap (install the bwrap package); until then commands run without it "
            "and can write anywhere you can.")


_GIT_DIRS: dict = {}                 # root -> (what the answer depends on, the answer): one git process per folder


def _git_dirs_key(root: Path):
    """What _git_dirs(root) depends on: the nearest .git above root (its identity, and its time and size when it is a
    worktree's "gitdir:" file) and the GIT_* variables that move it. Stat calls only, no process."""
    env = tuple((k, os.environ.get(k, "")) for k in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_CEILING_DIRECTORIES",
                                                     "GIT_DISCOVERY_ACROSS_FILESYSTEM"))
    for d in (root, *root.parents):
        p = d / ".git"
        try:
            st = os.lstat(p)
        except OSError:
            continue
        isdir = os.path.isdir(p)
        return env, str(p), st.st_ino, st.st_mode, None if isdir else (st.st_mtime_ns, st.st_size)
    return env, None


def _git_dirs(root: Path) -> list[str]:
    """The repository folders git writes to for a command run in ``root``: its own git dir and the shared one. They
    lie outside ``root`` for a project in a subfolder of a repository (``<repo>/.git``) and for a worktree copy
    (``<main>/.git`` and ``<main>/.git/worktrees/<name>``), so without them every commit, stash or add failed there.
    Kept per folder while its nearest .git is the same: every sandboxed command ran git rev-parse before."""
    root = Path(root)
    key = _git_dirs_key(root)
    hit = _GIT_DIRS.get(str(root))
    if hit is not None and hit[0] == key:
        return list(hit[1])
    out = _git_dirs_run(root)
    if out is None:                              # git missing or too slow this time: ask again next command
        return []
    if len(_GIT_DIRS) > 256:
        _GIT_DIRS.clear()
    _GIT_DIRS[str(root)] = (key, tuple(out))
    return out


def _git_dirs_run(root: Path) -> list[str] | None:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-common-dir", "--git-dir"],
                           capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode:
        return []                                  # not inside a repository (or no git)
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line:
            p = Path(line)
            out.append(str((p if p.is_absolute() else Path(root) / p).resolve()))
    return out


def _writable(root: Path) -> list[str]:
    home = Path.home()
    cands = [str(root), "/tmp", "/var/tmp", str(home / ".cache"), str(home / ".npm"), str(home / ".cargo"),
             str(home / ".local"), str(home / ".meshpool"), str(home / ".config" / "pip"), str(home / ".pnpm-store")]
    cands += _git_dirs(root)
    extra = os.environ.get("MH_SANDBOX_WRITE", "")
    cands += [x for x in extra.split(os.pathsep) if x]
    return [c for c in dict.fromkeys(cands) if os.path.isdir(c)]


def _data_folder() -> str | None:
    """meshpool's data folder (config.home()), resolved, when it exists."""
    try:
        from meshpool_harness import config as C
        p = Path(C.home()).resolve()
        return str(p) if p.is_dir() else None
    except Exception:  # noqa: BLE001
        return None


def _under(p: str, base: str) -> bool:
    """p is base or inside it."""
    return p == base or p.startswith(base.rstrip(os.sep) + os.sep)


def wrap(argv: list[str], root, platform: str | None = None, which=shutil.which, cwd=None, mode: str | None = None) -> list[str]:
    """argv, wrapped so it can only write where it should. Unchanged when the sandbox is off or unavailable (with a
    notice, once, when it was wanted)."""
    if not requested(mode):
        return argv
    plat = platform or sys.platform
    root = Path(root).resolve()
    net = network_allowed()
    if plat.startswith("linux") and which("bwrap"):
        out = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--die-with-parent", "--unshare-pid"]
        if not net:
            out.append("--unshare-net")
        ws = _writable(root)
        for w in ws:
            out += ["--bind", w, w]
        data = _data_folder()
        if data and any(_under(data, os.path.realpath(w)) for w in ws):
            # meshpool's own data folder (~/.local/share/meshpool, inside the writable ~/.local) holds the settings
            # (MH_SANDBOX itself), permissions.json, hooks.json and mcp.json: a command that writes them turns the
            # sandbox off or approves its own next commands. Read-only, except the folders a command works in.
            out += ["--ro-bind", data, data]
            for w in ws:
                rw = os.path.realpath(w)
                if _under(rw, data) and (rw != data or rw == str(root)):
                    out += ["--bind", w, w]
        return out + ["--chdir", str(Path(cwd).resolve() if cwd else root), "--"] + argv
    if plat == "darwin" and which("sandbox-exec"):
        allow = "".join(f'(subpath "{p}")' for p in _writable(root) + ["/private/tmp", "/private/var/folders"])
        profile = ('(version 1)(allow default)(deny file-write*)' + ("" if net else "(deny network*)") +
                   f'(allow file-write* {allow} (literal "/dev/null") (literal "/dev/dtracehelper") (regex #"^/dev/tty"))')
        return ["sandbox-exec", "-p", profile] + argv
    _say("unavailable", unavailable_notice(plat))
    return argv
