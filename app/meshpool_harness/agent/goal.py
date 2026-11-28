"""Goal mode: keep working until an independent check says the bar is met.

The user sets a goal and a bar ("keep going until it is a 10/10") and walks away. meshpool keeps taking turns until
a separate check says the bar is met, a budget runs out, it is truly stuck, or it needs the user. Then it reports
honestly. It works the same in Code mode and in Chat mode.

This module holds the contract and the pure parts:

- the contract: the goal text, the bar, the mode (code or chat) and the budgets;
- the rubric: 4 to 12 criteria, each with how to check it, a weight and a must-have flag, frozen and hashed before
  turn 1, so the working agent can read it but never change it;
- the score: computed here, never by a model: 10 times the weighted share of criteria met. A criterion the check
  could not check counts as not met. Any unmet must-have keeps the score below the bar;
- the gates: the hard checks run before the grader. In Code mode the test, build and lint commands the project
  has, judged by their exit codes. In Chat mode the office checks (decks, workbooks, documents) and page renders;
- the state in sessions/<id>/goal.json, written atomically. Counters never reset on resume;
- decide(): continue, met, paused or stopped, from the state and how the last turn and check went.

The session (ui/agent_api.py) runs the turns and the checks; agent/grader.py drafts the rubric and scores the work.

Budgets and their settings (all in DEFAULTS, read by budgets()): MH_GOAL_BAR (10), MH_GOAL_USD (else MH_BUDGET_USD,
0 means no money limit), MH_GOAL_HOURS (4), MH_GOAL_TURNS (30), MH_GOAL_CHECKS (10), MH_GOAL_CHECK_EVERY (5),
MH_GOAL_GATE_SECONDS (300). Every budget is enforced in code: nothing is left for a model to judge.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

VERSION = 1
MIN_CRITERIA = 4
MAX_CRITERIA = 12
# The one place the defaults live. Settings override each (see budgets()).
DEFAULTS = {
    "bar": 10.0,            # the score the work must reach, 1 to 10
    "usd": 0.0,             # money for the whole goal; 0 = none (the task limit MH_BUDGET_USD is the default)
    "hours": 4.0,           # working time for the whole goal (time while a turn or a check runs)
    "turns": 30,            # goal turns
    "checks": 10,           # independent checks
    "check_every": 5,       # a check every this many goal turns, even when the agent does not say it is ready
    "converge_at": 0.8,     # share of any budget at which the agent is told to converge
    "freeze_s": 60,         # the rubric freezes on its own after this many seconds
    "resume_s": 30,         # after a restart, an active goal resumes by itself after this countdown
    "stuck_checks": 3,      # no score rise across this many checks with no file change: stuck
    "stuck_blocker": 3,     # the same blocker this many turns running: stuck
    "idle_turns": 2,        # this many turns in a row that did nothing: stuck
    "gate_seconds": 300,    # time limit of one gate command
}
_SETTING = {"bar": "MH_GOAL_BAR", "usd": "MH_GOAL_USD", "hours": "MH_GOAL_HOURS", "turns": "MH_GOAL_TURNS",
            "checks": "MH_GOAL_CHECKS", "check_every": "MH_GOAL_CHECK_EVERY", "gate_seconds": "MH_GOAL_GATE_SECONDS"}
_LIMITS = {"bar": (1, 10), "usd": (0, 100000), "hours": (0, 720), "turns": (1, 1000), "checks": (1, 200),
           "check_every": (1, 100), "gate_seconds": (10, 7200)}

ACTIVE = ("drafting", "review", "working", "checking")      # the goal is running or about to
OPEN = ACTIVE + ("paused",)                                 # the goal still holds the chat
DONE = ("met", "stopped", "cleared")

READY, CONTINUE, NEEDS, BLOCKED = "READY", "CONTINUE", "NEEDS", "BLOCKED"
_LINE = re.compile(r"^\W*GOAL:\s*(READY|DONE|CONTINUE|NEEDS\s+YOU|BLOCKED)\b[:\s-]*(.*)$", re.I | re.M)


# ---------- settings ------------------------------------------------------------------------------------------------
def _setting(key: str) -> str:
    v = os.environ.get(key)
    if v in (None, ""):
        try:
            from meshpool_harness import config as C
            v = C.read().get(key)
        except Exception:  # noqa: BLE001
            v = None
    return "" if v in (None, "") else str(v)


def _clamp(name: str, value) -> float:
    lo, hi = _LIMITS[name]
    return max(lo, min(hi, float(value)))


def budgets(**over) -> dict:
    """The budgets for a new goal: DEFAULTS, then the settings, then `over` (a value of None is ignored)."""
    out = dict(DEFAULTS)
    for k, env in _SETTING.items():
        raw = _setting(env)
        if k == "usd" and not raw:
            raw = _setting("MH_BUDGET_USD")        # the per-task money limit is the goal's limit unless set apart
        if raw:
            try:
                out[k] = _clamp(k, raw)
            except (TypeError, ValueError):
                pass
    for k, v in over.items():
        if v is not None and k in _LIMITS:
            try:
                out[k] = _clamp(k, v)
            except (TypeError, ValueError):
                pass
    for k in ("turns", "checks", "check_every", "gate_seconds", "freeze_s", "resume_s", "stuck_checks",
              "stuck_blocker", "idle_turns"):
        out[k] = int(out[k])
    return out


# ---------- the state file -------------------------------------------------------------------------------------------
def path(sdir) -> Path:
    return Path(sdir) / "goal.json"


def new(goal: str, mode: str = "code", bar=None, now: float | None = None, **over) -> dict:
    """A new contract. Its rubric is drafted and frozen before turn 1 (status drafting, then review, then working)."""
    b = budgets(bar=bar, **over)
    now = time.time() if now is None else now
    return {"v": VERSION, "goal": str(goal or "").strip()[:2000], "mode": "chat" if mode == "chat" else "code",
            "bar": b.pop("bar"), "budgets": b, "status": "drafting", "reason": "", "question": "",
            "rubric": [], "rubric_hash": "", "frozen_at": 0.0, "draft_until": 0.0, "created": now, "updated": now,
            "started": 0.0, "ended": 0.0, "turns": 0, "checks": 0, "spent": 0.0, "spent_base": 0.0, "elapsed": 0.0,
            "idle": 0, "blocker": "", "blocker_n": 0, "changed": False, "last_check_turn": 0, "converged": False,
            "history": [], "findings": {}, "resume_at": 0.0, "report": ""}


def save(sdir, st: dict) -> bool:
    """Write goal.json atomically: a temporary file next to it, then a replace. False when the folder is missing."""
    d = Path(sdir)
    if not d.is_dir():
        return False
    st["updated"] = time.time()
    data = json.dumps(st, ensure_ascii=False, indent=1)
    tmp = d / f".goal.json.{os.getpid()}.{int(time.time() * 1000)}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path(d))
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def load(sdir) -> dict | None:
    """The saved state, or None. A frozen rubric whose hash no longer matches (the file was changed outside meshpool)
    is marked: the goal pauses instead of scoring against a rubric nobody agreed to."""
    try:
        st = json.loads(path(sdir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict) or not st.get("goal") or not isinstance(st["goal"], str):
        return None
    try:
        base = new(st["goal"], st.get("mode", "code"))
        for k, v in base.items():
            st.setdefault(k, v)
        # a damaged file (hand edited, cut short by a full disk) is never trusted: wrong types mean no goal, not a crash
        if not isinstance(st["budgets"], dict) or not isinstance(st["history"], list) \
                or not isinstance(st["findings"], dict) or not isinstance(st["rubric"], list) \
                or not all(isinstance(c, dict) and isinstance(c.get("id"), str) and isinstance(c.get("text"), str)
                           for c in st["rubric"]) or not all(isinstance(h, dict) for h in st["history"]):
            return None
        for k, v in base["budgets"].items():
            st["budgets"].setdefault(k, v)
        for k in ("turns", "checks"):
            st[k] = int(st[k])
        for k in ("bar", "spent", "elapsed", "resume_at", "draft_until", "frozen_at", "started", "created"):
            st[k] = float(st[k] or 0)
        if st.get("rubric_hash"):
            kept = _FROZEN.get(_key(sdir))
            if contract_hash(st) != st["rubric_hash"] or (kept and kept != st["rubric_hash"]):
                st["tampered"] = True
            elif st.get("frozen_at") and not kept:
                _FROZEN[_key(sdir)] = st["rubric_hash"]      # the first load after a restart (see _FROZEN)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    if st.get("tampered") and st.get("status") in ACTIVE:
        # never one more turn or check against a rubric nobody agreed to: paused, and the card says why
        st.update(status="paused", reason="the rubric was changed outside meshpool", resume_at=0.0)
    return st


# ---------- the rubric ----------------------------------------------------------------------------------------------
def _crit(text: str, check: str, weight: float = 1, must: bool = False, needs_user: bool = False) -> dict:
    return {"text": text, "check": check, "weight": weight, "must": must, "needs_user": needs_user}


def fallback_rubric(goal: str, mode: str = "code") -> list[dict]:
    """Criteria that hold for any goal, used when no model could draft one or to fill a short draft."""
    g = re.sub(r"\s+", " ", str(goal or "")).strip()[:300] or "the goal"
    if mode == "chat":
        rows = [_crit(f"The deliverable does what was asked: {g}", "Open the file or read the reply and compare it "
                      "with the request point by point.", 3, True),
                _crit("Every file opens cleanly and passes the office checks", "check_deck, check_workbook or "
                      "check_document on each file made.", 2, True),
                _crit("Facts and numbers are right and sourced where it matters", "Recompute key numbers and open "
                      "the cited sources.", 2),
                _crit("No placeholder text, no invented facts, nothing left half done", "Read the whole deliverable.",
                      1)]
    else:
        rows = [_crit(f"The change does what was asked: {g}", "Read the changed files and run the feature or the "
                      "tests that cover it.", 3, True),
                _crit("The project's tests, build and lint pass", "The exit codes of the gate commands.", 2, True),
                _crit("New logic that can break has tests", "Look for tests that cover the change.", 2),
                _crit("No dead code, placeholder or debug leftovers", "Read the diff.", 1)]
    return rows


def normalise_rubric(raw, goal: str = "", mode: str = "code") -> list[dict]:
    """Clean criteria from a model's draft or the user's edits: 4 to 12 of them, each with an id (c1, c2, ...), text,
    how to check it, a weight from 1 to 5 and a must-have flag. A draft with fewer than 4 is filled from the
    fallback criteria."""
    if isinstance(raw, dict):
        raw = raw.get("criteria") or raw.get("rubric") or []
    rows, seen = [], set()
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, str):
            c = {"text": c}
        if not isinstance(c, dict):
            continue
        text = re.sub(r"\s+", " ", str(c.get("text") or c.get("criterion") or c.get("requirement") or "")).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        try:
            w = float(c.get("weight", 1) or 1)
        except (TypeError, ValueError):
            w = 1.0
        w = max(1.0, min(5.0, round(w)))
        must = c.get("must", c.get("must_have", False))
        rows.append({"text": text[:300], "check": re.sub(r"\s+", " ", str(c.get("check") or c.get("how") or ""))
                     .strip()[:300], "weight": int(w), "must": must is True or str(must).lower() in ("true", "yes", "1"),
                     "needs_user": bool(c.get("needs_user"))})
        if len(rows) >= MAX_CRITERIA:
            break
    if len(rows) < MIN_CRITERIA:
        for c in fallback_rubric(goal, mode):
            if len(rows) >= MIN_CRITERIA:
                break
            if c["text"].lower() not in seen:
                rows.append(dict(c, weight=int(c["weight"])))
                seen.add(c["text"].lower())
    for i, c in enumerate(rows, 1):
        c["id"] = f"c{i}"
    return rows


CONTRACT_BUDGETS = ("usd", "hours", "turns", "checks")      # the budgets the frozen contract holds to


def rubric_hash(rubric: list, bar=None, budgets: dict | None = None) -> str:
    """The frozen contract's fingerprint: the same criteria (and, when given, the same bar and budgets) always give
    the same hash, any change gives another."""
    canon: list = [[str(c.get("id", "")), str(c.get("text", "")), str(c.get("check", "")), int(c.get("weight", 1) or 1),
                    bool(c.get("must")), bool(c.get("needs_user"))] for c in rubric or []]
    if bar is not None:
        canon.append(["bar", float(bar)])
    if budgets is not None:
        canon.append(["budgets", [float((budgets or {}).get(k) or 0) for k in CONTRACT_BUDGETS]])
    return hashlib.sha256(json.dumps(canon, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def contract_hash(st: dict) -> str:
    """The hash freeze() stores: the rubric, the bar and the budgets together."""
    return rubric_hash(st.get("rubric") or [], st.get("bar", DEFAULTS["bar"]), st.get("budgets") or {})


# The hash of each frozen goal, kept in this process when its rubric freezes (and when a budget is raised on Resume),
# by goal.json's folder. A goal.json rewritten from outside with a matching hash (an agent that can write files can
# compute a sha256) still differs from this one, so load() and disk_changed() catch it. The limit: after a restart this
# process has no value yet, and the first load() takes the one in the file. A file rewritten while meshpool was not
# running is caught only if its hash no longer matches its own content.
_FROZEN: dict = {}


def _key(sdir) -> str:
    try:
        return str(Path(sdir).resolve())
    except OSError:
        return str(sdir)


def remember(sdir, st: dict) -> None:
    """Keep the frozen goal's hash in this process (see _FROZEN)."""
    if st.get("frozen_at") and st.get("rubric_hash"):
        _FROZEN[_key(sdir)] = st["rubric_hash"]


def forget(sdir) -> None:
    _FROZEN.pop(_key(sdir), None)


def disk_changed(sdir) -> bool:
    """Was the frozen goal's file changed outside meshpool since this process froze or first read it? Its content
    must match its hash, and its hash the one kept here. A missing or unreadable file is not a change."""
    want = _FROZEN.get(_key(sdir))
    if not want:
        return False
    try:
        raw = json.loads(path(sdir).read_text(encoding="utf-8"))
        return raw.get("rubric_hash") != want or contract_hash(raw) != want
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def review(st: dict, rubric, freeze_s: float | None = None, now: float | None = None) -> dict:
    """The drafted rubric goes to the user, who can edit it until it freezes (freeze_s, 60 s by default)."""
    now = time.time() if now is None else now
    st["rubric"] = normalise_rubric(rubric, st["goal"], st["mode"])
    st["status"] = "review"
    st["draft_until"] = now + float(st["budgets"].get("freeze_s", DEFAULTS["freeze_s"]) if freeze_s is None
                                    else freeze_s)
    return st


def edit(st: dict, rubric) -> dict:
    """The user's edits while the rubric is open. Refused once it is frozen."""
    if st.get("frozen_at"):
        raise ValueError("The rubric is frozen. Clear the goal and set a new one to change it.")
    st["rubric"] = normalise_rubric(rubric, st["goal"], st["mode"])
    return st


def freeze(st: dict, rubric=None, now: float | None = None) -> dict:
    """Freeze the rubric (the user pressed start, or the countdown ran out): hash it, store it, start working."""
    if st.get("frozen_at"):
        return st
    now = time.time() if now is None else now
    if rubric is not None:
        st["rubric"] = normalise_rubric(rubric, st["goal"], st["mode"])
    elif not st.get("rubric"):
        st["rubric"] = normalise_rubric([], st["goal"], st["mode"])
    st["rubric_hash"] = contract_hash(st)
    st["frozen_at"] = now
    st["started"] = st.get("started") or now
    st["status"], st["reason"], st["question"] = "working", "", ""
    return st


# ---------- the score -----------------------------------------------------------------------------------------------
STATUS = ("met", "not_met", "unverified")


def score(rubric: list, statuses: dict, bar: float = 10.0) -> dict:
    """10 times the weighted share of criteria met, from the check's status per criterion id. A criterion with no
    status, or one the check could not check, is not met. An unmet must-have keeps the score below the bar."""
    total = sum(max(1, int(c.get("weight", 1) or 1)) for c in rubric) or 1
    met_w, met, not_met, unv, must_open = 0, [], [], [], []
    for c in rubric:
        st = statuses.get(c["id"], "unverified")
        st = st if st in STATUS else "unverified"
        w = max(1, int(c.get("weight", 1) or 1))
        if st == "met":
            met_w += w
            met.append(c["id"])
        else:
            (not_met if st == "not_met" else unv).append(c["id"])
            if c.get("must"):
                must_open.append(c["id"])
    raw = round(10.0 * met_w / total, 1)
    val = raw
    if must_open and val >= bar:
        val = below(bar)
    return {"score": val, "raw": raw, "met": met, "not_met": not_met, "unverified": unv, "must_open": must_open}


def below(bar: float) -> float:
    """The highest score that is still under the bar."""
    return round(max(0.0, float(bar) - (1.0 if float(bar) >= 1 else 0.1)), 1)


def fmt(x) -> str:
    """8.0 -> "8", 8.5 -> "8.5"."""
    try:
        v = round(float(x), 1)
    except (TypeError, ValueError):
        return "?"
    return str(int(v)) if v == int(v) else f"{v:.1f}"


def met_bar(check: dict, bar: float) -> bool:
    """Met: the score reaches the bar and every gate passes."""
    return (not check.get("error") and float(check.get("score") or 0) >= float(bar) and bool(check.get("gates_ok", True))
            and not check.get("must_open"))


# ---------- the gates -----------------------------------------------------------------------------------------------
_PY_TESTS = ("pytest.ini", "tox.ini", "conftest.py")


def _python(root: Path) -> str:
    for rel in (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe", "venv/Scripts/python.exe"):
        if (root / rel).is_file():
            return str(root / rel)
    return shutil.which("python3") or shutil.which("python") or sys.executable


def find_code_gates(root) -> list[dict]:
    """The test, build and lint commands the project declares: package.json scripts, a Makefile's targets, Python test
    settings, Cargo and Go. Only what the project itself names; nothing is guessed or installed."""
    root = Path(root)
    gates: list[dict] = []

    def add(kind, cmd, src):
        if not any(g["cmd"] == cmd for g in gates):
            gates.append({"kind": kind, "cmd": cmd, "source": src})
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            scripts = (json.loads(pkg.read_text(encoding="utf-8")) or {}).get("scripts") or {}
        except (OSError, ValueError):
            scripts = {}
        runner = "pnpm" if (root / "pnpm-lock.yaml").is_file() else "yarn" if (root / "yarn.lock").is_file() else "npm"
        for kind in ("test", "build", "lint"):
            body = str(scripts.get(kind) or "")
            if body and "no test specified" not in body:
                add(kind, [runner, "test"] if kind == "test" and runner != "yarn" else [runner, "run", kind]
                    if runner != "yarn" else ["yarn", kind], "package.json")
    mk = root / "Makefile"
    if mk.is_file():
        try:
            text = mk.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for kind in ("test", "build", "lint"):
            if re.search(rf"^{kind}\s*:", text, re.M):
                add(kind, ["make", kind], "Makefile")
    py_cfg = any((root / f).is_file() for f in _PY_TESTS)
    for f in ("pyproject.toml", "setup.cfg"):
        try:
            if (root / f).is_file() and re.search(r"\[(tool:pytest|tool\.pytest[^\]]*)\]",
                                                    (root / f).read_text(encoding="utf-8", errors="replace")):
                py_cfg = True
        except OSError:
            pass
    tests_dir = any((root / d).is_dir() and any((root / d).glob("test_*.py")) for d in ("tests", "test"))
    if py_cfg or tests_dir or any(root.glob("test_*.py")):
        if not any(g["kind"] == "test" for g in gates):
            add("test", [_python(root), "-m", "pytest", "-q"], "python tests")
    if (root / "pyproject.toml").is_file() and re.search(r"\[tool\.ruff", _read(root / "pyproject.toml")):
        if shutil.which("ruff") and not any(g["kind"] == "lint" for g in gates):
            add("lint", ["ruff", "check", "."], "pyproject.toml")
    if (root / "Cargo.toml").is_file():
        add("test", ["cargo", "test"], "Cargo.toml")
    if (root / "go.mod").is_file():
        add("test", ["go", "test", "./..."], "go.mod")
        add("build", ["go", "build", "./..."], "go.mod")
    return gates


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run_code_gates(root, seconds: int | None = None, stop=None) -> list[dict]:
    """Run each gate in the project folder with its own time limit, judged by the exit code. The environment has no
    credentials (config.scrub_env). A gate whose program is missing is "not run", which does not count as a pass
    or a failure: the card says so."""
    root = Path(root)
    lim = int(seconds or DEFAULTS["gate_seconds"])
    try:
        from meshpool_harness import config as C
        env = C.scrub_env(dict(os.environ))
    except Exception:  # noqa: BLE001
        env = dict(os.environ)
    env.setdefault("CI", "1")
    out = []
    for g in find_code_gates(root):
        if stop and stop():
            break
        name = " ".join(Path(g["cmd"][0]).name if i == 0 else x for i, x in enumerate(g["cmd"]))
        row = {"kind": g["kind"], "name": name, "ok": None, "code": None, "tail": "", "why": ""}
        exe = g["cmd"][0] if os.path.isabs(g["cmd"][0]) else shutil.which(g["cmd"][0])
        if not exe:
            row["why"] = f"{g['cmd'][0]} is not installed"
            out.append(row)
            continue
        t0 = time.time()
        try:
            p = subprocess.run([exe, *g["cmd"][1:]], cwd=str(root), env=env, capture_output=True, text=True,
                               timeout=lim, stdin=subprocess.DEVNULL, errors="replace")
            row["code"], row["ok"] = p.returncode, p.returncode == 0
            row["tail"] = "\n".join(((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-6:])[-800:]
        except subprocess.TimeoutExpired:
            row["ok"], row["why"] = False, f"it ran longer than {lim} s"
        except OSError as e:
            row["why"] = f"it could not start: {str(e)[:80]}"
        row["secs"] = round(time.time() - t0, 1)
        out.append(row)
    return out


_OFFICE = (".pptx", ".xlsx", ".xlsm", ".docx")


def office_files(root, since: float, limit: int = 12) -> list[str]:
    """The office files in the project changed since the goal started, newest first."""
    root = Path(root)
    found = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".") and d not in ("node_modules", "__pycache__", "views")]
        for fn in fns:
            if fn.lower().endswith(_OFFICE) and not fn.startswith("~$"):
                p = Path(dp) / fn
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                if m >= since - 1:
                    found.append((m, str(p.relative_to(root))))
    return [r for _, r in sorted(found, reverse=True)[:limit]]


def _engines_path():
    try:
        app = Path(__file__).resolve().parents[2]
        if str(app) not in sys.path:
            sys.path.insert(0, str(app))
    except Exception:  # noqa: BLE001
        pass


def _soffice() -> str | None:
    try:
        from meshpool_harness import office_runtime as OR
        return OR.find_soffice()
    except Exception:  # noqa: BLE001
        return None


def _renders(p: Path, seconds: int) -> tuple[bool | None, str]:
    """Does the file render to pages? Converted to PDF in a temporary folder with LibreOffice. None: not run."""
    exe = _soffice()
    if not exe:
        return None, "LibreOffice is not installed, so the pages were not rendered"
    import tempfile
    with tempfile.TemporaryDirectory(prefix="mh-goal-render-") as td:
        try:
            subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", td, str(p)], capture_output=True,
                           timeout=seconds, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, f"the render could not run: {str(e)[:80]}"
        pdf = Path(td) / (p.stem + ".pdf")
        if not pdf.is_file() or pdf.stat().st_size < 200:
            return False, "it did not render to any page"
        return True, "renders to pages"


def chat_gates(root, files: list[str], seconds: int | None = None, render: bool = True) -> list[dict]:
    """The office checks on each office file the goal made or changed: the file opens cleanly (check_document), the
    deck and workbook rules a rebuild can fix (check_deck, check_workbook), and its pages render."""
    _engines_path()
    root = Path(root)
    lim = int(seconds or DEFAULTS["gate_seconds"])
    out = []
    for rel in files:
        p = root / rel
        low = p.suffix.lower()
        try:
            from engines.forge.ooxml_check import check
            r = check(str(p))
            out.append({"kind": "opens", "name": f"check_document {rel}", "ok": bool(r.get("ok")), "code": None,
                        "tail": "; ".join(f"{x.get('check')}: {x.get('detail')}" for x in (r.get("problems") or [])[:3])
                        [:400], "why": ""})
        except Exception as e:  # noqa: BLE001
            out.append({"kind": "opens", "name": f"check_document {rel}", "ok": None, "code": None, "tail": "",
                        "why": f"the check could not run: {str(e)[:80]}"})
        try:
            if low == ".pptx":
                from engines.forge.validate_pptx import validate
                res = validate(str(p), render=False)
                name = f"check_deck {rel}"
            elif low in (".xlsx", ".xlsm"):
                from engines.forge.validate_xlsx import validate
                res = validate(str(p))
                name = f"check_workbook {rel}"
            else:
                res, name = None, ""
            if res is not None:
                bad = [f for f in res.get("failures") or [] if f.get("status") == "fail"
                       and f.get("fix_scope", "content") == "content"]
                out.append({"kind": "rules", "name": name, "ok": not bad, "code": None,
                            "tail": "; ".join(f"{f.get('rule')}: {str(f.get('message') or f.get('detail') or '')[:90]}"
                                              for f in bad[:3]), "why": ""})
        except Exception as e:  # noqa: BLE001
            out.append({"kind": "rules", "name": f"rules {rel}", "ok": None, "code": None, "tail": "",
                        "why": f"the check could not run: {str(e)[:80]}"})
        if render:
            ok, why = _renders(p, min(lim, 240))
            out.append({"kind": "render", "name": f"page render {rel}", "ok": ok, "code": None, "tail": "",
                        "why": "" if ok else why})
    return out


def gates_ok(gates: list[dict]) -> bool:
    """Every gate that ran passed (a gate that could not run is listed apart, never counted as a pass)."""
    return all(g.get("ok") is not False for g in gates or [])


# ---------- counters and decisions ------------------------------------------------------------------------------------
def parse_line(reply: str) -> tuple[str, str]:
    """The agent's last GOAL line: (READY | CONTINUE | NEEDS | BLOCKED | "", the words after it). The old
    "GOAL: DONE" is read as READY: the agent's own word never ends a goal, a check does."""
    found = _LINE.findall(str(reply or ""))
    if not found:
        return "", ""
    word, rest = found[-1]
    word = re.sub(r"\s+", " ", word.upper())
    word = READY if word in ("READY", "DONE") else NEEDS if word.startswith("NEEDS") else word
    return word, rest.strip()[:400]


def strip_line(reply: str) -> str:
    return _LINE.sub("", str(reply or "")).rstrip()


def _sig(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\d+", "#", str(text or "").lower())).strip()[:160]


def record_turn(st: dict, out: dict, secs: float = 0.0, spent: float | None = None) -> dict:
    """Count one goal turn. out: acted, blocker, changed (see decide). Counters only ever go up."""
    st["turns"] = int(st.get("turns", 0)) + 1
    st["elapsed"] = float(st.get("elapsed", 0.0)) + max(0.0, float(secs or 0))
    if spent is not None:
        st["spent"] = max(float(st.get("spent", 0.0)), float(spent))
    st["idle"] = 0 if out.get("acted") or out.get("ready") else int(st.get("idle", 0)) + 1
    b = _sig(out.get("blocker") or "")
    if b and b == st.get("blocker"):
        st["blocker_n"] = int(st.get("blocker_n", 0)) + 1
    else:
        st["blocker"], st["blocker_n"] = b, 1 if b else 0
    if out.get("changed"):
        st["changed"] = True
    return st


def record_check(st: dict, check: dict, secs: float = 0.0, spent: float | None = None, why: str = "") -> dict:
    """Count one check and keep its score and findings. The history keeps every score, for the card."""
    st["checks"] = int(st.get("checks", 0)) + 1
    st["elapsed"] = float(st.get("elapsed", 0.0)) + max(0.0, float(secs or 0))
    if spent is not None:
        st["spent"] = max(float(st.get("spent", 0.0)), float(spent))
    entry = {"n": st["checks"], "score": float(check.get("score") or 0), "turn": st.get("turns", 0),
             "changed": bool(st.get("changed")), "gates_ok": bool(check.get("gates_ok", True)),
             "error": str(check.get("error") or "")[:200], "why": why, "at": time.time()}
    st.setdefault("history", []).append(entry)
    st["history"] = st["history"][-60:]
    st["findings"] = {k: check.get(k) for k in ("score", "items", "gates", "gates_ok", "fix", "blockers", "error",
                                                 "needs_user", "must_open")}
    st["last_check_turn"] = st.get("turns", 0)
    st["changed"] = False
    return st


def used(st: dict) -> dict:
    """The share of each budget used, 0 to 1 (a budget of 0 has no limit and is left out)."""
    b = st.get("budgets") or {}
    out = {}
    if float(b.get("usd") or 0) > 0:
        out["usd"] = float(st.get("spent", 0.0)) / float(b["usd"])
    if float(b.get("hours") or 0) > 0:
        out["hours"] = float(st.get("elapsed", 0.0)) / (float(b["hours"]) * 3600)
    if int(b.get("turns") or 0) > 0:
        out["turns"] = int(st.get("turns", 0)) / int(b["turns"])
    if int(b.get("checks") or 0) > 0:
        out["checks"] = int(st.get("checks", 0)) / int(b["checks"])
    return out


_BUDGET_WORDS = {"usd": "the money limit", "hours": "the time limit", "turns": "the turn limit",
                 "checks": "the check limit"}


def exhausted(st: dict) -> str:
    """Which budget is used up ("usd", "hours", "turns", "checks"), or ""."""
    if st.get("budget_hit"):
        return st["budget_hit"]
    for k, v in used(st).items():
        if v >= 1.0 - 1e-9:
            return k
    return ""


def converge_due(st: dict) -> bool:
    """At 80 percent of any budget the agent gets one line telling it to converge (once per goal)."""
    if st.get("converged") or exhausted(st):
        return False
    share = float((st.get("budgets") or {}).get("converge_at", DEFAULTS["converge_at"]))
    return any(v >= share for v in used(st).values())


def converge_note(st: dict) -> str:
    u = used(st)
    k = max(u, key=u.get) if u else "turns"
    return (f"[meshpool] Goal budget is {int(round(u.get(k, 0) * 100))} percent used ({_BUDGET_WORDS[k]}). Converge: "
            "finish the most important open criteria now, check them, then end with GOAL: READY.")


def check_due(st: dict, out: dict) -> str:
    """Why a check runs now, or "": the agent said it is ready, every N goal turns, or a budget ran out."""
    if out.get("ready"):
        return "the agent said it is ready"
    every = int((st.get("budgets") or {}).get("check_every", DEFAULTS["check_every"]))
    if every and int(st.get("turns", 0)) - int(st.get("last_check_turn", 0)) >= every:
        return f"every {every} goal turns"
    if exhausted(st) and int(st.get("last_check_turn", -1)) != int(st.get("turns", 0)):
        return "the budget ran out"
    return ""


def flat(st: dict) -> bool:
    """Stuck on the scores: across the last N checks the score never rose and no file changed between them."""
    n = int((st.get("budgets") or {}).get("stuck_checks", DEFAULTS["stuck_checks"]))
    h = [x for x in st.get("history") or [] if not x.get("error")]
    if n < 2 or len(h) < n:
        return False
    last = h[-n:]
    return max(x["score"] for x in last[1:]) <= last[0]["score"] and not any(x.get("changed") for x in last[1:])


def decide(st: dict, out: dict, check: dict | None = None) -> dict:
    """What happens after a goal turn (and the check, when one ran). Pure: reads the state, never changes it.

    out: interrupted (the user pressed Stop), error ("" | "net" | "auth" | "quota" | "model" | "other" | "budget"),
    acted, ready, needs (a question only the user can answer), blocker, loop (the loop breaker tripped).
    Returns {"action": "continue" | "wait" | "met" | "paused" | "stopped", "reason", "question", "findings"}."""
    bar = float(st.get("bar") or DEFAULTS["bar"])
    if st.get("status") in DONE:
        return {"action": "stopped", "reason": st.get("reason") or "the goal ended", "question": "", "findings": {}}
    if st.get("tampered"):
        return {"action": "paused", "reason": "the rubric was changed outside meshpool", "question": "", "findings": {}}
    if out.get("interrupted"):
        return {"action": "paused", "reason": "you stopped it", "question": "", "findings": {}}
    if out.get("approval_pending"):              # a get_capability card nobody answered: wait for the user
        return {"action": "paused", "reason": "it is waiting for your approval",
                "question": f"Answer the card: {str(out['approval_pending'])[:300]}", "findings": {}}
    err = out.get("error") or ""
    if err == "auth":
        return {"action": "paused", "reason": "the model's key was refused", "question": "", "findings": {}}
    if err == "quota":
        return {"action": "paused", "reason": "the key's quota or credit is used up", "question": "", "findings": {}}
    if err == "model":
        return {"action": "paused", "reason": "the model is not available", "question": "", "findings": {}}
    if err == "net":
        hit = exhausted(st)                      # an outage is waited out, but never past a budget
        if hit:
            return {"action": "stopped", "reason": "budget", "budget": hit, "question": "", "findings": {}}
        return {"action": "wait", "reason": "the model connection failed", "question": "", "findings": {}}
    findings = (check if check is not None else st.get("findings")) or {}
    if check is not None and met_bar(check, bar):
        return {"action": "met", "reason": "", "question": "", "findings": findings}
    q = (out.get("needs") or "").strip()
    if not q and check is not None:
        q = str(check.get("needs_user") or "").strip()
    if not q and check is not None:              # a "needs you" criterion still open: only the user can close it
        open_ids = {i.get("id") for i in check.get("items") or [] if i.get("status") != "met"}
        c = next((c for c in st.get("rubric") or [] if c.get("needs_user") and c.get("id") in open_ids), None)
        if c:
            q = f"This needs you: {c['text']}. Can you provide it or decide it?"
    if q:
        return {"action": "paused", "reason": "it needs you", "question": q[:400], "findings": findings}
    hit = exhausted(st) or ("usd" if err == "budget" else "")
    if hit:
        return {"action": "stopped", "reason": "budget", "budget": hit, "question": "", "findings": findings}
    if out.get("loop"):
        return {"action": "stopped", "reason": "stuck: it kept repeating the same steps", "question": "",
                "findings": findings}
    b = st.get("budgets") or {}
    if st.get("blocker") and int(st.get("blocker_n", 0)) >= int(b.get("stuck_blocker", DEFAULTS["stuck_blocker"])):
        return {"action": "stopped", "reason": f"stuck: the same blocker {int(st['blocker_n'])} turns running",
                "question": "", "findings": findings}
    if int(st.get("idle", 0)) >= int(b.get("idle_turns", DEFAULTS["idle_turns"])):
        return {"action": "stopped", "reason": f"stuck: {int(st['idle'])} turns in a row without doing anything",
                "question": "", "findings": findings}
    if check is not None and flat(st):
        return {"action": "stopped", "reason": f"stuck: no score rise across {int(b.get('stuck_checks', 3))} checks "
                                               "and no file changed", "question": "", "findings": findings}
    if err == "other":
        return {"action": "paused", "reason": "an error stopped the turn", "question": "", "findings": findings}
    return {"action": "continue", "reason": "", "question": "", "findings": findings}


def error_kind(text: str) -> str:
    """What a turn's last error means for the goal: an outage to wait out, or a problem only the user can fix."""
    t = str(text or "")
    if not t:
        return ""
    if re.search(r"rejected the API key|stopped accepting the API key|invalid api key|unauthori[sz]ed|HTTP 401|"
                 r"key (?:is )?masked|masked\b", t, re.I):
        return "auth"
    if re.search(r"quota|budget or quota|insufficient (?:credit|funds|balance)|billing|HTTP 402", t, re.I):
        return "quota"
    if re.search(r"does not offer a model|model (?:was )?not found|no such model|unknown model|model_not_found|"
                 r"pick another model", t, re.I):
        return "model"
    if re.search(r"spending limit", t, re.I):
        return "budget"
    if re.search(r"Couldn't reach the model|model service had a problem|model is busy|HTTP 0|HTTP 5\d\d|HTTP 429|"
                 r"rate.?limit|too many requests|(?:code|status)\W{0,3}429\b|overloaded|keeps failing", t, re.I):
        return "net"
    return "other"


def apply(st: dict, d: dict, now: float | None = None) -> dict:
    """Put a decision into the state."""
    now = time.time() if now is None else now
    act = d.get("action")
    if act == "met":
        st["status"], st["reason"], st["question"], st["ended"] = "met", "", "", now
    elif act == "stopped":
        st["status"], st["question"], st["ended"] = "stopped", "", now
        st["reason"] = d.get("reason") or "stopped"
        if d.get("budget"):
            st["budget_hit"] = d["budget"]
    elif act == "paused":
        st["status"], st["reason"], st["question"] = "paused", d.get("reason") or "", d.get("question") or ""
    elif act in ("continue", "wait"):
        st["status"], st["reason"], st["question"] = "working", "", ""
    return st


# ---------- words -----------------------------------------------------------------------------------------------------
def _names(st: dict, ids, items=None) -> list[str]:
    by = {c["id"]: c for c in st.get("rubric") or []}
    ev = {i.get("id"): i.get("evidence", "") for i in items or []}
    out = []
    for i in ids or []:
        c = by.get(i)
        if c:
            out.append(c["text"] + (f" ({ev[i]})" if ev.get(i) else ""))
    return out


def score_line(st: dict) -> str:
    h = st.get("history") or []
    return f"{fmt(h[-1]['score'])}/{fmt(st.get('bar', 10))}" if h else f"not scored yet, bar {fmt(st.get('bar', 10))}"


def report(st: dict, check: dict | None = None) -> str:
    """The final reply, in the words of grader.report: "Met: 10/10 after 4 checks." or "Stopped at the budget:
    8/10." followed by what is not done and what could not be checked."""
    check = check if check is not None else (st.get("findings") or {})
    n = int(st.get("checks", 0))
    s = f"{fmt(check.get('score', 0))}/{fmt(st.get('bar', 10))}" if (st.get("history") or check.get("score") is not None) \
        else "not scored"
    status, reason = st.get("status"), st.get("reason") or ""
    if status == "met":
        head = f"Met: {s} after {n} check{'s' if n != 1 else ''}."
    elif status == "stopped" and reason == "budget":
        hit = st.get("budget_hit") or exhausted(st)
        head = f"Stopped at the budget: {s}." + (f" It reached {_BUDGET_WORDS[hit]}." if hit in _BUDGET_WORDS else "")
    elif status == "stopped":
        head = f"Stopped, {reason}: {s}." if reason else f"Stopped: {s}."
    elif status == "paused":
        head = f"Paused, {reason}: {s}." if reason else f"Paused: {s}."
    else:
        head = f"Goal: {s}."
    lines = [head]
    items = check.get("items") or []
    lines += [f"- Not done: {t}" for t in _names(st, [i["id"] for i in items if i.get("status") == "not_met"], items)]
    lines += [f"- Could not check: {t}" for t in _names(st, [i["id"] for i in items if i.get("status") == "unverified"],
                                                         items)]
    for g in check.get("gates") or []:
        if g.get("ok") is False:
            lines.append(f"- Gate failed: {g.get('name')}" + (f" (exit {g['code']})" if g.get("code") is not None else
                                                              f" ({g['why']})" if g.get("why") else ""))
        elif g.get("ok") is None and g.get("why"):
            lines.append(f"- Could not check: {g.get('name')} ({g['why']})")
    if check.get("error"):
        lines.append(f"- The last check could not run fully: {str(check['error'])[:160]}")
    if status == "paused" and st.get("question"):
        lines.append(st["question"])
    lines.append(f"{int(st.get('turns', 0))} turns, {n} checks, {money(st.get('spent', 0))}, "
                 f"{duration(st.get('elapsed', 0))} of work.")
    return "\n".join(lines[:24])


def money(x) -> str:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        v = 0.0
    return "under $0.01" if 0 < v < 0.01 else f"${v:.2f}"


def duration(secs) -> str:
    s = int(float(secs or 0))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60} min"


_STATUS_WORDS = {"drafting": "drafting the rubric", "review": "rubric open for edits", "working": "working",
                 "checking": "checking", "paused": "paused", "met": "met", "stopped": "stopped", "cleared": "cleared"}


def status_line(st: dict | None) -> str:
    """One plain line for the terminal: "Goal: ship it. Working. 7/10 (bar 10). Turn 6 of 30, 2 checks, $0.42, 12 min."""
    if not st:
        return "Goal: none."
    b = st.get("budgets") or {}
    state = _STATUS_WORDS.get(st.get("status"), st.get("status", ""))
    if st.get("reason") and st.get("status") in ("paused", "stopped"):
        state += f" ({'at the budget' if st['reason'] == 'budget' else st['reason']})"
    return (f"Goal: {st.get('goal', '')[:120]}. {state[:1].upper() + state[1:]}. {score_line(st)}. "
            f"Turn {int(st.get('turns', 0))} of {int(b.get('turns', 0))}, {int(st.get('checks', 0))} of "
            f"{int(b.get('checks', 0))} checks, {money(st.get('spent', 0))}"
            + (f" of ${float(b['usd']):.2f}" if float(b.get("usd") or 0) > 0 else "")
            + f", {duration(st.get('elapsed', 0))}.")


def away_line(st: dict) -> str:
    """One line for "While you were away" (away.py)."""
    g = re.sub(r"\s+", " ", str(st.get("goal") or "")).strip()
    g = g if len(g) <= 80 else g[:79].rstrip() + "…"
    s, n = score_line(st), int(st.get("checks", 0))
    status, reason = st.get("status"), st.get("reason") or ""
    if status == "met":
        return f"Goal met: {g}. {s} after {n} check{'s' if n != 1 else ''}."
    if status == "stopped" and reason == "budget":
        return f"Goal stopped at the budget: {g}. {s}."
    if status == "stopped":
        return f"Goal stopped, {reason}: {g}. {s}."
    if status == "paused":
        return f"Goal paused, {reason}: {g}." + (f" {st['question']}" if st.get("question") else "")
    return f"Goal {_STATUS_WORDS.get(status, status)}: {g}. {s}."


def missing_capabilities(st: dict) -> list[str]:
    """What the last check said open criteria need and meshpool lacks (grader item "missing_capability")."""
    items = ((st or {}).get("findings") or {}).get("items") or []
    return [f"{i.get('id')}: {i['missing_capability']}" for i in items
            if i.get("status") != "met" and str(i.get("missing_capability") or "").strip()]


def note(st: dict) -> str:
    """The note on each goal turn (hidden in the chat): the goal, the frozen rubric, the last findings, and how to end
    the reply. The agent sees the rubric; it cannot change it."""
    lines = [f"[meshpool] Goal mode. The goal of this chat: {st.get('goal', '')}",
             f"The bar is {fmt(st.get('bar', 10))}/10 on this fixed rubric. An independent check scores it; you cannot "
             "change the rubric or the score."]
    for c in st.get("rubric") or []:
        lines.append(f"- {c['id']}{' (must)' if c.get('must') else ''}, weight {c.get('weight', 1)}: {c['text']}"
                     + (f" Check: {c['check']}" if c.get("check") else ""))
    f = st.get("findings") or {}
    if st.get("history"):
        lines.append(f"Last check: {score_line(st)}.")
        items = f.get("items") or []
        for i in items:
            if i.get("status") != "met":
                lines.append(f"- {'not met' if i.get('status') == 'not_met' else 'could not check'}: {i.get('id')} "
                             f"{i.get('text', '')}" + (f" ({i['evidence']})" if i.get("evidence") else ""))
        for g in f.get("gates") or []:
            if g.get("ok") is False:
                lines.append(f"- gate failed: {g.get('name')}" + (f" (exit {g['code']})" if g.get("code") is not None
                                                                   else "") + (f": {g['tail'][-300:]}" if g.get("tail")
                                                                                else ""))
        for x in (f.get("fix") or [])[:8]:
            lines.append(f"- fix: {x}")
        for x in missing_capabilities(st)[:4]:
            lines.append(f"- needs a capability meshpool lacks ({x}). You may call get_capability to find a tool "
                         "and ask the user to add it, or write one and keep it with save_tool.")
    lines.append("Work toward the bar. End your reply with one last line: \"GOAL: READY\" when you believe every "
                 "criterion is met (an independent check will verify it), \"GOAL: CONTINUE\" and the next step if work "
                 "remains, \"GOAL: NEEDS YOU: <one plain question>\" if only the user can unblock it, or "
                 "\"GOAL: BLOCKED: <what blocks you>\" if something outside your control stops you.")
    if converge_due(st):
        lines.append(converge_note(st))
    return "\n".join(lines)


def brief(st: dict) -> str:
    """What the compaction handoff keeps word for word, so a long goal is never narrowed: the goal, the bar, the frozen
    rubric with its hash, and the last findings."""
    if not st or not st.get("goal"):
        return ""
    lines = [f"Goal (unchanged, set by the user): {st['goal']}", f"Bar: {fmt(st.get('bar', 10))}/10. Rubric (frozen, "
             f"hash {str(st.get('rubric_hash', ''))[:12]}):"]
    lines += [f"- {c['id']}{' (must)' if c.get('must') else ''}: {c['text']}" for c in st.get("rubric") or []]
    if st.get("history"):
        f = st.get("findings") or {}
        lines.append(f"Last check: {score_line(st)}. Open: " + "; ".join(
            f"{i.get('id')} {i.get('text', '')}" for i in f.get("items") or [] if i.get("status") != "met")[:1200])
    return "\n".join(lines)


def public(st: dict | None) -> dict | None:
    """The state for the page's goal card."""
    if not st:
        return None
    keep = ("goal", "mode", "bar", "budgets", "status", "reason", "question", "rubric", "rubric_hash", "frozen_at",
            "draft_until", "started", "ended", "turns", "checks", "spent", "elapsed", "history", "findings",
            "resume_at", "budget_hit")
    out = {k: st.get(k) for k in keep}
    out["history"] = [{"n": h.get("n"), "score": h.get("score"), "error": bool(h.get("error"))}
                      for h in st.get("history") or []]
    out["now"] = time.time()
    out["line"] = status_line(st)
    return out
