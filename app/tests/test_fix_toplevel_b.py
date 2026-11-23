"""Regression tests for top-level module fixes: cli, updater state, config, checkpoints, imports."""
import json
import os
import zipfile
from pathlib import Path

import pytest

from meshpool_harness import autoupdate as AU
from meshpool_harness import cli
from meshpool_harness import config as C


def test_files_flag_before_the_request(monkeypatch, capsys):
    seen = {}

    def fake(request, files, out, engine=None, workdir=None, output_format=None):
        seen.update(request=request, files=files)
        return {"ok": True}
    monkeypatch.setattr(cli, "run_request", fake)
    assert cli.main(["run", "--files", "a.csv", "make a chart"]) == 0
    assert seen == {"request": "make a chart", "files": ["a.csv"]}
    assert cli.main(["run", "make a chart", "--files", "a.csv", "b.csv"]) == 0
    assert seen == {"request": "make a chart", "files": ["a.csv", "b.csv"]}
    with pytest.raises(SystemExit):
        cli.main(["run"])


def test_import_errors_are_messages_not_tracebacks(tmp_path, capsys):
    assert cli.main(["import", str(tmp_path / "nope.zip")]) == 1
    assert "Could not import" in capsys.readouterr().out
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    assert cli.main(["import", str(bad)]) == 1
    notexport = tmp_path / "x.zip"
    with zipfile.ZipFile(notexport, "w") as z:
        z.writestr("a.txt", "x")
    assert cli.main(["import", str(notexport)]) in (0, 1)       # a message either way, never a traceback


def test_scheduler_usage_and_status(monkeypatch, capsys):
    from meshpool_harness import scheduler_os as SO
    monkeypatch.setattr(SO, "installed", lambda *a, **k: False)
    assert cli.main(["scheduler", "status"]) == 0
    assert "Not installed" in capsys.readouterr().out
    assert cli.main(["scheduler"]) == 0
    assert "Usage: meshpool scheduler" in capsys.readouterr().out


@pytest.fixture
def updates(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MESHPOOL_NO_UPDATE", raising=False)
    saved = dict(AU._ST)
    AU._ST.clear()
    AU._ST.update(state="idle")
    yield tmp_path
    AU._ST.clear()
    AU._ST.update(saved)


def test_failed_check_keeps_a_ready_update(updates, monkeypatch):
    AU._load()                                   # the state is persisted now: set it the way the app does
    AU._ST.update(state="ready", tag="v9")
    AU._persist()
    monkeypatch.setattr(AU.U, "check", lambda force=True, timeout=None: {"ok": False, "error": "offline"})
    AU.tick()
    assert AU._ST["state"] == "ready" and AU._ST["check_error"] == "offline"
    AU._ST["state"] = "idle"
    AU.tick()
    assert AU._ST["state"] == "idle" and AU._ST["check_error"] == "offline"     # a failed check never sets a state (M10-03)


def test_tick_does_not_consume_the_update_result(updates, monkeypatch):
    p = AU.udir() / "result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ok": True, "message": "Meshpool was updated."}), encoding="utf-8")
    monkeypatch.setattr(AU.U, "check", lambda force=True, timeout=None: {"ok": False, "error": "x"})
    assert AU.tick()["last_result"]["message"] == "Meshpool was updated."
    assert AU.status(consume=True)["last_result"]["message"] == "Meshpool was updated."   # the page still sees it
    assert AU.status(consume=True)["last_result"] is None                                   # and only once


def test_replace_file_keep_src(tmp_path, monkeypatch):
    src, dst = tmp_path / "a.part", tmp_path / "a.exe"
    src.write_text("x")
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr("time.sleep", lambda s: None)
    real = os.replace

    def locked(a, b):
        raise PermissionError("av")
    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(PermissionError):
        C.replace_file(src, dst, keep_src=True)
    assert src.exists()
    with pytest.raises(PermissionError):
        C.replace_file(src, dst)
    assert not src.exists()
    monkeypatch.setattr(os, "replace", real)


def test_scrub_env_removes_injected_connector_keys(monkeypatch):
    C._INJECTED.update({"PERPLEXITY_API_KEY", "MDB_MCP_CONNECTION_STRING", "SLACK_MCP_XOXP_TOKEN", "MH_MODEL"})
    env = {"PERPLEXITY_API_KEY": "k", "MDB_MCP_CONNECTION_STRING": "mongodb://u:p@h", "SLACK_MCP_XOXP_TOKEN": "t",
           "MH_MODEL": "m", "MY_OWN_TOKEN": "mine"}
    out = C.scrub_env(env)
    assert set(out) == {"MH_MODEL", "MY_OWN_TOKEN"}               # a key the user set themselves stays


def test_checkpoint_writes_are_atomic(tmp_path, monkeypatch):
    from meshpool_harness import checkpoints as CP
    root = tmp_path / "w"
    root.mkdir()
    (root / "f.txt").write_text("one")
    cp = CP.Checkpoints(str(root), tmp_path / "store")
    cp.begin_turn("t1")
    (root / "f.txt").write_text("two")
    cp.end_turn()
    assert len(cp.turns()) == 1
    leftovers = [p for p in (tmp_path / "store").rglob("*") if p.name.endswith(".tmp")]
    assert leftovers == []
    # a failed write leaves the old index intact
    before = cp.index_path.read_text()
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        cp._save_turns([])
    monkeypatch.undo()
    assert cp.index_path.read_text() == before


def test_context_import_uses_the_configured_model(monkeypatch):
    from meshpool_harness import importer
    src = Path(importer.__file__).read_text(encoding="utf-8")
    assert "gpt-6-luna" not in src


def test_password_pdf_message(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from meshpool_harness import docread
    w = pypdf.PdfWriter()
    w.add_blank_page(100, 100)
    w.encrypt("secret")
    p = tmp_path / "locked.pdf"
    with p.open("wb") as f:
        w.write(f)
    assert "password-protected" in docread._pdf(p)


def test_pdf_page_images_is_lazy(tmp_path):
    import inspect

    from meshpool_harness import ocr
    assert inspect.isgeneratorfunction(ocr.pdf_page_images)


def test_agent_import_memory_keys_use_the_path():
    src = Path(__import__("meshpool_harness.agent_import", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    assert 'f"memory:{aid}:{rel}:{i}"' in src
