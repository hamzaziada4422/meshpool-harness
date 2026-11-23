"""The Browser panel's routes (the app's server side of agent/browser_live.py).

    GET  /api/browser/state?chat=<id>&src=chat|signin     {open, url, title, width, height, ...}
    GET  /api/browser/frame?chat=<id>&src=...&known=<id>  the page as a JPEG (204: unchanged), page info in X-Frame-Info
    POST /api/browser/input   {chat, src, type, ...}      a click, scroll, key, text or address typed in the panel
    POST /api/browser/open    {url}                       open a web address in the app's own sign-in browser
    POST /api/browser/close   {src: "signin"}             close the sign-in browser (what it saved stays)

src "chat": the browser the agent of that chat uses (a process of its own; reached over its control channel).
src "signin": a browser inside the app for sign-in pages (connectors, GitHub, keys). It has a profile of its own, so it
never fights the agent's browser for a profile folder, and it is never the user's everyday browser.
"""
from __future__ import annotations

import json
import threading
from urllib.parse import parse_qs

from meshpool_harness.agent import browser_live as BL

_S: dict = {"b": None, "live": None, "error": "", "lock": threading.Lock()}


def _signin():
    with _S["lock"]:
        if _S["b"] is None:
            from meshpool_harness.agent import browser_mcp_server as BM
            _S["b"] = BM.Browser(profile="signin")
            _S["live"] = BL.LivePage(_S["b"])
    return _S["b"], _S["live"]


def open_signin(url: str) -> None:
    """Show a web address in the sign-in browser. Starts it when needed and returns as soon as it is up; the page loads
    in the background (the panel shows it as it arrives). Raises when no browser can start here."""
    url = BL.web_address(url)
    b, _ = _signin()
    _S["error"] = ""
    try:
        b.run(b.ensure, timeout=120)
    except Exception as e:  # noqa: BLE001
        _S["error"] = str(e)
        raise

    def go():
        try:
            b.run(lambda: (b.ensure(), b.page.goto(url, wait_until="domcontentloaded", timeout=45000)), timeout=90)
        except Exception as e:  # noqa: BLE001  the page can still be shown: it may have partly loaded
            _S["error"] = str(e).splitlines()[0][:200] if str(e) else ""
    threading.Thread(target=go, daemon=True).start()


def close_signin() -> None:
    b = _S["b"]
    if b is not None:
        try:
            b.run(b.shutdown, timeout=15)
        except Exception:  # noqa: BLE001
            pass


def _target(q: dict):
    src = (q.get("src") or "chat")
    return src, str(q.get("chat") or "")


def handle(method: str, u, body: bytes = b""):
    path = u.path
    if method == "GET":
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        src, chat = _target(q)
        if path == "/api/browser/state":
            if src == "signin":
                st = _S["live"].state() if _S["live"] is not None else {"open": False}
                if _S["error"] and not st.get("open"):
                    st["error"] = _S["error"]
                return 200, st
            code, data, _ = BL.remote(chat, "GET", "/state")
            try:
                return 200, json.loads(data.decode("utf-8") or "{}")
            except ValueError:
                return 200, {"open": False}
        if path == "/api/browser/frame":
            known = q.get("known", "")
            if src == "signin":
                if _S["live"] is None:
                    return 404, {"open": False}
                code, data, info = _S["live"].frame(known)
                hdr = {"X-Frame-Info": json.dumps(info)}
            else:
                from urllib.parse import quote
                code, data, h = BL.remote(chat, "GET", "/frame?known=" + quote(known))
                hdr = {"X-Frame-Info": h.get("X-Frame-Info", "{}")}
            if code == 200:
                return 200, data, "image/jpeg", hdr
            return code, (b"" if code == 204 else {"open": False}), "application/json", hdr
    elif method == "POST":
        b = json.loads(body.decode("utf-8")) if body else {}
        if path == "/api/browser/open":
            try:
                open_signin(b.get("url", ""))
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001
                return 400, {"error": f"The browser could not start: {e}"}
            return 200, {"ok": True}
        if path == "/api/browser/close":
            if b.get("src") == "signin":
                close_signin()
            return 200, {"ok": True}
        if path == "/api/browser/input":
            src, chat = _target(b)
            act = {k: v for k, v in b.items() if k not in ("src", "chat")}
            if src == "signin":
                if _S["live"] is None:
                    return 400, {"error": "No page is open yet."}
                return 200, _S["live"].act(act)
            code, data, _ = BL.remote(chat, "POST", "/input", act)
            try:
                out = json.loads(data.decode("utf-8") or "{}")
            except ValueError:
                out = {}
            return (200 if code == 200 else 400), (out if code == 200 else {"error": out.get("error") or "The browser is not open yet."})
    return 404, {"error": "not found"}
