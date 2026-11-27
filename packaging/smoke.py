"""Smoke test of a built Meshpool, run with the app's own bundled Python before a release is published.

    <bundled python> packaging/smoke.py --app <the app's code folder> --version 0.0.6

Checks what a user would hit first, with no network and no paid calls:
  1. the packages the app ships with import (PDF, Word, Excel, PowerPoint, browser),
  2. the app's version is the release's version,
  3. Word and PDF files can be made,
  3a. Node, Git and Pi run from the app, the measuring fonts come from its LibreOffice, and on Windows python3 and
      MinGit's programs folder are Meshpool's own (each found by the app's own lookup),
  3b. the programs and packages the skills rely on work from the app alone: ffmpeg, pandoc, speech to text (the bundled
      model, offline), a notebook run with the bundled kernel, a chart, HEIC support,
  3c. browser use: the app's own Chromium starts hidden (it ships without the headless shell) and the browser tool
      opens, reads, types into, clicks and screenshots a page served on 127.0.0.1; the look review's browser renders it,
  4. the app's server starts, and a Meshpool Code chat gets an answer from a stand-in model (testkit/mock_llm.py).
Exits non-zero on the first failure, so the release is not published.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def step(msg):
    print(f"smoke: {msg}", flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, secs: int = 90):
    end = time.time() + secs
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            time.sleep(1)
    raise SystemExit(f"smoke: FAILED, nothing answered at {url} within {secs} s")


def run_version(name: str, cmd: list, want: str, env: dict | None = None) -> str:
    """Run a bundled program's version command; fail the release unless it exits 0 and prints `want`. Returns what it
    printed."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, env=env,
                           errors="replace")
    except OSError as e:
        raise SystemExit(f"smoke: FAILED, the bundled {name} does not start ({cmd[0]}): {e}")
    out = (r.stdout or r.stderr).strip()
    if r.returncode or want not in out:
        raise SystemExit(f"smoke: FAILED, the bundled {name} ({cmd[0]}) exited {r.returncode}: {out[-300:]!r}")
    return out


def bundled_programs():
    """Node, Git, Pi, the measuring fonts and (Windows) python3 and MinGit's programs folder, each found by the app's
    own lookup and run once, offline. Their folder names come from upstream downloads (Node's latest 22.x, MinGit,
    LibreOffice, Pi from npm, python-build-standalone): when one is renamed, as MinGit's mingw64 became ucrt64, the
    release stops here instead of on a customer's computer. LibreOffice, ffmpeg, pandoc and the speech model have
    their own steps; Chromium has the browser step."""
    from meshpool_harness import bundled, office_runtime
    from meshpool_harness.harnesses import pi_rpc
    step("bundled Node, Git, Pi and the measuring fonts")
    if not bundled.packaged():
        raise SystemExit("smoke: FAILED, the app does not count as packaged, so it would look for tools on PATH")
    node, git = bundled.node_exe(), bundled.bundled_git()
    if not node:
        raise SystemExit("smoke: FAILED, Node.js is not in the app (node/)")
    if not git:
        raise SystemExit("smoke: FAILED, Git is not in the app (git/)")
    step(f"node {run_version('Node.js', [node, '--version'], 'v')[:40]} at {node}")
    step(f"{run_version('Git', [git, '--version'], 'git version')[:60]} at {git}")
    if os.name == "nt":
        progs = [d for d in bundled.git_dirs() if d.parent.name != "usr" and d.name == "bin" and (d / "git.exe").is_file()]
        if not progs:
            raise SystemExit("smoke: FAILED, MinGit's programs folder (ucrt64/bin, mingw64/bin) is not among the folders "
                             f"the app puts on PATH: {[str(d) for d in bundled.git_dirs()]}")
        step(f"MinGit programs folder on PATH: {progs[0]}")
    cli = pi_rpc.pi_cli()
    if not cli:
        raise SystemExit("smoke: FAILED, Pi is not in the app (vendor/pi/.../dist/bundle/cli.js)")
    step(f"Pi {run_version('Pi', [node, str(cli), '--version'], '.')[:40]}")
    from engines.forge.common import font_file
    lo = office_runtime.bundled_dir().resolve()
    for face, metric in (("Calibri", "Carlito"), ("Arial", "Liberation Sans")):
        path = font_file(face, False, False, ((face, metric),))[0]
        if not path or lo not in Path(path).resolve().parents:
            raise SystemExit(f"smoke: FAILED, {face} is not measured with the {metric} inside the app's LibreOffice "
                             f"(found {path!r})")
    step("Calibri and Arial are measured with the fonts in the app's LibreOffice")
    if os.name == "nt":                         # skills and models type python3: it must be Meshpool's Python
        sh = bundled.bundled_bash()
        env = dict(os.environ, PATH=os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")]))
        out = run_version("python3", [sh, "-lc", "python3 -c 'import sys; print(sys.prefix)'"], ":", env=env)
        out = out.splitlines()[-1]                  # a login shell may print a line of its own first
        here = os.path.normcase(os.path.realpath(sys.prefix))
        if os.path.normcase(os.path.realpath(out.strip())) != here:
            raise SystemExit(f"smoke: FAILED, python3 in the agents' shell is not Meshpool's Python: {out!r}, "
                             f"want {sys.prefix}")
        step(f"python3 in the agents' shell is Meshpool's Python ({out.strip()})")


def bundled_media(proj: Path):
    """What the skills rely on (transcribe-audio, convert-files, notebooks, make-chart, image-tools) runs from the app alone."""
    import wave
    from meshpool_harness import bundled
    bundled.use_bundled_tools()
    step("bundled ffmpeg, pandoc, speech to text, notebooks")
    why = bundled.ffmpeg_needs_newer_macos()
    ff = bundled.bundled_ffmpeg()
    if not ff:
        raise SystemExit("smoke: FAILED, ffmpeg is not in the app (vendor/ffmpeg)")
    if why:
        step(f"ffmpeg skipped: {why}")
    else:
        r = subprocess.run([ff, "-version"], capture_output=True, text=True, timeout=60)
        if r.returncode or "ffmpeg version" not in r.stdout:
            raise SystemExit(f"smoke: FAILED, the bundled ffmpeg does not run: {r.stdout[:200]!r} {r.stderr[-200:]!r}")
        step(r.stdout.splitlines()[0][:80])
    pd = bundled.bundled_pandoc()
    if not pd:
        raise SystemExit("smoke: FAILED, pandoc is not in the app (vendor/pandoc)")
    src = proj / "smoke.md"
    src.write_text("# Smoke\n\nA *test*.\n", encoding="utf-8")
    r = subprocess.run([pd, str(src), "-o", str(proj / "smoke.html")], capture_output=True, text=True, timeout=60)
    if r.returncode or "<em>test</em>" not in (proj / "smoke.html").read_text(encoding="utf-8"):
        raise SystemExit(f"smoke: FAILED, the bundled pandoc does not convert: {r.stderr[-300:]!r}")
    model = bundled.whisper_model_dir()
    if not model:
        raise SystemExit("smoke: FAILED, the speech model is not in the app (vendor/whisper)")
    wav = proj / "silence.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000 * 2)
    os.environ["HF_HUB_OFFLINE"] = "1"
    from faster_whisper import WhisperModel
    segs, info = WhisperModel(str(model), device="cpu", compute_type="int8").transcribe(str(wav), vad_filter=False)
    list(segs)
    step(f"speech to text ran offline ({info.language})")
    import nbformat
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("x = 6 * 7\nprint(x)")]
    nbf = proj / "smoke.ipynb"
    nbformat.write(nb, nbf)
    r = subprocess.run([sys.executable, "-m", "nbconvert", "--to", "notebook", "--execute", "--inplace", str(nbf)],
                       capture_output=True, text=True, timeout=300)
    done = nbformat.read(nbf, as_version=4)
    if r.returncode or "42" not in str(done.cells[0].outputs):
        raise SystemExit(f"smoke: FAILED, a notebook did not run with the bundled kernel: {r.stderr[-400:]!r}")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.plot([1, 2, 3])
    plt.savefig(proj / "smoke.png")
    from pillow_heif import register_heif_opener
    register_heif_opener()
    import onnxruntime  # noqa: F401  (the voice detector)
    step("notebook, chart and HEIC support work")


_PAGE = b"""<!doctype html><html><head><meta charset=utf-8><title>Smoke page</title></head><body>
<h1>Browser smoke</h1><label>Name <input id=name aria-label=Name></label>
<button onclick="document.getElementById('out').textContent = 'Hello ' + document.getElementById('name').value">Greet</button>
<p id=out>nobody yet</p></body></html>"""


def browser(proj: Path):
    """The browser tool and the look review start the app's own Chromium, hidden, and work a local page. Offline: the
    page is served on 127.0.0.1. The app ships the full Chromium only (no headless shell), so this fails the release
    on any system where browser use could not start (v0.0.18 on macOS: it looked for chromium_headless_shell)."""
    import http.server
    import threading
    from meshpool_harness import browser_use as BU
    from meshpool_harness import bundled
    step("browser use: the app's own Chromium, hidden, on a local page")
    why = bundled.browser_needs_newer_macos()
    if why:
        step(f"browser use skipped: {why}")
        return
    exe = BU.find_playwright_chromium()
    if bundled.packaged():
        own = bundled.browsers_dir().resolve()
        if not exe or own not in Path(exe).resolve().parents:
            raise SystemExit(f"smoke: FAILED, the app's own Chromium is not found in {own} (found {exe!r})")
        if list(own.glob("chromium_headless_shell-*")):
            raise SystemExit("smoke: FAILED, the headless shell is in the app; it is meant to be left out (--no-shell)")

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_PAGE)))
            self.end_headers()
            self.wfile.write(_PAGE)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    os.environ["MH_BROWSER_HEADLESS"] = "1"
    from meshpool_harness.agent import browser_mcp_server as BM
    try:
        snap = BM.browser_open(url)
        refs = dict(re.findall(r'\[(e\d+)\] (?:input|textbox|button) "(Name|Greet)"', snap))
        refs = {v: k for k, v in refs.items()}
        if "Browser smoke" not in snap or "Name" not in refs or "Greet" not in refs:
            raise SystemExit(f"smoke: FAILED, the browser tool did not read the page: {snap[:400]!r}")
        BM.browser_type(refs["Name"], "meshpool")
        after = BM.browser_click(refs["Greet"])
        if "Hello meshpool" not in after and "Hello meshpool" not in BM.browser_snapshot():
            raise SystemExit(f"smoke: FAILED, typing and clicking in the browser did not work: {after[:400]!r}")
        shot = BM.browser_screenshot()
        if not any(isinstance(p, dict) and p.get("type") == "image" for p in shot):
            raise SystemExit(f"smoke: FAILED, the browser took no screenshot: {str(shot)[:200]!r}")
        used = dict(BU.LAST_LAUNCH)
        step(f"browser tool opened, typed, clicked and took a screenshot ({used})")
    finally:
        try:
            BM.browser_close()
        except Exception:  # noqa: BLE001
            pass
    if bundled.packaged() and Path(used.get("executable_path", "")).resolve() != Path(exe).resolve():
        raise SystemExit(f"smoke: FAILED, the browser tool started {used}, not the app's own Chromium {exe}")
    # the look review and page previews (review_interact.launch_browser, ui_audit) start it the same way
    from playwright.sync_api import sync_playwright
    from meshpool_harness.review_interact import launch_browser
    with sync_playwright() as pw:
        b = launch_browser(pw)
        try:
            pg = b.new_page()
            pg.goto(url)
            if pg.inner_text("h1") != "Browser smoke":
                raise SystemExit("smoke: FAILED, the look review's browser did not render the page")
        finally:
            b.close()
    srv.shutdown()
    step("look review browser rendered the page")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="the built app's code folder (holds meshpool_harness)")
    ap.add_argument("--version", required=True)
    ap.add_argument("--launch", nargs="*", default=None,
                    help="command that starts Python with the app on its path (default: this Python)")
    a = ap.parse_args()
    app = Path(a.app).resolve()
    sys.path.insert(0, str(app))
    env = dict(os.environ, PYTHONPATH=str(app), PYTHONUTF8="1")

    step("imports")
    import importlib
    for mod in ("meshpool_harness", "pptx", "docx", "openpyxl", "reportlab", "pypdf", "pypdfium2", "pdfminer",
                "PIL", "lxml", "yaml", "pandas", "playwright"):
        importlib.import_module(mod)
    import meshpool_harness as MH
    if MH.__version__ != a.version:
        raise SystemExit(f"smoke: FAILED, the app says version {MH.__version__}, the release is {a.version}")
    if os.name == "nt":                         # the agents' shell is the bundled Git Bash, never a PowerShell fallback
        from meshpool_harness import bundled
        from meshpool_harness.config import find_bash
        sh = find_bash()
        if not sh or not bundled.bundled_bash() or Path(sh).name.lower() != "bash.exe":
            raise SystemExit(f"smoke: FAILED, no bundled bash for the agents' shell (found {sh!r})")
        out = subprocess.run([sh, "-lc", "echo $BASH_VERSION; ls / >/dev/null && grep -q . <<< x && echo ok"],
                             capture_output=True, text=True, timeout=60)
        if "ok" not in out.stdout:
            raise SystemExit(f"smoke: FAILED, the bundled bash does not run: {out.stdout!r} {out.stderr[-300:]!r}")
        step(f"bash {out.stdout.split()[0] if out.stdout.split() else '?'} at {sh}")

    step("Word and PDF files")
    tmp = Path(tempfile.mkdtemp(prefix="meshpool-smoke-"))
    proj, home = tmp / "proj", tmp / "home"
    proj.mkdir()
    home.mkdir()
    os.environ.update(MESHPOOL_PROJECT=str(proj), MESHPOOL_HOME=str(home), MH_CLARITY="0")
    from meshpool_harness.agent import office_mcp_server as O
    for fn, name in ((O.make_document, "smoke.docx"), (O.make_pdf, "smoke.pdf")):
        out, bad = fn("# Smoke test\n\nRevenue grew 12% to $4.2m.\n\n| a | b |\n|---|---|\n| 1 | 2 |", name)
        made = proj / name            # a name the user gives is saved where they asked (the project folder)
        if bad or not made.exists() or not made.stat().st_size:
            raise SystemExit(f"smoke: FAILED, {name}: {out}")

    bundled_programs()
    bundled_media(proj)
    browser(proj)

    step("kept LibreOffice session")
    # One LibreOffice for many conversions (meshpool_harness/lo_session.py). It needs LibreOffice's own Python with
    # uno next to the bundled copy; without it every conversion quietly falls back to a fresh LibreOffice, so the
    # release must fail here instead.
    from meshpool_harness import lo_session, office_runtime
    from engines.forge.common import soffice_convert
    exe = office_runtime.find_soffice()
    if not exe or not lo_session.real_office(exe):
        raise SystemExit(f"smoke: FAILED, no bundled LibreOffice ({exe!r})")
    if not lo_session.lo_python(exe):
        raise SystemExit(f"smoke: FAILED, LibreOffice's own Python with uno is missing next to {exe}")
    os.environ["MH_CONVERT_CACHE"] = "0"
    for i in range(2):
        pdf = soffice_convert(str(proj / "smoke.docx"), "pdf", str(tmp / f"lo{i}"))
        if pdf.read_bytes()[:4] != b"%PDF":
            raise SystemExit("smoke: FAILED, the kept LibreOffice did not write a PDF")
    st = lo_session.stats().get(exe) or {}
    if st.get("starts") != 1 or st.get("jobs") != 2:
        raise SystemExit(f"smoke: FAILED, the kept LibreOffice session did not do the work: {st}")
    lo_session.close_all()
    os.environ.pop("MH_CONVERT_CACHE")
    step("kept LibreOffice converted 2 files with 1 start")

    step("server and a chat")
    mport, uport = free_port(), free_port()
    (home / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    launch = a.launch or [sys.executable]
    procs = [subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport), str(tmp / "mock.jsonl")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    log = open(tmp / "ui.log", "w")
    # The app puts its own Python first on PATH for the agent's shell (packaging/linux/meshpool, desktop
    # _mac_environment); started directly here, the server gets the same, so `python3 hello.py` finds it in a bare system.
    app_path = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", os.environ.get("PATH", ""))])
    procs.append(subprocess.Popen(launch + ["-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                                  env=dict(env, MESHPOOL_HOME=str(home), PYTHONUNBUFFERED="1", PATH=app_path), stdout=log, stderr=subprocess.STDOUT))
    try:
        base = f"http://127.0.0.1:{uport}"
        # The page carries the API token only for the launch key the app prints at startup (?k=...)
        end, key = time.time() + 90, ""
        while time.time() < end and not key:
            m = re.search(r"\?k=([\w-]+)", (tmp / "ui.log").read_text(errors="replace"))
            key = m.group(1) if m else ""
            if not key:
                time.sleep(0.5)
        if not key:
            raise SystemExit("smoke: FAILED, the app printed no launch address: " + (tmp / "ui.log").read_text()[-600:])
        page = wait_http(f"{base}/?k={key}")
        tok = re.search(r'token: "([^"]+)"', page).group(1)

        def api(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        sid = api("/api/agent", {"workdir": str(proj), "harness": "meshpool-code", "mode": "auto"})["id"]
        time.sleep(1)
        api(f"/api/agent/{sid}/send", {"text": "Write hello and run it"})
        end, v = time.time() + 120, {}
        while time.time() < end:
            time.sleep(1)
            v = api(f"/api/agent/{sid}?since=0")
            if v.get("state") == "idle" and any(e.get("type") == "assistant" for e in v.get("events", [])):
                break
        replies = [e.get("text", "") for e in v.get("events", []) if e.get("type") == "assistant" and e.get("text")]
        errors = [e.get("text", "") for e in v.get("events", []) if e.get("type") == "error"]
        if replies and ("unknown tool" in replies[-1] or "hello from meshpool" not in replies[-1]):
            raise SystemExit(f"smoke: FAILED, the chat's tool call did not run: {replies[-1][:160]!r}")
        if not replies or errors:
            raise SystemExit(f"smoke: FAILED, the chat did not answer (state {v.get('state')}, errors {errors[:2]})")
        step(f"chat answered: {replies[-1][:80]!r}")
    except SystemExit:
        log.flush()
        print((tmp / "ui.log").read_text(errors="replace")[-3000:])
        raise
    finally:
        for p in procs:
            p.kill()
    step(f"PASSED (Meshpool {MH.__version__})")


if __name__ == "__main__":
    main()
