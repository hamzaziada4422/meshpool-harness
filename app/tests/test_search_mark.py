"""Release review nit: search snippets were marked on escaped HTML, so a search for "amp" or "lt" marked inside
"&amp;" and "&lt;" and garbled the snippet. The marks go on the text itself now."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_wf_ui018 import app, page  # noqa: E402,F401  (the real app and a browser page)


def test_search_marks_words_in_the_text_not_in_entities(page):
    got = page.evaluate("""() => [srchMark("a < b & c", "amp lt"), srchMark("Sample & lt text", "amp lt"),
                                  srchMark("<b>bold</b> & more", "bold"), srchMark("Q3 & Q4", "")]""")
    assert got[0] == "a &lt; b &amp; c", got[0]
    assert got[1] == "S<mark>amp</mark>le &amp; <mark>lt</mark> text", got[1]
    assert got[2] == "&lt;b&gt;<mark>bold</mark>&lt;/b&gt; &amp; more", got[2]
    assert got[3] == "Q3 &amp; Q4", got[3]
    assert page.errors == [], page.errors
