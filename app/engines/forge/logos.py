"""Company logos fetched from the internet as SVG, drawn as native vector shapes (zero images; render.logo_shape).

Sources, in order: the full-colour logo from gilbarbara/logos (CC0-1.0), else the Simple Icons mark (CC0-1.0) in the
brand's own colour from Simple Icons' data. The marks are their owners' trademarks, used only to identify the company.
Fetched once per company, cached in the Meshpool home folder, drawn as native shapes in their own colours
(vector.add_svg_colors). A company with no mark gets no logo (the slide shows its name instead).

    fetch("Visa") -> Path(".../logos/visa.svg") or None

Off when MH_FETCH_LOGOS=0 (tests set it) or when the network is unreachable; never raises.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

VERSION = "13.21.0"
COLOUR_URL = "https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/{slug}.svg"      # full colour, wordmarks
URLS = (f"https://cdn.jsdelivr.net/npm/simple-icons@{VERSION}/icons/{{slug}}.svg",
        f"https://unpkg.com/simple-icons@{VERSION}/icons/{{slug}}.svg")
DATA_URL = f"https://cdn.jsdelivr.net/npm/simple-icons@{VERSION}/_data/simple-icons.json"
TIMEOUT_S = 6
_MISS: set[str] = set()


def cache_dir() -> Path:
    home = os.environ.get("MESHPOOL_HOME") or str(Path.home() / ".meshpool")
    p = Path(home) / "cache" / "logos-colour"
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
        hyph = re.sub(r"[^a-z0-9]+", "-", cand).strip("-")      # gilbarbara/logos names: "goldman-sachs"
        for s in (plain, dot, hyph):
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
    cands = [c for c in slugs(name) if len(c) <= 64]       # a sentence is not a company: no 255+ char file names
    for s in cands:
        try:
            p = cache_dir() / f"{s}.svg"
            if p.is_file() and p.stat().st_size > 0:
                return p
        except OSError:
            continue
    for s in cands:                                      # 1. the full-colour logo
        if s in _MISS:
            continue
        data = _get(COLOUR_URL.format(slug=s))
        if data and b"<svg" in data[:400]:
            p = cache_dir() / f"{s}.svg"
            p.write_bytes(data)
            return p
    for s in cands:                                      # 2. the mark, in the brand's own colour
        if s in _MISS:
            continue
        for u in URLS:
            data = _get(u.format(slug=s))
            if data and data.lstrip().startswith(b"<svg"):
                hexc = brand_hex(s)
                if hexc and b"fill=" not in data[:200]:
                    data = data.replace(b"<svg ", f'<svg fill="#{hexc}" '.encode(), 1)
                p = cache_dir() / f"{s}.svg"
                p.write_bytes(data)
                return p
        _MISS.add(s)
    return None


_HEX: dict = {}


def brand_hex(slug: str) -> str | None:
    """The brand colour Simple Icons records for a slug (its data file, fetched once and cached)."""
    if not _HEX:
        import json as _json
        f = cache_dir() / "simple-icons.json"
        raw = f.read_bytes() if f.is_file() else None
        if raw is None:
            raw = _get_big(DATA_URL)
            if raw:
                f.write_bytes(raw)
        try:
            for ic in (_json.loads(raw).get("icons", []) if raw else []):
                sl = ic.get("slug") or re.sub(r"[^a-z0-9]", "", ic.get("title", "").lower()
                                              .replace("+", "plus").replace(".", "dot").replace("&", "and"))
                _HEX[sl] = ic.get("hex")
        except ValueError:
            pass
        _HEX.setdefault("__loaded__", "")
    return _HEX.get(slug) or None


def _get_big(url: str) -> bytes | None:
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "meshpool-forge"}),
                                    timeout=20) as r:
            return r.read(3_000_000) if r.status == 200 else None
    except Exception:                                      # noqa: BLE001
        return None


def for_names(names: list[str]) -> dict[str, str]:
    """{name: svg path} for the names that have a mark."""
    out = {}
    for n in names or []:
        p = fetch(str(n))
        if p:
            out[str(n)] = str(p)
    return out
