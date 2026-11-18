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


ANSWER_FIRST = ("Lead with the answer or the result. No preamble, no restating the question, no closing summary of "
                "what you just said.")

SPECS = {s.id: s for s in [
    Spec("chat", "Chat", "work", "general",
         "Conversation mode: most messages here are questions, explanations, rewrites or quick lookups. Answer "
         "directly from what you know when you can; use a tool only when the answer needs a file, a current fact "
         "or an action. Short questions get short answers (one to four sentences, or a tight list). " + ANSWER_FIRST),

    Spec("code.debug", "Debug", "code", "code",
         "Debugging method:\n"
         "1. Reproduce first: run the failing command, test or snippet the user gave (or write the smallest script "
         "that shows the bug) before reading much code. If it does not fail, say so and ask for the missing step.\n"
         "2. Locate: run diagnostics for syntax and type errors; read the traceback from the deepest project frame. "
         "Form one hypothesis at a time and test it with a command, not by rereading code.\n"
         "3. Fix the cause with the smallest edit, not the symptom. Do not change tests to make them pass.\n"
         "4. Verify: rerun the reproduction and the nearest existing tests. For a regression, `git log -p` or "
         "`git bisect run <check>` finds the change that broke it.\n"
         "Reply with: the cause (one or two sentences, file:line), the fix, and the command that now passes.",
         ("diagnostics",)),
    Spec("code.build", "Build", "code", "code",
         "Building method: find the closest existing pattern in the project (grep for a similar feature) and follow "
         "it. Make the change in small edits, run diagnostics after each file, then the relevant tests or the "
         "program itself. Do not add dependencies, files or abstractions the task does not need. Reply with what "
         "you built and how you checked it, in a few lines.", ("diagnostics",)),
    Spec("code.refactor", "Refactor", "code", "code",
         "Refactoring method: behaviour must not change. Run the tests before you start and after each step. For "
         "mechanical changes across many files (renames, API migrations) prefer one scripted change (sed, a short "
         "Python script, or ast-grep/codemod if installed) over many hand edits, then review the diff. Keep each "
         "step small and green.", ("diagnostics",)),
    Spec("code.review", "Review", "code", "code",
         "Review mode: read the diff (`git diff`, `git diff main...HEAD` or the PR) and the code around it. Do not "
         "edit files unless asked. Report only real problems: bugs, security issues, missing error handling, broken "
         "edge cases, missing tests, in order of severity, each as `file:line: problem -> suggested fix`. Skip "
         "style nits a formatter would fix. If nothing is wrong, say so in one line.", ("diagnostics",)),
    Spec("code.test", "Tests", "code", "code",
         "Testing method: find the project's test framework and an existing test file to copy the style from. Test "
         "behaviour through the public interface, including edge cases and the error path. Run the new tests and "
         "show they pass; when fixing a bug, show the test fails without the fix.", ("diagnostics",)),
    Spec("code.terminal", "Terminal", "code", "terminal",
         "Terminal work: check the environment before changing it (versions, what is installed, existing config). "
         "Batch independent checks in one bash_many call. Prefer reversible steps and say what you changed outside "
         "the project folder. When a command fails, read its error before retrying a variant."),
    Spec("code.explain", "Explain", "code", "code",
         "Explaining: read only the code needed to answer. Answer the question asked, pointing at file:line, in a "
         "few short paragraphs or a list. Do not edit files. " + ANSWER_FIRST),

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
