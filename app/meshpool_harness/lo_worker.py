"""The worker of meshpool's persistent LibreOffice session (lo_session.py). It runs in LibreOffice's own Python (the one
that has the uno module), connects to one LibreOffice started with --accept, and converts files on request: one JSON
line in on stdin, one JSON line out on stdout. It imports nothing from meshpool, so any Python 3 with uno can run it.

Request:  {"id": 1, "src": "/a/b.pptx", "out": "/c/b.pdf", "ext": "pdf", "filter": "", "options": "",
           "infilter": "", "recalc": false}
Reply:    {"id": 1, "ok": true} or {"id": 1, "ok": false, "error": "..."}
The first line it writes is {"ready": true} once it is connected, or {"ready": false, "error": "..."}.
"""
import json
import os
import sys
import time

# (document service, target extension) -> export filter, the choice soffice --convert-to makes
SERVICES = (("com.sun.star.presentation.PresentationDocument", "impress"),
            ("com.sun.star.sheet.SpreadsheetDocument", "calc"),
            ("com.sun.star.text.WebDocument", "writerweb"),
            ("com.sun.star.text.TextDocument", "writer"),
            ("com.sun.star.drawing.DrawingDocument", "draw"))
FILTERS = {
    "writer": {"pdf": "writer_pdf_Export", "docx": "MS Word 2007 XML", "doc": "MS Word 97", "odt": "writer8",
               "rtf": "Rich Text Format", "txt": "Text", "html": "HTML (StarWriter)", "png": "writer_png_Export",
               "jpg": "writer_jpg_Export", "svg": "writer_svg_Export", "epub": "EPUB"},
    "writerweb": {"pdf": "writer_web_pdf_Export", "html": "HTML", "txt": "Text (encoded)"},
    "calc": {"pdf": "calc_pdf_Export", "xlsx": "Calc MS Excel 2007 XML", "xls": "MS Excel 97", "ods": "calc8",
             "csv": "Text - txt - csv (StarCalc)", "html": "HTML (StarCalc)", "png": "calc_png_Export",
             "jpg": "calc_jpg_Export", "svg": "calc_svg_Export"},
    "impress": {"pdf": "impress_pdf_Export", "pptx": "Impress MS PowerPoint 2007 XML", "ppt": "MS PowerPoint 97",
                "odp": "impress8", "png": "impress_png_Export", "jpg": "impress_jpg_Export",
                "svg": "impress_svg_Export", "html": "impress_html_Export"},
    "draw": {"pdf": "draw_pdf_Export", "png": "draw_png_Export", "jpg": "draw_jpg_Export", "svg": "draw_svg_Export",
             "odg": "draw8"},
}


def _say(obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main(argv) -> int:
    pipe = argv[1]
    try:
        import uno
        from com.sun.star.beans import PropertyValue
    except Exception as e:  # noqa: BLE001
        _say({"ready": False, "error": f"no uno module in this Python: {e}"})
        return 2

    def pv(name, value):
        p = PropertyValue()
        p.Name, p.Value = name, value
        return p

    ctx = uno.getComponentContext()
    resolver = ctx.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", ctx)
    rc, last = None, ""
    end = time.time() + float(argv[2]) if len(argv) > 2 else time.time() + 60
    while time.time() < end:
        try:
            rc = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
            break
        except Exception as e:  # noqa: BLE001 - LibreOffice is still starting
            last = str(e)
            time.sleep(0.2)
    if rc is None:
        _say({"ready": False, "error": f"could not reach LibreOffice: {last[:200]}"})
        return 3
    desk = rc.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", rc)
    _say({"ready": True, "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            _say({"ok": False, "error": "not JSON"})
            continue
        rid = req.get("id")
        doc = None
        try:
            load = [pv("Hidden", True), pv("ReadOnly", True), pv("MacroExecutionMode", 0),   # NEVER_EXECUTE
                    pv("UpdateDocMode", 0)]                                                   # NO_UPDATE: no links
            if req.get("infilter"):
                load.append(pv("FilterName", req["infilter"]))
            doc = desk.loadComponentFromURL(uno.systemPathToFileUrl(req["src"]), "_blank", 0, tuple(load))
            if doc is None:
                raise RuntimeError("LibreOffice could not open the file")
            kind = next((k for s, k in SERVICES if doc.supportsService(s)), "")
            flt = req.get("filter") or FILTERS.get(kind, {}).get(req.get("ext", ""), "")
            if not flt:
                raise RuntimeError(f"no export filter for {kind or 'this document'} to {req.get('ext')}")
            if req.get("recalc") and kind == "calc":
                doc.calculateAll()
            store = [pv("FilterName", flt), pv("Overwrite", True)]
            if req.get("options"):
                store.append(pv("FilterOptions", req["options"]))
            doc.storeToURL(uno.systemPathToFileUrl(req["out"]), tuple(store))
            _say({"id": rid, "ok": True, "filter": flt})
        except Exception as e:  # noqa: BLE001 - reported to the caller, which falls back to a one-off run
            _say({"id": rid, "ok": False, "error": str(e)[:400]})
        finally:
            if doc is not None:
                try:
                    doc.close(True)
                except Exception:  # noqa: BLE001
                    try:
                        doc.dispose()
                    except Exception:  # noqa: BLE001
                        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
