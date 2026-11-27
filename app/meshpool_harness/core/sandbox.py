"""Sandbox for the agent's shell commands (G-01, G-02, OSS-17).

On by default where the computer has the tool, except in Full access (MH_SANDBOX=1 forces it on, MH_SANDBOX=0 off).
Commands can read everything but write only inside the project folder, the temporary folders and the usual tool caches
(pip, npm, cargo), so a mistaken or hostile command - including a shell redirection - cannot damage the rest of the
computer. MH_SANDBOX_NETWORK=0 (Settings, or `meshpool --no-network`) also cuts the network.
Linux uses bubblewrap (bwrap --ro-bind / / plus writable binds, --unshare-net): the system's when it can start here,
else the copy the Linux app carries (vendor/bwrap, built by packaging/linux/build_bwrap.sh). bubblewrap needs
unprivileged user namespaces; where the kernel refuses them (or, on Ubuntu 23.10 and later, AppArmor refuses them to a
copy without a profile) neither starts, and commands run without it, as on Windows. macOS uses sandbox-exec with a
Seatbelt profile in the same shape Codex's (openai/codex, Apache-2.0) uses: allow default, deny file-write*, then allow
the listed folders; see THIRD_PARTY_NOTICES.md. Windows has neither, and says so once.
"""
from __future__ import annotations

import os
import shutil
import subprocess
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


_STARTS: dict = {}                   # bubblewrap program -> (it starts a sandbox here, what it said when it did not)


def _bundled_bwrap() -> str | None:
    """The bubblewrap the Linux app carries (<app>/vendor/bwrap/bwrap), when this build has it."""
    try:
        from meshpool_harness import bundled
        return bundled.bundled_program("bwrap")
    except Exception:  # noqa: BLE001
        return None


def bwrap_starts(path: str) -> tuple[bool, str]:
    """(bubblewrap at `path` can make its sandbox on this computer, its error when it cannot). Asked once per program:
    it needs unprivileged user namespaces, which some kernels turn off, and which Ubuntu 23.10 and later allow only to
    programs with an AppArmor profile (the distribution's /usr/bin/bwrap has one, a copy elsewhere does not)."""
    hit = _STARTS.get(path)
    if hit is None:
        try:
            r = subprocess.run([path, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-pid",
                                "--unshare-net", "--die-with-parent", "--", "true"], capture_output=True, text=True, timeout=15,
                               stdin=subprocess.DEVNULL)
            hit = (r.returncode == 0, " ".join((r.stderr or "").split())[:300])
        except (OSError, subprocess.SubprocessError) as e:
            hit = (False, str(e)[:300])
        _STARTS[path] = hit
    return hit


def bwrap_program(which=None) -> str:
    """The bubblewrap commands run in: "bwrap" (the system's, on PATH) or the full path of the app's own copy,
    whichever starts a sandbox here, the system's first (on Ubuntu its AppArmor profile lets it start where a copy
    cannot); "" when none can. A `which` given by the caller (tests) is taken at its word, without starting it."""
    real = which is None
    system = (which or shutil.which)("bwrap")
    cands = [("bwrap", system)] if system else []
    own = _bundled_bwrap() if real else None
    if own and not (system and os.path.realpath(own) == os.path.realpath(system)):
        cands.append((own, own))
    for name, path in cands:
        if not real or bwrap_starts(path)[0]:
            return name
    return ""


def available(platform: str | None = None, which=None) -> bool:
    plat = platform or sys.platform
    return (plat.startswith("linux") and bool(bwrap_program(which))) or \
        (plat == "darwin" and bool((which or shutil.which)("sandbox-exec")))


def _say(key: str, msg: str) -> None:
    if key not in _SAID:
        _SAID.add(key)
        print(f"[meshpool] {msg}", file=sys.stderr)


def install_command(os_release: str | None = None) -> str:
    """The command that installs bubblewrap on this Linux, from /etc/os-release (ID and ID_LIKE); "" when unknown."""
    if os_release is None:
        try:
            os_release = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
        except OSError:
            os_release = ""
    ids = []
    for line in os_release.splitlines():
        k, _, v = line.partition("=")
        if k.strip() in ("ID", "ID_LIKE"):
            ids += v.strip().strip('"').lower().split()
    for keys, cmd in ((("debian", "ubuntu", "linuxmint", "pop", "elementary", "raspbian"), "sudo apt install bubblewrap"),
                      (("fedora", "rhel", "centos", "rocky", "almalinux"), "sudo dnf install bubblewrap"),
                      (("arch", "manjaro", "endeavouros"), "sudo pacman -S bubblewrap"),
                      (("opensuse", "suse", "opensuse-leap", "opensuse-tumbleweed"), "sudo zypper install bubblewrap"),
                      (("alpine",), "sudo apk add bubblewrap"), (("void",), "sudo xbps-install bubblewrap"),
                      (("gentoo",), "sudo emerge sys-apps/bubblewrap"), (("nixos",), "add bubblewrap to your system packages")):
        if any(i in keys for i in ids):
            return cmd
    return ""


_ASKS = ("meshpool asks once before it runs ordinary commands in your folder without it, then only before risky steps, "
         "such as deleting files, using the internet or installing something. Undo covers the changes.")


def unavailable_notice(platform: str | None = None, which=None, os_release: str | None = None) -> str:
    """Why commands run without the sandbox, in plain words, with the one command that fixes it where there is one."""
    plat = platform or sys.platform
    if plat.startswith("win"):
        return "The command sandbox is not available on Windows. " + _ASKS
    if plat == "darwin":
        return "The command sandbox needs sandbox-exec, which this Mac does not have, so commands run without it."
    cmd = install_command(os_release)
    system = (which or shutil.which)("bwrap")
    if system or _bundled_bwrap():
        why = ("The command sandbox (bubblewrap) cannot start on this computer: the system does not let programs make "
               "the user namespaces it needs. ")
        # Ubuntu 23.10 and later allow them only to programs with an AppArmor profile; the distribution's package has
        # one, so installing it helps there. Where the kernel turns them off for everyone, nothing short of a system
        # setting does, and that is not asked of anyone.
        fix = f" To turn the sandbox on, run: {cmd}" if cmd and not system else ""
    else:
        why = "The command sandbox needs bubblewrap, which this computer does not have. "
        fix = f" To turn the sandbox on, run: {cmd}" if cmd else ""
    return why + _ASKS + fix


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


def _writable(root: Path, offline: bool = False) -> list[str]:
    home = Path.home()
    if offline:
        # a command that ran without a question (review, 0.0.18): no ~/.local/bin, ~/.cargo/env or pip config to
        # plant code in that later runs outside the sandbox. Package folders are for installs, and those ask.
        cands = [str(root), "/tmp", "/var/tmp", str(home / ".cache")]
    else:
        cands = [str(root), "/tmp", "/var/tmp", str(home / ".cache"), str(home / ".npm"), str(home / ".cargo"),
                 str(home / ".local"), str(home / ".meshpool"), str(home / ".config" / "pip"),
                 str(home / ".pnpm-store")]
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


_SECRET_DIRS = (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure", ".password-store", ".config/gh",
                ".config/gcloud", ".config/hub", ".config/op", ".terraform.d")
_SECRET_FILES = (".netrc", ".git-credentials", ".npmrc", ".pypirc", ".pgpass", ".my.cnf", ".config/git/credentials")
_DATA_SECRETS = ("meshpool.config", "mcp.json", "mcp-auth.json", "connectors.json", "harnesses")


def _hidden(root: Path) -> tuple[list[str], list[str]]:
    """(folders, files) a command that ran without a question may not read (review, 0.0.18): keys and tokens in the
    home folder and meshpool's own settings (the API key), unless the chat works inside one of them."""
    home = Path.home()
    dirs = [home / d for d in _SECRET_DIRS]
    files = [home / f for f in _SECRET_FILES]
    data = _data_folder()
    if data:
        for n in _DATA_SECRETS:
            (dirs if n == "harnesses" else files).append(Path(data) / n)
    try:
        from meshpool_harness import config as C
        files.append(Path(C.config_path()))
    except Exception:  # noqa: BLE001
        pass
    r = os.path.realpath(str(root))
    keep = lambda p: not (_under(r, os.path.realpath(str(p))) or _under(os.path.realpath(str(p)), r))  # noqa: E731
    d = [os.path.realpath(str(p)) for p in dirs if p.is_dir() and keep(p)]
    f = [os.path.realpath(str(p)) for p in files if p.is_file() and keep(p)]
    return list(dict.fromkeys(d)), list(dict.fromkeys(f))


def _locked(root: Path) -> list[str]:
    """Files a command that ran without a question may not change, though they sit in a writable folder: git's hooks
    and config (they run programs at the next commit, outside the sandbox) and the project's .meshpool rules."""
    out = []
    for g in dict.fromkeys(_git_dirs(root) + [str(Path(root) / ".git")]):
        for n in ("hooks", "config"):
            p = Path(g) / n
            if p.exists():
                out.append(os.path.realpath(str(p)))
    m = Path(root) / ".meshpool"
    if m.exists():
        out.append(os.path.realpath(str(m)))
    return list(dict.fromkeys(out))


def _office_profile() -> str | None:
    """A LibreOffice user profile for sandboxed commands, kept in ~/.cache so it is made once, not per command.
    LibreOffice writes its profile at every start. The usual one (~/.config/libreoffice, ~/Library/Application
    Support/LibreOffice) is read-only in the sandbox, so `soffice --convert-to` crashed at once with a
    DeploymentException. Its own bootstrap reads the UserInstallation variable from the environment; a command
    that passes -env:UserInstallation still wins. None when the user set the variable, or the folder cannot be made."""
    if os.environ.get("UserInstallation"):
        return None
    d = Path.home() / ".cache" / "meshpool" / "sandbox-office-profile"
    if not d.is_dir():
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
    return str(d)


def wrap(argv: list[str], root, platform: str | None = None, which=None, cwd=None, mode: str | None = None,
         offline: bool = False, force: bool = False) -> list[str]:
    """argv, wrapped so it can only write where it should. Unchanged when the sandbox is off or unavailable (with a
    notice, once, when it was wanted). offline: no network for this command (one that ran without a question).
    force: sandboxed whenever this computer can, whatever MH_SANDBOX says (code meshpool runs on its own, such as a
    saved tool's tests, never needs the user's own commands to be unsandboxed)."""
    if not force and not requested(mode):
        return argv
    plat = platform or sys.platform
    root = Path(root).resolve()
    net = network_allowed() and not offline
    bw = bwrap_program(which) if plat.startswith("linux") else ""
    if bw:
        out = [bw, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--die-with-parent", "--unshare-pid"]
        if not net:
            out.append("--unshare-net")
        ws = _writable(root, True) if offline else _writable(root)
        for w in ws:
            out += ["--bind", w, w]
        if offline:
            home = os.path.realpath(str(Path.home()))
            if home not in ("/", "") and os.path.isdir(home):
                out += ["--ro-bind", home, home]        # nothing in home is writable, wherever it sits (even in /tmp)
                for w in ws:
                    if _under(os.path.realpath(w), home):
                        out += ["--bind", w, w]          # ...but the folder itself and ~/.cache
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
        prof = _office_profile()
        if prof:
            out += ["--bind", prof, prof, "--setenv", "UserInstallation", Path(prof).as_uri()]
        if offline:                                     # last, so no bind above undoes them
            hd, hf = _hidden(root)
            for d in hd:
                out += ["--tmpfs", d]
            for f in hf:
                out += ["--ro-bind", "/dev/null", f]
            for p in _locked(root):
                out += ["--ro-bind", p, p]
        return out + ["--chdir", str(Path(cwd).resolve() if cwd else root), "--"] + argv
    if plat == "darwin" and (which or shutil.which)("sandbox-exec"):
        ws = _writable(root, True) if offline else _writable(root)
        allow = "".join(f'(subpath "{p}")' for p in ws + ["/private/tmp", "/private/var/folders"])
        profile = ('(version 1)(allow default)(deny file-write*)' + ("" if net else "(deny network*)") +
                   f'(allow file-write* {allow} (literal "/dev/null") (literal "/dev/dtracehelper") (regex #"^/dev/tty"))')
        data = _data_folder()
        if data and any(_under(data, os.path.realpath(w)) for w in ws):
            # as for bwrap above: meshpool's data folder (settings, permissions.json, hooks.json) is read-only when a
            # writable folder covers it, except the folders a command works in. The last matching rule wins.
            back = [os.path.realpath(w) for w in ws]
            back = [rw for rw in dict.fromkeys(back) if _under(rw, data) and (rw != data or rw == str(root))]
            profile += f'(deny file-write* (subpath "{data}"))'
            if back:
                profile += "(allow file-write* " + "".join(f'(subpath "{rw}")' for rw in back) + ")"
        if offline:
            home = os.path.realpath(str(Path.home()))
            back = [os.path.realpath(w) for w in ws if _under(os.path.realpath(w), home)]
            if home not in ("/", ""):
                profile += f'(deny file-write* (subpath "{home}"))'
                if back:
                    profile += "(allow file-write* " + "".join(f'(subpath "{b}")' for b in back) + ")"
        prof = _office_profile()
        if prof:
            profile += f'(allow file-write* (subpath "{os.path.realpath(prof)}"))'
        if offline:                                     # last: the last matching rule wins
            hd, hf = _hidden(root)
            if hd or hf:
                profile += "(deny file-read* " + "".join(f'(subpath "{d}")' for d in hd) + \
                           "".join(f'(literal "{f}")' for f in hf) + ")"
            lk = _locked(root)
            if lk:
                profile += "(deny file-write* " + "".join(f'(subpath "{p}")' for p in lk) + ")"
        if prof:
            return ["sandbox-exec", "-p", profile, "/usr/bin/env", f"UserInstallation={Path(prof).as_uri()}"] + argv
        return ["sandbox-exec", "-p", profile] + argv
    _say("unavailable", unavailable_notice(plat))
    return argv
