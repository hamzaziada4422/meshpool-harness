"""Ledger 0.0.17 gap rows for the UI server (ui/server.py, ui/desktop.py, ui/agent_api.py, ui/workbench.py,
ui/previews.py): each test runs the audit's trigger against the real code and fails if the fix is reverted."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from meshpool_harness.ui import desktop as D
from meshpool_harness.ui import server as S


# ---------------------------------------------------------------------------------------------------- helpers
@pytest.fixture
def httpd():
    srv = S._Server(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


class _Proxy:
    """A stand-in for a module (os, sys) inside one module only: given attributes, the rest from the real module.
    Patching the real os.name or sys.platform would change pathlib and everything else in the test process."""

    def __init__(self, real, **over):
        self._real, self._over = real, over

    def __getattr__(self, k):
        return self._over[k] if k in self._over else getattr(self._real, k)


def _raw(port: int, request: bytes, timeout: float = 5.0) -> bytes:
    """Send a hand-written HTTP request (urllib refuses a negative Content-Length) and read the whole answer."""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(request)
        s.shutdown(socket.SHUT_WR)
        out = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
    return out


# ---------------------------------------------------------------------------------------------------- PA-uiserver-05
def test_windows_folder_picker_passes_the_start_path_outside_the_script(tmp_path, monkeypatch):
    """PA-uiserver-05: a Windows user named O'Connor clicks Add folder. The start path goes to PowerShell in an
    environment variable, so an apostrophe can neither break the script nor inject a command into it."""
    start = tmp_path / "O'Connor'; Remove-Item -Recurse C:\\ ; '"
    start.mkdir()
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env") or {}
        return types.SimpleNamespace(returncode=0, stdout="C:\\Users\\O'Connor\\Work\r\n", stderr="")

    monkeypatch.setattr(D, "WINDOW", [None])
    monkeypatch.setattr(D, "sys", _Proxy(sys, platform="win32"))
    monkeypatch.setattr(D, "os", _Proxy(os, name="nt"))
    monkeypatch.setattr(D.subprocess, "run", fake_run)
    got = D.choose_folder(str(start))
    monkeypatch.undo()
    assert got == "C:\\Users\\O'Connor\\Work"
    script = seen["cmd"][-1]
    assert seen["cmd"][0] == "powershell"
    assert "O'Connor" not in script and "Remove-Item" not in script      # nothing of the path is in the script text
    assert "$env:MESHPOOL_PICK_START" in script
    assert seen["env"]["MESHPOOL_PICK_START"] == str(start)


# ---------------------------------------------------------------------------------------------------- PA-uiserver-07
@pytest.mark.parametrize("name,body", [
    ("x.html", b"<html><script>fetch('/api/settings')</script></html>"),
    ("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
])
def test_files_route_sandboxes_agent_written_html_and_svg(httpd, tmp_path, monkeypatch, name, body):
    """PA-uiserver-07: the agent writes runs/<id>/out/x.html (or .svg) with a <script> and the user opens the link.
    /files must send Content-Security-Policy: sandbox (and nosniff), so the script cannot run in the app's origin."""
    runs = tmp_path / "runs"
    (runs / "r1" / "out").mkdir(parents=True)
    (runs / "r1" / "out" / name).write_bytes(body)
    monkeypatch.setattr(S, "RUNS", runs)
    url = f"http://127.0.0.1:{httpd}/files/r1/{name}?t={S.TOKEN}"
    with urllib.request.urlopen(url, timeout=10) as r:
        assert r.status == 200 and r.read() == body
        assert r.headers["Content-Security-Policy"] == "sandbox"
        assert r.headers["X-Content-Type-Options"] == "nosniff"
    with pytest.raises(urllib.error.HTTPError) as e:                     # and the token is still needed
        urllib.request.urlopen(f"http://127.0.0.1:{httpd}/files/r1/{name}", timeout=10)
    assert e.value.code == 403


# ---------------------------------------------------------------------------------------------------- PA-uiserver-08
@pytest.mark.parametrize("name", ["payload.pkg", "x.desktop", "console.msc", "setup.mpkg", "tool.appimage", "a.run"])
def test_show_in_folder_never_launches_an_agent_made_file(tmp_path, monkeypatch, name):
    """PA-uiserver-08: the agent makes payload.pkg / x.desktop and the user clicks to see it. The app shows the folder
    (or selects the file in Finder) and never hands the file itself to the system opener."""
    f = tmp_path / name
    f.write_text("#!/bin/sh\necho owned\n")
    calls = []
    monkeypatch.setattr(D.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(D, "sys", _Proxy(sys, platform="linux"))
    monkeypatch.setattr(D, "os", _Proxy(os, name="posix"))
    D.show_in_folder(f)
    assert calls == [["xdg-open", str(tmp_path)]]                       # the folder, not the file
    calls.clear()
    monkeypatch.setattr(D, "sys", _Proxy(sys, platform="darwin"))
    D.show_in_folder(f)
    assert calls == [["open", "-R", str(f)]]                             # -R reveals in Finder, it does not open
    assert {".pkg", ".desktop", ".msc", ".mpkg", ".appimage", ".run"} <= S.RUNNABLE


# ---------------------------------------------------------------------------------------------------- PA-uiserver-11
def test_write_meta_keeps_a_pin_written_at_the_same_time(tmp_path, monkeypatch):
    """PA-uiserver-11: pin a chat the moment its turn finishes. write_meta(model=...) and write_meta(pinned=True) run
    at once; the read-modify-write is under a lock, so neither field is lost."""
    from meshpool_harness.ui import agent_api as A
    monkeypatch.setattr(A, "SESSIONS", tmp_path)
    sid = "c1"
    (tmp_path / sid).mkdir()
    (tmp_path / sid / "meta.json").write_text(json.dumps({"title": "T"}), encoding="utf-8")
    A._METAS.pop(sid, None)
    real = A.read_meta

    def slow_read(s):                 # widen the window between the read and the write, as a busy machine does
        m = real(s)
        time.sleep(0.2)
        return m
    monkeypatch.setattr(A, "read_meta", slow_read)
    ts = [threading.Thread(target=A.write_meta, args=(sid,), kwargs={"model": "m-2"}),
          threading.Thread(target=A.write_meta, args=(sid,), kwargs={"pinned": True})]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    got = json.loads((tmp_path / sid / "meta.json").read_text(encoding="utf-8"))
    assert got == {"title": "T", "model": "m-2", "pinned": True}


# ---------------------------------------------------------------------------------------------------- PA-uiserver-18
@pytest.mark.parametrize("pr", [
    {"state": "closed", "merged_at": "2026-01-02T00:00:00Z", "mergeable": None},     # merged
    {"state": "closed", "merged_at": None, "mergeable": None},                       # closed without merging
])
def test_merging_a_merged_or_closed_pr_says_it_is_not_open(monkeypatch, pr):
    """PA-uiserver-18: click merge on a merged (or closed) pull request. The state is tested first: the answer is
    "it is not open", not "GitHub says it cannot merge yet", and no merge call goes to GitHub."""
    from meshpool_harness.ui import workbench as W
    calls = []
    full = {"number": 7, "title": "T", "html_url": "https://github.com/o/r/pull/7", "draft": False,
            "head": {"ref": "f", "sha": "abc1234", "repo": {"full_name": "o/r"}}, "base": {"ref": "main", "sha": "def"},
            "user": {"login": "u"}, **pr}

    def gh(path, token, method="GET", body=None):
        calls.append((method, path))
        if method != "GET":
            raise AssertionError(f"no write to GitHub expected: {method} {path}")
        if path.endswith("/pulls/7"):
            return full
        if "/actions/runs" in path:
            return {"workflow_runs": []}
        return []
    monkeypatch.setattr(W, "_repo", lambda root: ("o/r", "tok"))
    monkeypatch.setattr(W, "_gh_api", gh)
    monkeypatch.setattr(W, "_gh_all", lambda *a, **k: [])
    monkeypatch.setattr(W, "_checks_for", lambda *a, **k: [])
    with pytest.raises(ValueError) as e:
        W.pr_action("/repo", {"pr": 7, "action": "merge"})
    assert str(e.value) == "Not merged: it is not open."
    assert not [c for c in calls if c[0] != "GET"]


# ---------------------------------------------------------------------------------------------------- PA-uiserver-20
def _one_page_pdf() -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return out


def test_preview_job_finishes_when_meta_json_is_missing(tmp_path, monkeypatch):
    """PA-uiserver-20: the app was killed after doc.pdf was moved into place, before meta.json was written. The next
    job writes meta.json from doc.pdf and drops the "working" job, so the preview does not stay "working" forever."""
    pytest.importorskip("pypdfium2")
    from meshpool_harness.ui import previews as PV
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"not really a deck")
    key = PV._key(src)
    (PV._dir() / key).mkdir(parents=True)
    (PV._dir() / key / "doc.pdf").write_bytes(_one_page_pdf())
    with PV._JOBS_LOCK:
        PV._JOBS[key] = {"state": "working", "t": time.time()}
    import engines.forge.common as FC
    monkeypatch.setattr(FC, "soffice_convert", lambda *a, **k: pytest.fail("no new conversion is needed"))
    PV._convert_job(src, key)
    assert json.loads((PV._dir() / key / "meta.json").read_text()) == {"total": 1}
    assert key not in PV._JOBS
    assert PV._office_state(src) == {"state": "ready", "pages": 1, "total": 1, "key": key}


# ---------------------------------------------------------------------------------------------------- PA-uiserver-21
@pytest.mark.skipif(os.name == "nt", reason="the POSIX start-up files; Windows uses the registry PATH")
@pytest.mark.parametrize("shell,want", [("/usr/bin/fish", set()), ("/bin/bash", {".bashrc"}), ("/bin/zsh", {".zshrc"})])
def test_install_cli_only_touches_the_users_own_shell(tmp_path, monkeypatch, shell, want):
    """PA-uiserver-21: first launch of the Linux download. install_cli adds the PATH line only to the start-up file of
    the user's own $SHELL; a fish user gets no new .zshrc or .bashrc. (The "ask in Settings first" part is not here:
    ensure_cli still runs it at launch without asking.)"""
    home = tmp_path / "home"
    home.mkdir()
    cli = tmp_path / "meshpool"
    cli.write_text("#!/bin/sh\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", shell)
    monkeypatch.setattr(D, "_cli_path", lambda: str(cli))
    monkeypatch.setattr(D, "sys", _Proxy(sys, platform="linux"))
    monkeypatch.setattr(D, "os", _Proxy(os, access=lambda p, m: False if str(p) in ("/usr/local/bin", "/opt/homebrew/bin")
                                        else os.access(p, m)))
    r = D.install_cli()
    assert r["ok"] is True
    assert {p.name for p in home.iterdir() if p.is_file()} == want
    assert (home / ".local" / "bin" / "meshpool").is_symlink()
    for n in want:
        assert 'export PATH="$HOME/.local/bin:$PATH"' in (home / n).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------- PA-uiserver-22
def test_negative_content_length_is_refused_at_once(httpd):
    """PA-uiserver-22: a request with a negative Content-Length is answered 400 at once instead of hanging a thread
    on a read that never ends."""
    t0 = time.time()
    req = (b"POST /api/settings HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Meshpool-Token: " + S.TOKEN.encode()
           + b"\r\nContent-Type: application/json\r\nContent-Length: -5\r\nConnection: close\r\n\r\n")
    out = _raw(httpd, req)
    assert out.startswith(b"HTTP/1.0 400") or out.startswith(b"HTTP/1.1 400"), out[:200]
    assert b"Bad request." in out
    req = (b"POST /api/import-file HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Meshpool-Token: " + S.TOKEN.encode()
           + b"\r\nContent-Length: -1\r\nConnection: close\r\n\r\n")
    out = _raw(httpd, req)
    assert b" 400 " in out.split(b"\r\n", 1)[0], out[:200]
    assert time.time() - t0 < 5


def test_oversized_body_gets_one_true_limit_message(httpd):
    """PA-uiserver-22: a body over the limit is refused before it is read, with the limit the check really uses."""
    req = (b"POST /api/settings HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Meshpool-Token: " + S.TOKEN.encode()
           + b"\r\nContent-Type: application/json\r\nContent-Length: " + str(261 * 1024 * 1024).encode()
           + b"\r\nConnection: close\r\n\r\n{}")
    out = _raw(httpd, req)
    assert b" 400 " in out.split(b"\r\n", 1)[0], out[:200]
    assert b"File is larger than 260 MB." in out
