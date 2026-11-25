"""Refusal parser (core/refusals.py), learned model facts that persist (core/modelcaps.py), the 4xx classes of
classify_error, and the window a vLLM / llama.cpp server reports in /v1/models. No real network: local http.server."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness.core import client as CL  # noqa: E402
from meshpool_harness.core import context as CX  # noqa: E402
from meshpool_harness.core import modelcaps as MC  # noqa: E402
from meshpool_harness.core import refusals as RF  # noqa: E402

BODY = dict({k: 1 for k in RF.KNOWN}, model="m", messages=[], max_tokens=8192, tools=[])
SAMPLES = {n: m for n, m, a in RF.ERROR_SAMPLES}


@pytest.fixture(autouse=True)
def caps_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_CAPS_LEARNED", str(tmp_path / "learned.json"))
    MC.reset()
    yield tmp_path / "learned.json"
    MC.reset()


@pytest.mark.parametrize("name,msg,action", RF.ERROR_SAMPLES, ids=[s[0] for s in RF.ERROR_SAMPLES])
def test_samples(name, msg, action):
    d = RF.diagnose(msg)
    assert d and d["action"] == action, d
    assert d["note"] and isinstance(d["learn"], dict)
    d2 = RF.diagnose(msg, BODY)
    assert d2 and d2["action"] == action


def test_unrelated_is_none():
    assert RF.diagnose("HTTP 500: boom") is None
    assert RF.diagnose("HTTP 400: Invalid role 'user' for this thing") is None
    assert RF.diagnose("") is None


def test_tls_any_status_learns_nothing():
    d = RF.diagnose("HTTP 0: connection problem (SSLError(1, 'x'))")
    assert d["action"] == "tls" and d["learn"] == {} and "SSL_CERT_FILE" in d["note"]
    assert RF.is_tls_error("[SSL: CERTIFICATE_VERIFY_FAILED]") and not RF.is_tls_error("HTTP 500")


@pytest.mark.parametrize("msg", [m for n, m, a in RF.ERROR_SAMPLES if a == "drop_field"])
def test_drop_field_never_model_messages(msg):
    for body in (None, BODY):
        d = RF.diagnose(msg, body)
        assert d["field"] not in ("model", "messages", "max_tokens")
        assert set(d["learn"]["refused"]) == set(d["fields"])


@pytest.mark.parametrize("name", ["gemini_unknown_field", "azure_unrecognized", "pydantic_422"])
def test_drop_prompt_cache_key(name):
    assert RF.diagnose(SAMPLES[name], BODY)["field"] == "prompt_cache_key"


def test_drop_only_fields_present_in_body():
    msg = "HTTP 400: Unrecognized request argument supplied: prompt_cache_key"
    assert RF.diagnose(msg, {"model": "m", "messages": []}) is None
    plural = "HTTP 400: Unrecognized request arguments supplied: prompt_cache_key, user"
    assert set(RF.diagnose(plural, BODY)["fields"]) == {"prompt_cache_key", "user"}
    assert RF.diagnose('HTTP 400: Unknown name "model": Cannot find field.', {"model": "m"}) is None


def test_422_prefers_loc():
    msg = ('HTTP 422: {"detail":[{"type":"extra_forbidden","loc":["body","stream_options","include_usage"],'
           '"msg":"Extra inputs are not permitted","input":true}]}')
    assert RF.diagnose(msg, dict(BODY, stream_options={"include_usage": True}))["field"] == "stream_options"


def test_max_out_vllm_new_and_old():
    d = RF.diagnose(SAMPLES["vllm_new"])
    assert d["action"] == "max_out" and d["max_output"] == 4096 - 120 - 64 and d["learn"] == {"window": 4096}
    d = RF.diagnose(SAMPLES["vllm_old"])
    assert d["max_output"] == 4096 - 120 - 64 and "max_output" not in d["learn"]


def test_max_out_pure_caps():
    for n in ("openai_cap", "cap_le", "cap_le_sym", "anthropic_cap"):
        d = RF.diagnose(SAMPLES[n])
        assert d["max_output"] == 4096 and d["learn"] == {"max_output": 4096}


def test_real_overflow_is_not_max_out():
    msg = ("HTTP 400: This model's maximum context length is 4096 tokens. However, you requested 13192 tokens "
           "(5000 in the messages, 8192 in the completion). Please reduce the length.")
    assert RF.diagnose(msg) is None
    msg2 = ("HTTP 400: 'max_tokens' or 'max_completion_tokens' is too large: 8192. This model's maximum context length "
            "is 4096 tokens and your request has 5000 input tokens (8192 > 4096 - 5000).")
    assert RF.diagnose(msg2) is None
    assert CL.classify_error(msg) == "context"


def test_learned_persists_across_reset(caps_file):
    base = "https://api.example.com/v1"
    MC.learn(base, "m1", no_tools=True, tool_id_style="alnum9", window=4096, usage_cached=True)
    data = json.loads(caps_file.read_text())
    assert data["api.example.com"]["m1"] == {"no_tools": True, "tool_id_style": "alnum9", "window": 4096}
    MC.reset()                                           # a new process
    c = MC.resolve(base, "m1")
    assert c.no_tools and c.tool_id_style == "alnum9" and c.window == 4096 and not c.usage_cached
    assert MC.resolve(base, "other").no_tools is False


def test_refused_union_and_no_rewrite(caps_file):
    base = "https://api.example.com/v1"
    MC.learn(base, "m", refused=["user"])
    MC.learn(base, "m", refused=["prompt_cache_key"])
    assert MC.resolve(base, "m").refused == ("prompt_cache_key", "user")
    stamp = caps_file.stat().st_mtime_ns
    MC.learn(base, "m", refused=["user"], usage_cached=True)         # nothing persisted changed
    assert caps_file.stat().st_mtime_ns == stamp
    MC.reset()
    assert MC.resolve(base, "m").refused == ("prompt_cache_key", "user")


def test_off_disables_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_CAPS_LEARNED", "off")
    MC.reset()
    MC.learn("https://x.example.com/v1", "m", no_tools=True)
    assert MC.resolve("https://x.example.com/v1", "m").no_tools
    assert not list(tmp_path.glob("*.json"))


def test_corrupt_file_quarantined(caps_file):
    caps_file.write_text("{not json")
    assert MC.resolve("https://api.example.com/v1", "m").no_tools is False
    assert list(caps_file.parent.glob("learned.json.corrupt-*"))
    MC.learn("https://api.example.com/v1", "m", no_tools=True)
    assert json.loads(caps_file.read_text())["api.example.com"]["m"]["no_tools"] is True


def test_corrupt_file_when_learning(caps_file):
    MC.resolve("https://api.example.com/v1", "m")
    caps_file.write_text("")
    MC.learn("https://api.example.com/v1", "m", strict_roles=True)    # never raises
    assert MC.resolve("https://api.example.com/v1", "m").strict_roles


def test_concurrent_refused_both_stored(caps_file):
    base = "https://api.example.com/v1"
    names = [f"f{i}" for i in range(12)]
    ts = [threading.Thread(target=MC.learn, args=(base, "m"), kwargs={"refused": [n]}) for n in names]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert set(MC.resolve(base, "m").refused) == set(names)
    assert set(json.loads(caps_file.read_text())["api.example.com"]["m"]["refused"]) == set(names)


def test_learned_window_beats_context(monkeypatch):
    base = "https://api.example.com/v1"
    monkeypatch.setattr(CX, "window", lambda *a, **k: 200_000)
    assert MC.resolve(base, "m").window == 200_000
    MC.learn(base, "m", window=4096)
    assert MC.resolve(base, "m").window == 4096


def test_timeouts(monkeypatch):
    monkeypatch.delenv("MH_LOCAL_FIRST_BYTE_S", raising=False)       # conftest shortens it for loopback mocks
    local, cloud = "http://127.0.0.1:11434/v1", "https://api.example.com/v1"
    assert MC.is_local(local) and not MC.is_local(cloud)
    assert MC.first_byte_timeout(cloud, "m", 60) == 60
    assert MC.first_byte_timeout(local, "m", 60) == 900
    monkeypatch.setenv("MH_LOCAL_FIRST_BYTE_S", "5000")
    assert MC.first_byte_timeout(local, "m", 60) == 3600
    monkeypatch.delenv("MH_LOCAL_FIRST_BYTE_S")
    assert MC.probe_timeout(cloud, 8) == 8 and MC.probe_timeout(local, 8) == 120
    monkeypatch.setenv("MH_LOCAL_PROBE_S", "30")
    assert MC.probe_timeout(local, 8) == 30
    MC.observe_first_byte(cloud, "m", 3)                  # fast: not learned
    assert MC.first_byte_timeout(cloud, "m", 60) == 60
    MC.observe_first_byte(cloud, "m", 40)
    assert MC.first_byte_timeout(cloud, "m", 60) == 120
    MC.observe_first_byte(cloud, "m", 10)                 # decays 40 -> 32, never below the new sample
    assert MC.resolve(cloud, "m").first_byte_s == 32.0


@pytest.mark.parametrize("msg,kind", [
    ("HTTP 422: " + '{"detail":[{"type":"extra_forbidden","loc":["body","x"],"msg":"Extra inputs are not permitted"}]}',
     "bad_request"),
    ("HTTP 405: method not allowed", "bad_request"),
    ("HTTP 409: conflict", "bad_request"),
    ("HTTP 415: unsupported media type", "bad_request"),
    ("HTTP 0: connection problem (<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed>)", "tls"),
    ("HTTP 422: maximum context length is 4096 tokens", "context"),
    ("HTTP 500: oops", "transient"), ("HTTP 502: bad gateway", "transient"),
    ("HTTP 401: nope", "auth"), ("HTTP 403: nope", "auth"),
    ('HTTP 404: {"error":"model \'x\' not found"}', "model_missing"),
    ("HTTP 402: payment required", "quota"), ("HTTP 429: slow down", "rate_limit"),
    ("HTTP 400: rejected as invalid", "gateway_reject"),
    ("HTTP 413: too large", "context"),
    ("HTTP 400: image input not supported", "vision"), ("HTTP 415: bad image content type", "vision"),
    ("HTTP 400: nothing special", "bad_request"),
])
def test_classify(msg, kind):
    assert CL.classify_error(msg) == kind


class _Models(BaseHTTPRequestHandler):
    def do_GET(self):
        if "/models" in self.path:
            body = json.dumps({"object": "list", "data": [
                {"id": "small-vllm", "max_model_len": 4096},
                {"id": "gguf", "meta": {"n_ctx": 8192, "n_ctx_train": 32768}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


def test_listed_reads_max_model_len_and_n_ctx():
    srv = HTTPServer(("127.0.0.1", 0), _Models)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    try:
        CX._LISTS.pop(base, None)
        got = CX._listed(base, "")
        assert got["small-vllm"] == 4096 and got["gguf"] == 8192
        assert CX.probe(base, "small-vllm") == 4096
        assert CX.probe(base, "gguf") == 8192
    finally:
        srv.shutdown()
        CX._LISTS.pop(base, None)
