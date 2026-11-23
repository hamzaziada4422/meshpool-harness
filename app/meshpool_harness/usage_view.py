"""The usage numbers every front end shows. Nothing is counted or priced here: the totals, the per-model and per-chat
rows and every cost come from core/usage.py (the one ledger, through workbench.usage), the per-day rows from the same
ledger, and the key's own spend from the provider. The app's /api/usage and /api/spend routes and `meshpool usage`
call `summary`."""
from __future__ import annotations


def summary(days: int = 30, since: float = 0.0, fresh: bool = False, parts=("usage", "days", "key")) -> dict:
    from meshpool_harness.ui import settings_v2 as SV
    from meshpool_harness.ui import workbench as W
    out = {}
    if "usage" in parts:
        out["usage"] = W.usage(since)
    if "days" in parts:
        out["days"] = SV.usage_by_day(days)
    if "key" in parts:
        out["key"] = SV.key_spend(fresh)
    return out
