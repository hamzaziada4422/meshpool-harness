"""X-09: the loop graders (bench.grade, swe_local.grade) grade a scratch copy and never modify the workspace."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTKIT = Path(__file__).resolve().parents[1]
LOOP = Path(os.environ.get("LOOP_GRADERS_DIR") or TESTKIT / "loop")    # override: run against an older copy
sys.path.insert(0, str(LOOP))
sys.path.insert(0, str(TESTKIT))

import bench  # noqa: E402
import swe_local  # noqa: E402
import gradekit as GK  # noqa: E402

PY = sys.executable
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def _git(cwd, *a):
    return subprocess.run([*GIT, *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _task_dir(tmp_path):
    d = tmp_path / "wd"
    d.mkdir()
    (d / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (d / "notes.txt").write_text("agent notes\n")
    return d


HIDDEN = {"test_hidden.py": "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"}


def test_bench_grade_leaves_workdir_untouched(tmp_path):
    d = _task_dir(tmp_path)
    task = {"grade": {"hidden": HIDDEN, "check": f"{PY} -m pytest -q -p no:cacheprovider test_hidden.py",
                      "file_must": {"calc.py": "def add"}}}
    before = GK.tree_digest(d)
    ok, why = bench.grade(task, "", str(d))
    assert ok, why
    assert GK.tree_digest(d) == before
    assert not (d / "test_hidden.py").exists()


def test_bench_grade_still_fails_when_code_is_wrong(tmp_path):
    d = _task_dir(tmp_path)
    (d / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    task = {"grade": {"hidden": HIDDEN, "check": f"{PY} -m pytest -q -p no:cacheprovider test_hidden.py"}}
    before = GK.tree_digest(d)
    ok, why = bench.grade(task, "", str(d))
    assert not ok and "check failed" in why
    assert GK.tree_digest(d) == before


def test_bench_hidden_test_overwrites_agent_file_only_in_the_copy(tmp_path):
    d = _task_dir(tmp_path)
    (d / "test_hidden.py").write_text("# the agent's own file with the hidden test's name\n")
    task = {"grade": {"hidden": HIDDEN, "check": f"{PY} -m pytest -q -p no:cacheprovider test_hidden.py"}}
    before = GK.tree_digest(d)
    ok, why = bench.grade(task, "", str(d))
    assert ok, why
    assert GK.tree_digest(d) == before
    assert (d / "test_hidden.py").read_text().startswith("# the agent's own")


def test_bench_exit_zero_cannot_hide_a_failed_test(tmp_path):
    d = _task_dir(tmp_path)
    (d / "calc.py").write_text("def add(a, b):\n    return 0\n")
    task = {"grade": {"hidden": HIDDEN, "check": f"{PY} -m pytest -q -p no:cacheprovider test_hidden.py; true"}}
    ok, why = bench.grade(task, "", str(d))
    assert not ok


# ---------------------------------------------------------------------------------------------- swe_local

def _swe_repo(tmp_path):
    """A repo with a function (buggy at HEAD), a committed test file, and a test_patch adding a failing-then-passing test."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "mathy.py").write_text("def double(x):\n    return x + x + 1\n")        # bug
    (repo / "test_mathy.py").write_text("from mathy import double\n\n\ndef test_zero():\n    assert double(0) == 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    new_test = ("from mathy import double\n\n\ndef test_zero():\n    assert double(0) == 0\n\n\n"
                "def test_three():\n    assert double(3) == 6\n")
    (repo / "test_mathy.py").write_text(new_test)
    patch = _git(repo, "diff", "--", "test_mathy.py")
    _git(repo, "checkout", "--", "test_mathy.py")
    return repo, patch


def _swe_task(repo, patch):
    return {"id": "t1", "repo": {"path": str(repo), "commit": "HEAD"}, "path_prepend": str(Path(PY).parent),
            "grade": {"swe": {"test_patch": patch, "test_files": ["test_mathy.py"],
                              "f2p": ["test_mathy.py::test_three"], "p2p": ["test_mathy.py::test_zero"]}, "timeout": 120}}


@pytest.fixture
def swe(tmp_path):
    repo, patch = _swe_repo(tmp_path)
    task = _swe_task(repo, patch)
    wd = tmp_path / "wt"
    swe_local.worktree(task, str(wd))              # a real git worktree: .git is a FILE pointing into the clone
    assert (wd / ".git").is_file()
    yield task, wd, repo
    swe_local.drop_worktree(task, str(wd))


def _repo_state(repo, wd):
    return (GK.tree_digest(wd), _git(wd, "status", "--porcelain"), _git(repo, "worktree", "list"),
            (repo / ".git" / "worktrees").exists() and GK.tree_digest(repo / ".git" / "worktrees"))


def test_swe_grade_fixed_passes_and_workspace_untouched(swe):
    task, wd, repo = swe
    (wd / "mathy.py").write_text("def double(x):\n    return x * 2\n")
    # the agent also edited the test file (weakened it): grading must restore it, in the copy
    (wd / "test_mathy.py").write_text("def test_zero():\n    assert True\n")
    before = _repo_state(repo, wd)
    ok, why = swe_local.grade(task, str(wd))
    assert ok, why
    assert _repo_state(repo, wd) == before
    assert not (wd / ".swe_test.patch").exists()
    assert (wd / "test_mathy.py").read_text() == "def test_zero():\n    assert True\n"      # the agent's edit is still there


def test_swe_grade_unfixed_fails(swe):
    task, wd, repo = swe
    before = _repo_state(repo, wd)
    ok, why = swe_local.grade(task, str(wd))
    assert not ok and "required tests not passing" in why
    assert _repo_state(repo, wd) == before


def test_swe_grade_regular_clone_with_git_dir(tmp_path):
    repo, patch = _swe_repo(tmp_path)
    task = _swe_task(repo, patch)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    (clone / "mathy.py").write_text("def double(x):\n    return 2 * x\n")
    before = GK.tree_digest(clone)
    ok, why = swe_local.grade(task, str(clone))
    assert ok, why
    assert GK.tree_digest(clone) == before
