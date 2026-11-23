"""Shared pieces for the front-end CPU measurements: a fake 3k-event chat and a list of 5 chats served by page.route."""
import json
import re

EVENTS_N = 3000


def make_events(n=EVENTS_N):
    evs = []
    i = 0
    turn = 0
    while len(evs) < n:
        turn += 1
        evs.append({"i": len(evs), "type": "user", "text": f"Question number {turn}: please look at the thing."})
        evs.append({"i": len(evs), "type": "tool_start", "id": f"t{turn}", "name": "read_file", "input": {"path": "a.txt"}})
        evs.append({"i": len(evs), "type": "tool_end", "id": f"t{turn}", "ok": True, "output": "hello"})
        evs.append({"i": len(evs), "type": "assistant", "text": f"Answer {turn}. Here is **some** text.\n\n- one\n- two\n\n`code` and a [link](https://example.com)."})
        evs.append({"i": len(evs), "type": "turn_end"})
    return evs[:n]


def install(page, chats=5, events=EVENTS_N):
    evs = make_events(events)
    counts = {"req": 0, "by": {}}
    sessions = [{"id": f"c{k}", "title": f"Chat {k}", "workdir": "", "updated": 1000 + k, "state": "idle", "kind": "cowork", "harness": "meshpool-auto"} for k in range(chats)]

    def handler(route):
        req = route.request
        url = req.url
        m = re.search(r"/api/agent/(c\d+)(?:\?since=(\d+))?$", url)
        counts["req"] += 1
        key = re.sub(r"\?.*", "", url.split("127.0.0.1:")[1].split("/", 1)[1]) if "127.0.0.1:" in url else url
        counts["by"][key] = counts["by"].get(key, 0) + 1
        if m:
            since = int(m.group(2) or 0)
            view = {"id": m.group(1), "workdir": "", "state": "idle", "mode": "ask", "title": "Chat", "pinned": False, "project_id": "", "scratch": True,
                    "usage": {}, "kind": "cowork", "harness": {"id": "meshpool-auto", "name": "Auto"}, "model": "m", "plan": False, "goal": "",
                    "style": "", "incognito": False, "epoch": 1, "turns": [], "checkpoints_note": "", "events": evs[since:], "total": len(evs), "mcp": None}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(view))
        if re.search(r"/api/agent(\?.*)?$", url) and req.method == "GET":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"sessions": sessions}))
        return route.continue_()

    page.route("**/api/agent**", handler)
    return counts


COUNTERS = """
(() => {
  window.__c = {raf: 0, timeouts: 0, intervals: 0};
  const raf = window.requestAnimationFrame.bind(window); window.requestAnimationFrame = f => raf(t => { window.__c.raf++; return f(t); });
  const st = window.setTimeout.bind(window); window.setTimeout = (f, d, ...a) => { window.__c.timeouts++; return st(f, d, ...a); };
})();
"""
