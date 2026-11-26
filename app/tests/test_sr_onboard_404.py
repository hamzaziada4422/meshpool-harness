"""SR-05, SR-06: a 404 that says the model does not exist, at first run.

SR-05: the check said "answered 404: no such address or model. Check the base address", although the reply named the
missing model: the address was right and the model name was the thing to fix.
SR-06: for an address with no path, onboarding then tried the /v1 variant, and that one's plain 404 replaced the reply
that named the model."""
from meshpool_harness.core import endpoint as EP
from meshpool_harness.ui import server as S
from tests.test_onboarding_keys import KEY, mock  # noqa: F401  (fixture)
from tests.test_provider_setup import home  # noqa: F401  (fixture)

NO_MODEL = {"error": {"message": "The model `deepseek-v9-ultra` does not exist or you do not have access to it.",
                      "type": "invalid_request_error", "code": "model_not_found"}}


def test_explain_names_the_missing_model():
    m = EP.explain(404, NO_MODEL, host="127.0.0.1", model="deepseek-v9-ultra")
    assert m.startswith("127.0.0.1 has no model named 'deepseek-v9-ultra' (404).")
    assert "base address" not in m
    # a 404 that is about the address keeps the address advice
    assert "base address" in EP.explain(404, "<html><h1>Not Found</h1></html>", host="h", model="m")
    assert "base address" in EP.explain(404, {"detail": "Not Found"}, host="h", model="m")
    assert EP.missing_model(404, {"error": {"message": "model not found"}}) == ""      # names no model: not sure
    assert EP.missing_model(400, NO_MODEL) == ""


def test_onboarding_says_the_model_is_missing(home, mock):  # noqa: F811
    s = mock(lambda path, body, h: (404, NO_MODEL))
    r = S.onboard({"provider": "custom", "key": KEY, "base_url": s.url + "/v1", "model": "deepseek-v9-ultra"})
    assert not r["ok"]
    assert "no model named 'deepseek-v9-ultra'" in r["detail"] and "base address" not in r["detail"], r


def test_onboarding_keeps_the_reply_that_named_the_model(home, mock):  # noqa: F811
    def fn(path, body, h):
        if path == "/chat/completions":
            return 404, NO_MODEL                          # the right address: the model is wrong
        return 404, "<html><h1>404 Not Found</h1></html>"
    s = mock(fn)
    r = S.onboard({"provider": "custom", "key": KEY, "base_url": f"127.0.0.1:{s.port}", "model": "deepseek-v9-ultra"})
    assert not r["ok"] and "no model named 'deepseek-v9-ultra'" in r["detail"], r
    assert [p for p, _ in s.posts] == ["/chat/completions"]
