"""Terminal helpers added with the feature-ledger work: prompt history kept between sessions, the session list for
/resume, the context breakdown for /context, the transcript for /export and the clipboard for /copy."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from meshpool_harness import config as C

HISTORY_KEEP = 500


# ---------------------------------------------------------------- prompt history
def _history_file() -> Path:
    return C.home() / "prompt-history.jsonl"


def load_history(cwd: str, limit: int = HISTORY_KEEP) -> list[str]:
    """Earlier prompts typed in this folder, oldest first (Up arrow walks back through them)."""
    if os.environ.get("MH_HISTORY", "1") == "0":
        return []
    root = os.path.realpath(cwd)
    out = []
    try:
        for line in _history_file().read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("cwd") == root and isinstance(d.get("text"), str):
                out.append(d["text"])
    except OSError:
        return []
    dedup: list[str] = []
    for t in out:                                        # the same prompt twice in a row counts once
        if not dedup or dedup[-1] != t:
            dedup.append(t)
    return dedup[-limit:]


def add_history(cwd: str, text: str) -> None:
    if os.environ.get("MH_HISTORY", "1") == "0" or not text.strip() or len(text) > 20000:
        return
    f = _history_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"cwd": os.path.realpath(cwd), "text": text, "ts": int(time.time())},
                                ensure_ascii=False) + "\n")
        if f.stat().st_size > 4_000_000:                  # keep the newest half when the file grows large
            lines = f.read_text(encoding="utf-8").splitlines()
            tmp = f.with_suffix(".tmp")
            tmp.write_text("\n".join(lines[len(lines) // 2:]) + "\n", encoding="utf-8")
            os.replace(tmp, f)
        if os.name != "nt":
            os.chmod(f, 0o600)                            # prompts can hold private details
    except OSError:
        pass


# ---------------------------------------------------------------- sessions for /resume
def sessions(root: str | None = None, limit: int = 30) -> list[dict]:
    """Saved terminal and app sessions, newest first: id, title, workdir, updated, messages. root limits them to one
    folder. Sub-agents' sessions are left out."""
    from meshpool_harness.agent.core import SESSIONS
    want = os.path.realpath(root) if root else None
    out = []
    if not SESSIONS.exists():
        return out
    for p in SESSIONS.glob("*/session.json"):
        if p.parent.name.startswith("sub-"):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("harness") not in (None, "meshpool-code", "meshpool-work"):
            continue
        if want and os.path.realpath(str(d.get("workdir") or "")) != want:
            continue
        msgs = d.get("messages") or []
        first = next((m.get("content") for m in msgs if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
        out.append({"id": d.get("id") or p.parent.name, "title": d.get("title") or (first or "").split("​")[0][:60],
                    "workdir": d.get("workdir") or "", "updated": float(d.get("updated") or 0),
                    "messages": len(msgs)})
    out.sort(key=lambda x: -x["updated"])
    return out[:limit]


def ago(ts: float) -> str:
    s = max(0, time.time() - ts)
    for n, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if s >= n:
            return f"{int(s // n)}{unit} ago"
    return "just now"


# ---------------------------------------------------------------- /context
def _chars(x) -> int:
    if isinstance(x, str):
        return len(x)
    if isinstance(x, list):
        return sum(_chars(p.get("text", "")) if isinstance(p, dict) else _chars(p) for p in x)
    return len(json.dumps(x, ensure_ascii=False, default=str)) if x is not None else 0


def context_breakdown(agent) -> list[tuple[str, int]]:
    """What fills the context, as (part, estimated tokens): the system prompt and its parts, tool definitions,
    and the conversation by role. Estimates use four characters per token."""
    msgs = list(getattr(agent, "messages", []) or [])
    rows: list[tuple[str, int]] = []
    sys_text = msgs[0]["content"] if msgs and msgs[0].get("role") == "system" and isinstance(msgs[0].get("content"), str) else ""
    try:
        from meshpool_harness.agent import extras as EX
        ins = len(EX.instruction_text(agent.root))
    except Exception:  # noqa: BLE001
        ins = 0
    skills = 0
    i = sys_text.find("Skills")
    if i >= 0:
        skills = min(len(sys_text) - i, 20000)
    base = max(0, len(sys_text) - ins - skills)
    rows += [("System prompt", base // 4), ("Instruction files (MESHPOOL.md, AGENTS.md, CLAUDE.md)", ins // 4),
             ("Skills list", skills // 4)]
    try:
        tools = len(json.dumps(agent.tool_schemas(), ensure_ascii=False))
    except Exception:  # noqa: BLE001
        tools = 0
    rows.append(("Tool definitions", tools // 4))
    by = {"user": 0, "assistant": 0, "tool": 0}
    images = 0
    for m in msgs[1:]:
        c = m.get("content")
        if isinstance(c, list):
            images += sum(1 for p in c if isinstance(p, dict) and p.get("type") == "image_url")
        by[m.get("role", "user") if m.get("role") in by else "user"] += _chars(c) + _chars(m.get("tool_calls") or "")
    rows += [("Your messages", by["user"] // 4), ("Replies and tool calls", by["assistant"] // 4),
             ("Tool results", by["tool"] // 4)]
    if images:
        rows.append((f"Images ({images})", images * 1500))
    return rows


# ---------------------------------------------------------------- /export and /copy
def transcript(agent) -> str:
    """The conversation as Markdown: your messages, the replies, and each tool call with a short result."""
    lines = [f"# {getattr(agent, 'title', '') or 'Meshpool session'}", "",
             f"Folder: {getattr(agent, 'root', '')}  ", f"Exported: {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for m in (getattr(agent, "messages", []) or [])[1:]:
        role, c = m.get("role"), m.get("content")
        text = c if isinstance(c, str) else "\n".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
        text = (text or "").split("​")[0].strip()
        if role == "user":
            if text.startswith("[Meshpool]"):
                continue
            lines += ["## You", "", text, ""]
        elif role == "assistant":
            if text:
                lines += ["## Meshpool", "", text, ""]
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                lines += [f"- Tool `{fn.get('name', '')}`: `{str(fn.get('arguments', ''))[:200]}`"]
        elif role == "tool":
            short = text.splitlines()[0][:200] if text else ""
            lines += [f"  - Result: {short}", ""]
    return "\n".join(lines).rstrip() + "\n"


def replies(agent) -> list[str]:
    return [m["content"] for m in (getattr(agent, "messages", []) or [])
            if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip()]


def copy_to_clipboard(text: str, out=None) -> str:
    """Copies text with the system clipboard tool; over SSH or without one, with the terminal's OSC 52 sequence.
    Returns how it was copied, or "" when it could not be."""
    cmds = [["pbcopy"]] if sys.platform == "darwin" else [["clip"]] if os.name == "nt" else \
        [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    if not os.environ.get("SSH_CONNECTION"):
        for c in cmds:
            if shutil.which(c[0]):
                try:
                    subprocess.run(c, input=text.encode("utf-8" if os.name != "nt" else "utf-16-le"), timeout=5,
                                   check=True, capture_output=True)
                    return c[0]
                except (OSError, subprocess.SubprocessError):
                    continue
    try:
        (out or sys.stdout).write("\x1b]52;c;" + base64.b64encode(text.encode("utf-8")).decode() + "\x07")
        (out or sys.stdout).flush()
        return "terminal"
    except OSError:
        return ""
