"""Teach by editing: learn from what the user changes in files Meshpool made, not only from what they say.

`detect_edits()` runs when the app or a chat opens. For each file in the artifact registry whose checksum changed it
diffs the structure snapshot against the baseline. A change counts only when it is consistent across the file (the
same case, font, format or removal on at least 3 places and at least 70% of the places it could apply to). A one-off
edit is noise and produces nothing. It reads registered files only, never any other file, and does nothing when
"Learn from my edits" is off, memory is off, or the chat is incognito.

A detection becomes a `teach` card shaped like an insights card (insights.py ranks and budgets it). Accepting it
writes `learned` facts (source type `edit`, with the file) and, for fonts, updates the active company look.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import statistics
import time
from pathlib import Path

from meshpool_harness import artifacts_registry as REG
from meshpool_harness import assistant as A

MIN_PLACES = 3                  # a rule needs this many changed places
MIN_SHARE = 0.7                 # and this share of the places it could have applied to
GRACE_S = 5                     # a file changed within this many seconds of its registration is Meshpool's own edit
EXPIRES_S = 7 * 86400
GENERIC_SHAPES = ("placeholder", "title", "textbox", "text box", "rectangle", "picture", "group", "connector", "")


# ---------- the switch -------------------------------------------------------------------------------------------------
def _settings_file() -> Path:
    return A._dir() / "learn-edits.json"


def learn_from_edits_on() -> bool:
    """Settings > Your Meshpool > "Learn from my edits". On by default. Also off when memory is off."""
    if not A.settings()["memory_on"]:
        return False
    with contextlib.suppress(OSError, ValueError):
        if json.loads((A._dir() / "settings.json").read_text(encoding="utf-8")).get("learn_from_edits") is False:
            return False
    try:
        return bool(json.loads(_settings_file().read_text(encoding="utf-8")).get("learn_from_edits", True))
    except (OSError, ValueError):
        return True


def set_learn_from_edits(on: bool) -> bool:
    A._write(_settings_file(), json.dumps({"learn_from_edits": bool(on)}))
    return bool(on)


# ---------- diffing ------------------------------------------------------------------------------------------------------
def _ok(changed: int, candidates: int) -> bool:
    return changed >= MIN_PLACES and candidates > 0 and changed / candidates >= MIN_SHARE


def _rule(kind: str, scope: str, value, count: int, of: int, fact: str, phrase: str, noun: str = "") -> dict:
    return {"kind": kind, "scope": scope, "value": value, "count": count, "of": of, "fact": fact,
            "phrase": phrase, "noun": noun}


def _all(count: int, of: int, noun: str) -> str:
    return f"all {count} {noun}" if count == of else f"{count} of {of} {noun}"


def _case_rules(pairs: list[tuple[str, str]], scope: str, noun: str, fact_noun: str) -> list[dict]:
    """pairs: (old case, new case) of the same title or heading. One rule per target case."""
    out = []
    for target in ("sentence", "title", "upper", "lower"):
        cand = [(o, n) for o, n in pairs if o and n and o != target]
        changed = [(o, n) for o, n in cand if n == target]
        if _ok(len(changed), len(cand)):
            word = {"sentence": "sentence case", "title": "title case", "upper": "capitals", "lower": "lower case"}[target]
            out.append(_rule("case", scope, target, len(changed), len(cand),
                             f"Prefers {fact_noun} in {word}", f"{_all(len(changed), len(cand), noun)} to {word}", noun))
    return out


def _font_rules(old: dict, new: dict, old_runs: int, new_runs: int, scope: str) -> list[dict]:
    """The fonts the file uses, as {font: runs}. A font that now covers at least 70% of the text and covered under
    30% of it before."""
    out = []
    if new_runs < MIN_PLACES:
        return out
    for f, n in sorted(new.items(), key=lambda kv: -kv[1]):
        now_share = n / new_runs
        before = old.get(f, 0) / old_runs if old_runs else 0
        if now_share >= MIN_SHARE and before < 0.3 and n >= MIN_PLACES:
            out.append(_rule("font", scope, f, n, new_runs, f"Prefers {f} as the font", f"the font to {f}"))
            break
    return out


def pptx_rules(old: dict, new: dict) -> list[dict]:
    a, b = old["slides"], new["slides"]
    n = min(len(a), len(b))
    pairs = list(zip(a[:n], b[:n]))
    rules = _case_rules([(x["case"], y["case"]) for x, y in pairs], "deck", "slide titles", "slide titles")
    # fonts and colours: slide by slide
    rules += _font_rules(_sum(x["fonts"] for x in a[:n]), _sum(y["fonts"] for y in b[:n]),
                         sum(x.get("runs", 0) for x in a[:n]), sum(y.get("runs", 0) for y in b[:n]), "deck")
    with_text = [(x, y) for x, y in pairs if y.get("runs")]
    for color in sorted({c for _, y in with_text for c in y["colors"]}):
        changed = [1 for x, y in with_text if y["colors"].get(color, 0) >= 0.5 * max(1, sum(y["colors"].values()))
                   and x["colors"].get(color, 0) == 0]
        if _ok(len(changed), len(with_text)):
            rules.append(_rule("color", "deck", color, len(changed), len(with_text),
                               f"Prefers #{color} for slide text", f"the text colour to #{color}"))
            break
    short = [(x["words"], y["words"]) for x, y in pairs if x["words"] and y["words"]]
    shorter = [(o, nw) for o, nw in short if nw < o]
    if _ok(len(shorter), len(short)) and statistics.mean(o for o, _ in shorter) * 0.8 >= statistics.mean(nw for _, nw in shorter):
        cap = max(nw for _, nw in shorter)
        rules.append(_rule("length", "deck", cap, len(shorter), len(short),
                           f"Wants slide titles of {cap} words or fewer", f"slide titles to {cap} words or fewer"))
    names = {nm for x, _ in pairs for nm in x["shapes"] if not any(g in nm for g in GENERIC_SHAPES if g)}
    for nm in sorted(names):
        cand = [(x, y) for x, y in pairs if nm in x["shapes"]]
        gone = [1 for x, y in cand if nm not in y["shapes"]]
        if _ok(len(gone), len(cand)):
            rules.append(_rule("removed", "deck", nm, len(gone), len(cand),
                               f"Never wants a {nm} element on slides", f"the {nm} element off the slides"))
    return rules


def _sum(counters) -> dict:
    out: dict = {}
    for c in counters:
        for k, v in c.items():
            out[k] = out.get(k, 0) + v
    return out


def xlsx_rules(old: dict, new: dict, path: str = "") -> list[dict]:
    rules = []
    common = [s for s in new["sheets"] if s in old["sheets"]]
    fo = _sum(old["sheets"][s]["fonts"] for s in common)
    fn = _sum(new["sheets"][s]["fonts"] for s in common)
    rules += _font_rules(fo, fn, sum(fo.values()), sum(fn.values()), "workbook")
    pair_counts: dict = {}
    from_counts: dict = {}
    for s in common:
        o, nw = old["sheets"][s]["formats"], new["sheets"][s]["formats"]
        for coord, of_ in o.items():
            from_counts[of_] = from_counts.get(of_, 0) + 1
            if coord in nw and nw[coord] != of_:
                pair_counts[(of_, nw[coord])] = pair_counts.get((of_, nw[coord]), 0) + 1
    for (a, b), c in sorted(pair_counts.items(), key=lambda kv: -kv[1]):
        if _ok(c, from_counts.get(a, 0)) and c >= 5:
            rules.append(_rule("format", "workbook", b, c, from_counts[a], f"Prefers the number format {b}",
                               f"number format to {b}"))
            break
    wchanged, wtotal, wnew = 0, 0, []
    for s in common:
        for col, w in old["sheets"][s]["widths"].items():
            nw = new["sheets"][s]["widths"].get(col)
            if nw is None:
                continue
            wtotal += 1
            if abs(nw - w) >= 1:
                wchanged += 1
                wnew.append(nw)
    if _ok(wchanged, wtotal):
        w = round(statistics.median(wnew))
        rules.append(_rule("width", "workbook", w, wchanged, wtotal, f"Prefers columns about {w} wide",
                           f"the column widths to about {w}"))
    added = [s for s in new["sheets"] if s not in old["sheets"] and not s.lower().startswith("sheet")]
    for s in added:                                    # one workbook adding a sheet is a one-off; the same name twice is a habit
        seen = _state_add("added_sheets", s.lower(), path)
        if len(seen) >= 2:
            rules.append(_rule("sheet", "workbook", s, len(seen), len(seen), f"Adds a {s} sheet to workbooks",
                               f"a {s} sheet to {len(seen)} workbooks"))
    return rules


def docx_rules(old: dict, new: dict) -> list[dict]:
    a, b = old["headings"], new["headings"]
    n = min(len(a), len(b))
    rules = _case_rules([(x["case"], y["case"]) for x, y in zip(a[:n], b[:n])], "document", "headings", "headings")
    fo, fn = dict(old["body_fonts"]), dict(new["body_fonts"])
    for h in a:
        fo = _sum([fo, h["fonts"]])
    for h in b:
        fn = _sum([fn, h["fonts"]])
    rules += _font_rules(fo, fn, old.get("runs", 0), new.get("runs", 0), "document")
    return rules


def diff(old: dict, new: dict, path: str = "") -> list[dict]:
    if old.get("kind") != new.get("kind"):
        return []
    return {"pptx": lambda: pptx_rules(old, new), "xlsx": lambda: xlsx_rules(old, new, path),
            "docx": lambda: docx_rules(old, new)}[old["kind"]]()


# ---------- state (cross-file habits) ----------------------------------------------------------------------------------
def _state_file() -> Path:
    return REG.path().parent / "teach.json"


def _state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _state_add(group: str, key: str, value: str) -> list[str]:
    st = _state()
    vals = st.setdefault(group, {}).setdefault(key, [])
    if value and value not in vals:
        vals.append(value)
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(st), encoding="utf-8")
    return vals


# ---------- detection and cards ----------------------------------------------------------------------------------------
def detect_edits(incognito: bool = False, only: list[str] | None = None, grace: float = GRACE_S) -> list[dict]:
    """Teach cards for registered files the user changed. Never reads an unregistered file."""
    if incognito or os_off() or not learn_from_edits_on():
        return []
    cards = []
    for path, row in REG.baselines().items():
        if only is not None and path not in only:
            continue
        p = Path(path)
        try:
            if not p.is_file() or p.stat().st_mtime < row["ts"] + grace:
                continue
            if REG.checksum(p) == row["checksum"]:
                continue
            new = REG.snapshot(p)
        except Exception:  # noqa: BLE001 - an unreadable file is skipped, never an error
            continue
        rules = diff(row["snapshot"], new, path)
        if rules:
            cards.append(teach_card(path, row, rules, p.stat().st_mtime))
    return cards


def detect() -> list[dict]:
    """The shape insights.detect_teach reads: one candidate per edited file, rules as phrases plus the raw rules that
    accept() writes. Registered files only."""
    out = []
    for c in detect_edits():
        pl = c["action"]["payload"]
        raw = pl["rules"]
        out.append({"file": pl["file"], "chat_id": pl.get("chat_id", ""), "kind": Path(pl["file"]).suffix.lstrip("."),
                    "count": min(r["count"] for r in raw), "total": max(r["of"] for r in raw),
                    "rules": [r["phrase"] for r in raw], "rules_raw": raw, "label": c["title"]})
    return out


def os_off() -> bool:
    import os
    return os.environ.get("MH_INSIGHTS", "1") == "0"


def teach_card(path: str, row: dict, rules: list[dict], mtime: float | None = None) -> dict:
    now = time.time()
    name = Path(path).name
    phrase = _join([r["phrase"] for r in rules])
    cid = "teach_" + hashlib.sha1((path + row["checksum"]).encode()).hexdigest()[:10]
    ev = [{"text": f"{r['phrase'][0].upper() + r['phrase'][1:]} in {name}", "file": path,
           "chat_id": row.get("chat_id") or None, "ts": mtime or now} for r in rules]
    conf = min(r["count"] / max(r["of"], 1) for r in rules)
    return {"id": cid, "type": "teach", "title": f"You changed {phrase}. Make these your defaults?",
            "body": f"Meshpool made {name} and you edited it. Each change is consistent across the file: "
                    + "; ".join(r["fact"].lower() for r in rules) + ".",
            "evidence": ev, "confidence": round(conf, 2), "value": 0.8,
            "action": {"kind": "teach_accept", "label": "Make these my defaults",
                       "payload": {"rules": rules, "file": path, "chat_id": row.get("chat_id") or ""}},
            "score": round(conf * 0.8, 3), "created": now, "expires": now + EXPIRES_S,
            "dedupe_key": f"teach:{path}", "where": "home"}


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# ---------- accepting and dismissing -----------------------------------------------------------------------------------
def accept(rules: list[dict], file: str, chat_id: str = "", incognito: bool = False) -> dict:
    """Make the detected rules the user's defaults: `learned` facts (source: the edit), and the company look's fonts
    where a font rule applies to a deck. Returns {facts: [ids], company: bool}."""
    out = {"facts": [], "company": False}
    if incognito or not learn_from_edits_on():
        return dict(out, skipped="Learning from edits is off.")
    from meshpool_harness import memstore as MS
    for r in rules or []:
        text = str(r.get("fact") or "").strip()
        if not text:
            continue
        with contextlib.suppress(ValueError):
            f, _ = MS.add(text, kind="learned", category="preferences",
                          source={"type": "edit", "file": file, "chat_id": chat_id or None, "ts": time.time()},
                          by="meshpool")
            out["facts"].append(f["id"])
    for r in rules or []:
        if r.get("kind") == "font" and r.get("scope") == "deck" and r.get("value"):
            out["company"] = _company_font(str(r["value"])) or out["company"]
    rebase(file, chat_id)
    return out


def _company_font(font: str) -> bool:
    """Set the heading and body font of the active company look, if there is one. Conservative: never creates a
    profile, never touches a look built on a company master template."""
    try:
        from engines.forge import brand as B
        prof = B.active()
        if not prof or prof.get("template_mode") == "master":
            return False
        prof = dict(prof)
        prof["fonts"] = {"major": font, "minor": font}
        B.save(prof)
        return True
    except Exception:  # noqa: BLE001
        return False


def rebase(file: str, chat_id: str = "") -> None:
    """The file as it is now is the new baseline (after Yes or No), so the same edit is not offered again."""
    with contextlib.suppress(Exception):
        row = REG.get(file)
        REG.register(file, chat_id or (row or {}).get("chat_id", ""))


def dismiss(file: str) -> None:
    rebase(file)
