"""HTTPS trusts the operating system's certificates (truststore): behind a company proxy whose certificate is only in
the macOS Keychain or the Windows certificate store, the web tools, model calls and updates work. It is turned on at
start-up by the app, the command line and every MCP server, and a failure falls back to Python's default trust.

conftest.py turns it off for the other tests (it replaces ssl.SSLContext for the whole process); these turn it on."""
import contextlib
import datetime
import http.server
import json
import os
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from meshpool_harness.core import http as H

APP = Path(__file__).resolve().parents[1]
truststore = pytest.importorskip("truststore")


@pytest.fixture
def system_trust(monkeypatch):
    """use_system_trust allowed here, and every trace of it gone afterwards."""
    monkeypatch.delenv("MESHPOOL_SYSTEM_TRUST", raising=False)
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
              "SSL_CERT_FILE", "SSL_CERT_DIR", "MESHPOOL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    plain_https = ssl._create_default_https_context
    H.reset()
    yield
    truststore.extract_from_ssl()
    ssl.SSLContext = H._PLAIN_SSLCONTEXT
    ssl._create_default_https_context = plain_https
    H._TRUST.update(on=False, error="")
    H.reset()


def _ca_and_server_cert(tmp_path):
    """A private CA (the company proxy's) and a localhost certificate it signed, as PEM files."""
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    import ipaddress
    now = datetime.datetime.now(datetime.timezone.utc)

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(name("Example Corp proxy CA")).issuer_name(name("Example Corp proxy CA"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(name("localhost")).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    (tmp_path / "ca.pem").write_bytes(ca.public_bytes(pem))
    (tmp_path / "leaf.pem").write_bytes(leaf.public_bytes(pem))
    (tmp_path / "leaf.key").write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    return tmp_path / "ca.pem", tmp_path / "leaf.pem", tmp_path / "leaf.key"


@contextlib.contextmanager
def _https_server(cert, key):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_POST = do_GET

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = H.server_context()                         # the listener keeps ssl's own class, whatever the client side uses
    ctx.load_cert_chain(str(cert), str(key))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"https://localhost:{srv.server_address[1]}/"
    finally:
        srv.shutdown()
        srv.server_close()


def _calls(url):
    """Every HTTPS path the app uses: the web tools (web._get), the keyed model call (core.http.open), plain urllib
    (the updater, autoupdate), and requests and httpx where they are installed (bundled for skills and Hermes).
    name -> "ok" or the error text."""
    from meshpool_harness import web
    out = {}

    def run(name, fn):
        try:
            fn()
            out[name] = "ok"
        except Exception as e:  # noqa: BLE001
            out[name] = f"{type(e).__name__}: {e}"
    run("web tools", lambda: web._get(url, timeout=10))
    run("model call", lambda: H.open(urllib.request.Request(url + "v1/models"), timeout=10).read())
    run("urllib", lambda: urllib.request.urlopen(url, timeout=10).read())
    try:
        import requests
        run("requests", lambda: requests.get(url, timeout=10).raise_for_status())
    except ImportError:
        pass
    for mod in ("httpx", "httpx2"):
        try:
            hx = __import__(mod)
        except ImportError:
            continue
        run(mod, lambda: hx.get(url, timeout=10).raise_for_status())
    return out


def _uses_truststore_itself(mod: str) -> bool:
    """The installed httpx / httpx2 builds its default context from truststore (httpx2 2.14 does, 2.16 does not)."""
    import importlib
    import inspect
    try:
        return "truststore.SSLContext" in inspect.getsource(importlib.import_module(mod + "._config"))
    except (ImportError, OSError, TypeError):
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="the stand-in system store is truststore's Linux one; macOS and Windows ask their own store")
def test_a_certificate_only_in_the_system_store_is_trusted_everywhere(system_trust, monkeypatch, tmp_path):
    """A local HTTPS server whose CA is only in the system's trust source (truststore's store, here a stand-in that
    holds the private CA, as the Keychain or the Windows store would): every HTTPS path fails before, and works once
    use_system_trust has run."""
    ca, cert, key = _ca_and_server_cert(tmp_path)
    from truststore import _api

    @contextlib.contextmanager
    def system_store(ctx):                           # what the OS knows: the company CA, nothing else
        ctx.load_verify_locations(cafile=str(ca))
        yield
    monkeypatch.setattr(_api, "_configure_context", system_store)
    with _https_server(cert, key) as url:
        before = _calls(url)
        # a client that asks the system's store by itself already works: httpx2 before 2.16 (the newest one for
        # Python 3.10, so the CI's 3.10 job installs it) builds truststore.SSLContext on its own; every other path,
        # meshpool's own first, fails until use_system_trust runs
        native = {m for m in ("httpx", "httpx2") if m in before and _uses_truststore_itself(m)}
        assert {"web tools", "model call", "urllib"} <= set(before), before
        assert all("CERTIFICATE_VERIFY_FAILED" in v or "certificate" in v.lower()
                   for k, v in before.items() if k not in native), before
        assert all(before[k] == "ok" for k in native), before
        assert H.use_system_trust() is True and H.system_trust_on()
        after = _calls(url)
    assert after == {k: "ok" for k in before}, after
    assert type(H.server_context()) is H._PLAIN_SSLCONTEXT         # a listener never gets truststore's class


def test_a_failure_keeps_the_default_trust(system_trust, monkeypatch):
    """truststore that cannot start (a broken system store, an old build) leaves Python's own trust, and HTTPS
    still works the way it did."""
    def broken():
        raise OSError("no system store")
    monkeypatch.setattr(truststore, "inject_into_ssl", broken)
    assert H.use_system_trust() is False
    assert ssl.SSLContext is H._PLAIN_SSLCONTEXT and not H.system_trust_on()
    assert "no system store" in H._TRUST["error"]
    assert isinstance(H.tls_context(), ssl.SSLContext)


def test_it_can_be_turned_off(system_trust, monkeypatch):
    monkeypatch.setenv("MESHPOOL_SYSTEM_TRUST", "0")
    assert H.use_system_trust() is False and ssl.SSLContext is H._PLAIN_SSLCONTEXT


def test_the_tls_context_works_on_the_system_store(system_trust, monkeypatch, tmp_path):
    """tls_context() used to ask the store how many CAs it holds, which truststore's class does not answer: with
    truststore on it must not ask, must not export the Keychain, and must still load a CA file the user named."""
    assert H.use_system_trust()
    monkeypatch.setattr(H, "_macos_bundle", lambda: pytest.fail("the Keychain is read by truststore itself"))
    ctx = H.tls_context()
    assert ctx.__class__.__module__.startswith("truststore") and H.bundle_path() == ""
    ca, _, _ = _ca_and_server_cert(tmp_path)
    monkeypatch.setenv("MESHPOOL_CA_BUNDLE", str(ca))
    H.reset()
    assert H.bundle_path() == str(ca)


# ------------------------------------------------------------------ every entry point turns it on
_PROBE = r"""
import atexit, os, runpy, ssl, sys
atexit.register(lambda: print("TRUST=" + ssl.SSLContext.__module__, flush=True))
mod, args = sys.argv[1], sys.argv[2:]
if mod == "app":                                    # the desktop app: meshpool app, as the launchers start it
    from meshpool_harness.ui import desktop
    desktop.run = lambda: print("APP-START=" + ssl.SSLContext.__module__, flush=True) or 0
    mod, args = "meshpool_harness", ["app"]
sys.argv = [mod] + args
runpy.run_module(mod, run_name="__main__", alter_sys=True)
"""

ENTRY_POINTS = {
    "command line": ("meshpool_harness", ["--version"]),
    "desktop app": ("app", []),
    "web MCP server": ("meshpool_harness.agent.web_mcp_server", []),
    "office MCP server": ("meshpool_harness.agent.office_mcp_server", []),
    "assistant MCP server": ("meshpool_harness.agent.assistant_mcp_server", []),
    "browser MCP server": ("meshpool_harness.agent.browser_mcp_server", []),
    "computer MCP server": ("meshpool_harness.agent.computer_mcp_server", []),
    "demo MCP server": ("meshpool_harness.agent.demo_mcp_server", []),
}


def _probe(tmp_path, mod, args, **env):
    e = {k: v for k, v in os.environ.items() if k != "MESHPOOL_SYSTEM_TRUST"}
    e.update(PYTHONPATH=str(APP), MESHPOOL_HOME=str(tmp_path / "home"), MH_SLOTS_DIR=str(tmp_path / "slots"),
             MESHPOOL_CONFIG=str(tmp_path / "home" / "meshpool.config"), **env)
    r = subprocess.run([sys.executable, "-c", _PROBE, mod, *args], cwd=str(tmp_path), env=e, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=120)
    return r.stdout + r.stderr


@pytest.mark.parametrize("which", list(ENTRY_POINTS))
def test_every_entry_point_turns_it_on(tmp_path, which):
    mod, args = ENTRY_POINTS[which]
    out = _probe(tmp_path, mod, args)
    assert "TRUST=truststore" in out, (which, out[-2000:])
    if mod == "app":
        assert "APP-START=truststore" in out, out[-2000:]       # on before the app's first line runs


def test_an_entry_point_honours_the_switch(tmp_path):
    out = _probe(tmp_path, "meshpool_harness", ["--version"], MESHPOOL_SYSTEM_TRUST="0")
    assert "TRUST=ssl" in out, out[-2000:]
