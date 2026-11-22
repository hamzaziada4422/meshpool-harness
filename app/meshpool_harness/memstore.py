"""Memory v3: facts you can see.

One JSON line per fact in <Meshpool data folder>/memory/facts.jsonl, plus an append-only change timeline
(log.jsonl) and a per-turn record of which facts a chat was given (usage.jsonl). `assistant.py` and `project_store.py`
keep every old function and read and write through this module, so every harness keeps working. `memory.md` (and each
project's `memory.md`) is rewritten as a generated export on every change, so an older app version still reads it.

Fact: {id, text, kind: stated|learned|noticed, category, scope: everywhere|project:<pid>|harness:<id>,
       status: active|pending|archived, source: {type, ts, chat_id?, quote?, evidence?}, pinned, valid_until?,
       created, updated, last_given, given_count, history: [{ts, text, by, reason}]}
Times are Unix seconds. Nothing here calls a model except portrait() and improve_categories(), on the fast model,
cached, and never for detection.
"""
from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from meshpool_harness import config as C

KINDS = ("stated", "learned", "noticed")
CATEGORIES = ("about", "preferences", "people", "projects", "tools", "routines")
CATEGORY_LABELS = {"about": "About you", "preferences": "How you like work", "people": "People",
                   "projects": "Projects", "tools": "Tools and connectors", "routines": "Routines"}
STATUSES = ("active", "pending", "archived")
BUDGET = 12000                     # characters of everywhere-scope memory before consolidation is proposed
NEAR = 0.8                         # share of the budget at which the proposal appears
STALE_DAYS = 90                    # not given for this long: proposed for archiving
REVIEW_DAYS = 60                   # not given for this long: "Still true?"
MAX_TEXT = 400
_LOCK = threading.RLock()
_CACHE: dict = {}


# ---------- paths and files -----------------------------------------------------------------------------------------
def _A():
    from meshpool_harness import assistant as A
    return A


def mdir() -> Path:
    return _A()._dir()


def facts_path() -> Path:
    return mdir() / "facts.jsonl"


def log_path() -> Path:
    return mdir() / "log.jsonl"


def usage_path() -> Path:
    return mdir() / "usage.jsonl"


def backup_dir() -> Path:
    return mdir() / "backup-0.0.9"


_TL = threading.local()


@contextlib.contextmanager
def _lock():
    """The memory file lock, safe to take again inside itself (assistant._memory_lock is not)."""
    if getattr(_TL, "depth", 0) > 0:
        _TL.depth += 1
        try:
            yield
        finally:
            _TL.depth -= 1
        return
    with _A()._memory_lock():
        _TL.depth = 1
        try:
            yield
        finally:
            _TL.depth = 0


def now() -> float:
    return round(time.time(), 3)


def _fid() -> str:
    return "m_" + uuid.uuid4().hex[:10]


def _read_lines(p: Path) -> list[dict]:
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with contextlib.suppress(ValueError):
                    out.append(json.loads(line))
    except OSError:
        pass
    return out


def _append(p: Path, row: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _log(action: str, fid: str, by: str = "you", reason: str = "", before=None, after=None, text: str = "") -> None:
    _append(log_path(), {"ts": now(), "action": action, "id": fid, "by": by, "reason": reason, "text": text,
                         "before": before, "after": after})


# ---------- load and save --------------------------------------------------------------------------------------------
def load() -> list[dict]:
    """Every fact (all statuses). Migrates the 0.0.9 files the first time."""
    ensure()
    p = facts_path()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    hit = _CACHE.get("facts")
    if hit and hit[0] == key:
        return json.loads(hit[1])                      # a copy: callers may edit what they get
    rows = _read_lines(p)
    _CACHE["facts"] = (key, json.dumps(rows))
    return rows


def _save(facts: list[dict]) -> None:
    p = facts_path()
    _A()._write(p, "".join(json.dumps(f, ensure_ascii=False) + "\n" for f in facts))
    _CACHE.pop("facts", None)
    _export(facts)


def _export(facts: list[dict]) -> None:
    """memory.md (everywhere, active, not expired) and each project's memory.md, for older app versions."""
    A = _A()
    live = [f for f in facts if _live(f)]
    ev = [f for f in live if f["scope"] == "everywhere"]
    with contextlib.suppress(OSError):
        A._write(mdir() / "memory.md", "\n".join("- " + f["text"] for f in ev) + ("\n" if ev else ""))
    folders: dict[str, list] = {}
    for f in live:
        if f["scope"].startswith("project:folder-"):
            folders.setdefault(f["scope"][len("project:folder-"):], []).append(f["text"])
    for key, texts in folders.items():                     # per-folder notes, in the 0.0.9 file format
        fp = mdir() / "projects" / f"{key}.md"
        head = ""
        with contextlib.suppress(OSError):
            first = fp.read_text(encoding="utf-8").splitlines()[:1]
            head = first[0] + "\n" if first and first[0].startswith("# ") else ""
        body = head + "\n".join(texts) + "\n"
        with contextlib.suppress(OSError):
            if not fp.exists() or fp.read_text(encoding="utf-8") != body:
                A._write(fp, body)
    by: dict[str, list] = {}
    for f in live:
        if f["scope"].startswith("project:") and not f["scope"].startswith("project:folder-"):
            by.setdefault(f["scope"][8:], []).append(f)
    try:
        from meshpool_harness import project_store as PS
        for pid, fs in by.items():
            d = PS.root() / pid
            if (d / "project.json").is_file():
                A._write(d / "memory.md", "\n".join(f["text"] for f in fs) + "\n")
    except Exception:  # noqa: BLE001 - the export is a courtesy
        pass


def _live(f: dict, at: float | None = None) -> bool:
    if f.get("status") != "active":
        return False
    v = f.get("valid_until")
    return not (v and v < (at or time.time()))


def _expired(f: dict, at: float | None = None) -> bool:
    v = f.get("valid_until")
    return f.get("status") == "active" and bool(v) and v < (at or time.time())


# ---------- migration from 0.0.9 --------------------------------------------------------------------------------------
_ENSURED: set = set()


def ensure() -> None:
    p = facts_path()
    key = str(p)
    if key in _ENSURED and p.exists():
        return
    with _LOCK:
        if not p.exists():
            migrate()
        _ENSURED.add(key)


def migrate() -> dict:
    """0.0.9 files to facts. Backs the old files up first, once. Safe to run again: it does nothing when facts.jsonl
    exists. Every old line becomes a `stated` fact with source `earlier-memory`."""
    A = _A()
    with _lock():
        p = facts_path()
        if p.exists():
            return {"migrated": 0, "already": True}
        bk = backup_dir()
        old_global = mdir() / "memory.md"
        sources: list[tuple[str, Path]] = []                 # (scope, file)
        if old_global.is_file():
            sources.append(("everywhere", old_global))
        folder_notes = mdir() / "projects"
        if folder_notes.is_dir():
            for f in sorted(folder_notes.glob("*.md")):
                sources.append((f"project:folder-{f.stem}", f))
        try:
            from meshpool_harness import project_store as PS
            if PS.root().is_dir():
                for f in sorted(PS.root().glob("*/memory.md")):
                    sources.append((f"project:{f.parent.name}", f))
        except Exception:  # noqa: BLE001
            pass
        if sources and not bk.exists():
            bk.mkdir(parents=True, exist_ok=True)
            for scope, f in sources:
                name = "memory.md" if scope == "everywhere" else (
                    ("folder-notes/" + scope.split(":folder-", 1)[1] if ":folder-" in scope
                     else "project-memory/" + scope.split(":", 1)[1]) + ".md")
                dst = bk / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
        facts: list[dict] = []
        seen: set = set()
        t0 = now()
        for scope, f in sources:
            try:
                raw = f.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for i, line in enumerate(raw):
                text = A.dashless(re.sub(r"\s+", " ", line).strip().lstrip("-•* ").strip())
                if len(text) < 4 or line.startswith("# "):
                    continue
                key = (scope, norm(text))
                if key in seen:
                    continue
                seen.add(key)
                work = bool(A.WORK_ITEM.search(text))
                facts.append(_new(text[:MAX_TEXT], "stated", classify(text), scope, "archived" if work else "active",
                                  {"type": "earlier-memory", "ts": t0}, created=t0 - (len(raw) - i) * 0.001,
                                  by="meshpool", reason="Moved from your earlier memory" +
                                  (" (it was a task, not a fact, so it is archived)" if work else "")))
        _save_new(facts)
        for f in facts:
            _log("migrated", f["id"], "meshpool", "Moved from your earlier memory", after=f, text=f["text"])
        return {"migrated": len(facts), "already": False, "backup": str(bk) if bk.exists() else ""}


def _save_new(facts: list[dict]) -> None:
    p = facts_path()
    A = _A()
    A._write(p, "".join(json.dumps(f, ensure_ascii=False) + "\n" for f in facts))
    _CACHE.pop("facts", None)
    _export(facts)


# ---------- classification (cheap rules) --------------------------------------------------------------------------------
_R_ROUTINE = re.compile(r"(?i)\b(every (?:day|week|month|monday|tuesday|wednesday|thursday|friday|morning|evening|"
                        r"quarter)|each (?:day|week|month|morning|quarter)|daily|weekly|monthly|quarterly|weekdays|"
                        r"on mondays|on fridays|routine|usually (?:starts?|does|runs?))\b")
_R_PREF = re.compile(r"(?i)\b(prefers?|wants?|never|always|likes?|dislikes?|hates?|loves?|default to|font|format|tone|"
                     r"style|concise|brief|detailed|bullets?|sentence case|no em dash|one message per|keep it)\b")
_R_PEOPLE = re.compile(r"(?i)\b(manager|boss|colleague|coworker|teammate|client|customer|partner|wife|husband|"
                       r"spouse|sister|brother|daughter|son|friend|cto|ceo|cfo|director|reports? to|works? with|"
                       r"assistant|intern)\b")
_R_TOOLS = re.compile(r"(?i)\b(uses?|using|tool|jira|notion|slack|linear|github|gitlab|excel|powerpoint|word|"
                      r"google (?:sheets|docs|drive|calendar)|gmail|outlook|salesforce|hubspot|figma|python|sql|"
                      r"tableau|power bi|connector|connected|vs ?code|stack)\b")
_R_PROJECT = re.compile(r"(?i)\b(project|working on|building|launch|deck for|report for|board|q[1-4]\b|campaign|"
                        r"migration|roadmap|product|startup|app called|clients? in)\b")


def classify(text: str) -> str:
    t = text or ""
    if _R_ROUTINE.search(t):
        return "routines"
    if re.match(r"(?i)^(prefers|wants|never wants|always wants|likes|dislikes|never|always)\b", t.strip()) \
            or _R_PREF.search(t):
        return "preferences"
    if _R_PEOPLE.search(t):
        return "people"
    if re.search(r"(?i)\b(my name|name is|i am|i'm|lives? in|based in|works? (?:at|as|in)|role|title|company|"
                 r"years|born|speaks?|language|timezone|time zone)\b", t):
        return "about"
    if _R_TOOLS.search(t):
        return "tools"
    if _R_PROJECT.search(t):
        return "projects"
    return "about"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lstrip("-•* ").strip().rstrip(".").strip().lower()


def _new(text, kind, category, scope, status, source, created=None, pinned=False, valid_until=None, by="you",
         reason="") -> dict:
    t = created or now()
    src = dict(source or {})
    src.setdefault("ts", t)
    return {"id": _fid(), "text": text, "kind": kind, "category": category, "scope": scope, "status": status,
            "source": src, "pinned": bool(pinned), "valid_until": valid_until, "created": t, "updated": t,
            "last_given": None, "given_count": 0,
            "history": [{"ts": t, "text": text, "by": by, "reason": reason or "Added"}]}


# ---------- reading ------------------------------------------------------------------------------------------------------
def get(fid: str) -> dict | None:
    return next((f for f in load() if f["id"] == fid), None)


def facts(status: str | None = None, scope: str | None = None, kind: str | None = None,
          category: str | None = None) -> list[dict]:
    out = []
    for f in load():
        if status and f["status"] != status:
            continue
        if scope and f["scope"] != scope:
            continue
        if kind and f["kind"] != kind:
            continue
        if category and f["category"] != category:
            continue
        out.append(f)
    return out


def scope_ok(f: dict, project_id: str = "", harness: str = "") -> bool:
    s = f["scope"]
    if s == "everywhere":
        return True
    if s.startswith("project:"):
        return bool(project_id) and s == f"project:{project_id}"
    if s.startswith("harness:"):
        return bool(harness) and s == f"harness:{harness}"
    return False


def lines(scope: str = "everywhere") -> list[str]:
    return [f["text"] for f in load() if f["scope"] == scope and _live(f)]


def text(scope: str = "everywhere") -> str:
    ls = lines(scope)
    return "\n".join(("- " + x) if scope == "everywhere" else x for x in ls)


# ---------- writing -----------------------------------------------------------------------------------------------------
def _clean(fact: str) -> str:
    fact = re.sub(r"\s+", " ", (fact or "")).strip().lstrip("-• ").strip()
    return _A().dashless(fact)


def add(fact: str, kind: str = "stated", category: str | None = None, scope: str = "everywhere",
        source: dict | None = None, status: str | None = None, pinned: bool = False, valid_until: float | None = None,
        by: str | None = None, check: bool = True) -> tuple[dict, bool]:
    """Save one fact. Returns (fact, created). A fact already there (same words) is returned, not saved twice.
    Noticed facts wait as `pending`; a stated fact is never changed by a learned or noticed one."""
    A = _A()
    text = _clean(fact)
    if check:
        if len(text) < 4:
            raise ValueError("Say what to remember in a short sentence.")
        if len(text) > MAX_TEXT:
            raise ValueError("Keep it under 400 characters: one fact per call.")
        if A.BAD.search(text):
            raise ValueError("Memory does not store secrets or instructions about the system.")
        if A.WORK_ITEM.search(text):
            raise ValueError("Memory keeps lasting facts about the user (role, company, preferences), not tasks, "
                             "to-dos, bugs, feature requests or release plans. Keep those in the chat or in a file "
                             "in the project.")
    if kind not in KINDS:
        kind = "stated"
    if status is None:
        status = "pending" if kind == "noticed" else "active"
    with _lock():
        rows = load()
        key = norm(text)
        for f in rows:
            if f["scope"] == scope and norm(f["text"]) == key and f["status"] != "archived":
                return f, False
        # an archived twin the user removed earlier is brought back only by a stated fact
        for f in rows:
            if f["scope"] == scope and norm(f["text"]) == key and f["status"] == "archived":
                if kind == "stated" and f["kind"] != "noticed" or f["kind"] == "stated":
                    f["status"] = "active" if kind != "noticed" else "pending"
                    f["updated"] = now()
                    f["history"].append({"ts": now(), "text": f["text"], "by": by or "you", "reason": "Saved again"})
                    _save(rows)
                    _log("restored", f["id"], by or "you", "Saved again", text=f["text"])
                    return f, True
                return f, False
        fact_ = _new(text, kind, category if category in CATEGORIES else classify(text), scope, status, source,
                     pinned=pinned, valid_until=valid_until, by=by or ("you" if kind == "stated" else "meshpool"))
        rows.append(fact_)
        _save(rows)
        _log("added", fact_["id"], fact_["history"][0]["by"], "Added", after=fact_, text=text)
    return fact_, True


def update(fid: str, by: str = "you", reason: str = "", **fields) -> dict:
    """Change one fact: text, category, scope, pinned, valid_until, status, kind."""
    allowed = {"text", "category", "scope", "pinned", "valid_until", "status"}
    with _lock():
        rows = load()
        f = next((x for x in rows if x["id"] == fid), None)
        if f is None:
            raise KeyError("That memory does not exist.")
        before = json.loads(json.dumps(f))
        what = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "text":
                v = _clean(v)
                if len(v) < 4 or len(v) > MAX_TEXT:
                    raise ValueError("A memory is a short sentence, under 400 characters.")
                if _A().BAD.search(v):
                    raise ValueError("Memory does not store secrets or instructions about the system.")
            if k == "category" and v not in CATEGORIES:
                continue
            if k == "status" and v not in STATUSES:
                continue
            if k == "scope" and not re.fullmatch(r"everywhere|project:[\w-]+|harness:[\w.-]+", str(v)):
                continue
            if f.get(k) != v:
                f[k] = v
                what.append(k)
        if not what:
            return f
        if "text" in what and f["kind"] != "stated" and by == "you":
            f["kind"] = "stated"                        # the user wrote it themselves now
            what.append("kind")
        f["updated"] = now()
        f["history"].append({"ts": f["updated"], "text": f["text"], "by": by,
                             "reason": reason or ("Edited: " + ", ".join(what))})
        _save(rows)
        action = ("archived" if f["status"] == "archived" and before["status"] != "archived" else
                  "restored" if before["status"] == "archived" and f["status"] == "active" else
                  "edited" if "text" in what else "changed")
        _log(action, fid, by, reason or ", ".join(what), before=before, after=f, text=f["text"])
        return f


def archive(fid: str, by: str = "you", reason: str = "") -> dict:
    return update(fid, by=by, reason=reason or "Archived", status="archived")


def unarchive(fid: str, by: str = "you") -> dict:
    return update(fid, by=by, reason="Restored", status="active")


def confirm(fid: str, by: str = "you") -> dict:
    """Yes on a noticed fact: it becomes active and joins every agent's context."""
    return update(fid, by=by, reason="You confirmed it", status="active")


def reject(fid: str) -> dict:
    """No on a noticed fact: archived, and the detector will not suggest it again."""
    f = update(fid, reason="You said no", status="archived")
    _remember_rejection(f["text"])
    return f


def delete(fid: str, by: str = "you") -> None:
    """Remove a fact for good. The timeline keeps a copy so Undo can bring it back."""
    with _lock():
        rows = load()
        f = next((x for x in rows if x["id"] == fid), None)
        if f is None:
            return
        _save([x for x in rows if x["id"] != fid])
        _log("deleted", fid, by, "Deleted", before=f, text=f["text"])


def undelete(fid: str) -> dict:
    with _lock():
        for r in reversed(_read_lines(log_path())):
            if r.get("id") == fid and r.get("action") == "deleted" and r.get("before"):
                f = r["before"]
                rows = load()
                if not any(x["id"] == fid for x in rows):
                    f["history"].append({"ts": now(), "text": f["text"], "by": "you", "reason": "Restored after delete"})
                    rows.append(f)
                    _save(rows)
                    _log("restored", fid, "you", "Restored after delete", after=f, text=f["text"])
                return f
    raise KeyError("Nothing to restore.")


def restore_version(fid: str, index: int) -> dict:
    """Put a fact back to the text it had at history[index]."""
    f = get(fid)
    if f is None:
        raise KeyError("That memory does not exist.")
    try:
        old = f["history"][int(index)]["text"]
    except (IndexError, ValueError, TypeError):
        raise ValueError("That version does not exist.") from None
    return update(fid, reason=f"Restored an earlier version", text=old, status="active")


def set_scope(fid: str, scope: str) -> dict:
    return update(fid, reason="Moved to " + scope_label(scope), scope=scope)


def pin(fid: str, on: bool = True) -> dict:
    return update(fid, reason="Pinned" if on else "Unpinned", pinned=bool(on))


def set_expiry(fid: str, until: float | None) -> dict:
    return update(fid, reason="Expiry set" if until else "Expiry removed", valid_until=until or None)


def scope_label(scope: str) -> str:
    if scope == "everywhere":
        return "Everywhere"
    if scope.startswith("project:"):
        name = scope[8:]
        try:
            from meshpool_harness import project_store as PS
            return PS.get_meta(name).get("name") or "A project"
        except Exception:  # noqa: BLE001
            return "A project"
    if scope.startswith("harness:"):
        return scope[8:]
    return scope


def replace_scope_text(scope: str, text: str, kind: str = "stated") -> None:
    """The old "edit the whole memory as text" paths (set_memory, memory_set): lines added become facts, lines
    removed are archived, the rest are untouched."""
    A = _A()
    want = []
    for l in (text or "").splitlines():
        t = _clean(l)
        if len(t) >= 1 and t not in want and not A.BAD.search(t):
            want.append(t[:MAX_TEXT])
    with _lock():
        keep = {norm(t) for t in want}
        for f in list(load()):
            if f["scope"] == scope and f["status"] == "active" and norm(f["text"]) not in keep:
                archive(f["id"], reason="Removed when the memory text was edited")
        have = {norm(f["text"]) for f in load() if f["scope"] == scope and f["status"] != "archived"}
        for t in want:
            if norm(t) not in have:
                with contextlib.suppress(ValueError):
                    add(t, kind=kind, scope=scope, source={"type": "manual"}, check=False)


# ---------- noticed-fact rejections (teach the detector) ---------------------------------------------------------------
def _rej_path() -> Path:
    return mdir() / "rejected.json"


def _remember_rejection(text: str) -> None:
    try:
        d = json.loads(_rej_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = []
    d = (d + [norm(text)])[-300:]
    _A()._write(_rej_path(), json.dumps(d))


def was_rejected(text: str) -> bool:
    try:
        d = json.loads(_rej_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    k = norm(text)
    return any(k == x or difflib.SequenceMatcher(None, k, x).ratio() > 0.85 for x in d)


def notice(text: str, evidence: list | None = None, category: str | None = None, scope: str = "everywhere") -> dict | None:
    """The `remember` insight: a pattern worth a fact. Lands pending, never active, never twice, never after a No."""
    if not _A().settings()["memory_on"] or was_rejected(text):
        return None
    f, made = add(text, kind="noticed", category=category, scope=scope,
                  source={"type": "pattern", "evidence": list(evidence or [])[:6]}, by="meshpool")
    return f if made else None


# ---------- conflicts ------------------------------------------------------------------------------------------------------
_FONTS = ["segoe ui", "arial", "calibri", "helvetica", "times new roman", "georgia", "verdana", "tahoma", "cambria",
          "garamond", "roboto", "open sans", "aptos", "consolas", "courier new", "inter", "lato", "poppins"]
_OPPOSITE = [({"concise", "short", "brief", "terse", "shorter"}, {"detailed", "thorough", "long", "verbose",
                                                                   "comprehensive", "longer"}),
             ({"formal"}, {"casual", "informal", "relaxed"}),
             ({"bullets", "bullet"}, {"prose", "paragraphs", "paragraph"}),
             ({"metric", "celsius"}, {"imperial", "fahrenheit"}),
             ({"dark"}, {"light"})]
_POLARITY_NEG = re.compile(r"(?i)\b(never|don'?t|do not|doesn'?t|avoid|no)\b")
_POLARITY_POS = re.compile(r"(?i)\b(always|prefers?|wants?|likes?|use|must)\b")
_STOP = {"prefers", "wants", "never", "always", "the", "and", "for", "with", "that", "this", "user", "use", "not",
         "dont", "does", "like", "likes", "avoid", "must", "has", "have", "doesnt", "only"}


def _words(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2} - _STOP


def _fonts(t: str) -> set:
    low = " " + re.sub(r"[^a-z ]", " ", (t or "").lower()) + " "
    return {f for f in _FONTS if f" {f} " in low}


def disagree(a: str, b: str) -> str:
    """Why two facts disagree ("" when they do not). Rules only, no model."""
    fa, fb = _fonts(a), _fonts(b)
    if fa and fb and not (fa & fb):
        return "They name different fonts."
    wa, wb = _words(a), _words(b)
    for x, y in _OPPOSITE:
        if (wa & x and wb & y) or (wa & y and wb & x):
            return "They ask for opposite things."
    na, nb = bool(_POLARITY_NEG.search(a)), bool(_POLARITY_NEG.search(b))
    if na != nb:
        shared = wa & wb
        if len(shared) >= 2 and len(shared) >= 0.6 * min(len(wa), len(wb)):
            return "One says never and the other says always."
    return ""


def _dismissed_path() -> Path:
    return mdir() / "conflicts-dismissed.json"


def _dismissed() -> set:
    try:
        return set(json.loads(_dismissed_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def conflicts() -> list[dict]:
    """Pairs of live facts that disagree. Cached until the facts change; candidates come from an index of shared
    words, fonts and opposite words, so 1,000 facts are not compared pair by pair."""
    p = facts_path()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size, tuple(sorted(_dismissed())))
    except OSError:
        return []
    hit = _CACHE.get("conflicts")
    if hit and hit[0] == key:
        return json.loads(hit[1])
    live = [f for f in load() if _live(f)]
    dism = _dismissed()
    feat = {f["id"]: (_words(f["text"]), _fonts(f["text"])) for f in live}
    byid = {f["id"]: f for f in live}
    index: dict = {}
    for f in live:
        w, fo = feat[f["id"]]
        for t in w:
            index.setdefault(("w", t), []).append(f["id"])
        for t in fo:
            index.setdefault(("f", "any"), []).append(f["id"])
        for i, (x, y) in enumerate(_OPPOSITE):
            if w & x:
                index.setdefault(("o", i, 0), []).append(f["id"])
            if w & y:
                index.setdefault(("o", i, 1), []).append(f["id"])
    cand: set = set()
    for k, ids in index.items():
        if len(ids) > 60 and k[0] == "w":
            continue                                          # a word in most facts says nothing about conflict
        if k[0] == "w":
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    cand.add((a, b) if a < b else (b, a))
        elif k[0] == "f":
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    cand.add((a, b) if a < b else (b, a))
    for i in range(len(_OPPOSITE)):
        for a in index.get(("o", i, 0), []):
            for b in index.get(("o", i, 1), []):
                if a != b:
                    cand.add((a, b) if a < b else (b, a))
    out = []
    for a_id, b_id in sorted(cand):
        a, b = byid[a_id], byid[b_id]
        if a["category"] != b["category"] and "preferences" not in (a["category"], b["category"]):
            continue
        if not (a["scope"] == b["scope"] or "everywhere" in (a["scope"], b["scope"])):
            continue
        pair = "|".join(sorted((a_id, b_id)))
        if pair in dism:
            continue
        why = disagree(a["text"], b["text"])
        if why:
            out.append({"a": a_id, "b": b_id, "reason": why, "winner": winner_of(a, b)["id"], "pair": pair})
    _CACHE["conflicts"] = (key, json.dumps(out))
    return out


def winner_of(a: dict, b: dict) -> dict:
    """Stated beats learned and noticed; otherwise the newer wins, until the user decides."""
    rank = {"stated": 2, "learned": 1, "noticed": 0}
    if rank[a["kind"]] != rank[b["kind"]]:
        return a if rank[a["kind"]] > rank[b["kind"]] else b
    return a if a["updated"] >= b["updated"] else b


def resolve_conflict(winner: str, loser: str) -> None:
    update(loser, reason="Lost a conflict with a memory you chose", status="archived")
    pin_key = "|".join(sorted((winner, loser)))
    d = _dismissed() | {pin_key}
    _A()._write(_dismissed_path(), json.dumps(sorted(d)))


def keep_both(a: str, b: str) -> None:
    d = _dismissed() | {"|".join(sorted((a, b)))}
    _A()._write(_dismissed_path(), json.dumps(sorted(d)))


def _conflict_losers() -> set:
    try:
        return {(c["b"] if c["winner"] == c["a"] else c["a"]) for c in conflicts()}
    except Exception:  # noqa: BLE001
        return set()


# ---------- what goes into a chat's context -------------------------------------------------------------------------------
def select(task: str = "", project_id: str = "", harness: str = "", exclude=(), limit: int | None = None,
           scope: str = "everywhere") -> list[dict]:
    """Facts for the first message of a chat. Pending, archived, expired and conflict-losing facts never go in.
    Pinned facts always do; the rest by core, then match to the task, inside `limit` characters."""
    A = _A()
    limit = limit or A.RECALL_CHARS
    ex = set(exclude or ())
    losers = _conflict_losers()
    pool = [f for f in load() if _live(f) and f["id"] not in ex and f["id"] not in losers and scope_ok(f, project_id, harness)
            and (f["scope"] == "everywhere" or f["scope"].startswith("harness:")) == (scope == "everywhere")]
    if scope != "everywhere":
        pool = [f for f in load() if _live(f) and f["id"] not in ex and f["id"] not in losers and f["scope"] == scope]
        return pool
    pinned = [f for f in pool if f["pinned"]]
    rest = [f for f in pool if not f["pinned"]]
    size = lambda fs: sum(len(f["text"]) + 3 for f in fs)       # noqa: E731
    if size(pool) <= limit:
        return pinned + rest
    core = [f for f in rest if A.CORE.search(f["text"])][:8]
    q = A._terms(task)
    scored = sorted(((len(q & A._terms(f["text"])), -i, f) for i, f in enumerate(rest) if f not in core),
                    key=lambda t: (t[0], t[1]), reverse=True)
    picked, used = pinned + core, size(pinned + core)
    for score, _, f in scored:
        if score <= 0 or used + len(f["text"]) + 3 > limit:
            continue
        picked.append(f)
        used += len(f["text"]) + 3
    order = {f["id"]: i for i, f in enumerate(pool)}
    return sorted(picked, key=lambda f: order[f["id"]])


def project_facts(project_id: str, exclude=()) -> list[dict]:
    return select(project_id=project_id, exclude=exclude, scope=f"project:{project_id}")


def project_text(project_id: str, exclude=()) -> str:
    return "\n".join(f["text"] for f in project_facts(project_id, exclude))


# ---------- usage tracking -----------------------------------------------------------------------------------------------
def record_given(chat_id: str, turn: int, fact_ids: list[str], context: str = "") -> None:
    """The facts a chat was given for one turn: last_given, given_count and a per-turn record. Never raises."""
    if not fact_ids:
        return
    try:
        with _lock():
            rows = load()
            ids = set(fact_ids)
            t = now()
            for f in rows:
                if f["id"] in ids:
                    f["last_given"] = t
                    f["given_count"] = int(f.get("given_count") or 0) + 1
            _save(rows)
            _append(usage_path(), {"ts": t, "chat": chat_id, "turn": turn, "ids": list(fact_ids)})
        if context:
            d = mdir() / "ctx"
            d.mkdir(parents=True, exist_ok=True)
            _A()._write(d / (re.sub(r"[^\w-]", "_", chat_id)[:80] + ".txt"), context)
    except Exception:  # noqa: BLE001 - tracking never touches a turn
        pass


def given_for_chat(chat_id: str) -> list[dict]:
    """The facts this chat was given (current text, so an edit shows). Deleted ones are skipped."""
    ids: list[str] = []
    for r in _read_lines(usage_path()):
        if r.get("chat") == chat_id:
            ids += [i for i in r.get("ids", []) if i not in ids]
    by = {f["id"]: f for f in load()}
    return [by[i] for i in ids if i in by]


def context_snapshot(chat_id: str) -> str:
    try:
        return (mdir() / "ctx" / (re.sub(r"[^\w-]", "_", chat_id)[:80] + ".txt")).read_text(encoding="utf-8")
    except OSError:
        return ""


def chats_given(fid: str, days: int = 7) -> int:
    cut = time.time() - days * 86400
    return len({r["chat"] for r in _read_lines(usage_path()) if r.get("ts", 0) >= cut and fid in r.get("ids", [])})


def usage_counts(days: int = 7) -> dict:
    cut = time.time() - days * 86400
    out: dict = {}
    seen = set()
    for r in _read_lines(usage_path()):
        if r.get("ts", 0) < cut:
            continue
        for i in r.get("ids", []):
            if (i, r["chat"]) not in seen:
                seen.add((i, r["chat"]))
                out[i] = out.get(i, 0) + 1
    return out


def attribute_since(ts: float, chat_id: str, quote: str = "") -> int:
    """Facts saved by an agent during a turn that started at `ts` get that chat as their source."""
    n = 0
    try:
        with _lock():
            rows = load()
            for f in rows:
                s = f["source"]
                if f["created"] >= ts and s.get("type") == "chat" and not s.get("chat_id"):
                    s["chat_id"] = chat_id
                    if quote and not s.get("quote"):
                        s["quote"] = quote[:200]
                    n += 1
            if n:
                _save(rows)
    except Exception:  # noqa: BLE001
        pass
    return n


# ---------- review, health, consolidation ---------------------------------------------------------------------------------
def review_queue(days: int = REVIEW_DAYS) -> list[dict]:
    """"Still true?": active facts not given to any chat in `days` days (and older than that), plus expired ones."""
    cut = time.time() - days * 86400
    out = []
    for f in load():
        if f["status"] != "active" or f["kind"] == "noticed" and False:
            continue
        if _expired(f) and (f.get("reviewed") or 0) < time.time() - 86400:
            out.append(dict(f, review_reason="expired"))
        elif f["created"] < cut and (f["last_given"] or 0) < cut and (f.get("reviewed") or 0) < cut and not f["pinned"]:
            out.append(dict(f, review_reason="unused"))
    return out


def health() -> dict:
    ev = [f for f in load() if f["scope"] == "everywhere" and f["status"] == "active"]
    used = sum(len(f["text"]) + 3 for f in ev)
    return {"used": used, "budget": BUDGET, "share": round(used / BUDGET, 3), "facts": len(ev),
            "near": used >= NEAR * BUDGET, "pending": len([f for f in load() if f["status"] == "pending"]),
            "proposal": consolidation() if used >= NEAR * BUDGET else None}


def consolidation() -> dict:
    """What would shrink memory: merge near-duplicates, archive facts not given for 90 days. Only a proposal."""
    rows = [f for f in load() if f["status"] == "active" and f["scope"] == "everywhere" and not f["pinned"]]
    merges, used = [], set()
    for i, a in enumerate(rows):
        if a["id"] in used:
            continue
        group = [a]
        for b in rows[i + 1:]:
            if b["id"] in used:
                continue
            r = difflib.SequenceMatcher(None, norm(a["text"]), norm(b["text"])).ratio()
            if r >= 0.85 and not disagree(a["text"], b["text"]):
                group.append(b)
        if len(group) > 1:
            keep = max(group, key=lambda f: (f["kind"] == "stated", f["updated"]))
            for g in group:
                used.add(g["id"])
            merges.append({"keep": keep["id"], "drop": [g["id"] for g in group if g["id"] != keep["id"]],
                           "text": keep["text"]})
    cut = time.time() - STALE_DAYS * 86400
    stale = [f["id"] for f in rows if f["id"] not in used and f["created"] < cut and (f["last_given"] or 0) < cut]
    return {"merges": merges, "archive": stale,
            "saves": sum(len(load_by(i)["text"]) for m in merges for i in m["drop"]) +
            sum(len(load_by(i)["text"]) for i in stale)}


def load_by(fid: str) -> dict:
    return get(fid) or {"text": ""}


def apply_consolidation(merges: list | None = None, archive_ids: list | None = None) -> dict:
    n = 0
    for m in merges or []:
        for d in m.get("drop", []):
            archive(d, by="meshpool", reason="Merged with a near-duplicate you approved")
            n += 1
    for i in archive_ids or []:
        archive(i, by="meshpool", reason="Not given to any chat for 90 days; you approved archiving")
        n += 1
    return {"archived": n}


def history(limit: int = 200) -> list[dict]:
    rows = _read_lines(log_path())[-limit:]
    out = []
    for r in reversed(rows):
        row = {k: r.get(k) for k in ("ts", "action", "id", "by", "reason", "text")}
        row["before_text"] = (r.get("before") or {}).get("text") if isinstance(r.get("before"), dict) else None
        out.append(row)
    return out


def still_true(fid: str) -> dict:
    """Yes on a "Still true?" card: kept, not asked again for a while; an expired fact stops expiring."""
    with _lock():
        rows = load()
        f = next((x for x in rows if x["id"] == fid), None)
        if f is None:
            raise KeyError("That memory does not exist.")
        f["reviewed"] = now()
        if _expired(f):
            f["valid_until"] = None
        f["history"].append({"ts": now(), "text": f["text"], "by": "you", "reason": "You said it is still true"})
        _save(rows)
        _log("reviewed", fid, "you", "Still true", text=f["text"])
        return f


# ---------- export -------------------------------------------------------------------------------------------------------------
def export_markdown() -> str:
    out = ["# What Meshpool knows about you", ""]
    active = [f for f in load() if f["status"] == "active"]
    for cat in CATEGORIES:
        fs = [f for f in active if f["category"] == cat]
        if not fs:
            continue
        out += [f"## {CATEGORY_LABELS[cat]}", ""]
        for f in fs:
            tag = "" if f["scope"] == "everywhere" else f" ({scope_label(f['scope'])})"
            out.append(f"- {f['text']}{tag}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def export_onepager() -> str:
    """How I work: a page to hand to a colleague or another AI. Only everywhere-scope, active, stated or confirmed."""
    mine = [f for f in load() if f["status"] == "active" and f["scope"] == "everywhere"]
    out = ["# How I work", "", "Notes to read before working with me.", ""]
    for cat in ("about", "preferences", "people", "tools", "routines"):
        fs = [f for f in mine if f["category"] == cat]
        if fs:
            out += [f"## {CATEGORY_LABELS[cat]}", ""] + [f"- {f['text']}" for f in fs] + [""]
    return "\n".join(out).rstrip() + "\n"


# ---------- portrait and category polish (fast model, cached) ----------------------------------------------------------
def _fast_client():
    if os.environ.get("MH_LEARN", "1") == "0" or os.environ.get("MH_MEMORY_MODEL", "1") == "0":
        return None
    try:
        from meshpool_harness.core.client import LiveClient
        return LiveClient("memory")
    except Exception:  # noqa: BLE001
        return None


def _model() -> str:
    return os.environ.get("MH_MODEL_FAST") or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"


def _portrait_cache() -> Path:
    return mdir() / "portrait.json"


def portrait(client=None, force: bool = False) -> dict:
    """3 to 5 plain sentences from active facts only, each tied to the facts behind it. Regenerated only when the
    active facts change. No facts, no portrait."""
    act = [f for f in load() if _live(f) and f["scope"] == "everywhere"]
    if not act:
        return {"sentences": [], "hash": ""}
    h = hashlib.sha1("\n".join(sorted(f["id"] + f["text"] for f in act)).encode()).hexdigest()[:12]
    try:
        c = json.loads(_portrait_cache().read_text(encoding="utf-8"))
        if c.get("hash") == h and not force:
            return c
    except (OSError, ValueError):
        pass
    sents = _portrait_model(act, client) or _portrait_rules(act)
    out = {"hash": h, "sentences": sents, "ts": now()}
    with contextlib.suppress(OSError):
        _A()._write(_portrait_cache(), json.dumps(out))
    return out


def _portrait_rules(act: list[dict]) -> list[dict]:
    """No model: one plain sentence per category, built from the facts' own words."""
    out = []
    for cat, lead in (("about", ""), ("preferences", "You like work done this way: "), ("people", "People: "),
                      ("projects", "Projects: "), ("tools", "Tools: "), ("routines", "Routines: ")):
        fs = [f for f in act if f["category"] == cat][:3]
        if fs:
            body = "; ".join(f["text"].rstrip(".") for f in fs)
            out.append({"text": (lead + body)[:240].rstrip(".") + ".", "ids": [f["id"] for f in fs]})
    return out[:5]


def _portrait_model(act: list[dict], client) -> list[dict]:
    client = client or _fast_client()
    if client is None:
        return []
    listing = "\n".join(f"{f['id']}: {f['text']}" for f in act[:80])
    try:
        r = client.chat(model=_model(), messages=[
            {"role": "system", "content": "Write a short portrait of a user from the facts given. 3 to 5 plain "
             "sentences in the second person (\"You ...\"). Short sentences, no em dashes, nothing in all caps, no "
             "flattery, nothing not in the facts. Return ONLY JSON {\"sentences\":[{\"text\":str,\"ids\":[fact ids "
             "the sentence rests on]}]}."},
            {"role": "user", "content": listing}], max_tokens=500, temperature=0.2, task_id="portrait",
            role="worker", extra={"response_format": {"type": "json_object"}})
        txt = r["choices"][0]["message"].get("content") or ""
        got = json.loads(txt[txt.find("{"): txt.rfind("}") + 1]).get("sentences") or []
    except Exception:  # noqa: BLE001
        return []
    valid = {f["id"] for f in act}
    out = []
    for s in got[:5]:
        if isinstance(s, dict) and isinstance(s.get("text"), str):
            ids = [i for i in s.get("ids", []) if i in valid]
            out.append({"text": _A().dashless(s["text"].strip())[:300], "ids": ids})
    return out if len(out) >= 1 else []


def improve_categories(client=None) -> int:
    """Background pass after migration: the fast model may move a fact to a better category. Never changes text."""
    client = client or _fast_client()
    if client is None:
        return 0
    rows = [f for f in load() if f["source"].get("type") == "earlier-memory" and f["status"] == "active"][:60]
    if not rows:
        return 0
    try:
        r = client.chat(model=_model(), messages=[
            {"role": "system", "content": "File each fact under one category: about, preferences, people, projects, "
             "tools, routines. Return ONLY JSON {\"categories\": {\"<id>\": \"<category>\"}}."},
            {"role": "user", "content": "\n".join(f"{f['id']}: {f['text']}" for f in rows)}],
            max_tokens=800, temperature=0.0, task_id="memory-file", role="worker",
            extra={"response_format": {"type": "json_object"}})
        txt = r["choices"][0]["message"].get("content") or ""
        got = json.loads(txt[txt.find("{"): txt.rfind("}") + 1]).get("categories") or {}
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for fid, cat in got.items():
        f = get(fid)
        if f and cat in CATEGORIES and f["category"] != cat:
            with contextlib.suppress(Exception):
                update(fid, by="meshpool", reason="Filed under a better category", category=cat)
                n += 1
    return n


def on_project_deleted(pid: str) -> int:
    """A deleted project's facts are archived, not lost."""
    n = 0
    for f in load():
        if f["scope"] == f"project:{pid}" and f["status"] != "archived":
            with contextlib.suppress(Exception):
                update(f["id"], by="meshpool", reason="The project was deleted", status="archived")
                n += 1
    return n


def search(q: str, rows: list[dict] | None = None) -> list[dict]:
    rows = rows if rows is not None else load()
    ql = (q or "").lower().strip()
    if not ql:
        return rows
    toks = ql.split()
    out = []
    for f in rows:
        hay = (f["text"] + " " + json.dumps(f["source"]) + " " + f["category"] + " " + f["kind"]).lower()
        if all(t in hay for t in toks):
            out.append(f)
    return out


# ---------- incognito turns ---------------------------------------------------------------------------------------------
def _inc_dir() -> Path:
    return mdir() / "incognito"


def incognito_begin(chat_id: str) -> None:
    d = _inc_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / re.sub(r"[^\w-]", "_", chat_id)[:80]).write_text(str(now()))


def incognito_end(chat_id: str) -> None:
    with contextlib.suppress(OSError):
        (_inc_dir() / re.sub(r"[^\w-]", "_", chat_id)[:80]).unlink()


def incognito_active() -> bool:
    """An incognito chat is working right now. The memory tools every harness shares refuse to save meanwhile, so
    nothing said in it can reach memory (the tools run in another process and cannot see which chat is calling)."""
    d = _inc_dir()
    try:
        return any(time.time() - f.stat().st_mtime < 7200 for f in d.iterdir())
    except OSError:
        return False
