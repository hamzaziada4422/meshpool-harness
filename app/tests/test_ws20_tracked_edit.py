"""edit_office_file makes Word tracked changes the way an agent asks for them (K-01: Meshpool CLI got an untracked
edit from replace_text{tracked: true} and the op list it was shown hid tracked_replace)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from testkit.bench3 import tasks as T  # noqa: E402

from meshpool_harness.agent import office_mcp_server as O


def _contract(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    T.get("docx_tracked")[0].setup(tmp_path)
    return tmp_path


def _grade(w):
    return T.get("docx_tracked")[0].grade(w, "")


def test_replace_text_with_tracked_flag_makes_tracked_changes_everywhere(tmp_path, monkeypatch):
    w = _contract(tmp_path, monkeypatch)
    msg, err = O.edit_office_file("contract.docx", [
        {"op": "replace_text", "find": "Acme Corp", "replace": "Globex Inc", "tracked": True},
        {"op": "replace_text", "find": "4,000", "replace": "4,500", "tracked": True}], in_place=True)
    assert not err, msg
    r = _grade(w)
    assert r["ok"], r["detail"]                      # tracked, every place, fee still italic


def test_tracked_replace_covers_every_place(tmp_path, monkeypatch):
    w = _contract(tmp_path, monkeypatch)
    msg, err = O.edit_office_file("contract.docx", [
        {"op": "tracked_replace", "find": "Acme Corp", "replace": "Globex Inc"},
        {"op": "tracked_replace", "find": "4,000", "replace": "4,500"}], in_place=True)
    assert not err and "3 place(s)" in msg, msg
    assert _grade(w)["ok"]


def test_unknown_word_op_lists_tracked_ops(tmp_path, monkeypatch):
    _contract(tmp_path, monkeypatch)
    msg, err = O.edit_office_file("contract.docx", [{"op": "list_ops"}])
    assert err and "tracked_replace" in msg


def test_revision_ids_are_unique(tmp_path, monkeypatch):
    import re
    import zipfile
    w = _contract(tmp_path, monkeypatch)
    O.edit_office_file("contract.docx", [{"op": "tracked_replace", "find": "Acme Corp", "replace": "Globex Inc"}],
                       in_place=True)
    xml = zipfile.ZipFile(w / "contract.docx").read("word/document.xml").decode()
    ids = re.findall(r'<w:(?:ins|del) w:id="(\d+)"', xml)
    assert len(ids) == 6 and len(set(ids)) == 6


def test_description_names_tracked_replace():
    assert "tracked_replace" in O.TOOLS["edit_office_file"][1]
