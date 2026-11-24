"""Pi as a Meshpool harness, over Pi's RPC mode (pi --mode rpc: JSON lines on stdin and stdout).

Meshpool ships Pi and a Node runtime inside the app. Before Pi starts, Meshpool writes Pi's own config folder
(<home>/pi): models.json points Pi at the Meshpool endpoint with every model the key can use, mcp.json carries the
same MCP servers and connectors the other harnesses get, settings.json sets the shell, and skills/ holds the
user's Meshpool skills. pi_gate.js turns every tool call that could change something into an RPC confirm dialog,
which Meshpool answers from the thread's access mode or by asking the user.

Pi events become Meshpool UI events:
  message_update text_delta / thinking_delta -> text_delta / thought
  tool_execution_start / _end                -> tool_start / tool_end
  extension_ui_request confirm               -> approval
  agent_end                                  -> turn_end (usage from get_session_stats)
"""
from __future__ import annotations

import difflib
import json
import queue
import re
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness import session_store as SS
from meshpool_harness.core import usage as USAGE
from meshpool_harness.harnesses.acp import _kill_tree
from meshpool_harness.harnesses.safe_tools import SAFE_MCP

# Pi's own read-only tools, plus the MCP tools every Meshpool agent runs without asking (one list, safe_tools.py).
# pi_gate.js gets the same set through MESHPOOL_PI_SAFE, so it never asks for these.
READ_ONLY = frozenset({"read", "grep", "find", "ls", "tool_search", "codemode", "list_mcp_resources",
                       "list_mcp_resource_templates", "read_mcp_resource"}) | SAFE_MCP
LABEL = {"read": "Read", "write": "Wrote", "edit": "Edited", "bash": "Ran", "powershell": "Ran", "grep": "Searched",
         "find": "Listed", "ls": "Listed", "tool_search": "Found tools", "codemode": "Ran a script"}


_ADDED = "[Added by the user while you were working] "


class PiError(Exception):
    pass


def node_exe() -> str | None:
    """The Node runtime bundled with the app; one on PATH only in a source checkout (never in the packaged app)."""
    from meshpool_harness import bundled
    return bundled.node_exe()


def pi_cli() -> Path | None:
    for base in (C.APP / "vendor" / "pi", C.APP.parent / "pi"):
        p = base / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "bundle" / "cli.js"
        if p.is_file():
            return p
    return None


_FLAGS: dict[str, bool] = {}


def _takes_no_approve(node: str, cli: Path) -> bool:
    """Whether this Pi build has --no-approve (the bundled build does; upstream Pi has no approval prompts of its own
    and refuses the unknown option). Asked once per Pi build."""
    k = str(cli)
    if k not in _FLAGS:
        try:
            r = subprocess.run([node, k, "--help"], capture_output=True, text=True, timeout=30,
                               **({"creationflags": 0x08000000} if os.name == "nt" else {}))
            _FLAGS[k] = "--no-approve" in (r.stdout + r.stderr) or r.returncode != 0
        except (OSError, subprocess.SubprocessError):
            _FLAGS[k] = True                       # cannot tell: the bundled build's flag, as before
    return _FLAGS[k]


def available() -> bool:
    return bool(node_exe() and pi_cli())


def pi_home() -> Path:
    return C.home() / "pi"


def _models() -> list[dict]:
    try:
        from meshpool_harness.ui import workbench as W
        rows = W.models().get("models") or []
    except Exception:  # noqa: BLE001
        rows = []
    prov, cur = _pi_model(os.environ.get("MH_MODEL"))
    cur = cur if prov == "meshpool" else "deepseek-v4.1-flash"
    if not any(r.get("id") == cur for r in rows):
        rows = [{"id": cur}] + rows
    out = []
    for r in rows:
        caps = r.get("caps") or []
        out.append({"id": r["id"], "name": r["id"], "reasoning": "reasoning" in caps,
                    "input": ["text", "image"] if "vision" in caps else ["text"],
                    "contextWindow": int(r.get("context") or 128000), "maxTokens": 16384,
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}})
    return out


# One line per Meshpool server for Pi's system prompt (Pi lists a deferred server by this line and loads its tools
# through tool_search), so a chat pays for the lines, not for the 50 tool schemas.
SERVER_NOTE = {
    "meshpool-office": "Office files: read, edit and convert Word, Excel, PowerPoint and PDF; make decks, workbooks, "
                       "documents and PDFs.",
    "meshpool-assistant": "Meshpool memory, scheduled tasks, past chats and sub-agents.",
    "meshpool-web": "Web search, page fetch and multi-step research.",
    "meshpool-tools": "Time, exact arithmetic and project statistics.",
    "browser": "A browser to open, read and drive web pages.",
}
# What stays declared in a chat that asks for an office file: the tools that change or read the user's own file.
OFFICE_DIRECT = {"edit_office_file": "direct", "read_document": "direct", "convert_document": "direct",
                 "check_*": "direct"}


def pi_skills_all() -> bool:
    """MH_PI_SKILLS=all gives Pi every skill in its system prompt, as before (about 15k tokens on every request)."""
    return os.environ.get("MH_PI_SKILLS", "").lower() == "all"


def pi_mcp_direct() -> bool:
    """MH_PI_MCP=direct declares every Meshpool tool to Pi, as before (about 5k tokens on every request)."""
    return os.environ.get("MH_PI_MCP", "").lower() == "direct"


def _mcp_for_pi(root: Path, chat: str = "", office: bool = False) -> dict:
    """Pi's mcp.json. Every server is deferred: Pi lists it by one line and loads its tools through tool_search when
    the model asks. A chat that asks for an office file also declares edit_office_file, read_document,
    convert_document and the check_* tools of the office server."""
    from meshpool_harness.harnesses.acp import mcp_servers_for_acp, office_server
    servers = {}
    rows = mcp_servers_for_acp([str(root)], chat) + [office_server([str(root)])]
    every = pi_mcp_direct()
    for s in rows:
        name = s["name"]
        if s.get("url"):
            spec = {"url": s["url"], "headers": {h["name"]: h["value"] for h in s.get("headers") or []},
                    "exposure": "deferred"}
        else:
            env = {e["name"]: e["value"] for e in s.get("env") or []}
            if name == "meshpool-assistant":
                env["MH_CHAT_KIND"] = "code"             # Pi is a Code agent: what it schedules runs in Code mode
            spec = {"command": s["command"], "args": s.get("args") or [], "env": env,
                    "timeout": 1800 if name == "meshpool-assistant" else 300,
                    "exposure": "direct" if every else "deferred"}
            if name in SERVER_NOTE:
                spec["description"] = SERVER_NOTE[name]
            if name == "meshpool-office" and office and not every:
                spec["toolExposure"] = dict(OFFICE_DIRECT)
        servers[name] = spec
    return {"mcpServers": servers, "autoEnableCodemode": False}


def _pi_model(model: str | None) -> tuple[str, str]:
    """(Pi provider name, model id) for a Meshpool model id; one Pi cannot use falls back to the key endpoint."""
    from meshpool_harness import providers as PV
    prov, bare = PV.split(model)
    if prov == "chatgpt" and bare:
        return prov, bare
    return "meshpool", (bare if prov == "key" and bare else "deepseek-v4.1-flash")


def prepare(root: Path, chat: str = "", office: bool = False, task: str = "") -> Path:
    """Write Pi's config folder for this launch and return it. Each chat has its own folder (pi/chats/<chat>), so two
    chats starting at once never read each other's project, MCP servers or model (a shared one was rewritten in place
    at every launch); the user's skills stay in one shared folder that every chat's settings list."""
    from meshpool_harness import fsio
    shared = pi_home()
    h = shared / "chats" / re.sub(r"[^\w.-]", "_", chat) if chat else shared
    h.mkdir(parents=True, exist_ok=True)
    from meshpool_harness import providers as PV
    node, cli = node_exe(), pi_cli()
    # The key comes from Pi's environment: the bundled build reads "$NAME", upstream Pi reads a bare NAME.
    ref = (lambda name: "$" + name) if not (node and cli) or _takes_no_approve(node, cli) else (lambda name: name)
    base = PV.agent_endpoint("key")[0]          # the key endpoint, or the local relay when it needs more than Bearer
    if PV.key_wire() == "anthropic":           # an Anthropic Messages endpoint: Pi's own Anthropic client, whose SDK
        key_provider = {"baseUrl": re.sub(r"/v1/?$", "", base),        # adds /v1/messages itself
                        "api": "anthropic-messages", "apiKey": ref("MESHPOOL_API_KEY"), "models": _models()}
    else:
        key_provider = {"baseUrl": base, "api": "openai-completions", "apiKey": ref("MESHPOOL_API_KEY"),
                        "compat": {"supportsDeveloperRole": False}, "models": _models()}
    models = {"providers": {"meshpool": key_provider}}
    for prov, env in (("chatgpt", "MESHPOOL_CHATGPT_RELAY_KEY"),):
        if PV.ready(prov):                     # the other providers Pi can use, keys from its environment
            ids, _ = PV.list_models(prov)      # ChatGPT: the local relay, which holds the sign-in
            models["providers"][prov] = {
                "baseUrl": PV.endpoint(prov)[0], "api": "openai-completions", "apiKey": ref(env),
                "compat": {"supportsDeveloperRole": False},
                "models": [{"id": i, "name": i, "reasoning": False, "input": ["text", "image"],
                            "contextWindow": 200000, "maxTokens": 16384,
                            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}} for i in ids]}
    fsio.atomic_write_text(h / "models.json", json.dumps(models, indent=1), fsync=False)
    try:
        mcp = _mcp_for_pi(root, chat, office)
    except Exception:  # noqa: BLE001
        mcp = {"mcpServers": {}}
    from meshpool_harness.harnesses import write_private
    write_private(h / "mcp.json", json.dumps(mcp, indent=1))     # carries connector tokens
    sp = h / "settings.json"
    base_sp = sp if sp.exists() else shared / "settings.json"      # a chat's first launch starts from the shared one
    try:
        settings = json.loads(base_sp.read_text(encoding="utf-8")) if base_sp.exists() else {}
    except (ValueError, OSError):
        settings = {}
    settings = settings if isinstance(settings, dict) else {}
    dp, dm = _pi_model(os.environ.get("MH_MODEL"))
    settings.update({"defaultProvider": dp, "defaultModel": dm,
                     "defaultTools": ["read", "bash", "edit", "write"]})
    if os.name == "nt" and os.environ.get("MH_BASH"):
        settings["shellPath"] = os.environ["MH_BASH"]
    if pi_skills_all():                          # as before: every skill in Pi's system prompt
        skills = shared / "skills" / "meshpool"
        try:
            from meshpool_harness import skills as SK
            with fsio.file_lock(shared / "skills"):          # two launches must not copy over each other
                SK.sync_to(skills)
        except Exception:  # noqa: BLE001
            pass
        if h != shared:
            settings["skills"] = [str(skills)]
    else:
        settings.pop("skills", None)
        pi_skill_dirs(h, task)
    fsio.atomic_write_text(sp, json.dumps(settings, indent=1), fsync=False)
    return h


def pi_skill_dirs(h: Path, task: str = "") -> list[str]:
    """Stage the skills this chat may use in <pi home>/skills/meshpool and return the folders to load: the (at most 3)
    that match the task (skills.search, the same ranking the built-in agents use) and any the user names with $name.
    Pi starts with --no-skills, so its system prompt lists only these, not the whole catalog. Every other skill stays
    one read away: INDEX.md next to them lists them by name with their path, and the system prompt says so in one
    line (see pi_skill_note)."""
    try:
        from meshpool_harness import skills as SK
        from meshpool_harness import capabilities as CAP
        text = str(task or "").split(CAP.SEP)[0]
        rows = SK.enabled()
        pick = SK.search(text, k=3, rows=rows)
        named = {m.lower() for m in re.findall(r"(?<![\w$])\$([a-z0-9][a-z0-9-]*)", text)}
        want = {s["id"]: s for s in pick}
        want.update({s["id"]: s for s in rows if s["id"].lower() in named or s["name"].lower() in named})
        dst = h / "skills" / "meshpool"
        SK.sync_to(dst, only=set(want))
        idx = ["# Meshpool skills not loaded for this chat (name: path). Read a skill's SKILL.md when it fits the task.", ""]
        idx += [f"- {s['name']}: {Path(s['path'])}" for s in rows if s["id"] not in want]
        (dst / "INDEX.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
        return [str(dst / sid) for sid in want]
    except Exception:  # noqa: BLE001 - Pi still runs, without skills
        return []


def skill_args(home: Path) -> list[str]:
    """Pi's command-line options for skills: no catalog in the system prompt (--no-skills), the skills staged for this
    chat loaded explicitly, and one line pointing at the index of the others. MH_PI_SKILLS=all: none, as before."""
    if pi_skills_all():
        return []
    args = ["--no-skills"]
    for d in sorted((home / "skills" / "meshpool").glob("*/SKILL.md")):
        args += ["--skill", str(d.parent)]
    note = pi_skill_note(home)
    return args + (["--append-system-prompt", note] if note else [])


def _skill_read_dirs(home: Path) -> list[str]:
    try:
        from meshpool_harness import skills as SK
        return [str(home / "skills"), str(SK.folder())]
    except Exception:  # noqa: BLE001
        return [str(home / "skills")]


def pi_skill_note(h: Path) -> str:
    idx = h / "skills" / "meshpool" / "INDEX.md"
    return (f"Other Meshpool skills (task instructions) are listed in {idx}; read the one that fits the task."
            if idx.is_file() and idx.stat().st_size > 200 else "")


def _edit_diff(path: str, inp: dict, root: Path) -> str:
    edits = inp.get("edits") or ([{"oldText": inp.get("oldText", ""), "newText": inp.get("newText", "")}]
                                  if "oldText" in inp else [])
    try:
        p = Path(path) if Path(path).is_absolute() else root / path
        old = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        old = ""
    new = old
    for e in edits:
        new = new.replace(e.get("oldText", ""), e.get("newText", ""), 1) if e.get("oldText") else new
    if new == old and edits:
        return "\n".join(f"- {e.get('oldText', '')}\n+ {e.get('newText', '')}" for e in edits)[:4000]
    return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", n=2,
                                          lineterm=""))[:6000]


def _write_diff(path: str, content: str, root: Path) -> str:
    try:
        p = Path(path) if Path(path).is_absolute() else root / path
        old = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    except OSError:
        old = ""
    return "\n".join(difflib.unified_diff(old.splitlines(), (content or "").splitlines(), f"a/{path}", f"b/{path}",
                                          n=2, lineterm=""))[:6000]


def _summary(name: str, args: dict) -> str:
    for k in ("command", "path", "pattern", "query", "url"):
        if isinstance(args.get(k), str) and args[k]:
            return args[k].splitlines()[0][:160]
    return ""


def _result_text(res) -> str:
    if isinstance(res, dict):
        parts = [c.get("text", "") for c in res.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
        if parts:
            return "\n".join(parts)
        return json.dumps(res.get("details") or {}, ensure_ascii=False)[:2000] if res.get("details") else ""
    return str(res or "")


def _before_turn(agent) -> list:
    from meshpool_harness.harnesses.acp import before_turn
    return before_turn(agent)


class PiAgent:
    """One Pi process serving one Meshpool thread. Same surface as ACPAgent."""
    usage_task = None          # the session's usage task (core/usage.py): Pi's calls are recorded in it
    budget_stopped = False     # the last turn ended on the spending limit (agent_api pauses a goal on it)

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
        self.context = None
        self.events: list[dict] = []
        self.messages = [None]
        self.mcp = None
        self.office_on = False             # the chat asked for an office file: the office tools are declared to Pi
        self._task = ""                    # the first message: which skills Pi starts with
        self.agent_info = {"name": "Pi"}
        self.model: str | None = None
        self.available_models = None
        self.plan = False
        self.resume = False
        self._p = None
        self._rid = 0
        self._replies: dict[str, dict] = {}
        self._cv = threading.Condition()
        self._alive = False
        self._stop = False
        self._done = threading.Event()
        self._text: list[str] = []
        self._always: set[str] = set()
        self._denied: set[str] = set()
        self._lock = threading.Lock()
        self._turn = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        self._exited = threading.Event()   # set once the process's last event has been handled (a crash, not _done)
        self._sl = threading.Lock()        # _up and _held change together
        self._up = False                   # Pi runs and answers: a message added now goes to it as steering
        self._held: list = []              # messages added while Pi was starting: handed over once it runs
        self._pi_steering: list = []       # Pi's own queue of steering messages not yet read (queue_update)

    # ---------- process ----------------------------------------------------------------------------------------
    def _emit(self, ev: dict):
        if ev.get("type") not in ("text_delta", "thought", "model_start", "model_end", "context"):
            self.events.append({k: v for k, v in ev.items() if k not in ("i", "t")})
        self.emit_cb(ev)

    def _start_tracked(self):
        """_start, recording whether Pi could start (hrouter/health.py: Auto skips an agent that cannot)."""
        hid = self.manifest.get("id") or "pi"
        try:
            self._start()
        except Exception as e:  # noqa: BLE001 - PiError, or the process could not be spawned (OSError)
            if not self._stop:                       # a Stop is not a failed start
                self.start_error = str(e)
                try:
                    from meshpool_harness.hrouter import health
                    health.start_failed(hid, str(e))
                except Exception:  # noqa: BLE001
                    pass
            raise
        try:
            from meshpool_harness.hrouter import health
            health.start_ok(hid)
        except Exception:  # noqa: BLE001
            pass

    def _start(self):
        if self._p and self._p.poll() is None:
            return
        node, cli = node_exe(), pi_cli()
        if not (node and cli):
            raise PiError("Pi is not included in this copy of Meshpool. Reinstall Meshpool to get it back.")
        home = prepare(self.root, self.id, bool(self.office_on), getattr(self, "_task", ""))
        sess = self.dir / "pi"
        sess.mkdir(parents=True, exist_ok=True)
        args = [node, str(cli), "--mode", "rpc", "--session-dir", str(sess),
                *(["--no-approve"] if _takes_no_approve(node, cli) else []),
                "-e", str(Path(__file__).with_name("pi_gate.js"))]
        args += skill_args(home)
        if any(sess.glob("*.jsonl")):             # also after a crash: Pi's own session files hold the tool history
            args.append("--continue")
        elif any(e.get("type") in ("user", "assistant") for e in _before_turn(self)):
            self._carry = True                   # Pi's own session is gone (an old chat): carry the history over
        env = dict(os.environ)
        for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE"):
            env.pop(k, None)
        from meshpool_harness import providers as PV
        env.update({"PI_CODING_AGENT_DIR": str(home), "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0",
                    "MESHPOOL_PI_SAFE": json.dumps(sorted(READ_ONLY)),     # pi_gate.js: tools that never ask
                    "MESHPOOL_PI_ROOT": str(self.root),       # ... unless they read outside the project
                    "MESHPOOL_PI_READ": json.dumps(_skill_read_dirs(home)),     # (Meshpool's skills are fine to read)
                    "MESHPOOL_API_KEY": PV.agent_endpoint("key")[1]})
        if PV.ready("chatgpt"):
            from meshpool_harness.core import chatgpt_relay
            env.update(chatgpt_relay.env())    # the relay's address and token, never the ChatGPT sign-in
        nb = str(Path(node).parent)
        env["PATH"] = nb + os.pathsep + env.get("PATH", "")      # npx and node for MCP servers the user adds
        log_dir = C.runs_dir() / "harness-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        from meshpool_harness.logfiles import PipedLog      # H-23: rotates by size while Pi runs
        self._err = PipedLog(log_dir / "pi.log")
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
        self._p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err,
                                   cwd=str(self.root), env=env, **kw)
        self._err.release()
        self._alive = True
        self._exited.clear()
        with self._sl:                     # messages an earlier Pi had queued but not read: the new one gets them
            self._held += [(t.removeprefix(_ADDED), None) for t in self._pi_steering]
        self._pi_steering = []
        evq: queue.Queue = queue.Queue()
        threading.Thread(target=self._reader, args=(self._p, evq), daemon=True).start()
        threading.Thread(target=self._dispatch, args=(self._p, evq), daemon=True).start()
        st = self._request({"type": "get_state"}, timeout=120)
        try:
            ms = self._request({"type": "get_available_models"}, timeout=30).get("models") or []
            self.available_models = [{"id": m.get("id"), "name": m.get("name") or m.get("id")}
                                     for m in ms if m.get("provider") == "meshpool"]
        except PiError:
            pass
        self._apply_model(initial=(st.get("model") or {}).get("id"))
        with self._sl:
            self._up = True
            held, self._held = self._held, []
        for text, images in held:          # added while Pi started: Pi reads them with the prompt that follows
            self._steer_cmd(text, images)

    def _reader(self, proc, evq):
        """Only reads. Replies and queue updates are handled here; every other event goes to the dispatcher, so a
        reply is never stuck behind an event whose emit waits (the chat's lock, held by whoever waits for that
        reply: adding a message while Pi streamed froze the chat 30 s and lost the message)."""
        buf = b""
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:                    # LF-only framing (never split on U+2028)
                line, buf = buf.split(b"\n", 1)
                line = line.rstrip(b"\r")
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                t = msg.get("type")
                if t == "response":
                    if msg.get("id"):
                        with self._cv:
                            self._replies[msg["id"]] = msg
                            self._cv.notify_all()
                elif t == "queue_update":
                    self._pi_steering = list(msg.get("steering") or [])
                else:
                    evq.put(msg)
        with self._cv:
            if self._p is proc:                    # a late EOF from an earlier process must not end the new one
                self._alive = False
            self._cv.notify_all()
        evq.put(None)

    def _dispatch(self, proc, evq):
        while True:
            msg = evq.get()
            if msg is None:
                break
            try:
                self._on(msg)
            except Exception as e:  # noqa: BLE001  a bad event must not stop the events
                print(f"Pi event error: {e}", file=sys.stderr)
        if self._p is proc:
            with self._sl:
                self._up = False
            self._exited.set()                     # after every earlier event: the crash comes after the half reply

    def _write(self, obj: dict):
        try:
            with self._lock:
                self._p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
                self._p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError, AttributeError):
            raise PiError("Pi stopped. Its log is in runs/harness-logs/pi.log.")

    def _request(self, cmd: dict, timeout: float = 60) -> dict:
        with self._cv:
            self._rid += 1
            rid = f"m{self._rid}"
        self._write(dict(cmd, id=rid))
        end = time.time() + timeout
        with self._cv:
            while rid not in self._replies:
                if not self._alive:
                    raise PiError("Pi exited. See runs/harness-logs/pi.log.")
                if time.time() > end:
                    raise PiError(f"Pi did not answer {cmd.get('type')} within {int(timeout)} s.")
                self._cv.wait(0.3)
            r = self._replies.pop(rid)
        if not r.get("success"):
            raise PiError(str(r.get("error") or "Pi refused the request."))
        return r.get("data") or {}

    def _record_usage(self, msg: dict, u: dict):
        """One assistant message is one model call: put Pi's own numbers in the ledger (no usage block: counted as
        an estimate, never free)."""
        try:
            extra = None
            cost = u.get("cost")
            cost = cost.get("total") if isinstance(cost, dict) else cost
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
                extra = {"reported_cost": cost}
            text = "".join(str(c.get("text") or "") for c in msg.get("content") or [] if isinstance(c, dict))
            USAGE.record(str(msg.get("model") or self.model or "pi"), u or None, source="external:pi",
                         task=self.usage_task or None, reply_chars=len(text), extra=extra)
            if self.usage_task and USAGE.over_budget(self.usage_task) is not None and not self._stop:
                self.budget_stopped = True               # the call that crossed the limit is kept; nothing more runs
                self._emit({"type": "error", "text": str(USAGE.over_budget(self.usage_task)) + " The agent was "
                            "stopped. Raise the limit in Settings or send a message to continue."})
                self.stop()
        except Exception:  # noqa: BLE001 - accounting never breaks a turn
            pass

    # ---------- events -----------------------------------------------------------------------------------------
    def _on(self, m: dict):
        t = m.get("type")
        if t == "response":
            if m.get("id"):
                with self._cv:
                    self._replies[m["id"]] = m
                    self._cv.notify_all()
            return
        if t == "message_update":
            e = m.get("assistantMessageEvent") or {}
            if e.get("type") == "text_delta" and e.get("delta"):
                self._text.append(e["delta"])
                self._emit({"type": "text_delta", "text": e["delta"]})
            elif e.get("type") == "thinking_delta" and e.get("delta"):
                self._emit({"type": "thought", "text": e["delta"]})
        elif t == "message_end":
            msg = m.get("message") or {}
            if msg.get("role") == "assistant":
                u = msg.get("usage") or {}
                self._turn["calls"] += 1
                self._turn["in"] += int(u.get("input") or 0) + int(u.get("cacheRead") or 0)
                self._turn["cached"] += int(u.get("cacheRead") or 0)
                self._turn["out"] += int(u.get("output") or 0)
                self._record_usage(msg, u)
                has_tools = any(isinstance(c, dict) and c.get("type") == "toolCall" for c in msg.get("content") or [])
                self._flush_text(has_tools)
                if msg.get("stopReason") == "error" and msg.get("errorMessage"):
                    self._emit({"type": "error", "text": str(msg["errorMessage"])[:600]})
        elif t == "tool_execution_start":
            self._flush_text(True)
            name, args = m.get("toolName", ""), m.get("args") or {}
            mcp = name.startswith("mcp__")
            label = name.split("__", 2)[-1] if mcp else LABEL.get(name, name)
            diff = ""
            if name == "edit":
                diff = _edit_diff(args.get("path", ""), args, self.root)
            elif name == "write":
                diff = _write_diff(args.get("path", ""), args.get("content", ""), self.root)
            self._emit({"type": "tool_start", "id": m.get("toolCallId"), "name": label, "summary": _summary(name, args),
                        "mcp": mcp, "diff": diff})
        elif t == "tool_execution_end":
            tid = m.get("toolCallId")
            denied = tid in self._denied
            self._emit({"type": "tool_end", "id": tid, "name": m.get("toolName", ""), "ok": not m.get("isError"),
                        "denied": denied, "output": _result_text(m.get("result"))[:6000]})
        elif t == "extension_ui_request":
            threading.Thread(target=self._ui, args=(m,), daemon=True).start()
        elif t == "agent_end":
            if not m.get("willRetry"):
                self._done.set()
        elif t == "auto_retry_start":
            self._emit({"type": "status", "text": f"The model call failed ({str(m.get('errorMessage', ''))[:160]}). "
                                                  f"Retrying ({m.get('attempt')}/{m.get('maxAttempts')})."})
        elif t == "compaction_start":
            self._emit({"type": "status", "text": "Summarizing earlier messages to make room..."})
        elif t == "extension_error":
            self._emit({"type": "status", "text": f"Pi extension error: {str(m.get('error', ''))[:300]}"})

    def _ui(self, m: dict):
        rid, method = m.get("id"), m.get("method")
        try:
            if method == "confirm" and str(m.get("title", "")).startswith("meshpool:"):
                ok = self._gate(m)
                self._write({"type": "extension_ui_response", "id": rid, "confirmed": ok})
            elif method == "confirm":
                choice, _ = self.approve("mcp", m.get("title") or "Pi asks", m.get("message") or "") \
                    if self.approve else ("no", "")
                self._write({"type": "extension_ui_response", "id": rid, "confirmed": choice in ("yes", "always")})
            elif method == "select" and self.approve:
                opts = [{"id": str(o), "label": str(o), "kind": ""} for o in m.get("options") or []]
                choice, _ = self.approve("mcp", m.get("title") or "Pi asks you to choose", "", opts)
                if choice in (None, "", "no"):
                    self._write({"type": "extension_ui_response", "id": rid, "cancelled": True})
                else:
                    self._write({"type": "extension_ui_response", "id": rid, "value": choice})
            elif method == "notify":
                self._emit({"type": "status", "text": str(m.get("message", ""))[:400]})
            elif method in ("select", "input", "editor"):
                self._write({"type": "extension_ui_response", "id": rid, "cancelled": True})
        except PiError:
            pass

    def _gate(self, m: dict) -> bool:
        try:
            d = json.loads(m.get("message") or "{}")
        except ValueError:
            d = {}
        name, inp, tid = d.get("name") or m["title"].split(":", 1)[1], d.get("input") or {}, d.get("id")
        kind = "edit" if name in ("write", "edit") else "bash" if name in ("bash", "powershell") else "mcp"
        if name in READ_ONLY:
            out = self._outside(inp) if name in ("read", "grep", "find", "ls") else ""
            if not out or self.mode == "auto" or "read" in self._always:
                return True
            if not self.approve or self._stop:
                self._denied.add(tid)
                return False
            choice, note = self.approve("read", f"Read {out[:120]} (outside the project)?",
                                        json.dumps(inp, indent=1, ensure_ascii=False)[:3000])
            if choice == "always":
                self._always.add("read")
            if choice in ("yes", "always"):
                return True
            self._denied.add(tid)
            self._tell(note)
            return False
        if self.plan:
            # Plan mode only investigates: no edits, no commands, no tools that could change something.
            self._denied.add(tid)
            return False
        from meshpool_harness.core import permrules as PR
        if self.mode == "auto" or (kind != "bash" and kind in self._always) or (self.mode == "edits" and kind == "edit"):
            return True
        bpats = [x[5:] for x in self._always if x.startswith("bash:")]
        if kind == "bash" and PR.granted(str(inp.get("command", "")), bpats):
            return True
        if not self.approve or self._stop:
            self._denied.add(tid)
            return False
        if kind == "edit":
            path = inp.get("path", "")
            title = f"{'Edit' if name == 'edit' else 'Write'} {path}?"
            detail = _edit_diff(path, inp, self.root) if name == "edit" else _write_diff(path, inp.get("content", ""), self.root)
        elif kind == "bash":
            title, detail = "Run this command?", inp.get("command", "")
        else:
            title = f"Use {name.split('__', 2)[-1].replace('_', ' ')}" + \
                (f" ({name.split('__')[1]})" if name.startswith("mcp__") else "") + "?"
            detail = json.dumps(inp, indent=1, ensure_ascii=False)[:3000]
        pats = PR.grant_patterns(str(inp.get("command", ""))) if kind == "bash" else []
        scope = (" and ".join(f"`{p}`" for p in pats) + " commands") if pats else \
            "all file edits" if kind == "edit" else "this tool" if kind != "bash" else ""
        choice, note = PR.approve_with_scope(self.approve, kind, title, detail, scope)
        if choice == "always":
            if kind == "bash":
                self._always.update("bash:" + p for p in pats)
            else:
                self._always.add(kind)
        if choice in ("yes", "always"):
            return True
        self._denied.add(tid)
        self._tell(note)
        return False

    def _outside(self, inp: dict) -> str:
        p = os.path.expanduser(str(inp.get("path") or inp.get("directory") or "."))
        try:
            q = Path(p).resolve() if os.path.isabs(p) else (Path(self.root) / p).resolve()
            root = Path(self.root).resolve()
        except (OSError, ValueError):
            return p
        if q == root or root in q.parents:
            return ""
        return "" if any(Path(d) == q or Path(d) in q.parents for d in _skill_read_dirs(self._home())) else str(q)

    def _home(self) -> Path:
        shared = pi_home()
        return shared / "chats" / re.sub(r"[^\w.-]", "_", self.id) if self.id else shared

    def _tell(self, note: str):
        """A note left with a denial reaches Pi as a message before its next step (the gate only says no)."""
        if self._stop:
            return                  # Stop is not a refusal: do not tell Pi "I did not allow that"
        if note and note.strip():
            try:
                self.steer(f"I did not allow that. {note.strip()}")
            except Exception:  # noqa: BLE001
                pass

    def _flush_text(self, has_tools=False):
        if self._text:
            txt = "".join(self._text)
            self._text = []
            self._turn_reply = txt                       # the turn's last reply, for send()'s return value
            self._emit({"type": "assistant", "text": txt, "streamed": True, "has_tools": has_tools})

    # ---------- model ------------------------------------------------------------------------------------------
    def _apply_model(self, initial: str | None = None):
        prov, want = _pi_model(self.model or os.environ.get("MH_MODEL"))
        if want == initial:
            return
        try:
            self._request({"type": "set_model", "provider": prov, "modelId": want}, timeout=30)
        except PiError as e:
            self._emit({"type": "status", "text": f"Pi could not switch to {want}: {e}"})

    def set_model(self, model: str):
        self.model = model or None
        if self._p and self._p.poll() is None and self._alive:
            self._apply_model()

    def current_model(self) -> str:
        prov, m = _pi_model(self.model or os.environ.get("MH_MODEL"))
        from meshpool_harness import providers as PV
        return PV.join("key" if prov == "meshpool" else prov, m)

    # ---------- turns ------------------------------------------------------------------------------------------
    def turn_token(self) -> int:
        """Which turn this is: it changes when send() starts a turn. Pass it to nudge()."""
        return getattr(self, "_turn_seq", 0)

    def nudge(self, text: str, token: int) -> bool:
        """Tell Pi something while turn `token` runs, without a chat message (a note only the model sees). False when
        that turn is over or a later one started: it can never reach another turn."""
        gate = self.__dict__.setdefault("_turn_gate", threading.Lock())
        with gate:
            if token != getattr(self, "_turn_seq", 0) or not getattr(self, "_turn_live", False):
                return False
            self._request({"type": "steer", "message": text}, timeout=30)
            return True

    def _turn_over(self):
        with self.__dict__.setdefault("_turn_gate", threading.Lock()):
            self._turn_live = False

    def send(self, text: str, images: list | None = None, _show: bool = True) -> str:
        over = USAGE.over_budget(self.usage_task) if self.usage_task else None
        if over is not None:                             # the spending limit: an outside agent cannot be refused per call
            self.budget_stopped = True
            self._emit({"type": "error", "text": str(over) + " Raise the limit in Settings or send a message to continue."})
            return ""
        self.budget_stopped = False
        with self.__dict__.setdefault("_turn_gate", threading.Lock()):
            self._turn_seq = getattr(self, "_turn_seq", 0) + 1
            self._turn_live = True
        self._stop = False
        self.start_error = None                          # set when this turn's agent could not start (Session hands off)
        if not self.title:
            self.title = text.strip().splitlines()[0][:80] if text.strip() else "Session"  # first line only
        shown = text.split("\n\n\u200b")[0]          # attached skill text and image OCR go to the agent only
        self._turn_at = len(self.events)                 # the chat before this turn (earlier_context)
        if _show:
            self._emit({"type": "user", "text": shown, "images": [im.get("name", "image") for im in images or []],
                        "plan": self.plan})
        self.save()                                       # the thread survives a restart mid-turn
        body = text
        if self.plan:
            body = ("[Plan mode] Investigate as needed, then reply with a clear, numbered plan for the request below. "
                    "Do not change any files or run commands that change anything; the user will review the plan "
                    "first.\n\n" + text)
        self._text, self._denied = [], set()
        self._turn_reply = ""
        self._turn = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        final = ""
        try:
            self._emit({"type": "model_start"})
            if not (self._p and self._p.poll() is None):
                self._task = text
            self._start_tracked()
            if self._stop:                               # Stop pressed while Pi was starting: send nothing
                raise PiError("stopped")
            if getattr(self, "_carry", False):
                self._carry = False
                from meshpool_harness.harnesses.acp import earlier_context
                body = earlier_context(_before_turn(self)) + body
                self._emit({"type": "status", "text": "Pi could not reopen its own session for this chat, so the "
                                                      "conversation so far went along with your message."})
            self._done.clear()
            cmd = {"type": "prompt", "message": body}
            if images:
                cmd["images"] = [{"type": "image", "data": im["data"], "mimeType": im.get("mime") or "image/png"}
                                 for im in images[:6]]
            self._request(cmd, timeout=120)
            restarted = False
            while not self._done.wait(0.3):
                if self._exited.is_set():
                    if self._stop:
                        break
                    if restarted:
                        raise PiError("Pi exited again during the turn. See runs/harness-logs/pi.log.")
                    # Pi crashed mid-turn. What it did so far is in its own session files: start it again on that
                    # session and let it carry on, once, rather than show the half reply as the answer.
                    restarted = True
                    self._flush_text()
                    self._emit({"type": "status", "text": "Pi stopped unexpectedly in the middle of this task. "
                                                          "Restarting it to carry on..."})
                    try:
                        self._p.wait(timeout=5)
                    except Exception:  # noqa: BLE001
                        pass
                    self.resume = True
                    self._start()
                    if self._stop:                       # Stop pressed while it restarted
                        break
                    self._done.clear()
                    self._request({"type": "prompt", "message": (
                        "[Meshpool] Your process stopped unexpectedly in the middle of this task. Carry on from "
                        "where you left off; do not redo steps that are already done.")}, timeout=120)
                    continue
                if self._stop and time.time() - getattr(self, "_stop_t", 1e18) > 3:
                    # Pi ignored the abort (a tool that does not stop): end it and what it started. The next message
                    # restarts Pi and continues the same session.
                    self.close()
                    self._p, self.resume = None, True
                    break
            self._turn_over()                         # a nudge after this point is refused
            final = "".join(self._text)
            self._flush_text()
            # Text is flushed into the chat at each message boundary, so the buffer is often empty by now: the
            # answer is the turn's last flushed reply (send() returned "" before, to schedules and sub-agents).
            final = final or self._turn_reply
            if self._stop:
                self._emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
            try:
                st = self._request({"type": "get_session_stats"}, timeout=20)
                cu = st.get("contextUsage") or {}
                if cu.get("contextWindow"):
                    self.context = {"used": int(cu.get("tokens") or 0), "size": int(cu["contextWindow"])}
                    self._emit({"type": "context", **self.context})
            except PiError:
                pass
        except PiError as e:
            self._flush_text()
            if self._stop:                               # an error while stopping is just the stop
                self._emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
            else:
                self._emit({"type": "error", "text": str(e)})
        finally:
            self._turn_over()
            for k in self.usage:
                self.usage[k] += self._turn[k]
            self._emit({"type": "turn_end", "turn": dict(self._turn), "session": dict(self.usage),
                        "context": self.context})
            self.save()
        return final

    def _steer_cmd(self, text: str, images: list | None = None):
        cmd = {"type": "steer", "message": _ADDED + text}
        if images:
            cmd["images"] = [{"type": "image", "data": im["data"], "mimeType": im.get("mime") or "image/png"}
                             for im in images[:6]]
        self._request(cmd, timeout=30)

    def steer(self, text: str, images: list | None = None):
        """Add to the request while Pi works: Pi reads it after the current tool calls (RPC steer). While Pi is still
        starting, the message is held and handed over as soon as Pi runs (it was refused with "Pi stopped")."""
        with self._sl:
            up = self._up and self._p is not None and self._p.poll() is None
            if not up:
                self._held.append((text, images))
        if up:
            self._steer_cmd(text, images)
        self._emit({"type": "user", "text": text.split("\n\n\u200b")[0],
                    "images": [im.get("name", "image") for im in images or []], "plan": self.plan, "steer": True})

    def has_steers(self) -> bool:
        """A message added that Pi has not read: held while it started, or queued by Pi after its run ended."""
        return bool(self._held or self._pi_steering)

    def resume_steers(self):
        """Answer the messages has_steers() reports, as a turn of their own (they are in the chat already)."""
        with self._sl:
            if not (self._p and self._p.poll() is None):   # the Pi that queued them is gone: send them ourselves
                self._held += [(t.removeprefix(_ADDED), None) for t in self._pi_steering]
                self._pi_steering = []
            held, self._held = self._held, []
        queued, self._pi_steering = self._pi_steering, []
        if not held and not queued:
            return
        if held:
            body = _ADDED + "\n\n".join(t for t, _ in held)
        else:                                  # Pi holds them and reads them with the next prompt
            body = "[Meshpool] Answer the message I added while you were working."
        self.send(body, [im for _, ims in held for im in (ims or [])] or None, _show=False)

    def stop(self):
        self._stop = True
        self._stop_t = time.time()
        from meshpool_harness.harnesses.acp import stamp_stop
        stamp_stop(self.id)                           # sub-agents in this chat's Meshpool MCP servers stop too
        if self._p and self._p.poll() is None:
            try:
                self._write({"type": "abort"})
            except PiError:
                pass

    def forget_session(self):
        """Undo cut the chat: Pi's own session still holds the undone turns. Set it aside (kept, not deleted) and
        restart Pi, which then gets the chat as it now is (see _carry in _start)."""
        self.close()
        self._p = None
        sess = self.dir / "pi"
        if sess.is_dir() and any(sess.glob("*.jsonl")):
            try:
                sess.rename(self.dir / f"pi-undone-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}")
            except OSError:
                for f in sess.glob("*.jsonl"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
        self.resume = False

    def reset(self):
        self.events = []
        self.title = ""

    def close(self):
        p = self._p
        if not p:
            return
        _kill_tree(p)                                 # commands it started must not outlive the thread
        try:
            p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    def save(self):
        if getattr(self, "deleted", False):           # the user deleted this thread; never bring it back
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            SS.write_snapshot(self.dir, {
                "id": self.id, "workdir": str(self.root), "title": self.title, "created": self.created,
                "updated": time.time(), "harness": self.manifest.get("id"), "kind": self.manifest.get("mode", "code"),
                "usage": self.usage, "model": self.model, "events": self.events[-2000:]})
        except OSError:
            pass

    @classmethod
    def resume(cls, data: dict, manifest: dict, **kw) -> "PiAgent":
        a = cls(manifest, data["workdir"], session_id=data["id"], **kw)
        a.title = data.get("title", "")
        a.created = data.get("created", a.created)
        a.events = data.get("events") or []
        a.usage = data.get("usage") or a.usage
        a.model = data.get("model")
        a.resume = True
        return a
