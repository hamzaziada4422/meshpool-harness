"""Independent grader: a second pair of eyes before the agent reports back on a big task.

When the agent is about to give its final answer for a large task, a separate model loop (fresh context, its own system
prompt, read-only tools) writes down what the user expects, checks the result against that list with its own eyes, and
returns a strict JSON verdict. If something is not done, the findings go back to the agent as a message and it fixes
them, up to two rework rounds. If the grader itself fails, nothing is blocked: the reply says plainly that the check
could not run. The final reply never claims success the grader did not confirm.

Goal mode (agent/goal.py) uses the same read-only agent twice: draft_rubric() writes the goal's criteria before turn 1,
and run(rubric=...) scores the work against that frozen rubric (its own expectations are not written at grading time).

This module holds the pure parts (when to run, what the grader sees, reading its JSON, the report text) and the
read-only grader agent. The hook that calls it is Agent._grade_gate in agent/core.py.

Turn it off: MH_GRADER=0, or the Settings switch "Independent check on big tasks". Limits: MH_GRADER_STEPS (15),
MH_GRADER_SECONDS (240), MH_GRADER_TOKENS (150000), MH_GRADER_ROUNDS (2 rework rounds), MH_GRADER_MODEL (else
MH_MODEL_FAST, else the chat's model).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
import threading
import time
from pathlib import Path

ROUNDS = 2
STEPS = 15
SECONDS = 240
TOKENS = 150000
BIG_STEPS = 12
LONG_ANSWER = 1800            # characters: a long written answer after some work
RESEARCH_CALLS = 4
_BUILD = re.compile(r"\b(build|make|create|write|generate|design|implement|produce|draft|set up|develop|prepare|"
                    r"put together|compile|convert|export|add)\b", re.I)
_IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".meshpool", ".run", ".venv", "venv", ".mypy_cache",
                ".ruff_cache", ".next", "dist", "build", ".tox", ".idea", ".vscode"}
_SEARCH_TOOLS = ("web_search", "web_research", "fetch_url", "web_fetch")
# what the grader may call besides read_file, grep and glob: tools that only look (the name after the server prefix)
MCP_LOOK = {"read_document", "view_output", "ui_audit", "ui_smoke", "check_deck", "check_workbook", "check_document",
            "measure_office_file", "web_fetch", "web_search", "slide_layouts", "office_job_status", "current_time",
            "calculate"}
_TEST_CMD = re.compile(
    r"^(?:cd\s+\S+\s*&&\s*)?(?:(?:python3?|py)\s+-m\s+(?:pytest|unittest)|pytest|py\.test|npm\s+(?:run\s+)?test|"
    r"npm\s+t|yarn\s+test|pnpm\s+(?:run\s+)?test|go\s+test|cargo\s+test|node\s+--test|make\s+test|mvn\s+(?:-q\s+)?test|"
    r"gradle\s+test|dotnet\s+test|ruby\s+-Itest|rspec|bundle\s+exec\s+rspec|php\s+artisan\s+test|phpunit)\b[^;&|`$<>]*$")

SYSTEM = """You are the INDEPENDENT REVIEWER of Meshpool. Another agent has finished a task for a user and is about to report back. You were not part of that work. You have no stake in it. Your job: decide whether this user would accept the result as what they wanted, and say exactly what is missing if not.

You see only: the user's request, the agent's plan with statuses, the agent's final answer, the files it changed, and the commands it ran with exit codes. Treat the agent's own claims as claims, never as proof. Check for yourself with your tools.

Method:
1. Write down what the user expects, from their own words and any files or context they gave: the explicit asks; the implied ones (audience, format, file type and name, length, tone, language, numbers that must be right); what they said not to do; what "done" looks like to them.
2. Check each item against the real result. Open the files. Run the project's own checks or tests when there are any. For a web page or visual file, look at it (ui_audit, view_output). For Word, Excel, PowerPoint and PDF files use read_document, and check_deck, check_workbook, check_document or measure_office_file when you have them. For a research answer, open the sources it cites and check that each claim is supported. Recompute numbers that matter.
2b. For anything the agent BUILT as code (a web page or app, a game, a dashboard, a script, a CLI, an API), judge it as the user who will run it. Decide what a person expects such a thing to do (the features every version of it has, not only the words of the request), then confirm each one actually works, from the click-through and test evidence below and your own checks: no button or link that does nothing; each feature usable end to end; empty, loading and error states; bad input rejected with a message that says what is wrong; data kept where the user would expect it (a reload must not lose it); keyboard use and a phone-width layout for anything with a screen; no placeholder text or invented facts; tests exist for logic that can break and pass when run; a way to run it is stated (README or the reply). A "MUST FIX" line in the automatic click-through is a real defect: list it under blockers unless you can show it is wrong.
3. Flag: invented facts, unsupported numbers, claims without a source, instructions that were ignored, the wrong language, the wrong file type or name, anything the user asked for that is missing, plan items marked completed that are not.
4. Missing evidence counts as NOT met. If you cannot see it or prove it, it is not_met. Use "unverified" only when your tools truly cannot check it (say why in evidence).
4b. SCOPE (0.0.13): hold the agent to what was asked, no more. A result that fits in the reply (a short plan, a table, post copy, a reply email, a summary) does not need a file unless the user asked for one: do not mark a missing file, plan document, tracker or extra format as not_met. Never ask for extra deliverables, deeper research or more versions than the request called for. Do not ask the agent to fill a gap with an invented fact; a fact that is not known is left out or flagged in the reply.
5. Judge overall: would this person accept it as it is, or send it back?

E2E-AUTONOMY: an effect in a connected app or live system (a booking, a sent message, a status change, a record) that the log shows the tool accepted, and that no read-only tool can read back, is "unverified", not "not_met". Never ask the agent to create, send, book, update or delete anything just to prove something; ask only for read-only evidence.

READ-BACK RULE (0.0.13): when you cannot read an effect back (no read tool covers it, the tool only answers "ok", the app is not reachable from your tools), the item is "unverified", with the reason in its evidence. It is never "not_met", never a blocker, and never a reason to send the agent back. Do not write a requirement such as "confirm by reading it back" if you have no way to do that yourself. The section "Actions the agent made in connected apps" lists writes that the tool accepted: they happened once and are done. Your "fix" and "blockers" must never tell the agent to run, call, send, book, create or submit anything again, to repeat or retry a write, or to redo it as a test. A fix is only for something the agent can correct by editing its own files or by saying plainly in its reply what could not be checked.

Rules: you may only read and run checks. You must not create, change or delete any file, install anything, or send anything anywhere. Do not run commands that write. Be efficient: a few targeted looks are better than reading everything. You have a small step budget.

Finish with ONE JSON object and nothing else (no code fence), in plain words, short:
{"expectations": ["what the user expects, one short line each"],
 "verdict": "pass" | "partial" | "fail",
 "acceptance": "accept" | "accept_with_notes" | "would_send_back",
 "acceptance_reasons": ["the one or two reasons that matter most to the user"],
 "items": [{"requirement": "...", "status": "met" | "not_met" | "unverified", "evidence": "what you saw, with a file name or command"}],
 "blockers": ["things that must be fixed before this can be accepted"],
 "fix": ["one clear instruction per problem, for the agent to follow"]}
"pass" means every requirement is met and you would accept it. "partial" means it is useful but something is open. "fail" means the main ask is not delivered."""

CHECK = ("The agent says it is finished. Do not take its word for it: grade it now. Look at the real result, then reply "
         "with the JSON object only.")
REPAIR = "Reply again with only the JSON object described in your instructions. No other text, no code fence."
INJECT = ("[Independent review] A reviewer who did not do this work checked it against what you were asked. These are "
          "not done:\n{items}\n\nFix them, check your fixes yourself by looking only (read files, run tests, call read-only tools; never create, send, book, update or delete anything in a live system just to test it, and never run again a connected-app call that already succeeded this turn: it is done once), then finish with your answer. Do not say anything "
          "is done unless you checked it. If one of them cannot be done, say plainly why.")


# ---------- switch and limits -------------------------------------------------------------------------------------------
def _cfg(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    if v is None or v == "":
        try:
            from meshpool_harness import config as C
            v = C.read().get(key)
        except Exception:  # noqa: BLE001
            v = None
    return str(v) if v not in (None, "") else default


def enabled() -> bool:
    return _cfg("MH_GRADER", "1").strip().lower() not in ("0", "off", "false", "no")


def _num(key: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(_cfg(key, str(default))))))
    except (ValueError, OverflowError):
        return default


def limits() -> dict:
    return {"steps": _num("MH_GRADER_STEPS", STEPS, 2, 60), "seconds": _num("MH_GRADER_SECONDS", SECONDS, 10, 1800),
            "tokens": _num("MH_GRADER_TOKENS", TOKENS, 5000, 2000000), "rounds": _num("MH_GRADER_ROUNDS", ROUNDS, 0, 4)}


# ---------- what the turn looked like ---------------------------------------------------------------------------------------
def clean_text(c) -> str:
    """The user's own words from a message: no hidden notes, plan lines, snapshots or repo maps."""
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    t = str(c or "").split("\n\n​")[0]
    t = re.split(r"\n\n\[Meshpool\]|\n+<(?:reproduction|environment_snapshot|repo_map|repo-map)\b", t)[0]
    return t.strip()


def requests(messages: list, start: int, before: int = 2) -> list[str]:
    """The user's request(s) for the turn that began at messages[start] (plus a little earlier context)."""
    out, early = [], []
    for i, m in enumerate(messages):
        if m.get("role") != "user":
            continue
        raw = m.get("content")
        txt = clean_text(raw)
        if txt.startswith(("[Independent review]", "[Meshpool]", "[meshpool]")) or not txt:
            continue
        if txt.startswith("[Added by the user while you were working]"):
            txt = txt.replace("[Added by the user while you were working]", "(added while it worked)", 1).strip()
        (out if i >= start else early).append(txt)
    return [("Earlier in this chat: " + e[:1200]) for e in early[-before:]] + [o[:6000] for o in out]


def count_calls(messages: list, start: int) -> tuple[int, int]:
    """(tool calls, research tool calls) made by the agent since messages[start]."""
    n = r = 0
    for m in messages[start:]:
        for c in m.get("tool_calls") or []:
            n += 1
            nm = (c.get("function") or {}).get("name", "")
            if nm.split("__")[-1] in _SEARCH_TOOLS:
                r += 1
    return n, r


def files_changed(root, since: float, turn_files=(), limit: int = 60) -> list[str]:
    """Files the turn touched: what the file tools recorded, plus anything under the project newer than the turn start
    (the office tools write files from their own process)."""
    root = Path(root)
    seen: dict[str, float] = {}
    for f in turn_files or ():
        try:
            seen[os.path.relpath(f, root)] = 1.0
        except ValueError:
            seen[str(f)] = 1.0
    try:
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if not _skip_dir(dp, d)]
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    if os.stat(p).st_mtime >= since - 1 and not fn.endswith((".pyc", ".log")):
                        seen.setdefault(os.path.relpath(p, root), 1.0)
                except OSError:
                    pass
            if len(seen) > 400:
                break
    except OSError:
        pass
    return sorted(seen)[:limit]


def _skip_dir(parent: str, name: str) -> bool:
    """Folders nobody reviews: caches and vendored code, and the picture renders the viewing tools make."""
    return (name in _IGNORE_DIRS or name.endswith(".egg-info")
            or (name == "views" and os.path.basename(parent) == "Meshpool outputs"))


def large(info: dict) -> str:
    """Why this turn counts as a large task, or "" when it does not. info: todos, calls, research, final, files, request,
    after_plan, deep (see Agent._grade_info)."""
    todos = [t for t in info.get("todos") or [] if isinstance(t, dict)]
    if len(todos) >= 3 and info.get("calls", 0) >= _num("MH_GRADER_TODO_CALLS", 8, 0, 60):
        # a todo list alone is not a big job: every multi-step request is told to write one
        return f"a plan with {len(todos)} steps"
    if info.get("after_plan"):
        return "an approved plan"
    if info.get("calls", 0) >= BIG_STEPS:
        return f"{info['calls']} tool steps"
    if info.get("files") and _BUILD.search(info.get("request") or "") and info.get("calls", 0) >= 3:
        return "files made or changed for a build request"
    final = info.get("final") or ""
    if len(final) >= LONG_ANSWER and info.get("calls", 0) >= 4:
        return "a long written answer after research and work"
    if info.get("research", 0) >= RESEARCH_CALLS and len(final) >= 800:
        return "a research answer built from several sources"
    if info.get("deep") and info.get("calls", 0) >= 6:
        return "a long job"
    return ""


_WEB = (".html", ".htm")
_TEST_FILE = re.compile(r"(^|/)(tests?/|__tests__/|test_[^/]+\.py$|[^/]+_test\.(py|go)$|[^/]+\.(test|spec)\.[jt]sx?$)")


def _web_files(info: dict, limit: int = 2) -> list[str]:
    root = Path(info.get("root") or ".")
    out = []
    for f in info.get("files") or []:
        g = str(f).replace("\\", "/")
        if g.lower().endswith(_WEB) and "/views/" not in g and "ui-audit" not in g and "node_modules" not in g:
            out.append(str(root / f))
    out.sort(key=lambda x: (0 if os.path.basename(x).lower().startswith("index") else 1, len(x)))
    return out[:limit]


def code_facts(info: dict) -> str:
    """Plain facts about a code deliverable that no model needs to be trusted for: tests, README."""
    files = [str(f).replace("\\", "/") for f in info.get("files") or []]
    root = Path(info.get("root") or ".")
    code = [f for f in files if f.lower().endswith((".py", ".js", ".ts", ".go", ".rs", ".html", ".sh", ".rb", ".java"))]
    if not code:
        return ""
    tests = [f for f in files if _TEST_FILE.search(f)]
    readme = [f for f in files if os.path.basename(f).lower().startswith("readme")]
    if not readme:
        try:
            readme = [n for n in os.listdir(root) if n.lower().startswith("readme")]
        except OSError:
            pass
    return ("Code facts (measured, not claimed): " + f"{len(code)} code files changed; "
            + (f"{len(tests)} test files ({', '.join(tests[:4])})" if tests else "NO test files among the changed files") + "; "
            + (f"README present ({readme[0]})" if readme else "NO README among the project files") + ".")


def smoke_evidence(info: dict, seconds: float = 100.0) -> str:
    """Click-through of the web pages the agent made (ui_smoke.py), run here so the grader always sees it."""
    pages = _web_files(info)
    if not pages or os.environ.get("MH_GRADER_SMOKE", "1") in ("0", "off"):
        return ""
    box: dict = {}

    def work():
        try:
            from playwright.sync_api import sync_playwright
            from meshpool_harness import ui_smoke as US
            texts = []
            with sync_playwright() as pw:
                for pth in pages:
                    texts.append(US.smoke(pw, pth)["text"])
            box["t"] = "\n".join(texts)
        except Exception as e:  # noqa: BLE001 - evidence is optional
            box["t"] = f"(automatic click-through could not run: {str(e)[:100]})"
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(seconds)
    return box.get("t") or "(automatic click-through timed out)"


def evidence(info: dict, commands: list, maxchars: int = 14000) -> str:
    """The text the grader is given: request, plan, final answer, files, commands. Nothing else of the agent's work."""
    parts = ["## The user's request" + ("s" if len(info.get("requests") or []) > 1 else "")]
    parts += ["> " + r.replace("\n", "\n> ") for r in info.get("requests") or ["(not available)"]]
    todos = info.get("todos") or []
    parts.append("\n## The agent's plan, with the status it gave each step")
    parts += ([f"- [{t.get('status', 'pending')}] {t.get('content', '')}" for t in todos[:30]] or ["(it wrote no plan)"])
    parts.append("\n## The agent's final answer (its claims, not proof)\n" + (info.get("final") or "(empty)")[:6000])
    parts.append(f"\n## Files created or changed this turn (project folder: {info.get('root', '')})")
    parts += ([f"- {f}" for f in info.get("files") or []] or ["(none)"])
    parts.append("\n## Commands it ran, with exit code and the last lines of output")
    if commands:
        for c in commands[-14:]:
            tail = str(c.get("tail") or "").strip().splitlines()[-4:]
            parts.append(f"- `{str(c.get('cmd', ''))[:200]}` exit {c.get('code')}" + ("\n    " + "\n    ".join(
                x[:200] for x in tail) if tail else ""))
    else:
        parts.append("(none)")
    acts = info.get("actions") or []
    if acts:
        parts.append("\n## Actions the agent made in connected apps (the tool accepted each; done once, never to be repeated)")
        parts += [f"- {a}" for a in acts[:20]]
    facts = info.get("facts") or ""
    if facts:
        parts.append("\n## Automatic checks of the result (run by Meshpool, not by the agent)\n" + facts)
    text = "\n".join(parts)
    return text if len(text) <= maxchars else text[:maxchars] + "\n[evidence cut]"


# ---------- reading the grader's JSON -----------------------------------------------------------------------------------------
def _balanced(text: str) -> str:
    i = text.find("{")
    if i < 0:
        return ""
    depth, instr, esc = 0, False, False
    for j in range(i, len(text)):
        ch = text[j]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        elif ch == '"':
            instr = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return text[i:]                              # cut off: repair() closes it


def _repair(s: str) -> str:
    s = re.sub(r",\s*([}\]])", r"\1", s)                                     # trailing commas
    s = s.replace("“", '"').replace("”", '"')                      # curly quotes
    s = re.sub(r"(?<=[{,])\s*'([\w ]+)'\s*:", r' "\1":', s)                  # 'key': value
    s = re.sub(r":\s*'([^'\"]*)'(?=\s*[,}\]])", r': "\1"', s)               # 'value'
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bNone\b", "null", s)
    stack, instr, esc = [], False, False                                      # a reply cut off mid-way: close it
    for ch in s:
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        elif ch == '"':
            instr = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if instr:
        s += '"'
    return re.sub(r",\s*$", "", s) + "".join(reversed(stack))


def parse(text: str) -> dict | None:
    """The verdict object from the grader's reply (fences, chatter, trailing commas, cut-off tail tolerated), or None."""
    t = str(text or "")
    t = re.sub(r"```(?:json)?", "", t)
    body = _balanced(t)
    if not body:
        return None
    for cand in (body, _repair(body)):
        try:
            v = json.loads(cand)
        except ValueError:
            continue
        if isinstance(v, dict):
            return v
    return None


def _strs(v, n: int = 12, cap: int = 400) -> list[str]:
    if isinstance(v, str):
        v = [v]
    return [str(x).strip()[:cap] for x in (v or []) if str(x).strip()][:n] if isinstance(v, list) else []


# 0.0.13: an effect in a LIVE system (a booking, a sent message, a ticket, a connected-app call) that the reviewer could
# not read back is "unverified", and a rework request never asks for a second write. Both rules apply only to text that
# names a live effect, so a defect in a file ("the totals row is missing from report.docx") is never downgraded or
# dropped.
_LIVE = re.compile(
    r"mcp__|\b(?:booking|booked|meeting|calendar|appointment|reservation|ticket|invoice|payment|crm|slack|webhook|"
    r"connected app|connector|live system|status update|sent (?:e-?mail|message)|e-?mail (?:was )?sent|message (?:was )?sent)\b"
    r"|\b(?:create|send|book|update|delete|post|submit|add|set|schedule|close|cancel|issue|insert)_[a-z_]+\b", re.I)
_NO_READBACK = re.compile(
    r"read[- ]?back|can(?:no|')t be (?:read|verified|confirmed|checked|observed)|cannot (?:be )?(?:read|verif|confirm|check|observ)\w*"
    r"|(?:unable|not able|no way|not possible|impossible) to (?:read|verify|confirm|check|observe)"
    r"|(?:could not|couldn't|can't|can not|did not|didn't) (?:read|verify|confirm|observe) (?:it|that|the|this)\b"
    r"|no (?:read|get|list|lookup|query)(?:[- ]only)? tool|not (?:readable|verifiable|observable)|no read access", re.I)
_REPEAT_WRITE = re.compile(
    r"\bre-?(?:send|book|submit|post|invoke|issue)\b"
    r"|\b(?:call|send|book|create|post|update|submit|write|issue|invoke|trigger|make|run)\b[^.;]{0,50}\b(?:create|send|book|post|update|submit|write|issue|schedule|delete|insert|add)\w*[^.;]{0,30}\bagain\b"
    r"|\b(?:create|send|book|post|submit|issue|schedule)\w*\b[^.;]{0,50}\bagain\b"
    r"|\b(?:repeat|retry)\b[^.;]{0,40}\b(?:write|booking|send|call|request|action|update|creation)\b"
    r"|\bsecond (?:call|write|booking|send|request)\b|\bprobe\b"
    r"|\btest (?:booking|meeting|message|record|ticket|call|email|event|invoice)\b", re.I)


def _unreadable_live(text: str) -> bool:
    return bool(_NO_READBACK.search(text) and _LIVE.search(text))


def _unreadable(it: dict) -> bool:
    return _unreadable_live(f"{it.get('requirement', '')} {it.get('evidence', '')}")


def _no_second_write(lines: list[str]) -> list[str]:
    """Drop instructions to run a live write again; instructions about the agent's own files stay."""
    return [x for x in lines if not (_REPEAT_WRITE.search(x) and _LIVE.search(x))]


def normalise(raw: dict | None) -> dict | None:
    """A clean result with the safe reading of whatever the grader said: missing evidence is not met, a pass with an open
    item is partial. None when it has no usable items at all."""
    if not isinstance(raw, dict):
        return None
    items = []
    for it in raw.get("items") or []:
        if not isinstance(it, dict) or not str(it.get("requirement") or "").strip():
            continue
        st = str(it.get("status") or "").strip().lower().replace(" ", "_").replace("-", "_")
        st = st if st in ("met", "not_met", "unverified") else ("not_met" if st in ("unmet", "no", "failed", "missing")
                                                                else "unverified")
        ev = str(it.get("evidence") or "").strip()
        if st == "not_met" and _unreadable({"requirement": str(it["requirement"]), "evidence": ev}):
            st = "unverified"                                    # could not read it back: unverified, not a defect
        if st == "met" and not ev:
            st = "unverified"                                    # a claim of "met" with nothing seen is not met
        items.append({"requirement": str(it["requirement"]).strip()[:300], "status": st, "evidence": ev[:500]})
    if not items:
        return None
    bad = sum(1 for i in items if i["status"] == "not_met")
    unv = sum(1 for i in items if i["status"] == "unverified")
    blockers, fix = _no_second_write(_strs(raw.get("blockers"))), _no_second_write(_strs(raw.get("fix")))
    blockers = [b for b in blockers if not _unreadable_live(b)]
    verdict = str(raw.get("verdict") or "").strip().lower()
    verdict = verdict if verdict in ("pass", "partial", "fail") else ("pass" if not bad and not unv else "partial")
    acc = str(raw.get("acceptance") or "").strip().lower().replace(" ", "_")
    acc = acc if acc in ("accept", "accept_with_notes", "would_send_back") else ""
    if verdict == "pass" and (bad or blockers or unv or acc == "would_send_back"):
        verdict = "partial"                                      # never a pass with something open
    if verdict != "pass" and acc == "accept":
        acc = "accept_with_notes" if verdict == "partial" else "would_send_back"
    if verdict == "fail" and not acc:
        acc = "would_send_back"
    if not acc:
        acc = "accept" if verdict == "pass" else "accept_with_notes" if not bad else "would_send_back"
    return {"verdict": verdict, "acceptance": acc, "acceptance_reasons": _strs(raw.get("acceptance_reasons"), 3),
            "expectations": _strs(raw.get("expectations"), 14), "items": items[:30], "blockers": blockers, "fix": fix,
            "met": sum(1 for i in items if i["status"] == "met"), "total": len(items), "not_met": bad, "unverified": unv}


def needs_rework(res: dict) -> bool:
    """Something the agent must fix: a requirement not met, or a blocker. Items nobody could check (unverified) and
    suggestions alone are reported to the user but do not send the agent round again (that would double the cost)."""
    return res.get("verdict") in ("partial", "fail") and bool(res.get("not_met") or res.get("blockers"))


def findings_text(res: dict) -> str:
    out = []
    for i in res.get("items") or []:
        if i["status"] == "not_met":
            out.append(f"- {i['requirement']}" + (f" ({i['evidence']})" if i.get("evidence") else ""))
    out += [f"- {b}" for b in res.get("blockers") or [] if b not in " ".join(out)]
    fixes = [f for f in res.get("fix") or []]
    return "\n".join(out[:14]) + (("\n\nHow to fix:\n" + "\n".join(f"- {f}" for f in fixes[:10])) if fixes else "")


def inject_text(res: dict) -> str:
    return INJECT.format(items=findings_text(res) or "- (the reviewer gave no detail; re-check the request point by point)")


ACCEPT_WORDS = {"accept": "would accept", "accept_with_notes": "would accept with notes", "would_send_back": "would send it back"}


def report(res: dict, rounds: int = 0, forced: bool = False) -> str:
    """The short plain 'Independent check' section for the end of the final reply."""
    if res.get("error"):
        return ("Independent check: it could not run (" + str(res["error"])[:120].rstrip(".") + "). "
                "This work was not independently checked.")
    n, t = res.get("met", 0), res.get("total", 0)
    after = f" after {rounds} fix round{'s' if rounds != 1 else ''}" if rounds else ""
    if res["verdict"] == "pass":
        return f"Independent check: all {t} requirement{'s' if t != 1 else ''} met{after}."
    lines = [f"Independent check: {n} of {t} requirements met{after}. " + (
        "Not finished." if res["verdict"] == "fail" else "Some things are still open.")]
    for i in res["items"]:
        if i["status"] == "not_met":
            lines.append(f"- Not done: {i['requirement']}" + (f" ({i['evidence']})" if i["evidence"] else ""))
    for i in res["items"]:
        if i["status"] == "unverified":
            lines.append(f"- Could not check: {i['requirement']}" + (f" ({i['evidence']})" if i["evidence"] else ""))
    for b in res.get("blockers") or []:
        if b and not any(b[:40] in x for x in lines):
            lines.append(f"- Open: {b}")
    if res.get("acceptance"):
        why = " ".join(res.get("acceptance_reasons") or [])
        lines.append(f"The reviewer {ACCEPT_WORDS[res['acceptance']]}." + (f" {why}" if why else ""))
    return "\n".join(lines[:16])


def receipt_line(ev: dict) -> str:
    """One plain line for the receipt: what the check found, and what it cost."""
    if ev.get("error"):
        return "could not run, so this was not independently checked"
    t = ev.get("tokens") or {}
    n = int(t.get("in", 0)) + int(t.get("out", 0))
    word = {"pass": f"all {ev.get('total', 0)} requirements met", "partial": f"{ev.get('met', 0)} of {ev.get('total', 0)} met, "
            "some open", "fail": f"{ev.get('met', 0)} of {ev.get('total', 0)} met, not finished"}.get(ev.get("verdict"), "done")
    rounds = f", {ev['round']} fix round{'s' if ev['round'] != 1 else ''}" if ev.get("round") else ""
    return f"{word}{rounds} ({ev.get('secs', 0):.0f} s, {n / 1000:.1f}k tokens)"


def event(res: dict, *, text: str, rounds: int, why: str, secs: float, usage: dict, forced: bool = False,
          rework: bool = False, model: str = "") -> dict:
    """The 'review' event the app, the terminal, the headless output and the receipt share."""
    return {"type": "review", "verdict": res.get("verdict", "error"), "acceptance": res.get("acceptance", ""),
            "acceptance_reasons": res.get("acceptance_reasons", []), "met": res.get("met", 0), "total": res.get("total", 0),
            "items": res.get("items", []), "expectations": res.get("expectations", []), "blockers": res.get("blockers", []),
            "fix": res.get("fix", []), "error": res.get("error", ""), "text": text, "round": rounds, "reason": why,
            "rework": rework, "forced": forced, "secs": round(secs, 1), "model": model,
            "tokens": {"in": usage.get("in", 0), "cached": usage.get("cached", 0), "out": usage.get("out", 0),
                       "calls": usage.get("calls", 0)}, "touched": res.get("touched", [])}


# ---------- goal mode: a rubric written before the work, scored after it ------------------------------------------------------
RUBRIC_SYSTEM = """You write the rubric for a goal another agent of meshpool will work on until an independent check says it meets the bar. You do not do the work. Write it before any work starts, from the user's own words and a quick read-only look at the project when that helps.

Write 4 to 12 criteria. Each one is a thing the finished result must have, that a reviewer with read-only tools can check. For each: "text" (one short plain sentence), "check" (how to check it: which file to open, which command or office check to run, what to look for), "weight" (1 to 5, how much it matters to the user), "must" (true when the result is unacceptable without it), and "needs_user" (true only when only the user can provide or decide it, for example a password, a missing file or a choice between options).

Hold to what was asked: no extra deliverables, formats or research the user did not ask for. Include the implied basics a user expects (it works, it opens, numbers are right, no placeholder text). Do not write budgets or time limits into the criteria.

You may only read. Finish with ONE JSON object and nothing else (no code fence):
{"criteria": [{"text": "...", "check": "...", "weight": 3, "must": true, "needs_user": false}]}"""

RUBRIC_ASK = "Write the rubric for this goal now. Reply with the JSON object only."

GOAL_SYSTEM = """You are the INDEPENDENT REVIEWER of meshpool, in goal mode. Another agent is working toward a goal the user set. You were not part of that work and you have no stake in it. The rubric below was written and frozen before the work began: score the current result against it, criterion by criterion. Do not add, drop or reword criteria, and do not invent your own expectations.

Treat the agent's own claims as claims, never as proof. Check each criterion yourself with your tools: open the files, run the project's own tests, read documents with read_document, use check_deck, check_workbook, check_document or view_output for office files and pages. The gate results (tests, build, lint or office checks run by meshpool itself) are facts: a failing gate means the criteria it covers are not met.

Missing evidence counts as NOT met. Use "unverified" only when your tools truly cannot check it, and say why in the evidence. Mark "needs_user" with one plain question only when a criterion cannot be met without the user (a password, a missing file, a decision).

Rules: you may only read and run checks. You must not create, change or delete any file, install anything or send anything anywhere. Be efficient: a few targeted looks are better than reading everything.

Finish with ONE JSON object and nothing else (no code fence):
{"items": [{"id": "c1", "status": "met" | "not_met" | "unverified", "evidence": "what you saw, with a file name or command"}],
 "fix": ["one clear instruction per open criterion, for the agent"],
 "blockers": ["something outside the agent's control that stops a criterion"],
 "needs_user": "one plain question for the user, or an empty string"}"""

GOAL_CHECK = ("The agent is working toward the goal. Score the current result against the frozen rubric now. Look at "
              "the real result, then reply with the JSON object only.")


def goal_evidence(info: dict, commands: list, rubric: list, gates: list | None = None, maxchars: int = 16000) -> str:
    """What the goal check sees: the frozen rubric, the gate results, then the same evidence as the ordinary check."""
    parts = ["## The frozen rubric (score each criterion by its id)"]
    for c in rubric or []:
        parts.append(f"- {c.get('id')}{' (must have)' if c.get('must') else ''}, weight {c.get('weight', 1)}: "
                     f"{c.get('text')}" + (f"\n    How to check: {c['check']}" if c.get("check") else ""))
    parts.append("\n## Gates run by meshpool just now (facts, judged by exit codes and the office checks)")
    if gates:
        for g in gates:
            state = "passed" if g.get("ok") else "FAILED" if g.get("ok") is False else "not run"
            parts.append(f"- {g.get('name')}: {state}" + (f" (exit {g['code']})" if g.get("code") is not None else "")
                         + (f", {g['why']}" if g.get("why") else "")
                         + ("\n    " + "\n    ".join(str(g["tail"]).splitlines()[-4:]) if g.get("tail") else ""))
    else:
        parts.append("(no test, build, lint or office check applies)")
    head = "\n".join(parts)
    return head + "\n\n" + evidence(info, commands, maxchars=max(4000, maxchars - len(head)))


def normalise_goal(raw: dict | None, rubric: list) -> dict | None:
    """The goal check's reply, one item per rubric criterion (in rubric order). A criterion the reply leaves out is
    "unverified", which scores as not met. A "met" with no evidence is unverified too. None when no item matched."""
    if not isinstance(raw, dict):
        return None
    by: dict = {}
    for it in raw.get("items") or []:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("id") or "").strip().lower()
        if not cid and it.get("requirement"):
            cid = next((c["id"] for c in rubric if c["text"].strip().lower() == str(it["requirement"]).strip().lower()), "")
        if cid:
            by[cid] = it
    if not by:
        return None
    items = []
    for c in rubric:
        it = by.get(c["id"], {})
        st = str(it.get("status") or "").strip().lower().replace(" ", "_").replace("-", "_")
        st = st if st in ("met", "not_met", "unverified") else ("not_met" if st in ("unmet", "no", "failed", "missing")
                                                                else "unverified")
        ev = str(it.get("evidence") or "").strip()[:500]
        if not it:
            ev = "the check gave no answer for this criterion"
        if st == "met" and not ev:
            st = "unverified"
        items.append({"id": c["id"], "text": c["text"], "must": bool(c.get("must")), "status": st, "evidence": ev})
    nu = raw.get("needs_user")
    nu = str(nu).strip() if isinstance(nu, str) else ""
    return {"items": items, "fix": _no_second_write(_strs(raw.get("fix"))),
            "blockers": _no_second_write(_strs(raw.get("blockers"))), "needs_user": nu[:400],
            "met": sum(1 for i in items if i["status"] == "met"), "total": len(items)}


def draft_rubric(parent, goal: str, mode: str = "code", bar: float = 10, on_event=None) -> dict:
    """Write the goal's rubric before turn 1: a fresh read-only agent (MH_GRADER_MODEL when set) with a few steps to
    look at the project. Returns {"criteria": [...], "error": ""}; on any failure the criteria are the fallback set and
    "error" says why. Never raises (except for the chat's own Stop)."""
    from meshpool_harness.agent import core as K
    from meshpool_harness.agent import goal as G
    t0 = time.time()
    used = {"in": 0, "cached": 0, "out": 0, "calls": 0}
    a = None

    def emit(ev):
        if isinstance(ev, dict) and ev.get("type") == "usage":
            for k in used:
                used[k] += int(ev.get(k) or 0)
        elif isinstance(ev, dict) and ev.get("type") in ("tool_start", "status") and on_event:
            try:
                on_event(ev)
            except Exception:  # noqa: BLE001
                pass
    try:
        steps = _num("MH_GOAL_RUBRIC_STEPS", 4, 1, 20)
        a = make_agent(parent, emit, lambda: bool(getattr(parent, "_stop", False)), system=RUBRIC_SYSTEM, steps=steps)
        place = "Chat mode: the work is office files (decks, workbooks, documents) or a written answer." if mode == "chat" \
            else "Code mode: the work is a change to the project's code."
        a.messages.append({"role": "user", "content": f"## The goal\n> {str(goal)[:3000]}\n\nThe bar: {G.fmt(bar)}/10. "
                                                      f"{place}\nProject folder: {parent.root}\n\n{RUBRIC_ASK}"})
        reply = a._turn() or ""
        raw = parse(reply)
        if not raw or not (raw.get("criteria") or raw.get("rubric")):
            a._stop, a.max_steps, a._no_tools = False, 1, True
            a.messages.append({"role": "user", "content": "No more tool calls. " + RUBRIC_ASK})
            raw = parse(a._turn() or "")
        crit = (raw or {}).get("criteria") or (raw or {}).get("rubric") or []
        if not crit:
            out = {"criteria": G.fallback_rubric(goal, mode), "error": "the rubric could not be read"}
        else:
            out = {"criteria": crit, "error": ""}
    except (K.Interrupted, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001 - a failed draft falls back, it never blocks the goal
        out = {"criteria": G.fallback_rubric(goal, mode), "error": f"the rubric model failed: {str(e)[:100]}"}
    finally:
        if a is not None:
            shutil.rmtree(getattr(a, "_tmp", ""), ignore_errors=True)
            shutil.rmtree(a.dir, ignore_errors=True)
    out["secs"], out["usage"] = time.time() - t0, used
    return out


# ---------- the read-only grader agent ----------------------------------------------------------------------------------------
def _snapshot(root: Path, cap_files: int = 4000, keep: int = 262144, budget: int = 64 * 1024 * 1024) -> dict:
    """{relpath: (size, mtime_ns, bytes or None)} of the project, to report anything that changed while the grader ran."""
    snap, used = {}, 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not _skip_dir(dp, d)]
        for fn in fns:
            if len(snap) >= cap_files:
                return snap
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
                data = None
                if st.st_size <= keep and used + st.st_size <= budget:
                    with open(p, "rb") as f:
                        data = f.read()
                    used += st.st_size
                snap[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns, data)
            except OSError:
                pass
    return snap


def _changed(root: Path, before: dict) -> list[str]:
    """What differs from the snapshot (report only: the project may legitimately change while the grader works, for
    example an editor or a background process; the grader's own tools cannot write)."""
    touched = []
    now = _snapshot(root)
    for rel, (size, mt, _data) in before.items():
        cur = now.get(rel)
        if cur is None or (cur[0], cur[1]) != (size, mt):
            touched.append(rel)
    touched += [rel for rel in now if rel not in before]
    return sorted(touched)[:50]


def command_allowed(cmd: str) -> bool:
    """A shell command the grader may run: one that only reads, or one of the project's own test commands."""
    from meshpool_harness.core import tools as T
    c = (cmd or "").strip()
    if not c:
        return False
    if T.read_only(c):
        return True
    bare = re.sub(r"^cd\s+[^\s;&|`$<>]+\s*&&\s*", "", c)           # cd project && wc -c file: still only reads
    if bare != c and T.read_only(bare):
        return True
    c = re.sub(r"\s*2>&1", "", c)                                    # stderr into stdout is not a write
    c = re.sub(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+", "", c)               # PYTHONPATH=. pytest
    head, *rest = c.split("|")                                       # pytest ... | tail -20
    head = head.strip()
    if rest and not all(T.read_only(r.strip()) for r in rest):
        return False
    return bool(_TEST_CMD.match(head)) and not re.search(r"[;&`$<>\n]", re.sub(r"^cd\s+\S+\s*&&\s*", "", head))


class _Mcp:
    """The chat's MCP servers, narrowed to the tools that only look."""
    def __init__(self, mcp):
        self.m = mcp
        self.last_images: list = []

    @staticmethod
    def ok(name: str) -> bool:
        return name.replace("-", "_").split("__")[-1] in MCP_LOOK

    def has(self, name: str) -> bool:
        return self.ok(name) and self.m.has(name)

    def schemas(self) -> list:
        return [s for s in self.m.schemas() if self.ok(s["function"]["name"])]

    def call(self, name: str, args: dict):
        if hasattr(self.m, "call_with_images"):
            out, self.last_images = self.m.call_with_images(name, args)
            return out
        out = self.m.call(name, args)
        self.last_images = list(getattr(self.m, "last_images", []) or [])
        return out

    def __getattr__(self, k):
        return getattr(self.m, k)


def make_agent(parent, emit, stop_check=None, system: str | None = None, steps: int | None = None):
    """A fresh read-only agent for grading: its own system prompt and empty history, on the fast model if set.
    system: another prompt (goal mode drafts and scores a rubric with this same read-only agent)."""
    from meshpool_harness.agent import core as K
    from meshpool_harness.agent import delegate as DL

    class GraderAgent(K.Agent):
        _is_grader = True
        reports_to_agent = True      # its verdict is JSON for the parent, not a reply to the user

        def system_prompt(self):
            return system or SYSTEM

        def _style(self):
            return False

        def _plan_start(self, route, g):
            self._plan_needed = False

        def _tool_names(self):
            return ["read_file", "grep", "glob", "bash"]

        def tool_schemas(self):
            from meshpool_harness.core import tools as T
            return T.schemas(self._tool_names()) + (self.mcp.schemas() if self.mcp else [])

        def _permit(self, name, args):
            n = name.replace("-", "_")
            if n in ("read_file", "grep", "glob"):
                if self._outside(name, args):
                    return False, "the reviewer only reads inside the project"
                return True, ""
            if n == "bash":
                if command_allowed(str(args.get("command") or "")):
                    return True, ""
                return False, ("the reviewer may only run commands that read, or the project's own tests "
                               "(pytest, npm test, go test, cargo test)")
            if self.mcp and _Mcp.ok(name):
                if DL._mcp_paths_outside(parent.root, args, getattr(parent, 'id', None)):
                    return False, "the reviewer only reads inside the project"
                return True, ""
            return False, "the reviewer cannot use this tool: it only reads and runs checks"

        def _check_stop(self):
            if stop_check and stop_check():
                raise K.Interrupted()
            super()._check_stop()

    tmp = tempfile.mkdtemp(prefix="mh-grader-")
    a = GraderAgent(str(parent.root), mode="auto", profile=parent.profile, approve=None, emit=emit,
                    session_id="grader-" + parent.id[-12:], max_steps=steps or limits()["steps"], sessions_dir=tmp,
                    mcp=_Mcp(parent.mcp) if getattr(parent, "mcp", None) else None)
    a._tmp = tmp
    a._delegate_depth = 1                         # no sub-agents of its own
    a.model = (_cfg("MH_GRADER_MODEL") or _cfg("MH_MODEL_FAST") or parent.current_model() or _cfg("MH_MODEL"))
    a.lane = "task"
    a.interactive = lambda: False
    return a


def run(parent, info: dict, commands: list, *, on_event=None, rubric: list | None = None,
        gates: list | None = None) -> dict:
    """Grade one finished piece of work. Returns the normalised result, or {"error": why, ...} when the grader could not
    run. Never raises (except for the chat's own Stop, passed on as K.Interrupted).

    rubric: goal mode. The grader scores the work against this frozen rubric instead of writing its own expectations,
    with its read-only tools (never the one-call light review), and returns a status per criterion id
    (normalise_goal). The score itself is computed by agent/goal.py, never by the model. gates: the hard checks the
    harness ran first, shown to the grader as facts."""
    from meshpool_harness.agent import core as K
    lim = limits()
    t0 = time.time()
    used = {"in": 0, "cached": 0, "out": 0, "calls": 0}
    flags = {"over": False, "steps": False}
    holder: dict = {}

    def emit(ev):
        if not isinstance(ev, dict):
            return
        if ev.get("type") == "usage":
            for k in used:
                used[k] += int(ev.get(k) or 0)
            if used["in"] + used["out"] > lim["tokens"]:
                flags["over"] = True
                if holder.get("a"):
                    holder["a"].stop()
        elif ev.get("type") == "error" and "Stopped after" in str(ev.get("text")):
            flags["steps"] = True
        elif ev.get("type") in ("tool_start", "status") and on_event:
            try:
                on_event(ev)
            except Exception:  # noqa: BLE001
                pass

    def parent_stop():
        return bool(getattr(parent, "_stop", False))

    out: dict = {}
    snap, root, a, timer = {}, Path(parent.root), None, None
    try:
        try:
            snap = _snapshot(root)
        except Exception:  # noqa: BLE001
            snap = {}
        a = holder["a"] = (make_agent(parent, emit, parent_stop, system=GOAL_SYSTEM) if rubric
                           else make_agent(parent, emit, parent_stop))
        usage_total = used
        timer = threading.Timer(lim["seconds"], lambda: (flags.__setitem__("over", True), a.stop()))
        timer.daemon = True
        timer.start()
        try:
            info = dict(info)
            info["facts"] = "\n".join(x for x in (code_facts(info), smoke_evidence(info)) if x)
        except Exception:  # noqa: BLE001
            pass
        if rubric:
            a.messages.append({"role": "user", "content": goal_evidence(info, commands, rubric, gates) + "\n\n"
                               + GOAL_CHECK})
        else:
            a.messages.append({"role": "user", "content": evidence(info, commands) + "\n\n" + CHECK})
        if os.environ.get("MH_GRADER_LIGHT", "1") != "0" and not rubric:
            # one call on the evidence (files, commands, facts), no tool loop of up to 15 steps; MH_GRADER_LIGHT=0 restores
            # the full reviewer (on the 23 long tasks: 94% solved at 0.0030 per solved against 89% at 0.0051)
            a.max_steps, a._no_tools = 1, True
        reply = a._turn() or ""
        norm = (lambda raw: normalise_goal(raw, rubric)) if rubric else normalise
        res = norm(parse(reply))
        stopped_hard = flags["over"]
        if res is None and not parent_stop():
            # out of steps, cut off or chatter: one bounded repair with no tools
            a._stop = False
            a.max_steps, a._no_tools = 1, True
            a.messages.append({"role": "user", "content": ("You are out of budget: no more tool calls. " if (
                flags["steps"] or stopped_hard) else "") + REPAIR})
            if not flags["over"] or used["in"] + used["out"] <= lim["tokens"] * 1.3:
                reply = a._turn() or ""
                res = norm(parse(reply))
        if parent_stop():
            raise K.Interrupted()
        if res is None:
            why = ("it ran out of time" if stopped_hard and time.time() - t0 >= lim["seconds"] - 1 else
                   "it ran out of its token budget" if stopped_hard else "its answer could not be read")
            out = {"error": why}
        else:
            out = res
    except (K.Interrupted, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001 - the grader must never block the user
        out = {"error": f"the review model failed: {str(e)[:100]}"}
    finally:
        if timer:
            timer.cancel()
        touched = []
        try:
            touched = _changed(root, snap) if snap else []
        except Exception:  # noqa: BLE001
            pass
        if a is not None:
            shutil.rmtree(getattr(a, "_tmp", ""), ignore_errors=True)
            shutil.rmtree(a.dir, ignore_errors=True)
    out["touched"] = touched
    out["secs"] = time.time() - t0
    out["usage"] = dict(used)
    out["model"] = getattr(a, "model", "") or ""
    out["steps"] = sum(1 for m in (a.messages if a else []) if m.get("role") == "tool")
    return out
