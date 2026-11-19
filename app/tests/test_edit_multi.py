import pytest

from meshpool_harness.core.tools import SCHEMAS, ToolError, Workspace


def _ws(tmp_path, files, read=True):
    ws = Workspace(str(tmp_path), str(tmp_path / "out"))
    for n, c in files.items():
        (tmp_path / n).write_text(c)
        if read:
            ws.read_file(n)
    return ws


def test_several_edits_one_file(tmp_path):
    ws = _ws(tmp_path, {"a.py": "a = 1\nb = 2\nc = 3\n"})
    r = ws.edit("a.py", edits=[{"old": "a = 1", "new": "a = 10"}, {"old": "c = 3", "new": "c = 30"}])
    assert "2 edits applied" in r
    assert (tmp_path / "a.py").read_text() == "a = 10\nb = 2\nc = 30\n"
    assert ws.edits == 1


def test_edits_apply_in_order_on_updated_text(tmp_path):
    ws = _ws(tmp_path, {"a.py": "a = 1\n"})
    ws.edit("a.py", edits=[{"old": "a = 1", "new": "a = 2"}, {"old": "a = 2", "new": "a = 3"}])
    assert (tmp_path / "a.py").read_text() == "a = 3\n"


def test_bad_item_leaves_file_untouched(tmp_path):
    src = "a = 1\nb = 2\n"
    ws = _ws(tmp_path, {"a.py": src})
    with pytest.raises(ToolError) as e:
        ws.edit("a.py", edits=[{"old": "a = 1", "new": "a = 9"}, {"old": "b = 22", "new": "b = 3"}])
    assert "edit 2 of 2" in str(e.value) and "not found" in str(e.value) and "Closest" in str(e.value)
    assert (tmp_path / "a.py").read_text() == src
    assert ws.edits == 0


def test_ambiguous_item_after_previous_edit(tmp_path):
    ws = _ws(tmp_path, {"a.py": "x = 1\ny = 2\n"})
    with pytest.raises(ToolError, match="edit 2 of 2.*occurs 2 times"):
        ws.edit("a.py", edits=[{"old": "y = 2", "new": "x = 1"}, {"old": "x = 1", "new": "x = 5"}])
    assert (tmp_path / "a.py").read_text() == "x = 1\ny = 2\n"


def test_syntax_check_on_final_text_only(tmp_path):
    ws = _ws(tmp_path, {"a.py": "def f():\n    return 1\n"})
    with pytest.raises(ToolError, match="stop parsing"):
        ws.edit("a.py", edits=[{"old": "return 1", "new": "return (1"}])
    assert (tmp_path / "a.py").read_text() == "def f():\n    return 1\n"
    # intermediate state is broken, final state parses: allowed
    ws.edit("a.py", edits=[{"old": "return 1", "new": "return (1"}, {"old": "return (1", "new": "return 2"}])
    assert "return 2" in (tmp_path / "a.py").read_text()


def test_multi_file_changes(tmp_path):
    ws = _ws(tmp_path, {"a.py": "a = 1\n", "b.py": "b = 1\nc = 1\n"})
    r = ws.edit(changes=[{"path": "a.py", "old": "a = 1", "new": "a = 2"},
                         {"path": "b.py", "edits": [{"old": "b = 1", "new": "b = 2"}, {"old": "c = 1", "new": "c = 2"}]}])
    assert "2 files" in r and "3 edits applied" in r
    assert (tmp_path / "a.py").read_text() == "a = 2\n"
    assert (tmp_path / "b.py").read_text() == "b = 2\nc = 2\n"
    assert ws.edits == 2 and len(ws.written) == 2


def test_multi_file_failure_writes_nothing(tmp_path):
    ws = _ws(tmp_path, {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    with pytest.raises(ToolError, match="not found"):
        ws.edit(changes=[{"path": "a.py", "old": "a = 1", "new": "a = 2"},
                         {"path": "b.py", "old": "zzz", "new": "b = 2"}])
    assert (tmp_path / "a.py").read_text() == "a = 1\n"
    assert (tmp_path / "b.py").read_text() == "b = 1\n"
    assert ws.edits == 0


def test_multi_file_write_failure_rolls_back(tmp_path, monkeypatch):
    ws = _ws(tmp_path, {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    from pathlib import Path
    real = Path.write_text

    def flaky(self, data, *a, **k):
        if self.name == "b.py":
            raise OSError("disk full")
        return real(self, data, *a, **k)
    monkeypatch.setattr(Path, "write_text", flaky)
    with pytest.raises(ToolError, match="restored"):
        ws.edit(changes=[{"path": "a.py", "old": "a = 1", "new": "a = 2"},
                         {"path": "b.py", "old": "b = 1", "new": "b = 2"}])
    monkeypatch.undo()
    assert (tmp_path / "a.py").read_text() == "a = 1\n"
    assert (tmp_path / "b.py").read_text() == "b = 1\n"
    assert ws.edits == 0


def test_unread_file_allowed_when_old_matches_once(tmp_path):
    ws = _ws(tmp_path, {"a.py": "a = 1\nb = 1\nb = 1\n"}, read=False)
    ws.edit("a.py", "a = 1", "a = 2")
    assert (tmp_path / "a.py").read_text() == "a = 2\nb = 1\nb = 1\n"
    ws2 = _ws(tmp_path, {"c.py": "b = 1\nb = 1\n"}, read=False)
    with pytest.raises(ToolError, match="read c.py before editing"):
        ws2.edit("c.py", "b = 1", "b = 2")
    with pytest.raises(ToolError, match="read c.py before editing"):
        ws2.edit(changes=[{"path": "c.py", "old": "b = 1", "new": "b = 2"}])


def test_stale_file_still_refused(tmp_path):
    ws = _ws(tmp_path, {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    (tmp_path / "b.py").write_text("b = 1\n# changed\n")
    with pytest.raises(ToolError, match="changed since"):
        ws.edit(changes=[{"path": "a.py", "old": "a = 1", "new": "a = 2"},
                         {"path": "b.py", "old": "b = 1", "new": "b = 2"}])
    assert (tmp_path / "a.py").read_text() == "a = 1\n"


def test_schema_mentions_edits_and_changes():
    fn = SCHEMAS["edit"]["function"]
    assert "`edits`" in fn["description"] and "`changes`" in fn["description"]
    props = fn["parameters"]["properties"]
    assert {"path", "old", "new", "edits", "changes"} <= set(props)
    assert fn["parameters"]["required"] == []


def test_single_edit_unchanged(tmp_path):
    ws = _ws(tmp_path, {"a.py": "x = 1\n"})
    assert ws.edit("a.py", "x = 1", "x = 2") == "edited a.py"
    with pytest.raises(ToolError, match="identical"):
        ws.edit("a.py", "x = 2", "x = 2")
    with pytest.raises(ToolError, match="not found"):
        ws.edit("a.py", "nope", "x")
    with pytest.raises(ToolError, match="file not found"):
        ws.edit("zz.py", "a", "b")
    with pytest.raises(ToolError):
        ws.edit()


def test_approval_diff_shows_every_file_of_a_multi_edit(tmp_path):
    """Supervised mode approves an edit by its diff: a multi-file edit must show all of it, never an empty diff."""
    from meshpool_harness.agent.core import Agent, _summary_of
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    (tmp_path / "b.py").write_text("z = 3\n")
    a = Agent(str(tmp_path), mode="auto", sessions_dir=str(tmp_path / "_s"))
    args = {"changes": [{"path": "a.py", "edits": [{"old": "x = 1", "new": "x = 10"}, {"old": "y = 2", "new": "y = 20"}]},
                        {"path": "b.py", "old": "z = 3", "new": "z = 30"}]}
    d = a._diff_text("edit", args)
    assert "+x = 10" in d and "+y = 20" in d and "+z = 30" in d and "b/b.py" in d
    assert _summary_of("edit", args) == "a.py, b.py"
    d1 = a._diff_text("edit", {"path": "a.py", "edits": [{"old": "y = 2", "new": "y = 5"}]})
    assert "+y = 5" in d1
