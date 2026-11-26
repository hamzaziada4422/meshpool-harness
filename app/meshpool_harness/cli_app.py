"""The app's features as commands: `meshpool chats|projects|memory|skills|plugins|mcp|schedule|agents|models|usage|settings`.

Every handler calls the function the app's route calls (ui/agent_api.py, project_store, skills, plugins, assistant,
memory_api, settings_api, usage_view, chat_rewind...) and prints what it returns, as a short table or, with --json, as the
function's own dict. The terminal's slash commands run these same handlers through `run_text`. Nothing is counted, priced
or validated here.

HELP is the one table behind `meshpool help`, `meshpool CMD -h` and the docs.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys

HELP = {
    "chats": "Usage: meshpool chats list [--all] [--project ID] [--json] | search Q | show ID | rename ID TITLE\n"
             "       | pin|unpin|archive|unarchive|delete ID | move ID [PROJECT] | fork ID [--turn N] | undo ID --turn N\n"
             "       | diff ID [--turn N] | rewind ID --turn N | receipt ID\n"
             "Chats made here and in the app are the same chats.",
    "projects": "Usage: meshpool projects list | new NAME [--folder PATH] | show ID | delete ID\n"
                "       | memory ID [show | add TEXT | clear] | knowledge ID [add FILE | remove NAME]",
    "memory": "Usage: meshpool memory list [--q TEXT] | add TEXT [--scope everywhere|project] | forget ID | search TEXT",
    "skills": "Usage: meshpool skills list | show ID | enable ID | disable ID | new NAME --description TEXT "
              "[--body-file FILE] | remove ID | upload FILE...",
    "plugins": "Usage: meshpool plugins list | install SOURCE | remove NAME | enable NAME | disable NAME",
    "mcp": "Usage: meshpool mcp            start the configured MCP servers and show their status\n"
           "       meshpool mcp list | enable NAME | disable NAME | test NAME | library [add ID]",
    "schedule": "Usage: meshpool schedule list | add --name N --prompt P [--when daily|weekly|once|every] [--time HH:MM]\n"
                "       [--day mon] [--at ISO] [--minutes N] [--cron \"M H D MON DOW\"] [--folder DIR] [--agent ID]\n"
                "       | update ID --field VALUE...\n"
                "       | delete ID | enable ID | disable ID | run ID",
    "agents": "Usage: meshpool agents list | default ID [--mode code|work]\nThe agents a chat can run on (/agent in a session).",
    "models": "Usage: meshpool models list [--refresh] | default ID | favorite ID [--off]",
    "usage": "Usage: meshpool usage [--days N] [--json]\nWhat the app's Usage page shows, from the one usage ledger.",
    "settings": "Usage: meshpool settings get [KEY] | set KEY=VALUE... | instructions [TEXT]\nSecret values are shown only as 'set'. "
                "Observability: MH_OTEL=1 MH_OTEL_ENDPOINT=URL (MH_OTEL_CONTENT=1 adds prompt text).",
    "help": "Usage: meshpool help [COMMAND | telemetry]",
    "login": "Usage: meshpool login\nAsk for your API key and save it.",
    "setup": "Usage: meshpool setup\nFirst-run setup: key, folder and preferences.",
    "run-due": "Usage: meshpool run-due\nRun every scheduled task that is due, then exit.",
    "scheduler": "Usage: meshpool scheduler install | uninstall | status\nRun scheduled tasks while the app is closed.",
    "import": "Usage: meshpool import <export.zip | claude-code | codex>\n       meshpool import agents [--dry-run] [--yes] [--from claude,codex]",
    "app": "Usage: meshpool app\nOpen the desktop app.",
    "connect": "Usage: meshpool connect [name | --list]\nConnect an app or service.",
    "disconnect": "Usage: meshpool disconnect <name>\nRemove a connection.",
    "worktree": "Usage: meshpool worktree list [--all] | diff <id> [--stat] | merge <id> | discard <id>\n"
                "Isolated copies made by `meshpool --worktree`, chats, sub-agents and scheduled runs.",
    "permissions": "Usage: meshpool permissions [list | allow RULE | deny RULE | remove RULE] [--project] [--dir DIR]\n"
                   "The saved allow and deny rules (a tool name, mcp__server__*, or bash:PATTERN).",
    "agent": "Usage: meshpool [agent] [options] [-p PROMPT]   (meshpool -h for every option)\n"
             "Options include --effort LEVEL, --mode code|work|auto, --agent ID, --resume ID, --worktree, --incognito.",
    "run": "Usage: meshpool run REQUEST [--files F...] [--engine E] [--out DIR] [--model M]\nRun one request on an engine.",
    "route": "Usage: meshpool route REQUEST [--files F...]\nShow which engine a request would use.",
    "work": "Usage: meshpool work SOURCE [--report] [--json]\nFetch a task from a connector, run it, report back.",
    "ui": "Usage: meshpool ui [--port N] [--no-browser]\nOpen the Meshpool web app.",
    "check": "Usage: meshpool check [--live]\nShow settings and installed tools; --live sends one tiny model call.",
    "install-browser": "Usage: meshpool install-browser\nInstall Playwright and Chromium for the browser engine.",
    "acp": "Usage: meshpool acp [--profile code|work] [--auto]\nServe the Meshpool agent over ACP on stdio.",
    "trust": "Usage: meshpool trust [PATH] [--revoke] [--list]\nTrust a folder so its hooks, allow rules and MCP servers "
             "apply; --revoke takes it back, --list shows trusted folders.",
    "office": "Usage: meshpool office install | status | uninstall | manifest\nSet up the meshpool panel in Excel, "
              "PowerPoint and Word.",
    "completion": "Usage: meshpool completion bash|zsh|fish\nPrint a shell completion script.",
    "doctor": "Usage: meshpool doctor [FOLDER]\nFind problems in settings, hooks, rules, MCP server lists, skills and "
              "sub-agents.",
}
TOPICS = {
    "telemetry": "OpenTelemetry export is off. Turn it on with `meshpool settings set MH_OTEL=1 MH_OTEL_ENDPOINT=URL`\n"
                 "(or OTEL_EXPORTER_OTLP_ENDPOINT). Spans and metrics carry ids, names, counts, durations and cost, and\n"
                 "no prompt, reply or tool text unless MH_OTEL_CONTENT=1. Nothing is sent without the endpoint.",
}
INTERNAL = {"app", "acp"}


def _out(obj, as_json: bool, lines=None):
    if as_json or lines is None:
        print(json.dumps(obj, indent=1, default=str))
    else:
        for x in lines:
            print(x)


def _split(args, valued=(), flags=()):
    """-> (positional, {flag: value}); `--x V` for valued flags, bare `--x` for boolean ones."""
    pos, opt, i = [], {}, 0
    while i < len(args):
        a = args[i]
        if a in valued:
            if i + 1 >= len(args):
                raise ValueError(f"{a} needs a value.")
            opt[a], i = args[i + 1], i + 2
        elif a in flags:
            opt[a], i = True, i + 1
        elif re.fullmatch(r"--[A-Za-z][\w-]*", a):       # a mistyped or unsupported option is said, not dropped
            known = ", ".join(sorted({*valued, *flags})) or "none"
            raise ValueError(f"Unknown option {a}. This command takes: {known}.")
        else:
            pos.append(a)
            i += 1
    return pos, opt


def _need(pos, n, cmd):
    if len(pos) < n:
        raise ValueError(HELP[cmd].splitlines()[0])
    return pos


def _turn(opt) -> int | None:
    if "--turn" not in opt:
        return None
    if not str(opt["--turn"]).isdigit():
        raise ValueError("--turn takes a turn number (1 is the first message).")
    return int(opt["--turn"])


def _point(sid: str, turn: int) -> dict:
    from meshpool_harness import chat_rewind as CR
    p = next((x for x in CR.points(sid) if x.get("turn") == turn), None)
    if not p:
        raise ValueError(f"This chat has no turn {turn}.")
    return p


# ------------------------------------------------------------------------------------------------ chats
def _chats(a):
    from meshpool_harness import chats
    pos, o = _split(a, ("--project", "--turn"), ("--all", "--json"))
    sub = pos[0] if pos else "list"
    pos = pos[1:]
    js = bool(o.get("--json"))
    if sub == "list":
        rows = [r for r in chats.listing() if o.get("--all") or not r.get("archived")]
        if o.get("--project"):
            rows = [r for r in rows if r.get("project_id") == o["--project"]]
        _out(rows, js, [f"{r.get('id')}  {'*' if r.get('pinned') else ' '} {r.get('title') or 'Chat'}  "
                        f"{r.get('workdir') or ''}" for r in rows] or ["(no chats)"])
    elif sub == "search":
        rows = chats.search(" ".join(_need(pos, 1, "chats")))
        _out(rows, js, [f"{r.get('id')}  {r.get('title') or 'Chat'}" for r in rows] or ["(no matches)"])
    elif sub == "show":
        v = chats.view(_need(pos, 1, "chats")[0], 0)
        _out(v, js, [f"{e.get('type')}: {str(e.get('text', ''))[:300]}" for e in v.get("events", [])
                     if e.get("type") in ("user", "assistant", "text")])
    elif sub in ("rename", "pin", "unpin", "archive", "unarchive", "delete", "move"):
        sid = _need(pos, 1, "chats")[0]
        body = {"action": sub}
        if sub == "rename":
            body["title"] = " ".join(_need(pos, 2, "chats")[1:])
        if sub == "move":
            body["project_id"] = pos[1] if len(pos) > 1 else ""
        _out(chats.thread_action(sid, body), js, [f"{sub}: {sid}"])
    elif sub == "fork":
        sid, t = _need(pos, 1, "chats")[0], _turn(o)
        from meshpool_harness import chat_rewind as CR
        new = CR.fork(sid, None if t is None else _point(sid, t)["message_index"])
        _out({"id": new, "forked_from": sid}, js, [f"forked {sid} -> {new}"])
    elif sub in ("undo", "rewind"):
        sid, t = _need(pos, 1, "chats")[0], _turn(o)
        if t is None:
            raise ValueError(f"chats {sub} needs --turn N.")
        if sub == "undo":
            r = chats.get(sid).revert(t)
        else:
            from meshpool_harness import chat_rewind as CR
            r = CR.rewind(sid, _point(sid, t)["message_index"])
        _out(r, js, [f"{sub}: turn {t} of {sid}"])
    elif sub == "diff":
        sid, t = _need(pos, 1, "chats")[0], _turn(o)
        s = chats.get(sid)
        d = s.cp.diff(t) if s.cp else {"patch": "", "note": "No checkpoints for this chat."}
        _out(d, js, [d.get("patch") or d.get("note") or "No changes recorded."])
    elif sub == "receipt":
        from meshpool_harness import receipts as RC
        r = RC.receipt_for(_need(pos, 1, "chats")[0])
        _out(r, js, [RC.line(r) if r else "(no receipt for this chat)"])
    else:
        raise ValueError(HELP["chats"].splitlines()[0])
    return 0


# ------------------------------------------------------------------------------------------------ projects
def _projects(a):
    from meshpool_harness import project_store as PS
    pos, o = _split(a, ("--folder",), ("--json",))
    sub, js = (pos[0] if pos else "list"), bool(o.get("--json"))
    pos = pos[1:]
    if sub == "list":
        rows = PS.list_projects()
        _out(rows, js, [f"{r['id']}  {r.get('name')}" for r in rows] or ["(no projects)"])
    elif sub == "new":
        r = PS.create(" ".join(_need(pos, 1, "projects")), link=o.get("--folder"))
        _out(r, js, [f"{r['id']}  {r.get('name')}"])
    elif sub == "show":
        r = PS.detail(_need(pos, 1, "projects")[0])
        _out(r, js, [f"{r.get('name')}", r.get("instructions") or "", f"knowledge: {len(r.get('knowledge') or [])} file(s)"])
    elif sub == "delete":
        _out(PS.api_action(_need(pos, 1, "projects")[0], {"action": "delete"}), js, ["deleted"])
    elif sub == "memory":
        pid = _need(pos, 1, "projects")[0]
        act = pos[1] if len(pos) > 1 else "show"
        if act == "show":
            _out({"memory": PS.memory(pid)}, js, [PS.memory(pid) or "(empty)"])
        elif act in ("add", "clear"):
            r = PS.api_action(pid, {"action": "memory_" + act, "text": " ".join(pos[2:])})
            _out(r, js, [f"memory {act}: ok"])
        else:
            raise ValueError(HELP["projects"].splitlines()[1])
    elif sub == "knowledge":
        import base64
        from pathlib import Path
        pid = _need(pos, 3, "projects")[0]
        if pos[1] == "add":
            p = Path(pos[2]).expanduser()
            r = PS.api_action(pid, {"action": "knowledge_add", "name": p.name,
                                    "data": base64.b64encode(p.read_bytes()).decode()})
        elif pos[1] == "remove":
            r = PS.api_action(pid, {"action": "knowledge_remove", "name": pos[2]})
        else:
            raise ValueError(HELP["projects"].splitlines()[1])
        _out(r, js, ["ok"])
    else:
        raise ValueError(HELP["projects"].splitlines()[0])
    return 0


# ------------------------------------------------------------------------------------------------ memory
def _memory(a):
    from meshpool_harness.ui import memory_api as MA
    pos, o = _split(a, ("--q", "--scope"), ("--json",))
    sub, js = (pos[0] if pos else "list"), bool(o.get("--json"))
    pos = pos[1:]
    if sub in ("list", "search"):
        q = o.get("--q") or " ".join(pos)
        facts = MA.view({"q": [q]})["facts"]
        _out(facts, js, [f"{f['id']}  {f.get('text')}" for f in facts] or ["(no memories)"])
    elif sub == "add":
        r = MA.act({"action": "add", "text": " ".join(_need(pos, 1, "memory")), "scope": o.get("--scope") or "everywhere"})
        _out(r, js, [f"{r['fact']['id']}  {r['fact'].get('text')}"])
    elif sub == "forget":
        _out(MA.act({"action": "delete", "id": _need(pos, 1, "memory")[0]}), js, ["forgotten"])
    else:
        raise ValueError(HELP["memory"].splitlines()[0])
    return 0


# ------------------------------------------------------------------------------------------------ skills
def _skills(a):
    from meshpool_harness import skills as SK
    pos, o = _split(a, ("--description", "--body-file"), ("--json",))
    sub, js = (pos[0] if pos else "list"), bool(o.get("--json"))
    pos = pos[1:]
    if sub == "list":
        rows = SK.load_all()
        _out(rows, js, [f"{r.get('id')}  {'on ' if r.get('enabled', True) else 'off'} {r.get('description', '')[:80]}"
                        for r in rows] or ["(no skills)"])
    elif sub == "show":
        r = SK.get(_need(pos, 1, "skills")[0])
        if not r:
            raise KeyError("No such skill.")
        _out(r, js, [r.get("text") or r.get("body") or json.dumps(r, default=str)])
    elif sub in ("enable", "disable"):
        r = SK.api_action({"action": "toggle", "id": _need(pos, 1, "skills")[0], "on": sub == "enable"})
        _out(r, js, [f"{sub}d: {pos[0]}"])
    elif sub == "new":
        body = open(o["--body-file"], encoding="utf-8").read() if o.get("--body-file") else ""
        r = SK.api_action({"action": "create", "name": " ".join(_need(pos, 1, "skills")),
                           "description": o.get("--description") or "", "body": body})
        _out(r, js, ["created"])
    elif sub == "remove":
        _out(SK.api_action({"action": "remove", "id": _need(pos, 1, "skills")[0]}), js, ["removed"])
    elif sub == "upload":
        import base64
        from pathlib import Path
        files = [{"name": Path(p).name, "data": base64.b64encode(Path(p).expanduser().read_bytes()).decode()}
                 for p in _need(pos, 1, "skills")]
        _out(SK.api_action({"action": "upload", "files": files}), js, [f"uploaded {len(files)} file(s)"])
    else:
        raise ValueError(HELP["skills"].splitlines()[0])
    return 0


# ------------------------------------------------------------------------------------------------ plugins
def _plugins(a):
    from meshpool_harness import plugins as PL
    pos, o = _split(a, (), ("--json",))
    sub, js = (pos[0] if pos else "list"), bool(o.get("--json"))
    pos = pos[1:]
    if sub == "list":
        rows = PL.api_view()["plugins"]
        _out(rows, js, [f"{r.get('name')}  {'on' if r.get('enabled', True) else 'off'}" for r in rows] or ["(none installed)"])
    elif sub == "install":
        r = PL.api_action({"action": "install", "source": _need(pos, 1, "plugins")[0]})
        _out(r, js, [f"{x.get('name')}: {'; '.join(x.get('notes') or [])}" for x in r.get("installed", [])] or ["nothing installed"])
    elif sub == "remove":
        _out(PL.api_action({"action": "remove", "name": _need(pos, 1, "plugins")[0]}), js, ["removed"])
    elif sub in ("enable", "disable"):
        _out(PL.api_action({"action": "enable", "name": _need(pos, 1, "plugins")[0], "on": sub == "enable"}), js, [sub + "d"])
    else:
        raise ValueError(HELP["plugins"].splitlines()[0])
    return 0


# ------------------------------------------------------------------------------------------------ mcp
def _mcp(a):
    from meshpool_harness import chats
    pos, o = _split(a, (), ("--json",))
    sub, js = (pos[0] if pos else "list"), bool(o.get("--json"))   # `meshpool mcp --json`: the list, as JSON
    pos = pos[1:]
    if sub == "list":
        r = chats.mcp_info()
        _out(r, js, [f"{s['name']}  {'on' if s['enabled'] else 'off'}  {s.get('kind', '')}" for s in r.get("servers", [])]
             or ["(no MCP servers)"])
    elif sub in ("enable", "disable"):
        r = chats.mcp_save({"name": _need(pos, 1, "mcp")[0], "enabled": sub == "enable"})
        _out(r, js, [f"{sub}d: {pos[0]}"])
    elif sub == "test":
        r = chats.mcp_test({"workdir": ""})
        rows = [s for s in r.get("status", []) if not pos or s.get("name") == pos[0]]
        _out(r, js, [f"{s.get('name')}  {s.get('status')}  {s.get('error') or ''}" for s in rows] or [r.get("error") or "(none)"])
    elif sub == "library":
        from meshpool_harness import mcp_library as ML
        if pos[:1] == ["add"]:
            _out(ML.api_action({"action": "add", "id": _need(pos, 2, "mcp")[1]}), js, ["added"])
        else:
            lib = ML.api_view()["library"]
            _out(lib, js, [f"{x.get('id')}  {x.get('name', '')}" for x in lib])
    else:
        raise ValueError(HELP["mcp"].splitlines()[1])
    return 0


# ------------------------------------------------------------------------------------------------ schedule
def _schedule(a):
    from meshpool_harness import assistant as AS
    pos, o = _split(a, ("--name", "--prompt", "--when", "--time", "--day", "--at", "--minutes", "--folder", "--agent",
                        "--mode", "--field", "--cron"), ("--json",))
    sub, js = (pos[0] if pos else "list"), bool(o.get("--json"))
    pos = pos[1:]
    if sub == "list":
        rows = AS.api_view()["tasks"]
        _out(rows, js, [f"{t['id']}  {'on ' if t.get('enabled', True) else 'off'} {t.get('name')}  {t.get('describe', '')}"
                        for t in rows] or ["(no scheduled tasks)"])
        return 0
    body = {k: o[f"--{k}"] for k in ("name", "prompt", "when", "time", "day", "at", "minutes", "folder", "cron")
            if f"--{k}" in o}
    if "--agent" in o:
        body["harness"] = o["--agent"]
    if "--mode" in o:
        body["kind"] = o["--mode"]
    if sub == "add":
        r = AS.api_action(dict(body, action="task_add"))
    elif sub == "update":
        r = AS.api_action(dict(body, action="task_update", id=_need(pos, 1, "schedule")[0]))
    elif sub in ("delete", "run"):
        r = AS.api_action({"action": "task_" + sub, "id": _need(pos, 1, "schedule")[0]})
    elif sub in ("enable", "disable"):
        r = AS.api_action({"action": "task_update", "id": _need(pos, 1, "schedule")[0], "enabled": sub == "enable"})
    else:
        raise ValueError(HELP["schedule"].splitlines()[0])
    _out(r, js, [f"{sub}: ok"] + ([f"chat: {r['thread']}"] if r.get("thread") else []))
    return 0


# ------------------------------------------------------------------------------------------------ agents, models
def _agents(a):
    from meshpool_harness import chats
    pos, o = _split(a, ("--mode",), ("--json",))
    sub, js = (pos[0] if pos else "list"), bool(o.get("--json"))
    if sub == "list":
        r = chats.harness_info()
        _out(r, js, [f"{h['id']}  {h.get('name', '')}" for h in r["harnesses"]])
    elif sub == "default":
        r = chats.harness_change({"default_mode": o.get("--mode") or "code", "id": _need(pos[1:], 1, "agents")[0]})
        _out(r, js, [f"default: {r['defaults']}"])
    else:
        raise ValueError(HELP["agents"].splitlines()[0])
    return 0


def _models(a):
    from meshpool_harness.ui import workbench as W
    pos, o = _split(a, (), ("--json", "--refresh", "--off"))
    sub, js = (pos[0] if pos else "list"), bool(o.get("--json"))
    if sub == "list":
        r = W.models(refresh=bool(o.get("--refresh")))
        _out(r, js, [f"{m.get('id')}{'  (default)' if m.get('id') == r.get('default') else ''}" for m in r["models"]]
             or [r.get("error") or "(no models)"])
    elif sub == "default":
        r = W.set_default_model(_need(pos[1:], 1, "models")[0])
        _out(r, js, [f"default model: {r.get('default')}"])
    elif sub == "favorite":
        r = W.set_favorite(_need(pos[1:], 1, "models")[0], not o.get("--off"))
        _out(r, js, [f"favorites: {', '.join(r.get('favorites') or []) or '(none)'}"])
    else:
        raise ValueError(HELP["models"].splitlines()[0])
    return 0


# ------------------------------------------------------------------------------------------------ usage, settings
def _usage(a):
    from meshpool_harness import usage_view as UV
    pos, o = _split(a, ("--days",), ("--json",))
    r = UV.summary(days=int(o.get("--days") or 30))
    t = (r.get("usage") or {}).get("total") or {}
    lines = [f"{t.get('calls', 0):,} model calls, {t.get('in', 0):,} input tokens ({t.get('cached', 0):,} cached), "
             f"{t.get('out', 0):,} output tokens, cost ${t.get('cost', 0):.4f}"
             + (f" ({t['unpriced_calls']} unpriced)" if t.get("unpriced_calls") else "")]
    for m, b in sorted(((r.get("usage") or {}).get("by_model") or {}).items(), key=lambda kv: -kv[1].get("calls", 0))[:8]:
        lines.append(f"  {m}: {b.get('calls', 0)} calls, ${b.get('cost', 0):.4f}")
    k = r.get("key") or {}
    if k.get("spend") is not None:
        lines.append(f"key spend: {k.get('spend')}")
    _out(r, bool(o.get("--json")), lines)
    return 0


def _settings(a):
    from meshpool_harness import settings_api as SA
    pos, o = _split(a, (), ("--json",))
    sub, js = (pos[0] if pos else "get"), bool(o.get("--json"))
    pos = pos[1:]
    if sub == "get":
        rows = [r for r in SA.values() if not pos or r["key"] in pos]
        _out(rows, js, [f"{r['key']}={('(set)' if r.get('set') else '') if 'set' in r else r['value']}" for r in rows])
    elif sub == "instructions":                   # the Instructions page: what every chat is told about you
        from meshpool_harness import assistant as AS
        if pos:
            AS.api_action({"action": "instructions", "text": " ".join(pos)})
        text = AS.api_view()["instructions"]
        _out({"instructions": text}, js, [text or "(none)"])
    elif sub == "set":
        vals = {}
        for x in _need(pos, 1, "settings"):
            if "=" not in x:
                raise ValueError("settings set takes KEY=VALUE.")
            k, _, v = x.partition("=")
            vals[k.strip()] = v
        unknown = [k for k in vals if k not in {r["key"] for r in SA.values()}]
        if unknown:
            raise ValueError(f"Unknown setting: {', '.join(unknown)}. `meshpool settings get` lists them.")
        SA.save_settings({"values": vals})
        _out({"saved": sorted(vals)}, js, [f"saved: {', '.join(sorted(vals))}"])
    else:
        raise ValueError(HELP["settings"].splitlines()[0])
    return 0


def _help(a):
    if a and a[0] in {**HELP, **TOPICS}:
        print({**HELP, **TOPICS}[a[0]])
        return 0
    print("meshpool: the Meshpool agent in your terminal. `meshpool` starts a session, `meshpool -p \"...\"` answers once.\n")
    w = max(len(k) for k in HELP) + 2
    for k, v in HELP.items():
        if k not in INTERNAL:
            print(f"  {k:<{w}}{v.splitlines()[0].replace('Usage: ', '').replace('meshpool ', '', 1)[:100]}")
    print("\n`meshpool help COMMAND` or `meshpool COMMAND -h` for details; `meshpool help telemetry` for OpenTelemetry.")
    return 0


HANDLERS = {"chats": _chats, "projects": _projects, "memory": _memory, "skills": _skills, "plugins": _plugins,
            "mcp": _mcp, "schedule": _schedule, "agents": _agents, "models": _models, "usage": _usage,
            "settings": _settings, "help": _help}
NEW = tuple(HANDLERS)


def main(argv) -> int:
    argv = list(argv)
    name, rest = argv[0], argv[1:]
    if any(x in ("-h", "--help") for x in rest):
        print(HELP.get(name, ""))
        return 0
    try:
        return HANDLERS[name](rest)
    except (ValueError, KeyError, OSError) as e:
        from meshpool_harness.errors import plain
        print(f"error: {plain(e)}", file=sys.stderr)
        return 1


def run_text(argv) -> tuple[int, str]:
    """The same handlers for the terminal's slash commands: (exit code, what they printed)."""
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = main(argv)
    return rc, (buf.getvalue() + err.getvalue()).rstrip()
