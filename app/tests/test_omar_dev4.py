"""Omar (developer persona) fixes, part 4: a JSON tool call as the reply."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.core import textcalls as TC  # noqa: E402

_CALL = ('{"content": "", "tool_calls": [{"name": "exec_command", "arguments": {"cmd": "git add a.py && git commit -m '
         "'x' -m 'Co-Authored-By: A\nClaude-Session: s'\"}}]}")
OFFERED = ["exec_command", "read_file"]


def test_8_a_reply_that_is_only_a_json_call_runs_as_a_call():
    msg = {"role": "assistant", "content": '{"name": "read_file", "arguments": {"path": "a.py"}}'}
    assert TC.recover(msg, OFFERED)
    assert msg["tool_calls"][0]["function"]["name"] == "read_file" and not msg["content"]


def test_8_words_then_a_json_call_with_a_raw_newline_inside_runs_and_keeps_the_words():
    text = "I'll commit only the currency files.\n\n<invoke_placeholder>\n\n" + _CALL
    msg = {"role": "assistant", "content": text}
    assert TC.recover(msg, OFFERED)
    c = msg["tool_calls"][0]["function"]
    assert c["name"] == "exec_command" and "Claude-Session: s" in json.loads(c["arguments"])["cmd"]
    assert msg["content"] == "I'll commit only the currency files."


def test_8_a_tool_that_is_not_offered_or_json_in_the_middle_is_left_alone():
    for text in ('{"name": "rm_everything", "arguments": {}}',
                 'Use {"name": "read_file", "arguments": {"path": "a"}} like this, then say done.'):
        msg = {"role": "assistant", "content": text}
        assert not TC.recover(msg, OFFERED) and msg["content"] == text


def test_8_with_tools_off_the_json_call_is_never_printed_as_the_answer():
    assert TC.strip_markup(_CALL) == ""
    assert TC.strip_markup("All done.\n" + _CALL) == "All done."
    assert TC.strip_markup("Plain answer.") == "Plain answer."


# ---------------------------------------------------------------------------------------- 9: the Changes tab strip
import pytest  # noqa: E402

pytest.importorskip("playwright.sync_api")
from tests.test_ui_persona019 import _chat  # noqa: E402
from tests.test_ux018 import app, page, sync_api  # noqa: E402,F401


@pytest.mark.parametrize("width", [1024, 1366])
def test_9_every_side_panel_tab_is_reachable_and_the_clipped_ones_show_an_arrow(app, page, width):
    page.set_viewport_size({"width": width, "height": 800})
    _chat(page, app["work"], "code", "tabs")
    page.evaluate("() => { togglePanel('diff', true); }")
    page.wait_for_selector("#ptabs .pstrip button")
    page.wait_for_timeout(300)
    tabs = page.evaluate("() => [...document.querySelectorAll('#ptabs .pstrip button')].map(b => b.textContent)")
    assert "Git and CI" in tabs
    st = "document.querySelector('#ptabs .pstrip')"
    clipped = page.evaluate(f"() => {st}.scrollWidth > {st}.clientWidth + 1")
    seen = set()
    for _ in range(8):                                   # walk the strip with the arrow alone, as a mouse user would
        seen.update(page.evaluate(f"""() => [...document.querySelectorAll('#ptabs .pstrip button')].filter(b => {{
            const r = b.getBoundingClientRect(), s = {st}.getBoundingClientRect(); return r.left >= s.left - 1 && r.right <= s.right + 1; }}).map(b => b.textContent)"""))
        if page.evaluate("() => document.querySelector('#pnavr').hidden"):
            break
        assert page.evaluate("() => getComputedStyle(document.querySelector('#pnavr')).display") != "none"
        page.click("#pnavr")
        page.wait_for_timeout(450)
    assert seen == set(tabs), (clipped, set(tabs) - seen)
    if clipped:
        assert page.evaluate("() => !document.querySelector('#pnavl').hidden")     # and back again
