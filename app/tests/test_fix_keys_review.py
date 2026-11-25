"""Release review (keys): a saved endpoint is one pair, keys stay out of error text and off other hosts, plan mode
holds in -p, and the installer looks for the settings where the program keeps them. Local mock servers only."""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from meshpool_harness import config as C
from meshpool_harness import provider_setup as PS
from meshpool_harness import providers as PV
from meshpool_harness.core import endpoint as EP
from test_key_endpoint_pairing import DEFAULTS, _chat, mocks  # noqa: F401  (fixture)
from test_provider_setup import home  # noqa: F401  (fixture: MESHPOOL_HOME/CONFIG in tmp_path, endpoint env cleared)

ROOT = Path(__file__).resolve().parents[2]
KEY = "sk-ant-api03-Zx9Qw7Er5Ty3Ui1Op0As2Df4Gh6Jk8Lz"


# ---------- 1. an explicit save decides the key and its address together ---------------------------------------------
def _shell_key_with_default_file(monkeypatch, default_url):
    """MESHPOOL_API_KEY exported in the shell; MESHPOOL_BASE_URL only in the settings file (the shipped defaults)."""
    shutil.copy(DEFAULTS, C.config_path())
    monkeypatch.setattr(PV, "DEFAULT", default_url, raising=False)
    monkeypatch.setenv("MESHPOOL_API_KEY", "k-shell-meshpool-1")
    monkeypatch.setitem(C.SHELL, "MESHPOOL_API_KEY", "k-shell-meshpool-1")     # as read at import
    C.load_into_env()
    assert "MESHPOOL_BASE_URL" in C._INJECTED and PV.endpoint_pair()[1:] == ("k-shell-meshpool-1", "environment")


def test_saved_provider_key_goes_to_its_own_base_not_the_default(home, mocks, monkeypatch):  # noqa: F811
    new, default = mocks(), mocks()
    _shell_key_with_default_file(monkeypatch, default.url)
    PS.save(new.url, "sk-new-provider-key-1", default_model="m", allow_custom=True)
    assert PV.endpoint_pair() == (new.url, "sk-new-provider-key-1", "settings")
    _chat()
    assert new.keys() == ["Bearer sk-new-provider-key-1"] and default.seen == []
    C.load_into_env(override=True)                     # the app reloads its settings: nothing is taken for the shell's
    assert C.SHELL.get("MESHPOOL_API_KEY") is None and C.SHELL.get("MESHPOOL_BASE_URL") is None
    assert PV.endpoint_pair() == (new.url, "sk-new-provider-key-1", "settings")
    _chat()
    assert default.seen == []


@pytest.mark.parametrize("base", ["https://api.anthropic.com/v1", "https://api.openai.com/v1"])
def test_saved_hosted_provider_pairs_with_its_base(home, mocks, monkeypatch, base):  # noqa: F811
    default = mocks()
    _shell_key_with_default_file(monkeypatch, default.url)
    PS.save(base, KEY, default_model="m", allow_custom=True)         # no network: save only writes
    assert PV.endpoint_pair() == (base, KEY, "settings")
    C.load_into_env(override=True)
    assert PV.endpoint_pair() == (base, KEY, "settings")


def test_plain_write_of_a_new_key_takes_effect_on_first_save(home, mocks, monkeypatch):  # noqa: F811
    a, b = mocks(), mocks()
    monkeypatch.setenv("MESHPOOL_API_KEY", "k-shell-1234")
    monkeypatch.setenv("MESHPOOL_BASE_URL", a.url)
    monkeypatch.setitem(C.SHELL, "MESHPOOL_API_KEY", "k-shell-1234")
    monkeypatch.setitem(C.SHELL, "MESHPOOL_BASE_URL", a.url)
    C.write({"MESHPOOL_API_KEY": "k-written-5678"})
    k_base, k, src = PV.endpoint_pair()
    assert k == "k-written-5678" and src == "settings"
    assert k_base != a.url                              # never the address the shell set for its own key
    C.write({"MESHPOOL_API_KEY": "k-written-5678", "MESHPOOL_BASE_URL": b.url})
    assert PV.endpoint_pair() == (b.url, "k-written-5678", "settings")
    _chat()
    assert b.keys() == ["Bearer k-written-5678"] and a.seen == []


def test_writing_only_the_base_does_not_pair_it_with_the_shell_key(home, mocks, monkeypatch):  # noqa: F811
    a, b = mocks(), mocks()
    monkeypatch.setenv("MESHPOOL_API_KEY", "k-shell-1234")
    monkeypatch.setitem(C.SHELL, "MESHPOOL_API_KEY", "k-shell-1234")
    C.write({"MESHPOOL_BASE_URL": b.url})
    assert PV.key() != "k-shell-1234"                   # the shell's key is not sent to the address just saved


# ---------- 5. the old endpoint's auth header and scheme do not follow the user to a new one -------------------------
def test_auth_header_blanked_when_endpoint_changes(home, mocks):  # noqa: F811
    a, b = mocks(), mocks()
    PS.save(a.url, "k-first-12345", default_model="m", allow_custom=True, auth_header="api-key", auth_scheme="raw")
    assert C.read()["MH_AUTH_HEADER"] == "api-key" and C.read()["MH_AUTH_SCHEME"] == "raw"
    PS.save(a.url, "k-first-12345", default_model="m2", allow_custom=True)      # same endpoint: kept
    assert C.read()["MH_AUTH_HEADER"] == "api-key" and PV._ecfg("MH_AUTH_SCHEME") == "raw"
    PS.save(b.url, "k-second-1234", default_model="m", allow_custom=True)       # /login and onboarding pass none
    assert C.read()["MH_AUTH_HEADER"] == "" and C.read()["MH_AUTH_SCHEME"] == ""
    assert PV._ecfg("MH_AUTH_HEADER") == "" and PV._ecfg("MH_AUTH_SCHEME") == ""
    _chat()
    assert b.keys() == ["Bearer k-second-1234"]
    PS.save(a.url, "k-first-12345", default_model="m", allow_custom=True, auth_header="x-key", auth_scheme="raw")
    assert C.read()["MH_AUTH_HEADER"] == "x-key"                                # passed: saved as given


# ---------- 2. the price list fetch never follows a redirect with the key --------------------------------------------
class _Srv:
    def __init__(self, handler):
        self.srv = HTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def test_price_fetch_refuses_redirect_and_keeps_the_key(home, monkeypatch):  # noqa: F811
    from meshpool_harness.core import usage as U
    seen = []

    class Thief(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            b = b'{"data": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    thief = _Srv(Thief)

    class Redir(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", thief.url.replace("127.0.0.1", "localhost") + "/v1/models")
            self.end_headers()

    src = _Srv(Redir)
    try:
        monkeypatch.setenv("MESHPOOL_API_KEY", "sk-price-key-123456")
        monkeypatch.setenv("MESHPOOL_BASE_URL", src.url + "/v1")
        assert PV.endpoint_pair() == (src.url + "/v1", "sk-price-key-123456", "environment")
        assert U._http_prices(src.url + "/v1") is None
        assert seen == []                               # the other host never saw the request, let alone the key
    finally:
        src.close()
        thief.close()


# ---------- 4. the key is taken out of error text, punctuation next to it or not ---------------------------------------
@pytest.mark.parametrize("tail", [".", "-", "_", "", "'", "...."])
def test_explain_redacts_key_followed_by_punctuation(tail):
    body = {"error": {"message": f"Invalid API key: {KEY}{tail} Check it.", "code": "invalid_api_key"}}
    out = EP.explain(400, body, host="api.example.com", key=KEY)
    assert KEY not in out and "***" in out


def test_explain_keeps_short_keys_and_words_alone():
    assert EP.redact_key("a password here", "pass") == "a password here"
    assert EP.redact_key("tokenABCDEFGHIJ", "ABCDEFGHIJ") == "tokenABCDEFGHIJ"


def test_client_redacts_ledger_key_from_error_body(monkeypatch):
    from meshpool_harness.core import client as CL

    class Ledger:
        base_url = "http://127.0.0.1:9/v1"
        api_key = KEY

        def chat(self, **kw):
            e = RuntimeError("meshpool HTTP 429: rate limited")
            e.body = {"error": {"message": f"Invalid API key: {KEY}. Slow down."}}
            raise e

    monkeypatch.setenv("MH_RATE_LIMIT_WAIT_S", "0")
    c = CL.LiveClient.__new__(CL.LiveClient)
    c.phase, c.ledger = "phase_2_build", Ledger()
    assert not hasattr(c, "api_key") or not c.api_key          # the key lives on the ledger only
    with pytest.raises(RuntimeError) as ei:
        c._chat_send(model="m-redact", messages=[{"role": "user", "content": "x"}], attempts=1)
    assert "rate limit" in str(ei.value) and KEY not in str(ei.value)


# ---------- 8. no guessing http for a keyed request --------------------------------------------------------------------
@pytest.mark.parametrize("addr", ["llm.internal:8000/v1", "gpu.lan/v1", "myserver:8000/v1", "192.168.1.20:8000/v1",
                                  "10.0.0.5/v1"])
def test_schemeless_non_loopback_address_is_https(addr):
    assert EP.parse_pasted(addr).base.startswith("https://")


@pytest.mark.parametrize("addr", ["localhost:11434/v1", "127.0.0.1:8000/v1", "[::1]:8000/v1"])
def test_schemeless_loopback_address_is_http(addr):
    assert EP.parse_pasted(addr).base.startswith("http://")


def test_tls_failure_never_retries_keyed_request_over_http(home, monkeypatch):  # noqa: F811
    calls = []

    def fake_get(url, headers, timeout=15.0, body=None):
        calls.append((url, dict(headers)))
        return (0, None, "tls") if url.startswith("https://") else (200, {"data": [{"id": "m"}]}, "")

    monkeypatch.setattr(PS, "_get", fake_get)
    r = PS.test("https://192.168.1.20:8000/v1", "sk-lan-key-123456")
    assert not r["ok"] and "http://" in r["error"]                  # told how to choose http, not done for them
    assert all(u.startswith("https://") for u, _ in calls)
    calls.clear()
    r = PS.test("https://gpu.local:8000/v1", None)                   # keyless: the plain-http retry is still fine
    assert r["ok"] and any(u.startswith("http://") for u, _ in calls)
    calls.clear()
    r = PS.test("http://192.168.1.20:8000/v1", "sk-lan-key-123456")  # typed http:// by the user: used as typed
    assert r["ok"] and all(u.startswith("http://") for u, _ in calls)


# ---------- 7. `meshpool run` takes the same endpoint check as -p ------------------------------------------------------
def test_run_accepts_keyless_local_endpoint_and_refuses_bare_openai_key(home, monkeypatch, tmp_path):  # noqa: F811
    from meshpool_harness import cli
    from meshpool_harness import harnesses as HR
    monkeypatch.setattr(HR, "signed_in", lambda h: False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-only-123456")           # no OPENAI_BASE_URL: no endpoint
    with pytest.raises(SystemExit) as ei:
        cli._run_request("make a deck", [], str(tmp_path / "out"))
    assert "no API key" in str(ei.value)
    monkeypatch.delenv("OPENAI_API_KEY")
    C.write({"MESHPOOL_BASE_URL": "http://127.0.0.1:11434/v1", "MESHPOOL_API_KEY": ""})
    assert PV.ready("key")

    class Stop(Exception):
        pass

    def stop(*a, **k):
        raise Stop
    monkeypatch.setattr("meshpool_harness.router.router.route", stop)
    monkeypatch.setattr("meshpool_harness.core.client.LiveClient.__init__", lambda self, phase="": None)
    with pytest.raises(Stop):                                             # got past the key check
        cli._run_request("make a deck", [], str(tmp_path / "out"))


# ---------- 9. install.sh looks for the settings where the program keeps them ------------------------------------------
def _sh_ready(env):
    script = (ROOT / "install.sh").read_text().rsplit('main "$@"', 1)[0]
    return subprocess.run(["bash", "-c", script + '\nready && echo READY || echo NOT; settings_file'],
                          env=env, capture_output=True, text=True, timeout=30).stdout.split()


@pytest.mark.skipif(sys.platform == "win32", reason="install.sh is for macOS and Linux")
def test_install_sh_reads_the_real_settings_file(tmp_path):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path), "DIR": "unused"}
    want = subprocess.run([sys.executable, "-c", "from meshpool_harness import config as C; print(C.config_path())"],
                          env=dict(env, PYTHONPATH=str(ROOT / "app")), capture_output=True, text=True,
                          cwd=str(ROOT / "app")).stdout.strip()
    f = Path(want)
    f.parent.mkdir(parents=True)
    f.write_text("MESHPOOL_API_KEY=sk-saved-123456\n")
    out = _sh_ready(env)
    assert out[0] == "READY" and out[1] == want
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    out = _sh_ready(dict(env, MESHPOOL_HOME=str(custom)))
    assert out[0] == "NOT" and out[1] == str(custom / "meshpool.config")


def test_readme_names_the_real_settings_folders():
    text = (ROOT / "packaging" / "harness-docs" / "README.md").read_text()
    assert "~/.local/share/meshpool/meshpool.config" in text and "~/.local/share/Meshpool" not in text
    assert "keeps its own copy in `~/.meshpool-cli`" not in text


def test_terminal_onboarding_to_new_endpoint_blanks_old_auth_header(home, monkeypatch):  # noqa: F811
    from test_onboarding_keys import KEY as OKEY, Mock as OMock, _only_chat
    from meshpool_harness.agent import onboard as OB
    s = OMock(_only_chat)
    try:
        C.config_path().write_text("MESHPOOL_BASE_URL=https://res.openai.azure.com/openai/v1\nMESHPOOL_API_KEY=k-az-1234\n"
                                   "MH_AUTH_HEADER=api-key\nMH_AUTH_SCHEME=raw\n")
        answers = iter([f"http://127.0.0.1:{s.port}/v1", "m"])
        monkeypatch.setattr(OB, "_ask", lambda st, q, default="": next(answers))
        monkeypatch.setattr(OB.getpass, "getpass", lambda *a, **k: OKEY)
        monkeypatch.setattr(OB, "_p", lambda *a, **k: None)
        monkeypatch.setattr(OB, "_use", lambda which: None)

        class St:
            depth = 0

            def __getattr__(self, n):
                return lambda x="": x
        assert OB._key_step(St(), False, None, True)
        saved = C.read()
        assert saved["MESHPOOL_BASE_URL"] == f"http://127.0.0.1:{s.port}/v1"
        assert saved["MH_AUTH_HEADER"] == "" and saved["MH_AUTH_SCHEME"] == ""
    finally:
        s.srv.shutdown()
