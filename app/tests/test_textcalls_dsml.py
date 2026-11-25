"""DeepSeek DSML tool calls written as plain content (broad1: the grader's calls on deepseek-v4.1-flash came back as
`<｜DSML｜ invoke name="bash">` text, were lost, and the markup ended up in the answer)."""
import json

from meshpool_harness.core import textcalls as T

NAMES = {"bash", "apply_patch"}


def _calls(content):
    m = {"content": content}
    ok = T.recover(m, NAMES)
    return ok, [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in m.get("tool_calls") or []], \
        m["content"]


def test_v41_spaced_tags_with_typed_values():
    ok, calls, rest = _calls('<｜DSML｜ calls>\n<｜DSML｜ invoke name="bash">\n'
                             '<｜DSML｜ parameter name="command" string="true">ls -la</｜DSML｜ parameter>\n'
                             '<｜DSML｜ parameter name="timeout" string="false">30</｜DSML｜ parameter>\n'
                             '</｜DSML｜ invoke>\n</｜DSML｜ calls><｜end▁of▁sentence｜>')
    assert ok and calls == [("bash", {"command": "ls -la", "timeout": 30})] and rest is None


def test_v4_tags_two_calls_and_prose_kept():
    ok, calls, rest = _calls('Let me check.\n<｜DSML｜tool_calls>\n<｜DSML｜invoke name="bash">\n'
                             '<｜DSML｜parameter name="command" string="true">python -c "print(1<2)"</｜DSML｜parameter>\n'
                             '</｜DSML｜invoke>\n<｜DSML｜invoke name="bash">\n'
                             '<｜DSML｜parameter name="command" string="true">wc -l a.csv</｜DSML｜parameter>\n'
                             '</｜DSML｜invoke>\n</｜DSML｜tool_calls>')
    assert ok and [c[1]["command"] for c in calls] == ['python -c "print(1<2)"', "wc -l a.csv"]
    assert rest == "Let me check."


def test_ascii_pipes_and_a_cut_off_reply():
    assert _calls('<|DSML|tool_calls><|DSML|invoke name="bash"><|DSML|parameter name="command">echo hi'
                  '</|DSML|parameter></|DSML|invoke></|DSML|tool_calls>')[1] == [("bash", {"command": "echo hi"})]
    assert _calls('<｜DSML｜ calls> <｜DSML｜ invoke name="bash"> <｜DSML｜ parameter name="command" '
                  'string="true">ls')[1] == [("bash", {"command": "ls"})]


def test_unknown_tool_or_no_invoke_is_left_alone():
    for text in ('<｜DSML｜ calls><｜DSML｜ invoke name="rm_rf"><｜DSML｜ parameter name="x">1'
                 '</｜DSML｜ parameter></｜DSML｜ invoke></｜DSML｜ calls>',
                 "The DSML format is <｜DSML｜ calls> but I will not call anything."):
        ok, calls, rest = _calls(text)
        assert not ok and not calls and rest == text


def test_strip_markup_for_a_final_answer():
    text = ('The total is 5,075.98.\n<｜DSML｜ calls>\n<｜DSML｜ invoke name="bash">\n'
            '<｜DSML｜ parameter name="command" string="true">python check.py</｜DSML｜ parameter>\n'
            '</｜DSML｜ invoke>\n</｜DSML｜ calls><｜end▁of▁sentence｜>')
    assert T.strip_markup(text) == "The total is 5,075.98."
    assert T.strip_markup("plain answer") == "plain answer"


def test_agent_final_answer_with_tools_off_drops_the_markup(tmp_path):
    """Tools are off (a hard wrap): DSML calls in the reply cannot run, so the user gets only the prose."""
    from meshpool_harness.agent.core import Agent
    from test_agent_robustness import _script
    a = Agent(str(tmp_path), mode="auto")
    _script(a, [{"role": "assistant", "content": 'MRR is 5,075.98.\n<｜DSML｜ calls>\n<｜DSML｜ invoke name="bash">\n'
                 '<｜DSML｜ parameter name="command" string="true">python check.py</｜DSML｜ parameter>\n'
                 '</｜DSML｜ invoke>\n</｜DSML｜ calls>'}])
    a._no_tools = True
    assert a.send("go") == "MRR is 5,075.98."
