"""Office pre-answer check: the text of a file the request names, added to a Word document, must be in the document's
paragraphs as the file gives it (OfficeBench ob-1-15-2: 4 of 12 runs either hid it in a content control or rewrote it
in the template's citation style; 0 of the other 235 saved office runs trip the check)."""
import copy

import docx
import pytest

from meshpool_harness.agent import core as C

REF = "Martin Arjovsky, Amar Shah, and Yoshua Bengio. “Unitary Evolution Recurrent Neural Networks”. ICML 2016."
ASK = "Retrieve reference text from reference.txt and add it to the end of word file project_report.docx."
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture(autouse=True)
def _no_cache():
    C._recent_office.__dict__.get("cache", {}).clear()


def _setup(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "reference.txt").write_text(REF)
    d = docx.Document()
    d.add_paragraph("Project report")
    return d, tmp_path / "data" / "project_report.docx"


def test_text_added_as_a_body_paragraph_passes(tmp_path):
    d, out = _setup(tmp_path)
    d.add_paragraph(REF)
    d.save(out)
    assert C._docx_hidden_text_miss(ASK, tmp_path, 0) == ""


def test_text_inside_a_content_control_is_caught(tmp_path):
    from docx.oxml import parse_xml
    d, out = _setup(tmp_path)
    p = d.add_paragraph(REF)
    sdt = parse_xml(f'<w:sdt xmlns:w="{W[1:-1]}"><w:sdtContent/></w:sdt>')
    p._p.addnext(sdt)
    sdt[0].append(copy.deepcopy(p._p))
    p._p.getparent().remove(p._p)
    d.save(out)
    assert "Arjovsky" not in " ".join(q.text for q in docx.Document(out).paragraphs)   # python-docx does not see it
    msg = C._docx_hidden_text_miss(ASK, tmp_path, 0)
    assert "content control" in msg and "project_report.docx" in msg


def test_reworded_text_is_caught_when_asked_to_add_it(tmp_path):
    d, out = _setup(tmp_path)
    d.add_paragraph("Arjovsky, M., Shah, A., & Bengio, Y. (2016). Unitary evolution recurrent neural networks.")
    d.save(out)
    assert "reworded" in C._docx_hidden_text_miss(ASK, tmp_path, 0)


def test_a_summary_or_restyle_request_is_not_held_to_the_files_words(tmp_path):
    d, out = _setup(tmp_path)
    d.add_paragraph("Arjovsky, M., Shah, A., & Bengio, Y. (2016).")
    d.save(out)
    assert C._docx_hidden_text_miss("Summarize reference.txt into project_report.docx", tmp_path, 0) == ""
    assert C._docx_hidden_text_miss("Add reference.txt to project_report.docx in APA style", tmp_path, 0) == ""


def test_untouched_documents_and_requests_without_a_text_file_are_ignored(tmp_path):
    d, out = _setup(tmp_path)
    d.save(out)
    assert C._docx_hidden_text_miss(ASK, tmp_path, 10**10) == ""            # not written this turn
    assert C._docx_hidden_text_miss("Add a title to project_report.docx", tmp_path, 0) == ""
