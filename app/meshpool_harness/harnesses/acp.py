"""ACP client: runs any Agent Client Protocol agent (Hermes, Goose, the Codex adapter, ...)
as a Meshpool harness. Standard library only.

The agent is a child process speaking newline-delimited JSON-RPC 2.0 on stdin/stdout. This class presents the same
surface as the built-in Agent (send / stop / reset / save / usage / title / mode), and turns ACP session updates into
Meshpool UI events:

  agent_message_chunk -> text_delta, then one assistant event per turn
  agent_thought_chunk -> thought
  tool_call / tool_call_update -> tool_start / tool_end
  plan -> plan
  usage_update -> context
  session/request_permission -> approval (the agent's own options become the buttons)
"""
from __future__ import annotations

import contextlib
import json
import re
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from meshpool_harness import __version__ as _VERSION
from meshpool_harness import config as C
from meshpool_harness import session_store as SS
from meshpool_harness.core import usage as USAGE

PROTOCOL_VERSION = 1



def _kill_group(proc) -> None:
    """Without psutil: end the process and its whole process group (POSIX) or tree (Windows taskkill /T)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15,
                           creationflags=0x08000000)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)      # started with start_new_session: its pid is the group id
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _kill_tree(proc) -> None:
    """End a harness process and everything it started (commands, MCP servers)."""
    if not proc:
        return
    try:
        import psutil
        root = psutil.Process(proc.pid)
        kids = root.children(recursive=True)
    except ImportError:
        _kill_group(proc)
        return
    except Exception:  # noqa: BLE001
        kids = []
    for k in kids:
        try:
            k.kill()
        except Exception:  # noqa: BLE001
            pass


def stop_file(chat: str) -> Path:
    """A chat's Stop stamp: Meshpool's MCP servers started for an outside harness read it (MESHPOOL_STOP_FILE), so Stop
    reaches their sub-agents even when the harness does not forward notifications/cancelled."""
    return C.runs_dir() / "sessions" / chat / "stop"


def stamp_stop(chat: str) -> None:
    if not chat:
        return
    try:
        from meshpool_harness import fsio
        fsio.atomic_write_text(stop_file(chat), repr(time.time()), fsync=False)
    except OSError:
        pass


class ACPError(Exception):
    pass


class ACPStopped(ACPError):
    pass


# Sara, round 3: "Codex sent nothing for 300 seconds, so this turn was ended. It restarts with your next message
# (MH_ACP_IDLE_S sets the limit)." Codex was waiting on model calls that never answered. The chat says it in plain
# words and offers Try again (the error event's "again").
STALL_TEXT = "The model stopped answering. Your files are as the last step left them."


class ACPStall(ACPError):
    """The harness sent nothing for MH_ACP_IDLE_S during a turn (it is usually waiting on a model call that never
    answers). The turn was ended and the process restarted."""


def _text_of(content) -> str:
    """Flatten ACP tool-call content into text for the UI."""
    out = []
    for c in content or []:
        t = c.get("type")
        if t == "content":
            inner = c.get("content") or {}
            if inner.get("type") == "text":
                out.append(inner.get("text", ""))
            elif inner.get("type") == "resource":
                out.append((inner.get("resource") or {}).get("text", ""))
            elif inner.get("type") == "resource_link":
                out.append(f"[{inner.get('name', '')}] {inner.get('uri', '')}")
        elif t == "diff":
            path = c.get("path", "")
            old = (c.get("oldText") or "").splitlines()
            new = (c.get("newText") or "").splitlines()
            import difflib
            d = list(difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", n=2, lineterm=""))
            out.append("\n".join(d[:200]) if d else f"(no change to {path})")
        elif t == "terminal":
            out.append(f"[terminal {c.get('terminalId', '')}]")
    return "\n".join(x for x in out if x)


PLAN_BLOCKED = {"edit", "execute", "delete", "move"}      # ACP tool kinds that change something
ACP_KIND = {"execute": "bash", "read": "read_file", "edit": "edit", "search": "grep", "fetch": "fetch_url",
            "delete": "delete", "move": "move"}


def _raw_text(raw) -> str:
    """Command output an agent reports outside the content list (Codex sends it here)."""
    if isinstance(raw, dict):
        for k in ("formatted_output", "aggregated_output", "output", "stdout", "result", "text"):
            if raw.get(k):
                return str(raw[k])
        return ""
    return str(raw or "")


def _command_of(raw) -> str:
    """The shell command in a permission request's rawInput (Codex: {"command": ["bash", "-lc", "..."]} or a
    string), or ""."""
    if not isinstance(raw, dict):
        return ""
    c = raw.get("command") if raw.get("command") is not None else raw.get("cmd")
    if isinstance(c, (list, tuple)):
        c = [str(x) for x in c]
        if len(c) >= 3 and os.path.basename(c[0]) in ("bash", "sh", "zsh") and c[1] in ("-c", "-lc", "-ic"):
            return c[2]
        return " ".join(c)
    return str(c or "")


def _label(u: dict) -> tuple[str, str]:
    """Tool label for the UI: MCP tools read as 'make_deck (meshpool office)'; others use the ACP kind."""
    title = str(u.get("title") or "")
    kind = u.get("kind") or "tool"
    if title.startswith("mcp__"):
        parts = title.split("__")
        return parts[-1], parts[1].replace("_", " ") if len(parts) > 2 else ""
    if title.startswith("mcp.") and title.count(".") >= 2:     # Codex: mcp.<server>.<tool>
        _, server, tool = title.split(".", 2)
        return tool, server.replace("_", " ").replace("-", " ")
    if kind in ACP_KIND:                                         # the same step names as the built-in agents
        return ACP_KIND[kind], re.sub(r"^(terminal|execute|bash)\s*:\s*", "", title)
    if kind in ("other", "tool") and title:
        head, _, rest = title.partition(":")
        return (head.strip(), rest.strip()) if rest else (title[:60], "")
    return kind, title


def _diff_of(content) -> str:
    return "\n".join(_text_of([c]) for c in content or [] if c.get("type") == "diff")


# What Meshpool's own servers need from this process's environment, named in each server's config because an
# outside agent may start its servers with a filtered environment: Hermes passes only PATH, HOME, USER, LANG, LC_ALL,
# TERM, SHELL and TMPDIR plus the server's own env. Without the proxy and CA settings the office server could not reach
# the model, so make_deck under Hermes returned an empty dossier in 2 s and never a deck (found 20 Nov 2026 with
# testkit/deck_matrix.py; reproduced with exactly Hermes' environment, and fixed by adding these back).
NET_ENV = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy", "no_proxy",
           "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
           "PLAYWRIGHT_BROWSERS_PATH")


def passthrough_env() -> dict:
    """The network settings and Meshpool's own settings (MH_*, MESHPOOL_*) of this process, for a Meshpool server
    that an outside agent starts."""
    return {k: v for k, v in os.environ.items()
            if v and (k in NET_ENV or k.startswith("MH_") or k.startswith("MESHPOOL_"))}


def mcp_servers_for_acp(roots: list[str], chat: str = "") -> list[dict]:
    """Meshpool's enabled MCP servers in ACP's session/new format, so every harness sees the same tools."""
    from meshpool_harness.agent import mcp as M
    os.environ["MESHPOOL_PROJECT"] = str(Path(roots[0]).resolve()) if roots else os.getcwd()
    try:
        cfg = M.active_config()
    except (ValueError, OSError):
        return []
    out = []
    for name, spec in cfg.items():
        if not isinstance(spec, dict) or spec.get("disabled"):
            continue
        if M.recently_failed(name, spec) is not None:
            continue                                 # a broken server would make the harness wait and retry it
        spec = M._expand(spec)
        if spec.get("url") and spec.get("connector") and spec.get("headers"):
            # A signed-in connector: through Meshpool's bridge, which refreshes the token (it lasts about an hour). A
            # harness given the header itself keeps it until it restarts and then gets 401 on every call.
            env = {**passthrough_env(), "PYTHONPATH": str(C.APP), "MESHPOOL_HOME": str(C.home()),
                   "MESHPOOL_CONFIG": str(C.config_path()), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            if not os.environ.get("PYTHONPYCACHEPREFIX"):
                env["PYTHONDONTWRITEBYTECODE"] = "1"
            out.append({"name": name, "command": sys.executable,
                        "args": ["-m", "meshpool_harness.agent.connector_bridge", name],
                        "env": [{"name": k, "value": str(v)} for k, v in env.items()]})
        elif spec.get("url"):
            out.append({"type": "http", "name": name, "url": spec["url"],
                        "headers": [{"name": k, "value": str(v)} for k, v in (spec.get("headers") or {}).items()]})
        elif spec.get("command"):
            cmd = spec["command"]
            env = dict(spec.get("env") or {})
            if cmd in ("python", "python3", "py"):
                cmd = sys.executable
                env = {**passthrough_env(), **env}         # its own env wins over this process's settings
                env["PYTHONPATH"] = str(C.APP)
                env.setdefault("MESHPOOL_HOME", str(C.home()))
                env.setdefault("MESHPOOL_CONFIG", str(C.config_path()))
                env.setdefault("MESHPOOL_PROJECT", os.environ["MESHPOOL_PROJECT"])
                env.setdefault("PYTHONUTF8", "1")
                if chat and any(k in str(x) for x in spec.get("args", [])
                                for k in ("browser_mcp_server", "assistant_mcp_server", "office_mcp_server")):
                    env["MH_CHAT_ID"] = chat                  # browser view; scheduled tasks keep the mode; office builds per chat
                if chat:
                    env["MESHPOOL_STOP_FILE"] = str(stop_file(chat))   # the chat's Stop reaches its sub-agents
                if os.environ.get("PYTHONPYCACHEPREFIX"):        # never write caches inside the signed app
                    env["PYTHONPYCACHEPREFIX"] = os.environ["PYTHONPYCACHEPREFIX"]
                else:
                    env["PYTHONDONTWRITEBYTECODE"] = "1"
            cmd = shutil.which(cmd) or cmd
            out.append({"name": name, "command": cmd, "args": [str(a) for a in spec.get("args", [])],
                        "env": [{"name": k, "value": str(v)} for k, v in env.items()]})
    return out


def office_server(roots: list[str], edit_only: bool = False) -> dict:
    """Meshpool Office MCP server (PowerPoint, Excel, Word, data analysis) for ACP harnesses."""
    env = {"PYTHONPATH": str(C.APP), "MESHPOOL_PROJECT": str(Path(roots[0]).resolve()) if roots else os.getcwd(),
           "MESHPOOL_HOME": str(C.home()), "MESHPOOL_CONFIG": str(C.config_path()), "PYTHONUTF8": "1",
           "PYTHONIOENCODING": "utf-8"}
    env = {**passthrough_env(), **env}                 # proxies, CA bundles, MH_* and MESHPOOL_* settings
    for k in ("MH_MODEL", "PYTHONPYCACHEPREFIX", "PATH"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    if edit_only:
        env["MH_OFFICE_TOOLS"] = "edit"                # read, edit, convert and check: no engines to build a file
    from meshpool_harness import providers as PV
    base, key, _src = PV.endpoint_pair()               # a key goes only with the address it was set for
    if key:
        env["MESHPOOL_API_KEY"], env["MESHPOOL_BASE_URL"] = key, base
    if not os.environ.get("PYTHONPYCACHEPREFIX"):
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return {"name": "meshpool-office", "command": sys.executable, "args": ["-m", "meshpool_harness.agent.office_mcp_server"],
            "env": [{"name": k, "value": v} for k, v in env.items()]}


def office_extra(workdir: str) -> dict:
    """The office server as a Manager `extra` entry for Meshpool Work (make_document, make_pdf, conversions, document
    reading; its own office_task covers slides, sheets and data only). One definition for the app and for
    `meshpool acp --profile work`, so they cannot drift. The engines may run 280 s; this client waits 330 s."""
    spec = office_server([workdir])
    env_ = {e["name"]: e["value"] for e in spec["env"]}
    env_.setdefault("OFFICE_WAIT_S", "280")
    # per_call_project: one office process for the whole app; each call carries its own chat's folder
    return {spec["name"]: {"command": spec["command"], "args": spec["args"], "env": env_, "timeout": 330,
                           "per_call_project": True}}


_OWN_RX = re.compile(r"meshpool_harness\.agent\.\w*_mcp_server")


def is_own_server(spec: dict) -> bool:
    """One of Meshpool's own stdio servers (browser, assistant, tools, web, office), as opposed to a server or
    connector the user added."""
    return any(_OWN_RX.search(str(a)) for a in spec.get("args") or [])


def lean_mcp(servers: list[dict], office: bool, root: str, allowed: bool = True, edit_only: bool = False) -> list[dict]:
    """The MCP servers an outside agent is given at session start. Each server's tool schemas ride in every request of
    the chat (Codex: 46 tools, 5.5k tokens), so Meshpool's own servers are passed only when the chat needs them: the
    office server for a chat that asks for an office file (`office`; `allowed` is the manifest's office switch), and
    none otherwise. Servers and connectors the user added always go. MH_ACP_MCP=all passes everything, as before."""
    every = os.environ.get("MH_ACP_MCP", "").lower() == "all"
    out = list(servers) if every else [s for s in servers if not is_own_server(s)]
    if allowed and (office or every):
        out.append(office_server([root], edit_only and not every))
    return out


def _auth_failure(text: str) -> bool:
    """A short reply that is only an authentication error from the model endpoint (HTTP 401/403)."""
    import re
    s = (text or "").strip()
    return len(s) < 400 and bool(re.search(r"\b40[13]\b", s)) and bool(
        re.search(r"(?i)api.?key|bad key|unauthori[sz]ed|authenticat|forbidden|invalid.{0,20}key", s))


def _engine_error(text: str) -> bool:
    """A short reply that is only the engine's own error for a failed model call (an HTTP 4xx or 5xx, nothing
    answering), not an answer from the model."""
    from meshpool_harness.core import errtext as ET
    s = (text or "").strip()
    return len(s) < 600 and ET.is_raw(s) and ET.engine_failed(s)


def before_turn(agent) -> list:
    """The chat's events before the current turn's user message: status lines emitted since (a model the harness
    kept, say) must not push that message into the history."""
    return agent.events[:getattr(agent, "_turn_at", len(agent.events))]


def earlier_context(events: list, limit: int = 40000) -> str:
    """The chat so far as plain text for a harness that starts a fresh session on an old chat: the latest turns
    first to fit `limit` characters, written oldest first. Hidden in the chat (after \u200b)."""
    rows = []
    for e in events:
        t = e.get("type")
        if t == "user" and e.get("text"):
            rows.append("User: " + e["text"].split("\u200b")[0].strip())
        elif t == "assistant" and e.get("text"):
            rows.append("You: " + e["text"].strip())
        elif t == "tool_start" and e.get("name"):
            rows.append(f"(you used {e['name']}" + (f": {str(e.get('title') or '')[:120]}" if e.get("title") else "")
                        + ")")
    out, size = [], 0
    for r in reversed(rows):
        r = r if len(r) <= 4000 else r[:2000] + " [...] " + r[-1500:]
        if size + len(r) > limit:
            out.append("[earlier messages left out]")
            break
        out.append(r)
        size += len(r) + 1
    if not out:
        return ""
    return ("\u200b[Meshpool] This chat started earlier, in a session you no longer have. The conversation so far, "
            "for context (do not redo finished work; continue from where it stopped):\n\n"
            + "\n".join(reversed(out)) + "\n\n[The user's new message follows.]\n\n")


class ACPAgent:
    """One ACP agent process serving one Meshpool session."""
    usage_task = None          # the session's usage task (core/usage.py): the harness's calls are recorded in it
    budget_stopped = False     # the last turn ended on the spending limit (agent_api pauses a goal on it)
    _usage_recorded = False        # set once the current prompt response is in the ledger (any code path)
    _cost_now = _cost_seen = 0.0   # session cost the harness reported (usage_update), and the part already recorded

    def __init__(self, manifest: dict, workdir: str, *, emit=None, approve=None, mode: str | None = None,
                 session_id: str | None = None):
        self.manifest = manifest
        self.root = Path(workdir).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Project folder not found: {workdir}")
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = C.runs_dir() / "sessions" / self.id
        self.emit_cb = emit or (lambda ev: None)
        self.approve = approve
        self.mode = C.normalize_mode(mode, None) or C.permission_mode()
        self.title = ""
        self.created = time.time()
        self.usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        self.context = None                    # {"used": n, "size": n} from usage_update
        self.events: list[dict] = []           # saved transcript (the agent keeps its own history)
        self.messages = [None]                 # Session.view() compatibility
        self.mcp = None
        self.acp_session = None
        self.loaded_from = None                # ACP session id to load on first send (resume)
        self.office_on = False                 # the chat asked for an office file: the office server goes with the session
        self.office_edit = False               # ... only to change a file the user has (a smaller set of office tools)
        self._session_office = False           # whether the live session was opened with the office server (and how)
        self.agent_info = {}
        self.capabilities = {}
        self.model: str | None = None          # chosen in the app; applied with session/set_model
        self.available_models = None           # [{"id", "name"}] the harness offers, from session/new
        self.plan = False
        self._p = None
        self._id = 0
        self._pending: dict[int, dict] = {}
        self._cv = threading.Condition()
        self._alive = False
        self._stop = False
        self._text = []
        self._replaying = False
        self._tools: dict[str, dict] = {}
        self._open_tools: dict[str, str] = {}          # started this turn, no tool_end yet: id -> label
        self._lock = threading.Lock()
        self._in_turn = False                  # Stop ends only requests made for a running turn

    # ---------- process + JSON-RPC ----------------------------------------------------------------------------
    def _emit(self, ev: dict):
        if self._replaying:
            return
        if ev.get("type") not in ("text_delta", "thought", "model_start", "model_end", "context"):
            self.events.append({k: v for k, v in ev.items() if k not in ("i", "t")})
        self.emit_cb(ev)

    def _start(self) -> bool:
        """Start the harness process unless it runs. True when a new process started (its sessions are gone)."""
        if self._p and self._p.poll() is None:
            return False
        m = self.manifest
        from meshpool_harness.harnesses import unsupported
        if m.get("builtin") and unsupported(m.get("id", "")):    # Codex, OpenCode: not part of the packaged app
            raise ACPError(unsupported(m["id"]))
        extra = {}                             # this chat's own provider settings: never a file another chat rewrites
        if m.get("prepare") == "hermes":
            from meshpool_harness.harnesses import prepare_hermes
            extra = prepare_hermes(self.model) or {}
        elif m.get("prepare") == "codex":
            from meshpool_harness.harnesses import prepare_codex
            extra = prepare_codex(self.model, self.id) or {}
        elif m.get("prepare") == "opencode":
            from meshpool_harness.harnesses import prepare_opencode
            extra = prepare_opencode(self.model, self.id) or {}
        cmd = m.get("command", "")
        exe = shutil.which(cmd) or (cmd if Path(cmd).exists() else None)
        if not exe:
            raise ACPError(f"{m.get('name', 'This harness')} is not installed (command '{cmd}' not found). "
                           + (m.get("install") or ""))
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (m.get("env") or {}).items()})
        env.update(extra)
        for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE", "PYTHONPYCACHEPREFIX"):
            if k not in (m.get("env") or {}):
                env.pop(k, None)               # never leak Meshpool's Python into another Python app
        for k in m.get("env_unset") or []:
            env.pop(k, None)                   # e.g. a subscription harness must not bill an API key instead
        for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
            if env.get(k) and not (Path(self.root) / env[k]).exists():   # as the harness sees it from its cwd
                env.pop(k)                     # a stale CA path breaks every HTTPS client in the harness
        log_dir = C.runs_dir() / "harness-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if getattr(self, "_err", None):
            try:
                self._err.close()              # the log of a process that exited
            except OSError:
                pass
        from meshpool_harness.logfiles import RotatingFile      # H-23: rotates by size while the harness runs
        self._err = RotatingFile(log_dir / f"{m.get('id', 'acp')}.log")
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
        self._p = subprocess.Popen([exe, *[str(a) for a in m.get("args", [])]], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(self.root), env=env, **kw)
        self._alive = True
        self._last_msg = time.time()
        threading.Thread(target=self._pump_stderr, args=(self._p, self._err), daemon=True).start()
        threading.Thread(target=self._reader, args=(self._p,), daemon=True).start()
        try:
            return self._handshake(m)
        except BaseException:                  # a failed or timed-out handshake must not leave a half-started process
            dead, self._p = self._p, None
            _kill_tree(dead)
            try:
                dead.kill()
            except Exception:  # noqa: BLE001
                pass
            raise

    def _handshake(self, m) -> bool:
        init = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": False,
                                   "_meta": {"terminal_output": True}},   # Codex then sends command output
            "clientInfo": {"name": "meshpool", "title": "Meshpool", "version": _VERSION}}, timeout=300)
        self.agent_info = init.get("agentInfo") or {}
        self.capabilities = init.get("agentCapabilities") or {}
        auth = m.get("auth")                   # a manifest's "auth": which of its own logins to use
        if auth and any(a.get("id") == auth.get("methodId") for a in init.get("authMethods") or []):
            self._request("authenticate", auth, timeout=120)
        return True

    def _pump_stderr(self, proc, log):
        """The harness's stderr into its log with secret-looking values removed (OSS-29); a Codex sandbox failure
        (OSS-02) is told to the user once, plainly."""
        told = False
        for raw in proc.stderr:
            line = C.redact_secrets(raw.decode("utf-8", "replace"))
            try:
                log.write(line)
                log.flush()
            except (OSError, ValueError):
                pass
            if not told and self.manifest.get("id") == "codex":
                from meshpool_harness.harnesses import codex_sandbox_failure
                note = codex_sandbox_failure(line)
                if note:
                    told = True
                    self.sandbox_note = note
                    self._emit({"type": "text_delta", "text": "\n" + note + "\n"})

    def _serve_counted(self, msg):
        self._serving = getattr(self, "_serving", 0) + 1          # a request waiting on the user is not idleness
        try:
            self._serve_request(msg)
        finally:
            self._serving -= 1
            self._last_msg = time.time()

    def _reader(self, proc):
        for raw in proc.stdout:
            self._last_msg = time.time()
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "method" in msg and "id" in msg:
                threading.Thread(target=self._serve_counted, args=(msg,), daemon=True).start()
            elif "method" in msg:
                try:
                    self._notification(msg)
                except Exception as e:  # a malformed update must not kill the reader
                    print(f"ACP update error: {e}", file=sys.stderr)
            elif "id" in msg:
                with self._cv:
                    self._pending[msg["id"]] = msg
                    self._cv.notify_all()
        with self._cv:
            if self._p is proc:                # not a newer process started after this one exited
                self._alive = False
            self._cv.notify_all()

    def _write(self, obj):
        try:
            with self._lock:
                self._p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
                self._p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError, AttributeError):
            raise ACPError(f"{self.manifest.get('name', 'The harness')} stopped. Its log is in runs/harness-logs.")

    def _request(self, method: str, params: dict, timeout: float | None = None) -> dict:
        with self._cv:
            self._id += 1
            rid = self._id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        end = time.time() + timeout if timeout else None
        with self._cv:
            while rid not in self._pending:
                if not self._alive:
                    raise ACPError(f"{self.manifest.get('name', 'The harness')} exited. "
                                   f"See runs/harness-logs/{self.manifest.get('id', 'acp')}.log")
                if end and time.time() > end:
                    raise ACPError(f"No reply to {method} within {int(timeout)} s.")
                if method == "session/prompt" and not getattr(self, "_serving", 0) and self._idle_s() and \
                        time.time() - getattr(self, "_last_msg", time.time()) > self._idle_s():
                    break                                   # OSS-09: nothing for MH_ACP_IDLE_S: the harness is hung
                if self._stop and self._in_turn and (method != "session/prompt" or time.time() - getattr(self, "_stop_t", 1e18) > 3):
                    # session/prompt: the harness ignored the cancel. Anything else (initialize, authenticate,
                    # session/new ...) is start-up, which no cancel reaches: give up now, not at its timeout.
                    break
                self._cv.wait(0.3)
            else:
                msg = self._pending.pop(rid)
                if "error" in msg:
                    e = msg["error"]
                    raise ACPError(f"{e.get('message', 'error')}" + (f": {e.get('data')}" if e.get("data") else ""))
                return msg.get("result") or {}
        self._hard_restart()
        if self._idle_s() and not self._stop and method == "session/prompt":
            raise ACPStall(STALL_TEXT)
        raise ACPStopped("stopped")

    @staticmethod
    def _idle_s() -> float:
        try:
            return float(os.environ.get("MH_ACP_IDLE_S") or C.read().get("MH_ACP_IDLE_S") or 300)
        except (TypeError, ValueError, OSError):
            return 300.0

    def _hard_restart(self):
        """End the harness process (and what it started); the next message restarts it and reloads the session."""
        sid = self.acp_session
        try:
            import psutil
            root = psutil.Process(self._p.pid)
            procs = root.children(recursive=True) + [root]
        except ImportError:
            procs = []
            if self._p:
                _kill_group(self._p)
        except Exception:  # noqa: BLE001
            procs = []
        for pr in procs:
            try:
                pr.kill()
            except Exception:  # noqa: BLE001
                pass
        if not procs and self._p:
            try:
                self._p.kill()
            except Exception:  # noqa: BLE001
                pass
        if sid:
            self.loaded_from, self.acp_session = sid, None

    def _respond(self, rid, result=None, error=None):
        self._write({"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})})

    # ---------- agent -> client ---------------------------------------------------------------------------------
    def _serve_request(self, msg):
        m, p, rid = msg.get("method"), msg.get("params") or {}, msg["id"]
        try:
            if m == "session/request_permission":
                return self._respond(rid, {"outcome": self._permission(p)})
            if m == "fs/read_text_file":
                path = Path(p["path"]).resolve()
                from meshpool_harness import config as C
                ups = (C.runs_dir() / "_uploads").resolve()
                if not any(path == r or r in path.parents for r in (self.root, ups)):
                    return self._respond(rid, error={"code": -32602, "message": "reads through Meshpool are limited "
                                                     "to the project folder and attached files"})
                from meshpool_harness.core.tools import read_src, shown
                lines = shown(read_src(path)[0]).splitlines(keepends=True)
                start = max(0, int(p.get("line") or 1) - 1)
                lim = p.get("limit")
                return self._respond(rid, {"content": "".join(lines[start:start + int(lim)] if lim else lines[start:])})
            if m == "fs/write_text_file":
                path = Path(p["path"]).resolve()
                if self.root not in path.parents and path != self.root:
                    return self._respond(rid, error={"code": -32602, "message": "writes are limited to the project folder"})
                if ".git" in path.relative_to(self.root).parts:
                    return self._respond(rid, error={"code": -32602, "message": "writes inside .git are not allowed"})
                path.parent.mkdir(parents=True, exist_ok=True)
                from meshpool_harness.core.tools import read_src, write_src
                eol, bom = "\n", False
                if path.is_file():                   # keep the file's own line ending and BOM
                    try:
                        _t, eol, bom = read_src(path)
                    except OSError:
                        pass
                write_src(path, p.get("content", ""), eol, bom)
                return self._respond(rid, None)
            self._respond(rid, error={"code": -32601, "message": f"method not supported: {m}"})
        except ACPError:
            pass
        except Exception as e:
            try:
                self._respond(rid, error={"code": -32603, "message": str(e)})
            except ACPError:
                pass

    def _permission(self, p: dict) -> dict:
        options = p.get("options") or []
        tc = p.get("toolCall") or {}
        allow = next((o for o in options if o.get("kind") == "allow_once"), None) or \
            next((o for o in options if str(o.get("kind", "")).startswith("allow")), None)
        reject = next((o for o in options if o.get("kind") == "reject_once"), None) or \
            next((o for o in options if str(o.get("kind", "")).startswith("reject")), None)
        if self.plan and (tc.get("kind") in PLAN_BLOCKED or _diff_of(tc.get("content"))):
            # Plan mode only investigates, as the built-in agents' plan tools do: no edits, commands or moves.
            return {"outcome": "selected", "optionId": reject["optionId"]} if reject else {"outcome": "cancelled"}
        raw = tc.get("rawInput")
        cmd = _command_of(raw) or str(tc.get("title") or "")
        from meshpool_harness.core import permrules as PR
        try:                                   # a command that would write over a file open in Office always asks
            held = PR.open_office_write(cmd, self.root, self.root) if tc.get("kind") == "execute" else ""
        except Exception:  # noqa: BLE001
            held = ""
        if self.mode == "auto" and allow and not held:
            return {"outcome": "selected", "optionId": allow["optionId"]}
        if self.mode == "edits" and allow and not held and (tc.get("kind") == "edit" or _diff_of(tc.get("content"))):
            return {"outcome": "selected", "optionId": allow["optionId"]}
        if not self.approve or self._stop:
            return {"outcome": "cancelled"}
        title = tc.get("title") or "The harness asks for permission"
        detail = _diff_of(tc.get("content")) or _text_of(tc.get("content")) or \
            (json.dumps(raw, indent=1, ensure_ascii=False)[:3000] if raw else "")
        from meshpool_harness import office_guard as OG
        plain = OG.plain_command_title(cmd) if tc.get("kind") == "execute" else ""
        if held:
            plain = f"Change {held}? It is open in Office right now"
        if plain:
            # Sara: the card's title was the raw soffice command and its body a JSON blob. Plain words on the card,
            # the exact command in the Details fold (the page splits the body at "Details:").
            lines = ["If Office saves its own copy later, this change is lost."] if held else \
                ["meshpool runs this command for the agent."]
            detail = "\n".join(lines) + "\n\nDetails:\n" + (cmd or detail)[:3000]
            title = plain
        kind = "edit" if (tc.get("kind") == "edit" or _diff_of(tc.get("content"))) else \
            ("bash" if tc.get("kind") == "execute" else "mcp")
        choice, _note = self.approve(kind, title, detail,
                                     [{"id": o["optionId"], "label": o.get("name") or o["optionId"],
                                       "kind": o.get("kind", "")} for o in options])
        if choice in (None, "", "no"):
            return {"outcome": "selected", "optionId": reject["optionId"]} if reject else {"outcome": "cancelled"}
        if choice == "yes" and allow:
            return {"outcome": "selected", "optionId": allow["optionId"]}
        if any(o["optionId"] == choice for o in options):
            return {"outcome": "selected", "optionId": choice}
        return {"outcome": "cancelled"}

    def _notification(self, msg):
        if msg.get("method") != "session/update":
            return
        u = (msg.get("params") or {}).get("update") or {}
        kind = u.get("sessionUpdate")
        if self._replaying and kind not in ("usage_update", "session_info_update"):
            return                                 # session/load replays old turns: already in the saved thread
        if kind == "agent_message_chunk":
            c = u.get("content") or {}
            if c.get("type") == "text" and c.get("text"):
                if re.match(r"\s*Warning: Model metadata for .* not found", c["text"]):
                    return                         # Codex's own start-up notice about a custom model, not a reply
                t = c["text"] if self._text else c["text"].lstrip()   # the first piece kept has text in it
                if not t:
                    return                         # blank lines before the reply starts (Hermes sends two)
                self._text.append(t)
                self._emit({"type": "text_delta", "text": t})
        elif kind == "agent_thought_chunk":
            c = u.get("content") or {}
            if c.get("type") == "text" and c.get("text"):
                self._emit({"type": "thought", "text": c["text"]})
        elif kind == "tool_call":
            self._turn_tools = getattr(self, "_turn_tools", 0) + 1
            self._flush_text(has_tools=True)
            tid = u.get("toolCallId") or uuid.uuid4().hex
            self._tools[tid] = u
            name, summary = _label(u)
            self._open_tools[tid] = name
            self._emit({"type": "tool_start", "id": tid, "name": name, "summary": summary,
                        "mcp": False, "diff": _diff_of(u.get("content"))})
            if u.get("status") in ("completed", "failed"):
                self._tool_end(tid, u)
        elif kind == "tool_call_update":
            tid = u.get("toolCallId")
            meta = u.get("_meta") or {}
            if tid in self._tools:
                t = self._tools[tid]
                if (meta.get("terminal_output") or {}).get("data"):
                    t["_term_out"] = t.get("_term_out", "") + str(meta["terminal_output"]["data"])
                if meta.get("terminal_exit"):
                    t["_term_exit"] = meta["terminal_exit"].get("exit_code")
                t.update({k: v for k, v in u.items() if v is not None and k != "_meta"})
            if u.get("status") in ("completed", "failed"):
                self._tool_end(tid, self._tools.get(tid, u))
        elif kind == "plan":
            self._emit({"type": "plan", "entries": [{"content": e.get("content", ""), "status": e.get("status", "pending")}
                                                    for e in u.get("entries") or []]})
        elif kind == "usage_update":
            self.context = {"used": u.get("used", 0), "size": u.get("size", 0)}
            c = u.get("cost")
            if isinstance(c, dict) and str(c.get("currency") or "USD").upper() == "USD":
                with contextlib.suppress(TypeError, ValueError):
                    self._cost_now = max(self._cost_now, float(c.get("amount") or 0))
            self._emit({"type": "context", **self.context})
        elif kind == "session_info_update" and u.get("title"):
            self.title = self.title or str(u["title"])[:80]

    def _close_open_tools(self):
        """The turn is over: a tool card that never got a final status must not stay 'running'."""
        for tid, name in list(self._open_tools.items()):
            self._open_tools.pop(tid, None)
            self._emit({"type": "tool_end", "id": tid, "name": name, "ok": False, "denied": False,
                        "output": "Not finished: the turn ended."})

    def _tool_end(self, tid, u):
        self._open_tools.pop(tid, None)
        out = _text_of(u.get("content"))
        if not out.strip() or re.fullmatch(r"\s*\[terminal [^\]]*\]\s*", out):
            out = u.get("_term_out") or _raw_text(u.get("rawOutput")) or ""
        if u.get("_term_exit") not in (None, 0):
            u = dict(u, status="failed")
        self._emit({"type": "tool_end", "id": tid, "name": _label(u)[0], "ok": u.get("status") != "failed",
                    "denied": False, "output": out[:6000]})

    def _flush_text(self, has_tools=False):
        if self._text:
            txt = re.sub(r"^\s*Warning: Model metadata for [^\n]*not found[^\n]*\n*", "", "".join(self._text))
            self._text = []
            self._emit({"type": "assistant", "text": txt, "streamed": True, "has_tools": has_tools})

    # ---------- office work meshpool does or guards itself (office_guard.py) -----------------------------------
    def _office_direct(self, text: str) -> str | None:
        """A message that only asks for a PDF of one office file in the folder: meshpool's own converter makes it, in
        one step (Codex's sandbox cannot run LibreOffice: it took 9 steps and a raw approval card for Sara). In Ask
        mode the card says "Convert Board_Update_Q3.pptx to PDF". Returns the reply, or None to let the engine work."""
        if self.plan or os.environ.get("MH_OFFICE_DIRECT_CONVERT", "1") == "0":
            return None
        from meshpool_harness import office_guard as OG
        try:
            req = OG.conversion_request(text, self.root)
        except Exception:  # noqa: BLE001 - never blocks a message
            req = None
        if req is None or not OG.can_convert():
            return None
        src, fmt = req
        title = OG.convert_title(src, fmt)
        dst_name = src.with_suffix("." + fmt).name
        if self.mode not in ("auto", "edits"):
            if not self.approve:
                return None
            body = (f"Makes {dst_name} from {src.name}, in the same folder.\n"
                    + (f"It replaces the {dst_name} that is there now. Undo can bring it back.\n"
                       if src.with_suffix("." + fmt).exists() else "")
                    + f"\nDetails:\nmeshpool's own office converter (LibreOffice), outside the command sandbox.\n"
                    f"Source: {src}")
            from meshpool_harness.core import permrules as PR
            choice, _note = PR.approve_with_scope(self.approve, "edit", title, body, "once")
            if choice in (None, "", "no") or self._stop:
                if self._stop:
                    raise ACPStopped("stopped")
                reply = f"I did not make {dst_name}: you chose not to convert {src.name}."
                self._text = [reply]
                self._flush_text()
                return reply
        tid = "convert-" + uuid.uuid4().hex[:8]
        self._turn_tools = getattr(self, "_turn_tools", 0) + 1
        self._emit({"type": "tool_start", "id": tid, "name": "convert_document", "summary": title, "mcp": False,
                    "diff": ""})
        dst, why = OG.convert(src, fmt)
        if dst is None:
            self._emit({"type": "tool_end", "id": tid, "name": "convert_document", "ok": False, "denied": False,
                        "output": why})
            self._turn_tools -= 1                        # the engine tries instead, told what happened
            return None
        n = OG.page_count(dst) if fmt == "pdf" else 0
        self._emit({"type": "tool_end", "id": tid, "name": "convert_document", "ok": True, "denied": False,
                    "output": f"Saved: {dst}" + (f" ({n} page{'s' if n != 1 else ''})" if n else "")})
        reply = (f"{dst.name} is ready, made from {src.name}" + (f", {n} page{'s' if n != 1 else ''}" if n else "")
                 + ". It is in the same folder, next to the original.")
        self._text = [reply]
        self._flush_text()
        return reply

    def _open_file_guard(self, text: str) -> str:
        """Before an outside engine works on a message that changes an office file open in Excel, Word or PowerPoint
        (its lock file is next to it): ask first, as meshpool's own agent does. The engine writes files itself, in its
        own sandbox, so this is the one moment meshpool can stop it. Returns the hidden note for the engine ("" when
        the user said go ahead, or nothing is open)."""
        if self.plan or self.mode == "auto" or not self.approve:
            return ""
        from meshpool_harness import office_guard as OG
        try:
            found = OG.open_files(text, self.root)
        except Exception:  # noqa: BLE001
            return ""
        keep = []
        for name, app, lock in found:
            title, body = OG.guard_card(name, app, lock)
            opts = [{"id": "change", "label": "Change it anyway", "kind": "anyway"},
                    {"id": "keep", "label": "Don't change it", "kind": "reject_once"}]
            choice, _note = self.approve("edit", title, body, opts)
            if choice != "change" or self._stop:
                keep.append(name)
        if self._stop:
            raise ACPStopped("stopped")
        return OG.keep_note(keep) if keep else ""

    # ---------- session ------------------------------------------------------------------------------------------
    def _before_turn(self) -> list:
        return before_turn(self)

    def _ensure_session_tracked(self):
        """_ensure_session, recording whether the agent could start (hrouter/health.py: Auto skips one that cannot)."""
        hid = self.manifest.get("id")
        try:
            self._ensure_session()
        except ACPStopped:
            raise                                    # the user pressed Stop: says nothing about the install
        except Exception as e:  # noqa: BLE001 - ACPError, or the process could not even be spawned (OSError)
            self.start_error = str(e)
            if hid:
                try:
                    from meshpool_harness.hrouter import health
                    health.start_failed(hid, str(e))
                except Exception:  # noqa: BLE001
                    pass
            raise
        if hid:
            try:
                from meshpool_harness.hrouter import health
                health.start_ok(hid)
            except Exception:  # noqa: BLE001
                pass

    def _ensure_session(self):
        if self.acp_session and self.office_on and self._session_office != self._office_kind() \
                and self.manifest.get("office", True) \
                and self._p and self._p.poll() is None:
            # The chat started without the office server and has now asked for an office file: open the same session
            # again with the server (a harness that cannot load it starts a new one, which carries the history).
            self.loaded_from, self.acp_session = self.acp_session, None
            if not self.capabilities.get("loadSession"):
                self.loaded_from = None
        if self._start() and self.acp_session:
            # The process exited and a new one started: its session is not in the new process. Load it (or start
            # a new one), as after a hard restart.
            self.loaded_from, self.acp_session = self.acp_session, None
        if self.acp_session:
            return
        allowed = bool(self.manifest.get("office", True))
        office = bool(self.office_on and allowed)
        mcp = mcp_servers_for_acp([str(self.root)], self.id) if self.manifest.get("share_mcp", True) else []
        if self.manifest.get("lean"):          # Codex: only what the chat needs (see lean_mcp)
            mcp = lean_mcp(mcp, office, str(self.root), allowed, bool(self.office_edit))
        elif allowed:
            mcp.append(office_server([str(self.root)]))
            office = True
        self._session_office = self._office_kind() if office else ""
        if self.loaded_from and self.capabilities.get("loadSession"):
            try:
                self._replaying = True
                rl = self._request("session/load", {"sessionId": self.loaded_from, "cwd": str(self.root),
                                                    "mcpServers": mcp}, timeout=180)
                self.acp_session = self.loaded_from
                self._ask_mode((rl or {}).get("modes") or {})
                self._models_from(rl or {})
                self._reloaded = True
                self._apply_model()               # the thread's model, as after session/new
                return
            except ACPError:
                pass
            finally:
                self._replaying = False
        try:
            r = self._request("session/new", {"cwd": str(self.root), "mcpServers": mcp}, timeout=180)
        except ACPError as e:
            if "auth" in str(e).lower():
                raise ACPError(f"{self.manifest.get('name')} needs you to sign in first. "
                               + (self.manifest.get("setup") or "Run its setup command in a terminal."))
            raise
        self.acp_session = r["sessionId"]
        self._ask_mode(r.get("modes") or {})
        self._models_from(r)
        self._apply_model()
        self._set_title()
        # A fresh session for a chat that already has a history (the harness lost or cannot load its old session,
        # e.g. a chat from weeks ago): it would start with no memory of the chat. Carry the history in the prompt.
        self._carry = any(e.get("type") in ("user", "assistant") for e in self._before_turn())

    def _office_kind(self) -> str:
        return "edit" if self.office_edit else "all"

    def _set_title(self):
        """Give the session Meshpool's own title. codex-acp starts a second Codex session (about 12k tokens, on its own
        title model) to write a title for a session that has none; once a title is set explicitly it does not. An agent
        that does not know the method refuses it, and that is fine."""
        title = (self.title or "").strip()
        if not (title and self.acp_session and self.manifest.get("lean")) or os.environ.get("MH_ACP_TITLE", "1") == "0":
            return
        if getattr(self, "_title_refused", False):
            return
        try:
            self._request("session/set_title", {"sessionId": self.acp_session, "title": title[:80]}, timeout=30)
        except Exception:  # noqa: BLE001 - ACPError or a timeout: the agent titles the chat itself, as before
            self._title_refused = True

    def _models_from(self, r: dict):
        ms = r.get("models") or {}
        self.model_now = ms.get("currentModelId")
        if ms.get("availableModels"):
            self.available_models = [{"id": m.get("modelId"), "name": m.get("name") or m.get("modelId")}
                                     for m in ms["availableModels"] if m.get("modelId")]

    def _ask_mode(self, modes: dict):
        """Run the harness in its own ask-before-acting mode, so every edit and command comes to Meshpool, which
        applies the user's permission setting (Ask, Accept edits, Full access) the same way for every
        harness. Without this Codex, for one, edits files on its own."""
        ids = [m.get("id") for m in modes.get("availableModes") or [] if m.get("id")]
        self.modes_seen, self.mode_now = ids, modes.get("currentModeId")
        want = (self.manifest.get("modes") or {}).get(self.mode)        # e.g. Codex: a mode per Meshpool setting
        if getattr(self, "plan", False) and "read-only" in ids:
            want = "read-only"           # Plan changes nothing: Codex's own modes never ask, so Plan could not block
        pick = want if want in ids else next((i for w in ("default", "ask", "untrusted") for i in ids
                                              if i.lower() == w), None)
        if pick and pick != modes.get("currentModeId"):
            try:
                self._request("session/set_mode", {"sessionId": self.acp_session, "modeId": pick}, timeout=30)
                self.mode_now = pick
            except ACPError:
                pass

    def apply_mode(self):
        """The user changed Ask / Accept edits / Full access: tell a harness that has its own modes."""
        if self.acp_session and self._p and self._p.poll() is None and getattr(self, "modes_seen", None):
            self._ask_mode({"availableModes": [{"id": i} for i in self.modes_seen], "currentModeId": self.mode_now})

    def _apply_model(self):
        from meshpool_harness import providers as PV
        bare = PV.split(self.model)[1] if self.model else ""
        if not (bare and bare != "default" and self.acp_session):
            return                             # "default": the harness's own account picks the model
        now = getattr(self, "model_now", None) or ""
        ids = [m["id"] for m in self.available_models or [] if m.get("id")]
        eff = (getattr(self, "effort", "") or "").lower()
        pick = f"{bare}[{eff}]" if eff and eff not in ("off", "none", "default") else ""
        if pick and pick in ids:                   # the chat's effort level: Codex's own id for this model at that level
            if pick == now:
                return
            want = pick
        else:
            if bare == now or now.startswith((bare + "[", bare + "/")):
                return                         # already on it; switching anyway makes Hermes rebuild its agent
            # Codex names its models "<model>[<effort>]" (deepseek-v4.1-flash[medium]) and refuses a bare id
            want = bare if bare in ids or not ids else next((i for i in ids if i.startswith((bare + "[", bare + "/"))),
                                                            bare)
        try:
            self._request("session/set_model", {"sessionId": self.acp_session, "modelId": want}, timeout=60)
            self.model_now = want
        except ACPError as e:
            self._emit({"type": "status", "text": f"{self.manifest.get('name')} kept its own model ({e})."})

    effort = ""

    def set_effort(self, level: str):
        self.effort = level or ""
        if self.model and self.acp_session and self._p and self._p.poll() is None:
            self._apply_model()

    def set_model(self, model: str):
        self.model = model or None
        if self.acp_session and self._p and self._p.poll() is None:
            self._apply_model()

    def current_model(self) -> str:
        if self.model:
            return self.model
        if self.manifest.get("models") == "meshpool":
            return os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
        return self.agent_info.get("title") or self.agent_info.get("name") or self.manifest.get("name", "")

    def _descendants(self) -> set:
        try:
            import psutil
            return {c.pid for c in psutil.Process(self._p.pid).children(recursive=True)} if self._p else set()
        except Exception:  # noqa: BLE001
            return set()

    def _kill_new_children(self, before: set):
        """Stop commands the harness started during this turn (a cancel only stops the agent between steps;
        a long command would otherwise run to the end). MCP servers started earlier are left alone."""
        try:
            import psutil
            procs = [c for c in psutil.Process(self._p.pid).children(recursive=True) if c.pid not in before]
        except Exception:  # noqa: BLE001
            return
        for c in procs:
            try:
                c.kill()
            except Exception:  # noqa: BLE001
                pass

    def _prompt_call(self, prompt: list) -> dict:
        """One session/prompt request; its usage goes to the ledger once (_record_usage)."""
        self._usage_recorded = False
        r = self._prompt(prompt)                         # the turn's and chat's own counts (ws17); the ledger row is below
        self._record_usage(r, "".join(str(p.get("text") or "") for p in prompt if isinstance(p, dict)),
                           "".join(self._text))
        return r

    def _record_usage(self, r: dict, prompt_text: str, final_text: str) -> None:
        """The harness's own usage from a prompt response, else one call counted from the text sizes (flagged as an
        estimate, never free). At most once per response: other code that records it sets _usage_recorded."""
        if self._usage_recorded:
            return
        self._usage_recorded = True
        try:
            hid = str(self.manifest.get("id") or "acp")
            extra = None
            if self._cost_now > self._cost_seen:
                extra = {"reported_cost": round(self._cost_now - self._cost_seen, 6)}
                self._cost_seen = self._cost_now
            USAGE.record(str(getattr(self, "model_now", None) or self.model or hid),
                         (r.get("usage") if isinstance(r, dict) else None) or None, source=f"external:{hid}",
                         task=self.usage_task or None, extra=extra, request_chars=len(prompt_text),
                         reply_chars=len(final_text))
            if self.usage_task and USAGE.over_budget(self.usage_task) is not None and not self._stop:
                self.budget_stopped = True               # the call that crossed the limit is kept; nothing more runs
                self._emit({"type": "error", "text": str(USAGE.over_budget(self.usage_task)) + " The agent was "
                            "stopped. Raise the limit in Settings or send a message to continue."})
                self.stop()
        except Exception:  # noqa: BLE001 - accounting never breaks a turn
            pass

    def send(self, text: str, images: list | None = None) -> str:
        over = USAGE.over_budget(self.usage_task) if self.usage_task else None
        if over is not None:                             # the spending limit: an outside agent cannot be refused per call
            self.budget_stopped = True
            self._emit({"type": "error", "text": str(over) + " Raise the limit in Settings or send a message to continue."})
            return ""
        self.budget_stopped = False
        kt = getattr(self, "_kill_timer", None)
        if kt:
            kt.cancel()                                  # the sweep after a Stop must not hit this new turn's commands
            self._kill_timer = None
        self._stop = False
        self.start_error = None                          # set when this turn's agent could not start (Session hands off)
        self.turn_failed = None                          # the engine's own error when the turn failed before any work
        self._before = None                              # taken once the process and its MCP servers run (below)
        if not self.title:
            self.title = text.strip().splitlines()[0][:80] if text.strip() else "Session"  # first line only
        shown = text.split("\n\n\u200b")[0]          # attached skill text and image OCR go to the agent only
        self._turn_at = len(self.events)                 # the chat before this turn (earlier_context)
        self._emit({"type": "user", "text": shown, "images": [im.get("name", "image") for im in images or []],
                    "plan": self.plan})
        self.save()                                       # the thread survives a restart mid-turn
        body = text
        if self.plan:
            body = ("[Plan mode] Investigate as needed, then reply with a clear, numbered plan for the request below. "
                    "Do not change any files or run commands that change anything; the user will review the plan "
                    "first.\n\n" + text)
        self._text = []
        final = ""
        self.usage["calls"] += 1
        self._turn = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        self._in_turn = True
        try:
            self._emit({"type": "model_start"})
            self._turn_tools = 0
            done = self._office_direct(shown)
            if done is not None:                         # a plain "make a PDF of X": meshpool did it, no engine call
                return done
            body += self._open_file_guard(shown)
            self._ensure_session_tracked()
            if self._stop:                               # Stop pressed while the harness was starting
                raise ACPStopped("stopped")
            self._before = self._descendants()           # Stop then ends only what this turn starts
            if getattr(self, "_carry", False):
                self._carry = False
                body = earlier_context(self._before_turn()) + body
                self._emit({"type": "status", "text": f"{self.manifest.get('name', 'The agent')} could not reopen "
                                                      "its own session for this chat, so the conversation so far "
                                                      "went along with your message."})
            prompt = [{"type": "text", "text": body}]
            if images:
                if (self.capabilities.get("promptCapabilities") or {}).get("image"):
                    prompt += [{"type": "image", "mimeType": im.get("mime") or "image/png", "data": im["data"]}
                               for im in images[:6]]
                else:
                    self._emit({"type": "status", "text": f"{self.manifest.get('name')} does not take images; "
                                                          "they were left out."})
            r = self._prompt_call(prompt)
            if (r.get("stopReason") == "refusal" and getattr(self, "_reloaded", False) and not self._text
                    and not self._stop):                 # a refusal to a stopped prompt is just the stop
                self.acp_session, self.loaded_from = None, None     # the reloaded session was gone: start fresh
                self._ensure_session()
                if getattr(self, "_carry", False):        # and it starts knowing the chat so far
                    self._carry = False
                    prompt[0]["text"] = earlier_context(self._before_turn()) + prompt[0]["text"]
                r = self._prompt_call(prompt)
            self._reloaded = False
            final = "".join(self._text)
            if (not final.strip() and not self._stop and r.get("stopReason", "end_turn") == "end_turn"
                    and getattr(self, "_turn_tools", 0) == 0):
                # An empty reply with nothing done (the model sometimes returns a blank turn): ask once more.
                self._text = []
                r = self._prompt_call([
                    {"type": "text", "text": "Your last reply was empty. Please answer my previous message."}])
                final = "".join(self._text)
                if not final.strip() and not self._stop and getattr(self, "_turn_tools", 0) == 0:
                    self._emit({"type": "status", "text": f"{self.manifest.get('name', 'The harness')} didn't "
                                                          "answer. Try sending your message again."})
            if final.strip() and not self._stop and getattr(self, "_turn_tools", 0) == 0 and _engine_error(final):
                # Codex writes a failed model call as its reply ("unexpected status 404 Not Found: ..., url: ..."):
                # an error, not an answer. Session.emit says it in plain words; Session may run it on meshpool's agent.
                self._text, self.turn_failed = [], final.strip()
                self._emit({"type": "stream_reset"})     # the error text shown as it streamed goes
                self._emit({"type": "error", "text": final.strip()})
                final = ""
            self._flush_text()
            stop = r.get("stopReason", "end_turn")
            if _auth_failure(final):
                # Some harnesses (Hermes) reply with the raw HTTP error as plain text. Say what to do instead.
                self._emit({"type": "error", "text": "The endpoint rejected the API key. Check it in Settings, "
                                                     "Endpoint and key, then send your message again."})
            if stop == "cancelled" or self._stop:
                self._emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
            elif stop in ("max_tokens", "max_turn_requests"):
                self._emit({"type": "status", "text": f"The harness stopped early ({stop.replace('_', ' ')})."})
            elif stop == "refusal":
                self._emit({"type": "error", "text": "The harness refused this request."})
        except ACPStopped:
            self._flush_text()
            self._emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
        except ACPError as e:
            self._flush_text()
            if self._stop:                               # an error while stopping is just the stop
                self._emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
            else:
                if getattr(self, "_turn_tools", 0) == 0:
                    self.turn_failed = str(e)            # the engine refused the prompt before doing anything
                # A stall: the line offers Try again (Session.retry runs the message again, files as they are). With
                # nothing done yet, Session moves an Auto chat to meshpool's own agent (_engine_fallback).
                self._emit({"type": "error", "text": str(e), **({"again": True} if isinstance(e, ACPStall) else {})})
        finally:
            self._in_turn = False
            self._close_open_tools()
            self._emit({"type": "turn_end", "turn": dict(self._turn), "session": dict(self.usage),
                        "context": self.context})
            self.save()
        return final

    def _prompt(self, prompt: list) -> dict:
        """One session/prompt. The token usage a harness reports with its answer (ACP PromptResponse.usage, e.g.
        Codex) goes into the turn's and the chat's usage, so the usage ledger counts outside harnesses too."""
        r = self._request("session/prompt", {"sessionId": self.acp_session, "prompt": prompt})
        u = r.get("usage") if isinstance(r, dict) else None
        if isinstance(u, dict):
            def n(*keys):
                for k in keys:
                    try:
                        return max(0, int(u.get(k) or 0))
                    except (TypeError, ValueError):
                        continue
                return 0
            cached = n("cachedReadTokens", "cached_read_tokens")
            # ACP counts cached input apart (Codex: inputTokens 200 + cachedReadTokens 1000 for a 1,200-token
            # prompt); Meshpool's "in" is the whole prompt, cached part included, as for Pi and the built-in agents
            add = {"in": n("inputTokens", "input_tokens") + cached + n("cachedWriteTokens", "cached_write_tokens"),
                   "cached": cached, "out": n("outputTokens", "output_tokens") + n("thoughtTokens", "thought_tokens"),
                   "calls": 1}
            turn = getattr(self, "_turn", None)          # none outside a send() (a bare prompt call)
            for k, v in add.items():
                if turn is not None:
                    turn[k] += v
                if k != "calls":                         # calls: counted once per message in send()
                    self.usage[k] += v
        return r

    def stop(self):
        self._stop = True
        self._stop_t = time.time()
        stamp_stop(self.id)                              # sub-agents in this chat's Meshpool MCP servers stop too
        if self.acp_session and self._p and self._p.poll() is None:
            try:
                self._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.acp_session}})
            except ACPError:
                pass
            before = getattr(self, "_before", None)
            if before is not None:
                self._kill_new_children(before)          # now, and again in a moment for anything just starting
                t = threading.Timer(1.0, self._kill_new_children, args=(before,))
                self._kill_timer = t                     # cancelled if a new turn starts first (see send)
                t.start()

    def forget_session(self):
        """Undo cut the chat: the harness's own session still holds the undone turns. The next message opens a new
        session, which gets the chat as it now is (see _carry in _ensure_session)."""
        self.acp_session = None
        self.loaded_from = None

    def reset(self):
        self.acp_session = None
        self.loaded_from = None
        self.events = []
        self.title = ""

    def close(self):
        p = self._p
        if not p:
            return
        _kill_tree(p)                                 # commands it started must not outlive the thread
        try:
            p.stdin.close()
        except Exception:
            pass
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def save(self):
        if getattr(self, "deleted", False):           # the user deleted this thread; never bring it back
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            SS.write_snapshot(self.dir, {
                "id": self.id, "workdir": str(self.root), "title": self.title, "created": self.created,
                "updated": time.time(), "harness": self.manifest.get("id"), "kind": self.manifest.get("mode", "cowork"),
                "acp_session": self.acp_session or self.loaded_from, "usage": self.usage,
                "events": self.events[-2000:]})
        except OSError:
            pass

    @classmethod
    def resume(cls, data: dict, manifest: dict, **kw) -> "ACPAgent":
        a = cls(manifest, data["workdir"], session_id=data["id"], **kw)
        a.title = data.get("title", "")
        a.created = data.get("created", a.created)
        a.events = data.get("events") or []
        a.usage = data.get("usage") or a.usage
        a.loaded_from = data.get("acp_session")
        return a
