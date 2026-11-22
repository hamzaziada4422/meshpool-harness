"""A heavy-use Meshpool home for screenshots and the noise journey: 250+ chats, repeats worth a card, a scheduled
task that finished while away, and memory. Usage: seed_heavy.py HOME"""
import os
import sys
import time
from pathlib import Path

home = sys.argv[1]
os.environ["MESHPOOL_HOME"] = home
ROOT = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import insights_fixtures as F  # noqa: E402
from meshpool_harness import assistant as A  # noqa: E402
from meshpool_harness import config as C  # noqa: E402

corp = F.heavy_use()
runs = C.runs_dir()
delta = time.time() - F.NOW - 3600                    # fixtures live at a fixed date: move them to just before now


def shift(ch):
    for k in ("ts", "created"):
        if k in ch:
            ch[k] += delta
    return ch


import json  # noqa: E402


def write(ch):
    F.write_chat(runs, shift(ch))
    f = runs / "sessions" / ch["id"] / "session.json"        # real sessions start with the system message
    d = json.loads(f.read_text(encoding="utf-8"))
    d["messages"].insert(0, {"role": "system", "content": "system"})
    f.write_text(json.dumps(d), encoding="utf-8")


for ch in corp.chats:
    write(ch)
extra = F.weekly(5, "Make the weekly sales summary deck from the Q3 sales workbook", prefix="wk")
for ch in extra:
    write(ch)
t = A.add_task("Price watch", "Check supplier prices and tell me what moved", "daily", only_on_change=True)
rows = A.tasks()
sid = corp.chats[0]["id"]
for r in rows:
    r["last_thread"], r["last_run"] = sid, time.time() - 3600
A._save_tasks(rows)
print("seeded", len(corp.chats) + len(extra), "chats")
