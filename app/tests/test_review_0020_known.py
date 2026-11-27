"""Independent review of 0.0.20, the known issues outside the web app: each test failed on the release
candidate (942007e5) and passes with its fix."""
from __future__ import annotations

import json

import pytest

from meshpool_harness import checks as K
from meshpool_harness.agent.core import _plain_command_title, approval_view


# ---------- known issues --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cmd,want", [
    ('echo hi > "my notes.txt"', "Write my notes.txt?"),
    ("printf x > 'my notes.txt'", "Write my notes.txt?"),
    ('echo a >> "/tmp/a b/c d.csv"', "Write c d.csv?"),
    ("echo hi > out.txt", "Write out.txt?"),
    ('cat > "my chart.py" <<EOF\nimport x\nEOF\npython "my chart.py"', "Write my chart.py and run it (a Python step)?"),
])
def test_a_quoted_output_name_with_a_space_is_named_in_full(cmd, want):
    assert _plain_command_title(cmd) == want
    if "<<" in cmd:
        assert approval_view("bash", "", 'cat > "my notes.txt" <<EOF\nhello\nEOF')["file"]["name"] == "my notes.txt"


def test_a_detail_from_a_pass_a_later_pass_fixed_within_the_second_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)
    K.begin("fast")
    rows = [{"ts": 100.0, "kind": "deck_rules", "label": "design rules", "count": 27, "ok": True, "subject": "d.pptx"},
            {"ts": 100.0, "kind": "deck_rules", "label": "design rules", "count": 1, "ok": False, "subject": "d.pptx"},
            {"ts": 100.1, "kind": "detail", "label": "deck_rules", "count": None, "ok": True,
             "subject": json.dumps({"kind": "deck_rules", "subject": "d.pptx", "lines": ["Slide 4 has an empty box"]})},
            {"ts": 100.6, "kind": "deck_rules", "label": "design rules", "count": 28, "ok": True, "subject": "d.pptx"}]
    K._file("fast").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert K._latest_details("fast") == []
    rows.append({"ts": 100.6, "kind": "detail", "label": "deck_rules", "count": None, "ok": True,
                 "subject": json.dumps({"kind": "deck_rules", "subject": "d.pptx", "lines": ["The cover is small"]})})
    K._file("fast").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert [json.loads(r["subject"])["lines"] for r in K._latest_details("fast")] == [["The cover is small"]]
    K.end("fast")
