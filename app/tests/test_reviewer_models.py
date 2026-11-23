"""S-03: reviewers must only call models the endpoint serves, and must not resend a parameter it refused.

A local single-deployment endpoint serves one model ("m-main"): every other model is a 404 DeploymentNotFound and a
request carrying `temperature` is a 400 (a reasoning model). The old fixed chains (review.CHAIN, clarity.CHAIN,
sheets REVIEW_CHAIN) called built-in names on it (70 of 130 calls for one deck were 404s) and retried the 400 with
the same parameters."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from meshpool_harness.core import client as CL
from meshpool_harness.core import modelcaps as MC
from meshpool_harness.core import models as MD

BUILTIN = ("claude-sonnet-5.5", "gpt-6.1-terra", "kimi-k3", "gpt-6-luna", "gpt-5.6-luna")
REPLY = {"edits": []}


class _Endpoint:
    def __init__(self):
        self.requests, self.lock = [], threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._send(404, {"error": {"message": "no models list"}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if "messages" not in body:                      # the client's context-window probe, not a model call
                    return self._send(404, {"error": {"message": "unknown"}})
                with outer.lock:
                    outer.requests.append({"model": body.get("model"), "keys": sorted(body), "body": body})
                if body.get("model") != "m-main":
                    return self._send(404, {"error": {"code": "DeploymentNotFound",
                                                      "message": "The API deployment for this resource does not exist."}})
                if "temperature" in body:
                    return self._send(400, {"error": {"message": "Unsupported parameter: 'temperature' is not supported "
                                                      "with this model.", "type": "invalid_request_error",
                                                      "param": "temperature", "code": "unsupported_parameter"}})
                self._send(200, {"id": "x", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps(REPLY)}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def calls(self, model=None):
        return [r for r in self.requests if model is None or r["model"] == model]


@pytest.fixture
def ep(monkeypatch, tmp_path):
    e = _Endpoint()
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_BASE_URL", e.url)
    monkeypatch.setenv("MESHPOOL_API_KEY", "x")
    monkeypatch.setenv("MH_MODEL", "m-main")
    monkeypatch.setenv("MH_STREAM", "0")
    for k in ("MH_REVIEW_MODEL", "MH_REVIEWER_MODELS", "FORGE_CLARITY_MODEL", "MH_STORY_REVIEW_MODEL",
              "MH_SHEETS_REVIEW_MODEL", "MH_DESIGN_VISION_MODEL", "MH_MODEL_MEMORY_TTL_S", "MH_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    MC.reset()
    yield e
    e.srv.shutdown()
    MC.reset()


def _slides():
    return [{"archetype": "bar_takeaway", "title": "Revenue grew 20% as pricing levers unlocked upside",
             "bullets": ["Volume fell 4% overall", "Costs fell 3% after the plant closed"]}]


def _chat(model="m-main", **kw):
    return CL.LiveClient("forge_review").chat(model=model, messages=[{"role": "user", "content": "hi"}], **kw)


def test_clarity_calls_only_the_deployed_model_and_learns_temperature(ep):
    from engines.forge import clarity
    rep = clarity.review(_slides(), CL.LiveClient("forge_review"), main_model="m-main")
    assert {r["model"] for r in ep.requests} == {"m-main"}              # no built-in model name was ever requested
    assert len(ep.calls()) == 2 and "temperature" in ep.calls()[0]["keys"]
    assert "temperature" not in ep.calls()[1]["keys"]                    # exactly one 400, then sent without it
    assert rep["model"] == "m-main" and rep.get("status") != "not_run"


def test_unsupported_parameter_is_remembered_across_clients(ep):
    _chat(temperature=0.2)
    assert len(ep.calls()) == 2
    n = len(ep.requests)
    _chat(temperature=0.2)                                                # a new client: nothing is refused again
    assert len(ep.requests) == n + 1 and "temperature" not in ep.requests[-1]["keys"]
    assert MC.refused(ep.url, "m-main") == {"temperature"}


def test_missing_model_is_called_once_then_never(ep):
    with pytest.raises(RuntimeError, match="404"):
        _chat("x-other")
    assert len(ep.calls("x-other")) == 1
    with pytest.raises(RuntimeError, match="remembered; not called"):
        _chat("x-other")
    assert len(ep.calls("x-other")) == 1                                  # no second request, nothing billed
    assert MD.reviewer_models("x-other") == ["m-main"]                    # and it leaves the reviewer list


def test_reviewer_lists_have_no_builtin_names(ep, monkeypatch):
    from engines.forge import critics, review
    from engines.sheets.engine import WorkbookReview
    assert MD.reviewer_models() == ["m-main"] and MD.vision_reviewer_models() == ["m-main"]
    assert review.chain() == critics.deck_models({}) == critics.text_models({}) == ["m-main"]
    assert WorkbookReview(None, None, 5).models == ["m-main"]
    monkeypatch.setenv("MH_REVIEW_MODEL", "my-vision")
    monkeypatch.setenv("MH_STORY_REVIEW_MODEL", "my-text")
    assert review.chain("explicit") == ["explicit", "my-vision", "m-main"]
    assert critics.text_models({"text_models": ["t1"]}) == ["my-text", "t1", "m-main"]
    assert critics.deck_models({"deck_models": ["d1"]}) == ["d1", "my-vision", "m-main"]
    assert WorkbookReview(None, None, 5, model="sx").models == ["sx", "my-vision", "m-main"]
    monkeypatch.setenv("MH_REVIEWER_MODELS", "a, b")
    assert MD.reviewer_models("z") == ["z", "a", "b", "m-main"]
    for lst in (MD.reviewer_models(), review.chain(), critics.deck_models({})):
        assert not set(lst) & set(BUILTIN)


def test_review_deck_skips_when_no_model_can_read_images(ep, tmp_path):
    from PIL import Image

    from engines.forge import review
    png = tmp_path / "s1.png"
    Image.new("RGB", (8, 8), "white").save(png)
    MC.learn_no_vision(ep.url, "m-main")
    assert MD.vision_reviewer_models() == [] and MD.reviewer_models() == ["m-main"]
    rv = review.review_deck("", pngs=[(1, Path(png))], client=CL.LiveClient("forge_review"))
    assert rv["status"] == "not_run" and rv["errors"][0].startswith("skipped: no reviewer model")
    assert ep.requests == []


def test_image_refusal_marks_the_model_text_only(ep):
    class Reject(CL.LiveClient):
        def __init__(self):
            super().__init__("forge_review")
            self.ledger.chat = self._chat

        def _chat(self, **kw):
            raise RuntimeError("meshpool HTTP 400: {'error': {'message': 'this model does not support image content parts'}}")
    parts = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    with pytest.raises(RuntimeError):
        Reject().chat(model="m-main", messages=[{"role": "user", "content": parts}])
    assert MC.no_vision(ep.url, "m-main") and MD.vision_reviewer_models() == []


def test_critics_review_one_skips_a_missing_model(ep):
    from engines.forge import critics
    seen = []

    import engines.review.personas as PE
    orig = PE.review
    try:
        PE.review = lambda c, persona, target, material, m, **k: seen.append(m) or {
            "persona": persona, "target": target, "status": "ok", "verdict": "sign_off", "overall": 9, "scores": {},
            "issues": [], "research_requests": []}
        MC.learn_missing(ep.url, "gone")
        r = critics.review_one(None, "partner", "deck", "m", ["gone", "m-main"])
        assert seen == ["m-main"] and r["status"] == "ok" and not r.get("fallbacks")
        none = critics.review_one(None, "partner", "deck", "m", ["gone"])
        assert none["status"] == "not_run" and none["error"].startswith("skipped:")
    finally:
        PE.review = orig


def test_error_classes():
    msg = ("meshpool HTTP 404: {'error': {'code': 'DeploymentNotFound', 'message': 'The API deployment for this "
           "resource does not exist.'}}")
    assert CL.classify_error(msg) == "model_missing"
    assert CL.classify_error("meshpool HTTP 400: The API deployment for this resource does not exist") == "model_missing"
    assert CL.classify_error("meshpool HTTP 400: Unsupported parameter: 'temperature'") == "bad_request"
    assert CL.classify_error("meshpool HTTP 401: nope") == "auth"
    keys = {"model", "messages", "temperature", "top_p", "max_tokens"}
    cases = {
        "meshpool HTTP 400: Unsupported parameter: 'temperature' is not supported with this model.": "temperature",
        "meshpool HTTP 400: Unsupported value: 'temperature' does not support 0.2 with this model. Only the default "
        "(1) value is supported.": "temperature",
        "meshpool HTTP 400: {'error': {'message': 'bad', 'param': 'temperature', 'code': 'unsupported_parameter'}}":
            "temperature",
        '{"error": {"message": "x", "param": "top_p", "code": "unsupported_value"}}': "top_p",
        "meshpool HTTP 400: temperature is not supported": "temperature",
        "meshpool HTTP 400: 'top_p' is not supported": "top_p",
    }
    for text, want in cases.items():
        assert MD.unsupported_param(text, keys) == want, text
    assert MD.unsupported_param("Unsupported parameter: 'seed' is not supported", keys) is None   # not sent
    assert MD.unsupported_param("meshpool HTTP 400: Unsupported parameter: 'model'", keys) is None
    assert MD.unsupported_param("", keys) is None


def test_memory_expires_and_survives_damage(ep, monkeypatch):
    MC.learn_missing(ep.url, "old-model")
    assert MC.is_missing(ep.url, "old-model")
    monkeypatch.setenv("MH_MODEL_MEMORY_TTL_S", "0.2")
    time.sleep(0.4)
    assert not MC.is_missing(ep.url, "old-model") and "old-model" in MD.reviewer_models("old-model")
    monkeypatch.delenv("MH_MODEL_MEMORY_TTL_S")
    path = MC._mem_path()
    path.write_text("{ damaged", encoding="utf-8")                        # a damaged file counts as empty ...
    MC.reset()
    assert not MC.is_missing(ep.url, "old-model")
    MC.learn_missing(ep.url, "new-model")                                 # ... and is overwritten by the next mark
    MC.reset()
    assert MC.is_missing(ep.url, "new-model") and json.loads(path.read_text())
