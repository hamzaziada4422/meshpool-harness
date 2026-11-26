"""Look at what it made (agent/look.py, 0.0.18): when a turn makes or changes a web page, document, deck, sheet or chart,
meshpool renders it, measures what is wrong and gives the model one review message (the request, the screenshots when
the model takes images, the findings as text), then looks again after the fix and ends with a "Checked:" line.

No network and no paid model: the model is a script, or testkit/mock_llm.py on localhost. The web tests use the
headless Chromium meshpool already uses (PLAYWRIGHT_BROWSERS_PATH, or MH_TEST_CHROMIUM) and are skipped without one."""
import json
import os
import socket
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness.agent import core, look as L  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BUGGY = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
         "content=\"width=device-width\"><title>Petal</title></head><body><h1>Petal flowers</h1>"
         "<div style=\"width:2000px\">Fresh bouquets every morning</div><script>initGallery();</script></body></html>\n")
FIXED = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
         "content=\"width=device-width\"><title>Petal</title></head><body><h1>Petal flowers</h1>"
         "<div style=\"max-width:100%\">Fresh bouquets every morning</div></body></html>\n")
ASK = "make a landing page for a flower shop called Petal"


def _have_browser() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    from meshpool_harness import browser_use as BU
    return bool(os.environ.get("MH_TEST_CHROMIUM") or BU.find_playwright_chromium())


needs_browser = pytest.mark.skipif(not _have_browser(), reason="no headless Chromium on this computer")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_LOOK_REVIEW", "1")
    monkeypatch.setenv("MH_GRADER", "0")
    monkeypatch.delenv("MH_VISION", raising=False)
    C.ensure_home()


@pytest.fixture()
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


def text_of(m) -> str:
    c = m.get("content")
    return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict))


def reviews(a) -> list:
    return [m for m in a.messages if m.get("role") == "user" and "look at what you made" in text_of(m)]


class Model:
    """A scripted model. Each entry is a reply, or a callable(agent_tools) -> reply (to pick the write tool)."""
    def __init__(self, monkeypatch, replies):
        self.replies, self.bodies = list(replies), []
        model = self

        def fake(self_, base, key, body, stream):
            model.bodies.append(body)
            nxt = model.replies.pop(0) if model.replies else say("Done.")
            if callable(nxt):
                nxt = nxt({t["function"]["name"] for t in body.get("tools") or []})
            msg = say(nxt) if isinstance(nxt, str) else nxt
            return {"choices": [{"message": msg, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, False
        monkeypatch.setattr(core.Agent, "_post", fake)


def write(path, content, cid="w1"):
    """A file write in whatever tool set the chat offers."""
    def pick(names):
        if "write_file" in names:
            return call("write_file", {"path": path, "content": content}, cid)
        if "apply_patch" in names:
            body = "".join("+" + ln + "\n" for ln in content.splitlines())
            return call("apply_patch", {"input": f"*** Begin Patch\n*** Add File: {path}\n{body}*** End Patch\n"}, cid)
        if "exec_command" in names:
            return call("exec_command", {"cmd": f"cat > {path} <<'EOF'\n{content}EOF"}, cid)
        return call("shell", {"command": ["bash", "-lc", f"cat > {path} <<'EOF'\n{content}EOF"]}, cid)
    return pick


def overwrite(path, content, cid="w2"):
    """Replace a file that exists (apply_patch needs Update for that)."""
    def pick(names):
        if "write_file" in names:
            return call("write_file", {"path": path, "content": content}, cid)
        if "exec_command" in names:
            return call("exec_command", {"cmd": f"cat > {path} <<'EOF'\n{content}EOF"}, cid)
        return call("shell", {"command": ["bash", "-lc", f"cat > {path} <<'EOF'\n{content}EOF"]}, cid)
    return pick


def agent(proj, profile="work", **kw):
    ev = []
    return core.Agent(str(proj), emit=ev.append, mode="auto", profile=profile, **kw), ev


def fake_render(findings_per_call, shots=None):
    """A render_web stand-in: returns the next findings list (the last one repeats) and counts its calls."""
    calls = []

    def render(url, out_dir, tag="r1", request="", **kw):
        calls.append({"url": url, "tag": tag, "request": request})
        f = findings_per_call[min(len(calls) - 1, len(findings_per_call) - 1)]
        return {"findings": [dict(x) for x in f], "shots": list(shots or []), "steps": [], "interact": []}
    render.calls = calls
    return render


OVER = {"rule": "sideways-scroll", "sel": "body", "msg": "the page scrolls sideways by 720px", "views": ["desktop", "phone"]}
CONSOLE = {"rule": "console", "sel": "", "msg": "script error: initGallery is not defined", "views": ["desktop", "phone"]}


def _chrome_children() -> list:
    import psutil
    out = []
    for p in psutil.Process(os.getpid()).children(recursive=True):
        try:
            if "chrom" in p.name().lower() or "headless" in p.name().lower():
                out.append(p)
        except psutil.Error:
            pass
    return out


# ---------- the review round on a real page ------------------------------------------------------------------------
@needs_browser
@pytest.mark.parametrize("profile", ["work", "code"])
def test_overflow_and_console_error_reach_the_review_and_the_fix_is_checked(proj, monkeypatch, profile):
    Model(monkeypatch, [write("index.html", BUGGY), "Made index.html.", overwrite("index.html", FIXED),
                        "Fixed the wide banner and the script. The page is index.html."])
    a, ev = agent(proj, profile)
    final = a.send(ASK)
    rv = reviews(a)
    assert len(rv) == 1, [text_of(m)[:200] for m in a.messages if m["role"] == "user"]
    t = text_of(rv[0])
    assert ASK in t                                                    # the user's request
    assert "sideways" in t and "initGallery" in t                      # both measured findings
    assert "Does it do what was asked" in t                            # the rubric
    assert (proj / "index.html").read_text() == FIXED                  # the model's fix was applied
    assert "Checked: looked at the page at desktop and phone width; fixed" in final, final
    assert "the page scrolled sideways" in final and "a console error" in final
    assert not _chrome_children()                                      # no browser left running
    assert (Path(os.environ["MESHPOOL_HOME"]) / "look" / a.id / "t1-r1-desktop.jpg").is_file()
    assert (Path(os.environ["MESHPOOL_HOME"]) / "look" / a.id / "t1-r2-phone.jpg").is_file()   # looked again
    rec = [e for e in ev if e["type"] == "receipt"]
    assert rec and "looked at the page at desktop and phone width" in rec[-1]["text"]


@needs_browser
def test_real_render_closes_its_browser_and_measures(proj):
    (proj / "p.html").write_text(BUGGY.replace("<h1>", "<p style=\"color:#ccc\">faint</p><p>Lorem ipsum</p><h1>"))
    r = L.render_web((proj / "p.html").as_uri(), proj.parent / "shots", "r1", ASK)
    rules = {f["rule"] for f in r["findings"]}
    assert {"sideways-scroll", "console", "contrast", "left-over-text"} <= rules, r["findings"]
    assert [lbl.split()[0] for lbl, _ in r["shots"]][:2] == ["desktop", "phone"]
    assert len(r["shots"]) <= 4
    assert not _chrome_children()


@needs_browser
def test_wrapped_inline_labels_are_not_called_overlapping(proj):
    """At phone width an inline label wraps onto the next line; its bounding box then covers the label beside it. That
    is not an overlap a person sees, so it must not be reported (the review would send the model after a ghost)."""
    (proj / "f.html").write_text(DEAD)
    r = L.render_web((proj / "f.html").as_uri(), proj.parent / "shots", "r1", ASK)
    assert not [f for f in r["findings"] if f["rule"] == "overlap"], r["findings"]
    (proj / "o.html").write_text(BUGGY.replace("<h1>", '<div style="position:relative;height:40px"><span style="position:'
                                               'absolute;top:0">Order roses</span><span style="position:absolute;top:3px;'
                                               'left:6px">Free delivery</span></div><h1>'))
    r = L.render_web((proj / "o.html").as_uri(), proj.parent / "shots", "r2", ASK)
    assert [f for f in r["findings"] if f["rule"] == "overlap"]           # a real overlap is still found


# ---------- images or text ------------------------------------------------------------------------------------------
def _shots(tmp_path):
    from PIL import Image
    out = []
    for n in ("desktop", "phone"):
        f = tmp_path / f"{n}.png"
        Image.new("RGB", (40, 30), "white").save(f)
        out.append((n, f))
    return out


def test_review_carries_two_images_when_the_model_takes_images(proj, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "render_web", fake_render([[OVER, CONSOLE], []], _shots(tmp_path)))
    Model(monkeypatch, [write("index.html", BUGGY), "Made it.", overwrite("index.html", FIXED), "Fixed."])
    a, _ = agent(proj)
    a.send(ASK)
    rv = reviews(a)
    assert len(rv) == 1 and isinstance(rv[0]["content"], list)
    assert sum(p.get("type") == "image_url" for p in rv[0]["content"]) == 2
    assert "sideways" in text_of(rv[0])                                 # findings as text in every case


def test_second_round_replaces_the_first_rounds_pictures(proj, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "render_web", fake_render([[OVER]], _shots(tmp_path)))
    Model(monkeypatch, [write("index.html", BUGGY), "Made it.", overwrite("index.html", BUGGY + "<!-- 1 -->\n"),
                        "Tried.", overwrite("index.html", BUGGY + "<!-- 2 -->\n", "w3"), "Tried again."])
    a, _ = agent(proj)
    a.send(ASK)
    assert len(reviews(a)) == 2
    imgs = [p for m in a.messages if isinstance(m.get("content"), list) for p in m["content"]
            if p.get("type") == "image_url"]
    assert len(imgs) == 2                                               # the request stays small


def test_review_is_text_only_when_the_model_cannot_take_images(proj, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "render_web", fake_render([[OVER, CONSOLE], []], _shots(tmp_path)))
    Model(monkeypatch, [write("index.html", BUGGY), "Made it.", overwrite("index.html", FIXED), "Fixed."])
    a, _ = agent(proj)
    from meshpool_harness import providers as PV
    a._no_vision_models = {PV.split(a.current_model())[1]}             # this chat's model refused images before
    a.send(ASK)
    rv = reviews(a)
    assert len(rv) == 1 and isinstance(rv[0]["content"], str)
    assert "sideways" in rv[0]["content"] and "initGallery" in rv[0]["content"]
    assert "cannot take images" in rv[0]["content"]


# ---------- limits ----------------------------------------------------------------------------------------------------
def test_at_most_two_review_rounds(proj, monkeypatch):
    render = fake_render([[OVER]])                                      # the problem never goes away
    monkeypatch.setattr(L, "render_web", render)
    page = [BUGGY + f"<!-- {i} -->\n" for i in range(5)]
    Model(monkeypatch, [write("index.html", page[0]), "Made it.",
                        overwrite("index.html", page[1], "w2"), "Tried.",
                        overwrite("index.html", page[2], "w3"), "Tried again.",
                        overwrite("index.html", page[3], "w4"), "And again."])
    a, _ = agent(proj)
    final = a.send(ASK)
    assert len(reviews(a)) == 2
    assert len(render.calls) == 3                                       # looked, looked again, a last look
    assert "still open: the page scrolled sideways" in final, final


def test_no_second_look_when_nothing_changed_after_the_review(proj, monkeypatch):
    render = fake_render([[OVER]])
    monkeypatch.setattr(L, "render_web", render)
    Model(monkeypatch, [write("index.html", BUGGY), "Made it.", "It is right as it is: the banner is meant to scroll."])
    a, _ = agent(proj)
    final = a.send(ASK)
    assert len(reviews(a)) == 1 and len(render.calls) == 1
    assert "Checked: looked at the page" in final and "still open" in final


# ---------- when it does not run ---------------------------------------------------------------------------------------
def test_off_switch(proj, monkeypatch):
    render = fake_render([[OVER]])
    monkeypatch.setattr(L, "render_web", render)
    monkeypatch.setenv("MH_LOOK_REVIEW", "0")
    Model(monkeypatch, [write("index.html", BUGGY), "Made it."])
    a, _ = agent(proj)
    assert a.send(ASK) == "Made it."
    assert not reviews(a) and not render.calls


def test_settings_switch_turns_it_off(monkeypatch):
    from meshpool_harness.ui import settings_v2 as SV
    monkeypatch.delenv("MH_LOOK_REVIEW", raising=False)
    assert L.enabled()                                                  # on by default
    row = next(c for c in SV.capabilities() if c["id"] == "look")
    assert row["name"] == "Look at web pages it makes" and row["on"]
    SV.set_capability("look", False)
    assert C.read().get("MH_LOOK_REVIEW") == "0" and not L.enabled()
    SV.set_capability("look", True)
    assert L.enabled()


def test_plain_answer_has_no_review(proj, monkeypatch):
    render = fake_render([[OVER]])
    monkeypatch.setattr(L, "render_web", render)
    monkeypatch.setattr(L, "render_doc", render)
    Model(monkeypatch, ["Four."])
    a, _ = agent(proj)
    assert a.send("what is 2+2?") == "Four."
    assert not reviews(a) and not render.calls


@pytest.mark.parametrize("path,content", [("calc.py", "def add(a, b):\n    return a + b\n"),
                                          ("server.js", "module.exports = (a, b) => a + b;\n"),
                                          ("notes.md", "one line\n")])
def test_code_or_text_change_with_no_page_has_no_review(proj, monkeypatch, path, content):
    render = fake_render([[OVER]])
    monkeypatch.setattr(L, "render_web", render)
    monkeypatch.setattr(L, "render_doc", render)
    Model(monkeypatch, [write(path, content), "Done."])
    a, _ = agent(proj, "code")
    assert a.send("add a helper") == "Done."
    assert (proj / path).exists() and not reviews(a) and not render.calls


# ---------- what it looks at ---------------------------------------------------------------------------------------------
class _WS:
    def __init__(self):
        self.turn_files, self.commands = set(), []


class _A:
    def __init__(self, root):
        self.root, self.ws, self.trace = Path(root), _WS(), []


def test_targets(proj):
    a = _A(proj)
    g = {"t0": time.time() - 5}
    (proj / "site").mkdir()
    (proj / "site" / "index.html").write_text('<link rel="stylesheet" href="style.css"><h1>x</h1>')
    (proj / "site" / "style.css").write_text("h1{color:red}")
    t = L.web_target(a, g, [proj / "site" / "style.css"])                # a stylesheet: the page that uses it
    assert t["url"].endswith("/site/index.html")
    (proj / "src").mkdir()
    (proj / "src" / "App.tsx").write_text("export default () => <div/>")
    t = L.web_target(a, g, [proj / "src" / "App.tsx"])                   # an app with no dev server: said plainly
    assert "dev server" in t["none"]
    vite = proj / "index.html"
    vite.write_text('<div id="root"></div><script type="module" src="/src/main.tsx"></script>')
    assert "none" in L.web_target(a, g, [vite])                         # a bundler's shell is not a page on disk
    assert L.web_target(a, g, [proj / "lib.js"]) is None                 # a script with no page: not UI
    assert L.web_target(a, g, [proj / "tests" / "app.test.js"]) is None


def test_dev_server_the_turn_started_is_used_and_none_is_started(proj, monkeypatch):
    import threading
    a = _A(proj)
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        a.ws._bg = {"bg1": {"p": p, "t0": time.time(), "lock": threading.Lock(),
                            "text": ["  VITE ready\n", "  ➜  Local:   \x1b[36mhttp://localhost:5173/\x1b[0m\n"]}}
        t = L.web_target(a, {"t0": time.time() - 5}, [proj / "src" / "App.tsx"])
        assert t["url"] == "http://localhost:5173/" and t["served"]
    finally:
        p.kill()
        p.wait(10)
    t = L.web_target(a, {"t0": time.time() - 5}, [proj / "src" / "App.tsx"])   # it stopped: nothing to render
    assert "none" in t


# ---------- documents, decks and sheets ---------------------------------------------------------------------------------
def _docx(path):
    import docx
    d = docx.Document()
    d.add_heading("Spend report", 1)
    d.add_paragraph("Cache hit 82%")
    d.save(path)


def test_docx_made_in_a_chat_gets_a_render_check(proj, monkeypatch):
    calls = []

    def render_doc(path, out_dir, tag="r1"):
        calls.append(path)
        return {"findings": [], "shots": [], "text": "[page 1 of 1]\nSpend report\nCache hit 82%"}
    monkeypatch.setattr(L, "render_doc", render_doc)
    script = ("import docx; d = docx.Document(); d.add_heading('Spend report', 1); d.add_paragraph('Cache hit 82%'); "
              "d.save('spend.docx')")

    def make(names):
        if "exec_command" in names:
            return call("exec_command", {"cmd": f"python3 -c \"{script}\""})
        return call("bash" if "bash" in names else "shell",
                    {"command": f"python3 -c \"{script}\""} if "bash" in names else
                    {"command": ["bash", "-lc", f"python3 -c \"{script}\""]})
    Model(monkeypatch, [make, "Saved spend.docx.", "It matches the request. Saved spend.docx."])
    a, _ = agent(proj)
    final = a.send("write a word doc with the spend report")
    assert (proj / "spend.docx").exists()
    assert calls and calls[0].endswith("spend.docx")
    rv = reviews(a)
    assert len(rv) == 1 and "spend.docx" in text_of(rv[0]) and "Spend report" in text_of(rv[0])
    assert "Checked: looked at spend.docx" in final


def test_no_second_render_when_engine_qa_already_ran(proj):
    _docx(proj / "deck.docx")
    a = _A(proj)
    a.trace = [{"tool": "bash", "ok": True}, {"tool": "mcp__meshpool-office__check_document", "ok": True}]
    assert L.doc_target(a, {"t0": time.time() - 5, "trace0": 0}, [proj / "deck.docx"]) is None
    a.trace.append({"tool": "edit", "ok": True})                         # changed after the check: look again
    assert L.doc_target(a, {"t0": time.time() - 5, "trace0": 0}, [proj / "deck.docx"])["file"].endswith("deck.docx")


@pytest.mark.skipif(not (os.environ.get("MH_ALLOW_SYSTEM_SOFFICE") and __import__("shutil").which("soffice")),
                    reason="LibreOffice not available")
def test_real_docx_render_through_libreoffice(proj):
    _docx(proj / "r.docx")
    from meshpool_harness import lo_session
    try:
        r = L.render_doc(str(proj / "r.docx"), proj.parent / "shots")
    finally:
        lo_session.close_all()               # the kept LibreOffice: later tests count this process's children
    assert not r.get("error"), r
    assert r["shots"] and "Spend report" in r["text"]


# ---------- the interaction check (review_interact.exercise) -----------------------------------------------------------
def test_interaction_findings_and_shots_join_the_review(proj, tmp_path, monkeypatch):
    shots = _shots(tmp_path)
    step_shots = []
    for i in range(3):
        f = tmp_path / f"step{i}.png"
        f.write_bytes(shots[0][1].read_bytes())
        step_shots.append(str(f))
    seen = {}

    def exercise(target, request, out_dir, *, browser=None, widths=(1280, 390), max_steps=12, timeout_s=45):
        seen.update(target=target, request=request, browser=browser, widths=widths)
        return {"steps": [{"action": "click Order now", "ok": False, "note": "nothing happened", "shot": step_shots[0]},
                          {"action": "type an email", "ok": True, "note": "", "shot": step_shots[1]}],
                "findings": ["the Order now button gives no feedback"], "shots": step_shots,
                "console_errors": ["TypeError: cart is null"], "ms": 900}
    monkeypatch.setitem(sys.modules, "meshpool_harness.review_interact", types.SimpleNamespace(exercise=exercise))
    res = {"findings": [OVER], "shots": shots}
    it = L._interact(str(proj / "index.html"), ASK, str(tmp_path), "BROWSER")
    assert seen["browser"] == "BROWSER" and seen["widths"] == (1280, 390) and seen["request"] == ASK
    res.update(interact=it["findings"], steps=it["steps"],
               shots=shots + [("using it", Path(s)) for s in it["shots"][:2]])
    msg = L.message("web", {"label": "index.html", "files": []}, res, ASK, blind=False)
    t = text_of(msg)
    assert "the Order now button gives no feedback" in t and "FAILED: click Order now" in t
    assert "keyboard" in t and "feedback" in t                         # the rubric asks about use, not only looks
    assert sum(p.get("type") == "image_url" for p in msg["content"]) == 4   # never more than 4 pictures


def test_missing_interaction_module_means_no_interaction_checks(monkeypatch):
    monkeypatch.setitem(sys.modules, "meshpool_harness.review_interact", None)   # import fails
    assert L._interact("x.html", ASK, "/tmp", None) == {"steps": [], "findings": [], "shots": [],
                                                        "console_errors": [], "ms": 0}


def test_broken_interaction_module_does_not_break_the_review(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setitem(sys.modules, "meshpool_harness.review_interact", types.SimpleNamespace(exercise=boom))
    r = L._interact("x.html", ASK, "/tmp", None)
    assert r["findings"] == [] and "RuntimeError" in r["error"]


# ---------- the mock model and -p -----------------------------------------------------------------------------------------
def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def mock_llm(tmp_path, monkeypatch):
    port = _port()
    log = tmp_path / "mock.jsonl"
    p = subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(port), str(log)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    C.write({"MESHPOOL_API_KEY": "test-key-1234", "MESHPOOL_BASE_URL": f"http://127.0.0.1:{port}/v1"})
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MESHPOOL_BASE_URL", f"http://127.0.0.1:{port}/v1")
    yield port, log
    p.kill()


@needs_browser
def test_mock_model_chat_turn_is_reviewed_and_fixed(proj, mock_llm):
    a, _ = agent(proj, "work")
    final = a.send("landing page demo: " + ASK)
    assert len(reviews(a)) == 1
    assert "Fresh bouquets" in (proj / "index.html").read_text() and "initGallery" not in (proj / "index.html").read_text()
    assert "Checked: looked at the page at desktop and phone width; fixed" in final, final


@needs_browser
def test_print_mode_reviews_without_an_approval_prompt(proj, tmp_path, mock_llm):
    port, log = mock_llm
    env = dict(os.environ, MESHPOOL_API_KEY="test-key-1234", MESHPOOL_BASE_URL=f"http://127.0.0.1:{port}/v1",
               MH_LOOK_REVIEW="1", PYTHONPATH=str(REPO / "app"))
    r = subprocess.run([sys.executable, "-m", "meshpool_harness", "-p", "landing page demo: " + ASK, "--dir", str(proj),
                        "--permission-mode", "edits", "--no-mcp"], cwd=str(proj), env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    assert "Checked: looked at the page at desktop and phone width" in r.stdout, (r.stdout[-2000:], r.stderr[-2000:])
    assert "initGallery" not in (proj / "index.html").read_text()
    sent = [json.loads(x) for x in log.read_text().splitlines()]
    assert len(sent) >= 4                                               # write, answer, review: fix, answer
    assert "Allow" not in r.stdout and "approve" not in r.stdout.lower()


DEAD = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
        "content=\"width=device-width\"><title>Petal</title></head><body><main><h1>Petal flowers</h1>"
        "<p>Fresh bouquets every morning.</p><button id=\"order\">Order now</button>"
        "<h2>Contact us</h2><form id=\"contact\" onsubmit=\"event.preventDefault()\">"
        "<label>Name <input name=\"name\"></label><label>Email <input name=\"email\"></label>"
        "<button type=\"submit\">Send</button></form></main></body></html>\n")


@needs_browser
def test_dead_button_and_unchecked_form_reach_the_review(proj, monkeypatch):
    """End to end with the real interaction check (review_interact.exercise), in the same browser."""
    pytest.importorskip("meshpool_harness.review_interact")
    Model(monkeypatch, [write("index.html", DEAD), "Made index.html.", "Noted. The page is index.html."])
    a, _ = agent(proj)
    a.send(ASK + " with an order button and a contact form")
    rv = reviews(a)
    assert len(rv) == 1
    t = text_of(rv[0])
    assert "'Order now' button does nothing when clicked" in t, t
    assert "empty fields" in t, t                                      # the form takes empty fields without a word
    assert sum(p.get("type") == "image_url" for p in rv[0]["content"]) <= 4
    assert not _chrome_children()


def test_the_review_message_says_lowercase_meshpool_and_is_still_a_harness_note():
    """The notes the harness writes into the chat are skipped when it looks for what the user asked: that must hold for
    the review's lowercase "[meshpool]" too."""
    from meshpool_harness.agent import look as L, grader
    from meshpool_harness.agent.core import _has_images
    assert "[Meshpool]" not in L.AGAIN
    msg = L.message("web", {"label": "index.html", "url": "file:///x/index.html", "files": ["x/index.html"]},
                    {"findings": [], "steps": [], "shots": []}, "make a page", False)
    first = msg["content"] if isinstance(msg["content"], str) else msg["content"][0]["text"]
    assert first.startswith("[meshpool] Before you call this done")
    msgs = [{"role": "user", "content": "make a page"}, {"role": "assistant", "content": "Made it."}, msg]
    assert grader.requests(msgs, 0) == ["make a page"]
    assert _has_images(msgs) is False
