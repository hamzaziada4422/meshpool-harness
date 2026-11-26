"""SR-09: edit_office_file could not make a tracked change to text that sat inside an earlier revision (w:ins), nor
to a phrase that ran from plain text into one. On a policy with tracked changes from two reviewers, 9 steps failed
with "not found inside one piece of formatted text". The edit now works there and keeps the earlier revisions."""
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from meshpool_harness.agent import office_mcp_server as O

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def policy(path: Path) -> None:
    """Section 3 of the tester's travel policy: plain text, an insertion by Priya and a deletion by Tom."""
    doc = Document()
    doc.add_paragraph("3. Travel")
    p = doc.add_paragraph("Domestic flights are economy class.")
    p._p.append(parse_xml(f'<w:del {nsdecls("w")} w:id="101" w:author="Tom Becker" w:date="2026-10-20T14:00:00Z">'
                          '<w:r><w:delText xml:space="preserve"> Business class is never allowed.</w:delText></w:r></w:del>'))
    p._p.append(parse_xml(f'<w:ins {nsdecls("w")} w:id="100" w:author="Priya Shah" w:date="2026-10-14T09:30:00Z">'
                          '<w:r><w:t xml:space="preserve"> Trips to sanctioned countries are not allowed.</w:t></w:r></w:ins>'))
    p.add_run(" Receipts are required.")
    doc.save(str(path))


def body(path: Path):
    with zipfile.ZipFile(path) as z:
        from lxml import etree
        return etree.fromstring(z.read("word/document.xml"))


def view(path: Path, accept: bool) -> str:
    """The second paragraph as Word shows it after Accept all (or Reject all)."""
    p = body(path).findall(f".//{W}p")[1]
    out = []
    for el in p.iter(f"{W}t", f"{W}delText"):
        anc = [a.tag for a in el.iterancestors()]
        in_ins, in_del = f"{W}ins" in anc, f"{W}del" in anc
        if accept and not in_del and el.tag == f"{W}t":
            out.append(el.text)
        if not accept and not in_ins and (el.tag == f"{W}t" or in_del):
            out.append(el.text)
    return "".join(out)


def revisions(path: Path):
    return [(e.tag.split("}")[1], e.get(f"{W}author")) for e in body(path).iter(f"{W}ins", f"{W}del")]


def test_edit_inside_an_earlier_insertion_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    policy(tmp_path / "Policy.docx")
    msg, err = O.edit_office_file("Policy.docx", [
        {"op": "tracked_replace", "find": "sanctioned countries", "replace": "restricted countries"}], in_place=True)
    assert not err, msg
    out = tmp_path / "Policy.docx"
    assert view(out, accept=True) == ("Domestic flights are economy class. Trips to restricted countries are not "
                                      "allowed. Receipts are required.")
    assert view(out, accept=False) == "Domestic flights are economy class. Business class is never allowed. Receipts are required."
    revs = revisions(out)
    assert ("del", "Tom Becker") in revs and revs.count(("ins", "Priya Shah")) == 2      # split around the new text
    assert ("del", "Meshpool") in revs and ("ins", "Meshpool") in revs
    ins = [e for e in body(out).iter(f"{W}ins") if e.get(f"{W}author") == "Priya Shah"]
    assert any(d.get(f"{W}author") == "Meshpool" for e in ins for d in e.iter(f"{W}del"))   # deleted inside Priya's


def test_a_phrase_from_plain_text_into_an_insertion(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    policy(tmp_path / "Policy.docx")
    msg, err = O.edit_office_file("Policy.docx", [
        {"op": "tracked_replace", "find": "economy class. Trips to sanctioned countries are not allowed.",
         "replace": "economy class; no trips to sanctioned countries."}], in_place=True)
    assert not err, msg
    out = tmp_path / "Policy.docx"
    assert view(out, accept=True) == ("Domestic flights are economy class; no trips to sanctioned countries. "
                                      "Receipts are required.")
    assert view(out, accept=False) == "Domestic flights are economy class. Business class is never allowed. Receipts are required."
    Document(str(out))                                    # still a document python-docx reads


def test_deleted_text_is_not_matched_and_a_replacement_holding_the_text_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    policy(tmp_path / "Policy.docx")
    msg, err = O.edit_office_file("Policy.docx", [{"op": "tracked_delete", "find": "Business class"}], in_place=True)
    assert err and "not found" in msg
    msg, err = O.edit_office_file("Policy.docx", [
        {"op": "tracked_replace", "find": "Trips", "replace": "Trips abroad"}], in_place=True)
    assert not err and "1 place(s)" in msg, msg
