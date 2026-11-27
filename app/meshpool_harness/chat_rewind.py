"""Rewind a chat to just before one of its user messages, or fork it into a new chat. Works on a chat saved on disk
(the CLI, a chat nobody has open) and on a live ``Session`` (pass ``session=``; the app does).

A chat is addressed by ``message_index``: the position of a user message in the chat's conversation. For Meshpool's own
agent this is the index in the agent's message list, where index 0 is the system prompt (the same number the
checkpoints store as a turn's ``message_index``). For a chat run by an outside agent (Codex, Pi, ...) it is the position
in the conversation rebuilt from its events (``agent_api._messages_from_events``). ``points(sid)`` lists them.

* ``points(sid)``: the user messages: ``[{message_index, event_index, text, turn, steer}]``.
* ``rewind(sid, message_index, files=True)``: the conversation goes back to just before that message and the page
  shows it that way; ``text`` is what the user had typed there, ready to edit and send again. With ``files`` the file
  changes the agent made from that message on are undone too (``Checkpoints.revert_to_before``; what Undo replaced is
  kept, see its ``backup``). Refused while the chat is working. A chat run by an outside agent restarts that agent's
  session with the kept conversation sent as context; when that is not possible the rewind becomes a fork at that
  point and the result says ``{"forked": <new id>}``.
* ``fork(sid, message_index=None)``: a new chat holding the conversation up to the end of that message's exchange
  (everything when ``None``), in the same folder, with "Fork of <title>" as its title. No files change and no
  checkpoints are copied. A chat of an outside agent forks into Meshpool's own agent, with the conversation.
"""
from __future__ import annotations

import copy
import time
import uuid
from pathlib import Path

from meshpool_harness import session_store as SS

STEER = "[Added by the user while you were working]"
SUMMARY = "[Earlier steps of this task were summarized"
_HIDDEN = "\n\n​"
_META_KEEP = ("kind", "mode", "model", "style", "project_id", "plan", "route", "route_label", "route_profile", "routed",
              "incognito", "memory_exclude")


def _api():
    from meshpool_harness.ui import agent_api as A      # late: the module is also used by the CLI
    return A


def _text(content) -> str:
    if isinstance(content, list):
        content = " ".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return str(content or "").split(_HIDDEN)[0]


def _is_steer(m: dict) -> bool:
    c = m.get("content")
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return str(c or "").startswith(STEER)


def _is_summary(m: dict) -> bool:
    """The note compaction leaves in place of older steps: a user-role message the user never wrote."""
    return str(m.get("content") if not isinstance(m.get("content"), list) else "").startswith(SUMMARY)


def _same(a: str, b: str) -> bool:
    """The text an event shows and the text of a message are the same message (the message may carry a note before it)."""
    a, b = str(a or "").strip(), str(b or "").strip()
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def _first_line(text) -> str:
    t = str(text or "").strip().splitlines()
    return (t[0] if t else "Images").strip()[:120]


def turn_event_pos(events: list, turn: dict):
    """Position of the user event a checkpoint turn began at, or None. Meshpool's agent stamps user events with their
    message number (``mi``), but compaction can renumber the messages, so every candidate must also carry the turn's
    title. The last resort is the turn's own checkpoint event: the user event just before it."""
    title = str(turn.get("title") or "").strip()
    mi, ei = turn.get("message_index"), turn.get("event_index")
    cands = [p for p, e in enumerate(events) if e.get("type") == "user" and mi is not None and e.get("mi") == mi]
    if isinstance(ei, int) and 0 <= ei < len(events) and events[ei].get("type") == "user":
        cands.append(ei)
    for p in cands:
        if _first_line(events[p].get("text")) == title:
            return p
    for p, e in enumerate(events):
        if e.get("type") == "checkpoint" and any(t.get("n") == turn.get("n") for t in e.get("turns") or []):
            for q in range(p - 1, -1, -1):
                if events[q].get("type") == "user" and _first_line(events[q].get("text")) == title:
                    return q
            break
    return None


def _outside_type(harness_id: str) -> bool:
    try:
        from meshpool_harness import harnesses as HR
        return HR.get(harness_id)["type"] in ("acp", "pi")
    except Exception:  # noqa: BLE001
        return False


class _State:
    """What a rewind or fork reads: the chat's conversation, transcript and checkpoints, live or from disk."""

    def __init__(self, sid: str, session=None):
        A = _api()
        self.sid, self.live = sid, session
        self.dir = A.SESSIONS / sid
        self.data = None
        if session is not None:
            self.workdir, self.harness = session.workdir, session.harness["id"]
            self.outside = session.harness["type"] in ("acp", "pi")
            self.kind = session.kind
            with session.lock:
                self.events = [{k: v for k, v in e.items() if k != "i"} for e in session.events]
            self.messages = None if self.outside else list(session.agent.messages)
            self.cp = session.cp
            # the chat's name as the sidebar shows it: a rename or the work's name, else the cleaned first message
            # (the agent keeps the first line as typed, up to 240 characters, and a fork was named after it)
            self.title = A.read_meta(sid).get("title") or A.nice_title(session.agent.title or "")
            return
        data = A._saved(sid)
        if not data:
            raise KeyError("That chat no longer exists.")
        self.data = data
        self.workdir = data.get("workdir") or ""
        self.harness = data.get("harness") or "meshpool-code"
        self.outside = "messages" not in data and _outside_type(self.harness)
        self.kind = data.get("kind") or A.read_meta(sid).get("kind") or "code"
        self.title = A.read_meta(sid).get("title") or A.nice_title(data.get("title") or "")
        tr = A._transcript(sid)
        if self.outside:
            self.messages = None
            self.events = list((tr or {}).get("events") or data.get("events") or [])
        else:
            msgs = list(data.get("messages") or [])
            if not msgs or not isinstance(msgs[0], dict) or msgs[0].get("role") != "system":
                msgs.insert(0, {"role": "system", "content": ""})     # saved without it: keep message_index absolute
            self.messages = msgs
            evs = (tr or {}).get("events")
            self.events = list(evs) if evs else A.events_from_messages(msgs[1:])
        from meshpool_harness.checkpoints import Checkpoints
        self.cp = Checkpoints(self.workdir, self.dir / "checkpoints", mode="scan" if self.outside else "journal",
                              seq_floor=int(A.read_meta(sid).get("turn_seq") or 0)) if self.workdir else None

    # ---- the user messages -------------------------------------------------------------------------------------
    def user_messages(self) -> list:
        """[(message_index, text, steer)] for the conversation."""
        if self.outside:
            A = _api()
            out, n = [], 0
            for e in self.events:
                if e.get("type") in ("user", "assistant") and str(e.get("text") or "").strip():
                    if e["type"] == "user":
                        out.append((n, str(e["text"]), bool(e.get("steer"))))
                    n += 1
            return out
        out = []
        for i, m in enumerate(self.messages):
            if not i or m.get("role") != "user" or _is_summary(m):
                continue
            t = _text(m.get("content"))
            prev = self.messages[i - 1]
            if prev.get("role") == "user" and out and out[-1][0] == i - 1 and out[-1][1] == t:
                continue              # compaction puts the current task back after its summary: one message, not two
            out.append((i, t, _is_steer(m)))
        return out

    def event_pos(self, message_index: int):
        """Position in the transcript of the user event that shows this message, or None."""
        if self.outside:
            n = 0
            for p, e in enumerate(self.events):
                if e.get("type") in ("user", "assistant") and str(e.get("text") or "").strip():
                    if n == message_index and e["type"] == "user":
                        return p
                    n += 1
            return None
        text = next((t for i, t, _ in self.user_messages() if i == message_index), "")
        for p, e in enumerate(self.events):
            if e.get("type") == "user" and e.get("mi") == message_index and _same(e.get("text"), text):
                return p
        # no usable "mi" (an older transcript, or compaction renumbered the messages): the k-th message with this text
        # the user sent is the k-th user event with it
        same = [i for i, t, steer in self.user_messages() if not steer and _same(t, text)]
        evs = [p for p, e in enumerate(self.events) if e.get("type") == "user" and not e.get("steer")
               and _same(e.get("text"), text)]
        if message_index in same and len(same) == len(evs):
            return evs[same.index(message_index)]
        users = [i for i, _, steer in self.user_messages() if not steer]
        allev = [p for p, e in enumerate(self.events) if e.get("type") == "user" and not e.get("steer")]
        if message_index in users and len(users) == len(allev):
            return allev[users.index(message_index)]
        return None

    def turns(self) -> list:
        try:
            return self.cp.turns() if self.cp else []
        except Exception:  # noqa: BLE001
            return []

    def turn_from(self, message_index: int, pos):
        """The first checkpoint turn that began at or after this message: the first checkpoint event after the message's
        own event names it (message numbers can be renumbered by compaction); old chats fall back to the turn records."""
        turns = self.turns()
        if pos is not None:
            for e in self.events[pos:]:
                if e.get("type") == "checkpoint" and e.get("turns"):
                    n = e["turns"][0].get("n")
                    t = next((x for x in turns if x.get("n") == n), None)
                    if t:
                        return t
            if any(e.get("type") == "checkpoint" for e in self.events):
                return None                       # this chat records its turns in the transcript: none came after it
        for t in turns:
            mi = t.get("message_index")
            if mi is not None and not self.outside:
                if mi >= message_index:
                    return t
            elif t.get("event_index") is not None and pos is not None and t["event_index"] >= pos:
                return t
        return None


def _renumber(events: list) -> list:
    return [dict({k: v for k, v in e.items() if k != "i"}, i=i) for i, e in enumerate(events)]


def _live(sid: str):
    try:
        return _api().LIVE.get(sid)
    except Exception:  # noqa: BLE001
        return None


# ---------- public ---------------------------------------------------------------------------------------------------
def points(sid: str, session=None) -> list:
    session = session or _live(sid)
    st = _State(sid, session)
    turns = st.turns()
    out = []
    for mi, text, steer in st.user_messages():
        pos = st.event_pos(mi)
        t = next((x for x in turns if ((pos is not None and x.get("event_index") == pos) if st.outside
                                       else x.get("message_index") == mi)), None)
        out.append({"message_index": mi, "event_index": pos, "text": text, "turn": t["n"] if t else None,
                    "steer": steer})
    return out


def message_for_event(session, event_index: int) -> int:
    """The message_index of the user message a live session's event shows."""
    st = _State(session.id, session)
    with session.lock:
        evs = list(session.events)
    if not (0 <= event_index < len(evs)) or evs[event_index].get("type") != "user" or evs[event_index].get("steer"):
        raise ValueError("Pick one of your own messages.")
    ev = evs[event_index]
    users = st.user_messages()
    if not st.outside:
        by = dict((i, t) for i, t, _ in users)
        if ev.get("mi") in by and _same(ev.get("text"), by[ev["mi"]]):
            return int(ev["mi"])
        # compaction renumbers messages: find it by what was said (the k-th time the user said it)
        k = sum(1 for e in evs[:event_index] if e.get("type") == "user" and not e.get("steer")
                and _same(e.get("text"), ev.get("text")))
        same = [i for i, t, steer in users if not steer and _same(ev.get("text"), t)]
        if k < len(same):
            return same[k]
        raise ValueError("That message was summarized away to save space, so Meshpool cannot go back to it. "
                         "Pick a later message.")
    # an outside agent: the number of conversation entries before the event
    n = 0
    for e in evs[:event_index]:
        if e.get("type") in ("user", "assistant") and str(e.get("text") or "").strip():
            n += 1
    return n


def rewind(sid: str, message_index: int, files: bool = True, session=None, held: bool = False) -> dict:
    """See the module notes. On a live session the rewind holds the session's turn while it runs (``held``: the caller
    already holds it), so no turn starts meanwhile and a message sent meanwhile is queued and runs after it."""
    session = session or _live(sid)
    if session is None or held:
        return _rewind(sid, message_index, files, session)
    if not hasattr(session, "_hold_turn"):              # a stand-in session (tests): the plain state check
        if session.state in ("working", "waiting"):
            raise ValueError("Stop the agent before rewinding.")
        return _rewind(sid, message_index, files, session)
    token = session._hold_turn("Stop the agent before rewinding.")
    try:
        return _rewind(sid, message_index, files, session)
    finally:
        session._end_turn(token, notice=False)


def _rewind(sid: str, message_index: int, files: bool, session) -> dict:
    st = _State(sid, session)
    mi = int(message_index)
    found = next(((i, t, s) for i, t, s in st.user_messages() if i == mi), None)
    if not found:
        raise ValueError("That message is not in this chat.")
    text = found[1]
    pos = st.event_pos(mi)
    undo = None
    turn = st.turn_from(mi, pos)
    if files and turn and st.cp:
        undo = st.cp.revert_to_before(turn["n"])            # may refuse (too old): then nothing else changes
    if st.outside and not _restart_outside(st, pos, session):
        new = _fork_state(st, mi, before=True)
        return {"forked": new, "text": text, "undo": undo}
    _cut(st, mi, pos, session)
    if session is None:
        _api().write_meta(sid, unread=False)
    return {"text": text, "undo": undo}


def fork(sid: str, message_index=None, session=None) -> str:
    session = session or _live(sid)
    if session is not None and message_index is None and session.state in ("working", "waiting"):
        raise ValueError("Wait for the reply to finish, or fork from an earlier message.")
    st = _State(sid, session)
    return _fork_state(st, None if message_index is None else int(message_index))


# ---------- rewind: the conversation --------------------------------------------------------------------------------
def _cut(st: _State, mi: int, pos, session) -> None:
    A = _api()
    kept = st.events[:pos] if pos is not None else (
        A.events_from_messages(st.messages[1:mi]) if st.messages is not None else st.events)
    if session is not None:
        with session.lock:
            if st.outside:
                _cut_agent_events(session.agent, st.events, pos)
            else:
                session.agent.messages = st.messages[:mi]
                session.agent.unfinished = False
                session.agent.save(full=True)
            session.events = _renumber(kept)
            session._open_deltas = []
            session.epoch += 1
        if st.outside:
            session.agent.save()
        session._persist(running=False)
        return
    d = dict(st.data)
    d["updated"] = time.time()
    if st.outside:
        d["events"] = kept
        d.pop("acp_session", None)                       # the agent's own session still holds what was cut
        pi = st.dir / "pi"
        if pi.is_dir():
            for f in pi.glob("*.jsonl"):
                f.unlink(missing_ok=True)
    else:
        d["messages"] = [m for m in st.messages[1:mi]]
        d["unfinished"] = False
    SS.write_snapshot(st.dir, d)
    SS.TranscriptLog(st.dir).rewrite([{k: v for k, v in e.items() if k != "i"} for e in kept], False)
    A.list_dirty()


def _cut_agent_events(agent, events: list, pos) -> None:
    """Cut an outside agent's own event list at the same user event (the k-th user event in both lists)."""
    if pos is None:
        return
    k = sum(1 for e in events[:pos] if e.get("type") == "user")
    seen = 0
    for p, e in enumerate(getattr(agent, "events", [])):
        if e.get("type") == "user":
            if seen == k:
                agent.events = agent.events[:p]
                return
            seen += 1


def _restart_outside(st: _State, pos, session) -> bool:
    """Make an outside agent start a new session with the kept conversation as context. False when it cannot."""
    if pos is None:
        return False
    if session is None:
        return True                       # saved chat: _cut drops its session id, the agent carries the history on resume
    a = session.agent
    try:
        if hasattr(a, "forget_session"):                 # the harness's own way to drop cut turns (keeps Pi's old files)
            a.forget_session()
            return True
        if hasattr(a, "acp_session"):                    # ACP: a new session; the next message carries the history
            a.acp_session = None
            a.loaded_from = None
        elif hasattr(a, "_carry") and hasattr(a, "close"):     # Pi: forget its own session files, restart the process
            pi = Path(getattr(a, "dir", st.dir)) / "pi"
            if pi.is_dir():
                for f in pi.glob("*.jsonl"):
                    f.unlink(missing_ok=True)
            a.close()
        else:
            return False
        a._carry = True
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------- fork ------------------------------------------------------------------------------------------------------
def _fork_state(st: _State, message_index, before: bool = False) -> str:
    A = _api()
    users = st.user_messages()
    if message_index is None:
        end_msg, end_pos = None, None
    else:
        if not any(i == message_index for i, _, _ in users):
            raise ValueError("That message is not in this chat.")
        nxt = next((i for i, _, steer in users if i > message_index and not steer), None) if not before else message_index
        end_msg = nxt
        end_pos = st.event_pos(nxt) if nxt is not None else None
    if st.outside:
        from meshpool_harness.ui.agent_api import _messages_from_events
        evs = st.events if end_msg is None else (st.events[:end_pos] if end_pos is not None else st.events)
        msgs = _messages_from_events(evs)
        harness = "meshpool-work" if st.kind == "cowork" else "meshpool-code"
        kind = st.kind
    else:
        msgs = [copy.deepcopy(m) for m in (st.messages if end_msg is None else st.messages[:end_msg])][1:]
        harness = "meshpool-work" if st.harness == "meshpool-work" else "meshpool-code"
        kind = st.kind
    sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = A.SESSIONS / sid
    d.mkdir(parents=True, exist_ok=True)
    meta_old = A.read_meta(st.sid)
    title = "Fork of " + (st.title if st.title and st.title != "New chat" else "chat")
    now = time.time()
    snap = {"id": sid, "workdir": st.workdir, "title": title, "created": now, "updated": now, "harness": harness,
            "kind": kind, "usage": {"in": 0, "cached": 0, "out": 0, "calls": 0}, "unfinished": False, "messages": msgs}
    if not st.outside and st.data:
        for k in ("model", "model_choice", "plan", "route", "route_text"):
            if st.data.get(k) is not None:
                snap[k] = st.data[k]
    elif not st.outside and st.live is not None:
        a = st.live.agent
        snap.update({"model": a.current_model(), "model_choice": a.model, "plan": a.plan})
    SS.write_snapshot(d, snap)
    SS.TranscriptLog(d).rewrite([{k: v for k, v in e.items() if k != "i"} for e in A.events_from_messages(msgs)], False)
    keep = {k: meta_old[k] for k in _META_KEEP if meta_old.get(k) not in (None, "") and
            not (st.outside and k in ("model", "route", "routed"))}
    A.write_meta(sid, title=title, scratch=False, forked_from={"sid": st.sid, "message_index": message_index},
                 **keep)
    A.list_dirty()
    return sid
