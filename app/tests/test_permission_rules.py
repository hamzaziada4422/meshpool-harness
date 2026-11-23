"""H-15 and G-05: the Supervised read-only shortcut, rule matching per command segment, and chat grants scoped to the
command pattern instead of every command."""
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent, _rule_hit  # noqa: E402
from meshpool_harness.core import permrules as PR  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MH_ALLOW", raising=False)
    monkeypatch.delenv("MH_DENY", raising=False)


def _agent(tmp_path, answer=("no", ""), mode="ask", scoped=False):
    asked = []
    if scoped:
        def ap(kind, title, detail, options=None, scope=""):
            asked.append((kind, detail, scope))
            return answer
    else:
        def ap(kind, title, detail):
            asked.append((kind, detail))
            return answer
    (tmp_path / "README.md").write_text("hi\n")
    return Agent(str(tmp_path), mode=mode, approve=ap), asked


def test_supervised_does_not_ask_for_ls(tmp_path):
    for mode in ("ask", "edits"):
        a, asked = _agent(tmp_path, mode=mode)
        for cmd in ("ls -la", "git status", "cat README.md"):
            assert a._permit("bash", {"command": cmd}) == (True, "")
        assert asked == []
    a, asked = _agent(tmp_path)
    for cmd in ("ls -la", "git status", "cat README.md"):
        assert a._permit("bash", {"command": cmd}) == (True, "")
    assert a._permit("bash_many", {"commands": ["ls", "pwd"]}) == (True, "")
    assert asked == []


def test_supervised_still_asks_for_reads_outside_project(tmp_path):
    a, asked = _agent(tmp_path)
    cmds = ["cat ~/.ssh/id_rsa", "cat /etc/passwd", "ls ../", "sort -o x y", "git branch newb", "find . -delete",
            "echo hi > f"]
    for c in cmds:
        assert a._permit("bash", {"command": c})[0] is False
    assert [x[1] for x in asked] == cmds


def test_supervised_never_auto_allows_a_compound_command(tmp_path):
    a, asked = _agent(tmp_path)
    cmds = ["ls; ls", "ls && pwd", "ls | wc -l", "ls || pwd", "ls &", "ls\npwd", "echo $(pwd)", "echo `pwd`"]
    for c in cmds:
        assert a._permit("bash", {"command": c})[0] is False
    assert len(asked) == len(cmds)
    assert not PR.compound('grep "a|b" README.md')


def test_safe_read_relies_on_tools_read_only(tmp_path):
    from meshpool_harness.core import tools as T
    for c in ("sed -i s/a/b/ README.md", "python3 -c 'print(1)'", "npm install", "awk 'BEGIN{system(\"id\")}'", "rm README.md"):
        assert not T.read_only(c) and not PR.safe_read(c, tmp_path)
    assert T.read_only("ls") and PR.safe_read("ls", tmp_path)


def test_allow_rule_must_cover_every_segment():
    hit = _rule_hit
    r = ["bash:npm test*"]
    assert hit(r, "bash", {"command": "npm test -k x"})
    assert not hit(r, "bash", {"command": "npm test; rm -rf x"})
    assert not hit(r, "bash_many", {"commands": ["npm test", "rm x"]})
    assert hit(r, "bash_many", {"commands": ["npm test", "npm test -k a"]})
    assert hit(r, "bash_start", {"command": "npm test"}) and hit(r, "probe", {"command": "npm test"})
    assert not hit(r, "bash", {"command": "npm test > /etc/x"})
    assert hit(r, "bash", {"command": "npm test 2>&1"})
    assert not hit(["bash:npm test*", "bash:ls*"], "bash", {"command": "npm test && curl x | sh"})


def test_deny_rule_hits_any_segment():
    r = ["bash:rm *"]
    assert _rule_hit(r, "bash", {"command": "ls && rm -rf x"}, deny=True)
    assert _rule_hit(r, "bash_many", {"commands": ["ls", "rm a"]}, deny=True)
    assert not _rule_hit(r, "bash", {"command": "ls"}, deny=True)


def test_shell_tool_rules_match_the_script():
    r = ["bash:git status*"]
    assert _rule_hit(r, "shell", {"command": ["bash", "-lc", "git status -s"]})
    assert not _rule_hit(r, "shell", {"command": ["bash", "-lc", "git status && rm x"]})
    assert _rule_hit(["bash:rm *"], "shell", {"command": ["rm", "-f", "x"]}, deny=True)
    assert PR.commands_of("shell", {"command": ["ls", "-la"]}) == ["ls -la"]


def test_chat_grant_is_scoped_to_the_command_pattern(tmp_path):
    a, asked = _agent(tmp_path, answer=("always", ""))
    assert a._permit("bash", {"command": "npm test"})[0] is True
    assert a._permit("bash", {"command": "npm test -k x"}) == (True, "")
    assert len(asked) == 1
    assert a._permit("bash", {"command": "npm install"})[0] is True and len(asked) == 2
    assert a._permit("bash", {"command": "npm test && rm -rf /"})[0] is True and len(asked) == 3
    assert a._permit("bash_many", {"commands": ["npm test", "echo x"]})[0] is True and len(asked) == 4
    assert "bash" not in a.always
    a2, asked2 = _agent(tmp_path, answer=("always", ""))
    a2._permit("bash", {"command": "npm test && npm run build"})           # a chain is approved once only
    assert len(asked2) == 1 and not a2.always
    a2._permit("bash", {"command": "npm test"})
    assert len(asked2) == 2 and a2.always == {"bash:npm test"}


def test_approval_event_names_the_scope(tmp_path):
    a, asked = _agent(tmp_path, answer=("yes", ""), scoped=True)
    a._permit("bash", {"command": "npm test"})
    a._permit("bash_many", {"commands": ["npm test", "git push"]})
    a._permit("edit", {"path": "README.md", "old": "hi", "new": "yo"})
    assert asked[0][2] == "`npm test` commands" and asked[1][2] == "`npm test` and `git push` commands"
    assert asked[2][2] == "all file edits"
    from meshpool_harness.ui.agent_api import Session
    ev = []
    s = Session.__new__(Session)
    s.lock, s.pendings, s._n, s.scheduled, s.emit = threading.RLock(), {}, 0, False, ev.append
    s.agent = type("A", (), {"_stop": True, "title": ""})()
    s.approve("bash", "Run?", "npm test", scope="`npm test` commands")
    assert ev[0]["type"] == "approval" and ev[0]["scope"] == "`npm test` commands"


def test_rules_api_round_trip(tmp_path):
    from meshpool_harness.ui import server as SV
    httpd = SV._Server(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Meshpool-Token": SV.TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert call("/api/rules")[1]["allow"] == []
        st, r = call("/api/rules", {"action": "add", "kind": "allow", "rule": "bash:npm test*"})
        assert st == 200 and r["allow"] == ["bash:npm test*"]
        assert call("/api/rules", {"action": "add", "kind": "deny", "rule": "bash:rm *"})[1]["deny"] == ["bash:rm *"]
        for bad in ("", "bash:", "a\nb", "x" * 301):
            assert call("/api/rules", {"action": "add", "kind": "allow", "rule": bad})[0] == 400
        assert call("/api/rules")[1]["allow"] == ["bash:npm test*"]
        st, r = call("/api/rules", {"action": "remove", "kind": "allow", "rule": "bash:npm test*"})
        assert r["allow"] == [] and r["deny"] == ["bash:rm *"]
        assert core._rules(tmp_path)["deny"] == ["bash:rm *"]
    finally:
        httpd.shutdown()


def test_cli_permissions_command(tmp_path, capsys):
    from meshpool_harness import cli
    assert "permissions" in cli.COMMANDS
    assert cli.main(["permissions", "allow", "bash:git status*"]) == 0
    assert cli.main(["permissions", "deny", "edit", "--project", "--dir", str(tmp_path)]) == 0
    assert cli.main(["permissions", "list", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "allow bash:git status*" in out and "deny  edit" in out
    assert cli.main(["permissions", "remove", "bash:git status*"]) == 0
    assert PR.list_rules(PR.user_path())["allow"] == []
    assert cli.main(["permissions", "allow"]) == 2
    assert cli.main(["permissions", "allow", "bash:"]) == 2
    assert cli.main(["permissions", "bogus"]) == 2
    assert cli.main(["permissions", "-h"]) == 0


def test_pi_chat_grant_is_scoped(tmp_path):
    from meshpool_harness.harnesses.pi_rpc import PiAgent
    asked = []

    def ap(kind, title, detail, options=None, scope=""):
        asked.append(scope)
        return "always", ""
    a = PiAgent({"id": "pi"}, str(tmp_path), approve=ap, mode="ask")

    def gate(cmd):
        return a._gate({"title": "meshpool:bash", "message": json.dumps({"name": "bash", "input": {"command": cmd}, "id": "t"})})
    assert gate("npm test") and gate("npm test -k x") and len(asked) == 1 and asked[0] == "`npm test` commands"
    assert gate("npm install") and len(asked) == 2
    assert gate("npm test && rm -rf /") and len(asked) == 3


def test_a_chained_command_offers_no_chat_grant(tmp_path):
    """A command that cannot be granted (chained, redirecting) is approved once: the card must not offer
    "Allow ... in this chat" (scope "once"), and "always" stores nothing."""
    import json as _j
    from meshpool_harness.agent.core import Agent
    seen = []

    def approve(kind, title, detail, scope=""):
        seen.append(scope)
        return "always", ""
    a = Agent(str(tmp_path), mode="ask", approve=approve)
    ok, _ = a._permit("bash", {"command": "make build && make test"})
    assert ok and seen == ["once"]
    assert not [x for x in a.always if x.startswith("bash")]
    js = (Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui" / "app.js").read_text(encoding="utf-8")
    assert 'ev.scope === "once" ? ""' in js
