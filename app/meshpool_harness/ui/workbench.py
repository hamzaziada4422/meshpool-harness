"""The app's workbench services: projects (folders on this computer), models from the Meshpool endpoint, the file
browser, Git, a terminal, and usage totals. server.py exposes them as /api/... routes."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness.core import http as _mhttp

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ======================================== projects ================================================================
def _projects_file() -> Path:
    return C.home() / "projects.json"


def projects() -> list[dict]:
    try:
        rows = json.loads(_projects_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    out, seen = [], set()
    for r in rows:
        p = str(Path(r.get("path", "")).expanduser())
        if p in seen:
            continue
        seen.add(p)
        out.append({"path": p, "name": r.get("name") or Path(p).name or p, "added": r.get("added", 0),
                    "exists": Path(p).is_dir(), "managed": is_managed(p)})
    return out


_PROJ_LOCK = threading.RLock()                 # add, remove and rename read, change and write the list


def _projects_locked(fn):
    def run(*a, **kw):
        with _PROJ_LOCK:
            return fn(*a, **kw)
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


def _save_projects(rows: list[dict]):
    f = _projects_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps([{k: r[k] for k in ("path", "name", "added")} for r in rows], indent=1), encoding="utf-8")
    C.replace_file(tmp, f)                       # never a half-written file: one was left as invalid JSON


@_projects_locked
def add_project(path: str, name: str = "") -> dict:
    p = Path(path.strip().strip('"')).expanduser()
    if not p.is_dir():
        raise ValueError(f"Folder not found: {path}")
    p = p.resolve()
    rows = projects()
    for r in rows:
        if r["path"] == str(p):
            return r
    r = {"path": str(p), "name": name or p.name or str(p), "added": time.time(), "exists": True}
    rows.insert(0, r)
    _save_projects(rows)
    return r


@_projects_locked
def remove_project(path: str):
    _save_projects([r for r in projects() if r["path"] != path])


@_projects_locked
def rename_project(path: str, name: str):
    rows = projects()
    for r in rows:
        if r["path"] == path:
            r["name"] = name.strip() or r["name"]
    _save_projects(rows)


def known_roots() -> list[Path]:
    from meshpool_harness.ui import agent_api as A
    from meshpool_harness.ui.desktop import default_project
    roots = [Path(r["path"]) for r in projects()]
    d = default_project()
    try:
        d.mkdir(parents=True, exist_ok=True)       # the built-in "Meshpool Projects" folder is always usable
        roots.append(d)
    except OSError:
        pass
    with A.LOCK:
        roots += [Path(s.workdir) for s in A.LIVE.values()]
    try:                                           # folders linked to a project
        from meshpool_harness import project_store as PS
        roots += [Path(x) for x in PS.linked_paths()]
    except Exception:  # noqa: BLE001
        pass
    return roots


def check_root(root: str) -> Path:
    """Only folders the user added as projects (or a live session's folder) are browsable from the page."""
    p = Path(root).expanduser().resolve()
    from meshpool_harness import project_store as PS
    if PS.is_scratch(str(p)):                      # a chat's own private folder
        return p
    if not any(p == r.resolve() for r in known_roots()):
        raise ValueError("That folder is not one of your projects.")
    return p


def _inside(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise ValueError("Path is outside the project.")
    return p


# ======================================== models ==================================================================
_MODELS: dict = {"t": 0, "key": "", "rows": [], "error": ""}


_VENDORS = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google", "deepseek": "DeepSeek",
            "qwen": "Qwen", "meta-llama": "Meta", "meta": "Meta", "mistralai": "Mistral", "mistral": "Mistral",
            "x-ai": "xAI", "xai": "xAI", "moonshotai": "Moonshot", "moonshot": "Moonshot", "z-ai": "Zhipu",
            "zhipu": "Zhipu", "minimax": "MiniMax", "amazon": "Amazon", "cohere": "Cohere", "nvidia": "NVIDIA"}


def _provider_of(mid: str, owned: str) -> str:
    if "/" in mid:
        v = mid.split("/", 1)[0]
        return _VENDORS.get(v.lower(), v)
    m = mid.lower()
    for k, v in (("gpt", "OpenAI"), ("o1", "OpenAI"), ("o3", "OpenAI"), ("o4", "OpenAI"), ("claude", "Anthropic"),
                 ("gemini", "Google"), ("gemma", "Google"), ("deepseek", "DeepSeek"), ("qwen", "Qwen"),
                 ("llama", "Meta"), ("mistral", "Mistral"), ("codestral", "Mistral"), ("grok", "xAI"),
                 ("kimi", "Moonshot"), ("glm", "Zhipu"), ("minimax", "MiniMax"), ("nova", "Amazon")):
        if m.startswith(k):
            return v
    if owned and owned not in ("system", "organization-owner", "user"):
        return _VENDORS.get(owned.lower(), owned)
    return "Other"


def models(refresh: bool = False) -> dict:
    C.load_into_env(override=True)
    base, key, _src = __import__("meshpool_harness.providers", fromlist=["x"]).endpoint_pair()
    cache_key = base + "|" + key[-6:]
    vals = C.read()
    fav = [m for m in (vals.get("MH_FAVORITE_MODELS") or "").split(",") if m]
    default = vals.get("MH_MODEL") or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
    if not refresh and _MODELS["key"] == cache_key and time.time() - _MODELS["t"] < 300 and _MODELS["rows"]:
        rows, err = _MODELS["rows"], ""
    elif not key and not __import__("meshpool_harness.providers", fromlist=["x"]).keyless_ok(base):
        rows, err = [], "Add your API key to see your models."
    else:
        rows, err = [], ""
        try:
            from meshpool_harness import providers as _PV
            url, auth = _PV.chat_target(base, key, "", "models")     # Azure: /openai/models?api-version=, api-key
            req = urllib.request.Request(url, headers={**auth, "User-Agent": "Meshpool"})
            with _mhttp.open(req, 20) as r:
                data = json.loads(r.read().decode("utf-8"))
            items = data.get("data") if isinstance(data, dict) else data
            for it in items or []:
                mid = it.get("id") if isinstance(it, dict) else str(it)
                if not mid:
                    continue
                caps = []
                meta = it if isinstance(it, dict) else {}
                arch = meta.get("architecture") or {}
                mods = (arch.get("input_modalities") or meta.get("input_modalities") or meta.get("modalities") or [])
                if "image" in mods or re.search(r"vision|-vl\b|-vl-|gpt-4o|gpt-4\.1|gpt-5|claude|gemini|llava|pixtral", mid, re.I):
                    caps.append("vision")
                if re.search(r"reason|thinking|-r1\b|o1|o3|o4|deepseek-r|qwq", mid, re.I) or meta.get("reasoning"):
                    caps.append("reasoning")
                rows.append({"id": mid, "name": meta.get("name") or mid,
                             "provider": _provider_of(mid, meta.get("owned_by", "")),
                             "context": meta.get("context_length") or meta.get("context_window"), "caps": caps})
            rows.sort(key=lambda r: (r["provider"].lower(), r["id"].lower()))
            if _PV.is_azure(base) and "/openai/v1" not in base:      # Azure lists base models, not your deployments
                rows = []
            _MODELS.update(t=time.time(), key=cache_key, rows=rows, error="")
        except urllib.error.HTTPError as e:
            err = f"The endpoint answered HTTP {e.code} when listing models." + (
                " Check your API key." if e.code in (401, 403) else (" " + str(e.msg)) if 300 <= e.code < 400 else "")
        except Exception as e:  # noqa: BLE001
            err = f"Could not list models: {_mhttp.explain(e, base)}"
    from meshpool_harness import providers as PV
    dprov, dbare = PV.split(default)
    ids = {r["id"] for r in rows}
    if dprov == "key" and dbare not in ids and (key or PV.keyless_ok(base)):
        rows = [{"id": dbare, "name": dbare, "provider": _provider_of(dbare, ""), "caps": [],
                 "context": None, "configured": True}] + rows
    # One section per provider (the key endpoint and ChatGPT), ready or with what to set up.
    groups = PV.groups(rows, err, refresh=refresh)
    flat = [m for g in groups for m in g["models"]]
    return {"models": flat, "groups": groups, "favorites": fav, "default": default,
            "error": "" if any(g["ready"] for g in groups) else err, "base_url": base}


def set_favorite(mid: str, on: bool) -> dict:
    def change(vals):
        fav = [m for m in (vals.get("MH_FAVORITE_MODELS") or "").split(",") if m]
        fav = [m for m in fav if m != mid] + ([mid] if on else [])
        return {"MH_FAVORITE_MODELS": ",".join(fav)}
    C.update(change)                       # read and write under one lock
    return models()


# ======================================== files ===================================================================
HIDE = {".git", "node_modules", "__pycache__", ".venv", "venv", ".DS_Store", ".mypy_cache", ".pytest_cache", ".next"}


def list_dir(root: str, rel: str = "") -> dict:
    r = check_root(root)
    d = _inside(r, rel)
    if not d.is_dir():
        raise ValueError("Not a folder.")
    rows = []
    try:
        for p in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name in HIDE:
                continue
            try:
                rows.append({"name": p.name, "path": p.relative_to(r).as_posix(), "dir": p.is_dir(),
                             "size": p.stat().st_size if p.is_file() else None})
            except OSError:
                continue
    except PermissionError:
        raise ValueError(_perm_hint(d))
    return {"root": str(r), "path": rel, "entries": rows[:2000], "truncated": len(rows) > 2000, "total": len(rows)}


def _perm_hint(p: Path) -> str:
    if sys.platform == "darwin":
        return (f"macOS did not let Meshpool read {p}. Allow it when macOS asks, or turn on Full Disk Access for "
                "Meshpool in Settings, Permissions.")
    return f"Meshpool cannot read {p}: permission denied."


# B4: decided by name first, so an empty or odd-looking .jpg is never opened as text with a Save button.
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff", ".heic", ".avif",
              ".mp3", ".wav", ".m4a", ".ogg", ".oga", ".aac", ".flac", ".mp4", ".mov", ".m4v", ".webm", ".ogv", ".avi", ".mkv",
              ".zip", ".gz", ".7z", ".exe", ".dll", ".so", ".dylib", ".pyc", ".sqlite", ".db"}


def read_file(root: str, rel: str) -> dict:
    r = check_root(root)
    p = _inside(r, rel)
    if not p.is_file():
        raise ValueError("Not a file.")
    size = p.stat().st_size
    from meshpool_harness import docread
    if docread.is_document(p) and p.suffix.lower() not in (".html", ".htm", ".csv", ".tsv", ".ipynb"):
        if size > 60_000_000:
            return {"path": rel, "binary": True, "size": size, "text": "", "note": "File is larger than 60 MB."}
        try:
            text = docread.extract(p, max_chars=400_000)
            if text.startswith("Could not read "):         # docread reports a parser failure as text: "Could not read x: PackageNotFoundError ..."
                raise ValueError(text)
        except Exception as e:  # noqa: BLE001 - B6: a damaged document is a plain sentence, not a parser traceback
            from meshpool_harness.ui.previews import friendly_error
            raise ValueError(friendly_error(p, e)) from None
        return {"path": rel, "binary": False, "readonly": True, "size": size, "text": text,
                "note": "Preview of the document's text"}
    if p.suffix.lower() in BINARY_EXT:
        return {"path": rel, "binary": True, "readonly": True, "size": size, "text": "", "note": "This is not a text file."}
    if size > 2_000_000:
        return {"path": rel, "binary": True, "size": size, "text": "", "note": "File is larger than 2 MB."}
    try:
        data = p.read_bytes()
    except PermissionError:
        raise ValueError(_perm_hint(p))
    if b"\0" in data[:8000]:
        return {"path": rel, "binary": True, "size": size, "text": ""}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:                   # editing would rewrite every non-UTF-8 byte: show it read-only
        return {"path": rel, "binary": False, "readonly": True, "size": size, "text": data.decode("utf-8", "replace"),
                "note": "Not UTF-8 text, so it is read-only here. Open it in your editor to change it."}
    return {"path": rel, "binary": False, "size": size, "text": text}


def write_file(root: str, rel: str, text: str) -> dict:
    r = check_root(root)
    p = _inside(r, rel)
    if p.is_dir() or p == r:
        raise ValueError("That is a folder, not a file.")
    p.parent.mkdir(parents=True, exist_ok=True)
    text = text.replace("\r\n", "\n")
    try:                                         # keep the file's line endings: a one-line edit is a one-line diff
        if b"\r\n" in p.read_bytes()[:65536]:
            text = text.replace("\n", "\r\n")
    except OSError:
        pass
    p.write_bytes(text.encode("utf-8"))
    try:
        from meshpool_harness.checkpoints import note_foreign_write
        note_foreign_write(root, str(p), "workbench")
    except Exception:
        pass
    return {"ok": True, "path": rel, "size": p.stat().st_size}


# ======================================== git =====================================================================
def attach(root: str, name: str, data: bytes) -> dict:
    """Save a file the user attached in the message box into the project, without overwriting anything."""
    return attach_to(check_root(root), name, data)


def attach_to(r: Path, name: str, data: bytes) -> dict:
    """The saving itself, for a folder that is already known to be the chat's own (no check_root)."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(name or "file").name).strip().rstrip(". ") or "file"
    if name.strip(".") == "":
        name = "file"
    stem, dot, suf = name.rpartition(".")
    if dot and stem and len(suf) <= 20 and len(name) > 200:    # keep the extension, cut the rest (255-byte limit)
        name = stem[:200 - len(suf) - 1] + "." + suf
    name = name[:200]
    while len(name.encode("utf-8")) > 200:
        name = name[:-1]
    if len(data) > 150 * 1024 * 1024:
        raise ValueError("Attachments can be up to 150 MB. Put bigger files in the project folder instead.")
    sub = ""                                     # at the folder's root, so an edit in place lands where the file is
    base = r / sub if sub else r
    try:
        base.mkdir(exist_ok=True)
        p = base / name
        stem, suf, i = p.stem, p.suffix, 2
        while p.exists() or p.is_symlink():      # exists() follows a dangling link: it wrote outside the project
            p = base / f"{stem} ({i}){suf}"
            i += 1
        with open(p, "xb") as f:                 # x: never through a link or over a file made meanwhile
            f.write(data)
    except PermissionError:
        raise ValueError(f"Meshpool can't save files in {r}: it is read-only. Choose another folder or change its permissions.")
    except OSError as e:
        raise ValueError(f"Couldn't save {name}: {e.strerror or e}")
    try:
        from meshpool_harness.checkpoints import note_foreign_write
        note_foreign_write(str(r), str(p), "attach")
    except Exception:
        pass
    return {"path": (sub + "/" if sub else "") + p.name}


def git_exe() -> str | None:
    """A Git that will not pop up the macOS 'install developer tools' dialog."""
    if os.name == "nt":
        return shutil.which("git")
    for c in ("/opt/homebrew/bin/git", "/usr/local/bin/git"):
        if os.path.exists(c):
            return c
    g = shutil.which("git")
    if not g:
        return None
    if sys.platform == "darwin" and g == "/usr/bin/git":
        r = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True)
        if r.returncode != 0:
            return None
    return g


_NET_CMDS = {"push", "pull", "fetch", "clone", "ls-remote"}
# The token reaches git through the environment of that one process, never on the command line or in .git/config:
# an empty helper resets any stored ones, then this helper answers git's question from $MESHPOOL_GH_TOKEN, for
# github.com only: a remote the agent (or a repository's config) pointed elsewhere never receives the token.
_CRED_FLAGS = ["-c", "credential.helper=",
               "-c", 'credential.https://github.com.helper=!f() { echo username=x-access-token; echo "password=$MESHPOOL_GH_TOKEN"; }; f']


def _no_hooks() -> list[str]:
    """Git hooks off for the commands that carry the token: a hook the agent wrote into .git/hooks would otherwise run
    with MESHPOOL_GH_TOKEN in its environment. An empty folder of Meshpool's own stands in for hooks/."""
    d = C.home() / "no-git-hooks"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return ["-c", f"core.hooksPath={d}"]


def _gh_git_env(env: dict) -> dict:
    from meshpool_harness import mcp_apps as CN
    tok = CN.github_token()
    return dict(env, MESHPOOL_GH_TOKEN=tok) if tok else env


def _git(root: Path, *args, check=True, timeout=60) -> str:
    g = git_exe()
    if not g:
        raise ValueError("Git is not installed." + (" On a Mac, run: xcode-select --install" if sys.platform == "darwin" else ""))
    # A project's own .git/config must not run programs when Meshpool merely looks at it (status on opening a
    # project, diffs for the changes list): fsmonitor, an external diff tool and a pager are all programs.
    safe = ["-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "diff.external=", "-c", "core.quotePath=false",
            "--no-pager"]
    env, extra = dict(os.environ, GIT_TERMINAL_PROMPT="0"), []
    if args and args[0] in _NET_CMDS and is_managed(root):      # a clone Meshpool made: it signs in with the saved token
        env, extra = _gh_git_env(env), _CRED_FLAGS + _no_hooks()
    try:
        r = subprocess.run([g, *safe, *extra, *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=NO_WINDOW, env=env)
    except subprocess.TimeoutExpired:
        raise ValueError(f"git {args[0] if args else ''} took too long and was stopped. Check your network "
                         "connection and try again.") from None
    if check and r.returncode != 0:
        raise ValueError((r.stderr or r.stdout).strip()[-800:]
                         or f"git {next((a for a in args if not a.startswith('-')), 'command')} failed")
    return r.stdout


_GS: dict = {}                      # project folder -> (time, status): the panel and PR code ask again and again
_GS_LOCKS: dict = {}
_GS_TTL = 1.5


def git_status(root: str, fresh: bool = False) -> dict:
    """Status of a project's repository. It is six git processes, so calls within a moment share one answer, and
    callers at the same time wait for it instead of each starting their own. fresh=True after a change."""
    key = str(root)
    gate = _GS_LOCKS.setdefault(key, threading.Lock())
    with gate:
        stamp = _git_stamp(key)
        hit = _GS.get(key)
        if hit and not fresh and hit[2] == stamp and time.monotonic() - hit[0] < _GS_TTL:
            return dict(hit[1])
        res = _git_status(root)
        if len(_GS) > 50:
            _GS.clear()
        _GS[key] = (time.monotonic(), res, stamp)
        return dict(res)


def _git_stamp(root: str) -> tuple:
    """A change to the remote (config), the branch (HEAD) or the index shows at once, whatever the cache age."""
    out = []
    for n in ("config", "HEAD", "index"):
        try:
            st = (Path(root) / ".git" / n).stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _git_status(root: str) -> dict:
    r = check_root(root)
    if not git_exe():
        return {"repo": False, "git": False}
    try:
        top = _git(r, "rev-parse", "--show-toplevel").strip()
    except ValueError:
        return {"repo": False, "git": True}
    branch = _git(r, "branch", "--show-current", check=False).strip() or "(detached)"
    porcelain = _git(r, "status", "--porcelain", "-z", check=False)
    files, parts = [], porcelain.split("\0")
    i = 0
    while i < len(parts):
        ent = parts[i]
        i += 1
        if len(ent) < 4:
            continue
        if ent[0] in "RC" or ent[1] in "RC":          # a rename or copy: the old path follows as its own entry
            i += 1
        files.append({"status": ent[:2].strip(), "path": ent[3:]})
    ahead = behind = 0
    try:
        ab = _git(r, "rev-list", "--left-right", "--count", "@{upstream}...HEAD").split()
        behind, ahead = int(ab[0]), int(ab[1])
        upstream = True
    except (ValueError, IndexError):
        upstream = False
    remote = _git(r, "remote", "get-url", "origin", check=False).strip()
    branches = [b.strip().lstrip("* ").strip() for b in _git(r, "branch", "--format=%(refname:short)", check=False).splitlines() if b.strip()]
    return {"repo": True, "git": True, "top": top, "branch": branch, "files": files[:500], "dirty": len(files),
            "ahead": ahead, "behind": behind, "upstream": upstream, "remote": remote, "branches": branches[:200],
            "github": _github_slug(remote)}


def _github_slug(remote: str) -> str:
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$", remote or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def git_action(root: str, body: dict) -> dict:
    r = check_root(root)
    act = body.get("action")
    if act == "init":
        _git(r, "init")
    elif act == "checkout":
        name = body.get("branch", "").strip()
        if not name:
            raise ValueError("Give a branch name.")
        if name.startswith("-") or not git_exe() or subprocess.run(
                [git_exe(), "check-ref-format", "--branch", name], cwd=str(r), capture_output=True,
                creationflags=NO_WINDOW).returncode:
            raise ValueError(f"\"{name}\" is not a valid branch name.")
        if not body.get("create") and not _git(r, "symbolic-ref", "-q", "HEAD", check=False).strip() and \
                not _git(r, "for-each-ref", "--contains", "HEAD", "--format=%(refname)", "refs/heads",
                         check=False).strip():
            # detached, with commits on no branch: switching away would leave them only in the reflog
            raise ValueError("You have commits that are on no branch (a detached HEAD). Create a branch for them "
                             "first (type a name and press Create), then switch.")
        if body.get("create"):
            _git(r, "switch", "-c", name)
        else:
            _git(r, "switch", name)
    elif act == "commit":
        msg = (body.get("message") or "").strip()
        if not msg:
            raise ValueError("Write a commit message.")
        top = Path(_git(r, "rev-parse", "--show-toplevel").strip())
        if _git(r, "diff", "--name-only", "--diff-filter=U", check=False).strip() or (top / ".git" / "MERGE_HEAD").exists():
            raise ValueError("This repository is in the middle of a merge with conflicts. Resolve the conflicts "
                             "(look for <<<<<<< in the files), then commit.")
        _git(r, "add", "-A", "--", ".")          # this project's folder only, not the rest of a larger repository
        _git(r, "-c", "user.name=" + (_git(r, "config", "user.name", check=False).strip() or "Meshpool"),
             "-c", "user.email=" + (_git(r, "config", "user.email", check=False).strip() or "meshpool@localhost"),
             "commit", "-m", msg)
    elif act == "push":
        branch = _git(r, "branch", "--show-current").strip()
        if not branch:
            raise ValueError("Switch to a branch first. Pushing needs a branch, and this folder is on a detached commit.")
        _git(r, "push", "-u", "origin", branch, timeout=180)
    elif act == "pull":
        _git(r, "pull", "--ff-only", timeout=180)
    elif act == "pr":
        return _open_pr(r, body)
    else:
        raise ValueError("Unknown Git action.")
    return git_status(str(r), fresh=True)


def _open_pr(r: Path, body: dict) -> dict:
    st = git_status(str(r), fresh=True)
    slug = st.get("github")
    if not slug:
        raise ValueError("Pull requests work with GitHub remotes. This project's origin is not on GitHub.")
    if st.get("branch") == "(detached)":
        raise ValueError("Switch to a branch first. A pull request needs a branch, and this folder is on a detached commit.")
    title = (body.get("title") or "").strip() or _git(r, "log", "-1", "--pretty=%s").strip()
    desc = body.get("body") or ""
    if st["ahead"] or not st["upstream"]:
        _git(r, "push", "-u", "origin", st["branch"], timeout=180)
    gh = shutil.which("gh")
    from meshpool_harness import mcp_apps as CN
    token = CN.github_token()
    if token:
        req = urllib.request.Request(f"https://api.github.com/repos/{slug}", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "Meshpool"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                base = json.loads(resp.read())["default_branch"]
        except urllib.error.HTTPError as e:
            raise ValueError(f"GitHub did not let Meshpool read {slug} (HTTP {e.code}). Check the GitHub "
                             "connection in Settings.") from None
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            raise ValueError("Could not reach GitHub to look up the default branch. Check your network "
                             "connection and try again.") from None
        data = json.dumps({"title": title, "head": st["branch"], "base": body.get("base") or base,
                           "body": desc, "draft": bool(body.get("draft"))}).encode()
        req = urllib.request.Request(f"https://api.github.com/repos/{slug}/pulls", data=data, method="POST", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "Meshpool",
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                pr = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ValueError(f"GitHub said: {e.read().decode('utf-8', 'replace')[:400]}")
        return st | {"pr": pr.get("html_url")}
    if gh:
        out = subprocess.run([gh, "pr", "create", "--title", title, "--body", desc or title,
                              *(["--draft"] if body.get("draft") else [])], cwd=str(r),
                             capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
        if out.returncode != 0:
            raise ValueError(out.stderr.strip()[-600:])
        return st | {"pr": out.stdout.strip().splitlines()[-1]}
    return st | {"pr": f"https://github.com/{slug}/compare/{st['branch']}?expand=1",
                 "note": "Connect GitHub (Settings, Connectors) to open pull requests from Meshpool. This link opens the form."}


# ======================================== terminal ================================================================
TERMS: dict[str, dict] = {}


SHELLS_WIN = {"powershell": "PowerShell", "cmd": "Command Prompt", "bash": "Git Bash"}


def shells() -> dict:
    """The shells the Terminal panel offers on this computer, and the default."""
    if os.name == "nt":
        have = {k: v for k, v in SHELLS_WIN.items() if k != "bash" or (os.environ.get("MH_BASH")
                                                                        and os.path.exists(os.environ["MH_BASH"]))}
        return {"shells": have, "default": "powershell"}
    sh = os.environ.get("SHELL") or "/bin/bash"
    return {"shells": {"login": Path(sh).name if os.path.exists(sh) else "sh"}, "default": "login"}


def _shell_cmd(command: str, shell: str = "", nt: bool | None = None) -> list[str]:
    nt = os.name == "nt" if nt is None else nt
    if nt:
        bash = os.environ.get("MH_BASH")
        shell = shell or "powershell"
        if shell == "bash" and bash and os.path.exists(bash):
            return [bash, "-lc", command]
        if shell == "cmd":
            return ["cmd", "/d", "/s", "/c", command]
        # PowerShell, with UTF-8 output so accents and symbols come through
        return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
    sh = os.environ.get("SHELL") or "/bin/bash"
    if not os.path.exists(sh):
        sh = "/bin/sh"
    return [sh, "-lc", command]


def term_run(root: str, command: str, shell: str = "") -> dict:
    r = check_root(root)
    if not command.strip():
        raise ValueError("Type a command.")
    tid = uuid.uuid4().hex[:8]
    t = {"id": tid, "cwd": str(r), "command": command, "out": [], "base": 0, "rc": None, "start": time.time(),
         "p": None, "lock": threading.Lock()}
    done = [k for k, v in TERMS.items() if v["rc"] is not None]
    for k in done[:-20]:                         # finished commands keep their output, but only the last few
        TERMS.pop(k, None)
    TERMS[tid] = t
    env = dict(C.user_env(), TERM="dumb", FORCE_COLOR="0", NO_COLOR="1")
    p = subprocess.Popen(_shell_cmd(command, shell), cwd=str(r), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW,
                         **({} if os.name == "nt" else {"start_new_session": True}))
    t["p"] = p

    def pump():
        for line in iter(p.stdout.readline, b""):
            with t["lock"]:
                t["out"].append(line.decode("utf-8", "replace"))
                if len(t["out"]) > 5000:             # keep the last lines; "base" keeps every line's number stable
                    del t["out"][:1000]
                    t["base"] += 1000
        rc = p.wait()
        with t["lock"]:
            t["rc"] = rc
    threading.Thread(target=pump, daemon=True).start()
    return term_view(tid, 0)


def term_view(tid: str, since: int) -> dict:
    t = TERMS.get(tid)
    if not t:
        raise KeyError(tid)
    with t["lock"]:                              # lines and exit code from the same moment
        start = max(since, t["base"])
        out = t["out"][start - t["base"]:]
        rc = t["rc"]
    return {"id": tid, "command": t["command"], "cwd": t["cwd"], "rc": rc, "lines": out, "next": start + len(out),
            "running": rc is None}


def _term_kill(t: dict, wait: float = 3.0) -> None:
    """Stop a terminal command and everything it started: SIGTERM, then SIGKILL if it is still there."""
    p = t.get("p")
    if not p or p.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW)
        else:
            import signal
            pg = os.getpgid(p.pid)
            os.killpg(pg, signal.SIGTERM)
            try:
                p.wait(wait)
            except subprocess.TimeoutExpired:
                os.killpg(pg, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def term_stop(tid: str) -> dict:
    t = TERMS.get(tid)
    if t and t["p"] and t["rc"] is None:
        _term_kill(t)
    return term_view(tid, 0) if t else {}


def term_stop_all() -> None:
    """The app is quitting: no terminal command (a dev server, say) keeps running and holding its port."""
    ths = [threading.Thread(target=_term_kill, args=(t, 1.0), daemon=True) for t in list(TERMS.values())
           if t.get("rc") is None]
    for th in ths:                               # all at once: ten dev servers must not take ten seconds to stop
        th.start()
    for th in ths:
        th.join(5)


# ======================================== usage ===================================================================
def usage() -> dict:
    from meshpool_harness import session_store as SS
    from meshpool_harness.agent.core import SESSIONS
    rows, total = [], {"in": 0, "cached": 0, "out": 0, "calls": 0}
    by_model: dict[str, dict] = {}
    for p in SESSIONS.glob("*/session.json") if SESSIONS.exists() else []:
        try:
            d = SS.read_head(p.parent)           # P-07/H-14: the small head record; a chat saved before heads held usage is read once
            if not isinstance(d, dict) or "usage" not in d:
                d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        u = d.get("usage") or {}
        m = d.get("model") or d.get("harness") or "unknown"
        for k in total:
            total[k] += int(u.get(k) or 0)
        bm = by_model.setdefault(m, {"in": 0, "cached": 0, "out": 0, "calls": 0, "threads": 0})
        for k in ("in", "cached", "out", "calls"):
            bm[k] += int(u.get(k) or 0)
        bm["threads"] += 1
        rows.append({"id": d.get("id"), "title": d.get("title") or "New chat", "model": m,
                     "updated": d.get("updated", 0), **{k: int(u.get(k) or 0) for k in total}})
    rows.sort(key=lambda r: -r["updated"])
    return {"total": total, "by_model": by_model, "threads": rows[:200]}


def open_editor(root: str) -> dict:
    """"Open in Meshpool editor": the project folder opens in the app's own file tree and editor. No other program starts."""
    r = check_root(root)
    return {"ok": True, "open": {"root": str(r), "path": ""}, "message": "Opened in the Meshpool editor."}


# ======================================== pull request, CI and deployments =======================================

def _gh_api(path: str, token: str, method: str = "GET", body: dict | None = None):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "Meshpool", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403, 404) and not token:
            raise ValueError("Connect GitHub (Settings, Connectors) to see pull requests and checks for this repository.")
        raise ValueError(f"GitHub said ({e.code}): {msg}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError(f"Could not reach GitHub: {e}")


def branch_changes(root: str, base: str = "") -> dict:
    """What this branch adds and removes compared with the branch it merges into (the pull request's base, else the
    remote's default branch): each file added, changed, renamed or deleted, with its lines added and removed. Plain
    git on this computer, so it needs no GitHub sign-in and shows before a pull request exists."""
    r = check_root(root)
    if not base:                                         # the open pull request's base is the best guess; GitHub only
        try:
            base = (git_ci_pr(root) or {}).get("base", "")
        except Exception:  # noqa: BLE001
            base = ""
    cands = [f"origin/{base}", base] if base else []
    head = _git(r, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    cands += [head, "origin/main", "origin/master", "main", "master"]
    ref = next((c for c in cands if c and _git(r, "rev-parse", "-q", "--verify", c + "^{commit}", check=False).strip()), "")
    if not ref:
        return {"base": base, "files": [], "added": 0, "removed": 0, "note": "No base branch to compare with."}
    branch = _git(r, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    if branch and branch == re.sub(r"^origin/", "", ref):
        return {"base": ref, "same": True, "files": [], "added": 0, "removed": 0}    # on the base branch itself
    mb = _git(r, "merge-base", ref, "HEAD", check=False).strip()
    if not mb:
        return {"base": ref, "files": [], "added": 0, "removed": 0, "note": f"This branch shares no history with {ref}."}
    kinds = {"A": "added", "D": "deleted", "M": "changed", "R": "renamed", "C": "copied", "T": "changed"}
    status = {}
    for line in _git(r, "-c", "core.quotePath=false", "diff", "--name-status", "-M", mb, "HEAD").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status[parts[-1]] = (kinds.get(parts[0][:1], "changed"), parts[1] if len(parts) == 3 else "")
    files, plus, minus = [], 0, 0
    for line in _git(r, "-c", "core.quotePath=false", "diff", "--numstat", "-M", mb, "HEAD").splitlines():
        a_, d_, path = (line.split("\t", 2) + ["", "", ""])[:3]
        if " => " in path:                                 # a rename: "dir/{old => new}.py" or "old => new"
            m = re.match(r"(.*)\{(.*) => (.*)\}(.*)", path)
            path = (m.group(1) + m.group(3) + m.group(4)).replace("//", "/") if m else path.split(" => ")[-1]
        binary = a_ == "-"
        add, rem = (0, 0) if binary else (int(a_ or 0), int(d_ or 0))
        plus, minus = plus + add, minus + rem
        kind, old = status.get(path, ("changed", ""))
        files.append({"path": path, "status": kind, "added": add, "removed": rem, "binary": binary,
                      **({"from": old} if old else {})})
    files.sort(key=lambda f: (-(f["added"] + f["removed"]), f["path"]))
    return {"base": ref, "files": files[:300], "count": len(files), "added": plus, "removed": minus}


_PR_BASE: dict = {}


def git_ci_pr(root: str) -> dict | None:
    """The current branch's pull request as git_ci last saw it (no network call)."""
    return _PR_BASE.get(root)


def git_ci(root: str) -> dict:
    """The current branch's pull request, its CI checks and workflow runs, and recent deployments."""
    from meshpool_harness import mcp_apps as CN
    st = git_status(root)
    if not st.get("repo"):
        return {"available": False, "note": "This folder is not a Git repository."}
    slug = st.get("github")
    if not slug:
        return {"available": False, "note": "Checks and deployments show for repositories on GitHub."}
    token = CN.github_token()
    r = check_root(root)
    sha = _git(r, "rev-parse", "HEAD", check=False).strip()
    owner = slug.split("/")[0]
    out = {"available": True, "slug": slug, "branch": st["branch"], "sha": sha[:7], "connected": bool(token),
           "pr": None, "checks": [], "runs": [], "deployments": [],
           "repo_url": f"https://github.com/{slug}"}
    try:
        prs = _gh_api(f"/repos/{slug}/pulls?head={owner}:{urllib.parse.quote(st['branch'])}&state=all&per_page=1", token)
    except ValueError as e:
        out["error"] = str(e)
        prs = []
    if prs:
        p = prs[0]
        out["pr"] = {"number": p["number"], "title": p["title"], "url": p["html_url"],
                     "state": "merged" if p.get("merged_at") else p["state"], "draft": p.get("draft", False),
                     "base": p["base"]["ref"], "author": (p.get("user") or {}).get("login", "")}
        sha = p["head"]["sha"] or sha
        _PR_BASE[root] = out["pr"]
    if sha:
        try:
            cr = _gh_api(f"/repos/{slug}/commits/{sha}/check-runs?per_page=50", token)
            out["checks"] = [{"name": c["name"], "status": c["status"], "conclusion": c.get("conclusion"),
                              "url": c.get("html_url") or c.get("details_url"),
                              "app": (c.get("app") or {}).get("name", "")} for c in cr.get("check_runs") or []]
        except ValueError:
            pass
        try:
            cs = _gh_api(f"/repos/{slug}/commits/{sha}/status", token)
            out["checks"] += [{"name": x["context"], "status": "completed" if x["state"] != "pending" else "in_progress",
                               "conclusion": {"success": "success", "failure": "failure", "error": "failure"}.get(x["state"]),
                               "url": x.get("target_url"), "app": "status"} for x in cs.get("statuses") or []]
        except ValueError:
            pass
    try:
        runs = _gh_api(f"/repos/{slug}/actions/runs?branch={urllib.parse.quote(st['branch'])}&per_page=6", token)
        out["runs"] = [{"id": w["id"], "name": w.get("name") or w.get("display_title"), "status": w["status"],
                        "conclusion": w.get("conclusion"), "url": w["html_url"], "event": w.get("event"),
                        "created": w.get("created_at")} for w in runs.get("workflow_runs") or []]
    except ValueError:
        pass
    try:
        deps = _gh_api(f"/repos/{slug}/deployments?per_page=6", token)
        for d in deps:
            state, url = "unknown", ""
            try:
                sts = _gh_api(f"/repos/{slug}/deployments/{d['id']}/statuses?per_page=1", token)
                if sts:
                    state, url = sts[0].get("state", "unknown"), sts[0].get("environment_url") or sts[0].get("target_url") or ""
            except ValueError:
                pass
            out["deployments"].append({"environment": d.get("environment"), "ref": d.get("ref"), "state": state,
                                       "url": url, "created": d.get("created_at"), "sha": (d.get("sha") or "")[:7]})
    except ValueError:
        pass
    fails = [c for c in out["checks"] if c["conclusion"] in ("failure", "timed_out", "cancelled", "action_required")]
    running = [c for c in out["checks"] if c["status"] != "completed"]
    out["summary"] = "failing" if fails else "running" if running else "passing" if out["checks"] else "none"
    return out


# ---------------------------------------- the pull request view: a list, then one pull request in full

def _gh_all(path: str, token: str, limit: int = 300) -> list:
    """Every item of a paginated GitHub list, up to limit."""
    out, page = [], 1
    while len(out) < limit:
        sep = "&" if "?" in path else "?"
        got = _gh_api(f"{path}{sep}per_page=100&page={page}", token)
        if not isinstance(got, list) or not got:
            break
        out += got
        if len(got) < 100:
            break
        page += 1
    return out[:limit]


def _checks_for(slug: str, sha: str, token: str) -> list[dict]:
    """Check runs and commit statuses for one commit, newest attempt of each check only."""
    checks, seen = [], set()
    try:
        cr = _gh_api(f"/repos/{slug}/commits/{sha}/check-runs?per_page=100", token)
        for c in cr.get("check_runs") or []:              # newest first: a rerun replaces the failed attempt
            if c["name"] in seen:
                continue
            seen.add(c["name"])
            secs = None
            if c.get("started_at") and c.get("completed_at"):
                from datetime import datetime
                f = "%Y-%m-%dT%H:%M:%SZ"
                secs = int((datetime.strptime(c["completed_at"], f) - datetime.strptime(c["started_at"], f)).total_seconds())
            checks.append({"name": c["name"], "status": c["status"], "conclusion": c.get("conclusion"),
                           "url": c.get("html_url") or c.get("details_url"), "app": (c.get("app") or {}).get("name", ""),
                           "seconds": secs, "summary": ((c.get("output") or {}).get("title") or "")[:200]})
    except ValueError:
        pass
    try:
        cs = _gh_api(f"/repos/{slug}/commits/{sha}/status", token)
        for x in cs.get("statuses") or []:
            if x["context"] in seen:
                continue
            seen.add(x["context"])
            checks.append({"name": x["context"], "status": "completed" if x["state"] != "pending" else "in_progress",
                           "conclusion": {"success": "success", "failure": "failure", "error": "failure"}.get(x["state"]),
                           "url": x.get("target_url"), "app": "status", "seconds": None,
                           "summary": (x.get("description") or "")[:200]})
    except ValueError:
        pass
    return checks


def _verdict(checks: list[dict]) -> str:
    if any(c["conclusion"] in ("failure", "timed_out", "cancelled", "action_required", "startup_failure")
           for c in checks):
        return "failing"
    if any(c["status"] != "completed" for c in checks):
        return "running"
    return "passing" if checks else "none"


def _pr_row(p: dict) -> dict:
    return {"number": p["number"], "title": p["title"], "url": p["html_url"],
            "state": "merged" if p.get("merged_at") else p["state"], "draft": bool(p.get("draft")),
            "head": p["head"]["ref"], "base": p["base"]["ref"], "sha": p["head"]["sha"],
            "base_sha": p["base"].get("sha", ""),
            "author": (p.get("user") or {}).get("login", ""), "updated": p.get("updated_at"),
            "created": p.get("created_at"), "merged": p.get("merged_at"), "node": p.get("node_id")}


def _repo(root: str) -> tuple[str, str]:
    from meshpool_harness import mcp_apps as CN
    st = git_status(root)
    if not st.get("repo"):
        raise ValueError("This folder is not a Git repository.")
    if not st.get("github"):
        raise ValueError("Pull requests show here for repositories on GitHub.")
    return st["github"], CN.github_token()


def pr_list(root: str) -> dict:
    """Open pull requests, then the most recently closed ones, each with its checks verdict, for the list."""
    slug, token = _repo(root)
    rows = [_pr_row(p) for p in _gh_api(f"/repos/{slug}/pulls?state=open&sort=updated&direction=desc&per_page=30", token)]
    closed = [_pr_row(p) for p in _gh_api(f"/repos/{slug}/pulls?state=closed&sort=updated&direction=desc&per_page=15", token)]
    rows += closed
    for r in rows[:20]:                                   # verdicts cost one call each: the first twenty
        r["checks"] = _verdict(_checks_for(slug, r["sha"], token))
    branch = git_status(root).get("branch", "")
    return {"slug": slug, "connected": bool(token), "branch": branch, "prs": rows,
            "current": next((r["number"] for r in rows if r["head"] == branch and r["state"] == "open"), None)}


def pr_detail(root: str, number: int) -> dict:
    """One pull request in full: what it is, every file it changes with its diff, its checks, the workflow runs for
    its head commit, its reviews and the deployments of that commit."""
    slug, token = _repo(root)
    n = int(number)
    p = _gh_api(f"/repos/{slug}/pulls/{n}", token)
    out = _pr_row(p) | {
        "slug": slug, "connected": bool(token), "body": (p.get("body") or "")[:20000],
        "additions": p.get("additions", 0), "deletions": p.get("deletions", 0),
        "changed_files": p.get("changed_files", 0), "commits": p.get("commits", 0),
        "mergeable": p.get("mergeable"), "mergeable_state": p.get("mergeable_state", ""),
        "head_repo": ((p.get("head") or {}).get("repo") or {}).get("full_name", slug)}
    files = []
    for f in _gh_all(f"/repos/{slug}/pulls/{n}/files", token, 300):
        files.append({"path": f["filename"], "status": f["status"], "added": f.get("additions", 0),
                      "removed": f.get("deletions", 0), "from": f.get("previous_filename", ""),
                      "patch": (f.get("patch") or "")[:200000], "binary": "patch" not in f and f["status"] != "removed"})
    out["files"] = files
    out["checks"] = _checks_for(slug, out["sha"], token)
    out["verdict"] = _verdict(out["checks"])
    try:
        runs = _gh_api(f"/repos/{slug}/actions/runs?head_sha={out['sha']}&per_page=10", token)
        out["runs"] = [{"id": w["id"], "name": w.get("name") or w.get("display_title"), "status": w["status"],
                        "conclusion": w.get("conclusion"), "url": w["html_url"], "event": w.get("event"),
                        "created": w.get("created_at"), "attempt": w.get("run_attempt", 1)}
                       for w in runs.get("workflow_runs") or []]
    except ValueError:
        out["runs"] = []
    try:
        revs = _gh_api(f"/repos/{slug}/pulls/{n}/reviews?per_page=50", token)
        last = {}
        for rv in revs:                                    # each reviewer's latest verdict
            who = (rv.get("user") or {}).get("login", "")
            if rv.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"):
                last[who] = rv["state"].lower()
        out["reviews"] = [{"user": k, "state": v} for k, v in last.items()]
    except ValueError:
        out["reviews"] = []
    out["deployments"] = []
    try:
        for d in _gh_api(f"/repos/{slug}/deployments?sha={out['sha']}&per_page=5", token):
            state, url = "unknown", ""
            try:
                sts = _gh_api(f"/repos/{slug}/deployments/{d['id']}/statuses?per_page=1", token)
                if sts:
                    state = sts[0].get("state", "unknown")
                    url = sts[0].get("environment_url") or sts[0].get("target_url") or ""
            except ValueError:
                pass
            out["deployments"].append({"environment": d.get("environment"), "state": state, "url": url,
                                       "created": d.get("created_at")})
    except ValueError:
        pass
    try:                                                  # the conversation on GitHub, newest last
        cm = _gh_api(f"/repos/{slug}/issues/{n}/comments?per_page=30", token)
        out["comments"] = [{"user": (c.get("user") or {}).get("login", ""), "body": (c.get("body") or "")[:4000],
                            "created": c.get("created_at"), "url": c.get("html_url")} for c in cm[-30:]]
    except ValueError:
        out["comments"] = []
    # a preview of the change: a deployment's address, else a preview link a check posted (Vercel, Netlify, ...)
    out["preview_url"] = next((d["url"] for d in out["deployments"] if d.get("url")), "") or next(
        (c["url"] for c in out["checks"] if c.get("url") and re.search(r"(?i)preview|vercel|netlify|pages|deploy", c["name"] + c.get("app", ""))
         and not re.search(r"github\.com/.+/(actions|runs)/", c["url"])), "")
    # merging from the view: only an open pull request whose checks pass and that GitHub says can merge
    out["can_merge"] = (out["state"] == "open" and not out["draft"] and out["verdict"] in ("passing", "none")
                        and out["mergeable"] is True and bool(token))
    return out


PREVIEW_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp", ".svg": "image/svg+xml", ".md": "text/plain; charset=utf-8",
                 ".markdown": "text/plain; charset=utf-8", ".html": "text/plain; charset=utf-8",
                 ".htm": "text/plain; charset=utf-8"}


def pr_file(root: str, number: int, path: str, side: str = "head") -> tuple[bytes, str]:
    """One file of a pull request as it is after the change (head) or before it (base), for the preview: images,
    Markdown and HTML only, at most 5 MB. HTML and Markdown come back as plain text; the page shows them sandboxed."""
    import base64
    ext = Path(path).suffix.lower()
    if ext not in PREVIEW_TYPES:
        raise ValueError("No preview for this kind of file.")
    slug, token = _repo(root)
    p = _gh_api(f"/repos/{slug}/pulls/{int(number)}", token)
    repo, ref = ((p["head"].get("repo") or {}).get("full_name") or slug, p["head"]["sha"]) if side == "head" \
        else (slug, p["base"]["sha"])
    meta = _gh_api(f"/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}", token)
    if not isinstance(meta, dict) or meta.get("type") != "file":
        raise ValueError("That file is not in this version.")
    if meta.get("size", 0) > 5 * 1024 * 1024:
        raise ValueError("Too large to preview here. Open it on GitHub.")
    if meta.get("content"):
        data = base64.b64decode(meta["content"])
    else:                                                  # over 1 MB: GitHub sends a download address instead
        h = {"User-Agent": "Meshpool", **({"Authorization": f"Bearer {token}"} if token else {})}
        with urllib.request.urlopen(urllib.request.Request(meta["download_url"], headers=h), timeout=30) as r:
            data = r.read(5 * 1024 * 1024 + 1)
    return data[:5 * 1024 * 1024], PREVIEW_TYPES[ext]


def pr_action(root: str, body: dict) -> dict:
    """Merge (only when the checks pass), mark ready for review, or rerun failed jobs, for one pull request."""
    slug, token = _repo(root)
    if not token:
        raise ValueError("Connect GitHub (Settings, Connectors) first.")
    n, act = int(body.get("pr") or 0), body.get("action")
    if act == "merge":
        d = pr_detail(root, n)
        if not d["can_merge"]:
            why = "it is not open" if d.get("state") != "open" else \
                ("its checks are " + d["verdict"]) if d["verdict"] not in ("passing", "none") else \
                "it is a draft" if d["draft"] else "GitHub says it cannot merge yet" if d["mergeable"] is not True \
                else "it is not open"
            raise ValueError(f"Not merged: {why}.")
        _gh_api(f"/repos/{slug}/pulls/{n}/merge", token, "PUT",
                {"merge_method": body.get("method") or "squash", "sha": d["sha"]})
    elif act == "ready":
        d = _gh_api(f"/repos/{slug}/pulls/{n}", token)
        q = "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{isDraft}}}"
        req = urllib.request.Request("https://api.github.com/graphql", method="POST",
                                     data=json.dumps({"query": q, "variables": {"id": d["node_id"]}}).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                              "User-Agent": "Meshpool"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.loads(r.read() or b"{}")
        except (urllib.error.URLError, OSError) as e:
            raise ValueError(f"Could not reach GitHub: {e}")
        if res.get("errors"):
            raise ValueError("GitHub said: " + "; ".join(e.get("message", "") for e in res["errors"])[:300])
    elif act == "rerun":
        _gh_api(f"/repos/{slug}/actions/runs/{int(body['run'])}/rerun-failed-jobs", token, "POST", {})
    elif act == "comment":
        text = str(body.get("body") or "").strip()
        if not text:
            raise ValueError("Write a comment first.")
        _gh_api(f"/repos/{slug}/issues/{n}/comments", token, "POST", {"body": text[:60000]})
    else:
        raise ValueError("Unknown action.")
    return pr_detail(root, n)


def git_ci_action(root: str, body: dict) -> dict:
    from meshpool_harness import mcp_apps as CN
    st = git_status(root)
    slug = st.get("github")
    token = CN.github_token()
    if not (slug and token):
        raise ValueError("Connect GitHub (Settings, Connectors) first.")
    act = body.get("action")
    if act == "rerun":
        _gh_api(f"/repos/{slug}/actions/runs/{int(body['run'])}/rerun-failed-jobs", token, "POST", {})
    elif act == "merge":                                  # same rule as the pull request view: green and mergeable
        pr_action(root, {"action": "merge", "pr": body["pr"], "method": body.get("method")})
    elif act == "ready":
        raise ValueError("Mark the pull request ready for review on GitHub.")
    else:
        raise ValueError("Unknown action.")
    return git_ci(root)


# ======================================== GitHub sessions: a repository cloned into a managed workspace ===========
_SLUG_RX = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")


def workspaces_dir() -> Path:
    """Where Meshpool keeps the clones it makes for GitHub sessions. The user never has to know: the app shows
    owner/name, not this folder."""
    return C.home() / "workspaces"


def is_managed(path) -> bool:
    try:
        p = Path(path).expanduser().resolve()
        return workspaces_dir().resolve() in p.parents
    except (OSError, ValueError):
        return False


def clone_argv(slug: str, dest: Path) -> list[str]:
    """The git command that clones one repository. No token in it: _gh_git_env hands that to the process."""
    if not _SLUG_RX.match(slug or ""):
        raise ValueError("Give the repository as owner/name.")
    return [git_exe() or "git", *_CRED_FLAGS, *_no_hooks(), "clone", "--origin", "origin", "--",
            f"https://github.com/{slug}.git", str(dest)]


_REPOS: dict = {"t": 0, "tok": "", "rows": []}


def github_repos(q: str = "") -> dict:
    """Repositories the saved GitHub token can reach (newest push first), filtered by what the user typed. A typed
    owner/name that is not in the list is looked up directly, so public repositories can be opened too."""
    from meshpool_harness import mcp_apps as CN
    token = CN.github_token()
    if not token:
        raise ValueError("Connect GitHub (Settings, Connectors) to pick one of your repositories.")
    if _REPOS["tok"] != token[-8:] or time.time() - _REPOS["t"] > 60:
        got = _gh_all("/user/repos?sort=pushed&affiliation=owner,collaborator,organization_member", token, 300)
        _REPOS.update(t=time.time(), tok=token[-8:], rows=[
            {"full_name": r["full_name"], "private": bool(r.get("private")), "description": (r.get("description") or "")[:140],
             "pushed": r.get("pushed_at"), "default_branch": r.get("default_branch") or "main"} for r in got])
    q = (q or "").strip().lower()
    rows = [r for r in _REPOS["rows"] if q in r["full_name"].lower() or q in r["description"].lower()] if q \
        else list(_REPOS["rows"])
    if not rows and _SLUG_RX.match(q):
        try:
            r = _gh_api(f"/repos/{q}", token)
            rows = [{"full_name": r["full_name"], "private": bool(r.get("private")),
                     "description": (r.get("description") or "")[:140], "pushed": r.get("pushed_at"),
                     "default_branch": r.get("default_branch") or "main"}]
        except ValueError:
            pass
    for r in rows:
        r["cloned"] = _workspace_path(r["full_name"]).is_dir()
    return {"repos": rows[:100]}


def github_branches(slug: str) -> dict:
    from meshpool_harness import mcp_apps as CN
    if not _SLUG_RX.match(slug or ""):
        raise ValueError("Give the repository as owner/name.")
    token = CN.github_token()
    info = _gh_api(f"/repos/{slug}", token)
    names = [b["name"] for b in _gh_all(f"/repos/{slug}/branches", token, 300)]
    return {"repo": slug, "default": info.get("default_branch") or "main", "branches": names}


def _workspace_path(slug: str) -> Path:
    return workspaces_dir() / slug.replace("/", "__")


def github_workspace(body: dict) -> dict:
    """Make a repository ready to work in: clone it into the managed workspace (or reuse the clone that is there,
    fetching first), then switch to the branch asked for, creating it from `base` (default: the repository's
    default branch) when create is set. Returns the folder for the new session and the project's name."""
    slug = (body.get("repo") or "").strip()
    branch = (body.get("branch") or "").strip()
    if not _SLUG_RX.match(slug):
        raise ValueError("Give the repository as owner/name.")
    if not git_exe():
        raise ValueError("Git is not installed.")
    from meshpool_harness import mcp_apps as CN
    if not CN.github_token():
        raise ValueError("Connect GitHub (Settings, Connectors) first.")
    if branch and (branch.startswith("-") or subprocess.run([git_exe(), "check-ref-format", "--branch", branch],
                                                            capture_output=True, creationflags=NO_WINDOW).returncode):
        raise ValueError(f'"{branch}" is not a valid branch name.')
    dest = _workspace_path(slug)
    workspaces_dir().mkdir(parents=True, exist_ok=True)
    reused = (dest / ".git").exists()
    if reused:
        if _github_slug(_git(dest, "remote", "get-url", "origin", check=False).strip()).lower() != slug.lower():
            raise ValueError("The saved copy of this repository points somewhere else. Remove it and try again.")
        _git(dest, "fetch", "--prune", "origin", timeout=300)
    else:
        argv = clone_argv(slug, dest)
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                           creationflags=NO_WINDOW, env=_gh_git_env(dict(os.environ, GIT_TERMINAL_PROMPT="0")))
        if r.returncode:
            shutil.rmtree(dest, ignore_errors=True)
            tail = (r.stderr or r.stdout).strip()[-400:]
            raise ValueError("Could not copy the repository: " + re.sub(r"https://[^@\s]*@", "https://", tail))
    p = add_project(str(dest), slug)
    if branch:
        have_local = _git(dest, "rev-parse", "-q", "--verify", f"refs/heads/{branch}", check=False).strip()
        have_remote = _git(dest, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}", check=False).strip()
        if have_local or have_remote:
            _git(dest, "switch", branch)
        elif body.get("create"):
            base = (body.get("base") or "").strip()
            start = f"origin/{base}" if base else _git(dest, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD",
                                                       check=False).strip()
            _git(dest, "switch", "-c", branch, *([start] if start else []))
        else:
            raise ValueError(f'There is no branch "{branch}" in {slug}. Choose "new branch" to create it.')
    return {"path": p["path"], "name": slug, "reused": reused, "branch": _git(dest, "branch", "--show-current").strip()}


# ======================================== the session side panel ==================================================
_WINDOWS = [(r"gemini", 1_000_000), (r"gpt-4\.1", 1_000_000), (r"claude", 200_000), (r"gpt-5|o[134]\b", 400_000)]


def model_window(model: str) -> tuple[int, str]:
    """The context window in tokens for a model, and where that number came from: the endpoint's model list when
    it gave one, else a rule of thumb by name, else 128K (the size the agent's compaction is tuned for)."""
    bare = (model or "").split(":", 1)[-1]
    for row in _MODELS.get("rows") or []:
        if row.get("id") in (model, bare) and row.get("context"):
            try:
                return int(row["context"]), "endpoint"
            except (TypeError, ValueError):
                pass
    for rx, n in _WINDOWS:
        if re.search(rx, bare, re.I):
            return n, "estimate"
    return 128_000, "estimate"


def context_info(s) -> dict:
    """How full the model's context window is. Harnesses that report it (ACP, Pi) give used and size. The built-in
    agent does not, so: the prompt size of its latest model call (a usage event), else its message text / 4."""
    a = getattr(s, "agent", None)
    model = ""
    try:
        model = (a.current_model() if hasattr(a, "current_model") else getattr(a, "model", "")) or ""
    except Exception:  # noqa: BLE001
        pass
    ctx = getattr(a, "context", None)
    if isinstance(ctx, dict) and ctx.get("size"):
        used, size, src = int(ctx.get("used") or 0), int(ctx["size"]), "agent"
    else:
        size, src = model_window(model)
        used = 0
        with s.lock:
            evs = list(s.events)
        last = next((e for e in reversed(evs) if e.get("type") == "usage" and e.get("in") and not e.get("aux")), None)
        if last:
            used, src = int(last["in"]), src + "+usage"
        else:
            try:
                from meshpool_harness.agent.core import _context_size
                used, src = _context_size(getattr(a, "messages", []) or []) // 4, src + "+chars"
            except Exception:  # noqa: BLE001
                pass
    pct = min(100, round(100 * used / size)) if size else 0
    return {"used": used, "size": size, "percent": pct, "model": model, "source": src}


def _rel(root: Path, p: str) -> str:
    p = str(p or "").strip().strip('"')
    try:
        q = Path(p)
        if q.is_absolute():
            return q.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return ""
    return Path(p).as_posix().lstrip("./") if ".." not in Path(p).parts else ""


_OUT_RX = re.compile(r"(?:Saved(?: the [\w ]+)?|[Ww]rote|[Cc]reated)[: ]+((?:[A-Za-z]:)?[^\n\"'`<>():]+?\."
                     r"(?:docx|xlsx|pptx|pdf|csv|md|png|jpg|html|txt|json))(?![\w.])")


def outputs_from(evs: list, turns: list, root: Path) -> list[dict]:
    """Files a session created or changed, from its events (edits and writes, files its tools said they saved) and
    from what its checkpoints recorded per reply. Only files that still exist inside the folder."""
    found: dict[str, dict] = {}
    for e in evs:
        if e.get("type") == "tool_start" and e.get("name") in ("write_file", "edit"):
            rel = _rel(root, e.get("summary") or "")
            if rel:
                found.setdefault(rel, {"path": rel, "how": "written" if e["name"] == "write_file" else "edited"})
        elif e.get("type") == "tool_end" and e.get("ok") and e.get("output"):
            for m in _OUT_RX.finditer(str(e["output"])):
                rel = _rel(root, m.group(1))
                if rel:
                    found.setdefault(rel, {"path": rel, "how": "saved"})
    for t in turns:
        for c in t.get("changed") or []:
            if c.get("status") != "deleted":
                found.setdefault(str(c["path"]), {"path": str(c["path"]), "how": c.get("status") or "changed"})
    out = []
    for rel, row in found.items():
        if any(part in HIDE or part.startswith(".") for part in Path(rel).parts):
            continue                                   # caches and tool folders are not the user's files
        try:
            f = _inside(root, rel)
            if f.is_file():
                out.append(row | {"size": f.stat().st_size})
        except (ValueError, OSError):
            continue
    return out[:200]


def session_outputs(s, root: Path) -> list[dict]:
    """Files this session created or changed (see outputs_from)."""
    with s.lock:
        evs = list(s.events)
    try:
        turns = s.cp.turns() if s.cp else []
    except Exception:  # noqa: BLE001
        turns = []
    return outputs_from(evs, turns, root)


def session_connectors(s) -> dict:
    """The MCP servers enabled for this session, and which ones its tool calls actually used (names mcp__server__tool)."""
    used: dict[str, dict] = {}
    with s.lock:
        evs = list(s.events)
    for e in evs:
        if e.get("type") == "tool_start" and str(e.get("name") or "").startswith("mcp__"):
            parts = str(e["name"]).split("__", 2)
            if len(parts) == 3:
                u = used.setdefault(parts[1], {"calls": 0, "tools": []})
                u["calls"] += 1
                if parts[2] not in u["tools"]:
                    u["tools"].append(parts[2])
    st = []
    try:
        st = s.mcp.status() if getattr(s, "mcp", None) else []
    except Exception:  # noqa: BLE001
        pass
    rows = [{"name": r["name"], "status": r["status"], "tools": len(r.get("tools") or []),
             "error": r.get("error") or "", "used": used.get(r["name"], {}).get("calls", 0)}
            for r in st if r["status"] != "disabled"]
    known = {r["name"] for r in rows}
    rows += [{"name": n, "status": "not listed", "tools": 0, "error": "", "used": u["calls"]}
             for n, u in used.items() if n not in known]
    rows.sort(key=lambda r: (-r["used"], r["name"]))
    return {"servers": rows, "used": sorted(used)}


def session_background(s, root: str) -> dict:
    """What is running for this session: sub-agents (from its delegation events), the scheduled task that started
    it, and terminal commands run in its folder, with how long each has run. `finished` counts the ones that ended."""
    now = time.time()
    busy = getattr(s, "state", "") in ("working", "waiting")
    with s.lock:
        evs = list(s.events)
    subs: dict[str, dict] = {}
    for e in evs:
        if e.get("type") != "subagent" or not e.get("sid"):
            continue
        r = subs.setdefault(e["sid"], {"kind": "subagent", "id": e["sid"], "title": e.get("title") or "Sub-agent",
                                       "t0": e.get("t") or now, "t1": None})
        if e.get("kind") == "done":
            r["t1"] = e.get("t") or now
    running, finished = [], 0
    for r in subs.values():
        if r["t1"] is None and busy:
            running.append(r | {"elapsed": round(now - r["t0"], 1)})
        else:
            finished += 1
    for t in list(TERMS.values()):
        if t.get("cwd") != root:
            continue
        if t["rc"] is None:
            running.append({"kind": "terminal", "id": t["id"], "title": t["command"][:120],
                            "elapsed": round(now - t["start"], 1)})
        else:
            finished += 1
    if getattr(s, "scheduled", None):
        sch = s.scheduled
        row = {"kind": "scheduled", "id": s.id, "title": sch.get("name") or "Scheduled task",
               "elapsed": round(now - (getattr(s, "t_turn", None) or now), 1)}
        if busy:
            running.append(row)
        else:
            finished += 1
    return {"running": running, "finished": finished}


def session_bg_stop(sid: str, kind: str, ident: str) -> dict:
    from meshpool_harness.ui import agent_api as A
    s = A.LIVE.get(sid)
    if not s:
        raise KeyError(sid)
    if kind == "terminal":
        if TERMS.get(ident, {}).get("cwd") != s.workdir:
            raise ValueError("That command is not part of this session.")
        term_stop(ident)
    elif kind == "subagent":
        for d in list(getattr(s.agent, "_delegations", {}).values()):
            with d.lock:
                rec = d.children.get(ident)
            if rec:
                rec["stop"] = True
                ag = rec.get("agent")
                if ag is not None:
                    ag.stop()
                break
        else:
            raise ValueError("That sub-agent has already finished.")
    elif kind == "scheduled":
        s.stop()
    else:
        raise ValueError("Unknown task.")
    return session_background(s, s.workdir)


def _session_of(sid: str):
    from meshpool_harness.ui import agent_api as A
    s = A.LIVE.get(sid)
    if not s:
        raise KeyError(sid)
    return s


def session_plan(s) -> dict:
    """The agent's plan for the panel: steps with done / current / pending. From the live agent's todo list, else the
    last plan event of the session (a chat reopened after a restart)."""
    from meshpool_harness.agent import planning as P
    todos = list(getattr(getattr(s, "agent", None), "_todos", None) or [])
    if not todos:
        for ev in reversed(getattr(s, "events", None) or []):
            if ev.get("type") == "plan":
                todos = list(ev.get("entries") or ev.get("items") or [])
                break
    return P.panel(todos)


def session_panel(sid: str) -> dict:
    """Everything the session side panel shows that is local and quick: where it runs, the repository and branch,
    context fill, connectors (enabled and used), files made, background tasks and the working-tree summary. The
    pull request and other open PRs come from session_pr (network), so this answers at once."""
    s = _session_of(sid)
    root = s.workdir
    out = {"id": sid, "context": context_info(s), "connectors": session_connectors(s),
           "background": session_background(s, root), "state": s.state}
    managed = is_managed(root)
    env = {"where": "workspace" if managed else "computer",
           "label": "Managed workspace on this computer" if managed else "This computer",
           "managed": managed, "folder": "" if managed else root, "name": Path(root).name}
    out["environment"] = env
    out["repo"], out["branch"], out["tree"] = None, None, {"files": 0, "added": 0, "removed": 0}
    try:
        r = Path(root)
        g = git_status(root)
    except (ValueError, OSError):
        g = {"repo": False}
    if g.get("repo"):
        slug = g.get("github") or ""
        out["repo"] = {"slug": slug, "url": f"https://github.com/{slug}" if slug else "", "remote": bool(g.get("remote"))}
        if managed and slug:
            env["name"] = slug
        out["branch"] = {"name": g["branch"], "branches": g["branches"], "ahead": g["ahead"], "behind": g["behind"],
                         "upstream": g["upstream"]}
        add = rem = 0
        for line in _git(r, "diff", "--numstat", "HEAD", check=False).splitlines():
            a_, d_ = (line.split("\t") + ["0", "0"])[:2]
            add += int(a_) if a_.isdigit() else 0
            rem += int(d_) if d_.isdigit() else 0
        out["tree"] = {"files": g["dirty"], "added": add, "removed": rem}
    out["outputs"] = session_outputs(s, Path(root))
    out["plan"] = session_plan(s)
    return out


def session_pr(sid: str) -> dict:
    """The pull request for the session's current branch (title, number, size, draft state, checks) and the
    repository's other open pull requests. GitHub only; every failure is reported in `error`, never raised."""
    s = _session_of(sid)
    root = s.workdir
    out = {"available": False, "pr": None, "others": [], "error": ""}
    try:
        slug, token = _repo(root)
        branch = git_status(root).get("branch", "")
    except ValueError as e:
        out["error"] = str(e)
        return out
    out.update(available=True, slug=slug, connected=bool(token), branch=branch)
    try:
        owner = slug.split("/")[0]
        prs = _gh_api(f"/repos/{slug}/pulls?head={owner}:{urllib.parse.quote(branch)}&state=open&per_page=1", token)
        if prs:
            p = _gh_api(f"/repos/{slug}/pulls/{prs[0]['number']}", token)
            row = _pr_row(p)
            checks = _checks_for(slug, row["sha"], token)
            out["pr"] = {k: row[k] for k in ("number", "title", "url", "state", "draft", "base", "head")} | {
                "additions": p.get("additions", 0), "deletions": p.get("deletions", 0),
                "changed_files": p.get("changed_files", 0), "checks": _verdict(checks),
                "failing": [c["name"] for c in checks if c["conclusion"] in ("failure", "timed_out", "cancelled")][:5],
                "can_ready": bool(token) and bool(row["draft"])}
        others = _gh_api(f"/repos/{slug}/pulls?state=open&sort=updated&direction=desc&per_page=8", token)
        out["others"] = [{"number": o["number"], "title": o["title"], "url": o["html_url"],
                          "draft": bool(o.get("draft")), "head": o["head"]["ref"]}
                         for o in others if not out["pr"] or o["number"] != out["pr"]["number"]][:6]
    except ValueError as e:
        out["error"] = str(e)
    return out
