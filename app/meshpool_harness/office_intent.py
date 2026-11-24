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
         r"|dashboards?)")
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


def derive_job(request: str, files=()) -> bool:
    """An engine job that edits or derives from an existing workbook: a workbook or CSV is an input (or named in the
    request) and the request does not ask for a model, dashboard, report or deck."""
    has_source = any(Path(str(f)).suffix.lower() in SHEET_SOURCES for f in files or []) or \
        bool(re.search(r"[\w][\w.()-]*\.(?:xlsx|xlsm|xls|csv)\b", str(request or ""), re.I))
    return has_source and not styled_deliverable(request)


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
    if re.search(r"\btitle (?:row|line)s?\b|\badd (?:a )?title\b|\bheading rows?\b", t):
        out.add("title")
    return out
