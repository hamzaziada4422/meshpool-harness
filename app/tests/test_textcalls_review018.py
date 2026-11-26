"""Release review nits on textcalls: finding a JSON call at the end of a reply is linear, and a JSON example the user
asked for is not stripped as a call."""
import json
import time

from meshpool_harness.core import textcalls as TC

OFFERED = {"exec_command", "read_file"}


def test_a_long_reply_of_json_lines_is_read_in_linear_time():
    lines = "\n".join(json.dumps({"row": k, "v": "x" * 20}) for k in range(60_000))     # about 2 MB
    assert len(lines) > 1_800_000
    for text in (lines, lines + "\nDone.", "[\n" * 300_000, "{\n" * 300_000, '{"a\n' * 300_000):
        t0 = time.perf_counter()
        assert TC._trailing_json(text, OFFERED) is None
        assert time.perf_counter() - t0 < 1.0, text[:20]
    call = '{"name": "exec_command", "arguments": {"cmd": "ls"}}'
    t0 = time.perf_counter()
    got = TC._trailing_json(lines + "\nNow I list the folder.\n" + call, OFFERED)
    assert got and got[0] == [("exec_command", {"cmd": "ls"})] and got[1].endswith("Now I list the folder.")
    assert time.perf_counter() - t0 < 1.0


def test_a_call_nested_after_an_open_bracket_line_is_still_found():
    got = TC._trailing_json('Here:\n[\n{"name": "read_file", "arguments": {"path": "a"}}', OFFERED)
    assert got and got[0] == [("read_file", {"path": "a"})]


def test_a_json_example_the_user_asked_for_stays():
    fenced = 'Here is an example:\n```json\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n```'
    assert TC.strip_markup(fenced) == fenced
    example = 'The payload looks like this:\n{"id": 7, "arguments": ["a", "b"], "label": "x"}'
    assert TC.strip_markup(example) == example                 # no name: not shaped as a call
    assert TC.strip_markup('All done.\n{"name": "exec_command", "arguments": {"cmd": "ls"}}') == "All done."
    assert TC.strip_markup('{"tool_calls": [{"name": "x", "arguments": {}}]}') == ""
