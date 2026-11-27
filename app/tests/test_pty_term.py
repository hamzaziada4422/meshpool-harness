"""The Terminal panel's shell in a real pseudo-terminal (ui/pty_term.py): start, input and output, resize, the end of
the whole process tree on Stop, on a closed chat and on quit, the routes and their token, the one-command fallback
when no PTY library is there, and xterm.js served from the app itself."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from meshpool_harness.ui import pty_term as PT
from meshpool_harness.ui import workbench as W

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "app" / "meshpool_harness" / "ui" / "vendor"
posix = pytest.mark.skipif(os.name == "nt", reason="ptyprocess and POSIX sessions; Windows uses pywinpty and taskkill")


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: Path(r))
    monkeypatch.setenv("SHELL", "/bin/sh")              # the same small shell everywhere: no rc files, no prompt themes
    monkeypatch.setenv("PS1", "$ ")
    yield tmp_path
    PT.stop_all()
    PT.PTYS.clear()


def _needs_pty():
    pytest.importorskip("ptyprocess")
    if PT.backend() != "ptyprocess":
        pytest.skip("MH_TERMINAL_PTY turns the pseudo-terminal off here")


def _read_until(tid: str, want, secs: float = 15.0, since: int = 0) -> tuple[str, int]:
    text, end = "", time.time() + secs
    while time.time() < end:
        v = PT.read(tid, since, 0.3)
        text, since = text + v["data"], v["next"]
        if (want(text) if callable(want) else want in text) or not v["running"]:
            break
    return text, since


def _gone(pids, secs: float = 10.0) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if not any(PT._alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


@posix
def test_a_shell_starts_runs_what_is_typed_and_shows_colour(folder):
    _needs_pty()
    v = PT.open_pty(str(folder), "chat-1", "", 100, 30)
    assert v["running"] and PT.get(v["id"]).kind == "ptyprocess"
    PT.write(v["id"], "echo MARK$((40 + 2)); printf '\\033[31mred\\033[0m\\n'; tty\r")
    out, _ = _read_until(v["id"], lambda t: "MARK42" in t and "/dev/" in t.split("MARK42", 1)[1])
    assert "MARK42" in out
    assert "\x1b[31mred\x1b[0m" in out                       # colours come through as they are
    assert re.search(r"/dev/(pts/\d+|ttys\d+)", out), out    # a real terminal, not a pipe


@posix
def test_a_prompt_that_waits_for_an_answer_gets_it(folder):
    _needs_pty()
    v = PT.open_pty(str(folder), "chat-1", "", 100, 30)
    PT.write(v["id"], "printf 'Name? '; read n; echo \"hello $n\"\r")
    out, since = _read_until(v["id"], "Name? ")
    PT.write(v["id"], "bob\r")
    out, _ = _read_until(v["id"], "hello bob", since=since)
    assert "hello bob" in out


@posix
def test_resize_reaches_the_shell(folder):
    _needs_pty()
    v = PT.open_pty(str(folder), "chat-1", "", 100, 30)
    PT.write(v["id"], "stty size\r")
    out, since = _read_until(v["id"], "30 100")
    assert "30 100" in out
    assert PT.resize(v["id"], 132, 41) == {"cols": 132, "rows": 41}
    PT.write(v["id"], "stty size\r")
    out, _ = _read_until(v["id"], "41 132", since=since)
    assert "41 132" in out
    assert PT.resize(v["id"], 5, 99999) == {"cols": 20, "rows": 300}      # kept to sizes a terminal can have


@posix
def test_utf8_split_across_reads_is_not_broken(folder):
    _needs_pty()
    v = PT.open_pty(str(folder), "chat-1", "", 100, 30)
    word = "مرحبا é 漢字 "
    PT.write(v["id"], f"i=0; while [ $i -lt 300 ]; do printf '{word}'; i=$((i+1)); done; echo; echo END\r")
    out, _ = _read_until(v["id"], "\nEND", secs=20)
    assert "�" not in out and out.count(word) >= 290


@posix
def test_the_same_chat_and_folder_find_their_shell_again(folder):
    _needs_pty()
    a = PT.open_pty(str(folder), "chat-1", "", 80, 24)
    PT.write(a["id"], "echo first-output\r")
    _read_until(a["id"], "first-output")
    again = PT.open_pty(str(folder), "chat-1", "", 90, 20)       # the panel opened again, or the page reloaded
    assert again["id"] == a["id"] and "first-output" in again["data"]
    other = PT.open_pty(str(folder), "chat-2", "", 80, 24)       # another chat has its own
    assert other["id"] != a["id"]
    fresh = PT.open_pty(str(folder), "chat-1", "", 80, 24, new=True)
    assert fresh["id"] != a["id"]


@posix
def test_stop_ends_the_shell_and_every_process_it_started(folder):
    _needs_pty()
    v = PT.open_pty(str(folder), "chat-1", "", 100, 30)
    # a background job, a job in its own subshell, a process that leaves the shell's process group, and a foreground one
    PT.write(v["id"], f"sleep 301 & (sleep 302; true) & {sys.executable} -c 'import os, time; os.setpgid(0, 0); "
                      f"time.sleep(303)' & sh -c 'sleep 304'\r")
    p = PT.get(v["id"])
    end = time.time() + 10
    while time.time() < end and len(PT._descendants(p.pid)) < 4:
        time.sleep(0.1)
    kids = PT._descendants(p.pid)
    assert len(kids) >= 4, kids
    t0 = time.time()
    view = PT.stop(v["id"])
    assert not view["running"] and time.time() - t0 < 6
    assert _gone([p.pid, *kids]), [k for k in kids if PT._alive(k)]


@posix
def test_a_closed_chat_ends_its_shells_and_commands_only(folder):
    _needs_pty()
    mine = PT.open_pty(str(folder), "chat-1", "", 80, 24)
    other = PT.open_pty(str(folder), "chat-2", "", 80, 24)
    cmd = W.term_run(str(folder), "sleep 305", owner="chat-1")
    W.term_stop_owner("chat-1")
    assert not PT.read(mine["id"])["running"]
    assert PT.read(other["id"])["running"]
    W.TERMS[cmd["id"]]["p"].wait(5)
    assert W.TERMS[cmd["id"]]["p"].poll() is not None


@posix
def test_session_close_ends_the_chats_shells_but_a_dropped_copy_does_not(folder):
    _needs_pty()
    from meshpool_harness.ui import agent_api as A
    s = A.Session.__new__(A.Session)
    s.id, s.mcp, s.agent = "chat-9", None, object()
    s.stop = lambda: None
    v = PT.open_pty(str(folder), "chat-9", "", 80, 24)
    s.close(end_terminals=False)                         # another copy of a chat that stays open
    assert PT.read(v["id"])["running"]
    assert PT.owns_live("chat-9")
    s.close()
    assert not PT.read(v["id"])["running"] and not PT.owns_live("chat-9")


@posix
def test_quitting_the_app_ends_every_shell(folder):
    _needs_pty()
    vs = [PT.open_pty(str(folder), f"c{i}", "", 80, 24) for i in range(3)]
    pids = [PT.get(v["id"]).pid for v in vs]
    from meshpool_harness.ui import agent_api as A
    t0 = time.time()
    A.close_all()
    assert time.time() - t0 < 10
    assert _gone(pids)


@posix
def test_an_ended_shell_says_so_and_takes_no_input(folder):
    _needs_pty()
    v = PT.open_pty(str(folder), "chat-1", "", 80, 24)
    PT.write(v["id"], "exit 3\r")
    end = time.time() + 10
    while time.time() < end and PT.read(v["id"])["running"]:
        time.sleep(0.1)
    r = PT.read(v["id"])
    assert not r["running"] and r["rc"] == 3
    with pytest.raises(ValueError, match="ended"):
        PT.write(v["id"], "echo x\r")
    with pytest.raises(KeyError):
        PT.read("nosuchshell")


def test_the_backlog_keeps_offsets_when_old_output_is_dropped(monkeypatch):
    p = PT.Pty.__new__(PT.Pty)
    p.id, p.chunks, p.base, p.end, p.size, p.rc, p.cols, p.rows, p.shell, p.root = "x", [], 0, 0, 0, None, 80, 24, "", "/"
    p.cond = threading.Condition()
    monkeypatch.setattr(PT, "BACKLOG", 100)
    monkeypatch.setattr(PT, "CHUNK", 10)
    for i in range(50):
        p._append(f"{i:04d}" + "-" * 6)                  # 10 characters each, 500 in all
    v = p.view(0)
    assert v["next"] == 500 and v["lost"] > 0 and len(v["data"]) <= 100
    assert p.view(495)["data"] == "-" * 5
    assert p.view(10_000)["data"] == v["data"]          # an offset from an older shell starts again


def test_windows_ends_the_tree_with_taskkill_and_then_any_child_left(monkeypatch):
    """Not run on Windows here: the calls the Windows path makes, with the system faked."""
    ran, killed = [], []

    class Proc:
        def __init__(self, pid):
            self.pid = pid

        def kill(self):
            killed.append(self.pid)
    import psutil
    monkeypatch.setattr(PT.os, "name", "nt")
    monkeypatch.setattr(PT, "_descendants", lambda pid: [11, 12])
    monkeypatch.setattr(PT, "_alive", lambda pid: pid == 12)          # taskkill missed one child
    monkeypatch.setattr(PT.subprocess, "run", lambda argv, **kw: ran.append((argv, kw.get("creationflags"))))
    monkeypatch.setattr(psutil, "Process", Proc)
    PT.kill_tree(10)
    assert ran == [(["taskkill", "/F", "/T", "/PID", "10"], PT.NO_WINDOW)] and killed == [12]


# ---------------------------------------------------------------- without a PTY library: one command at a time
def test_without_a_pty_library_the_panel_runs_one_command_at_a_time(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "ptyprocess", None)          # import fails as if it were not installed
    monkeypatch.setitem(sys.modules, "winpty", None)
    assert PT.backend() == "" and PT.info() == {"pty": None}
    monkeypatch.setattr(W, "check_root", lambda r: Path(r))
    with pytest.raises(ValueError, match="one command at a time"):
        PT.open_pty(str(tmp_path), "chat-1")
    monkeypatch.setattr(W, "_shell_cmd", lambda c, s="": [sys.executable, "-c", c])
    v = W.term_run(str(tmp_path), "print('still works')")          # the one-command mode is what the page uses then
    end = time.time() + 10
    while time.time() < end and W.term_view(v["id"], 0)["running"]:
        time.sleep(0.05)
    assert "still works" in "".join(W.term_view(v["id"], 0)["lines"])


def test_the_pty_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("MH_TERMINAL_PTY", "0")
    assert PT.backend() == ""


def test_the_shell_for_each_system():
    assert PT.shell_argv("", nt=True) == ["powershell.exe", "-NoLogo"]
    assert PT.shell_argv("cmd", nt=True)[0].lower().endswith("cmd.exe")
    sh = PT.shell_argv("", nt=False)
    assert sh[-1] == "-l" and os.path.isabs(sh[0])


# ---------------------------------------------------------------- the routes and the page's assets
@pytest.fixture
def server(monkeypatch, tmp_path):
    from meshpool_harness.ui import server as SV
    monkeypatch.setattr(W, "check_root", lambda r: Path(r))
    monkeypatch.setenv("SHELL", "/bin/sh")
    httpd = SV.ThreadingHTTPServer(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield SV, httpd.server_address[1], tmp_path
    httpd.shutdown()
    PT.stop_all()
    PT.PTYS.clear()


def _call(port, path, body=None, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"X-Meshpool-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_the_pty_routes_need_the_page_token(server):
    SV, port, tmp = server
    for path, body in (("/api/pty", {"root": str(tmp)}), ("/api/pty/abc/input", {"data": "rm -rf ~\r"}),
                       ("/api/pty/abc/resize", {"cols": 1}), ("/api/pty/abc/stop", {})):
        assert _call(port, path, body)[0] == 403, path
    assert _call(port, "/api/pty/abc?since=0")[0] == 403
    assert not PT.PTYS


@posix
def test_the_pty_routes_run_a_shell(server):
    _needs_pty()
    SV, port, tmp = server
    st, raw, _ = _call(port, "/api/pty", {"root": str(tmp), "owner": "c1", "cols": 90, "rows": 25}, SV.TOKEN)
    assert st == 200
    tid = json.loads(raw)["id"]
    assert _call(port, f"/api/pty/{tid}/input", {"data": "echo via-$((1+1))-route\r"}, SV.TOKEN)[0] == 200
    since, text, end = 0, "", time.time() + 15
    while "via-2-route" not in text and time.time() < end:
        v = json.loads(_call(port, f"/api/pty/{tid}?since={since}&wait=0.5", token=SV.TOKEN)[1])
        text, since = text + v["data"], v["next"]
    assert "via-2-route" in text
    assert json.loads(_call(port, f"/api/pty/{tid}/resize", {"cols": 100, "rows": 30}, SV.TOKEN)[1]) == {"cols": 100, "rows": 30}
    assert json.loads(_call(port, f"/api/pty/{tid}/stop", {}, SV.TOKEN)[1])["running"] is False
    assert _call(port, "/api/pty/nosuch?since=0", token=SV.TOKEN)[0] == 404
    s = json.loads(_call(port, "/api/settings", token=SV.TOKEN)[1])
    assert s["terminal"] == {"pty": "ptyprocess"}


def test_xterm_is_bundled_and_served_from_the_app(server):
    SV, port, _ = server
    for f in ("xterm.min.js", "xterm-addon-fit.min.js", "xterm.css", "LICENSE-xterm.txt", "LICENSE-xterm-addon-fit.txt"):
        assert (VENDOR / f).is_file(), f
    js = (VENDOR / "xterm.min.js").read_text(encoding="utf-8")
    assert "e.Terminal=" in js or "Terminal=" in js
    assert "sourceMappingURL" not in js and "sourceMappingURL" not in (VENDOR / "xterm-addon-fit.min.js").read_text()
    for path, ctype in (("/ui/vendor/xterm.min.js", "javascript"), ("/ui/vendor/xterm-addon-fit.min.js", "javascript"),
                        ("/ui/vendor/xterm.css", "text/css")):
        st, body, hdr = _call(port, path)
        assert st == 200 and ctype in hdr.get("Content-Type", "") and len(body) > 1000, path
    app_js = (ROOT / "app" / "meshpool_harness" / "ui" / "app.js").read_text(encoding="utf-8")
    assert '"/ui/vendor/xterm.min.js"' in app_js and "cdn" not in app_js[app_js.index("function ptyAssets"):][:800].lower()
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "xterm.js 6.0.0" in notices and "LICENSE-xterm.txt" in notices
    assert "Permission is hereby granted" in (VENDOR / "LICENSE-xterm.txt").read_text(encoding="utf-8")


def test_the_agent_never_types_into_the_terminal():
    """The shell takes only the person's keys: no agent tool or module other than the page routes writes to it."""
    pkg = ROOT / "app" / "meshpool_harness"
    users = sorted(str(p.relative_to(pkg)) for p in pkg.rglob("*.py")
                   if "pty_term" in p.read_text(encoding="utf-8", errors="replace") and p.name != "pty_term.py")
    assert users == ["ui/agent_api.py", "ui/server.py", "ui/workbench.py"], users
    for name in ("agent_api.py", "workbench.py"):
        src = (pkg / "ui" / name).read_text(encoding="utf-8")
        assert "PT.write(" not in src and "pty_term.write" not in src
