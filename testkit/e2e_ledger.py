"""API checks for the feature-ledger additions, against a running app (port 8799) and the mock model (port 8911):
undo only the files or only the conversation, folder trust, per-chat effort, search inside files and the
cost-free receipt. Usage: python e2e_ledger.py PROJECT_DIR   (exits 1 when a check fails)."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("MP_BASE", "http://127.0.0.1:8799")
TOK = re.search(r'token: "([^"]+)"', urllib.request.urlopen(BASE + "/").read().decode()).group(1)
P = sys.argv[1]
RESULTS = []


def call(path, body=None, timeout=120):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Meshpool-Token": TOK, "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return {"_http": e.code, **json.loads(e.read() or b"{}")}
        except ValueError:
            return {"_http": e.code}


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(detail)[:300]}"), flush=True)


def turn(sid, text):
    n = sum(1 for e in call(f"/api/agent/{sid}?since=0").get("events", []) if e["type"] == "turn_end")
    call(f"/api/agent/{sid}/send", {"text": text})
    for _ in range(240):
        v = call(f"/api/agent/{sid}?since=0")
        if v.get("state") == "idle" and sum(1 for e in v["events"] if e["type"] == "turn_end") > n:
            return v
        time.sleep(0.5)
    return v


os.makedirs(P, exist_ok=True)
subprocess.run("git init -q && echo base > base.txt && git add -A && git -c user.email=t@t -c user.name=t commit -qm i",
               shell=True, cwd=P)
q = urllib.parse.quote(P)

# ---------------------------------------------------------------- undo only the files, then only the conversation
v = call("/api/agent", {"workdir": P, "harness": "meshpool-code", "kind": "code", "mode": "auto"})
sid = v["id"]
v = turn(sid, "say hello")
check("the turn wrote hello.py", os.path.exists(os.path.join(P, "hello.py")), [e.get("text") for e in v["events"]][-3:])
users_before = sum(1 for e in v["events"] if e["type"] == "user")
r = call(f"/api/agent/{sid}/revert", {"turn": 1, "what": "code", "since": 0})
check("undo only the files removes hello.py", "_http" not in r and not os.path.exists(os.path.join(P, "hello.py")), r)
check("undo only the files keeps the conversation",
      sum(1 for e in r.get("events", []) if e["type"] == "user") == users_before, [e["type"] for e in r.get("events", [])])
v = turn(sid, "say hello")
check("the next turn writes hello.py again", os.path.exists(os.path.join(P, "hello.py")))
r = call(f"/api/agent/{sid}/revert", {"turn": 2, "what": "chat", "since": 0})
check("undo only the conversation keeps the files", "_http" not in r and os.path.exists(os.path.join(P, "hello.py")), r)
check("undo only the conversation drops the later messages",
      sum(1 for e in call(f"/api/agent/{sid}?since=0")["events"] if e["type"] == "user") == users_before)
check("an unknown undo choice is refused", call(f"/api/agent/{sid}/revert", {"turn": 1, "what": "everything"}).get("_http") == 400)

# ---------------------------------------------------------------- effort per chat
r = call(f"/api/agent/{sid}/effort", {"effort": "low", "since": 1e9})
check("effort is set for a chat", r.get("effort") == "low", r.get("effort"))
check("a bad effort is refused", call(f"/api/agent/{sid}/effort", {"effort": "max"}).get("_http") == 400)
v2 = call("/api/agent", {"workdir": P, "harness": "meshpool-code", "kind": "code", "effort": "high"})
check("a new chat starts with the chosen effort", call(f"/api/agent/{v2['id']}?since=0").get("effort") == "high")

# ---------------------------------------------------------------- folder trust
os.makedirs(os.path.join(P, ".meshpool"), exist_ok=True)
with open(os.path.join(P, ".meshpool", "permissions.json"), "w") as f:
    json.dump({"allow": ["bash:*"]}, f)
st = call(f"/api/trust?root={q}")
check("an untrusted folder with allow rules is reported", st.get("trusted") is False and st.get("risky"), st)
check("the chat view carries the trust note", (call(f"/api/agent/{sid}?since=1000000").get("trust") or {}).get("files"))
st = call("/api/trust", {"root": P, "action": "trust"})
check("the folder can be trusted", st.get("trusted") is True, st)
check("trust is refused for a missing folder", call("/api/trust", {"root": P + "/nope"}).get("_http") == 400)
call("/api/trust", {"root": P, "action": "revoke"})

# ---------------------------------------------------------------- search inside files
with open(os.path.join(P, "notes.md"), "w") as f:
    f.write("first\nthe needle line\n")
r = call(f"/api/fs/search?root={q}&q=needle&content=1")
check("search inside files finds the line", any(x["path"] == "notes.md" and x["line"] == 2 for x in r.get("lines", [])), r)

# ---- scheduled tasks: each run in a new worktree of a git folder
G = os.path.join(os.path.dirname(os.path.abspath(P)), os.path.basename(P.rstrip("/")) + "-sched-git")
os.makedirs(G, exist_ok=True)
with open(os.path.join(G, "app.py"), "w") as f:
    f.write("x = 1\n")
subprocess.run("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init", shell=True, cwd=G,
               check=True)
r = call("/api/assistant", {"action": "task_add", "name": "Nightly tidy", "prompt": "Say hi", "when": "daily",
                            "time": "03:00", "folder": G, "kind": "code", "worktree": True})
t = next((x for x in r.get("tasks", []) if x.get("name") == "Nightly tidy"), {})
check("a scheduled task can run each time in a new worktree", t.get("worktree") is True, r)
check("a new worktree each run needs a folder",
      call("/api/assistant", {"action": "task_add", "prompt": "x", "when": "daily", "worktree": True}).get("_http") == 400)
r = call("/api/assistant", {"action": "task_run", "id": t.get("id")})
v = call(f"/api/agent/{r.get('thread')}?since=0")
wd = v.get("workdir") or ""
check("the run works in its own worktree, not the folder",
      re.search(r"[\\/]\.meshpool[\\/]worktrees[\\/]run-" + re.escape(t.get("id", "?")) + r"-\d{8}-\d{6}$", wd), wd)
check("the run says where it works", any(e.get("type") == "status" and "new worktree" in (e.get("text") or "")
                                         for e in v.get("events", [])), v.get("events", [])[:5])
for _ in range(120):
    if call(f"/api/agent/{r.get('thread')}?since=1000000").get("state") == "idle":
        break
    time.sleep(0.5)
check("the folder itself is untouched", subprocess.run(["git", "status", "--porcelain"], cwd=G, capture_output=True,
                                                         text=True).stdout.strip() == "")
N = os.path.join(os.path.dirname(os.path.abspath(P)), os.path.basename(P.rstrip("/")) + "-sched-plain")
os.makedirs(N, exist_ok=True)
r = call("/api/assistant", {"action": "task_add", "name": "Plain", "prompt": "Say hi", "when": "daily", "folder": N,
                            "worktree": True})
t2 = next((x for x in r.get("tasks", []) if x.get("name") == "Plain"), {})
r = call("/api/assistant", {"action": "task_run", "id": t2.get("id")})
v = call(f"/api/agent/{r.get('thread')}?since=0")
check("a run that cannot get a worktree does not start, and says why",
      any(e.get("type") == "error" and "did not start" in (e.get("text") or "") for e in v.get("events", [])) and
      not any(e.get("type") == "user" for e in v.get("events", [])), v.get("events", [])[:5])
for x in (t, t2):
    call("/api/assistant", {"action": "task_delete", "id": x.get("id")})

# ---- handoff: a chat moves to a new worktree, with its uncommitted changes, and back
with open(os.path.join(G, "app.py"), "w") as f:
    f.write("x = 2  # work in progress\n")
v = call("/api/agent", {"workdir": G, "kind": "cowork", "mode": "auto"})
hid = v.get("id")
def idle(sid):
    for _ in range(240):
        if call(f"/api/agent/{sid}?since=1000000").get("state") == "idle":
            return
        time.sleep(0.5)


turn(hid, "show python code")
idle(hid)
r = call(f"/api/agent/{hid}/checkout", {"to": "worktree", "carry": True})
wd = r.get("workdir") or ""
check("a chat moves to a new worktree", re.search(r"[\\/]\.meshpool[\\/]worktrees[\\/]task-\d+$", wd), r)
check("its uncommitted changes go with it, and the checkout is left clean",
      wd and open(os.path.join(wd, "app.py")).read() == "x = 2  # work in progress\n" and
      open(os.path.join(G, "app.py")).read() == "x = 1\n", r.get("moved"))
users = [e for e in call(f"/api/agent/{hid}?since=0").get("events", []) if e.get("type") == "user"]
check("the conversation stays one chat", len(users) == 1 and "show python code" in users[0].get("text", ""), users)
turn(hid, "show python code")
idle(hid)
check("the next turn runs in the worktree", call(f"/api/agent/{hid}?since=1000000").get("workdir") == wd)
check("moving to a worktree twice is refused", call(f"/api/agent/{hid}/checkout", {"to": "worktree"}).get("_http") == 400)
r = call(f"/api/agent/{hid}/checkout", {"to": "main", "carry": True})
check("and back to the main checkout, changes included",
      os.path.realpath(r.get("workdir") or "") == os.path.realpath(G) and
      open(os.path.join(G, "app.py")).read() == "x = 2  # work in progress\n", r)

r = call(f"/api/agent/{hid}/revert", {"turn": 1, "what": "both"})
check("undo of a reply from before the move is refused, not applied to the wrong reply",
      r.get("_http") == 400 and "before this chat moved" in r.get("error", ""), r)

# ---- branch: a new chat from an earlier reply; the original stays as it was
v = call("/api/agent", {"workdir": G, "kind": "cowork", "mode": "auto"})
fid = v.get("id")
turn(fid, "show python code")
idle(fid)
turn(fid, "think out loud")
idle(fid)
b = call(f"/api/agent/{fid}/fork", {"turn": 1})
bu = [e.get("text") for e in b.get("events", []) if e.get("type") == "user"]
check("a branch from the first reply keeps only that part", bu == ["show python code"], bu)
check("the branch says where it came from", any(e.get("type") == "status" and "Branched from" in (e.get("text") or "")
                                                for e in b.get("events", [])) and b.get("workdir") == v.get("workdir"), b.get("title"))
ou = [e.get("text") for e in call(f"/api/agent/{fid}?since=0").get("events", []) if e.get("type") == "user"]
check("the original chat is unchanged", ou == ["show python code", "think out loud"], ou)
turn(b.get("id"), "show python code")
bu2 = [e.get("text") for e in call(f"/api/agent/{b.get('id')}?since=0").get("events", []) if e.get("type") == "user"]
check("the branch goes on by itself", bu2 == ["show python code", "show python code"] and
      len([e for e in call(f"/api/agent/{fid}?since=0").get("events", []) if e.get("type") == "user"]) == 2, bu2)
w = call(f"/api/agent/{fid}/fork", {"turn": 0})
check("a branch of the whole chat has all of it", [e.get("text") for e in w.get("events", []) if e.get("type") == "user"] == ou)
check("a turn that is not there is refused", call(f"/api/agent/{fid}/fork", {"turn": 9}).get("_http") == 400)

ok = sum(1 for _, x in RESULTS if x)
print(f"\n{ok}/{len(RESULTS)} passed")
sys.exit(0 if ok == len(RESULTS) else 1)
