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
    page.evaluate("""() => { window.__x = []; const t = () => { __x.push(Math.round(document.querySelector('#composer').getBoundingClientRect().x));
        if (!window.__xs) requestAnimationFrame(t); }; requestAnimationFrame(t); }""")
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
