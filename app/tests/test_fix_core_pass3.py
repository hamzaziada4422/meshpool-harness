import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from meshpool_harness.core import anthropic_wire as AW
from meshpool_harness.core import guards as G
from meshpool_harness.core import loop as L
from meshpool_harness.core import tools as T
from meshpool_harness.core import wire as W


def ws_of(tmp_path):
    return T.Workspace(tmp_path, tmp_path / ".run")


# ---- grep
def test_grep_head_limit_is_fast_and_glob_semantics(tmp_path):
    ws = ws_of(tmp_path)
    (tmp_path / "big.log").write_text("line\n" * 60000, encoding="utf-8")
    t0 = time.time()
    out = ws.grep("line", head_limit=5)
    assert time.time() - t0 < 3 and out.count("big.log") == 5
    (tmp_path / "a.py").write_text("hit\n")
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "b.py").write_text("hit\n")
    (tmp_path / "src" / "deep" / "c.py").write_text("hit\n")
    (tmp_path / "n.txt").write_text("hit\n")
    g = lambda pat: ws.grep("hit", glob=pat, output_mode="files_with_matches")
    assert "a.py" in g("**/*.py") and "deep" in g("**/*.py")
    assert "b.py" in g("src/*.py") and "c.py" not in g("src/*.py")
    r = g("*.{py,txt}")
    assert "a.py" in r and "n.txt" in r
    r = g("!*.txt")
    assert "n.txt" not in r and "a.py" in r


# ---- output cap
def test_command_output_is_capped(tmp_path):
    ws = ws_of(tmp_path)
    t0 = time.time()
    out = ws.bash("yes | head -c 300000000", 30)
    assert time.time() - t0 < 20 and len(out) < 20000


def test_edit_note_and_dir_and_binary(tmp_path):
    ws = ws_of(tmp_path)
    (tmp_path / "r.py").write_text("x = 1\ny = 1\n")
    ws.read_file("r.py")
    out = ws.edit("r.py", "= 1", "= 2", replace_all=True)
    assert "r.py (replaced 2 occurrences)" in out
    with pytest.raises(T.ToolError, match="directory"):
        ws.write_file("", "x")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02abc")
    with pytest.raises(T.ToolError, match="binary"):
        ws.read_file("b.bin")


# ---- sanitize
def test_sanitize_calls():
    out = L._sanitize_calls([{"id": "a", "function": {"name": "t", "arguments": {"a": 1}}},
                             {"id": "b", "function": {"name": "t", "arguments": None}},
                             {"id": "c", "function": {"name": "finish", "arguments": '{"answer": "unfinis'}}])
    assert json.loads(out[0]["function"]["arguments"]) == {"a": 1}
    assert out[1]["function"]["arguments"] == "{}"
    with pytest.raises(T.ToolError, match="cut off"):
        T.validate("finish", out[2]["function"]["arguments"], list(T.SCHEMAS))


# ---- deliverables
@pytest.mark.parametrize("prompt,expected", [
    ("Write a function that parses data/input.csv", set()),
    ("Please don't write to config.json; create a report in report.md", {"report.md"}),
    ("Do not create notes.txt", set()),
    ("Write the results to out/results.json", {"out/results.json"}),
    ("Save the summary as summary.md.", {"summary.md"}),
])
def test_deliverable_names(prompt, expected):
    assert G.deliverable_names(prompt) == expected


# ---- test tampering with spaces
def test_git_test_changes_handles_spaces(tmp_path):
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "a@b")
    run("config", "user.name", "a")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test b.py").write_text("def test_a():\n    pass\n")
    run("add", ".")
    run("commit", "-qm", "x")
    (tmp_path / "tests" / "test b.py").write_text("def test_a():\n    assert 1\n")
    ws = ws_of(tmp_path)
    g = G.Guards({}, ws, allow_test_edits=False, require_check=False)
    assert g._git_test_changes() == ["tests/test b.py"]


# ---- retry-after
def test_retry_after_in_error_text():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(429)
            self.send_header("Retry-After", "2")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    led = W.SimpleLedger.__new__(W.SimpleLedger)
    led.base_url, led.api_key = f"http://127.0.0.1:{srv.server_address[1]}", "k"
    with pytest.raises(RuntimeError) as e:
        led.chat(model="m", messages=[{"role": "user", "content": "x"}], retries=0)
    srv.shutdown()
    assert "[retry-after: 2.0]" in str(e.value)


# ---- wires
def test_responses_tool_result_list_is_json():
    body = {"model": "m", "messages": [{"role": "tool", "tool_call_id": "c1",
                                        "content": [{"type": "text", "text": "hi"}]}]}
    out = W.to_responses(body)["input"][0]["output"]
    assert json.loads(out) == [{"type": "text", "text": "hi"}]


def test_anthropic_tool_images_and_empty_ids():
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "", "function": {"name": "shot", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "", "content": [
                {"type": "text", "text": "pic"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]
    out = AW.to_request({"model": "m", "messages": msgs})["messages"]
    use = out[1]["content"][0]
    res = out[2]["content"][0]
    assert use["id"] and res["tool_use_id"] == use["id"]
    assert any(b["type"] == "image" for b in res["content"])


# ---- read_only
@pytest.mark.parametrize("cmd", ["sort -o out.txt in.txt", "find . -fprint f", "sed -n 'w f' x", "git branch x",
                                 "git tag v1", "git remote add o u", "git diff --output=f", "git grep -O sh",
                                 "cat <(rm x)", "rg -z x"])
def test_read_only_rejects_writers(cmd):
    assert not T.read_only(cmd)


@pytest.mark.parametrize("cmd", ["git branch", "git branch -a", "git tag -l", "git log --oneline", "ls | sort | head",
                                 "sed -n '1,5p' f", "git remote -v"])
def test_read_only_accepts_readers(cmd):
    assert T.read_only(cmd)


def test_review_lgtm_but():
    from meshpool_harness.core.review import findings
    assert findings("LGTM") == "" and findings("lgtm.") == ""
    assert findings("LGTM, but 1. x breaks") != ""


# ---- relay chunked body
def test_relay_chunked_body():
    from meshpool_harness.core import chatgpt_relay as R

    class FakeRfile:
        def __init__(self, b):
            import io
            self.b = io.BytesIO(b)

        def readline(self, n=-1):
            return self.b.readline(n)

        def read(self, n):
            return self.b.read(n)
    h = R._Handler.__new__(R._Handler)
    h.rfile = FakeRfile(b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
    assert h._read_chunked() == b"hello world"


# ---- client retry budget
def test_closed_port_main_and_side_calls(monkeypatch):
    from meshpool_harness.core import client as C
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setenv("MESHPOOL_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setattr(C, "CONNECT_BUDGET_S", 5.0)
    told = []
    cl = C.LiveClient("compact")
    t0 = time.time()
    with pytest.raises(RuntimeError):
        cl.chat(model="deepseek-v4.1-flash", messages=[{"role": "user", "content": "x"}])
    assert time.time() - t0 < 15
    cl = C.LiveClient("phase_2_build")
    cl.notify = told.append
    t0 = time.time()
    with pytest.raises(RuntimeError):
        cl.chat(model="deepseek-v4.1-flash", messages=[{"role": "user", "content": "x"}])
    assert time.time() - t0 < 15 and len(told) == 1


# ---- routing
def test_harness_router_keywords():
    from meshpool_harness.harness_router import classify
    for t, e in [("implement binary search in Go", "code"), ("what is a closure in JavaScript?", "code"),
                 ("fix the broken login form validation", "code"), ("debug why the sales API returns 500", "code"),
                 ("write a sentence about word count in python", "code"), ("run a regression", "data"),
                 ("find outliers", "data"), ("calculate churn rate", "data"), ("look at events.jsonl", "data")]:
        assert classify(t)[0] == e, t


def test_hrouter_misroutes():
    from meshpool_harness.hrouter.route import route
    for t, f, e in [("make a chart of monthly revenue from data.xlsx", ["data.xlsx"], "data"),
                    ("forecast next quarter revenue from this spreadsheet", [], "data"),
                    ("open the dashboard and export the report", [], "browser"),
                    ("go to amazon and find a cheap usb cable", [], "browser"),
                    ("check my gmail", [], "browser"),
                    ("fill out the contact form on example.com", [], "browser")]:
        assert route(t, files=f, kind="cowork").spec == e, t
    for t in ["add a --verbose flag to the CLI", "there is a memory leak in the worker", "set up GitHub Actions"]:
        assert route(t, kind="cowork").spec.startswith("code"), t
    for t in ["translate 'good morning' to French", "draft a polite email declining the meeting",
              "tl;dr of the French revolution"]:
        assert route(t, kind="code").spec == "chat", t
    assert route("edit the attached proposal and fix the typos", kind="code").spec == "docs"


def test_plain_text_answer_is_kept(tmp_path):
    from meshpool_harness.core.client import MockClient
    msg = {"role": "assistant", "content": "The answer is 42."}
    r = L.run("what?", client=MockClient([msg, msg, msg, msg]), model="m", profile=None, ws=ws_of(tmp_path),
              ctx={"task_id": "x"}, system_text="s", require_check=False)
    assert not r.ok and r.reason == "model stopped calling tools" and r.answer == "The answer is 42."
