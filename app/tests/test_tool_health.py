"""Fixes for tools that packaging/tool_health.py found broken (docs/dev/TOOLS_HEALTH.md)."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _Page(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b"<!doctype html><html><head><title>Local page</title></head><body><p>LOCAL-PAGE-TOKEN</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def local_page(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


def test_fetch_reads_a_local_page_when_private_addresses_are_allowed(local_page, monkeypatch):
    """fetch_url and web_fetch refused a local address even with MH_ALLOW_PRIVATE_FETCH=1, the setting their own error
    names: the pinned connection (0.0.16) checked the address again and ignored the setting."""
    from meshpool_harness import web as W
    monkeypatch.setenv("MH_ALLOW_PRIVATE_FETCH", "1")
    W._CACHE.clear()
    assert "LOCAL-PAGE-TOKEN" in W.fetch(local_page, refresh=True)["text"]


def test_fetch_still_refuses_a_local_page_by_default(local_page, monkeypatch):
    from meshpool_harness import web as W
    monkeypatch.delenv("MH_ALLOW_PRIVATE_FETCH", raising=False)
    W._CACHE.clear()
    with pytest.raises(W.WebError, match="local or private network address"):
        W.fetch(local_page, refresh=True)


def test_pinned_connection_still_refuses_a_rebound_local_address(monkeypatch):
    """The DNS-rebinding guard stays: without the setting, a name that resolves to 127.0.0.1 at connect time is refused."""
    import socket
    from meshpool_harness import web as W
    monkeypatch.delenv("MH_ALLOW_PRIVATE_FETCH", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                                  ("127.0.0.1", 80))])
    with pytest.raises(W.WebError, match="local or private"):
        W._pinned_create("http://example.com/")(("example.com", 80))


# ---------- OCR: the meshpool apps ship without it (pyproject extra "ocr"), so no tool may promise it ---------------

def _no_ocr(monkeypatch):
    from meshpool_harness import ocr
    monkeypatch.setattr(ocr, "installed", lambda: False)
    monkeypatch.setattr(ocr, "available", lambda: False)


def test_read_file_does_not_promise_ocr_in_an_install_without_it(monkeypatch):
    """The 0.0.18 apps told the model read_file reads pictures with OCR; every picture came back as 'OCR of x.png
    failed: OCR is not included in this install.'"""
    from meshpool_harness.core import tools as T
    _no_ocr(monkeypatch)
    desc = T.schemas(["read_file"])[0]["function"]["description"]
    assert "OCR in many languages" not in desc and "view_output" in desc
    assert "OCR in many languages" in T.SCHEMAS["read_file"]["function"]["description"]    # the full text stays


def test_read_file_keeps_the_ocr_promise_where_ocr_is_installed(monkeypatch):
    from meshpool_harness import ocr
    from meshpool_harness.core import tools as T
    monkeypatch.setattr(ocr, "installed", lambda: True)
    assert "OCR in many languages" in T.schemas(["read_file"])[0]["function"]["description"]


def test_read_document_does_not_promise_ocr_in_an_install_without_it(monkeypatch):
    from meshpool_harness.agent import office_mcp_server as O
    _no_ocr(monkeypatch)
    assert "(OCR for scans)" not in O.tools()["read_document"].description


def test_a_picture_without_ocr_says_so_plainly(tmp_path, monkeypatch):
    from PIL import Image
    from meshpool_harness.core.tools import Workspace
    _no_ocr(monkeypatch)
    Image.new("RGB", (40, 20), "white").save(tmp_path / "shot.png")
    out = Workspace(str(tmp_path), str(tmp_path / "run")).read_file("shot.png")
    assert "failed" not in out and "no OCR" in out and "view_output" in out


# ---------- the release runs the check: a step limit it cannot meet fails the release ---------------------------------

def test_each_release_step_that_runs_the_check_has_time_for_it():
    """The Windows smoke step kept its 15-minute limit ("normally about 2 minutes") when the tool health check (every
    tool once, LibreOffice and Chromium started cold), the browser step and the bundled programs joined it: a slow
    runner would time the step out and fail the release. Every step that runs it now has at least 30 minutes."""
    from pathlib import Path

    import yaml
    wf = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml").read_text())
    steps = [(job, s) for job, j in wf["jobs"].items() for s in j.get("steps") or []
             if "packaging/tool_health.py" in str(s.get("run") or "")]
    assert {job for job, _ in steps} >= {"mac", "windows", "linux"}, steps
    for job, s in steps:
        limit = s.get("timeout-minutes", wf["jobs"][job].get("timeout-minutes", 360))
        assert limit >= 30, (job, s.get("name"), limit)
