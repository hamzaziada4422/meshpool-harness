"""Tool calls written as text in `content` are recovered (H-03 / S-10); anything else stays text."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.core.textcalls import recover  # noqa: E402

NAMES = {"read_file", "bash", "grep"}


def run(content, names=NAMES):
    msg = {"role": "assistant", "content": content}
    return recover(msg, names), msg


def calls(msg):
    return [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in msg["tool_calls"]]


def test_tool_call_tag():
    ok, m = run('<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>')
    assert ok and calls(m) == [("bash", {"command": "ls"})] and m["content"] is None
    c = m["tool_calls"][0]
    assert c["type"] == "function" and c["id"].startswith("call_txt_")


def test_tool_call_tag_missing_close_and_multiple():
    ok, m = run('<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>\n'
                '<tool_call>{"name": "grep", "arguments": {"pattern": "x"}}')
    assert ok and calls(m) == [("bash", {"command": "ls"}), ("grep", {"pattern": "x"})]
    assert len({c["id"] for c in m["tool_calls"]}) == 2


def test_function_tag_json_body():
    ok, m = run('<function=read_file>{"path": "a.txt"}</function>')
    assert ok and calls(m) == [("read_file", {"path": "a.txt"})]


def test_function_tag_parameters_inside_tool_call():
    ok, m = run('<tool_call>\n<function=bash>\n<parameter=command>\nls -la\n</parameter>\n'
                '<parameter=timeout>30</parameter>\n</function>\n</tool_call>')
    assert ok and calls(m) == [("bash", {"command": "ls -la", "timeout": 30})]
    assert m["content"] is None


def test_python_tag_multiple():
    ok, m = run('<|python_tag|>{"name": "bash", "parameters": {"command": "ls"}}; '
                '{"name": "grep", "parameters": {"pattern": "y"}}')
    assert ok and calls(m) == [("bash", {"command": "ls"}), ("grep", {"pattern": "y"})]


def test_mistral_tool_calls_array_and_args_form():
    ok, m = run('[TOOL_CALLS][{"name": "bash", "arguments": {"command": "pwd"}}, '
                '{"name": "grep", "arguments": {"pattern": "z"}}]')
    assert ok and calls(m) == [("bash", {"command": "pwd"}), ("grep", {"pattern": "z"})]
    ok, m = run('[TOOL_CALLS]read_file[ARGS]{"path": "b"}')
    assert ok and calls(m) == [("read_file", {"path": "b"})]


def test_whole_content_json_variants():
    for text, want in [
        ('{"name": "bash", "arguments": {"command": "ls"}}', ("bash", {"command": "ls"})),
        ('{"tool": "bash", "input": {"command": "ls"}}', ("bash", {"command": "ls"})),
        ('{"tool_name": "bash", "args": {"command": "ls"}}', ("bash", {"command": "ls"})),
        ('{"function": "bash", "parameters": {"command": "ls"}}', ("bash", {"command": "ls"})),
        ('{"type": "function", "function": {"name": "bash", "arguments": "{\\"command\\": \\"ls\\"}"}}',
         ("bash", {"command": "ls"})),
        ('{"tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}]}', ("bash", {"command": "ls"})),
        ('[{"name": "bash"}]', ("bash", {})),
    ]:
        ok, m = run(text)
        assert ok and calls(m) == [want], text


def test_arguments_as_string():
    ok, m = run('{"name": "bash", "arguments": "{\\"command\\": \\"ls\\"}"}')
    assert ok and calls(m) == [("bash", {"command": "ls"})]


def test_fenced_json():
    ok, m = run('```json\n{"name": "bash", "arguments": {"command": "ls"}}\n```')
    assert ok and calls(m) == [("bash", {"command": "ls"})] and m["content"] is None
    ok, m = run('```\n{"name": "bash", "arguments": {}}\n```')
    assert ok


def test_prose_kept_around_tagged_calls():
    ok, m = run('I will list the files.\n<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>\nDone.')
    assert ok and m["content"] == "I will list the files.\n\nDone."


def test_unknown_name_stays_text():
    for text in ['<tool_call>{"name": "rm_rf", "arguments": {}}</tool_call>',
                 '<function=nope>{"a": 1}</function>',
                 '<|python_tag|>{"name": "nope", "parameters": {}}',
                 '[TOOL_CALLS][{"name": "nope", "arguments": {}}]',
                 '{"name": "nope", "arguments": {}}',
                 '<tool_call>{"name": "bash", "arguments": {}}</tool_call><tool_call>{"name": "nope"}</tool_call>']:
        ok, m = run(text)
        assert not ok and m == {"role": "assistant", "content": text}, text


def test_json_answer_that_is_not_a_call_stays_text():
    for text in ['{"answer": 42}', '[1, 2, 3]', '{"name": "Alice", "age": 3}', '"bash"', '{"name": "bash", "arguments": [1]}']:
        ok, m = run(text)
        assert not ok and "tool_calls" not in m and m["content"] == text, text


def test_malformed_json_stays_text():
    for text in ['<tool_call>{"name": "bash", "arguments": {</tool_call>', '{"name": "bash", "arguments": {"c"',
                 '[TOOL_CALLS][{"name": "bash"', '<|python_tag|>{oops}']:
        ok, m = run(text)
        assert not ok and m["content"] == text, text


def test_message_with_tool_calls_untouched():
    msg = {"role": "assistant", "content": '<tool_call>{"name": "bash", "arguments": {}}</tool_call>',
           "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]}
    before = json.dumps(msg)
    assert recover(msg, NAMES) is False and json.dumps(msg) == before


def test_think_text_is_ignored():
    ok, m = run('<think>I could call <tool_call>{"name": "bash", "arguments": {}}</tool_call></think>Final answer.')
    assert not ok
    ok, m = run('<think>hmm</think><tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>')
    assert ok and calls(m) == [("bash", {"command": "ls"})]


@pytest.mark.parametrize("content", [None, "", 5, ["x"], "<tool_call>", "<function=bash>", "[TOOL_CALLS]", "<|python_tag|>",
                                     "<function=bash><parameter=", "\x00{", "{" * 5000, '{"name":' * 500])
def test_never_raises(content):
    assert recover({"role": "assistant", "content": content}, NAMES) is False
    assert recover({"role": "assistant", "content": content}, None) is False
    assert recover({"role": "assistant", "content": content}, []) is False


def test_non_dict_message():
    assert recover(None, NAMES) is False
    assert recover("x", NAMES) is False
