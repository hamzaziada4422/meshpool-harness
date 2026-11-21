"""The learning loop, for every harness: after each turn, Meshpool notices what the user taught it about how they
want work done ("never put a label over the title", "default to Segoe UI", "be concise") and keeps it in their
memory, so every agent and every engine follows it from the next turn on, in this chat and in new ones.

The idea follows Hermes Agent's loop (memory, a user model, nudges after a turn), done once at Meshpool's turn
runner (ui/agent_api.go) so Meshpool Code, Meshpool Work, Pi, Hermes and outside agents all learn the same way:

- after_turn(): a cheap gate (does the message state a standing preference or correct the work?), then one small
  model call that writes 0-3 lasting preferences as short lines; lines already in memory are skipped. Runs in the
  background after the reply, never blocks or fails a turn, and is off when the user turns memory off.
- pending(): what was learned since the chat last heard it, added to the chat's next message (an agent mid-chat
  follows a new preference at once, not only in new chats).
- preferences(): the user's standing preferences for an engine's own writer prompt (the deck engine reads them).
"""
from __future__ import annotations

import json
import os
import re
import threading

from meshpool_harness import assistant as A

GATE = re.compile(r"(?i)\b(never|always|don'?t|do not|stop|i (?:prefer|like|love|hate|dislike|want|need)|"
                  r"from now on|make sure|by default|default to|should(?:n'?t)?|hard no|too (?:long|short|much|little|"
                  r"wordy|dense|thin)|instead of|rather than|not like (?:that|this)|i told you|again)\b")
SYSTEM = """You keep a user's standing preferences. Read the user's message (and the reply it answers, for context).
List only LASTING preferences about how this user wants work done that a future chat should follow: style, format,
tone, defaults, things to always or never do. Not tasks, not facts about this one piece of work, not the content of
a document, nothing about the system. Each is one short sentence about the user, starting with "Prefers", "Wants",
"Never wants" or "Always wants", under 25 words. At most 3; none is common. Return ONLY JSON {"preferences": [...]}"""
MAX_PENDING = 6
_LOCK = threading.Lock()
_PENDING: dict[str, list[str]] = {}


def _on() -> bool:
    return os.environ.get("MH_LEARN", "1") != "0" and A.settings()["memory_on"]


def worth_a_look(text: str) -> bool:
    """A message that states a standing preference or corrects the work (cheap, no model call)."""
    t = (text or "").split("​")[0]
    return 12 <= len(t) <= 6000 and bool(GATE.search(t))


def _key(line: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", line.lower()) if len(w) > 2} - {
        "prefers", "wants", "never", "always", "the", "and", "for", "with", "that", "this", "user"}


def _known(line: str, lines: list[str]) -> bool:
    k = _key(line)
    return any(len(k & _key(x)) >= 0.7 * max(1, min(len(k), len(_key(x)))) for x in lines if _key(x))


def extract(text: str, reply: str, client, model: str) -> list[str]:
    """0-3 lasting preferences in the user's message (never raises)."""
    try:
        r = client.chat(model=model, messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"USER MESSAGE:\n{text[:6000]}\n\nTHE REPLY IT ANSWERS (context only):\n"
                                        f"{(reply or '')[:2000]}"}],
            max_tokens=400, temperature=0.1, task_id="learn", role="worker",
            extra={"response_format": {"type": "json_object"}})
        txt = r["choices"][0]["message"].get("content") or ""
        got = json.loads(txt[txt.find("{"): txt.rfind("}") + 1]).get("preferences") or []
    except Exception:  # noqa: BLE001 - learning is best effort
        return []
    return [re.sub(r"\s+", " ", str(x)).strip() for x in got if isinstance(x, str) and 8 <= len(x.strip()) <= 240][:3]


def after_turn(chat_id: str, text: str, reply: str = "", client=None, model: str | None = None) -> list[str]:
    """Learn from one turn: the preferences saved to memory (and queued for the chat's next message)."""
    if not _on() or not worth_a_look(text):
        return []
    if client is None:
        from meshpool_harness.core.client import LiveClient
        client = LiveClient("learn")
    model = model or os.environ.get("MH_MODEL_FAST") or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
    have = [x for x in A.memory().splitlines() if x.strip()]
    added = []
    for p in extract(text, reply, client, model):
        if _known(p, have + added):
            continue
        try:
            if A.remember(p) == "Saved to memory.":
                added.append(p)
        except ValueError:                                   # a task or anything memory refuses is not kept
            continue
    if added:
        with _LOCK:
            _PENDING[chat_id] = (_PENDING.get(chat_id, []) + added)[-MAX_PENDING:]
    return added


def after_turn_async(chat_id: str, text: str, reply: str = "", on_learned=None) -> threading.Thread:
    def run():
        got = after_turn(chat_id, text, reply)
        if got and on_learned:
            try:
                on_learned(got)
            except Exception:  # noqa: BLE001
                pass
    th = threading.Thread(target=run, daemon=True, name="meshpool-learn")
    th.start()
    return th


def pending(chat_id: str) -> str:
    """Preferences learned since this chat last heard them, as a note for its next message ("" when none)."""
    with _LOCK:
        got = _PENDING.pop(chat_id, [])
    if not got:
        return ""
    return "Standing preferences the user just taught you (saved to memory; follow them from now on):\n" + \
        "\n".join(f"- {x}" for x in got)


def preferences() -> str:
    """The user's standing preferences from memory, for an engine's writer prompt ("" when off or none)."""
    if os.environ.get("MH_LEARN", "1") == "0":
        return ""
    try:
        if not A.settings()["memory_on"]:
            return ""
        lines = [x.strip().lstrip("-• ").strip() for x in A.memory().splitlines() if x.strip()]
    except Exception:  # noqa: BLE001
        return ""
    prefs = [x for x in lines if re.match(r"(?i)(prefers|wants|never wants|always wants|likes|dislikes)\b", x)]
    if not prefs:
        return ""
    return "The user's standing preferences (learned from their feedback; they override the defaults above):\n" + \
        "\n".join(f"- {x}" for x in prefs[-30:])
