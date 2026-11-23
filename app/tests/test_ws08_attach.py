"""S-05: a file attached in a chat is saved at the chat folder's root, not under attachments/."""
from pathlib import Path

from meshpool_harness import project_store as PS
from meshpool_harness.ui import workbench as WB


def test_attach_saves_at_the_root_of_a_scratch_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    folder = PS.new_scratch()
    assert PS.is_scratch(folder)
    assert WB.attach(folder, "report.docx", b"x") == {"path": "report.docx"}
    assert (Path(folder) / "report.docx").read_bytes() == b"x"
    assert not (Path(folder) / "attachments").exists()
    assert WB.attach(folder, "report.docx", b"y") == {"path": "report (2).docx"}
    assert (Path(folder) / "report.docx").read_bytes() == b"x"
