"""Logos fetched from the internet (engines/forge/logos.py) and drawn as vector shapes; the cases part."""
from pptx import Presentation

from engines.forge import logos as L

SVG = ('<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>X</title>'
       '<path d="M2 8h20v8H2z"/></svg>').encode()


def test_slugs():
    assert L.slugs("Visa")[0] == "visa"
    assert "goldmansachs" in L.slugs("Goldman Sachs") and "stripe" in L.slugs("Stripe Inc.")
    assert "atandt" in L.slugs("AT&T") and "dotnet" in L.slugs(".NET")


def test_fetch_caches_and_respects_the_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(L, "_get", lambda u: calls.append(u) or (SVG if "/visa.svg" in u else None))
    monkeypatch.setenv("MH_FETCH_LOGOS", "1")
    p = L.fetch("Visa")
    assert p and p.read_bytes() == SVG and len(calls) == 1
    assert L.fetch("Visa") == p and len(calls) == 1             # cached
    assert L.fetch("Northwind Logistics") is None
    monkeypatch.setenv("MH_FETCH_LOGOS", "0")
    assert L.fetch("Visa") is None                              # off: never touches the network or the cache


def test_cases_part_draws_logos_as_vectors_or_names(tmp_path, monkeypatch):
    from engines.forge import render as R
    svg = tmp_path / "visa.svg"
    svg.write_bytes(SVG)
    monkeypatch.setattr(L, "fetch", lambda name: svg if name == "Visa" else None)
    s = {"archetype": "cases", "id": "s", "node": "n", "title": "Two firms show the model already works",
         "rows": [{"company": "Visa", "value": "80%", "text": "of disputes triaged automatically"},
                  {"company": "Northwind", "value": "$12M", "text": "saved on freight booking"}],
         "source": {"text": "Source: synthetic"}}
    out = tmp_path / "c.pptx"
    r = R.render([s], str(out), carry=False, fix_labels=False)
    assert r["errors"] == []
    sl = Presentation(str(out)).slides[0]
    names = {sh.name for sh in sl.shapes}
    assert "ov_CaseLogo1_1" in names and "ov_CaseName1_2" in names
    assert not any(sh.shape_type == 13 for sh in sl.shapes)       # zero pictures
    assert "Northwind" in " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)
