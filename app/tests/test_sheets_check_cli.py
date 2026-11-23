"""python -m engines.sheets.check runs the same gate as the engine's finish (finish_check), so PASS means finish accepts."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.sheets import check as CLI  # noqa: E402
from engines.sheets import engine as E  # noqa: E402
from engines.sheets.qa import check as qa_check  # noqa: E402


def _no_cover(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"], ws["B1"], ws["B2"] = "x", 1, "=B1*2"
    p = tmp_path / "plain.xlsx"
    wb.save(p)
    return str(p)


def test_cli_reports_what_finish_would_reject(tmp_path, capsys):
    p = _no_cover(tmp_path)
    assert not qa_check(p)["issues"]                 # workbook QA alone is happy with it
    assert CLI.main([p]) == 1
    out = capsys.readouterr().out
    assert "no Cover tab" in out and "PASS" not in out


def test_cli_prints_pass_when_finish_accepts(tmp_path, monkeypatch, capsys):
    p = _no_cover(tmp_path)
    seen = []
    monkeypatch.setattr(E, "finish_check", lambda answer, artifacts, ws, report=None: seen.append((artifacts, ws.root)))
    assert CLI.main([p]) == 0
    assert capsys.readouterr().out.startswith("PASS: ")
    assert seen == [([p], Path(p).parent)]
