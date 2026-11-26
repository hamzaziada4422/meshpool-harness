"""A running command's output reaches the step's card as it comes; the final output is unchanged."""
import json
import os
import sys
import time

import pytest

from meshpool_harness.core import tools as T
from meshpool_harness.agent import core as K


@pytest.mark.skipif(os.name == "nt", reason="uses sh")
def test_output_arrives_while_the_command_runs(tmp_path):
    ws = T.Workspace(str(tmp_path), str(tmp_path / "run"))
    got = []
    ws.on_output = lambda cid, text: got.append((cid, text, time.time()))
    T._LIVE.cid = "call-1"
    t0 = time.time()
    r = ws._proc(["sh", "-c", "for i in 1 2 3 4; do echo line$i; sleep 0.5; done; echo oops 1>&2"], dict(os.environ), 30)
    T._LIVE.cid = ""
    assert r.stdout == "line1\nline2\nline3\nline4\n" and r.stderr == "oops\n" and r.returncode == 0
    assert got and all(c == "call-1" for c, _, _ in got)
    assert got[0][2] - t0 < 1.8                          # the first lines showed before the command ended
    assert "line1" in "".join(t for _, t, _ in got)


def test_quick_commands_send_nothing_live(tmp_path):
    ws = T.Workspace(str(tmp_path), str(tmp_path / "run"))
    got = []
    ws.on_output = lambda cid, text: got.append(text)
    T._LIVE.cid = "c"
    r = ws._proc([sys.executable, "-c", "print('hi')"], dict(os.environ), 30)
    T._LIVE.cid = ""
    assert r.stdout.strip() == "hi" and got == []


@pytest.mark.skipif(os.name == "nt", reason="uses sh")
def test_the_agent_emits_tool_output_with_secrets_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "s")
    ev = []
    a = K.Agent(str(tmp_path), mode="auto", emit=ev.append)
    a._turn_no = 1
    cmd = "echo token=ghp_abcdefghijklmnopqrstuvwxyz0123456789; sleep 1.2; echo done"
    a._run_tool({"id": "c9", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}})
    live = [e for e in ev if e.get("type") == "tool_output"]
    assert live and live[0]["id"] == "c9" and "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in live[0]["text"]
