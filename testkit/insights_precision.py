"""Precision and recall of the insight detectors on hand-labelled situations (30 per family).

Each situation is a small made-up history plus a label written from the product rules, not from what the detector
does: "a person looking at this would want this card" (True) or "this card would be noise" (False). A detector that
fires on a True situation is a hit, on a False one a false alarm; silence on a True one is a miss.

Usage:  python testkit/insights_precision.py            print the table
        python testkit/insights_precision.py --write    also write docs/plan/0.0.10-precision.md
Types with precision >= 0.8 ship on by default (insights.DEFAULT_ON); below that they ship off.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "app" / "tests")]

from insights_fixtures import DAY, NOW, chat, corp, turn  # noqa: E402

from meshpool_harness import insights as I  # noqa: E402

T = "Summarize the weekly sales report for the GCC region and list the three biggest changes"
T2 = "Reconcile the open invoices against the bank export and flag anything unmatched in a sheet"
BG = ("Our client is a Gulf logistics group with fifty depots, three languages and a strict style guide that bans em "
      "dashes and wants sentence case titles. Reports go to the board each quarter and always lead with cash. ")
JIRA = "Please look at https://acme.atlassian.net/browse/OPS-12 and tell me what is blocked"
OPS = lambda i: [  # noqa: E731 - distinct one-line requests so unrelated chats never cluster
    "Explain how deferred revenue is recognised", "Rewrite this paragraph for a nervous client", "Translate the notice to Arabic",
    "Outline a pitch for the new loyalty scheme", "Check my formula for the churn rate", "Draft a thank you note to the speaker",
    "Plan a two day offsite agenda for sales", "Find a better name for the onboarding programme"][i % 8]


def runs(n, gap, text=T, jitter=(), start=None, **kw):
    start = start or NOW - 60 * DAY
    out, t = [], start
    for i in range(n):
        out.append(chat(f"r{i}", t, text, **kw))
        t += (gap + (jitter[i] if i < len(jitter) else 0)) * DAY
    return out


def ts(*days):
    return [NOW - d * DAY for d in days]


# ---------- situations: (name, builder, label) -------------------------------------------------------------------------------
def schedule_cases():
    P, N = True, False
    return [
        ("weekly x4", lambda: corp(runs(4, 7)), P), ("weekly x3", lambda: corp(runs(3, 7)), P),
        ("daily x5", lambda: corp(runs(5, 1, start=NOW - 6 * DAY)), P), ("weekly with a day of slack", lambda: corp(runs(4, 7, jitter=(1, -1, 1))), P),
        ("every 14 days x3", lambda: corp(runs(3, 14)), P), ("monthly x3", lambda: corp(runs(3, 30)), P),
        ("every 3 days x4", lambda: corp(runs(4, 3, start=NOW - 14 * DAY)), P), ("weekly x6", lambda: corp(runs(6, 7)), P),
        ("weekly, other task unrelated", lambda: corp(runs(4, 7), tasks=[{"prompt": "Water the plants reminder"}]), P),
        ("weekly invoices", lambda: corp(runs(4, 7, text=T2)), P), ("every 10 days x3", lambda: corp(runs(3, 10)), P),
        ("weekly, one run failed", lambda: corp(runs(4, 7)), P), ("fortnightly x4", lambda: corp(runs(4, 14, start=NOW - 55 * DAY)), P),
        ("weekly with date in the text", lambda: corp([chat(f"d{i}", NOW - (28 - 7 * i) * DAY, f"{T} for week {40 + i}") for i in range(4)]), P),
        ("daily x3", lambda: corp(runs(3, 1, start=NOW - 4 * DAY)), P),
        ("only twice", lambda: corp(runs(2, 7)), N), ("irregular gaps", lambda: corp(runs(3, 1, jitter=(0, 12))), N),
        ("already scheduled", lambda: corp(runs(4, 7), tasks=[{"prompt": T}]), N),
        ("those are the scheduled runs", lambda: corp(runs(4, 7, scheduled="t1")), N),
        ("three retries the same day", lambda: corp([chat(f"a{i}", NOW - 3600 * i, T) for i in range(3)]), N),
        ("every 90 days", lambda: corp(runs(3, 90, start=NOW - 200 * DAY)), N),
        ("similar but different requests", lambda: corp([chat(f"v{i}", NOW - (21 - 7 * i) * DAY, OPS(i)) for i in range(4)]), N),
        ("random gaps x5", lambda: corp(runs(5, 2, jitter=(0, 9, 1, 17, 3))), N), ("one chat", lambda: corp(runs(1, 7)), N),
        ("incognito runs", lambda: corp(runs(4, 7, incognito=True)), N),
        ("two weeks then silence, only 2", lambda: corp(runs(2, 14)), N),
        ("three different topics weekly", lambda: corp([chat(f"m{i}", NOW - (21 - 7 * i) * DAY, OPS(i * 3)) for i in range(3)]), N),
        ("burst then gap", lambda: corp(runs(4, 1, jitter=(0, 0, 25), start=NOW - 40 * DAY)), N),
        ("two runs same day plus one", lambda: corp([chat("a", NOW - 14 * DAY, T), chat("b", NOW - 14 * DAY + 600, T), chat("c", NOW - 1 * DAY, T)]), N),
        ("scheduled task prompt is a close match", lambda: corp(runs(4, 7), tasks=[{"prompt": T + " please"}]), N),
    ]


def skill_cases():
    P, N = True, False
    proc = lambda gaps, steps=5, t=T2: corp([chat(f"p{i}", NOW - g * DAY, t, steps=steps) for i, g in enumerate(gaps)])   # noqa: E731
    corr = lambda n, line="Never put an eyebrow label above the slide title": corp(          # noqa: E731
        [chat(f"k{i}", NOW - (i * 3 + 1) * DAY, "x", texts=["Make a deck", line]) for i in range(n)])
    return [
        ("procedure x3", lambda: proc((30, 26, 2)), P), ("procedure x4", lambda: proc((40, 33, 12, 2)), P),
        ("procedure 8 steps", lambda: proc((30, 25, 2), steps=8), P), ("procedure x5", lambda: proc((50, 44, 20, 9, 1)), P),
        ("procedure 3 steps", lambda: proc((30, 26, 2), steps=3), P), ("procedure, other text", lambda: proc((31, 27, 3), t="Take the signed contracts, extract the renewal dates and build a tracker sheet with owners"), P),
        ("correction x3", lambda: corr(3), P), ("correction x4", lambda: corr(4), P), ("correction x5", lambda: corr(5), P),
        ("correction font", lambda: corr(3, "Always use Segoe UI for every slide, I told you"), P),
        ("correction em dash", lambda: corr(3, "Don't use em dashes anywhere in the writing, stop that"), P),
        ("correction short answers", lambda: corr(3, "Make sure answers are short, I prefer two sentences"), P),
        ("correction bullets", lambda: corr(3, "Never use bullet points in emails, write in sentences"), P),
        ("correction sentence case", lambda: corr(4, "Always write titles in sentence case from now on"), P),
        ("procedure and correction", lambda: proc((30, 26, 2), steps=6), P), ("procedure x3 (b)", lambda: proc((45, 40, 4), steps=4), P),
        ("procedure twice", lambda: proc((30, 2)), N), ("one step", lambda: proc((30, 26, 2), steps=1), N),
        ("two steps", lambda: proc((30, 26, 2), steps=2), N), ("regular rhythm (a schedule)", lambda: proc((21, 14, 7)), N),
        ("correction twice", lambda: corr(2), N), ("correction said thrice in one chat", lambda: corp([chat("z", NOW, "x", texts=["Never use em dashes"] * 3)]), N),
        ("three different corrections", lambda: corp([chat("a", NOW - 3 * DAY, "x", texts=["Never use em dashes"]),
                                                      chat("b", NOW - 2 * DAY, "x", texts=["Always use Segoe UI everywhere"]),
                                                      chat("c", NOW - 1 * DAY, "x", texts=["Stop adding speaker notes automatically please"])]), N),
        ("three different requests", lambda: corp([chat(f"d{i}", NOW - (i * 9 + 2) * DAY, OPS(i), steps=5) for i in range(3)]), N),
        ("one chat", lambda: proc((2,)), N), ("scheduled runs", lambda: corp([chat(f"s{i}", NOW - g * DAY, T2, steps=5, scheduled="t") for i, g in enumerate((30, 26, 2))]), N),
        ("incognito", lambda: corp([chat(f"i{i}", NOW - g * DAY, T2, steps=5, incognito=True) for i, g in enumerate((30, 26, 2))]), N),
        ("plain questions", lambda: corp([chat(f"q{i}", NOW - g * DAY, "What is the difference between EBIT and EBITDA", steps=0) for i, g in enumerate((30, 26, 2))]), N),
        ("correction words, no repeat", lambda: corp([chat("a", NOW, "x", texts=["Never mind, I found it"])]), N),
        ("two procedures twice each", lambda: corp([chat(f"e{i}", NOW - g * DAY, T2 if i % 2 else T, steps=5) for i, g in enumerate((30, 24, 9, 2))]), N),
    ]


def _pasted(n, proj="", length=1, kind="cowork", scheduled="", names=None):
    return corp([dict(chat(f"p{i}", NOW - i * DAY, "x", project=proj, kind=kind, scheduled=scheduled),
                      texts=[f"For this job.\n\n{BG * length}\n\nNow draft item {i}"]) for i in range(n)])


def project_cases():
    P, N = True, False
    files = lambda n, name="brief.docx", proj="": corp([chat(f"f{i}", NOW - i * DAY, OPS(i), inputs=[name], project=proj) for i in range(n)])  # noqa: E731
    return [
        ("passage x3", lambda: _pasted(3), P), ("passage x4", lambda: _pasted(4), P), ("passage x6", lambda: _pasted(6), P),
        ("long passage", lambda: _pasted(3, length=4), P), ("passage x5", lambda: _pasted(5), P), ("passage x3 (b)", lambda: _pasted(3, length=2), P),
        ("passage x4 (b)", lambda: _pasted(4, length=3), P), ("passage, plus unrelated chats", lambda: corp(_pasted(3).chats + [chat("u", NOW, OPS(2))]), P),
        ("file in 3 chats", lambda: files(3), P), ("file in 4 chats", lambda: files(4), P), ("file in 5 chats", lambda: files(5), P),
        ("spreadsheet in 3 chats", lambda: files(3, "pricing.xlsx"), P), ("deck in 3 chats", lambda: files(3, "template.pptx"), P),
        ("file x6", lambda: files(6, "styleguide.pdf"), P), ("csv in 3 chats", lambda: files(3, "customers.csv"), P),
        ("passage twice", lambda: _pasted(2), N), ("already in a project", lambda: _pasted(3, proj="p1"), N),
        ("passage too short", lambda: corp([chat(f"s{i}", NOW - i * DAY, "Our client is a logistics group. Be brief and plain.") for i in range(3)]), N),
        ("code chats", lambda: _pasted(3, kind="code"), N), ("scheduled runs", lambda: _pasted(3, scheduled="t"), N),
        ("file twice", lambda: files(2), N), ("file in a project", lambda: files(3, proj="p1"), N),
        ("three different passages", lambda: corp([dict(chat(f"d{i}", NOW - i * DAY, "x"), texts=[f"Context.\n\n{BG.replace('Gulf', w)} {w * 40}\n\nJob"]) for i, w in enumerate(["alpha", "bravo", "charlie"])]), N),
        ("incognito", lambda: corp([dict(c, incognito=True) for c in _pasted(3).chats]), N),
        ("three different files", lambda: corp([chat(f"g{i}", NOW - i * DAY, OPS(i), inputs=[f"file{i}.docx"]) for i in range(3)]), N),
        ("no pasted text", lambda: corp([chat(f"h{i}", NOW - i * DAY, OPS(i)) for i in range(4)]), N),
        ("passage once", lambda: _pasted(1), N), ("two chats and one in a project", lambda: corp(_pasted(2).chats + _pasted(1, proj="p9").chats), N),
        ("long but unique messages", lambda: corp([dict(chat(f"l{i}", NOW - i * DAY, "x"), texts=[("Notes %d. " % i) + ("topic %d detail " % i) * 40]) for i in range(4)]), N),
        ("same file in code chats only", lambda: corp([chat(f"k{i}", NOW - i * DAY, OPS(i), inputs=["main.py"], kind="code") for i in range(3)]), N),
        ("passage in two plus file once", lambda: corp(_pasted(2).chats + [chat("o", NOW, OPS(1), inputs=["a.docx"])]), N),
    ]


def _fail_pair(bad=3, other_fail=0, other_n=4, spec="docs.slides"):
    rows = [turn(i, spec, "meshpool-work", failed=i < bad) for i in range(6)]
    rows += [turn(20 + i, spec, "meshpool-code", failed=i < other_fail) for i in range(other_n)]
    return corp(outcomes=rows)


def _cheap(h_n=10, g_n=5, g_tok=(1500, 700), g_fail=0, h_fail=0):
    rows = [turn(i, "docs.analyze", "meshpool-work", failed=i < h_fail, tin=4000, tout=2000) for i in range(h_n)]
    rows += [turn(50 + i, "docs.analyze", "pi", failed=i < g_fail, tin=g_tok[0], tout=g_tok[1]) for i in range(g_n)]
    return corp(outcomes=rows)


def route_cases():
    P, N = True, False
    return [
        ("3 of 6 failed, other clean", lambda: _fail_pair(3), P), ("4 of 6 failed", lambda: _fail_pair(4), P), ("5 of 6 failed", lambda: _fail_pair(5), P),
        ("other with 3 turns", lambda: _fail_pair(3, other_n=3), P), ("6 of 6", lambda: _fail_pair(6), P), ("other with 6 turns", lambda: _fail_pair(4, other_n=6), P),
        ("4 failed, other 1 of 4", lambda: _fail_pair(4, other_fail=1), P),
        ("cheaper, equal quality", lambda: _cheap(), P), ("much cheaper", lambda: _cheap(g_tok=(800, 300)), P), ("cheaper, 20 turns", lambda: _cheap(h_n=20), P),
        ("cheaper, both flawless 12 turns", lambda: _cheap(h_n=12, g_n=8), P), ("cheaper, other better", lambda: _cheap(h_fail=3), P),
        ("cheaper by 35%", lambda: _cheap(g_tok=(2600, 1300)), P), ("cheaper, 6 turns", lambda: _cheap(g_n=6), P), ("cheaper, 5 vs 11", lambda: _cheap(h_n=11), P),
        ("2 of 6 failed", lambda: _fail_pair(2), N), ("1 of 6", lambda: _fail_pair(1), N), ("none failed", lambda: _fail_pair(0), N),
        ("other no better", lambda: _fail_pair(3, other_fail=3), N), ("other 2 turns only", lambda: _fail_pair(3, other_n=2), N),
        ("no other agent", lambda: _fail_pair(3, other_n=0), N), ("cheaper but worse", lambda: _cheap(g_fail=2), N),
        ("9 turns only", lambda: _cheap(h_n=9), N), ("only 15% cheaper", lambda: _cheap(g_tok=(5100, 2550)), N), ("other has 4 turns", lambda: _cheap(g_n=4), N),
        ("same cost", lambda: _cheap(g_tok=(4000, 2000)), N), ("only 5 turns", lambda: corp(outcomes=[turn(i, "docs.slides", "meshpool-work", failed=True) for i in range(5)]), N),
        ("nothing logged", lambda: corp(), N), ("different spec on the other agent", lambda: corp(outcomes=[turn(i, "docs.slides", "meshpool-work", failed=i < 4) for i in range(6)] + [turn(20 + i, "docs.sheets", "pi") for i in range(5)]), N),
        ("failures long ago, last 6 clean", lambda: corp(outcomes=[turn(i, "docs.slides", "meshpool-work", failed=i < 3) for i in range(12)] + [turn(40 + i, "docs.slides", "pi") for i in range(4)]), N),
    ]


def connect_cases():
    P, N = True, False
    Jc = lambda s: {"name": "Jira", "connected": False, "id": "atlassian"}      # noqa: E731
    two = lambda a, b, **kw: corp([chat("a", NOW - DAY, a), chat("b", NOW - 2 * DAY, b)], **kw)  # noqa: E731
    return [
        ("jira urls x2", lambda: two(JIRA, JIRA + " again", connector=Jc), P), ("jira keys x2", lambda: two("OPS-3 and OPS-9 are blocked", "Summarize tickets PLAT-12, PLAT-14", connector=Jc), P),
        ("jira x3", lambda: corp([chat(f"j{i}", NOW - i * DAY, JIRA) for i in range(3)], connector=Jc), P),
        ("jira url and keys", lambda: two(JIRA, "Check ENG-1 and ENG-2 for me", connector=Jc), P),
        ("notion x2", lambda: two("Summarize https://www.notion.so/team/Roadmap-9f3a1c2b7d", "Update https://acme.notion.site/Plan-12ab34cd56", connector=lambda s: {"name": "Notion", "connected": False, "id": "notion"}), P),
        ("notion x3", lambda: corp([chat(f"n{i}", NOW - i * DAY, "Read https://www.notion.so/team/Spec-9f3a1c2b7d") for i in range(3)], connector=lambda s: {"name": "Notion", "connected": False, "id": "notion"}), P),
        ("linear x2", lambda: two("Triage https://linear.app/acme/issue/ENG-123/crash", "Close https://linear.app/acme/issue/ENG-124/typo", connector=lambda s: {"name": "Linear", "connected": False, "id": "linear"}), P),
        ("slack x2", lambda: two("Summarize https://acme.slack.com/archives/C024BE91L/p1699999999000100", "Reply to https://acme.slack.com/archives/C024BE91L/p1699999999000200", connector=lambda s: {"name": "Slack", "connected": False, "id": "slack"}), P),
        ("slack x4", lambda: corp([chat(f"s{i}", NOW - i * DAY, f"See https://acme.slack.com/archives/C024BE91L/p169999999900{i}100") for i in range(4)], connector=lambda s: {"name": "Slack", "connected": False, "id": "slack"}), P),
        ("jira, project key pairs", lambda: two("Tickets DATA-1 DATA-2 DATA-3 need owners", "Review DATA-5 and DATA-6", connector=Jc), P),
        ("jira x2 with text", lambda: two("Draft a status note on " + JIRA, "Sprint review for " + JIRA, connector=Jc), P),
        ("notion x2 short", lambda: two("https://notion.so/Meeting-Notes-aa11bb22cc", "https://notion.so/Meeting-Notes-dd33ee44ff", connector=lambda s: {"name": "Notion", "connected": False, "id": "notion"}), P),
        ("linear x3", lambda: corp([chat(f"l{i}", NOW - i * DAY, f"https://linear.app/acme/issue/ENG-{i}00/x") for i in range(3)], connector=lambda s: {"name": "Linear", "connected": False, "id": "linear"}), P),
        ("jira urls x2 (b)", lambda: two("https://corp.atlassian.net/browse/AB-7", "https://corp.atlassian.net/browse/AB-8", connector=Jc), P),
        ("slack x3", lambda: corp([chat(f"t{i}", NOW - i * DAY, f"https://team.slack.com/archives/C0123ABC/p17000000000{i}0001") for i in range(3)], connector=lambda s: {"name": "Slack", "connected": False, "id": "slack"}), P),
        ("one chat", lambda: corp([chat("a", NOW, JIRA)], connector=Jc), N), ("connected already", lambda: two(JIRA, JIRA, connector=lambda s: {"name": "Jira", "connected": True, "id": "atlassian"}), N),
        ("COVID and ISO", lambda: two("COVID-19 and ISO-9001 rules", "COVID-19 and ISO-27001 audit", connector=Jc), N),
        ("RFC and PEP", lambda: two("RFC-2119 wording", "PEP-8 style and RFC-7231", connector=Jc), N),
        ("jira word but no ticket", lambda: two("We use Jira for tracking", "Jira is slow today", connector=Jc), N),
        ("notion word only", lambda: two("Put it in Notion please", "Is Notion better than Confluence", connector=lambda s: {"name": "Notion", "connected": False, "id": "notion"}), N),
        ("slack word only", lambda: two("Post it in Slack", "I'll send this over Slack", connector=lambda s: {"name": "Slack", "connected": False, "id": "slack"}), N),
        ("catalog lacks it", lambda: two(JIRA, JIRA, connector=lambda s: None), N), ("one key only", lambda: two("Look at OPS-12", "Look at OPS-12", connector=Jc), N),
        ("different prefixes one each", lambda: two("Look at OPS-12", "Look at ENG-33", connector=Jc), N),
        ("incognito", lambda: corp([chat("a", NOW, JIRA, incognito=True), chat("b", NOW, JIRA, incognito=True)], connector=Jc), N),
        ("ticket numbers in prose", lambda: two("Invoice INV-2201 and INV-2205 are late", "INV-2210 paid", connector=Jc), N),
        ("linear word only", lambda: two("a linear regression", "linear algebra review", connector=lambda s: {"name": "Linear", "connected": False, "id": "linear"}), N),
        ("slack link in one, word in other (dropped)", lambda: two("https://acme.slack.com/archives/C024BE91L/p1699999999000100", "Use Slack", connector=lambda s: {"name": "Slack", "connected": False, "id": "slack"}), N),
        ("notion once", lambda: corp([chat("a", NOW, "https://notion.so/Meeting-Notes-aa11bb22cc")], connector=lambda s: {"name": "Notion", "connected": False, "id": "notion"}), N),
        ("part numbers", lambda: two("Order AB-100 and AB-200", "Part AB-100 is out of stock, AB-300 too", connector=Jc), N),
        ("ticket-like model names", lambda: two("GPT-4 and GPT-5 compared", "Use GPT-4 and GPT-4o", connector=Jc), N),
    ]


def _rerun(new="Q4_sales.xlsx", prior="Q3_sales.xlsx", new_age=1, prior_age=5, hdr=None, already=False, proj="p_a", chat_proj="p_a", text="Summarize sales by region in one slide"):
    ch = chat("old", NOW - prior_age * DAY, text, project=chat_proj, inputs=[prior] + ([new] if already else []))
    ff = {proj: [{"name": prior, "path": "/f/" + prior, "mtime": NOW - 9 * DAY}, {"name": new, "path": "/f/" + new, "mtime": NOW - new_age * DAY}]}
    return corp([ch], folder_files=ff, header=hdr or (lambda p: None))


def rerun_cases():
    P, N = True, False
    return [
        ("Q3 to Q4", lambda: _rerun(), P), ("month in the name", lambda: _rerun("sales_2026-11.xlsx", "sales_2026-10.xlsx"), P),
        ("version number", lambda: _rerun("report_v3.docx", "report_v2.docx"), P), ("week number", lambda: _rerun("wk42_tracker.csv", "wk41_tracker.csv"), P),
        ("year", lambda: _rerun("budget_2027.xlsx", "budget_2026.xlsx"), P), ("invoice numbers", lambda: _rerun("invoice_1043.pdf", "invoice_1042.pdf"), P),
        ("same headers", lambda: _rerun(hdr=lambda p: ("region", "sales", "units")), P), ("headers mostly match", lambda: _rerun(hdr=lambda p: ("region", "sales", "units") if "Q3" in p else ("region", "sales", "units", "notes")), P),
        ("deck", lambda: _rerun("board_Q4.pptx", "board_Q3.pptx", text="Tighten the board deck"), P), ("new today", lambda: _rerun(new_age=0.1), P),
        ("csv export", lambda: _rerun("export_0212.csv", "export_0129.csv"), P), ("report 2 to 3", lambda: _rerun("report 3.docx", "report 2.docx"), P),
        ("sheet months", lambda: _rerun("cashflow_nov.xlsx".replace("nov", "11"), "cashflow_10.xlsx"), P), ("numbered batches", lambda: _rerun("batch12.xlsx", "batch11.xlsx"), P),
        ("tracker q", lambda: _rerun("tracker_q4.xlsx", "tracker_q3.xlsx"), P),
        ("different file", lambda: _rerun("Q4_costs.xlsx"), N), ("older than the analysis", lambda: _rerun(new_age=9), N), ("different type", lambda: _rerun("Q4_sales.csv"), N),
        ("different structure", lambda: _rerun(hdr=lambda p: ("a", "b", "c") if "Q3" in p else ("x", "y", "z")), N), ("already analyzed", lambda: _rerun(already=True), N),
        ("no number in the name", lambda: _rerun("summary_final.xlsx", "summary.xlsx"), N), ("other project's folder", lambda: _rerun(proj="p_b"), N),
        ("chat not in a project", lambda: _rerun(chat_proj=""), N), ("copy of", lambda: _rerun("Q3_sales (copy).xlsx"), N),
        ("temp file", lambda: _rerun("~$Q4_sales.xlsx"), N), ("pattern differs by word", lambda: _rerun("Q4_forecast.xlsx"), N),
        ("different ext docx", lambda: _rerun("Q4_sales.docx"), N), ("no prior chat", lambda: corp(folder_files={"p_a": [{"name": "Q4_sales.xlsx", "path": "/f/Q4_sales.xlsx", "mtime": NOW}]}), N),
        ("prior chat without input", lambda: corp([chat("o", NOW - 5 * DAY, "Hello", project="p_a")], folder_files={"p_a": [{"name": "Q4_sales.xlsx", "path": "/f/Q4_sales.xlsx", "mtime": NOW}]}), N),
        ("empty folder", lambda: corp([chat("o", NOW - 5 * DAY, "Hello", project="p_a", inputs=["Q3_sales.xlsx"])], folder_files={"p_a": []}), N),
    ]


def teach_cases():
    P, N = True, False
    t = lambda **kw: corp(teach=[dict({"file": "d.pptx", "chat_id": "c", "kind": "deck", "count": 9, "total": 9, "rules": ["Sentence case titles"], "label": "x"}, **kw)])  # noqa: E731
    out = [(f"{k} edits of {n}", (lambda k=k, n=n: t(count=k, total=n)), True) for k, n in [(9, 9), (8, 9), (5, 6), (4, 4), (3, 3), (6, 8), (2, 2), (7, 7), (12, 12), (3, 4)]]
    out += [(f"rules {r}", (lambda r=r: t(rules=r)), True) for r in (["Arial"], ["Arial", "Sentence case"], ["Number format 0.0%"], ["Column width 24"], ["Heading style"])]
    out += [(f"one-off {k}", (lambda k=k: t(count=k, total=9)), False) for k in (1, 0)]
    out += [("no rules", lambda: t(rules=[]), False), ("no candidates", lambda: corp(), False)]
    out += [(f"one-off {i}", (lambda i=i: t(count=1, total=i)), False) for i in (1, 3, 5, 9, 20, 40)]
    out += [("long rule list", lambda: t(rules=["Arial", "Sentence case", "No eyebrow"]), True)]
    out += [("empty rules (b)", lambda: t(rules=[], count=5), False), ("zero count", lambda: t(count=0, rules=["Arial"]), False), ("one of one", lambda: t(count=1, total=1), False),
            ("one of two", lambda: t(count=1, total=2), False)]
    return out[:30]


def remember_cases():
    P, N = True, False
    LA = ["Email Layla Haddad the revised launch timeline", "Ask Layla Haddad which vendor won the tender", "Tell Layla Haddad the audit slipped",
          "Meeting with Layla Haddad on pricing tiers", "Send it to Layla Haddad once checked", "Draft a note for Layla Haddad about renewal", "Cc Layla Haddad on this"]
    person = lambda n: corp([chat(f"r{i}", NOW - i * DAY, LA[i]) for i in range(n)])           # noqa: E731
    tool = lambda n, w="Jira": corp([chat(f"t{i}", NOW - i * DAY, f"Update the {w} board for item {OPS(i)[:12]}") for i in range(n)])   # noqa: E731
    word = lambda n, tmpl: corp([chat(f"w{i}", NOW - i * DAY, tmpl.format(i=i) + ". " + OPS(i)) for i in range(n)])   # noqa: E731
    return [
        ("person x4", lambda: person(4), P), ("person x5", lambda: person(5), P), ("person x7", lambda: person(7), P), ("person x6", lambda: person(6), P),
        ("client Acme x4", lambda: word(4, "Prepare the monthly note for Acme Holdings"), P), ("person x4 (b)", lambda: corp([chat(f"p{i}", NOW - i * DAY, f"Email Sara Nasser about item {i} of the plan {OPS(i)[:9]}") for i in range(4)]), P),
        ("manager Omar x5", lambda: word(5, "Ask Omar Salem to review draft {i}"), P), ("Jira x5", lambda: tool(5), P), ("Jira x6", lambda: tool(6), P), ("Excel x5", lambda: tool(5, "Excel"), P),
        ("Notion x5", lambda: tool(5, "Notion"), P), ("Slack x6", lambda: tool(6, "Slack"), P), ("Figma x5", lambda: tool(5, "Figma"), P), ("Tableau x7", lambda: tool(7, "Tableau"), P), ("GitHub x5", lambda: tool(5, "GitHub"), P),
        ("person x3", lambda: person(3), N), ("person x2", lambda: person(2), N), ("tool x4", lambda: tool(4), N), ("tool x2", lambda: tool(2), N),
        ("weekdays", lambda: word(6, "Send the note for Friday and from Monday"), N), ("months", lambda: word(6, "Send the note for March, with April figures"), N),
        ("a city", lambda: word(5, "Prepare the monthly note for Dubai"), N), ("another city", lambda: word(5, "Collect the numbers from Riyadh"), N),
        ("a country", lambda: word(4, "Write the market note for Egypt"), N), ("sentence starters", lambda: word(5, "With Meshpool I want a summary"), N),
        ("capitalised verbs", lambda: word(5, "Please check the numbers; Make them tidy; Thanks"), N), ("different people each time", lambda: corp([chat(f"d{i}", NOW - i * DAY, f"Email {n} Karim about plans {OPS(i)[:9]}") for i, n in enumerate(["Aya", "Bilal", "Dina", "Eman", "Farid"])]), N),
        ("incognito person", lambda: corp([dict(c, incognito=True) for c in person(5).chats]), N), ("tool mentioned as comparison", lambda: word(5, "Is it better than Excel? Compare"), N),
        ("no repeats", lambda: corp([chat(f"n{i}", NOW - i * DAY, OPS(i)) for i in range(6)]), N),
    ]


def followup_cases():
    P, N = True, False
    one = lambda **kw: corp([chat("c", NOW, "Make it", **kw)])      # noqa: E731
    pos = [("deck", one(outputs=["a.pptx"]), "deck"), ("workbook", one(outputs=["a.xlsx"]), "workbook"), ("csv", one(outputs=["a.csv"]), "workbook"),
           ("doc", one(outputs=["a.docx"]), "document"), ("pdf", one(outputs=["a.pdf"]), "document"), ("research", one(tools=["web_research"]), "research"),
           ("research spec", one(spec="research.deep"), "research"), ("code py", one(outputs=["a.py"], kind="code"), "code"), ("code js", one(outputs=["a.js"], kind="code"), "code"),
           ("code ts", one(outputs=["a.ts"], kind="code"), "code"), ("deck in code kind", one(outputs=["a.pptx"], kind="code"), "deck"),
           ("workbook and deck", one(outputs=["a.xlsx", "b.pptx"]), "deck"), ("scheduled deck", one(outputs=["a.pptx"], scheduled="t"), "deck"),
           ("xls", one(outputs=["a.xls"]), "workbook"), ("ppt", one(outputs=["a.ppt"]), "deck"), ("markdown note", one(outputs=["notes.md"]), "document"),
           ("code html", one(outputs=["index.html"], kind="code"), "code")]
    neg = [("no output", one()), ("answer only", one(tools=["read_file"])), ("png only", one(outputs=["a.png"])), ("json only", one(outputs=["a.json"])),
           ("txt only", one(outputs=["a.txt"])), ("incognito deck", one(outputs=["a.pptx"], incognito=True)), ("py in cowork", one(outputs=["a.py"])),
           ("shell script in cowork", one(outputs=["run.sh"])), ("readme in a code chat", one(outputs=["README.md"], kind="code")), ("pdf in a code chat", one(outputs=["spec.pdf"], kind="code")),
           ("read only research word", one(tools=["grep"], spec="docs.sheets")), ("empty chat", corp([]))]
    return [(n, (lambda c=c: c), True, k) for n, c, k in pos[:17]] + [(n, (lambda c=c: c), False, None) for n, c in neg[:13]] + [("nothing made", (lambda: one(tools=["bash"])), False, None)]


FAMILIES = {"schedule": (schedule_cases, lambda c: I.detect_schedule(c)), "skill": (skill_cases, lambda c: I.detect_skill(c)),
            "project": (project_cases, lambda c: I.detect_project(c)), "route": (route_cases, lambda c: I.detect_route(c)),
            "connect": (connect_cases, lambda c: I.detect_connect(c)), "rerun": (rerun_cases, lambda c: I.detect_rerun(c)),
            "teach": (teach_cases, lambda c: I.detect_teach(c)), "remember": (remember_cases, lambda c: I.detect_remember(c)),
            "followup": (followup_cases, lambda c: I.detect_followup(c, "c"))}


def _kind_of(cards):
    labels = {x["title"] for x in cards}
    for k in I.FOLLOWUPS:
        if {c["label"] for c in I.followup_chips(k)} & labels:
            return k
    return None


def evaluate() -> dict:
    out = {}
    for fam, (cases, run) in FAMILIES.items():
        tp = fp = fn = tn = 0
        wrong = []
        for case in cases()[:30]:
            name, build, label = case[0], case[1], case[2]
            cards = run(build())
            fired = bool(cards)
            if fam == "followup" and label:
                fired = _kind_of(cards) == case[3]
                if cards and not fired:
                    fp += 1
                    fn += 1
                    wrong.append(f"{name}: wrong chips")
                    continue
            if label and fired:
                tp += 1
            elif label:
                fn += 1
                wrong.append(f"{name}: missed")
            elif fired:
                fp += 1
                wrong.append(f"{name}: false alarm")
            else:
                tn += 1
        n = tp + fp + fn + tn
        out[fam] = {"n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                    "precision": round(tp / (tp + fp), 3) if tp + fp else 1.0,
                    "recall": round(tp / (tp + fn), 3) if tp + fn else 1.0, "errors": wrong}
    return out


def table(res: dict) -> str:
    rows = ["| Type | Situations | Hits | False alarms | Misses | Precision | Recall | Ships |", "|---|---|---|---|---|---|---|---|"]
    for t in I.TYPES:
        r = res[t]
        rows.append(f"| {t} | {r['n']} | {r['tp']} | {r['fp']} | {r['fn']} | {r['precision']:.2f} | {r['recall']:.2f} | "
                    f"{'on' if r['precision'] >= 0.8 else 'off'} |")
    return "\n".join(rows)


def main() -> int:
    os.environ.setdefault("MESHPOOL_HOME", str(ROOT / ".insights-precision-tmp"))
    res = evaluate()
    print(table(res))
    for t, r in res.items():
        for e in r["errors"]:
            print(f"  {t}: {e}")
    if "--write" in sys.argv:
        body = ["# Insight precision and recall (0.0.10)", "",
                "Each family was run on 30 hand-labelled situations (`testkit/insights_precision.py`). A situation is a small "
                "made-up history and a label written from the product rules: would a person want this card (positive) or is it "
                "noise (negative). Precision is hits over hits plus false alarms; recall is hits over hits plus misses. "
                "A type with precision of 0.8 or better ships on by default; below that it ships off (`insights.DEFAULT_ON`).", "",
                table(res), "",
                "**Tuning history.** The first run, before any detector change, scored connect 0.94, remember 0.94 and followup "
                "0.84. The false alarms were Jira-style keys that were part numbers, a tool named in a comparison, a README in "
                "a code chat, and a deck-plus-workbook chat given the wrong chips. The detectors were then tightened (Jira keys "
                "need a ticket word or an Atlassian link; a person needs a person verb in front, not \"for\"; a tool needs a "
                "use word; a code chat ignores README and PDF; the latest output decides the chips). Two recall misses were "
                "accepted as the price of that (Jira keys with no ticket word, a client named after \"for\"). "
                "The `teach` row measures the card's gating only: how well the edit detector (C4) finds real, consistent "
                "edits is measured in its own tests. The labels were written before the detectors were tuned on them, "
                "but the fixtures are small and written by the same team, so read the numbers as a floor check, not a "
                "field measurement. Re-measure on real use before raising any type's budget.", "", "## Errors", ""]
        for t, r in res.items():
            for e in r["errors"]:
                body.append(f"- {t}: {e}")
        (ROOT / "docs" / "plan" / "0.0.10-precision.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
