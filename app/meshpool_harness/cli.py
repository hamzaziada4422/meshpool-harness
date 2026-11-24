"""Single entry point.

  meshpool                                           # interactive coding agent in the current folder
  meshpool -p "request" [--auto]                     # one request, print the reply, exit
  meshpool mcp                                       # MCP servers from mcp.json and their tools
  meshpool acp [--profile work] [--auto]             # serve the agent over ACP (Zed, JetBrains, other apps)
  meshpool setup                                     # first-run walkthrough, then a session
  meshpool login                                     # save your API key
  python -m meshpool_harness ui                      # the Meshpool web app in your browser
  python -m meshpool_harness check [--live]          # settings, installed tools, and (with --live) one tiny model call
  python -m meshpool_harness route "request" [--files a.csv ...]
  python -m meshpool_harness run "request" [--files ...] [--out DIR] [--engine code,slides] [--workdir REPO]
  python -m meshpool_harness install-browser         # optional: Playwright + Chromium for the browser engine

Settings come from meshpool.config (next to the launcher); shell variables win over the file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The benchmarked flag set for the code and browser engines of `meshpool run` (run C in docs/BENCHMARKS.md).
ENGINE_FLAGS = {"MH_STREAM": "1", "MH_CACHE_KEY": "1", "MH_STALL": "1", "MH_TURN_NOTE": "1", "MH_TODO": "1",
                "MH_ENV_SNAPSHOT": "1", "MH_DOOM": "1", "MH_MULTI_CMD": "1", "MH_400_WITHHOLD": "1",
                "MH_BROWSER_PREFETCH": "1", "MH_BROWSER_FINISH_SAME_TURN": "1"}

from meshpool_harness import config as _config  # noqa: E402

ENGINES = ("research", "credit", "code", "data", "sheets", "slides", "forge", "general", "browser")


def _engine(name):
    import importlib
    return importlib.import_module(f"engines.{name}.engine")


def run_request(request: str, files: list[str], out: str, engine: str | None = None, workdir: str | None = None,
                client=None, output_format: str | None = None) -> dict:
    from meshpool_harness.core.client import LiveClient
    from meshpool_harness.router.router import route
    if not request or not request.strip():
        raise SystemExit("error: empty request")
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        raise SystemExit(f"error: file(s) not found: {', '.join(missing)}")
    if engine:
        bad = [e for e in engine.split(",") if e not in ENGINES]
        if bad:
            raise SystemExit(f"error: unknown engine(s) {bad}; choose from {', '.join(ENGINES)}")
    if not (os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("MH_PROVIDER") == "chatgpt") and client is None:
        raise SystemExit(f"error: no API key. Put MESHPOOL_API_KEY=... in {_config.config_path()}")
    client = client or LiveClient("phase_2_build")
    task_id = "req-" + uuid.uuid4().hex[:8]
    r = route(request, files, output_format, workdir, client=client, engine=engine, task_id=task_id)
    outdir = Path(out).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    inputs = [str(Path(f).resolve()) for f in files]
    t0, results = time.time(), []
    researched = False
    for i, name in enumerate(r.engines):
        wd = workdir if (name == "code" and workdir) else str(outdir)
        for f in inputs:                         # engines see inputs inside their workdir
            dst = Path(wd) / Path(f).name
            if not dst.exists() and Path(f).parent != Path(wd):
                shutil.copy(f, dst)
        task = request
        if inputs:
            task += "\n\nInput files (in the working directory): " + ", ".join(Path(f).name for f in inputs)
        if results:
            prev = results[-1]
            task += (f"\n\nOutput of the previous step ({r.engines[i - 1]}), use it as your source:\n"
                     f"{prev['answer']}\nFiles: {', '.join(prev.get('artifacts', []))}")
        if len(r.engines) > 1:
            task += f"\n\nYou are step {i + 1} of {len(r.engines)} ({' -> '.join(r.engines)}); do only the {name} part."
        kw = {"files": inputs} if name in ("data", "sheets", "research", "credit") else {}
        if name == "slides" and (researched or request.lstrip().startswith("[add to deck:")):
            kw = {"files": inputs}                   # research facts, or the user's own deck (by its real path)
        if os.environ.get("MH_MODEL"):           # the model chosen in settings, for every engine
            kw["model"] = os.environ["MH_MODEL"]
        saved = dict(os.environ)
        if name in ("code", "browser"):
            for k, v in ENGINE_FLAGS.items():     # explicit MH_* settings win
                os.environ.setdefault(k, v)
        try:
            res = _engine(name).solve(task, workdir=wd, task_id=f"{task_id}-{name}", client=client, **kw)
        except Exception as e:   # one clean message instead of a traceback (kept, short, for whoever debugs it)
            import traceback
            res = {"ok": False, "answer": "", "reason": f"{type(e).__name__}: {e}", "artifacts": [],
                   "trace": traceback.format_exc()[-3000:]}
        finally:
            os.environ.clear()
            os.environ.update(saved)              # office engines keep their own tested settings
        res["engine"] = name
        results.append(res)
        if not res.get("ok"):
            break
        if name == "research":                       # later steps read the sourced report; sources.json stays in
            rep = Path(wd) / "report.md"             # the folder for assumption sources (R.Corpus(".").assumption_source)
            if rep.exists() and str(rep) not in inputs:
                inputs.append(str(rep))
                researched = True
    spent = client.spent() if hasattr(client, "spent") else None
    return {"task_id": task_id, "route": {"engines": r.engines, "confidence": r.confidence, "reason": r.reason,
                                         "used_model_call": r.used_model_call},
            "ok": all(x.get("ok") for x in results) and len(results) == len(r.engines),
            "steps": [{k: v for k, v in x.items() if k in ("engine", "ok", "answer", "artifacts", "evidence", "turns",
                                                          "reason", "usage")} for x in results],
            "out_dir": str(outdir), "wall_s": round(time.time() - t0, 1), "key_spend_since_start": spent}


COMMANDS = {"run-due", "scheduler", "route", "run", "work", "ui", "check", "install-browser", "agent", "mcp", "app", "acp", "login", "setup", "import", "connect", "disconnect", "trust", "worktree"}


def _agent_parser():
    ap = argparse.ArgumentParser(
        prog="meshpool", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Meshpool coding agent. Run it in a project folder: `meshpool` for a session, "
                    "`meshpool -p \"...\"` for one answer.",
        epilog="other commands (meshpool <command> -h for options):\n"
               "  setup            the first-run walkthrough: key, folder, permissions, your skills\n"
               "  import           bring your old chats and sessions (ChatGPT, Claude, Claude Code, Codex) and context\n"
               "  login            save your Meshpool API key\n"
               "  check            show settings and installed tools (--live sends one tiny model call)\n"
               "  connect          connect apps: Notion, Gmail, Slack, HubSpot... or any MCP server by URL\n"
               "  mcp              list, test and switch MCP servers\n"
               "  acp              serve the agent over the Agent Client Protocol (Zed, JetBrains, other apps)\n"
               "  work             fetch a task from GitHub, Jira, a URL or a file, run it, report back\n"
               "  run / route      run or preview Meshpool's task engines (decks, spreadsheets, data, code)\n"
               "  ui / app         open the Meshpool web app or desktop app\n"
               "  install-browser  install the optional browser engine")
    ap.add_argument("request", nargs="?", help="first request for an interactive session")
    ap.add_argument("-p", "--print", dest="prompt", help="answer one request, print the reply and exit")
    ap.add_argument("--dir", help="project folder (default: the current folder)")
    ap.add_argument("--auto", action="store_true", help="allow edits and commands without asking")
    ap.add_argument("--json", dest="json_out", action="store_true", help="with -p: print one JSON object (result, usage, session id) for scripts and CI")
    ap.add_argument("--output-format", choices=["text", "json", "stream-json"], help="with -p: text (default), json, or stream-json (one event per line as it happens)")
    ap.add_argument("--permission-mode", choices=["ask", "edits", "auto", "plan"], help="ask (default), edits (accept file changes), auto (everything), plan (read only, then a plan)")
    ap.add_argument("--append-system-prompt", help="extra instructions added to the agent's system prompt for this run")
    ap.add_argument("--max-turns", type=int, help="stop after this many tool steps in one request")
    ap.add_argument("--allowed-tools", help="comma-separated tools or bash:pattern rules that never ask, e.g. edit,bash:npm test*")
    ap.add_argument("--disallowed-tools", help="comma-separated tools or bash:pattern rules that are always refused")
    ap.add_argument("-c", "--continue", dest="resume", action="store_true", help="resume the last session here")
    ap.add_argument("--resume", dest="session", help="resume a session by id")
    ap.add_argument("--model", help="model id for this session")
    ap.add_argument("--no-mcp", action="store_true", help="do not start MCP servers")
    ap.add_argument("--profile", choices=["code", "work"], help="code (default): a coding agent; work: Cowork, for documents, research and connected apps")
    ap.add_argument("-w", "--worktree", nargs="?", const="", default=None, metavar="NAME",
                    help="work in a new git worktree of this repo (.meshpool/worktrees/NAME on branch meshpool/NAME)")
    ap.add_argument("--effort", choices=["auto", "low", "medium", "high"], help="how hard the model thinks (auto: Meshpool decides)")
    from meshpool_harness import __version__
    ap.add_argument("-v", "--version", action="version", version=f"meshpool {__version__}")
    return ap


def _stdin_has_data(explicit: bool) -> bool:
    """Piped input is read when asked for with `-p -`, or when a pipe or file already has data waiting. A terminal, a
    closed stdin or an open pipe nobody writes to is never waited on."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return False
        if explicit:
            return True
        import select
        import stat
        mode = os.fstat(sys.stdin.fileno()).st_mode
        if stat.S_ISREG(mode):
            return True
        return stat.S_ISFIFO(mode) and bool(select.select([sys.stdin], [], [], 0.3)[0])
    except Exception:  # noqa: BLE001  (pytest's captured stdin, Windows pipes)
        return False


def _no_console() -> bool:
    """Windows: this process has no console (the app, or the app restarted after an update)."""
    try:
        import ctypes
        return not ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:  # noqa: BLE001
        return False


def _worktree_cli(args: list) -> int:
    from meshpool_harness import worktrees as WT
    ap = argparse.ArgumentParser(prog="meshpool worktree", description="Git worktrees Meshpool makes for tasks.")
    ap.add_argument("action", choices=["list", "add", "remove"])
    ap.add_argument("name", nargs="?", default="")
    ap.add_argument("--force", action="store_true", help="remove even with uncommitted changes (commits stay on the branch)")
    a = ap.parse_args(args)
    try:
        if a.action == "list":
            rows = WT.listing(os.getcwd())
            for w in rows:
                flags = [f for f, on in (("uncommitted changes", w.get("dirty")),
                                         (f"{w.get('unpushed')} unpushed", w.get("unpushed"))) if on]
                print(f"{w['name']:<20} {w['branch']:<28} {', '.join(flags) or 'clean'}")
            if not rows:
                print("No worktrees. Start one with: meshpool -w [name]")
        elif a.action == "add":
            w = WT.create(os.getcwd(), a.name)
            print(f"{w['path']} (branch {w['branch']})")
        else:
            if not a.name:
                print("Usage: meshpool worktree remove NAME [--force]")
                return 1
            root = WT.repo_root(os.getcwd())
            r = WT.remove(root / WT.DIR / a.name, force=a.force)
            print(f"Removed {r['removed']}" + ("" if r["branch_deleted"] else f"; branch {r['branch']} kept"))
    except WT.WorktreeError as e:
        print(f"meshpool: {e}", file=sys.stderr)
        return 1
    return 0


def _trust_cli(args: list) -> int:
    from meshpool_harness import trust as TR
    ap = argparse.ArgumentParser(prog="meshpool trust", description="Let a folder's own hooks and allow rules "
                                 "(.meshpool/hooks.json, .meshpool/permissions.json) run. Folders are not trusted "
                                 "until you say so.")
    ap.add_argument("path", nargs="?", default=".", help="the folder (default: this one)")
    ap.add_argument("--revoke", action="store_true", help="stop trusting the folder")
    ap.add_argument("--list", action="store_true", help="list trusted folders")
    a = ap.parse_args(args)
    if a.list:
        for f in TR.folders():
            print(f)
        return 0
    root = Path(a.path).expanduser().resolve()
    if not root.is_dir():
        print(f"Folder not found: {root}")
        return 1
    if a.revoke:
        print(f"No longer trusted: {root}" if TR.revoke(root) else f"{root} was not trusted.")
        return 0
    TR.trust(root)
    risky = TR.risky_files(root)
    print(f"Trusted: {root}" + (f" (its {' and '.join(risky)} now apply)" if risky else ""))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.environ.pop("MESHPOOL_RESTART_WAIT", None):     # started by "Restart" after an update: let the old app close
        import time
        time.sleep(3)
    if os.name == "nt" and (sys.executable.lower().endswith("pythonw.exe") or _no_console()):
        # The Windows app runs windowless under pythonw.exe, whose children get no usable stdin/stdout. Launch child
        # Pythons (Hermes, MCP servers, engines) with python.exe; their console windows are suppressed.
        from meshpool_harness.ui.desktop import _hide_child_consoles
        _hide_child_consoles()
        pe = Path(sys.executable).with_name("python.exe")
        if pe.exists():
            sys.executable = str(pe)
    if argv[:1] == ["app"]:              # the desktop app (Meshpool.app / Meshpool.exe)
        from meshpool_harness.ui.desktop import run as run_app
        return run_app()
    if os.environ.get("MESHPOOL_HOME"):
        _config.ensure_home()
    _config.load_into_env()          # before any engine import: engines read MH_* at import time
    try:
        from meshpool_harness.ui.desktop import _bundled_tools
        _bundled_tools()             # the shell, Git and browser engine that ship inside the app
    except Exception:
        pass
    if not argv or argv[0] not in COMMANDS:
        if argv and argv[0] == "agent":
            argv = argv[1:]
        a = _agent_parser().parse_args(argv)
        if a.model:
            os.environ["MH_MODEL"] = a.model
        from meshpool_harness.agent.tui import main as agent_main
        prompt = a.prompt
        if prompt is not None and _stdin_has_data(prompt == "-"):   # cat notes.txt | meshpool -p "summarise this"
            piped = sys.stdin.read()
            prompt = (prompt + "\n\n" + piped) if prompt not in ("", "-") else piped
        if a.profile:
            os.environ["MH_AGENT_PROFILE"] = a.profile
        if a.max_turns:
            os.environ["MH_MAX_STEPS"] = str(a.max_turns)
        if a.effort:
            os.environ["MH_EFFORT"] = a.effort
        if a.worktree is not None:
            from meshpool_harness import worktrees as WT
            try:
                w = WT.create(a.dir or os.getcwd(), a.worktree)
            except WT.WorktreeError as e:
                print(f"meshpool: {e}", file=sys.stderr)
                return 1
            print(f"Working in {w['path']} on branch {w['branch']}" + (
                f" (copied {', '.join(w['copied'])})" if w["copied"] else ""), file=sys.stderr)
            a.dir = w["path"]
        if a.append_system_prompt:
            os.environ["MH_APPEND_PROMPT"] = a.append_system_prompt
        if a.permission_mode:
            os.environ["MH_PERMISSION_MODE"] = a.permission_mode
            if a.permission_mode == "auto":
                a.auto = True
        if a.allowed_tools:
            os.environ["MH_ALLOW"] = a.allowed_tools
        if a.disallowed_tools:
            os.environ["MH_DENY"] = a.disallowed_tools
        fmt = a.output_format or ("json" if a.json_out else "")
        return agent_main(a.dir, prompt, a.auto, a.resume, a.session, a.no_mcp, a.request, json_out=fmt if fmt in ("json", "stream-json") else False)
    if argv[0] == "agent":
        return main(argv[1:] or [])
    if argv[0] == "acp":
        ap = argparse.ArgumentParser(prog="meshpool acp", description="Serve the Meshpool agent over ACP on stdio.")
        ap.add_argument("--profile", choices=["code", "work"], default="code")
        ap.add_argument("--auto", action="store_true", help="allow edits and commands without asking the client")
        a = ap.parse_args(argv[1:])
        from meshpool_harness.agent.acp_server import main as acp_main
        return acp_main(a.profile, a.auto)
    if argv[0] == "setup":
        from meshpool_harness.agent import onboard
        from meshpool_harness.agent.tui import main as agent_main
        folder = onboard.run()
        if folder is None:
            return 1
        return agent_main(str(folder), onboarded=True)
    if argv[0] == "connect":
        from meshpool_harness.agent import connect_cli
        return connect_cli.main(argv[1:])
    if argv[0] == "disconnect":
        from meshpool_harness.agent import connect_cli
        if len(argv) < 2:
            print("Usage: meshpool disconnect <name>")
            return 1
        return connect_cli.disconnect(argv[1])
    if argv[0] == "trust":                     # meshpool trust [path] [--revoke] [--list]
        return _trust_cli(argv[1:])
    if argv[0] == "worktree":                  # meshpool worktree list | add [name] | remove NAME [--force]
        return _worktree_cli(argv[1:])
    if argv[0] == "run-due":
        from meshpool_harness import scheduler_os as SO
        print(f"ran {SO.run_due()} scheduled task(s)", flush=True)
        os._exit(0)                              # agent helper threads and servers must not keep a background job alive
    if argv[0] == "scheduler" and len(argv) > 1 and argv[1] in ("install", "uninstall"):
        from meshpool_harness import scheduler_os as SO
        try:
            print(SO.install() if argv[1] == "install" else SO.uninstall())
        except RuntimeError as e:
            print(str(e))
            return 1
        return 0
    if argv[0] == "import" and len(argv) > 1 and argv[1] == "agents":
        # meshpool import agents [--dry-run] [--yes] [--from claude,codex]: skills, tools, memories and instructions
        from meshpool_harness import agent_import as AI
        return AI.cli(argv[2:])
    if argv[0] == "import":
        from meshpool_harness.agent import onboard
        from meshpool_harness.agent.tui import Style, _enable_ansi
        if len(argv) > 1:                         # meshpool import export.zip | claude-code | codex
            from meshpool_harness import chat_import as CI
            what = argv[1]
            if what in ("claude-code", "codex"):
                r = CI.import_local([what])
                print(f"Imported {r['imported']} {what} sessions as chats ({r['skipped']} were already here).")
                return 0
            r = CI.import_export(os.path.expanduser(what))
            print(f"{'Claude' if r['kind'] == 'claude' else 'ChatGPT'} export: {r['imported']} chats "
                  f"({r['skipped']} were already here), {len(r['skills'])} project skills, {r['memory']} facts into "
                  f"memory, {r['instructions']} instructions.")
            return 0
        return 0 if onboard.context_step(Style(_enable_ansi())) else 1
    if argv[0] == "login":
        from meshpool_harness.agent.tui import ask_key
        return 0 if ask_key(force=True) else 1
    if argv[0] == "mcp":
        from meshpool_harness.agent.mcp import Manager, config_file
        from meshpool_harness.agent.tui import Terminal, _mcp_status
        print("Starting MCP servers...")
        m = Manager(roots=[os.getcwd()]).start()
        try:
            _mcp_status(Terminal(), m)
        finally:
            m.close()
        return 0 if all(s["status"] != "error" for s in m.status()) and not m.config_error else 1
    ap = argparse.ArgumentParser(prog="meshpool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("route", "run"):
        p = sub.add_parser(c)
        p.add_argument("request")
        p.add_argument("--files", nargs="*", default=[])
        p.add_argument("--engine")
        p.add_argument("--workdir")
        p.add_argument("--format", dest="output_format")
        if c == "run":
            p.add_argument("--out", default="./mh_out")
    w = sub.add_parser("work", help="fetch a task from a connector (github:, jira:, http:, file:, custom), run it, report back")
    w.add_argument("source", help="e.g. github:owner/repo#12, jira:PROJ-7, http:https://..., file:task.md")
    w.add_argument("--workdir", default=".")
    w.add_argument("--engine", default=None)
    w.add_argument("--out", default="./mh_out")
    w.add_argument("--report", action="store_true", help="post the outcome back to the source")
    u = sub.add_parser("ui", help="open the Meshpool web app")
    u.add_argument("--port", type=int, default=int(os.environ.get("MH_UI_PORT", "8765")))
    u.add_argument("--no-browser", action="store_true")
    ck = sub.add_parser("check", help="show settings and installed tools; --live sends one tiny model call")
    ck.add_argument("--live", action="store_true")
    sub.add_parser("install-browser", help="install Playwright and Chromium for the browser engine")
    a = ap.parse_args(argv)
    if a.cmd == "ui":
        from meshpool_harness.ui.server import serve
        return serve(a.port, open_browser=not a.no_browser)
    if a.cmd == "check":
        return _check(a.live)
    if a.cmd == "install-browser":
        import subprocess
        from meshpool_harness import browser_use as BU
        r = subprocess.run([sys.executable, "-m", "pip", "install", "playwright>=1.40"])
        have = BU.find_playwright_chromium()
        if r.returncode == 0 and have:
            print(f"browser engine ready (using the Chromium already at {have})")
            return 0
        if r.returncode == 0:
            r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
        print("browser engine ready" if r.returncode == 0 else "browser install failed (see above)")
        return r.returncode
    if a.cmd == "work":
        from meshpool_harness.connectors import get
        conn, ref = get(a.source)
        task = conn.fetch(ref)
        res = run_request(task.text(), [], a.out, a.engine, a.workdir)
        if a.report:
            conn.report(task, res)
        print(json.dumps({"task": task.ref, "source": task.source, **{k: res.get(k) for k in ("ok", "answer")}},
                         indent=1, default=str))
        return 0 if res.get("ok") else 1
    if a.cmd == "route":
        from meshpool_harness.router.rules import decide
        eng, conf, why = decide(a.request, a.files, a.output_format, a.workdir)
        print(json.dumps({"engines": eng, "confidence": conf, "reason": why}, indent=1))
        return 0
    res = run_request(a.request, a.files, a.out, a.engine, a.workdir, output_format=a.output_format)
    print(json.dumps(res, indent=1))
    return 0 if res["ok"] else 1


def ping(model: str | None = None, base: str = "", key: str = "", kind: str = "", api_version: str = "") -> dict:
    """One tiny model call with the current settings (or `model`, and a `base` URL and `key` not saved yet).
    Returns {ok, detail, model, seconds, endpoint}."""
    from meshpool_harness.core.wire import SimpleLedger
    model = model or os.environ.get("MH_MODEL", "deepseek-v4.1-flash")
    t0 = time.time()
    led = None
    try:
        led = SimpleLedger("check")
        if base:
            led.base_url = base.rstrip("/")
        if key:
            led.api_key = key
        led.kind, led.api_version = kind, api_version    # an Azure kind or api-version typed but not saved yet
        r = led.chat(model=model, messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                     max_tokens=5, retries=1, timeout=60)
        txt = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {"ok": True, "detail": f"model replied: {txt.strip()[:40]!r}", "model": model,
                "seconds": round(time.time() - t0, 1), "endpoint": led.base_url}
    except Exception as e:
        import re
        msg = re.sub(r"^meshpool HTTP \d+: ", "", str(e))     # wire.py already explains the status in plain words
        return {"ok": False, "detail": msg[:400], "model": model, "seconds": round(time.time() - t0, 1),
                "endpoint": led.base_url if led else (base or os.environ.get("MESHPOOL_BASE_URL", ""))}


def _check(live: bool) -> int:
    from meshpool_harness import harnesses as HR
    from meshpool_harness import providers as PV
    key = os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    # A ChatGPT sign-in is enough on its own: report what is ready rather than calling the setup broken when there
    # is no Meshpool key.
    ready = {p: _safe(lambda p=p: PV.ready(p)) for p in PV.PROVIDERS}
    usable = _safe(HR.ready)
    print(f"Settings file : {_config.config_path()}")
    print(f"Endpoint      : {os.environ.get('MESHPOOL_BASE_URL', 'https://llm.meshpool.ai/v1')}")
    print(f"Model         : {os.environ.get('MH_MODEL', 'deepseek-v4.1-flash')}")
    print(f"API key       : {'set (ends ' + key[-4:] + ')' if key else 'not set'}")
    names = [_safe(lambda p=p: PV.name(p), p) for p, ok in ready.items() if ok]
    print(f"Ready to use  : {', '.join(names) if names else 'nothing yet (run: meshpool setup)'}")
    print()
    for row in _config.system_check():
        print(f"  [{'ok' if row['ok'] else '--'}] {row['name']:<15} {row['detail']}")
        if not row["ok"]:
            print(f"       {row['fix']}")
    if live:
        print()
        if not usable:
            print("Live test skipped: no API key or signed-in subscription.")
            return 1
        r = ping()
        print(("Live test OK: " if r["ok"] else "Live test FAILED: ") + r["detail"] + f" ({r['seconds']} s)")
        return 0 if r["ok"] else 1
    return 0 if usable else 1


def _safe(fn, default=False):
    try:
        return fn()
    except Exception:  # noqa: BLE001  (a check must report, not crash)
        return default


if __name__ == "__main__":
    sys.exit(main())
