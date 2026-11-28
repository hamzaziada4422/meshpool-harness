"""Platform audit, pass 2: a regression test for each finding fixed on main without a test of its original trigger.

Each test is named after its row (P2-<area>-<n>) and was checked to fail with the fix reverted."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent


# ---------- P2-core-18: diagnostics with spaces in paths, E902 duplicates, TS18003 ------------------------------------
def _ws(root):
    from meshpool_harness.core.tools import Workspace
    return Workspace(str(root), str(root / ".run"))


def test_p2_core_18_diagnostics_paths_with_spaces_e902_and_ts18003(tmp_path):
    from meshpool_harness.hrouter import packs
    (tmp_path / "a b.py").write_text("import os\n\ndef f(x)\n    return x\n")
    out = packs.diagnostics(_ws(tmp_path), ["a b.py"])
    assert "a b.py:3:" in out and "expected ':'" in out and "Unparsed output" not in out, out
    (tmp_path / "a b.py").write_text("print(y)\n")
    if packs._tool(_ws(tmp_path), "ruff"):
        out = packs.diagnostics(_ws(tmp_path), ["a b.py"])
        assert "a b.py:1:7  F821" in out and "Unparsed output" not in out, out
    # a file Ruff cannot read (E902) that the compiler also reports: one problem, not a second E902 line for it
    (tmp_path / "bad.py").write_bytes(b'x = "\xff\xfe"\n')
    out = packs.diagnostics(_ws(tmp_path), ["bad.py"])
    assert "bad.py:1:" in out and "E902" not in out and "1 problem(s)" in out, out
    if not shutil.which("tsc"):
        return                                                # TS18003 needs the TypeScript compiler
    ts = tmp_path / "ts"
    ts.mkdir()
    (ts / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}\n')
    out = packs.diagnostics(_ws(ts), [], language="typescript")
    assert "Checks run: tsc" in out and "No problems found" in out and "TS18003" not in out, out


# ---------- P2-engines-15: CJK and accented names stay in output file names ------------------------------------------
JP = "株式会社テスト"
CA_VALUES = {"revenue_base": 1000, "rev_growth": 0.05, "ocf_margin": 0.2, "capex_base": 50, "capex_growth": 0.03,
             "cash": 300, "min_cash": 100, "shares": 100, "share_price": 25, "dps": 0.5, "dps_growth": 0.05,
             "special_dps": 0, "buyback": 20, "cash_yield": 0.03, "down_growth": -0.02, "down_margin_cut": 0.02,
             "capex_overrun": 0.1}


class _InputsClient:
    def __init__(self, company, values):
        self.reply = json.dumps({"company": company, "first_year": 2027, "years": 5,
                                 "inputs": {k: {"value": v, "source": "test"} for k, v in values.items()}})

    def chat(self, **kw):
        return {"choices": [{"message": {"content": self.reply}}]}


def test_p2_engines_15_cjk_and_accented_names_stay_in_file_names(tmp_path):
    from engines.sheets import pipeline

    class WS:
        root = tmp_path / "sheets"
    WS.root.mkdir()
    out = pipeline.build("Capital allocation model", WS, _InputsClient(JP, CA_VALUES), "mock")
    assert Path(out["path"]).name == f"{JP}_capital_allocation_model.xlsx" and Path(out["path"]).exists()
    out = pipeline.build("Capital allocation model", WS, _InputsClient("Société Générale", CA_VALUES), "mock")
    assert Path(out["path"]).name == "Société_Générale_capital_allocation_model.xlsx"

    from engines.credit.engine import solve
    csv = tmp_path / f"{JP}.csv"                          # the borrower is named after the file
    csv.write_text("USD millions,FY2024,FY2025\nNet sales,100,110\nCost of goods sold,(60),(66)\n"
                   "Operating income,15,17\nDepreciation,5,6\nInterest expense,(2),(2.5)\nIncome taxes,(3),(3.4)\n"
                   "Net income,10,11.1\nCash from operating activities,14,12\nCash,8,6\nTotal assets,80,87\n"
                   "Long-term debt,20,16\n", encoding="utf-8")
    r = solve("Credit memo for a $10m 5-year term loan at 8%", workdir=str(tmp_path / "credit"), task_id="t",
              files=[str(csv)], client=None)
    assert r["ok"], r
    names = sorted(Path(a).name for a in r["artifacts"])
    assert f"credit_{JP}.xlsx" in names and f"credit_memo_{JP}.md" in names, names


# ---------- P2-engines-16: capital allocation with a share price of 0 --------------------------------------------------
def test_p2_engines_16_zero_share_price_gives_no_div0(tmp_path, monkeypatch):
    from engines.sheets import builder as B
    wb = B.new_workbook(("Inputs", "Model"))
    book = B.assumption_book(wb["Inputs"])
    fmts = {"rev_growth": "pct", "ocf_margin": "pct", "capex_growth": "pct", "dps_growth": "pct", "cash_yield": "pct"}
    for name in ("revenue_base", "rev_growth", "ocf_margin", "capex_base", "capex_growth", "cash", "min_cash", "shares",
                 "share_price", "dps", "dps_growth", "special_dps", "buyback", "cash_yield"):
        book.add(name, 0 if name == "share_price" else CA_VALUES[name], fmts.get(name, "usd"), name=name, source="test")
    B.capital_allocation(wb["Model"], 1, [2027, 2028, 2029])
    p = tmp_path / "ca0.xlsx"
    wb.save(p)
    from engines.forge.common import find_soffice
    if find_soffice():
        from engines.sheets.qa import recalc_values
        wv = recalc_values(str(p))
        errs = [(c.coordinate, c.value) for row in wv["Model"].iter_rows() for c in row
                if isinstance(c.value, str) and c.value.startswith("#")]
        assert errs == [], errs
        retired = next(r for r in range(1, wv["Model"].max_row + 1) if wv["Model"].cell(r, 1).value == "Shares retired")
        assert [wv["Model"].cell(retired, c).value for c in (2, 3, 4)] == [0, 0, 0]
    else:                                                     # no LibreOffice: the guard is in the formula text
        ws = wb["Model"]
        retired = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == "Shares retired")
        for c in (2, 3, 4):
            f = ws.cell(retired, c).value
            assert f.startswith("=IF(share_price=0,0,") and f.endswith("/-share_price)"), f


# ---------- P2-engines-17: review digest and QA scan at most MAX_SCAN_ROWS rows --------------------------------------
def test_p2_engines_17_big_sheets_are_sampled_not_scanned(monkeypatch):
    from openpyxl import Workbook
    from openpyxl.worksheet.worksheet import Worksheet

    from engines.sheets import qa
    from engines.sheets import review_digest as RD
    n = 100_000
    wb = Workbook()
    big = wb.active
    big.title = "Checks"
    inp = wb.create_sheet("Inputs")
    for r in range(1, n + 1):
        big.append([r, r])
        inp.append([r, r])
    seen = {"max": 0}
    orig = Worksheet.cell

    def spy(self, row, column=None, value=None):
        seen["max"] = max(seen["max"], row)
        return orig(self, row, column, value)
    monkeypatch.setattr(Worksheet, "cell", spy)
    t0 = time.time()
    for fn in (lambda: RD.checks_section(wb, wb), lambda: RD.errors_section(wb, wb),
               lambda: RD.assumptions_section(wb, wb, {}), lambda: RD.literals_section(wb),
               lambda: RD.formulas_section(wb, wb)):
        seen["max"] = 0
        fn()
        assert seen["max"] <= RD.MAX_SCAN_ROWS, seen["max"]
    monkeypatch.setattr(qa, "load_workbook", lambda *a, **k: wb)
    monkeypatch.setattr(qa, "recalc_values", lambda path: wb)
    seen["max"] = 0
    qa.check("big.xlsx")
    assert seen["max"] <= qa.MAX_SCAN_ROWS, seen["max"]
    assert big.max_row == n and inp.max_row == n               # reading did not grow the sheets
    assert time.time() - t0 < 20


# ---------- P2-forge-8: finance_workbook reads read-only and closes the file -----------------------------------------
def test_p2_forge_8_finance_workbook_closes_the_file(tmp_path, monkeypatch):
    from openpyxl import Workbook
    from openpyxl.workbook.defined_name import DefinedName

    from engines.forge import finance_pages as FP
    wb = Workbook()
    wb.active["A1"] = 12.5
    dn = DefinedName("dcf_per_share", attr_text="Sheet!$A$1")
    try:
        wb.defined_names["dcf_per_share"] = dn
    except TypeError:
        wb.defined_names.append(dn)
    p = tmp_path / "val.xlsx"
    wb.save(p)
    opened = []
    real = FP.load_workbook

    def spy(*a, **k):
        w = real(*a, **k)
        opened.append((k, w))
        return w
    monkeypatch.setattr(FP, "load_workbook", spy)
    assert FP.finance_workbook([str(tmp_path / "none.xlsx"), str(p)]) == str(p)
    assert len(opened) == 1
    kw, w = opened[0]
    assert kw.get("read_only") is True                        # streams the file instead of loading every cell
    assert w._archive.fp is None                               # and the zip handle is closed afterwards


# ---------- P2-forge-9: golden.jsonl is written as UTF-8 whatever the locale -----------------------------------------
from test_forge_kb import fake_kb  # noqa: E402,F401 (the tiny synthetic knowledge base)


def test_p2_forge_9_golden_jsonl_is_utf8_under_a_cp1252_locale(fake_kb, monkeypatch):  # noqa: F811
    import builtins

    from engines.forge import kb
    used = {}

    def cp1252_open(file, mode="r", *a, **k):                   # what open() does on a Windows cp1252 locale
        if "b" not in mode and "encoding" not in k and len(a) < 2:
            k["encoding"] = "cp1252"
        if str(file).endswith("golden.jsonl"):
            used["encoding"] = k.get("encoding")
        return builtins.open(file, mode, *a, **k)
    monkeypatch.setattr(kb, "open", cp1252_open, raising=False)
    kb.score_all()
    kb.import_labels([{"id": "c1", "category": "chart", "quality": 5, "usable": True, "desc": "Umsatz – Übersicht ✓"},
                      {"id": "p1", "category": "process_flow", "quality": 5, "usable": True,
                       "desc": "株式会社テストの工程"}])
    gold = kb.build_golden(min_quality=4)
    assert gold
    raw = (fake_kb / "library" / "golden.jsonl").read_bytes()
    rows = [json.loads(ln) for ln in raw.decode("utf-8").splitlines()]
    assert {r["descr"] for r in rows} >= {"Umsatz – Übersicht ✓", "株式会社テストの工程"}
    # json.dumps escapes non-ASCII, so the bytes are ASCII either way; the file is opened as UTF-8 regardless of locale
    assert (used.get("encoding") or "").lower().replace("-", "") == "utf8", used


# ---------- P2-packaging-4: `meshpool check` says the terminal install has no LibreOffice -----------------------------
def test_p2_packaging_4_check_says_terminal_install_has_no_libreoffice(tmp_path, monkeypatch, capsys):
    from meshpool_harness import cli, office_runtime
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    monkeypatch.delenv("MH_SOFFICE", raising=False)
    monkeypatch.delenv("MH_ALLOW_SYSTEM_SOFFICE", raising=False)
    monkeypatch.setattr(office_runtime, "is_packaged", lambda: False)
    monkeypatch.setattr(office_runtime, "describe", lambda: {"path": None, "note": "not found"})
    cli._check(False)
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "LibreOffice" in ln and "[" in ln)
    assert "[--]" in line and "not included in this terminal install" in line, line
    assert "desktop app" in line, line


# ---------- P2-packaging-7: repack.sh checks the same six Apple secrets sign.sh needs --------------------------------
def test_p2_packaging_7_repack_checks_every_apple_secret():
    sign = (REPO / "packaging" / "mac" / "sign.sh").read_text(encoding="utf-8")
    repack = (REPO / "packaging" / "mac" / "repack.sh").read_text(encoding="utf-8")
    need = re.search(r"^need=\(([^)]*)\)", sign, re.MULTILINE).group(1).split()
    assert len(need) == 6, need
    line = next(ln for ln in repack.splitlines() if ln.strip().startswith("if [ -z") and "codesign" not in ln)
    checked = re.findall(r'-z "\$\{(\w+):-\}"', line)
    assert sorted(checked) == sorted(need), (checked, need)


# ---------- P2-skills-6: the test workflow installs pymupdf -----------------------------------------------------------
def test_p2_skills_6_test_workflow_installs_pymupdf():
    wf = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    installs = [ln.split("#")[0] for ln in wf.splitlines() if re.search(r"\bpip install\b", ln)]
    assert any(re.search(r"\bpymupdf\b", ln) for ln in installs), installs


# ---------- P2-toplevel-11: the update-check error names the rate limit or the status code ---------------------------
def test_p2_toplevel_11_update_check_names_rate_limit_or_status(monkeypatch):
    import email.message
    import urllib.error

    from meshpool_harness import updater as U

    def answer(code, msg):
        def fake(url, *a, **k):
            raise urllib.error.HTTPError(url, code, msg, email.message.Message(), None)
        return fake
    monkeypatch.setattr(U, "_get", answer(403, "rate limit exceeded"))
    r = U.check(force=True)
    assert not r["ok"] and "rate limit" in r["error"], r
    monkeypatch.setattr(U, "_get", answer(502, "Bad Gateway"))
    r = U.check(force=True)
    assert not r["ok"] and "502" in r["error"], r


# ---------- P2-uiserver-12: a non-zip upload is a 400 with a plain message -------------------------------------------
def test_p2_uiserver_12_non_zip_skill_upload_is_a_400(tmp_path, monkeypatch):
    from meshpool_harness.ui import server as SV
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    httpd = SV.ThreadingHTTPServer(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        body = json.dumps({"action": "upload", "files": [{"name": "my-skill.zip",
                                                          "data": base64.b64encode(b"this is not a zip").decode()}]})
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/skills", data=body.encode(),
                                     method="POST", headers={"X-Meshpool-Token": SV.TOKEN,
                                                             "Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=10)
        assert ei.value.code == 400
        assert json.loads(ei.value.read())["error"] == "That file is not a valid zip file."
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------- browser tests (P2-uifront-12, P2-uifront-15), in the style of test_fix_uifront_pass2.py ------------------
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


@pytest.fixture()
def ui_app(tmp_path):
    """The real app server in its own process, so a test can stop it."""
    home, mh = tmp_path / "home", tmp_path / "mh"
    for d in (home, mh):
        d.mkdir()
    uport = _port()
    (mh / "meshpool.config").write_text("MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:9/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(APP), PYTHONUTF8="1", MH_CLARITY="0")
    env.pop("MESHPOOL_CONFIG", None)
    env["MH_SLOTS_DIR"] = str(tmp_path / "slots")
    log = open(tmp_path / "ui.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                         cwd=str(APP), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{uport}"
    try:
        _wait(base + "/")
        yield {"base": base, "proc": p}
    finally:
        p.kill()
        p.wait(10)
        log.close()


@pytest.fixture()
def page(ui_app):
    sync_api = pytest.importorskip("playwright.sync_api")
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
        pg.goto(ui_app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined", timeout=30000)
        pg.errors = errors
        yield pg
        b.close()


def test_p2_uifront_12_consolidate_toast_points_to_history(page):
    page.route("**/api/memory", lambda r: r.fulfill(status=200, content_type="application/json", body='{"archived": 2}')
               if r.request.method == "POST" else r.continue_())
    page.evaluate("goPage('memory')")
    page.wait_for_function("() => MEM.d && document.querySelector('[data-mtab]')", timeout=15000)
    page.evaluate("MEM.d.health.proposal = {merges: [], archive: ['f1', 'f2']}; pageMemory(true)")
    page.wait_for_selector("#memConsolidate", timeout=10000)
    page.click("#memConsolidate")
    page.wait_for_function("() => [...document.querySelectorAll('#toasts .toast')].some(t => /Tidied up/.test(t.textContent))",
                           timeout=10000)
    text = page.evaluate("[...document.querySelectorAll('#toasts .toast')].map(t => t.textContent).join(' | ')")
    assert "Tidied up: 2 archived. They stay in History, where you can restore them." in text, text
    assert page.errors == [], page.errors


def test_p2_uifront_15_server_gone_mid_run_shows_reload_and_clears_stop(page, ui_app):
    r = page.evaluate("post('/api/agent', {kind: 'chat'})")
    page.evaluate("async id => { await loadThreads(); openThread(id); }", r["id"])
    page.wait_for_function("id => S.cur && S.cur.id === id && S.cur.view", arg=r["id"], timeout=15000)
    page.evaluate("S.stopping = true; renderBar()")                     # Stop was pressed while the chat ran
    ui_app["proc"].kill()
    ui_app["proc"].wait(10)
    page.evaluate("clearTimeout(S.cur.pollT); poll()")
    page.wait_for_selector("#pollErr", timeout=20000)
    page.wait_for_selector("#pollErr button", timeout=5000)
    assert page.inner_text("#pollErr button") == "Reload"
    assert page.evaluate("S.stopping") is False
    # the sidebar refreshes fail too while the server is gone (the 4 s timer while a chat runs, and coming back to the
    # tab): each failure is caught, none ends as an unhandled rejection
    page.evaluate("S.threads.push({id: 'running-1', title: 'x', state: 'working'})")
    page.wait_for_timeout(4500)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(1000)
    assert page.errors == [], page.errors


def test_p2_uifront_15b_server_gone_while_history_rebuilds_still_shows_reload(page, ui_app):
    # the server died while the chat was rebuilding its history (a reset after undo or rewind): the note must go to the
    # column on screen, not to the detached list being built, or the reader sees nothing and never gets Reload
    r = page.evaluate("post('/api/agent', {kind: 'chat'})")
    page.evaluate("async id => { await loadThreads(); openThread(id); }", r["id"])
    page.wait_for_function("id => S.cur && S.cur.id === id && S.cur.view", arg=r["id"], timeout=15000)
    ui_app["proc"].kill()
    ui_app["proc"].wait(10)
    page.evaluate("stopStream(S.cur); clearTimeout(S.cur.pollT); beginStage(S.cur); S.cur.fails = 1; poll()")
    page.wait_for_selector("#pollErr button", timeout=10000)
    assert page.inner_text("#pollErr button") == "Reload"
