"""Reference-guided review (review.py): golden library pictures of the same kind go beside each rendered slide only
when turned on and the knowledge base is set; off by default."""
import io

from PIL import Image

from engines.forge import kb
from engines.forge import review as RV


def _png(tmp_path, name):
    p = tmp_path / name
    Image.new("RGB", (40, 30), "white").save(p)
    return p


def _images(msgs):
    return [p for p in msgs[1]["content"] if p.get("type") == "image_url"]


def test_off_by_default(monkeypatch):
    from engines.forge.validate_pptx import load_rules
    monkeypatch.delenv("MH_REVIEW_REFERENCES", raising=False)
    assert RV.references_on(load_rules()) == (False, 2)
    monkeypatch.setenv("MH_REVIEW_REFERENCES", "1")
    assert RV.references_on(load_rules())[0] is True


def test_no_reference_images_without_the_knowledge_base(monkeypatch, tmp_path):
    monkeypatch.delenv("MESHPOOL_KB", raising=False)
    slides = [{"archetype": "risk_matrix", "title": "Two risks need owners"}]
    msgs = RV.build_messages([(1, _png(tmp_path, "s1.png"))], slides, 1, references=2)
    assert len(_images(msgs)) == 1 and RV.REFERENCE_ASK not in str(msgs)


def test_reference_images_beside_the_slide(monkeypatch, tmp_path):
    thumb = tmp_path / "t.webp"
    Image.new("RGB", (32, 24), "grey").save(thumb)
    monkeypatch.setattr(kb, "available", lambda: True)
    monkeypatch.setattr(kb, "priors", lambda a, k="all": {"categories": ["risk_matrix"]})
    monkeypatch.setattr(kb, "search", lambda **kw: [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    monkeypatch.setattr(kb, "thumbnail", lambda i: thumb)
    slides = [{"archetype": "risk_matrix", "title": "Two risks need owners"}]
    msgs = RV.build_messages([(1, _png(tmp_path, "s1.png"))], slides, 1, references=2)
    assert len(_images(msgs)) == 3 and RV.REFERENCE_ASK in str(msgs)       # the slide and two references
    off = RV.build_messages([(1, _png(tmp_path, "s1.png"))], slides, 1, references=0)
    assert len(_images(off)) == 1


def test_complaints_from_a_reference_review_become_failures():
    rv = {"status": "ok", "slides": [{"slide": 3, "complaints": [
        {"issue": "Labels crowd the top cells", "fix": "Spread items over the cells", "severity": "high", "scope": "layout"},
        {"issue": "The takeaway restates the title", "fix": "Say what to do", "severity": "medium", "scope": "content"}]}]}
    assert RV.as_failures(rv, scopes=("content",)) == {3: ["[review] The takeaway restates the title -> Say what to do (content)"]}
