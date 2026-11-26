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
# A match starts only where a run of name characters starts: the old form ([\w][\w.()-]*) retried from every
# character of one long unbroken token, which was quadratic (a 300 KB token held the server for minutes).
_FILE = re.compile(r"(?<![\w.()-])[.()-]*\w[\w.()-]*\.(?:xlsx|xlsm|xls|csv|docx?|pptx?|pdf|od[tsp])\b", re.I)

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


# The output file named in the request: "make deck.pptx", "create budget.xlsx", "save it as q3.docx", "write report.pdf".
_OUT_FILE = re.compile(r"\b(?:make|create|build|write|produce|generate|prepare|draft|output|save(?:\s+it)?\s+as|named|called)"
                       r"\s+(?:a\s+|an\s+|the\s+|me\s+|new\s+)?[\"'`]?[\w][\w.()-]*\.(?:pptx?|docx?|xlsx|xlsm|pdf)\b", re.I)


def names_output(text: str) -> bool:
    """The request names the file to make ("Make deck.pptx, 4 to 6 slides ...", "make budget.xlsx: a 12-month
    budget"): a concrete file the agent builds itself, as Codex does."""
    return bool(_OUT_FILE.search(str(text or "").split(SEP)[0]))


def engines_wanted(text: str) -> bool:
    """Office direct offers the engines only for an open-ended styled deliverable: a new deck, report or model the
    user describes without naming the file ("Make a 10-slide pitch deck for investors"). Frozen round on 1c0b6df5,
    Office-7: pptx_chart ("Make deck.pptx, 4 to 6 slides ...") ran the slides engine for 33 and 84 calls, $0.27 of
    the arm's $0.39, where Codex built the deck by hand for $0.02; xlsx_budget ("make budget.xlsx: a 12-month
    budget") took 26 and 18 calls against Codex's 10 and 5. Both grade the same when built by hand."""
    return styled_deliverable(text) and not names_output(text)


_REVISIONS = re.compile(r"\b(?:track(?:ed)?[\s-]+changes?|track changes|redline|redlin(?:e|ing)|revision marks?)\b", re.I)
_COMMENTS = re.compile(r"\b(?:add|insert|leave|with)\s+(?:a\s+|the\s+)?comments?\b(?!\s+column)", re.I)
_WORD = re.compile(r"\.docx?\b|\bword\b|\bdocument\b|\bcontract\b|\bmemo\b|\bletter\b", re.I)


def wants_revisions(text: str) -> bool:
    """Word tracked changes or comments: edit_office_file writes real revisions (w:ins / w:del wrapping runs) and
    comments. Office-7 on 59e49c8b: docx_tracked written by hand put <w:ins/> inside run properties, which Word does
    not accept or reject as a change; with edit_office_file's tracked_replace it passed 2/2 on 38f57ffb."""
    t = str(text or "").split(SEP)[0]
    return bool(_REVISIONS.search(t) or (_COMMENTS.search(t) and _WORD.search(t)))   # not "add a comment column"


SHEET_SOURCES = (".xlsx", ".xlsm", ".xls", ".csv")
_SHEET_NAME = re.compile(r"(?<![\w.()-])[.()-]*(\w[\w.()-]*\.(?:xlsx|xlsm|xls|csv))\b", re.I)   # linear, as _FILE
# A name the request gives to the output, not a source: "save it as x.xlsx", "into x.xlsx", "output x.xlsx".
_OUT_CUE = re.compile(r"\b(?:as|into|output(?:\s+(?:to|it\s+(?:to|as)))?|named|called)\s*[\"'`]?\s*$", re.I)


def _named_sources(request: str, files=(), workdir=None) -> list[str]:
    """Workbook or CSV names in the request that are real inputs: a file handed in (`files`) or one that exists under
    workdir, and not the output's name ("save it as apple_valuation.xlsx", "into sorted.xlsx")."""
    t = str(request or "").split(SEP)[0]
    given = {Path(str(f)).name.lower() for f in files or []}
    out = []
    for m in _SHEET_NAME.finditer(t):
        if _OUT_CUE.search(t[max(0, m.start(1) - 40):m.start(1)]):
            continue
        name = m.group(1)
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


_NAMED_SHEET = re.compile(r"\b(?:sheet|tab|worksheet)s?\s+(?:called\s+|named\s+|titled\s+)?[\"'`‘“][^\"'`’”\n]{1,31}"
                          r"[\"'`’”]|[\"'`‘“][^\"'`’”\n]{1,31}[\"'`’”]\s+(?:sheet|tab|worksheet)\b|"
                          r"\b(?:sheet|tab|worksheet)\s+(?:called|named|titled)\s+\w", re.I)

# A list, a table or a simple summary: one sheet with the data first. A forecast, a budget with assumptions or
# scenarios is a model and keeps the model structure (Cover, Inputs, Calc, Outputs, Checks). Maya's Flowers: a price
# list for 12 bouquets came back with those five tabs, a formula legend and range columns, opening on the Cover.
_MODEL_ASK = re.compile(r"\b(?:models?|forecasts?|projections?|budgets?|scenarios?|sensitivit(?:y|ies)|assumptions?|"
                        r"what[- ]if|dcf|lbo|valuations?|comps|precedents?|three[- ]statements?|3[- ]statements?|"
                        r"p&l|profit and loss|income statements?|balance sheets?|cash ?flows?|npv|irr|break[- ]?even|"
                        r"amorti[sz]ation|loan|mortgage|pricing model|unit economics|cap table|waterfall|"
                        r"financials?)\b", re.I)
_TABLE_ASK = re.compile(r"\b(?:lists?|listing|tables?|price ?lists?|menus?|catalogu?es?|inventor(?:y|ies)|rosters?|"
                        r"directory|directories|registers?|logs?|trackers?|checklists?|contacts?|schedules?|rotas?|"
                        r"timetables?|summar(?:y|ies)|totals?|one row (?:per|for each)|each row|columns?)\b", re.I)


def table_job(request: str) -> bool:
    """The request asks for a list, a table or a simple summary, not a model: the sheets engine builds one sheet with
    the data first and at most a totals row.

    table_job("make a price list for 12 bouquets with name, size and price")   -> True
    table_job("a staff contact list with name, phone and email")                -> True
    table_job("Build a 12-month budget for a coffee shop with assumptions")     -> False (a model)
    table_job("a 3-year revenue forecast with scenarios")                       -> False
    """
    t = str(request or "").split(SEP)[0]
    return bool(_TABLE_ASK.search(t)) and not _MODEL_ASK.search(t)


def asks_for(request: str) -> set[str]:
    """What an edit request asks for outright that an edit otherwise never does: a "cover" or "checks" tab, a "title"
    row above the header, or to "rename" sheets."""
    t = str(request or "").lower()
    out = set()
    # A sheet the request names ("one sheet 'Summary'", "a tab called Totals") is a rename it asks for. priya018:
    # "a summary workbook ... One sheet 'Summary'" was checked as an edit of rooms.xlsx, the checker refused the new
    # name, and the sheet stayed "Rooms".
    if re.search(r"\brenam", t) or _NAMED_SHEET.search(str(request or "")):
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


# The office file a request is about: Word, Excel, PowerPoint or PDF. A CSV counts only when the request also asks for
# an office file ("turn sales.csv into a workbook"): a CSV on its own is data work any agent does.
_OFFICE_NAME = re.compile(r"(?<![\w.()-])[.()-]*\w[\w.()-]*\.(?:xlsx|xlsm|xls|docx?|pptx?|pdf|od[tsp])\b", re.I)
_CODE_NAME = re.compile(r"(?<![\w.()-])[.()-]*\w[\w.()-]*\.(?:py|js|mjs|ts|tsx|jsx|rb|go|rs|java|kt|cs|cpp|cc|c|h|"
                        r"php|sh|ps1|sql|ipynb)\b", re.I)
_CODE_ASK = re.compile(r"\b(?:bug|stack ?trace|traceback|exception|unit tests?|pytest|refactor|function|class|module|"
                       r"script|repo(?:sitory)?|endpoint|api|library|parser|compile)\b", re.I)
_OFFICE_SUFFIX = (".xlsx", ".xlsm", ".xls", ".docx", ".doc", ".pptx", ".ppt", ".pdf")
# A change to a deck, memo or workbook asked in words (Daniel, round 3: "Add two slides to the same deck ... Add short
# speaker notes to every slide too"): a doing verb a few words before an office deliverable.
_OFFICE_ASK = re.compile(r"\b(?:make|create|build|add|edit|update|change|write|draft|prepare|turn|convert|export|"
                         r"shorten|extend|insert|remove|delete|rewrite|redo|fix|restyle|rebrand|summari[sz]e)\b"
                         r"(?:\W+[\w&'-]+){0,8}?\W+(?:slides?|decks?|presentations?|speaker notes|memos?|"
                         r"spreadsheets?|workbooks?|word documents?|power ?points?|excel)\b", re.I)


def office_work(text: str, files=()) -> bool:
    """The request's main input or output is an office file (xlsx, xls, docx, pptx, pdf, or a CSV made into one of
    them): meshpool Work is the engine for it. A request about code that only mentions such a file is not.

    office_work("Take rooms.xlsx ... make rooms-summary.xlsx with the areas and a total")   -> True
    office_work("Make a 10-slide pitch deck for investors")                                   -> True
    office_work("Turn sales.csv into a workbook with a chart")                                -> True
    office_work("clean this csv and chart revenue")                                           -> False (no office file)
    office_work("fix the bug in export.py that writes report.xlsx")                           -> False (code)
    """
    t = str(text or "").split(SEP)[0]
    if not t.strip():
        return False
    office_file = bool(_OFFICE_NAME.search(t)) or any(
        Path(str(f)).suffix.lower() in _OFFICE_SUFFIX for f in files or [])
    if _CODE_NAME.search(t) or (_CODE_ASK.search(t) and not office_file):
        return False
    if office_file or _OFFICE_ASK.search(t):
        return True
    try:
        from meshpool_harness import capabilities as CAP
        # a file type named outright ("the PDF", "an Excel file", "a PowerPoint"), or one asked for
        return bool(CAP.needs(t)) or any(rx is not None and rx.search(t) for rx in CAP._STRONG_RX.values()) or (
            bool(_SHEET_NAME.search(t)) and bool(re.search(r"\b(?:workbooks?|spreadsheets?|excel)\b", t, re.I)))
    except Exception:  # noqa: BLE001
        return False
