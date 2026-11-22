"""Builders for insights tests: single chats, whole corpora, chats on disk, and a heavy-use month."""
from __future__ import annotations

import json
import random
from pathlib import Path

from meshpool_harness import insights as I

DAY = 86400
NOW = 1795600000.0                                   # a Sunday afternoon in Nov 2026
WEEKLY = "Summarize the weekly sales report for the GCC region and list the three biggest changes"


def chat(cid, ts, text, project="", steps=1, inputs=(), outputs=(), scheduled="", incognito=False, spec="",
         harness="meshpool-work", kind="cowork", title="", texts=None, tools=None) -> dict:
    return {"id": cid, "ts": float(ts), "created": float(ts), "title": title or text[:40],
            "texts": list(texts) if texts is not None else [text], "tools": list(tools) if tools is not None else ["read_file"] * steps,
            "steps": steps, "outputs": list(outputs), "inputs": list(inputs), "project_id": project,
            "incognito": incognito, "scheduled": scheduled, "harness": harness, "kind": kind, "spec": spec,
            "workdir": ""}


def weekly(n=4, text=WEEKLY, start=NOW - 40 * DAY, gap=7 * DAY, prefix="w"):
    return [chat(f"{prefix}{i}", start + i * gap, text) for i in range(n)]


def corp(chats=(), **kw) -> I.Corpus:
    kw.setdefault("now", NOW)
    kw.setdefault("connector", lambda svc: {"name": svc.title(), "connected": False, "id": svc})
    kw.setdefault("header", lambda p: None)
    return I.Corpus(list(chats), **kw)


def turn(i, spec, harness, failed=False, tin=1000, tout=500, chat_id="c", lane="cowork"):
    return {"ts": NOW - 1000 + i, "chat": chat_id + str(i), "spec": spec, "harness": harness, "lane": lane,
            "tokens_in": tin, "tokens_out": tout, "tokens_cached": 0, "calls": 3, "error": failed, "stopped": False}


def write_chat(runs: Path, rec: dict) -> None:
    """A chat on disk the way the agent saves it: session.json (messages) and meta.json."""
    d = runs / "sessions" / rec["id"]
    d.mkdir(parents=True, exist_ok=True)
    msgs = []
    for t in rec["texts"]:
        msgs.append({"role": "user", "content": t + "\n\n​context that is hidden"})
        msgs.append({"role": "assistant", "content": "done", "tool_calls": [
            {"id": f"t{i}", "function": {"name": n, "arguments": json.dumps({"path": rec["inputs"][0]} if rec["inputs"] and n == "read_file" else {})}}
            for i, n in enumerate(rec["tools"])] if t is rec["texts"][-1] else []})
    for o in rec["outputs"]:
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "o", "function": {"name": "write_file", "arguments": json.dumps({"path": o})}}]})
    (d / "session.json").write_text(json.dumps({"id": rec["id"], "title": rec["title"], "created": rec["created"],
                                                "updated": rec["ts"], "kind": rec["kind"], "harness": rec["harness"],
                                                "workdir": rec["workdir"], "messages": msgs}), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"project_id": rec["project_id"], "incognito": rec["incognito"],
                                             "scheduled": rec["scheduled"], "route": rec["spec"]}), encoding="utf-8")


_TOPICS = ["pricing", "churn", "hiring", "budget", "roadmap", "invoice", "contract", "survey", "forecast", "logistics",
           "payroll", "brand", "onboarding", "audit", "inventory", "tender", "campaign", "retention", "compliance",
           "benchmark", "vendor", "pipeline", "headcount", "tariff", "warehouse", "licensing", "refund", "outage"]
_VERBS = ["Analyze", "Draft", "Review", "Compare", "Outline", "Explain", "Rewrite", "Plan", "Check", "Estimate"]


def heavy_use(seed=7) -> I.Corpus:
    """A month of heavy use: 260 turns over 30 days, three projects, a scheduled task, edited artifacts, an outcome
    log, and a handful of real patterns hidden in the noise."""
    rnd = random.Random(seed)
    chats, k = [], 0
    for d in range(30):                                       # varied one-off chats, 7 or 8 a day
        for j in range(8):
            k += 1
            a, b, c2 = rnd.sample(_TOPICS, 3)
            v = rnd.choice(_VERBS)
            chats.append(chat(f"x{k}", NOW - (30 - d) * DAY + j * 3600 * 2,
                              f"{v} the {a} numbers against {b} and write up what changed for {c2} {k}",
                              project=rnd.choice(["", "", "p_a", "p_b", "p_c"]), steps=rnd.randint(1, 6),
                              inputs=[f"file_{k}.xlsx"] if rnd.random() < 0.3 else [], spec="docs.analyze",
                              harness=rnd.choice(["meshpool-work", "meshpool-code"])))
    chats += weekly(5, prefix="sched", text="Draft the Monday ops digest from the tracker export and flag blockers",
                    start=NOW - 36 * DAY)                       # a real weekly habit
    chats += [chat(f"pj{i}", NOW - d * DAY, "Use this brief. " + "Our client is a Gulf logistics group with fifty depots, "
                   "three languages and a strict style guide that bans em dashes and wants sentence case titles. " * 4 +
                   f" Now do job {i}.") for i, d in enumerate((20, 8, 6))]       # the same background pasted three times
    chats += [chat(f"jr{i}", NOW - (9 - i) * DAY, "Fix the status in https://acme.atlassian.net/browse/OPS-1" + str(i))
              for i in range(2)]                                 # Jira pasted twice
    tasks = [{"id": "t1", "name": "Weekly ops", "prompt": "Draft the Monday ops digest from the tracker export and flag blockers"}]
    outcomes = []
    for i in range(40):
        outcomes.append(turn(i, "docs.analyze", "meshpool-work", failed=(i % 5 == 0)))
        outcomes.append(turn(100 + i, "docs.analyze", "meshpool-code", failed=(i % 5 == 0), tin=900, tout=450))
    ff = {"p_a": [{"name": "Q3_sales.xlsx", "path": "/x/Q3_sales.xlsx", "mtime": NOW - 5 * DAY},
                  {"name": "Q4_sales.xlsx", "path": "/x/Q4_sales.xlsx", "mtime": NOW - 1 * DAY}]}
    chats.append(chat("rr", NOW - 3 * DAY, "Summarize Q3 sales by region", project="p_a", inputs=["Q3_sales.xlsx"]))
    teach = [{"file": "board.pptx", "chat_id": "x3", "kind": "deck", "count": 9, "total": 9,
              "rules": ["Titles in sentence case", "Font set to Arial"],
              "label": "You changed all 9 slide titles to sentence case and the font to Arial. Make these your defaults?"}]
    return corp(chats, tasks=tasks, outcomes=outcomes, folder_files=ff, teach=teach)
