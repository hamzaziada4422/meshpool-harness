import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "loop"))
import arms  # noqa: E402
import report  # noqa: E402
import run  # noqa: E402


class Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        body = json.dumps({"choices": [{"message": {"content": "ok"}}],
                           "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                                     "prompt_tokens_details": {"cached_tokens": 600}}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


CODE = ("import sys,json,urllib.request;"
        "open('hello.py','w').write('print(\"hello\")\\n');"
        "r=urllib.request.Request(sys.argv[1]+'/chat/completions',data=json.dumps({'model':'gpt-6-luna','messages':[]}).encode(),"
        "headers={'content-type':'application/json'});urllib.request.urlopen(r).read();print('done')")


def fake_arm(*, prompt, workdir, home, base_url, model, timeout, log, **_):
    return arms.run_proc([sys.executable, "-c", CODE, base_url], cwd=workdir, env=dict(os.environ), timeout=timeout,
                         log=log) | {"reply": "done"}


def go(out, upstream, *extra):
    return run.main(["--out", str(out), "--tasks-module", str(HERE / "hello_tasks.py"), "--arms", "fake", "--reps", "2",
                     "--model", "gpt-6-luna", "--upstream", upstream, "--settle", "0", "--parallel", "2", *extra])


def test_end_to_end_and_resume(tmp_path, upstream, monkeypatch):
    monkeypatch.setitem(arms.ARMS, "fake", fake_arm)
    assert go(tmp_path, upstream) == 0
    rows = [json.loads(x) for x in (tmp_path / "results.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and all(r["ok"] and r["calls"] == 1 and r["input"] == 1000 and r["cached"] == 600
                                  and r["output"] == 100 for r in rows)
    # 400 uncached*0.10 + 600*0.01 + 100*0.50 per 1M
    assert rows[0]["cost"] == pytest.approx((400 * 0.10 + 600 * 0.01 + 100 * 0.50) / 1e6)
    assert len((tmp_path / "meter.jsonl").read_text().splitlines()) == 2
    assert (tmp_path / "work/hello/fake/1/.git").exists()
    assert json.loads((tmp_path / "run.json").read_text())["model"] == "gpt-6-luna"
    assert go(tmp_path, upstream) == 0                      # resume: nothing to do
    assert len((tmp_path / "results.jsonl").read_text().splitlines()) == 2
    assert report.main([str(tmp_path)]) == 0
    assert "fake" in (tmp_path / "REPORT.md").read_text()
    assert json.loads((tmp_path / "summary.json").read_text())["totals"]["fake"]["passed"] == 2


def test_unknown_model_has_no_cost(tmp_path, upstream, monkeypatch):
    monkeypatch.setitem(arms.ARMS, "fake", fake_arm)
    run.main(["--out", str(tmp_path), "--tasks-module", str(HERE / "hello_tasks.py"), "--arms", "fake", "--reps", "1",
              "--model", "no-such-model", "--upstream", upstream, "--settle", "0"])
    r = json.loads((tmp_path / "results.jsonl").read_text().splitlines()[0])
    assert r["cost"] is None and r["calls"] == 1


def test_report_on_synthetic(tmp_path):
    def row(task, arm, rep, ok, cost, secs):
        return {"task": task, "kind": "office", "arm": arm, "rep": rep, "ok": ok, "cost": cost, "secs": secs,
                "calls": 5, "failed_calls": 0, "input": 100, "cached": 50, "output": 10, "reasoning": None}
    rows = [row("t1", "codex", i, True, 0.01, 10) for i in (1, 2)] + \
           [row("t1", "mp-work", 1, True, 0.03, 8), row("t1", "mp-work", 2, False, 0.05, 12),
            row("t1", "pi", 1, True, 0.02, 20)]
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    report.main([str(tmp_path)])
    s = json.loads((tmp_path / "summary.json").read_text())
    assert s["per_task"]["t1"]["mp-work"]["passed"] == 1
    assert s["ratios"]["t1"]["mp-work/codex"]["cost"] == pytest.approx(4.0)
    assert any(x["arm"] == "mp-work" and x["vs"] == "codex" and "cost" in x["why"] for x in s["loses"])
    assert "Where Meshpool loses" in (tmp_path / "REPORT.md").read_text()


def test_arms_for():
    assert "mp-work" in arms.arms_for("office", None) and "mp-code" not in arms.arms_for("office", None)
    assert arms.arms_for("code", ["mp-code", "mp-work"]) == ["mp-code"]


def test_private_tmp_isolates_runs_and_never_hides_run_folders(tmp_path, monkeypatch):
    import subprocess
    from testkit.bench3 import arms as A
    if not A._private_tmp_ok():
        import pytest
        pytest.skip("no user namespaces here")
    open("/tmp/bench-shared-marker", "w").close()
    out = subprocess.run(A.private_tmp(["sh", "-c", "ls /tmp; echo x > /tmp/check.py"], "/home"),
                         capture_output=True, text=True)
    assert out.returncode == 0 and "bench-shared-marker" not in out.stdout
    import os
    assert not os.path.exists("/tmp/check.py") or open("/tmp/check.py").read() != "x\n"
    assert A.private_tmp(["true"], "/tmp/somewhere") == ["true"]       # a run folder under /tmp: no tmpfs over it
