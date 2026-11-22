"""A real /qa pass: an app running on a real model drives a second app with the browser tools and reports problems.
Usage: python qa_agent.py AGENT_PORT TARGET_PORT [MAX_SECONDS]
The agent app must be configured for the real model (DeepSeek V4.1 Flash only); the target can run on the mock."""
import json, re, sys, time, urllib.request

A = "http://127.0.0.1:" + sys.argv[1]
T = sys.argv[2]
MAX = int(sys.argv[3]) if len(sys.argv) > 3 else 840
TOK = re.search(r'token: "([^"]+)"', urllib.request.urlopen(A + "/").read().decode()).group(1)


def call(p, body=None, t=120):
    r = urllib.request.Request(A + p, data=None if body is None else json.dumps(body).encode(),
                               headers={"X-Meshpool-Token": TOK, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=t).read() or b"{}")


PROMPT = (f"Do a UI and UX pass of the Meshpool app running at http://127.0.0.1:{T}/ (open it with the browser tools; "
          "it is already running). Cover these screens: the home screen, Memory (all four tabs), Your work, Scheduled, "
          "Settings > Suggestions, Settings > Notifications, and a new chat's composer buttons. Do each at desktop width "
          "(1280x800) and phone width (390x844); light mode only. Take a screenshot of each problem. Report issues most "
          "severe first: what you saw, the likely cause and the fix. Look for broken flows, console errors, overlapping "
          "or cut-off elements, poor contrast, small tap targets, missing empty, loading and error states, and unclear "
          "wording. Do not change any files. Keep it to about 25 tool calls, then write the report.")
v = call("/api/agent", {"workdir": "/tmp/qa-work", "harness": "meshpool-code", "kind": "code", "mode": "auto"})
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
