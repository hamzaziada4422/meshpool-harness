"""Bring your setup: find the skills and MCP servers already set up in Claude, Codex, Cursor and other apps on this
computer, and import the ones the user picks into Meshpool. Every harness then shares them.

Nothing is read until the user opens the import step, and nothing is copied until they choose.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.parse
from pathlib import Path


HOME = Path.home()


def _appdata() -> Path:
    if sys.platform == "darwin":
        return HOME / "Library" / "Application Support"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")


# ---------- skills ----------------------------------------------------------------------------------------------------

def skill_sources() -> list[tuple[str, Path]]:
    rows = [("Claude Code", HOME / ".claude" / "skills"),
            ("Codex", HOME / ".codex" / "skills"),
            ("Agent skills", HOME / ".agents" / "skills"),
            ("Cursor", HOME / ".cursor" / "skills"),
            ("Gemini CLI", HOME / ".gemini" / "skills")]
    plugins = HOME / ".claude" / "plugins"
    if plugins.is_dir():
        for d in sorted(plugins.glob("**/skills")):
            if d.is_dir() and "node_modules" not in d.parts:
                rows.append(("Claude Code plugin", d))
    try:
        from meshpool_harness.ui import workbench as W
        for p in W.projects():
            for sub, label in ((".claude/skills", "Project (Claude)"), (".codex/skills", "Project (Codex)"),
                               (".agents/skills", "Project")):
                rows.append((f"{label}: {p['name']}", Path(p["path"]) / sub))
    except Exception:  # noqa: BLE001 - projects are optional here
        pass
    return rows


def find_skills() -> list[dict]:
    from meshpool_harness import skills as SK
    have = {s["id"] for s in SK.load_all()}
    seen, out = set(), []
    for source, root in skill_sources():
        if not root.is_dir():
            continue
        for f in sorted(root.glob("*/SKILL.md")) + sorted(root.glob("*/*/SKILL.md")):
            d = f.parent
            if d.name.startswith(".") or str(d) in seen:
                continue
            seen.add(str(d))
            try:
                meta, _ = SK.parse(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            sid = SK.slug(meta.get("name") or d.name)
            out.append({"key": "skill:" + str(d), "kind": "skill", "source": source, "name": meta.get("name") or d.name,
                        "description": meta.get("description", "")[:200], "path": str(d), "id": sid,
                        "exists": sid in have,
                        # plugin skills come in bundles of dozens: listed, but not ticked by default
                        "suggested": source != "Claude Code plugin"})
    return out


def import_skill(path: str) -> str:
    from meshpool_harness import skills as SK
    src = Path(path)
    if not (src / "SKILL.md").is_file():
        raise ValueError(f"No SKILL.md in {path}")
    meta, _ = SK.parse((src / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    dst = SK.folder() / SK.slug(meta.get("name") or src.name)
    if dst.exists():
        shutil.rmtree(dst)
    from meshpool_harness import agent_import as AI

    def skip(d, names):                      # links are left out: one pointing at ~/.ssh copied a private key in
        return {n for n in names if n in (".git", "node_modules", "__pycache__", ".DS_Store")
                or os.path.islink(os.path.join(d, n)) or AI.DENY_NAMES.match(n)}   # and never a login file
    shutil.copytree(src, dst, ignore=skip)
    return dst.name


# ---------- MCP servers -----------------------------------------------------------------------------------------------

def _json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _norm(spec: dict) -> dict | None:
    """One server entry from any app's config, in Meshpool's mcp.json shape."""
    if not isinstance(spec, dict):
        return None
    if spec.get("disabled") is True or spec.get("enabled") is False:
        return None
    url = spec.get("url") or spec.get("serverUrl") or spec.get("httpUrl")
    if url:
        out = {"url": url}
        if isinstance(spec.get("headers"), dict):
            out["headers"] = {str(k): str(v) for k, v in spec["headers"].items()}
        if spec.get("bearer_token_env_var"):
            out.setdefault("headers", {})["Authorization"] = "Bearer ${" + spec["bearer_token_env_var"] + "}"
        return out
    cmd = spec.get("command")
    if not cmd:
        return None
    out = {"command": str(cmd), "args": [str(a) for a in spec.get("args") or []]}
    if isinstance(spec.get("env"), dict) and spec["env"]:
        out["env"] = {str(k): str(v) for k, v in spec["env"].items()}
    if spec.get("cwd"):
        out["cwd"] = str(spec["cwd"])
    return out


def norm_existing(spec: dict) -> dict | None:
    """An entry already in Meshpool's mcp.json, switched on or off (imports start switched off)."""
    return _norm({k: v for k, v in spec.items() if k not in ("disabled", "enabled")}) if isinstance(spec, dict) else None


def toml_loads(text: str) -> dict:
    """Parse TOML: tomllib (Python 3.11+), else `tomli` if installed, else a small parser that understands what MCP
    server tables use (dotted [tables], strings, numbers, booleans, arrays and inline tables)."""
    try:
        import tomllib as _t
    except ImportError:
        try:
            import tomli as _t  # type: ignore[no-redef]
        except ImportError:
            return _mini_toml(text)
    return _t.loads(text)


def _mini_toml(text: str) -> dict:
    root: dict = {}
    cur = root

    def value(v: str):
        v = v.strip()
        out, i = _mini_value(v, 0)
        if v[i:].strip() not in ("", ) and not v[i:].strip().startswith("#"):
            raise ValueError("unsupported TOML value")
        return out

    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        if not buf and line.startswith("["):
            m = re.match(r"^\[\s*(\[?)\s*([^\]]+?)\s*\]\]?\s*(#.*)?$", line)
            if not m:
                raise ValueError("bad table header")
            cur = root
            for part in [x.strip().strip("\"'") for x in re.findall(r'"[^"]*"|\'[^\']*\'|[^.\s]+', m.group(2)) if x != "."]:
                nxt = cur.setdefault(part, {})
                if isinstance(nxt, list):
                    nxt = nxt[-1]
                cur = nxt
            continue
        buf += (" " if buf else "") + line
        if buf.count("[") != buf.count("]") or buf.count("{") != buf.count("}"):
            continue                                           # a multi-line array or table: keep reading
        k, sep, v = buf.partition("=")
        buf = ""
        if not sep:
            raise ValueError("bad line")
        key = k.strip().strip("\"'")
        cur[key] = value(v)
    if buf:
        raise ValueError("unterminated value")
    return root


def _mini_value(s: str, i: int):
    while i < len(s) and s[i] in " \t":
        i += 1
    if i >= len(s):
        raise ValueError("missing value")
    c = s[i]
    if c in "\"'":
        j = i + 1
        while j < len(s) and s[j] != c:
            j += 2 if (c == '"' and s[j] == "\\") else 1
        if j >= len(s):
            raise ValueError("unterminated string")
        body = s[i + 1:j]
        return (json.loads('"' + body + '"') if c == '"' else body), j + 1
    if c == "[":
        items, i = [], i + 1
        while True:
            while i < len(s) and s[i] in " \t,":
                i += 1
            if i >= len(s):
                raise ValueError("unterminated array")
            if s[i] == "]":
                return items, i + 1
            v, i = _mini_value(s, i)
            items.append(v)
    if c == "{":
        d, i = {}, i + 1
        while True:
            while i < len(s) and s[i] in " \t,":
                i += 1
            if i >= len(s):
                raise ValueError("unterminated table")
            if s[i] == "}":
                return d, i + 1
            m = re.match(r'\s*("[^"]*"|\'[^\']*\'|[A-Za-z0-9_-]+)\s*=', s[i:])
            if not m:
                raise ValueError("bad inline table")
            i += m.end()
            v, i = _mini_value(s, i)
            d[m.group(1).strip("\"'")] = v
    m = re.match(r"[^\s,\]}#]+", s[i:])
    tok = m.group(0) if m else ""
    if tok in ("true", "false"):
        return tok == "true", i + len(tok)
    try:
        return (int(tok.replace("_", "")) if re.fullmatch(r"[+-]?\d[\d_]*", tok) else float(tok)), i + len(tok)
    except ValueError:
        raise ValueError("unsupported TOML value") from None


def mcp_sources() -> list[tuple[str, dict]]:
    """(app name, {server name: raw spec}) for every app config found on this computer."""
    found = []
    ad = _appdata()
    for app, p, key in (("Claude Desktop", ad / "Claude" / "claude_desktop_config.json", "mcpServers"),
                        ("Cursor", HOME / ".cursor" / "mcp.json", "mcpServers"),
                        ("Windsurf", HOME / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers"),
                        ("Gemini CLI", HOME / ".gemini" / "settings.json", "mcpServers"),
                        ("VS Code", ad / "Code" / "User" / "mcp.json", "servers"),
                        ("Cline", ad / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings"
                         / "cline_mcp_settings.json", "mcpServers")):
        servers = _json(p).get(key)
        if isinstance(servers, dict) and servers:
            found.append((app, servers))
    cc = _json(HOME / ".claude.json")
    if isinstance(cc.get("mcpServers"), dict) and cc["mcpServers"]:
        found.append(("Claude Code", cc["mcpServers"]))
    for proj, conf in (cc.get("projects") or {}).items():
        if isinstance(conf, dict) and isinstance(conf.get("mcpServers"), dict) and conf["mcpServers"]:
            found.append((f"Claude Code: {Path(proj).name}", conf["mcpServers"]))
    codex = HOME / ".codex" / "config.toml"
    if codex.is_file():
        try:
            servers = toml_loads(codex.read_text(encoding="utf-8")).get("mcp_servers") or {}
            if servers:
                found.append(("Codex", servers))
        except Exception:  # noqa: BLE001 - a broken config file is skipped
            pass
    return found


def find_mcp() -> list[dict]:
    from meshpool_harness.agent import mcp as M
    try:
        have = M.load_config()
    except Exception:  # noqa: BLE001
        have = {}
    have_sig = {json.dumps(norm_existing(v) or {}, sort_keys=True) for v in have.values() if isinstance(v, dict)}
    out, seen = [], set()
    for app, servers in mcp_sources():
        for name, raw in servers.items():
            spec = _norm(raw)
            if not spec:
                continue
            from meshpool_harness import agent_import as AI
            spec, gone = AI.strip_secrets(spec)           # secret-looking variables, headers and arguments stay behind
            sig = json.dumps(spec, sort_keys=True)
            if sig in seen:
                continue
            seen.add(sig)
            what = ("Connects to " + urllib.parse.urlparse(spec["url"]).netloc) if spec.get("url") else \
                "Runs on this computer"
            broken = bool(spec.get("command")) and not shutil.which(str(spec["command"]))
            out.append({"key": f"mcp:{app}:{name}", "kind": "mcp", "source": app, "name": str(name),
                        "description": what, "spec": spec, "exists": sig in have_sig,
                        "has_secrets": bool(gone), "stripped": gone,
                        "default": not (broken or gone), "broken": broken})
    return out


def import_mcp(name: str, spec: dict, description: str | None = None) -> str:
    """Add a server to Meshpool's mcp.json under a free name (an identical entry keeps its name); returns the name."""
    from meshpool_harness.agent import mcp as M
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:40] or "server"

    def add(servers: dict) -> str:
        n, i = base, 2
        while n in servers and json.dumps(norm_existing(servers[n]) or {}, sort_keys=True) != json.dumps(spec, sort_keys=True):
            n, i = f"{base}-{i}", i + 1
        servers[n] = dict(spec, description=description or f"Imported from another app ({name}).",
                          disabled=True, needs_test=True)   # another app's command: it starts once before it can be on
        return n
    try:
        return M.edit_config(add)
    except ValueError:
        raise ValueError("mcp.json is not valid JSON. Fix it under Settings, MCP servers, then import again.")


# ---------- both --------------------------------------------------------------------------------------------------------

def scan() -> dict:
    return {"skills": find_skills(), "mcp": find_mcp()}


def run(keys: list[str]) -> dict:
    keys = set(keys or [])
    found = scan()
    done = {"skills": [], "mcp": [], "errors": []}
    for s in found["skills"]:
        if s["key"] in keys:
            try:
                done["skills"].append(import_skill(s["path"]))
            except (OSError, ValueError) as e:
                done["errors"].append(f"{s['name']}: {e}")
    for m in found["mcp"]:
        if m["key"] in keys:
            try:
                done["mcp"].append(import_mcp(m["name"], m["spec"]))
            except (OSError, ValueError) as e:
                done["errors"].append(f"{m['name']}: {e}")
    return done


# ---------- context: what another assistant already knows about the user -----------------------------------------
# Two ways in, both a few clicks: (1) a prompt the user pastes into ChatGPT, Claude or Gemini, whose answer they paste
# back here; (2) the normal data export (the .zip ChatGPT and Claude send by email). Nothing leaves this computer
# except, for an export, one call to a small model that pulls lasting facts out of conversation titles and openers.

CONTEXT_PROMPT = """I am moving to a new assistant and want it to know me the way you do. Please write out what you \
know about me from our past conversations and your memory, in exactly this format and nothing else:

## Instructions
(How I like answers: tone, length, format, language, things to always or never do. One per line, starting with "- ".)

## About me
(Lasting facts: my role, company, industry, location and time zone, tools and data I use, projects I work on, \
people and clients I mention often, preferences. One fact per line, starting with "- ". No secrets, passwords or \
account numbers.)

## Recurring work
(Tasks I ask for again and again, with how I like them done. One per line, starting with "- ".)"""


EXPORT_LINKS = [
    {"name": "ChatGPT", "url": "https://chatgpt.com/#settings/DataControls"},
    {"name": "Claude", "url": "https://claude.ai/settings/data-privacy-controls"},
]


def context_prompt() -> str:
    return CONTEXT_PROMPT


def _sections(text: str) -> dict:
    out, cur = {}, None
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        h = re.match(r"^\s*#{1,4}\s*(.+?)\s*$", line)
        if h:
            cur = h.group(1).strip().lower()
            out.setdefault(cur, [])
            continue
        item = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", line)
        if item and cur is not None:
            out[cur].append(item.group(1).strip())
    return out


def context_model() -> str:
    """The model that reads an export: MH_CONTEXT_MODEL, else the one the person configured (Settings, or the settings
    file), never a name written into the code (F-32)."""
    from meshpool_harness import config as C
    for k in ("MH_CONTEXT_MODEL", "MH_MODEL"):
        v = os.environ.get(k) or C.read().get(k)
        if v:
            return v
    return next(d for key, _l, d, _h in C.FIELDS if key == "MH_MODEL")


KNOWN_SECTIONS = ("instruction", "about", "recurring", "prefer", "style", "work")


def check_context_answer(text: str) -> str:
    """"" when a model's answer has the shape CONTEXT_PROMPT asks for (headings with "- " lines under them); else why
    not, in plain words. Chat, a refusal or an unrelated list must never become memory."""
    sec = _sections(text)
    known = {k: v for k, v in sec.items() if any(w in k for w in KNOWN_SECTIONS)}
    n = sum(len(v) for v in known.values())
    if not known or not n:
        return "The model did not answer in the format asked for, so nothing was saved from the conversations."
    if n > 120 or any(len(x) > 400 for v in known.values() for x in v):
        return "The model's answer was too long or too loose to trust, so nothing was saved from the conversations."
    return ""


def import_context_text(text: str) -> dict:
    """Read the answer to CONTEXT_PROMPT (or any similar list) into Meshpool: preferences become instructions,
    facts and recurring work become memory. Lines that look like secrets or instructions to the system are refused
    by the memory store."""
    from meshpool_harness import assistant as AS
    sec = _sections(text)
    if not sec:                                         # a plain list with no headings: treat every line as a fact
        sec = {"about me": [ln.strip("-•* ").strip() for ln in (text or "").splitlines() if len(ln.strip()) > 3]}
    ins = [x for k, v in sec.items() if "instruction" in k or "prefer" in k or "style" in k for x in v]
    facts = [x for k, v in sec.items() if not ("instruction" in k or "prefer" in k or "style" in k) for x in v]
    added_ins = 0
    if ins:
        cur = AS.instructions()
        new = [x for x in ins if x.lower() not in cur.lower()]
        if new:
            AS.set_instructions((cur + "\n" if cur else "") + "\n".join(f"- {x}" for x in new))
            added_ins = len(new)
    saved, skipped = 0, []
    for f in facts:
        try:
            from meshpool_harness import memstore as MS       # imported facts wait in the review tray first
            _, made = MS.add(f[:400], kind="stated", status="pending", source={"type": "import"}, by="meshpool")
            saved += bool(made)
        except ValueError as e:
            skipped.append(f"{f[:60]}: {e}")
    return {"instructions": added_ins, "memory": saved, "skipped": skipped}


def import_instructions_as_skill(name: str, text: str, description: str = "") -> str:
    """A custom GPT's instructions, a Claude project's instructions or any saved prompt becomes a Meshpool skill."""
    from meshpool_harness import skills as SK
    text = (text or "").strip()
    if len(text) < 20:
        raise ValueError("Paste the instructions (at least a sentence).")
    desc = description.strip() or re.sub(r"\s+", " ", text)[:200]
    r = SK.create(name or "imported-instructions", desc, text)
    return r.get("id") or r.get("name") or str(r) if isinstance(r, dict) else str(r)


_MARK = "<!-- imported into Meshpool -->"


def _write_skill(name: str, desc: str, body: str) -> str:
    """Write a skill, or rewrite one an earlier import made; returns its id. A skill the import did not make (a
    built-in like project-plan, or the user's own) is never replaced: the import takes the next free name."""
    from meshpool_harness import skills as SK
    base = SK.slug(name or "imported")
    desc = re.sub(r"\s+", " ", desc or "").strip()[:300] or base
    sid, n = base, 2
    while (SK.folder() / sid / "SKILL.md").is_file() and \
            _MARK not in (SK.folder() / sid / "SKILL.md").read_text(encoding="utf-8", errors="replace"):
        sid, n = f"{base}-{n}", n + 1
    SK.create(sid, desc, body.rstrip() + "\n\n" + _MARK + "\n", overwrite=True)
    return sid


def _export_kind(z) -> str:
    names = set(z.namelist())
    base = {n.rsplit("/", 1)[-1] for n in names}
    if "projects.json" in base or "users.json" in base:
        return "claude"
    if "conversations.json" in base and ("chat.html" in base or "message_feedback.json" in base or "user.json" in base):
        return "chatgpt"
    return "chatgpt" if "conversations.json" in base else "unknown"


def _read_json(z, leaf: str):
    for n in z.namelist():
        if n.rsplit("/", 1)[-1] == leaf:
            return json.loads(z.read(n).decode("utf-8", errors="replace"))
    return None


def _openers(convs: list, kind: str, limit: int = 80) -> list[str]:
    """Title plus the user's first message of the most recent conversations (trimmed): enough to find lasting facts."""
    rows = []
    for c in convs or []:
        title = c.get("title") or c.get("name") or ""
        first, ts = "", c.get("update_time") or c.get("updated_at") or c.get("create_time") or 0
        if kind == "chatgpt":
            for node in (c.get("mapping") or {}).values():
                m = (node or {}).get("message") or {}
                if (m.get("author") or {}).get("role") == "user":
                    parts = (m.get("content") or {}).get("parts") or []
                    first = " ".join(p for p in parts if isinstance(p, str))
                    break
        else:
            for m in c.get("chat_messages") or []:
                if m.get("sender") == "human":
                    first = m.get("text") or ""
                    break
        rows.append((str(ts), f"{title} :: {re.sub(chr(10), ' ', first)[:240]}"))
    rows.sort(reverse=True)
    return [r for _, r in rows[:limit]]


def import_export(path: str, client=None) -> dict:
    """Read a ChatGPT or Claude data export (.zip). Claude projects (their instructions and knowledge files) become
    skills; lasting facts about the user, distilled by a small model from recent conversation titles and openers,
    become memory. Conversations themselves are not copied."""
    import zipfile
    z = zipfile.ZipFile(path)
    kind = _export_kind(z)
    if kind == "unknown":
        raise ValueError("This does not look like a ChatGPT or Claude export (no conversations.json inside).")
    done = {"kind": kind, "skills": [], "memory": 0, "instructions": 0, "skipped": []}
    if kind == "claude":
        for p in _read_json(z, "projects.json") or []:
            body = (p.get("prompt_template") or "").strip()
            docs = p.get("docs") or []
            if not body and not docs:
                continue
            text = body or f"Project knowledge for {p.get('name', 'this project')}."
            if docs:
                text += "\n\nKnowledge files (in this skill's folder): " + ", ".join(d.get("filename", "doc") for d in docs)
            slug = _write_skill(f"project-{p.get('name', 'claude')}", p.get("description") or
                                f"Instructions and knowledge from the Claude project '{p.get('name', '')}'.", text)
            from meshpool_harness import skills as SK
            for d in docs[:30]:
                fn = re.sub(r"[^\w .-]", "_", d.get("filename") or "doc.txt")[:80]
                if fn.upper() == "SKILL.MD" or fn.startswith("."):
                    fn = "knowledge-" + fn.lstrip(".")              # never the skill's own instructions
                (SK.folder() / slug / fn).write_text(d.get("content") or "", encoding="utf-8")
            done["skills"].append(slug)
    convs = _read_json(z, "conversations.json") or []
    lines = _openers(convs, kind)
    if lines:
        try:
            from meshpool_harness.core.client import LiveClient
            client = client or LiveClient("context_import")
            r = client.chat(model=context_model(), max_tokens=2000, temperature=0.1,
                            task_id="context-import", role="worker", messages=[
                                {"role": "system", "content": "From these conversation titles and first messages, write "
                                 "what a new assistant should know about this person, in the format below. Only lasting "
                                 "facts and preferences supported by several conversations; no secrets, no one-off "
                                 "questions, no guesses.\n\n" + CONTEXT_PROMPT.split("in exactly this format and nothing else:")[1]},
                                {"role": "user", "content": "\n".join(lines)}])
            answer = r["choices"][0]["message"].get("content") or ""
            problem = check_context_answer(answer)
            if problem:                                  # not the format asked for: save nothing (F-32)
                raise ValueError(problem)
            got = import_context_text(answer)
            done.update(memory=got["memory"], instructions=got["instructions"], skipped=got["skipped"])
        except Exception as e:  # the export still counts: projects were imported
            done["skipped"].append(f"context from conversations: {type(e).__name__}: {str(e)[:160]}")
    done["conversations_seen"] = len(convs)
    return done
