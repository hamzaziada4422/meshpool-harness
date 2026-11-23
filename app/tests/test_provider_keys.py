"""Provider keys: Azure and every common kind of key, against mock servers that enforce each provider's own header,
URL and field rules (a request in the wrong style is refused with that provider's own error), so a regression in
core/endpoint.py fails here instead of at a customer.

The mocks run on 127.0.0.1, so a provider's kind is set the way the app saves it (MH_API_KIND) or passed in; host
detection itself is tested separately on plain strings."""
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meshpool_harness import config as C
from meshpool_harness import provider_setup as PS
from meshpool_harness import providers as PV
from meshpool_harness.core import endpoint as EP
from meshpool_harness.core import key_relay
from meshpool_harness.core.wire import SimpleLedger

KEY = "sk-good-key-1234567"
ENV = ("MESHPOOL_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
       "MESHPOOL_BASE_URL", "MH_PROVIDER", "MH_MODEL", "MH_API_WIRE", "MH_API_KIND", "MH_API_VERSION",
       "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_DEPLOYMENT",
       "OPENROUTER_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "MISTRAL_API_KEY", "XAI_API_KEY",
       "TOGETHER_API_KEY", "FIREWORKS_API_KEY", "DEEPSEEK_API_KEY", "PERPLEXITY_API_KEY", "CEREBRAS_API_KEY",
       "SAMBANOVA_API_KEY", "NVIDIA_API_KEY", "MH_STREAM", "MH_WIRE", "MH_SUBAGENT_MODEL")

# where each provider serves chat completions, relative to the address the user pastes
PREFIX = {"generic": "/v1", "openai": "/v1", "openrouter": "/api/v1", "groq": "/openai/v1", "together": "/v1",
          "fireworks": "/inference/v1", "mistral": "/v1", "xai": "/v1", "deepseek": "", "perplexity": "",
          "gemini": "/v1beta/openai", "bedrock": "/openai/v1", "github": "", "nvidia": "/v1", "cerebras": "/v1",
          "sambanova": "/v1", "ollama": "/v1", "lmstudio": "/v1", "azure-classic": "", "azure-v1": "/openai/v1",
          "azure-foundry": "/models", "azure-anthropic": "/anthropic/v1", "anthropic": "/v1"}
DEPLOYMENTS = ("my-gpt4o", "o3-prod")
SUPPORTED_VERSIONS = ("2024-10-21", "2024-06-01", "2025-04-01-preview")
BEARER_KINDS = [k for k in PREFIX if EP.caps(k)["auth"] == "bearer"]


class Provider:
    """A mock provider that enforces `kind`'s rules. .log holds every request: (method, path, headers, body)."""

    def __init__(self, kind, key=KEY):
        self.kind, self.key, self.log, self.prefix = kind, key, [], PREFIX[kind]
        outer = self
        c = EP.caps(kind)

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, obj, ctype="application/json", extra=None):
                b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b)

            def _auth_ok(self):
                h = {k.lower(): v for k, v in self.headers.items()}
                bearer = h.get("authorization", "").removeprefix("Bearer ")
                if c["auth"] == "api-key":
                    return h.get("api-key") == outer.key
                if kind == "azure-v1":                   # the v1 route takes the api-key header or a Bearer token
                    return h.get("api-key") == outer.key or bearer == outer.key
                if c["auth"] == "x-api-key":
                    return h.get("x-api-key") == outer.key and bool(h.get("anthropic-version"))
                return bearer == outer.key and "api-key" not in h and "x-api-key" not in h

            def _deny(self):
                if kind.startswith("azure"):
                    return self._send(401, {"error": {"code": "401", "message": "Access denied due to invalid "
                                                      "subscription key or wrong API endpoint."}})
                return self._send(401, {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}})

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                outer.log.append(("GET", u.path, {k.lower(): v for k, v in self.headers.items()}, None))
                if not self._auth_ok():
                    return self._deny()
                if u.path == outer.prefix + "/models" and c["lists"]:
                    return self._send(200, {"object": "list", "data": [{"id": "model-a"}, {"id": "model-b"}]})
                self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                body = json.loads(raw or b"{}")
                outer.log.append(("POST", self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if not self._auth_ok():
                    return self._deny()
                model = body.get("model", "")
                if kind == "azure-classic":
                    m = re.fullmatch(r"/openai/deployments/([^/]+)/chat/completions", u.path)
                    if not m:
                        return self._send(404, {"error": {"code": "404", "message": "Resource not found"}})
                    model = urllib.parse.unquote(m.group(1))
                    if (q.get("api-version") or [""])[0] not in SUPPORTED_VERSIONS:
                        return self._send(400, {"error": {"code": "BadRequest", "message": "Unsupported API version"}})
                    if model not in DEPLOYMENTS:
                        return self._send(404, {"error": {"code": "DeploymentNotFound", "message":
                                                          "The API deployment for this resource does not exist."}})
                    if body.get("stream_options") and (q["api-version"][0] < "2024-09-01"):
                        return self._send(400, {"error": {"code": "BadRequest", "message": "stream_options unsupported"}})
                else:
                    if u.path != outer.prefix + ("/messages" if c["wire"] == "anthropic" else "/chat/completions"):
                        return self._send(404, {"error": {"message": "wrong path " + u.path}})
                    if kind == "azure-foundry" and (q.get("api-version") or [""])[0] != EP.AZURE_FOUNDRY_VERSION:
                        return self._send(400, {"error": {"code": "BadRequest", "message": "api-version required"}})
                    if kind == "azure-v1" and model not in DEPLOYMENTS:
                        return self._send(404, {"error": {"code": "DeploymentNotFound", "message": "deployment does not exist"}})
                if c["reasoning"] and EP.reasoning_model(model) and ("max_tokens" in body or "temperature" in body):
                    return self._send(400, {"error": {"message": "Unsupported parameter: 'max_tokens' is not supported "
                                                      "with this model. Use 'max_completion_tokens' instead."}})
                if c["wire"] == "anthropic":
                    return self._anthropic(body)
                msgs = body.get("messages", [])
                called = any(m.get("role") == "tool" for m in msgs)
                if body.get("tools") and not called:
                    delta = {"role": "assistant", "content": None, "tool_calls": [
                        {"index": 0, "id": "call_1", "type": "function",
                         "function": {"name": body["tools"][0]["function"]["name"], "arguments": "{\"x\": 1}"}}]}
                    fin = "tool_calls"
                else:
                    last = msgs[-1].get("content") if msgs else ""
                    delta, fin = {"role": "assistant", "content": f"pong from {kind}: {str(last)[:30]}"}, "stop"
                usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
                if not body.get("stream"):
                    return self._send(200, {"id": "c1", "object": "chat.completion", "model": model,
                                            "choices": [{"index": 0, "message": delta, "finish_reason": fin}], "usage": usage})
                ev = []
                if kind.startswith("azure"):             # Azure's first stream chunk carries no choices
                    ev.append({"id": "c1", "choices": [], "prompt_filter_results": []})
                if delta.get("tool_calls"):
                    ev.append({"id": "c1", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                else:
                    ev.append({"id": "c1", "choices": [{"index": 0, "delta": {"role": "assistant", "content": delta["content"][:6]}}]})
                    ev.append({"id": "c1", "choices": [{"index": 0, "delta": {"content": delta["content"][6:]}}]})
                ev.append({"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": fin}]})
                if (body.get("stream_options") or {}).get("include_usage"):
                    ev.append({"id": "c1", "choices": [], "usage": usage})
                data = "".join(f"data: {json.dumps(e)}\n\n" for e in ev) + "data: [DONE]\n\n"
                self._send(200, data.encode(), "text/event-stream")

            def _anthropic(self, body):
                if "max_tokens" not in body or not isinstance(body.get("messages"), list):
                    return self._send(400, {"type": "error", "error": {"message": "bad messages request"}})
                tool_result = any(isinstance(m.get("content"), list) and any(b.get("type") == "tool_result" for b in m["content"])
                                  for m in body["messages"])
                if body.get("tools") and not tool_result:
                    blocks, stop = [{"type": "tool_use", "id": "tu_1", "name": body["tools"][0]["name"], "input": {"x": 1}}], "tool_use"
                else:
                    blocks, stop = [{"type": "text", "text": "pong from anthropic"}], "end_turn"
                self._send(200, {"id": "m1", "type": "message", "role": "assistant", "model": body.get("model"),
                                 "content": blocks, "stop_reason": stop, "usage": {"input_tokens": 3, "output_tokens": 2}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.origin = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.base = self.origin + self.prefix            # what the user pastes
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def model(self):                                      # a model name this provider serves
        return DEPLOYMENTS[0] if EP.caps(self.kind)["deployment"] else "model-a"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MH_STREAM_IDLE_S", "10")
    PS._TESTS.clear()
    yield tmp_path
    key_relay.stop()


@pytest.fixture
def mk():
    made = []

    def f(kind, key=KEY):
        p = Provider(kind, key)
        made.append(p)
        return p
    yield f
    for p in made:
        p.close()


def use(p, model=None, version="", key=KEY, kind=None):
    """Save p as the key endpoint, the way the settings page does."""
    kind = kind or p.kind
    C.write({"MESHPOOL_BASE_URL": p.base, "MESHPOOL_API_KEY": key, "MH_API_KIND": "" if kind == "generic" else kind,
             "MH_API_VERSION": version, "MH_MODEL": model or p.model, "MH_API_WIRE": EP.wire_of(kind)})


# ---------- pure: recognising and splitting addresses --------------------------------------------------------------

@pytest.mark.parametrize("url,kind", [
    ("https://api.openai.com/v1", "openai"), ("https://api.anthropic.com", "anthropic"),
    ("https://openrouter.ai/api/v1", "openrouter"), ("https://api.groq.com/openai/v1", "groq"),
    ("https://api.together.xyz/v1", "together"), ("https://api.together.ai/v1", "together"),
    ("https://api.fireworks.ai/inference/v1", "fireworks"), ("https://api.mistral.ai/v1", "mistral"),
    ("https://api.x.ai/v1", "xai"), ("https://api.deepseek.com", "deepseek"),
    ("https://api.perplexity.ai", "perplexity"),
    ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini"),
    ("https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1", "bedrock"),
    ("https://bedrock-mantle.us-east-1.api.aws/v1", "bedrock"),
    ("https://models.inference.ai.azure.com", "github"), ("https://models.github.ai/inference", "github"),
    ("https://integrate.api.nvidia.com/v1", "nvidia"), ("https://api.cerebras.ai/v1", "cerebras"),
    ("https://api.sambanova.ai/v1", "sambanova"), ("http://localhost:11434/v1", "ollama"),
    ("http://127.0.0.1:1234/v1", "lmstudio"), ("http://localhost:8000/v1", "generic"),
    ("https://litellm.corp.example.com", "generic"),
    ("https://x.openai.azure.com", "azure-v1"), ("https://x.cognitiveservices.azure.com", "azure-v1"),
    ("https://x.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-10-21", "azure-classic"),
    ("https://x.openai.azure.com/openai/v1/", "azure-v1"), ("https://x.services.ai.azure.com", "azure-v1"),
    ("https://x.services.ai.azure.com/models", "azure-foundry"),
    ("https://x.services.ai.azure.com/anthropic", "azure-anthropic"),
])
def test_detect_kind_from_address(url, kind):
    assert EP.detect(url) == kind


def test_github_models_is_not_mistaken_for_azure():
    # models.inference.ai.azure.com ends in azure.com but is GitHub Models: a GitHub token, Bearer, no api-key header
    p = EP.parse_pasted("https://models.inference.ai.azure.com")
    assert p.kind == "github" and not EP.is_azure(p.kind)
    c = EP.build("github", p.base, "ghp_x", {"model": "gpt-4o", "messages": []})
    assert c.headers["Authorization"] == "Bearer ghp_x" and "api-key" not in c.headers


def test_pasted_full_azure_call_address_is_split():
    p = EP.parse_pasted("https://x.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-10-21")
    assert p == EP.Parsed("https://x.openai.azure.com", "azure-classic", "gpt-4o", "2024-10-21")
    p = EP.parse_pasted("https://x.openai.azure.com/openai/deployments/my%20dep/chat/completions")
    assert p.deployment == "my dep" and p.api_version == ""
    p = EP.parse_pasted("https://x.openai.azure.com/openai/v1/chat/completions")
    assert p.kind == "azure-v1" and p.base == "https://x.openai.azure.com/openai/v1"
    p = EP.parse_pasted("https://x.services.ai.azure.com/models/chat/completions?api-version=2024-05-01-preview")
    assert p == EP.Parsed("https://x.services.ai.azure.com/models", "azure-foundry", "", "2024-05-01-preview")
    p = EP.parse_pasted("https://x.services.ai.azure.com/anthropic/v1/messages")
    assert p.kind == "azure-anthropic" and p.base == "https://x.services.ai.azure.com/anthropic/v1"
    # a Foundry project address keeps the resource's OpenAI route
    p = EP.parse_pasted("https://x.services.ai.azure.com/api/projects/p1/openai/v1/responses")
    assert p.kind == "azure-v1" and p.base == "https://x.services.ai.azure.com/api/projects/p1/openai/v1"
    # the user picked the kind and pasted only the resource
    assert EP.parse_pasted("https://x.openai.azure.com", "azure-v1").base == "https://x.openai.azure.com/openai/v1"
    assert EP.parse_pasted("https://x.services.ai.azure.com", "azure-anthropic").base.endswith("/anthropic/v1")
    assert PS.normalise_base("https://x.openai.azure.com/openai/deployments/d/chat/completions?api-version=1") \
        == "https://x.openai.azure.com"


def test_known_providers_get_their_default_path():
    n = PS.normalise_base
    assert n("https://api.groq.com") == "https://api.groq.com/openai/v1"
    assert n("openrouter.ai") == "https://openrouter.ai/api/v1"
    assert n("https://generativelanguage.googleapis.com") == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert n("https://api.fireworks.ai/inference/v1/chat/completions") == "https://api.fireworks.ai/inference/v1"
    assert n("https://api.perplexity.ai/") == "https://api.perplexity.ai"
    assert n("http://localhost:11434") == "http://localhost:11434"


# ---------- pure: URL, headers, fields ----------------------------------------------------------------------------

def test_build_azure_classic_url_and_headers():
    c = EP.build("azure-classic", "https://x.openai.azure.com", "K", {"model": "my dep", "messages": [], "max_tokens": 5})
    assert c.url == "https://x.openai.azure.com/openai/deployments/my%20dep/chat/completions?api-version=2024-10-21"
    assert c.headers["api-key"] == "K" and "Authorization" not in c.headers and not c.anthropic
    c = EP.build("azure-classic", "https://x.openai.azure.com", "K", {"model": "d", "messages": []}, api_version="2025-04-01-preview")
    assert c.url.endswith("?api-version=2025-04-01-preview")


def test_build_other_azure_kinds():
    c = EP.build("azure-v1", "https://x.openai.azure.com/openai/v1", "K", {"model": "d", "messages": []})
    assert c.url == "https://x.openai.azure.com/openai/v1/chat/completions" and c.headers["api-key"] == "K"
    c = EP.build("azure-foundry", "https://x.services.ai.azure.com/models", "K", {"model": "d", "messages": []})
    assert c.url == "https://x.services.ai.azure.com/models/chat/completions?api-version=2024-05-01-preview"
    c = EP.build("azure-anthropic", "https://x.services.ai.azure.com/anthropic/v1", "K",
                 {"model": "d", "max_tokens": 9, "messages": [{"role": "user", "content": "hi"}]})
    assert c.url == "https://x.services.ai.azure.com/anthropic/v1/messages" and c.anthropic
    assert c.headers["x-api-key"] == "K" and c.headers["anthropic-version"] == "2023-06-01"
    assert c.body["messages"][0]["content"][0]["text"] == "hi"


@pytest.mark.parametrize("kind", BEARER_KINDS)
def test_bearer_kinds_send_bearer_to_chat_completions(kind):
    c = EP.build(kind, "https://h.example/v1", "K", {"model": "m", "messages": []})
    assert c.url == "https://h.example/v1/chat/completions"
    assert c.headers["Authorization"] == "Bearer K" and "api-key" not in c.headers and "x-api-key" not in c.headers
    assert EP.build(kind, "https://h.example/v1", "", {"model": "m", "messages": []}).headers.get("Authorization") is None


def test_reasoning_models_get_max_completion_tokens_and_no_temperature():
    body = {"model": "o3-mini", "max_tokens": 100, "temperature": 0.2, "top_p": 1, "messages": []}
    for kind in ("openai", "azure-classic", "azure-v1", "azure-foundry", "github"):
        b = EP.adapt(kind, body)
        assert b["max_completion_tokens"] == 100 and "max_tokens" not in b and "temperature" not in b and "top_p" not in b
    assert EP.adapt("azure-classic", {**body, "model": "my-gpt5-prod"}).get("max_completion_tokens") == 100
    ok = EP.adapt("openai", {**body, "model": "gpt-4o"})
    assert ok["max_tokens"] == 100 and ok["temperature"] == 0.2          # gpt-4o is not an o-series model
    assert EP.adapt("deepseek", body)["max_tokens"] == 100                # other providers keep max_tokens
    assert EP.adapt("gemini", {"model": "gemini-2.5-flash", "max_tokens": 9})["max_tokens"] == 9
    assert not any(EP.reasoning_model(m) for m in ("gpt-4o", "gpt-4.1", "deepseek-v4.1-flash", "llama-3.1-70b", "mistral-large"))


def test_old_azure_api_version_drops_stream_options():
    b = EP.adapt("azure-classic", {"model": "d", "stream_options": {"include_usage": True}}, "2024-06-01")
    assert "stream_options" not in b
    assert "stream_options" in EP.adapt("azure-classic", {"model": "d", "stream_options": {}}, "2024-10-21")


# ---------- plain-words errors -------------------------------------------------------------------------------------

def test_errors_are_plain_words_not_json():
    body = json.dumps({"error": {"code": "DeploymentNotFound", "message": "The API deployment for this resource does not exist."}})
    m = EP.explain(404, body, kind="azure-classic", model="gpt4", host="x.openai.azure.com")
    assert "no deployment named 'gpt4'" in m and "deployment name" in m and "{" not in m
    m = EP.explain(401, '{"error":{"message":"Access denied due to invalid subscription key"}}', kind="azure-classic", key="abc123")
    assert "Key 1 or Key 2" in m and "{" not in m and "abc123" not in m
    m = EP.explain(400, '{"error":{"code":"BadRequest","message":"Unsupported API version 2020-01-01"}}', kind="azure-classic",
                   api_version="2020-01-01")
    assert "api-version 2020-01-01" in m and "2024-10-21" in m
    m = EP.explain(429, '{"error":{"message":"Rate limit"}}', kind="azure-classic")
    assert "tokens-per-minute" in m
    m = EP.explain(403, "<html>Forbidden</html>", kind="azure-classic")
    assert "network rules" in m and "<html" not in m
    m = EP.explain(401, "{}", kind="openai", host="api.openai.com", key="gsk_abcdef")
    assert "Groq key" in m and "gsk_abcdef" not in m                   # a key for another host is the commonest 401
    m = EP.explain(404, '{"error":{"message":"model not found"}}', kind="groq", host="api.groq.com")
    assert "404" in m and "/v1" in m
    assert "problem on its side" in EP.explain(503, "upstream", kind="generic", host="h")
    assert "sk-secret-key-1" not in EP.explain(400, '{"error":{"message":"bad key sk-secret-key-1"}}', kind="generic", key="sk-secret-key-1")


# ---------- the test button, per provider --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [k for k in PREFIX if not EP.caps(k)["deployment"] and EP.caps(k)["lists"]
                                  and EP.caps(k)["wire"] == "openai"])
def test_connection_with_a_model_list(home, mk, kind):
    p = mk(kind)
    r = PS.test(p.base, KEY, kind=kind)
    assert r["ok"] and r["kind"] == kind and [m["id"] for m in r["models"]] == ["model-a", "model-b"], r
    assert all(h["authorization"] == f"Bearer {KEY}" for m, path, h, b in p.log)
    bad = PS.test(p.base, "sk-wrong-key-7654321", kind=kind)
    assert not bad["ok"] and "rejected (401)" in bad["error"] and "{" not in bad["error"]


def test_anthropic_key_wire(home, mk):
    p = mk("anthropic")
    # the anthropic mock lists models like Anthropic's own API
    r = PS.test(p.base, KEY, kind="anthropic", wire="anthropic")
    assert not r["ok"] or r["wire"] == "anthropic"      # /models is not served by this mock: a 404 is not "bad key"
    assert all("authorization" not in h for m, path, h, b in p.log)
    assert any(h.get("x-api-key") == KEY for m, path, h, b in p.log)


@pytest.mark.parametrize("kind", ["azure-classic", "azure-v1", "azure-foundry", "azure-anthropic"])
def test_azure_connection_is_a_one_token_chat_call_never_models(home, mk, kind):
    p = mk(kind)
    r = PS.test(p.base, KEY, kind=kind)                                   # no deployment typed yet
    assert not r["ok"] and r["needs_model"] and "deployment name" in r["error"]
    assert "rejected" not in r["error"] and p.log == []                    # nothing was sent: the key is not called bad
    ver = EP.AZURE_DEFAULT_VERSION if kind == "azure-classic" else ""      # the form carries the version box's value
    r = PS.test(p.base, KEY, kind=kind, model="my-gpt4o", api_version=ver)
    assert r["ok"] and r["models"] == [{"id": "my-gpt4o"}] and r["kind"] == kind, r
    assert all(m == "POST" for m, path, h, b in p.log) and not any(path.endswith("/models") for m, path, h, b in p.log)
    bad = PS.test(p.base, "sk-wrong-key-7654321", kind=kind, model="my-gpt4o", api_version=ver)
    assert not bad["ok"] and "rejected (401)" in bad["error"] and "{" not in bad["error"]


def test_a_bare_azure_address_uses_the_v1_route_and_falls_back_to_the_deployment_route(home, mk, monkeypatch):
    """Microsoft's current route needs no api-version and serves every model. A resource that has no such route
    answers 404 there: the same address is then tried on the deployment route, and Save keeps the one that worked."""
    assert EP.parse_pasted("https://res.openai.azure.com").kind == "azure-v1"
    assert EP.parse_pasted("https://res.openai.azure.com").base == "https://res.openai.azure.com/openai/v1"
    assert EP.parse_pasted("https://res.openai.azure.com", "azure-classic", "2025-04-01-preview").kind == "azure-classic"
    p = mk("azure-classic")                                              # a resource that only has the deployment route
    monkeypatch.setattr(EP, "_azure_family", lambda host: "classic")     # the mock listens on 127.0.0.1, not *.azure.com
    r = PS.test(p.base, KEY, model="my-gpt4o")
    assert r["ok"] and r["kind"] == "azure-classic" and r["api_version"] == EP.AZURE_DEFAULT_VERSION, r
    cur = PS.save(p.base, KEY, "auto", "my-gpt4o")
    assert C.read()["MH_API_KIND"] == "azure-classic" and C.read()["MH_API_VERSION"] == EP.AZURE_DEFAULT_VERSION
    assert cur["base_url"] == p.origin


def test_azure_wrong_deployment_and_version_are_explained(home, mk):
    p = mk("azure-classic")
    r = PS.test(p.base, KEY, kind="azure-classic", model="gpt-4o-typo")
    assert not r["ok"] and "no deployment named 'gpt-4o-typo'" in r["error"] and "not the model name" in r["error"]
    r = PS.test(p.base, KEY, kind="azure-classic", model="my-gpt4o", api_version="2020-01-01")
    assert not r["ok"] and "api-version 2020-01-01" in r["error"]
    r = PS.test(p.base, KEY, kind="azure-classic", model="my-gpt4o", api_version="2025-04-01-preview")
    assert r["ok"] and r["api_version"] == "2025-04-01-preview"


def test_pasted_azure_call_address_works_in_the_test_button(home, mk):
    p = mk("azure-classic")
    r = PS.test(p.origin + "/openai/deployments/my-gpt4o/chat/completions?api-version=2024-06-01", KEY, kind="azure-classic")
    assert r["ok"] and r["base_url"] == p.origin and r["model"] == "my-gpt4o" and r["api_version"] == "2024-06-01"
    assert p.log[-1][1].endswith("api-version=2024-06-01")


def test_wrong_auth_style_is_refused_by_the_mock(home, mk):
    # a regression that sends Azure a Bearer key, or a bearer provider an api-key header, fails here
    az = mk("azure-classic")
    r = EP.probe(az.base, KEY, "my-gpt4o", kind="generic")
    assert not r["ok"]
    bearer = mk("openai")
    r = EP.probe(bearer.base, KEY, "model-a", kind="azure-v1")
    assert not r["ok"]
    assert EP.probe(bearer.base, KEY, "model-a", kind="openai")["ok"]


def test_no_model_list_provider_asks_for_a_model(home, mk):
    p = mk("perplexity")
    r = PS.test(p.base, KEY, kind="perplexity")
    assert not r["ok"] and r["needs_model"] and "model name" in r["error"]
    assert PS.test(p.base, KEY, kind="perplexity", model="sonar")["ok"]


def test_endpoint_that_hides_its_models_is_checked_by_chat_when_a_model_is_typed(home, mk):
    p = mk("generic")
    p.srv.RequestHandlerClass.do_GET = lambda self: (self.send_response(404), self.send_header("Content-Length", "0"), self.end_headers())
    r = PS.test(p.base, KEY, model="m")
    assert r["ok"] and r["models"] == [{"id": "m"}]
    assert not PS.test(p.base, "sk-wrong-key-7654321", model="m")["ok"]


def test_keyless_local_servers(home, mk):
    p = mk("ollama")
    p.key = ""
    r = PS.test(p.base, "", kind="ollama")
    assert r["ok"] and "Enter the API key" not in r["error"]               # no key asked for, none sent
    assert all("authorization" not in h for m, path, h, b in p.log)
    r = PS.test(p.base, "anything", kind="ollama")                          # any key is accepted by such a server
    assert r["ok"] is False or r["ok"] is True


# ---------- chat: streaming, tool calls, the agent-facing builder ---------------------------------------------------

ALL_CHAT = [k for k in PREFIX if EP.caps(k)["wire"] == "openai"]


@pytest.mark.parametrize("kind", ALL_CHAT)
def test_ledger_chat_streams_and_reassembles(home, mk, kind):
    p = mk(kind)
    use(p)
    r = SimpleLedger("t").chat(model=p.model, messages=[{"role": "user", "content": "ping please"}], max_tokens=20, retries=0)
    assert r["choices"][0]["message"]["content"].startswith(f"pong from {kind}"), r
    assert r["usage"]["completion_tokens"] == 2                           # the usage chunk after the last delta
    posts = [b for m, path, h, b in p.log if m == "POST"]
    assert posts[-1]["stream"] is True


@pytest.mark.parametrize("kind", ["azure-classic", "azure-v1", "azure-foundry", "gemini", "openrouter", "groq", "generic"])
def test_ledger_chat_tool_call_round_trip(home, mk, kind, monkeypatch):
    for stream in ("1", "0"):
        monkeypatch.setenv("MH_STREAM", stream)
        p = mk(kind)
        use(p)
        tools = [{"type": "function", "function": {"name": "lookup", "description": "d",
                                                  "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}}]
        msgs = [{"role": "user", "content": "find x"}]
        r = SimpleLedger("t").chat(model=p.model, messages=msgs, tools=None, extra={"tools": tools}, retries=0)
        call = r["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "lookup" and json.loads(call["function"]["arguments"]) == {"x": 1}
        msgs += [r["choices"][0]["message"], {"role": "tool", "tool_call_id": call["id"], "content": "42"}]
        r2 = SimpleLedger("t").chat(model=p.model, messages=msgs, extra={"tools": tools}, retries=0)
        assert r2["choices"][0]["message"]["content"].startswith("pong from")


def test_ledger_chat_azure_anthropic_round_trip(home, mk):
    p = mk("azure-anthropic")
    use(p)
    tools = [{"type": "function", "function": {"name": "lookup", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
    r = SimpleLedger("t").chat(model=p.model, messages=[{"role": "user", "content": "go"}], extra={"tools": tools}, retries=0)
    call = r["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "lookup"
    assert any(h.get("x-api-key") == KEY for m, path, h, b in p.log if m == "POST")


def test_reasoning_deployment_works_through_the_ledger(home, mk):
    p = mk("azure-classic")
    use(p, model="o3-prod")
    r = SimpleLedger("t").chat(model="o3-prod", messages=[{"role": "user", "content": "x"}], max_tokens=50, temperature=0.3, retries=0)
    assert r["choices"][0]["message"]["content"]                         # the mock refuses max_tokens/temperature for o3
    body = [b for m, path, h, b in p.log if m == "POST"][-1]
    assert body["max_completion_tokens"] == 50 and "max_tokens" not in body and "temperature" not in body


def test_ledger_error_is_plain_words(home, mk):
    p = mk("azure-classic")
    use(p, model="nope")
    with pytest.raises(RuntimeError) as e:
        SimpleLedger("t").chat(model="nope", messages=[{"role": "user", "content": "x"}], retries=0)
    msg = str(e.value)
    assert msg.startswith("meshpool HTTP 404:") and "no deployment named 'nope'" in msg and '{"error"' not in msg
    use(p, key="sk-wrong-key-7654321")
    with pytest.raises(RuntimeError) as e:
        SimpleLedger("t").chat(model="my-gpt4o", messages=[{"role": "user", "content": "x"}], retries=0)
    assert "HTTP 401" in str(e.value) and "Key 1 or Key 2" in str(e.value)


def test_agent_builder_uses_the_saved_kind_only_for_the_saved_address(home, mk):
    p = mk("azure-classic")
    use(p, version="2025-04-01-preview")
    url, body, h = EP.chat_request(p.base, KEY, {"model": "d", "messages": []})
    assert url == p.origin + "/openai/deployments/d/chat/completions?api-version=2025-04-01-preview" and h["api-key"] == KEY
    # any other address (the ChatGPT relay, a typed test) is read from its own text: plain Bearer, no Azure query
    url, body, h = EP.chat_request("http://127.0.0.1:9/v1", "other-key", {"model": "d", "messages": []})
    assert url == "http://127.0.0.1:9/v1/chat/completions" and h == {"Content-Type": "application/json", "Authorization": "Bearer other-key"}


def test_agent_post_and_probe_go_through_the_builder(home, mk, tmp_path):
    from meshpool_harness.agent import core as AC
    p = mk("azure-classic")
    use(p)
    assert AC._probe_once(p.base, KEY, "my-gpt4o") == "ok"
    assert AC._probe_once(p.base, "sk-wrong-key-7654321", "my-gpt4o") == "auth"
    a = AC.Agent(str(tmp_path), mode="auto")
    data, got_text = a._post(p.base, KEY, {"model": "my-gpt4o", "max_tokens": 20,
                                           "messages": [{"role": "user", "content": "hello there"}]}, True)
    assert data["choices"][0]["message"]["content"].startswith("pong from azure-classic") and got_text
    posts = [(path, h) for m, path, h, b in p.log if m == "POST"]
    assert all("/openai/deployments/my-gpt4o/chat/completions?api-version=" in path for path, h in posts)
    assert posts[-1][1]["api-key"] == KEY and "authorization" not in posts[-1][1]


def test_ping_and_spend_work_for_azure(home, mk):
    from meshpool_harness import cli
    from meshpool_harness.ui import settings_v2 as SV
    p = mk("azure-classic")
    use(p)
    r = cli.ping("my-gpt4o")
    assert r["ok"], r
    r = cli.ping("my-gpt4o", base=p.base, key=KEY, kind="azure-classic", api_version="2024-06-01")
    assert r["ok"]
    r = cli.ping("nope", base=p.base, key=KEY, kind="azure-classic")
    assert not r["ok"] and "no deployment named 'nope'" in r["detail"]
    SV._SPEND.update(t=0.0, v=None)
    assert SV.key_spend(force=True)["available"] is False                # no litellm headers, and no crash


def test_model_picker_does_not_call_models_on_azure(home, mk):
    from meshpool_harness.ui import workbench as W
    p = mk("azure-classic")
    use(p)
    W._MODELS.update(t=0, key="", rows=[], error="")
    out = W.models(refresh=True)
    assert p.log == []                                                    # no GET /models: it would not list deployments
    assert out["default"] == "my-gpt4o" and any(m["id"] == "my-gpt4o" for m in out["models"])
    g = next(g for g in out["groups"] if g["id"] == "key")
    assert g["error"] == ""
    q = mk("openrouter")
    use(q)
    W._MODELS.update(t=0, key="", rows=[], error="")
    assert {m["id"] for m in W.models(refresh=True)["models"]} >= {"model-a", "model-b"}


# ---------- keys never go to another host ---------------------------------------------------------------------------

def test_a_redirect_never_carries_the_key_on(home, mk):
    thief = mk("generic")
    victim = mk("generic")

    def redirect(self):
        self.send_response(307)
        self.send_header("Location", thief.origin + "/v1/chat/completions")
        self.send_header("Content-Length", "0")
        self.end_headers()
    victim.srv.RequestHandlerClass.do_POST = redirect
    use(victim)
    with pytest.raises(RuntimeError):
        SimpleLedger("t").chat(model="m", messages=[{"role": "user", "content": "x"}], retries=0)
    assert thief.log == []
    r = PS.test(victim.base, KEY)                                        # the model-list path never follows either
    assert thief.log == []


def test_env_key_is_only_offered_for_its_own_host(home, mk, monkeypatch):
    other = mk("generic")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-envkey-9876543")
    row = next(r for r in PS.env_keys() if r["env"] == "OPENROUTER_API_KEY")
    assert row["found"] and row["base_url"] == "https://openrouter.ai/api/v1" and row["provider"] == "openrouter"
    with pytest.raises(PS.SetupError, match="not sent anywhere else"):
        PS.test(other.base, env="OPENROUTER_API_KEY")
    with pytest.raises(PS.SetupError, match="not sent anywhere else"):
        PS.save(other.base, None, "auto", "m", env="OPENROUTER_API_KEY", confirm_env=True)
    assert other.log == [] and "MESHPOOL_API_KEY" not in C.read()


def test_saved_key_is_not_sent_to_a_new_host(home, mk):
    a, b = mk("generic"), mk("generic")
    use(a)
    r = PS.test(b.base)                                                  # no key typed for b: the saved one stays home
    assert all("authorization" not in h and "api-key" not in h for m, path, h, body in b.log)   # a's key never reached b


# ---------- saving, and what follows the user ----------------------------------------------------------------------

def test_save_azure_then_move_away_clears_azure_settings(home, mk):
    az = mk("azure-classic")
    PS.test(az.base, KEY, kind="azure-classic", model="my-gpt4o")
    cur = PS.save(az.base, KEY, "auto", "my-gpt4o", api_version="2025-04-01-preview", kind="azure-classic")
    assert cur["kind"] == "azure-classic" and cur["api_version"] == "2025-04-01-preview" and cur["deployment"] is True
    assert C.read()["MH_API_KIND"] == "azure-classic" and C.read()["MH_API_VERSION"] == "2025-04-01-preview"
    assert PV.key_kind() == "azure-classic" and PV.key_wire() == "openai"
    other = mk("openrouter")
    PS.test(other.base, "sk-or-new-key-123456", kind="openrouter")
    cur = PS.save(other.base, "sk-or-new-key-123456", "auto", "model-a", kind="openrouter")
    assert cur["kind"] == "openrouter" and cur["api_version"] == ""
    assert C.read().get("MH_API_VERSION", "") == "" and PV.key_api_version() == ""


def test_save_azure_anthropic_sets_the_messages_wire(home, mk):
    p = mk("azure-anthropic")
    PS.test(p.base, KEY, kind="azure-anthropic", model="my-gpt4o")
    PS.save(p.base, KEY, "auto", "my-gpt4o", kind="azure-anthropic")
    assert PV.key_wire() == "anthropic" and PV.key_kind() == "azure-anthropic"
    assert PV.key_base().endswith("/anthropic/v1")


def test_save_takes_a_pasted_full_azure_address(home, mk):
    p = mk("azure-classic")
    full = p.origin + "/openai/deployments/my-gpt4o/chat/completions?api-version=2025-04-01-preview"
    PS.test(full, KEY, kind="azure-classic")
    cur = PS.save(full, KEY, "auto", "", kind="azure-classic")           # the deployment came from the address
    assert cur["default_model"] == "my-gpt4o" and cur["api_version"] == "2025-04-01-preview" and cur["base_url"] == p.origin


# ---------- environment auto-detection ------------------------------------------------------------------------------

def test_azure_environment_pair_is_used_with_its_endpoint_and_version(home, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key-123456789")
    assert PV.key() == "" and PV.key_base() == "https://llm.meshpool.ai/v1"      # a key with no endpoint is never used
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res1.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "my-gpt4o")
    assert PV.key() == "az-key-123456789" and PV.key_base() == "https://res1.openai.azure.com"
    assert PV.key_kind() == "azure-classic" and PV.key_api_version() == "2025-04-01-preview"
    assert PV.split("")[1] == "my-gpt4o" and PV.split(PV.DEFAULT_MODEL)[1] == "my-gpt4o"
    assert PV.split("my-other")[1] == "my-other"
    url, body, h = EP.chat_request(PV.key_base(), PV.key(), {"model": "my-gpt4o", "messages": []})
    assert url == "https://res1.openai.azure.com/openai/deployments/my-gpt4o/chat/completions?api-version=2025-04-01-preview"
    assert h["api-key"] == "az-key-123456789"


def test_azure_environment_endpoint_may_be_a_v1_or_full_address(home, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key-123456789")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res1.openai.azure.com/openai/v1/")
    assert PV.key_kind() == "azure-v1" and PV.key_base() == "https://res1.openai.azure.com/openai/v1"
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res1.services.ai.azure.com/anthropic")
    assert PV.key_kind() == "azure-anthropic" and PV.key_wire() == "anthropic"


def test_environment_order_and_meshpool_key_wins(home, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key-123456789")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res1.openai.azure.com")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-123")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    assert PV.key() == "sk-openai-123" and PV.key_kind() == "openai"      # the OpenAI pair comes first
    monkeypatch.delenv("OPENAI_BASE_URL")
    assert PV.key() == "az-key-123456789"                                 # an OpenAI key with no base is skipped
    monkeypatch.setenv("MESHPOOL_API_KEY", "mk-123456789")
    assert PV.key() == "mk-123456789" and PV.key_base() == "https://llm.meshpool.ai/v1" and PV.key_kind() == "generic"


def test_a_saved_kind_is_ignored_when_the_address_comes_from_the_environment(home, monkeypatch):
    C.write({"MH_API_KIND": "openrouter", "MH_API_VERSION": "1999-01-01"})
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key-123456789")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res1.openai.azure.com")
    assert PV.key_kind() == "azure-v1" and PV.key_api_version() == ""      # no api-version named: the v1 route


@pytest.mark.parametrize("var,kind,host", [
    ("OPENROUTER_API_KEY", "openrouter", "openrouter.ai"), ("GROQ_API_KEY", "groq", "api.groq.com"),
    ("GEMINI_API_KEY", "gemini", "generativelanguage.googleapis.com"),
    ("GOOGLE_API_KEY", "gemini", "generativelanguage.googleapis.com"), ("MISTRAL_API_KEY", "mistral", "api.mistral.ai"),
    ("XAI_API_KEY", "xai", "api.x.ai"), ("TOGETHER_API_KEY", "together", "api.together.xyz"),
    ("FIREWORKS_API_KEY", "fireworks", "api.fireworks.ai"), ("DEEPSEEK_API_KEY", "deepseek", "api.deepseek.com"),
    ("PERPLEXITY_API_KEY", "perplexity", "api.perplexity.ai"), ("CEREBRAS_API_KEY", "cerebras", "api.cerebras.ai"),
    ("SAMBANOVA_API_KEY", "sambanova", "api.sambanova.ai"), ("NVIDIA_API_KEY", "nvidia", "integrate.api.nvidia.com"),
    ("ANTHROPIC_API_KEY", "anthropic", "api.anthropic.com"),
])
def test_other_provider_keys_are_found_each_with_its_own_host(home, monkeypatch, var, kind, host):
    monkeypatch.setenv(var, "env-key-0123456789")
    row = next(r for r in PS.env_keys() if r["env"] == var)
    assert row["found"] and row["provider"] == kind and PV.host(row["base_url"]) == host
    assert PV.key() == ""                                                 # offered, never silently used
    assert EP.detect(row["base_url"]) == kind


def test_anthropic_key_with_its_own_base(home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-envkey-9876")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com/v1")
    row = next(r for r in PS.env_keys() if r["env"] == "ANTHROPIC_API_KEY")
    assert row["base_url"] == "https://proxy.example.com/v1" and row["wire"] == "anthropic"


# ---------- agents Meshpool launches ---------------------------------------------------------------------------------

def _relay_call(url, token, body, stream=False):
    import urllib.request
    req = urllib.request.Request(url + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode()


def test_relay_lets_a_bearer_only_agent_use_azure_classic(home, mk):
    import urllib.error
    import urllib.request
    p = mk("azure-classic")
    use(p)
    base, tok = PV.agent_endpoint("key")
    assert base.startswith("http://127.0.0.1:") and tok != KEY             # the agent never sees the real key
    st, out = _relay_call(base, tok, {"model": "my-gpt4o", "messages": [{"role": "user", "content": "hi there"}], "max_tokens": 9})
    assert st == 200 and "pong from azure-classic" in out
    st, out = _relay_call(base, tok, {"model": "my-gpt4o", "stream": True, "stream_options": {"include_usage": True},
                                      "messages": [{"role": "user", "content": "hi there"}]})
    assert "data: [DONE]" in out and "pong" in out
    last = [x for x in p.log if x[0] == "POST"][-1]
    assert last[2]["api-key"] == KEY and "authorization" not in last[2] and "/openai/deployments/my-gpt4o/" in last[1]
    with pytest.raises(urllib.error.HTTPError) as e:                       # a wrong relay token is refused
        _relay_call(base, "wrong", {"model": "m", "messages": []})
    assert e.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as e:                       # the upstream error reaches the agent in words
        _relay_call(base, tok, {"model": "nope", "messages": [{"role": "user", "content": "x"}]})
    assert e.value.code == 404 and "no deployment named" in e.value.read().decode()
    req = urllib.request.Request(base + "/models", headers={"Authorization": f"Bearer {tok}"})
    assert json.loads(urllib.request.urlopen(req, timeout=5).read())["data"][0]["id"] == "my-gpt4o"


def test_agents_get_the_real_endpoint_when_it_is_plain_bearer(home, mk):
    p = mk("openrouter")
    use(p)
    assert PV.agent_endpoint("key") == (p.base, KEY)
    v1 = mk("azure-v1")
    use(v1)
    assert PV.agent_endpoint("key") == (v1.base, KEY)                        # Azure v1 accepts a Bearer key
    c = mk("azure-classic")
    use(c)
    assert PV.agent_endpoint("key")[0].startswith("http://127.0.0.1:") and PV.agent_endpoint("key")[0] != c.base


def test_harness_configs_for_azure(home, mk):
    import yaml
    from meshpool_harness import harnesses as HR
    from meshpool_harness.harnesses import pi_rpc
    p = mk("azure-classic")
    use(p, version="2025-04-01-preview")
    HR.prepare_opencode("my-gpt4o")
    cfg = json.loads((HR.opencode_home() / "opencode.json").read_text())
    base = cfg["provider"]["meshpool"]["options"]["baseURL"]
    assert base.startswith("http://127.0.0.1:") and base != p.base and KEY not in json.dumps(cfg)
    HR.prepare_hermes("my-gpt4o")
    h = yaml.safe_load((HR.hermes_home() / "config.yaml").read_text())
    assert h["model"]["base_url"].startswith("http://127.0.0.1:") and h["model"]["api_key"] != KEY
    HR.prepare_codex("my-gpt4o")
    toml = (HR.codex_home() / "config.toml").read_text()
    assert f'base_url = "{p.origin}/openai"' in toml and 'query_params = { api-version = "2025-04-01-preview" }' in toml
    pi_rpc.prepare(home)
    models = json.loads((pi_rpc.pi_home() / "models.json").read_text())
    assert models["providers"]["meshpool"]["baseUrl"].startswith("http://127.0.0.1:")
    assert KEY not in json.dumps(models)


def test_codex_is_told_plainly_when_the_azure_address_cannot_serve_it(home, mk):
    p = mk("azure-foundry")
    use(p)
    assert "Responses API" in PV.direct_agent_ok("codex") and PV.direct_agent_ok("hermes") == ""
    assert "codex" in PS.current()["agent_notes"]
    q = mk("openrouter")
    use(q)
    assert PV.direct_agent_ok("codex") == "" and PS.current()["agent_notes"] == {}


def test_catalog_lists_every_provider_with_what_to_paste():
    ids = {c["id"] for c in PS.catalog()}
    assert ids >= {"openai", "anthropic", "openrouter", "groq", "together", "fireworks", "mistral", "xai", "deepseek",
                   "perplexity", "gemini", "bedrock", "github", "nvidia", "cerebras", "sambanova", "ollama", "lmstudio",
                   "generic", "azure-classic", "azure-v1", "azure-foundry", "azure-anthropic"}
    assert all(c["label"] and c["key_hint"] and c["base"] for c in PS.catalog())
    az = next(c for c in PS.catalog() if c["id"] == "azure-classic")
    assert az["deployment"] and az["version"] == "2024-10-21" and "Keys and Endpoint" in az["key_hint"]


def test_the_agent_loop_never_follows_a_redirect_with_the_key(home):
    """core._urlopen (the agent's own call) used to follow a redirect and send the key header to the new host."""
    import http.server
    import threading
    from meshpool_harness.agent import core as CORE
    seen = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                       # urllib follows a 302 on a POST as a GET, header included
            seen.append(self.headers.get("api-key") or self.headers.get("authorization"))
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    tgt = http.server.HTTPServer(("127.0.0.1", 0), Target)

    class Hop(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{tgt.server_port}/steal")
            self.end_headers()

        def log_message(self, *a):
            pass

    hop = http.server.HTTPServer(("127.0.0.1", 0), Hop)
    for s in (tgt, hop):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{hop.server_port}/x", data=b"{}", method="POST",
                                     headers={"api-key": "secret-key-123456"})
        with pytest.raises(urllib.error.HTTPError) as e:
            CORE._urlopen(req, 5, lambda c: None)
        assert e.value.code == 302 and seen == [], seen
    finally:
        for s in (tgt, hop):
            s.shutdown()
