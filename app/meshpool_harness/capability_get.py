"""Get a capability mid-task: the get_capability agent tool.

When a request needs something meshpool lacks, the agent calls get_capability(need). The search goes in this order and
stops at the first place that has a good match:
  1. what is already here: enabled skills, saved tools and the chat's connected tools
  2. the curated MCP library (mcp_library.py)
  3. the official MCP Registry (active entries only; skipped with a plain note when offline)
  4. a package on npm or PyPI that is an MCP server
  5. else: write a new tool with save_tool
It returns ranked candidates. Adding one (get_capability with `add`) shows the user a card with the name, the publisher,
whether it is verified (the registry verified its namespace and package, or it is on meshpool's curated list), the
pinned exact version (never "latest") and where data goes. A remote service, where data leaves the computer, gets a card
that names the company and the data sent every time it is added, and is never added without the user's yes. A local
tool needs one yes. Secrets go to meshpool's settings store; mcp.json keeps only ${NAME} placeholders. No telemetry:
the only requests are the searches themselves.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

from meshpool_harness import config as C

REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers"
NPM_SEARCH = "https://registry.npmjs.org/-/v1/search"
PYPI_JSON = "https://pypi.org/pypi/{name}/json"
OFFICIAL = "io.modelcontextprotocol.registry/official"
HTTP_TIMEOUT = 8
MARK = "Added by get_capability."             # starts the mcp.json description of every server added here
_EXACT = re.compile(r"^v?\d+(\.\d+){0,3}([-+][0-9A-Za-z.-]+)?$")   # 1.2.3, 2026.11.11, 1.0.0-beta.1; never a range
TIERS = ("enabled", "library", "registry", "package", "write")
# What a registry or npm says is data from the internet: a package name must be a plain name, never an option such as
# "--registry=..." that npx or uvx would read as its own; a header name must be a plain token.
_NPM_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
_PYPI_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_HEADER = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")


def _d(x) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x) -> list:
    return x if isinstance(x, list) else []


def _s(x) -> str:
    return x if isinstance(x, str) else ""


def reserved(name: str) -> bool:
    """meshpool's own settings (its model key and address, MH_* settings, the keys it keeps for its tools): a tool found
    online never gets one of these filled in, whatever it asks for."""
    n = str(name or "").upper()
    return n.startswith(("MH_", "MESHPOOL_")) or n in C.SECRET or n in C.ENDPOINT_NAMES


class Offline(Exception):
    """A search could not reach its service."""


def http_get_json(url: str, timeout: float = HTTP_TIMEOUT) -> dict:
    """GET a JSON document with a timeout. Raises Offline on any network or parse error."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "meshpool"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(4_000_000).decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001 - offline, DNS, TLS, timeout, 4xx/5xx, bad JSON: all "could not reach it"
        raise Offline(str(e)[:200]) from e


def exact(version) -> bool:
    v = str(version or "").strip()
    return bool(v) and v.lower() not in ("latest", "*", "x") and bool(_EXACT.match(v))


# ---------- scoring -------------------------------------------------------------------------------------------------

def _terms(text: str) -> set:
    from meshpool_harness import skills as SK
    return set(SK._terms(text))


def _score(q: set, name: str, desc: str) -> int:
    n, d = _terms(re.sub(r"[_\-./]", " ", name)), _terms(desc)
    return 3 * len(q & n) + len(q & d)


def _strong(c: dict, q: set) -> bool:
    return c["score"] >= 3 or c.get("hits", 0) >= min(2, len(q))


# ---------- 1. already here -----------------------------------------------------------------------------------------

def _enabled(q: set, need: str, tools) -> list[dict]:
    from meshpool_harness import skills as SK
    out = []
    for s in SK.search(need, k=3, min_terms=1):
        sc = _score(q, s["name"], SK._desc(s))
        failing = s.get("status") == "failing"
        out.append({"id": f"skill:{s['id']}", "tier": "enabled", "name": s["name"], "description": SK._desc(s, 200),
                    "score": sc, "hits": len(q & _terms(s["name"] + " " + SK._desc(s))),
                    "how": f"Already here: load it with use_skill {s['name']}."
                    + (f" It is marked failing ({s.get('why') or 'it failed'}): check it first." if failing else "")})
    for t in tools or ():
        f = t.get("function", t) if isinstance(t, dict) else {"name": str(t)}
        name, desc = str(f.get("name", "")), str(f.get("description", ""))
        sc = _score(q, name, desc)
        if sc:
            out.append({"id": f"tool:{name}", "tier": "enabled", "name": name, "description": desc[:200], "score": sc,
                        "hits": len(q & _terms(name.replace("_", " ") + " " + desc)),
                        "how": f"Already connected: call {name} (look up its inputs with mcp_find_tools)."})
    return out


# ---------- 2. the curated library ----------------------------------------------------------------------------------

def _pin_args(command: str, args: list, kind: str, pkg: str, ver: str) -> list:
    """npx -y pkg@ver / uvx pkg==ver: the package argument with its exact version, any @latest dropped."""
    out = list(args)
    for i, a in enumerate(out):
        base = a.rsplit("@", 1)[0] if a.rfind("@") > 0 else a
        base = base.split("==", 1)[0]
        if base == pkg:
            out[i] = f"{pkg}@{ver}" if kind == "npm" else f"{pkg}=={ver}"
            return out
    return out


def _library_version(e: dict) -> tuple[str, str, list]:
    """(version shown, publisher hint, pinned args) for a library entry, from mcp_library.VERIFIED."""
    from meshpool_harness import mcp_library as ML
    v = ML.VERIFIED.get(e["id"], "")
    m = re.match(r"^(npm|PyPI) (\S+) (\S+)", v)
    if m and e.get("transport") == "stdio" and exact(m.group(3)):
        kind = "npm" if m.group(1) == "npm" else "pypi"
        return m.group(3), m.group(2), _pin_args(e["command"], e["args"], kind, m.group(2), m.group(3))
    m = re.search(r"git tag (v?[\d.]+)", v)
    if m and e.get("transport") == "stdio" and any(m.group(1) in a for a in e.get("args", [])):
        return f"git tag {m.group(1)}", "", list(e["args"])
    return "", "", list(e.get("args") or [])


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _company(url: str) -> str:
    """The domain that runs a remote service: mcp.exa.ai -> exa.ai, api.example.co.uk -> example.co.uk."""
    parts = _host(url).split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _library(q: set) -> list[dict]:
    from meshpool_harness import mcp_library as ML
    out = []
    for e in ML.LIBRARY:
        sc = _score(q, e["id"] + " " + e["name"], e["description"] + " " + e["category"])
        if not sc:
            continue
        secrets = [s["name"] for s in e.get("settings") or []]
        c = {"id": f"library:{e['id']}", "tier": "library", "name": e["name"], "description": e["description"],
             "score": sc, "hits": len(q & _terms(e["id"] + " " + e["name"] + " " + e["description"])),
             "verified": f"Yes: on meshpool's curated list (checked: {ML.VERIFIED.get(e['id'], 'by hand')}).",
             "license": e.get("license", ""), "secrets": secrets, "settings": [], "docs": e.get("docs", "")}
        if e["transport"] in ("connector", "oauth"):
            c.update(remote=True, addable=False, publisher=_company(e.get("url") or e.get("docs", "")) or e["name"],
                     how=f"{e['name']} signs in through the browser: ask the user to connect it in Settings, "
                         "Connectors.")
        elif e["transport"] == "http":
            c.update(remote=True, addable=True, version="hosted service (no version to pin)",
                     publisher=_company(e["url"]), company=_company(e["url"]),
                     run={"url": e["url"], **({"headers": dict(e["headers"])} if e.get("headers") else {})})
        else:
            ver, pkg, args = _library_version(e)
            c.update(remote=False, addable=bool(ver), version=ver, publisher=pkg or e["name"],
                     run={"command": e["command"], "args": args,
                          **({"env": dict(e["env"])} if e.get("env") else {})})
            if not ver:
                c["how"] = "No pinned version is known for it. The user can add it in Settings, MCP servers, Library."
        out.append(c)
    return out


# ---------- 3. the official registry --------------------------------------------------------------------------------

def publisher(name: str) -> str:
    """Who published a registry entry, from its verified namespace: io.github.alice/x -> alice (GitHub),
    com.example/x -> example.com."""
    ns = str(name or "").split("/", 1)[0]
    m = re.match(r"^io\.github\.([^/]+)$", ns)
    if m:
        return f"{m.group(1)} (GitHub)"
    parts = ns.split(".")
    return ".".join(reversed(parts)) if len(parts) >= 2 else ns


def _placeholders(text: str, prefix: str = "") -> tuple[str, list]:
    """"Bearer {api_key}" -> ("Bearer ${API_KEY}", ["API_KEY"]). prefix: put before each name (a remote service's
    names are its own, so a value already saved for something else is never sent to it). Any "$" in the text is
    dropped, so a literal "${NAME}" cannot reach a saved value either."""
    names = []

    def sub(m):
        n = re.sub(r"[^A-Za-z0-9_]", "_", m.group(1)).upper()
        n = ("MCP_" + n) if prefix and n.startswith(prefix[4:]) else prefix + n    # MCP_ZORB_ + ZORB_TOKEN: MCP_ZORB_TOKEN
        names.append(n)
        return "${" + n + "}"
    return re.sub(r"\{([A-Za-z0-9_.-]+)\}", sub, str(text or "").replace("$", "")), names


def _prefix(name: str) -> str:
    """The settings prefix of a remote service: com.acme/weather-mcp -> MCP_WEATHER_MCP_."""
    base = re.sub(r"[^A-Z0-9]+", "_", str(name or "").split("/")[-1].upper()).strip("_")[:40] or "SERVER"
    return f"MCP_{base}_"


def _env_spec(vars_: list) -> tuple[dict, list, list]:
    """environmentVariables -> ({NAME: "${NAME}"}, secret names, other required names). Values never go in."""
    env, secrets, other = {}, [], []
    for v in vars_ or []:
        if not isinstance(v, dict) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(v.get("name", ""))):
            continue
        n = v["name"]
        env[n] = "${" + n + "}"
        (secrets if v.get("isSecret") else other).append(n)
    return env, secrets, other


def parse_registry(doc: dict) -> list[dict]:
    """The registry's server list as candidates: active entries only, one local package (npm or PyPI, an exact
    version, stdio) or else one hosted remote per server. Deprecated and deleted entries, and packages without an exact
    version, are left out."""
    out = []
    for item in _l(_d(doc).get("servers")):
        if not isinstance(item, dict):
            continue
        srv = _d(item.get("server")) or item
        meta = _d(_d(item.get("_meta")).get(OFFICIAL)) or _d(_d(srv.get("_meta")).get(OFFICIAL))
        if str(meta.get("status") or "active").lower() != "active":
            continue
        name = _s(srv.get("name")).strip()[:200]
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", name):
            continue
        base = {"tier": "registry", "name": name, "title": _s(srv.get("title"))[:120] or name.split("/")[-1],
                "description": _s(srv.get("description"))[:300], "publisher": publisher(name),
                "verified": "Yes: the MCP Registry verified the publisher's namespace and package. It does not "
                            "review the code.",
                "docs": (_s(_d(srv.get("repository")).get("url")) or _s(srv.get("websiteUrl")))[:300]}
        cand = None
        for p in _l(srv.get("packages")):
            if not isinstance(p, dict):
                continue
            kind, ident, ver = p.get("registryType"), _s(p.get("identifier")), _s(p.get("version"))
            transport = _d(p.get("transport")).get("type", "stdio")
            if kind not in ("npm", "pypi") or transport != "stdio" or not exact(ver) \
                    or not (_NPM_NAME if kind == "npm" else _PYPI_NAME).match(ident):
                continue
            env, secrets, other = _env_spec(_l(p.get("environmentVariables")))
            if any(reserved(n) for n in env):
                continue                         # it asks for one of meshpool's own keys: never offered
            extra = [_s(a.get("value")) or _s(a.get("default")) for a in _l(p.get("packageArguments"))
                     if isinstance(a, dict) and a.get("type") == "positional"
                     and (_s(a.get("value")) or _s(a.get("default")))]
            if kind == "npm":
                run = {"command": "npx", "args": ["-y", f"{ident}@{ver}", *extra]}
            else:
                run = {"command": "uvx", "args": [f"{ident}=={ver}", *extra]}
            if env:
                run["env"] = env
            cand = dict(base, id=f"registry:{name}", remote=False, addable=True, version=ver, run=run,
                        package=f"{kind} {ident}", secrets=secrets, settings=other)
            break
        if cand is None:
            for r in _l(srv.get("remotes")):
                r = _d(r)
                url = _s(r.get("url"))
                if r.get("type") not in ("streamable-http", "sse") or not url.startswith("https://") \
                        or "{" in url or "$" in url or any(c.isspace() for c in url) or not _host(url):
                    continue
                headers, secrets, other, bad = {}, [], [], False
                for h in _l(r.get("headers")):
                    h = _d(h)
                    hn = _s(h.get("name"))
                    if not hn:
                        continue
                    val, names = _placeholders(_s(h.get("value")) or "{" + hn + "}", _prefix(name))
                    if not _HEADER.match(hn) or "\n" in val or "\r" in val:
                        bad = True                       # a header that could split the request: not offered
                        break
                    headers[hn] = val
                    (secrets if h.get("isSecret") else other).extend(n for n in names if n not in secrets + other)
                if bad:
                    continue
                run = {"url": url, **({"headers": headers} if headers else {}),
                       **({"transport": "sse"} if r.get("type") == "sse" else {})}
                cand = dict(base, id=f"registry:{name}", remote=True, addable=True, run=run,
                            version=_s(srv.get("version"))[:40] + " (hosted service)", company=_company(url),
                            secrets=secrets, settings=other)
                break
        if cand is not None:
            out.append(cand)
    return out


def _registry(need: str, q: set, http) -> list[dict]:
    url = f"{REGISTRY}?search={urllib.parse.quote(_query(need))}&version=latest&cursor="
    doc = http(url)
    out = parse_registry(doc)
    for c in out:
        c["score"] = _score(q, c["name"] + " " + c.get("title", ""), c["description"]) or 1
        c["hits"] = len(q & _terms(c["name"] + " " + c["description"]))
    return out


def _query(need: str) -> str:
    """A short search phrase: the need's first few content words."""
    from meshpool_harness import skills as SK
    words = SK._terms(need)[:4]
    return " ".join(words) or str(need or "")[:60]


# ---------- 4. a package --------------------------------------------------------------------------------------------

def _packages(need: str, q: set, http, notes: list) -> list[dict]:
    out = []
    unverified = "No: it is not in the MCP Registry or on meshpool's list. Nobody has checked who publishes it."
    try:
        doc = http(f"{NPM_SEARCH}?text={urllib.parse.quote(_query(need) + ' keywords:mcp')}&size=5")
        for o in _l(_d(doc).get("objects")):
            p = _d(_d(o).get("package"))
            name, ver = _s(p.get("name")), _s(p.get("version"))
            if not _NPM_NAME.match(name) or not exact(ver):
                continue
            pub = (_s(_d(p.get("publisher")).get("username"))
                   or _s(_d((_l(p.get("maintainers")) or [{}])[0]).get("username")) or "unknown")[:80]
            out.append({"id": f"npm:{name}", "tier": "package", "name": name,
                        "description": _s(p.get("description"))[:300],
                        "publisher": f"{pub} (npm)", "verified": unverified, "version": ver, "remote": False,
                        "addable": True, "package": f"npm {name}", "secrets": [], "settings": [],
                        "run": {"command": "npx", "args": ["-y", f"{name}@{ver}"]},
                        "docs": _s(_d(p.get("links")).get("npm"))[:300],
                        "score": _score(q, name, _s(p.get("description"))) or 1, "hits": 0})
    except Offline as e:
        notes.append(f"npm could not be reached ({e}), so it was skipped.")
    slug = "-".join(_query(need).split()[:2])
    for guess in ([f"mcp-server-{slug}", f"{slug}-mcp"] if slug else []):
        try:
            doc = http(PYPI_JSON.format(name=urllib.parse.quote(guess)))
        except Offline:
            continue
        info = _d(_d(doc).get("info"))
        ver, summary = _s(info.get("version")), _s(info.get("summary"))
        if not exact(ver) or not _PYPI_NAME.match(guess) or not re.search(r"\bmcp\b|model context protocol", summary + " " + guess, re.I):
            continue
        out.append({"id": f"pypi:{guess}", "tier": "package", "name": guess, "description": summary[:300],
                    "publisher": f"{(_s(info.get('author')) or _s(info.get('maintainer')) or 'unknown')[:80]} (PyPI)",
                    "verified": unverified, "version": ver, "remote": False, "addable": True,
                    "package": f"pypi {guess}", "secrets": [], "settings": [],
                    "run": {"command": "uvx", "args": [f"{guess}=={ver}"]}, "docs": _s(info.get("project_url"))[:300],
                    "score": _score(q, guess, summary) or 1, "hits": 0})
    return out


# ---------- the search ----------------------------------------------------------------------------------------------

WRITE = {"id": "write:new", "tier": "write", "name": "Write a new tool", "score": 0, "addable": False,
         "description": "Write a small tool for this need (a SKILL.md, a script or a local MCP server) with tests.",
         "how": "Write it, with tests, and keep it with save_tool. Its tests must pass and the scan must be clean."}


def find(need: str, tools=(), http=None, deep: bool = False, k: int = 6) -> dict:
    """Search in order, stopping at the first tier with a strong match (deep: search every tier).
    {"candidates": [...ranked], "searched": [tiers], "notes": [plain notes]}."""
    http = http or http_get_json
    q = _terms(need)
    found, searched, notes = [], [], []

    def take(tier, rows):
        searched.append(tier)
        rows.sort(key=lambda c: (-c["score"], c["name"]))
        found.extend(rows[:k])
        return any(_strong(c, q) for c in rows)

    stop = take("enabled", _enabled(q, need, tools))
    stop = take("library", _library(q)) or stop
    if deep or not stop:
        try:
            stop = take("registry", _registry(need, q, http))
        except Offline as e:
            searched.append("registry")
            notes.append(f"The MCP Registry could not be reached ({e}). It was skipped; this computer may be offline.")
            stop = False
    if deep or not (stop or any(c["tier"] == "registry" for c in found)):
        searched.append("package")
        found.extend(_packages(need, q, http, notes)[:k])
    found.append(dict(WRITE))
    searched.append("write")
    order = {t: i for i, t in enumerate(TIERS)}
    found.sort(key=lambda c: (order[c["tier"]], -c["score"], c["name"]))
    ranked = [c for c in found if c["tier"] != "write"][:k] + [c for c in found if c["tier"] == "write"]
    return {"candidates": ranked, "searched": searched, "notes": notes}


def render(res: dict) -> str:
    """The search result for the agent: one line per candidate, best first, with what to do next."""
    lines = ["Candidates, best first (searched: " + ", ".join(res["searched"]) + "):"]
    for i, c in enumerate(res["candidates"], 1):
        bits = [c["name"]]
        if c.get("version"):
            bits.append(f"version {c['version']}")
        if c.get("publisher"):
            bits.append(f"by {c['publisher']}")
        if c["tier"] in ("library", "registry", "package"):
            bits.append("remote service" if c.get("remote") else "runs on this computer")
            bits.append("verified" if str(c.get("verified", "")).startswith("Yes") else "not verified")
        lines.append(f"{i}. [{c['id']}] " + ", ".join(bits) + (f": {c['description']}" if c.get("description") else ""))
        if c.get("how"):
            lines.append(f"   {c['how']}")
    lines += res.get("notes") or []
    lines.append("To add one, call get_capability again with add set to its id in brackets. The user sees a card and "
                 "decides; nothing is added without their yes. Prefer what is already here, then a verified local "
                 "tool.")
    return "\n".join(lines)


# ---------- adding --------------------------------------------------------------------------------------------------

def server_name(c: dict) -> str:
    base = c["id"].split(":", 1)[1].split("/")[-1]
    return re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-")[:40] or "server"


def spec(c: dict) -> dict:
    """The mcp.json entry: only ${NAME} placeholders for settings, never a value."""
    run = json.loads(json.dumps(c["run"]))
    out = {k: v for k, v in run.items() if k in ("command", "args", "env", "url", "headers", "transport")}
    ver = c.get("version") or ""
    out["description"] = (f"{MARK} {c.get('description') or c['name']}"[:400]
                          + (f" Version {ver}." if ver and not c.get("remote") else "")
                          + (f" Needs: {', '.join(c.get('secrets', []) + c.get('settings', []))}."
                             if c.get("secrets") or c.get("settings") else ""))
    return out


def card(c: dict) -> dict:
    """The approval card: {"title", "detail", "remote"}."""
    remote = bool(c.get("remote"))
    company = c.get("company") or c.get("publisher") or "its publisher"
    lines = [f"Name: {c['name']}", f"Publisher: {c.get('publisher') or 'unknown'}",
             f"Verified: {c.get('verified') or 'No.'}",
             f"Version: {c.get('version') or 'none'}" + ("" if remote else " (pinned, exact)")]
    run = c.get("run") or {}
    if remote:
        lines.append(f"Connects to: {run.get('url', '')}")
        lines.append(f"Where data goes: what the agent sends this tool leaves this computer and goes to {company} "
                     f"({_host(run.get('url', ''))}). That can include text from this chat and file contents the "
                     "agent passes to it.")
    else:
        lines.append("Runs: " + " ".join([run.get("command", ""), *run.get("args", [])]))
        lines.append("Where data goes: it runs on this computer. Like any program it can go online; meshpool does not "
                     "watch what it sends.")
    need = c.get("secrets", []) + c.get("settings", [])
    if need:
        lines.append(f"Settings it needs: {', '.join(need)}. Values are saved in meshpool's settings on this "
                     "computer, never in mcp.json.")
    if c.get("license"):
        lines.append(f"License: {c['license']}")
    if c.get("docs"):
        lines.append(f"More: {c['docs']}")
    if remote:
        title = f"Send data to {company}? Add the remote service {c['name']}"
    else:
        title = f"Add {c['name']} {c.get('version', '')} to this computer?".replace("  ", " ")
    return {"title": title, "detail": "\n".join(lines), "remote": remote}


def missing(c: dict) -> list[str]:
    cfg = C.read()
    return [n for n in c.get("secrets", []) + c.get("settings", []) if not (os.environ.get(n) or cfg.get(n))]


def install(c: dict) -> dict:
    """Write the approved candidate into mcp.json: switched on when every setting has a value, else off until the user
    fills them in Settings. Returns {"server", "spec", "enabled", "missing"}."""
    if not c.get("addable"):
        raise ValueError(c.get("how") or f"{c['name']} cannot be added from here.")
    if not c.get("remote") and not exact(str(c.get("version", "")).replace("git tag ", "")):
        raise ValueError(f"{c['name']} has no exact version to pin, so it is not added.")
    from meshpool_harness.agent import mcp as M
    s = spec(c)
    gone = missing(c)
    if gone:
        s["disabled"] = True
    name = server_name(c)

    def put(servers):
        n, i = name, 2
        while n in servers and not str((servers[n] or {}).get("description", "")).startswith(MARK):
            n, i = f"{name}-{i}", i + 1
        servers[n] = s
        return n
    name = M.edit_config(put)
    return {"server": name, "spec": s, "enabled": not gone, "missing": gone}


def save_settings(c: dict, values: dict) -> list[str]:
    """Save a tool's settings (only the names it declares) to meshpool.config, the settings store. mcp.json keeps the
    ${NAME} placeholders. Returns the names saved."""
    allowed = set(c.get("secrets", []) + c.get("settings", []))
    vals = {k: str(v).strip() for k, v in (values or {}).items() if k in allowed and str(v).strip()}
    if any("\n" in v for v in vals.values()):
        raise ValueError("A setting cannot span several lines.")
    if vals:
        C.write(vals)
        C.load_into_env(override=True)
    return sorted(vals)


# ---------- noticing a missing capability mid-task ------------------------------------------------------------------
# A tool result or a reply that shows the agent lacks something: get_capability and save_tool join from the next model
# step on (Agent.capability_on). Ordinary failures (a failing test, a syntax error, a missing file) do not count.
_TOOL_SIGNALS = re.compile(
    r"command not found|is not recognized as (?:an internal or external command|the name of a cmdlet)"
    r"|ModuleNotFoundError|No module named\b|unknown tool ['\"]|tool not loaded|no such tool\b"
    r"|tool ['\"][^'\"]+['\"] (?:does not exist|not found)", re.I)
_REPLY_SIGNALS = re.compile(
    r"\bI (?:do not|don't) have (?:a |any )?(?:tool|integration|connector|access to (?:a |an )?(?:tool|API))\b"
    r"|\bno (?:tool|integration|connector) (?:available |here )?(?:for|to|that)\b"
    r"|\bI (?:cannot|can't|am unable to|am not able to) [^.\n]{0,80}(?:without (?:a|an|the) (?:tool|integration|"
    r"connector|API)|(?:because|since|as) I (?:do not|don't) have (?:a |any )?(?:tool|integration|access))", re.I)
_DONE = re.compile(r"GOAL:\s*(?:READY|DONE)\b", re.I)


def missing_signal(text: str, kind: str = "tool", task_open: bool = True) -> str:
    """The signal a tool result (kind "tool") or a reply (kind "reply") gives that a capability is missing, or "".
    A reply counts only while the task is still open (the caller says work remains, and no GOAL: READY)."""
    t = str(text or "")
    if kind == "tool":
        m = _TOOL_SIGNALS.search(t[-6000:])
        return m.group(0) if m else ""
    if not task_open or _DONE.search(t):
        return ""
    m = _REPLY_SIGNALS.search(t[-3000:])
    return m.group(0) if m else ""
