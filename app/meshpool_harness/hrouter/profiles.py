"""Harness profiles: a named, complete harness setup, and the config that decides which one a route runs under.

A profile sets the switches that used to be global env flags: plan mode, the independent review, mutation feedback,
the compaction limit, the spec-text set ("methods"), lean tools, per-step methods, and which engine runs it.

    {"plan": "auto"|"on"|"off", "review": bool, "mutation": bool, "compact_chars": int,
     "methods": null | "<name>", "lean_tools": bool, "step_methods": bool,
     "engine": "meshpool"|"pi"|"hermes", "extends": "<profile>"}

Assignment (app/defaults/profiles.json, then <data folder>/profiles.json, then MH_PROFILES_FILE; later wins):

    {"default": "full", "assign": {"code.debug": "lean", "code.build.python": "lean+review"}, "profiles": {...}}

A route resolves its profile from the most specific key that has an assignment (spec.variant.language, spec.language,
spec.variant, spec), else "default". MH_HARNESS_PROFILE=<name> (or MH_PROFILE=<name> when it names a harness profile)
forces one profile for every route. Explicit env flags (MH_PLAN, MH_REVIEW, MH_MUTATION, MH_COMPACT_CHARS,
MH_LEAN_TOOLS, MH_SPECS_FILE) still win over the profile, so existing experiments keep working. With no profiles.json
the profile is "full": exactly the behaviour before profiles existed.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ENGINES = ("meshpool", "pi", "hermes")

FULL = {"plan": "auto", "review": True, "mutation": True, "compact_chars": 160000, "methods": None,
        "lean_tools": True, "step_methods": False, "engine": "meshpool"}

BUILTIN = {
    "full": {},
    "lean": {"plan": "off", "review": False, "mutation": False, "methods": "v2"},
    "lean+plan": {"extends": "lean", "plan": "auto"},
    "lean+review": {"extends": "lean", "review": True},
}


def _plan(v) -> str:
    s = str(v).strip().lower()
    return "on" if s in ("1", "on", "always", "true") else "off" if s in ("0", "off", "never", "false") else "auto"


def _clean(d: dict) -> dict:
    """Keep only known keys, with sane types. Partial: only the keys the definition sets."""
    out: dict = {}
    if "plan" in d:
        out["plan"] = _plan(d["plan"])
    for k in ("review", "mutation", "lean_tools", "step_methods"):
        if k in d:
            out[k] = bool(d[k])
    if "compact_chars" in d:
        try:
            out["compact_chars"] = max(1000, int(d["compact_chars"]))
        except (TypeError, ValueError):
            pass
    if "methods" in d:
        m = d["methods"]
        out["methods"] = str(m) if m else None
    if "engine" in d:
        out["engine"] = d["engine"] if d["engine"] in ENGINES else "meshpool"
    if d.get("extends"):
        out["extends"] = str(d["extends"])
    return out


def _read(p) -> dict:
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8")) if p else {}
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def config() -> dict:
    """{"default": name, "assign": {key: name}, "profiles": {name: definition}} merged from the built-ins, app
    defaults, the data folder and MH_PROFILES_FILE (later wins, per profile and per key)."""
    from meshpool_harness import config as C
    cfg = {"default": "full", "assign": {}, "profiles": {k: dict(v) for k, v in BUILTIN.items()}}
    shipped = os.environ.get("MH_PROFILES_DEFAULTS", str(C.APP / "defaults" / "profiles.json"))   # "none": ignore it
    for p in ("" if shipped == "none" else shipped, C.home() / "profiles.json", os.environ.get("MH_PROFILES_FILE") or ""):
        d = _read(p)
        if isinstance(d.get("default"), str) and d["default"]:
            cfg["default"] = d["default"]
        if isinstance(d.get("assign"), dict):
            cfg["assign"].update({str(k): str(v) for k, v in d["assign"].items() if isinstance(v, str)})
        if isinstance(d.get("profiles"), dict):
            for name, df in d["profiles"].items():
                if isinstance(df, dict):
                    cfg["profiles"][str(name)] = df
    return cfg


def names() -> list[str]:
    return sorted(config()["profiles"])


def settings(name: str, cfg: dict | None = None) -> dict | None:
    """The complete settings of a profile (extends followed, FULL underneath), or None for an unknown name."""
    cfg = cfg or config()
    chain, cur, seen = [], name, set()
    while cur:
        if cur in seen or cur not in cfg["profiles"]:
            if cur not in cfg["profiles"] and not chain:
                return None
            break
        seen.add(cur)
        d = _clean(cfg["profiles"][cur])
        chain.append(d)
        cur = d.get("extends", "")
    out = dict(FULL)
    for d in reversed(chain):
        out.update({k: v for k, v in d.items() if k != "extends"})
    return out


def forced(cfg: dict | None = None) -> str:
    """The profile MH_HARNESS_PROFILE (or MH_PROFILE, when it names a harness profile) forces, else ''."""
    cfg = cfg or config()
    for var in ("MH_HARNESS_PROFILE", "MH_PROFILE"):       # MH_PROFILE also names a model profile (profiles/*.yaml)
        v = (os.environ.get(var) or "").strip()
        if v in cfg["profiles"]:
            return v
    return ""


def resolve(route) -> tuple[str, dict]:
    """(profile name, complete settings) for a route: forced, else the most specific assignment, else the default."""
    from .route import _keys
    cfg = config()
    name = forced(cfg)
    if not name:
        for k in reversed(_keys(route)):
            if cfg["assign"].get(k) in cfg["profiles"]:
                name = cfg["assign"][k]
                break
    if not name:
        name = cfg["default"] if cfg["default"] in cfg["profiles"] else "full"
    return name, settings(name, cfg) or dict(FULL)


def settings_of(route) -> dict:
    """The profile settings a route carries (FULL when it carries none: tests, old sessions, no router)."""
    s = getattr(route, "settings", None) or {}
    return {**FULL, **{k: v for k, v in s.items() if k in FULL}}


def _env(var: str) -> str | None:
    v = os.environ.get(var)
    return v.strip() if v is not None and v.strip() != "" else None


def effective(route) -> dict:
    """The route's settings with the explicit env flags applied on top (env wins; same meaning as before profiles)."""
    s = settings_of(route)
    if (v := _env("MH_PLAN")) is not None:
        s["plan"] = _plan(v)
    for var, key in (("MH_REVIEW", "review"), ("MH_MUTATION", "mutation"), ("MH_LEAN_TOOLS", "lean_tools")):
        if (v := _env(var)) is not None:
            s[key] = v != "0"
    if (v := _env("MH_COMPACT_CHARS")) is not None:
        try:
            s["compact_chars"] = int(v)
        except ValueError:
            pass
    return s


# ---------- spec-text sets ("methods") ----------------------------------------------------------------------------
def methods_specs(name: str | None) -> dict:
    """{spec key: {"addon": ..}} for a methods set: <data folder>/methods/<name>.json, else app/defaults/methods/."""
    if not name or not re.fullmatch(r"[\w.+-]+", str(name)):
        return {}
    from meshpool_harness import config as C
    for base in (C.home() / "methods", C.APP / "defaults" / "methods"):
        d = _read(base / f"{name}.json")
        if d:
            return {k: v for k, v in (d.get("specs") or {}).items() if isinstance(v, dict)}
    return {}
