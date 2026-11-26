"""Agent sessions for the web app: each session is an Agent running in a thread, with its own MCP servers.
The page polls for events and answers approval requests."""
from __future__ import annotations

import errno
import json
import re
import os
import threading
import time
import uuid
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness import session_store as SS
from meshpool_harness.agent.history_repair import repair_tool_pairs
from meshpool_harness.agent import mcp as M
from meshpool_harness.agent.core import SESSIONS, Agent
from meshpool_harness import harnesses as HR
from meshpool_harness.core import usage
from meshpool_harness.harnesses.acp import ACPAgent
from meshpool_harness.harnesses.pi_rpc import PiAgent

LIVE: dict[str, "Session"] = {}

# Update gate: while an update is being installed no turn or scheduled run may start (M11-02).
GATE = threading.Lock()
QUIESCING = [False]
_GATE_MSG = "meshpool is restarting to install an update. Send this again in a moment."


def quiesce() -> bool:
    """Close the gate if nothing is running. False (gate left open) while any live chat works or waits."""
    with GATE:
        if any(s.state in ("working", "waiting") for s in list(LIVE.values())):
            return False
        QUIESCING[0] = True
        return True


def resume() -> None:
    with GATE:
        QUIESCING[0] = False


def _claim_working(sess) -> None:
    """Set a session to "working" under the gate, or refuse while an update is being installed."""
    with GATE:
        if QUIESCING[0]:
            raise ValueError(_GATE_MSG)
        sess.state = "working"
LOCK = threading.Lock()
_SKIP = {"text_delta", "stream_reset", "model_start", "model_end", "usage", "context", "queued", "tool_output"}
# A text_delta of a finished reply (Session._fold_deltas): one shared, never-mutated marker in place of each, so event
# positions stay put. It has no "i"; outward views (Session.view, the event stream) leave it out.
_DELTA_GONE = {"type": "text_delta", "text": "", "gone": True}
# FL-R6a: the same for a streamed piece of the model's reasoning once the call's saved copy (thought, final) arrived.
_THOUGHT_GONE = {"type": "thought", "text": "", "delta": True, "gone": True}
_RUN_MCP_SPEC: list = []    # meshpool --mcp-config for this terminal run, read once (run_mcp_spec)


def run_mcp_spec() -> str:
    """meshpool --mcp-config: the servers for this terminal run only. FL-V17: read once and taken out of the
    environment, so commands the agent runs, MCP servers and other child runs (a nested meshpool -p) do not start
    them too. Chats opened later in the same terminal (/clear, /resume) still get them."""
    if os.environ.get("MESHPOOL_FRONTEND") != "terminal":
        return ""
    if "MH_MCP_CONFIG" in os.environ:
        _RUN_MCP_SPEC[:] = [os.environ.pop("MH_MCP_CONFIG")]
    return _RUN_MCP_SPEC[0] if _RUN_MCP_SPEC else ""
# /goal: the agent keeps taking turns until it says the goal is met. The note is hidden in the chat (after \u200b).
GOAL_NOTE = ("\n\n\u200b[Meshpool] The goal of this chat: {goal}\nKeep working until it is fully done and checked. End your "
             "reply with one last line: \"GOAL: DONE\" once the goal is met and verified, or \"GOAL: CONTINUE\" and "
             "the next step if work remains. If you need the user to decide or provide something, ask, and leave the "
             "GOAL line out.")
GOAL_NEXT = "Continue working toward the goal."
GOAL_NET_WAIT_S = 30                                 # first pause before a goal picks up after an outage (doubles, 5 min max)


def agent_token(agent) -> int:
    f = getattr(agent, "turn_token", None)
    return f() if callable(f) else 0


_GOAL_RE = re.compile(r"^\W*GOAL:\s*(DONE|CONTINUE)\b", re.I | re.M)
_NET_RE = re.compile(r"Couldn't reach the model|model service had a problem|model is busy|HTTP 0|HTTP 5\d\d|HTTP 429|"
                     r"rate.?limit|too many requests|(?:code|status)\W{0,3}429\b|overloaded",
                     re.I)                          # an outage or a key whose slots are full: wait it out


def _turn_outcome(evs: list) -> dict:
    """How a turn ENDED, from its events. An error counts only if nothing came after it: a later reply with text,
    a text delta or a tool start/end means the agent recovered (Pi reports an error before its own retry)."""
    err, acted, reply = None, False, ""
    for e in evs:
        t = e.get("type")
        if t == "error":
            err = e
        elif err is not None and (t in ("text_delta", "tool_start", "tool_end")
                                  or (t == "assistant" and (e.get("text") or "").strip())):
            err = None
        if t == "tool_end":
            acted = True
        if t == "assistant":
            reply = e.get("text") or ""
    return {"error": err, "interrupted": any(e.get("type") == "interrupted" for e in evs),
            "net": bool(err and _NET_RE.search(err.get("text") or "")), "acted": acted, "reply": reply}
# A turn cut off by a rate limit or an outage (no goal set): the chat waits, then picks the task up by itself, up to
# RESUME_TRIES times in a row (30 s, 1, 2, 4, 5 min). Typing a message or pressing Stop takes over at once.
RESUME_NEXT = "Continue where you left off."
RESUME_WAIT_S = 30
RESUME_TRIES = 5

_KEEP = {"user", "assistant", "offer", "tool_end", "interrupted", "error", "turn_end", "approval", "approval_done",
         "checkpoint", "status", "plan", "check", "checks_line", "task_receipt", "insight", "review", "retry_ok",
         "review_round"}
# A step's files, for "a later step redid it" (Session._mark_retried_steps): names with an extension in its summary.
_STEP_FILE = re.compile(r"[\w][\w ()+-]*\.(?:xlsx|xlsm|xls|csv|tsv|docx|doc|pptx|ppt|pdf|md|txt|html?|json|png|jpe?g|"
                        r"svg|py|js|ts)\b", re.I)
_MAKER = re.compile(r"^(?:make_|write|edit|create|apply_patch|office_task|add_slides|convert|save)", re.I)
_SHELL = re.compile(r"bash|shell|exec|command|terminal", re.I)


def _files_named(e: dict) -> set:
    """The file names a step's summary or arguments mention, lower case. "from rooms.xlsx" also gives "rooms.xlsx":
    a name may hold spaces, so every tail of the words before the extension is a candidate."""
    args = e.get("args")
    text = " ".join([str(e.get("summary") or ""), json.dumps(args) if isinstance(args, (dict, list)) else str(args or "")])
    out = set()
    for m in _STEP_FILE.finditer(text):
        name = re.split(r"[\\/]", m.group(0).strip())[-1]
        words = name.split()
        out.update(" ".join(words[i:]).lower() for i in range(len(words)))
    return out


# Transient "trying again" notices (core.py): tagged retry:true; a retry_ok event follows once the model answers.
_RETRY_RE = re.compile(r"Trying again \(\d+ of \d+\)|^Rate limited by the endpoint|^Trying again without streaming|"
                       r"^Usage limit reached")
_RETRY_DONE = ("usage", "model_end", "text_delta", "assistant", "tool_start")
_BUSY = ("working", "waiting")
# Turn state machine (Session._set_state): the only moves a chat's state may make.
_MOVES = {("starting", "idle"), ("starting", "working"), ("idle", "working"), ("working", "waiting"),
          ("waiting", "working"), ("working", "idle"), ("waiting", "idle")}


def _transcript(sid: str) -> dict | None:
    try:
        return SS.TranscriptLog(SESSIONS / sid).read()
    except OSError:
        return None
DELETED: set[str] = set()                            # deleted this run: never reopened, even by a late page poll


def _messages_from_events(events: list) -> list:
    """The conversation (what the user and the agent said) from an ACP thread's saved events, so a chat from an agent
    Meshpool no longer has can go on in Meshpool's own agent with its context."""
    out = []
    for e in events:
        if e.get("type") in ("user", "assistant") and str(e.get("text") or "").strip():
            out.append({"role": e["type"], "content": str(e["text"])})
    return out


def _with_plain(e: dict) -> dict:
    """A step event with its plain title for Chat mode (core.plain_step, built on the approval cards' own titles), so the
    page shows "Created open-house.html" and keeps the command itself in the step's details."""
    if e.get("type") != "tool_start" or "plain" in e:
        return e
    try:
        from meshpool_harness.agent.core import plain_step
        p = plain_step(e.get("name") or "", e.get("summary") or "", bool(e.get("mcp")))
    except Exception:  # noqa: BLE001 - a title is a help, never a second failure
        p = None
    return dict(e, plain=p) if p else e


def _approval_view(kind, title, detail, clean=None) -> dict:
    """The plain line and file preview of an approval card (core.approval_view), redacted like the card's detail."""
    try:
        from meshpool_harness.agent.core import approval_view
        v = approval_view(kind, title, detail)
        if clean and v.get("file"):
            v["file"] = dict(v["file"], text=clean(v["file"]["text"]))
        return v
    except Exception:  # noqa: BLE001 - the plain view is a help, never a second failure
        return {}


def _redact(text: str) -> str:
    from meshpool_harness.errors import tool_card    # a reopened chat's cards read like the live ones
    from meshpool_harness.redact import redact
    return redact(tool_card(text))


def undo_note(r: dict, replies: int = 1, files: bool = True) -> str:
    """The note the model gets after Undo: which of its replies the user undid and what happened to the files.
    "The user undid your previous reply. These files were restored to how they were before it: Bouquet Price
    List.xlsx." Each file is named by its path in the chat's folder."""
    def names(xs):
        seen = []
        for x in xs or []:
            x = re.sub(r"^(\./)+", "", str(x).replace("\\", "/").strip())
            if x and x not in seen:
                seen.append(x)
        return ", ".join(seen[:8]) + (f" and {len(seen) - 8} more" if len(seen) > 8 else "")
    n = max(1, int(replies or 1))
    what, it = ("your previous reply", "it") if n == 1 else (f"your last {n} replies", "them")
    out = [f"The user undid {what}."]
    if not files:
        out.append(f"The files {it} changed were kept as they are now.")
        return " ".join(out)
    restored, deleted, kept = names(r.get("restored")), names(r.get("deleted")), names(
        list(r.get("kept") or []) + list(r.get("outside") or []))
    if restored:
        out.append(f"These files were restored to how they were before {it}: {restored}.")
    if deleted:
        out.append(f"These files {it} made were removed: {deleted}.")
    if kept:
        out.append(f"These files were not put back and are as they are now: {kept}.")
    if not (restored or deleted or kept):
        out.append("No files were changed back.")
    return " ".join(out)


def undo_message(r: dict) -> tuple[str, str]:
    """(the line the chat shows after Undo, the details behind it). The line names the files Undo restored or removed
    and every changed file it could not put back; the folder holding copies of what Undo replaced is only in the
    details (WF-07: a full checkpoints path filled the chat; WF-C: "Files are back to how they were" while a file
    outside the chat's folder still had the agent's change)."""
    def once(xs):
        """Each file once, whatever order it came in: the same path listed twice (a file kept and also reported as
        outside, or a list that repeated itself) is one file ("Not restored: a, b, a, b")."""
        seen, out = set(), []
        for x in xs:
            key = re.sub(r"^(\./)+", "", str(x).replace("\\", "/").strip())
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    def named(x):
        """A file as people read it: its name, and the folder it is in when it is not at the top ("a.xlsx (in Prices)")."""
        name = Path(x).name or x
        folder = str(Path(x).parent).replace("\\", "/")
        return name if folder in ("", ".") else f"{name} (in {folder})"

    def names(xs, n=4):
        xs = [named(x) for x in once(xs)]
        return ", ".join(xs[:n]) + (f" and {len(xs) - n} more" if len(xs) > n else "")

    def count(xs, word):
        n = len(once(xs))
        return f"{word} {n} file{'' if n == 1 else 's'} ({names(xs)})"
    restored, deleted, kept, outside = (once(r.get(k) or []) for k in ("restored", "deleted", "kept", "outside"))
    done = [s for s in (count(restored, "Restored") if restored else "",
                        count(deleted, "removed" if restored else "Removed") if deleted else "") if s]
    if not done and r.get("conversation") is False and not kept and not outside:
        # OM-08: nothing left the conversation and no file changed: "Undone." said something happened that did not
        return "Nothing to undo: that turn changed no files and is no longer in the conversation.", ""
    from meshpool_harness.checkpoints import CHANGED_AFTER
    notes = [str(n) for n in r.get("notes") or []]
    why = {p: next((n[len(p) + 2:] for n in notes if n.startswith(f"{p}: ")), "") for p in kept}
    # Maya's Flowers: "No files in this chat's folder had changed. Not restored: Bouquet Price List.xlsx" said two
    # things that cannot both be true. A file the turn changed but Undo kept is named as kept, never as unchanged.
    if done:
        msg = "Undone. " + " and ".join(done) + "."
    elif kept or outside:
        msg = "Undone. No files were put back."
    else:
        msg = "Undone. No files in this chat's folder had changed."
    if kept:
        one = len(kept) == 1
        if all(not w or w == CHANGED_AFTER for w in why.values()):
            msg += (f" Kept as {'it is' if one else 'they are'}: {names(kept)}, because {'it' if one else 'they'} "
                    "changed after the agent's edit.")
        else:
            msg += f" Kept as {'it is' if one else 'they are'}: {names(kept)} (see details for why)."
    if outside:
        msg += f" Not restored, because Undo only covers this chat's folder: {names(outside)}."
    det = list(notes)                        # why each file was kept: changed later, never stored, a link...
    det += [f"{p}: {CHANGED_AFTER}" for p in kept if not why[p]]
    if outside:
        det.append("Changed outside this chat's folder, not restored: " + ", ".join(Path(x).name or x for x in once(outside)))
    if r.get("backup"):                      # the folder of saved copies is not shown: nothing technical in the details
        det.append("meshpool kept a copy of what Undo replaced, in case you change your mind.")
    return msg, "\n".join(det)


def events_from_messages(messages: list) -> list:
    """Rebuild a transcript from saved messages (for sessions reopened after the app restarted)."""
    evs, outputs = [], {m.get("tool_call_id"): m.get("content", "") for m in messages if m.get("role") == "tool"}
    for m in messages:
        r = m.get("role")
        if r == "user":
            c = m.get("content", "")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            evs.append({"type": "user", "text": str(c).split("\n\n\u200b")[0]})
        elif r == "assistant":
            if m.get("content") and m["content"] != "(no reply)":
                evs.append({"type": "assistant", "text": m["content"], "streamed": False})
            for c in m.get("tool_calls") or []:
                f = c.get("function") or {}
                try:
                    from meshpool_harness.agent.core import _summary
                    summ = _summary(f.get("name", ""), json.loads(f.get("arguments") or "{}"))
                except ValueError:
                    summ = ""
                out = outputs.get(c.get("id"), "")
                if f.get("name") == "ask_user":           # a question card, answered, as it looked at the time
                    evs += _replay_question(c.get("id"), f.get("arguments"), out)
                    continue
                evs.append({"type": "tool_start", "id": c.get("id"), "name": f.get("name", ""), "summary": summ,
                            "mcp": f.get("name", "").startswith("mcp__"), "diff": ""})
                evs.append({"type": "tool_end", "id": c.get("id"), "name": f.get("name", ""),
                            "ok": not out.startswith(("ERROR", "The user denied")),
                            "denied": out.startswith("The user denied"), "output": _redact(out[:6000])})
    for i, e in enumerate(evs):
        e["i"] = i
    return evs


def _replay_question(cid, raw_args, out: str) -> list:
    from meshpool_harness.agent import ask as ASK
    try:
        args = json.loads(raw_args or "{}") if isinstance(raw_args, str) else dict(raw_args or {})
        qs = ASK.normalize(args)
    except Exception:  # noqa: BLE001  a malformed call was answered with an error; show nothing for it
        return []
    if not out or out.startswith("ERROR"):
        return []
    stopped = out.startswith("Not run")
    return [{"type": "question", "id": cid, "questions": qs, "timeout_s": 0},
            {"type": "question_done", "id": cid, "source": "stopped" if stopped else "replay", "answers": [],
             "text": "Stopped before you answered." if stopped else ASK.replay_text(out)}]


MODES = ("ask", "edits", "auto")


def _meta_path(sid: str) -> Path:
    return SESSIONS / sid / "meta.json"


_METAS: dict = {}                   # meta.json path -> (mtime, size, meta): the sidebar lists every chat every few s


def _plain_id(sid) -> bool:
    """A chat id is one folder name (the routes take [\\w-]+): "", ".." or a path would reach outside the chats folder."""
    return isinstance(sid, str) and bool(re.fullmatch(r"[\w-]+", sid))


def read_meta(sid: str) -> dict:
    if not _plain_id(sid):
        return {}
    p = _meta_path(sid)
    try:
        st = p.stat()
        hit = _METAS.get(sid)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return dict(hit[2])
        m = json.loads(p.read_text(encoding="utf-8"))
        _METAS[sid] = (st.st_mtime_ns, st.st_size, m)
        return dict(m)
    except (OSError, ValueError):
        return {}


_META_LOCK = threading.RLock()      # read-modify-write of meta.json: turn, post-turn and request threads all write it


def write_meta(sid: str, **kw) -> dict:
    if not _plain_id(sid):
        return {}
    s = LIVE.get(sid)
    if s is None and not (SESSIONS / sid).is_dir():
        return {}                                     # deleted thread: do not recreate its folder
    with _META_LOCK:
        m = read_meta(sid)
        m.update({k: v for k, v in kw.items() if v is not None})
        _meta_path(sid).parent.mkdir(parents=True, exist_ok=True)
        tmp = _meta_path(sid).with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(m), encoding="utf-8")
        C.replace_file(tmp, _meta_path(sid))
        _METAS.pop(sid, None)
        list_dirty()
        return m


def _image_text(images: list) -> str:
    """OCR of attached images, added to the message so every model (with or without vision) gets their text."""
    try:
        import base64
        from meshpool_harness import ocr
        if not ocr.available():
            return ""
        out = []
        for im in images[:6]:
            r = ocr.read_image(base64.b64decode(im.get("data", "")))
            if r["text"].strip():
                out.append(f"[Text in the attached image {im.get('name', 'image')} (OCR)]\n{r['text'][:4000]}")
        return ("\n\n\u200b" + "\n\n".join(out)) if out else ""
    except Exception:  # noqa: BLE001  OCR is a helper; the image still goes to the model
        return ""


_FILLER = re.compile(r"^(?:please|pls|can you|could you|would you|i want you to|i need you to|i'd like you to)\s+",
                     re.I)


_GREETING = re.compile(r"^(?:hi|hello|hey|hiya|howdy|hallo|hej|hola|greetings|good\s+(?:morning|afternoon|evening|day))"
                       r"(?:\s+(?:there|all|team|everyone|again)|\s+(?-i:[A-Z])[\w-]*(?=\s*[,!.]))?\s*[,.!?:;-]*\s*", re.I)
_FILEISH = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def _path_name(tok: str) -> str:
    """The file (or last folder) name of a path token, for a title: "/home/dan/Reports/q3.pdf" -> "q3.pdf"."""
    parts = [x for x in re.split(r"[/\\]+", tok) if x and not re.fullmatch(r"[A-Za-z]:|~|\.{1,2}", x)]
    return parts[-1] if parts else tok


def _paths_to_names(t: str) -> str:
    """Paths in t replaced by their file names, whole: quoted ones (which may hold spaces) and bare tokens that start
    at a root (/, ~/, ./, C:\\) with two or more parts, or that end in a file name. A single "/word" stays: it is a
    command, removed later."""
    t = re.sub(r"[\"'`]([^\"'`]*[/\\][^\"'`]*)[\"'`]", lambda m: _path_name(m.group(1)), t)

    def one(m):
        tok, tail = re.match(r"(.*?)([.,;:!?)\]]*)$", m.group(0)).groups()
        rooted = re.match(r"(?:~|\.{1,2}|[A-Za-z]:)?[/\\]", tok)
        several = len([x for x in re.split(r"[/\\]+", tok) if x]) >= 2
        if _FILEISH.search(tok) or (rooted and (several or tok[:1] not in "/\\")):
            return _path_name(tok) + tail
        return m.group(0)
    return re.sub(r"(?<!\S)[^\s]*[/\\][^\s]*", one, t)


_DELIVERABLE = (".pptx", ".xlsx", ".docx", ".pdf", ".html", ".csv", ".md")


def deliverable_title(path: str) -> str:
    """A file's name as a chat title: "Ridgeway_PdM_Client_Deck.pptx" reads "Ridgeway PdM Client Deck"."""
    stem = Path(str(path).replace("\\", "/")).stem
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", stem)).strip()[:80]


_KIND = {".pptx": "deck", ".docx": "document", ".xlsx": "workbook", ".pdf": "PDF", ".html": "page", ".csv": "data",
         ".md": "notes"}


def work_title(paths: list) -> str:
    """A chat title from the deliverables it made, oldest first. One file: its name ("Ridgeway PdM Client Deck").
    Several that share their first words: those words and what each one is ("Ridgeway PdM: deck and memo"). Several
    that share nothing: the newest one's name."""
    names: list[tuple[str, str]] = []
    for p in paths:
        t = re.sub(r"\s*\((?:updated|edited|copy|\d+)\)$", "", deliverable_title(p), flags=re.I)
        if t and t.lower() not in [n.lower() for n, _ in names]:
            names.append((t, Path(str(p)).suffix.lower()))
    if not names:
        return ""
    if len(names) == 1:
        return names[0][0]
    words = [n.split() for n, _ in names]
    common = []
    for ws in zip(*words):
        if len({w.lower() for w in ws}) != 1:
            break
        common.append(ws[0])
    if not common or len(" ".join(common)) < 3:
        return names[-1][0]
    kinds = []
    for ws, (_, ext) in zip(words, names):
        rest = ws[len(common):]
        k = rest[-1] if rest else _KIND.get(ext, "file")
        k = k if k.isupper() and len(k) > 1 else k.lower()
        if k not in kinds:
            kinds.append(k)
    kinds = kinds[:3]
    tail = kinds[0] if len(kinds) == 1 else ", ".join(kinds[:-1]) + " and " + kinds[-1]
    return (" ".join(common) + ": " + tail)[:80]


def retitle_from_work(sid: str, root: Path, made: list) -> str:
    """Title a chat after the work: once a turn makes a file, the chat is named for the newest deliverable, not for the
    first sentence the user typed. Never over a name the user gave (rename), never from a message ("go ahead", a retry):
    only a file's name is used. Returns the new title, or "" when nothing changed."""
    try:
        meta = read_meta(sid)
        if meta.get("renamed") or (meta.get("title") and not meta.get("auto_title")):
            return ""
        files = []
        for o in made or []:
            p = str(o.get("path") or "")
            if p.lower().endswith(_DELIVERABLE):
                try:
                    files.append(((root / p).stat().st_mtime, p))
                except OSError:
                    continue
        files.sort()
        title = work_title([p for _, p in files])
        if title and title != meta.get("title"):
            write_meta(sid, title=title, auto_title=True)
            return title
    except Exception:  # noqa: BLE001 - a title is never worth breaking the turn
        pass
    return ""


def nice_title(text: str) -> str:
    """A short thread title from the first message: the first sentence, without greetings, skill names, links,
    or the hidden notes Meshpool adds, capitalised and cut at a word under 48 characters. A path is never cut: it
    becomes its file name."""
    t = str(text or "").split("\n\n\u200b")[0].replace("\u200b", "").strip()   # notes start with this separator
    if not t or t in ("New thread", "New chat", "Session"):
        return "New chat" if t in ("", "New thread") else t
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    t = lines[0]
    g = _GREETING.match(t)
    if g and g.end() == len(t) and len(lines) > 1:        # "Hi!" on a line of its own, the request below it
        t = lines[1]
    elif g and len(t[g.end():].split()) >= 2:              # "Hi! The file ..." -> "The file ..."
        t = t[g.end():]
    t = re.sub(r"https?://\S+", "", t)
    t = _paths_to_names(t)
    t = re.sub(r"(?<!\S)[$/][\w-]+(?![\w/\\.-])", "", t)
    t = re.split(r"(?<=[.!?])\s", t)[0].strip(" .!?:;,-")
    full = t
    for _ in range(3):
        t = _FILLER.sub("", t).strip()
    if len(t.split()) < 2:                     # "hi there" stays "Hi there", not "There"
        t = full
    t = re.sub(r"\s+", " ", t)
    if not t:
        return "New chat"
    if len(t) > 48:
        words, cut = t.split(" "), ""
        for w in words:
            if len(cut) + len(w) + bool(cut) > 48:
                break
            cut += " " * bool(cut) + w
        if len(cut) <= 20:                             # one long word after a short start
            w = words[len(cut.split(" ")) if cut else 0]
            cut = (cut + " " + w).strip() if _FILEISH.search(w.rstrip(".,;:!?")) and len(cut) + len(w) < 72 \
                else t[:48]                            # a file name is kept whole
        t = cut.rstrip(" ,;:") + ("…" if cut != t else "")
    return t[0].upper() + t[1:]


# Chats running a turn, by folder. Undo compares the folder before and after a turn, so it cannot tell this chat's
# changes from another chat's (or an edit the user saved) made at the same time; such a turn is marked shared and
# Undo refuses it rather than delete someone else's work.
_ACTIVE: dict[str, set] = {}
_ACTIVE_LOCK = threading.Lock()


def mark_shared(root: str):
    """Someone else (the user's editor) changed files under `root`: every turn running there is shared."""
    r = str(Path(root).resolve()) if root else ""
    with _ACTIVE_LOCK:
        for o in _ACTIVE.get(r, ()):
            o._turn_shared = True



def _last_reply(agent) -> str:
    """The agent's last answer as text, for the learning loop's context ("" when the harness does not expose it)."""
    try:
        for m in reversed(getattr(agent, "messages", None) or []):
            if isinstance(m, dict) and m.get("role") == "assistant":
                c = m.get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                if isinstance(c, str) and c.strip():
                    return c[:2000]
    except Exception:  # noqa: BLE001
        pass
    return ""

class _Always(set):
    """The "Allow ... in this chat" set of an agent, saved to the chat's meta.json whenever it changes, so the choice
    survives the reaper closing the chat, a restart and a handoff to another agent."""

    def __init__(self, items=(), on_change=None):
        super().__init__(items)
        self._cb = on_change

    def _changed(self):
        cb = getattr(self, "_cb", None)
        if cb:
            try:
                cb(set(self))
            except Exception:  # noqa: BLE001 - failing to save a choice never stops the turn
                pass

    def add(self, x):
        new = x not in self
        super().add(x)
        if new:
            self._changed()

    def update(self, *others):
        n = len(self)
        super().update(*others)
        if len(self) != n:
            self._changed()

    def discard(self, x):
        had = x in self
        super().discard(x)
        if had:
            self._changed()

    def remove(self, x):
        super().remove(x)
        self._changed()

    def clear(self):
        had = bool(self)
        super().clear()
        if had:
            self._changed()


UNATTENDED_NOTE = ("This is an unattended scheduled run, so nothing can be approved. Do what you can without it "
                   "and say in your summary what needed the user.")


class Session:
    scheduled = None           # class default: the scheduled task that started this thread
    incognito = False          # class defaults: tests and old callers build a Session without __init__
    auto_resume = True         # wait out an outage and carry on (the terminal's -p turns it off: a script wants the failure)
    listeners = ()             # front ends that watch the events (the terminal): callables taking the stored event
    _saved_mtime = 0.0
    _ck_run, _ck_n, _ck_stop = "", 0, None
    _turn_id = 0               # the token of the current turn (see _claim_turn / _end_turn)
    _stop_req = False          # Stop was pressed for the current turn
    _interrupting = None       # the token of the turn a "send now" message is stopping
    _after_stop = None         # messages sent once Stop was pressed: they run as their own turn (list, made lazily)
    _retry_open = False        # a retry notice is showing: the next good model event resolves it
    _undo_token = None         # the turn an Undo / Rewind holds: a message sent meanwhile waits in the queue
    _open_deltas = ()          # positions in self.events of the text_delta events of the reply still streaming
    _open_thoughts = ()        # positions in self.events of the streamed reasoning pieces not folded yet (FL-R6a)

    def __init__(self, workdir: str | None = None, resume_id: str | None = None, mode: str | None = None,
                 harness: str | None = None, kind: str = "code", model: str | None = None, plan: bool = False,
                 no_mcp: bool = False):
        self.events: list[dict] = []
        self.listeners: list = []
        from meshpool_harness import telemetry as _TM
        _tl = _TM.listener(self)                  # G-16: spans and metrics, only with MH_OTEL=1 and an endpoint
        if _tl:
            self.listeners.append(_tl)
        self.epoch = int(time.time() * 1000)      # a resumed thread is a new event list: pages reset on change
        self.plan = bool(plan)
        self.effort = ""                        # /effort: this chat's reasoning effort ("" = the setting)
        self.goal, self._goal_idle, self._goal_net = "", 0, 0   # /goal: see GOAL_NOTE
        self.cp = None
        self.lock = threading.RLock()          # re-entrant: a steer taken under it emits an event
        self.changed = threading.Condition(self.lock)    # notified by emit(): event streams wait on it, not sleep
        self.state = "starting"
        self.queue: list = []                       # messages sent while a harness without mid-turn input works
        # Instructions and memory go with the first message. Kept in the chat's meta, so a restart or an idle close
        # before that message does not lose them (M20-06); a chat saved before this was kept has them already.
        self.ctx_pending = bool(read_meta(resume_id).get("ctx_pending")) if resume_id else True
        self.incognito = bool(resume_id and read_meta(resume_id).get("incognito"))   # no memory, learning or insights
        self.scheduled: dict | None = None           # the scheduled task that started this thread
        self.pendings: dict = {}                     # open approvals by id: harnesses may ask several at once
        self.mcp = None
        self._mcp_ready = threading.Event()          # set once _start_mcp has finished (connected or not)
        self.agent = None
        self.touched = time.time()                   # last view, send or event: idle threads are closed (_reap)
        mode = C.normalize_mode(mode, None)
        if mode not in MODES and resume_id:
            mode = read_meta(resume_id).get("mode")      # a chat keeps its own mode across restarts
        self.mode = C.normalize_mode(mode, None) or C.permission_mode()
        self._n = 0
        kw = dict(emit=self.emit, approve=self.approve, mode=self.mode)
        legacy = False
        if resume_id:
            data = _saved(resume_id)
            if not data:
                raise KeyError(resume_id)
            if data.get("workdir") and not Path(data["workdir"]).is_dir():
                raise ProjectMissing(data["workdir"])
            old = data.get("harness") or "meshpool-code"
            # A chat from an agent Meshpool no longer has (HR.LEGACY) continues in the built-in agent it maps to.
            legacy = old in HR.LEGACY and "messages" not in data and HR.LEGACY[old] in ("meshpool-code", "meshpool-work")
            self.harness = HR.get(old, None if legacy else (read_meta(resume_id).get("model") or None))
            self.kind = data.get("kind") or self.harness.get("mode", "code")
            if legacy:
                a = Agent(data["workdir"], session_id=resume_id,
                          profile="work" if self.harness["id"] == "meshpool-work" else "code", **kw)
                a.messages = [a.messages[0]] + _messages_from_events(data.get("events") or [])
                a.title = data.get("title", "")
                a.created = data.get("created", a.created)
                self.agent = a
                with self.lock:
                    self.events = [{k: v for k, v in e.items() if k != "i"} for e in data.get("events") or []]
                    self.events = [dict(e, i=i) for i, e in enumerate(self.events)]
            elif self.harness["type"] in ("acp", "pi"):
                cls = PiAgent if self.harness["type"] == "pi" else ACPAgent
                self.agent = cls.resume(data, self.harness, **kw)
                with self.lock:
                    self.events = [dict(e, i=i) for i, e in enumerate(self.agent.events)]
            else:
                a = Agent.resume(data["workdir"], resume_id, **kw)
                if not a:
                    raise KeyError(resume_id)
                self.agent = a
                with self.lock:
                    self.events = events_from_messages(a.messages[1:])
        else:
            if not workdir or not Path(workdir).expanduser().is_dir():
                raise ValueError(f"Project folder not found: {workdir or '(empty)'}")
            hid = harness or HR.default_for(kind)
            if hid == "meshpool-auto":                     # Auto picks per task; without one, use the mode default
                hid = "meshpool-code" if kind == "code" else "meshpool-work"
            self.harness = HR.get(hid, model)               # credentials from the provider of the thread's model
            if not self.harness.get("ready", True):        # nothing to run on under the chosen provider
                raise ValueError(f"{self.harness['name']} cannot run yet. {self.harness.get('setup') or ''}".strip())
            self.kind = kind if kind in HR.MODES else self.harness.get("mode", "code")
            if self.harness["type"] in ("acp", "pi"):
                if self.harness.get("unsupported"):        # Codex, OpenCode in the packaged app
                    raise ValueError(self.harness["unsupported"])
                if not self.harness.get("available"):
                    raise ValueError(f"{self.harness['name']} is not installed on this computer. "
                                     + (self.harness.get("install") or ""))
                cls = PiAgent if self.harness["type"] == "pi" else ACPAgent
                self.agent = cls(self.harness, workdir, **kw)
            else:
                self.agent = Agent(workdir, profile="work" if self.harness["id"] == "meshpool-work" else "code", **kw)
            self.agent.save()
        self.id, self.workdir = self.agent.id, str(self.agent.root)
        if not resume_id:
            write_meta(self.id, ctx_pending=True)
        self._bind_always(self.agent)
        if self.harness["type"] not in ("acp", "pi"):
            # ask_user waits for this page's answer; a scheduled run has no one watching, so it takes the defaults
            self.agent.interactive = lambda: not self.scheduled and not getattr(self, "deleted", False)
        if resume_id:
            self.kind = read_meta(self.id).get("kind") or self.kind
            tr = _transcript(self.id)
            if tr and tr.get("events"):
                with self.lock:
                    self.events = [dict(e, i=i) for i, e in enumerate(tr["events"])]
                if tr.get("running"):
                    last = next((e.get("text") for e in reversed(tr["events"]) if e.get("type") == "user"), "")
                    self.emit({"type": "interrupted", "restarted": True, "resend": last,
                               "text": "meshpool closed while this was running, so it stopped. Everything done so far "
                                       "is kept: press Continue to pick it up, or send your message again."})
                    self._persist(running=False)
            try:                                     # an approval, question or step left open when the app closed
                self._close_open_items(turns=bool(tr and tr.get("events")))
            except Exception:  # noqa: BLE001 - never blocks reopening a chat
                pass
        else:
            write_meta(self.id, kind=self.kind, mode=self.mode)   # Cowork or Code, and its permission mode
        if model:
            self.set_model(model)
        if resume_id:
            meta = read_meta(self.id)
            self.plan = bool(meta.get("plan", self.plan))
            self.effort = str(meta.get("effort") or "")
            self.goal = str(meta.get("goal") or "")
            if meta.get("model") and not getattr(self.agent, "model", None) and not legacy:
                self.set_model(meta["model"])
        if hasattr(self.agent, "plan"):
            self.agent.plan = self.plan
        if self.effort:
            self._apply_effort()
        from meshpool_harness.checkpoints import Checkpoints
        outside = self.harness["type"] in ("acp", "pi")
        self.cp = Checkpoints(self.workdir, SESSIONS / self.id / "checkpoints", mode="scan" if outside else "journal",
                              seq_floor=int(read_meta(self.id).get("turn_seq") or 0))
        if not outside:
            self.agent.checkpoint = self.cp          # the agent books what its own tools wrote (core._run_tool)
        if self.harness["type"] in ("acp", "pi"):
            self._set_state("idle", expect=("starting",))
            name = self.harness["name"]
            if not self.events:
                self.emit({"type": "status", "text": f"{name} is ready. "
                           + (self.harness.get("settings_note") or "")})
        else:
            if legacy:
                self.emit({"type": "status", "text": f"meshpool no longer includes the agent this chat started with. "
                                                     f"It continues in {self.harness['name']}."})
                self.agent.save()
            if no_mcp:                                   # the terminal's --no-mcp: no servers, ready at once
                self.state = "idle"
                self._mcp_ready.set()
            else:
                threading.Thread(target=self._start_mcp, daemon=True).start()
        self._mark_saved()

    def _mark_saved(self):
        """Remember when session.json was last written by this Session, to notice another front end's writes."""
        try:
            self._saved_mtime = (SESSIONS / self.id / "session.json").stat().st_mtime
        except (OSError, AttributeError):
            pass

    def _close_open_items(self, turns: bool = True):
        """A chat reopened after the app closed: whatever was open then can never be answered or finished. Appends,
        once, what closes it: approval_done / question_done / tool_end for each, and a synthetic turn_end when the
        last user message has no end. Running it again adds nothing. Ids may be ints (old transcripts) or strings."""
        with self.lock:
            evs = list(self.events)
        done = {"approval": set(), "question": set(), "tool": set()}
        for e in evs:
            t = e.get("type")
            if t == "approval_done":
                done["approval"].add(str(e.get("id")))
            elif t == "question_done":
                done["question"].add(str(e.get("id")))
            elif t == "tool_end":
                done["tool"].add(str(e.get("id")))
        out, seen = [], set()
        last_end = max((n for n, e in enumerate(evs) if e.get("type") == "turn_end"), default=-1)
        for n, e in enumerate(evs):
            t, i = e.get("type"), str(e.get("id"))
            if e.get("id") is None or (t, i) in seen:
                continue
            if t == "approval" and i not in done["approval"]:
                out.append({"type": "approval_done", "id": e["id"], "decision": "stopped", "closed": True})
            elif t == "question" and e.get("timeout_s") and i not in done["question"]:
                out.append({"type": "question_done", "id": e["id"], "source": "stopped", "answers": [],
                            "text": "meshpool closed before you answered.", "closed": True})
            elif t == "tool_start" and i not in done["tool"] and n > last_end:   # steps of finished turns stay as they were
                out.append({"type": "tool_end", "id": e["id"], "name": e.get("name", ""), "ok": False,
                            "stopped": True, "output": "Not run: meshpool closed before this step finished."})
            else:
                continue
            seen.add((t, i))
        if turns or any(e.get("type") == "turn_end" for e in evs):
            last = max((n for n, e in enumerate(evs) if e.get("type") == "user" and not e.get("steer")), default=None)
            if last is not None and not any(e.get("type") in ("turn_end", "interrupted") for e in evs[last + 1:]):
                out.append({"type": "turn_end", "synthetic": True})
        for ev in out:
            self.emit(ev)
        if out:
            self._persist(running=False)

    def _start_mcp(self):
        try:
            self._start_mcp_servers()
        finally:
            self._mcp_ready.set()

    def _start_mcp_servers(self):
        self.emit({"type": "status", "text": "Starting MCP servers..."})
        try:
            # Meshpool Work and Meshpool Code get the office server Hermes and Pi get: decks (make_deck), Word
            # documents, workbooks, PDFs, conversions and document reading. Work's own office_task covers slides,
            # sheets and data too. A Code chat in Codex style shows the office tools only once a message asks for an
            # office file (Agent.office_on), so coding chats do not pay for them.
            from meshpool_harness.harnesses.acp import office_extra
            extra = office_extra(self.workdir)
            spec = run_mcp_spec()
            if spec:                                  # meshpool --mcp-config: servers for this terminal run only
                from meshpool_harness.agent.tui import _mcp_for_run
                try:
                    extra = {**(extra or {}), **_mcp_for_run(spec)}
                except ValueError as e:
                    self.emit({"type": "status", "text": f"--mcp-config: {e}"})
            self.mcp = M.Manager(roots=[self.workdir], extra=extra, chat=self.id).start(
                timeout=float(os.environ.get("MH_MCP_START_S", "45")))
            if getattr(self, "_closed", False):       # the chat was closed while its servers were starting
                self.mcp.close()
                return
            self.agent.mcp = self.mcp
            self.mcp.model = lambda: getattr(self.agent, "model", None) or ""
            ok = [s for s in self.mcp.status() if s["status"] == "connected"]
            bad = [s for s in self.mcp.status() if s["status"] == "error"]
            txt = ("MCP: " + ", ".join(f"{s['name']} ({len(s['tools'])} tools)" for s in ok)) if ok else "No MCP servers enabled."
            if bad:
                txt += ". Failed: " + "; ".join(f"{s['name']}: {s['error']}" for s in bad)
            if self.mcp.config_error:
                txt += ". " + self.mcp.config_error
            self.emit({"type": "status", "text": txt})
        except Exception as e:
            self.emit({"type": "status", "text": f"MCP did not start: {e}"})
            if isinstance(e, OSError) and e.errno in (errno.EMFILE, errno.ENFILE):
                self.emit({"type": "error", "text": "This chat could not start its tools: the computer's limit of open "
                                                    "files was reached. Close chats you are not using and try again."})
        self._set_state("idle", expect=("starting",))     # a turn that started meanwhile keeps "working"

    # ---- the turn state machine. Every move of self.state goes through _claim_turn / _set_state / _end_turn, under
    # self.lock, tied to the token of the turn it belongs to (self._turn_id).
    def _claim_turn_locked(self):
        if getattr(self, "state", "idle") in _BUSY:
            return None
        with GATE:                                   # an update is being installed: no turn may start (M11-02)
            if QUIESCING[0]:
                raise ValueError(_GATE_MSG)
            self.state = "working"
        self._turn_id += 1
        self._stop_req = False
        self.t_turn = time.time()
        return self._turn_id

    def _claim_turn(self):
        """Idle/starting -> working under the lock: the token of the new turn, or None when a turn already runs."""
        with self.lock:
            return self._claim_turn_locked()

    def _hold_turn(self, busy: str):
        """Claim the turn for an Undo / Rewind, which changes the conversation and the files: no turn may start while
        it runs, and a message sent meanwhile is queued (not added to a turn that is not running) and runs after it.
        Returns the token to pass to _end_turn(token, notice=False) when done; raises ValueError(busy) when busy."""
        with self.lock:
            token = self._claim_turn_locked()
            if token is None:
                raise ValueError(busy)
            self._undo_token = token
            self._turn_open = False                  # no reply runs: _end_turn must not add a turn_end for one
        return token

    def _set_state(self, to: str, token=None, expect=None) -> bool:
        """Move to `to` when allowed. Refused when `token` is not the current turn, when the state is not one of
        `expect`, or when the move is not one of the allowed ones. Returns whether it moved."""
        with self.lock:
            cur = getattr(self, "state", None)
            if token is not None and token != self._turn_id:
                return False
            if expect is not None and cur not in expect:
                return False
            if cur != to and (cur, to) not in _MOVES:
                return False
            self.state = to
            return True

    def _no_dead_links(self, ev: dict) -> dict:
        """A reply that names a file in a folder the app cannot open (a model's own scratch folder: Sara's
        "A copy ... is at /tmp/.../Budget_2026.backup.xlsx") says its name as plain text, not a link that opens on
        "That folder is not one of your projects." (office_guard.unlink_dead_paths). Never raises."""
        try:
            from meshpool_harness import office_guard as OG
            from meshpool_harness.ui import workbench as WB

            def can_open(d):
                WB.check_root(d)
                return True
            text = str(ev.get("text") or "")
            new = OG.unlink_dead_paths(text, str(getattr(self, "workdir", "") or ""), can_open)
            return dict(ev, text=new) if new != text else ev
        except Exception:  # noqa: BLE001
            return ev

    def emit(self, ev: dict):
        self._question_state(ev)
        t = ev.get("type")
        if "down" not in ev:
            self._last_ev_at = time.time()           # what the outside agent's endpoint watch counts quiet from
        if t == "model_wait":                        # a note for the terminal (waiting for the model): not part of the chat,
            for fn in list(self.listeners):          # and it must not reset the page's own quiet timer
                try:
                    fn(ev)
                except Exception:  # noqa: BLE001
                    pass
            return
        if (t == "interrupted" and self._interrupting is not None and self._interrupting == self._turn_id
                and getattr(self, "state", None) in _BUSY):
            self._interrupting = None                # the new message is being answered: do not say "send a message"
            ev = dict(ev, text="Stopped the previous request.")
        if t == "error" and not ev.get("detail") and (getattr(self, "harness", None) or {}).get("type") in ("acp", "pi"):
            # An outside engine's own error ("unexpected status 404 Not Found: ..., url: http://...") in plain words;
            # its raw text goes in the Details fold (core/errtext.outside).
            from meshpool_harness.core import errtext as ET
            plain, raw = ET.outside(str(ev.get("text") or ""))
            if raw:
                ev = dict(ev, text=plain, detail=raw)
        if t == "assistant" and "<" in str(ev.get("text") or ""):
            # tool markup the model wrote as text (Maya, round 3: "<invoke_placeholder> </invoke_placeholder>" three
            # times in a reply, kept in the transcript): never shown, never saved as the reply
            from meshpool_harness.core import textcalls as TC
            clean = TC.display_safe(str(ev["text"]))
            if clean != ev["text"]:
                ev = dict(ev, text=clean)
        offer = False
        if t == "assistant" and ev.get("text") and self._no_folder_chat():
            from meshpool_harness import nofolder as NF          # "run this in a session that has a shell": a plain
            offer = NF.wants_folder(ev["text"])                  # offer and an "Add a folder" button instead
            if offer:
                ev = dict(ev, text=NF.plain(ev["text"]))
        if t == "assistant" and "/" in str(ev.get("text") or ""):
            ev = self._no_dead_links(ev)
        if t == "reply_trim":                         # the answer without its trailing promise (core): not a new bubble
            with self.lock:
                for e in reversed(self.events):
                    if e.get("type") == "assistant" and not e.get("has_tools"):
                        e["text"] = str(ev.get("text") or "")
                        break
            return
        if t == "turn_end":
            self._mark_retried_steps()               # failed steps a later step redid: not problems in the count
        if t == "status" and _RETRY_RE.search(str(ev.get("text") or "")):
            ev = dict(ev, retry=True)                # a transient notice: resolved by retry_ok once the call works
            self._retry_open = True
        elif self._retry_open and t in _RETRY_DONE:
            self._retry_open = False
            self.emit({"type": "retry_ok"})
        with self.lock:
            ev = dict(ev, i=len(self.events), t=time.time())
            self.events.append(ev)
            if t == "text_delta":
                if not isinstance(self._open_deltas, list):
                    self._open_deltas = []
                self._open_deltas.append(ev["i"])
            elif self._open_deltas and t in ("assistant", "stream_reset", "turn_end"):
                self._fold_deltas(final=t != "turn_end")
            if t == "thought" and not ev.get("final"):
                if not isinstance(self._open_thoughts, list):
                    self._open_thoughts = []
                self._open_thoughts.append(ev["i"])
            elif self._open_thoughts and t == "thought":
                self._fold_thoughts()                # the call's saved copy is here: the streamed pieces can go
            elif self._open_thoughts and t == "turn_end":
                self._open_thoughts = []             # no saved copy came (a stop, or a harness that sends none): kept
            self.changed.notify_all()
        lease = getattr(self, "_lease", None)
        if lease and time.time() - getattr(self, "_lease_beat", 0) > 60:      # a working run keeps its task claimed
            self._lease_beat = time.time()

            def beat(tid=lease[0], tok=lease[1]):
                from meshpool_harness import assistant as AS
                AS.renew(tid, tok)
            threading.Thread(target=beat, daemon=True).start()
        if ev.get("type") == "user" and not ev.get("steer"):
            self._turn_open = True                   # a turn is running until its turn_end arrives
        elif ev.get("type") == "turn_end":
            self._turn_open = False
        if ev.get("type") in ("user", "turn_end", "interrupted", "error", "approval"):
            self._persist()                          # moments a restart must not lose: write now
        elif ev.get("type") in _KEEP:
            self._persist_soon()
        for fn in list(self.listeners):                  # outside the lock: a listener may call back into the Session
            try:
                fn(ev)
            except Exception:  # noqa: BLE001 - a front end's drawing never breaks the turn
                pass
        if ev.get("type") == "user" and not ev.get("steer"):
            after = self.__dict__.pop("_after_user", None)
            if after:                                # the fallback line goes under the message it is about
                self.emit(after)
        if offer:                                    # the reply offered a folder: the app shows the button under it
            self.emit({"type": "offer", "action": "add_folder", "label": "Add a folder"})

    def _no_folder_chat(self) -> bool:
        """True for a chat that works in its own private folder, not in one the user chose."""
        try:
            from meshpool_harness import project_store as PS
            return bool(getattr(self, "workdir", "")) and PS.is_scratch(self.workdir)
        except Exception:  # noqa: BLE001
            return False

    def _mark_retried_steps(self):
        """Before a turn_end: the failed steps of this turn that a later step redid (the same file worked on and the
        later step worked) are marked "retried" ({"type": "steps_retried", "ids": [...]}), so the step count does not
        call them problems. Priya: make_spreadsheet failed, ls and two checks then failed on the missing file, a
        script made it and the check passed: "7 steps, 4 had a problem" for a task that worked."""
        try:
            with self.lock:
                evs = list(self.events)
            start = max((i for i, e in enumerate(evs) if e.get("type") == "user" and not e.get("steer")), default=0)
            steps, order = {}, []
            for e in evs[start:]:
                if e.get("type") == "tool_start" and e.get("id") is not None:
                    steps[e["id"]] = {"name": str(e.get("name") or ""), "about": _files_named(e), "ok": None}
                    order.append(e["id"])
                elif e.get("type") == "tool_end" and e.get("id") in steps:
                    st = steps[e["id"]]
                    st["ok"] = bool(e.get("ok")) or bool(e.get("denied"))
            ids = []
            for k, sid in enumerate(order):
                st = steps[sid]
                if st["ok"] is not False or not st["about"]:
                    continue
                maker = _MAKER.search(st["name"].split("__")[-1]) is not None
                for later in order[k + 1:]:
                    lt = steps[later]
                    if lt["ok"] and st["about"] & lt["about"] and (
                            not maker or _MAKER.search(lt["name"].split("__")[-1]) or _SHELL.search(lt["name"])):
                        ids.append(sid)
                        break
            if ids:
                self.emit({"type": "steps_retried", "ids": ids})
        except Exception:  # noqa: BLE001 - a label never breaks a turn
            pass

    def _fold_deltas(self, final: bool):
        """Under self.lock. A reply streams as many text_delta events (one per token or so); kept for the whole session
        they cost memory and a reload replayed every one. Once the reply is complete they are folded IN PLACE (positions
        never move: event_index, checkpoints' event_index, since= cursors and ev0 slices all stay right):
        `final` (an assistant event carries the whole text, or stream_reset discarded it): each becomes _DELTA_GONE,
        a shared marker the page is never sent. At turn_end without such an event (an outside agent stopped
        mid-reply): each run of consecutive deltas becomes one delta with the run's text, so what the page shows is
        unchanged."""
        idx, self._open_deltas = self._open_deltas, []
        evs = self.events
        idx = [p for p in idx if 0 <= p < len(evs) and evs[p].get("type") == "text_delta" and not evs[p].get("gone")]
        if final:
            for p in idx:
                evs[p] = _DELTA_GONE
            return
        k = 0
        while k < len(idx):
            j = k
            while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
                j += 1
            if j > k:
                first = idx[k]
                evs[first] = dict(evs[first], text="".join(str(evs[p].get("text") or "") for p in idx[k:j + 1]))
                for p in idx[k + 1:j + 1]:
                    evs[p] = _DELTA_GONE
            k = j + 1

    def _fold_thoughts(self):
        """Under self.lock. FL-R6a: reasoning streams as many thought events (one per token or so), like a reply's
        text. Once the call's saved copy (one thought event, final, with the whole text) has arrived, each streamed
        piece becomes _THOUGHT_GONE, a shared marker the page is never sent and that is never saved. Positions never
        move, so since= cursors and event indexes stay right. The page adds any part it had not seen yet from the
        saved copy (app.js, case "thought")."""
        idx, self._open_thoughts = self._open_thoughts, []
        evs = self.events
        for p in idx:
            if 0 <= p < len(evs) and evs[p].get("type") == "thought" and not evs[p].get("gone") \
                    and not evs[p].get("final"):
                evs[p] = _THOUGHT_GONE

    def _persist_soon(self):
        """Steps arrive many per second: write the transcript at most once every two seconds instead of on every step."""
        with self.lock:
            if getattr(self, "_flush", None):
                return
            self._flush = threading.Timer(2.0, self._persist)
            self._flush.daemon = True
            self._flush.start()

    def _persist(self, running: bool | None = None):
        """Save what the thread shows (steps, stop notes, approvals), so it looks the same after a restart, and
        whether a turn is still running, so a turn cut off by a restart can say so."""
        with self.lock:
            t, self._flush = getattr(self, "_flush", None), None
        if t:
            t.cancel()
        sid = getattr(self, "id", None)
        if not sid or sid in DELETED or getattr(self.agent, "deleted", False):
            return
        try:
            d = SESSIONS / sid
            d.mkdir(parents=True, exist_ok=True)
            plock = self.__dict__.get("_plock") or self.__dict__.setdefault("_plock", threading.RLock())
            with plock:                              # the emit thread and the 1 s timer thread must not interleave
                log = self.__dict__.get("_tlog")
                if log is None or log.dir != d:
                    log = self._tlog = SS.TranscriptLog(d)
                    self._tlog_n = None
                run = bool(getattr(self, "_turn_open", False)) if running is None else running
                with self.lock:
                    total, epoch = len(self.events), getattr(self, "epoch", 0)
                    n = getattr(self, "_tlog_n", None)
                    replace = n is None or epoch != getattr(self, "_tlog_epoch", None) or total < n
                    src = self.events if replace else self.events[n:]
                    evs = [{k: v for k, v in e.items() if k != "i"} for e in src if e.get("type") not in _SKIP
                           and not e.get("gone")
                           and not (e.get("type") == "subagent" and e.get("kind") == "tool")]   # live only
                if replace:
                    log.rewrite(evs[-3000:], run)
                else:
                    log.append(evs)
                    log.set_running(run)
                self._tlog_n, self._tlog_epoch = total, epoch
        except OSError as e:
            first = not getattr(self, "_persist_err", None)
            self._persist_err = e.strerror or str(e) or "write failed"
            if first:                                # one notice per failure streak
                self.emit({"type": "status", "save_error": True, "text": (
                    f"Couldn't save this chat ({self._persist_err}). Free some disk space; meshpool keeps trying.")})
        else:
            if getattr(self, "_persist_err", None):
                self._persist_err = None
                self.emit({"type": "status", "text": "Saved again."})

    @staticmethod
    def _acp_key(kind, title=""):
        """edit and bash are remembered per kind; any other tool only for that one tool (its title)."""
        if kind in ("edit", "bash"):
            return "acp:" + str(kind)
        return "acp:" + str(kind) + ":" + str(title or "").strip()[:200]

    def _acp_always_pick(self, kind, detail, options, title=""):
        """"Allow always" chosen once on an ACP harness's card for this kind of request (saved in meta.json): the
        next ask of that kind is answered with its allow option, without a card. Never for a destructive command."""
        if not options or not getattr(self, "id", None):
            return None
        try:
            if self._acp_key(kind, title) not in (read_meta(self.id).get("always") or []):
                return None
            if kind == "bash":
                from meshpool_harness.agent.core import _destructive
                if _destructive({"command": detail}):
                    return None
            pick = (next((o for o in options if o.get("kind") == "allow_once"), None)
                    or next((o for o in options if o.get("kind") == "allow_always"), None))
            return pick["id"] if pick else None
        except Exception:  # noqa: BLE001 - when in doubt, ask
            return None

    def _bind_always(self, agent):
        """The agent's "Allow in this chat" set (built-in Agent.always, Pi _always) becomes a set that saves itself
        to meta.json["always"] and starts from what was saved: it outlives the reaper, a restart and a handoff."""
        attr = "always" if hasattr(agent, "always") else "_always" if hasattr(agent, "_always") else ""
        sid = getattr(self, "id", None)
        if not attr or not sid:
            return
        try:
            seed = set(read_meta(sid).get("always") or []) | set(getattr(agent, attr) or ())
            setattr(agent, attr, _Always(seed, lambda items, sid=sid: write_meta(sid, always=sorted(items))))
        except Exception:  # noqa: BLE001 - the choice just stays in memory
            pass

    def approve(self, kind, title, detail, options=None, scope=""):
        sid = getattr(self, "id", None)
        gone = bool(getattr(self, "deleted", False) or getattr(getattr(self, "agent", None), "deleted", False) or (sid and sid in DELETED))
        if os.environ.get("MESHPOOL_HEADLESS") == "1" or getattr(self, "scheduled", False) or gone:
            # `meshpool run-due` with the app closed, an unattended scheduled run, or a chat nobody can open any more:
            # no one can answer, so it fails fast instead of waiting for ever.
            note = UNATTENDED_NOTE if not gone else ("This chat was deleted, so nothing can be approved. Stop here "
                                                      "and do not retry the action.")
            if hasattr(self, "lock") and hasattr(self, "events"):   # a live chat: the card shows it, already closed
                aid = "ap-" + uuid.uuid4().hex[:10]
                self.emit({"type": "approval", "id": aid, "kind": kind, "title": title, "detail": detail,
                           "options": options, "scope": scope, **_approval_view(kind, title, detail)})
                self.emit({"type": "approval_done", "id": aid, "decision": "no", "reason": note})
            return "no", note
        auto = self._acp_always_pick(kind, detail, options, title)
        if auto:
            return auto, ""
        with self.lock:
            tok = self._turn_id
            running = getattr(self, "state", "idle") in _BUSY     # an ask outside a turn never changes the state
            aid = "ap-" + uuid.uuid4().hex[:10]
            p = {"id": aid, "kind": kind, "title": title, "detail": detail, "ev": threading.Event(),
                 "decision": "no", "note": "", "options": options, "token": tok, "in_turn": running}
            self.pendings[aid] = p
        if running:
            self._set_state("waiting", token=tok, expect=_BUSY)
        from meshpool_harness.redact import redact
        try:                                  # a chat with no folder of its own choosing: no "Always in this folder"
            from meshpool_harness import project_store as _PS
            folder = bool(getattr(self, "workdir", "")) and not _PS.is_scratch(self.workdir)
        except Exception:  # noqa: BLE001
            folder = True
        self.emit({"type": "approval", "id": aid, "kind": kind, "title": title, "detail": redact(detail),
                   "options": options, "scope": scope, "folder": folder, **_approval_view(kind, title, detail, redact),
                   "folder_name": os.path.basename(str(getattr(self, "workdir", "") or "").rstrip("/\\")) if folder else ""})
        if time.time() - getattr(self, "t_turn", time.time()) > 60:
            _notify(f"{self.agent.title or 'A task'} needs your approval", title)
        try:
            limit = float(os.environ.get("MH_APPROVAL_TIMEOUT_S", "1800"))
        except ValueError:
            limit = 1800.0
        t_end = time.time() + limit if limit > 0 else None
        timed_out = False
        while not p["ev"].wait(0.5 if t_end is None else max(0.01, min(0.5, t_end - time.time()))):
            if getattr(self.agent, "_stop", False):
                break
            if t_end is not None and time.time() >= t_end:
                timed_out = True
                break
        if timed_out and not p.get("answered"):
            mins = max(1, round(limit / 60))
            p["note"] = (f"No answer within {mins} minute{'s' if mins != 1 else ''}, so this was not done. "
                         "Ask again when you are back.")
        with self.lock:
            self.pendings.pop(aid, None)
            others = any(q.get("in_turn") and q.get("token") == tok for q in self.pendings.values())
        if running and not others:
            self._set_state("working", token=tok, expect=("waiting",))   # not when the turn ended meanwhile
        shown = p["decision"] if p.get("answered") else ("timeout" if timed_out else "stopped")
        if p.get("answered") and options and getattr(self, "id", None):
            try:                                      # "allow always" on an ACP card: remembered for this chat
                if next((o.get("kind") for o in options if o.get("id") == p["decision"]), "") == "allow_always":
                    cur = set(read_meta(self.id).get("always") or [])
                    write_meta(self.id, always=sorted(cur | {self._acp_key(kind, title)}))
            except Exception:  # noqa: BLE001
                pass
        self.emit({"type": "approval_done", "id": aid, "decision": shown})
        return p["decision"], p["note"]

    def answer(self, aid, decision: str, note: str = ""):
        p = self.pendings.get(str(aid))
        if not p or p.get("closed"):
            raise ValueError("That approval is no longer waiting.")
        allowed = {"yes", "always", "save", "no"} | {o["id"] for o in (p.get("options") or [])}
        p["decision"] = decision if decision in allowed else "no"
        p["note"] = note
        p["answered"] = True
        p["ev"].set()

    def _question_state(self, ev: dict):
        """ask_user: the thread waits for the user while a question is open (like an approval)."""
        t = ev.get("type")
        if t == "question" and ev.get("timeout_s"):
            self._set_state("waiting", token=self._turn_id, expect=("working", "waiting"))
            if self.scheduled or time.time() - getattr(self, "t_turn", time.time()) > 60:
                qs = ev.get("questions") or [{}]
                _notify(f"{self.agent.title or 'A task'} has a question for you", str(qs[0].get("question", ""))[:200])
        elif t == "question_done":
            with self.lock:
                if not getattr(self, "pendings", None):
                    self._set_state("working", token=self._turn_id, expect=("waiting",))

    def reply(self, question_id: str, answers) -> None:
        """The user answered an ask_user question (POST /api/agent/<id>/reply)."""
        self.touched = time.time()
        if not hasattr(self.agent, "reply"):
            raise ValueError("This agent does not ask questions.")
        self.agent.reply(str(question_id or ""), answers)

    def _codex_office(self, text: str) -> bool:
        """This message's office job runs on Codex 0.171's request (core.Agent.office_c171_for)."""
        try:
            f = getattr(self.agent, "office_c171_for", None)
            return bool(f and self.harness.get("type") not in ("acp", "pi") and f(text))
        except Exception:  # noqa: BLE001
            return False

    def _office_direct(self, text: str = "") -> bool:
        """The agent writes office files itself (core.Agent._office_direct: a lean Work chat, MH_OFFICE_DIRECT on), so
        the notes name the libraries, not engine tools. Not once the engines are offered: a message asking for a new
        deck, report or model (office_intent.engines_wanted, the same test Agent.send makes for this message after
        these notes are built), or an earlier one did. MH_OFFICE_DIRECT_NOTE=0 keeps the engine notes."""
        if os.environ.get("MH_OFFICE_DIRECT_NOTE", "1") == "0" or self.harness.get("type") in ("acp", "pi"):
            return False
        try:
            if not self.agent._office_direct() or getattr(self.agent, "office_engines", False):
                return False
            from meshpool_harness.office_intent import engines_wanted
            return not (text and engines_wanted(text))
        except Exception:  # noqa: BLE001
            return False

    def _body(self, text: str, images: list | None) -> str:
        body = text
        try:                                         # every agent: a skill named with $ is attached now, so the
            from meshpool_harness import skills as SK  # built-in agents do not spend a call on use_skill
            body = SK.expand_mentions(text)
        except Exception:  # noqa: BLE001
            body = text
        if images:
            body += _image_text(images)
        return body + self._style_note()

    def style_id(self) -> str:
        """This chat's own writing style (see styles.py), or "" while it follows the default."""
        return str(read_meta(self.id).get("style") or "") if getattr(self, "id", None) else ""

    def _style_note(self) -> str:
        """The chat's writing style as a hidden note on every message: the same for every agent. Normal adds nothing."""
        try:
            from meshpool_harness import styles as ST
            return ST.note(self.style_id())
        except Exception:  # noqa: BLE001 - a style never blocks a message
            return ""

    def set_style(self, style_id: str):
        from meshpool_harness import styles as ST
        style_id = (style_id or "").strip()
        if style_id and not ST.get(style_id):
            raise ValueError("That style does not exist any more.")
        write_meta(self.id, style=style_id or ST.NORMAL)

    _STOPPED_NOTE = ("\n\n\u200b[Meshpool] I stopped your previous request. Do not continue it; "
                     "answer this message instead.")

    def send(self, text: str, images: list | None = None, interrupt: bool = False, files: list | None = None) -> dict:
        """Start a turn. While the agent works, the message is added to the current task instead: the built-in
        agent and Pi read it before their next step; other harnesses get it as soon as the current turn ends.
        interrupt=True stops the current work and sends the message as the next turn (it does not wait for the stop
        to land); so does any message sent once Stop was pressed. Which of these happens is decided under the lock
        the turn state changes under, so two messages can never both start a turn."""
        if not text.strip() and not images and not files:
            raise ValueError("Type a message first.")
        if self.scheduled and not getattr(self, "_system_send", False):           # the person has taken over: someone is here now (M18-06)
            self.scheduled = None
        if files:                                    # saved first: if it fails, nothing was emitted or started
            text = self._save_files(text, files)
        self.touched = time.time()
        acp = self.harness.get("type") == "acp"
        if interrupt:
            tok = None
            with self.lock:
                if getattr(self, "state", "idle") in _BUSY:
                    tok = self._turn_id
                    queued = [q[0] if isinstance(q, (list, tuple)) else str(q) for q in self.queue]
                    qimgs = [im for q in self.queue if isinstance(q, (list, tuple)) for im in (q[1] or [])]
                    if queued:                       # messages waiting in the queue go with the new one, not lost
                        text = "\n\n".join([*queued, text])
                        images = [*qimgs, *(images or [])] or None
                    self._interrupting = tok          # set for this turn only: its end clears it
            if tok is not None:
                if acp:
                    # ACP agents (Hermes, the subscriptions) can merge the cancelled message into the new one and
                    # carry on with the old task. Say plainly that it was stopped; hidden in the chat (after \u200b).
                    text = text + self._STOPPED_NOTE
                self.stop()
                with self.lock:
                    same = self._turn_id == tok and getattr(self, "state", "idle") in _BUSY
                    if same:
                        if self._after_stop is None:
                            self._after_stop = []
                        self._after_stop.append((text, images))
                        n = len(self._after_stop)
                    elif self._interrupting == tok:
                        self._interrupting = None
                if same:
                    self.emit({"type": "queued", "text": text, "n": n, "after_stop": True})
                    return {"queued": True, "after_stop": True}
                # the turn ended before the lock was taken: this message starts the next one, below
        q = self.agent.pending_question() if hasattr(self.agent, "pending_question") else None
        if q and not interrupt and text.strip():
            self.agent.reply(q["id"], text)              # typed in the message box: the answer to the open question
            return {"queued": False, "answered": q["id"]}
        token, queued_as, n = None, "", 0
        with self.lock:
            if getattr(self, "state", "idle") not in _BUSY:
                if getattr(self, "_was_stopped", False) and not interrupt and acp:
                    # After Stop, ACP agents can pick the stopped request back up: say plainly that it was stopped.
                    text = text + self._STOPPED_NOTE
                self._was_stopped = False
                token = self._claim_turn_locked()
            elif self._stop_req or interrupt:        # Stop was pressed: this message is its own turn, after it
                if acp and not interrupt:
                    text = text + self._STOPPED_NOTE
                if self._after_stop is None:
                    self._after_stop = []
                self._after_stop.append((text, images))
                queued_as, n = "after", len(self._after_stop)
            elif hasattr(self.agent, "steer") and not acp and self._undo_token != self._turn_id:
                self._attach_to_turn(getattr(self, "_turn_n", None))         # added to the running turn: its copies too
                self.agent.steer(self._body(text, images), images=images)    # emits an event: the lock is re-entrant
                from meshpool_harness import pace as PACE
                if PACE.classify(text, bool(images)) != "quick":
                    self._quick_off = getattr(self, "t_turn", None)   # a real task joined: no quick wrap (Pi)
                return {"queued": False, "added": True}
            else:
                self.queue.append((text, images))
                queued_as, n = "queue", len(self.queue)
        if queued_as == "after":
            self.emit({"type": "queued", "text": text, "n": n, "after_stop": True})
            return {"queued": True, "after_stop": True}
        if queued_as:
            self.emit({"type": "queued", "text": text, "n": n})
            return {"queued": True}
        self._start_turn(text, images, token)
        return {"queued": False}

    def _attach_to_turn(self, n) -> None:
        """The copies of attached files made for the message that starts (or joins) checkpoint turn n (OM-03)."""
        made = self.__dict__.pop("_attach_made", None)
        if made and n and self.cp:
            try:
                self.cp.note_attached(n, made)
            except Exception:  # noqa: BLE001 - never stops a turn
                pass

    def take_back(self) -> dict:
        """The last message sent while the agent worked, back to the user: from the queue, or from the running
        turn when the agent has not read it yet. {"text": None} when there is nothing left to take back. The copies
        made for that message belong to no turn now: a later turn's Undo must not remove them."""
        self.__dict__.pop("_attach_made", None)
        with self.lock:
            if self.queue:
                text, _ = self.queue.pop()
                self.emit({"type": "queued", "text": self.queue[-1][0] if self.queue else "", "n": len(self.queue)})
                return {"text": text}
        if hasattr(self.agent, "take_back"):
            return {"text": self.agent.take_back()}
        return {"text": None}
    def attachments_dir(self) -> Path:
        """Where files dropped into this chat from outside its folder are kept: meshpool's data, not the user's folder
        (runs/_uploads, which the agent, the office tools and outside agents may read)."""
        return C.runs_dir() / "_uploads" / f"chat-{self.id}"

    def _save_files(self, text: str, files: list) -> str:
        """Attached files: one already in this chat's folder (same name, same bytes) is used where it is (WF-02);
        any other is kept in this chat's attachments in meshpool's data, never copied into the user's folder (Lina,
        round 3: her 2.2 MB CSV was duplicated into LinaData, and not counted as a change). The message names them;
        what the agent makes still goes to the folder."""
        import base64
        from meshpool_harness import project_store as PS
        from meshpool_harness.ui import workbench as W
        if not self.workdir:
            raise ValueError("This chat has no folder to save files in.")
        here, kept, notes = [], [], []
        for f in files:
            f = f if isinstance(f, dict) else {}
            try:
                data = base64.b64decode(f.get("data", "") or "")
            except (ValueError, TypeError):
                raise ValueError(f"Couldn't read {f.get('name') or 'the file'}.")
            name = Path(str(f.get("name") or "file")).name
            same = W.find_same(Path(self.workdir), Path(name).name, data)
            if same:
                here.append(same)
                notes.append({"name": name, "path": same, "reused": True})
                continue
            box = self.attachments_dir()
            box.mkdir(parents=True, exist_ok=True)
            r = W.attach_to(box, name, data)
            p = str(box / r["path"])
            kept.append(p)
            notes.append({"name": name, "path": p, "attached": True})
        self._attached = notes                       # the app says where each one is (session_action "attached")
        where = "this chat's folder" if PS.is_scratch(self.workdir) else "the project folder"
        out = text.rstrip()
        if here:
            out += f"\n\nAttached files (in {where}): {', '.join(here)}"
        if kept:
            out += f"\n\nAttached files (in meshpool's attachments for this chat): {', '.join(kept)}"
            out += ("\n\n​[meshpool] The user attached these files; meshpool keeps them for this chat outside the "
                    "project folder. Read them where they are. Save anything you make in the project folder.")
        return out

    def _office_ready(self, edit_only: bool = False):
        """The office tools for this chat from this message on: an outside agent (Pi, Codex) gets its office server at
        its next launch or session start; Meshpool's own agents wait for the office MCP server to be up."""
        self.agent.office_on = True
        if self.harness.get("type") == "acp":
            # edit only: Codex gets the six tools that read, change and check a file; a request to make one adds the rest
            self.agent.office_edit = bool(edit_only) and not (getattr(self.agent, "office_on_all", False))
            if not edit_only:
                self.agent.office_on_all = True
                self.agent.office_edit = False
        if self.harness.get("type") not in ("acp", "pi"):
            ready = getattr(self, "_mcp_ready", None)
            if ready is not None and not ready.is_set():
                # The office tools come from the office MCP server, which starts with the chat. A first message that
                # asks for a deck must not start before it is up: the agent then had no make_deck, looked for one in
                # the shell and built the deck by hand (158 shell calls, testkit/deck_matrix.py, 20 Nov 2026).
                self.emit({"type": "status", "text": "Starting the office tools..."})
                end = time.time() + float(os.environ.get("MH_MCP_START_S", "45")) + 5
                while not ready.is_set() and time.time() < end and not getattr(self, "_stop_req", False):
                    ready.wait(0.25)                 # Stop ends the wait

    def _office_to_own(self, text: str) -> None:
        """An office deliverable (a deck, slides, a memo or document, a spreadsheet, a PDF, speaker notes, or an edit
        to an existing .pptx, .docx or .xlsx) asked of an outside engine Auto chose moves the chat to meshpool's own
        agent, which has the office tools, the forge engine and its checks (Daniel, round 3: after he added a folder
        every deck, memo and notes turn went to Codex, which hand-wrote python-pptx on blank layouts with no checks).
        An engine the user picked by hand keeps the work. Never raises."""
        try:
            if self.harness.get("type") not in ("acp", "pi") or not self._auto_engine():
                return
            from meshpool_harness.office_intent import office_work
            if not office_work(text):
                return
            own = HR.builtin_for(self.kind) or "meshpool-code"
            if own != self.harness.get("id"):
                self.handoff(own, "office work goes to meshpool's own agent", quiet=True)
        except Exception:  # noqa: BLE001 - the current agent still answers
            pass

    def _capability_turn(self, text: str) -> list:
        """Before a message runs: what it asks Meshpool to make (capabilities.needs), and an agent that can make it.
        The chat keeps its agent when that agent can; otherwise it moves, with its history, to the agent Auto would
        pick among those that can; only when none can does it say so. Returns the capabilities the message needs,
        for the hidden note (empty when no agent can make them)."""
        from meshpool_harness import capabilities as CAP
        self._office_to_own(text)
        try:
            caps = CAP.needs(text)
        except Exception:  # noqa: BLE001 - a check never blocks a message
            return []
        if not caps:
            if CAP.edits(text):                      # a file the user has: edit_office_file, no engine to build one
                self._office_ready(edit_only=True)
                return ["edit"]
            return []
        try:
            from meshpool_harness import harness_router as HRT
            ready = HRT._ready(self.kind)
            cur = self.harness.get("id")
            ready.setdefault(cur, self.harness)
            try:
                ranked = [r["harness"] for r in HRT.route(text, self.kind).get("ranking") or []]
            except Exception:  # noqa: BLE001
                ranked = []
            target = CAP.pick(caps, cur, [*ranked, "meshpool-work", "meshpool-code"], ready)
            if target is None:
                self.emit({"type": "status", "text": f"No agent in meshpool can make {CAP.labels(caps)}."})
                return []
            if target != cur:
                self.handoff(target, f"{self.harness.get('name', 'This agent')} cannot make {CAP.labels(caps)}")
        except Exception as e:  # noqa: BLE001 - the current agent still answers
            from meshpool_harness.core import errtext as ET
            plain, raw = ET.outside(str(e))
            self.emit({"type": "status", "text": f"Could not check which agent can do this: {plain}",
                       **({"detail": raw} if raw else {})})
        self._office_ready()
        return caps

    def switch_harness(self, hid: str) -> None:
        """The user picked another agent for this chat: it continues there, with the conversation so far."""
        if self.state in ("working", "waiting"):
            raise ValueError("Wait for the current reply to finish (or stop it) before switching agents.")
        if not hid or hid == self.harness.get("id"):
            return
        if hid == "meshpool-auto":
            raise ValueError("Auto picks the agent when a chat starts. Pick a specific agent to switch this chat.")
        h = HR.get(hid)
        if h.get("unsupported"):
            raise ValueError(h["unsupported"])
        self.handoff(hid, f"You switched this chat to {h.get('name', hid)}")
        write_meta(self.id, picked=hid)              # the user's pick: its failures are shown, never worked around

    def handoff(self, hid: str, why: str = "", quiet: bool = False) -> None:
        """Move this chat to another agent, keeping what was said: the new agent starts with the conversation so far
        (Meshpool's agents as messages, outside agents as context with the next message), under the same chat id,
        so the chat, its files and its history stay one thread. Used when the current agent cannot do what a message
        asks; the user sees one line saying which agent took over and why."""
        model = getattr(self.agent, "model", None) or read_meta(self.id).get("model") or None
        new = HR.get(hid, model)
        if not new.get("ready", True) or (new.get("type") in ("acp", "pi") and not new.get("available")):
            raise ValueError(f"{new.get('name', hid)} cannot run here")
        kw = dict(emit=self.emit, approve=self.approve, mode=self.mode)
        with self.lock:
            history = [{k: v for k, v in e.items() if k != "i"} for e in self.events
                       if e.get("type") in ("user", "assistant", "tool_start")]
        old = self.agent
        if new["type"] in ("acp", "pi"):
            cls = PiAgent if new["type"] == "pi" else ACPAgent
            a = cls(new, self.workdir, session_id=self.id, **kw)
            a.events = list(history)                  # before_turn() reads them; _carry sends them with the message
            a._carry = any(e.get("type") in ("user", "assistant") for e in history)
        else:
            a = Agent(self.workdir, session_id=self.id, profile="work" if hid == "meshpool-work" else "code", **kw)
            a.messages = [a.messages[0]] + _messages_from_events(history)
            a.interactive = lambda: not self.scheduled and not getattr(self, "deleted", False)
            if hasattr(a, "plan"):
                a.plan = self.plan
        if model and hasattr(a, "model") and not getattr(a, "model", None):
            try:
                from meshpool_harness import providers as PV
                if not new.get("providers") or PV.split(model)[0] in new["providers"]:
                    a.model = model
            except Exception:  # noqa: BLE001
                pass
        self._bind_always(a)                         # "Allow in this chat" goes with the chat, not the agent
        self.agent, self.harness = a, new
        if self.cp:                                   # Meshpool's agent books by its tools, an outside agent by scan
            a_out = new["type"] in ("acp", "pi")
            self.cp.mode = "scan" if a_out else "journal"
            a.checkpoint = None if a_out else self.cp
        try:
            if hasattr(old, "close") and old is not a:
                old.close()
        except Exception:  # noqa: BLE001
            pass
        if new["type"] in ("acp", "pi"):
            if self.mcp:
                try:
                    self.mcp.close()
                except Exception:  # noqa: BLE001
                    pass
                self.mcp = None
        elif self.mcp:
            a.mcp = self.mcp
        else:
            self._mcp_ready.clear()
            threading.Thread(target=self._start_mcp, daemon=True).start()
        a.save()
        write_meta(self.id, routed=hid, handoff=hid)
        if quiet:                                    # the caller says it in its own line: no "Using ..." line
            self.emit({"type": "status", "using": True, "text": f"Using {_short_harness(new['name'])}",
                       "moved": {"harness": hid, "reason": why}})
            return
        self.emit({"type": "status", "using": True, "text": f"Using {_short_harness(new['name'])}",
                   "handoff": {"harness": hid, "reason": why}})

    FALLBACK_LINE = "That way of working failed, so meshpool used its own agent."

    def _watch_outside(self, token) -> None:
        """An outside agent's turn (Codex, Pi, Hermes on the key endpoint): meshpool does not see its model calls, so a
        dead model service looked like a slow one ("Still waiting for the model (3 min)", Maya). When the agent has
        said nothing for MH_OUTSIDE_PROBE_S (15 s), the endpoint's address is checked with a plain connection: nothing
        answering (connection refused, a name that does not resolve) is the service-down line at once, the same line
        meshpool's own agent shows, and it says so again when the service answers. Never through a proxy (a direct
        connection there says nothing); never sends a request."""
        try:
            every = float(os.environ.get("MH_OUTSIDE_PROBE_S", "15") or 15)
        except ValueError:
            every = 15.0
        if every <= 0 or self.harness.get("provider") != "key":
            return
        try:
            from meshpool_harness import providers as PV
            base = PV.key_base()
        except Exception:  # noqa: BLE001
            return
        again = max(1, int(min(every, 10)))
        down, last = False, 0.0
        while (self._turn_id == token and getattr(self, "state", "idle") in _BUSY and not self._stop_req
               and self.harness.get("type") in ("acp", "pi")):
            time.sleep(min(0.5, every / 4))
            now = time.time()
            if (not down and now - getattr(self, "_last_ev_at", now) < every) or now - last < (again if down else every):
                continue
            last = now
            why = _unreachable(base)
            if why is None:
                return                                 # behind a proxy or not a plain address: nothing to tell
            if why and not down:
                down = True
                self.emit({"type": "status", "down": True, "text": f"The model endpoint isn't answering ({why}). "
                                                                   f"Checking again in {again} s. Press Stop to end."})
            elif not why and down:
                down = False
                self.emit({"type": "status", "down": False, "text": "The model endpoint is answering again."})

    def _fallback_prepare(self, caps: list, token) -> None:
        """meshpool's own agent took over a turn the outside engine failed (_engine_fallback): it gets what the turn
        set up for the engine (office tools, connectors, lane, spending task), and the open checkpoint turn becomes
        one its tools book, at the message it starts from, so Undo cuts its conversation there."""
        a = self.agent
        if hasattr(a, "usage_task"):
            a.usage_task = getattr(self, "_usage_task", None)
        if caps:
            self._office_ready(edit_only=list(caps) == ["edit"])
        self._wait_mcp(token)
        rr = getattr(self, "_turn_route", None)
        if rr is not None and hasattr(a, "lane"):
            a.lane = rr.lane if rr.lane != "chat" else "task"
        if hasattr(a, "_repair"):
            a._expect_gen = getattr(a, "_gen", 0)
        if self.cp and hasattr(a, "messages"):
            try:
                self.cp.to_journal(len(a.messages))
            except Exception:  # noqa: BLE001 - a checkpoint never blocks the message
                pass

    def _engine_failure(self, ev0: int | None = None) -> str:
        """The error that ended this turn of an outside engine (Codex, Pi, Hermes) before it did any work: it could not
        start, or the AI service answered its first call with an HTTP 4xx or 5xx, or nothing answered. "" when the turn
        did something (a step, a reply), was stopped, or did not fail that way."""
        if self.harness.get("type") not in ("acp", "pi"):
            return ""
        from meshpool_harness.core import errtext as ET
        with self.lock:
            evs = list(self.events[ev0:]) if ev0 is not None else []
        if any(e.get("type") in ("tool_start", "interrupted", "approval") or (
                e.get("type") == "assistant" and str(e.get("text") or "").strip()) for e in evs):
            return ""
        err = getattr(self.agent, "start_error", None) or getattr(self.agent, "turn_failed", None)
        if err:
            return str(err)
        for e in evs:
            raw = str(e.get("detail") or e.get("text") or "") if e.get("type") == "error" else ""
            if raw and ET.engine_failed(raw):
                return raw
        return ""

    def _auto_engine(self) -> bool:
        """This chat runs on the engine Auto chose (at the start, or when a message needed another agent), not on
        one the user picked."""
        m = read_meta(self.id)
        hid = self.harness.get("id")
        return bool(hid and m.get("routed") == hid and m.get("picked") != hid)

    def _engine_fallback(self, text: str, ev0: int | None = None) -> bool:
        """The outside engine Auto chose failed before doing any work (_engine_failure): the same message runs again on
        meshpool's own agent, with one plain line in the chat and the details in its fold; no engine name, no URL
        (Maya and Priya: "unexpected status 404 Not Found: ..., url: http://127.0.0.1:.../v1/responses", then a click
        on "Use meshpool Code instead" to get anything done). True when the chat moved and the caller should send the
        message again. Once per chat. An engine the user picked keeps its plain error and gets a one-click way to
        meshpool's own agent instead. Never raises."""
        try:
            raw = self._engine_failure(ev0)
            if not raw or self._stop_req or getattr(self, "_fallback_done", False):
                return False
            own = HR.builtin_for(self.kind) or "meshpool-code"
            failed = self.harness.get("id")
            if own == failed:
                return False
            if not self._auto_engine():
                self.emit({"type": "status", "using": True, "text": f"Using {_short_harness(self.harness['name'])}",
                           "back": {"harness": own, "name": HR.get(own)["name"]}})
                return False
            self._fallback_done = True
            from meshpool_harness.core import errtext as ET
            plain, _ = ET.outside(raw)
            if not ET.is_raw(raw) and plain:
                plain = "It could not start."
            line = {"type": "status", "fallback": True, "text": self.FALLBACK_LINE, "detail": plain,
                    "raw": ET._redact(raw)[:2000]}
            if ev0 is not None:
                # The failed attempt (its copy of the message, the error, its turn end) goes: the chat shows the
                # message once, then the line under it, and the next agent's history carries it once. The line that
                # named the engine Auto chose stays where it is (positions never move) but no longer names it.
                with self.lock:
                    kept = []
                    for e in self.events[:ev0]:
                        if e.get("using") and not e.get("handoff") and (
                                (e.get("auto") or {}).get("harness") == failed or e.get("back")):
                            e = dict({k: v for k, v in e.items() if k != "back"}, text="Using meshpool")
                        kept.append(e)
                    self.events = [dict(e, i=i) for i, e in enumerate(kept)]
                    self._after_user = line
                self.epoch += 1                      # the page reloads the transcript instead of appending to it
                self._persist(running=True)
            else:
                self.emit(line)
            self.handoff(own, "the engine Auto chose failed", quiet=True)
            return True
        except Exception:  # noqa: BLE001 - the failed turn already told the user; nothing new may break it
            return False

    # ---- 0.0.10: checks, receipts, memory tracking, learning and insights around a turn. None of it may slow or
    # fail a turn: the slow parts run on a thread, every call is guarded.
    def _turn_begin(self):
        self._ck_run, self._ck_n = f"{self.id}:{int(self.t_turn)}", 0
        if not getattr(self, "_awake", False):         # no idle sleep while this turn runs (awake.py)
            from meshpool_harness import awake as AW
            AW.hold()
            self._awake = True
        try:
            from meshpool_harness import checks as CK
            CK.begin(self._ck_run)
            self._ck_stop = threading.Event()

            def watch(run=self._ck_run, stop=self._ck_stop):
                while not stop.wait(1.5):
                    self._emit_checks(run)
            threading.Thread(target=watch, daemon=True, name="meshpool-checks").start()
        except Exception:  # noqa: BLE001
            self._ck_run = ""
        if self.incognito:
            try:
                from meshpool_harness import memstore as MS
                MS.incognito_begin(self.id)
            except Exception:  # noqa: BLE001
                pass

    def _emit_checks(self, run):
        try:
            from meshpool_harness import checks as CK
            evs = CK.events_for(run, since=self._ck_n)
            self._ck_n += len(evs)
            for e in evs:
                self.emit(e)
        except Exception:  # noqa: BLE001
            pass

    def _turn_finish(self):
        if getattr(self, "_awake", False):
            from meshpool_harness import awake as AW
            AW.release()
            self._awake = False
        lease = getattr(self, "_lease", None)
        if lease:                                    # the run is over: its task may run again
            self._lease = None
            from meshpool_harness import assistant as AS
            AS.release(*lease)
        run = getattr(self, "_ck_run", "")
        if getattr(self, "_ck_stop", None):
            self._ck_stop.set()
        if run:
            try:
                from meshpool_harness import checks as CK
                self._emit_checks(run)
                CK.end(run)
                sm = CK.summary(run)
                look = ""
                ll = getattr(self.agent, "last_look", None)           # the look of this turn: in the footer, not the reply
                if isinstance(ll, dict) and ll.get("turn") == getattr(self.agent, "_turn_no", 0) and ll.get("line") \
                        and ll.get("shown") != self._turn_id:
                    look = str(ll["line"]).split("\n\n")[0]
                    ll["shown"] = self._turn_id
                with self.lock:                              # the turn's own reply: an unsure one gets no "all good"
                    said = next((str(e.get("text") or "") for e in reversed(self.events)
                                 if e.get("type") == "assistant" and not e.get("look")
                                 and str(e.get("text") or "").strip()), "")
                short, full = CK.footer(look, sm, reply=[said, str(ll["line"]) if look else ""])
                if short:
                    # Lina, Priya (round 3): "Checked as ...: found nothing to fix." read as noise in the answer. One
                    # short muted line in the turn's footer ("Checked · all good"), the sentence and items in its fold.
                    self.emit({"type": "checks_line", "text": short, "summary": full, "items": sm.get("items", []),
                               "details": sm.get("details", []), **({"look": look} if look else {})})
            except Exception:  # noqa: BLE001
                pass
            self._ck_run = ""
        if self.incognito:
            try:
                from meshpool_harness import memstore as MS
                MS.incognito_end(self.id)
            except Exception:  # noqa: BLE001
                pass

    def _after_turn(self, text: str, ev0: int, t0: float):
        text0 = text.split("\u200b")[0]
        if not self.incognito:
            try:                                    # facts an agent saved this turn: this chat is their source
                from meshpool_harness import memstore as MS
                MS.attribute_since(t0, self.id, text0)
            except Exception:  # noqa: BLE001
                pass
            try:                                    # the learning loop: what the user taught goes to memory,
                from meshpool_harness import learn as LRN   # for this chat's next turn and every new chat
                LRN.after_turn_async(self.id, text, _last_reply(self.agent), on_learned=lambda got: self.emit(
                    {"type": "status", "text": "Noted for next time: " + "; ".join(got),
                     "undo": {"ids": list(LRN.LAST_IDS.get(self.id, []))}}))
            except Exception:  # noqa: BLE001 - learning never touches the turn
                pass
        try:                                        # the receipt: time, cost and the agent Auto chose
            from meshpool_harness import receipts as RC
            r = RC.receipt_for(self.id)
            if r:
                self.emit({"type": "task_receipt", **r})
        except Exception:  # noqa: BLE001
            pass
        threading.Thread(target=self._post_turn, args=(ev0,), daemon=True, name="meshpool-after-turn").start()

    def _post_turn(self, ev0: int):
        """Off the turn's path: register files made, summarise an idle chat, look for an insight to show."""
        try:
            if not self.incognito:
                from meshpool_harness import artifacts_registry as AR
                from meshpool_harness.ui import workbench as W
                root = Path(self.workdir).resolve()
                with self.lock:
                    evs = list(self.events[ev0:])
                made = W.outputs_from(evs, [], root)
                for o in made:
                    if o["path"].lower().endswith((".pptx", ".xlsx", ".docx")):
                        try:
                            AR.register(str(root / o["path"]), self.id, incognito=self.incognito)
                        except Exception:  # noqa: BLE001 - one file the registry refuses never skips the title
                            pass
                self._retitle(None, evs)
        except Exception:  # noqa: BLE001
            pass
        try:
            from meshpool_harness import summaries as SU
            if not self.incognito:
                SU.on_idle_async(self.id)
        except Exception:  # noqa: BLE001
            pass
        try:
            from meshpool_harness import insights as IN
            if not self.incognito and hasattr(IN, "scan_after_turn"):
                for c in IN.scan_after_turn(self.id) or []:
                    self.emit({"type": "insight", "card": c})
        except Exception:  # noqa: BLE001
            pass

    def _key_problem(self) -> str:
        """A masked or mangled key: one clear message for every agent ("" when the key looks usable)."""
        try:
            from meshpool_harness import providers as PV
            if self.harness.get("type") != "auto" and PV.split(getattr(self.agent, "model", None) or "")[0] == "key":
                return PV.key_problem(PV.key())
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _wait_mcp(self, token: int):
        """A message sent while the MCP servers still start (up to 45 s) is accepted; its turn waits here, on the turn's
        own thread, until they are up, so the agent has its tools. Stop ends the wait."""
        if self.harness.get("type") in ("acp", "pi"):
            return
        ready = getattr(self, "_mcp_ready", None)
        if ready is None or ready.is_set():
            return
        end = time.time() + float(os.environ.get("MH_MCP_START_S", "45")) + 5
        while not ready.wait(0.2):
            if self._stop_req or self._turn_id != token or time.time() > end:
                break

    def _start_turn(self, text: str, images: list | None, token: int | None = None, cont: bool = False):
        """Run one turn (`cont`: a continuation - goal next turn, resume after an outage - keeps the usage task of the
        message that began the work, so the spending limit covers the whole goal). Run one turn on its own thread. `token` is the turn this message was given by _claim_turn (send, or
        _end_turn handing over to the next message); everything slow (key check, capability wait, MCP wait,
        checkpoint) happens inside it, and every way out of the thread ends it with _end_turn."""
        if token is None:
            token = self._claim_turn()
            if token is None:
                return
        if not (cont and getattr(self, "_usage_task", None)):
            self._usage_task = usage.start_task(chat=self.id, label="scheduled" if self.scheduled else "task")
        if hasattr(self.agent, "usage_task"):
            self.agent.usage_task = self._usage_task          # the agent (or harness) records its calls in this task
        ev0 = len(self.events)
        root = str(Path(self.workdir).resolve()) if self.workdir else ""
        with _ACTIVE_LOCK:
            others = _ACTIVE.setdefault(root, set()) - {self}
            self._turn_shared = bool(others)
            for o in others:
                o._turn_shared = True
            _ACTIVE[root].add(self)
        self._turn_n = None

        def stopped_before_start():                  # Stop pressed before the agent started: never start it
            if not self._stop_req:
                return False
            self.emit({"type": "user", "text": text.split("\u200b")[0].strip(), "images": []})
            self.emit({"type": "interrupted", "text": "Stopped before it started. Your message is kept "
                                                      "above; send it again to run it."})
            return True

        def go():
            synthetic, cp_begun, nxt = False, False, None
            try:
                try:
                    C.load_into_env(override=True)      # newest settings (model, key) for this turn
                except Exception:  # noqa: BLE001
                    pass
                bad = self._key_problem()
                if bad:
                    self.emit({"type": "user", "text": text, "images": []})
                    self.emit({"type": "error", "text": bad})
                    synthetic = True
                    return
                caps = self._capability_turn(text)
                self._wait_mcp(token)
                if stopped_before_start():
                    synthetic = True
                    return
                if hasattr(self.agent, "plan"):
                    changed = bool(self.agent.plan) != self.plan
                    self.agent.plan = self.plan
                    if changed and hasattr(self.agent, "apply_mode"):
                        self.agent.apply_mode()      # an agent with its own modes (Codex): the mode the turn runs in
                idx = len(self.agent.messages) if self.harness["type"] not in ("acp", "pi") else None
                if self.cp:
                    cp_begun = True
                    try:
                        t = self.cp.begin_turn(text.strip().splitlines()[0] if text.strip() else "Images", idx,
                                               event_index=ev0)
                        self._turn_n = (t or {}).get("n")
                        self._attach_to_turn(self._turn_n)
                        if t is None and self.cp.disabled and not getattr(self, "_cp_noted", False):
                            self._cp_noted = True          # say it once per thread, not on every turn
                            self.emit({"type": "status", "text": self.cp.disabled})
                    except Exception as e:  # noqa: BLE001
                        self.emit({"type": "status", "text": f"Could not record a checkpoint: {e}"})
                body = self._body(text, images)
                codex_office = self._codex_office(text)   # Codex's own request: no Meshpool office notes on it
                if caps and not codex_office:           # a file the agent can make: say so, with the tools for it
                    from meshpool_harness import capabilities as CAP
                    body += CAP.note(caps, self.harness, direct=self._office_direct(text))
                from meshpool_harness import pace as PACE
                quick = (not self.plan and not self.scheduled and not self.goal
                         and PACE.classify(text, bool(images)) == "quick")
                self._turn_route = None
                try:                                    # per message: the lane (and, on Auto, the fast model)
                    from meshpool_harness import hrouter as HRR
                    rr = HRR.route(text, kind=self.kind, workdir=self.workdir)
                    self._turn_route = rr
                    if hasattr(self.agent, "lane"):
                        self.agent.lane = "chat" if quick and rr.lane == "chat" else rr.lane if rr.lane != "chat" \
                            else "task"
                    pending = getattr(self, "route_addon", "")
                    if pending and self.harness["type"] in ("acp", "pi"):
                        self.route_addon = ""           # an outside agent: the method rides on its first message
                        body += "\n\n\u200b" + pending.strip()
                except Exception:  # noqa: BLE001 - routing never blocks a message
                    pass
                u0 = dict(getattr(self.agent, "usage", {}) or {})
                if self.goal:
                    body += GOAL_NOTE.format(goal=self.goal)
                if quick:
                    body += PACE.NOTE                   # hidden in the chat; a small budget for a quick request
                    if hasattr(self.agent, "pace_deadline"):
                        self.agent.pace_deadline = time.time() + PACE.QUICK_S
                    elif self.harness["type"] == "pi" and hasattr(self.agent, "nudge"):
                        threading.Thread(target=self._pi_quick_wrap,
                                         args=(self.agent, self.t_turn, agent_token(self.agent), None, token), daemon=True).start()
                elif hasattr(self.agent, "pace_deadline"):
                    self.agent.pace_deadline = 0
                commits: list = []                   # what this turn used up: spent only once it really starts (M20-03)
                if self.ctx_pending:
                    try:
                        from meshpool_harness import assistant as AS
                        m0 = read_meta(self.id)
                        pid = m0.get("project_id") or ""
                        ex = list(m0.get("memory_exclude") or [])
                        outside = self.harness.get("type") in ("acp", "pi")
                        tools = "all"                  # which Meshpool tools this agent has (names only those)
                        if outside:
                            tools = "deferred" if self.harness.get("type") == "pi" else (
                                "all" if not self.harness.get("lean") else "none")
                        elif codex_office:             # Codex's request has one command tool: name no Meshpool tool
                            tools = "none"             # (pdf_report: told to check with view_output, it searched the
                                                       # disk for it and re-rendered the PDF, 42 calls vs Codex's 10)
                        body += "\n\n\u200b" + AS.context_block(
                            text, self.workdir, pid, chat_id=self.id, incognito=self.incognito, exclude=ex,
                            lookup=not m0.get("project_unbound"), rules=outside, tools=tools,
                            chat=m0.get("code_mode") is not True,     # Chat mode: plain outcomes, not code
                            data_rule=outside or bool(getattr(self.agent, "_style", lambda: False)()),
                            # Pi: the office rules ride on the message that asks for a file (capabilities.note),
                            # not on every chat's first message
                            office=False if codex_office else ("direct" if self._office_direct(text) else True) if (
                                tools != "none" and tools != "deferred") or (
                                bool(getattr(self.agent, "office_on", False)) and not caps) else False)

                        def spend_ctx(pid=pid, ex=ex):
                            self.ctx_pending = False
                            write_meta(self.id, ctx_pending=False, memory_exclude_sent=ex,
                                       **({"project_ctx": pid} if pid else {}))
                        commits.append(spend_ctx)
                    except Exception:  # noqa: BLE001
                        self.ctx_pending = False
                else:                               # a chat moved into a project later gets the project once, next turn
                    try:
                        m = read_meta(self.id)
                        pid = m.get("project_id") or ""
                        if pid and m.get("project_ctx") != pid:
                            from meshpool_harness import assistant as AS
                            pb = AS.project_block(pid, text, m.get("memory_exclude") or [])
                            if pb:
                                body += "\n\n\u200b" + pb
                            commits.append(lambda pid=pid: write_meta(self.id, project_ctx=pid))
                    except Exception:  # noqa: BLE001
                        pass
                try:                                    # what Undo did since the last message (note_undo)
                    un = str(read_meta(self.id).get("undo_note") or "")
                    if un:
                        body += "\n\n\u200b" + un
                        commits.append(lambda: write_meta(self.id, undo_note=""))
                except Exception:  # noqa: BLE001
                    pass
                try:                                    # facts the user left out of this chat after its start
                    if not self.ctx_pending:
                        m1 = read_meta(self.id)
                        ex, sent = list(m1.get("memory_exclude") or []), list(m1.get("memory_exclude_sent") or [])
                        if set(ex) - set(sent):
                            from meshpool_harness import memstore as MS
                            gone = [f["text"] for f in map(MS.get, set(ex) - set(sent)) if f]
                            if gone:
                                body += ("\n\n\u200b" + "The user left these out of this chat. Ignore them from now on:\n"
                                         + "\n".join("- " + g for g in gone))
                            commits.append(lambda ex=ex: write_meta(self.id, memory_exclude_sent=ex))
                except Exception:  # noqa: BLE001
                    pass
                try:                                    # preferences learned after the last turn apply now
                    from meshpool_harness import learn as LRN
                    lines = LRN.peek(self.id)
                    if lines:
                        body += "\n\n\u200b" + LRN.note(lines)
                        commits.append(lambda lines=lines: LRN.commit(self.id, lines))
                except Exception:  # noqa: BLE001
                    pass
                # The Stop generation of the agent that will run the turn (a handoff above may have replaced it):
                # a Stop from here on either trips the check below or changes the generation send() checks.
                gen0 = getattr(self.agent, "_gen", 0)
                if stopped_before_start():          # Stop pressed while the checkpoint was taken
                    synthetic = True
                    return
                for spend in commits:
                    try:
                        spend()
                    except Exception:  # noqa: BLE001
                        pass
                self._turn_begin()
                if self.harness.get("type") not in ("acp", "pi") and hasattr(self.agent, "_repair"):
                    self.agent._expect_gen = gen0     # a Stop since the message was taken ends this turn at once
                elif self.harness.get("type") in ("acp", "pi"):
                    threading.Thread(target=self._watch_outside, args=(token,), daemon=True).start()
                if images:
                    self.agent.send(body, images=images)
                else:
                    self.agent.send(body)
                if self._engine_fallback(text, ev0):   # the engine Auto picked failed before any work: run it here
                    self._fallback_prepare(caps, token)
                    if images:
                        self.agent.send(body, images=images)
                    else:
                        self.agent.send(body)
                try:
                    from meshpool_harness.hrouter import outcomes as OUT
                    OUT.log_turn(self, getattr(self, "_turn_route", None), u0, ev0=ev0,
                                 turn=self._turn_n if self._turn_n is not None
                                 else sum(1 for e in self.events if e.get("type") == "user" and not e.get("steer")))
                except Exception:  # noqa: BLE001
                    pass
                self._after_turn(text, ev0, self.t_turn)
            except Exception as e:  # noqa: BLE001 - a bug here must end in a message, not a chat stuck on working
                try:
                    with self.lock:
                        shown = any(x.get("type") == "user" for x in self.events[ev0:])
                    if not shown:
                        self.emit({"type": "user", "text": text.split("\u200b")[0].strip(), "images": []})
                    from meshpool_harness.core import errtext as ET
                    plain, raw = ET.outside(str(e)[:2000])
                    self.emit({"type": "error", "text": f"This message could not run: {(plain or str(e))[:300]}",
                               **({"detail": raw} if raw else {})})
                except Exception:  # noqa: BLE001
                    pass
            finally:
                try:
                    nxt = self._turn_epilogue(root, ev0, cp_begun)
                except Exception:  # noqa: BLE001
                    nxt = None
                self._end_turn(token, synthetic, nxt)
        threading.Thread(target=go, daemon=True).start()

    def _turn_epilogue(self, root: str, ev0: int, cp_begun: bool):
        """What runs after a turn, however it ended. Each step is guarded alone: one failing (a full disk, a locked
        file) never skips the rest. Returns the goal or auto-resume follow-up (message, images), or None."""
        def guard(fn, *a):
            try:
                return fn(*a)
            except Exception:  # noqa: BLE001
                return None
        guard(self._finish_values)                   # before the checkpoint closes: the write is booked to this turn
        guard(self._turn_finish)
        with _ACTIVE_LOCK:
            _ACTIVE.get(root, set()).discard(self)
        if self.cp and cp_begun:
            guard(self._end_cp_turn)
        while (not getattr(self.agent, "_stop", False) and not self._stop_req and hasattr(self.agent, "has_steers")
               and guard(self.agent.has_steers)):         # added with Enter just as the turn ended: answer it now
            try:
                self._resume_steers()
            except Exception as e:  # noqa: BLE001
                guard(self.emit, {"type": "error", "text": str(e)})
                break
        guard(lambda: write_meta(self.id, model=getattr(self.agent, "model", None), plan=self.plan))
        stopped = getattr(self.agent, "_stop", False) or self._stop_req
        with self.lock:                              # a message waiting to run: it goes first, the goal waits
            waiting = bool(self._after_stop) or bool(self.queue)
        if waiting or stopped:
            return None
        if self.goal:
            return guard(self._goal_next, ev0)       # these take self.lock themselves: never called under it
        return guard(self._resume_next, ev0)

    def _end_turn(self, token: int, synthetic: bool = False, nxt=None, notice: bool = True):
        """The only way a turn ends. A stale token is ignored. A turn that opened (a "user" event) but never
        closed gets a synthetic turn_end, so the saved transcript never says it is still running. Then, under the
        lock: the next message (sent after Stop, then the queue, then the goal / auto-resume follow-up `nxt` the
        caller decided) takes over as a new turn, or the chat goes idle. Approvals this turn left open are closed."""
        if token != self._turn_id:
            return
        self._retry_open = False                     # no retry_ok for a turn that ends in an error
        try:
            if synthetic or getattr(self, "_turn_open", False):
                self.emit({"type": "turn_end", "synthetic": True})
        except Exception:  # noqa: BLE001
            pass
        item, skip_late = None, False
        while True:
            with self.lock:
                if token != self._turn_id:
                    return
                agent = self.agent
                stopped = getattr(agent, "_stop", False)
                late = (not skip_late and not stopped and not self._stop_req and hasattr(agent, "has_steers")
                        and agent.has_steers())
                if not late:
                    if self._after_stop:             # sent after Stop: always runs, as its own turn
                        items, self._after_stop = self._after_stop, []
                        item = ("\n\n".join(t for t, _ in items), [im for _, ims in items for im in (ims or [])])
                    elif getattr(self, "queue", None):   # Stop clears the queue: what is in it now was sent after Stop
                        items, self.queue = self.queue, []
                        item = ("\n\n".join(t for t, _ in items), [im for _, ims in items for im in (ims or [])])
                        if stopped and self.harness.get("type") == "acp":
                            item = (item[0] + self._STOPPED_NOTE, item[1])
                    elif nxt and not stopped and not self._stop_req:
                        item = nxt
                    for p in getattr(self, "pendings", {}).values():  # a card of this turn nobody can answer any more
                        if p.get("in_turn") and p.get("token") == token and not p.get("answered"):
                            p["decision"], p["closed"] = "no", True
                            p["ev"].set()
                    if self._interrupting == token:
                        self._interrupting = None
                    if item:
                        self._turn_id += 1
                        new = self._turn_id
                        self.state, self._stop_req, self._was_stopped = "working", False, False
                        self.t_turn = time.time()
                    else:
                        took = time.time() - getattr(self, "t_turn", time.time())
                        self.state = "idle"
                    break
            try:
                self._resume_steers()
            except Exception as e:  # noqa: BLE001
                skip_late = True
                try:
                    self.emit({"type": "error", "text": str(e)})
                except Exception:  # noqa: BLE001
                    pass
        if item:
            try:
                self.emit({"type": "queued", "text": "", "n": 0})
                self._start_turn(item[0], item[1] or None, new,
                                 cont=item[0] in (GOAL_NEXT, RESUME_NEXT))   # stays "working": straight into the next one
            except Exception:  # noqa: BLE001
                self._end_turn(new, True)
        elif notice and not synthetic:
            self._turn_done_notice(took)

    def _turn_done_notice(self, took: float):
        """After a turn that went idle: mark the chat unread and, for a long or scheduled one, tell the user."""
        try:
            if not getattr(self, "deleted", False):
                write_meta(self.id, unread=True)     # cleared by the page when the chat is opened
        except Exception:  # noqa: BLE001
            pass
        try:
            if (self.scheduled or took > 45) and not getattr(self.agent, "_stop", False):
                last = next((e.get("text") for e in reversed(self.events)
                             if e.get("type") == "assistant" and e.get("text")), "") or "Finished."
                _notify(("Done: " if not self.scheduled else "Scheduled task done: ")
                        + (self.agent.title or "task")[:60], last.strip()[:200])
        except Exception:  # noqa: BLE001
            pass

    def _finish_values(self) -> int:
        """meshpool's finish hook for every harness: workbooks this turn wrote whose formulas have no stored results
        get them (xl_values.fill), booked to the turn like any write. meshpool's own agent already does it when it
        answers (agent/core._cache_formula_values); an outside agent (Codex, Pi, Hermes) did not, and Priya's
        Codex-made workbook was saved with no totals showing. Only files the turn wrote: one it only read is never
        rewritten. Returns how many cells were filled."""
        if os.environ.get("MH_CACHE_FORMULAS", "1") == "0" or not self.cp:
            return 0
        wrote = self.cp.turn_written()
        if not wrote:
            return 0
        from meshpool_harness import xl_values
        n = 0
        for rel in wrote[:200]:
            p = Path(self.cp.root) / rel
            if p.suffix.lower() != ".xlsx" or p.name.startswith("~$") or not p.is_file():
                continue
            with self.cp.own_write(p):
                n += xl_values.fill(p)
        return n

    def _end_cp_turn(self):
        """Close the open checkpoint turn, remember the turn counter, and tell the page which turn it was."""
        if not self.cp:
            return
        try:
            self.cp.end_turn()
            write_meta(self.id, turn_seq=self.cp.seq)
            from meshpool_harness.ui import workbench as W
            turns = self.cp.turns()
            ev = {"type": "checkpoint", "turns": W.own_changes(turns[-1:], self.cp.root)}
            try:                                   # files the turn said it saved and then deleted: no card for them
                with self.lock:
                    evs = list(self.events[int((turns[-1] if turns else {}).get("event_index") or 0):])
                gone = W.gone_files(evs, self.cp.root)
                if gone:
                    ev["gone"] = gone
            except Exception:  # noqa: BLE001
                pass
            self.emit(ev)
            self._retitle(turns)
        except Exception:  # noqa: BLE001
            pass

    def _retitle(self, turns: list | None = None, evs: list | None = None) -> str:
        """Name the chat after what it made, from every turn's checkpoint (the files really added) and this turn's
        events. Daniel, 0.0.18: the chat kept "This is the Northfield bench...": the event scan missed "Saved as: X"
        and "Files: - X", so retitle_from_work saw no deliverable."""
        if self.incognito:
            return ""
        try:
            from meshpool_harness.ui import workbench as W
            root = Path(self.cp.root if self.cp else self.workdir).resolve()
            made: dict = {}
            if turns is None and self.cp:
                turns = self.cp.turns()
            for t in W.own_changes(turns or [], root):
                for c in t.get("changed") or []:
                    if c.get("status") == "added":
                        made.setdefault(str(c["path"]), {"path": str(c["path"])})
            for o in W.outputs_from(evs or [], [], root):
                made.setdefault(o["path"], o)
            with self.lock:                        # a file the user attached is theirs, not something this chat made
                said = [str(e.get("text") or "") for e in self.events if e.get("type") == "user"]
            attached = {x.strip() for t in said for line in re.findall(r"Attached files \(in [^)]*\): ([^\n]+)", t)
                        for x in line.split(", ")}
            return retitle_from_work(self.id, root, [o for k, o in made.items() if k not in attached])
        except Exception:  # noqa: BLE001
            return ""

    def _resume_steers(self):
        """Answer what the user added just as the turn ended, as a turn of its own: its file changes get their own
        checkpoint (so Undo and the rewound chat agree), titled by the added message."""
        title, ev_i = "Added message", None
        with self.lock:
            for e in reversed(self.events):
                if e.get("type") == "user" and e.get("steer"):
                    ev_i = e.get("i")
                    t = str(e.get("text") or "").strip()
                    if t:
                        title = t.splitlines()[0]
                    break
            if ev_i is None:
                ev_i = len(self.events)
        began = False
        if self.cp:
            try:
                msgs = None if self.harness["type"] in ("acp", "pi") else len(self.agent.messages)
                began = self.cp.begin_turn(title, msgs, event_index=ev_i) is not None
            except Exception:  # noqa: BLE001
                began = False
        try:
            return self.agent.resume_steers()
        finally:
            if began:
                self._end_cp_turn()

    def grade(self) -> dict:
        """"Check this": an independent review of the chat's last piece of work, on demand (works with the switch off).
        Runs beside nothing else: the chat must be idle. The result arrives as a review event."""
        busy = "Wait for the reply to finish, or stop it, then check it."
        if self.state in _BUSY:
            raise ValueError(busy)
        if not hasattr(self.agent, "grade_now"):
            raise ValueError("This agent cannot be checked by meshpool's reviewer.")
        token = self._claim_turn()
        if token is None:
            raise ValueError(busy)

        def go():
            try:
                self.agent.grade_now()
            except Exception as e:  # noqa: BLE001
                self.emit({"type": "error", "text": "The check could not run: " + str(e)[:160]})
            finally:
                self._end_turn(token, notice=False)
        threading.Thread(target=go, daemon=True, name="meshpool-grade").start()
        return {"started": True}

    def set_route(self, spec: str):
        """The user picked the harness for this chat (the route chip). Applies from the next message; on the
        built-in agents the system prompt changes once, so one request misses the prompt cache."""
        from meshpool_harness import hrouter as HRR
        from meshpool_harness.hrouter.route import addon
        from meshpool_harness.hrouter.specs import SPECS
        if spec not in SPECS:
            raise ValueError(f"Unknown harness {spec!r}.")
        first = next((e.get("text") for e in self.events if e.get("type") == "user"), "") or ""
        r = HRR.route(first, kind=self.kind, workdir=self.workdir, force=spec)
        if hasattr(self.agent, "route"):
            self.agent.route = r
            if self.agent.messages:
                self.agent.messages[0] = {"role": "system", "content": self.agent.system_prompt()}
            self.agent.save()
        else:
            self.route_addon = addon(r)           # an outside agent: rides on the next message
        write_meta(self.id, route=r.spec, route_label=r.label, route_profile=r.profile or None)
        self.emit({"type": "status", "route": r.to_dict(), "text": f"Harness: {r.label} (picked by you)."})

    def set_model(self, model: str, check: bool = False):
        model = (model or "").strip()
        from meshpool_harness import providers as PV
        sup = self.harness.get("providers") or []
        own = {m.get("id") for m in (getattr(self.agent, "available_models", None) or []) if isinstance(m, dict)}
        prov = PV.split(model)[0] if model and model not in own else None   # the agent's own model ids carry no prefix
        if prov and sup and prov not in sup:
            raise ValueError(f"{self.harness['name']} cannot run {PV.name(prov)} models. Start a new chat with an "
                             "agent that can.")
        if (prov and self.harness["type"] == "acp" and self.harness.get("provider")
                and prov != self.harness["provider"] and self.agent.acp_session):
            raise ValueError(f"This chat runs {self.harness['name']} on {self.harness.get('runs_on')}. To use "
                             f"{PV.name(prov)}, start a new chat.")
        # A pick in the model menu (check=True, the "model" action only) on an agent that uses the picker's models: a
        # name the endpoint's list lacks is refused. Never when a chat is created or reopened: a chat saved on a model
        # that is gone must still open (review, 0.0.18: get(sid) raised and the chat's history could not be shown).
        # A variant suffix (OpenRouter's ":nitro", ":online") counts as its base model, which is what the list has.
        if check and prov and self.harness.get("models") == "meshpool" and model != getattr(self.agent, "model", None):
            known = _known_models(prov)
            bare = PV.split(model)[1]
            if known is not None and bare not in known and bare.split(":", 1)[0] not in known:
                raise ValueError(f"The endpoint does not offer a model named {model}. Pick one from the model list.")
        if hasattr(self.agent, "set_model"):
            self.agent.set_model(model)
        else:
            self.agent.model = model or None
        write_meta(self.id, model=model)

    def set_goal(self, text: str):
        """/goal: keep working until this is done. An empty text clears it."""
        had = self.goal
        self._goal_t = time.time()
        self.goal, self._goal_idle, self._goal_net = (text or "").strip()[:2000], 0, 0
        write_meta(self.id, goal=self.goal)
        if not self.goal and not had:
            return                                   # nothing was set: no "Goal cleared." line
        self.emit({"type": "status", "text": f"Goal set: {self.goal}. The agent keeps going until it is done; press "
                                             "Stop or clear the goal to end it." if self.goal else "Goal cleared."})

    def _goal_next(self, ev0: int):
        """After a turn with a goal: the next turn's message, or None when the goal is met or the user is needed."""
        with self.lock:
            evs = self.events[ev0:]
        out = _turn_outcome(evs)
        reply = next((e.get("text") or "" for e in reversed(evs) if e.get("type") == "assistant"), "")
        if getattr(getattr(self, "agent", None), "budget_stopped", False):
            self.emit({"type": "status", "text": "Goal paused: the spending limit for this task was reached. Raise it "
                                                 "in Settings or send a message to continue."})
            return None                 # the goal stays set; a new message starts a new task with a fresh limit
        if getattr(self, "_goal_t", 0) > getattr(self, "t_turn", 0):
            return (GOAL_NEXT, [])      # set while this turn ran: that turn never saw the goal, so it cannot judge it
        found = _GOAL_RE.findall(reply)
        verdict = found[-1].upper() if found else ""
        acted = out["acted"]
        failed = bool(out["error"]) or out["interrupted"]      # ended on an error (not one it recovered from)
        if out["net"] and not out["interrupted"]:
            # An outage, not the task: never give up on the goal for it. The turn already waited for the endpoint;
            # pause a little longer each time (30 s up to 5 min) so a long outage is waited out without hammering
            # the gateway, then pick the goal up. Stop, or clearing the goal, ends the wait.
            n = self._goal_net = getattr(self, "_goal_net", 0) + 1
            wait = min(10 * GOAL_NET_WAIT_S, GOAL_NET_WAIT_S * 2 ** (n - 1))
            self.emit({"type": "status", "text": f"Goal: the model connection failed. Picking the goal up again in "
                                                 f"{wait:.0f} s (try {n}). Press Stop or clear the goal to end it."})
            return self._pause(wait, GOAL_NEXT, lambda: bool(self.goal))
        if not failed:
            self._goal_net = 0
        if verdict == "DONE" and not out["interrupted"] and not out["error"]:
            done, self.goal = self.goal, ""
            write_meta(self.id, goal="")
            self.emit({"type": "status", "text": f"Goal met: {done}"})
            return None
        if failed or verdict != "CONTINUE":
            self.emit({"type": "status", "text": "Goal paused: the agent needs you. Reply to carry on; the goal stays "
                                                 "set." if not failed else "Goal paused after an error. Reply to "
                                                 "carry on; the goal stays set."})
            return None
        self._goal_idle = 0 if acted else self._goal_idle + 1
        if self._goal_idle >= 2:
            self._goal_idle = 0
            self.emit({"type": "status", "text": "Goal paused: two turns in a row without doing anything. Reply "
                                                 "with what to try next; the goal stays set."})
            return None
        return (GOAL_NEXT, [])

    def _pause(self, wait: float, then: str, keep=lambda: True):
        """Wait out an outage or a rate limit before the next turn. Returns (message, images) to send next: `then`
        when the wait runs out, or what the user wrote meanwhile; None when Stop is pressed or keep() turns false."""
        end = time.time() + wait
        while time.time() < end:
            if not keep() or getattr(self, "_stop_req", False) or getattr(self.agent, "_stop", False):
                return None
            if hasattr(self.agent, "has_steers") and self.agent.has_steers():
                break                                # the user wrote meanwhile: go now, their message goes along
            with self.lock:
                if getattr(self, "queue", None):     # ACP harnesses queue it instead: send it, not `then`
                    items, self.queue = self.queue, []
                    return ("\n\n".join(t for t, _ in items), [im for _, ims in items for im in (ims or [])])
            time.sleep(min(0.5, max(0.0, end - time.time())))
        return (then, [])

    def _resume_next(self, ev0: int):
        """After a turn with no goal: when it ended on a rate limit or an outage (not Stop), wait and pick the task
        up again by itself, so a long task is not left half done. None when there is nothing to pick up."""
        if (os.environ.get("MH_AUTO_RESUME", "1") == "0" or not self.auto_resume
                or getattr(getattr(self, "agent", None), "budget_stopped", False)):
            return None                              # off, or the spending limit stopped it: never run on by itself
        with self.lock:
            evs = self.events[ev0:]
        out = _turn_outcome(evs)
        if out["interrupted"]:
            self._resume_n = 0
            return None
        if not out["net"]:
            self._resume_n = 0                       # a good turn (or another kind of error): start the count again
            return None
        n = self._resume_n = getattr(self, "_resume_n", 0) + 1
        if n > RESUME_TRIES:
            self._resume_n = 0
            self.emit({"type": "status", "text": f"The model is still unavailable after {RESUME_TRIES} tries. "
                                                 "Everything done so far is kept: send \"continue\" when it is back "
                                                 "to pick up where it stopped."})
            return None
        wait = min(10 * RESUME_WAIT_S, RESUME_WAIT_S * 2 ** (n - 1))
        self.emit({"type": "status", "text": f"The model is unavailable right now (rate limit or outage). Picking "
                                             f"the task up again in {wait:.0f} s (try {n} of {RESUME_TRIES}). Type "
                                             "a message or press Stop to take over."})
        return self._pause(wait, RESUME_NEXT)

    def _pi_quick_wrap(self, agent, turn, tok0, wait: float | None = None, turn_tok: int | None = None):
        """Pi: when a quick request runs past its time, tell Pi to answer (a note only the model sees). It targets the
        turn send() starts after tok0 and nothing later; a real task added meanwhile (send) cancels it."""
        from meshpool_harness import pace as PACE
        end = time.time() + (PACE.QUICK_S + 10 if wait is None else wait)
        tok = tok0
        while tok == tok0 and time.time() < end:          # send() has not started its turn yet
            time.sleep(0.05)
            tok = agent_token(agent)
        if tok == tok0:
            return False
        time.sleep(max(0.0, end - time.time()))
        if (self.state == "working" and self.t_turn == turn and not getattr(agent, "_stop", False)
                and getattr(self, "_quick_off", None) != turn and (turn_tok is None or self._turn_id == turn_tok)):
            try:
                return bool(agent.nudge(PACE.WRAP, tok))   # only while that same turn runs; no chat message
            except Exception:  # noqa: BLE001
                return False
        return False

    def set_plan(self, on: bool):
        self.plan = bool(on)
        if self.state in ("working", "waiting"):     # the running turn keeps its tools; this applies to the next one
            write_meta(self.id, plan=self.plan)
            self.emit({"type": "status", "text": f"Plan mode turns {'on' if self.plan else 'off'} from your next "
                                                 "message."})
            return
        if hasattr(self.agent, "plan"):
            self.agent.plan = self.plan
        if hasattr(self.agent, "apply_mode"):
            self.agent.apply_mode()                  # an agent with its own modes (Codex) switches to read-only
        write_meta(self.id, plan=self.plan)

    EFFORTS = ("off", "low", "medium", "high", "xhigh")

    def _apply_effort(self):
        self.agent.effort = self.effort                  # core.Agent reads it when it builds the request
        if hasattr(self.agent, "set_effort"):
            self.agent.set_effort(self.effort)           # an agent with its own models (Codex's bare[effort] ids)

    def set_effort(self, level: str):
        """/effort: how hard the model thinks, for this chat only ("" goes back to the Settings value)."""
        level = (level or "").strip().lower()
        if level in ("default", "auto", "none-set"):
            level = ""
        if level and level not in self.EFFORTS:
            raise ValueError("Effort is one of: " + ", ".join(self.EFFORTS) + " (or empty for the default).")
        self.effort = level
        self._apply_effort()
        write_meta(self.id, effort=level)

    def _turn_cut(self, turn: int):
        """Position in the transcript of the user event that started this checkpoint turn, or None (old turns)."""
        from meshpool_harness import chat_rewind as CR
        t = next((x for x in (self.cp.turns() if self.cp else []) if x.get("n") == int(turn)), None)
        if not t:
            return None
        with self.lock:
            return CR.turn_event_pos(self.events, t)

    def revert(self, turn: int) -> dict:
        """Undo checkpoint turn `turn` and everything after it: the files, the conversation and the transcript. Holds
        the turn while it runs (see _hold_turn), so a message sent meanwhile waits and runs after it."""
        if not self.cp:
            raise ValueError("No checkpoints for this chat.")
        token = self._hold_turn("Stop the agent before undoing a turn.")
        try:
            return self._revert(turn)
        finally:
            self._end_turn(token, notice=False)

    def _revert(self, turn: int) -> dict:
        cut = self._turn_cut(turn)
        with self.lock:
            had = (len(getattr(self.agent, "messages", None) or []), len(self.events))
        try:
            undone = sum(1 for t in self.cp.turns() if int(t.get("n") or 0) >= int(turn)) or 1
        except Exception:  # noqa: BLE001
            undone = 1
        r = self.cp.revert_to_before(int(turn))
        idx = r.get("message_index")
        built_in = self.harness["type"] not in ("acp", "pi")
        if built_in and isinstance(idx, int) and 0 < idx <= len(self.agent.messages):
            self.agent.messages = self.agent.messages[:idx]
            self.agent.save()
            with self.lock:
                self._open_deltas = []
                self._open_thoughts = []
                if cut is not None:                  # keep the transcript up to there: earlier turns keep their Undo
                    self.events = [dict(e, i=i) for i, e in enumerate(self.events[:cut])]
                else:
                    self.events = events_from_messages(self.agent.messages[1:])
        else:
            if hasattr(self.agent, "forget_session"):
                self.agent.forget_session()          # the harness's own session still holds the undone turns
            with self.lock:
                # the harness keeps its own history; keep the transcript up to that turn
                if cut is None:
                    users = [i for i, e in enumerate(self.events) if e.get("type") == "user" and not e.get("steer")]
                    cut = users[int(turn) - 1] if 0 < int(turn) <= len(users) else len(self.events)
                self._open_deltas = []
                self._open_thoughts = []
                self.events = [dict(e, i=i) for i, e in enumerate(self.events[:cut])]
                if hasattr(self.agent, "events"):
                    self.agent.events = [{k: v for k, v in e.items() if k not in ("i", "t")} for e in self.events]
                    self.agent.save()
        self.epoch += 1
        with self.lock:                          # whether the conversation lost anything (undo_message says so)
            r["conversation"] = (len(getattr(self.agent, "messages", None) or []), len(self.events)) != had
        self._persist(running=False)
        try:                                     # Undo counts against every logged turn it removed; never breaks Undo
            from meshpool_harness.hrouter import outcomes as OUT
            logged = {x.get("turn") for x in OUT.rows(self.id) if isinstance(x.get("turn"), int)}
            for n in sorted(t for t in logged if t >= int(turn)):
                OUT.mark(self.id, "undo", n)
        except Exception:
            pass
        msg, detail = undo_message(r)
        self.emit({"type": "status", "text": msg, **({"detail": detail} if detail else {}),
                   "undo_files": {"deleted": list(r.get("deleted") or []), "restored": list(r.get("restored") or [])}})
        if r.get("conversation") is not False or r.get("restored") or r.get("deleted"):
            self.note_undo(undo_note(r, undone))
        return r

    def note_undo(self, note: str) -> None:
        """What Undo did, for the model: it rides, hidden, on the next message (see _start_turn), as the other notes
        do. Without it the conversation was rewound with no trace, and "did my prices go back?" got a confused answer
        (Maya). The undone messages stay gone."""
        if not note:
            return
        try:
            old = str(read_meta(self.id).get("undo_note") or "")
            write_meta(self.id, undo_note=(old + " " + note).strip() if old else note)
        except Exception:  # noqa: BLE001 - a note never breaks Undo
            pass

    def rewind(self, event_index: int, files: bool = True) -> dict:
        """Go back to just before one of the user's messages (the event at `event_index`): the conversation, the
        transcript and, with `files`, the file changes made from there on. Returns {text, undo[, forked]}."""
        from meshpool_harness import chat_rewind as CR
        token = self._hold_turn("Stop the agent before rewinding.")      # as revert(): a message sent meanwhile waits
        try:
            r = CR.rewind(self.id, CR.message_for_event(self, int(event_index)), files=bool(files), session=self,
                          held=True)
            if r.get("undo"):
                msg, detail = undo_message(r["undo"])
                u = r["undo"]
                self.emit({"type": "status", "text": "Rewound. " + msg[len("Undone. "):],
                           **({"detail": detail} if detail else {}),
                           "undo_files": {"deleted": list(u.get("deleted") or []), "restored": list(u.get("restored") or [])}})
            return r
        finally:
            self._end_turn(token, notice=False)

    def retry(self) -> dict:
        """Try again (the page's line under a long wait): stop the turn if one runs, wait for it to end, then run the
        user's last message again in place of that attempt. The attempt leaves the chat, so the message shows once and
        no "Stopped. Send a message to continue." line is left behind (Maya: Try again posted the message twice under
        "Stopped."). Files stay as they are. Returns what send returns."""
        if getattr(self, "state", "idle") in _BUSY:
            self.stop()
            end = time.time() + float(os.environ.get("MH_RETRY_STOP_S", "25"))
            while getattr(self, "state", "idle") in _BUSY and time.time() < end:
                time.sleep(0.1)
            if getattr(self, "state", "idle") in _BUSY:
                raise ValueError("It is still stopping. Press Try again in a moment.")
        with self.lock:
            pos = next((i for i in range(len(self.events) - 1, -1, -1) if self.events[i].get("type") == "user"
                        and not self.events[i].get("steer") and str(self.events[i].get("text") or "").strip()), None)
            text = str(self.events[pos].get("text") or "") if pos is not None else ""
        if pos is None:
            raise ValueError("There is no message to send again.")
        from meshpool_harness import chat_rewind as CR
        r = CR.rewind(self.id, CR.message_for_event(self, pos), files=False, session=self)
        if r.get("forked"):                           # an outside agent that cannot drop the attempt: send it as is
            text = r.get("text") or text
        self._was_stopped = False                    # the message itself runs again: no "I stopped" note with it
        return self.send(text)

    def fork(self, event_index: int) -> str:
        """A new chat with the conversation up to the end of the exchange that starts at this message."""
        from meshpool_harness import chat_rewind as CR
        return CR.fork(self.id, CR.message_for_event(self, int(event_index)), session=self)

    def stop(self):
        with self.lock:
            self._stop_req = True                    # checked before the agent starts: its own flag resets then
            if getattr(self, "state", "idle") in _BUSY:
                self._was_stopped = True             # a request really was cut short
            had = bool(self.queue)
            self.queue = []
            pend = list(self.pendings.values())
        if had:
            self.emit({"type": "queued", "text": "", "n": 0})
        if self.agent:
            self.agent.stop()
        for p in pend:
            p["ev"].set()

    def set_mode(self, mode: str, save: bool = True):
        """Change the access mode. save=False is for this run only: the chat's stored mode stays as it was."""
        self.agent.mode = self.mode = C.normalize_mode(mode, "ask")
        if save:
            write_meta(self.id, mode=self.mode)      # restored on resume: an Ask chat stays Ask
        if hasattr(self.agent, "apply_mode"):
            try:
                self.agent.apply_mode()
            except Exception:  # noqa: BLE001
                pass

    def view(self, since: int) -> dict:
        self.touched = time.time()
        with self.lock:
            n = len(self.events)
            raw = self.events[since:]
        evs = [e for e in raw if not e.get("gone")]  # a folded reply's deltas: its assistant event shows the text
        evs = [_with_plain(e) for e in evs]
        a = self.agent
        meta = read_meta(self.id)
        try:
            oq = (a.pending_question() or {}).get("id") if hasattr(a, "pending_question") else None
        except Exception:  # noqa: BLE001
            oq = None
        with self.lock:
            open_ids = [k for k, p in self.pendings.items() if not p.get("closed")]
        return {"id": self.id, "workdir": self.workdir, "state": self.state, "mode": a.mode,
                "open": {"approvals": open_ids, "question": oq},
                "turn_started": getattr(self, "t_turn", None) if self.state in ("working", "waiting") else None,
                # True while a model request is made or waited on, False when none is; None when the harness cannot say
                "model_inflight": (getattr(a, "model_req_at", None) is not None) if hasattr(a, "model_req_at") else None,
                "title": meta.get("title") or nice_title(a.title), "pinned": bool(meta.get("pinned")),
                "project_id": _project_of(meta, self.workdir), "scratch": bool(meta.get("scratch")),
                "code_mode": meta.get("code_mode"),       # False: a chat in a folder that was never started from the Code tab
                "usage": a.usage, "context": getattr(a, "context", None), "kind": self.kind,
                "harness": {k: self.harness.get(k) for k in ("id", "name", "type", "description", "models", "provider",
                                                             "providers", "runs_on")},
                "model": (a.current_model() if hasattr(a, "current_model") else
                          getattr(a, "model", None) or a.agent_info.get("name", self.harness["name"])),
                "harness_models": getattr(a, "available_models", None), "plan": self.plan, "goal": self.goal,
                "effort": self.effort,
                "style": self.style_id() or _default_style(), "incognito": bool(self.incognito),
                "worktree": meta.get("worktree") or None, "versions": meta.get("versions") or {},
                "epoch": self.epoch, "turns": _own_turns(self.cp),
                "checkpoints_note": self.cp.disabled if self.cp else "",
                "events": evs, "total": since + len(raw) if since <= n else n,
                "mcp": self.mcp.status() if self.mcp else None, "trust": _trust_note(self.workdir)}

    def close(self):
        self._closed = True                           # an MCP start still running closes its servers when it finishes
        self.stop()
        _release(getattr(self, "id", ""))
        if self.mcp:
            self.mcp.close()
        if hasattr(self.agent, "close"):
            self.agent.close()


# ---------- module API used by server.py ----------------------------------------------------------------------------

def _default_style() -> str:
    try:
        from meshpool_harness import styles as ST
        return ST.default_id()
    except Exception:  # noqa: BLE001
        return "normal"


_TRUST_NOTES: dict = {}             # workdir -> (stamp of the files the note depends on, note)


def _trust_stamp(workdir: str) -> tuple:
    """What the trust note depends on: the trusted-folders files and the project's risky files (inode, mtime, size)."""
    from meshpool_harness import trust as TR
    w = Path(workdir)
    out: list = [os.environ.get("MH_TRUST_PROJECT", ""), str(C.home())]
    for f in (TR._store(), C.home() / "trusted_projects.json", w / ".meshpool" / "hooks.json",
              w / ".meshpool" / "permissions.json", w / ".mcp.json", w / ".meshpool" / "mcp.json"):
        try:
            st = os.stat(f)
            out.append((st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _trust_note(workdir: str) -> dict | None:
    """When the folder has hooks or allow rules that are being ignored until the user trusts it. Every chat view asks,
    so the answer is kept until one of the files it depends on changes."""
    if not workdir:
        return None
    try:
        from meshpool_harness import trust as TR
        stamp = _trust_stamp(workdir)
        hit = _TRUST_NOTES.get(workdir)
        if hit and hit[0] == stamp:
            return dict(hit[1], files=list(hit[1]["files"])) if hit[1] else None
        files = TR.needs_prompt(workdir)
        note = {"files": files, "text": TR.message(workdir)} if files else None
    except Exception:  # noqa: BLE001 - a status line must never break the chat view
        return None
    if len(_TRUST_NOTES) > 200:
        _TRUST_NOTES.clear()
    _TRUST_NOTES[workdir] = (stamp, note)
    return dict(note, files=list(files)) if note else None


def create(body: dict) -> dict:
    """Start a chat. A private folder made for it is removed again when anything before the chat exists fails (M20-04)."""
    made: list = []
    try:
        return _create(body, made)
    except BaseException:
        from meshpool_harness import project_store as PS
        for p in made:
            PS.remove_scratch(p)
        raise


def _create(body: dict, made: list) -> dict:
    from meshpool_harness import project_store as PS
    from meshpool_harness.ui.desktop import default_project
    wd = (body.get("workdir") or "").strip().strip('"')
    pid = (body.get("project_id") or "").strip()
    if pid and not PS.get_meta(pid):
        raise ValueError("That project does not exist any more.")
    link = (PS.get_meta(pid).get("link") or {}) if pid else {}
    scratch = False
    if not wd and link.get("path"):                 # a chat in a project with a linked folder or repository works there
        wd = link["path"]
        if not Path(wd).is_dir():
            raise ValueError(f"This project's folder ({wd}) was moved or deleted. Link it again on the project page.")
    if not wd:                                      # a chat that needs no folder: it gets a private one
        wd, scratch = PS.new_scratch(pid), True
        made.append(wd)
    elif Path(wd).expanduser().resolve() == default_project().resolve():
        wd = str(default_project())
        Path(wd).mkdir(parents=True, exist_ok=True)
    harness, routed = body.get("harness") or None, None
    kind_given = body.get("kind")
    kind = kind_given or ("code" if link.get("path") else "cowork")
    task = body.get("task") or ""
    hr = None
    try:                                            # the specialised harness: a few ms, no model call
        from meshpool_harness import hrouter as HRR
        from meshpool_harness.hrouter.specs import get as spec_of
        hr = HRR.route(task, files=body.get("files") or [], workdir=wd, kind=kind,
                       force=body.get("spec") or None) if (task or body.get("spec")) else None
    except Exception:  # noqa: BLE001
        hr = None
    if (harness or HR.default_for(kind)) == "meshpool-auto":
        from meshpool_harness import harness_router as HRT
        if not kind_given and kind == "code":
            # Chat mode in a linked folder: office work is Work's (Priya: rooms.xlsx in her Studio folder went to Codex
            # as "docs words in a Code chat"). The Code tab (kind given) keeps its own agents (S-04).
            try:
                from meshpool_harness.office_intent import office_work
                if office_work(task, body.get("files") or []):
                    kind = "cowork"
            except Exception:  # noqa: BLE001
                pass
        routed = HRT.route(task, kind, category=spec_of(hr.spec).backend if hr else None,
                           model=body.get("model") or None)
        harness = routed["harness"]
        if not kind_given:                          # no Work or Code switch: the agent Auto picked decides the kind
            try:
                m = HR.get(harness).get("mode")
                kind = m if m in HR.MODES else kind
            except (KeyError, ValueError):
                pass
    model = body.get("model") or None
    try:
        from meshpool_harness import providers as PV
        h = HR.get(harness or HR.default_for(kind))
        if model and h.get("providers") and PV.split(model)[0] not in h["providers"]:
            model = None                            # a model this agent cannot run: it uses its own default
    except (KeyError, ValueError):
        pass
    plan = body.get("plan")
    if isinstance(plan, str):
        plan = plan.strip().lower() in ("1", "true", "yes", "on")
    if plan is None:                                # no say in the request: Settings, "Plan first", decides
        try:
            from meshpool_harness.ui import settings_v2 as SV2
            plan = SV2.profile()["plan_first"]
        except Exception:  # noqa: BLE001
            plan = False
    wt, made_wt = _chat_worktree(body, wd, kind, scratch, task)
    if wt:
        wd = wt["workdir"]                          # the chat works in its own isolated copy of the folder
    try:
        s = Session(workdir=wd, mode=body.get("mode"), harness=harness,
                    kind=kind, model=model, plan=bool(plan), no_mcp=bool(body.get("no_mcp")))
    except BaseException as e:
        if scratch:                                 # the private folder made above must not be left behind
            PS.remove_scratch(wd)
        if made_wt:
            from meshpool_harness import worktrees as WT
            WT.discard(wt["id"])
        if isinstance(e, OSError) and e.errno in (errno.EMFILE, errno.ENFILE):
            # not "[Errno 24] Too many open files": say what to do (the page shows this text where Send was pressed)
            raise ValueError("meshpool cannot start another chat: this computer's limit of open files was reached. "
                             "Close the chats you are not using, then send again.") from e
        raise
    made.clear()                                    # the chat exists: it owns its folder now
    if body.get("effort"):
        s.set_effort(str(body["effort"]))
    if wt:
        from meshpool_harness import worktrees as WT
        WT.set_owner(wt["id"], "chat", s.id)
        write_meta(s.id, worktree=WT.summary(wt))
    if hr is not None:
        try:
            from meshpool_harness.hrouter.route import addon
            if hasattr(s.agent, "set_route"):
                s.agent.set_route(hr, task)
                s.agent.save()
            else:
                s.route_addon = addon(hr)
            write_meta(s.id, route=hr.spec, route_label=hr.label, route_profile=hr.profile or None)
            if hr.spec not in ("general", "docs"):
                s.emit({"type": "status", "route": hr.to_dict(),
                        "text": f"Working as: {hr.label}."})
        except Exception:  # noqa: BLE001
            pass
    if routed:
        # The chat shows a short neutral label; why Auto picked it travels with the event for the Session panel.
        using_ev = {"type": "status", "using": True, "text": f"Using {_short_harness(routed['name'])}",
              "auto": {"harness": routed["harness"], "category": routed.get("category"), "reason": routed.get("reason")}}
        try:                                        # S-04: Auto picked something other than the mode's own agent: one click back
            back = HR.own_for(kind)
            if back and back != routed["harness"]:
                using_ev["back"] = {"harness": back, "name": HR.get(back)["name"]}
        except Exception:  # noqa: BLE001
            pass
        s.emit(using_ev)
        write_meta(s.id, routed=routed["harness"])
    else:
        try:
            s.emit({"type": "status", "using": True, "text": f"Using {_short_harness(HR.get(harness or HR.default_for(kind))['name'])}"})
        except (KeyError, ValueError):
            pass
    write_meta(s.id, project_id=pid or "", scratch=bool(scratch),
               code_mode=(kind_given == "code") if kind == "code" else None)     # made from the Code tab, or only in a folder
    if body.get("memory_exclude"):                  # facts left out before the first message (the context X-ray)
        write_meta(s.id, memory_exclude=[str(i) for i in body["memory_exclude"]][:200])
    if body.get("incognito"):                       # no memory read or written, no learning, nothing logged for insights
        s.incognito = True
        write_meta(s.id, incognito=True)
    try:
        from meshpool_harness import styles as ST
        picked = str(body.get("style") or "")
        write_meta(s.id, style=picked if picked and ST.get(picked) else ST.default_id())   # fixed for this chat
    except Exception:  # noqa: BLE001
        pass
    if body.get("effort") and hasattr(s.agent, "effort"):
        try:
            s.set_effort(str(body["effort"]))
        except ValueError:
            pass
    if pid:
        PS.touch(pid)
    with LOCK:
        LIVE[s.id] = s
    _claim(s.id)
    _start_reaper()
    if not scratch and not pid:
        try:
            from meshpool_harness.ui import workbench as W
            W.add_project(wt["source"] if wt else s.workdir)
        except Exception:  # noqa: BLE001
            pass
    return s.view(0)


def _chat_worktree(body: dict, wd: str, kind: str, scratch: bool, task: str) -> tuple:
    """(record, made here) of the isolated copy a new chat works in, or (None, False). ``worktree_id``: a copy made
    for it already (a scheduled run); ``worktree: true``: a Code chat asked for one ("Work in an isolated copy")."""
    from meshpool_harness import worktrees as WT
    if body.get("worktree_id"):
        return WT.get(str(body["worktree_id"])), False
    if body.get("worktree") and not scratch and wd:
        if kind != "code":
            raise ValueError("Isolated copies are for Code chats.")
        return WT.create(wd, owner_kind="chat", title=(task or "").strip()[:80]), True
    return None, False


def worktree_users(folder: str, exclude: str = "") -> list:
    """Open chats that work directly in ``folder`` (not in an isolated copy of it): a new chat there is offered one."""
    from meshpool_harness import worktrees as WT
    want = WT._norm(folder)
    with LOCK:
        live = list(LIVE.values())
    out = []
    for s in live:
        if s.id == exclude or getattr(s, "deleted", False) or not s.workdir:
            continue
        m = read_meta(s.id)
        if WT._norm(s.workdir) == want and not m.get("worktree"):
            out.append({"id": s.id, "title": m.get("title") or nice_title(s.agent.title or "New chat"),
                        "state": s.state})
    return out


def _known_models(prov: str) -> set | None:
    """Model ids (without the provider prefix) a provider is known to serve, from the lists already read; None when
    no list is known, so a model picker that is offline never blocks a name. Never a network call."""
    try:
        if prov == "chatgpt":                  # its list leaves out hidden models that still work: no check
            return None
        from meshpool_harness.ui import workbench as W
        return W.known_models()
    except Exception:  # noqa: BLE001 - a check that cannot run allows the name
        return None


def _unreachable(base: str, timeout: float = 3.0):
    """Why nothing answers at a model endpoint's address ("nothing answered at that address (connection refused)"),
    "" when a connection opens, None when a direct connection says nothing (a proxy is in the way, no address)."""
    import socket
    import urllib.request
    from urllib.parse import urlparse
    try:
        u = urlparse(str(base or ""))
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return None
    if not host or (urllib.request.getproxies().get(u.scheme or "http") and not urllib.request.proxy_bypass(host)):
        return None
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ""
    except ConnectionRefusedError:
        return "nothing answered at that address (connection refused)"
    except socket.gaierror:
        return "its name could not be found (DNS lookup failed)"
    except OSError as e:
        if getattr(e, "errno", None) in (101, 113, 10050, 10051, 10065):
            return "this computer has no network route to it"
        return ""                                    # a slow connection is not a dead one


def _short_harness(name: str) -> str:
    """'Meshpool Work' and 'Meshpool Code' read as Meshpool; Codex, Pi, OpenCode and Hermes keep their names."""
    return "meshpool" if str(name).lower().startswith("meshpool") else str(name)


_REAPER: list = []


def _reap_once(idle_s: float) -> list:
    """Close threads nobody has used for idle_s seconds: not working, not waiting for an answer, not viewed. Their
    MCP servers and harness processes end; opening the thread again resumes it from its saved state."""
    now, closed = time.time(), []
    with LOCK:
        idle = [(sid, s) for sid, s in LIVE.items()
                if s.state not in ("working", "waiting", "starting") and not s.pendings and not s.queue
                and now - getattr(s, "touched", now) > idle_s]
        for sid, _ in idle:
            LIVE.pop(sid, None)
    for sid, s in idle:
        try:
            s.close()
        except Exception:  # noqa: BLE001 - a helper that is already gone is fine
            pass
        closed.append(sid)
    return closed


def _start_reaper():
    """MH_IDLE_CLOSE_S (default 900; 0 = never): how long an unused thread keeps its helper processes."""
    idle_s = float(os.environ.get("MH_IDLE_CLOSE_S", "900") or 0)
    if _REAPER:
        return
    purge_due()                                    # chats deleted before the app last closed
    if idle_s <= 0:
        return

    def loop():
        while True:
            time.sleep(min(60.0, max(5.0, idle_s / 4)))
            purge_due()
            _reap_once(idle_s)
    th = threading.Thread(target=loop, daemon=True, name="meshpool-reaper")
    _REAPER.append(th)
    th.start()


class ChatBusy(ValueError):
    """The chat is open in another running Meshpool process (the terminal or the app): two would overwrite each other."""


def _live_file(sid: str) -> Path:
    return SESSIONS / sid / "live.json"


def _claim(sid: str, write: bool = True) -> None:
    """Record that this process has the chat open. Another live process holding it refuses (ChatBusy); a dead one's
    claim (a crash) is taken over."""
    f = _live_file(sid)
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = {}
    pid = rec.get("pid")
    if isinstance(pid, int) and pid != os.getpid() and HR._pid_alive(pid):
        where = "the terminal" if rec.get("frontend") == "terminal" else "the meshpool app"
        raise ChatBusy(f"This chat is open in {where} (process {pid}). Close it there, or fork it: "
                       f"meshpool chats fork {sid}")
    if not write or not f.parent.is_dir():
        return
    try:
        tmp = f.with_name("live.json.tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "frontend": os.environ.get("MESHPOOL_FRONTEND") or "app",
                                   "t": time.time()}), encoding="utf-8")
        C.replace_file(tmp, f)
    except OSError:
        pass


def _release(sid: str) -> None:
    if not sid:
        return
    f = _live_file(sid)
    try:
        if json.loads(f.read_text(encoding="utf-8")).get("pid") == os.getpid():
            f.unlink()
    except (OSError, ValueError):
        pass


def _stale(s: Session) -> bool:
    """An idle chat whose session.json another process has written since: its copy here is out of date."""
    if (getattr(s, "state", "") != "idle" or getattr(s, "pendings", None)
            or not hasattr(getattr(s, "agent", None), "messages") or not getattr(s, "id", "")):
        return False
    try:
        m = (SESSIONS / s.id / "session.json").stat().st_mtime
    except OSError:
        return False
    if m <= s._saved_mtime + 0.001:
        return False
    if not all(isinstance(x, dict) for x in s.agent.messages):
        return False                                    # Pi / ACP agents keep no Meshpool message list ([None])
    if len(saved_messages(s.id)) != len([x for x in s.agent.messages if x.get("role") != "system"]):
        return True
    s._saved_mtime = m                                  # same content: our own write
    return False


class ProjectMissing(ValueError):
    """The chat's project folder was moved or deleted: its history can be read but not continued."""


_OPENING: dict = {}                 # chat id -> lock: only one request builds the Session of a saved chat


def get(sid: str, no_mcp: bool = False) -> Session:
    if sid in DELETED or read_meta(sid).get("deleted"):
        raise KeyError(sid)
    with LOCK:
        s = LIVE.get(sid)
    if s and _stale(s):                          # another front end wrote this chat: show what it wrote
        with LOCK:
            if LIVE.get(sid) is s:
                del LIVE[sid]
        s.close()
        s = None
    if s:
        return s
    with LOCK:
        gate = _OPENING.setdefault(sid, threading.Lock())
    with gate:                                   # polls reopen the same chat at once: build one Session, not many
        with LOCK:
            s = LIVE.get(sid)
        if s:
            return s
        _claim(sid, write=False)                 # refuses while another running process has this chat open
        s = Session(resume_id=sid, **({"no_mcp": True} if no_mcp else {}))    # reopen a saved session
        _claim(sid)
        with LOCK:
            dead = sid in DELETED or bool(read_meta(sid).get("deleted"))   # deleted while it was being built
            live = s if dead else LIVE.setdefault(sid, s)
        if dead:
            try:
                s.deleted = True
                if getattr(s, "agent", None) is not None:
                    s.agent.deleted = True
            except Exception:  # noqa: BLE001
                pass
            s.close()
            raise KeyError(sid)
        if live is not s:
            s.close()
            _claim(sid)
        return live


def session_action(sid: str, act: str, b: dict) -> dict:
    """What the app's /api/agent/<id>/(send|stop|answer|mode|compact|grade|model|plan|goal|revert|route|harness|style|
    effort) routes do; the terminal's slash commands and `meshpool chats` call it too. Returns the chat's view."""
    s = get(sid)
    since = int(b.get("since", 10 ** 9))
    if act == "send":
        s._attached = []
        r = s.send(b.get("text", ""), images=b.get("images") or None, interrupt=bool(b.get("interrupt")),
                   files=b.get("files") or None)
        return dict(s.view(since), sent=r, attached=list(getattr(s, "_attached", None) or []))
    if act == "take_back":                          # a message sent while the agent worked, back to the box
        return dict(s.view(since), taken=s.take_back())
    if act == "retry":                              # Try again: stop, then the last message again, shown once
        return dict(s.view(since), sent=s.retry())
    if act == "stop":
        s.stop()
    elif act == "grade":
        s.grade()
    elif act == "answer":
        s.answer(str(b.get("id", "")), b.get("decision", "no"), b.get("note", ""))   # ap-<hex> ids (ws09)
    elif act == "compact":
        if s.state in ("working", "waiting"):
            raise ValueError("Wait for the task to finish or stop it, then compact.")
        r = s.agent.compact_now(str(b.get("focus") or "")) if hasattr(s.agent, "compact_now") else None
        return dict(s.view(since), compacted=r)
    elif act == "mode":
        s.set_mode(b.get("mode", "ask"))
    elif act == "model":
        s.set_model(b.get("model", ""), check=True)
    elif act == "harness":
        s.switch_harness(b.get("harness", ""))
    elif act == "route":
        s.set_route(b.get("spec", ""))
    elif act == "plan":
        s.set_plan(bool(b.get("on")))
    elif act == "goal":
        s.set_goal(b.get("text", ""))
    elif act == "style":
        s.set_style(b.get("style", ""))
    elif act == "effort":
        s.set_effort(b.get("effort", ""))
    elif act == "revert":
        turn, undone = int(b.get("turn", 0)), None
        if str(b.get("what") or "both") == "chat":   # the conversation only: the files stay as they are
            pos = s._turn_cut(turn)
            if pos is None:
                raise ValueError("That reply cannot be taken back without its files.")
            try:
                n = sum(1 for t in s.cp.turns() if int(t.get("n") or 0) >= turn) if s.cp else 1
            except Exception:  # noqa: BLE001
                n = 1
            s.rewind(pos, files=False)
            s.note_undo(undo_note({}, n, files=False))
        else:
            u = s.revert(turn)
            undone = {"deleted": list(u.get("deleted") or []), "restored": list(u.get("restored") or [])}
        if not b.get("keep_versions"):               # Undo or Edit: the earlier answers belonged to that message
            vs = {k: v for k, v in (read_meta(s.id).get("versions") or {}).items() if k.isdigit() and int(k) < turn}
            write_meta(s.id, versions=vs)
        if undone is not None:                         # the app closes or refreshes a preview of these files
            return dict(s.view(since), undone=undone)
    else:
        raise ValueError("Unknown chat action.")
    return s.view(since)


def view(sid: str, since: int) -> dict:
    """A chat for the page. A chat whose project folder is gone still shows its history, read-only."""
    try:
        return get(sid).view(since)
    except ProjectMissing as e:
        tr = _transcript(sid) or {}
        evs = tr.get("events") or events_from_messages(saved_messages(sid))
        evs = [_with_plain(dict(x, i=i)) for i, x in enumerate(evs)]
        evs.append({"type": "error", "i": len(evs), "text": f"This chat's project folder ({e}) was moved or deleted, "
                    "so it can't continue here. Its history is below; start a new chat to keep working."})
        meta = read_meta(sid)
        return {"id": sid, "workdir": str(e), "state": "idle", "readonly": True, "mode": "ask", "kind":
                meta.get("kind") or "code", "title": meta.get("title") or "Chat", "pinned": bool(meta.get("pinned")),
                "usage": {}, "harness": {}, "model": meta.get("model"), "plan": False, "goal": meta.get("goal", ""), "epoch": 0, "turns": [],
                "checkpoints_note": "", "events": evs[since:], "total": len(evs), "mcp": None}


def _project_of(meta: dict, workdir: str, legacy: dict | None = None) -> str:
    """The project a chat belongs to: the one stored with it (an empty string means it was taken out of its project);
    for chats made before projects existed, the project that was made from the folder they work in."""
    if "project_id" in meta:
        return meta.get("project_id") or ""
    if legacy is None:
        try:
            from meshpool_harness import project_store as PS
            legacy = PS.legacy_map()
        except Exception:  # noqa: BLE001
            legacy = {}
    return legacy.get(workdir or "", "")


def _listed_project(meta: dict, workdir: str, legacy: dict, kind: str | None = None) -> str:
    """The project a chat is listed under. A Code session whose folder is a project's linked folder is listed under
    that project even when it was started outside it (Code mode, a folder pick): Lina's data question in a project
    became a Code session that was no longer under the project. Listing only: the chat's own project, its
    instructions and memory, stay as stored. A chat taken out of its project (project_unbound) stays out."""
    pid = _project_of(meta, workdir, legacy)
    if pid or meta.get("project_unbound") or (meta.get("kind") or kind) != "code" or not workdir:
        return pid
    return (legacy or {}).get(workdir) or ""          # both paths are stored resolved: no disk access per row


def _list_project(r: dict, m: dict, legacy: dict, live_pids: set) -> None:
    """Set a listed row's project_id (where the sidebar shows it) and project_linked: whether the chat's own project
    is that one. A Code session listed under a project by its folder is not linked, so the menu does not tick it as
    if it were."""
    wd = r.get("workdir") or ""
    pid = _listed_project(m, wd, legacy, r.get("kind"))
    r["project_id"] = pid if pid in live_pids else ""
    r["project_linked"] = bool(r["project_id"]) and _project_of(m, wd, legacy) == r["project_id"]


_ROWS: dict = {}                    # session.json path -> (mtime, size, row): imported histories can be thousands


_LIST_CACHE: list = [0.0, None, ""]       # [time built, decorated rows of the chats saved on disk]
_LIST_TTL = 2.0                        # seconds; writes made here drop the cache at once (list_dirty)
_LIST_LOCK = threading.Lock()


def list_dirty() -> None:
    """A chat was saved, renamed, moved or deleted here: the next sidebar call rebuilds the saved-chat rows."""
    _LIST_CACHE[1] = None


def _decorate(r: dict, m: dict, legacy: dict, live_pids: set) -> dict:
    r["title"] = m.get("title") or r["title"]
    r["pinned"], r["archived"] = bool(m.get("pinned")), bool(m.get("archived"))
    r["unread"] = bool(m.get("unread")) and r.get("state") not in ("working", "waiting")
    r["scheduled"] = bool(m.get("scheduled"))
    r["pr"] = m.get("pr") or None                 # the pull request last seen for this session (the row's icon and card)
    _list_project(r, m, legacy, live_pids)
    r["_m"] = m                                   # kept for the cached rows: projects are re-resolved on every call
    r["scratch"] = bool(m.get("scratch"))
    r["code_mode"] = m.get("code_mode")           # False: a chat in a folder that was never started from the Code tab
    r["kind"] = m.get("kind") or r.get("kind") or "code"
    if m.get("worktree"):                         # a chat in an isolated copy is listed under the folder it came from
        r["worktree"] = m["worktree"].get("id")
        r["workdir"] = m["worktree"].get("source") or r.get("workdir")
    return r


def _project_info() -> tuple:
    try:
        from meshpool_harness import project_store as PS
        return PS.link_index()
    except Exception:  # noqa: BLE001
        return {}, set()


def _disk_rows() -> tuple:
    """Rows of the saved chats, built from session.json and meta.json at most every _LIST_TTL seconds (the page asks
    every few seconds, and every ask used to stat and decorate every chat)."""
    with _LIST_LOCK:
        t, hit = _LIST_CACHE[0], _LIST_CACHE[1]
        if hit is not None and time.monotonic() - t < _LIST_TTL and _LIST_CACHE[2:] == [str(SESSIONS)]:
            return hit
        rows = {}
        if SESSIONS.exists():
            for p in SESSIONS.glob("*/session.json"):
                if p.parent.name.startswith("sub-"):
                    continue
                try:
                    key = SS.stamp(p.parent)
                    hit_row = _ROWS.get(str(p))
                    if hit_row and hit_row[0] == key:
                        rows[hit_row[2]["id"]] = dict(hit_row[2])
                        continue
                    d = SS.read_head(p.parent) or SS.read(p.parent)     # the small head record: no messages loaded
                except OSError:
                    continue
                if not isinstance(d, dict) or not isinstance(d.get("id"), str) or not d["id"]:
                    continue
                row = {"id": d["id"], "title": nice_title(d.get("title") or "New chat"), "workdir": d.get("workdir"),
                       "updated": d.get("updated", 0), "state": "saved", "kind": d.get("kind") or "code",
                       "harness": HR.LEGACY.get(d.get("harness") or "", d.get("harness") or "meshpool-code"),
                       "model": d.get("model"),
                       "imported": (d.get("imported") or {}).get("source", "")}
                _ROWS[str(p)] = (key, None, row)
                rows[d["id"]] = dict(row)
        legacy, live_pids = _project_info()
        base = {}
        for sid, r in rows.items():
            m = read_meta(sid)
            if not m.get("deleted"):
                base[sid] = _decorate(r, m, legacy, live_pids)
        res = (base, legacy, live_pids)
        _LIST_CACHE[:] = [time.monotonic(), res, str(SESSIONS)]
        return res


def _own_turns(cp) -> list:
    """The checkpoint turns for the page: copies of unchanged files left out of "changed" (workbench.own_changes)."""
    if not cp:
        return []
    from meshpool_harness.ui import workbench as W
    return W.own_changes(cp.turns(), cp.root)


def listing() -> list:
    base, _, _ = _disk_rows()
    legacy, live_pids = _project_info()           # projects change by their own store, so never from the cache
    rows = {}
    for sid, r in base.items():
        if sid not in DELETED:
            r = dict(r)
            m = r.pop("_m")
            _list_project(r, m, legacy, live_pids)
            rows[sid] = r
    with LOCK:
        live = list(LIVE.items())
    for sid, s in live:
        if sid in DELETED:
            continue
        m = read_meta(sid)
        if m.get("deleted"):
            continue
        r = {"id": sid, "title": nice_title(s.agent.title or "New chat"), "workdir": s.workdir,
             "updated": time.time() if s.state != "idle" else rows.get(sid, {}).get("updated", time.time()),
             "state": s.state, "kind": s.kind, "harness": s.harness["id"]}
        rows[sid] = _decorate(r, m, legacy, live_pids)
        rows[sid].pop("_m", None)
    return sorted(rows.values(), key=lambda r: -r["updated"])        # P-07: no cap; the page asks for a window (limit, offset)


def listing_page(limit: int = 0, offset: int = 0) -> dict:
    """The sidebar list in windows: the newest ``limit`` chats after ``offset`` (0 = all), with the total and whether more follow."""
    rows = listing()
    if limit and limit > 0:
        window = rows[max(0, offset): max(0, offset) + limit]
        return {"sessions": window, "total": len(rows), "more": max(0, offset) + limit < len(rows)}
    return {"sessions": rows, "total": len(rows), "more": False}


def exists(sid: str) -> bool:
    with LOCK:
        if sid in LIVE:
            return True
    return bool(re.fullmatch(r"[\w-]+", sid or "")) and (SESSIONS / sid).is_dir()


_DEL_LOCK = threading.Lock()
_DEL_TIMERS: dict = {}


def _grace() -> float:
    try:
        return float(os.environ.get("MH_DELETE_GRACE_S", "8"))
    except ValueError:
        return 8.0


def _write_meta_raw(sid: str, m: dict) -> None:
    from meshpool_harness import fsio
    fsio.atomic_write_json(_meta_path(sid), m, indent=None, private=True)
    _METAS.pop(sid, None)


def _has_user_message(sid: str) -> bool:
    with LOCK:
        s = LIVE.get(sid)
    if s is not None:
        with s.lock:
            if any(e.get("type") == "user" for e in s.events):
                return True
    tr = _transcript(sid) or {}
    if any(e.get("type") == "user" for e in tr.get("events") or []):
        return True
    return any(m.get("role") == "user" for m in (_saved(sid) or {}).get("messages") or [] if isinstance(m, dict))


def _purge(sid: str, force: bool = False) -> None:
    """Really remove a deleted chat (stop it, its folder, checks records, private folder). Idempotent; a chat that was
    undeleted meanwhile is left alone."""
    import shutil
    with _DEL_LOCK:
        if sid in DELETED and not (SESSIONS / sid).exists():
            return
        m = read_meta(sid)
        if not force and not m.get("deleted"):
            return
        t = _DEL_TIMERS.pop(sid, None)
        if t and t is not threading.current_thread():
            t.cancel()
        try:
            from meshpool_harness import chat_import as CI
            CI.bury(sid)                                  # an imported chat the person deleted stays deleted (M21-02)
        except Exception:  # noqa: BLE001
            pass
        scratch_dir = ""
        if m.get("scratch"):
            scratch_dir = (_saved(sid) or {}).get("workdir") or (LIVE[sid].workdir if sid in LIVE else "")
        with LOCK:
            DELETED.add(sid)
            list_dirty()
            s = LIVE.pop(sid, None)
        import sys as _sys                                # the Office panel's token no longer reaches it
        _sv = _sys.modules.get("meshpool_harness.ui.server")
        if _sv is not None and hasattr(_sv, "OFFICE_CHATS"):
            _sv.OFFICE_CHATS.discard(sid)
    if s:
        s.deleted = True
        s.agent.deleted = True                        # its last save after stopping must not recreate the thread
        s.close()
        end = time.time() + 6
        while s.state in ("working", "waiting") and time.time() < end:
            time.sleep(0.1)
    d = SESSIONS / sid
    if d.is_dir() and d.parent == SESSIONS:
        shutil.rmtree(d, ignore_errors=True)
    att = C.runs_dir() / "_uploads" / f"chat-{sid}"   # the files dropped into it go with it
    if att.is_dir() and "/" not in sid and "\\" not in sid and sid not in ("", ".", ".."):
        shutil.rmtree(att, ignore_errors=True)
    list_dirty()
    try:                                              # the look's pictures of its pages go with it
        from meshpool_harness.agent import look as LK
        LK.forget(sid)
    except Exception:  # noqa: BLE001
        pass
    try:                                              # its checks records go with it
        from meshpool_harness import checks as CK
        for f in [CK._file(sid), *CK._dir().glob(f"{CK._safe(sid)}_*.jsonl")]:
            f.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    if scratch_dir:                                   # the chat's private folder goes with it
        from meshpool_harness import project_store as PS
        PS.remove_scratch(scratch_dir)
    try:                                              # and its isolated copy (unmerged work stays on its branch)
        from meshpool_harness import worktrees as WT
        WT.prune_owner("chat", sid)
    except Exception:  # noqa: BLE001
        pass


def purge_due() -> list:
    """Purge every chat whose delete grace has run out (also after a restart: the page need not be open)."""
    out, now, grace = [], time.time(), _grace()
    if not SESSIONS.exists():
        return out
    for p in list(SESSIONS.glob("*/meta.json")):
        sid = p.parent.name
        m = read_meta(sid)
        if m.get("deleted") and now - float(m.get("deleted_at") or 0) >= grace:
            try:
                _purge(sid)
                out.append(sid)
            except Exception:  # noqa: BLE001
                pass
    return out


def thread_action(sid: str, body: dict) -> dict:
    act = body.get("action")
    if not exists(sid):
        raise KeyError("That chat no longer exists. Refresh the list.")
    if act == "rename":
        write_meta(sid, title=(body.get("title") or "").strip()[:120], renamed=True)    # the user's own name is never replaced
    elif act in ("pin", "unpin"):
        write_meta(sid, pinned=act == "pin")
    elif act in ("archive", "unarchive"):
        write_meta(sid, archived=act == "archive")
    elif act == "keep_version":                      # Try again: the answer being replaced stays viewable
        turn, text = int(body.get("turn") or 0), str(body.get("text") or "")[:20000]
        if turn < 1 or not text.strip():
            raise ValueError("Give the reply's turn and text.")
        vs = {k: v for k, v in (read_meta(sid).get("versions") or {}).items() if k.isdigit() and int(k) < turn}
        vs[str(turn)] = ((read_meta(sid).get("versions") or {}).get(str(turn)) or [])[-3:] + [text]
        write_meta(sid, versions=vs)
    elif act in ("seen", "unseen"):                   # the blue dot: a finished chat nobody has opened yet
        write_meta(sid, unread=act == "unseen")
    elif act == "move":                               # into a project, or out of it (project_id empty)
        pid = (body.get("project_id") or "").strip()
        if pid:
            from meshpool_harness import project_store as PS
            if not PS.get_meta(pid):
                raise ValueError("That project does not exist any more.")
        if not read_meta(sid):
            raise KeyError("Chat not found.")
        write_meta(sid, project_id=pid, project_ctx="", project_unbound=not pid)
        try:                                          # the chat's own folder follows it to the new project (M20-01)
            from meshpool_harness import project_store as PS
            wd = (_saved(sid) or {}).get("workdir") or (LIVE[sid].workdir if sid in LIVE else "")
            PS.rebind_chat(wd, pid)
        except Exception:  # noqa: BLE001
            pass
    elif act == "delete":                             # soft: the server purges it after the grace, page or no page
        import sys as _sys                            # the Office panel's token stops reaching it at once
        _sv = _sys.modules.get("meshpool_harness.ui.server")
        if _sv is not None and hasattr(_sv, "OFFICE_CHATS"):
            _sv.OFFICE_CHATS.discard(sid)
        with _DEL_LOCK:
            if sid in DELETED:
                raise KeyError("That chat no longer exists. Refresh the list.")
            write_meta(sid, deleted=True, deleted_at=time.time())
            old = _DEL_TIMERS.pop(sid, None)
            if old:
                old.cancel()
            if _grace() > 0:
                t = threading.Timer(_grace(), _purge, args=(sid,))
                t.daemon = True
                _DEL_TIMERS[sid] = t
                t.start()
        list_dirty()
        if _grace() <= 0:                         # MH_DELETE_GRACE_S=0: no Undo window, gone at once
            _purge(sid)
    elif act == "undelete":
        with _DEL_LOCK:
            if sid in DELETED or not read_meta(sid).get("deleted"):
                raise ValueError("This chat was already deleted.")
            m = read_meta(sid)
            m.pop("deleted", None)
            m.pop("deleted_at", None)
            _write_meta_raw(sid, m)
            t = _DEL_TIMERS.pop(sid, None)
            if t:
                t.cancel()
        list_dirty()
    elif act == "fork":                               # a new chat from this one, up to a message (default: all of it)
        from meshpool_harness import chat_rewind as CR
        mi = body.get("message_index")
        return {"ok": True, "id": CR.fork(sid, None if mi is None else int(mi))}
    elif act == "discard":                            # a brand-new chat whose first message failed
        if _has_user_message(sid):
            raise ValueError("This chat already has messages.")
        _purge(sid, force=True)
    else:
        raise ValueError("Unknown chat action.")
    return {"ok": True}


_RAW: dict = {}                      # session.json path -> ((mtime, size), text, lowercase text) for search
_RAW_BYTES = [0]
_RAW_CAP = 48 * 1024 * 1024          # characters x 2 copies; beyond it a file is read each time


_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"(\*\*|__|~~|`+)")


def plain_markdown(text: str) -> str:
    """Markdown as plain reading text: no **, `, #, list dashes or link targets (a search snippet is not the rendered reply)."""
    t = _MD_LINK.sub(r"\1", str(text or ""))
    t = re.sub(r"(?m)^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+", "", t)
    t = re.sub(r"(?m)^\s*\|?[\s:|-]{3,}\|?\s*$", " ", t)
    t = _MD_MARKS.sub("", t)
    t = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]*?)\*(?![\w*])", r"\1", t)
    t = t.replace("|", " ")
    return " ".join(t.split())


def _plain_snippet(text: str, q: str, words: list) -> str:
    """About 160 characters of plain text around the first match, starting and ending at a whole word."""
    plain = plain_markdown(text)
    lo = plain.lower()
    j = lo.find(q) if q in lo else min((lo.find(w) for w in words if w in lo), default=0)
    a, b = max(0, j - 60), min(len(plain), j + 100)
    if a > 0 and plain[a - 1] != " ":
        k = plain.find(" ", a)
        a = a if k < 0 or k >= j else k + 1
    if b < len(plain) and plain[b] != " ":
        k = plain.rfind(" ", j, b)
        b = k if k > j else b
    return ("…" if a > 0 else "") + plain[a:b].strip() + ("…" if b < len(plain) else "")


def search(q: str) -> list:
    """Chats that hold every word of q, in any order and anywhere in the chat ("dropped rows" finds "rows were
    dropped"), newest first, with a snippet around the first place a word appears."""
    q = q.strip().lower()
    words = q.split()
    if not words:
        return []
    hits = []
    quick = all(32 <= ord(c) < 127 for c in q) and not any(c in q for c in '"\\')     # plain ASCII text is never escaped in the saved JSON
    for p in SESSIONS.glob("*/session.json") if SESSIONS.exists() else []:
        if p.parent.name.startswith("sub-"):             # a sub-agent's session, not a chat
            continue
        try:
            key = SS.stamp(p.parent)
            hit = _RAW.get(str(p))
            if hit and hit[0] == key:
                raw, low = hit[1], hit[2]
            else:
                if quick and not (p.parent / "session.journal").exists():
                    # P-07: a chat that cannot contain the words is skipped from its raw text (no parse, no re-dump)
                    try:
                        low0 = p.read_text(encoding="utf-8", errors="replace").lower() + "\n" + \
                            str(read_meta(p.parent.name).get("title") or "").lower()
                        if not all(w in low0 for w in words):
                            continue
                    except OSError:
                        continue
                sd = SS.read(p.parent)
                if not isinstance(sd, dict):
                    continue
                raw = json.dumps(sd, ensure_ascii=False, default=str)
                low = raw.lower()
                if _RAW_BYTES[0] + 2 * len(raw) <= _RAW_CAP:      # keep the text of recent searches in memory, up to a cap
                    old_hit = _RAW.get(str(p))
                    _RAW_BYTES[0] += 2 * len(raw) - (2 * len(old_hit[1]) if old_hit else 0)
                    _RAW[str(p)] = (key, raw, low)
        except OSError:
            continue
        named = str(read_meta(p.parent.name).get("title") or "").lower()
        if not all(w in low or w in named for w in words):
            continue                                     # the name the person gave the chat counts too (F-07)
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or not d["id"]:
            continue
        if read_meta(d["id"]).get("deleted") or any(h["id"] == d["id"] for h in hits):
            continue
        title = read_meta(d["id"]).get("title") or nice_title(d.get("title") or "New chat")
        # Search what the user reads (titles, messages, replies), not the saved JSON around it.
        texts = [title]
        for m in d.get("messages") or []:
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            if isinstance(c, str) and m.get("role") in ("user", "assistant"):
                texts.append(c.split("\n\n\u200b")[0])
        for e in d.get("events") or []:
            if isinstance(e, dict) and e.get("type") in ("user", "assistant") and isinstance(e.get("text"), str):
                texts.append(e["text"])
        shown = "\n".join(texts).lower()
        if not all(w in shown for w in words):
            continue
        body = texts[1:] or texts                         # the words in the messages, not just in the title, make the snippet
        hit = next((s for s in body if q in s.lower()), None) or \
            max(body, key=lambda s: sum(w in s.lower() for w in words))
        snippet = _plain_snippet(hit, q, words)
        hits.append({"id": d["id"], "title": title, "workdir": d.get("workdir"), "updated": d.get("updated", 0),
                     "snippet": snippet})
    return sorted(hits, key=lambda r: -r["updated"])[:50]


def mcp_info() -> dict:
    path = M.config_file()
    text = path.read_text(encoding="utf-8") if path.exists() else '{\n  "mcpServers": {}\n}\n'
    try:
        cfg = M.load_config(path)
        servers = [{"name": n, "enabled": not s.get("disabled"), "description": s.get("description", ""),
                    "kind": "http" if s.get("url") else "stdio",
                    "target": s.get("url") or " ".join([s.get("command", "")] + [str(a) for a in s.get("args", [])])}
                   for n, s in cfg.items() if isinstance(s, dict)]
        err = ""
    except ValueError as e:
        servers, err = [], f"mcp.json is not valid JSON: {e}"
    return {"path": str(path), "text": text, "servers": servers, "error": err}


def _test_server(name: str, spec: dict) -> dict | None:
    """Start one server on its own and report how it went (nothing else starts)."""
    import tempfile
    one = {k: v for k, v in spec.items() if k not in ("disabled", "needs_test")}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mcp.json"
        p.write_text(json.dumps({"mcpServers": {name: one}}), encoding="utf-8")
        m = M.Manager(roots=[str(Path.home())], path=p).start(timeout=40, use_health=False)
        try:
            return next((r for r in m.status() if r["name"] == name), None)
        finally:
            m.close()


def mcp_save(body: dict) -> dict:
    path = M.config_file()
    if "text" in body:
        try:
            json.loads(body["text"])
        except ValueError as e:
            raise ValueError(f"Not valid JSON: {e}")
        M.write_text_atomic(path, body["text"].rstrip() + "\n")
    elif "name" in body:
        if body.get("enabled"):                       # an imported server is switched on only once it starts (F-17)
            cur = M.load_config(path).get(body["name"])
            if isinstance(cur, dict) and cur.get("needs_test"):
                row = _test_server(body["name"], cur)
                if not row or row.get("status") != "connected":
                    why = (row or {}).get("error") or "it did not start"
                    raise ValueError(f"{body['name']} did not start ({str(why)[:200]}). It stays off. Check its "
                                     "command and keys, then try again.")

        def toggle(servers):
            srv = servers.get(body["name"])
            if not isinstance(srv, dict):
                raise ValueError(f"No server named {body['name']}")
            if body.get("enabled"):
                srv.pop("disabled", None)
                srv.pop("needs_test", None)
            else:
                srv["disabled"] = True
        if not path.exists():
            raise ValueError(f"No server named {body['name']}")
        M.edit_config(toggle, create=False)
    return mcp_info()


def mcp_test(body: dict) -> dict:
    C.load_into_env(override=True)
    wd = (body.get("workdir") or "").strip() or str(Path.home())
    m = M.Manager(roots=[wd if Path(wd).is_dir() else str(Path.home())]).start(timeout=90, use_health=False)
    try:
        return {"status": m.status(), "error": m.config_error}
    finally:
        m.close()


def close_all():
    try:                                          # M26-01: first, and whatever a chat's close() does: no command outlives the app
        from meshpool_harness.ui import workbench as W
        W.term_stop_all()
    except Exception:  # noqa: BLE001
        pass
    try:
        from engines.forge.common import kill_all_soffice      # P-04: nor a LibreOffice preview run
        kill_all_soffice()
    except Exception:  # noqa: BLE001
        pass
    with LOCK:
        for s in list(LIVE.values()):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


def _notify(title: str, body: str):
    if os.environ.get("MESHPOOL_FRONTEND") == "terminal":     # the terminal shows its own prompt
        return
    try:
        from meshpool_harness.ui.desktop import notify
        notify(title, body)
    except Exception:  # noqa: BLE001
        pass


def run_scheduled(t: dict) -> str:
    """Start a new thread for a scheduled task and send its prompt. Returns the thread id."""
    from meshpool_harness import assistant as AS
    token = t.get("claim")                       # from due(); Run now takes its own
    if not token:
        try:
            token = AS.claim(t["id"])
        except KeyError:                         # the task was deleted just now: this run still goes ahead
            token = ""
        else:
            if not token:
                raise ValueError("This task is still running. Wait for it to finish, or stop its chat first.")
    try:
        folder = t.get("folder") or ""
        if folder and not Path(folder).expanduser().is_dir():
            raise ValueError(f"The folder for this task ({folder}) was moved or deleted. Edit the task and pick it again.")
        rf = AS.run_folder(t)                   # a repository: the run gets its own isolated copy
        try:
            v = create({"workdir": rf["workdir"], "harness": t.get("harness") or None, "kind": t.get("kind") or "cowork",
                        "task": t.get("prompt", ""), "plan": False,      # nobody is there to approve a plan
                        "worktree_id": rf.get("worktree_id") or None})
        except BaseException:
            if rf.get("worktree_id"):
                from meshpool_harness import worktrees as WT
                WT.discard(rf["worktree_id"])
            raise
        s = get(v["id"])
        s.scheduled = dict(t, claim=token)
        s._lease = (t["id"], token) if token else None
        late = ""
        if t.get("late"):
            h = float(t.get("late_s") or 0) / 3600
            late = (f"This run was due {h:.0f} hours ago: meshpool was closed or the computer was off. "
                    "It ran as soon as it could.")
            s.emit({"type": "status", "text": late})
        write_meta(s.id, title=f"{t.get('name') or 'Scheduled task'} ({time.strftime('%d %b %H:%M')})"
                   + (" (late)" if late else ""), scheduled=t["id"])
        s._system_send = True                    # the run's own prompt is not the person taking over
        s.send(t["prompt"] + (f"\n\n\u200b({late} " if late else "\n\n\u200b(") + "This is a scheduled run. The user is not "
               "watching: make reasonable choices, say which ones you made, and finish with a short summary.)")
        s._system_send = False
        AS.set_thread(t["id"], s.id)
        return s.id
    except BaseException as e:
        AS.run_failed(t["id"], token, str(e))
        raise


def recover_worktrees():
    """At app start: remove isolated copies whose creation never finished, whose chat was deleted, or whose
    sub-agent's process is gone. Copies of chats that still exist are kept."""
    try:
        from meshpool_harness import worktrees as WT

        def alive(kind, oid, rec):
            if kind == "chat":                      # no id yet: a chat being made right now
                return exists(oid) if oid else time.time() - float(rec.get("created") or 0) < 600
            return WT._default_alive(kind, oid, rec)
        return WT.recover(alive)
    except Exception:  # noqa: BLE001
        return None


def probe_mcp():
    """At app start, start every enabled MCP server once and remember which fail, so threads skip broken ones."""
    try:
        time.sleep(3)
        C.load_into_env(override=True)
        M.Manager(roots=[str(Path.home())]).start(timeout=45, use_health=False).close()
    except Exception:  # noqa: BLE001
        pass


def prune_old_chats(days: float | None = None, now: float | None = None) -> list[str]:
    """Settings, Keep chats for: deletes chats not touched for that many days (the same way Delete does), except
    pinned ones and chats that are running. Returns the ids deleted. 0 or unset: nothing is deleted."""
    if days is None:
        try:
            days = float(os.environ.get("MH_KEEP_DAYS") or C.read().get("MH_KEEP_DAYS") or 0)
        except ValueError:
            days = 0
    days = int(days or 0)
    if days < 1 or not SESSIONS.is_dir():
        return []
    cutoff = (now or time.time()) - days * 86400
    gone = []
    for d in list(SESSIONS.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        sid = d.name
        if sid.startswith("sub-") or sid in LIVE:      # a sub-agent's own record, or a chat that is open now
            continue
        meta = read_meta(sid)
        if meta.get("pinned"):
            continue
        try:
            last = max(f.stat().st_mtime for f in d.iterdir()) if any(d.iterdir()) else d.stat().st_mtime
        except OSError:
            continue
        if last < cutoff:
            try:
                _purge(sid, force=True)                  # no undo window: the user asked for chats this old to go
                gone.append(sid)
            except Exception:  # noqa: BLE001
                continue
    try:                                              # and the look's pictures of chats that are gone, as old
        from meshpool_harness.agent import look as LK
        LK.prune(cutoff, keep=[d.name for d in SESSIONS.iterdir() if d.is_dir()])
    except Exception:  # noqa: BLE001
        pass
    return gone


def scheduler_loop():
    from meshpool_harness import assistant as AS
    last_prune = 0.0
    while True:
        if time.time() - last_prune > 86400:          # Keep chats for N days: at start, then once a day
            last_prune = time.time()
            try:
                prune_old_chats()
            except Exception:  # noqa: BLE001
                pass
        try:
            for t in ([] if QUIESCING[0] else AS.due()):      # due tasks stay due and run after the restart
                try:
                    run_scheduled(t)
                except Exception as e:  # noqa: BLE001           # run_scheduled kept the reason on the task
                    _notify(f"Scheduled task could not start: {t.get('name')}", str(e)[:200])
        except Exception:  # noqa: BLE001
            pass
        time.sleep(20)


def _saved(sid: str) -> dict | None:
    try:
        d = SS.read(SESSIONS / sid)
        if d and isinstance(d.get("messages"), list):
            d["messages"] = repair_tool_pairs(d["messages"])[0]       # a saved chat never loads with an orphan
        return d
    except OSError:
        return None


def saved_messages(sid: str) -> list:
    """Messages of a saved chat. session.json is written without the system prompt, so skip index 0 only when it is one."""
    msgs = (_saved(sid) or {}).get("messages")
    msgs = msgs if isinstance(msgs, list) else []
    if msgs and isinstance(msgs[0], dict) and msgs[0].get("role") == "system":
        return msgs[1:]
    return msgs


def harness_info() -> dict:
    HR.ensure_defaults()
    secret = ("env", "auth", "env_unset")         # launch details: they can hold API keys, and the page needs none
    return {"harnesses": [{k: v for k, v in h.items() if k not in secret} for h in HR.load_all()],
            "examples": HR.examples(), "folder": str(HR.folder()),
            "defaults": {m: HR.default_for(m) for m in HR.MODES}}


def harness_change(body: dict) -> dict:
    if body.get("text"):
        HR.add(body["text"])
    elif body.get("example"):
        HR.add_example(body["example"])
    elif body.get("remove"):
        HR.remove(body["remove"])
    elif body.get("default_mode") in HR.MODES:
        hid = body.get("id") or ""
        HR.get(hid)                                  # raises KeyError for an unknown harness
        C.write({f"MH_HARNESS_{body['default_mode'].upper()}": hid})
        C.load_into_env(override=True)
    return harness_info()
