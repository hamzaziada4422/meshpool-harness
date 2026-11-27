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
