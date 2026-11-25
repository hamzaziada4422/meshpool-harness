"""meshpool doctor: finds problems in the files that set meshpool up, the ones that otherwise fail quietly. The settings
file, hooks, saved rules, MCP server lists, skills, sub-agents, commands and instruction files, for the user and for
a project. It only reads; it changes nothing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from meshpool_harness import config as C


def _p(level: str, file, problem: str) -> dict:
    return {"level": level, "file": str(file), "problem": problem}


def _json(f: Path, out: list) -> object:
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except OSError as e:
        out.append(_p("error", f, f"cannot be read: {e}"))
    except ValueError as e:
        out.append(_p("error", f, f"is not valid JSON: {e}"))
    return None


def _settings(out: list):
    f = C.config_path()
    if not f.is_file():
        return
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        out.append(_p("error", f, f"cannot be read: {e}"))
        return
    known = {k for k, *_ in C.FIELDS}
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        k, sep, _ = s.partition("=")
        k = k.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            out.append(_p("error", f, f"line {i} is not KEY=VALUE, so it is ignored"))
        elif not k.startswith(("MH_", "MESHPOOL_")) and k not in known:
            out.append(_p("warning", f, f"line {i}: {k} is not a meshpool setting (they start with MH_ or MESHPOOL_)"))


def _hooks(f: Path, out: list):
    from meshpool_harness.agent import extras as EX
    d = _json(f, out)
    if d is None:
        return
    if not isinstance(d, dict):
        return out.append(_p("error", f, "must be a JSON object of event names"))
    for ev, hs in d.items():
        if ev not in EX.EVENTS:
            out.append(_p("warning", f, f"{ev} is not a hook event, so its hooks never run. Events: "
                                        + ", ".join(EX.EVENTS)))
            continue
        if not isinstance(hs, list):
            out.append(_p("error", f, f"{ev} must be a list of hooks"))
            continue
        for i, h in enumerate(hs, 1):
            where = f"{ev} hook {i}"
            if not isinstance(h, dict) or not isinstance(h.get("command"), str) or not h["command"].strip():
                out.append(_p("error", f, f"{where} has no command, so it never runs"))
                continue
            if h.get("args") is not None and not isinstance(h["args"], list):
                out.append(_p("error", f, f"{where}: args must be a list, so this hook never runs"))
            if h.get("timeout") is not None and not isinstance(h["timeout"], (int, float)):
                out.append(_p("error", f, f"{where}: timeout must be a number of seconds"))
            if h.get("matcher") is not None and not isinstance(h["matcher"], str):
                out.append(_p("error", f, f"{where}: matcher must be text"))
            if h.get("shell") and str(h["shell"]).lower() not in ("bash", "powershell", "sh"):
                out.append(_p("warning", f, f"{where}: shell is bash or powershell"))


def _rules(f: Path, out: list):
    d = _json(f, out)
    if d is None:
        return
    if not isinstance(d, dict):
        return out.append(_p("error", f, "must be a JSON object with allow, deny and ask lists"))
    layers = [("", d)] + [(f"projects[{k}].", v) for k, v in (d.get("projects") or {}).items() if isinstance(v, dict)]
    for pre, layer in layers:
        for k in ("allow", "deny", "ask"):
            v = layer.get(k)
            if v is None:
                continue
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                out.append(_p("error", f, f"{pre}{k} must be a list of rules (text)"))
                continue
            for r in v:
                if r.startswith("bash:") and not r[5:].strip():
                    out.append(_p("warning", f, f"{pre}{k}: {r!r} has no command pattern"))
    for k in d:
        if k not in ("allow", "deny", "ask", "projects", "defaultMode", "additionalDirectories"):
            out.append(_p("warning", f, f"{k} is not read by meshpool (it reads allow, deny, ask and projects)"))


def _mcp(f: Path, out: list):
    d = _json(f, out)
    if d is None:
        return
    if not isinstance(d, dict):
        return out.append(_p("error", f, "must be a JSON object with mcpServers"))
    servers = d.get("mcpServers", d.get("servers"))
    if servers is None:
        return out.append(_p("warning", f, "has no mcpServers, so it adds no servers"))
    if not isinstance(servers, dict):
        return out.append(_p("error", f, "mcpServers must be an object of server name to settings"))
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            out.append(_p("error", f, f"server {name}: its settings must be an object"))
        elif not (spec.get("command") or spec.get("url")):
            out.append(_p("error", f, f"server {name} needs a command or a url"))
        elif spec.get("args") is not None and not isinstance(spec["args"], list):
            out.append(_p("error", f, f"server {name}: args must be a list"))


def _skills(out: list):
    from meshpool_harness import skills as SK
    base = SK.folder()
    if not base.is_dir():
        return
    names: dict[str, Path] = {}
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        f = d / "SKILL.md"
        if not f.is_file():
            out.append(_p("warning", d, "has no SKILL.md, so it is not a skill"))
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            out.append(_p("error", f, f"cannot be read: {e}"))
            continue
        if not text.startswith("---") or text.find("\n---", 3) < 0:
            out.append(_p("error", f, "has no front matter (--- name and description ---)"))
            continue
        shipped = (C.APP / "defaults" / "skills" / d.name).is_dir()   # a copy of meshpool's own: it loads fine here
        try:
            import yaml
            if not shipped:
                yaml.safe_load(text[3:text.find("\n---", 3)])
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            out.append(_p("warning", f, f"front matter is not valid YAML ({str(e).splitlines()[0]}); "
                                        "only simple key: value lines are read"))
        meta, body = SK.parse(text)
        if not str(meta.get("description") or "").strip():
            out.append(_p("error", f, "has no description, so the agent cannot tell when to use it"))
        elif len(str(meta["description"])) > 1024:
            out.append(_p("warning", f, "description is longer than 1024 characters"))
        if not body.strip():
            out.append(_p("error", f, "has no instructions after the front matter"))
        key = SK.slug(str(meta.get("name") or d.name))
        if key in names:
            out.append(_p("warning", f, f"has the same name as {names[key]}; only one of them is used"))
        names[key] = f


def _agents(root, out: list):
    from meshpool_harness import skills as SK
    from meshpool_harness.agent import subagents as SA
    for d in SA.folders(root):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                meta, body = SK.parse(f.read_text(encoding="utf-8", errors="replace"))
            except OSError as e:
                out.append(_p("error", f, f"cannot be read: {e}"))
                continue
            if not body.strip():
                out.append(_p("error", f, "has no instructions, so this sub-agent is skipped"))
            if not str(meta.get("description") or "").strip():
                out.append(_p("warning", f, "has no description, so the agent cannot tell when to use it"))


def _instructions(root, out: list):
    from meshpool_harness.agent import extras as EX
    for _, f in EX.instruction_files(Path(root)):
        try:
            n = len(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if n > EX.MAX_FILE:
            out.append(_p("warning", f, f"is {n} characters; only the first {EX.MAX_FILE} are used"))


def check(root=".") -> list[dict]:
    """[{"level": "error" | "warning" | "info", "file", "problem"}]"""
    from meshpool_harness import trust as TR
    from meshpool_harness.agent import mcp as M
    root = Path(root).expanduser().resolve()
    out: list = []
    _settings(out)
    home = C.home()
    if (home / "hooks.json").is_file():
        _hooks(home / "hooks.json", out)
    if (home / "permissions.json").is_file():
        _rules(home / "permissions.json", out)
    if M.config_file().is_file():
        _mcp(M.config_file(), out)
    _skills(out)
    proj = [root / ".meshpool" / "hooks.json", root / ".meshpool" / "permissions.json"] + \
        [root / x for x in M.PROJECT_FILES]
    for f in proj:
        if not f.is_file():
            continue
        if f.name == "hooks.json":
            _hooks(f, out)
        elif f.name == "permissions.json":
            _rules(f, out)
        else:
            _mcp(f, out)
    if any(f.is_file() for f in proj) and not TR.is_trusted(root):
        out.append(_p("info", root, "this folder has its own hooks, rules or MCP servers, which stay off until you "
                                    "trust it (meshpool trust)"))
    _agents(root, out)
    _instructions(root, out)
    return out


def report(root=".") -> tuple[str, int]:
    """(text, exit code): 1 when any error was found."""
    rows = check(root)
    if not rows:
        return "No problems found in meshpool's settings, hooks, rules, MCP servers, skills or sub-agents.", 0
    lines = []
    for lvl in ("error", "warning", "info"):
        for r in (x for x in rows if x["level"] == lvl):
            lines.append(f"{lvl}: {r['file']}: {r['problem']}")
    n = sum(1 for r in rows if r["level"] == "error")
    lines.append(f"{n} error{'s' if n != 1 else ''}, {sum(1 for r in rows if r['level'] == 'warning')} warning(s).")
    return "\n".join(lines), 1 if n else 0
