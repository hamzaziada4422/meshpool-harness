"""Measure what Meshpool adds to Pi's requests (no real model): a capture endpoint that logs every request body and
answers from a script, plus two arms, vanilla Pi and Meshpool driving Pi (harnesses/pi_rpc.py).

    python testkit/lean_pi.py --out /tmp/lean [--task code|office] [--msg TEXT]

Prints one line per request (tokens = chars/3.5 over the JSON, the estimate testkit/mock_llm.py uses). Pi needs
app/vendor/pi (npm i @earendil-works/pi-coding-agent@1.5.2 --prefix app/vendor/pi).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

CODE_MSG = "Create hello.py that prints hello, then run it."
OFFICE_MSG = ("In inventory.xlsx, add a Value column (qty times unit cost) as live formulas. Raise the unit cost of "
              "every item in the Cables category by 10%. Save over the same file.")


def toks(obj) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return int(len(s) / 3.5) + 1


class Capture:
    """OpenAI chat-completions endpoint that logs each request body. Step 0 calls `call` (tool name, args) when the
    request declares that tool; later steps answer in text."""

    def __init__(self, call=("bash", {"command": "echo hi"}), script=None):
        self.rows: list[dict] = []
        self.call = call
        self.script = script                  # [(tool name suffix, args)]: step n calls the n-th entry, then text
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps({"data": [{"id": "deepseek-v4.1-flash"}]}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.rows.append(body)
                msgs = body.get("messages") or []
                done = [m for m in msgs if m.get("role") == "tool"]
                names = {(t.get("function") or {}).get("name") for t in body.get("tools") or []}
                use = outer.call if (outer.call and not done and outer.call[0] in names) else None
                if outer.script is not None:
                    step = len(done)
                    use = None
                    if step < len(outer.script):
                        suf, args = outer.script[step]
                        full = next((n for n in names if n and n.endswith(suf)), suf)
                        use = (full, args)
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()

                def ev(d):
                    self.wfile.write(b"data: " + json.dumps(d).encode() + b"\n\n"); self.wfile.flush()
                if use:
                    ev({"id": "x", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": f"c{len(msgs)}",
                        "type": "function", "function": {"name": use[0], "arguments": json.dumps(use[1])}}]}}]})
                    fin = "tool_calls"
                else:
                    ev({"id": "x", "choices": [{"index": 0, "delta": {"content": "Done."}}]})
                    fin = "stop"
                ev({"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": fin}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 5}})
                self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()

    def sizes(self, i: int) -> dict:
        b = self.rows[i]
        msgs = b.get("messages") or []
        sysm = [m for m in msgs if m.get("role") in ("system", "developer")]
        rest = [m for m in msgs if m.get("role") not in ("system", "developer")]
        return {"total": toks({"messages": msgs, "tools": b.get("tools") or []}), "system": toks(sysm),
                "tools": toks(b.get("tools") or []), "n_tools": len(b.get("tools") or []), "messages": toks(rest)}


def pi_cli() -> Path:
    from meshpool_harness.harnesses import pi_rpc
    cli = pi_rpc.pi_cli()
    if not cli:
        raise SystemExit("Pi is not installed under app/vendor/pi")
    return cli


def run_vanilla(cap: Capture, msg: str, work: Path, home: Path) -> None:
    pd = home / "pi-vanilla"
    pd.mkdir(parents=True, exist_ok=True)
    models = {"providers": {"meshpool": {"baseUrl": cap.url, "api": "openai-completions", "apiKey": "k",
              "compat": {"supportsDeveloperRole": False},
              "models": [{"id": "deepseek-v4.1-flash", "name": "x", "reasoning": False, "input": ["text"],
                          "contextWindow": 128000, "maxTokens": 16384,
                          "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}]}}}
    (pd / "models.json").write_text(json.dumps(models))
    env = dict(os.environ, PI_CODING_AGENT_DIR=str(pd), PI_SKIP_VERSION_CHECK="1", PI_TELEMETRY="0")
    subprocess.run(["node", str(pi_cli()), "--provider", "meshpool", "--model", "deepseek-v4.1-flash", "-p", msg],
                   cwd=work, env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)


def meshpool_body(msg: str, work: str, office: bool = False, chat: str = "c1") -> str:
    """The first message the app hands PiAgent (ui/agent_api.py): the user's text, the capability note when it asks
    for a file, then the hidden context block (Pi's office rules ride on the note, so office=False there)."""
    from meshpool_harness import assistant as AS
    from meshpool_harness import capabilities as CAP
    caps = CAP.needs(msg) or (["edit"] if CAP.edits(msg) else [])
    body = msg + (CAP.note(caps) if caps else "")
    return body + "\n\n\u200b" + AS.context_block(msg, work, "", chat_id=chat, rules=True, tools="deferred",
                                                  office=bool(office and not caps))


def run_meshpool(cap: Capture, msg: str, work: Path, home: Path, office: bool) -> None:
    os.environ.update({"MESHPOOL_HOME": str(home), "MESHPOOL_BASE_URL": cap.url, "MESHPOOL_API_KEY": "k",
                       "MH_MODEL": "deepseek-v4.1-flash", "MH_GRADER": "0", "MH_LEARN": "0"})
    (home / "meshpool.config").write_text(
        f"MESHPOOL_API_KEY=k\nMESHPOOL_BASE_URL={cap.url}\nMH_MODEL=deepseek-v4.1-flash\n")
    from meshpool_harness import config as C
    C.ensure_home()
    from meshpool_harness.harnesses import pi_rpc
    a = pi_rpc.PiAgent({"id": "pi", "type": "pi"}, str(work), emit=lambda e: None, mode="auto")
    a.office_on = office
    try:
        a.send(meshpool_body(msg, str(work), office))
    finally:
        a.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--task", default="code", choices=["code", "office"])
    ap.add_argument("--msg", default="")
    a = ap.parse_args()
    msg = a.msg or (OFFICE_MSG if a.task == "office" else CODE_MSG)
    out = Path(a.out or tempfile.mkdtemp(prefix="lean_pi_"))
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    for arm in ("vanilla", "meshpool"):
        cap = Capture()
        work = out / arm / "work"
        work.mkdir(parents=True, exist_ok=True)
        home = out / arm / "home"
        home.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        if arm == "vanilla":
            run_vanilla(cap, msg, work, home)
        else:
            run_meshpool(cap, msg, work, home, a.task == "office")
        (out / f"{arm}.requests.json").write_text(json.dumps(cap.rows, indent=1))
        res[arm] = [cap.sizes(i) for i in range(len(cap.rows))]
        print(f"{arm}: {len(cap.rows)} requests in {time.time() - t0:.0f}s")
        for i, s in enumerate(res[arm]):
            print(f"  #{i}: total {s['total']}  system {s['system']}  tools {s['tools']} ({s['n_tools']})  messages {s['messages']}")
        cap.close()
    if res["vanilla"] and res["meshpool"]:
        print(f"first request: vanilla {res['vanilla'][0]['total']}  meshpool {res['meshpool'][0]['total']}  "
              f"delta {res['meshpool'][0]['total'] - res['vanilla'][0]['total']}")
    print("out:", out)


if __name__ == "__main__":
    main()
