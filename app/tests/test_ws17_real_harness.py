"""The real Pi and Codex binaries through Meshpool's adapters, against local mock models (no network, no key).

Installed in CI with: npm i -g @mariozechner/pi-coding-agent@0.73.1 @agentclientprotocol/codex-acp@2.6.0
Each test is skipped when its binary is not installed."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _npm_root() -> Path | None:
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        return Path(subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


ROOT = _npm_root()
PI_CLI = ROOT / "@mariozechner" / "pi-coding-agent" / "dist" / "cli.js" if ROOT else None
CODEX_ACP = shutil.which("codex-acp")


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    C.ensure_home()
    proj = tmp_path / "proj"
    proj.mkdir()
    return tmp_path, proj


@pytest.fixture()
def chat_mock(home, monkeypatch):
    tmp, _ = home
    port = _port()
    p = subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(port), str(tmp / "mock.jsonl")],
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
    yield tmp / "mock.jsonl"
    p.kill()


@pytest.mark.skipif(not (PI_CLI and PI_CLI.is_file() and shutil.which("node")), reason="real Pi not installed")
def test_real_pi_runs_tools_counts_usage_and_continues_its_session(home, chat_mock, monkeypatch):
    from meshpool_harness.harnesses import pi_rpc
    monkeypatch.setattr(pi_rpc, "node_exe", lambda: shutil.which("node"))
    monkeypatch.setattr(pi_rpc, "pi_cli", lambda: PI_CLI)
    _, proj = home
    evs = []
    a = pi_rpc.PiAgent({"id": "pi", "type": "pi"}, str(proj), emit=evs.append, mode="auto")
    try:
        r = a.send("hello")                                     # the mock writes hello.py and runs it
        assert "hello.py" in r, (r, [e for e in evs if e["type"] in ("error", "status")])
        assert (proj / "hello.py").exists()
        assert [e["name"] for e in evs if e["type"] == "tool_start"][:2] == ["Wrote", "Ran"]
        end = [e for e in evs if e["type"] == "turn_end"][-1]
        assert end["turn"]["in"] > 0 and end["turn"]["out"] > 0   # Pi's usage reaches the ledger
        assert (a.dir / "pi").is_dir() and any((a.dir / "pi").glob("*.jsonl"))
        assert (pi_rpc.pi_home() / "chats" / a.id / "models.json").is_file()   # this chat's own config
        # the process dies between turns (a crash, an update): the next message continues the same session
        pid = a._p.pid
        a._p.kill()
        a._p.wait(5)
        r2 = a.send("thanks")
        assert a._p.pid != pid and "thanks" in r2
        assert not any(e["type"] == "error" for e in evs)
        msgs = [json.loads(x) for x in chat_mock.read_text().splitlines()]
        assert msgs and all(m["auth"] == "Bearer test-key-1234" for m in msgs)
    finally:
        a.close()


# ---------- Codex (Responses API mock) --------------------------------------------------------------------------

class _Responses(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        out = json.dumps({"data": [{"id": "mock-model", "object": "model"}], "object": "list"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        _Responses.calls.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                 "model": body.get("model")})
        text = "Hello from the mock Responses model."
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        item = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}]}

        def ev(name, data):
            self.wfile.write(f"event: {name}\ndata: {json.dumps(dict(data, type=name))}\n\n".encode())
            self.wfile.flush()
        ev("response.created", {"response": {"id": "resp_1", "status": "in_progress"}})
        ev("response.output_item.added", {"output_index": 0, "item": dict(item, status="in_progress", content=[])})
        ev("response.output_text.delta", {"output_index": 0, "content_index": 0, "item_id": "msg_1", "delta": text})
        ev("response.output_item.done", {"output_index": 0, "item": item})
        ev("response.completed", {"response": {"id": "resp_1", "status": "completed", "output": [item], "usage": {
            "input_tokens": 1200, "input_tokens_details": {"cached_tokens": 1000}, "output_tokens": 30,
            "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 1230}}})


@pytest.mark.skipif(not CODEX_ACP, reason="real Codex (codex-acp) not installed")
def test_real_codex_answers_on_the_key_endpoint_with_its_own_config(home, monkeypatch):
    from meshpool_harness import harnesses as HR
    from meshpool_harness.harnesses.acp import ACPAgent
    _, proj = home
    _Responses.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Responses)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    C.write({"MESHPOOL_API_KEY": "test-key-1234", "MESHPOOL_BASE_URL": f"http://127.0.0.1:{srv.server_address[1]}/v1"})
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MESHPOOL_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1")
    m = dict(HR.CODEX, command=CODEX_ACP, args=[], office=False, share_mcp=False, **HR._launch("codex", "key"))
    m["env"] = dict(m["env"], MESHPOOL_API_KEY="test-key-1234")
    evs = []
    a = ACPAgent(m, str(proj), emit=evs.append, mode="auto")
    a.model = "mock-model"
    try:
        r = a.send("Say hello")
        assert "Hello from the mock Responses model" in r, [e for e in evs if e["type"] in ("error", "status")]
        assert _Responses.calls and _Responses.calls[-1]["auth"] == "Bearer test-key-1234"
        assert _Responses.calls[-1]["model"] == "mock-model"
        assert (HR.codex_home() / "chats" / a.id / "config.toml").is_file()   # this chat's own CODEX_HOME
        end = [e for e in evs if e["type"] == "turn_end"][-1]["turn"]
        assert (end["in"], end["cached"], end["out"]) == (1200, 1000, 30)        # Codex's usage reaches the ledger
        assert not any(e["type"] == "error" for e in evs)
        assert not any("kept its own model" in (e.get("text") or "") for e in evs)   # Codex ids: model[effort]
        # Stop then a new message: the chat goes on
        a.stop()
        r2 = a.send("Say hello again")
        assert "Hello from the mock Responses model" in r2
    finally:
        a.close()
        srv.shutdown()
