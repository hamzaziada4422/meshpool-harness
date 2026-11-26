"""Bugs a backend developer persona (Omar: `meshpool -p`, the terminal UI and web Code chats) hit on 0.0.18, IDs
OM-01 to OM-10. Scripted model replies or the mock in testkit; no paid model is called."""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meshpool_harness import capabilities as CAP  # noqa: E402
from meshpool_harness import chats  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import tui as T  # noqa: E402
from meshpool_harness.ui import agent_api as A  # noqa: E402


# ---------------------------------------------------------------------------------------------------- OM-02
@pytest.mark.parametrize("text", [
    "make report.py print the totals by month",
    "make the tests in report.py pass",
    "update the docs for the cli and make the examples run",
    "make the docs build pass",
    "fix docs/index.md and make the links work",
    "add a usage section to README.md and make it short",
    "write docs for dedupe.py",
    "make the src/reports/ folder a package",
])
def test_om02_a_code_file_or_the_word_docs_is_not_a_word_document(text):
    """OM-02: "report" in report.py and the word "docs" counted as a request for a Word document, so a code request
    got a hidden note telling the agent to build one."""
    assert CAP.needs(text) == [], text


@pytest.mark.parametrize("text", ["write a report on Q3 sales", "make a Word doc of the notes", "I need a .docx",
                                  "make me a doc summarising the budget", "make report.docx with the totals",
                                  "export it as report.pdf"])
def test_om02_real_requests_for_a_document_still_count(text):
    assert CAP.needs(text), text


# ---------------------------------------------------------------------------------------------------- shared set-up
def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


@pytest.fixture
def home(monkeypatch, tmp_path):
    """A fresh meshpool home and sessions folder (as in test_ws23_cli_session.py)."""
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test-key-123456")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    sessions = tmp_path / "runs" / "sessions"
    monkeypatch.setattr(core, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(chats, "SESSIONS", sessions)
    for k in ("MESHPOOL_FRONTEND", "MH_PERMISSION_MODE", "MH_AGENT_PROFILE", "MH_ALLOW", "MH_DENY", "MH_MODEL",
              "MH_SANDBOX"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    A.LIVE.clear()
    yield tmp_path
    for s in list(A.LIVE.values()):
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
    A.LIVE.clear()


def script(monkeypatch, *replies):
    """The model says `replies` in order (a dict is the assistant message; a str is plain text)."""
    it = iter([{"role": "assistant", "content": r} if isinstance(r, str) else r for r in replies])
    seen = []

    def fake(self, turn):
        for k, v in {"in": 100, "cached": 0, "out": 10, "calls": 1}.items():
            turn[k] += v
            self.usage[k] += v
        seen.append([dict(m) for m in self.messages])
        return next(it), False
    monkeypatch.setattr(core.Agent, "_chat", fake)
    return seen


@pytest.fixture
def proj(home):
    p = home / "proj"
    (p / "ledgerly").mkdir(parents=True)
    (p / "ledgerly" / "storage.py").write_text("FIELDS = ['date']\n")
    return p


# ---------------------------------------------------------------------------------------------------- OM-01
OM01_CMDS = ["find . -name '*.py' | head -5 && cat ledgerly/storage.py", "python3 -m pytest -q"]


def _om01_run(monkeypatch, proj, capfd):
    script(monkeypatch, *[_call("bash", {"command": c}, f"c{i}") for i, c in enumerate(OM01_CMDS)],
           _call("write_file", {"path": "notes.md", "content": "hi\n"}, "c9"),
           "I looked at storage.py.")
    rc = T.main(str(proj), no_mcp=True, prompt="explain ledgerly/storage.py", json_out="json")
    cap = capfd.readouterr()
    return rc, cap.err, json.loads(cap.out.strip().splitlines()[-1])


def test_om01_p_names_each_refused_command_and_a_flag_that_really_allows_it(monkeypatch, proj, capfd):
    """OM-01: `meshpool -p` without a sandbox refused find, a pipe and &&; the summary said "Run this command?" five
    times and recommended --permission-mode edits, under which no command runs. Now each refused command is named,
    with a rule the permission code accepts for it, and edits mode is offered only for the file edit."""
    from meshpool_harness.core import sandbox as SB
    monkeypatch.setattr(SB, "available", lambda *a, **k: False)     # Windows, or Linux without bubblewrap
    rc, err, res = _om01_run(monkeypatch, proj, capfd)
    assert rc == 3 and len(res["denied"]) == 3, err
    note = err[err.index("action(s) were not allowed"):]
    assert "Run this command?" not in note
    for c in OM01_CMDS:
        assert f"`{c}`" in note, c
    assert '--allowed-tools "bash:python3 -m pytest*"' in note
    assert "--allowed-tools bash (every command" in note                  # a pipe or && needs the whole tool
    assert note.count("--permission-mode edits") == 1 and "Write notes.md: --permission-mode edits" in note
    assert "command sandbox" in note
    # the advice works: the same run with the rules it named refuses nothing
    A.LIVE.clear()
    monkeypatch.setenv("MH_ALLOW", "bash")                             # --allowed-tools bash
    monkeypatch.setenv("MH_PERMISSION_MODE", "edits")                  # --permission-mode edits
    rc, err, res = _om01_run(monkeypatch, proj, capfd)
    assert rc == 0 and res["denied"] == [] and (proj / "notes.md").exists(), err


def test_om01_in_the_sandbox_p_runs_ordinary_commands_without_a_question(monkeypatch, proj, capfd):
    """With the OS sandbox (0.0.18's approvals redesign), -p in the default mode runs find, pipes and && in the
    folder: nothing is refused."""
    from meshpool_harness.core import sandbox as SB
    monkeypatch.setenv("MH_SANDBOX_AUTO", "1")                         # the shipped default (conftest turns it off)
    monkeypatch.setattr(SB, "available", lambda *a, **k: True)
    rc, err, res = _om01_run(monkeypatch, proj, capfd)
    assert rc == 0 and res["denied"] == [] and "not allowed" not in err, err
