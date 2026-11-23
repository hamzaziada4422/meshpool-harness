"""A command-line copy updates itself from a fake release server, end to end: two clicks at once install once, the app
restarts into the new code, and no stage folder or journal is left behind."""
from __future__ import annotations

import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the restart handshake is exercised on POSIX only")

APP = Path(__file__).resolve().parents[1]
NEW = "0.99.0"   # not 9.x: the updater treats major >= 2 as the old legacy line for a 0.x copy


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _post(base, tok, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def _wait(url, secs=60):
    end = time.time() + secs
    while time.time() < end:
        try:
            return _get(url)
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    pytest.fail(f"nothing answered at {url} within {secs} s")


def _copy_app(dst: Path, version: str | None = None) -> None:
    dst.mkdir(parents=True)
    ign = shutil.ignore_patterns("__pycache__", "*.pyc")
    for d in ("meshpool_harness", "engines", "profiles", "defaults"):
        if (APP / d).is_dir():
            shutil.copytree(APP / d, dst / d, ignore=ign)
    for f in ("meshpool_recover.py", "sitecustomize.py", "routing.csv"):
        if (APP / f).is_file():
            shutil.copy2(APP / f, dst / f)
    if version:
        init = dst / "meshpool_harness" / "__init__.py"
        init.write_text(re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', init.read_text(), count=1))


def test_cli_copy_updates_itself_end_to_end(tmp_path):
    inst = tmp_path / "inst"
    _copy_app(inst / "app")
    (inst / "venv").mkdir()
    req = (APP.parent / "packaging" / "requirements-cli.txt").read_text()
    (inst / "app" / "requirements-cli.txt").write_text(req)

    # the release: Meshpool-src.tar.gz (top folder holds app/ and packaging/), its checksums and the releases list
    rel = tmp_path / "rel"
    top = rel / "build" / "Meshpool-src"
    _copy_app(top / "app", NEW)
    (top / "packaging").mkdir()
    (top / "packaging" / "requirements-cli.txt").write_text(req)
    serve = tmp_path / "serve"
    serve.mkdir()
    with tarfile.open(serve / "Meshpool-src.tar.gz", "w:gz") as tf:
        tf.add(top, arcname="Meshpool-src")
    digest = hashlib.sha256((serve / "Meshpool-src.tar.gz").read_bytes()).hexdigest()
    (serve / "SHA256SUMS.txt").write_text(f"{digest}  Meshpool-src.tar.gz\n")
    fs = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=str(serve)))
    threading.Thread(target=fs.serve_forever, daemon=True).start()
    root = f"http://127.0.0.1:{fs.server_address[1]}"
    (serve / "releases.json").write_text(json.dumps([{
        "tag_name": f"v{NEW}", "draft": False, "prerelease": False, "body": "test", "html_url": root,
        "published_at": "2099-01-01T00:00:00Z",
        "assets": [{"name": n, "browser_download_url": f"{root}/{n}", "size": 1}
                   for n in ("Meshpool-src.tar.gz", "SHA256SUMS.txt")]}]))

    home, mh = tmp_path / "home", tmp_path / "mh"
    home.mkdir()
    mh.mkdir()
    (mh / "meshpool.config").write_text("MESHPOOL_API_KEY=test-key-1234\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(inst / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MESHPOOL_UPDATE_API=f"{root}/releases.json", MH_SLOTS_DIR=str(tmp_path / "slots"))
    env.pop("MESHPOOL_CONFIG", None)
    port = _port()
    base = f"http://127.0.0.1:{port}"
    log = open(tmp_path / "ui.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(port)],
                            cwd=str(inst / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    pids = {proc.pid}
    try:
        tok = re.search(r'token: "([^"]+)"', _wait(base + "/")).group(1)
        a = json.loads(_get(base + "/healthz"))
        assert a["pid"] == proc.pid

        results = []

        def click():
            try:
                results.append(_post(base, tok, "/api/update/install"))
            except Exception as e:  # noqa: BLE001 - the old process may already be gone
                results.append({"exc": repr(e)})
        ts = [threading.Thread(target=click) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(120)
        oks = [r for r in results if r.get("ok") and r.get("action") == "restarting"]
        assert len(oks) == 1, results                      # one click installs; the other never swaps a second time
        rest = [r for r in results if r not in oks]
        assert len(rest) == 1 and not rest[0].get("ok"), results
        if "error" in rest[0]:
            assert "already being installed" in rest[0]["error"] or "restarting" in rest[0]["error"], results

        end = time.time() + 90
        b = None
        while time.time() < end:
            try:
                h = json.loads(_get(base + "/healthz"))
                if h.get("boot") != a["boot"]:
                    b = h
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        assert b, "the app did not come back with a new boot id:\n" + (tmp_path / "ui.log").read_text()[-3000:]
        pids.add(b["pid"])
        tok2 = re.search(r'token: "([^"]+)"', _get(base + "/")).group(1)
        st = _post(base, tok2, "/api/update/status")
        assert st["current"] == NEW, st
        left = [p.name for p in (inst / "app").iterdir() if p.name.startswith(".update-") and p.name != ".update-recovered.json"]
        assert left == [], left                            # no stage folder, no journal
        assert not (inst / "app" / ".update-recovered.json").exists()
        assert f'__version__ = "{NEW}"' in (inst / "app" / "meshpool_harness" / "__init__.py").read_text()
    finally:
        for p in pids:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        proc.kill()
        proc.wait(10)
        log.close()
        fs.shutdown()
