"""Helps the agent finish work that goes through connected-app (MCP) tools.

Three small, pure pieces used by Agent._run_tool and Agent.tool_schemas in agent/core.py:

- failure_text: tells a failed call from a good one. A server may flag an error (isError), but many return an
  error as ordinary text ("Error: ...", {"ok": false, "error": ...}). Either way the model gets a clear
  "the call failed" line with what to do next, so it fixes the arguments or the order of steps and calls again
  instead of reading the failure as a result.
- page_hint: a result that says more is available (next_cursor, nextPageToken, has_more) gets a one-line reminder
  to fetch the rest before summarising it.
- LazyTools: with many connected tools the schemas alone fill the context on every call. Past a threshold only an
  index (name and a short description) is shown, plus the tool mcp_find_tools; a tool's full schema is loaded when
  the model looks it up (or calls it by name), and stays loaded.

Turn off: MH_MCP_ASSIST=0 (failure and page notes), MH_MCP_LAZY=0 (lazy tool loading); MH_MCP_LAZY_AT sets the
threshold (default 60 tools from servers other than Meshpool's own).
"""
from __future__ import annotations

import json
import os
import re

FIND = "mcp_find_tools"

FAIL_HINT = ("\n[Meshpool] This call FAILED. Read the message: if it names a missing or wrong argument, a step to do "
             "first, or a format, fix that and call again. If it looks temporary (timeout, 5xx, rate limit, try "
             "again), call it again unchanged, up to 3 times. Do not report this as done or give up before you "
             "have tried a corrected call or another tool.")
SOFT_HINT = ("\n[Meshpool] This result reports a failure even though the call did not error. Treat it as failed: "
             "fix what it names and call again; do not count this step as done.")
_ERR_START = re.compile(r"^\s*(?:(?:error|err|failed|failure|exception|fatal)\s*[:!]|traceback \(most recent call last\))", re.I)
_BAD_STATUS = {"error", "failed", "failure", "fail", "denied", "rejected", "invalid", "forbidden", "unauthorized"}
_NEXT_KEYS = ("next_cursor", "nextCursor", "next_page_token", "nextPageToken", "next_page", "nextPage", "next_token",
              "nextToken", "next_offset", "continuation", "continuation_token", "cursor_next", "after")


def assist_on() -> bool:
    return os.environ.get("MH_MCP_ASSIST", "1") != "0"


def _json(text: str):
    t = text.strip()
    if not t or t[0] not in "{[":
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


def soft_failure(text: str) -> bool:
    """True when ordinary result text reports a failure: it starts with Error/Failed, or is a JSON object saying
    ok/success false, an error, or a failed status."""
    t = str(text or "")
    if _ERR_START.match(t[:80]):
        return True
    d = _json(t[:200000])
    if not isinstance(d, dict):
        return False
    if d.get("ok") is False or d.get("success") is False:
        return True
    if d.get("error") or d.get("errors"):
        if d.get("ok") is True or d.get("success") is True:
            return False
        return not any(k in d for k in ("data", "result", "results", "items", "tickets", "records"))
    st = str(d.get("status") or "").lower()
    return st in _BAD_STATUS


def page_hint(text: str) -> str:
    """One line when a JSON result points at a next page, else ''."""
    d = _json(str(text or "")[:400000])
    if not isinstance(d, dict):
        return ""
    for k in _NEXT_KEYS:
        v = d.get(k)
        if v not in (None, "", False, 0, [], {}):
            return (f"\n[Meshpool] The result has more pages ({k}={json.dumps(v)}). Fetch them all with the same call "
                    f"and that {k} until it is empty before you summarise, count or conclude.")
    if d.get("has_more") is True or d.get("hasMore") is True or d.get("truncated") is True:
        return "\n[Meshpool] The result says there is more. Fetch the rest before you summarise, count or conclude."
    return ""


def classify(out: str, is_error: bool) -> tuple[str, str, bool]:
    """(prefix, suffix, ok) for an MCP result: a failure gets a clear line before and a hint after, a long list
    gets a paging line. Computed on the whole text, so a result that is cut for length keeps its notes."""
    if not assist_on():
        return "", "", not is_error
    if is_error:
        return "ERROR (tool call failed): ", FAIL_HINT, False
    if soft_failure(out):
        return "", SOFT_HINT, False
    return "", page_hint(out), True


def annotate(out: str, is_error: bool) -> tuple[str, bool]:
    pre, post, ok = classify(out, is_error)
    return pre + out + post, ok


# ---------- lazy tool loading --------------------------------------------------------------------------------------

FIND_SCHEMA = {"type": "function", "function": {
    "name": FIND, "description": (
        "Look up connected-app tools you can use. The tool list shows only some of them to save space; the rest are "
        "named in the index below. Give a few words about what you need (e.g. 'close ticket', 'invoice') or exact "
        "tool names; you get the matching tools with their full parameters and they become callable."),
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "What you need, in a few words"},
        "names": {"type": "array", "items": {"type": "string"}, "description": "Exact tool names to load"}}}}}


def lazy_at() -> int:
    try:
        return int(os.environ.get("MH_MCP_LAZY_AT", "60"))
    except ValueError:
        return 60


def _builtin(name: str) -> bool:
    return name.replace("-", "_").startswith("mcp__meshpool_")


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", s.lower()) if len(w) > 1]


class LazyTools:
    """Which connected tools show their full schema. State lives on the Agent (one per chat)."""

    def __init__(self):
        self.active: set[str] = set()

    @staticmethod
    def wanted(schemas: list) -> bool:
        if os.environ.get("MH_MCP_LAZY", "1") == "0":
            return False
        return sum(1 for s in schemas if not _builtin(s["function"]["name"])) > lazy_at()

    def split(self, schemas: list) -> tuple[list, list]:
        """(schemas to send, hidden schemas). Meshpool's own tools and activated ones are sent in full."""
        shown, hidden = [], []
        for s in schemas:
            n = s["function"]["name"]
            (shown if _builtin(n) or n in self.active else hidden).append(s)
        return shown, hidden

    def find_schema(self, hidden: list) -> dict:
        lines = []
        for s in hidden:
            f = s["function"]
            d = re.sub(r"^\[MCP server '[^']*'\]\s*", "", f.get("description") or "")
            d = " ".join(d.split())
            lines.append(f"{f['name']}: {d[:70]}" if len(hidden) <= 400 else f["name"])
        sch = json.loads(json.dumps(FIND_SCHEMA))
        sch["function"]["description"] += "\n\nIndex of tools not loaded yet:\n" + "\n".join(lines)
        return sch

    def search(self, hidden_or_all: list, query: str = "", names=None, limit: int = 8) -> str:
        by_name = {s["function"]["name"]: s for s in hidden_or_all}
        picked: list[str] = []
        for n in names or []:
            n = str(n)
            if n in by_name:
                picked.append(n)
            else:
                near = [k for k in by_name if k.endswith("__" + n) or n in k]
                picked += near[:1]
        qw = _words(query)
        if qw:
            scored = []
            for n, s in by_name.items():
                f = s["function"]
                hay = set(_words(n.split("__", 2)[-1])) | set(_words(f.get("description") or ""))
                score = sum(2 if w in _words(n) else 1 for w in qw if w in hay or any(h.startswith(w) for h in hay))
                if score:
                    scored.append((-score, n))
            picked += [n for _, n in sorted(scored)][:limit]
        picked = list(dict.fromkeys(picked))[:limit]
        if not picked:
            return ("No connected tool matches that. Try other words, or exact names from the index in this tool's "
                    "description.")
        self.active.update(picked)
        out = ["Loaded; you can call these now:"]
        for n in picked:
            f = by_name[n]["function"]
            out.append(f"\n{n}\n{f.get('description', '')}\nparameters: {json.dumps(f.get('parameters', {}))}")
        return "\n".join(out)

    def stub(self, name: str, schemas: list) -> str:
        """The result when a model calls a tool whose schema was not loaded: load it and ask for the call again."""
        for s in schemas:
            if s["function"]["name"] == name:
                self.active.add(name)
                f = s["function"]
                return (f"The schema of {name} was not loaded, so the call was not run. It is loaded now. Call it again "
                        f"with the right arguments.\n{f.get('description', '')}\n"
                        f"parameters: {json.dumps(f.get('parameters', {}))}")
        return ""
