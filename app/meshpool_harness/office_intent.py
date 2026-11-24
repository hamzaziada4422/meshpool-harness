"""Does an office request need the office engines, or is it a job on a file the user already has?

Plain rules, no model call. Two questions:
- styled_deliverable(text): the message asks for a new styled deliverable, which the engines exist for: a deck or
  presentation, a report or memo, a financial model or a dashboard. A lean Work chat under MH_OFFICE_DIRECT=1 offers
  office_task and the office make_* tools only then (agent/core.py); everything else (sort, filter, fill, add a column
  or a slide, convert, extract, recompute, rename a sheet, small builds) is done with openpyxl, python-docx or
  python-pptx through the shell, as Codex does.
- derive_job(request, files): an engine was handed a job that edits or derives from an existing workbook. The sheets
  engine then keeps the source's sheets and layout: no Cover or Checks tab and no title rows unless asked.
"""
from __future__ import annotations

import re
from pathlib import Path

SEP = "\n\n​"                  # notes only the model sees follow this (pace.SEP)

# A file named in the message: "report.docx" is a file, not a request for a report.
_FILE = re.compile(r"[\w][\w.()-]*\.(?:xlsx|xlsm|xls|csv|docx?|pptx?|pdf|od[tsp])\b", re.I)

# The styled deliverables the engines build.
_NOUN = (r"(?:(?:pitch |slide |board |investor |sales )?decks?|presentations?|slides|slideshows?|power ?points?"
         r"|reports?|memos?|memorand(?:um|a)|briefings?|white ?papers?|proposals?"
         r"|(?:financial|three[- ]statement|3[- ]statement|dcf|lbo|valuation|operating|budget|forecast(?:ing)?"
         r"|revenue|pricing|cash ?flow|p&l|saas)\s+models?"
         r"|dashboards?"
         # models named on their own ("a DCF valuation", "an LBO", "a comps table", "a 3-year budget", "an
         # amortization schedule"); not the verb in "need to budget" or "want to schedule a call"
         r"|dcfs?|lbos?|valuations?|comps|comparables|three[- ]statements?|3[- ]statements?|cash ?flows?"
         r"|(?<!\bto )(?:budgets?|forecasts?|schedules?|models?))")
# Asked for: a making verb a few words before the noun ("make a 10-slide pitch deck", "turn the notes into a deck").
_ASK = re.compile(r"\b(?:make|create|build|prepare|draft|write|produce|design|generate|assemble|put together|"
                  r"give me|need|want|into)\b(?:\W+[\w&'-]+){0,6}?\W+" + _NOUN + r"\b", re.I)
# A deliverable the message points at as one that exists ("make the report in q3.docx shorter") ...
_OLD = re.compile(r"\b(?:the|this|that|my|our|your|its|existing|current|attached)\s+(?:[\w&-]+\s+){0,2}?"
                  + _NOUN + r"\b", re.I)
# ... unless it also asks for a new one ("... and make a deck from it").
_FRESH = re.compile(r"\b(?:a|an|new|another|into)\s+(?:[\w&-]+\s+){0,3}?" + _NOUN + r"\b", re.I)


def styled_deliverable(text: str) -> bool:
    """True when the message asks for a new deck, report or memo, financial model or dashboard.

    styled_deliverable("Make a 10-slide pitch deck for investors")            -> True
    styled_deliverable("Turn notes.docx into a presentation")                  -> True
    styled_deliverable("Sort score.xlsx by midterm1 into sorted_score.xlsx")   -> False (an edit of a file)
    styled_deliverable("Make the report in q3.docx shorter")                   -> False (the report exists)
    styled_deliverable("Add a chart of revenue to slide 3 of deck.pptx")      -> False (no new deliverable)
    """
    t = str(text or "").split(SEP)[0]
    named = bool(_FILE.search(t))
    t = _FILE.sub(" ", t)                     # "report.docx", "deck.pptx" are files, not deliverables
    if not _ASK.search(t):
        return False
    return not (named and _OLD.search(t) and not _FRESH.search(t))


SHEET_SOURCES = (".xlsx", ".xlsm", ".xls", ".csv")
_SHEET_NAME = re.compile(r"[\w][\w.()-]*\.(?:xlsx|xlsm|xls|csv)\b", re.I)
# A name the request gives to the output, not a source: "save it as x.xlsx", "into x.xlsx", "output x.xlsx".
_OUT_CUE = re.compile(r"\b(?:as|into|output(?:\s+(?:to|it\s+(?:to|as)))?|named|called)\s*[\"'`]?\s*$", re.I)


def _named_sources(request: str, files=(), workdir=None) -> list[str]:
    """Workbook or CSV names in the request that are real inputs: a file handed in (`files`) or one that exists under
    workdir, and not the output's name ("save it as apple_valuation.xlsx", "into sorted.xlsx")."""
    t = str(request or "").split(SEP)[0]
    given = {Path(str(f)).name.lower() for f in files or []}
    out = []
    for m in _SHEET_NAME.finditer(t):
        if _OUT_CUE.search(t[max(0, m.start() - 40):m.start()]):
            continue
        name = m.group(0)
        if name.lower() in given or (workdir and (Path(workdir) / name).is_file()):
            out.append(name)
    return out


def derive_job(request: str, files=(), workdir=None) -> bool:
    """An engine job that edits or derives from an existing workbook: a workbook or CSV is an input (handed in, or
    named in the request and present under workdir) and the request does not ask for a model, dashboard, report or
    deck. A build that uses an input ("a DCF valuation using financials.xlsx") is never a derive job."""
    if styled_deliverable(request):
        return False
    return any(Path(str(f)).suffix.lower() in SHEET_SOURCES for f in files or []) or \
        bool(_named_sources(request, files, workdir))


def asks_for(request: str) -> set[str]:
    """What an edit request asks for outright that an edit otherwise never does: a "cover" or "checks" tab, a "title"
    row above the header, or to "rename" sheets."""
    t = str(request or "").lower()
    out = set()
    if re.search(r"\brenam", t):
        out.add("rename")
    if re.search(r"\bcover\s*(?:sheet|tab|page)\b", t):
        out.add("cover")
    if re.search(r"\b(?:checks?|error[- ]checks?|integrity checks?)\s*(?:sheet|tab)\b", t):
        out.add("checks")
    if re.search(r"\b(?:title|heading) (?:row|line)s?\b|\b(?:add|insert|put|place|include|write|give it|create|"
                 r"make)(?:\s+[\w'-]+){0,2}?\s+(?:title|heading)s?\b|\btitle (?:it|the (?:sheet|table|tab|workbook|"
                 r"report))\b|\bwith (?:a|the) (?:title|heading)\b|\b(?:a|the) (?:title|heading) (?:at|above|on top|"
                 r"across|over)\b", t):
        out.add("title")
    return out
