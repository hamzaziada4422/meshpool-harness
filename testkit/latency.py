"""Turn latency on the mock model: send a message, wait until the chat is idle. Run against two apps (0.0.9 and
0.0.10) and compare. Usage: latency.py PORT [N]"""
import json, re, statistics, sys, time, urllib.request
from ui_token import token as _ui_token  # noqa: E402  (the launch key, testkit/ui_token.py)
B = "http://127.0.0.1:" + sys.argv[1]; N = int(sys.argv[2]) if len(sys.argv) > 2 else 12
TOK = _ui_token(B)
def call(p, body=None):
    r = urllib.request.Request(B + p, data=None if body is None else json.dumps(body).encode(), headers={"X-Meshpool-Token": TOK, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read() or b"{}")
secs, tails = [], []
for i in range(N):
    v = call("/api/agent", {"workdir": "", "harness": "meshpool-auto", "kind": "cowork", "mode": "auto"})
    sid = v["id"]
    for _ in range(80):
        if call(f"/api/agent/{sid}?since=0")["state"] != "starting": break
        time.sleep(0.25)
    t0 = time.time(); call(f"/api/agent/{sid}/send", {"text": f"Say hello number {i} in one line"})
    t_end = None
    while time.time() - t0 < 120:
        time.sleep(0.05)
        v = call(f"/api/agent/{sid}?since=0")
        if t_end is None and any(e["type"] == "turn_end" for e in v["events"]):
            t_end = time.time()
        if v["state"] in ("idle", "error") and t_end is not None:
            break
    secs.append(time.time() - t0); tails.append(time.time() - (t_end or time.time()))
print(f"turns={N} median_total={statistics.median(secs):.2f}s p90_total={sorted(secs)[int(N*0.9)-1]:.2f}s median_after_turn_end={statistics.median(tails):.2f}s")
