"""Synthetic chats in a tmp Meshpool home, shared by the summaries and Your work tests."""
import json
import time

from meshpool_harness import config as C
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import hub as HUB


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "userhome"))
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.SESSIONS.mkdir()
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "_METAS", {})
    monkeypatch.setattr(A, "_ROWS", {})
    monkeypatch.setattr(HUB, "_ART", {})
    C.ensure_home()


def chat(tmp_path, sid, users, replies=(), title=None, updated=None, files=(), harness="meshpool-work", model="m1",
         tools=(), **meta):
    """A saved chat. `users` and `replies` alternate; `files` are written in the chat's folder and recorded as tool
    calls; `tools` are extra tool-call names (for connector counts)."""
    d = A.SESSIONS / sid
    d.mkdir(parents=True, exist_ok=True)
    wd = tmp_path / ("wd-" + sid)
    wd.mkdir(exist_ok=True)
    msgs = [{"role": "system", "content": "x"}]
    for i, u in enumerate(users):
        msgs.append({"role": "user", "content": u})
        if i < len(replies):
            msgs.append({"role": "assistant", "content": replies[i]})
    calls = []
    for i, f in enumerate(files):
        (wd / f).write_bytes(b"x" * 20)
        calls.append({"id": f"c{i}", "type": "function",
                      "function": {"name": "write_file", "arguments": json.dumps({"path": f, "content": "hi"})}})
    for j, t in enumerate(tools):
        calls.append({"id": f"t{j}", "type": "function", "function": {"name": t, "arguments": "{}"}})
    if calls:
        msgs.append({"role": "assistant", "content": "", "tool_calls": calls})
        msgs += [{"role": "tool", "tool_call_id": c["id"], "content": "Saved"} for c in calls]
    (d / "session.json").write_text(json.dumps({"id": sid, "title": title or users[0][:40] if users else "x",
                                                "workdir": str(wd), "updated": updated or time.time(),
                                                "kind": "cowork", "harness": harness, "model": model,
                                                "messages": msgs}))
    (d / "meta.json").write_text(json.dumps(dict({"kind": "cowork"}, **meta)))
    return sid
