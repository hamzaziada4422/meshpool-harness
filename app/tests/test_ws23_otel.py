"""G-16: OpenTelemetry export is off by default, carries no text unless asked, matches the ledger and receipts, and a dead
collector never slows a turn. An in-process OTLP/HTTP collector records what is posted."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meshpool_harness import telemetry
from ws23_kit import call, finish, home, new_chat, proj, script  # noqa: F401


class Collector:
    def __init__(self):
        self.posts = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.posts.append((self.path, json.loads(self.rfile.read(n) or b"{}"), dict(self.headers)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def spans(self):
        return [s for p, d, _ in self.posts if p == "/v1/traces" for rs in d["resourceSpans"]
                for ss in rs["scopeSpans"] for s in ss["spans"]]

    def metrics(self):
        return [(m["name"], dp) for p, d, _ in self.posts if p == "/v1/metrics" for rm in d["resourceMetrics"]
                for sm in rm["scopeMetrics"] for m in sm["metrics"] for dp in m["sum"]["dataPoints"]]

    def text(self):
        return json.dumps([d for _, d, _ in self.posts])

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def col():
    c = Collector()
    yield c
    c.close()


def attrs(d):
    return {a["key"]: next(iter(a["value"].values())) for a in d.get("attributes", [])}


def turn_with_tool(monkeypatch, proj):
    (proj / "a.txt").write_text("secret-file-text")
    script(monkeypatch, call("read_file", {"path": "a.txt"}), "the final reply")
    s = new_chat(proj, mode="auto")
    finish(s, "please read the SECRETPROMPT file")
    telemetry.flush()
    return s


def test_disabled_by_default_sends_nothing(monkeypatch, proj, col):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", col.url)          # an endpoint alone is not enough
    assert not telemetry.enabled()
    turn_with_tool(monkeypatch, proj)
    time.sleep(0.4)
    assert col.posts == []
    monkeypatch.setenv("MH_OTEL", "1")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    assert not telemetry.enabled() and telemetry.listener(object()) is None   # on, but nowhere to send


def test_spans_and_metrics_for_a_turn_with_a_tool(monkeypatch, proj, col):
    monkeypatch.setenv("MH_OTEL", "1")
    monkeypatch.setenv("MH_OTEL_ENDPOINT", col.url)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-api-key=abc")
    s = turn_with_tool(monkeypatch, proj)
    sp = col.spans()
    names = [x["name"] for x in sp]
    turn = next(x for x in sp if x["name"] == "meshpool.turn")
    assert names.count("meshpool.turn") == 1 and "meshpool.model_call" in names and "meshpool.tool" in names
    for x in sp:
        if x["name"] != "meshpool.turn":
            assert x["parentSpanId"] == turn["spanId"] and x["traceId"] == turn["traceId"]
    tool = next(x for x in sp if x["name"] == "meshpool.tool")
    assert attrs(tool)["tool.name"] == "read_file" and attrs(tool)["ok"] is True
    assert attrs(turn)["meshpool.session"] == s.id and turn["status"]["code"] == 1
    usage = [e for e in s.view(0)["events"] if e["type"] == "usage"]
    for kind in ("in", "cached", "out"):
        got = sum(int(dp["asInt"]) for n, dp in col.metrics()
                  if n == "gen_ai.client.token.usage" and attrs(dp)["gen_ai.token.type"] == kind)
        assert got == sum(int(u[kind]) for u in usage), kind
    assert sum(int(dp["asInt"]) for n, dp in col.metrics() if n == "meshpool.model.calls") == len(usage)
    assert sum(int(dp["asInt"]) for n, dp in col.metrics() if n == "meshpool.tool.calls") == 1
    assert {k.lower(): v for k, v in col.posts[0][2].items()}["x-api-key"] == "abc"
    res = json.loads(json.dumps(col.posts[0][1]["resourceSpans"][0]["resource"]))
    assert attrs(res)["service.name"] == "meshpool" and "meshpool.frontend" in attrs(res)


def test_cost_is_the_receipts_own_number(monkeypatch, proj, col):
    monkeypatch.setenv("MH_OTEL", "1")
    monkeypatch.setenv("MH_OTEL_ENDPOINT", col.url)
    st = telemetry._Turn(type("S", (), {"id": "x", "harness": {}, "kind": "code"})())
    telemetry.on_event(st, {"type": "task_receipt", "cost": 0.0123, "model": "m"})
    telemetry.flush()
    assert [dp["asDouble"] for n, dp in col.metrics() if n == "meshpool.cost.usd"] == [0.0123]
    import inspect
    assert "cost_of" not in inspect.getsource(telemetry) and "price_for" not in inspect.getsource(telemetry)


def test_no_text_without_the_content_switch(monkeypatch, proj, col):
    monkeypatch.setenv("MH_OTEL", "1")
    monkeypatch.setenv("MH_OTEL_ENDPOINT", col.url)
    turn_with_tool(monkeypatch, proj)
    t = col.text()
    assert "SECRETPROMPT" not in t and "the final reply" not in t and "secret-file-text" not in t


def test_content_switch_adds_text(monkeypatch, proj, col):
    monkeypatch.setenv("MH_OTEL", "1")
    monkeypatch.setenv("MH_OTEL_ENDPOINT", col.url)
    monkeypatch.setenv("MH_OTEL_CONTENT", "1")
    turn_with_tool(monkeypatch, proj)
    t = col.text()
    assert "SECRETPROMPT" in t and "the final reply" in t


def test_a_dead_collector_never_delays_or_breaks_a_turn(monkeypatch, proj):
    monkeypatch.setenv("MH_OTEL", "1")
    monkeypatch.setenv("MH_OTEL_ENDPOINT", "http://127.0.0.1:9")          # nothing listens here
    script(monkeypatch, "fine")
    s = new_chat(proj)
    t0 = time.time()
    finish(s, "hello")
    assert time.time() - t0 < 5
    st = telemetry._Turn(s)
    t1 = time.time()
    for _ in range(200):
        telemetry.on_event(st, {"type": "usage", "in": 1, "out": 1, "cached": 0, "calls": 1})
    assert time.time() - t1 < 0.5
    assert [e for e in s.view(0)["events"] if e["type"] == "assistant"]
