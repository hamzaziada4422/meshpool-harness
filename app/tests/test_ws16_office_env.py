"""ws16: engine jobs end as a whole tree (M27-01, M27-02), the office server gets the chat's model and folder per call
(office_mcp_server OJ.start), per-server MESHPOOL_PROJECT (M22-02/M22-05/M16-02/M20-02)."""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness.agent import mcp as M  # noqa: E402
from meshpool_harness.agent import office_jobs as OJ  # noqa: E402


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except OSError:
        return True


def _job(tmp_path, code):
    out = tmp_path / "out"
    out.mkdir()
    jid = "t" + str(time.time_ns())
    OJ.JOBS[jid] = {"engine": "slides", "cmd": [sys.executable, "-c", code], "out": out, "started": time.time(),
                    "build_no": 1, "scope": "x", "request": "r", "paths": [], "event": None, "inputs": set()}
    return jid


GRAND = ("import subprocess, sys, time\n"
         "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
         "open('child.pid', 'w').write(str(p.pid))\n"
         "time.sleep(300)\n")


def _child(tmp_path) -> int:
    f = tmp_path / "out" / "child.pid"
    end = time.time() + 5
    while not f.exists() and time.time() < end:
        time.sleep(0.05)
    return int(f.read_text())


def test_stop_ends_engine_and_everything_it_started(tmp_path):
    import threading
    jid = _job(tmp_path, GRAND)
    stop = threading.Event()
    th = threading.Thread(target=OJ.run, args=(jid, 60, stop.is_set), daemon=True)
    th.start()
    child = _child(tmp_path)
    assert _alive(child)
    stop.set()
    th.join(10)
    assert OJ.JOBS[jid]["stopped"] is True
    end = time.time() + 5
    while _alive(child) and time.time() < end:
        time.sleep(0.05)
    assert not _alive(child), "the engine's child (LibreOffice, a script) kept running after Stop"


def test_timeout_ends_the_whole_tree(tmp_path):
    jid = _job(tmp_path, GRAND)
    OJ.run(jid, timeout=1.2)
    child = int((tmp_path / "out" / "child.pid").read_text())
    end = time.time() + 5
    while _alive(child) and time.time() < end:
        time.sleep(0.05)
    assert not _alive(child)
    assert "longer than" in OJ.JOBS[jid]["stderr"]


def test_office_server_passes_the_chats_model_and_folder(monkeypatch, tmp_path):
    from meshpool_harness.agent import office_mcp_server as S
    seen = {}

    def fake_start(engine, request, paths, root, scope="", model=None):
        seen.update(root=str(root), model=model)
        raise RuntimeError("stop here")

    monkeypatch.setattr(S.OJ, "start", fake_start)
    monkeypatch.setenv("MH_ASK_AUTO", "1")
    monkeypatch.setenv("MH_MODEL", "from-env")
    proj = tmp_path / "p"
    proj.mkdir()
    # through the server loop, as the client sends it: the private arguments are not in the tool's schema
    import io
    import json
    from meshpool_harness.agent import mcp_stdio
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "make_deck", "arguments": {"request": "a deck about our Q3 results", "brief_ok": True,
                                                         "_project": str(proj), "_model": "chat-model"}}}
    out, old_in, old_out = io.StringIO(), sys.stdin, sys.stdout
    try:
        sys.stdin, sys.stdout = io.StringIO(json.dumps(msg) + "\n"), out
        mcp_stdio.serve("t", S.tools())
        end = time.time() + 20
        while "\n" not in out.getvalue() and time.time() < end:
            time.sleep(0.05)
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    assert seen == {"root": str(proj), "model": "chat-model"}, (seen, out.getvalue()[:300])


def test_manager_sends_chat_model_with_per_call_servers(tmp_path, monkeypatch):
    s = M.Server("office", {"command": "x", "per_call_project": True}, tmp_path, [str(tmp_path)])
    sent = {}
    s._rpc = lambda method, params, timeout: sent.update(params) or {}
    s.get_model = lambda: "gpt-chat"
    s.call_with_images("make_deck", {"request": "r"})
    assert sent["arguments"]["_model"] == "gpt-chat" and sent["arguments"]["_project"] == str(tmp_path.resolve())


def test_each_server_gets_its_own_chats_folder_not_the_process_env(tmp_path, monkeypatch):
    seen = []

    def fake_popen(args, **kw):
        seen.append(kw["env"].get("MESHPOOL_PROJECT"))
        raise FileNotFoundError

    monkeypatch.setattr(M.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("MESHPOOL_PROJECT", "/some/other/chat")
    monkeypatch.setenv("MH_LOGIN_ENV", "0")
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        with pytest.raises(M.MCPError):
            M._Stdio("assistant", {"command": "x"}, tmp_path / "logs", [str(tmp_path / d)])
    assert seen == [str((tmp_path / "a").resolve()), str((tmp_path / "b").resolve())]
