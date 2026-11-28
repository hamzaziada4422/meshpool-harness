"""Finish-time checks shared by the benchmark loop (core/loop.py) and the app agent (agent/core.py)."""
from __future__ import annotations

from .guards import TEST_PATH


def scope_report(ws, done: str = "finish") -> str | None:
    """MH_SCOPE_CHECK=1: at the first finish, if the change spreads past a minimal fix (more than one non-test source
    file, or more than 30 changed non-test lines), show the per-file diff size once and ask to drop what the task
    does not need. Rule fixed in advance, not tuned on any task. None = nothing to show."""
    try:
        out, _ = ws._run("git diff --numstat HEAD -- . ':!.run' ':!.junie' 2>/dev/null", 30, "bash", record=False)
    except Exception:                                 # noqa: BLE001 - a report must never break a run
        return None
    rows = []
    for line in str(out).splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit() or TEST_PATH.search(parts[2]):
            continue
        rows.append((parts[2], int(parts[0]) + int(parts[1])))
    if len(rows) <= 1 and sum(n for _, n in rows) <= 30:
        return None
    lines = "\n".join(f"  {f}: {n} lines" for f, n in sorted(rows, key=lambda r: -r[1])[:10])
    return (f"Scope check (shown once; {done} is accepted next time). Your change touches these non-test files:\n"
            + lines + "\nTests check the behaviour the task asks for, and every extra change is a chance to "
            "break one. Keep the smallest fix that does what the task asks: revert edits it does not need (git "
            "checkout -- <file> for a whole file), re-run your check, then {done}. If every change is needed, {done} "
            "again.".format(done=done))


# ---------- 0.0.21: finish the whole ask, and recover from a refusal ----------------------------------------------
# Pure checks for the app loop (agent/core.py _turn). No model calls: a short turn pays a few regex scans.
import re  # noqa: E402

_ITEM = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+(.+)$", re.M)
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{4,}")
_STOP = frozenset("""about above after again against being below between could doesn't during every first having
other their there these those through under until where which while would should please thanks really still into
from with that this what when your yours make sure using based each also just like only very will want need
order adding""".split())
_NEEDS_USER = re.compile(r"\b(?:need|needs|needed) (?:your|you to|the user)|\bcan(?:no|')t [^.\n]{0,40}without\b"
                         r"|\bnot (?:possible|able) [^.\n]{0,40}without\b|\bplease (?:send|share|provide|confirm)\b",
                         re.I)


def _words(text: str) -> set:
    return {w.lower().strip("'-") for w in _WORD.findall(text or "")} - _STOP


def unaddressed(ask: str, said: str, root=None) -> list:
    """What the request asked for that the turn shows no trace of, or []. said: the turn's replies, the arguments of
    its tool calls and the names of the files it wrote. Two rules, both conservative:
    - a list of 2 to 12 items in the request: an item with 2 or more content words, none of which the turn mentions;
    - a file the request asks to be written (guards.deliverable_names) that is not in the folder.
    A reply that says it needs the user, or cannot go on without something, is an answer: nothing is missing."""
    from .guards import deliverable_names
    ask = (ask or "").split("\n\n​")[0][:8000]
    if not ask.strip() or _NEEDS_USER.search(said or ""):
        return []
    out = []
    items = [m.group(1).strip() for m in _ITEM.finditer(ask)]
    if 2 <= len(items) <= 12:
        seen = _words(said)
        for it in items:
            w = _words(it)
            if len(w) >= 2 and not (w & seen):
                out.append(it[:120])
    if root is not None:
        from pathlib import Path
        for n in sorted(deliverable_names(ask)):
            try:
                if not (Path(root) / n).exists():
                    out.append(f"write {n}")
            except OSError:
                pass
    return out[:8]


_DECLINE = re.compile(
    r"\bI (?:can(?:no|')t|won't|will not|am unable to|am not able to|must decline to) (?:help|assist)(?: you)? with\b"
    r"|\bI(?:'m| am) sorry,? but I (?:can(?:no|')t|won't|will not|am unable to)\b"
    r"|\bI must (?:respectfully )?decline\b"
    r"|\b(?:against|violates?) my (?:guidelines|policies|usage polic(?:y|ies)|principles)\b"
    r"|\bI(?:'m| am) not (?:able|comfortable|allowed|permitted) to (?:help|assist)\b"
    r"|抱歉[，,]?\s*(?:但)?我(?:无法|不能)|我(?:无法|不能)(?:协助|帮助|提供|完成)", re.I)


def task_refusal(final: str) -> bool:
    """A short reply that declines the task ("I'm sorry, but I can't help with that", or the same in Chinese), with no
    sign that a missing tool is the reason (that is capability_get's case). Long replies are answers that mention a
    limit, not refusals."""
    t = re.sub(r"[*_`]", "", str(final or "")).strip()
    if not t or len(t) > 600 or not _DECLINE.search(t):
        return False
    from meshpool_harness import capability_get as CG
    return not CG.missing_signal(t, "reply", True)


_HARMFUL = re.compile(
    r"\b(?:bomb|explosive|detonat\w*|nerve agent|bioweapon|chemical weapon|weaponi[sz]\w*|firearm|ghost gun|"
    r"ransomware|malware|keylogger|spyware|trojan|botnet|ddos|rootkit|credential (?:theft|harvest\w*)|phishing|"
    r"steal\w* (?:passwords?|credentials|cards?|identit\w*)|hack(?:ing)? into|exploit (?:someone|a victim)|"
    r"evade (?:antivirus|detection|edr)|bypass (?:antivirus|edr|2fa|authentication)|"
    r"self[- ]harm|suicide|kill (?:myself|someone|him|her|them)|poison (?:someone|him|her|them)|"
    r"child (?:porn\w*|sexual)|minors? (?:sexual|nude)|csam|"
    r"meth(?:amphetamine)?|fentanyl|synthesi[sz]e (?:drugs|cocaine|heroin)|"
    r"stalk\w*|track (?:someone|my (?:wife|husband|girlfriend|boyfriend|partner|ex))|dox\w*|"
    r"jailbreak|ignore (?:your|all) (?:rules|guidelines|instructions))\b", re.I)


def harmful_ask(ask: str) -> bool:
    """A request in an area where a refusal is the model's safety training working: never retried."""
    return bool(_HARMFUL.search((ask or "")[:20000]))


def refusal_context(mode: str, root: str = "") -> str:
    """The one retry after a refusal of an ordinary task: what the model may not have known. Never asks the model to
    set its rules aside."""
    where = f" in {root}" if root else ""
    return ("[Meshpool] Context for the request above: the files" + where + " are the user's own, on their own "
            f"computer, and meshpool runs your tools with their permission (mode: {mode}). The request is ordinary "
            "work. If you declined because of a misunderstanding, do it now with your tools. If it is genuinely "
            "harmful, decline in one sentence.")
