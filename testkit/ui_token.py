"""The API token of a running Meshpool app, for test scripts.

Since 0.0.16 the app page carries the token only for the launch key the app prints at startup
("Open http://127.0.0.1:PORT/?k=KEY ..."); a bare GET / is refused with 403. token(base) finds the key, in order:
  MESHPOOL_UI_KEY            the key itself
  MESHPOOL_UI_URL            the printed launch address (used when its port matches base)
  log=...                    the app's own log file, when the script started the app itself
  MESHPOOL_UI_LOG            one app log file
  MESHPOOL_UI_LOGS           a folder of app-PORT.log files (testkit/run_all.sh)
An app started with MH_UI_OPEN_PAGE=1 (tests only) needs no key.
"""
from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

_KEY = re.compile(r"\?k=([\w-]+)")


def _from_log(path, wait: float) -> str:
    end = time.time() + wait
    while True:
        try:
            m = _KEY.search(Path(path).read_text(errors="replace"))
        except OSError:
            m = None
        if m or time.time() >= end:
            return m.group(1) if m else ""
        time.sleep(0.5)


def key(base: str, log=None, wait: float = 60) -> str:
    """The launch key for the app at base, or "" when none is known."""
    if os.environ.get("MESHPOOL_UI_KEY"):
        return os.environ["MESHPOOL_UI_KEY"]
    port = urlparse(base).port
    url = os.environ.get("MESHPOOL_UI_URL", "")
    if url and urlparse(url).port == port and _KEY.search(url):
        return _KEY.search(url).group(1)
    logs = [log] if log else []
    if os.environ.get("MESHPOOL_UI_LOG"):
        logs.append(os.environ["MESHPOOL_UI_LOG"])
    if os.environ.get("MESHPOOL_UI_LOGS") and port:
        logs.append(Path(os.environ["MESHPOOL_UI_LOGS"]) / f"app-{port}.log")
    for p in logs:
        k = _from_log(p, wait if p == log else 2)
        if k:
            return k
    return ""


def page(base: str, log=None, wait: float = 60, timeout: float = 30) -> str:
    """The app page with the token in it. Raises SystemExit with what to set when the app refuses it."""
    base = base.rstrip("/")
    k = key(base, log, wait)
    try:
        with urllib.request.urlopen(base + "/" + (f"?k={k}" if k else ""), timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise SystemExit(f"{base} refused its page: it needs the launch key the app printed at startup. Set "
                             "MESHPOOL_UI_URL to that address (Open http://127.0.0.1:PORT/?k=...), or start the app "
                             "with MH_UI_OPEN_PAGE=1 for a test run.") from None
        raise


def token(base: str, log=None, wait: float = 60) -> str:
    m = re.search(r'token: "([^"]+)"', page(base, log, wait))
    if not m:
        raise SystemExit(f"{base} answered, but its page has no API token")
    return m.group(1)
