"""Meshpool web app: the local server behind the desktop window and `meshpool ui`.

Standard library only. Binds to 127.0.0.1. Every API call must carry the per-launch token that is baked into the
page, so other websites open in the same browser cannot drive it. Chats run in ui/agent_api.py.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import uuid
import zipfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from meshpool_harness import config as C
from meshpool_harness import harnesses as HR
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import workbench as W

APP = C.APP
HERE = Path(__file__).resolve().parent
RUNS = C.runs_dir()
UPLOADS = RUNS / "_uploads"
EXTRA_PREFIXES = ("/api/memory", "/api/xray", "/api/insights", "/api/work", "/api/summaries", "/api/receipt",
                  "/api/checks", "/api/firstrun", "/api/away", "/api/scheduler", "/api/fs", "/api/browser/",
                  "/api/worktree")

TOKEN = secrets.token_urlsafe(24)
UI_VER = secrets.token_hex(4)
DESKTOP = False
LAST_PING = [time.time()]
HTTPD = [None]
NATIVE = [False]                    # True while the interface is shown in the app's own window
ON_SHOW: list = []                  # called when a second launch asks this instance to come forward
BOOT = uuid.uuid4().hex                 # identifies this process: the page tells the restarted app from the old one
ON_QUIT: list = []                  # called when the app quits (closes the native window)
LOCK = threading.Lock()


# ---------- settings -----------------------------------------------------------------------------------------------

def get_settings() -> dict:
    from meshpool_harness import providers as PV
    vals = C.read()
    fields = []
    for key, label, default, help_ in C.FIELDS:
        v = vals.get(key, "")
        item = {"key": key, "label": label, "default": default, "help": help_}
        if key in C.SECRET:
            item["set"] = bool(v)
            item["hint"] = ("ends in " + v[-4:]) if len(v) >= 8 else ("set" if v else "")
            item["value"] = ""
        else:
            item["value"] = v
        fields.append(item)
    from meshpool_harness.ui import desktop as Dk
    return {"path": str(C.config_path()), "fields": fields, "system": C.system_check(),
            "desktop": DESKTOP, "home": str(C.home()), "cli": Dk.cli_status(),
            "default_project": str(Dk.default_project()),
            "native": NATIVE[0], "platform": sys.platform, "shells": W.shells(), "user_home": str(Path.home()),
            "has_key": bool(PV.key()), "key_name": PV.key_name(),
            "ready": HR.ready(), "provider": HR.provider(), "providers": HR.PROVIDER_NAMES,
            "signed_in": {p: HR.signed_in(w) for p, w in HR.LOGINS.items()}}


def _key_follows_host(vals: dict, typed_key: str = ""):
    """A saved key belongs to its endpoint: moving to another host needs that host's key, or the saved one is sent
    to the new host."""
    from meshpool_harness import providers as PV
    new = vals.get("MESHPOOL_BASE_URL")
    if new and PV.host(new) and PV.host(new) != PV.host(PV.key_base()) and PV.key() \
            and not (vals.get("MESHPOOL_API_KEY") or typed_key):
        raise ValueError(f"Enter the API key for {PV.host(new)} too: your saved key is for {PV.host(PV.key_base())} "
                         "and is not sent anywhere else.")


def save_settings(body: dict) -> dict:
    allowed = {f[0] for f in C.FIELDS}
    vals = {}
    for k, v in (body.get("values") or {}).items():
        if k not in allowed:
            continue
        v = re.sub(r"[\r\n]", "", str(v)).strip()
        if k in C.SECRET:                # accept a key the way people paste it: quoted, or with "Bearer " in front
            v = C.clean_key(v)           # quotes, "Bearer ", line breaks, a pasted zero-width space
        if k in C.SECRET and v == "":
            continue                     # blank key field = keep the saved key
        vals[k] = v
    if vals.get("MESHPOOL_BASE_URL"):
        u = urlparse(vals["MESHPOOL_BASE_URL"])
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError("The server address must start with http:// or https:// and name a host.")
    from meshpool_harness import providers as PV
    new = vals.get("MESHPOOL_BASE_URL")
    from meshpool_harness import provider_setup as PS
    if new and not vals.get("MESHPOOL_API_KEY") and not PS._same_endpoint(new, PV.key_base()) and PV.key() \
            and PV.keyless_ok(new):
        vals["MESHPOOL_API_KEY"] = ""    # a local server needs no key: the old endpoint's key goes, not to this server
    _key_follows_host(vals)
    from meshpool_harness import browser_use as BU
    BU.validate(vals)                    # a browser setting that cannot work is refused here, with the reason
    if vals.get("GITHUB_TOKEN"):
        try:
            from meshpool_harness.ui import agent_api as _A
            _A.mcp_save({"name": "github", "enabled": True})
        except Exception:
            pass
    if vals:
        C.write(vals)
        for k, v in vals.items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
    return get_settings()


def assistant_view() -> dict:
    from meshpool_harness import assistant as AS
    rows = [dict(t, describe=AS.describe(t)) for t in AS.tasks()]
    return {"instructions": AS.instructions(), "memory": AS.memory(), "settings": AS.settings(), "tasks": rows}


def assistant_action(b: dict) -> dict:
    from meshpool_harness import assistant as AS
    act = b.get("action")
    if act == "instructions":
        AS.set_instructions(b.get("text", ""))
    elif act == "memory":
        AS.set_memory(b.get("text", ""))
    elif act == "settings":
        AS.save_settings(**{k: b[k] for k in ("memory_on", "instructions_on", "learn_from_edits", "insights_on", "quiet_hours") if k in b})
    elif act == "task_add":
        AS.add_task(b.get("name", ""), b.get("prompt", ""), b.get("when", "daily"), b.get("time", "09:00"),
                    b.get("day", "mon"), b.get("at", ""), int(b.get("minutes") or 60), b.get("folder", ""),
                    b.get("harness", ""), b.get("kind", "cowork"), bool(b.get("only_on_change")))
    elif act == "task_update":
        AS.update_task(b.get("id", ""), **{k: b.get(k) for k in ("name", "prompt", "when", "time", "day", "at",
                                                                   "minutes", "folder", "harness", "kind", "enabled",
                                                                   "only_on_change")})
    elif act == "task_delete":
        AS.delete_task(b.get("id", ""))
    elif act == "task_run":
        t = next((x for x in AS.tasks() if x["id"] == b.get("id")), None)
        if not t:
            raise ValueError("That task is gone.")
        return dict(assistant_view(), thread=A.run_scheduled(t))
    else:
        raise ValueError("Unknown action.")
    return assistant_view()


def test_connection(values: dict | None = None, fresh: bool = False) -> dict:
    """Check the saved key and endpoint, or (with values) a key and endpoint before they are saved. Typed values go
    straight to the check: saved settings win over the environment, so setting them there would test the saved
    endpoint and send the typed key to it."""
    C.load_into_env(override=True)
    from meshpool_harness import providers as PV
    v = {k: str(x).strip() for k, x in (values or {}).items() if str(x or "").strip()}
    if v.get("MESHPOOL_API_KEY"):
        v["MESHPOOL_API_KEY"] = C.clean_key(v["MESHPOOL_API_KEY"])
    try:
        _key_follows_host(v)
    except ValueError as e:
        return {"ok": False, "detail": str(e), "endpoint": v.get("MESHPOOL_BASE_URL", "")}
    from meshpool_harness import provider_setup as PS
    base_in = v.get("MESHPOOL_BASE_URL") or PV.key_base()
    try:
        base = PS.normalise_base(base_in)
    except PS.SetupError as e:
        return {"ok": False, "detail": str(e), "endpoint": base_in}
    same = PS._same_endpoint(base, PV.key_base()) and not fresh   # fresh: nothing saved applies (onboarding)
    key = v.get("MESHPOOL_API_KEY") or (PV.key() if same else "")
    if not key and not PV.keyless_ok(base):
        return {"ok": False, "detail": f"No API key {'saved yet' if same else 'entered for ' + PV.host(base)}.",
                "endpoint": base}
    if PV.key_problem(key):
        return {"ok": False, "detail": PV.key_problem(key)}
    model = PV.split(v.get("MH_MODEL") or os.environ.get("MH_MODEL") or PV.DEFAULT_MODEL)[1] or PV.azure_deployment(base)
    hdr = v.get("MH_AUTH_HEADER", PV._cfg("MH_AUTH_HEADER") if same else "")
    sch = v.get("MH_AUTH_SCHEME", PV._cfg("MH_AUTH_SCHEME") if same else "").lower()
    ver = v.get("MESHPOOL_AZURE_API_VERSION", PV._cfg("MESHPOOL_AZURE_API_VERSION") if same else "")
    if "api-version=" in base_in and not ver:
        ver = PV.azure_version(base_in)
    wire = (v.get("MH_API_WIRE") or (PV.key_wire() if same else "")).lower()
    if wire not in ("openai", "anthropic"):
        wire = "anthropic" if PV.host(base).endswith("anthropic.com") else "openai"
    t0 = time.time()
    st, data, err = PS.probe_chat(base, key, model, wire, hdr, sch, ver)
    secs = round(time.time() - t0, 1)
    if err or st not in (200, 429) or (st == 200 and not PS._is_reply(wire, data)):
        why = (PS._net_error(err, base) if err else PS._not_reply_msg(data) if st == 200
               else PS.explain(st, data, base=base, model=model, version=ver))
        return {"ok": False, "detail": why[:400], "model": model, "seconds": secs, "endpoint": base,
                "status": 0 if err else st}
    txt = ""
    if isinstance(data, dict):
        try:
            txt = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        except (AttributeError, IndexError, TypeError):
            txt = ""
    return {"ok": True, "detail": ("model replied: " + repr(txt.strip()[:40])) if txt.strip() else
            ("connected" + (" (rate limited right now)" if st == 429 else "")), "model": model, "seconds": secs,
            "endpoint": base, "wire": wire, "api_version": ver}


def onboard(body: dict) -> dict:
    """First-run key setup in one step: check the key (the typed address normalised, the /v1 variant tried when the
    first answers 404), and only when it works write the key, the address, the model and the provider in ONE write."""
    from meshpool_harness import provider_setup as PS
    which = "custom" if body.get("provider") == "custom" else "meshpool"
    key = C.clean_key(str(body.get("key") or ""))
    if not key:
        return {"ok": False, "detail": "Paste your API key first."}
    model = str(body.get("model") or "").strip()
    try:
        base = (PS.normalise_base(str(body.get("base_url") or "")) if which == "custom"
                else "https://llm.meshpool.ai/v1")
    except PS.SetupError as e:
        return {"ok": False, "detail": str(e)}
    if which == "custom" and not model:
        return {"ok": False, "detail": "Enter a model the provider serves."}
    r, used = {"ok": False, "detail": ""}, base
    for cand in PS._candidates(base):
        vals = {"MESHPOOL_API_KEY": key, "MESHPOOL_BASE_URL": cand}
        if model:
            vals["MH_MODEL"] = model
        r, used = test_connection(vals, fresh=True), cand
        if r.get("ok") or r.get("status") != 404:
            break
    if not r.get("ok"):
        return {"ok": False, "detail": r.get("detail") or "The check failed.", "endpoint": used}
    vals = {"MESHPOOL_API_KEY": key, "MESHPOOL_BASE_URL": used if which == "custom" else "",   # default: no saved address
            **HR.provider_values(which)}
    if which == "custom":
        vals["MH_MODEL"] = model
    from meshpool_harness import providers as PV
    vals.update(MH_API_WIRE=r.get("wire") or "openai", MH_AUTH_HEADER="", MH_AUTH_SCHEME="",
                MESHPOOL_AZURE_API_VERSION=r.get("api_version") or "")      # nothing of the previous endpoint stays
    if (C.read().get("MH_SUBAGENT_MODEL") or "").strip() and not PS._same_endpoint(used, PV.key_base()):
        vals["MH_SUBAGENT_MODEL"] = ""                   # named a model of the old endpoint
    C.write(vals)
    HR.apply_env(vals)
    return {"ok": True, "base_url": used}


def save_upload(name: str, data: bytes) -> dict:
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip() or "file"
    d = UPLOADS / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    p = d / safe
    p.write_bytes(data)
    return {"path": str(p), "name": safe, "size": len(data)}


# ---------- HTTP ---------------------------------------------------------------------------------------------------

def _disposition(kind: str, name: str) -> str:
    """Headers must be Latin-1: "图表 – chart.png" dropped the connection. ASCII fallback plus the UTF-8 name (RFC 6266)."""
    from urllib.parse import quote
    plain = re.sub(r'[^\x20-\x7e]|["\\]', "_", name)
    return f"{kind}; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


_RESTART_FAILED = "Meshpool was updated but could not restart itself. Quit and open it again."


def _restart_or_recover(UP, AU) -> None:
    """Restart into the new code. The code on disk is already new, so if the restart itself fails (it could not start the
    new process) the app stays up: reopen the run gate and tell the user to restart it by hand."""
    try:
        UP.restart([A.close_all, *ON_QUIT])
    except Exception:  # noqa: BLE001
        A.resume()
        try:
            AU.install_finished(False, _RESTART_FAILED)
        except Exception:  # noqa: BLE001
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = "Meshpool"

    def log_message(self, *a):          # quiet console
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code: int, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0:
            raise ValueError("Bad request.")
        if n > 260 * 1024 * 1024:
            raise ValueError("File is larger than 260 MB.")
        return self.rfile.read(n) if n else b""

    def _json(self) -> dict:
        raw = self._body()
        d = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(d, dict):
            raise ValueError("The request must be a JSON object.")
        return d

    def _extra(self, u):
        from meshpool_harness.ui import routes_extra as RX
        try:
            return self._send(*RX.handle(self.command, u, self._body() if self.command == "POST" else b""))
        except (ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile) as e:
            return self._send(400, {"error": _client_error(e)})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # noqa: BLE001 - keep the app alive, show the cause
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _company(self, u):
        from meshpool_harness.ui import company_api as CO
        try:
            return self._send(*CO.handle(self.command, u, self._body() if self.command == "POST" else b""))
        except (ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile) as e:
            return self._send(400, {"error": _client_error(e)})
        except Exception as e:  # noqa: BLE001 - keep the app alive, show the cause
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _event_stream(self, sid: str, since: int):
        """Server-Sent Events for one chat: a JSON view of whatever is new, about 30 times a second at most, until the
        chat is no longer working (then an `end` event). The page falls back to polling if this fails."""
        A.get(sid)                                    # unknown chat: KeyError -> 404 before any stream starts
        self.send_response(200)
        for k, v in (("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-store"),
                     ("X-Accel-Buffering", "no"), ("X-Content-Type-Options", "nosniff")):
            self.send_header(k, v)
        self.end_headers()
        self.close_connection = True
        busy = ("starting", "working", "waiting")
        cursor, last_state, beat, idle_since = since, None, time.time(), None

        def put(text: str):
            self.wfile.write(text.encode("utf-8"))
            self.wfile.flush()
        try:
            put("retry: 1500\n\n")
            while True:
                try:
                    s = A.get(sid)
                    n, state = len(s.events), s.state
                except Exception:  # noqa: BLE001 - a chat whose folder vanished: send what there is, then stop
                    put("data: " + json.dumps(A.view(sid, cursor), default=str) + "\n\n")
                    put("event: end\ndata: {}\n\n")
                    return
                if n > cursor or state != last_state:
                    with s.lock:
                        fresh = s.events[cursor:]
                    if fresh and state == last_state and all(e.get("type") == "text_delta" for e in fresh):
                        v = {"id": sid, "state": state, "epoch": s.epoch, "events": fresh, "total": cursor + len(fresh),
                             "turn_started": getattr(s, "t_turn", None) if state in ("working", "waiting") else None}
                    else:
                        v = A.view(sid, cursor)
                    cursor, last_state, beat = v["total"], state, time.time()
                    put("data: " + json.dumps(v, default=str) + "\n\n")
                    idle_since = None if state in busy else (idle_since or time.time())
                elif state not in busy:
                    put("event: end\ndata: {}\n\n")
                    return
                elif time.time() - beat > 10:
                    put(": keep-alive\n\n")
                    beat = time.time()
                if idle_since and time.time() - idle_since > 0.4:
                    put("event: end\ndata: {}\n\n")
                    return
                with s.lock:                      # wake the moment an event arrives; 0.25 s at most for a state change
                    if len(s.events) <= cursor and s.state == last_state:
                        s.changed.wait(0.25)
                time.sleep(0.01)                  # let a burst of text_delta pile up into one frame
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            html = (HERE / "index.html").read_text(encoding="utf-8").replace("__MESHPOOL_TOKEN__", TOKEN) \
                .replace("__MESHPOOL_DESKTOP__", "true" if DESKTOP else "false").replace("__MESHPOOL_VER__", UI_VER)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "frame-ancestors 'none'"})
        m = re.fullmatch(r"/ui/(app\.js|app\.css|shell\.css|shell\.js|composer\.js|pages\.js|code\.js|company\.js|projects\.js|settings2\.js|preview\.js|editor\.js|memory\.js|memory\.css|insights\.js|insights\.css|yourwork\.js|yourwork\.css|firstrun\.js|chat010\.js|home010\.js|worktree\.js)", u.path)
        if m:
            ctype = "text/javascript; charset=utf-8" if m.group(1).endswith(".js") else "text/css; charset=utf-8"
            return self._send(200, (HERE / m.group(1)).read_bytes(), ctype)
        m = re.fullmatch(r"/ui/vendor/((?:fonts/)?[A-Za-z0-9_.-]+\.(?:js|css|woff2))", u.path)
        if m and (HERE / "vendor" / m.group(1)).is_file():           # Mermaid and KaTeX, bundled so diagrams and maths work offline
            ct = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "woff2": "font/woff2"}[m.group(1).rsplit(".", 1)[1]]
            return self._send(200, (HERE / "vendor" / m.group(1)).read_bytes(), ct, {"Cache-Control": "max-age=86400"})
        if u.path == "/oauth/callback":          # the browser comes back here after signing in to a connector
            from meshpool_harness import mcp_apps as CN
            q = parse_qs(u.query)
            try:
                name = CN.finish(q.get("state", [""])[0], q.get("code", [None])[0], q.get("error", [None])[0])
                msg, ok = f"{name} is connected. You can close this tab and go back to Meshpool.", True
                cid = CN.LAST_FINISHED[0]
                r = CN.check(cid) if cid else {"ok": True, "tools": []}
                if r["ok"]:
                    msg = f"{name} is connected ({len(r['tools'])} tools). You can close this tab and go back to Meshpool."
                else:
                    msg, ok = f"{name} signed in, but the connection test failed: {r['error']}", False
            except Exception as e:  # noqa: BLE001
                msg, ok = str(e), False
            page = ("<!doctype html><meta charset=utf-8><title>Meshpool</title><body style=\"font:15px -apple-system,"
                    "Segoe UI,sans-serif;display:grid;place-items:center;height:90vh;color:#0B131F\"><div style=\"max-width:"
                    "460px\"><h2 style=\"margin:0 0 8px\">" + ("Connected" if ok else "Sign-in did not finish") +
                    "</h2><p>" + msg.replace("<", "&lt;") + "</p></div>")
            return self._send(200 if ok else 400, page.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/healthz":
            return self._send(200, {"ok": True, "app": "meshpool", "pid": os.getpid(), "boot": BOOT})
        if u.path == "/favicon.svg":
            return self._send(200, (HERE / "favicon.svg").read_bytes(), "image/svg+xml")
        m = re.fullmatch(r"/brand/((?:fonts/)?[\w.-]+\.(png|woff2))", u.path)
        if m and (HERE / "brand" / m.group(1)).is_file():
            return self._send(200, (HERE / "brand" / m.group(1)).read_bytes(),
                              "image/png" if m.group(2) == "png" else "font/woff2",
                              {"Cache-Control": "max-age=86400"})
        if u.path == "/raw":                          # a project file, for image and PDF previews
            q = parse_qs(u.query)
            if q.get("t", [""])[0] != TOKEN:
                return self._send(403, {"error": "forbidden"})
            try:
                from meshpool_harness.ui import files_api as FA
                p = FA.safe(q.get("root", [""])[0], q.get("path", [""])[0])[1]
            except (ValueError, OSError):
                return self._send(404, {"error": "not found"})
            if not p.is_file() or p.stat().st_size > 60_000_000:
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if not (ctype.startswith("image/") or ctype == "application/pdf"):
                return self._send(415, {"error": "only images and PDFs are served"})
            return self._send(200, p.read_bytes(), ctype, {"Content-Disposition": _disposition("inline", p.name),
                                                          "X-Content-Type-Options": "nosniff",
                                                          "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"})
        if u.path.startswith("/files/"):
            q = parse_qs(u.query)
            if q.get("t", [""])[0] != TOKEN:
                return self._send(403, {"error": "forbidden"})
            parts = unquote(u.path[len("/files/"):]).split("/", 1)
            if len(parts) != 2:
                return self._send(404, {"error": "not found"})
            base = (RUNS / parts[0] / "out").resolve()
            p = (base / parts[1]).resolve()
            if base not in p.parents or not p.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            disp = "inline" if ctype.startswith(("image/", "text/")) or ctype == "application/pdf" else "attachment"
            hdr = {"Content-Disposition": _disposition(disp, p.name)}
            if ctype in ("text/html", "image/svg+xml", "application/xhtml+xml"):
                hdr["Content-Security-Policy"] = "sandbox"       # a page an agent wrote must not reach the app's token
            return self._send(200, p.read_bytes(), ctype, hdr)
        m = re.fullmatch(r"/api/agent/([\w-]+)/events", u.path)
        if m:                                         # a chat's events pushed as they happen (EventSource cannot set headers)
            q = parse_qs(u.query)
            if q.get("t", [""])[0] != TOKEN:
                return self._send(403, {"error": "forbidden"})
            try:
                return self._event_stream(m.group(1), max(0, int(q.get("since", ["0"])[0])))
            except KeyError:
                return self._send(404, {"error": "Not found."})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 - e.g. the chat's folder is gone: the page polls instead
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        if u.path == "/api/preview":                  # a file's preview: JSON, or one page as PNG (an <img> cannot send the header)
            q = parse_qs(u.query)
            if q.get("t", [""])[0] != TOKEN and self.headers.get("X-Meshpool-Token") != TOKEN:
                return self._send(403, {"error": "forbidden"})
            from meshpool_harness.ui import previews as PV
            try:
                if "page" in q:
                    png = PV.page_png(q.get("root", [""])[0], q.get("path", [""])[0], int(q["page"][0] or 0),
                                      int(q.get("w", [PV.MAX_WIDTH])[0] or PV.MAX_WIDTH))
                    return self._send(200, png, "image/png")
                return self._send(200, PV.meta(q.get("root", [""])[0], q.get("path", [""])[0]))
            except (ValueError, OSError, KeyError) as e:
                return self._send(400, {"error": str(e).strip("'\"") or "Could not preview this file."})
            except (BrokenPipeError, ConnectionResetError):
                return None
            except Exception as e:  # noqa: BLE001 - a damaged file must not take the app down
                return self._send(500, {"error": f"Could not preview this file ({type(e).__name__}: {str(e)[:200]})."})
        if self.headers.get("X-Meshpool-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        if u.path.startswith("/api/company"):        # Settings > Company, the company look for decks
            return self._company(u)
        if u.path.startswith(EXTRA_PREFIXES):        # 0.0.10: memory page, insights, summaries, receipts
            return self._extra(u)
        try:
            if u.path == "/api/settings":
                return self._send(200, get_settings())
            if u.path in ("/api/provider", "/api/provider/logins"):      # key endpoint setup (provider_setup.py)
                from meshpool_harness import provider_setup as PS
                return self._send(200, PS.current() if u.path == "/api/provider" else {"logins": PS.logins()})
            if u.path == "/api/rules":
                from meshpool_harness.core import permrules as PR
                return self._send(200, {**PR.list_rules(PR.user_path()), "path": str(PR.user_path())})
            if u.path == "/api/assistant":
                return self._send(200, assistant_view())
            if u.path == "/api/import-agents":        # other agents on this computer, with a preview of every item
                from meshpool_harness import agent_import as AI
                return self._send(200, AI.scan())
            if u.path == "/api/import":
                from meshpool_harness import importer as IM
                return self._send(200, IM.scan())
            if u.path == "/api/import-context":
                from meshpool_harness import importer as IM
                return self._send(200, {"prompt": IM.context_prompt(), "links": IM.EXPORT_LINKS})
            if u.path == "/api/import-chats":         # Claude Code and Codex sessions found on this computer
                from meshpool_harness import chat_import as CI
                return self._send(200, CI.scan_local())
            if u.path == "/api/about":
                import meshpool_harness as MH

                def rd(name):
                    for base in (C.APP, C.APP.parent):      # packaged: beside the app; from source: the repo root
                        try:
                            return (base / name).read_text(encoding="utf-8")
                        except OSError:
                            continue
                    return ""
                return self._send(200, {"version": MH.__version__, "author": MH.__author__,
                                        "third_party": rd("THIRD_PARTY_NOTICES.md")})
            if u.path == "/api/agent":
                return self._send(200, {"sessions": A.listing()})
            if u.path == "/api/styles":
                from meshpool_harness import styles as ST
                return self._send(200, ST.listing())
            if u.path == "/api/mcp":
                return self._send(200, A.mcp_info())
            if u.path == "/api/mcp-library":
                from meshpool_harness import mcp_library as ML
                return self._send(200, ML.api_view())
            if u.path == "/api/harnesses":
                return self._send(200, A.harness_info())
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/api/models":
                return self._send(200, W.models(refresh=q.get("refresh") == "1"))
            if u.path == "/api/projects":
                return self._send(200, {"projects": W.projects(), "default": str(_default_project())})
            if u.path in ("/api/profile", "/api/capabilities", "/api/spend", "/api/data", "/api/browser-use",
                          "/api/computer-status"):
                from meshpool_harness.ui import settings_v2 as SV
                if u.path == "/api/browser-use":
                    return self._send(200, SV.browser_use())
                if u.path == "/api/computer-status":
                    return self._send(200, SV.computer_status())
                if u.path == "/api/profile":
                    return self._send(200, SV.profile())
                if u.path == "/api/capabilities":
                    return self._send(200, {"features": SV.capabilities()})
                if u.path == "/api/spend":
                    return self._send(200, {"key": SV.key_spend(q.get("fresh") == "1"), "days": SV.usage_by_day(),
                                            "billing_url": SV.BILLING_URL})
                return self._send(200, SV.data_info())
            m = re.fullmatch(r"/api/pj(?:/(p_[0-9a-f]{10}))?", u.path)
            if m:                                                  # projects: name, instructions, knowledge, memory
                from meshpool_harness import project_store as PS
                return self._send(200, PS.detail(m.group(1)) if m.group(1) else {"projects": PS.list_projects()})
            m = re.fullmatch(r"/api/pj/(p_[0-9a-f]{10})/file", u.path)
            if m:                                                  # a knowledge file, for the preview
                from meshpool_harness import project_store as PS
                fp = PS.knowledge_path(m.group(1), q.get("name", ""))
                ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
                if ctype in ("text/html", "image/svg+xml", "application/xhtml+xml"):
                    ctype = "text/plain"
                return self._send(200, fp.read_bytes(), ctype, {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"})
            if u.path == "/api/skills":
                from meshpool_harness import skills as SK
                return self._send(200, {"skills": SK.load_all(), "folder": str(SK.folder())})
            if u.path == "/api/plugins":
                from meshpool_harness import plugins as PL
                return self._send(200, PL.api_view())
            if u.path == "/api/skill":
                from meshpool_harness import skills as SK
                sk = SK.get(q.get("id", ""))
                if not sk:
                    raise KeyError(q.get("id"))
                return self._send(200, sk | {"text": Path(sk["path"]).read_text(encoding="utf-8", errors="replace")})
            if u.path == "/api/files":
                return self._send(200, W.list_dir(q.get("root", ""), q.get("path", "")))
            if u.path == "/api/file":
                return self._send(200, W.read_file(q.get("root", ""), q.get("path", "")))
            if u.path == "/api/git":
                return self._send(200, W.git_status(q.get("root", "")))
            if u.path == "/api/connectors":
                from meshpool_harness import mcp_apps as CN
                C.load_into_env(override=True)
                return self._send(200, {"connectors": CN.listing()})
            if u.path == "/api/git/changes":                    # local git only: shows before GitHub answers
                q = parse_qs(u.query)
                return self._send(200, W.branch_changes(q.get("root", [""])[0], q.get("base", [""])[0]))
            if u.path == "/api/git/ci":
                return self._send(200, W.git_ci(parse_qs(u.query).get("root", [""])[0]))
            if u.path == "/api/git/prs":
                return self._send(200, W.pr_list(parse_qs(u.query).get("root", [""])[0]))
            if u.path == "/api/git/pr/file":                # file previews: served sandboxed, never run as a page
                q = parse_qs(u.query)
                data, ctype = W.pr_file(q.get("root", [""])[0], int(q.get("n", ["0"])[0] or 0), q.get("path", [""])[0],
                                        q.get("side", ["head"])[0])
                return self._send(200, data, ctype, {"Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"})
            if u.path == "/api/git/pr":
                q = parse_qs(u.query)
                return self._send(200, W.pr_detail(q.get("root", [""])[0], int(q.get("n", ["0"])[0] or 0)))
            if u.path == "/api/github/repos":
                return self._send(200, W.github_repos(parse_qs(u.query).get("q", [""])[0]))
            if u.path == "/api/github/branches":
                return self._send(200, W.github_branches(parse_qs(u.query).get("repo", [""])[0]))
            m = re.fullmatch(r"/api/session/([\w-]+)/(panel|pr)", u.path)
            if m:                                           # the session side panel: local facts, then GitHub's
                out = (W.session_panel if m.group(2) == "panel" else W.session_pr)(m.group(1))
                if m.group(2) == "pr" and isinstance(out, dict) and isinstance(out.get("pr"), dict):
                    p_ = out["pr"]                         # remembered, so the session row can show it without asking GitHub
                    A.write_meta(m.group(1), pr={k: p_.get(k) for k in ("number", "title", "draft", "additions", "deletions", "url")} | {"repo": out.get("repo", "")})
                return self._send(200, out)
            if u.path == "/api/usage":
                return self._send(200, W.usage())
            if u.path == "/api/search":
                return self._send(200, {"hits": A.search(q.get("q", ""))})
            if u.path.startswith("/api/hub/"):             # the shell's pages: artifacts, scheduled, search, account
                from meshpool_harness.ui import hub as HUB
                what = u.path[len("/api/hub/"):]
                if what == "artifacts":
                    return self._send(200, HUB.artifacts())
                if what == "scheduled":
                    return self._send(200, HUB.scheduled())
                if what == "search":
                    return self._send(200, HUB.search(q.get("q", ""), q.get("tab", "all")))
                if what == "account":
                    return self._send(200, HUB.account())
                raise KeyError(what)
            m = re.fullmatch(r"/api/term/(\w+)", u.path)
            if m:
                return self._send(200, W.term_view(m.group(1), int(q.get("since", "0"))))
            m = re.fullmatch(r"/api/agent/([\w-]+)/diff", u.path)
            if m:
                s_ = A.get(m.group(1))
                turn = int(q["turn"]) if q.get("turn") else None
                return self._send(200, s_.cp.diff(turn) if s_.cp else {"files": [], "patch": "", "note": ""})
            m = re.fullmatch(r"/api/agent/([\w-]+)", u.path)
            if m:
                since = int(parse_qs(u.query).get("since", ["0"])[0])
                return self._send(200, A.view(m.group(1), since))
        except KeyError:
            return self._send(404, {"error": "Not found."})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            return None                    # the page went away mid-answer; nothing left to tell it
        except Exception as e:  # keep the app alive, show the cause
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        if u.path == "/show":                  # a second launch of the app asks this one to come to the front
            if "json" not in (self.headers.get("Content-Type") or "").lower():
                return self._send(403, {"error": "forbidden"})     # a web page cannot send this without a preflight
            shown = any(fn() for fn in list(ON_SHOW))
            return self._send(200, {"ok": True, "native": shown})
        if self.headers.get("X-Meshpool-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        if u.path.startswith("/api/company"):        # Settings > Company, the company look for decks
            return self._company(u)
        if u.path.startswith(EXTRA_PREFIXES):        # 0.0.10: memory page, insights, summaries, receipts
            return self._extra(u)
        try:
            if u.path == "/api/settings":
                return self._send(200, save_settings(self._json()))
            if u.path in ("/api/provider/test", "/api/provider/save", "/api/provider/use"):
                from meshpool_harness import provider_setup as PS
                j = self._json() or {}
                if u.path.endswith("/test"):
                    try:
                        return self._send(200, PS.test(j.get("base_url", ""), j.get("api_key"), j.get("wire", "auto"),
                                                       j.get("env", ""), j.get("model", ""), j.get("auth_header", ""),
                                                       j.get("auth_scheme", ""), j.get("api_version", "")))
                    except PS.SetupError as e:
                        return self._send(200, {"ok": False, "wire": "", "models": [], "latency_ms": 0,
                                                "base_url": "", "error": str(e)})
                    except Exception as e:           # a test must answer, never a 500
                        return self._send(200, {"ok": False, "wire": "", "models": [], "latency_ms": 0, "base_url": "",
                                                "error": f"The test could not finish: {type(e).__name__}: {e}"})
                if u.path.endswith("/save"):
                    return self._send(200, dict(PS.save(j.get("base_url", ""), j.get("api_key"), j.get("wire", "auto"),
                                                        j.get("default_model", ""), bool(j.get("allow_custom")),
                                                        j.get("env", ""), bool(j.get("confirm_env")),
                                                        j.get("auth_header"), j.get("auth_scheme"),
                                                        j.get("api_version")), ok=True))
                return self._send(200, PS.use(j.get("kind", "")))
            if u.path == "/api/provider":            # the provider picked at sign-up or in Settings
                HR.use_provider((self._json() or {}).get("provider", ""))
                return self._send(200, get_settings())
            if u.path == "/api/onboard":
                return self._send(200, onboard(self._json() or {}))
            if u.path == "/api/test":
                return self._send(200, test_connection((self._json() or {}).get("values")))
            if u.path == "/api/update/check":
                from meshpool_harness import autoupdate as AU
                from meshpool_harness import updater as UP
                r = UP.check(force=bool(self._json().get("force")))
                if r.get("ok") and r.get("newer") and AU.settings()["auto_download"]:
                    AU.kick()                      # a check by hand also starts the background download
                return self._send(200, r)
            if u.path == "/api/update/status":
                from meshpool_harness import autoupdate as AU
                return self._send(200, AU.status(consume=True))
            if u.path == "/api/update/settings":
                from meshpool_harness import autoupdate as AU
                return self._send(200, AU.save_settings(self._json() or {}))
            if u.path == "/api/update/download":
                from meshpool_harness import autoupdate as AU
                AU.kick(manual=True)
                return self._send(200, {"ok": True})
            if u.path == "/api/update/apply":     # "Restart to update": the helper installs it after this app quits
                from meshpool_harness import autoupdate as AU
                from meshpool_harness import updater as UP
                gate_open = False
                try:
                    with UP.install_lock():
                        if not A.quiesce():
                            return self._send(200, {"ok": False, "error": "A task is still running. Restart when it finishes."})
                        gate_open = True
                        r = AU.begin_apply()
                        if not r.get("ok"):
                            return self._send(200, r)
                        if r.get("mode") == "code":
                            self._send(200, {"ok": True, "mode": "code", "restarting": True, "boot": BOOT})
                            gate_open = False          # the gate stays closed: the process is going away
                            threading.Thread(target=_restart_or_recover, args=(UP, AU), daemon=True).start()
                            return
                        self._send(200, r)
                        gate_open = False
                        threading.Thread(target=AU.finish_quit, args=([A.close_all, *ON_QUIT],), daemon=True).start()
                        return
                except UP.UpdateBusy as e:
                    return self._send(200, {"ok": False, "error": str(e)})
                finally:
                    if gate_open:
                        A.resume()
            if u.path == "/api/update/install":
                from meshpool_harness import autoupdate as AU
                from meshpool_harness import updater as UP
                gate_open = False
                marked = applied = False
                stage = ""
                try:
                    with UP.install_lock():
                        r = UP.install()
                        if r.get("action") != "staged":
                            return self._send(200, r)
                        stage = r["stage"]
                        AU.mark_installing(r["tag"], r["latest"], "code")
                        marked = True
                        if not A.quiesce():
                            UP.discard_stage(stage)
                            msg = "A task is still running. Update when it finishes."
                            AU.install_finished(False, msg)
                            return self._send(200, {"ok": False, "error": msg})
                        gate_open = True
                        r2 = UP.apply_staged(r)
                        if not r2.get("ok"):
                            AU.install_finished(False, r2.get("error", ""))
                            return self._send(200, r2)        # the finally below reopens the gate
                        applied = True
                        self._send(200, {"ok": True, "action": "restarting", "boot": BOOT})
                        gate_open = False                  # the gate stays closed: the process is going away
                        threading.Thread(target=_restart_or_recover, args=(UP, AU), daemon=True).start()
                        return
                except UP.UpdateBusy as e:
                    return self._send(200, {"ok": False, "error": str(e)})
                except Exception as e:  # noqa: BLE001 - after mark_installing: never leave "installing" behind
                    if not marked:
                        raise
                    msg = str(e) or e.__class__.__name__
                    if applied:
                        msg = _RESTART_FAILED
                    else:
                        UP.discard_stage(stage)
                    try:
                        AU.install_finished(False, msg)
                    except Exception:  # noqa: BLE001
                        pass
                    return self._send(200, {"ok": False, "error": msg})
                finally:
                    if gate_open:
                        A.resume()
            if u.path == "/api/update/restart":
                from meshpool_harness import updater as UP
                self._send(200, {"ok": True, "boot": BOOT})
                threading.Thread(target=UP.restart, args=([A.close_all, *ON_QUIT],), daemon=True).start()
                return
            if u.path == "/api/upload":
                name = unquote(parse_qs(u.query).get("name", ["file"])[0])
                return self._send(200, save_upload(name, self._body()))
            if u.path == "/api/ping":
                LAST_PING[0] = time.time()
                return self._send(200, {"ok": True})
            if u.path == "/api/quit":
                threading.Timer(0.3, _shutdown).start()
                return self._send(200, {"ok": True})
            if u.path == "/api/assistant":
                return self._send(200, assistant_action(self._json()))
            if u.path == "/api/suggest":
                from meshpool_harness import suggest as SG
                b = self._json()
                return self._send(200, {"suggestions": SG.suggest(b.get("text", ""), b.get("harness", ""),
                                                                  b.get("kind", "cowork"), bool(b.get("new", True)))})
            if u.path == "/api/route-harness":
                from meshpool_harness import harness_router as HRT
                b = self._json()
                return self._send(200, HRT.route(b.get("text", ""), b.get("kind", "cowork")))
            if u.path == "/api/harness-login":
                from meshpool_harness import harnesses as HRS
                b = self._json()
                hid = HRS.LEGACY.get(b.get("id", ""), b.get("id", ""))
                which = "codex" if hid == "codex" else HRS.LOGINS.get(hid, hid)
                return self._send(200, HRS.login(which))     # by harness id (codex) or by provider (chatgpt)
            if u.path == "/api/import-context":
                # context from ChatGPT, Claude or another assistant: pasted answer, pasted instructions, or an export
                from meshpool_harness import importer as IM
                b = self._json()
                if b.get("file"):
                    import base64
                    import tempfile
                    try:
                        raw = base64.b64decode(b["file"].get("data", ""))
                    except ValueError:
                        raise ValueError("That file could not be read.") from None
                    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
                        tf.write(raw)
                    try:
                        return self._send(200, IM.import_export(tf.name))
                    finally:
                        os.unlink(tf.name)
                if b.get("instructions"):
                    return self._send(200, {"skill": IM.import_instructions_as_skill(
                        b.get("name", ""), b["instructions"], b.get("description", ""))})
                return self._send(200, IM.import_context_text(b.get("text", "")))
            if u.path == "/api/import-file":          # a ChatGPT or Claude export, streamed to disk (they get large)
                from meshpool_harness import chat_import as CI
                import tempfile
                n = int(self.headers.get("Content-Length") or 0)
                if n < 0 or n > 8 * 1024 ** 3:
                    raise ValueError("That file is larger than 8 GB.")
                q = parse_qs(u.query)
                tf = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
                try:
                    with tf:
                        left = n
                        while left > 0:
                            chunk = self.rfile.read(min(left, 1 << 20))
                            if not chunk:
                                break
                            tf.write(chunk)
                            left -= len(chunk)
                except BaseException:                 # the upload was cut off: do not leave the partial file
                    try:
                        os.unlink(tf.name)
                    except OSError:
                        pass
                    raise
                want_ctx = q.get("context", ["1"])[0] != "0"
                try:
                    r = CI.import_export(tf.name, chats=q.get("chats", ["1"])[0] != "0", context=False)
                except Exception:
                    os.unlink(tf.name)
                    raise
                if not want_ctx:
                    os.unlink(tf.name)
                    return self._send(200, r)
                # The chats are in. Projects (as skills) and lasting facts (one model call) follow in the background
                # and land in Skills and in Instructions and memory a little later.
                from meshpool_harness import importer as IM

                def learn(path=tf.name):
                    try:
                        IM.import_export(path)
                    except Exception:  # noqa: BLE001
                        pass
                    finally:
                        os.unlink(path)
                threading.Thread(target=learn, daemon=True).start()
                return self._send(200, dict(r, context_pending=True))
            if u.path == "/api/import-chats":
                from meshpool_harness import chat_import as CI
                b = self._json()
                return self._send(200, dict(CI.import_local(b.get("sources") or None), found=CI.scan_local()))
            if u.path == "/api/import-agents":        # {keys, dry_run}: writes nothing when dry_run
                from meshpool_harness import agent_import as AI
                b = self._json()
                r = AI.apply(b.get("keys") or [], dry_run=bool(b.get("dry_run")))
                if not b.get("dry_run"):
                    C.load_into_env(override=True)
                return self._send(200, r)
            if u.path == "/api/import":
                from meshpool_harness import importer as IM
                r = IM.run(self._json().get("keys") or [])
                C.load_into_env(override=True)
                return self._send(200, dict(r, found=IM.scan()))
            if u.path == "/api/web-test":
                from meshpool_harness import web as WEB
                C.load_into_env(override=True)
                t0 = time.time()
                try:
                    rows = WEB.search("Meshpool AI", 5)
                    return self._send(200, {"ok": True, "count": len(rows), "seconds": round(time.time() - t0, 1),
                                            "first": rows[0]["title"] if rows else ""})
                except WEB.WebError as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            if u.path == "/api/rules":
                from meshpool_harness.core import permrules as PR
                b = self._json() or {}
                kind, rule = b.get("kind"), str(b.get("rule") or "")
                if kind not in ("allow", "deny") or b.get("action") not in ("add", "remove"):
                    return self._send(400, {"error": "action is add or remove, kind is allow or deny"})
                if b["action"] == "add" and PR.check_rule(rule):
                    return self._send(400, {"error": PR.check_rule(rule)})
                fn = PR.add_rule if b["action"] == "add" else PR.remove_rule
                return self._send(200, {**fn(PR.user_path(), kind, rule), "path": str(PR.user_path())})
            if u.path == "/api/permissions":
                from meshpool_harness.ui.desktop import permissions, request_permission
                b = self._json()
                return self._send(200, request_permission(b["id"]) if b.get("id") else permissions())
            if u.path == "/api/install-cli":
                from meshpool_harness.ui.desktop import install_cli
                return self._send(200, install_cli(admin=True))
            if u.path == "/api/styles":                       # create, update, delete, default, sample
                from meshpool_harness import styles as ST
                b = self._json()
                act = b.get("action", "create")
                if act == "sample":
                    return self._send(200, ST.from_sample(b.get("sample", "")))
                if act == "create":
                    ST.create(b.get("name", ""), b.get("instruction", ""))
                elif act == "update":
                    ST.update(b.get("id", ""), b.get("name", ""), b.get("instruction", ""))
                elif act == "delete":
                    ST.delete(b.get("id", ""))
                elif act == "default":
                    ST.set_default(b.get("id", ""))
                else:
                    return self._send(400, {"error": "Unknown action."})
                return self._send(200, ST.listing())
            if u.path == "/api/agent":
                return self._send(200, A.create(self._json()))
            if u.path == "/api/mcp":
                return self._send(200, A.mcp_save(self._json()))
            if u.path == "/api/mcp-library":
                from meshpool_harness import mcp_library as ML
                return self._send(200, ML.api_action(self._json()))
            if u.path == "/api/harnesses":
                return self._send(200, A.harness_change(self._json()))
            if u.path == "/api/mcp/test":
                return self._send(200, A.mcp_test(self._json()))
            if u.path == "/api/models/favorite":
                b = self._json()
                return self._send(200, W.set_favorite(b.get("id", ""), bool(b.get("on"))))
            if u.path == "/api/models/default":
                C.write({"MH_MODEL": self._json().get("id", "").strip()})   # may carry a provider prefix
                C.load_into_env(override=True)
                return self._send(200, W.models())
            if u.path in ("/api/profile", "/api/capabilities", "/api/data", "/api/notify", "/api/browser-use"):
                from meshpool_harness.ui import settings_v2 as SV
                b = self._json()
                if u.path == "/api/browser-use":
                    return self._send(200, SV.set_browser_use(b.get("mode", "meshpool"), b.get("cdp_url"), b.get("app"), b.get("show_window")))
                if u.path == "/api/profile":
                    return self._send(200, SV.save_profile(**{k: b.get(k) for k in SV.PROFILE_KEYS}))
                if u.path == "/api/capabilities":
                    return self._send(200, {"features": SV.set_capability(b.get("id", ""), bool(b.get("on")))})
                if u.path == "/api/notify":
                    A._notify(str(b.get("title", "Meshpool")), str(b.get("body", "")))
                    return self._send(200, {"ok": True})
                if b.get("action") == "export":
                    import base64
                    return self._send(200, {"name": "meshpool-export-" + time.strftime("%Y-%m-%d") + ".zip",
                                            "data": base64.b64encode(SV.export_zip()).decode()})
                if b.get("action") == "clear_chats":
                    return self._send(200, {"deleted": SV.clear_chats()})
                raise ValueError("Unknown action.")
            if u.path == "/api/pj":
                from meshpool_harness import project_store as PS
                b = self._json()
                return self._send(200, PS.create(b.get("name", ""), b.get("description", ""), b.get("instructions", ""),
                                                 b.get("link")))
            m = re.fullmatch(r"/api/pj/(p_[0-9a-f]{10})", u.path)
            if m:
                from meshpool_harness import project_store as PS
                pid, b = m.group(1), self._json()
                act = b.get("action", "update")
                if act == "update":
                    kw = {k: b[k] for k in ("name", "description", "instructions", "link", "pinned") if k in b}
                    return self._send(200, PS.update(pid, **kw))
                if act == "delete":
                    PS.delete(pid)
                    return self._send(200, {"ok": True})
                if act == "memory_set":
                    PS.memory_set(pid, b.get("text", ""))
                elif act == "memory_add":
                    PS.memory_add(pid, b.get("text", ""))
                elif act == "memory_clear":
                    PS.memory_clear(pid)
                elif act == "knowledge_add":
                    import base64
                    PS.knowledge_add(pid, b.get("name", ""), base64.b64decode(b.get("data", "")))
                elif act == "knowledge_remove":
                    PS.knowledge_remove(pid, b.get("name", ""))
                else:
                    raise ValueError("Unknown project action.")
                return self._send(200, PS.detail(pid))
            if u.path == "/api/projects":
                b = self._json()
                act = b.get("action", "add")
                if act == "pick":
                    from meshpool_harness.ui.desktop import choose_folder, has_folder_picker
                    if not has_folder_picker():          # the UI asks the user to type or paste the path instead
                        return self._send(200, {"projects": W.projects(), "picked": "", "no_picker": True})
                    path = choose_folder(b.get("start", ""))
                    if not path:
                        return self._send(200, {"projects": W.projects(), "picked": ""})
                    W.add_project(path)
                    return self._send(200, {"projects": W.projects(), "picked": str(Path(path).resolve())})
                if act == "dirs":                     # the in-app folder browser: the folders inside one folder
                    here = Path(b.get("path") or Path.home()).expanduser()
                    try:
                        here = here.resolve()
                        dirs = sorted((d.name for d in here.iterdir() if d.is_dir() and not d.name.startswith(".")),
                                      key=str.lower)
                    except OSError as e:
                        raise ValueError(f"Can't open that folder ({e.strerror or e}).")
                    return self._send(200, {"path": str(here), "parent": str(here.parent) if here.parent != here else "",
                                            "dirs": dirs[:500], "home": str(Path.home())})
                if act == "home":                     # the whole home folder ("the whole computer" for one user)
                    home = str(Path.home().resolve())
                    W.add_project(home, "Home")
                    return self._send(200, {"projects": W.projects(), "picked": home})
                if act == "add":
                    added = W.add_project(b.get("path", ""), b.get("name", ""))
                    return self._send(200, {"projects": W.projects(), "picked": added["path"]})
                elif act == "remove":
                    W.remove_project(b.get("path", ""))
                elif act == "rename":
                    W.rename_project(b.get("path", ""), b.get("name", ""))
                return self._send(200, {"projects": W.projects()})
            if u.path == "/api/skills":
                from meshpool_harness import skills as SK
                b = self._json()
                act = b.get("action")
                if act == "create":
                    SK.create(b.get("name", ""), b.get("description", ""), b.get("body", ""))
                elif act == "update":
                    SK.update(b.get("id", ""), b.get("text", ""))
                elif act == "toggle":
                    SK.toggle(b.get("id", ""), bool(b.get("on")))
                elif act == "remove":
                    SK.remove(b.get("id", ""))
                elif act == "upload":
                    import base64
                    SK.add_files([(f["name"], base64.b64decode(f["data"])) for f in b.get("files") or []])
                elif act == "open":                       # the page shows the folder in its own file tree
                    SK.folder().mkdir(parents=True, exist_ok=True)
                return self._send(200, {"skills": SK.load_all(), "folder": str(SK.folder())})
            if u.path == "/api/plugins":
                from meshpool_harness import plugins as PL
                return self._send(200, PL.api_action(self._json()))
            if u.path == "/api/file":
                b = self._json()
                A.mark_shared(b.get("root", ""))            # a turn running there can no longer be undone safely
                return self._send(200, W.write_file(b.get("root", ""), b.get("path", ""), b.get("text", "")))
            if u.path == "/api/git":
                b = self._json()
                return self._send(200, W.git_action(b.get("root", ""), b))
            if u.path == "/api/git/ci":
                b = self._json()
                return self._send(200, W.git_ci_action(b.get("root", ""), b))
            if u.path == "/api/git/pr":
                b = self._json()
                return self._send(200, W.pr_action(b.get("root", ""), b))
            if u.path == "/api/attach":
                import base64
                b = self._json()
                return self._send(200, W.attach(b.get("root", ""), b.get("name", ""), base64.b64decode(b.get("data", ""))))
            if u.path == "/api/connectors":
                from meshpool_harness import mcp_apps as CN
                b = self._json()
                act, cid = b.get("action"), b.get("id", "")
                redirect = f"http://127.0.0.1:{HTTPD[0].server_address[1]}/oauth/callback"
                if act in ("connect", "client"):
                    if act == "client":                  # the user's own client, for apps that do not register others
                        CN.set_client(cid, b.get("client_id", ""), b.get("client_secret", ""))
                    try:
                        url = CN.begin(cid, redirect)
                    except CN.NeedsClient as e:
                        return self._send(200, {"needs_client": cid, "redirect": e.redirect, "message": str(e),
                                                "connectors": CN.listing()})
                    return self._send(200, {"url": url, "inapp": _open_web(url), "connectors": CN.listing()})
                if act == "check":
                    r = CN.check(cid)
                    return self._send(200, dict(r, connectors=CN.listing()))
                if act == "custom":
                    c = CN.add_custom(b.get("name", ""), b.get("url", ""), b.get("token", ""),
                                      b.get("client_id", ""), b.get("client_secret", ""))
                    n = len(c.get("tools") or [])
                    if c["auth"] == "oauth":
                        try:
                            url = CN.begin(c["id"], redirect)
                        except CN.NeedsClient as e:
                            return self._send(200, {"id": c["id"], "needs_client": c["id"], "redirect": e.redirect,
                                                    "message": str(e), "connectors": CN.listing()})
                        return self._send(200, {"url": url, "id": c["id"], "inapp": _open_web(url), "connectors": CN.listing()})
                    return self._send(200, {"id": c["id"], "tools": n, "connectors": CN.listing(),
                                            "message": f"Connected: {n} tool{'s' * (n != 1)}. New chats can use it."})
                if act == "github_found":
                    r = CN.github_use_found()
                    C.load_into_env(override=True)
                    return self._send(200, {"connectors": CN.listing(), "message": f"Connected as {r['login']} ({r['source']})."})
                if act == "github_device":
                    r = CN.github_device_start()
                    return self._send(200, dict(r, inapp=_open_web(r["url"])))
                if act == "github_poll":
                    r = CN.github_device_poll()
                    C.load_into_env(override=True)
                    return self._send(200, dict(r, connectors=CN.listing()))
                if act == "token":
                    tok = b.get("token", "").strip()
                    if cid == "github" and tok and not CN._github_check(tok):
                        raise ValueError("GitHub did not accept that token. Check it and try again.")
                    CN.set_token(cid, tok)
                elif act == "enable":
                    CN.set_enabled(cid, bool(b.get("on")))
                elif act == "disconnect":
                    CN.disconnect(cid)
                C.load_into_env(override=True)
                return self._send(200, {"connectors": CN.listing()})
            if u.path == "/api/open-url":
                url = str(self._json().get("url", "")).strip()
                if not re.match(r"https?://", url, re.I):
                    raise ValueError("Only web addresses can be opened.")
                return self._send(200, {"ok": True, "inapp": _open_web(url)})
            if u.path == "/api/open-editor":
                return self._send(200, W.open_editor(self._json().get("path", "")))
            if u.path == "/api/github/workspace":          # clone (or reuse) a repository for a new session
                return self._send(200, W.github_workspace(self._json()))
            m = re.fullmatch(r"/api/session/([\w-]+)/bg/stop", u.path)
            if m:
                b = self._json()
                return self._send(200, W.session_bg_stop(m.group(1), b.get("kind", ""), b.get("id", "")))
            if u.path == "/api/term":
                b = self._json()
                return self._send(200, W.term_run(b.get("root", ""), b.get("command", ""), b.get("shell", "")))
            m = re.fullmatch(r"/api/term/(\w+)/stop", u.path)
            if m:
                return self._send(200, W.term_stop(m.group(1)))
            m = re.fullmatch(r"/api/threads/([\w-]+)", u.path)
            if m:
                return self._send(200, A.thread_action(m.group(1), self._json()))
            m = re.fullmatch(r"/api/agent/([\w-]+)/(model|plan|goal|revert|route|harness|style)", u.path)
            if m:
                s_, act, b = A.get(m.group(1)), m.group(2), self._json()
                if act == "model":
                    s_.set_model(b.get("model", ""))
                elif act == "harness":
                    s_.switch_harness(b.get("harness", ""))
                elif act == "route":
                    s_.set_route(b.get("spec", ""))
                elif act == "plan":
                    s_.set_plan(bool(b.get("on")))
                elif act == "goal":
                    s_.set_goal(b.get("text", ""))
                elif act == "style":
                    s_.set_style(b.get("style", ""))
                else:
                    s_.revert(int(b.get("turn", 0)))
                return self._send(200, s_.view(int(b.get("since", 10**9))))
            m = re.fullmatch(r"/api/agent/([\w-]+)/(rewind|fork)", u.path)     # back to a message / a new chat from it
            if m:
                s_, act, b = A.get(m.group(1)), m.group(2), self._json()
                if act == "fork":
                    return self._send(200, {"id": s_.fork(int(b.get("event_index", -1)))})
                r = s_.rewind(int(b.get("event_index", -1)), files=b.get("files") is not False)
                return self._send(200, dict(s_.view(int(b.get("since", 10**9))), text=r.get("text", ""),
                                            forked=r.get("forked"), undo=r.get("undo")))
            m = re.fullmatch(r"/api/agent/([\w-]+)/reply", u.path)     # an answer to an ask_user question
            if m:
                s, b = A.get(m.group(1)), self._json()
                s.reply(b.get("question_id", ""), b.get("answers"))
                return self._send(200, s.view(int(b.get("since", 10**9))))
            m = re.fullmatch(r"/api/agent/([\w-]+)/(send|stop|answer|mode|compact|grade)", u.path)
            if m:
                s, act, b = A.get(m.group(1)), m.group(2), self._json()
                if act == "send":
                    r = s.send(b.get("text", ""), images=b.get("images") or None, interrupt=bool(b.get("interrupt")),
                               files=b.get("files") or None)
                    return self._send(200, dict(s.view(int(b.get("since", 10**9))), sent=r))
                elif act == "stop":
                    s.stop()
                elif act == "grade":
                    s.grade()
                elif act == "answer":
                    s.answer(int(b.get("id", 0)), b.get("decision", "no"), b.get("note", ""))
                elif act == "compact":
                    if s.state in ("working", "waiting"):
                        raise ValueError("Wait for the task to finish or stop it, then compact.")
                    r = s.agent.compact_now() if hasattr(s.agent, "compact_now") else None
                    return self._send(200, dict(s.view(int(b.get("since", 10**9))), compacted=r))
                else:
                    s.set_mode(b.get("mode", "ask"))
                return self._send(200, s.view(int(b.get("since", 10**9))))
        except KeyError as e:
            msg = str(e).strip("'\"")
            if not msg or msg == "None":
                msg = "Not found."
            elif " " not in msg:                  # a bare id or field name: say what it was
                msg = f"Not found: {msg}."
            return self._send(404, {"error": msg})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except zipfile.BadZipFile:
            return self._send(400, {"error": "That file is not a valid zip file."})
        except (TypeError, AttributeError) as e:      # a field of the wrong type in the request body
            return self._send(400, {"error": f"The request has a missing or wrong value ({e})."})
        except (BrokenPipeError, ConnectionResetError):
            return None                    # the page went away mid-answer; nothing left to tell it
        except Exception as e:  # keep the app alive, show the cause
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})


# Files the operating system runs rather than shows when "opened"
RUNNABLE = {".py", ".pyw", ".js", ".jse", ".vbs", ".vbe", ".wsf", ".wsh", ".bat", ".cmd", ".ps1", ".exe", ".msi",
            ".com", ".scr", ".sh", ".command", ".app", ".jar", ".lnk", ".reg", ".hta", ".cpl", ".url", ".tool",
            ".pif", ".msc", ".msp", ".inf", ".scf", ".application", ".appref-ms", ".gadget", ".ws", ".sct",
            ".terminal", ".workflow", ".action", ".pkg", ".mpkg", ".dmg", ".desktop", ".appimage", ".run"}


def _open_web(url: str) -> bool:
    """Open a sign-in page. The address comes from the app's server metadata: only ever a web page.
    It opens inside Meshpool, in the Browser panel (True: the page shows it). Only on a computer where Meshpool's
    own browser cannot start does it use the system browser (False). The terminal sign-in (agent/connect_cli.py) has
    no panel and keeps printing the link and trying the system browser."""
    if not re.match(r"https?://", url or "", re.I):
        raise ValueError("That app's sign-in address is not a web page, so Meshpool did not open it.")
    try:
        from meshpool_harness.ui import browser_api as BA
        BA.open_signin(url)
        return True
    except Exception as e:  # noqa: BLE001 - no browser engine here: the last resort is the system browser
        print(f"  The in-app browser could not start ({e}); opening the sign-in page in the system browser.")
    webbrowser.open(url)
    return False


from meshpool_harness.errors import plain as _client_error  # noqa: E402  (the terminal says the same)


class _Server(ThreadingHTTPServer):
    # Windows' SO_REUSEADDR lets a second server bind a port already in use, so the free-port search never moved on
    allow_reuse_address = os.name != "nt"


def _running() -> bool:
    return any(s.state in ("working", "waiting") for s in list(A.LIVE.values()))


def _busy() -> bool:
    """Keep the app open: a chat is running, or a scheduled task needs the app to fire it."""
    try:
        from meshpool_harness import assistant as AS
        if any(t.get("enabled") for t in AS.tasks()):
            return True
    except Exception:  # noqa: BLE001
        pass
    return _running()


def _shutdown():
    if HTTPD[0] is not None:
        threading.Thread(target=HTTPD[0].shutdown, daemon=True).start()
    for fn in list(ON_QUIT):
        try:
            fn()
        except Exception:
            pass


def _watchdog(idle_s: float):
    while True:
        time.sleep(30)
        if time.time() - LAST_PING[0] > idle_s and not _busy():
            _shutdown()
            return


def serve(port: int = 8765, open_browser: bool = True, desktop: bool = False, on_ready=None) -> int:
    global DESKTOP
    DESKTOP = desktop
    RUNS.mkdir(parents=True, exist_ok=True)
    try:
        from meshpool_harness.harnesses import ensure_defaults
        ensure_defaults()
    except Exception:
        pass
    try:                                            # folders and chats from before projects: once, read-only
        from meshpool_harness import project_store as PS
        PS.migrate([r.get("workdir") for r in A.listing() if r.get("workdir")])
    except Exception:  # noqa: BLE001 - a failed migration never keeps the app from opening
        pass
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = _Server(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print(f"Could not open a local port between {port} and {port + 19}.")
        return 1
    url = f"http://127.0.0.1:{port}/"
    HTTPD[0] = httpd
    if desktop:
        LAST_PING[0] = time.time()
        threading.Thread(target=_watchdog, args=(float(os.environ.get("MH_IDLE_EXIT_S", "1200")),), daemon=True).start()
    threading.Thread(target=A.purge_due, daemon=True).start()          # chats deleted before the last close
    threading.Thread(target=A.scheduler_loop, daemon=True).start()
    threading.Thread(target=A.probe_mcp, daemon=True).start()
    ON_QUIT.append(HR.stop_login)                   # an unfinished ChatGPT sign-in does not outlive the app
    threading.Thread(target=A.recover_worktrees, daemon=True).start()   # copies a crash or a deleted chat left
    if desktop:                                     # background update checks and downloads; install on quit
        try:
            from meshpool_harness import autoupdate as AU
            ON_QUIT.append(AU.on_quit)
            AU.start_background(busy=_running)
        except Exception:  # noqa: BLE001
            pass
    if on_ready:
        on_ready(port)
    print("")
    print("  Meshpool is running.")
    print(f"  Open {url} in your browser if it did not open by itself.")
    print(f"  Settings file: {C.config_path()}")
    print(f"  Task outputs:  {RUNS}")
    print("  Use Quit in the app to stop Meshpool." if desktop else "  Close this window (or press Ctrl+C) to stop Meshpool.")
    print("")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()   # the app itself, in a browser tab (not the desktop app)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        A.close_all()
        httpd.server_close()
    return 0


def _default_project() -> Path:
    from meshpool_harness.ui.desktop import default_project
    return default_project()
