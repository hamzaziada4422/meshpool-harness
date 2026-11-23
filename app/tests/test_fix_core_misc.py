import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from meshpool_harness.core import sandbox as SB
from meshpool_harness.core import tools as T
from meshpool_harness.core import wire as W
from meshpool_harness.hrouter import packs as P


def test_repro_commands_only_safe_verbs(tmp_path):
    (tmp_path / "deploy.sh").write_text("echo hi")
    for bad in ("`npm publish` fails with 403", "`make deploy` fails", "`shutdown -h now` hangs",
                "`sh deploy.sh` breaks", "`cargo run -- --wipe` dies", "`go run ./cmd/migrate` fails"):
        assert P.repro_commands(bad, tmp_path) == [], bad
    assert P.repro_commands("`npm test` fails", tmp_path) == ["npm test"]
    assert P.repro_commands("`pytest -x tests` fails", tmp_path) == ["pytest -x tests"]


def test_javac_template_formats():
    c = [c for c in P.PACKS["java"].checks if c.name == "javac"][0]
    out = c.cmd.format(files="A.java")
    assert "${TMPDIR:-/tmp}" in out


def test_config_syntax_accepts_jsonc(tmp_path):
    f = tmp_path / "tsconfig.json"
    f.write_text('{\n // c\n "a": 1, /* x */\n "b": [1, 2,],\n}\n', encoding="utf-8")
    g = tmp_path / "bad.json"
    g.write_text('{"a": }', encoding="utf-8")
    s = tmp_path / "s.py"
    s.write_text(P.CONFIG_SYNTAX, encoding="utf-8")
    ok = subprocess.run([sys.executable, str(s), str(f)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout
    bad = subprocess.run([sys.executable, str(s), str(g)], capture_output=True, text=True)
    assert bad.returncode == 1 and "JSONDecodeError" in bad.stdout


def test_sandbox_chdir_uses_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_SANDBOX", "1")
    sub = tmp_path / "sub"
    sub.mkdir()
    out = SB.wrap(["bash", "-c", "pwd"], tmp_path, platform="linux", which=lambda n: "/usr/bin/" + n, cwd=sub)
    assert out[out.index("--chdir") + 1] == str(sub.resolve())


def test_validate_empty_arguments():
    assert T.validate("bash_output", "", list(T.SCHEMAS)) == {}
    assert T.validate("bash_output", "  ", list(T.SCHEMAS)) == {}


def test_grep_glob_read_edge_cases(tmp_path):
    ws = T.Workspace(tmp_path, tmp_path / ".run")
    (tmp_path / "a.py").write_text("x\ny\n", encoding="utf-8")
    with pytest.raises(T.ToolError):
        ws.grep("x", "nope")
    with pytest.raises(T.ToolError):
        ws.glob("/etc/*")
    with pytest.raises(T.ToolError):
        ws.glob("../*")
    assert "file has 2 lines" in ws.read_file("a.py", start=10)


def test_cap_writes_utf8(tmp_path):
    ws = T.Workspace(tmp_path, tmp_path / ".run")
    out = ws._cap("✓ ok\n" * 5000, "bash")
    assert "full output in" in out


def test_sse_error_event_and_truncation():
    err = [b'data: {"error": {"message": "overloaded", "code": 503}}\n']
    with pytest.raises(RuntimeError, match="HTTP 503"):
        W.sse_to_chat(err)
    cut = [b'data: {"choices":[{"delta":{"content":"hel"}}]}\n']
    with pytest.raises(RuntimeError, match="HTTP 0"):
        W.sse_to_chat(cut)
    ok = cut + [b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n', b"data: [DONE]\n"]
    assert W.sse_to_chat(ok)["choices"][0]["message"]["content"] == "hel"


def test_chatgpt_error_event_codes():
    from meshpool_harness.core import chatgpt as CG
    from meshpool_harness.core.client import classify_error
    ev = ['data: {"type":"response.failed","response":{"error":{"code":"context_length_exceeded"}}}']
    with pytest.raises(RuntimeError) as e:
        CG.read_stream(ev)
    assert classify_error(str(e.value)) == "context"


def test_budget_prices():
    from meshpool_harness.core.loop import _budget_prices
    assert _budget_prices("deepseek-v4")[0] == 0.15
    assert _budget_prices("claude-sonnet")[2] > 1


def test_connector_url_routing():
    from meshpool_harness import connectors as CN
    c, ref = CN.get("https://github.com/o/r/issues/12")
    assert c.name == "github" and ref.endswith("/issues/12")
    c, ref = CN.get("https://example.com/t/1?x=1")
    assert c.name == "http"


def test_jira_needs_base_url(monkeypatch):
    from meshpool_harness import connectors as CN
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="JIRA_BASE_URL"):
        CN.get("jira:PROJ-1")


def test_http_report_result_before_query(monkeypatch):
    from meshpool_harness.connectors import generic
    from meshpool_harness.connectors.base import Task
    seen = {}
    monkeypatch.delenv("MH_HTTP_RESULT_URL", raising=False)
    monkeypatch.setattr(generic, "http_json", lambda m, u, h, b=None: seen.setdefault("u", u))
    generic.HTTPSource().report(Task("http", "https://x.io/t/1?k=2", "t"), {"ok": True})
    assert seen["u"] == "https://x.io/t/1/result?k=2"


def test_routing_no_duplicate_keywords_and_codex_example_gone():
    d = json.loads((Path(__file__).parent.parent / "defaults" / "routing.json").read_text(encoding="utf-8"))
    for v in d["categories"].values():
        assert len(v["keywords"]) == len(set(v["keywords"]))
    assert not (Path(__file__).parent.parent / "defaults" / "harnesses" / "examples" / "codex.json").exists()


def test_smalltalk_routes_to_chat():
    from meshpool_harness.hrouter.route import route
    r = route("hi", kind="code")
    assert r.spec == "chat"
