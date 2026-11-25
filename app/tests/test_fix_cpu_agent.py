"""CPU use: MCP servers are shared per folder, start on first use, stop when idle; documents are read once."""
import os
import sys
import time
from pathlib import Path

import pytest

psutil = pytest.importorskip("psutil")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import docread  # noqa: E402
from meshpool_harness.agent import mcp as M  # noqa: E402
from meshpool_harness.harnesses.acp import office_extra  # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    yield
    M.stop_all()


def _kids():
    return psutil.Process().children(recursive=True)


def _open_chats(tmp_path, n):
    return [M.Manager(roots=[str(tmp_path)], extra=office_extra(str(tmp_path))).start(timeout=60) for _ in range(n)]


def _tool(m, word):
    return next(k for k in m.map if word in k)


def test_five_chats_share_one_process_and_idle_cpu_is_nil(tmp_path):
    before = len(_kids())
    first = _open_chats(tmp_path, 1)[0]              # the first start saves the tool list
    first.close()
    chats = _open_chats(tmp_path, 5)
    assert len(_kids()) - before == 0                # lazy: nothing started yet, whatever the number of chats
    name = _tool(chats[0], "find_icon")
    assert not chats[0].call(name, {"query": "money", "limit": 1})[1]
    assert not chats[3].call(name, {"query": "bank", "limit": 1})[1]
    assert len(_kids()) - before == 1                # one shared process serves all five chats
    base = {c.pid: sum(c.cpu_times()[:2]) for c in _kids()}
    time.sleep(20)
    used = sum(sum(c.cpu_times()[:2]) - base.get(c.pid, 0) for c in _kids())
    assert used < 1.0                                # 20 s idle: well under 5 % of one core
    for c in chats:
        c.close()
    time.sleep(0.5)
    assert len(_kids()) - before == 0                # closed chats leave no child behind


def test_idle_server_is_stopped_and_restarts_on_use(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_MCP_IDLE_S", "1")
    before = len(_kids())
    _open_chats(tmp_path, 1)[0].close()
    m = _open_chats(tmp_path, 1)[0]
    name = _tool(m, "find_icon")
    assert not m.call(name, {"query": "money", "limit": 1})[1]
    assert len(_kids()) - before == 1
    deadline = time.time() + 15
    while time.time() < deadline and len(_kids()) - before:
        time.sleep(0.5)
    assert len(_kids()) - before == 0
    assert not m.call(name, {"query": "money", "limit": 1})[1]    # starts again on the next call
    m.close()


def test_document_text_is_cached_and_long_pdfs_are_paged(tmp_path, monkeypatch):
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    pdf = tmp_path / "long.pdf"
    c = reportlab.Canvas(str(pdf))
    for i in range(docread.PDF_PAGES + 20):
        c.drawString(72, 700, f"page {i} has a text layer long enough to count as text")
        c.showPage()
    c.save()
    calls = []
    real = docread._extract
    monkeypatch.setattr(docread, "_extract", lambda *a, **k: calls.append(1) or real(*a, **k))
    a = docread.extract(pdf)
    b = docread.extract(pdf)
    assert a == b and len(calls) == 1                 # the second read came from the cache
    assert "20 more pages" in a and "## Page 80" in a and "## Page 81" not in a
    pdf.write_bytes(pdf.read_bytes() + b"\n%x")       # changed file: read again
    docread.extract(pdf)
    assert len(calls) == 2
    assert "## Page 81" in docread.extract(pdf, pages="81-90")


def test_parallel_document_reads_are_capped():
    assert docread._SLOTS._initial_value == max(1, (os.cpu_count() or 2) // 2)


def test_twenty_reads_across_three_chats_use_one_reader_started_once(tmp_path, monkeypatch):
    spawns, inits = [], []
    real_init, real_req = M._Stdio.__init__, M._Stdio.request

    def counting_init(self, *a, **k):
        spawns.append(1)
        real_init(self, *a, **k)

    def counting_req(self, rid, method, params, timeout):
        if method == "initialize":
            inits.append(1)
        return real_req(self, rid, method, params, timeout)
    monkeypatch.setattr(M._Stdio, "__init__", counting_init)
    monkeypatch.setattr(M._Stdio, "request", counting_req)
    M.Manager(roots=[str(tmp_path)], extra=office_extra(str(tmp_path))).start(timeout=60).close()   # saves the tool list
    spawns.clear()
    inits.clear()
    folders = []
    for i in range(3):                                   # three chats, three different project folders
        d = tmp_path / f"proj{i}"
        d.mkdir()
        (d / "note.docx").write_bytes(b"")
        (d / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        folders.append(d)
    chats = [M.Manager(roots=[str(d)], extra=office_extra(str(d))).start(timeout=60) for d in folders]
    assert spawns == [] and inits == []                  # opening chats that never ask for a document starts nothing
    name = _tool(chats[0], "read_document")
    for n in range(20):
        chat, d = chats[n % 3], folders[n % 3]
        text, failed = chat.call(name, {"path": "a.csv"})
        assert not failed and "x" in text
    assert len(spawns) == 1 and len(inits) == 1          # one reader, initialised once, for all 20 reads
    for c in chats:
        c.close()


def test_chat_without_document_request_never_starts_the_reader(tmp_path, monkeypatch):
    spawns = []
    real_init = M._Stdio.__init__
    monkeypatch.setattr(M._Stdio, "__init__", lambda self, *a, **k: (spawns.append(1), real_init(self, *a, **k))[1])
    M.Manager(roots=[str(tmp_path)], extra=office_extra(str(tmp_path))).start(timeout=60).close()
    spawns.clear()
    before = {k.pid for k in _kids()}              # leftovers of earlier tests (a zombie from 26 minutes before, CI)
    chat = M.Manager(roots=[str(tmp_path)], extra=office_extra(str(tmp_path))).start(timeout=60)
    time.sleep(1)
    new = [k for k in _kids() if k.pid not in before and k.status() != psutil.STATUS_ZOMBIE]
    assert spawns == [] and not new
    chat.close()


def test_reader_restarts_after_a_crash(tmp_path):
    m = M.Manager(roots=[str(tmp_path)], extra=office_extra(str(tmp_path))).start(timeout=60)
    (tmp_path / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    name = _tool(m, "read_document")
    assert not m.call(name, {"path": "a.csv"})[1]
    for c in _kids():
        c.kill()
    time.sleep(0.5)
    ok = False
    for _ in range(2):                                   # the call in flight at the crash may fail; the next works
        ok = not m.call(name, {"path": "a.csv"})[1]
        if ok:
            break
    assert ok
    m.close()
