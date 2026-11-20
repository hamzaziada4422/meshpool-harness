"""Company logos fetched from the internet as SVG, drawn as native vector shapes (zero images; render.logo_shape).

Source: Simple Icons (https://simpleicons.org, icon data CC0-1.0; the marks are their owners' trademarks and are
used only to identify the company), version pinned, fetched once per company and cached in the Meshpool home
folder. A company with no mark there gets no logo (the slide shows its name instead).

    fetch("Visa") -> Path(".../logos/visa.svg") or None

Off when MH_FETCH_LOGOS=0 (tests set it) or when the network is unreachable; never raises.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

VERSION = "13.21.0"
URLS = (f"https://cdn.jsdelivr.net/npm/simple-icons@{VERSION}/icons/{{slug}}.svg",
        f"https://unpkg.com/simple-icons@{VERSION}/icons/{{slug}}.svg")
TIMEOUT_S = 6
_MISS: set[str] = set()


def cache_dir() -> Path:
    home = os.environ.get("MESHPOOL_HOME") or str(Path.home() / ".meshpool")
    p = Path(home) / "cache" / "logos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def slugs(name: str) -> list[str]:
    """Simple Icons slugs to try for a company name, most likely first ("J.P. Morgan" -> "jpmorgan")."""
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().strip()
    t = re.sub(r"\s*\((?:[^)]*)\)", "", t)
    base = t.replace("&", "and").replace("+", "plus")
    out = []
    for cand in (base, re.sub(r"\b(inc|corp|corporation|ltd|limited|plc|ag|sa|nv|group|holdings|co|llc|gmbh)\b\.?",
                              "", base)):
        dot = re.sub(r"[^a-z0-9.]", "", cand).replace(".", "dot")
        plain = re.sub(r"[^a-z0-9]", "", cand)
        for s in (plain, dot):
            if s and s not in out:
                out.append(s)
    first = re.sub(r"[^a-z0-9]", "", base.split()[0]) if base.split() else ""
    if first and first not in out and len(first) >= 3:
        out.append(first)
    return out


def _get(url: str) -> bytes | None:
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "meshpool-forge"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            if r.status != 200:
                return None
            return r.read(200_000)
    except Exception:                                      # noqa: BLE001 - offline, 404, slow: no logo
        return None


def fetch(name: str) -> Path | None:
    """The cached SVG of the company's mark, fetching it once; None when there is none or fetching is off."""
    if not name or os.environ.get("MH_FETCH_LOGOS", "1") == "0":
        return None
    cands = slugs(name)
    for s in cands:
        p = cache_dir() / f"{s}.svg"
        if p.is_file() and p.stat().st_size > 0:
            return p
    for s in cands:
        if s in _MISS:
            continue
        for u in URLS:
            data = _get(u.format(slug=s))
            if data and data.lstrip().startswith(b"<svg"):
                p = cache_dir() / f"{s}.svg"
                p.write_bytes(data)
                return p
        _MISS.add(s)
    return None


def for_names(names: list[str]) -> dict[str, str]:
    """{name: svg path} for the names that have a mark."""
    out = {}
    for n in names or []:
        p = fetch(str(n))
        if p:
            out[str(n)] = str(p)
    return out
