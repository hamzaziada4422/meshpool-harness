"""The forge logo cache never serves a cut-short or damaged file (M33-01)."""
import io
import json

import pytest

from engines.forge import logos as L
from meshpool_harness import fsio

SVG = ('<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Visa</title>'
       '<path d="M2 8h20v8H2z"/><path d="M4 4h4v4H4z"/><path d="M10 10h4v4h-4z"/></svg>').encode()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_FETCH_LOGOS", "1")
    monkeypatch.setattr(L, "_MISS", set())
    monkeypatch.setattr(L, "_HEX", {})
    return tmp_path


def _serve(monkeypatch):
    calls = []
    monkeypatch.setattr(L, "_get", lambda u: calls.append(u) or (SVG if "/visa.svg" in u else None))
    return calls


def test_a_truncated_cached_svg_is_not_served_and_is_fetched_again(monkeypatch):
    calls = _serve(monkeypatch)
    f = L.cache_dir() / "visa.svg"
    f.write_bytes(SVG[:100])
    p = L.fetch("Visa")
    assert p == f and p.read_bytes() == SVG and len(calls) == 1


@pytest.mark.parametrize("junk", [b"", b"<html><body>404</body></html>", b"not xml at all", b"\x00\xff\xfe"])
def test_an_empty_or_non_svg_cached_file_is_replaced(monkeypatch, junk):
    calls = _serve(monkeypatch)
    f = L.cache_dir() / "visa.svg"
    f.write_bytes(junk)
    p = L.fetch("Visa")
    assert p == f and p.read_bytes() == SVG and len(calls) == 1


def test_a_good_cached_svg_is_served_without_fetching(monkeypatch):
    calls = _serve(monkeypatch)
    (L.cache_dir() / "visa.svg").write_bytes(SVG)
    assert L.fetch("Visa").read_bytes() == SVG and calls == []


def test_a_truncated_download_is_not_cached(monkeypatch):
    monkeypatch.setattr(L, "_get", lambda u: SVG[:100] if "/visa.svg" in u else None)
    assert L.fetch("Visa") is None
    assert not (L.cache_dir() / "visa.svg").exists()


def test_a_download_over_the_size_cap_is_refused(monkeypatch):
    import urllib.request

    class Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    big = b"<svg xmlns='http://www.w3.org/2000/svg'>" + b"<g/>" * (L.MAX_SVG // 4) + b"</svg>"
    assert len(big) > L.MAX_SVG
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: Resp(big))
    assert L._get("https://example.invalid/x.svg") is None
    assert L.fetch("Visa") is None
    assert not list(L.cache_dir().glob("*.svg"))
    ok = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: Resp(ok))
    assert L._get("https://example.invalid/x.svg") == ok


def test_a_killed_writer_leaves_no_partial_file(monkeypatch):
    _serve(monkeypatch)

    def boom(src, dst):
        raise OSError("killed")
    monkeypatch.setattr(fsio, "_replace", boom)
    p = L.fetch("Visa")
    assert p is None or p.read_bytes() == SVG
    assert not (L.cache_dir() / "visa.svg").exists()
    assert not list(L.cache_dir().glob(".*.tmp"))              # the temporary file is cleaned up too


def test_a_corrupt_simple_icons_json_is_deleted_and_fetched_again(monkeypatch):
    good = json.dumps({"icons": [{"slug": "visa", "hex": "1A1F71"}]}).encode()
    f = L.cache_dir() / "simple-icons.json"
    f.write_bytes(good[:20])
    calls = []
    monkeypatch.setattr(L, "_get_big", lambda u: calls.append(u) or good)
    assert L.brand_hex("visa") == "1A1F71"
    assert len(calls) == 1 and f.read_bytes() == good


def test_a_corrupt_download_of_simple_icons_json_is_not_cached(monkeypatch):
    monkeypatch.setattr(L, "_get_big", lambda u: b'{"icons": [')
    assert L.brand_hex("visa") is None
    assert not (L.cache_dir() / "simple-icons.json").exists()


def test_off_switch_still_wins(monkeypatch):
    calls = _serve(monkeypatch)
    monkeypatch.setenv("MH_FETCH_LOGOS", "0")
    assert L.fetch("Visa") is None and calls == []
