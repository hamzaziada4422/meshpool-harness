"""Insights: an agent that notices, quietly.

Local detectors over data Meshpool already keeps (chats, scheduled tasks, the routing outcome log, a project's folder,
the files Meshpool made). No model call anywhere in detection. Cards surface only at a boundary (app open, chat open,
turn end, scheduled-task end), within a small daily budget, and each says what Meshpool saw.

Card:  {id, type, title, body, evidence: [{text, chat_id?, file?, ts?}], action: {kind, label, payload}, score,
        created, expires, dedupe_key, where: "home"|"turn"|"task"}      (+ chat_id, turn_n on turn cards)
Log:   {ts, card_id, type, event: shown|accepted|dismissed|muted|expired}      one JSON line each, insights/log.jsonl

Files under <Meshpool data folder>/insights/: cards.json (cards waiting or shown), log.jsonl, state.json (per-type
switches, mutes, throttle, notification count), settings.json (switches and quiet hours; kept apart from the
memory settings file, which assistant.save_settings rewrites).

Rules the module keeps: at most 1 non-chip card at a turn end (plus the next-step chips), 3 on the home screen per
day, 1 OS notification per day; none in quiet hours, none when Suggestions is off (Settings or MH_INSIGHTS=0), none
from an incognito chat, no memory-derived card when memory is off; a type under 15% acceptance after 10 showings
stops by itself; a dismissed or accepted card never comes back; cards expire after 7 days.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import threading
import time
from datetime import datetime
from pathlib import Path

from meshpool_harness import config as C

TYPES = ("schedule", "skill", "project", "route", "connect", "rerun", "teach", "remember", "followup")
LABELS = {"schedule": "Repeat a task on a schedule", "skill": "Turn a routine into a skill",
          "project": "Make a project from repeated background", "route": "Pick a better agent or a cheaper one",
          "connect": "Connect a service you paste from", "rerun": "Run a task again on a new file",
          "teach": "Make your edits your defaults", "remember": "Add something Meshpool noticed to memory",
          "followup": "Next-step chips after a reply"}
# Precision of 0.8 or better on the labelled fixtures ships on; below that ships off (docs/plan/0.0.10-precision.md).
DEFAULT_ON = {t: True for t in TYPES}
VALUE = {"schedule": 1.0, "skill": 0.9, "project": 0.9, "route": 0.8, "connect": 0.7, "rerun": 0.9, "teach": 1.0,
         "remember": 0.5, "followup": 0.6}
MEMORY_TYPES = {"remember", "teach"}                  # derived from memory, or writing to it: off with memory
EXPIRY_S = 7 * 86400
HOME_PER_DAY = 2                      # fewer is better: a weak or repeated card is worse than none
MIN_HOME = 0.35                       # below this ranked score a card is not worth the screen
TURN_CARDS = 1
OS_PER_DAY = 1
THROTTLE_MIN_SHOWN = 10
THROTTLE_RATE = 0.15
RESCAN_S = 120
MIN_CHATS = 3
_LOCK = threading.RLock()
_SCAN: dict = {}
AUTO_TEXT = re.compile(r"(?i)^(continue where you left off\.?|continue working toward the goal\.?)$")


# ---------- files, switches ------------------------------------------------------------------------------------------
def folder() -> Path:
    d = C.home() / "insights"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jread(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _jwrite(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    C.replace_file(tmp, p)


def _state() -> dict:
    s = _jread(folder() / "state.json", {})
    if not isinstance(s, dict):
        s = {}
    s.setdefault("types", {})
    s.setdefault("mutes", [])                  # [{id, ts, type, card_id, title, active}] "show me less like this"
    s.setdefault("osn", {})                    # {"2026-11-22": 1}
    s.setdefault("notices", [])                # one-time Settings notes, e.g. a type stopped by itself
    return s


def _save_state(s: dict) -> None:
    _jwrite(folder() / "state.json", s)


def settings() -> dict:
    d = _jread(folder() / "settings.json", {})
    q = d.get("quiet_hours") or {}
    return {"insights_on": bool(d.get("insights_on", True)),
            "learn_from_edits": bool(d.get("learn_from_edits", True)),
            "quiet_hours": {"on": bool(q.get("on", bool(q.get("start") and q.get("end")))),
                            "start": str(q.get("start") or "22:00"), "end": str(q.get("end") or "07:00")}}


def save_settings(**kw) -> dict:
    d = _jread(folder() / "settings.json", {})
    if "insights_on" in kw:
        d["insights_on"] = bool(kw["insights_on"])
    if "learn_from_edits" in kw:
        d["learn_from_edits"] = bool(kw["learn_from_edits"])
    if isinstance(kw.get("quiet_hours"), dict):
        q = kw["quiet_hours"]
        cur = settings()["quiet_hours"]
        for k in ("start", "end"):
            if re.fullmatch(r"\d{1,2}:\d{2}", str(q.get(k, ""))):
                cur[k] = str(q[k])
        if "on" in q:
            cur["on"] = bool(q["on"])
        d["quiet_hours"] = cur
    _jwrite(folder() / "settings.json", d)
    return settings()


def enabled() -> bool:
    """Suggestions on: the Settings switch and MH_INSIGHTS (0 turns every detector and card off)."""
    return os.environ.get("MH_INSIGHTS", "1") != "0" and settings()["insights_on"]


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def quiet_now(now: float | None = None) -> bool:
    q = settings()["quiet_hours"]
    if not q["on"]:
        return False
    t = datetime.fromtimestamp(now or time.time())
    cur, a, b = t.hour * 60 + t.minute, _hm(q["start"]), _hm(q["end"])
    return (a <= cur < b) if a <= b else (cur >= a or cur < b)


def _memory_on() -> bool:
    try:
        from meshpool_harness import assistant as A
        return A.settings()["memory_on"]
    except Exception:  # noqa: BLE001
        return True


def type_on(t: str) -> bool:
    st = _state()["types"].get(t, {})
    if st.get("throttled") or st.get("on") is False:
        return False
    if t in MEMORY_TYPES and not _memory_on():
        return False
    return st["on"] if st.get("on") is not None else DEFAULT_ON.get(t, True)


# ---------- reading chats ------------------------------------------------------------------------------------------------
_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "my", "me", "i", "is", "it", "this",
         "that", "be", "please", "can", "you", "do", "what", "how", "make", "get", "use", "from", "into", "me", "our",
         "your", "all", "are", "was", "will", "would", "then", "also", "just"}
_FILE = re.compile(r"[\w .()\-]+\.(?:xlsx|xls|csv|pptx|docx|pdf|md|txt|json|py|js|ts|html|png)$", re.I)
_OUT_TOOLS = re.compile(r"(?i)write|make|create|save|deck|slides|sheet|workbook|doc|edit|export")
_IN_TOOLS = re.compile(r"(?i)^(read_file|read|open|inspect|preview|load|grep|glob|attach)")


def _runs() -> Path:
    return C.runs_dir() / "sessions"


def words(text: str) -> set:
    t = re.sub(r"\d+", "#", (text or "").lower())
    return {w[:6] for w in re.findall(r"[a-z#][a-z#']+", t) if len(w) > 2 and w not in _STOP}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _text_of(c) -> str:
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return str(c or "").split("​")[0].strip()


def chat_record(sid: str, meta: dict | None = None, sess: dict | None = None) -> dict | None:
    """One chat reduced to what the detectors need. Reads session.json and meta.json; never raises."""
    d = _runs() / sid
    sess = sess if sess is not None else _jread(d / "session.json", None)
    if not sess:
        return None
    meta = meta if meta is not None else _jread(d / "meta.json", {})
    texts, tools, outs, ins = [], [], [], []
    reply = ""
    for m in sess.get("messages") or []:
        if m.get("role") == "assistant" and _text_of(m.get("content")):
            reply = _text_of(m.get("content"))                 # the last thing said: what a next-step chip must relate to
        if m.get("role") == "user":
            t = _text_of(m.get("content"))
            if t and not AUTO_TEXT.match(t) and not t.startswith(("[Meshpool]", "[meshpool]")):
                texts.append(t)
        elif m.get("role") == "assistant":
            for c in m.get("tool_calls") or []:
                f = c.get("function") or {}
                name = f.get("name", "")
                tools.append(name)
                try:
                    args = json.loads(f.get("arguments") or "{}")
                except ValueError:
                    args = {}
                for k in ("path", "file_path", "file", "output", "filename", "name"):
                    v = args.get(k) if isinstance(args, dict) else None
                    if isinstance(v, str) and _FILE.search(v):
                        base = v.replace("\\", "/").rsplit("/", 1)[-1]
                        (ins if _IN_TOOLS.match(name) else outs if _OUT_TOOLS.search(name) else ins).append(base)
    route = sess.get("route") or {}
    return {"id": sid, "ts": float(sess.get("updated") or 0), "created": float(sess.get("created") or 0),
            "title": sess.get("title") or "", "texts": texts, "tools": tools, "steps": len(tools), "reply": reply[:4000],
            "outputs": list(dict.fromkeys(outs + list(meta.get("outputs") or []))),
            "inputs": list(dict.fromkeys(ins + list(meta.get("inputs") or []))),
            "project_id": meta.get("project_id") or "", "incognito": bool(meta.get("incognito")),
            "scheduled": meta.get("scheduled") or "", "harness": meta.get("routed") or sess.get("harness") or "",
            "kind": sess.get("kind") or meta.get("kind") or "", "spec": meta.get("route") or route.get("spec") or "",
            "workdir": sess.get("workdir") or ""}


def load_chats(limit: int = 400) -> list[dict]:
    root = _runs()
    out = []
    if root.is_dir():
        paths = []
        for p in root.glob("*/session.json"):
            if p.parent.name.startswith("sub-"):
                continue
            try:
                paths.append((p.stat().st_mtime, p.parent.name))
            except OSError:
                continue
        for _, sid in sorted(paths, reverse=True)[:limit]:
            r = chat_record(sid)
            if r:
                out.append(r)
    return sorted(out, key=lambda r: r["ts"])


def _outcomes() -> list[dict]:
    try:
        from meshpool_harness.hrouter import outcomes as OUT
        rows = []
        for line in OUT.path().read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows
    except Exception:  # noqa: BLE001
        return []


def _folder_files(chats: list[dict]) -> dict:
    """{project_id: [{name, path, mtime}]}: names and times only, a shallow listing of the project's folder."""
    out: dict = {}
    try:
        from meshpool_harness import project_store as PS
        for p in PS.list_projects():
            link = (p.get("link") or {}).get("path") or ""
            if not link or not Path(link).is_dir():
                continue
            rows = []
            for i, f in enumerate(sorted(Path(link).iterdir())):
                if i > 300:
                    break
                if f.is_file() and not f.name.startswith("."):
                    try:
                        rows.append({"name": f.name, "path": str(f), "mtime": f.stat().st_mtime})
                    except OSError:
                        pass
            out[p["id"]] = rows
    except Exception:  # noqa: BLE001
        pass
    return out


def _teach_candidates() -> list[dict]:
    """Edits the user made to files Meshpool produced (C4): [{file, chat_id, kind, count, total, rules, label}]."""
    if not settings()["learn_from_edits"]:
        return []
    try:
        from meshpool_harness import teach
        return list(teach.detect())
    except Exception:  # noqa: BLE001 - the teach module may not be there, or nothing changed
        return []


def _connector_state(service: str) -> dict | None:
    """{"name", "connected"} for a service word, or None when the catalog has none. Reuses suggest._connectors."""
    try:
        from meshpool_harness import suggest as S
        hits = S._connectors(f" in {service} ")
        if hits:
            return {"name": hits[0]["label"].replace("Connect ", "").replace("Turn on ", ""),
                    "connected": False, "id": hits[0]["id"], "type": hits[0]["type"]}
        from meshpool_harness import mcp_apps as CN
        for c in CN.listing():
            if service in (c["id"], c["name"].lower()) or (service == "jira" and c["id"] == "atlassian"):
                return {"name": c["name"], "connected": bool(c["connected"] and c["enabled"]), "id": c["id"],
                        "type": "none"}
    except Exception:  # noqa: BLE001
        pass
    return None


def _header(path: str):
    """The first row of a csv or workbook (the file's structure), or None."""
    try:
        if path.lower().endswith(".csv"):
            with open(path, encoding="utf-8", errors="replace") as f:
                return tuple(x.strip().lower() for x in f.readline().split(","))
        if path.lower().endswith(".xlsx"):
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            ws = wb.worksheets[0]
            row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
            wb.close()
            return tuple(str(x).strip().lower() for x in row if x is not None)
    except Exception:  # noqa: BLE001
        return None
    return None


class Corpus:
    """What the detectors read. Built from disk by corpus(); tests build one directly."""

    def __init__(self, chats=None, tasks=None, outcomes=None, folder_files=None, teach=None, connector=None,
                 header=None, now=None):
        self.now = now or time.time()
        self.chats = [c for c in (chats or []) if not c.get("incognito")]       # incognito logs nothing
        self.tasks = tasks or []
        self.outcomes = outcomes or []
        self.folder_files = folder_files or {}
        self.teach = teach or []
        self.connector = connector or _connector_state
        self.header = header or _header


def corpus(now: float | None = None) -> Corpus:
    chats = load_chats()
    try:
        from meshpool_harness import assistant as A
        tasks = A.tasks()
    except Exception:  # noqa: BLE001
        tasks = []
    return Corpus(chats, tasks, _outcomes(), _folder_files(chats), _teach_candidates(), now=now)


# ---------- detector helpers ----------------------------------------------------------------------------------------------
def _key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d %b").lstrip("0")


def _clip(t: str, n: int = 70) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _card(typ, title, body, evidence, action, confidence, c: Corpus, dedupe, where="home", **extra) -> dict:
    return {"id": "ins_" + _key(dedupe), "type": typ, "title": title, "body": body, "evidence": evidence,
            "action": action, "score": round(confidence * VALUE[typ], 3), "confidence": round(confidence, 3),
            "created": c.now, "expires": c.now + EXPIRY_S, "dedupe_key": dedupe, "where": where, **extra}


def clusters(chats: list[dict], thr: float = 0.8) -> list[list[dict]]:
    """Chats whose first message says nearly the same thing (same words, numbers ignored), outside scheduled runs."""
    groups: list[tuple[set, list]] = []
    for ch in chats:
        if ch.get("scheduled") or not ch["texts"]:
            continue
        w = words(ch["texts"][0])
        if len(w) < 3:
            continue
        for gw, members in groups:
            if jaccard(w, gw) >= thr:
                members.append(ch)
                break
        else:
            groups.append((w, [ch]))
    return [m for _, m in groups if len(m) >= 2]


def rhythm(times: list[float]) -> dict | None:
    """A regular gap between runs: {every_s, label, day, time, spread} or None. Gaps within 35% of the median."""
    ts = sorted(times)
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b - a > 3600]
    if len(gaps) < len(ts) - 2 or len(gaps) < 2:
        return None
    med = statistics.median(gaps)
    if not 18 * 3600 <= med <= 45 * 86400:
        return None
    spread = max(abs(g - med) for g in gaps) / med
    if spread > 0.35:
        return None
    days = med / 86400
    label = "daily" if days < 2 else "weekly" if 5.5 <= days <= 9 else "monthly" if 26 <= days <= 34 else "every"
    dts = [datetime.fromtimestamp(t) for t in ts]
    mins = [d.hour * 60 + d.minute for d in dts]
    mm = int(statistics.median(mins))
    wd = max(set(d.weekday() for d in dts), key=[d.weekday() for d in dts].count)
    return {"every_s": med, "label": label, "spread": round(spread, 2), "time": f"{mm // 60:02d}:{mm % 60:02d}",
            "day": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][wd]}


def _failed_turns(rows: list[dict]) -> dict:
    """Outcome rows with a `failed` flag: an error, a stop, or a later undo, retry or "wrong" mark on the chat."""
    turns, last = [], {}
    for r in sorted(rows, key=lambda r: r.get("ts", 0)):
        if "outcome" in r:
            t = last.get(r.get("chat"))
            if t is not None and str(r["outcome"]).lower() in ("undo", "undone", "retry", "retried", "wrong", "down",
                                                                "thumbs_down", "bad"):
                t["failed"] = True
            continue
        t = dict(r, failed=bool(r.get("error") or r.get("stopped")))
        turns.append(t)
        last[r.get("chat")] = t
    return turns


# ---------- detectors ------------------------------------------------------------------------------------------------------
def detect_schedule(c: Corpus) -> list[dict]:
    out = []
    for grp in clusters(c.chats):
        if len(grp) < MIN_CHATS:
            continue
        rh = rhythm([m["ts"] for m in grp])
        if not rh:
            continue
        w0 = words(grp[0]["texts"][0])
        if any(jaccard(w0, words(t.get("prompt", ""))) >= 0.6 for t in c.tasks):
            continue                                              # already scheduled
        keep = [m for m in grp if not m.get("failed")][-1]       # the run the user kept
        text = keep["texts"][0]
        dates = ", ".join(_day(m["ts"]) for m in grp[-4:])
        every = {"daily": "every day", "weekly": f"every week on {rh['day'].capitalize()}", "monthly": "every month",
                 "every": f"every {max(1, round(rh['every_s'] / 86400))} days"}[rh["label"]]
        conf = min(1.0, 0.65 + 0.1 * (len(grp) - 3)) * (1 - rh["spread"])
        payload = {"name": _clip(text, 40), "prompt": text, "when": rh["label"] if rh["label"] != "monthly" else "every",
                   "time": rh["time"], "day": rh["day"], "minutes": int(rh["every_s"] / 60),
                   "only_on_change": True, "source_chats": [m["id"] for m in grp]}
        out.append(_card("schedule", f"Run this {every}?",
                         f"Asked {len(grp)} times, about {rh['label'] if rh['label'] != 'every' else 'regularly'}: “{_clip(text, 48)}”",
                         [{"text": f"{_day(m['ts'])}: {_clip(m['texts'][0], 50)}", "chat_id": m["id"], "ts": m["ts"]}
                          for m in grp[-5:]],
                         {"kind": "schedule", "label": "Set up the schedule", "payload": payload}, conf, c,
                         "schedule:" + _key(sorted(words(text))[:6])))
    return out


def detect_skill(c: Corpus) -> list[dict]:
    out, seen = [], set()
    from meshpool_harness import learn as L
    for grp in clusters(c.chats):                                 # a multi-step procedure done again and again
        if len(grp) < MIN_CHATS or rhythm([m["ts"] for m in grp]):
            continue
        steps = [m for m in grp if m["steps"] >= 3]
        if len(steps) < MIN_CHATS:
            continue
        text = steps[-1]["texts"][0]
        seen.update(m["id"] for m in steps)
        out.append(_card("skill", "Save this routine as a skill?",
                         f"Ran in {len(steps)} chats: “{_clip(text, 48)}”",
                         [{"text": _clip(m["texts"][0], 60), "chat_id": m["id"], "ts": m["ts"]} for m in steps[-5:]],
                         {"kind": "draft_skill", "label": "Draft the skill",
                          "payload": {"chat_ids": [m["id"] for m in steps], "hint": _clip(text, 40)}},
                         min(1.0, 0.6 + 0.1 * (len(steps) - 3)), c, "skill:proc:" + _key(sorted(words(text))[:6])))
    corr = []                                                     # the same correction in several chats
    for ch in c.chats:
        for t in ch["texts"]:
            if L.GATE.search(t) and 12 <= len(t) <= 400:
                corr.append((words(t), t, ch))
    used = set()
    for i, (w, t, ch) in enumerate(corr):
        if i in used or len(w) < 3:
            continue
        mem = {ch["id"]: (t, ch)}
        idx = [i]
        for j in range(i + 1, len(corr)):
            if j not in used and corr[j][2]["id"] not in mem and jaccard(w, corr[j][0]) >= 0.5:
                mem[corr[j][2]["id"]] = (corr[j][1], corr[j][2])
                idx.append(j)
        if len(mem) >= MIN_CHATS:
            used.update(idx)
            chs = [x[1] for x in mem.values()]
            out.append(_card("skill", "Make this a skill instead of repeating it?",
                             f"Same correction in {len(mem)} chats: “{_clip(t, 48)}”",
                             [{"text": _clip(x[0], 60), "chat_id": x[1]["id"], "ts": x[1]["ts"]} for x in mem.values()],
                             {"kind": "draft_skill", "label": "Draft the skill",
                              "payload": {"chat_ids": [x["id"] for x in chs], "hint": _clip(t, 40)}},
                             min(1.0, 0.6 + 0.1 * (len(mem) - 3)), c, "skill:corr:" + _key(sorted(w)[:6])))
    return out


def _paragraphs(text: str):
    for p in re.split(r"\n\s*\n", text):
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) >= 200:
            yield p


def detect_project(c: Corpus) -> list[dict]:
    loose = [ch for ch in c.chats if not ch["project_id"] and not ch["scheduled"] and ch["kind"] != "code"]
    by_para: dict = {}
    by_file: dict = {}
    for ch in loose:
        for t in ch["texts"][:3]:
            for p in _paragraphs(t):
                by_para.setdefault(_key(re.sub(r"\W+", " ", p.lower())[:200]), {}).setdefault(ch["id"], (p, ch))
        for f in set(ch["inputs"]):
            by_file.setdefault(f.lower(), {})[ch["id"]] = (f, ch)
    out = []
    for k, mem in by_para.items():
        if len(mem) >= MIN_CHATS:
            chs = [x[1] for x in mem.values()]
            p = next(iter(mem.values()))[0]
            files = sorted({f for ch in chs for f in ch["inputs"]})[:8]
            out.append(_card("project", "Make this a project?",
                             f"Same background pasted into {len(mem)} chats.",
                             [{"text": f"Same passage: “{_clip(p, 50)}”", "chat_id": ch["id"], "ts": ch["ts"]} for ch in chs[-4:]],
                             {"kind": "make_project", "label": "Make a project",
                              "payload": {"name": _clip(p, 36), "instructions": p[:1500], "files": files,
                                          "chat_ids": [ch["id"] for ch in chs]}},
                             min(1.0, 0.65 + 0.1 * (len(mem) - 3)), c, "project:para:" + k))
    for k, mem in by_file.items():
        covered = {i for x in out for i in x["action"]["payload"]["chat_ids"]}      # a pasted-background card has them
        if len(mem) >= MIN_CHATS and not set(mem) <= covered:
            chs = [x[1] for x in mem.values()]
            name = next(iter(mem.values()))[0]
            out.append(_card("project", "Make this a project?",
                             f"“{name}” used in {len(mem)} chats.",
                             [{"text": f"Used {name}", "chat_id": ch["id"], "ts": ch["ts"], "file": name} for ch in chs[-4:]],
                             {"kind": "make_project", "label": "Make a project",
                              "payload": {"name": Path(name).stem[:36], "instructions": "", "files": [name],
                                          "chat_ids": [ch["id"] for ch in chs]}},
                             min(0.9, 0.55 + 0.1 * (len(mem) - 3)), c, "project:file:" + k))
    return out


def detect_route(c: Corpus) -> list[dict]:
    turns = _failed_turns(c.outcomes)
    by: dict = {}
    for t in turns:
        if t.get("spec") and t.get("harness"):
            by.setdefault(t["spec"], {}).setdefault(t["harness"], []).append(t)
    out = []
    for spec, hs in by.items():
        rate = lambda xs: sum(1 for x in xs if x["failed"]) / len(xs)   # noqa: E731
        tok = lambda xs: statistics.mean((x.get("tokens_in", 0) or 0) + (x.get("tokens_out", 0) or 0) for x in xs)  # noqa: E731
        for h, xs in hs.items():
            last6 = xs[-6:]
            bad = sum(1 for x in last6 if x["failed"])
            if len(last6) == 6 and bad >= 3:                        # undone, retried or wrong in 3 of the last 6
                better = [(rate(ys), g) for g, ys in hs.items() if g != h and len(ys) >= 3 and rate(ys) + 0.34 <= bad / 6]
                if better:
                    r, g = min(better)
                    out.append(_card("route", f"Try another agent for {spec.split('.')[-1] or spec} work?",
                                     f"{bad} of the last 6 on {h} failed. On {g}: {sum(1 for y in hs[g] if y['failed'])} of {len(hs[g])}.",
                                     [{"text": f"{h}: {bad} of 6 failed"}, {"text": f"{g}: {sum(1 for y in hs[g] if y['failed'])} of {len(hs[g])} failed"}],
                                     {"kind": "set_default", "label": f"Use {g} for this",
                                      "payload": {"spec": spec, "harness": g, "lane": last6[-1].get("lane")}},
                                     min(1.0, 0.6 + (bad / 6 - r) / 2), c, f"route:fail:{spec}:{h}:{g}"))
            if len(xs) >= 10:                                      # a cheaper option with the same measured quality
                for g, ys in hs.items():
                    if g == h or len(ys) < 5 or rate(ys) > rate(xs):
                        continue
                    a, b = tok(xs), tok(ys)
                    if a > 0 and b <= 0.7 * a:
                        out.append(_card("route", f"Same quality for about {round((1 - b / a) * 100)}% fewer tokens?",
                                         f"{g} used {round((1 - b / a) * 100)}% fewer tokens than {h}, with no more failures.",
                                         [{"text": f"{h}: {len(xs)} turns, {round(a)} tokens each, {sum(1 for x in xs if x['failed'])} failed"},
                                          {"text": f"{g}: {len(ys)} turns, {round(b)} tokens each, {sum(1 for y in ys if y['failed'])} failed"}],
                                         {"kind": "set_default", "label": f"Use {g} for this",
                                          "payload": {"spec": spec, "harness": g, "lane": xs[-1].get("lane")}},
                                         0.7, c, f"route:cost:{spec}:{h}:{g}"))
    return out


# Jira keys alone (OPS-12) look like part numbers and invoice ids, so they count only beside a word that says tickets.
_JIRA_WORD = re.compile(r"(?i)\b(jira|tickets?|issues?|sprint|epic|stories|story|backlog|blocked|blockers?)\b")
_SERVICES = [("jira", re.compile(r"atlassian\.net/browse/|\b(?!(?:COVID|ISO|UTF|SHA|HTTP|RFC|ANSI|IEEE|GPT|USB|MD|PEP|CVE|INV)-)"
                                 r"([A-Z][A-Z0-9]{1,9})-\d{1,6}\b.*?\b\1-\d{1,6}\b", re.S)),
             ("notion", re.compile(r"notion\.(?:so|site)/[\w\-/]{6,}")),
             ("linear", re.compile(r"linear\.app/[\w\-]+/issue/[\w\-]+")),
             ("slack", re.compile(r"[\w\-]+\.slack\.com/archives/[A-Z0-9]+/p\d+"))]


def detect_connect(c: Corpus) -> list[dict]:
    out = []
    for svc, rx in _SERVICES:
        hit = [ch for ch in c.chats if any(rx.search(t) and (svc != "jira" or "atlassian.net" in t or _JIRA_WORD.search(t))
                                           for t in ch["texts"])]
        if len(hit) < 2:
            continue
        st = c.connector(svc)
        if not st or st.get("connected"):
            continue
        out.append(_card("connect", f"Connect {st['name']}?",
                         f"You pasted {st['name']} content into {len(hit)} chats.",
                         [{"text": f"{st['name']} content in “{_clip(ch['title'] or ch['texts'][0], 40)}”",
                           "chat_id": ch["id"], "ts": ch["ts"]} for ch in hit[-4:]],
                         {"kind": "connect", "label": f"Connect {st['name']}",
                          "payload": {"connector": st.get("id") or svc}}, min(1.0, 0.5 + 0.15 * len(hit)), c,
                         f"connect:{svc}"))
    return out


def pattern(name: str) -> str:
    """A file name with its numbers blanked: Q3_sales.xlsx and Q4_sales.xlsx share one pattern."""
    return re.sub(r"\d+", "#", name.lower())


def detect_rerun(c: Corpus) -> list[dict]:
    out = []
    for pid, files in c.folder_files.items():
        prior = [ch for ch in c.chats if ch["project_id"] == pid and ch["inputs"] and ch["texts"]]
        known = {n.lower() for ch in prior for n in ch["inputs"]}
        for f in files:
            if f["name"].lower() in known or "#" not in pattern(f["name"]):
                continue
            for ch in sorted(prior, key=lambda x: -x["ts"]):
                if f["mtime"] <= ch["ts"]:
                    continue
                old = next((n for n in ch["inputs"] if pattern(n) == pattern(f["name"]) and n.lower() != f["name"].lower()), "")
                if not old:
                    continue
                h1 = c.header(next((x["path"] for x in files if x["name"] == old), "")) if any(x["name"] == old for x in files) else None
                h2 = c.header(f["path"])
                if h1 and h2 and jaccard(set(h1), set(h2)) < 0.6:
                    continue                                        # same name pattern, different structure
                out.append(_card("rerun", f"Run “{_clip(ch['texts'][0], 40)}” on {f['name']}?",
                                 f"Looks like {old}, which you used on {_day(ch['ts'])}.",
                                 [{"text": f"Earlier: {_clip(ch['texts'][0], 60)}", "chat_id": ch["id"], "ts": ch["ts"]},
                                  {"text": f"New file: {f['name']}", "file": f["path"], "ts": f["mtime"]}],
                                 {"kind": "new_chat", "label": "Start it",
                                  "payload": {"prompt": ch["texts"][0], "files": [f["path"]], "project_id": pid}},
                                 0.8, c, f"rerun:{pid}:{f['name'].lower()}"))
                break
    return out


def detect_teach(c: Corpus) -> list[dict]:
    out = []
    for t in c.teach:
        if t.get("count", 0) < 2 or not t.get("rules"):
            continue
        out.append(_card("teach", t.get("label") or "Make these your defaults?",
                         "; ".join(t["rules"][:3]).capitalize() + ".",
                         [{"text": f"{t.get('count')} of {t.get('total', t.get('count'))} changed the same way",
                           "file": t.get("file"), "chat_id": t.get("chat_id")}],
                         {"kind": "teach", "label": "Make these defaults",
                          "payload": {"rules": t.get("rules_raw") or t["rules"], "file": t.get("file"), "chat_id": t.get("chat_id"),
                                      "type": t.get("kind")}},
                         min(1.0, 0.6 + 0.4 * t.get("count", 0) / max(1, t.get("total", t.get("count", 1)))), c,
                         "teach:" + _key(t.get("file"), sorted(t["rules"]))))
    return out


_COLLAB = re.compile(r"(?:\b(?i:email|ask|message|tell|meeting with|call with|cc|send (?:it )?to)\s+)"
                     r"([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})?)\b")           # people, not places: "for Dubai" is not a person
_NOT_NAMES = {"The", "This", "That", "Please", "Make", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
              "Saturday", "Sunday", "January", "February", "March", "April", "June", "July", "August", "September",
              "October", "November", "December", "Excel", "Word", "Python", "Google", "Notion", "Slack", "Jira",
              "Meshpool", "Segoe", "Arial"}
_TOOLS = ["excel", "powerpoint", "jira", "notion", "slack", "tableau", "figma", "github", "salesforce", "hubspot",
          "power bi", "sql", "airtable", "asana", "linear"]


def detect_remember(c: Corpus) -> list[dict]:
    out = []
    names: dict = {}
    for ch in c.chats:
        for t in ch["texts"]:
            for m in _COLLAB.findall(t):
                if m.split()[0] not in _NOT_NAMES:
                    names.setdefault(m, {})[ch["id"]] = ch
    for n, mem in names.items():
        if len(mem) >= 4:
            ev = [{"text": f"Named in “{_clip(ch['title'] or ch['texts'][0], 40)}”", "chat_id": ch["id"], "ts": ch["ts"]}
                  for ch in list(mem.values())[-4:]]
            out.append(_card("remember", f"Remember {n}?",
                             f"Came up in {len(mem)} chats. You confirm before it is saved.",
                             ev, {"kind": "remember", "label": "Add to what Meshpool thinks",
                                  "payload": {"text": f"Works regularly with {n}", "category": "people", "evidence": ev}},
                             min(0.9, 0.4 + 0.1 * len(mem)), c, f"remember:person:{n.lower()}"))
    for tool in _TOOLS:
        use = r"\b(?:in|on|from|to|using|use|update|open|export|via|into|my|our|the)\s+(?:\w+\s+)?" + re.escape(tool) + r"(?![a-z])"
        hit = [ch for ch in c.chats if any(re.search(use, t.lower()) for t in ch["texts"])]
        if len(hit) >= 5:
            ev = [{"text": f"Used {tool} in “{_clip(ch['title'] or ch['texts'][0], 40)}”", "chat_id": ch["id"], "ts": ch["ts"]}
                  for ch in hit[-4:]]
            out.append(_card("remember", f"Remember that you use {tool.title()}?",
                             f"You brought up {tool.title()} in {len(hit)} chats.", ev,
                             {"kind": "remember", "label": "Add to what Meshpool thinks",
                              "payload": {"text": f"Uses {tool.title()} for work", "category": "tools", "evidence": ev}},
                             min(0.8, 0.3 + 0.08 * len(hit)), c, f"remember:tool:{tool}"))
    return out


FOLLOWUPS = {
    "deck": [("notes", "Add speaker notes", "Add speaker notes to every slide of the deck you just made."),
             ("memo", "One-page memo", "Turn the deck you just made into a one-page memo.")],
    "workbook": [("sens", "Sensitivity table", "Add a sensitivity table to the workbook you just made."),
                 ("chart", "Chart the trend", "Chart the main trend in the workbook you just made.")],
    "document": [("onepager", "One-slide summary", "Summarize the document you just made in one slide."),
                 ("cover", "Draft a cover note", "Draft a short cover email to go with the document you just made.")],
    "research": [("verify", "Check each figure", "Check each figure in your answer against its source and tell me which ones hold.")],
    "code": [("tests", "Write tests", "Write tests for the change you just made."),
             ("pr", "Open a pull request", "Open a pull request for the change you just made.")],
}
_EXT_KIND = {"pptx": "deck", "ppt": "deck", "xlsx": "workbook", "xls": "workbook", "csv": "workbook",
             "docx": "document", "md": "document", "pdf": "document",
             "py": "code", "js": "code", "ts": "code", "html": "code"}


def output_kind(chat: dict) -> str:
    """What a finished turn produced: deck, workbook, document, research, code or ""."""
    exts = [o.rsplit(".", 1)[-1].lower() for o in chat.get("outputs", []) if "." in o]
    if chat.get("kind") == "code":                            # a README or a spec PDF in a coding chat is still code
        exts = [e for e in exts if e not in ("md", "pdf")]
    for e in reversed(exts):                                  # the latest thing made decides
        if _EXT_KIND.get(e) in ("deck", "workbook", "document"):
            return _EXT_KIND[e]
    if any(t in ("web_research", "research", "deep_research") for t in chat.get("tools", [])) or \
            "research" in str(chat.get("spec", "")):
        return "research"
    if any(_EXT_KIND.get(e) == "code" for e in exts) and chat.get("kind") == "code":
        return "code"
    return ""


# A chip is offered only when the reply or the request is about what it does (priya018: a sensitivity table, a trend
# chart and a one-slide summary after a plain list of room sizes). Each pattern is matched against the last request
# and the last reply; "block" patterns rule a chip out. A chip with no entry here is always offered.
_RELATES = {
    "sens": (r"\b(forecast|scenario|assumption|sensitiv|growth|margin|revenue|valuation|dcf|budget|price|pricing|cost|irr|npv|"
             r"projection|profit|cash ?flow|return)\b", None),
    "chart": (r"\b(trend|over time|monthly|quarterly|yearly|annual|by month|by year|by quarter|series|history|forecast|timeline|"
              r"growth|weekly|daily)\b", None),
    "onepager": (r"\b(report|proposal|paper|white ?paper|strategy|analysis|study|chapters?|sections?|findings)\b",
                 r"\b(one[- ]page|one page|short|brief|update|note|letter)\b"),
    "cover": (r"\b(send|email|e-mail|share|client|customer|letter|proposal|application|submit|forward)\b", None),
}


def _chip_relates(chip_id: str, chat: dict | None) -> bool:
    rule = _RELATES.get(chip_id)
    if rule is None or chat is None:
        return True
    text = ((chat.get("texts") or [""])[-1] + " " + (chat.get("reply") or "")).lower()
    want, block = rule
    if block and re.search(block, text):
        return False
    return bool(re.search(want, text))


def followup_chips(kind: str, chat: dict | None = None) -> list[dict]:
    """The fixed next-step table for an output type: [{id, label, text}]. With the chat, only the chips that relate to
    its last request and reply (none when none do)."""
    return [{"id": i, "label": lab, "text": txt} for i, lab, txt in FOLLOWUPS.get(kind, []) if _chip_relates(i, chat)]


def detect_followup(c: Corpus, chat_id: str | None = None) -> list[dict]:
    out = []
    for ch in c.chats:
        if chat_id and ch["id"] != chat_id:
            continue
        kind = output_kind(ch)
        if not kind:
            continue
        where = "task" if ch.get("scheduled") else "turn"
        n = len(ch["texts"])
        for chip in followup_chips(kind, ch):
            out.append(_card("followup", chip["label"], chip["text"],
                             [{"text": f"This reply made a {kind}", "chat_id": ch["id"], "ts": ch["ts"]}],
                             {"kind": "send", "label": chip["label"], "payload": {"text": chip["text"], "chip": chip["id"]}},
                             0.9, c, f"followup:{ch['id']}:{n}:{chip['id']}", where=where, chat_id=ch["id"], turn_n=n,
                             expires=c.now + EXPIRY_S))
    return out


DETECTORS = {"schedule": detect_schedule, "skill": detect_skill, "project": detect_project, "route": detect_route,
             "connect": detect_connect, "rerun": detect_rerun, "teach": detect_teach, "remember": detect_remember}


# ---------- log, acceptance, throttle ----------------------------------------------------------------------------------------
def _log_rows() -> list[dict]:
    rows = []
    try:
        for line in (folder() / "log.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return rows


def _log(card_id: str, typ: str, event: str, now: float | None = None) -> None:
    with _LOCK:
        with (folder() / "log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(now or time.time(), 1), "card_id": card_id, "type": typ, "event": event}) + "\n")


def acceptance() -> dict:
    """Per type: shown, accepted, dismissed, muted, rate (accepted / shown), throttled."""
    out = {t: {"shown": 0, "accepted": 0, "dismissed": 0, "muted": 0, "expired": 0, "rate": None, "throttled": False}
           for t in TYPES}
    for r in _log_rows():
        b = out.get(r.get("type"))
        if b is not None and r.get("event") in b:
            b[r["event"]] += 1
    th = _state()["types"]
    for t, b in out.items():
        if b["shown"]:
            b["rate"] = round(b["accepted"] / b["shown"], 3)
        b["throttled"] = bool(th.get(t, {}).get("throttled"))
    return out


def _rate_factor(t: str, acc: dict) -> float:
    b = acc[t]
    return (b["accepted"] + 1) / (b["shown"] + 2)            # 0.5 until there is evidence


def _check_throttle(typ: str) -> None:
    """Under 15% acceptance after 10 showings: stop the type and leave one note for Settings. Followups are chips
    the user rarely needs to "accept", so they are never throttled this way."""
    if typ == "followup":
        return
    b = acceptance()[typ]
    if b["shown"] >= THROTTLE_MIN_SHOWN and (b["accepted"] / b["shown"]) < THROTTLE_RATE:
        s = _state()
        t = s["types"].setdefault(typ, {})
        if not t.get("throttled"):
            t["throttled"] = True
            s["notices"].append({"type": typ, "ts": time.time(),
                                 "text": f"Meshpool stopped “{LABELS[typ]}” suggestions: you accepted {b['accepted']} "
                                         f"of {b['shown']}. Turn them back on in Settings > Features."})
            _save_state(s)


# ---------- cards: scan, store, select ----------------------------------------------------------------------------------------
def _cards() -> list[dict]:
    return _jread(folder() / "cards.json", {}).get("cards", [])


def _save_cards(cards: list[dict]) -> None:
    _jwrite(folder() / "cards.json", {"cards": cards})


def _acted_keys() -> set:
    """Dedupe keys of cards the user dismissed, accepted or muted: they never come back."""
    ids = {r["card_id"] for r in _log_rows() if r.get("event") in ("dismissed", "accepted", "muted")}
    return ids


def scan(c: Corpus | None = None, force: bool = False, chat_id: str | None = None) -> list[dict]:
    """Run the detectors and merge the result into cards.json. Zero model calls. Cheap enough for app open."""
    if not enabled():
        return []
    with _LOCK:
        now = (c.now if c else time.time())
        if c is None:
            hit = _SCAN.get("t")
            if not force and hit and now - hit < RESCAN_S and _SCAN.get("home") == str(C.home()):
                return _cards()
            c = corpus(now)
        old = {x["id"]: x for x in _cards()}
        acted = _acted_keys()
        fresh: dict = {}
        for typ, fn in DETECTORS.items():
            if not type_on(typ):
                continue
            try:
                for card in fn(c):
                    if card["id"] not in acted:
                        fresh[card["id"]] = card
            except Exception:  # noqa: BLE001 - one detector failing never takes the others down
                continue
        if type_on("followup"):
            try:
                for card in detect_followup(c, chat_id):
                    if card["id"] not in acted:
                        fresh[card["id"]] = card
            except Exception:  # noqa: BLE001
                pass
        merged = []
        for cid, card in fresh.items():
            prev = old.get(cid)
            if prev:                                          # keep when it was created and shown
                card = dict(card, created=prev["created"], expires=prev["expires"], shown_at=prev.get("shown_at"))
            merged.append(card)
        for cid, prev in old.items():                         # shown cards stay until acted on or expired
            if cid not in fresh and prev.get("shown_at") and prev["type"] != "followup" and cid not in acted:
                merged.append(prev)
        _save_cards(merged)
        _SCAN.update(t=now, home=str(C.home()))
        return merged


def _expire(cards: list[dict], now: float, chats: dict | None = None) -> list[dict]:
    live = []
    for x in cards:
        gone = x["expires"] <= now
        if x["type"] == "followup" and not gone:
            ch = (chats or {}).get(x.get("chat_id"))
            if ch is None and x.get("chat_id"):
                ch = chat_record(x["chat_id"])
            gone = bool(ch) and len(ch["texts"]) > x.get("turn_n", 0)       # a next message ends the chips
        if gone:
            _log(x["id"], x["type"], "expired", now)
        else:
            live.append(x)
    return live


def _today(now: float) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def _is_incognito(chat_id: str | None) -> bool:
    if not chat_id:
        return False
    return bool(_jread(_runs() / chat_id / "meta.json", {}).get("incognito"))


def cards_for(where: str = "home", chat_id: str | None = None, now: float | None = None) -> list[dict]:
    """Cards to show at one boundary. where: "home", "chat:<id>" (chat open) or "turn:<id>" / "task:<id>" (turn end).
    Marks each newly returned card as shown, once. Returns [] in quiet hours, when Suggestions is off, or for an
    incognito chat."""
    now = now or time.time()
    if ":" in where:
        where, _, cid = where.partition(":")
        chat_id = chat_id or cid
    if not enabled() or quiet_now(now) or _is_incognito(chat_id):
        return []
    scan(force=False)
    with _LOCK:
        cards = _expire(_cards(), now)
        _save_cards(cards)
        acted = _acted_keys()
        acc = acceptance()
        cards = [x for x in cards if x["id"] not in acted and type_on(x["type"])]
        if where == "home":
            pool = [x for x in cards if x["where"] == "home" and x["type"] != "followup"]
            used = _state().get("home", {}).get(_today(now), 0)      # showings today, acted on or not
            shown_today = [x for x in pool if x.get("shown_at") and _today(x["shown_at"]) == _today(now)]
            fresh = [x for x in pool if not x.get("shown_at")]
            fresh.sort(key=lambda x: -x["score"] * _freshness(x, now) * _rate_factor(x["type"], acc))
            types = {x["type"] for x in shown_today}                 # one card per type: three schedule cards are one idea
            chosen = []
            for x in fresh:
                if x["type"] in types or x["score"] * _freshness(x, now) < MIN_HOME:
                    continue
                chosen.append(x)
                types.add(x["type"])
            pick = (shown_today + chosen[: max(0, HOME_PER_DAY - used)])[:HOME_PER_DAY]
        else:
            chips = [x for x in cards if x["type"] == "followup" and x.get("chat_id") == chat_id]
            others = [x for x in cards if x["type"] != "followup" and x["where"] == "turn"
                      and any(e.get("chat_id") == chat_id for e in x["evidence"])]
            others.sort(key=lambda x: -x["score"] * _freshness(x, now) * _rate_factor(x["type"], acc))
            already = [x for x in others if x.get("shown_at") and _today(x["shown_at"]) == _today(now)][:TURN_CARDS]
            pick = chips + (already or others[:TURN_CARDS])
        stamp = {x["id"] for x in pick if not x.get("shown_at")}
        if stamp:
            allc = _cards()
            for x in allc:
                if x["id"] in stamp:
                    x["shown_at"] = now
            _save_cards(allc)
            for x in pick:
                if x["id"] in stamp:
                    x["shown_at"] = now
                    _log(x["id"], x["type"], "shown", now)
            if where == "home":
                st = _state()
                st["home"] = {_today(now): st.get("home", {}).get(_today(now), 0) + len(stamp)}
                _save_state(st)
            for typ in {x["type"] for x in pick}:
                _check_throttle(typ)
        return [dict(x) for x in pick if type_on(x["type"])]


def _freshness(card: dict, now: float) -> float:
    return max(0.2, 1 - (now - card["created"]) / EXPIRY_S)


def scan_after_turn(chat_id: str, now: float | None = None) -> list[dict]:
    """Called in the background at the end of a turn. Cheap: reads this chat, plus the usual scan when it is due.
    Returns the cards to emit (the chips and at most one other); nothing for an incognito chat."""
    if not enabled() or _is_incognito(chat_id):
        return []
    c = corpus(now)
    chat = next((x for x in c.chats if x["id"] == chat_id), None)
    if chat is None:
        return []
    scan(c, chat_id=chat_id)
    cards = _cards()
    for x in cards:                                           # a card about this chat belongs at its turn end
        if x["type"] not in ("followup",) and x["where"] == "home" and any(e.get("chat_id") == chat_id for e in x["evidence"]) \
                and x["type"] in ("schedule", "skill", "project", "connect"):
            x["where"] = "turn"
    _save_cards(cards)
    return cards_for(f"turn:{chat_id}", chat_id=chat_id, now=c.now)


# ---------- acting, muting, settings ------------------------------------------------------------------------------------------
def act(card_id: str, event: str, now: float | None = None) -> dict:
    """The user's response to a card: accepted, dismissed or muted (shown and expired come from the app). Returns the
    card's action on accept, so the page can carry it out."""
    now = now or time.time()
    if event not in ("accepted", "dismissed", "muted", "shown", "expired"):
        raise ValueError("Unknown event.")
    with _LOCK:
        card = next((x for x in _cards() if x["id"] == card_id), None)
        if card is None:
            return {"ok": False, "error": "That card is gone."}
        _log(card_id, card["type"], event, now)
        res = {"ok": True, "card": card}
        if event == "muted":
            s = _state()
            ent = {"id": "mute_" + _key(card_id, now), "ts": now, "type": card["type"], "card_id": card_id,
                   "title": card["title"], "active": True}
            s["mutes"].append(ent)
            s["types"].setdefault(card["type"], {})["on"] = False
            _save_state(s)
            res["mute"] = ent
        if event == "accepted":
            res["action"] = card["action"]
            if card["type"] == "remember":
                try:
                    from meshpool_harness import memstore as MS
                    p = card["action"]["payload"]
                    f = MS.notice(p["text"], p.get("evidence"), p.get("category"))
                    res["fact_id"] = f["id"] if f else None
                except Exception:  # noqa: BLE001
                    pass
        if event in ("accepted", "dismissed", "muted"):
            _save_cards([x for x in _cards() if x["id"] != card_id])
        _check_throttle(card["type"])
        return res


def undo_mute(mute_id: str) -> dict:
    s = _state()
    for m in s["mutes"]:
        if m["id"] == mute_id and m["active"]:
            m["active"] = False
            s["types"].setdefault(m["type"], {})["on"] = None
            _save_state(s)
            return {"ok": True, "type": m["type"]}
    return {"ok": False}


def set_type(typ: str, on: bool) -> dict:
    if typ not in TYPES:
        raise ValueError("Unknown suggestion type.")
    s = _state()
    t = s["types"].setdefault(typ, {})
    t["on"] = bool(on)
    if on:
        t["throttled"] = False
        s["notices"] = [n for n in s["notices"] if n["type"] != typ]
    _save_state(s)
    return settings_view()


def reset_learning() -> dict:
    """Forget what Meshpool learned from your responses: counts, mutes and throttles. Types go back to their defaults."""
    with _LOCK:
        (folder() / "log.jsonl").write_text("", encoding="utf-8")
        s = _state()
        s["types"], s["mutes"], s["notices"] = {}, [], []
        _save_state(s)
        _save_cards([])
        _SCAN.clear()
    return settings_view()


def ack_notices() -> None:
    s = _state()
    s["notices"] = []
    _save_state(s)


def settings_view() -> dict:
    acc = acceptance()
    st = _state()
    return {"insights_on": enabled(), "env_off": os.environ.get("MH_INSIGHTS", "1") == "0",
            "learn_from_edits": settings()["learn_from_edits"], "quiet_hours": settings()["quiet_hours"],
            "types": [{"type": t, "label": LABELS[t], "on": type_on(t), "default_on": DEFAULT_ON[t],
                       "memory_needed": t in MEMORY_TYPES, **acc[t]} for t in TYPES],
            "history": [m for m in st["mutes"] if m["active"]][-20:], "notices": st["notices"]}


def take_os_notification(now: float | None = None) -> dict | None:
    """The one OS notification a day from insights: the best waiting card, or None (quiet hours, off, already sent)."""
    now = now or time.time()
    if not enabled() or quiet_now(now):
        return None
    s = _state()
    day = _today(now)
    if s["osn"].get(day, 0) >= OS_PER_DAY:
        return None
    acted = _acted_keys()
    pool = [x for x in _cards() if x["type"] != "followup" and x["id"] not in acted and type_on(x["type"]) and x["expires"] > now]
    if not pool:
        return None
    best = max(pool, key=lambda x: x["score"])
    s["osn"] = {day: s["osn"].get(day, 0) + 1}
    _save_state(s)
    return best
