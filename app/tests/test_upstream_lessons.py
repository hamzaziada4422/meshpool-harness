"""Bugs other agent apps hit (their issue trackers), checked against meshpool and fixed here.

1. Tool arguments in the wrong JSON type: "10" for a number, "true" for a boolean, a list sent as JSON text. Gemini,
   Qwen and several OpenAI-compatible servers do this; refusing the call cost a turn and could loop
   (open-webui/open-webui#25641, RooCodeInc/Roo-Code#10322).
2. Background `git status` (the changes panel, the approval checks) took .git/index.lock and rewrote the index, so the
   user's own `git commit` failed with "index.lock: File exists" (anthropics/claude-code#11005).
3. Errors shown raw: OpenRouter's provider error came through as escaped JSON, and an untrusted TLS certificate read
   as "the connection failed" with no hint about company proxies (open-webui/open-webui#4158 class).
No network, no model."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.core import errtext as ET  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402

ALL = list(T.SCHEMAS)


# ------------------------------------------------------------------ 1. wrong JSON types with a plain meaning
def test_numbers_and_booleans_sent_as_text_are_taken():
    a = T.validate("read_file", json.dumps({"path": "a.py", "start": "10", "end": " 20 "}), ALL)
    assert a["start"] == 10 and a["end"] == 20
    a = T.validate("grep", {"pattern": "x", "ignore_case": "true", "multiline": "False", "context": 2.0}, ALL)
    assert a["ignore_case"] is True and a["multiline"] is False and a["context"] == 2 and isinstance(a["context"], int)
    a = T.validate("shell", {"command": ["ls"], "timeout_ms": "1500"}, ["shell"])
    assert a["timeout_ms"] == 1500
    a = T.validate("exec_command", {"cmd": "ls", "yield_time_ms": "2.5"}, ALL)
    assert a["yield_time_ms"] == 2.5


def test_a_list_sent_as_json_text_is_taken():
    a = T.validate("bash_many", {"commands": '["echo a", "echo b"]'}, ALL)
    assert a["commands"] == ["echo a", "echo b"]


@pytest.mark.parametrize("name,args", [
    ("read_file", {"path": "a.py", "start": "ten"}),
    ("read_file", {"path": "a.py", "start": "1.5"}),
    ("grep", {"pattern": "x", "ignore_case": "yes"}),
    ("shell", {"command": ["ls"], "timeout_ms": "soon"}),
    ("bash_many", {"commands": "echo a"}),
    ("exec_command", {"cmd": 7}),
])
def test_values_without_a_plain_meaning_are_still_refused(name, args):
    with pytest.raises(T.ToolError):
        T.validate(name, args, ALL + ["shell"])


# ------------------------------------------------------------------ 2. looking at a repository never takes its lock
def _repo(tmp_path) -> Path:
    if not shutil.which("git"):
        pytest.skip("git is not installed here")
    r = tmp_path / "repo"
    r.mkdir()
    g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(g + ["init", "-q"], check=True)
    (r / "a.txt").write_text("one\n")
    subprocess.run(g + ["add", "a.txt"], check=True)
    subprocess.run(g + ["commit", "-qm", "first"], check=True)
    time.sleep(1.1)                       # the next touch is newer than the index: status would refresh and rewrite it
    os.utime(r / "a.txt")
    return r


def _index(r: Path) -> int:
    return (r / ".git" / "index").stat().st_mtime_ns


def test_safe_git_status_leaves_the_index_alone(tmp_path):
    r = _repo(tmp_path)
    before = _index(r)
    subprocess.run(T.safe_git("status", "--porcelain"), cwd=r, capture_output=True, check=True)
    assert _index(r) == before
    subprocess.run(["git", "status", "--porcelain"], cwd=r, capture_output=True, check=True)
    assert _index(r) != before            # the plain call does rewrite it: the case above is a real test


def test_changes_panel_status_leaves_the_index_alone(tmp_path):
    from meshpool_harness.ui import workbench as WB
    r = _repo(tmp_path)
    before = _index(r)
    WB._git(r, "status", "--porcelain", "-z", "--untracked-files=all", "--", ".", check=False)
    assert _index(r) == before


def test_approval_check_before_git_stash_leaves_the_index_alone(tmp_path):
    from meshpool_harness.core import permrules as PR
    r = _repo(tmp_path)
    before = _index(r)
    assert PR._git_unmade_changes(r, r, [], True) is False      # a touch is not a change: nothing of the user's to shelve
    assert _index(r) == before


# ------------------------------------------------------------------ 2b. `cd FOLDER && read` needs no approval
def test_a_read_after_cd_into_the_project_does_not_ask(tmp_path):
    from meshpool_harness.core import permrules as PR
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    out = tmp_path / "elsewhere"
    out.mkdir()
    (root / "away").symlink_to(out, target_is_directory=True)
    for c in ("cd src && ls -la", f"cd {root} && git log --oneline -5", 'cd "src" && grep -rn foo .',
              "cd src && cd .. && cat README.md"):
        assert PR.safe_read(c, root), c
    for c in ("cd .. && ls", "cd ~ && ls", "cd /etc && cat passwd", "cd away && ls", "cd src && cat ../../x",
              "cd src && ls && rm -rf .", "cd src && rm -rf .", "cd src && ls > f", "cd $HOME && ls",
              "cd src; cat a.txt", "cd missing && ls"):
        assert not PR.safe_read(c, root), c


def test_a_cd_the_shell_reads_differently_is_never_followed(tmp_path, monkeypatch):
    """The checked folder must be the one the shell enters. A newline after `cd` ends that command (a bare `cd` goes
    home), and braces and globs expand: with a folder named `true`, "cd\\ntrue && cat .ssh/id_rsa" read the user's key
    unasked, and so did "cd {/home/me,} && cat .ssh/id_rsa" with a folder named `{/home/me,}` in the project. Each is
    proved against bash first, then must ask."""
    from meshpool_harness.core import permrules as PR
    monkeypatch.delenv("CDPATH", raising=False)
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("SECRET-KEY\n")
    root = tmp_path / "proj"
    (root / "true").mkdir(parents=True)
    (root / ("{" + str(home) + ",}")).mkdir(parents=True)        # a folder whose name brace-expands to the home folder
    (root / "src").mkdir()
    (root / "src" / "*").mkdir()
    bash = shutil.which("bash")
    env = dict(os.environ, HOME=str(home))
    for c in ("cd\ntrue && cat .ssh/id_rsa", "cd {" + str(home) + ",} && cat .ssh/id_rsa", "cd true\n&& ls",
              "cd src/* && ls", "cd sr? && ls", "cd ~/x && ls", "cd =true && ls", "cd true\r&& ls"):
        assert not PR.safe_read(c, root), repr(c)
    if bash:
        for c in ("cd\ntrue && cat .ssh/id_rsa", "cd {" + str(home) + ",} && cat .ssh/id_rsa"):
            got = subprocess.run([bash, "-c", c], cwd=root, env=env, capture_output=True, text=True).stdout
            assert "SECRET-KEY" in got, (c, got)                      # the shell really reads the key: it must ask
    assert PR.safe_read("cd\ttrue\t&&\tls", root)                     # tabs are plain separators
    assert PR.safe_read("cd 'src/*' && ls", root)                     # quoted, a glob is a plain name


def test_with_cdpath_set_a_bare_folder_name_is_not_followed(tmp_path, monkeypatch):
    """bash looks a relative `cd NAME` up in CDPATH first: the folder entered can be outside the project."""
    from meshpool_harness.core import permrules as PR
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    monkeypatch.setenv("CDPATH", str(tmp_path / "other"))
    assert not PR.safe_read("cd src && ls", root)
    assert PR.safe_read("cd ./src && ls", root)
    assert PR.safe_read(f"cd {root / 'src'} && ls", root)
    monkeypatch.delenv("CDPATH")
    assert PR.safe_read("cd src && ls", root)


# ------------------------------------------------------------------ 3. errors in plain words
def test_openrouter_provider_error_shows_the_provider_words_not_json():
    inner = {"error": {"message": "This model's maximum context length is 128000 tokens."}}
    raw = "meshpool HTTP 400: " + json.dumps({"error": {"message": "Provider returned error", "code": 400,
                                                         "metadata": {"raw": json.dumps(inner),
                                                                      "provider_name": "DeepInfra"}}})
    out = ET.explain(raw, host="openrouter.ai", model="m")
    assert "maximum context length is 128000 tokens" in out
    assert '{"error"' not in out and "\\\"" not in out
    assert "longer than this model can take" in out and "Check the model in Settings" not in out    # what helps


def test_untrusted_certificate_is_named_with_what_to_do():
    raw = ("HTTP 0: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local "
           "issuer certificate (_ssl.c:1006)>")
    out = ET.explain(raw, host="api.example.com", model="m")
    assert "certificate" in out.lower() and "SSL_CERT_FILE" in out
    assert "certificate" in ET.reason(raw)


# ------------------------------------------------------------------ MCP: choose a server's tools; typed arguments
_MCP_SRV = '''
import json, sys
TOOLS = [{"name": n, "inputSchema": {"type": "object", "properties": {
    "n": {"type": "integer"}, "on": {"type": ["boolean", "null"]}, "tags": {"type": "array"}, "s": {"type": "string"}}}}
    for n in ("read_page", "write_page", "delete_page")]
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    m = msg["method"]
    if m == "initialize":
        res = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "pages"}}
    elif m == "tools/list":
        res = {"tools": TOOLS}
    else:
        a = msg["params"]["arguments"]
        res = {"content": [{"type": "text", "text": json.dumps({k: type(v).__name__ for k, v in sorted(a.items())})}]}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
'''


def _pages(tmp_path, monkeypatch, **extra):
    from meshpool_harness.agent import mcp as M
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    p = tmp_path / "pages_srv.py"
    p.write_text(_MCP_SRV, encoding="utf-8")
    m = M.Manager(roots=[str(tmp_path)])
    m.add("pages", {"command": sys.executable, "args": [str(p)], **extra}, 20)
    return m


def test_mcp_disabled_tools_are_not_offered(tmp_path, monkeypatch):
    m = _pages(tmp_path, monkeypatch, disabled_tools=["delete_page"])
    try:
        names = {x["function"]["name"] for x in m.schemas()}
        assert any("read_page" in n for n in names) and any("write_page" in n for n in names)
        assert not any("delete_page" in n for n in names)
    finally:
        m.close()


def test_mcp_enabled_tools_offers_only_those(tmp_path, monkeypatch):
    m = _pages(tmp_path, monkeypatch, enabled_tools="read_page")         # one comma-separated text works too
    try:
        names = {x["function"]["name"] for x in m.schemas()}
        assert len(names) == 1 and "read_page" in next(iter(names))
    finally:
        m.close()


def test_mcp_arguments_sent_as_text_reach_the_server_typed(tmp_path, monkeypatch):
    m = _pages(tmp_path, monkeypatch)
    try:
        name = next(x["function"]["name"] for x in m.schemas() if "read_page" in x["function"]["name"])
        out, failed = m.call(name, {"n": "5", "on": "true", "tags": '["a"]', "s": "7"})
        assert not failed
        assert json.loads(out) == {"n": "int", "on": "bool", "s": "str", "tags": "list"}
        out, _ = m.call(name, {"n": "five"})                              # no plain meaning: passed on as it was
        assert json.loads(out) == {"n": "str"}
    finally:
        m.close()


def _server(tmp_path, **extra):
    from meshpool_harness.agent import mcp as M
    p = tmp_path / "pages_srv.py"
    p.write_text(_MCP_SRV, encoding="utf-8")
    return M.Server("pages", {"command": sys.executable, "args": [str(p)], **extra}, tmp_path / "logs", [str(tmp_path)])


def _start(tmp_path, **extra):
    """One start of the pages server with these settings, as a chat starts it; the names it offers."""
    s = _server(tmp_path, **extra)
    try:
        s.start(20)
        assert s.status == "connected", s.error
        return sorted(t["name"] for t in s.tools)
    finally:
        s.close()


def _lazy(tmp_path, **extra):
    """The next chat: the server connected from the saved tool list, without starting it (Server.start_lazy)."""
    s = _server(tmp_path, **extra)
    try:
        assert s.start_lazy(), "the tool list was not saved"
        return sorted(t["name"] for t in s.tools)
    finally:
        s.close()


@pytest.fixture
def mcp_home(tmp_path, monkeypatch):
    from meshpool_harness.agent import mcp as M
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("MH_MCP_POOL", raising=False)
    yield tmp_path
    M.stop_all()


def test_a_tool_turned_back_on_is_offered_in_the_next_chat(mcp_home):
    """The saved tool list (a day's cache, so a new chat does not start every server) was saved after disabled_tools
    had narrowed it: a tool the user turned back on stayed hidden in every new chat until the cache ran out."""
    assert _start(mcp_home, disabled_tools=["delete_page"]) == ["read_page", "write_page"]
    assert _lazy(mcp_home) == ["delete_page", "read_page", "write_page"]
    assert _lazy(mcp_home, enabled_tools=["write_page"]) == ["write_page"]


def test_a_server_switched_to_read_only_is_read_only_from_the_saved_list(mcp_home):
    """Settings, Connectors, "read only" was applied when the server started but not to the saved list, so the next
    chat was offered the server's write and delete tools."""
    assert _start(mcp_home) == ["delete_page", "read_page", "write_page"]
    assert _lazy(mcp_home, read_only=True) == ["read_page"]


_OFFICE_SRV = r'''
import json, sys
TOOLS = [{"name": "analyze_data", "inputSchema": {"type": "object", "properties": {
    "request": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}}}}]
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    m = msg["method"]
    if m == "initialize":
        res = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "office"}}
    elif m == "tools/list":
        res = {"tools": TOOLS}
    else:
        res = {"content": [{"type": "text", "text": "READ " + json.dumps(msg["params"]["arguments"].get("files"))}]}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
'''


def test_a_list_sent_as_text_to_an_mcp_tool_is_judged_as_the_list_it_becomes(tmp_path, monkeypatch):
    """The MCP server is sent '["~/.ssh/id_rsa"]' as the list it means (coerce_args), but the approval check saw one
    string: a relative path inside the project, so the office tools read an SSH key without asking. The agent now
    types the arguments before it judges the call."""
    from meshpool_harness.agent import mcp as M
    from meshpool_harness.agent.core import Agent
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    proj = tmp_path / "proj"
    proj.mkdir()
    srv = tmp_path / "office_srv.py"
    srv.write_text(_OFFICE_SRV, encoding="utf-8")
    m = M.Manager(roots=[str(proj)])
    m.add("meshpool-office", {"command": sys.executable, "args": [str(srv)]}, 20)
    asked = []

    def ap(kind, title, detail, scope=""):
        asked.append(title)
        return ("no", "") if "outside the project" in title else ("yes", "")     # the write in the folder: yes
    try:
        name = next(x["function"]["name"] for x in m.schemas() if x["function"]["name"].endswith("analyze_data"))
        assert name.replace("-", "_") == "mcp__meshpool_office__analyze_data", name
        a = Agent(str(proj), mode="ask", approve=ap, mcp=m)
        out = a._run_tool({"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(
            {"request": "summarise", "files": '["~/.ssh/id_rsa"]'})}})
        assert asked and "outside the project" in asked[0], (asked, out)
        assert "READ" not in out, out                                     # denied: the server never read it
        asked.clear()
        out = a._run_tool({"id": "c2", "type": "function", "function": {"name": name, "arguments": json.dumps(
            {"request": "summarise", "files": '["data.csv"]'})}})
        assert 'READ ["data.csv"]' in out, (asked, out)                  # inside the project: runs, typed
    finally:
        m.close()
        M.stop_all()


def test_bad_tool_lists_in_the_settings_never_widen_what_is_offered():
    from meshpool_harness.agent import mcp as M
    tools = [{"name": n} for n in ("a", "b", "c")]

    def names(spec):
        return [t["name"] for t in M.filter_tools(spec, tools)]
    assert names({"enabled_tools": []}) == []                              # an empty allow list offers none (Codex)
    assert names({"enabled_tools": " a , c ,"}) == ["a", "c"]
    assert names({"enabled_tools": ["a", "b"], "disabled_tools": "b"}) == ["a"]
    assert names({"enabled_tools": [1, None]}) == []                       # names that match nothing: nothing
    assert names({"disabled_tools": None, "enabled_tools": None}) == ["a", "b", "c"]
    assert names({"enabled_tools": ""}) == ["a", "b", "c"]                 # a blank text: not set
