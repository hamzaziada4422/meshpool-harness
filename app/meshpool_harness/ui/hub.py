"""Data behind the app shell's hub pages: files made across all chats (Artifacts), scheduled tasks as cards, search
across chats, code sessions, projects, artifacts and scheduled tasks, and the account menu. Read-only; server.py
exposes it as /api/hub/... routes. Nothing here calls GitHub or a model."""
from __future__ import annotations

import hashlib
import mimetypes
import platform
import time
from pathlib import Path

from meshpool_harness import assistant as AS
from meshpool_harness import config as C
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import settings_v2 as SV
from meshpool_harness.ui import workbench as W

# What counts as an artifact: files a person opens, not source code. Label, tint (the page colours the tile).
KINDS = {
    "docx": ("Docs", "blue"), "md": ("Docs", "blue"), "txt": ("Docs", "blue"), "pdf": ("PDF", "red"),
    "pptx": ("Slides", "orange"), "xlsx": ("Sheets", "green"), "csv": ("Sheets", "green"), "json": ("Data", "purple"),
    "html": ("Web", "pink"), "png": ("Images", "pink"), "jpg": ("Images", "pink"), "jpeg": ("Images", "pink"),
    "svg": ("Images", "pink"), "gif": ("Images", "pink"),
}
TABS = ("all", "artifacts", "sessions", "code", "projects", "scheduled")
_ART: dict[str, tuple] = {}                     # session id -> (mtime_ns of session.json, [artifact rows])


def is_code_session(r: dict) -> bool:
    """A coding session: it works in a real folder or a GitHub workspace. A chat that only has its own private
    folder is a chat, whichever agent the router picked."""
    return r.get("kind") == "code" and not r.get("scratch") and r.get("code_mode") is not False


def _events(sid: str) -> list:
    with A.LOCK:
        s = A.LIVE.get(sid)
    if s is not None:
        with s.lock:
            return list(s.events)
    msgs = A.saved_messages(sid)
    try:
        return A.events_from_messages(msgs)
    except Exception:  # noqa: BLE001
        return []


def _session_files(r: dict) -> list[dict]:
    sid, root = r["id"], r.get("workdir") or ""
    if not root or not Path(root).is_dir():
        return []
    stamp = []
    # the cache follows the transcript and the checkpoints (a turn's files, an Undo): either one changing means new
    # rows. With session.json alone, Artifacts kept a file Undo had removed and missed a deck an edit had changed.
    for p in (A.SESSIONS / sid / "session.json", A.SESSIONS / sid / "checkpoints" / "turns.json",
              A.SESSIONS / sid / "transcript.jsonl"):
        try:
            stamp.append(p.stat().st_mtime_ns)
        except OSError:
            stamp.append(0)
    stamp = tuple(stamp)
    live = sid in A.LIVE and r.get("state") in ("working", "waiting")
    hit = _ART.get(sid)
    if hit and hit[0] == stamp and not live:
        return hit[1]
    turns = []
    try:
        from meshpool_harness.checkpoints import Checkpoints
        turns = Checkpoints(root, A.SESSIONS / sid / "checkpoints").turns()
    except Exception:  # noqa: BLE001
        turns = []
    rows = []
    for o in W.outputs_from(_events(sid), turns, Path(root).resolve()):     # files that still exist, user files only
        ext = o["path"].rsplit(".", 1)[-1].lower() if "." in o["path"] else ""
        if ext not in KINDS or (o["how"] == "edited" and r.get("kind") == "code"):
            continue
        rows.append({"path": o["path"], "ext": ext, "size": o.get("size", 0), "how": o["how"]})
    _ART[sid] = (stamp, rows)
    return rows


def artifacts(limit: int = 300, listing: list | None = None) -> dict:
    """Files made or changed across all chats, newest first."""
    out = []
    for r in (A.listing() if listing is None else listing)[:150]:
        for f in _session_files(r):
            fp = Path(r["workdir"]) / f["path"]
            try:
                mt = fp.stat().st_mtime
            except OSError:
                continue
            label, tint = KINDS[f["ext"]]
            out.append({"id": hashlib.sha1(f"{r['id']}:{f['path']}".encode()).hexdigest()[:12], "name": fp.name,
                        "path": f["path"], "root": r["workdir"], "ext": f["ext"], "type": label, "tint": tint,
                        "size": f["size"], "updated": mt, "session_id": r["id"], "session_title": r["title"],
                        "project_id": r.get("project_id") or "", "kind": r.get("kind"),
                        "mime": mimetypes.guess_type(fp.name)[0] or ""})
    out.sort(key=lambda a: -a["updated"])
    counts: dict[str, int] = {}
    for a in out:
        counts[a["type"]] = counts.get(a["type"], 0) + 1
    return {"artifacts": out[:limit], "types": counts}


def _task_row(t: dict, threads: dict, names: dict | None = None) -> dict:
    last = threads.get(t.get("last_thread") or "")
    pj = ""
    if t.get("folder"):
        if names is None:
            names = {p.get("path"): p.get("name", "") for p in W.projects()}
        pj = names.get(t["folder"], "")
    return {"id": t["id"], "name": t.get("name") or "Scheduled task", "prompt": t.get("prompt", ""),
            "enabled": bool(t.get("enabled")), "status": AS.status(t),
            "error": (t.get("last_error") or {}).get("text", ""), "invalid": t.get("invalid", ""),
            "skipped": (t.get("skipped") or {}).get("why", ""), "skip_if_missed_h": t.get("skip_if_missed_h"),
            "running": bool(t.get("lease") and float((t["lease"] or {}).get("until") or 0) > __import__("time").time()),
            "schedule": AS.describe(t), "when": t.get("when"), "time": t.get("time"), "day": t.get("day"),
            "minutes": t.get("minutes"), "at": t.get("at"), "only_on_change": bool(t.get("only_on_change")), "next_run": t.get("next_run"),
            "last_run": t.get("last_run"), "last_thread": t.get("last_thread"), "kind": t.get("kind", "cowork"),
            "project": pj, "folder": t.get("folder", ""), "unread": bool(last and last.get("unread"))}


TEMPLATES = [
    {"name": "Morning briefing", "prompt": "Summarise what I should know before the day starts: my calendar, unread mail and anything due today.", "when": "weekdays", "time": "08:30"},
    {"name": "Weekly project recap", "prompt": "Write a short recap of what changed in my projects this week and what is still open.", "when": "weekly", "day": "fri", "time": "16:00"},
    {"name": "Repository health check", "prompt": "Check this repository for failing tests, stale branches and open pull requests, and list what needs attention.", "when": "daily", "time": "09:00", "kind": "code"},
    {"name": "Spend check", "prompt": "Tell me how much my model key spent this week and which chats used the most.", "when": "weekly", "day": "mon", "time": "09:00"},
]


def scheduled(listing: list | None = None) -> dict:
    """The tasks as cards. `listing` is the chat list when the caller has it already (it is not cheap to build)."""
    threads = {r["id"]: r for r in (A.listing() if listing is None else listing)}
    tasks = AS.tasks()
    names = {p.get("path"): p.get("name", "") for p in W.projects()} if any(t.get("folder") for t in tasks) else {}
    rows = [_task_row(t, threads, names) for t in tasks]
    return {"tasks": rows, "templates": TEMPLATES, "problem": AS.tasks_problem()}


def account() -> dict:
    """What the account menu and the sidebar footer show. Meshpool keeps no remote profile: the name is the one in
    Settings, the plan is the state of the key, and 'this computer' is the one device."""
    p = SV.profile()
    name = (p["address"] or p["name"]).strip()
    first = (name.split() or [""])[0]
    key = bool(__import__("meshpool_harness.providers", fromlist=["x"]).key())
    rows = A.listing()
    code = [r for r in rows if is_code_session(r) and not r.get("archived")]
    return {"name": name, "first_name": first, "initial": (first or "M")[:1].upper(), "full_name": p["name"],
            "plan": "Meshpool key" if key else "No key yet", "key_set": key,
            "email": "Signed in with a Meshpool key" if key else "Not signed in",
            "device": {"name": platform.node() or "This computer", "connected": True, "os": platform.system()},
            "billing_url": SV.BILLING_URL, "keys_url": SV.BILLING_URL,
            "code_waiting": len([r for r in code if r.get("state") == "waiting"]),
            "chat_unread": len([r for r in rows if not is_code_session(r) and r.get("unread") and not r.get("archived")]),
            "code_unread": len([r for r in code if r.get("unread")]),
            "scheduled_unread": len([t for t in scheduled(rows)["tasks"] if t["unread"]])}


def _item(kind: str, id_: str, title: str, sub: str = "", **kw) -> dict:
    return {"kind": kind, "id": id_, "title": title, "sub": sub, **kw}


def _thread_item(r: dict, sub: str = "") -> dict:
    code = is_code_session(r)
    pr = r.get("pr") or {}
    return _item("code" if code else "chat", r["id"], r["title"], sub or (f"PR #{pr['number']}" if pr.get("number") else ""), unread=bool(r.get("unread")),
                 waiting=r.get("state") == "waiting", updated=r.get("updated", 0), project_id=r.get("project_id") or "")


def search(q: str, tab: str = "all") -> dict:
    """Search results as groups. An empty query gives what needs attention, recent items and actions."""
    q = (q or "").strip().lower()
    tab = tab if tab in TABS else "all"
    every = A.listing()                             # once: artifacts and scheduled below use it too
    rows = [r for r in every if not r.get("archived")]
    chats = [r for r in rows if not is_code_session(r)]
    codes = [r for r in rows if is_code_session(r)]
    want = lambda t: tab in ("all", t)  # noqa: E731
    groups: list[dict] = []

    def add(key: str, label: str, items: list[dict]):
        if items:
            groups.append({"key": key, "label": label, "items": items})

    if not q:
        att = [r for r in rows if r.get("state") == "waiting" or r.get("unread")]
        att = [r for r in att if (tab == "all" or (tab == "code" and is_code_session(r)) or (tab == "sessions" and not is_code_session(r)))]
        add("attention", "Needs attention", [_thread_item(r) for r in att[:6]])
        seen = {i["id"] for g in groups for i in g["items"]}
        recent = []
        if want("sessions"):
            recent += chats[:6]
        if want("code"):
            recent += codes[:6]
        recent.sort(key=lambda r: -r.get("updated", 0))
        add("recents", "Recents", [_thread_item(r) for r in recent if r["id"] not in seen][:6])
        if want("projects"):
            from meshpool_harness import project_store as PS
            add("projects", "Projects", [_item("project", p["id"], p["name"], p.get("description", ""), pinned=bool(p.get("pinned")))
                                         for p in PS.list_projects()[:5]])
        if want("artifacts"):
            add("artifacts", "Artifacts", [_item("artifact", a["id"], a["name"], a["type"], session_id=a["session_id"], root=a["root"], path=a["path"])
                                           for a in artifacts(20, every)["artifacts"][:5]])
        if want("scheduled"):
            add("scheduled", "Scheduled", [_item("scheduled", t["id"], t["name"], t["schedule"], unread=t["unread"])
                                           for t in scheduled(every)["tasks"][:5]])
        if tab == "all":
            add("actions", "Actions", [_item("action", "new_chat", "New chat"), _item("action", "new_session", "New session"),
                                       _item("action", "settings", "Open settings")])
        return {"q": "", "tab": tab, "groups": groups}

    if want("sessions") or want("code"):
        full = {h["id"]: h for h in A.search(q)}
        try:
            from meshpool_harness import summaries as SU
            boost = SU.search_boost(q)                       # a chat is also found by its one-line summary
        except Exception:  # noqa: BLE001
            SU, boost = None, {}
        seen = set()
        for key, label, pool, on in (("sessions", "Chats", chats, want("sessions")), ("code", "Code sessions", codes, want("code"))):
            if not on:
                continue
            items = []
            for r in pool:
                hit = full.get(r["id"])
                in_title = all(w in r["title"].lower() for w in q.split())     # every word, in any order
                if in_title or hit or r["id"] in boost:
                    seen.add(r["id"])
                    sub = (hit or {}).get("snippet", "") if not in_title else ""
                    if not sub and SU is not None:
                        sub = SU.summary_for(r["id"])
                    items.append(_thread_item(r, sub))
            add(key, label, items[:12])
    if want("projects"):
        from meshpool_harness import project_store as PS
        add("projects", "Projects", [_item("project", p["id"], p["name"], p.get("description", ""), pinned=bool(p.get("pinned")))
                                     for p in PS.list_projects() if q in p["name"].lower() or q in (p.get("description") or "").lower()][:8])
    if want("artifacts"):
        add("artifacts", "Artifacts", [_item("artifact", a["id"], a["name"], a["type"] + " · " + a["session_title"], session_id=a["session_id"],
                                             root=a["root"], path=a["path"])
                                       for a in artifacts(listing=every)["artifacts"] if q in a["name"].lower()][:8])
    if want("scheduled"):
        add("scheduled", "Scheduled", [_item("scheduled", t["id"], t["name"], t["schedule"], unread=t["unread"])
                                       for t in scheduled(every)["tasks"] if q in t["name"].lower() or q in t["prompt"].lower()][:8])
    return {"q": q, "tab": tab, "groups": groups, "now": time.time()}
