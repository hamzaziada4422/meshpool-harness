"""WS04 WP2: one gate for every model call (M28-02, P-12): provider-sized limit, waiting state, endpoint health
shared across processes, and SimpleLedger.chat (engines, helpers, reviewers) going through both."""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meshpool_harness.core import gateway as GW

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "sk-test-secret-12345678"


class Mock:
    """A threaded OpenAI-style server: counts requests running at once, can delay, answers chat completions with
    plain JSON. A request with max_tokens <= 1 is a health probe: it is counted apart and answers probe_status."""

    def __init__(self, delay=0.0, probe_status=200):
        self.delay, self.probe_status = delay, probe_status
        self.lock = threading.Lock()
        self.running = self.max_running = self.chats = self.probes = 0
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if (body.get("max_tokens") or 99) <= 1:
                    with mock.lock:
                        mock.probes += 1
                    return self._send(mock.probe_status, {"error": "probe"} if mock.probe_status != 200 else
                                      {"choices": [{"message": {"role": "assistant", "content": "p"}}]})
                with mock.lock:
                    mock.chats += 1
                    mock.running += 1
                    mock.max_running = max(mock.max_running, mock.running)
                try:
                    time.sleep(mock.delay)
                    self._send(200, {"id": "x", "choices": [{"index": 0, "finish_reason": "stop",
                                                              "message": {"role": "assistant", "content": "ok"}}],
                                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
                finally:
                    with mock.lock:
                        mock.running -= 1

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """No fixed limit unless a test sets one; no saved settings; plain (non-streamed) replies."""
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "none.config"))
    monkeypatch.delenv("MH_MAX_INFLIGHT", raising=False)
    monkeypatch.setenv("MH_STREAM", "0")
    monkeypatch.setenv("MH_RECOVER_MIN", "0")
    GW._CFG["t"] = 0
    yield
    GW._CFG["t"] = 0


@pytest.fixture
def mock():
    m = Mock()
    yield m
    m.close()


def _fixed(monkeypatch, n):
    monkeypatch.setenv("MH_MAX_INFLIGHT", str(n))
    GW._CFG["t"] = 0


# ---------- P-12: the limit follows the provider ----------------------------------------------------------------
def test_limit_follows_the_provider(env):
    assert GW.health("https://llm.meshpool.ai/v1", "k").gate.limit == 16
    assert GW.health("https://eu.meshpool.ai/v1", "k").gate.limit == 16
    for host in ("api.openai.com", "api.anthropic.com", "api.deepseek.com", "openrouter.ai",
                 "generativelanguage.googleapis.com", "api.x.ai", "api.moonshot.ai", "open.bigmodel.cn"):
        assert GW.health(f"https://{host}/v1", "k").gate.limit == 32, host
    for base in ("http://localhost:11434/v1", "http://127.0.0.1:8080/v1", "http://[::1]:8080/v1",
                 "http://0.0.0.0:1/v1"):
        assert GW.health(base, "k").gate.limit == 2, base
    assert GW.health("https://models.example.org/v1", "k").gate.limit == 8


def test_a_local_or_ollama_endpoint_takes_two_calls_at_once(env):
    """Release review, 0.0.18: the local cap went from 2 to 6, which thrashes a model served by Ollama."""
    for base in ("http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://gpu-box.lan:11434/v1",
                 "http://ollama:8000/v1"):
        assert GW.provider_cap(base) == 2, base
    assert GW.provider_cap("https://models.example.org/v1") == 8


def test_a_fixed_setting_stays_a_cap(env, monkeypatch):
    _fixed(monkeypatch, 4)
    g = GW.health("https://llm.meshpool.ai/v1", "k").gate
    assert g.limit == 4 and g.cap == 4
    assert GW.health("http://localhost:1/v1", "k").gate.limit == 4          # the setting wins for every endpoint


def test_ten_chats_on_the_meshpool_gateway_wait_for_nobody(env):
    h = GW.health("https://llm.meshpool.ai/v1", "k")
    assert h.gate.limit >= 10
    events, slots, t0 = [], [], time.time()
    ts = [threading.Thread(target=lambda: slots.append(h.acquire(lambda: None, events.append))) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert len(slots) == 10 and time.time() - t0 < 2 and h.gate.inflight == 10 and h.gate.waiting == 0
    assert not [e for e in events if e["type"] in ("gate", "status")]
    for s in slots:
        h.release(True, s)


def test_a_429_halves_the_limit_and_ten_good_calls_give_one_back(env):
    g = GW.health("https://llm.meshpool.ai/v1", "k").gate
    g.throttled()
    assert g.limit == 8
    g.throttled()
    assert g.limit == 4
    for _ in range(9):
        g.release(True)
    assert g.limit == 4
    g.release(True)
    assert g.limit == 5                                                     # 10 in a row: +1
    g.release(False)
    for _ in range(9):
        g.release(True)
    assert g.limit == 5                                                     # a failure restarts the count
    for _ in range(300):
        g.release(True)
    assert g.limit == 16                                                    # never above the cap
    g.degrade()
    assert g.limit == 1


def test_a_429_from_the_endpoint_lowers_the_gate_through_call(env):
    base = "https://llm.meshpool.ai/v1"

    def send():
        raise RuntimeError("meshpool HTTP 429: slow down")
    with pytest.raises(RuntimeError):
        GW.call(base, "k", "m", send)
    g = GW.health(base, "k", "m").gate
    assert g.limit == 8 and g.inflight == 0
    with pytest.raises(RuntimeError):
        GW.call(base, "k", "m", lambda: (_ for _ in ()).throw(RuntimeError("HTTP 429: budget exceeded")))
    assert g.limit == 8                                                     # a spent budget is not a rate limit
    assert g.inflight == 0


# ---------- waiting state -------------------------------------------------------------------------------------
def test_a_waiting_call_says_so_and_the_snapshot_shows_it(env, monkeypatch):
    _fixed(monkeypatch, 1)
    h = GW.health("https://llm.meshpool.ai/v1", KEY, "m")
    first = h.acquire(lambda: None)
    events, got = [], []
    t = threading.Thread(target=lambda: got.append(h.acquire(lambda: None, events.append)))
    t.start()
    time.sleep(0.5)
    snap = GW.snapshot()
    assert snap == [{"endpoint": "llm.meshpool.ai", "inflight": 1, "limit": 1, "cap": 1, "waiting": 1,
                     "degraded": False, "down_models": []}]
    assert KEY not in json.dumps(snap)
    assert {"type": "gate", "state": "waiting", "inflight": 1, "limit": 1} in events
    assert [e["text"] for e in events if e["type"] == "status"] == [
        "Waiting: other meshpool chats are using the model right now (1 of 1)."]
    h.release(True, first)
    t.join(5)
    gate_events = [e for e in events if e["type"] == "gate"]
    assert [e["state"] for e in gate_events] == ["waiting", "acquired"] and gate_events[1]["waited_s"] >= 0.4
    assert GW.snapshot()[0]["waiting"] == 0
    h.release(True, got[0])


def test_a_long_wait_without_a_listener_writes_one_line_to_stderr(env, monkeypatch, capsys):
    _fixed(monkeypatch, 1)
    h = GW.health("https://llm.meshpool.ai/v1", "k")
    first = h.acquire(lambda: None)
    threading.Timer(2.6, lambda: h.release(True, first)).start()
    second = h.acquire(lambda: None)
    h.release(True, second)
    assert capsys.readouterr().err.count("Waiting: other meshpool chats are using the model right now (1 of 1).") == 1


# ---------- M28-02: engine and helper calls go through the gate ---------------------------------------------------
def test_simple_ledger_calls_obey_the_cap(env, monkeypatch, mock):
    from meshpool_harness.core.wire import SimpleLedger
    mock.delay = 0.5
    _fixed(monkeypatch, 2)
    monkeypatch.setenv("MESHPOOL_BASE_URL", mock.base)
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    out, errs = [], []

    def one():
        try:
            out.append(SimpleLedger("t").chat(model="m", messages=[{"role": "user", "content": "hi"}])
                       ["choices"][0]["message"]["content"])
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=one) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errs and out == ["ok"] * 6
    assert mock.max_running == 2 and mock.chats == 6


def test_an_endpoint_marked_down_by_another_process_is_not_hammered(env, monkeypatch, mock):
    from meshpool_harness.core.wire import SimpleLedger
    mock.probe_status = 503                                                 # the probe finds it still down
    monkeypatch.setenv("MESHPOOL_BASE_URL", mock.base)
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    led = SimpleLedger("t")
    code = ("import sys; from meshpool_harness.core import gateway as GW; "
            "GW.health(sys.argv[1], sys.argv[2], 'm')._mark_down()")
    r = subprocess.run([sys.executable, "-c", code, led.base_url, led.api_key], cwd=APP, timeout=60,
                       env={**os.environ, "PYTHONPATH": APP}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert any(f.endswith(".down") for f in os.listdir(os.environ["MH_SLOTS_DIR"]))
    with pytest.raises(RuntimeError, match="marked down by another chat or process"):
        led.chat(model="m", messages=[{"role": "user", "content": "hi"}])
    assert mock.chats == 0                                                  # nothing was sent
    assert mock.probes == 1                                                 # one probe asked whether it is back
    # another model on the same key is not held back
    mock.probe_status = 200
    assert led.chat(model="other", messages=[{"role": "user", "content": "hi"}])["choices"][0]["message"]["content"] == "ok"


def test_an_endpoint_that_answers_the_probe_is_used_again(env, monkeypatch, mock):
    from meshpool_harness.core.wire import SimpleLedger
    monkeypatch.setenv("MESHPOOL_BASE_URL", mock.base)
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    led = SimpleLedger("t")
    h = GW.health(led.base_url, led.api_key, "m")
    d = os.environ["MH_SLOTS_DIR"]
    os.makedirs(d, exist_ok=True)
    marker = os.path.join(d, f"{h.gate.name}-{__import__('hashlib').sha256(b'm').hexdigest()[:8]}.down")
    with open(marker, "w") as f:
        json.dump({"since": time.time(), "next_probe": 0, "pid": os.getpid() + 1}, f)
    assert h.is_down()
    assert led.chat(model="m", messages=[{"role": "user", "content": "hi"}])["choices"][0]["message"]["content"] == "ok"
    assert mock.probes == 1 and mock.chats == 1
    assert not os.path.exists(marker) and not h.is_down()                   # back up: the marker is gone


# ---------- the marker ----------------------------------------------------------------------------------------
def _marker(h):
    return h._marker_path()


def test_marker_written_on_down_and_cleared_on_up(env):
    h = GW.health("https://models.example.org/v1", "k", "m")
    h._mark_down()
    d = json.load(open(_marker(h)))
    assert d["pid"] == os.getpid() and d["since"] > 0 and "next_probe" in d
    assert h.is_down()
    with h.cond:
        h._mark_up()
    assert not os.path.exists(_marker(h)) and not h.is_down()


def test_a_stale_marker_is_ignored_and_a_fresh_one_from_another_process_counts(env, monkeypatch):
    h = GW.health("https://models.example.org/v1", "k", "m")
    os.makedirs(os.path.dirname(_marker(h)), exist_ok=True)

    def write(since, pid):
        with open(_marker(h), "w") as f:
            json.dump({"since": since, "next_probe": 0, "pid": pid}, f)
    write(time.time() - 31 * 60, os.getpid() + 1)
    assert not h.is_down()                                                  # older than 30 minutes
    write(time.time() - 5 * 60, os.getpid() + 1)
    assert h.is_down()
    monkeypatch.setenv("MH_DOWN_MARKER_S", "60")
    assert not h.is_down()                                                  # the window is configurable
    monkeypatch.delenv("MH_DOWN_MARKER_S")
    write(time.time(), os.getpid())
    assert not h.is_down()                                                  # our own marker says nothing new
    with open(_marker(h), "w") as f:
        f.write("not json")
    assert not h.is_down()                                                  # a broken marker never raises


def test_wait_until_up_works_when_only_the_marker_says_down(env, monkeypatch):
    monkeypatch.setattr(GW, "BACKOFF_S", 0.05)
    h = GW.health("https://models.example.org/v1", "k", "m")
    os.makedirs(os.path.dirname(_marker(h)), exist_ok=True)
    with open(_marker(h), "w") as f:
        json.dump({"since": time.time(), "next_probe": 0, "pid": os.getpid() + 1}, f)
    assert h.wait_until_up(lambda: "HTTP 503", lambda s: time.sleep(0.01), lambda e: None, 0, lambda: None) is False
    assert h.is_down()
    assert h.wait_until_up(lambda: "ok", lambda s: time.sleep(0.01), lambda e: None, 5, lambda: None) is True
    assert not h.is_down() and not os.path.exists(_marker(h))


def test_a_marker_that_cannot_be_written_never_raises(env, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("MH_SLOTS_DIR", str(blocker / "sub"))                # a folder that cannot be made
    h = GW.health("https://models.example.org/v1", "k", "m")
    h._mark_down()
    assert h.is_down()
    with h.cond:
        h._mark_up()
    assert not h.is_down()


def test_a_side_call_fails_at_once_on_a_down_endpoint_without_probing(env, monkeypatch, mock):
    """Compaction, memory, classifiers (side calls) must not wait out an outage another process saw."""
    from meshpool_harness.core.wire import SimpleLedger
    monkeypatch.setenv("MESHPOOL_BASE_URL", mock.base)
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    monkeypatch.setenv("MH_RECOVER_MIN", "30")                              # a main call would wait up to 30 min
    led = SimpleLedger("t")
    with GW.health(led.base_url, led.api_key, "m").cond:
        GW.health(led.base_url, led.api_key, "m")._mark_down()
    t0 = time.time()
    with pytest.raises(RuntimeError, match="marked down"):
        led.chat(model="m", messages=[{"role": "user", "content": "hi"}], side=True)
    assert time.time() - t0 < 2
    assert mock.chats == 0 and mock.probes == 0
