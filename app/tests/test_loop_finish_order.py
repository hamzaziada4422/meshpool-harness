from meshpool_harness.core.client import MockClient, tool_call
from meshpool_harness.core.loop import run, Caps
from meshpool_harness.core.tools import Workspace

CTX = {"task_id": "t", "model": "mock", "profile": "deepseek"}


def multi(*calls):
    """One assistant message carrying several tool_calls, in the given order."""
    tcs = []
    for i, (name, args) in enumerate(calls):
        tcs.extend(tool_call(name, args, cid=f"c{i}")["tool_calls"])
    return {"role": "assistant", "content": None, "tool_calls": tcs}


def go(tmp_path, script):
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    c = MockClient(script)
    r = run("task", client=c, model="mock", profile=None, ws=ws, ctx=CTX, system_text="sys",
            caps=Caps(max_turns=8), require_check=False)
    return r, c


def test_finish_before_action_still_runs_action(tmp_path):
    r, c = go(tmp_path, [multi(("finish", {"answer": "done", "evidence": []}),
                               ("write_file", {"path": "out.txt", "content": "hello"}))])
    assert (tmp_path / "out.txt").read_text() == "hello"
    assert r.ok


def test_finish_before_erroring_action_not_accepted(tmp_path):
    r, c = go(tmp_path, [multi(("finish", {"answer": "done", "evidence": []}),
                               ("read_file", {"path": "missing.txt"})),
                         tool_call("finish", {"answer": "really done", "evidence": []})])
    assert len(c.requests) == 2          # first finish was not accepted; the run needed a second turn
    assert r.ok
