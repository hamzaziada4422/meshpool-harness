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


def _writable(root: Path) -> list[str]:
    home = Path.home()
    cands = [str(root), "/tmp", "/var/tmp", str(home / ".cache"), str(home / ".npm"), str(home / ".cargo"),
             str(home / ".local"), str(home / ".meshpool"), str(home / ".config" / "pip"), str(home / ".pnpm-store")]
    extra = os.environ.get("MH_SANDBOX_WRITE", "")
    cands += [x for x in extra.split(os.pathsep) if x]
    return [c for c in dict.fromkeys(cands) if os.path.isdir(c)]


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
        for w in _writable(root):
            out += ["--bind", w, w]
        return out + ["--chdir", str(Path(cwd).resolve() if cwd else root), "--"] + argv
    if plat == "darwin" and which("sandbox-exec"):
        allow = "".join(f'(subpath "{p}")' for p in _writable(root) + ["/private/tmp", "/private/var/folders"])
        profile = ('(version 1)(allow default)(deny file-write*)' + ("" if net else "(deny network*)") +
                   f'(allow file-write* {allow} (literal "/dev/null") (literal "/dev/dtracehelper") (regex #"^/dev/tty"))')
        return ["sandbox-exec", "-p", profile] + argv
    _say("unavailable", unavailable_notice(plat))
    return argv
