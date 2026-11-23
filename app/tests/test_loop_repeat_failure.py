from meshpool_harness.core.client import MockClient, tool_call
from meshpool_harness.core.loop import run, Caps
from meshpool_harness.core.tools import Workspace

CTX = {"task_id": "t", "model": "mock", "profile": "deepseek"}


def test_decreasing_failures_do_not_abort(tmp_path):
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    # same command string each turn; output varies via a counter file (7 -> 3 issues)
    cmd = ("python -c \"import pathlib;p=pathlib.Path('n.txt');n=int(p.read_text()) if p.exists() else 7;"
           "p.write_text(str(n-1));print('FAIL: %d issues'%n);raise SystemExit(1)\"")
    script = [tool_call("bash", {"command": cmd}) for _ in range(5)]
    script.append(tool_call("finish", {"answer": "gave up", "evidence": []}))
    c = MockClient(script)
    r = run("check", client=c, model="mock", profile=None, ws=ws, ctx=CTX, system_text="sys",
            caps=Caps(max_turns=12), require_check=False)
    assert "abort" not in str(getattr(r, "reason", "")).lower()
    assert len(c.requests) >= 6
