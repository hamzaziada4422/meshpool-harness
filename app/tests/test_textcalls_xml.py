"""PF-01: Claude-style tool calls written as text (`<invoke name="x"><parameter name="k">v</parameter></invoke>`, bare or
inside <function_calls> / <tool_calls>) were neither run nor removed, so the raw XML became the final answer."""
import json

from meshpool_harness.core import textcalls as T

NAMES = {"exec_command", "read_file"}


def _calls(content, names=NAMES):
    m = {"role": "assistant", "content": content}
    ok = T.recover(m, names)
    return ok, [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in m.get("tool_calls") or []], \
        m["content"]


def test_bare_invoke_becomes_a_call():
    ok, calls, rest = _calls('<invoke name="exec_command"><parameter name="cmd">ls</parameter></invoke>')
    assert ok and calls == [("exec_command", {"cmd": "ls"})] and rest is None


def test_function_calls_wrapper_two_calls_prose_kept():
    ok, calls, rest = _calls('Let me look.\n<function_calls>\n<invoke name="exec_command">\n'
                             '<parameter name="cmd">ls -la</parameter>\n<parameter name="timeout">30</parameter>\n'
                             '</invoke>\n<invoke name="read_file">\n<parameter name="path">notes.txt</parameter>\n'
                             '</invoke>\n</function_calls>')
    assert ok and calls == [("exec_command", {"cmd": "ls -la", "timeout": 30}), ("read_file", {"path": "notes.txt"})]
    assert rest == "Let me look."


def test_tool_calls_wrapper_prefixed_tags_and_multiline_value():
    ok, calls, rest = _calls('<x:tool_calls><x:invoke name="exec_command"><x:parameter name="cmd">\n'
                             'echo a\necho b\n</x:parameter></x:invoke></x:tool_calls>')
    assert ok and calls == [("exec_command", {"cmd": "echo a\necho b"})] and rest is None


def test_tool_not_offered_or_loose_text_is_left_alone():
    for text in ('<invoke name="rm_everything"><parameter name="x">1</parameter></invoke>',
                 '<invoke name="exec_command">just talking about it</invoke>'):
        ok, calls, rest = _calls(text)
        assert not ok and not calls and rest == text


def test_dsml_path_is_unchanged():
    text = ('<｜DSML｜ calls>\n<｜DSML｜ invoke name="exec_command">\n'
            '<｜DSML｜ parameter name="cmd" string="true">ls</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>')
    ok, calls, rest = _calls(text)
    assert ok and calls == [("exec_command", {"cmd": "ls"})] and rest is None


def test_strip_markup_removes_the_xml_form():
    text = ('The total is 5,075.98.\n<function_calls>\n<invoke name="exec_command">\n'
            '<parameter name="cmd">python check.py</parameter>\n</invoke>\n</function_calls>')
    assert T.strip_markup(text) == "The total is 5,075.98."
    assert T.strip_markup('Done.\n<invoke name="exec_command"><parameter name="cmd">ls</parameter></invoke>') == "Done."
    assert T.strip_markup("Use <b>bold</b> here.") == "Use <b>bold</b> here."


def test_agent_runs_an_xml_call_and_hides_it_when_tools_are_off(tmp_path):
    from meshpool_harness.agent.core import Agent
    from test_agent_robustness import _script
    a = Agent(str(tmp_path), mode="auto")
    (tmp_path / "a.txt").write_text("hello\n")
    _script(a, [{"role": "assistant", "content": '<function_calls><invoke name="read_file">'
                 '<parameter name="path">a.txt</parameter></invoke></function_calls>'},
                {"role": "assistant", "content": "It says hello."}])
    assert a.send("what is in a.txt") == "It says hello."
    assert any(m["role"] == "tool" and "hello" in str(m["content"]) for m in a.messages)
    b = Agent(str(tmp_path), mode="auto")
    _script(b, [{"role": "assistant", "content": 'MRR is 5,075.98.\n<function_calls><invoke name="read_file">'
                 '<parameter name="path">a.txt</parameter></invoke></function_calls>'}])
    b._no_tools = True
    assert b.send("go") == "MRR is 5,075.98."
