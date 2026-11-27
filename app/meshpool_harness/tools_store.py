"""Saved tools: what the agent writes mid-task and keeps (the save_tool agent tool).

A saved tool is a folder in <meshpool home>/tools/<name>/ with one of three shapes, and a tests/ folder:
  skill   SKILL.md (instructions, maybe with helper files)
  script  scripts/... plus a SKILL.md that says how to run them (written for it when missing)
  mcp     server.py, server.js or server.mjs: a small local MCP server, added to mcp.json switched on

Before a tool is kept: its tests run (in the sandbox when this computer has one, without the user's keys in the
environment) and must pass, and two scans run over every file: skills.risks and the scan rules ported from Hermes
Agent's skills_guard (MIT, see THIRD_PARTY_NOTICES.md). A "dangerous" finding or any skills.risks hit refuses the save;
"caution" findings are kept in provenance.json and shown. provenance.json records where the tool came from, its version
and the sha256 of its files.

Saved tools never go into the system prompt in full: skill and script tools are found with find_skill, MCP tools with
mcp_find_tools. A tool that fails later is marked failing (provenance.json "status") and shown as failing, never
dropped. uninstall() removes the folder and its mcp.json entry.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from meshpool_harness import config as C

MARK = "Saved by meshpool (tools)."           # starts the mcp.json description of every server saved here
KINDS = ("skill", "script", "mcp")
# From Hermes Agent's skill_manager_tool.py (MIT): name pattern and size limits.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MAX_NAME = 64
MAX_DESC = 1024
MAX_FILE_BYTES = 1_048_576
MAX_FILES = 50
TEST_TIMEOUT_S = 120
SERVER_FILES = ("server.py", "server.js", "server.mjs")

# ---------- scan rules, ported from Hermes Agent tools/skills_guard.py (MIT, Copyright (c) 2025 Nous Research) ------
# (pattern, id, severity, what it does). critical makes the verdict "dangerous" (refused), high makes it "caution"
# (kept and shown), medium and low are notes only. A subset: the rules that matter for code a tool runs.
_LOOPBACK = r"(?![^\n]*https?://(?:localhost|127\.0\.0\.1|\[::1\]))"
GUARD_RULES = [
    (rf"curl\s+{_LOOPBACK}[^\n]*\$\{{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)S?\b", "env_exfil_curl", "critical",
     "sends a secret from the environment with curl"),
    (rf"wget\s+{_LOOPBACK}[^\n]*\$\{{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)S?\b", "env_exfil_wget", "critical",
     "sends a secret from the environment with wget"),
    (rf"requests\.(get|post|put|patch)\s*\({_LOOPBACK}[^\n]*(KEY|TOKEN|SECRET|PASSWORD)", "env_exfil_requests",
     "critical", "sends a secret with requests"),
    (rf"httpx?\.(get|post|put|patch)\s*\({_LOOPBACK}[^\n]*(KEY|TOKEN|SECRET|PASSWORD)", "env_exfil_httpx", "critical",
     "sends a secret with httpx"),
    (r"cat\s+(?!>)[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)", "read_secrets_file", "critical",
     "reads a file of saved secrets"),
    (r"\$HOME/\.(ssh|aws|gnupg|kube|docker)|~/\.(ssh|aws|gnupg|kube|docker)", "secret_dir_access", "high",
     "looks in a folder of keys or logins"),
    (r"printenv|env\s*\|", "dump_all_env", "high", "dumps every environment variable"),
    (r"(?<![-/])\b(dig|nslookup)\s+[^\n]*\$", "dns_exfil", "critical", "puts data into a DNS lookup"),
    (r">\s*/tmp/[^\s]*\s*&&\s*(curl|wget|nc|python)", "tmp_staging", "critical", "writes to /tmp, then sends it"),
    (r"ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+instructions", "prompt_injection_ignore", "critical",
     "tells the agent to ignore its instructions"),
    (r"disregard\s+(?:\w+\s+)*(your|all|any)\s+(?:\w+\s+)*(instructions|rules|guidelines)", "disregard_rules",
     "critical", "tells the agent to drop its rules"),
    (r"system\s+(?:\w+\s+)*prompt\s+(?:\w+\s+)*override", "sys_prompt_override", "critical",
     "tries to override the system prompt"),
    (r"(when|if)\s+no\s*one\s+is\s+(watching|looking)", "conditional_deception", "high",
     "behaves differently when nobody watches"),
    (r"rm\s+-rf\s+/(\s|$)", "destructive_root_rm", "critical", "deletes from the root folder"),
    (r"rm\s+(-[^\s]*)?r.*\$HOME|\brmdir\s+.*\$HOME", "destructive_home_rm", "critical", "deletes the home folder"),
    (r">\s*/etc/", "system_overwrite", "critical", "overwrites a system file"),
    (r"\bmkfs\b", "format_filesystem", "critical", "formats a disk"),
    (r"\bdd\s+.*if=.*of=/dev/", "disk_overwrite", "critical", "writes straight to a disk"),
    (r"shutil\.rmtree\s*\(\s*[\"'](/|~)", "python_rmtree", "high", "deletes a folder by absolute path"),
    (r"\bcrontab\b", "persistence_cron", "medium", "changes scheduled jobs"),
    (r"authorized_keys", "ssh_backdoor", "critical", "changes who may log in by SSH"),
    (r"/etc/sudoers|visudo|NOPASSWD", "sudoers_mod", "critical", "changes who may use sudo"),
    (r"launchctl\s+load|LaunchAgents|LaunchDaemons", "macos_launchd", "high", "starts itself at login on macOS"),
    (r"\bnc\s+-[lp]|ncat\s+-[lp]|\bsocat\b", "reverse_shell", "critical", "opens a listener (a reverse shell)"),
    (r"/bin/(ba)?sh\s+-i\s+.*>/dev/tcp/", "bash_reverse_shell", "critical", "opens a reverse shell"),
    (r"python[23]?\s+-c\s+[\"']import\s+socket", "python_socket_oneliner", "critical", "opens a raw socket"),
    (r"\bngrok\b|\blocaltunnel\b|\bserveo\b|\bcloudflared\b", "tunnel_service", "high",
     "opens a tunnel from the internet"),
    (r"webhook\.site|requestbin\.com|pipedream\.net|hookbin\.com", "exfil_service", "high",
     "names a known data-collection service"),
    (r"base64\s+(-d|--decode)\s*\|", "base64_decode_pipe", "high", "decodes and runs hidden text"),
    (r"\beval\s*\(\s*[\"']", "eval_string", "high", "runs code from a string"),
    (r"\bexec\s*\(\s*[\"']", "exec_string", "high", "runs code from a string"),
    (r"echo\s+[^\n]*\|\s*(bash|sh|python|perl|ruby|node)", "echo_pipe_exec", "critical",
     "pipes text into an interpreter"),
    (r"getattr\s*\(\s*__builtins__", "python_getattr_builtins", "high", "reaches Python builtins indirectly"),
    (r"__import__\s*\(\s*[\"']os[\"']\s*\)", "python_import_os", "high", "imports os indirectly"),
    (r"chr\s*\(\s*\d+\s*\)\s*\+\s*chr\s*\(\s*\d+", "chr_building", "high", "builds text from character codes"),
    (r"os\.system\s*\(", "python_os_system", "high", "runs a shell command with os.system"),
    (r"/etc/passwd|/etc/shadow", "system_passwd_access", "critical", "reads the system password files"),
    (r"xmrig|stratum\+tcp|coinhive|cryptonight", "crypto_mining", "critical", "mines cryptocurrency"),
    (r"curl\s+[^\n]*\|\s*(ba)?sh|wget\s+[^\n]*-O\s*-\s*\|\s*(ba)?sh|curl\s+[^\n]*\|\s*python", "pipe_to_shell",
     "critical", "downloads and runs a script"),
    (r"pip\s+install\s+(?!-r\s)(?!.*==)", "unpinned_pip_install", "medium", "installs a package without a version"),
    (r"npm\s+install\s+(?!.*@\d)", "unpinned_npm_install", "medium", "installs a package without a version"),
    (r"\bsudo\b", "sudo_usage", "high", "uses sudo"),
    (r"setuid|setgid|cap_setuid|chmod\s+[u+]?s\b", "setuid", "critical", "sets the setuid bit"),
    (r"(?:api[_-]?key|token|secret|password)\s*[=:]\s*[\"'][A-Za-z0-9+/=_-]{20,}", "hardcoded_secret", "critical",
     "has a key or password written into it"),
    (r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----", "embedded_private_key", "critical", "has a private key in it"),
    (r"ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{80,}|sk-ant-[A-Za-z0-9_-]{90,}|AKIA[0-9A-Z]{16}",
     "leaked_token", "critical", "has an access token in it"),
    (r"(include|output|print|send|share)\s+(?:\w+\s+)*(conversation|chat\s+history|previous\s+messages)",
     "context_exfil", "high", "tells the agent to send the conversation"),
]
_RULES = [(re.compile(p, re.I), rid, sev, what) for p, rid, sev, what in GUARD_RULES]
_SCAN_EXT = {".md", ".txt", ".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".json", ".yaml", ".yml", ".toml",
             ".ps1", ".rb", ".pl", ""}


class ToolRefused(ValueError):
    """save_tool said no: the reason is plain text the agent can act on."""


# ---------- where tools live ----------------------------------------------------------------------------------------

def folder() -> Path:
    return C.home() / "tools"


def tool_dir(name: str) -> Path:
    return folder() / check_name(name)


def check_name(name: str) -> str:
    n = str(name or "").strip()
    if not n or len(n) > MAX_NAME or not NAME_RE.match(n) or n in (".", ".."):
        raise ToolRefused(f"Name the tool with lowercase letters, digits, '.', '_' or '-' (at most {MAX_NAME}), "
                          "starting with a letter or digit.")
    return n


def _prov_path(name: str) -> Path:
    return tool_dir(name) / "provenance.json"


def provenance(name: str) -> dict:
    try:
        d = json.loads(_prov_path(name).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_prov(name: str, d: dict) -> None:
    p = _prov_path(name)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def names() -> list[str]:
    try:
        return sorted(d.name for d in folder().iterdir() if d.is_dir() and (d / "provenance.json").is_file())
    except OSError:
        return []


def listing() -> list[dict]:
    """Every saved tool with its state, for Settings and for search results: failing ones are shown, never hidden."""
    out = []
    for n in names():
        p = provenance(n)
        out.append({"name": n, "kind": p.get("kind", ""), "description": p.get("description", ""),
                    "status": p.get("status", "ok"), "why": p.get("why", ""), "version": p.get("version", ""),
                    "server": p.get("server", ""), "source": p.get("source", ""), "sha256": p.get("sha256", "")})
    return out


def skill_rows() -> list[dict]:
    """Skill and script tools as skill rows (skills.search and skills.get read them). Not part of skills.load_all, so
    they never go into the prompt's skills list."""
    from meshpool_harness import skills as SK
    rows = []
    for t in listing():
        if t["kind"] not in ("skill", "script"):
            continue
        d = tool_dir(t["name"])
        try:
            row = SK._skill_row(str(d), str(d / "SKILL.md"))
        except OSError:
            continue
        row.update(id=t["name"], saved=True, status=t["status"], why=t["why"])
        rows.append(row)
    return rows


def server_tool(server: str) -> str:
    """The saved tool behind an mcp.json server name, or ""."""
    for t in listing():
        if t["server"] and t["server"] == server:
            return t["name"]
    return ""


# ---------- scans ---------------------------------------------------------------------------------------------------

def _scan_files(root: Path) -> list[Path]:
    return sorted(f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in _SCAN_EXT
                  and "__pycache__" not in f.parts and "node_modules" not in f.parts)[:MAX_FILES * 2]


def guard_scan(root) -> list[dict]:
    """The ported Hermes rules over every text file: [{"id", "severity", "file", "line", "what"}]."""
    root = Path(root)
    out = []
    for f in _scan_files(root):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = f.relative_to(root).as_posix()
        for i, line in enumerate(text.splitlines(), 1):
            for rx, rid, sev, what in _RULES:
                if rx.search(line):
                    out.append({"id": rid, "severity": sev, "file": rel, "line": i, "what": what})
    return out[:80]


def verdict(findings: list[dict]) -> str:
    """Hermes' rule: a critical finding is dangerous, a high one is caution, the rest are notes."""
    sev = {f["severity"] for f in findings}
    return "dangerous" if "critical" in sev else "caution" if "high" in sev else "safe"


# ---------- tests ---------------------------------------------------------------------------------------------------

def _python() -> str:
    return sys.executable or "python3"


def _has_pytest(py: str) -> bool:
    try:
        r = subprocess.run([py, "-c", "import pytest"], capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def test_command(root: Path) -> list[str]:
    """How a tool's tests run: Python test_*.py files with pytest (unittest when pytest is missing), Node *.test.js or
    *.test.mjs files with node --test. Raises ToolRefused for any other kind of test."""
    t = root / "tests"
    py = sorted(p.name for p in t.glob("test_*.py")) if t.is_dir() else []
    js = sorted(p.name for p in t.iterdir() if re.search(r"\.test\.(m?js|cjs)$", p.name)) if t.is_dir() else []
    if py:
        exe = _python()
        return ([exe, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"] if _has_pytest(exe)
                else [exe, "-m", "unittest", "discover", "-s", "tests", "-t", "."])
    if js:
        from meshpool_harness import bundled
        node = bundled.node_exe()
        if not node:
            raise ToolRefused("Node is not available here, so Node tests cannot run. Write the tests in Python.")
        return [node, "--test", *(f"tests/{n}" for n in js)]
    raise ToolRefused("Add tests: tests/test_<name>.py (Python) or tests/<name>.test.js (Node), with at least one "
                      "real check of what the tool does.")


def run_tests(root, timeout: int = TEST_TIMEOUT_S) -> dict:
    """Run a tool's tests in a scratch copy of its folder: in the sandbox, offline, when this computer has one, and
    always without the user's keys in the environment. {"ok", "output", "sandboxed", "command"}."""
    from meshpool_harness.core import sandbox as SB
    src = Path(root)
    with tempfile.TemporaryDirectory(prefix="mh-tooltest-") as tmp:
        work = Path(tmp) / src.name
        shutil.copytree(src, work, ignore=shutil.ignore_patterns("__pycache__", "provenance.json"))
        argv = test_command(work)
        boxed = SB.available()
        run = SB.wrap(argv, work, mode="ask", offline=True) if boxed else argv
        env = C.scrub_env(dict(os.environ))
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(work), MESHPOOL_TOOL_DIR=str(work))
        try:
            r = subprocess.run(run, cwd=str(work), env=env, capture_output=True, text=True, errors="replace",
                               timeout=timeout, stdin=subprocess.DEVNULL)
            out, ok = (r.stdout or "") + (r.stderr or ""), r.returncode == 0
        except subprocess.TimeoutExpired:
            out, ok = f"The tests ran longer than {timeout} s and were stopped.", False
        except OSError as e:
            out, ok = f"The tests could not start: {e}", False
    # "no tests ran" is not a pass
    if ok and re.search(r"\bno tests ran\b|Ran 0 tests|# tests 0\b", out):
        ok, out = False, out + "\nNo test ran. Add at least one real test."
    return {"ok": ok, "output": out[-4000:], "sandboxed": boxed, "command": " ".join(argv[1:])}


# ---------- saving --------------------------------------------------------------------------------------------------

def _rel_ok(rel: str) -> str:
    r = str(rel or "").replace("\\", "/").strip()
    parts = [p for p in r.split("/") if p]
    if not parts or r.startswith("/") or re.match(r"^[A-Za-z]:", r) or any(p in (".", "..") for p in parts) \
            or any(p.startswith(".") for p in parts) or parts[-1] == "provenance.json":
        raise ToolRefused(f"{rel!r} is not a plain relative file path inside the tool's folder.")
    return "/".join(parts)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(root) -> tuple[str, dict]:
    """(sha256 over every file, {relative path: sha256}), provenance.json and caches left out."""
    root = Path(root)
    files = {}
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.name != "provenance.json" and "__pycache__" not in f.parts:
            files[f.relative_to(root).as_posix()] = _sha(f.read_bytes())
    whole = hashlib.sha256("".join(f"{k}\0{v}\n" for k, v in sorted(files.items())).encode()).hexdigest()
    return whole, files


def _skill_md(name: str, description: str, body: str) -> str:
    from meshpool_harness import skills as SK
    return SK.front_matter(name=name, description=description) + "\n" + body.strip() + "\n"


def _script_skill(name: str, description: str, root: Path) -> str:
    scripts = sorted(p.relative_to(root).as_posix() for p in (root / "scripts").rglob("*") if p.is_file()) \
        if (root / "scripts").is_dir() else []
    lines = [f"# {name}", "", description, "", "Run a script from this tool's folder (shown when the skill loads):"]
    lines += [f"- `{s}`" for s in scripts]
    return _skill_md(name, description, "\n".join(lines))


def _server_spec(root: Path, final: Path, description: str) -> dict:
    srv = next(n for n in SERVER_FILES if (root / n).is_file())
    if srv.endswith(".py"):
        cmd = _python()
    else:
        from meshpool_harness import bundled
        cmd = bundled.node_exe() or "node"
    return {"command": cmd, "args": [str(final / srv)], "description": f"{MARK} {description}"[:600]}


def stage(name: str, kind: str, description: str, files: dict, tests: dict, dest: Path) -> Path:
    """Write a tool's files into dest (a scratch folder), checked for shape and size. Returns dest."""
    if kind not in KINDS:
        raise ToolRefused(f"kind must be one of {', '.join(KINDS)}.")
    description = " ".join(str(description or "").split())
    if not description or len(description) > MAX_DESC:
        raise ToolRefused(f"Give a one-line description of what the tool does (at most {MAX_DESC} characters).")
    if not isinstance(files, dict) or not isinstance(tests, dict):
        raise ToolRefused("files and tests are objects of {relative path: file text}.")
    if not tests:
        raise ToolRefused("A tool is kept only with tests that pass. Add tests (tests/test_<name>.py).")
    items = [(_rel_ok(k), v) for k, v in files.items()]
    items += [(_rel_ok(k if str(k).replace("\\", "/").startswith("tests/") else f"tests/{k}"), v)
              for k, v in tests.items()]
    if len(items) > MAX_FILES:
        raise ToolRefused(f"A tool holds at most {MAX_FILES} files.")
    dest.mkdir(parents=True, exist_ok=True)
    for rel, text in items:
        data = str(text if text is not None else "").encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ToolRefused(f"{rel} is larger than {MAX_FILE_BYTES // 1024} KB.")
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    if kind == "skill" and not (dest / "SKILL.md").is_file():
        raise ToolRefused("A skill tool needs a SKILL.md with what to do, step by step.")
    if kind == "script":
        if not (dest / "scripts").is_dir():
            raise ToolRefused("A script tool keeps its scripts under scripts/.")
        if not (dest / "SKILL.md").is_file():
            (dest / "SKILL.md").write_text(_script_skill(name, description, dest), encoding="utf-8")
    if kind == "mcp" and not any((dest / n).is_file() for n in SERVER_FILES):
        raise ToolRefused(f"An MCP tool needs its server as one of {', '.join(SERVER_FILES)} at the top of the folder.")
    if (dest / "SKILL.md").is_file():
        from meshpool_harness import skills as SK
        meta, body = SK.parse((dest / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
        if not meta.get("name") or not meta.get("description"):
            (dest / "SKILL.md").write_text(_skill_md(name, description, body), encoding="utf-8")
    return dest


def check(root) -> dict:
    """Both scans over a staged tool. {"refuse": [reasons], "caution": [notes], "findings": [...]}."""
    from meshpool_harness import skills as SK
    root = Path(root)
    found = guard_scan(root)
    refuse = [f"{f['file']}:{f['line']}: {f['what']}" for f in found if f["severity"] == "critical"]
    refuse += [r for r in SK.risks(root) if r not in refuse]
    caution = [f"{f['file']}:{f['line']}: {f['what']}" for f in found if f["severity"] == "high"]
    return {"refuse": list(dict.fromkeys(refuse))[:20], "caution": caution[:20], "findings": found,
            "verdict": "dangerous" if refuse else verdict(found)}


def save(name: str, kind: str, description: str, files: dict, tests: dict, source: str = "",
         version: str = "", overwrite: bool = False, register=None) -> dict:
    """Check, test and keep a tool. Raises ToolRefused with what to fix. Returns its provenance.

    register(name, spec) adds the mcp.json entry for an MCP tool (default: mcp_entry)."""
    name = check_name(name)
    final = folder() / name
    if final.exists() and not overwrite:
        raise ToolRefused(f"A saved tool named {name} exists. Pick another name, or pass overwrite to replace it.")
    existing = provenance(name) if final.exists() else {}
    with tempfile.TemporaryDirectory(prefix="mh-toolsave-") as tmp:
        st = stage(name, kind, description, files, tests, Path(tmp) / name)
        scan = check(st)
        if scan["refuse"]:
            raise ToolRefused("Not saved: the scan found risky code. Remove it and try again.\n- "
                              + "\n- ".join(scan["refuse"]))
        res = run_tests(st)
        if not res["ok"]:
            raise ToolRefused("Not saved: the tests failed. Fix the tool or the tests and try again.\n"
                              + res["output"][-2500:])
        whole, per = digest(st)
        server = ""
        spec = None
        if kind == "mcp":
            server = existing.get("server") or name
            spec = _server_spec(st, final, description)
            taken = _servers().get(server)
            if isinstance(taken, dict) and not str(taken.get("description", "")).startswith(MARK):
                raise ToolRefused(f"An MCP server named {server} exists already. Pick another tool name.")
        folder().mkdir(parents=True, exist_ok=True)
        old = final.with_name(f".{name}.old")
        if final.exists():
            shutil.rmtree(old, ignore_errors=True)
            final.rename(old)
        shutil.copytree(st, final)
        shutil.rmtree(old, ignore_errors=True)
    prov = {"name": name, "kind": kind, "description": " ".join(description.split()),
            "source": source or "written by the agent", "version": version or "1", "sha256": whole, "files": per,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": "ok", "why": "",
            "tests": {"passed": True, "command": res["command"], "sandboxed": res["sandboxed"]},
            "caution": scan["caution"], "server": server}
    _write_prov(name, prov)
    if spec is not None:
        (register or mcp_entry)(server, spec)
    return prov


# ---------- mcp.json ------------------------------------------------------------------------------------------------

def _servers() -> dict:
    from meshpool_harness.agent import mcp as M
    try:
        return M.load_config()
    except (OSError, ValueError):
        return {}


def mcp_entry(server: str, spec: dict) -> None:
    from meshpool_harness.agent import mcp as M

    def put(servers):
        servers[server] = dict(spec)
    M.edit_config(put)


def _drop_entry(server: str) -> None:
    from meshpool_harness.agent import mcp as M
    if not server or not M.config_file().exists():
        return

    def drop(servers):
        s = servers.get(server)
        if isinstance(s, dict) and str(s.get("description", "")).startswith(MARK):
            del servers[server]
    M.edit_config(drop, create=False)


# ---------- after saving --------------------------------------------------------------------------------------------

def uninstall(name: str) -> None:
    """Remove a saved tool: its folder and its mcp.json entry (only one this store added)."""
    name = check_name(name)
    p = provenance(name)
    _drop_entry(p.get("server") or (name if p.get("kind") == "mcp" else ""))
    d = folder() / name
    if not d.exists():
        raise ToolRefused(f"No saved tool named {name}.")
    shutil.rmtree(d)


def mark_failing(name: str, why: str) -> bool:
    """A saved tool that failed in use: marked failing with the reason (shown in search results and Settings). Never
    removed. Returns True when the status changed."""
    p = provenance(name)
    if not p:
        return False
    changed = p.get("status") != "failing"
    p.update(status="failing", why=" ".join(str(why or "it failed").split())[:300],
             failed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    _write_prov(name, p)
    return changed


def mark_ok(name: str) -> bool:
    p = provenance(name)
    if not p or p.get("status") != "failing":
        return False
    p.update(status="ok", why="")
    p.pop("failed_at", None)
    _write_prov(name, p)
    return True


def recheck(name: str) -> dict:
    """Run a saved tool's tests again and record the result (and whether its files still match the saved sha256)."""
    d = tool_dir(name)
    p = provenance(name)
    whole, _ = digest(d)
    if p.get("sha256") and whole != p["sha256"]:
        mark_failing(name, "its files changed after it was saved")
        return {"ok": False, "output": "The files changed after the tool was saved."}
    res = run_tests(d)
    (mark_ok if res["ok"] else lambda n: mark_failing(n, "its tests fail: " + res["output"][-200:]))(name)
    return res
