"""The 0.0.18 design pass on the desktop UI (IDs US-nn), after unslop by Damir Sokolovsky (CC BY 4.0). These tests hold
the rules the owner approved: one easing and short durations, no overshoot, no 700 weights, spacing on the 4px grid,
the product name in lowercase, hover and selected that read apart, pool blue as the only accent, text at 4.5:1 or
more in both themes, and a chat that keeps 560px beside the file preview at 1280px. Most read the UI files; the last
ones drive the real app in Chromium and are skipped when no Chromium can start here."""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui"
CSS_FILES = ("app.css", "shell.css", "memory.css")


def _css(name: str) -> str:
    return re.sub(r"/\*.*?\*/", "", (UI / name).read_text(encoding="utf-8"), flags=re.S)


ALL_CSS = "\n".join(_css(n) for n in CSS_FILES)


def _decls(text: str, props: str):
    """(property, value) for every declaration of the given properties (a regex alternation)."""
    for m in re.finditer(rf"(?<![\w-])({props})\s*:\s*([^;}}\"'`]+)", text):
        yield m.group(1), m.group(2).strip()


def _block(selector_rx: str, text: str = None) -> str:
    m = re.search(selector_rx + r"\s*\{(.*?)\}", text or ALL_CSS, re.S)
    assert m, selector_rx
    return m.group(1)


def _vars(body: str) -> dict:
    return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)}


ROOT = _vars(_block(r"(?m)^:root"))
DARK = {**ROOT, **_vars(_block(r':root\[data-theme="dark"\]'))}
LIGHT = ROOT


# ------------------------------------------------------------------------------------------------ JS strings
KEYWORDS = {"return", "typeof", "case", "in", "of", "void", "delete", "throw", "else", "new", "instanceof", "yield", "await"}


def js_strings(src: str) -> list[str]:
    """The text of every quoted string and template literal outside comments and regex literals."""
    out, pos, n = [], [0], len(src)

    def code(nested):
        depth, last = 0, ""
        while pos[0] < n:
            i = pos[0]
            c = src[i]
            if src.startswith("//", i):
                j = src.find("\n", i)
                pos[0] = n if j < 0 else j
                continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2)
                pos[0] = n if j < 0 else j + 2
                continue
            if c in "'\"":
                j = i + 1
                while j < n and src[j] != c and src[j] != "\n":
                    j += 2 if src[j] == "\\" else 1
                out.append(src[i + 1:j])
                pos[0], last = j + 1, "s"
                continue
            if c == "`":
                pos[0] = i + 1
                template()
                last = "s"
                continue
            if c == "/" and (last == "" or last in "(,=:[!&|?{};+-*%<>~^"):
                j, cls = i + 1, False
                while j < n and src[j] != "\n":
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        cls = True
                    elif src[j] == "]":
                        cls = False
                    elif src[j] == "/" and not cls:
                        break
                    j += 1
                j += 1
                while j < n and src[j].isalpha():
                    j += 1
                pos[0], last = j, "a"
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if nested and depth == 0:
                    pos[0] = i + 1
                    return
                depth -= 1
            if c.isalnum() or c in "_$":
                w = re.match(r"[\w$]+", src[i:]).group(0)
                last = "" if w in KEYWORDS else "a"
                pos[0] = i + len(w)
                continue
            if not c.isspace():
                last = c
            pos[0] = i + 1

    def template():
        buf = []
        while pos[0] < n:
            i = pos[0]
            c = src[i]
            if c == "\\":
                buf.append(src[i:i + 2])
                pos[0] = i + 2
                continue
            if c == "`":
                out.append("".join(buf))
                pos[0] = i + 1
                return
            if src.startswith("${", i):
                out.append("".join(buf))
                buf = []
                pos[0] = i + 2
                code(True)
                continue
            buf.append(c)
            pos[0] = i + 1
        out.append("".join(buf))

    code(False)
    return out


JS = {p.name: p.read_text(encoding="utf-8") for p in sorted(UI.glob("*.js"))}


# ------------------------------------------------------------------------------------------------ colour helpers
def _rgb(v: str, bg=(255, 255, 255)):
    v = v.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", v)
    if m:
        return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+))?\s*\)", v)
    assert m, v
    r, g, b = (float(m.group(i)) for i in (1, 2, 3))
    a = float(m.group(4)) if m.group(4) else 1.0
    return tuple(round(x * a + y * (1 - a)) for x, y in zip((r, g, b), bg))


def _lum(c) -> float:
    f = [x / 255 for x in c]
    f = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in f]
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]


def ratio(a, b) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# ================================================================================================ US-01 motion
def test_one_easing_and_no_spring_or_overshoot():
    """US-01: --spring overshoot ran on every button transform, beside --ease-in, a second --ease and the ease, ease-in,
    ease-out and ease-in-out keywords. One curve now, and no keyframes that overshoot, squash, rotate or pulse a ring."""
    beziers = set(re.findall(r"cubic-bezier\([^)]*\)", ALL_CSS))
    assert beziers == {"cubic-bezier(.22,1,.36,1)"}, beziers
    assert ROOT["--ease"] == "cubic-bezier(.22,1,.36,1)"
    assert "--spring" not in ALL_CSS and "--ease-in" not in ALL_CSS
    for prop, val in _decls(ALL_CSS, r"transition(?:-timing-function)?|animation(?:-timing-function)?"):
        words = set(re.findall(r"(?<![\w-])(ease|ease-in|ease-out|ease-in-out|linear|steps)(?![\w-])", val))
        assert not words or (words == {"linear"} and val.startswith("spin ")), (prop, val)
    frames = dict(re.findall(r"@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", ALL_CSS))
    for gone in ("mp-launch", "mp-stage", "mp-lightup", "mp-toast"):
        assert gone not in frames and gone not in ALL_CSS, gone
    for name, body in frames.items():
        for s in re.findall(r"scale\(([\d.]+)", body):
            assert float(s) <= 1, (name, body)
        assert "box-shadow" not in body, name
        assert "rotate" not in body or name == "spin", name
    assert not re.search(r":hover\s*\{[^}]*translateY\(-", ALL_CSS), "a hover lifts"
    assert ".send.go" not in ALL_CSS and 'classList.add("go")' not in JS["app.js"]


def _dur_ms(tok: str) -> float | None:
    m = re.fullmatch(r"([\d.]+)(ms|s)", tok)
    if m:
        return float(m.group(1)) * (1 if m.group(2) == "ms" else 1000)
    m = re.fullmatch(r"var\((--t-[\w-]+)\)", tok)
    if m:
        return _dur_ms(ROOT[m.group(1)])
    return None


def test_durations_are_200ms_or_less_except_opacity_fades():
    """US-02: motion ran from 70 to 900 ms. Hover and press take 120 ms, menus 160, panels 200. A loop over 200 ms is
    allowed only for live work, and only when it animates opacity alone or turns a spinner."""
    assert (ROOT["--t-hover"], ROOT["--t-press"], ROOT["--t-menu"], ROOT["--t-side"]) == ("120ms", "120ms", "160ms", "200ms")
    frames = dict(re.findall(r"@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", ALL_CSS))
    for prop, val in _decls(ALL_CSS, r"transition|transition-duration|animation|animation-duration"):
        for part in val.split(","):
            toks = part.split()
            durs = [d for d in (_dur_ms(t) for t in toks) if d is not None]
            if not durs:
                continue
            first = durs[0]
            if first <= 200:
                continue
            name = toks[0] if prop.startswith("animation") else ""
            body = frames.get(name, "")
            props = set(re.findall(r"([\w-]+)\s*:", body))
            assert name == "spin" or (body and props == {"opacity"}), (prop, part)


def test_reduced_motion_turns_everything_off():
    """US-03: people who ask for less motion get none, pseudo-elements included."""
    assert re.search(r"@media \(prefers-reduced-motion:reduce\)\{\*,\*::before,\*::after\{transition:none!important;"
                     r"animation:none!important\}\}", _css("app.css"))


def test_the_panel_slides_once_and_its_bar_scales_instead_of_growing():
    """US-04: the side panel and every child faded in, twice the work for one change, and .spbar animated its width
    (a layout on every frame). The panel alone slides; the bar scales."""
    assert ".panel:not([hidden])>*" not in ALL_CSS
    bar = _block(r"\.spbar i")
    assert "width .3s" not in bar and "transition:transform" in bar
    assert "scaleX(" in JS["app.js"] and 'style="width:${cx.percent}%' not in JS["app.js"]


# ================================================================================================ US-05 type
def test_no_700_weights_and_no_bricolage():
    """US-05: display headings were 700 in Bricolage Grotesque with -0.02em tracking. IBM Plex only now, display at 500
    with no negative tracking below 40px, and bold defaults to 600."""
    sources = [ALL_CSS] + [s for n, src in JS.items() for s in js_strings(src) if n != "company.js"]
    for s in sources:
        assert not re.search(r"font-weight\s*:\s*(700|800|900|bold)\b", s), s[:200]
        assert not re.search(r"(?<![\w-])font\s*:\s*(700|800|900|bold)\b", s), s[:200]
        for v in re.findall(r"letter-spacing\s*:\s*(-[\d.]+)em", s):
            assert False, f"negative tracking {v}em: {s[:120]}"
    assert re.search(r"b,strong,th,h1,h2,h3,h4,h5,h6\{font-weight:600\}", ALL_CSS)
    assert "Bricolage" not in ALL_CSS and "bricolage" not in (UI / "index.html").read_text()
    assert not list((UI / "brand" / "fonts").glob("bricolage*"))
    assert ROOT["--display"].startswith('"IBM Plex Sans"')
    for sel in (r"\.hero h1", r"\.gh h1", r"\.onb-card h1"):
        assert re.search(sel + r"\{font:500 var\(--fs-2xl\)", ALL_CSS), sel


def test_sizes_are_on_the_scale_and_the_receipt_is_the_signature():
    """US-06: off-scale sizes (10, 11, 15, 15.5, 18, 26, 32px) go; the receipt line is Plex Mono 12/16 with tabular
    figures."""
    for v in re.findall(r"font-size\s*:\s*([\d.]+)px", ALL_CSS):
        assert float(v) in (12, 13, 14, 16, 20, 28), v
    assert "15.5px" not in ALL_CSS
    rc = _block(r"\.rcpt010")
    assert "font:12px/16px var(--mono)" in rc and "font-variant-numeric:tabular-nums" in rc


# ================================================================================================ US-07 spacing
# The only values off the 4px grid: 2px nudges inside small controls, and the -1px that lays a settings sub-tab's
# underline over the row's own border.
ALLOWED_OFF_GRID = {2.0}
BORDER_OVERLAP = re.compile(r"margin-bottom:-1px$")


def _off_grid(text: str):
    for prop, val in _decls(text, r"(?:padding|margin)(?:-[a-z-]+)?|gap|row-gap|column-gap"):
        if BORDER_OVERLAP.search(f"{prop}:{val}"):
            continue
        for n in re.findall(r"(-?[\d.]+)px", val):
            v = abs(float(n))
            if v and v % 4 and v not in ALLOWED_OFF_GRID:
                yield f"{prop}:{val}"


def test_every_padding_margin_and_gap_is_on_the_4px_grid():
    """US-07: 14px had 49 uses, 18px 13, and 5, 7 and 9 were scattered. Every padding, margin and gap in the UI CSS,
    and in the inline styles the UI scripts write, is a multiple of 4 (2px nudges allowed)."""
    bad = list(_off_grid(ALL_CSS))
    for name, src in JS.items():
        for s in js_strings(src):
            if "px" in s:
                bad += [f"{name}: {d}" for d in _off_grid(s)]
    assert not bad, bad[:20]


def test_rows_in_a_list_share_one_height():
    """US-08: sidebar rows were 34px, the settings nav 34px and menu rows 34px beside 36px popovers. Navigation, chat
    rows, settings nav and menu rows are 32px."""
    assert "height:32px" in _block(r"\.nv", _css("shell.css"))
    assert "min-height:32px" in _block(r"\.th", _css("shell.css"))
    assert "height:32px" in _block(r"\.mnav button", _css("shell.css"))
    assert "min-height:32px" in _block(r"\.ctx \.mi", _css("shell.css"))


# ================================================================================================ US-09 copy
KEEP_CAPITAL = ("X-Meshpool-Token", "Meshpool outputs", "Meshpool.app")


def test_the_product_name_is_lowercase_in_every_ui_string():
    """US-09: "Meshpool" appeared in about 149 UI strings. Every string the scripts show is lowercase "meshpool" now;
    a header name, the outputs folder and the macOS app bundle keep their spelling."""
    bad = []
    for name, src in JS.items():
        for s in js_strings(src):
            t = s
            for k in KEEP_CAPITAL:
                t = t.replace(k, "")
            if re.search(r"\bMeshpool\b", t):
                bad.append(f"{name}: {s[:100]!r}")
    assert not bad, bad[:20]
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "<title>meshpool</title>" in html and not re.search(r">[^<]*\bMeshpool\b", html)
    assert not re.search(r"content\s*:\s*\"[^\"]*Meshpool", ALL_CSS)


def test_copy_has_no_greeting_and_one_cost_and_duration_format():
    """US-10: the home said "Good morning", the first run "Welcome to meshpool", costs read $0.0005 beside $0.0020 and
    durations 0.6 s beside 3 min 45 s. The receipt names the model as the composer does."""
    from meshpool_harness import receipts as RC
    comp = JS["composer.js"]
    assert "Good morning" not in comp and "greet()" not in comp and '"New chat"' in comp
    assert "<h1>Choose what meshpool runs on</h1>" in JS["app.js"] and "Welcome to" not in JS["app.js"]
    assert sum(len(re.findall("20% cheaper", s)) for s in js_strings(JS["app.js"])) == 1
    assert "Key: ${" not in JS["settings2.js"]
    assert RC.fmt_cost(0.0005) == RC.fmt_cost(0.002) == "<$0.01" and RC.fmt_cost(0.42) == "$0.42"
    assert [RC._fmt_secs(s) for s in (0.6, 42.4, 225, 180, 3900)] == ["<1 s", "42 s", "3 min 45 s", "3 min", "1 h 5 min"]
    line = RC.line({"secs": 225, "cost": 0.002, "harness": "meshpool-work", "harness_name": "Meshpool Work",
                    "model": "deepseek-v4.1-flash", "reason": "docs words (1)"})
    assert line == "3 min 45 s · <$0.01 · DeepSeek V4.1 Flash, chosen for documents", line


# ================================================================================================ US-11 colour
def test_hover_and_selected_read_apart_in_both_themes():
    """US-11: on the light sidebar hover #E8E8E8 and selected #DCDCDC looked the same, and the open chat stacked a fill,
    a 3px blue edge and a dot. Selected is a clear step past hover in both themes (luminance ratio of at least 1.08),
    and the selected row adds weight 500 and a hairline instead of the accent edge."""
    for theme in (LIGHT, DARK):
        assert ratio(_rgb(theme["--hover"]), _rgb(theme["--sel"])) >= 1.08, theme["--hover"]
    assert (LIGHT["--hover"], LIGHT["--sel"]) == ("#ECECEC", "#E2E2E2")
    on = _block(r"\.th\.on", _css("shell.css"))
    assert "inset 0 0 0 1px var(--sel-ring)" in on and "var(--pool)" not in on
    assert re.search(r"\.th\.on \.tt,[^{]*\{font-weight:500\}", ALL_CSS)
    assert 'id !== "new"' in JS["shell.js"]          # "New" is never drawn as the current page


def test_one_tab_style_and_no_focus_ring_until_the_keyboard():
    """US-12: four selected styles (a blue underline, grey tabs, a black Memory chip, the nav fill) become one --sel
    fill with ink text; the settings sub-tab underline is ink; the settings search rings only when reached by Tab."""
    for sel in (r'\.srch-tabs button:hover\{[^}]*\}\.srch-tabs button\[aria-selected="true"\]',
                r'\.gtabs button:hover\{[^}]*\}\.gtabs button\[aria-selected="true"\]',
                r'\.ptabs \.sp\{[^}]*\}\.ptabs button\[aria-selected="true"\]', r'\.mem-chips \.chip\.on'):
        assert "background:var(--sel)" in _block(sel), sel
    assert "border-bottom-color:var(--ink)" in _block(r'\.subtabs button\[aria-selected="true"\]')
    assert ".kbd .msearch:focus-within" in ALL_CSS and not re.search(r"(?<!\.kbd )\.msearch:focus-within", ALL_CSS)


# Colours that carry meaning, not decoration: GitHub's merged purple on a PR state tag, and the editor's search
# matches (amber, as in every editor).
SEMANTIC_EXTRA = {"#6B3FA0", "#C4A8F0", "#EFE7FB", "#2C2340", "rgba(232,163,61,.35)", "rgba(232,163,61,.75)",
                  "rgba(31,111,208,.30)"}


def _hue(c) -> tuple[float, float]:
    h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
    return h * 360, s * v       # hue, chroma


def test_pool_blue_is_the_only_accent_in_the_chrome():
    """US-13: two accents (pool blue and the logo's teal), a hard-coded #3FB37F and coloured gallery tiles. Every
    coloured token is pool blue or a semantic colour (add, del, warn), no rule outside the tokens writes a colour of its
    own, and teal is gone."""
    assert "--brand" not in ALL_CSS and "#3FB37F" not in ALL_CSS.upper()
    for theme in (LIGHT, DARK):
        for k, v in theme.items():
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                continue
            hue, chroma = _hue(_rgb(v))
            if chroma < 0.08:
                continue
            assert re.match(r"--(pool|focus|add|del|warn)", k), (k, v)
            if k.startswith(("--pool", "--focus")):
                assert 200 <= hue <= 225, (k, v, hue)
    rules = re.sub(r"(?s)(:root[^{]*\{.*?\}|@media \(prefers-color-scheme:dark\)\{:root[^{]*\{.*?\}\})", "", ALL_CSS)
    for c in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)", rules):
        if c in SEMANTIC_EXTRA:
            continue
        rgb = _rgb(c) if len(c) != 4 else tuple(int(ch * 2, 16) for ch in c[1:])
        assert _hue(rgb)[1] < 0.08, f"a colour outside the tokens: {c}"


def test_text_reaches_4_5_to_1_on_every_surface_in_both_themes():
    """US-14: dark text was pure #FFF on big surface steps; the new graphite ramp keeps all three text roles at 4.5:1
    or more on the page, the sidebar, cards and menus, in light and in dark."""
    for name, theme in (("light", LIGHT), ("dark", DARK)):
        assert theme["--ink"] != "#FFFFFF"
        for fg in ("--ink", "--ink-2", "--ink-3", "--pool-text"):
            for bg in ("--bg", "--side", "--surface", "--raise"):
                r = ratio(_rgb(theme[fg]), _rgb(theme[bg]))
                assert r >= 4.5, (name, fg, bg, round(r, 2))
    assert (DARK["--bg"], DARK["--side"], DARK["--surface"], DARK["--sel"]) == ("#141415", "#0f0f10", "#19191b", "#2c2c2f")
    assert DARK["--line"] == "rgba(237,237,237,.08)"


def test_one_icon_size_and_stroke_in_the_chrome():
    """US-15: icons were 12, 13, 14, 15, 16 and 18px with a 1.8 stroke. The chrome draws them at 16px with a 1.5
    stroke; only the gallery's type tiles and the file card's type icon are larger."""
    big_ok = (".mkt svg", ".gx svg", ".pvi svg")
    for m in re.finditer(r"([^{}]*svg[^{}]*)\{([^}]*)\}", ALL_CSS):
        sel, body = m.group(1).strip(), m.group(2)
        for w in re.findall(r"(?<![\w-])width:(\d+)px", body):
            assert w == "16" or any(sel.endswith(b) for b in big_ok), (sel, w)
    assert 'stroke-width="1.5"' in JS["app.js"] and 'stroke-width="1.8"' not in "".join(JS.values())
    assert not re.search(r"I\('[^']*', 1[2-58]\)", JS["app.js"])


# ================================================================================================ in the app
sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def ui_page(tmp_path_factory):
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    from tests.test_ux018 import _launch

    repo = Path(__file__).resolve().parents[2]
    tmp = tmp_path_factory.mktemp("us018")
    home, mh = tmp / "home", tmp / "mh"
    home.mkdir()
    mh.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (mh / "meshpool.config").write_text("MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:9/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_|ANTHROPIC_|CLAUDE_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(repo / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_SLOTS_DIR=str(tmp / "slots"), MH_UI_OPEN_PAGE="1")
    log = open(tmp / "ui.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(port)],
                         cwd=str(repo / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        end = time.time() + 60
        while True:
            try:
                urllib.request.urlopen(base + "/", timeout=3)
                break
            except Exception:  # noqa: BLE001
                if time.time() > end:
                    pytest.fail("the app did not start")
                time.sleep(0.3)
        with sync_api.sync_playwright() as pw:
            b = _launch(pw)
            pg = b.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); localStorage.setItem('mp_permsAsked', '1'); } catch (e) {}")
            pg.goto(base + "/")
            pg.wait_for_function("() => typeof S === 'object' && S.settings && S.threads", timeout=30000)
            pg.errors = errors
            yield pg
            b.close()
    finally:
        p.kill()
        p.wait(10)
        log.close()


def test_the_preview_at_1280_keeps_the_chat_560px_wide(ui_page):
    """US-16: at 1280px the preview squeezed the chat to about 400px and cut the model name. Below 1366px the sidebar
    folds away while the preview is open, and the chat keeps 560px or more with the model name whole."""
    pg = ui_page
    pg.set_viewport_size({"width": 1280, "height": 800})
    pg.evaluate("() => { newThread(); openPreviewCode('html', '<p>hello</p>'); }")
    pg.wait_for_timeout(500)
    got = pg.evaluate("""() => { const l = document.querySelector('#mPill .lbl'); l.textContent = 'DeepSeek V4.1 Flash';
        return {main: document.querySelector('#main').getBoundingClientRect().width, pv: !document.querySelector('#pvp').hidden,
                side: document.querySelector('#app').classList.contains('autoside'), cut: l.scrollWidth > l.clientWidth}; }""")
    assert got["pv"] and got["side"] and got["main"] >= 560 and not got["cut"], got
    pg.evaluate("() => closePreview()")
    pg.wait_for_timeout(300)
    assert not pg.evaluate("() => document.querySelector('#app').classList.contains('autoside')")
    pg.set_viewport_size({"width": 1024, "height": 700})
    pg.evaluate("() => openPreviewCode('html', '<p>hello</p>')")
    pg.wait_for_timeout(500)
    assert pg.evaluate("() => document.querySelector('#main').getBoundingClientRect().width") >= 560
    pg.evaluate("() => closePreview()")
    pg.set_viewport_size({"width": 1280, "height": 800})
    assert pg.errors == [], pg.errors


def test_home_has_no_greeting_new_is_not_selected_and_composer_buttons_are_labelled(ui_page):
    """US-17: the home title is the context, "New" is an action and is not drawn as the current page, and the
    composer's model, permission, eye and lock buttons say what they are."""
    pg = ui_page
    pg.evaluate("() => { S.mode = 'chat'; syncMode?.(); newThread(); }")
    pg.wait_for_timeout(400)
    got = pg.evaluate("""() => ({h1: document.querySelector('.hero h1').textContent.trim(),
        newOn: document.querySelector('#newThread').classList.contains('on'),
        labels: ['#mPill', '#aPill', '#xrayBtn', '#incogBtn'].map(s => { const b = document.querySelector(s);
            return b ? [b.getAttribute('aria-label') || '', b.dataset.tip || ''] : null; }),
        weights: [...document.querySelectorAll('body *')].filter(e => e.offsetParent && +getComputedStyle(e).fontWeight >= 700).length})""")
    assert got["h1"] == "New chat" and not got["newOn"], got
    assert all(lab and lab[0] and lab[1] for lab in got["labels"]), got["labels"]
    assert got["weights"] == 0, got
    assert pg.errors == [], pg.errors
