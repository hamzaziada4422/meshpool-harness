"""Core tool arguments of type object (release 0.0.20, the tool health check): save_tool's `files` and `tests` are
objects, and validate() knew only string, integer, number, boolean and array, so every save_tool call a model made
ended in KeyError: 'object' instead of running."""
import json

import pytest

from meshpool_harness.agent import core  # noqa: F401  registers get_capability and save_tool
from meshpool_harness.core import tools as T
from meshpool_harness.core.tools import ToolError

ARGS = {"name": "greet", "kind": "script", "description": "Greets someone", "files": {"SKILL.md": "x"},
        "tests": {"tests/test_greet.py": "def test_x():\n    pass\n"}}


def test_save_tool_arguments_pass_validation():
    got = T.validate("save_tool", json.dumps(ARGS), ["save_tool"])
    assert got["files"] == {"SKILL.md": "x"}


def test_an_object_sent_as_json_text_is_read_and_a_wrong_type_is_named():
    got = T.validate("save_tool", json.dumps({**ARGS, "files": json.dumps({"SKILL.md": "x"})}), ["save_tool"])
    assert got["files"] == {"SKILL.md": "x"}
    with pytest.raises(ToolError, match="`files` must be an object, but it was a list"):
        T.validate("save_tool", json.dumps({**ARGS, "files": ["SKILL.md"]}), ["save_tool"])


def test_every_core_tool_declares_only_types_validate_knows():
    known = {"string", "integer", "number", "boolean", "array", "object"}
    bad = [(n, k, p.get("type")) for n, s in T.SCHEMAS.items()
           for k, p in ((s.get("function") or s).get("parameters", {}).get("properties") or {}).items()
           if p.get("type") not in known]
    assert not bad, bad
