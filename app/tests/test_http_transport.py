"""The one transport for keyed model calls (core/http.py): a redirect never carries the key to another host, a loopback
model server is never reached through a proxy, and HTTPS trust works without a system CA bundle."""
import http.server
import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

from meshpool_harness import config as C
from meshpool_harness.agent.core import Agent
from meshpool_harness.core import http as H
from meshpool_harness.core.wire import SimpleLedger

KEY = "sk-secret-key-1234567890"
KEY_HEADERS = ("authorization", "x-api-key", "api-key")
OK_CHAT = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
MSGS = [{"role": "user", "content": "hi"}]


class Srv:
    """A local HTTP(S) server on a thread. `responder(handler, rec)` writes the reply; every request is recorded."""

    def __init__(self, responder, tls=None):
        self.seen = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                rec = {"method": self.command, "path": self.path, "body": self.rfile.read(n) if n else b"",
                       "headers": {k.lower(): v for k, v in self.headers.items()}}
                outer.seen.append(rec)
                responder(self, rec)

            do_GET = do_POST = do_HEAD = do_PUT = _do

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        self.port = self.srv.server_address[1]
        self.scheme = "https" if tls else "http"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):                                  # one name per host: 127.0.0.1 here, localhost for "another host"
        return f"{self.scheme}://127.0.0.1:{self.port}"

    @property
    def other(self):
        return f"{self.scheme}://localhost:{self.port}"

    def keyed(self):
        return [r for r in self.seen if any(h in r["headers"] for h in KEY_HEADERS)]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def send(h, code, obj=None, location=None, raw=None):
    body = raw if raw is not None else json.dumps(obj if obj is not None else {}).encode()
    h.send_response(code)
    if location:
        h.send_header("Location", location)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(body)


@pytest.fixture
def servers():
    made = []

    def mk(responder, tls=None):
        s = Srv(responder, tls)
        made.append(s)
        return s
    yield mk
    for s in made:
        s.close()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    for k in ("MESHPOOL_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "MH_PROVIDER", "MH_API_WIRE",
              "MESHPOOL_CA_BUNDLE", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy",
              "https_proxy", "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(k, "x")                  # recorded, then removed: the test's own value is undone afterwards
        monkeypatch.delenv(k)
    monkeypatch.setattr(ssl, "_create_default_https_context", ssl._create_default_https_context)
    monkeypatch.setenv("MH_STREAM", "0")
    monkeypatch.setenv("MH_MODEL", "key/deepseek-v4.1-flash")
    H.reset()
    yield
    H.reset()


def use_endpoint(monkeypatch, base, wire=None):
    monkeypatch.setenv("MESHPOOL_BASE_URL", base)
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    if wire:
        monkeypatch.setenv("MH_API_WIRE", wire)


# ---------------------------------------------------------------------------------------------- call sites
def _call_chat(tmp_path, base):
    try:
        SimpleLedger("t").chat(model="key/m", messages=MSGS, max_tokens=5, retries=0)
    except RuntimeError as e:
        return str(e)
    return ""


def _call_agent(stream):
    def go(tmp_path, base):
        a = Agent(str(tmp_path), mode="auto")
        try:
            a._post(base, KEY, {"model": "m", "messages": MSGS}, stream)
        except RuntimeError as e:
            return str(e)
        return ""
    return go


def _call_anthropic(tmp_path, base):
    try:
        SimpleLedger("t").chat(model="key/m", messages=MSGS, max_tokens=5, retries=0)
    except RuntimeError as e:
        return str(e)
    return ""


def _call_models(tmp_path, base):
    from meshpool_harness.ui import workbench
    return json.dumps(workbench.models(refresh=True))


def _call_spend(tmp_path, base):
    from meshpool_harness.ui import settings_v2
    settings_v2._SPEND.update(t=0.0, v=None)
    return json.dumps(settings_v2.key_spend(force=True))


SITES = {"chat": (_call_chat, None, True), "agent_stream": (_call_agent(True), None, True),
         "agent_plain": (_call_agent(False), None, True), "anthropic_wire": (_call_anthropic, "anthropic", True),
         "models": (_call_models, None, True), "key_spend": (_call_spend, None, False)}


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("site", list(SITES))
def test_cross_host_redirect_never_carries_key(servers, tmp_path, monkeypatch, site, code):
    call, wire, reports = SITES[site]
    target = servers(lambda h, rec: send(h, 200, OK_CHAT))
    origin = servers(lambda h, rec: send(h, code, location=target.other + rec["path"]))
    use_endpoint(monkeypatch, origin.url + "/v1", wire)
    text = call(tmp_path, origin.url + "/v1")
    assert target.seen == [], "the redirect was followed to another host"
    assert not target.keyed()
    assert len(origin.keyed()) == 1                  # the key went to the host the user gave, once
    if reports:
        assert "localhost" in text, text
        assert KEY not in text


def test_https_to_http_downgrade_refused():
    h = H._KeyedRedirect()
    req = urllib.request.Request("https://api.example.com/v1/chat/completions", data=b"{}", method="POST",
                                 headers={"Authorization": "Bearer k"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        h.redirect_request(req, None, 302, "Found", {}, "http://api.example.com/v1/chat/completions")
    assert ei.value.code == 302 and "api.example.com" in ei.value.read().decode()
    # an upgrade on the same host is fine, and so is a same-host https path; another port or host is not
    up = h.redirect_request(req.__class__("http://api.example.com/v1/x", data=b"{}", method="POST",
                                          headers={"Authorization": "Bearer k"}), None, 308, "x", {},
                            "https://api.example.com/v1/x")
    assert up.full_url == "https://api.example.com/v1/x" and up.get_method() == "POST" and up.data == b"{}"
    assert up.get_header("Authorization") == "Bearer k"
    same = h.redirect_request(req, None, 307, "x", {}, "/v1/other")
    assert same.full_url == "https://api.example.com/v1/other"
    for bad in ("https://api.example.com:8443/v1/x", "https://Api.Example.org/v1/x", "ftp://api.example.com/x"):
        with pytest.raises(urllib.error.HTTPError):
            h.redirect_request(req, None, 301, "x", {}, bad)
    assert H._same_host_target("http://API.example.com/a", "https://api.example.com/a")
    assert not H._same_host_target("http://api.example.com:8080/a", "https://api.example.com/a")
    assert H._same_host_target("http://api.example.com:8080/a", "https://api.example.com:8080/a")


@pytest.mark.parametrize("code", [307, 308])
def test_same_host_redirect_is_refused_and_says_what_to_set(servers, monkeypatch, tmp_path, code):
    """0.0.14: a request with a key follows no redirect at all, not even on the same host. The error names the host,
    says the key was not sent there and which address to set."""
    def responder(h, rec):
        if rec["path"] == "/v1/chat/completions":
            send(h, code, location="/v1/moved/chat/completions")
        else:
            send(h, 200, OK_CHAT)
    srv = servers(responder)
    use_endpoint(monkeypatch, srv.url + "/v1")
    with pytest.raises(RuntimeError) as ei:
        SimpleLedger("t").chat(model="key/m", messages=MSGS, max_tokens=5, retries=0)
    msg = str(ei.value)
    assert "127.0.0.1" in msg and "did not send your key" in msg and srv.url + "/v1/moved" in msg, msg
    assert len(srv.seen) == 1 and KEY not in msg
    # the opt-in same-host policy still resends the POST body to the same host
    srv.seen.clear()
    req = urllib.request.Request(srv.url + "/v1/chat/completions", data=b'{"a":1}', method="POST",
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    with H.open(req, 5, refuse_redirects=False) as r:
        assert r.status == 200
    first, second = srv.seen
    assert second["path"] == "/v1/moved/chat/completions" and second["method"] == "POST" and second["body"] == first["body"]


def test_redirect_hops_are_limited(servers, monkeypatch):
    srv = servers(lambda h, rec: send(h, 307, location="/v1/loop"))
    use_endpoint(monkeypatch, srv.url + "/v1")
    with pytest.raises(urllib.error.HTTPError):
        H.open(urllib.request.Request(srv.url + "/v1/x", data=b"{}", method="POST"), 5)
    assert len(srv.seen) <= H._MAX_HOPS + 1


def test_refuse_redirects_never_follows(servers):
    srv = servers(lambda h, rec: send(h, 301, location="/elsewhere"))
    with pytest.raises(urllib.error.HTTPError) as ei:
        H.open(urllib.request.Request(srv.url + "/v1/models"), 5, refuse_redirects=True)
    assert ei.value.code == 301 and len(srv.seen) == 1


# ---------------------------------------------------------------------------------------------- proxy
def test_loopback_bypasses_http_proxy(servers, monkeypatch, tmp_path):
    proxy = servers(lambda h, rec: send(h, 502, {"error": "the proxy was used"}))
    model = servers(lambda h, rec: send(h, 200, OK_CHAT))
    for n in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(n, proxy.url)
    use_endpoint(monkeypatch, model.url + "/v1")
    out = SimpleLedger("t").chat(model="key/m", messages=MSGS, max_tokens=5, retries=0)
    assert out["choices"][0]["message"]["content"] == "ok"
    assert proxy.seen == []
    assert len(model.keyed()) == 1
    # the agent path (connection trackers) and the model list too
    resp, _ = Agent(str(tmp_path), mode="auto")._post(model.url + "/v1", KEY, {"model": "m", "messages": MSGS}, False)
    assert resp["choices"][0]["message"]["content"] == "ok" and proxy.seen == []


def test_other_hosts_still_use_the_environment_proxy(servers, monkeypatch):
    proxy = servers(lambda h, rec: send(h, 502, {"error": "proxy"}))
    monkeypatch.setenv("HTTP_PROXY", proxy.url)
    monkeypatch.setenv("http_proxy", proxy.url)
    with pytest.raises(urllib.error.HTTPError):
        H.open(urllib.request.Request("http://models.example.invalid/v1/models"), 5)
    assert len(proxy.seen) == 1
    for h in ("localhost", "foo.localhost", "127.0.0.1", "127.8.8.8", "::1", "[::1]", "0.0.0.0"):
        assert H._is_loopback(h), h
    assert not H._is_loopback("example.com") and not H._is_loopback("10.0.0.5")


# ---------------------------------------------------------------------------------------------- TLS trust
class FakeCtx:
    def __init__(self, cas=0):
        self.cas, self.loaded = cas, []

    def cert_store_stats(self):
        return {"x509": self.cas, "x509_ca": self.cas, "crl": 0}

    def load_verify_locations(self, cafile=None, **k):
        self.loaded.append(cafile)


def _pem(tmp_path, name="bundle.pem"):
    p = tmp_path / name
    p.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    return p


def test_tls_fallback_when_default_has_no_cas(tmp_path, monkeypatch):
    fake = FakeCtx(cas=0)
    bundle = _pem(tmp_path)
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fake)
    monkeypatch.setattr(H, "_FALLBACK_FILES", ("/nonexistent/cert.pem", str(bundle)))
    monkeypatch.setitem(sys.modules, "certifi", None)                      # not importable
    assert H.tls_context() is fake and H.tls_context() is fake             # cached
    assert fake.loaded == [str(bundle)] and H.bundle_path() == str(bundle)
    H.install_tls()
    H.install_tls()                                                        # idempotent
    assert ssl._create_default_https_context() is fake
    assert os.environ["SSL_CERT_FILE"] == str(bundle) and os.environ["NODE_EXTRA_CA_CERTS"] == str(bundle)


def test_tls_untouched_when_default_has_cas(tmp_path, monkeypatch):
    fake = FakeCtx(cas=140)
    before = ssl._create_default_https_context
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fake)
    assert H.tls_context() is fake and fake.loaded == []
    H.install_tls()
    assert ssl._create_default_https_context is before and "SSL_CERT_FILE" not in os.environ


def test_certifi_is_used_before_system_files(tmp_path, monkeypatch):
    fake, cf = FakeCtx(cas=0), _pem(tmp_path, "certifi.pem")
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fake)
    monkeypatch.setitem(sys.modules, "certifi", type(sys)("certifi"))
    sys.modules["certifi"].where = lambda: str(cf)
    monkeypatch.setattr(H, "_FALLBACK_FILES", (str(_pem(tmp_path, "sys.pem")),))
    H.tls_context()
    assert fake.loaded == [str(cf)]


def test_ca_bundle_env_wins(tmp_path, monkeypatch):
    mine, theirs = _pem(tmp_path, "mine.pem"), _pem(tmp_path, "theirs.pem")
    fake = FakeCtx(cas=140)                                                # the system already has CAs: still loaded
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fake)
    monkeypatch.setenv("MESHPOOL_CA_BUNDLE", str(mine))
    monkeypatch.setenv("SSL_CERT_FILE", str(theirs))
    assert H.tls_context() is fake and fake.loaded == [str(mine)] and H.bundle_path() == str(mine)
    H.install_tls()
    assert ssl._create_default_https_context() is fake
    assert os.environ["SSL_CERT_FILE"] == str(theirs)                      # an existing value is never replaced
    assert os.environ["NODE_EXTRA_CA_CERTS"] == str(mine)
    H.reset()                                                              # a missing file is ignored
    monkeypatch.setenv("MESHPOOL_CA_BUNDLE", str(tmp_path / "gone.pem"))
    fake2 = FakeCtx(cas=140)
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fake2)
    H.tls_context()
    assert fake2.loaded == [str(theirs)]                                   # SSL_CERT_FILE, a file that exists


def test_macos_keychain_export(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "argv.txt"
    sec = bin_ / "security"
    sec.write_text(f"#!/bin/sh\necho \"$@\" > '{log}'\n"
                   "printf -- '-----BEGIN CERTIFICATE-----\\nMIIBcorp\\n-----END CERTIFICATE-----\\n'\n")
    sec.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    fake = FakeCtx(cas=0)
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fake)
    monkeypatch.setattr(H, "_FALLBACK_FILES", ())
    monkeypatch.setitem(sys.modules, "certifi", None)
    monkeypatch.setattr(sys, "platform", "darwin")
    dest = C.home() / "cache" / "ca-bundle.pem"
    assert H.tls_context() is fake
    assert fake.loaded == [str(dest)] and "MIIBcorp" in dest.read_text()
    argv = log.read_text()
    assert "find-certificate -a -p" in argv and "System.keychain" in argv and "SystemRootCertificates.keychain" in argv
    assert not list(dest.parent.glob("*.tmp"))
    # a Python that already has its own CAs (Homebrew) still gets the keychain: a company proxy's CA lives there
    H.reset()
    fake2 = FakeCtx(cas=140)
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fake2)
    assert H.tls_context() is fake2
    assert fake2.loaded == [str(dest)] and H._STATE["needed"] is True
    # ... unless the user named their own bundle
    H.reset()
    monkeypatch.setenv("MESHPOOL_CA_BUNDLE", str(dest))
    fake4 = FakeCtx(cas=140)
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fake4)
    H.tls_context()
    assert fake4.loaded == [str(dest)]                       # only their file, loaded once
    monkeypatch.delenv("MESHPOOL_CA_BUNDLE")
    # not on another platform
    H.reset()
    dest.unlink()
    monkeypatch.setattr(sys, "platform", "linux")
    fake3 = FakeCtx(cas=0)
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: fake3)
    H.tls_context()
    assert fake3.loaded == [] and not dest.exists()


def test_certificate_error_message_is_actionable():
    cert = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
                                           "local issuer certificate (_ssl.c:1000)")
    for e in (urllib.error.URLError(cert), cert, ConnectionError("x [SSL: CERTIFICATE_VERIFY_FAILED]")):
        text = H.explain(e, "https://api.example.com/v1/chat/completions")
        assert "api.example.com" in text and "MESHPOOL_CA_BUNDLE" in text and "Install Certificates.command" in text
    assert H.explain(urllib.error.URLError(ConnectionRefusedError("refused"))) == "<urlopen error refused>"
    assert H.explain(ValueError("plain")) == "plain"


needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="the openssl CLI is not installed")


@pytest.fixture
def selfsigned(tmp_path):
    crt, key = tmp_path / "ca.pem", tmp_path / "ca.key"
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
                        "-days", "2", "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                        "-addext", "basicConstraints=critical,CA:TRUE",
                        "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
                        "-addext", "subjectKeyIdentifier=hash"], capture_output=True, text=True)
    if r.returncode:
        pytest.skip("openssl could not make a certificate: " + r.stderr[:200])
    return crt, key


@needs_openssl
def test_https_with_company_ca_end_to_end(servers, monkeypatch, tmp_path, selfsigned):
    crt, key = selfsigned
    srv = servers(lambda h, rec: send(h, 200, OK_CHAT), tls=(str(crt), str(key)))
    use_endpoint(monkeypatch, f"https://localhost:{srv.port}/v1")
    text = _call_chat(tmp_path, "")                                         # an untrusted certificate: refused, said plainly
    assert "MESHPOOL_CA_BUNDLE" in text and "localhost" in text and "HTTP" in text, text
    assert srv.seen == []
    a = Agent(str(tmp_path), mode="auto")
    with pytest.raises(RuntimeError, match=r"HTTP 0: connection problem \(.*MESHPOOL_CA_BUNDLE"):
        a._post(f"https://localhost:{srv.port}/v1", KEY, {"model": "m", "messages": MSGS}, False)
    monkeypatch.setenv("MESHPOOL_CA_BUNDLE", str(crt))
    H.reset()
    out = SimpleLedger("t").chat(model="key/m", messages=MSGS, max_tokens=5, retries=0)
    assert out["choices"][0]["message"]["content"] == "ok"
    resp, _ = a._post(f"https://localhost:{srv.port}/v1", KEY, {"model": "m", "messages": MSGS}, False)
    assert resp["choices"][0]["message"]["content"] == "ok"
    assert len(srv.keyed()) == 2


@needs_openssl
def test_load_into_env_installs_trust(monkeypatch, tmp_path, selfsigned):
    crt, _ = selfsigned
    monkeypatch.setenv("MESHPOOL_CA_BUNDLE", str(crt))
    H.reset()
    C.load_into_env()
    assert isinstance(ssl._create_default_https_context(), ssl.SSLContext)
    assert os.environ["SSL_CERT_FILE"] == str(crt) and os.environ["NODE_EXTRA_CA_CERTS"] == str(crt)
