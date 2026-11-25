"""A real /qa pass: an app running on a real model drives a second app with the browser tools and reports problems.
Usage: python build_page_e2e.py AGENT_PORT - [MAX_SECONDS] [WORKDIR]: asks the real model to build a landing page.
The agent app must be configured for the real model (DeepSeek V4.1 Flash only); the target can run on the mock."""
import json, re, sys, time, urllib.request
from ui_token import token as _ui_token  # noqa: E402  (the launch key, testkit/ui_token.py)

A = "http://127.0.0.1:" + sys.argv[1]
T = sys.argv[2]
MAX = int(sys.argv[3]) if len(sys.argv) > 3 else 840
TOK = _ui_token(A)


def call(p, body=None, t=120):
    r = urllib.request.Request(A + p, data=None if body is None else json.dumps(body).encode(),
                               headers={"X-Meshpool-Token": TOK, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=t).read() or b"{}")


PROMPT = ("Build me a landing page for my bakery, Rye & Co, in Cairo. We bake sourdough, rye loaves and cardamom buns every "
          "morning from 6am, sell at our shop on 26 July Street, and take pre-orders by WhatsApp on +20 122 555 7890. "
          "Make it look professional and make sure it works well on a phone. Save it as index.html in the current folder.")
PROMPT = __import__("os").environ.get("E2E_PROMPT") or PROMPT
WORK = sys.argv[4] if len(sys.argv) > 4 else "/tmp/lp-work"
v = call("/api/agent", {"workdir": WORK, "harness": "meshpool-code", "kind": "code", "mode": "auto"})
sid = v["id"]
for _ in range(60):
    if call(f"/api/agent/{sid}?since=0")["state"] != "starting":
        break
    time.sleep(1)
call(f"/api/agent/{sid}/send", {"text": PROMPT})
t0, seen, approved = time.time(), 0, 0
while time.time() - t0 < MAX:
    time.sleep(3)
    v = call(f"/api/agent/{sid}?since=0")
    for e in v["events"][seen:]:
        if e["type"] == "approval":
            call(f"/api/agent/{sid}/answer", {"id": e["id"], "decision": "always"})
            approved += 1
        if e["type"] == "question":              # the real model asked something: answer as a busy owner would
            qs = e.get("questions") or [{}]
            opts = qs[0].get("options") or []
            call(f"/api/agent/{sid}/answer", {"id": e.get("id"), "decision": (opts[0].get("id") if opts and isinstance(opts[0], dict) else "yes"),
                                              "note": "Leave the phone number off the page and tell me what to add."})
            print("question answered:", str(qs[0].get("question", ""))[:80], flush=True)
        if e["type"] == "tool_start":
            print("tool:", e.get("name"), str(e.get("summary", ""))[:90], flush=True)
    seen = len(v["events"])
    if v["state"] == "idle" and any(e["type"] == "turn_end" for e in v["events"]):
        break
else:
    call(f"/api/agent/{sid}/stop", {})
    print("STOPPED at the time limit", flush=True)
v = call(f"/api/agent/{sid}?since=0")
texts = [e.get("text", "") for e in v["events"] if e["type"] == "assistant"]
print("\n=== REPORT ===\n" + (texts[-1] if texts else "(no reply)"))
print("\nusage:", json.dumps(v.get("usage")), "approvals:", approved, "seconds:", round(time.time() - t0))
