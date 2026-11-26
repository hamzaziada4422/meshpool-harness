"""An OpenAI-compatible endpoint backed by the local Claude Code CLI (`claude -p`), for trying meshpool's own harness
on a Claude model (Haiku, Sonnet) with no meshpool gateway credit. Test use only.

    python testkit/claude_shim.py 8920 haiku
    MESHPOOL_BASE_URL=http://127.0.0.1:8920/v1 MESHPOOL_API_KEY=x MH_MODEL=claude-haiku ...

Each POST /v1/chat/completions becomes one `claude -p` call with Claude Code's own tools off (--tools ""): the
conversation, meshpool's system prompt and meshpool's tool list go in as text, and the model answers with one JSON
object, {"content": ..., "tool_calls": [{"name", "arguments"}]}, which is sent back as an OpenAI reply (streamed when
asked). Meshpool runs the tools itself, so its prompts, tools, guards and checks are exactly the release's; only the
model differs. Calls are slower than an API (the CLI starts per call) and the model reads tools as text.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8920
MODEL = sys.argv[2] if len(sys.argv) > 2 else "haiku"
LOG = sys.argv[3] if len(sys.argv) > 3 else "/tmp/claude_shim.jsonl"
_LOCK = threading.Lock()

RULES = """You are the model behind an agent harness. You cannot act yourself: you act only by asking the harness to
run its tools, listed below. Answer with ONE JSON object and nothing else (no prose around it, no code fence):
{"content": "<text for the user, may be empty while you still work>", "tool_calls": [{"name": "<tool>", "arguments": {...}}]}
Put the tool calls you want run now in tool_calls (several are fine when they do not depend on each other). Leave
tool_calls empty only when the task is finished or you need the user; then content MUST be your complete final
answer to the user (never empty). Arguments must match the tool's parameters exactly."""

PLAIN = """You are the model behind an agent harness. No tools are available for this reply: answer the user directly in
plain text (no JSON), with your complete final answer."""


def _text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in c)
    return "" if c is None else str(c)


def build_prompt(body: dict) -> tuple[str, str]:
    msgs = body.get("messages") or []
    system = "\n\n".join(_text(m.get("content")) for m in msgs if m.get("role") in ("system", "developer"))
    tools = [t.get("function", t) for t in body.get("tools") or []]
    lines = ["# Tools the harness can run"]
    for t in tools:
        params = json.dumps(t.get("parameters") or {}, separators=(",", ":"))
        lines.append(f"## {t.get('name')}\n{str(t.get('description') or '')[:1500]}\nparameters: {params[:3000]}")
    lines.append("\n# Conversation so far")
    for m in msgs:
        role = m.get("role")
        if role in ("system", "developer"):
            continue
        if role == "assistant":
            calls = [{"name": c["function"]["name"], "arguments": c["function"].get("arguments")}
                     for c in m.get("tool_calls") or []]
            lines.append("[assistant]\n" + json.dumps({"content": _text(m.get("content")), "tool_calls": calls}))
        elif role == "tool":
            lines.append(f"[tool result for {m.get('tool_call_id', '')}]\n{_text(m.get('content'))[:30000]}")
        else:
            lines.append(f"[{role}]\n{_text(m.get('content'))}")
    lines.append("\n# Your turn: one JSON object, as the rules say.")
    if not tools:
        lines[-1] = "\n# Your turn: answer the user in plain text."
        return system + "\n\n" + PLAIN, "\n\n".join(lines)
    return system + "\n\n" + RULES, "\n\n".join(lines)


def ask_claude(system: str, prompt: str) -> str:
    r = subprocess.run(["claude", "-p", "--model", MODEL, "--tools", "", "--strict-mcp-config",
                        "--no-session-persistence", "--output-format", "text", "--system-prompt", system],
                       input=prompt, capture_output=True, text=True, timeout=600, cwd="/tmp")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "claude -p failed").strip()[:500])
    return r.stdout.strip()


_INVOKE = re.compile(r'<invoke name="([^"]+)">(.*?)</invoke>', re.S)
_PARAM = re.compile(r'<parameter name="([^"]+)">(.*?)</parameter>', re.S)


def _invokes(out: str) -> tuple[str, list]:
    """Claude's own tool-call markup (<invoke name=...><parameter name=...>), which it sometimes writes instead of
    the JSON asked for: the calls, and the text before the first one."""
    calls = []
    for name, inner in _INVOKE.findall(out):
        args = {}
        for k, v in _PARAM.findall(inner):
            v = v.strip()
            try:
                args[k] = json.loads(v) if v[:1] in "[{" or v in ("true", "false", "null") or v.lstrip("-").isdigit() else v
            except ValueError:
                args[k] = v
        calls.append({"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args)}})
    text = re.split(r"<(?:function_calls|invoke)\b", out, maxsplit=1)[0].strip()
    return text, calls


def parse(out: str) -> tuple[str, list]:
    if "<invoke name=" in out and not out.strip().startswith("{"):
        return _invokes(out)
    s = out.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    try:
        obj = json.loads(s[i:j + 1]) if i >= 0 and j > i else None
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        return out, []
    calls = []
    for c in obj.get("tool_calls") or []:
        if isinstance(c, dict) and c.get("name"):
            a = c.get("arguments", {})
            calls.append({"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                          "function": {"name": str(c["name"]),
                                       "arguments": a if isinstance(a, str) else json.dumps(a)}})
    return str(obj.get("content") or ""), calls


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": "claude-" + MODEL, "object": "model"}]})
        return self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "not found"}})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        system, prompt = build_prompt(body)
        t0 = time.time()
        try:
            out = ask_claude(system, prompt)
        except Exception as e:  # noqa: BLE001
            return self._json(502, {"error": {"message": f"claude -p: {e}"}})
        content, calls = parse(out) if body.get("tools") else (out, [])
        usage = {"prompt_tokens": (len(system) + len(prompt)) // 4, "completion_tokens": len(out) // 4}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        with _LOCK, open(LOG, "a") as f:
            f.write(json.dumps({"t": round(time.time() - t0, 1), "n_tools": len(body.get("tools") or []),
                                "calls": [c["function"]["name"] for c in calls], "content": content[:200],
                                "raw": out[:400]}) + "\n")
        finish = "tool_calls" if calls else "stop"
        msg = {"role": "assistant", "content": content or None}
        if calls:
            msg["tool_calls"] = calls
        rid = "chatcmpl-" + uuid.uuid4().hex[:12]
        if not body.get("stream"):
            return self._json(200, {"id": rid, "object": "chat.completion", "model": body.get("model"),
                                    "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": usage})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def ev(obj):
            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        base = {"id": rid, "object": "chat.completion.chunk", "model": body.get("model")}
        delta = {"role": "assistant", "content": content}
        if calls:
            delta["tool_calls"] = [dict(c, index=i) for i, c in enumerate(calls)]
        ev(dict(base, choices=[{"index": 0, "delta": delta, "finish_reason": None}]))
        ev(dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": finish}]))
        ev(dict(base, choices=[], usage=usage))
        self.wfile.write(b"data: [DONE]\n\n")


if __name__ == "__main__":
    print(f"claude shim on 127.0.0.1:{PORT}, model {MODEL}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
