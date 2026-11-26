"""Visible quality checks: a record of the checks that actually ran while a turn made a file or a report.

The office engines (deck rules, workbook rules, Office schema) and the research verifier call `passed()` or
`failed()` when a check finishes. The turn runner calls `begin(run_id)` at the start of a turn and `end(run_id)` after
it, shows `events_for()` in the progress panel and `final_line()` under the reply.

Honest by construction: only a check that ran is recorded; a rule that could not be evaluated ("not_run") is never
listed. With no recorder active every call does nothing, so the engines behave exactly as before.

The record is a small file under <Meshpool data folder>/checks/, not memory, because the engines often run in another
process (the office tool server) than the app. `begin()` writes a pointer file and sets MH_CHECK_RUN for child
processes; both are read by `passed()`.

PA-toplevel-23: that pointer and MH_CHECK_RUN are one per process, so two chats running at once would mix their
checks. In this process the recorder is therefore also keyed by context: `begin()` binds the run to the calling
thread's context (a ContextVar), `bind()` carries it into a worker thread, and `env_for(chat)` gives an engine process
started for one chat that chat's own run. The process-wide pointer is only the fallback (a tool server started
before the turn, say).
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from meshpool_harness import config as C

STALE_S = 6 * 3600
_LABELS = {                                   # kind -> (singular, plural) wording for the one-line summary
    "deck_rules": ("design rule", "design rules"),
    "workbook_rules": ("workbook rule", "workbook rules"),
    "sources": ("source", "sources"),
}
# Plain words for the person reading the chat (priya018). A kind with a "the <thing>" noun counts its checks:
# "Checked the workbook: 16 of 20 checks passed". The others are one fixed sentence.
_NOUN = {"deck_rules": "deck", "workbook_rules": "workbook"}
_SAID = {"office_structure": ("the file opens and reads correctly", "the file may not open cleanly"),
         "office_schema": ("the file follows Office's own format rules", "the file breaks Office's format rules")}


_RUN: contextvars.ContextVar = contextvars.ContextVar("meshpool_check_run", default="")
_ACTIVE: dict = {}                            # run id -> start time: runs begun in this process and not ended
_ACTIVE_LOCK = threading.Lock()


def _dir() -> Path:
    d = C.home() / "checks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(run_id: str) -> str:
    return re.sub(r"[^\w.-]", "_", str(run_id))[:100] or "run"


def _file(run_id: str) -> Path:
    return _dir() / f"{_safe(run_id)}.jsonl"


def _pointer() -> Path:
    return _dir() / "current.json"


def begin(run_id: str) -> None:
    """Start recording for `run_id`. Replaces any earlier record of the same id."""
    with _ACTIVE_LOCK:
        _ACTIVE[str(run_id)] = time.time()
    _RUN.set(str(run_id))                     # this thread's context: its checks go to this run, whatever began later
    try:
        _file(run_id).write_text("", encoding="utf-8")
        _pointer().write_text(json.dumps({"run": str(run_id), "ts": time.time()}), encoding="utf-8")
        os.environ["MH_CHECK_RUN"] = str(run_id)
    except OSError:
        pass


def end(run_id: str) -> None:
    """Stop recording. The record stays readable."""
    with _ACTIVE_LOCK:
        _ACTIVE.pop(str(run_id), None)
    if _RUN.get() == str(run_id):
        _RUN.set("")
    try:
        if current() == str(run_id):
            _pointer().unlink(missing_ok=True)
        if os.environ.get("MH_CHECK_RUN") == str(run_id):
            os.environ.pop("MH_CHECK_RUN", None)
    except OSError:
        pass


def current() -> str:
    """The run being recorded now ("" when none): this context's own run first, then the process-wide one."""
    own = _RUN.get()
    if own:
        with _ACTIVE_LOCK:
            live = own in _ACTIVE             # bound here and not ended (a thread reused after end() falls through)
        if live and (C.home() / "checks" / f"{_safe(own)}.jsonl").is_file():   # and its record is in this data folder
            return own
    rid = os.environ.get("MH_CHECK_RUN", "")
    if rid:
        return rid
    try:
        d = json.loads(_pointer().read_text(encoding="utf-8"))
        if time.time() - float(d.get("ts", 0)) < STALE_S:
            return str(d.get("run", ""))
    except (OSError, ValueError):
        pass
    return ""


@contextmanager
def bind(run_id: str):
    """Record the checks made inside this block (in a worker thread, say) into `run_id`."""
    tok = _RUN.set(str(run_id or ""))
    try:
        yield
    finally:
        _RUN.reset(tok)


def run_for(chat_id: str) -> str:
    """The newest run begun in this process for chat `chat_id` (run ids are "<chat id>:<turn start>"), else ""."""
    if not chat_id:
        return ""
    pre = f"{chat_id}:"
    with _ACTIVE_LOCK:
        mine = [(t, r) for r, t in _ACTIVE.items() if r.startswith(pre)]
    return max(mine)[1] if mine else ""


def env_for(chat_id: str) -> dict:
    """Environment for a process started for chat `chat_id`: its checks go to that chat's run, not to whichever chat
    began a turn last. Empty when the chat has no run here (the inherited behaviour stays)."""
    rid = run_for(chat_id)
    return {"MH_CHECK_RUN": rid} if rid else {}


def record(kind: str, label: str, count: int | None = None, ok: bool = True, subject: str = "") -> bool:
    """One check that ran. Returns False (and does nothing) when no recorder is active. Never raises."""
    try:
        rid = current()
        if not rid:
            return False
        row = {"ts": round(time.time(), 3), "kind": kind, "label": label, "count": count, "ok": bool(ok),
               "subject": subject}
        with _file(rid).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 - a check record must never break an engine
        return False


def stage(text: str) -> bool:
    """What a long engine job (deck, report, workbook) is doing now, e.g. "Deck: writing the slides". It goes into the
    same record as the checks, so the turn's watcher (agent_api._turn_begin, every 1.5 s) brings it to the chat's
    working line as a "job_stage" event: no extra polling. Not a check: never counted in the summary."""
    return record("stage", str(text or "")[:120])


def passed(kind: str, label: str, count: int | None = None, subject: str = "") -> bool:
    return record(kind, label, count, True, subject)


def failed(kind: str, label: str, count: int | None = None, subject: str = "") -> bool:
    return record(kind, label, count, False, subject)


def rules_ran(kind: str, label: str, statuses: dict, subject: str = "", names: dict | None = None) -> None:
    """From a validator's per-rule status dict ({"P3": "pass" | "fail" | "warn" | "not_run"}): the rules that ran.
    Rules marked not_run are not counted. `names` ({"P3": "what is wrong"}) is kept for the details line."""
    try:
        ran = [s for s in (statuses or {}).values() if s != "not_run"]
        ok = sum(1 for s in ran if s in ("pass", "warn"))
        bad = sum(1 for s in ran if s == "fail")
        if ok:
            passed(kind, label, ok, subject)
        if bad:
            failed(kind, label, bad, subject)
            det = [f"{k}: {str(v)[:110]}" for k, v in (names or {}).items() if (statuses or {}).get(k) == "fail"][:8]
            if det:
                _detail(kind, subject, det)
    except Exception:  # noqa: BLE001
        pass


def fact(name: str, value: bool, subject: str = "") -> bool:
    """A fact about the file that decides what the closing line may say ("workbook_totals": it has a totals row or a SUM).
    Not a check: never counted."""
    return record("fact", name, None, bool(value), subject)


def _detail(kind: str, subject: str, lines: list) -> None:
    """The names of what did not pass, for the "details" line. Not a check: never counted."""
    record("detail", kind, None, True, json.dumps({"kind": kind, "subject": subject, "lines": lines}))


def _rows(run_id: str) -> list[dict]:
    out = []
    try:
        for line in _file(run_id).read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def _latest(rows: list[dict]) -> list[dict]:
    """A deck checked three times while it was fixed counts once. Per (kind, subject) the latest run of results
    counts: the rows written within a second of the last row of that group."""
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["kind"], r.get("subject", "")), []).append(r)
    out = []
    for g in groups.values():
        end = g[-1]["ts"]
        out += [r for r in g if end - r["ts"] <= 1.0]
    return sorted(out, key=lambda r: r["ts"])


def _phrase(kind: str, label: str, n: int | None) -> str:
    one, many = _LABELS.get(kind, (label, label))
    if n is None:
        return label
    return f"{n} {one if n == 1 else many}"


def _row_text(r: dict) -> str:
    """One check row in plain words: "16 checks on the workbook passed", "the file opens and reads correctly"."""
    kind, n, ok = r["kind"], r.get("count"), r["ok"]
    if kind in _NOUN and n is not None:
        return f"{n} {'check' if n == 1 else 'checks'} on the {_NOUN[kind]} {'passed' if ok else 'did not pass'}"
    if kind in _SAID:
        return _SAID[kind][0 if ok else 1]
    return _phrase(kind, r["label"], n) + (" passed" if ok else " did not pass")


def _checked_rows(run_id: str) -> list[dict]:
    return [r for r in _rows(run_id) if r.get("kind") not in ("stage", "detail", "fact")]


def _details(run_id: str) -> list[str]:
    """What did not pass, by name: one line per failing check, for those who want to know."""
    out: list = []
    for r in _rows(run_id):
        if r.get("kind") == "detail":
            try:
                d = json.loads(r.get("subject") or "{}")
            except ValueError:
                continue
            # "P3: placeholder failed" names a rule by its code: the code means nothing to a person, the words after it do
            for x in d.get("lines", []):
                x = re.sub(r"^[A-Za-z]{1,3}\d{1,3}:\s*", "", str(x))
                if x and x not in out:
                    out.append(x)
    return out


# What a kind of check looks at, in plain words for the line under the reply.
_WHAT = {"workbook_rules": "the formulas", "deck_rules": "the slide layout and text",
         "office_structure": "that the file opens cleanly", "office_schema": "that the file opens cleanly"}
# The failures that are a real problem in the user's file, by rule, in plain words. Every other failing rule (number
# formats, colours, a frozen header, a checks tab, a slide's word count) stays in the details fold only: "27 of 36
# checks passed" and "the checker flagged three things" read as failure to a non-technical user (Priya, Maya).
_REAL = {"X0": "the file may not open cleanly in Excel", "X3": "the balance sheet does not balance",
         "X4": "a total does not tie out", "X5": "some formulas show errors",
         "X6": "a formula refers back to itself", "X13": "the file links to other workbooks or holds macros",
         "X14": "a cost is added where it should be taken away",
         "P0": "the file may not open cleanly in PowerPoint", "P1": "some slides did not draw",
         "P5": "some text runs past the edge of its box", "P14": "a chart and its text do not agree"}
# The deck rules that name a real problem, by the plain name the details carry (validate_pptx RULE_NAMES and
# visual_check NAMES), as the sentence the line says. The slide numbers are read from the message.
_REAL_DECK = {"The file opens and follows Office's format": "the file may not open cleanly in PowerPoint",
              "Nothing overlaps or runs off the page": "something overlaps or runs off the page",
              "Text fits its box at a readable size": "some text runs past the edge of its box",
              "Spacing is even and nothing collides": "some things collide",
              "Text agrees with the charts": "a chart and its text do not agree",
              "Blocks line up and fill the page": "some blocks do not line up",
              "Chart labels clear of the bars": "a chart label sits on a bar",
              "No empty column, box or placeholder": "an empty box or placeholder is left",
              "Footnotes and source lines are whole sentences": "a footnote is cut off before the end of its sentence",
              "Titles wrap without a lone last word": "a title leaves one word alone on its last line",
              "No stamp the brief did not ask for": "there is a stamp you did not ask for",
              "The brand the request asks for (footer, colours, fonts)": "something the brand asks for is missing"}
_REAL_X12 = re.compile(r"integrity checks are FALSE|Master flag", re.I)   # the workbook's own checks fail


def _problems(run_id: str, kinds: dict) -> list[str]:
    """The real problems in the file, in plain words, each once."""
    out: list[str] = []

    def add(s):
        if s not in out:
            out.append(s)
    for r in _rows(run_id):
        if r.get("kind") != "detail":
            continue
        try:
            d = json.loads(r.get("subject") or "{}")
        except ValueError:
            continue
        for line in d.get("lines", []):
            rule, _, msg = str(line).partition(":")
            rule = rule.strip()
            if rule in _REAL_DECK:
                nums = list(dict.fromkeys(re.findall(r"\b[Ss]lides? (\d+)", msg)))
                where = ""
                if nums:
                    where = (" on slide " + nums[0]) if len(nums) == 1 else \
                        " on slides " + ", ".join(nums[:-1]) + " and " + nums[-1]
                add(_REAL_DECK[rule] + where)
            elif rule in _REAL:
                add(_REAL[rule])
            elif rule == "X12" and _REAL_X12.search(msg):
                add("some of the workbook's own checks fail")
    for kind in ("office_structure", "office_schema"):
        k = kinds.get(kind)
        if k and k["badrow"] and not k["okrow"]:
            add("the file may not open cleanly")
    return out


def summary(run_id: str) -> dict:
    """{"line": "Checked the totals and formulas, and that the file opens cleanly.", "items": [...], "details":
    [...], "failed": n}. The line says what was checked in plain words and names only a real problem in the file
    ("One thing to look at: some formulas show errors."); counts and the checks that did not pass stay in "items" and
    "details", the details fold."""
    rows = _latest(_checked_rows(run_id))
    kinds: dict = {}
    for r in rows:                                     # same kind from several files: add their counts
        k = kinds.setdefault(r["kind"], {"label": r["label"], "ok": 0, "bad": 0, "okn": False, "okrow": False,
                                         "badrow": False})
        n = r.get("count")
        if r["ok"]:
            k["ok"] += int(n or 0)
            k["okn"] = k["okn"] or n is not None
            k["okrow"] = True
        else:
            k["bad"] += int(n or 1)
            k["badrow"] = True
    facts = {r.get("label"): bool(r.get("ok")) for r in _rows(run_id) if r.get("kind") == "fact"}
    nbad = sum(k["bad"] for k in kinds.values())
    probs = _problems(run_id, kinds) if nbad else []
    what, sents = [], []
    for kind, k in sorted(kinds.items(), key=lambda kv: kv[0] not in _NOUN):      # what was checked in the file first
        if kind in _WHAT:
            if (k["okrow"] or kind in _NOUN) and _WHAT[kind] not in what \
                    and not (probs and k["bad"] and kind == "deck_rules"):   # a deck rule that failed is named below
                w = _WHAT[kind]
                if kind == "workbook_rules":                       # "totals" only for a workbook that has a totals row or a SUM
                    w = "the totals and formulas" if facts.get("workbook_totals") else \
                        "the formulas" if facts.get("workbook_formulas", True) else ""
                if w and w not in what:
                    what.append(w)
        elif k["okrow"]:
            sents.append(f"Checked {_phrase(kind, k['label'], k['ok'] if k['okn'] else None)}"
                         + (f" ({k['bad']} did not hold up)" if k["bad"] else ""))
    if what:
        sents.insert(0, "Checked " + (what[0] if len(what) == 1 else ", ".join(what[:-1]) + ", and " + what[-1]))
    if probs:
        sents.append(("One thing to look at: " if len(probs) == 1 else "Things to look at: ") + "; ".join(probs))
    line = " ".join(x.rstrip(".") + "." for x in sents)
    return {"line": line, "items": [{"kind": r["kind"], "label": _row_text(r), "ok": r["ok"],
                                      "subject": r.get("subject", "")} for r in rows],
            "details": _details(run_id) if nbad else [], "failed": nbad}


_LOOK_RX = re.compile(r"Checked(?: as (?P<who>[^:]+?))?: (?P<body>.+)", re.S)
_GENERIC_WHO = re.compile(r"^(?:someone|anyone|a (?:first-time )?(?:visitor|user)|the (?:visitor|user)|people|a person)\b", re.I)


# The reply ends unsure of its own result: a question back to the user, or words of doubt about the output. Sara: a
# reply that asked "I can't see section 3's ..., want me to fix it?" sat over "Checked it ...: all good."
# Priya, round 4: "I haven't opened it to confirm the summary formulas... My check of the workbook didn't finish." sat
# over "Checked · all good": the doubt was in the first paragraph, and "opened" and "didn't finish" were not read.
_DOUBT = re.compile(r"\b(?:can(?:'|’|no)?t|cannot|could ?not|couldn(?:'|’)t|unable to|did ?not|didn(?:'|’)t|"
                    r"have ?not|haven(?:'|’)t|has ?not|hasn(?:'|’)t|was not able to|wasn(?:'|’)t able to)\s+"
                    r"(?:yet\s+|been able to\s+|fully\s+)?(?:see|seen|view|viewed|check|checked|verify|verified|confirm|"
                    r"confirmed|open|opened|test|tested|look at|looked at|recalculate|recalculated|finish|finished|"
                    r"render|rendered|read it back|run the check)\b|"
                    r"\b(?:not sure|unsure|may be wrong|might be wrong|please check|double-check|unchecked|unverified|"
                    r"unseen|not (?:yet )?(?:checked|verified|looked at|seen|opened|tested|confirmed)|"
                    r"(?:check|checks|review)\b[^.?!\n]{0,40}\b(?:did ?n(?:o|')t|never) (?:finish|run|complete))\b", re.I)


def open_doubt(reply) -> bool:
    """The reply ends with an open question or says it could not confirm part of the result. A list: any of its
    texts (the reply, the look's own line with its offer)."""
    if isinstance(reply, (list, tuple)):
        return any(open_doubt(r) for r in reply)
    paras = [p.strip() for p in re.split(r"\n\s*\n", str(reply or "")) if p.strip()]
    if not paras:
        return False
    last = paras[-1]
    return last.rstrip("*_ )\"'").endswith("?") or bool(_DOUBT.search(last)) or bool(_DOUBT.search(paras[0]))


def merge_line(look: str, checks: str, reply: str = "") -> str:
    """One line from the look line ("Checked as a florist shop owner: found nothing to fix.") and the checks line ("Checked
    the formulas, and that the file opens cleanly."): "Checked it the way a florist shop owner would, and the formulas, and
    that the file opens: all good." The persona is left out when it adds nothing ("someone opening this file"). "" when the
    look line is not in the shape this reads (the two lines then stay as they are)."""
    m = _LOOK_RX.fullmatch(str(look or "").strip())
    bare = re.fullmatch(r"Checked it(?: as (?P<who>.+?))?\.", str(look or "").strip())     # the look made no claim
    if not (m or bare) or not str(checks or "").strip():
        return ""
    who = ((m or bare).group("who") or "").strip()
    body = m.group("body").strip() if m else ""
    sents = [x.strip() for x in re.split(r"(?<=\.)\s+(?=[A-Z])", checks.strip()) if x.strip()]
    what = ""
    rest = []
    for x in sents:
        if x.startswith("Checked ") and not what:
            what = re.sub(r"\bopens cleanly\b", "opens", x[len("Checked "):].rstrip("."))
        else:
            rest.append(x)
    head = "Checked it" + (f" the way {who} would" if who and not _GENERIC_WHO.match(who) else "")
    if what:
        head += (", and " if what.startswith(("that ", "the ")) else ", ") + what
    first = body.rstrip(".")
    verdict = "all good" if first.lower() == "found nothing to fix" and not rest else first
    if verdict == "all good" and open_doubt(reply):
        verdict = ""                                   # the reply itself is unsure: the line claims nothing it is not
    return " ".join([f"{head}: {verdict}." if verdict else f"{head}."] + rest)


_NUM_WORDS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def _count(part: str) -> int:
    """Things named in "fixed 2 things: a and b", "fixed low contrast and a console error", "still open: a, b"."""
    m = re.match(r"\s*(\d+|one|a|an|two|three|four|five)\s+(?:things?|problems?|issues?)\b", part, re.I)
    if m:
        w = m.group(1).lower()
        return int(w) if w.isdigit() else _NUM_WORDS[w]
    part = re.split(r"[.;]", part, maxsplit=1)[0]
    m = re.search(r"\band (\d+) more\b", part)
    names = [x for x in re.split(r",\s*|\s+and\s+", re.sub(r"\band \d+ more\b", "", part)) if x.strip()]
    return max(1, len(names) + (int(m.group(1)) if m else 0))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def footer(look: str, checks: dict | None = None, reply="") -> tuple[str, str]:
    """(the short muted line for the turn's footer, the full sentence for its fold) from the look line and the
    checks summary: "Checked · all good", "Checked · fixed 2 things", "Checked · fixed 1 thing · 1 to look at". The
    reply itself carries no "Checked as ..." sentence (Lina, Priya, round 3). A reply that is itself unsure (open_doubt)
    gets no "all good". ("", "") when there is nothing."""
    look = str(look or "").strip()
    sm = checks or {}
    cline = str(sm.get("line") or "").strip()
    if not look and not cline:
        return "", ""
    full = (merge_line(look, cline, reply=reply) if look and cline else "") or " ".join(x for x in (look, cline) if x)
    fixed = still = 0
    named: list = []                                   # what is still open, by name (Maya, round 4: "1 to look at" said
    low = look.lower()                                 # not what)
    m = re.search(r"\bfixed\s+(.+?)(?:\.\s|$|;\s*still open)", look, re.I | re.S)
    if m and not re.match(r"\s*nothing\b", m.group(1), re.I):
        fixed = _count(m.group(1).split(": ", 1)[0] if re.match(r"\s*\d+\s+things?\s*:", m.group(1)) else m.group(1))
    m = re.search(r"\bstill open:\s*(.+?)(?:\.\s|\.$|$)", look, re.I | re.S)
    if m:
        still += _count(m.group(1))
        named.append(m.group(1))
    if re.search(r"\b(?:does|do) not open\b|\bis empty\b|\bare empty\b|could not (?:run|finish|open)|did not open", low):
        still = max(still, 1)
    m = re.search(r"\b(?:One thing to look at|Things to look at):\s*(.+?)(?:\.\s|\.$|$)", cline, re.S)
    if m:
        still += len([x for x in m.group(1).split("; ") if x.strip()]) if m.group(0).startswith("Things") else 1
        named.append(m.group(1))
    bits = []
    if fixed:
        bits.append("fixed " + _plural(fixed, "thing"))
    if still:
        point = _brief(named[0]) if still == 1 and len(named) == 1 else ""
        bits.append(f"{still} to look at" + (f": {point}" if point else ""))
    if not bits:
        if open_doubt(reply):
            return "Checked", full
        bits.append("all good")
    return "Checked · " + " · ".join(bits), full


def _brief(point: str, limit: int = 60) -> str:
    """One open point, short enough for the footer: "legend sits on top of the two most expensive bars"."""
    t = re.sub(r"\s*\([^)]*\)", "", str(point or ""))          # "(where: ...)" stays in the fold
    t = " ".join(t.split()).strip(" .;:,")
    if re.match(r"(?:\d+|one|a|an|two|three|four|five)\s+(?:things?|problems?|issues?)\b", t, re.I):
        return ""                                              # "Still open: 1 thing." names nothing
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "..."


def final_line(run_id: str) -> str:
    return summary(run_id)["line"]


def events_for(run_id: str, since: int = 0) -> list[dict]:
    """Events for the progress panel, one per recorded check after the first `since` records."""
    out = []
    for r in _rows(run_id)[since:]:
        if r.get("kind") == "stage":                   # an engine's stage: the working line, not a check row
            out.append({"type": "job_stage", "text": r.get("label", "")})
            continue
        if r.get("kind") in ("detail", "fact"):        # names of what failed: shown with the final line, not as a row
            continue
        out.append({"type": "check", "kind": r["kind"], "ok": r["ok"], "subject": r.get("subject", ""),
                    "text": _row_text(r)})
    return out
