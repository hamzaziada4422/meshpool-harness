"""The in-app file browser and editor (editor.js): everything a user does with a file or a folder happens here, inside
Meshpool. Nothing in this module starts another program.

GET  /api/fs/home                         {home, skills}   the Meshpool home folder and the skills folder inside it
GET  /api/fs/list?root=&path=             one folder, lazily: {entries: [{name, path, dir, size, link}]}
GET  /api/fs/search?root=&q=[&content=1]  file names matching q anywhere under root (a flat list); content=1 adds
                                          the lines inside text files that contain q
GET  /api/fs/read?root=&path=             {kind: text|preview|binary|large, text, sha, mtime_ns, ...}
GET  /api/fs/stat?root=&path=             {sha, mtime_ns, size}  to notice a change on disk
POST /api/fs/save {root, path, text, base_sha, overwrite?}
                                          writes the file; 409 {conflict: true} when it changed on disk since it was read
POST /api/fs/reveal {root, path}          the one explicit "Show in Finder / File Explorer" action (context menus only)

Safety, in order: the root must be a project the user added, a chat's own folder, or the Meshpool home; the path is
resolved and must stay inside the root (so `..` and a symlink that points out are refused); a save never writes through
a symlink; inside the Meshpool home, files that hold keys and tokens are hidden and refused.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import parse_qs

from meshpool_harness import config as C
from meshpool_harness.ui import workbench as W

HIDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache",
             ".next", ".nuxt", ".gradle", ".tox", ".cache", "Pods", ".DS_Store", "Thumbs.db", ".hg", ".svn"}
MAX_LIST = 2000
MAX_TEXT = 2_000_000          # a bigger file is not opened for editing
MAX_SAVE = 4_000_000
MAX_SEARCH = 100
KEEP_BACKUPS = 20
# Inside the Meshpool home only: files that hold keys, tokens or sign-in state are never shown or read here.
# Folders too: the agent browser's profile holds cookies and saved logins (security review).
_SECRET = re.compile(r"(^\.env|token|secret|credential|oauth|\.pem$|\.key$|^id_rsa|\.config$|^mcp.*\.json$|^connectors.*\.json$|"
                     r"^browser-profile|^window-profile|^cookies|^login data|^web data|^local state$)", re.I)
PREVIEW_EXT = {".pdf", ".pptx", ".ppt", ".pptm", ".ppsx", ".pps", ".potx", ".odp", ".docx", ".doc", ".docm", ".dotx", ".odt",
               ".rtf", ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".key", ".pages", ".numbers",
               ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
               ".mp3", ".wav", ".m4a", ".ogg", ".oga", ".aac", ".flac", ".mp4", ".mov", ".m4v", ".webm", ".ogv"}


class Conflict(Exception):
    pass


def fs_root(root: str) -> Path:
    """A project folder, a chat's folder (the same rule as every other file route), the Meshpool home or its runs folder."""
    if not root:
        raise ValueError("Choose a folder first.")
    p = Path(root).expanduser().resolve()
    try:
        if p in (C.home().resolve(), C.runs_dir().resolve()):
            return p
    except OSError:
        pass
    return W.check_root(root)


def _is_home(root: Path) -> bool:
    try:
        return root in (C.home().resolve(), C.runs_dir().resolve())
    except OSError:
        return False


def _hidden_name(root: Path, name: str) -> bool:
    return bool(_is_home(root) and _SECRET.search(name))


def safe(root_s: str, rel: str) -> tuple[Path, Path]:
    """(root, resolved path). Refuses an absolute path, `..`, a symlink that leaves the root and a secret in the home."""
    root = fs_root(root_s)
    rel = str(rel or "").replace("\\", "/").strip()
    if "\0" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise ValueError("Path is outside the project.")
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise ValueError("Path is outside the project.")
    if any(_hidden_name(root, part) for part in p.relative_to(root).parts):
        raise ValueError("Meshpool keeps that file private.")
    return root, p


def _rel(root: Path, p: Path) -> str:
    return "" if p == root else p.relative_to(root).as_posix()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------------------------------- listing
def list_dir(root_s: str, rel: str = "") -> dict:
    root, d = safe(root_s, rel)
    if not d.is_dir():
        raise ValueError("That is not a folder.")
    rows, total = [], 0
    try:
        with os.scandir(d) as it:
            items = sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False) and not _dir_link(e), e.name.lower()))
    except PermissionError:
        raise ValueError(W._perm_hint(d))
    for e in items:
        if e.name in HIDE_DIRS or _hidden_name(root, e.name):
            continue
        link = e.is_symlink()
        if link:                                           # a link is shown only when it stays inside the project
            try:
                t = Path(e.path).resolve()
            except OSError:
                continue
            if t != root and root not in t.parents:
                continue
        total += 1
        if len(rows) >= MAX_LIST:
            continue
        try:
            is_dir = e.is_dir()
            rows.append({"name": e.name, "path": _rel(root, Path(e.path)), "dir": is_dir, "link": link,
                         "size": None if is_dir else e.stat().st_size})
        except OSError:
            continue
    return {"root": str(root), "path": _rel(root, d), "entries": rows, "truncated": total > len(rows)}


def _dir_link(e) -> bool:
    try:
        return e.is_symlink() and e.is_dir()
    except OSError:
        return False


def search(root_s: str, q: str, content: bool = False) -> dict:
    """File names containing q; with content=True, also lines inside text files that contain q (case-insensitive),
    as {name, path, line, text}. Hidden folders and secret-looking files are skipped either way."""
    root, _ = safe(root_s, "")
    q = (q or "").strip().lower()
    if not q:
        return {"results": [], "truncated": False}
    if content:
        names = search(root_s, q)
        return {"results": names["results"], "lines": _search_lines(root, q),
                "truncated": names["truncated"]}
    hits, deadline, truncated = [], time.time() + 2.5, False
    tracked = _git_files(root)
    if tracked is not None:                         # a git repository: what git sees, so ignored files stay out
        for rel in tracked:
            parts = rel.split("/")
            if any(x in HIDE_DIRS or _hidden_name(root, x) for x in parts) or q not in rel.lower():
                continue
            hits.append({"name": parts[-1], "path": rel, "dir": False})
            if len(hits) >= MAX_SEARCH:
                return {"results": hits, "truncated": True}
        return {"results": hits, "truncated": False}
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(x for x in dirs if x not in HIDE_DIRS and not _hidden_name(root, x))
        base = Path(cur)
        for name in sorted(files):
            if _hidden_name(root, name) or name in HIDE_DIRS:
                continue
            rel = _rel(root, base / name)
            if q in rel.lower():
                hits.append({"name": name, "path": rel, "dir": False})
                if len(hits) >= MAX_SEARCH:
                    return {"results": hits, "truncated": True}
        if time.time() > deadline:
            truncated = True
            break
    return {"results": hits, "truncated": truncated}


def _git_files(root: Path) -> list | None:
    """The files of root that git tracks or would track (not ignored), relative to root; None when root is not in a
    git repository, git is missing, or MH_PICKER_IGNORED=1 asks for ignored files too."""
    if os.environ.get("MH_PICKER_IGNORED") == "1":
        return None
    import shutil
    import subprocess
    g = shutil.which("git")
    if not g:
        return None
    try:
        r = subprocess.run([g, "-c", "core.fsmonitor=false", "-c", "core.quotePath=false", "ls-files", "-z", "-co",
                            "--exclude-standard", "--", "."], cwd=str(root), capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode:
        return None
    return sorted(x for x in r.stdout.decode("utf-8", "replace").split("\0") if x)[:200000]


_SYMBOL_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs",
               ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".scala", ".dart", ".lua", ".ex", ".exs"}
_SYMBOL = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+)?(?:public\s+|private\s+|protected\s+|static\s+|async\s+|"
    r"abstract\s+|final\s+|override\s+|inline\s+)*"
    r"(?P<kind>def|class|function|func|fn|interface|struct|enum|trait|type|module|object)\s+"
    r"(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_$][\w$]*)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+(?P<arrow>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")


def symbols(root_s: str, q: str) -> dict:
    """Functions, classes and similar definitions whose name contains q, for the @ picker: {name, kind, path, line}.
    Source files only, up to 1 MB each, about 1.5 seconds; names that start with q first."""
    root, _ = safe(root_s, "")
    q = (q or "").strip().lower()
    if len(q) < 2:
        return {"results": []}
    files = _git_files(root)
    if files is None:
        files = []
        for cur, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(x for x in dirs if x not in HIDE_DIRS and not _hidden_name(root, x))
            files += [_rel(root, Path(cur) / n) for n in sorted(names)]
            if len(files) > 20000:
                break
    hits, deadline = [], time.time() + 1.5
    for rel in files:
        if Path(rel).suffix.lower() not in _SYMBOL_EXT or any(x in HIDE_DIRS for x in rel.split("/")):
            continue
        f = root / rel
        try:
            if f.is_symlink() or f.stat().st_size > 1_000_000:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if q not in text.lower():
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = _SYMBOL.match(line)
            if not m:
                continue
            name = m.group("name") or m.group("arrow")
            if name and q in name.lower():
                hits.append({"name": name, "kind": "function" if m.group("arrow") else m.group("kind"), "path": rel,
                             "line": i})
        if time.time() > deadline or len(hits) > 400:
            break
    hits.sort(key=lambda h: (not h["name"].lower().startswith(q), len(h["name"]), h["path"]))
    return {"results": hits[:20]}


MAX_LINE_HITS = 200


def _search_lines(root: Path, q: str) -> list:
    """Lines that contain q in the text files under root: at most 200, files up to 2 MB, about 3 seconds."""
    hits, deadline = [], time.time() + 3.0
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(x for x in dirs if x not in HIDE_DIRS and not _hidden_name(root, x))
        base = Path(cur)
        for name in sorted(files):
            if _hidden_name(root, name) or name in HIDE_DIRS:
                continue
            f = base / name
            try:
                if f.is_symlink() or f.stat().st_size > 2_000_000:
                    continue
                raw = f.read_bytes()
            except OSError:
                continue
            if b"\0" in raw[:4096]:
                continue
            for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
                if q in line.lower():
                    hits.append({"name": name, "path": _rel(root, f), "line": i, "text": line.strip()[:200]})
                    if len(hits) >= MAX_LINE_HITS:
                        return hits
            if time.time() > deadline:
                return hits
    return hits


# ---------------------------------------------------------------------------------------------------- read
def read(root_s: str, rel: str) -> dict:
    root, p = safe(root_s, rel)
    if not p.is_file():
        raise ValueError("That file is gone.")
    st = p.stat()
    ext = p.suffix.lower()
    base = {"path": _rel(root, p), "name": p.name, "ext": ext.lstrip("."), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if ext in PREVIEW_EXT:
        return base | {"kind": "preview"}
    if st.st_size > MAX_TEXT:
        return base | {"kind": "large", "note": "This file is larger than 2 MB, so it is not opened for editing."}
    try:
        data = p.read_bytes()
    except PermissionError:
        raise ValueError(W._perm_hint(p))
    base["sha"] = _sha(data)
    if b"\0" in data[:8000]:
        return base | {"kind": "binary", "note": "This is not a text file."}
    eol = "crlf" if b"\r\n" in data[:65536] else "lf"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return base | {"kind": "text", "eol": eol, "readonly": True, "text": data.decode("utf-8", "replace").replace("\r\n", "\n"),
                       "note": "This file is not UTF-8, so it is read only here."}
    return base | {"kind": "text", "eol": eol, "readonly": False, "text": text.replace("\r\n", "\n")}


def stat(root_s: str, rel: str) -> dict:
    root, p = safe(root_s, rel)
    if not p.is_file():
        return {"gone": True}
    st = p.stat()
    sha = _sha(p.read_bytes()) if st.st_size <= MAX_TEXT else ""
    return {"gone": False, "mtime_ns": st.st_mtime_ns, "size": st.st_size, "sha": sha}


# ---------------------------------------------------------------------------------------------------- save
def _backup(root: Path, p: Path, data: bytes) -> str:
    """The content before this save, kept in the Meshpool runs folder: user-edits/<file key>/<time>_<name>.bak."""
    key = hashlib.sha1(str(p).encode("utf-8", "replace")).hexdigest()[:12]
    d = C.runs_dir() / "user-edits" / key
    d.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}_{time.time_ns() % 1000000:06d}_{p.name}.bak"
    (d / name).write_bytes(data)
    old = sorted(d.glob("*.bak"))
    for f in old[:-KEEP_BACKUPS]:
        try:
            f.unlink()
        except OSError:
            pass
    return str(d / name)


def save(body: dict) -> dict:
    root_s, rel = body.get("root", ""), body.get("path", "")
    root, p = safe(root_s, rel)
    lexical = root / str(rel).replace("\\", "/")
    if lexical.is_symlink():                                 # never write through a link, even one that stays inside
        raise ValueError("That file is a link, so Meshpool will not write to it.")
    text = body.get("text")
    if not isinstance(text, str):
        raise ValueError("Nothing to save.")
    if len(text.encode("utf-8")) > MAX_SAVE:
        raise ValueError("That is too much text to save here (4 MB at most).")
    if not p.is_file():
        raise Conflict("This file is gone from the folder. Nothing was saved.")
    try:
        cur = p.read_bytes()
    except PermissionError:
        raise ValueError(W._perm_hint(p))
    base_sha = str(body.get("base_sha") or "")
    if not body.get("overwrite") and base_sha and _sha(cur) != base_sha:
        raise Conflict("This file changed on disk since you opened it. Nothing was saved.")
    try:
        cur.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("This file is not UTF-8 text, so it cannot be saved here.")
    text = text.replace("\r\n", "\n")
    if b"\r\n" in cur[:65536]:                               # keep the file's line endings: a one-line edit stays one line
        text = text.replace("\n", "\r\n")
    new = text.encode("utf-8")
    from meshpool_harness.ui import agent_api as A
    A.mark_shared(str(root))                                  # a turn running here can no longer be undone as a whole
    backup = ""
    if cur != new:
        try:
            backup = _backup(root, p, cur)
        except OSError:
            backup = ""
        tmp = p.with_name(f".{p.name}.mp-{os.getpid()}.tmp")
        try:
            tmp.write_bytes(new)
            try:
                shutil.copymode(p, tmp)
            except OSError:
                pass
            os.replace(tmp, p)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    try:
        from meshpool_harness.checkpoints import note_foreign_write
        note_foreign_write(str(root), str(p), "editor")
    except Exception:
        pass
    st = p.stat()
    return {"ok": True, "path": _rel(root, p), "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha": _sha(new),
            "backup": Path(backup).name if backup else ""}


# ---------------------------------------------------------------------------------------------------- reveal
def reveal(body: dict) -> dict:
    """The only way the app asks the operating system to show a file: a person picked "Show in Finder / File
    Explorer" from a context menu. It shows the folder (the file selected where the system can); it never opens the file."""
    root, p = safe(body.get("root", ""), body.get("path", ""))
    if not p.exists():
        raise ValueError("That file is gone.")
    from meshpool_harness.ui.desktop import show_in_folder
    try:
        show_in_folder(p)
    except OSError as e:
        raise ValueError(f"There is no file manager to show it in ({e.strerror or e}). It is at: {p}") from e
    return {"ok": True}


def home_info() -> dict:
    home = C.home()
    sk = home / "skills"
    try:
        sk.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return {"home": str(home.resolve()), "skills": "skills"}


# ---------------------------------------------------------------------------------------------------- http glue
def handle(method: str, u, body: bytes = b""):
    import json
    p = u.path
    if method == "GET":
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if p == "/api/fs/home":
            return 200, home_info()
        if p == "/api/fs/list":
            return 200, list_dir(q.get("root", ""), q.get("path", ""))
        if p == "/api/fs/search":
            return 200, search(q.get("root", ""), q.get("q", ""), q.get("content") == "1")
        if p == "/api/fs/symbols":
            return 200, symbols(q.get("root", ""), q.get("q", ""))
        if p == "/api/fs/read":
            return 200, read(q.get("root", ""), q.get("path", ""))
        if p == "/api/fs/stat":
            return 200, stat(q.get("root", ""), q.get("path", ""))
        return 404, {"error": "Not found."}
    b = json.loads(body.decode("utf-8")) if body else {}
    if p == "/api/fs/save":
        try:
            return 200, save(b)
        except Conflict as e:
            cur = {}
            try:
                cur = stat(b.get("root", ""), b.get("path", ""))
            except (ValueError, OSError):
                pass
            return 409, {"error": str(e), "conflict": True, "current": cur}
    if p == "/api/fs/reveal":
        return 200, reveal(b)
    return 404, {"error": "Not found."}
