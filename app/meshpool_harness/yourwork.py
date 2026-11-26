"""Your work: a Reflect-style look back over 1, 3, 6 or 12 months, built only from what Meshpool already keeps on this
computer (saved chats, the outcome log, memory history, the files chats made). No model call, no invented numbers:
tokens are counted, cost and routing savings appear only where they were measured, and there is no "time saved".

Incognito chats are left out everywhere. The recap image is drawn from counts and categories only: no chat text,
no file names, no memory.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

RANGES = (1, 3, 6, 12)
MONTH = 30 * 86400
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BUILTIN_SERVERS = {"browser", "computer-use", "playwright"}
_STOP = set("""a an the and or of to for in on with my me i is it this that be please can you do what how make get use
about from into at as are was were will would should could have has had not no yes new just also more most some any all
your our their its then than them they we us one two three file files chat chats work need want help create write give
show tell let using used looks look like add changes change against over under between after before across
draft outline explain check compare estimate review plan rewrite analyze analyse summarize summarise numbers
user users wanted wants asked asks requested request requests needed needs number assistant meshpool agent model
provided gave made built created wrote produced received tried started finished done thing things something
there here which who whom whose when where while also very much many lot lots way ways today yesterday""".split())
# a stored chat summary reads "The user wanted a number of ...": its filler is not a topic (WF-05)
_SPEC_LABELS = {"docs.slides": "Slides", "docs.sheets": "Spreadsheets", "docs.word": "Documents", "docs": "Documents",
                "code": "Coding", "data": "Data analysis", "research": "Research", "debug": "Debugging",
                "build": "Building", "write": "Writing", "browse": "Browsing", "lead": "Lead generation",
                "refactor": "Refactoring", "chat": "Everyday questions"}


def _nosys(msgs) -> list:
    """A saved chat's messages without a leading system prompt (Agent.save() already leaves it out)."""
    msgs = list(msgs or [])
    return msgs[1:] if msgs and isinstance(msgs[0], dict) and msgs[0].get("role") == "system" else msgs


def _A():
    from meshpool_harness.ui import agent_api as A
    return A


def _words(text: str) -> set:
    out = set()
    for w in re.findall(r"(?<![a-z0-9])[a-z]+(?![a-z0-9])", (text or "").lower()):   # words only: no "user2", no ids
        if len(w) > 2 and w not in _STOP:
            w = w[:-1] if len(w) > 4 and w.endswith("s") else w
            if w not in _STOP:
                out.add(w)
    return out


def spec_label(spec: str | None) -> str:
    if not spec:
        return ""
    s = str(spec)
    for k in sorted(_SPEC_LABELS, key=len, reverse=True):
        if s == k or s.startswith(k + ".") or s.startswith(k):
            return _SPEC_LABELS[k]
    return s.split(".")[0].replace("_", " ").title()


def agent_name(hid: str) -> str:
    h = (hid or "").strip()
    if not h:
        return "Unknown"
    return re.sub(r"\s+", " ", h.replace("meshpool-", "Meshpool ").replace("-", " ").replace("_", " ")).strip().title() \
        .replace("Meshpool", "Meshpool")


# ---------- reading what Meshpool keeps ---------------------------------------------------------------------------------
def _chats(cutoff: float) -> list[dict]:
    """Saved chats updated since `cutoff`, incognito ones left out."""
    A = _A()
    out = []
    try:
        paths = [p for p in A.SESSIONS.glob("*/session.json") if not p.parent.name.startswith("sub-")]
    except OSError:
        return []
    for p in paths:
        sid = p.parent.name
        meta = A.read_meta(sid)
        if meta.get("incognito"):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        upd = float(d.get("updated") or 0)
        if upd < cutoff:
            continue
        users, tools = [], []
        for m in _nosys(d.get("messages")):
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            if m.get("role") == "user" and isinstance(c, str):
                t = c.split("\n\n​")[0].strip()
                if t:
                    users.append(t)
            for tc in m.get("tool_calls") or []:
                n = (tc.get("function") or {}).get("name")
                if n:
                    tools.append(n)
        if not users:
            continue
        out.append({"id": sid, "title": d.get("title") or "", "updated": upd, "users": users, "tools": tools,
                    "harness": d.get("harness") or "", "model": d.get("model") or "",
                    "project_id": meta.get("project_id") or "", "workdir": d.get("workdir") or "",
                    "kind": d.get("kind") or "cowork"})
    return out


def _outcomes(cutoff: float, allowed: set, until: float) -> list[dict]:
    try:
        from meshpool_harness.hrouter import outcomes as OUT
        lines = OUT.path().read_text(encoding="utf-8").splitlines()
    except (OSError, ImportError):
        return []
    rows = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if "outcome" in r or r.get("ts", 0) < cutoff or r.get("ts", 0) > until or r.get("chat") not in allowed:
            continue
        rows.append(r)
    return rows


def _artifacts(chats: list[dict]) -> list[dict]:
    try:
        from meshpool_harness.ui import hub
    except Exception:  # noqa: BLE001
        return []
    out = []
    for c in chats:
        try:
            files = hub._session_files({"id": c["id"], "workdir": c["workdir"], "kind": c["kind"], "state": "saved"})
        except Exception:  # noqa: BLE001
            continue
        for f in files:
            fp = Path(c["workdir"]) / f["path"]
            try:
                mt = fp.stat().st_mtime
            except OSError:
                continue
            label, tint = hub.KINDS[f["ext"]]
            out.append({"name": fp.name, "path": f["path"], "root": c["workdir"], "ext": f["ext"], "type": label,
                        "tint": tint, "updated": mt, "chat_id": c["id"], "title": c["title"]})
    out.sort(key=lambda a: -a["updated"])
    seen, once = set(), []                      # one entry per file: the same path made in several chats is one deliverable
    for a in out:                               # (newest first, so the newest copy is the one kept)
        key = (str(a["root"]).rstrip("/\\"), str(a["path"]).replace("\\", "/"))
        if key not in seen:
            seen.add(key)
            once.append(a)
    return once


def _skills_used(chats: list[dict]) -> list[dict]:
    try:
        from meshpool_harness import skills as SK
        names = {s.get("name", "").lower(): s.get("name", "") for s in SK.load_all()}
        names.update({s.get("id", "").lower(): s.get("name") or s.get("id", "") for s in SK.load_all()})
    except Exception:  # noqa: BLE001
        return []
    cnt: Counter = Counter()
    for c in chats:
        used = set()
        for u in c["users"]:
            for m in re.findall(r"\$([A-Za-z0-9_.-]+)", u):
                if m.lower() in names:
                    used.add(names[m.lower()])
        cnt.update(used)
    return [{"name": n, "chats": k} for n, k in cnt.most_common(5)]


def _connectors_used(chats: list[dict]) -> list[dict]:
    cnt: Counter = Counter()
    for c in chats:
        servers = set()
        for t in c["tools"]:
            m = re.match(r"mcp__([A-Za-z0-9_-]+?)__", t)
            if m and not m.group(1).startswith("meshpool") and m.group(1) not in BUILTIN_SERVERS:
                servers.add(m.group(1).replace("_", " ").replace("-", " "))
        cnt.update(servers)
    return [{"name": n, "chats": k} for n, k in cnt.most_common(5)]


def _teaching(cutoff: float) -> list[dict]:
    try:
        from meshpool_harness import assistant as AS
        from meshpool_harness import memstore as MS
        if not AS.settings()["memory_on"]:
            return []
        rows = MS.history(500)
    except Exception:  # noqa: BLE001
        return []
    keep = {"added": "Learned", "edited": "Changed", "archived": "Set aside", "restored": "Brought back"}
    out = []
    for r in rows:
        if (r.get("ts") or 0) >= cutoff and r.get("action") in keep:
            out.append({"ts": r["ts"], "what": keep[r["action"]], "by": r.get("by") or "", "text": r.get("text") or ""})
    return out[:40]


# ---------- topics ----------------------------------------------------------------------------------------------------
def _topics(chats: list[dict]) -> tuple[list[dict], dict]:
    try:
        from meshpool_harness import summaries as SM
    except Exception:  # noqa: BLE001
        SM = None
    df: Counter = Counter()
    docs = {}
    for c in chats:
        line = (SM.summary_for(c["id"]) if SM else "") or c["title"] or c["users"][0]
        w = _words(line) | _words(c["title"])
        docs[c["id"]] = w
        df.update(w)
    floor = 2                                               # a word in one chat only is not a topic you worked on
    out, used = [], set()
    for term, n in df.most_common(40):
        if n < floor or term in used:
            continue
        ids = [i for i, w in docs.items() if term in w]
        if any(set(ids) == set(x["ids"]) for x in out):
            continue                                        # the same chats as a topic already listed
        out.append({"topic": term, "chats": n, "ids": ids})
        used.add(term)
        if len(out) == 8:
            break
    return [{"topic": x["topic"], "chats": x["chats"], "share": round(x["chats"] / max(1, len(chats)), 2)} for x in out], \
        {x["topic"]: x["ids"] for x in out}


# ---------- suggestions (concrete, from track B) ----------------------------------------------------------------------
def _jacc(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _suggestions(chats: list[dict], topic_ids: dict, months: int) -> list[dict]:
    try:                                                        # the insights engine, when it offers them
        from meshpool_harness import insights as INS
        fn = getattr(INS, "work_suggestions", None)
        if callable(fn):
            got = fn(months)
            if got:
                return list(got)[:3]
    except Exception:  # noqa: BLE001
        pass
    out = []
    firsts = {c["id"]: _words(c["users"][0]) for c in chats}
    # the same request again and again: a scheduled task (near identical) or a skill (the same kind of procedure)
    done: set = set()
    for c in chats:
        if c["id"] in done or len(firsts[c["id"]]) < 3:
            continue
        same = [d for d in chats if _jacc(firsts[c["id"]], firsts[d["id"]]) >= 0.8]
        if len(same) >= 3:
            done |= {d["id"] for d in same}
            out.append({"kind": "schedule", "title": "Run this on a schedule",
                        "detail": f"You asked for nearly the same thing in {len(same)} chats.",
                        "evidence": [{"chat_id": d["id"], "ts": d["updated"]} for d in same[:5]],
                        "action": {"kind": "schedule", "label": "Schedule it",
                                   "payload": {"prompt": same[0]["users"][0][:600], "name": same[0]["title"][:60]}}})
            break
    if not out:
        for c in chats:
            sim = [d for d in chats if 0.45 <= _jacc(firsts[c["id"]], firsts[d["id"]]) < 0.8 or d["id"] == c["id"]]
            if len(sim) >= 3 and sum(len(d["tools"]) >= 4 for d in sim) >= 3 and len(firsts[c["id"]]) >= 3:
                out.append({"kind": "skill", "title": "Save this as a skill",
                            "detail": f"{len(sim)} chats followed the same steps.",
                            "evidence": [{"chat_id": d["id"], "ts": d["updated"]} for d in sim[:5]],
                            "action": {"kind": "skill", "label": "Draft a skill",
                                       "payload": {"chat_ids": [d["id"] for d in sim[:5]]}}})
                break
    loose = {c["id"] for c in chats if not c["project_id"]}
    for term, ids in topic_ids.items():
        ids = [i for i in ids if i in loose]
        if len(ids) >= 3:
            out.append({"kind": "project", "title": f"Make a project for your {term} chats",
                        "detail": f"{len(ids)} chats about {term} are outside any project.",
                        "evidence": [{"chat_id": i} for i in ids[:5]],
                        "action": {"kind": "project", "label": "Make a project",
                                   "payload": {"name": term.title(), "chat_ids": ids}}})
            break
    return out[:3]


# ---------- the aggregate ------------------------------------------------------------------------------------------------
def build(months: int = 3, now: float | None = None) -> dict:
    months = months if months in RANGES else 3
    now = now or time.time()
    cutoff = now - months * MONTH
    chats = _chats(cutoff)
    ids = {c["id"] for c in chats}
    rows = _outcomes(cutoff, ids, now + 60)
    topics, topic_ids = _topics(chats) if chats else ([], {})
    # busiest days and hours: from each turn's own time when the outcome log has it, otherwise the chat's last update
    stamps = [r["ts"] for r in rows] or [c["updated"] for c in chats]
    wd, hr, days = [0] * 7, [0] * 24, Counter()
    for t in stamps:
        d = datetime.fromtimestamp(t)
        wd[d.weekday()] += 1
        hr[d.hour] += 1
        days[d.strftime("%Y-%m-%d")] += 1
    kinds = Counter(spec_label(r.get("spec") or r.get("chat_spec")) for r in rows if r.get("spec") or r.get("chat_spec"))
    kinds.pop("", None)
    agents_c: Counter = Counter()
    models_c: Counter = Counter()
    turns_by: Counter = Counter()
    for c in chats:
        agents_c[c["harness"] or ""] += 1
        if c["model"]:
            models_c[c["model"]] += 1
    for r in rows:
        if r.get("harness"):
            turns_by[r["harness"]] += 1
    tok_in = sum(int(r.get("tokens_in") or 0) for r in rows)
    tok_out = sum(int(r.get("tokens_out") or 0) for r in rows)
    tok_cached = sum(int(r.get("tokens_cached") or 0) for r in rows)
    cost, unpriced, from_ledger = None, 0, False
    try:                                         # the call ledger: every model call with its own price, chats shown here only
        from meshpool_harness.core import usage as U
        U.flush()
        led = U.summarize(r for r in U.read_records(since=cutoff, until=now + 60) if r.get("chat") in ids)
        if led["calls"]:
            from_ledger = True
            tok_in, tok_out, tok_cached = led["in"], led["out"], led["cached"]
            unpriced = led["unpriced_calls"]
            cost = round(led["cost"], 4) if led["calls"] > unpriced else None
    except Exception:  # noqa: BLE001
        pass
    art = _artifacts(chats)
    skills = _skills_used(chats)
    conns = _connectors_used(chats)
    teaching = _teaching(cutoff)
    out = {
        "months": months, "since": cutoff, "now": now, "empty": not chats,
        "totals": {"chats": len(chats), "turns": len(rows) or sum(len(c["users"]) for c in chats),
                   "deliverables": len(art), "projects": len({c["project_id"] for c in chats if c["project_id"]})},
        "topics": topics,
        "kinds": [{"label": k, "count": n} for k, n in kinds.most_common(6)],
        "gallery": art[:30],
        "agents": [{"id": h, "name": agent_name(h), "chats": n, "turns": turns_by.get(h, 0)}
                   for h, n in agents_c.most_common(6)],
        "models": [{"name": m, "chats": n} for m, n in models_c.most_common(6)],
        "usage": {"tokens_in": tok_in, "tokens_out": tok_out, "tokens": tok_in + tok_out,
                  "cached": tok_cached, "measured_turns": len(rows),
                  "cost_usd": cost, "unpriced_calls": unpriced, "routing_savings_usd": None,
                  "note": ("Tokens and cost are counted from every model call Meshpool made for these chats, "
                           "engines and checks included. A cost is shown only for calls with a known price"
                           + (f"; {unpriced} call{'s' if unpriced != 1 else ''} had none." if unpriced else ".")
                           + " Routing savings are shown only when Meshpool has measured them, so they are blank here."
                           if from_ledger else
                           "Tokens are counted from your outcome log. Cost and routing savings are shown only when "
                           "Meshpool has measured them, so they are blank here.")},
        "when": {"weekday": wd, "hour": hr, "busiest_weekday": WEEKDAYS[wd.index(max(wd))] if any(wd) else "",
                 "busiest_hour": hr.index(max(hr)) if any(hr) else None,
                 "busiest_days": [{"date": d, "turns": n} for d, n in days.most_common(3)]},
        "skills": skills, "connectors": conns, "taught": teaching,
        "suggestions": _suggestions(chats, topic_ids, months) if chats else [],
        "privacy": "Incognito chats are not counted. Nothing here leaves your computer.",
    }
    return out


# ---------- the recap card ------------------------------------------------------------------------------------------------
def recap_inputs(months: int = 3, now: float | None = None, data: dict | None = None) -> dict:
    """Everything the recap image is drawn from: counts and category names only. No chat text, file names or memory."""
    d = data or build(months, now)
    return {"months": d["months"], "chats": d["totals"]["chats"], "turns": d["totals"]["turns"],
            "deliverables": d["totals"]["deliverables"],
            "kinds": [{"label": k["label"], "count": k["count"]} for k in d["kinds"][:5]],
            "agents": [{"name": a["name"], "count": a["chats"]} for a in d["agents"][:3]],
            "busiest_weekday": d["when"]["busiest_weekday"], "busiest_hour": d["when"]["busiest_hour"],
            "taught": len(d["taught"]), "tokens": d["usage"]["tokens"]}


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = (["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"] if bold else
             ["DejaVuSans.ttf", "Arial.ttf", "arial.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def recap_png(months: int = 3, now: float | None = None, inputs: dict | None = None) -> bytes:
    """A clean 1080 x 1350 card drawn locally from recap_inputs() alone."""
    from PIL import Image, ImageDraw
    r = inputs or recap_inputs(months, now)
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), "#101826")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill="#1F6FD0")
    d.text((72, 80), "Meshpool", font=_font(40, True), fill="#FFFFFF")
    span = "month" if r["months"] == 1 else f"{r['months']} months"
    d.text((72, 140), f"My work, last {span}", font=_font(30), fill="#9FB3CC")
    stats = [(r["chats"], "chats"), (r["turns"], "requests"), (r["deliverables"], "files made")]
    x = 72
    for n, label in stats:
        d.rounded_rectangle([x, 230, x + 296, 430], radius=24, fill="#18243A")
        d.text((x + 28, 262), f"{n:,}", font=_font(72, True), fill="#FFFFFF")
        d.text((x + 28, 362), label, font=_font(28), fill="#9FB3CC")
        x += 320
    y = 500
    d.text((72, y), "What I used it for", font=_font(32, True), fill="#FFFFFF")
    y += 66
    kinds = r["kinds"] or []
    top = max([k["count"] for k in kinds] or [1])
    if not kinds:
        d.text((72, y), "Not enough measured requests yet.", font=_font(28), fill="#9FB3CC")
        y += 60
    for k in kinds:
        d.text((72, y), k["label"], font=_font(28), fill="#FFFFFF")
        w = int(520 * k["count"] / top)
        d.rounded_rectangle([420, y + 4, 420 + max(w, 12), y + 36], radius=10, fill="#1F6FD0")
        d.text((420 + max(w, 12) + 16, y), str(k["count"]), font=_font(26), fill="#9FB3CC")
        y += 62
    y += 40
    d.text((72, y), "Agents", font=_font(32, True), fill="#FFFFFF")
    ag = ", ".join(a["name"] for a in r["agents"]) or "None yet"
    d.text((72, y + 54), ag[:60], font=_font(28), fill="#9FB3CC")
    y += 140
    when = []
    if r["busiest_weekday"]:
        when.append(f"Busiest day: {r['busiest_weekday']}")
    if r["busiest_hour"] is not None:
        when.append(f"Busiest hour: {r['busiest_hour']:02d}:00")
    if r["taught"]:
        when.append(f"Things I taught it: {r['taught']}")
    for i, t in enumerate(when):
        d.text((72, y + i * 46), t, font=_font(28), fill="#9FB3CC")
    d.text((72, H - 90), "Made on my computer. No chats or files are in this image.", font=_font(22), fill="#6E839F")
    buf = io.BytesIO()
    img.save(buf, "PNG")                                         # no metadata: nothing but pixels
    return buf.getvalue()


def recap_preview(months: int = 3, now: float | None = None) -> dict:
    """The same image as base64, for the preview before saving, with the exact inputs it was drawn from."""
    inputs = recap_inputs(months, now)
    png = recap_png(months, now, inputs)
    return {"png_b64": base64.b64encode(png).decode(), "width": 1080, "height": 1350, "inputs": inputs,
            "filename": f"meshpool-recap-{months}m.png"}
