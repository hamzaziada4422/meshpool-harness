"""Deck matrix: which harnesses actually produce a PowerPoint file when a user asks for one in a chat.

Runs the real desktop app (one fresh server, MESHPOOL_HOME, HOME and empty workdir per run), creates a chat the way
the app's page does (POST /api/agent with the first message as `task`, then /send), waits for the turn to finish,
then looks for .pptx files and records slides, refusals, tools called, the harness Auto chose, time and tokens.

    <python with the app's requirements> testkit/deck_matrix.py --out RESULTS_DIR [--only name,name] [--parallel 3]
    <python> testkit/deck_matrix.py --out RESULTS_DIR --mock      # no paid calls: the tool list each harness offers

Real runs use the model in --model on --base-url; the key is the literal "proxy-injected" (a network proxy supplies
the real one). Spend is read from the gateway's X-Litellm-Key-Spend header and the matrix stops past --budget.
"""
from __future__ import annotations

import argparse
import concurrent.futures as CF
import json
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app"
DECK = "Make a 3-slide PowerPoint about the history of coffee and save it in this folder."
TARGETS = ("meshpool-work", "meshpool-code", "pi", "hermes")
REFUSAL = re.compile(r"[^.!?\n]*\b(can't|cannot|can not|unable|not able|don't have|do not have)\b[^.!?\n]*[.!?]?", re.I)
PPTX_REF = re.compile(r"[\w./~\-]*[\w\-]+\.pptx", re.I)


def runs() -> list[dict]:
    out = []
    for h in ("meshpool-work", "meshpool-code", "pi", "hermes", "meshpool-auto"):
        for k in ("cowork", "code"):
            out.append({"name": f"{h}-{k}", "harness": h, "kind": k, "messages": [DECK]})
    out.append({"name": "meshpool-auto-cowork-second-msg", "harness": "meshpool-auto", "kind": "cowork",
                "messages": ["hi, what can you help me with?", DECK]})
    for i, p in enumerate(("can you create a ppt on coffee", "I need slides for a talk about coffee",
                           "make a deck about coffee"), 1):
        out.append({"name": f"meshpool-auto-cowork-phrasing{i}", "harness": "meshpool-auto", "kind": "cowork",
                    "messages": [p]})
    return out


# ---------- gateway spend ----------------------------------------------------------------------------------------

def _ssl_ctx():
    ca = os.environ.get("SSL_CERT_FILE") or ("/root/.ccr/ca-bundle.crt" if Path("/root/.ccr/ca-bundle.crt").exists() else None)
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


def spend(base_url: str, model: str) -> float | None:
    """The key's spend so far (USD), from the header the gateway puts on every answer: one 5-token call."""
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                 data=json.dumps({"model": model, "messages": [{"role": "user", "content": "ok"}],
                                                  "max_tokens": 3}).encode(),
                                 headers={"Authorization": "Bearer proxy-injected", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as r:
            v = r.headers.get("X-Litellm-Key-Spend")
            return float(v) if v else None
    except Exception:  # noqa: BLE001
        return None


# ---------- the app ----------------------------------------------------------------------------------------------

def a_app() -> Path:
    return Path(os.environ.get("DECK_MATRIX_APP") or APP)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class App:
    def __init__(self, root: Path, python: str, config: str):
        self.root = root
        self.home, self.fakehome = root / "home", root / "fakehome"
        for d in (self.home, self.fakehome):
            d.mkdir(parents=True, exist_ok=True)
        (self.home / "meshpool.config").write_text(config)
        self.port = free_port()
        # As testkit/run_all.sh: no credential of the tester's reaches the app or the harnesses it starts.
        env = {k: v for k, v in os.environ.items()
               if not re.match(r"^(CLAUDE_|ANTHROPIC_|OPENAI_|CODEX_|AWS_|GH_|GITHUB_|GOOGLE_|AZURE_|GEMINI_)", k)}
        env.update(HOME=str(self.fakehome), MESHPOOL_HOME=str(self.home), MH_BROWSER_HEADLESS="1",
                   PYTHONPYCACHEPREFIX=str(root.parent / ".pycache"))
        self.log = open(root / "app.log", "w")
        self.p = subprocess.Popen([python, "-m", "meshpool_harness", "ui", "--port", str(self.port), "--no-browser"],
                                  cwd=str(a_app()), env=env, stdout=self.log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
        self.base = f"http://127.0.0.1:{self.port}"
        end, page = time.time() + 120, ""
        while time.time() < end:
            k = ""
            try:
                k = re.search(r"\?k=([\w-]+)", (root / "app.log").read_text(errors="replace")).group(1)
                with urllib.request.urlopen(f"{self.base}/?k={k}", timeout=3) as r:   # the launch key it printed
                    page = r.read().decode("utf-8", "replace")
                    break
            except Exception:  # noqa: BLE001
                if self.p.poll() is not None:
                    break
                time.sleep(1)
        m = re.search(r'token: "([^"]+)"', page)
        if not m:
            self.close()
            raise RuntimeError(f"the app did not start (see {root / 'app.log'})")
        self.tok = m.group(1)

    def api(self, path: str, body=None, timeout=60):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Meshpool-Token": self.tok, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"http_error": e.code, "body": e.read().decode("utf-8", "replace")[:2000]}

    def close(self):
        try:
            os.killpg(self.p.pid, signal.SIGTERM)
            self.p.wait(10)
        except Exception:  # noqa: BLE001
            try:
                os.killpg(self.p.pid, signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass
        self.log.close()


# ---------- one run ----------------------------------------------------------------------------------------------

def wait_turn(app: App, sid: str, n0: int, limit: float, rec: dict, grace: float = 0, watch: list | None = None) -> dict:
    """Poll until this turn ends: idle twice in a row with a turn_end/assistant/error event after index n0.
    Answers approvals with yes and ask_user questions with "go ahead"; both are recorded. Past `limit` the run counts
    as timed out, but the turn may go on for `grace` more seconds (a deck job still building) before it is stopped;
    meanwhile the folders in `watch` are checked for the first .pptx, and when it appeared is recorded."""
    t0 = time.time()
    end, v, idle_seen = t0 + limit + grace, {}, 0
    answered = set()
    while time.time() < end:
        if watch and rec.get("first_pptx_s") is None and any(any(Path(w).rglob("*.pptx")) for w in watch if Path(w).exists()):
            rec["first_pptx_s"] = round(time.time() - t0, 1)
        if time.time() - t0 > limit:
            rec["timed_out"] = True
        time.sleep(2)
        v = app.api(f"/api/agent/{sid}?since=0")
        evs = v.get("events") or []
        for e in evs:
            if e.get("type") == "approval" and e.get("id") not in answered:
                answered.add(e.get("id"))
                rec["approvals"].append(str(e.get("text") or e.get("title") or e.get("summary") or "")[:300])
                app.api(f"/api/agent/{sid}/answer", {"id": e["id"], "decision": "yes"})
            if e.get("type") == "question" and e.get("timeout_s") and e.get("id") not in answered:
                answered.add(e.get("id"))
                rec["questions"].append(e.get("questions"))
                app.api(f"/api/agent/{sid}/reply", {"question_id": e["id"],
                                                    "answers": "Go ahead, use your best judgment."})
        done = any(e.get("i", -1) >= n0 and e.get("type") in ("turn_end", "assistant", "error", "interrupted")
                   for e in evs)
        idle_seen = idle_seen + 1 if (v.get("state") == "idle" and done) else 0
        if idle_seen >= 2:
            if rec["timed_out"]:
                rec["finished_after_s"] = round(time.time() - t0, 1)
            return v
    rec["timed_out"] = True
    rec["stopped"] = True
    app.api(f"/api/agent/{sid}/stop", {})
    time.sleep(3)
    return app.api(f"/api/agent/{sid}?since=0")


def slides(p: Path):
    try:
        from pptx import Presentation
        return len(Presentation(str(p)).slides)
    except Exception as e:  # noqa: BLE001
        return f"unreadable: {e}"[:200]


def find_decks(roots: list[Path], since: float) -> list[dict]:
    seen, out = set(), []
    for r in roots:
        if not r.exists():
            continue
        for p in r.rglob("*.pptx"):
            try:
                rp = p.resolve()
                if rp in seen or p.stat().st_mtime < since - 5:
                    continue
                seen.add(rp)
                out.append({"path": str(p), "bytes": p.stat().st_size, "slides": slides(p)})
            except OSError:
                continue
    return out


def run_one(spec: dict, out: Path, a, guard: dict) -> dict:
    root = out / "runs" / spec["name"]
    if root.exists():
        subprocess.run(["rm", "-rf", str(root)])
    work = root / "work"
    work.mkdir(parents=True)
    rec = {"name": spec["name"], "harness": spec["harness"], "kind_requested": spec["kind"], "messages": spec["messages"],
           "limit": a.limit, "first_pptx_s": None,
           "approvals": [], "questions": [], "timed_out": False, "turns": []}
    if guard.get("stop"):
        rec["skipped"] = guard["stop"]
        return rec
    t_start = time.time()
    app = App(root, a.python, a.config)
    try:
        hs = {h["id"]: h for h in (app.api("/api/harnesses").get("harnesses") or [])}
        h = hs.get(spec["harness"]) or {}
        rec["harness_info"] = {k: h.get(k) for k in ("id", "available", "ready", "error", "unsupported", "setup",
                                                     "install", "type", "mode")}
        v = app.api("/api/agent", {"workdir": str(work), "harness": spec["harness"], "kind": spec["kind"],
                                   "mode": "auto", "task": spec["messages"][0]})
        if "id" not in v:
            rec["create_error"] = v
            return rec
        sid = v["id"]
        rec.update(session=sid, workdir=v.get("workdir"), kind=v.get("kind"), model=v.get("model"),
                   harness_used=(v.get("harness") or {}).get("id"))
        statuses = [e for e in v.get("events", []) if e.get("type") == "status"]
        auto = [e.get("auto") for e in statuses if e.get("auto")]
        rec["auto"] = auto[0] if auto else None
        if rec["harness_used"] not in TARGETS:
            rec["guard"] = f"harness {rec['harness_used']} is not one of {TARGETS}: not run"
            app.api(f"/api/agent/{sid}/stop", {})
            return rec
        if not a.mock and "deepseek" not in str(v.get("model") or "").lower() and rec["harness_used"] not in ("hermes", "pi"):
            rec["guard"] = f"model {v.get('model')!r} is not {a.model}: not run"
            return rec
        for msg in spec["messages"]:
            cur = app.api(f"/api/agent/{sid}?since=0")
            n0 = cur.get("total") or len(cur.get("events") or [])
            t0 = time.time()
            app.api(f"/api/agent/{sid}/send", {"text": msg, "since": 10**9})
            mcp_at_send = app.api(f"/api/agent/{sid}?since=0").get("mcp")
            last = msg is spec["messages"][-1]
            v = wait_turn(app, sid, n0, a.limit, rec, grace=a.grace if last else 0,
                          watch=[str(work), str(app.home)] if last else None)
            rec["turns"].append({"text": msg, "seconds": round(time.time() - t0, 1), "mcp_at_send": mcp_at_send,
                                 "events_from": n0})
            if rec["timed_out"]:
                break
        evs = v.get("events") or []
        rec["seconds"] = sum(t["seconds"] for t in rec["turns"])
        rec["deck_turn_seconds"] = rec["turns"][-1]["seconds"] if rec["turns"] else None
        rec["usage"] = v.get("usage")
        rec["model_final"] = v.get("model")
        rec["mcp_final"] = v.get("mcp")
        rec["statuses"] = [{k: e.get(k) for k in ("text", "auto", "route", "using") if e.get(k) is not None}
                           for e in evs if e.get("type") == "status"]
        rec["tools"] = [{"name": e.get("name"), "summary": str(e.get("summary") or "")[:300], "turn_from": e.get("i")}
                        for e in evs if e.get("type") == "tool_start"]
        rec["tool_outputs"] = [str(e.get("output") or "")[:600] for e in evs if e.get("type") == "tool_end"]
        assistant = [e.get("text") or "" for e in evs if e.get("type") == "assistant" and e.get("text")]
        rec["assistant"] = assistant
        rec["errors"] = [str(e.get("text") or "")[:500] for e in evs if e.get("type") == "error"]
        last_from = rec["turns"][-1]["events_from"] if rec["turns"] else 0
        deck_turn_text = " ".join(e.get("text") or "" for e in evs
                                  if e.get("type") == "assistant" and e.get("i", 0) >= last_from)
        rec["refusal_sentences"] = sorted({m.group(0).strip() for m in REFUSAL.finditer(deck_turn_text)})
        rec["pptx_mentioned"] = sorted({m.group(0) for m in PPTX_REF.finditer(" ".join(assistant + rec["tool_outputs"]))})
        rec["decks"] = find_decks([work, app.home, app.fakehome], t_start)
        rec["decks_elsewhere"] = [d for d in find_decks([Path("/tmp")], t_start)
                                  if not d["path"].startswith((str(out), "/tmp/pytest-of-", "/tmp/claude-"))]
        rec["events"] = evs
    finally:
        app.close()
    return rec


# ---------- mock: which tools each harness offers ------------------------------------------------------------------

def mock_tools(out: Path, a) -> dict:
    mroot = out / "mock"
    subprocess.run(["rm", "-rf", str(mroot)])
    mroot.mkdir(parents=True)
    mport = free_port()
    log = mroot / "mock.jsonl"
    mp = subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport), str(log)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    a.config = f"MESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\nMESHPOOL_API_KEY=test-key-1234\nMH_MODEL={a.model}\n"
    a.limit, a.grace = 60, 0
    res = {}
    try:
        for h, k in (("meshpool-code", "code"), ("meshpool-code", "cowork"), ("meshpool-work", "cowork")):
            n = sum(1 for _ in open(log)) if log.exists() else 0
            spec = {"name": f"mock-{h}-{k}", "harness": h, "kind": k, "messages": [DECK]}
            rec = run_one(spec, mroot, a, {})
            lines = [json.loads(x) for x in open(log).read().splitlines()[n:]] if log.exists() else []
            res[spec["name"]] = {"requests": len(lines), "tool_names_first": lines[0]["tool_names"] if lines else None,
                                 "tool_names_all": sorted({t for x in lines for t in x["tool_names"]}),
                                 "mcp_at_send": (rec.get("turns") or [{}])[0].get("mcp_at_send"),
                                 "mcp_final": rec.get("mcp_final")}
    finally:
        os.killpg(mp.pid, signal.SIGTERM)
    (out / "mock_tools.json").write_text(json.dumps(res, indent=1))
    return res


# ---------- report -----------------------------------------------------------------------------------------------

def table(recs: list[dict]) -> str:
    rows = ["| run | harness (used) | kind | first message | pptx produced | slides | refused | tools used | seconds | tokens in/out |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r in recs:
        if r.get("skipped") or r.get("create_error") or r.get("guard"):
            rows.append(f"| {r['name']} | {r['harness']} | {r['kind_requested']} | {r['messages'][0][:40]} | "
                        f"not run: {str(r.get('skipped') or r.get('create_error') or r.get('guard'))[:120]} |  |  |  |  |  |")
            continue
        used = r.get("harness_used")
        h = r["harness"] + (f" -> {used}" if used and used != r["harness"] else "")
        decks = r.get("decks") or []
        fp = r.get("first_pptx_s")
        late = bool(decks) and r.get("timed_out") and (fp is None or fp > r.get("limit", 360))
        tools = {}
        for t in r.get("tools") or []:
            tools[t["name"]] = tools.get(t["name"], 0) + 1
        ts = ", ".join(f"{n}x{c}" if c > 1 else str(n) for n, c in tools.items()) or "none"
        u = r.get("usage") or {}
        refused = "y" if r.get("refusal_sentences") else "n"
        sec = f"{r.get('seconds', 0):.0f}" + (" (timed out at 6 min" + (", stopped)" if r.get("stopped") else
                                                                          ", ended later)") if r.get("timed_out") else "")
        msg = " / ".join(m[:40] for m in r["messages"])
        rows.append(f"| {r['name']} | {h} | {r.get('kind') or r['kind_requested']} | {msg} | "
                    f"{('late (' + str(fp) + ' s)') if late else ('y' if decks else 'n')} | {', '.join(str(d['slides']) for d in decks) or '-'} | {refused} | "
                    f"{ts} | {sec} | {u.get('in', 0)}/{u.get('out', 0)} |")
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--python", default=sys.executable, help="the Python the app (and Hermes) runs with")
    ap.add_argument("--base-url", default="https://llm.meshpool.ai/v1")
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--limit", type=float, default=360, help="seconds to wait per message")
    ap.add_argument("--app", default=str(APP), help="the app folder to run (e.g. a worktree of an older commit)")
    ap.add_argument("--grace", type=float, default=300, help="extra seconds a timed-out deck turn may run before it is stopped")
    ap.add_argument("--budget", type=float, default=2.5, help="stop starting runs past this gateway spend (USD)")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--only", default="", help="comma-separated run names")
    ap.add_argument("--mock", action="store_true", help="tool lists against testkit/mock_llm.py, no paid calls")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["DECK_MATRIX_APP"] = str(Path(a.app).resolve())
    print(f"app code: {a_app()}", flush=True)
    if a.mock:
        print(json.dumps(mock_tools(out, a), indent=1))
        return
    a.config = f"MESHPOOL_BASE_URL={a.base_url}\nMESHPOOL_API_KEY=proxy-injected\nMH_MODEL={a.model}\n"
    todo = [r for r in runs() if not a.only or r["name"] in a.only.split(",")]
    s0 = spend(a.base_url, a.model)
    print(f"gateway spend at start: {s0}", flush=True)
    guard, lock = {}, threading.Lock()

    def one(spec):
        with lock:
            s = spend(a.base_url, a.model)
            if s is not None and s0 is not None and s - s0 > a.budget:
                guard["stop"] = f"budget: spent {s - s0:.2f} USD"
        t = time.time()
        try:
            rec = run_one(spec, out, a, guard)
        except Exception as e:  # noqa: BLE001
            rec = {"name": spec["name"], "harness": spec["harness"], "kind_requested": spec["kind"],
                   "messages": spec["messages"], "create_error": f"{type(e).__name__}: {e}"}
        rec["spend_after"] = spend(a.base_url, a.model)
        (out / "raw").mkdir(exist_ok=True)
        (out / "raw" / f"{spec['name']}.json").write_text(json.dumps(rec, indent=1, default=str))
        print(f"{spec['name']}: decks={len(rec.get('decks') or [])} refused={bool(rec.get('refusal_sentences'))} "
              f"tools={[x['name'] for x in rec.get('tools') or []]} {time.time() - t:.0f}s", flush=True)
        return rec

    with CF.ThreadPoolExecutor(a.parallel) as ex:
        recs = list(ex.map(one, todo))
    allrecs = []
    for r in runs():
        p = out / "raw" / f"{r['name']}.json"
        if p.exists():
            allrecs.append(json.loads(p.read_text()))
    (out / "table.md").write_text(table(allrecs) + "\n")
    print(table(allrecs))
    print(f"gateway spend at end (may lag): {spend(a.base_url, a.model)} (start {s0})")


if __name__ == "__main__":
    main()
