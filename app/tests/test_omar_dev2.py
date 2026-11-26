"""Omar (developer persona) fixes, part 2: git stash, repeats, discard titles, the -p hint."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import tui_session as TS  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core import sandbox as SB  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402

STASH_TITLE = "Shelve your uncommitted changes (git stash)?"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.delenv("MH_SANDBOX", raising=False)
    monkeypatch.setattr(core.Agent, "_mcp_destructive", lambda self, n: False)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    home = tmp_path / "home"
    p = home / "work"
    p.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    run = lambda *c: subprocess.run(["git", "-C", str(p), *c], check=True, capture_output=True)   # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    (p / "a.py").write_text("x = 1\n")
    (p / "b.py").write_text("y = 1\n")
    run("add", ".")
    run("commit", "-q", "-m", "first")
    return p


def _agent(root, monkeypatch, sandbox, mode="ask"):
    monkeypatch.setattr(SB, "available", lambda *a, **k: sandbox)
    monkeypatch.setenv("MH_SANDBOX_AUTO", "1" if sandbox else "0")
    asked = []

    def ap(kind, title, detail, scope=""):
        asked.append({"kind": kind, "title": title, "scope": scope})
        return ("no", "")
    return Agent(str(root), mode=mode, approve=ap), asked


@pytest.mark.parametrize("sandbox,mode", [(True, "ask"), (False, "edits")])
def test_2_stash_over_the_users_hand_edit_asks_with_a_plain_title(proj, monkeypatch, sandbox, mode):
    (proj / "a.py").write_text("x = 2\n")                       # the user's own uncommitted edit
    a, asked = _agent(proj, monkeypatch, sandbox, mode)
    for c in ("git stash", "git stash push -m wip", "git stash save wip"):
        assert a._permit("bash", {"command": c})[0] is False, c
    assert [q["title"] for q in asked] == [STASH_TITLE] * 3


@pytest.mark.parametrize("sandbox,mode", [(True, "ask"), (False, "edits")])
def test_2_stash_on_a_clean_tree_or_over_the_chats_own_edit_runs(proj, monkeypatch, sandbox, mode):
    a, asked = _agent(proj, monkeypatch, sandbox, mode)
    assert a._permit("bash", {"command": "git stash"}) == (True, "")        # nothing to shelve
    (proj / "a.py").write_text("x = 2\n")
    a.ws.turn_files.add(str(proj / "a.py"))                                 # the chat made this edit
    assert a._permit("bash", {"command": "git stash"}) == (True, "")
    assert asked == []
    (proj / "b.py").write_text("y = 2\n")                                   # and now one it did not make
    assert a._permit("bash", {"command": "git stash"})[0] is False
    assert asked and asked[0]["title"] == STASH_TITLE


def test_2_stash_pop_and_list_never_ask(proj, monkeypatch):
    a, asked = _agent(proj, monkeypatch, False, "edits")
    (proj / "a.py").write_text("x = 2\n")
    a.ws.turn_files.add(str(proj / "a.py"))
    subprocess.run(["git", "-C", str(proj), "stash"], check=True, capture_output=True)
    for c in ("git stash list", "git stash pop"):
        assert a._permit("bash", {"command": c}) == (True, ""), c


def _call(cmd):
    return {"id": "c", "function": {"name": "exec_command", "arguments": json.dumps({"cmd": cmd})}}


def _repeats(proj, monkeypatch, cmds):
    a, _ = _agent(proj, monkeypatch, False, "auto")
    n = iter(range(1000))
    # the output and the tree differ every time (a stash round trip rewrites files), so only the new rule can refuse
    monkeypatch.setattr(a, "_run_tool", lambda c, cut=False: f"ok {next(n)}")
    monkeypatch.setattr(a, "_tree_sig", lambda: next(n))
    g = {}
    return ["ok" if (o := a._repeat_guard(_call(c), g)).startswith("ok") else o for c in cmds]


def test_2_an_identical_command_is_not_run_a_fourth_time_in_a_row(proj, monkeypatch):
    outs = _repeats(proj, monkeypatch, ["git stash"] * 5)
    assert outs[:3] == ["ok"] * 3
    assert "repeating" in outs[3] and "repeating" in outs[4]


def test_2_a_stash_and_pop_round_trip_is_stopped_after_three_stashes(proj, monkeypatch):
    outs = _repeats(proj, monkeypatch, ["git stash", "git stash pop"] * 3)
    assert outs[:5] == ["ok"] * 5
    assert "repeating" in outs[5]


def test_2_different_commands_are_not_a_loop(proj, monkeypatch):
    outs = _repeats(proj, monkeypatch, ["ls", "git status", "ls", "pwd", "ls", "git diff", "ls"])
    assert outs == ["ok"] * 7


# ---------------------------------------------------------------------------------------- 6: discard titles
@pytest.mark.parametrize("cmd,title", [
    ("git checkout -- cli.py report.py test_storage_cli.py", "Discard your changes to 3 files?"),
    ("git restore cli.py report.py", "Discard your changes to 2 files?"),
    ("git checkout -- cli.py", "Discard your changes to cli.py?"),
    ("git checkout .", "Discard all your changes in this folder?"),
    ("git reset --hard", "Discard all your uncommitted changes (git reset --hard)?"),
    ("git clean -fd", "Delete the files git does not track (git clean)?"),
    ("git stash", STASH_TITLE),
    ("python3 -c 'print(1)' && git checkout -- cli.py report.py && tail -3 x.log",
     "Discard your changes to 2 files?"),
])
def test_6_discard_commands_say_what_they_discard(cmd, title):
    assert core._plain_command_title(cmd) == title


def test_6_a_branch_switch_and_unstaging_are_not_discards():
    assert not core._plain_command_title("git checkout main").startswith("Discard")
    assert not core._plain_command_title("git restore --staged a.py").startswith("Discard")


def test_6_git_clean_dry_run_deletes_nothing_and_needs_no_card():
    for c in ("git clean -n", "git clean --dry-run", "git clean -nd", "git clean -n -d"):
        assert T.git_read_only(c), c
    for c in ("git clean", "git clean -f", "git clean -fd", "git clean -d", "git clean -fdx"):
        assert not T.git_read_only(c), c


def test_6_git_clean_dry_run_runs_without_a_card_and_clean_f_asks(proj, monkeypatch):
    a, asked = _agent(proj, monkeypatch, False, "ask")
    assert a._permit("bash", {"command": "git clean -n"}) == (True, "")
    assert a._permit("bash", {"command": "git clean -fd"})[0] is False
    assert asked and asked[0]["title"].startswith("Delete the files git does not track")


def test_6_the_p_hint_for_a_discard_names_the_flag_that_allows_that_command():
    d = TS._denied({"kind": "bash", "title": "Discard your changes to 3 files?",
                    "detail": "git checkout -- cli.py report.py x.py"})
    what, how, kind = TS._allow_hint(d, "ask")
    assert "--allowed-tools" in how and "bash:git checkout -- cli.py report.py x.py" in how
    assert "does not delete" not in how and "--auto" in how
    assert kind == "destructive"


def test_6_the_plain_bash_hint_no_longer_claims_to_exclude_deletes():
    d = TS._denied({"kind": "bash", "title": "Run a Python step?", "detail": "python3 -c 'print(1)' && ls"})
    what, how, kind = TS._allow_hint(d, "ask")
    assert "does not delete" not in how


def _chart_body():
    return "\n".join(["import pandas as pd", "import matplotlib", "matplotlib.use('Agg')",
                      "df = pd.read_csv('sales_2025_clean.csv')", "m = df.groupby('month')", "src = df['month']"] * 5)


def test_6_a_heredoc_script_is_titled_from_what_it_does_not_from_its_lines():
    body = _chart_body()
    two = f"cat > chart.py <<'EOF'\n{body}\nEOF\npython chart.py"
    one = f"python - <<'EOF'\n{body}\nEOF"
    assert core._plain_command_title(two) == "Write chart.py and run it (a Python step on sales_2025_clean.csv)?"
    assert core._plain_command_title(one) == "Run a Python step on sales_2025_clean.csv?"
    for t in (core._plain_command_title(two), core._plain_command_title(one)):
        assert "steps" not in t and "matplotlib" not in t
    assert core._plain_command_title(f"cat > notes.txt <<'EOF'\nhello\nEOF") == "Write notes.txt?"
