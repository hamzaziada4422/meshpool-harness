"""Bring old chats and sessions into Meshpool as real threads: listed in the sidebar by their original date,
searchable (search_chats, the sidebar) and continuable with any agent.

Sources:
  - a ChatGPT data export (.zip with conversations.json): every conversation, the branch that was showing
  - a Claude data export (.zip with conversations.json and projects.json): every conversation
  - Claude Code sessions on this computer (~/.claude/projects/<folder>/<session>.jsonl), in their project folders
  - Codex sessions on this computer (~/.codex/sessions/**/rollout-*.jsonl), in their project folders

Only what was said is kept: the user's messages and the assistant's replies, with a short note for each tool the
assistant used. Tool outputs, hidden system text and reasoning are left out. Importing again skips threads that are
already there, so it is safe to repeat. Nothing leaves this computer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from meshpool_harness import fsio
from meshpool_harness import session_store as SS
from meshpool_harness.agent.history_repair import repair_tool_pairs

MAX_MSG = 20000                     # characters kept per message
MAX_MSGS = 400                      # messages kept per conversation (the most recent)


# ---------- helpers ------------------------------------------------------------------------------------------------

def _ts(v) -> float:
    """Epoch seconds from a number, an ISO string or nothing."""
    if isinstance(v, (int, float)):
        return float(v) / (1000.0 if v > 1e12 else 1.0)
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _clean(t: str) -> str:
    t = (t or "").replace("\r\n", "\n").strip()
    return t[:MAX_MSG] + ("\n[... cut for length]" if len(t) > MAX_MSG else "")


def _title(t: str, first_user: str) -> str:
    t = (t or "").strip() or re.sub(r"\s+", " ", first_user or "").strip()[:70] or "Imported chat"
    return t[:120]


def _merge(msgs: list[dict]) -> list[dict]:
    """Join consecutive messages from the same side (exports split one reply into several parts)."""
    out: list[dict] = []
    for m in msgs:
        if not m["content"].strip():
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n\n" + m["content"]
        else:
            out.append(dict(m))
    return out[-MAX_MSGS:]


def _conv(source: str, oid: str, title: str, created: float, updated: float, msgs: list[dict],
          workdir: str | None = None, kind: str = "cowork") -> dict | None:
    msgs = _merge(msgs)
    if not any(m["role"] == "user" for m in msgs):
        return None
    first = next(m["content"] for m in msgs if m["role"] == "user")
    return {"source": source, "id": oid, "title": _title(title, first), "created": created or updated or time.time(),
            "updated": updated or created or time.time(), "messages": msgs, "workdir": workdir, "kind": kind}


# ---------- ChatGPT export -----------------------------------------------------------------------------------------

def _chatgpt_text(m: dict) -> str:
    c = m.get("content") or {}
    ct = c.get("content_type")
    if ct in ("text", "multimodal_text"):
        parts = []
        for p in c.get("parts") or []:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                kind = p.get("content_type") or ""
                parts.append("[image]" if "image" in kind else "[attachment]" if kind else "")
        return "\n".join(x for x in parts if x)
    return ""


def parse_chatgpt(convs: list) -> list[dict]:
    out = []
    for c in convs or []:
        mapping = c.get("mapping") or {}
        node = c.get("current_node")
        chain = []
        seen = set()
        while node and node in mapping and node not in seen:       # the branch the user was looking at
            seen.add(node)
            chain.append(mapping[node])
            node = mapping[node].get("parent")
        if not chain:                                                 # no current node: take the mapping order
            chain = list(mapping.values())
        else:
            chain.reverse()
        msgs = []
        for n in chain:
            m = (n or {}).get("message") or {}
            role = (m.get("author") or {}).get("role")
            meta = m.get("metadata") or {}
            if role not in ("user", "assistant") or meta.get("is_visually_hidden_from_conversation"):
                continue
            if role == "assistant" and m.get("recipient", "all") != "all":
                continue                                              # a call to a tool, not a reply
            text = _chatgpt_text(m)
            if text.strip():
                msgs.append({"role": role, "content": _clean(text)})
        cv = _conv("chatgpt", str(c.get("conversation_id") or c.get("id") or c.get("title") or len(out)),
                   c.get("title") or "", _ts(c.get("create_time")), _ts(c.get("update_time")), msgs)
        if cv:
            out.append(cv)
    return out


# ---------- Claude export ------------------------------------------------------------------------------------------

def parse_claude(convs: list) -> list[dict]:
    out = []
    for c in convs or []:
        msgs = []
        for m in c.get("chat_messages") or []:
            role = {"human": "user", "assistant": "assistant"}.get(m.get("sender"))
            if not role:
                continue
            blocks = m.get("content") or []
            text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text") \
                if blocks else ""
            text = text or m.get("text") or ""
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    text += f"\n[Used {b.get('name', 'a tool')}]"
            for a in m.get("attachments") or []:
                body = (a.get("extracted_content") or "").strip()
                text += f"\n\n[Attached: {a.get('file_name', 'file')}]" + (f"\n{body[:4000]}" if body else "")
            for f in m.get("files") or []:
                text += f"\n[File: {f.get('file_name', 'file')}]"
            if text.strip():
                msgs.append({"role": role, "content": _clean(text)})
        cv = _conv("claude", str(c.get("uuid") or c.get("name") or len(out)), c.get("name") or "",
                   _ts(c.get("created_at")), _ts(c.get("updated_at")), msgs)
        if cv:
            out.append(cv)
    return out


# ---------- Claude Code and Codex sessions on this computer -------------------------------------------------------

def claude_code_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"


def _jsonl(p: Path):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except OSError:
        return


def _short(v, n: int = 120) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] + ("..." if len(s) > n else "")


_CC_SKIP = re.compile(r"^\s*(<command-name>|<command-message>|<local-command-stdout>|<system-reminder>|Caveat: The "
                      r"messages below|<bash-input>|<bash-stdout>|\[Request interrupted)")


def parse_claude_code(path: Path) -> dict | None:
    msgs, title, cwd, t0, t1 = [], "", None, 0.0, 0.0
    for o in _jsonl(path):
        if o.get("type") == "summary" and o.get("summary"):
            title = title or o["summary"]
            continue
        if o.get("type") not in ("user", "assistant") or o.get("isSidechain") or o.get("isMeta"):
            continue
        cwd = cwd or o.get("cwd")
        t = _ts(o.get("timestamp"))
        t0, t1 = t0 or t, t or t1
        m = o.get("message") or {}
        c = m.get("content")
        parts = []
        if isinstance(c, str):
            if not _CC_SKIP.match(c):
                parts.append(c)
        for b in c if isinstance(c, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and not _CC_SKIP.match(b.get("text", "")):
                parts.append(b.get("text", ""))
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                arg = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or inp
                parts.append(f"[{b.get('name', 'tool')}: {_short(arg)}]")
        text = "\n".join(p for p in parts if p.strip())
        if text.strip():
            msgs.append({"role": "user" if o["type"] == "user" else "assistant", "content": _clean(text)})
    wd = cwd if cwd and Path(cwd).is_dir() else None
    return _conv("claude-code", path.stem, title, t0, t1, msgs, wd, "code")


_CX_SKIP = re.compile(r"^\s*(<environment_context>|<user_instructions>|<user_shell_command>|# AGENTS\.md)")


def parse_codex(path: Path) -> dict | None:
    msgs, cwd, t0, t1, oid = [], None, 0.0, 0.0, path.stem
    for o in _jsonl(path):
        p = o.get("payload") if isinstance(o.get("payload"), dict) else o
        t = _ts(o.get("timestamp") or p.get("timestamp"))
        if o.get("type") == "session_meta" or ("instructions" in p and "id" in p and "type" not in p):
            cwd = cwd or p.get("cwd")                # the file name is the id (rollout-<time>-<uuid>), as scan_local uses
            continue
        if p.get("type") == "message" and p.get("role") in ("user", "assistant"):
            text = "\n".join(c.get("text", "") for c in p.get("content") or []
                             if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text"))
            if not text.strip() or _CX_SKIP.match(text):
                continue
            t0, t1 = t0 or t, t or t1
            msgs.append({"role": p["role"], "content": _clean(text)})
        elif p.get("type") in ("function_call", "local_shell_call", "custom_tool_call"):
            args = p.get("arguments") or p.get("input") or (p.get("action") or {}).get("command") or ""
            try:
                a = json.loads(args) if isinstance(args, str) else args
                args = (a.get("command") if isinstance(a, dict) else a) or args
            except ValueError:
                pass
            msgs.append({"role": "assistant", "content": f"[{p.get('name') or 'shell'}: {_short(args)}]"})
    wd = cwd if cwd and Path(cwd).is_dir() else None
    return _conv("codex", oid, "", t0, t1, msgs, wd, "code")


def scan_local() -> dict:
    """How many sessions each coding agent has on this computer, and how many are already in Meshpool."""
    have = _existing()
    cc = sorted(claude_code_dir().glob("*/*.jsonl")) if claude_code_dir().is_dir() else []
    cx = sorted(codex_dir().rglob("*.jsonl")) if codex_dir().is_dir() else []
    out = {"claude-code": {"name": "Claude Code", "found": len(cc), "path": str(claude_code_dir()),
                           "imported": sum(1 for p in cc if _tid("claude-code", p.stem) in have)},
           "codex": {"name": "Codex", "found": len(cx), "path": str(codex_dir()),
                     "imported": sum(1 for p in cx if _tid("codex", p.stem) in have)}}
    for v in out.values():                                   # the first-run step ticks what is there and not yet in
        v["default"] = v["found"] > v["imported"]
    return out


# ---------- writing threads ----------------------------------------------------------------------------------------

def _sessions() -> Path:
    from meshpool_harness import config as C
    return C.runs_dir() / "sessions"


def _tid(source: str, oid: str) -> str:
    return f"imp-{source}-" + hashlib.sha1(f"{source}:{oid}".encode()).hexdigest()[:12]


def _complete(d: Path) -> bool:
    """A conversation folder that was written whole: a readable session.json and its meta.json."""
    try:
        if not (d / "meta.json").is_file():
            return False
        data = json.loads((d / "session.json").read_text(encoding="utf-8"))
        return isinstance(data, dict) and bool(data.get("id"))
    except (OSError, ValueError):
        return False


def _existing() -> set:
    """Imported threads already here. A folder a crash left half-written (no readable session.json or meta.json) does
    not count: it is cleared away so the next import writes it again (M21-01)."""
    d = _sessions()
    out = set()
    if d.is_dir():
        for p in d.iterdir():
            if p.name.startswith(".imp-") and p.name.endswith(".tmp"):
                shutil.rmtree(p, ignore_errors=True)               # an import that never finished
            elif p.name.startswith("imp-"):
                if _complete(p):
                    out.add(p.name)
                else:
                    shutil.rmtree(p, ignore_errors=True)
    return out


def _tomb_path() -> Path:
    from meshpool_harness import config as C
    return C.home() / "imported-deleted.json"


def tombstones() -> dict:
    """Imported chats the person deleted, by thread id (made from the source's own id): a new export that holds them
    again does not bring them back (M21-02)."""
    try:
        d = fsio.read_json(_tomb_path(), default={})
        return d if isinstance(d, dict) else {}
    except fsio.CorruptFile:
        return {}


def bury(tid: str) -> None:
    """Remember that this imported chat was deleted."""
    if not str(tid).startswith("imp-"):
        return

    def add(cur):
        cur = cur if isinstance(cur, dict) else {}
        cur[tid] = time.time()
        return cur
    try:
        fsio.update_json(_tomb_path(), add, default={})
    except (fsio.CorruptFile, OSError, TimeoutError):
        pass


def _default_folder() -> str:
    from meshpool_harness.ui.desktop import default_project
    p = default_project()
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def write_threads(convs: list[dict]) -> dict:
    """Save conversations as Meshpool threads. Ones already imported are skipped (their id comes from the source), and
    so are ones the person deleted. Each is written under a temporary name and renamed when whole, so a crash never
    leaves a folder that later imports mistake for finished."""
    have = _existing()
    gone = tombstones()
    folder = None
    done = {"imported": 0, "skipped": 0, "removed_earlier": 0, "messages": 0, "ids": []}
    for cv in convs:
        tid = _tid(cv["source"], cv["id"])
        if tid in have:
            done["skipped"] += 1
            continue
        if tid in gone:
            done["removed_earlier"] += 1
            done["skipped"] += 1
            continue
        if not cv["workdir"]:
            folder = folder or _default_folder()
        d = _sessions() / tid
        tmp = _sessions() / f".{tid}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        code = cv["kind"] == "code"
        try:
            (tmp / "meta.json").write_text(json.dumps({"imported": cv["source"]}), encoding="utf-8")
            SS.write_snapshot(tmp, {
                "id": tid, "workdir": cv["workdir"] or folder, "title": cv["title"], "created": cv["created"],
                "updated": cv["updated"], "model": None, "model_choice": None, "plan": False,
                "harness": "meshpool-code" if code else "meshpool-work", "kind": "code" if code else "cowork",
                "usage": {"in": 0, "cached": 0, "out": 0, "calls": 0}, "messages": repair_tool_pairs(cv["messages"])[0],
                "imported": {"source": cv["source"], "id": cv["id"]}})
            os.utime(tmp / "session.json", (cv["updated"], cv["updated"]))
            shutil.rmtree(d, ignore_errors=True)
            os.replace(tmp, d)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        have.add(tid)
        done["imported"] += 1
        done["messages"] += len(cv["messages"])
        done["ids"].append(tid)
    done["summary"] = summary(done)
    return done


def _status_path() -> Path:
    from meshpool_harness import config as C
    return C.home() / "import-status.json"


def set_context_status(state: str, error: str = "", result: dict | None = None) -> None:
    """How the background read of an export's context went, for the page to show (M21-04)."""
    row = {"state": state, "error": error, "at": time.time()}
    if result:
        row.update({k: result[k] for k in ("memory", "instructions", "skills") if k in result})
    try:
        fsio.atomic_write_json(_status_path(), row)
    except OSError:
        pass


def context_status() -> dict:
    try:
        d = fsio.read_json(_status_path(), default={})
        return d if isinstance(d, dict) else {}
    except fsio.CorruptFile:
        return {}


def summary(r: dict) -> str:
    """What an import did, in one sentence: how many are new and how many were here already (F-33)."""
    new, old, back = int(r.get("imported", 0)), int(r.get("skipped", 0)), int(r.get("removed_earlier", 0))
    s = (f"{new} new chat{'s' if new != 1 else ''} imported" if new else "Nothing new to import")
    if old - back:
        s += f"; {old - back} {'was' if old - back == 1 else 'were'} already here"
    if back:
        s += f"; {back} you deleted earlier {'was' if back == 1 else 'were'} left out"
    return s + "."


# ---------- entry points -------------------------------------------------------------------------------------------

def _all_json(z, pattern: str) -> list:
    rows = []
    for n in sorted(z.namelist()):
        if re.fullmatch(pattern, n.rsplit("/", 1)[-1]):
            data = json.loads(z.read(n).decode("utf-8", errors="replace"))
            rows += data if isinstance(data, list) else (data.get("conversations") or [])
    return rows


def import_export(path: str, chats: bool = True, context: bool = True, client=None) -> dict:
    """A ChatGPT or Claude export: the chats become threads; projects become skills and lasting facts become memory
    (importer.import_export). Returns counts for both."""
    import zipfile
    from meshpool_harness import importer
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError("That file is not a .zip. Use the file ChatGPT or Claude emailed you, without unzipping it.")
    kind = importer._export_kind(z)
    if kind == "unknown":
        raise ValueError("This does not look like a ChatGPT or Claude export (no conversations.json inside).")
    out = {"kind": kind, "imported": 0, "skipped": 0, "messages": 0, "conversations_seen": 0,
           "skills": [], "memory": 0, "instructions": 0}
    convs = _all_json(z, r"conversations(-\d+)?\.json")
    out["conversations_seen"] = len(convs)
    if chats:
        out.update(write_threads(parse_chatgpt(convs) if kind == "chatgpt" else parse_claude(convs)))
    else:
        out["summary"] = summary(out)
    if context:
        r = importer.import_export(path, client=client)
        out.update({k: r[k] for k in ("skills", "memory", "instructions", "skipped_context") if k in r},
                   context_skipped=r.get("skipped", []))
    return out


def import_local(sources: list[str] | None = None) -> dict:
    """Claude Code and Codex sessions from this computer."""
    sources = sources or ["claude-code", "codex"]
    convs = []
    if "claude-code" in sources and claude_code_dir().is_dir():
        convs += [c for c in (parse_claude_code(p) for p in claude_code_dir().glob("*/*.jsonl")) if c]
    if "codex" in sources and codex_dir().is_dir():
        convs += [c for c in (parse_codex(p) for p in codex_dir().rglob("*.jsonl")) if c]
    r = write_threads(convs)
    r["by_source"] = {s: sum(1 for c in convs if c["source"] == s) for s in sources}
    return r
