"""A key only ever goes to the endpoint it was set for (S-01, M15-01) and the wording names that endpoint (F-16)."""
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from meshpool_harness import config as C
from meshpool_harness import provider_setup as PS
from meshpool_harness import providers as PV
from meshpool_harness.core import wire as W
from test_provider_setup import home  # noqa: F401  (fixture: MESHPOOL_HOME/CONFIG in tmp_path, endpoint env cleared)

DEFAULTS = Path(__file__).resolve().parent.parent / "defaults" / "meshpool.config"


class Rec:
    """A local OpenAI-style endpoint that records the headers of every request it gets."""

    def __init__(self):
        self.seen = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _rec(self):
                outer.seen.append({k.lower(): v for k, v in self.headers.items()} | {"_path": self.path})

            def do_GET(self):
                self._rec()
                self._j({"object": "list", "data": [{"id": "m"}]})

            def do_POST(self):
                self._rec()
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._j({"choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

            def _j(self, obj):
                b = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def keys(self):
        return [h.get("authorization") or h.get("api-key") or h.get("x-api-key") for h in self.seen]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def mocks(home, monkeypatch):  # noqa: F811
    monkeypatch.setattr(C, "_INJECTED", set())
    monkeypatch.setattr(C, "SHELL", {})
    monkeypatch.setenv("MH_STREAM", "0")
    made = []

    def mk():
        m = Rec()
        made.append(m)
        return m
    yield mk
    for m in made:
        m.close()


def _chat():
    return W.SimpleLedger("t").chat(model="m", messages=[{"role": "user", "content": "x"}], retries=0)


def test_shell_key_goes_with_shell_base_when_file_is_defaults(home, mocks, monkeypatch):  # noqa: F811
    az, default = mocks(), mocks()
    shutil.copy(DEFAULTS, C.config_path())                       # a settings file copied from the shipped defaults
    monkeypatch.setenv("MESHPOOL_API_KEY", "k-az")
    monkeypatch.setenv("MESHPOOL_BASE_URL", az.url)
    C.load_into_env(override=True)                               # what the app does at start: the file's base wins
    assert PV.endpoint_pair() == (az.url, "k-az", "environment")
    _chat()
    assert az.keys() == ["Bearer k-az"]
    monkeypatch.setattr(PV, "DEFAULT", default.url, raising=False)
    _chat()
    assert default.seen == []                                    # the key never reached the default gateway


def test_shell_key_without_base_is_not_sent_to_custom_file_base(home, mocks, monkeypatch):  # noqa: F811
    custom, default = mocks(), mocks()
    C.config_path().write_text(f"MESHPOOL_BASE_URL={custom.url}\nMESHPOOL_API_KEY=\n")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k-shell")
    monkeypatch.setattr(PV, "DEFAULT", default.url, raising=False)
    C.load_into_env(override=True)
    _chat()
    assert custom.seen == []                                     # the file's address is not the shell key's
    assert default.keys() == ["Bearer k-shell"]


def test_saved_pair_unchanged(home, mocks, monkeypatch):  # noqa: F811
    a, other = mocks(), mocks()
    C.config_path().write_text(f"MESHPOOL_BASE_URL={a.url}\nMESHPOOL_API_KEY=k-file\n")
    monkeypatch.setenv("MESHPOOL_BASE_URL", other.url)           # an address from the shell does not take the file's key
    C.load_into_env()
    assert PV.endpoint_pair() == (a.url, "k-file", "settings")
    _chat()
    assert a.keys() == ["Bearer k-file"] and other.seen == []


def test_openai_env_pair_unchanged(home, mocks, monkeypatch):  # noqa: F811
    o = mocks()
    monkeypatch.setenv("OPENAI_API_KEY", "k-oa")
    monkeypatch.setenv("OPENAI_BASE_URL", o.url)
    assert PV.endpoint_pair() == (o.url, "k-oa", "environment")
    _chat()
    assert o.keys() == ["Bearer k-oa"]
    monkeypatch.delenv("OPENAI_BASE_URL")                        # an OpenAI key alone is never sent to Meshpool
    assert PV.key() == "" and PV.endpoint_pair()[2] == "none"


def _hosted_then(mocks, save_local):
    hosted, local = mocks(), mocks()
    PS.save(hosted.url, "sk-hosted-key-1", default_model="m", allow_custom=True)
    assert PV.key() == "sk-hosted-key-1"
    save_local(local)
    return hosted, local


def _no_auth(local):
    _chat()
    assert local.seen and all(not (h.get("authorization") or h.get("api-key") or h.get("x-api-key"))
                              for h in local.seen)
    assert PV.key() == ""


def test_saving_keyless_local_endpoint_clears_hosted_key(home, mocks):  # noqa: F811
    _, local = _hosted_then(mocks, lambda l: PS.save(l.url, None, default_model="m", allow_custom=True))
    _no_auth(local)
    assert "MESHPOOL_API_KEY=" in C.config_path().read_text()
    assert C.read().get("MESHPOOL_API_KEY", "") == ""


def test_saving_keyless_local_endpoint_through_settings_route(home, mocks):  # noqa: F811
    from meshpool_harness.ui import server as S
    _, local = _hosted_then(mocks, lambda l: S.save_settings({"values": {"MESHPOOL_BASE_URL": l.url}}))
    _no_auth(local)
    assert C.read().get("MESHPOOL_API_KEY", "") == ""


def test_hosted_endpoint_without_key_is_still_refused(home, mocks):  # noqa: F811
    from meshpool_harness.ui import server as S
    hosted = mocks()
    PS.save(hosted.url, "sk-hosted-key-1", default_model="m", allow_custom=True)
    with pytest.raises(ValueError):
        S.save_settings({"values": {"MESHPOOL_BASE_URL": "https://api.example.com/v1"}})
    assert PV.key() == "sk-hosted-key-1"


def _shell_then_file(mocks, monkeypatch):
    a, b = mocks(), mocks()
    C.config_path().write_text("MESHPOOL_API_KEY=k-file\n")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k-shell")
    monkeypatch.setenv("MESHPOOL_BASE_URL", a.url)
    C.load_into_env(override=True)
    assert PV.endpoint_pair()[:2] == (a.url, "k-shell")
    return a, b


def test_saved_key_beats_earlier_shell_key_after_save(home, mocks, monkeypatch):  # noqa: F811
    a, b = _shell_then_file(mocks, monkeypatch)
    PS.save(b.url, "k-new-key-12", default_model="m", allow_custom=True)
    _chat()
    assert b.keys() == ["Bearer k-new-key-12"] and a.seen == []


def test_saved_key_beats_earlier_shell_key_through_settings_route(home, mocks, monkeypatch):  # noqa: F811
    from meshpool_harness.ui import server as S
    a, b = _shell_then_file(mocks, monkeypatch)
    S.save_settings({"values": {"MESHPOOL_BASE_URL": b.url, "MESHPOOL_API_KEY": "k-new-key-12"}})
    _chat()
    chats = [h for h in b.seen if h["_path"].endswith("/chat/completions")]   # (local-server probes carry no key)
    assert [h.get("authorization") for h in chats] == ["Bearer k-new-key-12"] and a.seen == []


def test_saving_only_the_model_keeps_shell_pair(home, mocks, monkeypatch):  # noqa: F811
    from meshpool_harness.ui import server as S
    a, _ = _shell_then_file(mocks, monkeypatch)
    S.save_settings({"values": {"MH_MODEL": "x"}})
    assert PV.endpoint_pair() == (a.url, "k-shell", "environment")


def test_shell_key_is_dropped_when_saving_keyless_local_endpoint(home, mocks, monkeypatch):  # noqa: F811
    a, local = _shell_then_file(mocks, monkeypatch)
    PS.save(local.url, None, default_model="m", allow_custom=True)
    _no_auth(local)
    assert a.seen == []


def test_wording_names_custom_endpoint(home, monkeypatch, capsys):  # noqa: F811
    from meshpool_harness import cli
    from meshpool_harness.agent import onboard
    monkeypatch.setenv("MESHPOOL_API_KEY", "k-az-1234")
    monkeypatch.setenv("MESHPOOL_BASE_URL", "https://res.openai.azure.com/openai/v1")
    monkeypatch.setattr(C, "SHELL", {"MESHPOOL_API_KEY": "k-az-1234",
                                     "MESHPOOL_BASE_URL": "https://res.openai.azure.com/openai/v1"})
    monkeypatch.setattr(C, "_INJECTED", set())
    s = PV.setup("key")
    assert "res.openai.azure.com" in s and "Meshpool" not in s
    cli._check(False)
    line = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("API key")][0]
    assert "res.openai.azure.com" in line and "Meshpool" not in line
    assert "res.openai.azure.com" in onboard._checking_text("https://res.openai.azure.com/openai/v1", True)
    assert "Meshpool" not in onboard._key_title(True) and "Meshpool" in onboard._key_title(False)
    # the Meshpool endpoint keeps its own wording
    monkeypatch.setattr(C, "SHELL", {})
    monkeypatch.delenv("MESHPOOL_BASE_URL")
    assert "Meshpool" in PV.setup("key")
