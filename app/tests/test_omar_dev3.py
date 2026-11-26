"""Omar (developer persona) fixes, part 3: the quoted apply_patch form, verification after reads, the slot cap,
mode names, a JSON tool call as the final text."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402

PATCH = ("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = \"two\"\n*** End Patch")


def _quoted(patch=PATCH):
    return 'apply_patch "' + patch.replace("\\", "\\\\").replace('"', '\\"') + '"'


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    return T.Workspace(tmp_path, tmp_path / ".run")


def test_3_the_quoted_form_is_a_patch_not_a_command():
    hit = T.patch_from_shell(_quoted())
    assert hit and hit[0] == PATCH and hit[1] is None
    single = "apply_patch '" + PATCH + "'"
    assert T.patch_from_shell(single)[0] == PATCH
    assert T.patch_from_shell("cd sub && " + _quoted())[1] == "sub"


def test_3_the_quoted_form_with_commands_after_it_splits():
    sp = T.split_patch_script(_quoted() + "\npython -m pytest -q")
    assert sp and sp[0] == PATCH and sp[2] == "python -m pytest -q" and sp[3] is False
    sp = T.split_patch_script(_quoted() + " && python -m pytest -q")
    assert sp and sp[2] == "python -m pytest -q" and sp[3] is True


def test_3_other_quoted_text_and_piped_forms_are_not_patches():
    assert T.patch_from_shell('apply_patch "not a patch"') is None
    assert T.patch_from_shell(_quoted() + " | tee out.txt") is None
    assert T.split_patch_script(_quoted() + " | tee out.txt") is None
    assert T.patch_from_shell('echo "*** Begin Patch"') is None


def test_3_exec_command_applies_the_quoted_patch(ws, tmp_path):
    out = ws.exec_command(_quoted())
    assert "command not found" not in out
    assert (tmp_path / "a.py").read_text() == 'x = "two"\n'


def test_3_the_step_row_reads_as_an_edit_not_the_whole_patch():
    row = core._patch_step(_quoted() + "\npython -m pytest -q")
    assert row.startswith("Edit a.py") and "Begin Patch" not in row and len(row) < 80
    assert core._patch_step(_quoted()) == "Edit a.py"
