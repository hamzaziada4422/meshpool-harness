"""Core tools: strict schemas, validation with precise errors, sandboxed execution in the run's workdir."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


OUTPUT_HEAD, OUTPUT_TAIL, OUTPUT_KILL_CHARS = 200_000, 200_000, 64_000_000
SKIP_DIRS = frozenset({".git", ".jj", ".sl", ".hg", "node_modules", "__pycache__", ".venv", "venv", "build", "dist", "target"})   # H-11
MAX_GREP_FILE = 5_000_000                       # H-11: bytes read per file by grep
_WIN_RESERVED = frozenset({"con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8",
                           "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"})


def reserved_name(path: str) -> str | None:
    """H-22: a Windows device name (nul, con, aux ...) as a file name: creating it makes a file Explorer cannot delete."""
    for part in re.split(r"[\\/]", str(path)):
        if part.split(".")[0].strip().lower() in _WIN_RESERVED and part:
            return part
    return None


def _git_dir_name(part: str) -> bool:
    """A path part that is the repository's .git folder on some file system: any letter case (.GIT on macOS APFS and
    Windows NTFS), with the trailing dots or spaces Windows drops, or its 8.3 short name (GIT~1)."""
    p = part.lower()
    return p.rstrip(". ") == ".git" or bool(re.fullmatch(r"git~\d+", p))


def fix_win_path(p):
    """OSS-28: a drive-letter path a model or a URI gave as /C:\\Users\\x, /C:/x or file:///C:/x becomes C:\\Users\\x."""
    if not isinstance(p, str):
        return p
    q = re.sub(r"^file:///(?=[A-Za-z]:)", "", p)
    return re.sub(r"^[/\\]+(?=[A-Za-z]:([\\/]|$))", "", q)


_NUL_REDIRECT = re.compile(r"(?<![\w/])((?:\d|&)?>>?)\s*nul\b(?!\.)", re.I)


def fix_nul_redirect(command: str) -> str:
    """H-22: `2>nul` in Git Bash makes a real file called nul (undeletable in Explorer); /dev/null is what was meant."""
    return _NUL_REDIRECT.sub(lambda m: m.group(1) + " /dev/null", command)


def _quiet_close(f) -> None:
    try:
        f.close()
    except Exception:  # noqa: BLE001
        pass


class _Capped:
    """A command's output kept as its first and last characters only, so a runaway producer cannot fill memory."""

    def __init__(self):
        self.head, self.tail, self.total, self.dropped = [], "", 0, 0
        self._hn = 0

    def add(self, chunk: str):
        self.total += len(chunk)
        if self._hn < OUTPUT_HEAD:
            take = chunk[:OUTPUT_HEAD - self._hn]
            self.head.append(take)
            self._hn += len(take)
            chunk = chunk[len(take):]
        if chunk:
            self.tail += chunk
            if len(self.tail) > 2 * OUTPUT_TAIL:
                self.dropped += len(self.tail) - OUTPUT_TAIL
                self.tail = self.tail[-OUTPUT_TAIL:]

    def text(self) -> str:
        head = "".join(self.head)
        if not self.dropped:
            return head + self.tail
        return head + f"\n... [{self.dropped} characters of output dropped] ...\n" + self.tail


def _bash():
    from meshpool_harness.config import find_bash
    return find_bash()

MAX_TOOL_CHARS = 8000
HEAD, TAIL = 3000, 3000
_CAP_LOCK = threading.Lock()

_RO_PROGS = {"cat", "head", "tail", "ls", "find", "grep", "egrep", "fgrep", "rg", "wc", "file", "stat", "which",
             "type", "echo", "printf", "pwd", "uname", "du", "df", "tree", "nl", "sort", "uniq",
             "cut", "tr", "diff", "cmp", "md5sum", "sha256sum", "sha1sum", "xxd", "od", "hexdump", "strings", "jq",
             "readlink", "realpath", "basename", "dirname", "id", "whoami", "date", "ps", "true", "column", "less",
             "awk", "sed", "git", "pip", "pip3", "python", "python3", "node", "npm", "cargo", "go", "java", "gcc",
             "rustc", "ldd", "nproc", "free", "hostname", "lsb_release", "getconf", "test", "["}
_RO_GIT = {"log", "show", "diff", "status", "grep", "ls-files", "blame", "rev-parse", "branch", "remote", "describe",
           "shortlog", "cat-file", "ls-tree", "tag"}


def _git_subcommand(rest: list) -> list:
    """git's arguments from the subcommand on, past the global options that only pick a folder or the pager (-C dir,
    --no-pager). [] for any other global option: -c core.fsmonitor=... and --config-env run commands from
    settings, --exec-path, --git-dir and --work-tree point git at other programs or another repository."""
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        a = rest[i]
        if a == "--no-pager":
            i += 1
        elif a == "-C" and i + 1 < len(rest):
            i += 2
        else:
            return []
    return rest[i:]


def _brace_expand(pat: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", pat)
    if not m:
        return [pat]
    return [x for alt in m.group(1).split(",") for x in _brace_expand(pat[:m.start()] + alt + pat[m.end():])]


def _glob_rx(pat: str):
    out, i = "", 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if pat.startswith("**", i):
            out += ".*"
            i += 2
            continue
        out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
        i += 1
    return re.compile(out + r"\Z", re.S)


def glob_match(rel: str, glob: str) -> bool:
    """ripgrep-like file filter: comma-less braces ({py,txt}), ** across folders (also none), a leading ! to exclude,
    a pattern without / matches the file name at any depth, one with / matches the path from the project root."""
    rel = rel.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    pos, neg = [], []
    for g in re.split(r"\s+", glob.strip()):
        if not g:
            continue
        (neg if g.startswith("!") else pos).append(g.lstrip("!"))

    def one(p: str) -> bool:
        for q in _brace_expand(p):
            q = q[2:] if q.startswith("./") else q
            if "/" not in q:
                if fnmatch.fnmatch(name, q):
                    return True
            elif _glob_rx(q.lstrip("/")).match(rel):
                return True
        return False
    return (not pos or any(one(p) for p in pos)) and not any(one(n) for n in neg)


def read_only(cmd: str) -> bool:
    """Conservative check that a shell command only reads (safe to run beside others). Unknown means False."""
    import re
    import shlex
    s = re.sub(r"\d?>\s*/dev/null|2>&1|>&2", " ", cmd)
    if re.search(r"[>`]|\$\(|<\(|<<|\bxargs\b|-exec\b|-execdir\b|-ok\b|-delete\b|\bsudo\b", s):
        return False
    for seg in re.split(r"\|\|?|&&|;|\n", s):
        try:
            w = shlex.split(seg)
        except ValueError:
            return False
        while w and re.match(r"^[A-Za-z_]\w*=", w[0]):             # FOO=bar cmd
            w = w[1:]
        if not w:
            continue
        prog, rest = w[0].rsplit("/", 1)[-1], w[1:]
        if prog not in _RO_PROGS:
            return False
        if prog == "sed" and any(a.startswith("-i") or a == "--in-place" for a in rest):
            return False
        if prog == "awk" and ("system" in seg or "getline" in seg):
            return False
        if prog == "sort" and any(a == "-o" or a.startswith(("--output", "-o")) for a in rest):
            return False
        if prog == "find" and any(a in ("-fprint", "-fprint0", "-fprintf", "-fls") for a in rest):
            return False
        if prog == "sed" and any(re.search(r"(^|[;\s{])[wWeE]\s*\S|^s(.).*?\1.*?\1[gIpm0-9]*[we]", a)
                                 for a in rest if not a.startswith("-")):
            return False
        if prog in ("rg", "grep", "egrep", "fgrep") and any(a in ("-z", "--search-zip", "--pre") or a.startswith("--pre=")
                                                           for a in rest):
            return False
        if prog == "git":
            rest = _git_subcommand(rest)
            if not (rest and rest[0] in _RO_GIT):
                return False
            if any(a.startswith(("--output", "--ext-diff", "--textconv", "-O", "--open-files-in-pager")) for a in rest):
                return False
            if rest[0] in ("branch", "tag", "remote"):
                pos = [a for a in rest[1:] if not a.startswith("-")]
                bad = {"-d", "-D", "-m", "-M", "-c", "-C", "-u", "-f", "-a", "--delete", "--move", "--copy", "--force",
                       "--set-upstream-to", "--unset-upstream", "--edit-description", "-s", "-S", "-F", "-e"}
                if rest[0] == "remote":
                    if pos and pos[0] not in ("show", "get-url"):
                        return False
                elif pos and not any(a in ("-l", "--list", "--contains", "--merged", "--no-merged", "--points-at")
                                     for a in rest[1:]):
                    return False                     # `git branch x` / `git tag v1` create refs
                if any(a.split("=")[0] in bad - {"-a"} for a in rest[1:]):
                    return False
        if prog in ("pip", "pip3") and not (rest and rest[0] in ("show", "list", "freeze", "--version", "-V")):
            return False
        if prog in ("python", "python3", "node", "npm", "cargo", "go", "java", "gcc", "rustc") and not (
                rest and (rest[0] in ("--version", "-V", "version", "-version")
                          or rest[:3] in (["-m", "pip", "show"], ["-m", "pip", "list"]))):
            return False
    return True


# Allowlist, not blocklist (review H1, H2, M1, M8): a token is plain characters only, so no shell (bash, sh or
# PowerShell) can expand it; every flag is one this subcommand is known to read with; paths stay in the project.
_GIT_TOKEN = re.compile(r"^[A-Za-z0-9_.,/:=^~+%-]+$")
_GIT_FLAGS = {
    "status": {"-s", "--short", "-b", "--branch", "--porcelain", "--porcelain=v1", "--porcelain=v2", "-v", "--long",
               "-uno", "-unormal", "-uall", "--untracked-files=no", "--untracked-files=normal",
               "--untracked-files=all", "--ignored"},
    "log": {"--oneline", "--graph", "--decorate", "--stat", "--shortstat", "--name-only", "--name-status", "-p",
            "--patch", "--all", "--no-merges", "--merges", "--reverse", "--first-parent", "--abbrev-commit",
            "--date=short", "--date=iso", "--date=relative", "--no-color", "--follow"},
    "show": {"--stat", "--shortstat", "--name-only", "--name-status", "--oneline", "-s", "--no-patch", "-p",
             "--patch", "--no-color", "--abbrev-commit"},
    "diff": {"--stat", "--shortstat", "--numstat", "--name-only", "--name-status", "--cached", "--staged", "-w",
             "--ignore-all-space", "--no-color", "--word-diff", "--summary", "--check"},
    "blame": {"-w", "-s", "--porcelain", "-e"},
    "rev-parse": {"--abbrev-ref", "--show-toplevel", "--short", "--verify", "--is-inside-work-tree", "--git-dir",
                  "--show-prefix", "--symbolic-full-name"},
    "ls-files": {"-m", "--modified", "-o", "--others", "--exclude-standard", "-d", "--deleted", "-s", "--stage",
                 "-c", "--cached"},
    "describe": {"--tags", "--always", "--long", "--abbrev=0", "--dirty"},
    "shortlog": {"-s", "-n", "-sn", "-ns", "-e", "--summary", "--numbered", "--all"},
    "rev-list": {"--count", "--all", "--max-count=1"},
    "merge-base": {"--is-ancestor"},
    "ls-tree": {"-r", "--name-only", "-l"},
    "cat-file": {"-t", "-s", "-p"},
    "branch": {"-a", "-r", "-v", "-vv", "--all", "--remotes", "--list", "-l", "--show-current", "--merged",
               "--no-merged"},
    "tag": {"-l", "--list"},
    "remote": {"-v", "--verbose"},
    "stash": set(),
    "clean": {"-n", "--dry-run", "-d", "-nd", "-dn", "-x", "-nx", "-xn", "-ndx", "-nxd", "-dnx", "-dxn", "-xnd", "-xdn",
              "-X", "-nX", "-Xn", "-ndX", "-nXd", "-dnX", "-dXn", "-Xnd", "-Xdn"},   # only a dry run: it deletes nothing
}
_GIT_VALUE_FLAGS = ("-n", "--max-count=", "--since=", "--until=", "--author=", "-L", "-U", "--unified=",
                    "--format=", "--pretty=")      # flags that carry a value, checked as plain tokens too
_GIT_NO_POSITIONAL = {"branch", "tag", "remote", "status"}       # their positional words create or change things


def _git_path_ok(tok: str) -> bool:
    """A ref, range or path argument: never absolute, never from home, never climbing out with .."""
    t = tok.split(":", 1)[-1] if ":" in tok and not tok.startswith(":") else tok
    if tok.startswith(("/", "~", ":")) or t.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", tok):
        return False
    return ".." not in t.replace("...", "").split("/") if "/" in t else t != ".."


def git_read_only(cmd: str) -> bool:
    """True only for one git command that reads this repository: no shell expansion is possible (plain characters
    only), every flag comes from a list of flags known to read, and no path leaves the project. Everything else
    asks. The caller also checks the repo's own config (git_config_safe), since git status and diff can run
    programs named there."""
    if re.search(r"[^\x20-\x7e]", cmd or ""):              # a CR, tab or Unicode break separates statements somewhere
        return False
    w = (cmd or "").split()
    if len(w) < 2 or w[0] != "git" or not all(_GIT_TOKEN.match(t) for t in w):
        return False
    if any("%" in t and not t.startswith(("--format=", "--pretty=")) for t in w):
        return False                                      # % only in a log format, never elsewhere
    rest = w[1:]
    if rest[0] == "--no-pager":
        rest = rest[1:]
    if not rest or rest[0] not in _GIT_FLAGS:
        return False
    sub, args = rest[0], rest[1:]
    if sub == "stash":                                    # only "git stash list"
        return args == ["list"]
    if sub == "tag" and not args:
        return True
    if sub == "remote":                                   # "git remote" and "git remote -v": no network
        return all(a in _GIT_FLAGS["remote"] for a in args)
    if sub == "clean" and not any(a == "--dry-run" or (a.startswith("-") and not a.startswith("--") and "n" in a)
                                  for a in args):
        return False                                      # git clean without -n deletes files
    after_dashdash = False
    for a in args:
        if a == "--":
            after_dashdash = True
            continue
        if after_dashdash and a.startswith("-"):           # a shell that drops "--" would make it an option
            return False
        if a.startswith("-") and not after_dashdash:
            if a in _GIT_FLAGS[sub] or (re.fullmatch(r"-\d{1,4}", a) and sub in ("log", "shortlog", "show")):
                continue
            if any(a.startswith(v) and len(a) > len(v) for v in _GIT_VALUE_FLAGS) and sub in ("log", "show", "diff",
                                                                                                 "blame", "shortlog"):
                continue
            return False
        if sub in _GIT_NO_POSITIONAL or not _git_path_ok(a):
            return False
    return True


_GIT_RISKY_CONFIG = re.compile(r"(fsmonitor|pager|external|textconv|command|program|askpass|editor|proxy|hookspath|"
                               r"worktree|^include|^includeif|^filter\.|^alias\.|^gpg\.|sshcommand)", re.I)


_GIT_SAFE_KEY = re.compile(
    r"^(core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks|autocrlf|"
    r"eol|safecrlf|quotepath|longpaths|checkstat|trustctime)|user\.[a-z]+|remote\.[^.]+\.(url|fetch|pushurl)|"
    r"branch\..+\.(remote|merge|rebase|description)|pull\.(rebase|ff)|init\.defaultbranch|gc\.auto|color\.[a-z.]+|"
    r"commit\.gpgsign|merge\.ff|push\.(default|autosetupremote)|fetch\.prune|lfs\.repositoryformatversion|"
    r"maintenance\.(auto|strategy)|index\.(version|skiphash)|extensions\.objectformat|status\.showuntrackedfiles)$",
    re.I)                                                  # anything else in a repo's own config: the shortcut is off


# What meshpool passes to every git call that only looks at a repository (status, log, diff): the repository's own
# config must not run programs then (fsmonitor, an external diff, a pager, hooks). ui/workbench.py does the same.
SAFE_GIT_OPTS = ["-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "diff.external=", "-c",
                 f"core.hooksPath={os.devnull}", "--no-pager"]


def safe_git(*args: str) -> list:
    """argv for a read-only git call that never runs the repository's own programs."""
    return ["git", *SAFE_GIT_OPTS, *args]


def git_config_runs_programs(root) -> bool:
    """True when the repository's own config names a program that a checkout, a diff or a merge would run (a filter,
    an external diff or textconv, an include, an ssh command). Narrower than git_config_safe: submodules and unknown
    keys are fine here, and fsmonitor and hooks are not counted because worktrees.py turns them off on every call."""
    try:
        r = subprocess.run(["git", "-C", str(root), "config", "--name-only", "--list", "--show-scope"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    if r.returncode not in (0, 1):
        return True
    for line in r.stdout.splitlines():
        scope, _, key = line.partition("\t")
        if scope in ("local", "worktree") and _GIT_RISKY_CONFIG.search(key) and \
                not re.search(r"fsmonitor|hookspath", key, re.I):
            return True
    return False


def git_config_safe(root) -> bool:
    """False when the repository's own config (.git/config, which a downloaded project can ship) names a program
    git would run on a read: core.fsmonitor, a pager, an external diff, a textconv, an include, an alias.

    It ran two git processes on every git command the agent wanted to run without asking. A "safe" answer is now kept
    for the folder while nothing it read has changed: the folders from here up to the .git it found, the .git entry,
    the git dir and its config files, the hooks folder and the user's own git config files (each by modification time,
    size and inode), and the GIT_* environment. Only a "safe" answer is kept, never for files changed in the last few
    seconds (a change in the same clock tick would not show), and for two minutes at most. Anything else asks git."""
    key = os.path.abspath(str(root))
    envk = _git_env_key()
    with _GCS_LOCK:
        hit = _GCS.get(key)
    if hit is not None:
        paths, stamp, env0, at = hit
        if env0 == envk and 0 <= time.time() - at < _GCS_MAX_AGE and _git_stamp(paths) == stamp:
            return True
    t0 = time.time()
    ok, hooks = _git_config_check(root)
    with _GCS_LOCK:
        _GCS.pop(key, None)
    if ok and hooks is not None:
        paths = _git_safe_paths(key, hooks)
        stamp = _git_stamp(paths) if paths else None
        if stamp and all(s[1] is None or s[1] < (t0 - _GCS_RACY_S) * 1e9 for s in stamp):
            with _GCS_LOCK:
                _GCS[key] = (paths, stamp, envk, t0)
    return ok


_GCS: dict = {}                      # folder -> (paths read, their stamps, GIT_* environment, when): "safe" answers
_GCS_LOCK = threading.Lock()
_GCS_MAX_AGE = 120.0
_GCS_RACY_S = 3.0                    # FAT keeps times in 2 s steps: a newer file may change again unseen


def _git_env_key() -> tuple:
    return tuple(sorted((k, v) for k, v in os.environ.items()
                        if k.startswith("GIT_") or k in ("HOME", "XDG_CONFIG_HOME", "PREFIX", "PATH")))


def _git_safe_paths(root: str, hooks: Path) -> list | None:
    """What a git_config_safe answer for root depends on, as files and folders to stat; None when the .git cannot be
    found the way git finds it (the answer is then not kept)."""
    here = Path(root)
    dirs, dotgit = [], None
    for d in (here, *here.parents):                  # git looks up from the folder: a new .git on the way is a new
        dirs.append(d)                               # repo, and it changes that folder's modification time
        if os.path.lexists(d / ".git"):
            dotgit = d / ".git"
            break
    if dotgit is None:
        return None
    gitdir = dotgit
    try:
        if dotgit.is_file():                         # a worktree or submodule: "gitdir: <path>"
            txt = dotgit.read_text(encoding="utf-8", errors="replace").strip()
            if not txt.startswith("gitdir:"):
                return None
            g = Path(txt[7:].strip())
            gitdir = g if g.is_absolute() else dotgit.parent / g
        common = gitdir
        cf = gitdir / "commondir"
        if cf.is_file():
            c = Path(cf.read_text(encoding="utf-8", errors="replace").strip())
            common = c if c.is_absolute() else gitdir / c
    except OSError:
        return None
    home = Path(os.path.expanduser("~"))
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    files = [gitdir / "config", gitdir / "config.worktree", gitdir / "commondir", common / "config",
             common / "config.worktree", here / ".gitmodules", home / ".gitconfig", xdg / "git" / "config",
             Path("/etc/gitconfig")]
    files += [Path(os.environ[k]) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM") if os.environ.get(k)]
    # the git dir itself only by identity: git status rewrites the index in it, which changes its time every call
    out = [(d, False) for d in dirs] + [(dotgit, True), (gitdir, True), (common, True)] + [(f, False) for f in files]
    out.append((Path(hooks), False))                 # adding or renaming a hook changes the folder's time
    return out


def _git_stamp(paths) -> tuple:
    out = []
    for p, ident in paths:
        try:
            st = os.stat(p)
            out.append((str(p), None if ident else st.st_mtime_ns, None if ident else st.st_size, st.st_ino,
                        st.st_mode))
        except OSError:
            out.append((str(p), None))
    return tuple(out)


def _git_config_check(root) -> tuple[bool, Path | None]:
    """(safe, the hooks folder git uses): git_config_safe without the kept answer."""
    try:
        r = subprocess.run(["git", "-C", str(root), "config", "--name-only", "--list", "--show-scope"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False, None
    if r.returncode not in (0, 1):
        return False, None
    for line in r.stdout.splitlines():   # global, system and command (-c, GIT_CONFIG_PARAMETERS) scopes are the user's
        scope, _, key = line.partition("\t")
        if scope in ("local", "worktree") and (_GIT_RISKY_CONFIG.search(key) or not _GIT_SAFE_KEY.match(key)):
            return False, None
    if (Path(root) / ".gitmodules").exists():           # submodules have configs and hooks of their own
        return False, None
    try:                                                  # hooks in the repo's own .git/hooks run on some reads
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", "hooks"], capture_output=True, text=True,
                           timeout=10)
        h = r.stdout.strip()
        hooks = (Path(h) if os.path.isabs(h) else Path(root) / h) if r.returncode == 0 and h else None
        if hooks is not None and hooks.is_dir() and any(f.is_file() and not f.name.endswith(".sample") for f in hooks.iterdir()):
            return False, None
    except (OSError, subprocess.SubprocessError):
        return False, None
    return True, hooks


_PATCH_CMDS = ("apply_patch", "applypatch")
_HEREDOC = re.compile(r"\A\s*(?:cd\s+(?P<cd>'[^']*'|\"[^\"]*\"|[^\s&;|<>]+)\s*&&\s*)?(?:apply_patch|applypatch)\s*"
                      r"<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)[ \t]*\n(?P<body>.*)\n[ \t]*(?P=tag)[ \t]*\n?\s*\Z", re.S)


def shell_script(argv) -> str:
    """The script a shell tool call runs: argv as the command list Codex sends, or a string."""
    import shlex
    if isinstance(argv, str):
        return argv
    argv = [str(a) for a in argv or []]
    if len(argv) >= 3 and os.path.basename(argv[0]) in ("bash", "sh", "zsh") and argv[1] in ("-c", "-lc"):
        return argv[2]
    if len(argv) == 1:
        return argv[0]
    if os.path.basename(argv[0]) not in ("bash", "sh", "zsh") and any(_ARGV_OP.match(a) for a in argv[1:]) \
            and not _pattern_is_operator(argv):
        # swe6 traces (D0, 7 calls in 23 runs): ["python", "t.py", "2>&1", "|", "tail"] meant a pipeline; joined as
        # quoted words it ran python with "|" as an argument and failed. Operators standing alone stay operators. The
        # permission checks read this same script (permrules.commands_of), so they judge what really runs.
        execs = any(a in ("-exec", "-execdir", "-ok", "-okdir") for a in argv)   # find -exec ... ; ends with ";"
        return " ".join(a if _ARGV_OP.match(a) and not (execs and a == ";") else shlex.quote(a) for a in argv)
    return shlex.join(argv)


def _pattern_is_operator(argv) -> bool:
    """A search whose pattern is itself an operator character (["grep", "-c", "|", "notes.md"]): the "|" is the text
    searched for, not a pipe (code review). The pattern is the first argument that is not an option (or -e's value)."""
    if os.path.basename(argv[0]) not in ("grep", "egrep", "fgrep", "rg", "ag", "ack"):
        return False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-e", "--regexp"):
            return i + 1 < len(argv) and bool(_ARGV_OP.match(argv[i + 1]))
        if a == "--":
            return i + 1 < len(argv) and bool(_ARGV_OP.match(argv[i + 1]))
        if a.startswith("-") and a != "-":
            i += 1
            continue
        return bool(_ARGV_OP.match(a))
    return False


# An argv element that is a shell operator on its own: | || && ; & > >> 2> 2>&1 &> < and a redirect to /dev/null
_ARGV_OP = re.compile(r"^(\|\||\||&&|;|&|\d?>>?|\d?>&\d|&>>?|<|\d?>/dev/null)$")


def patch_from_shell(argv):
    """(patch text, working folder or None) when a shell call is Codex's apply_patch through the shell, as the Codex
    prompt shows it: ["apply_patch", "<patch>"], or a bash script `[cd dir &&] apply_patch <<'EOF' ... EOF`. Else None."""
    if isinstance(argv, (list, tuple)) and len(argv) == 2 and str(argv[0]) in _PATCH_CMDS:
        return str(argv[1]), None
    script = shell_script(argv)
    m = _HEREDOC.match(script)
    if not m:
        return None
    cd = (m.group("cd") or "").strip("'\"") or None
    return m.group("body"), cd


_PATCH_HEAD = re.compile(r"\A\s*(?:cd\s+(?P<cd>'[^']*'|\"[^\"]*\"|[^\s&;|<>]+)\s*&&\s*)?(?:apply_patch|applypatch)\s*"
                         r"<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)(?P<inline>[^\n]*)\n")


def split_patch_script(argv):
    """(patch, working folder or None, rest, rest only if the patch applied) for a script that starts with an
    apply_patch heredoc and then runs other commands: `apply_patch <<'EOF' ... EOF` and `python -m pytest` on the
    next line (Omar, 0.0.19: such a script was judged as one command of 50 "steps" and refused in Accept edits).
    The rest keeps the `cd dir &&` the script started with. None when the script is not of that form, or when the
    heredoc line pipes or redirects (`apply_patch <<EOF | tee`): those are judged as one command."""
    script = argv if isinstance(argv, str) else shell_script(argv)
    m = _PATCH_HEAD.match(script or "")
    if not m:
        return None
    tag, lines = m.group("tag"), script[m.end():].split("\n")
    end = next((i for i, ln in enumerate(lines) if ln.strip() == tag), None)
    if end is None:
        return None
    body, after = "\n".join(lines[:end]), "\n".join(lines[end + 1:]).strip()
    inline, strict = m.group("inline").strip(), False
    if inline:
        mm = re.match(r"(&&|;)\s*(.*)\Z", inline, re.S)
        if not mm:
            return None
        strict, inline = mm.group(1) == "&&", mm.group(2).strip()
    rest = "\n".join(x for x in (inline, after) if x).strip()
    cd = (m.group("cd") or "").strip("'\"") or None
    if rest and m.group("cd"):
        rest = f"cd {m.group('cd')} && {rest}"          # the cd holds for the whole script, as in the shell
    return body, cd, rest, strict


_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def expand_path(p: str) -> str:
    """A path as a shell would read it: ~ and ~user, $HOME, ${VAR} and $VAR expanded. Omar, 0.0.19: an apply_patch to
    `$HOME/omar_notes.txt` made a folder named "$HOME" in the project. A variable that is not set is an error, never
    an empty string or a literal "$NAME" folder."""
    s = str(p or "")
    if "$" in s:
        def var(m):
            name = m.group(1) or m.group(2)
            v = os.environ.get(name)
            if v is None and name in ("HOME", "USERPROFILE"):
                v = os.path.expanduser("~")
            if v is None:
                raise ToolError(f"the path {s} names ${name}, which is not set here. Give the full path instead.")
            return v
        s = _VAR.sub(var, s)
    return os.path.expanduser(s) if s.startswith("~") else s


_BG_ALL: set = set()


def _kill_tree(p) -> None:
    """End a process and everything it started."""
    import signal
    if p.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=0x08000000)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def _kill_all_background() -> None:
    for p in list(_BG_ALL):
        _kill_tree(p)


__import__("atexit").register(_kill_all_background)


def exit_cleanly_on_sigterm() -> None:
    """SIGTERM (a supervisor, a benchmark, `kill`) skips atexit by default, so commands this process started in their
    own process group outlived it and kept running (eval: check scripts at 99% CPU minutes after their agent was
    gone). Turn it into a normal exit, so _kill_all_background runs. Only from the main thread, only when no one
    else has set a handler."""
    import signal
    import threading
    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        return
    try:
        if signal.getsignal(signal.SIGTERM) in (signal.SIG_DFL, None):
            signal.signal(signal.SIGTERM, lambda *_: (_kill_all_background(), __import__("sys").exit(143)))
    except (ValueError, OSError):
        pass


class AllStr(str):
    """An `old` text that edit replaces everywhere (replace_all)."""


def _fn(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": req, "additionalProperties": False}}}


S = {"type": "string"}
I = {"type": "integer"}
SCHEMAS = {
    "read_file": _fn("read_file", "Read a file. Text and code come back with line numbers (use start/end, 1-based, "
                     "for large files). PDF, Word, PowerPoint, Excel, CSV, Power BI (.pbix/.pbit), OpenDocument, "
                     "notebooks and email files come back as readable text; images and scanned PDFs are read with "
                     "OCR in many languages. For a PDF, start/end pick pages.",
                     {"path": S, "start": I, "end": I}, ["path"]),
    "grep": _fn("grep", "Search file contents with a regular expression. Returns path:line:text. Options: `context` "
                        "(lines around each match, or `before` and `after`), `output_mode` (content, files_with_matches "
                        "or count), `head_limit` (most results, default 400), `ignore_case`, `multiline` (a pattern may "
                        "span lines).",
                {"pattern": S, "path": S, "glob": S, "context": I, "before": I, "after": I,
                 "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
                 "head_limit": I, "ignore_case": {"type": "boolean"}, "multiline": {"type": "boolean"}}, ["pattern"]),
    "notebook_edit": _fn("notebook_edit", "Edit a Jupyter notebook (.ipynb) by cell. mode replace (change cell `cell`'s "
                         "source), insert (new cell before index `cell`; use the cell count to add at the end) or "
                         "delete. Cells are numbered from 0; read the notebook first to see them. Outputs of a "
                         "replaced code cell are cleared.",
                         {"path": S, "cell": I, "source": S, "mode": {"type": "string", "enum": ["replace", "insert", "delete"]},
                          "cell_type": {"type": "string", "enum": ["code", "markdown"]}}, ["path", "cell"]),
    "glob": _fn("glob", "List files matching a glob pattern relative to the workdir, e.g. '**/*.py'.",
                {"pattern": S}, ["pattern"]),
    "edit": _fn("edit", "Replace exactly one occurrence of `old` with `new` in a file (or every occurrence with "
                        "`replace_all`, to rename something). Fails if `old` is missing, "
                        "not unique, the same as `new`, or the file changed since you last read it. To make several "
                        "changes, put them all in one call: `edits` for one file, `changes` for several files "
                        "(all-or-nothing).",
                {"path": S, "old": S, "new": S, "replace_all": {"type": "boolean"},
                 "edits": {"type": "array", "items": {"type": "object", "properties": {"old": S, "new": S, "replace_all": {"type": "boolean"}},
                                                      "required": ["old", "new"]}},
                 "changes": {"type": "array", "items": {"type": "object", "properties": {
                     "path": S, "old": S, "new": S,
                     "edits": {"type": "array", "items": {"type": "object", "properties": {"old": S, "new": S},
                                                          "required": ["old", "new"]}}}, "required": ["path"]}}},
                []),
    "write_file": _fn("write_file", "Create or overwrite a file. Set append=true to add to the end (for large files written in parts).",
                      {"path": S, "content": S, "append": {"type": "boolean"}}, ["path", "content"]),
    "bash": _fn("bash", "Run a shell command in the workdir. Output is capped; full output is saved to a file.",
                {"command": S, "timeout_s": I}, ["command"]),
    "bash_start": _fn("bash_start", "Start a long-running command in the background (a dev server, a watcher, a slow "
                                    "build) and return at once with an id. Read what it prints with bash_output and end "
                                    "it with bash_stop. It keeps running between turns until stopped or Meshpool closes.",
                      {"command": S, "name": S}, ["command"]),
    "bash_output": _fn("bash_output", "Read the new output of a background command started with bash_start, and say "
                                      "whether it is still running (or its exit code). `wait_s` waits up to that many "
                                      "seconds for output first. Without an id, lists every background command.",
                       {"id": S, "wait_s": I}, []),
    "bash_stop": _fn("bash_stop", "Stop a background command (and everything it started). Without an id, stops all of them.",
                     {"id": S}, []),
    "bash_many": _fn("bash_many", "Run several independent shell commands in ONE call (for example: read 3-5 file "
                                  "ranges, grep several patterns, check versions). Each runs on its own, even if an "
                                  "earlier one fails; you get every output, labelled.",
                     {"commands": {"type": "array", "items": S}, "timeout_s": I}, ["commands"]),
    "probe": _fn("probe", "Run a test or a script and get a diagnosis instead of raw output: the failing tests, the "
                          "exception, and the failing frames in this project's code with their local variable values "
                          "(library frames dropped). Use it to see the bug before you edit, and after an edit to see "
                          "what still fails. Counts as a check for finish.",
                 {"command": S, "timeout_s": I}, ["command"]),
    "trace": _fn("trace", "Run a Python test or script (python file.py, python -m mod, pytest ...) and list the "
                          "project's own functions that ran, in first-call order with call counts, then the output "
                          "tail. Use it to find where to edit before you change anything.",
                 {"command": S, "timeout_s": I}, ["command"]),
    "run_tests": _fn("run_tests", "Run a test or check command. Its exit code counts as evidence for finish.",
                     {"command": S, "timeout_s": I}, ["command"]),
    "finish": _fn("finish", "Finish the task. `evidence` must name checks you actually ran in this session "
                            "(the exact command) that passed after your last edit.",
                  {"answer": S, "evidence": {"type": "array", "items": S}, "artifacts": {"type": "array", "items": S}},
                  ["answer", "evidence"]),
    "todo": _fn("todo", "Write or update your todo list for this task: the concrete requirements and steps, each with "
                        "done true/false. Call it again to update. finish is refused while items are not done.",
                {"items": {"type": "array", "items": {"type": "object", "properties": {"text": S, "done": {"type": "boolean"}},
                                                      "required": ["text", "done"], "additionalProperties": False}}},
                ["items"]),
    "load_skill": _fn("load_skill", "Load detailed instructions for a skill listed in the skill index.",
                      {"name": S}, ["name"]),
    "need_context": _fn("need_context", "Ask the orchestrator for files or information outside your job packet "
                                        "instead of guessing.",
                        {"paths": {"type": "array", "items": S}, "reason": S}, ["reason"]),
}
CORE = ["read_file", "grep", "glob", "edit", "write_file", "bash", "run_tests", "finish"]

# The Codex tool set (engine style "codex", see agent/core.py): shell, apply_patch, update_plan. The shell and plan
# definitions follow Codex's (command as an argv list, workdir, timeout_ms; plan items with a step and a status); the
# apply_patch format text is Codex's own (app/defaults/engines/codex/prompt.md), as a JSON function because the chat
# API has no freeform grammar tools.
CODEX_TOOLS = ["shell", "apply_patch", "update_plan"]
_PATCH_HELP = (
    "Edit files with a patch. `input` is the whole patch: '*** Begin Patch', one or more file operations, "
    "'*** End Patch'. Operations: '*** Add File: <path>' (every following line starts with +), "
    "'*** Delete File: <path>', '*** Update File: <path>' (optionally followed by '*** Move to: <new path>'), then "
    "hunks that start with '@@' (optionally '@@ <a line such as a function header that comes before the change>') "
    "and have lines starting with ' ' (unchanged context), '-' (removed) or '+' (added). Give about 3 lines of "
    "context before and after each change; '*** End of File' marks a change at the end of a file. Paths are relative "
    "to the project folder. The patch is applied to all files or to none.")
SCHEMAS["shell"] = _fn(
    "shell", "Runs a shell command and returns its output. The command is an argv list, for example "
             '["bash", "-lc", "pytest -q"]; a plain string is run with bash. Set `workdir` instead of using cd '
             'when you can. `apply_patch` can also be called through this tool as ["apply_patch", "<patch>"].',
    {"command": {"type": "array", "items": S, "description": "The command to execute"},
     "workdir": {"type": "string", "description": "The working directory to execute the command in"},
     "timeout_ms": {"type": "number", "description": "The timeout for the command in milliseconds"}}, ["command"])
SCHEMAS["apply_patch"] = _fn("apply_patch", _PATCH_HELP, {"input": {"type": "string", "description":
                             "The entire contents of the apply_patch command"}}, ["input"])
SCHEMAS["update_plan"] = _fn(
    "update_plan", "Updates the task plan.\nProvide an optional explanation and a list of plan items, each with a "
                   "step and status.\nAt most one step can be in_progress at a time.",
    {"explanation": S,
     "plan": {"type": "array", "description": "The list of steps", "items": {"type": "object", "properties": {
         "step": S, "status": {"type": "string", "description": "One of: pending, in_progress, completed",
                               "enum": ["pending", "in_progress", "completed"]}},
         "required": ["step", "status"], "additionalProperties": False}}}, ["plan"])


# Codex CLI 0.171's exec_command (harness profile "c171", agent/core.py _c171): the definition exactly as Codex sent it
# (docs/results/codex171-request/tools.json; Apache-2.0, see THIRD_PARTY_NOTICES.md), less the Responses API's
# "strict": false, which is the chat API's default. It runs on the same command runner as bash and shell; the result
# is formatted as Codex formats it (exec_result). There is no PTY and no session to come back to (no write_stdin):
# tty, shell, justification, sandbox_permissions and prefix_rule are accepted and not used.
SCHEMAS["exec_command"] = {"type": "function", "function": {
    "name": "exec_command",
    "description": "Runs a command in a PTY, returning output or a session ID for ongoing interaction.",
    "parameters": {
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "Shell command to execute."},
            "justification": {"type": "string", "description":
                              "User-facing approval question for `require_escalated`; omit otherwise."},
            "login": {"type": "boolean", "description": "Request login-shell startup. Omit to inherit."},
            "max_output_tokens": {"type": "number", "description":
                                  "Output token budget. Defaults to 10000 tokens; larger requests may be capped by "
                                  "policy."},
            "prefix_rule": {"type": "array", "description":
                            "Reusable approval prefix for `cmd`, only with "
                            "`sandbox_permissions: \"require_escalated\"`; for example [\"git\", \"pull\"].",
                            "items": {"type": "string"}},
            "sandbox_permissions": {"type": "string", "description":
                                    "Per-command sandbox override. Defaults to `use_default`; use `require_escalated` "
                                    "for unsandboxed execution.", "enum": ["use_default", "require_escalated"]},
            "shell": {"type": "string", "description": "Shell binary to launch. Defaults to the user's default shell."},
            "tty": {"type": "boolean", "description":
                    "True allocates a PTY for the command; false or omitted uses plain pipes."},
            "workdir": {"type": "string", "description":
                        "Working directory for the command. Defaults to the turn cwd."},
            "yield_time_ms": {"type": "number", "description":
                              "Wait before yielding output. Defaults to 10000 ms; effective range is 250-30000 ms."},
        },
        "required": ["cmd"],
        "additionalProperties": False,
    }}}
EXEC_TOKENS = 10000         # exec_command's default max_output_tokens
EXEC_POLICY_BYTES = 10000   # the model's truncation policy on top: Codex cut long outputs to 10,000 bytes (outputs.json)


def _exec_tokens(text: str) -> int:
    """Codex's token estimate: UTF-8 bytes / 4, rounded up (51 for 203 bytes, 7354 for 29,415: outputs.json)."""
    return -(-len(text.encode("utf-8", "surrogatepass")) // 4)


def _exec_truncate(text: str, budget: int) -> str:
    """Codex's cut of a long output: a warning and the line count, then the first and last budget/2 bytes around
    "…N chars truncated…" (outputs.json, "long": 29,415 bytes -> 5,000 + 5,000, "…19415 chars truncated…")."""
    raw = text.encode("utf-8", "surrogatepass")
    if len(raw) <= budget:
        return text
    half = budget // 2
    head = raw[:half].decode("utf-8", "ignore")                      # whole characters only
    tail = raw[len(raw) - (budget - half):]
    while tail and (tail[0] & 0xC0) == 0x80:                          # never start inside a character
        tail = tail[1:]
    tail = tail.decode("utf-8", "ignore")
    cut = len(text) - len(head) - len(tail)
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    return (f"Warning: truncated output (original token count: {_exec_tokens(text)})\nTotal output lines: {lines}\n\n"
            f"{head}…{cut} chars truncated…{tail}")


def exec_result(output: str, code, wall_s: float, max_output_tokens=None, chunk_id: str | None = None) -> str:
    """exec_command's result, byte for byte as Codex 0.171 writes it (docs/results/codex171-request/outputs.json)."""
    import secrets
    try:
        tokens = int(max_output_tokens) if max_output_tokens is not None else EXEC_TOKENS
    except (TypeError, ValueError):
        tokens = EXEC_TOKENS
    budget = min(max(1, tokens) * 4, EXEC_POLICY_BYTES)
    parts = [f"Chunk ID: {chunk_id or secrets.token_hex(3)}", f"Wall time: {wall_s:.4f} seconds"]
    if code is not None:
        parts.append(f"Process exited with code {code}")
    parts += [f"Original token count: {_exec_tokens(output)}", "Output:", _exec_truncate(output, budget)]
    return "\n".join(parts)


def exec_exit_code(out: str):
    """The exit code an exec_command result reports, or None."""
    m = re.match(r"Chunk ID: [0-9a-f]+\nWall time: [^\n]*\nProcess exited with code (-?\d+)\n", str(out or ""))
    return int(m.group(1)) if m else None


def _delegate_schema():
    from meshpool_harness.agent.delegate import schema
    return schema(12)


SCHEMAS["delegate_task"] = _delegate_schema()


def schemas(names):
    return [SCHEMAS[n] for n in names]


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def clean(text: str) -> str:
    """Tool output can contain binary bytes (e.g. reading a .db file); upstream APIs reject NUL/control chars and
    lone surrogates with HTTP 400, which used to crash the run."""
    return _CTRL.sub("\ufffd", text) if isinstance(text, str) else text


class ToolError(Exception):
    pass


def validate(name: str, raw_args, allowed: list[str]) -> dict:
    if name not in allowed:
        raise ToolError(f"unknown tool '{name}'. Available: {', '.join(allowed)}")
    if isinstance(raw_args, str) and raw_args.strip() in ("", "null"):
        raw_args = "{}"                          # some models/gateways send "" for a no-argument call
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError as e:
        raise ToolError(f"arguments for {name} are not valid JSON ({e.msg} at char {e.pos}). "
                        f"Send a single JSON object.")
    if not isinstance(args, dict):
        raise ToolError(f"arguments for {name} must be a JSON object")
    if args.get("__truncated__") is True:
        raise ToolError(f"your {name} call was cut off before its arguments were complete (the reply hit the output "
                        f"limit). Send it again with shorter arguments.")
    if (name == "write_file" and isinstance(args.get("content"), (dict, list))
            and str(args.get("path", "")).lower().endswith(".json")):
        args["content"] = json.dumps(args["content"], indent=2, ensure_ascii=False) + "\n"   # models often send JSON as an object
    if name == "shell" and isinstance(args.get("command"), str):        # a plain string: run it with bash
        args["command"] = ["bash", "-lc", args["command"]]
    for k in ("path", "file_path", "cwd", "dir", "directory"):
        if isinstance(args.get(k), str):
            args[k] = fix_win_path(args[k])
    sch = SCHEMAS[name]["function"]["parameters"]
    for k in sch["required"]:
        if k not in args:
            raise ToolError(f"{name}: missing required argument '{k}'")
    for k, v in args.items():
        if k not in sch["properties"]:
            raise ToolError(f"{name}: unknown argument '{k}'; allowed: {', '.join(sch['properties'])}")
        t = sch["properties"][k]["type"]
        ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
              "number": isinstance(v, (int, float)) and not isinstance(v, bool),
              "boolean": isinstance(v, bool), "array": isinstance(v, list)}[t]
        if not ok:
            raise ToolError(f"{name}: argument '{k}' must be {t}, got {type(v).__name__}")
    return args



_PY_VERSIONS: dict = {}
_NODE_CHECKS: dict = {}            # (extension, node, sha1 of the text) -> its node --check answer, oldest first
_NODE_LOCK = threading.Lock()


_BOM = b"\xef\xbb\xbf"


def node_check_known(p: Path, node: str) -> bool:
    """True when the edit tools already asked this node to --check the file's current text (packs.quick_check then
    has nothing to add). Only where the check of a copy in the temp folder means the same as a check of the file:
    .mjs and .cjs, and .js outside a package whose package.json says "type": "module"."""
    suf = p.suffix.lower()
    if suf == ".js":
        for d in p.resolve().parents:
            pj = d / "package.json"
            if pj.is_file():
                try:
                    if json.loads(pj.read_text(encoding="utf-8")).get("type") == "module":
                        return False
                except (OSError, ValueError, AttributeError):
                    return False
                break
    elif suf not in (".mjs", ".cjs"):
        return False
    try:
        text = read_src(p)[0]
    except OSError:
        return False
    key = (suf, node, hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest())
    with _NODE_LOCK:
        return key in _NODE_CHECKS


def read_src(p: Path) -> tuple[str, str, bool]:
    """(text, line ending, has BOM) of a text file, read as bytes so nothing is translated on any platform. When
    the file is mostly CRLF the text comes back with plain \\n (what the model reads and writes); write_src puts the
    CRLF back. Undecodable bytes survive the round trip (surrogateescape)."""
    raw = p.read_bytes()
    bom = raw.startswith(_BOM)
    text = raw[3 if bom else 0:].decode("utf-8", "surrogateescape")
    crlf = text.count("\r\n")
    if crlf and crlf * 2 > text.count("\n"):
        return text.replace("\r\n", "\n"), "\r\n", bom
    return text, "\n", bom


def _user_python_env(env: dict) -> dict:
    """The user's project runs its own Python: Meshpool's app folder is never on its PYTHONPATH. That folder holds
    sitecustomize.py (run at every interpreter start: update recovery against the app folder) and a `tests` package
    that shadowed the project's own; swe4 saw 8 import errors in Meshpool's runs against 1 in Codex's. Bytecode is
    allowed (a big repo recompiled on every test run without it); a stale .pyc after a same-size, same-second edit
    is prevented by write_src dropping the edited module's cached bytecode."""
    app = os.path.normcase(os.path.abspath(str(Path(__file__).resolve().parents[2])))
    parts = [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep)
             if p and os.path.normcase(os.path.abspath(p)) != app]
    if parts:
        env["PYTHONPATH"] = os.pathsep.join(parts)
    else:
        env.pop("PYTHONPATH", None)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    return env


def _drop_pyc(p: Path) -> None:
    """Remove the cached bytecode of an edited .py file: Python validates a .pyc by the source's whole-second mtime
    and size, so a same-size edit within the same second would otherwise run the old code."""
    if p.suffix != ".py":
        return
    try:
        for c in (p.parent / "__pycache__").glob(p.stem + ".*.pyc"):
            c.unlink(missing_ok=True)
    except OSError:
        pass


def write_src(p: Path, text: str, eol: str = "\n", bom: bool = False, append: bool = False) -> None:
    """Write text with the file's own line ending and BOM, byte for byte (Python's text mode would turn every \\n
    into CRLF on Windows and drop a CRLF file's endings elsewhere)."""
    if eol == "\r\n":
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("utf-8", "surrogateescape")
    if append:
        with p.open("ab") as f:
            f.write(data)
    else:
        from meshpool_harness import fsio
        try:
            mode = p.stat().st_mode & 0o7777
        except OSError:
            mode = None
        # OSS-15: never a half-written file. A symlink (CLAUDE.md -> AGENTS.md) stays a link and a hard-linked file
        # stays one file: both are written through, not replaced by a new regular file.
        fsio.atomic_write_bytes(p, (_BOM if bom else b"") + data, mode=mode, keep_links=True)
    _drop_pyc(p)


def text_lines(text: str) -> list[str]:
    """The lines of a text the way compilers, tracebacks, grep and editors count them: split at \\n only (a CR before
    it dropped). str.splitlines also splits at form feeds, \\x1c-\\x1e, \\x85 and U+2028, so a Python file with a
    form feed had every later line number off by one against its tracebacks. A file with only CR endings (classic
    Mac) is split at CR."""
    lines = text.split("\n" if "\n" in text or "\r" not in text else "\r")
    if lines and lines[-1] == "":
        lines.pop()
    return [ln[:-1] if ln.endswith("\r") else ln for ln in lines]


def shown(text: str) -> str:
    """Text safe to show the model: bytes that are not UTF-8 become U+FFFD."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _newer_pythons(path: Path) -> list:
    """Interpreters newer than this one that could run `path`: its project's virtual environment (the nearest
    .venv / venv above it), then the newest python3.x on PATH. Each is asked its version once."""
    import sys
    cands = []
    for d in [path.parent, *path.parent.parents][:8]:
        for rel in (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe", "venv/Scripts/python.exe"):
            if (d / rel).exists():
                cands.append(str(d / rel))
    try:
        from meshpool_harness.hrouter.packs import newest_python
        np_ = newest_python()
        if np_:
            cands.append(np_)
    except Exception:  # noqa: BLE001
        pass
    out = []
    for exe in dict.fromkeys(cands):
        if exe not in _PY_VERSIONS:
            try:
                r = subprocess.run([exe, "-c", "import sys;print(sys.version_info[0], sys.version_info[1])"],
                                   capture_output=True, text=True, timeout=10)
                _PY_VERSIONS[exe] = tuple(int(x) for x in r.stdout.split()[:2]) if r.returncode == 0 else ()
            except (OSError, subprocess.SubprocessError, ValueError):
                _PY_VERSIONS[exe] = ()
        if _PY_VERSIONS[exe] and _PY_VERSIONS[exe] > sys.version_info[:2]:
            out.append(exe)
    return out


def _parses_with_newer_python(path: Path, text: str) -> bool:
    for exe in _newer_pythons(path):
        try:
            r = subprocess.run([exe, "-c", "import sys; compile(sys.stdin.read(), sys.argv[1], 'exec', dont_inherit=True)",
                                str(path)], input=text, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False

_LIVE = threading.local()          # the tool call whose command is running in this thread



def _squeeze(text: str, keep: int = 2) -> str:
    """Runs of identical lines (progress bars, repeated warnings, polling output) shown once with a count: tool
    output is information-sparse (agents use about a quarter of it, arXiv 2611.12122) and every line is billed."""
    if os.environ.get("MH_SQUEEZE", "1") == "0" or "\n" not in text:
        return text
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        n = j - i + 1
        if n > keep + 1 and lines[i].strip():
            out += lines[i:i + keep] + [f"[... the line above repeated {n - keep} more times]"]
        else:
            out += lines[i:j + 1]
        i = j + 1
    return "\n".join(out)


def _prune_scratch(tmp: str, max_age_s: float = 86400) -> None:
    """Scratch folders a crashed or killed process left behind: this user's, older than a day. Never raises."""
    try:
        now, uid = time.time(), getattr(os, "getuid", lambda: None)()
        for e in os.scandir(tmp):
            if not e.name.startswith("meshpool-scratch-") or not e.is_dir(follow_symlinks=False):
                continue
            st = e.stat(follow_symlinks=False)
            if (uid is None or st.st_uid == uid) and now - st.st_mtime > max_age_s:
                shutil.rmtree(e.path, ignore_errors=True)
    except OSError:
        pass

class Workspace:
    """Executes tools inside workdir, tracks reads/writes/commands for guards."""

    def __init__(self, workdir: str, run_dir: str, skills: dict[str, str] | None = None,
                 container: str | None = None, container_dir: str | None = None, mounted: bool = True):
        """container: run bash/run_tests natively inside this Docker container (cwd container_dir), so the model
        never writes nested `docker exec ... bash -lc '...'` quoting. mounted=True: workdir is bind-mounted at
        container_dir (file tools use the host copy); mounted=False: file tools read/write inside the container."""
        self.container, self.cdir, self.mounted = container, container_dir, mounted
        self.root = Path(workdir).resolve()
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.skills = skills or {}
        self.seen: dict[str, str] = {}        # path -> content hash the agent last saw/wrote
        self.original: dict[str, str] = {}    # path -> content hash when the agent first saw it (revert check)
        self.commands: list[dict] = []        # executed commands with exit codes and edit counter
        self.edits = 0
        self.written: set[str] = set()
        self.created: set[str] = set()        # files the agent created (new tests are allowed)
        self.turn_new: set[str] = set()       # files created in the current turn (the receipt's "new")
        self.turn_files: set[str] = set()     # files changed in the current turn (the review receipt)
        self._n = 0

    # -- helpers
    def _path(self, p: str, write=False) -> Path:
        if self.container and self.mounted and self.cdir and os.path.isabs(p) and (p == self.cdir or p.startswith(self.cdir.rstrip("/") + "/")):
            p = str(self.root / os.path.relpath(p, self.cdir))     # container path -> host copy
        p = fix_win_path(p)
        if write and ("$" in p or p.startswith("~")):
            p = expand_path(p)                    # a shell's reading, never a literal "$HOME" or "~" folder
        if write and (bad := reserved_name(p)):
            raise ToolError(f"'{bad}' is a reserved device name on Windows and cannot be used as a file name")
        q = (self.root / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
        scratch = self.__dict__.get("_scratch_dir")
        if write and scratch and Path(scratch).resolve() in q.parents:
            return q                          # the task's own private scratch folder ($TMPDIR): edits there are fine
        base = next((r for r in [self.root, *getattr(self, "extra_roots", [])] if q == r or r in q.parents), None)
        if write and base is None:
            raise ToolError(f"writes must stay inside the workdir {self.root}"
                            + (f" or the added folders ({', '.join(map(str, self.extra_roots))})"
                               if getattr(self, "extra_roots", None) else "") + f"; got {q}")
        if write and any(_git_dir_name(x) for x in q.relative_to(base).parts):
            # git runs settings and hooks from here (core.fsmonitor, hooks/): a write there can run any command
            raise ToolError("agents cannot change files inside .git; use git commands instead")
        try:
            rel = [x.lower().rstrip(". ") for x in q.relative_to(self.root).parts] if write else []
        except ValueError:                             # an allowed folder outside the project (uploads, scratch)
            rel = []
        if len(rel) == 2 and rel[0] == ".meshpool" and rel[1] in ("hooks.json", "permissions.json"):
            # the project's hooks run commands and its rules approve them: an agent that writes them approves itself
            # (security review: in a trusted folder, a written hooks.json ran on the next tool call with shell=True)
            raise ToolError("agents cannot change .meshpool/hooks.json or .meshpool/permissions.json; ask the user to "
                            "change hooks and permission rules in Settings")
        return q

    @staticmethod
    def _hash(text: str) -> str:
        # line endings and a BOM do not count: read_file/edit see \\n text, apply_patch sees the raw text
        return hashlib.sha1(text.lstrip("\ufeff").replace("\r\n", "\n").encode("utf-8", "replace")).hexdigest()

    max_tool_chars = MAX_TOOL_CHARS          # lowered for a small context window (core/context.py)

    engine_libs = True        # an engine's own scripts import its helper libraries (from engines.sheets import ...)

    def _py_env(self, env: dict) -> dict:
        """The Python environment for a command: the user's project gets its own Python only (the chat agent sets
        engine_libs False); an engine's workspace also gets Meshpool's app folder, for its helper libraries."""
        env = _user_python_env(env)
        if self.engine_libs:
            app = str(Path(__file__).resolve().parents[2])
            env["PYTHONPATH"] = app + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return env

    def _cap(self, text: str, label: str) -> str:
        text = _squeeze(clean(text))
        cap = self.max_tool_chars
        if len(text) <= cap:
            return text
        head = tail = HEAD if cap >= MAX_TOOL_CHARS else max(250, int(cap * 0.375))
        more = {"read": " Read the rest with read_file start/end.", "grep": " Narrow the pattern or the path.",
                "glob": " Use a narrower pattern."}.get(label, "") if cap < MAX_TOOL_CHARS else ""
        with _CAP_LOCK:                          # bash_many may run commands in threads (MH_PARALLEL_MANY)
            self._n += 1
            n = self._n
        f = self.run_dir / f"out_{n:03d}_{label}.txt"
        cut = len(text) - head - tail
        try:                                     # the task may delete .run (e.g. git clean): recreate it
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8", errors="replace")
        except OSError:
            return text[:head] + f"\n... [{cut} chars truncated{'.' + more if more else ''}] ...\n" + text[-tail:]
        return (text[:head] + f"\n... [{cut} chars truncated; full output in {f}{'.' + more if more else ''}] ...\n"
                + text[-tail:])

    # -- container (unmounted) file access
    def _cpath(self, p: str) -> str:
        return p if p.startswith("/") else f"{self.cdir.rstrip('/')}/{p}"

    def _cexec(self, args: list[str], input_text: str | None = None, timeout=120):
        cmd = ["docker", "exec"] + (["-i"] if input_text is not None else []) + ["-w", self.cdir, self.container, *args]
        return subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout, errors="replace")

    def _cread(self, path: str) -> str:
        r = self._cexec(["cat", self._cpath(path)])
        if r.returncode:
            raise ToolError(f"file not found or unreadable in container: {path}")
        return r.stdout

    def _cwrite(self, path: str, text: str, append=False):
        cp = self._cpath(path)
        # the path goes in as $1, never inside the script: a name with $(...) or a quote in it is not run as code
        r = self._cexec(["sh", "-c", f'mkdir -p "$(dirname -- "$1")" && cat {">>" if append else ">"} "$1"', "sh", cp],
                        input_text=text)
        if r.returncode:
            raise ToolError(f"could not write {path} in container: {r.stderr[-200:]}")

    # -- tools
    def read_file(self, path, start=None, end=None):
        if self.container and not self.mounted:
            text = self._cread(path)
            self.seen[self._cpath(path)] = self._hash(text)
            self.original.setdefault(self._cpath(path), self.seen[self._cpath(path)])
            lines = text_lines(text)
            s, e = max(1, start or 1), min(len(lines), end or len(lines))
            body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(s, e + 1))
            return self._cap(f"{path} lines {s}-{e} of {len(lines)}\n{body}", "read")
        p = self._path(path)
        if not p.exists():
            raise ToolError(f"file not found: {path}")
        if p.is_dir():
            raise ToolError(f"{path} is a directory; use glob")
        from meshpool_harness import docread
        if docread.is_image(p):
            return self._cap(docread.extract(p), "read")
        if docread.is_document(p) and p.suffix.lower() not in (".html", ".htm", ".csv", ".tsv", ".ipynb"):
            pages = f"{start or 1}-{end or ''}" if (start or end) else None
            return self._cap(docread.extract(p, pages=pages), "read")
        with p.open("rb") as _f:
            head = _f.read(8192)
        if b"\0" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):   # binary (UTF-16 text has a BOM)
            raise ToolError(f"{path} is a binary file ({p.stat().st_size:,} bytes); read_file shows text only. Use a "
                            "tool made for its format, or `file`, `xxd | head` or `strings | head` in the shell.")
        text = read_src(p)[0]
        self.seen[str(p)] = self._hash(text)
        self.original.setdefault(str(p), self.seen[str(p)])
        lines = text_lines(shown(text))
        s = max(1, start or 1)
        e = min(len(lines), end or len(lines))
        if s > len(lines) and lines:
            return f"{path}: file has {len(lines)} lines (start={s} is past the end)"
        cap = self.max_tool_chars
        if sum(len(x) + 8 for x in lines[s - 1:e]) > 20 * cap:
            # a big file (a log, a data dump): only the head and the tail are shown, so only those are numbered, and no
            # numbered copy of the whole file goes to the run folder (135 MB read whole: 3.8 s and a 152 MB copy)
            keep = HEAD if cap >= MAX_TOOL_CHARS else max(250, int(cap * 0.375))

            def rows(idx):
                out, n = [], 0
                for i in idx:
                    row = f"{i:>5}  {lines[i - 1]}"[:keep]
                    n += len(row) + 1
                    if n > keep and out:
                        break
                    out.append(row)
                return out
            hl = rows(range(s, e + 1))
            tl = rows(range(e, s + len(hl) - 1, -1))[::-1]
            gap = e - s + 1 - len(hl) - len(tl)
            return clean(f"{path} lines {s}-{e} of {len(lines)}\n" + "\n".join(hl)
                         + f"\n... [{gap} lines not shown. Read them with read_file start/end, or search with grep] ...\n"
                         + "\n".join(tl))
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(s, e + 1))
        return self._cap(f"{path} lines {s}-{e} of {len(lines)}\n{body}", "read")

    def _inside(self, p, roots=None) -> bool:
        """p resolves inside the project (or one of roots)."""
        try:
            q = Path(p).resolve()
            return any(q.is_relative_to(Path(r).resolve()) for r in (roots or [self.root]))
        except (OSError, ValueError):
            return False

    def _ignored(self, files: list, base: Path | None = None) -> set:
        """The files git ignores (.gitignore, .git/info/exclude, the global excludes), so search skips build output,
        caches and dependencies the way other agents do. Empty when this is not a git repo, git is missing, or the
        folder being searched is itself ignored (the user pointed at it on purpose)."""
        if not files or os.environ.get("MH_SEARCH_IGNORED") == "1" or not shutil.which("git"):
            return set()
        try:                     # no "is this a repo" call first: outside one, check-ignore itself fails (128)
            if base is not None and base != self.root and subprocess.run(
                    ["git", "-C", str(self.root), "check-ignore", "-q", str(base)], capture_output=True,
                    timeout=10).returncode != 1:
                return set()     # 0: the folder searched is itself ignored; 128: not a repo
            r = subprocess.run(["git", "-C", str(self.root), "check-ignore", "--stdin", "-z"],
                               input="\0".join(str(f) for f in files) + "\0", capture_output=True, text=True, timeout=30)
            if r.returncode not in (0, 1):
                return set()
            return {Path(x) for x in r.stdout.split("\0") if x}
        except (OSError, subprocess.SubprocessError):
            return set()

    def grep(self, pattern, path=".", glob=None, inside_only=False, context=0, before=0, after=0,
             output_mode="content", head_limit=400, ignore_case=False, multiline=False):
        """inside_only: skip files that resolve outside the project (a symlink), before anything is read or saved;
        a list of folders instead of True allows those folders as well. context/before/after: lines around a match.
        output_mode: content, files_with_matches or count. head_limit: most results returned."""
        try:
            head = max(1, min(int(head_limit or 400), 2000))
            before = max(0, min(int(before or context or 0), 20))
            after = max(0, min(int(after or context or 0), 20))
        except (TypeError, ValueError):
            raise ToolError("context, before, after and head_limit must be whole numbers")
        if output_mode not in ("content", "files_with_matches", "count"):
            raise ToolError("output_mode must be content, files_with_matches or count")
        if self.container and not self.mounted:
            inc = f"--include='{glob}'" if glob else ""
            ctx = f"-B{before} -A{after}" if (before or after) and output_mode == "content" else ""
            mode = {"files_with_matches": "-l", "count": "-c"}.get(output_mode, "")
            case = "-i" if ignore_case else ""
            out, _ = self._run(f"grep -rnE {case} {mode} {ctx} {inc} --exclude-dir=.git --exclude-dir=.jj --exclude-dir=.sl --exclude-dir=node_modules -e "
                               f"{json.dumps(pattern)} {json.dumps(path)} | head -{head}", 60, "grep", record=False)
            return out
        try:
            rx = re.compile(pattern, (re.I if ignore_case else 0) | (re.M | re.S if multiline else 0))
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        base = self._path(path)
        if not base.exists():
            raise ToolError(f"path not found: {path}")
        files = [base] if base.is_file() else list(self._walk(base))
        if not base.is_file():                                   # files git ignores are left out, like ripgrep
            skip = self._ignored(files, base)
            files = [f for f in files if f not in skip]
        out, matched, truncated = [], 0, False
        for f in files:
            rel = f.relative_to(self.root) if self.root in f.parents else f
            if self.should_stop and self.should_stop():
                raise ToolError("stopped by the user")
            if glob and not glob_match(str(rel), glob):
                continue
            if inside_only and not self._inside(f, inside_only if isinstance(inside_only, (list, tuple)) else None):
                continue
            try:
                if f.stat().st_size > MAX_GREP_FILE:
                    continue                                       # H-11: a huge file is not source
                raw = f.read_bytes()
            except OSError:
                continue
            if b"\0" in raw[:8192]:                               # a binary file: never matched as text
                continue
            text = raw.decode("utf-8", errors="ignore")
            lines = text_lines(text)
            hits: list[int] = []                                   # 1-based line numbers that start a match
            need = head - len(out) + 1                             # enough to know the list is full; no more
            if multiline:
                for m in rx.finditer(text):
                    hits.append(text.count("\n", 0, m.start()) + 1)
                    if output_mode != "count" and len(hits) >= need:
                        break
            else:
                for i, line in enumerate(lines, 1):
                    if rx.search(line):
                        hits.append(i)
                        if output_mode != "count" and len(hits) >= need:
                            break
            if not hits:
                continue
            hitset = set(hits)
            matched += 1
            if output_mode == "files_with_matches":
                out.append(str(rel))
            elif output_mode == "count":
                out.append(f"{rel}:{len(hits)}")
            else:
                shown: set[int] = set()
                last = 0
                for h in hits:
                    if len(out) > head:
                        break
                    lo, hi = max(1, h - before), min(len(lines), h + after)
                    if (before or after) and last and lo > last + 1:
                        out.append("--")
                    for n in range(lo, hi + 1):
                        if n in shown:
                            continue
                        shown.add(n)
                        sep = ":" if n in hitset else "-"
                        out.append(f"{rel}{sep}{n}{sep}{lines[n - 1][:300]}")
                    last = max(last, hi)
            if len(out) >= head:
                truncated = True
                out = out[:head]
                break
        if truncated:
            out.append("... more results truncated; narrow the pattern or raise head_limit")
        return self._cap("\n".join(out) or "no matches", "grep")

    def glob(self, pattern, inside_only=False):
        """inside_only: leave out what resolves outside the project (through a symlinked folder or file)."""
        if self.container and not self.mounted:
            out, _ = self._run(f"find . -path ./.git -prune -o -path './{pattern.lstrip('./')}' -print 2>/dev/null | head -2000"
                               if "/" in pattern else f"find . -name {json.dumps(pattern.split('/')[-1])} -not -path './.git/*' | head -2000",
                               60, "glob", record=False)
            return out
        if os.path.isabs(pattern) or re.match(r"^[A-Za-z]:", pattern) or ".." in Path(pattern).parts:
            raise ToolError("glob patterns are relative to the working directory (no absolute paths or '..')")
        try:
            res = []
            for p in self._glob_paths(pattern):
                if not inside_only or self._inside(p):
                    res.append(str(p.relative_to(self.root)))
            if not any(ch in pattern for ch in ("node_modules", "dist/", "build/")):   # unless asked for by name
                skip = {str(x.relative_to(self.root)) for x in self._ignored([self.root / r for r in res])}
                res = [r for r in res if r not in skip]
            res.sort()
        except (NotImplementedError, ValueError) as e:
            raise ToolError(f"invalid glob pattern: {e}")
        return self._cap("\n".join(res[:2000]) or "no files", "glob")

    @staticmethod
    def _glob_hit(pat: list, parts: list) -> bool:
        """parts (a path's names) matches pat (a glob's names, "**" for any number of folders), as pathlib does."""
        if not pat:
            return not parts
        if pat[0] == "**":
            return Workspace._glob_hit(pat[1:], parts) or (bool(parts) and Workspace._glob_hit(pat, parts[1:]))
        return bool(parts) and fnmatch.fnmatch(parts[0], pat[0]) and Workspace._glob_hit(pat[1:], parts[1:])

    def _glob_paths(self, pattern: str):
        """What root.glob(pattern) finds outside SKIP_DIRS, found without entering them (H-11: pathlib walked all of
        node_modules for **/*.py and the results were dropped afterwards, with no Stop check while it walked), and
        no deeper than the pattern can match."""
        parts = [x for x in pattern.replace("\\", "/").split("/") if x not in ("", ".")]
        if not parts:
            raise ValueError(f"Unacceptable pattern: {pattern!r}")
        dirs_only = pattern.replace("\\", "/").endswith("/") or parts[-1] == "**"
        magic = re.compile(r"[*?\[]")
        n = 0
        while n < len(parts) and not magic.search(parts[n]) and parts[n] != "**":
            n += 1
        lit, rest = parts[:n], parts[n:]
        if any(x in SKIP_DIRS for x in lit):
            return
        base = self.root.joinpath(*lit)
        if not rest:                                          # no wildcard: the path itself, when it is there
            if base.exists() and (not dirs_only or base.is_dir()):
                yield base
            return
        if not base.is_dir():
            return
        depth = None if "**" in rest else len(rest)
        if not rest or all(x == "**" for x in rest):
            yield base                                        # "**" includes the folder itself, as pathlib does
        # a fixed depth follows symlinked folders as pathlib's * does; ** never enters them (no loops), as in pathlib
        for dp, dns, fns in os.walk(base, followlinks=depth is not None):
            if self.should_stop and self.should_stop():
                raise ToolError("stopped by the user")
            rel = list(Path(dp).relative_to(base).parts)
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            names = [(d, True) for d in dns] + [(f, False) for f in fns if not dirs_only]
            for nm, is_dir in names:
                if self._glob_hit(rest, rel + [nm]):
                    p = Path(dp, nm)
                    if rest[-1] == "**" and p.is_symlink():
                        continue                              # a final ** lists real folders only, as pathlib's
                    if not dirs_only or is_dir or p.is_dir():
                        yield p
            if depth is not None and len(rel) + 1 >= depth:
                dns[:] = []                                   # deeper names cannot match the pattern

    def _walk(self, base: Path):
        """H-11: files under base, never entering SKIP_DIRS, and checking the Stop flag as it walks."""
        for dp, dns, fns in os.walk(base):
            if self.should_stop and self.should_stop():
                raise ToolError("stopped by the user")
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                f = Path(dp, fn)
                if f.is_file():
                    yield f

    @staticmethod
    def _syntax_error(path: Path, text: str) -> str | None:
        """Lint on edit: the exact place a Python, JSON or JavaScript file stops parsing, shown the way the compiler
        shows it (line, column, the line itself and a caret), so the agent fixes it in the same step instead of
        finding out when the code runs. None when the file parses or the language is not checked."""
        def where(line: int, col: int, msg: str) -> str:
            lines = text_lines(text)
            src = lines[line - 1] if 0 < line <= len(lines) else ""
            caret = " " * max(0, min(col, len(src) + 1) - 1) + "^"
            return f"line {line}, column {col}: {msg}\n    {src}\n    {caret}"
        suf = path.suffix.lower()
        if suf in (".py", ".pyw"):
            try:
                compile(text, str(path), "exec", dont_inherit=True)
                return None
            except SyntaxError as e:     # Python's own message: "'(' was never closed", "invalid syntax. Perhaps you forgot a comma?"
                # Meshpool's own Python (3.12 in the app) does not know newer syntax (3.14: `except A, B:`, t-strings).
                # The code is judged by the newest Python that would run it: the project's venv, else the newest
                # python3.x on this computer. Without a newer one the code cannot run here either, so the error stands.
                if _parses_with_newer_python(path, text):
                    return None
                return where(e.lineno or 0, e.offset or 1, e.msg)
            except (ValueError, TypeError):
                return None
        if suf == ".json":
            try:
                json.loads(text) if text.strip() else None
                return None
            except json.JSONDecodeError as e:
                return where(e.lineno, e.colno, e.msg)
        if suf in (".js", ".mjs", ".cjs"):
            node = shutil.which("node")
            if not node:
                return None
            # An edit checks the text before and after, and the next edit's "before" is this edit's "after": each
            # was a node process. The answer for the same text from the same node is kept.
            key = (suf, node, hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest())
            with _NODE_LOCK:
                if key in _NODE_CHECKS:
                    _NODE_CHECKS[key] = _NODE_CHECKS.pop(key)
                    return _NODE_CHECKS[key]
            got = Workspace._node_check(node, suf, text, where)
            if got != "":
                with _NODE_LOCK:
                    _NODE_CHECKS[key] = got
                    while len(_NODE_CHECKS) > 64:
                        _NODE_CHECKS.pop(next(iter(_NODE_CHECKS)))
            return got or None
        return None

    @staticmethod
    def _node_check(node: str, suf: str, text: str, where) -> str | None:
        """node --check on the text: None when it parses, the error when it does not, "" when node could not run."""
        try:
            with tempfile.NamedTemporaryFile("w", suffix=suf, delete=False, encoding="utf-8", errors="surrogateescape") as f:
                f.write(text)
            r = subprocess.run([node, "--check", f.name], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return ""
        finally:
            try:
                os.unlink(f.name)
            except (OSError, NameError):
                pass
        if r.returncode == 0:
            return None
        m = re.search(r":(\d+)\n", r.stderr)
        msg = next((x for x in r.stderr.splitlines() if "Error" in x), "syntax error").strip()
        return where(int(m.group(1)), 1, msg) if m else msg

    NOOP_EDIT = ("edit rejected: `new` is identical to `old`, so nothing would change and {path} is unchanged. Put "
                 "the changed text in `new` (keep `old` exactly as it is in the file).")

    _LINENO = re.compile(r"^ *\d+(?: {2}|$)")    # read_file prints f"{i:>5}  {line}"

    @classmethod
    def _strip_line_numbers(cls, text: str) -> str | None:
        """`text` without read_file's line-number prefixes when every line has one, else None."""
        lines = text.split("\n")
        body = lines[:-1] if len(lines) > 1 and lines[-1] == "" else lines
        if not body or not all(cls._LINENO.match(ln) for ln in body):
            return None
        out = [cls._LINENO.sub("", ln, count=1) for ln in body]
        return "\n".join(out) + ("\n" if len(body) != len(lines) else "")

    @staticmethod
    def _numbered(lines: list[str], a: int, b: int) -> str:
        return "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(a, b + 1))

    @staticmethod
    def _closest_region(lines: list[str], old: str) -> str:
        """About 8 lines of the file most like `old`, numbered the way read_file numbers them ('' when nothing is close)."""
        import difflib
        k = max(1, len(old.splitlines()))
        if not lines or len(lines) > 20000:
            return ""
        target = "\n".join(ln.rstrip() for ln in old.splitlines())
        best, best_i = 0.0, 0
        sm = difflib.SequenceMatcher(autojunk=False)
        sm.set_seq2(target)
        for i in range(max(1, len(lines) - k + 1)):
            sm.set_seq1("\n".join(ln.rstrip() for ln in lines[i:i + k]))
            if sm.real_quick_ratio() <= best or sm.quick_ratio() <= best:
                continue
            r = sm.ratio()
            if r > best:
                best, best_i = r, i
        if best < 0.2:
            return ""
        a = best_i + 1
        b = min(len(lines), a + 7)
        return f"\nClosest text in the file (lines {a}-{b}):\n{Workspace._numbered(lines, a, b)}"

    @staticmethod
    def _indent_of(line: str) -> int:
        e = line.expandtabs(4)
        return len(e) - len(e.lstrip(" "))

    @classmethod
    def _fuzzy_matches(cls, lines: list[str], old: str, level: int):
        """Start indexes (and indentation offset) of regions of `lines` equal to `old` after normalising: level 1 =
        trailing whitespace; level 2 = also tabs as 4 spaces and a uniform indentation difference."""
        olines = [ln.rstrip() for ln in old.splitlines()]
        k = len(olines)
        if k == 0 or not any(olines):
            return []
        if level == 1:
            return [(i, 0) for i in range(len(lines) - k + 1)
                    if all(lines[i + j].rstrip() == olines[j] for j in range(k))]
        o_ind = min((cls._indent_of(x) for x in olines if x.strip()), default=0)
        o_norm = [x.expandtabs(4)[o_ind:].rstrip() if x.strip() else "" for x in olines]
        out = []
        for i in range(len(lines) - k + 1):
            w = lines[i:i + k]
            if any(bool(w[j].strip()) != bool(olines[j].strip()) for j in range(k)):
                continue
            w_ind = min((cls._indent_of(x) for x in w if x.strip()), default=0)
            if all((w[j].expandtabs(4)[w_ind:].rstrip() if w[j].strip() else "") == o_norm[j] for j in range(k)):
                out.append((i, w_ind - o_ind))
        return out

    @classmethod
    def _plan_edit(cls, text: str, old: str, new: str, path: str):
        """The file text after replacing `old` with `new`, and a note ('' for an exact match). Raises ToolError with
        guidance when `old` is missing or ambiguous."""
        if text.count(old) == 0:
            stripped = cls._strip_line_numbers(old)
            if stripped is not None and stripped:
                old = stripped
                n2 = cls._strip_line_numbers(new)
                if n2 is not None:
                    new = n2
        n = text.count(old)
        if n == 1:
            return text.replace(old, new, 1), ""
        if n > 1:
            starts = [m.start() for m in re.finditer(re.escape(old), text)]
            at = ", ".join(str(text.count("\n", 0, s) + 1) for s in starts[:10])
            more = f" (and {len(starts) - 10} more)" if len(starts) > 10 else ""
            raise ToolError(f"`old` text occurs {n} times in {path}, starting at lines {at}{more}; include more "
                            f"surrounding lines to make it unique")
        keep = text.splitlines(keepends=True)
        lines = [ln.rstrip("\r\n") for ln in keep]
        for level in (1, 2):
            hits = cls._fuzzy_matches(lines, old, level)
            if len(hits) == 1:
                i, off = hits[0]
                k = len(old.splitlines())
                nl = [ln.rstrip() for ln in new.splitlines()]
                if level == 2:
                    nl = [" " * max(0, cls._indent_of(x) + off) + x.lstrip() if x.strip() else "" for x in nl]
                eol = "\n"
                last = keep[i + k - 1]
                if last.endswith("\r\n"):
                    eol = "\r\n"
                tail = eol if last.endswith(("\n", "\r")) and nl else ""
                repl = eol.join(nl) + tail
                return "".join(keep[:i]) + repl + "".join(keep[i + k:]), " (matched after normalising whitespace)"
            if len(hits) > 1:
                at = ", ".join(str(i + 1) for i, _ in hits[:10])
                raise ToolError(f"`old` text matches {len(hits)} places in {path} after normalising whitespace, "
                                f"starting at lines {at}; include more surrounding lines to make it unique")
        raise ToolError(f"`old` text not found in {path}. Copy it exactly from read_file output "
                        f"(without the line-number prefix)." + cls._closest_region(lines, old))

    @staticmethod
    def _edit_items(spec: dict, where: str) -> list[tuple[str, str]]:
        """The (old, new) pairs of one edit spec: its own old/new first, then its `edits` list, in order. An `old`
        with replace_all set comes back as an AllStr, which _plan_many replaces everywhere."""
        items = []
        if spec.get("old") is not None or spec.get("new") is not None:
            o = spec.get("old")
            items.append((AllStr(o) if spec.get("replace_all") and isinstance(o, str) else o, spec.get("new")))
        extra = spec.get("edits")
        if extra is not None:
            if not isinstance(extra, list):
                raise ToolError(f"`edits` for {where} must be a list of {{old, new}} objects")
            for j, it in enumerate(extra, 1):
                if not isinstance(it, dict):
                    raise ToolError(f"edit {j} for {where} must be an object with `old` and `new`")
                o = it.get("old")
                items.append((AllStr(o) if it.get("replace_all") and isinstance(o, str) else o, it.get("new")))
        if not items:
            raise ToolError(f"nothing to edit in {where}: give `old` and `new`, or an `edits` list")
        for j, (o, n) in enumerate(items, 1):
            if not isinstance(o, str) or not isinstance(n, str) or o == "":
                raise ToolError(f"edit {j} of {len(items)} for {where} needs a non-empty string `old` and a string `new`")
        return items

    def _plan_many(self, text: str, items: list[tuple[str, str]], path: str, unread: bool):
        """All `items` applied in order to `text`: (final text, notes). Each must match once in the text as it stands
        after the previous ones. Raises ToolError naming the failing item; nothing is written here."""
        notes, n = [], len(items)
        for j, (old, new) in enumerate(items, 1):
            tag = f"edit {j} of {n} in {path}: " if n > 1 else ""
            if old == new:
                raise ToolError(tag + self.NOOP_EDIT.format(path=path))
            if unread and (text.count(old) != 1 or isinstance(old, AllStr)):
                raise ToolError(f"read {path} before editing it (an unread file can only be edited when `old` "
                                f"matches exactly once)" + (f" [{tag.strip(' :')}]" if tag else ""))
            if isinstance(old, AllStr) and text.count(old) > 1:        # replace_all: every occurrence, counted
                notes.append(f" (replaced {text.count(old)} occurrences)")
                text = text.replace(str(old), new)
                continue
            try:
                text, note = self._plan_edit(text, str(old), new, path)
            except ToolError as e:
                raise ToolError(tag + str(e) + (f"\nNothing was written; {path} is unchanged." if n > 1 else "")) from None
            notes.append(note)
        return text, notes

    def _load(self, path: str):
        """(key, text, path object|None, (eol, bom)) for a file about to be edited, host or container."""
        if self.container and not self.mounted:
            return self._cpath(path), self._cread(path), None, ("\n", False)
        p = self._path(path, write=True)
        if not p.exists():
            raise ToolError(f"file not found: {path}")
        if p.is_dir():
            raise ToolError(f"{path or '.'} is a directory; give a file path")
        text, eol, bom = read_src(p)
        return str(p), text, p, (eol, bom)

    def edit(self, path=None, old=None, new=None, edits=None, changes=None, replace_all=False):
        if changes is not None:
            if not isinstance(changes, list) or not changes or not all(isinstance(c, dict) for c in changes):
                raise ToolError("`changes` must be a non-empty list of {path, old, new} or {path, edits} objects")
            specs = list(changes)
            if path is not None:
                specs.insert(0, {"path": path, "old": old, "new": new, "edits": edits})
        elif path is None:
            raise ToolError("edit needs `path` with `old` and `new` (or `edits`), or `changes` for several files")
        else:
            specs = [{"path": path, "old": old, "new": new, "edits": edits, "replace_all": bool(replace_all)}]
        grouped: dict[str, list] = {}
        for sp in specs:
            pth = sp.get("path")
            if not isinstance(pth, str) or not pth:
                raise ToolError("every change needs a `path`")
            grouped.setdefault(pth, []).extend(self._edit_items(sp, pth))
        # validate everything first
        plans = []     # (path, key, p, before, after, notes, n_items)
        for pth, items in grouped.items():
            key, text, p, fmt = self._load(pth)
            unread = key not in self.seen
            if not unread and self.seen[key] != self._hash(text):
                raise ToolError(f"{pth} changed since you last read it (stale edit). Read it again, then edit.")
            after, notes = self._plan_many(text, items, pth, unread)
            if p is not None:
                bad = self._syntax_error(p, after) if not self._syntax_error(p, text) else None
                if bad:
                    raise ToolError(f"edit rejected: it would make {pth} stop parsing. Nothing was written; "
                                    f"{pth} is unchanged. Fix `new` at the marked place and retry.\n{bad}")
            plans.append((pth, key, p, text, after, notes, len(items), fmt))
        # write, restoring what was written if one write fails
        done = []
        try:
            for pth, key, p, before, after, _n, _c, fmt in plans:
                done.append((pth, p, before, fmt))
                if p is None:
                    self._cwrite(pth, after)
                else:
                    write_src(p, after, *fmt)
                    if self._hash(read_src(p)[0]) != self._hash(after):      # OSS-14: success only if the file changed
                        raise OSError(f"{pth} does not hold the new text after writing (another program changed it?)")
        except Exception as e:  # noqa: BLE001
            for pth, p, before, fmt in done:
                try:
                    self._cwrite(pth, before) if p is None else write_src(p, before, *fmt)
                except Exception:  # noqa: BLE001
                    pass
            raise ToolError(f"edit failed while writing ({e}); files already written were restored") from None
        for pth, key, p, before, after, _n, _c, _f in plans:
            self.seen[key] = self._hash(after)
            self.edits += 1
            self.written.add(key)
            self.turn_files.add(key)
        if len(plans) == 1:
            pth, _k, _p, _b, _a, notes, count, _f = plans[0]
            if count == 1 and edits is None:
                return f"edited {pth}{notes[0]}"
            fuzzy = sum(1 for x in notes if x)
            return f"edited {pth}: {count} edits applied" + (f" ({fuzzy} matched after normalising whitespace)" if fuzzy else "")
        total = sum(pl[6] for pl in plans)
        lines = [f"edited {len(plans)} files: {total} edits applied"]
        lines += [f"- {pl[0]}: {pl[6]} edit{'s' if pl[6] != 1 else ''}" for pl in plans]
        return "\n".join(lines)

    def notebook_edit(self, path=None, cell=None, source="", mode="replace", cell_type="code"):
        if not path or cell is None:
            raise ToolError("notebook_edit needs `path` and `cell`")
        p = self._path(path, write=True)
        if not p.is_file():
            raise ToolError(f"{path} does not exist")
        try:
            nb = json.loads(read_src(p)[0])
            cells = nb["cells"]
        except (ValueError, KeyError):
            raise ToolError(f"{path} is not a notebook (.ipynb JSON with cells)")
        n, i = len(cells), int(cell)
        lines = (source or "").splitlines(keepends=True)
        if mode == "insert":
            if not 0 <= i <= n:
                raise ToolError(f"cell {i} is out of range: the notebook has {n} cells (0 to {n} for insert)")
            new = {"cell_type": cell_type, "metadata": {}, "source": lines}
            if cell_type == "code":
                new.update({"execution_count": None, "outputs": []})
            cells.insert(i, new)
            did = f"inserted a {cell_type} cell at {i}"
        else:
            if not 0 <= i < n:
                raise ToolError(f"cell {i} is out of range: the notebook has {n} cells (0 to {n - 1})")
            if mode == "delete":
                cells.pop(i)
                did = f"deleted cell {i}"
            elif mode == "replace":
                cells[i]["source"] = lines
                if cells[i].get("cell_type") == "code":
                    cells[i]["outputs"], cells[i]["execution_count"] = [], None
                did = f"replaced cell {i}"
            else:
                raise ToolError("mode must be replace, insert or delete")
        self.write_file(path, json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
        return f"{did} in {path}; it now has {len(cells)} cells"

    def write_file(self, path, content, append=False):
        if self.container and not self.mounted:
            if subprocess.run(["docker", "exec", self.container, "test", "-e", self._cpath(path)]).returncode != 0:
                self.created.add(self._cpath(path))
            elif self._cpath(path) not in self.original:
                self.original[self._cpath(path)] = self._hash(self._cread(path))
            self._cwrite(path, content, append)
            self.seen[self._cpath(path)] = self._hash(self._cread(path))
            self.edits += 1
            self.written.add(self._cpath(path))
            self.turn_files.add(self._cpath(path))
            return f"wrote {path} ({len(content)} chars{', appended' if append else ''}) in the container"
        p = self._path(path, write=True)
        if p.is_dir():
            raise ToolError(f"{path or '.'} is a directory; give a file path")
        fmt = ("\n", False)                           # a new file: plain \\n endings on every platform
        same = False
        if not p.exists():
            self.created.add(str(p))
            self.turn_new.add(str(p))
        elif p.is_file():
            text, eol, bom = read_src(p)                # an existing file keeps its line endings and BOM
            same = not append and text == content.replace("\r\n", "\n")      # rewritten with the text it already had
            fmt = (eol, bom)
            if str(p) not in self.original:
                self.original[str(p)] = self._hash(text)
        p.parent.mkdir(parents=True, exist_ok=True)
        if append and p.exists():
            write_src(p, content, fmt[0], append=True)
        else:
            write_src(p, content.lstrip("\ufeff") if fmt[1] else content, *fmt)
        after = read_src(p)[0]
        self.seen[str(p)] = self._hash(after)
        if not self._in_scratch(p):                   # a scratch file is not project progress (or a review receipt)
            self.edits += 1
            self.written.add(str(p))
            if not same:                              # F-21: an unchanged rewrite is not a change
                self.turn_files.add(str(p))
        bad = self._syntax_error(p, after) if p.is_file() else None
        return f"wrote {path} ({len(content)} chars{', appended' if append else ''})" + (
            f"\nIt does not parse yet; fix it next:\n{bad}" if bad else "")

    should_stop = None          # set by the agent: a callable that says the user pressed Stop
    on_output = None            # set by the agent: on_output(call id, text) while a command runs (live output)
    get_mode = None             # set by the agent: a callable that gives the chat's access mode (Full access skips the sandbox)

    def _proc(self, args, env, timeout_s, shell=False, encoding=None, cwd=None):
        """Run a command the user can stop at any moment: the whole process tree is killed on Stop or timeout."""
        import signal
        kw = {"creationflags": 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
        p = subprocess.Popen(args, shell=shell, cwd=cwd or self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True, errors="replace", encoding=encoding, **kw)
        _BG_ALL.add(p)                                   # killed at exit too if this process ends mid-command
        box: dict = {}
        bufs = {"out": _Capped(), "err": _Capped()}
        cid, live = getattr(_LIVE, "cid", ""), self.on_output
        live_q = [] if (live and cid) else None           # what came since the last look, for the step's card
        over = threading.Event()

        def drain(stream, buf):
            import codecs
            dec = codecs.getincrementaldecoder(getattr(stream, "encoding", None) or "utf-8")(errors="replace")
            try:
                while True:
                    # read1: what is there now. stream.read(65536) waits for 65536 characters or the end of the pipe,
                    # which a background child keeps open (H-01).
                    raw = stream.buffer.read1(65536)
                    chunk = dec.decode(raw, final=not raw).replace("\r\n", "\n")
                    if not raw and not chunk:
                        break
                    if not chunk:
                        continue
                    buf.add(chunk)
                    if live_q is not None:
                        live_q.append(chunk)
                    if bufs["out"].total + bufs["err"].total > OUTPUT_KILL_CHARS:
                        over.set()                       # an endless producer: end it, keep head and tail
                        break
            except (OSError, ValueError):
                pass

        def pump():
            ts = [threading.Thread(target=drain, args=(p.stdout, bufs["out"]), daemon=True),
                  threading.Thread(target=drain, args=(p.stderr, bufs["err"]), daemon=True)]
            for t in ts:
                t.start()
            while any(t.is_alive() for t in ts) and not over.is_set():
                for t in ts:
                    t.join(0.1)
                if p.poll() is not None:                 # H-01: the command is done; a background child may still
                    for t in ts:                         # hold the pipes, so give the last output a moment, then stop
                        t.join(0.3)
                    if any(t.is_alive() for t in ts):
                        box["left_running"] = True
                        for st in (p.stdout, p.stderr):  # close our ends (in a thread: a blocked read holds the lock)
                            threading.Thread(target=_quiet_close, args=(st,), daemon=True).start()
                    break
            if over.is_set():
                _kill_tree(p)                            # the whole tree: taskkill /T /F on Windows, killpg elsewhere
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
            box["out"], box["err"] = bufs["out"].text(), bufs["err"].text()
        th = threading.Thread(target=pump, daemon=True)
        th.start()
        end = time.time() + timeout_s
        why = None
        t_live = time.time() + 0.6                      # a quick command shows its output once, at the end
        while th.is_alive():
            th.join(0.2)
            if live_q and time.time() >= t_live:        # what arrived since the last look, for the step's card
                got, live_q[:] = "".join(live_q), []
                try:
                    live(cid, got[-4000:])
                except Exception:  # noqa: BLE001 - showing progress must never break the command
                    pass
                t_live = time.time() + 0.4
            if th.is_alive() and self.should_stop and self.should_stop():
                why = "stopped"
            elif th.is_alive() and time.time() > end:
                why = "timeout"
            if why:
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                                       creationflags=0x08000000)
                    else:
                        os.killpg(p.pid, signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    p.kill()
                th.join(5)
                break
        _BG_ALL.discard(p)                              # finished, stopped or timed out: no longer ours to kill
        out, err = box.get("out") or "", box.get("err") or ""
        if why == "timeout":
            raise subprocess.TimeoutExpired(args, timeout_s, output=out)
        rc = 130 if why == "stopped" else p.returncode
        if over.is_set():
            err = (err + f"\n[output stopped after {OUTPUT_KILL_CHARS // 1_000_000} million characters]").lstrip()
        if why == "stopped":
            err = (err + "\n[stopped by the user]").lstrip()
        if box.get("left_running"):
            err = (err + "\n[the command finished but left a background process running; its output is not captured. "
                         "Start servers and watchers with bash_start to read their output and stop them]").lstrip()
        return subprocess.CompletedProcess(args, rc, out, err)

    def _docker_run(self, cdir, command, timeout_s):
        """A command inside the container. Killing the local docker client does not end what runs in the container,
        so the command runs under the container's own `timeout` when it has one, and on a timeout or Stop the process
        is also killed inside the container. Output is capped as for a local command (_proc)."""
        import secrets
        pidf = f"/tmp/.meshpool-run-{secrets.token_hex(6)}.pid"
        # the container's own limit is a little later than ours: normally our timeout ends it through _docker_kill,
        # and this one is the backstop when that step cannot run
        wrap = (f'if command -v timeout >/dev/null 2>&1; then timeout -k 5 {int(timeout_s) + 2} bash -lc "$1" & '
                f'else bash -lc "$1" & fi; echo $! > {pidf} 2>/dev/null; wait $!; rc=$?; rm -f {pidf}; exit $rc')
        try:
            r = self._proc(["docker", "exec", "-w", cdir, self.container, "bash", "-c", wrap, "meshpool", command],
                           None, timeout_s)
        except subprocess.TimeoutExpired:
            self._docker_kill(pidf)
            raise
        if r.returncode == 130 and "[stopped by the user]" in (r.stderr or ""):
            self._docker_kill(pidf)
        return r

    def _docker_kill(self, pidf):
        try:
            subprocess.run(["docker", "exec", self.container, "sh", "-c",
                            # `timeout` leads its own process group, so the group signal reaches what it started
                            f'p=$(cat {pidf} 2>/dev/null) || exit 0; kill -TERM -- "-$p" 2>/dev/null || '
                            f'kill -TERM "$p" 2>/dev/null; sleep 1; kill -KILL -- "-$p" 2>/dev/null || '
                            f'kill -KILL "$p" 2>/dev/null; rm -f {pidf}'],
                           capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass

    def _in_scratch(self, p) -> bool:
        d = self.__dict__.get("_scratch_dir")
        try:
            return bool(d) and Path(d).resolve() in Path(p).resolve().parents
        except (OSError, ValueError):
            return False

    def _scratch(self, env: dict) -> dict:
        """A private temp folder per workspace for every command it runs: scratch files (check scripts, repro copies) of
        one agent cannot be read or overwritten by another running at the same time (parallel subagents, a second
        chat, the benchmark's parallel runs: eval traces showed agents clobbering each other's /tmp/check.py)."""
        d = self.__dict__.get("_scratch_dir")
        if not d or not os.path.isdir(d):
            import tempfile
            _prune_scratch(tempfile.gettempdir())
            try:
                d = self.__dict__["_scratch_dir"] = tempfile.mkdtemp(prefix="meshpool-scratch-")
            except OSError:
                return env
            import weakref                              # removed with the workspace, or at exit at the latest
            weakref.finalize(self, shutil.rmtree, d, True)
        env["TMPDIR"] = env["TMP"] = env["TEMP"] = d
        return env

    def _run(self, command, timeout_s, kind, record=True, raw=False, ctx=True, cwd=None, login=False):
        cap = 1800 if os.environ.get("MH_MODE") == "terminal" else 900   # terminal tasks: builds, training runs
        timeout_s = max(1, min(int(timeout_s or (300 if kind == 'check' else 180)), cap))
        t0 = time.time()
        try:
            from meshpool_harness import config as _C
            if _C.packaged():                                       # the user's own tools, not Meshpool's Python
                env = _C.user_env()
            else:
                env = self._py_env(_C.scrub_env(dict(os.environ)))
            if self.container and not command.lstrip().startswith("docker "):
                cdir = self.cdir if not cwd else self._cpath(os.path.relpath(cwd, self.root).replace(os.sep, "/"))
                r = self._docker_run(cdir, command, timeout_s)
            else:
                env.setdefault("PYTHONUTF8", "1")
                self._scratch(env)
                bash = _bash()
                from meshpool_harness.core import sandbox as _SB
                mode = self.get_mode() if self.get_mode else None
                if bash and os.name == "nt":                     # Git Bash: keep the working folder
                    env["CHERE_INVOKING"] = "1"
                    _C.msys_lang(env)                            # no "/usr/bin/locale: No such file" in every output
                    if _SB.requested(mode):
                        _SB.wrap(["x"], self.root, platform="win32")     # says once that it is not available here
                    r = self._proc([bash, "-lc" if login else "-c", fix_nul_redirect(command)], env, timeout_s,
                                   encoding="utf-8", cwd=cwd)
                elif bash:
                    offline = command.strip() in self.__dict__.get("sbx_offline", ())   # ran without a question
                    r = self._proc(_SB.wrap([bash, "-lc" if login else "-c", command], self.root, cwd=cwd, mode=mode,
                                            offline=offline), env, timeout_s, cwd=cwd)
                elif os.name == "nt":                            # no Git Bash: PowerShell, not cmd.exe
                    r = self._proc(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                    "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command],
                                   env, timeout_s, encoding="utf-8", cwd=cwd)
                else:
                    r = self._proc(command, env, timeout_s, shell=True, cwd=cwd)
            code, out = r.returncode, _C.redact_secrets(r.stdout + ("\n" + r.stderr if r.stderr else ""))   # OSS-29
        except subprocess.TimeoutExpired as e:
            from meshpool_harness import config as _C
            code = 124                           # OSS-29: what a command printed before its timeout is redacted too
            out = _C.redact_secrets((e.stdout or "") if isinstance(e.stdout, str) else "") + \
                f"\n[timed out after {timeout_s}s]"
        if not record:
            return self._cap(out or "no matches", kind), code
        asked = self.__dict__.get("asked_cmds") or ()
        self.commands.append({"cmd": command.strip(), "code": code, "kind": kind, "after_edit": self.edits,
                              "secs": round(time.time() - t0, 1), "tail": out[-600:],
                              "cwd": str(cwd) if cwd else None,            # re-check runs it in the same folder
                              "approved_once": command.strip() in asked})  # a yes for this run is not a yes for more
        try:                                     # full outputs: evidence for number tracing; the task may have
            self.run_dir.mkdir(parents=True, exist_ok=True)   # deleted .run (git clean), so recreate it
            with (self.run_dir / "commands.log").open("a", encoding="utf-8", errors="replace") as f:
                f.write(f"\n$ {_C.redact_secrets(command.strip())}\n[exit {code}]\n{out[:200000]}\n")
        except OSError:
            pass
        if raw:                                  # probe/trace post-process the full text themselves
            return f"[exit {code}]\n{out}", code
        return self._cap(f"[exit {code}]\n{out}", kind) + (self._where(out, code, command) if ctx else ""), code

    def _where(self, out, code, command) -> str:
        """MH_ERROR_CONTEXT: excerpts of the project code a failing command points to (core/errctx.py)."""
        from . import errctx
        on = errctx.enabled() or getattr(self, "error_context", False)   # the routed code harnesses turn it on
        return errctx.context(out, self, command) if code not in (0, 124) and on else ""

    def bash(self, command, timeout_s=None):
        return self._run(command, timeout_s, "bash")[0]

    # ---- background commands: started now, read and stopped later (a dev server, a watcher)
    MAX_BG = 6
    BG_KEEP = 400_000                      # characters of output kept per command

    def bash_start(self, command, name=None):
        if self.container and not self.mounted:
            raise ToolError("background commands run on this computer, not inside a container")
        bg = self.__dict__.setdefault("_bg", {})
        if sum(1 for e in bg.values() if e["p"].poll() is None) >= self.MAX_BG:
            raise ToolError(f"{self.MAX_BG} background commands are already running; stop one with bash_stop first")
        from meshpool_harness import config as _C
        if _C.packaged():
            env = _C.user_env()
        else:
            env = self._py_env(_C.scrub_env(dict(os.environ)))
        env.setdefault("PYTHONUTF8", "1")
        env["PYTHONUNBUFFERED"] = "1"
        self._scratch(env)
        bash = _bash()
        kw = {"creationflags": 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
        if bash:
            args, shell = [bash, "-c", command], False
            if os.name != "nt":
                from meshpool_harness.core import sandbox as _SB
                args = _SB.wrap(args, self.root, mode=self.get_mode() if self.get_mode else None)
            else:
                args = [bash, "-c", fix_nul_redirect(command)]
        elif os.name == "nt":
            args, shell = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                           "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command], False
        else:
            args, shell = command, True
        # Git Bash and PowerShell (with its OutputEncoding set) write UTF-8 on Windows, as in _run: the locale's code
        # page (cp1252) turned every non-ASCII character of a dev server's output into mojibake
        p = subprocess.Popen(args, shell=shell, cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, errors="replace",
                             encoding="utf-8" if os.name == "nt" else None, **kw)
        _BG_ALL.add(p)
        n = self.__dict__["_bg_n"] = self.__dict__.get("_bg_n", 0) + 1
        bid = f"bg{n}"
        entry = {"p": p, "command": command.strip(), "name": (name or "").strip()[:40], "text": [], "size": 0,
                 "read": 0, "t0": time.time(), "lock": threading.Lock()}
        bg[bid] = entry

        def pump():
            for line in p.stdout:
                with entry["lock"]:
                    entry["text"].append(line)
                    entry["size"] += len(line)
                    while entry["size"] > self.BG_KEEP and len(entry["text"]) > 1:      # keep the newest output
                        entry["size"] -= len(entry["text"].pop(0))
                        entry["read"] = max(0, entry["read"] - 1)
        threading.Thread(target=pump, daemon=True).start()
        time.sleep(0.4)                                            # a command that fails at once says so now
        return (f"started {bid}{' (' + entry['name'] + ')' if entry['name'] else ''}, pid {p.pid}. "
                f"Read its output with bash_output id={bid}; stop it with bash_stop id={bid}.\n" + self._bg_read(bid, entry))

    def _bg_read(self, bid, entry):
        from meshpool_harness import config as _C
        with entry["lock"]:
            new = _C.redact_secrets("".join(entry["text"][entry["read"]:]))   # OSS-29, as for bash
            entry["read"] = len(entry["text"])
        rc = entry["p"].poll()
        state = "running" if rc is None else f"exited with code {rc}"
        return f"[{bid} {state}]\n" + (self._cap(new, "bash") if new else "(no new output)")

    def bash_output(self, id=None, wait_s=0):
        bg = self.__dict__.get("_bg", {})
        if not id:
            if not bg:
                return "no background commands"
            return "\n".join(f"{b}: {'running' if e['p'].poll() is None else 'exited ' + str(e['p'].poll())}  "
                             f"{e['name'] or e['command'][:60]}" for b, e in bg.items())
        e = bg.get(id)
        if e is None:
            raise ToolError(f"no background command {id}. Running: {', '.join(bg) or 'none'}")
        try:
            wait = max(0, min(int(wait_s or 0), 60))
        except (TypeError, ValueError):
            wait = 0
        end = time.time() + wait
        while wait and time.time() < end and e["p"].poll() is None and e["read"] >= len(e["text"]):
            time.sleep(0.2)
        return self._bg_read(id, e)

    def bash_stop(self, id=None):
        bg = self.__dict__.get("_bg", {})
        ids = [id] if id else list(bg)
        if id and id not in bg:
            raise ToolError(f"no background command {id}. Running: {', '.join(bg) or 'none'}")
        stopped = []
        for b in ids:
            _kill_tree(bg[b]["p"])
            stopped.append(b)
        return ("stopped " + ", ".join(stopped)) if stopped else "no background commands"

    def bash_many(self, commands, timeout_s=None):
        """Several independent commands in one tool call: one turn instead of many (Terminus 2 / KIRA style).
        MH_PARALLEL_MANY=1: when every command is read-only (see read_only), run them at the same time; the turn then
        takes as long as the slowest command instead of the sum. Output order and format are unchanged."""
        cmds = [c for c in commands if isinstance(c, str) and c.strip()][:8]

        def one(cmd):
            if self.should_stop and self.should_stop():
                return f"$ {cmd}\n[not run: stopped by the user]"
            out, code = self._run(cmd, timeout_s, "bash", ctx=False)
            body = out.split("\n", 1)[1] if out.startswith("[exit ") else out
            if len(body) > 2500:
                body = body[:1200] + f"\n... [{len(body) - 2400} chars cut; run it alone for the full output] ...\n" + body[-1200:]
            return f"$ {cmd}\n[exit {code}]\n{body}" + self._where(out, code, cmd)
        if os.environ.get("MH_PARALLEL_MANY") == "1" and len(cmds) > 1 and all(read_only(c) for c in cmds):
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(cmds))) as ex:
                parts = list(ex.map(one, cmds))
        else:
            parts = [one(c) for c in cmds]
        return "\n\n".join(parts) or "no commands given"

    def probe(self, command, timeout_s=None):
        """MH_OBSERVE: run a check and return a failure digest (core/observe.py). Recorded as a check."""
        from .observe import digest, probe_command
        out, code = self._run(probe_command(command), timeout_s, "check", raw=True)
        return digest(out) + self._where(out, code, command)

    def trace(self, command, timeout_s=None):
        """MH_OBSERVE: run a Python command under a project-only profiler (core/observe.py)."""
        from .observe import trace_command
        wrapped = trace_command(command)
        if wrapped is None:
            raise ToolError("trace runs one Python command: python file.py ..., python -m module ..., or pytest ... "
                            "(optionally after `cd dir &&`), without pipes or redirects")
        out, _ = self._run(wrapped, timeout_s, "bash", raw=True)
        i = out.rfind("=== trace:")
        if i < 0:
            return self._cap(out, "trace")
        return self._cap((out[:i][-1500:] + "\n" + out[i:])[-7000:], "trace")

    def run_tests(self, command, timeout_s=None):
        return self._run(command, timeout_s, "check")[0]

    # -- the Codex tools (engine style "codex")
    def shell(self, command, workdir=None, timeout_ms=None):
        """Codex's shell tool: command (argv list or string), workdir, timeout_ms. Runs through the same path as bash
        (timeout, stop, output cap, error context). An apply_patch given through the shell is applied as a patch."""
        hit = patch_from_shell(command)
        if hit:
            patch, cd = hit
            return self.apply_patch(patch, base=cd or workdir)
        sp = split_patch_script(command)
        if sp and sp[2]:                              # a patch, then other commands: the patch as an edit, then the rest
            patch, cd, rest, strict = sp
            try:
                done = self.apply_patch(patch, base=cd or workdir)
            except ToolError as e:
                if strict:
                    raise ToolError(f"{e}\nThe commands after the patch were not run.") from None
                done = f"apply_patch failed: {e}"
            return done.rstrip("\n") + "\n" + self.shell(rest, workdir=workdir, timeout_ms=timeout_ms)
        script = shell_script(command)
        if not script.strip():
            raise ToolError("shell: `command` is empty")
        try:
            from .applypatch import parse
            parse(script)
        except Exception:  # noqa: BLE001 - not a patch: an ordinary command
            pass
        else:
            raise ToolError("patch detected without explicit call to apply_patch. Rerun as apply_patch with the "
                            "patch as `input` (or [\"apply_patch\", \"<patch>\"] here).")
        cwd = None
        if workdir:
            d = self._path(str(workdir))
            if not d.is_dir() and not (self.container and not self.mounted):
                raise ToolError(f"workdir not found: {workdir}")
            cwd = d
        again = self._reread(script, cwd)
        if again:
            return again
        secs = None
        if timeout_ms is not None:
            secs = max(1, -(-int(timeout_ms) // 1000))
        out = self._run(script, secs, "bash", cwd=cwd)[0]
        self._remember_cat(script, cwd, out)
        return out

    def exec_command(self, cmd, workdir=None, yield_time_ms=None, max_output_tokens=None, login=None, shell=None,
                     tty=None, justification=None, sandbox_permissions=None, prefix_rule=None):
        """Codex 0.171's exec_command (profile "c171"): `cmd` runs with bash -lc (bash -c when login is false) in
        workdir (default: the project folder), on the same runner as bash and shell (stop, private $TMPDIR, the
        user's own Python, the sandbox, the command log). Not a PTY session: the command runs to the end, and
        yield_time_ms, when given, is its time limit (default: the bash tool's). The result is Codex's format
        (exec_result): no Meshpool notes, no saved-output pointer, no error-context excerpts. An apply_patch given
        as a heredoc is applied as a patch, as Codex intercepts it."""
        if not isinstance(cmd, str) or not cmd.strip():
            raise ToolError("exec_command: `cmd` is empty")
        cwd = None
        if workdir:
            d = self._path(str(workdir))
            if not d.is_dir() and not (self.container and not self.mounted):
                raise ToolError(f"workdir not found: {workdir}")
            cwd = d
        t0 = time.time()
        hit = patch_from_shell(cmd)
        if hit:
            try:
                out, code = self.apply_patch(hit[0], base=hit[1] or workdir), 0
            except ToolError as e:
                out, code = str(e), 1
            return exec_result(out if out.endswith("\n") else out + "\n", code, time.time() - t0, max_output_tokens)
        pre = ""
        for _ in range(20):                           # a patch (or several), then other commands: each patch as an
            sp = split_patch_script(cmd)              # edit, then the rest as commands of their own
            if not (sp and sp[2]):
                break
            try:
                done = self.apply_patch(sp[0], base=sp[1] or workdir)
            except ToolError as e:
                if sp[3]:
                    return exec_result(f"{pre}{e}\nThe commands after the patch were not run.\n", 1, time.time() - t0,
                                       max_output_tokens)
                done = f"apply_patch failed: {e}"
            pre, cmd = pre + done.rstrip("\n") + "\n", sp[2]
        hit = patch_from_shell(cmd) if pre else None
        if hit:                                       # the last part is a patch too
            try:
                out, code = pre + self.apply_patch(hit[0], base=hit[1] or workdir), 0
            except ToolError as e:
                out, code = pre + str(e), 1
            return exec_result(out if out.endswith("\n") else out + "\n", code, time.time() - t0, max_output_tokens)
        secs = None
        if isinstance(yield_time_ms, (int, float)) and not isinstance(yield_time_ms, bool):
            secs = max(1, -(-int(max(250, yield_time_ms)) // 1000))
        text, code = self._run(cmd, secs, "bash", raw=True, cwd=cwd, login=login is not False)
        out = text.split("\n", 1)[1] if text.startswith("[exit ") and "\n" in text else text
        return exec_result(pre + out, code, time.time() - t0, max_output_tokens)

    # -- subsumed retrieval: a whole-file read of text the model already has, unchanged, is answered from memory.
    # Measured on coding agents, re-reading code already in context occurs 2-6 times per task (arXiv 2609.30725);
    # each such read sends the file again as new, uncached input and costs a round trip of its own.
    _CAT = re.compile(r"^\s*(?:cat|cat -n|nl -ba)\s+(['\"]?)([^\s'\";|&<>*?]+)\1\s*$")

    def _known(self) -> dict:
        return self.__dict__.setdefault("_known_text", {})

    def remember_text(self, path, text: str, where: str) -> None:
        """Note that the model has the full current text of this file (where: how it got it)."""
        self._known()[str(Path(path).resolve())] = (self._hash(text), where)

    def _cat_target(self, script: str, cwd):
        if os.environ.get("MH_REREAD_NOTE", "1") == "0" or (self.container and not self.mounted):
            return None
        m = self._CAT.match(script.strip())
        if not m:
            return None
        p = Path(m.group(2))
        p = (Path(cwd or self.root) / p) if not p.is_absolute() else p
        return p.resolve()

    def _reread(self, script: str, cwd) -> str:
        p = self._cat_target(script, cwd)
        if p is None or str(p) not in self._known():
            return ""
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        h, where = self._known()[str(p)]
        if h != self._hash(text):
            return ""                             # changed since: a real read
        vis = getattr(self, "visible", None)
        if vis is not None and not vis(text):    # the history was compacted or trimmed: the text is gone, print it
            self._known().pop(str(p), None)
            return ""
        rel = os.path.relpath(p, self.root)
        return (f"[exit 0]\n[Meshpool] {rel} is unchanged since {where}; its full text is already in this "
                "conversation above, so it was not printed again. Use it from there (or read a line range if you "
                "need exact text for an edit).")

    def _remember_cat(self, script: str, cwd, out: str) -> None:
        p = self._cat_target(script, cwd)
        if p is None or not str(out).startswith("[exit 0]") or "[Meshpool]" in str(out)[:200]:
            return
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if len(text) <= self.max_tool_chars and len(str(out)) >= len(text):     # printed whole, not cut
            self.remember_text(p, text, "you printed it")

    def apply_patch(self, input, base=None):
        """Codex's apply_patch: every hunk is checked and every new file built in memory first; only then are the files
        written, so a patch that cannot be applied changes nothing. Code files that would stop parsing are refused."""
        from .applypatch import PatchError, apply_update, parse
        try:
            hunks = parse(input)
        except PatchError as e:
            raise ToolError(str(e)) from None
        remote = bool(self.container and not self.mounted)
        base = str(base) if base else ""
        shown: dict = {}                              # key -> path as the patch names it

        def loc(path: str):
            if "$" in path or path.startswith("~"):
                path = expand_path(path)           # before the base is joined: $HOME/x is the home folder's x
            full = os.path.join(base, path) if base and not os.path.isabs(path) else path
            if remote:
                return self._cpath(full), None
            p = self._path(full, write=True)
            return str(p), p

        files: dict = {}                              # key -> [path object, text before or None, text after or None]
        order: list = []
        boms: set = set()                             # files that start with a UTF-8 BOM: matched without it, kept

        def get(path: str):
            key, p = loc(path)
            if key not in files:
                if remote:
                    r = self._cexec(["cat", key])
                    before = r.stdout if r.returncode == 0 else None
                elif p.is_file():
                    before = p.read_bytes().decode("utf-8", "surrogateescape")
                else:
                    before = None
                if before is not None and before.startswith("\ufeff"):
                    before = before[1:]               # else the first line never matches the patch's context
                    boms.add(key)
                files[key] = [p, before, before]
                order.append(key)
            shown.setdefault(key, path)
            return key, files[key]

        added, modified, deleted = [], [], []
        for h in hunks:
            if h.kind == "add":
                key, f = get(h.path)
                f[2] = h.contents
                added.append(h.path)
            elif h.kind == "delete":
                key, f = get(h.path)
                if f[2] is None:
                    raise ToolError(f"Failed to delete file {h.path}: no such file")
                f[2] = None
                deleted.append(h.path)
            else:
                key, f = get(h.path)
                if f[2] is None:
                    raise ToolError(f"Failed to read file to update {h.path}: no such file")
                try:
                    new = apply_update(f[2], h.path, h.chunks)
                except PatchError as e:
                    raise ToolError(str(e)) from None
                if h.move and h.move != h.path:
                    k2, f2 = get(h.move)
                    f2[2], f[2] = new, None
                    if key in boms:
                        boms.add(k2)
                    modified.append(h.move)
                else:
                    f[2] = new
                    modified.append(h.path)
        for key in order:                             # a code file may not stop parsing because of the patch
            p, before, after = files[key]
            if after is None or p is None or after == before:
                continue
            bad = self._syntax_error(p, after)
            if bad and (before is None or not self._syntax_error(p, before)):
                raise ToolError(f"apply_patch rejected: it would make {shown[key]} stop parsing. Nothing was "
                                f"written. Fix the patch at the marked place and retry.\n{bad}")
        def raw(key, text):
            return (("\ufeff" if key in boms else "") + text).encode("utf-8", "surrogateescape")

        done: list = []                               # write, restoring everything if one write fails
        try:
            for key in order:
                p, before, after = files[key]
                if after == before:
                    continue
                done.append(key)
                if remote:
                    if after is None:
                        self._cexec(["rm", "-f", key])
                    else:
                        self._cwrite(key, ("\ufeff" if key in boms else "") + after)
                elif after is None:
                    p.unlink()
                    _drop_pyc(p)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(raw(key, after))
                    _drop_pyc(p)
        except Exception as e:  # noqa: BLE001
            for key in done:
                p, before, after = files[key]
                try:
                    if remote:
                        self._cexec(["rm", "-f", key]) if before is None else self._cwrite(key, ("\ufeff" if key in boms else "") + before)
                    elif before is None:
                        p.unlink(missing_ok=True)
                    else:
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(raw(key, before))
                except Exception:  # noqa: BLE001
                    pass
            raise ToolError(f"apply_patch failed while writing ({e}); the files written so far were restored") from None
        for key in done:
            p, before, after = files[key]
            if before is None and after is not None:
                self.created.add(key)
                if not self._in_scratch(p):           # the receipt marks it "(new)", as for write_file
                    self.turn_new.add(key)
            elif before is not None and key not in self.original:
                self.original[key] = self._hash(before)
            if after is None:
                self.seen.pop(key, None)
            else:
                self.seen[key] = self._hash(after)
                self.written.add(key)
            if not self._in_scratch(p):               # a scratch file is not project progress (or a review receipt)
                self.turn_files.add(key)
                self.edits += 1
        lines = ["Success. Updated the following files:"]
        lines += [f"A {x}" for x in added] + [f"M {x}" for x in modified] + [f"D {x}" for x in deleted]
        return "\n".join(lines) + self._recheck(added + modified)

    # -- re-check on edit: after a successful patch to project code, the model's most recent check command is run again
    # and its result rides on the patch result. The edit-then-run-the-same-check loop is the largest remaining gap to
    # Codex (2.2-2.4 check turns between edits per task against 0-0.6); SWE-agent's lint-on-edit showed feedback
    # attached to the edit removes those rounds. Only a short check that ran in this task (under 60 s), only on code.
    _CHECKISH = re.compile(r"\b(pytest|python3?|node|npm (?:run )?test|go test|cargo test|ruby|bash \S+\.sh|sh \S+\.sh)\b")
    _WRITES = re.compile(r"(cat\s*>|>\s*[\w./$\"'-]+\.(?:py|js|ts|json|txt|csv)\b|tee |sed -i|rm |mv |cp |git |pip |npm i)")

    # What may be re-run unasked: a test runner, an inline assertion (python -c / node -e), a test file, or a check
    # script in the task's scratch folder. Never a project script with side effects (an importer writing rows twice),
    # and never sudo, network or destructive commands, which the user may have approved once (review, 0.0.16).
    _TEST_RUNNER = re.compile(r"\b(pytest|unittest|tox|nox|npm (?:run )?test|yarn test|pnpm test|go test|cargo test|jest|"
                              r"vitest|mocha|rspec|phpunit|dotnet test|mvn test|gradle test)\b|\bpython3?\s+-c\b|"
                              r"\bnode\s+-e\b|(?:^|[\s/])(?:test_\w+|\w+_test)\.py\b|\$TMPDIR|\$\{TMPDIR\}")
    _UNSAFE = re.compile(r"\b(sudo|doas|su|curl|wget|ssh|scp|rsync|docker|kubectl|psql|mysql|mongo|redis-cli|dd|mkfs|"
                         r"shutdown|reboot|kill|pkill|chmod|chown)\b|\brm\s|git\s+(?:push|reset|clean|checkout)")

    def _recheckable(self, cmd: str) -> bool:
        from .permrules import DESTRUCTIVE
        if self._UNSAFE.search(cmd) or DESTRUCTIVE.search(cmd):
            return False
        scratch = str(self.__dict__.get("_scratch_dir") or "")              # the task's private $TMPDIR
        return bool(self._TEST_RUNNER.search(cmd)) or bool(scratch and len(scratch) > 4 and scratch in cmd)

    def _last_check(self):
        for c in reversed(self.commands):
            cmd = c.get("cmd", "")
            if c.get("kind") != "bash" or not self._CHECKISH.search(cmd) or self._WRITES.search(cmd):
                continue
            if (c.get("secs") or 0) > 60 or len(cmd) > 2000 or not self._recheckable(cmd) or c.get("approved_once"):
                return None                       # a command the user had to say yes to is never re-run unasked
            return c
        return None

    def _recheck(self, changed: list) -> str:
        if not getattr(self, "recheck", False) or os.environ.get("MH_RECHECK", "1") == "0":
            return ""
        if (self.get_mode() if self.get_mode else None) != "auto":
            return ""                             # only where commands already run unasked: a re-run is a new run
        if not any(str(x).endswith((".py", ".js", ".mjs", ".cjs", ".ts", ".go", ".rs", ".rb", ".java")) for x in changed):
            return ""
        c = self._last_check()
        if c is None:
            return ""
        try:          # a slow check (a big repo's test run) is not re-run after every patch: broad2 swe_sympy, 2.3x time
            slow = float(os.environ.get("MH_RECHECK_MAX_S", "10") or 0)
        except ValueError:
            slow = 10.0
        if slow > 0 and float(c.get("secs") or 0) > slow:
            return ""
        limit = int(min(60, max(10, 3 * float(c.get("secs") or 0))))   # a check that got slow is cut short, not waited on
        cwd = Path(c["cwd"]) if c.get("cwd") else None     # the shell call's workdir, not the project root
        if cwd is not None and not (self.container and not self.mounted) and not cwd.is_dir():
            return ""
        out, code = self._run(c["cmd"], limit, "bash", cwd=cwd)
        out = str(out)
        if len(out) > 4000:
            out = out[:1500] + "\n[...]\n" + out[-2000:]
        return (f"\n\n[Meshpool re-ran your last check after this edit: `{c['cmd'][:200]}`]\n{out}\n"
                "(Do not run that check again yourself unless you change it.)")

    def all_output(self) -> str:
        p = self.run_dir / "commands.log"
        return p.read_text(errors="replace") if p.exists() else ""

    def todo(self, items):
        self.todos = [{"text": str(i.get("text", ""))[:200], "done": bool(i.get("done"))} for i in items
                      if isinstance(i, dict)][:30]
        left = [t for t in self.todos if not t["done"]]
        return ("todo saved: " + f"{len(self.todos) - len(left)}/{len(self.todos)} done" + "".join(
            f"\n- [{'x' if t['done'] else ' '}] {t['text']}" for t in self.todos))

    def load_skill(self, name):
        if name not in self.skills:
            raise ToolError(f"unknown skill '{name}'. Available: {', '.join(self.skills) or 'none'}")
        return self.skills[name]
