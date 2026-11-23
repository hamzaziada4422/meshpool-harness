"""The click-through (ui_smoke) finds what a person would hit: buttons that do nothing, forms that take nonsense, mouse-only
controls, lost data, a dead game. It passes a page that works. The grader is given its findings and facts about the code."""
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

from meshpool_harness import ui_audit as UA  # noqa: E402
from meshpool_harness import ui_smoke as US  # noqa: E402
from meshpool_harness.agent import grader as GR  # noqa: E402

APP = Path(__file__).resolve().parents[1]

BAD = """<!doctype html><html lang=en><head><title>x</title><meta name=viewport content="width=device-width"></head><body>
<h1>App</h1><button id=a>Does nothing</button><button onclick="document.getElementById('o').textContent+='+'">Works</button>
<a href="#">Dead</a><div onclick="1" style="cursor:pointer">fake btn</div>
<form onsubmit="event.preventDefault();document.getElementById('o').textContent='Thank you, booked'"><input name=email type=email><button>Book</button></form>
<p id=o></p><script>console.error('boom')</script></body></html>"""

GOOD = """<!doctype html><html lang=en><head><title>Todo</title><meta name=viewport content="width=device-width"></head><body>
<h1>Todo</h1><input id=t type=text aria-label="Task"><button id=add type=button>Add</button><ul id=l></ul><p id=e>No tasks yet</p>
<script>
const L=document.getElementById('l'),E=document.getElementById('e');let items=[];
try{items=JSON.parse(localStorage.getItem('todo')||'[]')}catch(x){}
function draw(){L.textContent='';items.forEach(i=>{const li=document.createElement('li');li.textContent=i;L.appendChild(li)});E.hidden=items.length>0;localStorage.setItem('todo',JSON.stringify(items))}
function add(){const v=t.value.trim();if(!v)return;items.push(v);t.value='';draw()}
add_=document.getElementById('add');add_.onclick=add;t.addEventListener('keydown',e=>{if(e.key==='Enter')add()});draw();
</script></body></html>"""

DEAD_GAME = """<!doctype html><html lang=en><head><title>g</title><meta name=viewport content="width=device-width"></head><body>
<h1>Game</h1><canvas width=100 height=100></canvas><script>document.querySelector('canvas').getContext('2d').fillRect(0,0,10,10)</script></body></html>"""


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


def test_smoke_finds_what_a_person_would_hit(pw, tmp_path):
    r = _run(pw, tmp_path, BAD)
    rules = {x["rule"] for x in r["findings"]}
    assert {"dead-button", "dead-link", "mouse-only", "console-error", "form-accepts-empty"} <= rules, rules
    assert "MUST FIX" in r["text"] and "Does nothing" in r["text"]


def test_smoke_passes_a_page_that_works(pw, tmp_path):
    r = _run(pw, tmp_path, GOOD)
    assert "No problems found" in r["text"], r["text"]


def test_smoke_catches_lost_data_and_an_inert_game(pw, tmp_path):
    lost = GOOD.replace("localStorage.setItem('todo',JSON.stringify(items))", "")
    assert any(x["rule"] == "no-persistence" for x in _run(pw, tmp_path, lost)["findings"])
    assert any(x["rule"] == "game-inert" for x in _run(pw, tmp_path, DEAD_GAME)["findings"])


def test_grader_is_given_facts_and_the_click_through(tmp_path, pw):
    (tmp_path / "index.html").write_text(BAD, encoding="utf-8")
    (tmp_path / "tool.py").write_text("print(1)", encoding="utf-8")
    info = {"root": str(tmp_path), "files": ["index.html", "tool.py"], "requests": ["make a page"], "final": "done"}
    assert "NO test files" in GR.code_facts(info) and "NO README" in GR.code_facts(info)
    assert "dead-link" in str(US.smoke(pw, str(tmp_path / "index.html"))["findings"])
    assert "MUST FIX" in GR.smoke_evidence(info)
    info["facts"] = GR.code_facts(info) + "\nMUST FIX: x"
    ev = GR.evidence(info, [])
    assert "Automatic checks of the result" in ev and "MUST FIX: x" in ev
    assert "BUILT as code" in GR.SYSTEM


def test_the_build_skills_point_at_the_click_through():
    for name in ("build-app", "build-game", "build-dashboard", "build-website", "build-ui"):
        t = (APP / "defaults/skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "ui_smoke" in t and "—" not in t and "–" not in t, name
    t = (APP / "defaults/skills/build-code/SKILL.md").read_text(encoding="utf-8")
    assert "README" in t and "—" not in t and "–" not in t


def test_the_tool_is_offered_and_safe():
    from meshpool_harness.agent import browser_mcp_server as BM
    from meshpool_harness.harnesses.safe_tools import SAFE_MCP
    assert "ui_smoke" in BM.TOOLS and "mcp__browser__ui_smoke" in SAFE_MCP and "ui_smoke" in GR.MCP_LOOK
