"""Run the real Codex CLI (`codex exec`) on one task against an OpenAI-compatible endpoint, for the small cost tests.

The endpoint is whatever base URL you pass (normally the metering proxy in small_meter.py, which forwards to your real
gateway), on the Responses wire like the Codex arm of the full benchmark. Codex's own token report is ignored: the
proxy counts every request, which is the only count that is right per call.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CACHE = Path(os.environ.get("MH_SMALL_CACHE") or Path.home() / ".cache" / "meshpool-small")   # not /tmp: Codex refuses to
                                                                                            # make helper binaries there


def find_codex() -> list[str] | None:
    """The command that starts the Codex CLI: $CODEX_BIN, `codex` on PATH, a copy in the npx cache, or npx itself."""
    if os.environ.get("CODEX_BIN"):
        return [os.environ["CODEX_BIN"]]
    if shutil.which("codex"):
        return [shutil.which("codex")]
    hits = sorted(glob.glob(str(Path.home() / ".npm" / "_npx" / "*" / "node_modules" / ".bin" / "codex")))
    if hits:
        return [hits[-1]]
    if shutil.which("npx"):
        return ["npx", "-y", os.environ.get("CODEX_PKG", "@openai/codex@latest")]
    return None


def version(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        return (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "?"
    except Exception as e:  # noqa: BLE001
        return f"unavailable ({e})"


def make_home(base_url: str, model: str) -> str:
    """A throwaway CODEX_HOME whose config.toml points Codex at base_url (key from $MESHPOOL_API_KEY, never a file)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    home = tempfile.mkdtemp(prefix="codex-home-", dir=str(CACHE))
    q = lambda v: json.dumps(str(v))  # noqa: E731  (a TOML basic string is a JSON string for our values)
    (Path(home) / "config.toml").write_text(
        f"model = {q(model)}\nmodel_provider = \"meshpool\"\nmodel_context_window = 128000\n\n"
        f"[model_providers.meshpool]\nname = \"Meshpool\"\nbase_url = {q(base_url)}\nenv_key = \"MESHPOOL_API_KEY\"\n"
        "wire_api = \"responses\"\nrequest_max_retries = 2\nstream_max_retries = 2\n", encoding="utf-8")
    return home


def run(cmd: list[str], prompt: str, workdir: str, base_url: str, model: str, timeout: float,
        sandbox: str | None = None) -> dict:
    """Returns {answer, returncode, error, events}. Never raises for a failed run."""
    home = make_home(base_url, model)
    out_file = Path(home) / "last.txt"
    env = dict(os.environ, CODEX_HOME=home)
    env.setdefault("MESHPOOL_API_KEY", "proxy-injected")
    argv = cmd + ["exec", "--json", "--skip-git-repo-check", "--ephemeral",
                  "-s", sandbox or os.environ.get("MH_SMALL_CODEX_SANDBOX", "workspace-write"),
                  "-C", workdir, "-o", str(out_file), prompt]
    res = {"answer": "", "returncode": None, "error": None, "events": 0}
    try:
        p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        res["returncode"] = p.returncode
        errs = []
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            res["events"] += 1
            it = ev.get("item") or {}
            if ev.get("type") == "item.completed" and it.get("type") == "agent_message":
                res["answer"] = it.get("text") or res["answer"]
            if ev.get("type") in ("error", "turn.failed"):
                errs.append(str((ev.get("error") or {}).get("message") or ev.get("message") or ev)[:300])
        if out_file.exists() and out_file.read_text().strip():
            res["answer"] = out_file.read_text()
        if p.returncode != 0 and not errs:
            errs.append((p.stderr or p.stdout)[-300:].strip())
        res["error"] = errs[-1] if errs else None
    except subprocess.TimeoutExpired:
        res["error"] = f"timeout after {timeout:.0f}s"
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return res
