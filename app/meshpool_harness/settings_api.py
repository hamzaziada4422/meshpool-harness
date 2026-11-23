"""Settings, as the app's Settings page and `meshpool settings` read and write them: one validation and one write path.
The server adds what only the window knows (desktop/native flags) on top of `values()`."""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from meshpool_harness import config as C


def values(masked: bool = True) -> list[dict]:
    """Every setting as the Settings page lists it. Secrets show only whether they are set and their last 4 characters."""
    vals = C.read()
    fields = []
    for key, label, default, help_ in C.FIELDS:
        v = vals.get(key, "")
        item = {"key": key, "label": label, "default": default, "help": help_}
        if key in C.SECRET and masked:
            item["set"] = bool(v)
            item["hint"] = ("ends in " + v[-4:]) if len(v) >= 8 else ("set" if v else "")
            item["value"] = ""
        else:
            item["value"] = v
        fields.append(item)
    return fields


def _key_follows_host(vals: dict, typed_key: str = ""):
    """A saved key belongs to its endpoint: moving to another host needs that host's key, or the saved one is sent
    to the new host."""
    from meshpool_harness import providers as PV
    new = vals.get("MESHPOOL_BASE_URL")
    if new and PV.host(new) and PV.host(new) != PV.host(PV.key_base()) and PV.key() \
            and not (vals.get("MESHPOOL_API_KEY") or typed_key):
        raise ValueError(f"Enter the API key for {PV.host(new)} too: your saved key is for {PV.host(PV.key_base())} "
                         "and is not sent anywhere else.")


def save_settings(body: dict) -> None:
    allowed = {f[0] for f in C.FIELDS}
    vals = {}
    for k, v in (body.get("values") or {}).items():
        if k not in allowed:
            continue
        v = re.sub(r"[\r\n]", "", str(v)).strip()
        if k in C.SECRET:                # accept a key the way people paste it: quoted, or with "Bearer " in front
            v = C.clean_key(v)           # quotes, "Bearer ", line breaks, a pasted zero-width space
        if k in C.SECRET and v == "":
            continue                     # blank key field = keep the saved key
        vals[k] = v
    if vals.get("MESHPOOL_BASE_URL"):
        u = urlparse(vals["MESHPOOL_BASE_URL"])
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError("The server address must start with http:// or https:// and name a host.")
    from meshpool_harness import providers as PV
    new = vals.get("MESHPOOL_BASE_URL")
    from meshpool_harness import provider_setup as PS
    if new and not vals.get("MESHPOOL_API_KEY") and not PS._same_endpoint(new, PV.key_base()) and PV.key() \
            and PV.keyless_ok(new):
        vals["MESHPOOL_API_KEY"] = ""    # a local server needs no key: the old endpoint's key goes, not to this server
    _key_follows_host(vals)
    from meshpool_harness import browser_use as BU
    BU.validate(vals)                    # a browser setting that cannot work is refused here, with the reason
    if vals.get("GITHUB_TOKEN"):
        try:
            from meshpool_harness.ui import agent_api as _A
            _A.mcp_save({"name": "github", "enabled": True})
        except Exception:
            pass
    if vals:
        C.write(vals)
        for k, v in vals.items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
    return None


