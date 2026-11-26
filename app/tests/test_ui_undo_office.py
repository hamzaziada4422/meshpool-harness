"""Undo of an office edit, clicked in a real browser against the real app server, the real office MCP server and the
mock model (testkit/mock_llm.py: "raise all prices by 10%" is one edit_office_file set_range, in place). Maya's
Flowers: Undo said "No files in this chat's folder had changed. Not restored: Bouquet Price List.xlsx (changed
since...)" and the file kept the new prices, because meshpool wrote the formula results into the workbook at the end of
the turn (xl_values.fill) outside the turn's checkpoint. Needs LibreOffice for that write (MH_ALLOW_SYSTEM_SOFFICE=1
in a checkout)."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
openpyxl = pytest.importorskip("openpyxl")

REPO = Path(__file__).resolve().parents[2]
BOOK = "Bouquet Price List.xlsx"
PRICES = [69, 42, 48, 150, 65, 38, 55, 72, 88, 135, 28, 59]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, secs: int = 60) -> str:
    end = time.time() + secs
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    pytest.fail(f"nothing answered at {url} within {secs} s")


def _book(path: Path) -> None:
    """The price list as an engine leaves it: prices on Inputs, formulas on Outputs and Checks."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inputs"
    for i, v in enumerate(PRICES):
        ws[f"A{4 + i}"], ws[f"C{4 + i}"] = f"Bouquet {i + 1}", v
    out = wb.create_sheet("Outputs")
    for i in range(12):
        out[f"C{4 + i}"] = f"=Inputs!C{4 + i}*1"
    chk = wb.create_sheet("Checks")
    chk["B4"], chk["B5"] = "=MIN(Inputs!C4:C15)", "=MAX(Inputs!C4:C15)"
    wb.save(path)


def _prices(path: Path) -> list:
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        return [wb["Inputs"][f"C{r}"].value for r in range(4, 16)]
    finally:
        wb.close()


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from meshpool_harness import office_runtime
    if not office_runtime.find_soffice():
        pytest.skip("no LibreOffice here: the end-of-turn value write this test is about needs it")
    tmp = tmp_path_factory.mktemp("undooffice")
    home, mh, proj = tmp / "home", tmp / "mh", tmp / "Maya's Flowers"
    for d in (home, mh, proj):
        d.mkdir()
    _book(proj / BOOK)
    mport, uport = _port(), _port()
    (mh / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_ALLOW_SYSTEM_SOFFICE="1")
    env.pop("MESHPOOL_CONFIG", None)
    env["MH_SLOTS_DIR"] = str(tmp / "slots")
    procs = [subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport), str(tmp / "mock.jsonl")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    log = open(tmp / "ui.log", "w")
    procs.append(subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                                  cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT))
    base = f"http://127.0.0.1:{uport}"
    try:
        tok = re.search(r'token: "([^"]+)"', _wait(base + "/")).group(1)
        req = urllib.request.Request(base + "/api/projects", data=json.dumps({"action": "add", "path": str(proj)}).encode(),
                                     headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        yield {"base": base, "proj": str(proj), "log": tmp / "ui.log", "mock": tmp / "mock.jsonl"}
    finally:
        for p in procs:
            p.kill()
        log.close()


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("MH_TEST_CHROMIUM")
        try:
            b = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as e:  # noqa: BLE001
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium for Playwright here: {e}")
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
        pg.errors = errors
        yield pg
        b.close()


def test_undo_of_an_office_edit_puts_the_prices_back_and_says_so(page, app):
    proj = Path(app["proj"])
    before = (proj / BOOK).read_bytes()
    page.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["proj"])
    page.fill("#input", "Please raise all prices by 10% in my bouquet spreadsheet.")
    page.press("#input", "Enter")
    try:
        page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.state === 'idle' && "
                               "document.querySelectorAll('#col .msg-user').length === 1 && "
                               "document.querySelector('#working') === null && "
                               "document.querySelectorAll('#col .turnfoot [data-undo]').length === 1", timeout=180000)
    except Exception:  # noqa: BLE001
        pytest.fail("the chat did not finish\n" + app["log"].read_text(errors="replace")[-3000:])
    assert _prices(proj / BOOK) == [round(p * 1.1, 2) for p in PRICES]
    wb = openpyxl.load_workbook(proj / BOOK, data_only=True)
    assert wb["Checks"]["B5"].value == 165, "meshpool wrote the formula results in at the end of the turn"
    wb.close()
    assert sorted(x.name for x in proj.iterdir() if not x.name.startswith(".")) == [BOOK]   # no "(edited)" copy

    page.locator("#col .turnfoot [data-undo]").first.click()
    page.click(".dlg [data-a='1']")
    page.wait_for_function("() => [...document.querySelectorAll('#col .note')].some(n => /^Undone\\./.test(n.innerText))",
                           timeout=60000)
    line = page.evaluate("[...document.querySelectorAll('#col .note')].map(n => n.innerText)"
                         ".filter(t => /^Undone\\./.test(t)).pop()")
    assert line.startswith(f"Undone. Restored 1 file ({BOOK})."), line
    assert "had changed" not in line and "Not restored" not in line and "Kept" not in line, line
    assert (proj / BOOK).read_bytes() == before
    assert _prices(proj / BOOK) == PRICES
    assert page.errors == [], page.errors
