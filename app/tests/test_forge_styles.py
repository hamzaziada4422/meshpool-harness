"""Style packs (engines/forge/styles.py): contrast rules, a full deck in each, the validator, the CLI, Settings > Company."""
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

from engines.forge import styles
from engines.forge.brand import check_roles, contrast
from engines.forge.render import render
from engines.forge.template import FONT_PAIRS
from engines.forge.validate_pptx import validate

APP = Path(__file__).resolve().parents[1]
SLIDES = APP / "tests" / "fixtures" / "forge_kb_briefs" / "01_market_entry" / "slides.json"


def test_pack_count_and_shape():
    assert 6 <= len(styles.names()) <= 8
    for n in styles.names():
        s = styles.get(n)
        assert s["fonts"] in FONT_PAIRS and s["label"] and s["description"] and s["evidence"]["decks"] > 0
        assert len(s["palette"]) == 12


@pytest.mark.parametrize("name", styles.names())
def test_roles_pass(name):
    assert check_roles(styles.get(name)["palette"]) == []
    assert contrast(styles.get(name)["palette"]["accent4"], "FFFFFF") >= 3.0      # white data labels sit on it
    assert styles.render_kwargs(name) == {"theme": styles.theme(name)}


@pytest.mark.parametrize("name", styles.names())
def test_full_deck_validates(name, tmp_path):
    slides = json.loads(SLIDES.read_text())
    out = tmp_path / "deck.pptx"
    r = render(slides, str(out), theme=styles.theme(name), carry=False, density="balanced", fix_labels=False)
    assert not r.get("errors")
    res = validate(str(out), render=False)
    for rule in ("P4", "P6", "P19"):
        assert res["rules"][rule] == "pass", (name, rule, [f for f in res["failures"] if f["rule"] == rule])


def test_unknown_style():
    with pytest.raises(KeyError):
        styles.theme("nope")
    p = subprocess.run([sys.executable, "-m", "engines.forge.build", "b.md", "out", "--style", "nope"], cwd=APP,
                       capture_output=True, text=True)
    assert p.returncode != 0 and "unknown style" in p.stderr and "teal-modern" in p.stderr


def test_company_page_style(tmp_path, monkeypatch):
    from meshpool_harness import config as C
    from meshpool_harness.ui import company_api as A
    monkeypatch.setattr(C, "home", lambda: tmp_path)
    st = A.state()
    assert st["style"] is None and [s["name"] for s in st["styles"]] == styles.names()
    code, out = A.handle("POST", urlparse("/api/company/style"), json.dumps({"name": "deep-green"}).encode())
    assert code == 200 and out["style"] == "deep-green"
    assert A.state()["style"] == "deep-green"
    with pytest.raises(ValueError):
        A.handle("POST", urlparse("/api/company/style"), json.dumps({"name": "nope"}).encode())
    assert A.handle("POST", urlparse("/api/company/style"), json.dumps({"name": None}).encode())[1]["style"] is None


def test_settings_style_reaches_builds(tmp_path, monkeypatch):
    """The style picked in Settings > Company is used for decks when no company look or request colour is set."""
    from engines.forge import brand as B
    from engines.forge import engine as E
    from engines.forge import styles as ST
    name = ST.names()[1]
    monkeypatch.setattr(B, "for_request", lambda task: None)
    monkeypatch.setattr(B, "_index", lambda: {"style": name})
    look, prof = E.brand_from("Make a board deck on the pricing review")
    assert prof is None and look["theme"] == ST.theme(name)
    look, _ = E.brand_from("Make a board deck, ocean theme")       # a colour word in the request still wins
    assert look["theme"] != ST.theme(name)
    monkeypatch.setattr(B, "_index", lambda: {"style": "no-such-style"})
    assert E.brand_from("Make a board deck")[0]["theme"] is None
