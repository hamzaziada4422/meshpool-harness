import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def home(tmp_path, monkeypatch):
    from meshpool_harness import config as C
    monkeypatch.setattr(C, "home", lambda: tmp_path)
    (tmp_path / "keep.txt").write_text("x")
    return tmp_path


@pytest.mark.parametrize("bad", ["..", ".", "", "../x", "a/b", "/", "ABCDEF0123", "abc", "..\\x"])
def test_brand_delete_rejects_unsafe_ids(home, bad):
    from engines.forge import brand as B
    (home / "brand").mkdir(exist_ok=True)
    B.delete(bad)
    assert (home / "keep.txt").exists() and (home / "brand").exists()


def test_brand_delete_removes_own_folder(home):
    from engines.forge import brand as B
    d = home / "brand" / "0123456789"
    d.mkdir(parents=True)
    (d / "logo.png").write_bytes(b"x")
    B.delete("0123456789")
    assert not d.exists() and (home / "keep.txt").exists()


def test_brand_save_rejects_path_id(home):
    from engines.forge import brand as B
    with pytest.raises(ValueError):
        B.save({"id": "../..", "name": "x"})


def test_set_logo_unknown_id(home):
    from engines.forge import brand as B
    with pytest.raises(KeyError):
        B.set_logo("..", "a.png", b"x")


def test_today_line_has_no_posix_only_format():
    from engines.forge.build import today_line
    assert "Today is" in today_line()
    src = (Path(__file__).resolve().parents[1] / "engines/forge/build.py").read_text(encoding="utf-8")
    assert "%-d" not in src


def test_validate_pptx_parses_on_py310():
    import ast
    p = Path(__file__).resolve().parents[1] / "engines/forge/validate_pptx.py"
    ast.parse(p.read_text(encoding="utf-8"), feature_version=(3, 10))


def test_slide_count_prefers_slides_over_source_pages():
    from engines.forge.brief import slide_count
    assert slide_count("Turn the attached 80-page report into a 10 slide deck") == 10
    assert slide_count("Summarise our 45 page contract in 6 slides") == 6
    assert slide_count("a 6-page deck") == 6
    assert slide_count("make 99 slides") == 60
    assert slide_count("no number") is None


def test_insert_position_needs_slide_word():
    from engines.forge.engine import insert_position
    for t in ("add a summary as one slide", "add 2 slides after 30 days of data", "put it at first glance",
              "add a slide about retention at 5 pm"):
        assert insert_position(t) is None, t
    assert insert_position("add a slide after slide 4") == 5
    assert insert_position("insert as slide 7") == 7


def test_vault_layout_notes_have_use_when():
    from engines.forge import vault
    hits = [h for h in vault.search("pilot group improved between two dates", limit=10) if "layouts" in h["note"]]
    assert hits and all(h["use_when"] for h in hits)
    assert not any(len(h["name"]) == 6 and h["name"].isalnum() and h["name"].islower() and h["name"].isalnum()
                   and h["name"] == Path(h["note"]).stem for h in hits)


def test_sidecar_ignores_unrelated_folder_json(tmp_path):
    from engines.forge.tools import _sidecar
    (tmp_path / "brief.json").write_text('{"slide_count": 3}')
    assert _sidecar(tmp_path / "deck.pptx", "brief") is None
    (tmp_path / "deck.brief.json").write_text("{bad")
    assert _sidecar(tmp_path / "deck.pptx", "brief") is None
    (tmp_path / "deck.brief.json").write_text('{"slide_count": 4}')
    assert _sidecar(tmp_path / "deck.pptx", "brief") == {"slide_count": 4}


def test_corrupt_zip_gives_readable_result(tmp_path):
    from engines.forge.tools import check_document, measure_office_file
    p = tmp_path / "bad.pptx"
    p.write_bytes(b"not a zip")
    msg, err = check_document(str(p))
    assert "BadZipFile" not in msg and "bad.pptx" in msg
    msg, err = measure_office_file(str(p))
    assert err and "BadZipFile" not in msg


def test_null_title_keeps_slide_content(tmp_path, monkeypatch):
    """PA-forge-09: a slide whose title is null (JSON null from the content model) renders with an empty title and
    keeps its content. Before the fix the title setter raised a TypeError, the layout and its fallback both failed,
    and the slide came out blank."""
    pytest.importorskip("pptx")
    from pptx import Presentation
    from engines.forge import common as FC
    from engines.forge import render as R
    monkeypatch.setattr(FC, "find_soffice", lambda: None)           # no LibreOffice label pass: the render only
    out = tmp_path / "null_title.pptx"
    res = R.render([{"archetype": "cover", "title": "Deck"},
                    {"archetype": "argument_only", "title": None,
                     "blocks": [{"text": "KEEPME alpha", "bullets": [{"text": "KEEPME beta"}]}]}],
                   str(out), fix_labels=False)
    errs = " ".join(str(e) for e in (res.get("errors") or []))
    assert "TypeError" not in errs and "NoneType" not in errs, errs
    assert not res.get("errors"), res.get("errors")
    p = Presentation(str(out))
    assert len(p.slides) == 2
    second = " ".join(sh.text_frame.text for sh in p.slides[1].shapes if sh.has_text_frame)
    assert "KEEPME alpha" in second and "KEEPME beta" in second
