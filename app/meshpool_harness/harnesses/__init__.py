"""Harness registry: the built-in agents plus every ACP agent described by a .json file in <home>/harnesses."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
from pathlib import Path

from meshpool_harness import config as C

BUILTIN = [
    {"id": "meshpool-code", "name": "Meshpool Code", "mode": "code", "type": "builtin", "default": True,
     "models": "meshpool", "tagline": "Coding in your project: more fixes solved, for less, than Codex CLI",
     "description": ("Best for coding in your project. On SWE-bench Verified it solved 18 of 20 tasks at $0.008 "
                     "each, more and cheaper than Codex CLI on the same model. Edits code, runs commands and tests, "
                     "and uses a browser, your connected apps and skills.")},
    {"id": "meshpool-work", "name": "Meshpool Work", "mode": "cowork", "type": "builtin", "models": "meshpool",
     "tagline": "Word, Excel, PowerPoint and PDF, checked before you get them",
     "description": ("Best for documents, spreadsheets, decks, data and research. Passed all 7 tasks in our office "
                     "run, from a live-formula workbook to editing a contract in place. Every deck and workbook is "
                     "checked against Meshpool's design and formula rules before it reaches you.")},
]
PI = {"id": "pi", "name": "Pi", "mode": "code", "type": "pi", "models": "meshpool",
      "tagline": "Quick, low-cost code fixes",
      "description": ("Best for quick, low-cost coding. A small open-source coding agent: 19 of 20 SWE-bench "
                      "Verified tasks at $0.007 each in our run.")}
AUTO = {"id": "meshpool-auto", "name": "Meshpool Auto", "mode": "any", "type": "auto", "models": "meshpool",
        "tagline": "Picks the best agent per task: quality first, then cost and speed",
        "description": ("Best when you are not sure. For each task it takes the agent with the best benchmark "
                        "quality for that kind of work, then the cheapest and fastest of the equally good ones, "
                        "and tells you which one and why.")}
# The official Codex agent, over the Agent Client Protocol. It is a harness (which agent runs); the provider of the
# thread's model decides the credentials it runs on (see credentials() below).
CODEX_ACP = "@agentclientprotocol/codex-acp@2.6.0"
NODE_NOTE = "It needs Node.js: install it first (the Meshpool app does not include this agent)."
# Codex: the same official agent on the Meshpool key, another API key (its own config in the Meshpool data folder
# points it at that endpoint) or the user's ChatGPT sign-in (Codex's own login in ~/.codex).
CODEX = {"id": "codex", "name": "Codex", "mode": "code", "type": "acp", "models": "meshpool", "login": "codex",
         # Codex's read-only mode refuses to edit rather than asking; in Workspace access it edits the project and
         # asks before anything outside it. Meshpool's Undo covers every change.
         "modes": {"ask": "workspace-write", "edits": "workspace-write", "auto": "agent-full-access"},
         "command": "npx", "args": ["-y", CODEX_ACP], "share_mcp": True, "lean": True, "install": NODE_NOTE,
         "tagline": "Terminal and command-line work, on GPT models",
         "description": ("Best for terminal and command-line work. OpenAI's open-source Codex agent, on your Meshpool "
                         "key, another API key or your ChatGPT sign-in. It edits files in your project without "
                         "asking and asks before anything outside it; Undo reverses its changes.")}
# Thread files and settings saved by older versions name these ids: agents Meshpool no longer has open in
# Meshpool Code, so old chats still open.
LEGACY = {"chatgpt-subscription": "codex", "claude-subscription": "meshpool-code", "claude-code": "meshpool-code",
          "gemini-cli": "meshpool-code"}
MODES = ("code", "cowork")
# OpenCode: the open-source coding agent, over ACP (`opencode acp`). It runs on the same providers as Meshpool's own
# agents: its config in the Meshpool data folder points it at the key endpoint or at the local ChatGPT relay.
OPENCODE_PKG = "opencode-ai@1.19.5"
CODEX_CLI = "@openai/codex@0.171.0"         # only its `login` is used; pinned like the others (PA-core-05),
                                             # the version every benchmark in docs/results ran
OPENCODE = {"id": "opencode", "name": "OpenCode", "mode": "code", "type": "acp", "models": "meshpool",
            "command": "npx", "args": ["-y", OPENCODE_PKG, "acp"], "share_mcp": True, "install": NODE_NOTE,
            "tagline": "The open-source coding agent, with its own tools and plans",
            "description": ("OpenCode, the popular open-source coding agent, on your Meshpool key, another API key or "
                            "your ChatGPT sign-in. Uses your connected apps and skills.")}
# Claude Code, over ACP through the Claude agent adapter. It runs under the user's own Claude Code sign-in on this
# computer (`claude`); Meshpool never reads that sign-in, only starts the agent, and the agent talks to Anthropic as
# the user's own Claude Code. Listed only when the `claude` command is installed.
CLAUDE_ACP = "@agentclientprotocol/claude-agent-acp@0.109.0"
CLAUDE_AGENT = {"id": "claude-agent", "name": "Claude Code", "mode": "code", "type": "acp", "models": "harness",
                "login": "claude", "command": "npx", "args": ["-y", CLAUDE_ACP], "share_mcp": True,
                "install": NODE_NOTE,
                "tagline": "Anthropic's coding agent, on your own Claude Code sign-in",
                "description": ("Anthropic's Claude Code agent, started the way you run it in a terminal: it uses "
                                "the Claude Code sign-in already on this computer, never an API key from Meshpool.")}
RESERVED = {"meshpool-code", "meshpool-work", "hermes", "pi", "meshpool-auto", "codex", "opencode", "claude-agent",
            *LEGACY}
ID_RE = r"[A-Za-z0-9_.-]{1,60}"
HERMES_NOTE = ("Best for ongoing work where memory matters: Hermes remembers what it learns across chats, uses "
               "the web, files and terminal, and makes Office files.")


def vendored_hermes() -> Path | None:
    p = C.APP / "vendor" / "hermes"
    return p if (p / "acp_adapter").is_dir() else None


def hermes_home() -> Path:
    return C.home() / "hermes"


def _bundled_hermes() -> dict | None:
    v = vendored_hermes()
    if not v:
        return None
    env = {"PYTHONPATH": str(v), "HERMES_HOME": str(hermes_home()), "HERMES_BUNDLED_SKILLS": str(v / "skills"),
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "HERMES_DISABLE_LAZY_INSTALLS": "1",
           "HERMES_ASK_EVERY_COMMAND": "1",   # every command asks Meshpool, which applies the user's permission mode
           "HERMES_ACP_APPROVAL_TIMEOUT": "86400"}   # an approval card waits for the user, as in every harness
    if os.environ.get("PYTHONPYCACHEPREFIX"):
        env["PYTHONPYCACHEPREFIX"] = os.environ["PYTHONPYCACHEPREFIX"]
    else:
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Not the cowork default (office handoff, 13 Nov 2026): Meshpool Work passed every office task tested, was faster
    # on the Word proposal (42 s vs 64 s) and runs the forge deck/workbook checks; Hermes stays one click away.
    return {"id": "hermes", "name": "Hermes", "mode": "cowork", "type": "acp", "prepare": "hermes",
            "models": "meshpool", "command": sys.executable, "args": ["-m", "acp_adapter"], "env": env,
            "tagline": "Ongoing work: remembers what it learns across chats", "description": HERMES_NOTE}


def codex_home() -> Path:
    """Codex's config when it runs on an API key: in the Meshpool data folder, apart from the user's own Codex."""
    return C.home() / "codex"


def user_codex_home() -> Path:
    """The user's own Codex folder, where `codex login` keeps the ChatGPT sign-in (auth.json)."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _chat_dir(base: Path, chat: str) -> Path:
    import re
    return base / "chats" / re.sub(r"[^\w.-]", "_", chat) if chat else base


def prune_codex_homes(max_age_days: float = 30, now: float | None = None) -> list[str]:
    """OSS-36: per-chat CODEX_HOME folders pile up (one per chat, forever). Remove those of chats that no longer exist
    and those not touched for max_age_days. Returns the names removed. Called at start-up."""
    import re
    import shutil
    import time
    base = codex_home() / "chats"
    if not base.is_dir():
        return []
    sess = C.runs_dir() / "sessions"
    live = {re.sub(r"[^\w.-]", "_", d.name) for d in sess.iterdir()} if sess.is_dir() else None
    cutoff = (now or time.time()) - max_age_days * 86400
    gone = []
    for d in base.iterdir():
        try:
            if d.is_dir() and ((live is not None and d.name not in live) or d.stat().st_mtime < cutoff):
                shutil.rmtree(d, ignore_errors=True)
                gone.append(d.name)
        except OSError:
            pass
    return gone


_SANDBOX_FAIL = (
    "bwrap:", "No permissions to create new namespace", "creating new namespace", "unshare: ", "Can't mount proc",
    "Failed RTM_NEWADDR", "sandbox setup", "sandbox failed", "failed to set up the sandbox", "CreateRestrictedToken",
    "windows sandbox", "setup_refresh", "ACL", "landlock", "seccomp", "Sandbox(", "sandbox denied")


def codex_sandbox_failure(text: str, platform: str | None = None) -> str | None:
    """OSS-02: Codex's own sandbox could not start (Windows ACLs, bubblewrap inside a container or another bwrap, a
    kernel without user namespaces). Returns the plain-language note for the user, or None when the text is something else."""
    t = str(text or "")
    low = t.lower()
    if not any(k.lower() in low for k in _SANDBOX_FAIL) or ("sandbox" not in low and "bwrap" not in low and "namespace" not in low and "acl" not in low):
        return None
    win = (platform or sys.platform).startswith("win")
    return ("Codex could not start its own sandbox on this computer" + (" (Windows file permissions block it)" if win else
            " (it needs bubblewrap and user namespaces, which are not available here)") + ". Its commands did not run. "
            + ("Switch this chat to Full access to run Codex without its sandbox, or use the Meshpool agent: the command sandbox "
               "is not available on Windows either, so approvals and Undo are the safety net." if win else
               "Switch this chat to Full access to run Codex without its sandbox (Meshpool's Undo still covers the edits), "
               "or use the Meshpool agent, which runs commands in its own sandbox."))


def prepare_codex(model: str | None = None, chat: str = "") -> dict:
    """Point Codex at the API key endpoint and model: its own config.toml, in a folder of the chat's own (its
    CODEX_HOME), so chats starting at once never run on each other's model. Codex reads the key from MESHPOOL_API_KEY
    in its own environment (set by _launch), never from the file. Returns the environment that points Codex there."""
    from meshpool_harness import providers as PV
    home = _chat_dir(codex_home(), chat)
    home.mkdir(parents=True, exist_ok=True)
    base = PV.key_base()
    prov, bare = PV.split(model or os.environ.get("MH_MODEL") or "")
    model = bare if prov == "key" and bare else "deepseek-v4.1-flash"
    q = lambda v: '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
    from meshpool_harness import fsio
    extra = ""
    if PV.key_kind() == "azure-classic":       # Codex's documented Azure provider: base .../openai and an api-version
        from meshpool_harness.core import endpoint as EP
        base = base.rstrip("/") + "/openai"
        extra = f"query_params = {{ api-version = {q(PV.key_api_version() or EP.AZURE_DEFAULT_VERSION)} }}\n"
    fsio.atomic_write_text(home / "config.toml",
        f"model = {q(model)}\nmodel_provider = \"meshpool\"\nmodel_context_window = 128000\n"
        "\n"
        f"[model_providers.meshpool]\nname = \"Meshpool\"\nbase_url = {q(base)}\nenv_key = \"MESHPOOL_API_KEY\"\n"
        f"{extra}wire_api = \"responses\"\nrequest_max_retries = 6\nstream_max_retries = 6\n", fsync=False)
    return {"CODEX_HOME": str(home)}


def write_private(p: Path, text: str) -> None:
    """Write a file only the user can read (it holds a key or tokens): written to a 0600 temp file and moved into
    place, so it is never readable by others and a reader never sees it half written."""
    from meshpool_harness import fsio
    fsio.atomic_write_text(p, text, fsync=False, private=True)       # 0600 from the first byte
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def opencode_home() -> Path:
    return C.home() / "opencode"


def prepare_opencode(model: str | None = None, chat: str = "") -> dict:
    """Write OpenCode's config for the thread's provider and model (a file of the chat's own, so chats starting at
    once never read each other's); returns the extra environment it needs. The key stays in the environment
    ({env:...} in the config), never in the file."""
    from meshpool_harness import providers as PV
    prov, bare = PV.split(model or os.environ.get("MH_MODEL") or "")
    env = {}
    if prov == "chatgpt":
        from meshpool_harness.core import chatgpt_relay
        env = chatgpt_relay.env()               # the relay holds the sign-in; OpenCode only sees its address and token
        base, key_env = env["MESHPOOL_CHATGPT_RELAY_URL"], "MESHPOOL_CHATGPT_RELAY_KEY"
    else:
        base, key_env, bare = PV.agent_endpoint("key")[0], "MESHPOOL_API_KEY", bare or PV.DEFAULT_MODEL
    home = opencode_home()
    home.mkdir(parents=True, exist_ok=True)
    # "permission": OpenCode allows everything by default, so it never asked and Supervised and Plan meant nothing.
    # Asking hands every edit, command and fetch to Meshpool, which applies the user's mode (and blocks in Plan).
    cfg = {"$schema": "https://opencode.ai/config.json", "autoupdate": False, "share": "disabled",
           "permission": {"edit": "ask", "bash": "ask", "webfetch": "ask"},
           "provider": {"meshpool": {"npm": "@ai-sdk/openai-compatible", "name": "Meshpool",
                                     "options": {"baseURL": base, "apiKey": "{env:" + key_env + "}"},
                                     "models": {bare: {"name": bare}}}},
           "model": f"meshpool/{bare}"}
    path = (_chat_dir(home, chat) / "opencode.json") if chat else home / "opencode.json"
    from meshpool_harness import fsio
    fsio.atomic_write_text(path, json.dumps(cfg, indent=2), fsync=False)
    return {**env, "OPENCODE_CONFIG": str(path)}


HERMES_ENV = {"base_url": "MESHPOOL_HERMES_BASE_URL", "default": "MESHPOOL_HERMES_MODEL",
              "api_key": "MESHPOOL_HERMES_API_KEY", "api_mode": "MESHPOOL_HERMES_API_MODE"}


def prepare_hermes(model: str | None = None) -> dict:
    """Point the bundled Hermes at the thread's provider (the key endpoint or the ChatGPT relay), model and key.
    Every chat runs its own Hermes on one HERMES_HOME (its memory is shared across chats), and Hermes re-reads
    config.yaml whenever the file changes. So the file names no provider: it holds ${VAR} references that each Hermes
    resolves from its own environment, which this returns. The file is the same for every chat and is rewritten
    (atomically) only when it changes."""
    from meshpool_harness import providers as PV
    import yaml
    home = hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    p = home / "config.yaml"
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    m = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    prov = PV.for_harness("hermes", model or os.environ.get("MH_MODEL")) or "key"
    base, key = PV.agent_endpoint(prov)         # the key endpoint, or the local relay when it needs more than Bearer
    p_, bare = PV.split(model or os.environ.get("MH_MODEL") or "")
    env = {HERMES_ENV["base_url"]: base, HERMES_ENV["default"]: bare if p_ == prov and bare else "deepseek-v4.1-flash",
           HERMES_ENV["api_key"]: key or "",
           # an Anthropic Messages endpoint (Hermes strips the /v1 itself), else chat completions
           HERMES_ENV["api_mode"]: "anthropic_messages" if prov == "key" and PV.key_wire() == "anthropic"
           else "chat_completions"}
    m.update({"provider": "custom", **{k: "${" + v + "}" for k, v in HERMES_ENV.items()}})
    m.pop("key_env", None)
    cfg["model"] = m
    tools = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
    ts = tools.get("tool_search") if isinstance(tools.get("tool_search"), dict) else {}
    ts["enabled"] = "off"                       # show Meshpool's office tools directly, not behind a search
    tools["tool_search"] = ts
    cfg["tools"] = tools
    sec = cfg.get("security") if isinstance(cfg.get("security"), dict) else {}
    sec["allow_lazy_installs"] = False          # no pip downloads at runtime; never write into the signed app
    cfg["security"] = sec
    au = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {}
    au["adopt_external_logins"] = False         # never borrow another app's sign-in: Hermes runs on the key above
    cfg["auth"] = au
    ap = cfg.get("approvals") if isinstance(cfg.get("approvals"), dict) else {}
    ap["mode"] = "manual"                       # every command comes to Meshpool, which applies the user's setting;
    cfg["approvals"] = ap                       # Hermes's own "smart" mode auto-approved deletes
    db = cfg.get("database") if isinstance(cfg.get("database"), dict) else {}
    # Each chat runs its own Hermes process on the same state.db. In WAL mode one process closing retired the
    # write-ahead log the others still held, and Hermes then refused to open the database for every chat.
    db["journal_mode"] = "delete"
    cfg["database"] = db
    from meshpool_harness import fsio
    try:
        from meshpool_harness import skills as SK
        with fsio.file_lock(home / "skills"):              # two launches must not copy over each other
            SK.sync_to(home / "skills" / "meshpool")       # the user's Meshpool skills, for Hermes too
    except Exception:
        pass
    text = yaml.safe_dump(cfg, sort_keys=False)
    with fsio.file_lock(p):
        try:
            same = p.read_text(encoding="utf-8") == text
        except OSError:
            same = False
        if not same:                                       # an unchanged file keeps its mtime: no re-read
            write_private(p, text)
    # Hermes keeps a "last known good" copy of config.yaml and skips the copy when the newest one matches.
    # Writing it here keeps Hermes off shutil.copy2 at startup (CopyFile2 is missing on some Windows
    # compatibility layers) and keeps the backup folder from filling with one copy per launch.
    try:
        b = home / "backups" / "config"
        b.mkdir(parents=True, exist_ok=True)
        olds = sorted((f for f in b.iterdir() if f.name.startswith("config.yaml.good.")), reverse=True)
        if not olds or olds[0].read_text(encoding="utf-8", errors="replace") != text:
            import time
            dst = b / f"config.yaml.good.{time.strftime('%Y%m%d-%H%M%S')}"
            write_private(dst, text)
            for stale in [dst, *olds][5:]:
                stale.unlink(missing_ok=True)
    except OSError:
        pass
    return env


def hermes_model(env: dict) -> dict:
    """The model section of Hermes's config.yaml as a Hermes started with env (prepare_hermes's result) reads it."""
    import re
    import yaml
    m = (yaml.safe_load((hermes_home() / "config.yaml").read_text(encoding="utf-8")) or {}).get("model") or {}
    return {k: re.sub(r"\$\{([^}]+)\}", lambda g: env.get(g.group(1), g.group(0)), v) if isinstance(v, str) else v
            for k, v in m.items()}


def folder() -> Path:
    return C.home() / "harnesses"


def ensure_defaults() -> None:
    """First run: copy the shipped manifests (Hermes) and the examples into the user's harness folder."""
    src = C.APP / "defaults" / "harnesses"
    dst = folder()
    if dst.exists() or not src.exists():
        return
    tmp = dst.with_name(dst.name + ".part")         # copy then rename, so a failed first run never leaves half a folder
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, copy_function=shutil.copyfile)
    tmp.rename(dst)


def _validate(d: dict) -> dict:
    if not isinstance(d, dict):
        raise ValueError("A harness file must be a JSON object.")
    for k in ("id", "name", "command"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"The harness file needs \"{k}\" (text).")
    if not re.fullmatch(ID_RE, d["id"]):
        raise ValueError("\"id\" may only use letters, numbers, dots, dashes and underscores.")
    if d["id"] in RESERVED:
        raise ValueError(f"\"{d['id']}\" is a built-in harness id. Pick another.")
    d.setdefault("type", "acp")
    if d["type"] != "acp":
        raise ValueError("Only ACP harnesses (\"type\": \"acp\") can be added.")
    d.setdefault("mode", "cowork")
    if d["mode"] not in MODES:
        raise ValueError("\"mode\" must be \"code\" or \"cowork\".")
    if not isinstance(d.get("args", []), list):
        raise ValueError("\"args\" must be a list.")
    return d


def _available(d: dict) -> bool:
    cmd = d.get("command", "")
    return bool(shutil.which(cmd) or (cmd and Path(cmd).exists()))


# Codex and OpenCode are the benchmark's agents (testkit/loop/bench.py runs them with npx), not part of the product:
# the installers do not ship them, so a packaged app never offers them (no npx, no copy from the customer's machine).
NOT_SHIPPED = ("codex", "opencode", "claude-agent")   # claude-agent: its ACP adapter is not bundled yet (0.0.8)


def unsupported(hid: str) -> str:
    """Why a built-in agent cannot run in this copy of Meshpool, or "". Codex and OpenCode are not in the installed
    app; a source checkout and the benchmark still run them with npx."""
    from meshpool_harness import bundled
    if hid in NOT_SHIPPED and bundled.packaged():
        name = next((h["name"] for h in (CODEX, OPENCODE, CLAUDE_AGENT) if h["id"] == hid), hid)
        return f"{name} is not part of the Meshpool app."
    return ""


def _npx_agent(h: dict, available: bool | None = None, **extra) -> dict:
    """A built-in agent that runs with npx (Codex, OpenCode). In the packaged app it is listed as unavailable and
    hidden: it is not shipped, and there is no install to point the customer at."""
    if available is None:
        available = _available(h)
    why = unsupported(h["id"])
    row = dict(h, available=available and not why, builtin=True, **extra, **credentials(h["id"]))
    if why:
        row.update(unsupported=why, install="", hidden=True)
    return row


def load_all() -> list[dict]:
    from meshpool_harness.harnesses import pi_rpc
    out = [dict(BUILTIN[0], available=True, builtin=True, **credentials("meshpool-code"))]
    if pi_rpc.available():
        out.append(dict(PI, available=True, builtin=True, **credentials("pi")))
    bh = _bundled_hermes()
    if bh:
        out.append(dict(bh, available=True, builtin=True, **credentials("hermes")))
    out += [dict(b, available=True, builtin=True, **credentials(b["id"])) for b in BUILTIN[1:]]
    out.append(_npx_agent(CODEX, signed_in=signed_in(CODEX["login"])))
    out.append(_npx_agent(OPENCODE))
    if shutil.which("claude"):                       # Claude Code is installed (and signed in by the user themselves)
        out.append(_npx_agent(CLAUDE_AGENT))
    out.insert(0, dict(AUTO, available=True, builtin=True, **credentials("meshpool-auto")))
    if folder().exists():
        for p in sorted(folder().glob("*.json")):
            if p.stem in RESERVED:
                continue                   # an agent added from an example before it became built in, or one
                                           # Meshpool no longer has
            try:
                d = _validate(json.loads(p.read_text(encoding="utf-8")))
                out.append(dict(d, available=_available(d), builtin=False, file=str(p)))
            except (ValueError, TypeError, OSError) as e:
                out.append({"id": p.stem, "name": p.stem, "mode": "cowork", "type": "acp", "available": False,
                            "builtin": False, "error": str(e), "file": str(p)})
    return out


def examples() -> list[dict]:
    rows = []
    ex = folder() / "examples"
    if ex.exists():
        for p in sorted(ex.glob("*.json")):
            try:
                d = _validate(json.loads(p.read_text(encoding="utf-8")))
                if not (folder() / p.name).exists():
                    rows.append(dict(d, available=_available(d), file=str(p)))
            except (ValueError, TypeError, OSError):
                continue
    return rows


def signed_in(which: str) -> bool:
    """Whether the Codex (ChatGPT) sign-in already exists on this computer (it never reads the secret)."""
    try:
        if which == "codex":
            from meshpool_harness.core import chatgpt as CG
            return bool(CG.signin_state()["ok"])      # a rejected or API-key sign-in is not a ready one
    except Exception:  # noqa: BLE001
        return False
    return False


def npx() -> str:
    return shutil.which("npx") or "npx"       # source checkouts only: the packaged app has no Codex


LOGIN_TIMEOUT_S = 600                     # an abandoned ChatGPT sign-in is stopped after this long
_LOGIN: dict = {"proc": None}             # the `npx codex login` this process started
_LOGIN_LOCK = threading.Lock()
_LOGIN_ATEXIT = [False]


def _login_file() -> Path:
    return C.home() / "codex-login.json"


def _pid_alive(pid: int) -> bool:
    """Whether a process with this pid is running (a zombie is not)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import subprocess
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                                 timeout=10, creationflags=0x08000000).stdout
        except Exception:  # noqa: BLE001
            return False
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rsplit(b")", 1)[1].split()[0] != b"Z"
    except (OSError, IndexError):
        return True


def _kill_tree(proc) -> None:
    """Stop a sign-in and everything it started (npx -> node -> codex)."""
    import subprocess
    pid = proc.pid
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=0x08000000)
    else:
        import signal
        import time
        try:
            os.killpg(pid, signal.SIGTERM)       # it is its own group leader (start_new_session)
        except (ProcessLookupError, PermissionError):
            pass
        end = time.monotonic() + 2
        while time.monotonic() < end:
            try:
                proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                continue
            try:
                os.killpg(pid, 0)                # leader gone: wait for the rest of the group too
            except (ProcessLookupError, PermissionError):
                break
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _clear_login(proc) -> None:
    """Forget this sign-in: the state and its pid file (only if the file is still ours)."""
    from meshpool_harness import fsio
    with _LOGIN_LOCK:
        if _LOGIN["proc"] is proc:
            _LOGIN["proc"] = None
    try:
        with fsio.file_lock(_login_file(), timeout=5):
            rec = fsio.read_json(_login_file(), None)
            if isinstance(rec, dict) and rec.get("pid") == proc.pid:
                _login_file().unlink(missing_ok=True)
    except (TimeoutError, OSError, ValueError, fsio.CorruptFile):
        pass


def _login_watchdog(proc) -> None:
    """Wait for the sign-in; stop it after LOGIN_TIMEOUT_S; either way clear the state when it is over."""
    import subprocess
    try:
        proc.wait(timeout=LOGIN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
    except Exception:  # noqa: BLE001
        pass
    _clear_login(proc)


def stop_login() -> None:
    """Stop the ChatGPT sign-in this app started, with its whole process tree. Called when the app quits."""
    with _LOGIN_LOCK:
        proc, _LOGIN["proc"] = _LOGIN["proc"], None
    if proc is None:
        return
    if proc.poll() is None:
        _kill_tree(proc)
    _clear_login(proc)


def _cmdline(pid: int) -> str:
    """The command line of a running process, or "" (Linux and macOS; Windows is not asked)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        pass
    if os.name == "nt" or not shutil.which("ps"):
        return ""
    import subprocess
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout
    except Exception:  # noqa: BLE001
        return ""


def reap_stale_login() -> bool:
    """Stop a ChatGPT sign-in left by a meshpool that is gone (killed, or crashed before it could stop it): it held the
    sign-in port and ran for good (seen 16 minutes after the user had picked another option). Only a process whose
    command line is still Codex's login, and only when the app that started it is no longer running. True when one
    was stopped. One small file read when there is nothing to do."""
    from meshpool_harness import fsio
    import signal
    try:
        rec = fsio.read_json(_login_file(), None)
    except (fsio.CorruptFile, OSError, ValueError):
        return False
    if not isinstance(rec, dict):
        return False
    pid, owner = rec.get("pid"), rec.get("owner")
    if owner == os.getpid() or (isinstance(owner, int) and owner > 0 and _pid_alive(owner)):
        return False
    if not _pid_alive(pid):
        _login_file().unlink(missing_ok=True)
        return False
    cl = _cmdline(pid)
    if "codex" not in cl or "login" not in cl:
        return False                                  # the pid now belongs to something else: leave it alone
    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        try:
            os.killpg(pid, sig) if os.name != "nt" else os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            break
        import time
        end = time.monotonic() + 2
        while time.monotonic() < end and _pid_alive(pid):
            time.sleep(0.05)
        if not _pid_alive(pid):
            break
    _login_file().unlink(missing_ok=True)
    return True


def _login_running() -> bool:
    """A sign-in is open: started by this process, or by another Meshpool (its pid file names a live process)."""
    from meshpool_harness import fsio
    proc = _LOGIN["proc"]
    if proc is not None and proc.poll() is None:
        return True
    reap_stale_login()                            # one an app that is gone left behind does not count
    try:
        rec = fsio.read_json(_login_file(), None)
    except fsio.CorruptFile:                      # a damaged record names no sign-in
        return False
    # A record older than the timeout is stale even if its pid now belongs to an unrelated process.
    import time
    fresh = isinstance(rec, dict) and time.time() - float(rec.get("started") or 0) < LOGIN_TIMEOUT_S + 60
    return fresh and _pid_alive(rec.get("pid"))


def login(which: str) -> dict:
    """Start the official ChatGPT sign-in (Codex's own login), which opens the browser by itself. One at a time: a
    second click (or a second Meshpool) finds it running instead of fighting it for the sign-in port."""
    import subprocess
    import time
    from meshpool_harness import fsio
    if which == "codex" and unsupported("codex"):      # Codex's sign-in is not part of the packaged app
        raise ValueError("Signing in with ChatGPT needs Codex, which is not part of the Meshpool app. Use the Meshpool "
                         "API key or another API key, or sign in once with `codex login` from a terminal "
                         "(npm install -g @openai/codex): Meshpool then uses that sign-in.")
    if which == "codex":
        if not shutil.which("npx"):
            raise ValueError("Signing in with ChatGPT needs Node.js (npx), which was not found. Install Node.js, or use "
                             "an API key instead.")
        cmd = [npx(), "-y", CODEX_CLI, "login"]
        if os.name != "nt" and shutil.which("timeout"):
            # a time limit the sign-in keeps even if this app is killed and its watchdog with it
            cmd = ["timeout", "-k", "10", str(LOGIN_TIMEOUT_S), *cmd]
        try:
            with _LOGIN_LOCK, fsio.file_lock(_login_file(), timeout=10):
                if _login_running():
                    return {"ok": True, "already": True,
                            "message": "The ChatGPT sign-in is already open in your browser. Finish it there, or wait "
                                       "a moment and try again."}
                flags = {"creationflags": 0x08000000 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
                try:
                    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            stdin=subprocess.DEVNULL, **flags)
                except OSError as e:
                    raise ValueError(f"Could not start the sign-in: {e}")
                _LOGIN["proc"] = proc
                fsio.atomic_write_json(_login_file(), {"pid": proc.pid, "started": time.time(), "owner": os.getpid()})
        except TimeoutError:
            raise ValueError("Another sign-in is starting. Try again in a moment.")
        threading.Thread(target=_login_watchdog, args=(proc,), daemon=True).start()
        if not _LOGIN_ATEXIT[0]:
            import atexit
            _LOGIN_ATEXIT[0] = True
            atexit.register(stop_login)
        return {"ok": True, "message": "Your browser opens for the ChatGPT sign-in. Come back here when it says you "
                                       "are signed in."}
    raise ValueError("Unknown sign-in.")


def get(hid: str, model: str | None = None) -> dict:
    """A harness, with the credentials for the thread's model when one is given."""
    hid = LEGACY.get(hid, hid)
    for d in load_all():
        if d["id"] == hid:
            if d.get("error"):
                raise ValueError(f"{d['name']}: {d['error']}")
            if model and d.get("providers"):
                d = {k: v for k, v in d.items() if k not in ("env", "env_unset", "prepare", "auth", "plan", "setup")}
                cred = credentials(hid, model)
                # The harness's own environment (bundled Hermes: PYTHONPATH, HERMES_HOME ...) plus the provider's.
                env = {**(_manifest_env(hid)), **(cred.get("env") or {})}
                d.update(cred)
                if env:
                    d["env"] = env
            return d
    raise KeyError(hid)


def _manifest_env(hid: str) -> dict:
    """The environment a built-in harness itself needs, apart from any provider's credentials."""
    if hid == "hermes":
        return dict((_bundled_hermes() or {}).get("env") or {})
    h = next((h for h in (*BUILTIN, PI, AUTO, CODEX, OPENCODE) if h["id"] == hid), None)
    return dict((h or {}).get("env") or {})


def default_for(mode: str) -> str:
    want = (os.environ.get(f"MH_HARNESS_{mode.upper()}") or C.read().get(f"MH_HARNESS_{mode.upper()}") or "").strip()
    want = LEGACY.get(want, want)
    rows = {d["id"]: d for d in load_all()}

    def usable(hid: str) -> bool:
        d = rows.get(hid) or {}
        return bool(d.get("available") and not d.get("error") and d.get("ready", True))
    if want and usable(want):
        return want
    # Meshpool's own harnesses are the defaults: they passed every task in the practical check (13 Nov 2026) and
    # need nothing else installed. Hermes and the others stay one click away in the harness picker.
    return builtin_for(mode)


def builtin_for(mode: str) -> str:
    """The mode's own agent: the one place that names it (Auto's way back and default_for read it)."""
    return "meshpool-code" if mode == "code" else "meshpool-work"


def own_for(mode: str) -> str:
    """The agent a chat in this mode goes back to from an Auto pick: the user's default, or the mode's own agent when
    that default is Auto itself."""
    hid = default_for(mode)
    return builtin_for(mode) if hid == AUTO["id"] else hid


def add(text: str) -> dict:
    try:
        d = _validate(json.loads(text))
    except json.JSONDecodeError as e:
        raise ValueError(f"That file is not valid JSON: {e}")
    folder().mkdir(parents=True, exist_ok=True)
    p = folder() / f"{d['id']}.json"
    p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    return dict(d, available=_available(d), builtin=False, file=str(p))


def _check_id(hid: str) -> str:
    if not isinstance(hid, str) or not re.fullmatch(ID_RE, hid):
        raise KeyError(hid)
    return hid


def add_example(hid: str) -> dict:
    _check_id(hid)
    p = folder() / "examples" / f"{hid}.json"
    if not p.exists():
        raise KeyError(hid)
    return add(p.read_text(encoding="utf-8"))


def remove(hid: str) -> None:
    if hid in RESERVED:
        raise ValueError("Built-in harnesses cannot be removed.")
    _check_id(hid)
    p = folder() / f"{hid}.json"
    if not p.exists():
        raise KeyError(hid)
    p.unlink()


# ---------- providers: what the models run on -----------------------------------------------------------------------
# A harness is which agent runs; a provider is where its models run (providers.py). The model picker chooses the
# provider per thread; sign-up chooses the default one (MH_PROVIDER):
#   meshpool  the Meshpool key                  custom   another OpenAI-compatible key and URL
#   chatgpt   the user's ChatGPT sign-in
PROVIDER_NAMES = {"meshpool": "Meshpool API key", "custom": "Another API key", "chatgpt": "ChatGPT subscription"}
LOGINS = {"chatgpt": "codex"}   # provider -> the official sign-in it uses
SIGNIN_PROVIDERS = tuple(LOGINS)


def provider() -> str:
    p = (C.read().get("MH_PROVIDER") or os.environ.get("MH_PROVIDER") or "meshpool").strip() or "meshpool"
    return p if p in PROVIDER_NAMES else "meshpool"


def api_key() -> str:
    from meshpool_harness import providers as PV
    return PV.key()


def base_url() -> str:
    from meshpool_harness import providers as PV
    return PV.key_base()


def _launch(hid: str, prov: str | None) -> dict:
    """How a harness process starts on a provider: env, config to write, how Meshpool picks its models."""
    from meshpool_harness import providers as PV
    if hid == "codex":
        if prov == "key":
            return {"prepare": "codex", "models": "meshpool",
                    "env": {"CODEX_HOME": str(codex_home()), "MESHPOOL_API_KEY": PV.key()},
                    "env_unset": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}
        return {"models": "harness", "env": {"CODEX_HOME": str(user_codex_home())},
                "env_unset": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}
    if hid == "hermes":
        return {"prepare": "hermes"}
    if hid == "opencode":
        return {"prepare": "opencode", "env": {"OPENCODE_CONFIG": str(opencode_home() / "opencode.json"),
                                               "MESHPOOL_API_KEY": PV.agent_endpoint("key")[1]},
                "env_unset": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}
    return {}


def credentials(hid: str, model: str | None = None) -> dict:
    """What a built-in harness runs on: {providers, provider, ready, runs_on, setup} for the pickers, plus the
    environment and config its process starts with. model: the thread's model, whose provider wins when the
    harness can use it."""
    from meshpool_harness import providers as PV
    sup = PV.SUPPORTS.get(hid)
    if sup is None:
        return {}
    prov = PV.for_harness(hid, model)
    out = {"providers": sup, "provider": prov, "ready": prov is not None,
           "runs_on": PV.name(prov) if prov else ""}
    if prov == "chatgpt":
        out["plan"] = prov                      # a subscription: Auto uses it only when it is the chosen provider
    if prov is None:
        out["setup"] = PV.setup(sup[0])
    out.update(_launch(hid, prov))
    return out


def provider_values(which: str) -> dict:
    """What choosing a provider writes to the settings: the provider, the default agents and a model that fits it."""
    if which not in PROVIDER_NAMES:
        raise ValueError(f"Unknown provider: {which}")
    vals = {"MH_PROVIDER": which, "MH_HARNESS_CODE": "", "MH_HARNESS_COWORK": ""}
    cur = (C.read().get("MH_MODEL") or "").strip()
    if which == "chatgpt":
        from meshpool_harness.core import chatgpt as CG
        if not cur.startswith("chatgpt:"):
            vals["MH_MODEL"] = "chatgpt:" + CG.default_model()
    elif which in ("meshpool", "custom") and cur.startswith("chatgpt:"):
        vals["MH_MODEL"] = ""                   # back to the key endpoint's default model
    return vals


def apply_env(vals: dict) -> None:
    """Mirror settings just written into this process."""
    for k, v in vals.items():
        if v:
            os.environ[k] = v
        elif k != "MH_PROVIDER":
            os.environ.pop(k, None)


def use_provider(which: str) -> None:
    """Save the provider chosen at sign-up and pick the defaults from it: its models become the default model, and
    Meshpool Code and Work stay the default agents."""
    vals = provider_values(which)
    C.write(vals)
    apply_env(vals)


def ready() -> bool:
    """Whether Meshpool has something to run on: an API key, or a signed-in subscription chosen as the provider."""
    from meshpool_harness import providers as PV
    if provider() == "chatgpt":
        return signed_in(LOGINS["chatgpt"])
    return PV.ready("key")
