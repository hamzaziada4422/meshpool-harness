"""The one way a keyed model request is opened (standard library only).

 - a redirect is followed only to the same host (same scheme and port, or an http -> https upgrade of the same host),
   never from https to http; any other target raises an HTTPError that names the new host, so the key never leaves
   the host the user gave;
 - a loopback model server is never reached through an HTTP(S)_PROXY from the environment;
 - HTTPS trust: the operating system's certificates (truststore, turned on at start-up by use_system_trust: the
   macOS Keychain, the Windows certificate store, the Linux system bundle), so a company proxy whose certificate is
   installed only there works; plus a CA file the user names (MESHPOOL_CA_BUNDLE, a company proxy's CA). Without
   truststore: ssl's default context, plus a CA bundle when this Python has none (python.org on macOS).
"""
from __future__ import annotations

import io
import ipaddress
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

_REDIRECTS = (301, 302, 303, 307, 308)
_MAX_HOPS = 5
_API_TAIL = ("/chat/completions", "/messages", "/responses", "/models")
_FALLBACK_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")
_KEYCHAINS = ("/Library/Keychains/System.keychain", "/System/Library/Keychains/SystemRootCertificates.keychain")

_LOCK = threading.RLock()
_CTX: ssl.SSLContext | None = None
_STATE: dict = {"bundle": "", "needed": False, "installed": False}
# ssl's own context class, kept before use_system_trust replaces ssl.SSLContext: a listener (server side) needs it,
# because truststore's class checks the peer's certificate on every handshake and a browser sends none (on Windows
# that refuses every connection).
_PLAIN_SSLCONTEXT = ssl.SSLContext if ssl.SSLContext.__module__ == "ssl" else getattr(
    sys.modules.get("truststore._ssl_constants"), "_original_SSLContext", ssl.SSLContext)
_TRUST: dict = {"on": False, "error": ""}


# ------------------------------------------------------------------ redirects
def _is_loopback(host: str) -> bool:
    h = (host or "").strip("[]").lower()
    if h in ("localhost", "0.0.0.0", "::1") or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _port(u) -> int:
    return u.port or (443 if u.scheme == "https" else 80)


def _same_host_target(old: str, new: str) -> bool:
    a, b = urlparse(old), urlparse(new)
    if b.scheme not in ("http", "https") or not a.hostname or not b.hostname:
        return False
    if a.hostname.lower() != b.hostname.lower():
        return False
    if a.scheme == b.scheme and _port(a) == _port(b):
        return True
    # an upgrade of the same host: http:80 -> https:443, or the same explicit port; never https -> http
    return a.scheme == "http" and b.scheme == "https" and (_port(a), _port(b)) in ((80, 443), (_port(a), _port(a)))


def _moved_message(newurl: str) -> str:
    p = urlparse(newurl)
    path = p.path.rstrip("/")
    for tail in _API_TAIL:
        if path.endswith(tail):
            path = path[: -len(tail)]
            break
    base = f"{p.scheme}://{p.netloc}{path}"
    return (f"The endpoint redirected to {p.hostname or p.netloc}; Meshpool did not send your key there. "
            f"If the service moved, set the endpoint address to {base}.")


class _KeyedRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = _MAX_HOPS

    def __init__(self, refuse: bool = False):
        self.refuse = refuse

    # Python 3.10's handler knows 301/302/303/307 only (308 came in 3.11): without this a 308 surfaced as a bare
    # "HTTP 308" that neither named the target nor said the key stayed home
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code not in _REDIRECTS:
            return None
        newurl = urljoin(req.full_url, newurl)
        if self.refuse or not _same_host_target(req.full_url, newurl):
            text = _moved_message(newurl)
            raise urllib.error.HTTPError(req.full_url, code, text, headers, io.BytesIO(text.encode("utf-8")))
        hdrs = {k: v for k, v in req.header_items() if k.lower() != "content-length"}
        return urllib.request.Request(newurl, data=req.data, headers=hdrs, method=req.get_method(),
                                      origin_req_host=req.origin_req_host, unverifiable=True)


# ------------------------------------------------------------------ open
def open(req, timeout, handlers=(), refuse_redirects: bool = True):  # noqa: A001 - the module's one verb
    """urllib's urlopen for a request that carries a key. `handlers` are extra urllib handlers (the agent's
    connection trackers). A request with a key never follows a redirect (0.0.14): every redirect becomes an
    HTTPError that names the target host, says the key was not sent there and which address to set.
    refuse_redirects=False follows a same-host redirect only (never another host, never https to http)."""
    if isinstance(req, str):
        req = urllib.request.Request(req)
    host = urlparse(req.full_url).hostname or ""
    hs = [urllib.request.ProxyHandler({}) if _is_loopback(host) else urllib.request.ProxyHandler(),
          _KeyedRedirect(refuse=refuse_redirects)]
    hs.extend(handlers)
    if not any(isinstance(h, urllib.request.HTTPSHandler) for h in hs):
        hs.append(urllib.request.HTTPSHandler(context=tls_context()))
    return urllib.request.build_opener(*hs).open(req, timeout=timeout)


# ------------------------------------------------------------------ TLS trust
def system_trust_on() -> bool:
    """truststore is in place: ssl.SSLContext is its class, which asks the operating system to verify certificates."""
    return _TRUST["on"] and ssl.SSLContext is not _PLAIN_SSLCONTEXT


def use_system_trust() -> bool:
    """Trust the operating system's certificates for every HTTPS call in this process: urllib (model calls, web
    tools, updates), requests and httpx all build their contexts from ssl.SSLContext, which truststore replaces. A
    company proxy whose certificate is only in the macOS Keychain or the Windows certificate store then works. Call
    it first thing at start-up, before any HTTP client is made. MESHPOOL_SYSTEM_TRUST=0 leaves Python's default.
    Never raises: any failure keeps the default trust (and is kept in _TRUST["error"]). True when it is on."""
    with _LOCK:
        if system_trust_on():
            return True
        if os.environ.get("MESHPOOL_SYSTEM_TRUST", "").strip().lower() in ("0", "false", "no", "off"):
            return False
        try:
            import truststore
            truststore.inject_into_ssl()
            ssl.create_default_context().wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname="localhost")
        except Exception as e:  # noqa: BLE001 - missing, too old, or this platform's store failed: keep the default
            _TRUST["error"] = f"{type(e).__name__}: {e}"[:300]
            try:
                truststore.extract_from_ssl()           # type: ignore[possibly-undefined]
            except Exception:  # noqa: BLE001
                ssl.SSLContext = _PLAIN_SSLCONTEXT
            return False
        _TRUST.update(on=True, error="")
        ur = sys.modules.get("urllib.request")       # urlopen's shared opener made its context when it was built
        op = getattr(ur, "_opener", None)
        if op is not None and any(isinstance(h, ur.HTTPSHandler) and type(getattr(h, "_context", None))
                                  is _PLAIN_SSLCONTEXT for h in getattr(op, "handlers", ())):
            ur.install_opener(None)                  # the next urlopen builds one on the system's trust
        installed = _STATE["installed"]
        reset()                                         # a context cached before this is rebuilt on the system's trust
        if installed:
            install_tls()
        return True


def server_context() -> ssl.SSLContext:
    """A context for a local HTTPS listener (the Office panel): ssl's own class, never truststore's."""
    return _PLAIN_SSLCONTEXT(ssl.PROTOCOL_TLS_SERVER)


def _load(ctx: ssl.SSLContext, path) -> bool:
    try:
        ctx.load_verify_locations(cafile=str(path))
        return True
    except (OSError, ssl.SSLError):
        return False


def _macos_bundle() -> str:
    """The System and SystemRoot keychains as one PEM file (a company proxy's CA lives there), kept for a day."""
    try:
        from meshpool_harness import config as C
        dest = C.home() / "cache" / "ca-bundle.pem"
        if dest.is_file() and dest.stat().st_size > 0 and time.time() - dest.stat().st_mtime < 86400:
            return str(dest)
        exe = shutil.which("security") or "/usr/bin/security"
        out = subprocess.run([exe, "find-certificate", "-a", "-p", *_KEYCHAINS],
                             capture_output=True, text=True, timeout=15, check=False).stdout
        if "BEGIN CERTIFICATE" not in out:
            return ""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + f".{os.getpid()}.tmp")
        tmp.write_text(out, encoding="utf-8")
        C.replace_file(tmp, dest)
        return str(dest)
    except (OSError, subprocess.SubprocessError):
        return ""


def tls_context() -> ssl.SSLContext:
    """The HTTPS trust every Meshpool call uses (cached). A normal system gets ssl's default context untouched."""
    global _CTX
    with _LOCK:
        if _CTX is not None:
            return _CTX
        ctx = ssl.create_default_context()
        bundle, needed = "", False
        chosen = None
        for n in ("MESHPOOL_CA_BUNDLE", "SSL_CERT_FILE"):
            p = os.path.expanduser(os.environ.get(n, "").strip())
            if p and os.path.isfile(p):
                chosen = (n, p)
                break
        if chosen and _load(ctx, chosen[1]):
            bundle, needed = chosen[1], chosen[0] == "MESHPOOL_CA_BUNDLE"
        elif system_trust_on():
            pass                    # the system's own store is used: no fallback file, and no Keychain export below
        elif ctx.cert_store_stats().get("x509_ca", 0) == 0:
            cands = []
            try:
                import certifi                                    # type: ignore
                cands.append(certifi.where())
            except Exception:  # noqa: BLE001 - optional
                pass
            cands += _FALLBACK_FILES
            for p in cands:
                if p and os.path.isfile(p) and _load(ctx, p):
                    bundle, needed = p, True
                    break
        if sys.platform == "darwin" and not os.environ.get("MESHPOOL_CA_BUNDLE", "").strip() and \
                not system_trust_on():
            # A company TLS proxy's CA lives in the System keychain, which a Python with its own CA bundle (Homebrew,
            # python.org) never reads: add the keychain certificates on top of whatever was loaded.
            p = _macos_bundle()
            if p and _load(ctx, p):
                bundle, needed = p, True
        _STATE.update(bundle=bundle, needed=needed)
        _CTX = ctx
        return ctx


def bundle_path() -> str:
    """The CA file tls_context() added ("" when ssl's default trust was enough)."""
    tls_context()
    return _STATE["bundle"]


def reset() -> None:
    """Forget the cached context (tests, or after the user changes the CA setting)."""
    global _CTX
    with _LOCK:
        _CTX = None
        _STATE.update(bundle="", needed=False, installed=False)


def install_tls() -> None:
    """Give every other urllib HTTPS call in the app the same trust, only when a fallback or override was needed."""
    with _LOCK:
        if _STATE["installed"]:
            return
        tls_context()
        _STATE["installed"] = True
        if not _STATE["needed"]:
            return
        ssl._create_default_https_context = lambda *a, **k: tls_context()   # noqa: SLF001
        if _STATE["bundle"]:
            for name in ("SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS"):
                os.environ.setdefault(name, _STATE["bundle"])


# ------------------------------------------------------------------ error text
def explain(exc, host: str = "") -> str:
    """A failed-HTTPS error as one actionable sentence; any other error as its text. `host` may be a URL."""
    chain, e = [], exc
    for _ in range(4):
        if e is None or isinstance(e, str):
            break
        chain.append(e)
        e = getattr(e, "reason", None)
    if not any(isinstance(t, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(t) for t in chain):
        return str(exc)
    h = host or getattr(exc, "url", "") or ""
    if "://" in h:
        h = urlparse(h).hostname or h
    return (f"The HTTPS certificate of {h or 'the server'} could not be verified (no trusted CA certificates, or a "
            "company proxy's certificate). Set MESHPOOL_CA_BUNDLE to your company's CA file (PEM); on macOS with "
            "python.org Python, run Install Certificates.command once.")
