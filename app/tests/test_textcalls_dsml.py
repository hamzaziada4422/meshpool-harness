"""DeepSeek's native DSML tool-call markup, passed through as text by some gateways, becomes real tool calls."""
from meshpool_harness.core.textcalls import recover

T = ('Checking.\n\n<｜DSML｜ calls>\n<｜DSML｜ invoke name="bash">\n'
     '<｜DSML｜ parameter name="command" string="true">cd /x && echo "hi"</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n'
     '<｜DSML｜ invoke name="read_file">\n<｜DSML｜ parameter name="path" string="true">a.py</｜DSML｜ parameter>\n'
     '<｜DSML｜ parameter name="start" string="false">3</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>')


def test_dsml_calls_are_recovered():
    m = {"content": T}
    assert recover(m, ["bash", "read_file"])
    import json
    got = [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in m["tool_calls"]]
    assert got == [("bash", {"command": 'cd /x && echo "hi"'}), ("read_file", {"path": "a.py", "start": 3})]
    assert m["content"] == "Checking."


def test_dsml_with_an_unknown_tool_is_left_alone():
    m = {"content": T}
    assert not recover(m, ["bash"]) and "tool_calls" not in m


def test_ascii_bar_variant():
    m = {"content": T.replace("｜", "|")}
    assert recover(m, ["bash", "read_file"]) and len(m["tool_calls"]) == 2
