"""Nothing on a slide that the sources or the request do not state (rule P29).

Daniel, round 2: the deck drew a "Target 47%" dashed line (the report gives 47% as the average, not a target), gave
the next steps owners and dates ("Plants · 29 Jan 2027", "HR · 30 Apr 2027") and dated the cover "Board meeting,
November 2026". None of it was in the report or the brief; a footnote saying "proposed" did not make it his.

    corpus = sources(brief, facts)               # the request, its attached files and the facts
    done = ground(slides, corpus, request)       # before rendering: the plan's own fields lose what is invented
    fails = check(pptx, corpus, request)         # after: a number, date or name on a slide found in neither fails

A next-steps slide without owners or dates in the sources says "Owner to confirm" or has no such column.
"""
from __future__ import annotations

import datetime as _dt
import re

from .common import candidates, failure as F, numbers

_MON = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MNAME = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
         r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
DATE = re.compile(r"(?i)\b(?:(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?)?" + _MNAME + r"\s+((?:19|20)\d\d)\b|"
                  r"\b(q[1-4]|h[12])\s+((?:19|20)\d\d)\b|\b((?:19|20)\d\d)\b")       # "29th of January 2027" too
_DATE_FIRST = re.compile(r"(?i)\b" + _MNAME + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d\d)\b")   # "January 29, 2027"
_ISO = re.compile(r"(?<![\d-])((?:19|20)\d\d)-(\d{1,2})-(\d{1,2})(?!\d)")                 # "2027-01-29"
_NUMERIC = re.compile(r"(?<![\d/.])(\d{1,2})([/.])(\d{1,2})\2((?:19|20)\d\d)(?!\d)")    # "29/01/2027", "29.01.2027"


def _numeric(m) -> list[tuple]:
    """The dates "a/b/yyyy" can mean: day first ("29/01/2027"), month first when day first cannot be ("01/29/2027"),
    and both when both can ("05/03/2027"), day first leading."""
    a, b, y = int(m.group(1)), int(m.group(3)), int(m.group(4))
    keys = [(y, mo, d) for d, mo in ((a, b), (b, a)) if 1 <= mo <= 12 and 1 <= d <= 31]
    return list(dict.fromkeys(keys))
ROLES = re.compile(r"\b(?:CEO|CFO|COO|CTO|CIO|CHRO|CMO|CDO|HR|PMO|Ops|Procurement|Sourcing|Purchasing|Finance team|"
                   r"Legal|Head of [A-Z][a-z]+(?: [A-Z][a-z]+)?|VP [A-Z][a-z]+|Plant managers?|Maintenance lead|"
                   r"Operations director|Steering committee|Programme office|Program office)\b")
EVENTS = re.compile(r"(?i)\b(?:board meeting|board review|board session|steering committee(?: meeting)?|town hall|"
                    r"off-?site|workshop|kick-?off(?: meeting)?|annual general meeting|investor day|"
                    r"(?:management|leadership|executive) (?:meeting|committee|offsite)|ExCo)\b")
GOALS = re.compile(r"(?i)\b(?:targets?|goals?)\b")
ACRONYM = re.compile(r"\b[A-Z]{2,5}s?\b")
_NEUTRAL = re.compile(r"(?i)^(?:owner to confirm|to confirm|tbc|to be confirmed)$")
_OWN_CUE = r"(?:owner|owns|owned by|responsible|accountable|leads?|led by|sponsor)"
_ROLE_NOTE = re.compile(r"(?i)\b(?:owners?|dates?|timings?|deadlines?)\b[^.]{0,60}\b(?:proposed|confirm\w*|illustrative|"
                        r"indicative|placeholders?)\b")
_FOLLOW = r"(?:gate|review|meeting|deadline|milestone|checkpoint|board|decision)"


def today() -> _dt.date:
    import os
    v = os.environ.get("MH_TODAY")                       # tests pin the date
    return _dt.date.fromisoformat(v) if v else _dt.date.today()


def sources(brief: str, facts: list | None = None) -> str:
    """Everything the deck may state: the request with its attached files, and the facts (research included)."""
    return str(brief or "") + "\n" + "\n".join(str(f.get("text") or "") for f in facts or [] if isinstance(f, dict))


def request_of(brief: str) -> str:
    return str(brief or "").split("\n--- ", 1)[0]


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%.$€£ ]+", " ", str(t or "").lower())).strip()


def _month(name: str) -> int:
    return _MON.index(name.lower()[:3]) + 1


def _dates(text: str, every: bool = False) -> list[tuple[str, tuple]]:
    """(as written, key) for each date: key (y, m, d), (y, m, None), (y, 'q3'), or (y, None, None) for a year. A
    numeric date that reads both ways ("05/03/2027") gives its day-first key, or with every=True both (what the sources
    may mean). The user's dates in ISO form, as 29/01/2027 and as "29th of January 2027" are whole dates too."""
    out = []
    t = str(text or "")
    for m in _ISO.finditer(t):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append((m.group(0), (y, mo, d)))
            t = t.replace(m.group(0), " ", 1)
    for m in _NUMERIC.finditer(t):
        keys = _numeric(m)
        if keys:
            out += [(m.group(0), k) for k in (keys if every else keys[:1])]
            t = t.replace(m.group(0), " ", 1)
    for m in _DATE_FIRST.finditer(t):
        out.append((m.group(0), (int(m.group(3)), _month(m.group(1)), int(m.group(2)))))
    t = _DATE_FIRST.sub(" ", t)
    for m in DATE.finditer(t):
        if m.group(3):
            out.append((m.group(0), (int(m.group(3)), _month(m.group(2)), int(m.group(1)) if m.group(1) else None)))
        elif m.group(5):
            out.append((m.group(0), (int(m.group(5)), m.group(4).lower())))
        elif m.group(6):
            out.append((m.group(0), (int(m.group(6)), None, None)))
    return out


class Known:
    """What the sources state: their dates, numbers and words, plus today's month (a cover is dated today)."""

    def __init__(self, corpus: str, request: str = "", when: _dt.date | None = None):
        self.text = _norm(corpus)
        self.request = _norm(request)
        self.raw = str(corpus or "")
        d = when or today()
        self.dates = {k for _, k in _dates(corpus, every=True)}
        self.dates |= {(d.year, d.month, None), (d.year, d.month, d.day), (d.year, None, None)}
        self.years = {k[0] for k in self.dates}
        self.months = {(k[0], k[1]) for k in self.dates if len(k) == 3 and k[1]}
        self.quarters = {(k[0], k[1]) for k in self.dates if len(k) == 2}
        self.nums = []
        for _, v, u in numbers(corpus):
            self.nums += candidates(abs(v), u)

    def date_ok(self, key: tuple) -> bool:
        if len(key) == 2:                                 # a quarter or a half: stated, or a month inside it
            y, q = key
            if (y, q) in self.quarters:
                return True
            n = int(q[1])
            span = range(3 * n - 2, 3 * n + 1) if q[0] == "q" else range(6 * n - 5, 6 * n + 1)
            return any((y, m) in self.months for m in span)
        y, m, d = key
        if m is None:
            return y in self.years
        if d is None:
            return (y, m) in self.months
        return (y, m, d) in self.dates

    def word_ok(self, w: str) -> bool:
        n = _norm(w)
        if not n:
            return True
        stem = re.sub(r"s$", "", n)
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(stem) + r"s?(?![a-z0-9])", self.text))

    def owner_ok(self, owner: str) -> bool:
        """An owner the request names, or one the sources tie to a task ("the CFO owns the case")."""
        o = _norm(owner)
        if not o or _NEUTRAL.match(owner.strip()):
            return True
        if re.search(r"(?<![a-z0-9])" + re.escape(o) + r"(?![a-z0-9])", self.request):
            return True
        pat = (r"(?i)(?:" + _OWN_CUE + r")\W+(?:\w+\W+){0,4}?" + re.escape(owner.strip()) + r"\b|\b" +
               re.escape(owner.strip()) + r"\W+(?:\w+\W+){0,3}?" + _OWN_CUE)
        return bool(re.search(pat, self.raw))

    def number_ok(self, raw: str, v: float, u: str, extra: list[float]) -> bool:
        m = re.search(r"\.(\d+)", raw)
        half = 0.5 * 10 ** -(len(m.group(1)) if m else 0) + 1e-9
        for c in candidates(abs(v), u):
            scale = c / abs(v) if v else 1.0
            for k in self.nums + extra:
                if abs(c - k) <= max(half * scale, 0.006 * abs(k)):
                    return True
        return False


def _strip_dates(text: str, known: Known) -> str:
    """The text without the dates the sources do not give: "Business case written by 29 Jan 2027" reads "Business
    case written"; "review the scale-up at a June 2027 gate" reads "review the scale-up at a gate"."""
    t = str(text or "")
    for raw, key in _dates(t):
        if known.date_ok(key) or re.fullmatch(r"(?:19|20)\d\d", raw.strip()) and key[0] in known.years:
            continue
        esc = re.escape(raw)
        if re.search(esc + r"\s+" + _FOLLOW, t, re.I):
            t = re.sub(r"\s*" + esc, "", t, count=1)
        else:
            t = re.sub(r"(?i),?\s*\b(?:by|on|in|from|before|after|until|due|at|starting|start(?:s|ing)? in)?\s*"
                       r"(?:the\s+|a\s+|end of\s+|early\s+|mid-?\s*|late\s+)?" + esc, "", t, count=1)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+([,.;:)])", r"\1", t)
    return re.sub(r"^[\s,;:]+|[\s,;:]+$", "", t)


def _walk_charts(s):
    for c in [s.get("chart")] + list(s.get("charts") or []) + [p.get("chart") for p in s.get("parts") or []
                                                                 if isinstance(p, dict)]:
        if isinstance(c, dict):
            yield c


def ground(slides: list, corpus: str, request: str = "", when: _dt.date | None = None) -> list[str]:
    """The plan's own fields lose what the sources and the request do not give: a chart target, the owner and the
    date of a next step, a dated milestone, the occasion on the cover. Returns what was changed, in plain words."""
    known = Known(corpus, request, when)
    goals = bool(GOALS.search(corpus or ""))
    done: list[str] = []
    d = when or today()
    for n, s in enumerate(slides, 1):
        if not isinstance(s, dict):
            continue
        for c in _walk_charts(s):
            if c.get("target") is None:
                continue
            lab = str(c.get("target_label") or "")
            if lab and not GOALS.search(lab):             # the plan's own words for the line ("All-plant average 47%")
                continue
            v = c["target"]
            num = f"{v:g}" if isinstance(v, (int, float)) else str(v)
            if goals and re.search(r"(?i)\b(?:targets?|goals?)\b\W+(?:[\w.,]+\W+){0,6}?" + re.escape(num) + r"\b|\b" +
                                   re.escape(num) + r"\b\W*(?:%|percent)?\W+(?:[\w.,]+\W+){0,4}?(?:targets?|goals?)\b",
                                   corpus or ""):
                continue                                  # a target the sources set, at that value
            c.pop("target", None)
            c.pop("target_label", None)
            done.append(f"slide {n}: dropped a target line the sources do not give")
        arch = s.get("archetype")
        if arch == "cover" and s.get("date"):
            dt = str(s["date"])
            ev = EVENTS.search(dt)
            if ev and not known.word_ok(ev.group(0)):
                dt = re.sub(r"^[\s,;:]+", "", dt.replace(ev.group(0), "", 1))
            dt = _strip_dates(dt, known) or f"{d:%B %Y}"
            if not re.search(r"\d", dt):
                dt = f"{d:%B %Y}"
            if dt != s["date"]:
                done.append(f"slide {n}: cover date '{s['date']}' is now '{dt}'")
                s["date"] = dt
        if arch not in ("closing", "next_steps"):
            continue
        removed = False
        for k in ("title", "decision", "subtitle"):
            if isinstance(s.get(k), str):
                new = _strip_dates(s[k], known)
                if new != s[k]:
                    s[k], removed = new, True
        for st in [x for x in s.get("steps") or [] if isinstance(x, dict)]:
            if st.get("owner") and not known.owner_ok(str(st["owner"])):
                st.pop("owner")
                removed = True
            tm = str(st.get("timing") or "")
            if tm and (any(not known.date_ok(k) for _, k in _dates(tm)) or EVENTS.search(tm)):
                st.pop("timing")
                removed = True
            for k in ("action", "detail"):
                if isinstance(st.get(k), str):
                    new = _strip_dates(st[k], known)
                    if new != st[k]:
                        st[k], removed = new, True
            if isinstance(st.get("milestones"), list):
                ms = [_strip_dates(m, known) if isinstance(m, str) else m for m in st["milestones"]]
                if ms != st["milestones"]:
                    removed = True
                st["milestones"] = [m for m in ms if not isinstance(m, str) or len(m.split()) >= 2]
        g = s.get("gate")
        if isinstance(g, dict):
            if g.get("date") and any(not known.date_ok(k) for _, k in _dates(str(g["date"]))):
                g.pop("date")
                removed = True
            if isinstance(g.get("text"), str):
                g["text"] = _strip_dates(g["text"], known)
        if removed:
            done.append(f"slide {n}: owners and dates the sources do not give were taken out of the next steps")
            if isinstance(s.get("footnotes"), list):
                s["footnotes"] = [t for t in s["footnotes"] if not _ROLE_NOTE.search(str(t))]
    return done


# ------------------------------------------------------------------ P29 on the built file
_SKIP = ("Firm", "ov_SectionNo")


def _slide_numbers(prov: dict, n: int) -> list[float]:
    """Numbers the render traced to a calc (verified arithmetic on the facts) on this slide."""
    out = []
    for e in (prov or {}).get(str(n), []) or []:
        if re.match(r"^(?:S\d+)?C\d+$|^calc$", str(e.get("prov") or "")):
            for _, v, u in numbers(str(e.get("text") or "")):
                out += candidates(abs(v), u)
    return out


def check(path: str, corpus: str, request: str = "", provenance: dict | None = None,
          when: _dt.date | None = None) -> list[dict]:
    """P29: every number, date and name on a slide is in the sources or the request."""
    from pptx import Presentation
    known = Known(corpus, request, when)
    goals = bool(GOALS.search(corpus or ""))
    out = []
    ignore = re.compile(r"^(19|20)\d{2}$|^[0-9]{1,2}$")
    prov = (provenance or {}).get("slides", provenance or {}) if isinstance(provenance, dict) else {}
    for n, sl in enumerate(Presentation(path).slides, 1):
        bad: list[str] = []
        texts = [(sh.name or "", sh.text_frame.text) for sh in sl.shapes if sh.has_text_frame
                 and sh.text_frame.text.strip() and (sh.name or "") not in _SKIP
                 and not re.fullmatch(r"(?:ov_)?role_numeral_\d+|ov_CoverSecNo\d+", sh.name or "")]
        for sh in sl.shapes:
            if getattr(sh, "has_table", False) and sh.has_table:
                texts += [("table", c.text) for r in sh.table.rows for c in r.cells if c.text.strip()]
        found = []
        for _, t in texts:
            found += numbers(t, ignore)
        extra = _slide_numbers(prov, n)
        vals = [abs(v) for _, v, _ in found]
        derived = [abs(a - b) for a in vals for b in vals] + [a / b for a in vals for b in vals if b] + \
            [a + b for a in vals for b in vals]
        for raw, v, u in found:
            if not known.number_ok(raw, v, u, extra + derived):
                bad.append(f"the number '{raw.strip()}'")
        for name, t in texts:
            for raw, key in _dates(t):
                if not known.date_ok(key) and not (len(key) == 3 and key[1] is None and name == "Source"):
                    bad.append(f"the date '{raw}'")
            if re.fullmatch(r"When\d+", name):            # a next step's owner cell: someone the sources name
                for part in [p.strip() for p in t.split("·")]:
                    if part and not _dates(part) and not known.owner_ok(part):
                        bad.append(f"the owner '{part}'")
            for m in ROLES.finditer(t):
                if not known.word_ok(m.group(0)):
                    bad.append(f"the name '{m.group(0)}'")
            for m in EVENTS.finditer(t):
                if not known.word_ok(m.group(0)):
                    bad.append(f"the occasion '{m.group(0)}'")
            if not goals:
                m = GOALS.search(t)
                if m:
                    bad.append(f"a '{m.group(0)}' the sources do not set")
            for m in ACRONYM.finditer(t):
                if not known.word_ok(m.group(0)) and not ROLES.fullmatch(m.group(0)):
                    bad.append(f"the name '{m.group(0)}'")
        bad = list(dict.fromkeys(bad))
        if bad:
            out.append(F("P29", "invented", f"Slide {n}: {', '.join(bad[:10])} {'is' if len(bad) == 1 else 'are'} in "
                         "neither the sources nor the request. Take it out, or use neutral wording (\"Owner to "
                         "confirm\") when the sources do not say.", slide=n, measured=len(bad), limit=0,
                         fix_scope="content"))
    return out
