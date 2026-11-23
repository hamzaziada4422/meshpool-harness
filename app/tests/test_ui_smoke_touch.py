"""0.0.13: ui_smoke emulates a touch drag on draggable cards and reports permission-gated APIs (Notification) plainly."""
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

from meshpool_harness import ui_audit as UA  # noqa: E402
from meshpool_harness import ui_smoke as US  # noqa: E402

GOOD = """<!doctype html><html lang=en><head><title>Todo</title><meta name=viewport content="width=device-width"></head><body>
<h1>Todo</h1><input id=t type=text aria-label="Task"><button id=add type=button>Add</button><ul id=l></ul><p id=e>No tasks yet</p>
<script>
const L=document.getElementById('l'),E=document.getElementById('e');let items=[];
try{items=JSON.parse(localStorage.getItem('todo')||'[]')}catch(x){}
function draw(){L.textContent='';items.forEach(i=>{const li=document.createElement('li');li.textContent=i;L.appendChild(li)});E.hidden=items.length>0;localStorage.setItem('todo',JSON.stringify(items))}
function add(){const v=t.value.trim();if(!v)return;items.push(v);t.value='';draw()}
add_=document.getElementById('add');add_.onclick=add;t.addEventListener('keydown',e=>{if(e.key==='Enter')add()});draw();
</script></body></html>"""


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        try:
            UA.launcher(p)().close()
        except Exception:  # noqa: BLE001
            pytest.skip("no Chromium on this computer")
        yield p


def _run(pw, tmp_path, html):
    f = tmp_path / "p.html"
    f.write_text(html, encoding="utf-8")
    return US.smoke(pw, str(f))


def _board(events: str) -> str:
    """Two columns of cards moved by custom drag code. events: 'mouse' (mouse events only) or 'pointer'."""
    ev = {"mouse": ("mousedown", "mousemove", "mouseup"), "pointer": ("pointerdown", "pointermove", "pointerup")}[events]
    return """<!doctype html><html lang=en><head><title>Board</title><meta name=viewport content="width=device-width">
<style>body{font:16px sans-serif}.col{float:left;width:200px;min-height:200px;margin:10px;padding:10px;border:1px solid #888}
.card{padding:12px;margin:6px 0;background:#eef;cursor:grab;touch-action:none}</style></head><body>
<div class=col id=todo><h2>To do</h2><div class=card>Write</div><div class=card>Test</div></div>
<div class=col id=done><h2>Done</h2><div class=card>Plan</div></div>
<script>
let drag=null;
document.addEventListener('%s',e=>{const c=e.target.closest('.card'); if(c) drag=c;});
document.addEventListener('%s',e=>{if(drag) drag.style.opacity='.6';});
document.addEventListener('%s',e=>{ if(!drag) return; drag.style.opacity='1';
  const t=document.elementFromPoint(e.clientX,e.clientY); const col=t&&t.closest('.col'); if(col) col.appendChild(drag); drag=null;});
</script></body></html>""" % ev


def test_a_mouse_only_board_is_found(pw, tmp_path):
    r = _run(pw, tmp_path, _board("mouse"))
    assert any(x["rule"] == "touch-drag" for x in r["findings"]), r["text"]
    assert "touch drag" in r["text"]


def test_a_board_that_drags_with_pointer_events_passes(pw, tmp_path):
    r = _run(pw, tmp_path, _board("pointer"))
    assert not any(x["rule"] == "touch-drag" for x in r["findings"]), r["text"]
    assert "NOTES" not in r["text"] or "Drag" not in r["text"], r["text"]


def test_a_page_with_nothing_to_drag_has_no_drag_finding(pw, tmp_path):
    r = _run(pw, tmp_path, GOOD)
    assert "Drag" not in r["text"] and "No problems found" in r["text"]


NOTIFY_OK = GOOD.replace("<ul id=l>", "<button id=nb type=button onclick=\"Notification.requestPermission().then(p=>{document.getElementById('nb').textContent='Alerts '+p})\">Enable alerts</button><ul id=l>")
NOTIFY_SILENT = GOOD.replace("</script>", "function ding(){ if (Notification.permission==='granted') new Notification('done'); }setTimeout(ding,5000);</script>")


def test_notification_permission_is_reported_not_silent(pw, tmp_path):
    r = _run(pw, tmp_path, NOTIFY_OK)
    assert "needs permission, button present" in r["text"], r["text"]
    assert "No problems found" in r["text"]            # a note, not a defect


def test_notification_code_without_any_ask_is_a_finding(pw, tmp_path):
    r = _run(pw, tmp_path, NOTIFY_SILENT)
    assert any(x["rule"] == "notification-no-ask" for x in r["findings"]), r["text"]
    assert "needs permission" in r["text"]
