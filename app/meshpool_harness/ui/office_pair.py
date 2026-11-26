"""Pairing the Office panel with the meshpool app.

The panel page (https://localhost:<port>/office/taskpane.html) is served to anyone who asks: Office loads it with no
secret of its own. Until 0.0.18 the page carried the panel's token, so any local process (a sandboxed command, a
script) could fetch the page, read the token, start Work chats and answer approvals "yes" (0.0.18 ledger, SV, found
not fixed). The page now carries no token. Instead:

  1. The panel asks to connect: POST /office/pair. It gets back a pairing id (a secret only it holds) and a short code
     that it shows to the user.
  2. The meshpool app, which only the user's own window can drive (its launch key), shows the same code and asks
     the user to allow it. Several requests at once are all shown, each with its own code, so a request from another
     process cannot pass for the panel's own.
  3. The panel polls with its pairing id (POST /office/pair/check). Once the user allowed the code, it gets a session
     token once. That token is what the panel's API calls carry.

A process that only fetches the page or starts a pairing of its own gets nothing that works: its request waits for
the user, who sees a code that their panel does not show. Sessions live in this process's memory only and end when
the app quits; the panel keeps its token in memory (sessionStorage) too, never on disk.
"""
from __future__ import annotations

import secrets
import threading
import time

PAIR_TTL_S = 120            # a request the user did not answer in time is dropped
MAX_PENDING = 4             # more waiting requests than this are refused (a process flooding the app's prompt)
MAX_SESSIONS = 8            # panels connected at once; the oldest is dropped beyond this
HOSTS = ("Excel", "PowerPoint", "Word")

_LOCK = threading.Lock()
_PAIRS: dict = {}           # pairing id -> {"code", "host", "t", "state": "pending" | "allowed" | "denied", "token"}
_SESSIONS: dict = {}        # session token -> {"host", "t", "chats": the chat ids this panel started}


class TooMany(RuntimeError):
    pass


def _same(a, b) -> bool:
    """compare_digest on bytes: on str it raises for non-ASCII text, and a pairing id or token header can carry it."""
    return secrets.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


def _prune(now: float) -> None:
    for pid in [p for p, r in _PAIRS.items() if now - r["t"] > PAIR_TTL_S]:
        del _PAIRS[pid]


def _code() -> str:
    taken = {r["code"] for r in _PAIRS.values()}
    while True:
        n = secrets.randbelow(10 ** 6)
        code = f"{n // 1000:03d} {n % 1000:03d}"
        if code not in taken:
            return code


def start(host: str) -> dict:
    """A new pairing request from a panel. Returns the secret pairing id and the code the panel shows."""
    host = host if host in HOSTS else "Office"
    now = time.time()
    with _LOCK:
        _prune(now)
        if sum(1 for r in _PAIRS.values() if r["state"] == "pending") >= MAX_PENDING:
            raise TooMany("Too many requests are waiting. Answer them in the meshpool app, or wait two minutes.")
        pid = secrets.token_urlsafe(24)
        _PAIRS[pid] = {"code": _code(), "host": host, "t": now, "state": "pending", "token": ""}
        return {"pair": pid, "code": _PAIRS[pid]["code"], "expires_in": PAIR_TTL_S}


def check(pid: str) -> dict:
    """What became of a pairing request. The session token is handed over once, to the holder of the pairing id."""
    now = time.time()
    with _LOCK:
        _prune(now)
        rec = None
        for k in _PAIRS:                       # compare every id in constant time
            if _same(k, pid):
                rec = _PAIRS[k]
                pid = k
        if rec is None:
            return {"state": "expired"}
        if rec["state"] == "pending":
            return {"state": "pending", "code": rec["code"]}
        del _PAIRS[pid]
        if rec["state"] == "denied":
            return {"state": "denied"}
        return {"state": "allowed", "token": rec["token"], "host": rec["host"]}


def pending() -> list[dict]:
    """The requests waiting for the user, for the app's prompt (no pairing ids: the app never needs them)."""
    now = time.time()
    with _LOCK:
        _prune(now)
        return [{"code": r["code"], "host": r["host"], "age": int(now - r["t"])}
                for r in sorted(_PAIRS.values(), key=lambda r: r["t"]) if r["state"] == "pending"]


def decide(code: str, allow: bool) -> bool:
    """The user's answer in the app, for the request showing this code. False when no such request waits."""
    code = " ".join(str(code or "").split())
    now = time.time()
    with _LOCK:
        _prune(now)
        for r in _PAIRS.values():
            if r["state"] == "pending" and _same(r["code"], code):
                if not allow:
                    r["state"] = "denied"
                    return True
                tok = secrets.token_urlsafe(32)
                while len(_SESSIONS) >= MAX_SESSIONS:
                    del _SESSIONS[min(_SESSIONS, key=lambda k: _SESSIONS[k]["t"])]
                _SESSIONS[tok] = {"host": r["host"], "t": now, "chats": set()}
                r["state"], r["token"] = "allowed", tok
                return True
    return False


def session_of(token: str | None) -> str:
    """The connected panel's session token that matches, or "" (compared in constant time)."""
    if not token:
        return ""
    with _LOCK:
        hit = ""
        for k in _SESSIONS:
            if _same(k, token):
                hit = k
        return hit


def session_ok(token: str | None) -> bool:
    return bool(session_of(token))


def add_chat(session: str, chat: str) -> None:
    """A chat this panel started: only this panel's later calls may reach it."""
    with _LOCK:
        if session in _SESSIONS:
            _SESSIONS[session]["chats"].add(str(chat))


def owns(session: str, chat: str) -> bool:
    """Whether this panel started the chat. A panel disconnected (forget, a newer panel pushed it out) owns none."""
    with _LOCK:
        return bool(session) and str(chat) in (_SESSIONS.get(session) or {}).get("chats", ())


def sessions() -> int:
    with _LOCK:
        return len(_SESSIONS)


def forget() -> int:
    """Disconnect every panel and drop every waiting request. Returns how many panels were connected."""
    with _LOCK:
        n = len(_SESSIONS)
        _SESSIONS.clear()
        _PAIRS.clear()
        return n
