"""Stop and cancellation (ws05 package C): backoff reset, Stop through every blocking call, vision per model,
request validity (tool calls always answered), Stop generation, health probing."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.agent.history_repair import repair_tool_pairs  # noqa: E402
from meshpool_harness.core import gateway as GW  # noqa: E402

TURN = {"in": 0, "cached": 0, "out": 0, "calls": 0}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")


def _ok(text="done"):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {}}, False


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _later(sec, fn):
    t = threading.Timer(sec, fn)
    t.daemon = True
    t.start()


def _valid(messages):
    """Every assistant tool call is answered by exactly one tool message right after it; no orphan tool messages."""
    i = 0
    while i < len(messages):
        m = messages[i]
        assert m.get("role") != "tool", "orphan tool message"
        i += 1
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = [c["id"] for c in m["tool_calls"]]
            got = []
            while i < len(messages) and messages[i].get("role") == "tool":
                got.append(messages[i]["tool_call_id"])
                i += 1
            assert sorted(got) == sorted(ids)


# ---------- M28-05 ------------------------------------------------------------------------------------------------
def test_429_backoff_resets_each_turn(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    waits = []

    def sleep(w):
        waits.append(w)
        if len(waits) == 6:
            a.stop()
    a._sleep = sleep

    def limited(base, key, body, stream):
        raise RuntimeError("HTTP 429: too many requests")
    a._post = limited
    a.send("one")
    assert a._n429 >= 5 and waits[:3] == [3, 6, 12]
    waits.clear()
    calls = []

    def once(base, key, body, stream):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("HTTP 429: too many requests")
        return _ok()
    a._post = once
    a._sleep = waits.append
    a.send("two")
    assert waits == [3]


# ---------- M28-03 ------------------------------------------------------------------------------------------------
def test_stop_cuts_reviewer_call(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    release = threading.Event()
    a._post = lambda base, key, body, stream: (release.wait(30), _ok())[1]
    _later(0.3, a.stop)
    t0 = time.time()
    with pytest.raises(Exception) as ei:              # by name: another test may have reloaded agent.core
        a._aux_complete("sys", "user", dict(TURN))
    assert type(ei.value).__name__ == "Interrupted"
    release.set()
    assert time.time() - t0 < 2


def test_stop_cuts_mutation_check(tmp_path):
    from meshpool_harness.core import mutate as MU
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_calc.py").write_text(
        "import time\nfrom calc import add\n\ndef test_add():\n    time.sleep(30)\n    assert add(1, 2) == 3\n")
    t0 = time.time()
    r = MU.survivors(str(tmp_path), {"calc.py"}, per_mutant=60, total=300, should_stop=lambda: time.time() - t0 > 0.5)
    assert time.time() - t0 < 3 and r == []


# ---------- M28-04 ------------------------------------------------------------------------------------------------
def test_stop_cuts_responses_wire_call(tmp_path, monkeypatch):
    from meshpool_harness.core import wire as W
    monkeypatch.setenv("MH_WIRE", "responses")
    release = threading.Event()

    class Blocking:
        def __init__(self, *a, **k):
            pass

        def chat(self, **kw):
            release.wait(30)
            return _ok()[0]
    monkeypatch.setattr(W, "SimpleLedger", Blocking)
    a = Agent(str(tmp_path), mode="auto")
    evs = []
    a.emit = evs.append
    _later(0.3, a.stop)
    t0 = time.time()
    a.send("hi")
    release.set()
    assert time.time() - t0 < 2 and any(e.get("type") == "interrupted" for e in evs)


# ---------- M28-01 ------------------------------------------------------------------------------------------------
IMG = {"mime": "image/png", "data": "aGVsbG8=", "name": "s.png"}


def _has_img(body):
    return any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
               for m in body["messages"])


def test_second_image_to_text_only_model_does_not_400(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    a.model = "text-only-model"
    evs = []
    a.emit = evs.append
    sent = []

    def post(base, key, body, stream):
        sent.append(_has_img(body))
        if _has_img(body):
            raise RuntimeError("HTTP 400: image input is not supported by this model")
        return _ok()
    a._post = post
    a.send("look", images=[IMG])
    a.send("and again", images=[IMG])
    assert not [e for e in evs if e.get("type") == "error"]
    assert [m for m in a.messages if m.get("role") == "assistant"][-1]["content"] == "done"
    assert sent.count(True) == 1                       # only the very first request found out
    assert any(isinstance(m.get("content"), list) for m in a.messages)    # history keeps the images
    a.model = "vision-model"
    a._post = lambda base, key, body, stream: (sent.append(_has_img(body)), _ok())[1]
    a.send("third", images=[IMG])
    assert sent[-1] is True


# ---------- M02-01 ------------------------------------------------------------------------------------------------
def test_stop_during_approval_is_not_a_denial(tmp_path):
    gate, asked = threading.Event(), threading.Event()
    a = Agent(str(tmp_path), mode="ask", approve=lambda *x, **k: (asked.set(), gate.wait(30), ("no", ""))[2])
    seq = [{"content": "", "tool_calls": [_call(1, "bash", {"command": "touch x.txt"})]}, {"content": "done"}]
    a._post = lambda base, key, body, stream: ({"choices": [{"message": seq.pop(0), "finish_reason": "stop"}],
                                                "usage": {}}, False)

    def stop():
        asked.wait(30)                                 # Stop while the approval is open, not before the turn starts
        a.stop()
        gate.set()
    threading.Thread(target=stop, daemon=True).start()
    a.send("do it")
    assert asked.is_set()
    assert not any("denied" in str(m.get("content")) for m in a.messages)
    assert any("stopped" in str(m.get("content")) for m in a.messages if m.get("role") == "tool")


# ---------- M01-02 ------------------------------------------------------------------------------------------------
def test_stop_between_check_and_send(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    evs, reqs = [], []
    a.emit = evs.append
    a._post = lambda base, key, body, stream: (reqs.append(1), _ok())[1]
    a._expect_gen = getattr(a, "_gen", 0)
    a.stop()                                           # Stop arrives after the front end took the message
    a.send("hello")
    assert not reqs and any(e.get("type") == "interrupted" for e in evs)
    assert any("hello" in str(m.get("content")) for m in a.messages if m.get("role") == "user")


def test_stop_while_office_tools_start_never_runs_the_turn(tmp_path, monkeypatch):
    from meshpool_harness.ui import agent_api as A
    s = A.Session.__new__(A.Session)
    s.events, s.lock, s.goal, s.id, s.queue, s._stop_req = [], threading.Lock(), "", "t-stop", [], False
    s.emit = lambda ev: s.events.append(ev)
    s.state = "idle"
    s.pendings = {}
    s.harness = {"type": "auto", "id": "meshpool-code"}
    s.cp, s.plan, s.scheduled, s.kind, s.workdir, s.ctx_pending, s.incognito = None, False, False, "code", str(tmp_path), False, True
    s.route_addon, s.t_turn, s._turn_shared = "", 0, False
    s._body = lambda text, images: text
    s._turn_begin = s._turn_finish = lambda: None
    reqs = []

    class Ag:
        _stop = False
        messages = []
        plan = False

        def stop(self):
            self._stop = True

        def send(self, *a, **k):
            reqs.append(1)
    s.agent = Ag()

    def slow(text):
        t0 = time.time()
        while time.time() - t0 < 5 and not s._stop_req:
            time.sleep(0.05)
        return []
    s._capability_turn = slow
    monkeypatch.setattr(A.C, "load_into_env", lambda **k: None)
    _later(0.3, s.stop)
    t0 = time.time()
    try:
        s._start_turn("make a deck", None)
    except Exception:  # noqa: BLE001 - the rest of the turn needs a full Session; only the Stop is under test
        pass
    time.sleep(0.5)
    assert not reqs and time.time() - t0 < 4        # the wait ended on Stop, not after 5 s, and nothing was sent
    assert any(e.get("type") == "interrupted" for e in s.events), s.events   # said so, instead of silently dying


# ---------- M28-06 ------------------------------------------------------------------------------------------------
def test_fresh_caller_after_everyone_gave_up_probes_at_once():
    h = GW.Health()
    h.down_since, h.next_probe = time.time() - 100, time.time() + 60
    t0, calls = time.time(), []
    ok = h.wait_until_up(lambda: (calls.append(1), "ok")[1], lambda s: time.sleep(min(s, 0.05)), lambda e: None,
                         30, lambda: None)
    assert ok and calls and time.time() - t0 < 0.5


def test_backoff_respected_when_another_caller_waits():
    h = GW.Health()
    h.down_since, h.next_probe = time.time() - 100, time.time() + 60
    h.waiters = 1                                      # someone else is already waiting on this endpoint
    calls, ev = [], []
    ok = h.wait_until_up(lambda: (calls.append(1), "ok")[1], lambda s: time.sleep(min(s, 0.05)), ev.append,
                         0.5, lambda: None)
    assert not ok and not calls and any("Checking again" in e["text"] for e in ev)


# ---------- H-25 --------------------------------------------------------------------------------------------------
def _two_step(first_calls):
    seq = [{"content": "", "tool_calls": first_calls}, {"content": "done"}]
    seen = []

    def post(base, key, body, stream):
        seen.append(json.loads(json.dumps(body["messages"])))
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    return post, seen


def test_stop_mid_tool_leaves_valid_history(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    a._post, seen = _two_step([_call(1, "bash", {"command": "sleep 5"})])
    _later(0.5, a.stop)
    t0 = time.time()
    a.send("run")
    assert time.time() - t0 < 4
    _valid(a.messages)
    a._post, seen = _two_step([])
    a._post = lambda base, key, body, stream: (seen.append(body["messages"]), _ok())[1]
    a.send("again")
    _valid(seen[0])


def test_stop_mid_parallel_batch(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    (tmp_path / "f.txt").write_text("x")
    slow = threading.Event()
    monkeypatch.setattr(a.ws, "grep", lambda *x, **k: (slow.wait(10), "no")[1], raising=False)
    calls = [_call(i, "grep", {"pattern": "x"}) for i in range(3)] + [_call(9, "read_file", {"path": "f.txt"})]
    a._post, _ = _two_step(calls)
    _later(0.5, a.stop)
    a.send("go")
    slow.set()
    _valid(a.messages)


def test_error_mid_turn_leaves_valid_history(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    a._post, _ = _two_step([_call(1, "bash", {"command": "echo hi"}), _call(2, "bash", {"command": "echo yo"})])
    real = a._run_tool
    n = []

    def boom(c, cut=False):
        n.append(1)
        if len(n) == 2:
            raise ValueError("tool exploded")
        return real(c, cut)
    monkeypatch.setattr(a, "_run_tool", boom)
    try:
        a.send("go")
    except Exception:  # noqa: BLE001
        pass
    seen = []
    a._post = lambda base, key, body, stream: (seen.append(body["messages"]), _ok())[1]
    a.send("again")
    _valid(seen[0])


def test_resume_after_crash_mid_tool_sends_valid_request(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    a.messages += [{"role": "user", "content": "do"},
                   {"role": "assistant", "content": "", "tool_calls": [_call(1, "bash", {"command": "ls"})]}]
    a.title = "t"
    a.save()
    b = Agent.resume(str(tmp_path), session_id=a.id)
    seen = []
    b._post = lambda base, key, body, stream: (seen.append(body["messages"]), _ok())[1]
    b.send("continue")
    _valid(seen[0])
    assert any("app closed" in str(m.get("content")) for m in b.messages if m.get("role") == "tool")


def test_body_pairs_orphans():
    msgs = [{"role": "system", "content": "s"}, {"role": "tool", "tool_call_id": "zz", "content": "orphan"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": [_call(1, "a", {}), _call(2, "b", {})]},
            {"role": "tool", "tool_call_id": "c2", "content": "two"},
            {"role": "tool", "tool_call_id": "nope", "content": "stray"},
            {"role": "user", "content": "next"}]
    out, changed = repair_tool_pairs(msgs)          # the one shared repair (ws10 history_repair)
    assert changed
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "tool", "user"]
    assert out[3]["tool_call_id"] == "c1" and out[3]["content"].startswith("Not run")
    assert out[4]["content"] == "two"
    assert len(msgs) == 7                              # the input is not changed
