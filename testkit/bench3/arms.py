"""How each agent is run. Every arm gets a fresh workdir, a private home/config dir, the metered base URL and a model id,
and returns {"reply", "secs", "rc", "error", "log", "timed_out"}; secs is the wall-clock of the agent run only."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PYTHON = next((p for p in (__import__("os").environ.get("BENCH_PYTHON"), "/root/.venvs/mp/bin/python")
               if p and __import__("os").path.exists(p)), __import__("sys").executable)   # any machine, not one path
BUILTIN = ("codex", "pi", "mp-cli", "mp-work", "mp-code", "mp-on-pi", "mp-on-codex")
# which arms apply to which task kind (the runner's default)
APPLIES = {"office": ["codex", "pi", "mp-work", "mp-on-pi", "mp-on-codex", "mp-cli"],
           "code": ["codex", "pi", "mp-code", "mp-on-pi", "mp-on-codex", "mp-cli"]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def kill_group(p: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            p.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


_PRIVATE_TMP: bool | None = None


def _private_tmp_ok() -> bool:
    """unshare can give a run its own /tmp (a user + mount namespace with a tmpfs on /tmp). Checked once."""
    global _PRIVATE_TMP
    if _PRIVATE_TMP is None:
        _PRIVATE_TMP = False
        if os.name != "nt" and os.environ.get("BENCH_PRIVATE_TMP", "1") != "0" and shutil.which("unshare"):
            try:
                r = subprocess.run(["unshare", "--mount", "--map-root-user", "sh", "-c", "mount -t tmpfs tmpfs /tmp"],
                                   capture_output=True, timeout=10)
                _PRIVATE_TMP = r.returncode == 0
            except (OSError, subprocess.SubprocessError):
                pass
    return _PRIVATE_TMP


def private_tmp(cmd: list, *paths) -> list:
    """Wrap cmd so it sees an empty /tmp of its own: agents write literal /tmp/check.py, and parallel runs read and
    overwrite each other's (eval: 142 Meshpool bodies used /tmp/check.py). Not possible when the run's own folders
    live under /tmp (the tmpfs would hide them) or the system has no user namespaces: then cmd is returned as is."""
    if not _private_tmp_ok():
        return cmd
    for p in paths:
        try:
            if p and Path(p).resolve().is_relative_to("/tmp"):
                return cmd
        except (OSError, ValueError):
            return cmd
    # also hide the benchmark's own sources (task definitions with their hidden tests): an agent that runs `find /`
    # found and read them (eval final14, csv-import: 8 calls in testkit/loop, then it ran the hidden test)
    kit = str(Path(__file__).resolve().parents[1])
    for p in paths:
        try:
            if p and Path(p).resolve().is_relative_to(kit):
                kit = ""                                    # a run folder inside the kit: leave it visible
        except (OSError, ValueError):
            pass
    hide = f" && mount -t tmpfs tmpfs {shlex.quote(kit)}" if kit else ""
    return ["unshare", "--mount", "--map-root-user", "sh", "-c", f'mount -t tmpfs tmpfs /tmp{hide} && exec "$@"', "sh",
            *cmd]


def run_proc(cmd: list, *, cwd, env: dict, timeout: float, log: Path) -> dict:
    """Run cmd in its own process group with stdin=/dev/null, stdout+stderr to log; kill the group on timeout."""
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    # a private temp dir per run: scratch files one run leaves in $TMPDIR (repro copies, probe scripts) must not be
    # found by the next rep of the same task. Literal /tmp paths cannot be isolated here: runs share /tmp in parallel.
    tmp = Path(log).with_suffix(".tmp.d")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    env = {**env, "TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp)}
    cmd = private_tmp(cmd, cwd, log, tmp, env.get("HOME"), env.get("CODEX_HOME"), env.get("PI_CODING_AGENT_DIR"))
    t0 = time.time()
    timed_out = False
    with open(log, "wb") as lf:
        try:
            p = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        except OSError as e:
            return {"rc": None, "secs": 0.0, "timed_out": False, "error": f"cannot start {cmd[0]}: {e}", "log": str(log)}
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        kill_group(p)       # also reaps stragglers (engines, browsers) the agent left in the group
        shutil.rmtree(tmp, ignore_errors=True)
    return {"rc": p.returncode, "secs": round(time.time() - t0, 2), "timed_out": timed_out,
            "error": f"timeout after {timeout}s" if timed_out else "", "log": str(log)}


def _tail(log, n=4000) -> str:
    try:
        return Path(log).read_text(errors="replace")[-n:]
    except OSError:
        return ""


def _env(extra: dict | None = None) -> dict:
    e = dict(os.environ)                  # HTTPS_PROXY etc. pass through unchanged
    e.update(extra or {})
    return e


# ------------------------------------------------------------------ codex
def codex_cmd() -> list:
    """The command that starts Codex (small_codex.find_codex: $CODEX_BIN, PATH, the npx cache)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "loop"))
    import small_codex
    return small_codex.find_codex() or ["codex"]


def run_codex(*, prompt, workdir, home, base_url, model, timeout, log, **_) -> dict:
    home = Path(home)
    import tempfile
    import small_codex                        # Codex refuses to build helper binaries under /tmp: use its cache dir
    small_codex.CACHE.mkdir(parents=True, exist_ok=True)
    ch = Path(tempfile.mkdtemp(prefix="codex-home-", dir=str(small_codex.CACHE)))
    last = ch / "last.txt"
    (ch / "config.toml").write_text(
        f'model = "{model}"\nmodel_provider = "meter"\napproval_policy = "never"\nsandbox_mode = "danger-full-access"\n\n'
        f'[model_providers.meter]\nname = "meter"\nbase_url = "{base_url}"\nenv_key = "METER_KEY"\nwire_api = "responses"\n')
    codex = codex_cmd()
    cmd = codex + ["exec", "--skip-git-repo-check", "--output-last-message", str(last), prompt]
    r = run_proc(cmd, cwd=workdir, env=_env({"CODEX_HOME": str(ch), "METER_KEY": "dummy"}), timeout=timeout, log=log)
    reply = last.read_text(errors="replace").strip() if last.exists() else ""
    r["reply"] = reply or _tail(log, 2000)
    import shutil
    shutil.rmtree(ch, ignore_errors=True)
    return r


# ------------------------------------------------------------------ pi
def run_pi(*, prompt, workdir, home, base_url, model, timeout, log, **_) -> dict:
    home = Path(home)
    pd = home / "pi"
    pd.mkdir(parents=True, exist_ok=True)
    chat = model.startswith("deepseek")
    models = {"providers": {"meter": {
        "baseUrl": base_url, "api": "openai-completions" if chat else "openai-responses", "apiKey": "dummy",
        "models": [{"id": model, "name": model, "reasoning": not chat, "input": ["text"], "contextWindow": 400000,
                    "maxTokens": 64000, "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}]}}}
    (pd / "models.json").write_text(json.dumps(models, indent=1))
    cmd = ["pi", "--provider", "meter", "--model", model, "-p", prompt]
    r = run_proc(cmd, cwd=workdir, env=_env({"PI_CODING_AGENT_DIR": str(pd), "PI_SKIP_VERSION_CHECK": "1",
                                              "PI_TELEMETRY": "0"}), timeout=timeout, log=log)
    r["reply"] = _tail(log, 4000).strip()
    return r


# ------------------------------------------------------------------ meshpool
def write_mp_config(home: Path, base_url: str, model: str) -> dict:
    home.mkdir(parents=True, exist_ok=True)
    # the settings file overrides the environment, so the per-run base URL has to be in it
    (home / "meshpool.config").write_text(
        f"MESHPOOL_API_KEY=dummy\nMESHPOOL_BASE_URL={base_url}\nMH_MODEL={model}\n")
    return {"MESHPOOL_HOME": str(home), "MESHPOOL_API_KEY": "dummy", "MESHPOOL_BASE_URL": base_url, "MH_MODEL": model}


def _mp_env(mp_src, home, base_url, model) -> dict:
    e = write_mp_config(Path(home), base_url, model)
    e["PYTHONPATH"] = str(mp_src) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
    e.setdefault("MH_ALLOW_SYSTEM_SOFFICE", "1")       # the shipped app bundles LibreOffice; a source run uses the system's
    return _env(e)


def run_mp_cli(*, prompt, workdir, home, base_url, model, timeout, log, mp_src="app", python=DEFAULT_PYTHON, **_) -> dict:
    env = _mp_env(Path(mp_src).resolve(), home, base_url, model)
    cmd = [python, "-m", "meshpool_harness", "-p", prompt, "--auto", "--output-format", "json"]
    r = run_proc(cmd, cwd=workdir, env=env, timeout=timeout, log=log)
    reply = ""
    for line in reversed(_tail(log, 200000).splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                reply = str(json.loads(line).get("result") or "")
                break
            except ValueError:
                continue
    r["reply"] = reply
    return r


def _client(base: str):
    tok = re.search(r'token: "([^"]+)"', urllib.request.urlopen(base + "/", timeout=30).read().decode()).group(1)

    def call(path, body=None, timeout=120):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"_http": e.code, "_body": e.read()[:300].decode(errors="replace")}
    return call


def _drive_turn(c, harness, kind, text, workdir, limit) -> tuple[dict, float, bool]:
    """Create an agent session in the app, send one message, auto-approve, answer questions, wait for the turn end.
    Returns (final state, secs from send to turn end, timed_out)."""
    v = c("/api/agent", {"workdir": str(workdir), "harness": harness, "kind": kind, "mode": "auto"})
    if "id" not in v:
        return {"error": v}, 0.0, False
    sid = v["id"]
    time.sleep(1.5)
    t0 = time.time()
    c(f"/api/agent/{sid}/send", {"text": text})
    seen_q: set = set()
    while time.time() - t0 < limit:
        v = c(f"/api/agent/{sid}?since=0")
        evs = v.get("events", [])
        done = {e["id"] for e in evs if e["type"] == "approval_done"}
        for e in evs:
            if e["type"] == "approval" and e["id"] not in done:
                c(f"/api/agent/{sid}/answer", {"id": e["id"], "decision": "yes"})
        answered = {e["id"] for e in evs if e["type"] == "question_done"}
        for e in evs:                               # ask_user: take the recommended options, as a user in a hurry
            if e["type"] == "question" and e["id"] not in answered and e["id"] not in seen_q:
                seen_q.add(e["id"])
                picks = [{"selected": [o["label"] for o in q.get("options", []) if o.get("recommended")]
                          or [q["options"][0]["label"]], "other": ""} for q in e.get("questions") or []]
                c(f"/api/agent/{sid}/reply", {"question_id": e["id"], "answers": picks})
        if v.get("state") == "idle" and any(e["type"] == "turn_end" for e in evs):
            return v, round(time.time() - t0, 2), False
        time.sleep(1)
    c(f"/api/agent/{sid}/stop", {})
    return v, round(time.time() - t0, 2), True


def _run_mp_app(harness, kind, *, prompt, workdir, home, base_url, model, timeout, log, mp_src="app",
                python=DEFAULT_PYTHON, **_) -> dict:
    env = _mp_env(Path(mp_src).resolve(), home, base_url, model)
    port = free_port()
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    lf = open(log, "wb")
    p = subprocess.Popen([python, "-m", "meshpool_harness", "ui", "--port", str(port), "--no-browser"], cwd=str(workdir),
                         env=env, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    res = {"rc": None, "secs": 0.0, "timed_out": False, "error": "", "log": str(log), "reply": ""}
    try:
        c = None
        for _ in range(120):
            if p.poll() is not None:
                break
            try:
                c = _client(base)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        if c is None:
            res["error"] = "app server did not start: " + _tail(log, 600)
            return res
        v, secs, to = _drive_turn(c, harness, kind, prompt, workdir, timeout)
        res.update(secs=secs, timed_out=to, rc=0 if not to else None)
        if "error" in v and "events" not in v:
            res["error"] = f"cannot start agent: {v['error']}"
        elif to:
            res["error"] = f"timeout after {timeout}s"
        events = v.get("events", [])
        res["reply"] = "\n".join(e.get("text") or "" for e in events if e["type"] == "assistant")
        errs = [str(e.get("text") or e.get("message") or e)[:300] for e in events if e["type"] == "error"]
        if errs and not res["error"]:
            res["error"] = "; ".join(errs[-2:])
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
    finally:
        kill_group(p)
        lf.close()
    return res


def run_mp_work(**kw) -> dict:
    return _run_mp_app("meshpool-work", "cowork", **kw)


def run_mp_code(**kw) -> dict:
    return _run_mp_app("meshpool-code", "code", **kw)


PI_PKG = "@earendil-works/pi-coding-agent@1.5.2"          # the version the app ships (THIRD_PARTY_NOTICES.md)
_PI_LOCK = __import__("threading").Lock()


def pi_mp_src(mp_src, cache: Path | None = None) -> Path:
    """The app's Pi harness needs the vendored Pi (app/vendor/pi), which source checkouts do not contain. Return mp_src
    itself when it has Pi; otherwise a copy of it with vendor/pi pointing at a cached npm install (the checkout under
    test is never modified). The copy is made once per cache dir and mp_src path."""
    import hashlib
    import shutil
    mp_src = Path(mp_src).resolve()
    rel = Path("node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js")
    if (mp_src / "vendor" / "pi" / rel).is_file() or (mp_src.parent / "pi" / rel).is_file():
        return mp_src
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "loop"))
    import small_codex
    cache = cache or small_codex.CACHE / "bench3"
    with _PI_LOCK:
        pi = cache / "pi"
        if not (pi / rel).is_file():
            pi.mkdir(parents=True, exist_ok=True)
            subprocess.run(["npm", "install", "--no-audit", "--no-fund", "--prefix", str(pi), PI_PKG], check=True,
                           capture_output=True, text=True, timeout=600)
        shadow = cache / "src" / hashlib.sha1(str(mp_src).encode()).hexdigest()[:10] / "app"
        stamp = _src_stamp(mp_src)                 # same path, changed code (a new commit checked out): copy again
        if not (shadow / "vendor" / "pi").exists() or _read(shadow.parent / "stamp") != stamp:
            shutil.rmtree(shadow.parent, ignore_errors=True)
            shutil.copytree(mp_src, shadow, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            (shadow / "vendor").mkdir(exist_ok=True)
            (shadow / "vendor" / "pi").symlink_to(pi)
            (shadow.parent / "stamp").write_text(stamp)
    return shadow


def _read(p: Path) -> str:
    try:
        return p.read_text()
    except OSError:
        return ""


def _src_stamp(mp_src: Path) -> str:
    """A fingerprint of the app source: every file's path, size and modification time."""
    import hashlib
    h = hashlib.sha1()
    for f in sorted(mp_src.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc" and "vendor" not in f.parts:
            st = f.stat()
            h.update(f"{f.relative_to(mp_src)}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def run_mp_on_pi(task_kind="code", mp_src="app", **kw) -> dict:
    """The Meshpool app driving Pi (harness "pi"). Its models.json comes from the app's endpoint setting, which is the
    per-run meshpool.config, so Pi's calls land on the meter path."""
    try:
        src = pi_mp_src(mp_src)
    except Exception as e:  # noqa: BLE001
        return {"reply": "", "secs": 0.0, "rc": None, "timed_out": False, "log": str(kw.get("log", "")),
                "error": f"cannot provide Pi for the app: {e}"}
    return _run_mp_app("pi", "cowork" if task_kind == "office" else "code", mp_src=src, **kw)


def run_mp_on_codex(task_kind="code", **kw) -> dict:
    """The Meshpool app driving Codex over ACP (harness "codex"); prepare_codex writes its config.toml from the same
    endpoint setting."""
    return _run_mp_app("codex", "cowork" if task_kind == "office" else "code", **kw)


ARMS = {"codex": run_codex, "pi": run_pi, "mp-cli": run_mp_cli, "mp-work": run_mp_work, "mp-code": run_mp_code,
        "mp-on-pi": run_mp_on_pi, "mp-on-codex": run_mp_on_codex}


def arms_for(kind: str, wanted: list[str] | None) -> list[str]:
    base = list(APPLIES.get(kind, APPLIES["code"]))
    extra = [a for a in ARMS if a not in BUILTIN]                  # registered test/custom arms apply everywhere
    allowed = base + extra
    return [a for a in (wanted or allowed) if a in allowed]


def run_arm(arm: str, **kw) -> dict:
    r = ARMS[arm](**kw)
    r.setdefault("reply", "")
    r.setdefault("error", "")
    r.setdefault("timed_out", False)
    return r
