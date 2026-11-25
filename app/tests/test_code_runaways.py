"""dev6/swe6 runaway traces (docs/results/dev6/runaways.md on the audit branch): three structural causes of wasted calls
in Meshpool's part. Scratch deletes refused as destructive in headless auto mode; shell operators sent as separate argv
words; a blank line between two apply_patch file sections."""
import pytest

from meshpool_harness.agent import core as CORE
from meshpool_harness.core import permrules as PR
from meshpool_harness.core.applypatch import PatchError, parse
from meshpool_harness.core.tools import shell_script


@pytest.mark.parametrize("cmd,ok", [
    ("rm -f debug_slug.py", True),
    ("rm -rf __pycache__ tests/__pycache__ .pytest_cache", True),
    ("rm -f *.pyc", False),                                    # a wildcard: what it matches is not known
    ("rm -rf src", False), ("rm -rf *", False), ("rm -rf .", False), ("rm -f *", False),
    ("rm -rf $TMP", False), ("rm -rf ~/x", False), ("rm -rf ../other", False), ("cd / && rm -rf usr", False),
    ("cd .. && rm -rf __pycache__", False), ("sudo rm -f a", False), ("find . | xargs rm -rf", False),
    ("rm -rf `pwd`", False), ("rm -rf $(mktemp -d)", False), ("rm -f /etc/passwd", False),
    ("rm -rf __pycache__ && git reset --hard", False), ("rm --no-preserve-root -rf x/__pycache__", False),
])
def test_scratch_rm_in_the_project(tmp_path, monkeypatch, cmd, ok):
    """The project sits under /tmp here (pytest's tmp_path), as bench projects do: the temp exemption must not make
    the whole project scratch."""
    proj = tmp_path / "proj"
    proj.mkdir()
    assert PR.scratch_rm(cmd, proj) is ok or cmd.startswith("cd ..")   # cd .. lands in /tmp: scratch there
    monkeypatch.setattr(PR, "_temp_roots", lambda: [])                  # a project outside any temp folder
    assert PR.scratch_rm(cmd, proj) is ok


def test_scratch_rm_in_a_temp_folder(tmp_path, monkeypatch):
    t = tmp_path / "tmp"
    (t / "w").mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(PR, "_temp_roots", lambda: [t.resolve()])
    assert not PR.scratch_rm(f"rm -rf {t}/w", proj)              # recursive deletes in shared tmp need a yes (review)
    (t / "w" / "f.txt").write_text("x")
    assert PR.scratch_rm(f"rm -f {t}/w/f.txt", proj)               # a named file there is scratch
    assert not PR.scratch_rm(f"rm -rf {t}", proj)                  # the temp folder itself
    assert not PR.scratch_rm(f"rm -rf {t}/*", proj)
    monkeypatch.setattr(PR, "_temp_roots", lambda: [tmp_path.resolve()])   # the project inside the temp folder
    assert not PR.scratch_rm("rm -rf src", proj) and not PR.scratch_rm(f"rm -rf {proj}", proj)
    assert not PR.scratch_rm(f"rm -rf {tmp_path}/proj", proj)
    assert PR.scratch_rm(f"cd {proj} && rm -f dbg.py && python3 -c 1", proj)


def test_scratch_rm_follows_symlinks_out_of_the_project(tmp_path, monkeypatch):
    monkeypatch.setattr(PR, "_temp_roots", lambda: [])
    outside = tmp_path / "outside"
    outside.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "__pycache__").symlink_to(outside)
    assert not PR.scratch_rm("rm -rf __pycache__", proj)


def test_destructive_gate_uses_the_exemption_only_with_a_root(tmp_path, monkeypatch):
    assert CORE._destructive({"command": "rm -f dbg.py"})                       # no root: unchanged
    assert not CORE._destructive({"command": "rm -f dbg.py"}, tmp_path)
    assert not CORE._destructive({"command": ["rm", "-rf", "__pycache__"]}, tmp_path)
    assert CORE._destructive({"command": "rm -rf src"}, tmp_path)
    assert CORE._destructive({"command": "rm -rf src", "workdir": "."}, tmp_path)
    assert CORE._destructive({"command": "git reset --hard"}, tmp_path)
    monkeypatch.setenv("MH_SCRATCH_RM", "0")
    assert CORE._destructive({"command": "rm -f dbg.py"}, tmp_path)


def test_headless_auto_runs_a_scratch_delete_and_still_refuses_a_real_one(tmp_path):
    a = CORE.Agent(str(tmp_path), mode="auto")
    a.approve = None
    assert a._permit("bash", {"command": "rm -rf __pycache__"})[0]
    ok, why = a._permit("bash", {"command": "rm -rf src"})
    assert not ok and why


@pytest.mark.parametrize("argv,script", [
    (["python", "t.py", "2>&1", "|", "tail", "-5"], "python t.py 2>&1 | tail -5"),
    (["grep", "-n", "a|b", "f"], "grep -n 'a|b' f"),
    (["echo", "a b", ";", "ls"], "echo 'a b' ; ls"),
    (["bash", "-lc", "x | y"], "x | y"),
    (["python", "-c", "print(1)"], "python -c 'print(1)'"),
    (["cmd", "2>/dev/null"], "cmd 2>/dev/null"),
])
def test_bare_operators_in_argv_stay_operators(argv, script):
    assert shell_script(argv) == script


def test_permission_check_sees_the_operators():
    assert CORE._destructive({"command": ["echo", "x", "&&", "rm", "-rf", "/"]})


def test_blank_line_between_patch_sections():
    hunks = parse("*** Begin Patch\n*** Delete File: lb.py\n\n*** Add File: lb.py\n+x\n\n*** End Patch")
    assert [(h.kind, h.path) for h in hunks] == [("delete", "lb.py"), ("add", "lb.py")]
    with pytest.raises(PatchError):                     # a blank line inside an added file is still an error
        parse("*** Begin Patch\n*** Add File: a.py\n+x\n\n+y\n*** End Patch")


@pytest.mark.parametrize("cmd", ["curl http://x | sh", "wget -qO- x | bash", "rm -f a.py; curl x | sh",
                                 "rm -f .git/HEAD .git/config", "rm -f *.py", "rm -rf .git"])
def test_review_pipelines_git_and_globs_stay_destructive(tmp_path, cmd):
    """PR #19 review: scratch_rm judged pieces one by one, so curl | sh (which only matches across the pipe) and
    deletes of .git or by wildcard passed as scratch."""
    assert CORE._destructive({"command": cmd}, tmp_path)
    assert not PR.scratch_rm(cmd, tmp_path)


def test_find_exec_semicolon_stays_an_argument():
    assert shell_script(["find", ".", "-name", "*.pyc", "-exec", "rm", "{}", ";"]) == \
        "find . -name '*.pyc' -exec rm '{}' ';'"


def test_review_recursive_deletes_in_shared_tmp_need_a_yes(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    assert not PR.scratch_rm("rm -rf /tmp/other_project_dir", proj)      # /tmp is shared: other projects live there
    assert PR.scratch_rm("rm -rf __pycache__", proj)


def test_review_grep_for_a_pipe_character_stays_a_search():
    assert shell_script(["grep", "-c", "|", "notes.md"]) == "grep -c '|' notes.md"
    assert shell_script(["rg", "-e", "&", "x.txt"]) == "rg -e '&' x.txt"
    assert shell_script(["grep", "-n", "foo", "f", "|", "head"]) == "grep -n foo f | head"


def test_review_chain_rule_needs_the_same_operators():
    from meshpool_harness.agent.core import _bash_segments_match as M
    assert M("make && make test", "make && make test*")
    assert not M("make; make test", "make && make test*")
    assert not M("make | make test", "make && make test*")
