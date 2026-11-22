"""Fill a Meshpool home with realistic memory for screenshots and journeys. Usage: seed_memory.py HOME [N_EXTRA]"""
import os, sys, time
os.environ["MESHPOOL_HOME"] = sys.argv[1]
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from meshpool_harness import memstore as MS, project_store as PS
pid = PS.create("Q3 board deck", "Quarterly board pack", "One message per slide. Numbers in USD.")["id"]
rows = [
 ("Works in strategy and finance for GCC clients", "stated", "about", "everywhere", {"type": "chat"}),
 ("Based in Dubai, works in Gulf time", "stated", "about", "everywhere", {"type": "manual"}),
 ("Prefers Segoe UI and no label over slide titles", "learned", "preferences", "everywhere", {"type": "correction", "quote": "never put an eyebrow over the title"}),
 ("Wants one message per slide", "stated", "preferences", "everywhere", {"type": "chat"}),
 ("Prefers sentence case in titles", "learned", "preferences", "everywhere", {"type": "edit", "file": "Q3_board.pptx"}),
 ("Manager is Layla Haddad", "stated", "people", "everywhere", {"type": "chat"}),
 ("Uses Jira and Slack for team work", "stated", "tools", "everywhere", {"type": "chat"}),
 ("Sends a board update every Monday morning", "stated", "routines", "everywhere", {"type": "chat"}),
 ("Board wants all numbers in USD", "stated", "projects", f"project:{pid}", {"type": "chat"}),
 ("Always use Arial for slides", "stated", "preferences", "everywhere", {"type": "manual"}),
]
for t, k, c, s, src in rows:
    f, _ = MS.add(t, kind=k, category=c, scope=s, source=src); time.sleep(0.01)
MS.notice("Meets the CFO every Friday", evidence=[{"text": "4 chats"}] * 4, category="routines")
MS.notice("Uses Linear for engineering tickets", evidence=[{"text": "2 chats"}] * 2, category="tools")
for i in range(int(sys.argv[2]) if len(sys.argv) > 2 else 0):
    MS.add(f"Extra fact number {i} about topic {i % 23}", category=MS.CATEGORIES[i % 6])
print("seeded")
