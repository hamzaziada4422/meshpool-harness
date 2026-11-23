"""Tiny stdio MCP server for lifecycle tests. Tools: slow (waits for notifications/cancelled), die (exits at once),
echo, spawn (starts a long sleeper child and reports its pid), pid."""
import json
import os
import subprocess
import sys
import threading
import time

OUT = os.environ.get("WS16_DIR", ".")
lock = threading.Lock()
cancelled = set()


def send(o):
    with lock:
        sys.stdout.write(json.dumps(o) + "\n")
        sys.stdout.flush()


def log(line):
    with open(os.path.join(OUT, "server.log"), "a") as f:
        f.write(line + "\n")


def handle(m):
    rid, meth = m.get("id"), m.get("method")
    if meth == "notifications/cancelled":
        cancelled.add(m["params"]["requestId"])
        log("cancelled %s" % m["params"]["requestId"])
        return
    if rid is None:
        return
    if meth == "initialize":
        if os.environ.get("WS16_INIT_DELAY"):
            time.sleep(float(os.environ["WS16_INIT_DELAY"]))
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                                      "serverInfo": {"name": "ws16", "version": "1"}}})
    elif meth == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": n, "inputSchema": {"type": "object"}} for n in ("slow", "die", "echo", "spawn", "pid")]}})
    elif meth == "tools/call":
        n = m["params"]["name"]
        if n == "die":
            os._exit(3)
        if n == "slow":
            for _ in range(100):
                if rid in cancelled:
                    return
                time.sleep(0.05)
            log("ran slow to the end")
        if n == "spawn":
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
            txt = str(p.pid)
        elif n == "pid":
            txt = str(os.getpid())
        else:
            txt = "ok"
        send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": txt}]}})


for line in sys.stdin:
    try:
        threading.Thread(target=handle, args=(json.loads(line),), daemon=True).start()
    except ValueError:
        pass
