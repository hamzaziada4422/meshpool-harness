"""Keep tool calls and tool results paired, so a chat is never rejected by an Anthropic-style API."""
from __future__ import annotations

INTERRUPTED = "Not run: the chat was interrupted."


def repair_tool_pairs(messages: list) -> tuple[list, bool]:
    """Return ``(messages, changed)`` where every assistant tool call has exactly one result right after it and no
    tool result lacks its call.

    - a call without a result gets a synthetic one ("Not run: the chat was interrupted."), placed after the
      assistant's existing results;
    - a result whose id matches no call of the nearest preceding assistant message (or with no assistant before it)
      is dropped;
    - a second result for the same id is dropped (the first is kept).

    The system message and every other message are untouched. One O(n) pass; when nothing is wrong the SAME list
    object is returned (``changed`` False), otherwise a new list (the input is never mutated, unchanged messages
    are shared)."""
    n = len(messages)
    out: list | None = None
    i = 0
    while i < n:
        m = messages[i]
        role = m.get("role") if isinstance(m, dict) else None
        if role == "assistant" and m.get("tool_calls"):
            ids = [c.get("id") for c in m["tool_calls"] if isinstance(c, dict) and c.get("id")]
            j = i + 1
            while j < n and isinstance(messages[j], dict) and messages[j].get("role") == "tool":
                j += 1
            seen: set = set()
            keep = []
            for t in messages[i + 1:j]:
                tid = t.get("tool_call_id")
                if tid in ids and tid not in seen:
                    seen.add(tid)
                    keep.append(t)
            missing = [k for k in ids if k not in seen]
            if len(keep) != j - i - 1 or missing:
                if out is None:
                    out = list(messages[:i])
                out.append(m)
                out.extend(keep)
                out.extend({"role": "tool", "tool_call_id": k, "content": INTERRUPTED} for k in missing)
            elif out is not None:
                out.extend(messages[i:j])
            i = j
            continue
        if role == "tool":                                      # no assistant call block before it
            if out is None:
                out = list(messages[:i])
        elif out is not None:
            out.append(m)
        i += 1
    return (messages, False) if out is None else (out, True)
