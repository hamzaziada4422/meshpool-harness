"""Long tasks that keep going: when a conversation is too big even after old tool output is shortened, the older steps
are replaced by a handoff note, so a task can run for hundreds of steps without losing the thread.

The conversation keeps: the system message, the user's original task word for word, the most recent messages word for
word, and in between one note. The note has two parts. One is written by the fast model from the older steps (what is
done, what was learned, what is left). The other is built by rules from the log itself, so it holds even if the model
call fails or leaves something out: the files touched, the commands run with their exit codes, and the plan as it stood.

Nothing is ever dropped silently: a status line says what happened, and the full history stays in the saved chat.
"""
from __future__ import annotations

import json
import os
import re

KEEP_RECENT = 10                 # messages kept word for word at the end
MAX_LOG = 60_000                 # characters of older steps shown to the summariser
MAX_NOTE = 12_000                # characters of summary kept
SYSTEM = ("You write a handoff note for an AI agent that will continue a long task after its earlier messages are "
          "removed to save space. Use only facts in the log. Plain sentences, no flattery, no em dashes. Keep exact "
          "file paths, names, numbers, error messages and decisions. Use these headings: Done, Learned (facts, "
          "constraints, user preferences), Problems still open, Next steps. Under 700 words.")


def split(messages: list, keep_recent: int = KEEP_RECENT):
    """(head, middle, tail) or None when there is nothing worth summarising. The tail never starts on a tool result,
    so every tool result in it still has the assistant call that asked for it."""
    n = len(messages)
    if n < keep_recent + 6:
        return None
    head_len = 2 if n > 1 and messages[1].get("role") == "user" else 1
    cands = [i for i in range(head_len, n) if messages[i].get("role") in ("user", "assistant")]
    ahead = [i for i in cands if i >= n - keep_recent]
    start = min(ahead) if ahead else (max(cands) if cands else None)
    if start is None or start - head_len < 5:
        return None
    return messages[:head_len], messages[head_len:start], messages[start:]


def _text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(str(p.get("text") or "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _calls(m: dict) -> list[tuple[str, str, dict]]:
    out = []
    for c in m.get("tool_calls") or []:
        f = c.get("function") or {}
        try:
            args = json.loads(f.get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        out.append((c.get("id") or "", f.get("name") or "", args if isinstance(args, dict) else {}))
    return out


def render(middle: list) -> str:
    """The older steps as a short log for the summariser (the start and the most recent part when it is long)."""
    lines = []
    for m in middle:
        role = m.get("role")
        if role == "tool":
            lines.append("  result: " + _text(m.get("content")).replace("\n", " ")[:300])
            continue
        t = _text(m.get("content")).replace("\n", " ").strip()
        if t:
            lines.append(f"{role}: {t[:1200]}")
        for _id, name, args in _calls(m):
            lines.append(f"  call {name}: " + json.dumps(args, ensure_ascii=False)[:260])
    log = "\n".join(lines)
    if len(log) > MAX_LOG:
        log = log[:10_000] + "\n... [middle of the log left out] ...\n" + log[-(MAX_LOG - 10_000):]
    return log


def rule_notes(middle: list) -> str:
    """Facts read straight from the log: no model, so they are always right."""
    files: list[str] = []
    cmds: list[tuple[str, str]] = []
    results = {m.get("tool_call_id"): _text(m.get("content")) for m in middle if m.get("role") == "tool"}
    todo = ""
    for m in middle:
        if m.get("role") != "assistant":
            continue
        for cid, name, args in _calls(m):
            if name in ("edit", "write_file", "apply_patch"):
                paths = [args.get("path")] + [c.get("path") for c in args.get("changes") or [] if isinstance(c, dict)]
                files += [p for p in paths if isinstance(p, str) and p and p not in files]
            elif name in ("bash", "bash_start", "run_tests", "probe"):
                out = results.get(cid, "")
                mm = re.match(r"\[exit (\d+)\]", out)
                cmds.append((str(args.get("command") or name)[:140], mm.group(1) if mm else "?"))
            elif name == "bash_many":
                for c in args.get("commands") or []:
                    cmds.append((str(c)[:140], "?"))
            elif name == "todo_write":
                todo = json.dumps(args.get("todos") if "todos" in args else args, ensure_ascii=False)[:1500]
    parts = []
    if files:
        parts.append("Files changed: " + ", ".join(files[:40]) + (f" (+{len(files) - 40} more)" if len(files) > 40 else ""))
    if cmds:
        bad = [c for c in cmds if c[1] not in ("0", "?")]
        parts.append("Commands run: " + str(len(cmds)) + ". Last: " + "; ".join(f"{c} (exit {e})" for c, e in cmds[-6:]))
        if bad:
            parts.append("Commands that failed: " + "; ".join(f"{c} (exit {e})" for c, e in bad[-5:]))
    if todo:
        parts.append("Plan at that point: " + todo)
    return "\n".join(parts)


def summarize(middle: list, task: str, client=None, model: str | None = None, focus: str = "") -> str:
    """The note's model-written part. "" when no model can be reached (the rule-based part still goes in)."""
    if os.environ.get("MH_COMPACT_MODEL", "1") == "0":
        return ""
    try:
        if client is None:
            from meshpool_harness.core.client import LiveClient
            client = LiveClient("compact")
        model = model or os.environ.get("MH_MODEL_FAST") or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
        r = client.chat(model=model, messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"The task:\n{task[:3000]}\n\nThe log of earlier steps:\n{render(middle)}"
             + (f"\n\nThe user asked this summary to keep, in full detail: {focus[:1000]}" if focus else "")}],
            max_tokens=2800, temperature=0.1, task_id="compact", role="worker")
        return (r["choices"][0]["message"].get("content") or "").strip()[:MAX_NOTE]
    except Exception:  # noqa: BLE001 - a failed summary must never stop the task
        return ""


def compact_history(messages: list, client=None, model: str | None = None, keep_recent: int = KEEP_RECENT,
                    focus: str = "") -> dict | None:
    """Replace the older steps in `messages` (in place) with the handoff note. Returns {"removed": n, "model": bool}
    or None when nothing was done."""
    parts = split(messages, keep_recent)
    if parts is None:
        return None
    head, middle, tail = parts
    task = _text(head[-1].get("content")) if head[-1].get("role") == "user" else ""
    written = summarize(middle, task, client, model, focus)
    rules = rule_notes(middle)
    note = ("[Earlier steps of this task were summarized to save space. The summary and the log facts below are "
            "accurate; the user's task above is unchanged.]\n"
            + (written + "\n\n" if written else "(The summary could not be written; the facts from the log follow.)\n")
            + (rules + "\n" if rules else "")
            + "[End of summary. Continue from the messages below. Do not redo finished work.]")
    messages[:] = head + [{"role": "user", "content": note}] + tail
    return {"removed": len(middle), "model": bool(written)}
