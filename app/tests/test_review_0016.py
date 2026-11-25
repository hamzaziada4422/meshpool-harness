"""Fixes from the 0.0.16 release review: a destructive command never rides on a chat grant, Windows drive and UNC
paths are outside the project, re-check on edit re-runs only checks, hook patterns keep a closing parenthesis."""
from meshpool_harness.agent import extras as EX
from meshpool_harness.agent.core import Agent
from meshpool_harness.core import permrules as PR
from meshpool_harness.core import tools as T


def test_always_on_a_destructive_command_is_this_once_only(tmp_path):
    asked = []

    def approve(kind, title, detail, scope=""):
        asked.append(scope)
        return "always", ""
    a = Agent(str(tmp_path), mode="auto", approve=approve)
    assert a._permit("bash", {"command": "rm -rf build"})[0] is True
    assert asked == ["once"]                                          # the card offers no chat grant
    assert a._permit("bash", {"command": "rm -rf other"})[0] is True and len(asked) == 2   # asked again
    a.always.add("outside-bash")                                      # a grant saved by an older version
    assert a._permit("bash", {"command": "rm -rf third"})[0] is True and len(asked) == 3


def test_drive_and_unc_paths_are_not_inside_the_project():
    root = "/home/u/proj"
    for p in (r"C:/Users/me/.aws/credentials", r"C:\Users\me\x", r"\\server\share\f"):
        assert not PR._inside(p, root), p
    assert PR._inside("src/a.py", root)
    assert not PR.safe_read("cat C:/Users/me/.aws/credentials", root)


def test_recheck_reruns_only_checks(tmp_path):
    ws = T.Workspace(str(tmp_path / "proj"), str(tmp_path / "run"))
    ws._scratch_dir = str(tmp_path / "scratch-xyz")
    ok = ["python -m pytest -q tests", "python3 -c 'import a; assert a.x == 1'", "npm test", "python test_pager.py",
          f"python3 {tmp_path}/scratch-xyz/check.py"]
    bad = ["python import_rows.py", "sudo python3 -m pytest", "python3 -c 'import os' && curl http://x",
           "node server.js"]
    for c in ok:
        assert ws._recheckable(c), c
    for c in bad:
        assert not ws._recheckable(c), c


def test_bash_colon_pattern_keeps_a_closing_parenthesis():
    assert EX._tool_matches("bash:python -c print(1)", "bash", {"command": "python -c print(1)"})
    assert EX._tool_matches("Bash(rm *)", "bash", {"command": "rm a"})
