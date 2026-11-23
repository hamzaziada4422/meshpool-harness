"""Per-arm component tables from the captured first requests: python components.py [captures_dir] -> markdown on stdout.
chars is the text/JSON size of the component, ~tok = chars/4 (what the benchmark's token counts divide down to, within ~10%)."""
import json
import re
import sys
from pathlib import Path

CAP = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "captures")
exec(open(Path(__file__).parent / "analyze.py").read().split("def main")[0].replace("CAP = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / \"captures\")", "CAP = CAP_").replace("DUMP = CAP.parent / \"dumps\"\nDUMP.mkdir(exist_ok=True)", ""), {"CAP_": CAP, "__file__": __file__, **globals()}) if False else None
sys.argv = sys.argv[:1]
src = open(Path(__file__).parent / "analyze.py").read().split("def main")[0]
src = src.replace("DUMP = CAP.parent / \"dumps\"\nDUMP.mkdir(exist_ok=True)\n", "")
ns = {"__file__": __file__}
exec(src, ns)
text_of, tools_of, parts_of = ns["text_of"], ns["tools_of"], ns["parts_of"]


def first(arm, main=True):
    rows = [json.loads(l) for l in open(CAP / f"{arm}.jsonl")]
    rows = [r for r in rows if "/v1/" in r["path"]]
    return rows[0]["body"], rows[0]["bytes"]


def split(s, marks):
    pos = sorted((s.find(m), l) for l, m in marks if s.find(m) >= 0)
    out = []
    if pos and pos[0][0] > 0:
        out.append(("(start)", pos[0][0]))
    for i, (p, l) in enumerate(pos):
        e = pos[i + 1][0] if i + 1 < len(pos) else len(s)
        out.append((l, e - p))
    return out or [("(all)", len(s))]


def tool_groups(tl):
    g = {}
    for t in tl:
        m = re.match(r"mcp__(.+?)__", t["name"]) or re.match(r"(?:mcp__)?(meshpool[_-]\w+?|browser)\.", t["name"])
        k = ("MCP " + m.group(1).replace("-", "_")) if m else "built-in tools"
        g.setdefault(k, [0, 0])
        g[k][0] += 1
        g[k][1] += t["chars"]
    return g


USER_MARKS = [("user task", "In inventory"), ("capability note [Meshpool] This message asks...", "[Meshpool] This message asks"),
              ("route addon [Harness: ...]", "[Harness:"),
              ("Today line", "Today is"), ("HONESTY text", "Honesty comes first"), ("PLAIN text", "Write in plain sentences"),
              ("Notes from Meshpool (+ENGINES)", "Notes from Meshpool"), ("<environment_snapshot>", "<environment_snapshot>"),
              ("pace/quick note", "[Meshpool] This is a quick request")]
SYS_MARKS = {
    "mp-cli": [("SYSTEM (code agent)", "You are Meshpool, a coding agent"), ("V3_RULES", "Working efficiently"), ("E2E_RULES", "Finishing the job"),
               ("skills index", "\n\nSkills:"), ("PLAIN", "Write in plain sentences"), ("HONESTY", "Honesty comes first")],
    "mp-work": [("WORK_SYSTEM", "You are Meshpool Cowork"), ("V3_RULES", "Working efficiently"), ("E2E_RULES", "Finishing the job"),
                ("skills index", "\n\nSkills:"), ("PLAIN", "Write in plain sentences"), ("HONESTY", "Honesty comes first")],
    "mp-on-pi": [("Pi preamble + <tools> + <rules>", "You are a coding assistant"), ("Pi <docs> block", "<docs>"),
                 ("Pi <skills> header", "<skills>"), ("Pi <available_skills> list", "<available_skills>"), ("cwd", "</skills>")],
}


def emit(title, rows, total_bytes, note=""):
    tot = sum(c for _, c, _ in rows)
    print(f"\n### {title}\n")
    if note:
        print(note + "\n")
    print("| component | chars | ~tokens | source |")
    print("|---|---:|---:|---|")
    for n, c, s in rows:
        print(f"| {n} | {c:,} | {c // 4:,} | {s} |")
    print(f"| **sum of components** | **{tot:,}** | **{tot // 4:,}** | request body {total_bytes:,} bytes = ~{total_bytes // 4:,} tokens |")


def tool_rows(tl, srcmap):
    g = tool_groups(tl)
    return [(f"tools: {k} ({n})", c, srcmap.get(k, "")) for k, (n, c) in g.items()]


def top15(tl):
    return ", ".join(f"{t['name'].replace('mcp__', '')} {t['chars']}" for t in sorted(tl, key=lambda t: -t["chars"])[:15])


SRC_USER = {"user task": "testkit/bench3/tasks.py xlsx_edit", "capability note [Meshpool] This message asks...": "capabilities.py:100 note(), added at ui/agent_api.py:837",
            "Today line": "assistant.py:367 context_block (first message)", "HONESTY text": "assistant.py:15",
            "PLAIN text": "assistant.py:45", "Notes from Meshpool (+ENGINES)": "assistant.py:431, ENGINES assistant.py:23",
            "<environment_snapshot>": "agent/core.py:1320 (_env_snapshot 537)"}
SRC_SYS = {"SYSTEM (code agent)": "agent/core.py:143", "WORK_SYSTEM": "agent/core.py:242", "V3_RULES": "agent/core.py:177, appended 972",
           "E2E_RULES": "agent/core.py:188, appended 974", "skills index": "skills.py:242 catalog_text, appended core.py:983",
           "PLAIN": "assistant.py:45, appended core.py:993", "HONESTY": "assistant.py:15, appended core.py:993",
           "Pi preamble + <tools> + <rules>": "Pi 1.5.2 built-in prompt (vendor/pi), 7 tools from settings defaultTools harnesses/pi_rpc.py:150",
           "Pi <docs> block": "Pi 1.5.2 built-in prompt", "Pi <skills> header": "Pi 1.5.2 built-in prompt",
           "Pi <available_skills> list": "130 skills copied into <PI_HOME>/skills/meshpool by pi_rpc.py:156 -> skills.py:380 sync_to (app/defaults/skills)",
           "cwd": "Pi"}


def chat_arm(arm, title):
    b, nb = first(arm)
    ms = b["messages"]
    sys_t, usr_t = text_of(ms[0]["content"]), text_of(ms[1]["content"])
    rows = [(f"system: {n}", c, SRC_SYS.get(n, "")) for n, c in split(sys_t, SYS_MARKS[arm])]
    rows += [(f"user msg: {n}", c, SRC_USER.get(n, "")) for n, c in split(usr_t, USER_MARKS)]
    tl = tools_of(b)
    srcmap = {"built-in tools": "core/tools.py:239 SCHEMAS + agent/core.py:57 BUILTIN/66 WORK_TOOLS" if arm != "mp-on-pi" else "Pi built-ins (read, bash, edit, write, grep, find, ls)",
              "MCP meshpool_office": "agent/office_mcp_server.py:726 TOOLS", "MCP browser": "agent/browser_mcp_server.py:534 TOOLS",
              "MCP meshpool_assistant": "agent/assistant_mcp_server.py:72-104 TOOLS", "MCP meshpool_tools": "agent/demo_mcp_server.py",
              "MCP meshpool_web": "agent/web_mcp_server.py"}
    rows += tool_rows(tl, srcmap)
    rest = {k: len(json.dumps(v)) for k, v in b.items() if k not in ("tools", "messages")}
    emit(title, rows, nb, f"{len(tl)} tools, {sum(t['chars'] for t in tl):,} chars of schema. 15 largest tools (chars): {top15(tl)}. Other body keys: {rest}")


chat_arm("mp-on-pi", "mp-on-pi, request 1")
for arm in ("mp-work", "mp-cli"):
    rows = [json.loads(l) for l in open(CAP / f"{arm}.jsonl")]
    rows = [r for r in rows if "/chat/completions" in r["path"]]
    b, nb = rows[0]["body"], rows[0]["bytes"]
    ms = b["messages"]
    sys_t, usr_t = text_of(ms[0]["content"]), text_of(ms[1]["content"])
    rr = [(f"system: {n}", c, SRC_SYS.get(n, "")) for n, c in split(sys_t, SYS_MARKS[arm])]
    rr += [(f"user msg: {n}", c, SRC_USER.get(n, "")) for n, c in split(usr_t, USER_MARKS)]
    tl = tools_of(b)
    srcmap = {"built-in tools": "core/tools.py:239 SCHEMAS (+ask.py, delegate.py:167, core.py office_task) ; names in agent/core.py:57-66",
              "MCP meshpool_office": "agent/office_mcp_server.py:726 TOOLS", "MCP browser": "agent/browser_mcp_server.py:534 TOOLS",
              "MCP meshpool_assistant": "agent/assistant_mcp_server.py:72-104 TOOLS", "MCP meshpool_tools": "agent/demo_mcp_server.py"}
    rr += tool_rows(tl, srcmap)
    emit(f"{arm}, request 1 of the agent", rr, nb, f"{len(tl)} tools. 15 largest (chars): {top15(tl)}")
# responses-wire arms
def resp_arm(arm, title, rowsel=0):
    rows = [json.loads(l) for l in open(CAP / f"{arm}.jsonl")]
    rows = [r for r in rows if "/v1/" in r["path"]]
    b, nb = rows[rowsel]["body"], rows[rowsel]["bytes"]
    rr = []
    for lab, t in parts_of(b):
        if "In inventory" in t and len(t) > 300 and "<environment_context>" not in t:
            rr += [(f"user msg: {n}", c, SRC_USER.get(n, "")) for n, c in split(t, USER_MARKS)]
        else:
            what = t[:60].replace("\n", " ").replace("|", "/")
            rr.append((f"{lab} ({what}...)", len(t), CODEX_SRC.get(lab.split(".")[0], "") if rowsel == 0 else "second Codex session (title writer), model gpt-5.6-luna"))
    tl = tools_of(b)
    srcmap = {"built-in tools": "Codex built-ins (exec_command, write_stdin, multi_agent_v1, goal tools, view_image, ...)",
              "MCP meshpool_office": "agent/office_mcp_server.py:726 TOOLS via harnesses/acp.py:200 office_server, acp.py:627",
              "MCP browser": "agent/browser_mcp_server.py:534, via acp.py:159 mcp_servers_for_acp",
              "MCP meshpool_assistant": "agent/assistant_mcp_server.py:72-104, via acp.py:159",
              "MCP meshpool_tools": "agent/demo_mcp_server.py, via acp.py:159", "MCP meshpool_web": "agent/web_mcp_server.py, via acp.py:159"}
    rr += tool_rows(tl, srcmap)
    emit(title, rr, nb, f"{len(tl)} tools. 15 largest tools (chars): {top15(tl)}")


CODEX_SRC = {"in0": "Codex's own base instructions (model-family prompt)", "in1": "Codex skills list (system skills)",
             "in2": "Codex <environment_context>", "in3": "task"}   # indexes of request 1 (deepseek slug: no code-mode)
for arm in ("pi", "pi152"):
    b, nb = first(arm)
    rr = [(f"{lab} ({t[:50].replace(chr(10), ' ')}...)", len(t), "") for lab, t in parts_of(b)]
    tl = tools_of(b)
    rr.append((f"tools ({len(tl)})", sum(t["chars"] for t in tl), ""))
    emit(f"{arm}, request 1", rr, nb, f"15 largest tools (chars): {top15(tl)}")
resp_arm("codex", "codex, request 1")
resp_arm("mp-on-codex", "mp-on-codex, request 1 (main session)")
resp_arm("mp-on-codex", "mp-on-codex, request 3 = the second Codex session codex-acp starts to write the chat title", 2)
