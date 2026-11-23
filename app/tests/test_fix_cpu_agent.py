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
    (tmp_path / "home").mkdir()
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
