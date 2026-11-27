"""MCP client (Model Context Protocol), standard library only.

Transports:
- stdio: {"command": "npx", "args": [...], "env": {...}}   (the server is a child process; JSON-RPC over its pipes)
- streamable HTTP: {"url": "https://...", "headers": {"Authorization": "Bearer ${TOKEN}"}}
- HTTP with SSE (the 2024-11-05 transport): {"url": "https://.../sse", "type": "sse"}; also used on its own when a
  server refuses the streamable HTTP POST, as the MCP spec's backwards-compatibility section describes.

Config file: mcp.json next to meshpool.config, in the common shape most MCP clients use:
    {"mcpServers": {"name": {...}, ...}}
Extra keys: "disabled": true skips a server; "timeout": seconds per tool call (default 120).
${VAR} in any string is replaced from the environment (meshpool.config values are in the environment).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from meshpool_harness import __version__ as _VERSION
from meshpool_harness import config as C
from meshpool_harness import fsio
from meshpool_harness.core import http as _HTTP

PROTOCOL = "2025-06-18"
CLIENT = {"name": "meshpool", "version": _VERSION}
UA = {"User-Agent": f"Meshpool/{_VERSION} (MCP client)"}   # Python's default agent is blocked by many CDNs


def config_file() -> Path:
    p = os.environ.get("MESHPOOL_MCP")
    return Path(p) if p else C.home() / "mcp.json"


def _expand(v, extra: dict | None = None):
    if isinstance(v, str):
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                      lambda m: (extra or {}).get(m.group(1)) or os.environ.get(m.group(1), ""), v)
    if isinstance(v, list):
        return [_expand(x, extra) for x in v]
    if isinstance(v, dict):
        return {k: _expand(x, extra) for k, x in v.items()}
    return v


def _servers_key(data: dict) -> str:
    """The key a config file keeps its servers under: the common "mcpServers", or VS Code's "servers"."""
    return "mcpServers" if "mcpServers" in data else "servers" if "servers" in data else "mcpServers"


def _owner_only(path: Path) -> None:
    """mcp.json can hold tokens and API keys. One written by an earlier version may be 0644: make it 0600."""
    if os.name == "nt":
        return
    try:
        st = path.stat()
        if st.st_mode & 0o077 and st.st_uid == os.getuid():
            os.chmod(path, 0o600)
    except OSError:
        pass


def load_config(path: Path | None = None) -> dict:
    path = path or config_file()
    if not path.exists():
        return {}
    _owner_only(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    a, b = data.get("mcpServers"), data.get("servers")
    a, b = (a if isinstance(a, dict) else None), (b if isinstance(b, dict) else None)
    if a is not None and b is not None:
        return {**b, **a}                      # a file that ended up with both keys: show every server
    return a or b or {}


PROJECT_FILES = (".mcp.json", ".meshpool/mcp.json")


def project_servers(root) -> dict:
    """MCP servers a project lists in .mcp.json (the shared format) or .meshpool/mcp.json. They start programs, so
    they are used only when the user trusts the folder (trust.py). The user's own servers win on a name clash."""
    from meshpool_harness import trust as TR
    if not root or not TR.is_trusted(root):
        return {}
    out: dict = {}
    for rel in PROJECT_FILES:
        try:
            got = load_config(Path(root) / rel)
        except (ValueError, OSError):
            continue
        out.update({k: v for k, v in got.items() if isinstance(v, dict)})
    return out


_CFG_LOCK = threading.Lock()


def write_text_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """Write a file so a reader never sees half of it: a temporary file next to it, then a rename."""
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:   # it may hold tokens and API keys: always owner-only, also when an older version left it 0644
            os.chmod(tmp, 0o600 if mode is None else mode)
        except OSError:
            pass
        C.replace_file(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def edit_config(fn, path: Path | None = None, create: bool = True, whole: bool = False):
    """The one way to change mcp.json: load it, let fn change the servers dict in place (fn(servers), or
    fn(servers, document) with whole=True), and write it back atomically. The servers stay under the key the file
    already uses ("mcpServers", else VS Code's "servers"); a file that ended up with both is merged into one. Nothing
    is written when fn changed nothing. A missing file is created (create=True) or left alone (returns None).
    Returns what fn returned. Raises ValueError when the file is not valid JSON."""
    path = Path(path or config_file())
    with _CFG_LOCK:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            cfg = json.loads(text) if text.strip() else {}
        elif create:
            cfg = {}
        else:
            return None
        if not isinstance(cfg, dict):
            raise ValueError(f"{path.name} must hold a JSON object")
        before = json.dumps(cfg, sort_keys=True)
        key = _servers_key(cfg)
        had = key in cfg
        if had and not isinstance(cfg[key], dict):
            raise ValueError(f'"{key}" in {path.name} must be an object of servers')
        servers = cfg.setdefault(key, {})
        other = cfg.get("servers") if key == "mcpServers" else None
        if isinstance(other, dict):                # repair a file an older version gave a second key
            servers.update({k: v for k, v in other.items() if k not in servers})
            del cfg["servers"]
        out = fn(servers, cfg) if whole else fn(servers)
        if not had and not cfg.get(key):
            cfg.pop(key, None)
            if not path.exists():
                cfg[key] = {}
        if path.exists() and json.dumps(cfg, sort_keys=True) == before:
            return out
        write_text_atomic(path, json.dumps(cfg, indent=2) + "\n")
        return out


_PLACEHOLDER_TOKEN = re.compile(r"^(x+|\*+|\.+|-+|none|null|todo|changeme|change[-_ ]?me|your[-_ ]?\w*|"
                                r"<.*>|\$\{.*\}|\{\{.*\}\}|.*(?:proxy|placeholder|example|dummy|token[-_]here).*)$", re.I)


def token_problem(spec: dict) -> str:
    """Why the token in a connector's Authorization header cannot be real (empty, whitespace or a line break inside,
    an obvious placeholder), or "" when it may be."""
    hdr = (spec.get("headers") or {}).get("Authorization")
    if hdr is None:
        return ""
    tok = re.sub(r"^bearer\s+", "", str(hdr), flags=re.I)
    if not tok.strip() or re.search(r"\s", tok) or _PLACEHOLDER_TOKEN.match(tok.strip()):
        return "looks invalid"
    return ""


def active_config(path: Path | None = None) -> dict:
    """mcp.json plus the connectors the user signed in to (GitHub, Jira, Linear, ...), with fresh credentials."""
    cfg = load_config(path)
    try:
        from meshpool_harness import mcp_apps as CN
        for name, spec in CN.servers().items():
            cur = cfg.get(name)
            if not isinstance(cur, dict) or cur.get("disabled"):
                if token_problem(spec):          # a placeholder token only makes a red "failed" server
                    spec = dict(spec, disabled=True, description=(
                        f"The {name.upper()}_TOKEN looks invalid: unset it, or run meshpool connect {name}."))
                cfg[name] = spec
    except Exception:  # noqa: BLE001  a connector problem must never block the other servers
        pass
    return cfg


# ---------- health: remember which servers failed, so one broken server does not slow every new thread ----------

HEALTH_TTL = 30 * 60
_HLOCK = threading.Lock()


def _health_file() -> Path:
    return C.home() / "mcp-health.json"


def _sig(spec) -> str:
    import hashlib
    return hashlib.sha1(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _read_health() -> dict:
    try:
        d = fsio.read_json(_health_file(), {})
        return d if isinstance(d, dict) else {}
    except fsio.CorruptFile:
        return {}                                   # only a cache of recent failures: starting afresh is safe


_TIMEOUT_ERR = re.compile(r"no reply to .* within \d+ s|did not start within|still starting", re.I)


def record_health(rows: dict) -> None:
    """rows: {name: (spec, ok, error)}. Only real failures (and successes) are remembered: a start that merely ran
    out of time (a cold npx download) says nothing about the server, so the next chat tries again."""
    rows = {n: r for n, r in (rows or {}).items() if r[1] or not _TIMEOUT_ERR.search(str(r[2] or ""))}
    if not rows:
        return
    with _HLOCK:
        d = _read_health()
        now = time.time()
        for name, (spec, ok, err) in rows.items():
            d[name] = {"sig": _sig(spec), "ok": bool(ok), "error": redact(err)[:300], "t": now}
        try:
            fsio.atomic_write_json(_health_file(), d, fsync=False, private=True)
        except OSError:
            pass


def recently_failed(name: str, spec) -> str | None:
    """The error, if this exact server config failed in the last 30 minutes; else None."""
    h = _read_health().get(name)
    if h and not h.get("ok") and h.get("sig") == _sig(spec) and time.time() - float(h.get("t") or 0) < HEALTH_TTL:
        return h.get("error") or "failed"
    return None


def _short_hash(*parts: str) -> str:
    import hashlib
    return hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()[:8]


def tool_name(server: str, tool: str) -> str:
    """The function name a provider sees: letters, digits, _ and -, at most 64 characters. A name that had to be cut
    ends in a short hash of the full server and tool names, so two long names never become the same."""
    n = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{server}__{tool}")
    return n if len(n) <= 64 else n[:55] + "_" + _short_hash(server, tool)


def unique_tool_names(pairs) -> dict:
    """{(server, tool): function name} for every pair, every name distinct. When two pairs come out the same after
    sanitising ("a.b" and "a_b"), the later one gets a hash suffix."""
    out, used = {}, set()
    for server, tool in pairs:
        if (server, tool) in out:
            continue
        n = tool_name(server, tool)
        i = 0
        while n in used:
            base = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{server}__{tool}")[:55]
            n = base + "_" + _short_hash(server, tool, str(i))
            i += 1
        used.add(n)
        out[(server, tool)] = n
    return out


_SECRET_Q = re.compile(r"([?&;](?:[A-Za-z0-9_.-]*(?:key|token|secret|password|passwd|auth|sig|signature|code)"
                       r"[A-Za-z0-9_.-]*)=)[^&#\s\"'<>]+", re.I)


def redact(text) -> str:
    """Hide secrets carried in URL query strings (?apikey=..., &access_token=...) in messages people see or save."""
    return _SECRET_Q.sub(r"\1***", str(text or ""))


class MCPError(Exception):
    pass


class _AuthRefused(MCPError):
    """HTTP 401/403: the credentials were refused (a connector's token may only need refreshing)."""


class _NotSent(MCPError):
    """The request could not be written (the server process is gone), so it never reached the server and it is safe
    to start the server again and send it once more."""


class Cancelled(MCPError):
    """The user pressed Stop (or the call timed out) and the server was told to drop the request."""


class _SessionLost(MCPError):
    """The server forgot the session (HTTP 404 with a session id, or the legacy event stream closed) before it saw
    the request, so it is safe to connect again and resend."""


# ---------- transports --------------------------------------------------------------------------------------------

_SECRET_RE = __import__("re").compile(r"(API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE_KEY)$", 2)


def _secret_name(k: str) -> bool:
    return k in C.SECRET or bool(_SECRET_RE.search(k)) or k in ("OPENAI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")


def raise_file_limit(want: int = 4096) -> None:
    """Many chats mean many helper processes and pipes: macOS starts at 256 open files per process. Raise the soft
    limit to 4096 (or the hard limit when that is lower). Windows has no such limit."""
    if os.name == "nt":
        return
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        top = hard if hard != resource.RLIM_INFINITY else want
        new = max(soft, min(want, top)) if soft != resource.RLIM_INFINITY else soft
        if hard != resource.RLIM_INFINITY and new > hard:
            new = hard
        if new > soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (new, hard))
    except (ImportError, ValueError, OSError):
        pass


_LOGIN_ENV: list = []
_LOGIN_ENV_LOCK = threading.Lock()       # servers start in parallel threads: the login shell runs once, not per server


def login_shell_env() -> dict:
    """The environment a terminal would give a program: the app started from the Dock or Start menu has only a bare
    one (no nvm, no pyenv, no tokens set in .zshrc), so a server that works from the terminal failed in Meshpool.
    Resolved once, as VS Code does: run the user's shell as an interactive login shell and read its env. {} when it
    cannot be read (then the app's own environment is used). Off with MH_LOGIN_ENV=0."""
    if _LOGIN_ENV:
        return _LOGIN_ENV[0]
    with _LOGIN_ENV_LOCK:
        if not _LOGIN_ENV:
            _LOGIN_ENV.append(_read_login_env())
    return _LOGIN_ENV[0]


def _read_login_env() -> dict:
    got: dict = {}
    if os.name != "nt" and os.environ.get("MH_LOGIN_ENV", "1") != "0" and not os.environ.get("MESHPOOL_RESOLVING_ENV"):
        sh = os.environ.get("MH_LOGIN_SHELL") or os.environ.get("SHELL") or "/bin/sh"
        mark = "MESHPOOL_ENV_" + str(os.getpid())
        try:
            r = subprocess.run([sh, "-ilc", f"printf '%s' {mark}; env -0; printf '%s' {mark}"], capture_output=True,
                               timeout=float(os.environ.get("MH_LOGIN_ENV_S", "10")), stdin=subprocess.DEVNULL,
                               env={**os.environ, "MESHPOOL_RESOLVING_ENV": "1"})
            out = r.stdout.decode("utf-8", "replace")
            if out.count(mark) >= 2:
                body = out.split(mark)[1]
                got = dict(kv.split("=", 1) for kv in body.split("\0") if "=" in kv)
        except (OSError, subprocess.SubprocessError, ValueError):
            got = {}
    return got


def child_env() -> dict:
    """What an MCP server starts with: the terminal's environment plus anything Meshpool itself was started with
    (its own settings win), PATH merged so neither side's folders are lost."""
    env = dict(os.environ)
    login = login_shell_env()
    if login:
        merged = {**login, **env}
        for k in ("PATH",):
            seen, parts = set(), []
            for part in (login.get(k, "") + os.pathsep + env.get(k, "")).split(os.pathsep):
                if part and part not in seen:
                    seen.add(part)
                    parts.append(part)
            merged[k] = os.pathsep.join(parts)
        env = merged
        for k in ("MESHPOOL_RESOLVING_ENV", "PWD", "OLDPWD", "SHLVL", "_"):
            env.pop(k, None)
        try:
            # The packaged app's own Node, Git and tools stay first: the login PATH went ahead of them, so the bundled
            # npx (#!/usr/bin/env node) ran on an older system Node from /usr/local/bin or Homebrew and failed
            from meshpool_harness import bundled as _B
            front = [p for p in _B.front_dirs() if p]
        except Exception:  # noqa: BLE001
            front = []
        if front:
            rest = [p for p in env.get("PATH", "").split(os.pathsep) if p and p not in front]
            env["PATH"] = os.pathsep.join(front + rest)
    # PA-toplevel-23: a server outlives the turn it was started in; with the turn's MH_CHECK_RUN it would record every
    # later turn's checks into that first one. Without it, it follows checks.current() (the pointer file).
    env.pop("MH_CHECK_RUN", None)
    return env


_RUNNERS = {"npx", "uvx", "uv", "bunx", "pnpm", "pipx", "deno", "docker", "podman"}


def first_response_seconds(spec: dict | None = None) -> float:
    """How long an MCP server may take to answer initialize. Upstream clients wait about 25 s; a server started by a
    package runner (npx, uvx) may be downloading itself the first time, so it gets longer, and the chat does not wait
    for it (see Manager.start). Per server: "init_timeout"; for all: MH_MCP_INIT_S."""
    spec = spec or {}
    try:
        if spec.get("init_timeout"):
            return max(1.0, float(spec["init_timeout"]))
    except (TypeError, ValueError):
        pass
    try:
        base = max(1.0, float(os.environ.get("MH_MCP_INIT_S", "25")))
    except ValueError:
        base = 25.0
    if is_cold_start(spec):
        return max(base, float(os.environ.get("MH_MCP_COLD_S", "180") or 180))
    return base


def is_cold_start(spec: dict) -> bool:
    """A package runner that may download the server on first use."""
    cmd = Path(str(spec.get("command") or "")).name.lower()
    cmd = re.sub(r"\.(cmd|exe|bat)$", "", cmd)
    return cmd in _RUNNERS and not spec.get("url")


class _Stdio:
    def __init__(self, name: str, spec: dict, log_dir: Path, roots: list[str]):
        cmd = spec["command"]
        if cmd in ("python", "python3", "py"):
            cmd = sys.executable                          # the same Python Meshpool runs on
        exe = shutil.which(cmd) or cmd                    # Windows: npx -> npx.cmd
        args = [exe, *[str(a) for a in spec.get("args", [])]]
        env = child_env()
        if exe != sys.executable:
            # Someone else's server (often an unpinned npx package) gets none of Meshpool's own provider keys unless
            # its "env" names them. Everything else the terminal has (GITHUB_TOKEN set in .zshrc, ...) is passed on.
            env = {k: v for k, v in env.items() if k not in C.SECRET or login_shell_env().get(k) == v}
        if roots:
            env["MESHPOOL_PROJECT"] = str(Path(roots[0]).resolve())    # not the shared os.environ: it may be another chat's
        env.update({k: str(v) for k, v in (spec.get("env") or {}).items()})
        env.setdefault("PYTHONUTF8", "1")
        if exe == sys.executable:
            env["PYTHONPATH"] = str(C.APP) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log = log_dir / f"{name}.log"
        from meshpool_harness.logfiles import PipedLog       # H-23: the server's log rotates by size while it runs
        self.err = PipedLog(self.log)
        self.log_start = self.err.tell()
        kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
        try:
            self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err,
                                      env=env, cwd=spec.get("cwd") or None, **kw)
        except BaseException as e:
            self.err.close()
            import shutil as _sh               # an unreadable folder on PATH turns "not found" into EACCES
            missing = isinstance(e, FileNotFoundError) or (
                isinstance(e, PermissionError) and os.sep not in str(spec["command"]) and not _sh.which(spec["command"]))
            if not missing:
                raise
            raise MCPError(f"command not found: {spec['command']}"
                           + (" (install Node.js from nodejs.org)" if spec["command"] in ("npx", "node") else "")
                           + (" (install uv from docs.astral.sh/uv)" if spec["command"] in ("uvx", "uv") else ""))
        self.err.release()
        self.roots = roots
        self.grace = 6.0 if any("browser_mcp_server" in str(a) for a in args) else 0.0   # time to exit on EOF
        self.pending: dict[int, dict] = {}
        self.cv = threading.Condition()
        self.alive = True
        self.reader_error = ""
        self.cancelled_ids: set = set()                # requests we told the server to drop (Stop, timeout)
        self.cancel_why: dict = {}
        self.closing = False
        self._rt = threading.Thread(target=self._reader, daemon=True)
        self._rt.start()

    def _reader(self):
        try:
            for raw in self.p.stdout:
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue                               # servers sometimes print banners
                if not isinstance(msg, dict):              # a stray "42" or "true" on stdout is not a message
                    continue
                rid = msg.get("id")
                if not isinstance(rid, (int, str)) or isinstance(rid, bool):
                    continue                               # notifications, and ids no request of ours could have
                if "method" in msg:                        # server -> client request
                    try:
                        self._answer(msg)
                    except MCPError:
                        pass                               # stdin closed: the loop ends when stdout does
                    continue
                with self.cv:
                    if rid in self.cancelled_ids:          # a late reply to a call we gave up on: drop it
                        self.cancelled_ids.discard(rid)
                        continue
                    self.pending[rid] = msg
                    self.cv.notify_all()
        except Exception as e:  # noqa: BLE001  never leave waiters hanging until their timeout
            if not self.closing:
                self.reader_error = f"{type(e).__name__}: {e}"
                try:
                    self.p.kill()
                except Exception:  # noqa: BLE001
                    pass
        with self.cv:
            self.alive = False
            self.cv.notify_all()

    def _answer(self, msg):
        m = msg.get("method")
        if m == "roots/list":
            res = {"result": {"roots": [{"uri": Path(r).resolve().as_uri(), "name": Path(r).name} for r in self.roots]}}
        elif m == "ping":
            res = {"result": {}}
        else:
            res = {"error": {"code": -32601, "message": f"method not supported: {m}"}}
        self._write({"jsonrpc": "2.0", "id": msg["id"], **res})

    def _write(self, obj):
        try:
            self.p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise _NotSent(self._exit_reason())

    def _exit_reason(self) -> str:
        """Why the server stopped, from the last meaningful line it printed (its whole log is in runs/mcp-logs)."""
        try:
            self.err.flush()
            with open(self.log, encoding="utf-8", errors="replace") as f:
                f.seek(self.log_start)
                lines = [ln.strip() for ln in f.read()[-20000:].splitlines()]
        except OSError:
            lines = []
        skip = re.compile(r"^(npm (warn|notice)|at |node:|Node\.js v|File \"|\^+$|~+|\{|\}|status:|signal:|output:|"
                          r"pid:|stdout:|stderr:|throw err|warning: The `UV_|Traceback|Installed|Resolved|Prepared|"
                          r"Downloading|Downloaded|Updated|Updating|Building|Built)")
        why = []
        for ln in lines:
            if ln.startswith("{") and ln.endswith("}"):           # a JSON log line: keep its message
                try:
                    j = json.loads(ln)
                    ln = str(j.get("message") or j.get("msg") or j.get("error") or "")
                except ValueError:
                    pass
            if ln and not skip.match(ln) and not ln.startswith("Error: Command failed") and \
                    not re.match(r"^[A-Za-z_]+: ['\"\d-]", ln):     # object dumps such as "port: 1"
                why.append(ln)
        bad = [ln for ln in why if re.search(r"error|fatal|fail|invalid|denied|missing|refused|not found|unauthori",
                                             ln, re.I)]
        msg = (bad or why or [""])[-1][:300]
        return "the server process exited" + (f": {msg}" if msg else "") + f" (log: {self.log.name} in runs/mcp-logs)"

    def request(self, rid: int, method: str, params: dict, timeout: float) -> dict:
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        end = time.time() + timeout
        with self.cv:
            while rid not in self.pending:
                if rid in self.cancelled_ids:
                    raise Cancelled(self.cancel_why.get(rid) or "cancelled")
                if not self.alive:
                    if self.reader_error:
                        raise MCPError(f"Meshpool stopped reading the server's replies ({self.reader_error})")
                    raise MCPError(self._exit_reason())
                left = end - time.time()
                if left <= 0:
                    break
                self.cv.wait(min(left, 0.5))
            else:
                return self.pending.pop(rid)
        if method != "initialize":
            self.cancel_request(rid, f"no reply within {int(timeout)} s")       # the server must not run it later
        raise MCPError(f"no reply to {method} within {int(timeout)} s")

    def cancel_request(self, rid: int, reason: str = "cancelled by the user") -> None:
        """MCP spec: notifications/cancelled with the request id. We stop waiting at once and drop a late reply."""
        with self.cv:
            if rid in self.pending:
                return
            self.cancelled_ids.add(rid)
            self.cancel_why[rid] = reason
            self.cv.notify_all()
        try:
            self.notify("notifications/cancelled", {"requestId": rid, "reason": reason})
        except MCPError:
            pass                                          # the process is gone: nothing left to tell

    def notify(self, method: str, params: dict | None = None):
        self._write({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def close(self):
        """Stop the process and everything it started, and give back its pipes (file descriptors)."""
        self.closing = True
        try:
            self.p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        if self.grace:                       # the browser server closes its window on EOF, which saves sign-ins
            try:
                self.p.wait(timeout=self.grace)
            except Exception:  # noqa: BLE001
                pass
        if os.name == "nt":
            # taskkill /T finds the children (npx -> node, uvx -> python, a .cmd's child) through the leader, so it
            # has to run while the leader is still alive: terminate() first left the whole tree running.
            kill_tree(self.p)
        try:
            self.p.terminate()
            self.p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        kill_tree(self.p)                    # npx, uvx and scripts start children: end the whole tree
        try:
            self.p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        self._rt.join(2.0)
        for f in (self.p.stdout, self.p.stderr):
            try:
                if f:
                    f.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.err.close()
        except Exception:  # noqa: BLE001
            pass


def kill_tree(p) -> None:
    """End a process and all it started (killpg on macOS and Linux, taskkill /T /F on Windows). It also finds
    children of a process that already exited when it led its own group."""
    from meshpool_harness.core.tools import _kill_tree
    try:
        if os.name == "nt":
            if p.poll() is None:
                _kill_tree(p)
            return
        import signal
        try:
            os.killpg(p.pid, signal.SIGKILL)      # the group outlives its leader when children remain
        except (ProcessLookupError, PermissionError, OSError):
            pass
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


class _Http:
    def __init__(self, name: str, spec: dict):
        self.url = spec["url"]
        self.headers = {k: str(v) for k, v in (spec.get("headers") or {}).items()}
        self.session = None
        self.version = None

    def _post(self, obj: dict, timeout: float, want_id=None) -> dict | None:
        h = {**UA, "Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        req = urllib.request.Request(self.url, data=json.dumps(obj).encode(), headers=h, method="POST")
        try:
            r = _HTTP.open(req, timeout)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            if e.code in (401, 403):
                raise _AuthRefused(f"HTTP {e.code}: the server refused the credentials. Sign in again under Settings, "
                                   "Connectors, or check the headers in mcp.json")
            if e.code == 404 and self.session:     # the session expired: the spec says start a new one
                raise _SessionLost(f"HTTP 404: the server ended the session ({redact(body)})")
            raise MCPError(f"HTTP {e.code}: {redact(body)}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(redact(f"could not reach {self.url}: {e}"))
        with r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.session = sid
            if want_id is None:
                return None
            ctype = r.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                data = []
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif line == "" and data:
                        try:
                            msg = json.loads("\n".join(data))
                        except ValueError:
                            msg = {}
                        data = []
                        if msg.get("id") == want_id:
                            return msg
                raise MCPError("the event stream ended without a reply")
            txt = r.read().decode("utf-8", "replace")
            try:
                msg = json.loads(txt) if txt.strip() else {}
            except ValueError:
                raise MCPError("the server did not answer with an MCP reply (a login or proxy page?): "
                               + txt.strip()[:80].replace("\n", " "))
            if isinstance(msg, list):
                msg = next((m for m in msg if isinstance(m, dict) and m.get("id") == want_id), {})
            return msg if isinstance(msg, dict) else {}

    def request(self, rid, method, params, timeout):
        return self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, timeout, want_id=rid)

    def notify(self, method, params=None):
        try:
            self._post({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})}, 30)
        except MCPError:
            pass

    def close(self):
        if self.session:
            try:
                h = {**UA, "Mcp-Session-Id": self.session, **self.headers}
                _HTTP.open(urllib.request.Request(self.url, headers=h, method="DELETE"), 5)
            except Exception:
                pass


class _Sse:
    """The older HTTP+SSE transport: a GET event stream that first names the URL to POST messages to, then carries
    the replies."""

    def __init__(self, name: str, spec: dict, timeout: float = 30):
        import urllib.parse
        self.url = spec["url"]
        self.headers = {k: str(v) for k, v in (spec.get("headers") or {}).items()}
        self.pending: dict = {}
        self.cv = threading.Condition()
        self.alive = True
        self.endpoint = None
        req = urllib.request.Request(self.url, headers={**UA, "Accept": "text/event-stream", **self.headers})
        try:                                    # the timeout covers connecting and the response headers only
            self.r = _HTTP.open(req, timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise _AuthRefused(f"HTTP {e.code}: the server refused the credentials. Sign in again under "
                                   "Settings, Connectors, or check the headers in mcp.json")
            raise MCPError(f"HTTP {e.code} opening the event stream")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(redact(f"could not reach {self.url}: {e}"))
        if "text/event-stream" not in self.r.headers.get("Content-Type", ""):
            self.r.close()
            raise MCPError("the server is not an MCP server (no event stream)")
        self._join = urllib.parse.urljoin
        end = time.time() + timeout
        threading.Thread(target=self._reader, daemon=True).start()
        with self.cv:
            while self.endpoint is None and self.alive and time.time() < end:
                self.cv.wait(0.3)
        if not self.endpoint:
            self.close()
            raise MCPError("the event stream did not say where to send messages")

    def _sock(self):
        try:
            return self.r.fp.raw._sock
        except AttributeError:
            return None

    def _no_read_timeout(self):
        """An event stream may be silent for minutes between replies; the connect timeout must not end it. Each
        request still waits only its own timeout for its reply."""
        s = self._sock()
        if s is not None:
            try:
                s.settimeout(None)
            except OSError:
                pass

    def _reader(self):
        event, data = "message", []
        try:
            for raw in self.r:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif line == "":
                    if data:
                        payload = "\n".join(data)
                        with self.cv:
                            if event == "endpoint":
                                self.endpoint = self._join(self.url, payload.strip())
                                self._no_read_timeout()
                            else:
                                try:
                                    msg = json.loads(payload)
                                except ValueError:
                                    msg = {}
                                if isinstance(msg, dict) and "id" in msg and "method" not in msg:
                                    self.pending[msg["id"]] = msg
                            self.cv.notify_all()
                    event, data = "message", []
        except Exception:  # noqa: BLE001  the stream closed
            pass
        with self.cv:
            self.alive = False
            self.cv.notify_all()

    def _send(self, obj: dict):
        req = urllib.request.Request(self.endpoint, data=json.dumps(obj).encode(), method="POST",
                                     headers={**UA, "Content-Type": "application/json", **self.headers})
        try:
            _HTTP.open(req, 30).close()
        except urllib.error.HTTPError as e:
            body = redact(e.read()[:200].decode('utf-8', 'replace'))
            if e.code in (401, 403):
                raise _AuthRefused(f"HTTP {e.code}: the server refused the credentials. Sign in again under "
                                   "Settings, Connectors, or check the headers in mcp.json")
            if e.code == 404:                   # the server no longer knows this stream's session
                raise _SessionLost(f"HTTP 404: the server ended the session ({body})")
            raise MCPError(f"HTTP {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(redact(f"could not reach {self.endpoint}: {e}"))

    def request(self, rid, method, params, timeout):
        if not self.alive:                      # it died while idle: the caller connects again and resends
            raise _SessionLost("the server closed the event stream")
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        end = time.time() + timeout
        with self.cv:
            while rid not in self.pending:
                if not self.alive:              # after sending: the request may have run, so do not resend it
                    raise MCPError("the server closed the event stream")
                left = end - time.time()
                if left <= 0:
                    raise MCPError(f"no reply to {method} within {int(timeout)} s")
                self.cv.wait(min(left, 0.5))
            return self.pending.pop(rid)

    def notify(self, method, params=None):
        try:
            self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})
        except MCPError:
            pass

    def close(self):
        self.alive = False
        try:                                    # shut the socket first: close() alone waits for the reader thread
            import socket
            self.r.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.r.close()
        except Exception:  # noqa: BLE001
            pass


def _sse_spec(spec: dict) -> bool:
    return str(spec.get("type") or spec.get("transport") or "").lower() == "sse" or \
        str(spec.get("url", "")).rstrip("/").endswith("/sse")


# ---------- one process per app, not per chat -----------------------------------------------------------------------
# A stdio server (the office tools, the web and assistant servers...) is a Python or Node process. Every chat used to
# start its own: five chats, five processes, each importing its libraries at once. Now chats on the same folder share
# one process; it starts on the first tool call (the tool list comes from a cache on disk), a chat that closes lets
# go of it, and one nobody has used for MH_MCP_IDLE_S seconds (60) is stopped and starts again on the next call.
# Browser and computer servers keep per-chat state (pages, the screen), so they stay one process per chat. An app
# chat's browser (MH_CHAT_ID in its env, so its key is its own) is pooled too, only to start on its first call instead
# of when the chat opens; it is never stopped for being idle (it would lose the pages and sign-ins), only when the chat
# closes. The app's office and assistant servers serve every chat from one process: the chat's folder and id go with
# each call (_project, _chat), not in the process's environment, which would make one process per chat again.

_STATEFUL = ("browser", "computer")
_POOL: dict[str, "_Entry"] = {}
_PLOCK = threading.Lock()
_REAPER: list = []
_REAPER_THREAD: list = []


class _Entry:
    def __init__(self):
        self.lock = threading.RLock()
        self.init_lock = threading.Lock()   # one initialize at a time, outside self.lock
        self.t = None                    # the shared _Stdio, None while stopped
        self.ready = False               # initialize already done on self.t
        self.info: dict = {}
        self.refs: set = set()           # the Server objects (chats) using it
        self.inflight = 0
        self.last = time.time()
        self.keep = False                # a stateful server (a chat's browser): never stopped for being idle
        self._n = 0

    def next_id(self) -> int:
        with self.lock:
            self._n += 1
            return self._n

    def stop(self):
        with self.lock:
            t, self.t, self.ready = self.t, None, False
        if t is not None:
            try:
                t.close()
            except Exception:  # noqa: BLE001
                pass


def idle_seconds() -> float:
    try:
        return float(os.environ.get("MH_MCP_IDLE_S", "60"))
    except ValueError:
        return 60.0


def _reap_loop():
    stop = _REAPER_STOP
    while True:
        limit = idle_seconds()
        if stop.wait(max(0.5, min(15.0, limit / 4 if limit > 0 else 15.0))):
            return
        with _PLOCK:
            entries = list(_POOL.values())
        for e in entries:
            with e.lock:
                if limit > 0 and e.t is not None and not e.inflight and not e.keep and time.time() - e.last > limit:
                    e.stop()                          # idle: no process, no CPU; the next call starts it again


_REAPER_STOP = threading.Event()


def start_reaper() -> None:
    """Start the idle reaper. The app calls this at start-up, so helper processes of chats reopened after a restart
    are stopped when idle even before anything else touches MCP."""
    _ensure_reaper()


def _ensure_reaper():
    with _PLOCK:
        if _REAPER and not (_REAPER_THREAD and _REAPER_THREAD[0].is_alive()):
            _REAPER.clear()
        if _REAPER:
            return
        _REAPER.append(1)
        _REAPER_STOP.clear()
    th = threading.Thread(target=_reap_loop, daemon=True, name="mcp-reaper")
    _REAPER_THREAD[:] = [th]
    th.start()


def stop_all() -> None:
    """Stop every shared server process (the app is closing)."""
    with _PLOCK:
        entries = list(_POOL.values())
        _POOL.clear()
    for e in entries:
        e.stop()


import atexit  # noqa: E402

atexit.register(stop_all)


def _pool_key(spec: dict, roots: list) -> str:
    """Same command, environment and folder: one process. A server that takes the folder with each call
    (per_call_project) is one process for the whole app."""
    env = spec.get("env")
    where = str(Path(roots[0]).resolve()) if roots else ""
    if spec.get("per_call_project"):
        where = ""
        env = {k: v for k, v in env.items() if k != "MESHPOOL_PROJECT"} if isinstance(env, dict) else env
    return json.dumps([spec.get("command"), spec.get("args"), env, spec.get("cwd"), where], sort_keys=True, default=str)


def _cache_file() -> Path:
    return C.home() / "mcp-tools-cache.json"


def _cache_key(name: str, spec: dict) -> str:
    # MH_CHAT_ID only tells a built-in server which chat it serves (live view, task mode, build counts); its tool list
    # is the same for every chat, so a new chat (every `meshpool -p`) finds the saved list instead of starting servers
    env = spec.get("env")
    if isinstance(env, dict) and "MH_CHAT_ID" in env:
        spec = {**spec, "env": {k: v for k, v in env.items() if k != "MH_CHAT_ID"} or None}
    stamp = ""
    try:
        stamp = str(int(max(f.stat().st_mtime for f in Path(__file__).parent.glob("*_mcp_server.py"))))
    except (OSError, ValueError):
        pass
    sig = json.dumps([name, _pool_key(spec, []), stamp, _VERSION, sys.executable], sort_keys=True, default=str)
    return _short_hash(sig)


_CACHE_TTL = 24 * 3600


def cached_tools(name: str, spec: dict):
    """(tools, info) saved from an earlier start of this exact server, or None."""
    try:
        d = json.loads(_cache_file().read_text(encoding="utf-8"))
        e = d.get(_cache_key(name, spec))
        if e and time.time() - float(e.get("t") or 0) < _CACHE_TTL and e.get("tools"):
            return e["tools"], e.get("info") or {}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def save_tools(name: str, spec: dict, tools: list, info: dict) -> None:
    try:
        f = _cache_file()
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        d = {k: v for k, v in d.items() if isinstance(v, dict) and time.time() - float(v.get("t") or 0) < _CACHE_TTL}
        d[_cache_key(name, spec)] = {"t": time.time(), "tools": tools, "info": info}
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


# ---------- one server ---------------------------------------------------------------------------------------------

RES_LIST, RES_READ = "mcp__meshpool_resources__list_resources", "mcp__meshpool_resources__read_resource"
_READ_WORDS = {"get", "list", "search", "read", "fetch", "find", "query", "describe", "view", "show", "lookup",
               "count", "retrieve", "browse", "check", "inspect", "status", "info", "stats", "preview", "explain",
               "summarize", "summary", "download", "export", "resolve", "estimate", "validate", "analyze", "lookup"}
_WRITE_VERBS = {"create", "update", "delete", "remove", "send", "post", "write", "add", "set", "put", "patch", "edit",
                "move", "merge", "archive", "close", "reopen", "assign", "invite", "upload", "publish", "cancel",
                "approve", "reply", "comment", "insert", "modify", "rename", "transfer", "trigger", "run", "execute",
                "exec", "start", "stop", "pause", "resume", "mark", "label", "draft", "schedule", "share", "duplicate",
                "enable", "disable", "batch", "bulk", "forward", "trash", "untrash", "apply", "deploy", "push", "fork",
                "buy", "submit", "kill", "revoke", "rotate", "restore", "reset", "upsert", "import", "commit",
                "complete", "replace", "connect", "disconnect", "purchase", "pay", "charge", "refund", "migrate",
                "drop", "truncate", "grant", "unlock", "lock", "login", "logout", "register", "subscribe",
                "unsubscribe", "save", "store", "toggle", "rerun", "retry", "invoke", "call", "generate", "make",
                "build", "install", "uninstall", "configure", "provision", "terminate", "rollback", "promote",
                "release", "sync", "clone", "copy", "attach", "detach", "link", "unlink", "react", "vote", "follow",
                "unfollow", "block", "unblock", "mute", "unmute", "pin", "unpin", "star", "unstar", "accept",
                "decline", "reject", "dismiss", "resolve_thread", "rate", "tag", "untag", "notify", "email", "message",
                "book", "order", "sign", "request", "convert", "clear", "flush", "purge", "wipe", "erase"}


def _words(name: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name or ""))     # createIssue -> create_Issue
    return [w for w in re.split(r"[^A-Za-z0-9]+", s.lower()) if w]


def read_only_tools(tools: list[dict]) -> list[dict]:
    """The tools of a connector in read-only mode. The server's own readOnlyHint decides when it gives one; else a
    tool stays only when its name has a read word (get, list, search...) and no word that changes something
    (create, push, deploy, apply...): an allow list, so an unknown verb is left out, not let through."""
    def ok(t):
        ann = t.get("annotations") or {}
        if ann.get("readOnlyHint") is True and ann.get("destructiveHint") is not True:
            return True
        if ann.get("readOnlyHint") is False or ann.get("destructiveHint") is True:
            return False
        w = _words(t.get("name"))
        return bool(set(w) & _READ_WORDS) and not (set(w) & _WRITE_VERBS)
    return [t for t in tools if ok(t)]


def _tool_list(v) -> set:
    """A tool-name list from a server's settings: a JSON list, or one comma-separated text."""
    if isinstance(v, str):
        v = v.split(",")
    return {str(x).strip() for x in v or [] if str(x).strip()} if isinstance(v, (list, tuple, set, str)) else set()


def filter_tools(spec: dict, tools: list[dict]) -> list[dict]:
    """The tools of one server the model may see. A server's settings can name them (Codex's names for it):
    "enabled_tools": only these; "disabled_tools": all but these. A server with 40 tools filled the context and the
    model picked the wrong one; a user could only switch the whole server off (anthropics/claude-code#7328)."""
    on, off = _tool_list(spec.get("enabled_tools")), _tool_list(spec.get("disabled_tools"))
    return [t for t in tools if (not on or t.get("name") in on) and t.get("name") not in off]


def coerce_args(schema: dict | None, args: dict) -> dict:
    """Arguments the model sent as text where the tool's input schema wants a number, true/false or a list ("10",
    "true", '["a"]'), in the declared type; everything else as it was. Many servers validate strictly and refuse the
    call, which cost a turn (open-webui/open-webui#25641). Only top-level properties with one plain type change."""
    props = (schema or {}).get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict) or not isinstance(args, dict):
        return args
    from meshpool_harness.core.tools import coerce_arg
    out = dict(args)
    for k, v in args.items():
        t = (props.get(k) or {}).get("type") if isinstance(props.get(k), dict) else None
        if isinstance(t, list):                          # ["integer", "null"]: the one type besides null
            rest = [x for x in t if x != "null"]
            t = rest[0] if len(rest) == 1 else None
        if t in ("integer", "number", "boolean", "array") and isinstance(v, str):
            out[k] = coerce_arg(v, t)
    return out


class Server:
    def __init__(self, name: str, spec: dict, log_dir: Path, roots: list[str]):
        # this chat's own folder, not whatever the process-wide variable holds when several chats start together
        self.name, self.spec = name, _expand(spec, {"MESHPOOL_PROJECT": str(Path(roots[0]).resolve())} if roots else None)
        self.status, self.error, self.tools, self.info, self.caps = "starting", "", [], {}, {}
        self._id = 0
        self._lock = threading.Lock()
        self._conn = threading.RLock()         # one reconnect or token refresh at a time
        self._gen = 0                          # bumped on each reconnect, so racing callers reconnect once
        self.cancelled = self.closed = False
        self.t = None
        self.images: list[dict] = []           # images from the latest tool call
        self.log_dir, self.roots = log_dir, roots
        self._entry: _Entry | None = None      # the shared process of a stdio server (see the pool above)
        self._key = ""
        self.get_model = None                  # () -> the chat's model; sent with calls of a per-call-project server
        self._inflight: dict[int, object] = {}   # request id -> transport, for Stop (notifications/cancelled)
        self.timed_out = False                 # the start only ran out of time: not a verdict on the server

    def _stateful(self) -> bool:
        return any(w in self.name.lower() for w in _STATEFUL)

    def _poolable(self) -> bool:
        if not (bool(self.spec.get("command")) and not self.spec.get("url") and self.spec.get("shared", True) is not False
                and os.environ.get("MH_MCP_POOL", "1") != "0"):
            return False
        env = self.spec.get("env")                 # a stateful server only with a chat of its own (its own pool key)
        return not self._stateful() or (isinstance(env, dict) and bool(env.get("MH_CHAT_ID")))

    def _join_pool(self):
        key = _pool_key(self.spec, self.roots)
        with _PLOCK:
            e = _POOL.setdefault(key, _Entry())
        with e.lock:
            e.refs.add(id(self))
            e.keep = e.keep or self._stateful()
        self._entry, self._key = e, key
        _ensure_reaper()

    def _acquire(self, timeout: float):
        """The shared process, started and initialized when it is not running (first use, or after an idle stop)."""
        e = self._entry
        with e.lock:
            if self.closed:                        # close() came first: do not start a process nobody will stop
                raise MCPError("closed")
            if e.t is not None and not e.t.alive:
                e.stop()
            if e.t is None:
                e.t = _Stdio(self.name, self.spec, self.log_dir, self.roots)
                e.ready = False
            t = e.t
        if not e.ready:
            # initialize waits for the server, so it runs without e.lock: close() must be able to stop the process
            # (which ends this wait) instead of queueing behind a slow start
            with e.init_lock:
                if e.t is t and not e.ready:
                    rid = e.next_id()
                    msg = t.request(rid, "initialize", self._init_params(), min(timeout, self._init_budget()))
                    if "error" in msg:
                        er = msg["error"]
                        with e.lock:
                            if e.t is t:
                                e.stop()
                        raise MCPError(f"{er.get('message', 'error')} (code {er.get('code')})")
                    e.info = (msg.get("result") or {}).get("serverInfo") or {}
                    e.caps = (msg.get("result") or {}).get("capabilities") or {}   # resources, prompts
                    t.notify("notifications/initialized")
                    e.ready = True
        with e.lock:
            if self.closed or e.t is not t or not e.ready:
                raise MCPError("closed" if self.closed else t._exit_reason())
            self.t, self.info = e.t, e.info
            self.caps = getattr(e, "caps", None) or {}
            e.last = time.time()

    def _rpc(self, method, params, timeout):
        retried = set()
        e = self._entry
        if e is not None:
            with e.lock:
                e.inflight += 1                   # before the start: the idle reaper must not stop a process that is still
        try:                                      # starting for this very call (a slow start looked idle: its last use was old)
            if e is not None:
                self._acquire(timeout)
            return self._rpc_inner(method, params, timeout, retried, e)
        finally:
            if e is not None:
                with e.lock:
                    e.inflight -= 1
                    e.last = time.time()

    def _rpc_inner(self, method, params, timeout, retried, e):
        while True:
            with self._lock:
                self._id += 1
                rid = e.next_id() if e is not None else self._id
                gen = self._gen
            t = self.t
            if isinstance(t, _Stdio) and e is None and not t.alive and method != "initialize" and "dead" not in retried:
                retried.add("dead")                # a private server that died while idle: start it once more
                self._restart_stdio(gen, timeout)
                continue
            try:
                self._inflight[rid] = t
                try:
                    msg = t.request(rid, method, params, timeout)
                finally:
                    self._inflight.pop(rid, None)
                break
            except _NotSent:
                if "dead" in retried or self.closed or method == "initialize" or not isinstance(t, _Stdio):
                    raise
                retried.add("dead")                # the request never reached the server: restart it, send again
                if e is not None:
                    with e.lock:
                        if e.t is t:
                            e.stop()
                    self._acquire(timeout)
                else:
                    self._restart_stdio(gen, timeout)
            except _AuthRefused:
                if "auth" in retried or self.closed or not self._refresh_auth():
                    raise
                retried.add("auth")
            except _SessionLost:
                if "session" in retried or self.closed or method == "initialize":
                    raise
                retried.add("session")
                self._reconnect(gen, timeout)
        if "error" in msg:
            e = msg["error"]
            raise MCPError(f"{e.get('message', 'error')} (code {e.get('code')})")
        return msg.get("result") or {}

    def _restart_stdio(self, gen: int, timeout: float):
        with self._conn:
            if self._gen != gen or self.closed:
                return
            old = self.t
            try:
                if old is not None:
                    old.close()
            except Exception:  # noqa: BLE001
                pass
            self.t = _Stdio(self.name, self.spec, self.log_dir, self.roots)
            self._handshake(min(timeout, self._init_budget()))
            self._gen += 1

    def abort_calls(self, reason: str = "cancelled by the user") -> int:
        """Stop pressed: tell the server to drop every call in flight (notifications/cancelled with its request id)
        and stop waiting for it. Returns how many calls were cancelled."""
        n = 0
        for rid, t in list(self._inflight.items()):
            fn = getattr(t, "cancel_request", None)
            if fn:
                fn(rid, reason)
                n += 1
        return n

    def _init_budget(self) -> float:
        return first_response_seconds(self.spec)

    def _init_params(self) -> dict:
        return {"protocolVersion": PROTOCOL, "clientInfo": CLIENT, "capabilities": {"roots": {"listChanged": False}}}

    def _handshake(self, timeout):
        res = self._rpc("initialize", self._init_params(), timeout)
        self.info = res.get("serverInfo") or {}
        self.caps = res.get("capabilities") or {}
        if isinstance(self.t, _Http):
            self.t.version = res.get("protocolVersion") or PROTOCOL
        self.t.notify("notifications/initialized")

    def _reconnect(self, gen: int, timeout: float):
        """The server dropped the session (HTTP 404) or the legacy event stream closed: start a new session once."""
        with self._conn:
            if self._gen != gen:               # another call already reconnected
                return
            if isinstance(self.t, _Http):
                self.t.session = self.t.version = None
            elif isinstance(self.t, _Sse):
                self.t.close()
                self.t = _Sse(self.name, self.spec, min(timeout, 30))
            self._handshake(min(timeout, 60))
            self._gen += 1

    def _refresh_auth(self) -> bool:
        """A connector's token was refused (it expires after about an hour): fetch a fresh one and use it."""
        if not self.spec.get("connector"):
            return False
        with self._conn:
            try:
                from meshpool_harness import mcp_apps as CN
                headers = CN.refresh_headers(self.name, getattr(self.t, "headers", None))
            except Exception:  # noqa: BLE001
                return False
            if not headers or headers == getattr(self.t, "headers", None):
                return False
            self.t.headers = {k: str(v) for k, v in headers.items()}
            self.spec["headers"] = dict(self.t.headers)
            return True

    def start(self, timeout: float = 90):
        try:
            if self.closed:
                raise MCPError("closed")
            if self.spec.get("url") and _sse_spec(self.spec):
                self.t = _Sse(self.name, self.spec, min(timeout, 30))
                self._handshake(timeout)
            elif self.spec.get("url"):
                self.t = _Http(self.name, self.spec)
                try:
                    self._handshake(timeout)
                except MCPError as e:
                    # An older server: POST is refused, so try the HTTP+SSE transport at the same address.
                    if not re.match(r"HTTP (400|404|405)\b", str(e)):
                        raise
                    try:
                        self.t = _Sse(self.name, self.spec, min(timeout, 30))
                        self._handshake(timeout)
                    except MCPError:
                        raise e from None              # not an SSE server either: report what the POST said
            elif self.spec.get("command"):
                if self._poolable():
                    if self._entry is None:
                        self._join_pool()
                    self._acquire(timeout)
                else:
                    self.t = _Stdio(self.name, self.spec, self.log_dir, self.roots)
                    if self.closed:                    # close() ran while the process started
                        raise MCPError("closed")
                    self._handshake(min(timeout, self._init_budget()))
            else:
                raise MCPError("needs either a command or a url")
            if self.closed:
                raise MCPError("closed")
            tools, cursor = [], None
            for _ in range(20):
                r = self._rpc("tools/list", {"cursor": cursor} if cursor else {}, 60)
                tools += r.get("tools") or []
                cursor = r.get("nextCursor")
                if not cursor:
                    break
            with self._lock:
                if self.cancelled:                 # the caller gave up waiting: stay an error, do not appear late
                    raise MCPError(self.error or "cancelled")
                self.tools = filter_tools(self.spec, [t for t in tools if isinstance(t, dict)
                                                     and isinstance(t.get("name"), str)])
                if self.spec.get("read_only"):     # Settings, Connectors: read only
                    self.tools = read_only_tools(self.tools)
                self.status = "connected"
            if self.spec.get("command") and self.tools:   # a private one too: a chat's browser then starts lazily
                save_tools(self.name, self.spec, self.tools, self.info)
        except Exception as e:
            with self._lock:
                if not self.cancelled:
                    self.status, self.error = "error", redact(e)[:400]
                    self.timed_out = bool(_TIMEOUT_ERR.search(str(e)))
            self.close()

    def start_lazy(self) -> bool:
        """Connected from the saved tool list, without starting the process: it starts on the first tool call.
        False when this server is not shared, or its tool list was never saved (it then starts as usual)."""
        if not self._poolable():
            return False
        hit = cached_tools(self.name, self.spec)
        if not hit:
            return False
        self._join_pool()
        self.tools = filter_tools(self.spec, [t for t in hit[0] if isinstance(t, dict)
                                              and isinstance(t.get("name"), str)])
        self.info = hit[1]
        self.status = "connected"
        return True

    def cancel(self, reason: str):
        """Give up on a server whose start is taking too long. Its start thread may still finish later; it then
        closes the connection instead of marking the server connected."""
        with self._lock:
            if self.status == "connected":
                return
            self.cancelled = True
            self.timed_out = True
            self.status, self.error = "error", reason
        self.close()

    def call(self, tool: str, args: dict) -> tuple[str, bool]:
        out, self.images = self.call_with_images(tool, args)
        return out

    def call_with_images(self, tool: str, args: dict, project: str | None = None):
        """((text, failed), images) for this call alone: sub-agents call one server from several threads. ``project``:
        the folder this call works in when it is not the chat's own (a sub-agent in an isolated copy); a server that
        takes the folder with each call (per_call_project, the office server) is given it, so its files go there."""
        takes = self.spec.get("per_call_project") or (project and any(
            "office_mcp_server" in str(a) for a in self.spec.get("args") or []))
        if takes and (project or self.roots):
            args = {**args, "_project": str(Path(project or self.roots[0]).resolve())}
            try:
                model = self.get_model() if self.get_model else ""
            except Exception:  # noqa: BLE001
                model = ""
            if model:
                args["_model"] = str(model)                  # the chat's model, for engines that call one
            if self.spec.get("chat_id"):
                args["_chat"] = str(self.spec["chat_id"])    # the chat it serves (one process serves every chat)
        schema = next((t.get("inputSchema") for t in (getattr(self, "tools", None) or [])
                       if isinstance(t, dict) and t.get("name") == tool), None)   # a server not listed yet: no schema
        args = coerce_args(schema, args)
        r = self._rpc("tools/call", {"name": tool, "arguments": args}, float(self.spec.get("timeout", 120)))
        images: list[dict] = []
        parts = []
        for c in r.get("content") or []:
            if not isinstance(c, dict):
                continue
            t = c.get("type")
            if t == "text":
                parts.append(c.get("text", ""))
            elif t == "resource":
                res = c.get("resource") or {}
                parts.append(res.get("text") or f"[resource {res.get('uri', '')}]")
            elif t == "resource_link":
                parts.append(f"[link {c.get('name', '')}: {c.get('uri', '')}]")
            elif t == "image" and c.get("data"):
                images.append({"mime": c.get("mimeType") or "image/png", "data": c["data"]})
                parts.append("[image attached]")
            elif t in ("image", "audio"):
                parts.append(f"[{t} returned, {c.get('mimeType', '')}; not shown to the model]")
        if not parts and r.get("structuredContent") is not None:
            parts.append(json.dumps(r["structuredContent"], indent=1))
        return ("\n".join(parts) or "(no content)", bool(r.get("isError"))), images

    def close(self):
        self.closed = True
        e = self._entry
        if e is not None:                          # shared: the process stops when the last chat lets go of it
            with e.lock:
                e.refs.discard(id(self))
                last = not e.refs
            if last:
                with _PLOCK:
                    if _POOL.get(self._key) is e:
                        del _POOL[self._key]
                e.stop()
            self.t = None
            return
        if self.t:
            try:
                self.t.close()
            except Exception:
                pass


# ---------- all servers --------------------------------------------------------------------------------------------

class Manager:
    """Starts the configured servers in parallel and exposes their tools as OpenAI function tools."""

    def __init__(self, roots: list[str] | None = None, path: Path | None = None, extra: dict | None = None,
                 chat: str = ""):
        self.chat = chat                               # the chat these servers belong to (the browser's live view)
        self.extra = extra or {}                       # servers supplied by an ACP client for this session
        self.path = path or config_file()
        self.roots = roots or [os.getcwd()]
        self.servers: dict[str, Server] = {}
        self.disabled: list[str] = []
        self.config_error = ""
        self.map: dict[str, tuple[str, str]] = {}
        self._names: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self.last_images: list[dict] = []
        self.model = None                              # str or () -> str: the chat's model (set by the agent)
        self.closed = False
        self._threads: list = []
        self._sig = None

    def start(self, timeout: float = 90, use_health: bool = True, lazy: bool | None = None) -> "Manager":
        """lazy (default: when use_health is on, that is for real chats; off for a server test): a shared stdio server
        whose tool list is saved is connected without starting its process."""
        lazy = use_health if lazy is None else lazy
        try:
            cfg = active_config(self.path)
        except (ValueError, OSError) as e:
            self.config_error = f"{self.path.name} is not valid JSON: {e}"
            cfg = {}
        cfg = {**project_servers(self.roots[0]), **cfg, **self.extra}
        if os.environ.get("MH_STRICT_MCP") == "1":      # --strict-mcp-config: this run's servers and meshpool's own
            cfg = dict(self.extra)
        log_dir = C.runs_dir() / "mcp-logs"
        raise_file_limit()
        os.environ["MESHPOOL_PROJECT"] = str(Path(self.roots[0]).resolve())   # usable as ${MESHPOOL_PROJECT}
        threads = []
        for name, spec in cfg.items():
            if self.closed:
                break
            if not isinstance(spec, dict) or spec.get("disabled"):
                self.disabled.append(name)
                continue
            s = Server(name, self._with_chat(spec), log_dir, self.roots)
            s.get_model = lambda: self.model() if callable(self.model) else self.model
            self.servers[name] = s
            bad = recently_failed(name, spec) if use_health else None
            if bad is not None:                       # failed a moment ago: do not make every thread wait for it
                s.status, s.error = "error", f"skipped, it failed recently ({bad}). Check it in Settings, MCP servers."
                continue
            if lazy and s.start_lazy():
                continue
            # a cold npx/uvx start may be downloading: it keeps starting in the background with its own, longer budget
            # (the chat does not wait for it) instead of blocking the chat or being cached as broken
            th = threading.Thread(target=s.start, args=(max(timeout, first_response_seconds(spec))
                                                        if is_cold_start(spec) else timeout,), daemon=True)
            th.start()
            threads.append((s, th, is_cold_start(spec)))
        self._threads = [th for _, th, _ in threads]
        t0 = time.time()
        for s, th, cold in threads:
            # a cold start is waited for only as long as any server's first reply (25 s); the others get the full budget
            end = t0 + (min(timeout + 5, first_response_seconds({"command": "x"})) if cold else timeout + 5)
            th.join(max(0.0, end - time.time()))
            if th.is_alive():
                if cold and not self.closed:
                    s.status, s.error = "starting", "still starting: the first start downloads the server"
                else:
                    s.cancel(f"did not start within {int(timeout)} s")
        record_health({n: (cfg.get(n), s.status == "connected", s.error) for n, s in list(self.servers.items())
                       if not str(s.error).startswith("skipped") and s.status != "starting"})
        self._rebuild()
        return self

    def _with_chat(self, spec: dict) -> dict:
        """The browser server learns which chat it serves, so the app can find its live view; the assistant server
        learns it to schedule tasks in the chat's own mode; the office server, so its builds are counted per chat.
        The office and assistant servers get it with each call (_chat, as they get the folder): an id in their
        environment made one process per chat, each started when the chat opened."""
        args = [str(a) for a in spec.get("args") or []]
        if not self.chat:
            return spec
        if any("browser_mcp_server" in a for a in args):
            return {**spec, "env": {**(spec.get("env") or {}), "MH_CHAT_ID": self.chat}}
        if any(x in a for a in args for x in ("assistant_mcp_server", "office_mcp_server")):
            return {**spec, "per_call_project": True, "chat_id": self.chat}
        return spec

    def _rebuild(self):
        """Tool name -> (server, tool) for every connected server. schemas() reads the same table, so the model is
        never offered a tool that call() does not know."""
        with self._lock:
            pairs = [(s.name, t["name"]) for s in list(self.servers.values()) if s.status == "connected" for t in s.tools]
            self._sig = tuple(pairs)
            names = unique_tool_names(pairs)
            self._names = names
            self.map = {n: pair for pair, n in names.items()}

    def add(self, name: str, spec: dict, timeout: float = 60) -> "Server":
        """Start one more server in a running session (after /connect) and make its tools available at once."""
        old = self.servers.pop(name, None)
        if old:
            old.close()
        s = Server(name, self._with_chat(spec), C.runs_dir() / "mcp-logs", self.roots)
        s.start(timeout)
        self.servers[name] = s
        self._rebuild()
        return s

    def builtin_tool_names(self, module: str) -> set:
        """The model-facing names of the tools served by one of Meshpool's own servers, found by the module it runs
        (browser_mcp_server, office_mcp_server, ...), not by the server's name, which a user's server may share."""
        with self._lock:
            names = dict(self._names)
        return {fn for s in list(self.servers.values())
                if any(module in str(a) for a in (getattr(s, "spec", {}) or {}).get("args") or [])
                for t in s.tools for fn in [names.get((s.name, t["name"]))] if fn}

    def schemas(self) -> list[dict]:
        out = []
        if self._sig != tuple((s.name, t["name"]) for s in list(self.servers.values())
                              if s.status == "connected" for t in s.tools):
            self._rebuild()                    # a server that was still starting has connected since
        with self._lock:
            names = dict(self._names)
        for s in list(self.servers.values()):
            for t in s.tools:
                fname = names.get((s.name, t["name"]))
                if fname is None:              # not connected, or connected after the table was built
                    continue
                params = dict(t.get("inputSchema") or {"type": "object", "properties": {}})
                params.pop("$schema", None)
                params.setdefault("type", "object")
                params.setdefault("properties", {})
                desc = (t.get("description") or t.get("title") or t["name"]).strip()
                out.append({"type": "function", "function": {
                    "name": fname, "description": f"[MCP server '{s.name}'] {desc}"[:1024], "parameters": params}})
        return out + self._resource_schemas()

    def has(self, name: str) -> bool:
        return name in self.map or (name in (RES_LIST, RES_READ) and bool(self._resource_servers()))

    # ---- prompts (ready-made requests a server offers, used as /mcp__server__prompt) ------------------------------
    def prompts(self) -> list[dict]:
        """[{"command", "server", "name", "description", "arguments"}] for every connected server that offers prompts."""
        out = []
        for s in list(self.servers.values()):
            if s.status != "connected" or "prompts" not in (getattr(s, "caps", None) or {}):
                continue
            try:
                r = s._rpc("prompts/list", {}, 30)
            except MCPError:
                continue
            for p in (r.get("prompts") or [])[:100]:
                if isinstance(p, dict) and isinstance(p.get("name"), str):
                    out.append({"command": "/" + tool_name(s.name, p["name"]), "server": s.name, "name": p["name"],
                                "description": str(p.get("description") or "")[:300],
                                "arguments": [a for a in (p.get("arguments") or []) if isinstance(a, dict)]})
        return out

    def expand_prompt(self, text: str) -> str | None:
        """The message for "/mcp__server__prompt words...": the server's prompt filled in, None when text is not one.
        The words fill the prompt's arguments in order; the last one takes the rest. Raises ValueError for a prompt
        that needs more arguments than were given."""
        m = re.match(r"^/(mcp__[A-Za-z0-9_-]+)(?:\s+(.*))?$", text.strip(), re.S)
        if not m:
            return None
        hit = next((p for p in self.prompts() if p["command"] == "/" + m.group(1)), None)
        if not hit:
            return None
        import shlex
        rest = (m.group(2) or "").strip()
        try:
            words = shlex.split(rest)
        except ValueError:
            words = rest.split()
        args, specs = {}, hit["arguments"]
        for i, a in enumerate(specs):
            if i < len(words):
                args[a["name"]] = " ".join(words[i:]) if i == len(specs) - 1 else words[i]
        missing = [a["name"] for a in specs if a.get("required") and a["name"] not in args]
        if missing:
            raise ValueError(f"{hit['command']} needs: {', '.join(missing)}")
        try:
            r = self.servers[hit["server"]]._rpc("prompts/get", {"name": hit["name"], "arguments": args}, 60)
        except MCPError as e:
            raise ValueError(f"Could not get the prompt {hit['name']} from {hit['server']}: {redact(e)}")
        parts = []
        for msg in r.get("messages") or []:
            c = (msg or {}).get("content")
            for item in (c if isinstance(c, list) else [c]):
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item, dict) and item.get("type") == "resource":
                    res = item.get("resource") or {}
                    if res.get("text"):
                        parts.append(f"[{res.get('uri', '')}]\n{res['text']}")
        return "\n\n".join(p for p in parts if p.strip()) or None

    def annotations(self, name: str) -> dict:
        """The server's own hints for one tool (readOnlyHint, destructiveHint, ...), {} when it gives none."""
        pair = self.map.get(name)
        s = self.servers.get(pair[0]) if pair else None
        if not s:
            return {}
        t = next((t for t in s.tools if t.get("name") == pair[1]), None)
        ann = (t or {}).get("annotations")
        return ann if isinstance(ann, dict) else {}

    # ---- resources (files, records, docs a server offers to read) ------------------------------------------------
    def _resource_servers(self) -> list:
        return [s for s in list(self.servers.values()) if s.status == "connected" and "resources" in (getattr(s, "caps", None) or {})]

    def _resource_schemas(self) -> list[dict]:
        if not self._resource_servers():
            return []
        names = ", ".join(s.name for s in self._resource_servers())
        return [{"type": "function", "function": {"name": RES_LIST, "description": (
                    f"List the resources (files, records, documents) the connected MCP servers offer to read: {names}. "
                    "Then read one with " + RES_READ + "."), "parameters": {"type": "object", "properties": {
                        "server": {"type": "string", "description": "Only this server (optional)"}}}}},
                {"type": "function", "function": {"name": RES_READ, "description": "Read one MCP resource by its "
                    "server and URI (from " + RES_LIST + ").", "parameters": {"type": "object", "properties": {
                        "server": {"type": "string"}, "uri": {"type": "string"}}, "required": ["server", "uri"]}}}]

    def _resources(self, args: dict) -> tuple[str, bool]:
        want = str(args.get("server") or "")
        lines = []
        for s in self._resource_servers():
            if want and s.name != want:
                continue
            cursor, n = None, 0
            try:
                for _ in range(5):
                    r = s._rpc("resources/list", {"cursor": cursor} if cursor else {}, 60)
                    for x in r.get("resources") or []:
                        n += 1
                        if n <= 200:
                            lines.append(f"{s.name}: {x.get('uri', '')}  {x.get('name') or ''}"
                                         + (f" ({x.get('mimeType')})" if x.get("mimeType") else "")
                                         + (f" - {str(x.get('description'))[:120]}" if x.get("description") else ""))
                    cursor = r.get("nextCursor")
                    if not cursor:
                        break
            except MCPError as e:
                lines.append(f"{s.name}: could not list resources ({redact(e)})")
            if n > 200:
                lines.append(f"{s.name}: and {n - 200} more")
        return ("\n".join(lines) or "No resources." if lines or not want else f"No server named {want} offers resources."), False

    def _read_resource(self, args: dict) -> tuple[str, bool]:
        s = self.servers.get(str(args.get("server") or ""))
        if s is None or s not in self._resource_servers():
            return f"No connected server named {args.get('server')!r} offers resources.", True
        try:
            r = s._rpc("resources/read", {"uri": str(args.get("uri") or "")}, float(s.spec.get("timeout", 120)))
        except MCPError as e:
            return f"MCP error from '{s.name}': {redact(e)}", True
        parts = []
        for c in r.get("contents") or []:
            if c.get("text") is not None:
                parts.append(str(c["text"]))
            elif c.get("blob"):
                parts.append(f"[{c.get('mimeType') or 'binary'} content, {len(c['blob']) * 3 // 4} bytes, not shown]")
        return ("\n\n".join(parts) or "(empty)")[:200000], False

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """Run a tool. Images it returned are left in self.last_images for the agent to show the model."""
        out, self.last_images = self.call_with_images(name, args)
        return out

    def call_with_images(self, name: str, args: dict, project: str | None = None):
        """call() without the shared last_images: ((text, failed), images), safe from several threads. ``project``:
        see Server.call_with_images."""
        if name == RES_LIST and name not in self.map:
            return self._resources(args), []
        if name == RES_READ and name not in self.map:
            return self._read_resource(args), []
        sname, tool = self.map[name]
        s = self.servers[sname]
        if s.status != "connected":
            return (f"MCP server '{sname}' is not connected: {s.error}", True), []
        try:
            return s.call_with_images(tool, args, project=project) if project else s.call_with_images(tool, args)
        except Cancelled as e:
            return (f"Stopped: '{sname}' was told to cancel this call ({redact(e)}). It may have finished part of the "
                    "work: a file it was already writing can still appear, so check before running it again.", True), []
        except MCPError as e:
            return (f"MCP error from '{sname}': {redact(e)}", True), []

    def status(self) -> list[dict]:
        rows = [{"name": s.name, "status": s.status, "tools": [t["name"] for t in s.tools], "error": s.error,
                 "transport": ("sse" if isinstance(s.t, _Sse) else "http") if s.spec.get("url") else "stdio",
                 "server": (s.info.get("name") or "") + (" " + s.info.get("version", "") if s.info.get("version") else "")}
                for s in self.servers.values()]
        rows += [{"name": n, "status": "disabled", "tools": [], "error": "", "transport": "", "server": ""}
                 for n in self.disabled]
        return rows

    def abort_calls(self, reason: str = "cancelled by the user") -> int:
        """Stop: tell every server to drop the calls in flight."""
        return sum(s.abort_calls(reason) for s in list(self.servers.values()))

    def close(self):
        """Close every server, including ones whose start has not returned yet: they stop what they started."""
        self.closed = True
        for s in list(self.servers.values()):
            s.close()
        for th in list(self._threads):
            if th is not threading.current_thread():
                th.join(5)
        for s in list(self.servers.values()):      # a start that finished while we waited
            s.close()
