"""First-hour run of the released 0.0.18 app, the small UI points: the "Connect GitHub" card on a folder that is not a
Git repository; the terminal used first making the app skip onboarding and add ~/work as a project; "The AI service is
answering again" in a new chat that never saw the outage; a chat title left as the request was typed.

The browser tests drive the real app (python -m meshpool_harness ui) in Chromium, after a real terminal run
(`meshpool -p`) against the mock model, as tests/test_ux018.py does; they skip when no Chromium can start here."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))


# ------------------------------------------------------------------ c. "answering again", server side
def test_a_chat_that_never_saw_the_outage_is_not_told_it_is_over(monkeypatch, tmp_path):
    """The endpoint was marked down (another chat, or before a restart). A new chat's first probe finds it up: it
    carries on with nothing said. A chat that was told it is down is told it is back."""
    from meshpool_harness.core import gateway as GW
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "none.config"))
    monkeypatch.setattr(GW, "BACKOFF_S", 0.05)
    GW.reset()
    h = GW.health("https://models.example.org/v1", "k-12345678", "m")
    assert h.check(lambda: "HTTP 0: timed out") == "down"
    said = []
    assert h.wait_until_up(lambda: "ok", lambda s: time.sleep(0.01), said.append, 5, lambda: None) is True
    assert said == [], said
    h = GW.health("https://models2.example.org/v1", "k-12345678", "m")
    assert h.check(lambda: "HTTP 0: timed out") == "down"
    probes = iter(["HTTP 503", "ok"])
    said = []
    assert h.wait_until_up(lambda: next(probes), lambda s: time.sleep(0.01), said.append, 5, lambda: None) is True
    assert said[0]["down"] and said[-1]["down"] is False and "answering again" in said[-1]["text"], said
    GW.reset()


# ------------------------------------------------------------------ d. chat titles
RAW = "can you please make me a budget spreadsheet for march with the rent, food and travel costs in it thanks"


def _saved_chat(tmp_path, monkeypatch, sid="c-raw-title"):
    from meshpool_harness.ui import agent_api as A
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    d = A.SESSIONS / sid
    d.mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({
        "id": sid, "workdir": str(tmp_path), "title": RAW, "updated": time.time(), "harness": "meshpool-work",
        "kind": "cowork", "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": RAW},
                                       {"role": "assistant", "content": "Done."}]}))
    return A, sid


def test_your_work_shows_the_chat_title_not_the_request_as_typed(tmp_path, monkeypatch):
    from meshpool_harness import yourwork as YW
    A, sid = _saved_chat(tmp_path, monkeypatch)
    [c] = [c for c in YW._chats(0) if c["id"] == sid]
    assert c["title"] == A.nice_title(RAW) and c["title"] != RAW and len(c["title"]) <= 60, c["title"]
    A.write_meta(sid, title="March budget", renamed=True)
    assert [c["title"] for c in YW._chats(0) if c["id"] == sid] == ["March budget"]


def test_a_forked_chat_is_named_after_the_chats_title(tmp_path, monkeypatch):
    from meshpool_harness import chat_rewind as CRW
    A, sid = _saved_chat(tmp_path, monkeypatch)
    new = CRW.fork(sid, None)
    title = A.read_meta(new).get("title")
    assert title == "Fork of " + A.nice_title(RAW) and RAW not in title, title


# ------------------------------------------------------------------ the real app, after the terminal was used first
sync_api = pytest.importorskip("playwright.sync_api")


def _port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from tests.test_ux018 import _wait
    tmp = tmp_path_factory.mktemp("firsthour_ui")
    home, mh = tmp / "home", tmp / "mh"
    work = home / "work"
    for d in (home, mh, work):
        d.mkdir(parents=True, exist_ok=True)
    mport, port = _port(), _port()
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_|ANTHROPIC_|CLAUDE_)", k)}
    env.pop("MESHPOOL_CONFIG", None)
    mlog = open(tmp / "mock.log", "w")
    mock = subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport), str(tmp / "mock.jsonl")],
                            env=env, stdout=mlog, stderr=subprocess.STDOUT)
    (mh / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_SLOTS_DIR=str(tmp / "slots"), MH_UI_OPEN_PAGE="1")
    procs = [mock]
    try:
        _wait(f"http://127.0.0.1:{mport}/v1/models")
        # the terminal first, in ~/work, as the person did
        cli = subprocess.run([sys.executable, "-m", "meshpool_harness", "-p", "say hello"], cwd=str(work), env=env,
                             capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
        assert cli.returncode == 0, cli.stdout[-800:] + cli.stderr[-800:]
        log = open(tmp / "ui.log", "w")
        p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(port)],
                             cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
        procs.append(p)
        base = f"http://127.0.0.1:{port}"
        _wait(base + "/")
        yield {"base": base, "work": str(work), "mh": mh, "tmp": tmp}
    finally:
        for proc in procs:
            proc.kill()
            proc.wait(10)
        mlog.close()


def _page(app, pw, onboarded: bool):
    from tests.test_ux018 import _launch
    b = _launch(pw)
    pg = b.new_context(viewport={"width": 1280, "height": 820}).new_page()
    pg.set_default_timeout(20000)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    if onboarded:
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); localStorage.setItem('mp_permsAsked', '1'); } catch (e) {}")
    pg.goto(app["base"] + "/")
    pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined && S.threads && S.booted", timeout=30000)
    pg.errors, pg.browser_ = errors, b
    return pg


def test_the_terminal_does_not_make_a_project_the_user_never_chose(app):
    """`meshpool -p` in ~/work put ~/work on the app's folder list, and the app's next start made it a pinned project."""
    listed = json.loads((app["mh"] / "projects.json").read_text()) if (app["mh"] / "projects.json").exists() else []
    assert not [r for r in listed if Path(r["path"]).resolve() == Path(app["work"]).resolve()], listed
    with sync_api.sync_playwright() as pw:
        pg = _page(app, pw, onboarded=True)
        try:
            got = pg.evaluate("""async () => ({folders: (await api('/api/projects')).projects, pj: (await api('/api/pj')).projects,
                chats: (await api('/api/agent')).sessions})""")
        finally:
            pg.browser_.close()
    work = Path(app["work"]).resolve()
    assert not [r for r in got["folders"] if Path(r["path"]).resolve() == work], got["folders"]
    assert not [p for p in got["pj"] if Path((p.get("link") or {}).get("path") or "/nonexistent").resolve() == work], got["pj"]
    assert got["chats"] and all(r.get("terminal") for r in got["chats"]), got["chats"]   # the terminal's chat, marked


def test_the_first_app_start_still_offers_onboarding(app):
    with sync_api.sync_playwright() as pw:
        pg = _page(app, pw, onboarded=False)
        try:
            pg.wait_for_function("() => S.settings.ready && S.threads.length >= 1")   # a key and a chat, from the terminal
            pg.wait_for_selector("#onb", state="visible", timeout=15000)              # the folder and permission step
            assert pg.errors == [], pg.errors
        finally:
            pg.browser_.close()


def test_the_connect_github_card_shows_only_on_a_git_repository(app):
    plain = Path(app["tmp"]) / "docs"
    repo = Path(app["tmp"]) / "code"
    plain.mkdir(exist_ok=True)
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with sync_api.sync_playwright() as pw:
        pg = _page(app, pw, onboarded=True)
        try:
            shown = """async f => { await post('/api/projects', {action: 'add', path: f});
                S.connCache = [{id: 'github', name: 'GitHub', connected: false}];
                if (S.cur) newThread(); S.mode = 'chat'; S.draft.project = f; renderHero();
                await new Promise(r => setTimeout(r, 600)); renderHero();
                return !!document.querySelector('.setupcard[data-card="github"]'); }"""
            assert pg.evaluate(shown, str(plain)) is False                # a folder of documents: no GitHub card
            assert pg.evaluate(shown, str(repo)) is True                  # a Git repository: the card
            assert pg.errors == [], pg.errors
        finally:
            pg.browser_.close()


def test_answering_again_shows_only_in_the_chat_that_saw_the_outage(app):
    with sync_api.sync_playwright() as pw:
        pg = _page(app, pw, onboarded=True)
        try:
            from tests.test_ux018 import _fake_chat
            _fake_chat(pg, "t-outage-a")
            n = pg.evaluate("""() => { applyEv({type: 'status', down: false, text: 'The model endpoint is answering again. Picking up where it left off.', i: 0});
                return [...document.querySelectorAll('#col .note')].filter(e => /answering again/.test(e.textContent)).length; }""")
            assert n == 0                                                  # a new chat that never saw the outage
            _fake_chat(pg, "t-outage-b")
            texts = pg.evaluate("""() => { applyEv({type: 'status', down: true, text: "The model endpoint isn't answering (timed out). Checking again in 10 s.", i: 0});
                applyEv({type: 'status', down: false, text: 'The model endpoint is answering again. Picking up where it left off.', i: 1});
                return [...document.querySelectorAll('#col .note')].map(e => e.textContent); }""")
            assert any("The AI service is answering again" in t for t in texts), texts
            assert pg.errors == [], pg.errors
        finally:
            pg.browser_.close()
