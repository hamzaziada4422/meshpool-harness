import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _render(slides, tmp_path):
    from engines.forge import render
    return render.render(slides, str(tmp_path / "d.pptx"), fix_labels=False) if "fix_labels" in render.render.__code__.co_varnames \
        else render.render(slides, str(tmp_path / "d.pptx"))


GOOD = {"archetype": "bar_takeaway", "title": "Fine", "bullets": ["a", "b", "c"]}


def test_empty_table_row_does_not_abort(tmp_path):
    bad = {"archetype": "table_focus", "title": "T", "table": {"columns": ["a", "b", "c"],
           "rows": [["x"], ["x", "y", "z", "w"], [], "str"]}}
    r = _render([bad, GOOD], tmp_path)
    assert (tmp_path / "d.pptx").exists()
    assert r is not None


def test_wrong_typed_fields_never_abort_deck(tmp_path):
    from pptx import Presentation
    bads = [{"archetype": "closing", "title": "C", "steps": 7},
            dict(GOOD, tags=[{"a": 1}]),
            dict(GOOD, tags=5),
            {"archetype": "bar_takeaway", "title": "X", "chart": {"kind": "line", "categories": ["a", "b", "c"],
             "series": [{"name": "s", "values": [1, float("nan"), float("inf")]}]}},
            {"archetype": "benchmark", "title": "B", "companies": 1.5}]
    _render(bads + [GOOD], tmp_path)
    assert len(Presentation(str(tmp_path / "d.pptx")).slides) >= len(bads) + 1


def test_slide_count_other_languages():
    from engines.forge.brief import slide_count as f
    assert f("Erstelle 8 Folien") == 8
    assert f("Crea una presentación de 6 diapositivas") == 6
    assert f("12 diapositives") == 12
    assert f("10页的幻灯片") == 10
    assert f("deck with 100 slides") == 60
    assert f("80-page report into 10 slide deck") == 10


def test_logo_fetch_long_name_never_raises(monkeypatch):
    from engines.forge import logos
    monkeypatch.setenv("MH_FETCH_LOGOS", "1")
    monkeypatch.setattr(logos, "_get", lambda *a, **k: None)
    assert logos.fetch("x" * 300) is None


def test_measure_without_theme(tmp_path):
    import zipfile
    from pptx import Presentation
    from engines.forge.tools import measure_office_file
    p = tmp_path / "a.pptx"
    Presentation().save(str(p))
    q = tmp_path / "b.pptx"
    with zipfile.ZipFile(p) as zi, zipfile.ZipFile(q, "w") as zo:
        for n in zi.namelist():
            if "theme" not in n:
                zo.writestr(n, zi.read(n))
    msg, _ = measure_office_file(str(q))
    assert "StopIteration" not in msg
