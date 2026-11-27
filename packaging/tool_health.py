"""Tool health check: call every tool meshpool's agent can be offered, once, for real, and check the result.

    <bundled python> packaging/tool_health.py --app <the app's code folder> [--version 0.0.19]

Run with the app's own Python, like packaging/smoke.py. Everything happens in a temp project and a temp data folder,
offline: a stand-in model (testkit/mock_llm.py), a local web server for the web and browser tools (it also answers
as a SearXNG search API), and the HTTP proxy pointed at a closed port, so nothing reaches the internet by accident.

The tools are called the way a chat calls them: core tools through the agent's own dispatch (Agent._run_tool), MCP
tools through the app's MCP manager, which starts the built-in servers from the default mcp.json with this Python.
Every tool the agent can be offered must have a check here: a tool with none is a FAIL, so a new tool cannot ship
without one. The inventory is docs/dev/TOOLS_HEALTH.md.

Prints one line per tool: ok, FAIL with the error, or skip with the reason ("needs network" for tools that need the
live internet or an account, "not on this platform" for computer use on Linux after it has said so plainly).
Exits 1 on any FAIL. --skip NAME (repeatable, or a prefix ending in *) leaves a tool out and reports it as skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS: list[tuple[str, str, str]] = []          # (tool, status, detail)
T0 = time.time()


class Fail(Exception):
    pass


class Skip(Exception):
    pass


def line(tool: str, status: str, detail: str = "") -> None:
    detail = re.sub(r"\s+", " ", str(detail or "")).strip()
    RESULTS.append((tool, status, detail))
    tag = {"ok": "ok  ", "FAIL": "FAIL", "skip": "skip"}.get(status, status)
    print(f"{tag} {tool}" + (f": {detail[:300]}" if detail else ""), flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- the local web: pages for the browser and web tools, and a SearXNG-style search API ----------------------

FILLER = ("This page is served by the meshpool tool health check on this computer. It has enough words for the "
          "web tools to treat it as a real article, so the reader keeps it instead of dropping it as too short. ")


def page(n: int) -> str:
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Health page {n}</title>"
            "<meta name=\"viewport\" content=\"width=device-width\"></head><body><main>"
            f"<h1>Health page {n}</h1><p>The health token is HEALTH-{7730 + n}. {FILLER * 3}</p>"
            "<form onsubmit=\"event.preventDefault()\"><label>Name <input id=\"name\" name=\"name\"></label>"
            "<label>Colour <select id=\"colour\"><option>Red</option><option>Blue</option></select></label>"
            "<button type=\"button\" id=\"go\" onclick=\"document.getElementById('out').textContent="
            "'clicked ' + document.getElementById('name').value + ' ' + document.getElementById('colour').value\">"
            "Go</button></form><p id=\"out\">not clicked</p>"
            f"<a href=\"/page{n % 8 + 1}.html\">Next page</a></main></body></html>")


class Web(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.path)
        m = re.fullmatch(r"/page(\d+)\.html", u.path)
        if m:
            body, ctype = page(int(m.group(1))).encode(), "text/html; charset=utf-8"
        elif u.path == "/search":                      # SearXNG's JSON API (web.py, SEARXNG_URL)
            q = (parse_qs(u.query).get("q") or [""])[0]
            port = self.server.server_address[1]
            rows = [{"title": f"Health page {i} about {q}", "url": f"http://127.0.0.1:{port}/page{i}.html",
                     "content": f"The health token for {q} is on this page."} for i in range(1, 9)]
            body, ctype = json.dumps({"results": rows}).encode(), "application/json"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# A stand-in for a user's MCP server that offers resources: the resource tools (mcp__meshpool_resources__*) are only
# offered when a connected server has resources, and the built-in servers have none.
RESOURCE_SERVER = r'''
import json, sys
for raw in sys.stdin:
    msg = json.loads(raw)
    rid, m = msg.get("id"), msg.get("method")
    if rid is None:
        continue
    if m == "initialize":
        res = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
               "capabilities": {"tools": {}, "resources": {}}, "serverInfo": {"name": "health-res", "version": "1"}}
    elif m == "tools/list":
        res = {"tools": []}
    elif m == "resources/list":
        res = {"resources": [{"uri": "health://note", "name": "Health note", "mimeType": "text/plain"}]}
    elif m == "resources/read":
        res = {"contents": [{"uri": "health://note", "mimeType": "text/plain", "text": "HEALTH-RESOURCE-42"}]}
    else:
        res = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}) + "\n")
    sys.stdout.flush()
'''


# ---------- the run ------------------------------------------------------------------------------------------------

class Health:
    def __init__(self, app: Path, skip: list[str]):
        self.app, self.skips = app, skip
        self.tmp = Path(tempfile.mkdtemp(prefix="meshpool-health-"))
        self.proj, self.home = self.tmp / "proj", self.tmp / "home"
        self.proj.mkdir()
        self.home.mkdir()
        self.checked: set[str] = set()
        self.procs: list = []
        self.agent = self.mgr = None

    # -- setup --
    def setup(self):
        web = ThreadingHTTPServer(("127.0.0.1", 0), Web)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        self.web = f"http://127.0.0.1:{web.server_address[1]}"
        mport = free_port()
        self.procs.append(subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport),
                                            str(self.tmp / "mock.jsonl")],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        dead = f"http://127.0.0.1:{free_port()}"       # nothing listens: an accidental internet call fails at once
        os.environ.update(
            MESHPOOL_HOME=str(self.home), MESHPOOL_PROJECT=str(self.proj), MH_CLARITY="0", PYTHONUTF8="1",
            MESHPOOL_API_KEY="test-key-1234", MESHPOOL_BASE_URL=f"http://127.0.0.1:{mport}/v1",
            MH_ALLOW_PRIVATE_FETCH="1", SEARXNG_URL=self.web, MH_RESEARCH_S="20", HF_HUB_OFFLINE="1",
            HTTP_PROXY=dead, HTTPS_PROXY=dead, http_proxy=dead, https_proxy=dead, ALL_PROXY=dead,
            NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost", MH_ASK_AUTO="1",
            MH_PERMISSION_MODE="auto", MH_BROWSER_HEADLESS="1", MH_TELEMETRY="0")
        # The app's launcher (packaging/linux/meshpool, the Mac and Windows apps) puts its own Python first on PATH,
        # so the agent's `python3` is the bundled one with python-docx, openpyxl and python-pptx.
        os.environ["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
        (self.home / "meshpool.config").write_text(
            f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n", encoding="utf-8")
        sys.path.insert(0, str(self.app))
        from meshpool_harness import bundled
        from meshpool_harness import config as C
        bundled.use_bundled_tools()
        C.ensure_home()
        # every built-in server on, including computer use (off by default: checked here so it reports plainly);
        # no remote servers (context7 needs the internet); the resource stand-in added
        cfg = json.loads((self.home / "mcp.json").read_text(encoding="utf-8"))
        srv = cfg["mcpServers"]
        self.remote = {k: v for k, v in srv.items() if v.get("url")}
        for k in list(srv):
            if srv[k].get("url"):
                del srv[k]
            else:
                srv[k].pop("disabled", None)
        rs = self.tmp / "resource_server.py"
        rs.write_text(RESOURCE_SERVER, encoding="utf-8")
        srv["health-resources"] = {"command": "python", "args": ["-I", str(rs)]}
        (self.home / "mcp.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        # a past chat for search_chats to find
        ses = C.runs_dir() / "sessions" / "health-old-chat"
        ses.mkdir(parents=True)
        (ses / "session.json").write_text(json.dumps({"messages": [
            {"role": "user", "content": "Plan the zebrafish aquarium budget"},
            {"role": "assistant", "content": "The zebrafish aquarium budget is 300 euros."}]}), encoding="utf-8")
        # sample files for the read tools
        (self.proj / "notes.txt").write_text("alpha line\nbeta HEALTH-GREP line\ngamma line\n", encoding="utf-8")
        (self.proj / "data.csv").write_text("region,sales\nNorth,120\nSouth,80\nEast,95\n", encoding="utf-8")

    def start_servers(self):
        from meshpool_harness.agent import mcp as M
        t = time.time()
        from meshpool_harness.harnesses.acp import office_extra
        # as a chat starts them (ui/agent_api.py _start_mcp_servers): mcp.json plus the office server
        self.mgr = M.Manager(roots=[str(self.proj)], extra=office_extra(str(self.proj)), chat="tool-health").start(
            timeout=120, use_health=False, lazy=False)
        for st in self.mgr.status():
            name, ok = st["name"], st["status"] == "connected"
            if name in self.mgr.disabled or st["status"] == "disabled":
                line(f"server {name}", "skip", "needs network and an account (a connector, off until signed in)")
                continue
            line(f"server {name}", "ok" if ok else "FAIL",
                 f"{len(st['tools'])} tools" if ok else f"{st['status']}: {st['error']}")
        print(f"     (servers started in {time.time() - t:.1f} s)", flush=True)

    def make_agent(self):
        from meshpool_harness.agent import core as K
        from meshpool_harness.core import tools as T
        self.K, self.T = K, T
        a = K.Agent(str(self.proj), mcp=self.mgr, mode="auto", sessions_dir=str(self.tmp / "agent-sessions"))
        a._turn_no = 1
        a.approve = lambda *x, **k: (True, "")
        # every core tool may be called here: which chat type offers which is the agent's choice, not this check's
        names = sorted(set(T.SCHEMAS) - {"finish", "need_context"})
        a._tool_names = lambda: list(names)
        self.agent = a

    # -- calling --
    def mcp_name(self, server: str, tool: str) -> str:
        from meshpool_harness.agent import mcp as M
        for n, (s, t) in self.mgr.map.items():
            if s == server and t == tool:
                return n
        return M.tool_name(server, tool)

    def call(self, name: str, args: dict, want_ok: bool = True) -> str:
        a = self.agent
        n0 = len(a.trace)
        out = a._run_tool({"id": f"h{len(a.trace)}", "type": "function",
                           "function": {"name": name, "arguments": json.dumps(args)}})
        step = a.trace[-1] if len(a.trace) > n0 else {"ok": not str(out).startswith("ERROR")}
        if re.search(r"^ERROR: unknown tool|is not connected|MCP error from|Traceback \(most recent", str(out), re.M):
            raise Fail(str(out)[:600])
        if want_ok and (not step.get("ok") or str(out).startswith("ERROR")):
            raise Fail(str(out)[:600])
        return str(out)

    def check(self, tool: str, fn, *a, **kw):
        """Run one check; tool is the name the agent sees."""
        self.checked.add(tool)
        if any(tool == s or (s.endswith("*") and tool.startswith(s[:-1])) for s in self.skips):
            line(tool, "skip", "left out with --skip")
            return None
        t = time.time()
        try:
            detail = fn(*a, **kw)
            line(tool, "ok", (detail or "") + f" ({time.time() - t:.1f} s)")
            return True
        except Skip as e:
            line(tool, "skip", str(e))
        except Fail as e:
            line(tool, "FAIL", str(e))
        except Exception as e:  # noqa: BLE001  a crash in a check is a failure of that tool
            line(tool, "FAIL", f"{type(e).__name__}: {e} | {traceback.format_exc(limit=3)[-400:]}")
        return False

    def mcp(self, server: str, tool: str, fn, *a):
        self.check(self.mcp_name(server, tool), fn, *a)

    def mcall(self, server: str, tool: str, args: dict, want_ok: bool = True) -> str:
        return self.call(self.mcp_name(server, tool), args, want_ok)


def expect(out: str, *words: str) -> str:
    for w in words:
        if w not in out:
            raise Fail(f"expected {w!r} in the result, got: {out[:400]!r}")
    return ""


def made(p: Path, magic: bytes | None = None) -> str:
    if not p.is_file() or not p.stat().st_size:
        raise Fail(f"{p.name} was not made")
    if magic and p.read_bytes()[:len(magic)] != magic:
        raise Fail(f"{p.name} is not a {magic!r} file")
    return f"{p.name} {p.stat().st_size} bytes"


# ---------- core tools (core/tools.py, agent/core.py) -------------------------------------------------------------

def core_tools(h: Health):
    c, P = h.call, h.proj
    h.check("exec_command", lambda: expect(c("exec_command", {"cmd": "echo exec-$((6*7))"}), "exec-42",
                                           "Process exited with code 0"))
    h.check("shell", lambda: expect(c("shell", {"command": ["bash", "-lc", "echo shell-$((6*7))"]}), "shell-42"))
    h.check("bash", lambda: expect(c("bash", {"command": "echo bash-$((6*7))"}), "bash-42", "[exit 0]"))
    h.check("bash_many", lambda: expect(c("bash_many", {"commands": ["echo one-1", "echo two-2"]}), "one-1", "two-2"))

    def bg():
        out = c("bash_start", {"command": "echo started-bg; sleep 30", "name": "health"})
        bid = (re.search(r"started (\S+)", out) or [None, ""])[1]
        if not bid:
            raise Fail(out[:300])
        h.check("bash_output", lambda: expect(c("bash_output", {"id": bid, "wait_s": 1}), bid, "running"))
        h.check("bash_stop", lambda: c("bash_stop", {"id": bid}) and "")
        return f"started {bid}"
    h.check("bash_start", bg)
    h.check("probe", lambda: expect(c("probe", {"command": "python3 -c \"print('probe-ok')\""}), "probe-ok"))
    (P / "traced.py").write_text("def f():\n    return 'trace-ok'\n\nprint(f())\n", encoding="utf-8")
    h.check("trace", lambda: expect(c("trace", {"command": "python3 traced.py"}), "trace-ok"))
    h.check("run_tests", lambda: expect(c("run_tests", {"command": "python3 -c \"print('tests-ok')\""}), "tests-ok"))
    h.check("write_file", lambda: (c("write_file", {"path": "made.txt", "content": "one\ntwo\n"}),
                                   expect((P / "made.txt").read_text(), "two"))[1])
    h.check("read_file", lambda: expect(c("read_file", {"path": "notes.txt"}), "HEALTH-GREP"))
    h.check("edit", lambda: (c("edit", {"path": "made.txt", "old": "two", "new": "zwei"}),
                             expect((P / "made.txt").read_text(), "zwei"))[1])
    h.check("apply_patch", lambda: (c("apply_patch", {"input": "*** Begin Patch\n*** Update File: made.txt\n@@\n one\n"
                                                               "-zwei\n+deux\n*** End Patch\n"}),
                                    expect((P / "made.txt").read_text(), "deux"))[1])
    h.check("grep", lambda: expect(c("grep", {"pattern": "HEALTH-GREP"}), "notes.txt"))
    h.check("glob", lambda: expect(c("glob", {"pattern": "*.txt"}), "notes.txt", "made.txt"))

    def nb():
        import nbformat
        n = nbformat.v4.new_notebook()
        n.cells = [nbformat.v4.new_code_cell("x = 1")]
        nbformat.write(n, P / "health.ipynb")
        c("notebook_edit", {"path": "health.ipynb", "cell": 0, "source": "x = 2", "mode": "replace"})
        return expect(nbformat.read(P / "health.ipynb", as_version=4).cells[0].source, "x = 2")
    h.check("notebook_edit", nb)
    h.check("update_plan", lambda: expect(c("update_plan", {"plan": [{"step": "check", "status": "in_progress"}]}),
                                          "Plan updated"))
    h.check("todo_write", lambda: expect(c("todo_write", {"items": [{"content": "check", "status": "completed"}]}),
                                         "1 of 1 done"))
    h.check("todo", lambda: c("todo", {"items": [{"text": "check", "done": True}]}) and "")
    h.check("ask_user", lambda: c("ask_user", {"questions": [{"question": "Which colour?", "options": [
        {"label": "Red", "recommended": True}, {"label": "Blue"}]}]}) and "")

    def skills():
        from meshpool_harness import skills as SK
        SK.ensure_defaults()
        names = [r["name"] for r in SK.enabled()]
        if not names:
            raise Fail("no skills are installed")
        name = "build-code" if "build-code" in names else names[0]
        out = c("use_skill", {"name": name})
        if len(out) < 200:
            raise Fail(out[:300])
        return f"loaded {name} ({len(names)} skills)"
    h.check("use_skill", skills)
    h.check("find_skill", lambda: expect(c("find_skill", {"query": "make a spreadsheet chart"}), ""))
    h.check("load_skill", lambda: c("load_skill", {"name": "build-code"}, want_ok=False) and "")
    (P / "mod.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    h.check("diagnostics", lambda: c("diagnostics", {"paths": ["mod.py"]}) and "")
    h.check("fetch_url", lambda: expect(c("fetch_url", {"url": f"{h.web}/page1.html"}), "HEALTH-7731"))
    h.check("web_search", lambda: expect(c("web_search", {"query": "health token", "max_results": 3}),
                                         "Health page 1"))
    h.check("web_research", lambda: expect(c("web_research", {"question": "What is the health token?",
                                                              "queries": ["health token"], "max_sources": 2}),
                                           "HEALTH-77"))
    h.check("delegate_task", lambda: expect(c("delegate_task", {"tasks": [{"goal": "Reply with one short line."}]}),
                                            "mock model"))

    def office_task():
        n = model_calls(h)
        out = c("office_task", {"engine": "data", "request": "Chart sales by region from data.csv",
                                "files": ["data.csv"]}, want_ok=False)
        return engine_result(h, out, n)
    h.check("office_task", office_task)
    for n in ("finish", "need_context"):
        h.checked.add(n)
        line(n, "skip", "a signal to the one-shot engine loop (engines/code), not a call that does work")


def model_calls(h: Health) -> int:
    f = h.tmp / "mock.jsonl"
    return len(f.read_text(encoding="utf-8", errors="replace").splitlines()) if f.exists() else 0


def engine_result(h: Health, out: str, calls_before: int) -> str:
    """An office engine runs its own model loop. Offline it talks to the stand-in model, which cannot plan a deck or a
    model, so the job is judged by how it ends: the engine started and asked the model (it is in the stand-in's log),
    and it did not crash on a missing module, program or file."""
    bad = re.search(r"Traceback|ModuleNotFoundError|ImportError|No such file or directory|not found: |"
                    r"command not found|Permission denied|LibreOffice.*(missing|not available)", out)
    if bad:
        raise Fail(out[:600])
    files = re.findall(r"[\w ().-]+\.(?:pptx|xlsx|docx|pdf|png)", out)
    if files:
        return "made " + ", ".join(dict.fromkeys(files[:3]))
    if model_calls(h) <= calls_before:
        raise Fail("the engine never asked the model: " + out[:400])
    return f"ran, asked the stand-in model {model_calls(h) - calls_before} times: " + first_line(out)[:110]


def first_line(out: str) -> str:
    """The first line of a result that says something (not the untrusted-content marker or a failure prefix)."""
    for ln in str(out).splitlines():
        ln = re.sub(r"^ERROR \(tool call failed\): |^ERROR: ", "", ln.strip())
        if ln and not ln.startswith("<"):
            return ln
    return ""


# ---------- MCP servers --------------------------------------------------------------------------------------------

def office(h: Health):
    S, P, m = "meshpool-office", h.proj, h.mcall
    md = "# Health report\n\nRevenue grew 12% to $4.2m.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    h.mcp(S, "make_document", lambda: (m(S, "make_document", {"markdown": md, "filename": "health.docx"}),
                                       made(P / "health.docx", b"PK"))[1])
    h.mcp(S, "make_pdf", lambda: (m(S, "make_pdf", {"markdown": md, "filename": "health.pdf"}),
                                  made(P / "health.pdf", b"%PDF"))[1])
    # small .xlsx and .pptx the way a chat makes them: the agent's own Python with openpyxl and python-pptx
    py = ("import openpyxl, pptx\nwb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Sheet'\n"
          "ws.append(['region', 'sales']); ws.append(['North', 120]); ws.append(['South', 80]); wb.save('health.xlsx')\n"
          "p = pptx.Presentation(); s = p.slides.add_slide(p.slide_layouts[1])\n"
          "s.shapes.title.text = 'Health deck'; s.placeholders[1].text = 'Revenue grew 12%'; p.save('health.pptx')\n")
    (P / "mk.py").write_text(py, encoding="utf-8")
    h.check("python3 (agent's shell) makes .xlsx and .pptx", lambda: (
        expect(h.call("exec_command", {"cmd": "python3 mk.py && echo made-ok"}), "made-ok"),
        made(P / "health.xlsx", b"PK"), made(P / "health.pptx", b"PK"))[2])

    def edit(fname, ops, check):
        m(S, "edit_office_file", {"path": fname, "operations": ops, "in_place": True})
        return check()

    def edits():
        from docx import Document
        from openpyxl import load_workbook
        from pptx import Presentation
        edit("health.docx", [{"op": "replace_text", "find": "Revenue", "replace": "Income"}],
             lambda: expect("\n".join(p.text for p in Document(P / "health.docx").paragraphs), "Income"))
        edit("health.xlsx", [{"op": "set_cell", "sheet": "Sheet", "cell": "C2", "value": "=B2*2"}],
             lambda: expect(str(load_workbook(P / "health.xlsx")["Sheet"]["C2"].value), "=B2*2"))
        edit("health.pptx", [{"op": "replace_text", "find": "Revenue", "replace": "Income"}],
             lambda: expect(" ".join(sh.text_frame.text for sl in Presentation(P / "health.pptx").slides
                                     for sh in sl.shapes if sh.has_text_frame), "Income"))
        return "docx, xlsx and pptx edited in place"
    h.mcp(S, "edit_office_file", edits)

    def reads():
        for f, w in (("health.docx", "Income"), ("health.xlsx", "North"), ("health.pptx", "Income"),
                     ("health.pdf", "Revenue")):
            expect(m(S, "read_document", {"path": f}), w)
        return "docx, xlsx, pptx and pdf"
    h.mcp(S, "read_document", reads)

    def converts():
        m(S, "convert_document", {"path": "health.pptx", "to": "pdf", "output": "deck.pdf"})
        made(P / "deck.pdf", b"%PDF")
        m(S, "convert_document", {"path": "health.pptx", "to": "png", "output": "deck.png"})
        made(P / "deck.png", b"\x89PNG")
        m(S, "convert_document", {"path": "health.pdf", "to": "docx", "output": "from-pdf.docx"})   # the PDF edit route
        made(P / "from-pdf.docx", b"PK")
        m(S, "convert_document", {"path": "health.docx", "to": "pdf", "output": "doc.pdf"})
        return made(P / "doc.pdf", b"%PDF") + "; pptx to pdf and png, pdf to docx, docx to pdf"
    h.mcp(S, "convert_document", converts)
    h.mcp(S, "find_icon", lambda: expect(m(S, "find_icon", {"query": "chart", "limit": 3}), ":"))
    h.mcp(S, "check_deck", lambda: expect_no_crash(m(S, "check_deck", {"path": "health.pptx"}, want_ok=False)))
    h.mcp(S, "check_workbook", lambda: expect_no_crash(m(S, "check_workbook", {"path": "health.xlsx"}, want_ok=False)))
    h.mcp(S, "check_document", lambda: expect_no_crash(m(S, "check_document", {"path": "health.docx"}, want_ok=False)))
    h.mcp(S, "measure_office_file", lambda: expect_no_crash(m(S, "measure_office_file", {"path": "health.pptx"})))
    h.mcp(S, "slide_layouts", lambda: expect_no_crash(m(S, "slide_layouts", {"message": "Revenue grew 12%"})))

    def job(tool, args):
        n = model_calls(h)
        out = m(S, tool, args, want_ok=False)
        jid = re.search(r"job[ _]?id[^\w]*([\w-]{6,})", out, re.I)
        if "Call office_job_status" in out and jid:
            h.checked.add(h.mcp_name(S, "office_job_status"))
            end = time.time() + 240
            while time.time() < end and "Call office_job_status" in out:
                time.sleep(3)
                out = m(S, "office_job_status", {"job_id": jid.group(1)}, want_ok=False)
        return engine_result(h, out, n)
    h.mcp(S, "analyze_data", lambda: job("analyze_data", {"request": "Chart sales by region", "files": ["data.csv"]}))
    h.mcp(S, "make_spreadsheet", lambda: job("make_spreadsheet", {"request": "A one-sheet budget with 3 lines",
                                                                  "save_as": "budget.xlsx"}))
    h.mcp(S, "make_deck", lambda: job("make_deck", {"request": "Two slides on health checks", "brief_ok": True,
                                                    "slides": 2, "save_as": "engine-deck.pptx"}))
    h.mcp(S, "add_slides", lambda: job("add_slides", {"deck": "health.pptx", "request": "One slide on costs",
                                                      "slides": 1}))
    sid = h.mcp_name(S, "office_job_status")
    if sid not in h.checked:
        h.mcp(S, "office_job_status", lambda: expect(m(S, "office_job_status", {"job_id": "no-such-job"},
                                                       want_ok=False), "job"))


def expect_no_crash(out: str) -> str:
    if re.search(r"Traceback|ModuleNotFoundError|ImportError|MCP error|is not connected", out):
        raise Fail(out[:500])
    return first_line(out)[:100]


def web_server(h: Health):
    S, m = "meshpool-web", h.mcall
    h.mcp(S, "web_fetch", lambda: expect(m(S, "web_fetch", {"url": f"{h.web}/page2.html"}), "HEALTH-7732"))
    h.mcp(S, "web_search", lambda: expect(m(S, "web_search", {"query": "health token", "max_results": 3}),
                                          "Health page 1"))
    h.mcp(S, "web_research", lambda: expect(m(S, "web_research", {"question": "What is the health token?",
                                                                   "max_sources": 2}), "HEALTH-77"))


def assistant(h: Health):
    S, m = "meshpool-assistant", h.mcall
    h.mcp(S, "memory_save", lambda: expect(m(S, "memory_save", {"fact": "The user prefers EUR for prices."}), ""))
    h.mcp(S, "memory_view", lambda: expect(m(S, "memory_view", {}), "prefers EUR"))
    h.mcp(S, "project_note", lambda: m(S, "project_note", {"note": "Tests run with pytest -q."}) and "")
    h.mcp(S, "memory_forget", lambda: (m(S, "memory_forget", {"text": "prefers EUR"}),
                                       expect_not(m(S, "memory_view", {}), "prefers EUR"))[1])
    h.mcp(S, "schedule_task", lambda: expect(m(S, "schedule_task", {"prompt": "Write a one-line brief.",
                                                                    "when": "daily", "time": "08:30",
                                                                    "name": "Health brief"}), "Scheduled"))
    h.mcp(S, "list_scheduled_tasks", lambda: expect(m(S, "list_scheduled_tasks", {}), "Health brief"))
    h.mcp(S, "delete_scheduled_task", lambda: expect(m(S, "delete_scheduled_task", {"id": "Health brief"}),
                                                     "Deleted"))
    h.mcp(S, "search_chats", lambda: expect(m(S, "search_chats", {"query": "zebrafish aquarium"}), "zebrafish"))
    h.mcp(S, "run_subtasks", lambda: expect(m(S, "run_subtasks", {"tasks": [{"title": "one", "prompt": "Reply with one short line."}],
                                                                  "max_parallel": 1}), "mock model"))


def expect_not(out: str, word: str) -> str:
    if word in out:
        raise Fail(f"{word!r} is still there: {out[:300]!r}")
    return ""


def tools_server(h: Health):
    S, m = "meshpool-tools", h.mcall
    h.mcp(S, "current_time", lambda: expect(m(S, "current_time", {"utc_offset_hours": 2}), "UTC+2"))
    h.mcp(S, "calculate", lambda: expect(m(S, "calculate", {"expression": "(1200*0.15)/12"}), "= 15"))
    h.mcp(S, "project_stats", lambda: expect(m(S, "project_stats", {"path": str(h.proj)}), "files"))


def resources(h: Health):
    h.check("mcp__meshpool_resources__list_resources",
            lambda: expect(h.call("mcp__meshpool_resources__list_resources", {}), "health://note"))
    h.check("mcp__meshpool_resources__read_resource",
            lambda: expect(h.call("mcp__meshpool_resources__read_resource",
                                  {"server": "health-resources", "uri": "health://note"}), "HEALTH-RESOURCE-42"))


def browser(h: Health):
    """Browser use against a local page. Each tool after browser_open needs the page it opened."""
    S, m = "browser", h.mcall
    url = f"{h.web}/page1.html"
    if not h.check(h.mcp_name(S, "browser_open"), lambda: expect(m(S, "browser_open", {"url": url}), "Health page 1")):
        for t in ("browser_snapshot", "browser_click", "browser_type", "browser_select", "browser_press",
                  "browser_scroll", "browser_back", "browser_tabs", "browser_screenshot", "browser_wait",
                  "browser_close"):
            h.checked.add(h.mcp_name(S, t))
            line(h.mcp_name(S, t), "FAIL", "not run: browser_open failed")
    else:
        snap = {}

        def ref(label: str) -> str:
            s = snap.get("s") or m(S, "browser_snapshot", {})
            r = re.search(r"\[(e\d+)\][^\n]*" + re.escape(label), s)
            if not r:
                raise Fail(f"no element {label!r} in the snapshot: {s[:400]!r}")
            return r.group(1)

        def snapshot():
            snap["s"] = m(S, "browser_snapshot", {})
            return expect(snap["s"], "HEALTH-7731", "[e")
        h.mcp(S, "browser_snapshot", snapshot)
        h.mcp(S, "browser_type", lambda: expect(m(S, "browser_type", {"ref": ref("Name"), "text": "Ada"}), ""))
        h.mcp(S, "browser_select", lambda: expect(m(S, "browser_select", {"ref": ref("select"), "option": "Blue"}), ""))
        h.mcp(S, "browser_click", lambda: expect(m(S, "browser_click", {"ref": ref('"Go"')}), "clicked Ada Blue"))
        h.mcp(S, "browser_press", lambda: expect(m(S, "browser_press", {"key": "Tab"}), ""))
        h.mcp(S, "browser_scroll", lambda: expect(m(S, "browser_scroll", {"direction": "down", "pages": 1}), ""))
        h.mcp(S, "browser_wait", lambda: expect(m(S, "browser_wait", {"seconds": 1}), "Health page"))
        h.mcp(S, "browser_screenshot", lambda: expect(m(S, "browser_screenshot", {}), ".png"))
        h.mcp(S, "browser_tabs", lambda: expect(m(S, "browser_tabs", {}), "Health page"))

        def back():
            snap.pop("s", None)
            m(S, "browser_click", {"ref": ref("Next page")})
            return expect(m(S, "browser_back", {}), "Health page 1")
        h.mcp(S, "browser_back", back)
        h.mcp(S, "browser_close", lambda: m(S, "browser_close", {}) and "")
    (h.proj / "site.html").write_text(page(3), encoding="utf-8")
    h.mcp(S, "view_output", lambda: expect(m(S, "view_output", {"path": "health.pdf"}), "health"))
    h.mcp(S, "ui_audit", lambda: expect(m(S, "ui_audit", {"url": "site.html", "views": "wide"}), "site"))
    h.mcp(S, "ui_smoke", lambda: expect_no_crash(m(S, "ui_smoke", {"url": "site.html"})))


def computer(h: Health):
    S, m = "computer-use", h.mcall
    names = ("screenshot", "click", "move_mouse", "drag", "scroll", "type_text", "press_key", "open_app",
             "screen_info", "computer_check")
    if sys.platform.startswith("linux"):
        # not supported on Linux: every tool must say so in plain words, not crash
        def plain(tool, args):
            out = m(S, tool, args, want_ok=False)
            if "not supported on Linux" not in out or "Traceback" in out:
                raise Fail(f"no plain answer on Linux: {out[:300]!r}")
            raise Skip("not on this platform (says so plainly: computer use runs on macOS and Windows)")
        args = {"click": {"x": 1, "y": 1}, "move_mouse": {"x": 1, "y": 1}, "drag": {"x1": 1, "y1": 1, "x2": 2, "y2": 2},
                "scroll": {"x": 1, "y": 1}, "type_text": {"text": "a"}, "press_key": {"keys": "tab"},
                "open_app": {"name": "Calculator"}}
        for t in names:
            h.mcp(S, t, plain, t, args.get(t, {}))
        return
    h.mcp(S, "computer_check", lambda: expect_no_crash(m(S, "computer_check", {}, want_ok=False)))
    h.mcp(S, "screen_info", lambda: expect_no_crash(m(S, "screen_info", {}, want_ok=False)))
    for t in names[:-2]:
        h.checked.add(h.mcp_name(S, t))
        line(h.mcp_name(S, t), "skip", "needs a signed-in desktop and the user's permission (run by hand)")


# ---------- programs the skills rely on (bundled in the app: vendor/) ---------------------------------------------

def skill_programs(h: Health):
    from meshpool_harness import bundled
    packaged = bundled.packaged()

    def need(exe, what):
        if exe:
            return exe
        if not packaged:
            raise Skip(f"a source checkout has no bundled {what} (app/vendor)")
        raise Fail(f"{what} is not in the app (vendor)")

    def ffmpeg():
        why = bundled.ffmpeg_needs_newer_macos()
        ff = need(bundled.bundled_ffmpeg(), "ffmpeg")
        if why:
            raise Skip(why)
        r = subprocess.run([ff, "-hide_banner", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y",
                            str(h.tmp / "tone.wav")], capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise Fail(r.stderr[-300:])
        return made(h.tmp / "tone.wav")
    h.check("skill program: ffmpeg", ffmpeg)

    def pandoc():
        pd = need(bundled.bundled_pandoc(), "pandoc")
        (h.tmp / "p.md").write_text("# Hi\n\nA *test*.\n", encoding="utf-8")
        r = subprocess.run([pd, str(h.tmp / "p.md"), "-o", str(h.tmp / "p.docx")], capture_output=True, text=True,
                           timeout=60)
        if r.returncode:
            raise Fail(r.stderr[-300:])
        return made(h.tmp / "p.docx", b"PK")
    h.check("skill program: pandoc", pandoc)

    def whisper():
        import wave
        model = need(bundled.whisper_model_dir(), "speech model")
        wav = h.tmp / "silence.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\0" * 16000)
        from faster_whisper import WhisperModel
        segs, info = WhisperModel(str(model), device="cpu", compute_type="int8").transcribe(str(wav), vad_filter=True)
        list(segs)
        return f"ran offline ({info.language})"
    h.check("skill program: speech to text", whisper)

    def notebook():
        import nbformat
        nb = nbformat.v4.new_notebook()
        nb.cells = [nbformat.v4.new_code_cell("print(6 * 7)")]
        f = h.tmp / "run.ipynb"
        nbformat.write(nb, f)
        r = subprocess.run([sys.executable, "-m", "nbconvert", "--to", "notebook", "--execute", "--inplace", str(f)],
                           capture_output=True, text=True, timeout=300)
        if r.returncode or "42" not in str(nbformat.read(f, as_version=4).cells[0].outputs):
            raise Fail(r.stderr[-400:])
        return "ran with the bundled kernel"
    h.check("skill program: notebooks", notebook)

    def chart():
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.figure()
        plt.bar(["a", "b"], [1, 2])
        plt.savefig(h.tmp / "chart.png")
        return made(h.tmp / "chart.png", b"\x89PNG")
    h.check("skill program: charts", chart)

    def heic():
        from PIL import Image
        from pillow_heif import register_heif_opener
        register_heif_opener()
        Image.new("RGB", (8, 8), "red").save(h.tmp / "x.heic")
        Image.open(h.tmp / "x.heic").load()
        return "wrote and read a .heic"
    h.check("skill program: HEIC", heic)

    def ocr():
        out = h.call("read_file", {"path": str(h.proj / "deck.png")}) if (h.proj / "deck.png").exists() else ""
        if not out:
            raise Skip("no picture to read (convert_document to png failed)")
        import importlib.util
        if not all(importlib.util.find_spec(m) for m in ("rapidocr", "onnxruntime")):
            # the apps ship without OCR (pyproject extra "ocr"): read_file must say so plainly
            if "no OCR" not in out or "failed" in out:
                raise Fail(f"read_file on a picture does not say plainly that there is no OCR: {out[:300]!r}")
            raise Skip("not in this install (an optional part; read_file says so plainly and points to view_output)")
        return expect(out, "Health")
    h.check("skill program: OCR (read_file on a picture)", ocr)


# ---------- the command sandbox (Linux: the bubblewrap the app carries, or the system's) ---------------------------

def sandbox(h: Health):
    """Commands in Ask mode run in the sandbox without a question: it must start, and confine a command to the folder.
    Where the computer refuses user namespaces (a container, some kernels) the app runs commands without it and asks
    before risky steps instead: a skip that says so, not a failure."""
    if not sys.platform.startswith("linux"):
        return
    from meshpool_harness import bundled
    from meshpool_harness.core import sandbox as SBX

    def run():
        own = bundled.bundled_program("bwrap")
        if bundled.packaged() and not own:
            raise Fail("bubblewrap is not in the app (vendor/bwrap): packaging/build_all.py bwrap_linux")
        bw = SBX.bwrap_program()
        if not bw:
            said = "; ".join(f"{p}: {SBX.bwrap_starts(p)[1]}" for p in dict.fromkeys(filter(None, (shutil.which("bwrap"), own))))
            raise Skip("this computer does not let programs make user namespaces, so commands run without the sandbox "
                       f"and the app asks before risky steps ({said[:200] or 'no bubblewrap'})")
        # exits 0 when confined: a refused write outside the folder is the expected result, not the command's failure
        argv = SBX.wrap(["bash", "-c", "echo inside > sandbox-health.txt; if touch /usr/meshpool-health 2>/dev/null; "
                         "then echo escaped; fi"], h.proj, mode="ask")
        if argv[0] != bw:
            raise Fail(f"the command was not wrapped: {argv[:3]}")
        r = subprocess.run(argv, cwd=str(h.proj), capture_output=True, text=True, timeout=60)
        if r.returncode or "escaped" in r.stdout or not (h.proj / "sandbox-health.txt").is_file():
            raise Fail(f"exit {r.returncode}: {(r.stdout + r.stderr)[-300:]}")
        (h.proj / "sandbox-health.txt").unlink()
        return f"{'the app' if bw == own else 'the system'}'s bubblewrap: writes in the folder, not outside it"
    h.check("command sandbox (bubblewrap)", run)


# ---------- main ---------------------------------------------------------------------------------------------------

def every_tool(h: Health) -> list[str]:
    """Every tool name the agent can be offered: the core tool schemas and every tool of every connected server."""
    names = set(h.T.SCHEMAS) | set(h.mgr.map)
    names |= {"mcp__meshpool_resources__list_resources", "mcp__meshpool_resources__read_resource"}
    return sorted(names)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="the app's code folder (holds meshpool_harness)")
    ap.add_argument("--version", default="", help="also check the app says this version")
    ap.add_argument("--skip", action="append", default=[], help="a tool to leave out (NAME, or a prefix ending in *)")
    ap.add_argument("--keep", action="store_true", help="keep the temp folder")
    a = ap.parse_args()
    app = Path(a.app).resolve()
    h = Health(app, a.skip)
    print(f"tool health: {app} with Python {sys.version.split()[0]} ({sys.executable}) as "
          f"{'root' if hasattr(os, 'geteuid') and os.geteuid() == 0 else 'user ' + os.environ.get('USER', '?')}",
          flush=True)
    try:
        h.setup()
        import meshpool_harness as MH
        if a.version and MH.__version__ != a.version:
            line("version", "FAIL", f"the app says {MH.__version__}, expected {a.version}")
        h.start_servers()
        h.make_agent()
        for part in (core_tools, office, web_server, assistant, tools_server, resources, browser, computer,
                     skill_programs, sandbox):
            try:
                part(h)
            except Exception as e:  # noqa: BLE001
                line(f"({part.__name__})", "FAIL", f"{type(e).__name__}: {e} | {traceback.format_exc(limit=4)[-500:]}")
        for name, spec in h.remote.items():
            line(f"server {name}", "skip", f"needs network ({spec.get('url')})")
        line("connectors (GitHub, Jira, Linear, ...)", "skip", "needs network and an account")
        line("web_search on the live internet", "skip", "needs network (checked here against a local search API)")
        for name in every_tool(h):
            if name not in h.checked:
                line(name, "FAIL", "no health check for this tool: add one to packaging/tool_health.py")
    finally:
        try:
            if h.mgr:
                h.mgr.close()
            from meshpool_harness.agent import mcp as M
            M.stop_all()
        except Exception:  # noqa: BLE001
            pass
        for p in h.procs:
            p.kill()
        if not a.keep:
            shutil.rmtree(h.tmp, ignore_errors=True)
    n = {s: sum(1 for r in RESULTS if r[1] == s) for s in ("ok", "FAIL", "skip")}
    print(f"tool health: {n['ok']} ok, {n['FAIL']} FAIL, {n['skip']} skipped in {time.time() - T0:.0f} s", flush=True)
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
