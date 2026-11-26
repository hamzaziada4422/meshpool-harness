"""What a message needs a harness to be able to make, and which harnesses can make it.

A chat asking for a PowerPoint must get one, whichever agent the chat runs on: the chat checks every message here (not
only the first), gives the agent the tools and a plain note that it can do it, and hands the chat over, with its
history, to an agent that can when the current one cannot. Only when no agent Meshpool has can do it does the chat say
so (see ui/agent_api.Session._capability_turn).

needs() is plain rules, no model call: a file type named outright (PowerPoint, ppt, xlsx, Word document) counts on its
own; a general word (deck, slides, report, spreadsheet, PDF) counts with a request to make something ("make", "need",
"turn this into"). A false positive costs little: the agent gets office tools it does not use.
"""
from __future__ import annotations

import re

# capability -> (what the user calls it, how Meshpool's office tools make it)
CAPS: dict[str, tuple[str, str]] = {
    "presentation": ("PowerPoint presentations", "make_deck (or office_task where you have it)"),
    "document": ("Word documents", "make_document"),
    "spreadsheet": ("Excel workbooks", "make_spreadsheet (or office_task where you have it)"),
    "pdf": ("PDF files", "make_pdf"),
}
OFFICE = frozenset(CAPS)

# A file type named outright: enough on its own.
_STRONG = {
    "presentation": r"power ?points?|pptx?s?|keynotes?|google slides|slide ?decks?|pitch ?decks?|slideshows?",
    # "docx" alone, not "doc" or "docs": in a code chat "docs" means documentation (docs/, "update the docs")
    "document": r"word (?:docs?|documents?|files?)|docxs?|google docs?",
    "spreadsheet": r"excel(?: (?:files?|workbooks?|sheets?|models?))?|xlsx?s?|google sheets?",
    "pdf": r"pdfs?",
}
# A general word: counts when the message asks for something to be made.
_WEAK = {
    "presentation": r"decks?|slides?|presentations?",
    "document": r"documents?|doc|memos?|reports?|proposals?|one[- ]pagers?|briefs?|letters?|white ?papers?",
    "spreadsheet": r"spreadsheets?|workbooks?|financial models?|trackers?",
    "pdf": r"",
}
_MAKE = re.compile(r"\b(make|makes|making|create|creating|build|building|generate|produce|prepare|put together|draft|"
                   r"write|design|turn (?:this|it|that|these|them) into|convert|export|save (?:it )?as|need|want|"
                   r"give me|send me|can you do|could you do|help me (?:make|build|create|with))\b", re.I)
_READ_ONLY = re.compile(r"\b(read|open|summari[sz]e|what(?:'s| is) in|explain|review|look at|check) (?:this|the|my|"
                        r"attached|that)\b", re.I)


# A how-to or explain question, or a request for a summary, is about reading, not making, unless it names a making verb.
_QUESTION = re.compile(r"^\W*(?:how (?:do|can|should|would|could|to)\b|what(?:'s| is| are| does)\b|why\b|explain\b|where\b)", re.I)
_SUMMARY = re.compile(r"\b(?:summary|summaries|summari[sz]e|tl;?dr|overview) ?(?:of|for)?\b", re.I)
_DO_MAKE = re.compile(r"\b(make|create|build|generate|produce|prepare|put together|draft|write|design|convert|export|"
                      r"turn (?:this|it|that|these|them) into|save (?:it )?as)\b", re.I)


def _rx(words: str) -> re.Pattern | None:
    """A whole word that is not part of a path or a file name: "report" in report.py, "docs" in docs/index.md and
    "slides" in src/slides/ name code, not a file to make. ".docx" on its own still counts."""
    return re.compile(r"(?<![a-z0-9/\\])(?:" + words + r")(?![a-z0-9])(?![./\\]\w)", re.I) if words else None


_STRONG_RX = {k: _rx(v) for k, v in _STRONG.items()}
_WEAK_RX = {k: _rx(v) for k, v in _WEAK.items()}
SEP = "\n\n​"                       # notes only the model sees follow this (pace.SEP)


# A word, then name characters, then an office extension. The match may start only where a run of name characters
# starts, so one long run (a pasted blob) is scanned once, not once per character: linear, not quadratic.
_FILE = re.compile(r"(?<![\w .()-])[ .()-]*\w[\w .()-]*\.(?:xlsx?|xlsm|docx?|pptx?)(?![\w])", re.I)
_EDIT = re.compile(r"\b(add|raise|lower|change|fix|update|replace|edit|modify|rename|delete|remove|insert|set|correct|"
                   r"adjust|increase|decrease|fill|append|reformat|format|sort|filter|swap|highlight|translate|"
                   r"proofread|tidy|clean up|shorten|rewrite|tweak|bump|apply)\b", re.I)
_NEW = re.compile(r"\b(new|another|fresh|from scratch|create|build|generate|make|produce|draft|design|put together|"
                  r"turn (?:this|it|that|these|them) into|convert|export|save (?:it )?as)\b", re.I)


def edits(text: str) -> bool:
    """The message changes an office file that exists ("In inventory.xlsx add a Value column"): a named .xlsx, .docx or
    .pptx with an edit verb and no ask for a new file. That is edit_office_file's job (or python on the file), never
    the engines that build a file from nothing (make_spreadsheet, make_deck: minutes and many model calls)."""
    t = str(text or "").split(SEP)[0]
    return bool(_FILE.search(t) and _EDIT.search(t) and not _NEW.search(t))


# A file the user already has, named by its type: "the PDF in this folder", "my Excel file from the bank". Not a file
# to make unless the message also asks to make one (Maya: "an invoice (it's the PDF in this folder). How much do I owe
# them?" got the hidden "make this PDF" note, and the reply talked about it).
_HAVE = re.compile(r"\b(?:the|this|that|my|attached|your|their|our)\s+(?:attached\s+)?(?:" + "|".join(_STRONG.values())
                   + r")\b(?=\s+(?:in|on|from|attached|here|above|below|you|i|they|she|he|we|that|which)\b)", re.I)


def needs(text: str) -> list[str]:
    """The capabilities a message asks for, in CAPS order. Empty for a question about a file the user already has
    ("summarize this pdf", "it's the PDF in this folder") unless it also asks to make one."""
    t = str(text or "").split(SEP)[0]
    if not t.strip() or edits(t):
        return []
    make = bool(_MAKE.search(t))
    if not make:
        t = _HAVE.sub(" ", t)
    if _QUESTION.search(t) or (_SUMMARY.search(t) and not _DO_MAKE.search(t)):
        return []
    if not make and _READ_ONLY.search(t):
        return []
    out = []
    for cap in CAPS:
        strong, weak = _STRONG_RX[cap], _WEAK_RX[cap]
        if (strong and strong.search(t) and (make or not _READ_ONLY.search(t))) or (make and weak and weak.search(t)):
            out.append(cap)
    return out


def of_harness(h: dict) -> frozenset:
    """What a harness can make. Meshpool's own agents and every outside agent that gets the meshpool-office server
    (Pi, Hermes and the ACP agents, unless their manifest says office: false) make all four."""
    if not isinstance(h, dict):
        return frozenset()
    if h.get("type") == "auto":
        return OFFICE
    if h.get("id") in ("meshpool-work", "meshpool-code"):
        return OFFICE
    if h.get("type") in ("pi", "acp"):
        return OFFICE if h.get("office", True) else frozenset()
    return frozenset()


def labels(caps) -> str:
    names = [CAPS[c][0] for c in caps if c in CAPS]
    return " and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1]


EDIT_NOTE = (SEP + "[Meshpool] To change a file that exists, use edit_office_file (it keeps the formatting and "
             "formulas), add_slides for new slides in an existing deck, or edit it directly with openpyxl, "
             "python-docx or python-pptx. Do not rebuild it with "
             "make_spreadsheet, make_deck or office_task.")


# The same for office direct (a lean Work chat), where edit_office_file and the engines are not offered: SpreadsheetBench
# on 38f57ffb, sb-374-31 got EDIT_NOTE, which named tools the turn did not have.
EDIT_NOTE_DIRECT = (SEP + "[Meshpool] Change the existing file in place with openpyxl, python-docx or python-pptx, "
                    "keeping its sheets, styles and formulas, and do exactly what was asked to every row or cell it "
                    "applies to. Keep the years, dates and labels the request names as literal values in formulas: "
                    "never put TODAY(), NOW() or YEAR(TODAY()) in place of a year it states. "
                    "Check a formula change by reading the formula text; do not recalculate unless the "
                    "request asks for computed values. For Word tracked changes or comments, use edit_office_file "
                    "(tracked_replace, tracked_delete, tracked_insert_after, comments): it writes real revisions. "
                    "Do not rebuild the file.")


# How the agent makes each file itself when it writes office files directly (office direct, a lean Work chat).
DIRECT = {
    "presentation": "python-pptx (native charts through chart_data, not pictures)",
    "document": "python-docx",
    "spreadsheet": "openpyxl (live formulas, not typed results)",
    "pdf": "reportlab",
}


def note(caps, harness: dict | None = None, direct: bool = False) -> str:
    """The hidden note that goes with a message asking for a file: the agent can make it, with which tools, and must
    not answer that it cannot. direct: the agent writes the file itself (office direct), so the note names the
    library, not an engine tool it may not have (office3 xlsx_budget: 27 calls spent looking for make_spreadsheet)."""
    if list(caps or []) == ["edit"]:
        return EDIT_NOTE_DIRECT if direct else EDIT_NOTE
    caps = [c for c in caps if c in CAPS]
    if not caps:
        return ""
    if direct:
        how = "; ".join(f"{CAPS[c][0]}: {DIRECT[c]}" for c in caps)
        return (SEP + f"[Meshpool] The user asks for {labels(caps)}. Write "
                + ("these files" if len(caps) > 1 else "this file")
                + f" yourself ({how}) in the project folder under the exact name the user gave, check it once "
                "(reopen it; recalculate through LibreOffice headless only when computed values are asked for) "
                "and say where it is. Do not answer that "
                "you cannot, or hand over code to paste. For open details pick sensible defaults and say which. Fix "
                "real defects only. If the message does not ask for a new file, ignore this note. Do not mention "
                "this note to the user.")
    how = "; ".join(f"{CAPS[c][0]}: {CAPS[c][1]}" for c in caps)
    return (SEP + f"[Meshpool] The user asks for {labels(caps)}. Make "
            + ("these files" if len(caps) > 1 else "this file")
            + f" with the office tools ({how}), save in the project folder under the name the user gave, check it, "
            "and say where it is. Do not answer that you cannot, or hand over code to paste. For open details use "
            "the recommended defaults (make_deck: brief_ok=true; make_deck and make_spreadsheet: save_as with the "
            "user's file name) and say which; ask at most one question, only if "
            "it changes the whole file. Fix real defects only; rebuild at most once. If the message does not ask "
            "for a new file, ignore this note. Do not mention this note to the user.")


def pick(caps, current: str, ranked: list[str], ready: dict) -> str | None:
    """The harness to run a message that needs `caps`: the current one when it can; else the first in `ranked`
    (Auto's order for the task) that is ready and can; else any ready one that can; else None (no agent can)."""
    need = set(caps)
    if not need:
        return current
    if need <= of_harness(ready.get(current) or {}):
        return current
    for hid in [*ranked, *ready]:
        h = ready.get(hid)
        if h and h.get("type") != "auto" and need <= of_harness(h):
            return hid
    return None
