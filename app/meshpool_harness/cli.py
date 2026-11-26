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
import re
import json
import os
import shutil
import sys
import time
import uuid
import zipfile
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
                client=None, output_format: str | None = None, model: str | None = None) -> dict:
    from meshpool_harness import telemetry
    with telemetry.span("meshpool.run", {"meshpool.engine": engine or ""}):
        return _run_request(request, files, out, engine, workdir, client, output_format, model)


def _endpoint_ready() -> bool:
    """The check -p uses: a key paired with its endpoint (or a keyless local server), or a ChatGPT sign-in. An
    OPENAI_API_KEY without its OPENAI_BASE_URL is not a key for any endpoint here."""
    from meshpool_harness import providers as PV
    try:
        return bool(PV.ready("key") or PV.ready("chatgpt"))
    except Exception:  # noqa: BLE001 - a broken sign-in file is not a key
        return bool(PV.ready("key"))


def _run_request(request: str, files: list[str], out: str, engine: str | None = None, workdir: str | None = None,
                 client=None, output_format: str | None = None, model: str | None = None) -> dict:
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
    if client is None and not _endpoint_ready():
        raise SystemExit(f"error: no API key. Put MESHPOOL_API_KEY=... in {_config.config_path()}")
    client = client or LiveClient("phase_2_build")
    task_id = "req-" + uuid.uuid4().hex[:8]
    model = model or os.environ.get("MH_MODEL")      # the model the caller (or the app chat) picked
    r = route(request, files, output_format, workdir, client=client, engine=engine, model=model, task_id=task_id)
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
        if model:                                # the model chosen in settings or in the chat, for every engine
            kw["model"] = model
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
    arts = list(dict.fromkeys(a for x in results for a in (x.get("artifacts") or [])))
    bad = next((x for x in results if not x.get("ok")), None)
    return {"answer": (results[-1].get("answer") or "") if results else "", "artifacts": arts,
            "reason": (bad.get("reason") or "") if bad else "", "task_id": task_id, "route": {"engines": r.engines, "confidence": r.confidence, "reason": r.reason,
                                         "used_model_call": r.used_model_call},
            "ok": all(x.get("ok") for x in results) and len(results) == len(r.engines),
            "steps": [{k: v for k, v in x.items() if k in ("engine", "ok", "answer", "artifacts", "evidence", "turns",
                                                          "reason", "usage")} for x in results],
            "out_dir": str(outdir), "wall_s": round(time.time() - t0, 1), "key_spend_since_start": spent}


COMMANDS = {"run-due", "scheduler", "route", "run", "work", "ui", "check", "install-browser", "agent", "mcp", "app",
            "acp", "login", "setup", "import", "connect", "disconnect", "worktree", "permissions", "chats",
            "projects", "memory", "skills", "plugins", "schedule", "agents", "models", "usage", "settings", "help",
            "trust", "office", "completion", "doctor"}


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
               "  doctor           find problems in settings, hooks, rules, MCP servers, skills and sub-agents\n"
               "  completion       print a shell completion script (bash, zsh or fish)\n"
               "  connect          connect apps: Notion, Gmail, Slack, HubSpot... or any MCP server by URL\n"
               "  mcp              list, test and switch MCP servers\n"
               "  acp              serve the agent over the Agent Client Protocol (Zed, JetBrains, other apps)\n"
               "  work             fetch a task from GitHub, Jira, a URL or a file, run it, report back\n"
               "  run / route      run or preview Meshpool's task engines (decks, spreadsheets, data, code)\n"
               "  worktree         isolated copies: list, diff, merge or discard (see --worktree)\n"
               "  ui / app         open the Meshpool web app or desktop app\n"
               "  install-browser  install the optional browser engine")
    ap.add_argument("request", nargs="?", help="first request for an interactive session")
    ap.add_argument("-p", "--print", dest="prompt", help="answer one request, print the reply and exit")
    ap.add_argument("--dir", help="project folder (default: the current folder)")
    ap.add_argument("--auto", action="store_true", help="allow edits and commands without asking")
    ap.add_argument("--json", dest="json_out", action="store_true", help="with -p: print one JSON object (result, usage, session id) for scripts and CI")
    ap.add_argument("--output-format", choices=["text", "json", "stream-json"], help="with -p: text (default), json, or stream-json (one event per line as it happens)")
    ap.add_argument("--permission-mode", type=lambda v: v if v == "plan" else (_config.normalize_mode(v, None) or v), choices=["ask", "edits", "auto", "plan"], help="ask (default), accept-edits (accept file changes), full-access (everything), plan (read only, then a plan). Old names (edits, auto, manual, supervised) still work")
    ap.add_argument("--sandbox", action="store_true", help="run the agent's commands in the sandbox (on by default except in Full access): they can write only in the project and temporary folders")
    ap.add_argument("--no-network", dest="no_network", action="store_true", help="also cut the network for sandboxed commands")
    ap.add_argument("--append-system-prompt", help="extra instructions added to the agent's system prompt for this run")
    ap.add_argument("--append-system-prompt-file", metavar="FILE", help="like --append-system-prompt, read from a file")
    ap.add_argument("--system-prompt", help="replace meshpool's own instructions for this run (the rule that tool "
                                             "output is data, not instructions, is always kept)")
    ap.add_argument("--system-prompt-file", metavar="FILE", help="like --system-prompt, read from a file")
    ap.add_argument("--max-turns", "--max-steps", dest="max_turns", type=int, metavar="N",
                    help="stop after this many tool steps in one reply (default 200; 0 = no limit)")
    ap.add_argument("--allowed-tools", help="comma-separated tools or bash:pattern rules that never ask, e.g. edit,bash:npm test*")
    ap.add_argument("--disallowed-tools", help="comma-separated tools or bash:pattern rules that are always refused")
    ap.add_argument("-c", "--continue", dest="resume", action="store_true", help="resume the last session here")
    ap.add_argument("--resume", dest="session", help="resume a session by id")
    ap.add_argument("--fork-session", action="store_true", help="with -c or --resume: continue in a new session, "
                                                                 "leaving the old one as it was")
    ap.add_argument("--session-id", help="the id for a new session (letters, digits, - and _)")
    ap.add_argument("--no-session-persistence", action="store_true", help="with -p: do not save this session")
    ap.add_argument("--model", help="model id for this session")
    ap.add_argument("--effort", choices=["off", "low", "medium", "high", "xhigh"],
                    help="how hard the model thinks (setting MH_REASONING_EFFORT; /effort in a session)")
    ap.add_argument("--no-mcp", action="store_true", help="do not start MCP servers")
    ap.add_argument("--mode", choices=["code", "work", "auto"], help="code (default): a coding agent; work: Cowork, for documents, research and connected apps; auto: pick the agent for the request")
    ap.add_argument("--profile", choices=["code", "work"], help=argparse.SUPPRESS)      # the old name of --mode
    ap.add_argument("--agent", dest="agent_id", help="the agent to run (an id from `meshpool agents`, e.g. meshpool-code, meshpool-work, codex)")
    ap.add_argument("--project", help="a project id: its instructions, memory and folder apply")
    ap.add_argument("--incognito", action="store_true", help="no memory is read or written and nothing is learned from this chat")
    ap.add_argument("--attach", action="append", default=[], metavar="PATH", help="a file or picture to send with the request (repeatable)")
    ap.add_argument("--output-last-message", metavar="FILE", help="with -p: also write the final reply to FILE")
    ap.add_argument("-w", "--worktree", action="store_true", help="work in an isolated copy of the folder (a git worktree on its own branch; a plain copy without git), so other sessions and chats are not disturbed; merge it back with `meshpool worktree merge <id>`")
    ap.add_argument("-n", "--name", help="a name for this session (shown in /resume and the app)")
    ap.add_argument("--mcp-config", help="MCP servers for this run only: a JSON file, or JSON text, with mcpServers")
    ap.add_argument("--strict-mcp-config", action="store_true", help="use only the servers from --mcp-config, not "
                                                                      "your saved ones or the project's")
    ap.add_argument("--settings", metavar="FILE", help="meshpool settings for this run: a JSON object or KEY=VALUE "
                                                      "lines (MH_ and MESHPOOL_ keys)")
    ap.add_argument("--disable-slash-commands", action="store_true", help="no custom commands, server prompts or "
                                                                           "skills for this run")
    from meshpool_harness import __version__
    ap.add_argument("--subagent", metavar="NAME", help="work as one of your saved sub-agents (its instructions, tools and model)")
    return ap


def _settings_file(path) -> dict:
    """{KEY: value} from a JSON object or KEY=VALUE lines; only meshpool's own MH_ and MESHPOOL_ keys."""
    import json
    # read the way the settings file itself is read: a byte-order mark or UTF-16 is fine, and KEY="value" loses its
    # quotes, so a line copied from meshpool.config means the same here
    text = _config.decode_settings(Path(path).expanduser().read_bytes())[0].replace("﻿", "")
    if text.lstrip().startswith("{"):
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError("the JSON must be an object")
        pairs = d.items()
    else:
        pairs = _config._parse(text).items()
    out = {}
    for k, v in pairs:
        k = str(k).strip()
        if not re.fullmatch(r"(MH|MESHPOOL)_[A-Z0-9_]+", k):
            raise ValueError(f"{k} is not a meshpool setting (they start with MH_ or MESHPOOL_)")
        out[k] = ("1" if v else "0") if isinstance(v, bool) else str(v).strip()
    return out


def _read_arg_file(path) -> str:
    if not path:
        return ""
    return Path(path).expanduser().read_text(encoding="utf-8", errors="replace").strip()


def completion(shell: str) -> str:
    """A shell completion script for meshpool: commands and the main options."""
    import argparse
    opts = sorted({o for act in _agent_parser()._actions for o in act.option_strings if o.startswith("--")})
    cmds = sorted(COMMANDS | {"completion"})
    if shell == "bash":
        return ("_meshpool() {\n"
                "  local cur=${COMP_WORDS[COMP_CWORD]}\n"
                f"  local cmds=\"{' '.join(cmds)}\"\n"
                f"  local opts=\"{' '.join(opts)}\"\n"
                "  if [[ $COMP_CWORD -eq 1 && $cur != -* ]]; then COMPREPLY=($(compgen -W \"$cmds\" -- \"$cur\"));\n"
                "  elif [[ $cur == -* ]]; then COMPREPLY=($(compgen -W \"$opts\" -- \"$cur\"));\n"
                "  else COMPREPLY=($(compgen -f -- \"$cur\")); fi\n"
                "}\ncomplete -F _meshpool meshpool\n")
    if shell == "zsh":
        return ("#compdef meshpool\n_meshpool() {\n"
                f"  local -a cmds; cmds=({' '.join(cmds)})\n"
                f"  local -a opts; opts=({' '.join(opts)})\n"
                "  if (( CURRENT == 2 )) && [[ $words[CURRENT] != -* ]]; then compadd -a cmds\n"
                "  elif [[ $words[CURRENT] == -* ]]; then compadd -a opts\n"
                "  else _files; fi\n}\ncompdef _meshpool meshpool\n")
    if shell == "fish":
        lines = [f"complete -c meshpool -n '__fish_use_subcommand' -a '{c}'" for c in cmds]
        lines += [f"complete -c meshpool -l {o[2:]}" for o in opts]
        return "\n".join(lines) + "\n"
    raise argparse.ArgumentTypeError("bash, zsh or fish")


def _worktree_session(a, prompt) -> dict | None:
    """--worktree: an isolated copy for this session; a.dir then points into it."""
    if not getattr(a, "worktree", False):
        return None
    from meshpool_harness import worktrees as WT
    if a.resume or a.session:
        raise SystemExit("error: --worktree starts a new session; it cannot be combined with -c or --resume")
    try:
        rec = WT.create(a.dir or os.getcwd(), owner_kind="cli", owner_id=str(os.getpid()),
                        title=(prompt or a.request or "terminal session").strip()[:80])
    except WT.WorktreeError as e:
        raise SystemExit(f"error: {e}")
    print(f"Working in an isolated copy ({rec['id']}): {rec['workdir']}", file=sys.stderr)
    a.dir = rec["workdir"]
    return rec


def _worktree_done(rec: dict | None) -> None:
    if not rec:
        return
    from meshpool_harness import worktrees as WT
    try:
        n = len(WT.diff(rec["id"], max_patch=0)["files"])
    except (WT.WorktreeError, KeyError, OSError):
        n = -1
    what = "No changes" if n == 0 else f"{n} file(s) changed" if n > 0 else "Changes"
    print(f"{what} in isolated copy {rec['id']}. Review: meshpool worktree diff {rec['id']}  |  bring them in: "
          f"meshpool worktree merge {rec['id']}  |  throw away: meshpool worktree discard {rec['id']}",
          file=sys.stderr)


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


def _mcp_add_json(args: list) -> int:
    import json
    from meshpool_harness.agent import mcp as M
    if len(args) != 2 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", args[0]):
        print("usage: meshpool mcp add-json NAME JSON   (JSON: {\"command\": ..., \"args\": [...]} or {\"url\": ...};"
              " - reads it from standard input)", file=sys.stderr)
        return 2
    raw = sys.stdin.read() if args[1] == "-" else args[1]
    try:
        spec = json.loads(raw)
    except ValueError as e:
        print(f"meshpool: not valid JSON: {e}", file=sys.stderr)
        return 2
    if isinstance(spec, dict) and isinstance(spec.get("mcpServers"), dict) and len(spec["mcpServers"]) == 1:
        spec = next(iter(spec["mcpServers"].values()))       # a whole snippet as a server's docs give it
    if not isinstance(spec, dict) or not (isinstance(spec.get("command"), str) or isinstance(spec.get("url"), str)):
        print("meshpool: the server needs a command or a url", file=sys.stderr)
        return 2
    if spec.get("args") is not None and not isinstance(spec["args"], list):
        print("meshpool: args must be a list", file=sys.stderr)
        return 2
    try:
        existed = M.edit_config(lambda s: (args[0] in s, s.__setitem__(args[0], spec))[0])
    except (OSError, ValueError) as e:
        print(f"meshpool: {e}", file=sys.stderr)
        return 1
    print(f"{'Replaced' if existed else 'Added'} the MCP server {args[0]} in {M.config_file()}.")
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


from meshpool_harness.cli_app import HELP as _HELP, NEW as _APP_COMMANDS  # noqa: E402  (one table for help)


def _permissions_cmd(argv) -> int:
    ap = argparse.ArgumentParser(prog="meshpool permissions", description="List or edit the saved allow/deny rules. "
                                 "A rule is a tool name (edit, mcp__github__*) or bash: and a command pattern (bash:npm test*).")
    ap.add_argument("action", nargs="?", choices=["list", "allow", "deny", "remove"], default="list")
    ap.add_argument("rule", nargs="?", help="the rule to add or remove")
    ap.add_argument("--project", action="store_true", help="use <dir>/.meshpool/permissions.json instead of the user file")
    ap.add_argument("--dir", default=".", help="the project folder (with --project, and for listing)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    from meshpool_harness.core import permrules as PR
    path = PR.project_path(a.dir) if a.project else PR.user_path()
    if a.action == "list":
        for label, p in (("user", PR.user_path()), ("project", PR.project_path(a.dir))):
            r = PR.list_rules(p)
            print(f"{label} rules ({p}):")
            for k in ("allow", "deny"):
                for x in r[k]:
                    print(f"  {k:5} {x}")
            if not (r["allow"] or r["deny"]):
                print("  (none)")
        return 0
    if not a.rule:
        print(f"Usage: meshpool permissions {a.action} RULE [--project] [--dir DIR]", file=sys.stderr)
        return 2
    try:
        if a.action == "remove":
            PR.remove_rule(path, "allow", a.rule)
            PR.remove_rule(path, "deny", a.rule)
        else:
            PR.add_rule(path, a.action, a.rule)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"{a.action}: {a.rule} ({path})")
    return 0


def main(argv=None):
    """The command line. Anything that goes wrong comes out as one `error: ...` line and exit code 1, never a
    traceback (F-10); MH_DEBUG=1 keeps the traceback. Ctrl+C and sys.exit pass through."""
    from meshpool_harness.core.tools import exit_cleanly_on_sigterm
    exit_cleanly_on_sigterm()                   # the console script enters here, not through __main__
    try:
        return _main(argv)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        if os.environ.get("MH_DEBUG") == "1":
            raise
        from meshpool_harness.errors import plain
        expected = isinstance(e, (ValueError, KeyError, OSError, zipfile.BadZipFile, RuntimeError))
        line = f"error: {plain(e)}"
        if not expected:
            try:
                import traceback
                log = _config.runs_dir() / "logs" / "cli-error.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as f:
                    f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(sys.argv[1:])}\n"
                            + traceback.format_exc() + "\n")
                line += f" (details: {log})"
            except OSError:
                pass
        print(line, file=sys.stderr)
        return 1


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _HELP and any(x in ("-h", "--help") for x in argv[1:]):
        print(_HELP[argv[0]])                              # help never runs the command
        return 0
    _rw = os.environ.pop("MESHPOOL_RESTART_WAIT", None)
    if _rw:                                               # started by "Restart" after an update: let the old app close
        import time
        if _rw.isdigit() and int(_rw) > 1:                # the old app's PID: wait until it has really exited
            try:
                from meshpool_harness import updater as _up
                _up.wait_for_exit(int(_rw), 120)
            except Exception:  # noqa: BLE001
                time.sleep(3)
        else:
            time.sleep(3)
        os.environ["MESHPOOL_RESTARTED"] = "1"
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
    from meshpool_harness.agent import tui_render as _R
    _R.prepare_streams()             # OSS-30: a cp1252 console or a redirected stdout must not crash on glyphs
    _config.ensure_home()
    _config.load_into_env()          # before any engine import: engines read MH_* at import time
    try:
        from meshpool_harness.ui.desktop import _bundled_tools
        _bundled_tools()             # the shell, Git and browser engine that ship inside the app
    except Exception:
        pass
    if argv and argv[0] in ("--version", "-V", "-v"):
        from meshpool_harness import __version__
        print(f"meshpool {__version__}")
        return 0
    if not argv or argv[0] not in COMMANDS:
        if argv and argv[0] == "agent":
            argv = argv[1:]
        a = _agent_parser().parse_args(argv)
        if a.settings:                       # first, so the flags below still win
            try:
                for k, v in _settings_file(a.settings).items():
                    os.environ[k] = v
            except (OSError, ValueError) as e:
                print(f"meshpool: --settings: {e}", file=sys.stderr)
                return 2
        if a.model:
            os.environ["MH_MODEL"] = a.model
        if a.strict_mcp_config:
            os.environ["MH_STRICT_MCP"] = "1"
        if a.subagent:
            os.environ["MH_START_AGENT"] = a.subagent
        if a.disable_slash_commands:
            os.environ["MH_NO_COMMANDS"] = "1"
        if os.environ.get("MH_EFFORT") and not os.environ.get("MH_REASONING_EFFORT"):
            os.environ["MH_REASONING_EFFORT"] = os.environ["MH_EFFORT"]      # the old name, kept as an alias
        if getattr(a, "effort", None):
            os.environ["MH_REASONING_EFFORT"] = a.effort
        from meshpool_harness.agent.tui import main as agent_main
        prompt = a.prompt
        if prompt is not None and _stdin_has_data(prompt == "-"):   # cat notes.txt | meshpool -p "summarise this"
            piped = sys.stdin.read()
            prompt = (prompt + "\n\n" + piped) if prompt not in ("", "-") else piped
        if prompt is not None and not prompt.strip():
            print("error: -p needs a request (an empty one was given)", file=sys.stderr)
            return 2
        mode = a.mode or a.profile
        if mode in ("code", "work"):
            os.environ["MH_AGENT_PROFILE"] = mode
        if a.max_turns is not None:
            os.environ["MH_MAX_STEPS"] = str(a.max_turns)
        if a.name:
            os.environ["MH_SESSION_NAME"] = a.name.strip()[:80]
        if a.mcp_config:
            os.environ["MH_MCP_CONFIG"] = a.mcp_config
        try:
            extra = "\n\n".join(x for x in (a.append_system_prompt, _read_arg_file(a.append_system_prompt_file)) if x)
            own = a.system_prompt or _read_arg_file(a.system_prompt_file)
        except OSError as e:
            print(f"meshpool: {e}", file=sys.stderr)
            return 2
        if extra:
            os.environ["MH_APPEND_PROMPT"] = extra
        if own:
            os.environ["MH_SYSTEM_PROMPT"] = own
        if a.session_id:
            from meshpool_harness.agent import core as K
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", a.session_id) or a.session_id.startswith("sub-"):
                print("meshpool: --session-id takes letters, digits, - and _ (up to 80)", file=sys.stderr)
                return 2
            if (K.SESSIONS / a.session_id / "session.json").exists():
                print(f"meshpool: a session with the id {a.session_id} already exists; use --resume", file=sys.stderr)
                return 2
            if (a.resume or a.session) and not a.fork_session:
                print("meshpool: --session-id names a new session; add --fork-session to resume into it",
                      file=sys.stderr)
                return 2
            os.environ["MH_SESSION_ID"] = a.session_id
        if a.fork_session:
            if not (a.resume or a.session):
                print("meshpool: --fork-session goes with -c or --resume", file=sys.stderr)
                return 2
            os.environ["MH_FORK_SESSION"] = "1"
        if a.no_session_persistence:
            if prompt is None:
                print("meshpool: --no-session-persistence goes with -p", file=sys.stderr)
                return 2
            os.environ["MH_NO_PERSIST"] = "1"
        if getattr(a, "sandbox", False):
            os.environ["MH_SANDBOX"] = "1"
        if getattr(a, "no_network", False):
            os.environ["MH_SANDBOX"] = "1"
            os.environ["MH_SANDBOX_NETWORK"] = "0"
        if a.permission_mode:
            os.environ["MH_PERMISSION_MODE"] = a.permission_mode
            if a.permission_mode == "auto":
                a.auto = True
        if a.allowed_tools:
            os.environ["MH_ALLOW"] = a.allowed_tools
        if a.disallowed_tools:
            os.environ["MH_DENY"] = a.disallowed_tools
        fmt = a.output_format or ("json" if a.json_out else "")
        wt = _worktree_session(a, prompt)
        try:
            return agent_main(a.dir, prompt, a.auto, a.resume, a.session, a.no_mcp, a.request,
                              json_out=fmt if fmt in ("json", "stream-json") else False, mode=mode,
                              agent=a.agent_id, project=a.project, incognito=a.incognito, attach=a.attach or None,
                              output_last_message=a.output_last_message, effort=getattr(a, "effort", None),
                              **({"worktree_id": wt["id"]} if wt else {}))
        finally:
            _worktree_done(wt)
    if argv[:2] == ["mcp", "add-json"]:          # meshpool mcp add-json NAME '{"command": ...}'  (or - for stdin)
        return _mcp_add_json(argv[2:])
    if argv[0] in _APP_COMMANDS and not (argv[0] == "mcp" and len(argv) == 1):   # the app's features as commands
        from meshpool_harness import cli_app
        return cli_app.main(argv)
    if argv[0] == "worktree":                    # meshpool worktree list|diff|merge|discard
        from meshpool_harness import worktrees as WT
        return WT.cli(argv[1:])
    if argv[0] == "agent":
        return main(argv[1:] or [])
    if argv[0] == "permissions":
        return _permissions_cmd(argv[1:])
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
    if argv[0] == "doctor":                    # meshpool doctor [folder]: problems in meshpool's own files
        from meshpool_harness import doctor as DR
        text, code = DR.report(argv[1] if len(argv) > 1 else ".")
        print(text)
        return code
    if argv[0] == "completion":                # meshpool completion bash|zsh|fish
        sh = (argv[1:2] or [""])[0]
        if sh not in ("bash", "zsh", "fish"):
            print("usage: meshpool completion bash|zsh|fish\n  bash: eval \"$(meshpool completion bash)\" in "
                  "~/.bashrc\n  zsh: meshpool completion zsh > \"${fpath[1]}/_meshpool\"\n  fish: meshpool "
                  "completion fish > ~/.config/fish/completions/meshpool.fish", file=sys.stderr)
            return 2
        sys.stdout.write(completion(sh))
        return 0
    if argv[0] == "trust":                     # meshpool trust [path] [--revoke] [--list]
        return _trust_cli(argv[1:])
    if argv[0] == "office":                    # meshpool office install | status | uninstall | manifest
        from meshpool_harness import office as OF
        return OF.cli(argv[1:])
    if argv[0] == "run-due":
        from meshpool_harness import scheduler_os as SO
        failed: list = []
        print(f"ran {SO.run_due(failed)} scheduled task(s)", flush=True)
        if failed:
            print(f"{len(failed)} scheduled task(s) could not be started", file=sys.stderr, flush=True)
        # agent helper threads and servers must not keep a background job alive. A failure exits 1; the cron line
        # sends all output to /dev/null and launchd keeps no log for this job, so that only reaches a person who ran it
        os._exit(1 if failed else 0)
    if argv[0] == "scheduler":
        from meshpool_harness import scheduler_os as SO
        sub_cmd = argv[1] if len(argv) > 1 else ""
        if sub_cmd == "status":
            print("Installed: scheduled tasks also run while Meshpool is closed." if SO.installed()
                  else "Not installed: scheduled tasks run only while Meshpool is open.")
            return 0
        if sub_cmd not in ("install", "uninstall"):
            print("Usage: meshpool scheduler install | uninstall | status")
            return 0 if sub_cmd in ("", "help", "-h", "--help") else 1
        try:
            print(SO.install() if sub_cmd == "install" else SO.uninstall())
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
                print(f"{what}: {r['summary']}")
                return 0
            try:
                r = CI.import_export(os.path.expanduser(what))
            except (OSError, ValueError, zipfile.BadZipFile) as e:
                print(f"Could not import {what}: {e}")
                return 1
            print(f"{'Claude' if r['kind'] == 'claude' else 'ChatGPT'} export: {r['summary']} "
                  f"{len(r['skills'])} project skills, {r['memory']} facts into "
                  f"memory, {r['instructions']} instructions.")
            return 0
        return 0 if onboard.context_step(Style(_enable_ansi())) else 1
    if argv[0] == "login":
        if any(x in ("-h", "--help") for x in argv[1:]):
            print("usage: meshpool login\n\nAsk for your Meshpool API key and save it in the settings file.")
            return 0
        from meshpool_harness.agent.tui import ask_key
        return 0 if ask_key(force=True) else 1
    if argv[0] == "mcp" and len(argv) > 1:
        from meshpool_harness import cli_app
        return cli_app.main(argv)
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
        p.add_argument("request", nargs="?")
        p.add_argument("--files", nargs="*", default=[])
        p.add_argument("--engine")
        p.add_argument("--workdir")
        p.add_argument("--format", dest="output_format")
        if c == "run":
            p.add_argument("--out", default="./mh_out")
            p.add_argument("--model", help="model id for every engine (e.g. chatgpt:gpt-6.1-sol); default: MH_MODEL / settings")
    w = sub.add_parser("work", help="fetch a task from a connector (github:, jira:, http:, file:, custom), run it, report back")
    w.add_argument("source", help="e.g. github:owner/repo#12, jira:PROJ-7, http:https://..., file:task.md")
    w.add_argument("--workdir", default=".")
    w.add_argument("--engine", default=None)
    w.add_argument("--out", default="./mh_out")
    w.add_argument("--report", action="store_true", help="post the outcome back to the source")
    w.add_argument("--json", action="store_true", help="print the result as JSON")
    u = sub.add_parser("ui", help="open the Meshpool web app")
    u.add_argument("--port", type=int, default=int(os.environ.get("MH_UI_PORT", "8765")))
    u.add_argument("--no-browser", action="store_true")
    ck = sub.add_parser("check", help="show settings and installed tools; --live sends one tiny model call")
    ck.add_argument("--live", action="store_true")
    sub.add_parser("install-browser", help="install Playwright and Chromium for the browser engine")
    a = ap.parse_args(argv)
    if a.cmd in ("route", "run"):
        if not a.request and a.files:                 # `--files a.csv "request"`: --files took the request too
            a.request = a.files.pop()
        if not a.request:
            ap.error(f"{a.cmd}: the request is required")
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
        try:
            conn, ref = get(a.source)
            task = conn.fetch(ref)
        except (OSError, ValueError) as e:
            print(f"Could not fetch {a.source}: {e}")
            return 1
        res = run_request(task.text(), [], a.out, a.engine, a.workdir)
        if a.report:
            conn.report(task, res)
        if a.json:
            print(json.dumps({"task": task.ref, "source": task.source,
                              **{k: res.get(k) for k in ("ok", "answer", "artifacts", "reason", "out_dir")}},
                             indent=1, default=str))
            return 0 if res.get("ok") else 1
        print(res.get("answer") or "")
        if res.get("artifacts"):
            print("\nFiles:")
            for f in res["artifacts"]:
                print("  " + str(f))
        print("\nOutput folder: " + str(res.get("out_dir", "")))
        if not res.get("ok"):
            print("Failed: " + str(res.get("reason") or "no reason given"))
        return 0 if res.get("ok") else 1
    if a.cmd == "route":
        from meshpool_harness.router.rules import decide
        eng, conf, why = decide(a.request, a.files, a.output_format, a.workdir)
        print(json.dumps({"engines": eng, "confidence": conf, "reason": why}, indent=1))
        return 0
    from meshpool_harness.agent.office_jobs import guard_from_env
    guard_from_env()                          # an office job: ends at its deadline or when its starter is gone
    res = run_request(a.request, a.files, a.out, a.engine, a.workdir, output_format=a.output_format,
                      **({"model": a.model} if getattr(a, "model", None) else {}))
    print(json.dumps(res, indent=1))
    return 0 if res["ok"] else 1


def ping(model: str | None = None, base: str = "", key: str = "", kind: str = "", api_version: str = "") -> dict:
    """One tiny model call with the current settings (or `model`, and a `base` URL and `key` not saved yet), always
    to the key endpoint (never the ChatGPT sign-in). Returns {ok, detail, model, seconds, endpoint}."""
    from meshpool_harness.core.wire import SimpleLedger
    t0 = time.time()
    led = None
    try:
        led = SimpleLedger("check")
        if base:
            led.base_url = base.rstrip("/")
        if key:
            led.api_key = key
        m = (model or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash").strip()
        if m.partition(":")[0] == "chatgpt" and ":" in m:    # a ChatGPT model is not served by the key endpoint
            alt = (os.environ.get("MH_MODEL") or "").strip()
            from meshpool_harness import providers as PV
            if alt and not alt.startswith("chatgpt:"):
                m = alt
            elif "meshpool" in PV.host(led.base_url):
                m = "deepseek-v4.1-flash"
            else:
                return {"ok": False, "detail": "Choose a model this endpoint serves first.", "model": "",
                        "seconds": 0.0, "endpoint": led.base_url}
        model = m
        led.kind, led.api_version = kind, api_version    # an Azure kind or api-version typed but not saved yet
        r = led.chat(model=model, messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                     max_tokens=16, retries=1, timeout=60, key_only=True)   # Responses endpoints need at least 16
        msg_ = ((r.get("choices") or [{}])[0].get("message"))
        if not isinstance(msg_, dict):
            raise RuntimeError("meshpool HTTP 200 but not a model reply: no message")
        txt = msg_.get("content") or ""
        from meshpool_harness.core import usage
        usage.record(model, r.get("usage"), base=led.base_url, source="check", request_chars=30,
                     reply_chars=len(txt))
        return {"ok": True, "detail": f"model replied: {txt.strip()[:40]!r}" if txt.strip() else "connected",
                "model": model, "seconds": round(time.time() - t0, 1), "endpoint": led.base_url}
    except Exception as e:
        import re
        m_st = re.match(r"^meshpool HTTP (\d+): ", str(e))
        msg = re.sub(r"^meshpool HTTP \d+: ", "", str(e))     # wire.py already explains the status in plain words
        return {"ok": False, "detail": msg[:400], "status": int(m_st.group(1)) if m_st else 0,
                "model": model or "", "seconds": round(time.time() - t0, 1),
                "endpoint": led.base_url if led else (base or __import__("meshpool_harness.providers", fromlist=["x"]).key_base())}


def _check(live: bool) -> int:
    from meshpool_harness import harnesses as HR
    from meshpool_harness import providers as PV
    base, key, _src = PV.endpoint_pair()             # the key and the address it was set for, never mixed
    # A ChatGPT sign-in is enough on its own: report what is ready rather than calling the setup broken when there
    # is no Meshpool key.
    ready = {p: _safe(lambda p=p: PV.ready(p)) for p in PV.PROVIDERS}
    usable = _safe(HR.ready)
    print(f"Settings file : {_config.config_path()}")
    print(f"Endpoint      : {base}")
    print(f"Model         : {os.environ.get('MH_MODEL', 'deepseek-v4.1-flash')}")
    kn = PV.key_name()
    for_ = "" if kn == "Meshpool" else f" for {kn}"        # the key belongs to the endpoint above, not to Meshpool
    print(f"API key       : {'set' + for_ + ' (ends ' + key[-4:] + ')' if key else 'not set' + for_}")
    names = [_safe(lambda p=p: PV.name(p), p) for p, ok in ready.items() if ok]
    print(f"Ready to use  : {', '.join(names) if names else 'nothing yet (run: meshpool setup)'}")
    print()
    for row in _config.system_check():
        detail = row["detail"]
        if row["name"] == "LibreOffice" and not row["ok"]:
            from meshpool_harness import office_runtime
            if not office_runtime.is_packaged():
                detail = ("not included in this terminal install. Decks are not rendered for review and workbook "
                          "formulas are not recalculated; the Meshpool desktop app (or the Linux download) has it.")
        print(f"  [{'ok' if row['ok'] else '--'}] {row['name']:<15} {detail}")
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
