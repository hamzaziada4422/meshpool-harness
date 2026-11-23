"""Summaries: a one-line summary per chat, a living brief per project, and "Catch me up".

- Chat summary: made when a chat goes idle (`on_idle`, or `refresh_idle` at app open), on the fast model
  (`MH_MODEL_FAST`), cached by a hash of what it was made from, and rebuilt only when that changes. Without a model
  (or with MH_LEARN=0) a rule-based line is used instead. Stored in <Meshpool data folder>/summaries/chats.json.
  Incognito chats and chats with no turns get none, and nothing about them is stored.
- Project brief: what the project is, decisions, open threads, files produced, last activity. Stored in
  summaries/projects/<pid>.json and rebuilt only when the project's chats changed.
- catch_me_up(): what happened in a project since the user last opened it.

Everything is local; the only model use is phrasing, on the fast model, and it is optional.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from meshpool_harness import config as C

_LOCK = threading.RLock()
LINE_MAX = 140
SPLIT = "\n\n​"                        # what the app adds after the user's own words (hidden context)
_DECISION = re.compile(r"(?i)\b(decided|we will|we'll|i'll go with|going with|chose|chosen|agreed|settled on|"
                       r"the plan is|final (?:answer|version|choice)|let's go with)\b")
_OPEN = re.compile(r"(?i)\b(next step|next steps|still need|todo|to-do|not yet|waiting (?:for|on)|follow up|"
                   r"open question|need to (?:decide|confirm|check)|unresolved)\b")


# ---------- paths and small helpers -------------------------------------------------------------------------------
def sdir() -> Path:
    d = C.home() / "summaries"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _nosys(msgs) -> list:
    """A saved chat's messages without a leading system prompt (Agent.save() already leaves it out)."""
    msgs = list(msgs or [])
    return msgs[1:] if msgs and isinstance(msgs[0], dict) and msgs[0].get("role") == "system" else msgs


def _A():
    from meshpool_harness.ui import agent_api as A
    return A


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p: Path, data) -> None:
    from meshpool_harness import assistant as AS
    p.parent.mkdir(parents=True, exist_ok=True)
    AS._write(p, json.dumps(data, ensure_ascii=False))


def _dashless(t: str) -> str:
    from meshpool_harness import assistant as AS
    return AS.dashless(t)


def _fast_client():
    if os.environ.get("MH_LEARN", "1") == "0" or os.environ.get("MH_SUMMARY_MODEL", "1") == "0":
        return None
    try:
        from meshpool_harness.core.client import LiveClient
        return LiveClient("summary")
    except Exception:  # noqa: BLE001
        return None


def _model() -> str:
    return os.environ.get("MH_MODEL_FAST") or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"


def _json_call(client, system: str, user: str, tag: str, max_tokens: int = 500) -> dict | None:
    try:
        r = client.chat(model=_model(), messages=[{"role": "system", "content": system},
                                                  {"role": "user", "content": user[:9000]}],
                        max_tokens=max_tokens, temperature=0.2, task_id=tag, role="worker",
                        extra={"response_format": {"type": "json_object"}})
        txt = r["choices"][0]["message"].get("content") or ""
        return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except Exception:  # noqa: BLE001 - phrasing is optional
        return None


def _sentence(t: str, n: int = LINE_MAX) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    m = re.match(r"(.{12,}?[.!?])(\s|$)", t)
    s = m.group(1) if m else t
    if len(s) > n:
        s = s[: n - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return s


def _hash(*parts) -> str:
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8", "replace")).hexdigest()[:14]


# ---------- reading a chat ------------------------------------------------------------------------------------------
def chat_digest(sid: str) -> dict | None:
    """What a summary is made from: the user's messages, the last reply, the turn count. None when the chat is
    missing, has no turns, or is incognito (nothing is read for those beyond the flag)."""
    A = _A()
    meta = A.read_meta(sid)
    if meta.get("incognito"):
        return None
    d = A._saved(sid)
    if not d:
        return None
    users, replies = [], []
    for m in _nosys(d.get("messages")):
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        if m.get("role") == "user" and isinstance(c, str):
            t = c.split(SPLIT)[0].strip()
            if t:
                users.append(t)
        elif m.get("role") == "assistant" and isinstance(c, str) and c.strip() and c != "(no reply)":
            replies.append(c.strip())
    if not users:
        return None
    return {"id": sid, "title": d.get("title") or "", "users": users, "replies": replies, "turns": len(users),
            "updated": d.get("updated", 0), "project_id": meta.get("project_id") or "",
            "harness": d.get("harness") or "", "model": d.get("model") or ""}


def _chat_hash(dg: dict) -> str:
    return _hash(dg["title"], dg["turns"], *[u[:300] for u in dg["users"][:6]], (dg["replies"] or [""])[-1][:500])


def _rule_line(dg: dict) -> str:
    first = _sentence(dg["users"][0])
    return first or _sentence(dg["title"]) or "Chat"


# ---------- chat summaries ------------------------------------------------------------------------------------------
def _chats_file() -> Path:
    return sdir() / "chats.json"


def _all() -> dict:
    return _read_json(_chats_file(), {})


def summary_for(sid: str) -> str:
    """The stored one-line summary ("" when none; never made on demand, never for an incognito chat)."""
    return (_all().get(sid) or {}).get("summary", "")


def summarise_chat(sid: str, client=None, force: bool = False) -> str:
    """Make (or refresh) a chat's summary. Returns it, or "" for an incognito chat, a chat with no turns, or an
    unchanged chat that already has one (use summary_for to read). Never raises."""
    try:
        dg = chat_digest(sid)
        if dg is None:
            with _LOCK:                                  # a chat that became incognito loses any stored line
                rows = _all()
                if sid in rows and _A().read_meta(sid).get("incognito"):
                    rows.pop(sid)
                    _write_json(_chats_file(), rows)
            return ""
        h = _chat_hash(dg)
        with _LOCK:
            cur = _all().get(sid)
        if cur and cur.get("hash") == h and not force:
            return cur["summary"]
        line, source = "", "rules"
        client = client or _fast_client()
        if client is not None:
            got = _json_call(client, "Summarise this chat in ONE plain sentence of at most 18 words, saying what the "
                             "user got or wanted. No em dashes, nothing in all caps. Return ONLY JSON "
                             "{\"summary\": str}.",
                             f"Title: {dg['title']}\nUser messages:\n" + "\n".join("- " + u[:300] for u in dg["users"][:6])
                             + f"\nLast reply:\n{(dg['replies'] or [''])[-1][:600]}", "chat-summary", 160)
            s = (got or {}).get("summary")
            if isinstance(s, str) and 6 <= len(s.strip()) <= 300:
                line, source = _sentence(_dashless(s)), "model"
        if not line:
            line = _rule_line(dg)
        with _LOCK:
            rows = _all()
            rows[sid] = {"summary": line, "hash": h, "ts": round(time.time(), 1), "source": source,
                         "updated": dg["updated"]}
            _write_json(_chats_file(), rows)
        return line
    except Exception:  # noqa: BLE001
        return ""


def on_idle(sid: str) -> str:
    """Called when a chat goes idle: summarise it in the background-friendly way (never raises, never blocks a turn
    if called from a thread)."""
    return summarise_chat(sid)


def on_idle_async(sid: str) -> threading.Thread:
    th = threading.Thread(target=on_idle, args=(sid,), daemon=True, name="meshpool-summary")
    th.start()
    return th


def refresh_idle(limit: int = 12, idle_secs: float = 300.0) -> int:
    """At app open: summarise chats that went idle without one (or whose summary is out of date). Returns how many."""
    A = _A()
    n, now = 0, time.time()
    rows = _all()
    try:
        dirs = sorted(A.SESSIONS.glob("*/session.json"), key=lambda p: -p.stat().st_mtime)
    except OSError:
        return 0
    for p in dirs:
        sid = p.parent.name
        if sid.startswith("sub-") or n >= limit:
            continue
        try:
            if now - p.stat().st_mtime < idle_secs:
                continue
        except OSError:
            continue
        before = (rows.get(sid) or {}).get("hash")
        if summarise_chat(sid) and (_all().get(sid) or {}).get("hash") != before:
            n += 1
    return n


def search_boost(query: str) -> dict:
    """chat id -> score for chats whose summary matches the query's words (for the search ranking)."""
    toks = [t for t in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(t) > 2]
    if not toks:
        return {}
    out = {}
    for sid, r in _all().items():
        low = r.get("summary", "").lower()
        hit = sum(1 for t in toks if t in low)
        if hit:
            out[sid] = hit / len(toks)
    return out


# ---------- project briefs ------------------------------------------------------------------------------------------
def _pdir() -> Path:
    d = sdir() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _opened_file() -> Path:
    return sdir() / "opened.json"


def touch_opened(pid: str, ts: float | None = None) -> float:
    """Record that the user opened the project now. Returns the time of the visit before this one (0 if none)."""
    with _LOCK:
        d = _read_json(_opened_file(), {})
        prev = float(d.get(pid) or 0)
        d[pid] = round(ts or time.time(), 3)
        _write_json(_opened_file(), d)
    return prev


def last_opened(pid: str) -> float:
    return float(_read_json(_opened_file(), {}).get(pid) or 0)


def project_chats(pid: str) -> list[dict]:
    """The project's chats (incognito ones left out), newest first, each with its digest and summary."""
    A = _A()
    out = []
    try:
        dirs = [p.parent for p in A.SESSIONS.glob("*/session.json") if not p.parent.name.startswith("sub-")]
    except OSError:
        return []
    for d in dirs:
        meta = A.read_meta(d.name)
        if (meta.get("project_id") or "") != pid or meta.get("incognito"):
            continue
        dg = chat_digest(d.name)
        if dg:
            dg["summary"] = summary_for(d.name) or _rule_line(dg)
            out.append(dg)
    out.sort(key=lambda r: -r["updated"])
    return out


def _project_files(pid: str, chats: list[dict]) -> list[dict]:
    try:
        from meshpool_harness.ui import hub
        A = _A()
        rows = []
        for r in A.listing():
            if r["id"] in {c["id"] for c in chats}:
                rows += [dict(f, chat_id=r["id"]) for f in hub._session_files(r)]
        return [{"name": Path(f["path"]).name, "ext": f.get("ext", ""), "chat_id": f["chat_id"]} for f in rows][:30]
    except Exception:  # noqa: BLE001
        return []


def _brief_rules(name: str, desc: str, chats: list[dict], files: list[dict]) -> dict:
    decisions, open_ = [], []
    for c in chats[:12]:
        for r in c["replies"][-3:]:
            for s in re.split(r"(?<=[.!?])\s+|\n", r):
                s = s.strip(" -*•#")
                if 15 <= len(s) <= 200 and _DECISION.search(s) and s not in decisions:
                    decisions.append(_sentence(s, 160))
        last_user = c["users"][-1]
        lastr = c["replies"][-1] if c["replies"] else ""
        if not c["replies"] or (lastr and _OPEN.search(lastr[-400:])) or last_user.rstrip().endswith("?") and not lastr:
            open_.append({"chat_id": c["id"], "text": _sentence(c["summary"], 140)})
    return {"what": (desc or "").strip() or f"A project called {name}.", "decisions": decisions[:6],
            "open_threads": open_[:6]}


def _brief_file(pid: str) -> Path:
    return _pdir() / f"{re.sub(r'[^A-Za-z0-9_-]', '_', pid)}.json"


def project_brief(pid: str, client=None, force: bool = False) -> dict:
    """The living brief. Rebuilt only when the project's chats (or its description) changed. {} for no project."""
    from meshpool_harness import project_store as PS
    try:
        meta = PS.get_meta(pid)
    except Exception:  # noqa: BLE001
        return {}
    if not meta:
        return {}
    name, desc = meta.get("name") or "Project", meta.get("description") or ""
    chats = project_chats(pid)
    h = _hash(name, desc, *[f"{c['id']}:{c['updated']}:{c['turns']}:{c['summary']}" for c in chats])
    cur = _read_json(_brief_file(pid), {})
    if cur.get("hash") == h and not force:
        return cur
    files = _project_files(pid, chats)
    brief = _brief_rules(name, desc, chats, files)
    source = "rules"
    client = client or (_fast_client() if chats else None)
    if client is not None and chats:
        got = _json_call(client, "Write a living brief for a project from its chats. Plain words, short sentences, no "
                         "em dashes, nothing in all caps, nothing not in the input. Return ONLY JSON {\"what\": one "
                         "or two sentences, \"decisions\": [up to 5 short strings], \"open_threads\": [up to 5 short "
                         "strings]}.",
                         f"Project: {name}. {desc}\n" + "\n".join(f"- {c['title']}: {c['summary']}" for c in chats[:15]),
                         "project-brief", 600)
        if got and isinstance(got.get("what"), str):
            brief["what"] = _sentence(_dashless(got["what"]), 300)
            if isinstance(got.get("decisions"), list):
                brief["decisions"] = [_sentence(_dashless(str(x)), 160) for x in got["decisions"][:5] if str(x).strip()]
            if isinstance(got.get("open_threads"), list):
                ids = [c["id"] for c in chats]
                brief["open_threads"] = [{"chat_id": ids[i] if i < len(ids) else "", "text": _sentence(_dashless(str(x)), 140)}
                                         for i, x in enumerate(got["open_threads"][:5]) if str(x).strip()]
            source = "model"
    out = dict(brief, id=pid, name=name, files=files, chats=len(chats),
               last_activity=max([c["updated"] for c in chats], default=0) or meta.get("updated", 0),
               hash=h, ts=round(time.time(), 1), source=source)
    _write_json(_brief_file(pid), out)
    return out


def catch_me_up(pid: str, since_ts: float | None = None, client=None, touch: bool = True) -> dict:
    """What happened in a project since `since_ts` (default: since the user last opened it). Marks the project opened."""
    since = float(since_ts) if since_ts else last_opened(pid)
    chats = [c for c in project_chats(pid) if c["updated"] > since]
    files = [f for f in _project_files(pid, chats)] if chats else []
    items = [{"chat_id": c["id"], "title": c["title"], "summary": c["summary"], "updated": c["updated"],
              "turns": c["turns"]} for c in chats]
    if not items:
        text = "Nothing new since you last looked." if since else "No chats in this project yet."
    else:
        text = f"{len(items)} chat{'s' if len(items) != 1 else ''} moved: " + "; ".join(
            _sentence(i["summary"], 90).rstrip(".") for i in items[:4]) + "."
        if files:
            text += f" {len(files)} file{'s' if len(files) != 1 else ''} made."
        client = client or _fast_client()
        if client is not None:
            got = _json_call(client, "In two short plain sentences, catch the user up on what changed in their project. "
                             "No em dashes. Return ONLY JSON {\"text\": str}.",
                             "\n".join(f"- {i['title']}: {i['summary']}" for i in items[:10]), "catch-up", 200)
            if got and isinstance(got.get("text"), str) and got["text"].strip():
                text = _dashless(got["text"].strip())[:400]
    if touch:
        touch_opened(pid)
    return {"id": pid, "since": since, "items": items, "files": files, "text": text}
