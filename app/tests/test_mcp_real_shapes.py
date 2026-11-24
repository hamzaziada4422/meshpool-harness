"""0.0.13: shapes real MCP servers have that the benchmark's fake did not: a streamable-HTTP server that answers in an
SSE stream (with progress events and keep-alive comments before the result), tool schemas that use $ref, anyOf and
oneOf, and 300 tools with long descriptions behind the lazy tool index. OAuth is not covered here (see
test_mcp_client.py for the local sign-in mock; a real provider cannot be reached from a test)."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness.agent import mcp as M  # noqa: E402
from meshpool_harness.agent import mcp_assist as MA  # noqa: E402

LONG = ("Creates, updates or lists the records of one kind in the workspace. Accepts filters, ordering and paging. "
        "Use it when the user names a record, a customer or a date range. Returns JSON with the matching rows. ") * 6

REF_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "$defs": {"Address": {"type": "object", "properties": {"city": {"type": "string"}, "zip": {"type": "string"}},
                          "required": ["city"]}},
    "properties": {
        "ship_to": {"$ref": "#/$defs/Address"},
        "when": {"anyOf": [{"type": "string", "format": "date"}, {"type": "integer"}, {"type": "null"}]},
        "contact": {"oneOf": [{"type": "object", "properties": {"email": {"type": "string"}}},
                              {"type": "object", "properties": {"phone": {"type": "string"}}}]},
    },
    "required": ["ship_to"],
}


def _tools(n_extra=0):
    t = [{"name": "ship_order", "description": "Ship an order to an address.", "inputSchema": REF_SCHEMA},
         {"name": "find_ticket", "description": "Find a support ticket by words in its title.",
          "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}}]
    for i in range(n_extra):
        t.append({"name": f"tool_{i:03d}_" + ("invoice", "ticket", "contact", "meeting", "report")[i % 5],
                  "description": f"Tool number {i}. " + LONG,
                  "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": LONG[:200]}}}})
    return t


class _Handler(BaseHTTPRequestHandler):
    tools: list = []
    calls: list = []

    def log_message(self, *a):
        pass

    def _sse(self, events: list[str]):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Mcp-Session-Id", "sess-1")
        self.end_headers()
        for e in events:
            self.wfile.write(e.encode())
            self.wfile.flush()

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        if "id" not in msg:
            self.send_response(202)
            self.end_headers()
            return
        m, rid = msg.get("method"), msg["id"]
        if m == "initialize":
            res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "sse-http", "version": "1"}}
        elif m == "tools/list":
            res = {"tools": self.tools}
        elif m == "tools/call":
            type(self).calls.append(msg["params"])
            res = {"content": [{"type": "text", "text": json.dumps({"ok": True, "got": msg["params"]["arguments"]})}]}
        else:
            res = {}
        reply = json.dumps({"jsonrpc": "2.0", "id": rid, "result": res})
        events = ["id: 0\ndata:\n\n", ": keep-alive\n\n"]
        if m == "tools/call":                                   # progress notification first, then the result
            events.append("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                                                 "params": {"progress": 1, "total": 2}}) + "\n\n")
        events.append("event: message\ndata: " + reply + "\n\n")
        self._sse(events)

    def do_DELETE(self):
        self.send_response(204)
        self.end_headers()


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    handlers = []

    def start(tools):
        h = type("H", (_Handler,), {"tools": tools, "calls": []})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), h)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        handlers.append(httpd)
        return h, f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
    yield start
    for h in handlers:
        h.shutdown()


def _mgr(tmp_path, url):
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"biz": {"url": url}}}))
    return M.Manager(roots=[str(tmp_path)]).start(timeout=20, use_health=False)


def test_streamable_http_answering_in_an_sse_stream(server, tmp_path):
    h, url = server(_tools())
    m = _mgr(tmp_path, url)
    try:
        st = {s["name"]: s for s in m.status()}["biz"]
        assert st["status"] == "connected", st
        names = [s["function"]["name"] for s in m.schemas()]
        assert any(n.endswith("find_ticket") for n in names)
        name = next(n for n in names if n.endswith("find_ticket"))
        out, is_err = m.call(name, {"q": "refund"})
        assert not is_err and json.loads(out) == {"ok": True, "got": {"q": "refund"}}
        assert h.calls and h.calls[-1]["name"] == "find_ticket"
    finally:
        m.close()


def test_ref_anyof_oneof_schemas_pass_through_and_load_lazily(server, tmp_path):
    _, url = server(_tools(n_extra=80))
    m = _mgr(tmp_path, url)
    try:
        schemas = m.schemas()
        ship = next(s for s in schemas if s["function"]["name"].endswith("ship_order"))
        p = ship["function"]["parameters"]
        assert "$schema" not in p and p["$defs"]["Address"]["required"] == ["city"]
        assert p["properties"]["ship_to"] == {"$ref": "#/$defs/Address"}
        assert len(p["properties"]["when"]["anyOf"]) == 3 and len(p["properties"]["contact"]["oneOf"]) == 2
        json.dumps(schemas)                                     # all of it can be sent to a model
        lazy = MA.LazyTools()
        assert lazy.wanted(schemas)
        shown, hidden = lazy.split(schemas)
        found = lazy.search(hidden, "ship order address")
        assert "ship_order" in found and "$defs" in found and "oneOf" in found and "anyOf" in found
        assert ship["function"]["name"] in lazy.active
        # the loaded call goes through with nested arguments untouched
        out, err = m.call(ship["function"]["name"], {"ship_to": {"city": "Oslo"}, "when": None})
        assert not err and json.loads(out)["got"] == {"ship_to": {"city": "Oslo"}, "when": None}
    finally:
        m.close()


def test_300_tools_with_long_descriptions_stay_small_in_the_index(server, tmp_path):
    _, url = server(_tools(n_extra=300))
    m = _mgr(tmp_path, url)
    try:
        schemas = m.schemas()
        assert len(schemas) == 302
        assert all(len(s["function"]["description"]) <= 1024 for s in schemas)
        lazy = MA.LazyTools()
        assert lazy.wanted(schemas)
        shown, hidden = lazy.split(schemas)
        assert len(hidden) >= 300 and lazy.find_schema(hidden)["function"]["description"]
        index = lazy.find_schema(hidden)["function"]["description"]
        full = sum(len(json.dumps(s)) for s in schemas)
        assert len(index) < full / 10                            # the index is a small part of the full schemas
        assert len(index) < 40000
        # a search names the right tool by what the user wants, and loads at most 8 of them
        out = lazy.search(hidden, "meeting")
        assert out.count("parameters:") <= 8 and "_meeting" in out
        # calling a tool that was never loaded loads it and asks for the call again
        target = next(s["function"]["name"] for s in hidden if s["function"]["name"].endswith("tool_007_contact"))
        assert "not loaded" in lazy.stub(target, schemas) and target in lazy.active
    finally:
        m.close()
