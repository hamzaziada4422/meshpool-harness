"""Office engine jobs are counted and reused per chat (M27-03, X-04). The rebuild counter used to be global to the
app process, so a chat's first deck could be reported as "build 3, stop rebuilding"; and nothing tied a build to its
request, so one request could start several engine processes (a repeat call after the 100 s wait, a rebuild after
"flagged some items", two parallel calls). No real engines run: each job's command is a tiny fake. No model calls."""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meshpool_harness.agent import office_jobs as OJ  # noqa: E402
from meshpool_harness.agent import office_mcp_server as O  # noqa: E402

FAKE = ("import json, time, pathlib; time.sleep({s}); pathlib.Path('deck.pptx').write_bytes(b'x'); "
        "print(json.dumps({{'ok': True, 'steps': [{{'answer': 'Built deck.pptx. rules failing: P1.', "
        "'artifacts': ['deck.pptx']}}]}}))")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr("engines.forge.tools.qa_summary", lambda files: "")
    saved = dict(OJ.JOBS)
    OJ.JOBS.clear()
    real = OJ.new_job

    def new_job(*a, **k):
        jid = real(*a, **k)
        OJ.JOBS[jid]["cmd"] = [sys.executable, "-c", FAKE.format(s=new_job.sleep)]
        new_job.starts.append(jid)
        return jid
    new_job.starts, new_job.sleep = [], 0.3
    monkeypatch.setattr(OJ, "new_job", new_job)
    yield new_job
    OJ.JOBS.clear()
    OJ.JOBS.update(saved)


def build(root, request, scope, engine="slides"):
    """What core.py _office does: start, then run or wait, then report. Returns (job id, how, text)."""
    jid, how = OJ.start(engine, request, [], root, scope=scope)
    if how == "refused":
        return jid, how, OJ.refusal_text(jid)
    if how == "running":
        OJ.JOBS[jid]["event"].wait(30)
    elif not how:
        OJ.run(jid, timeout=30)
    return jid, how, OJ.report(jid, reused=how)[0]


def test_build_count_is_per_chat(tmp_path):
    """M27-03: other chats' decks do not make a chat's first deck "build 3"."""
    for i in range(3):
        _, _, text = build(tmp_path, f"deck number {i} about subject {i * 7}", "chat-A")
    assert "This is build 3" in text
    jid, how, text = build(tmp_path, "a sales deck", "chat-B")
    assert how == "" and OJ.JOBS[jid]["build_no"] == 1 and "This is build" not in text


def test_same_request_while_running_is_one_process(tmp_path, fast):
    """X-04: two parallel calls with the same request run one engine process and get the same report."""
    fast.sleep = 1.5
    got = []
    ts = [threading.Thread(target=lambda: got.append(build(tmp_path, "Sales deck  for ACME", "chat-A"))) for _ in range(2)]
    ts[0].start()
    time.sleep(0.4)
    ts[1].start()
    [t.join(60) for t in ts]
    assert len(fast.starts) == 1 and len(list((tmp_path / "Meshpool outputs").iterdir())) == 1
    assert len(got) == 2 and got[0][0] == got[1][0] and {g[1] for g in got} == {"", "running"}
    assert got[0][2] == got[1][2]


def test_same_request_after_done_is_not_rebuilt(tmp_path, fast):
    """X-04: a finished build is returned again for the same request, however it is spaced or cased."""
    jid, _, first = build(tmp_path, "Sales deck for ACME", "chat-A")
    assert "Deliver this file" in first and "already built" not in first
    jid2, how, text = build(tmp_path, "  sales DECK   for acme ", "chat-A")
    assert jid2 == jid and how == "done" and len(fast.starts) == 1
    assert text.startswith("This request was already built in this chat; here is that build again (nothing was rebuilt)")


def test_different_request_or_scope_builds(tmp_path, fast):
    build(tmp_path, "Sales deck for ACME", "chat-A")
    assert build(tmp_path, "Board update on hiring", "chat-A")[1] == ""
    assert build(tmp_path, "Sales deck for ACME", "chat-B")[1] == ""
    assert len(fast.starts) == 3


def test_similar_request_past_the_limit_is_refused(tmp_path, fast):
    for r in ("quarterly sales review for the northern region",
              "annual hiring plan for engineering and design",
              "customer onboarding flow and support playbook"):
        build(tmp_path, r, "chat-A")
    n = len(fast.starts)
    _, how, text = build(tmp_path, "Quarterly sales review for the northern region team", "chat-A")
    assert how == "refused" and len(fast.starts) == n
    assert "deck.pptx" in text and "tell the user which checks remain" in text
    assert build(tmp_path, "pricing strategy for enterprise accounts overseas", "chat-A")[1] == ""   # dissimilar builds
    assert build(tmp_path, "Quarterly sales review for the northern region team", "chat-B")[1] == ""  # other chat


def test_mcp_engine_twice_while_running_is_one_job(tmp_path, monkeypatch, fast):
    """X-04: the agent calls make_deck again after "Still working": the same job, not a second process."""
    fast.sleep = 1.5
    monkeypatch.setattr(O, "_project", lambda: tmp_path)
    monkeypatch.setattr(O, "WAIT", 0.3)
    monkeypatch.setenv("MH_CHAT_ID", "chat-A")
    assert O._engine("slides", "Sales deck for ACME", None)[0].startswith("Still working")
    O._engine("slides", "sales deck for acme", None)
    assert len(fast.starts) == 1 and len(OJ.JOBS) == 1
    jid = fast.starts[0]
    assert OJ.JOBS[jid]["scope"] == "chat-A"
    OJ.JOBS[jid]["event"].wait(30)
    assert "already built" in O._engine("slides", "Sales deck for ACME", None)[0] and len(fast.starts) == 1


def test_mcp_scope_falls_back_to_the_process(monkeypatch):
    monkeypatch.delenv("MH_CHAT_ID", raising=False)
    assert O._scope().startswith("pid")


def test_manager_passes_chat_id_to_the_office_server():
    from meshpool_harness.agent import mcp
    m = mcp.Manager.__new__(mcp.Manager)
    m.chat = "chat-A"
    spec = {"command": "python", "args": ["-m", "meshpool_harness.agent.office_mcp_server"]}
    assert m._with_chat(spec)["env"]["MH_CHAT_ID"] == "chat-A"
    assert "env" not in m._with_chat({"command": "x", "args": ["other.py"]})
