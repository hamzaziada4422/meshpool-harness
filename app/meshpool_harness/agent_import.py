"""One-click import from the other agents on this computer: Claude Code, Codex, Cursor, OpenClaw and Hermes.

What comes over: skills, MCP server definitions, memories (as facts waiting in the "Meshpool thinks" tray) and the
instruction files the user wrote (CLAUDE.md, AGENTS.md, rules). Every item is listed first with a preview; nothing is
written until the user picks it, and `dry_run=True` (`meshpool import agents --dry-run`) writes nothing at all.

Credentials are never read. Every file this module opens goes through `_read()`, which refuses any file whose name or
folder looks like a login (`.credentials.json`, `auth.json`, `.env`, `*token*`, `oauth*`, keychains) and records what it
refused, so the report can say "not read". An MCP server comes over without its secret-looking environment variables,
headers and arguments (`*_TOKEN`, `*_API_KEY`, `Authorization`, `--token=...`); they are listed per server so the user
can add them again in Settings. Connecting an account is a separate, explicit step.

This is the only module that touches other agents' files, so it is the one to audit.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MAX_FILE = 400_000                 # bytes read from any one file
MAX_FACTS_PER_FILE = 60
MAX_INSTRUCTIONS = 3000            # characters taken from one instruction file
MAX_SKILLS_PER_AGENT = 200

# ---------- what is a credential ------------------------------------------------------------------------------------
DENY_NAMES = re.compile(r"(?i)^(\.credentials\.json|credentials(\..*)?|auth(\.json|\.toml)?|\.env(\..*)?|.*\.env|"
                        r".*token.*|oauth.*|.*secret.*|.*\.pem|.*\.key|id_rsa.*|.*keychain.*|\.netrc|cookies.*|"
                        r"session\.json|mcp-auth.*|.*\.p12)$")
DENY_DIRS = {".ssh", ".gnupg", "keychains", "oauth", "credentials", "secrets"}
# files that mix settings with logins: never opened, even though they hold settings we would like to read
OPAQUE = {"openclaw.json", "hermes.json"}

SECRET_NAME = re.compile(r"(?i)(token|secret|passw(or)?d|passwd|credential|authorization|bearer|cookie|api[_-]?key|"
                         r"private[_-]?key|access[_-]?key|auth[_-]?key|(^|[_-])key$)")
SECRET_VALUE = re.compile(r"^(sk-[\w-]{8,}|sk_[\w-]{8,}|ghp_\w{8,}|github_pat_\w{8,}|gho_\w{8,}|xox[bpars]-[\w-]{8,}|"
                          r"AKIA[0-9A-Z]{8,}|AIza[\w-]{8,}|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}|glpat-[\w-]{8,})")

_REFUSED: list[str] = []           # credential files this run declined to open (names only, for the report)


def home() -> Path:
    return Path.home()


def _denied(p: Path) -> bool:
    return bool(DENY_NAMES.match(p.name)) or p.name.lower() in OPAQUE or any(x.lower() in DENY_DIRS for x in p.parts)


def _read(p: Path) -> str:
    """The only way this module opens a file. A credential-looking file is refused, never opened."""
    if _denied(p):
        _REFUSED.append(str(p))
        return ""
    try:
        if p.stat().st_size > MAX_FILE:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _json(p: Path) -> dict:
    try:
        d = json.loads(_read(p) or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _present(p: Path) -> bool:
    """Does it exist? A stat, never a read: used to report credential files we left alone."""
    try:
        return p.exists()
    except OSError:
        return False


# ---------- secrets in an MCP server entry ------------------------------------------------------------------------------
def strip_secrets(spec: dict) -> tuple[dict, list[str]]:
    """The server entry without anything that looks like a secret, and the names that were removed."""
    out = dict(spec)
    gone: list[str] = []
    for key in ("env", "headers"):
        if isinstance(out.get(key), dict):
            keep = {}
            for k, v in out[key].items():
                if SECRET_NAME.search(str(k)) or SECRET_VALUE.match(str(v)):
                    gone.append(str(k))
                else:
                    keep[k] = v
            if keep:
                out[key] = keep
            else:
                out.pop(key)
    if isinstance(out.get("args"), list):
        args, skip = [], False
        for a in out["args"]:
            a = str(a)
            if skip:                                  # the value after "--token"
                skip = False
                gone.append("(argument value)")
                continue
            m = re.match(r"^(-{1,2}[\w-]+)=(.*)$", a)
            if m and (SECRET_NAME.search(m.group(1)) or SECRET_VALUE.match(m.group(2))):
                gone.append(m.group(1))
                continue
            if re.match(r"^-{1,2}[\w-]+$", a) and SECRET_NAME.search(a):
                gone.append(a)
                skip = True
                continue
            if SECRET_VALUE.match(a):
                gone.append("(argument)")
                continue
            args.append(a)
        out["args"] = args
    if out.get("url"):
        u = urlparse(str(out["url"]))
        q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)]
        keepq = [(k, v) for k, v in q if not (SECRET_NAME.search(k) or SECRET_VALUE.match(v))]
        gone += [k for k, v in q if (k, v) not in keepq]
        netloc = u.netloc.split("@")[-1]
        if "@" in u.netloc:
            gone.append("(login in the address)")
        out["url"] = urlunparse(u._replace(netloc=netloc, query=urlencode(keepq)))
    return out, sorted(set(gone))


# ---------- the agents ------------------------------------------------------------------------------------------------
AGENTS = {
    "claude": {"name": "Claude Code", "dir": ".claude", "creds": [".credentials.json"],
               "skills": ["skills"], "instructions": ["CLAUDE.md"], "memories": ["memory", "projects/*/memory"],
               "mcp_json": [(".claude.json", "mcpServers")]},
    "codex": {"name": "Codex", "dir": ".codex", "creds": ["auth.json"],
              "skills": ["skills"], "instructions": ["AGENTS.md"], "memories": ["memories"], "mcp_toml": ["config.toml"]},
    "cursor": {"name": "Cursor", "dir": ".cursor", "creds": [],
               "skills": ["skills"], "instructions": ["rules/*.mdc", "rules/*.md"], "memories": [],
               "mcp_json": [("mcp.json", "mcpServers")]},
    "openclaw": {"name": "OpenClaw", "dir": ".openclaw", "creds": ["openclaw.json"],
                 "skills": ["skills", "workspace/skills"], "instructions": ["workspace/SOUL.md", "workspace/AGENTS.md"],
                 "memories": ["workspace/MEMORY.md", "workspace/USER.md", "workspace/memory"], "mcp_json": []},
    "hermes": {"name": "Hermes", "dir": ".hermes", "creds": [".env", "auth.json"],
               "skills": ["skills"], "instructions": ["SOUL.md"], "memories": ["memories"],
               "mcp_yaml": ["config.yaml"]},
}


def _files(base: Path, pats: list[str], exts=(".md", ".mdc", ".txt")) -> list[Path]:
    out: list[Path] = []
    for pat in pats:
        if any(c in pat for c in "*?"):
            cands = sorted(base.glob(pat))
        else:
            cands = [base / pat]
        for c in cands:
            if c.is_dir():
                out += [f for f in sorted(c.iterdir()) if f.is_file() and f.suffix.lower() in exts]
            elif c.is_file():
                out.append(c)
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def _norm_server(raw: dict) -> dict | None:
    from meshpool_harness import importer as IM
    return IM._norm(raw)


def _servers(agent: dict, base: Path, root: Path) -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    for fname, key in agent.get("mcp_json", []):
        p = (root / fname) if fname.startswith(".claude.json") else (base / fname)
        d = _json(p)
        if isinstance(d.get(key), dict):
            found += list(d[key].items())
        if fname == ".claude.json":
            for proj, conf in (d.get("projects") or {}).items():
                if isinstance(conf, dict) and isinstance(conf.get("mcpServers"), dict):
                    found += [(f"{n} ({Path(proj).name})", s) for n, s in conf["mcpServers"].items()]
    for fname in agent.get("mcp_toml", []):
        try:
            from meshpool_harness.importer import toml_loads
            found += list((toml_loads(_read(base / fname) or "").get("mcp_servers") or {}).items())
        except Exception:  # noqa: BLE001 - a broken file is skipped
            pass
    for fname in agent.get("mcp_yaml", []):
        try:
            import yaml
            d = yaml.safe_load(_read(base / fname) or "") or {}
            sv = d.get("mcp_servers") or d.get("mcpServers") or {}
            if isinstance(sv, dict):
                found += list(sv.items())
        except Exception:  # noqa: BLE001
            pass
    return found


def _facts(text: str) -> list[str]:
    """Lasting lines from a notes file: bullets and short paragraphs, no headings or code."""
    out, fence = [], False
    for chunk in re.split(r"\n\s*§\s*\n", text or ""):               # Hermes separates entries with a section sign
        for line in chunk.splitlines():
            t = line.strip()
            if t.startswith("```"):
                fence = not fence
                continue
            if fence or not t or t.startswith("#") or t.startswith("---") or t.startswith("<!--"):
                continue
            t = re.sub(r"^([-*•]|\d+[.)])\s+", "", t).strip()
            t = re.sub(r"[*_`]+", "", t)
            if 8 <= len(t) <= 300:
                out.append(t)
    return out[:MAX_FACTS_PER_FILE]


def _existing_facts() -> set:
    try:
        from meshpool_harness import memstore as MS
        return {MS.norm(f["text"]) for f in MS.load() if f["status"] != "archived"}
    except Exception:  # noqa: BLE001
        return set()


def _existing_mcp() -> set:
    try:
        from meshpool_harness.agent import mcp as M
        return {json.dumps(_norm_server({k: x for k, x in v.items() if k not in ("disabled", "enabled")}) or {}, sort_keys=True)
                for v in M.load_config().values() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001
        return set()


def _existing_skills() -> set:
    try:
        from meshpool_harness import skills as SK
        return {s.get("id") for s in SK.load_all()}
    except Exception:  # noqa: BLE001
        return set()


# ---------- scan -------------------------------------------------------------------------------------------------------
def scan(only: list[str] | None = None) -> dict:
    """Everything found, per agent, with a preview and what was left out. Reads no credential file."""
    _REFUSED.clear()
    h = home()
    have_f, have_m, have_s = _existing_facts(), _existing_mcp(), _existing_skills()
    agents = []
    for aid, a in AGENTS.items():
        if only and aid not in only:
            continue
        base = h / a["dir"]
        if not base.is_dir() and not (aid == "claude" and _present(h / ".claude.json")):
            continue
        items: list[dict] = []
        for rel in a["skills"]:
            d = base / rel
            if not d.is_dir():
                continue
            for sk in sorted(d.iterdir())[:MAX_SKILLS_PER_AGENT]:
                md = sk / "SKILL.md"
                if sk.is_dir() and md.is_file():
                    head = _read(md)
                    desc = ""
                    m = re.search(r"(?im)^description:\s*(.+)$", head)
                    if m:
                        desc = m.group(1).strip().strip("\"'")
                    items.append({"key": f"skill:{aid}:{sk.name}", "kind": "skill", "name": sk.name, "path": str(sk),
                                  "preview": desc or head[:200].replace("\n", " "), "exists": _slug(sk.name) in have_s,
                                  "files": sum(1 for _ in sk.rglob("*") if _.is_file())})
        for name, raw in _servers(a, base, h):
            spec = _norm_server(raw if isinstance(raw, dict) else {})
            if not spec:
                continue
            clean, gone = strip_secrets(spec)
            sig = json.dumps(clean, sort_keys=True)
            what = ("Connects to " + urlparse(clean["url"]).netloc) if clean.get("url") else \
                "Runs " + " ".join([clean.get("command", "")] + clean.get("args", []))[:80]
            broken = bool(clean.get("command")) and not shutil.which(str(clean["command"]))
            items.append({"key": f"mcp:{aid}:{name}", "kind": "mcp", "name": str(name), "spec": clean, "preview": what,
                          "stripped": gone, "exists": sig in have_m,
                          "default": not (broken or gone), "broken": broken})
        for f in _files(base, a["instructions"]):
            t = _read(f).strip()
            if t:
                items.append({"key": f"instructions:{aid}:{f.name}", "kind": "instructions", "name": f.name,
                              "path": str(f), "preview": t[:200].replace("\n", " "), "chars": len(t),
                              "exists": False})
        for f in _files(base, a["memories"]):
            try:
                rel = f.relative_to(base).as_posix()           # the path, not just the name: several MEMORY.md files
            except ValueError:
                rel = f.name
            for i, fact in enumerate(_facts(_read(f))):
                items.append({"key": f"memory:{aid}:{rel}:{i}", "kind": "memory", "name": fact[:60],
                              "path": str(f), "text": fact, "preview": fact, "exists": _fold(fact) in have_f})
        creds = [str(base / c) for c in a["creds"] if _present(base / c)]
        agents.append({"id": aid, "name": a["name"], "path": str(base), "items": items,
                       "counts": {k: sum(1 for i in items if i["kind"] == k) for k in ("skill", "mcp", "memory", "instructions")},
                       "credentials_not_read": creds})
    return {"agents": agents, "refused": sorted(set(_REFUSED))}


def _slug(name: str) -> str:
    from meshpool_harness import skills as SK
    return SK.slug(name)


def _fold(t: str) -> str:
    from meshpool_harness import memstore as MS
    return MS.norm(t)


def _scan_text(text: str) -> tuple[str, int]:
    """Instruction text without lines that hold a secret value or something the memory filter refuses."""
    from meshpool_harness import assistant as AS
    keep, dropped = [], 0
    for ln in text.splitlines():
        if AS.BAD.search(ln) or any(SECRET_VALUE.match(w.strip("\"'`,;:()[]")) for w in ln.split()):
            dropped += 1
        else:
            keep.append(ln)
    return "\n".join(keep), dropped


# ---------- apply ------------------------------------------------------------------------------------------------------
def apply(keys: list[str], dry_run: bool = False, only: list[str] | None = None) -> dict:
    """Write the chosen items (or, with dry_run, say what would be written). Returns the report."""
    from meshpool_harness import importer as IM
    want = set(keys or [])
    found = scan(only)
    rep = {"dry_run": bool(dry_run), "skills": [], "mcp": [], "memory": 0, "instructions": 0, "skipped": [],
           "stripped": [], "credentials_not_read": [c for a in found["agents"] for c in a["credentials_not_read"]],
           "errors": []}
    for a in found["agents"]:
        for it in a["items"]:
            if it["key"] not in want:
                continue
            if it["exists"]:
                rep["skipped"].append(f"{it['name']}: already in Meshpool")
                continue
            try:
                if it["kind"] == "skill":
                    rep["skills"].append(it["name"] if dry_run else IM.import_skill(it["path"]))
                elif it["kind"] == "mcp":
                    if it["stripped"]:
                        rep["stripped"].append({"server": it["name"], "agent": a["name"], "names": it["stripped"]})
                    rep["mcp"].append(it["name"] if dry_run else IM.import_mcp(
                        it["name"], it["spec"], f"Imported from {a['name']}. Switched off until you review it: add any keys it needs, then turn it on in Settings."))
                elif it["kind"] == "memory":
                    if not dry_run:
                        from meshpool_harness import memstore as MS
                        _, made = MS.add(it["text"], kind="stated", status="pending", by="meshpool",
                                         source={"type": "import", "agent": a["id"], "file": it["path"]})
                        if not made:
                            rep["skipped"].append(f"{it['name']}: already known")
                            continue
                    rep["memory"] += 1
                elif it["kind"] == "instructions":
                    if not dry_run:
                        from meshpool_harness import assistant as AS
                        body, dropped = _scan_text(_read(Path(it["path"])).strip()[:MAX_INSTRUCTIONS])
                        if dropped:
                            rep["skipped"].append(f"{it['name']}: {dropped} line(s) that looked like secrets were left out")
                        if not body.strip():
                            continue
                        cur = AS.instructions()
                        head = f"From {a['name']} ({it['name']}):"
                        if head not in cur:
                            AS.set_instructions((cur + "\n\n" if cur else "") + head + "\n" + body)
                    rep["instructions"] += 1
            except (OSError, ValueError) as e:
                rep["errors"].append(f"{it['name']}: {e}")
    rep["refused"] = found["refused"]
    return rep


# ---------- command line: meshpool import agents [--dry-run] [--yes] [--from claude,codex] --------------------------------
def cli(args: list[str]) -> int:
    dry = "--dry-run" in args
    yes = "--yes" in args or "-y" in args
    only = None
    for i, a in enumerate(args):
        if a == "--from" and i + 1 < len(args):
            only = [x.strip() for x in args[i + 1].split(",") if x.strip()]
    found = scan(only)
    if not found["agents"]:
        print("No other agents found on this computer.")
        return 0
    keys: list[str] = []
    for a in found["agents"]:
        c = a["counts"]
        print(f"\n{a['name']}  ({a['path']})\n  {c['skill']} skills, {c['mcp']} tools, {c['memory']} memories, "
              f"{c['instructions']} instruction files")
        for it in a["items"]:
            mark = "already here" if it["exists"] else "new"
            extra = f"  (leaves out: {', '.join(it['stripped'])})" if it.get("stripped") else ""
            print(f"  [{it['kind']}] {it['name'][:60]}: {mark}{extra}")
            if not it["exists"]:
                keys.append(it["key"])
        for cred in a["credentials_not_read"]:
            print(f"  not read: {Path(cred).name}")
    if not keys:
        print("\nNothing new to import.")
        return 0
    if dry:
        r = apply(keys, dry_run=True, only=only)
        print(f"\nDry run: would import {len(r['skills'])} skills, {len(r['mcp'])} tools, {r['memory']} memories "
              f"(to review), {r['instructions']} instruction files. Nothing was written.")
        return 0
    if not yes:
        if not sys.stdin.isatty() or input(f"\nImport these {len(keys)} items? [y/N] ").strip().lower() != "y":
            print("Nothing imported. Run again with --yes to import, or --dry-run to preview.")
            return 0
    r = apply(keys, only=only)
    print(f"\nImported {len(r['skills'])} skills, {len(r['mcp'])} tools, {r['memory']} memories (to review in Memory), "
          f"{r['instructions']} instruction files.")
    for s in r["stripped"]:
        print(f"  {s['server']} came over without: {', '.join(s['names'])}. Add them in Settings.")
    for e in r["errors"]:
        print("  problem:", e)
    return 0
