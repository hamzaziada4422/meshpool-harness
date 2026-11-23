"""The memory page and the chat's memory controls, behind /api/memory/* and /api/xray.

    GET  /api/memory?q=&category=&scope=&kind=&status=   facts, counts, tray, conflicts, review queue, health
    GET  /api/memory/portrait[?force=1]                  3 to 5 sentences, each tied to the facts behind it
    GET  /api/memory/history                             the timeline of every change
    GET  /api/memory/export?type=md|onepager             a readable markdown file
    GET  /api/memory/sources?chat=<id>                   the facts a chat was given ("Given to the agent")
    GET  /api/xray?chat=&text=&project=&workdir=         exactly what the next message carries
    POST /api/memory  {action, ...}                      add, edit, pin, scope, expiry, archive, unarchive, delete,
                                                         undelete, confirm, reject, restore_version, resolve,
                                                         keep_both, consolidate, undo_noted, import_review
    POST /api/memory/exclude {chat, ids}                 facts left out of one chat
The server routes these paths here after its token check; handle() returns (status, payload[, content type]).
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import parse_qs

from meshpool_harness import assistant as AS
from meshpool_harness import memstore as MS


def _chat_title(cid: str) -> str:
    try:
        from meshpool_harness.ui import agent_api as AG
        m = AG.read_meta(cid)
        return m.get("title") or ""
    except Exception:  # noqa: BLE001
        return ""


def _day(ts) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%d %b").lstrip("0")
    except (TypeError, ValueError, OSError):
        return ""


def source_line(f: dict) -> dict:
    """Where a fact came from, in plain words, with the chat or file it links to."""
    s = f.get("source") or {}
    t, day = s.get("type"), _day(s.get("ts"))
    cid = s.get("chat_id") or ""
    title = _chat_title(cid) if cid else ""
    where = f" in {title}" if title else ""
    out = {"chat_id": cid, "quote": s.get("quote") or "", "file": s.get("file") or ""}
    if t == "chat":
        out["text"] = f"You said this on {day}{where}" if f["kind"] == "stated" else f"Saved on {day}{where}"
    elif t == "correction":
        out["text"] = f"Learned when you corrected the work on {day}{where}"
    elif t == "edit":
        name = (s.get("file") or "").replace("\\", "/").split("/")[-1]
        out["text"] = f"Learned when you edited {name or 'a file'} on {day}"
    elif t == "pattern":
        n = len(s.get("evidence") or [])
        out["text"] = f"Noticed across {n} chats" if n > 1 else "Noticed from your recent work"
    elif t == "import":
        out["text"] = f"Imported on {day}"
    elif t == "manual":
        out["text"] = f"You wrote this on {day}"
    elif t == "earlier-memory":
        out["text"] = "Moved from your earlier memory"
    else:
        out["text"] = f"Saved on {day}"
    return out


def _row(f: dict, used: dict) -> dict:
    n = used.get(f["id"], 0)
    return {k: f[k] for k in ("id", "text", "kind", "category", "scope", "status", "pinned", "valid_until", "created",
                              "updated", "last_given", "given_count")} | {
        "scope_label": MS.scope_label(f["scope"]), "source": source_line(f),
        "given_week": n, "given_line": (f"Given to agents in {n} chat{'s' if n != 1 else ''} this week" if n else
                                        ("Not given to an agent this week" if f["status"] == "active" else "")),
        "versions": len(f.get("history") or []), "expired": MS._expired(f)}


def view(q: dict) -> dict:
    g = lambda k: (q.get(k) or [""])[0]  # noqa: E731
    rows = MS.load()
    used = MS.usage_counts(7)
    qq, cat, scope, kind, status = g("q"), g("category"), g("scope"), g("kind"), g("status") or "active"
    out = []
    for f in MS.search(qq, rows):
        if status != "all" and f["status"] != status:
            continue
        if cat and f["category"] != cat:
            continue
        if scope and f["scope"] != scope:
            continue
        if kind and f["kind"] != kind:
            continue
        if g("unused") == "1" and ((f["last_given"] or 0) > time.time() - 60 * 86400 or f["created"] > time.time() - 60 * 86400):
            continue
        out.append(_row(f, used))
    order = {c: i for i, c in enumerate(MS.CATEGORIES)}
    out.sort(key=lambda r: (order.get(r["category"], 9), not r["pinned"], -r["updated"]))
    pending = [_row(f, used) for f in rows if f["status"] == "pending"]
    projects = []
    try:
        from meshpool_harness import project_store as PS
        for p in PS.list_projects():
            dt = PS.detail(p["id"])
            projects.append({"id": p["id"], "name": p["name"], "instructions": bool(dt["instructions"]),
                             "instructions_text": dt["instructions"][:240],
                             "knowledge": len(dt["knowledge"]), "files": [k["name"] for k in dt["knowledge"]][:8]})
    except Exception:  # noqa: BLE001
        pass
    by = {f["id"]: f for f in rows}
    conflicts = [dict(c, a_fact=_row(by[c["a"]], used), b_fact=_row(by[c["b"]], used)) for c in MS.conflicts()
                 if c["a"] in by and c["b"] in by]
    st = AS.settings()
    return {"facts": out, "pending": pending, "conflicts": conflicts,
            "review": [_row(f, used) | {"review_reason": f["review_reason"]} for f in MS.review_queue()],
            "health": MS.health(), "projects": projects, "settings": st,
            "labels": MS.CATEGORY_LABELS, "categories": list(MS.CATEGORIES),
            "counts": {"active": len([f for f in rows if f["status"] == "active"]),
                       "archived": len([f for f in rows if f["status"] == "archived"]),
                       "pending": len(pending)}}


def xray(q: dict) -> dict:
    """What the next message in this chat carries. A chat that has not sent its first message gets a dry run of the
    context it will get; a chat already under way shows what it was given at its start."""
    g = lambda k: (q.get(k) or [""])[0]  # noqa: E731
    cid, text, pid, workdir = g("chat"), g("text"), g("project"), g("workdir")
    meta = {}
    try:
        from meshpool_harness.ui import agent_api as AG
        meta = AG.read_meta(cid) if cid else {}
    except Exception:  # noqa: BLE001
        pass
    exclude = meta.get("memory_exclude") or []
    incog = bool(meta.get("incognito")) or g("incognito") == "1"
    started = bool(MS.context_snapshot(cid)) if cid else False
    ctx = AS.build_context(text, workdir, pid or meta.get("project_id") or "", cid, incog, exclude, record=False)
    by = {f["id"]: f for f in MS.load()}
    sections = []
    for s in ctx["sections"]:
        sections.append({"title": s["title"], "text": s["text"][:3000],
                         "facts": [{"id": i, "text": by[i]["text"], "scope_label": MS.scope_label(by[i]["scope"]),
                                    "kind": by[i]["kind"], "excluded": False} for i in s["ids"] if i in by]})
    left_out = [{"id": i, "text": by[i]["text"], "scope_label": MS.scope_label(by[i]["scope"]), "kind": by[i]["kind"],
                 "excluded": True} for i in exclude if i in by]
    return {"started": started, "incognito": incog, "memory_on": AS.settings()["memory_on"], "sections": sections,
            "excluded": left_out, "text": ctx["text"], "chars": len(ctx["text"]),
            "snapshot": MS.context_snapshot(cid) if started else "",
            "note": ("Already sent. Unticking asks the agent to ignore it." if started else "Sent with your first message.")}


def sources(cid: str) -> dict:
    used = MS.usage_counts(7)
    return {"facts": [_row(f, used) for f in MS.given_for_chat(cid)],
            "wording": "Given to the agent, not necessarily used."}


def _body_fact(b: dict) -> dict:
    f = MS.get(b.get("id", ""))
    if f is None:
        raise KeyError("That memory does not exist.")
    return f


def act(b: dict) -> dict:
    a = b.get("action", "")
    used = MS.usage_counts(7)
    row = lambda f: _row(f, used)  # noqa: E731
    if a == "add":
        f, made = MS.add(b.get("text", ""), kind="stated", category=b.get("category"),
                         scope=b.get("scope") or "everywhere", source={"type": "manual"})
        return {"fact": row(f), "created": made}
    if a == "edit":
        return {"fact": row(MS.update(b["id"], text=b.get("text", "")))}
    if a == "category":
        return {"fact": row(MS.update(b["id"], category=b.get("category", "")))}
    if a == "pin":
        return {"fact": row(MS.pin(b["id"], bool(b.get("on", True))))}
    if a == "scope":
        return {"fact": row(MS.set_scope(b["id"], b.get("scope", "everywhere")))}
    if a == "expiry":
        until = b.get("until")
        if isinstance(until, str) and until:
            until = datetime.fromisoformat(until.strip().replace("Z", "+00:00")).timestamp()
        return {"fact": row(MS.set_expiry(b["id"], float(until) if until else None))}
    if a == "archive":
        return {"fact": row(MS.archive(b["id"]))}
    if a == "unarchive":
        return {"fact": row(MS.unarchive(b["id"]))}
    if a == "delete":
        MS.delete(b["id"])
        return {"ok": True}
    if a == "undelete":
        return {"fact": row(MS.undelete(b["id"]))}
    if a == "confirm":
        return {"fact": row(MS.confirm(b["id"]))}
    if a == "confirm_edit":
        MS.update(b["id"], text=b.get("text", ""))
        return {"fact": row(MS.confirm(b["id"]))}
    if a == "reject":
        return {"fact": row(MS.reject(b["id"]))}
    if a == "wrong":                       # "Wrong" under a reply: archive it and teach the learner
        f = MS.get(b["id"])
        if f is None:
            raise KeyError("That memory does not exist.")
        MS.archive(b["id"], reason="You said it was wrong")
        MS._remember_rejection(f["text"])
        return {"ok": True}
    if a == "restore_version":
        return {"fact": row(MS.restore_version(b["id"], int(b.get("index", 0))))}
    if a == "resolve":
        MS.resolve_conflict(b["winner"], b["loser"])
        return {"ok": True}
    if a == "keep_both":
        MS.keep_both(b["a"], b["b"])
        return {"ok": True}
    if a == "consolidate":
        p = MS.consolidation()
        return MS.apply_consolidation(p["merges"] if b.get("merges", True) else [],
                                      p["archive"] if b.get("archive", True) else [])
    if a == "still_true":
        return {"fact": row(MS.still_true(b["id"]))}
    if a == "restore_text":                # Restore on a history row: put the text back
        return {"fact": row(MS.update(b["id"], reason="Restored from history", text=b.get("text", ""), status="active"))}
    if a == "undo_noted":                  # Undo on "Noted for next time"
        n = 0
        for i in b.get("ids", []):
            MS.archive(i, reason="You undid it right after it was noted")
            n += 1
        return {"archived": n}
    if a == "import_review":               # imported facts wait in the "Meshpool thinks" tray
        f, made = MS.add(b.get("text", ""), kind="stated", status="pending", source={"type": "import"}, by="meshpool")
        return {"fact": row(f), "created": made}
    if a == "settings":
        return {"settings": AS.save_settings(**{k: b[k] for k in ("memory_on", "instructions_on", "learn_from_edits", "insights_on", "quiet_hours") if k in b})}
    if a == "refresh_portrait":
        return MS.portrait(force=True)
    raise ValueError("Unknown action.")


def handle(method: str, u, body: bytes = b"") -> tuple:
    import json
    q = parse_qs(u.query)
    path = u.path
    if method == "GET":
        if path == "/api/memory":
            return 200, view(q)
        if path == "/api/memory/portrait":
            return 200, MS.portrait(force=(q.get("force") or [""])[0] == "1")
        if path == "/api/memory/history":
            return 200, {"history": MS.history(300)}
        if path == "/api/memory/export":
            kind = (q.get("type") or ["md"])[0]
            text = MS.export_onepager() if kind == "onepager" else MS.export_markdown()
            return 200, text.encode("utf-8"), "text/markdown; charset=utf-8"
        if path == "/api/memory/sources":
            return 200, sources((q.get("chat") or [""])[0])
        if path == "/api/xray":
            return 200, xray(q)
        return 404, {"error": "not found"}
    b = json.loads(body.decode("utf-8")) if body else {}
    if path == "/api/memory":
        try:
            return 200, act(b)
        except KeyError as e:
            return 404, {"error": str(e).strip("'\"")}
    if path == "/api/memory/exclude":
        from meshpool_harness.ui import agent_api as AG
        cid = b.get("chat", "")
        ids = [i for i in b.get("ids", []) if isinstance(i, str)][:200]
        AG.write_meta(cid, memory_exclude=ids)
        return 200, {"ok": True, "ids": ids}
    return 404, {"error": "not found"}
