"""ws19 server-side items: B3 B4 B6 F-05 F-21 F-24, B1 script order, F-25 brand, F-39 titles (no browser needed)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from meshpool_harness.ui import desktop as D
from meshpool_harness.ui import previews as P
from meshpool_harness.ui import workbench as W

UI = Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui"


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    P._JOBS.clear()
    return root


def test_b1_preview_js_loads_before_app_js_and_the_call_is_guarded():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert html.index("/ui/preview.js") < html.index("/ui/app.js")
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"(?<!safe\(\(\) => )pvTurnCards\(cur", js)


def test_b3_audio_and_video_have_a_preview_kind(proj):
    (proj / "a.mp3").write_bytes(b"ID3" + b"\0" * 40)
    (proj / "v.mp4").write_bytes(b"\0\0\0\x18ftypmp42" + b"\0" * 40)
    assert P.meta(str(proj), "a.mp3")["kind"] == "audio"
    assert P.meta(str(proj), "v.mp4")["kind"] == "video"


def test_b4_an_empty_jpg_is_binary_and_read_only(proj):
    (proj / "empty.jpg").write_bytes(b"")
    r = W.read_file(str(proj), "empty.jpg")
    assert r["binary"] is True and r.get("readonly") is True and r["text"] == ""
    (proj / "a.txt").write_text("x")
    assert W.read_file(str(proj), "a.txt")["binary"] is False


def test_b6_a_corrupt_word_file_says_so_plainly(proj):
    (proj / "bad.docx").write_bytes(b"this is not a zip " * 20)
    with pytest.raises(ValueError) as e:
        P.meta(str(proj), "bad.docx")
    msg = str(e.value)
    assert "damaged" in msg and str(proj) not in msg and "Traceback" not in msg and "zip" not in msg.lower()
    with pytest.raises(ValueError) as e2:
        W.read_file(str(proj), "bad.docx")
    assert str(proj) not in str(e2.value) and "damaged" in str(e2.value)


def test_b6_a_converter_failure_has_no_paths_or_tool_output():
    msg = P.friendly_error(Path("/home/x/secret/report.docx"),
                           RuntimeError("LibreOffice could not convert report.docx: /tmp/xyz javax.Error"))
    assert "/" not in msg and "LibreOffice" not in msg and "Word document" in msg


def test_f24_formula_cells_without_cached_values_show_the_formula(proj, monkeypatch):
    """Without LibreOffice to calculate them, formula cells with no stored value show the formula."""
    import openpyxl
    from meshpool_harness import xl_values
    monkeypatch.setattr(xl_values, "values", lambda *a, **k: None)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["A2"], ws["B1"] = 5, "=A1*2", "=SUM(A1:A2)"
    wb.save(proj / "f.xlsx")                       # openpyxl writes no cached values
    rows = P.meta(str(proj), "f.xlsx")["sheets"][0]["rows"]
    assert rows[1][0] == "=A1*2" and rows[0][1] == "=SUM(A1:A2)" and rows[0][0] == "5"


def test_f24_formula_cells_without_cached_values_show_the_computed_value_when_libreoffice_runs(proj):
    """With LibreOffice (the kept session), the preview shows what Excel would show (item 18 of the release review)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["A2"], ws["B1"] = 5, "=A1*2", "=SUM(A1:A2)"
    wb.save(proj / "g.xlsx")
    rows = P.meta(str(proj), "g.xlsx")["sheets"][0]["rows"]
    if rows[1][0].startswith("="):
        pytest.skip("no LibreOffice here to calculate it")
    assert rows[1][0] == "10" and rows[0][1] == "15" and rows[0][0] == "5", rows


def test_f05_a_picker_that_cannot_open_is_reported_not_treated_as_a_cancel(monkeypatch):
    monkeypatch.setattr(D, "WINDOW", [None])
    monkeypatch.setattr(D.sys, "platform", "linux")
    monkeypatch.setattr(D.os, "name", "posix")
    monkeypatch.setattr(D.shutil, "which", lambda n: "/usr/bin/zenity")
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "cannot open display"))
    with pytest.raises(D.PickerUnavailable):
        D.choose_folder("")
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    assert D.choose_folder("") == ""               # a real cancel prints nothing


def test_f21_an_unchanged_rewrite_is_not_a_change_and_old_files_are_not_new(tmp_path):
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.core.tools import Workspace
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    ws.write_file("hello.py", "print('hi')\n")
    assert ws.turn_new == {str(tmp_path / "hello.py")}
    ws.turn_files, ws.turn_new = set(), set()      # the next turn
    ws.write_file("hello.py", "print('hi')\n")     # same text
    assert not ws.turn_files
    ws.write_file("hello.py", "print('bye')\n")    # changed: listed, but not "(new)"
    me = SimpleNamespace(ws=ws, root=str(tmp_path), last_review=None, _turn_no=1)
    out = Agent.receipt(me)
    assert "hello.py" in out and "(new)" not in out


def test_f25_the_brand_is_meshpool_in_user_facing_text():
    root = UI.parent
    for f in ("ui/hub.py", "ui/composer.js", "agent/tui.py", "agent/tui_render.py"):
        assert not re.search(r"\bMeshPool\b", (root / f).read_text(encoding="utf-8")), f
    prompt = (root.parent / "defaults/engines/codex/prompt.md").read_text(encoding="utf-8")
    assert not re.search(r"\bMeshPool\b", prompt)


def test_f39_disabled_buttons_say_why():
    js = (UI / "app.js").read_text(encoding="utf-8")
    for bid in ("gcommit", "gpush", "gpull", "gpr", "ghGo"):
        # each button's own opening tag only: several buttons share one line, so a title on the next button
        # must not count for this one
        tags = re.findall(r'<button\b[^>]*\bid="%s"[^>]*>' % bid, js)
        assert len(tags) == 1, (bid, tags)
        assert re.search(r'\bdisabled title="[^"]{8,}"', tags[0]), (bid, tags[0])
