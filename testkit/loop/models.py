"""Which models on the endpoint actually work for the agent? One tiny chat call and one tool call per model.

    python testkit/loop/models.py --out /tmp/models            # every model the endpoint lists
    python testkit/loop/models.py --only deepseek,gpt,claude    # ids containing any of these words

For each model: a plain reply ("say OK"), then a forced tool call (the agent cannot work without tool calling).
Records pass/fail, the error text, latency and, when /models gives them, prices per 1M tokens. Writes models.json and
models.md; prints the ids that passed both, cheapest first, for the loop's --models.
Cost: two calls of a few tokens per model.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = (os.environ.get("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")
KEY = os.environ.get("MESHPOOL_API_KEY", "")
SKIP = ("embed", "embedding", "whisper", "tts", "dall-e", "image", "moderation", "rerank", "audio", "transcribe",
        "realtime", "vision-preview", "sora", "veo", "imagen")
TOOL = {"type": "function", "function": {"name": "add", "description": "Add two integers.", "parameters": {
    "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}}


LAST_HEADERS: dict = {}


def req(path: str, body: dict | None = None, timeout: int = 90, headers_out: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method="POST" if body is not None else "GET",
                               headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        if headers_out is not None:
            headers_out.update({k.lower(): v for k, v in resp.headers.items()
                                if k.lower().startswith(("x-litellm", "x-meshpool", "x-ratelimit"))})
        return json.loads(resp.read().decode("utf-8", "replace"))


def err_text(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            body = ""
        return f"HTTP {e.code}: {body}"
    return f"{type(e).__name__}: {str(e)[:200]}"


def price(m: dict) -> dict:
    """Prices per 1M tokens from whatever shape /models uses (OpenRouter-style per-token strings, or per-1M)."""
    p = m.get("pricing") or m.get("price") or {}
    out = {}
    for k_in, k in (("prompt", "in"), ("input", "in"), ("completion", "out"), ("output", "out"),
                    ("input_cache_read", "cached"), ("cached_input", "cached"), ("cache_read", "cached")):
        v = p.get(k_in)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        out[k] = round(v * 1e6, 4) if v < 0.01 else v      # per token -> per 1M
    return out


def probe(mid: str) -> dict:
    row = {"id": mid, "chat": False, "tools": False}
    t0 = time.time()
    try:
        hdr: dict = {}
        r = req("/chat/completions", {"model": mid, "max_tokens": 600,       # room for a reasoning model's thinking
                                      "messages": [{"role": "user", "content": "Reply with just: OK"}]}, headers_out=hdr)
        msg = r["choices"][0]["message"]
        txt = (msg.get("content") or "").strip()
        row["chat"] = bool(txt)
        row["chat_s"] = round(time.time() - t0, 2)
        row["reply"] = txt[:40]
        row["reasoning"] = bool(msg.get("reasoning_content") or msg.get("reasoning")
                                or ((r.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens"))
        row["usage"] = r.get("usage")
        row["headers"] = hdr
        if not txt:
            row["error"] = "empty reply: " + json.dumps(r["choices"][0])[:200]
    except Exception as e:  # noqa: BLE001
        row["error"] = err_text(e)
        return row
    t0 = time.time()
    try:
        r = req("/chat/completions", {"model": mid, "max_tokens": 600, "tools": [TOOL],
                                      "tool_choice": "auto",
                                      "messages": [{"role": "user", "content": "Use the add tool to add 2 and 3."}]})
        calls = r["choices"][0]["message"].get("tool_calls") or []
        args = json.loads(calls[0]["function"]["arguments"]) if calls else {}
        row["tools"] = bool(calls) and calls[0]["function"]["name"] == "add" and args.get("a") == 2
        row["tools_s"] = round(time.time() - t0, 2)
        if not row["tools"]:
            row["error"] = "no usable tool call: " + json.dumps(r["choices"][0]["message"])[:200]
    except Exception as e:  # noqa: BLE001
        row["error"] = "tools: " + err_text(e)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/models")
    ap.add_argument("--only", default="")
    ap.add_argument("--parallel", type=int, default=8)
    a = ap.parse_args()
    if not KEY:
        sys.exit("MESHPOOL_API_KEY is not set")
    listed = req("/models")
    items = listed.get("data") if isinstance(listed, dict) else listed
    models = {m["id"]: m for m in items or [] if isinstance(m, dict) and m.get("id")}
    ids = [i for i in sorted(models) if not any(s in i.lower() for s in SKIP)]
    if a.only:
        words = [w.strip().lower() for w in a.only.split(",") if w.strip()]
        ids = [i for i in ids if any(w in i.lower() for w in words)]
    print(f"{len(models)} models listed, probing {len(ids)}", flush=True)
    with ThreadPoolExecutor(a.parallel) as ex:
        rows = list(ex.map(probe, ids))
    for r in rows:
        r["price"] = price(models[r["id"]])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "models.json").write_text(json.dumps(rows, indent=1))
    (out / "models_raw_sample.json").write_text(json.dumps(items[:3] if items else [], indent=1)[:4000])
    good = sorted([r for r in rows if r["chat"] and r["tools"]],
                  key=lambda r: (r["price"].get("out", 1e9), r.get("tools_s", 99)))
    sample = next((r for r in rows if r.get("headers")), None)
    lines = ["# Models on the endpoint", "",
             "Cost headers seen: " + (", ".join(sorted(sample["headers"])) if sample else "none"), "", f"{len(good)} of {len(rows)} probed models answer and call tools.", "",
             "| Model | Chat | Tools | Reasoning | Chat s | Tool s | Call cost header | Error |",
             "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (not (r["chat"] and r["tools"]), r["id"])):
        p = r["price"]
        h = r.get("headers") or {}
        cost_h = h.get("x-litellm-response-cost", "")
        lines.append(f"| {r['id']} | {'yes' if r['chat'] else 'no'} | {'yes' if r['tools'] else 'no'} | "
                     f"{'yes' if r.get('reasoning') else ''} | {r.get('chat_s', '')} | {r.get('tools_s', '')} | "
                     f"{cost_h} | {(r.get('error') or '')[:90].replace('|', '/')} |")
    text = "\n".join(lines) + "\n"
    (out / "models.md").write_text(text)
    print(text)
    print("WORKING=" + ",".join(r["id"] for r in good))


if __name__ == "__main__":
    main()
