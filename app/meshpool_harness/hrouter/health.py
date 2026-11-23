"""Which installed agents failed to start, so Auto stops picking them (audit M29-02).

An agent whose process cannot start (missing runtime, broken install, sign-in needed) looked "installed" to
`harness_router._ready()` and was picked for every new Auto chat. ACP and Pi record the failure here and the success
that clears it. A failure excludes the agent from Auto for RETRY_AFTER_S, so a fixed install comes back on its own;
`start_ok` (it started, e.g. the user picked it by hand) clears it at once.

Nothing here may raise: routing must never fail a task because a health file is damaged or the disk is full.
"""
from __future__ import annotations

import time

from meshpool_harness import config as C
from meshpool_harness import fsio

RETRY_AFTER_S = 6 * 3600


def _path():
    return C.home() / "routing" / "harness-health.json"


def _load() -> dict:
    try:
        d = fsio.read_json(_path(), {})
    except Exception:  # noqa: BLE001 - CorruptFile or anything else: treat as no failures
        return {}
    return d if isinstance(d, dict) else {}


def start_failed(hid: str, error: str) -> None:
    try:
        def fn(cur):
            cur = cur if isinstance(cur, dict) else {}
            n = int((cur.get(hid) or {}).get("count") or 0) + 1
            cur[hid] = {"error": str(error)[:300], "ts": time.time(), "count": n}
            return cur
        fsio.update_json(_path(), fn, {})
    except Exception:  # noqa: BLE001 - includes CorruptFile: never fail a turn over bookkeeping
        pass


def start_ok(hid: str) -> None:
    try:
        if hid not in _load():
            return                                  # the usual case: a healthy start writes nothing
        fsio.update_json(_path(), lambda cur: {k: v for k, v in (cur or {}).items() if k != hid}, {})
    except Exception:  # noqa: BLE001
        pass


def broken(now: float | None = None) -> dict:
    """{harness id: {error, ts, count}} for agents whose last start failed less than RETRY_AFTER_S ago."""
    t = time.time() if now is None else now
    return {k: v for k, v in _load().items()
            if isinstance(v, dict) and t - float(v.get("ts") or 0) < RETRY_AFTER_S}
