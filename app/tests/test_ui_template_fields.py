"""The audit catches bracketed template fields left on a page ([Studio name], [street address]) and leaves footnotes alone."""
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

from meshpool_harness import ui_audit as UA  # noqa: E402

HEAD = '<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width"><title>t</title></head>'


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        try:
            UA.launcher(p)().close()
        except Exception:  # noqa: BLE001
            pytest.skip("no Chromium on this computer")
        yield p


def test_template_fields_are_filler_but_footnotes_are_not(pw, tmp_path):
    f = tmp_path / "t.html"
    f.write_text(HEAD + "<body><main><h1>[Studio name]</h1><p>Find us at [street address], [city, postcode].</p></main></body></html>",
                 encoding="utf-8")
    r = UA.audit(pw, str(f), views=("desktop",), schemes=("light",))
    assert any(x["rule"] == "left-over-text" and "Studio name" in x["msg"] for x in r["findings"])
    ok = tmp_path / "ok.html"
    ok.write_text(HEAD + "<body><main><h1>Notes</h1><p>As shown in the study [1] and [2].</p></main></body></html>", encoding="utf-8")
    r = UA.audit(pw, str(ok), views=("desktop",), schemes=("light",))
    assert not any(x["rule"] == "left-over-text" for x in r["findings"])
