"""Shared pieces of the browser tests: a small multi-page site on localhost, a way to find a Chromium that really
launches here, and a way to start Meshpool's browser MCP server the way the agent does (agent/mcp.py)."""
from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGES = {
    "/": """<title>Test site</title><h1>Test site</h1>
<a href="/login">Log in</a> <a href="/controls">Controls</a> <a href="/late">Late page</a>""",
    "/login": """<title>Log in</title><h1>Log in</h1>
<form method="post" action="/login">
<label>User <input name="user"></label>
<label>Password <input name="password" type="password"></label>
<button type="submit">Sign in</button></form>""",
    "/controls": """<title>Controls</title><h1>Controls</h1>
<button id="add" onclick="n++;document.getElementById('count').textContent='Count: '+n">Add one</button>
<span id="count">Count: 0</span>
<a href="/second">Second page</a> <a href="/tab" target="_blank">Open in new tab</a>
<label>Name <input id="name"></label>
<label>Colour <select id="color"><option>Red</option><option>Green</option><option>Blue</option></select></label>
<label><input type="checkbox" id="agree"> I agree</label>
<input id="keys" placeholder="Press keys here"> <div id="keyout">Key: none</div>
<form action="/echo"><input name="q" aria-label="Search"></form>
<button onclick="document.getElementById('state').textContent='State: name='+document.getElementById('name').value+
 ';color='+document.getElementById('color').value+';agree='+document.getElementById('agree').checked">Show state</button>
<div id="state"></div>
<div style="height:3000px">tall block</div><div id="bottom">Bottom marker</div>
<script>var n=0;document.getElementById('keys').addEventListener('keydown',function(e){
 document.getElementById('keyout').textContent='Key: '+e.key;});</script>""",
    "/second": "<title>Second</title><h1>Second page</h1><a href='/controls'>Back to controls</a>",
    "/tab": "<title>New tab page</title><h1>I am the new tab</h1>",
    "/late": """<title>Late</title><h1>Late page</h1><div id="out">Loading...</div>
<script>setTimeout(function(){fetch('/api/late').then(function(r){return r.text()}).then(function(t){
 document.getElementById('out').textContent=t;});},1500);</script>""",
}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/dashboard":
            who = re.search(r"mp_session=(\w+)", self.headers.get("Cookie", ""))
            return self._send(200, f"<title>Dashboard</title><h1>Dashboard</h1><p>"
                                   f"{'Signed in as ' + who.group(1) if who else 'Signed out'}</p>")
        if u.path == "/api/late":
            return self._send(200, "Late content arrived", "text/plain")
        if u.path == "/echo":
            return self._send(200, f"<title>Echo</title><h1>You searched: {parse_qs(u.query).get('q', [''])[0]}</h1>")
        if u.path in PAGES:
            return self._send(200, PAGES[u.path])
        self._send(404, "<title>Not found</title><h1>Not found</h1>")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(n).decode("utf-8"))
        user = re.sub(r"\W", "", form.get("user", ["nobody"])[0]) or "nobody"
        # Max-Age: a cookie without one is a session cookie, which Chromium never keeps across a restart
        self._send(302, "", headers=[("Location", "/dashboard"),
                                     ("Set-Cookie", f"mp_session={user}; Max-Age=3600; Path=/; HttpOnly")])


def start_site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def find_chromium() -> tuple[str, str]:
    """(channel, why_not): the MH_BROWSER_CHANNEL value ("" = Playwright's Chromium, "chrome") that launches headless
    here, or ("", reason) when no browser can start (CI without browsers skips)."""
    import tempfile
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "", "playwright is not installed"
    why = ""
    for ch in ("", "chrome"):
        try:
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), headless=True, channel=ch or None)
                ctx.close()
            return ch, ""
        except Exception as e:  # noqa: BLE001
            why = why or str(e).splitlines()[0][:200]
    return "", f"no Chromium that launches here ({why})"


def server_for(home: Path, project: Path, channel: str, extra_env: dict | None = None, name: str = "browser"):
    """The browser MCP server started exactly as the agent starts it: agent/mcp.py's Server, command "python"."""
    from meshpool_harness.agent import mcp as M
    env = {"MESHPOOL_HOME": str(home), "MESHPOOL_PROJECT": str(project), "MH_BROWSER_HEADLESS": "1",
           "MH_BROWSER_CHANNEL": channel, "MH_BROWSER_USE": "meshpool", "MH_BROWSER_CDP_URL": "",
           "MH_BROWSER_EXE": "", "MH_BROWSER_APP": ""}
    env.update(extra_env or {})
    spec = {"command": "python", "args": ["-m", "meshpool_harness.agent.browser_mcp_server"], "timeout": 120, "env": env}
    s = M.Server(name, spec, home / "logs", [str(project)])
    s.start(timeout=60)
    assert s.status == "connected", s.error
    return s


def ref(snapshot: str, pattern: str) -> str:
    """The [eN] number of the first interactive element whose line matches pattern."""
    m = re.search(r"\[(e\d+)\][^\n]*(?:" + pattern + ")", snapshot)
    assert m, f"no element matching {pattern!r} in:\n{snapshot[:1500]}"
    return m.group(1)


def chromium_exe() -> str:
    """A real Chromium/Chrome program on this computer (for "installed" and "attach"), or ""."""
    import shutil
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if Path(p.chromium.executable_path).is_file():
                return p.chromium.executable_path
    except Exception:  # noqa: BLE001
        pass
    return shutil.which("google-chrome") or shutil.which("chromium") or ""
