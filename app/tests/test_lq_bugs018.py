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
