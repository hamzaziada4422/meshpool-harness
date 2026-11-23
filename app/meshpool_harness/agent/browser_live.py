"""The live view of the agent's browser: what lets the app show the page and pass the user's clicks and typing on.

The browser runs in the browser MCP server (one process per chat); the app's window talks to the app's own server, which
is another process. So the MCP server opens a small control channel:

  * a loopback HTTP server (127.0.0.1, a random port) with a random token, started when the MCP server starts;
  * a file under <runs>/browser-live/ that tells the app the port, the token and which chat it belongs to
    (readable by this user only, removed when the server exits).

Endpoints (the token goes in the X-Live-Token header; the Host header must be the loopback address):
    GET  /state          {open, url, title, width, height, tabs}
    GET  /frame          the page as a JPEG (204 when it has not changed since X-Frame-Id in ?known=)
    POST /input          {type: click|dblclick|move|scroll|key|text|goto|back|forward|reload, ...}

LivePage does the work against any Browser object (agent/browser_mcp_server.py). The app uses it directly for its own
sign-in browser (connector sign-in pages) and over HTTP (remote_*) for a chat's browser. Standard library only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

JPEG_QUALITY = 55
FRAME_MIN_GAP = 0.25            # seconds: frames are shared between viewers, so a busy panel costs one screenshot per gap
KEYS = {"Enter", "Tab", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End",
        "PageUp", "PageDown", "Space", "Insert"}


def live_dir() -> Path:
    from meshpool_harness import config as C
    return C.runs_dir() / "browser-live"


def web_address(url: str) -> str:
    """The address to open: a web page only (http or https; a bare name gets https://). ValueError for anything else."""
    u = str(url or "").strip()
    if not u:
        raise ValueError("Type a web address.")
    if "://" not in u and not u.startswith(("about:", "file:", "javascript:", "data:")):
        u = "https://" + u
    if not re.match(r"https?://", u, re.I):
        raise ValueError("Only web addresses (http or https) can be opened here.")
    return u


# ---------- the page, driven through the browser's own thread ---------------------------------------------------------

class LivePage:
    def __init__(self, browser):
        self.b = browser
        self._cache: tuple[float, bytes, dict] | None = None
        self._lock = threading.Lock()

    def _page(self):
        b = self.b
        if b.ctx is None or b.page is None:
            return None
        try:
            if b.page.is_closed():
                pages = [p for p in b.ctx.pages if not p.is_closed()]
                if not pages:
                    return None
                b.page = pages[-1]
        except Exception:  # noqa: BLE001  the browser went away
            return None
        return b.page

    def state(self) -> dict:
        def go():
            p = self._page()
            if p is None:
                return {"open": False}
            try:
                title = p.title()
            except Exception:  # noqa: BLE001  mid-navigation
                title = ""
            w, h = self._size(p)
            return {"open": True, "url": p.url, "title": title, "width": w, "height": h, "tabs": len(self.b.ctx.pages)}
        return self._run(go, {"open": False, "busy": True})

    @staticmethod
    def _size(p) -> tuple[int, int]:
        try:
            w, h = p.evaluate("[innerWidth, innerHeight]")
            return int(w), int(h)
        except Exception:  # noqa: BLE001
            vs = p.viewport_size or {"width": 1280, "height": 720}
            return vs["width"], vs["height"]

    def frame(self, known: str = "") -> tuple[int, bytes, dict]:
        """(status, jpeg, info). 200 with a picture, 204 when it is the one the viewer already has, 404 when no page is
        open, 503 when the browser is busy with the agent's own step (try again in a moment)."""
        with self._lock:
            c = self._cache
            if c and time.time() - c[0] < FRAME_MIN_GAP:
                data, info = c[1], c[2]
            else:
                def go():
                    p = self._page()
                    if p is None:
                        return None
                    w, h = self._size(p)
                    png = p.screenshot(type="jpeg", quality=JPEG_QUALITY, timeout=6000)
                    try:
                        title = p.title()
                    except Exception:  # noqa: BLE001
                        title = ""
                    return png, {"url": p.url, "title": title, "width": w, "height": h}
                got = self._run(go, "busy")
                if got == "busy":
                    return 503, b"", {}
                if got is None:
                    return 404, b"", {}
                data, info = got
                info = dict(info, id=hashlib.sha1(data).hexdigest()[:16])
                self._cache = (time.time(), data, info)
        if known and known == info.get("id"):
            return 204, b"", info
        return 200, data, info

    def act(self, d: dict) -> dict:
        kind = str(d.get("type") or "")

        def num(k, hi=20000.0):
            v = float(d.get(k, 0))
            if v != v or abs(v) > hi:
                raise ValueError("That position is not on the page.")
            return v

        def go():
            p = self._page()
            if p is None:
                raise ValueError("No page is open yet.")
            if kind in ("click", "dblclick"):
                p.mouse.click(num("x"), num("y"), click_count=2 if kind == "dblclick" else max(1, min(3, int(d.get("count") or 1))),
                              button="right" if d.get("button") == "right" else "left")
            elif kind == "move":
                p.mouse.move(num("x"), num("y"))
            elif kind == "scroll":
                p.mouse.move(num("x"), num("y"))
                p.mouse.wheel(num("dx"), num("dy"))
            elif kind == "key":
                key = str(d.get("key") or "")
                if not (key in KEYS or re.fullmatch(r"(?:(?:Control|Shift|Alt|Meta)\+)*(?:[A-Za-z0-9]|F\d{1,2}|" + "|".join(KEYS) + ")", key)):
                    raise ValueError("That key is not supported.")
                p.keyboard.press(key)
            elif kind == "text":
                t = str(d.get("text") or "")[:5000]
                if len(t) <= 40:
                    p.keyboard.type(t, delay=5)
                else:
                    p.keyboard.insert_text(t)
            elif kind == "goto":
                p.goto(web_address(d.get("url")), wait_until="domcontentloaded", timeout=30000)
            elif kind == "back":
                p.go_back(wait_until="domcontentloaded", timeout=15000)
            elif kind == "forward":
                p.go_forward(wait_until="domcontentloaded", timeout=15000)
            elif kind == "reload":
                p.reload(wait_until="domcontentloaded", timeout=30000)
            else:
                raise ValueError("Unknown action.")
            return {"ok": True}
        self._cache = None
        try:
            return self.b.run(go, timeout=40)
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001  a navigation that timed out, a page that closed
            raise ValueError(str(e).splitlines()[0][:200] if str(e) else "The page did not respond.")

    def _run(self, fn, default):
        try:
            return self.b.run(fn, timeout=8)
        except Exception:  # noqa: BLE001  busy with the agent's step, or the browser went away
            return default


# ---------- the server inside the browser MCP process -----------------------------------------------------------------

class LiveServer:
    def __init__(self, browser, chat: str = ""):
        self.live = LivePage(browser)
        self.chat = chat
        self.token = secrets.token_urlsafe(24)
        self.httpd: ThreadingHTTPServer | None = None
        self.file: Path | None = None

    def start(self) -> "LiveServer":
        live, token = self.live, self.token

        class H(BaseHTTPRequestHandler):
            server_version = "MeshpoolLive"

            def log_message(self, *a):
                pass

            def _ok(self) -> bool:
                host = (self.headers.get("Host") or "").split(":")[0]
                tok = self.headers.get("X-Live-Token") or ""
                if host not in ("127.0.0.1", "localhost") or not hmac.compare_digest(tok, token):
                    self._send(403, {"error": "forbidden"})
                    return False
                return True

            def _send(self, code, body, ctype="application/json", extra=None):
                data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, str(v))
                self.end_headers()
                if code != 204:
                    self.wfile.write(data)

            def do_GET(self):
                if not self._ok():
                    return
                u = urlparse(self.path)
                if u.path == "/state":
                    return self._send(200, live.state())
                if u.path == "/frame":
                    code, data, info = live.frame((parse_qs(u.query).get("known") or [""])[0])
                    return self._send(code, data, "image/jpeg", {"X-Frame-Info": json.dumps(info)})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._ok():
                    return
                if urlparse(self.path).path != "/input":
                    return self._send(404, {"error": "not found"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(min(n, 100_000)).decode("utf-8") or "{}")
                    self._send(200, live.act(d if isinstance(d, dict) else {}))
                except ValueError as e:
                    self._send(400, {"error": str(e)})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        try:
            d = live_dir()
            d.mkdir(parents=True, exist_ok=True)
            self.file = d / f"{re.sub(r'[^A-Za-z0-9_-]', '', self.chat) or 'chat'}-{os.getpid()}.json"
            self.file.write_text(json.dumps({"pid": os.getpid(), "port": self.httpd.server_address[1], "token": token,
                                             "chat": self.chat, "started": time.time()}), encoding="utf-8")
            try:
                os.chmod(self.file, 0o600)
            except OSError:
                pass
        except OSError:
            self.file = None
        return self

    def stop(self):
        try:
            if self.file:
                self.file.unlink()
        except OSError:
            pass
        try:
            if self.httpd:
                self.httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass


# ---------- the app's side: find a chat's browser and talk to it -------------------------------------------------------

def find(chat: str) -> dict | None:
    """The newest live browser server of this chat, or None. Files of servers that no longer answer are removed."""
    chat = re.sub(r"[^A-Za-z0-9_-]", "", chat or "")
    if not chat:
        return None
    best = None
    try:
        files = sorted(live_dir().glob(f"{chat}-*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            int(d["port"]), str(d["token"])
        except (OSError, ValueError, KeyError):
            continue
        d["_file"] = str(f)
        best = d
        break
    return best


def _call(entry: dict, method: str, path: str, body: dict | None = None, timeout: float = 12):
    req = urllib.request.Request(f"http://127.0.0.1:{int(entry['port'])}{path}", method=method,
                                 data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 headers={"X-Live-Token": entry["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def remote(chat: str, method: str, path: str, body: dict | None = None):
    """(status, bytes, headers) from a chat's browser; (404, ...) when the chat has none running."""
    e = find(chat)
    if not e:
        return 404, b'{"open": false}', {}
    try:
        return _call(e, method, path, body)
    except (OSError, ValueError):
        try:
            Path(e["_file"]).unlink()                   # the server is gone (the chat ended): forget it
        except OSError:
            pass
        return 404, b'{"open": false}', {}
