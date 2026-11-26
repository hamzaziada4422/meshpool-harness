"""Desktop UI smoothness and clarity, 0.0.18 (IDs UX-01 to UX-11). Each test drives the real app (python -m
meshpool_harness ui) in Chromium against the mock model (testkit/mock_llm_long.py, which streams long Markdown replies
with tool steps) and measures one thing a user sees: which chat is marked, which elements are thrown away and drawn
again, where a chat opens, how many frames the page moves, how long motion lasts, whether focus shows and whether text
is cut. Skipped when no Chromium can start here (outside CI)."""
from __future__ import annotations

import glob
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


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, secs: int = 60) -> None:
    end = time.time() + secs
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    pytest.fail(f"nothing answered at {url} within {secs} s")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ux018")
    home, mh, work = tmp / "home", tmp / "mh", tmp / "work"
    for d in (home, mh, work):
        d.mkdir()
    mport, port = _port(), _port()
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_|ANTHROPIC_|CLAUDE_)", k)}
    env.pop("MESHPOOL_CONFIG", None)
    mlog = open(tmp / "mock.log", "w")
    mock = subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm_long.py"), str(mport), str(tmp / "mock.jsonl")],
                            env={**env, "MOCK_WORD_DELAY": "0.003"}, stdout=mlog, stderr=subprocess.STDOUT)
    (mh / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_SLOTS_DIR=str(tmp / "slots"), MH_UI_OPEN_PAGE="1")
    log = open(tmp / "ui.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(port)],
                         cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait(base + "/")
        yield {"base": base, "work": str(work), "chats": []}
    finally:
        for proc in (p, mock):
            proc.kill()
            proc.wait(10)
        log.close()
        mlog.close()


def _launch(pw):
    """Playwright's own Chromium; else MH_TEST_CHROMIUM; else any Chromium in the browsers folder."""
    try:
        return pw.chromium.launch()
    except Exception as first:  # noqa: BLE001
        roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", "/opt/pw-browsers",
                 str(Path.home() / ".cache" / "ms-playwright")]
        cands = [os.environ.get("MH_TEST_CHROMIUM") or ""]
        for r in filter(None, roots):
            cands += sorted(glob.glob(os.path.join(r, "chromium_headless_shell-*", "*", "chrome-headless-shell")))
            cands += sorted(glob.glob(os.path.join(r, "chromium-*", "chrome-linux*", "chrome")))
        for exe in filter(None, cands):
            try:
                return pw.chromium.launch(executable_path=exe)
            except Exception:  # noqa: BLE001
                continue
        if os.environ.get("CI"):
            raise
        pytest.skip(f"no Chromium for Playwright here: {first}")


def _open(app, pw, width=1280, height=820, **ctx):
    b = _launch(pw)
    c = b.new_context(viewport={"width": width, "height": height}, **ctx)
    pg = c.new_page()
    pg.set_default_timeout(20000)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); localStorage.setItem('mp_permsAsked', '1'); "
                       "localStorage.removeItem('mp_last_chat'); localStorage.removeItem('mp_sidehidden'); } catch (e) {}")
    pg.goto(app["base"] + "/")
    pg.wait_for_selector("#newThread", timeout=30000)
    pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined && S.threads", timeout=30000)
    pg.errors = errors
    pg.browser_ = b
    return pg


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        pg = _open(app, pw)
        yield pg
        pg.browser_.close()


def _chats(app, page, n=3):
    """n finished chats on the server, each with a long streamed reply after three tool steps (made once)."""
    if len(app["chats"]) < n:
        ids = page.evaluate("""async ([w, n]) => { const ids = [];
            for (let i = 0; i < n; i++) { const v = await post('/api/agent', {workdir: w, harness: 'meshpool-work', kind: 'cowork', mode: 'auto', task: 'stream long ' + i});
              await post('/api/agent/' + v.id + '/send', {text: 'stream long please, part ' + i}); ids.push(v.id); }
            return ids; }""", [app["work"], n - len(app["chats"])])
        page.wait_for_function("""ids => Promise.all(ids.map(id => api('/api/agent/' + id + '?since=0').then(v => v.state)))
            .then(s => s.every(x => !['starting', 'working', 'waiting'].includes(x)))""", arg=ids, timeout=120000, polling=1000)
        app["chats"] += ids
    page.evaluate("() => loadThreads()")
    page.wait_for_timeout(300)
    return app["chats"][:n]


def _fake_chat(page, cid="t-ux018"):
    """An open chat with no server behind it: applyEv draws into it as a live chat would."""
    page.evaluate("""cid => { openThread(cid, true); if (S.cur.pollT) { clearTimeout(S.cur.pollT); clearInterval(S.cur.pollT); }
        S.cur.pollT = null; stopStream(S.cur); S.cur.stage = null; $('#col').replaceChildren(); $('#col').classList.remove('loading');
        S.cur.view.state = 'idle'; }""", cid)


# Elements removed under a selector; removed() also counts records not yet delivered (the observer reports after the task).
REMOVED = """sel => { window.__rm = 0; const el = document.querySelector(sel);
    const add = rs => { for (const r of rs) for (const n of r.removedNodes) if (n.nodeType === 1) __rm++; };
    window.__mo = new MutationObserver(add); __mo.observe(el, {childList: true, subtree: true});
    window.removed = () => { add(__mo.takeRecords()); return __rm; }; }"""


def _lum(rgb: str) -> float:
    v = [float(x) / 255 for x in re.findall(r"[\d.]+", rgb)[:3]]
    f = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in v]
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]


def _ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# ---------------------------------------------------------------------------------------------------- UX-01
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_open_chat_stays_marked_while_another_row_is_hovered(app, page, scheme):
    """UX-01: the open chat's row and a hovered row had the very same fill (#E8E8E8 light, #404040 dark), so resting the
    pointer on another chat showed two marked rows and nothing said which one was open. Selected is now a step stronger
    than hover: measured as a luminance ratio between the two fills of at least 1.08, and the open row keeps its fill."""
    ids = _chats(app, page, 2)
    page.emulate_media(color_scheme=scheme)
    page.click(f'.th[data-id="{ids[0]}"]')
    page.mouse.move(700, 5)
    page.wait_for_timeout(250)
    bg = "id => getComputedStyle(document.querySelector(`.th[data-id='${id}']`)).backgroundColor"
    sel = page.evaluate(bg, ids[0])
    page.hover(f'.th[data-id="{ids[1]}"]')
    page.wait_for_timeout(250)
    hov, sel2 = page.evaluate(bg, ids[1]), page.evaluate(bg, ids[0])
    assert sel2 == sel, (sel, sel2)
    assert _ratio(sel, hov) >= 1.08, (scheme, sel, hov, round(_ratio(sel, hov), 3))
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-02
def test_a_sidebar_redraw_keeps_the_rows_that_did_not_change(app, page):
    """UX-02: any change to the chat list beyond a status dot (a new title, a new chat) rewrote the whole sidebar with
    innerHTML: every row was thrown away and drawn again, hover dropped, and every unread dot replayed its pop. Rows are
    now patched in place: one renamed chat costs at most 2 removed elements, the other rows are the same nodes, and a
    dot that already popped does not pop again."""
    ids = _chats(app, page, 3)
    page.evaluate("id => { const t = S.threads.find(x => x.id === id); t.unread = true; renderSide(); }", ids[2])
    page.wait_for_timeout(450)                                   # the unread dot's pop is over
    page.evaluate("() => { for (const li of document.querySelectorAll('#sideList .th, #sideList .nv')) li._mark = 1; }")
    page.evaluate(REMOVED, "#sideList")
    page.evaluate("id => { const t = S.threads.find(x => x.id === id); t.title = t.title + ' (renamed)'; renderSide(); }", ids[1])
    page.wait_for_timeout(50)
    got = page.evaluate("""id => ({removed: removed(), rows: document.querySelectorAll('#sideList .th').length,
        lost: [...document.querySelectorAll('#sideList .th, #sideList .nv')].filter(li => !li._mark).length,
        title: document.querySelector(`.th[data-id='${id}'] .tt`).textContent,
        pops: [...document.querySelectorAll('#sideList .dot.done')].flatMap(d => d.getAnimations()).filter(a => a.playState === 'running').length})""", ids[1])
    assert got["title"].endswith("(renamed)"), got
    assert got["removed"] <= 2 and got["lost"] == 0, got
    assert got["pops"] == 0, got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-03
def test_a_streaming_reply_keeps_its_elements_and_the_readers_selection(page):
    """UX-03: every frame of a streaming reply threw away the paragraph (or list, or table) being written and drew a new
    one: 840 elements were removed while one long reply streamed, and text the reader selected in that paragraph was
    lost at the next word. The open block is now patched: at most 10 elements are removed over the whole reply (a block
    that turns from text into a table or heading), and a selection made mid-stream survives the following words."""
    _fake_chat(page)
    page.evaluate(REMOVED, "#col")
    got = page.evaluate("""() => {
        const para = 'The pool keeps a **short list** of options and `routes` each task to the best one. ';
        const text = '## Summary\\n\\n' + para.repeat(6) + '\\n\\n- one ' + para + '\\n- two ' + para + '\\n\\n| a | b |\\n|---|---|\\n| 1 | 2 |\\n\\n' + para.repeat(8);
        applyEv({type: 'user', text: 'go', i: 0});
        const words = text.split(' '); let sel = null, kept = null;
        words.forEach((w, k) => {
            applyEv({type: 'text_delta', text: w + (k < words.length - 1 ? ' ' : '')}); flushLive(S.cur);
            if (k === words.length - 30) {               // select "pool" in the paragraph still being written
                const ps = S.cur.live.querySelectorAll('p'), p = ps[ps.length - 1], t = p.firstChild;
                const r = document.createRange(); const at = t.nodeValue.indexOf('pool'); r.setStart(t, at); r.setEnd(t, at + 4);
                getSelection().removeAllRanges(); getSelection().addRange(r); sel = getSelection().getRangeAt(0).toString();
            }
        });
        kept = getSelection().rangeCount ? getSelection().getRangeAt(0).toString() : '';
        return {removed: removed(), sel, kept, html: S.cur.live.querySelectorAll('h2, ul, table, p').length};
    }""")
    assert got["html"] >= 4, got
    assert got["removed"] <= 10, got
    assert got["sel"] == "pool" and got["kept"] == "pool", got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-04
def test_a_run_starting_and_ending_keeps_the_composer_buttons(page):
    """UX-04: the composer bar was rebuilt with innerHTML when a run started and when it ended, so the plus button, the
    pills and Send were new elements twice a turn: keyboard focus on them was lost and hover flashed. Only the Stop and
    Send now buttons come and go now (at most 3 removed elements over a run), and focus stays where it was."""
    _fake_chat(page)
    page.evaluate("() => renderBar()")
    page.focus("#attBtn")
    page.evaluate("() => { document.querySelector('#attBtn')._mark = 1; document.querySelector('#sendBtn')._mark = 1; }")
    page.evaluate(REMOVED, "#cbar")
    got = page.evaluate("""() => { S.cur.view.state = 'working'; renderBar(); const busy = !!document.querySelector('#stopBtn');
        S.cur.view.state = 'idle'; renderBar();
        return {busy, removed: removed(), att: !!document.querySelector('#attBtn')._mark, send: !!document.querySelector('#sendBtn')._mark,
                focus: document.activeElement && document.activeElement.id, stop: !!document.querySelector('#stopBtn')}; }""")
    assert got["busy"] and not got["stop"], got
    assert got["att"] and got["send"] and got["focus"] == "attBtn", got
    assert got["removed"] <= 3, got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-05
def test_a_header_redraw_keeps_its_buttons_and_binds_them_once(page):
    """UX-05: a new title or a run's state change rewrote the whole top bar: every button and tooltip target was new,
    and the title flashed. The header is now patched (at most 1 removed element for a new title) and its buttons keep
    one click handler each: the progress button opens the panel instead of opening and closing it in one click."""
    _fake_chat(page)
    page.evaluate("() => { S.cur.view.title = 'First name'; renderTop(); }")
    page.evaluate("() => { for (const b of document.querySelectorAll('#topbar button, #ttl')) b._mark = 1; }")
    page.evaluate(REMOVED, "#topbar")
    got = page.evaluate("""() => { S.cur.view.title = 'A second, longer name'; renderTop(); S.cur.view.title = 'Third'; renderTop();
        return {removed: removed(), title: document.querySelector('#ttl').textContent,
                lost: [...document.querySelectorAll('#topbar button, #ttl')].filter(b => !b._mark).map(b => b.id)}; }""")
    assert got["title"] == "Third", got
    assert got["removed"] <= 1 and got["lost"] == [], got
    page.click("#panelBtn")
    page.wait_for_timeout(200)
    assert page.evaluate("() => !!S.panel && !document.querySelector('#panel').hidden"), "one click opens the panel"
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-06
FRAMES = """() => { window.__fr = []; const t = () => { const c = document.querySelector('#conv'), col = document.querySelector('#col');
    __fr.push([S.cur ? S.cur.id : '', col.classList.contains('loading') ? 1 : 0, col.children.length, Math.round(c.scrollTop),
               Math.round(c.scrollHeight - c.scrollTop - c.clientHeight)]);
    if (!window.__frStop) requestAnimationFrame(t); }; requestAnimationFrame(t); }"""


def test_an_opened_chat_is_painted_at_its_end_and_stays_there(app):
    """UX-06: a chat opened from the sidebar was painted first at its top or middle (its messages count as 96 px guesses
    until drawn) and jumped to the end a frame later; the scroll event of that jump also unpinned the chat, so some
    opens stayed at the top. Measured over six opens of three long chats: once a chat is drawn, no frame shows it more
    than 40 px above where it settles, and every open ends at the end."""
    with sync_api.sync_playwright() as pw:
        page = _open(app, pw, height=700)
        try:
            ids = _chats(app, page, 3)
            page.evaluate(FRAMES)
            ends = []
            for _ in range(2):
                for cid in ids:
                    page.evaluate("() => __fr.push(['click'])")
                    page.click(f'.th[data-id="{cid}"]')
                    page.wait_for_function("id => S.cur && S.cur.id === id && !S.cur.stage && document.querySelector('#col').children.length > 2", arg=cid)
                    page.wait_for_timeout(500)
                    ends.append(page.evaluate("() => { const c = document.querySelector('#conv'); return Math.round(c.scrollHeight - c.scrollTop - c.clientHeight); }"))
            fr = page.evaluate("() => { window.__frStop = true; return __fr; }")
            segs, cur = [], None
            for f in fr:
                if f == ["click"]:
                    cur = []
                    segs.append(cur)
                elif cur is not None:
                    cur.append(f)
            off = []
            for k, seg in enumerate(segs):
                drawn = [f for f in seg if f[0] == ids[k % 3] and not f[1] and f[2] > 2]
                if not drawn:
                    continue
                settled = drawn[-1][3]
                off += [(k, f[3], settled) for f in drawn if f[3] < settled - 40]
            assert max(ends) <= 2, ends
            assert off == [], off[:8]
            assert page.errors == [], page.errors
        finally:
            page.browser_.close()


# ---------------------------------------------------------------------------------------------------- UX-07
def test_hiding_and_showing_the_sidebar_lays_the_page_out_once(app, page):
    """UX-07: the sidebar opened and closed by animating the grid's column width, which laid out the chat, the header
    and the composer again on every frame for 200 ms (12 frames of moving layout per toggle, 48 for two round trips).
    The columns now change once and the sidebar slides with transform and opacity: the composer moves on at most one
    frame per toggle, and no transition runs on the grid."""
    _fake_chat(page)
    # the layout position of the composer (offsets add up through the parents and ignore transforms): the page glides over by
    # transform after the change (J-01), which is not a new layout
    page.evaluate("""() => { window.__x = []; const t = () => { let x = 0; for (let e = document.querySelector('#composer'); e; e = e.offsetParent) x += e.offsetLeft;
        __x.push(Math.round(x)); if (!window.__xs) requestAnimationFrame(t); }; requestAnimationFrame(t); }""")
    for _ in range(2):
        page.click("#hideSide")
        page.wait_for_timeout(400)
        page.click("#showSide")
        page.wait_for_timeout(400)
    xs = page.evaluate("() => { window.__xs = true; return __x; }")
    moves = sum(1 for a, b in zip(xs, xs[1:]) if a != b)
    tr = page.evaluate("() => [getComputedStyle(document.querySelector('#app')).transitionProperty, getComputedStyle(document.querySelector('#side')).transitionProperty, getComputedStyle(document.querySelector('#app')).transitionDuration]")
    assert moves <= 4, (moves, xs[:40])
    assert "grid" not in tr[0] and (tr[0] != "all" or tr[2] == "0s"), tr
    assert "transform" in tr[1], tr
    assert not page.evaluate("() => document.querySelector('#app').classList.contains('noside')")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-08
def test_with_reduced_motion_a_reply_grows_without_an_eased_height(app):
    """UX-08: the streaming reply eased its box open to the height of its text with a script-driven height animation
    that ignored prefers-reduced-motion (CSS turned off every other animation): the box kept moving and the frame loop
    ran at display rate to drive it. With reduced motion the reply is never put on the easing list (0 entries, no
    overflow clip, no inline height); without it, the easing still runs."""
    with sync_api.sync_playwright() as pw:
        for motion, want in (("reduce", False), ("no-preference", True)):
            page = _open(app, pw, reduced_motion=motion)
            try:
                _fake_chat(page)
                got = page.evaluate("""() => { applyEv({type: 'user', text: 'go', i: 0});
                    applyEv({type: 'text_delta', text: 'Some words that start the reply. '.repeat(20)}); flushLive(S.cur);
                    const n = S.cur.live; return {rv: n.classList.contains('rv'), h: n.style.height, ease: EASE.size}; }""")
                eased = got["rv"] or got["ease"] > 0 or bool(got["h"])
                assert eased is want, (motion, got)
                assert page.errors == [], page.errors
            finally:
                page.browser_.close()


# ---------------------------------------------------------------------------------------------------- UX-09
def test_keyboard_focus_in_the_message_box_is_visible(page):
    """UX-09: tabbing into the message box changed only its border, from #E0E0E0 to #CBCBCB: a 1.2:1 change, invisible,
    so a keyboard user could not see that typing would go there. Reached with Tab, the box now shows the app's focus
    ring (border contrast against the resting border of at least 3:1, plus the soft halo). A click into the box keeps the
    quiet border, so the look for pointer users is unchanged."""
    page.evaluate("() => newThread()")
    page.mouse.click(640, 200)
    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    page.wait_for_timeout(300)                                     # the border eases over 120 ms
    rest = page.evaluate("() => getComputedStyle(document.querySelector('#composer')).borderTopColor")
    page.focus("#attBtn")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("() => document.activeElement.id") == "input"
    page.wait_for_timeout(300)
    kb = page.evaluate("() => { const c = getComputedStyle(document.querySelector('#composer')); return [c.borderTopColor, c.boxShadow]; }")
    assert _ratio(kb[0], rest) >= 3, (kb, rest, round(_ratio(kb[0], rest), 2))
    page.mouse.click(640, 200)
    page.click("#input")
    page.wait_for_timeout(300)
    ms = page.evaluate("() => getComputedStyle(document.querySelector('#composer')).borderTopColor")
    assert _ratio(ms, rest) < 1.5, (ms, rest)                     # pointer users keep the quiet border
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-10
def test_the_model_name_is_not_cut_at_1024_pixels(app):
    """UX-10: under a 760 px main column (1024 px wide with the sidebar, or 1280 with the side panel) the model button's
    name was capped at 120 px, so "DeepSeek V4.1 Flash" read "DeepSeek V4.1 F..." with half the row empty. The name keeps
    its 200 px down to a 640 px column; the row's other side gives way first."""
    with sync_api.sync_playwright() as pw:
        page = _open(app, pw, width=1024)
        try:
            page.evaluate("() => newThread()")
            got = page.evaluate("""() => { const l = document.querySelector('#mPill .lbl'); l.textContent = 'DeepSeek V4.1 Flash';
                return {sw: l.scrollWidth, cw: l.clientWidth, main: Math.round(document.querySelector('#main').getBoundingClientRect().width),
                        row: [...document.querySelectorAll('#selrow > *')].every(k => k.getBoundingClientRect().right <= document.querySelector('#selrow').getBoundingClientRect().right + 1)}; }""")
            assert got["main"] < 760, got
            assert got["sw"] <= got["cw"], got
            assert got["row"], got
        finally:
            page.browser_.close()


# ---------------------------------------------------------------------------------------------------- UX-11
def test_one_off_motion_lasts_200ms_or_less(page):
    """UX-11: one-off motion ran long and uneven: the unread dot popped for 400 ms (500 ms in the older rule, replayed on
    every sidebar redraw), and Send's launch and the message entrance used a 260 ms token. Menus, dialogs, toasts, the
    sidebar, the send button and the unread dot now all finish within 200 ms."""
    got = page.evaluate("""() => {
        const ms = v => Math.max(...String(v).split(',').map(x => parseFloat(x) * (x.trim().endsWith('ms') ? 1 : 1000)));
        const probe = (html, sel, where) => { const box = document.createElement('div'); box.innerHTML = html; (where || document.body).appendChild(box);
            const el = box.querySelector(sel); const cs = getComputedStyle(el); const r = {anim: cs.animationName !== 'none' ? ms(cs.animationDuration) : 0, trans: ms(cs.transitionDuration)}; box.remove(); return r; };
        const side = document.querySelector('#sideList');
        return {
          dot: probe('<span class="dot done"></span>', '.dot', side),
          send: probe('<button class="send go"></button>', '.send'),
          toast: probe('<div class="toast">x</div>', '.toast'),
          menu: probe('<div class="ctx">x</div>', '.ctx'),
          pop: probe('<div class="pop">x</div>', '.pop'),
          dialog: probe('<div class="scrim"><div class="dlg">x</div></div>', '.dlg'),
          side: {anim: 0, trans: ms(getComputedStyle(document.querySelector('#side')).transitionDuration)},
          slow: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--t-slow')),
        }; }""")
    over = {k: v for k, v in got.items() if k != "slow" and max(v["anim"], v["trans"]) > 200}
    assert over == {}, json.dumps(got)
    assert got["slow"] <= 200, got
    assert page.errors == [], page.errors


# ================================================== findings from the persona runs (data analyst, consultant): UX-12 on
def _fake_project(page, kind="code"):
    """A pinned project with one chat in it (a Code chat by default), only in the page."""
    page.evaluate("""kind => { S.pj = (S.pj || []).filter(p => p.id !== 'pj-ux'); S.pj.push({id: 'pj-ux', name: 'Sales analysis', pinned: true, updated: 1});
        S.threads = S.threads.filter(t => t.id !== 't-pj-ux').concat([{id: 't-pj-ux', title: 'Monthly revenue chart', kind, workdir: '/tmp/proj018',
          project_id: 'pj-ux', state: 'idle', updated: Date.now() / 1000}]);
        S.pjOpen = new Set(['pj-ux']); renderSide(); }""", kind)


# ---------------------------------------------------------------------------------------------------- UX-12
def test_the_open_chat_has_an_accent_edge_and_a_running_dot_stays_visible(page):
    """UX-12 (owner): the open row also needs a steady mark of its own, and hovering or opening a running chat hid its
    running dot (the options button took its place). The open row now carries an accent edge, and a running or waiting
    dot stays visible beside the options button."""
    page.evaluate("""() => { S.threads = S.threads.filter(t => t.id !== 't-run').concat([{id: 't-run', title: 'Busy chat', kind: 'chat', state: 'working', updated: Date.now() / 1000}]);
        S.view = 'chat'; S.cur = {id: 't-run'}; renderSide(); }""")
    row = '.th[data-id="t-run"]'
    page.hover(row)
    page.wait_for_timeout(200)
    got = page.evaluate("""s => { const r = document.querySelector(s), d = r.querySelector('.dot.run'), m = r.querySelector('.more');
        const dr = d.getBoundingClientRect(), mr = m.getBoundingClientRect();
        return {shadow: getComputedStyle(r).boxShadow, vis: getComputedStyle(d).visibility, overlap: dr.right > mr.left && dr.left < mr.right}; }""", row)
    page.evaluate("() => { S.cur = null; S.view = 'chat'; }")
    assert "inset" in got["shadow"], got
    assert got["vis"] == "visible" and not got["overlap"], got


# ---------------------------------------------------------------------------------------------------- UX-13
def test_the_progress_button_looks_pressed_only_while_its_panel_is_open(page):
    """UX-13: the Progress button always had the hover fill, so it looked pressed with its panel closed. It has no fill
    when closed and the selected fill when open."""
    _fake_chat(page)
    page.evaluate("() => { if (S.panel) togglePanel(S.panel); renderTop(); }")
    page.mouse.move(5, 700)
    page.wait_for_timeout(200)
    closed = page.evaluate("() => getComputedStyle(document.querySelector('#panelBtn')).backgroundColor")
    page.click("#panelBtn")
    page.mouse.move(5, 700)
    page.wait_for_timeout(300)
    opened = page.evaluate("() => getComputedStyle(document.querySelector('#panelBtn')).backgroundColor")
    assert closed in ("rgba(0, 0, 0, 0)", "transparent"), closed
    assert opened not in ("rgba(0, 0, 0, 0)", "transparent"), opened
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-14
def test_the_side_panel_slides_in_and_the_model_button_keeps_auto(page):
    """UX-14: the side panel appeared at once, and with it open (a 580 px column at 1280 px) the "Auto" label beside
    the model went away and the name was cut to 96 px. The panel now slides in with transform, and the model button
    keeps its full name and "Auto"."""
    _fake_chat(page)
    page.evaluate("() => { if (S.panel) togglePanel(S.panel); renderBar(); }")
    page.click("#panelBtn")
    anim = page.evaluate("() => document.querySelector('#panel').getAnimations().map(a => a.animationName || '')")
    page.wait_for_timeout(400)
    page.click("#showSide")             # the sidebar folds while the panel is open (J-01); a person who brings it back gets the 580 px column
    page.wait_for_timeout(400)
    got = page.evaluate("""() => { const em = document.querySelector('#mPill em'), l = document.querySelector('#mPill .lbl'); l.textContent = 'DeepSeek V4.1 Flash';
        return {main: Math.round(document.querySelector('#main').getBoundingClientRect().width), em: em && getComputedStyle(em).display, sw: l.scrollWidth, cw: l.clientWidth}; }""")
    assert "mp-panel-in" in anim, anim
    assert got["main"] < 640 and got["em"] != "none" and got["sw"] <= got["cw"], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-15
def test_answering_a_question_card_shifts_nothing_below_it(page):
    """UX-15: an answered question card eased its height down over 180 ms, so everything under it moved on every frame
    (a run of layout shifts, CLS about 0.01 in the consultant's run). It now takes its answered size at once and fades
    its answer in: at most one layout shift, and no height left on the card."""
    _fake_chat(page)
    page.evaluate("""() => { applyEv({type: 'user', text: 'Ask me', i: 0});
        applyEv({type: 'question', id: 'q1', questions: [{question: 'Which quarter?', options: [{label: 'Q1'}, {label: 'Q2'}, {label: 'Q3'}]}], i: 1});
        applyEv({type: 'text_delta', text: 'Text under the card. '.repeat(20)}); flushLive(S.cur); }""")
    page.wait_for_timeout(400)
    page.evaluate("""() => { window.__sh = []; new PerformanceObserver(l => { for (const e of l.getEntries()) __sh.push(e.value); }).observe({type: 'layout-shift'});
        applyEv({type: 'question_done', id: 'q1', source: 'user', answers: [{selected: ['Q2']}], i: 2}); }""")
    page.wait_for_timeout(500)
    got = page.evaluate("() => ({shifts: __sh.length, cls: __sh.reduce((a, b) => a + b, 0), h: (document.querySelector('.qcard.done') || {style: {}}).style.height})")
    assert got["shifts"] <= 1 and not got["h"], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-16, UX-17
def test_an_empty_memory_page_shows_one_empty_state_and_one_add_button(page):
    """UX-16: with no memory the page showed two empty states ("Nothing here yet" and "No memories yet") under filters
    over nothing, and more than one way to add. It now shows one empty state and the one Add memory button in the bar.
    UX-17: the page title said "What Meshpool knows"; the product name is always lowercase in prose."""
    page.evaluate("() => goPage('memory')")
    page.wait_for_selector(".mem-empty")
    page.wait_for_timeout(300)
    got = page.evaluate("""() => ({empties: document.querySelectorAll('.mem-empty').length, filters: document.querySelectorAll('.mem-filters').length,
        adds: [...document.querySelectorAll('button')].filter(b => b.offsetParent && /^\\s*Add( a)? memory\\s*$/i.test(b.textContent)).length,
        title: document.querySelector('.gh h1').textContent})""")
    assert got["empties"] == 1 and got["filters"] == 0 and got["adds"] == 1, got
    assert got["title"] == "What meshpool knows", got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-18, UX-25
def test_bare_links_are_clickable_and_short_code_does_not_break(page):
    """UX-18: a plain https address in a reply was text (0 links for https://www.revenue.ie/...). http and https
    addresses now become links that open outside the app; javascript: and other schemes never do, and code spans and
    existing links are left alone. UX-25: a short inline code token such as `2025-12` broke at its hyphen at the end of
    a line; short tokens now never break."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'go', i: 0});
        applyEv({type: 'assistant', text: 'See https://www.revenue.ie/en/tax-rates/index.aspx?x=1&y=2. Not javascript:alert(1) or `https://in.code/x` or [a link](https://ok.example/a). Months run to `2025-12` and `units x unit_price`.', i: 1});
        const n = S.cur.lastAi; return {hrefs: [...n.querySelectorAll('a')].map(a => [a.getAttribute('href'), a.target]),
            codes: [...n.querySelectorAll('code')].map(c => [c.textContent, getComputedStyle(c).whiteSpace])}; }""")
    hrefs = dict(got["hrefs"])
    assert "https://www.revenue.ie/en/tax-rates/index.aspx?x=1&y=2" in hrefs, got
    assert all(h.startswith("https://") for h in hrefs) and len(hrefs) == 2, got
    assert all(t == "_blank" for t in hrefs.values()), got
    ws = dict(got["codes"])
    assert ws["2025-12"] == "nowrap" and ws["https://in.code/x"] == "nowrap", got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-19
def test_a_turn_that_failed_offers_try_again_but_no_thumbs(page):
    """UX-19: a turn that ended in an error still showed copy, check and thumbs under it. Only Try again is left."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'go', i: 0}); applyEv({type: 'error', text: 'The model did not answer.', i: 1}); applyEv({type: 'turn_end', i: 2});
        applyEv({type: 'user', text: 'again', i: 3}); applyEv({type: 'assistant', text: 'Here it is.', i: 4}); applyEv({type: 'turn_end', i: 5});
        return [...document.querySelectorAll('#col .racts010')].map(w => [...w.querySelectorAll('[data-ra]')].map(b => b.dataset.ra)); }""")
    assert got[0] == ["retry"], got
    assert "up" in got[1] and "copy" in got[1], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-20
def test_attached_files_show_as_chips_and_the_chats_folder_by_name(page):
    """UX-20: the note the server adds for the agent, "Attached files (in this chat's folder): a.csv", showed inside the
    user's own bubble, and a chat's private folder showed by its id (b3c8e781c617) in the Progress panel. The names
    are file chips now, Copy gives back what the user typed (Edit gets the files back as chips, SR-07), and the folder
    reads by the chat's name, "<title> (this chat's own folder)" (WF-H)."""
    _fake_chat(page)
    got = page.evaluate("""() => { S.cur.view.scratch = true; S.cur.view.workdir = '/tmp/x/chats/b3c8e781c617';
        applyEv({type: 'user', text: "Chart this\\n\\nAttached files (in this chat's folder): sales.csv, notes.pdf", i: 0});
        const mu = document.querySelector('#col .msg-user');
        S.cur.view.title = 'Sales chart';
        return {bubble: mu.querySelector('.b').textContent, chips: [...mu.querySelectorAll('.att .flink')].map(c => c.textContent), copy: splitAtt(userText(mu)).text, folder: rootLabel()}; }""")
    assert "Attached files" not in got["bubble"], got
    assert got["chips"] == ["sales.csv", "notes.pdf"] and got["copy"] == "Chart this", got
    assert "b3c8" not in got["folder"] and "Sales chart" in got["folder"] and "folder" in got["folder"], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-21
@pytest.mark.parametrize("width", [1280, 1024])
def test_a_toast_never_covers_the_message_box(app, width):
    """UX-21: toasts sat 96 px above the bottom right, on top of Send ("1 chats imported" covered it on first open), and
    one import said "1 chats". Toasts now show under the header, clear of the message box, and the count reads right."""
    with sync_api.sync_playwright() as pw:
        page = _open(app, pw, width=width, height=760)
        try:
            page.evaluate("() => { newThread(); toast('1 chat imported.', 9000, {label: 'Open', fn: () => {}}); }")
            page.wait_for_timeout(300)
            got = page.evaluate("""() => { const a = document.querySelector('.toast').getBoundingClientRect(), b = document.querySelector('#composer').getBoundingClientRect();
                return {hit: a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top, top: Math.round(a.top)}; }""")
            assert not got["hit"], got
        finally:
            page.browser_.close()
    src = (REPO / "app" / "meshpool_harness" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "toLocaleString()} chats imported" not in src


# ---------------------------------------------------------------------------------------------------- UX-22, UX-23
def test_a_project_page_has_no_empty_card_and_the_breadcrumb_keeps_the_project(app):
    """UX-22: the project brief's card was drawn empty before (or without) its text, an empty pill above
    Instructions. UX-23: at 1024 px the header dropped the project name from the breadcrumb of a chat in a project."""
    with sync_api.sync_playwright() as pw:
        page = _open(app, pw, width=1024, height=760)
        try:
            pid = page.evaluate("""async () => { const r = await post('/api/pj', {action: 'create', name: 'Sales analysis'}); await loadPj();
                return r.id || (r.project && r.project.id); }""")
            page.evaluate("pid => { window.MeshBrief = null; openProject(pid); }", pid)
            page.wait_for_selector(".pjside")
            empty = page.evaluate("() => { const b = document.querySelector('#pjBrief'); return b ? b.getBoundingClientRect().height : 0; }")
            assert empty == 0, empty
            _fake_chat(page)
            page.evaluate("pid => { S.cur.view.project_id = pid; S.cur.view.title = 'Revenue by region'; renderTop(); }", pid)
            got = page.evaluate("() => { const c = document.querySelector('#pjChip'); return {w: c ? c.getBoundingClientRect().width : 0, main: Math.round(document.querySelector('#main').getBoundingClientRect().width)}; }")
            assert got["main"] < 860 and got["w"] > 40, got
            assert page.errors == [], page.errors
        finally:
            page.browser_.close()


# ---------------------------------------------------------------------------------------------------- UX-24
def test_the_onboarding_button_never_covers_the_steps_fields(page):
    """UX-24: onboarding's Continue and Start buttons were sticky at the bottom of the screen: at 1024x700 the button
    covered the API key field of step 1, and on step 3 it sat on "3. Add to memory" (1366 and 1024 px), with a 24 px
    band under it where the next card showed. The button now closes the card in the flow: whatever the scroll, it
    covers none of the step's fields."""
    page.set_viewport_size({"width": 1024, "height": 700})
    got = page.evaluate("""() => { const o = document.createElement('div'); o.className = 'onb'; o.id = 'onbTest';
        o.innerHTML = `<div class="onb-card"><h1>Step</h1><input id="kf" style="display:block;width:100%;height:36px;margin-top:520px"><section class="card" style="height:400px">3. Add to memory</section><div class="onb-go"><button class="bigbtn">Start</button></div></div>`;
        document.body.appendChild(o); const hits = [];
        for (const top of [0, 200, 400, 10000]) { o.scrollTop = top; const b = o.querySelector('.onb-go').getBoundingClientRect();
          for (const el of o.querySelectorAll('#kf, .card')) { const r = el.getBoundingClientRect(); const vis = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0) > 0;
            if (vis && b.top < innerHeight && b.left < r.right && b.right > r.left && b.top < r.bottom && b.bottom > r.top) hits.push([top, el.id || el.className]); } }
        o.remove(); return hits; }""")
    assert got == [], got


# ---------------------------------------------------------------------------------------------------- UX-26
def test_a_chart_in_the_chats_folder_shows_as_an_image(app, page):
    """UX-26: a reply that pointed at the chart it saved, ![Monthly revenue](monthly_revenue.png), showed the Markdown as
    text. An image in the chat's folder now shows inline through the token-checked /raw route; a remote image, a path
    outside the folder or a parent path stays as text."""
    import base64
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8DAwMDAxMDAwMDAAAANHQEDK+mmyAAAAABJRU5ErkJggg==")
    _chats(app, page, 1)                                           # the folder is one a chat works in
    (Path(app["work"]) / "monthly_revenue.png").write_bytes(png)
    _fake_chat(page)
    got = page.evaluate("""async w => { S.cur.view.workdir = w; applyEv({type: 'user', text: 'chart', i: 0});
        applyEv({type: 'assistant', text: 'Here:\\n\\n![Monthly revenue](monthly_revenue.png)\\n\\n![x](../secret.png) ![y](https://example.com/a.png) ![z](/etc/a.png)', i: 1});
        const imgs = [...S.cur.lastAi.querySelectorAll('img.mdimg')]; await new Promise(r => setTimeout(r, 800));
        return {n: imgs.length, ok: imgs.map(i => i.naturalWidth), alt: imgs.map(i => i.alt), text: S.cur.lastAi.textContent}; }""", app["work"])
    assert got["n"] == 1 and got["ok"] == [2] and got["alt"] == ["Monthly revenue"], got
    assert "../secret.png" in got["text"] and "/etc/a.png" in got["text"], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-27
def test_a_pinned_project_lists_its_code_chats_too(page):
    """UX-27: a chat started from a project's page and routed to Code was missing under the project in the sidebar,
    which said "No chats yet": the project row only listed chats of the sidebar's mode. It lists all its chats now."""
    _fake_project(page, "code")
    got = page.evaluate("() => [...document.querySelectorAll('#sideList .threads.sub .th')].map(t => t.dataset.id)")
    assert "t-pj-ux" in got, got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-28
def test_the_router_note_is_kept_out_of_the_chat_in_plain_words(page):
    """UX-28: router notes reached the chat as written for engineers: "no strong signal" and "Best benchmark quality for
    a data task (csv)" at the end of the turn's line, and "data words in a Code chat". The receipt says "chosen for
    data" or nothing, and the "Using" line keeps the reason on its tooltip, not in the text."""
    sys.path.insert(0, str(REPO / "app"))
    from meshpool_harness import receipts as RC
    assert RC.plain_reason("no strong signal") == ""
    assert RC.plain_reason("Best benchmark quality for a data task (csv)") == "chosen for data"
    assert RC.plain_reason("data words in a Code chat") == "chosen for data"
    assert RC.plain_reason("docs words (1)") == "chosen for documents"
    _fake_chat(page)
    # meshpool's own agent is no longer announced (0.0.18 design pass): Auto's pick of another agent is, as here
    got = page.evaluate("""() => { applyEv({type: 'status', using: true, text: 'Using Codex', auto: {reason: 'Best benchmark quality for a data task (csv)'}, i: 0});
        const n = document.querySelector('#col .note.using'); return [n.textContent, (n.querySelector('[title]') || {}).title || '']; }""")
    assert "benchmark" not in got[0] and "benchmark" in got[1], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-29
def test_a_file_dropped_on_the_conversation_is_attached(page):
    """UX-29: a file dropped on the conversation, not on the small message box, was not taken: dragover was not
    prevented, so the browser opened the file in place of the app. The whole chat pane takes it now, with the box's
    highlight and the same attachment chip; a file dropped anywhere else is ignored, never opened."""
    _fake_chat(page)
    got = page.evaluate("""async () => { S.attachments = []; renderAttach();
        const dt = new DataTransfer(); dt.items.add(new File(['region,sales\\n'], 'sales.csv', {type: 'text/csv'}));
        const conv = document.querySelector('#conv'), ev = t => new DragEvent(t, {dataTransfer: dt, bubbles: true, cancelable: true});
        const over = ev('dragover'); conv.dispatchEvent(over);
        const lit = document.querySelector('#composer').classList.contains('drag');
        const drop = ev('drop'); conv.dispatchEvent(drop); await new Promise(r => setTimeout(r, 200));
        const elsewhere = ev('dragover'); document.querySelector('#topbar').dispatchEvent(elsewhere);
        return {over: over.defaultPrevented, lit, drop: drop.defaultPrevented, chips: [...document.querySelectorAll('#attachRow .att-chip')].map(c => c.textContent.trim()),
                after: document.querySelector('#composer').classList.contains('drag'), elsewhere: elsewhere.defaultPrevented}; }""")
    assert got["over"] and got["lit"] and got["drop"] and not got["after"], got
    assert any("sales.csv" in c for c in got["chips"]), got
    assert got["elsewhere"], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-30
def test_onboarding_counts_its_steps_and_keeps_the_logo_still(page):
    """UX-30: with the key already set, onboarding opened on "Step 2 of 3", and the logo moved about 10 px from one step
    to the next because each card was centred on its own height. It now reads "Step 1 of 2" and every step starts at
    the same top, so the logo does not move."""
    got = page.evaluate("""() => { try { localStorage.removeItem('mp_onbFull'); } catch (e) {} const o = document.createElement('div'); o.className = 'onb'; o.id = 'onbT'; document.body.appendChild(o);
        const card = h => { o.innerHTML = `<div class="onb-card"><img class="logo mlogo" src="${logoSrc()}" alt="" width="164" height="40">${onbSteps(2)}<div style="height:${h}px"></div></div>`;
            return [Math.round(o.querySelector('.logo').getBoundingClientRect().top), o.querySelector('.onb-steps small').textContent]; };
        const a = card(200), b = card(520); o.remove(); return {a, b}; }""")
    assert got["a"][1] == "Step 1 of 2", got
    assert got["a"][0] == got["b"][0], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-31
def test_an_unknown_cost_is_not_written_under_every_reply(page):
    """UX-31: every reply on a model without a price list ended "cost not known (unpriced model)". The line leaves it
    out and says it once in its tooltip."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'go', i: 0}); applyEv({type: 'assistant', text: 'Done.', i: 1}); applyEv({type: 'turn_end', i: 2});
        applyEv({type: 'task_receipt', text: '12 s · cost not known (unpriced model) · Meshpool Work on claude-sonnet', reason: ''});
        const r = document.querySelector('#col .rcpt010 span'); return r ? [r.textContent, r.title] : null; }""")
    assert got and "cost not known" not in got[0] and "12 s" in got[0] and "Meshpool Work" in got[0], got
    assert "not known" in got[1], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-32
def test_the_rule_field_lines_up_with_its_buttons(page):
    """UX-32: in Settings > Permissions the new-rule field was an unstyled box shorter than its Allow and Deny buttons
    and only as wide as its placeholder. It is as tall as the buttons now and fills the row."""
    page.evaluate("() => openSettings('permissions')")              # its own page since the approvals work (18d6de94)
    page.wait_for_selector("#ruleText", timeout=10000)
    got = page.evaluate("""() => { const r = e => document.querySelector(e).getBoundingClientRect(); const i = r('#ruleText'), a = r('#ruleAdd'), row = document.querySelector('#ruleText').parentElement.getBoundingClientRect();
        return {ih: Math.round(i.height), ah: Math.round(a.height), dt: Math.abs(Math.round(i.top - a.top)), share: i.width / row.width}; }""")
    assert abs(got["ih"] - got["ah"]) <= 1 and got["dt"] <= 1, got
    assert got["share"] > 0.5, got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-33
def test_the_permissions_tab_draws_without_an_error_and_add_a_folder_works(page):
    """UX-33: drawing Settings > Permissions ended in a TypeError (a stray second drawRules call was given an element,
    not the rules), so the code after it never ran: "Add a folder" had no click handler and a second, empty rules box
    was added. It draws cleanly now, with one rules box and a working button."""
    page.evaluate("() => openSettings('permissions')")              # its own page since the approvals work (18d6de94)
    page.wait_for_selector("#ruleText", timeout=10000)
    page.wait_for_timeout(400)
    got = page.evaluate("() => ({boxes: document.querySelectorAll('#rulesBox').length, add: typeof (document.querySelector('#addP') || {}).onclick})")
    assert got == {"boxes": 1, "add": "function"}, got
    assert page.errors == [], page.errors


# ================================================== persona: finance manager (UX-34 to UX-36)
LONG_CHAT = """() => { const para = 'The totals were checked against the workbook, row by row, and the deck now matches it. ';
    applyEv({type: 'user', text: 'go', i: 0}); applyEv({type: 'assistant', text: para.repeat(60), i: 1}); applyEv({type: 'turn_end', i: 2}); S.cur.view.state = 'idle'; }"""


# ---------------------------------------------------------------------------------------------------- UX-34
def test_a_suggestion_takes_its_own_room_above_the_composer(page):
    """UX-34: a suggestion ("Save this routine as a skill?") floated over the end of the chat, on top of the last lines
    of the reply. It now sits in the flow above the message box: the chat pane ends where it starts, and a reader at
    the end stays at the end."""
    _fake_chat(page)
    page.evaluate(LONG_CHAT)
    page.wait_for_timeout(300)
    page.evaluate("() => insightCard010({id: 'n-ux', title: 'Save this routine as a skill?', body: 'You did this three times.', action: {label: 'Save'}})")
    page.wait_for_selector(".nudge010")
    page.wait_for_timeout(300)
    got = page.evaluate("""() => { const n = document.querySelector('.nudge010').getBoundingClientRect(), c = document.querySelector('#conv'), r = c.getBoundingClientRect();
        return {overlap: n.top < r.bottom - 1, gap: Math.round(c.scrollHeight - c.scrollTop - c.clientHeight)}; }""")
    page.evaluate("() => document.querySelector('.nudge010')?.remove()")
    assert not got["overlap"] and got["gap"] <= 2, got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-35
@pytest.mark.parametrize("width", [1024, 1280, 1366])
def test_jump_to_latest_sits_above_the_composer(app, width):
    """UX-35: "Jump to latest" was placed 12 px from the bottom of the window, on top of the model name under the box.
    It now sits just above the composer, clear of the box, its buttons and the model name, at 1024, 1280 and 1366 px."""
    with sync_api.sync_playwright() as pw:
        page = _open(app, pw, width=width, height=768)
        try:
            _fake_chat(page)
            page.evaluate(LONG_CHAT)
            page.wait_for_timeout(300)
            page.mouse.move(width // 2, 300)
            page.mouse.wheel(0, -1500)
            page.wait_for_selector(".jumpdown:not([hidden])")
            got = page.evaluate("""() => { const r = s => document.querySelector(s).getBoundingClientRect(), j = r('.jumpdown');
                const hit = b => j.left < b.right && j.right > b.left && j.top < b.bottom && j.bottom > b.top;
                return {composer: hit(r('#composer')), model: hit(r('#mPill')), row: hit(r('#selrow')), inPane: j.bottom <= r('#conv').bottom + 1}; }""")
            assert got == {"composer": False, "model": False, "row": False, "inPane": True}, got
        finally:
            page.browser_.close()


# ---------------------------------------------------------------------------------------------------- UX-36
def test_the_context_window_line_in_settings_reads_plainly():
    """UX-36: Settings' check list said "context 1,000k (from provider); older steps are summarized above 120k
    tokens". It reads "Up to 1,000k tokens per chat (...). Past 120k, older steps are summed up to make room." The
    number and its source stay; the wording is for people."""
    sys.path.insert(0, str(REPO / "app"))
    from meshpool_harness import config as C
    os.environ["MESHPOOL_CONTEXT_WINDOW"] = "1000000"
    try:
        row = [r for r in C.system_check() if r["name"] == "Context window"][0]
    finally:
        os.environ.pop("MESHPOOL_CONTEXT_WINDOW", None)
    assert row["detail"].startswith("Up to 1,000k tokens per chat (your setting)"), row
    assert "summarized above" not in row["detail"], row


# ================================================== persona: first-time user (UX-37 to UX-40)
# ---------------------------------------------------------------------------------------------------- UX-37
def test_a_long_wait_on_the_model_says_so_in_the_chat_with_stop(page):
    """UX-37: while the model took minutes to answer, the only sign was a small "Thinking 247s" under the box. After
    20 s with no reply text and no step running, the chat says "Still waiting for the model (1 min)" with a Stop
    button; the line goes as soon as anything arrives, and never shows while a step runs. It is drawn by the existing
    tick: no timer of its own."""
    _fake_chat(page)
    got = page.evaluate("""() => { const cur = S.cur; applyEv({type: 'user', text: 'go', i: 0});
        cur.view.state = 'working'; cur.working = true; cur.since = Date.now() - 75000; cur.lastEvAt = 0;
        workTick(cur); const n = document.querySelector('#waitNote');
        const first = n ? [n.textContent, !!n.querySelector('button')] : null;
        applyEv({type: 'tool_start', id: 'x', name: 'bash', summary: 'sleep 99', i: 1}); workTick(cur);
        const duringStep = !!document.querySelector('#waitNote');
        applyEv({type: 'tool_end', id: 'x', ok: true, output: 'ok', i: 2}); cur.lastEvAt = Date.now() - 30000; workTick(cur);
        const back = !!document.querySelector('#waitNote');
        ingest(cur, {epoch: cur.epoch, total: 4, state: 'working', events: [{type: 'text_delta', text: 'Hello', i: 3}]});
        return {first, duringStep, back, gone: !document.querySelector('#waitNote')}; }""")
    assert got["first"] and "Still waiting for the model (1 min)" in got["first"][0] and got["first"][1], got
    assert not got["duringStep"] and got["back"] and got["gone"], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-38
@pytest.mark.parametrize("q,group,page_", [("folder", "permissions", "permissions"), ("approval", "permissions", "permissions"),
                                           ("permission", "permissions", "permissions"), ("ask before", "permissions", "permissions")])
def test_settings_search_finds_what_a_page_holds(page, q, group, page_):
    """UX-38: Settings search matched page names only: "folder", "approval" and "permission" found nothing, and "ask"
    found "Scheduled tasks" (t-ask-s). Each page now carries its own words, a query matches word starts, and Enter
    opens the matching page itself (Privacy > Permissions)."""
    page.evaluate("() => openSettings()")
    page.wait_for_selector("#mnavq")
    page.fill("#mnavq", q)
    shown = page.evaluate("() => [...document.querySelectorAll('.mnav [data-g]:not([hidden])')].map(b => b.dataset.g)")
    assert group in shown and "scheduled" not in shown, shown
    page.press("#mnavq", "Enter")
    page.wait_for_timeout(700)
    assert page.evaluate("() => document.querySelector('#subtabs [aria-selected=true]')?.dataset.sub") == page_
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UX-39
def test_a_long_file_name_on_a_card_ends_in_an_ellipsis(page):
    """UX-39: a file card under a reply cut a long name at its edge with no ellipsis (the name box grew to the text's
    width inside a card that hides overflow). The name now fits the card and ends in an ellipsis."""
    got = page.evaluate("""() => { const b = document.createElement('div'); b.className = 'pvcards';
        b.innerHTML = `<button class="pvcard"><span class="pvth"></span><span class="pvcm"><b>Q3_Quarterly_Review_final_version_for_the_board.pptx</b><small>Deck · .pptx</small></span></button>`;
        document.querySelector('#col').appendChild(b); const n = b.querySelector('.pvcm b'), card = b.querySelector('.pvcard').getBoundingClientRect(), r = n.getBoundingClientRect();
        const out = {inside: r.right <= card.right + 0.5, ell: getComputedStyle(n).textOverflow, cut: n.scrollWidth > n.clientWidth}; b.remove(); return out; }""")
    assert got["inside"] and got["ell"] == "ellipsis" and got["cut"], got


# ---------------------------------------------------------------------------------------------------- UX-40
def test_code_steps_are_named_in_words(page):
    """UX-40: a Code chat's steps showed their tool names ("exec command", "apply patch"), and a group with a failed
    step read "1 step, 1 failed". Steps now read "Ran a command" and "Edited files", and the group "1 step, 1 did not
    work". Display only: the tools themselves are unchanged."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'go', i: 0});
        applyEv({type: 'tool_start', id: 'a', name: 'exec_command', summary: 'ls', i: 1}); applyEv({type: 'tool_end', id: 'a', ok: true, output: 'x', i: 2});
        applyEv({type: 'tool_start', id: 'b', name: 'apply_patch', summary: 'a.py', i: 3}); applyEv({type: 'tool_end', id: 'b', ok: false, output: '[exit 1] no', i: 4});
        return [document.querySelector('#tool-a .tn').textContent, document.querySelector('#tool-b .tn').textContent, document.querySelector('#col .work .wl').textContent]; }""")
    assert got[0] == "Ran a command" and got[1] == "Edit files", got
    assert got[2] == "2 steps, 1 did not work", got
    assert page.errors == [], page.errors


def test_copy_on_your_message_gives_your_words_and_says_copied(page):
    """Copy on a message of yours puts only your words on the clipboard (not the attached-files note) and says
    "Copied". A merge put the .then(...) that shows the toast inside a line comment, so it never said so."""
    _fake_chat(page)
    got = page.evaluate("""async () => { let put = null; navigator.clipboard.writeText = async t => { put = t; };
        applyEv({type: 'user', text: "Chart this\\n\\nAttached files (in this chat's folder): sales.csv", i: 0});
        document.querySelector('#col .msg-user [data-ma=copy]').click();
        await new Promise(r => setTimeout(r, 300));
        return {put, toast: [...document.querySelectorAll('.toast')].map(t => t.textContent).join(' ')}; }""")
    assert got["put"] == "Chart this" and "Copied" in got["toast"], got
    assert page.errors == [], page.errors
