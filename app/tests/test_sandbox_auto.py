"""0.0.18: ordinary work in the chat's folder runs without a question (owner: "way too many approvals for normal users").
Persona runs on 0.0.18 asked 6 times to summarise a PDF and about 16 times in a data job. Now, as in Codex's default
(workspace-write sandbox) and Claude's: inside the OS sandbox, commands and edits in the folder run without asking,
the commands with the network cut; without a sandbox (Windows) one "Allow ordinary commands and edits in this folder
in this chat" covers the rest of the chat. Destructive commands, the network and installs, paths elsewhere in the
user's home, meshpool's own rule files and anything outside the folder still ask."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core import permrules as PR  # noqa: E402
from meshpool_harness.core import sandbox as SB  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MH_SANDBOX_AUTO", "1")
    monkeypatch.delenv("MH_SANDBOX", raising=False)


def _agent(root, sandbox: bool, monkeypatch, answer=("no", "")):
    monkeypatch.setattr(SB, "available", lambda *a, **k: sandbox)
    asked = []

    def ap(kind, title, detail, scope=""):
        asked.append({"kind": kind, "title": title, "scope": scope})
        return answer
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return Agent(str(root), mode="ask", approve=ap), asked


def _home_project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "work"
    proj.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return proj


ORDINARY = ["python3 -c \"import openpyxl; print('ok')\"", "python3 analyze.py data.csv > summary.txt",
            "ls -la && head -5 notes.md | wc -l", "soffice --headless --convert-to pdf report.docx",
            "unzip -o deck.pptx -d /tmp/deck", "npm run build"]
STILL_ASK = ["pip install pandas", "curl https://example.com/x", "git push origin main", "npm install left-pad",
             "cat ~/.ssh/id_rsa", "rm -rf src", "sudo ls", "cp report.docx ~/Desktop/"]


def test_in_the_sandbox_ordinary_commands_run_without_a_question(tmp_path, monkeypatch):
    proj = _home_project(tmp_path, monkeypatch)
    a, asked = _agent(proj, True, monkeypatch)
    for c in ORDINARY:
        ok, _ = a._permit("bash", {"command": c})
        assert ok, c
    assert asked == []
    assert set(a.ws.__dict__["sbx_offline"]) == {c.strip() for c in ORDINARY}     # these run with the network cut


def test_in_the_sandbox_risky_commands_still_ask(tmp_path, monkeypatch):
    proj = _home_project(tmp_path, monkeypatch)
    a, asked = _agent(proj, True, monkeypatch)
    for c in STILL_ASK:
        ok, _ = a._permit("bash", {"command": c})
        assert not ok, c                                    # asked, and the test's answer is no
    assert len(asked) == len(STILL_ASK)
    assert not (a.ws.__dict__.get("sbx_offline") or set()) & set(STILL_ASK)


def test_in_the_sandbox_edits_in_the_folder_run_without_a_question_and_outside_ask(tmp_path, monkeypatch):
    proj = _home_project(tmp_path, monkeypatch)
    a, asked = _agent(proj, True, monkeypatch)
    assert a._permit("write_file", {"path": "out/summary.md", "content": "x"})[0]
    assert a._permit("apply_patch", {"input": "*** Begin Patch\n*** Add File: a.txt\n+hi\n*** End Patch\n"})[0]
    assert asked == []
    ok, _ = a._permit("write_file", {"path": str(tmp_path / "elsewhere.txt"), "content": "x"})
    assert not ok and asked                                 # outside the folder: asked
    asked.clear()
    ok, _ = a._permit("write_file", {"path": ".meshpool/permissions.json", "content": "{}"})
    assert not ok and asked                                 # meshpool's own rule file: always asked


def test_lean_shell_and_codex_exec_command_get_the_same_treatment(tmp_path, monkeypatch):
    proj = _home_project(tmp_path, monkeypatch)
    a, asked = _agent(proj, True, monkeypatch)
    assert a._permit("shell", {"command": ["bash", "-lc", "python3 analyze.py"]})[0]
    assert a._permit("exec_command", {"cmd": "python3 analyze.py --chart"})[0]
    assert not a._permit("exec_command", {"cmd": "pip install seaborn"})[0]
    assert len(asked) == 1


def test_without_a_sandbox_one_yes_covers_the_chat(tmp_path, monkeypatch):
    proj = _home_project(tmp_path, monkeypatch)
    a, asked = _agent(proj, False, monkeypatch, answer=("always", ""))
    assert a._permit("bash", {"command": "python3 analyze.py data.csv"})[0]
    assert len(asked) == 1 and asked[0]["scope"] == "ordinary commands and edits in this folder"
    for c in ORDINARY:
        assert a._permit("bash", {"command": c})[0]
    assert a._permit("write_file", {"path": "chart.png", "content": "x"})[0]
    assert len(asked) == 1                                  # one question for the whole chat
    assert "sbx_offline" not in a.ws.__dict__               # no sandbox: nothing is run offline
    a._permit("bash", {"command": "curl https://example.com"})
    a._permit("bash", {"command": "rm -rf src"})
    assert len(asked) == 3                                  # the network and a destructive command still ask


def test_off_by_setting_and_never_in_full_access_changes(tmp_path, monkeypatch):
    proj = _home_project(tmp_path, monkeypatch)
    monkeypatch.setenv("MH_SANDBOX_AUTO", "0")
    a, asked = _agent(proj, True, monkeypatch)
    assert not a._permit("bash", {"command": "python3 analyze.py"})[0] and len(asked) == 1


def test_the_sandbox_cuts_the_network_only_for_commands_that_ran_without_a_question(tmp_path):
    argv = SB.wrap(["bash", "-c", "x"], tmp_path, platform="linux", which=lambda n: "/usr/bin/bwrap", mode="ask",
                   offline=True)
    assert "--unshare-net" in argv
    argv = SB.wrap(["bash", "-c", "x"], tmp_path, platform="linux", which=lambda n: "/usr/bin/bwrap", mode="ask")
    assert "--unshare-net" not in argv
    prof = SB.wrap(["x"], tmp_path, platform="darwin", which=lambda n: "/usr/bin/sandbox-exec", mode="ask",
                   offline=True)[2]
    assert "(deny network*)" in prof


def test_paths_in_the_users_home_outside_the_project_still_ask(tmp_path, monkeypatch):
    proj = _home_project(tmp_path, monkeypatch)
    assert PR.sandbox_auto_ok("cat notes.md", proj) and PR.sandbox_auto_ok("cat /etc/hosts", proj)
    assert not PR.sandbox_auto_ok("cat ~/Documents/tax.pdf", proj)
    assert not PR.sandbox_auto_ok("python3 x.py ../other/secret.txt", proj)
    assert not PR.sandbox_auto_ok("ls", proj, cwd=str(tmp_path))          # a workdir outside the project
