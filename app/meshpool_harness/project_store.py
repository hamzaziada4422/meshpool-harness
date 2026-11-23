"""Projects: a name, instructions, knowledge files and a memory of their own, plus an optional linked folder or GitHub
repository. A chat can belong to one project (or to none). See docs/plan/UX_V2.md.

On disk, under the Meshpool data folder (C.home()):

  projects/<pid>/project.json      {id, name, description, created, updated, link, legacy_path}
  projects/<pid>/instructions.md   what the user wants for every chat in the project
  projects/<pid>/memory.md         what agents learned in the project, one fact per line
  projects/<pid>/knowledge/<file>  the user's files (copies; the originals stay where they are)
  projects/<pid>/knowledge/.index.json   text cache: per file size, hash, text length and the chunks used for recall
  projects/.migrated.json          folders already turned into projects (see migrate)
  chats/<id>/                      the private working folder of a chat that has no linked folder
  chats/<id>/.project              the project id of such a chat (so a project note lands in the right memory)

Nothing here ever writes to projects.json or memory/projects/*.md, so the old app version keeps working on the same
data folder: delete projects/ to roll back.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from meshpool_harness import config as C

ID_RX = re.compile(r"p_[0-9a-f]{10}")
NAME_MAX, DESC_MAX, INSTR_MAX = 80, 400, 8000
FILE_MAX = 25 * 1024 * 1024               # one knowledge file
PROJECT_FILES_MAX = 60
MEM_MAX = 6000                           # characters of project memory kept
CHUNK = 1200
CTX_FILES = 6000                          # characters of knowledge text put into a chat's first message
CTX_WHOLE = 1500                          # a file this small goes in whole

_LOCK = threading.RLock()


# ---------- paths ----------------------------------------------------------------------------------------------------
def root() -> Path:
    return C.home() / "projects"


def chats_root() -> Path:
    return C.home() / "chats"


def _dir(pid: str) -> Path:
    if not isinstance(pid, str) or not ID_RX.fullmatch(pid):
        raise ValueError("That project does not exist.")
    return root() / pid


def _exists(pid: str) -> Path:
    d = _dir(pid)
    if not (d / "project.json").is_file():
        raise KeyError("That project does not exist.")
    return d


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    C.replace_file(tmp, p)


def _meta(d: Path) -> dict:
    try:
        return json.loads((d / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_meta(d: Path, m: dict) -> None:
    _write(d / "project.json", json.dumps(m, indent=1))


# ---------- projects -------------------------------------------------------------------------------------------------
def _clean_link(link) -> dict | None:
    if not link:
        return None
    if not isinstance(link, dict):
        raise ValueError("A link needs a folder or a repository.")
    path = str(link.get("path") or "").strip().strip('"')
    if not path or not Path(path).expanduser().is_dir():
        raise ValueError("That folder was not found. Pick a folder that exists on this computer.")
    kind = "github" if link.get("type") == "github" or link.get("repo") else "folder"
    out = {"type": kind, "path": str(Path(path).expanduser().resolve())}
    if kind == "github":
        out["repo"] = str(link.get("repo") or "")[:200]
        out["branch"] = str(link.get("branch") or "")[:200]
    return out


def _summary(d: Path, m: dict) -> dict:
    link = m.get("link")
    if link and not Path(link.get("path", "")).is_dir():
        link = dict(link, missing=True)
    idx = _index(d)
    return {"id": m["id"], "name": m.get("name", "Untitled"), "description": m.get("description", ""),
            "created": m.get("created", 0), "updated": m.get("updated", 0), "link": link,
            "has_instructions": bool(_read(d / "instructions.md").strip()), "knowledge_count": len(idx),
            "memory_count": len([l for l in _read(d / "memory.md").splitlines() if l.strip() and not l.startswith("#")]),
            "legacy_path": m.get("legacy_path", ""), "pinned": bool(m.get("pinned"))}


def list_projects() -> list[dict]:
    out = []
    try:
        dirs = [p for p in root().iterdir() if p.is_dir() and ID_RX.fullmatch(p.name)]
    except OSError:
        dirs = []
    for d in dirs:
        m = _meta(d)
        if m.get("id") == d.name:
            out.append(_summary(d, m))
    return sorted(out, key=lambda r: -float(r.get("updated") or 0))


def linked_paths() -> list[str]:
    """The folders projects are linked to, from project.json alone (list_projects also reads every project's
    knowledge index, which a file-browsing request must not do each time)."""
    out = []
    try:
        dirs = [p for p in root().iterdir() if p.is_dir() and ID_RX.fullmatch(p.name)]
    except OSError:
        return out
    for d in dirs:
        link = _meta(d).get("link")
        if isinstance(link, dict) and link.get("path"):
            out.append(str(link["path"]))
    return out


def create(name: str, description: str = "", instructions: str = "", link=None, legacy_path: str = "") -> dict:
    name = " ".join((name or "").split())[:NAME_MAX]
    if not name:
        raise ValueError("Give the project a name.")
    link = _clean_link(link)
    with _LOCK:
        pid = "p_" + uuid.uuid4().hex[:10]
        d = _dir(pid)
        now = time.time()
        _save_meta(d, {"id": pid, "name": name, "description": (description or "").strip()[:DESC_MAX], "created": now,
                       "updated": now, "link": link, "legacy_path": legacy_path})
        if instructions.strip():
            _write(d / "instructions.md", instructions.strip()[:INSTR_MAX] + "\n")
        return detail(pid)


def detail(pid: str) -> dict:
    d = _exists(pid)
    m = _meta(d)
    out = _summary(d, m)
    out["instructions"] = _read(d / "instructions.md").strip()
    out["memory"] = memory(pid)
    out["knowledge"] = knowledge_list(pid)
    return out


def update(pid: str, **kw) -> dict:
    with _LOCK:
        d = _exists(pid)
        m = _meta(d)
        if "name" in kw and kw["name"] is not None:
            nm = " ".join(str(kw["name"]).split())[:NAME_MAX]
            if not nm:
                raise ValueError("Give the project a name.")
            m["name"] = nm
        if kw.get("description") is not None:
            m["description"] = str(kw["description"]).strip()[:DESC_MAX]
        if "link" in kw:                                  # {} or None unlinks
            m["link"] = _clean_link(kw["link"])
        if kw.get("instructions") is not None:
            _write(d / "instructions.md", str(kw["instructions"]).strip()[:INSTR_MAX] + "\n")
        if kw.get("pinned") is not None:                  # pinning is not activity: the date stays
            m["pinned"] = bool(kw["pinned"])
        else:
            m["updated"] = time.time()
        _save_meta(d, m)
        return detail(pid)


def delete(pid: str) -> None:
    """Remove the project and what it holds. Its chats stay, as plain chats. A linked folder is never touched."""
    with _LOCK:
        d = _exists(pid)
        try:
            from meshpool_harness import memstore as MS
            MS.on_project_deleted(pid)                  # its facts are archived, never lost with the project
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(d, ignore_errors=True)


def get_meta(pid: str) -> dict:
    try:
        return _meta(_exists(pid))
    except (KeyError, ValueError):
        return {}


def touch(pid: str) -> None:
    try:
        d = _exists(pid)
        m = _meta(d)
        m["updated"] = time.time()
        _save_meta(d, m)
    except (KeyError, ValueError, OSError):
        pass


def legacy_map() -> dict:
    """{linked folder path: project id}: how chats made before projects existed find their project."""
    out = {}
    for r in list_projects():
        for k in (r.get("legacy_path"), (r.get("link") or {}).get("path")):
            if k:
                out.setdefault(k, r["id"])
    return out


# ---------- memory ---------------------------------------------------------------------------------------------------
def memory(pid: str) -> str:
    from meshpool_harness import memstore as MS
    _exists(pid)
    return MS.text(f"project:{pid}")


def memory_set(pid: str, text: str) -> str:
    from meshpool_harness import memstore as MS
    with _LOCK:
        _exists(pid)
        MS.replace_scope_text(f"project:{pid}", text)
        touch(pid)
        return memory(pid)


def memory_add(pid: str, note: str) -> str:
    from meshpool_harness import assistant as AS
    from meshpool_harness import memstore as MS
    note = " ".join((note or "").split())
    if not note:
        raise ValueError("A project note needs the note.")
    if AS.BAD.search(note):
        raise ValueError("Memory does not store secrets or instructions about the system.")
    with _LOCK:
        _exists(pid)
        f, made = MS.add(note[:400], scope=f"project:{pid}", source={"type": "chat"}, check=False)
        if made:
            touch(pid)
    return "Saved to this project's memory." if made else "Already in this project's memory."


def memory_clear(pid: str) -> None:
    memory_set(pid, "")


# ---------- knowledge ------------------------------------------------------------------------------------------------
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".py",
            ".js", ".ts", ".tsx", ".jsx", ".css", ".sql", ".sh", ".toml", ".ini", ".cfg", ".log", ".rst", ".tex", ".java",
            ".c", ".h", ".cpp", ".go", ".rs", ".rb", ".php", ".swift", ".kt"}


def _kdir(pid: str) -> Path:
    return _exists(pid) / "knowledge"


_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _safe_name(name: str) -> str:
    n = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(name or "file").name).rstrip(". ")
    if n in (".", ".."):
        n = ""
    if n.partition(".")[0].lower() in _RESERVED:           # CON, NUL, PRN ... cannot be file names on Windows
        n = "_" + n
    if len(n) > 120:                                       # keep the extension, cut the middle
        stem, dot, ext = n.rpartition(".")
        n = (stem[:100] + dot + ext[:15]) if dot and len(ext) <= 15 and stem else n[:120]
    return n or "file"


def _index(d: Path) -> dict:
    try:
        return json.loads((d / "knowledge" / ".index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _text_of(p: Path) -> str:
    try:
        if p.suffix.lower() in TEXT_EXT:
            return p.read_text(encoding="utf-8", errors="replace")[:400_000]
        from meshpool_harness import docread
        if docread.is_document(p):
            return str(docread.extract(p, max_chars=400_000) or "")
    except Exception:  # noqa: BLE001 - a file that cannot be read is still kept, just without recall text
        return ""
    return ""


def _chunks(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(cur) + len(para) > CHUNK and cur:
            out.append(cur.strip())
            cur = ""
        while len(para) > CHUNK:
            out.append(para[:CHUNK])
            para = para[CHUNK:]
        cur += para + "\n\n"
    if cur.strip():
        out.append(cur.strip())
    return out


def knowledge_list(pid: str) -> list[dict]:
    d = _exists(pid)
    idx = _index(d)
    rows = []
    for name, e in sorted(idx.items(), key=lambda kv: kv[0].lower()):
        if (d / "knowledge" / name).is_file():
            rows.append({"name": name, "size": e.get("size", 0), "added": e.get("added", 0), "text_chars": e.get("chars", 0),
                         "readable": bool(e.get("chars"))})
    return rows


def knowledge_add(pid: str, name: str, data: bytes) -> dict:
    if len(data) > FILE_MAX:
        raise ValueError(f"Knowledge files can be up to {FILE_MAX // (1024 * 1024)} MB.")
    if not data:
        raise ValueError("That file is empty.")
    with _LOCK:
        d = _exists(pid)
        k = d / "knowledge"
        idx = _index(d)
        name = _safe_name(name)
        if name == ".index.json":
            name = "index.json"
        if name not in idx and len(idx) >= PROJECT_FILES_MAX:
            raise ValueError(f"A project holds up to {PROJECT_FILES_MAX} files. Remove one first.")
        k.mkdir(parents=True, exist_ok=True)
        p = k / name
        with open(p, "wb") as f:
            f.write(data)
        text = _text_of(p)
        idx[name] = {"size": len(data), "hash": hashlib.sha1(data).hexdigest()[:16], "added": time.time(),
                     "chars": len(text), "chunks": _chunks(text)[:200], "head": " ".join(text[:240].split())}
        _write(k / ".index.json", json.dumps(idx))
        touch(pid)
        return {"name": name, "size": len(data), "readable": bool(text), "text_chars": len(text)}


def knowledge_remove(pid: str, name: str) -> None:
    with _LOCK:
        d = _exists(pid)
        idx = _index(d)
        name = _safe_name(name)
        idx.pop(name, None)
        try:
            (d / "knowledge" / name).unlink()
        except OSError:
            pass
        if (d / "knowledge").is_dir():
            _write(d / "knowledge" / ".index.json", json.dumps(idx))
        touch(pid)


def knowledge_path(pid: str, name: str) -> Path:
    p = _kdir(pid) / _safe_name(name)
    if not p.is_file():
        raise KeyError("That file is not in the project.")
    return p


_STOP = {"the", "and", "for", "with", "that", "this", "what", "when", "which", "who", "how", "are", "was", "you",
         "your", "can", "will", "from", "have", "has", "not", "all", "any", "but", "our", "about", "into", "please"}


def _terms(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (t or "").lower()) if w not in _STOP}


def knowledge_context(pid: str, task: str = "") -> str:
    """The knowledge part of a chat's first message: the file list with a line each, small files whole, and for
    larger ones the passages that match the task. Deterministic for the same task (no clocks, sorted names)."""
    d = _exists(pid)
    idx = _index(d)
    names = [n for n in sorted(idx, key=str.lower) if (d / "knowledge" / n).is_file()]
    if not names:
        return ""
    lines = ["Files in this project's knowledge (the user added them for every chat here):"]
    for n in names:
        e = idx[n]
        lines.append(f"- {n}" + (f": {e['head']}" if e.get("head") else " (no text could be read from it)"))
    budget = CTX_FILES
    body = []
    big = []
    for n in names:
        e = idx[n]
        chunks = e.get("chunks") or []
        if e.get("chars", 0) and e["chars"] <= CTX_WHOLE and budget > 0:
            t = "\n\n".join(chunks)[:budget]
            body.append(f"[{n}]\n{t}")
            budget -= len(t)
        else:
            big += [(n, i, c) for i, c in enumerate(chunks)]
    q = _terms(task)
    if big and budget > 0:
        scored = []
        for n, i, c in big:
            s = len(q & _terms(c)) if q else 0
            scored.append((-s, n.lower(), i, n, c))
        scored.sort()
        picked = [x for x in scored if x[0] < 0] or scored[:2]
        used = []
        for _, _, i, n, c in picked:
            if budget < 200:
                break
            c = c[:budget]
            used.append((n, i, c))
            budget -= len(c)
        for n, i, c in sorted(used, key=lambda x: (x[0].lower(), x[1])):
            body.append(f"[{n}, passage {i + 1}]\n{c}")
    return "\n".join(lines) + ("\n\n" + "\n\n".join(body) if body else "")


# ---------- the text a project adds to a chat's first message ----------------------------------------------------------
def context_parts(pid: str, task: str = "", instructions_on: bool = True, memory_on: bool = True,
                  exclude=()) -> tuple[str, str]:
    """(instructions, background): the user's directions for this project, and the data (memory and knowledge) the
    app puts inside the memory fence. Both may be empty."""
    try:
        d = _exists(pid)
    except (KeyError, ValueError):
        return "", ""
    m = _meta(d)
    head = f"This chat is in the project \"{m.get('name', '')}\"."
    if m.get("description"):
        head += " " + m["description"].strip()
    ins = _read(d / "instructions.md").strip() if instructions_on else ""
    text = head
    if ins:
        text += ("\nThe user's instructions for every chat in this project (follow them; where they differ from the "
                 "general instructions, these win):\n" + ins[:INSTR_MAX])
    back = []
    from meshpool_harness import memstore as MS
    mem = MS.project_text(pid, exclude) if memory_on else ""
    if mem:
        back.append("About this project:\n" + mem[-2500:])
    k = knowledge_context(pid, task)
    if k:
        back.append(k)
    return text, "\n\n".join(back)


# ---------- chats' own folders -----------------------------------------------------------------------------------------
def new_scratch(pid: str = "") -> str:
    """A private working folder for a chat with no linked folder. In a project, the project's knowledge files are
    copied into it (knowledge/) so the agent can open them in full."""
    d = chats_root() / uuid.uuid4().hex[:12]
    d.mkdir(parents=True, exist_ok=True)
    if pid:
        try:
            _exists(pid)
            (d / ".project").write_text(pid, encoding="utf-8")
            k = _kdir(pid)
            names = [n for n in _index(_exists(pid)) if (k / n).is_file()]
            if names:
                (d / "knowledge").mkdir(exist_ok=True)
                for n in names:
                    try:           # a copy, not a link: an edit by the agent must never reach the project's own file
                        shutil.copy2(k / n, d / "knowledge" / n)
                    except OSError:
                        pass
        except (KeyError, ValueError):
            pass
    return str(d.resolve())


def is_scratch(path: str) -> bool:
    try:
        p = Path(path).expanduser().resolve()
        return p.parent == chats_root().resolve()
    except OSError:
        return False


def remove_scratch(path: str) -> None:
    if path and is_scratch(path):
        shutil.rmtree(path, ignore_errors=True)


def for_root(path: str) -> str:
    """The project a working folder belongs to: a chat's own folder made inside a project, or a linked folder."""
    if not path:
        return ""
    try:
        p = Path(path).expanduser().resolve()
    except OSError:
        return ""
    if is_scratch(str(p)):
        pid = _read(p / ".project").strip()
        return pid if ID_RX.fullmatch(pid) and (root() / pid / "project.json").is_file() else ""
    return legacy_map().get(str(p), "")


# ---------- migration --------------------------------------------------------------------------------------------------
def _migrated_file() -> Path:
    return root() / ".migrated.json"


def migrate(extra_paths=()) -> list[dict]:
    """Turn the folders the old app listed (projects.json), and folders its chats worked in, into projects with a
    linked folder, once each. Copies the folder's old notes into the project's memory. Reads old data only."""
    from meshpool_harness import assistant as AS
    from meshpool_harness.ui import workbench as W
    try:
        from meshpool_harness.ui.desktop import default_project
        skip = {str(default_project().resolve()), str(Path.home().resolve())}
    except Exception:  # noqa: BLE001
        skip = {str(Path.home().resolve())}
    with _LOCK:
        try:
            seen = json.loads(_migrated_file().read_text(encoding="utf-8")).get("paths", {})
        except (OSError, ValueError):
            seen = {}
        names = {}
        try:
            for r in W.projects():
                names[r["path"]] = r.get("name") or Path(r["path"]).name
        except Exception:  # noqa: BLE001
            pass
        for p in extra_paths:
            if p and p not in names:
                names[str(p)] = Path(p).name
        made = []
        for path, name in names.items():
            try:
                rp = str(Path(path).expanduser().resolve())
            except OSError:
                continue
            if rp in seen or rp in skip or is_scratch(rp) or not Path(rp).is_dir():
                continue
            managed = False
            try:
                managed = W.is_managed(rp)
            except Exception:  # noqa: BLE001
                pass
            link = {"type": "folder", "path": rp}
            if managed:
                try:
                    slug = W._github_slug(W._git(Path(rp), "remote", "get-url", "origin", check=False))
                except Exception:  # noqa: BLE001
                    slug = ""
                link = {"type": "github", "path": rp, "repo": slug, "branch": ""}
            proj = create(name, link=link, legacy_path=rp)
            old = AS.project_memory(rp)
            if old:
                memory_set(proj["id"], old)
            seen[rp] = proj["id"]
            made.append(proj)
        _write(_migrated_file(), json.dumps({"version": 1, "paths": seen}, indent=1))
        return made
