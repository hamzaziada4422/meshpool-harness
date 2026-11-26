"""SR-08: an Office panel chat was titled by the panel's context block ("[I am in Excel, using the me...") instead of
the user's own words. The message here is what office/taskpane.js buildMessage sends."""
import re
from pathlib import Path

import pytest

GUIDE = ("When the answer is data or formulas for the sheet, give it as one markdown table (a formula cell starts with =) "
         "or, for a single formula, just the formula in a code block. The panel inserts it at the active cell.")
MSG = ("[I am in Excel, using the meshpool panel. Answer in this chat; do not create files unless I ask. " + GUIDE + "]"
       "\n\nSelected range Sheet1!B4:D10:\n<selection>\n412000\t418500\n54000\t54000\n</selection>"
       "\n\nWhat is the Q3 total, and is it right?")


def test_the_panel_still_sends_this_context_block():
    js = (Path(__file__).resolve().parents[1] / "meshpool_harness" / "office" / "taskpane.js").read_text()
    assert "[I am in ${host || \"Office\"}, using the meshpool panel. " in js


@pytest.mark.parametrize("msg,want", [
    (MSG, "Excel: What is the Q3 total, and is it right"),
    ("[I am in Word, using the meshpool panel. Answer in this chat.]\n\nNothing is selected; this is the whole "
     "document.\n\nShorten section 3", "Word: Shorten section 3"),
    ("[I am in PowerPoint, using the meshpool panel. No preamble.]\n\nMake a title slide", "PowerPoint: Make a title slide"),
])
def test_office_panel_chat_is_titled_by_the_users_words(tmp_path, msg, want):
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.ui.agent_api import nice_title
    from test_agent_robustness import _script
    a = Agent(str(tmp_path), mode="auto")
    _script(a, [{"role": "assistant", "content": "Done."}])
    a.send(msg)
    assert not a.title.startswith("[")
    assert nice_title(a.title) == want


def test_other_chats_keep_their_first_line(tmp_path):
    from meshpool_harness.agent.core import Agent
    from test_agent_robustness import _script
    a = Agent(str(tmp_path), mode="auto")
    _script(a, [{"role": "assistant", "content": "Done."}])
    a.send("[Draft] Fix the totals in Budget.xlsx")
    assert a.title == "[Draft] Fix the totals in Budget.xlsx"
