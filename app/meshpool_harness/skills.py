"""Skills: folders with a SKILL.md (name and description in YAML front matter, then instructions), the same format
Codex and Hermes use. They live in the Meshpool data folder under skills/. Agents see each skill's name and
description, and load the full instructions when a task matches (or when the user mentions $skill-name).

Meshpool ships a few skills in defaults/skills; the user adds their own by dropping a folder, a .zip or a SKILL.md
on the Skills page, or by writing one there.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from pathlib import Path

from meshpool_harness import config as C

NAME_RE = re.compile(r"[^a-z0-9-]+")


def folder() -> Path:
    return C.home() / "skills"


def _state_path() -> Path:
    return folder() / ".disabled.json"


def _disabled() -> set:
    try:
        return set(json.loads(_state_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_disabled(s: set):
    folder().mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(sorted(s)), encoding="utf-8")


def slug(name: str) -> str:
    s = NAME_RE.sub("-", name.strip().lower()).strip("-")
    return s[:64] or "skill"


def parse(text: str) -> tuple[dict, str]:
    """Front matter and body. Each key: value line is read as text; where the front matter is valid YAML, its string
    values (quoted, escaped, folded or block) replace the raw text."""
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" in line and not line.startswith((" ", "\t")):
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"').strip("'")
            try:
                import yaml
                doc = yaml.load(text[3:end], Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
            except Exception:  # noqa: BLE001 - not valid YAML (or no PyYAML): keep the line-by-line reading
                doc = None
            if isinstance(doc, dict):
                for k, v in doc.items():
                    if isinstance(k, str) and isinstance(v, str):
                        meta[k.strip()] = v.strip()
            body = text[end + 4:].lstrip("\n")
    return meta, body


def _front_list(text: str, *keys: str) -> list[str]:
    """A front-matter value that may be a list: a YAML list, [a, b], or a comma or space separated line."""
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end < 0 or not any(f"\n{k}:" in text[:end + 1] for k in keys):   # cheap: most skills have none of these
        return []
    raw = None
    try:
        import yaml
        doc = yaml.load(text[3:end], Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
        if isinstance(doc, dict):
            raw = next((doc[k] for k in keys if doc.get(k) not in (None, "")), None)
    except Exception:  # noqa: BLE001
        raw = None
    if raw is None:
        meta, _ = parse(text)
        raw = next((meta[k] for k in keys if meta.get(k)), "")
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw).strip("[]"))
    return [str(x).strip().strip("'\"") for x in items if str(x).strip().strip("'\"")]


def _flag(meta: dict, key: str, default: bool) -> bool:
    v = str(meta.get(key, meta.get(key.replace("-", "_"), ""))).strip().lower()
    return default if v == "" else v in ("true", "yes", "1", "on")


def front_matter(**kv) -> str:
    """A SKILL.md front matter block. Values are written as YAML (quoted when needed), so a description with a colon,
    a '#', quotes or a leading '-' still parses in Codex, Hermes and here. Empty values are left out."""
    import yaml
    lines = [yaml.safe_dump({k: v}, allow_unicode=True, default_flow_style=False, sort_keys=False,
                            width=1_000_000).rstrip("\n") for k, v in kv.items() if v not in (None, "")]
    return "---\n" + "\n".join(lines) + "\n---\n"


# Default skills whose text changed in a release because they told the agent to install something that now ships inside the
# app (docs/plan/0.0.11-bundled.md). An install that has the OLD default, byte for byte (the user never edited it), gets
# the new one; an edited copy is left alone. {skill: SHA-256 of every earlier shipped SKILL.md}.
REFRESHED_DEFAULTS = {
    "transcribe-audio": {"3e9a05eb613a601a3209285f2713f169b8edcd8694fee67151699df706332dfb"},
    "convert-files": {"31921397babd7efde4b533297328b4835119c7e23820bf4f86aca81dd87bcc7d", "7208b2af222dc3274042665c9004888e0af0c86e21b9204606f7541085feda3d"},
    "pdf-tools": {"6bda4b25570556d043a9654ccf0df38ec61f90723de60e4a38d3907780737ff0"},
    "image-tools": {"221c5aa55b774d466a4c6b2274655dac71b792ee7d6328ea2272fe28904ef576"},
    "notebooks": {"a15b7391182d52e2b2bf66363a2962d84f02da8bbd7c6f47df556e16c9299d29"},
    "query-database": {"49bfb409b2c1c6f05042e2a8af283781b9b9ddf942ef8cfa0669783f10054f94"},
    "make-chart": {"34720b574aa2bcaf369328f618f7988701719ece76f1b12962ebb07394a2ca71"},
    # 0.0.13: skills that asked for more than the user did (plan files, trackers, a second document, deep research)
    "email-newsletter": {"3e006a413bbce93ac5d7fb6327fd959405c1511355dc8d99cc7cc3312ef45bc0"},
    "blog-post": {"64329c4a7a5640120049b494a874b18fedee62b836edc8357a9e54d34b02507c"},
    "daily-plan": {"39a7889361211ccdc75b3ae0ee7b02311f1dc45038e2d93d0d0b21f087dce450", "c85a178ba386cfe28c7d725636eb15fd64a87c0bc8a7000dfffb9a6832d9de0c"},
    "briefing-interview": {"244a32c6ff198ba8c57adf769e1edf0bb1e21def30f3633c3796e390482390c7"},
    "project-plan": {"792a70ee3c312b61ec7344934e71af4e25701bc0b9b78c4ad42fdda5d46ed4fc", "c73fd6570355ba425cc6a7bebad3100ad1345da23901b4e021ecb28606f37168"},
    "competitor-analysis": {"b507ab50e61130618751871ef028a21e6f5285d1ec1fad234ba7ebdfba5eebb4"},
    "research-brief": {"cea677509224462597685db1614e8df91209ba092f5867d7210acfbae3edb5dd"},
    "meeting-prep": {"240458f6c4f23466eebd1626f2e23180bdb3659d5fcf171cec284e36794724da", "c37f5e609262cf0b3fe78d2d5e632d527ae82223f56448f3aac26142b41a2efd"},
    "seo-content-brief": {"9a87b2e21973afde065dad87733e2885e92e495f130c175a41fb3ee79f266f36", "b34e659ca9f56eb1bb2f60ddf5c664517d0262045b1e49ef47d7541e53a10d6d"},
    "feedback-synthesis": {"15c2c58b9d1487f8da1ccd0853e1d6288012098ad8c81cb4dccc8b571b61aa53"},
    "survey-analysis": {"406d25eb7f07630f9093481c9eae463b48c76e53788e31fd639f090c020b78d9", "24cc5214b6721eb05045d8d788cdd79cf554c932e99feb281348453c4edbea2e"},
    "interview-plan": {"5842c313a72830ea3c96f3d8624b10fb8b1671eef090e152c9c624b76475815e", "3167c7573767195cc1ab40ed86d91fd9d4a49c62d138f40d51637ab55b256c86"},
    # 0.0.15: front matter made valid YAML (descriptions with ": " quoted), so other tools can read these too
    "build-app": {"d95588280427c5e9da6ee2d6efc8fa9aeed29b33c37b5fa99ed5153a45a98047"},
    "chat-catch-up": {"db465c84ff15ea2f2c88bbf121dc14506fbc05524537c79004a43c4103a0c476"},
    "finance-assumption-book": {"d5d26f7803e8691bb4eb52a297fbf7f9f0ba721078fb82232bd073d808799a60"},
    "git-help": {"9a8b46135ee6c523e06492c9055e8314d76b7295dd3e665c1f78597c5ae2bc3c"},
    "make-diagram": {"7439477e886f91b9853b428025ff1930554eab1c9c1bba5f609442f30bf9cc25"},
    "memo": {"b67109067f7868b1851cb2f8adf10ada241cdfb3f5b5bbb8d9fa2bc549a6b780"},
    "power-bi-review": {"0557b0a1139d0d3019a8efa3c3ed09763be103da1c4231e6987e0300a9af6a30"},
    "prd": {"e4ce0c2a415a95d29239a5d5083f4483468ed307f934c44acc22193cc1363c33"},
    "weekly-status-update": {"f0572cca30ecf83859d1de910caff1f81d63a268ed68de817b89061997477d07"},
}


def _refresh_unedited_defaults(src: Path, dst: Path) -> None:
    import hashlib
    for name, old in REFRESHED_DEFAULTS.items():
        have, shipped = dst / name / "SKILL.md", src / name
        try:
            if have.is_file() and shipped.is_dir() and hashlib.sha256(have.read_bytes()).hexdigest() in old:
                shutil.rmtree(dst / name)
                shutil.copytree(shipped, dst / name, copy_function=shutil.copyfile)
        except OSError:
            pass                                      # a locked file: the next start tries again


def ensure_defaults():
    src = C.APP / "defaults" / "skills"
    dst = folder()
    dst.mkdir(parents=True, exist_ok=True)
    marker = dst / ".defaults-installed"
    if not src.is_dir():
        return
    done: set = set()                               # names already offered once; a removed default stays removed
    if marker.exists():
        try:
            v = json.loads(marker.read_text(encoding="utf-8"))
        except ValueError:
            v = None
        # 2.2 wrote "1" after installing its three defaults; anything newer is offered once
        done = set(v) if isinstance(v, list) else {"code-review", "pull-request", "research-brief"}
    _refresh_unedited_defaults(src, dst)
    new = [d for d in src.iterdir() if d.is_dir() and d.name not in done]
    if not new:
        return
    for d in new:
        if not (dst / d.name).exists():
            shutil.copytree(d, dst / d.name, copy_function=shutil.copyfile)
    marker.write_text(json.dumps(sorted(done | {d.name for d in new})), encoding="utf-8")


def expand_mentions(text: str) -> str:
    """For harnesses with their own skill systems (Pi, Hermes): when the message names $skill, attach that skill's
    instructions so it is followed the same way in every harness."""
    out, done = [], set()
    for m in re.finditer(r"(?<![\w$])\$([A-Za-z0-9][\w-]{1,63})([^\n]*)", text):
        if m.group(1).lower() in done:
            continue
        done.add(m.group(1).lower())
        sk = get(m.group(1))
        if sk and sk.get("enabled", True) and sk.get("user_ok", True):
            try:
                out.append(instructions(m.group(1), m.group(2).strip(), by_user=True))
            except ValueError:
                pass
    if not out:
        return text
    return text + "\n\n\u200b---\nThe user asked for these skills. Follow their instructions:\n\n" + "\n\n---\n".join(out)


def load_all(include_disabled: bool = True) -> list[dict]:
    ensure_defaults()
    off = _disabled()
    out = []
    for d in sorted(folder().iterdir()) if folder().exists() else []:
        f = d / "SKILL.md"
        if not d.is_dir() or not f.is_file():
            continue
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
            meta, body = parse(raw)
        except OSError:
            continue
        sid = d.name
        row = {"id": sid, "name": meta.get("name") or sid, "description": meta.get("description", ""),
               "when": meta.get("when_to_use") or meta.get("when-to-use") or "",
               "model_ok": not _flag(meta, "disable-model-invocation", False),   # only when the user asks for it
               "user_ok": _flag(meta, "user-invocable", True),                   # only the model loads it
               "disallowed": _front_list(raw, "disallowed-tools", "disallowed_tools"),
               "paths": _front_list(raw, "paths"),
               "argument_hint": meta.get("argument-hint") or meta.get("argument_hint") or "",
               "enabled": sid not in off, "path": str(f),
               "files": sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() and p.name != "SKILL.md")[:50]}
        if include_disabled or row["enabled"]:
            out.append(row)
    return out


def enabled() -> list[dict]:
    return load_all(include_disabled=False)


def for_model() -> list[dict]:
    """The enabled skills the agent may pick by itself (not the ones marked disable-model-invocation)."""
    return [s for s in enabled() if s.get("model_ok", True)]


def _desc(s: dict, n: int = 0) -> str:
    d = s["description"] + (f" Use when: {s['when']}" if s.get("when") else "")
    return d[:n] if n else d


def for_path(rel: str, seen: set) -> str:
    """A note naming skills whose paths: patterns match a file the agent is working on, once per skill."""
    from meshpool_harness.agent import extras as EX
    hits = []
    for s in for_model():
        key = "skill:" + s["id"]
        if s.get("paths") and key not in seen and any(EX._glob_match(rel, g) for g in s["paths"]):
            seen.add(key)
            hits.append(f"- {s['name']}: {_desc(s, 300)}")
    return ("[Skills made for files like this one (load one with use_skill if it fits the task):]\n" + "\n".join(hits)
            ) if hits else ""


def get(name: str) -> dict | None:
    key = slug(name)
    for s in load_all():
        if s["id"] == key or slug(s["name"]) == key:
            return s
    return None


def _with_arguments(text: str, arguments: str) -> str:
    """$ARGUMENTS (or ${ARGUMENTS}) is everything given; $1 to $9 are its words. A skill with none of these and
    some arguments gets them at the end."""
    import shlex
    arguments = str(arguments or "").strip()
    try:
        words = shlex.split(arguments)
    except ValueError:
        words = arguments.split()
    used = bool(re.search(r"\$(\{ARGUMENTS\}|ARGUMENTS\b|[1-9]\b)", text))
    text = re.sub(r"\$\{ARGUMENTS\}|\$ARGUMENTS\b", lambda m: arguments, text)
    text = re.sub(r"\$([1-9])\b", lambda m: words[int(m.group(1)) - 1] if int(m.group(1)) <= len(words) else "", text)
    if arguments and not used:
        text += f"\n\nArguments: {arguments}"
    return text


def instructions(name: str, arguments: str = "", by_user: bool = False) -> str:
    s = get(name)
    if not s or (not by_user and not s.get("model_ok", True)):
        names = ", ".join(x["name"] for x in enabled() if x.get("model_ok", True)) or "none"
        if s:
            raise ValueError(f"The skill {s['name']} runs only when the user asks for it (${s['id']}). "
                             f"Other skills: {names}")
        raise ValueError(f"No skill named {name}. Available: {names}")
    text = _with_arguments(Path(s["path"]).read_text(encoding="utf-8", errors="replace"), arguments)
    extra = ""
    if s["files"]:
        extra = (f"\n\nFiles that come with this skill (read them with read_file using these full paths):\n"
                 + "\n".join(str(Path(s["path"]).parent / f) for f in s["files"]))
    return f"Skill: {s['name']}\nFolder: {Path(s['path']).parent}\n\n{text}{extra}"


# Above this many enabled skills the system prompt lists names only and the agent searches descriptions with
# find_skill. With ~100 skills the full list was ~30k characters (~7.5k tokens) on every model call of every chat:
# slower first tokens and more for the model to wade through. MH_SKILLS=full keeps the full list.
ON_DEMAND_MIN = 15
NAMES_MAX = 60
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "with", "my", "me", "i", "is", "it", "this",
         "that", "be", "use", "when", "from", "by", "as", "at", "your", "you", "can", "do", "how", "what"}


def on_demand() -> bool:
    import os
    return os.environ.get("MH_SKILLS", "").lower() != "full" and len(enabled()) > ON_DEMAND_MIN


def _terms(text: str) -> list[str]:
    out = []
    for w in _WORD.findall((text or "").lower()):
        if w in _STOP or len(w) < 2:
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith("s") else w)   # decks -> deck
    return out


def search(query: str, k: int = 8, rows: list | None = None) -> list[dict]:
    """Skills ranked by word overlap with the query; a hit in the name counts three times one in the description."""
    q = set(_terms(query))
    if not q:
        return []
    rows = for_model() if rows is None else rows
    scored = []
    for s in rows:
        name, desc = set(_terms(s["name"].replace("-", " "))), set(_terms(_desc(s)))
        score = 3 * len(q & name) + len(q & desc)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    return [s for _, s in scored[:k]]


def search_text(query: str) -> str:
    hits = search(query)
    if not hits:
        return "No skill matches. Try other words, or go ahead without a skill."
    return "Best matching skills (load one with use_skill):\n" + "\n".join(
        f"- {s['name']}: {_desc(s, 300)}" for s in hits)


def catalog_text(task: str = "", searchable: bool = True) -> str:
    """The skills list for an agent's system prompt: in full for a few skills, otherwise names only plus the
    ones that match the task the chat started with."""
    rows = for_model()
    if not rows:
        return ""
    if not on_demand():
        lines = [f"- {s['name']}: {_desc(s)}" for s in rows]
        return ("\n\nSkills you can use (call use_skill with the name to load full instructions before doing a task "
                "that matches one; when the user writes $name, load that skill first):\n" + "\n".join(lines))
    if not searchable:                            # a lean harness: only the skills that match its task
        hits = search(task, k=3, rows=rows) if task else []
        return ("\n\nSkills that may fit this task (load one with use_skill):\n" + "\n".join(
            f"- {s['name']}: {_desc(s, 200)}" for s in hits)) if hits else ""
    text = (f"\n\nSkills: {len(rows)} saved instruction sets for specific jobs. Before a task that may match one, "
            "call find_skill with a few words; load a match with use_skill. A skill the user names with $name is "
            "attached to their message already.")
    if len(rows) <= NAMES_MAX:                    # a few dozen names are cheap; hundreds (imported) are not
        text += " Names: " + ", ".join(s["name"] for s in rows) + "."
    hits = search(task, k=5, rows=rows) if task else []
    if hits:
        text += "\nLikely relevant to this chat:\n" + "\n".join(f"- {s['name']}: {_desc(s, 240)}" for s in hits)
    return text


# ---------- changes from the app ---------------------------------------------------------------------------------
def create(name: str, description: str, body: str, overwrite: bool = False) -> dict:
    if not name.strip() or not description.strip():
        raise ValueError("A skill needs a name and a one-line description.")
    sid = slug(name)
    d = folder() / sid
    if d.exists() and not overwrite:
        raise ValueError(f"A skill named {sid} already exists.")
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(front_matter(name=sid, description=description.strip()) + f"\n{body.strip()}\n",
                                encoding="utf-8")
    return get(sid)


def update(sid: str, text: str) -> dict:
    d = folder() / slug(sid)
    if not (d / "SKILL.md").exists():
        raise ValueError("No such skill.")
    meta, _ = parse(text)
    if not meta.get("description"):
        raise ValueError("SKILL.md needs front matter with a description line.")
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return get(sid)


def add_files(files: list[tuple[str, bytes]]) -> list[dict]:
    """Add skills from uploads: a .zip of one or more skill folders, a SKILL.md, or files of one folder
    (names like my-skill/SKILL.md, my-skill/scripts/x.py)."""
    added = []
    folder().mkdir(parents=True, exist_ok=True)
    loose: dict[str, list[tuple[str, bytes]]] = {}
    for name, data in files:
        name = name.replace("\\", "/").lstrip("/")
        if name.lower().endswith((".zip", ".skill")):
            added += _from_zip(data)
        elif name.endswith("SKILL.md") and "/" not in name:
            meta, _ = parse(data.decode("utf-8", "replace"))
            sid = slug(meta.get("name") or "skill")
            d = folder() / sid
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_bytes(data)
            added.append(sid)
        elif "/" in name:
            top, rest = name.split("/", 1)
            loose.setdefault(top, []).append((rest, data))
        elif name.lower().endswith(".md"):
            meta, body = parse(data.decode("utf-8", "replace"))
            sid = slug(meta.get("name") or Path(name).stem)
            d = folder() / sid
            d.mkdir(parents=True, exist_ok=True)
            text = data.decode("utf-8", "replace")
            if not meta.get("description"):
                text = front_matter(name=sid, description=Path(name).stem.replace('-', ' ')) + f"\n{text}"
            (d / "SKILL.md").write_text(text, encoding="utf-8")
            added.append(sid)
    for top, items in loose.items():
        if not any(r == "SKILL.md" for r, _ in items):
            continue
        meta, _ = parse(next(d for r, d in items if r == "SKILL.md").decode("utf-8", "replace"))
        d = folder() / slug(meta.get("name") or top)
        for rel, data in items:
            p = (d / rel).resolve()
            if d.resolve() not in p.parents and p != d.resolve():
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        added.append(d.name)
    if not added:
        raise ValueError("No skill found. Add a folder or .zip that contains a SKILL.md, or a SKILL.md file.")
    return [get(a) for a in added if get(a)]


def _from_zip(data: bytes) -> list[str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if not n.endswith("/") and "__MACOSX" not in n]
    roots = sorted({n.rsplit("/SKILL.md", 1)[0] if "/" in n else "" for n in names if n.endswith("SKILL.md")})
    out = []
    for root in roots:
        prefix = (root + "/") if root else ""
        meta, _ = parse(z.read(prefix + "SKILL.md").decode("utf-8", "replace"))
        d = folder() / slug(meta.get("name") or (root.rsplit("/", 1)[-1] if root else "skill"))
        for n in names:
            if not n.startswith(prefix):
                continue
            rel = n[len(prefix):]
            p = (d / rel).resolve()
            if d.resolve() not in p.parents:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(z.read(n))
        out.append(d.name)
    return out


def toggle(sid: str, on: bool):
    off = _disabled()
    (off.discard if on else off.add)(sid)
    _save_disabled(off)


def remove(sid: str):
    d = folder() / slug(sid)
    if d.is_dir() and d.parent == folder():
        shutil.rmtree(d)


def sync_to(dst: Path):
    """Mirror enabled skills into another harness's skills folder (Hermes reads HERMES_HOME/skills)."""
    dst.mkdir(parents=True, exist_ok=True)
    want = {s["id"]: Path(s["path"]).parent for s in enabled()}
    for d in dst.iterdir():
        if d.is_dir() and (d / ".from-meshpool").exists() and d.name not in want:
            shutil.rmtree(d, ignore_errors=True)
    for sid, src in want.items():
        t = dst / sid
        if t.exists() and not (t / ".from-meshpool").exists():
            continue                                # the harness has its own skill with this name: keep it
        shutil.rmtree(t, ignore_errors=True)
        shutil.copytree(src, t, copy_function=shutil.copyfile)
        (t / ".from-meshpool").write_text("1", encoding="utf-8")
