"""Tests for core/compat.py: request shaping for strict endpoints (audit ws03)."""
import copy
import json
import re
from types import SimpleNamespace

from meshpool_harness.core import compat as C


def J(x):
    return json.dumps(x, sort_keys=True)


def call(i, name="read_file", args=None):
    return {"id": i, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {"path": "a"})}}


def asst(*calls, content=None):
    return {"role": "assistant", "content": content, "tool_calls": list(calls)}


def tool(i, text="ok"):
    return {"role": "tool", "tool_call_id": i, "content": text}


# ------------------------------------------------------------------ normalize_roles

def test_consecutive_users_merge_and_input_is_untouched():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    before = copy.deepcopy(msgs)
    out = C.normalize_roles(msgs)
    assert out == [{"role": "system", "content": "S"}, {"role": "user", "content": "a\n\nb"}]
    assert msgs == before


def test_user_parts_merge_into_one_parts_list():
    img = {"type": "image_url", "image_url": {"url": "data:x"}}
    out = C.normalize_roles([{"role": "user", "content": "hi"}, {"role": "user", "content": [img]}])
    assert out == [{"role": "user", "content": [{"type": "text", "text": "hi"}, img]}]


def test_non_leading_system_becomes_user_note_and_merges():
    out = C.normalize_roles([{"role": "system", "content": "S"}, {"role": "user", "content": "u"},
                             {"role": "system", "content": "later"}])
    assert [m["role"] for m in out] == ["system", "user"]
    assert out[1]["content"] == "u\n\n[System note] later"


def test_assistant_merge_keeps_tool_calls_and_empty_assistant_is_dropped():
    out = C.normalize_roles([{"role": "user", "content": "u"},
                             {"role": "assistant", "content": ""},
                             {"role": "assistant", "content": "x", "tool_calls": [call("a1")]},
                             {"role": "assistant", "content": None, "tool_calls": [call("a2")]},
                             {"role": "assistant", "content": "y"}])
    assert len(out) == 2
    assert out[1]["content"] == "x\n\ny"
    assert [c["id"] for c in out[1]["tool_calls"]] == ["a1", "a2"]


def test_tool_messages_never_merged_or_changed():
    msgs = [{"role": "user", "content": "u"}, asst(call("a1"), call("a2")), tool("a1", "r1"), tool("a2", "r2")]
    assert C.normalize_roles(msgs) == msgs


def test_strict_folds_system_and_inserts_continue():
    out = C.normalize_roles([{"role": "system", "content": "S"}, {"role": "user", "content": "q"}], strict=True)
    assert out == [{"role": "user", "content": "[System instructions]\nS\n\n[User]\nq"}]
    out = C.normalize_roles([{"role": "system", "content": "S"}, {"role": "assistant", "content": "hello"}],
                            strict=True)
    assert [m["role"] for m in out] == ["user", "assistant"]
    assert out[0]["content"].startswith("[System instructions]\nS") and out[0]["content"].endswith("Continue.")
    parts = C.normalize_roles([{"role": "system", "content": "S"},
                               {"role": "user", "content": [{"type": "text", "text": "q"}]}], strict=True)
    assert parts[0]["content"][0]["text"].startswith("[System instructions]\nS")
    assert parts[0]["content"][1] == {"type": "text", "text": "q"}


def test_normalize_roles_is_idempotent():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"},
            {"role": "assistant", "content": " "}, {"role": "system", "content": "n"},
            {"role": "assistant", "content": "z"}, {"role": "assistant", "content": "w"}]
    for strict in (False, True):
        once = C.normalize_roles(msgs, strict)
        assert C.normalize_roles(once, strict) == once


def test_normalize_roles_prefix_is_stable_when_history_grows():
    h = [{"role": "system", "content": "S"}, {"role": "user", "content": "a"},
         {"role": "user", "content": "b"}, {"role": "system", "content": "n"}]
    a = {"role": "assistant", "content": "reply"}
    n1, n2 = C.normalize_roles(h), C.normalize_roles(h + [a])
    assert n2[:len(n1)] == n1 and n2[-1] == a
    s1, s2 = C.normalize_roles(h, True), C.normalize_roles(h + [a], True)
    assert s2[:len(s1)] == s1


# ------------------------------------------------------------------ fix_tool_ids

def test_repeated_call_0_becomes_unique_and_results_follow():
    msgs = [{"role": "user", "content": "u"},
            asst(call("call_0")), tool("call_0", "r1"),
            asst(call("call_0")), tool("call_0", "r2")]
    out = C.fix_tool_ids(msgs)
    ids = [c["id"] for m in out if m.get("tool_calls") for c in m["tool_calls"]]
    assert len(set(ids)) == 2 and ids[0] == "call_0"
    assert [m["tool_call_id"] for m in out if m["role"] == "tool"] == ids
    assert [m["content"] for m in out if m["role"] == "tool"] == ["r1", "r2"]


def test_toolu_id_with_alnum9_style_becomes_nine_alnum():
    msgs = [asst(call("toolu_01ABCdef")), tool("toolu_01ABCdef"), asst(call("call_xyz")), tool("call_xyz")]
    out = C.fix_tool_ids(msgs, "alnum9")
    ids = [c["id"] for m in out if m.get("tool_calls") for c in m["tool_calls"]]
    assert all(re.fullmatch(r"[A-Za-z0-9]{9}", i) for i in ids) and len(set(ids)) == 2
    assert [m["tool_call_id"] for m in out if m["role"] == "tool"] == ids


def test_dotted_colon_and_long_ids_are_rewritten():
    long_id = "x" * 64
    msgs = [asst(call("fc.1:2")), tool("fc.1:2"), asst(call(long_id)), tool(long_id)]
    out = C.fix_tool_ids(msgs)
    for m in out:
        for i in [c["id"] for c in m.get("tool_calls") or []] + ([m["tool_call_id"]] if m["role"] == "tool" else []):
            assert re.fullmatch(r"[A-Za-z0-9_-]{1,40}", i)
    assert out[1]["tool_call_id"] == out[0]["tool_calls"][0]["id"]
    assert out[3]["tool_call_id"] == out[2]["tool_calls"][0]["id"]


def test_orphan_tool_result_becomes_user_text():
    out = C.fix_tool_ids([{"role": "user", "content": "u"}, tool("ghost", "data")])
    assert out[1] == {"role": "user", "content": "[Tool result] data"}


def test_missing_result_is_stubbed_after_the_turns_results():
    msgs = [asst(call("a1"), call("a2")), tool("a1", "r1"), {"role": "user", "content": "next"}]
    out = C.fix_tool_ids(msgs)
    assert [m["role"] for m in out] == ["assistant", "tool", "tool", "user"]
    assert out[2]["tool_call_id"] == "a2" and "interrupted" in out[2]["content"]


def test_trailing_pending_calls_are_left_alone():
    msgs = [{"role": "user", "content": "u"}, asst(call("a1"))]
    assert C.fix_tool_ids(msgs) == msgs


def test_fix_tool_ids_is_deterministic_and_does_not_mutate():
    msgs = [asst(call("call_0")), tool("call_0"), asst(call("call_0")), tool("call_0")]
    before = copy.deepcopy(msgs)
    a, b = C.fix_tool_ids(msgs), C.fix_tool_ids(msgs)
    assert J(a) == J(b) and msgs == before


def test_valid_ids_stay_byte_identical():
    msgs = [{"role": "user", "content": "u"}, asst(call("call_a1"), call("call_a2")), tool("call_a1"),
            tool("call_a2"), {"role": "assistant", "content": "done"}]
    assert json.dumps(C.fix_tool_ids(msgs)) == json.dumps(msgs)


# ------------------------------------------------------------------ sanitize_schemas

NASTY = {
    "$schema": "http://json-schema.org/draft-07/schema#", "$id": "x", "type": "object",
    "$defs": {
        "Addr": {"type": "object", "properties": {"city": {"type": "string", "format": "uri"},
                                                  "zip": {"type": ["string", "null"]}}, "required": ["city", "gone"]},
        "Loop": {"type": "object", "properties": {"next": {"$ref": "#/$defs/Loop"}}},
    },
    "definitions": {"Color": {"enum": ["red", 1, True]}},
    "properties": {
        "addr": {"$ref": "#/$defs/Addr", "description": "where"},
        "loop": {"$ref": "#/$defs/Loop"},
        "color": {"$ref": "#/definitions/Color"},
        "kind": {"const": "fixed"},
        "when": {"type": "string", "format": "date-time"},
        "uri": {"type": "string", "format": "uri", "additionalProperties": False},
        "tags": {"type": "array"},
        "num": {"type": ["integer", "string"], "minimum": 1, "default": 3, "title": "T"},
        "either": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        "merged": {"allOf": [{"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
                             {"properties": {"b": {"type": "integer"}}}]},
        "map": {"type": "object", "additionalProperties": {"$ref": "#/$defs/Addr"}},
        "$comment": {"type": "string"},
    },
    "required": ["addr", "missing"],
    "additionalProperties": False,
}


def _tool(params):
    return {"type": "function", "function": {"name": "t", "description": "d", "parameters": params}}


def _find(node, pred, path=""):
    hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            if pred(k, v):
                hits.append(path + "/" + k)
            hits += _find(v, pred, path + "/" + k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits += _find(v, pred, path + f"[{i}]")
    return hits


def test_portable_sanitize_inlines_refs_and_prunes_required():
    before = copy.deepcopy(NASTY)
    p = C.sanitize_schemas([_tool(NASTY)])[0]["function"]["parameters"]
    assert NASTY == before
    assert not _find(p, lambda k, v: k in ("$ref", "$defs", "definitions", "$schema", "$id"))
    assert p["type"] == "object" and p["required"] == ["addr"]
    addr = p["properties"]["addr"]
    assert addr["description"] == "where" and addr["required"] == ["city"]
    assert addr["properties"]["zip"]["type"] == ["string", "null"]       # portable mode keeps type lists
    assert p["properties"]["loop"]["properties"]["next"] == {"type": "object"}  # cycle cut
    assert p["properties"]["map"]["additionalProperties"]["required"] == ["city"]


def test_cyclic_ref_terminates_with_plain_object():
    p = C.sanitize_schemas([_tool(NASTY)])[0]["function"]["parameters"]
    node, depth = p["properties"]["loop"], 0
    while "properties" in node:
        node, depth = node["properties"]["next"], depth + 1
    assert node == {"type": "object"} and depth <= 9


def test_missing_parameters_become_empty_object_schema():
    for t in ({"type": "function", "function": {"name": "n"}},
              {"type": "function", "function": {"name": "n", "parameters": None}}):
        assert C.sanitize_schemas([t])[0]["function"]["parameters"] == {"type": "object", "properties": {}}
    odd = C.sanitize_schemas([_tool({"type": "string"})])[0]["function"]["parameters"]
    assert odd == {"type": "object", "properties": {}}


ALLOWED = {"type", "nullable", "description", "enum", "properties", "required", "items", "anyOf",
           "minimum", "maximum", "minItems", "maxItems", "format"}


def _walk_subset(node):
    assert set(node) <= ALLOWED, set(node) - ALLOWED
    for v in (node.get("properties") or {}).values():
        _walk_subset(v)
    if isinstance(node.get("items"), dict):
        _walk_subset(node["items"])
    for v in node.get("anyOf") or []:
        _walk_subset(v)
    if "format" in node:
        assert node["type"] == "string" and node["format"] in ("enum", "date-time")
    if "enum" in node:
        assert node["type"] == "string" and all(isinstance(x, str) for x in node["enum"])
    if node.get("type") == "array":
        assert isinstance(node.get("items"), dict)
    assert not isinstance(node.get("type"), list)


def test_subset_sanitize_keeps_only_allowed_keys():
    before = copy.deepcopy(NASTY)
    p = C.sanitize_schemas([_tool(NASTY)], subset=True)[0]["function"]["parameters"]
    assert NASTY == before
    _walk_subset(p)
    props = p["properties"]
    assert props["kind"] == {"type": "string", "enum": ["fixed"]}
    assert props["color"]["enum"] == ["red", "1", "True"]
    assert props["addr"]["properties"]["zip"] == {"type": "string", "nullable": True}
    assert "format" not in props["addr"]["properties"]["city"]
    assert props["when"]["format"] == "date-time" and "format" not in props["uri"]
    assert props["tags"]["items"] == {"type": "string"}
    assert props["num"]["anyOf"] == [{"type": "integer"}, {"type": "string"}] and props["num"]["minimum"] == 1
    assert "default" not in props["num"] and "title" not in props["num"]
    assert props["either"]["anyOf"] == [{"type": "string"}, {"type": "integer"}] and "oneOf" not in props["either"]
    assert set(props["merged"]["properties"]) == {"a", "b"} and props["merged"]["required"] == ["a"]
    assert "additionalProperties" not in p and p["required"] == ["addr"]


def test_is_subset_host():
    assert C.is_subset_host("https://generativelanguage.googleapis.com/v1beta/openai")
    assert C.is_subset_host("https://us-central1-aiplatform.googleapis.com/v1")
    assert not C.is_subset_host("https://api.openai.com/v1") and not C.is_subset_host("")


# ------------------------------------------------------------------ cap_images

def img(n, size=3000):
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{n:04d}" + "A" * size}}


def img_msg(n):
    return {"role": "user", "content": [{"type": "text", "text": f"Image returned by screenshot {n}:"}, img(n)]}


def count_images(msgs):
    return sum(1 for m in msgs if isinstance(m.get("content"), list) for p in m["content"] if p.get("type") == "image_url")


def naive_cap(msgs, keep=4):
    out, seen = copy.deepcopy(msgs), 0
    for m in reversed(out):
        c = m.get("content")
        if not isinstance(c, list):
            continue
        new = []
        for p in reversed(c):
            if p.get("type") == "image_url":
                seen += 1
                if seen > keep:
                    continue
            new.append(p)
        new.reverse()
        if len(new) != len(c):
            m["content"] = new if any(p.get("type") == "image_url" for p in new) else \
                " ".join(p.get("text", "") for p in new if p.get("type") == "text") + " [screenshot removed to save space]"
    return out


def _history(n):
    msgs = [{"role": "system", "content": "S" * 500}, {"role": "user", "content": "start the task " * 20}]
    for i in range(n):
        msgs += [{"role": "assistant", "content": f"step {i} " * 30}, img_msg(i)]
    return msgs


def _common_prefix_fraction(a, b):
    x, y = json.dumps(a), json.dumps(b)
    n = 0
    for p, q in zip(x, y):
        if p != q:
            break
        n += 1
    return n / len(x)


def test_cap_images_unchanged_when_within_keep():
    msgs = _history(3)
    assert C.cap_images(msgs, keep=4) == msgs


def test_cap_images_cut_moves_in_steps_and_kept_count_in_range():
    seen_cuts = set()
    for n in range(5, 30):
        out = C.cap_images(_history(n), keep=4)
        k = count_images(out)
        assert 3 <= k <= 4
        seen_cuts.add(n - k)
    assert all(c % 2 == 0 for c in seen_cuts)


def test_cap_images_removed_message_becomes_string_with_note():
    out = C.cap_images(_history(8), keep=4)
    gone = [m for m in out if isinstance(m["content"], str) and m["content"].endswith("[screenshot removed to save space]")]
    assert gone and gone[0]["content"] == "Image returned by screenshot 0: [screenshot removed to save space]"


def test_cap_images_does_not_mutate_input():
    msgs = _history(9)
    before = copy.deepcopy(msgs)
    C.cap_images(msgs)
    assert msgs == before


def test_cap_images_prefix_stability_beats_naive_cap():
    stable, naive = [], []
    prev_s = prev_n = None
    for n in range(1, 21):
        h = _history(n)
        s, nv = C.cap_images(h, keep=4), naive_cap(h, 4)
        if prev_s is not None:
            stable.append(_common_prefix_fraction(prev_s, s))
            naive.append(_common_prefix_fraction(prev_n, nv))
        prev_s, prev_n = s, nv
    mean_s, mean_n = sum(stable) / len(stable), sum(naive) / len(naive)
    assert mean_s >= 0.45
    assert mean_s > mean_n


def test_cap_images_keeps_every_image_of_newest_user_message():
    msgs = _history(10)
    msgs.append({"role": "user", "content": [{"type": "text", "text": "compare these"}, img(100), img(101), img(102)]})
    out = C.cap_images(msgs, keep=4)
    assert count_images([out[-1]]) == 3
    assert out[-1] == msgs[-1]


def test_cap_images_newest_user_message_with_more_than_keep_keeps_its_newest():
    msgs = _history(6)
    msgs.append({"role": "user", "content": [{"type": "text", "text": "many"}] + [img(200 + i) for i in range(6)]})
    out = C.cap_images(msgs, keep=4)
    assert count_images(out) == 4
    assert [p["image_url"]["url"][22:26] for p in out[-1]["content"] if p["type"] == "image_url"] == \
        ["0202", "0203", "0204", "0205"]


# ------------------------------------------------------------------ strip_tools / text protocol

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a file.\nSecond line.",
                                      "parameters": {"type": "object", "properties": {
                                          "path": {"type": "string"}, "limit": {"type": "integer"}},
                                          "required": ["path"]}}},
    {"type": "function", "function": {"name": "ping", "description": "x" * 400}},
]


def _agent_body():
    return {"model": "m", "max_tokens": 100, "tools": TOOLS, "tool_choice": "auto", "parallel_tool_calls": True,
            "messages": [{"role": "system", "content": "You are an agent."},
                         {"role": "user", "content": "read it"},
                         asst(call("c1", args={"path": "é.txt"}), content="looking"),
                         tool("c1", "file body"),
                         {"role": "user", "content": "[Meshpool] note"},
                         {"role": "assistant", "content": "done"},
                         {"role": "user", "content": "thanks"}]}


def test_strip_tools_removes_native_tool_fields_and_alternates_roles():
    body = _agent_body()
    before = copy.deepcopy(body)
    out = C.strip_tools(body)
    assert body == before
    assert not {"tools", "tool_choice", "parallel_tool_calls"} & set(out)
    msgs = out["messages"]
    assert all(m["role"] in ("system", "user", "assistant") and "tool_calls" not in m for m in msgs)
    roles = [m["role"] for m in msgs[1:]]
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert '<tool_call>{"name": "read_file", "arguments": {"path": "é.txt"}}</tool_call>' in msgs[2]["content"]
    assert msgs[2]["content"].startswith("looking")
    assert msgs[3]["content"].startswith("[Result of read_file]\nfile body")
    sysm = msgs[0]["content"]
    assert sysm.startswith("You are an agent.\n\n") and "- read_file(path: string, limit?: integer): Read a file. Second line." in sysm
    assert "- ping(): " + "x" * 200 in sysm and "x" * 201 not in sysm


def test_strip_tools_inserts_system_when_missing_and_keeps_unparsable_args_raw():
    body = {"model": "m", "tools": TOOLS, "messages": [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "z", "type": "function",
                                                               "function": {"name": "ping", "arguments": "{bad"}}]}]}
    out = C.strip_tools(body)["messages"]
    assert out[0]["role"] == "system" and "read_file" in out[0]["content"]
    assert '"arguments": "{bad"' in out[2]["content"]


def test_text_tools_prompt_shows_protocol_example():
    p = C.text_tools_prompt(TOOLS)
    assert '<tool_call>{"name": "read_file", "arguments": {"path": "x"}}</tool_call>' in p
    assert "cannot call tools natively" in p and "wait for the result" in p


# ------------------------------------------------------------------ parse_text_tool_calls

def test_parse_plain_block():
    text, calls = C.parse_text_tool_calls('Sure.\n<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>')
    assert text == "Sure."
    assert len(calls) == 1 and calls[0]["type"] == "function"
    assert re.fullmatch(r"[A-Za-z0-9]{9}", calls[0]["id"])
    assert calls[0]["function"]["name"] == "read_file"
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "a"}


def test_parse_fenced_block_and_parameters_key():
    t = '<tool_call>\n```json\n{"name": "grep", "parameters": {"pattern": "x"}}\n```\n</tool_call>'
    text, calls = C.parse_text_tool_calls(t)
    assert text == "" and calls[0]["function"]["name"] == "grep"
    assert json.loads(calls[0]["function"]["arguments"]) == {"pattern": "x"}


def test_parse_malformed_block_stays_in_text():
    bad = '<tool_call>{"name": "x", "arguments": </tool_call>'
    text, calls = C.parse_text_tool_calls("before " + bad + " after")
    assert calls == [] and text == "before " + bad + " after"
    text, calls = C.parse_text_tool_calls('<tool_call>{"arguments": {}}</tool_call>')
    assert calls == [] and "<tool_call>" in text


def test_parse_multiple_blocks_have_distinct_deterministic_ids():
    t = ('a <tool_call>{"name": "ping", "arguments": {}}</tool_call> b '
         '<tool_call>{"name": "ping", "arguments": {}}</tool_call> c')
    text, calls = C.parse_text_tool_calls(t)
    assert text == "a  b  c" and len(calls) == 2
    assert calls[0]["id"] != calls[1]["id"]
    assert [c["id"] for c in C.parse_text_tool_calls(t)[1]] == [c["id"] for c in calls]


# ------------------------------------------------------------------ prepare

def _full_body():
    return {"model": "m", "max_tokens": 8192, "temperature": 0.2, "prompt_cache_key": "k", "user": "u",
            "tool_choice": "auto", "tools": [_tool(NASTY)],
            "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "a"},
                         {"role": "user", "content": "b"}, asst(call("call_0")), tool("call_0"),
                         asst(call("call_0")), tool("call_0")]}


def test_prepare_drops_refused_but_never_model_or_messages():
    out = C.prepare(_full_body(), {"refused": ["prompt_cache_key", "user", "model", "messages"]})
    assert "prompt_cache_key" not in out and "user" not in out
    assert out["model"] == "m" and out["messages"]


def test_prepare_renames_max_tokens_and_clamps():
    out = C.prepare(_full_body(), {"max_tokens_param": "max_completion_tokens", "max_output": 4096})
    assert "max_tokens" not in out and out["max_completion_tokens"] == 4096
    out = C.prepare(_full_body(), {"refused": ["max_tokens"]})
    assert out["max_completion_tokens"] == 8192
    assert C.prepare(_full_body(), {"max_output": 0})["max_tokens"] == 8192
    assert C.prepare(_full_body(), {"max_output": 100000})["max_tokens"] == 8192


def test_prepare_shapes_messages_tools_and_does_not_mutate():
    body = _full_body()
    before = copy.deepcopy(body)
    out = C.prepare(body, {"strict_roles": True, "tool_id_style": "alnum9", "schema_subset": True})
    assert body == before
    assert out["messages"][0] == {"role": "user", "content": "[System instructions]\nS\n\n[User]\na\n\nb"}
    ids = [c["id"] for m in out["messages"] if m.get("tool_calls") for c in m["tool_calls"]]
    assert len(set(ids)) == 2 and all(re.fullmatch(r"[A-Za-z0-9]{9}", i) for i in ids)
    _walk_subset(out["tools"][0]["function"]["parameters"])


def test_prepare_uses_subset_for_google_host():
    out = C.prepare(_full_body(), None, "https://generativelanguage.googleapis.com/v1beta/openai")
    _walk_subset(out["tools"][0]["function"]["parameters"])
    assert not C.is_subset_host("https://api.openai.com/v1")
    portable = C.prepare(_full_body(), None, "https://api.openai.com/v1")
    assert "additionalProperties" in portable["tools"][0]["function"]["parameters"]


def test_prepare_no_tools_strips_everything_native():
    out = C.prepare(_full_body(), {"no_tools": True})
    assert "tools" not in out and "tool_choice" not in out
    assert all(m["role"] != "tool" and "tool_calls" not in m for m in out["messages"])
    assert "<tool_call>" in json.dumps(out["messages"]) and "t(" in out["messages"][0]["content"]


def test_prepare_accepts_object_caps_and_defaults():
    caps = SimpleNamespace(refused=("user",), max_output=1000, max_tokens_param="max_completion_tokens",
                           no_tools=False, strict_roles=False, tool_id_style=None, schema_subset=False)
    out = C.prepare(_full_body(), caps)
    assert "user" not in out and out["max_completion_tokens"] == 1000
    assert C.prepare(_full_body())["max_tokens"] == 8192
    assert C.prepare({"model": "m", "messages": []}, {}) == {"model": "m", "messages": []}
