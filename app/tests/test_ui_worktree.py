"""G-11 in a real browser against the real app server and the mock model: two Code chats on one repository, the
second offered an isolated copy because the first already works there; its edit stays out of the folder until Merge."""
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
REPO = Path(__file__).resolve().parents[2]
ID = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait(url, secs=60):
    end = time.time() + secs
    while time.time() < end:
        try:
            return urllib.request.urlopen(url, timeout=5).read().decode()
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    pytest.fail(f"nothing answered at {url} within {secs} s")


def git(cwd, *args):
    return subprocess.run(["git", *ID, *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wtui")
    home, mh, proj = tmp / "home", tmp / "mh", tmp / "repo"
    for d in (home, mh, proj):
        d.mkdir()
    git(proj, "init", "-q", "-b", "main")
    (proj / "README.md").write_text("# demo\n")
    git(proj, "add", "-A")
    git(proj, "commit", "-qm", "init")
    mport, uport = _port(), _port()
    (mh / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="Test",
               GIT_COMMITTER_EMAIL="t@example.com")
    env.pop("MESHPOOL_CONFIG", None)
    env["MH_SLOTS_DIR"] = str(tmp / "slots")
    env["MH_PROFILES_DEFAULTS"] = "none"            # Meshpool's own tools (write_file, bash), which the mock model knows
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
        yield {"base": base, "proj": proj, "log": tmp / "ui.log", "tmp": tmp}
    finally:
        for p in procs:
            p.kill()
        log.close()


def _draft(pg, proj):
    pg.evaluate("p => { S.mode = 'code'; S.draft.project = p; S.draft.mode = 'auto'; renderTop(); renderBar(); renderHero(); }",
                str(proj))


def _idle(pg, app):
    try:
        pg.wait_for_function("() => S.cur && S.cur.view && S.cur.view.state === 'idle' && S.cur.view.turns && "
                             "S.cur.view.turns.length", timeout=90000)
    except Exception:  # noqa: BLE001
        pytest.fail("the chat did not finish:\n" + app["log"].read_text(errors="replace")[-3000:])


def test_two_chats_one_repo_the_second_in_an_isolated_copy_then_merge(app):
    proj = app["proj"]
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("MH_TEST_CHROMIUM")
        try:
            b = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as e:  # noqa: BLE001
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium for Playwright here: {e}")
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)

        # chat 1 works in the folder itself
        _draft(pg, proj)
        assert not pg.query_selector("#wtSug")             # nobody else there yet: no suggestion
        pg.fill("#input", "Hi there, look around")
        pg.press("#input", "Enter")
        _idle(pg, app)
        first = pg.evaluate("S.cur.id")
        assert pg.evaluate("S.cur.view.worktree") is None

        # chat 2 on the same folder: offered an isolated copy
        pg.click("#newThread")
        pg.wait_for_timeout(300)
        _draft(pg, proj)
        pg.wait_for_selector("#wtSug #wtOn", timeout=15000)
        assert "already working" in pg.inner_text("#wtSug")
        pg.click("#wtOn")
        pg.wait_for_selector("#wtSug #wtOff", timeout=5000)
        pg.fill("#input", "Say hello")
        pg.press("#input", "Enter")
        pg.wait_for_function("f => S.cur && S.cur.id !== f && S.cur.view && S.cur.view.worktree", arg=first,
                             timeout=30000)
        _idle(pg, app)
        second = pg.evaluate("S.cur.id")
        wt = pg.evaluate("S.cur.view.worktree")
        assert second != first and wt and Path(wt["source"]) == proj.resolve()
        end = time.time() + 25
        while not Path(wt["workdir"], "hello.py").is_file() and time.time() < end:
            time.sleep(0.3)
        pg.wait_for_function("() => S.cur.view.state === 'idle' && S.cur.view.turns.some(t => (t.changed || []).length)",
                             timeout=60000)
        assert Path(wt["workdir"], "hello.py").is_file() and not (proj / "hello.py").exists()

        # the Isolated copy panel: the diff, then Merge
        pg.evaluate("togglePanel('worktree', true)")
        pg.wait_for_selector("#wtPatch", timeout=15000)
        assert "hello.py" in pg.inner_text("#wtBody")
        pg.screenshot(path=str(app["tmp"] / "worktree-panel.png"))
        pg.click("#wtMerge")
        pg.wait_for_function("() => (document.querySelector('#wtNote')||{}).textContent?.includes('Merged')",
                             timeout=15000)
        pg.screenshot(path=str(app["tmp"] / "worktree-merged.png"))
        assert (proj / "hello.py").read_text() == "print('hello from meshpool')\n"
        assert git(proj, "status", "--porcelain") == "" and "hello" in git(proj, "log", "-1", "--format=%s").lower()
        assert errors == [], errors
        b.close()
