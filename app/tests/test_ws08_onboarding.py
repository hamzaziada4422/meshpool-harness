"""F-18: onboarding no longer says tasks start in "Meshpool Projects"; a chat with no folder gets its own folder."""
import uuid
from pathlib import Path

import pytest

from meshpool_harness import config as C
from meshpool_harness import project_store as PS
from meshpool_harness.ui import agent_api as A

UI = Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui"


def test_onboarding_text_matches_the_app():
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "Tasks start in your Meshpool Projects folder" not in js
    assert "Start in Meshpool Projects" not in js
    assert "Start without a folder" in js
    assert js.count("Each chat keeps its files in its own folder; open it from the chat's Files panel.") == 2
    assert 'data-scope="later"' in js


class _Session:
    def __init__(self, workdir=None, mode=None, harness=None, kind="code", model=None, plan=False, **kw):
        self.id = uuid.uuid4().hex[:8]
        (A.SESSIONS / self.id).mkdir()
        self.workdir, self.kind, self.state, self.harness = workdir, kind, "idle", {"id": harness or "meshpool-work"}
        self.agent = type("G", (), {"title": "", "deleted": False})()
        self.closed = False

    def view(self, since):
        return {"id": self.id, "workdir": self.workdir}

    def emit(self, ev):
        pass

    def close(self):
        self.closed = True


def test_a_new_chat_without_a_folder_gets_its_own_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "userhome"))
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.SESSIONS.mkdir()
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "_METAS", {})
    monkeypatch.setattr(A, "_ROWS", {})
    monkeypatch.setattr(A, "Session", _Session)
    C.ensure_home()
    v = A.create({"harness": "meshpool-auto", "task": "hello"})
    try:
        wd = Path(v["workdir"]).resolve()
        assert wd.parent == PS.chats_root().resolve() and wd.is_dir()
        assert A.read_meta(v["id"])["scratch"] is True
        assert "Meshpool Projects" not in str(wd)
    finally:
        A.thread_action(v["id"], {"action": "delete"})
