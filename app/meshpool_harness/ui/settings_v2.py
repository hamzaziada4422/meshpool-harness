"""What the regrouped Settings pages need beyond the old ones: the profile, capability switches, key spend and usage
by day, where the data lives (export, clear). server.py exposes them under /api/profile, /api/capabilities,
/api/spend, /api/data."""
from __future__ import annotations

import io
import json
import os
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path

from meshpool_harness import assistant as AS
from meshpool_harness import config as C

BILLING_URL = "https://meshpool.ai"          # where keys and accounts are managed

# ---------- profile ----------------------------------------------------------------------------------------------------
PROFILE_KEYS = ("name", "address", "language", "plan_first")


def _pfile() -> Path:
    return AS._dir() / "profile.json"


def profile() -> dict:
    try:
        d = json.loads(AS._read(_pfile()) or "{}")
    except ValueError:
        d = {}
    return {"name": str(d.get("name", ""))[:80], "address": str(d.get("address", ""))[:80],
            "language": str(d.get("language", ""))[:40], "plan_first": bool(d.get("plan_first", False))}


def save_profile(**kw) -> dict:
    d = profile()
    for k in ("name", "address", "language"):
        if kw.get(k) is not None:
            d[k] = " ".join(str(kw[k]).split())[:80]
    if kw.get("plan_first") is not None:
        d["plan_first"] = bool(kw["plan_first"])
    with AS._memory_lock():
        AS._write(_pfile(), json.dumps(d))
    return d


def profile_note() -> str:
    """One line for a chat's first message: who the user is and the language to answer in."""
    p = profile()
    bits = []
    if p["name"]:
        bits.append(f"The user's name is {p['name']}" + (f"; address them as {p['address']}" if p["address"] else "") + ".")
    elif p["address"]:
        bits.append(f"Address the user as {p['address']}.")
    if p["language"]:
        bits.append(f"Reply in {p['language']} unless the user writes in another language.")
    return " ".join(bits)


# ---------- capabilities --------------------------------------------------------------------------------------------------
# id, name, what it does, where the switch lives. "server" ones turn a built-in tool server on or off in mcp.json.
CAPS = [
    {"id": "memory", "name": "Memory", "desc": "Remember lasting facts and preferences between chats.", "kind": "memory"},
    {"id": "web", "name": "Web search", "desc": "Search the web, read pages and research across sources.", "kind": "server", "server": "meshpool-web"},
    {"id": "browser", "name": "Browser use", "desc": "Open pages, click, type and fill in forms for you.", "kind": "server", "server": "browser"},
    {"id": "subtasks", "name": "Sub-tasks", "desc": "Split big jobs into parts that run at the same time.", "kind": "env", "env": "MH_SUBAGENTS"},
    {"id": "plan_first", "name": "Plan first", "desc": "New chats start with a plan and wait for your OK.", "kind": "profile", "advanced": True},
    {"id": "computer", "name": "Computer use", "desc": "Let Meshpool see your screen and use the mouse and keyboard.", "kind": "server", "server": "computer-use", "advanced": True},
]


def _server_on(name: str) -> bool | None:
    from meshpool_harness.agent import mcp as M
    try:
        s = M.load_config().get(name)
    except (OSError, ValueError):
        return None
    return None if not isinstance(s, dict) else not s.get("disabled")


def capabilities() -> list[dict]:
    out = []
    for c in CAPS:
        k = c["kind"]
        if k == "memory":
            on = AS.settings()["memory_on"]
        elif k == "server":
            on = _server_on(c["server"])
        elif k == "env":
            on = (C.read().get(c["env"]) or os.environ.get(c["env"], "1")) != "0"
        else:
            on = profile()["plan_first"]
        row = {"id": c["id"], "name": c["name"], "desc": c["desc"], "on": on, "advanced": bool(c.get("advanced")),
               "available": on is not None}
        if c["id"] == "browser":
            row["browser_use"] = browser_use()              # which browser: Meshpool's, the installed one, or attached
        if c["id"] == "computer":
            row["computer_status"] = computer_status()      # what is missing (macOS permissions, Windows lock, Linux)
        out.append(row)
    return out


def browser_use() -> dict:
    """{mode, modes[{id, name, desc, available, detail}], cdp_url, installed[...]}: the "Which browser" setting."""
    from meshpool_harness import browser_use as BU
    return BU.describe()


def set_browser_use(mode: str, cdp_url: str | None = None, app: str | None = None) -> dict:
    """Save which browser browser use drives (meshpool, installed, attach), the debugging address for attach and, for
    installed, which of chrome, edge, brave (empty: the first found). New chats use it."""
    from meshpool_harness import browser_use as BU
    vals = {"MH_BROWSER_USE": str(mode or "meshpool").strip().lower()}
    if cdp_url is not None:
        vals["MH_BROWSER_CDP_URL"] = str(cdp_url).strip()
    if app is not None:
        vals["MH_BROWSER_APP"] = str(app).strip().lower()
    BU.validate(vals)
    C.write(vals)
    for k, v in vals.items():
        os.environ[k] = v
    return browser_use()


def computer_status() -> dict:
    from meshpool_harness.agent import computer_mcp_server as CU
    return CU.computer_status()


def set_capability(cid: str, on: bool) -> list[dict]:
    c = next((x for x in CAPS if x["id"] == cid), None)
    if not c:
        raise ValueError("Unknown feature.")
    k = c["kind"]
    if k == "memory":
        AS.save_settings(memory_on=on)
    elif k == "server":
        from meshpool_harness.agent import mcp as M
        if _server_on(c["server"]) is None:
            raise ValueError(f"{c['name']} isn't set up on this computer.")

        def toggle(servers):
            s = servers[c["server"]]
            s.pop("disabled", None) if on else s.__setitem__("disabled", True)
        M.edit_config(toggle, create=False)
    elif k == "env":
        C.write({c["env"]: "1" if on else "0"})
        C.load_into_env(override=True)
    else:
        save_profile(plan_first=on)
    return capabilities()


# ---------- spend ---------------------------------------------------------------------------------------------------------
_SPEND = {"t": 0.0, "v": None}


def key_spend(force: bool = False) -> dict:
    """What the Meshpool key has spent and its limit, from the headers the model service adds to any reply (a
    one-token call, at most once a minute). {available: False} when the service does not say. The key is never
    returned."""
    if not force and time.time() - _SPEND["t"] < 60 and _SPEND["v"] is not None:
        return _SPEND["v"]
    from meshpool_harness import providers as PV
    res = {"available": False, "spent": None, "budget": None}
    try:
        key, base = PV.key(), PV.key_base()
        if key:
            model = C.read().get("MH_MODEL") or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
            mdl = PV.split(model)[1]
            body = json.dumps(PV.adapt_body(base, {"model": mdl, "max_tokens": 16 if PV.is_azure(base) else 1,
                                                   "messages": [{"role": "user", "content": "1"}]})).encode()
            url, auth = PV.chat_target(base, key, mdl)
            r = urllib.request.Request(url, data=body, headers={**auth, "Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=20) as resp:
                sp, mx = resp.headers.get("x-litellm-key-spend"), resp.headers.get("x-litellm-key-max-budget")
                if sp not in (None, ""):
                    res.update(available=True, spent=round(float(sp), 4))
                if mx not in (None, "", "None"):
                    res["budget"] = round(float(mx), 2)
    except Exception:  # noqa: BLE001 - the page says "not available"; never an error for this
        pass
    _SPEND.update(t=time.time(), v=res)
    return res


def usage_by_day(days: int = 30) -> list[dict]:
    """Tokens per day from the saved chats (a chat counts on the day it was last used)."""
    from meshpool_harness.agent.core import SESSIONS
    buckets: dict[str, dict] = {}
    for p in SESSIONS.glob("*/session.json") if SESSIONS.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        u = d.get("usage") or {}
        day = time.strftime("%Y-%m-%d", time.localtime(d.get("updated", 0) or 0))
        b = buckets.setdefault(day, {"day": day, "in": 0, "out": 0, "calls": 0})
        b["in"] += int(u.get("in") or 0)
        b["out"] += int(u.get("out") or 0)
        b["calls"] += int(u.get("calls") or 0)
    return sorted(buckets.values(), key=lambda r: r["day"], reverse=True)[:days]


# ---------- data ----------------------------------------------------------------------------------------------------------
def data_info() -> dict:
    from meshpool_harness.agent.core import SESSIONS
    n = size = 0
    for p in SESSIONS.glob("*/session.json") if SESSIONS.exists() else []:
        if p.parent.name.startswith("sub-"):
            continue
        n += 1
        try:
            size += p.stat().st_size
        except OSError:
            pass
    return {"folder": str(C.home()), "chats": n, "bytes": size}


def export_zip() -> bytes:
    """Every chat's history and the memory files, as a zip the user can keep."""
    from meshpool_harness.agent.core import SESSIONS
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in SESSIONS.glob("*/session.json") if SESSIONS.exists() else []:
            if not p.parent.name.startswith("sub-"):
                z.write(p, f"chats/{p.parent.name}/session.json")
                m = p.parent / "meta.json"
                if m.is_file():
                    z.write(m, f"chats/{p.parent.name}/meta.json")
        for name in ("memory/memory.md", "memory/instructions.md", "memory/profile.json"):
            f = C.home() / name
            if f.is_file():
                z.write(f, name)
        proot = C.home() / "projects"
        if proot.is_dir():
            for f in proot.rglob("*"):
                if f.is_file() and "knowledge" not in f.parts:
                    z.write(f, "projects/" + str(f.relative_to(proot)))
    return buf.getvalue()


def clear_chats() -> int:
    """Delete every chat (and the private folders of chats that had no folder). Memory, projects and settings stay."""
    from meshpool_harness.ui import agent_api as A
    n = 0
    ids = [d.name for d in sorted(A.SESSIONS.iterdir()) if d.is_dir() and not d.name.startswith("sub-")
           and (d / "session.json").is_file()] if A.SESSIONS.is_dir() else []
    for sid in ids:
        try:
            A.thread_action(sid, {"action": "delete"})
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n
