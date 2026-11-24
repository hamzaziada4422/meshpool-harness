"""Request shaping for endpoints that are stricter than OpenAI (audit ws03: H-05, H-19, H-21, P-06, W-02).

Everything here is a pure function of its input: the stored history is never mutated (callers get copies) and every
transform is deterministic and leaves messages that need no change byte-identical, so the prefix a provider caches
stays the same from one request to the next.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Any

_ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_ID_ALNUM9 = re.compile(r"^[A-Za-z0-9]{9}$")
_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SHOT_NOTE = " [screenshot removed to save space]"


def _cap(caps: Any, name: str, default: Any = None) -> Any:
    """Read a capability from a dict or an object (ModelCaps-like); None means unset."""
    if caps is None:
        return default
    v = caps.get(name) if isinstance(caps, dict) else getattr(caps, name, None)
    return default if v is None else v


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content)


def _parts(content: Any) -> list:
    if isinstance(content, list):
        return list(content)
    s = _text(content)
    return [{"type": "text", "text": s}] if s else []


def _b62(digest: bytes, n: int = 9) -> str:
    v = int.from_bytes(digest, "big")
    out = ""
    while len(out) < n:
        v, r = divmod(v, 62)
        out += _B62[r]
    return out


# ---------------------------------------------------------------- 1. roles (H-05)

def _blank(content: Any) -> bool:
    if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") != "text" for p in content):
        return False
    return not _text(content).strip()


def _merge_user(a: dict, b: dict) -> dict:
    ca, cb = a.get("content"), b.get("content")
    out = dict(a)
    if not isinstance(ca, list) and not isinstance(cb, list):
        out["content"] = "\n\n".join(s for s in (_text(ca), _text(cb)) if s)
    else:
        out["content"] = _parts(ca) + _parts(cb)
    return out


def _merge_assistant(a: dict, b: dict) -> dict:
    out = dict(a)
    texts = [s for s in (_text(a.get("content")), _text(b.get("content"))) if s.strip()]
    calls = list(a.get("tool_calls") or []) + list(b.get("tool_calls") or [])
    if texts:
        out["content"] = "\n\n".join(texts)
    elif "content" not in out:
        out["content"] = None
    if calls:
        out["tool_calls"] = calls
    return out


def normalize_roles(messages: list, strict: bool = False) -> list:
    """Make the role sequence acceptable to strict chat templates. Returns new objects; input untouched."""
    msgs = copy.deepcopy(list(messages))
    # a system message anywhere but the very first slot becomes a user note
    for i, m in enumerate(msgs):
        if i > 0 and m.get("role") == "system":
            msgs[i] = {**m, "role": "user", "content": "[System note] " + _text(m.get("content"))}
    # assistant turns with nothing in them
    msgs = [m for m in msgs if not (m.get("role") == "assistant" and not m.get("tool_calls")
                                    and _blank(m.get("content")))]
    merged: list = []
    for m in msgs:
        role = m.get("role")
        if merged and role == "user" and not strict and merged[-1].get("role") == "user" and \
                _text(merged[-1].get("content")).startswith("<environment_context>"):
            merged.append(m)                 # Codex 0.171's environment context stays a message of its own (c171)
        elif merged and role in ("user", "assistant") and merged[-1].get("role") == role:
            merged[-1] = (_merge_user if role == "user" else _merge_assistant)(merged[-1], m)
        else:
            merged.append(m)
    return _strict(merged) if strict else merged


def _strict(msgs: list) -> list:
    sys_msg = None
    if msgs and msgs[0].get("role") == "system":
        sys_msg, msgs = msgs[0], msgs[1:]
    if msgs and msgs[0].get("role") == "assistant":
        msgs = [{"role": "user", "content": "Continue."}] + msgs
    if sys_msg is None:
        return msgs
    sys_text = _text(sys_msg.get("content"))
    if not msgs or msgs[0].get("role") != "user":
        return [{"role": "user", "content": "[System instructions]\n" + sys_text}] + msgs
    first = dict(msgs[0])
    head = "[System instructions]\n" + sys_text + "\n\n[User]\n"
    c = first.get("content")
    first["content"] = [{"type": "text", "text": head}] + c if isinstance(c, list) else head + _text(c)
    return [first] + msgs[1:]


# ---------------------------------------------------------------- 2. tool ids (H-19)

def fix_tool_ids(messages: list, style: str | None = None) -> list:
    """Make tool-call ids valid and unique, results follow their call, orphans and missing results repaired."""
    msgs = copy.deepcopy(list(messages))
    used: set = set()
    gi = 0
    turns: list = []          # {"map": {orig: [new, ...]}, "pending": [new, ...]}
    out: list = []
    open_turn: dict | None = None

    def valid(i: Any) -> bool:
        return (isinstance(i, str) and bool(_ID_OK.match(i)) and i not in used
                and (style != "alnum9" or bool(_ID_ALNUM9.match(i))))

    def flush() -> None:
        nonlocal open_turn
        if open_turn is not None:
            for cid in open_turn["pending"]:
                out.append({"role": "tool", "tool_call_id": cid,
                            "content": "(no result: the call was interrupted)"})
            open_turn["pending"] = []
        open_turn = None

    for m in msgs:
        role = m.get("role")
        if role == "tool":
            tid = m.get("tool_call_id")
            hit = None
            for t in reversed(turns):
                for new in t["map"].get(tid, []):
                    if new in t["pending"]:
                        hit = (t, new)
                        break
                if hit:
                    break
            if hit is None:
                flush()
                out.append({"role": "user", "content": "[Tool result] " + _text(m.get("content"))})
                continue
            t, new = hit
            t["pending"].remove(new)
            if new != tid:
                m["tool_call_id"] = new
            out.append(m)
            continue
        flush()
        if role == "assistant" and m.get("tool_calls"):
            turn: dict = {"map": {}, "pending": []}
            for call in m["tool_calls"]:
                orig = call.get("id")
                new = orig
                if not valid(orig):
                    salt = 0
                    while True:
                        seed = f"{orig}|{gi}" + (f"#{salt}" if salt else "")
                        new = _b62(hashlib.sha1(seed.encode()).digest())
                        if valid(new):
                            break
                        salt += 1
                    call["id"] = new
                used.add(new)
                gi += 1
                turn["map"].setdefault(orig, []).append(new)
                turn["pending"].append(new)
            turns.append(turn)
            open_turn = turn
        out.append(m)
    # a trailing assistant turn (nothing after it) may still get its results: leave it alone
    return out


# ---------------------------------------------------------------- 3. schemas (H-21)

_SUBSET_KEYS = ("type", "nullable", "description", "enum", "properties", "required", "items", "anyOf",
                "minimum", "maximum", "minItems", "maxItems", "format")
_DROP = ("$defs", "definitions", "$schema", "$id", "$comment")
_REF_PREFIXES = ("#/$defs/", "#/definitions/")
_MAX_REF_DEPTH = 8
_EMPTY_PARAMS = {"type": "object", "properties": {}}


def is_subset_host(base: str) -> bool:
    b = (base or "").lower()
    return "generativelanguage.googleapis.com" in b or "aiplatform.googleapis.com" in b


def _walk(node: Any, defs: dict, depth: int, stack: tuple, subset: bool) -> Any:
    if not isinstance(node, dict):
        return node
    if isinstance(node.get("$ref"), str):
        ref = node["$ref"]
        name = next((ref[len(p):] for p in _REF_PREFIXES if ref.startswith(p)), None)
        if name is None or depth >= _MAX_REF_DEPTH or name in stack or not isinstance(defs.get(name), dict):
            return {"type": "object"}
        merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
        return _walk(merged, defs, depth + 1, stack + (name,), subset)
    out: dict = {}
    for k, v in node.items():
        if k in _DROP:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: _walk(pv, defs, depth, stack, subset) for pk, pv in v.items()}
        elif k == "items":
            out[k] = ([_walk(x, defs, depth, stack, subset) for x in v] if isinstance(v, list)
                      else _walk(v, defs, depth, stack, subset))
        elif k in ("anyOf", "oneOf", "allOf") and isinstance(v, list):
            out[k] = [_walk(x, defs, depth, stack, subset) for x in v]
        elif k == "additionalProperties" and isinstance(v, dict):
            out[k] = _walk(v, defs, depth, stack, subset)
        else:
            out[k] = copy.deepcopy(v)
    if subset:
        out = _to_subset(out)
    if isinstance(out.get("properties"), dict):     # an allOf branch may require a sibling branch's property
        _prune_required(out)
    return out


def _prune_required(node: dict) -> None:
    if "required" not in node:
        return
    props = node.get("properties")
    req = node["required"]
    keep = [r for r in req if isinstance(props, dict) and r in props] if isinstance(req, list) else []
    if keep:
        node["required"] = keep
    else:
        del node["required"]


def _to_subset(n: dict) -> dict:
    n = dict(n)
    for sub in n.pop("allOf", None) or []:
        if not isinstance(sub, dict):
            continue
        for k, v in sub.items():
            if k == "properties" and isinstance(v, dict):
                n["properties"] = {**(n.get("properties") or {}), **v}
            elif k == "required" and isinstance(v, list):
                have = list(n.get("required") or [])
                n["required"] = have + [r for r in v if r not in have]
            else:
                n.setdefault(k, v)
    if "oneOf" in n:
        n["anyOf"] = list(n.get("anyOf") or []) + list(n.pop("oneOf") or [])
    t = n.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        if len(non_null) != len(t):
            n["nullable"] = True
        if len(non_null) > 1:
            n.pop("type")
            n["anyOf"] = list(n.get("anyOf") or []) + [{"type": x} for x in non_null]
        else:
            n["type"] = non_null[0] if non_null else "string"
    if "const" in n:
        n["enum"] = [str(n.pop("const"))]
    if isinstance(n.get("enum"), list):
        n["enum"] = [str(x) for x in n["enum"]]
        n["type"] = "string"
    if isinstance(n.get("anyOf"), list):
        nulls = [a for a in n["anyOf"] if isinstance(a, dict) and a.get("type") == "null"]
        if nulls:
            rest = [a for a in n["anyOf"] if a not in nulls]
            n["nullable"] = True
            if len(rest) == 1 and isinstance(rest[0], dict):
                n.pop("anyOf")
                for k, v in rest[0].items():
                    n.setdefault(k, v)
            elif rest:
                n["anyOf"] = rest
            else:
                n.pop("anyOf")
                n.setdefault("type", "string")
    if n.get("type") == "array" and not isinstance(n.get("items"), dict):
        n["items"] = {"type": "string"}
    if "format" in n and not (n.get("type") == "string" and n["format"] in ("enum", "date-time")):
        n.pop("format")
    return {k: n[k] for k in n if k in _SUBSET_KEYS}


def _clean_params(params: Any, subset: bool) -> dict:
    if not isinstance(params, dict):
        return copy.deepcopy(_EMPTY_PARAMS)
    defs: dict = {}
    for key in ("definitions", "$defs"):
        if isinstance(params.get(key), dict):
            defs.update(params[key])
    out = _walk(params, defs, 0, (), subset)
    if not isinstance(out.get("properties"), dict):
        out["properties"] = {}
    _prune_required(out)
    if out.get("type") != "object" or list(out)[0] != "type":
        out = {"type": "object", **{k: v for k, v in out.items() if k != "type"}}
    return out


def sanitize_schemas(tools: list, subset: bool = False) -> list:
    """Return tools whose parameter schemas every provider accepts (input untouched)."""
    out = []
    for tool in tools or []:
        t = copy.deepcopy(tool)
        holder = t.get("function") if isinstance(t.get("function"), dict) else t
        holder["parameters"] = _clean_params(holder.get("parameters"), subset)
        out.append(t)
    return out


# ---------------------------------------------------------------- 4. images (P-06)

def _is_image(p: Any) -> bool:
    return isinstance(p, dict) and p.get("type") == "image_url"


def _typed_by_user(m: dict) -> bool:
    c = m.get("content")
    if isinstance(c, list):
        first = next((p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"), "")
    else:
        first = _text(c)
    return not (first.startswith("Image returned by") or first.startswith("[Meshpool]"))


def cap_images(messages: list, keep: int = 4, step: int | None = None) -> list:
    """Drop the oldest images in blocks of `step` so the cut (and the cacheable prefix) moves rarely."""
    msgs = copy.deepcopy(list(messages))
    slots = [(i, j) for i, m in enumerate(msgs) if isinstance(m.get("content"), list)
             for j, p in enumerate(m["content"]) if _is_image(p)]
    total = len(slots)
    if total <= keep:
        return msgs
    step = step or max(1, keep // 2)
    cut = ((total - keep + step - 1) // step) * step
    prot = next((i for i in range(len(msgs) - 1, -1, -1)
                 if msgs[i].get("role") == "user" and _typed_by_user(msgs[i])), None)
    prot_slots = [s for s in slots if s[0] == prot]
    if len(prot_slots) > keep:
        drop = set(slots) - set(prot_slots[-keep:])
    else:
        drop = set([s for s in slots if s[0] != prot][:cut])
    for i in sorted({s[0] for s in drop}):
        c = msgs[i]["content"]
        new = [p for j, p in enumerate(c) if (i, j) not in drop]
        if any(_is_image(p) for p in new):
            msgs[i]["content"] = new
        else:
            msgs[i]["content"] = " ".join(p.get("text", "") for p in new if p.get("type") == "text") + _SHOT_NOTE
    return msgs


# ---------------------------------------------------------------- 4b. huge images (OSS-23)

_SHRUNK: dict = {}                                    # (sha1 of url, limits) -> shrunk url; small, newest last


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(float(os.environ.get(name) or default)))
    except ValueError:
        return default


def _shrink(url: str, max_bytes: int, max_side: int) -> str:
    import base64
    import io
    from PIL import Image, ImageOps
    head, _, b64 = url.partition(";base64,")
    im = Image.open(io.BytesIO(base64.b64decode(b64, validate=False)))
    if getattr(im, "is_animated", False) or getattr(im, "n_frames", 1) > 1:
        return url
    w, h = im.size
    if len(b64) <= max_bytes and max(w, h) <= max_side:
        return url
    im = ImageOps.exif_transpose(im)
    im.load()
    alpha = im.mode in ("RGBA", "LA", "PILA") or (im.mode == "P" and "transparency" in im.info)
    base = im.convert("RGBA") if alpha or im.mode in ("P", "LA") else im.convert("RGB")
    side = min(max_side, max(base.size))
    last = url
    while True:
        cur = base.copy()
        cur.thumbnail((side, side), Image.LANCZOS)
        if alpha:
            buf = io.BytesIO()
            cur.save(buf, "PNG", optimize=True)
            last = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            if len(last) - len("data:image/png;base64,") <= max_bytes:
                return last
        flat = cur
        if cur.mode == "RGBA":
            flat = Image.new("RGB", cur.size, (255, 255, 255))
            flat.paste(cur, mask=cur.getchannel("A"))
        for q in (85, 70, 55):
            buf = io.BytesIO()
            flat.save(buf, "JPEG", quality=q, optimize=True)
            last = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            if len(last) - len("data:image/jpeg;base64,") <= max_bytes:
                return last
        if side < 256 * 2:
            return last                               # as small as it gets: send the smallest try
        side //= 2


def shrink_image_url(url: str, max_bytes: int | None = None, max_side: int | None = None) -> str:
    """A data:image URL over max_bytes of base64 (MH_IMAGE_MAX_BYTES, 1.5 MB) or with a side over max_side
    (MH_IMAGE_MAX_SIDE, 2048 px) is downscaled and re-encoded (JPEG, or PNG when it has transparency and fits).
    Anything else, or anything Pillow cannot read, comes back unchanged. Never raises; the same input gives the same
    output (the cacheable prefix stays put)."""
    try:
        if not isinstance(url, str) or not url.startswith("data:image/") or ";base64," not in url[:80]:
            return url
        mb = max_bytes or _env_int("MH_IMAGE_MAX_BYTES", 1_500_000)
        ms = max_side or _env_int("MH_IMAGE_MAX_SIDE", 2048)
        key = (hashlib.sha1(url.encode("ascii", "ignore")).hexdigest(), mb, ms)
        if key in _SHRUNK:
            return _SHRUNK[key]
        out = _shrink(url, mb, ms)
        if len(_SHRUNK) >= 16:
            _SHRUNK.pop(next(iter(_SHRUNK)))
        _SHRUNK[key] = out
        return out
    except Exception:  # noqa: BLE001 - an image we cannot read is sent as it is
        return url


def shrink_images(messages: list, max_bytes: int | None = None) -> list:
    """Every image_url part through shrink_image_url. Only messages that change are new objects; a request with no
    big image costs a length check per image and no decoding."""
    cheap = max_bytes or _env_int("MH_IMAGE_MAX_BYTES", 1_500_000)
    out = list(messages)
    for i, m in enumerate(out):
        c = m.get("content") if isinstance(m, dict) else None
        if not isinstance(c, list):
            continue
        new, changed = [], False
        for p in c:
            u = p.get("image_url") if _is_image(p) else None
            url = u.get("url") if isinstance(u, dict) else u
            if isinstance(url, str) and len(url) > cheap:
                nu = shrink_image_url(url, max_bytes)
                if nu != url:
                    p = {**p, "image_url": {**u, "url": nu} if isinstance(u, dict) else nu}
                    changed = True
            new.append(p)
        if changed:
            out[i] = {**m, "content": new}
    return out


# ---------------------------------------------------------------- 5. no-tools endpoints (W-02)

def _sig(tool: dict) -> tuple:
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
    props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
    req = set(params.get("required") or [])
    args = []
    for k, v in props.items():
        t = v.get("type") if isinstance(v, dict) else None
        t = "|".join(map(str, t)) if isinstance(t, list) else (t or "any")
        args.append(f"{k}{'' if k in req else '?'}: {t}")
    return fn.get("name", "tool"), args, " ".join(str(fn.get("description") or "").split())[:200]


def text_tools_prompt(tools: list) -> str:
    lines = []
    for tool in tools or []:
        name, args, desc = _sig(tool)
        lines.append(f"- {name}({', '.join(args)}): {desc}")
    return ("You cannot call tools natively. To use a tool, reply with a block exactly like\n"
            '<tool_call>{"name": "read_file", "arguments": {"path": "x"}}</tool_call>\n'
            "then stop and wait for the result. Results come back in the next message as "
            '"[Result of NAME]". Available tools:\n' + "\n".join(lines))


def _render_call(call: dict) -> str:
    fn = call.get("function") or {}
    raw = fn.get("arguments")
    try:
        args: Any = json.loads(raw) if isinstance(raw, str) else (raw if raw is not None else {})
    except ValueError:
        args = raw
    return "<tool_call>" + json.dumps({"name": fn.get("name"), "arguments": args}, ensure_ascii=False) + "</tool_call>"


def strip_tools(body: dict, strict: bool = False) -> dict:
    """Rewrite a request for an endpoint without native tool calling; the model gets a text protocol instead."""
    out = copy.deepcopy(body)
    tools = out.pop("tools", None)
    out.pop("tool_choice", None)
    out.pop("parallel_tool_calls", None)
    names: dict = {}
    msgs = []
    for m in out.get("messages") or []:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            blocks = [_render_call(c) for c in m["tool_calls"]]
            for c in m["tool_calls"]:
                names[c.get("id")] = (c.get("function") or {}).get("name") or "tool"
            m = {k: v for k, v in m.items() if k != "tool_calls"}
            m["content"] = "\n".join(s for s in [_text(m.get("content")).strip()] + blocks if s)
        elif role == "tool":
            name = names.get(m.get("tool_call_id"), "tool")
            m = {"role": "user", "content": f"[Result of {name}]\n" + _text(m.get("content"))}
        msgs.append(m)
    msgs = normalize_roles(msgs, strict)
    if tools:
        extra = text_tools_prompt(tools)
        if msgs and msgs[0].get("role") == "system":
            msgs[0] = {**msgs[0], "content": _text(msgs[0].get("content")) + "\n\n" + extra}
        else:
            msgs.insert(0, {"role": "system", "content": extra})
    out["messages"] = msgs
    return out


_BLOCK = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_text_tool_calls(text: str) -> tuple:
    """Pull <tool_call>{...}</tool_call> blocks out of a reply. Returns (remaining text, OpenAI-style calls)."""
    text = text or ""
    calls: list = []
    spans: list = []
    for idx, mt in enumerate(_BLOCK.finditer(text)):
        inner = mt.group(1).strip()
        fence = _FENCE.match(inner)
        if fence:
            inner = fence.group(1)
        try:
            obj = json.loads(inner)
        except ValueError:
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("name"), str) or not obj["name"]:
            continue
        args = obj.get("arguments", obj.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                continue
        if args is None:
            args = {}
        if not isinstance(args, dict):
            continue
        cid = _b62(hashlib.sha1((mt.group(0) + str(idx)).encode()).digest())
        calls.append({"id": cid, "type": "function",
                      "function": {"name": obj["name"], "arguments": json.dumps(args, ensure_ascii=False)}})
        spans.append(mt.span())
    rest, pos = [], 0
    for a, b in spans:
        rest.append(text[pos:a])
        pos = b
    rest.append(text[pos:])
    return "".join(rest).strip(), calls


# ---------------------------------------------------------------- 6. entry point

def prepare(body: dict, caps: Any = None, base: str = "") -> dict:
    """Shape one chat-completions body for the endpoint described by `caps`. Returns a new dict."""
    out = copy.deepcopy(body)
    refused = set(_cap(caps, "refused", ()) or ())
    if "max_tokens" in out and ("max_tokens" in refused or _cap(caps, "max_tokens_param") == "max_completion_tokens"):
        out["max_completion_tokens"] = out.pop("max_tokens")
    for f in refused:
        if f not in ("model", "messages"):
            out.pop(f, None)
    cap_out = _cap(caps, "max_output", 0)
    if isinstance(cap_out, int) and cap_out > 0:
        for k in ("max_tokens", "max_completion_tokens"):
            if isinstance(out.get(k), int) and out[k] > cap_out:
                out[k] = cap_out
    if out.get("tools"):
        out["tools"] = sanitize_schemas(out["tools"], subset=bool(_cap(caps, "schema_subset", False))
                                        or is_subset_host(base))
    strict = bool(_cap(caps, "strict_roles", False))
    if "messages" in out:
        msgs = fix_tool_ids(out["messages"], _cap(caps, "tool_id_style"))
        out["messages"] = normalize_roles(msgs, strict)
    if _cap(caps, "no_tools", False):
        out = strip_tools(out, strict)
    return out
