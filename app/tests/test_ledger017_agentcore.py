"""meshpool 0.0.17 ledger gaps, agent and core rows: one test (or a few) per row, each a real call into the code where
the code can run here. The row id is the first words of each docstring."""
import codecs
import importlib
import io
import json
import os
import queue
import re
import ssl
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

from meshpool_harness import config as C

REPO = Path(__file__).resolve().parents[2]
KEY = "sk-test-key-0000"


@pytest.fixture
def home(tmp_path, monkeypatch):
    from meshpool_harness.core import gateway as GW
    GW.reset()                                             # no endpoint marked down by an earlier test
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    monkeypatch.setenv("MH_SLOTS_DIR", str(tmp_path / "slots"))
    (tmp_path / "mh").mkdir()
    for k in ("MESHPOOL_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "MESHPOOL_BASE_URL",
              "MH_MODEL", "MH_API_KIND", "MH_API_VERSION", "MH_PROVIDER", "MH_API_WIRE", "MH_PROFILE"):
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    yield tmp_path
    GW.reset()


class Plain:
    """A Style stand-in: every colour is the plain text."""
    depth = 0

    def __getattr__(self, n):
        return lambda x="": x


# ================================================================================================ K-04 (docs)
def _bench_table():
    """The office table of docs/BENCHMARKS.md: arm -> the six columns as text."""
    text = (REPO / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")
    sec = text.split("## Office work against Codex and Pi", 1)[1].split("\n## ", 1)[0]
    names = {"Pi": "pi", "Codex CLI": "codex", "Meshpool on Codex": "mp-on-codex", "Meshpool on Pi": "mp-on-pi",
             "Meshpool CLI": "mp-cli", "Meshpool Work": "mp-work"}
    rows = {}
    for line in sec.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[1].count("/") == 1 and cells[1][0].isdigit():
            label = re.sub(r"\s*\(.*\)$", "", cells[0])
            rows[names[label]] = cells[1:]
    return sec, rows


def _measured(run: str):
    out = {}
    for line in (REPO / "docs" / "results" / "v4" / run / "results.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            a = out.setdefault(r["arm"], {"n": 0, "ok": 0, "cost": 0.0, "calls": 0, "secs": 0.0})
            a["n"] += 1
            a["ok"] += bool(r["ok"])
            a["cost"] += r["cost"] or 0.0
            a["calls"] += r["calls"]
            a["secs"] += r["secs"]
    return out


def _money_close(shown: str, value: float) -> bool:
    """$0.0061 matches 0.00607: within half a unit of the last digit shown."""
    s = shown.lstrip("$")
    places = len(s.split(".")[1]) if "." in s else 0
    return abs(float(s) - value) <= 0.5 * 10 ** -places + 1e-9


@pytest.mark.skipif(not (REPO / "docs" / "results" / "v4" / "final-office").is_dir(), reason="docs not in this copy")
def _readme_comparison_matches_the_data(readme: str) -> None:
    """The README's "How it compares with Codex" table is docs/benchmarks/headline.json, row for row; the 0.0.18 rows
    recompute from their raw runs, the 0.0.16 rows are the MESHPOOL_RESULTS.md table, and Codex's leads are said."""
    import json
    import statistics
    from collections import defaultdict
    head = json.loads((REPO / "docs" / "benchmarks" / "headline.json").read_text(encoding="utf-8"))["suites"]
    sec = readme[readme.index("## How it compares with Codex"):]
    for x in head:
        row = f"| {x['group']} | {x['name']} | {x['meshpool']} vs {x['codex']} of {x['tasks']} | **{x['cost']:.2f}x** | " \
              f"{x['time']:.2f}x |"
        assert row in sec, row
        if x["version"] == "0.0.18":                          # recomputed from the raw rows
            rows = [json.loads(ln) for ln in (REPO / x["data"] / "results.jsonl").read_text().splitlines() if ln.strip()]
            arm = {k: [r for r in rows if r["arm"] == k] for k in ("mp-work", "codex")}
            ok = {k: sum(bool(r["ok"]) for r in v) for k, v in arm.items()}
            assert (ok["mp-work"], ok["codex"], len(arm["mp-work"])) == (x["meshpool"], x["codex"], x["tasks"]), x
            per = {k: sum(r["cost"] or 0 for r in v) / ok[k] for k, v in arm.items()}
            assert round(per["mp-work"] / per["codex"], 2) == x["cost"], x
            t = defaultdict(lambda: defaultdict(list))
            for r in rows:
                t[r["task"]][r["arm"]].append(r["secs"] or 0)
            ratio = statistics.median(statistics.median(v["mp-work"]) / statistics.median(v["codex"]) for v in t.values())
            assert round(ratio, 2) == x["time"], x
        else:                                                  # the 0.0.16 table, as published
            res = (REPO / "docs" / "results" / "MESHPOOL_RESULTS.md").read_text(encoding="utf-8")
            assert any(f"| {x['meshpool']} vs {x['codex']} |" in ln and f"**{x['cost']:.2f}x**" in ln
                       and f"| {x['time']:.2f}x |" in ln for ln in res.splitlines()), x
    assert sum(x["meshpool"] for x in head) == int(re.search(r"\*\*(\d+) vs (\d+) of (\d+) tasks", sec).group(1))
    assert "Where Codex is ahead" in sec and "OfficeBench task (12 vs 11)" in sec    # the losses are said too


def test_k04_benchmark_claims_match_the_raw_rows():
    """K-04: every number in the BENCHMARKS.md office table and the README office line comes from the raw rows in
    docs/results/v4, and neither page claims "all 7 passed" or "cheapest on every run"."""
    sec, rows = _bench_table()
    assert set(rows) == {"pi", "codex", "mp-on-codex", "mp-on-pi", "mp-cli", "mp-work"}, rows
    now, old = _measured("final-office"), _measured("baseline-0.0.12-flash")
    for arm, (old_pass, old_cost, now_pass, now_cost, now_calls, now_secs) in rows.items():
        o, n = old[arm], now[arm]
        assert old_pass == f"{o['ok']}/{o['n']}" and now_pass == f"{n['ok']}/{n['n']}", arm
        assert _money_close(old_cost, o["cost"] / o["n"]) and _money_close(now_cost, n["cost"] / n["n"]), arm
        assert int(now_calls) == n["calls"] and int(now_secs) == round(n["secs"] / n["n"]), arm
    per = {a: v["cost"] / v["n"] for a, v in now.items()}
    # "Pi is the cheapest and Codex the most reliable."
    assert min(per, key=per.get) == "pi" and max(now, key=lambda a: now[a]["ok"]) == "codex"
    # "Meshpool on Codex, costs about 2.2x Codex and 4x Pi per task."
    m = re.search(r"costs about ([\d.]+)x Codex and ([\d.]+)x Pi", sec)
    assert m, "the ratio sentence is gone: update this test with the new claim"
    assert round(per["mp-on-codex"] / per["codex"], 1) == float(m.group(1))
    assert round(per["mp-on-codex"] / per["pi"]) == float(m.group(2))
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    _readme_comparison_matches_the_data(readme)
    for page in (readme, (REPO / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")):
        assert "all 7 passed" not in page.lower() and "cheapest on every" not in page.lower()
    v2 = (REPO / "docs" / "BENCHMARKS_V2.md").read_text(encoding="utf-8")
    if "cheapest harness on every" in v2.lower():       # the old claim may stay only under the superseded notice
        assert v2.lower().index("superseded") < v2.lower().index("cheapest harness on every")
        assert 'does not support "cheapest"' in v2


# ================================================================================================ PA-agent-08/09
def _drain(keys):
    out = []
    while True:
        k = keys.read(timeout=0)
        if k is None:
            if not keys.pending:
                return out
            continue
        out.append(k)


@pytest.mark.skipif(os.name == "nt", reason="the POSIX reader; Windows has its own test below")
def test_pa_agent_08_long_non_ascii_paste_is_not_corrupted():
    """PA-agent-08: a pasted Arabic paragraph longer than one 4 KB read keeps every character (no U+FFFD where a
    character was split between two reads), as a bracketed paste and as plain typed-ahead text."""
    from meshpool_harness.agent import tui_input as K
    para = "x" + "مرحبا بالعالم " * 400                   # odd byte offset: 4096 falls inside a 2-byte character
    assert len(para.encode()) > 8000
    for wire, want in ((f"\x1b[200~{para}\x1b[201~", [("paste", para)]), (para, list(para))):
        r, w = os.pipe()
        try:
            os.write(w, wire.encode("utf-8"))
            keys = K.RawKeys(fd=r)
            assert not keys.win or os.name == "nt"
            got = _drain(keys)
        finally:
            os.close(r)
            os.close(w)
        assert "�" not in "".join(k if isinstance(k, str) else k[1] for k in got)
        assert got == want


def test_pa_agent_09_windows_multiline_paste_arrives_as_one_paste(monkeypatch):
    """PA-agent-09: on Windows a pasted 3-line block (a burst msvcrt reports with kbhit) is one ("paste", text), not
    three Enter presses; a single key is still a key. msvcrt is faked: this is not run on Windows."""
    from meshpool_harness.agent import tui_input as K
    burst = list("one\r\ntwo\r\nthree")
    fake = types.SimpleNamespace(getwch=lambda: burst.pop(0), kbhit=lambda: bool(burst))
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    keys = K.RawKeys.__new__(K.RawKeys)
    keys.pending, keys.win, keys._dec = [], True, codecs.getincrementaldecoder("utf-8")()
    assert keys.read() == ("paste", "one\ntwo\nthree")
    burst[:] = ["\r"]
    assert keys.read() == "enter"


# ================================================================================================ PA-agent-11/12
def _key_server(code, body):
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            b = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _onboard_custom(monkeypatch, base, said):
    from meshpool_harness.agent import onboard as OB
    answers = iter([base, "wrong-model-id"] * 10)
    keys = []
    monkeypatch.setattr(OB, "_ask", lambda st, q, default="": next(answers))
    def getpass(*a, **k):
        keys.append(1)
        if len(keys) > 2:                                  # a third ask: the user leaves (Ctrl+D)
            raise EOFError
        return KEY
    monkeypatch.setattr(OB.getpass, "getpass", getpass)
    monkeypatch.setattr(OB, "_p", lambda s="": said.append(s))
    monkeypatch.setattr(OB, "_use", lambda which: None)
    return OB.key_step(Plain(), custom=True), len(keys)


def test_pa_agent_11_setup_does_not_save_a_key_the_endpoint_answered_4xx(home, monkeypatch):
    """PA-agent-11: `meshpool setup` with another API key and a wrong model id: the endpoint answers 400, so the key
    and endpoint are not saved, the message says what the endpoint answered (not the network), and it asks again."""
    srv = _key_server(400, {"error": {"message": "The model `wrong-model-id` does not exist"}})
    said = []
    try:
        ok, asked = _onboard_custom(monkeypatch, f"http://127.0.0.1:{srv.server_address[1]}/v1", said)
    finally:
        srv.shutdown()
    text = "\n".join(said)
    assert not ok and asked == 3                           # asked again after each answer, never accepted
    assert "MESHPOOL_API_KEY" not in C.read() and "MESHPOOL_BASE_URL" not in C.read()
    assert "endpoint answered with an error (HTTP 400)" in text and "could not reach" not in text
    assert "internet connection" not in text


def test_pa_agent_11_a_network_failure_still_keeps_the_key(home, monkeypatch):
    """PA-agent-11: only a connection error (no HTTP answer) keeps the key, and says the network is the problem."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                              # nothing listens: connection refused
    said = []
    ok, asked = _onboard_custom(monkeypatch, f"http://127.0.0.1:{port}/v1", said)
    assert ok and asked == 1
    assert C.read().get("MESHPOOL_API_KEY") == KEY
    assert "could not reach the model" in "\n".join(said)


def test_pa_agent_12_enter_at_the_folder_prompt_asks_again(home, monkeypatch, tmp_path):
    """PA-agent-12: Enter alone at "Folder path" asks again instead of silently picking the current folder."""
    from meshpool_harness.agent import onboard as OB
    start = tmp_path / "start"
    want = tmp_path / "picked"
    start.mkdir()
    want.mkdir()
    choices = iter([3, 1])                                 # "Another folder", then the "Ask" permission mode
    typed = iter(["", "   ", str(want)])
    asked = []
    monkeypatch.setattr(OB, "_choose", lambda st, opts, default=1: next(choices))
    monkeypatch.setattr(OB, "_ask", lambda st, q, default="": (asked.append(q), next(typed))[1])
    monkeypatch.setattr(OB, "_p", lambda s="": None)
    got = OB._where_step(Plain(), start)
    assert got == want.resolve() and got != start.resolve()
    assert asked == ["Folder path"] * 3


# ================================================================================================ PA-agent-21
class _Page:
    def __init__(self, ctx, name):
        self.ctx, self.url = ctx, f"https://example.com/{name}"

    def title(self):
        return self.url.rsplit("/", 1)[1]

    def close(self):
        self.ctx.pages.remove(self)

    def bring_to_front(self):
        pass


def test_pa_agent_21_closing_a_background_tab_keeps_the_current_tab(monkeypatch):
    """PA-agent-21: with three tabs open and the first one current, closing a background tab leaves the agent on the
    first tab (it used to jump to the last tab); closing the current tab moves to one that is still open."""
    from meshpool_harness.agent import browser_mcp_server as BM
    ctx = types.SimpleNamespace(pages=[])
    ctx.pages += [_Page(ctx, "a"), _Page(ctx, "b"), _Page(ctx, "c")]
    fake = types.SimpleNamespace(ctx=ctx, page=ctx.pages[0], ensure=lambda: None, run=lambda fn, timeout=240: fn())
    monkeypatch.setattr(BM, "B", fake)
    out = BM.browser_tabs(close=1)
    assert fake.page is ctx.pages[0] and fake.page.url.endswith("/a")
    assert "0: a - https://example.com/a (current)" in out and "/b" not in out
    BM.browser_tabs(close=0)                               # the current tab: the agent moves to an open one
    assert fake.page in ctx.pages and fake.page.url.endswith("/c")


# ================================================================================================ PA-agent-23
def _acp_lines(buf):
    return [json.loads(x) for x in buf.getvalue().decode().splitlines() if x.strip()]


def test_pa_agent_23_acp_server_errors_are_messages_and_refusal():
    """PA-agent-23: a model or auth error in a turn reaches the ACP client (Zed) as an agent_message_chunk, not a
    hidden thought, and the turn ends with stopReason "refusal", not "end_turn"."""
    from meshpool_harness.agent.acp_server import Server
    srv = Server()
    srv.out = io.BytesIO()
    sid = "s1"

    class Agent:
        _stop = False

        def send(self, text):
            srv.on_event(sid, {"type": "status", "text": "thinking"})
            srv.on_event(sid, {"type": "error", "text": "meshpool HTTP 401: the endpoint rejected the API key"})
    srv.sessions[sid] = {"id": sid, "busy": False, "turn": threading.Lock(), "seq": 0, "cancelled": 0,
                         "agent": Agent(), "calls": {}}
    srv.prompt({"sessionId": sid, "prompt": [{"type": "text", "text": "hi"}]}, 7)
    end = time.time() + 5
    while time.time() < end and not any(m.get("id") == 7 for m in _acp_lines(srv.out)):
        time.sleep(0.02)
    msgs = _acp_lines(srv.out)
    reply = next(m for m in msgs if m.get("id") == 7)
    assert reply["result"]["stopReason"] == "refusal"
    ups = [m["params"]["update"] for m in msgs if m.get("method") == "session/update"]
    err = [u for u in ups if "rejected the API key" in u["content"]["text"]]
    assert err and all(u["sessionUpdate"] == "agent_message_chunk" for u in err)


def test_pa_agent_23_acp_server_skips_lines_that_are_not_objects(monkeypatch):
    """PA-agent-23: a JSON line that is not an object (an array, a number, a string) is skipped; the server keeps
    serving and answers the next request."""
    from meshpool_harness.agent.acp_server import Server
    srv = Server()
    srv.out = io.BytesIO()
    lines = b'[1, 2]\n42\n"text"\nnull\n{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}\n'
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(lines)))
    srv.serve()
    (reply,) = _acp_lines(srv.out)
    assert reply["id"] == 1 and reply["result"]["agentInfo"]["name"] == "meshpool"


# ================================================================================================ PA-agent-24
def test_pa_agent_24_cmd_shortcuts_use_ctrl_on_windows(monkeypatch):
    """PA-agent-24: on Windows the model's cmd+c / command+v / meta+a press Ctrl, not the Win key; an explicit win+r
    still presses the Win key, and macOS and Linux keep cmd. os.name is faked: this is not run on Windows."""
    from meshpool_harness.agent import computer_mcp_server as CM
    monkeypatch.setattr(CM, "os", types.SimpleNamespace(name="nt"))
    assert CM._parse_combo("cmd+c") == (["ctrl"], "c")
    assert CM._parse_combo("command+v") == (["ctrl"], "v")
    assert CM._parse_combo("meta+a") == (["ctrl"], "a")
    assert CM._parse_combo("cmd+ctrl+t") == (["ctrl"], "t")          # never ctrl twice
    assert CM._parse_combo("win+r") == (["cmd"], "r")
    monkeypatch.setattr(CM, "os", types.SimpleNamespace(name="posix"))
    assert CM._parse_combo("cmd+c") == (["cmd"], "c")


# ================================================================================================ PA-agent-26/27
@pytest.mark.parametrize("typed,want", [
    ("2 because speed", {"selected": [], "other": "2 because speed"}),
    ("2", {"selected": ["Fast"], "other": ""}),
    ("1, 2", {"selected": ["Cheap", "Fast"], "other": ""}),
    ("", {"selected": ["Cheap"], "other": ""}),
])
def test_pa_agent_26_plain_answer_starting_with_a_number_keeps_its_words(monkeypatch, typed, want):
    """PA-agent-26: in plain mode an answer like "2 because speed" is the user's own text, whole; only an answer made
    of option numbers picks options."""
    from meshpool_harness.agent import tui as T
    term = T.Terminal(out=io.StringIO(), interactive=False)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)
    q = {"header": "Pick", "question": "Which?", "multi": True,
         "options": [{"label": "Cheap", "recommended": True}, {"label": "Fast"}]}
    assert term.ask_questions("q1", [q]) == [want]


def test_pa_agent_27_print_mode_without_a_key_says_so_once(home, monkeypatch, capsys):
    """PA-agent-27: `meshpool -p "hi"` with no API key and stdin not a terminal prints the No API key message once."""
    from meshpool_harness.agent import tui as T
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert T.main(workdir=str(home), prompt="hi") == 1
    cap = capsys.readouterr()
    assert (cap.out + cap.err).count("No API key") == 1, cap


# ================================================================================================ PA-core-02
@pytest.mark.skipif(os.name == "nt", reason="the OS sandbox wraps bash_start on macOS and Linux only")
def test_pa_core_02_bash_start_runs_inside_the_sandbox_wrapper(tmp_path, monkeypatch):
    """PA-core-02: bash_start (a background command) goes through core.sandbox.wrap like bash does, with the chat's
    access mode, and runs the argv the sandbox returns."""
    from meshpool_harness.core import sandbox as SB
    from meshpool_harness.core.tools import Workspace, _bash
    if not _bash():
        pytest.skip("no bash here")
    seen = []

    def wrap(argv, root, platform=None, which=None, cwd=None, mode=None):
        seen.append((list(argv), Path(root), mode))
        return ["/usr/bin/env", "MH_WRAPPED=yes"] + list(argv)      # a stand-in sandbox that marks what it ran
    monkeypatch.setattr(SB, "wrap", wrap)
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    ws.get_mode = lambda: "ask"
    out = ws.bash_start('echo "inside=$MH_WRAPPED"')
    try:
        deadline = time.time() + 5
        while "inside=yes" not in out and time.time() < deadline:
            out += ws.bash_output(id="bg1")
            time.sleep(0.05)
    finally:
        ws.bash_stop(id="bg1")
    assert seen and seen[0][0][-1] == 'echo "inside=$MH_WRAPPED"' and seen[0][1] == tmp_path.resolve()
    assert seen[0][2] == "ask"
    assert "inside=yes" in out


# ================================================================================================ PA-core-04/13
def _acp_agent(tmp_path, monkeypatch, script="", args=()):
    from meshpool_harness.harnesses.acp import ACPAgent
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    C.ensure_home()
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    manifest = {"id": "fake", "name": "Fake", "mode": "cowork", "type": "acp", "command": sys.executable,
                "args": [script, *args], "office": False, "share_mcp": False}
    return ACPAgent(manifest, str(proj), mode="ask"), proj


def test_pa_core_04_acp_fs_keeps_line_endings_and_bom_and_refuses_git(tmp_path, monkeypatch):
    """PA-core-04: an ACP agent that edits through fs/read_text_file and fs/write_text_file keeps a CRLF file CRLF
    (and its BOM), reads plain \\n text, and cannot write inside .git."""
    a, proj = _acp_agent(tmp_path, monkeypatch)
    sent = []
    a._write = sent.append
    f = proj / "notes.txt"
    f.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    a._serve_request({"id": 1, "method": "fs/read_text_file", "params": {"path": str(f)}})
    assert sent[-1]["result"]["content"] == "one\ntwo\n"
    a._serve_request({"id": 2, "method": "fs/write_text_file", "params": {"path": str(f), "content": "one\ntwo\nthree\n"}})
    assert "error" not in sent[-1]
    assert f.read_bytes() == b"\xef\xbb\xbfone\r\ntwo\r\nthree\r\n"
    new = proj / "new.txt"
    a._serve_request({"id": 3, "method": "fs/write_text_file", "params": {"path": str(new), "content": "a\nb\n"}})
    assert new.read_bytes() == b"a\nb\n"                   # a new file gets \n on every platform
    (proj / ".git").mkdir()
    hook = proj / ".git" / "config"
    a._serve_request({"id": 4, "method": "fs/write_text_file", "params": {"path": str(hook), "content": "[core]\n"}})
    assert "error" in sent[-1] and ".git" in sent[-1]["error"]["message"]
    assert not hook.exists()


FAIL_INIT = textwrap.dedent('''
    import json, os, sys
    mark = sys.argv[1]
    first = not os.path.exists(mark)
    open(mark, "a").close()
    for line in sys.stdin:
        m = json.loads(line)
        if m.get("method") == "initialize":
            if first:
                r = {"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32000, "message": "no auth"}}
            else:
                r = {"jsonrpc": "2.0", "id": m["id"], "result": {"protocolVersion": 1, "agentInfo": {"name": "fake"}}}
            sys.stdout.write(json.dumps(r) + "\\n")
            sys.stdout.flush()
''')


def test_pa_core_13_failed_acp_initialize_kills_the_process_and_retries(tmp_path, monkeypatch):
    """PA-core-13: when initialize fails, the ACP process is killed and forgotten, so the next message starts a new
    process and runs initialize again (it used to find the old process alive and skip the handshake)."""
    from meshpool_harness.harnesses import acp as ACP
    script = tmp_path / "fail_init.py"
    script.write_text(FAIL_INIT)
    a, _proj = _acp_agent(tmp_path, monkeypatch, str(script), (str(tmp_path / "started"),))
    procs = []
    real = subprocess.Popen

    def spy(*args, **kw):
        p = real(*args, **kw)
        procs.append(p)
        return p
    monkeypatch.setattr(ACP.subprocess, "Popen", spy)
    try:
        with pytest.raises(ACP.ACPError, match="no auth"):
            a._start()
        assert a._p is None
        assert procs[0].wait(timeout=5) is not None         # the half-started process is gone
        assert a._start() is True                           # a new process, initialised again
        assert len(procs) == 2 and a._p is procs[1] and a._p.poll() is None
        assert a.agent_info == {"name": "fake"}
    finally:
        a.close()
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait(5)


# ================================================================================================ PA-core-05/29
def test_pa_core_05_chatgpt_sign_in_runs_a_pinned_codex(home, monkeypatch):
    """PA-core-05: "Sign in with ChatGPT" (the app and `meshpool setup`) runs one pinned @openai/codex version,
    never @latest."""
    from meshpool_harness import harnesses as HR
    from meshpool_harness.agent import onboard as OB
    assert re.fullmatch(r"@openai/codex@\d+\.\d+\.\d+", HR.CODEX_CLI), HR.CODEX_CLI
    for _name, _plans, pkg in OB.SUBS.values():
        assert pkg is None or "@latest" not in pkg
    ran = []
    monkeypatch.setattr(HR, "unsupported", lambda hid: "")
    monkeypatch.setattr(HR.shutil, "which", lambda n: "/usr/bin/npx" if n == "npx" else None)
    monkeypatch.setattr(HR, "_login_running", lambda: False)
    monkeypatch.setattr(HR, "_login_watchdog", lambda proc: None)
    monkeypatch.setattr(HR, "_LOGIN_ATEXIT", [True])

    class P:
        pid = 1

        def __init__(self, cmd, **kw):
            ran.append(cmd)

        def poll(self):
            return 0
    monkeypatch.setattr(subprocess, "Popen", P)
    monkeypatch.setattr(HR, "_LOGIN", {"proc": None})
    assert HR.login("codex")["ok"]
    assert ran == [["/usr/bin/npx", "-y", HR.CODEX_CLI, "login"]]
    # meshpool setup's ChatGPT step runs the same pinned package
    monkeypatch.setattr(HR, "signed_in", lambda login: bool(ran[1:]))
    monkeypatch.setattr(OB, "_ask", lambda st, q, default="": "")
    monkeypatch.setattr(OB, "_p", lambda s="": None)
    monkeypatch.setattr(OB, "_use", lambda which: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: ran.append(cmd))
    from meshpool_harness import bundled
    monkeypatch.setattr(bundled, "packaged", lambda: False)
    assert OB.sub_step(Plain(), "chatgpt")
    assert ran[1] == ["/usr/bin/npx", "-y", HR.CODEX_CLI, "login"]


def test_pa_core_29_chatgpt_sign_in_without_node_says_what_is_missing(home, monkeypatch):
    """PA-core-29: on a source checkout without Node.js, the ChatGPT sign-in says Node.js is needed (a ValueError the
    app shows), not a bare FileNotFoundError."""
    from meshpool_harness import harnesses as HR
    monkeypatch.setattr(HR, "unsupported", lambda hid: "")
    monkeypatch.setattr(HR.shutil, "which", lambda n: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("started a process without npx"))
    with pytest.raises(ValueError, match="Node.js"):
        HR.login("codex")


# ================================================================================================ PA-core-09
class _Resp:
    def __init__(self, lines, fail_after=None):
        self.status, self.headers, self._lines, self._fail = 200, {"Content-Type": "text/event-stream"}, lines, fail_after

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for i, line in enumerate(self._lines):
            if self._fail is not None and i == self._fail:
                raise ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:2427) UNEXPECTED_EOF")
            yield line


def test_pa_core_09_tls_eof_mid_stream_is_retried(home, monkeypatch):
    """PA-core-09: a TLS UNEXPECTED_EOF (an OSError) in the middle of a streamed reply is a lost connection: it is
    retried, and the second attempt's reply comes back (it used to escape as a raw ssl error)."""
    from meshpool_harness.core import endpoint as EP
    from meshpool_harness.core.wire import SimpleLedger
    monkeypatch.setenv("MESHPOOL_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    monkeypatch.setenv("MH_STREAM", "1")
    def chunk(d):
        return f"data: {json.dumps(d)}\n".encode()
    good = [chunk({"choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello"}}]}), b"\n",
            chunk({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 1, "completion_tokens": 1}}), b"\n", b"data: [DONE]\n", b"\n"]
    calls = []

    def urlopen(req, timeout=30, handlers=(), refuse_redirects=True):
        calls.append(req.full_url)
        return _Resp(good, fail_after=2) if len(calls) == 1 else _Resp(good)
    monkeypatch.setattr(EP, "urlopen", urlopen)
    from meshpool_harness.agent import core as AC
    monkeypatch.setattr(AC, "_probe_once", lambda *a, **k: "ok")      # the gateway's health probe: the endpoint is up
    out = SimpleLedger("t").chat(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=5, retries=1)
    assert len(calls) == 2
    assert out["choices"][0]["message"]["content"] == "hello"


def test_pa_core_09_tls_eof_without_retries_is_a_clear_error(home, monkeypatch):
    """PA-core-09: with no retries left the drop is a RuntimeError that says the connection was lost, not ssl's own
    exception escaping the call."""
    from meshpool_harness.core import endpoint as EP
    from meshpool_harness.core.wire import SimpleLedger
    monkeypatch.setenv("MESHPOOL_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    monkeypatch.setenv("MH_STREAM", "1")
    monkeypatch.setattr(EP, "urlopen", lambda *a, **k: _Resp([b"data: {}\n", b"\n"], fail_after=1))
    with pytest.raises(RuntimeError) as ei:
        SimpleLedger("t").chat(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=5, retries=0)
    assert "connection lost" in str(ei.value) and not isinstance(ei.value, OSError)


# ================================================================================================ PA-core-15/16
def _pi(tmp_path, monkeypatch):
    from meshpool_harness.harnesses import pi_rpc as PR
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    C.ensure_home()
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    return PR, PR.PiAgent({"id": "pi", "name": "Pi"}, str(proj), mode="ask")


def test_pa_core_15_late_eof_from_an_old_pi_does_not_end_the_new_one(tmp_path, monkeypatch):
    """PA-core-15: after a Stop that Pi ignored, a new Pi starts; the old process's reader then hits EOF. That late
    EOF must not mark the new process dead or end its turn; the new process's own EOF still does."""
    _PR, a = _pi(tmp_path, monkeypatch)
    old = types.SimpleNamespace(stdout=io.BytesIO(b""))
    new = types.SimpleNamespace(stdout=io.BytesIO(b""))
    a._p, a._alive, a._up = new, True, True
    evq = queue.Queue()
    a._reader(old, evq)
    a._dispatch(old, evq)
    assert a._alive and a._up and not a._exited.is_set()
    a._reader(new, evq)
    a._dispatch(new, evq)
    assert not a._alive and not a._up and a._exited.is_set()


@pytest.mark.parametrize("crashed", [True, False])
def test_pa_core_16_pi_restart_after_a_crash_uses_continue(tmp_path, monkeypatch, crashed):
    """PA-core-16: when Pi crashed (its process is gone, its session files remain), the next start passes
    --continue so Pi keeps its own history (tool calls included) instead of the chat being re-sent as text."""
    PR, a = _pi(tmp_path, monkeypatch)
    sess = a.dir / "pi"
    sess.mkdir(parents=True, exist_ok=True)
    if crashed:
        (sess / "2026-11-25T10-00-00_abc.jsonl").write_text('{"type":"session"}\n')
        a._p = types.SimpleNamespace(poll=lambda: -9)       # killed from outside
    a.events = [{"type": "user", "text": "earlier"}, {"type": "assistant", "text": "reply"}]
    monkeypatch.setattr(PR, "node_exe", lambda: sys.executable)
    monkeypatch.setattr(PR, "pi_cli", lambda: tmp_path / "pi.js")
    monkeypatch.setattr(PR, "prepare", lambda *a, **k: tmp_path / "pihome")
    monkeypatch.setattr(PR, "_takes_no_approve", lambda node, cli: False)
    monkeypatch.setattr(PR, "skill_args", lambda h: [])
    argv = []

    class Stop(Exception):
        pass

    def popen(args, **kw):
        argv.append(args)
        raise Stop
    monkeypatch.setattr(PR.subprocess, "Popen", popen)
    try:
        with pytest.raises(Stop):
            a._start()
    finally:
        if getattr(a, "_err", None):
            a._err.release()
    assert ("--continue" in argv[0]) is crashed
    assert getattr(a, "_carry", False) is not crashed          # no Pi session files: the history is carried as text


# ================================================================================================ PA-core-21
def test_pa_core_21_model_override_recomputes_variant_language_and_lane(monkeypatch):
    """PA-core-21: the rules say code.debug (perf, python) with low confidence and the model says docs: the route
    is docs with no code variant or language left over; a model "chat" also moves the lane to chat."""
    R = importlib.import_module("meshpool_harness.hrouter.route")
    monkeypatch.delenv("MH_HARNESS_ROUTER", raising=False)

    def decide(text, files, workdir, kind):
        return R.Route(spec="code.debug", family="code", sub="debug", variant="perf", language="python", lane="task",
                       base="code", confidence=0.4)
    monkeypatch.setattr(R, "_decide", decide)

    class Client:
        def __init__(self, sid):
            self.sid = sid

        def chat(self, **kw):
            return {"choices": [{"message": {"content": json.dumps({"id": self.sid, "confidence": 0.9})}}]}
    r = R.route("write up why the python service is slow", client=Client("docs"), model="m")
    assert (r.spec, r.family, r.sub, r.variant, r.language) == ("docs", "docs", "", "", "")
    assert r.lane == "task" and "rules said code.debug" in r.reason
    r = R.route("is it slow?", client=Client("chat"), model="m")
    assert (r.spec, r.lane, r.variant, r.language) == ("chat", "chat", "", "")
    r = R.route("fix it", client=Client("code.build"), model="m")      # another code spec: its own variant
    assert r.spec == "code.build" and r.variant != "perf" and r.language == "python"


# ================================================================================================ PA-core-23
def test_pa_core_23_openai_profile_sends_max_completion_tokens(home, monkeypatch):
    """PA-core-23: an OpenAI reasoning model (the openai profile: limits.param max_completion_tokens) on a direct
    OpenAI-compatible endpoint gets max_completion_tokens in the request; a deepseek model does not."""
    from meshpool_harness.core import client as CL
    monkeypatch.setenv("MESHPOOL_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    sent = []

    class Ledger:
        base_url, api_key = "https://api.example.com/v1", KEY

        def chat(self, **kw):
            sent.append(kw)
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    lc = CL.LiveClient.__new__(CL.LiveClient)
    lc.phase, lc.ledger = "phase_2_build", Ledger()
    lc.chat(model="gpt-6-luna", messages=[{"role": "user", "content": "hi"}], max_tokens=1234)
    assert sent[-1]["extra"].get("max_completion_tokens"), sent[-1]
    assert sent[-1]["temperature"] is None                  # the profile drops temperature too
    lc.chat(model="deepseek-v4.1-flash", messages=[{"role": "user", "content": "hi"}], max_tokens=1234)
    assert "max_completion_tokens" not in sent[-1]["extra"]


# ================================================================================================ PA-core-24
def test_pa_core_24_route_fallback_names_the_agent(home, monkeypatch):
    """PA-core-24: with no agent ready (no key, no provider), /api/route-harness falls back to the mode's default
    and shows its name, not its raw id."""
    from meshpool_harness import harness_router as HRT
    from meshpool_harness import harnesses as HR
    monkeypatch.setattr(HRT, "_ready", lambda kind: {})
    for kind in ("code", "cowork"):
        r = HRT.route("fix the failing test", kind=kind)
        want = next(d["name"] for d in HR.load_all() if d["id"] == r["harness"])
        assert r["name"] == want and r["name"] != r["harness"], r
        assert r["ranking"] == [] and "default" in r["reason"]


# ================================================================================================ PA-core-27
def test_pa_core_27_no_stale_routing_csv_in_the_tree():
    """PA-core-27: the stale app/routing.csv with test rows is gone, and nothing in the package reads it."""
    app = Path(__file__).resolve().parents[1]
    assert not (app / "routing.csv").exists()
    for f in (app / "meshpool_harness").rglob("*.py"):
        assert not re.search(r"[\"'/]routing\.csv", f.read_text(encoding="utf-8", errors="replace")), f
    RT = importlib.import_module("meshpool_harness.router.router")
    RT.route("make a bar chart of sales.csv", files=["sales.csv"])     # routing writes no log next to the app
    assert not (app / "routing.csv").exists()
