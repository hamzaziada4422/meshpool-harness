"""Platform audit pass 2, rows still open after the first fixes (UI server and agent): each test runs the audit's
trigger against the real code and fails if the fix is reverted."""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from meshpool_harness import capabilities as CAP
from meshpool_harness import config as C
from meshpool_harness.agent import core as K
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import server as S
from meshpool_harness.ui import workbench as W


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    sessions = tmp_path / "mh" / "runs" / "sessions"
    monkeypatch.setitem(A.Agent.__init__.__globals__, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(A.Session, "_start_mcp", lambda self: self._mcp_ready.set())
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    A._METAS.clear()
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


@pytest.fixture
def httpd(home):
    srv = S._Server(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _post(port: int, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Meshpool-Token": S.TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------------------------------- P2-uiserver-2
def test_p2_uiserver_2_long_token_is_checked_fast():
    """P2-uiserver-2: a message that is one long unbroken token (a pasted blob) is checked in well under a second,
    and short messages give the same answers as before."""
    for blob in ("a" * 300_000, "a." * 150_000, "a " * 150_000, "(" + "a-" * 150_000):
        t0 = time.process_time()
        assert CAP.needs(blob) == [] and CAP.edits(blob) is False
        assert time.process_time() - t0 < 1.0     # CPU time: other test workers do not count
    t0 = time.process_time()
    assert CAP.edits("In inventory.xlsx add a Value column " + "a" * 300_000)
    assert time.process_time() - t0 < 1.0
    assert CAP.edits("In inventory.xlsx add a Value column")
    assert CAP.edits("fix the totals in (Q3 report).docx")
    assert CAP.edits("update - my deck v2.pptx please")
    assert CAP.edits("change .( a.xlsx")
    assert not CAP.edits("change a.xlsxx")
    assert not CAP.edits("make a new budget.xlsx with a Value column")
    assert CAP.needs("In inventory.xlsx add a Value column") == []
    assert CAP.needs("make me a spreadsheet of my expenses") == ["spreadsheet"]


# ---------------------------------------------------------------------------------------------------- P2-uiserver-7
def test_p2_uiserver_7_missing_field_is_400_and_unknown_id_is_404(httpd):
    """P2-uiserver-7: a request without a field it needs is a 400 that names the field; an id that does not exist
    is still a 404 that names the id."""
    code, r = _post(httpd, "/api/skills", {"action": "upload", "files": [{"name": "notes.md"}]})
    assert (code, r["error"]) == (400, "Missing field: data.")
    code, r = _post(httpd, "/api/agent/ghost-1/send", {"text": "hello"})
    assert (code, r["error"]) == (404, "Not found: ghost-1.")


# ---------------------------------------------------------------------------------------------------- P2-uiserver-14
def test_p2_uiserver_14_failed_git_names_the_subcommand(tmp_path, monkeypatch):
    """P2-uiserver-14: a commit run as `git -c user.name=Meshpool ... commit` that fails with no output says
    "git commit failed", not "git user.name=Meshpool failed"."""
    monkeypatch.setattr(W, "git_exe", lambda: "git")
    monkeypatch.setattr(W.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    with pytest.raises(ValueError, match=r"^git commit failed$"):
        W._git(tmp_path, "-c", "user.name=Meshpool", "-c", "user.email=m@example.com", "commit", "-m", "x")
    with pytest.raises(ValueError, match=r"^git status failed$"):
        W._git(tmp_path, "-C", str(tmp_path), "--no-optional-locks", "status")
    with pytest.raises(ValueError, match=r"^git switch failed$"):
        W._git(tmp_path, "switch", "-c", "topic")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(a, 1)
    monkeypatch.setattr(W.subprocess, "run", slow)
    with pytest.raises(ValueError, match=r"^git push took too long"):
        W._git(tmp_path, "-c", "user.name=Meshpool", "push")


# ---------------------------------------------------------------------------------------------------- P2-uiserver-16
def test_p2_uiserver_16_set_model_checks_the_known_model_list(home, monkeypatch):
    """P2-uiserver-16: a model no endpoint serves is refused when the model list is known; with no list (offline)
    any name is taken; the default model set up by hand for a custom endpoint is always taken."""
    from meshpool_harness import providers as PV
    s = A.Session(workdir=str(home), harness="meshpool-code", kind="code")
    base, key, _ = PV.endpoint_pair()
    monkeypatch.setattr(W, "_MODELS", {"t": time.time(), "key": base + "|" + key[-6:], "error": "",
                                       "rows": [{"id": "deepseek-v4.1-flash"}, {"id": "gpt-5"}]})
    with pytest.raises(ValueError, match="nonexistent:zz"):
        s.set_model("nonexistent:zz", check=True)                 # a pick in the model menu
    assert A.read_meta(s.id).get("model") != "nonexistent:zz"
    s.set_model("gpt-5", check=True)
    assert A.read_meta(s.id)["model"] == "gpt-5"
    s.set_model("gpt-5:nitro", check=True)                        # a variant of a listed model (OpenRouter)
    C.write({"MH_MODEL": "my-own-finetune"})                      # saved with "use a name not in the list"
    s.set_model("my-own-finetune", check=True)
    assert A.read_meta(s.id)["model"] == "my-own-finetune"
    s.set_model("retired-model")                                  # creating or reopening a chat: never refused
    assert A.read_meta(s.id)["model"] == "retired-model"          # (review: a saved chat could not be opened)
    monkeypatch.setattr(W, "_MODELS", {"t": 0, "key": "", "rows": [], "error": ""})   # offline: no list
    s.set_model("nonexistent:zz", check=True)
    assert A.read_meta(s.id)["model"] == "nonexistent:zz"
    s.close()


# ---------------------------------------------------------------------------------------------------- P2-agent-9
def test_p2_agent_9_model_that_keeps_failing_says_so(tmp_path, monkeypatch):
    """P2-agent-9: every call gets a 503 and the endpoint answers a small probe. Once the retries are used up the
    error says the model keeps failing and to switch models (no "in a moment"), and the detail is cut at a word."""
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    a = K.Agent(str(tmp_path), approve=lambda *x: ("yes", ""), mode="auto", sessions_dir=str(tmp_path / "s"))
    monkeypatch.setattr(K.Agent, "_sleep", lambda self, s: None)
    a._probe = lambda base, key, model: "ok"
    calls, events = [], []
    a.emit = events.append
    detail = "HTTP 503: " + " ".join(["upstream_service_unavailable"] * 12)

    def post(base, key, body, stream):
        calls.append(1)
        raise RuntimeError(detail)
    a._post = post
    a.send("hello")
    err = [e["text"] for e in events if e["type"] == "error"][-1]
    assert len(calls) >= 5
    assert "in a moment" not in err and "keeps failing" in err and "another model" in err
    tail = err[err.index("(HTTP 503"):]
    assert tail.endswith("…)") and "upstream_service_unavailable…" in tail
    said = tail[tail.index("the service said: ") + len("the service said: "):-2]
    assert said.split() and all(w == "upstream_service_unavailable" for w in said.split())   # clipped on a word
    assert "—" not in err


def test_p2_agent_9_one_5xx_still_says_continue(tmp_path, monkeypatch):
    """P2-agent-9: a 5xx that did not use up the retries keeps the old advice, with a short detail left whole."""
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    a = K.Agent(str(tmp_path), approve=lambda *x: ("yes", ""), mode="auto", sessions_dir=str(tmp_path / "s"))
    events = []
    a.emit = events.append

    def boom(turn):
        raise RuntimeError("HTTP 502: bad gateway")
    monkeypatch.setattr(a, "_chat", boom)
    a.send("hello")
    err = [e["text"] for e in events if e["type"] == "error"][-1]
    assert "Send \"continue\" in a moment" in err and err.startswith("The model service had a problem.")
    assert err.endswith("the service said: bad gateway)") and "HTTP 502" in err and "…" not in err


# ---------- P2-uiserver-2 (same shape): office_intent's own file-name regex ------------------------------------------
def test_p2_uiserver_2_office_intent_long_token_is_checked_fast():
    from meshpool_harness import office_intent as OI
    t0 = time.process_time()
    for blob in ("a" * 300_000, "x." * 150_000, "(" + "b" * 300_000):
        assert OI.styled_deliverable(blob) is False
    assert time.process_time() - t0 < 1.0               # was minutes: the search retried from every character
    # same answers as before on ordinary messages (the docstring cases)
    assert OI.styled_deliverable("Make a 10-slide pitch deck for investors") is True
    assert OI.styled_deliverable("Turn notes.docx into a presentation") is True
    assert OI.styled_deliverable("Sort score.xlsx by midterm1 into sorted_score.xlsx") is False
    assert OI.styled_deliverable("Make the report in q3.docx shorter") is False
    assert OI.styled_deliverable("Add a chart of revenue to slide 3 of deck.pptx") is False
    assert OI.styled_deliverable("Make the report in (q3.docx) shorter") is False


def test_p2_uiserver_2_agent_file_name_scans_are_linear(tmp_path):
    # The office checks in agent/core.py read file names out of the user's message with the same quadratic pattern.
    from meshpool_harness.agent import core as K
    import re as _re
    t0 = time.process_time()
    for blob in ("a" * 300_000, "a." * 150_000):
        for ext in ("docx", "txt|md", "xlsx"):
            assert _re.findall(K._names_re(ext), blob) == []
        assert not K._TEMPLATE.search(blob)
        assert K._docx_hidden_text_miss("Add " + blob, tmp_path, time.time()) == ""
    assert time.process_time() - t0 < 2.0
    # the names found are the same as before: punctuation in front of a name is not part of it
    assert _re.findall(K._names_re("docx"), 'Fill "(template_a.docx)" and out.docx, not a.docxb') == \
        ["template_a.docx", "out.docx"]
    assert _re.findall(K._names_re("txt|md"), "Add notes.md and x.txt to report.docx") == ["notes.md", "x.txt"]
