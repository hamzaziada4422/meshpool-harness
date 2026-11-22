import json

import pytest

from meshpool_harness.core import tools as T


def _nb(tmp_path):
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "markdown", "metadata": {}, "source": ["# Title\n"]},
        {"cell_type": "code", "metadata": {}, "execution_count": 3, "outputs": [{"output_type": "stream"}], "source": ["print(1)\n"]}]}
    p = tmp_path / "a.ipynb"
    p.write_text(json.dumps(nb), encoding="utf-8")
    return p


def test_replace_insert_delete_cells(tmp_path):
    p = _nb(tmp_path)
    ws = T.Workspace(str(tmp_path), str(tmp_path / "out"))
    assert "replaced cell 1" in ws.notebook_edit("a.ipynb", 1, "print(2)\nprint(3)", "replace")
    nb = json.loads(p.read_text(encoding="utf-8"))
    assert nb["cells"][1]["source"] == ["print(2)\n", "print(3)"] and nb["cells"][1]["outputs"] == []
    ws.notebook_edit("a.ipynb", 2, "## Notes", "insert", "markdown")
    assert json.loads(p.read_text(encoding="utf-8"))["cells"][2]["cell_type"] == "markdown"
    ws.notebook_edit("a.ipynb", 0, mode="delete")
    assert len(json.loads(p.read_text(encoding="utf-8"))["cells"]) == 2
    with pytest.raises(T.ToolError, match="out of range"):
        ws.notebook_edit("a.ipynb", 9, "x", "replace")
    with pytest.raises(T.ToolError, match="not a notebook"):
        (tmp_path / "b.ipynb").write_text("{}", encoding="utf-8")
        ws.notebook_edit("b.ipynb", 0, "x")


def test_the_tool_is_offered_and_asks_like_an_edit():
    from meshpool_harness.agent.core import BUILTIN
    assert "notebook_edit" in BUILTIN and "notebook_edit" in T.SCHEMAS
