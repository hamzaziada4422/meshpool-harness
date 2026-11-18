"""The harness family: one spec per specialised harness.

A spec adds to a base agent (Meshpool Code or Work) rather than replacing it, so every harness keeps the shared
tools, permissions, memory and checks. What a spec changes is small and measurable:

  addon     a few lines of method for that kind of task, appended to the system prompt once, at the start of the
            chat (the prompt stays byte-stable afterwards, so the provider's prompt cache keeps hitting)
  tools     extra tools that kind of task needs (diagnostics for code)
  base      which built-in agent profile runs it: "code" or "work"
  backend   which agent family Auto scores it under in routing.json (code, terminal, browser, research, documents,
            data, gtm, general)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Spec:
    id: str
    name: str
    base: str                    # "code" | "work"
    backend: str                 # category in defaults/routing.json
    addon: str = ""
    tools: tuple = field(default_factory=tuple)
    drop: tuple = field(default_factory=tuple)     # tools this kind of task does not need: their schemas cost
                                                   # tokens on every call (MH_LEAN_TOOLS=0 keeps them)


# Rarely useful when fixing, testing, refactoring, reviewing or explaining code in a project, and about 1,100 tokens of
# schema on every call (sub-agents alone are 1,462 characters). fetch_url stays for reading docs.
LEAN_CODE = ("delegate_task", "ask_user", "web_search", "web_research", "find_skill")
# Focused fixes, tests, reviews and explanations also skip the todo list: on these tasks it cost an extra call or two
# per task (writing the list, the follow-through nudge) against Pi, which has none. Build and refactor keep it.
LEAN_FOCUSED = LEAN_CODE + ("todo_write",)

ANSWER_FIRST = ("Lead with the answer or the result. No preamble, no restating the question, no closing summary of "
                "what you just said.")

SPECS = {s.id: s for s in [
    Spec("chat", "Chat", "work", "general",
         "Conversation mode: most messages here are questions, explanations, rewrites or quick lookups. Answer "
         "directly from what you know when you can; use a tool only when the answer needs a file, a current fact "
         "or an action. Short questions get short answers (one to four sentences, or a tight list). " + ANSWER_FIRST),

    Spec("code.debug", "Debug", "code", "code",
         "Debugging: match the effort to the bug. If the error and the code it points to make the cause clear, fix "
         "it now and run the failing command once to confirm. Otherwise:\n"
         "1. Reproduce: run the failing command or test (or the smallest script that shows the bug). If a "
         "<reproduction> block is in the request, that run is done: start from its output.\n"
         "2. Locate: read the traceback from the deepest project frame; test one hypothesis at a time with a command, "
         "not by rereading code. diagnostics finds syntax and type errors across files.\n"
         "3. Fix the cause with the smallest edit. Never change a test to make it pass.\n"
         "4. Verify: rerun the reproduction and the nearest tests. For a regression, `git log -p` or "
         "`git bisect run <check>` finds the change that broke it.\n"
         "Edits to code files come back with a syntax and bug check; fix what it reports first. Reply in two or "
         "three lines: the cause (file:line), the fix, and the command that now passes.",
         ("diagnostics",), LEAN_FOCUSED),
    Spec("code.build", "Build", "code", "code",
         "Building: find the closest existing pattern in the project and follow it. Make the change in as few edits "
         "as it needs (edits come back with a syntax and bug check), then run the relevant tests or the program "
         "once. Do not add dependencies, files or abstractions the task does not need. Reply in a few lines: what "
         "you built and the command that shows it works.", ("diagnostics",)),
    Spec("code.refactor", "Refactor", "code", "code",
         "Refactoring method: behaviour must not change. Run the tests before you start and after each step. For "
         "mechanical changes across many files (renames, API migrations) prefer one scripted change (sed, a short "
         "Python script, or ast-grep/codemod if installed) over many hand edits, then review the diff. Keep each "
         "step small and green.", ("diagnostics",), LEAN_CODE),
    Spec("code.review", "Review", "code", "code",
         "Review mode: read the diff (`git diff`, `git diff main...HEAD` or the PR) and the code around it. Do not "
         "edit files unless asked. Report only real problems: bugs, security issues, missing error handling, broken "
         "edge cases, missing tests, in order of severity, each as `file:line: problem -> suggested fix`. Skip "
         "style nits a formatter would fix. If nothing is wrong, say so in one line.", ("diagnostics",), LEAN_FOCUSED),
    Spec("code.test", "Tests", "code", "code",
         "Testing method: find the project's test framework and an existing test file to copy the style from. Test "
         "behaviour through the public interface, including edge cases and the error path. Run the new tests and "
         "show they pass; when fixing a bug, show the test fails without the fix.", ("diagnostics",), LEAN_FOCUSED),
    Spec("code.terminal", "Terminal", "code", "terminal",
         "Terminal work: check the environment before changing it (versions, what is installed, existing config). "
         "Batch independent checks in one bash_many call. Prefer reversible steps and say what you changed outside "
         "the project folder. When a command fails, read its error before retrying a variant."),
    Spec("code.explain", "Explain", "code", "code",
         "Explaining: read only the code needed to answer. Answer the question asked, pointing at file:line, in a "
         "few short paragraphs or a list. Do not edit files. " + ANSWER_FIRST, (), LEAN_FOCUSED),

    Spec("research", "Research", "work", "research",
         "Research: plan two to five varied queries, run them in one web_research call, read the key primary sources "
         "in full, and cite every fact with its link. Prefer primary sources (filings, official pages, papers) over "
         "aggregators. Report what you could not verify. Stop when each claim you need has a source."),
    Spec("data", "Data", "work", "data",
         "Data analysis: look at the schema and a sample before analysing. Compute with code (pandas, or DuckDB SQL "
         "for large files) and quote only numbers your code printed. State the method in one line, then the findings "
         "with the numbers; a chart only when it helps."),
    Spec("docs", "Documents", "work", "documents", ""),
    Spec("browser", "Browser", "work", "browser",
         "Web task: use fetch_url for public pages; use the browser only for sign-ins, clicks and forms. Confirm with "
         "the user before anything irreversible (submit, pay, send, delete)."),
    Spec("automation", "Automation", "work", "general",
         "Automation: if the user wants this to repeat or react to something, set it up as a scheduled task (the "
         "meshpool_assistant tools) after confirming the schedule, then run it once now to show the result."),
    Spec("general", "General", "work", "general", ""),
]}


def get(sid: str) -> Spec:
    return SPECS.get(sid) or SPECS["general"]


# ---------- sub-subtask methods: added to the sub-task's method when the router detects the variant ------------------
VARIANT_ADDON = {
    "code.debug.syntax": "This is a syntax problem: the cause is at or just before the reported line (a missing ':', "
                         "bracket, quote, comma or indent). Fix it there, run the program or diagnostics once, done.",
    "code.debug.test": "A test fails: read its assertion and the code under test. The test states the intended "
                       "behaviour; never change the test or loosen its assertion.",
    "code.debug.exception": "A runtime error: start at the deepest project frame. Check what the data really looks like "
                            "there (print or a one-line probe) before changing code.",
    "code.debug.regression": "It used to work: find what changed before reading code. `git log --oneline -15`, then "
                             "`git diff <commit>~1 <commit> -- <file>` on the likely commit (or `git bisect run "
                             "<check>`). Restore the intended behaviour at the change, do not patch around it.",
    "code.debug.perf": "Too slow: time the slow call on realistic input first, find the hot spot (nested loops over the "
                       "data, repeated work, N+1 calls), replace it with a dict/set index, sorting once or batching, "
                       "keep the output identical, then time it again and report both numbers.",
    "code.build.cli": "A command-line change: follow the existing argument parsing and output style, keep current "
                      "outputs unchanged, and run two or three real invocations (including the default) to check.",
    "code.build.api": "An API change: follow the existing handlers and response shapes, validate inputs at the "
                      "boundary with clear errors, and call the function or endpoint with a normal case, an edge case "
                      "and a bad input.",
    "code.build.ui": "A UI change: follow the existing components and styles; keep markup accessible (labels, focus, "
                     "keyboard).",
    "code.build.function": "A new function or class: match the module's style, handle the edge cases the request "
                           "names (empty, invalid, boundary), and run a few calls that show it works.",
}


# ---------- data-driven overrides: the harness optimizer (testkit/loop/optimize.py) edits these, not the code --------
def overrides() -> dict:
    """{spec or spec.variant or spec.variant.language: {"addon": str, "addon_extra": str, "drop": [...],
    "tools": [...]}} from app/defaults/specs.json, then <data folder>/specs.json, then MH_SPECS_FILE (later wins)."""
    import json
    import os
    from pathlib import Path
    from meshpool_harness import config as C
    out: dict = {}
    for p in (C.APP / "defaults" / "specs.json", C.home() / "specs.json", os.environ.get("MH_SPECS_FILE") or ""):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8")) if p else {}
        except (OSError, ValueError):
            continue
        for k, v in (d.get("specs") or {}).items():
            if isinstance(v, dict):
                out.setdefault(k, {}).update(v)
    return out
