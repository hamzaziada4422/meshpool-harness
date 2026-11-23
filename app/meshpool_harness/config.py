"""Settings file (meshpool.config) and local tool discovery.

The config file is plain KEY=VALUE lines. Values already set in the shell environment win, so a one-off
`MH_MODEL=... meshpool run ...` still works. The file path comes from MESHPOOL_CONFIG, else the folder above the app.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

# Keys the web app shows and edits, in display order. (key, label, default, help)
FIELDS = [
    ("MESHPOOL_API_KEY", "API key", "", "Your Meshpool key (or any OpenAI-compatible key)."),
    ("MESHPOOL_BASE_URL", "Endpoint", "https://llm.meshpool.ai/v1", "OpenAI-compatible base URL."),
    ("MH_MODEL", "Model", "deepseek-v4.1-flash", "Any model id the endpoint serves."),
    ("MH_AUTH_HEADER", "Key header", "", "Optional. The header that carries the key when it is not Authorization: "
     "Bearer: api-key (Azure OpenAI), x-api-key, Authorization. Empty picks the right one for the endpoint."),
    ("MH_AUTH_SCHEME", "Key scheme", "", "Optional. How the key is sent: bearer, basic, token or raw (the key alone)."),
    ("MESHPOOL_AZURE_API_VERSION", "Azure api-version", "", "Optional. The api-version sent to an Azure OpenAI "
     "endpoint. Empty uses the current GA version."),
    ("MH_PROVIDER", "Provider", "meshpool", "What the models run on: meshpool (your Meshpool key), custom (another "
     "OpenAI-compatible key) or chatgpt (your ChatGPT sign-in)."),
    ("MH_AGENT_PERMISSIONS", "What agents may do without asking", "ask",
     "Ask asks before every change and command. Accept edits changes files without asking but asks before "
     "commands. Full access never asks."),
    ("MH_BUDGET_USD", "Spending limit per task (USD)", "0", "A task stops when it reaches this. 0 means no limit."),
    ("MH_ONBOARDED", "Setup finished", "0", "Set when the first-run steps are done."),
    ("MH_AUTO_SUBSCRIPTIONS", "Let Auto use your ChatGPT subscription", "0",
     "On: Meshpool Auto may pick an agent on your ChatGPT sign-in when you did not choose it as the provider (it "
     "uses that plan's limits). Off: Auto stays on the provider you chose."),
    ("MH_SUBAGENTS", "Sub-agents", "1", "On: an agent can hand self-contained parts of a job to sub-agents that "
     "work at the same time, each in a fresh context, and report back. Off: it does the parts itself."),
    ("MH_SUBAGENT_MODEL", "Sub-agent model", "", "The model sub-agents run on. Empty: the same model as the chat that "
     "starts them."),
    ("MH_SUBAGENT_ACCESS", "What sub-agents may do", "inherit", "Same as the chat: the chat's tools and permission "
     "mode; anything that would need your yes is refused (they cannot ask), unless they may approve their own "
     "actions below. Read and edit files: no commands. Read and research: files and the web only. They work inside "
     "the chat's turn, so Undo covers what they change and Stop stops them. Sub-agents started from Hermes, Codex "
     "or Pi only read."),
    ("MH_SUBAGENT_AUTO_APPROVE", "Sub-agents approve their own actions", "0", "On: what would need your yes in the "
     "chat (a command in Ask, say) is allowed once for a sub-agent. Off: it is refused and the sub-agent "
     "reports it."),
    ("MH_SUBAGENT_PARALLEL", "Sub-agents at the same time", "4", "1 to 10."),
    ("MH_SUBAGENT_STEPS", "Steps per sub-agent", "50", "The most tool steps one sub-agent takes before it reports "
     "(5 to 250)."),
    ("MH_SUBAGENT_MINUTES", "Sub-agent time limit (minutes)", "30", "All sub-agents of one job stop at this and "
     "report what they have. 0: no limit."),
    ("MH_SUBAGENT_DEPTH", "Sub-agent levels", "1", "1: sub-agents do their part themselves. 2 or 3: they may hand "
     "parts on to sub-agents of their own."),
    ("MH_MAX_INFLIGHT", "Model calls at the same time", "4", "Per key, across all chats and sub-agents. While the "
     "endpoint is recovering from an outage, calls go one at a time."),
    ("MH_BROWSER_USE", "Browser use: which browser", "meshpool",
     "meshpool: Meshpool's own browser window (default). installed: the Chrome, Edge or Brave on this computer, with a "
     "profile folder that belongs to Meshpool (Chrome does not allow automation of your everyday profile). attach: "
     "connect to a browser you started with --remote-debugging-port."),
    ("MH_BROWSER_CDP_URL", "Browser use: debugging address (attach)", "http://127.0.0.1:9222",
     "Where your browser listens when you started it with --remote-debugging-port=9222 and its own --user-data-dir."),
    ("MH_BROWSER_HEADLESS", "Browser use: show a window", "",
     "Empty (the default): Meshpool's browser stays hidden and shows inside the app, in the Browser panel. 0: it also "
     "opens a window of its own."),
    ("MH_BROWSER_APP", "Browser use: which installed browser", "",
     "chrome, edge or brave. Empty: the first one found."),
    ("GITHUB_TOKEN", "GitHub token", "", "Optional. Connects GitHub (issues, pull requests, Actions runs) through "
                                       "the GitHub MCP server. Create one at github.com/settings/tokens."),
    ("BRAVE_SEARCH_API_KEY", "Brave Search API key", "", "Optional. Faster, steadier web search (free tier at brave.com/search/api)."),
    ("TAVILY_API_KEY", "Tavily API key", "", "Optional. Web search built for agents (free tier at tavily.com)."),
    ("SERPER_API_KEY", "Serper API key", "", "Optional. Google results through serper.dev."),
    ("SEARXNG_URL", "SearXNG address", "", "Optional. Your own SearXNG search server, for example http://localhost:8888."),
    ("INSTANTLY_API_KEY", "Instantly API key", "", "Set from Settings, Connectors."),
    ("MH_SANDBOX", "Sandbox for commands", "", "Optional. 1 = commands can write only in the project and temporary folders (Linux needs bubblewrap; macOS has it built in)."),
]
SECRET = {"MESHPOOL_API_KEY", "GITHUB_TOKEN", "BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY",
          "INSTANTLY_API_KEY"}


def replace_file(src, dst, keep_src: bool = False) -> None:
    """os.replace that survives Windows: a reader, the search indexer or antivirus holding the target or the fresh
    file for a moment makes the rename fail with PermissionError, so try again briefly before giving up. A temp file
    the caller just wrote is removed on failure; `keep_src` keeps it (a finished download worth retrying later)."""
    import time
    n = (100 if keep_src else 20) if os.name == "nt" else 1
    for i in range(n):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == n - 1:
                if not keep_src:
                    try:
                        os.unlink(src)
                    except OSError:
                        pass
                raise
            time.sleep(0.05)


def home() -> Path:
    """Where settings, MCP config and runs live: MESHPOOL_HOME when set (the desktop apps and the launchers set
    it), else the per-user data folder. Never the source tree: a checkout must stay clean (C-02, C-90)."""
    h = os.environ.get("MESHPOOL_HOME")
    return Path(h).expanduser() if h else user_home_default()


def runs_dir() -> Path:
    return Path(os.environ["MH_RUNS_DIR"]) if os.environ.get("MH_RUNS_DIR") else home() / "runs"


def user_home_default() -> Path:
    """The per-user data folder the desktop apps use."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Meshpool"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Meshpool"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "meshpool"


# What lives in the data folder. An older layout kept it in the source tree (or, on Linux, under ~/.config).
_HOME_ITEMS = ("meshpool.config", "mcp.json", "mcp-auth.json", "connectors.json", "projects.json", "runs", "skills",
               "memory", "brand", "harnesses", "mcp-health.json", "mcp-tools-cache.json")


def _legacy_homes() -> list:
    out = [APP.parent]
    if sys.platform != "darwin" and os.name != "nt":
        out.append(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "meshpool")
    return out


def _migrate_legacy_home(h: Path) -> None:
    """Copy (never move) the settings and data an older layout kept elsewhere into the new data folder, once."""
    if os.environ.get("MESHPOOL_HOME") or (h / "meshpool.config").exists() or (h / ".migrated").exists():
        return
    for old in _legacy_homes():
        try:
            if old.resolve() == h.resolve() or not (old / "meshpool.config").exists():
                continue
            for name in _HOME_ITEMS:
                src, dst = old / name, h / name
                if dst.exists() or not src.exists():
                    continue
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
            (h / ".migrated").write_text(str(old), encoding="utf-8")
            return
        except OSError:
            continue


def ensure_home() -> Path:
    """Create the data folder and copy the default settings and MCP list into it the first time."""
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_home(h)
    for name in ("meshpool.config", "mcp.json"):
        src, dst = APP / "defaults" / name, h / name
        if not dst.exists() and src.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    runs_dir().mkdir(parents=True, exist_ok=True)
    _upgrade_mcp(h / "mcp.json")
    try:
        from meshpool_harness.harnesses import ensure_defaults
        ensure_defaults()
    except Exception:
        pass
    return h


def _upgrade_mcp(path: Path) -> None:
    """Older versions listed browser and computer use as npx servers (Node.js needed, off by default). Swap in the
    built-in ones and drop the other Node-only entries the user never turned on."""
    import json
    try:
        new = json.loads((APP / "defaults" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    except (OSError, ValueError, KeyError):
        return

    def upgrade(servers: dict, cfg: dict) -> None:
        changed = False
        for name in ("browser", "computer-use"):
            cur = servers.get(name)
            if not isinstance(cur, dict) or cur.get("command") in ("npx", "npx.cmd"):
                servers[name] = new[name]
                changed = True
        for name in ("meshpool-web", "meshpool-assistant"):        # 2.4: shared web and assistant tools
            if name not in servers and name in new:
                servers[name] = new[name]
                changed = True
        if not cfg.get("_upgraded_24"):                          # 2.4: computer use is opt-in; research uses web tools
            cu = servers.get("computer-use")
            if isinstance(cu, dict) and not cu.get("disabled"):
                cu["disabled"] = True
            for name in ("browser", "computer-use"):
                if isinstance(servers.get(name), dict) and name in new:
                    servers[name]["description"] = new[name]["description"]
            cfg["_upgraded_24"] = True
            changed = True
        gh = servers.get("github")
        if isinstance(gh, dict) and gh.get("disabled") and gh.get("url") == "https://api.githubcopilot.com/mcp/":
            del servers["github"]                      # GitHub is a connector now (Settings, Connectors)
            changed = True
        for name in ("memory", "sequential-thinking", "filesystem", "fetch"):
            cur = servers.get(name)
            if isinstance(cur, dict) and cur.get("disabled") and cur.get("command") in ("npx", "uvx"):
                del servers[name]
                changed = True
        if changed:                                    # the default servers first, in their order
            order = {k: servers[k] for k in new if k in servers}
            order.update({k: v for k, v in servers.items() if k not in order})
            servers.clear()
            servers.update(order)

    from meshpool_harness.agent import mcp as M
    try:
        M.edit_config(upgrade, path=path, create=False, whole=True)
    except (OSError, ValueError):
        return


def config_path() -> Path:
    p = os.environ.get("MESHPOOL_CONFIG")
    return Path(p) if p else home() / "meshpool.config"


def decode_settings(raw: bytes) -> tuple[str, bool]:
    """Settings text from raw bytes, and whether it was UTF-16 (PowerShell's `>` writes that): a byte-order mark, or
    NUL bytes in every other position, means UTF-16. utf-8-sig drops a Notepad BOM; errors=replace keeps an ANSI "é"
    from breaking every save."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace"), True
    if len(raw) >= 4 and raw.count(b"\x00") * 3 >= len(raw):
        return raw.decode("utf-16-le" if raw[1] == 0 else "utf-16-be", errors="replace"), True
    return raw.decode("utf-8-sig", errors="replace"), False


def read_text(path: Path) -> str:
    """The settings file as text; a UTF-16 file is decoded and put back as UTF-8 (C-01)."""
    raw = path.read_bytes()
    text, wide = decode_settings(raw)
    text = text.replace("\ufeff", "")
    if wide:
        try:
            with _WRITE_LOCK, _file_lock(path.with_name(path.name + ".lock")):
                if path.read_bytes() == raw:
                    _store(path, text)
        except OSError:
            pass
    return text


def read(path: Path | None = None) -> dict:
    path = path or config_path()
    out = {}
    if not path.exists():
        return out
    return _parse(read_text(path))


def _parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


# One set of names for what an agent may do without asking: Ask, Accept edits, Full access. Stored as ask, edits,
# auto. Earlier spellings stay as aliases everywhere a name is read (F-19).
MODE_ALIASES = {"ask": "ask", "manual": "ask", "supervised": "ask", "default": "ask",
                "edits": "edits", "accept edits": "edits", "accept-edits": "edits", "accept_edits": "edits",
                "acceptedits": "edits", "auto-accept edits": "edits", "auto-accept-edits": "edits",
                "auto-accept": "edits", "edit": "edits",
                "auto": "auto", "full": "auto", "full access": "auto", "full-access": "auto", "full_access": "auto",
                "fullaccess": "auto", "bypass": "auto"}


def normalize_mode(v, default: str | None = "ask") -> str | None:
    """ask / edits / auto from any accepted spelling; `default` for anything else (plan is not an access level)."""
    return MODE_ALIASES.get(str(v or "").strip().lower(), default)


def permission_mode() -> str:
    """The access mode a NEW chat starts in (C-08): one setting, MH_AGENT_PERMISSIONS, read from the settings file at
    that moment so an edit made by hand or by another process reaches the next chat; `MH_PERMISSION_MODE` (what
    `meshpool --permission-mode` sets) is the same setting for one process and wins when it is set."""
    one_off = normalize_mode(os.environ.get("MH_PERMISSION_MODE"), None)
    if one_off:
        return one_off
    try:
        vals = read()
    except OSError:
        vals = {}
    for v in (vals.get("MH_AGENT_PERMISSIONS"), vals.get("MH_PERMISSION_MODE"), os.environ.get("MH_AGENT_PERMISSIONS")):
        m = normalize_mode(v, None)
        if m:
            return m
    return "ask"


def clean_key(k) -> str:
    """A key the way people paste it: in quotes, with a header name or "Bearer " in front, or with line breaks and
    zero-width characters in it. Nothing else is touched: dashes, dots, =, _ and any prefix (or none) stay."""
    k = str(k or "").strip()
    for _ in range(3):
        if len(k) >= 2 and (k[0], k[-1]) in {('"', '"'), ("'", "'"), ("`", "`"), ("\u201c", "\u201d"), ("\u2018", "\u2019")}:
            k = k[1:-1].strip()
    k = re.sub(r"^(?:(?:authorization|api-key|x-api-key|ocp-apim-subscription-key)\s*[:=]\s*)", "", k, flags=re.I)
    k = re.sub(r"^(?:bearer|token)\s+", "", k, flags=re.I)
    return re.sub(r"[\s\u200b-\u200f\u2060\ufeff]", "", k)


_WRITE_LOCK = __import__("threading").Lock()


def write(values: dict, path: Path | None = None) -> Path:
    """Update keys in place, keeping comments and unknown keys. One writer at a time, and the file is replaced in one
    step: two saves at once (two favourite stars clicked quickly) used to lose the API key."""
    path = path or config_path()
    for k in values:
        if k in ENDPOINT_NAMES:
            SHELL.pop(k, None)           # an explicit save replaces what the shell once gave for this name
    with _WRITE_LOCK, _file_lock(path.with_name(path.name + ".lock")):
        return _write(values, path)


def update(fn, path: Path | None = None) -> Path:
    """Read-modify-write under the same lock as write(): `fn(current_values)` returns the keys to set. Two favourites
    starred together, or a star and a save, can no longer undo each other (M16-03)."""
    path = path or config_path()
    with _WRITE_LOCK, _file_lock(path.with_name(path.name + ".lock")):
        cur = _parse(decode_settings(path.read_bytes())[0].replace("\ufeff", "")) if path.exists() else {}
        changes = fn(cur) or {}
        for k in changes:
            if k in ENDPOINT_NAMES:
                SHELL.pop(k, None)
        return _write(changes, path) if changes else path


@__import__("contextlib").contextmanager
def _file_lock(lock: Path):
    """An exclusive lock across processes for one read-modify-write (the app, `meshpool login`, MCP servers)."""
    import time
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock, "a+b")
    except OSError:
        yield                                             # no lock file possible: same as before
        return
    with fh:
        try:
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass


def _write(values: dict, path: Path) -> Path:
    lines = decode_settings(path.read_bytes())[0].replace("\ufeff", "").splitlines() if path.exists() else []
    done = set()
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in values:
                lines[i] = f"{k}={values[k]}"
                done.add(k)
    for k, v in values.items():
        if k not in done:
            lines.append(f"{k}={v}")
    _store(path, "\n".join(lines) + "\n")
    return path


def _store(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{__import__('threading').get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)                     # it holds API keys: readable by this user only
    except OSError:
        pass
    replace_file(tmp, path)


_SECRET_NAME = re.compile(r"(API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE_KEY|CONNECTION_STRING|DATABASE_URL)$", re.I)
_INJECTED: set = set()          # names Meshpool itself copied into the environment from its saved settings
# The endpoint names exactly as the user's own shell had them, kept apart from what load_into_env copies in from the file,
# so a key and the address it was set for can be told from a key and an address that came from different places.
ENDPOINT_NAMES = ("MESHPOOL_API_KEY", "MESHPOOL_BASE_URL", "MH_API_WIRE", "MH_AUTH_HEADER", "MH_AUTH_SCHEME",
                  "MESHPOOL_AZURE_API_VERSION", "OPENAI_API_KEY", "OPENAI_BASE_URL")
SHELL: dict = {k: os.environ[k].strip() for k in ENDPOINT_NAMES if os.environ.get(k, "").strip()}


def scrub_env(env: dict) -> dict:
    """The environment for a command the agent runs: without the keys Meshpool saved, so `env` or a script the model
    writes cannot read them and send them to the model provider or into a log. Keys the user set in their own shell stay."""
    for k in list(env):
        if k == "MESHPOOL_API_KEY" or (k in _INJECTED and (k in SECRET or _SECRET_NAME.search(k))):
            env.pop(k, None)
    return env


# Names that belong to the running process, not to the settings: a line in the file must never replace them (C-09).
PROCESS_OWNED = frozenset({
    "PATH", "PATHEXT", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR", "SHELL", "USER",
    "LOGNAME", "COMSPEC", "SYSTEMROOT", "WINDIR", "PYTHONPATH", "PYTHONHOME", "PYTHONIOENCODING", "VIRTUAL_ENV",
    "LD_LIBRARY_PATH", "LD_PRELOAD", "DYLD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "MESHPOOL_HOME", "MESHPOOL_CONFIG",
    "MH_RUNS_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"})


def load_into_env(override: bool = False) -> dict:
    """Copy config values into os.environ (shell values win unless override). An empty value clears what this
    function copied in before (so a cleared setting really is cleared (M16-01)."""
    vals = read()
    for k, v in vals.items():
        if k.upper() in PROCESS_OWNED:
            continue
        if v == "":
            if k in _INJECTED:
                os.environ.pop(k, None)
                _INJECTED.discard(k)
            continue
        if override and k in ENDPOINT_NAMES and os.environ.get(k) and k not in _INJECTED:
            SHELL[k] = os.environ[k].strip()           # the shell's own value, before the file's replaces it
        if override or k not in os.environ:
            os.environ[k] = v
            _INJECTED.add(k)
    from meshpool_harness import bundled
    bundled.use_bundled_tools()               # packaged app: its own Node and Git come first on PATH (idempotent)
    try:
        from meshpool_harness.core import http as _h
        _h.install_tls()                      # CA trust fallback (python.org macOS, a company proxy's CA)
    except Exception:  # noqa: BLE001 - never block start-up
        pass
    return vals


# ---------- local tools -------------------------------------------------------------------------------------------

def find_soffice() -> str | None:
    """LibreOffice for Office conversions. One resolver (office_runtime.py): MH_SOFFICE, the copy bundled in the app;
    never the customer's install in the packaged app."""
    from meshpool_harness import office_runtime
    return office_runtime.find_soffice()


def find_bash() -> str | None:
    """A real bash for the agent's shell commands. On Windows: Git Bash, never WSL's System32 bash."""
    if os.environ.get("MH_BASH"):
        return os.environ["MH_BASH"]
    from meshpool_harness import bundled
    if os.name == "nt" and bundled.bundled_bash():
        return bundled.bundled_bash()                  # the Git that ships with Meshpool, never the customer's
    if bundled.packaged() and os.name == "nt":
        return None
    if os.name != "nt":
        return shutil.which("bash")                    # macOS: /bin/bash is part of the OS
    cands = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), r"C:\Program Files",
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            cands.append(Path(base) / "Git" / "bin" / "bash.exe")
    for c in cands:
        if c.exists():
            return str(c)
    hit = shutil.which("bash")
    if hit and "system32" not in hit.lower():
        return hit
    return None


def system_check() -> list[dict]:
    """What is installed, and what each missing piece limits."""
    rows = [{"name": "Python", "ok": sys.version_info >= (3, 10), "detail": sys.version.split()[0],
             "fix": "Install Python 3.10 or newer."}]
    rows.append({"name": "Shell", "ok": bool(find_bash()), "detail": find_bash() or "not found",
                 "fix": "Agents run shell commands. The Windows app includes one; for the terminal-only harness on "
                        "Windows, install Git for Windows (git-scm.com)."})
    from meshpool_harness import bundled
    git = bundled.bundled_git() if bundled.packaged() else shutil.which("git")
    rows.append({"name": "Git", "optional": True, "ok": bool(git),
                 "detail": (f"{git} (ships with Meshpool)" if bundled.packaged() else git) if git else "not found",
                 "fix": ("Missing from this install; reinstall Meshpool." if bundled.packaged() else
                         "Optional. Used to spot edited tests and for pull requests. On a Mac, run: xcode-select --install")})
    node = bundled.node_exe()
    rows.append({"name": "Node.js", "optional": True, "ok": bool(node),
                 "detail": (f"{node} (ships with Meshpool)" if bundled.packaged() else node) if node else "not found",
                 "fix": ("Missing from this install; reinstall Meshpool." if bundled.packaged() else
                         "Optional. Pi runs on Node.js (the app includes it); from the command "
                         "line, install Node.js 22 or newer.")})
    try:
        import playwright  # noqa: F401
        pw = True
    except ImportError:
        pw = False
    from meshpool_harness import bundled
    why = bundled.browser_needs_newer_macos()
    if why:                                 # the app opens on macOS 11, its Chromium needs 13
        rows.append({"name": "Browser use", "optional": True, "ok": False, "detail": "needs macOS 13 or later",
                     "fix": why})
    else:
        from meshpool_harness import browser_use as BU
        have = bundled.packaged() or bool(BU.find_playwright_chromium())
        rows.append({"name": "Browser use", "ok": True,
                     "detail": ("ready" if pw and have else "sets itself up the first time it is used"),
                     "fix": "To do it now, run: meshpool install-browser"})
    try:
        from meshpool_harness.agent import computer_mcp_server as CU
        st = CU.computer_status()
        rows.append({"name": "Computer use", "optional": True, "ok": st["ready"],
                     "detail": "ready" if st["ready"] else st["summary"],
                     "fix": " ".join(f"{i['name']}: {i['fix']}" for i in st["items"] if i["fix"])})
    except Exception as e:  # noqa: BLE001  a failing probe must never break the settings page
        rows.append({"name": "Computer use", "optional": True, "ok": False, "detail": f"check failed ({e})", "fix": ""})
    from meshpool_harness import office_runtime
    lo = office_runtime.describe()
    rows.append({"name": "LibreOffice", "optional": True, "ok": bool(lo["path"]),
                 "detail": f"{lo['path']} ({lo['note']})" if lo["path"] else lo["note"],
                 "fix": "Optional. Meshpool uses only its own LibreOffice (the copy inside the app), never one you "
                        "installed. With it, Meshpool checks spreadsheet formula results, "
                        "renders slides to review them and converts Office files. Files are complete without it."})
    rows.extend(_bundled_program_rows(bundled))
    rows.append({"name": "Slide icons", "optional": True, "ok": True, "detail": "built in",
                 "fix": "Not needed: icons ship pre-rendered with Meshpool."})
    try:                                    # the model's context window and where that number came from
        from meshpool_harness.core import context as _ctx
        rows.append({"name": "Context window", "optional": True, "ok": True, "detail": _ctx.describe(),
                     "fix": "Set MESHPOOL_CONTEXT_WINDOW (tokens) if this is wrong for your model."})
    except Exception:  # noqa: BLE001 - a check must report, not crash
        pass
    return rows


def _bundled_program_rows(bundled) -> list[dict]:
    """ffmpeg, pandoc, speech to text and notebooks, each with the version that is really there. In the app they are
    Meshpool's own copies (vendor/); a source or terminal install may use the ones on PATH. A probe that fails shows as a
    row that says so; it never breaks the settings page."""
    rows = []
    for name, label, uses in (("ffmpeg", "ffmpeg", "converts audio and video"),
                              ("pandoc", "pandoc", "converts documents between Markdown, HTML, Word, EPUB and more")):
        try:
            mine = bundled.bundled_program(name)
            exe = mine if (mine or bundled.packaged()) else shutil.which(name)
            why = bundled.ffmpeg_needs_newer_macos() if name == "ffmpeg" and exe else ""
            ver = bundled.program_version(exe, "-version" if name == "ffmpeg" else "--version") if exe and not why else ""
            if why:
                rows.append({"name": label, "optional": True, "ok": False, "detail": "needs macOS 12 or later", "fix": why})
            elif exe:
                tail = " (ships with Meshpool)" if mine else ""
                rows.append({"name": label, "optional": True, "ok": bool(ver),
                             "detail": f"{ver or 'found but did not start'} at {exe}{tail}",
                             "fix": "Missing from this install; reinstall Meshpool." if not ver and bundled.packaged() else
                                    f"Optional. {label} {uses}."})
            else:
                rows.append({"name": label, "optional": True, "ok": False, "detail": "not found",
                             "fix": ("Missing from this install; reinstall Meshpool." if bundled.packaged() else
                                     f"Optional. {label} {uses}. The desktop app includes it; for a terminal install, "
                                     "install it with your package manager.")})
        except Exception as e:  # noqa: BLE001
            rows.append({"name": label, "optional": True, "ok": False, "detail": f"check failed ({e})", "fix": ""})
    try:
        sp = bundled.speech_status()
        rows.append({"name": "Speech to text", "optional": True, "ok": sp["ok"], "detail": sp["detail"], "fix": sp["fix"]})
    except Exception as e:  # noqa: BLE001
        rows.append({"name": "Speech to text", "optional": True, "ok": False, "detail": f"check failed ({e})", "fix": ""})
    try:
        from importlib import metadata
        have = {}
        for pkg in ("nbformat", "nbconvert", "ipykernel"):
            try:
                have[pkg] = metadata.version(pkg)
            except metadata.PackageNotFoundError:
                pass
        ok = len(have) == 3
        rows.append({"name": "Notebooks", "optional": True, "ok": ok,
                     "detail": ", ".join(f"{k} {v}" for k, v in have.items()) if have else "not installed",
                     "fix": "" if ok else ("Missing from this install; reinstall Meshpool." if bundled.packaged() else
                                           "The desktop app includes them. In a terminal install: pip install nbformat "
                                           "nbconvert ipykernel")})
    except Exception as e:  # noqa: BLE001
        rows.append({"name": "Notebooks", "optional": True, "ok": False, "detail": f"check failed ({e})", "fix": ""})
    return rows


def packaged() -> bool:
    """True inside the installed app (Meshpool.app or the Windows install), where Python is Meshpool's own. A
    command-line install (its own venv, or the launcher the installers write) is not the app, even when it
    downloaded its own Python."""
    if (APP.parent / "venv").is_dir() or (APP / "meshpool_launch.py").exists():
        return False
    return (APP.parent / "python").is_dir() or (APP.parent / "runtime").is_dir()


def user_env(base: dict | None = None) -> dict:
    """Environment for the user's own commands (the agent's shell, the terminal panel). Inside the app, drop the
    variables that point Python at Meshpool's bundled runtime, so the user's python3, pip and tools behave as in
    their own terminal."""
    env = scrub_env(dict(os.environ if base is None else base))
    if packaged():
        for k in ("PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE",
                  "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
            env.pop(k, None)
    return env

