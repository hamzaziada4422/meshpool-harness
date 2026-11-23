"""F-03: `meshpool work` prints and reports the answer and the files."""
import json
from types import SimpleNamespace

from meshpool_harness import cli


def _setup(tmp_path, monkeypatch):
    task = tmp_path / "task.md"
    task.write_text("# Title\nbody", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setenv("MESHPOOL_API_KEY", "dummy")
    monkeypatch.setattr("meshpool_harness.router.router.route", lambda *a, **k: SimpleNamespace(
        engines=["fake"], confidence=1, reason="x", used_model_call=False))

    class Stub:
        @staticmethod
        def solve(task_, workdir, task_id, client, **kw):
            return {"ok": True, "answer": "The answer is 42", "artifacts": [str(out / "r.xlsx")]}

    monkeypatch.setattr(cli, "ENGINES", cli.ENGINES + ("fake",))
    monkeypatch.setattr(cli, "_engine", lambda name: Stub)

    class FakeLive:
        def __init__(self, *a, **k):
            pass

        def spent(self):
            return 0.0

    monkeypatch.setattr("meshpool_harness.core.client.LiveClient", FakeLive)
    return task, out


def test_work_prints_and_reports_answer(tmp_path, monkeypatch, capsys):
    task, out = _setup(tmp_path, monkeypatch)
    rc = cli.main(["work", f"file:{task}", "--out", str(out), "--report"])
    shown = capsys.readouterr().out
    assert rc == 0
    assert "The answer is 42" in shown and "r.xlsx" in shown and "Files:" in shown
    assert "The answer is 42" in (tmp_path / "task.md.result.json").read_text(encoding="utf-8")


def test_work_json_flag(tmp_path, monkeypatch, capsys):
    task, out = _setup(tmp_path, monkeypatch)
    rc = cli.main(["work", f"file:{task}", "--out", str(out), "--json"])
    d = json.loads(capsys.readouterr().out)
    assert rc == 0 and d["answer"] == "The answer is 42" and d["artifacts"][0].endswith("r.xlsx")
    assert d["out_dir"] == str(out.resolve()) and d["reason"] == ""
