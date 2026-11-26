"""Bugs left over from the 0.0.18 ledger ("Found, not fixed"), IDs LQ-nn. Each test failed before its fix."""
import io
import zipfile

import openpyxl
import pytest

from meshpool_harness.agent.office_mcp_server import edit_office_file

_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
_X14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"

_DRAWING = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    '<xdr:twoCellAnchor><xdr:from><xdr:col>3</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>1</xdr:row>'
    '<xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>6</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>5</xdr:row>'
    '<xdr:rowOff>0</xdr:rowOff></xdr:to>'
    '<xdr:sp macro="" textlink=""><xdr:nvSpPr><xdr:cNvPr id="2" name="TextBox 1"/><xdr:cNvSpPr txBox="1"/></xdr:nvSpPr>'
    '<xdr:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
    '<xdr:txBody><a:bodyPr/><a:p><a:r><a:t>Read me first</a:t></a:r></a:p></xdr:txBody></xdr:sp>'
    '<xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>')

# kind -> (parts to add, sheet relationships, workbook relationships, content types, sheet XML before </worksheet>,
#          the words the tool result must use, a zip entry or sheet text whose loss shows the part was dropped)
_KINDS = {
    "shapes": ({"xl/drawings/drawing1.xml": _DRAWING},
               [("rIdLq1", f"{_R}/drawing", "../drawings/drawing1.xml")], [],
               {"/xl/drawings/drawing1.xml": "application/vnd.openxmlformats-officedocument.drawing+xml"},
               '<drawing r:id="rIdLq1"/>', "shapes or text boxes", "Read me first"),
    "form controls": ({"xl/ctrlProps/ctrlProp1.xml": f'<formControlPr xmlns="{_X14}" objectType="CheckBox" checked="Checked"/>'},
                      [("rIdLq2", f"{_R}/ctrlProp", "../ctrlProps/ctrlProp1.xml")], [],
                      {"/xl/ctrlProps/ctrlProp1.xml": "application/vnd.ms-excel.controlproperties+xml"},
                      "", "form controls", "xl/ctrlProps/ctrlProp1.xml"),
    "slicers": ({"xl/slicers/slicer1.xml": f'<slicers xmlns="{_X14}"><slicer name="Region" cache="Slicer_Region" '
                 'caption="Region" rowHeight="241300"/></slicers>'},
                [("rIdLq3", "http://schemas.microsoft.com/office/2007/relationships/slicer", "../slicers/slicer1.xml")], [],
                {"/xl/slicers/slicer1.xml": "application/vnd.ms-excel.slicer+xml"},
                "", "slicers", "xl/slicers/slicer1.xml"),
    "threaded comments": (
        {"xl/threadedComments/threadedComment1.xml":
         '<ThreadedComments xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments">'
         '<threadedComment ref="A1" dT="2026-01-01T00:00:00.00" personId="{00000000-0000-0000-0000-000000000001}" '
         'id="{00000000-0000-0000-0000-000000000002}"><text>Is this right?</text></threadedComment></ThreadedComments>',
         "xl/persons/person.xml":
         '<personList xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments">'
         '<person displayName="A" id="{00000000-0000-0000-0000-000000000001}" providerId="None"/></personList>'},
        [("rIdLq4", "http://schemas.microsoft.com/office/2017/10/relationships/threadedComment",
          "../threadedComments/threadedComment1.xml")],
        [("rIdLq5", "http://schemas.microsoft.com/office/2017/10/relationships/person", "persons/person.xml")],
        {"/xl/threadedComments/threadedComment1.xml": "application/vnd.ms-excel.threadedcomments+xml",
         "/xl/persons/person.xml": "application/vnd.ms-excel.person+xml"},
        "", "threaded comments", "xl/threadedComments/threadedComment1.xml"),
    "data bars": ({}, [], [], {},
                  f'<extLst><ext uri="{{78C0D931-6437-407d-A8EE-F0AAD7539E65}}" xmlns:x14="{_X14}">'
                  '<x14:conditionalFormattings><x14:conditionalFormatting xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main">'
                  '<x14:cfRule type="dataBar" id="{00000000-0000-0000-0000-00000000000A}"><x14:dataBar minLength="0" maxLength="100">'
                  '<x14:cfvo type="autoMin"/><x14:cfvo type="autoMax"/></x14:dataBar></x14:cfRule><xm:sqref>B1:B3</xm:sqref>'
                  '</x14:conditionalFormatting></x14:conditionalFormattings></ext></extLst>',
                  "data bars", "x14:dataBar"),
    "sparklines": ({}, [], [], {},
                   f'<extLst><ext uri="{{05C60535-1F16-4fd2-B633-F4F36F0B64E0}}" xmlns:x14="{_X14}">'
                   '<x14:sparklineGroups xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main"><x14:sparklineGroup>'
                   '<x14:sparklines><x14:sparkline><xm:f>S!A1:B1</xm:f><xm:sqref>C1</xm:sqref></x14:sparkline></x14:sparklines>'
                   '</x14:sparklineGroup></x14:sparklineGroups></ext></extLst>',
                   "sparklines", "x14:sparkline"),
}


def _rels(items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' f'<Relationships xmlns="{_PR}">'
            + "".join(f'<Relationship Id="{i}" Type="{t}" Target="{g}"/>' for i, t, g in items) + "</Relationships>")


def _workbook_with(kind, path):
    """A real workbook (openpyxl writes the base) with one part openpyxl cannot keep, added with zipfile and XML."""
    adds, sheet_rels, book_rels, types, sheet_tail, _, _ = _KINDS[kind]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S"
    for r in range(1, 4):
        ws.append([f"row {r}", r * 10])
    buf = io.BytesIO()
    wb.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in src.namelist():
            data = src.read(name).decode("utf-8")
            if name == "xl/worksheets/sheet1.xml":
                if 'xmlns:r=' not in data.split(">", 2)[1]:
                    data = data.replace("<worksheet ", f'<worksheet xmlns:r="{_R}" ', 1)
                data = data.replace("</worksheet>", sheet_tail + "</worksheet>")
            elif name == "[Content_Types].xml":
                data = data.replace("</Types>", "".join(f'<Override PartName="{k}" ContentType="{v}"/>'
                                                        for k, v in types.items()) + "</Types>")
            elif name == "xl/_rels/workbook.xml.rels" and book_rels:
                data = data.replace("</Relationships>", "".join(
                    f'<Relationship Id="{i}" Type="{t}" Target="{g}"/>' for i, t, g in book_rels) + "</Relationships>")
            z.writestr(name, data)
        if sheet_rels:
            z.writestr("xl/worksheets/_rels/sheet1.xml.rels", _rels(sheet_rels))
        for name, data in adds.items():
            z.writestr(name, data)
    return path


def _has(path, marker):
    with zipfile.ZipFile(path) as z:
        if marker in z.namelist():
            return True
        return any(marker in z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".xml"))


@pytest.mark.parametrize("kind", list(_KINDS))
def test_lq01_an_excel_edit_never_drops_parts_openpyxl_cannot_keep_from_the_users_file(tmp_path, kind):
    """LQ-01: edit_office_file saved .xlsx edits through openpyxl, which silently drops shapes and text boxes, form
    controls, slicers, threaded comments, data bars and sparklines. With in_place=true the user's own file lost them."""
    words, marker = _KINDS[kind][5], _KINDS[kind][6]
    p = _workbook_with(kind, tmp_path / "book.xlsx")
    assert _has(p, marker)
    plain = tmp_path / "plain.xlsx"                       # the loss itself: a bare openpyxl round trip drops the part
    openpyxl.load_workbook(p).save(plain)
    assert not _has(plain, marker), f"openpyxl now keeps {kind}; the guard may be relaxed"
    before = p.read_bytes()

    msg, err = edit_office_file(str(p), [{"op": "set_cell", "sheet": "S", "cell": "C1", "value": 5}], in_place=True)
    assert not err, msg
    assert p.read_bytes() == before, f"the user's workbook was rewritten and lost its {kind}"
    assert words in msg and "in place" in msg, msg
    copy = tmp_path / "book (edited).xlsx"
    assert copy.is_file() and str(copy) in msg, msg
    assert openpyxl.load_workbook(copy)["S"]["C1"].value == 5

    msg2, err2 = edit_office_file(str(p), [{"op": "set_cell", "sheet": "S", "cell": "C2", "value": 6}])
    assert not err2 and words in msg2, msg2           # a copy says what it lacks too

    clean = tmp_path / "clean.xlsx"                     # a workbook with nothing to lose is still changed in place
    openpyxl.load_workbook(p).save(clean)
    msg3, err3 = edit_office_file(str(clean), [{"op": "set_cell", "sheet": "S", "cell": "C1", "value": 7}], in_place=True)
    assert not err3 and msg3.startswith("Changed"), msg3
    assert openpyxl.load_workbook(clean)["S"]["C1"].value == 7


_PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                     "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def _deck_with_tag_and_movie(path, word, slides=1):
    """A deck whose first slide has a text box with a tag part (ppt/tags/tag1.xml) and a movie (ppt/media/media1.mp4),
    the names every such deck uses; with slides=2, the text box also links to the second slide."""
    from lxml import etree
    from pptx import Presentation
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.opc.package import Part
    from pptx.opc.packuri import PackURI
    from pptx.util import Inches
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tb = s.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    tb.text_frame.text = word
    tag = Part.load(PackURI("/ppt/tags/tag1.xml"), "application/vnd.openxmlformats-officedocument.presentationml.tags+xml",
                    prs.part.package, ('<p:tagLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                                       f'<p:tag name="OWNER" val="{word}"/></p:tagLst>').encode())
    rid = s.part.relate_to(tag, RT.TAGS)
    tb._element.nvSpPr.nvPr.append(etree.fromstring(
        '<p:custDataLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        f'xmlns:r="{_R}"><p:tags r:id="{rid}"/></p:custDataLst>'))
    s.shapes.add_movie(io.BytesIO(word.encode() * 50), Inches(1), Inches(3), Inches(2), Inches(2),
                       poster_frame_image=io.BytesIO(_PNG), mime_type="video/mp4")
    if slides == 2:
        last = prs.slides.add_slide(prs.slide_layouts[6])
        last.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)).text_frame.text = word + " end"
        tb.click_action.target_slide = last
    prs.save(path)
    return str(path)


def test_lq02_merged_slides_copy_tags_media_and_slide_links_under_fresh_names(tmp_path):
    """LQ-02: engines/forge/merge.py related the source deck's own tag, media and linked-slide parts to the new
    slides. They kept their names, so ppt/tags/tag1.xml and ppt/media/media1.mp4 were written twice (the target's
    slide then read the source's tag), and a link to another slide pulled in the source's slide, layout and master."""
    import warnings
    from pptx import Presentation
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from engines.forge import merge
    target = _deck_with_tag_and_movie(tmp_path / "target.pptx", "target")
    source = _deck_with_tag_and_movie(tmp_path / "source.pptx", "source", slides=2)
    out = str(tmp_path / "out.pptx")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert merge.append(target, source, out) == 2
    assert not [w for w in caught if "Duplicate name" in str(w.message)], [str(w.message) for w in caught]
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)
        assert sum(n.startswith("ppt/slideLayouts/slideLayout") and n.endswith(".xml") for n in names) == 11
    prs = Presentation(out)
    assert len(prs.slides) == 3

    def owner(slide):
        tag = next(r.target_part for r in slide.part.rels.values() if r.reltype == RT.TAGS)
        return tag.blob.decode().split('val="')[1].split('"')[0]

    def movie(slide):
        return next(r.target_part.blob for r in slide.part.rels.values() if r.reltype == RT.MEDIA)
    assert owner(prs.slides[0]) == "target" and owner(prs.slides[1]) == "source"
    assert movie(prs.slides[0]).startswith(b"target") and movie(prs.slides[1]).startswith(b"source")
    link = next(r.target_part for r in prs.slides[1].part.rels.values() if r.reltype == RT.SLIDE)
    assert link is prs.slides[2].part                 # the copy of the slide it linked to, in this deck


def test_lq03_session_panel_runs_one_git_process_per_refresh_and_still_shows_each_edit(tmp_path, monkeypatch):
    """LQ-03: each Session panel refresh ran 8 git processes (7 for git_status, 1 for diff --numstat), and the 1.5 s
    cache never lasts from one refresh to the next. Now a refresh with nothing changed runs only git status, and
    an edit through meshpool's own tools shows on the next refresh."""
    import os
    import subprocess
    import threading
    import time
    from types import SimpleNamespace

    from meshpool_harness.core.tools import Workspace
    from meshpool_harness.ui import agent_api as A
    from meshpool_harness.ui import workbench as W
    if not W.git_exe():
        pytest.skip("no git")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(W, "_MODELS", {"t": 0, "key": "", "rows": [{"id": "m", "context": 32000}], "error": ""})
    repo = tmp_path / "proj"
    repo.mkdir()

    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\ntwo\n")
    git("add", "-A")
    git("commit", "-qm", "init")
    git("remote", "add", "origin", "https://github.com/o/r.git")
    agent = SimpleNamespace(context=None, messages=[], current_model=lambda: "m", _delegations={})
    A.LIVE["lq3"] = SimpleNamespace(id="lq3", workdir=str(repo), lock=threading.RLock(), events=[], mcp=None,
                                    agent=agent, cp=None, state="idle", scheduled=None, stop=lambda: None)
    monkeypatch.setattr(W, "_GS_TTL", 0)             # refreshes 15 s apart: the short cache has always run out
    W._GS.clear()
    runs = []
    real = subprocess.run

    def counting(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == W.git_exe():
            runs.append(next((c for c in cmd[1:] if not c.startswith("-") and "=" not in c), ""))
        return real(cmd, *a, **k)
    try:
        assert W.session_panel("lq3")["tree"]["files"] == 0
        old = time.time() - 10                        # files written a while ago, as in a real project
        os.utime(repo / "a.txt", (old, old))
        W.session_panel("lq3")
        monkeypatch.setattr(subprocess, "run", counting)
        for _ in range(5):
            d = W.session_panel("lq3")
        assert d["tree"] == {"files": 0, "added": 0, "removed": 0} and d["branch"]["name"] == "main"
        assert d["repo"]["slug"] == "o/r"
        assert len(runs) <= 5, f"{len(runs)} git processes for 5 refreshes with nothing changed: {runs}"

        ws = Workspace(str(repo), str(tmp_path / "run"))
        ws.edit("a.txt", "two\n", "two\nthree\n")     # a clean file changed by the agent's own edit tool
        assert W.session_panel("lq3")["tree"] == {"files": 1, "added": 1, "removed": 0}
        os.utime(repo / "a.txt", (old, old))          # long enough ago that its size and time are trusted
        assert W.session_panel("lq3")["tree"]["added"] == 1
        ws.edit("a.txt", "three\n", "three\nfour\n")  # the same, already changed file again
        assert W.session_panel("lq3")["tree"] == {"files": 1, "added": 2, "removed": 0}
        os.utime(repo / "a.txt", (old, old))
        assert W.session_panel("lq3")["tree"]["added"] == 2
        ws.edit("a.txt", "four\n", "four\nfive\n")    # and again, with the next refresh seconds later
        os.utime(repo / "a.txt", (old + 5, old + 5))
        assert W.session_panel("lq3")["tree"] == {"files": 1, "added": 3, "removed": 0}
        ws.write_file("b.txt", "new\n")
        assert W.session_panel("lq3")["tree"]["files"] == 2
        git("checkout", "-q", "-b", "next")           # outside meshpool: a new branch shows too
        d = W.session_panel("lq3")
        assert d["branch"]["name"] == "next" and "next" in d["branch"]["branches"]
    finally:
        A.LIVE.pop("lq3", None)
