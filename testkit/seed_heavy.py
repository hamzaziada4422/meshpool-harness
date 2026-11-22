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
for ch in corp.chats:
    F.write_chat(runs, ch)
extra = F.weekly(5, "Make the weekly sales summary deck from the Q3 sales workbook", prefix="wk")
for ch in extra:
    F.write_chat(runs, ch)
t = A.add_task("Price watch", "Check supplier prices and tell me what moved", "daily", only_on_change=True)
rows = A.tasks()
sid = corp.chats[0]["id"]
for r in rows:
    r["last_thread"], r["last_run"] = sid, time.time() - 3600
A._save_tasks(rows)
print("seeded", len(corp.chats) + len(extra), "chats")
