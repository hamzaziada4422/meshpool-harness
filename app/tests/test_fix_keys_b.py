"""Custom header and scheme, Anthropic-style, no-key local servers, Settings test connection and saved keys of any shape."""
import base64
import json

import pytest

from meshpool_harness import config as C
from meshpool_harness import provider_setup as PS
from meshpool_harness import providers as PV
from tests.test_fix_keys_a import KEY32, Fake, _ok_chat, home  # noqa: F401


def _models_and_chat(rec):
    if rec["path"].endswith("/models"):
        return 200, {"data": [{"id": "m1"}]}
    return _ok_chat(rec)


@pytest.mark.parametrize("key,header,scheme,expect", [
    ("sk-abc.DEF_123=", "", "", ("authorization", "Bearer sk-abc.DEF_123=")),
    ("plainkey", "api-key", "", ("api-key", "plainkey")),
    ("plainkey", "x-api-key", "raw", ("x-api-key", "plainkey")),
    ("plainkey", "Authorization", "token", ("authorization", "Token plainkey")),
    ("user:pw", "", "basic", ("authorization", "Basic " + base64.b64encode(b"user:pw").decode())),
    ("eyJhbGciOi.eyJzdWIi.sig_nature-x", "", "", ("authorization", "Bearer eyJhbGciOi.eyJzdWIi.sig_nature-x")),
])
def test_setup_test_and_save_send_the_chosen_header(home, monkeypatch, key, header, scheme, expect):
    monkeypatch.setenv("MH_STREAM", "0")
    f = Fake(_models_and_chat)
    try:
        r = PS.test(f.url + "/v1", key, auth_header=header, auth_scheme=scheme)
        assert r["ok"], r
        assert f.log[-1]["path"] == "/v1/models" and f.log[-1]["headers"][expect[0]] == expect[1]
        PS.save(f.url + "/v1", key, "auto", "m1", auth_header=header, auth_scheme=scheme)
        C.load_into_env(override=True)
        assert C.read()["MH_AUTH_HEADER"] == header and C.read()["MESHPOOL_API_KEY"] == key
        from meshpool_harness.core.wire import SimpleLedger
        out = SimpleLedger("t").chat(model="m1", messages=[{"role": "user", "content": "x"}], retries=0)
        assert out["choices"][0]["message"]["content"] == "ok"
        h = f.log[-1]["headers"]
        assert f.log[-1]["path"] == "/v1/chat/completions" and h[expect[0]] == expect[1]
        if expect[0] != "authorization":
            assert "authorization" not in h
    finally:
        f.close()


def test_local_server_without_a_key(home, monkeypatch):
    monkeypatch.setenv("MH_STREAM", "0")
    f = Fake(_models_and_chat)
    try:
        r = PS.test(f.url + "/v1", "")
        assert r["ok"] and "authorization" not in f.log[-1]["headers"]
        PS.save(f.url + "/v1", "", "auto", "m1")
        C.load_into_env(override=True)
        assert PV.ready("key")
        from meshpool_harness.core.wire import SimpleLedger
        SimpleLedger("t").chat(model="m1", messages=[{"role": "user", "content": "x"}], retries=0)
        assert "authorization" not in f.log[-1]["headers"]
    finally:
        f.close()


def test_remote_host_still_needs_a_key(home):
    assert "Enter the API key" in PS.test("https://api.example.com/v1", "")["error"]


def test_anthropic_style_headers(home):
    def h(rec):
        if rec["path"].endswith("/models"):
            return 200, {"data": [{"id": "claude-x", "type": "model", "display_name": "X"}]}
        return 200, {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                     "usage": {"input_tokens": 1, "output_tokens": 1}}
    f = Fake(h)
    try:
        r = PS.test(f.url + "/v1", "ant-key.1", wire="anthropic")
        assert r["ok"] and r["wire"] == "anthropic"
        hd = f.log[-1]["headers"]
        assert hd["x-api-key"] == "ant-key.1" and hd["anthropic-version"] and "authorization" not in hd
    finally:
        f.close()


def test_errors_name_the_problem(home):
    codes = {"v1": 401}

    def h(rec):
        return codes["v1"], {"error": {"message": "nope"}}
    f = Fake(h)
    try:
        assert "rejected" in PS.test(f.url + "/v1", "k")["error"] and "401" in PS.test(f.url + "/v1", "k")["error"]
        codes["v1"] = 403
        assert "403" in PS.test(f.url + "/v1", "k")["error"]
    finally:
        f.close()
    r = PS.explain(404, {"error": {"message": "model foo not found"}}, base="https://x.example/v1", model="foo")
    assert "foo" in r and "404" in r


def test_settings_test_connection_azure_and_custom_header(home, monkeypatch):
    from meshpool_harness.ui import server as SV
    monkeypatch.setattr(PV, "AZURE_SUFFIXES", ("127.0.0.1",))
    f = Fake(lambda rec: _ok_chat(rec) if rec["headers"].get("api-key") == KEY32 else (401, {"error": {"message": "bad"}}))
    try:
        r = SV.test_connection({"MESHPOOL_BASE_URL": f.url, "MESHPOOL_API_KEY": "Bearer " + KEY32, "MH_MODEL": "dd"})
        assert r["ok"], r
        assert f.log[-1]["path"].startswith("/openai/deployments/dd/chat/completions?api-version=2024-10-21")
        bad = SV.test_connection({"MESHPOOL_BASE_URL": f.url, "MESHPOOL_API_KEY": "0" * 32, "MH_MODEL": "dd"})
        assert not bad["ok"] and "rejected the key" in bad["detail"]
        r = SV.test_connection({"MESHPOOL_BASE_URL": f.url, "MESHPOOL_API_KEY": KEY32, "MH_MODEL": "dd",
                                "MESHPOOL_AZURE_API_VERSION": "2025-04-01-preview"})
        assert f.log[-1]["path"].endswith("api-version=2025-04-01-preview")
    finally:
        f.close()


def test_save_settings_keeps_key_shape(home):
    from meshpool_harness.ui import server as SV
    SV.save_settings({"values": {"MESHPOOL_API_KEY": '  "Bearer ab-12.cd_34=="\n', "MH_AUTH_HEADER": "api-key",
                                 "MH_AUTH_SCHEME": "raw", "MESHPOOL_AZURE_API_VERSION": "2024-10-21"}})
    v = C.read()
    assert v["MESHPOOL_API_KEY"] == "ab-12.cd_34==" and v["MH_AUTH_HEADER"] == "api-key"


def test_save_rejects_a_bad_header_name(home):
    with pytest.raises(PS.SetupError):
        PS.save("http://127.0.0.1:1/v1", "", "auto", "m", auth_header="bad header")
    with pytest.raises(PS.SetupError):
        PS.save("http://127.0.0.1:1/v1", "", "auto", "m", auth_scheme="digest")


def test_key_spend_probe_uses_the_header(home):
    from meshpool_harness.ui import settings_v2 as S2
    f = Fake(_ok_chat)
    try:
        PS.save(f.url + "/v1", "k.1", "auto", "m1", auth_header="x-token", auth_scheme="raw")
        C.load_into_env(override=True)
        S2.key_spend(force=True)
        assert f.log[-1]["headers"]["x-token"] == "k.1"
    finally:
        f.close()
