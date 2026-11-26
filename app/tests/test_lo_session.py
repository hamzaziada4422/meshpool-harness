"""The persistent LibreOffice session (meshpool_harness/lo_session.py): one LibreOffice for many conversions, closed when
idle, restarted after a crash, and never in the way: anything it cannot do falls back to the one-off run."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import lo_session as LS  # noqa: E402
from meshpool_harness import office_runtime  # noqa: E402

pytestmark = pytest.mark.timeout(600)


@pytest.fixture
def lo(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_ALLOW_SYSTEM_SOFFICE", "1")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_CONVERT_CACHE", "0")            # every call converts: the session does the work
    exe = office_runtime.find_soffice()
    if not exe or not LS.real_office(exe) or not LS.lo_python(exe):
        pytest.skip("LibreOffice with its Python (uno) is not installed here (CI installs python3-uno)")
    LS.close_all()
    LS._SESSIONS.clear()
    yield exe
    LS.close_all()
    LS._SESSIONS.clear()


def _docx(path: Path, text: str) -> Path:
    from docx import Document
    d = Document()
    d.add_paragraph(text)
    d.save(path)
    return path


def _xlsx(path: Path) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["A2"], ws["A3"] = 6, 7, "=A1*A2"
    wb.save(path)
    return path


def _soffice_children() -> list:
    import psutil
    return [p for p in psutil.Process().children(recursive=True) if p.name().startswith("soffice")]


def test_many_conversions_start_one_libreoffice(lo, tmp_path):
    from engines.forge.common import soffice_convert
    for i in range(5):
        out = soffice_convert(str(_docx(tmp_path / f"d{i}.docx", f"page {i}")), "pdf", str(tmp_path / "out"))
        assert out.read_bytes()[:4] == b"%PDF"
    st = LS.stats()[lo]
    assert st["starts"] == 1 and st["jobs"] == 5             # was five LibreOffice starts, one per file


def test_recalculation_goes_through_the_session_and_computes_formulas(lo, tmp_path):
    import openpyxl
    from engines.sheets import qa
    made = qa.recalc(str(_xlsx(tmp_path / "f.xlsx")), str(tmp_path / "out"))
    assert openpyxl.load_workbook(made, data_only=True).active["A3"].value == 42
    assert LS.stats()[lo]["jobs"] == 1


def test_formats_the_app_asks_for(lo, tmp_path):
    from engines.forge.common import soffice_convert
    src = _docx(tmp_path / "w.docx", "hello")
    for to, magic in (("pdf", b"%PDF"), ("odt", b"PK"), ("docx", b"PK")):
        assert soffice_convert(str(src), to, str(tmp_path / to)).read_bytes()[:len(magic)] == magic
    x = soffice_convert(str(_xlsx(tmp_path / "x.xlsx")), "xlsx:Calc MS Excel 2007 XML", str(tmp_path / "xo"))
    assert x.read_bytes()[:2] == b"PK"
    assert LS.stats()[lo]["starts"] == 1


def test_it_closes_when_idle_and_starts_again_on_the_next_call(lo, tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LO_IDLE_S", "5")
    assert LS.convert(lo, str(_docx(tmp_path / "a.docx", "a")), "pdf", str(tmp_path / "o")) is not None
    s = LS._SESSIONS[lo]
    assert s.alive() and _soffice_children()
    end = time.time() + 30
    while s.office is not None and time.time() < end:
        time.sleep(0.5)
    assert s.office is None and not s.alive()                # closed, with its profile folder
    time.sleep(1)
    assert not [p for p in _soffice_children() if p.is_running() and p.status() != "zombie"]
    assert LS.convert(lo, str(_docx(tmp_path / "b.docx", "b")), "pdf", str(tmp_path / "o")) is not None
    assert s.starts == 2


def test_a_crashed_libreoffice_is_replaced_on_the_next_call(lo, tmp_path):
    assert LS.convert(lo, str(_docx(tmp_path / "a.docx", "a")), "pdf", str(tmp_path / "o")) is not None
    s = LS._SESSIONS[lo]
    for p in _soffice_children():
        p.kill()
    time.sleep(1)
    assert LS.convert(lo, str(_docx(tmp_path / "b.docx", "b")), "pdf", str(tmp_path / "o")) is not None
    assert s.starts == 2 and s.alive()


def test_a_file_it_cannot_open_falls_back_and_the_session_stays(lo, tmp_path):
    from engines.forge.common import soffice_convert
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"PK\x03\x04 not really a document")
    assert LS.convert(lo, str(bad), "pdf", str(tmp_path / "o")) is None   # the caller runs the one-off command
    with pytest.raises(RuntimeError):
        soffice_convert(str(bad), "pdf", str(tmp_path / "o2"))
    good = _docx(tmp_path / "g.docx", "fine")
    assert soffice_convert(str(good), "pdf", str(tmp_path / "o3")).exists()
    assert LS._SESSIONS[lo].starts == 1


def test_parallel_callers_share_it(lo, tmp_path):
    from engines.forge.common import soffice_convert
    srcs = [_docx(tmp_path / f"p{i}.docx", f"p {i}") for i in range(4)]
    outs, errs = [], []

    def go(src):
        try:
            outs.append(soffice_convert(str(src), "pdf", str(tmp_path / "par")))
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go, args=(s,)) for s in srcs]
    for t in ts:
        t.start()
    for t in ts:
        t.join(240)
    assert not errs and len(outs) == 4 and all(o.exists() for o in outs)
    assert LS.stats()[lo]["starts"] == 1


def test_turned_off_or_without_uno_the_one_off_run_still_works(lo, tmp_path, monkeypatch):
    from engines.forge.common import soffice_convert
    monkeypatch.setenv("MH_LO_RESIDENT", "0")
    assert LS.convert(lo, str(_docx(tmp_path / "a.docx", "a")), "pdf", str(tmp_path / "o")) is None
    assert soffice_convert(str(_docx(tmp_path / "b.docx", "b")), "pdf", str(tmp_path / "o")).exists()
    monkeypatch.delenv("MH_LO_RESIDENT")
    monkeypatch.setattr(LS, "lo_python", lambda exe: None)
    LS._SESSIONS.clear()
    assert LS.convert(lo, str(_docx(tmp_path / "c.docx", "c")), "pdf", str(tmp_path / "o")) is None
    assert soffice_convert(str(_docx(tmp_path / "d.docx", "d")), "pdf", str(tmp_path / "o")).exists()


def test_a_stand_in_script_is_never_started_as_a_session(tmp_path):
    fake = tmp_path / "soffice"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    assert not LS.real_office(str(fake))
    assert LS.session(str(fake)) is None


def test_convert_document_never_borrows_the_users_libreoffice(tmp_path, monkeypatch):
    """Without a profile of its own, `soffice --convert-to` handed the job to a LibreOffice the user had open,
    which then quit."""
    from meshpool_harness.agent import office_mcp_server as OM
    src = _docx(tmp_path / "a.docx", "a")
    monkeypatch.setattr(OM, "_soffice", lambda: "/opt/lo/program/soffice")
    monkeypatch.setattr(OM, "_resolve", lambda p: Path(p))
    monkeypatch.setattr(LS, "convert", lambda *a, **k: None)
    seen = []

    def run(cmd, timeout):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "no")
    monkeypatch.setattr(OM, "_run_soffice", run)
    OM.convert_document(str(src), "pdf")
    assert seen and any(str(a).startswith("-env:UserInstallation=") for a in seen[0])


def test_the_worker_maps_each_target_to_libreoffices_own_filter():
    from meshpool_harness import lo_worker as W
    assert W.FILTERS["impress"]["pdf"] == "impress_pdf_Export"
    assert W.FILTERS["calc"]["xlsx"] == "Calc MS Excel 2007 XML"
    assert W.FILTERS["writer"]["docx"] == "MS Word 2007 XML"
    assert all(set(v) <= {"pdf", "docx", "doc", "odt", "rtf", "txt", "html", "png", "jpg", "svg", "epub", "xlsx",
                          "xls", "ods", "csv", "pptx", "ppt", "odp", "odg"} for v in W.FILTERS.values())


def test_closing_the_app_stops_the_kept_libreoffice_without_waiting(lo, tmp_path):
    from engines.forge.common import kill_all_soffice
    assert LS.convert(lo, str(_docx(tmp_path / "a.docx", "a")), "pdf", str(tmp_path / "o")) is not None
    s = LS._SESSIONS[lo]
    with s.lock:                                             # a conversion in progress holds the lock
        t0 = time.time()
        kill_all_soffice()
        assert time.time() - t0 < 15 and s.office is None
    time.sleep(1)
    assert not [p for p in _soffice_children() if p.is_running() and p.status() != "zombie"]


def test_libreoffice_ends_when_the_app_goes_away_without_closing_it(lo, tmp_path):
    """Review, 0.0.18: soffice runs in a process group of its own, so a crash or kill of the app left it running with
    --accept. The worker now ends LibreOffice once its stdin (the app's pipe) closes, as it does when the app dies."""
    assert LS.convert(lo, str(_docx(tmp_path / "a.docx", "a")), "pdf", str(tmp_path / "o")) is not None
    s = LS._SESSIONS[lo]
    office, worker = s.office, s.worker
    s.office = s.worker = s.profile = None             # the app is gone: nothing will call _stop
    worker.stdin.close()
    end = time.time() + 30
    while office.poll() is None and time.time() < end:
        time.sleep(0.2)
    assert office.poll() is not None                   # LibreOffice exited on its own
    worker.wait(timeout=10)


def test_a_busy_session_hands_the_job_back_instead_of_waiting(lo, tmp_path, monkeypatch):
    """Review, 0.0.18: a preview waited for a 240 s deck render to finish before its own timeout started. Busy past
    BUSY_WAIT_S, the session says so and convert() returns None, so the caller runs its own one-off conversion."""
    assert LS.convert(lo, str(_docx(tmp_path / "a.docx", "a")), "pdf", str(tmp_path / "o")) is not None
    s = LS._SESSIONS[lo]
    monkeypatch.setattr(LS, "BUSY_WAIT_S", 1.0)
    s.lock.acquire()                                     # a long job holds the session
    try:
        t0 = time.time()
        assert LS.convert(lo, str(_docx(tmp_path / "b.docx", "b")), "pdf", str(tmp_path / "o")) is None
        assert time.time() - t0 < 5
    finally:
        s.lock.release()
    assert LS.convert(lo, str(_docx(tmp_path / "c.docx", "c")), "pdf", str(tmp_path / "o")) is not None
